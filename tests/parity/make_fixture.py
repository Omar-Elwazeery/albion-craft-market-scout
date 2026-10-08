"""Capture a frozen AODP sample for the Python/JavaScript parity test.

Run from the repo root after `export-index`:
    python tests/parity/make_fixture.py

Writes tests/parity/fixture.json.gz: raw price and history rows for a fixed
set of items plus a random sample, their inputs, the reference items that date
each city's history, and the capture time.
"""

import gzip
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "albion_scout", ROOT / "albion-craft-market-scout" / "scripts" / "albion_scout.py")
scout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scout)

FIXED = [
    "T4_BAG", "T4_BAG@1", "T8_BAG@3", "T6_MEAL_STEW_AVALON", "T4_POTION_HEAL@1", "T5_POTION_MOB_RESET",
    "T6_POTION_LAVA", "T5_OFF_ORB_MORGANA", "T4_ARMOR_CLOTH_KEEPER", "T4_2H_CLAYMORE_AVALON",
    "T5_PLANKS", "T4_CAPEITEM_FW_MARTLOCK", "T4_CAPEITEM_SMUGGLER", "T5_2H_SHAPESHIFTER_SET2@1",
    "T4_2H_TOOL_PICK", "T4_MAIN_SWORD", "T7_OFF_HORN_KEEPER@1", "T4_HEAD_GATHERER_ORE",
]


def main():
    index = json.loads((ROOT / "web" / "data" / "index.json").read_text(encoding="utf-8"))
    rng = random.Random(7)
    pool = sorted(i for i in index["items"] if i not in FIXED and index["items"][i]["tier"] >= 4)
    items = FIXED + rng.sample(pool, 22)
    ids = set(items)
    for iid in items:
        for alt in index["items"][iid]["recipes"]:
            ids.update(inp for inp, _, _ in alt["inputs"])
    host = scout.HOSTS["europe"]
    now = scout.now_utc()
    start = scout.history_start(now, 14)
    price_rows = scout.fetch_price_rows(host, ids, scout.SELL_MARKETS)
    history_rows = scout.fetch_history_rows(host, ids, scout.SELL_MARKETS, start, now.date())
    fixture = {"now": now.isoformat(), "start": start.isoformat(), "items": items,
               "price_rows": price_rows, "history_rows": history_rows}
    out = Path(__file__).with_name("fixture.json.gz")
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(fixture, f, separators=(",", ":"))
    sys.stderr.write("Wrote %s: %d items, %d price rows, %d history rows\n" % (
        out, len(items), len(price_rows), len(history_rows)))


if __name__ == "__main__":
    main()
