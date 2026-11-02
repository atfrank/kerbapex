"""The Overlay and Tools tabs of the KerbApex control panel (see gui.py).

Overlay: shape, size, corner and distance from the edges, theme, border, how see-through, which window (the see-through native
one or the browser one), reset. They are sent to StintRadio (`/api/overlay`), and the overlay window follows them; the hotkeys and
dragging keep working, and what they change shows here too.

Tools: the driving suggestions, force-feedback advice, the Coach Dave lap import, a check of the whole setup, a check of which
program makes a sound, and the improvement rounds.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Optional

from .config import data_dir
from .overlay_window import CORNERS, SHAPES, clamp_size, clean_geom

BG, CARD, EDGE, TEXT, DIM = "#0b0e14", "#131824", "#242b3b", "#f3f6fa", "#8794a6"
GOOD, BAD, WARN = "#34e089", "#ff5d6c", "#ffc83d"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
REPO = Path(__file__).resolve().parents[2]                    # the folder with StintRadio/ and dev-plugin/ (not there in a packaged build)
DEFAULT_GEOM = {"anchor": "top-right", "w": 640, "h": 420, "dx": 24, "dy": 24}


# ---- pure helpers (tested) -------------------------------------------------------------------------------------------

def shape_geom(geom: Optional[dict], shape: str) -> dict:
    """The layout in a named shape (wide, square, tall, banner) with the area it has now, so changing shape does not change the size."""
    g = clean_geom(geom) or dict(DEFAULT_GEOM)
    base = next((s for s in SHAPES if s[0] == shape), None)
    if base is None:
        return g
    k = math.sqrt(g["w"] * g["h"] / (base[1] * base[2]))
    w, h = clamp_size(base[1] * k, base[2] * k)
    return {**g, "w": w, "h": h}


def sized(geom: Optional[dict], w, h) -> dict:
    g = clean_geom(geom) or dict(DEFAULT_GEOM)
    w, h = clamp_size(float(w), float(h))
    return {**g, "w": w, "h": h}


def placed(geom: Optional[dict], anchor: str, dx, dy) -> dict:
    g = clean_geom(geom) or dict(DEFAULT_GEOM)
    if anchor not in CORNERS:
        anchor = g["anchor"]
    return {**g, "anchor": anchor, "dx": int(max(0, float(dx))), "dy": int(max(0, float(dy)))}


def shape_name(geom: Optional[dict]) -> str:
    """Which named shape the layout is closest to, by its proportions."""
    g = clean_geom(geom) or DEFAULT_GEOM
    return min(SHAPES, key=lambda s: abs(math.log((s[1] / s[2]) / (g["w"] / g["h"]))))[0]


def load_prefs(path: Optional[Path] = None) -> dict:
    """What the panel remembers: which overlay window to use."""
    try:
        d = json.loads((path or data_dir() / "gui.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict, path: Optional[Path] = None) -> None:
    try:
        (path or data_dir() / "gui.json").write_text(json.dumps(prefs), encoding="utf-8")
    except OSError:
        pass


# Programs that, while a session is open, send this PC's sound to another device (so the engineer is heard twice, a moment apart)
REMOTE_SESSION_PROGRAMS = {"remoting_desktop.exe": "Chrome Remote Desktop", "teamviewer.exe": "TeamViewer", "anydesk.exe": "AnyDesk",
                           "parsecd.exe": "Parsec", "rustdesk.exe": "RustDesk"}


def remote_sessions(tasklist_csv: str) -> list:
    """Names of remote-desktop programs with a session open, from `tasklist /FO CSV /NH` output."""
    found = []
    for line in tasklist_csv.splitlines():
        name = line.split('","')[0].strip('"').lower()
        if name in REMOTE_SESSION_PROGRAMS and REMOTE_SESSION_PROGRAMS[name] not in found:
            found.append(REMOTE_SESSION_PROGRAMS[name])
    return found


def setup_checks(app_running: bool, games: dict, settings: Optional[dict], env: dict, ffmpeg: Optional[str], claude: Optional[str],
                 voice_cache: Optional[bool], remote: Optional[list] = None) -> list:
    """What a working setup needs, as (ok, what, what to do about it) rows. Only facts passed in; nothing is looked up here."""
    live = (settings or {}).get("live", {})
    rows = [
        (settings is not None, "Settings file is readable", "Fix or delete %APPDATA%\\StintRadio\\settings.json (the log says what is wrong)."),
        (app_running, "StintRadio is running", "Press Start in the Control tab."),
        (any(games.values()), "A game is running (" + ", ".join(n for n, v in games.items() if v) + ")" if any(games.values()) else
         "A game is running", "Start RaceRoom or Le Mans Ultimate; the coach attaches by itself."),
        (ffmpeg is not None, "ffmpeg is installed (voice clips, push-to-talk)", "Install ffmpeg (winget install Gyan.FFmpeg) and restart StintRadio."),
    ]
    if live.get("voice_engine") == "elevenlabs":
        rows.append((bool(env.get("ELEVENLABS_API_KEY")), "ELEVENLABS_API_KEY is set (your voice)", "Set it in Windows environment variables, then restart StintRadio."))
        if voice_cache is not None:
            rows.append((voice_cache, "Your voice clips are made", "They are made the first time the coach starts, with the key set."))
    rows.append((not remote, "No remote-desktop session is open" if not remote else "A remote-desktop session is open (" + ", ".join(remote) + ")",
                 "It sends this PC's sound to the other device too, so the engineer is heard twice a moment apart. Close the session, or mute the other device."))
    if live.get("ptt"):
        rows.append((claude is not None, "Claude CLI found (answers to spoken questions)", "Install and sign in to the Claude CLI; without it only built-in answers work."))
    return rows


def summarise_audiomon(text: str) -> list:
    """A log from audiomon.py as one line per program: how many bursts and how long it was audible."""
    open_: dict = {}
    total: dict = {}
    count: dict = {}
    def secs(t: str) -> float:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    for line in text.splitlines():
        m = re.match(r"([\d:.]+) SOUND  (\S+) \(pid (\d+)\)", line)
        if m:
            open_[m.group(3)] = (m.group(2), secs(m.group(1)))
            continue
        m = re.match(r"([\d:.]+) quiet  (\S+) \(pid (\d+)\)", line)
        if m and m.group(3) in open_:
            name, t0 = open_.pop(m.group(3))
            total[name] = total.get(name, 0.0) + max(0.0, secs(m.group(1)) - t0)
            count[name] = count.get(name, 0) + 1
    return [f"{n}: {count[n]} sounds, {total[n]:.1f} s audible" for n in sorted(total, key=total.get, reverse=True)]


# ---- helpers for the windows -----------------------------------------------------------------------------------------

def _row(parent, label: str, row: int, style: str = "Dim.TLabel"):
    ttk.Label(parent, text=label, style=style).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)


class OverlayTab(ttk.Frame):
    def __init__(self, book: ttk.Notebook, panel) -> None:
        super().__init__(book, style="Card.TFrame")
        self.panel = panel
        self.seen: dict = {}                         # what StintRadio said last, so a field is only rewritten when that changes
        self.vars = {"w": tk.StringVar(), "h": tk.StringVar(), "dx": tk.StringVar(), "dy": tk.StringVar(), "anchor": tk.StringVar(),
                     "theme": tk.StringVar(value="dark"), "brand": tk.BooleanVar(value=True), "border": tk.BooleanVar(value=False), "opacity": tk.DoubleVar(value=1.0)}
        self.note = tk.StringVar(value="Start the overlay in the Control tab; changes here show on it within a second. The window choice applies when it is started.")
        box = ttk.Frame(self, style="Card.TFrame")
        box.pack(fill="x", padx=18, pady=14)

        _row(box, "Shape", 0)
        shapes = ttk.Frame(box, style="Card.TFrame")
        shapes.grid(row=0, column=1, sticky="w")
        for name, _w, _h in SHAPES:
            ttk.Button(shapes, text=name.capitalize(), command=lambda n=name: self.send_geom(shape_geom(self.geom(), n))).pack(side="left", padx=(0, 8))

        _row(box, "Size (pixels)", 1)
        size = ttk.Frame(box, style="Card.TFrame")
        size.grid(row=1, column=1, sticky="w")
        ttk.Spinbox(size, from_=240, to=1800, textvariable=self.vars["w"], width=6).pack(side="left")
        ttk.Label(size, text="  x  ").pack(side="left")
        ttk.Spinbox(size, from_=160, to=1100, textvariable=self.vars["h"], width=6).pack(side="left")
        ttk.Button(size, text="Apply", command=lambda: self.send_geom(sized(self.geom(), self.vars["w"].get(), self.vars["h"].get()))).pack(side="left", padx=(12, 4))
        ttk.Button(size, text="Bigger", command=lambda: self.scale(1.1)).pack(side="left", padx=4)
        ttk.Button(size, text="Smaller", command=lambda: self.scale(1 / 1.1)).pack(side="left", padx=4)

        _row(box, "Place in the game window", 2)
        corner = ttk.Frame(box, style="Card.TFrame")
        corner.grid(row=2, column=1, sticky="w")
        ttk.Combobox(corner, textvariable=self.vars["anchor"], values=list(CORNERS), state="readonly", width=14).pack(side="left")
        ttk.Label(corner, text="  side").pack(side="left")
        ttk.Spinbox(corner, from_=0, to=3000, textvariable=self.vars["dx"], width=5).pack(side="left", padx=4)
        ttk.Label(corner, text="top/bottom").pack(side="left")
        ttk.Spinbox(corner, from_=0, to=3000, textvariable=self.vars["dy"], width=5).pack(side="left", padx=4)
        ttk.Button(corner, text="Apply", command=lambda: self.send_geom(placed(self.geom(), self.vars["anchor"].get(), self.vars["dx"].get(), self.vars["dy"].get()))).pack(side="left", padx=8)

        _row(box, "Look", 3)
        look = ttk.Frame(box, style="Card.TFrame")
        look.grid(row=3, column=1, sticky="w")
        for value in ("dark", "light"):
            ttk.Radiobutton(look, text=value.capitalize(), value=value, variable=self.vars["theme"],
                            command=lambda: self.put({"theme": self.vars["theme"].get()})).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(look, text="KerbApex mark", variable=self.vars["brand"], command=lambda: self.put({"brand": self.vars["brand"].get()})).pack(side="left", padx=12)
        ttk.Checkbutton(look, text="Border", variable=self.vars["border"], command=lambda: self.put({"border": self.vars["border"].get()})).pack(side="left", padx=4)

        _row(box, "How solid", 4)
        op = ttk.Frame(box, style="Card.TFrame")
        op.grid(row=4, column=1, sticky="w")
        self.scale_widget = ttk.Scale(op, from_=0.3, to=1.0, variable=self.vars["opacity"], length=260, command=lambda v: None)
        self.scale_widget.pack(side="left")
        self.scale_widget.bind("<ButtonRelease-1>", lambda e: self.put({"opacity": round(float(self.vars["opacity"].get()), 2)}))
        ttk.Label(op, text="   30 % to 100 %", style="Dim.TLabel").pack(side="left")

        _row(box, "Window", 5)
        win = ttk.Frame(box, style="Card.TFrame")
        win.grid(row=5, column=1, sticky="w")
        ttk.Radiobutton(win, text="See-through", value="native", variable=panel.engine,
                        command=panel.save_engine).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(win, text="Browser window", value="edge", variable=panel.engine,
                        command=panel.save_engine).pack(side="left")

        bar = ttk.Frame(self, style="Card.TFrame")
        bar.pack(fill="x", padx=18, pady=(4, 6))
        ttk.Button(bar, text="Reset the layout", command=lambda: self.send_geom(dict(DEFAULT_GEOM))).pack(side="left", padx=(0, 8))
        self.edit_button = ttk.Button(bar, text="Move / resize with the mouse", command=panel.toggle_edit)
        self.edit_button.pack(side="left", padx=(0, 8))
        tk.Label(self, textvariable=self.note, bg=CARD, fg=DIM, anchor="w", font=("Segoe UI", 9), wraplength=980, justify="left").pack(fill="x", padx=18)
        keys = ("Keys that work while the game has the focus:\n"
                "   Ctrl+Alt+O  move / resize        Ctrl+Alt+S  next shape        Ctrl+Alt+=  /  -  bigger / smaller\n"
                "   Ctrl+Alt+B  border        Ctrl+Alt+T  light / dark        Ctrl+Alt+R  reset the layout        Ctrl+Alt+Q  close the overlay")
        tk.Label(self, text=keys, bg=CARD, fg=DIM, justify="left", anchor="w", font=("Segoe UI", 9), wraplength=980).pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(self, text="The overlay needs RaceRoom in windowed or borderless mode: no window can be drawn over exclusive fullscreen.",
                 bg=CARD, fg=DIM, justify="left", anchor="w", font=("Segoe UI", 9), wraplength=980).pack(fill="x", padx=18)

    # -- what the overlay has now
    def geom(self) -> dict:
        return clean_geom((self.seen or {}).get("geom")) or dict(DEFAULT_GEOM)

    def scale(self, factor: float) -> None:
        g = self.geom()
        w, h = clamp_size(g["w"] * factor, g["h"] * factor)
        self.send_geom({**g, "w": w, "h": h})

    def put(self, body: dict) -> None:
        threading.Thread(target=lambda: self.panel.api.safe("PUT", "/api/overlay", body), daemon=True).start()
        self.note.set("Sent. The overlay window follows within a second.")
        self.seen.update(body)

    def send_geom(self, geom: dict) -> None:
        try:
            g = clean_geom(geom)
        except Exception:                                       # noqa: BLE001
            g = None
        if g is None:
            self.note.set("That is not a layout I can use.")
            return
        self.put({"geom": g})
        self._fill(g)

    def _fill(self, g: dict) -> None:
        for k in ("w", "h", "dx", "dy"):
            self.vars[k].set(str(g[k]))
        self.vars["anchor"].set(g["anchor"])

    def refresh(self, ov: Optional[dict]) -> None:
        """Called every second with StintRadio's overlay state; a field is only rewritten when that state changed."""
        if ov is None:
            return
        if ov.get("geom") != self.seen.get("geom"):
            self._fill(clean_geom(ov.get("geom")) or DEFAULT_GEOM)
        if ov.get("theme") != self.seen.get("theme") and ov.get("theme") in ("dark", "light"):
            self.vars["theme"].set(ov["theme"])
        if ov.get("brand") != self.seen.get("brand") and isinstance(ov.get("brand"), bool):
            self.vars["brand"].set(ov["brand"])
        if ov.get("border") != self.seen.get("border") and isinstance(ov.get("border"), bool):
            self.vars["border"].set(ov["border"])
        if ov.get("opacity") != self.seen.get("opacity"):
            self.vars["opacity"].set(float(ov["opacity"]) if isinstance(ov.get("opacity"), (int, float)) else 1.0)
        self.seen = {k: ov.get(k) for k in ("geom", "theme", "brand", "border", "opacity")}


