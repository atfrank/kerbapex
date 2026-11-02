"""RaceRoom Racing Experience: replay library, game control, recording and finishing.

RaceRoom has no replay API. What it offers instead, all verified live on 2026-10-26:

* `ReplayData/*.Vcr` files whose header carries the metadata (track, session, car, duration, finish, player);
* the game's own replay screen: pick a replay from the menu list (film icon, the search box filters by file name),
  then keys: Space play/pause, Insert driving cams, Home onboard, Page Down external, End cycle external, Page Up chase,
  H hides the replay bar, Esc leaves the replay;
* `UserData/Player1/SessionReports/*.json` (since 24 Oct 2026) with the lap times, positions, weather and time of day.

The mode records the replay in real time through OBS while cycling the camera groups, then adds an opening title and
fades. There is no race data in the picture (the game's shared memory is not filled during a replay), and no commentary.
Nothing here touches the game unless a method that says so is called; reading the library never does.
"""
from __future__ import annotations

import bisect
import ctypes
import json
import os
import random
import re
import struct
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .config import RaceRoomSettings

# ---- library --------------------------------------------------------------------------------------------------------
GAME_DIR = Path("My Games") / "SimBin" / "RaceRoom Racing Experience"


def game_data_dir() -> Path:
    """The folder with ReplayData and UserData (it follows Documents, which OneDrive may move)."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    cands = [Path(os.environ[v]) / "Documents" for v in ("OneDriveConsumer", "OneDrive") if os.environ.get(v)]
    cands += [home / "OneDrive" / "Documents", home / "Documents"]
    for c in cands:
        if (c / GAME_DIR).is_dir():
            return c / GAME_DIR
    raise FileNotFoundError("RaceRoom's data folder (Documents\\My Games\\SimBin\\RaceRoom Racing Experience) not found")


@dataclass
class ReplayInfo:
    path: Path
    name: str                         # file name without .Vcr: what the game's list shows and its search matches
    track: str = ""
    layout: str = ""
    session: str = ""                 # Practice | Qualifying | Race ... as the game names it
    race_mode: str = ""               # e.g. Leaderboard Challenge, Race
    car: str = ""
    car_class: str = ""
    driver: str = ""
    duration: float = 0.0             # seconds
    finish: int = 0
    players: int = 0
    timestamp: int = 0                # epoch seconds when the recording began
    all_time_best: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d") if self.timestamp else ""

    def as_dict(self) -> dict:
        return {"name": self.name, "track": self.track, "layout": self.layout, "session": self.session,
                "race_mode": self.race_mode, "car": self.car, "car_class": self.car_class, "driver": self.driver,
                "duration": round(self.duration, 1), "finish": self.finish, "players": self.players,
                "date": self.date, "size_mb": round(self.path.stat().st_size / 1048576, 1),
                "all_time_best": self.all_time_best}


def read_header(path: str | Path) -> dict:
    """The key/value strings at the start of a .Vcr file, plus `_driver` (the recorded driver's full name)."""
    with open(path, "rb") as f:
        b = f.read(16384)
    i = b.find(b"\x09\x00\x00\x00car_class")
    if b[:4] != b"IRSR" or i < 0:
        raise ValueError(f"not a RaceRoom replay: {path}")
    out: dict = {}
    while i + 8 <= len(b):
        n = struct.unpack_from("<I", b, i)[0]
        if not 1 <= n <= 64:
            break
        j = i + 4 + n
        if j + 4 > len(b):
            break
        m = struct.unpack_from("<I", b, j)[0]
        if m > 512:
            break
        try:
            out[b[i + 4:j].decode("ascii")] = b[j + 4:j + 4 + m].decode("utf-8")
        except UnicodeDecodeError:
            break
        i = j + 4 + m
    m = re.search(rb"[\x00-\x20]([A-Z][A-Za-z.'\-]+ [A-Z][A-Za-z.'\- ]{1,28}?)[\x01-\x20][A-Z]", b[i:i + 80])
    out["_driver"] = m.group(1).decode("ascii", "replace") if m else ""
    return out


def _num(v, cast=float, default=0):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def replay_info(path: str | Path) -> ReplayInfo:
    p = Path(path)
    h = read_header(p)
    return ReplayInfo(
        path=p, name=p.stem, track=h.get("track_name", ""), layout=h.get("track_layout", ""),
        session=h.get("session", ""), race_mode=h.get("race_mode", ""), car=h.get("car_model", ""),
        car_class=h.get("car_class", ""), driver=h["_driver"], duration=_num(h.get("duration"), float, 0.0),
        finish=_num(h.get("finish_position"), int, 0), players=_num(h.get("player_count"), int, 0),
        timestamp=_num(h.get("timestamp"), int, 0), all_time_best=p.stem.startswith("AllTimeBest"), meta=h)


def list_replays(folder: Path | None = None, include_best: bool = True) -> list[ReplayInfo]:
    """Every replay in the library, newest first."""
    d = folder or game_data_dir() / "ReplayData"
    out = []
    for p in d.glob("*.Vcr"):
        try:
            r = replay_info(p)
        except (OSError, ValueError):
            continue
        if include_best or not r.all_time_best:
            out.append(r)
    out.sort(key=lambda r: (r.timestamp or r.path.stat().st_mtime), reverse=True)
    return out


def find_replay(query: str, replays: list[ReplayInfo] | None = None) -> ReplayInfo:
    """One replay by (part of) its name; an exact name wins. Raises if none or several match."""
    rs = replays if replays is not None else list_replays()
    q = query.lower().removesuffix(".vcr")
    exact = [r for r in rs if r.name.lower() == q]
    hits = exact or [r for r in rs if q in r.name.lower()]
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} replays match {query!r}" + (": " + ", ".join(r.name for r in hits[:5]) if hits else ""))
    return hits[0]


