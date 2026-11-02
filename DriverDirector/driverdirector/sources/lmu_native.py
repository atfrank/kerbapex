"""Reader for Le Mans Ultimate's native shared memory block ("LMU_Data").

LMU reuses rFactor 2's ScoringInfo / VehicleScoring structs inside one 327,680-byte block.
Offsets below were found empirically against a live build (driver names located in the block,
stride 584 bytes, values cross-checked with /rest/watch/standings):
    ScoringInfo   @ 1632   (548 bytes)
    VehicleScoring[slot] @ 2192 + 584 * slot
They may move with game updates, so `check()` validates them and `doctor` reports problems.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Callable, Optional

from ..models import Car, Frame
from .rf2shm import ScoringInfo, VehicleScoring, _s, _vehicle_number

TAG = "LMU_Data"
BLOCK = 327_680
INFO_OFF = 1632
VEH_OFF = 2192
MAX_VEH = 104

FILE_MAP_READ = 4


class LMUSharedMemory:
    finite = False

    def __init__(self, focus_provider: Optional[Callable[[], Optional[int]]] = None) -> None:
        if sys.platform != "win32":
            raise OSError("LMU shared memory is Windows-only")
        k = ctypes.windll.kernel32
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.MapViewOfFile.restype = ctypes.c_void_p
        h = k.OpenFileMappingW(FILE_MAP_READ, False, TAG)
        if not h:
            raise OSError(f"shared memory '{TAG}' not found (is LMU running?)")
        self._ptr = k.MapViewOfFile(ctypes.c_void_p(h), FILE_MAP_READ, 0, 0, 0)
        if not self._ptr:
            raise OSError("MapViewOfFile failed")
        self._focus = focus_provider

    def _snapshot(self) -> bytes:
        return ctypes.string_at(self._ptr, BLOCK)

    def check(self) -> Optional[str]:
        """Return None if the layout looks right, else a human-readable problem."""
        info = ScoringInfo.from_buffer_copy(self._snapshot()[INFO_OFF:INFO_OFF + 548])
        if not (0 <= info.mNumVehicles <= MAX_VEH):
            return f"implausible vehicle count {info.mNumVehicles}; offsets may have changed"
        return None

    def poll(self) -> Optional[Frame]:
        buf = self._snapshot()
        info = ScoringInfo.from_buffer_copy(buf[INFO_OFF:INFO_OFF + 548])
        n = info.mNumVehicles
        if not (0 < n <= MAX_VEH):
            return None
        size = ctypes.sizeof(VehicleScoring)
        cars: list[Car] = []
        for slot in range(MAX_VEH):
            v = VehicleScoring.from_buffer_copy(buf[VEH_OFF + size * slot: VEH_OFF + size * (slot + 1)])
            name = _s(v.mDriverName)
            if not name or v.mPlace == 0:
                continue
            vel = v.mLocalVel
            cars.append(Car(
                id=v.mID, driver=name, number=_vehicle_number(_s(v.mVehicleName)),
                car_class=_s(v.mVehicleClass), place=v.mPlace, laps=v.mTotalLaps,
                lap_dist=v.mLapDist, speed=(vel.x ** 2 + vel.y ** 2 + vel.z ** 2) ** 0.5,
                in_pits=bool(v.mInPits), penalties=v.mNumPenalties, finished=v.mFinishStatus == 1,
                best_lap=v.mBestLapTime, last_lap=v.mLastLapTime,
            ))
        counts: dict[str, int] = {}
        for c in sorted(cars, key=lambda c: c.place):
            counts[c.car_class] = counts.get(c.car_class, 0) + 1
            c.class_place = counts[c.car_class]
        cars.sort(key=lambda c: c.place)
        return Frame(t=info.mCurrentET, track_len=info.mLapDist, cars=cars, phase=info.mGamePhase,
                     yellow=info.mYellowFlagState > 0, focus_id=self._focus() if self._focus else None)

    def close(self) -> None:
        pass
