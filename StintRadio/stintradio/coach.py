"""Corner-by-corner coaching while driving: as the car leaves each corner, compare it with the reference lap.

The corners are the slow points of the reference lap (driving.corner_apexes). For each corner the coach measures the
time gained or lost from the reference's braking point to the exit, where the driver started braking against the
reference, and the causes driving.causes() finds from measured numbers (too deep, braked too early, missed the apex,
poor exit, late on the throttle). Nothing is said without a number behind it."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from driverdirector import driving
from driverdirector.phrasing import w as words
from driverdirector.telemetry import Trace
from .laps import Lap

MIN_REPORT_S = 0.05        # smaller differences are noise
ORD = ["", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th"]
BRAKE_ON = 20.0            # % pedal that counts as braking


@dataclass
class Corner:
    n: int
    name: str
    entry: float           # metres: a little before the reference's braking point
    apex: float
    brake: Optional[float]  # the reference's braking point
    done_at: float         # judged once the car is this far round the lap
    lo: float = 0.0        # braking for this corner is looked for from here (just after the previous one)
    gear: Optional[int] = None   # the reference's gear at the slowest point of the corner (None: lap has no gears)
    throttle_at: Optional[float] = None   # where the reference gets back on the power after the apex (first 40 % throttle)
    brake_peak: Optional[float] = None    # the reference's hardest brake pedal in the braking zone, %
    side: int = 0          # +1 a left, -1 a right, 0 unknown or a kink (from the reference's heading)
    look: str = ""         # what the corner is like, for a driver who does not know the track: "slow right", "left hairpin"


WIDE_M = 2.0                         # metres off the reference line at the apex that count as off the line
SLOW_KMH, FAST_KMH = 90.0, 150.0     # apex speed: below is slow, above is fast, between is medium
HAIRPIN_DEG = 110.0                  # a turn of more than this is a hairpin
MIN_TURN_DEG = 20.0                  # less than this is a kink, described by its speed only


def turn_angle(lap: Lap, apex: float, turn_sign: int) -> Optional[float]:
    """The heading change round the apex in degrees, + = left, - = right (None without positions or a known handedness)."""
    s = lap.samples
    if not s or not turn_sign:
        return None
    near = lambda d: min(s, key=lambda q: abs(q[0] - d))
    a, b, c, d = near(apex - 110), near(apex - 40), near(apex + 40), near(apex + 110)
    if any(p[4] is None or p[5] is None for p in (a, b, c, d)):
        return None
    h1 = math.atan2(b[5] - a[5], b[4] - a[4])
    h2 = math.atan2(d[5] - c[5], d[4] - c[4])
    return math.degrees((h2 - h1 + math.pi) % (2 * math.pi) - math.pi) * turn_sign


def line_offset(lap: Lap, ref: Lap, d: float, side: int, turn_sign: int) -> Optional[float]:
    """How far `lap` is from `ref` across the track at lap distance d, in metres: + = on the inside of the turn
    (`side` +1 left, -1 right), - = outside. None without positions."""
    if not lap.samples or not ref.samples or not side or not turn_sign:
        return None
    near = lambda smp, x: min(smp, key=lambda q: abs(q[0] - x))
    p, r, r0, r1 = near(lap.samples, d), near(ref.samples, d), near(ref.samples, d - 12), near(ref.samples, d + 12)
    if any(q[4] is None or q[5] is None for q in (p, r, r0, r1)):
        return None
    hx, hz = r1[4] - r0[4], r1[5] - r0[5]
    norm = math.hypot(hx, hz)
    if norm < 1e-6:
        return None
    cross = (hx * (p[5] - r[5]) - hz * (p[4] - r[4])) / norm        # + = counter-clockwise of the reference's heading
    to_left = cross * turn_sign > 0                                  # in true left/right
    return abs(cross) * (1 if to_left == (side > 0) else -1)


def describe(lap: Lap, apex: float, turn_sign: int = 0) -> str:
    """"slow right", "fast left", "right hairpin" from the lap's apex speed and the turn of its heading round the apex.
    `turn_sign` says which way the game's world coordinates turn: +1 = a positive heading change (counter-clockwise in
    x/z) is a left, -1 = a right, 0 = unknown (then only the speed class is given). Without positions (Coach Dave laps)
    it is the speed class alone."""
    s = lap.samples
    if not s:
        return ""
    near = lambda d: min(s, key=lambda q: abs(q[0] - d))
    v = near(apex)[1]
    speed = "slow" if v < SLOW_KMH else ("fast" if v > FAST_KMH else "medium")
    a, b, c, d = near(apex - 110), near(apex - 40), near(apex + 40), near(apex + 110)
    if not turn_sign or any(p[4] is None or p[5] is None for p in (a, b, c, d)):
        return f"{speed} corner"
    h1 = math.atan2(b[5] - a[5], b[4] - a[4])
    h2 = math.atan2(d[5] - c[5], d[4] - c[4])
    ang = math.degrees((h2 - h1 + math.pi) % (2 * math.pi) - math.pi) * turn_sign
    if abs(ang) < MIN_TURN_DEG:
        return f"{speed} kink"
    side = "left" if ang > 0 else "right"
    return f"{side} hairpin" if abs(ang) > HAIRPIN_DEG else f"{speed} {side}"


@dataclass
class Note:
    lap: int
    corner: str
    delta: float           # seconds, + = lost against the reference
    brake_diff: Optional[float]   # metres, + = braked later than the reference
    causes: list
    text: str
    n: int = 0                       # corner number
    my_gear: Optional[int] = None    # the gear the driver used at the slowest point
    ref_gear: Optional[int] = None   # ... and the reference
    held: Optional[bool] = None      # where the reference short-shifts: True = the driver revved the gear out instead
    short_to: Optional[int] = None   # the gear the reference short-shifts into there
    brake_peak: Optional[float] = None   # the driver's hardest brake pedal in this corner's braking zone, %
    ref_peak: Optional[float] = None     # ... and the reference's
    throttle_diff: Optional[float] = None   # metres the driver got back on the power after the reference (+ = later)
    throttle_s: Optional[float] = None      # ... and about how many seconds that is
    entry_off: Optional[float] = None       # metres from the reference line before the corner, + = inside of the turn
    apex_off: Optional[float] = None        # ... at the apex (negative = wide, outside the reference)
    exit_off: Optional[float] = None        # ... and on the way out
    report: bool = True              # worth showing on its own (False: kept only for the pattern finder)


def brake_point(tr: Trace, apex: float, lo: float) -> Optional[float]:
    """Where the braking zone into `apex` starts: the FIRST firm application (BRAKE_ON) between `lo` (just after the
    previous corner) and the apex, walked back to where the pedal first went down. A light touch late in the zone
    (trail braking) is not the braking point."""
    if not tr:
        return None
    k, end = tr.idx(lo), tr.idx(apex)
    while k < end and tr.s[k][2] < BRAKE_ON:
        k += 1
    if k >= end:
        return None
    floor = tr.idx(lo)
    while k > floor and tr.s[k - 1][2] >= 5:
        k -= 1
    return tr.s[k][0]


def peak_brake(tr: Trace, lo: float, hi: float) -> Optional[float]:
    """The hardest brake pedal (%) between two lap distances."""
    seg = tr.s[tr.idx(lo):tr.idx(hi) + 1]
    return max((p[2] for p in seg), default=None) if seg else None


def throttle_pickup(tr: Trace, apex: float, back: float = 150.0, limit: float = 400.0, level: float = 15.0) -> Optional[float]:
    """Where the car starts getting back on the power for the corner: the start of the throttle application that is
    running at the slowest point (found by walking back to where the pedal was last below `level` %). If the pedal was
    down the whole way back (a kink), or is still off `limit` metres after the apex, there is no pick-up point."""
    k, floor = tr.idx(apex), tr.idx(max(0.0, apex - back))
    while k > floor and tr.s[k][3] >= level:
        k -= 1
    if tr.s[k][3] >= level:
        return None
    while k < tr.idx(apex + limit) and k + 1 < len(tr.s):
        k += 1
        if tr.s[k][3] >= level:
            return tr.s[k][0]
    return None


def apex_gear(lap: Lap, apex: float, around: float = 30.0) -> Optional[int]:
    """The lowest forward gear the lap used within `around` metres of the corner's slowest point."""
    gears = [p[7] for p in lap.samples if len(p) > 7 and abs(p[0] - apex) <= around and p[7] and p[7] > 0]
    return min(gears) if gears else None


