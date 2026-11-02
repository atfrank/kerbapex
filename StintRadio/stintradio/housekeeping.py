"""Finding and clearing stray StintRadio programs: overlays that were started twice, servers left over from an earlier run, extra
control panels. The control panel's "Clear stray overlays" and "Reset everything" buttons use this.

Everything is limited to ONE StintRadio, the one on a given port (6420 unless set otherwise): a test copy on another port, or a
second install, is never touched by the panel of the first.

An overlay is two processes on Windows (the virtual-environment launcher and the Python it starts), so programs are counted by the
process whose parent is not also one of them.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Callable, Optional

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DEFAULT_PORT = 6420
_LIST = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(pythonw?|msedge|StintRadio|KerbApex)\\.exe$' -and $_.CommandLine -match 'stintradio|kerbapex|app=http://127' } "
         "| ForEach-Object { [pscustomobject]@{ pid = $_.ProcessId; parent = $_.ParentProcessId; name = $_.Name; cmd = $_.CommandLine } } | ConvertTo-Json -Compress")


def kind(cmd: str) -> str:
    """What a StintRadio command line is: overlay, panel, browser (an Edge overlay window), server, or other."""
    c = cmd.lower()
    if "app=http://127" in c and "/overlay" in c:
        return "browser"
    m = re.search(r"-m\s+stintradio\b\s*(\S*)", c)
    if m:
        return {"overlay": "overlay", "gui": "panel", "studio": "other"}.get(m.group(1), "server")
    m = re.search(r"(?:^|[\\/\"\s])(stintradio|kerbapex)\.exe\"?\s*(\S*)", c)          # a packaged install: StintRadio.exe [overlay], KerbApex.exe
    if m:
        return "panel" if m.group(1) == "kerbapex" else {"overlay": "overlay", "gui": "panel", "studio": "other"}.get(m.group(2).strip('"'), "server")
    return "other"


def port_of(cmd: str) -> int:
    """The port of the StintRadio an overlay belongs to: from its address, else the usual one."""
    m = re.search(r"http://127\.0\.0\.1:(\d+)/overlay", cmd)
    return int(m.group(1)) if m else DEFAULT_PORT


def processes() -> list:
    """Every StintRadio-related process: {pid, parent, name, cmd, kind, port}."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _LIST], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW).stdout.strip()
        rows = json.loads(out) if out else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    rows = [rows] if isinstance(rows, dict) else rows
    return [{**r, "kind": kind(r.get("cmd") or ""), "port": port_of(r.get("cmd") or "")} for r in rows if kind(r.get("cmd") or "") != "other"]


def roots(procs: list, what: str, port: Optional[int] = None) -> list:
    """One entry per running program of a kind (the launcher and its child count once); for overlays, only those of this port."""
    mine = [p for p in procs if p["kind"] == what and (port is None or what not in ("overlay", "browser") or p.get("port") == port)]
    pids = {p["pid"] for p in mine}
    return [p for p in mine if p["parent"] not in pids]


def listener_pid(port: int) -> Optional[int]:
    """The process that is listening on this TCP port, if any."""
    if sys.platform != "win32":
        return None
    cmd = f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out) if out.isdigit() else None


def listening_pids() -> set:
    """Every process that listens on a TCP port."""
    if sys.platform != "win32":
        return set()
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }"],
                             capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(x) for x in out if x.isdigit()}


def kill(pids, tree: bool = True) -> None:
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", *(["/T"] if tree else []), "/PID", str(pid)], capture_output=True, timeout=20, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass


def clear_overlays(ask_to_quit: Optional[Callable[[], None]] = None, stop_asking: Optional[Callable[[], None]] = None, wait: float = 6.0,
                   port: int = DEFAULT_PORT) -> int:
    """Close every overlay of this StintRadio: first politely (each overlay closes itself when asked, and takes its Edge window with
    it), then by force. Returns how many had to be ended by force. Overlays of another StintRadio (another port) are left alone."""
    def mine() -> list:
        ps = processes()
        return roots(ps, "overlay", port) + roots(ps, "browser", port)

    if ask_to_quit:
        ask_to_quit()
        end = time.monotonic() + wait
        while time.monotonic() < end and mine():
            time.sleep(0.4)
    left = mine()
    kill([p["pid"] for p in left])
    time.sleep(0.5)
    if stop_asking:
        stop_asking()
    return len(left)


def clear_servers(port: int = DEFAULT_PORT) -> int:
    """End the StintRadio on this port, and any StintRadio server that listens nowhere (a leftover that never took its port).
    A server on another port (a test copy) is left alone."""
    ps = processes()
    listening = listening_pids()
    by_parent: dict = {}
    for p in ps:
        by_parent.setdefault(p["parent"], []).append(p["pid"])

    def tree(pid: int) -> set:
        out, todo = set(), [pid]
        while todo:
            x = todo.pop()
            if x not in out:
                out.add(x)
                todo += by_parent.get(x, [])
        return out

    ended = []
    mine = listener_pid(port)
    if mine:
        ended.append(mine)
    for r in roots(ps, "server"):
        if not (tree(r["pid"]) & listening):
            ended.append(r["pid"])
    kill(ended)
    return len(ended)
