"""Check that web/core.js and albion_scout.py give the same results.

Run from the repo root after `export-index` (needs Node 18+):
    python tests/parity/run.py

Both sides read the same frozen AODP sample (fixture.json.gz) and the same
option sets (cases.json), then the normalized results are compared.
Numbers must match to 1e-9 relative; text is compared with digits masked,
because Python and JavaScript round exact .5 ties differently when printing.

The sample is also packed into a web snapshot (`snapshot` command format);
both sides unpack it and must agree with each other and with the direct run.
"""

import gzip
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location(
    "albion_scout", ROOT / "albion-craft-market-scout" / "scripts" / "albion_scout.py")
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)

DIGITS = re.compile(r"\d[\d.,]*")
SNAPSHOT_CASE = "defaults, from the web snapshot"


def mask(text):
    return None if text is None else DIGITS.sub("#", text)


def normalize(r):
    sale = r["sale"]
    h = sale["hist"] if sale else None
    return {
        "item": r["item"], "craft_city": r["craft_city"], "city_note": r["city_note"],
        "rrr": r["rrr"], "rrr_why": r["rrr_why"], "recipe_index": r["recipe_index"],
        "amount": r["amount"], "raw": r["raw"], "returnable_raw": r["returnable_raw"],
        "returned": r["returned"], "silver": r["silver"], "station_fee": r["station_fee"],
        "fee_note": mask(r["fee_note"]), "transport": r["transport"], "cost": r["cost"],
        "missing": r["missing"],
        "lines": [{k: l[k] for k in ("id", "unit", "cost_unit", "city", "age_h", "ext", "returnable",
                                     "cheap_vs_avg", "buy_limit")} for l in r["lines"]],
        "sale": None if sale is None else {k: sale.get(k) for k in ("city", "mode", "price", "net", "basis", "outlier")},
        "sale_hist": None if h is None else {k: h[k] for k in (
            "units7", "days7", "vwap7", "units14", "window_end", "lag_days", "last_sale", "quiet_days")},
        "revenue": r["revenue"], "profit": r["profit"], "profit_unit": r["profit_unit"],
        "margin": r["margin"], "breakeven_cap": r["breakeven_cap"], "sell_limit": r["sell_limit"],
        "breakeven_price": r["breakeven_price"], "verdict": r["verdict"],
        "reasons": [mask(x) for x in r["reasons"]], "daily_capacity": r["daily_capacity"],
        "pilot_crafts": r["pilot_crafts"], "pilot_capital": r["pilot_capital"],
        "safe_alt": r["safe_alt"],
    }


def run_cases(db, fixture, cases, quotes, hist, out, suffix=""):
    for case in cases:
        opts = scout.default_opts(**case["opts"])
        for iid in fixture["items"]:
            if iid not in db["items"]:
                continue  # removed by a game patch since the fixture was captured
            r = scout.evaluate(db, iid, quotes, hist if case["history"] else None, opts)
            out["%s%s :: %s" % (case["name"], suffix, iid)] = normalize(r)


def python_results(fixture, cases, snap):
    now = datetime.fromisoformat(fixture["now"])
    db = scout.load_db(scout.argparse.Namespace(cache_dir=None, refresh=False))
    out = {}
    quotes = scout.parse_prices(fixture["price_rows"], now)
    hist = scout.summarize_history(fixture["history_rows"], date.fromisoformat(fixture["start"]))
    run_cases(db, fixture, cases, quotes, hist, out)
    price_rows, history_rows, start = scout.decode_snapshot(snap)
    quotes = scout.parse_prices(price_rows, now)
    hist = scout.summarize_history(history_rows, start)
    run_cases(db, fixture, [c for c in cases if c["name"] == "defaults"], quotes, hist, out, ", from the web snapshot")
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
    now = datetime.fromisoformat(fixture["now"])
    snap = scout.encode_snapshot("europe", fixture["price_rows"], fixture["history_rows"], now,
                                 date.fromisoformat(fixture["start"]), "fixture")
    py = python_results(fixture, cases, snap)
    with tempfile.TemporaryDirectory() as tmp:
        snap_path = Path(tmp) / "snapshot.json"
        snap_path.write_text(json.dumps(snap), encoding="utf-8")
        proc = subprocess.run(["node", str(HERE / "run_js.mjs"), str(snap_path)], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("JavaScript side failed")
    js = json.loads(proc.stdout)
    diffs = []
    compare(py, js, "results", diffs)
    # The snapshot must carry everything the direct run used.
    for k, r in py.items():
        if k.startswith(SNAPSHOT_CASE):
            direct = py[k.replace(SNAPSHOT_CASE, "defaults", 1)]
            compare(direct, r, "snapshot vs direct " + k, diffs)
    verdicts = {}
    for k, r in py.items():
        if not k.startswith(SNAPSHOT_CASE):
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
    if diffs:
        sys.stderr.write("\n".join(diffs[:40]) + "\n")
        raise SystemExit("PARITY FAILED: %d differences" % len(diffs))
    print("Parity OK: %d evaluations match (%s), snapshot round trip included" % (
        len(py), ", ".join("%s %d" % kv for kv in sorted(verdicts.items()))))


if __name__ == "__main__":
    main()