@dataclass
class SessionReport:
    path: Path
    data: dict

    @property
    def weather(self) -> dict:
        return {**self.data.get("weather", {}), "mood": self.data.get("track", {}).get("mood", ""),
                "time_of_day": self.data.get("track", {}).get("timeOfDay", "")}

    def driver(self, name: str = "") -> dict | None:
        ds = self.data.get("drivers", [])
        last = name.split()[-1].lower() if name else ""
        return (next((d for d in ds if last and d.get("name", "").lower().endswith(last)), None)
                or next((d for d in ds if d.get("isPlayer")), None))


def find_report(info: ReplayInfo, folder: Path | None = None) -> Optional[SessionReport]:
    """The session report written when this replay's session ended (same track, finished shortly after it began)."""
    d = (folder or game_data_dir()) / "UserData" / "Player1" / "SessionReports"
    if not d.is_dir() or not info.timestamp:
        return None
    for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        dt = data.get("createdAt", 0) - info.timestamp
        if data.get("track", {}).get("trackName") == info.track and 0 <= dt <= info.duration + 900:
            return SessionReport(p, data)
    return None


def lap_text(ms: int) -> str:
    return f"{ms // 60000}:{ms % 60000 / 1000:06.3f}" if ms and ms > 0 else ""


# ---- camera plan ----------------------------------------------------------------------------------------------------
# The replay's own camera keys (scan code, extended); pressing a key again moves to the next camera of that kind.
CAMERA_KEYS = {"driving": "INS", "onboard": "HOME", "external": "PGDN", "cycle": "END", "chase": "PGUP"}


# How many cameras a key steps through. The replay bar's Select Camera list names them all (read 2026-10-27): Onboard 1-10,
# Driving Nose/Hood/Cockpit/Chase, External Static/Aerial/Action/TV, Free cameras (moving with car, attached, free flight).
# Switching to a group starts at its first camera and every further press moves to the next. External and chase (Page Up =
# the driving group's Chase, no longer used: chase views are left out) are one view each; the End key cycles the four external cameras.
CAMERA_VARIANTS = {"driving": 3, "onboard": 10, "cycle": 4, "external": 1, "chase": 1}
# The first onboard cameras look down an empty track from the car; from the third press on they show wheels and bodywork.
CAMERA_FIRST = {"onboard": 3}
# The driving group, in the order Insert steps through it (tested 2026-10-28; not the order the replay bar lists them):
# 1 Hood, 2 Nose, 3 Chase, 4 Cockpit, then round again. Chase is never a shot, it only flashes by on the way to the cockpit.
DRIVING_CAMERAS = 4
CAMERA_ANGLES = {"driving": [4]}                # the Cockpit only: the view from inside the car (the Hood view is outside it)


@dataclass
class Cue:
    t: float                          # seconds into the recording
    group: str
    key: str
    presses: int = 1                  # presses of the key, to reach this camera of the group


