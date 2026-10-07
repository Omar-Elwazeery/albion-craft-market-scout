# Report template

Use this shape for the final answer. Fill every field. When a cost is unknown, write "unknown" and give the break-even cap instead of leaving it out. `evaluate` output already follows section 3, so you can reuse it.

## 1. Assumptions (always first)

One line: server, Premium or not (tax rate), no Focus, quality 1 output, how inputs are bought (instant at lowest ask, or buy orders), cities allowed, red zones allowed or not, station fee used, transport used, data pulled at (UTC).

## 2. Shortlist

| # | Item (tier.enchant) | Craft in | Sell in, how | Profit per craft | Margin | Sold per day (7 d) | Red zone? | Verdict |
|---|---|---|---|---:|---:|---:|---|---|

Under the table, say which rows are **high margin, low volume** and which are **lower margin, high volume**. Keep red-zone routes and safe routes apart.

## 3. One block per recommended item

### Item name, tier.enchant (`ITEM_ID`)

- **Verdict:** pilot / watch / avoid, with every reason.
- **Craft in:** city and return rate. **Sell in:** city, sell order or instant sell. Route risk.
- **Sale price used:** amount, and whether it is the lowest ask, the highest buy order, or the 7-day average sale price. City, quality 1, quote time (UTC), source URL. Never the highest ask.

| Input | Qty per craft | Unit price | Bought in | Quote time / age | Cost | Returned? |
|---|---:|---:|---|---|---:|---|

List artifacts, tokens, hearts, crests and Avalonian Energy as their own rows, marked "no". Journals go in a separate line, not in the cost, unless the user wants them counted.

| Per craft | Silver |
|---|---:|
| Raw input cost | |
| Returned value (rate x eligible cost) | - |
| Station fee (rate used) | |
| Recipe silver cost, if any | |
| Transport (or "unknown") | |
| **Total cost** | |
| Net sale proceeds (output count x price x (1 - tax - setup fee)) | |
| **Profit per craft / per item / margin** | |
| Break-even cap for unknown costs | |

- **Sales evidence:** units sold in the last 7 and 14 days in the sell city, days with data, the window's end date, and any gaps or lag.
- **Test batch:** number of crafts and silver needed.
- **Open risks:** stale quotes, thin inputs, red-zone route, unverified fees.

## 4. Next step

One action the user can take now, for example: "Check the T5 Cured Leather order depth in Martlock, then buy inputs for 3 crafts."
