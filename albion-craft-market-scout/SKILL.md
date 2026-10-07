---
name: albion-craft-market-scout
description: Albion Online craft-profit scout. Finds crafts that make silver and verifies them with live market data - Albion Online Data Project prices and sales history plus ao-bin-dumps recipes - then computes per-craft profit after resource returns, station fee, sales tax, setup fee and transport, and checks real sales volume before suggesting a small test batch. Use when the user asks what to craft or sell for profit in Albion Online, wants a crafting or market strategy (including one from a video) checked, or asks for current item prices on the Europe, Americas or Asia server, even if they never mention a skill or a tool.
license: MIT
compatibility: Works in any AI agent. Best with internet access plus Python 3.8+ (standard library only) to run scripts/albion_scout.py. Without code execution, follow "Manual path".
metadata:
  version: "2.0.0"
  author: "Wezza"
  tags: "albion-online, crafting, markets, silver, prices"
---

# Albion Online Craft Market Scout

Find crafts that make silver, prove the profit with current market data, and recommend a small test batch. A calculator's headline profit is a lead, not an answer.

## Defaults

State these at the top of every answer. Change one only when the user says so.

- Server: Europe.
- No Premium: 8% sales tax.
- No Focus. No crafting specialization. No assumed starting capital.
- Any city. The user will buy inputs in one city and carry them to another.
- Red-zone routes (Caerleon, Black Market) are shown but flagged, with a safe-route comparison.
- Output quality: Normal (quality 1). Higher-quality rolls are a bonus, never counted.
- Inputs bought instantly at the lowest ask, unless the user prefers buy orders.
- Station fee: the maximum, 1,000 silver per 100 nutrition, until the user gives the real one.

If the answer depends on it, ask once, then continue with the defaults if there is no reply:
1. Highest tier the user can craft. T5 and above need Destiny Board unlocks.
2. Silver available for a test batch.
3. Willing to carry goods through red zones? If no, add `--avoid-red-zones`.

## Workflow

### With code execution (preferred)

Run the script from this skill's folder. It needs internet access and nothing outside Python's standard library. The first run downloads recipe data (about 40 MB, cached for 7 days).

1. **Find leads across every craftable category.** Run `python scripts/albion_scout.py scan`. It checks bags, capes, weapons, armor, off-hands, tools, gatherer gear, potions and food at T4-T8, enchantments 0-3, in about 30 seconds. Refining and mounts are left out unless asked for (`--groups refining,mounts`). Narrow only when the user asks, for example `--groups potions,food --max-tier 6`.
2. **Apply the user's strategy if they gave one.** Turn a video or method into scan filters: categories, tiers, cities. Do not assume the item it showcased is the answer.
3. **Verify the top 3-5 leads one at a time.** Run `python scripts/albion_scout.py evaluate ITEM_ID`. Add what you know: `--transport SILVER` (per craft), `--budget SILVER`, `--fee-per-100 N`, `--daily-bonus 0.10`, `--craft-city`, `--sell-city`.
4. **Check the output yourself.** Profit must equal net revenue minus every cost. Confirm that non-returnable inputs got no return, the output count per craft is right, and the sale price is not the highest ask. Drop anything that fails "Decision rules".
5. **Report** using [the report template](references/report-template.md). Show several candidates. Separate high-margin, low-volume crafts from lower-margin, high-volume ones, and red-zone routes from safe ones.

Other commands: `search "master's bag"` finds IDs by name. `recipe T4_BAG@1` shows a recipe. `prices ID,ID` and `history ID` show raw quotes and sales. Add `--json` for structured output, `--server west` or `east` for other servers, `--premium` for 4% tax. Run any command with `--help` for all flags.

### Manual path (no code execution)

Use this when you cannot run Python. URL details and response examples: [data sources](references/data-sources.md).

