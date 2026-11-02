"""Who is around the focus driver and how the time is moving, for ANY driver in ANY replay that has exact results.

Gaps are exact (history.Timeline: how long after one car passed a point the next car did), so they need no telemetry.
Everything handed to the narrator is said the way people say it (see phrasing.py): "about two seconds up the road",
"closing, taking about half a second out of the gap over the last lap".
"""
from __future__ import annotations

from typing import Callable, Optional

from . import history as hist
from .models import Car, Frame
from .overlay import _gap
from .phrasing import say_gap, w

HOLDING = 0.25          # a change smaller than this (seconds) is "holding steady"


class RaceContext:
    def __init__(self, h: hist.RaceHistory):
        self.h = h
        self.pts = hist._progress_points(h)
        self.tl = hist.Timeline(h, self.pts) if h.exact else None
        self._cache: dict[float, Frame] = {}

    def frame(self, t: float) -> Frame:
        k = round(t, 1)
        if k not in self._cache:
            self._cache[k] = hist.frame_at(self.h, t, self.pts)
        return self._cache[k]

    def _me(self, frame: Frame, focus: int) -> Optional[Car]:
        return next((c for c in frame.cars if c.id == focus), None)

    def around(self, t: float, focus: int) -> dict:
        """{'ahead': (car, gap seconds) | None, 'behind': ...}: the cars directly ahead and behind in the focus
        driver's own class, with the exact gap to each (None when a lap or more away)."""
        f = self.frame(t)
        me = self._me(f, focus)
        out: dict = {"ahead": None, "behind": None}
        if me is None or self.tl is None:
            return out
        for key, place in (("ahead", me.class_place - 1), ("behind", me.class_place + 1)):
            car = next((c for c in f.cars if c.car_class == me.car_class and c.class_place == place), None)
            gap = _gap(f, me, car, self.tl) if car else None
            if car is not None and gap is not None:
                out[key] = (car, gap)
        return out

    def gap_to(self, t: float, focus: int, other: int) -> Optional[float]:
        """The exact gap in seconds between the focus car and one particular car at `t`, wherever it is in the order
        (None when a lap or more apart, or one of them is not on track)."""
        f = self.frame(t)
        me, car = self._me(f, focus), self._me(f, other)
        if me is None or car is None or self.tl is None:
            return None
        return _gap(f, me, car, self.tl)

    def sector(self, slot: int, t: float) -> int:
        """1, 2 or 3: the timing sector `slot` is in at `t` (progress counts the real timing lines)."""
        f = self.frame(t)
        c = self._me(f, slot)
        if c is None or not f.track_len:
            return 0
        return min(3, int(c.lap_dist / f.track_len * 3) + 1)


def _name(car: Car) -> str:
    """The driver's name as it should be read out: the game stores some in ALL CAPS, which a voice may spell out."""
    n = car.driver
    return n.title() if n.isupper() else n


def around_facts(ctx: RaceContext, t: float, focus: int, back: float = 100.0) -> str:
    """The cars directly ahead and behind, the gap to each and which way it has moved over about the last lap."""
    now, tb = ctx.around(t, focus), max(ctx.frame(t).t - back, ctx.h.race_start)
    parts: list[str] = []
    for key, verb in (("ahead", "up the road"), ("behind", "behind")):
        cur = now[key]
        if cur is None:
            continue
        car, gap = cur
        if gap < 0.4:
            s = f"{_name(car)} is right on the back bumper" if key == "behind" else \
                f"our driver is right on the back of {_name(car)}"
        else:
            s = f"{_name(car)} is {say_gap(gap)} {verb}"
        old = ctx.gap_to(tb, focus, car.id) if tb < ctx.frame(t).t - 20 else None   # the same car, about a lap earlier
        if old is not None:
            delta = old - gap                          # positive: the gap has shrunk
            if delta >= HOLDING:
                s += (", and our driver has been closing, taking " if key == "ahead" else ", and has been closing, "
                      "taking ") + f"{say_gap(delta)} out of the gap over the last lap"
            elif delta <= -HOLDING:
                s += (", and our driver has been losing ground, the gap up by " if key == "ahead" else
                      ", but our driver has been pulling away, the gap up by ") + f"{say_gap(-delta)} over the last lap"
            else:
                s += ", and the gap has held steady over the last lap"
        parts.append(s)
    return ". ".join(p[0].upper() + p[1:] for p in parts) + "." if parts else ""


def gap_effect(ctx: RaceContext, t0: float, t1: float, focus: int) -> str:
    """What a stretch of lost (or gained) time did to the gaps to the cars around: only changes worth saying."""
    b = ctx.around(t1, focus)
    out: list[str] = []
    for key in ("ahead", "behind"):
        y = b[key]
        if y is None:
            continue
        x = ctx.gap_to(t0, focus, y[0].id)             # the same car, at the start of the stretch
        if x is None:
            continue
        d = y[1] - x                                   # seconds the gap changed by over the stretch
        if abs(d) < HOLDING:
            continue
        name = _name(y[0])
        from_, to = say_gap(x), say_gap(y[1])
        how = f"by {say_gap(abs(d))}" if from_ == to else f"from {from_} to {to}"      # the two ends can round alike
        if key == "ahead":
            out.append(f"the gap to {name} up the road {'stretched' if d > 0 else 'shrank'} {how}")
        else:
            out.append(f"{name} {'dropped back' if d > 0 else 'closed up'} {how} on our driver")
    return ("In that stretch " + " and ".join(out) + ".") if out else ""


def where_in_sector(ctx: RaceContext, slot: int, t: float) -> str:
    """Where a car was on track when only timing sectors are known: "in sector two" ('' if it cannot be told)."""
    try:
        s = ctx.sector(slot, t)
    except Exception:
        return ""
    return f"in sector {w(s)}" if s else ""
