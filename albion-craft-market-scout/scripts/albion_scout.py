#!/usr/bin/env python3
"""Albion Online craft-profit scout. Python 3.8+, standard library only.

Data
  Prices and sales history: Albion Online Data Project (AODP), community scans.
  Recipes, item values, names: ao-data/ao-bin-dumps on GitHub. Downloaded on
  first use (~40 MB), reduced to a small index, cached, refreshed every 7 days.

Commands (each has --help)
  search TEXT       find item IDs by English name
  recipe ITEM       recipe alternatives, counts, which inputs get resource returns
  prices ITEMS      current quotes per city: lowest ask, highest bid, quote age
  history ITEMS     daily units traded and average price per city
  evaluate ITEM     full per-craft economics for one item
  scan              rank many craftable items to find leads

ITEM is an Albion ID: T4_BAG, T6_MAIN_SWORD@2 (enchantment 2), T5_PLANKS_LEVEL1@1.
Add --json for machine-readable output. All money is silver.
"""

import argparse
import copy
import gzip
import json
import math
import os
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Constants. Sources and dates for every number: references/economics.md.
# Override per run with flags instead of editing when a patch changes them.
# --------------------------------------------------------------------------

HOSTS = {
    "europe": "https://europe.albion-online-data.com",
    "west": "https://west.albion-online-data.com",
    "east": "https://east.albion-online-data.com",
}
DUMP_ITEMS = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
DUMP_NAMES = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json"
INDEX_MAX_AGE_DAYS = 7

ROYAL = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford"]
BUY_MARKETS = ROYAL + ["Brecilien", "Caerleon"]          # you can buy here
SELL_MARKETS = BUY_MARKETS + ["Black Market"]            # Black Market: sell only
CRAFT_CITIES = ROYAL + ["Brecilien", "Caerleon"]         # 18% base production bonus
ROUTE_RISK = {
    "Caerleon": "red-zone route (full-loot PvP)",
    "Black Market": "Caerleon, red-zone route (full-loot PvP)",
}
ROUTE_NOTE = {
    "Brecilien": "Brecilien: Travel Planner fee, or the Mists (portal needs 50,000 standing)",
}

SALES_TAX = 0.08              # non-Premium
PREMIUM_TAX = 0.04            # with Premium (--premium)
SETUP_FEE = 0.025             # charged when you post a sell order or buy order
NUTRITION_PER_VALUE = 0.1125  # station nutrition used = item value * this
DEFAULT_FEE_PER_100 = 1000    # maximum a station owner may charge per 100 nutrition

BASE_BONUS = 0.18             # Royal cities, Caerleon, Brecilien; no focus
CRAFT_SPEC_BONUS = 0.15       # crafting in the city that specializes in the item
REFINE_SPEC_BONUS = 0.40      # refining in the city that specializes in the resource

# Crafting specializations by ao-bin-dumps @craftingcategory (game data 2026-09-23).
CRAFT_SPECIALIZATION = {
    "Thetford": ["mace", "naturestaff", "firestaff", "leather_armor", "cloth_helmet"],
    "Lymhurst": ["sword", "bow", "arcanestaff", "leather_helmet", "leather_shoes"],
    "Bridgewatch": ["crossbow", "dagger", "cursestaff", "plate_armor", "cloth_shoes"],
    "Martlock": ["axe", "quarterstaff", "froststaff", "plate_shoes", "offhand"],
    "Fort Sterling": ["hammer", "spear", "holystaff", "plate_helmet", "cloth_armor"],
    "Caerleon": ["gatherergear", "tools", "food", "knuckles", "shapeshifterstaff"],
    "Brecilien": ["cape", "bag", "potion"],
}
REFINE_SPECIALIZATION = {"ore": "Thetford", "fiber": "Lymhurst", "rock": "Bridgewatch",
                         "hide": "Martlock", "wood": "Fort Sterling"}
SPECIALIZATION = {cat: city for city, cats in CRAFT_SPECIALIZATION.items() for cat in cats}

# Decision thresholds (see SKILL.md "Decision rules").
FRESH_HOURS = 6        # quotes older than this are flagged "aging"
MAX_AGE_HOURS = 24     # AODP drops orders not seen for 24 h anyway
MIN_MARGIN = 0.10      # profit / total cost needed for a pilot
MIN_DAILY_UNITS = 5    # average units sold per day in the sell city (7-day window)
MIN_COVERAGE_DAYS = 4  # days with history data out of the last 7
OUTLIER_BAND = 0.30    # ask more than 30% away from 7-day average is flagged
CAPTURE_SHARE = 0.10   # assume you can sell ~10% of observed daily volume
PILOT_SHARE = 0.05     # test batch: ~5% of one day's observed sales

SCAN_GROUPS = {
    "bags": lambda m: m["shop"] == "bags",
    "capes": lambda m: m["shop"] == "capes",
    "weapons": lambda m: m["shop"] == "weapons",
    "armor": lambda m: m["shop"] in ("armors", "head", "shoes"),
    "offhands": lambda m: m["shop"] == "offhands",
    "tools": lambda m: m["type"] in ("weapon", "trackingitem") and m["shop"] == "gathering",
    "gatherer-gear": lambda m: m["type"] == "equipmentitem" and m["shop"] == "gathering",
    "potions": lambda m: m["shop"] == "consumables" and m["sub"] == "potions",
    "food": lambda m: m["shop"] == "consumables" and m["sub"] == "food",
    "mounts": lambda m: m["shop"] == "mounts",
    "refining": lambda m: m["shop"] == "crafting" and m["sub"] == "refinedresources",
}
DEFAULT_SCAN_GROUPS = [g for g in SCAN_GROUPS if g not in ("refining", "mounts")]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def market_id(uniquename, enchant):
    """AODP/market ID: add @n only when the enchantment level is above 0."""
    try:
        level = int(enchant or 0)
    except ValueError:
        level = 0
    return "%s@%d" % (uniquename, level) if level > 0 else uniquename


def norm_city(name):
    return (name or "").lower().replace(" ", "").replace("'", "")


CITY_BY_NORM = {norm_city(c): c for c in SELL_MARKETS}


def canonical_city(name):
    c = CITY_BY_NORM.get(norm_city(name))
    if not c:
        raise SystemExit("Unknown city '%s'. Use one of: %s" % (name, ", ".join(SELL_MARKETS)))
    return c


