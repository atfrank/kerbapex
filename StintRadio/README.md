# StintRadio

**A race engineer on your team radio, in your own voice.**

StintRadio reads your sim's live data while you drive and talks to you on the straights: how the lap went, the one
corner that cost you the most and why, when to brake, which gear, how far the car ahead is, and what to do about
fuel and tyres. Ask it a question by voice and it answers from the data of the session you are in.

Supported games: **RaceRoom Racing Experience** and **Le Mans Ultimate** (Windows).

## Highlights

| | |
|---|---|
| **Lap debrief** | Lap time, the gap to the reference, and the corner that cost the most, with the measured reason ("braking about fifteen metres early"). Quiet once you are settled; it speaks when something changed. |
| **Braking countdown** | "Brake in four, three, two, one, brake, second gear", timed from your speed to the reference lap's braking points. |
| **Corner praise** | Tells you when you nailed a corner, so you know what good feels like. |
| **Race craft** | Position and gaps, the drivers around you by name, and where time is lost to the car directly ahead. |
| **Pit strategy** | Ask "do I need to stop?" and get fuel and tyre projections to the flag, the mandatory window and a recommendation. |
| **Driving tips** | Brake bias direction from measured lock-ups, shift points from your car's acceleration in each gear, minimum speeds. |
| **Setup experiments** | Change brake bias, ABS, TC or engine map; the engineer compares your laps before and after and tells you to keep or revert. |
| **Live dashboards** | A full dashboard for a second screen or the Steam overlay, and a read-only race page for a laptop or phone. The last data stays on screen after the race. |
| **Your voice** | Speaks in your ElevenLabs voice clone, or an offline Windows voice. |
| **Recorded data** | Every value the game reports is saved locally (about 10 times a second) for later analysis. |

## Requirements

- Windows 10 or 11, 64-bit
- RaceRoom Racing Experience, or Le Mans Ultimate with **Enable Plugins** turned on in its settings
- Headphones or speakers; a microphone for spoken questions
- Optional: an [ElevenLabs](https://elevenlabs.io) account (for your cloned voice and natural sentences) and the
  Claude CLI (for free-form spoken questions). Without them StintRadio uses the offline Windows voice and its built-in answers.

## Install

Run `KerbApex-Setup-<version>.exe` and follow the steps, then open **KerbApex** from the Start menu and press **Start everything**.
The engineer waits for the game and begins as soon as its live data appears; the overlay and the dashboard at
<http://127.0.0.1:6420/> come with it.

See the **[User Guide](docs/USER_GUIDE.md)** for first-run setup, the settings, and a walk through a session.

## Documentation

- [User Guide](docs/USER_GUIDE.md): setup, using it in a session, voice, dashboards, questions
- [Settings reference](docs/SETTINGS.md): every option and its default
- [Troubleshooting](docs/TROUBLESHOOTING.md): no voice, no data, wrong voice, laptop cannot connect
- [Privacy](docs/PRIVACY.md): what stays on your PC and what is sent where
- [Sim adapters](docs/ADAPTERS.md): add another racing sim with one file and a data contract
- [Technical reference](docs/REFERENCE.md): data recorded, MCP tools, force feedback advice
- [Building and packaging](docs/BUILDING.md): for developers
- [Changelog](CHANGELOG.md) and [third-party notices](THIRD_PARTY_NOTICES.md)

## Licence and trademarks

Copyright (c) 2026 atfrank. Released under the [MIT licence](LICENSE.txt).

StintRadio is an independent product. It is not affiliated with or endorsed by Sector3 Studios, Studio 397, Motorsport Games,
Le Mans Ultimate, ElevenLabs, Anthropic or Coach Dave Academy. RaceRoom Racing Experience, Le Mans Ultimate, ElevenLabs,
Claude and Coach Dave Delta are trademarks of their respective owners.
