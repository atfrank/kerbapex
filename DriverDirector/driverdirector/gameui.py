"""Show/hide LMU's replay screen UI (toolbar, standings, timeline, HUD widgets) the way the game does it.

The game's own UI hides everything on a MIDDLE MOUSE click (the toolbar's "Toggle UI" label has a
mouse-middle icon): its handler flips the web UI *and* posts `replay/setReplayUIVisible` +
`hud/toggleAllComponents`. Calling those endpoints alone changes the game's state but not what is drawn,
so we send the same middle click to the game window.

Safety: the click is only sent after the game window is verified to be the foreground window and the
cursor is over it; the original cursor position and foreground window are restored afterwards.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Callable, Optional

from .lmu_api import LMUApiError, LMUClient

PROCESS = "Le Mans Ultimate.exe"

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]

MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002


def _exe_of(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        return buf.value.rsplit("\\", 1)[-1] if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def find_game_window() -> Optional[int]:
    """The visible top-level window of Le Mans Ultimate (the largest one if there are several)."""
    found: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) and _exe_of(hwnd).lower() == PROCESS.lower():
            r = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(r))
            found.append((r.right * r.bottom, hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return max(found)[1] if found else None


def _bring_to_front(hwnd, timeout: float = 2.0) -> bool:
    """Make the game the foreground window. Windows only lets the foreground process do that, so try an Alt tap
    first, then attach to the foreground window's input thread (the documented way round the restriction)."""
    if user32.GetForegroundWindow() == hwnd:
        return True
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                                    # SW_RESTORE
    user32.keybd_event(VK_MENU, 0, 0, 0)                          # an Alt tap lets SetForegroundWindow through
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    end = time.time() + timeout
    while time.time() < end:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    fg = user32.GetForegroundWindow()
    me, them = kernel32.GetCurrentThreadId(), user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(them) and them != me and user32.AttachThreadInput(me, them, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, them, False)
    end = time.time() + timeout
    while time.time() < end:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return False


