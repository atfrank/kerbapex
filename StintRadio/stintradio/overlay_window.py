"""A window laid over the game (windowed or borderless): the track map, delta and sector chips from /overlay, in a frameless
always-on-top Edge window that is translucent, never takes the keyboard, lets the mouse through to the game, and follows the
game window. Its shape, size and place are yours to change, with hotkeys that work while the game has the focus:

  Ctrl+Alt+O   edit mode on/off: drag the window to move it, drag its bottom-right corner to resize it; it snaps to the
               nearest corner of the game window and the layout is saved
  Ctrl+Alt+S   next shape (wide, square, tall, banner)
  Ctrl+Alt+= / Ctrl+Alt+-   bigger / smaller (10 % a step)
  Ctrl+Alt+B   the KerbApex border (kerb stripes) on/off        Ctrl+Alt+M   the KerbApex mark on/off
  Ctrl+Alt+T   light / dark theme
  Ctrl+Alt+R   back to the default layout
  Ctrl+Alt+Q   close the overlay

  python -m stintradio overlay [--corner top-right|top-left|bottom-right|bottom-left] [--size 640x420] [--opacity 0.88]
                               [--margin 24] [--game raceroom|lmu|EXE] [--reset] [--engine edge|native]

--engine native is a native window with a truly see-through background instead of the Edge one (see overlay_native.py).

It only draws; the game is not touched. Exclusive fullscreen cannot be drawn over by any window: use the Steam overlay there.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00C00000, 0x00040000, 0x00080000, 0x00020000, 0x00010000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x8, 0x80, 0x08000000
LWA_ALPHA = 0x2
SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_FRAMECHANGED, SWP_HIDEWINDOW = 0x0010, 0x0040, 0x0020, 0x0080
HWND_TOPMOST = -1
GAMES = {"raceroom": "RRRE64.exe", "lmu": "Le Mans Ultimate.exe"}
EDGES = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
SHAPES = [("wide", 760, 360), ("square", 520, 520), ("tall", 380, 640), ("banner", 960, 230)]
MIN_W, MIN_H, MAX_W, MAX_H = 240, 160, 1800, 1100
GRIP = 56                                   # the bottom-right square that resizes, in edit mode
VK = {"ctrl": 0x11, "alt": 0x12, "lbutton": 0x01, "o": 0x4F, "s": 0x53, "b": 0x42, "m": 0x4D, "r": 0x52, "q": 0x51, "t": 0x54, "plus": 0xBB, "minus": 0xBD,
      "add": 0x6B, "sub": 0x6D}


# ---- layout: pure functions --------------------------------------------------------------------------------------------

def edge_path() -> str | None:
    return shutil.which("msedge") or next((p for p in EDGES if Path(p).is_file()), None)


def parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().split("x")
    return int(w), int(h)


def clamp_size(w: float, h: float) -> tuple[int, int]:
    return int(max(MIN_W, min(MAX_W, w))), int(max(MIN_H, min(MAX_H, h)))


def placement(game: tuple, size: tuple, corner: str, margin: int, margin_y: int | None = None) -> tuple[int, int]:
    """Where the overlay's top-left goes inside the game's window rectangle (left, top, right, bottom): `margin` from the
    chosen corner's side edge, `margin_y` (default the same) from its top or bottom edge."""
    l, t, r, b = game
    w, h = size
    my = margin if margin_y is None else margin_y
    x = l + margin if "left" in corner else r - w - margin
    y = t + my if "top" in corner else b - h - my
    return int(x), int(y)


