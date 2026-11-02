"""Reader for the rFactor 2 Shared Memory Map plugin buffers used by Le Mans Ultimate
(Settings > Gameplay > Enable Plugins must be on).

Struct layouts follow the plugin's InternalsPlugin.hpp (pack=4). They were written from the
public header and must be validated against a live game: run `driverdirector doctor`.
"""
from __future__ import annotations

import ctypes as C
import mmap
import sys
from typing import Optional

from ..models import Car, Frame


class Vec3(C.Structure):
    _pack_ = 4
    _fields_ = [("x", C.c_double), ("y", C.c_double), ("z", C.c_double)]


class ScoringInfo(C.Structure):
    _pack_ = 4
    _fields_ = [
        ("mTrackName", C.c_char * 64), ("mSession", C.c_int32),
        ("mCurrentET", C.c_double), ("mEndET", C.c_double), ("mMaxLaps", C.c_int32),
        ("mLapDist", C.c_double), ("pointer1", C.c_ubyte * 8), ("mNumVehicles", C.c_int32),
        ("mGamePhase", C.c_ubyte), ("mYellowFlagState", C.c_byte), ("mSectorFlag", C.c_byte * 3),
        ("mStartLight", C.c_ubyte), ("mNumRedLights", C.c_ubyte), ("mInRealtime", C.c_ubyte),
        ("mPlayerName", C.c_char * 32), ("mPlrFileName", C.c_char * 64),
        ("mDarkCloud", C.c_double), ("mRaining", C.c_double),
        ("mAmbientTemp", C.c_double), ("mTrackTemp", C.c_double), ("mWind", Vec3),
        ("mMinPathWetness", C.c_double), ("mMaxPathWetness", C.c_double),
        ("mExpansion", C.c_ubyte * 256), ("pointer2", C.c_ubyte * 8),
    ]


class VehicleScoring(C.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID", C.c_int32), ("mDriverName", C.c_char * 32), ("mVehicleName", C.c_char * 64),
        ("mTotalLaps", C.c_int16), ("mSector", C.c_byte), ("mFinishStatus", C.c_byte),
        ("mLapDist", C.c_double), ("mPathLateral", C.c_double), ("mTrackEdge", C.c_double),
        ("mBestSector1", C.c_double), ("mBestSector2", C.c_double), ("mBestLapTime", C.c_double),
        ("mLastSector1", C.c_double), ("mLastSector2", C.c_double), ("mLastLapTime", C.c_double),
        ("mCurSector1", C.c_double), ("mCurSector2", C.c_double),
        ("mNumPitstops", C.c_int16), ("mNumPenalties", C.c_int16),
        ("mIsPlayer", C.c_ubyte), ("mControl", C.c_byte), ("mInPits", C.c_ubyte),
        ("mPlace", C.c_ubyte), ("mVehicleClass", C.c_char * 32),
        ("mTimeBehindNext", C.c_double), ("mLapsBehindNext", C.c_int32),
        ("mTimeBehindLeader", C.c_double), ("mLapsBehindLeader", C.c_int32),
        ("mLapStartET", C.c_double),
        ("mPos", Vec3), ("mLocalVel", Vec3), ("mLocalAccel", Vec3),
        ("mOri", Vec3 * 3), ("mLocalRot", Vec3), ("mLocalRotAccel", Vec3),
        ("mHeadlights", C.c_ubyte), ("mPitState", C.c_ubyte),
        ("mServerScored", C.c_ubyte), ("mIndividualPhase", C.c_ubyte),
        ("mQualification", C.c_int32), ("mTimeIntoLap", C.c_double),
        ("mEstimatedLapTime", C.c_double), ("mPitGroup", C.c_char * 24),
        ("mFlag", C.c_ubyte), ("mUnderYellow", C.c_ubyte), ("mCountLapFlag", C.c_ubyte),
        ("mInGarageStall", C.c_ubyte), ("mUpgradePack", C.c_ubyte * 16),
        ("mPitLapDist", C.c_float), ("mBestLapSector1", C.c_float), ("mBestLapSector2", C.c_float),
        ("mExpansion", C.c_ubyte * 48),
    ]


class Scoring(C.Structure):
    _pack_ = 4
    _fields_ = [
        ("mVersionUpdateBegin", C.c_uint32), ("mVersionUpdateEnd", C.c_uint32),
        ("mBytesUpdatedHint", C.c_int32), ("mScoringInfo", ScoringInfo),
        ("mVehicles", VehicleScoring * 128),
    ]


EXPECTED_VEHICLE_SIZE = 584
EXPECTED_INFO_SIZE = 548


def layout_report() -> dict:
    return {
        "vehicle_size": C.sizeof(VehicleScoring), "vehicle_expected": EXPECTED_VEHICLE_SIZE,
        "info_size": C.sizeof(ScoringInfo), "info_expected": EXPECTED_INFO_SIZE,
        "scoring_size": C.sizeof(Scoring),
    }


def _s(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


class RF2SharedMemory:
    TAG = "$rFactor2SMMP_Scoring$"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("shared memory reader is Windows-only")
        self._mm = mmap.mmap(-1, C.sizeof(Scoring), tagname=self.TAG, access=mmap.ACCESS_READ)
        self._last_v = -1

    def poll(self) -> Optional[Frame]:
        # Torn-read protection: copy, then check begin == end counters.
        for _ in range(3):
            buf = bytes(self._mm[: C.sizeof(Scoring)])
            sc = Scoring.from_buffer_copy(buf)
            if sc.mVersionUpdateBegin == sc.mVersionUpdateEnd:
                break
        else:
            return None
        info = sc.mScoringInfo
        n = max(0, min(info.mNumVehicles, 128))
        cars: list[Car] = []
        counts: dict[str, int] = {}
        for v in sorted(sc.mVehicles[:n], key=lambda v: v.mPlace):
            cls = _s(v.mVehicleClass)
            counts[cls] = counts.get(cls, 0) + 1
            vel = v.mLocalVel
            speed = (vel.x ** 2 + vel.y ** 2 + vel.z ** 2) ** 0.5
            cars.append(Car(
                id=v.mID, driver=_s(v.mDriverName), number=_vehicle_number(_s(v.mVehicleName)),
                car_class=cls, place=v.mPlace, class_place=counts[cls], laps=v.mTotalLaps,
                lap_dist=v.mLapDist, speed=speed, in_pits=bool(v.mInPits),
                penalties=v.mNumPenalties, finished=v.mFinishStatus == 1,
                best_lap=v.mBestLapTime, last_lap=v.mLastLapTime,
            ))
        return Frame(t=info.mCurrentET, track_len=info.mLapDist, cars=cars,
                     phase=info.mGamePhase, yellow=info.mYellowFlagState > 0)

    def close(self) -> None:
        self._mm.close()


def _vehicle_number(name: str) -> str:
    # Vehicle names look like "Manthey DK Engineering 2026 #91:LM". Take what sits between # and ':'.
    if "#" not in name:
        return ""
    return name.split("#", 1)[1].split(":", 1)[0].strip()[:4]
