"""Reads the player's car from LMU's live shared memory while driving (read-only; nothing is ever written).

Layout from layout.py (the game's own header). Telemetry updates ~100x a second; the player's distance around the lap
comes from the scoring part (~5x a second) and is carried forward between updates with the car's speed."""
from __future__ import annotations

import ctypes
import math
import struct
import sys
from dataclasses import dataclass
from typing import Optional

from . import layout as L

TAG = "LMU_Data"
FILE_MAP_READ = 4


@dataclass
class Sample:
    t: float                 # session time, seconds (telemetry mElapsedTime)
    lap: int                 # lap number (telemetry mLapNumber)
    lap_start: float         # session time the lap began
    dist: float              # metres around the lap (scoring, carried forward with speed between updates)
    speed: float             # km/h
    throttle: float          # 0..100 %, unfiltered (what the driver's foot does)
    brake: float             # 0..100 %
    steer: float             # -1..1
    gear: int
    rpm: float
    x: float                 # world position, metres
    z: float
    delta_best: float        # the game's own delta to the driver's best lap, seconds
    invalid: bool            # lap invalidated (track limits)
    track: str
    car: str                 # vehicle model ("BMW M4 LMGT3")
    track_len: float
    last_lap: float          # last completed lap time, seconds (-1 = none)
    best_lap: float
    g_lat: float = 0.0       # car-body g-forces, m/s^2: sideways and along the car (RaceRoom only)
    g_lon: float = 0.0
    tyre_temp: tuple = ()    # tyre temperatures FL, FR, RL, RR in degrees C, the average across the tread (RaceRoom only)
    fuel: float = -1.0       # litres in the tank (RaceRoom only)
    sectors: tuple = ()      # the previous lap's cumulative sector times, seconds (RaceRoom only)
    ffb: float = 0.0         # force-feedback output as a fraction of full scale (RaceRoom only; 1.0 = the limit)


def _num(buf: bytes, off: int, typ: str):
    fmt = L.PRIM[typ][1]
    return struct.unpack_from("<" + fmt, buf, off)[0]


def _text(buf: bytes, off: int, n: int) -> str:
    return buf[off:off + n].split(b"\0")[0].decode("utf-8", "replace").strip()