@dataclass
class Shift:
    dist: float            # metres: where the reference changed up
    from_gear: int
    to_gear: int
    rpm: int               # the reference's revs at the change
    usual: int             # the reference's usual upshift revs (the short shift is this much below)


SHORT_SHIFT_FRACTION = 0.94    # an upshift below 94% of the lap's usual shift revs is a short shift
EXIT_M = 350.0                 # ... when it comes this soon after a corner's slowest point (for traction)


def upshifts(lap: Lap) -> list[tuple[float, int, int, int]]:
    """(dist, from, to, revs just before the change) for every upshift. Gear reads 0 for a moment mid-change in some
    sources, so a change is measured from the last real gear."""
    s, out, last, last_i = lap.samples, [], 0, 0
    if not s or len(s[0]) < 9:
        return out
    for i, p in enumerate(s):
        g = p[7]
        if not g or g <= 0:
            continue
        if last and g > last:
            out.append((p[0], last, g, max(q[8] for q in s[max(0, last_i - 8):last_i + 1])))
        last, last_i = g, i
    return out


def short_shifts(lap: Lap, apexes: list[float]) -> list[Shift]:
    ups = upshifts(lap)
    if len(ups) < 4:
        return []
    revs = sorted(u[3] for u in ups)
    usual = revs[len(revs) // 2]
    out = []
    for d, a, b, rpm in ups:
        after = [d - x for x in apexes if 0 <= d - x <= EXIT_M]
        if after and rpm < SHORT_SHIFT_FRACTION * usual:
            out.append(Shift(d, a, b, int(rpm), int(usual)))
    return out


class Coach:
    def __init__(self, ref: Lap, track: str, track_len: float, turn_sign: int = 0):
        self.ref = ref.scaled(track_len)
        self.turn_sign = turn_sign
        self.track = track
        self.rt = self.ref.trace()
        apexes = driving.corner_apexes(self.rt) if self.rt else []
        self.corners: list[Corner] = []
        for i, a in enumerate(apexes):
            lo = max(a - 400.0, apexes[i - 1] + 60 if i else 0.0)
            b = brake_point(self.rt, a, lo)
            entry = (b if b is not None else a - 150) - 60
            nxt = apexes[i + 1] - 250 if i + 1 < len(apexes) else self.rt.length - 20
            done = max(a + 150, min(a + 400, nxt))
            name = driving.corner_label(track, a, apexes) or f"corner {words(i + 1)}"
            self.corners.append(Corner(i + 1, name, max(0.0, entry), a, b, done, lo, apex_gear(self.ref, a),
                                       throttle_at=throttle_pickup(self.rt, a),
                                       brake_peak=peak_brake(self.rt, b, a) if b is not None else None,
                                       look=describe(self.ref, a, turn_sign), side=self._side(a)))
        self.shifts = short_shifts(self.ref, apexes)

    def _side(self, apex: float) -> int:
        ang = turn_angle(self.ref, apex, self.turn_sign)
        return 0 if ang is None or abs(ang) < MIN_TURN_DEG else (1 if ang > 0 else -1)

    def ref_elapsed(self, d: float) -> float:
        return self.rt.elapsed(d) if self.rt else 0.0

    def delta(self, lap: Lap) -> Optional[float]:
        """Seconds behind (+) or ahead (-) of the reference at the car's current distance."""
        if not self.rt or len(lap.samples) < 20:
            return None
        d, t = lap.samples[-1][0], lap.samples[-1][6]
        return t - self.ref_elapsed(d)

    def judge(self, lap: Lap, c: Corner, lap_no: int) -> Optional[Note]:
        tr = lap.trace(self.ref)
        if not tr or tr.s[0][0] > c.entry or tr.length < c.done_at:
            return None
        loss = (tr.elapsed(c.done_at) - self.rt.elapsed(c.done_at)) - (tr.elapsed(c.entry) - self.rt.elapsed(c.entry))
        mine = brake_point(tr, c.apex, c.lo)
        bdiff = (mine - c.brake) if mine is not None and c.brake is not None else None
        out, _, _ = driving.causes(tr, self.rt, c.entry, c.done_at, "your best lap")
        report = not (abs(loss) < MIN_REPORT_S and not out and
                      not any(c.apex <= sh.dist <= c.done_at + 50 for sh in self.shifts))
        report = report or abs(line_offset(lap, self.ref, c.apex, c.side, self.turn_sign) or 0.0) >= WIDE_M
        parts = []
        if bdiff is not None and abs(bdiff) >= 5:
            parts.append(f"braked {abs(bdiff):.0f} m {'later' if bdiff > 0 else 'earlier'}")
        if loss > 0:                                   # a cause only explains time lost, never time gained
            parts += [o.split(" (")[0] for o in out]
        held, short_to = None, None
        for sh in self.shifts:                          # held a gear the reference short-shifts out of
            if c.apex <= sh.dist <= c.done_at + 50:
                short_to = sh.to_gear
                mine = [u for u in upshifts(lap) if u[1] == sh.from_gear and abs(u[0] - sh.dist) <= 150]
                held = bool(mine and mine[0][3] > sh.rpm + 300)
                if held:
                    parts.append(f"held {ORD[sh.from_gear]} to {mine[0][3]} rpm, short-shift at {sh.rpm}")
        my_peak = peak_brake(tr, mine if mine is not None else c.brake, c.apex) if c.brake is not None else None
        pick = throttle_pickup(tr, c.apex) if c.throttle_at is not None else None
        thr_diff = (pick - c.throttle_at) if pick is not None else None
        thr_s = thr_diff / max(tr.at(c.apex)[1] / 3.6, 8.0) if thr_diff is not None else None
        offs = [line_offset(lap, self.ref, c.apex + dx, c.side, self.turn_sign) for dx in (-60, 0, 80)]
        sign = "+" if loss >= 0 else "-"
        text = f"{c.name.capitalize()}{f' ({c.look})' if c.look else ''}: {sign}{abs(loss):.2f}s" + (f" - {', '.join(parts)}" if parts else "")
        return Note(lap_no, c.name, round(loss, 3), round(bdiff, 1) if bdiff is not None else None, out, text,
                    n=c.n, my_gear=apex_gear(lap, c.apex), ref_gear=c.gear, held=held, short_to=short_to, brake_peak=my_peak, ref_peak=c.brake_peak,
                    throttle_diff=round(thr_diff, 1) if thr_diff is not None else None,
                    throttle_s=round(thr_s, 2) if thr_s is not None else None,
                    entry_off=offs[0], apex_off=offs[1], exit_off=offs[2], report=report)