def rrr_from_bonus(bonus):
    """Resource return rate from a production bonus: 1 - 1/(1+bonus)."""
    return 1.0 - 1.0 / (1.0 + bonus) if bonus > 0 else 0.0


def fmt(n, digits=0):
    if n is None:
        return "n/a"
    if digits == 0:
        return "{:,.0f}".format(n)
    return "{:,.{}f}".format(n, digits)


def pct(x):
    return "n/a" if x is None else "{:.1f}%".format(x * 100)


def age_text(hours):
    if hours is None:
        return "n/a"
    return "%.1fh" % hours


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(s):
    if not s or s.startswith("0001"):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hours_since(ts, now=None):
    if ts is None:
        return None
    return max(0.0, ((now or now_utc()) - ts).total_seconds() / 3600.0)


def out(text=""):
    sys.stdout.write(text + "\n")


# --------------------------------------------------------------------------
# HTTP with gzip (AODP throttles uncompressed requests hard) and 429 handling
# --------------------------------------------------------------------------

_last_call = [0.0]


def _ssl_context():
    ctx = ssl.create_default_context()
    # Python 3.13+ turns on strict X.509 checks that reject some antivirus and
    # corporate TLS-inspection CAs. Certificates are still fully verified.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


SSL_CONTEXT = _ssl_context()


