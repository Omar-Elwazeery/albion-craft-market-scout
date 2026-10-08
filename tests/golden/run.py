"""Hand-checked tests for the scout's math, run on both Python and web/core.js.

Run from the repo root (needs Node 18+; no network):
    python tests/golden/run.py

cases.json holds small markets and the numbers worked out by hand from
references/economics.md. Both implementations must produce them. The parity
test (tests/parity) then checks the two agree on real market data.

Also checks, in Python only: recipe parsing on a frozen excerpt of the game
data (items_excerpt.json), AODP missing-data handling, the snapshot format,
and that paying exactly a "pay at most" limit leaves exactly the target margin.
"""

import argparse
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location(
    "albion_scout", ROOT / "albion-craft-market-scout" / "scripts" / "albion_scout.py")
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)

MISSING = "0001-01-01T00:00:00"
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def close(a, b):
    if a is None or b is None or isinstance(a, (str, bool, list)) or isinstance(b, (str, bool, list)):
        return a == b
    return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))


# ---------------------------------------------------------------- markets

def stamp(now, hours):
    return (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")


def merge(base, extra, key_len=2):
    rows = {tuple(r[:key_len]): r for r in base}
    for r in extra:
        rows[tuple(r[:key_len])] = r
    return list(rows.values())


def market_rows(case, cfg, now):
    """AODP-shaped price and history rows for one case."""
    quotes = merge(cfg["base"]["quotes"], case.get("quotes", []))
    history = merge(cfg["base"]["history"], case.get("history", []))
    drop = {tuple(x) for x in case.get("remove_history", [])}
    history = [h for h in history if tuple(h[:2]) not in drop]
    price_rows = []
    for item, city, ask, ask_age, bid, bid_age in quotes:
        price_rows.append({
            "item_id": item, "city": city, "quality": 1,
            "sell_price_min": ask or 0,
            "sell_price_min_date": stamp(now, ask_age) if ask and ask_age is not None else MISSING,
            "buy_price_max": bid or 0,
            "buy_price_max_date": stamp(now, bid_age) if bid and bid_age is not None else MISSING,
        })
    history_rows = []
    for item, city, spans in history:
        data = []
        for first, last, units, avg in spans:
            for ago in range(first, last - 1, -1):
                day = now.date() - timedelta(days=ago)
                data.append({"timestamp": day.isoformat() + "T00:00:00", "item_count": units, "avg_price": avg})
        history_rows.append({"item_id": item, "location": city, "quality": 1, "data": data})
    return price_rows, history_rows


def resolve(r, path):
    cur = r
    parts = path.split(".")
    i = 0
    while i < len(parts):
        if cur is None:
            return None
        p = parts[i]
        if p == "lines":
            cur = next((l for l in cur["lines"] if l["id"] == parts[i + 1]), None)
            i += 2
            continue
        cur = cur.get(p) if isinstance(cur, dict) else None
        i += 1
    return cur


def check_result(lang, case, r):
    name = "%s [%s]" % (case["name"], lang)
    for path, want in case.get("expect", {}).items():
        got = resolve(r, path)
        check(close(got, want), "%s: %s = %r, expected %r" % (name, path, got, want))
    if "reasons" in case:
        check(r["reasons"] == case["reasons"], "%s: reasons %r, expected %r" % (name, r["reasons"], case["reasons"]))
    for text in case.get("reasons_include", []):
        check(any(text in x for x in r["reasons"]), "%s: no reason contains %r; got %r" % (name, text, r["reasons"]))
    if "fee_note_include" in case:
        check(case["fee_note_include"] in (r["fee_note"] or ""), "%s: fee note %r" % (name, r["fee_note"]))


# ---------------------------------------------------------------- cases

def run_python(cfg, db, now):
    results, markets = {}, {}
    start = scout.history_start(now, 14)
    for case in cfg["cases"]:
        price_rows, history_rows = market_rows(case, cfg, now)
        markets[case["name"]] = {"price_rows": price_rows, "history_rows": history_rows}
        quotes = scout.parse_prices(price_rows, now)
        hist = scout.summarize_history(history_rows, start) if case.get("use_history", True) else None
        r = scout.evaluate(db, case["item"], quotes, hist, scout.default_opts(**case.get("opts", {})))
        results[case["name"]] = r
        if case.get("check_limits"):
            check_limits(case, db, price_rows, history_rows, start, now, r)
    return results, markets


def check_limits(case, db, price_rows, history_rows, start, now, r):
    """Paying exactly an input's limit, or selling at exactly the sale limit, gives MIN_MARGIN."""
    hist = scout.summarize_history(history_rows, start)
    opts = scout.default_opts(**case.get("opts", {}))
    targets = [(l["id"], l["city"], l["buy_limit"], False) for l in r["lines"]]
    targets.append((r["item"], r["sale"]["city"], r["sell_limit"], True))
    for item, city, price, is_sale in targets:
        rows = copy.deepcopy(price_rows)
        for row in rows:
            if row["item_id"] == item and row["city"] == city:
                row["sell_price_min"] = price
                if is_sale:
                    row["buy_price_max"], row["buy_price_max_date"] = 0, MISSING
        r2 = scout.evaluate(db, case["item"], scout.parse_prices(rows, now), hist, opts)
        check(close(r2["margin"], scout.MIN_MARGIN),
              "%s: at the limit %.4f for %s the margin is %r, expected %r" % (
                  case["name"], price, item, r2["margin"], scout.MIN_MARGIN))


def run_js(cfg, db, markets):
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index.json"
        index.write_text(json.dumps(scout.export_data(db)), encoding="utf-8")
        payload = Path(tmp) / "cases.json"
        payload.write_text(json.dumps({"now": cfg["now"], "cases": cfg["cases"], "markets": markets}), encoding="utf-8")
        proc = subprocess.run(["node", str(HERE / "run_js.mjs"), str(index), str(payload)], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("JavaScript side failed")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------- parsing

def check_parsing():
    x = json.loads((HERE / "items_excerpt.json").read_text(encoding="utf-8"))
    db = scout.build_index(x["items_json"], x["names_json"])
    items = db["items"]

    def recipe(iid, idx=0):
        return items[iid]["recipes"][idx] if iid in items else None
    check(recipe("T4_BAG") == {"silver": 0, "amount": 1, "extra": [],
                               "inputs": [["T4_CLOTH", 8, True], ["T4_LEATHER", 8, True]]}, "parse: T4_BAG")
    check(recipe("T4_BAG@1")["inputs"] == [["T4_CLOTH_LEVEL1@1", 8, True], ["T4_LEATHER_LEVEL1@1", 8, True]],
          "parse: enchanted recipes add @n to enchanted inputs")
    check(recipe("T4_POTION_HEAL")["amount"] == 5, "parse: potions make 5 (@amountcrafted)")
    check(recipe("T4_POTION_HEAL@1")["inputs"][2] == ["T1_ALCHEMY_EXTRACT_LEVEL1", 15, True],
          "parse: alchemy extracts take no @")
    check(len(items["T4_2H_DUALSICKLE_UNDEAD"]["recipes"]) == 2, "parse: alternative recipes kept")
    check(recipe("T4_2H_DUALSICKLE_UNDEAD")["inputs"][2] == ["T4_ARTEFACT_2H_DUALSICKLE_UNDEAD", 1, False],
          "parse: artifacts never return (@maxreturnamount 0)")
    check(all(not ret for _, _, ret in recipe("T4_CAPEITEM_FW_MARTLOCK")["inputs"]),
          "parse: faction cape inputs never return")
    check(items["T4_CAPEITEM_FW_MARTLOCK"]["cat"] is None, "parse: faction capes have no crafting category")
    check("T5_FARM_MOABIRD_FW_BRIDGEWATCH_BABY" not in items, "parse: recipes with no market inputs are skipped")
    check(db["values"].get("T4_PLANKS") == 16 and db["values"].get("T5_PLANKS") == 32, "parse: item values")
    check(db["names"].get("T4_BAG") == "Adept's Bag", "parse: English names")
    extra = scout.parse_recipes({"@silver": "0", "craftresource": {"@uniquename": "T4_HIDE", "@count": "10"},
                                 "currency": {"@uniquename": "FACTION_STEPPE", "@amount": "3000"},
                                 "playerfactionstanding": {"@faction": "Steppe", "@minstanding": "120000"}})
    check(extra == [{"silver": 0, "amount": 1, "inputs": [["T4_HIDE", 10, True]],
                     "extra": ["3000 FACTION_STEPPE", "standing Steppe >= 120000"]}], "parse: currency and standing")
    check(scout.market_id("T4_CLOTH_LEVEL1", "1") == "T4_CLOTH_LEVEL1@1" and scout.market_id("T4_BAG", "0") == "T4_BAG",
          "market_id adds @n only above 0")


def check_aodp_and_snapshot():
    now = datetime.fromisoformat("2026-10-08T12:00:00+00:00")
    rows = [{"item_id": "T4_BAG", "city": "Lymhurst", "sell_price_min": 0, "sell_price_min_date": MISSING,
             "buy_price_max": 7000, "buy_price_max_date": "2026-10-08T11:00:00"},
            {"item_id": "T4_BAG", "city": "Nowhere", "sell_price_min": 5, "sell_price_min_date": "2026-10-08T11:00:00",
             "buy_price_max": 0, "buy_price_max_date": MISSING}]
    q = scout.parse_prices(rows, now)
    check(q[("T4_BAG", "Lymhurst")]["ask"] is None and q[("T4_BAG", "Lymhurst")]["bid"] == 7000,
          "AODP: 0 with 0001-01-01 means no price")
    check(len(q) == 1, "AODP: unknown city names are ignored")
    history = [{"item_id": "T4_BAG", "location": "Lymhurst", "data": [
        {"timestamp": "2026-09-23T00:00:00", "item_count": 9, "avg_price": 1},
        {"timestamp": "2026-10-07T00:00:00", "item_count": 3, "avg_price": 100}]}]
    start = scout.history_start(now, 14)
    snap = scout.encode_snapshot("europe", rows, history, now, start, "test")
    p2, h2, s2 = scout.decode_snapshot(snap)
    check(scout.parse_prices(p2, now) == q, "snapshot: prices survive the round trip")
    check(scout.summarize_history(h2, s2) == scout.summarize_history(history, start),
          "snapshot: history survives the round trip (the bucket before the start is dropped)")


def check_backtest():
    """Track-record scoring: the 7 days after the scan decide each pilot."""
    def day(s):
        return datetime.fromisoformat(s).date()
    logs = [{"date": "2026-09-20", "pilots": [
        {"item": "A", "sell_city": "Lymhurst", "mode": "sell order", "price": 120, "breakeven_price": 100},
        {"item": "B", "sell_city": "Martlock", "mode": "sell order", "price": 120, "breakeven_price": 100},
        {"item": "C", "sell_city": "Thetford", "mode": "sell order", "price": 120, "breakeven_price": 100}]}]
    daily = {
        # A: 7 days x 10 units at 110 in Sep 21-27, plus a sale on the scan day that must not count.
        ("A", "Lymhurst"): dict([(day("2026-09-20"), (99, 99 * 1))] +
                                [(day("2026-09-%02d" % d), (10, 10 * 110)) for d in range(21, 28)]),
        # B: 3 units a day at 90: below break-even and below 5 a day.
        ("B", "Martlock"): {day("2026-09-%02d" % d): (3, 3 * 90) for d in range(21, 28)},
        # C: no data in the week after.
        ("C", "Thetford"): {day("2026-09-28"): (50, 50 * 200)},
    }
    s = scout.score_pilots(logs, daily)
    check((s["pilots"], s["scored"], s["no_data"], s["made_money"], s["demand_held"]) == (3, 2, 1, 1, 1),
          "backtest: counts %r" % ({k: s[k] for k in ("pilots", "scored", "no_data", "made_money", "demand_held")},))
    a = s["rows"][0]
    check(a["units"] == 70 and close(a["avg_price"], 110), "backtest: only the 7 days after the scan count: %r" % a)


# ---------------------------------------------------------------- main

def main():
    cfg = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    db = json.loads((HERE / "db.json").read_text(encoding="utf-8"))
    now = datetime.fromisoformat(cfg["now"])
    py, markets = run_python(cfg, db, now)
    js = run_js(cfg, db, markets)
    for case in cfg["cases"]:
        check_result("python", case, py[case["name"]])
        check_result("js", case, js[case["name"]])
    check_parsing()
    check_aodp_and_snapshot()
    check_backtest()
    if failures:
        sys.stderr.write("\n".join(failures) + "\n")
        raise SystemExit("GOLDEN TESTS FAILED: %d problems" % len(failures))
    print("Golden tests OK: %d hand-checked cases on Python and JavaScript, plus parsing and snapshot checks" % (
        len(cfg["cases"])))


if __name__ == "__main__":
    main()
