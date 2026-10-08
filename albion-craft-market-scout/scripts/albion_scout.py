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
  export-index      write the recipe index the web app loads
  snapshot          write the market snapshot the web app shows on load
  check-game-data   compare the constants below with the latest game data
  backtest          score past pilot verdicts against the sales that followed

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
DUMP_MODIFIERS = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/craftingmodifiers.json"
DUMP_GAMEDATA = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/gamedata.json"
INDEX_MAX_AGE_DAYS = 7

ROYAL = ["Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford"]
BUY_MARKETS = ROYAL + ["Brecilien", "Caerleon"]          # you can buy here
SELL_MARKETS = BUY_MARKETS + ["Black Market"]            # Black Market: sell only
CRAFT_CITIES = ROYAL + ["Brecilien", "Caerleon"]         # 18% base production bonus
ROUTE_RISK = {                                           # reached only through red zones (full-loot PvP)
    "Caerleon": "Caerleon",
    "Black Market": "the Black Market (in Caerleon)",
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
KEY_QUOTE_HOURS = 12   # sale price or biggest input quote older than this: watch
MAX_AGE_HOURS = 24     # AODP drops orders not seen for 24 h anyway
MIN_MARGIN = 0.10      # profit / total cost needed for a pilot
MIN_DAILY_UNITS = 5    # average units sold per day in the sell city (7-day window)
MIN_COVERAGE_DAYS = 4  # days with history data out of the last 7
QUIET_DAYS = 3         # no sales data this many days before the city's latest data: watch
CITY_LAG_NOTE = 3      # a city's history this many days behind other cities is noted
CITY_LAG_HARD = 5      # ... and this many days behind means watch
OUTLIER_BAND = 0.30    # ask more than 30% away from 7-day average is flagged
CAPTURE_SHARE = 0.10   # assume you can sell ~10% of observed daily volume
PILOT_SHARE = 0.05     # test batch: ~5% of one day's observed sales
PREFILTER_MARGIN = 0.05  # scan: margin on current quotes needed before the history check
HISTORY_FOR = 400        # scan: how many of those get a history check
GAME_DATA_CHECKED = "2026-10-08"  # last day check-game-data passed against these constants

# History only reaches AODP when a player opens an item's price chart, so one
# item's series can stop while its city's data goes on. These often-viewed items
# ride along with every history request, so each city's latest data day is known.
REFERENCE_ITEMS = [
    "T4_BAG", "T5_BAG", "T6_BAG", "T4_CAPE", "T5_CAPE", "T4_MAIN_DAGGER", "T4_2H_BOW",
    "T4_ARMOR_CLOTH_SET1", "T4_ARMOR_CLOTH_SET2", "T4_ARMOR_CLOTH_SET3", "T5_ARMOR_CLOTH_SET1",
    "T4_ARMOR_LEATHER_SET1", "T4_ARMOR_LEATHER_SET2", "T4_HEAD_LEATHER_SET3", "T4_HEAD_PLATE_SET1",
    "T4_SHOES_CLOTH_SET1", "T4_SHOES_PLATE_SET1", "T4_PLANKS", "T4_METALBAR", "T4_LEATHER", "T4_CLOTH",
]

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


# Every AODP URL fetched in this run, with the time, so answers can cite them.
FETCH_LOG = []


def fetch_rows(host, kind, ids, query):
    rows = []
    for chunk in chunked(sorted(set(ids)), len(host) + 40 + len(query)):
        path = ",".join(urllib.parse.quote(i, safe="@_") for i in chunk)
        url = "%s/api/v2/stats/%s/%s.json%s" % (host, kind, path, query)
        rows.extend(get_json(url))
        FETCH_LOG.append({"url": url, "retrieved": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")})
    return rows


def fetch_price_rows(host, ids, locations):
    return fetch_rows(host, "prices", ids, "?locations=%s&qualities=1" % loc_param(locations))


def fetch_quotes(host, ids, locations):
    """{(item, city): {ask, ask_age_h, bid, bid_age_h}} for quality 1."""
    return parse_prices(fetch_price_rows(host, ids, locations), now_utc())


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


def history_query(start, end, locations):
    return "?date=%s&end_date=%s&locations=%s&qualities=1&time-scale=24" % (
        start.isoformat(), end.isoformat(), loc_param(locations))


def fetch_history_rows(host, ids, locations, start, end):
    """Raw daily history rows. REFERENCE_ITEMS ride along to date each city's data."""
    return fetch_rows(host, "history", set(ids) | set(REFERENCE_ITEMS), history_query(start, end, locations))


def fetch_history(host, ids, locations, days=14):
    """{(item, city): summary} from daily buckets; first partial bucket dropped."""
    now = now_utc()
    start = history_start(now, days)
    return summarize_history(fetch_history_rows(host, ids, locations, start, now.date()), start)


def summarize_history(rows, start):
    series = {}
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
    # History arrives when a player opens an item's price chart, so each city's
    # data runs to a different day. Every item's window ends on its city's latest
    # day, and days without data inside it count as zero sales.
    city_end = {}
    for (_, city), days_map in series.items():
        if days_map:
            city_end[city] = max(city_end.get(city, max(days_map)), max(days_map))
    latest = max(city_end.values()) if city_end else None
    summaries = {}
    for key, days_map in series.items():
        if not days_map:
            continue
        end_day = city_end[key[1]]
        sold = [d for d, v in days_map.items() if v[0] > 0]
        last_sale = max(sold) if sold else None

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
            "last_sale": last_sale.isoformat() if last_sale else None,
            "quiet_days": (end_day - last_sale).days if last_sale else None,
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


def demand_ok(h):
    """Sales history strong enough for a pilot: volume, coverage, and recent data."""
    return bool(h) and h["per_day7"] >= MIN_DAILY_UNITS and h["days7"] >= MIN_COVERAGE_DAYS \
        and h["quiet_days"] is not None and h["quiet_days"] < QUIET_DAYS


def pick_sale(opts_list, hist, min_net):
    """Best net price, preferring cities that pass the demand checks and still clear
    MIN_MARGIN (net per item of at least min_net), so a slightly cheaper city with
    real sales beats a pricier one without them.

    Once history is loaded, only cities with observed sales can be the pick:
    an ask nobody has paid for is not revenue.
    """
    pool = opts_list
    if hist is not None:
        seen = [o for o in opts_list if o["hist"] and o["hist"]["units7"] > 0]
        good = [o for o in seen if demand_ok(o["hist"]) and min_net is not None and o["net"] >= min_net]
        pool = good or seen or opts_list
    return max(pool, key=lambda o: o["net"]) if pool else None


def add_limits(res):
    """Prices the player can check in game: the most each input may cost, and the
    least the output may sell for, with every other number fixed, for MIN_MARGIN."""
    for l in res["lines"]:
        l["buy_limit"] = None
    res["sell_limit"], res["breakeven_price"] = None, None
    if res["profit"] is None:
        return
    cost, sale = res["cost"], res["sale"]
    cost_max = res["revenue"] / (1 + MIN_MARGIN)
    for l in res["lines"]:
        kept = 1 - (res["rrr"] or 0.0) if l["returnable"] else 1.0
        l["buy_limit"] = l["cost_unit"] + (cost_max - cost) / (l["count"] * kept)
    keep = 1 - res["tax"] - (SETUP_FEE if sale["mode"] == "sell order" else 0.0)
    res["sell_limit"] = cost * (1 + MIN_MARGIN) / (res["amount"] * keep)
    res["breakeven_price"] = cost / (res["amount"] * keep)


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
            # An ask far below what the input really trades at may be one small
            # order, so the cost uses the 7-day average instead.
            cost_unit = (cheap or q["price"]) if q else None
            lines.append({
                "id": inp, "name": name_of(db, inp), "count": cnt, "returnable": returnable,
                "unit": q["price"] if q else None, "cost_unit": cost_unit, "city": q["city"] if q else None,
                "age_h": q["age_h"] if q else None,
                "ext": cost_unit * cnt if q else None, "cheap_vs_avg": cheap,
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
    sale = pick_sale(opts_list, hist,
                     None if best["missing"] else best["cost"] * (1 + MIN_MARGIN) / best["amount"])
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
    add_limits(res)
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
    """(verdict, reasons). Any failed check is a reason; only hard ones block a pilot."""
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
    hard = []

    def add(text, blocks=True):
        reasons.append(text)
        if blocks:
            hard.append(text)
    if r["margin"] is not None and r["margin"] < MIN_MARGIN:
        add("margin %s below %s" % (pct(r["margin"]), pct(MIN_MARGIN)))
    if h["per_day7"] < MIN_DAILY_UNITS:
        add("thin volume: %.1f/day in %s" % (h["per_day7"], sale["city"]))
    if h["days7"] < MIN_COVERAGE_DAYS:
        add("history covers %d of 7 days" % h["days7"])
    if h["quiet_days"] is not None and h["quiet_days"] >= QUIET_DAYS:
        add("no sales data in %s since %s, %d days before that city's latest data" % (
            sale["city"], h["last_sale"], h["quiet_days"]))
    if h["lag_days"] >= CITY_LAG_HARD:
        add("sales history for %s is stale: ends %s, %d days behind other cities" % (
            sale["city"], h["window_end"], h["lag_days"]))
    elif h["lag_days"] >= CITY_LAG_NOTE:
        add("history for %s ends %s, %d days behind other cities" % (
            sale["city"], h["window_end"], h["lag_days"]), blocks=False)
    if sale["outlier"]:
        quoted = sale["bid"] if sale["mode"].startswith("instant") else sale["ask"]
        add("current price %s is >%d%% from 7-day avg %s; used the lower" % (
            fmt(quoted), OUTLIER_BAND * 100, fmt(sale["vwap7"])))
    for l in r["lines"]:
        if l["cheap_vs_avg"]:
            add("input %s ask %s is far below its 7-day avg %s in %s; costed at the average" % (
                l["id"], fmt(l["unit"]), fmt(l["cheap_vs_avg"]), l["city"]), blocks=False)
    main = max(r["lines"], key=lambda l: l["ext"])
    stale_key = False
    if sale["age_h"] > KEY_QUOTE_HOURS:
        add("sale price quote is %s old (limit %dh)" % (age_text(sale["age_h"]), KEY_QUOTE_HOURS))
        stale_key = True
    if main["age_h"] > KEY_QUOTE_HOURS:
        add("main input %s quote is %s old (limit %dh)" % (main["id"], age_text(main["age_h"]), KEY_QUOTE_HOURS))
        stale_key = True
    ages = [l["age_h"] for l in r["lines"]] + [sale["age_h"]]
    if not stale_key and max(ages) > FRESH_HOURS:
        add("oldest quote %s (aging)" % age_text(max(ages)), blocks=False)
    risks = sorted({ROUTE_RISK[c] for c in route_cities(r) if c in ROUTE_RISK})
    if risks:
        add("route risk: red zones (full-loot PvP) to reach " + " and ".join(risks), blocks=False)
    return ("watch" if hard else "pilot"), reasons


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def limit_text(x):
    if x is None:
        return "n/a"
    return fmt(x) if x > 0 else "none"


def checklist(r):
    """What to confirm on the in-game market before buying, since AODP shows no order sizes."""
    if r["profit"] is None or r["verdict"] == "avoid":
        return []
    crafts = r["pilot_crafts"] or 1
    s = r["sale"]
    items = []
    for l in r["lines"]:
        items.append("In %s, you can buy %d %s at %s or less each." % (
            l["city"], l["count"] * crafts, l["name"], limit_text(l["buy_limit"])))
    units = r["amount"] * crafts
    if s["mode"] == "sell order":
        items.append("In %s, the cheapest %s listing is still %s or more (you will list %d)." % (
            s["city"], r["name"], limit_text(r["sell_limit"]), units))
    else:
        items.append("In %s, buy orders at %s or more cover %d units." % (s["city"], limit_text(r["sell_limit"]), units))
    if r["station_fee"] is not None:
        items.append("In %s, the crafting window's station fee is at most %s per craft." % (
            r["craft_city"], fmt(r["station_fee"])))
    return items


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
                "7-day avg %s; 14 days: %s units, avg %s; window ends %s, last sale data %s" % (
                    s["city"], fmt(h["units7"]), h["per_day7"], h["days7"], fmt(h["vwap7"]),
                    fmt(h["units14"]), fmt(h["vwap14"]), h["window_end"], h["last_sale"] or "none"))
        else:
            out("- Sales evidence: none in AODP history for %s. Sparse data is not proof of no demand." % s["city"])
    out("- Output per craft: %d" % a)
    out("")
    out("| Input | Qty/craft | Unit price | Bought in | Quote age | Cost | Returns? | Pay at most |")
    out("|---|---:|---:|---|---:|---:|---|---:|")
    for l in r["lines"]:
        unit = fmt(l["unit"])
        if l["cheap_vs_avg"]:
            unit += " (costed at avg %s)" % fmt(l["cost_unit"])
        out("| %s `%s` | %d | %s | %s | %s | %s | %s | %s |" % (
            l["name"], l["id"], l["count"], unit, l["city"] or "MISSING",
            age_text(l["age_h"]), fmt(l["ext"]), "yes" if l["returnable"] else "no", limit_text(l["buy_limit"])))
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
    if r["sell_limit"] is not None:
        out("| Lowest sale price for a %s margin / to break even | %s / %s each |" % (
            pct(MIN_MARGIN), limit_text(r["sell_limit"]), limit_text(r["breakeven_price"])))
    out("")
    if r["pilot_crafts"]:
        out("- Test batch: %d craft(s), about %s silver up front (about 5%% of one day's observed sales%s)" % (
            r["pilot_crafts"], fmt(r["pilot_capital"]), ", capped by --budget" if r.get("budget") else ""))
    checks = checklist(r)
    if checks:
        out("- Check in game before buying (each price limit assumes the other prices stay as shown):")
        for c in checks:
            out("  - " + c)
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
    out("| Item | City | Units 7d | Per day | Days w/ data | Avg price 7d | Units 14d | Avg price 14d | Window end | Last sale |")
    out("|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    for (iid, city), h in sorted(hist.items()):
        out("| %s | %s | %s | %.1f | %d/7 | %s | %s | %s | %s | %s |" % (
            iid, city, fmt(h["units7"]), h["per_day7"], h["days7"], fmt(h["vwap7"]),
            fmt(h["units14"]), fmt(h["vwap14"]), h["window_end"], h["last_sale"] or "-"))


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
        out(json.dumps(dict(r, sources=FETCH_LOG), indent=1, default=str))
        return
    print_assumptions(args)
    print_evaluation(r)
    out("Sources (AODP, retrieved UTC):")
    for f in FETCH_LOG:
        out("- %s %s" % (f["retrieved"], f["url"]))


def scan_ids(db, groups, tiers, enchants):
    ids = []
    for iid, it in db["items"].items():
        if it["tier"] in tiers and it["ench"] in enchants and any(SCAN_GROUPS[g](it) for g in groups):
            if not iid.startswith(("UNIQUE_", "QUESTITEM_")):
                ids.append(iid)
    return ids


def inputs_of(db, ids):
    return {inp for i in ids for alt in db["items"][i]["recipes"] for inp, _, _ in alt["inputs"]}


def scan_stage1(db, ids, quotes, opts, prefilter_margin):
    """Items that clear the pre-filter on current quotes alone, best margin first."""
    stage1 = []
    for iid in ids:
        r = evaluate(db, iid, quotes, None, opts)
        if r["profit"] is not None and r["margin"] is not None and r["margin"] >= prefilter_margin:
            stage1.append(r)
    stage1.sort(key=lambda r: -r["margin"])
    return stage1


def scan_final(db, shortlist, quotes, hist, opts):
    """Shortlist re-checked with sales history: still profitable, pilots first."""
    final = [evaluate(db, iid, quotes, hist, opts) for iid in shortlist]
    final = [r for r in final if r["profit"] is not None and r["profit"] > 0]
    order = {"pilot": 0, "watch": 1, "avoid": 2}
    final.sort(key=lambda r: (order[r["verdict"]], -(r["daily_capacity"] or 0)))
    return final


def cmd_scan(args):
    db = load_db(args)
    host = HOSTS[args.server]
    groups = args.groups or DEFAULT_SCAN_GROUPS
    ids = scan_ids(db, groups, set(range(args.min_tier, args.max_tier + 1)),
                   set(range(args.min_enchant, args.max_enchant + 1)))
    if not ids:
        raise SystemExit("No craftable items match these filters.")
    inputs = inputs_of(db, ids)
    sys.stderr.write("Scanning %d items (%d inputs) across %d markets...\n" % (len(ids), len(inputs), len(SELL_MARKETS)))
    quotes = fetch_quotes(host, set(ids) | inputs, SELL_MARKETS)
    stage1 = scan_stage1(db, ids, quotes, args, args.prefilter_margin)
    shortlist = [r["item"] for r in stage1[:args.history_for]]
    sys.stderr.write("%d items look profitable on current quotes; checking history for %d...\n" % (
        len(stage1), len(shortlist)))
    # Inputs too, so a single cheap order is costed at what the input really trades at.
    hist = fetch_history(host, set(shortlist) | inputs_of(db, shortlist),
                         sorted(set(args.sells) | set(args.sources)), 14) if shortlist else {}
    final = scan_final(db, shortlist, quotes, hist, args)
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


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), default=str)
    os.replace(str(tmp), str(path))
    return path.stat().st_size


def cmd_export_index(args):
    """Write the compact recipe index and constants that the web app (web/core.js) loads."""
    data = export_data(load_db(args))
    size = write_json(args.out, data)
    sys.stderr.write("Wrote %d items to %s (%.1f MB)\n" % (len(data["items"]), args.out, size / 1e6))


def export_data(db):
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
        "key_quote_hours": KEY_QUOTE_HOURS, "max_age_hours": MAX_AGE_HOURS, "min_margin": MIN_MARGIN,
        "min_daily_units": MIN_DAILY_UNITS, "min_coverage_days": MIN_COVERAGE_DAYS,
        "quiet_days": QUIET_DAYS, "city_lag_note": CITY_LAG_NOTE, "city_lag_hard": CITY_LAG_HARD,
        "outlier_band": OUTLIER_BAND, "capture_share": CAPTURE_SHARE, "pilot_share": PILOT_SHARE,
        "prefilter_margin": PREFILTER_MARGIN, "history_for": HISTORY_FOR,
        "reference_items": REFERENCE_ITEMS, "game_data_checked": GAME_DATA_CHECKED,
        "scan_groups": list(SCAN_GROUPS), "default_groups": DEFAULT_SCAN_GROUPS,
    }
    return {"recipes_built": db["built"], "exported": now_utc().isoformat(),
            "econ": econ, "items": items, "names": names}


def default_opts(**overrides):
    """The settings evaluate and scan use when no flag changes them (the web app's defaults)."""
    opts = argparse.Namespace(
        tax=SALES_TAX, sources=list(BUY_MARKETS), sells=list(SELL_MARKETS), craft_city=None,
        sell_city=None, rrr=None, daily_bonus=0.0, fee_per_100=DEFAULT_FEE_PER_100, station_fee=None,
        transport=None, max_age=MAX_AGE_HOURS, budget=None)
    for k, v in overrides.items():
        setattr(opts, k, v)
    return opts


# --------------------------------------------------------------------------
# Snapshot: the market data the web app shows on load
# --------------------------------------------------------------------------

def snapshot_wide_opts():
    """The most favorable scan settings a web visitor can pick. Every visitor's
    shortlist is inside the items that clear the pre-filter under these, so the
    snapshot carries the history any of them needs."""
    return default_opts(tax=PREMIUM_TAX, fee_per_100=0.0)


def encode_snapshot(server, price_rows, history_rows, generated, start, recipes_built):
    """Compact form of AODP rows: prices per item as [city, ask, ask age min, bid, bid age min],
    history per item as [city, [[day after start, units, avg price], ...]]."""
    base = generated.replace(second=0, microsecond=0)
    ci = {c: i for i, c in enumerate(SELL_MARKETS)}

    def minutes(t):
        return int(round((base - t).total_seconds() / 60.0))
    prices = {}
    for row in price_rows:
        city = CITY_BY_NORM.get(norm_city(row.get("city")))
        if city is None:
            continue
        ask_t, bid_t = parse_ts(row.get("sell_price_min_date")), parse_ts(row.get("buy_price_max_date"))
        ask = row.get("sell_price_min") or None if ask_t else None
        bid = row.get("buy_price_max") or None if bid_t else None
        if ask is None and bid is None:
            continue
        prices.setdefault(row["item_id"], []).append([
            ci[city], ask, minutes(ask_t) if ask else None, bid, minutes(bid_t) if bid else None])
    history = {}
    for row in history_rows:
        city = CITY_BY_NORM.get(norm_city(row.get("location")))
        if city is None:
            continue
        days = []
        for d in row.get("data") or []:
            ts = parse_ts(d.get("timestamp"))
            if ts is None or ts.date() < start:
                continue
            days.append([(ts.date() - start).days, d.get("item_count") or 0, d.get("avg_price") or 0])
        if days:
            history.setdefault(row["item_id"], []).append([ci[city], days])
    return {"v": 1, "server": server, "generated": generated.isoformat(),
            "base": base.strftime("%Y-%m-%dT%H:%M:%S"), "start": start.isoformat(),
            "recipes_built": recipes_built, "cities": list(SELL_MARKETS),
            "prices": prices, "history": history}


def decode_snapshot(snap):
    """(price rows, history rows, history start) in the AODP API's own shape."""
    base = datetime.fromisoformat(snap["base"])
    start = datetime.fromisoformat(snap["start"]).date()
    missing = "0001-01-01T00:00:00"

    def stamp(m):
        return (base - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S")
    price_rows = []
    for iid, rows in snap["prices"].items():
        for c, ask, ask_m, bid, bid_m in rows:
            price_rows.append({
                "item_id": iid, "city": snap["cities"][c], "quality": 1,
                "sell_price_min": ask or 0, "sell_price_min_date": stamp(ask_m) if ask else missing,
                "buy_price_max": bid or 0, "buy_price_max_date": stamp(bid_m) if bid else missing})
    history_rows = []
    for iid, series in snap["history"].items():
        for c, days in series:
            history_rows.append({
                "item_id": iid, "location": snap["cities"][c], "quality": 1,
                "data": [{"timestamp": (start + timedelta(days=d)).isoformat() + "T00:00:00",
                          "item_count": n, "avg_price": p} for d, n, p in days]})
    return price_rows, history_rows, start


def pilot_log_entry(r):
    s = r["sale"]
    return {"item": r["item"], "name": r["name"], "craft_city": r["craft_city"], "sell_city": s["city"],
            "mode": s["mode"], "price": s["price"], "breakeven_price": r["breakeven_price"],
            "sell_limit": r["sell_limit"], "amount": r["amount"], "cost": r["cost"], "profit": r["profit"],
            "margin": r["margin"], "per_day7": s["hist"]["per_day7"]}


def cmd_snapshot(args):
    db = load_db(args)
    host = HOSTS[args.server]
    ids = scan_ids(db, list(SCAN_GROUPS), set(range(4, 9)), set(range(0, 5)))
    inputs = inputs_of(db, ids)
    generated = now_utc()
    sys.stderr.write("[%s] prices for %d items and %d inputs...\n" % (args.server, len(ids), len(inputs)))
    price_rows = fetch_price_rows(host, set(ids) | inputs, SELL_MARKETS)
    quotes = parse_prices(price_rows, generated)
    wide = [r["item"] for r in scan_stage1(db, ids, quotes, snapshot_wide_opts(), PREFILTER_MARGIN)]
    start = history_start(generated, 14)
    sys.stderr.write("[%s] history for %d items that could clear the pre-filter...\n" % (args.server, len(wide)))
    history_rows = fetch_history_rows(host, set(wide) | inputs_of(db, wide), SELL_MARKETS, start, generated.date())
    snap = encode_snapshot(args.server, price_rows, history_rows, generated, start, db["built"])
    size = write_json(args.out or "web/data/live-%s.json" % args.server, snap)
    sys.stderr.write("[%s] wrote %s: %d priced items, %d with history, %.1f MB\n" % (
        args.server, args.out or "web/data/live-%s.json" % args.server, len(snap["prices"]),
        len(snap["history"]), size / 1e6))
    if args.log_out:
        # The pilots a visitor with default settings sees, for the track record.
        opts = default_opts()
        hist = summarize_history(history_rows, start)
        default_ids = scan_ids(db, DEFAULT_SCAN_GROUPS, set(range(4, 9)), set(range(0, 4)))
        stage1 = scan_stage1(db, default_ids, quotes, opts, PREFILTER_MARGIN)
        final = scan_final(db, [r["item"] for r in stage1[:HISTORY_FOR]], quotes, hist, opts)
        pilots = [pilot_log_entry(r) for r in final if r["verdict"] == "pilot"]
        write_json(args.log_out, {"date": generated.date().isoformat(), "server": args.server,
                                  "generated": generated.isoformat(), "settings": "web defaults",
                                  "pilots": pilots})
        sys.stderr.write("[%s] logged %d pilots to %s\n" % (args.server, len(pilots), args.log_out))


# --------------------------------------------------------------------------
# Track record: did past pilots sell at or above break-even?
# --------------------------------------------------------------------------

def score_pilots(logs, daily):
    """Score each logged pilot on the 7 days after its scan.

    daily: {(item, city): {date: (units, silver)}}. A pilot made money when the
    average sale price in those days was at or above its break-even price, and
    kept its demand when it averaged MIN_DAILY_UNITS a day. A week with no
    history at all is "no data": AODP only has history when players open charts.
    """
    rows = []
    for log in logs:
        day0 = datetime.fromisoformat(log["date"]).date()
        week = {day0 + timedelta(days=i) for i in range(1, 8)}
        for p in log["pilots"]:
            series = daily.get((p["item"], p["sell_city"]), {})
            units = sum(v[0] for d, v in series.items() if d in week)
            silver = sum(v[1] for d, v in series.items() if d in week)
            avg = silver / units if units else None
            rows.append({
                "date": log["date"], "item": p["item"], "name": p.get("name"), "sell_city": p["sell_city"],
                "mode": p["mode"], "breakeven_price": p["breakeven_price"], "price": p["price"],
                "units": units, "avg_price": avg,
                "made_money": None if avg is None else avg >= p["breakeven_price"],
                "demand_held": None if avg is None else units / 7.0 >= MIN_DAILY_UNITS,
            })
    scored = [r for r in rows if r["made_money"] is not None]
    return {
        "pilots": len(rows), "scored": len(scored), "no_data": len(rows) - len(scored),
        "made_money": sum(1 for r in scored if r["made_money"]),
        "demand_held": sum(1 for r in scored if r["demand_held"]),
        "rows": rows,
    }


def cmd_backtest(args):
    today = now_utc().date()
    logs = []
    for path in sorted(Path(args.log_dir).glob("*-%s.json" % args.server)):
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
            age = (today - datetime.fromisoformat(log["date"]).date()).days
        except (OSError, ValueError, KeyError):
            continue
        if args.min_age <= age <= args.max_age:
            logs.append(log)
    daily = {}
    if any(log["pilots"] for log in logs):
        first = min(datetime.fromisoformat(log["date"]).date() for log in logs) + timedelta(days=1)
        last = min(today, max(datetime.fromisoformat(log["date"]).date() for log in logs) + timedelta(days=7))
        pairs = {(p["item"], p["sell_city"]) for log in logs for p in log["pilots"]}
        rows = fetch_history_rows(HOSTS[args.server], {i for i, _ in pairs}, sorted({c for _, c in pairs}),
                                  first, last)
        for row in rows:
            city = CITY_BY_NORM.get(norm_city(row.get("location")))
            series = daily.setdefault((row["item_id"], city), {})
            for d in row.get("data") or []:
                ts = parse_ts(d.get("timestamp"))
                if ts is not None:
                    n, p = d.get("item_count") or 0, d.get("avg_price") or 0
                    prev = series.get(ts.date(), (0, 0))
                    series[ts.date()] = (prev[0] + n, prev[1] + n * p)
    result = dict(score_pilots(logs, daily), generated=now_utc().isoformat(), server=args.server,
                  days=len(logs), min_age=args.min_age, max_age=args.max_age,
                  first=min((log["date"] for log in logs), default=None),
                  last=max((log["date"] for log in logs), default=None))
    if args.out:
        write_json(args.out, result)
    if args.json:
        out(json.dumps(result, indent=1))
        return
    if not result["scored"]:
        out("Not enough history yet: %d logged scans between %d and %d days old, %d pilots, none with sales data." % (
            len(logs), args.min_age, args.max_age, result["pilots"]))
        return
    out("%s, scans %s to %s: %d pilots, %d with sales data in the following week." % (
        args.server, result["first"], result["last"], result["pilots"], result["scored"]))
    out("Sold at or above break-even: %d of %d (%s). Kept %d+ sales a day: %d of %d (%s)." % (
        result["made_money"], result["scored"], pct(result["made_money"] / result["scored"]),
        MIN_DAILY_UNITS, result["demand_held"], result["scored"], pct(result["demand_held"] / result["scored"])))


# --------------------------------------------------------------------------
# Game-data check: catch a patch that changes the constants above
# --------------------------------------------------------------------------

# Rough minimum item counts per scan group; a much smaller index means the dump changed shape.
MIN_GROUP_ITEMS = {"weapons": 2700, "armor": 1700, "gatherer-gear": 450, "offhands": 350, "capes": 300,
                   "food": 170, "potions": 130, "refining": 90, "tools": 60, "mounts": 40, "bags": 40}
# Recipes that must parse exactly like this; a mismatch means the dump format or the game changed.
CANARY_RECIPES = {
    "T4_BAG": {"amount": 1, "inputs": [["T4_CLOTH", 8, True], ["T4_LEATHER", 8, True]]},
    "T4_BAG@1": {"amount": 1, "inputs": [["T4_CLOTH_LEVEL1@1", 8, True], ["T4_LEATHER_LEVEL1@1", 8, True]]},
    "T4_POTION_HEAL": {"amount": 5, "inputs": [["T4_BURDOCK", 24, True], ["T3_EGG", 6, True]]},
    "T4_2H_DUALSICKLE_UNDEAD": {"amount": 1, "inputs": [
        ["T4_METALBAR", 16, True], ["T4_LEATHER", 16, True], ["T4_ARTEFACT_2H_DUALSICKLE_UNDEAD", 1, False]]},
}
CANARY_VALUES = {"T4_PLANKS": 16, "T4_CLOTH": 16, "T5_PLANKS": 32, "T4_PLANKS_LEVEL1@1": 32}


def find_key(obj, key):
    """Every value stored under `key` anywhere in a JSON tree."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(find_key(v, key))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(find_key(v, key))
    return found


def check_modifiers(locations):
    """Errors when the game's crafting bonuses differ from BASE_BONUS and the specialization tables."""
    errors = []

    def bonuses(loc):
        return {m["@name"]: float(m["@value"]) for m in as_list(loc.get("craftingmodifier"))}

    def base(loc, key):
        v = (loc.get(key) or {}).get("@value")
        return float(v) if v is not None else None
    cities = [loc for loc in locations if base(loc, "craftingbonus") == BASE_BONUS
              and base(loc, "refiningbonus") == BASE_BONUS]
    if not cities:
        seen = sorted({base(loc, "craftingbonus") for loc in locations} - {None})
        return ["no city in game data has a %s base production bonus; game data has %s" % (BASE_BONUS, seen)]
    matched = set()
    for city, cats in CRAFT_SPECIALIZATION.items():
        want = set(cats)
        hits = [loc for loc in cities
                if {k for k, v in bonuses(loc).items() if v == CRAFT_SPEC_BONUS} == want]
        if not hits:
            best = max(cities, key=lambda loc: len(want & set(bonuses(loc))), default=None)
            errors.append("%s: no city in game data gives +%.0f%% crafting to exactly %s. Closest: cluster %s %s" % (
                city, CRAFT_SPEC_BONUS * 100, sorted(want), best and best.get("@clusterid"),
                best and sorted(bonuses(best).items())))
            continue
        loc = hits[0]
        matched.add(loc.get("@clusterid"))
        for res, res_city in REFINE_SPECIALIZATION.items():
            if res_city == city and bonuses(loc).get(res) != REFINE_SPEC_BONUS:
                errors.append("%s: refining %s bonus is %s in game data, expected %s" % (
                    city, res, bonuses(loc).get(res), REFINE_SPEC_BONUS))
    for loc in cities:
        if loc.get("@clusterid") not in matched:
            errors.append("game data has a city (cluster %s) with %.0f%% base bonus that the tables do not "
                          "know: %s" % (loc.get("@clusterid"), BASE_BONUS * 100, sorted(bonuses(loc).items())))
    return errors


def check_index(db):
    """(errors, warnings) for a recipe index built from the dump."""
    errors, warnings = [], []
    counts = {g: 0 for g in SCAN_GROUPS}
    cats = set()
    for iid, it in db["items"].items():
        for g, match in SCAN_GROUPS.items():
            if match(it):
                counts[g] += 1
                if it["cat"] and not specialization_city(it):
                    cats.add(it["cat"])
    for g, n in MIN_GROUP_ITEMS.items():
        if counts.get(g, 0) < n:
            errors.append("only %d craftable %s items in the index (expected at least %d)" % (counts.get(g, 0), g, n))
    for iid, want in CANARY_RECIPES.items():
        it = db["items"].get(iid)
        got = it and {"amount": it["recipes"][0]["amount"], "inputs": it["recipes"][0]["inputs"]}
        if got != want:
            errors.append("recipe for %s parsed as %s, expected %s" % (iid, got, want))
    for iid, want in CANARY_VALUES.items():
        if db["values"].get(iid) != want:
            errors.append("item value of %s is %s, expected %s" % (iid, db["values"].get(iid), want))
    known = {it["cat"] for it in db["items"].values()}
    for city, cs in CRAFT_SPECIALIZATION.items():
        for c in cs:
            if c not in known:
                errors.append("%s specializes in '%s', but no item has that crafting category" % (city, c))
    for c in sorted(cats):
        warnings.append("scanned items in crafting category '%s' have no specialization city" % c)
    return errors, warnings, counts


def cmd_check_game_data(args):
    errors, warnings = [], []
    try:
        mods = get_json(DUMP_MODIFIERS)
        gamedata = get_json(DUMP_GAMEDATA)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SystemExit("Cannot download game data: %s" % e)
    errors += check_modifiers(as_list((mods.get("craftingmodifiers") or {}).get("craftinglocation")))
    fees = sorted({float(v) for v in find_key(gamedata, "@maxuseagefee")})
    if fees != [DEFAULT_FEE_PER_100]:
        errors.append("station fee cap (maxuseagefee) in game data is %s, expected %s" % (fees, DEFAULT_FEE_PER_100))
    db = load_db(args)
    e, w, counts = check_index(db)
    errors += e
    warnings += w
    result = {"checked": now_utc().isoformat(), "ok": not errors, "errors": errors, "warnings": warnings,
              "recipes_built": db["built"], "group_counts": counts}
    if args.out:
        write_json(args.out, result)
    for x in warnings:
        sys.stderr.write("warning: %s\n" % x)
    for x in errors:
        sys.stderr.write("ERROR: %s\n" % x)
    if errors:
        raise SystemExit("Game data no longer matches the constants (%d problems). Check references/economics.md, "
                         "update the constants, then re-run." % len(errors))
    out("Game data matches: base bonus, city specializations, station fee cap, and %d recipe and value checks." % (
        len(CANARY_RECIPES) + len(CANARY_VALUES)))


def print_assumptions(args):
    out("Assumptions: %s server, %s, no Focus, quality 1 output, inputs bought instantly at the lowest ask "
        "in %s, quotes up to %dh old, station fee %s%s, transport %s. Data pulled %s UTC. "
        "Fee and bonus constants last matched game data on %s." % (
            args.server, "Premium (4% tax)" if args.premium else "no Premium (8% tax)",
            ", ".join(args.sources), args.max_age,
            ("%s per craft" % fmt(args.station_fee)) if args.station_fee is not None
            else ("%s per 100 nutrition" % fmt(args.fee_per_100)),
            (", daily bonus +%.0f%%" % (args.daily_bonus * 100)) if args.daily_bonus else "",
            ("%s per craft" % fmt(args.transport)) if args.transport is not None else "not included",
            now_utc().strftime("%Y-%m-%d %H:%M"), GAME_DATA_CHECKED))
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
    p.add_argument("--prefilter-margin", type=float, default=PREFILTER_MARGIN,
                   help="minimum margin on current quotes before history check")
    p.add_argument("--history-for", type=int, default=HISTORY_FOR, help="how many leads get a history check")
    p.add_argument("--top", type=int, default=20)
    add_common(p)
    add_econ(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("export-index", help="write the recipe index used by the web app")
    p.add_argument("--out", default="web/data/index.json")
    add_common(p, market=False)
    p.set_defaults(func=cmd_export_index)

    p = sub.add_parser("snapshot", help="write the market snapshot the web app shows on load")
    p.add_argument("--out", help="default: web/data/live-SERVER.json")
    p.add_argument("--log-out", help="also write today's default-settings pilots here, for the track record")
    add_common(p)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("check-game-data", help="compare the constants with the latest game data")
    p.add_argument("--out", help="also write the result as JSON here")
    add_common(p, market=False)
    p.set_defaults(func=cmd_check_game_data)

    p = sub.add_parser("backtest", help="score past pilots against the sales that followed")
    p.add_argument("--log-dir", required=True, help="folder of snapshot --log-out files (the scan-log branch)")
    p.add_argument("--min-age", type=int, default=8, help="ignore scans younger than this many days")
    p.add_argument("--max-age", type=int, default=30, help="ignore scans older than this many days")
    p.add_argument("--out", help="also write the result as JSON here")
    add_common(p)
    p.set_defaults(func=cmd_backtest)

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
