"""RaceRoom's shared memory ($R3E, API 3.6) decoded: the field layout of the all-drivers array, measured in game (the record
starts at all_drivers_offset, 328 bytes each). The memory updates while a replay plays and freezes while it is paused, but
the header flags (game_in_replay) stay 0, so only the values show that a replay is running.

Layout of one driver record, offsets in bytes from the record start:
    4 name[64] ... 132 finish_status, 136 place, 140 place_class, 144 lap_distance (m), 148 lap_distance_fraction,
    152 position x/y/z (m), 164 track_sector (1-3), 168 completed_laps, 172 current_lap_valid,
    176 lap_time_current_self, 180 sector_time_current_self[3], 192 sector_time_previous_self[3] and
    204 sector_time_best_self[3] (both cumulative: S1, S1+S2, lap), 216 time_delta_front (s), 220 time_delta_behind (s),
    224 pitstop_status, 228 in_pitlane, 232 num_pitstops, 256 car_speed (m/s). Unavailable values are -1."""
from __future__ import annotations

import ctypes
import struct
import sys
from dataclasses import dataclass, field
from typing import Optional

RECORD = 328
MAX_DRIVERS = 128
HEADER = struct.Struct("<8i")          # version major/minor, all_drivers_offset, driver_data_size, paused, menus, replay, vr


@dataclass
class DriverTelemetry:
    index: int
    name: str
    place: int
    place_class: int
    lap_distance: float                 # metres from the start line
    lap_fraction: float
    x: float
    y: float
    z: float
    sector: int
    laps: int                           # completed laps
    lap_valid: bool
    prev_sectors: list[float]           # cumulative times of the previous lap, -1 if none
    best_sectors: list[float]
    gap_front: float                    # seconds to the car in front, -1 for the leader
    gap_behind: float
    pit_lane: bool
    pit_stops: int
    speed: float                        # m/s
    car_number: int = 0                 # 0 when not known

    @property
    def kmh(self) -> float:
        return self.speed * 3.6

    @property
    def last_lap(self) -> float:
        return self.prev_sectors[2] if self.prev_sectors[2] > 0 else -1.0

    @property
    def best_lap(self) -> float:
        return self.best_sectors[2] if self.best_sectors[2] > 0 else -1.0


@dataclass
class TelemetryFrame:
    track: str
    layout: str
    drivers: list[DriverTelemetry] = field(default_factory=list)

    def by_name(self, name: str) -> Optional[DriverTelemetry]:
        last = name.split()[-1].lower() if name else ""
        return next((d for d in self.drivers if last and d.name.lower().endswith(last)), None)


class R3EMemory:
    """Read-only view of the game's shared memory. Python's mmap cannot open it (access denied), the Win32 calls can."""

    def __init__(self, name: str = "$R3E"):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.restype = ctypes.c_void_p
        k.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
        h = k.OpenFileMappingW(0x4, 0, name)       # FILE_MAP_READ
        if not h:
            raise OSError("the RaceRoom shared memory is not there (is the game running?)")
        self._p = k.MapViewOfFile(h, 0x4, 0, 0, 0)
        if not self._p:
            raise OSError("could not map the RaceRoom shared memory")

    def read(self) -> TelemetryFrame:
        head = ctypes.string_at(self._p, HEADER.size)
        major, _minor, off, size = HEADER.unpack(head)[:4]
        if major != 3 or size != RECORD:
            raise ValueError(f"unexpected RaceRoom shared memory layout (version {major}, record {size} bytes)")
        buf = ctypes.string_at(self._p, off + RECORD * MAX_DRIVERS)
        return decode(buf)


def _text(b: bytes) -> str:
    return b.split(b"\0")[0].decode("utf-8", "replace")


def decode(buf: bytes) -> TelemetryFrame:
    off = struct.unpack_from("<i", buf, 8)[0]
    frame = TelemetryFrame(track=_text(buf[600:664]), layout=_text(buf[664:728]))
    for i in range(MAX_DRIVERS):
        b = buf[off + i * RECORD: off + (i + 1) * RECORD]
        name = _text(b[4:68])
        if len(b) < RECORD or len(name) < 2:
            continue
        place, place_class = struct.unpack_from("<2i", b, 136)
        dist, frac, x, y, z = struct.unpack_from("<5f", b, 144)
        sector, laps, valid = struct.unpack_from("<3i", b, 164)
        prev = list(struct.unpack_from("<3f", b, 192))
        best = list(struct.unpack_from("<3f", b, 204))
        front, behind = struct.unpack_from("<2f", b, 216)
        pit, in_pit, stops = struct.unpack_from("<3i", b, 224)
        speed = struct.unpack_from("<f", b, 256)[0]
        number = struct.unpack_from("<i", b, 68)[0]
        frame.drivers.append(DriverTelemetry(i, name, place, place_class, dist, frac, x, y, z, sector, laps, bool(valid),
                                              prev, best, front, behind, bool(in_pit), stops, speed,
                                              number if 0 < number < 1000 else 0))
    frame.drivers.sort(key=lambda d: d.place if d.place > 0 else 999)
    return frame


def wrapped(before: TelemetryFrame, after: TelemetryFrame) -> bool:
    """True if the replay jumped back to its start between two frames. A replay never stops at its end, it loops: the
    leader's completed laps drop to 0 (a skip past the end does the same). Measured on a qualifying replay: 4 -> 0."""
    if not before.drivers or not after.drivers:
        return False
    laps = lambda f: max(d.laps for d in f.drivers)
    return laps(after) < laps(before)


if sys.platform != "win32":                  # the shared memory only exists on Windows
    R3EMemory = None                          # type: ignore[assignment,misc]
