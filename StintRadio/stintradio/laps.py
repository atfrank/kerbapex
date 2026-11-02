"""Laps for the live coach: recorded while driving, or imported from Coach Dave Delta's local session files.

A lap is stored as JSON in %APPDATA%\\StintRadio\\laps\\<track>\\<car>\\: its time, where it came from, and samples
(lap distance m, speed km/h, brake %, throttle %, world x, world z, seconds into the lap, gear, rpm, steering -1..1,
force-feedback output, sideways and along g m/s^2, tyre temperatures FL FR RL RR, fuel litres; laps saved before that lack the
last six columns). Coach Dave files carry the
lap position as a fraction of the lap and no usable world position; their distances are scaled to the live track
length when they are used."""
from __future__ import annotations

import json
import math
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .config import data_dir
from driverdirector.telemetry import Trace


def key(text: str) -> str:
    """Track/car name -> folder key: letters and digits only, a trailing model year dropped ("BMW_M4_LMGT3_2023" and
    "BMW M4 LMGT3" give the same key)."""
    k = re.sub(r"[^a-z0-9]", "", text.lower())
    return re.sub(r"(19|20)\d\d$", "", k) or "unknown"


@dataclass
class Lap:
    track: str
    car: str
    time: float                          # seconds
    source: str                          # live | coachdave
    samples: list = field(default_factory=list)   # [dist, kmh, brake, thr, x, z, t, gear, rpm] (older laps lack the last)
    normalized: bool = False             # dist is a fraction of the lap (0..1), not metres
    sectors: list = field(default_factory=list)
    when: str = ""
    driver: str = ""                     # whose lap (the class leader's name for a leader lap)

    def scaled(self, track_len: float) -> "Lap":
        if not self.normalized or track_len <= 0:
            return self
        s = [[p[0] * track_len] + list(p[1:]) for p in self.samples]
        return Lap(self.track, self.car, self.time, self.source, s, False, self.sectors, self.when, self.driver)

    def trace(self, ref: Optional["Lap"] = None) -> Trace:
        """As driving.py's Trace: (dist, kmh, brake %, throttle %, lateral offset, edge, t). The lateral offset is the
        distance from `ref`'s position at the same lap distance (0 when either has no positions); the track edge is
        unknown live, so it never reads as 'ran wide'."""
        rt = ref.trace() if ref is not None and ref.samples and ref.samples[0][4] is not None else None
        out = []
        for d, kmh, brk, thr, x, z, t, *_ in self.samples:
            lat = 0.0
            if rt is not None and x is not None:
                rp = ref.samples[rt.idx(d)]
                lat = math.hypot(x - rp[4], z - rp[5])
            out.append((d, kmh, brk, thr, lat, 99.0, t))
        return Trace(out)


def laps_dir() -> Path:
    return data_dir() / "laps"


def save(lap: Lap) -> Path:
    d = laps_dir() / key(lap.track) / key(lap.car)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{lap.source}_{lap.time:.3f}_{lap.when or 'x'}.json".replace(":", "-")
    p.write_text(json.dumps(lap.__dict__), encoding="utf-8")
    return p


def load(p: Path) -> Lap:
    return Lap(**json.loads(p.read_text(encoding="utf-8")))


def stored(track: str, car: str) -> list[Path]:
    d = laps_dir() / key(track) / key(car)
    return sorted(d.glob("*.json")) if d.is_dir() else []


def tokens(name: str) -> set[str]:
    """The words of a car name, lower case, model year dropped: Coach Dave's "Ginetta_G61Evo_2025" and the game's
    "Ginetta G61-LT-P325 Evo" share ginetta, g, 61, evo."""
    words = re.findall(r"[a-z]+|\d+[a-z]*", re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z]{2,})", " ", name.lower()))
    return {w for w in words if not re.fullmatch(r"(19|20)\d\d", w)}


