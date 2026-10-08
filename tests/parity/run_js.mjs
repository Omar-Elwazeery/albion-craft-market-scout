// JavaScript side of the parity test. Called by tests/parity/run.py with the
// path of a snapshot built from the same fixture.
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { makeCore } from "../../web/core.js";

const here = new URL(".", import.meta.url);
const fixture = JSON.parse(gunzipSync(readFileSync(new URL("fixture.json.gz", here))).toString("utf-8"));
const cases = JSON.parse(readFileSync(new URL("cases.json", here), "utf-8"));
const data = JSON.parse(readFileSync(new URL("../../web/data/index.json", here), "utf-8"));
const snap = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const core = makeCore(data);

const DIGITS = /\d[\d.,]*/g;
const mask = (t) => (t === null || t === undefined ? null : t.replace(DIGITS, "#"));
const pick = (o, keys) => Object.fromEntries(keys.map((k) => [k, o[k] === undefined ? null : o[k]]));

function normalize(r) {
  const h = r.sale ? r.sale.hist : null;
  return {
    item: r.item, craft_city: r.craft_city, city_note: r.city_note, rrr: r.rrr, rrr_why: r.rrr_why,
    recipe_index: r.recipe_index, amount: r.amount, raw: r.raw, returnable_raw: r.returnable_raw,
    returned: r.returned, silver: r.silver, station_fee: r.station_fee, fee_note: mask(r.fee_note),
    transport: r.transport, cost: r.cost, missing: r.missing,
    lines: r.lines.map((l) => pick(l, ["id", "unit", "cost_unit", "city", "age_h", "ext", "returnable",
      "cheap_vs_avg", "buy_limit"])),
    sale: r.sale === null ? null : pick(r.sale, ["city", "mode", "price", "net", "basis", "outlier"]),
    sale_hist: h === null ? null : pick(h, ["units7", "days7", "vwap7", "units14", "window_end", "lag_days",
      "last_sale", "quiet_days"]),
    revenue: r.revenue, profit: r.profit, profit_unit: r.profit_unit, margin: r.margin,
    breakeven_cap: r.breakeven_cap, sell_limit: r.sell_limit, breakeven_price: r.breakeven_price,
    verdict: r.verdict, reasons: r.reasons.map(mask),
    daily_capacity: r.daily_capacity, pilot_crafts: r.pilot_crafts, pilot_capital: r.pilot_capital,
    safe_alt: r.safe_alt,
  };
}

function runCases(list, quotes, hist, out, suffix = "") {
  for (const c of list) {
    const opts = core.defaultOpts(c.opts);
    for (const iid of fixture.items) {
      if (!(iid in data.items)) continue; // removed by a game patch since the fixture was captured
      out[c.name + suffix + " :: " + iid] = normalize(core.evaluate(iid, quotes, c.history ? hist : null, opts));
    }
  }
}

const nowMs = Date.parse(fixture.now);
const out = {};
runCases(cases, core.parsePrices(fixture.price_rows, nowMs), core.summarizeHistory(fixture.history_rows, fixture.start), out);
const live = core.fromSnapshot(snap, nowMs);
runCases(cases.filter((c) => c.name === "defaults"), live.quotes, live.hist, out, ", from the web snapshot");
process.stdout.write(JSON.stringify(out));
