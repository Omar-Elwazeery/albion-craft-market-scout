// Page wiring for Albion Craft Scout. All profit math lives in core.js.
import { makeCore, makeNet, bindNet, fmt, pct, ageText } from "./core.js";

const $ = (id) => document.getElementById(id);
const GROUP_LABELS = {
  bags: "Bags", capes: "Capes", weapons: "Weapons", armor: "Armor", offhands: "Off-hands",
  tools: "Tools", "gatherer-gear": "Gatherer gear", potions: "Potions", food: "Food",
  mounts: "Mounts", refining: "Refining",
};
const CITY_VAR = {
  Bridgewatch: "--city-bridgewatch", "Fort Sterling": "--city-fortsterling", Lymhurst: "--city-lymhurst",
  Martlock: "--city-martlock", Thetford: "--city-thetford", Caerleon: "--city-caerleon",
  Brecilien: "--city-brecilien", "Black Market": "--city-blackmarket",
};
const SETTINGS_KEY = "albion-craft-scout-settings";

let core = null;
let searchIndex = [];
let lastResults = [];
let busy = false;

// ------------------------------------------------------------------ boot

boot().catch((err) => showStatus(errorBanner("The page could not load its recipe data: " + err.message +
  ". Reload to try again.")));

async function boot() {
  const data = await fetch("data/index.json").then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
  core = makeCore(data);
  buildControls();
  loadSettings();
  searchIndex = Object.keys(data.items).map((id) => ({ id, name: data.names[id] || id, lower: ((data.names[id] || "") + " " + id).toLowerCase() }));
  $("data-built").textContent = "Recipe data built " + shortDate(data.exported) + ".";

  $("settings").addEventListener("submit", (e) => { e.preventDefault(); runScan(); });
  $("settings").addEventListener("change", saveSettings);
  wireSearch();
  $("detail-close").addEventListener("click", () => $("detail").close());
  $("detail").addEventListener("click", (e) => { if (e.target === $("detail")) $("detail").close(); });
  $("detail").addEventListener("close", () => { if (location.hash) history.replaceState(null, "", location.pathname); });
  window.addEventListener("hashchange", openFromHash);

  const sample = await fetch("data/sample-scan.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (sample && sample.results && sample.results.length) {
    showResults(sample.results, {
      example: true,
      summary: sample.scanned + " items scanned, " + sample.profitable_on_quotes + " positive on quotes",
      when: sample.generated, server: sample.server,
    });
  } else {
    showResults([], { example: true });
  }
  openFromHash();
}

function buildControls() {
  const E = core.E;
  $("groups").innerHTML = E.scan_groups.map((g) =>
    `<label for="g-${g}"><input type="checkbox" id="g-${g}" value="${g}"${E.default_groups.includes(g) ? " checked" : ""}> ${GROUP_LABELS[g] || g}</label>`).join("");
  const tierOpts = [4, 5, 6, 7, 8].map((t) => `<option value="${t}">T${t}</option>`).join("");
  const enchOpts = [0, 1, 2, 3, 4].map((t) => `<option value="${t}">.${t}</option>`).join("");
  $("min-tier").innerHTML = tierOpts;
  $("max-tier").innerHTML = tierOpts;
  $("min-ench").innerHTML = enchOpts;
  $("max-ench").innerHTML = enchOpts;
  $("min-tier").value = "4";
  $("max-tier").value = "8";
  $("min-ench").value = "0";
  $("max-ench").value = "3";
  $("fee").value = String(E.default_fee_per_100);
}

// -------------------------------------------------------------- settings

function readSettings() {
  const num = (id) => {
    const v = $(id).value.trim();
    return v === "" ? null : Math.max(0, Number(v));
  };
  return {
    server: $("server").value,
    premium: $("premium").checked,
    avoidRed: $("avoid-red").checked,
    budget: num("budget"),
    fee: num("fee") ?? core.E.default_fee_per_100,
    transport: num("transport"),
    dailyBonus: Number($("daily-bonus").value) || 0,
    groups: [...document.querySelectorAll("#groups input:checked")].map((i) => i.value),
    minTier: Number($("min-tier").value), maxTier: Number($("max-tier").value),
    minEnchant: Number($("min-ench").value), maxEnchant: Number($("max-ench").value),
  };
}

