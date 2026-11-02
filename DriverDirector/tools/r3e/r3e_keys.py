"""Bring the AC Evo window to the front, press keys (scan codes, like a real keyboard) and screenshot after each.
Usage: python evo_keys.py OUTDIR KEY [KEY ...]   KEY = name from SCAN (e.g. F1) or 'shot' for a screenshot only."""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDPIAware()
SCAN = {"F1": 0x3B, "F2": 0x3C, "F3": 0x3D, "F4": 0x3E, "F5": 0x3F, "F6": 0x40, "F7": 0x41, "F8": 0x42,
        "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
        "C": 0x2E, "NUMPLUS": 0x4E, "NUMMINUS": 0x4A, "ESC": 0x01, "H": 0x23, "V": 0x2F, "SPACE": 0x39, "PGUP": (0x49, True), "PGDN": (0x51, True),
        "LEFT": (0x4B, True), "RIGHT": (0x4D, True), "UP": (0x48, True), "DOWN": (0x50, True),
        "INS": (0x52, True), "NP1": 0x4F, "NP2": 0x50, "NP3": 0x51, "NP4": 0x4B, "NP5": 0x4C, "NP6": 0x4D, "NP7": 0x47, "NP8": 0x48, "NP9": 0x49, "DEL": (0x53, True), "HOME": (0x47, True), "END": (0x4F, True)}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def press(name, hold=0.08):
    code = SCAN[name]
    ext = False
    if isinstance(code, tuple):
        code, ext = code
    flags = 0x0008 | (0x0001 if ext else 0)          # KEYEVENTF_SCANCODE (| EXTENDEDKEY)
    for f in (flags, flags | 0x0002):                 # down, up
        inp = INPUT(type=1, ki=KEYBDINPUT(0, code, f, 0, 0))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(hold)


def find_window():
    found = []
    pids = set()
    out = subprocess.run(["powershell", "-c", "(Get-Process RRRE64).Id"], capture_output=True, text=True).stdout
    pids = {int(x) for x in out.split()}
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(h):
            found.append(h)
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


def front(h):
    if user32.IsIconic(h):
        user32.ShowWindow(h, 9)                       # SW_RESTORE
        time.sleep(0.8)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(h)
    time.sleep(0.4)
    return user32.GetForegroundWindow() == h


def shot(path):
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;Add-Type -MemberDefinition '[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware();' -Name D -Namespace W;[W.D]::SetProcessDPIAware()|Out-Null;"
          "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
          "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
          "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
          "$s=New-Object System.Drawing.Bitmap $bmp,($b.Width/3.5),($b.Height/3.5);"
          f"$s.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Jpeg)")
    subprocess.run(["powershell", "-c", ps], check=True)


if __name__ == "__main__":
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    h = find_window()
    if not h:
        raise SystemExit("AC Evo window not found")
    print("window", h, "front:", front(h))
    for i, k in enumerate(sys.argv[2:]):
        if k != "shot":
            if user32.GetForegroundWindow() != h and not front(h):
                raise SystemExit("could not keep AC Evo in front (something else has focus)")
            press(k)
            time.sleep(1.5)
        p = os.path.join(outdir, f"{i:02d}_{k}.jpg")
        shot(p)
        print("saved", p, flush=True)


def click(x, y):
    """Left click at full-resolution screen coordinates."""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.2)
    user32.mouse_event(2, 0, 0, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(4, 0, 0, 0, 0)
