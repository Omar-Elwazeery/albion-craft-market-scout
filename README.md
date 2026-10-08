# Albion Online Craft Market Scout

Finds Albion Online crafts that make silver and checks them against live market data before recommending a small test batch.

**Try it in your browser:** https://omar-elwazeery.github.io/albion-craft-market-scout/

It comes in three forms that share the same math:

- **Web app:** results are ready when the page opens, from a market snapshot taken about every 30 minutes on all three servers. They update as you change settings. Check any one item against live prices. Nothing to install.
- **AI agent skill:** follows the open [Agent Skills](https://agentskills.io/specification) format, so the same folder works in Claude Code, Codex, Gemini CLI, Cursor, GitHub Copilot, OpenCode, Hermes Agent and other tools that read `SKILL.md`. Chat-only AIs can use it by pasting the instructions.
- **Command-line tool:** `albion_scout.py`, Python 3.8+ with no packages.

## How it compares

**Checking markets by hand** shows exact prices and how many units sit at each price, which no website can. But you see one market at a time, you see orders rather than completed sales, and fees are easy to forget. Use a scan to find candidates, then check the top few in game before you buy materials.

**Calculator sites** such as [Albion Free Market](https://albionfreemarket.com/crafting) and [AlbionOracle](https://albionoracle.com/en/craft) work out one item in detail, including Focus, hideouts and journals. They are the right tool once you know what to craft.

**Scanner sites** such as [Albion Profit Forge](https://albionprofitforge.com/) rank thousands of crafts by profit and cover more activities, such as flipping and salvaging.

**Data clients** such as the [Albion Data Client](https://github.com/ao-data/albiondata-client) and the [AFM Data Client](https://albionfreemarket.com/data-client) are not scanners. They run next to the game and upload the prices you see to the Albion Online Data Project, the source this tool reads. Every player who runs one makes this tool's numbers fresher. The AFM client can also send private data, such as your character's specs, to your Albion Free Market account; this tool cannot see that.

This tool is a scanner built on one rule: a craft only pays if people are buying it.

- **Real, recent sales decide the verdict.** An item is marked `pilot` only when the selling city averaged at least 5 sales a day over the last 7 days of that city's data, with data on at least 4 of those days, and the margin is at least 10%. An item whose sales data stopped 3 or more days before the rest of its city is marked `watch`. An item nobody bought in the last 7 days is marked `avoid`, however big its margin looks.
- **Old and odd prices are not trusted.** The sale price is the lower of the current price and the 7-day average sale price. A sale price or main input price older than 12 hours means `watch`, and anything older than 24 hours is ignored. An input listed far below what it usually trades at, often a single cheap order, is costed at its 7-day average.
- **Every cost is counted:** the resource return rate for the craft city, station fee, sales tax, the 2.5% setup fee and transport.
- **It gives you prices to check in game.** For every input, the most you can pay and still make 10%; for the output, the lowest price you can sell at; and a short checklist to confirm in the market before you buy. No website can see how many units sit at each price, so this last check is yours.
- **It sizes a test batch** at about 5% of one day's sales, capped by your budget.
- **It shows the safe route.** When the best route runs through Caerleon or its Black Market, it also shows the profit without them.
- **It checks itself.** Hand-checked tests run on every change, and every build compares the fee and bonus constants with the latest game data and stops publishing if a patch changed them. Once pilots are a week old, the page shows how many sold at or above break-even in the week after.
- **It is free and open.** No account, no paid tier, and every formula is in this repo for anyone to check.
- **It also works as an AI skill**, so you can ask in plain words, for example to check a crafting strategy from a video.

What it leaves out: Focus, crafting specialization, journals and higher-quality rolls are not counted, so its numbers are on the safe side and experienced crafters will usually earn more. It covers crafting and refining, not flipping or salvaging. Like every Albion price site, it is only as fresh as what players upload to the Albion Online Data Project. No tool can promise a profit: prices move, and other crafters see the same chances.

## What is in the repo

```
albion-craft-market-scout/       the skill: copy this folder to install it
  SKILL.md                       instructions the AI reads
  scripts/albion_scout.py        helper: scan, evaluate, prices, history
  references/economics.md        fees, return rates, city bonuses, transport, with sources
  references/data-sources.md     price API and recipe data details
  references/report-template.md  shape of the final answer
web/                             the web app (plain HTML, CSS and JavaScript)
  core.js                        the profit math, ported line by line from albion_scout.py
tests/golden/                    hand-checked cases both versions must match, plus parsing tests
tests/parity/                    checks that core.js and albion_scout.py agree on real market data
.github/workflows/pages.yml      market snapshot and publish to GitHub Pages every 30 minutes
.github/workflows/tests.yml      tests on every push and pull request
```

## Install

Copy the `albion-craft-market-scout` folder (not just `SKILL.md`) to the place your tool reads skills from. The folder name must stay `albion-craft-market-scout`.

| Tool | Folder for all projects | Folder for one project |
|---|---|---|
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| Codex, Gemini CLI, Cursor, GitHub Copilot, OpenCode | `~/.agents/skills/` | `.agents/skills/` |
| Hermes Agent | `~/.hermes/skills/` (any category subfolder) | `.hermes/skills/` |

Claude Code does not read `.agents/skills/`. Hermes can, if you add `~/.agents/skills` to `skills.external_dirs` in its config.

PowerShell example for Claude Code:

```powershell
Copy-Item -Recurse .\albion-craft-market-scout "$HOME\.claude\skills\"
```

Bash example for the shared folder:

```bash
mkdir -p ~/.agents/skills && cp -r albion-craft-market-scout ~/.agents/skills/
```

**claude.ai:** zip the folder and upload it under Settings, Capabilities, Skills. Code execution must be on. If the sandbox cannot reach the internet, the skill falls back to its manual path.

**Chat-only AIs (ChatGPT, Gemini web, others without skills):** paste the contents of `SKILL.md` as custom instructions or at the start of a chat, and attach the three `references/` files. The AI will use the manual path and give you URLs to open if it cannot browse.

## Try it

Ask your AI: "What can I craft for profit in Albion EU right now? No Premium, about 200k silver."

Or run the helper yourself:

```bash
cd albion-craft-market-scout
python scripts/albion_scout.py scan --top 15                 # all categories, T4-T8
python scripts/albion_scout.py scan --avoid-red-zones --max-tier 6
python scripts/albion_scout.py evaluate T4_BAG --transport 300 --budget 200000
python scripts/albion_scout.py search "druid robe"
```

The first run downloads recipe data (about 40 MB) and caches it for 7 days in `~/.cache/albion-craft-market-scout` (override with `--cache-dir` or `ALBION_SCOUT_CACHE`).

## Work on the web app

The page has no build step. It needs a recipe index and a market snapshot, both written by the Python script:

```bash
python albion-craft-market-scout/scripts/albion_scout.py export-index --out web/data/index.json
python albion-craft-market-scout/scripts/albion_scout.py snapshot --server europe   # writes web/data/live-europe.json
python -m http.server 8765 --directory web      # then open http://127.0.0.1:8765
```

If you change the math in `albion_scout.py`, make the same change in `web/core.js`, then run the tests (needs Node 18+):

```bash
python tests/golden/run.py    # hand-checked cases, no network
python tests/parity/run.py    # both versions on a saved market sample
```

The golden tests hold small markets with the expected numbers worked out by hand in `tests/golden/cases.json`. The parity test replays a saved market sample (`tests/parity/fixture.json.gz`) through both versions and fails on any difference; `tests/parity/make_fixture.py` captures a fresh sample. Both run on every push, and again before every publish.

## How the live site stays current

Every 30 minutes, `.github/workflows/pages.yml` rebuilds the recipe index, runs `check-game-data` and the tests, takes a market snapshot on all three servers and publishes the page. If any check fails, nothing is published and the last good page stays up. A server whose snapshot fails keeps its last one. The first run of each UTC day also saves that day's pilots to the `scan-log` branch, and `backtest` scores the pilots that are 8 to 30 days old for the track record on the page.

GitHub pauses scheduled workflows in a public repository after 60 days without activity, and emails the owner first. If the snapshot's age on the page keeps growing, re-enable the workflow under Actions.

## When the game changes

Fees, return rates and city bonuses are listed with sources and a check date in `references/economics.md`. `python albion-craft-market-scout/scripts/albion_scout.py check-game-data` compares them with the latest game data, and the live site stops publishing when a patch changes one. Update the constants at the top of `scripts/albion_scout.py` and `GAME_DATA_CHECKED`; until then, pass new values as flags: `--rrr`, `--daily-bonus`, `--fee-per-100`, `--premium`.

## Data sources

- Prices and sales history: [Albion Online Data Project](https://www.albion-online-data.com/), community-uploaded and partial.
- Recipes, item values and names: [ao-data/ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps).

Results are estimates from community data, not guarantees. Always test with a small batch.

Prices are only as fresh as what players upload. If you play, run the [Albion Data Client](https://github.com/ao-data/albiondata-client) or the [AFM Data Client](https://albionfreemarket.com/data-client) while you visit markets, but not both at once: either one sends the prices your game shows to the Data Project, which makes every scan more accurate for everyone.

## License

[MIT](LICENSE)
