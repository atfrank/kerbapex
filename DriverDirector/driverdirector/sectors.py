"""Sector times with timing-screen colours: purple = best in class so far, green = driver's own best so far,
orange = slower. Built from the race result XML.

Invalid laps: the game records only sector 1 (and the lap's end time) for them. The missing sector times are
ESTIMATED from the lap's true duration (end time minus the previous lap's end) using the driver's usual
sector proportions, and flagged `est` so the overlay can mark them. Invalid or estimated times are coloured
by comparison but never set a best, so a cut corner cannot take purple from anyone.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional

from .history import RaceHistory

PURPLE, GREEN, ORANGE = "purple", "green", "orange"
EPS = 1e-4


@dataclass
class Cell:
    time: float
    color: str
    est: bool = False


@dataclass
class LapSectors:
    slot: int
    lap: int
    cells: list[Optional[Cell]]            # S1, S2, S3 (None when unknown)
    lap_time: Optional[Cell]               # exact lap duration, coloured against lap bests
    crossings: list[float]                 # when S1, S2 and the finish line were crossed
    valid: bool
    start: float


def _ratios(h: RaceHistory) -> dict[int, tuple[float, float, float]]:
    """Each driver's typical share of a lap spent in S1/S2/S3 (from their complete laps)."""
    out: dict[int, tuple[float, float, float]] = {}
    allr = []
    for slot, ls in h.laps.items():
        full = [l.s for l in ls if all(x > 0 for x in l.s)]
        if full:
            r = tuple(statistics.median(s[k] / sum(s) for s in full) for k in range(3))
            out[slot] = r
            allr.append(r)
    default = tuple(statistics.median(r[k] for r in allr) for k in range(3)) if allr else (0.27, 0.5, 0.23)
    for slot in h.laps:
        out.setdefault(slot, default)
    return out


def build(h: RaceHistory, estimate: bool = True) -> dict[tuple[int, int], LapSectors]:
    """(slot, lap) -> LapSectors with colours, for every lap that has an end time."""
    ratios = _ratios(h)
    laps: dict[tuple[int, int], LapSectors] = {}
    for slot, ls in h.laps.items():
        prev_et = None
        for l in sorted(ls, key=lambda x: x.n):
            if l.et is None:
                continue
            # The recorded lap time is exact; the lap-end `et` stamps jitter by up to ~0.7 s (and lap 1's is
            # early), so they are only a fallback for laps the game gave no time for.
            if l.time > 0:
                start = l.et - l.time
            else:
                start = prev_et if prev_et is not None else (h.race_start if l.n == 1 else None)
            prev_et = l.et
            dur = l.time if l.time > 0 else ((l.et - start) if start is not None else None)
            s = list(l.s)
            est = [False, False, False]
            valid = l.time > 0 and all(x > 0 for x in s)
            if not valid and estimate and dur and dur > 0:
                known = sum(x for x in s if x > 0)
                missing = [k for k in range(3) if s[k] <= 0]
                share = sum(ratios[slot][k] for k in missing)
                rest = dur - known
                if missing and rest > 0 and share > 0:
                    for k in missing:
                        s[k] = rest * ratios[slot][k] / share
                        est[k] = True
            cells: list[Optional[Cell]] = [Cell(s[k], ORANGE, est[k]) if s[k] > 0 else None for k in range(3)]
            cr, acc = [], start
            for k in range(3):
                if acc is not None and s[k] > 0:
                    acc += s[k]
                    cr.append(acc)
                else:
                    cr.append(None)
            if cr[2] is None or abs(cr[2] - l.et) > 0.5:      # the finish line is always the recorded end time
                cr[2] = l.et
            laps[(slot, l.n)] = LapSectors(slot, l.n, cells, Cell(dur, ORANGE, est=l.time <= 0) if dur else None,
                                           [c for c in cr], valid, start if start is not None else l.et)
    _colour(h, laps)
    return laps


