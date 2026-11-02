---
name: debug
description: Debug DriverDirector or StintRadio. Gathers the app's health, failing tests, log errors and the newest recording into one report, then finds the cause and fixes it with a test. Use when an app crashes, says nothing, shows wrong numbers, a test fails, or the user says something "is not working".
---

# Debugger

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/kdev.py"` (called `kdev` below).
It only reads (logs, recordings, test runs); it never touches a running app's settings.

1. **Collect.** `kdev debug` (add `--app stintradio` or `--app driverdirector`). It reports: venv, test result with the
   full output of the first failure, whether the app is running, recent log problems, git state, the newest StintRadio
   recording (`%APPDATA%\StintRadio\sessions`).
2. **Reproduce before fixing.** A failing test is the reproduction. For live behaviour (a call not spoken, a wrong
   number) read the recording with the StintRadio data tools (`query`, `lap_trace`, `corners`) and find the frames
   where it went wrong. Never guess a game's memory layout or REST request shape: use only verified ones (see the
   memory notes); a malformed LMU REST body has crashed the game.
3. **Find the cause, not the symptom.** Check the real app's `stintradio.log` / DriverDirector log, `sr state`, and
   settings that silently fell back to defaults (a "settings.json could not be read" line).
4. **Fix with a test** that fails first and passes after. Keep the change small and in the surrounding style.
5. **Verify.** `kdev doctor` for both apps. For a dashboard change also look at the page (mock data is fine) before
   claiming it works.
6. **Report** what was wrong, the evidence, the fix, and what was NOT verified (anything that needs the game running).

Do not restart the user's running StintRadio while they may be driving without asking, and never start the coach
(`sr start --wait`, `sr on`) casually: it makes voice clips and costs credits.
