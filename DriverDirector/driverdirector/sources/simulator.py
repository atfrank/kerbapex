"""Deterministic synthetic race for tests and demo mode (no game needed).

Scripted story with 8 cars (class "HYP"):
  * cars 1 & 2 run nose-to-tail from ~t=60 and car 2 passes car 1 at ~t=200
  * car 5 spins and stops (sudden decel) at t=300 for 25 s
  * car 3 rear-ends into a slow stop at t=420 (contact) and rejoins
"""
from __future__ import annotations

import math
from typing import Iterator, Optional

from ..models import Car, Frame

TRACK = 5000.0
NAMES = ["Alex Rivera", "Jonas Keller", "Mika Tanaka", "Luis Ferreira",
         "Sam Okafor", "Ines Moreau", "Tom Whitfield", "Yuri Petrov"]


def generate(duration: float = 600.0, hz: float = 5.0) -> Iterator[Frame]:
    n = len(NAMES)
    base = [62.0 - 0.15 * i for i in range(n)]            # m/s
    base[1] = 62.2                                         # car 2 slightly quicker than car 1
    base[0] = 61.6
    dist = [900.0 - 60.0 * i for i in range(n)]
    dt = 1.0 / hz
    t = 0.0
    prev_speed = base[:]
    while t <= duration:
        speeds = []
        for i in range(n):
            v = base[i] + 0.8 * math.sin(t / 17.0 + i)
            if i == 4 and 300 <= t < 325:
                v = 0.0 if t >= 302 else max(0.0, 62 - (t - 300) * 31)   # spin -> stop
            if i == 2 and 420 <= t < 450:
                v = 2.0 if t >= 421 else 62 - (t - 420) * 60            # contact -> crawl
            if i == 1 and 150 <= t < 200:
                v += 1.2                                                # lunge on car 1
            if i == 0 and 150 <= t < 200:
                v -= 0.2
            speeds.append(max(0.0, v))
        for i in range(n):
            dist[i] += speeds[i] * dt
        order = sorted(range(n), key=lambda i: -dist[i])
        cars: list[Car] = []
        for rank, i in enumerate(order, 1):
            laps, ld = divmod(dist[i], TRACK)
            cars.append(Car(
                id=i + 1, driver=NAMES[i], number=str(10 + i), car_class="HYP",
                place=rank, class_place=rank, laps=int(laps), lap_dist=ld,
                speed=speeds[i], in_pits=False, best_lap=212.0 + i, last_lap=213.0 + i,
            ))
        yield Frame(t=round(t, 3), track_len=TRACK, cars=cars)
        prev_speed = speeds
        t += dt


class Simulator:
    """Source adapter: `poll()` steps through the scripted race one frame at a time."""

    finite = True

    def __init__(self, duration: float = 600.0, hz: float = 5.0):
        self._it = generate(duration, hz)

    def poll(self) -> Optional[Frame]:
        return next(self._it, None)

    def close(self) -> None:
        pass
