// Albion craft-profit logic for the browser. A line-by-line port of
// albion-craft-market-scout/scripts/albion_scout.py: same formulas, same
// thresholds, same field names. Constants come from data/index.json, which
// the Python script exports, so the numbers have a single source.
// tests/parity checks both implementations give the same results.
//
// No DOM access here, so Node can run it for the parity test.

export function makeCore(data) {
  const E = data.econ;
  const CITY_BY_NORM = new Map(E.sell_markets.map((c) => [normCity(c), c]));
  const key = (item, city) => item + "|" + city;

  // ---------------------------------------------------------------- helpers

  function canonicalCity(name) {
    const c = CITY_BY_NORM.get(normCity(name));
    if (!c) throw new Error("Unknown city '" + name + "'. Use one of: " + E.sell_markets.join(", "));
    return c;
  }

  function rrrFromBonus(bonus) {
    return bonus > 0 ? 1 - 1 / (1 + bonus) : 0;
  }

  function nameOf(iid) {
    return data.names[iid] || iid;
  }

  // ------------------------------------------------------------ AODP parsing

  function parsePrices(rows, nowMs) {
    const quotes = new Map();
    for (const row of rows) {
      const city = CITY_BY_NORM.get(normCity(row.city));
      if (!city) continue;
      const askT = parseTs(row.sell_price_min_date);
      const bidT = parseTs(row.buy_price_max_date);
      quotes.set(key(row.item_id, city), {
        ask: askT !== null ? row.sell_price_min || null : null,
        ask_age_h: hoursSince(askT, nowMs),
        bid: bidT !== null ? row.buy_price_max || null : null,
        bid_age_h: hoursSince(bidT, nowMs),
      });
    }
    return quotes;
  }

  function summarizeHistory(rows, startDay) {
    const series = new Map();
    let latest = null;
    for (const row of rows) {
      const city = CITY_BY_NORM.get(normCity(row.location));
      if (!city) continue;
      const k = key(row.item_id, city);
      if (!series.has(k)) series.set(k, new Map());
      const daysMap = series.get(k);
      for (const d of row.data || []) {
        const ts = parseTs(d.timestamp);
        if (ts === null) continue;
        const day = dayOf(ts);
        if (day < startDay) continue;
        const cnt = d.item_count || 0;
        const avg = d.avg_price || 0;
        const prev = daysMap.get(day) || [0, 0];
        daysMap.set(day, [prev[0] + cnt, prev[1] + cnt * avg]);
        if (latest === null || day > latest) latest = day;
      }
    }
    // Each city's scans lag by a different number of days, so every series
    // gets its own 7/14-day window ending at its latest bucket.
    const summaries = new Map();
    for (const [k, daysMap] of series) {
      if (daysMap.size === 0) continue;
      const endDay = [...daysMap.keys()].reduce((a, b) => (b > a ? b : a));
      const agg = (n) => {
        const window = new Set();
        for (let i = 0; i < n; i++) window.add(addDays(endDay, -i));
        let units = 0, silver = 0, covered = 0;
        for (const [d, v] of daysMap) {
          if (!window.has(d)) continue;
          units += v[0];
          silver += v[1];
          if (v[0] > 0) covered += 1;
        }
        return [units, units ? silver / units : null, covered];
      };
      const [u7, vwap7, cov7] = agg(7);
      const [u14, vwap14, cov14] = agg(14);
      summaries.set(k, {
        units7: u7, per_day7: u7 / 7, vwap7, days7: cov7,
        units14: u14, per_day14: u14 / 14, vwap14, days14: cov14,
        window_end: endDay, lag_days: daysBetween(endDay, latest),
      });
    }
    return summaries;
  }

  // -------------------------------------------------------------- economics

  function craftRrr(item, city, opts) {
    if (opts.rrr != null) return [opts.rrr, "set by --rrr"];
    if (!E.craft_cities.includes(city)) return [null, "unknown for " + city + "; pass --rrr"];
    let bonus = E.base_bonus;
    let why = pct0(E.base_bonus) + " base bonus";
    if (item.spec === city) {
      const spec = item.refining ? E.refine_spec_bonus : E.craft_spec_bonus;
      bonus += spec;
      why += " + " + pct0(spec) + " city specialization";
    }
    if (opts.daily_bonus) {
      bonus += opts.daily_bonus;
      why += " + " + pct0(opts.daily_bonus) + " daily bonus";
    }
    return [rrrFromBonus(bonus), why];
  }

  function pickCraftCity(item, opts) {
    if (opts.craft_city) return [canonicalCity(opts.craft_city), null];
    const spec = item.spec;
    if (spec && spec !== "Caerleon") return [spec, null];
    let note = null;
    if (spec === "Caerleon") {
      note = "Caerleon specializes in this (24.8% return rate) but needs red-zone hauling; " +
        "compare with --craft-city Caerleon";
    }
    return [null, note];
  }

  function bestAsk(quotes, iid, sources, maxAge) {
    let best = null;
    for (const city of sources) {
      const q = quotes.get(key(iid, city));
      if (!q || !q.ask || q.ask_age_h === null || q.ask_age_h > maxAge) continue;
      if (best === null || q.ask < best.price) best = { price: q.ask, city, age_h: q.ask_age_h };
    }
    return best;
  }

  function sellOptions(iid, quotes, hist, sellCities, tax, maxAge) {
    const options = [];
    for (const city of sellCities) {
      const q = quotes.get(key(iid, city)) || {};
      const h = hist ? hist.get(key(iid, city)) || null : null;
      const vwap = h ? h.vwap7 : null;
      if (city !== "Black Market" && q.ask && q.ask_age_h != null && q.ask_age_h <= maxAge) {
        const price = vwap == null ? q.ask : Math.min(q.ask, vwap);
        const basis = vwap == null || q.ask <= vwap ? "lowest ask" : "7-day avg sale price, below lowest ask";
        options.push({
          city, mode: "sell order", price, basis,
          net: price * (1 - tax - E.setup_fee), age_h: q.ask_age_h,
          ask: q.ask, vwap7: vwap, hist: h,
          outlier: vwap != null && Math.abs(q.ask - vwap) / vwap > E.outlier_band,
        });
      }
      if (q.bid && q.bid_age_h != null && q.bid_age_h <= maxAge) {
        const price = vwap == null ? q.bid : Math.min(q.bid, vwap);
        const basis = vwap == null || q.bid <= vwap ? "highest buy order" : "7-day avg sale price, below highest buy order";
        options.push({
          city, mode: "instant sell to buy order", price, basis,
          net: price * (1 - tax), age_h: q.bid_age_h, ask: q.ask ?? null,
          bid: q.bid, vwap7: vwap, hist: h,
          // A buy order below the average is normal; only one far above it is suspicious.
          outlier: vwap != null && q.bid > (1 + E.outlier_band) * vwap,
        });
      }
    }
    return options;
  }

  function evaluate(iid, quotes, hist, opts, nested = false) {
    const item = data.items[iid];
    if (!item) throw new Error("No recipe for '" + iid + "'.");
    let [craftCity, cityNote] = pickCraftCity(item, opts);
    // No specialization city: every Royal city gives the same return rate.
    const [rrr, rrrWhy] = craftRrr(item, craftCity || E.royal[0], opts);
    const tax = opts.tax;
    let best = null;
    item.recipes.forEach((alt, idx) => {
      const lines = [];
      const missing = [];
      for (const [inp, cnt, returnable] of alt.inputs) {
        const q = bestAsk(quotes, inp, opts.sources, opts.max_age);
        if (q === null) missing.push(inp);
        const ih = q && hist ? hist.get(key(inp, q.city)) || null : null;
        const cheap = ih && ih.vwap7 && q.price < (1 - E.outlier_band) * ih.vwap7 ? ih.vwap7 : null;
        lines.push({
          id: inp, name: nameOf(inp), count: cnt, returnable,
          unit: q ? q.price : null, city: q ? q.city : null, age_h: q ? q.age_h : null,
          ext: q ? q.price * cnt : null, cheap_vs_avg: cheap,
        });
      }
      const raw = sum(lines.filter((l) => l.ext !== null).map((l) => l.ext));
      const returnableRaw = sum(lines.filter((l) => l.ext !== null && l.returnable).map((l) => l.ext));
      const returned = returnableRaw * (rrr || 0);
      const value = item.value;
      const valueBasis = value ? value * Math.max(1, alt.amount) : null;
      let stationFee, feeNote;
      if (opts.station_fee != null) {
        stationFee = opts.station_fee;
        feeNote = "set by --station-fee";
      } else if (valueBasis) {
        stationFee = valueBasis * E.nutrition_per_value * opts.fee_per_100 / 100;
        feeNote = "item value " + fmt(valueBasis) + " x " + E.nutrition_per_value + " x " +
          fmt(opts.fee_per_100) + "/100, estimate" + (item.value_complete ? "" : ", some input values unknown");
      } else {
        stationFee = null;
        feeNote = "unknown item value; check the station in game";
      }
      const transport = opts.transport ?? null;
      const cost = raw - returned + alt.silver + (stationFee || 0) + (transport || 0);
      const cand = {
        recipe_index: idx, amount: alt.amount, lines, missing, extra: alt.extra, raw,
        returnable_raw: returnableRaw, returned, silver: alt.silver, station_fee: stationFee,
        fee_note: feeNote, transport, cost,
      };
      if (best === null || (cand.missing.length === 0 && (best.missing.length > 0 || cand.cost < best.cost))) {
        best = cand;
      }
    });

    let optsList = sellOptions(iid, quotes, hist, opts.sells, tax, opts.max_age);
    if (opts.sell_city) {
      const want = canonicalCity(opts.sell_city);
      optsList = optsList.filter((o) => o.city === want);
    }
    // Once history is loaded, only cities with observed sales can be the pick;
    // an ask nobody has paid for is not revenue.
    let pool = optsList;
    if (hist !== null) {
      const seen = optsList.filter((o) => o.hist && o.hist.units7 > 0);
      if (seen.length) pool = seen;
    }
    const sale = maxBy(pool, (o) => o.net);
    if (craftCity === null) {
      const spend = new Map();
      for (const l of best.lines) {
        if (E.royal.includes(l.city) && l.ext) spend.set(l.city, (spend.get(l.city) || 0) + l.ext);
      }
      craftCity = spend.size ? maxBy([...spend.keys()], (c) => spend.get(c)) : E.royal[0];
      cityNote = cityNote || "no city specialization; any Royal city gives the same return rate";
    }
    const res = {
      item: iid, name: nameOf(iid), category: item.category,
      craft_city: craftCity, city_note: cityNote, rrr, rrr_why: rrrWhy, tax, ...best,
      sale, sell_options: optsList, budget: opts.budget ?? null,
    };
    const amount = best.amount;
    if (sale && best.missing.length === 0) {
      const revenue = sale.net * amount;
      res.revenue = revenue;
      res.profit = revenue - best.cost;
      res.profit_unit = res.profit / amount;
      res.margin = best.cost > 0 ? res.profit / best.cost : null;
      res.breakeven_cap = revenue - (best.cost - (best.transport || 0));
    } else {
      Object.assign(res, { revenue: null, profit: null, profit_unit: null, margin: null, breakeven_cap: null });
    }
    [res.verdict, res.reasons] = verdict(res);
    const h = sale ? sale.hist : null;
    res.daily_capacity = null;
    res.pilot_crafts = null;
    res.pilot_capital = null;
    if (h && res.profit_unit) {
      res.daily_capacity = res.profit_unit * E.capture_share * h.per_day7;
      const perCraft = best.raw + best.silver + (best.station_fee || 0);
      let crafts = Math.max(1, Math.ceil(E.pilot_share * h.per_day7 / amount));
      if (opts.budget && perCraft > 0) crafts = Math.min(crafts, Math.floor(opts.budget / perCraft));
      if (crafts > 0 && res.verdict === "pilot") {
        res.pilot_crafts = crafts;
        res.pilot_capital = crafts * perCraft;
      }
    }
    // Same craft with every red-zone city removed, so the user can weigh the risk.
    res.safe_alt = null;
    if (!nested && routeCities(res).some((c) => c in E.route_risk)) {
      const safeOpts = { ...opts };
      safeOpts.sources = opts.sources.filter((c) => !(c in E.route_risk));
      safeOpts.sells = opts.sells.filter((c) => !(c in E.route_risk));
      if (safeOpts.craft_city && canonicalCity(safeOpts.craft_city) in E.route_risk) safeOpts.craft_city = null;
      if (safeOpts.sell_city && canonicalCity(safeOpts.sell_city) in E.route_risk) safeOpts.sell_city = null;
      const alt = evaluate(iid, quotes, hist, safeOpts, true);
      res.safe_alt = {
        craft_city: alt.craft_city, profit: alt.profit, profit_unit: alt.profit_unit,
        margin: alt.margin, verdict: alt.verdict,
        sale: alt.sale ? alt.sale.city + " (" + alt.sale.mode + ")" : null,
      };
    }
    return res;
  }

  function routeCities(r) {
    const cities = [r.craft_city, ...r.lines.map((l) => l.city)];
    if (r.sale) cities.push(r.sale.city);
    return cities;
  }

  function verdict(r) {
    let reasons = [];
    if (r.missing.length) reasons.push("no fresh price for: " + r.missing.join(", "));
    if (r.extra.length) reasons.push("needs non-silver inputs: " + r.extra.join("; "));
    if (r.sale === null) reasons.push("no fresh sell price");
    if (r.rrr === null) reasons.push("return rate unknown for craft city");
    if (reasons.length || r.profit === null) return ["avoid", reasons];
    if (r.profit <= 0) return ["avoid", ["loses " + fmt(-r.profit) + " per craft after fees"]];
    const sale = r.sale;
    const h = sale.hist;
    if (h === null || h.units7 === 0) {
      return ["avoid", ["no observed sales in " + sale.city + " in the last 7 days; demand unverified"]];
    }
    if (r.margin !== null && r.margin < E.min_margin) {
      reasons.push("margin " + pct(r.margin) + " below " + pct(E.min_margin));
    }
    if (h.per_day7 < E.min_daily_units) reasons.push("thin volume: " + h.per_day7.toFixed(1) + "/day in " + sale.city);
    if (h.days7 < E.min_coverage_days) reasons.push("history covers " + h.days7 + " of 7 days");
    if ((h.lag_days || 0) >= 5) {
      reasons.push("sales history for " + sale.city + " is stale: ends " + h.window_end + ", " +
        h.lag_days + " days behind other cities");
    } else if ((h.lag_days || 0) >= 3) {
      reasons.push("history for " + sale.city + " ends " + h.window_end + ", " + h.lag_days + " days behind other cities");
    }
    if (sale.outlier) {
      const quoted = sale.mode.startsWith("instant") ? sale.bid : sale.ask;
      reasons.push("current price " + fmt(quoted) + " is >" + Math.trunc(E.outlier_band * 100) +
        "% from 7-day avg " + fmt(sale.vwap7) + "; used the lower");
    }
    for (const l of r.lines) {
      if (l.cheap_vs_avg) {
        reasons.push("input " + l.id + " ask " + fmt(l.unit) + " is far below its 7-day avg " +
          fmt(l.cheap_vs_avg) + " in " + l.city + "; may be a small order");
      }
    }
    const ages = r.lines.filter((l) => l.age_h !== null).map((l) => l.age_h).concat([sale.age_h]);
    const oldest = Math.max(...ages);
    if (oldest > E.fresh_hours) reasons.push("oldest quote " + ageText(oldest) + " (aging)");
    const risks = [...new Set(routeCities(r).filter((c) => c in E.route_risk).map((c) => E.route_risk[c]))].sort();
    if (risks.length) reasons.push("route risk: " + risks.join(", "));
    const soft = ["oldest quote", "route risk", "history for", "input "];
    const hard = reasons.filter((x) => !soft.some((s) => x.startsWith(s)));
    return [hard.length ? "watch" : "pilot", reasons];
  }

  // ------------------------------------------------------------------ scan

  function scanIds({ groups, minTier, maxTier, minEnchant, maxEnchant }) {
    const ids = [];
    for (const [iid, it] of Object.entries(data.items)) {
      if (it.tier < minTier || it.tier > maxTier) continue;
      if (it.ench < minEnchant || it.ench > maxEnchant) continue;
      if (!it.groups.some((g) => groups.includes(g))) continue;
      ids.push(iid);
    }
    return ids;
  }

  function inputsOf(ids) {
    const inputs = new Set();
    for (const iid of ids) {
      for (const alt of data.items[iid].recipes) for (const [inp] of alt.inputs) inputs.add(inp);
    }
    return inputs;
  }

  // Stage 1 ranks on current quotes; stage 2 re-checks the best with sales history.
  async function scan(scanOpts, opts, net, progress = () => {}) {
    const ids = scanIds(scanOpts);
    if (!ids.length) throw new Error("No craftable items match these filters.");
    const inputs = inputsOf(ids);
    progress({ stage: "prices", text: "Scanning " + ids.length + " items (" + inputs.size + " inputs)" });
    const quotes = await net.fetchQuotes([...ids, ...inputs], E.sell_markets, progress);
    const stage1 = [];
    for (const iid of ids) {
      const r = evaluate(iid, quotes, null, opts);
      if (r.profit !== null && r.margin !== null && r.margin >= scanOpts.prefilterMargin) stage1.push(r);
    }
    stage1.sort((a, b) => b.margin - a.margin);
    const shortlist = stage1.slice(0, scanOpts.historyFor).map((r) => r.item);
    progress({ stage: "history", text: stage1.length + " look profitable; checking sales history for " + shortlist.length });
    const hist = shortlist.length ? await net.fetchHistory(shortlist, opts.sells, 14, progress) : new Map();
    let final = shortlist.map((iid) => evaluate(iid, quotes, hist, opts));
    final = final.filter((r) => r.profit !== null && r.profit > 0);
    const order = { pilot: 0, watch: 1, avoid: 2 };
    final.sort((a, b) => order[a.verdict] - order[b.verdict] || (b.daily_capacity || 0) - (a.daily_capacity || 0));
    return { scanned: ids.length, profitable_on_quotes: stage1.length, results: final, quotes, hist };
  }

  async function evaluateLive(iid, opts, net, progress = () => {}) {
    const item = data.items[iid];
    if (!item) throw new Error("No recipe for '" + iid + "'.");
    const ids = new Set([iid]);
    for (const alt of item.recipes) for (const [inp] of alt.inputs) ids.add(inp);
    const quotes = await net.fetchQuotes([...ids], E.sell_markets, progress);
    // Output history decides the sale price; input history flags suspiciously cheap asks.
    const locations = [...new Set([...opts.sells, ...opts.sources])].sort();
    const hist = await net.fetchHistory([...ids], locations, 14, progress);
    return evaluate(iid, quotes, hist, opts);
  }

  function defaultOpts(overrides = {}) {
    const o = {
      tax: E.sales_tax, sources: [...E.buy_markets], sells: [...E.sell_markets],
      craft_city: null, sell_city: null, rrr: null, daily_bonus: 0,
      fee_per_100: E.default_fee_per_100, station_fee: null, transport: null,
      max_age: E.max_age_hours, budget: null, ...overrides,
    };
    if (o.avoid_red_zones) {
      o.sources = o.sources.filter((c) => !(c in E.route_risk));
      o.sells = o.sells.filter((c) => !(c in E.route_risk));
    }
    return o;
  }

  return {
    E, data, key, nameOf, canonicalCity, parsePrices, summarizeHistory, evaluate, verdict,
    sellOptions, routeCities, scan, scanIds, evaluateLive, defaultOpts,
  };
}