def same_car(a: str, b: str) -> bool:
    """One name's words all appear in the other's (exact key match first)."""
    if key(a) == key(b):
        return True
    ta, tb = tokens(a), tokens(b)
    return len(min(ta, tb, key=len)) >= 2 and (ta <= tb or tb <= ta)


def stored_any(track: str, car: str) -> list[Path]:
    """Stored laps of this car on this track, also under another spelling of the car's name."""
    found = stored(track, car)
    d = laps_dir() / key(track)
    if d.is_dir():
        for sub in d.iterdir():
            if sub.is_dir() and sub.name != key(car):
                laps_in = sorted(sub.glob("*.json"))
                if laps_in and same_car(load(laps_in[0]).car, car):
                    found += laps_in
    return found


def best(track: str, car: str, sources: Iterable[str] = ("live", "coachdave")) -> Optional[Lap]:
    """The fastest stored lap of this car on this track (the reference the coach compares with)."""
    found = []
    for p in stored_any(track, car):
        src, t = p.stem.split("_")[0], float(p.stem.split("_")[1])
        if src in sources:
            found.append((t, p))
    return load(min(found)[1]) if found else None


class LapBuilder:
    """Collects live samples into laps. A lap is kept when it was driven from start to finish without a reset."""

    def __init__(self) -> None:
        self.cur: list = []
        self.lap = None
        self.start = 0.0
        self._invalid = False

    def add(self, s) -> Optional[Lap]:
        """Feed a reader Sample; returns the lap that just ended, else None. Its time comes from the game's own lap
        start times (telemetry): the scoring "last lap" updates a moment after the lap counter, so at the line it can
        still hold the lap before."""
        done = None
        if self.lap is not None and s.lap != self.lap:
            measured = s.lap_start - self.start
            if measured < 10:                                   # the new lap's start time is not in yet
                measured = s.t - self.start
            ok = (s.lap == self.lap + 1 and len(self.cur) > 200 and measured > 10
                  and self.cur[0][0] < 0.05 * max(s.track_len, 1) and not self._invalid)
            if ok:
                sec = [round(x, 3) for x in (s.sectors or ())]
                done = Lap(s.track, s.car, round(measured, 3), "live", self.cur,
                           sectors=sec if len(sec) == 3 and all(x > 0 for x in sec) and abs(sec[2] - measured) < 0.5 else [])
            self.cur, self._invalid = [], False
        if self.lap != s.lap:
            self.lap, self.start, self._invalid = s.lap, s.lap_start, False
        self._invalid = self._invalid or s.invalid
        self.cur.append([round(s.dist, 2), round(s.speed, 2), round(s.brake, 1), round(s.throttle, 1),
                         round(s.x, 2), round(s.z, 2), round(s.t - self.start, 3), int(s.gear), int(s.rpm),
                         round(s.steer, 3), round(s.ffb, 3), round(s.g_lat, 2), round(s.g_lon, 2),
                         [int(round(x)) for x in s.tyre_temp], round(s.fuel, 2)])
        return done


# ---- Coach Dave Delta --------------------------------------------------------------------------------------------
def coach_dave_files() -> list[Path]:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "CoachDaveDelta"
    return sorted(root.glob("app-*/resources/*.delta"))


def _varint(b: bytes, i: int) -> tuple[int, int]:
    s = r = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _fields(m: bytes) -> list[tuple[int, object]]:
    i, out = 0, []
    while i < len(m):
        k, i = _varint(m, i)
        f, wt = k >> 3, k & 7
        if wt == 0:
            v, i = _varint(m, i)
        elif wt == 1:
            v = struct.unpack_from("<d", m, i)[0]
            i += 8
        elif wt == 5:
            v = struct.unpack_from("<f", m, i)[0]
            i += 4
        elif wt == 2:
            n, i = _varint(m, i)
            v = m[i:i + n]
            i += n
        else:
            raise ValueError(f"unexpected wire type {wt}")
        out.append((f, v))
    return out


