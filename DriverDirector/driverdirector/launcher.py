"""Start Le Mans Ultimate through Steam and report whether it is running."""
from __future__ import annotations

import os
import subprocess
import sys

PROCESS = "Le Mans Ultimate.exe"


def is_running() -> bool:
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {PROCESS}", "/NH"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    return PROCESS.lower() in out.lower()


def launch(app_id: int) -> bool:
    """Launch via steam://rungameid so Steam login and anti-cheat start normally.
    Returns False (and does nothing) if the game is already running."""
    if is_running():
        return False
    if sys.platform != "win32":
        raise OSError("launching is only supported on Windows")
    os.startfile(f"steam://rungameid/{int(app_id)}")      # type: ignore[attr-defined]
    return True


def close() -> bool:
    """Ask the game to close normally (like clicking its window's X). No force-kill."""
    if sys.platform != "win32" or not is_running():
        return False
    r = subprocess.run(["taskkill", "/IM", PROCESS], capture_output=True, text=True, creationflags=0x08000000)
    return r.returncode == 0


# ---- OBS Studio ------------------------------------------------------------------------------
OBS_PROCESS = "obs64.exe"


def find_obs(configured: str = "") -> str | None:
    """obs64.exe: the configured path, the installer's registry entry, or the usual install folders."""
    if configured and os.path.isfile(configured):
        return configured
    cands = []
    if sys.platform == "win32":
        import winreg
        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OBS Studio"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\OBS Studio"),
                          (winreg.HKEY_CURRENT_USER, r"SOFTWARE\OBS Studio")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    cands.append(os.path.join(winreg.QueryValue(k, None), "bin", "64bit", OBS_PROCESS))
            except OSError:
                pass
    for root in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        cands.append(os.path.join(root, "obs-studio", "bin", "64bit", OBS_PROCESS))
        cands.append(os.path.join(root, "Steam", "steamapps", "common", "OBS Studio", "bin", "64bit", OBS_PROCESS))
    return next((c for c in cands if c and os.path.isfile(c)), None)


def obs_running() -> bool:
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {OBS_PROCESS}", "/NH"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout
    return OBS_PROCESS.lower() in out.lower()


def clear_obs_crash_flags(appdata: str | None = None) -> int:
    """OBS marks each run with a file in %APPDATA%\\obs-studio\\.sentinel and removes it on a clean exit. A file left
    behind (OBS was force-closed) makes the next start wait on a "Crash Detected" dialog with the websocket down, so
    nothing can record until someone clicks it. Only call this while OBS is not running. Returns files removed."""
    root = os.path.join(appdata or os.environ.get("APPDATA", ""), "obs-studio", ".sentinel")
    n = 0
    for name in os.listdir(root) if os.path.isdir(root) else []:
        if name.startswith("run_"):
            try:
                os.remove(os.path.join(root, name))
                n += 1
            except OSError:
                pass
    return n


def launch_obs(configured: str = "", scene: str = "") -> bool:
    """Start OBS Studio. It must run from its own bin folder (it looks for its data files relative to the
    working directory), so we set that. Returns False if it is already running."""
    if obs_running():
        return False
    exe = find_obs(configured)
    if not exe:
        raise OSError("OBS Studio was not found; set its path in the OBS settings")
    clear_obs_crash_flags()
    # Minimised to the tray: an OBS window on top of the game takes the middle click that hides the game's UI.
    cmd = [exe, "--minimize-to-tray"] + (["--scene", scene] if scene else [])
    subprocess.Popen(cmd, cwd=os.path.dirname(exe), close_fds=True,
                     creationflags=0x00000008 | 0x00000200 if sys.platform == "win32" else 0)   # detached
    return True
