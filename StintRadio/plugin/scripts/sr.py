#!/usr/bin/env python3
"""Command-line front end to a running StintRadio (its REST API, default http://127.0.0.1:6420).

Standard library only, so the Claude plugin needs nothing installed. Every command prints JSON and exits non-zero on
failure, so a skill can run it with Bash and read the result.

  sr.py health                     is the app running, where is the game, which voice and style
  sr.py start [--wait]             start the app in the background (finds the install; see `home`); --wait also starts
                                   the coach as soon as the game's live data is there
  sr.py home [PATH]                show or remember where StintRadio is installed
  sr.py on | off                   start / stop the coach (the game must be running with Enable Plugins on)
  sr.py briefing [KIND] [say]      what the engineer says at the start of a session (practice | qualifying | race); 'say' speaks it
  sr.py tips                       brake bias, shift timing and minimum-speed suggestions from the newest recorded session
  sr.py state                      what the coach sees now (status, reference, delta, latest corner notes)
  sr.py import                     store the fastest whole laps from Coach Dave Delta's local session files as references
  sr.py replay TRACK CAR [RANK] [TRACK_LEN]   play a stored lap through the coach (try it without driving)
  sr.py settings [JSON]            show settings, or deep-merge a JSON patch into them (applies from the next `on`)
  sr.py quit                       close the app
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("STINTRADIO_URL", "http://127.0.0.1:6420").rstrip("/")
APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
HOME_FILES = [APPDATA / "StintRadio" / "home.txt"]
HOME_FILES += sorted((Path(os.environ.get("LOCALAPPDATA", "")) / "Packages").glob(
    "Claude_*/LocalCache/Roaming/StintRadio/home.txt")) if os.environ.get("LOCALAPPDATA") else []


def out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def fail(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def api(method: str, path: str, body=None, timeout: float = 120.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        fail(f"{method} {path}: {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        fail(f"StintRadio is not running at {BASE}. Run: sr.py start")
    return json.loads(raw) if raw else None


def running() -> bool:
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=3).read()
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def find_home() -> Path | None:
    cands = []
    if os.environ.get("STINTRADIO_HOME"):
        cands.append(Path(os.environ["STINTRADIO_HOME"]))
    for f in HOME_FILES:
        try:
            cands.append(Path(f.read_text(encoding="utf-8").strip()))
        except OSError:
            pass
    cands.append(Path(__file__).resolve().parents[2])             # the plugin lives inside the project
    return next((c for c in cands if (c / "stintradio" / "__main__.py").is_file()), None)


def deep_merge(a: dict, b: dict) -> dict:
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            deep_merge(a[k], v)
        else:
            a[k] = v
    return a


def cmd_home(args):
    if args:
        p = Path(args[0]).expanduser().resolve()
        if not (p / "stintradio" / "__main__.py").is_file():
            fail(f"{p} does not look like a StintRadio install (no stintradio/__main__.py)")
        HOME_FILES[0].parent.mkdir(parents=True, exist_ok=True)
        HOME_FILES[0].write_text(str(p), encoding="utf-8")
    h = find_home()
    out({"home": str(h) if h else None})


def cmd_start(args):
    if running():
        if "--wait" in args:
            try:
                api("POST", "/api/start", {})
            except SystemExit:
                pass
        return out({"started": False, "message": "already running", "url": BASE})
    home = find_home()
    if not home:
        fail("cannot find StintRadio. Run: sr.py home <path to the StintRadio folder>")
    py = home / ".venv" / "Scripts" / "python.exe"
    py = py if py.is_file() else Path(sys.executable)
    cmd = [str(py), "-m", "stintradio"] + (["--start"] if "--wait" in args else [])
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
             if os.name == "nt" else 0)
    subprocess.Popen(cmd, cwd=str(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=flags)
    for _ in range(40):
        time.sleep(0.5)
        if running():
            return out({"started": True, "url": BASE, "home": str(home)})
    fail("StintRadio did not come up within 20 s; run it by hand to see the error: "
         f"cd \"{home}\" && .venv\\Scripts\\python.exe -m stintradio")


def cmd_health(args):
    h = {"home": str(find_home() or ""), "app_running": running()}
    if h["app_running"]:
        h.update(api("GET", "/api/health"))
        h["coach"] = api("GET", "/api/state").get("status")
    out(h)


def cmd_state(args):
    s = api("GET", "/api/state")
    for k in ("rolling", "ref_window", "cur_window"):
        if k in s:
            s[k] = f"{len(s[k])} points"
    out(s)


def cmd_tips(args):
    t = api("GET", "/api/tips")
    for line in t.get("lines", []):
        print(line)


def cmd_briefing(args):
    """What the engineer says at the start of a session (practice | qualifying | race), from earlier runs; 'say' also speaks it."""
    kind = next((a for a in args if a in ("practice", "qualifying", "race", "warm-up")), "")
    r = api("GET", f"/api/briefing?kind={kind}&say={'true' if 'say' in args else 'false'}")
    print(r.get("text") or r.get("error") or "Nothing to say yet.")


def cmd_on(args):
    out(api("POST", "/api/start", {}))
    print(f"Listening. The page is {BASE}/ (Steam overlay: Shift+Tab > web browser > pin).", file=sys.stderr)


def cmd_replay(args):
    if len(args) < 2:
        fail("usage: sr.py replay TRACK CAR [RANK] [TRACK_LEN]")
    body = {"track": args[0], "car": args[1], "rank": int(args[2]) if len(args) > 2 else 1,
            "track_len": float(args[3]) if len(args) > 3 else 0}
    out(api("POST", "/api/replay", body))


def cmd_settings(args):
    s = api("GET", "/api/settings")
    if args:
        try:
            patch = json.loads(" ".join(args))
        except ValueError as e:
            fail(f"settings patch is not valid JSON: {e}")
        s = api("PUT", "/api/settings", deep_merge(s, patch))
    out(s)


def cmd_ffb(args):
    """sr ffb: the force-feedback verdict and the change it would make. sr ffb apply --yes: write it (RaceRoom closed)."""
    if args[:1] == ["apply"]:
        if "--yes" not in args:
            fail("this changes a game profile: look at 'sr ffb', then run 'sr ffb apply --yes' once the user has agreed")
        return out(api("POST", "/api/ffb/apply", {"confirm": True}))
    out(api("GET", "/api/ffb"))


def cmd_quit(args):
    """Stop the app process (found by its command line)."""
    if not running():
        return out({"running": False})
    try:
        api("POST", "/api/stop", {})
    except SystemExit:
        pass
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -match "
              "('-m ' + 'stintradio') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    out({"running": running()})


COMMANDS = {
    "health": cmd_health, "start": cmd_start, "home": cmd_home, "on": cmd_on,
    "off": lambda a: out(api("POST", "/api/stop", {})), "state": cmd_state, "tips": cmd_tips, "briefing": cmd_briefing,
    "import": lambda a: out(api("POST", "/api/import-coach-dave", {}, timeout=600)),
    "replay": cmd_replay, "settings": cmd_settings, "quit": cmd_quit, "ffb": cmd_ffb,
}


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help", "help") or a[0] not in COMMANDS:
        print(__doc__)
        return 0 if not a or a[0] in ("-h", "--help", "help") else 2
    COMMANDS[a[0]](a[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
