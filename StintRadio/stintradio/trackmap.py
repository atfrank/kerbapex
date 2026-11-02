"""The track map on the race page: a lap's world x/z resampled to even steps of lap distance, so that any car (the game
gives the others' lap distance, not their position) can be placed on the drawing from its lap distance alone."""
from __future__ import annotations

from typing import Optional

POINTS = 400           # steps round a whole lap
MIN_SAMPLES = 20       # fewer than this is not a line yet
MIN_SPAN_M = 50.0      # a lap that never moved this far (replays of laps with no positions read 0, 0) draws nothing


def has_positions(samples: list) -> bool:
    """Coach Dave laps carry no world position."""
    return bool(samples) and samples[0][4] is not None and samples[0][5] is not None


def outline(samples: list, track_len: float = 0.0, n: int = POINTS, turn_sign: int = 0) -> Optional[dict]:
    """{"pts": [[x, y], ...], "from", "to", "closed"} from samples [dist, kmh, brake, throttle, x, z, ...], with y up the
    page. A whole lap gives n points at distances k * to / n, joined back to the first; part of a lap gives n points
    from its first distance to its last. `turn_sign` is the reader's: -1 = the game's x/z turn the other way, so x is
    flipped to keep left turns turning left on the page. None when there is nothing to draw yet."""
    pts, top = [], float("-inf")
    for p in samples:
        if p[4] is None or p[5] is None or p[0] <= top:           # lap distance can step back between scoring updates
            continue
        pts.append((p[0], p[4], p[5]))
        top = p[0]
    if len(pts) < MIN_SAMPLES:
        return None
    xs, zs = [p[1] for p in pts], [p[2] for p in pts]
    if max(max(xs) - min(xs), max(zs) - min(zs)) < MIN_SPAN_M:
        return None
    length = track_len if track_len > 0 else pts[-1][0]
    closed = pts[0][0] <= 0.05 * length and pts[-1][0] >= 0.95 * length
    if closed:
        lo, hi = 0.0, length
        wanted = [k * length / n for k in range(n)]
    else:
        lo, hi = pts[0][0], pts[-1][0]
        wanted = [lo + k * (hi - lo) / (n - 1) for k in range(n)]
    flip = -1 if turn_sign < 0 else 1
    out, i = [], 0
    for d in wanted:
        while i + 1 < len(pts) and pts[i + 1][0] <= d:
            i += 1
        a, b = pts[i], pts[min(i + 1, len(pts) - 1)]
        f = (d - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
        f = max(0.0, min(1.0, f))
        out.append([round(flip * (a[1] + f * (b[1] - a[1])), 1), round(a[2] + f * (b[2] - a[2]), 1)])
    return {"pts": out, "from": round(lo, 1), "to": round(hi, 1), "closed": closed}


def braking_zones(corners: list) -> list[dict]:
    """Where each of the reference's braking zones runs, from its braking point to the apex (a corner taken without
    braking has none)."""
    return [{"from": round(c.brake, 1), "to": round(c.apex, 1), "n": c.n, "name": c.name, "look": c.look or "", "gear": c.gear}
            for c in corners if c.brake is not None]


def around(snap: Optional[dict]) -> list[dict]:
    """The other cars for the map: lap distance, short name, place, whether directly ahead (-1) or behind (+1) in the
    order, in the pits or not."""
    if not snap:
        return []
    cars = sorted(snap["cars"], key=lambda c: c["place"])
    mine = next((i for i, c in enumerate(cars) if c["me"]), None)
    out = []
    for i, c in enumerate(cars):
        if c["me"] or c.get("dist", -1) < 0:
            continue
        side = (i - mine) if mine is not None and abs(i - mine) == 1 else 0
        out.append({"d": round(c["dist"], 1), "n": c.get("name", "").split("#")[0].strip(), "p": c["place"], "side": side,
                    "pit": bool(c.get("in_pits"))})
    return out