def middle_click_game() -> None:
    """Middle-click the centre of the game window (verified foreground), then restore cursor and focus."""
    if sys.platform != "win32":
        raise OSError("only supported on Windows")
    hwnd = find_game_window()
    if not hwnd:
        raise RuntimeError("Le Mans Ultimate window not found")
    prev_fg, prev_pos = user32.GetForegroundWindow(), wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(prev_pos))
    if not _bring_to_front(hwnd):
        raise RuntimeError(f"could not bring the game window to the front: {_exe_of(user32.GetForegroundWindow()) or 'another window'} is holding focus "
                           "(close it, e.g. Windows search or a dialog); no click was sent")
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wintypes.POINT(r.right // 2, r.bottom // 2)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    for dx in (0, 8, -8, 0):                                       # the game's web UI ignores a click with no hover before it
        user32.SetCursorPos(pt.x + dx, pt.y)
        time.sleep(0.08)
    time.sleep(0.2)
    if user32.GetForegroundWindow() != hwnd:                       # last check before any click
        raise RuntimeError("the game lost focus; no click was sent")
    user32.mouse_event(MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
    user32.mouse_event(MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)
    time.sleep(0.15)
    user32.SetCursorPos(prev_pos.x, prev_pos.y)
    if prev_fg and prev_fg != hwnd:
        user32.SetForegroundWindow(prev_fg)


def ui_hidden(lmu: LMUClient) -> Optional[bool]:
    """True when the game reports every HUD component off, False when all on, None if mixed/unknown."""
    try:
        state = lmu._call("GET", "hud")
    except LMUApiError:
        return None
    if not isinstance(state, dict) or not state:
        return None
    vals = [bool(v) for v in state.values()]
    return False if all(vals) else True if not any(vals) else None


def set_game_ui(lmu: LMUClient, hidden: bool, click: Callable[[], None] = middle_click_game,
                settle: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Make the replay UI hidden/shown. Uses the game's HUD state to decide whether a click is needed and to
    confirm it worked. Returns True when the state matches afterwards."""
    cur = ui_hidden(lmu)
    if cur == hidden:
        return True
    for _ in range(2):                                             # the toggle is idempotent per state: retry once
        click()
        sleep(settle)
        if ui_hidden(lmu) == hidden:
            return True
    return False


def set_speedo(lmu: LMUClient, on: bool) -> bool:
    """Show or hide just the game's own speedo (gear, speed, rpm ring, pedals): the one source of real telemetry
    during a replay, since the game publishes none while one plays. Uses POST hud/toggle/speedo (path only, no
    body); the drawing follows the HUD state once the rest of the UI has been hidden. True when it matches."""
    def state() -> Optional[bool]:
        try:
            v = lmu._call("GET", "hud")
        except LMUApiError:
            return None
        return bool(v.get("speedo")) if isinstance(v, dict) and "speedo" in v else None

    if state() == on:
        return True
    try:
        lmu._call("POST", "hud/toggle/speedo")
    except LMUApiError:
        return False
    time.sleep(0.3)
    return state() == on


# ---- verified hide/show ----------------------------------------------------------------------------------
# The game's /rest/hud readout is not a usable indicator: right after a replay loads it reports every component
# off while the toolbar is drawn, and a click does not change it. So the click is verified against the picture.
def _gray(png: bytes, crop: str, w: int, h: int) -> list[int]:
    from .compose import ffmpeg_bin
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", "pipe:0", "-vf",
                        f"{crop},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-frames:v", "1", "pipe:1"],
                       input=png, capture_output=True, timeout=20, creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode or len(r.stdout) != w * h:
        raise RuntimeError("could not read the game picture from OBS")
    return list(r.stdout)


def _rgb(png: bytes, crop: str, w: int, h: int) -> bytes:
    from .compose import ffmpeg_bin
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", "pipe:0", "-vf", f"{crop},scale={w}:{h}:flags=area,format=rgb24",
                        "-f", "rawvideo", "-frames:v", "1", "pipe:1"], input=png, capture_output=True, timeout=20,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode or len(r.stdout) != w * h * 3:
        raise RuntimeError("could not read the game picture from OBS")
    return r.stdout


def looks_like_main_menu(png: bytes) -> bool:
    """LMU's main menu has a large red panel down its left side; a replay screen never does. The game can report a
    replay as active (and answer its data calls) while still showing the menu, so the REST state alone cannot say
    that the replay is on screen."""
    px = _rgb(png, "crop=iw*0.30:ih:0:0", 24, 32)
    red = sum(1 for i in range(0, len(px), 3) if px[i] > 170 and px[i + 1] < 110 and px[i + 2] < 130)
    return red / (len(px) // 3) > 0.20


# The replay toolbar (timeline and "Lap" row) at the bottom of the replay screen: the same glyphs on every replay and
# every camera, and gone when the UI is hidden. These are the positions (in a 120x30 grey map of that region) of its
# bright pixels, taken from a real screenshot. It tells whether the UI is SHOWN right now, which no click can.
_BAR_BOX = (0.27, 0.88, 0.40, 0.97)
_BAR = (900, 901, 902, 994, 1001, 1003, 1018, 1019, 1020, 1021, 1022, 1023, 1114, 1121, 1123, 1128, 1130, 1140, 1141,
        1142, 2693, 2798, 2800, 2813, 2913, 2914, 2915, 2916, 2918, 2919, 2933)


def ui_shown(png: bytes) -> Optional[bool]:
    """True when the replay UI is on screen, False when it is hidden, None when the picture does not say (a menu, a
    loading screen). Measured on real pictures: shown 0.39-1.00 of the template present, hidden 0.00-0.03."""
    if looks_like_main_menu(png):
        return None
    x0, y0, x1, y1 = _BAR_BOX
    px = _rgb(png, f"crop=iw*{x1 - x0}:ih*{y1 - y0}:iw*{x0}:ih*{y0}", 120, 30)
    hit = sum(1 for i in _BAR if (px[i * 3] * 299 + px[i * 3 + 1] * 587 + px[i * 3 + 2] * 114) / 1000 >= 170)
    score = hit / len(_BAR)
    return True if score >= 0.25 else False if score <= 0.12 else None


def screen_probe(png: bytes) -> list[int]:
    """A small greyscale thumbnail of the two places LMU draws its replay UI: the standings panel (top left) and
    the timeline strip (bottom centre). Toggling the UI changes these a lot; the moving scene changes them little."""
    return _gray(png, "crop=iw*0.34:ih*0.30:0:0", 34, 12) + _gray(png, "crop=iw*0.50:ih*0.16:iw*0.25:ih*0.82", 40, 8)


def _diff(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


class UiController:
    """Hides/shows the replay UI and knows which state it left it in. The state is tracked here (the game's own
    readout is unreliable) and every toggle is checked against a before/after picture of the game, so a click that
    did nothing is retried and then reported with a reason instead of being assumed to have worked.

    `probe()` returns the thumbnail above (None = no picture available: the click is then trusted, not verified).
    The UI is shown whenever a replay has just been loaded, so call `reset()` after loading one."""

    def __init__(self, click: Callable[[], None] = middle_click_game, probe: Optional[Callable[[], list[int]]] = None,
                 sleep: Callable[[float], None] = time.sleep, settle: float = 1.2, attempts: int = 3,
                 min_change: float = 4.0, shown: Optional[Callable[[], Optional[bool]]] = None):
        self.click, self.probe, self.sleep = click, probe, sleep
        self.shown = shown          # () -> True/False/None: is the UI on screen right now (see ui_shown)
        self.settle, self.attempts, self.min_change = settle, attempts, min_change
        self.hidden = False
        self.verified = True
        self.last_error = ""

    def reset(self) -> None:
        self.hidden, self.last_error = False, ""

    def _look(self) -> Optional[list[int]]:
        if self.probe is None:
            return None
        try:
            return self.probe()
        except Exception as e:                      # OBS not connected, ffmpeg missing, ...
            self.last_error = f"cannot see the game picture ({e}); the click is not verified"
            return None

    def _really_shown(self) -> Optional[bool]:
        if self.shown is None:
            return None
        try:
            return self.shown()
        except Exception:
            return None

    def set(self, hidden: bool) -> bool:
        """Get the UI hidden/shown. The replay is paused while this runs (Director._segment), so the picture only
        changes when a click toggles the UI: each click is checked by how much it changed the picture, and the state
        is counted from the last replay load (the UI is always shown then, see `reset`). The picture check `ui_shown`
        is believed outright only when it says hidden: on a pale track (Algarve) or a white glove in the cockpit it reads
        a hidden UI as shown, but it has not been seen to read a shown UI as hidden. When it says shown while the count
        says hidden, one test click settles it: if the picture then reads hidden, the UI really was on screen (a hide
        that was believed done but was not); otherwise it was a misreading and a second click puts things back."""
        real = self._really_shown()
        if real is False:
            self.hidden = True
        elif real is True and self.hidden:
            if self._toggle() is not False:
                if self._really_shown() is False:
                    self.hidden = True                              # it was shown; this click hid it
                else:
                    self._toggle()                                  # a misreading: undo the test click
        if self.hidden == hidden:
            return True
        for _ in range(self.attempts):
            done = self._toggle()
            if done is None:                                        # no probe: the picture check is all there is
                real = self._really_shown()
                if real is None:
                    self.hidden, self.verified = hidden, False      # trusted, not seen
                    return True
                if real != hidden:
                    self.hidden, self.verified, self.last_error = hidden, True, ""
                    return True
                self.last_error = "the click was sent but the UI is still " + ("shown" if real else "hidden")
                continue
            if done:
                self.hidden, self.verified, self.last_error = not self.hidden, True, ""
                if self.hidden == hidden:
                    return True
        return False

    def _toggle(self) -> Optional[bool]:
        """One click, checked by how much it changed the (paused) picture: True changed, False did not, None no
        picture to check with."""
        before = self._look()
        self.sleep(0.4)
        again = self._look()
        noise = _diff(before, again) if before and again else 0.0
        try:
            self.click()
        except Exception as e:
            self.last_error = str(e)
            self.sleep(1.0)
            return False
        self.sleep(self.settle)
        after = self._look()
        if before is None or after is None:
            return None
        if _diff(before, after) > max(self.min_change, 3 * noise):
            return True
        self.last_error = "the click was sent but the game's UI did not change"
        return False