function optsFrom(s, { daily = false } = {}) {
  return core.defaultOpts({
    tax: s.premium ? core.E.premium_tax : core.E.sales_tax,
    avoid_red_zones: s.avoidRed,
    fee_per_100: s.fee,
    transport: s.transport,
    budget: s.budget,
    daily_bonus: daily ? s.dailyBonus : 0,
  });
}

function saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(readSettings())); } catch (_) { /* storage blocked */ }
}

function loadSettings() {
  let s = null;
  try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); } catch (_) { s = null; }
  if (!s) return;
  $("server").value = s.server || "europe";
  $("premium").checked = !!s.premium;
  $("avoid-red").checked = !!s.avoidRed;
  $("budget").value = s.budget ?? "";
  $("fee").value = s.fee ?? core.E.default_fee_per_100;
  $("transport").value = s.transport ?? "";
  $("daily-bonus").value = String(s.dailyBonus || 0);
  if (Array.isArray(s.groups) && s.groups.length) {
    document.querySelectorAll("#groups input").forEach((i) => { i.checked = s.groups.includes(i.value); });
  }
  for (const [id, v] of [["min-tier", s.minTier], ["max-tier", s.maxTier], ["min-ench", s.minEnchant], ["max-ench", s.maxEnchant]]) {
    if (v !== undefined) $(id).value = String(v);
  }
}

// ------------------------------------------------------------------ scan

async function runScan() {
  if (busy) return;
  const s = readSettings();
  if (!s.groups.length) return showStatus(errorBanner("Pick at least one category to scan."));
  if (s.minTier > s.maxTier || s.minEnchant > s.maxEnchant) {
    return showStatus(errorBanner("The 'from' value must not be higher than the 'to' value."));
  }
  setBusy(true);
  try {
    const net = bindNet(core, makeNet(core.E.hosts[s.server]));
    const out = await core.scan(
      { groups: s.groups, minTier: s.minTier, maxTier: s.maxTier, minEnchant: s.minEnchant,
        maxEnchant: s.maxEnchant, prefilterMargin: 0.05, historyFor: 400 },
      optsFrom(s), net, progress);
    showResults(out.results.slice(0, 50), {
      example: false, when: new Date().toISOString(), server: s.server,
      summary: out.scanned + " items scanned, " + out.profitable_on_quotes + " positive on current quotes, " +
        out.results.length + " still positive after sales history",
    });
  } catch (err) {
    showStatus(errorBanner(err.message));
  } finally {
    setBusy(false);
  }
}

function progress(p) {
  const bar = $("progress-bar");
  if (p.total) {
    const label = p.stage === "prices" ? "Fetching current prices" : "Fetching sales history";
    $("progress-text").textContent = label + " (" + p.done + " of " + p.total + ")";
    const base = p.stage === "prices" ? 0 : 80;
    const span = p.stage === "prices" ? 80 : 20;
    bar.style.width = (base + span * p.done / p.total).toFixed(0) + "%";
  } else if (p.text) {
    $("progress-text").textContent = p.text + "...";
  }
}

function setBusy(on) {
  busy = on;
  $("scan-btn").disabled = on;
  $("scan-btn").textContent = on ? "Scanning..." : "Scan the market";
  $("progress").hidden = !on;
  if (on) $("progress-bar").style.width = "0%";
}

// --------------------------------------------------------------- results

