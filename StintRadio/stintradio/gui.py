"""KerbApex control panel: start and stop StintRadio, its coach and the overlay, change the settings, read the logs.

  python -m stintradio gui          (or double-click "Start KerbApex.bat")

A window of its own (tkinter). It talks to StintRadio over its local REST API, so it can be opened and closed freely
without touching a running race; when StintRadio is not running it edits the settings file directly.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

from . import __version__
from .config import Settings, data_dir
from . import housekeeping as H
from .gui_tabs import OverlayTab, ToolsTab, load_prefs, save_prefs
from .studio_tab import StudioTab

BG, CARD, EDGE, TEXT, DIM = "#0b0e14", "#131824", "#242b3b", "#f3f6fa", "#8794a6"
ACCENT, KERB, GOOD, BAD, WARN = "#7c6cff", "#e10600", "#34e089", "#ff5d6c", "#ffc83d"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DETACHED = (0x00000008 | 0x00000200) if sys.platform == "win32" else 0        # DETACHED_PROCESS | NEW_PROCESS_GROUP
GAME_EXES = {"RaceRoom": "RRRE64.exe", "Le Mans Ultimate": "Le Mans Ultimate.exe"}

# What the Settings tab edits: (section, key, label, kind, choices). Everything else stays in settings.json untouched.
FIELDS = [
    ("live", "game", "Game", "choice", ["auto", "raceroom", "lmu"]),
    ("live", "driver_name", "Your name for the engineer", "text", None),
    ("live", "voice_engine", "Voice", "choice", ["elevenlabs", "windows"]),
    ("live", "voice_boost_db", "Voice loudness (dB)", "number", None),
    ("live", "style", "Style", "choice", ["engineer", "countdown"]),
    ("live", "talk", "How much it says", "choice", ["quiet", "normal", "chatty"]),
    ("live", "countdown", "Braking countdown", "choice", ["focus", "all", "off"]),
    ("live", "coach_laps", "Full coaching laps", "number", None),
    ("live", "briefing", "Briefing before a session", "check", None),
    ("live", "corner_guide", "Corner-guide laps", "number", None),
    ("live", "reference", "Race reference", "choice", ["leader", "best"]),
    ("live", "radio_effect", "Team-radio sound", "check", None),
    ("live", "ptt", "Push-to-talk questions", "check", None),
    ("live", "ptt_button", "Push-to-talk button", "text", None),
    ("live", "lan", "Laptop race page (LAN)", "check", None),
    ("live", "record", "Record session data", "check", None),
    ("live", "ffb_advice", "Force-feedback advice", "check", None),
]


# ---- pure helpers (tested) -------------------------------------------------------------------------------------------

def windowless(exe: str) -> str:
    """pythonw.exe beside python.exe: the same Python with no console, so what the panel starts never opens a terminal window."""
    if exe.lower().endswith("python.exe"):
        w = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(w):
            return w
    return exe


def sibling(exe: str, name: str = "StintRadio.exe") -> str:
    """A program next to this one (in a packaged install the panel, KerbApex.exe, starts StintRadio.exe beside it)."""
    return os.path.join(os.path.dirname(exe), name)


def app_command(frozen: bool = getattr(sys, "frozen", False), exe: str = sys.executable) -> list:
    """The command that starts the StintRadio server (the installed program, or this Python running the package)."""
    return [sibling(exe)] if frozen else [windowless(exe), "-m", "stintradio"]


def overlay_command(game: str = "raceroom", frozen: bool = getattr(sys, "frozen", False), exe: str = sys.executable, engine: str = "edge",
                    port: int = 6420) -> list:
    cmd = [sibling(exe), "overlay", "--game", game] if frozen else [windowless(exe), "-m", "stintradio", "overlay", "--game", game]
    if port != 6420:                                 # the overlay talks to this StintRadio, wherever it listens
        cmd += ["--url", f"http://127.0.0.1:{port}/overlay"]
    return cmd + ["--engine", engine] if engine != "edge" else cmd


def coerce(kind: str, text, old):
    """A settings value from what the form holds: numbers keep their type, checks are bools, text is stripped."""
    if kind == "check":
        return bool(text)
    if kind == "number":
        value = float(str(text).strip())
        return int(value) if isinstance(old, int) and not isinstance(old, bool) else value
    return str(text).strip()


def merge_form(settings: dict, form: dict) -> dict:
    """The settings with the form's values laid in; sections and keys the form does not edit are left as they were."""
    out = json.loads(json.dumps(settings))
    for (section, key, _label, kind, _choices) in FIELDS:
        if (section, key) in form:
            out.setdefault(section, {})[key] = coerce(kind, form[(section, key)], out.get(section, {}).get(key))
    return out


