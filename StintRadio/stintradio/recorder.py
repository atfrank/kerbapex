"""Record RaceRoom's live data stream to disk so it can be searched afterwards, or while driving, by anything that can run a
query (the StintRadio MCP server for Claude, a notebook, the page).

One SQLite file per session in %APPDATA%\\StintRadio\\sessions\\, written while the game runs (WAL mode, so a reader can
look at the file at the same time):
    meta    key, value        the track, layout, car, class, driver, game version and when it began
    frames  t + every scalar field of the shared memory (r3e_fields.FIELDS), ~10 a second
    cars    t + every car on track (place, lap distance, speed, gaps, laps, pit) once a second
    laps    one row per completed lap: number, time, sector times, whether it was valid
`t` is wall-clock seconds (Unix time). Nothing is written when the player is not in a car."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import struct
import time
from pathlib import Path
from typing import Optional

from .config import data_dir
from .r3e_fields import FIELDS

KEEP_DAYS = 60
KEEP_BYTES = 5 * 1024 ** 3
FMT_SIZE = {"i": 4, "f": 4, "d": 8}
SQL_TYPE = {"i": "INTEGER", "f": "REAL", "d": "REAL"}


def sessions_dir() -> Path:
    d = data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "unknown"


def schema_sql() -> list[str]:
    cols = ", ".join(f'"{n}" {SQL_TYPE[f]}' for n, _, f in FIELDS)
    return [
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
        f"CREATE TABLE IF NOT EXISTS frames (t REAL, {cols})",
        "CREATE INDEX IF NOT EXISTS frames_t ON frames (t)",
        "CREATE TABLE IF NOT EXISTS cars (t REAL, slot INTEGER, name TEXT, class TEXT, place INTEGER, dist REAL, speed REAL, "
        "laps INTEGER, in_pit INTEGER, gap_front REAL, gap_behind REAL)",
        "CREATE INDEX IF NOT EXISTS cars_t ON cars (t)",
        "CREATE TABLE IF NOT EXISTS laps (lap INTEGER, time REAL, s1 REAL, s2 REAL, s3 REAL, valid INTEGER, ended REAL)",
    ]


def decode_frame(buf: bytes) -> list:
    """The values of FIELDS, in order, from one copy of the shared memory."""
    return [struct.unpack_from("<" + f, buf, off)[0] for _, off, f in FIELDS]


def _text(b: bytes) -> str:
    return b.split(b"\0")[0].decode("utf-8", "replace").strip()


class Recorder:
    def __init__(self, hz: float = 10.0, folder: Optional[Path] = None, clock=time.time):
        self.period = 1.0 / max(0.5, hz)
        self.folder = folder or sessions_dir()
        self.clock = clock
        self.db: Optional[sqlite3.Connection] = None
        self.path: Optional[Path] = None
        self.key: Optional[tuple] = None
        self._last = 0.0
        self._last_cars = 0.0
        self._commit = 0.0
        self._lap_seen: Optional[int] = None
        self._insert = None
        self.rows = 0
        self.prune()

    # ---- writing --------------------------------------------------------------------------------------------------
    def offer(self, reader, buf: bytes) -> None:
        """Called with each fresh copy of the shared memory; writes at most `hz` rows a second."""
        now = self.clock()
        if now - self._last < self.period or not reader.driving(buf):
            return
        self._last = now
        names = (_text(buf[600:664]), _text(buf[1196:1260]), _text(buf[1324:1388]))
        key = (_text(buf[600:664]), _text(buf[664:728]), struct.unpack_from("<i", buf, 1268)[0])
        if key != self.key:
            self._open(reader, buf, key)
        vals = decode_frame(buf)
        self.db.execute(self._insert, [now] + vals)
        self.rows += 1
        if now - self._last_cars >= 1.0:
            self._last_cars = now
            self.db.executemany("INSERT INTO cars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                [(now, c["id"], c["name"], c["cls"], c["place"], c["dist"], c["speed"], c["laps"],
                                  int(c["in_pits"]), c["gap_front"], c["gap_behind"]) for c in reader._cars(buf)])
        done = struct.unpack_from("<i", buf, 1028)[0]               # laps completed
        if self._lap_seen is not None and done > self._lap_seen:
            prev = struct.unpack_from("<f", buf, 1084)[0]
            sec = struct.unpack_from("<3f", buf, 1088)
            self.db.execute("INSERT INTO laps VALUES (?,?,?,?,?,?,?)",
                            (done, prev, sec[0], sec[1] - sec[0] if sec[1] > 0 else None, sec[2] - sec[1] if sec[2] > 0 else None,
                             struct.unpack_from("<i", buf, 1176)[0], now))
        self._lap_seen = done
        if now - self._commit >= 2.0:
            self.db.commit()
            self._commit = now

    def _open(self, reader, buf: bytes, key: tuple) -> None:
        self.close()
        track, layout, model = key
        car = reader.car_name(model)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        self.path = self.folder / f"{stamp}_{_slug(track)}_{_slug(car)}.sqlite"
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        for stmt in schema_sql():
            self.db.execute(stmt)
        meta = {"game": "RaceRoom Racing Experience", "track": track, "layout": layout, "car": car,
                "class": reader.class_name(struct.unpack_from("<i", buf, 1264)[0]), "driver": _text(buf[1324:1388]),
                "api_version": "%d.%d" % struct.unpack_from("<2i", buf, 0), "started": self.clock(),
                "units": json.dumps({"speed": "m/s (car_speed), km/h in the laps tools", "temperatures": "C",
                                     "fuel": "litres", "g": "m/s^2", "pedals": "0..1", "t": "unix seconds"})}
        self.db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, str(v)) for k, v in meta.items()])
        cols = ",".join(["t"] + [f'"{n}"' for n, _, _ in FIELDS])
        self._insert = f"INSERT INTO frames ({cols}) VALUES ({','.join('?' * (len(FIELDS) + 1))})"
        self.key, self._lap_seen = key, None
        self.db.commit()

    def close(self) -> None:
        if self.db is not None:
            try:
                self.db.commit()
                self.db.close()
            except sqlite3.Error:
                pass
        self.db, self.key = None, None

    # ---- housekeeping ---------------------------------------------------------------------------------------------
    def prune(self, keep_days: int = KEEP_DAYS, keep_bytes: int = KEEP_BYTES) -> None:
        """Delete sessions older than `keep_days`, then the oldest until the folder is under `keep_bytes`."""
        files = sorted(self.folder.glob("*.sqlite"), key=lambda p: p.stat().st_mtime)
        cutoff = self.clock() - keep_days * 86400
        for p in list(files):
            if p.stat().st_mtime < cutoff and p != self.path:
                self._drop(p)
                files.remove(p)
        total = sum(self._size(p) for p in files)
        while files and total > keep_bytes:
            p = files.pop(0)
            total -= self._size(p)
            self._drop(p)

    @staticmethod
    def _size(p: Path) -> int:
        return sum(q.stat().st_size for q in p.parent.glob(p.name + "*") if q.is_file())

    @staticmethod
    def _drop(p: Path) -> None:
        for q in p.parent.glob(p.name + "*"):
            try:
                q.unlink()
            except OSError:
                pass


def latest_session(folder: Optional[Path] = None) -> Optional[Path]:
    files = sorted((folder or sessions_dir()).glob("*.sqlite"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None