def camera_plan(duration: float, cfg: RaceRoomSettings, seed: int = 1, weights: dict | None = None) -> list[Cue]:
    """Camera changes across `duration` seconds: weighted groups, shots of about shot_s, never the same group twice in a row
    (unless it is the only one), the opening on an external camera. A group deals out its angles in shuffled rounds, so an
    angle comes back only after all the others of its group were used; a group with a single view (external, chase) cannot
    vary, so it is kept out of the next few shots after each use."""
    rng = random.Random(seed)
    groups = {g: w for g, w in (weights or cfg.weights).items() if w > 0 and g in CAMERA_KEYS}
    if not groups:
        raise ValueError("every camera group has weight 0")
    angles = {g: CAMERA_ANGLES.get(g) or list(range(CAMERA_FIRST.get(g, 1), CAMERA_VARIANTS.get(g, 1) + 1)) for g in groups}
    bags: dict[str, list[int]] = {g: [] for g in groups}
    last_angle: dict[str, int] = {}

    def deal(g: str) -> int:
        if not bags[g]:
            bag = angles[g][:]
            rng.shuffle(bag)
            if len(bag) > 1 and bag[-1] == last_angle.get(g):       # the next round must not open on the angle just shown
                bag[0], bag[-1] = bag[-1], bag[0]
            bags[g] = bag
        last_angle[g] = bags[g].pop()
        return last_angle[g]

    cues: list[Cue] = []
    t = 0.0
    while t < duration - cfg.min_shot:
        recent = [c.group for c in cues[-4:]]
        pool = {g: w for g, w in groups.items() if g != (recent[-1] if recent else "")
                and not (len(angles[g]) == 1 and g in recent)} or {g: w for g, w in groups.items() if len(angles[g]) > 1} or groups
        g = "external" if not cues and "external" in groups else rng.choices(list(pool), list(pool.values()))[0]
        cues.append(Cue(round(t, 2), g, CAMERA_KEYS[g], deal(g)))
        t += max(cfg.min_shot, cfg.shot_s * rng.uniform(0.8, 1.2))
    return cues


# ---- window, keys and clicks ----------------------------------------------------------------------------------------
if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetProcessDPIAware()                   # real pixels (3440x1440), not the scaled desktop
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.GetPixel.restype = wintypes.DWORD
    gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]

SCAN = {"ESC": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "H": 0x23, "SPACE": 0x39, "F1": 0x3B, "NUMPLUS": 0x4E,
        "NUMMINUS": 0x4A, "PGUP": (0x49, True), "PGDN": (0x51, True), "LEFT": (0x4B, True), "RIGHT": (0x4D, True),
        "UP": (0x48, True), "DOWN": (0x50, True), "INS": (0x52, True), "HOME": (0x47, True), "END": (0x4F, True),
        "ENTER": 0x1C, "BACKSPACE": 0x0E, "CTRL": 0x1D, "SHIFT": 0x2A}


class _KB(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KB), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class GameError(RuntimeError):
    pass


