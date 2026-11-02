"""The two sims that ship with KerbApex, as adapters. They wrap the existing readers; anything the adapter does not name
(the recorder's raw frame, the other cars' list) is passed through to the reader underneath."""
from __future__ import annotations

from typing import Optional

from . import contract as C
from .base import SimAdapter


class _Wrapped(SimAdapter):
    def __init__(self, inner):
        self._inner = inner
        self.TURN_SIGN = getattr(inner, "TURN_SIGN", 0)

    def __getattr__(self, name):                  # only called for what this class does not define
        if name.startswith("__") or name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def sample(self, vehicle_id: Optional[int] = None):
        return self._inner.sample(vehicle_id)

    def player_name(self) -> str:
        return self._inner.player_name()

    def session_info(self):
        return self._inner.session_info()

    def session_best(self):
        return self._inner.session_best()

    def class_leader(self):
        return self._inner.class_leader()

    def race_snapshot(self):
        return self._inner.race_snapshot()

    def race_gaps(self):
        return self._inner.race_gaps()

    def setup(self) -> dict:
        return self._inner.setup()


class RaceRoomAdapter(_Wrapped):
    id = "raceroom"
    name = "RaceRoom Racing Experience"
    capabilities = frozenset({C.SAMPLE, C.SESSION_INFO, C.SESSION_BEST, C.RACE_SNAPSHOT, C.RACE_GAPS, C.SETUP, C.RECORDING})

    @classmethod
    def available(cls, cfg=None) -> bool:
        from .. import r3e_reader
        return r3e_reader.available()

    @classmethod
    def hint(cls) -> str:
        return "Start RaceRoom (its shared memory is always on)."

    @classmethod
    def open(cls, cfg=None, **opts):
        from .. import r3e_reader
        return cls(r3e_reader.R3EReader())


class SessionReplayAdapter(_Wrapped):
    """The coaching layer of a Studio video: a recorded session replayed through the engineer, slaved to a game replay."""
    id = "replay"
    name = "Recorded session over a game replay (Studio)"
    capabilities = frozenset({C.SAMPLE, C.SESSION_INFO, C.SESSION_BEST, C.RACE_SNAPSHOT, C.RACE_GAPS, C.SETUP})
    auto_detect = False

    @classmethod
    def available(cls, cfg=None) -> bool:
        return getattr(cfg, "game", "") == cls.id and bool(getattr(cfg, "replay_session", ""))

    @classmethod
    def hint(cls) -> str:
        return "Studio starts this itself: it needs live.replay_session (a recorded session) and RaceRoom playing a replay."

    @classmethod
    def open(cls, cfg=None, **opts):
        from ..sessionreplay import SessionReplayReader
        path = getattr(cfg, "replay_session", "")
        if not path:
            raise OSError("no recorded session was given (live.replay_session)")
        return cls(SessionReplayReader(path))


class LMUAdapter(_Wrapped):
    id = "lmu"
    name = "Le Mans Ultimate"
    capabilities = frozenset({C.SAMPLE, C.OTHER_CARS, C.RACE_SNAPSHOT, C.RACE_GAPS, C.CLASS_LEADER})

    @classmethod
    def _game_dir(cls, cfg=None, **opts) -> str:
        from ..service import find_game_dir
        return opts.get("game_dir") or find_game_dir(getattr(cfg, "game_dir", "") or "") or ""

    @classmethod
    def available(cls, cfg=None) -> bool:
        return bool(cls._game_dir(cfg))

    @classmethod
    def hint(cls) -> str:
        return "Le Mans Ultimate was not found: start it with Enable Plugins on, or set live.game_dir to its install folder."

    @classmethod
    def open(cls, cfg=None, **opts):
        from ..reader import LiveReader
        gd = cls._game_dir(cfg, **opts)
        if not gd:
            raise OSError(cls.hint())
        return cls(LiveReader(gd))
