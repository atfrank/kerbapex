"""Game control: camera groups, driver focus and replay transport.

LMU's REST API (found in the game's own swagger schema) exposes focus, camera and replay control.
`DryRunGame` logs actions instead, for the simulator and tests.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

from .config import CameraSettings
from .models import Frame


class Game(Protocol):
    def set_camera(self, group: str) -> None: ...
    def focus_driver(self, car_id: int, poll=None) -> bool: ...
    def seek(self, target_t: float, poll=None, tol: float = 3.0) -> bool: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def slow(self) -> None: ...


class DryRunGame:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def set_camera(self, group): self.log.append(("camera", group))
    def focus_driver(self, car_id, poll=None): self.log.append(("focus", car_id)); return True
    def seek(self, target_t, poll=None, tol=3.0): self.log.append(("seek", target_t)); return True
    def play(self): self.log.append(("play",))
    def pause(self): self.log.append(("pause",))
    def slow(self): self.log.append(("slow",))


class RestGame:
    """Drives LMU through its REST API: focus by slot, camera by name, seek by time, VCR transport.
    Calls are verified by reading the game state back where the API allows it."""

    PLAY, STOP, SLOW = "VCRCOMMAND_PLAY", "VCRCOMMAND_STOP", "VCRCOMMAND_SLOW"   # names from the game's own UI

    def __init__(self, lmu, cam: CameraSettings):
        self.lmu = lmu
        self.cameras = cam.cameras
        self._rot: dict[str, int] = {}

    def set_camera(self, group: str) -> None:
        names = self.cameras.get(group) or self.cameras["trackside"]
        i = self._rot.get(group, 0)
        self._rot[group] = i + 1                      # rotate through the group's cameras
        self.lmu.set_camera(names[i % len(names)])

    def focus_driver(self, car_id: int, poll=None, **_) -> bool:
        self.lmu.set_focus(car_id)
        for _ in range(10):                           # the game applies focus a moment later
            if self.lmu.focus() == car_id:
                return True
            time.sleep(0.1)
        return False

    def seek(self, target_t: float, poll=None, tol: float = 3.0, settle: float = 1.2) -> bool:
        """Jump the replay to `target_t`. LMU publishes no clock during replays, so this can't be
        read back; it was verified by eye (seek to 130 s landed on the right moment)."""
        self.lmu.seek(target_t)
        time.sleep(settle)
        return True

    def play(self) -> None:
        self.lmu.command(self.PLAY)

    def pause(self) -> None:
        self.lmu.command(self.STOP)

    def slow(self) -> None:
        """The game's own slow motion (the replay bar's slow button): real frames, not stretched video."""
        self.lmu.command(self.SLOW)