class ToolsTab(ttk.Frame):
    def __init__(self, book: ttk.Notebook, panel) -> None:
        super().__init__(book, style="Card.TFrame")
        self.panel = panel
        self.kdev = REPO / "dev-plugin" / "scripts" / "kdev.py"
        bar = ttk.Frame(self, style="Card.TFrame")
        bar.pack(fill="x", padx=14, pady=(14, 4))
        for text, cmd in (("Driving suggestions", self.tips), ("Session briefing", self.briefing), ("Force-feedback advice", self.ffb),
                          ("Import Coach Dave laps", self.coach_dave), ("Check my setup", self.check_setup), ("Supported sims", self.sims)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 8))
        bar2 = ttk.Frame(self, style="Card.TFrame")
        bar2.pack(fill="x", padx=14, pady=4)
        ttk.Button(bar2, text="Which program makes a sound? (2 min)", command=self.audio_check).pack(side="left", padx=(0, 8))
        self.b_plan = ttk.Button(bar2, text="Improvement plan", command=self.plan)
        self.b_plan.pack(side="left", padx=(0, 8))
        self.b_round = ttk.Button(bar2, text="Run one improvement round…", command=self.improve)
        self.b_round.pack(side="left", padx=(0, 8))
        if not self.kdev.exists():                             # a packaged install has no developer tools
            for b in (self.b_plan, self.b_round):
                b.state(["disabled"])
        tk.Label(self, text="Suggestions come from your recorded laps (brake bias, shift points, minimum speeds). The improvement round works on a copy of the "
                            "code on its own branch and never touches the running app; nothing is merged without you.",
                 bg=CARD, fg=DIM, justify="left", anchor="w", font=("Segoe UI", 9), wraplength=940).pack(fill="x", padx=16, pady=(4, 6))
        self.text = tk.Text(self, bg="#0d111a", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 10), wrap="word")
        self.text.pack(fill="both", expand=True, padx=14, pady=(2, 12))
        self.show("Pick a tool above. Its answer appears here.")

    def show(self, body: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("end", body)

    def later(self, fn: Callable[[], None]) -> None:
        try:
            self.after(0, fn)
        except tk.TclError:
            pass

    def run(self, working: str, job: Callable[[], str]) -> None:
        """A tool in the background: say what is going on, show the answer when it is there."""
        self.show(working)
        def go() -> None:
            try:
                body = job()
            except Exception as e:                              # noqa: BLE001
                body = f"That did not work: {type(e).__name__}: {e}"
            self.later(lambda: self.show(body))
        threading.Thread(target=go, daemon=True).start()

    # -- the tools
    def tips(self) -> None:
        def job() -> str:
            d = self.panel.api.safe("GET", "/api/tips", timeout=60)
            if d is None:
                return "StintRadio is not running, or there is no recorded session yet. Start it from the Control tab and drive a few laps."
            lines = d.get("lines") if isinstance(d, dict) else None
            return "\n".join(f"• {l}" for l in lines) if lines else json.dumps(d, indent=2)[:6000]
        self.run("Reading your recorded laps…", job)

    def briefing(self) -> None:
        def job() -> str:
            d = self.panel.api.safe("GET", "/api/briefing?kind=race", timeout=30)
            if d is None:
                return "StintRadio is not running."
            if not d.get("text"):
                return d.get("error") or "Nothing yet: drive a lap or two here first."
            ptr = "\n".join(f"  • {p['name']}: {p['habit'] or 'time lost'}, {p['loss']:.2f} s a lap over {p['laps']} laps" for p in d.get("pointers", []))
            return (f"What the engineer says before a race at {d['track']} in {d['car']}:\n\n{d['text']}\n\n"
                    f"It is made from your earlier runs:\n{ptr or '  (not enough laps yet)'}")
        self.run("Working out the briefing…", job)

    def sims(self) -> None:
        def job() -> str:
            from . import sims
            rows = sims.listing()
            out = ["Sims KerbApex can read (the one marked RUNNING is found now):", ""]
            for r in rows:
                out.append(f"  {'RUNNING' if r['running'] else '       '}  {r['name']}  [{r['id']}]")
                out.append(f"           provides: {', '.join(r['capabilities'])}")
                if r["missing"]:
                    out.append("           not available: " + "; ".join(r["missing"].values()))
            from .sims import registry
            out += ["", f"Add a sim: drop an adapter .py file in {registry.adapters_dir()} (see docs/ADAPTERS.md)."]
            out += [f"Problem: {e}" for e in registry.errors]
            return "\n".join(out)
        self.run("Listing the sims…", job)

    def ffb(self) -> None:
        def job() -> str:
            d = self.panel.api.safe("GET", "/api/ffb", timeout=30)
            return "StintRadio is not running." if d is None else json.dumps(d, indent=2)[:6000]
        self.run("Checking the force feedback…", job)

    def coach_dave(self) -> None:
        def job() -> str:
            d = self.panel.api.safe("POST", "/api/import-coach-dave", {}, timeout=300)
            return "StintRadio is not running." if d is None else "Import finished:\n" + json.dumps(d, indent=2)[:6000]
        self.run("Looking for Coach Dave Delta session files and storing your fastest laps as references…", job)

    def check_setup(self) -> None:
        def job() -> str:
            st = self.panel.status
            try:
                from driverdirector.compose import ffmpeg_bin
                ff = ffmpeg_bin()
                ffmpeg = ff if (shutil.which(ff) or Path(ff).is_file()) else None
            except Exception:                                   # noqa: BLE001
                ffmpeg = shutil.which("ffmpeg")
            try:
                from driverdirector.commentary import find_claude_cli
                claude = find_claude_cli("")
            except Exception:                                   # noqa: BLE001
                claude = shutil.which("claude")
            try:
                settings = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                settings = None
            voice = (settings or {}).get("voice", {}).get("elevenlabs_voice_id", "")
            cache = None
            if voice:
                cache = any((p / "brake.wav").is_file() for p in (data_dir() / "live_voice").glob(f"eleven_{voice}_boost*"))
            try:
                tl = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
            except (OSError, subprocess.SubprocessError):
                tl = ""
            rows = setup_checks(bool(st.get("app")), st.get("games", {}), settings, dict(os.environ), ffmpeg, claude, cache, remote_sessions(tl))
            return "\n".join(f"{'✔' if ok else '✘'}  {what}" + ("" if ok else f"\n      → {fix}") for ok, what, fix in rows)
        self.run("Checking the setup…", job)

    def audio_check(self) -> None:
        script = REPO / "dev-plugin" / "scripts" / "audiomon.py"
        def job() -> str:
            if not script.exists():
                return "The sound check script is not part of this install."
            log = data_dir() / "audiomon.log"
            log.unlink(missing_ok=True)
            r = subprocess.run([sys.executable, str(script), "120", str(log)], capture_output=True, text=True, timeout=300, creationflags=NO_WINDOW)
            if r.returncode != 0 and not log.exists():
                tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
                return f"The sound check needs two small packages: pip install pycaw psutil\n({tail[0]})"
            lines = summarise_audiomon(log.read_text(encoding="utf-8", errors="replace"))
            return ("Programs that made sound in the last 2 minutes:\n" + "\n".join("  " + l for l in lines)) if lines else "Nothing made a sound while it listened."
        self.run("Listening to which programs make a sound for 2 minutes. Play the game and let the engineer talk…", job)

    def plan(self) -> None:
        def job() -> str:
            r = subprocess.run([sys.executable, str(self.kdev), "loop", "--rounds", "1", "--app", "stintradio"], capture_output=True, text=True,
                               timeout=60, creationflags=NO_WINDOW, cwd=str(REPO))
            return (r.stdout or r.stderr).strip()[:4000]
        self.run("Asking the improvement tool for its plan…", job)

    def improve(self) -> None:
        if not messagebox.askyesno("KerbApex", "Start one improvement round?\n\nA headless Claude session works on a copy of the code, on its own branch, "
                                   "and runs the tests. It uses your Claude usage, can take up to an hour, and never touches the running app. "
                                   "Nothing is merged unless you do it.", parent=self):
            return
        log = data_dir() / "improver.log"
        out = open(log, "w", encoding="utf-8")
        subprocess.Popen([sys.executable, str(self.kdev), "loop", "--rounds", "1", "--go", "--app", "stintradio"], cwd=str(REPO), stdout=out,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self.show(f"An improvement round is running in the background. Its progress is written to:\n{log}\n"
                  f"When it finishes, its report is in %LOCALAPPDATA%\\KerbApex\\rounds\\<round>\\ROUND_REPORT.md and the branch is improve/<round>.")
