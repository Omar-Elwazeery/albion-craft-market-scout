# Albion Online Craft Market Scout

An AI agent skill that finds Albion Online crafts that make silver and checks them against live market data before recommending a small test batch.

It follows the open [Agent Skills](https://agentskills.io/specification) format, so the same folder works in Claude Code, Codex, Gemini CLI, Cursor, GitHub Copilot, OpenCode, Hermes Agent and other tools that read `SKILL.md`. Chat-only AIs can use it by pasting the instructions.

## What is in the folder

```
albion-craft-market-scout/
  SKILL.md                     instructions the AI reads
  scripts/albion_scout.py      optional helper: scan, evaluate, prices, history (Python 3.8+, no packages)
  references/economics.md      fees, return rates, city bonuses, transport, with sources
  references/data-sources.md   price API and recipe data details
  references/report-template.md  shape of the final answer
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

## When the game changes

Fees, return rates and city bonuses are listed with sources and a check date in `references/economics.md`. After a patch, verify them there. Until the constants at the top of `scripts/albion_scout.py` are updated, pass new values as flags: `--rrr`, `--daily-bonus`, `--fee-per-100`, `--premium`.

## Data sources

- Prices and sales history: [Albion Online Data Project](https://www.albion-online-data.com/), community-uploaded and partial.
- Recipes, item values and names: [ao-data/ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps).

Results are estimates from community data, not guarantees. Always test with a small batch.

## License

MIT
