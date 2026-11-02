"""Try-and-compare experiments during a session: the engineer suggests a change (or you make one yourself), the game's own
setting values show it was made, and the laps before and after are compared on lap time and wheel lock-ups.

Nothing here touches the game: it only reads the settings (brake bias, ABS, traction control, engine map, engine braking)
and the recorded laps. The comparison is honest about its limits: a couple of laps in traffic prove little, and it says so."""
from __future__ import annotations

import statistics
from typing import Optional

from . import tips

NAMES = {"brake_bias": "brake bias", "abs": "ABS", "tc": "traction control", "engine_map": "engine map",
         "engine_brake": "engine braking"}
SETTLE_S = 2.0            # a setting that stays put this long after the last click counts as the new value
BASE_LAPS = 3             # clean laps before the change that make the baseline
TEST_LAPS = 2             # clean laps after it that are judged
FASTER, SLOWER = 0.10, 0.15      # seconds a lap that count as a real change in pace


class Watcher:
    """Notices a setting change: the value moved and then stayed put for a couple of seconds."""
    def __init__(self) -> None:
        self.stable: dict[str, float] = {}
        self.cand: dict[str, tuple] = {}              # name -> (value, since)

    @staticmethod
    def _same(a: float, b: float) -> bool:
        return abs(a - b) < 0.0005

    def update(self, vals: dict, now: float) -> list[tuple]:
        """Feed the current settings; returns [(name, old, new)] for changes that have settled."""
        out = []
        for name, v in vals.items():
            if v is None or v < 0:
                continue
            if name not in self.stable:
                self.stable[name] = v
                continue
            if self._same(v, self.stable[name]):
                self.cand.pop(name, None)
                continue
            c = self.cand.get(name)
            if c is None or not self._same(c[0], v):
                self.cand[name] = (v, now)
            elif now - c[1] >= SETTLE_S:
                out.append((name, self.stable[name], v))
                self.stable[name] = v
                self.cand.pop(name, None)
        return out


class Experiment:
    def __init__(self, name: str, old: float, new: float, lap: int) -> None:
        self.name, self.old, self.new = name, old, new
        self.base_lap = lap                           # the first change's lap: the baseline is the clean laps before it
        self.lap = lap                                # the lap the latest change was made in: it is not judged

    def change(self, new: float, lap: int) -> None:
        self.new, self.lap = new, lap

    @property
    def label(self) -> str:
        return NAMES.get(self.name, self.name)

    def ready(self, completed: int) -> bool:
        """`lap` is the number of laps completed when the change was made (the lap being driven then is not judged)."""
        return completed >= self.lap + TEST_LAPS + 1     # the test laps have all been driven to the line


def _per_lap(group: dict, base: Optional[list]) -> dict:
    lock = tips.lock_counts(group["frames"], base) if base else {"zones": 0, "front": 0, "rear": 0}
    return {"time": group["time"], **lock}


def evaluate(frames: list[dict], exp: Experiment) -> dict:
    """Before and after. Lap times use the clean laps only; lock-ups are counted per lap against the whole session's
    wheel-speed calibration."""
    laps = tips.lap_groups(frames)
    base = tips.calibration(frames)
    before = [k for k in sorted(laps) if k < exp.base_lap and laps[k]["clean"]][-BASE_LAPS:]
    after = [k for k in sorted(laps) if k > exp.lap and laps[k]["clean"]][:TEST_LAPS]
    if not after:
        return {"ok": False, "why": "no clean laps since the change"}
    if not before:
        return {"ok": False, "why": "no clean lap before the change to compare with"}
    b = [_per_lap(laps[k], base) for k in before]
    a = [_per_lap(laps[k], base) for k in after]
    mean = lambda rows, key: sum(r[key] for r in rows) / len(rows)
    spread = max(r["time"] for r in b + a) - min(r["time"] for r in b + a)
    return {"ok": True, "n_before": len(b), "n_after": len(a),
            "d_avg": mean(a, "time") - mean(b, "time"), "d_best": min(r["time"] for r in a) - min(r["time"] for r in b),
            "front_before": mean(b, "front"), "front_after": mean(a, "front"),
            "rear_before": mean(b, "rear"), "rear_after": mean(a, "rear"),
            "locks_known": base is not None and all(r["zones"] >= 3 for r in b + a),
            "noisy": spread > 1.2, "after_laps": after, "before_laps": before}


def verdict(r: dict) -> str:
    """keep | revert | mixed | same"""
    d = r["d_avg"]
    faster, slower = d <= -FASTER, d >= SLOWER
    locks = 0.0
    if r.get("locks_known"):
        locks = (r["front_after"] + r["rear_after"]) - (r["front_before"] + r["rear_before"])
    better, worse = locks <= -1, locks >= 1
    if faster and not worse:
        return "keep"
    if slower and not better:
        return "revert"
    if (faster and worse) or (slower and better):
        return "mixed"
    if better:
        return "keep"
    if worse:
        return "revert"
    return "same"
