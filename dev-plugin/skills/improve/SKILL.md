---
name: improve
description: Run rounds of sandboxed self-improvement on DriverDirector and StintRadio - each round works on its own git branch in a worktree, picks the top item from the backlog (dashboards first), implements it with tests, checks it, and leaves a report for the user to approve. Use when the user wants the apps to improve themselves, more complete, or the dashboards more dynamic and stunning.
---

# Self-improvement rounds

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/kdev.py"` (called `kdev`).

## The sandbox
Every round is a git worktree under `%LOCALAPPDATA%\KerbApex\rounds\<id>` (outside OneDrive, which locks files) on branch `improve/<id>`. The checks run the sandbox's own code
with a temporary settings folder, spare ports (6498 StintRadio, 6497 DriverDirector) and no API keys in the
environment. The user's running apps, settings, voice cache and recordings are never touched. **Nothing is pushed or
merged by a round: the branch is for the user to review.**

## One round (you, interactive)
1. `kdev round start <short-name>` prints the round id; work only in that folder.
2. Read `dev-plugin/backlog.md`. Pick ONE item (dashboard first unless the user said otherwise). Say which and why.
3. Implement it in small steps, each with a test where logic is involved. Match the surrounding code style.
4. `kdev round check <id>` until it passes (tests for both apps and the dashboard smoke test of the sandbox copy).
   For a visual change also serve the page with mock data and look at it (resize to a laptop 1366x768 and a phone).
5. Update the backlog: tick the item, add what you learnt and any new ideas. Commit on the branch.
6. `kdev round finish <id>` writes `ROUND_REPORT.md` in the round folder. Show the user the diff stat and the report, and
   what you could not verify (anything needing the game running). Merge only if the user says so.

## Many rounds (automatic)
`kdev loop --rounds N` shows the plan; `kdev loop --rounds N --go` runs N headless rounds with the Claude CLI, one
sandbox each, then writes a report per round. Only run `--go` when the user has asked for it. After it, list the
branches and reports; do not merge anything unasked.

## Rules for any round
- Never run a real-race feature "to see if it works" on the live ports; never start the coach or make voice clips.
- No guessing of game memory offsets or REST bodies; only verified shapes.
- Keep honesty: say what was measured, what was only checked with mock data, and what is untested in a real race.
- Dashboards must keep the last data on screen when the feed stops (no "connection lost" flicker).
