"""The Studio tab of the KerbApex control panel: turn a RaceRoom replay into a finished video with DriverDirector.

DriverDirector plays the replay in the game, cuts the cameras, records it through OBS and builds the video (title, fades, a box
of other views). It takes over the PC for the length of the replay twice over, so the tab asks first and says so. The work
itself runs in DriverDirector's own command line (`python -m driverdirector raceroom ...`), so this tab only lists the replays,
starts and stops it, shows what it prints and offers the private YouTube upload.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from . import studio
from .studio import available, estimate_minutes, parse_replays, video_from

BG, CARD, EDGE, TEXT, DIM = "#0b0e14", "#131824", "#242b3b", "#f3f6fa", "#8794a6"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0



class StudioTab(ttk.Frame):
    def __init__(self, book: ttk.Notebook, panel) -> None:
        super().__init__(book, style="Card.TFrame")
        self.panel = panel
        self.replays: list = []
        self.proc: Optional[subprocess.Popen] = None
        self.video: Optional[str] = None
        self.made_from: Optional[str] = None
        top = ttk.Frame(self, style="Card.TFrame")
        top.pack(fill="x", padx=14, pady=(14, 4))
        tk.Label(top, text="Make a video of a RaceRoom replay", bg=CARD, fg=TEXT, font=("Segoe UI", 12, "bold")).pack(side="left")
        self.races_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Races only", variable=self.races_only, command=self._fill).pack(side="right")
        self.b_refresh = ttk.Button(top, text="Refresh the list", command=self.refresh)
        self.b_refresh.pack(side="right", padx=(0, 10))
        cols = ("name", "session", "minutes", "place", "car")
        self.table = ttk.Treeview(self, columns=cols, show="headings", height=8, selectmode="browse")
        for c, w, t in (("name", 330, "Replay"), ("session", 90, "Session"), ("minutes", 70, "Minutes"), ("place", 60, "Place"), ("car", 280, "Car")):
            self.table.heading(c, text=t)
            self.table.column(c, width=w, anchor="w" if c in ("name", "car") else "center")
        self.table.pack(fill="x", padx=14, pady=4)
        bar = ttk.Frame(self, style="Card.TFrame")
        bar.pack(fill="x", padx=14, pady=4)
        self.b_make = ttk.Button(bar, text="Make the video", command=self.make)
        self.b_make.pack(side="left", padx=(0, 8))
        self.b_last = ttk.Button(bar, text="Make the last 5 races", command=self.make_last)
        self.b_last.pack(side="left", padx=(0, 8))
        self.coaching = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="KerbApex overlay and engineer", variable=self.coaching).pack(side="left", padx=(0, 12))
        self.b_stop = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.b_stop.pack(side="left", padx=(0, 8))
        self.b_upload = ttk.Button(bar, text="Upload as a private draft", command=self.upload, state="disabled")
        self.b_upload.pack(side="left", padx=(0, 8))
        self.b_folder = ttk.Button(bar, text="Show the video", command=self.show_video, state="disabled")
        self.b_folder.pack(side="left", padx=(0, 8))
        self.state = tk.StringVar(value="")
        tk.Label(self, textvariable=self.state, bg=CARD, fg=DIM, anchor="w", justify="left", wraplength=940, font=("Segoe UI", 9)).pack(fill="x", padx=16, pady=(2, 4))
        self.text = tk.Text(self, bg="#0d111a", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 10), wrap="word", height=10)
        self.text.pack(fill="both", expand=True, padx=14, pady=(2, 12))
        if available():
            self.log("The list of your replays loads when you press Refresh (RaceRoom does not need to be running).")
            self.after(600, self.refresh)
        else:
            for b in (self.b_make, self.b_last, self.b_refresh):
                b.state(["disabled"])
            self.log("Studio (the video maker) is a separate part of KerbApex and is not part of this install.\n\n"
                     "It runs from the source download: see the README, section \"Studio\".")

    # ---- helpers ---------------------------------------------------------------------------------------------------
    def log(self, line: str, clear: bool = False) -> None:
        if clear:
            self.text.delete("1.0", "end")
        self.text.insert("end", line + "\n")
        self.text.see("end")

    def later(self, fn: Callable[[], None]) -> None:
        try:
            self.after(0, fn)
        except tk.TclError:
            pass

    def _selected(self) -> Optional[dict]:
        sel = self.table.selection()
        return next((r for r in self.replays if r["name"] == sel[0]), None) if sel else None

    def _fill(self) -> None:
        self.table.delete(*self.table.get_children())
        for r in self.replays:
            if self.races_only.get() and r["session"] != "Race":
                continue
            self.table.insert("", "end", iid=r["name"], values=(r["name"], r["session"], f"{r['minutes']:.1f}", r["place"], r["car"]))

    # ---- actions ---------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.state.set("Reading the replay list…")

        def job() -> None:
            try:
                r = subprocess.run(studio.command("list"), capture_output=True, text=True, timeout=90, creationflags=NO_WINDOW)
                rows, err = parse_replays(r.stdout), (r.stderr or "").strip().splitlines()[-1:]
            except (OSError, subprocess.SubprocessError) as e:
                rows, err = [], [str(e)]

            def done() -> None:
                self.replays = rows
                self._fill()
                self.state.set(f"{len(rows)} replays." if rows else "No replays found. " + (err[0] if err else ""))
            self.later(done)
        threading.Thread(target=job, daemon=True).start()

    def _busy(self, on: bool) -> None:
        for b in (self.b_make, self.b_last):
            b.state(["disabled"] if on else ["!disabled"])
        self.b_refresh.state(["disabled"] if on else ["!disabled"])
        self.b_stop.state(["!disabled"] if on else ["disabled"])

    def make(self) -> None:
        r = self._selected()
        if r is None:
            self.state.set("Pick a replay in the list first.")
            return
        mins = estimate_minutes(r["minutes"])
        if not messagebox.askyesno(
                "KerbApex Studio",
                f"Make a video of\n{r['name']}\n\nIt takes about {mins} minutes and uses the whole PC: the game is restarted and played by "
                "keyboard and mouse, then played again for the other camera views. Do not use the PC meanwhile, and do not do this while "
                "you are racing. The live engineer is stopped for the duration.\n\nStart?", parent=self.winfo_toplevel()):
            return
        self.video = None
        self.b_upload.state(["disabled"])
        self.b_folder.state(["disabled"])
        self.made_from = r["name"]
        self.panel.api.safe("POST", "/api/stop")              # the engineer and the video maker must not both want the game
        self.log(f"Making: {r['name']}", clear=True)
        self.state.set(f"Working; about {mins} minutes. The picture appears in OBS's output folder when it is done.")
        self._run(self._studio("make", r["name"]), after=self._made)

    def _studio(self, *args: str) -> list:
        return [sys.executable, "-m", "stintradio", "studio", *args, *([] if self.coaching.get() else ["--no-coaching"])]

    def make_last(self) -> None:
        if not messagebox.askyesno(
                "KerbApex Studio",
                "Make videos of your last 5 races, one after the other?\n\nEach takes about 45 minutes, so this runs for around four hours and uses the "
                "whole PC: the game is restarted and played by keyboard and mouse. Leave the PC alone until it is finished, and do not do this while you "
                "are racing. The live engineer is stopped meanwhile.\n\nStart?", parent=self.winfo_toplevel()):
            return
        self.video = None
        self.made_from = None
        self.panel.api.safe("POST", "/api/stop")
        self.log("Making the last 5 races…", clear=True)
        self.state.set("Working; each video takes about 45 minutes. The finished videos are in OBS's output folder.")
        self._run(self._studio("last", "5"), after=lambda code: self.state.set("All five are done." if code == 0 else "It stopped before all five were made; see above."))

    def _run(self, cmd: list, after: Callable[[int], None]) -> None:
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=NO_WINDOW)
        except OSError as e:
            self.log(f"Could not start: {e}")
            return
        self._busy(True)

        def pump() -> None:
            p = self.proc
            for line in p.stdout:
                line = line.rstrip()
                v = video_from(line)
                if v:
                    self.video = v
                self.later(lambda l=line: (self.log(l), self.state.set(l[:140]) if l else None))
            code = p.wait()
            self.later(lambda: (self._busy(False), after(code)))
        threading.Thread(target=pump, daemon=True).start()

    def _made(self, code: int) -> None:
        if code == 0 and self.video:
            self.state.set(f"Done: {self.video}")
            self.b_upload.state(["!disabled"])
            self.b_folder.state(["!disabled"])
        else:
            self.state.set("It stopped without a video; the last lines above say why. If someone used the PC, leave it alone and try again.")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            studio.stop_engineer()
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
            else:
                self.proc.kill()
            self.state.set("Stopped.")

    def upload(self) -> None:
        if not (self.video and self.made_from):
            return
        if not messagebox.askyesno("KerbApex Studio", "Upload this video to your YouTube channel as a PRIVATE draft?\nYou make it public yourself, on YouTube.\n\n"
                                   "YouTube must be connected in DriverDirector (its setup does that once).", parent=self.winfo_toplevel()):
            return
        self.log("Uploading as a private draft…", clear=True)
        self.state.set("Uploading; a race video is a few gigabytes.")
        self._run(studio.command("upload", self.video, self.made_from, "--privacy", "private"),
                  after=lambda code: self.state.set("Uploaded (private): the link is in the lines above." if code == 0 else "The upload did not finish; see above."))

    def show_video(self) -> None:
        if self.video and os.path.exists(self.video):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.video)])