class LiveReader:
    def __init__(self, game_dir: str):
        if sys.platform != "win32":
            raise OSError("LMU shared memory is Windows-only")
        self.lay = L.compute(game_dir)
        problem = L.verify(self.lay)
        if problem:
            raise OSError(problem)
        k = ctypes.windll.kernel32
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.MapViewOfFile.restype = ctypes.c_void_p
        h = k.OpenFileMappingW(FILE_MAP_READ, False, TAG)
        if not h:
            raise OSError("LMU's live data is not available: start the game with 'Enable Plugins' on (Settings), "
                          "then restart it once")
        self._ptr = k.MapViewOfFile(ctypes.c_void_p(h), FILE_MAP_READ, 0, 0, 0)
        if not self._ptr:
            raise OSError("could not map LMU's live data")
        st = self.lay.structs
        self.ti, self.si, self.vs = st["TelemInfoV01"].fields, st["ScoringInfoV01"].fields, st["VehicleScoringInfoV01"].fields
        self.ti_size, self.vs_size = st["TelemInfoV01"].size, st["VehicleScoringInfoV01"].size
        self._last_score: dict = {}       # car id -> (scoring lap distance, session time of that reading)

    def _read(self, off: int, n: int) -> bytes:
        return ctypes.string_at(self._ptr + off, n)

    def standings(self) -> tuple[int, list[dict]]:
        """(session, cars): every car's id, place, class, name and lap data from the scoring part. The session number
        follows rFactor 2: 10 and up are races."""
        sb = self._read(self.lay.scoring_info, self.lay.structs["ScoringInfoV01"].size)
        n = max(0, min(_num(sb, self.si["mNumVehicles"][0], "long"), 104))
        v, cars = self.vs, []
        for k in range(n):
            vb = self._read(self.lay.vehicle_scoring + k * self.vs_size, self.vs_size)
            cars.append({"id": _num(vb, v["mID"][0], v["mID"][1]), "place": _num(vb, v["mPlace"][0], v["mPlace"][1]),
                         "cls": _text(vb, v["mVehicleClass"][0], 32), "name": _text(vb, v["mDriverName"][0], 32),
                         "me": bool(_num(vb, v["mIsPlayer"][0], v["mIsPlayer"][1])),
                         "dist": _num(vb, v["mLapDist"][0], "double"), "last": _num(vb, v["mLastLapTime"][0], "double"),
                         "best": _num(vb, v["mBestLapTime"][0], "double"),
                         "in_pits": bool(_num(vb, v["mInPits"][0], v["mInPits"][1])),
                         "behind_next": _num(vb, v["mTimeBehindNext"][0], "double"),
                         "laps_behind_next": _num(vb, v["mLapsBehindNext"][0], v["mLapsBehindNext"][1])})
        return _num(sb, self.si["mSession"][0], "long"), cars

    def session_best(self) -> Optional[float]:
        return None

    def session_info(self) -> Optional[dict]:
        """Which session this is and whether the car is in the pit lane (RaceRoom only for now)."""
        return None

    def setup(self) -> dict:
        """The driver-adjustable car settings now (brake bias, ABS, ...), where the game shows them; {} if not."""
        return {}

    def class_leader(self) -> Optional[dict]:
        """In a race, the car leading the player's class (None outside races, or when the player leads)."""
        session, cars = self.standings()
        me = next((c for c in cars if c["me"]), None)
        if session < 10 or me is None:
            return None
        mine = sorted((c for c in cars if c["cls"] == me["cls"]), key=lambda c: c["place"] or 999)
        return mine[0] if mine and not mine[0]["me"] else None

    def sample(self, vehicle_id: Optional[int] = None) -> Optional[Sample]:
        """The player's car now (or the car with `vehicle_id`), or None when there is no such car on track."""
        lay = self.lay
        head = self._read(lay.telemetry, 3)
        active, idx, has = head[0], head[1], bool(head[2])
        if vehicle_id is None:
            if not has or not active or idx >= 104:
                return None
        else:
            idx = None
            for k in range(min(active, 104)):            # the telemetry entry with that id
                if _num(self._read(lay.telem_info + k * self.ti_size, 4), 0, "long") == vehicle_id:
                    idx = k
                    break
            if idx is None:
                return None
        tb = self._read(lay.telem_info + idx * self.ti_size, self.ti_size)
        f = self.ti
        num = lambda name: _num(tb, f[name][0], f[name][1])
        vid = num("mID")
        vx, vy, vz = (struct.unpack_from("<ddd", tb, f["mLocalVel"][0]))
        px, _, pz = struct.unpack_from("<ddd", tb, f["mPos"][0])
        t = num("mElapsedTime")
        speed = math.sqrt(vx * vx + vy * vy + vz * vz) * 3.6
        # that car's scoring entry: lap distance, lap times, track length
        sb = self._read(lay.scoring_info, self.lay.structs["ScoringInfoV01"].size)
        nveh = _num(sb, self.si["mNumVehicles"][0], "long")
        track_len = _num(sb, self.si["mLapDist"][0], "double")
        dist = last = best = -1.0
        for k in range(max(0, min(nveh, 104))):
            vb = self._read(lay.vehicle_scoring + k * self.vs_size, self.vs_size)
            mine = (_num(vb, self.vs["mIsPlayer"][0], self.vs["mIsPlayer"][1]) if vehicle_id is None
                    else _num(vb, self.vs["mID"][0], self.vs["mID"][1]) == vid)
            if mine:
                dist = _num(vb, self.vs["mLapDist"][0], "double")
                last = _num(vb, self.vs["mLastLapTime"][0], "double")
                best = _num(vb, self.vs["mBestLapTime"][0], "double")
                break
        if dist >= 0:
            prev = self._last_score.get(vid)
            if prev and abs(dist - prev[0]) < 0.01 and t >= prev[1]:
                dist = prev[0] + (t - prev[1]) * speed / 3.6                 # carried forward between updates
                if track_len > 0 and dist >= track_len:
                    dist -= track_len
            else:
                self._last_score[vid] = (dist, t)
        return Sample(
            t=t, lap=num("mLapNumber"), lap_start=num("mLapStartET"), dist=max(0.0, dist), speed=speed,
            throttle=100 * num("mUnfilteredThrottle"), brake=100 * num("mUnfilteredBrake"),
            steer=num("mUnfilteredSteering"), gear=num("mGear"), rpm=num("mEngineRPM"), x=px, z=pz,
            delta_best=num("mDeltaBest"), invalid=bool(num("mLapInvalidated")),
            track=_text(tb, f["mTrackName"][0], 64), car=_text(tb, f["mVehicleModel"][0], 30) or _text(tb, f["mVehicleName"][0], 64),
            track_len=track_len, last_lap=last, best_lap=best)

    def race_snapshot(self) -> Optional[dict]:
        """Everything the race watcher needs in one plain dict (session clock, flags, weather, the player's fuel, tyre
        wear, penalties and the other cars' positions), or None when the player is not in a car."""
        lay = self.lay
        head = self._read(lay.telemetry, 3)
        if not head[2] or not head[0] or head[1] >= 104:
            return None
        sb = self._read(lay.scoring_info, lay.structs["ScoringInfoV01"].size)
        si = self.si
        sn = lambda name: _num(sb, si[name][0], si[name][1])
        nveh = max(0, min(sn("mNumVehicles"), 104))
        cars, me = [], None
        for k in range(nveh):
            vb = self._read(lay.vehicle_scoring + k * self.vs_size, self.vs_size)
            vn = lambda name: _num(vb, self.vs[name][0], self.vs[name][1])
            car = {"id": vn("mID"), "place": vn("mPlace"), "cls": _text(vb, self.vs["mVehicleClass"][0], 32),
                   "name": _text(vb, self.vs["mDriverName"][0], 32), "me": bool(vn("mIsPlayer")), "dist": vn("mLapDist"), "in_pits": bool(vn("mInPits")),
                   "laps": vn("mTotalLaps"), "penalties": vn("mNumPenalties"), "finish": vn("mFinishStatus"),
                   "pit_state": vn("mPitState"), "flag": vn("mFlag")}
            cars.append(car)
            me = car if car["me"] else me
        if me is None:
            return None
        tb = self._read(lay.telem_info + head[1] * self.ti_size, self.ti_size)
        f, wf = self.ti, self.lay.structs["TelemWheelV01"]
        wear = [_num(tb, f["mWheel"][0] + i * wf.size + wf.fields["mWear"][0], "double") for i in range(4)]
        tn = lambda name: _num(tb, f[name][0], f[name][1])
        return {"session": sn("mSession"), "t": sn("mCurrentET"), "end_t": sn("mEndET"), "max_laps": sn("mMaxLaps"),
                "phase": sn("mGamePhase"), "yellow_state": sn("mYellowFlagState"),
                "sector_flags": [_num(sb, si["mSectorFlag"][0] + i, "signed char") for i in range(3)],
                "raining": sn("mRaining"), "wetness": sn("mAvgPathWetness"), "track_len": sn("mLapDist"),
                "me": me, "cars": cars, "fuel": tn("mFuel"), "fuel_cap": tn("mFuelCapacity"), "wear": wear,
                "impact_t": tn("mLastImpactET"), "impact_mag": tn("mLastImpactMagnitude"), "lap": tn("mLapNumber"),
                "tyre": _text(tb, f["mFrontTireCompoundName"][0], 18)}

    def race_gaps(self) -> Optional[tuple[int, Optional[float], Optional[float]]]:
        """(place, gap to the car ahead, gap to the car behind) in seconds, in a race; None otherwise. A gap of a lap or
        more (lapped traffic) is None."""
        session, cars = self.standings()
        me = next((c for c in cars if c["me"]), None)
        if session < 10 or me is None:
            return None
        ahead = me["behind_next"] if me["place"] > 1 and me["laps_behind_next"] == 0 else None
        nxt = next((c for c in cars if c["place"] == me["place"] + 1), None)
        behind = nxt["behind_next"] if nxt and nxt["laps_behind_next"] == 0 else None
        return me["place"], ahead, behind