def rect_for(geom: dict, game: tuple) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the overlay for a saved layout {anchor, w, h, dx, dy} inside the game window."""
    w, h = clamp_size(geom["w"], geom["h"])
    x, y = placement(game, (w, h), geom["anchor"], int(geom["dx"]), int(geom["dy"]))
    return x, y, w, h


def geom_for(x: int, y: int, w: int, h: int, game: tuple) -> dict:
    """The layout for an overlay dropped at (x, y): it snaps to whichever corner of the game window it is nearest."""
    l, t, r, b = game
    left, top = (x + w / 2) < (l + r) / 2, (y + h / 2) < (t + b) / 2
    anchor = ("top" if top else "bottom") + "-" + ("left" if left else "right")
    dx = x - l if left else r - (x + w)
    dy = y - t if top else b - (y + h)
    return {"anchor": anchor, "w": int(w), "h": int(h), "dx": int(max(0, dx)), "dy": int(max(0, dy))}


def scaled(geom: dict, factor: float) -> dict:
    w, h = clamp_size(geom["w"] * factor, geom["h"] * factor)
    return {**geom, "w": w, "h": h}


def next_shape(geom: dict) -> dict:
    """The next shape in the cycle, keeping roughly the same area, so a shape change does not jump in size."""
    area = geom["w"] * geom["h"]
    names = [s[0] for s in SHAPES]
    cur = min(range(len(SHAPES)), key=lambda i: abs(SHAPES[i][1] / SHAPES[i][2] - geom["w"] / geom["h"]))
    name, bw, bh = SHAPES[(cur + 1) % len(names)]
    k = (area / (bw * bh)) ** 0.5
    w, h = clamp_size(bw * k, bh * k)
    return {**geom, "w": w, "h": h}


def clean_geom(g) -> dict | None:
    """A layout from outside (the control panel, the saved file) made safe, or None if it is not one."""
    try:
        if g["anchor"] not in CORNERS:
            return None
        w, h = clamp_size(float(g["w"]), float(g["h"]))
        return {"anchor": g["anchor"], "w": w, "h": h, "dx": int(max(0, float(g["dx"]))), "dy": int(max(0, float(g["dy"])))}
    except (KeyError, TypeError, ValueError):
        return None


def default_geom(corner: str, size: tuple, margin: int) -> dict:
    return {"anchor": corner, "w": size[0], "h": size[1], "dx": margin, "dy": margin}


# ---- Win32 -----------------------------------------------------------------------------------------------------------

class Win:
    """The few Win32 calls the overlay needs."""

    def __init__(self) -> None:
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.g = ctypes.WinDLL("gdi32")
        self.g.CreateRectRgn.restype = wintypes.HRGN
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)        # real pixels, to match the game's window rectangle
        except (OSError, AttributeError):
            self.u.SetProcessDPIAware()
        self.u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.u.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        self.u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
        self.k.OpenProcess.restype = wintypes.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

    def windows(self) -> list:
        found = []
        cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(lambda h, _: found.append(h) or True)
        self.u.EnumWindows(cb, 0)
        return found

    def pid(self, hwnd) -> int:
        p = wintypes.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        return p.value

    def exe(self, pid: int) -> str:
        h = self.k.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            n = wintypes.DWORD(520)
            return os.path.basename(buf.value) if self.k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
        finally:
            self.k.CloseHandle(h)

    def rect(self, hwnd) -> tuple:
        r = wintypes.RECT()
        self.u.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def visible(self, hwnd) -> bool:
        return bool(self.u.IsWindowVisible(hwnd)) and not self.u.IsIconic(hwnd)

    def foreground_pid(self) -> int:
        """The process that owns the window the user is in now (0 if none)."""
        self.u.GetForegroundWindow.restype = wintypes.HWND
        h = self.u.GetForegroundWindow()
        return self.pid(h) if h else 0

    def title(self, hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        self.u.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    def cursor(self) -> tuple:
        p = wintypes.POINT()
        self.u.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def down(self, *keys: str) -> bool:
        return all(self.u.GetAsyncKeyState(VK[k]) & 0x8000 for k in keys)

    def game_window(self, exe_name: str):
        """The biggest visible top-level window of the game's process, with its rectangle."""
        best = None
        for h in self.windows():
            if not self.visible(h) or self.exe(self.pid(h)).lower() != exe_name.lower():
                continue
            l, t, r, b = self.rect(h)
            if best is None or (r - l) * (b - t) > best[1]:
                best = (h, (r - l) * (b - t), (l, t, r, b))
        return (best[0], best[2]) if best else (None, None)

    def edge_window(self, pids: set):
        for h in self.windows():
            if self.pid(h) in pids and self.visible(h) and self.title(h):
                return h
        return None

    def make_overlay(self, hwnd, alpha: float) -> None:
        """Frameless, translucent, always on top, never active, and the mouse goes straight through it to the game."""
        style = self.u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        self.u.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX))
        self.click_through(hwnd, True)
        self.u.SetLayeredWindowAttributes(hwnd, 0, int(max(0.1, min(1.0, alpha)) * 255), LWA_ALPHA)

    def set_alpha(self, hwnd, alpha: float) -> None:
        self.u.SetLayeredWindowAttributes(hwnd, 0, int(max(0.1, min(1.0, alpha)) * 255), LWA_ALPHA)

    def click_through(self, hwnd, on: bool) -> None:
        ex = self.u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT))

    def content_rect(self, hwnd):
        """Where the page itself is inside the browser window (its renderer child), or None."""
        kids = []
        cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(lambda c, _: kids.append(c) or True)
        self.u.EnumChildWindows(hwnd, cb, 0)
        for c in kids:
            cls = ctypes.create_unicode_buffer(128)
            self.u.GetClassNameW(c, cls, 128)
            if cls.value == "Chrome_RenderWidgetHostHWND" and self.u.IsWindowVisible(c):
                return self.rect(c)
        return None

    def place(self, hwnd, x: int, y: int, w: int, h: int, show: bool = True) -> bool:
        """Put the PAGE at (x, y, w, h): the browser window is made bigger by its header and frame, moved up and left by
        them, and clipped to the page so none of the header or frame can be seen."""
        flags = SWP_NOACTIVATE | SWP_FRAMECHANGED | (SWP_SHOWWINDOW if show else SWP_HIDEWINDOW)
        top, page = self.rect(hwnd), self.content_rect(hwnd)
        if page is None:                                          # not up yet: plain placement
            self.u.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), x, y, w, h, flags)
            return False
        il, it, ir, ib = page[0] - top[0], page[1] - top[1], top[2] - page[2], top[3] - page[3]
        self.u.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), x - il, y - it, w + il + ir, h + it + ib, flags)
        region = self.g.CreateRectRgn(il, it, il + w, it + h)
        self.u.SetWindowRgn(hwnd, region, True)                   # the system owns the region from here
        try:                                                      # no rounded corners or 1 px border from the window manager
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(1)), 4)
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)
        except (OSError, AttributeError):
            pass
        return True


