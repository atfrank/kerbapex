---
name: raceroom
description: Make a video of a RaceRoom Racing Experience replay (cameras cycled with the replay's own keys, leaderboard and stats overlay from the game's telemetry, title card, fades) and, when the user asks, upload it to their YouTube channel as a private draft. Use when the user wants a RaceRoom replay turned into a video, or to know what RaceRoom replays they have.
argument-hint: "[replay name or 'my last race']"
---

# RaceRoom videos

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/dd.py" raceroom <command>` (called `dd raceroom` below). It runs the app's own command line and streams its output; no DriverDirector server is needed. OBS must be installed (the command starts it and builds the "RaceRoom" scene by itself).

## The user's PC must be free
`make` drives the RaceRoom window with real key presses and clicks for the whole replay (a 17 minute race takes about 22 minutes with the finishing). RaceRoom minimises as soon as another window takes focus and the run fails; keys and clicks that land in the user's other windows are also a risk. **Ask the user to leave the PC alone and wait for their yes before starting `make`**, and if they are playing a game (live), do not start. If a run fails with "could not bring RaceRoom to the front", someone used the PC: say so and wait for them to say it is free.

## 1. Find the replay
`dd raceroom list` shows the replay library (name, session, length, finishing place, car), newest first. "My last race" is the first `Race` row. Qualifying and practice replays work too. Use the full name from the list.

## 2. Make the video
```
dd raceroom make "<replay name>" --restart
```
- `--restart` quits and relaunches RaceRoom first. **Always use it**: exiting a replay and loading another re-opens the first one.
- The run is the **standard video**: the replay is played twice. Pass 1 is the main picture, cockpit views only (the driving group's Cockpit and Hood, alternating; never Chase), with the leaderboard overlay and the game sound. Pass 2 (RaceRoom is restarted in between) shows other views (external, trackside cycle; never the driving/chase cameras) without overlay, and is laid in a bordered box in the top right corner, cut to the same moment using the driver's progress in the telemetry. It stops when the replay wraps to its start, builds the finished video (title, fades) in OBS's output folder and **closes the game** when done (`--stay` keeps it open). A race takes about 45 minutes. `--no-inset` makes the old single-pass video (all camera groups in the main picture).
- Try a short range first when something changed: `--start 4 --minutes 1.5` (minutes into the replay). `--no-overlay` leaves the leaderboard off. The first minutes of a race are a static grid.
- Run it in the background and follow the output; it prints `video: <path>` at the end. It can only be checked by looking: take frames with ffmpeg (`-ss` before `-i` so it is fast) at several times and look at them. Check the replay moves (the picture is not the same grid at minute 5 and 10), the overlay shows names and gaps, and the title card is there.

## 3. Upload (only on the user's say)
Nothing is uploaded unless the user asks. They may ask for "post"; that means a **private** draft first: the user looks at it on YouTube and makes it public themselves.
```
dd raceroom upload "<video path>" "<replay name>"          # private by default
```
It drafts the title ("<driver> | <track> <session> | RaceRoom Racing Experience") and description from the replay, uploads (about 4.5 GB for a race, 20 minutes) and prints the link. `--privacy unlisted|public` only when the user said so for this video. YouTube must be connected (`dd youtube`; see the publish skill).

## Settings (config `raceroom`, `dd settings` does not cover it: edit the app's settings file)
`driver` (the video is about this driver; a race replay's header lists the whole grid), `inset` (on), `main_weights` and `inset_weights` (camera groups of the two passes), `inset_size`, `weights` (camera groups of a `--no-inset` video), `shot_s`, `hide_ui`, `title_s`, `fade_s`, `overlay`, `overlay_zoom`, `tags`.

## What is known
- Replay keys: Space play/pause, Insert driving cameras (from another group it steps Hood, Nose, Chase, Cockpit and wraps, so the cockpit is 4 presses after Home; Chase is only ever flashed past; inside the group it carries on from where it is, which `RaceRoomGame.driving_at` tracks), Home onboard (10 cameras), Page Down / End external (static, aerial, action, TV), Page Up chase, H hides the replay bar. The bar's Select Camera list names every camera and could be clicked.
- The game's telemetry (shared memory `$R3E`) updates while a replay plays and is what the overlay reads; lap and sector times match the session report exactly. Session reports exist only for qualifying runs.
- A replay never stops at its end: it loops. The run detects the wrap from the telemetry.
