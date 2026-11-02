# Improvement backlog

Rounds pick the top unticked item. Dashboards first. Tick an item when done and add what you learnt.

## Dashboards: more dynamic and stunning (StintRadio `/` and `/race`, DriverDirector panel)
- [x] **Native overlay window with a truly see-through background** (`--engine native`, or the control panel's tick box): built and tested off-screen.
- [x] **Try the native overlay over the real game, then decide the default.** Seen over RaceRoom (see-through, click-through, on top); the user chose to keep it, and `native` is now the default (`--engine edge` keeps the browser window). Still to do: check CPU use and drag in edit mode during a real race; the panel's tick box is not remembered between runs.
- [x] Animated track map (on `/race`; `/` not done): car position (and the cars around, by name) on a drawn track outline learnt from the lap's x/z, with the reference line and braking zones lit as you approach them.
- [ ] Live lap trace overlay: speed, brake and throttle against the reference over lap distance, with the delta as a filled area, scrolling as you drive.
- [ ] Smooth motion: ease numbers and bars between updates (requestAnimationFrame interpolation), count-up digits, no jumps at 1 Hz.
- [ ] Sector strip polish: purple / green / yellow sector colours (session best / personal best / slower) alongside up/down, with the sector time.
- [ ] Tyre and brake temperature heat tiles with a trend arrow and a one-line verdict.
- [ ] Fuel and tyre strategy graph: projected fuel and wear to the flag with the pit window shaded, updating each lap.
- [ ] Race timeline: position over laps as a bump chart, with pit stops and the engineer's calls as markers.
- [ ] Session recap screen at the chequered flag: best corners, biggest losses, tips and experiment results, kept on screen (last data stays).
- [ ] Themes (dark, high-contrast, daylight for a laptop in the sun), a layout that adapts to phone, tablet, laptop and 21:9; cards can be hidden.
- [ ] A big-text mode readable at a glance on a second screen.
- [ ] Track map: colour the line by where this lap gains or loses against the reference; the same map on the `/` page.

## Completeness
- [ ] First-run wizard: detect the game, check Enable Plugins, test the microphone and the voice, one button each.
- [ ] A visible status of every dependency (game data, ElevenLabs key, Claude CLI, ffmpeg) with the fix next to each red item.
- [ ] Per-track corner names and notes for the common RaceRoom and LMU tracks.
- [ ] More cars/classes data for the pit strategy (fuel tank, typical stop time) so fewer "not known" answers.
- [ ] Session history page: list recordings, replay a lap, compare two laps.
- [ ] Export a session summary (markdown / PDF) after a race.

## Reliability
- [ ] Settings validation with a clear on-page error when `settings.json` cannot be read (no silent defaults).
- [ ] Soak test: replay a long recording through the engineer and check memory and CPU stay flat.
- [ ] A packaged-build smoke test in `kdev round check` (build with PyInstaller, start the exe on a spare port).
- [ ] Test the Claude plugin's `sr start` against the packaged build.
- [ ] `kdev round check` run from inside a worktree finds no `.venv` (ROOT is the worktree) and falls back to a Python without the packages, so every check fails until a venv is made there. Decide: reuse the main checkout's venv, or have `round start` make one.
- [ ] Check the track map on a real LMU session: LMU's reader has no `TURN_SIGN`, so the map assumes x right, z up and could be drawn mirrored; add a `TURN_SIGN` once checked against a known corner, as RaceRoom's was.

## Learnt
- Track map (improve/20261101-115120-auto1): the game gives other cars' lap distance, not position, so the outline is resampled evenly by distance and every car is placed from distance alone. Other cars update at 1 Hz, so the page dead-reckons them between readings.
- LMU's `race_snapshot` had no driver `name`, though `race_view`, `_neighbours` and the battle code read it: by reading the code, `/api/race` would raise a KeyError once a snapshot exists. Fixed by adding the name; not run against the game. It also lacks `speed`, which `recorder.py` reads for its car rows: not looked into.
- `/race` grid: a hidden flags or sector row shifted the rows and gave the radio calls a full share of the height. The rows are now fixed.
- `dev-plugin/scripts/mock_race.py` drives the real `LiveService` over a made-up track and takes headless screenshots, for looking at the race page without the game. It only feeds `/race` and its two APIs.
- Native overlay (improve/20261101-153205-auto1): GDI+ through ctypes draws anti-aliased paths into a premultiplied DIB section, which `UpdateLayeredWindow` takes as it is, with no extra package. A 640x420 frame took about 3 ms here (made-up data, no window update), since what never moves is drawn once and copied. Text goes through paths, so it has an outline but no font fallback: draw symbols such as the chips' arrows as shapes. `GdipSaveImageToFile` failed with status 10 (cause not found), so PNGs are written by a small encoder instead. A fully clear pixel of a layered window lets the mouse through, so edit mode lays a faint sheet over the whole window.
- `dev-plugin/scripts/overlay_preview.py OUT_DIR` renders the native overlay over dark, bright and busy backdrops to PNG, with no window. Unlike the Edge page, the native window does not remember the track between runs (no localStorage): it fetches it again on start.

## Done
(rounds move items here with the branch name)
- Animated track map: improve/20261101-115120-auto1
- Native overlay engine (the default): improve/20261101-153205-auto1
