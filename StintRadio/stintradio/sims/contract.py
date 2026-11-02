"""The data contract between a sim and KerbApex.

A sim adapter turns whatever its game offers (shared memory, UDP, a plug-in, a file) into the plain values described
here. Everything else (the engineer, the overlay, the dashboards, the recorder) reads only these values, so a new game
needs one adapter and nothing else. Units are fixed; an adapter converts. A value the sim cannot give is left at its
default (or None for a whole block), and the features that depend on it switch off by themselves (see FEATURES).

The contract is plain data on purpose: `Sample` is a dataclass, the rest are dicts with the keys listed below.
`check_*` validate real values against it; `python -m stintradio.sims check <id>` runs them on a live adapter.
"""
from __future__ import annotations

import math
from typing import Iterable

from ..reader import Sample          # the one per-tick record every adapter produces

CONTRACT_VERSION = 1

# ---- capabilities: what an adapter says it can provide ------------------------------------------------------------
SAMPLE = "sample"                    # the player's car, every tick (required)
OTHER_CARS = "other_cars"            # sample(vehicle_id) for other cars (pedals of the race leader: learns their lap)
SESSION_INFO = "session_info"        # session type/iteration/phase, pit lane
SESSION_BEST = "session_best"        # fastest lap anyone has set
RACE_SNAPSHOT = "race_snapshot"      # session clock, flags, weather, fuel, tyre wear, all cars
RACE_GAPS = "race_gaps"              # position and gaps ahead/behind
SETUP = "setup"                      # driver-adjustable settings (brake bias, ABS, TC, engine map)
CLASS_LEADER = "class_leader"        # the car leading the player's class (with its sample)
RECORDING = "recording"              # raw frames for the recorder (adapters that expose `last_frame`)

ALL_CAPABILITIES = (SAMPLE, OTHER_CARS, SESSION_INFO, SESSION_BEST, RACE_SNAPSHOT, RACE_GAPS, SETUP, CLASS_LEADER, RECORDING)

# What each capability switches on, so the panel and the docs can say what a sim gets (and what it is missing).
FEATURES = {
    SAMPLE: "Lap debriefs, braking countdowns, corner coaching, delta, track map, dashboards, overlay (the core)",
    SESSION_INFO: "Pre-session briefing, pit-lane awareness, new-session detection",
    SESSION_BEST: "Gap to the session's fastest lap",
    RACE_SNAPSHOT: "Race watcher: flags, weather, fuel and tyre projections, pit strategy, penalties",
    RACE_GAPS: "Position and gap calls, battle detection",
    SETUP: "Setup experiments (brake bias and more, before/after verdict)",
    OTHER_CARS: "Learning the race leader's lap as the reference",
    CLASS_LEADER: "Reference from the class leader",
    RECORDING: "Recorded data for later analysis",
}

# ---- the Sample, field by field (units are fixed) -----------------------------------------------------------------
SAMPLE_FIELDS = {
    "t": ("s", "session time, increasing"),
    "lap": ("1..", "lap number being driven"),
    "lap_start": ("s", "session time the current lap began"),
    "dist": ("m", "metres along the lap, 0 at the start line"),
    "speed": ("km/h", "speed"),
    "throttle": ("0..100 %", "the driver's foot, unfiltered"),
    "brake": ("0..100 %", "the driver's foot, unfiltered"),
    "steer": ("-1..1", "wheel, negative is left"),
    "gear": ("int", "-1 reverse, 0 neutral, 1.."),
    "rpm": ("rev/min", "engine speed"),
    "x": ("m", "world position"),
    "z": ("m", "world position; x and z are plan view, TURN_SIGN tells which way is left"),
    "delta_best": ("s", "the sim's own delta to the driver's best lap (0 if the sim has none)"),
    "invalid": ("bool", "the current lap is invalid (track limits)"),
    "track": ("text", "track and layout name, as shown to the driver"),
    "car": ("text", "car model, as shown to the driver"),
    "track_len": ("m", "lap length"),
    "last_lap": ("s", "last completed lap time, -1 if none"),
    "best_lap": ("s", "driver's best lap time, -1 if none"),
    "g_lat": ("m/s^2", "optional, 0 if unknown"),
    "g_lon": ("m/s^2", "optional, 0 if unknown"),
    "tyre_temp": ("deg C x4", "optional: FL, FR, RL, RR; () if unknown"),
    "fuel": ("litres", "optional, -1 if unknown"),
    "sectors": ("s x3", "optional: previous lap's cumulative sector times; () if unknown"),
    "ffb": ("0..1", "optional: force-feedback output as a fraction of full scale"),
}
SESSION_INFO_KEYS = {
    "type": "int: 0 practice, 1 qualifying, 2 race, 3 warm-up",
    "iteration": "int: which one of the weekend (changes when a new session of the same type starts)",
    "phase": "int: the sim's own phase number (only compared for change)",
    "laps": "int: laps completed",
    "time_left": "float s: time left in a timed session, -1 if none",
    "time_total": "float s: length of a timed session, -1 if none",
    "max_laps": "int: laps in a lap-limited session, -1 if none",
    "in_pits": "bool: the car is in the pit lane",
    "pit_state": "int: the sim's own pit state (only compared for change)",
}
CAR_KEYS = {
    "id": "int", "name": "str", "cls": "str: class name", "place": "int", "me": "bool: exactly one car",
    "dist": "float m along the lap", "laps": "int completed", "in_pits": "bool", "finish": "int: 0 racing",
}
SNAPSHOT_KEYS = {
    "session": "int: 10 and up is a race", "t": "float s: session clock", "end_t": "float s: when a timed session ends",
    "max_laps": "int", "phase": "int", "yellow_state": "int", "sector_flags": "3 ints", "raining": "float 0..1",
    "wetness": "float 0..1", "track_len": "float m", "me": "dict: the player's car (see CAR_KEYS)",
    "cars": "list of cars", "fuel": "float l", "fuel_cap": "float l", "wear": "4 floats, 1 new .. 0 gone",
    "impact_t": "float", "impact_mag": "float", "lap": "int", "tyre": "str: compound name",
}
SETUP_NOTE = "dict name -> number, e.g. {'brake_bias': 56.5, 'abs': 3, 'tc': 2, 'engine_map': 1}; -1 means the car has none"


