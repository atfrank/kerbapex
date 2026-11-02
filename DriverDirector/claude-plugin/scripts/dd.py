#!/usr/bin/env python3
"""Command-line front end to a running DriverDirector (its REST API, default http://127.0.0.1:6410).

Standard library only, so the Claude plugin needs nothing installed. Every command prints JSON (or short text for
`watch`), and exits non-zero on failure, so a skill can run it with Bash and read the result.

  dd.py health                     what is installed/running (app, LMU, OBS, voice runtime, music model, claude CLI)
  dd.py start [--demo]             start the app in the background (finds the install; see `home`)
  dd.py home [PATH]                show or remember where DriverDirector is installed
  dd.py launch game|obs            start Le Mans Ultimate / OBS
  dd.py replays                    the replay library
  dd.py load ID                    load a replay in the game and read its race history (waits until ready)
  dd.py roster                     drivers in the loaded race
  dd.py settings [JSON]            show settings, or deep-merge a JSON patch into them
  dd.py plan                       analyse + plan with the current settings: length, picture-in-picture boxes and the
                                   driver's own mistakes the narrator will call (all times are VIDEO times)
  dd.py preflight                  check the replay is on screen, OBS, and that the game UI can be hidden (run does this)
  dd.py run                        record + produce the whole video (long!)
  dd.py produce                    finish the last recording (voice, music, compose)
  dd.py rerecord-reference         record only the reference lap (best qualifying lap box) again and finish the video
  dd.py redo-commentary            write, voice and mix the commentary again over the last recording (nothing is recorded)
  dd.py voices                     which cloud voice keys the app sees (true/false only) and the engine in use
  dd.py voice-test [ENGINE] [TEXT] voice one short line (engines: chatterbox, edge, fish, elevenlabs) and save it
  dd.py music                      prepare the music loop now
  dd.py watch [SECONDS]            follow progress until the job ends (default 540 s, then rerun)
  dd.py youtube [status|setup [PATH]|connect|disconnect|draft [VIDEO]|upload JSON]   publish a finished video (asks first!)
  dd.py cleanup [--yes] [--purge DAYS]   list leftover raw recordings; --yes moves them aside, --purge deletes old ones
  dd.py claude-login               is the Claude CLI that writes the commentary signed in? prints the exact sign-in command
                                   for the user's own terminal (Git Bash and PowerShell). It never signs in for them.
  dd.py raceroom list|scene|make NAME [...]|upload VIDEO NAME|draft VIDEO NAME   RaceRoom Racing Experience videos (no app
                                   server needed; runs `python -m driverdirector raceroom ...` and streams its output)
  dd.py cancel | exit-replay | status
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("DRIVERDIRECTOR_URL", "http://127.0.0.1:6410").rstrip("/")
APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
HOME_FILES = [APPDATA / "DriverDirector" / "home.txt"]
HOME_FILES += sorted((Path(os.environ.get("LOCALAPPDATA", "")) / "Packages").glob(
    "Claude_*/LocalCache/Roaming/DriverDirector/home.txt")) if os.environ.get("LOCALAPPDATA") else []
TERMINAL = ("done", "error", "idle")


def out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


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
        fail(f"DriverDirector is not running at {BASE}. Run: dd.py start")
    return json.loads(raw) if raw else None


def fail(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def running() -> bool:
    try:
        urllib.request.urlopen(BASE + "/api/status", timeout=3).read()
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


def find_home() -> Path | None:
    cands = []
    if os.environ.get("DRIVERDIRECTOR_HOME"):
        cands.append(Path(os.environ["DRIVERDIRECTOR_HOME"]))
    for f in HOME_FILES:
        try:
            cands.append(Path(f.read_text(encoding="utf-8").strip()))
        except OSError:
            pass
    cands.append(Path(__file__).resolve().parents[2])             # the plugin lives inside the project
    return next((c for c in cands if (c / "driverdirector" / "__main__.py").is_file()), None)


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
        if not (p / "driverdirector" / "__main__.py").is_file():
            fail(f"{p} does not look like a DriverDirector install (no driverdirector/__main__.py)")
        HOME_FILES[0].parent.mkdir(parents=True, exist_ok=True)
        HOME_FILES[0].write_text(str(p), encoding="utf-8")
    h = find_home()
    out({"home": str(h) if h else None})


def cmd_start(args):
    if running():
        return out({"started": False, "message": "already running", "url": BASE})
    home = find_home()
    if not home:
        fail("cannot find DriverDirector. Run: dd.py home <path to the DriverDirector folder>")
    py = home / ".venv" / "Scripts" / "python.exe"
    py = py if py.is_file() else Path(sys.executable)
    cmd = [str(py), "-m", "driverdirector", "--no-browser"] + (["--demo"] if "--demo" in args else [])
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
             if os.name == "nt" else 0)
    subprocess.Popen(cmd, cwd=str(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=flags)
    for _ in range(40):
        time.sleep(0.5)
        if running():
            return out({"started": True, "url": BASE, "home": str(home)})
    fail("DriverDirector did not come up within 20 s; run it by hand to see the error: "
         f"cd \"{home}\" && .venv\\Scripts\\python.exe -m driverdirector")


def cmd_health(args):
    h = {"home": str(find_home() or ""), "app_running": running()}
    if h["app_running"]:
        h["game"] = api("GET", "/api/game")
        h["music"] = api("GET", "/api/music")
        try:
            v = api("GET", "/api/voices")
            h["voices"] = {"chatterbox": v.get("chatterbox"), "cloud_keys": v.get("cloud_keys")}
        except SystemExit:
            pass
        s = api("GET", "/api/settings")
        h["focus_driver"] = s["direct"].get("focus_driver")
        h["commentary"] = {"enabled": s["commentary"]["enabled"], "writer": s["commentary"]["backend"],
                           "engine": s["commentary"].get("tts_engine"), "voice": s["commentary"]["voice"]}
        h["reference_lap"] = s["direct"].get("reference_lap")
        h["driving_feedback"] = s["direct"].get("driving_feedback")
        h["music_source"] = s["music"]["source"]
        h["status"] = api("GET", "/api/status")["stage"]
        h.update(api("GET", "/api/tools"))                   # ffmpeg and the Claude CLI, as the app finds them
    else:
        from shutil import which
        h["ffmpeg"] = which("ffmpeg")
    out(h)


def cmd_launch(args):
    if not args or args[0] not in ("game", "obs"):
        fail("usage: dd.py launch game|obs")
    out(api("POST", "/api/game/launch" if args[0] == "game" else "/api/obs/launch", {}))


def _open_replay(rid: int) -> bool:
    """Load a replay and wait until it is really ON SCREEN. The game can report a replay as open (and answer data calls)
    while it still shows the main menu; nothing recorded then would be the replay."""
    api("POST", f"/api/replays/{rid}/load", {})
    for _ in range(60):                                      # the game needs a while to open a replay
        time.sleep(3)
        if api("GET", "/api/game").get("replay_active"):
            break
    else:
        fail("the replay did not become active within 3 minutes")
    for _ in range(30):                                      # up to about 90 s for the replay screen itself
        if api("GET", "/api/game").get("screen") != "menu":
            time.sleep(6)                                    # let it finish drawing
            return True
        time.sleep(3)
    return False


def cmd_load(args):
    if not args:
        fail("usage: dd.py load REPLAY_ID")
    rid = int(args[0])
    if not _open_replay(rid):                                # stuck on the menu: leave cleanly, let it settle, once more
        api("POST", "/api/game/exit_replay", {})
        time.sleep(25)
        if not _open_replay(rid):
            fail("the game reports the replay open but still shows its main menu. Run dd.py exit-replay, wait "
                 "20 seconds and try again")
    out(api("POST", "/api/history", {}))


def cmd_settings(args):
    s = api("GET", "/api/settings")
    if args:
        try:
            patch = json.loads(" ".join(args))
        except ValueError as e:
            fail(f"settings patch is not valid JSON: {e}")
        s = api("PUT", "/api/settings", deep_merge(s, patch))
    out(s)


def cmd_plan(args):
    summary = api("POST", "/api/plan", {})
    p = api("GET", "/api/plan")
    roster = {r["id"]: r["driver"] for r in api("GET", "/api/roster")}
    mm = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
    t0 = p["summary"]["t0"]
    # The plan is on the REPLAY clock; the finished video starts at t0. Everything shown to the user is VIDEO time
    # (the replay time is kept as replay_at for reference).
    drv = p.get("driving") or {}
    try:                                        # how long it will take and the disk it needs (never blocks the plan)
        est = api("GET", "/api/estimate")
    except SystemExit:
        est = None
    out({"summary": summary, "estimate": est,
         "length": mm(p["summary"]["t1"] - t0),
         "focus": roster.get(p["summary"]["focus_id"]),
         "inlays": [{"video_at": mm(i["show_at"] - t0), "video_until": mm(i["show_until"] - t0),
                     "replay_at": mm(i["show_at"]), "label": i["label"], "camera": i["camera"],
                     "slow_motion": i.get("slow", 1.0) < 1.0, "replay": bool(i.get("replay")),
                     "names": i.get("names", []),
                     "drivers": [roster.get(d, d) for d in i["drivers"]]} for i in p["inlays"]],
         "driving": {"note": drv.get("note", ""),
                     "mistakes": [{"video_at": mm(m["at"] - t0), "lap": m["lap"], "where": m["where"],
                                   "lost_s": m["loss"], "what": m["desc"]} for m in drv.get("mistakes", [])]}})


def cmd_sync_test(args):
    """sync-test [DELAY ...]: record one short stretch once per overlay delay (default 0.4 1.0 1.6), side by side"""
    body = {"delays": [float(a) for a in args]} if args else {}
    out(api("POST", "/api/sync_test", body))


def cmd_cut(path):
    def run(args):
        """highlights|shorts [VIDEO]: cut them from the last finished video (or the one given)"""
        out(api("POST", path, {"video": args[0]} if args else {}))
    return run


def cmd_rebuild_recording(args):
    """rebuild-recording FOLDER: recreate the recording info of the planned race from the raw files in FOLDER"""
    if not args:
        fail("usage: dd.py rebuild-recording FOLDER   (the race must be loaded and planned)")
    out(api("POST", "/api/recording/rebuild", {"folder": args[0]}))


def cmd_voice_test(args):
    """voice-test [engine] [text...]: voice one short line with an engine and save it; prints where the file is."""
    engines = ("chatterbox", "edge", "fish", "elevenlabs")
    body: dict = {}
    if args and args[0] in engines:
        body["engine"], args = args[0], args[1:]
    if args:
        body["text"] = " ".join(args)
    out(api("POST", "/api/voices/test", body, timeout=240.0))


def cmd_voices(args):
    v = api("GET", "/api/voices")
    s = api("GET", "/api/settings")["commentary"]
    out({"engine": s["tts_engine"], "cloud_keys": v.get("cloud_keys"), "chatterbox": v.get("chatterbox"),
         "elevenlabs_voice_id": s.get("elevenlabs_voice_id"), "fish_voice_id": s.get("fish_voice_id") or "(default)",
         "fish_model": s.get("fish_model")})


def cmd_claude_login(args):
    """Read-only: is the commentary writer's Claude CLI signed in, and what must the USER type in their own terminal if
    not. The desktop app is a packaged app, so the CLI has two paths: the short one only the app sees, and the real one
    under AppData\\Local\\Packages that a normal terminal needs."""
    import glob
    appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
    found = []
    for pat in (os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code", "*", "*", "claude.exe"),
                os.path.join(appdata, "Claude", "claude-code", "*", "*", "claude.exe")):
        found += glob.glob(pat)
    key = lambda p: [int(x) if x.isdigit() else 0 for x in Path(p).parts[-3].split(".")]
    pkg = sorted((p for p in found if "Packages" in p), key=key)
    short = sorted((p for p in found if "Packages" not in p), key=key)
    exe = (pkg or short or [None])[-1]
    if not exe:
        fail("the Claude CLI was not found. Install or update the Claude desktop app, then run this again.")
    state = None
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        state = bool(json.loads(r.stdout).get("loggedIn"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    fwd = exe.replace("\\", "/")
    out({"logged_in": state, "cli": exe,
         "sign_in": None if state else {
             "git_bash": f'"{fwd}" auth login --claudeai',
             "powershell": f'& "{exe}" auth login --claudeai',
             "note": "Run it in your own terminal (not through Claude). It opens your browser to sign in; "
                     "tell Claude when it says it succeeded."}})


def youtube_setup(arg: str | None) -> None:
    """Point DriverDirector at the OAuth client file: the one given, else the newest client_secret*.json in Downloads.
    Only the project id and client type are shown; the secret in the file is never printed."""
    if arg and arg.strip().startswith("AIza"):
        fail("that is an API key; it cannot upload or edit videos. Create an OAuth client ID of type 'Desktop app' in "
             "the same Google Cloud project, download its JSON and run `dd youtube setup` again (see `dd youtube`).")
    if arg:
        path = Path(arg.strip().strip('"')).expanduser()
    else:
        found = sorted((Path.home() / "Downloads").glob("client_secret*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        if not found:
            fail("no client_secret*.json in your Downloads folder: download the OAuth client (type 'Desktop app') "
                 "from Google Cloud first, or give its path: dd youtube setup PATH")
        path = found[0]
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail(f"cannot read {path}: {e}")
    if "installed" not in d:
        fail("that file is not an OAuth client of type 'Desktop app' (it is "
             f"{'a Web application client' if 'web' in d else 'not an OAuth client file'}); create a Desktop app client")
    project = d["installed"].get("project_id", "?")
    fwd = str(path.resolve()).replace("\\", "/")              # forward slashes: no JSON escaping trouble
    cmd_settings_quiet({"youtube": {"client_secret_path": fwd}})
    st = api("GET", "/api/youtube")
    out({"client_file": fwd, "project_id": project, "client_type": "Desktop app",
         "check_in_google_cloud": [
             f"Project '{project}' has the YouTube Data API v3 enabled.",
             f"Project '{project}' > Audience > Test users lists the Google account that owns your channel.",
         ],
         "authorized": st.get("authorized"), "next_step": st.get("next_step")})


def cmd_settings_quiet(patch: dict) -> None:
    api("PUT", "/api/settings", deep_merge(api("GET", "/api/settings"), patch))


def cmd_youtube(args):
    """youtube | youtube setup [PATH] | youtube connect | youtube disconnect | youtube draft [VIDEO] | youtube upload JSON"""
    sub = args[0] if args else "status"
    if sub == "status":
        return out(api("GET", "/api/youtube"))
    if sub == "setup":
        return youtube_setup(" ".join(args[1:]) or None)
    if sub == "connect":
        return out(api("POST", "/api/youtube/connect", {}))
    if sub == "disconnect":
        return out(api("POST", "/api/youtube/disconnect", {}))
    if sub == "draft":
        q = "?video=" + urllib.parse.quote(args[1]) if len(args) > 1 else ""
        return out(api("GET", "/api/youtube/draft" + q))
    if sub == "upload":
        try:
            body = json.loads(" ".join(args[1:]))
        except ValueError as e:
            fail(f"upload needs a JSON body with video, title, description, privacy and confirm: {e}")
        return out(api("POST", "/api/youtube/upload", body))
    fail("usage: dd.py youtube [status|setup [PATH]|connect|disconnect|draft [VIDEO]|upload JSON]")


def cmd_raceroom(args):
    """RaceRoom mode: the app's own command line, output streamed. `make` drives the game window for 15 to 25 minutes."""
    home = find_home()
    if not home:
        fail("cannot find DriverDirector. Run: dd.py home <path to the DriverDirector folder>")
    py = home / ".venv" / "Scripts" / "python.exe"
    py = py if py.is_file() else Path(sys.executable)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    code = subprocess.call([str(py), "-m", "driverdirector", "raceroom", *args], cwd=str(home), env=env)
    if code:
        fail(f"raceroom {' '.join(args[:2])} exited with code {code}")