function showResults(results, { example, summary, when, server }) {
  lastResults = results;
  const serverName = { europe: "Europe", west: "Americas", east: "Asia" }[server] || "Europe";
  if (example) {
    showStatus(results.length
      ? `<div class="banner"><span><b>Example results</b> from a scan of the ${serverName} market on ${shortDate(when)}. ` +
        `Prices change every hour, so run your own scan before you buy anything.</span>` +
        `<button type="button" class="secondary" id="banner-scan">Scan now</button></div>`
      : `<div class="banner"><span>Pick what to scan on the left, then press <b>Scan the market</b>.</span></div>`);
    const btn = $("banner-scan");
    if (btn) btn.addEventListener("click", runScan);
  } else {
    showStatus(`<strong>${results.length ? results.length + " crafts to look at" : "No profitable crafts found"}</strong>` +
      `<span>${serverName}, ${summary}. Pulled ${shortTime(when)}.</span>`);
  }
  const body = $("results-body");
  if (!results.length) {
    body.innerHTML = example ? "" : `<tr><td class="empty" colspan="8">Nothing passed. Try more categories, ` +
      `other tiers, or a lower station fee if you know the real one.</td></tr>`;
    return;
  }
  body.innerHTML = results.map((r, i) => {
    const s = r.sale;
    const h = s ? s.hist : null;
    const risky = core.routeCities(r).some((c) => c in core.E.route_risk);
    return `<tr class="row" tabindex="0" data-i="${i}">
      <td>${itemCell(r.item, r.name, { showId: false })}</td>
      <td>${cityTag(r.craft_city)}</td>
      <td>${s ? cityTag(s.city) : "-"}<div class="sub-line">${s ? (s.mode.startsWith("instant") ? "instant sell" : "sell order") : ""}${risky ? '<span class="chip risk">red zone</span>' : ""}</div></td>
      <td class="num">${fmt(r.cost)}</td>
      <td class="num ${r.profit > 0 ? "profit" : "loss"}">${fmt(r.profit)}${r.amount > 1 ? `<br><span class="mono">${r.amount} per craft</span>` : ""}</td>
      <td class="num">${pct(r.margin)}</td>
      <td class="num">${h ? h.per_day7.toFixed(1) : "-"}</td>
      <td><span class="pill ${r.verdict}">${r.verdict}</span></td>
    </tr>`;
  }).join("");
  body.querySelectorAll("tr.row").forEach((tr) => {
    const open = () => openDetail(lastResults[Number(tr.dataset.i)], { fromScan: true, example });
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
  });
}

function showStatus(html) {
  $("status").innerHTML = html;
}

function errorBanner(msg) {
  return `<div class="banner error"><span>${escapeHtml(msg)}</span></div>`;
}

// ---------------------------------------------------------------- detail

async function checkItem(id) {
  if (!core.data.items[id]) {
    showStatus(errorBanner("No recipe for " + id + ". Search by name instead."));
    return;
  }
  const s = readSettings();
  const dlg = $("detail");
  fillDetailHead(id, core.nameOf(id));
  $("detail-body").innerHTML = `<p>Fetching live prices and sales for ${escapeHtml(core.nameOf(id))}...</p>`;
  if (!dlg.open) dlg.showModal();
  try {
    const net = bindNet(core, makeNet(core.E.hosts[s.server]));
    const r = await core.evaluateLive(id, optsFrom(s, { daily: true }), net);
    renderDetail(r, { live: true, settings: s });
  } catch (err) {
    $("detail-body").innerHTML = errorBanner(err.message);
  }
}

function openDetail(r, { example }) {
  fillDetailHead(r.item, r.name);
  renderDetail(r, { live: !example, settings: readSettings() });
  const dlg = $("detail");
  if (!dlg.open) dlg.showModal();
  history.replaceState(null, "", "#item=" + encodeURIComponent(r.item));
}

function fillDetailHead(id, name) {
  $("detail-icon").src = iconUrl(id, 112);
  $("detail-title").textContent = name;
  $("detail-id").textContent = id;
}

