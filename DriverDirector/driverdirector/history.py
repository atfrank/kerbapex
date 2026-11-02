"""Plan from race history: analyse a whole race from LMU's REST data without playing the replay.

Sources (all available while a replay is loaded):
  /rest/watch/standings/history   per car, per lap: position, lap/sector times, pit flag
  /rest/watch/getIncidentsList    every contact with its exact replay time
  /rest/watch/standings           roster (slot, name, number, class)

Contacts are second-accurate. Everything derived from laps (passes, pit stops, fastest lap) is only
known to the lap, so those events carry confidence="lap" and a lap-wide window; they feed the
focus driver's story and commentary, and `scan_windows()` lists where a targeted live scan could
pin them down. Lap end times are reconstructed by summing lap times from `race_start`, which is
an estimate unless supplied.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from .lmu_api import fix_mojibake
from .phrasing import say_duration, say_gap
from .models import Car, Event, Frame


@dataclass
class Driver:
    slot: int
    name: str
    number: str = ""
    car_class: str = ""
    vehicle: str = ""
    grid: int = 0


@dataclass
class Lap:
    n: int
    position: int
    time: float            # seconds, or -1 if the game didn't record one
    pit: bool = False
    finished: bool = False
    et: float | None = None                  # exact session time at lap end (results XML only)
    lines: tuple[float, ...] = ()            # exact crossing times of sector-1/2/finish lines
    s: tuple[float, float, float] = (-1.0, -1.0, -1.0)    # recorded sector durations (-1 = not recorded)
    compound: str = ""                       # tyre compound on the car this lap ("Medium"); results XML only


@dataclass
class Incident:
    et: float
    player: str
    other: str | None      # None => immovable object
    mag: float = 0.0       # impact magnitude as reported by the game (results XML only)


@dataclass
class Quali:
    position: int
    class_position: int
    best_lap: float        # seconds, or -1 if the car set no time
    best_lap_n: int = 0              # which lap of the session it was (0 = unknown)
    best_lap_start: float = -1.0     # session time at which that lap began (the qualifying replay's clock)
    best_sectors: tuple[float, float, float] = (-1.0, -1.0, -1.0)   # sector durations of that lap


@dataclass
class RaceHistory:
    drivers: dict[int, Driver]
    laps: dict[int, list[Lap]]
    incidents: list[Incident]
    race_start: float
    track: str = ""
    session: str = ""
    exact: bool = False                      # True when lap end times come from the results XML
    penalties: list[tuple[float, str, str]] = field(default_factory=list)    # (et, driver, text)
    source: str = "rest"
    track_len: float = 0.0
    date: str = ""                           # local date of the session, YYYY-MM-DD (results XML only)
    race_minutes: float = 0.0                # time limit of the race (0 = none), from the results XML
    race_laps: int = 0                       # lap limit of the race (0 = none)
    event: str = ""                          # the event's name ("1000 Miles of Sebring"), results XML only
    track_warnings: list[tuple[float, str, str]] = field(default_factory=list)   # (et, driver, text) track-limit warnings
    sector_bests: list[tuple[float, str, int]] = field(default_factory=list)     # (et, driver, sector) new class best
    quali: dict[int, Quali] = field(default_factory=dict)    # slot -> qualifying result (results XML only)
    # filled by build_times(): est. replay time at the end of each completed lap, per slot
    t_end: dict[int, dict[int, float]] = field(default_factory=dict)

    def by_name(self, name: str) -> int | None:
        for d in self.drivers.values():
            if d.name == name:
                return d.slot
        base = name.split("#")[0]
        for d in self.drivers.values():
            if d.name.split("#")[0] == base:
                return d.slot
        return None

    @property
    def total_laps(self) -> int:
        """Laps the winner completed (the longest run of timed laps of any car), 0 when unknown."""
        return max((l.n for ls in self.laps.values() for l in ls if l.et is not None), default=0)

    @property
    def race_end(self) -> float:
        ends = [max(t.values()) for t in self.t_end.values() if t]
        return max(ends) if ends else self.race_start


def _number(vehicle: str, fallback: str = "") -> str:
    if "#" in vehicle:
        return vehicle.split("#", 1)[1].split(":", 1)[0].strip()[:4]
    return fallback


def parse(raw_history: dict, raw_incidents: list, raw_session: dict, raw_standings: list,
          race_start: float | None = None) -> RaceHistory:
    drivers: dict[int, Driver] = {}
    for s in raw_standings:
        drivers[int(s["slotID"])] = Driver(int(s["slotID"]), fix_mojibake(s["driverName"]),
                                           str(s.get("carNumber", "")), s.get("carClass", ""),
                                           s.get("vehicleName", ""))
    laps: dict[int, list[Lap]] = {}
    for slot_s, recs in raw_history.items():
        slot = int(slot_s)
        if slot not in drivers and recs:
            r0 = recs[0]
            drivers[slot] = Driver(slot, fix_mojibake(r0["driverName"]), _number(r0.get("vehicleName", "")),
                                   r0.get("carClass", ""), r0.get("vehicleName", ""))
        laps[slot] = sorted((Lap(r["totalLaps"], r["position"], r["lapTime"] if r["lapTime"] > 0 else -1.0,
                                 bool(r["pitting"]), r.get("finishStatus") == "FSTAT_FINISHED")
                             for r in recs), key=lambda l: l.n)
    h = RaceHistory(drivers, laps, [], 0.0, fix_mojibake(raw_session.get("trackName", "")),
                    raw_session.get("session", ""))
    for i in raw_incidents:
        other = None if i["contactWith"] == "Immovable" else fix_mojibake(i["contactWith"])
        h.incidents.append(Incident(float(i["et"]), fix_mojibake(i["player"]), other))
    h.incidents.sort(key=lambda i: i.et)
    h.race_start = race_start if race_start is not None else estimate_race_start(h)
    build_times(h)
    return h


def finish_exact(h: RaceHistory) -> None:
    """Use the results XML's lap end times as-is; race start = when the leader's lap 1 began."""
    h.exact = True
    h.t_end = {s: {l.n: l.et for l in ls if l.et is not None} for s, ls in h.laps.items()}
    starts = [l.et - l.time for ls in h.laps.values() for l in ls if l.n == 1 and l.et and l.time > 0]
    h.race_start = min(starts) if starts else 0.0


def estimate_race_start(h: RaceHistory) -> float:
    """Heuristic: the first-corner pile-up lands ~12 s after the green flag. Override when known."""
    if not h.incidents:
        return 0.0
    return max(0.0, h.incidents[0].et - 12.0)


def build_times(h: RaceHistory) -> None:
    """Estimate each car's lap-end replay times by cumulative lap time (gaps filled with its median)."""
    allv = [l.time for ls in h.laps.values() for l in ls if l.time > 0]
    glob = statistics.median(allv) if allv else 100.0
    h.t_end = {}
    for slot, ls in h.laps.items():
        valid = [l.time for l in ls if l.time > 0]
        med = statistics.median(valid) if valid else glob
        by_n = {l.n: l for l in ls}
        t, out = h.race_start, {}
        for n in range(1, max(by_n, default=0) + 1):
            l = by_n.get(n)
            t += l.time if l and l.time > 0 else med
            out[n] = t
        h.t_end[slot] = out


