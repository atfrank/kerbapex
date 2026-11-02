"""Change RaceRoom's force-feedback strength in a wheel profile, safely.

RaceRoom keeps its wheel settings in plain-text profiles, UserData\\ControlSet\\*.rcs, with lines like
    FFB Strength="0.5" // Strength of Force Feedback. Range 0.0 to 1.5.
The game rewrites these files while it runs and on exit, so a change is only made with the game closed. Only that one
value is changed, in a step of at most 10 %, after a dated backup, and the new value is read back to check it."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

KEY = "FFB Strength"
LINE = re.compile(rb'^(FFB Strength=")([-0-9.]+)(")', re.M)
LO, HI = 0.10, 1.50           # the game's range is 0.0 to 1.5; never go near zero from advice
MAX_STEP = 0.10               # at most this fraction of the current value per change
GAME_EXES = ("RRRE64.exe", "RRRE.exe")


class FFBError(Exception):
    pass


def controlset_dir() -> Optional[Path]:
    """UserData\\ControlSet of the RaceRoom profile (Documents may live in OneDrive)."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    sub = Path("My Games") / "SimBin" / "RaceRoom Racing Experience" / "UserData" / "ControlSet"
    for base in (home / "OneDrive" / "Documents", home / "Documents"):
        if (base / sub).is_dir():
            return base / sub
    return None


def game_running(run: Callable = subprocess.run) -> bool:
    if sys.platform != "win32":
        return False
    out = run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, creationflags=0x08000000).stdout.lower()
    return any(exe.lower() in out for exe in GAME_EXES)


def profiles(folder: Optional[Path] = None) -> dict:
    """name -> {"file", "strength", "device"} for every wheel profile with an FFB Strength line."""
    folder = folder or controlset_dir()
    out: dict = {}
    if folder is None:
        return out
    for f in sorted(folder.glob("*.rcs")):
        data = f.read_bytes()
        m = LINE.search(data)
        if not m:
            continue
        dev = re.search(rb'^FFB Device Name="([^"]*)"', data, re.M)
        out[f.stem] = {"file": str(f), "strength": float(m.group(2)), "device": dev.group(1).decode("latin-1") if dev else ""}
    return out


def pick_profile(found: dict, wanted: str = "") -> str:
    """The profile to change: the one named, else the only one with a real FFB device. Anything unclear is an error, so
    nothing is ever changed in a profile that might not be the one in use."""
    if wanted:
        if wanted not in found:
            raise FFBError(f"no wheel profile called {wanted!r}; there are: {', '.join(found) or 'none'}")
        return wanted
    real = [n for n, p in found.items() if p["device"] and p["device"].lower() != "none"]
    if len(real) == 1:
        return real[0]
    raise FFBError("which wheel profile is in use? name it (live.ffb_profile): " + ", ".join(found))


def propose(current: float, scale: float) -> float:
    """The new strength: `scale` times the current one, but at most MAX_STEP either way, in the game's range."""
    s = max(1 - MAX_STEP, min(1 + MAX_STEP, scale))
    return round(max(LO, min(HI, current * s)), 2)


def apply(file: str | Path, new: float, running: Callable[[], bool] = game_running, now: Callable[[], float] = time.time) -> dict:
    """Write `new` as FFB Strength in `file`. Refuses while the game runs. Returns what was done."""
    if running():
        raise FFBError("RaceRoom is running; close it first (it rewrites these files itself)")
    if not LO <= new <= HI:
        raise FFBError(f"{new} is outside the allowed range {LO} to {HI}")
    f = Path(file)
    data = f.read_bytes()
    m = LINE.search(data)
    if not m:
        raise FFBError("no FFB Strength line in that profile")
    old = float(m.group(2))
    backup = f.with_name(f"{f.name}.bak-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now()))}")
    shutil.copy2(f, backup)
    patched = LINE.sub(lambda mm: mm.group(1) + f"{new:g}".encode() + mm.group(3), data, count=1)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_bytes(patched)
    tmp.replace(f)
    check = LINE.search(f.read_bytes())
    if not check or abs(float(check.group(2)) - new) > 1e-9:
        shutil.copy2(backup, f)                                       # could not verify: put the original back
        raise FFBError("the new value could not be read back; the profile was restored")
    return {"file": str(f), "old": old, "new": new, "backup": str(backup)}