class RaceRoomGame:
    """Finds the RaceRoom window and drives it with real key presses and clicks. The window must be in front for any
    input, so every method brings it forward first and refuses to send input if it cannot."""

    # positions on the menu screens as fractions of the window (measured on a 3440x1440 window)
    LOGO = (0.225, 0.085)                         # the RaceRoom logo: back to the main menu from any page
    ICON_REPLAYS = (0.797, 0.083)                 # film icon in the top bar of the menu
    SEARCH = (0.509, 0.083)                       # the replay list's search box
    FIRST_PLAY = (0.2065, 0.203)                   # play arrow of the first row

    def __init__(self, process: str = "RRRE64.exe"):
        self.process = process
        self.hwnd = None
        self.driving_at: Optional[int] = None         # the driving camera the game shows now; None when another group is on

    # -- window
    def running(self) -> bool:
        out = os.popen(f'tasklist /FI "IMAGENAME eq {self.process}" /NH').read()
        return self.process.lower() in out.lower()

    def find_window(self):
        pids = {int(x) for x in os.popen(
            f'powershell -NoProfile -c "(Get-Process {Path(self.process).stem}).Id"').read().split()}
        found = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(h, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(h):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, buf, 256)
                if "RaceRoom" in buf.value:
                    found.append(h)
            return True
        user32.EnumWindows(proc(cb), 0)
        self.hwnd = found[0] if found else None
        if not self.hwnd:
            raise GameError("the RaceRoom window was not found (is the game running?)")
        return self.hwnd

    def front(self) -> bool:
        h = self.hwnd or self.find_window()
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)
            time.sleep(0.8)
        if user32.GetForegroundWindow() != h:
            user32.keybd_event(0x12, 0, 0, 0)     # a tap of Alt lets SetForegroundWindow through
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(h)
            time.sleep(0.4)
        return user32.GetForegroundWindow() == h

    def need_front(self) -> None:
        for _ in range(4):                        # OBS or another window may grab the focus for a moment
            if self.front():
                return
            time.sleep(0.5)
        raise GameError("could not bring RaceRoom to the front (another window has focus)")

    def client_rect(self) -> tuple[int, int, int, int]:
        h = self.hwnd or self.find_window()
        r = wintypes.RECT()
        user32.GetClientRect(h, ctypes.byref(r))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(h, ctypes.byref(pt))
        return pt.x, pt.y, r.right, r.bottom

    # -- input
    def press(self, name: str, hold: float = 0.08) -> None:
        self.need_front()
        code = SCAN[name]
        ext = False
        if isinstance(code, tuple):
            code, ext = code
        flags = 0x0008 | (0x0001 if ext else 0)    # KEYEVENTF_SCANCODE (| EXTENDEDKEY)
        for f in (flags, flags | 0x0002):
            inp = _INPUT(type=1, ki=_KB(0, code, f, 0, 0))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            time.sleep(hold)

    def camera(self, cue) -> None:
        """Switch to the cue's camera: its key, pressed once per step through the group (the steps flash by quickly)."""
        presses = max(1, cue.presses)
        if cue.group == "driving":
            # Insert steps Hood, Nose, Chase, Cockpit and wraps (tested 2026-10-28), counted from the first after another
            # group was on (Home puts it there). Already in the group, it goes on from where it is.
            if self.driving_at is None:
                self.press("HOME", hold=0.03)
                time.sleep(0.05)
            else:
                presses = (cue.presses - self.driving_at) % DRIVING_CAMERAS
                if not presses:
                    return
            self.driving_at = cue.presses
        else:
            self.driving_at = None
        for n in range(presses):
            if n:
                time.sleep(0.05)                      # quick taps: the angles on the way flash by in a few frames
            self.press(cue.key, hold=0.03)

    def type_text(self, text: str) -> None:
        self.need_front()
        for ch in text:
            for f in (0x0004, 0x0004 | 0x0002):    # KEYEVENTF_UNICODE down, up
                inp = _INPUT(type=1, ki=_KB(0, ord(ch), f, 0, 0))
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            time.sleep(0.03)

    def click_at(self, fx: float, fy: float) -> None:
        self.need_front()
        x, y, w, h = self.client_rect()
        user32.SetCursorPos(int(x + fx * w), int(y + fy * h))
        time.sleep(0.25)
        user32.mouse_event(2, 0, 0, 0, 0)
        time.sleep(0.08)
        user32.mouse_event(4, 0, 0, 0, 0)

    def quit(self) -> None:
        """Close the game. Leaving a replay with Esc and Enter can stick on its "Exit replay?" dialog, and a replay loops
        for ever, so a finished run just ends the process; the next run starts a fresh game anyway."""
        if self.running():
            os.system(f'taskkill /IM {self.process} /F >nul 2>&1')
            time.sleep(3)
        self.hwnd = None

    def restart(self, settle: float = 50.0) -> None:
        """Quit the game and start it again from Steam, to the main menu. Exiting a replay and loading another one
        re-opens the first, so every replay gets a fresh game."""
        self.driving_at = None
        if self.running():
            os.system(f'taskkill /IM {self.process} /F >nul 2>&1')
            time.sleep(6)
        self.hwnd = None
        t_end = time.monotonic() + 240
        t_ask = 0.0
        asked = 0
        while True:
            if time.monotonic() >= t_ask:               # Steam sometimes lets one request go by: ask again every 50 s
                if asked % 2 == 0 or not _steam_exe():
                    os.startfile("steam://rungameid/211500")
                else:                                   # the other way in, in case the URL handler is stuck
                    subprocess.Popen([_steam_exe(), "-applaunch", "211500"])
                asked += 1
                t_ask = time.monotonic() + 50
            try:
                self.find_window()
                break
            except GameError:
                if time.monotonic() > t_end:
                    raise GameError("RaceRoom did not start")
                time.sleep(3)
        time.sleep(settle)                           # intro videos and news page

    # -- what is on screen
    def frame_sample(self, cols: int = 24, rows: int = 10) -> list[int]:
        """A grid of screen pixels over the game window, to tell a moving picture from a frozen one."""
        x, y, w, h = self.client_rect()
        dc = user32.GetDC(None)
        try:
            return [gdi32.GetPixel(dc, int(x + w * (i + 0.5) / cols), int(y + h * 0.75 * (j + 0.5) / rows))
                    for j in range(rows) for i in range(cols)]
        finally:
            user32.ReleaseDC(None, dc)

    def moving(self, gap: float = 1.0) -> bool:
        """True if the picture changes over `gap` seconds. A paused replay is pixel-identical; a running one never is
        (flags, clouds, engines). The replay bar sits below the sampled area."""
        a = self.frame_sample()
        time.sleep(gap)
        b = self.frame_sample()
        return sum(1 for p, q in zip(a, b) if p != q) >= 5

    def start_playing(self, timeout: float = 90.0) -> None:
        """Press Space until the replay is running. Space is ignored while the replay still loads (its tips screen can
        last well beyond the usual settle on a full grid), so keep trying; a press on a running replay would pause it,
        so test the picture before every press."""
        t_end = time.monotonic() + timeout
        # A replay that just finished loading is still fading in, which looks like motion: wait for a still, paused picture
        # (two quiet checks in a row) before judging, or a replay that never started would pass for a running one.
        quiet, t_wait = 0, time.monotonic() + 45
        while quiet < 2 and time.monotonic() < t_wait:
            quiet = 0 if self.moving() else quiet + 1
        first = True
        while time.monotonic() < t_end:
            if not (first and quiet >= 2) and self.moving():     # a picture that never went still is a replay playing
                return                                           # by itself; later rounds: an earlier press landed late
            first = False
            self.press("SPACE")
            time.sleep(2.5)
            if self.moving():
                return
            time.sleep(2.0)
        raise GameError("the replay did not start playing")

    # -- replay screens
    def load_replay(self, name: str, settle: float = 12.0) -> None:
        """From the main menu: open the replay list, filter it to `name` and play the first row. The replay opens paused."""
        self.need_front()
        self.click_at(*self.LOGO)                    # the game starts on the page last left open (settings, for one)
        time.sleep(2.5)
        self.click_at(*self.ICON_REPLAYS)
        time.sleep(2.5)
        self.click_at(*self.SEARCH)
        time.sleep(0.5)
        self.type_text(name)
        time.sleep(1.5)
        self.click_at(*self.FIRST_PLAY)
        time.sleep(settle)

    def exit_replay(self, settle: float = 10.0) -> None:
        """Esc asks "Exit replay?" with Exit highlighted; Enter confirms. Load the next replay only after the settle."""
        self.press("ESC")
        time.sleep(1.5)
        self.press("ENTER")
        time.sleep(settle)


