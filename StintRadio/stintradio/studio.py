"""Studio: make a video of a RaceRoom replay with DriverDirector, with the KerbApex overlay and the engineer in it.

The video is DriverDirector's standard one (cockpit view from inside the car as the main picture, other views in a box,
leaderboard, title and fades). With coaching on, a second StintRadio (port 6421, its own settings, saving nothing) replays the
recording KerbApex made while you drove that race, slaved to the game replay (see sessionreplay.py). It shows the KerbApex
overlay as a browser source in the OBS scene and speaks as the engineer; OBS records both with the game. For the second pass
(other views in a box) it is stopped again.

    python -m stintradio studio list
    python -m stintradio studio make "<replay name>" [--no-coaching] [--start M --minutes M]
    python -m stintradio studio last [N]            (the last N races without a video yet; default 5)
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from . import housekeeping
from .config import Settings, data_dir

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DETACHED = 0x00000008 if sys.platform == "win32" else 0
VIDEO_PORT = 6421

_ROW = re.compile(r"^(?P<name>.+?)\s{2,}(?P<session>Race|Qualifying|Practice|Warm-?up|\S+)\s+(?P<minutes>[\d.]+) min\s+(?P<place>P\d+|-)\s+(?P<car>.+?)\s*$")


# ---- DriverDirector's command line -----------------------------------------------------------------------------------
def available() -> bool:
    """DriverDirector can be run from this install (from source, or installed next to it); a packaged KerbApex does not carry its
    command line."""
    return not getattr(sys, "frozen", False) and importlib.util.find_spec("driverdirector") is not None


def command(*args: str, python: Optional[str] = None) -> list:
    """The command line that runs DriverDirector's RaceRoom commands, e.g. command("list")."""
    return [python or sys.executable, "-m", "driverdirector", "raceroom", *args]


def parse_replays(text: str) -> list:
    """The rows `raceroom list` prints: name, session, minutes, finishing place and car."""
    out = []
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if m:
            d = m.groupdict()
            out.append({"name": d["name"].strip(), "session": d["session"], "minutes": float(d["minutes"]), "place": d["place"], "car": d["car"]})
    return out


def list_replays() -> list:
    r = subprocess.run(command("list"), capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW)
    return parse_replays(r.stdout)


def video_from(line: str) -> Optional[str]:
    """The finished video's path, from the `video: <path>` line the command ends with."""
    return line.split("video:", 1)[1].strip() if line.startswith("video:") else None


def estimate_minutes(replay_minutes: float, two_passes: bool = True) -> int:
    """About how long a video takes: the replay is played in real time, twice, with the game restarted between, plus the cutting."""
    return int(round(replay_minutes * (2 if two_passes else 1) + (6 if two_passes else 3) + replay_minutes * 0.6))


def place_number(place: str) -> int:
    return int(place[1:]) if place.startswith("P") and place[1:].isdigit() else 0


# ---- the second StintRadio -----------------------------------------------------------------------------------------------
def _api(port: int, method: str, path: str, body: Optional[dict] = None, timeout: float = 3.0):
    try:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=data,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except (OSError, ValueError):
        return None


def video_command(session: str, python: Optional[str] = None) -> list:
    from .gui import windowless
    return [windowless(python or sys.executable), "-m", "stintradio", "--video", session, "--port", str(VIDEO_PORT)]


def start_engineer(session: str, log: Callable[[str], None] = print) -> bool:
    """Start the second StintRadio on the recording and switch its engineer on (it waits for the game's replay)."""
    if _api(VIDEO_PORT, "GET", "/api/health") is None:
        subprocess.Popen(video_command(session), cwd=str(Path(__file__).resolve().parent.parent), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=DETACHED | NO_WINDOW)
    for _ in range(60):
        if _api(VIDEO_PORT, "GET", "/api/health") is not None:
            break
        time.sleep(0.5)
    else:
        log("The engineer for the video did not start.")
        return False
    _api(VIDEO_PORT, "POST", "/api/start", {"wait": True}, timeout=20)
    return True


