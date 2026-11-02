"""The adapter base class: implement `sample()` and say which of the optional parts you have."""
from __future__ import annotations

from typing import Optional

from . import contract as C
from .contract import Sample


class SimAdapter:
    """One sim. Subclass it, set `id`, `name` and `capabilities`, and implement the methods for those capabilities.

    Only `sample()` is required. Everything else defaults to "not available" and the features that need it switch off.
    The engine never touches the game directly, so an adapter is the whole of the work for a new title."""

    id = ""                                  # short lower-case name used in settings (live.game): "raceroom", "lmu", "acc"
    name = ""                                # shown to the driver
    capabilities: frozenset = frozenset({C.SAMPLE})
    TURN_SIGN = 0                            # +1 / -1: which direction of heading change in x/z is a left turn; 0 = work it out
    auto_detect = True                       # may "auto" pick this sim when its live data is present?
    contract = C.CONTRACT_VERSION            # the contract version the adapter was written for

    # ---- finding and opening the sim -------------------------------------------------------------------------------
    @classmethod
    def available(cls, cfg=None) -> bool:
        """True when this sim's live data is there right now (the game is running)."""
        return False

    @classmethod
    def hint(cls) -> str:
        """What to tell the driver when the sim's data is not there."""
        return f"Start {cls.name or cls.id}."

    @classmethod
    def open(cls, cfg=None, **opts) -> "SimAdapter":
        """Connect. Raise OSError with a clear message if the data is not there."""
        return cls()

    def close(self) -> None:
        pass

    # ---- the data (see contract.py for every field and unit) ------------------------------------------------------
    def sample(self, vehicle_id: Optional[int] = None) -> Optional[Sample]:
        """The player's car now (or the car `vehicle_id`, if OTHER_CARS), or None when there is no car on track."""
        raise NotImplementedError

    def player_name(self) -> str:
        return ""

    def session_info(self) -> Optional[dict]:
        return None

    def session_best(self) -> Optional[float]:
        return None

    def class_leader(self) -> Optional[dict]:
        return None

    def race_snapshot(self) -> Optional[dict]:
        return None

    def race_gaps(self) -> Optional[tuple]:
        return None

    def setup(self) -> dict:
        return {}

    # ---- helpers -----------------------------------------------------------------------------------------------------
    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    @classmethod
    def describe(cls) -> dict:
        return {"id": cls.id, "name": cls.name, "capabilities": sorted(cls.capabilities), "missing": C.lacking(cls.capabilities)}