# ---- validators: each returns a list of problems (empty = fine) --------------------------------------------------
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_sample(s) -> list[str]:
    if not isinstance(s, Sample):
        return [f"sample() must return a Sample (or None), not {type(s).__name__}"]
    bad = []
    for name in ("t", "lap_start", "dist", "speed", "throttle", "brake", "steer", "rpm", "x", "z", "delta_best", "track_len", "last_lap", "best_lap"):
        if not _num(getattr(s, name)):
            bad.append(f"{name} must be a finite number, got {getattr(s, name)!r}")
    for name in ("lap", "gear"):
        v = getattr(s, name)
        if not isinstance(v, int) or isinstance(v, bool):
            bad.append(f"{name} must be an int, got {v!r}")
    if bad:
        return bad
    if not s.track:
        bad.append("track is empty")
    if not s.car:
        bad.append("car is empty")
    if s.track_len <= 0:
        bad.append("track_len must be above 0 (metres)")
    if not 0 <= s.speed < 600:
        bad.append(f"speed {s.speed:.1f} is not km/h (expected 0..600)")
    for n in ("throttle", "brake"):
        if not -0.5 <= getattr(s, n) <= 100.5:
            bad.append(f"{n} {getattr(s, n):.2f} is outside 0..100 (percent, not a 0..1 fraction)")
    if not -1.05 <= s.steer <= 1.05:
        bad.append(f"steer {s.steer:.2f} is outside -1..1")
    if s.track_len > 0 and not -1 <= s.dist <= s.track_len * 1.02:
        bad.append(f"dist {s.dist:.0f} is outside the lap (0..{s.track_len:.0f} m)")
    if s.lap < 0:
        bad.append("lap must be 0 or more")
    if s.lap_start > s.t + 1e-6:
        bad.append("lap_start is later than t")
    if not -1 <= s.gear <= 12:
        bad.append(f"gear {s.gear} is outside -1..12")
    if s.tyre_temp and len(s.tyre_temp) != 4:
        bad.append("tyre_temp must be 4 values (FL, FR, RL, RR) or empty")
    if s.sectors and len(s.sectors) != 3:
        bad.append("sectors must be 3 cumulative times or empty")
    return bad


def check_session_info(d) -> list[str]:
    if d is None:
        return []
    if not isinstance(d, dict):
        return ["session_info() must return a dict or None"]
    bad = [f"missing key '{k}'" for k in SESSION_INFO_KEYS if k not in d]
    if not bad:
        if not isinstance(d["in_pits"], bool):
            bad.append("in_pits must be a bool")
        for k in ("type", "iteration", "phase", "laps", "max_laps", "pit_state"):
            if not isinstance(d[k], int) or isinstance(d[k], bool):
                bad.append(f"{k} must be an int")
        for k in ("time_left", "time_total"):
            if not _num(d[k]):
                bad.append(f"{k} must be a number")
    return bad


def check_snapshot(d) -> list[str]:
    if d is None:
        return []
    if not isinstance(d, dict):
        return ["race_snapshot() must return a dict or None"]
    bad = [f"missing key '{k}'" for k in SNAPSHOT_KEYS if k not in d]
    if bad:
        return bad
    cars = d["cars"]
    if not isinstance(cars, list) or not cars:
        return ["cars must be a non-empty list"]
    for c in [d["me"]] + cars:
        bad += [f"car missing key '{k}'" for k in CAR_KEYS if k not in c]
        if bad:
            return bad[:5]
    if sum(1 for c in cars if c["me"]) != 1:
        bad.append("exactly one car must have me=True")
    return bad


def check_gaps(g) -> list[str]:
    if g is None:
        return []
    if not (isinstance(g, tuple) and len(g) == 3):
        return ["race_gaps() must return (place, gap_ahead_s, gap_behind_s) or None"]
    return [] if isinstance(g[0], int) else ["place must be an int"]


def check_setup(d) -> list[str]:
    if not isinstance(d, dict):
        return ["setup() must return a dict (empty if none)"]
    return [f"setup value '{k}' must be a number" for k, v in d.items() if not _num(v)]


def lacking(capabilities: Iterable[str]) -> dict[str, str]:
    """capability -> what is switched off, for the capabilities an adapter does not have."""
    have = set(capabilities)
    return {c: FEATURES[c] for c in ALL_CAPABILITIES if c not in have and c != SAMPLE}