def _lap_at(h: RaceHistory, slot: int, t: float) -> int:
    n = 0
    for lap, te in h.t_end.get(slot, {}).items():
        if te <= t:
            n = lap
    return n


def place_at(h: RaceHistory, slot: int, t: float) -> int | None:
    laps = {l.n: l for l in h.laps.get(slot, [])}
    n = _lap_at(h, slot, t)
    l = laps.get(n) or laps.get(0)
    return l.position if l else None


def _bonus(place: int | None) -> float:
    return 0.0 if place is None else max(0.0, 6 - place) * 0.5


def _short(name: str) -> str:
    return name.split("#")[0]


# ---- event derivation -----------------------------------------------------
def incident_events(h: RaceHistory, cluster_s: float = 5.0) -> list[Event]:
    clusters: list[list[Incident]] = []
    for inc in h.incidents:
        if clusters and inc.et - clusters[-1][-1].et <= cluster_s:
            clusters[-1].append(inc)
        else:
            clusters.append([inc])
    out: list[Event] = []
    for cl in clusters:
        ids: list[int] = []
        for inc in cl:
            for nm in (inc.player, inc.other):
                s = h.by_name(nm) if nm else None
                if s is not None and s not in ids:
                    ids.append(s)
        if not ids:
            continue
        t0, t1 = cl[0].et, cl[-1].et
        best = min((place_at(h, i, t0) or 99) for i in ids)
        hard = max((c.mag for c in cl), default=0.0)
        score = (3.0 + min(len(cl), 5) * 0.6 + (1.5 if len(ids) > 2 else 0) + _bonus(best)
                 + min(2.0, max(0.0, (hard - 300) / 1000)))
        names = [_short(h.drivers[i].name) for i in ids]
        if len(ids) == 1:                                   # how hard it was: the game reports the strength of the impact
            how = "brushes the barrier" if hard < 250 else "hits the barrier" if hard < 700 else "hits the barrier hard"
            desc = f"{names[0]} {how}"
        elif len(ids) == 2:
            desc = f"contact: {names[0]} and {names[1]}"
        else:
            desc = f"{len(ids)}-car incident"
        out.append(Event("incident", t0 - 2.0, t1 + 3.0, ids, round(score, 2), desc, "exact", peak=t0))
    return out