def http_get(url, retries=4, pause=0.4):
    wait = pause - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={
        "Accept-Encoding": "gzip",
        "User-Agent": "albion-craft-market-scout/2.0 (+agent skill)",
    })
    for attempt in range(retries):
        try:
            _last_call[0] = time.time()
            with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as r:
                data = r.read()
                if r.headers.get("Content-Encoding", "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                reset = e.headers.get("RateLimit-Reset") or e.headers.get("Retry-After") or "30"
                try:
                    delay = float(reset)
                except ValueError:
                    delay = 30.0
                time.sleep(min(max(delay, 5.0), 120.0))
                continue
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as e:
            if attempt < retries - 1 and not isinstance(e.reason, ssl.SSLError):
                time.sleep(5 * (attempt + 1))
                continue
            raise


def get_json(url):
    return json.loads(http_get(url).decode("utf-8"))


# --------------------------------------------------------------------------
# Item index (recipes, item values, names) built from ao-bin-dumps
# --------------------------------------------------------------------------

def cache_dir(arg=None):
    candidates = [arg, os.environ.get("ALBION_SCOUT_CACHE"),
                  str(Path.home() / ".cache" / "albion-craft-market-scout"),
                  os.path.join(tempfile.gettempdir(), "albion-craft-market-scout")]
    for c in candidates:
        if not c:
            continue
        try:
            Path(c).mkdir(parents=True, exist_ok=True)
            probe = Path(c) / ".write-test"
            probe.write_text("ok")
            probe.unlink()
            return Path(c)
        except OSError:
            continue
    raise SystemExit("No writable cache directory. Pass --cache-dir.")


def parse_recipes(cr):
    recipes = []
    for alt in as_list(cr):
        inputs = []
        for r in as_list(alt.get("craftresource")):
            inputs.append([market_id(r["@uniquename"], r.get("@enchantmentlevel")),
                           int(float(r["@count"])),
                           r.get("@maxreturnamount") != "0"])
        if not inputs:
            continue
        extra = []
        for c in as_list(alt.get("currency")):
            extra.append("%s %s" % (c.get("@amount"), c.get("@uniquename")))
        for key in ("playerfactionstanding", "standing"):
            for s in as_list(alt.get(key)):
                extra.append("standing %s >= %s" % (s.get("@faction") or s.get("@type"), s.get("@minstanding")))
        recipes.append({
            "silver": int(float(alt.get("@silver") or 0)),
            "amount": int(float(alt.get("@amountcrafted") or 1)),
            "inputs": inputs,
            "extra": extra,
        })
    return recipes


def build_index(raw, names_raw):
    items, values = {}, {}
    for typ, lst in raw["items"].items():
        if not isinstance(lst, list):
            continue
        for it in lst:
            uid = it.get("@uniquename")
            if not uid:
                continue
            top_ench = int(it.get("@enchantmentlevel") or 0)
            base_id = market_id(uid, top_ench)
            if it.get("@itemvalue"):
                values[base_id] = float(it["@itemvalue"])
            meta = {
                "type": typ,
                "tier": int(it.get("@tier") or 0),
                "shop": it.get("@shopcategory"),
                "sub": it.get("@shopsubcategory1"),
                "cat": it.get("@craftingcategory"),
                "maxq": int(it.get("@maxqualitylevel") or 1),
            }
            recipes = parse_recipes(it.get("craftingrequirements"))
            if recipes:
                items[base_id] = dict(meta, ench=top_ench, recipes=recipes)
            for e in as_list((it.get("enchantments") or {}).get("enchantment")):
                lvl = int(e.get("@enchantmentlevel") or 0)
                er = parse_recipes(e.get("craftingrequirements"))
                if er and lvl > 0:
                    items[market_id(uid, lvl)] = dict(meta, ench=lvl, recipes=er)
                if e.get("@itemvalue"):
                    values[market_id(uid, lvl)] = float(e["@itemvalue"])
    names = {}
    for x in names_raw:
        en = (x.get("LocalizedNames") or {}).get("EN-US")
        if en:
            names[x["UniqueName"]] = en
    return {"built": now_utc().isoformat(), "items": items, "values": values, "names": names}


def load_db(args):
    path = cache_dir(getattr(args, "cache_dir", None)) / "index.json"
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < INDEX_MAX_AGE_DAYS * 86400
    if fresh and not getattr(args, "refresh", False):
        cached = read_index(path)
        if cached:
            return cached
    sys.stderr.write("Downloading recipe data from ao-bin-dumps (~40 MB, once a week)...\n")
    try:
        raw = get_json(DUMP_ITEMS)
        names_raw = get_json(DUMP_NAMES)
    except (urllib.error.URLError, OSError, ValueError) as e:
        cached = read_index(path) if path.exists() else None
        if cached:
            sys.stderr.write("Download failed (%s); using the older cached index.\n" % e)
            return cached
        raise SystemExit("Cannot download recipe data: %s. Use the manual path in SKILL.md." % e)
    db = build_index(raw, names_raw)
    # Write then rename, so an interrupted run never leaves a half-written index.
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(db, f, separators=(",", ":"))
    os.replace(str(tmp), str(path))
    return db


def read_index(path):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def name_of(db, iid):
    n = db["names"].get(iid)
    if not n:
        return iid
    ench = iid.split("@")[1] if "@" in iid else None
    tier = iid[1] if iid.startswith("T") and len(iid) > 1 and iid[1].isdigit() else None
    label = "%s.%s" % (tier, ench or "0") if tier else None
    return "%s (%s)" % (n, label) if label else n


def item_value(db, iid, depth=0):
    """(value per unit, complete?) for station fees.

    Uses the dump's itemvalue when present, else the sum of ingredient values.
    Ingredients with no known value count as 0 and mark the result incomplete.
    """
    if iid in db["values"]:
        return db["values"][iid], True
    it = db["items"].get(iid)
    if not it or depth > 5:
        return None, False
    r = it["recipes"][0]
    total, complete = 0.0, True
    for inp, cnt, _ in r["inputs"]:
        v, ok = item_value(db, inp, depth + 1)
        complete = complete and ok
        total += (v or 0.0) * cnt
    return total / max(1, r["amount"]), complete


def require_item(db, iid):
    if iid not in db["items"]:
        hint = [k for k in db["items"] if k.startswith(iid.split("@")[0])][:5]
        raise SystemExit("No recipe for '%s'.%s Use: search <name>" % (
            iid, (" Close IDs: " + ", ".join(hint) + ".") if hint else ""))
    return db["items"][iid]


# --------------------------------------------------------------------------
# AODP: prices and history
# --------------------------------------------------------------------------

def chunked(ids, prefix_len, limit=3900):
    chunk, size = [], prefix_len
    for i in ids:
        add = len(urllib.parse.quote(i, safe="@_")) + 1
        if chunk and size + add > limit:
            yield chunk
            chunk, size = [], prefix_len
        chunk.append(i)
        size += add
    if chunk:
        yield chunk


def loc_param(locations):
    return ",".join(urllib.parse.quote(l) for l in locations)


def fetch_quotes(host, ids, locations):
    """{(item, city): {ask, ask_age_h, bid, bid_age_h}} for quality 1."""
    rows = []
    query = "?locations=%s&qualities=1" % loc_param(locations)
    for chunk in chunked(sorted(set(ids)), len(host) + 40 + len(query)):
        path = ",".join(urllib.parse.quote(i, safe="@_") for i in chunk)
        rows.extend(get_json("%s/api/v2/stats/prices/%s.json%s" % (host, path, query)))
    return parse_prices(rows, now_utc())


def parse_prices(rows, now):
    """Price API rows -> quotes. Zero prices and 0001-01-01 dates mean no data."""
    quotes = {}
    for row in rows:
        city = CITY_BY_NORM.get(norm_city(row.get("city")))
        if not city:
            continue
        ask_t = parse_ts(row.get("sell_price_min_date"))
        bid_t = parse_ts(row.get("buy_price_max_date"))
        quotes[(row["item_id"], city)] = {
            "ask": row.get("sell_price_min") or None if ask_t else None,
            "ask_age_h": hours_since(ask_t, now),
            "bid": row.get("buy_price_max") or None if bid_t else None,
            "bid_age_h": hours_since(bid_t, now),
        }
    return quotes


def history_start(now, days):
    return now.date() - timedelta(days=days)


def fetch_history(host, ids, locations, days=14):
    """{(item, city): summary} from daily buckets; first partial bucket dropped."""
    now = now_utc()
    start = history_start(now, days)
    query = "?date=%s&end_date=%s&locations=%s&qualities=1&time-scale=24" % (
        start.isoformat(), now.date().isoformat(), loc_param(locations))
    rows = []
    for chunk in chunked(sorted(set(ids)), len(host) + 40 + len(query)):
        path = ",".join(urllib.parse.quote(i, safe="@_") for i in chunk)
        rows.extend(get_json("%s/api/v2/stats/history/%s.json%s" % (host, path, query)))
    return summarize_history(rows, start)


def summarize_history(rows, start):
    series = {}
    latest = None
    for row in rows:
        city = CITY_BY_NORM.get(norm_city(row.get("location")))
        if not city:
            continue
        days_map = series.setdefault((row["item_id"], city), {})
        for d in row.get("data") or []:
            ts = parse_ts(d.get("timestamp"))
            if ts is None or ts.date() < start:
                continue
            day = ts.date()
            cnt, avg = d.get("item_count") or 0, d.get("avg_price") or 0
            prev = days_map.get(day, (0, 0))
            days_map[day] = (prev[0] + cnt, prev[1] + cnt * avg)
            latest = day if latest is None or day > latest else latest
    # Each city's scans lag by a different number of days, so every series gets
    # its own 7/14-day window ending at its latest bucket, plus a lag figure.
    summaries = {}
    for key, days_map in series.items():
        if not days_map:
            continue
        end_day = max(days_map)

        def agg(n):
            window = {end_day - timedelta(days=i) for i in range(n)}
            units = sum(v[0] for d, v in days_map.items() if d in window)
            silver = sum(v[1] for d, v in days_map.items() if d in window)
            covered = sum(1 for d, v in days_map.items() if d in window and v[0] > 0)
            return units, (silver / units if units else None), covered
        u7, vwap7, cov7 = agg(7)
        u14, vwap14, cov14 = agg(14)
        summaries[key] = {
            "units7": u7, "per_day7": u7 / 7.0, "vwap7": vwap7, "days7": cov7,
            "units14": u14, "per_day14": u14 / 14.0, "vwap14": vwap14, "days14": cov14,
            "window_end": end_day.isoformat(), "lag_days": (latest - end_day).days,
        }
    return summaries


# --------------------------------------------------------------------------
# Economics
# --------------------------------------------------------------------------

def is_refining(item):
    return item.get("shop") == "crafting" and item.get("sub") == "refinedresources"


def specialization_city(item):
    if is_refining(item):
        return REFINE_SPECIALIZATION.get(item.get("cat"))
    return SPECIALIZATION.get(item.get("cat"))


def craft_rrr(item, city, opts):
    if opts.rrr is not None:
        return opts.rrr, "set by --rrr"
    if city not in CRAFT_CITIES:
        return None, "unknown for %s; pass --rrr" % city
    bonus = BASE_BONUS
    why = "%.0f%% base bonus" % (BASE_BONUS * 100)
    if specialization_city(item) == city:
        spec = REFINE_SPEC_BONUS if is_refining(item) else CRAFT_SPEC_BONUS
        bonus += spec
        why += " + %.0f%% city specialization" % (spec * 100)
    if opts.daily_bonus:
        bonus += opts.daily_bonus
        why += " + %.0f%% daily bonus" % (opts.daily_bonus * 100)
    return rrr_from_bonus(bonus), why


def pick_craft_city(item, opts):
    """(city or None, note). Caerleon needs red-zone hauling, so it is never the default."""
    if opts.craft_city:
        return canonical_city(opts.craft_city), None
    spec = specialization_city(item)
    if spec and spec != "Caerleon":
        return spec, None
    note = None
    if spec == "Caerleon":
        note = ("Caerleon specializes in this (24.8% return rate) but needs red-zone hauling; "
                "compare with --craft-city Caerleon")
    return None, note


def best_ask(quotes, iid, sources, max_age):
    best = None
    for city in sources:
        q = quotes.get((iid, city))
        if not q or not q["ask"] or q["ask_age_h"] is None or q["ask_age_h"] > max_age:
            continue
        if best is None or q["ask"] < best["price"]:
            best = {"price": q["ask"], "city": city, "age_h": q["ask_age_h"]}
    return best


def sell_options(iid, quotes, hist, sell_cities, tax, max_age):
    """Conservative net revenue per unit for each city and mode."""
    options = []
    for city in sell_cities:
        q = quotes.get((iid, city)) or {}
        h = (hist or {}).get((iid, city))
        vwap = h["vwap7"] if h else None
        if city != "Black Market" and q.get("ask") and q.get("ask_age_h") is not None \
                and q["ask_age_h"] <= max_age:
            price = q["ask"] if vwap is None else min(q["ask"], vwap)
            basis = "lowest ask" if vwap is None or q["ask"] <= vwap else "7-day avg sale price, below lowest ask"
            outlier = vwap is not None and abs(q["ask"] - vwap) / vwap > OUTLIER_BAND
            options.append({
                "city": city, "mode": "sell order", "price": price, "basis": basis,
                "net": price * (1 - tax - SETUP_FEE), "age_h": q["ask_age_h"],
                "ask": q["ask"], "vwap7": vwap, "outlier": outlier, "hist": h,
            })
        if q.get("bid") and q.get("bid_age_h") is not None and q["bid_age_h"] <= max_age:
            price = q["bid"] if vwap is None else min(q["bid"], vwap)
            basis = "highest buy order" if vwap is None or q["bid"] <= vwap \
                else "7-day avg sale price, below highest buy order"
            options.append({
                "city": city, "mode": "instant sell to buy order", "price": price, "basis": basis,
                "net": price * (1 - tax), "age_h": q["bid_age_h"], "ask": q.get("ask"),
                "bid": q["bid"], "vwap7": vwap, "hist": h,
                # A buy order below the average is normal; only one far above it is suspicious.
                "outlier": vwap is not None and q["bid"] > (1 + OUTLIER_BAND) * vwap,
            })
    return options


def evaluate(db, iid, quotes, hist, opts, _nested=False):
    item = require_item(db, iid)
    craft_city, city_note = pick_craft_city(item, opts)
    # No specialization city: every Royal city gives the same return rate.
    rrr, rrr_why = craft_rrr(item, craft_city or ROYAL[0], opts)
    tax = opts.tax
    best = None
    for idx, alt in enumerate(item["recipes"]):
        lines, missing = [], []
        for inp, cnt, returnable in alt["inputs"]:
            q = best_ask(quotes, inp, opts.sources, opts.max_age)
            if q is None:
                missing.append(inp)
            ih = (hist or {}).get((inp, q["city"])) if q else None
            cheap = ih["vwap7"] if ih and ih["vwap7"] and q["price"] < (1 - OUTLIER_BAND) * ih["vwap7"] else None
            lines.append({
                "id": inp, "name": name_of(db, inp), "count": cnt, "returnable": returnable,
                "unit": q["price"] if q else None, "city": q["city"] if q else None,
                "age_h": q["age_h"] if q else None,
                "ext": q["price"] * cnt if q else None, "cheap_vs_avg": cheap,
            })
        raw = sum(l["ext"] for l in lines if l["ext"] is not None)
        returnable_raw = sum(l["ext"] for l in lines if l["ext"] is not None and l["returnable"])
        returned = returnable_raw * (rrr or 0.0)
        value, complete = item_value(db, iid)
        value_basis = value * max(1, alt["amount"]) if value else None
        if opts.station_fee is not None:
            station_fee, fee_note = opts.station_fee, "set by --station-fee"
        elif value_basis:
            station_fee = value_basis * NUTRITION_PER_VALUE * opts.fee_per_100 / 100.0
            fee_note = "item value %s x %s x %s/100, estimate%s" % (
                fmt(value_basis), NUTRITION_PER_VALUE, fmt(opts.fee_per_100),
                "" if complete else ", some input values unknown")
        else:
            station_fee, fee_note = None, "unknown item value; check the station in game"
        transport = opts.transport
        cost = raw - returned + alt["silver"] + (station_fee or 0.0) + (transport or 0.0)
        cand = {
            "recipe_index": idx, "amount": alt["amount"], "lines": lines, "missing": missing,
            "extra": alt["extra"], "raw": raw, "returnable_raw": returnable_raw,
            "returned": returned, "silver": alt["silver"], "station_fee": station_fee,
            "fee_note": fee_note, "transport": transport, "cost": cost,
        }
        if best is None or (not cand["missing"] and (best["missing"] or cand["cost"] < best["cost"])):
            best = cand
    opts_list = sell_options(iid, quotes, hist, opts.sells, tax, opts.max_age)
    if opts.sell_city:
        opts_list = [o for o in opts_list if o["city"] == canonical_city(opts.sell_city)]
    # Once history is loaded, only cities with observed sales can be the pick;
    # an ask nobody has paid for is not revenue.
    if hist is not None:
        pool = [o for o in opts_list if o["hist"] and o["hist"]["units7"] > 0] or opts_list
    else:
        pool = opts_list
    sale = max(pool, key=lambda o: o["net"]) if pool else None
    if craft_city is None:
        spend = {}
        for l in best["lines"]:
            if l["city"] in ROYAL and l["ext"]:
                spend[l["city"]] = spend.get(l["city"], 0) + l["ext"]
        craft_city = max(spend, key=spend.get) if spend else ROYAL[0]
        city_note = city_note or "no city specialization; any Royal city gives the same return rate"
    res = {
        "item": iid, "name": name_of(db, iid), "category": "%s/%s" % (item["shop"], item["sub"]),
        "craft_city": craft_city, "city_note": city_note, "rrr": rrr, "rrr_why": rrr_why,
        "tax": tax, **best, "sale": sale, "sell_options": opts_list, "budget": opts.budget,
    }
    amount = best["amount"]
    if sale and not best["missing"]:
        revenue = sale["net"] * amount
        res["revenue"] = revenue
        res["profit"] = revenue - best["cost"]
        res["profit_unit"] = res["profit"] / amount
        res["margin"] = res["profit"] / best["cost"] if best["cost"] > 0 else None
        res["breakeven_cap"] = revenue - (best["cost"] - (best["transport"] or 0.0))
    else:
        res.update(revenue=None, profit=None, profit_unit=None, margin=None, breakeven_cap=None)
    res["verdict"], res["reasons"] = verdict(res)
    h = sale["hist"] if sale else None
    res["daily_capacity"], res["pilot_crafts"], res["pilot_capital"] = None, None, None
    if h and res["profit_unit"]:
        res["daily_capacity"] = res["profit_unit"] * CAPTURE_SHARE * h["per_day7"]
        per_craft = best["raw"] + best["silver"] + (best["station_fee"] or 0)
        crafts = max(1, int(math.ceil(PILOT_SHARE * h["per_day7"] / amount)))
        if opts.budget and per_craft > 0:
            crafts = min(crafts, int(opts.budget // per_craft))
        if crafts > 0 and res["verdict"] == "pilot":
            res["pilot_crafts"], res["pilot_capital"] = crafts, crafts * per_craft
    # Same craft with every red-zone city removed, so the user can weigh the risk.
    res["safe_alt"] = None
    if not _nested and any(c in ROUTE_RISK for c in route_cities(res)):
        safe_opts = copy.copy(opts)
        safe_opts.sources = [c for c in opts.sources if c not in ROUTE_RISK]
        safe_opts.sells = [c for c in opts.sells if c not in ROUTE_RISK]
        if safe_opts.craft_city and canonical_city(safe_opts.craft_city) in ROUTE_RISK:
            safe_opts.craft_city = None
        if safe_opts.sell_city and canonical_city(safe_opts.sell_city) in ROUTE_RISK:
            safe_opts.sell_city = None
        alt = evaluate(db, iid, quotes, hist, safe_opts, _nested=True)
        res["safe_alt"] = {k: alt[k] for k in ("craft_city", "profit", "profit_unit", "margin", "verdict")}
        res["safe_alt"]["sale"] = ("%s (%s)" % (alt["sale"]["city"], alt["sale"]["mode"])) if alt["sale"] else None
    return res


def route_cities(r):
    cities = [r["craft_city"]] + [l["city"] for l in r["lines"]]
    if r["sale"]:
        cities.append(r["sale"]["city"])
    return cities


def verdict(r):
    reasons = []
    if r["missing"]:
        reasons.append("no fresh price for: " + ", ".join(r["missing"]))
    if r["extra"]:
        reasons.append("needs non-silver inputs: " + "; ".join(r["extra"]))
    if r["sale"] is None:
        reasons.append("no fresh sell price")
    if r["rrr"] is None:
        reasons.append("return rate unknown for craft city")
    if reasons or r["profit"] is None:
        return "avoid", reasons
    if r["profit"] <= 0:
        return "avoid", ["loses %s per craft after fees" % fmt(-r["profit"])]
    sale, h = r["sale"], r["sale"]["hist"]
    if h is None or h["units7"] == 0:
        return "avoid", ["no observed sales in %s in the last 7 days; demand unverified" % sale["city"]]
    if r["margin"] is not None and r["margin"] < MIN_MARGIN:
        reasons.append("margin %s below %s" % (pct(r["margin"]), pct(MIN_MARGIN)))
    if h["per_day7"] < MIN_DAILY_UNITS:
        reasons.append("thin volume: %.1f/day in %s" % (h["per_day7"], sale["city"]))
    if h["days7"] < MIN_COVERAGE_DAYS:
        reasons.append("history covers %d of 7 days" % h["days7"])
    if h.get("lag_days", 0) >= 5:
        reasons.append("sales history for %s is stale: ends %s, %d days behind other cities" % (
            sale["city"], h["window_end"], h["lag_days"]))
    elif h.get("lag_days", 0) >= 3:
        reasons.append("history for %s ends %s, %d days behind other cities" % (
            sale["city"], h["window_end"], h["lag_days"]))
    if sale["outlier"]:
        quoted = sale["bid"] if sale["mode"].startswith("instant") else sale["ask"]
        reasons.append("current price %s is >%d%% from 7-day avg %s; used the lower" % (
            fmt(quoted), OUTLIER_BAND * 100, fmt(sale["vwap7"])))
    for l in r["lines"]:
        if l.get("cheap_vs_avg"):
            reasons.append("input %s ask %s is far below its 7-day avg %s in %s; may be a small order" % (
                l["id"], fmt(l["unit"]), fmt(l["cheap_vs_avg"]), l["city"]))
    ages = [l["age_h"] for l in r["lines"] if l["age_h"] is not None] + [sale["age_h"]]
    if max(ages) > FRESH_HOURS:
        reasons.append("oldest quote %s (aging)" % age_text(max(ages)))
    risks = sorted({ROUTE_RISK[c] for c in route_cities(r) if c in ROUTE_RISK})
    if risks:
        reasons.append("route risk: " + ", ".join(risks))
    soft = ("oldest quote", "route risk", "history for", "input ")
    hard = [x for x in reasons if not x.startswith(soft)]
    return ("watch" if hard else "pilot"), reasons


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_evaluation(r):
    a = r["amount"]
    out("## %s  `%s`" % (r["name"], r["item"]))
    out("")
    out("**Verdict: %s**%s" % (r["verdict"].upper(), (" - " + "; ".join(r["reasons"])) if r["reasons"] else ""))
    out("")
    out("- Craft in: %s (return rate %s: %s)" % (r["craft_city"], pct(r["rrr"]), r["rrr_why"]))
    if r.get("city_note"):
        out("- Note: %s" % r["city_note"])
    notes = sorted({ROUTE_NOTE[c] for c in route_cities(r) if c in ROUTE_NOTE})
    if notes:
        out("- Travel: " + "; ".join(notes))
    s = r["sale"]
    if s:
        out("- Sell in: %s by %s at %s each (%s; quote age %s)" % (
            s["city"], s["mode"], fmt(s["price"]), s["basis"], age_text(s["age_h"])))
        h = s["hist"]
        if h:
            out("- Sales evidence (%s, quality 1): %s units in 7 days (%.1f/day, %d/7 days with data), "
                "7-day avg %s; 14 days: %s units, avg %s; window ends %s" % (
                    s["city"], fmt(h["units7"]), h["per_day7"], h["days7"], fmt(h["vwap7"]),
                    fmt(h["units14"]), fmt(h["vwap14"]), h["window_end"]))
        else:
            out("- Sales evidence: none in AODP history for %s. Sparse data is not proof of no demand." % s["city"])
    out("- Output per craft: %d" % a)
    out("")
    out("| Input | Qty/craft | Unit price | Bought in | Quote age | Cost | Returns? |")
    out("|---|---:|---:|---|---:|---:|---|")
    for l in r["lines"]:
        out("| %s `%s` | %d | %s | %s | %s | %s | %s |" % (
            l["name"], l["id"], l["count"], fmt(l["unit"]), l["city"] or "MISSING",
            age_text(l["age_h"]), fmt(l["ext"]), "yes" if l["returnable"] else "no"))
    out("")
    out("| Per craft | Silver |")
    out("|---|---:|")
    out("| Raw input cost | %s |" % fmt(r["raw"]))
    out("| Returned (%s of %s eligible) | -%s |" % (pct(r["rrr"]), fmt(r["returnable_raw"]), fmt(r["returned"])))
    if r["silver"]:
        out("| Recipe silver cost | %s |" % fmt(r["silver"]))
    out("| Station fee (%s) | %s |" % (r["fee_note"], fmt(r["station_fee"])))
    out("| Transport | %s |" % (fmt(r["transport"]) if r["transport"] is not None else "not set (see break-even)"))
    out("| **Total cost** | **%s** |" % fmt(r["cost"]))
    if s:
        fee_rate = r["tax"] + (SETUP_FEE if s["mode"] == "sell order" else 0)
        out("| Sale: %d x %s, minus %s tax/fees | %s |" % (a, fmt(s["price"]), pct(fee_rate), fmt(r["revenue"])))
    out("| **Profit per craft** | **%s** |" % fmt(r["profit"]))
    out("| Profit per item / margin | %s / %s |" % (fmt(r["profit_unit"]), pct(r["margin"])))
    if r["transport"] is None and r["breakeven_cap"] is not None:
        out("| Max transport + other unknown costs before a loss | %s per craft |" % fmt(r["breakeven_cap"]))
    out("")
    if r["pilot_crafts"]:
        out("- Test batch: %d craft(s), about %s silver up front (about 5%% of one day's observed sales%s)" % (
            r["pilot_crafts"], fmt(r["pilot_capital"]), ", capped by --budget" if r.get("budget") else ""))
    sa = r.get("safe_alt")
    if sa:
        if sa["profit"] is None:
            out("- Without red zones: no complete safe route (missing prices or sales)")
        else:
            out("- Without red zones: craft in %s, sell in %s, profit %s per craft (%s per item, %s margin), verdict %s" % (
                sa["craft_city"], sa["sale"], fmt(sa["profit"]), fmt(sa["profit_unit"]),
                pct(sa["margin"]), sa["verdict"]))
    alts = sorted(r["sell_options"], key=lambda o: -o["net"])[:5]
    if alts:
        out("- Other sell options (net per item): " + "; ".join(
            "%s %s %s" % (o["city"], o["mode"], fmt(o["net"])) for o in alts))
    out("")


def scan_table(rows, limit):
    out("| # | Item | Craft in | Sell in | Makes | Cost/craft | Profit/craft | Margin | Sold/day | Days | Red zone? | Verdict |")
    out("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    for i, r in enumerate(rows[:limit], 1):
        s = r["sale"]
        h = s["hist"] if s else None
        mode = "instant" if s and s["mode"].startswith("instant") else "order"
        risky = any(c in ROUTE_RISK for c in route_cities(r))
        out("| %d | %s `%s` | %s | %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["name"], r["item"], r["craft_city"], ("%s (%s)" % (s["city"], mode)) if s else "-",
            r["amount"], fmt(r["cost"]), fmt(r["profit"]), pct(r["margin"]),
            "%.1f" % h["per_day7"] if h else "-", "%d/7" % h["days7"] if h else "-",
            "yes" if risky else "no", r["verdict"]))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def parse_ids(text):
    return [t.strip() for t in text.replace(" ", ",").split(",") if t.strip()]


def cmd_search(args):
    db = load_db(args)
    words = args.text.lower().split()
    hits = []
    for iid, name in db["names"].items():
        if all(w in name.lower() or w in iid.lower() for w in words):
            hits.append((iid, name, iid in db["items"]))
    hits.sort(key=lambda h: (not h[2], h[0]))
    if args.json:
        out(json.dumps([{"id": h[0], "name": h[1], "craftable": h[2]} for h in hits[:args.limit]], indent=1))
        return
    for iid, name, craftable in hits[:args.limit]:
        out("%-34s %s%s" % (iid, name, "" if craftable else "  (no recipe)"))
    if len(hits) > args.limit:
        out("... %d more; narrow the search" % (len(hits) - args.limit))


def cmd_recipe(args):
    db = load_db(args)
    item = require_item(db, args.item)
    if args.json:
        out(json.dumps({"item": args.item, "name": name_of(db, args.item), **item}, indent=1))
        return
    out("%s `%s`  category %s/%s, crafting category %s, max quality %d" % (
        name_of(db, args.item), args.item, item["shop"], item["sub"], item["cat"], item["maxq"]))
    spec = specialization_city(item)
    out("City specialization bonus: %s" % (spec or "none known"))
    for i, alt in enumerate(item["recipes"]):
        out("Recipe %d: makes %d per craft%s%s" % (
            i + 1, alt["amount"], (", silver %d" % alt["silver"]) if alt["silver"] else "",
            (", also needs " + "; ".join(alt["extra"])) if alt["extra"] else ""))
        for inp, cnt, ret in alt["inputs"]:
            out("  %4d x %-32s %s%s" % (cnt, inp, name_of(db, inp), "" if ret else "  [no resource return]"))
    v, complete = item_value(db, args.item)
    out("Item value (station fee basis): %s%s" % (fmt(v), "" if complete else " (some input values unknown)"))


def cmd_prices(args):
    ids = parse_ids(args.items)
    quotes = fetch_quotes(HOSTS[args.server], ids, args.locations)
    if args.json:
        out(json.dumps([{"item": k[0], "city": k[1], **v} for k, v in sorted(quotes.items())], indent=1))
        return
    out("| Item | City | Lowest ask | Ask age | Highest bid | Bid age |")
    out("|---|---|---:|---:|---:|---:|")
    for (iid, city), q in sorted(quotes.items()):
        if q["ask"] or q["bid"]:
            out("| %s | %s | %s | %s | %s | %s |" % (iid, city, fmt(q["ask"]), age_text(q["ask_age_h"]),
                                                   fmt(q["bid"]), age_text(q["bid_age_h"])))


def cmd_history(args):
    ids = parse_ids(args.items)
    hist = fetch_history(HOSTS[args.server], ids, args.locations, args.days)
    if args.json:
        out(json.dumps([{"item": k[0], "city": k[1], **v} for k, v in sorted(hist.items())], indent=1))
        return
    out("| Item | City | Units 7d | Per day | Days w/ data | Avg price 7d | Units 14d | Avg price 14d | Window end |")
    out("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for (iid, city), h in sorted(hist.items()):
        out("| %s | %s | %s | %.1f | %d/7 | %s | %s | %s | %s |" % (
            iid, city, fmt(h["units7"]), h["per_day7"], h["days7"], fmt(h["vwap7"]),
            fmt(h["units14"]), fmt(h["vwap14"]), h["window_end"]))


def cmd_evaluate(args):
    db = load_db(args)
    item = require_item(db, args.item)
    host = HOSTS[args.server]
    ids = {args.item} | {inp for alt in item["recipes"] for inp, _, _ in alt["inputs"]}
    quotes = fetch_quotes(host, ids, SELL_MARKETS)
    # Output history decides the sale price; input history flags suspiciously cheap asks.
    hist = fetch_history(host, ids, sorted(set(args.sells) | set(args.sources)), 14)
    r = evaluate(db, args.item, quotes, hist, args)
    if args.json:
        out(json.dumps(r, indent=1, default=str))
        return
    print_assumptions(args)
    print_evaluation(r)


def cmd_scan(args):
    db = load_db(args)
    host = HOSTS[args.server]
    groups = args.groups or DEFAULT_SCAN_GROUPS
    tiers = set(range(args.min_tier, args.max_tier + 1))
    enchants = set(range(args.min_enchant, args.max_enchant + 1))
    ids = []
    for iid, it in db["items"].items():
        if it["tier"] in tiers and it["ench"] in enchants and any(SCAN_GROUPS[g](it) for g in groups):
            if not iid.startswith(("UNIQUE_", "QUESTITEM_")):
                ids.append(iid)
    if not ids:
        raise SystemExit("No craftable items match these filters.")
    inputs = {inp for i in ids for alt in db["items"][i]["recipes"] for inp, _, _ in alt["inputs"]}
    sys.stderr.write("Scanning %d items (%d inputs) across %d markets...\n" % (len(ids), len(inputs), len(SELL_MARKETS)))
    quotes = fetch_quotes(host, set(ids) | inputs, SELL_MARKETS)
    stage1 = []
    for iid in ids:
        r = evaluate(db, iid, quotes, None, args)
        if r["profit"] is not None and r["margin"] is not None and r["margin"] >= args.prefilter_margin:
            stage1.append(r)
    stage1.sort(key=lambda r: -r["margin"])
    shortlist = [r["item"] for r in stage1[:args.history_for]]
    sys.stderr.write("%d items look profitable on current quotes; checking history for %d...\n" % (
        len(stage1), len(shortlist)))
    hist = fetch_history(host, shortlist, args.sells, 14) if shortlist else {}
    final = [evaluate(db, iid, quotes, hist, args) for iid in shortlist]
    final = [r for r in final if r["profit"] is not None and r["profit"] > 0]
    order = {"pilot": 0, "watch": 1, "avoid": 2}
    final.sort(key=lambda r: (order[r["verdict"]], -(r["daily_capacity"] or 0)))
    if args.json:
        out(json.dumps({"generated": now_utc().isoformat(), "server": args.server, "groups": groups,
                        "tiers": [args.min_tier, args.max_tier],
                        "enchants": [args.min_enchant, args.max_enchant],
                        "scanned": len(ids), "profitable_on_quotes": len(stage1),
                        "results": final[:args.top]}, indent=1, default=str))
        return
    print_assumptions(args)
    out("Scanned %d items in: %s. Tiers %d-%d, enchantments %d-%d." % (
        len(ids), ", ".join(groups), args.min_tier, args.max_tier, args.min_enchant, args.max_enchant))
    out("%d positive on current quotes; %d still positive after sales history. "
        "Sorted by verdict, then estimated silver/day at %d%% of observed volume." % (
            len(stage1), len(final), CAPTURE_SHARE * 100))
    out("")
    scan_table(final, args.top)
    out("")
    out("These are leads. Run `evaluate ITEM` on a row before crafting.")


def cmd_export_index(args):
    """Write the compact recipe index and constants that the web app (web/core.js) loads."""
    db = load_db(args)
    items, names = {}, {}
    for iid, it in db["items"].items():
        groups = [g for g, match in SCAN_GROUPS.items() if match(it)]
        if not groups or iid.startswith(("UNIQUE_", "QUESTITEM_")):
            continue
        value, complete = item_value(db, iid)
        items[iid] = {
            "tier": it["tier"], "ench": it["ench"], "groups": groups,
            "category": "%s/%s" % (it["shop"], it["sub"]),
            "spec": specialization_city(it), "refining": is_refining(it),
            "recipes": it["recipes"], "value": value, "value_complete": complete,
        }
        names[iid] = name_of(db, iid)
        for alt in it["recipes"]:
            for inp, _, _ in alt["inputs"]:
                names[inp] = name_of(db, inp)
    econ = {
        "hosts": HOSTS, "royal": ROYAL, "buy_markets": BUY_MARKETS, "sell_markets": SELL_MARKETS,
        "craft_cities": CRAFT_CITIES, "route_risk": ROUTE_RISK, "route_note": ROUTE_NOTE,
        "sales_tax": SALES_TAX, "premium_tax": PREMIUM_TAX, "setup_fee": SETUP_FEE,
        "nutrition_per_value": NUTRITION_PER_VALUE, "default_fee_per_100": DEFAULT_FEE_PER_100,
        "base_bonus": BASE_BONUS, "craft_spec_bonus": CRAFT_SPEC_BONUS,
        "refine_spec_bonus": REFINE_SPEC_BONUS, "fresh_hours": FRESH_HOURS,
        "max_age_hours": MAX_AGE_HOURS, "min_margin": MIN_MARGIN, "min_daily_units": MIN_DAILY_UNITS,
        "min_coverage_days": MIN_COVERAGE_DAYS, "outlier_band": OUTLIER_BAND,
        "capture_share": CAPTURE_SHARE, "pilot_share": PILOT_SHARE,
        "scan_groups": list(SCAN_GROUPS), "default_groups": DEFAULT_SCAN_GROUPS,
    }
    data = {"recipes_built": db["built"], "exported": now_utc().isoformat(),
            "econ": econ, "items": items, "names": names}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    sys.stderr.write("Wrote %d items to %s (%.1f MB)\n" % (
        len(items), out_path, out_path.stat().st_size / 1e6))


def print_assumptions(args):
    out("Assumptions: %s server, %s, no Focus, quality 1 output, inputs bought instantly at the lowest ask "
        "in %s, quotes up to %dh old, station fee %s%s, transport %s. Data pulled %s UTC." % (
            args.server, "Premium (4% tax)" if args.premium else "no Premium (8% tax)",
            ", ".join(args.sources), args.max_age,
            ("%s per craft" % fmt(args.station_fee)) if args.station_fee is not None
            else ("%s per 100 nutrition" % fmt(args.fee_per_100)),
            (", daily bonus +%.0f%%" % (args.daily_bonus * 100)) if args.daily_bonus else "",
            ("%s per craft" % fmt(args.transport)) if args.transport is not None else "not included",
            now_utc().strftime("%Y-%m-%d %H:%M")))
    out("")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def city_list(text):
    return [canonical_city(c) for c in text.split(",") if c.strip()]


def add_common(p, market=True):
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--cache-dir", help="where the recipe index is cached")
    p.add_argument("--refresh", action="store_true", help="re-download recipe data now")
    if market:
        p.add_argument("--server", choices=sorted(HOSTS), default="europe")


def add_econ(p):
    p.add_argument("--craft-city", help="city you craft in (default: specialization city)")
    p.add_argument("--sell-city", help="force one sell city")
    p.add_argument("--sources", type=city_list, default=list(BUY_MARKETS),
                   help="comma list of cities to buy inputs in (default: all buy markets)")
    p.add_argument("--sells", type=city_list, default=list(SELL_MARKETS),
                   help="comma list of cities to consider selling in")
    p.add_argument("--rrr", type=float, help="override resource return rate, e.g. 0.1525")
    p.add_argument("--daily-bonus", type=float, default=0.0,
                   help="today's extra production bonus for this category, 0.10 or 0.20 "
                        "(in-game Activities menu)")
    p.add_argument("--premium", action="store_true", help="4%% sales tax instead of 8%%")
    p.add_argument("--fee-per-100", type=float, default=DEFAULT_FEE_PER_100,
                   help="station fee in silver per 100 nutrition (default %(default)s, the maximum allowed)")
    p.add_argument("--station-fee", type=float, help="station fee in silver per craft (overrides formula)")
    p.add_argument("--transport", type=float, help="transport cost in silver per craft")
    p.add_argument("--max-age", type=float, default=MAX_AGE_HOURS, help="max quote age in hours")
    p.add_argument("--budget", type=float, help="silver available; caps the suggested test batch")
    p.add_argument("--avoid-red-zones", action="store_true",
                   help="never buy, craft or sell in Caerleon or the Black Market")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="find item IDs by English name")
    p.add_argument("text")
    p.add_argument("--limit", type=int, default=25)
    add_common(p, market=False)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("recipe", help="show recipe for one item")
    p.add_argument("item")
    add_common(p, market=False)
    p.set_defaults(func=cmd_recipe)

    for name, func, help_text in (("prices", cmd_prices, "current quotes"), ("history", cmd_history, "sales history")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("items", help="comma-separated item IDs")
        p.add_argument("--locations", type=city_list, default=list(SELL_MARKETS))
        if name == "history":
            p.add_argument("--days", type=int, default=14)
        add_common(p)
        p.set_defaults(func=func)

    p = sub.add_parser("evaluate", help="full economics for one item")
    p.add_argument("item")
    add_common(p)
    add_econ(p)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("scan", help="rank craftable items to find leads")
    p.add_argument("--groups", type=lambda s: [g.strip() for g in s.split(",")],
                   help="comma list from: %s (default: all except refining, mounts)" % ", ".join(SCAN_GROUPS))
    p.add_argument("--min-tier", type=int, default=4)
    p.add_argument("--max-tier", type=int, default=8)
    p.add_argument("--min-enchant", type=int, default=0)
    p.add_argument("--max-enchant", type=int, default=3)
    p.add_argument("--prefilter-margin", type=float, default=0.05,
                   help="minimum margin on current quotes before history check")
    p.add_argument("--history-for", type=int, default=400, help="how many leads get a history check")
    p.add_argument("--top", type=int, default=20)
    add_common(p)
    add_econ(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("export-index", help="write the recipe index used by the web app")
    p.add_argument("--out", default="web/data/index.json")
    add_common(p, market=False)
    p.set_defaults(func=cmd_export_index)

    args = ap.parse_args(argv)
    if hasattr(args, "premium"):
        args.tax = PREMIUM_TAX if args.premium else SALES_TAX
    if getattr(args, "avoid_red_zones", False):
        args.sources = [c for c in args.sources if c not in ROUTE_RISK]
        args.sells = [c for c in args.sells if c not in ROUTE_RISK]
        for flag in ("craft_city", "sell_city"):
            if getattr(args, flag) and canonical_city(getattr(args, flag)) in ROUTE_RISK:
                raise SystemExit("--%s conflicts with --avoid-red-zones" % flag.replace("_", "-"))
    if getattr(args, "groups", None):
        bad = [g for g in args.groups if g not in SCAN_GROUPS]
        if bad:
            raise SystemExit("Unknown group(s): %s" % ", ".join(bad))
    try:
        args.func(args)
    except urllib.error.HTTPError as e:
        raise SystemExit("HTTP %s from %s. AODP may be down or rate-limiting; wait a minute." % (e.code, e.url))
    except urllib.error.URLError as e:
        raise SystemExit("Network error: %s. No internet? Use the manual path in SKILL.md." % e.reason)


if __name__ == "__main__":
    main()
