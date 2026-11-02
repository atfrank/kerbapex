# Sim adapters: adding a racing sim to KerbApex

KerbApex never talks to a game directly. Between the game and everything else sits one small piece, an **adapter**, that
turns the game's own data (shared memory, UDP, a plug-in, a file) into a fixed set of plain values: the **sim contract**.
The engineer, the overlay, the dashboards, the track map and the recorder only ever read the contract. So a new sim is
one adapter file, and nothing else changes.

```
 game ──(its own format)──▶ adapter ──(the contract)──▶ engineer · overlay · dashboards · map · recorder
```

Built in: **RaceRoom Racing Experience** (`raceroom`), **Le Mans Ultimate** (`lmu`) and a no-game **demo** (`demo`).

## The five-minute version

```
python -m stintradio.sims new acc          # writes %APPDATA%\StintRadio\adapters\acc.py from the template
# edit it: fill in available(), open() and sample()
python -m stintradio.sims check acc        # reads your adapter for 3 seconds and tells you what is wrong
```

Then set **Settings > game** to `acc` (or leave `auto`: any adapter that says its data is there is picked up, built-ins first).
`python -m stintradio.sims list` shows every adapter, which one is running, and what each provides; the panel's
**Tools > Supported sims** shows the same.

A broken adapter never stops the others: it is listed under "problem" with the error.

## What an adapter is

A subclass of `SimAdapter` (`stintradio/sims/base.py`). Only `sample()` is required.

| Member | Purpose |
|---|---|
| `id`, `name` | the value of `live.game` that selects it; the name shown to the driver |
| `capabilities` | which optional parts you provide (below) |
| `TURN_SIGN` | `+1` / `-1`: which way a left turn turns the world x/z (0 = worked out from lap one) |
| `auto_detect` | may `auto` pick it? (`demo` says no) |
| `available(cfg)` | classmethod: is the sim's data there right now? |
| `open(cfg, **opts)` | classmethod: connect; raise `OSError("clear message")` if you cannot |
| `sample(vehicle_id=None)` | the player's car now, or `None` if not on track |
| `player_name()` | what the sim calls the driver |
| `session_info()`, `race_snapshot()`, `race_gaps()`, `session_best()`, `class_leader()`, `setup()` | the optional parts |

## Capabilities: what each one switches on

An adapter lists what it provides. What it leaves out, the engine leaves off: nothing breaks, the feature just is not there.

| Capability | Gives |
|---|---|
| `sample` (required) | lap debriefs, braking countdowns, corner coaching, delta, track map, dashboards, overlay |
| `session_info` | pre-session briefing, pit-lane awareness, new-session detection |
| `session_best` | gap to the session's fastest lap |
| `race_snapshot` | race watcher: flags, weather, fuel and tyre projections, pit strategy, penalties |
| `race_gaps` | position and gap calls, battle detection |
| `setup` | setup experiments (brake bias, ABS, TC, engine map) with a before/after verdict |
| `other_cars` | `sample(vehicle_id)` for other cars: learning the race leader's lap |
| `class_leader` | reference from the class leader |
| `recording` | raw frames for the recorder (RaceRoom only for now) |

A sim that only provides `sample` already gets the full single-driver experience: the engineer, the coaching, the overlay.

## The contract

Version **1** (`stintradio.sims.CONTRACT_VERSION`). Units are fixed; the adapter converts. Run `python -m stintradio.sims contract`
for the same tables.

### `sample()` returns a `Sample`

| Field | Unit | Meaning |
|---|---|---|
| `t` | s | session time, increasing |
| `lap` | 1.. | lap being driven |
| `lap_start` | s | session time the lap began |
| `dist` | m | metres along the lap, 0 at the start line |
| `speed` | km/h | |
| `throttle`, `brake` | 0..100 % | the driver's foot, unfiltered |
| `steer` | -1..1 | negative is left |
| `gear`, `rpm` | int, rev/min | -1 reverse, 0 neutral |
| `x`, `z` | m | world position (plan view) |
| `delta_best` | s | the sim's own delta to the driver's best lap; 0 if none |
| `invalid` | bool | lap invalid (track limits) |
| `track`, `car` | text | as shown to the driver |
| `track_len` | m | |
| `last_lap`, `best_lap` | s | -1 if none |
| optional: `g_lat`, `g_lon` (m/s²), `tyre_temp` (4 values, °C), `fuel` (l), `sectors` (3 cumulative s), `ffb` (0..1) | | leave at the defaults if the sim has no such value |

Return `None` when the car is not on track (menus, replays, garage).

### `session_info()` returns a dict (or `None`)

`type` (0 practice, 1 qualifying, 2 race, 3 warm-up), `iteration` (changes for a new session of the same type), `phase`, `laps`,
`time_left`, `time_total`, `max_laps` (-1 when not applicable), `in_pits` (bool), `pit_state`. All keys, always.

### `race_snapshot()` returns a dict (or `None`)

Keys: `session` (10 and up is a race), `t`, `end_t`, `max_laps`, `phase`, `yellow_state`, `sector_flags`, `raining`, `wetness`, `track_len`,
`me`, `cars`, `fuel`, `fuel_cap`, `wear` (4 values, 1 new to 0 gone), `impact_t`, `impact_mag`, `lap`, `tyre`. Each car in `cars` (and `me`) has
`id`, `name`, `cls`, `place`, `me` (exactly one), `dist`, `laps`, `in_pits`, `finish`, and where the sim has them `penalties`, `flag`, `pit_state`.

### The rest

- `race_gaps()` returns `(place, seconds_to_car_ahead, seconds_to_car_behind)`; a gap you do not know is `None`.
- `setup()` returns `{name: number}`, e.g. `{"brake_bias": 56.5, "abs": 3, "tc": 2, "engine_map": 1}`; `-1` means the car has none.

## Checking an adapter

`python -m stintradio.sims check <id or file.py> [seconds]` opens the adapter, reads it for a few seconds (**drive, or be on track**,
or `sample()` returns nothing) and reports:

- **ERROR** the contract is broken (a value in the wrong unit, a missing key, time going backwards). The engine would misbehave.
- **WARNING** it will work, but badly (no world positions for the map, no delta, nothing returned from a declared part).
- the list of features that stay off, from what is not provided.

The built-in adapters and the demo pass; the checks are the same ones the tests run.

## Packaging an adapter for others

Single file: send the `.py` file; it goes in `%APPDATA%\StintRadio\adapters`.

Package: declare an entry point and `pip install` it into the same Python as KerbApex:

```toml
[project.entry-points."stintradio.sims"]
acc = "my_acc_adapter:AccAdapter"
```

## Rules for adapters

- **Read only.** An adapter must never write to the game.
- **Be quick.** `sample()` is called about 10 times a second; read once, convert, return. Do the heavy work once, in `open()`.
- **Do not guess.** If the sim does not give a value, leave the default rather than inventing one.
- **Names as the driver sees them**, not internal ids.
- Mention in your adapter file which sim and which version of its interface it was written for.
