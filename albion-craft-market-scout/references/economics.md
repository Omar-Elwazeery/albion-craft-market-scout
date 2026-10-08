# Economics: fees, return rates, city bonuses, transport

Read this when you compute profit by hand, explain a number, or a patch may have changed one.
Checked 2026-10-07 against the official wiki, forum posts and the game data in `ao-data/ao-bin-dumps` (dump of 2026-09-23). The Dragonfire update (2026-08-31) changed none of these values. If a newer patch did, pass the new value to the script with a flag instead of editing it.

`python scripts/albion_scout.py check-game-data` compares the base bonus, every city specialization, the refining bonuses and the station fee cap with the latest `craftingmodifiers.json` and `gamedata.json`. It also checks that a few known recipes and item values still parse the same. The web app's build runs it every time and stops publishing on any difference. Sales tax and the setup fee are not in that game data, so check them by hand.

## Contents
- Market fees
- Resource return rate (no Focus)
- City specializations
- Station fee
- Transport
- Not counted by default: journals, quality, Focus

## Market fees

| Action | Cost |
|---|---|
| Item sells from your sell order | 8% sales tax (4% with Premium) + the 2.5% setup fee you paid when posting |
| You sell instantly into someone's buy order | 8% sales tax (4% with Premium). No setup fee |
| You buy instantly from a sell order | Nothing extra |
| You post a buy order | 2.5% setup fee, not refunded if cancelled |
| Black Market (Caerleon) | Same 8%/4% tax. Instant sell has no setup fee. Medium confidence: community guide, consistent with game data |

Sources: https://wiki.albiononline.com/wiki/Marketplace, game data `gamedata.xml` (`transactiontax=0.08`), https://albionmarket.gg/guides/black-market-flipping

## Resource return rate (no Focus)

`return rate = 1 - 1 / (1 + total production bonus)`

| Where you craft | Bonus | Return rate |
|---|---:|---:|
| Royal city, Caerleon or Brecilien, item not specialized there | 18% | 15.25% |
| Same, in the city that specializes in the item (crafting) | 18% + 15% | 24.8% |
| Same, refining in the resource's city | 18% + 40% | 36.7% |
| Daily bonus +10% (no specialization / with specialization) | 28% / 43% | 21.9% / 30.1% |
| Daily bonus +20% (no specialization / with specialization) | 38% / 53% | 27.5% / 34.6% |
| Player island | 0% | 0% |

- **Daily bonus:** every day, two crafting or refining categories get +10% or +20%. Check the in-game Activities menu. Pass it with `--daily-bonus 0.10`. It resets at maintenance.
- **Hideouts:** combine a Power Level bonus (0-26%), a zone-quality specialization (1-26%) and power cores (up to +30%). Pass the result with `--rrr`.
- **Focus** adds 59% to the bonus. Not used unless the user has Focus.
- **Only eligible inputs come back.** Inputs marked `@maxreturnamount="0"` never return: artifacts, Crystallized Divinity, Avalonian Energy, faction hearts, Royal sigils, Mists tokens, rare alchemy drops, cape crests, and base items that get upgraded (the T4 cape in a faction cape, SET1-3 armor in Royal armor). Refined materials and arcane extracts do return.

Sources: https://wiki.albiononline.com/wiki/Resource_return_rate (edited 2026-06-01), game data `craftingmodifiers.xml`

## City specializations (+15% crafting)

| City | Crafting | Refining (+40%) |
|---|---|---|
| Thetford | Mace, Nature Staff, Fire Staff, Leather Armor, Cloth Helmet | Ore (metal bars) |
| Lymhurst | Sword, Bow, Arcane Staff, Leather Helmet, Leather Shoes | Fiber (cloth) |
| Bridgewatch | Crossbow, Dagger, Cursed Staff, Plate Armor, Cloth Shoes | Stone |
| Martlock | Axe, Quarterstaff, Frost Staff, Plate Shoes, Off-hand | Hide (leather) |
| Fort Sterling | Hammer, Spear, Holy Staff, Plate Helmet, Cloth Armor | Wood (planks) |
| Caerleon | Gatherer gear, Tools, Food, War Gloves, Shapeshifter Staff | none |
| Brecilien | Cape, Bag, Potion | none |

A wiki edit dated 2026-10-06 also lists Off-hand for Brecilien. The game data does not, so this table keeps Off-hand in Martlock.

Sources: game data `craftingmodifiers.xml`, https://wiki.albiononline.com/wiki/Local_Production_Bonus

## Station fee

```
nutrition used = item value x 0.1125
fee            = nutrition used x (station's silver per 100 nutrition) / 100
```

- Station owners set the rate. The maximum is 1,000 silver per 100 nutrition. Typical rates are not published, so the script assumes the maximum unless told otherwise (`--fee-per-100` or `--station-fee`). The in-game crafting window shows the real fee.
- Item value of a refined material is 2^(tier + enchantment): T4 = 16, T4.1 = 32, T5 = 32, T8.4 = 4,096.
- Gear has no listed value. It is the sum of its ingredients' values, artifacts included. Example: a T4 sword (16 bars + 8 leather) = 24 x 16 = 384, so it uses 43.2 nutrition. Medium confidence: the official post only says the value is "based on raw ingredients".
- Crafting at T2 and below is free.

Sources: https://wiki.albiononline.com/wiki/Building, https://forum.albiononline.com/index.php/Thread/157229-Usage-Fee-and-Crafting-Changes-Lands-Awakened-Update/, https://forum.albiononline.com/index.php/Thread/194320-Upcoming-Changes-to-Crafting-Fee-Limit/

## Transport

| Route | Risk |
|---|---|
| Royal city to Royal city | Blue and yellow zones. No full loot. A yellow-zone death costs durability, not items. |
| To or from Caerleon, including the Black Market | Through at least one red zone: full-loot PvP. |
| Brecilien | Mists portal (needs 50,000 Brecilien standing) or Roads of Avalon (black zones), or the Travel Planner. |

- **Travel Planner** can carry items between Royal cities, islands and Brecilien for a fee: roughly `150 x weight x travel-cost modifier x distance`. Brecilien counts as 2 city distances. The modifier is `@fasttravelfactor` in the item data (T4 refined resources = 2). Gold-market and discount effects change the result, so read the exact quote in game.
- You can never carry tradable items into or out of Caerleon, hideouts or Outland towns by fast travel.
- Pass a known cost with `--transport SILVER_PER_CRAFT`. If it is unknown, report the break-even cap.

Sources: https://wiki.albiononline.com/wiki/Travel_Planner, https://wiki.albiononline.com/wiki/Caerleon, https://wiki.albiononline.com/wiki/Types_of_Regions

## Not counted by default

Show these as a separate upside only when the user confirms them.

- **Laborer journals** fill with base crafting fame (Premium bonus does not count). Fame to fill: T4 3,600; T5 7,200; T6 14,400; T7 28,380; T8 58,590. An empty journal costs 500 x 2^(tier - 2) silver (T4 = 2,000). Full journals sell on the market. Source: https://wiki.albiononline.com/wiki/Journal
- **Quality rolls** without Focus or Destiny Board bonuses: Normal 68.9%, Good 25%, Outstanding 5%, Excellent 1%, Masterpiece 0.1%. Food, potions and most tools have no quality. Source: https://wiki.albiononline.com/wiki/Quality
- **Premium** halves the sales tax to 4% (`--premium`).
