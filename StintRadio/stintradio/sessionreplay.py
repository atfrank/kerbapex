"""Replay a recorded session through the engineer while a game replay plays (the coaching layer of a Studio video).

A replay in RaceRoom shows the race but carries no pedals, gears or g-forces for the driver. KerbApex recorded all of those
when you drove it (sessions\\*.sqlite, about ten times a second). This reader joins the two: it watches the replay's own
data for how far the driver is into the race (laps done and metres into the lap), finds that moment in the recording and
rebuilds the shared-memory frame the engineer would have seen then. The engineer, the overlay and the dashboards run on it
unchanged, so they say and show what they said and showed while you drove, at the moment the replay shows it, wherever
the replay is skipped to. The other cars (map, gaps, standings) come from the replay's live data.
"""
from __future__ import annotations

import bisect
import itertools
import re
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional

from .r3e_fields import FIELDS
from .r3e_reader import O, SIZE, R3EReader, _text

LAP = 100000.0                   # laps done are folded into one number with the metres into the lap: laps * LAP + metres


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sessions(folder: Path) -> list:
    """Every recorded session file, newest first."""
    return sorted(Path(folder).glob("*.sqlite"), reverse=True)


def describe(path: Path) -> Optional[dict]:
    """What a recording is: track, layout, car, when it began, whether it was a race, where it finished and how long it ran."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = dict(c.execute("SELECT key, value FROM meta"))
            n, t0, t1 = c.execute("SELECT COUNT(*), MIN(t), MAX(t) FROM frames").fetchone()
            last = c.execute("SELECT session_type, position, completed_laps FROM frames ORDER BY rowid DESC LIMIT 1").fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return None
    if not n or not last:
        return None
    return {"path": str(path), "track": meta.get("track", ""), "layout": meta.get("layout", ""), "car": meta.get("car", ""),
            "driver": meta.get("driver", ""), "started": float(meta.get("started") or t0), "frames": n, "minutes": (t1 - t0) / 60.0,
            "race": last[0] == 2, "place": int(last[1]), "laps": int(last[2])}


def find_session(folder: Path, replay_name: str, minutes: float, place: int, car: str = "") -> Optional[str]:
    """The recording of a game replay, matched by the date in its name ("2026-11-01 Circuit Zandvoort - Modern Grand Prix_12"),
    the track, the finishing place and the length. None if nothing fits."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)\s+(.*?)(?:_\d+)?$", replay_name.strip())
    if not m:
        return None
    day, track = f"{m[1]}{m[2]}{m[3]}", _slug(m[4])
    best, best_score = None, 1e9
    for p in sessions(folder):
        if not p.name.startswith(day):
            continue
        d = describe(p)
        if d is None or not d["race"]:
            continue
        name = _slug(f"{d['track']} {d['layout']}")
        if not (name in track or track in name or _slug(d["track"]) in track):
            continue
        if car and _slug(car) != _slug(d["car"]):
            continue
        if d["place"] != place or d["minutes"] < minutes - 1.0:       # the same finish, and a recording at least as long as the replay
            continue
        score = d["minutes"] - minutes                                # a recording also holds the time before the start and after the flag
        if score < best_score:
            best, best_score = d["path"], score
    return best


class SessionReplayReader(R3EReader):
    """A RaceRoom reader whose player data is the recording, slaved to the replay playing in the game."""

    def __init__(self, path: str, driver: str = ""):
        super().__init__()                                   # the replay's live data: the other cars, the standings
        self.path = path
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.db = c
        self.meta = dict(c.execute("SELECT key, value FROM meta"))
        self.cols = [r[1] for r in c.execute("PRAGMA table_info(frames)")]
        rows = c.execute("SELECT rowid, completed_laps, lap_distance FROM frames ORDER BY rowid").fetchall()
        if not rows:
            raise OSError("that recording has no frames")
        self.ids = [r[0] for r in rows]
        self.keys = list(itertools.accumulate((r[1] * LAP + max(0.0, r[2]) for r in rows), max))   # never goes back: a lap line seen twice
        self.driver = (driver or self.meta.get("driver", "")).strip().lower()
        self.row = 0
        self.TURN_SIGN = R3EReader.TURN_SIGN

    # ---- the player's progress, in the replay ---------------------------------------------------------------------
    def progress(self, b: bytes) -> Optional[float]:
        me = self._player_car(self._cars(b))
        return None if me is None else me["laps"] * LAP + max(0.0, me["dist"])

    def _player_car(self, cars: list):
        """The driver among the replay's cars: by name (the slot numbers differ from one replay to the next), else the game's own player."""
        last = self.driver.split()[-1] if self.driver.split() else ""
        return (next((c for c in cars if c["name"].lower() == self.driver), None)
                or next((c for c in cars if last and c["name"].lower().endswith(last)), None)
                or next((c for c in cars if c["me"]), None))

    def row_for(self, key: float) -> int:
        return min(max(0, bisect.bisect_left(self.keys, key)), len(self.keys) - 1)

    # ---- the frame the engineer saw then ------------------------------------------------------------------------------
    def rebuild(self, index: int) -> bytes:
        """One recorded row back into a copy of the shared memory (the scalar fields, and the track and layout names)."""
        row = self.db.execute("SELECT * FROM frames WHERE rowid=?", (self.ids[index],)).fetchone()
        buf = bytearray(SIZE)
        values = dict(zip(self.cols, row))
        for name, off, fmt in FIELDS:
            v = values.get(name)
            if v is not None:
                struct.pack_into("<" + fmt, buf, off, v)
        for key, text in (("track", self.meta.get("track", "")), ("layout", self.meta.get("layout", ""))):
            raw = text.encode("utf-8")[:63]
            buf[O[key]:O[key] + len(raw)] = raw
        return bytes(buf)

    def frame(self) -> bytes:
        """The frame the engineer works from: the recorded one for the player (pedals, gears, fuel, session clock), with the
        other cars, the standings and the car numbers of the replay playing now."""
        live = self._read(0, SIZE)
        key = self.progress(live)
        if key is None:
            self.last_frame = live
            return live
        self.row = self.row_for(key)
        buf = bytearray(self.rebuild(self.row))
        buf[O["num_cars"]:] = live[O["num_cars"]:]
        me = self._me_slot(live)
        if me is not None:
            struct.pack_into("<i", buf, O["slot_id"], me)
        self.last_frame = bytes(buf)
        return self.last_frame

    def _me_slot(self, live: bytes) -> Optional[int]:
        car = self._player_car(self._cars(live))
        return car["id"] if car else None

    def player_name(self) -> str:
        return self.meta.get("driver", "")
