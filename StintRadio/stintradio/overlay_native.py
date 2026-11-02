"""The overlay as a native Windows window with a truly see-through background (python -m stintradio overlay --engine native): a
layered window fed a bitmap with per-pixel alpha, so what is not drawn is simply the game. Same layout, hotkeys, edit mode,
snap-to-corner, saved layout and /api/overlay state as the Edge window in overlay_window.py, which it borrows them from.

--png FILE draws one frame from the running StintRadio into an image and opens no window at all.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import time
import urllib.request
from ctypes import byref, wintypes

from . import overlay_scene as sc
from .overlay_gdip import Canvas
from .overlay_window import (GAMES, GRIP, HWND_TOPMOST, SWP_NOACTIVATE, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
                             WS_EX_TRANSPARENT, GWL_EXSTYLE, Hotkeys, Win, clamp_size, default_geom, geom_for, next_shape, parse_size, rect_for, scaled)

ALWAYS = os.environ.get("KERBAPEX_OVERLAY_ALWAYS") == "1"       # on top of every window, not only while the game is in front
WS_POPUP = 0x80000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 2, 0, 1
WM_MOUSEACTIVATE, MA_NOACTIVATE, PM_REMOVE = 0x0021, 3, 1
FRAME_S, DRAG_FRAME_S = 1 / 30, 1 / 60
CLASS_NAME = "KerbApexOverlay"
OFF_SCREEN = -32000

_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WndClass(ctypes.Structure):
    _fields_ = [("size", wintypes.UINT), ("style", wintypes.UINT), ("proc", _WNDPROC), ("cls_extra", ctypes.c_int), ("wnd_extra", ctypes.c_int),
                ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE), ("background", wintypes.HBRUSH),
                ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR), ("small_icon", wintypes.HICON)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _Blend(ctypes.Structure):
    _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte), ("format", ctypes.c_ubyte)]


def ex_style(click_through: bool) -> int:
    """Always on top, never in the task switcher, never takes the keyboard; the mouse goes through it unless editing."""
    base = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    return base | WS_EX_TRANSPARENT if click_through else base


class NativeWindow:
    """The layered window. It stays hidden until show() is called."""

    _proc = None                                  # one window class per process; ctypes must keep its callback alive

    def __init__(self, opacity: float = 1.0) -> None:
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32")
        k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HMODULE
        u = self.u
        u.DefWindowProcW.argtypes, u.DefWindowProcW.restype = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], _LRESULT
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u.CreateWindowExW.restype = wintypes.HWND
        u.RegisterClassExW.argtypes = [ctypes.POINTER(_WndClass)]
        u.GetWindowLongPtrW.restype, u.GetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype, u.SetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(_Size), ctypes.c_void_p,
                                          ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(_Blend), wintypes.DWORD]
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        inst = k.GetModuleHandleW(None)
        if NativeWindow._proc is None:
            NativeWindow._proc = _WNDPROC(self._wnd_proc)
            cls = _WndClass(size=ctypes.sizeof(_WndClass), proc=NativeWindow._proc, instance=inst, name=CLASS_NAME)
            if not u.RegisterClassExW(byref(cls)):
                NativeWindow._proc = None
                raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = u.CreateWindowExW(ex_style(True), CLASS_NAME, "KerbApex overlay", WS_POPUP, OFF_SCREEN, OFF_SCREEN, 1, 1, None, None, inst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.blend = _Blend(AC_SRC_OVER, 0, int(max(0.1, min(1.0, opacity)) * 255), AC_SRC_ALPHA)
        self.shown = False

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:               # dragging it in edit mode must not take the focus from the game
            return MA_NOACTIVATE
        return self.u.DefWindowProcW(hwnd, msg, wparam, lparam)

    def click_through(self, on: bool) -> None:
        self.u.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex_style(on))

    def set_opacity(self, opacity: float) -> bool:
        """How see-through the whole window is (on top of the per-pixel alpha). True if it changed."""
        alpha = int(max(0.1, min(1.0, opacity)) * 255)
        if alpha == self.blend.alpha:
            return False
        self.blend.alpha = alpha
        return True

    def update(self, canvas: Canvas, x: int, y: int) -> bool:
        """Put the bitmap on the window, at this place and with the bitmap's size."""
        return bool(self.u.UpdateLayeredWindow(self.hwnd, None, byref(wintypes.POINT(x, y)), byref(_Size(canvas.w, canvas.h)), canvas.dc,
                                               byref(wintypes.POINT(0, 0)), 0, byref(self.blend), ULW_ALPHA))

    def show(self) -> None:
        """Up, on top of the game, without taking anything from it (also when it has come back up over us)."""
        self.u.SetWindowPos(self.hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self.shown = True

    def hide(self) -> None:
        self.u.ShowWindow(self.hwnd, SW_HIDE)
        self.shown = False

    def pump(self) -> None:
        msg = wintypes.MSG()
        while self.u.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
            self.u.DispatchMessageW(byref(msg))

    def close(self) -> None:
        if self.hwnd:
            self.u.DestroyWindow(self.hwnd)
            self.hwnd = None


class Layout:
    """The window's layout and modes, and what the hotkeys and the mouse do to them. No Win32 here. key() and drag() return what
    StintRadio should be told, if anything."""

    def __init__(self, geom: dict, default: dict, brand: bool = True, theme: str = "light", border: bool = False) -> None:
        self.geom, self.default, self.brand, self.theme, self.border = geom, default, brand, theme, border
        self.editing, self.held, self.quit = False, None, False
        self.quiet_until = 0.0                    # until then a layout from StintRadio is not taken over (it may be older than ours)

    def adopt(self, geom, now: float) -> bool:
        """A layout set elsewhere (the control panel) becomes ours, unless we changed it a moment ago or are being dragged."""
        from .overlay_window import clean_geom
        new = clean_geom(geom)
        if new is None or new == self.geom or self.held is not None or self.editing or now < self.quiet_until:
            return False
        self.geom = new
        return True

    def key(self, name: str) -> dict | None:
        if name == "edit":
            self.editing, self.held = not self.editing, None
            return {"edit": True} if self.editing else {"edit": False, "geom": self.geom}
        if name == "brand":
            self.brand = not self.brand
            return {"brand": self.brand}
        if name == "border":
            self.border = not self.border
            return {"border": self.border}
        if name == "theme":
            self.theme = "dark" if self.theme == "light" else "light"
            return {"theme": self.theme}
        if name == "quit":
            self.quit = True
            return None
        if name in ("shape", "bigger", "smaller", "reset"):
            self.geom = {"shape": lambda: next_shape(self.geom), "bigger": lambda: scaled(self.geom, 1.1),
                         "smaller": lambda: scaled(self.geom, 1 / 1.1), "reset": lambda: dict(self.default)}[name]()
            self.quiet_until = time.monotonic() + 3.0
            return {"geom": self.geom}
        return None

    def set_edit(self, on: bool) -> bool:
        """The control panel's Move / resize button. True if it changed anything."""
        if on == self.editing:
            return False
        self.editing, self.held = on, None
        return True

    def drag(self, down: bool, cursor: tuple, game: tuple) -> dict | None:
        """One look at the mouse in edit mode: drag to move, the bottom-right corner resizes; on release the layout is saved."""
        if not down:
            if self.held is None:
                return None
            self.held = None
            self.quiet_until = time.monotonic() + 3.0
            return {"geom": self.geom}
        cx, cy = cursor
        if self.held is None:
            x, y, w, h = rect_for(self.geom, game)
            if not (x <= cx <= x + w and y <= cy <= y + h):
                return None
            mode = "resize" if cx > x + w - GRIP and cy > y + h - GRIP else "move"
            self.held = {"mode": mode, "cx": cx, "cy": cy, "x": x, "y": y, "w": w, "h": h}
        d = self.held
        ddx, ddy = cx - d["cx"], cy - d["cy"]
        if d["mode"] == "move":
            self.geom = geom_for(d["x"] + ddx, d["y"] + ddy, d["w"], d["h"], game)
        else:
            nw, nh = clamp_size(d["w"] + ddx, d["h"] + ddy)
            self.geom = geom_for(d["x"], d["y"], nw, nh, game)
        return None


class Feed(threading.Thread):
    """Talks to StintRadio on its own thread, so that a slow answer never holds up a frame."""

    def __init__(self, base: str, model: sc.Model, lock: threading.Lock) -> None:
        super().__init__(daemon=True)
        self.base, self.model, self.lock = base.rstrip("/"), model, lock
        self.outbox, self.state, self.stopping = queue.Queue(), None, False

    def call(self, path: str, body: dict | None = None, timeout: float = 3.0) -> dict | None:
        try:
            req = urllib.request.Request(self.base + path, method="PUT" if body is not None else "GET", headers={"Content-Type": "application/json"},
                                         data=None if body is None else json.dumps(body).encode())
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return None

    def send(self, body: dict) -> None:
        self.state = None                         # whatever was read before this no longer holds
        self.outbox.put(body)

    def take_state(self) -> dict | None:
        s, self.state = self.state, None
        return s

    def tick(self, now: float) -> None:
        """One second's worth: the race, the track if a new one is wanted, and our heartbeat (which also brings the asks)."""
        race = self.call("/api/race")
        with self.lock:
            self.model.apply(race, now)
            want = self.model.wants_track()
        if want:
            track = self.call("/api/track")
            with self.lock:
                self.model.set_track(track or {})
        state = self.call("/api/overlay", {"ping": True})
        if state is not None and self.outbox.empty():
            self.state = state

    def run(self) -> None:
        next_at = 0.0
        while not self.stopping:
            try:
                while True:
                    self.call("/api/overlay", self.outbox.get_nowait())
            except queue.Empty:
                pass
            now = time.monotonic()
            if now >= next_at:
                next_at = now + 1.0
                self.tick(now)
            time.sleep(0.05)


def render_png(path, race: dict | None, track: dict | None, state: dict | None, size: tuple) -> None:
    """One frame into an image file; no window is involved."""
    state = state or {}
    geom = state.get("geom") or {"w": size[0], "h": size[1]}
    w, h = clamp_size(geom["w"], geom["h"])
    model = sc.Model()
    model.set_track(track or {})
    model.apply(race, 0.0)
    canvas = Canvas(w, h)
    try:
        canvas.paint("png", sc.static_items(w, h, model.track, state.get("brand", True), state.get("border", False)), sc.dynamic_items(model, w, h, state.get("theme", "light")))
        canvas.save_png(path)
    finally:
        canvas.close()


def run(a) -> int:
    """The window's life. `a` holds the arguments of overlay_window.main."""
    base = a.url.split("/overlay")[0]
    model, lock = sc.Model(), threading.Lock()
    feed = Feed(base, model, lock)
    size = clamp_size(*parse_size(a.size))
    if a.png:
        render_png(a.png, feed.call("/api/race"), feed.call("/api/track"), feed.call("/api/overlay"), size)
        print(f"overlay: one frame drawn to {a.png}")
        return 0
    exe_name = GAMES.get(a.game, a.game)
    default = default_geom(a.corner, size, a.margin)
    saved = {} if a.reset else (feed.call("/api/overlay") or {})
    lay = Layout(saved.get("geom") or default, default, saved.get("brand", True), saved.get("theme", "light"), saved.get("border", False))
    win = Win()                                   # makes this process use real pixels, to match the game's window rectangle
    keys = Hotkeys(win)
    window = NativeWindow(1.0 if a.opacity is None else a.opacity)
    canvas = None
    print(f"overlay (native): over {exe_name}; Ctrl+Alt+O to move/resize, Ctrl+Alt+S shape, Ctrl+Alt+=/- size, Ctrl+Alt+B border")
    feed.send({"quit": False, "edit": False, "ping": True})
    feed.start()
    game, game_at, last_frame, shown_frame, game_pid = None, 0.0, time.monotonic(), None, 0
    try:
        while True:
            now = time.monotonic()
            window.pump()
            state = feed.take_state()
            if state:
                if state.get("quit"):
                    break
                if isinstance(state.get("edit"), bool) and lay.set_edit(state["edit"]):
                    window.click_through(not lay.editing)
                if isinstance(state.get("brand"), bool):
                    lay.brand = state["brand"]
                if isinstance(state.get("border"), bool):
                    lay.border = state["border"]
                if state.get("theme") in sc.THEMES:
                    lay.theme = state["theme"]
                if lay.adopt(state.get("geom"), now):
                    shown_frame = None
                op = state.get("opacity")
                if isinstance(op, (int, float)) and not isinstance(op, bool) and window.set_opacity(float(op)):
                    shown_frame = None
            if now - game_at > 0.5:                  # the game window moves rarely: look every half second
                g, rect = win.game_window(exe_name)
                game, game_at = (rect if g is not None else None), now
                game_pid = win.pid(g) if g is not None else 0
            # Only on top while the game is the window in front (alt-tab to a browser and the overlay stays behind it, as the
            # game does); moving it with Ctrl+Alt+O keeps it up. KERBAPEX_OVERLAY_ALWAYS=1 puts it on top of everything.
            in_front = ALWAYS or lay.editing or (game_pid != 0 and win.foreground_pid() in (game_pid, os.getpid()))
            for k in keys.pressed():
                was = lay.editing
                out = lay.key(k)
                if lay.editing != was:
                    window.click_through(not lay.editing)
                if out:
                    feed.send(out)
            if lay.quit:
                break
            if game is None or not in_front:
                if window.shown:
                    window.hide()
                time.sleep(0.25)
                continue
            if lay.editing:
                out = lay.drag(win.down("lbutton"), win.cursor(), game)
                if out:
                    feed.send(out)
            x, y, w, h = rect_for(lay.geom, game)
            if canvas is None or (canvas.w, canvas.h) != (w, h):
                if canvas is not None:
                    canvas.close()
                canvas = Canvas(w, h)
            with lock:
                model.step(min(0.25, now - last_frame), now)
                static_key = (w, h, (model.track or {}).get("id"), lay.brand, lay.border)
                track = model.track
                moving = sc.rounded(sc.dynamic_items(model, w, h, lay.theme, lay.editing))
            last_frame = now
            frame = (static_key, x, y, moving)
            if frame != shown_frame:                  # nothing moved: nothing to draw, nothing to send to the screen
                if static_key != canvas.layer_key:
                    canvas.paint(static_key, sc.static_items(w, h, track, lay.brand, lay.border), moving)
                else:
                    canvas.paint(static_key, [], moving)
                window.update(canvas, x, y)
                shown_frame = frame
            if not window.shown:
                window.show()
            time.sleep(DRAG_FRAME_S if lay.held else FRAME_S)
    except KeyboardInterrupt:
        pass
    finally:
        feed.stopping = True
        feed.call("/api/overlay", {"edit": False, "gone": True}, timeout=1.0)
        window.close()
        if canvas is not None:
            canvas.close()
    return 0
