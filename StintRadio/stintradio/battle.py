"""Where the time goes against the car directly ahead.

Cut the lap into segments and time both cars through each one: when a car crosses a segment boundary is found by
interpolating its lap distance, read once a second. The difference between the two times for the same stretch of track,
lap after lap, is what that car is taking (or giving back) there. (The game's own time gap cannot be used: it is a
distance divided by your current speed, so it swings with your own braking.) Only the same car counts: when the car ahead
changes the record starts again, and a segment needs a couple of laps showing the same thing before it is talked about."""
from __future__ import annotations

from collections import deque
from typing import Optional

SEGMENTS = 12             # a segment is about a twelfth of the lap, 8 seconds of a minute-and-a-half lap
KEEP = 4                  # laps remembered per segment
MIN_LAPS = 2              # laps a segment must have before it is talked about
MAX_SEG_S = 60.0          # a segment taking longer than this (a spin, the pits) is thrown away


class _Timer:
    """When one car crosses each segment boundary, and how long each whole segment took."""
    def __init__(self) -> None:
        self.prev: Optional[tuple] = None          # (t, cumulative distance)
        self.cross: dict[int, float] = {}          # boundary number -> time
        self.times: dict[int, float] = {}          # segment number (absolute) -> seconds

    def add(self, t: float, cum: float, seg_len: float) -> None:
        if self.prev is not None:
            t0, c0 = self.prev
            if cum <= c0 or t <= t0:
                self.prev = (t, cum)
                return
            first, last = int(c0 // seg_len) + 1, int(cum // seg_len)
            for n in range(first, last + 1):
                tc = t0 + (n * seg_len - c0) / (cum - c0) * (t - t0)
                self.cross[n] = tc
                if n - 1 in self.cross and 0 < tc - self.cross[n - 1] < MAX_SEG_S:
                    self.times[n - 1] = tc - self.cross[n - 1]
                self.cross.pop(n - 3, None)
        self.prev = (t, cum)


class Battle:
    def __init__(self) -> None:
        self.name: Optional[str] = None
        self.rel: dict[int, deque] = {}            # segment (0..SEGMENTS-1) -> seconds I am slower than the car ahead, per lap
        self._me, self._ahead = _Timer(), _Timer()
        self._done: set[int] = set()
        self._laps_said = -99

    def reset(self) -> None:
        self.rel.clear()
        self._me, self._ahead = _Timer(), _Timer()
        self._done.clear()

    def update(self, t: float, me: tuple, ahead: Optional[tuple], name: Optional[str], track_len: float) -> None:
        """One reading: the time, my (completed laps, lap distance), the car ahead's, who that is, and the lap length."""
        if ahead is None or not name or not track_len:
            return
        if name != self.name:
            self.name = name
            self.reset()
        seg_len = track_len / SEGMENTS
        self._me.add(t, me[0] * track_len + me[1], seg_len)
        self._ahead.add(t, ahead[0] * track_len + ahead[1], seg_len)
        for n in sorted(set(self._me.times) & set(self._ahead.times)):
            if n in self._done:
                continue
            self._done.add(n)
            self.rel.setdefault(n % SEGMENTS, deque(maxlen=KEEP)).append(self._me.times[n] - self._ahead.times[n])
        for n in [n for n in self._done if n < min(self._me.times, default=0) - 3 * SEGMENTS]:
            self._done.discard(n)

    def per_segment(self) -> dict[int, float]:
        """Mean seconds per lap the car ahead takes off me in each segment (negative = I am quicker there)."""
        return {s: sum(d) / len(d) for s, d in self.rel.items() if len(d) >= MIN_LAPS}

    def summary(self) -> Optional[dict]:
        """The segment losing most, the one gaining most, and the net change per lap. None until there is data."""
        per = self.per_segment()
        if len(per) < SEGMENTS - 2:
            return None
        worst, best = max(per, key=per.get), min(per, key=per.get)
        return {"name": self.name, "net": sum(per.values()), "worst": worst, "loss": per[worst],
                "best": best, "gain": -per[best]}

    def due(self, lap: int, every: int = 2) -> bool:
        """True (and noted) at most every `every` laps."""
        if lap - self._laps_said < every:
            return False
        self._laps_said = lap
        return True


def describe(seg: int, track_len: float, corners: list) -> str:
    """A segment in the driver's words: the corner(s) in it, else the straight after the last corner, else the sector."""
    a, b = seg * track_len / SEGMENTS, (seg + 1) * track_len / SEGMENTS
    inside = [c for c in corners if a <= c.apex < b]
    if inside:
        names = [c.name.replace("corner", "turn") for c in inside[:2]]
        return names[0] if len(names) == 1 else f"{names[0]} and {names[1].replace('turn ', '')}"
    before = [c for c in corners if c.apex < a]
    if before:
        return f"the run after {max(before, key=lambda c: c.apex).name.replace('corner', 'turn')}"
    return f"sector {1 + int(3 * seg / SEGMENTS)}"