def lap_events(h: RaceHistory) -> list[Event]:
    out: list[Event] = []
    pos = {s: {l.n: l.position for l in ls} for s, ls in h.laps.items()}
    pit = {s: {l.n for l in ls if l.pit and l.n > 0} for s, ls in h.laps.items()}
    maxlap = max((max(p) for p in pos.values() if p), default=0)
    # passes: pair swaps order between consecutive lap ends (same class, neither pitting)
    slots = sorted(h.drivers)
    for n in range(1, maxlap + 1):
        for a in slots:
            for b in slots:
                if a >= b or h.drivers[a].car_class != h.drivers[b].car_class:
                    continue
                pa0, pb0, pa1, pb1 = pos[a].get(n - 1), pos[b].get(n - 1), pos[a].get(n), pos[b].get(n)
                if None in (pa0, pb0, pa1, pb1) or n in pit[a] or n in pit[b] or (n - 1) in pit[a] | pit[b]:
                    continue
                if (pa0 - pb0) * (pa1 - pb1) < 0:           # order flipped
                    win, lose = (a, b) if pa1 < pb1 else (b, a)
                    t0 = h.t_end[win].get(n - 1, h.race_start)
                    t1 = h.t_end[win].get(n, t0 + 100)
                    p = min(pa1, pb1)
                    out.append(Event("overtake", t0, t1, [win, lose], round(2.5 + _bonus(p), 2),
                                     f"{_short(h.drivers[win].name)} passes {_short(h.drivers[lose].name)} "
                                     f"for P{p} on lap {n}", "lap"))
    for s, ps in pit.items():
        for n in ps:
            t1 = h.t_end[s].get(n, h.race_start)
            out.append(Event("pit", t1 - 100, t1, [s], 1.0, f"pit stop on lap {n}", "lap"))
    best = [(l.time, s, l.n) for s, ls in h.laps.items() for l in ls if l.time > 0]
    if best:
        tm, s, n = min(best)
        t1 = h.t_end[s].get(n, h.race_start)
        out.append(Event("fastest", t1 - tm, t1, [s], 3.0, f"fastest lap {tm:.3f}s by {_short(h.drivers[s].name)} on lap {n}", "lap"))
    return out