def _text(b) -> str:
    if not isinstance(b, bytes):
        return ""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _smooth_steps(samples: list) -> None:
    """The lap position in these files only changes every few samples (the game's scoring rate); in between, spread it
    evenly over time so traces drawn against distance do not zigzag."""
    i, n = 0, len(samples)
    while i < n:
        j = i
        while j + 1 < n and samples[j + 1][0] == samples[i][0]:
            j += 1
        if j > i and j + 1 < n:
            a, b = samples[i], samples[j + 1]
            span = b[6] - a[6]
            for k in range(i + 1, j + 1):
                f = (samples[k][6] - a[6]) / span if span > 0 else 0.0
                samples[k][0] = round(a[0] + f * (b[0] - a[0]), 6)
        i = j + 1


def read_delta(path: str | Path) -> list[Lap]:
    """The laps in a Coach Dave Delta session file (length-prefixed protobuf: a header, then one message per lap with
    ~50 samples a second). Fields as decoded from the user's own files: header 1 car, 2 track, 3 session; lap 1 number,
    5 time ms, 12 sector times ms; sample 2 throttle, 3 brake (0..1), 4 speed km/h, 8 gear, 9 rpm, 10 lap fraction,
    11 ms into lap."""
    b = Path(path).read_bytes()
    msgs, i = [], 0
    while i < len(b):
        n, i = _varint(b, i)
        msgs.append(b[i:i + n])
        i += n
    if not msgs:
        return []
    head = dict(_fields(msgs[0]))
    car, track = _text(head.get(1)), _text(head.get(2))
    when = re.search(r"-(\d{13})-", Path(path).name)
    out = []
    for m in msgs[1:]:
        fs = _fields(m)
        top = {f: v for f, v in fs if f != 6}
        ms = top.get(5, 0)
        if not isinstance(ms, int) or ms <= 0:
            continue
        sectors = []
        if isinstance(top.get(12), bytes):
            j, raw = 0, top[12]
            while j < len(raw):
                v, j = _varint(raw, j)
                sectors.append(v / 1000)
        samples = []
        for f, v in fs:
            if f != 6:
                continue
            s = dict(_fields(v))
            frac, t = s.get(10), s.get(11)
            if frac is None or t is None:
                continue
            samples.append([round(float(frac), 6), round(float(s.get(4, 0.0)), 2), round(100 * float(s.get(3, 0.0)), 1),
                            round(100 * float(s.get(2, 0.0)), 1), None, None, round(t / 1000, 3), int(s.get(8, 0)), int(s.get(9, 0))])
        _smooth_steps(samples)
        fracs = [s[0] for s in samples]
        whole = (len(samples) > 200 and fracs[0] < 0.05 and fracs[-1] > 0.95       # start line to start line
                 and sum(1 for a, b in zip(fracs, fracs[1:]) if b < a - 0.01) == 0)  # no jump back (reset, tow)
        if whole:
            out.append(Lap(track, car, ms / 1000, "coachdave", samples, True, sectors,
                           f"{when.group(1)}-{top.get(1, 0)}" if when else str(top.get(1, 0))))
    return out


def import_coach_dave(limit_per_combo: int = 3) -> dict:
    """Store the fastest laps of every car/track combination found in Coach Dave Delta's files (only a few per
    combination: the coach needs the best, not the whole history). Returns what was stored."""
    by: dict[tuple[str, str], list[Lap]] = {}
    for p in coach_dave_files():
        try:
            for lap in read_delta(p):
                by.setdefault((key(lap.track), key(lap.car)), []).append(lap)
        except (OSError, ValueError, IndexError):
            continue
    stored_n = {}
    for (t, c), laps in by.items():
        have = {p.name for p in stored(laps[0].track, laps[0].car)}
        keep = sorted(laps, key=lambda l: l.time)[:limit_per_combo]
        for lap in keep:
            if not any(n.startswith(f"coachdave_{lap.time:.3f}_") for n in have):
                save(lap)
        stored_n[f"{laps[0].track} / {laps[0].car}"] = [round(l.time, 3) for l in keep]
    return stored_n
