"""Where the driver is: which session (practice, qualifying, race), when a NEW one starts, and when the car is in the pit lane.
Pure logic over the plain dict the game reader gives; no game and no clock in here, so it can be tested with made-up readings.

A new session is: a different track, car, session type or session number of the weekend; or the same session started over (the
lap count goes back down, or the clock of a timed session goes back up). The pit lane is debounced: it must read the same for half a
second, so one odd reading does not make a pit stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

KINDS = {0: "practice", 1: "qualifying", 2: "race", 3: "warm-up"}
DEBOUNCE = 5                       # readings in a row (about half a second at 10 a second)


@dataclass
class SessionWatch:
    key: Optional[tuple] = None
    laps: int = 0
    time_left: float = -1.0
    in_pits: bool = False          # debounced
    pitted: bool = False           # has been in the pits since this session began (a start from the pit lane does not count)
    started_in_pits: bool = False
    _raw: bool = False
    _votes: int = 0

    def update(self, info: Optional[dict], track: str, car: str) -> list:
        """One reading. Returns the events it brings: ("new_session", {"kind", "restart"}), ("pit_in", {}), ("pit_out", {"first"})."""
        if not info:
            return []
        events: list = []
        key = (track, car, info.get("type"), info.get("iteration"))
        laps, left = int(info.get("laps", 0)), float(info.get("time_left", -1.0))
        restart = self.key == key and (laps < self.laps or (self.time_left >= 0 and left > self.time_left + 30.0))
        if key != self.key or restart:
            events.append(("new_session", {"kind": KINDS.get(info.get("type"), "session"), "restart": bool(restart and self.key is not None)}))
            self.key, self.pitted = key, False
            self.started_in_pits = bool(info.get("in_pits"))
            self.in_pits, self._raw, self._votes = self.started_in_pits, self.started_in_pits, 0
        self.laps, self.time_left = laps, left
        raw = bool(info.get("in_pits"))
        self._votes = self._votes + 1 if raw == self._raw else 1
        self._raw = raw
        if self._votes >= DEBOUNCE and raw != self.in_pits:
            self.in_pits = raw
            if raw:
                self.pitted = True
                events.append(("pit_in", {}))
            else:
                events.append(("pit_out", {"first": not self.pitted and self.started_in_pits}))
        return events