def line_crossings(h: RaceHistory) -> dict[int, dict[int, float]]:
    """slot -> {line index: exact crossing time}; 3 lines per lap (sector 1, sector 2, finish)."""
    out: dict[int, dict[int, float]] = {}
    for s, ls in h.laps.items():
        d: dict[int, float] = {}
        for l in ls:
            for k, t in enumerate(l.lines):
                d[(l.n - 1) * 3 + k + 1] = t
        out[s] = d
    return out


def timing_events(h: RaceHistory, battle_gap: float = 1.0, min_lines: int = 3) -> list[Event]:
    """Battles and passes from exact line-crossing times (about every 30 s of racing)."""
    cr = line_crossings(h)
    pit_lines: dict[int, set[int]] = {}
    for s, ls in h.laps.items():
        pl: set[int] = set()
        for l in ls:
            if l.pit:                                   # the pit lap and the lap before are unreliable
                pl |= {(l.n - 1) * 3 + k + 1 for k in range(3)} | {(l.n - 2) * 3 + k + 1 for k in range(3)}
        pit_lines[s] = pl
    slots = sorted(h.drivers)
    maxline = max((max(c) for c in cr.values() if c), default=0)
    out: list[Event] = []
    ahead_at: dict[int, list[tuple[int, int, float]]] = {}      # line -> [(ahead, behind, gap)]
    for k in range(1, maxline + 1):
        reached = sorted((cr[s][k], s) for s in slots if k in cr.get(s, {}))
        rows = []
        for cls in {h.drivers[s].car_class for _, s in reached}:
            cls_rows = [(t, s) for t, s in reached if h.drivers[s].car_class == cls]
            for (ta, a), (tb, b) in zip(cls_rows, cls_rows[1:]):
                rows.append((a, b, tb - ta))
        ahead_at[k] = rows
    # battles: a pair kept within battle_gap on >= min_lines consecutive lines
    run: dict[frozenset, list[tuple[int, float, int, int]]] = {}
    closed: list[list[tuple[int, float, int, int]]] = []
    for k in range(1, maxline + 1):
        seen = set()
        for a, b, g in ahead_at[k]:
            if g <= battle_gap and k not in pit_lines[a] and k not in pit_lines[b]:
                key = frozenset((a, b))
                seen.add(key)
                run.setdefault(key, []).append((k, g, a, b))
        for key in [x for x in run if x not in seen]:
            closed.append(run.pop(key))
    closed += run.values()
    for r in closed:
        if len(r) < min_lines:
            continue
        best = min(r, key=lambda x: x[1])
        lead, chase = best[2], best[3]
        t0, t1 = cr[lead][r[0][0]], cr[lead][r[-1][0]]
        place = place_at(h, lead, t0) or 99
        score = 2.5 + min(len(r), 12) * 0.25 + (battle_gap - best[1]) * 2 + _bonus(place)
        out.append(Event("battle", t0, t1, [chase, lead], round(score, 2),
                         f"{_short(h.drivers[chase].name)} stuck to the back of {_short(h.drivers[lead].name)} "
                         f"for {say_duration(t1 - t0)}, getting as close as {say_gap(best[1])}", "exact",
                         peak=cr[lead][best[0]], label=f"BATTLE FOR P{place}"))
    # passes: an adjacent pair flips order between two lines; time is interpolated from crossings
    for k in range(2, maxline + 1):
        prev = {(a, b) for a, b, _ in ahead_at[k - 1]}
        for a, b, _ in ahead_at[k]:
            if (b, a) not in prev:
                continue
            if not all(x in cr[s] for s in (a, b) for x in (k - 1, k)):
                continue
            if {k, k - 1} & (pit_lines[a] | pit_lines[b]):
                continue
            a0, a1, b0, b1 = cr[a][k - 1], cr[a][k], cr[b][k - 1], cr[b][k]
            den = (a1 - a0) - (b1 - b0)
            tp = (b0 * (a1 - a0) - a0 * (b1 - b0)) / den if abs(den) > 1e-6 else (a0 + a1) / 2
            tp = min(max(tp, min(a0, b0)), max(a1, b1))
            p = place_at(h, a, tp) or 99
            out.append(Event("overtake", tp - 4, tp + 4, [a, b], round(3.5 + _bonus(p), 2),
                             f"{_short(h.drivers[a].name)} passes {_short(h.drivers[b].name)} for P{p}",
                             "est", peak=tp))
    return out


