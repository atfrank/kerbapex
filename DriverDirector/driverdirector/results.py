"""Parser for LMU race result XML files (UserData/Log/Results/*.xml).

The game writes one per session. It holds what the REST history lacks: exact lap-end times for
every car, sector times, exact incident times with impact magnitude, penalties, pit flags.
Its time axis is the same as the replay's (verified: incident times match /rest/watch incidents).
"""
from __future__ import annotations

import glob
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .history import Driver, Incident, Lap, Quali, RaceHistory, build_times, finish_exact
from .lmu_api import fix_mojibake

_CONTACT = re.compile(r"^(?P<a>.*)\((?P<aid>\d+)\) reported contact \((?P<mag>[\d.]+)\) with "
                      r"(?:another vehicle (?P<b>.*)\((?P<bid>\d+)\)|(?P<imm>.*))$")


def _load(path: str | Path) -> ET.Element:
    raw = Path(path).read_bytes()
    raw = re.sub(rb"<!DOCTYPE.*?\]>", b"", raw, flags=re.S)
    return ET.fromstring(raw)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fix_mojibake(name).lower())


def results_dir_for(replay_dir: str | None = None) -> Path | None:
    """UserData/Replays/ -> UserData/Log/Results/."""
    if replay_dir:
        p = Path(replay_dir).parent / "Log" / "Results"
        if p.is_dir():
            return p
    return None


def _header(path: str) -> tuple[str, float] | None:
    try:
        head = Path(path).read_bytes()[:6000].decode("utf-8", "replace")
    except OSError:
        return None
    m = re.search(r"<TrackCourse>(.*?)</TrackCourse>", head)
    return (m.group(1), os.path.getmtime(path)) if m else None


def find_results(results_dir: Path, track: str, rest_incident_times: list[float],
                 limit: int = 60) -> str | None:
    """Pick the results file for the loaded replay: same track, and its incident times overlap the
    REST incident list (a fingerprint that is unique to one race)."""
    files = sorted(glob.glob(str(results_dir / "*.xml")), key=os.path.getmtime, reverse=True)[:limit]
    want = _norm(track)
    rest = {round(t, 1) for t in rest_incident_times}
    best, best_score = None, 0.0
    for f in files:
        hd = _header(f)
        if not hd or _norm(hd[0]) != want:
            continue
        txt = Path(f).read_text(encoding="utf-8", errors="replace")
        ets = {round(float(x), 1) for x in re.findall(r'<Incident et="([\d.]+)"', txt)}
        score = len(rest & ets) / len(rest) if rest else 0.0
        if score > best_score:
            best, best_score = f, score
    return best if best_score >= 0.5 else None


def find_qualifying(race_file: str, race_names: set[str], limit: int = 40) -> str | None:
    """The qualifying results file for a race: same track, written before the race file, and mostly the
    same drivers (names are normalised). The latest such file wins (a session may be qualified twice)."""
    folder = Path(race_file).parent
    race_mtime = os.path.getmtime(race_file)
    hd = _header(race_file)
    if not hd or not race_names:
        return None
    files = sorted(glob.glob(str(folder / "*.xml")), key=os.path.getmtime, reverse=True)[:limit]
    for f in files:
        if f == race_file or os.path.getmtime(f) >= race_mtime:
            continue
        h = _header(f)
        if not h or _norm(h[0]) != _norm(hd[0]):
            continue
        try:
            q = _load(f).find("RaceResults/Qualify")
        except ET.ParseError:
            continue
        if q is None:
            continue
        names = {_norm(d.findtext("Name", "")) for d in q.findall("Driver")}
        if len(names & race_names) >= 0.5 * len(race_names):
            return f
    return None


def _compound(raw: str) -> str:
    """"0,Medium" -> "Medium" (the number in front is the compound's slot in the car's list)."""
    return raw.split(",", 1)[-1].strip()


def _best_lap_sectors(dr: ET.Element, best: float) -> tuple[float, float, float]:
    """Sector durations of the lap whose time is `best` (-1 where the game did not record one)."""
    for l in dr.findall("Lap"):
        try:
            t = float(l.text or "")
        except ValueError:
            continue
        if abs(t - best) < 0.0015:
            a = l.attrib
            return tuple(float(a[k]) if k in a else -1.0 for k in ("s1", "s2", "s3"))     # type: ignore[return-value]
    return (-1.0, -1.0, -1.0)


def _best_lap_start(dr: ET.Element, best: float) -> tuple[int, float]:
    """(lap number, session time the lap began) of the lap whose time is `best`. A <Lap>'s `et` is its START
    time (same convention as the race laps); a lap without one (the out lap reads "--.---") is placed from the
    start of the lap after it. (0, -1) when no lap matches."""
    laps = []
    for l in dr.findall("Lap"):
        try:
            et = float(l.attrib.get("et", ""))
        except ValueError:
            et = None
        try:
            t = float(l.text or "")
        except ValueError:
            t = -1.0
        laps.append((int(l.attrib.get("num", 0) or 0), et, t))
    for i, (n, et, t) in enumerate(laps):
        if t > 0 and abs(t - best) < 0.0015:
            if et is None and i + 1 < len(laps) and laps[i + 1][1] is not None:
                et = laps[i + 1][1] - t
            return n, (et if et is not None else -1.0)
    return 0, -1.0


