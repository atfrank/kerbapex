"""Lookups the engineer can ask for while answering a question, read-only, on the laps the driver has saved.

The Claude CLI call is a single prompt with its own tools switched off, so the lookups are done here: Claude answers
with {"tool": "<name>", ...} instead of a reply, this module runs it against the live service and the stored laps, and
the result goes back into the next prompt (at most MAX_ROUNDS times). Nothing here changes any data or reaches out of the
program."""
from __future__ import annotations

import json
import math
from typing import Optional

from . import laps as LP

MAX_ROUNDS = 2
MAX_CHARS = 1400

DESCRIPTION = (
    'To look something up, answer with ONE JSON object {"tool": "<name>", ...} instead of a reply. Tools:\n'
    '- {"tool":"corner","n":5}: everything about turn n: the reference lap there and the driver\'s last laps.\n'
    '- {"tool":"lap_at","lap":"best|last|<lap number>","at_m":1500,"window_m":60}: what the car did around that lap '
    'distance (speed, gears, brake, throttle, steering, g-forces, time taken), and the same for the reference lap.\n'
    '- {"tool":"compare","a":"last","b":"best","from_m":0,"to_m":4000}: the time difference and speeds of two laps '
    "between two lap distances (use turn distances from the CORNERS facts).\n"
    '- {"tool":"laps"}: the driver\'s saved laps for this track and car, fastest first, and this session\'s laps.\n'
    "Lap distances are metres from the start line. Use at most two lookups, then reply. Prefer the facts when they already "
    "contain the answer."
)


def _lap(svc, which) -> Optional[LP.Lap]:
    """"best"/"reference", "last", or a lap number of this session."""
    which = str(which).lower().strip()
    recent = getattr(svc, "recent_laps", {})
    if which in ("best", "reference", "ref", "fastest"):
        c = getattr(svc, "coach", None)
        return c.ref if c is not None else None
    if which in ("last", "previous"):
        return recent[max(recent)] if recent else None
    try:
        return recent.get(int(which))
    except ValueError:
        return None


def _window(lap: LP.Lap, at: float, win: float) -> list:
    return [p for p in lap.samples if abs(p[0] - at) <= win]


def _summ(pts: list) -> str:
    if not pts:
        return "no data there"
    sp = [p[1] for p in pts]
    out = [f"speed {min(sp):.0f} to {max(sp):.0f} km/h (average {sum(sp) / len(sp):.0f})",
           f"brake peak {max(p[2] for p in pts):.0f} percent", f"throttle average {sum(p[3] for p in pts) / len(pts):.0f} percent"]
    if len(pts[0]) > 7:
        g = [p[7] for p in pts if p[7] and p[7] > 0]
        if g:
            out.append(f"gear {min(g)}" + (f" to {max(g)}" if max(g) != min(g) else ""))
    if len(pts[0]) > 9:
        out.append(f"steering up to {max(abs(p[9]) for p in pts):.2f} of full lock")
        out.append(f"sideways g up to {max(abs(p[11]) for p in pts) / 9.81:.1f}")
    out.append(f"{pts[-1][6] - pts[0][6]:.2f} s to cover {pts[-1][0] - pts[0][0]:.0f} m")
    return ", ".join(out)


def _at(lap: LP.Lap, d: float) -> float:
    return min(lap.samples, key=lambda p: abs(p[0] - d))[6]


def run(svc, req: dict) -> str:
    """The result of one lookup as plain text for the next prompt (an explanation when it cannot be done)."""
    try:
        name = str(req.get("tool", ""))
        if name == "corner":
            from .ask import corner_line
            coach = getattr(svc, "coach", None)
            n = int(req.get("n", 0))
            c = next((c for c in coach.corners if c.n == n), None) if coach else None
            if c is None:
                return f"There is no turn {n} on the reference lap."
            hist = (getattr(getattr(svc, "engineer", None), "history", {}) or {}).get(n, [])
            lines = [corner_line(coach, c, hist[-1:])]
            for h in hist[-3:-1]:
                lines.append(f"Earlier lap {h.lap}: gear {h.my_gear}, delta {h.delta:+.2f} s.")
            return "\n".join(lines)[:MAX_CHARS]
        if name == "lap_at":
            at, win = float(req.get("at_m", 0)), min(float(req.get("window_m", 60)), 400.0)
            lap = _lap(svc, req.get("lap", "last"))
            ref = _lap(svc, "best")
            if lap is None:
                return "That lap is not available (laps are kept for this session only)."
            out = [f"Lap {req.get('lap')} around {at:.0f} m: {_summ(_window(lap, at, win))}."]
            if ref is not None and ref is not lap:
                out.append(f"Reference lap there: {_summ(_window(ref, at, win))}.")
            return "\n".join(out)[:MAX_CHARS]
        if name == "compare":
            a, b = _lap(svc, req.get("a", "last")), _lap(svc, req.get("b", "best"))
            if a is None or b is None:
                return "One of those laps is not available."
            lo, hi = float(req.get("from_m", 0)), float(req.get("to_m", 1e9))
            hi = min(hi, a.samples[-1][0], b.samples[-1][0])
            ta, tb = _at(a, hi) - _at(a, lo), _at(b, hi) - _at(b, lo)
            return (f"Between {lo:.0f} m and {hi:.0f} m lap {req.get('a')} took {ta:.2f} s and lap {req.get('b')} took "
                    f"{tb:.2f} s, so lap {req.get('a')} was {ta - tb:+.2f} s. Lap {req.get('a')}: "
                    f"{_summ(_window(a, (lo + hi) / 2, (hi - lo) / 2))}. Lap {req.get('b')}: "
                    f"{_summ(_window(b, (lo + hi) / 2, (hi - lo) / 2))}.")[:MAX_CHARS]
        if name == "laps":
            s = getattr(svc, "sample", None)
            parts = []
            if s is not None:
                stored = sorted(float(p.stem.split("_")[1]) for p in LP.stored(s.track, s.car) if p.stem.startswith("live_"))
                parts.append("Saved laps, fastest first: " + ", ".join(f"{t:.3f}" for t in stored[:10]) + ".")
            log = getattr(svc, "lap_log", [])
            if log:
                parts.append("This session: " + ", ".join(f"lap {n} {t:.3f}" for n, t in log[-10:]) + ".")
            return " ".join(parts) or "No laps yet."
        return f"Unknown tool {name!r}."
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, ZeroDivisionError) as e:
        return f"That lookup failed ({type(e).__name__})."


def parse_request(raw: str) -> Optional[dict]:
    """A tool request in Claude's answer, or None when it is a reply."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        d = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) and "tool" in d and "reply" not in d else None
