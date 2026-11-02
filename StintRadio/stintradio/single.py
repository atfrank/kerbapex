"""One copy only: a named Windows mutex held for as long as the program lives. A second copy that asks for the same name is
refused. Used for the server (per port), the overlay (per port) and the control panel, so a double click, a startup shortcut
and a Start button can never leave two of them running (two overlays draw over each other; two servers could both speak).
"""
from __future__ import annotations

import ctypes
import sys
import time

ERROR_ALREADY_EXISTS = 183
_held: list = []                      # the handles stay open until the program ends


def acquire(name: str, wait: float = 0.0) -> bool:
    """True if this is now the only holder of `name`. `wait` seconds of retrying lets a copy that is just closing finish first."""
    if sys.platform != "win32":
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    deadline = time.monotonic() + wait
    while True:
        ctypes.set_last_error(0)
        h = k.CreateMutexW(None, False, "Local\\" + name)
        if not h:
            return True                                   # cannot tell: do not stop the program for it
        if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            _held.append(h)
            return True
        k.CloseHandle(h)
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def focus_window(title: str) -> None:
    """Bring the window with this title forward (the copy that is already running)."""
    if sys.platform != "win32":
        return
    u = ctypes.WinDLL("user32")
    h = u.FindWindowW(None, title)
    if h:
        u.ShowWindow(h, 9)                                # restore if minimized
        u.SetForegroundWindow(h)
