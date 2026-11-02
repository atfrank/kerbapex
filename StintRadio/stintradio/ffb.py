"""Force-feedback advice: watch how hard the wheel is driven and say whether the strength looks too high (the output clips
at full scale and the detail in the heavy corners is lost) or too low (the forces never get near it).

RaceRoom's live data gives the force-feedback output as a fraction of full scale (1.0 = the device's limit, beyond that
the wheel clips). Nothing here changes a game file: the engineer says what to try and the driver sets it in the game."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from driverdirector.phrasing import w as words

from .engineer import Msg

MIN_SPEED = 40.0        # km/h: slower than this the wheel forces say nothing about the setting
MIN_SAMPLES = 1500      # about half a minute of driving at 50 readings a second
KEEP_LAPS = 3           # judged over the last few laps
CLIP_AT = 1.0
CLIPPING_BAD = 0.08     # clipping this much of the time: too strong
CLIPPING_SOME = 0.02
WEAK_P99 = 0.55         # the strongest 1 % of the forces only reach this: too weak
TARGET_P99 = 0.90


def percentile(sorted_vals: list, q: float) -> float:
    if not sorted_vals:
        return 0.0
    k = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


@dataclass
class Verdict:
    kind: str                 # strong | slightly_strong | weak | good
    clip: float               # fraction of the time at or beyond full scale
    p99: float                # the strongest 1 % of the forces, as a fraction of full scale
    scale: float              # change the strength to this fraction of its current value (1.0 = leave it)


def judge(vals: list) -> Optional[Verdict]:
    """The verdict on a list of |force| readings (fractions of full scale), or None if there are too few."""
    if len(vals) < MIN_SAMPLES:
        return None
    v = sorted(vals)
    clip = sum(1 for x in v if x >= CLIP_AT) / len(v)
    p99 = percentile(v, 0.99)
    if clip >= CLIPPING_BAD:
        return Verdict("strong", clip, p99, round(max(0.5, min(0.95, TARGET_P99 / max(p99, 1.0))), 2))
    if clip >= CLIPPING_SOME:
        return Verdict("slightly_strong", clip, p99, 0.9)
    if p99 < WEAK_P99:
        return Verdict("weak", clip, p99, round(min(1.6, TARGET_P99 / max(p99, 0.2)), 2))
    return Verdict("good", clip, p99, 1.0)


@dataclass
class FFBWatch:
    laps: list = field(default_factory=list)       # |force| readings of the last few laps
    _cur: list = field(default_factory=list)
    last: Optional[Verdict] = None
    _said: Optional[str] = None                     # the kind of the advice given last
    _since: int = 0                                 # laps since it was given

    def add(self, speed_kmh: float, force: float) -> None:
        """One reading while driving. Forces beyond ten times full scale are a glitch, not a setting problem."""
        if speed_kmh >= MIN_SPEED and abs(force) < 10:
            self._cur.append(abs(force))

    def stats(self) -> Optional[dict]:
        v = judge([x for lap in self.laps + [self._cur] for x in lap])
        return None if v is None else {"verdict": v.kind, "clipping_percent": round(100 * v.clip, 1),
                                       "strongest_percent": round(100 * v.p99), "scale": v.scale}

    def lap_done(self, wording=None) -> list:
        """At the end of a lap: the advice, if there is enough data and it is new (or still unfixed after a few laps)."""
        self.laps = (self.laps + [self._cur])[-KEEP_LAPS:]
        self._cur = []
        self._since += 1
        v = judge([x for lap in self.laps for x in lap])
        if v is None:
            return []
        self.last = v
        if v.kind == "good":
            out = [] if self._said in (None, "good") else [Msg(_pick(wording, "Force feedback looks well set now.",
                                                                       "That force feedback setting looks right."),
                                                             priority=2, expires=30.0, kind="ffb")]
            self._said = "good"
            return out
        if v.kind == self._said and self._since < 4:        # said it already: not again until a few laps have passed
            return []
        self._said, self._since = v.kind, 0
        return [Msg(self.advice(v, wording), priority=2, expires=30.0, kind="ffb")]

    @staticmethod
    def advice(v: Verdict, wording=None) -> str:
        pct = words(int(round(100 * v.clip)))
        change = words(int(round(abs(1 - v.scale) * 100 / 5) * 5))
        if v.kind == "strong":
            return _pick(wording,
                         f"Your force feedback is clipping about {pct} percent of the time. Take the strength down by about "
                         f"{change} percent, you're losing detail in the heavy corners.",
                         f"The wheel is hitting its limit {pct} percent of the time. Lower the force feedback strength by "
                         f"around {change} percent and you'll feel more.",
                         f"Force feedback is too strong, it clips {pct} percent of the time. Try {change} percent less.")
        if v.kind == "slightly_strong":
            return _pick(wording,
                         f"Force feedback clips a little, about {pct} percent of the time. A small cut in strength, "
                         "five or ten percent, would keep the detail.",
                         f"You're touching the force feedback limit now and then, {pct} percent of the time. "
                         "Take the strength down a touch.")
        return _pick(wording,
                     f"The force feedback is quite light, the strongest forces only reach {words(int(round(100 * v.p99)))} "
                     f"percent of full. You could raise the strength by about {change} percent.",
                     f"You've got room on the force feedback, it peaks around {words(int(round(100 * v.p99)))} percent. "
                     f"Try raising the strength by {change} percent.")


def _pick(wording, *options: str) -> str:
    """Choose a wording with the engineer's no-repeat picker when one is given."""
    return wording(*options) if wording is not None else options[0]
