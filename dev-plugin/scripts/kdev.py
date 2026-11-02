#!/usr/bin/env python3
"""KerbApex dev tools: a debugger and a sandboxed self-improvement loop for DriverDirector and StintRadio.
Standard library only. Every command prints markdown or JSON; non-zero exit means a check failed.

  kdev.py doctor [--app stintradio|driverdirector]   health: venv, tests, running app, log errors, git state
  kdev.py debug  [--app ...]                          doctor plus the full failing-test output, recent log
                                                      tracebacks and the newest StintRadio recording summary
  kdev.py round start [NAME]                          a fresh git worktree and branch improve/<id> (the sandbox)
  kdev.py round check ID                              tests plus a dashboard smoke test of the sandbox copy
  kdev.py round finish ID                             writes the round report (diff, tests, smoke); never merges
  kdev.py loop --rounds N [--go] [--app ...]          runs N rounds headless with the Claude CLI (without --go it
                                                      only prints what it would do)

The sandbox: every round works in its own git worktree under %LOCALAPPDATA%KerbApexounds on its own branch, with its own temporary
settings folder (APPDATA), a spare port, and no API keys in the environment. The real app, its settings, voice
cache and recordings are never touched, nothing is pushed, and nothing is merged: the branch is for you to review.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Outside OneDrive (which locks files in a worktree); KERBAPEX_ROUNDS overrides.
ROUNDS = Path(os.environ.get("KERBAPEX_ROUNDS") or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KerbApex" / "rounds")
APPS = {"stintradio": ROOT / "StintRadio", "driverdirector": ROOT / "DriverDirector"}
PORTS = {"stintradio": 6420, "driverdirector": 6410}
HEALTH = {"stintradio": "/api/health", "driverdirector": "/api/status"}
SMOKE_PORT = {"stintradio": 6498, "driverdirector": 6497}
APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
SECRETS = ("ELEVENLABS_API_KEY", "ANTHROPIC_API_KEY", "FISH_API_KEY", "FISH_AUDIO_API_KEY", "OPENAI_API_KEY")

# What each dashboard page must contain to count as alive (an element id the script relies on).
PAGES = {"stintradio": {"/": ["delta"], "/race": ["pos", "delta", "fuel", "trackmap"], "/api/health": None, "/api/state": None,
                        "/api/race": None, "/api/track": None},
         "driverdirector": {"/": [], "/api/status": None, "/api/settings": None}}


# DriverDirector's built-in simulated race (no game, no OBS) on a spare port; `--demo` itself ignores the port setting.
DD_DEMO = """
import uvicorn
from driverdirector.config import Settings
from driverdirector.director import Director
from driverdirector.camera import DryRunGame
from driverdirector.obs import DryRunObs
from driverdirector.lmu_api import DemoLMU
from driverdirector.__main__ import make_source
from driverdirector.server import create_app
s = Settings(overlay_port={port})
d = Director(s, make_source(True), DryRunGame(), DryRunObs(), DemoLMU())
uvicorn.run(create_app(d), host="127.0.0.1", port={port}, log_level="warning")
"""


def main_root() -> Path:
    """The main checkout, also when this script runs from a round's worktree (that has no .venv)."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, capture_output=True,
                                text=True, timeout=20).stdout.strip()
        return Path(common).parent if common else ROOT
    except (OSError, subprocess.SubprocessError):
        return ROOT


def venv_python(app_dir: Path) -> str:
    for base in (app_dir, main_root() / app_dir.name, main_root() / "StintRadio", ROOT / "StintRadio"):
        p = base / ".venv" / "Scripts" / "python.exe"
        if p.exists():
            return str(p)
    return sys.executable


def run(cmd, cwd=None, env=None, timeout=600):
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout} s"


def git(*args, cwd=ROOT):
    return run(["git", *args], cwd=cwd, timeout=120)