def parse_qualifying(path: str, h: RaceHistory) -> dict[int, Quali]:
    """slot -> qualifying position, best lap and where that lap begins, for every race driver found in the
    qualifying file."""
    q = _load(path).find("RaceResults/Qualify")
    if q is None:
        return {}
    by_name = {_norm(d.name): s for s, d in h.drivers.items()}
    out: dict[int, Quali] = {}
    for dr in q.findall("Driver"):
        slot = by_name.get(_norm(dr.findtext("Name", "")))
        if slot is None:
            continue
        try:
            best = float(dr.findtext("BestLapTime", "") or -1)
            n, start = _best_lap_start(dr, best) if best > 0 else (0, -1.0)
            out[slot] = Quali(int(dr.findtext("Position", "0") or 0), int(dr.findtext("ClassPosition", "0") or 0),
                              best if best > 0 else -1.0, n, start,
                              _best_lap_sectors(dr, best) if best > 0 else (-1.0, -1.0, -1.0))
        except ValueError:
            continue
    return out


def parse(path: str, standings: list[dict]) -> RaceHistory:
    root = _load(path)
    race = root.find("RaceResults/Race")
    if race is None:
        raise ValueError("results file has no Race section")
    slot_by_name = {_norm(s["driverName"]): int(s["slotID"]) for s in standings}
    drivers: dict[int, Driver] = {}
    laps: dict[int, list[Lap]] = {}
    next_free = 1000
    for dr in race.findall("Driver"):
        name = fix_mojibake(dr.findtext("Name", ""))
        slot = slot_by_name.get(_norm(name))
        if slot is None:
            slot, next_free = next_free, next_free + 1
        drivers[slot] = Driver(slot, name, dr.findtext("CarNumber", ""), dr.findtext("CarClass", ""),
                               dr.findtext("VehName", ""), int(dr.findtext("GridPos", "0") or 0))
        # A <Lap> element's `et` is the time the lap STARTS (every car's lap 1 shares the green-flag time) and
        # its text is that lap's duration. The lap ends at start + duration, or where the next lap starts.
        recs = []
        for l in dr.findall("Lap"):
            a = l.attrib
            t = float(l.text) if l.text and l.text.replace(".", "", 1).isdigit() and float(l.text) > 0 else -1.0
            recs.append((int(a["num"]), float(a["et"]), t, a))
        ls: list[Lap] = []
        for i, (n, start, t, a) in enumerate(recs):
            nxt = recs[i + 1][1] if i + 1 < len(recs) else None
            end = start + t if t > 0 else nxt
            s1, s2 = float(a["s1"]) if "s1" in a else -1, float(a["s2"]) if "s2" in a else -1
            lines: tuple[float, ...] = ()
            if end is not None and s1 > 0 and s2 > 0:
                lines = (start + s1, start + s1 + s2, end)
            s3 = float(a["s3"]) if "s3" in a else -1
            ls.append(Lap(n, int(a["p"]), t, a.get("pit") == "1", False, end, lines, (s1, s2, s3),
                          _compound(a.get("fcompound", ""))))
        laps[slot] = ls
    t0 = min((l.et - l.time for ls in laps.values() for l in ls if l.n == 1 and l.time > 0 and l.et), default=0.0)
    h = RaceHistory(drivers, laps, [], t0, fix_mojibake(root.findtext("RaceResults/TrackCourse", "")),
                    "RACE", source="xml")
    h.track_len = float(root.findtext("RaceResults/TrackLength", "0") or 0)
    h.event = fix_mojibake(root.findtext("RaceResults/TrackEvent", "") or "")
    try:                                                      # the game writes 2147483647 laps when only time limits the race
        laps_lim = int(race.findtext("Laps", "0") or 0)
        h.race_laps = laps_lim if 0 < laps_lim < 100000 else 0
        h.race_minutes = float(race.findtext("Minutes", "0") or 0)
    except ValueError:
        pass
    try:
        h.date = time.strftime("%Y-%m-%d", time.localtime(float(race.findtext("DateTime", ""))))
    except (ValueError, OverflowError, OSError):
        pass
    # incidents: each contact is reported by both cars; keep one per (time, pair)
    seen: set[tuple] = set()
    for inc in race.find("Stream").findall("Incident") if race.find("Stream") is not None else []:
        m = _CONTACT.match((inc.text or "").strip())
        if not m:
            continue
        et = float(inc.attrib["et"])
        a, b = fix_mojibake(m["a"]), fix_mojibake(m["b"]) if m["b"] else None
        key = (round(et, 1), *sorted([a, b or ""]))
        if key in seen:
            continue
        seen.add(key)
        h.incidents.append(Incident(et, a, b, float(m["mag"])))
    h.incidents.sort(key=lambda i: i.et)
    for pen in (race.find("Stream").findall("Penalty") if race.find("Stream") is not None else []):
        h.penalties.append((float(pen.attrib.get("et", 0)), fix_mojibake(pen.attrib.get("Driver", "")),
                            (pen.text or "").strip()))
    for sc in (race.find("Stream").findall("Sector") if race.find("Stream") is not None else []):
        try:
            h.sector_bests.append((float(sc.attrib.get("et", 0)), fix_mojibake(sc.attrib.get("Driver", "")),
                                   int(sc.attrib.get("Sector", 0))))
        except ValueError:
            continue
    for tl in (race.find("Stream").findall("TrackLimits") if race.find("Stream") is not None else []):
        if tl.attrib.get("Resolution", "7") != "7":                # 7 = "No Further Action": only issued warnings count
            h.track_warnings.append((float(tl.attrib.get("et", 0)), fix_mojibake(tl.attrib.get("Driver", "")),
                                     (tl.text or "").strip()))
    finish_exact(h)
    qf = find_qualifying(str(path), {_norm(d.name) for d in drivers.values()})
    if qf:
        h.quali = parse_qualifying(qf, h)
    return h
