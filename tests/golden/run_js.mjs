// JavaScript side of the golden tests. Called by tests/golden/run.py with the
// exported test index and the cases with their market rows.
import { readFileSync } from "node:fs";
import { makeCore } from "../../web/core.js";

const data = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const { now, cases, markets } = JSON.parse(readFileSync(process.argv[3], "utf-8"));
const core = makeCore(data);
const nowMs = Date.parse(now);
const start = new Date(nowMs - 14 * 86400000).toISOString().slice(0, 10);

const out = {};
for (const c of cases) {
  const m = markets[c.name];
  const quotes = core.parsePrices(m.price_rows, nowMs);
  const hist = c.use_history === false ? null : core.summarizeHistory(m.history_rows, start);
  out[c.name] = core.evaluate(c.item, quotes, hist, core.defaultOpts(c.opts || {}));
}
process.stdout.write(JSON.stringify(out));
