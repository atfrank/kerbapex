"""The stint plan: from the fuel in the tank, the fuel used per lap and the laps to go, where the stops fall and how much
goes in at each. Stops are as late as the fuel allows (a lap in hand), tanks are filled full except the last, which is
sized to the flag with a lap in hand. Replanned every lap from measured use, so it follows the race as it unfolds."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from driverdirector.phrasing import w as words

from .racewatch import say_litres


@dataclass
class Stop:
    lap: int            # box at the end of this lap
    fill: float         # litres to put in


@dataclass
class Plan:
    stops: list = field(default_factory=list)
    spare: float = 0.0              # laps of fuel in hand at the flag
    feasible: bool = True           # False when the tank is too small to make progress


def make_plan(laps_done: int, togo: float, fuel: float, cap: float, use: float, reserve: int = 1) -> Plan:
    if use <= 0 or cap <= 0:
        return Plan(feasible=False)
    remaining, done, f, stops = math.ceil(togo), laps_done, fuel, []
    for _ in range(12):
        if f / use >= remaining:
            return Plan(stops, f / use - remaining)
        run = max(0, int(f / use) - reserve)
        remaining, done, f = remaining - run, done + run, f - run * use
        fill = max(0.0, min(cap, use * (remaining + reserve)) - f)
        if fill <= 0.05 * use:
            return Plan(stops, feasible=False)
        stops.append(Stop(done, fill))
        f += fill
    return Plan(stops, feasible=False)


def say_plan(p: Plan) -> str:
    if not p.feasible:
        return "I can't make a plan from this fuel yet."
    if not p.stops:
        return "No stop needed, you can get to the end."
    n = len(p.stops)
    first = p.stops[0]
    text = (f"Plan: {words(n)} stop{'s' if n > 1 else ''}. First at the end of lap {words(first.lap)}, "
            f"{say_litres(first.fill)}")
    if n > 1:
        text += ", then " + ", ".join(f"lap {words(s.lap)}" for s in p.stops[1:])
    return text + "."