function renderDetail(r, { live, settings }) {
  const s = r.sale;
  const h = s ? s.hist : null;
  const parts = [];
  parts.push(`<div class="verdict-box">
    <div><span class="pill ${r.verdict}">${r.verdict}</span>
    ${live ? "" : ' <span class="mono">from the example scan</span> <button type="button" class="secondary" id="recheck">Check live prices</button>'}</div>
    ${r.reasons.length ? `<ul>${r.reasons.map((x) => `<li>${escapeHtml(x)}</li>`).join("")}</ul>` : "<p>Every check passed.</p>"}
  </div>`);

  const facts = [
    ["Craft in", cityTag(r.craft_city) + `<br><span class="mono">return rate ${pct(r.rrr)}: ${escapeHtml(r.rrr_why)}</span>`],
    ["Sell in", s ? cityTag(s.city, s.mode) + `<br><span class="mono">${fmt(s.price)} each, ${escapeHtml(s.basis)}, quote ${ageText(s.age_h)} old</span>` : "No fresh sell price"],
    ["Sales in that city", h ? `${fmt(h.units7)} in 7 days (${h.per_day7.toFixed(1)} a day, data on ${h.days7} of 7 days)<br><span class="mono">7-day average ${fmt(h.vwap7)}, history ends ${h.window_end}</span>` : "No recorded sales"],
    ["Output", r.amount + " per craft"],
  ];
  parts.push(`<dl class="facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`);

  parts.push(`<div><h3>Inputs per craft</h3><div class="table-wrap"><table class="grid">
    <thead><tr><th>Input</th><th class="num">Qty</th><th class="num">Unit price</th><th>Bought in</th><th class="num">Quote age</th><th class="num">Cost</th><th>Returned?</th></tr></thead>
    <tbody>${r.lines.map((l) => `<tr>
      <td>${itemCell(l.id, l.name)}</td><td class="num">${l.count}</td><td class="num">${fmt(l.unit)}</td>
      <td>${l.city ? cityTag(l.city) : '<span class="loss">no fresh price</span>'}</td>
      <td class="num">${ageText(l.age_h)}</td><td class="num">${fmt(l.ext)}</td>
      <td>${l.returnable ? "yes" : "no"}</td></tr>`).join("")}</tbody></table></div></div>`);

  const feeRate = s ? r.tax + (s.mode === "sell order" ? core.E.setup_fee : 0) : null;
  const rows = [
    ["Raw input cost", fmt(r.raw)],
    [`Returned (${pct(r.rrr)} of ${fmt(r.returnable_raw)} eligible)`, "-" + fmt(r.returned)],
  ];
  if (r.silver) rows.push(["Recipe silver cost", fmt(r.silver)]);
  rows.push([`Station fee (${escapeHtml(r.fee_note)})`, fmt(r.station_fee)]);
  rows.push(["Transport", r.transport != null ? fmt(r.transport) : "not set"]);
  rows.push(["<b>Total cost</b>", `<b>${fmt(r.cost)}</b>`, "total"]);
  if (s) rows.push([`Sale: ${r.amount} x ${fmt(s.price)}, minus ${pct(feeRate)} tax and fees`, fmt(r.revenue)]);
  rows.push(["<b>Profit per craft</b>", `<b class="${r.profit > 0 ? "profit" : "loss"}">${fmt(r.profit)}</b>`, "total"]);
  rows.push(["Profit per item / margin", `${fmt(r.profit_unit)} / ${pct(r.margin)}`]);
  if (r.transport == null && r.breakeven_cap != null) rows.push(["Most that transport can cost before a loss", fmt(r.breakeven_cap) + " per craft"]);
  parts.push(`<div><h3>Per craft, in silver</h3><table class="sum-table"><tbody>${rows.map(([k, v, cls]) =>
    `<tr${cls ? ` class="${cls}"` : ""}><td>${k}</td><td>${v}</td></tr>`).join("")}</tbody></table></div>`);

  const notes = [];
  if (r.pilot_crafts) notes.push(`Test batch: ${r.pilot_crafts} craft(s), about ${fmt(r.pilot_capital)} silver up front (about 5% of one day's sales${r.budget ? ", capped by your budget" : ""}).`);
  if (r.city_note) notes.push(escapeHtml(r.city_note.replace("compare with --craft-city Caerleon", "the command-line tool can compare it")));
  if (r.safe_alt) {
    notes.push(r.safe_alt.profit == null ? "Without red zones: no complete safe route (missing prices or sales)." :
      `Without red zones: craft in ${r.safe_alt.craft_city}, sell in ${escapeHtml(r.safe_alt.sale)}, profit ${fmt(r.safe_alt.profit)} per craft (${pct(r.safe_alt.margin)} margin), verdict ${r.safe_alt.verdict}.`);
  }
  const others = [...r.sell_options].sort((a, b) => b.net - a.net).slice(0, 5);
  if (others.length) notes.push("Other places to sell (net per item): " + others.map((o) => `${o.city} ${o.mode} ${fmt(o.net)}`).join("; ") + ".");
  if (notes.length) parts.push(`<div><h3>Notes</h3><ul class="notes">${notes.map((n) => `<li>${n}</li>`).join("")}</ul></div>`);

  const cli = cliCommand(r.item, settings);
  parts.push(`<div><h3>Same check on your computer or in an AI agent</h3><div class="cli"><code id="cli-cmd">${escapeHtml(cli)}</code>
    <button type="button" class="secondary" id="copy-cli">Copy</button></div></div>`);

  $("detail-body").innerHTML = parts.join("");
  const recheck = $("recheck");
  if (recheck) recheck.addEventListener("click", () => checkItem(r.item));
  $("copy-cli").addEventListener("click", async (e) => {
    try {
      await navigator.clipboard.writeText(cli);
      e.target.textContent = "Copied";
    } catch (_) {
      const range = document.createRange();
      range.selectNodeContents($("cli-cmd"));
      getSelection().removeAllRanges();
      getSelection().addRange(range);
    }
  });
}