def penalty_events(h: RaceHistory) -> list[Event]:
    out = []
    for et, name, text in h.penalties:
        s = h.by_name(name)
        if s is not None and "received" in text:
            out.append(Event("penalty", et - 1, et + 8, [s], round(3.5 + _bonus(place_at(h, s, et)), 2),
                             text.split(".")[0][:70], "exact", peak=et))
    return out


def pits_and_fastest(h: RaceHistory) -> list[Event]:
    out = []
    for s, ls in h.laps.items():
        for l in ls:
            if l.pit and l.et:
                out.append(Event("pit", l.et - 25, l.et + 5, [s], 1.0, f"pit stop on lap {l.n}", "exact", peak=l.et))
    best = [(l.time, s, l) for s, ls in h.laps.items() for l in ls if l.time > 0 and l.et]
    if best:
        tm, s, l = min(best, key=lambda x: x[0])
        out.append(Event("fastest", l.et - tm, l.et, [s], 3.0,
                         f"fastest lap {tm:.3f}s by {_short(h.drivers[s].name)} on lap {l.n}", "exact", peak=l.et))
    return out


def tracklimit_events(h: RaceHistory) -> list[Event]:
    """Track-limit warnings that were actually issued (the game also logs every excursion, which is not worth a mention)."""
    out = []
    for et, name, text in h.track_warnings:
        s = h.by_name(name)
        if s is not None:
            out.append(Event("tracklimits", et - 1, et + 8, [s], round(3.0 + _bonus(place_at(h, s, et)), 2),
                             "track limits warning", "exact", peak=et))
    return out


def derive_events(h: RaceHistory) -> list[Event]:
    if h.exact:
        ev = (incident_events(h) + timing_events(h) + penalty_events(h) + pits_and_fastest(h)
              + tracklimit_events(h))
    else:
        ev = incident_events(h) + lap_events(h)
    return sorted(ev, key=lambda e: e.t_start)


def scan_windows(events: list[Event], focus: int, pad: float = 6.0, min_score: float = 2.5) -> list[tuple[float, float]]:
    """Lap-resolution windows worth a targeted live scan (focus-driver events), merged."""
    wins = sorted((e.t_start - pad, e.t_end + pad) for e in events
                  if e.confidence == "lap" and e.involves(focus) and e.score >= min_score)
    merged: list[list[float]] = []
    for a, b in wins:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


# ---- coarse frames so the planner/commentary can reuse their Frame-based code ------------
def frames_from_history(h: RaceHistory, step: float = 10.0) -> list[Frame]:
    """One Frame every `step` seconds, for roster/place lookups in the planner and commentary."""
    if h.exact:
        pts = _progress_points(h)
        out, t = [], h.race_start
        while t <= h.race_end + step:
            out.append(frame_at(h, t, pts))
            t += step
        return out
    frames: list[Frame] = []
    t = h.race_start
    end = h.race_end
    while t <= end + step:
        cars = []
        for s, d in h.drivers.items():
            laps = {l.n: l for l in h.laps.get(s, [])}
            n = _lap_at(h, s, t)
            l = laps.get(n) or laps.get(0)
            if not l:
                continue
            last = next((x.time for x in (laps.get(k) for k in range(n, 0, -1)) if x and x.time > 0), -1.0)
            cars.append(Car(id=s, driver=_short(d.name), number=d.number, car_class=d.car_class,
                            place=l.position, laps=n, last_lap=last, in_pits=False,
                            best_lap=min((x.time for x in laps.values() if x.time > 0 and x.n <= n), default=-1.0)))
        counts: dict[str, int] = {}
        for c in sorted(cars, key=lambda c: c.place):
            counts[c.car_class] = counts.get(c.car_class, 0) + 1
            c.class_place = counts[c.car_class]
        frames.append(Frame(t=round(t, 2), track_len=0.0, cars=sorted(cars, key=lambda c: c.place)))
        t += step
    return frames