1. **Find leads.** Use public profit calculators or rankings the user trusts. Set server, no Premium and no Focus if the site allows it. Disclose capped guest previews or unclear defaults. Treat every row as unverified.
2. **Get the recipe.** Open `https://gameinfo-ams.albiononline.com/api/gameinfo/items/{ID}/data` and read `craftingRequirements`. Artifacts, Crystallized Divinity, Avalonian Energy, crests, hearts and the base cape in faction capes get no resource return. Potions make 5 or 10 per craft and most food makes 10.
3. **Get prices.** Open `https://europe.albion-online-data.com/api/v2/stats/prices/{OUTPUT_ID},{INPUT_IDS}.json?locations=Bridgewatch,Fort%20Sterling,Lymhurst,Martlock,Thetford,Caerleon,Brecilien,Black%20Market&qualities=1`.
4. **Get sales.** Open `https://europe.albion-online-data.com/api/v2/stats/history/{OUTPUT_ID}.json?date={today minus 14 days}&end_date={today}&locations={sell cities}&qualities=1&time-scale=24`.
5. **Compute** with the formulas below and apply "Decision rules".

If you cannot open web pages either, give the user these exact URLs and ask them to paste the responses. They can also read prices from the in-game market.

## Formulas (per craft)

Numbers, sources and the city bonus table: [economics](references/economics.md).

```
raw input cost   = sum(quantity x lowest fresh ask, cheapest allowed city per input)
returned value   = return rate x cost of returnable inputs only
station fee      = item value x 0.1125 x (silver per 100 nutrition) / 100
total cost       = raw input cost - returned value + station fee + recipe silver + transport

sale price       = min(lowest ask, 7-day average sale price)         listing a sell order
                 = min(highest buy order, 7-day average sale price)  selling instantly
net revenue      = output count x sale price x (1 - 0.08 tax - 0.025 setup fee)   sell order
                 = output count x sale price x (1 - 0.08 tax)                     instant sell
profit           = net revenue - total cost        margin = profit / total cost
```

Return rate without Focus: 15.25% in a Royal city, Caerleon or Brecilien; 24.8% where the city specializes in the item; 36.7% when refining in the resource's city. The daily bonus raises these (see economics). If transport cost is unknown, report the break-even cap: the most transport can cost before the craft loses money.

## Decision rules

| Check | Rule |
|---|---|
| Quote age | Up to 6 h is fresh. 6-24 h is usable but flagged. Missing or older: no price. |
| Demand | At least 5 units sold per day in the sell city over its latest 7 days of history, with data on at least 4 of those days. |
| No observed sales | Avoid. You cannot recommend an item on unseen demand. |
| Stale history | Sell city's history ends 5 or more days before other cities': watch. |
| Margin | At least 10% after every known cost. |
| Outlier price | Current ask or buy order more than 30% away from the 7-day average: use the lower number and flag it. |
| Cheap input | An input ask far below its own 7-day average may be one small order. Flag it and tell the user to check order depth in game. |
| Test batch | About 5% of one day's observed sales in the sell city, at least 1 craft, capped by the user's budget. |
| Verdict | **pilot**: all checks pass. **watch**: profitable but a check fails. **avoid**: loss, missing data or no observed sales. |

## Gotchas

- `sell_price_max` is the highest ask. It is never the sale price.
- AODP returns `0` with date `0001-01-01` for missing data. That means no data, not a price of 0.
- Add `@n` to an ID only for enchantment 1-4: `T6_MAIN_SWORD@2`, `T5_PLANKS_LEVEL1@1`. Alchemy extracts and fish sauce take no `@`.
- Divide craft cost by the output count. One potion craft makes 5 or 10 potions.
- Resource returns are an average. A small batch can come back above or below it.
- The Black Market only buys, and it is in Caerleon: a red-zone route with full-loot PvP. Its buy orders can be small and short-lived.
- AODP shows prices, not order sizes. The cheapest ask may be for one unit.
- Prices move. Re-check input prices right before buying a batch.
- Do not count journals, quality rolls, Focus or Premium unless the user confirms them. Show any such extra separately.

## Citing sources

Name every source you used: AODP price and history URLs with retrieval time (UTC), the ao-bin-dumps recipe file, the fee and return-rate sources in [economics](references/economics.md), and any calculator site. Quote the data you extracted, not search snippets.