function cliCommand(id, s) {
  const parts = ["python scripts/albion_scout.py evaluate", id];
  if (s.server !== "europe") parts.push("--server " + s.server);
  if (s.premium) parts.push("--premium");
  if (s.avoidRed) parts.push("--avoid-red-zones");
  if (s.fee !== core.E.default_fee_per_100) parts.push("--fee-per-100 " + s.fee);
  if (s.transport != null) parts.push("--transport " + s.transport);
  if (s.budget != null) parts.push("--budget " + s.budget);
  if (s.dailyBonus) parts.push("--daily-bonus " + s.dailyBonus);
  return parts.join(" ");
}

function openFromHash() {
  const m = location.hash.match(/^#item=([A-Za-z0-9_@]+)$/);
  if (m && core) checkItem(decodeURIComponent(m[1]));
}

// ---------------------------------------------------------------- search

function wireSearch() {
  const input = $("item-query");
  const list = $("item-suggestions");
  let matches = [];
  let active = -1;

  const render = () => {
    if (!matches.length) { list.hidden = true; return; }
    list.innerHTML = matches.map((m, i) => `<li role="option" id="sug-${i}" aria-selected="${i === active}" data-id="${m.id}">
      <img src="${iconUrl(m.id, 64)}" alt="" loading="lazy"><span>${escapeHtml(m.name)}</span><span class="mono">${m.id}</span></li>`).join("");
    list.hidden = false;
  };
  const choose = (id) => {
    list.hidden = true;
    input.value = "";
    history.replaceState(null, "", "#item=" + encodeURIComponent(id));
    checkItem(id);
  };

  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    active = -1;
    if (q.length < 2) { matches = []; render(); return; }
    const words = q.split(/\s+/);
    matches = searchIndex.filter((x) => words.every((w) => x.lower.includes(w))).slice(0, 12);
    render();
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(matches.length - 1, active + 1); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); render(); e.preventDefault(); }
    else if (e.key === "Escape") { list.hidden = true; }
  });
  $("item-search").addEventListener("submit", (e) => {
    e.preventDefault();
    const pick = matches[active >= 0 ? active : 0];
    const raw = input.value.trim().toUpperCase();
    if (pick) choose(pick.id);
    else if (core.data.items[raw]) choose(raw);
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li");
    if (li) { e.preventDefault(); choose(li.dataset.id); }
  });
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
}

// --------------------------------------------------------------- helpers

function itemCell(id, name, { showId = true } = {}) {
  const [base, ench] = id.split("@");
  const tier = /^T(\d)/.exec(base);
  const label = tier ? `${tier[1]}.${ench || 0}` : "";
  const clean = (name || id).replace(/\s*\(\d\.\d\)$/, "");
  return `<div class="item-cell" title="${id}"><img src="${iconUrl(id, 64)}" alt="" loading="lazy" width="36" height="36">
    <div><span class="name">${escapeHtml(clean)}</span>${label ? `<span class="tier">${label}</span>` : ""}${showId ? `<span class="mono">${id}</span>` : ""}</div></div>`;
}

function cityTag(city, how) {
  const v = CITY_VAR[city];
  return `<span class="city" style="${v ? `--city-color: var(${v})` : ""}">${escapeHtml(city)}${how ? ` <span class="how">${escapeHtml(how)}</span>` : ""}</span>`;
}

function iconUrl(id, size) {
  return "https://render.albiononline.com/v1/item/" + encodeURIComponent(id) + ".png?quality=1&size=" + size;
}

function shortDate(iso) {
  if (!iso) return "an earlier date";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function shortTime(iso) {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
