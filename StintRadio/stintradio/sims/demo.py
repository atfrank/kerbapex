"""A sim with no game: a made-up car driving a made-up oval, in real time. It is the reference adapter (the shortest
complete example of the contract) and lets you try the engineer, the overlay and the dashboards, or test an install,
without a sim running. Select it with live.game = demo (it is never picked by "auto")."""
from __future__ import annotations

import math
import time
from typing import Optional

from . import contract as C
from .base import SimAdapter
from .contract import Sample

LAP_S = 80.0
TRACK_LEN = 3000.0


class DemoAdapter(SimAdapter):
    id = "demo"
    name = "Demo (no game)"
    capabilities = frozenset({C.SAMPLE, C.SESSION_INFO, C.SESSION_BEST, C.RACE_GAPS, C.SETUP})
    TURN_SIGN = 1
    auto_detect = False

    def __init__(self, speed_up: float = 1.0):
        self._t0 = time.monotonic()
        self._k = speed_up

    @classmethod
    def available(cls, cfg=None) -> bool:
        return getattr(cfg, "game", "") == cls.id

    @classmethod
    def hint(cls) -> str:
        return "Set live.game to demo."

    def _now(self) -> float:
        return (time.monotonic() - self._t0) * self._k

    def sample(self, vehicle_id: Optional[int] = None) -> Optional[Sample]:
        if vehicle_id is not None:
            return None
        t = self._now()
        lap, frac = int(t // LAP_S), (t % LAP_S) / LAP_S
        a = 2 * math.pi * frac
        corner = max(0.0, math.cos(2 * a))                 # two corners a lap, at the ends of the oval
        brake = 100.0 * max(0.0, corner - 0.6) / 0.4
        throttle = 0.0 if brake > 0 else 100.0 * (1.0 - 0.7 * corner)
        return Sample(
            t=t, lap=lap + 1, lap_start=lap * LAP_S, dist=frac * TRACK_LEN, speed=210.0 - 120.0 * corner, throttle=throttle,
            brake=brake, steer=0.5 * math.sin(2 * a) * corner, gear=max(2, int(6 - 4 * corner)), rpm=4500 + 3000 * (1 - corner),
            x=600.0 * math.cos(a), z=300.0 * math.sin(a), delta_best=0.15 * math.sin(a), invalid=False, track="Demo Oval",
            car="Demo GT", track_len=TRACK_LEN, last_lap=LAP_S if lap else -1.0, best_lap=LAP_S if lap else -1.0)

    def player_name(self) -> str:
        return "Demo Driver"

    def session_info(self) -> Optional[dict]:
        t = self._now()
        return {"type": 0, "iteration": 0, "phase": 5, "laps": int(t // LAP_S), "time_left": -1.0, "time_total": -1.0,
                "max_laps": -1, "in_pits": False, "pit_state": 0}

    def session_best(self) -> Optional[float]:
        return LAP_S if self._now() >= LAP_S else None

    def race_gaps(self):
        return (1, None, 2.5)

    def setup(self) -> dict:
        return {"brake_bias": 56.0, "abs": 3, "tc": 2, "engine_map": 1}