def lan_address(port: int = 6420) -> str:
    """This PC's address on the home network, for the laptop's browser."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return f"http://{s.getsockname()[0]}:{port}/race"
    except OSError:
        return f"http://<this PC's address>:{port}/race"


def tail(path: Path, lines: int = 250) -> str:
    try:
        return "".join(path.read_text(encoding="utf-8", errors="replace").splitlines(True)[-lines:]) or "(empty)"
    except OSError:
        return "(no file yet)"


# ---- talking to StintRadio -------------------------------------------------------------------------------------------

class Api:
    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"

    def call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 2.5):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, method=method, data=data,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def safe(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 2.5):
        try:
            return self.call(method, path, body, timeout)
        except (OSError, ValueError):
            return None


def kill_app_process(port: int = 6420) -> None:
    """End the StintRadio that listens on `port` (and no other: a panel for a test copy must never end yours)."""
    pid = H.listener_pid(port)
    if pid:
        H.kill([pid])


def game_running(exe: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/NH"], capture_output=True, text=True, timeout=6,
                             creationflags=NO_WINDOW).stdout
        return exe.lower() in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


# ---- the window --------------------------------------------------------------------------------------------------------

class Panel(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("KerbApex")
        self.geometry("1100x900")
        self.minsize(940, 820)
        self.configure(bg=BG)
        self.settings = Settings.load()
        self.prefs = load_prefs()
        self.engine = tk.StringVar(value=self.prefs.get("engine", "native"))
        self.api = Api(self.settings.port)
        self.status: dict = {}
        self.stop_poll = False
        self.note = tk.StringVar(value="")
        self._style()
        self._header()
        book = self.book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        self.control, self.setting_tab, self.log_tab = ttk.Frame(book, style="Card.TFrame"), ttk.Frame(book, style="Card.TFrame"), ttk.Frame(book, style="Card.TFrame")
        book.add(self.control, text="  Control  ")
        book.add(self.setting_tab, text="  Settings  ")
        book.add(self.log_tab, text="  Logs  ")
        self._control_tab()
        self._settings_tab()
        self._logs_tab()
        self.overlay_tab, self.tools_tab = OverlayTab(book, self), ToolsTab(book, self)
        book.insert(1, self.overlay_tab, text="  Overlay  ")
        book.insert(3, self.tools_tab, text="  Tools  ")
        self.studio_tab = StudioTab(book, self)
        book.insert(4, self.studio_tab, text="  Studio  ")
        tk.Label(self, textvariable=self.note, bg=BG, fg=DIM, anchor="w", font=("Segoe UI", 9)).pack(fill="x", padx=16, pady=(0, 8))
        threading.Thread(target=self._poll, daemon=True).start()
        self.after(400, self._refresh)
        self.after(2000, self._refresh_logs)
        self.after(100, self._front)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def save_engine(self) -> None:
        self.prefs["engine"] = self.engine.get()
        save_prefs(self.prefs)
        self.say("Overlay window: " + ("see-through (native)" if self.engine.get() == "native" else "browser window") + ". It applies when the overlay is started.")

    def _front(self) -> None:
        """Come up in front, never minimized (a launcher can start the window hidden)."""
        try:
            self.state("normal")
            self.lift()
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

    # -- look
    def _style(self) -> None:
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=TEXT, fieldbackground=CARD, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE, font=("Segoe UI", 10))
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=BG, foreground=DIM, padding=(14, 7), borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)])
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=CARD, foreground=TEXT)
        st.configure("Dim.TLabel", background=CARD, foreground=DIM)
        st.configure("TCheckbutton", background=CARD, foreground=TEXT, indicatorbackground="#1a2030", indicatorforeground=GOOD,
                     indicatormargin=(0, 0, 8, 0))
        st.map("TCheckbutton", background=[("active", CARD)], indicatorbackground=[("selected", "#1a2030"), ("active", "#2a3350")])
        st.configure("TRadiobutton", background=CARD, foreground=TEXT, indicatorbackground="#1a2030", indicatorforeground=GOOD, indicatormargin=(0, 0, 8, 0))
        st.map("TRadiobutton", background=[("active", CARD)], indicatorbackground=[("selected", "#1a2030"), ("active", "#2a3350")])
        st.configure("Horizontal.TScale", background=CARD, troughcolor="#1a2030", bordercolor=EDGE, lightcolor=ACCENT, darkcolor=ACCENT)
        st.configure("TButton", background="#1d2433", foreground=TEXT, padding=(12, 7), borderwidth=0, focusthickness=0)
        st.map("TButton", background=[("active", "#2a3350"), ("disabled", "#151a26")], foreground=[("disabled", "#566")])
        st.configure("Go.TButton", background=ACCENT, foreground="#fff")
        st.map("Go.TButton", background=[("active", "#9486ff"), ("disabled", "#2a2850")])
        st.configure("Stop.TButton", background="#3a1a1f", foreground="#ffb3bb")
        st.map("Stop.TButton", background=[("active", "#55232b"), ("disabled", "#1f1417")])
        st.configure("TCombobox", fieldbackground="#1a2030", background="#1a2030", foreground=TEXT, arrowcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", "#1a2030")], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", "#1a2030")], selectforeground=[("readonly", TEXT)], background=[("active", "#2a3350")])
        st.configure("TEntry", fieldbackground="#1a2030", foreground=TEXT, insertcolor=TEXT)
        st.configure("TSpinbox", fieldbackground="#1a2030", foreground=TEXT, arrowcolor=TEXT)
        self.option_add("*TCombobox*Listbox.background", "#1a2030")
        self.option_add("*TCombobox*Listbox.foreground", TEXT)

    def _header(self) -> None:
        stripes = tk.Canvas(self, height=9, bg=BG, highlightthickness=0)
        stripes.pack(fill="x")
        stripes.bind("<Configure>", lambda e: self._kerb(stripes, e.width))
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16, pady=(10, 4))
        tk.Label(bar, text="KERB", bg=BG, fg=KERB, font=("Segoe UI", 24, "bold italic")).pack(side="left")
        tk.Label(bar, text="APEX", bg=BG, fg=TEXT, font=("Segoe UI", 24, "bold italic")).pack(side="left")
        tk.Label(bar, text="  StintRadio control panel", bg=BG, fg=DIM, font=("Segoe UI", 11)).pack(side="left", pady=(10, 0))
        tk.Label(bar, text=f"v{__version__}", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="right", pady=(12, 0))

    @staticmethod
    def _kerb(c: tk.Canvas, width: int) -> None:
        c.delete("all")
        step = 14
        for i, x in enumerate(range(-step, width + step, step)):
            c.create_polygon(x, 9, x + step // 2 + 4, 0, x + step + 4, 0, x + step // 2, 9, fill=KERB if i % 2 == 0 else "#f4f4f4", outline="")

    # -- control tab
    def _control_tab(self) -> None:
        f = self.control
        f.columnconfigure(1, weight=1)
        self.dots: dict = {}
        rows = [("app", "StintRadio"), ("coach", "Race engineer"), ("game", "Game"), ("overlay", "Overlay"), ("voice", "Voice")]
        for r, (key, label) in enumerate(rows):
            ttk.Label(f, text=label, style="Dim.TLabel").grid(row=r, column=0, sticky="w", padx=(18, 10), pady=(14 if r == 0 else 4, 4))
            dot = tk.Label(f, text="●  …", bg=CARD, fg=DIM, anchor="w", font=("Segoe UI", 10, "bold"))
            dot.grid(row=r, column=1, sticky="w", pady=(14 if r == 0 else 4, 4))
            self.dots[key] = dot
        ttk.Separator(f).grid(row=len(rows), column=0, columnspan=3, sticky="ew", padx=14, pady=10)

        def buttons(row: int, title: str, items: list) -> None:
            ttk.Label(f, text=title, style="Dim.TLabel").grid(row=row, column=0, sticky="w", padx=(18, 10), pady=6)
            box = ttk.Frame(f, style="Card.TFrame")
            box.grid(row=row, column=1, sticky="w")
            for text, cmd, style, attr in items:
                b = ttk.Button(box, text=text, command=cmd, style=style)
                b.pack(side="left", padx=(0, 8))
                setattr(self, attr, b)

        r = len(rows) + 1
        buttons(r, "Everything", [("Start everything", self.start_all, "Go.TButton", "b_all"), ("Stop everything", self.stop_all, "Stop.TButton", "b_none")])
        buttons(r + 1, "StintRadio + engineer", [("Start", self.start_app, "TButton", "b_app_on"), ("Stop", self.stop_app, "Stop.TButton", "b_app_off")])
        buttons(r + 2, "Overlay", [("Start", self.start_overlay, "TButton", "b_ov_on"), ("Stop", self.stop_overlay, "Stop.TButton", "b_ov_off"),
                                   ("Move / resize", self.toggle_edit, "TButton", "b_ov_edit")])
        buttons(r + 3, "Housekeeping", [("Clear stray overlays", self.clear_strays, "TButton", "b_clear"), ("Reset everything", self.reset_everything, "Stop.TButton", "b_reset")])
        ttk.Separator(f).grid(row=r + 4, column=0, columnspan=3, sticky="ew", padx=14, pady=10)
        buttons(r + 5, "Dashboards", [("Main dashboard", lambda: webbrowser.open(self.api.base + "/"), "TButton", "b_d1"),
                                      ("Race page", lambda: webbrowser.open(self.api.base + "/race"), "TButton", "b_d2"),
                                      ("Copy laptop address", self.copy_lan, "TButton", "b_d3")])
        self.lan_text = tk.StringVar(value="")
        tk.Label(f, textvariable=self.lan_text, bg=CARD, fg=DIM, font=("Segoe UI", 9), anchor="w").grid(row=r + 6, column=1, sticky="w")
        hint = ("Overlay keys (work while the game has focus):  Ctrl+Alt+O move/resize   Ctrl+Alt+S shape   Ctrl+Alt+= / -  size\n"
                "Ctrl+Alt+B border   Ctrl+Alt+M KerbApex mark   Ctrl+Alt+T theme   Ctrl+Alt+R reset   Ctrl+Alt+Q close.   The overlay needs RaceRoom in windowed or borderless mode.")
        tk.Label(f, text=hint, bg=CARD, fg=DIM, justify="left", anchor="w", font=("Segoe UI", 9), wraplength=880).grid(row=r + 7, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 8))

    # -- settings tab
    def _settings_tab(self) -> None:
        f = self.setting_tab
        f.columnconfigure(1, weight=1)
        self.vars: dict = {}
        self.fields_box = ttk.Frame(f, style="Card.TFrame")
        self.fields_box.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=18, pady=14)
        half = (len(FIELDS) + 1) // 2                                  # two columns of settings
        self.fields_box.columnconfigure(2, minsize=36)
        for n, (section, key, label, kind, choices) in enumerate(FIELDS):
            r, c = n % half, (n // half) * 3
            ttk.Label(self.fields_box, text=label).grid(row=r, column=c, sticky="w", pady=5, padx=(0, 12))
            var = tk.BooleanVar() if kind == "check" else tk.StringVar()
            self.vars[(section, key)] = var
            if kind == "check":
                w = ttk.Checkbutton(self.fields_box, variable=var)
            elif kind == "choice":
                w = ttk.Combobox(self.fields_box, textvariable=var, values=choices, state="readonly", width=14)
            else:
                w = ttk.Entry(self.fields_box, textvariable=var, width=16)
            w.grid(row=r, column=c + 1, sticky="w", pady=5)
        bar = ttk.Frame(f, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 6))
        ttk.Button(bar, text="Save", command=lambda: self.save(False), style="Go.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Save and restart coach", command=lambda: self.save(True)).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Revert", command=self.load_form).pack(side="left", padx=(0, 8))
        tk.Label(f, text="Voice keys come from the ELEVENLABS_API_KEY environment variable, never from here. "
                         "Changes apply when the coach starts.", bg=CARD, fg=DIM, font=("Segoe UI", 9), anchor="w", wraplength=700, justify="left"
                 ).grid(row=2, column=0, columnspan=2, sticky="w", padx=18, pady=(4, 0))
        self.load_form()

    def current_settings(self) -> dict:
        got = self.api.safe("GET", "/api/settings")
        if got:
            return got
        self.settings = Settings.load()
        return self.settings.model_dump()

    def load_form(self) -> None:
        cur = self.current_settings()
        for section, key, _label, kind, _choices in FIELDS:
            val = cur.get(section, {}).get(key)
            self.vars[(section, key)].set(bool(val) if kind == "check" else ("" if val is None else str(val)))
        self.say("Settings loaded.")

    def save(self, restart: bool) -> None:
        try:
            form = {k: v.get() for k, v in self.vars.items()}
            merged = merge_form(self.current_settings(), form)
            Settings.model_validate(merged)
        except (ValueError, TypeError) as e:
            messagebox.showerror("KerbApex", f"A setting is not valid: {e}")
            return
        if self.api.safe("PUT", "/api/settings", merged) is None:        # StintRadio is not running: write the file
            new = Settings.model_validate(merged)
            self.settings.live, self.settings.voice = new.live, new.voice
            self.settings.save()
        self.say("Settings saved." + (" Restarting the coach…" if restart else " They apply when the coach starts."))
        if restart:
            threading.Thread(target=lambda: (self.api.safe("POST", "/api/stop"), time.sleep(1.0), self.api.safe("POST", "/api/start", {})), daemon=True).start()

    # -- logs tab
    def _logs_tab(self) -> None:
        f = self.log_tab
        top = ttk.Frame(f, style="Card.TFrame")
        top.pack(fill="x", padx=14, pady=(12, 4))
        self.log_choice = tk.StringVar(value="stintradio.log")
        box = ttk.Combobox(top, textvariable=self.log_choice, values=["stintradio.log", "audio.log"], state="readonly", width=20)
        box.pack(side="left")
        ttk.Button(top, text="Refresh", command=self._refresh_logs).pack(side="left", padx=8)
        ttk.Button(top, text="Open data folder", command=lambda: os.startfile(str(data_dir())) if sys.platform == "win32" else None).pack(side="left")
        self.text = tk.Text(f, bg="#0d111a", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 9), wrap="none")
        self.text.pack(fill="both", expand=True, padx=14, pady=(4, 12))

    def _refresh_logs(self) -> None:
        body = tail(data_dir() / self.log_choice.get())
        if body != getattr(self, "_log_body", None):
            self._log_body = body
            self.text.delete("1.0", "end")
            self.text.insert("end", body)
            self.text.see("end")
        self.after(2500, self._refresh_logs)

    # -- status polling (a thread: a slow answer must never freeze the window)
    def _poll(self) -> None:
        last_game, last_procs = 0.0, 0.0
        games: dict = {}
        counts: dict = {}
        while not self.stop_poll:
            st: dict = {}
            health = self.api.safe("GET", "/api/health", timeout=1.2)
            st["app"] = health is not None
            if health is not None:
                state = self.api.safe("GET", "/api/state", timeout=1.5) or {}
                st["coach"] = state.get("status", "?")
                st["error"] = state.get("error", "")
                st["ref"] = state.get("ref") or {}
                st["voice"] = health.get("voice_engine", "")
                ov = self.api.safe("GET", "/api/overlay", timeout=1.2) or {}
                st["overlay"], st["edit"] = bool(ov.get("alive")), bool(ov.get("edit"))
                st["ov"] = ov
            if time.monotonic() - last_game > 4:
                games = {name: game_running(exe) for name, exe in GAME_EXES.items()}
                last_game = time.monotonic()
            st["games"] = games
            if time.monotonic() - last_procs > 5:                 # how many of each program are really running (strays show up here)
                procs = H.processes()
                port = self.settings.port
                counts = {"overlays": len(H.roots(procs, "overlay", port)) + len(H.roots(procs, "browser", port)), "servers": len(H.roots(procs, "server")),
                          "panels": len(H.roots(procs, "panel"))}
                last_procs = time.monotonic()
            st["counts"] = counts
            self.status = st
            time.sleep(1.0)

    def _refresh(self) -> None:
        st = self.status
        up = st.get("app", False)

        def show(key: str, text: str, colour: str) -> None:
            self.dots[key].config(text="●  " + text, fg=colour)

        n = st.get("counts") or {}
        extra_servers = f"  ·  {n['servers']} copies running: press Reset everything" if n.get("servers", 0) > 1 else ""
        show("app", ("running" + extra_servers) if up else "stopped", (WARN if extra_servers else GOOD) if up else DIM)
        coach = st.get("coach", "") if up else ""
        coach_colour = GOOD if coach in ("driving", "rolling") else WARN if coach in ("waiting", "starting") else DIM
        ref = st.get("ref") or {}
        extra = f"   ·   reference {ref['time']:.3f} s ({ref.get('source', '')})" if ref.get("time") else ""
        show("coach", (coach or "off") + extra if up else "off", coach_colour if up else DIM)
        games = st.get("games", {})
        on = [n for n, v in games.items() if v]
        show("game", ", ".join(on) + " is running" if on else "no game running", GOOD if on else DIM)
        if up:
            stray = f"  ·  {n['overlays']} copies running: press Clear stray overlays" if n.get("overlays", 0) > 1 else ""
            show("overlay", ("running" + (" · editing layout" if st.get("edit") else "") + stray) if st.get("overlay") else
                 ("stopped" if not n.get("overlays") else f"{n['overlays']} running without reporting in: press Clear stray overlays"),
                 (WARN if stray else GOOD) if st.get("overlay") else (WARN if n.get("overlays") else DIM))
            show("voice", st.get("voice") or "?", TEXT)
        else:
            show("overlay", "stopped", DIM)
            show("voice", self.settings.live.voice_engine, DIM)
        self.overlay_tab.refresh(st.get("ov"))
        self.overlay_tab.edit_button.config(text="Lock the layout" if st.get("edit") else "Move / resize with the mouse")
        self._enable(self.b_app_on, not up)
        self._enable(self.b_app_off, up)
        self._enable(self.b_ov_on, up and not st.get("overlay"))
        self._enable(self.b_ov_off, bool(st.get("overlay")))
        self._enable(self.b_ov_edit, bool(st.get("overlay")))
        self.b_ov_edit.config(text="Lock layout" if st.get("edit") else "Move / resize")
        self._enable(self.b_d1, up)
        self._enable(self.b_d2, up)
        lan = bool(self.settings.live.lan)
        self.lan_text.set(lan_address(self.settings.port) if lan else "Turn on \"Laptop race page\" in Settings to use the race page on another device.")
        if st.get("error"):
            self.say(st["error"])
        self.after(700, self._refresh)

    @staticmethod
    def _enable(button: ttk.Button, on: bool) -> None:
        held = getattr(button, "hold_until", 0.0) > time.monotonic()        # a button just pressed stays off while its action starts
        button.state(["!disabled"] if on and not held else ["disabled"])

    @staticmethod
    def _hold(*buttons, seconds: float = 8.0) -> None:
        for b in buttons:
            b.hold_until = time.monotonic() + seconds
            b.state(["disabled"])

    def say(self, text: str) -> None:
        self.note.set(text)

    # -- actions
    def _spawn(self, cmd: list) -> None:
        home = Path(__file__).resolve().parent.parent
        subprocess.Popen(cmd, cwd=str(home), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=DETACHED | NO_WINDOW)

    def start_app(self, then=None) -> None:
        if self.status.get("app"):
            self.start_coach()                          # already up: make sure the engineer is on, too
            return then() if then else None
        self._hold(self.b_app_on, self.b_all, seconds=15.0)
        self.say("Starting StintRadio…")
        self._spawn(app_command())

        def wait() -> None:
            for _ in range(60):
                time.sleep(0.5)
                if self.api.safe("GET", "/api/health", timeout=1.0) is not None:
                    self.say("StintRadio is running.")
                    self.start_coach()                  # the engineer is part of StintRadio: it waits for the game by itself
                    if then:
                        then()
                    return
            self.say("StintRadio did not come up; see the Logs tab.")
        threading.Thread(target=wait, daemon=True).start()

    def stop_app(self) -> None:
        self.say("Stopping StintRadio…")

        def go() -> None:
            self.api.safe("PUT", "/api/overlay", {"quit": True})
            if self.api.safe("POST", "/api/quit", {}) is None:             # a StintRadio from before the quit route: end its process
                self.api.safe("POST", "/api/stop")
                kill_app_process(self.settings.port)
        threading.Thread(target=go, daemon=True).start()

    def start_coach(self) -> None:

        def go() -> None:
            r = self.api.safe("POST", "/api/start", {"wait": True}, timeout=20)
            if r is None:
                self.say("The coach could not start; see the Logs tab.")
            elif r.get("running"):
                self.say("The coach is already running.")
            else:
                self.say("Coach on: it waits for the game, and starts talking as soon as you are on track.")
        threading.Thread(target=go, daemon=True).start()

    def stop_coach(self) -> None:
        threading.Thread(target=lambda: (self.api.safe("POST", "/api/stop"), self.say("Coach stopped.")), daemon=True).start()

    def start_overlay(self) -> None:
        if (self.api.safe("GET", "/api/overlay", timeout=1.0) or {}).get("alive"):          # ask the overlay itself, not the last reading
            self.say("The overlay is already running.")
            return
        self._hold(self.b_ov_on, self.b_all)
        game = {"Le Mans Ultimate": "lmu"}.get(next((n for n, v in self.status.get("games", {}).items() if v), ""), "raceroom")
        self._spawn(overlay_command(game, engine=self.engine.get(), port=self.settings.port))
        self.say("Overlay started: it appears over the game window (windowed or borderless mode).")

    def stop_overlay(self) -> None:
        threading.Thread(target=lambda: self.api.safe("PUT", "/api/overlay", {"quit": True}), daemon=True).start()
        self.say("Closing the overlay…")

    def toggle_edit(self) -> None:
        editing = bool(self.status.get("edit"))
        threading.Thread(target=lambda: self.api.safe("PUT", "/api/overlay", {"edit": not editing}), daemon=True).start()
        self.say("Drag the overlay to move it, its bottom-right corner to resize it, then press Lock layout." if not editing else "Layout locked.")

    def clear_strays(self) -> None:
        """Close every overlay, however many were started; then Start gives exactly one."""
        self._hold(self.b_clear, seconds=6.0)
        self.say("Closing every overlay…")

        def go() -> None:
            forced = H.clear_overlays(lambda: self.api.safe("PUT", "/api/overlay", {"quit": True}),
                                      lambda: self.api.safe("PUT", "/api/overlay", {"quit": False}), port=self.settings.port)
            self.status["overlay"] = False
            self.say("Every overlay is closed" + (f" ({forced} had to be ended)" if forced else "") + ". Press Start under Overlay for a single new one.")
        threading.Thread(target=go, daemon=True).start()

    def reset_everything(self) -> None:
        """Close every overlay, end every StintRadio (also ones left over from earlier runs), then start one of each fresh."""
        if not messagebox.askyesno("KerbApex", "Reset everything?\n\nEvery overlay and StintRadio is closed and a single new StintRadio, engineer and overlay "
                                   "are started. Do not do this in the middle of a race.", parent=self):
            return
        self._hold(self.b_reset, self.b_all, seconds=40.0)
        self.say("Resetting: closing everything…")

        def go() -> None:
            H.clear_overlays(lambda: self.api.safe("PUT", "/api/overlay", {"quit": True}), lambda: None, port=self.settings.port)
            self.api.safe("POST", "/api/quit", {})
            time.sleep(2.0)
            H.clear_servers(self.settings.port)
            time.sleep(1.0)
            self.status["app"] = False
            self.status["overlay"] = False
            self.after(0, self.start_all)                    # one fresh StintRadio with its engineer, then one overlay
        threading.Thread(target=go, daemon=True).start()

    def start_all(self) -> None:
        self._hold(self.b_all, seconds=10.0)
        self.start_app(then=lambda: (time.sleep(1.5), self.start_overlay()))

    def stop_all(self) -> None:
        self.stop_app()

    def copy_lan(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(lan_address(self.settings.port))
        self.say("Address copied: open it in the laptop's browser.")

    def _close(self) -> None:
        self.stop_poll = True
        self.destroy()


def main(argv=None) -> int:
    tab = int(argv[argv.index("--tab") + 1]) if argv and "--tab" in argv else 0      # 0 Control, 1 Overlay, 2 Settings, 3 Tools, 4 Studio, 5 Logs
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    from .single import acquire, focus_window
    if not acquire(f"KerbApexPanel-{Settings.load().port}"):                  # already open: bring that one forward instead of opening a second
        focus_window("KerbApex")
        return 0
    panel = Panel()
    panel.book.select(tab)
    panel.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