def _progress_points(h: RaceHistory) -> dict[int, list[tuple[float, float]]]:
    """slot -> [(time, progress)] where progress counts timing lines (3 per lap)."""
    cr = line_crossings(h)
    pts: dict[int, list[tuple[float, float]]] = {}
    for s in h.drivers:
        d = dict(cr.get(s, {}))
        for n, t in h.t_end.get(s, {}).items():
            d[n * 3] = t                                  # lap ends are always known
        p = [(h.race_start, 0.0)] + sorted((t, float(k)) for k, t in d.items())
        pts[s] = [x for i, x in enumerate(p) if i == 0 or x[0] > p[i - 1][0]]
    return pts


def frame_at(h: RaceHistory, t: float, pts: dict | None = None) -> Frame:
    """Race state at replay time `t`, ordered by interpolated track progress (exact crossing times)."""
    pts = pts or _progress_points(h)
    track = h.track_len or 4000.0
    cars: list[Car] = []
    for s, d in h.drivers.items():
        p = pts.get(s) or []
        if not p:
            continue
        prog = 0.0
        for (t0, g0), (t1, g1) in zip(p, p[1:]):
            if t < t0:
                break
            prog = g1 if t >= t1 else g0 + (g1 - g0) * (t - t0) / (t1 - t0)
        laps_done = {l.n: l for l in h.laps.get(s, []) if l.et is not None and l.et <= t}
        last = max(laps_done) if laps_done else 0
        lt = laps_done[last].time if last else -1.0
        cars.append(Car(id=s, driver=_short(d.name), number=d.number, car_class=d.car_class,
                        laps=int(prog // 3), lap_dist=(prog % 3) / 3 * track, speed=45.0,
                        last_lap=lt, best_lap=min((l.time for l in laps_done.values() if l.time > 0), default=-1.0)))
    cars.sort(key=lambda c: (-c.total_dist(track), h.drivers[c.id].grid or 999))   # grid order at the start
    counts: dict[str, int] = {}
    for i, c in enumerate(cars, 1):
        c.place = i
        counts[c.car_class] = counts.get(c.car_class, 0) + 1
        c.class_place = counts[c.car_class]
    return Frame(t=t, track_len=track, cars=cars)


class Timeline:
    """When each car reached each point of the track, for exact time gaps between two cars (live gaps need
    no speed guess: the gap behind a car is how long after it passed this point that the next car did)."""

    def __init__(self, h: RaceHistory, pts: dict | None = None) -> None:
        self._t: dict[int, list[float]] = {}
        self._p: dict[int, list[float]] = {}
        for s, p in (pts or _progress_points(h)).items():
            hi, ps = -1.0, []
            for _, g in p:
                hi = max(hi, g)                                 # progress never goes backwards
                ps.append(hi)
            self._t[s], self._p[s] = [t for t, _ in p], ps

    def time_at(self, slot: int, progress: float) -> Optional[float]:
        """The race time at which `slot` reached `progress` (timing lines; 3 per lap), or None if it never did."""
        import bisect
        ts, ps = self._t.get(slot), self._p.get(slot)
        if not ps or progress > ps[-1]:
            return None
        i = bisect.bisect_left(ps, progress)
        if i == 0:
            return ts[0]
        p0, p1 = ps[i - 1], ps[i]
        return ts[i] if p1 <= p0 else ts[i - 1] + (ts[i] - ts[i - 1]) * (progress - p0) / (p1 - p0)

    def gap(self, me: int, me_progress: float, other: int, other_progress: float, t: float) -> Optional[float | int]:
        """Seconds `other` is ahead of (negative) or behind (positive) `me` at time `t`. A car a lap or more
        away gives whole laps instead (an int, same sign convention)."""
        laps = (other_progress - me_progress) / 3.0
        if abs(laps) >= 1.0:
            return -int(laps) if laps > 0 else int(-laps)
        when = self.time_at(other, me_progress)
        return None if when is None else when - t
