"""Reader for the game's own telemetry files (UserData/Telemetry/<track>_<P|Q|R>_<time>Z.duckdb).

LMU writes one file per session for the PLAYER's car only, one table per channel. Verified on real files:

* Channels are plain arrays at a fixed rate with no per-sample time: Lap Dist and Path Lateral / Track Edge at 10 Hz,
  Brake/Throttle at 50 Hz, Ground Speed and Steering at 100 Hz.
* Sample 0 is at the timestamp of the FIRST row of the `Lap` table, and that clock is the session clock the replay and
  the results XML use (lap-distance wraps line up with the lap-end timestamps; race lap 1 starts at the green flag).
* `Lap` and `Lap Time` rows are stamped at the END of a lap (value = laps completed / that lap's time). The out lap
  has no time, so the two tables are matched by timestamp, never by position.
* Path Lateral and Track Edge are signed alike; the car is beyond the edge when |lateral| > |edge|.

Files are only ever opened read-only (a copy is used if the game still holds one).
"""
from __future__ import annotations

import bisect
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

_NAME = re.compile(r"^(?P<track>.+)_(?P<kind>[PQR])_(?P<stamp>\d{4}-\d\d-\d\dT[\d_]+Z)\.duckdb$")
KIND = {"Race": "R", "Qualify": "Q", "Practice": "P"}


def dir_for(replay_dir: str | None) -> Optional[Path]:
    """UserData/Replays/ -> UserData/Telemetry/."""
    if replay_dir:
        p = Path(replay_dir).parent / "Telemetry"
        if p.is_dir():
            return p
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _connect(path: str | Path):
    import duckdb
    try:
        return duckdb.connect(str(path), read_only=True), None
    except duckdb.Error:                                    # held open by the game: read a copy instead
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="dd_tel_"))
        cp = tmp / Path(path).name
        shutil.copy2(path, cp)
        return duckdb.connect(str(cp), read_only=True), tmp


def _close(con, tmp) -> None:
    con.close()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class TimedLap:
    n: int                 # laps completed when it ended (race lap 1 = the first lap after the green flag)
    start: float           # session time, seconds
    end: float
    time: float


@dataclass
class Telemetry:
    path: str
    driver: str
    session: str
    track: str
    off: float             # session time of sample 0
    dist: list[float]      # 10 Hz
    lat: list[float]       # 10 Hz
    edge: list[float]      # 10 Hz
    spd: list[float]       # 100 Hz, km/h
    brk: list[float]       # 50 Hz, %
    thr: list[float]       # 50 Hz, %
    laps: list[TimedLap]


def _timed_laps(con) -> tuple[float, list[TimedLap]]:
    laps = con.execute("select ts, value from Lap order by rowid").fetchall()
    times = con.execute('select ts, value from "Lap Time" order by rowid').fetchall()
    if not laps:
        return 0.0, []
    count = {round(ts, 1): int(n) for ts, n in laps}
    return laps[0][0], [TimedLap(count.get(round(ts, 1), 0), ts - t, ts, t) for ts, t in times if t > 0]


def _meta(con) -> dict:
    return {k: v for k, v in con.execute("select * from metadata").fetchall()
            if k in ("DriverName", "SessionType", "TrackName")}


def peek(path: str | Path) -> Optional[tuple[dict, list[TimedLap]]]:
    """Who/what a file is and its timed laps, without loading the channels (cheap enough to scan many files)."""
    try:
        con, tmp = _connect(path)
    except Exception:
        return None
    try:
        _, laps = _timed_laps(con)
        return _meta(con), laps
    except Exception:
        return None
    finally:
        _close(con, tmp)


def load(path: str | Path) -> Telemetry:
    con, tmp = _connect(path)
    try:
        ch = lambda n: [r[0] for r in con.execute(f'select value from "{n}" order by rowid').fetchall()]
        off, laps = _timed_laps(con)
        m = _meta(con)
        return Telemetry(str(path), m.get("DriverName", ""), m.get("SessionType", ""), m.get("TrackName", ""), off,
                         ch("Lap Dist"), ch("Path Lateral"), ch("Track Edge"), ch("Ground Speed"),
                         ch("Brake Pos"), ch("Throttle Pos"), laps)
    finally:
        _close(con, tmp)


def candidates(folder: Path, track: str, kind: str, limit: int = 80) -> Iterator[Path]:
    """Telemetry files of this track and session kind ('R' or 'Q'), newest first."""
    files = sorted(folder.glob("*.duckdb"), key=os.path.getmtime, reverse=True)[:limit]
    want = _norm(track)
    for f in files:
        m = _NAME.match(f.name)
        if m and m["kind"] == kind and _norm(m["track"]) == want:
            yield f


def find(folder: Path, track: str, kind: str, driver: str,
         match: Callable[[list[TimedLap]], bool]) -> Optional[Path]:
    """The newest file of that track/kind, recorded by `driver`, whose timed laps satisfy `match`. Matching on the
    laps themselves (their exact times) is what ties a file to one specific session."""
    for f in candidates(folder, track, kind):
        got = peek(f)
        if got and _norm(got[0].get("DriverName", "")) == _norm(driver) and match(got[1]):
            return f
    return None


def lap_trace(t: Telemetry, ts0: float, ts1: float) -> list[tuple]:
    """One lap at 50 Hz: (distance m, speed km/h, brake %, throttle %, lateral m, edge m, session time s). Starts at the
    line and ends when the distance wraps; the previous lap's tail and any blend across the wrap are dropped."""
    out: list[tuple] = []
    nd, ns, nb = len(t.dist), len(t.spd), len(t.brk)
    for i in range(int(round((ts0 - t.off) * 50)), int((ts1 - t.off) * 50)):
        if i < 0:
            continue
        sec = i / 50
        j = min(int(sec * 10), nd - 2)
        f = sec * 10 - j
        a, b = t.dist[j], t.dist[j + 1]
        dist = (a if f < 0.5 else b) if b < a - 1000 else a + f * (b - a)
        if not out and dist > 200:
            continue
        if out and dist < out[-1][0] - 50:
            break
        out.append((dist, t.spd[min(i * 2, ns - 1)], t.brk[min(i, nb - 1)], t.thr[min(i, nb - 1)],
                    t.lat[j], t.edge[j], t.off + sec))
    return out


class Trace:
    """A lap trace with lookups by distance (distance is made non-decreasing so it can be bisected)."""

    def __init__(self, samples: list[tuple]):
        self.s = samples
        run, self.d = 0.0, []
        for p in samples:
            run = max(run, p[0])
            self.d.append(run)

    def __bool__(self) -> bool:
        return len(self.s) > 100

    def idx(self, dist: float) -> int:
        return min(len(self.s) - 1, bisect.bisect_left(self.d, dist))

    def at(self, dist: float) -> tuple:
        return self.s[self.idx(dist)]

    def elapsed(self, dist: float) -> float:
        return self.s[self.idx(dist)][6] - self.s[0][6]

    @property
    def length(self) -> float:
        return self.d[-1] if self.d else 0.0
