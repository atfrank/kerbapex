"""Facts for the commentary that come from the race's lap data: the track opening, how the first lap compares with the
reference lap, pit stops, personal and class bests, and the closing recap.

Every function returns plain sentences (times, gaps and lengths already said the way people say them, see phrasing.py) so
the writer never has to turn a figure into words, and never has a figure to get wrong. Nothing here talks to the game.
"""
from __future__ import annotations

import statistics
from typing import Optional

from . import history as hist
from . import tracks
from .phrasing import say_gap, say_km, say_race_length, w


def _fmt_lap(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def _short(name: str) -> str:
    return name.split("#")[0]


# ---- the opening --------------------------------------------------------------------------------------------------
def track_intro(h: hist.RaceHistory, conditions: str = "") -> str:
    """The circuit, how long a lap is, how many turns it has and how long the race is. '' when there is nothing to say."""
    if not h or not h.track:
        return ""
    info = tracks.lookup(h.track)
    parts = [f"Welcome to the race at {h.track}" + (f", in {info['place']}" if info.get("place") else "") + "."]
    bits = []
    if info.get("turns"):
        bits.append(f"{w(info['turns'])} turns")
    if h.track_len > 0:
        bits.append(f"about {say_km(h.track_len).removeprefix('about ')} a lap")
    if bits:
        parts.append("The circuit has " + " and is ".join(bits) + "." if len(bits) == 2 and "turns" in bits[0] else
                     "It is " + " and ".join(bits) + ".")
    length = say_race_length(h.race_minutes, h.race_laps)
    if length:
        parts.append(f"The race is {length} long.")
    if h.event and not any(c.isdigit() for c in h.event):       # the game's event name can contradict the race ("4 Hours")
        parts.append(f"The event is called {h.event}.")
    if conditions.strip():
        parts.append(f"Conditions: {conditions.strip()}.")
    return " ".join(parts)


# ---- the first lap against the reference lap -------------------------------------------------------------------------
def reference_sector_facts(h: hist.RaceHistory, slot: int, until: float) -> list[tuple[float, str, int]]:
    """(race time, facts, excitement) at the end of sectors one and two of the first race lap: how far ahead or behind
    the best qualifying lap (shown beside it) the driver is at that point. Only until `until` (when the box goes)."""
    q = h.quali.get(slot)
    lap = next((l for l in h.laps.get(slot, []) if l.n == 1), None)
    if not q or lap is None or len(lap.lines) < 2 or min(q.best_sectors[:2]) <= 0 or min(lap.s[:2]) <= 0:
        return []
    out = []
    race_cum = [lap.s[0], lap.s[0] + lap.s[1]]
    ref_cum = [q.best_sectors[0], q.best_sectors[0] + q.best_sectors[1]]
    for k, word in ((0, "one"), (1, "two")):
        t = lap.lines[k] + 0.5
        if t >= until - 4:
            continue
        d = race_cum[k] - ref_cum[k]                     # positive: behind the reference
        if abs(d) < 0.3:
            what = "level with"
        else:
            what = f"{say_gap(abs(d))} {'behind' if d > 0 else 'ahead of'}"
        out.append((t, f"REFERENCE LAP COMPARISON, end of sector {word}: our driver's first race lap is {what} the best "
                       "qualifying lap in the picture-in-picture box at the same point.", 0))
    return out


# ---- pit stops ------------------------------------------------------------------------------------------------------
def pit_facts(h: hist.RaceHistory, slot: int, t0: float, t1: float) -> list[tuple[float, str, int]]:
    """One scene per pit stop of the driver: how much longer the lap with the stop took and what happened to the place."""
    laps = {l.n: l for l in h.laps.get(slot, [])}
    normal = [l.time for l in laps.values() if l.time > 0 and not l.pit and l.n > 1
              and not laps.get(l.n - 1, l).pit]
    if not normal:
        return []
    base = statistics.median(normal)
    out = []
    for n, l in sorted(laps.items()):
        if not l.pit or l.et is None or not (t0 <= l.et <= t1 - 30):
            continue
        before, after = laps.get(n - 1), laps.get(n + 1)
        parts = [f"PIT STOP: our driver came into the pits on lap {w(n)}."]
        if l.time > 0 and l.time - base >= 5:
            parts.append(f"The lap with the stop took {say_gap(l.time - base)} longer than a normal lap.")
        if before and after:
            if after.position != before.position:
                parts.append(f"They were P{before.position} before the stop and P{after.position} once back out.")
            else:
                parts.append(f"They kept P{before.position} through the stop.")
        if after and l.compound and after.compound and after.compound != l.compound:
            parts.append(f"Tyres: {l.compound} on the way in, {after.compound} on the way out.")
        out.append((l.et + 2.0, " ".join(parts), 0))
    return out


# ---- personal and class bests --------------------------------------------------------------------------------------
def milestone_facts(h: hist.RaceHistory, slot: int, t0: float, t1: float, limit: int = 3
                    ) -> list[tuple[float, str, int]]:
    """The driver's biggest new personal-best laps (and any lap that is the fastest of the race at that moment), and the
    sectors where they set a new best for their class. At most `limit` of each, spread out."""
    out: list[tuple[float, str, int]] = []
    laps = [l for l in h.laps.get(slot, []) if l.time > 0 and l.et is not None and not l.pit and l.n > 1]
    best, cands = float("inf"), []
    for l in sorted(laps, key=lambda l: l.n):
        if l.time < best - 0.15 and best < float("inf") and l.n > 3 and best - l.time <= 5.0:   # not a recovery from a slow lap
            cands.append((best - l.time, l))
        best = min(best, l.time)
    for gain, l in sorted(cands, key=lambda x: -x[0])[:limit]:
        if not (t0 + 20 <= l.et <= t1 - 40):
            continue
        field_best = min((x.time for s, ls in h.laps.items() for x in ls
                          if x.time > 0 and x.et is not None and x.et < l.et and not x.pit), default=float("inf"))
        top = l.time < field_best
        out.append((l.et + 1.0, (f"{'FASTEST LAP OF THE RACE SO FAR' if top else 'PERSONAL BEST'}: our driver's lap "
                                 f"{w(l.n)} was a {_fmt_lap(l.time)}, {say_gap(gain)} quicker than their previous best."),
                    1 if top else 0))
    bests = [(et, sec) for et, drv, sec in h.sector_bests if h.by_name(drv) == slot and t0 + 20 <= et <= t1 - 40]
    last = -1e9
    for et, sec in sorted(bests)[:limit * 3]:
        if et - last >= 60 and sum(1 for o in out if o[1].startswith("CLASS")) < limit:
            out.append((et + 0.8, f"CLASS BEST: our driver has just set the best time in their class so far through "
                                  f"sector {w(sec)}.", 1))
            last = et
    return out


# ---- the finish -----------------------------------------------------------------------------------------------------
def final_places(h: hist.RaceHistory, slot: int) -> tuple[int, int]:
    """(place overall, place in class) from the results: the position at the end of the driver's last lap, and the order of
    the class by those. (0, 0) when the driver has no laps. The flag line and the recap both use this, so they agree."""
    laps = h.laps.get(slot, [])
    if not laps:
        return 0, 0
    cls = h.drivers[slot].car_class
    finals = {s: max(ls, key=lambda l: l.n) for s, ls in h.laps.items() if ls and h.drivers[s].car_class == cls}
    order = sorted(finals, key=lambda s: finals[s].position)
    return max(laps, key=lambda l: l.n).position, (order.index(slot) + 1 if slot in order else 0)


def recap_facts(h: hist.RaceHistory, slot: int, mistakes=()) -> str:
    """How the race ended for the driver: finishing place against the grid, the gap to the class winner, the best lap and
    what the flagged mistakes cost. '' when the results do not say."""
    laps = h.laps.get(slot, [])
    if not laps:
        return ""
    last = max(laps, key=lambda l: l.n)
    cls = h.drivers[slot].car_class
    finals = {s: max(ls, key=lambda l: l.n) for s, ls in h.laps.items() if ls and h.drivers[s].car_class == cls}
    order = sorted(finals, key=lambda s: finals[s].position)
    class_pos = order.index(slot) + 1 if slot in order else 0
    parts = [f"RACE RECAP: our driver finished P{last.position} overall"
             + (f", P{class_pos} in class" if class_pos else "") + "."]
    grid = h.drivers[slot].grid
    if grid and grid != last.position:
        moved = grid - last.position
        parts.append(f"They started from P{grid}, so they {'gained' if moved > 0 else 'lost'} {w(abs(moved))} "
                     f"place{'s' if abs(moved) != 1 else ''}.")
    elif grid:
        parts.append(f"They finished where they started, P{grid}.")
    winner = order[0] if order else None
    if winner is not None and winner != slot:
        wl = finals[winner]
        if wl.n > last.n:
            laps_down = wl.n - last.n
            parts.append(f"They finished {w(laps_down)} lap{'s' if laps_down > 1 else ''} behind the class winner.")
        elif wl.et is not None and last.et is not None and last.et > wl.et:
            parts.append(f"They crossed the line {say_gap(last.et - wl.et)} after the class winner.")
    valid = [l for l in laps if l.time > 0 and not l.pit and l.n > 1]
    if valid:
        b = min(valid, key=lambda l: l.time)
        parts.append(f"Their best lap was a {_fmt_lap(b.time)} on lap {w(b.n)}.")
    lost = sum(getattr(m, "loss", 0.0) for m in mistakes)
    if lost >= 1.0:
        parts.append(f"The driving mistakes flagged in this video cost {say_gap(lost)} in total against the best "
                     "qualifying lap.")
    return " ".join(parts)