def family_pids(root: int) -> set:
    """The process and everything it started (Edge's window may belong to any of them)."""
    try:
        cmd = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.ParentProcessId -eq {root} -or $_.ProcessId -eq {root} }} "
               "| ForEach-Object { $_.ProcessId }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=15, creationflags=0x08000000).stdout
        return {int(x) for x in out.split() if x.isdigit()} | {root}
    except (OSError, subprocess.SubprocessError):
        return {root}


class Server:
    """The overlay state kept by StintRadio (edit mode, border, saved layout); best effort, the window works without it."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def get(self) -> dict:
        try:
            with urllib.request.urlopen(self.base + "/api/overlay", timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return {}

    def put(self, body: dict) -> dict:
        try:
            req = urllib.request.Request(self.base + "/api/overlay", method="PUT", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return {}


class Hotkeys:
    """Ctrl+Alt+<key> chords, fired once per press (polled, so they work while the game has the focus)."""

    def __init__(self, win: Win) -> None:
        self.win, self.held = win, set()

    def pressed(self) -> list:
        out = []
        for name, keys in (("edit", ("o",)), ("shape", ("s",)), ("bigger", ("plus",)), ("bigger", ("add",)), ("smaller", ("minus",)),
                           ("smaller", ("sub",)), ("border", ("b",)), ("brand", ("m",)), ("theme", ("t",)), ("reset", ("r",)), ("quit", ("q",))):
            on = self.win.down("ctrl", "alt", *keys)
            key = (name, keys)
            if on and key not in self.held:
                out.append(name)
            (self.held.add if on else self.held.discard)(key)
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stintradio overlay")
    ap.add_argument("--url", default="http://127.0.0.1:6420/overlay")
    ap.add_argument("--corner", default="top-right", choices=CORNERS)
    ap.add_argument("--size", default="640x420", help="in screen pixels")
    ap.add_argument("--opacity", type=float, default=None, help="of the whole window; default 0.88 (edge), 1 (native)")
    ap.add_argument("--margin", type=int, default=24)
    ap.add_argument("--game", default="raceroom", help="raceroom | lmu | an exe name")
    ap.add_argument("--reset", action="store_true", help="ignore the saved layout")
    ap.add_argument("--engine", default="native", choices=("edge", "native"), help="native (default): a truly see-through background; edge: a browser window with a solid background")
    ap.add_argument("--png", default=None, help="native: draw one frame to this image file, with no window")
    a = ap.parse_args(argv)
    if not a.png:                                  # one overlay per StintRadio: a second one would draw over the first
        from urllib.parse import urlparse
        from .single import acquire
        if not acquire(f"KerbApexOverlay-{urlparse(a.url).port or 6420}", wait=4.0):
            print("an overlay is already running for this StintRadio")
            return 0
    if sys.platform != "win32":
        raise SystemExit("the overlay is Windows-only")
    if a.engine == "native":
        from .overlay_native import run
        return run(a)
    if a.opacity is None:
        a.opacity = 0.88
    edge = edge_path()
    if not edge:
        raise SystemExit("Microsoft Edge was not found")
    exe_name = GAMES.get(a.game, a.game)
    base = a.url.split("/overlay")[0]
    server = Server(base)

    def save_geom() -> None:
        """Tell StintRadio the layout, and ignore what it says about the layout for a moment (its answer may be older than this)."""
        nonlocal quiet_until
        server.put({"geom": geom})
        quiet_until = time.monotonic() + 3.0
    default = default_geom(a.corner, clamp_size(*parse_size(a.size)), a.margin)
    saved = {} if a.reset else server.get()
    geom = saved.get("geom") or default
    brand, border = saved.get("brand", True), saved.get("border", False)
    theme = saved.get("theme", "light")
    win = Win()
    keys = Hotkeys(win)
    profile = tempfile.mkdtemp(prefix="stintradio_overlay_")
    proc = subprocess.Popen([edge, f"--app={a.url}", f"--user-data-dir={profile}", "--no-first-run", "--disable-extensions",
                             f"--window-size={geom['w']},{geom['h']}"])
    print(f"overlay: {a.url} over {exe_name}; Ctrl+Alt+O to move/resize, Ctrl+Alt+S shape, Ctrl+Alt+=/- size, Ctrl+Alt+B border")
    hwnd, editing, drag, game, game_at, applied = None, False, None, None, 0.0, None
    server.put({"quit": False, "edit": False, "ping": True})
    ping_at, leave, quiet_until, cur_op = 0.0, False, 0.0, a.opacity
    try:
        while proc.poll() is None:
            now = time.monotonic()
            if now - ping_at > 1.0:                              # say we are here, and see whether we were asked to close
                ping_at = now
                state = server.put({"ping": True})
                if state.get("quit"):
                    break
                if isinstance(state.get("edit"), bool) and state["edit"] != editing:     # the control panel's Move / resize button
                    editing = state["edit"]
                    win.click_through(hwnd, not editing)
                    drag = None
                if now > quiet_until and drag is None and not editing:         # a layout set from the control panel
                    new = clean_geom(state.get("geom"))
                    if new and new != geom:
                        geom, applied = new, None
                op = state.get("opacity")
                if isinstance(op, (int, float)) and not isinstance(op, bool) and hwnd is not None and abs(op - cur_op) > 0.01:
                    cur_op = float(op)
                    win.set_alpha(hwnd, cur_op)
            if hwnd is None:
                hwnd = win.edge_window(family_pids(proc.pid))
                if hwnd is not None:
                    win.make_overlay(hwnd, a.opacity)
                time.sleep(0.3)
                continue
            if now - game_at > 0.5:                              # the game window moves rarely: look every half second
                g, rect = win.game_window(exe_name)
                game, game_at = (rect if g is not None else None), now

            changed = False
            for k in keys.pressed():
                if k == "edit":
                    editing = not editing
                    win.click_through(hwnd, not editing)
                    server.put({"edit": editing, "geom": geom} if not editing else {"edit": True})
                elif k == "shape":
                    geom, changed = next_shape(geom), True
                elif k == "bigger":
                    geom, changed = scaled(geom, 1.1), True
                elif k == "smaller":
                    geom, changed = scaled(geom, 1 / 1.1), True
                elif k == "brand":
                    brand = not brand
                    server.put({"brand": brand})
                elif k == "border":
                    border = not border
                    server.put({"border": border})
                elif k == "theme":
                    theme = "dark" if theme == "light" else "light"
                    server.put({"theme": theme})
                elif k == "reset":
                    geom, changed = dict(default), True
                elif k == "quit":
                    leave = True
            if leave:
                break

            if game is None:
                if applied != "hidden":
                    win.place(hwnd, 0, 0, geom["w"], geom["h"], show=False)
                    applied = "hidden"
                time.sleep(0.25)
                continue

            if editing and game is not None:                       # drag to move; the bottom-right corner resizes
                x, y, w, h = rect_for(geom, game)
                cx, cy = win.cursor()
                if win.down("lbutton"):
                    if drag is None and x <= cx <= x + w and y <= cy <= y + h:
                        mode = "resize" if (cx > x + w - GRIP and cy > y + h - GRIP) else "move"
                        drag = {"mode": mode, "cx": cx, "cy": cy, "x": x, "y": y, "w": w, "h": h}
                    if drag is not None:
                        ddx, ddy = cx - drag["cx"], cy - drag["cy"]
                        if drag["mode"] == "move":
                            nx, ny, nw, nh = drag["x"] + ddx, drag["y"] + ddy, drag["w"], drag["h"]
                        else:
                            nw, nh = clamp_size(drag["w"] + ddx, drag["h"] + ddy)
                            nx, ny = drag["x"], drag["y"]
                        geom = geom_for(nx, ny, nw, nh, game)
                        win.place(hwnd, nx, ny, nw, nh)
                        applied = (nx, ny, nw, nh)
                        time.sleep(0.016)
                        continue
                elif drag is not None:
                    drag = None
                    save_geom()
                    changed = True

            if game is not None:
                r = rect_for(geom, game)
                if changed or applied != r:
                    applied = r if win.place(hwnd, *r) else None      # not clipped to the page yet: place it again at once
                if changed:
                    save_geom()
            time.sleep(0.016 if editing else 0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
        server.put({"edit": False, "gone": True})
    return 0


if __name__ == "__main__":
    sys.exit(main())
