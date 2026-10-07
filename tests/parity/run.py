"""Check that web/core.js and albion_scout.py give the same results.

Run from the repo root after `export-index` (needs Node 18+):
    python tests/parity/run.py

Both sides read the same frozen AODP sample (fixture.json.gz) and the same
option sets (cases.json), then the normalized results are compared.
Numbers must match to 1e-9 relative; text is compared with digits masked,
because Python and JavaScript round exact .5 ties differently when printing.
"""

import argparse
import gzip
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location(
    "albion_scout", ROOT / "albion-craft-market-scout" / "scripts" / "albion_scout.py")
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)

DIGITS = re.compile(r"\d[\d.,]*")


def mask(text):
    return None if text is None else DIGITS.sub("#", text)


def normalize(r):
    sale = r["sale"]
    return {
        "item": r["item"], "craft_city": r["craft_city"], "city_note": r["city_note"],
        "rrr": r["rrr"], "rrr_why": r["rrr_why"], "recipe_index": r["recipe_index"],
        "amount": r["amount"], "raw": r["raw"], "returnable_raw": r["returnable_raw"],
        "returned": r["returned"], "silver": r["silver"], "station_fee": r["station_fee"],
        "fee_note": mask(r["fee_note"]), "transport": r["transport"], "cost": r["cost"],
        "missing": r["missing"],
        "lines": [{k: l[k] for k in ("id", "unit", "city", "age_h", "ext", "returnable", "cheap_vs_avg")}
                  for l in r["lines"]],
        "sale": None if sale is None else {k: sale.get(k) for k in ("city", "mode", "price", "net", "basis", "outlier")},
        "revenue": r["revenue"], "profit": r["profit"], "profit_unit": r["profit_unit"],
        "margin": r["margin"], "breakeven_cap": r["breakeven_cap"], "verdict": r["verdict"],
        "reasons": [mask(x) for x in r["reasons"]], "daily_capacity": r["daily_capacity"],
        "pilot_crafts": r["pilot_crafts"], "pilot_capital": r["pilot_capital"],
        "safe_alt": r["safe_alt"],
    }


def python_results(fixture, cases):
    now = datetime.fromisoformat(fixture["now"])
    quotes = scout.parse_prices(fixture["price_rows"], now)
    hist = scout.summarize_history(fixture["history_rows"], date.fromisoformat(fixture["start"]))
    db = scout.load_db(argparse.Namespace(cache_dir=None, refresh=False))
    out = {}
    for case in cases:
        opts = argparse.Namespace(
            tax=scout.SALES_TAX, sources=list(scout.BUY_MARKETS), sells=list(scout.SELL_MARKETS),
            craft_city=None, sell_city=None, rrr=None, daily_bonus=0.0,
            fee_per_100=scout.DEFAULT_FEE_PER_100, station_fee=None, transport=None,
            max_age=scout.MAX_AGE_HOURS, budget=None)
        for k, v in case["opts"].items():
            setattr(opts, k, v)
        for iid in fixture["items"]:
            if iid not in db["items"]:
                continue  # removed by a game patch since the fixture was captured
            r = scout.evaluate(db, iid, quotes, hist if case["history"] else None, opts)
            out["%s :: %s" % (case["name"], iid)] = normalize(r)
    return out


def compare(a, b, path, diffs):
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        if a != b:
            diffs.append("%s: python=%r js=%r" % (path, a, b))
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > max(1e-6, 1e-9 * max(abs(a), abs(b))):
            diffs.append("%s: python=%r js=%r" % (path, a, b))
    elif isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            compare(a.get(k), b.get(k), "%s.%s" % (path, k), diffs)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append("%s: python has %d entries, js has %d" % (path, len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, "%s[%d]" % (path, i), diffs)
    elif a != b:
        diffs.append("%s: python=%r js=%r" % (path, a, b))


def main():
    with gzip.open(HERE / "fixture.json.gz", "rt", encoding="utf-8") as f:
        fixture = json.load(f)
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    py = python_results(fixture, cases)
    proc = subprocess.run(["node", str(HERE / "run_js.mjs")], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("JavaScript side failed")
    js = json.loads(proc.stdout)
    diffs = []
    compare(py, js, "results", diffs)
    verdicts = {}
    for r in py.values():
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    if diffs:
        sys.stderr.write("\n".join(diffs[:40]) + "\n")
        raise SystemExit("PARITY FAILED: %d differences" % len(diffs))
    print("Parity OK: %d evaluations match (%s)" % (
        len(py), ", ".join("%s %d" % kv for kv in sorted(verdicts.items()))))


if __name__ == "__main__":
    main()
