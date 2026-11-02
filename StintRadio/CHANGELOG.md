# Changelog

## Unreleased

- **Videos with the KerbApex overlay and the engineer**: Studio replays the recording KerbApex made while you drove a race, in step with the game's replay (`sessionreplay.py`), as a second StintRadio (port 6421) that saves nothing. Its overlay is an OBS source in the video and its engineer is heard on it. The main picture is always the cockpit view from inside the car. `python -m stintradio studio list | make | last`.

- **Studio** tab in the control panel: pick a RaceRoom replay and make the video (DriverDirector), stop it, and upload it as a private YouTube draft. Runs from the source download.
- DriverDirector's standard RaceRoom video is two passes (cockpit main picture, other views in a box). Test data and examples use neutral names; the driver is a setting.

- **Sim adapters**: the boundary between a sim and KerbApex is now an explicit, versioned data contract (`stintradio/sims`). RaceRoom and
  Le Mans Ultimate are adapters; a new sim is one adapter file dropped in `%APPDATA%\StintRadio\adapters` (or a pip package with a
  `stintradio.sims` entry point). Features switch on by what an adapter provides. `python -m stintradio.sims list | check | new | contract`,
  a no-game `demo` sim, and a "Supported sims" button in the panel. See `docs/ADAPTERS.md`.

- Overlay: a native window with a truly see-through background (`--engine native`, or the tick box in the control panel), beside the Edge window, which stays the default.

- Control panel: Overlay tab (shape, size, corner, theme, border, opacity, window) and Tools tab (suggestions, force-feedback advice,
  lap import, setup check, sound check, improvement rounds). The overlay windows follow a layout and opacity set from the panel.

- The engineer knows the pit lane and new sessions: no corner coaching or debrief for laps through the pits, a fresh start for every new
  session, and a short briefing before each session made from your earlier runs (the lap to beat and the corners to work on), said as
  advice for laps you have not driven yet. `live.briefing` turns it off.

## 1.0.0-rc.2 (2026-11-02)

- **KerbApex** is the name of the whole: the control panel is `KerbApex.exe`, the engine and overlay `StintRadio.exe`; the installer is `KerbApex-Setup`.
- Native see-through overlay (the default; the browser overlay stays as `--engine edge`): track map with the cars around you, delta, sector
  chips, the next turn (number, distance, what it is like, gear) and the three corners to work on, in any shape, size and corner.
- The engineer knows the pit lane and new sessions, and briefs you before each one from your earlier runs.
- Control panel: one StintRadio + engineer, Overlay, Settings, Tools and Logs tabs; Clear stray overlays and Reset everything; a check
  of your whole setup; only one copy of the engine, overlay and panel can run, and nothing the panel starts opens a terminal window.
- The KerbApex mark and the border are separate options (mark on, border off by default).

## 1.0.0-rc.1 (2026-11-01)

First release candidate for distribution.

- RaceRoom Racing Experience and Le Mans Ultimate support.
- Engineer radio: lap debrief, one focus corner, habit coaching, braking countdowns, shift cues, in your own ElevenLabs voice.
- Quiet after the first laps (`live.coach_laps`), and "nailed it" calls when a corner matches the reference.
- Races: the reference is the fastest lap by anyone, updated live.
- Race craft: driver names, battle calls, where time is lost to the car ahead (segment timing).
- Pit strategy answers: stops, mandatory window, fuel and tyres to the flag.
- Driving tips: brake bias direction from lock-ups, shift points per gear, minimum speeds.
- Setup experiments for brake bias, ABS, TC, engine map and engine braking, with before/after comparison.
- New dashboards: full dashboard and a read-only race page for a laptop; both keep the last data after the race.
- Never falls back to a computer voice when your voice clone was chosen.
- Settings file problems are written to the log.
