# StintRadio technical reference

A race engineer on the team radio while you drive Le Mans Ultimate. It reads the game's live data (read-only), compares every corner with a reference lap and talks to you on the straights.

## Install

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ..\DriverDirector -e .[dev]
```

StintRadio uses DriverDirector (next to it in `RaceRoomWork`) as a library for telemetry analysis, ffmpeg and ElevenLabs. Settings and data live in `%APPDATA%\StintRadio`; on the first run the live and voice settings are carried over from DriverDirector. The ElevenLabs key is read only from `ELEVENLABS_API_KEY`.

Claude plugin: `claude plugin marketplace add <this folder>` then `claude plugin install stintradio@stintradio-local`.

## RaceRoom

The same coach works with RaceRoom Racing Experience (`live.game` auto | raceroom | lmu; auto uses whichever game is running).
It reads RaceRoom's shared memory (`$R3E`, always on, no setting to change): pedals, gear, revs, lap distance, lap and sector
times, fuel, tyre wear, position and gaps. The references are your own laps (saved while you drive); there is no class-leader
lap, because other cars' pedals are not in the memory. It also watches your force feedback output (`live.ffb_advice`) and says if the
strength looks too high (clipping) or too low; it advises, and `sr ffb` shows the change it would make. `sr ffb apply --yes` writes it into the wheel profile
(`ControlSet\NAME.rcs`, `FFB Strength` only, at most 10 % a step, dated backup first) and only while RaceRoom is closed; `live.ffb_profile` names the profile when more than one has a wheel. RaceRoom has no rain or track-wetness data, so those calls stay off.

## Recorded data and Claude (RaceRoom)

While you drive, StintRadio writes every value RaceRoom reports (~10 a second, 377 fields), all cars once a second and each
lap to a SQLite file in `%APPDATA%\StintRadio\sessions\` (`live.record`, `live.record_hz`; kept 60 days / 5 GB). The plugin's
MCP server (`python -m stintradio.mcp_server`, started by `.mcp.json`) lets Claude read it, read-only: `live`, `query` (SQL
SELECT), `list_sessions`, `schema`, `corners`, `stored_laps`, `lap_trace`. Spoken questions use the same tools through the
Claude CLI (`live.ask_tools`).

## What it does


`Start StintRadio.bat` (or `sr on` from the plugin) reads Le Mans Ultimate's live data (needs **Enable Plugins** on in the game's settings) and serves a
page at `http://127.0.0.1:6420/`:
- **Delta:** the live gap to a reference lap.
- **Track ahead:** the next ~450 m with the reference's brake and throttle dashed, "brake in N m" before its braking
  point, and your own trace behind the car.
- **Pedals:** the last 10 seconds of brake and throttle.
- **Corner notes:** one after each corner, e.g. "Corner two: +0.35s - braked 20 m earlier, had a poor exit".

By default it talks like a **race engineer** on the team radio, in your voice and only on the straights: the lap time
and delta after each lap, the corner that cost the most and why, a reminder before that corner next lap, and in races
your position and gaps (`live.style` engineer | countdown). It also **talks**: "brake in four, three, two, one, brake, second gear" before each braking point of the reference
lap, timed in seconds from your speed, and "short shift" where the reference changes up early out of a corner. The
phrases are made once (your ElevenLabs voice with `live.voice_engine` elevenlabs, or the Windows voice) and boosted
to carry over the engine.
In a **race** the reference is the fastest lap recorded from the leader of your class (`live.reference`: leader | best).

Over an **exclusive-fullscreen** game, show it with the Steam overlay: Shift+Tab, open the web browser, go to the
address above and pin it (adjust its opacity). Otherwise any browser window, a second screen or a phone works.

- **References:** `sr import` takes your fastest whole laps from Coach Dave Delta's local session files
  (`%LOCALAPPDATA%\CoachDaveDelta\app-*\resources\*.delta`, decoded: lap position, time, speed, pedals; no positions
  on track). Laps you drive are stored in `%APPDATA%\StintRadio\laps\`, and the fastest per car and track is the
  reference.
- **Coach:** it reuses DriverDirector's race analysis (driverdirector/driving.py): each corner is judged from the reference's braking point to
  its exit, and only measured causes are named.
- **Header:** the live data layout is read from the game's own header
  (`Support/SharedMemoryInterface/InternalsPlugin.hpp`) at start. Its licence does not allow copying it, and it is
  checked against offsets verified earlier.
- **Without driving:** `sr replay "<track>" "<car>" 1 <track length m>` plays a stored lap through the coach.