def http(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:                                           # noqa: BLE001
        return getattr(e, "code", 0), str(e)


def clean_env(tree: Path | None = None, appdata: Path | None = None) -> dict:
    """The environment of a sandboxed run: no API keys, its own settings folder, the sandbox's code first."""
    env = {k: v for k, v in os.environ.items() if k not in SECRETS and "TOKEN" not in k.upper()}
    if appdata is not None:
        env["APPDATA"] = str(appdata)
    if tree is not None:
        env["PYTHONPATH"] = os.pathsep.join(str(tree / a) for a in ("StintRadio", "DriverDirector"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# ---- doctor / debug ------------------------------------------------------------------------------------------------

def log_errors(path: Path, limit: int = 12) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
    hits = [ln for ln in lines if re.search(r"Traceback|Error|ERROR|Exception|could not|failed", ln)]
    return hits[-limit:]


def tests(app: str, tree: Path = ROOT, full: bool = False):
    app_dir = tree / {"stintradio": "StintRadio", "driverdirector": "DriverDirector"}[app]
    rc, out = run([venv_python(APPS[app]), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], cwd=app_dir,
                  env=clean_env(tree, Path(tempfile.mkdtemp(prefix="kdev_appdata_"))), timeout=900)
    tail = out if full else "\n".join(out.strip().splitlines()[-3:])
    return rc == 0, tail


def recording_summary() -> str:
    d = APPDATA / "StintRadio" / "sessions"
    files = sorted(d.glob("*.sqlite*"), key=lambda p: p.stat().st_mtime) if d.exists() else []
    if not files:
        return "no StintRadio recordings"
    f = files[-1]
    try:
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        n = con.execute("select count(*) from frames").fetchone()[0]
        laps = con.execute("select count(*) from laps").fetchone()[0]
        t = con.execute("select min(t), max(t) from frames").fetchone()
        return f"{f.name}: {n} frames, {laps} laps, {((t[1] or 0) - (t[0] or 0)) / 60:.1f} min recorded"
    except sqlite3.Error as e:
        return f"{f.name}: unreadable ({e})"


def doctor(apps, full=False) -> tuple[str, bool]:
    out, ok_all = ["# KerbApex doctor", ""], True
    for app in apps:
        out.append(f"## {app}")
        d = APPS[app]
        out.append(f"- venv: {'yes' if (d / '.venv').exists() else 'MISSING'}")
        ok, tail = tests(app, full=full)
        ok_all &= ok
        out.append(f"- tests: {'pass' if ok else 'FAIL'}\n```\n{tail}\n```")
        code, body = http(f"http://127.0.0.1:{PORTS[app]}{HEALTH[app]}", 2)
        out.append(f"- running: {'yes' if code == 200 else 'no'}")
        log = APPDATA / ("StintRadio" if app == "stintradio" else "DriverDirector") / (
            "stintradio.log" if app == "stintradio" else "driverdirector.log")
        errs = log_errors(log)
        out.append(f"- recent log problems ({log.name}): {len(errs)}")
        out += [f"  - `{e[:200]}`" for e in errs]
        out.append("")
    rc, st = git("status", "--short")
    changed = [ln for ln in st.splitlines() if ln.strip()]
    rc, ahead = git("rev-list", "--count", "@{u}..HEAD")
    out.append(f"## git\n- uncommitted files: {len(changed)}\n- commits not pushed: {ahead.strip() if rc == 0 else '?'}")
    return "\n".join(out), ok_all


# ---- rounds ----------------------------------------------------------------------------------------------------------

def round_dir(rid: str) -> Path:
    return ROUNDS / rid


def round_start(name: str = "", base: str = "HEAD") -> str:
    rid = time.strftime("%Y%m%d-%H%M%S") + (("-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")) if name else "")
    ROUNDS.mkdir(parents=True, exist_ok=True)
    rc, out = git("worktree", "add", str(round_dir(rid)), "-b", f"improve/{rid}", base)
    if rc:
        raise SystemExit(f"could not create the sandbox: {out}")
    return rid


def smoke(app: str, tree: Path, appdata: Path) -> list[tuple[str, bool, str]]:
    """Starts the sandbox copy of the app on a spare port with empty settings, fetches its pages, stops it."""
    port = SMOKE_PORT[app]
    folder = appdata / ("StintRadio" if app == "stintradio" else "DriverDirector")
    folder.mkdir(parents=True, exist_ok=True)
    if app == "stintradio":
        (folder / "settings.json").write_text(json.dumps({"port": port}), encoding="utf-8")
        cmd = [venv_python(APPS[app]), "-m", "stintradio"]
    else:
        cmd = [venv_python(APPS[app]), "-c", DD_DEMO.format(port=port)]
    proc = subprocess.Popen(cmd, cwd=tree / ("StintRadio" if app == "stintradio" else "DriverDirector"),
                            env=clean_env(tree, appdata), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    results = []
    try:
        for _ in range(40):
            if http(f"http://127.0.0.1:{port}{HEALTH[app]}", 1)[0] == 200:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        for path, needles in PAGES[app].items():
            code, body = http(f"http://127.0.0.1:{port}{path}")
            miss = [n for n in (needles or []) if f'id="{n}"' not in body]
            results.append((path, code == 200 and not miss, f"HTTP {code}" + (f", missing ids {miss}" if miss else "")))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def round_check(rid: str, apps) -> tuple[str, bool]:
    tree = round_dir(rid)
    if not tree.exists():
        raise SystemExit(f"no such round: {rid}")
    out, ok_all = [f"# Round {rid}: checks", ""], True
    for app in apps:
        ok, tail = tests(app, tree)
        ok_all &= ok
        out.append(f"- {app} tests: {'pass' if ok else 'FAIL'}\n```\n{tail}\n```")
        appdata = Path(tempfile.mkdtemp(prefix="kdev_smoke_"))
        try:
            res = smoke(app, tree, appdata)
        finally:
            shutil.rmtree(appdata, ignore_errors=True)
        for path, ok, note in res:
            ok_all &= ok
            out.append(f"- {app} {path}: {'ok' if ok else 'FAIL'} ({note})")
    return "\n".join(out), ok_all


def round_finish(rid: str, apps) -> Path:
    tree = round_dir(rid)
    report, ok = round_check(rid, apps)
    _, stat = git("diff", "--stat", "HEAD...improve/" + rid, cwd=ROOT)
    _, log = git("log", "--oneline", "HEAD..improve/" + rid, cwd=ROOT)
    body = (f"{report}\n\n## Commits on improve/{rid}\n```\n{log.strip() or '(none)'}\n```\n\n## Diff against main\n"
            f"```\n{stat.strip() or '(no changes)'}\n```\n\nVerdict: {'all checks passed' if ok else 'CHECKS FAILED'}.\n\n"
            f"To review: `git diff HEAD...improve/{rid}`. To keep it: `git merge improve/{rid}`. To drop it: "
            f"`git worktree remove --force {tree}` and `git branch -D improve/{rid}`.\n")
    p = tree / "ROUND_REPORT.md"
    p.write_text(body, encoding="utf-8")
    return p


# ---- the loop --------------------------------------------------------------------------------------------------------

def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data")
    hits = glob.glob(os.path.join(base, "*", "claude-code", "*", "*", "claude.exe"))
    return max(hits, key=os.path.getmtime) if hits else None


ROUND_PROMPT = """You are running one self-improvement round for the KerbApex apps ({apps}) in a sandbox: this folder is a git
worktree on branch improve/{rid}. Read dev-plugin/backlog.md and dev-plugin/skills/improve/SKILL.md first and follow them.

The user may be RACING right now. Never show a window on the screen (nothing visible, topmost or on a real monitor: test
with off-screen coordinates, headless rendering or unit tests) and never make a sound. Never restart or stop the running
StintRadio, never touch the overlay that is up, never press keys or move the mouse.

Rules: work only inside this folder. Never read or write %APPDATA%, the real settings, voice caches, recordings or API
keys. Never start an app on its normal port (use `python dev-plugin/scripts/kdev.py round check {rid}`, which uses
spare ports and an empty settings folder). Never push, merge, delete branches or change git config.

{task}Do this: pick the ONE most valuable item from the backlog (dashboard first, unless a task is given above), implement it with tests, run
`python dev-plugin/scripts/kdev.py round check {rid}` until it passes, update the backlog (tick it, add what you
learnt), commit on this branch with a clear message, and finish with a short summary of what changed and why.
"""


def loop(rounds: int, apps, go: bool, base: str = "HEAD", task: str = "") -> None:
    claude = find_claude()
    print(f"# Self-improvement loop: {rounds} round(s) on {', '.join(apps)}")
    if not go:
        print("Dry run (no --go): each round would create a sandbox worktree, run the Claude CLI headless inside it "
              "with the improve instructions, then run the checks and write a report. Nothing was started.")
        print(f"Claude CLI: {claude or 'NOT FOUND'}")
        return
    if not claude:
        raise SystemExit("the Claude CLI was not found")
    for n in range(1, rounds + 1):
        rid = round_start(f"auto{n}", base)
        tree = round_dir(rid)
        print(f"\n## Round {n}: {rid}")
        env = clean_env(tree, Path(tempfile.mkdtemp(prefix="kdev_appdata_")))
        env["PATH"] = os.environ.get("PATH", "")
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        env["USERPROFILE"] = os.environ.get("USERPROFILE", "")
        env["LOCALAPPDATA"] = os.environ.get("LOCALAPPDATA", "")
        rc, out = run([claude, "-p", ROUND_PROMPT.format(apps=", ".join(apps), rid=rid, task=(f"YOUR TASK FOR THIS ROUND (from the user): {task}\n\n" if task else "")), "--permission-mode", "acceptEdits",
                       "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", "Bash(python:*)", "Bash(git add:*)",
                       "Bash(git commit:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"],
                      cwd=tree, env=env, timeout=3600)
        print(out.strip()[-3000:])
        report = round_finish(rid, apps)
        print(f"report: {report}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kdev")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("doctor", "debug"):
        p = sub.add_parser(name)
        p.add_argument("--app", choices=list(APPS))
    r = sub.add_parser("round")
    r.add_argument("action", choices=["start", "check", "finish"])
    r.add_argument("arg", nargs="?", default="")
    r.add_argument("--app", choices=list(APPS))
    r.add_argument("--base", default="HEAD", help="start the round from this branch or commit")
    lp = sub.add_parser("loop")
    lp.add_argument("--base", default="HEAD", help="start each round from this branch or commit")
    lp.add_argument("--task", default="", help="what the round should do, instead of the top backlog item")
    lp.add_argument("--rounds", type=int, default=1)
    lp.add_argument("--go", action="store_true")
    lp.add_argument("--app", choices=list(APPS))
    a = ap.parse_args(argv)
    apps = [a.app] if getattr(a, "app", None) else list(APPS)

    if a.cmd in ("doctor", "debug"):
        text, ok = doctor(apps, full=a.cmd == "debug")
        if a.cmd == "debug":
            text += f"\n\n## Newest StintRadio recording\n{recording_summary()}"
        print(text)
        return 0 if ok else 1
    if a.cmd == "round":
        if a.action == "start":
            print(round_start(a.arg, a.base))
            return 0
        if not a.arg:
            raise SystemExit("give the round id")
        if a.action == "check":
            text, ok = round_check(a.arg, apps)
            print(text)
            return 0 if ok else 1
        print(round_finish(a.arg, apps).read_text(encoding="utf-8"))
        return 0
    loop(a.rounds, apps, a.go, a.base, a.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
