"""Where and why the focus driver lost time, from the game's own telemetry (see telemetry.py).

Every timed race lap is compared with a reference lap (the best qualifying lap of the same event, else the driver's own
best race lap) at the same lap distance. A STRETCH of track where a lap falls at least `min_loss` seconds behind is a
candidate; it only becomes a mistake when the data also shows a cause. Time lost alone is not narrated (traffic, a
lift, a yellow flag all lose time without being an error), so nobody is told they erred without evidence:

* went in too deep  - carried more speed than the reference at the reference's braking point AND was slower at the apex
* braked too early  - much slower than the reference at its braking point and slower at the apex
* missed the apex   - on a line several metres away from the reference at the apex (without carrying extra speed in)
* ran wide          - beyond the track edge (|lateral| > |edge|) around the corner
* poor exit         - slower at the exit, or the throttle fully applied noticeably later than the reference

Everything said about a mistake is a measured number (km/h, metres, seconds).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import telemetry as T
from .models import Event

GRID = 25.0            # metres between the points laps are compared at
WINDOW = 150.0         # a stretch is judged over at least this much track
MIN_SEP = 250.0        # two stretches of one lap are at least this far apart

CORNER_DROP_KMH = 30.0   # the reference slows by at least this much: only then is the stretch a corner
DEEP_ENTRY_KMH = 8.0       # extra speed partway into the reference's braking zone
APEX_SLOWER_KMH = 3.0
EARLY_ENTRY_KMH = -10.0    # (same point)
EARLY_APEX_KMH = -5.0
LINE_METRES = 2.5          # distance from the reference line at the apex
OVER_METRES = 0.3          # past the track edge
EXIT_KMH = -6.0
THROTTLE_LATE_M = 20.0

# Corner names by lap distance (metres from the start/finish line), per track. Only corners that were confirmed against
# a real lap's speed trace are listed; anywhere else the stretch is described by its distance. Add more in
# %APPDATA%/DriverDirector/corners.json: {"<track name>": [[from_m, to_m, "name"], ...]}.
CORNERS: dict[str, list[tuple[float, float, str]]] = {
    "circuitdespafrancorchamps": [(0, 450, "La Source"), (2250, 2600, "Les Combes"), (3600, 3850, "Pouhon"),
                                  (4700, 4950, "Stavelot"), (6500, 6800, "the Bus Stop")],
}


def corner_name(track: str, dist: float) -> str:
    table = dict(CORNERS)
    try:
        import json
        from .config import data_dir
        extra = json.loads((data_dir() / "corners.json").read_text(encoding="utf-8"))
        table.update({T._norm(k): [tuple(r) for r in v] for k, v in extra.items()})
    except (OSError, ValueError):
        pass
    for a, b, name in table.get(T._norm(track), []):
        if a <= dist <= b:
            return name
    return ""


from .phrasing import (CAR_LENGTH_M, join_and as _and, say_across, say_along, say_lap_position, say_over,  # noqa: F401
                       say_seconds, say_speed, w as _w)


def corner_apexes(ref: T.Trace) -> list[float]:
    """Lap distance of each corner of the reference lap: a slowest point that the car comes down to from, and goes back
    up to, a clearly higher speed (CORNER_DROP_KMH). Works on any track, so corners can be numbered when no name is
    known. Flat-out kinks are not counted, so the numbers are 'slow corners from the start line', not an official map."""
    import bisect
    pts = [(p[0], p[1]) for p in ref.s]
    found: list[tuple[float, float]] = []
    for k in range(5, len(pts) - 5):
        if pts[k][1] > min(p[1] for p in pts[k - 5:k + 6]):
            continue
        a, b = bisect.bisect_left(ref.d, pts[k][0] - 450), bisect.bisect_right(ref.d, pts[k][0] + 450)
        before = max((p[1] for p in pts[a:k]), default=0.0)
        after = max((p[1] for p in pts[k:b]), default=0.0)
        if before - pts[k][1] >= CORNER_DROP_KMH and after - pts[k][1] >= CORNER_DROP_KMH:
            if found and pts[k][0] - found[-1][0] < 150:
                if pts[k][1] < found[-1][1]:
                    found[-1] = pts[k]
            else:
                found.append(pts[k])
    return [round(d) for d, _ in found]


def corner_label(track: str, dist: float, apexes: list[float]) -> str:
    """"the Bus Stop" if the track's corner is named (shipped table or corners.json), else "corner five" counted from the
    start line, else "" (somewhere between corners)."""
    name = corner_name(track, dist)
    if name:
        return name
    if apexes:
        i = min(range(len(apexes)), key=lambda k: abs(apexes[k] - dist))
        if abs(apexes[i] - dist) <= 150:
            return f"corner {_w(i + 1)}"
    return ""


@dataclass
class Mistake:
    lap: int
    d0: float                  # lap distance where the stretch starts / ends, metres
    d1: float
    t0: float                  # session time at those points
    t1: float
    loss: float                # seconds lost over the stretch against the reference
    causes: list[str] = field(default_factory=list)
    where: str = ""
    desc: str = ""             # precise, with the measured numbers (shown in the plan)
    spoken: str = ""           # the same facts in everyday spoken units (what the narrator is given)

    def event(self, focus_id: int) -> Event:
        score = round(3.5 + 3.0 * self.loss, 2)
        return Event("mistake", self.t0, self.t1, [focus_id], score, self.spoken or self.desc, "exact", peak=self.t1,
                     label=self.where.upper()[:30])


def _stretches(lap: T.Trace, ref: T.Trace, min_loss: float, skip_before: float) -> list[tuple[float, float, float]]:
    """(from m, to m, seconds lost) for the stretches where `lap` falls behind `ref`, best first."""
    n = int(min(lap.length, ref.length) // GRID)
    if n < 8:
        return []
    delta = [lap.elapsed(i * GRID) - ref.elapsed(i * GRID) for i in range(n + 1)]
    w = int(WINDOW // GRID)
    cands = sorted(((delta[i + w] - delta[i], i) for i in range(n - w + 1) if i * GRID >= skip_before), reverse=True)
    picked: list[tuple[float, float]] = []
    for loss, i in cands:
        if loss < min_loss:
            break
        if all(abs(i * GRID - p) >= MIN_SEP for p, _ in picked):
            picked.append((i * GRID, loss))
    out = []
    for d0, _ in picked:
        a, b = int(d0 // GRID), int(d0 // GRID) + w
        for _ in range(2):                                   # take in the whole of the loss, a little either side
            if a > 0 and delta[a] - delta[a - 1] > 0.01:
                a -= 1
            if b < n and delta[b + 1] - delta[b] > 0.01:
                b += 1
        out.append((a * GRID, b * GRID, delta[b] - delta[a]))
    return out


def _first_at(tr: T.Trace, start: float, stop: float, pick) -> Optional[float]:
    for k in range(tr.idx(start), tr.idx(stop) + 1):
        if pick(tr.s[k]):
            return tr.s[k][0]
    return None


def causes(lap: T.Trace, ref: T.Trace, a: float, b: float, ref_label: str) -> tuple[list[str], list[str], float]:
    """The measured reasons a stretch lost time (possibly none), each once with its numbers and once in everyday spoken
    units, and the reference apex distance."""
    lo, hi = max(0.0, a - 100), b + 100
    apex = min(ref.s[ref.idx(lo):ref.idx(hi) + 1], key=lambda p: p[1])
    ad = apex[0]
    near_ref = ref.s[ref.idx(ad - 400):ref.idx(ad + 400) + 1]
    if max(p[1] for p in near_ref) - apex[1] < CORNER_DROP_KMH:      # no corner here: a straight, or a flat-out kink
        return [], [], ad
    # the reference's braking point: the start of the brake application that leads into the apex
    k, floor = ref.idx(ad), ref.idx(ad - 400)
    while k > floor and ref.s[k][2] < 20:
        k -= 1
    brake_d = None
    if ref.s[k][2] >= 20:
        while k > 0 and ref.s[k - 1][2] >= 5:
            k -= 1
        brake_d = ref.s[k][0]
    # speed compared PARTWAY into the braking zone: at the braking point both cars are still flat out, and an over-fast
    # entry only shows once the reference has started to slow
    mid = brake_d + 0.6 * (ad - brake_d) if brake_d is not None else None
    entry_dv = lap.at(mid)[1] - ref.at(mid)[1] if mid is not None else None
    # a lap that is simply slower everywhere (traffic, a lift) is slower at the corner too: only the shortfall BEYOND
    # what was already carried into the braking zone counts as a mistake of this corner
    carried = min(0.0, lap.at(brake_d)[1] - ref.at(brake_d)[1]) if brake_d is not None else 0.0
    near = lap.s[lap.idx(ad - 80):lap.idx(ad + 80) + 1] or [lap.at(ad)]
    apex_dv = min(p[1] for p in near) - apex[1]
    apex_rel = apex_dv - carried
    lat_dev = lap.at(ad)[4] - ref.at(ad)[4]
    over = max((abs(p[4]) - abs(p[5]) for p in lap.s[lap.idx(a - 50):lap.idx(b + 100) + 1]), default=0.0)
    exit_dv = lap.at(ad + 120)[1] - ref.at(ad + 120)[1]
    exit_rel = exit_dv - carried
    thr_lap = _first_at(lap, ad, ad + 400, lambda p: p[3] >= 80)
    thr_ref = _first_at(ref, ad, ad + 400, lambda p: p[3] >= 80)
    late = (thr_lap - thr_ref) if thr_lap is not None and thr_ref is not None else 0.0

    out: list[str] = []
    say: list[str] = []
    rs = ref_label.replace("your ", "the ")                     # "the best qualifying lap"
    deep = entry_dv is not None and entry_dv >= DEEP_ENTRY_KMH and apex_rel <= -APEX_SLOWER_KMH
    if deep:
        out.append(f"went in too deep ({entry_dv:.0f} km/h faster than {ref_label} into the braking zone, "
                   f"{-apex_dv:.0f} km/h slower at the apex)")
        say.append(f"came in far too hot, carrying {say_speed(entry_dv)} more than {rs} into the braking zone, "
                   f"and was {say_speed(apex_dv)} slower through the middle of the corner")
    elif entry_dv is not None and entry_dv - carried <= EARLY_ENTRY_KMH and apex_rel <= EARLY_APEX_KMH:
        out.append(f"braked too early ({-entry_dv:.0f} km/h slower than {ref_label} into the braking zone)")
        say.append(f"braked far too early, {say_speed(entry_dv)} slower than {rs} going into the braking zone")
    elif abs(lat_dev) >= LINE_METRES and apex_rel <= -APEX_SLOWER_KMH:
        out.append(f"missed the apex ({abs(lat_dev):.1f} metres off the line, {-apex_dv:.0f} km/h slower)")
        say.append(f"missed the apex, {say_across(lat_dev)} off the ideal line, and was {say_speed(apex_dv)} slower "
                   "through the middle of the corner")
    if over >= OVER_METRES:
        out.append(f"ran {over:.1f} metres past the track edge")
        say.append(f"ran {say_over(over)}")
    if exit_rel <= EXIT_KMH:
        out.append(f"had a poor exit ({-exit_dv:.0f} km/h down)")
        say.append(f"came out of the corner {say_speed(exit_dv)} down on {rs}")
    elif late >= THROTTLE_LATE_M:
        out.append(f"was late on the throttle ({late:.0f} metres later than {ref_label})")
        say.append(f"got back on the power {say_along(late)} later than {rs}")
    return out, say, ad


def analyze(race: T.Telemetry, ref: T.Trace, ref_label: str, track: str, min_loss: float = 0.25,
            max_lap_deficit: float = 8.0, apexes: Optional[list[float]] = None) -> list[Mistake]:
    best = ref.s[-1][6] - ref.s[0][6]
    apexes = corner_apexes(ref) if apexes is None else apexes
    found: list[Mistake] = []
    for lp in race.laps:
        if lp.time > best + max_lap_deficit:                 # a pit, a spin, a safety car: not a lap to compare
            continue
        tr = T.Trace(T.lap_trace(race, lp.start, lp.end))
        if not tr or tr.length < 0.9 * ref.length:
            continue
        if lp.n == 1:                                        # the opening lap: grid start and a packed field
            continue
        for a, b, loss in _stretches(tr, ref, min_loss, 0.0):
            why, said, ad = causes(tr, ref, a, b, ref_label)
            if not why:
                continue
            name = corner_label(track, ad, apexes)
            where = name or f"{round(ad, -1):.0f} metres into the lap"
            spoken = (f"On lap {_w(lp.n)}, at {name or say_lap_position(ad)}, the driver {_and(said)}. "
                      f"That cost {say_seconds(loss)}.")
            found.append(Mistake(lp.n, a, b, tr.s[tr.idx(a)][6], tr.s[tr.idx(b)][6], loss, why, where,
                                 f"lap {lp.n}, {where}: " + " and ".join(why) + f"; lost {loss:.1f} seconds", spoken))
    return found


def select(found: list[Mistake], limit: int, min_gap_s: float = 25.0, windows=()) -> list[Mistake]:
    """The worst ones, spread out, and not where an incident already tells the story (`windows`: (t0, t1) pairs)."""
    keep: list[Mistake] = []
    for m in sorted(found, key=lambda m: -m.loss):
        if len(keep) >= limit:
            break
        if any(a - 6 <= m.t1 and m.t0 <= b + 10 for a, b in windows):
            continue
        if all(abs(m.t1 - k.t1) >= min_gap_s for k in keep):
            keep.append(m)
    return sorted(keep, key=lambda m: m.t0)


def reference(folder: Path, track: str, driver: str, quali_best: float, race: T.Telemetry
              ) -> tuple[Optional[T.Trace], str]:
    """The reference lap: the best qualifying lap from the matching qualifying telemetry, else the best race lap."""
    if quali_best and quali_best > 0:
        f = T.find(folder, track, "Q", driver, lambda laps: any(abs(l.time - quali_best) < 0.002 for l in laps))
        if f:
            q = T.load(f)
            lp = min((l for l in q.laps if abs(l.time - quali_best) < 0.002), key=lambda l: abs(l.time - quali_best))
            tr = T.Trace(T.lap_trace(q, lp.start, lp.end))
            if tr:
                return tr, "your best qualifying lap"
    laps = [l for l in race.laps if l.n > 1]
    for lp in sorted(laps, key=lambda l: l.time):
        tr = T.Trace(T.lap_trace(race, lp.start, lp.end))
        if tr:
            return tr, "your best race lap"
    return None, ""


@dataclass
class Session:
    """The focus driver's own telemetry for one race, with the reference lap and the track's corners: it finds their
    mistakes and says which corner they were at at any moment of the race."""
    race: T.Telemetry
    ref: T.Trace
    ref_label: str
    track: str
    apexes: list[float]
    _laps: dict = field(default_factory=dict)

    def mistakes(self, min_loss: float, limit: int, windows=(), span=None) -> list[Mistake]:
        found = analyze(self.race, self.ref, self.ref_label, self.track, min_loss, apexes=self.apexes)
        if span:                                             # only what the video will show
            found = [m for m in found if m.t1 >= span[0] and m.t0 <= span[1]]
        return select(found, limit, windows=windows)

    def _lap_trace(self, lap):
        tr = self._laps.get(lap.n)
        if tr is None:
            tr = self._laps[lap.n] = T.Trace(T.lap_trace(self.race, lap.start, lap.end))
        times = self._laps.get(("times", lap.n))
        if times is None and tr:
            times = self._laps[("times", lap.n)] = [p[6] for p in tr.s]
        return tr, times

    def live(self, t: float) -> Optional[dict]:
        """What the on-screen readout needs at session time `t`: the corner the car is at ("" between corners) and how
        far ahead (negative) or behind (positive) the reference lap it is at this point of the lap, in seconds. None when
        `t` is not inside a timed lap."""
        import bisect
        lp = next((l for l in self.race.laps if l.start <= t <= l.end), None)
        if lp is None:
            return None
        tr, times = self._lap_trace(lp)
        if not tr or not times:
            return None
        k = min(len(times) - 1, bisect.bisect_left(times, t))
        dist = tr.s[k][0]
        return {"corner": corner_label(self.track, dist, self.apexes),
                "delta": round(times[k] - times[0] - self.ref.elapsed(dist), 2), "lap": lp.n,
                # what the gap is measured against: the qualifying lap when there is one, else the driver's best race lap
                "ref": "best quali" if "qualifying" in self.ref_label else "best lap"}

    def where(self, t: float) -> str:
        """The corner this driver's car was at at session time `t` ("the Bus Stop", "corner five"), or '' when it was
        between corners or `t` is not inside a timed lap."""
        import bisect
        lp = next((l for l in self.race.laps if l.start <= t <= l.end), None)
        if lp is None:
            return ""
        tr = self._laps.get(lp.n)
        if tr is None:
            tr = self._laps[lp.n] = T.Trace(T.lap_trace(self.race, lp.start, lp.end))
        if not tr:
            return ""
        times = [p[6] for p in tr.s]
        k = min(len(times) - 1, bisect.bisect_left(times, t))
        return corner_label(self.track, tr.s[k][0], self.apexes)


def open_session(folder: Optional[Path], track: str, driver: str, lap_times: list[float], quali_best: float
                 ) -> tuple[Optional[Session], str]:
    """(session, note). No session means no telemetry for this driver and race, and the note says why."""
    if not folder:
        return None, "no Telemetry folder next to the replays"
    want = [t for t in lap_times if t > 0]

    def is_this_race(laps: list[T.TimedLap]) -> bool:
        hits = sum(any(abs(l.time - t) < 0.002 for t in want) for l in laps)
        return len(laps) >= 2 and hits >= max(2, int(0.8 * len(laps)))

    f = T.find(folder, track, "R", driver, is_this_race)
    if not f:
        return None, f"no telemetry file for this race by {driver} (the game records only the player's own car)"
    race = T.load(f)
    ref, label = reference(folder, track, driver, quali_best, race)
    if not ref:
        return None, "no reference lap in the telemetry"
    return Session(race, ref, label, track, corner_apexes(ref)), f"compared with {label}"


def find_mistakes(folder: Optional[Path], track: str, driver: str, lap_times: list[float], quali_best: float,
                  min_loss: float, limit: int, windows=(), span=None) -> tuple[list[Mistake], str]:
    """(mistakes, note). The note says why there are none (no files, someone else drove, ...) or what was compared."""
    session, note = open_session(folder, track, driver, lap_times, quali_best)
    if session is None:
        return [], note
    return session.mistakes(min_loss, limit, windows, span), note