def _colour(h: RaceHistory, laps: dict[tuple[int, int], LapSectors]) -> None:
    """Assign purple/green/orange in the order the lines were crossed, per class."""
    for idx in range(4):                                    # 0..2 = sectors, 3 = whole lap
        events = []
        for ls in laps.values():
            cell = ls.cells[idx] if idx < 3 else ls.lap_time
            when = ls.crossings[idx] if idx < 3 else ls.crossings[2]
            if cell is not None and when is not None:
                events.append((when, ls, cell))
        events.sort(key=lambda e: e[0])
        class_best: dict[str, float] = {}
        own_best: dict[int, float] = {}
        for _, ls, cell in events:
            cls = h.drivers[ls.slot].car_class
            counts = ls.valid and not cell.est
            best, pb = class_best.get(cls), own_best.get(ls.slot)
            if best is None or cell.time < best - EPS:
                cell.color = GREEN if cell.est else PURPLE     # an estimate never takes purple
            elif pb is None or cell.time < pb - EPS:
                cell.color = GREEN
            else:
                cell.color = ORANGE
            if counts:
                class_best[cls] = min(cell.time, best) if best is not None else cell.time
                own_best[ls.slot] = min(cell.time, pb) if pb is not None else cell.time


def panel(laps: dict[tuple[int, int], LapSectors], slot: int, t: float, hold: float = 6.0) -> Optional[dict]:
    """What to show for `slot` at replay time `t`: the lap in progress (sectors fill in as they are
    crossed), or the finished lap for `hold` seconds after the line."""
    mine = sorted((ls for (s, _), ls in laps.items() if s == slot), key=lambda x: x.lap)
    if not mine:
        return None
    done = [ls for ls in mine if ls.crossings[2] <= t]
    last = done[-1] if done else None
    if last is not None and t - last.crossings[2] < hold:
        cur, finished = last, True
    else:
        cur = next((ls for ls in mine if ls.crossings[2] > t), None)
        finished = False
        if cur is None:
            return None

    def cell(c: Optional[Cell], when: Optional[float]):
        if c is None or when is None or when > t:
            return None
        return {"time": round(c.time, 3), "color": c.color, "est": c.est}

    return {"lap": cur.lap, "valid": cur.valid,
            "cells": [cell(cur.cells[k], cur.crossings[k]) for k in range(3)],
            "lap_time": cell(cur.lap_time, cur.crossings[2]) if finished else None}


class Index:
    """The laps of every driver in finishing order, so a timing tower can look up all its rows cheaply."""

    def __init__(self, laps: dict[tuple[int, int], LapSectors]) -> None:
        self.by_slot: dict[int, list[LapSectors]] = {}
        for (slot, _), ls in laps.items():
            self.by_slot.setdefault(slot, []).append(ls)
        self.ends: dict[int, list[float]] = {}
        for slot, ls in self.by_slot.items():
            ls.sort(key=lambda x: x.lap)
            self.ends[slot] = [x.crossings[2] for x in ls]

    def bars(self, slot: int, t: float, hold: float = 6.0) -> Optional[list[Optional[dict]]]:
        """Three sector colours for a tower row. Sectors of the lap in progress appear as they are crossed;
        until then the previous lap's colours stay (marked `old`), as on a timing screen. For `hold` seconds
        after the line, the lap just finished is shown whole."""
        import bisect
        ls, ends = self.by_slot.get(slot), self.ends.get(slot)
        if not ls:
            return None
        i = bisect.bisect_right(ends, t)                     # first lap not yet finished
        cur = ls[i] if i < len(ls) else None
        prev = ls[i - 1] if i > 0 else None
        if prev is not None and t - ends[i - 1] < hold:
            show = [(prev.cells[k], False) for k in range(3)]
        else:
            show = []
            for k in range(3):
                if cur is not None and cur.cells[k] is not None and cur.crossings[k] is not None                         and cur.crossings[k] <= t:
                    show.append((cur.cells[k], False))
                elif prev is not None and prev.cells[k] is not None:
                    show.append((prev.cells[k], True))
                else:
                    show.append((None, False))
        return [None if c is None else {"color": c.color, "est": c.est, "old": old, "time": round(c.time, 3)}
                for c, old in show]
