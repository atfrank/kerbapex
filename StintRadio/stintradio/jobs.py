"""Jobs the driver gives the engineer by voice: "check my tyres in three laps", "tell me when the gap behind drops under a
second", "shout if rain is coming". Claude turns the sentence into one of the fixed shapes below; anything else is
refused, so a misheard request can only ever produce one of these reports. The reports are written from the live data,
not by Claude."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from driverdirector.phrasing import w as words

from .engineer import Msg, say_gap
from .racewatch import say_litres

TOPICS = ("tyres", "fuel", "plan", "position", "weather", "time", "penalties")
CONDITIONS = ("rain", "yellow", "fuel_laps_below", "tyres_worn_above", "gap_behind_below", "gap_ahead_below")
LIMITS = {"fuel_laps_below": (1, 30), "tyres_worn_above": (20, 95), "gap_behind_below": (0.2, 10),
          "gap_ahead_below": (0.2, 10)}
MAX_JOBS = 8


@dataclass
class Job:
    kind: str                    # laps | minutes | when
    topic: str = ""              # for laps / minutes
    at: float = 0.0              # lap count or session time (seconds) when it is due
    cond: str = ""               # for when
    value: float = 0.0
    armed: bool = True           # a condition fires when it becomes true, and again only after it was false

    def describe(self) -> str:
        if self.kind == "when":
            return f"{self.cond.replace('_', ' ')} {self.value:g}".strip()
        return f"{self.topic} at {self.at:g}"


def parse(spec: dict, snap: dict) -> Optional[Job]:
    """A Job from Claude's description, or None if it is not one of the allowed shapes. `snap` fixes 'in three laps' to a
    lap count and 'in ten minutes' to a session time."""
    if not isinstance(spec, dict) or not snap:
        return None
    try:
        when, laps = spec.get("when"), snap["me"]["laps"]
        if when in ("laps", "minutes") and spec.get("topic") in TOPICS:
            n = float(spec["n"])
            if not 1 <= n <= (60 if when == "laps" else 240):
                return None
            return Job(when, spec["topic"], at=(laps + math.floor(n)) if when == "laps" else snap["t"] + 60 * n)
        if when == "when" and spec.get("cond") in CONDITIONS:
            cond, value = spec["cond"], float(spec.get("value", 0))
            if cond in LIMITS and not LIMITS[cond][0] <= value <= LIMITS[cond][1]:
                return None
            return Job("when", cond=cond, value=value)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def report(topic: str, snap: dict, watch, gaps) -> str:
    me = snap["me"]
    if topic == "tyres":
        avg, worst = watch.tyre_wear(snap)
        return f"Tyres are {words(int(round(avg * 100)))} percent worn. The {worst} is working hardest."
    if topic == "fuel":
        use, togo = watch.per_lap(), watch.laps_to_go(snap)
        text = f"Fuel is {say_litres(snap['fuel'])}"
        if use:
            text += f", {words(int(snap['fuel'] / use))} laps in the tank"
        if togo is not None:
            text += f", about {words(int(math.ceil(togo)))} to go"
        return text + "."
    if topic == "plan":
        from .plan import make_plan, say_plan
        use, togo = watch.per_lap(), watch.laps_to_go(snap)
        if not use or togo is None:
            return "I can't make a plan yet."
        return say_plan(make_plan(me["laps"], togo, snap["fuel"], snap["fuel_cap"], use))
    if topic == "position":
        text = f"P{me['place']}"
        if gaps:
            if gaps[1] is not None:
                text += f", gap ahead {say_gap(gaps[1])}"
            if gaps[2] is not None:
                text += f", gap behind {say_gap(gaps[2])}"
        return text + "."
    if topic == "weather":
        if snap["raining"] >= 0.05:
            return f"It's raining, track wetness {words(int(round(snap['wetness'] * 100)))} percent."
        return f"Dry. Track wetness {words(int(round(snap['wetness'] * 100)))} percent."
    if topic == "time":
        togo = watch.laps_to_go(snap)
        return f"About {words(int(math.ceil(togo)))} laps to go." if togo is not None else "I can't tell the time left yet."
    n = me["penalties"]
    return "You have a penalty." if n else "No penalties."


def holds(job: Job, snap: dict, watch, gaps) -> bool:
    c, v = job.cond, job.value
    if c == "rain":
        return snap["raining"] >= 0.05
    if c == "yellow":
        return snap["phase"] == 6 or any(snap["sector_flags"])
    if c == "fuel_laps_below":
        use = watch.per_lap()
        return bool(use) and snap["fuel"] / use < v
    if c == "tyres_worn_above":
        return watch.tyre_wear(snap)[0] * 100 > v
    if c == "gap_behind_below":
        return bool(gaps) and gaps[2] is not None and gaps[2] < v
    return bool(gaps) and gaps[1] is not None and gaps[1] < v


CONDITION_TEXT = {"rain": "As you asked: rain.", "yellow": "As you asked: yellow flag out there.",
                  "fuel_laps_below": "As you asked: fuel is getting short.",
                  "tyres_worn_above": "As you asked: tyres are past your limit.",
                  "gap_behind_below": "As you asked: car behind is close.",
                  "gap_ahead_below": "As you asked: you're right on the car ahead."}


class JobBook:
    def __init__(self):
        self.jobs: list[Job] = []

    def add(self, job: Job) -> bool:
        if len(self.jobs) >= MAX_JOBS:
            return False
        self.jobs.append(job)
        return True

    def clear(self) -> None:
        self.jobs.clear()

    def due(self, snap: Optional[dict], watch, gaps) -> list[Msg]:
        """The jobs whose time has come, as calls (a timed job is done once; a condition rearms when it stops holding)."""
        if not snap or watch is None:
            return []
        out, keep = [], []
        for j in self.jobs:
            if j.kind == "when":
                now = holds(j, snap, watch, gaps)
                if now and j.armed:
                    out.append(Msg(CONDITION_TEXT[j.cond], priority=0, expires=15, kind="job"))
                j.armed = not now
                keep.append(j)
            elif (j.kind == "laps" and snap["me"]["laps"] >= j.at) or (j.kind == "minutes" and snap["t"] >= j.at):
                out.append(Msg(report(j.topic, snap, watch, gaps), priority=1, expires=30, kind="job"))
            else:
                keep.append(j)
        self.jobs = keep
        return out