# ---- recording ------------------------------------------------------------------------------------------------------
class Cancelled(RuntimeError):
    pass


@dataclass
class Recording:
    raw: str
    offset: float                     # seconds of the raw file before the replay started playing
    duration: float                   # seconds of replay recorded
    plan: list[Cue]
    marks: list = field(default_factory=list)   # (seconds into the raw file, the driver's race progress): lines two passes up


def record(info: ReplayInfo, game: RaceRoomGame, obs, cfg: RaceRoomSettings, load: bool = True,
           progress: Callable[[float, str], None] = lambda f, m: None,
           cancelled: Callable[[], bool] = lambda: False, seed: int = 1, out_dir: str | None = None,
           telemetry=None, live=None, weights: dict | None = None) -> Recording:
    """Play the replay in real time while OBS records it and the camera groups cycle. `obs` is driverdirector.obs.Obs.
    The game must be at its main menu (or `load=False` and the replay already open and paused at the start).
    A replay loops instead of stopping, so with `telemetry` (raceroom_telemetry.R3EMemory) the recording also ends the
    moment the replay wraps back to its start, and the loop is cut off. With `live` (raceroom_overlay.LiveOverlay) the same
    readings feed the leaderboard overlay that OBS shows on top of the picture."""
    from . import raceroom_telemetry as tel
    t0 = max(0.0, cfg.t_start)
    end = min(info.duration, cfg.t_end) if cfg.t_end else info.duration
    length = end - t0
    if length < 10:
        raise ValueError("nothing to record: the range is shorter than 10 seconds")
    if load:
        progress(0.0, f"loading {info.name}")
        game.load_replay(info.name)
    game.need_front()
    if t0 >= 6:
        _skip(game, t0)                            # the replay is paused, so the skips land exactly
    plan = camera_plan(length, cfg, seed, weights)
    marks: list = []
    obs.set_scene(cfg.scene)
    time.sleep(1.0)
    obs.start_record(out_dir)
    rec_t = time.monotonic()
    try:
        time.sleep(1.5)
        game.camera(plan[0])
        game.start_playing()
        if cfg.hide_ui:                            # H is only taken once the replay runs; the moment with the bar is cut off
            time.sleep(1.5)
            game.press("H")
            time.sleep(1.0)
        t_play = time.monotonic()
        offset = t_play - rec_t
        state = {"frame": telemetry.read() if telemetry else None, "wrap": None}

        def wrapped() -> bool:
            if not telemetry:
                return False
            frame = telemetry.read()
            if tel.wrapped(state["frame"], frame):
                state["wrap"] = time.monotonic()
                return True
            state["frame"] = frame
            p = _focus_progress(frame, cfg.driver)
            if p is not None:
                marks.append((time.monotonic() - rec_t, p))
            if live:
                live.update(frame, time.monotonic())
            return False

        for i, cue in enumerate(plan):
            if i:
                if _wait_until(t_play + cue.t, cancelled, wrapped):
                    break
                game.camera(cue)
            progress(min(0.99, (time.monotonic() - t_play) / length), f"{cue.group} camera")
        else:
            _wait_until(t_play + length, cancelled, wrapped)
        game.press("SPACE")                         # pause
        if state["wrap"]:                           # the replay ran out first: keep what was real, drop the restart
            length = max(10.0, min(length, state["wrap"] - t_play - 1.0))
    finally:
        try:
            raw = obs.stop_record()
        except Exception:
            raw = ""
        if cfg.hide_ui:
            try:
                game.press("H")                     # bring the bar back for whoever uses the game next
            except GameError:
                pass
    progress(1.0, "recorded")
    return Recording(raw, offset, length, plan, marks)


