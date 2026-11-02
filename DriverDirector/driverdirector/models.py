"""Shared data model. Everything downstream of a telemetry source speaks these types."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Car:
    id: int                      # stable slot id within the session
    driver: str
    number: str = ""
    car_class: str = ""
    place: int = 0               # overall position, 1-based
    class_place: int = 0
    laps: int = 0                # completed laps
    lap_dist: float = 0.0        # metres around the lap
    speed: float = 0.0           # m/s
    in_pits: bool = False
    penalties: int = 0
    finished: bool = False
    best_lap: float = -1.0
    last_lap: float = -1.0

    def total_dist(self, track_len: float) -> float:
        return self.laps * track_len + self.lap_dist


@dataclass
class Frame:
    t: float                     # session elapsed time, seconds
    track_len: float
    cars: list[Car]
    phase: int = 5               # rF2 game phase: 5 = green, 6 = full-course yellow, 8 = over...
    yellow: bool = False
    focus_id: Optional[int] = None   # car currently watched by the game camera, if known

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Frame":
        return Frame(
            t=d["t"], track_len=d["track_len"], phase=d.get("phase", 5),
            yellow=d.get("yellow", False), focus_id=d.get("focus_id"),
            cars=[Car(**c) for c in d["cars"]],
        )


@dataclass
class Event:
    kind: str                    # battle | overtake | incident | penalty | start | finish | pit
    t_start: float
    t_end: float
    drivers: list[int]           # car ids; drivers[0] is the primary subject
    score: float = 0.0
    desc: str = ""
    confidence: str = "exact"    # exact: to the second | est: interpolated, ~+-5s | lap: somewhere in a lap window
    peak: float | None = None    # the key moment, for events longer than a PiP box
    label: str = ""              # short on-screen tag for the PiP box (falls back to desc)

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    def involves(self, car_id: int) -> bool:
        return car_id in self.drivers


@dataclass
class CameraCue:
    t: float                     # replay time at which to switch
    group: str                   # trackside | onboard | driving | tv
    focus_id: int


@dataclass
class Inlay:
    """A picture-in-picture clip shown on top of the main pass."""
    event: Event
    clip_start: float            # replay time of the clip
    clip_end: float
    show_at: float               # replay time on the main timeline when the box appears
    show_until: float
    focus_id: int
    camera: str = "trackside"
    label: str = ""
    path: Optional[str] = None   # filled after the clip is recorded
    offset: float = 0.0          # seconds of video before replay playback actually began
    # a clip from another replay than the loaded race (the reference lap): which replay, and who to focus there
    replay_id: Optional[int] = None
    replay_name: str = ""
    focus_name: str = ""
    slow: float = 1.0            # playback speed in the box (0.5 = half speed); the clip is recorded at normal speed
    names: list = field(default_factory=list)    # who is in the clip (a strip under the box names them)
    replay: bool = False         # a replay of something already seen (tagged REPLAY on the box)


@dataclass
class ReferenceLap:
    """The focus driver's best qualifying lap, to be shown beside the race's first lap."""
    replay_id: int               # the qualifying replay in the game's library
    replay_name: str
    driver: str
    lap_time: float              # seconds
    lap_start: float             # session time in the qualifying replay at which that lap begins


@dataclass
class Plan:
    focus_id: int
    t0: float
    t1: float
    cues: list[CameraCue] = field(default_factory=list)
    inlays: list[Inlay] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    main_offset: float = 0.0     # seconds of main-pass video before playback began
    slow: list = field(default_factory=list)     # cinematic: (replay start, replay end) stretches the game plays slowly
    style: str = "broadcast"     # broadcast | cinematic

    def summary(self) -> dict:
        return {
            "focus_id": self.focus_id, "t0": self.t0, "t1": self.t1,
            "cues": len(self.cues), "inlays": len(self.inlays), "events": len(self.events),
            **({"style": self.style, "slow_moments": len(self.slow), "slow": [list(s) for s in self.slow],
                "cutaways": sum(1 for c in self.cues if c.focus_id != self.focus_id)} if self.style != "broadcast" else {}),
        }
