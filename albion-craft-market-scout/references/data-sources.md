# Data sources

Read this when you query the market or recipe data by hand, or when the script fails.
Facts checked 2026-10-07 against live responses and the AODP server source.

## Contents
- Albion Online Data Project (AODP): prices, history, limits, cities, item IDs
- ao-bin-dumps: recipes, return eligibility, item values, names
- Fallbacks

## AODP

Community-run. Players upload what their game client sees, so data is partial and can lag.
It is not a live order book and not a full sales ledger.

| Server | Host |
|---|---|
| Europe | `https://europe.albion-online-data.com` |
| Americas | `https://west.albion-online-data.com` |
| Asia | `https://east.albion-online-data.com` |

### Current prices

```
GET {host}/api/v2/stats/prices/{ID1,ID2,...}.json?locations={cities}&qualities=1
```

One row per item, city and quality:

```json
{"item_id":"T4_BAG","city":"Bridgewatch","quality":1,
 "sell_price_min":3986,"sell_price_min_date":"2026-10-07T02:45:00",
 "sell_price_max":4071,"sell_price_max_date":"2026-10-07T02:45:00",
 "buy_price_min":1000,"buy_price_min_date":"2026-10-06T18:30:00",
 "buy_price_max":3002,"buy_price_max_date":"2026-10-06T18:30:00"}
```

- `sell_price_min` is the lowest ask: what you pay to buy instantly.
- `buy_price_max` is the highest buy order: what you get when you sell instantly.
- Times are UTC, rounded to 5 minutes. Only orders seen in the last 24 hours count.
- No data comes back as `0` with date `0001-01-01T00:00:00`. Treat it as missing, never as price 0.
- `sell_price_max` is the highest ask. Never use it as a sale price.

### History

```
GET {host}/api/v2/stats/history/{IDs}.json?date=YYYY-MM-DD&end_date=YYYY-MM-DD&locations={cities}&qualities=1&time-scale=24
```

```json
[{"location":"Bridgewatch","item_id":"T4_BAG","quality":1,
  "data":[{"item_count":798,"avg_price":3468,"timestamp":"2026-09-30T00:00:00"}]}]
```

- `item_count` is units traded in that bucket. `avg_price` is silver per unit for those trades.
- `time-scale` is 1, 6 or 24 hours. Anything else becomes 1.
- The first daily bucket can start before `date` and be partial. Drop it.
- Data lags 1 to 3 days, and the lag differs by city. Compare each city on its own latest 7 days.
- Volume-weighted average = sum(item_count x avg_price) / sum(item_count).
- A city/item with no history is left out of the response. Missing history is not proof of zero demand, but you cannot recommend an item on unseen demand.

### Limits

- 180 requests per minute and 300 per 5 minutes.
- Requests without `Accept-Encoding: gzip` are throttled to 6 per minute. Always send gzip.
- HTTP 429 means wait until `RateLimit-Reset` seconds pass.
- URLs must stay under 4096 characters. Put about 100 to 150 IDs in one request.
- On Windows, `curl` may need `--ssl-no-revoke`. Python 3.13+ may reject antivirus or corporate TLS-inspection certificates under its strict X.509 mode; the script turns off only that strict mode.

### Cities

Use these names in `locations`: `Bridgewatch`, `Fort Sterling`, `Lymhurst`, `Martlock`, `Thetford`, `Caerleon`, `Brecilien`, `Black Market`.
URL-encode spaces as `%20`. An unknown name silently returns nothing.
Players cannot buy from the Black Market. It is a place to sell only.

Quality: 1 Normal, 2 Good, 3 Outstanding, 4 Excellent, 5 Masterpiece. Resources, potions, food and tools only have quality 1.

### Item IDs

- `T{tier}_{BASE}`, plus `@{1-4}` for enchanted items: `T4_BAG`, `T6_MAIN_SWORD@2`.
- Enchanted refined resources also carry `_LEVEL{n}`: `T5_PLANKS_LEVEL1@1`.
- Alchemy extracts and fish sauce have `LEVEL` in the name but no `@`: `T1_ALCHEMY_EXTRACT_LEVEL1`, `T1_FISHSAUCE_LEVEL1`.
- Rule: add `@n` to a recipe ingredient only when its `@enchantmentlevel` is above 0.

## ao-bin-dumps (recipes)

Maintained repo: `ao-data/ao-bin-dumps`. The `broderickhyman` copy is archived since 2023.

- Recipes and item values: `https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json` (about 17 MB)
- Names: `https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json` (about 24 MB). `UniqueName` already includes `@n`. English name is `LocalizedNames["EN-US"]`.

Structure (converted from XML, so every value is a string and attributes start with `@`):

```json
"craftingrequirements": {"@silver":"0","@craftingfocus":"858",
  "craftresource":[{"@uniquename":"T4_CLOTH","@count":"8"},
                   {"@uniquename":"T4_LEATHER","@count":"8"}]},
"enchantments":{"enchantment":[{"@enchantmentlevel":"1","craftingrequirements":{
  "craftresource":[{"@uniquename":"T4_CLOTH_LEVEL1","@enchantmentlevel":"1","@count":"8"}, ...]}}]}
```

- `craftingrequirements` is a list when an item has alternative recipes (for example artifact or Crystallized Divinity). `craftresource` can be one object or a list.
- Enchanted recipes sit inside the base item under `enchantments.enchantment[]`.
- `@amountcrafted` is output per craft: potions 5 or 10, most food 10, gear 1. Divide craft cost by it.
- `@maxreturnamount="0"` marks inputs that never come back from resource returns: artifacts, Crystallized Divinity, Avalonian Energy, crests, hearts, base capes in faction-cape recipes.
- A `currency` or `standing` block means the recipe also needs faction points or reputation, which have no silver price.
- `@itemvalue` exists on resources and many consumables (T4 = 16, each tier doubles, each enchantment doubles). Gear has none: add up its ingredients' values.

## Fallbacks

- Official item data (undocumented): `https://gameinfo-ams.albiononline.com/api/gameinfo/items/{ID}/data`. Has the recipe but lacks output count, return eligibility and item value.
- Item icons: `https://render.albiononline.com/v1/item/{ID}.png?quality=1`.
- AODP's browser view: `{host}/api/v2/stats/view/{IDs}` shows an HTML table. Useful for chat-only AIs that can open web pages.