def _focus_progress(frame, driver: str) -> Optional[float]:
    """How far the driver is into the race (laps and distance, in the overlay's units), or None if not in the telemetry."""
    from .raceroom_overlay import progress, track_length
    last = driver.split()[-1].lower() if driver.split() else ""
    d = next((d for d in frame.drivers if last and d.name.lower().endswith(last)), None)
    return progress(d.laps, d.lap_distance, track_length(frame)) if d else None


def align(main: Recording, inset: Recording, anchor: float = 30.0) -> tuple[float, float, float]:
    """Where to cut two recordings of the same replay so they show the same moment: (start in the main raw file, start in the
    inset raw file, seconds to use). Each pass starts playing a second or two off the other, so the driver's progress marks
    tell how far: the inset time at which the driver reached where the main picture is `anchor` seconds in."""
    start1, start2 = main.offset, inset.offset
    dur = min(main.duration, inset.duration)
    end1, end2 = main.offset + main.duration, inset.offset + inset.duration
    m1 = [m for m in main.marks if m[0] <= end1]
    m2 = [m for m in inset.marks if m[0] <= end2]
    if len(m1) < 10 or len(m2) < 10:
        return start1, start2, dur
    a1 = start1 + min(anchor, main.duration / 2)
    p1 = [p for _, p in m1]
    j = max(1, bisect.bisect_left([t for t, _ in m1], a1))
    j = min(j, len(m1) - 1)
    (t0, q0), (t1, q1) = m1[j - 1], m1[j]
    target = q0 + (q1 - q0) * ((a1 - t0) / (t1 - t0) if t1 > t0 else 0.0)
    ps = [p for _, p in m2]
    if not ps[0] <= target <= ps[-1] or any(b < a - 0.5 for a, b in zip(ps, ps[1:])):
        return start1, start2, dur                 # not usable (a restart in the middle, the driver not found)
    k = max(1, min(bisect.bisect_left(ps, target), len(m2) - 1))
    (u0, r0), (u1, r1) = m2[k - 1], m2[k]
    a2 = u0 + (u1 - u0) * ((target - r0) / (r1 - r0) if r1 > r0 else 0.0)
    ideal = a2 - (a1 - start1)                     # the inset's raw time that matches the main picture's first moment
    shift = max(0.0, start2 - ideal)               # the inset has nothing before it played: start the main picture later
    inset_start, main_start = ideal + shift, start1 + shift
    return main_start, inset_start, max(10.0, min(end1 - main_start, end2 - inset_start))