def stop_engineer() -> None:
    _api(VIDEO_PORT, "POST", "/api/quit", {})
    time.sleep(1.0)
    housekeeping.clear_servers(VIDEO_PORT)


def overlay_url() -> str:
    return f"http://127.0.0.1:{VIDEO_PORT}/overlay?panel=1"


# ---- making a video ------------------------------------------------------------------------------------------------------
def find_recording(replay: dict) -> Optional[str]:
    from . import sessionreplay
    from .recorder import sessions_dir
    return sessionreplay.find_session(sessions_dir(), replay["name"], replay["minutes"], place_number(replay["place"]), replay.get("car", ""))


def make(replay: dict, coaching: bool = True, extra: Optional[list] = None, log: Callable[[str], None] = print) -> Optional[str]:
    """Make the video of one replay (a row of list_replays). Returns the finished video's path, or None."""
    session = find_recording(replay) if coaching else None
    if coaching and session is None:
        log("There is no KerbApex recording of that race (it was driven before recording began, or on another PC): "
            "making the video without the KerbApex overlay and the engineer.")
    live_was_on = _api(6420, "GET", "/api/health") is not None
    if session:
        _api(6420, "POST", "/api/stop")                 # one engineer at a time: the live one is switched off for the video
        log(f"Coaching from the recording {Path(session).name}")
        if not start_engineer(session, log):
            session = None
    cmd = command("make", replay["name"], "--restart", *(extra or []))
    if session:
        cmd += ["--kerbapex", overlay_url()]
    video = None
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=NO_WINDOW)
        for line in p.stdout:
            line = line.rstrip()
            log(line)
            video = video_from(line) or video
            if session and line.startswith("second pass"):
                stop_engineer()                         # the box of other views has no engineer and no overlay
        code = p.wait()
    finally:
        if session:
            stop_engineer()
        if live_was_on:
            _api(6420, "POST", "/api/start", {"wait": True}, timeout=20)
    return video if code == 0 else None


def last_races(n: int, replays: list, made: Optional[set] = None) -> list:
    """The newest n races (not qualifying or practice) whose names are not in `made`."""
    made = made or set()
    return [r for r in replays if r["session"] == "Race" and r["name"] not in made][:n]


def main(args: list) -> int:
    if not available():
        print("Studio needs DriverDirector next to this install (run from the source download).")
        return 2
    cmd = args[0] if args else "list"
    rest = args[1:]
    if cmd == "list":
        for r in list_replays():
            rec = "recorded" if find_recording(r) else "no recording"
            print(f"{r['name']:60} {r['session']:11} {r['minutes']:5.1f} min {r['place']:>4}  {rec}")
        return 0
    extra = [a for a in rest if a in ("--stay", "--no-overlay", "--no-inset")]
    for flag in ("--start", "--minutes"):
        if flag in rest:
            extra += [flag, rest[rest.index(flag) + 1]]
    coaching = "--no-coaching" not in rest
    if cmd == "make" and rest:
        r = next((x for x in list_replays() if x["name"] == rest[0]), None)
        if r is None:
            print(f"No replay called {rest[0]!r}. Try: python -m stintradio studio list")
            return 2
        v = make(r, coaching, extra)
        print(f"video: {v}" if v else "no video was made")
        return 0 if v else 1
    if cmd == "last":
        n = int(rest[0]) if rest and rest[0].isdigit() else 5
        done = 0
        for r in last_races(n, list_replays()):
            print(f"\n=== {r['name']} ===", flush=True)
            v = make(r, coaching, extra)
            if not v:
                print("stopped: that one did not finish", flush=True)
                return 1
            done += 1
            print(f"video: {v}", flush=True)
        return 0
    print(__doc__)
    return 2