def cmd_live(args):
    fail("the live coach moved to StintRadio: use its plugin (/stintradio:radio) or 'Start StintRadio.bat' in the "
         "StintRadio folder")


def cmd_preflight(args):
    r = api("POST", "/api/preflight", {})
    for c in r["checks"]:
        print(("ok   " if c["ok"] else "FAIL ") + c["name"] + (f": {c['detail']}" if c["detail"] else ""))
    if not r["ok"]:
        fail("preflight failed; fix the items above before recording")
    print("preflight ok")


def cmd_run(args):
    """Check everything first (a hidden-UI failure used to show only half an hour into the recording)."""
    cmd_preflight([])
    out(api("POST", "/api/run", {}))


def cmd_cleanup(args):
    """cleanup (list) | cleanup --yes (move the leftovers aside) | cleanup --purge DAYS (delete old quarantined ones)"""
    body: dict = {}
    if "--yes" in args:
        body["sweep"] = True
    if "--purge" in args:
        i = args.index("--purge")
        body["purge_days"] = float(args[i + 1]) if len(args) > i + 1 else 7.0
    if body:
        return out(api("POST", "/api/cleanup", body))
    r = api("GET", "/api/cleanup")
    gb = lambda n: f"{n / 1e9:.2f} GB"
    print(f"folder: {r['folder']}")
    for c in r["candidates"]:
        print(f"  leftover  {gb(c['size']):>9}  {Path(c['path']).name}")
    print(f"{len(r['candidates'])} leftover recording(s), {gb(sum(c['size'] for c in r['candidates']))}; "
          f"already moved aside: {gb(r['quarantined_bytes'])} (deleted after {r['keep_days']:g} days)")
    print("finished videos are never touched. Run `cleanup --yes` to move the leftovers aside.")


