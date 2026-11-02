"""Reads the player's car from RaceRoom's live shared memory ($R3E, API 3.6) while driving (read-only).

Same interface as reader.LiveReader, so the coach, the engineer and the race watcher work unchanged. Field offsets are the
game's own header (sector3studios/r3e-api, r3e.h, structs packed to 1 byte), checked against the running game: the
all-drivers array starts at 2012 and holds 328-byte records, as raceroom_telemetry (DriverDirector) already measured.

Unlike LMU, the memory has only the player's pedals, gear and revs; other cars show position, speed and timing. So there
is no leader lap to compare with: the reference is your own best lap on the track and car."""
from __future__ import annotations

import ctypes
import json
import math
import struct
import sys
from pathlib import Path
from typing import Optional

from .reader import Sample

TAG = "$R3E"
FILE_MAP_READ = 4
SIZE = 44024
RECORD = 328
DRIVERS_AT = 2012          # first driver record (num_cars is the int just before)
MAX_DRIVERS = 128

# shared-struct offsets (bytes)
O = dict(version_major=0, version_minor=4, paused=20, menus=24, replay=28, garage=36,
         sim_time=48, pos_x=56, pos_z=72,      # the player block starts at byte 40
         track=600, layout=664, layout_length=736, session_type=780, session_phase=796, laps=812,
         time_total=816, time_left=820, pit_state=904, in_pitlane=848, pit_limiter=1572,
         flags=932, yellow=932, sector_yellow=948, blue=964,
         position=988, position_class=992, finish=996, penalties=1024, completed=1028, lap_valid=1032,
         dist=1040, lap_best=1068, lap_prev=1084, lap_cur=1100, delta_best=1132, delta_front=1124, delta_behind=1128,
         class_id=1264, model_id=1268, slot_id=1288, player=1324, control=1388, speed=1392, rps=1396,
         gear=1408, fuel=1456, fuel_cap=1460, fuel_lap=1464, throttle_raw=1504, brake_raw=1512,
         steer=1524, ffb=328, g_local=296, tyre_temp=1744, sectors_prev=1088, wear=1680, tyre_front=1840, num_cars=2008)
# driver-record offsets, from the start of one record
D = dict(name=0, class_id=68, model_id=72, slot_id=92, finish=128, place=132, place_class=136, dist=140,
         completed=164, in_pit=224, penalties_time=232, delta_front=212, delta_behind=216, speed=252)

CONTROL_PLAYER = 0
PHASE_CHECKERED = 6


def _text(b: bytes) -> str:
    return b.split(b"\0")[0].decode("utf-8", "replace").strip()


def names_file() -> Optional[Path]:
    """The game's id -> name table (cars and classes), from the Steam library that holds RaceRoom."""
    import os
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"]
    try:
        import re
        vdf = (roots[0] / "steamapps" / "libraryfolders.vdf").read_text(errors="replace")
        roots += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    except OSError:
        pass
    for r in roots:
        p = r / "steamapps" / "common" / "raceroom racing experience" / "Game" / "GameData" / "General" / "r3e-data.json"
        if p.is_file():
            return p
    return None