// ------------------------------------------------------------------ network

// Fetches from the Albion Online Data Project with polite pacing:
// requests start at least `gapMs` apart (AODP allows 180 per minute).
export function makeNet(host, { gapMs = 350, maxInFlight = 3, fetchFn = globalThis.fetch, nowFn = Date.now } = {}) {
  let nextStart = 0;
  let inFlight = 0;
  const waiters = [];

  async function slot() {
    while (inFlight >= maxInFlight) await new Promise((r) => waiters.push(r));
    inFlight += 1;
    const wait = Math.max(0, nextStart - nowFn());
    nextStart = Math.max(nextStart, nowFn()) + gapMs;
    if (wait) await sleep(wait);
  }

  function release() {
    inFlight -= 1;
    const w = waiters.shift();
    if (w) w();
  }

  async function getJson(url) {
    for (let attempt = 0; attempt < 4; attempt++) {
      await slot();
      let resp;
      try {
        resp = await fetchFn(url);
      } catch (err) {
        release();
        if (attempt === 3) throw new Error("Network error reaching the price API: " + err.message);
        await sleep(3000 * (attempt + 1));
        continue;
      }
      release();
      if (resp.ok) return resp.json();
      // The 429 reset header is not exposed to browsers, so wait a fixed time.
      if ((resp.status === 429 || resp.status >= 500) && attempt < 3) {
        await sleep(resp.status === 429 ? 30000 : 5000 * (attempt + 1));
        continue;
      }
      throw new Error("Price API returned HTTP " + resp.status + ". It may be busy; try again in a minute.");
    }
  }

  async function fetchRows(kind, ids, query, progress) {
    const chunks = chunked([...new Set(ids)].sort(), host.length + 40 + query.length);
    let done = 0;
    const parts = await Promise.all(chunks.map(async (chunk) => {
      const rows = await getJson(host + "/api/v2/stats/" + kind + "/" + chunk.join(",") + ".json" + query);
      done += 1;
      progress({ stage: kind, done, total: chunks.length });
      return rows;
    }));
    return parts.flat();
  }

  return {
    host,
    async fetchPriceRows(ids, locations, progress = () => {}) {
      return fetchRows("prices", ids, "?locations=" + locParam(locations) + "&qualities=1", progress);
    },
    async fetchHistoryRows(ids, locations, startDay, endDay, progress = () => {}) {
      const query = "?date=" + startDay + "&end_date=" + endDay + "&locations=" + locParam(locations) +
        "&qualities=1&time-scale=24";
      return fetchRows("history", ids, query, progress);
    },
  };
}