def simple(method, path):
    return lambda args: out(api(method, path, {} if method == "POST" else None))


def cmd_watch(args):
    limit = float(args[0]) if args else 540.0
    end, last, since = time.time() + limit, None, time.time()
    while True:
        st = api("GET", "/api/status")
        line = f"{st['stage']} | {st['message'][:160]}"
        key = st["stage"] + (st["message"] if st["stage"] in ("done", "error", "warning") else "")
        if key != last or time.time() - since > 300:
            print(time.strftime("%H:%M:%S"), ("WARNING: " if st["stage"] == "warning" else "") + line,
                  f"| {round(st['progress'] * 100)}%", flush=True)
            last, since = key, time.time()
        recorded = st["stage"] == "recorded" and "Recording saved" in st["message"]
        if st["stage"] in ("done", "error") or recorded:
            if st["stage"] == "done":
                print("FINAL:", st.get("final") or st["message"])
            if recorded:
                print("NEEDS: close Le Mans Ultimate (or exit the replay), then run: dd.py produce")
            return sys.exit(1 if st["stage"] == "error" else 0)
        if st["stage"] == "idle":
            print("idle: nothing is running")
            return
        if time.time() > end:
            print("STILL RUNNING: run `dd.py watch` again to keep following")
            return
        time.sleep(10)


COMMANDS = {
    "health": cmd_health, "start": cmd_start, "home": cmd_home, "launch": cmd_launch, "load": cmd_load,
    "replays": simple("GET", "/api/replays"), "roster": simple("GET", "/api/roster"), "settings": cmd_settings,
    "plan": cmd_plan, "run": cmd_run, "preflight": cmd_preflight, "cleanup": cmd_cleanup, "produce": simple("POST", "/api/produce"),
    "music": simple("POST", "/api/music/prepare"), "cancel": simple("POST", "/api/cancel"),
    "rerecord-reference": simple("POST", "/api/rerecord_reference"), "redo-commentary": simple("POST", "/api/redo_commentary"), "voices": cmd_voices, "voice-test": cmd_voice_test,
    "exit-replay": simple("POST", "/api/game/exit_replay"), "status": simple("GET", "/api/status"),
    "sync-test": cmd_sync_test, "highlights": cmd_cut("/api/highlights"), "shorts": cmd_cut("/api/shorts"),
    "estimate": simple("GET", "/api/estimate"), "rebuild-recording": cmd_rebuild_recording,
    "watch": cmd_watch, "youtube": cmd_youtube, "raceroom": cmd_raceroom, "live": cmd_live, "claude-login": cmd_claude_login,
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