def _skip(game: RaceRoomGame, seconds: float) -> None:
    """Skip forward `seconds` in a paused replay with Shift+Right (6 s each)."""
    n = int(seconds // 6)
    game.need_front()
    for _ in range(n):
        for name, down in (("SHIFT", True), ("RIGHT", True), ("RIGHT", False), ("SHIFT", False)):
            code = SCAN[name]
            ext = isinstance(code, tuple)
            code = code[0] if ext else code
            flags = 0x0008 | (0x0001 if ext else 0) | (0 if down else 0x0002)
            inp = _INPUT(type=1, ki=_KB(0, code, flags, 0, 0))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            time.sleep(0.08)
        time.sleep(0.15)


def _steam_exe() -> str:
    """steam.exe, from the registry, or '' when it cannot be found."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            exe = winreg.QueryValueEx(k, "SteamExe")[0]
        return exe if os.path.isfile(exe) else ""
    except (OSError, ImportError):
        return ""


def _wait_until(t: float, cancelled: Callable[[], bool], stop: Callable[[], bool] = lambda: False) -> bool:
    """Wait for the monotonic time `t`; True if `stop()` ended the wait early."""
    while True:
        left = t - time.monotonic()
        if cancelled():
            raise Cancelled("cancelled")
        if left <= 0:
            return False
        if stop():
            return True
        time.sleep(min(0.25, left))


# ---- finishing and publishing ---------------------------------------------------------------------------------------
def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def title_lines(info: ReplayInfo, driver: str = "") -> list[str]:
    who = driver or info.driver or "Driver"
    place = f" - P{info.finish}" if info.finish > 0 and info.session.lower() == "race" else ""
    return [who, f"{info.track} {info.layout}".strip(), f"{info.session}{place} - {info.car}".strip(" -")]


def final_name(info: ReplayInfo, driver: str = "") -> str:
    return f"{slug(info.track)}_{info.date or 'replay'}_{slug(info.session or 'session')}_{slug(driver or info.driver or 'driver')}.mp4"


def finish(info: ReplayInfo, rec: Recording, cfg: RaceRoomSettings, out_dir: str, driver: str = "",
           progress: Callable[[float, str], None] | None = None, inset: Recording | None = None) -> str:
    """Trim to the replay, add the opening title and the fades: the finished video. With `inset` (a second pass over the
    same replay, other cameras) it is shown in a box in the top right corner, cut to the same moment as the main picture."""
    main_start, inset_start, dur = (rec.offset, 0.0, rec.duration) if inset is None else align(rec, inset)
    from . import compose
    driver = driver or cfg.driver
    out = str(Path(out_dir) / final_name(info, driver))
    n = 2
    while Path(out).exists():
        out = str(Path(out_dir) / final_name(info, driver).replace(".mp4", f" ({n}).mp4"))
        n += 1
    extra = {} if inset is None else {"inset": inset.raw, "inset_offset": inset_start, "inset_size": cfg.inset_size}
    compose.compose_cinematic(rec.raw, out, main_start, dur, None, progress=progress, letterbox=0.0,
                              grade="none", fade_s=cfg.fade_s, title=title_lines(info, driver) if cfg.title_s else (),
                              title_s=cfg.title_s, game_level_db=0.0, **extra)
    return out


def draft(info: ReplayInfo, cfg: RaceRoomSettings, yt, report: SessionReport | None = None, driver: str = "",
          length: float = 0.0) -> dict:
    """YouTube title, description and tags for the finished video (`yt` is the YouTubeSettings)."""
    from . import youtube
    who = driver or cfg.driver or info.driver or "Driver"
    session = info.session or "session"
    facts = []
    if info.finish > 0 and session.lower() == "race":
        facts.append(f"Finished P{info.finish}" + (f" of {info.players}" if info.players > 1 else "") + ".")
    me = report.driver(who) if report else None
    if me and me.get("bestLapTimeMs", -1) > 0:
        facts.append(f"Best lap {lap_text(me['bestLapTimeMs'])}.")
    if report:
        w = report.weather
        bits = [x for x in (w.get("time_of_day", ""), str(w.get("mood", "")).replace("_", " "),
                            f"{w['ambientTempC']} C air" if "ambientTempC" in w else "") if x]
        if bits:
            facts.append("Conditions: " + ", ".join(bits) + ".")
    facts.append(f"Car: {info.car}.")
    d = youtube.draft_metadata(yt, who, info.track, session, [], length, " ".join(facts), music=False, voice=False,
                               game="RaceRoom Racing Experience")
    d["title"] = cfg.title_template.format(driver=who, track=info.track, session=session)[:100]
    d["tags"] = list(cfg.tags)
    return d


# ---- OBS scene ------------------------------------------------------------------------------------------------------
WINDOW_ID = "RaceRoom Racing Experience:One:RRRE64.exe"      # OBS game-capture window id: title:class:exe


def kerbapex_layout(base_w: float, base_h: float, size: float, corner: str, margin: float = 0.02) -> dict:
    """Where the KerbApex overlay goes: its box (the overlay is drawn 32:21) and the OBS alignment of the chosen corner."""
    w = base_w * size
    h = w * 21 / 32
    top, left = corner.startswith("top"), corner.endswith("left")
    m = base_w * margin
    return {"w": int(w), "h": int(h), "x": m if left else base_w - m, "y": m if top else base_h - m,
            "alignment": (1 if left else 2) | (4 if top else 8)}       # OBS: left 1, right 2, top 4, bottom 8


def ensure_kerbapex(obs, cfg: RaceRoomSettings, url: str) -> None:
    """The KerbApex overlay as a browser source of the scene, in the chosen corner, on top. `url` is its page."""
    c = obs._req()
    v = c.get_video_settings()
    g = kerbapex_layout(v.base_width, v.base_height, cfg.kerbapex_size, cfg.kerbapex_corner)
    settings = {"url": url, "width": g["w"], "height": g["h"], "fps": 30, "reroute_audio": False, "shutdown": False,
                "restart_when_active": False}
    inputs = [i["inputName"] for i in c.get_input_list().inputs]
    items = [i["sourceName"] for i in c.get_scene_item_list(cfg.scene).scene_items]
    if cfg.kerbapex_source not in inputs:
        c.create_input(cfg.scene, cfg.kerbapex_source, "browser_source", settings, True)
    else:
        c.set_input_settings(cfg.kerbapex_source, settings, True)
        if cfg.kerbapex_source not in items:
            c.create_scene_item(cfg.scene, cfg.kerbapex_source, True)
    item = c.get_scene_item_id(cfg.scene, cfg.kerbapex_source).scene_item_id
    c.set_scene_item_transform(cfg.scene, item, {"positionX": g["x"], "positionY": g["y"], "alignment": g["alignment"],
                                                 "boundsType": "OBS_BOUNDS_NONE"})
    c.set_scene_item_enabled(cfg.scene, item, True)
    c.set_scene_item_index(cfg.scene, item, 98)


def ensure_scene(obs, cfg: RaceRoomSettings, overlay_url: str = "") -> list[str]:
    """Create the RaceRoom scene in OBS if it is missing: a game capture of the RaceRoom window scaled to the canvas, plus
    the shared game-audio source when there is one, plus the overlay browser source when `overlay_url` is given. Existing
    scenes and sources are left alone (the overlay's address is refreshed). Returns what was added."""
    c = obs._req()
    added = []
    if cfg.scene not in [s["sceneName"] for s in c.get_scene_list().scenes]:
        c.create_scene(cfg.scene)
        added.append(f"scene {cfg.scene}")
    inputs = [i["inputName"] for i in c.get_input_list().inputs]
    items = [i["sourceName"] for i in c.get_scene_item_list(cfg.scene).scene_items]
    if cfg.capture_source not in inputs:
        c.create_input(cfg.scene, cfg.capture_source, "game_capture",
                       {"capture_mode": "window", "window": WINDOW_ID, "priority": 2, "capture_cursor": False}, True)
        added.append(f"source {cfg.capture_source}")
    elif cfg.capture_source not in items:
        c.create_scene_item(cfg.scene, cfg.capture_source, True)
    if cfg.audio_source in inputs and cfg.audio_source not in items:
        c.create_scene_item(cfg.scene, cfg.audio_source, True)
        added.append(f"audio {cfg.audio_source}")
    v = c.get_video_settings()
    item = c.get_scene_item_id(cfg.scene, cfg.capture_source).scene_item_id
    c.set_scene_item_transform(cfg.scene, item, {
        "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0, "boundsWidth": float(v.base_width),
        "boundsHeight": float(v.base_height), "positionX": v.base_width / 2, "positionY": v.base_height / 2,
        "alignment": 0})
    if overlay_url:
        settings = {"url": overlay_url, "width": int(v.base_width), "height": int(v.base_height), "fps": 30,
                    "reroute_audio": False, "shutdown": False, "restart_when_active": False}
        if cfg.overlay_source not in inputs:
            c.create_input(cfg.scene, cfg.overlay_source, "browser_source", settings, True)
            added.append(f"source {cfg.overlay_source}")
        else:
            c.set_input_settings(cfg.overlay_source, settings, True)
            if cfg.overlay_source not in items:
                c.create_scene_item(cfg.scene, cfg.overlay_source, True)
        top = c.get_scene_item_id(cfg.scene, cfg.overlay_source).scene_item_id
        c.set_scene_item_enabled(cfg.scene, top, True)
        c.set_scene_item_index(cfg.scene, top, 99)                    # on top of the game picture
    return added