// Binds a core to a network client: fetchQuotes/fetchHistory return parsed maps.
export function bindNet(core, net, nowFn = Date.now) {
  return {
    async fetchQuotes(ids, locations, progress) {
      const rows = await net.fetchPriceRows(ids, locations, progress);
      return core.parsePrices(rows, nowFn());
    },
    async fetchHistory(ids, locations, days, progress) {
      const today = dayOf(nowFn());
      const start = addDays(today, -days);
      const rows = await net.fetchHistoryRows(ids, locations, start, today, progress);
      return core.summarizeHistory(rows, start);
    },
  };
}

// ------------------------------------------------------------------ utilities

export function normCity(name) {
  return (name || "").toLowerCase().replaceAll(" ", "").replaceAll("'", "");
}

export function parseTs(s) {
  if (!s || s.startsWith("0001")) return null;
  const ms = Date.parse(s.endsWith("Z") ? s : s + "Z");
  return Number.isNaN(ms) ? null : ms;
}

export function hoursSince(tsMs, nowMs) {
  return tsMs === null ? null : Math.max(0, (nowMs - tsMs) / 3600000);
}

export function dayOf(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(day, n) {
  return dayOf(Date.parse(day + "T00:00:00Z") + n * 86400000);
}

function daysBetween(fromDay, toDay) {
  return Math.round((Date.parse(toDay + "T00:00:00Z") - Date.parse(fromDay + "T00:00:00Z")) / 86400000);
}

export function chunked(ids, prefixLen, limit = 3900) {
  const chunks = [];
  let chunk = [];
  let size = prefixLen;
  for (const i of ids) {
    const add = i.length + 1;
    if (chunk.length && size + add > limit) {
      chunks.push(chunk);
      chunk = [];
      size = prefixLen;
    }
    chunk.push(i);
    size += add;
  }
  if (chunk.length) chunks.push(chunk);
  return chunks;
}

function locParam(locations) {
  return locations.map((l) => encodeURIComponent(l)).join(",");
}

export function fmt(n) {
  if (n === null || n === undefined) return "n/a";
  return Math.round(n).toLocaleString("en-US");
}

export function pct(x) {
  return x === null || x === undefined ? "n/a" : (x * 100).toFixed(1) + "%";
}

function pct0(x) {
  return (x * 100).toFixed(0) + "%";
}

export function ageText(hours) {
  return hours === null || hours === undefined ? "n/a" : hours.toFixed(1) + "h";
}

function sum(xs) {
  return xs.reduce((a, b) => a + b, 0);
}

// First element with the largest score, like Python's max(key=...).
function maxBy(xs, score) {
  let best = null;
  let bestScore = -Infinity;
  for (const x of xs) {
    const s = score(x);
    if (best === null || s > bestScore) {
      best = x;
      bestScore = s;
    }
  }
  return best;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