class R3EReader:
    TURN_SIGN = 1          # a positive heading change in the world x/z is a left (checked: Zandvoort turn one is a right, -159 degrees)

    def __init__(self, game_dir: str = ""):
        if sys.platform != "win32":
            raise OSError("RaceRoom shared memory is Windows-only")
        k = ctypes.windll.kernel32
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        h = k.OpenFileMappingW(FILE_MAP_READ, 0, TAG)
        if not h:
            raise OSError("RaceRoom's live data is not available: start the game first")
        self._ptr = k.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
        if not self._ptr:
            raise OSError("could not map RaceRoom's live data")
        head = self._read(0, 8)
        major, minor = struct.unpack("<2i", head)
        if major != 3:
            raise OSError(f"unexpected RaceRoom shared memory version {major}.{minor}")
        self.last_frame: Optional[bytes] = None
        self.cars: dict = {}
        self.classes: dict = {}
        try:
            d = json.loads(names_file().read_text(encoding="utf-8"))
            self.cars = {int(k): v.get("Name", "") for k, v in d.get("cars", {}).items()}
            self.classes = {int(k): v.get("Name", "") for k, v in d.get("classes", {}).items()}
        except (OSError, ValueError, AttributeError):
            pass

    def _read(self, off: int, n: int) -> bytes:
        return ctypes.string_at(self._ptr + off, n)

    # ---- the frame, decoded from one copy of the memory ---------------------------------------------------------
    def frame(self) -> bytes:
        self.last_frame = self._read(0, SIZE)       # the recorder writes the copy the sample was made from
        return self.last_frame

    def player_name(self) -> str:
        return _text(self._read(O["player"], 64))

    def class_name(self, cid: int) -> str:
        return self.classes.get(cid) or f"class {cid}"

    def car_name(self, mid: int) -> str:
        return self.cars.get(mid) or f"car {mid}"

    @staticmethod
    def driving(b: bytes) -> bool:
        """True when the player is in a car on track (not in a menu, the garage or a replay)."""
        i = lambda name: struct.unpack_from("<i", b, O[name])[0]
        return not (i("menus") or i("garage") or i("replay")) and i("control") == CONTROL_PLAYER and i("num_cars") > 0

    def sample(self, vehicle_id: Optional[int] = None) -> Optional[Sample]:
        """The player's car now, or None when not driving. Other cars' pedals are not in the memory."""
        if vehicle_id is not None:
            return None
        return self.sample_from(self.frame())

    def sample_from(self, b: bytes) -> Optional[Sample]:
        """The player's car from one copy of the memory (also one rebuilt from a recording, see sessionreplay)."""
        if not self.driving(b):
            return None
        i = lambda name: struct.unpack_from("<i", b, O[name])[0]
        f = lambda name: struct.unpack_from("<f", b, O[name])[0]
        t = struct.unpack_from("<d", b, O["sim_time"])[0]
        cur = f("lap_cur")
        dist = f("dist")
        if dist < 0:
            return None
        mid = i("model_id")
        track = _text(b[O["track"]:O["track"] + 64])
        layout = _text(b[O["layout"]:O["layout"] + 64])
        px, pz = struct.unpack_from("<d", b, O["pos_x"])[0], struct.unpack_from("<d", b, O["pos_z"])[0]
        thr, brk = f("throttle_raw"), f("brake_raw")
        db = f("delta_best")
        return Sample(
            t=t, lap=i("completed") + 1, lap_start=t - cur if cur >= 0 else t, dist=dist,
            speed=f("speed") * 3.6, throttle=100 * max(0.0, thr), brake=100 * max(0.0, brk), steer=f("steer"),
            gear=i("gear"), rpm=f("rps") * 60 / (2 * math.pi), x=px, z=pz,
            delta_best=db if db > -900 else 0.0, invalid=i("lap_valid") == 0,
            track=f"{track} {layout}".strip() if layout and layout.lower() not in track.lower() else track,
            car=self.car_name(mid), track_len=f("layout_length"),
            last_lap=f("lap_prev"), best_lap=f("lap_best"),
            ffb=struct.unpack_from("<d", b, O["ffb"])[0],
            g_lat=struct.unpack_from("<d", b, O["g_local"])[0], g_lon=struct.unpack_from("<d", b, O["g_local"] + 16)[0],
            tyre_temp=tuple(round(sum(struct.unpack_from("<3f", b, O["tyre_temp"] + 24 * k)) / 3, 1) for k in range(4)),
            fuel=f("fuel"), sectors=tuple(struct.unpack_from("<3f", b, O["sectors_prev"])))

    # ---- the other cars --------------------------------------------------------------------------------------
    def _cars(self, b: bytes) -> list[dict]:
        out = []
        n = max(0, min(struct.unpack_from("<i", b, O["num_cars"])[0], MAX_DRIVERS))
        me_slot = struct.unpack_from("<i", b, O["slot_id"])[0]
        for k in range(n):
            o = DRIVERS_AT + k * RECORD
            g = lambda name, fmt="i": struct.unpack_from("<" + fmt, b, o + D[name])[0]
            slot = g("slot_id")
            out.append({"id": slot, "name": _text(b[o:o + 64]), "cls": self.class_name(g("class_id")),
                        "place": g("place"), "place_class": g("place_class"), "me": slot == me_slot,
                        "dist": g("dist", "f"), "laps": g("completed"), "in_pits": bool(g("in_pit")),
                        "gap_front": g("delta_front", "f"), "gap_behind": g("delta_behind", "f"),
                        "finish": g("finish"), "speed": g("speed", "f")})
        return out

    def standings(self) -> tuple[int, list[dict]]:
        """(session, cars). The session number follows the LMU reader: 10 and up are races."""
        b = self.frame()
        st = struct.unpack_from("<i", b, O["session_type"])[0]
        return (10 if st == 2 else 0), self._cars(b)

    def class_leader(self) -> Optional[dict]:
        return None            # no pedals for other cars, so no leader lap to learn from

    def setup(self) -> dict:
        """The settings the driver can change on the move, from the frame the last sample was made from. A value of -1 (or
        a bias of -1.0) means the car does not have it."""
        b = self.last_frame
        if b is None:
            return {}
        i = lambda off: struct.unpack_from("<i", b, off)[0]
        return {"brake_bias": struct.unpack_from("<f", b, 1596)[0], "abs": i(1616), "tc": i(1936), "engine_map": i(1940),
                "engine_brake": i(1944)}

    def session_info(self) -> Optional[dict]:
        """Which session this is and where the car is: type (0 practice, 1 qualifying, 2 race, 3 warm-up), which one of the weekend,
        its phase, laps done, time left, the length of the session, and the pit lane. None if the game is not giving it."""
        try:
            b = self.frame()
            i = lambda off: struct.unpack_from("<i", b, off)[0]
            f = lambda off: struct.unpack_from("<f", b, off)[0]
            return {"type": i(O["session_type"]), "iteration": i(784), "phase": i(O["session_phase"]), "laps": i(O["completed"]),
                    "time_left": f(O["time_left"]), "time_total": f(O["time_total"]), "max_laps": i(O["laps"]),
                    "in_pits": i(O["in_pitlane"]) == 1, "pit_state": i(O["pit_state"])}
        except (struct.error, TypeError):
            return None

    def session_best(self) -> Optional[float]:
        """In a race, the fastest lap anyone has set so far (the game's session-best sector times are cumulative, so the
        last one is the whole lap). None outside races or before any lap is done."""
        b = self.frame()
        if struct.unpack_from("<i", b, O["session_type"])[0] != 2:
            return None
        t = struct.unpack_from("<f", b, 1064)[0]
        return t if 20.0 < t < 1800.0 else None

    def race_gaps(self) -> Optional[tuple[int, Optional[float], Optional[float]]]:
        b = self.frame()
        if struct.unpack_from("<i", b, O["session_type"])[0] != 2 or not self.driving(b):
            return None
        i = lambda name: struct.unpack_from("<i", b, O[name])[0]
        f = lambda name: struct.unpack_from("<f", b, O[name])[0]
        front, behind = f("delta_front"), f("delta_behind")
        place = i("position")
        # a gap of a lap or more (lapped traffic) or no car there reads -1 / very large
        ahead = front if place > 1 and 0 <= front < 600 else None
        back = behind if 0 <= behind < 600 else None
        return place, ahead, back

    def race_snapshot(self) -> Optional[dict]:
        """The plain dict racewatch.RaceWatch wants. RaceRoom has no rain or track-wetness data and no wet/dry tyre name
        in the memory, so those read as dry; phase stays 0 (racewatch's phase 6 is LMU's full-course yellow)."""
        b = self.frame()
        if not self.driving(b):
            return None
        i = lambda name: struct.unpack_from("<i", b, O[name])[0]
        f = lambda name: struct.unpack_from("<f", b, O[name])[0]
        cars = self._cars(b)
        me = next((c for c in cars if c["me"]), None)
        if me is None:
            return None
        me.update({"penalties": i("penalties"), "flag": 6 if i("blue") == 1 else 0,
                   "in_pits": bool(i("in_pitlane") == 1), "pit_state": i("pit_state"), "laps": i("completed"),
                   "dist": f("dist")})
        for c in cars:
            c.setdefault("penalties", 0)
            c.setdefault("flag", 0)
            c.setdefault("pit_state", 0)
        timed = f("time_total") > 0 and i("laps") < 0
        total, left = f("time_total"), f("time_left")
        laps = i("laps")
        return {"session": 10 if i("session_type") == 2 else 0,
                "t": (total - left) if timed and left >= 0 else struct.unpack_from("<d", b, O["sim_time"])[0],
                "end_t": total if timed else -1.0,
                "max_laps": laps if laps > 0 else 1_000_000,
                "phase": 0, "yellow_state": 0,
                "sector_flags": [max(0, struct.unpack_from("<i", b, O["sector_yellow"] + 4 * k)[0]) for k in range(3)],
                "raining": 0.0, "wetness": 0.0, "track_len": f("layout_length"),
                "me": me, "cars": cars, "fuel": f("fuel"), "fuel_cap": f("fuel_cap"),
                "wear": [f_ for f_ in struct.unpack_from("<4f", b, O["wear"])],
                "impact_t": 0.0, "impact_mag": 0.0, "lap": i("completed") + 1, "tyre": "",
                "stops": struct.unpack_from("<i", b, 920)[0],
                "pit_window": tuple(struct.unpack_from("<3i", b, 836)),          # status, start, end (the game's own units)
                "pit_min_s": struct.unpack_from("<f", b, 924)[0], "last_stop_s": struct.unpack_from("<f", b, 908)[0],
                "game_fuel_per_lap": f("fuel_lap")}


def available() -> bool:
    """True when RaceRoom's shared memory exists (the game is running)."""
    if sys.platform != "win32":
        return False
    k = ctypes.windll.kernel32
    k.OpenFileMappingW.restype = ctypes.c_void_p
    k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    h = k.OpenFileMappingW(FILE_MAP_READ, 0, TAG)
    if h:
        k.CloseHandle(ctypes.c_void_p(h))
        return True
    return False
