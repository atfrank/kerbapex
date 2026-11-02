"""Orchestrator: scan -> detect -> plan -> record (main + PiP clips) -> commentary -> compose."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from . import commentary as cm
from . import context, driving, telemetry
from . import compose
from . import cleanup, extras, gameui
from . import tracks
from . import launcher
from . import tts
from .camera import DryRunGame, Game
from .config import Settings, data_dir
from .detect import detect_all
from . import history as hist
from . import results
from . import music
from . import sectors as sec
from .lmu_api import LMUApiError, LMUClient
from .models import Event, Frame, Inlay, Plan, ReferenceLap
from .obs import DryRunObs
from .overlay import build_state, tyre_table
from .planner import build_cinematic_plan, build_plan, video_length
from .sources.base import FrameLog


def unique_path(p: str | Path) -> str:
    """`name.mp4` -> `name.mp4` if free, else `name (2).mp4`, `name (3).mp4`... Never overwrites a video."""
    p = Path(p)
    if not p.exists():
        return str(p)
    n = 2
    while (p.with_name(f"{p.stem} ({n}){p.suffix}")).exists():
        n += 1
    return str(p.with_name(f"{p.stem} ({n}){p.suffix}"))


def _slug(text: str) -> str:
    import unicodedata
    ascii_ = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_).strip("-")


class Director:
    def __init__(self, settings: Settings, source=None, game: Game | None = None, obs=None, lmu=None):
        self.s = settings
        self.lmu = lmu or LMUClient(settings.rest_url)
        self.source = source
        self.game = game or DryRunGame()
        self.obs = obs or DryRunObs()
        self.frames: list[Frame] = []
        self.events: list[Event] = []
        self.mode = "live"                 # "live" (scanned frames) | "history" (REST lap data)
        self.history: Optional[hist.RaceHistory] = None
        self.race_replay_id: Optional[int] = None      # the replay library entry the race was loaded from
        self.race_replay_name = ""
        self.hide_wanted = False                       # recordings are meant to be made with the game's UI hidden
        self.session = None                            # the focus driver's own telemetry for this race, if any
        self._tyres = None                             # slot -> {lap: tyre letter} when the race used several compounds
        self._ctx = None                               # exact gaps and sectors for the loaded race
        self.mistakes: list = []                       # the driving errors found in the focus driver's telemetry
        self.driving_note = ""                         # why there are none, or what they were compared with
        self.sector_laps: dict = {}        # exact-times history only
        self.sector_index = sec.Index({})
        self.timeline = None               # exact gaps between cars (exact-times history only)
        self.plan: Optional[Plan] = None
        self.final_path: Optional[str] = None
        self.youtube_url: Optional[str] = None
        self.main_path: Optional[str] = None
        self.commentary_wav: Optional[str] = None
        self.script_path = data_dir() / "work" / "commentary.json"
        # hide/show LMU's replay UI; (hidden: bool) -> ok. A no-op unless wired to the real game.
        self.set_game_ui: Callable[[bool], bool] = lambda hidden: True
        self.set_telemetry: Callable[[bool], bool] = lambda on: True    # the game's speedo widget
        self.ui_failure: Callable[[], str] = lambda: ""        # why the last hide/show failed
        self.ui_reset: Callable[[], None] = lambda: None       # a freshly loaded replay always shows its UI
        # what the game is showing: "menu" | "replay" | "unknown". The game can report a replay as open (and answer its
        # data calls) while the screen still shows the main menu, so the screen has to be looked at.
        self.screen_state: Callable[[], str] = lambda: "unknown"
        self.low_memory = lambda: launcher.is_running() and tts.free_ram_gb() < 5.0   # injectable for tests
        self.manifest_path = data_dir() / "last_recording.json"
        self.status = {"stage": "idle", "message": "", "progress": 0.0}
        self.overlay_state: dict = build_state(None, 0)
        self.on_overlay: Callable[[dict], None] = lambda s: None
        self.on_status: Callable[[dict], None] = lambda s: None
        self.cancel = False
        self.workdir = data_dir() / "work"
        self._out_dir = self.workdir
        self._now, self._sleep = time.monotonic, time.sleep      # injectable for tests
        self.workdir.mkdir(parents=True, exist_ok=True)

    # ---- helpers ---------------------------------------------------
    def _set(self, stage: str, message: str = "", progress: float = 0.0) -> None:
        self.status = {"stage": stage, "message": message, "progress": round(progress, 3)}
        self.on_status(self.status)

    def roster(self) -> list[dict]:
        if not self.frames:
            return []
        return [{"id": c.id, "driver": c.driver, "number": c.number, "class": c.car_class}
                for c in sorted(self.frames[0].cars, key=lambda c: c.place)]

    def focus_id(self) -> int:
        name = self.s.direct.focus_driver.lower()
        for c in self.frames[0].cars:
            if name and (name == c.driver.lower() or name == f"#{c.number}"):
                return c.id
        raise ValueError(f"focus driver '{self.s.direct.focus_driver}' not in session roster")

    # ---- stages ----------------------------------------------------
    def scan(self, idle_timeout: float = 8.0, log_path: str | None = None) -> int:
        """Capture frames from the source until it ends or session time stops advancing."""
        self._set("scan", "capturing telemetry")
        self.frames = []
        last_t, last_move = -1.0, time.time()
        while not self.cancel:
            fr = self.source.poll()
            if fr is None:
                if getattr(self.source, "finite", False):
                    break
                if time.time() - last_move > idle_timeout:
                    break
                time.sleep(0.05)
                continue
            if fr.t > last_t:
                self.frames.append(fr)
                last_t, last_move = fr.t, time.time()
            elif time.time() - last_move > idle_timeout:
                break
        if log_path:
            FrameLog(log_path).write(self.frames)
        self._set("scan", f"{len(self.frames)} frames", 1.0)
        return len(self.frames)

    def load_log(self, path: str) -> int:
        self.frames = FrameLog(path).read()
        return len(self.frames)

    def load_history(self, race_start: float | None = None) -> dict:
        """Analyse the whole loaded replay from LMU's REST history (no playback needed)."""
        self._set("history", "reading race history from LMU")
        self.ui_reset()
        standings, session = self.lmu.standings(), self.lmu.session_info()
        incidents = self.lmu.incidents(2.0)
        h = None
        rd = results.results_dir_for(self.lmu.replay_folder())
        if rd:                                    # preferred: the game's own result file, exact times
            f = results.find_results(rd, session.get("trackName", ""), [i["et"] for i in incidents])
            if f:
                h = results.parse(f, standings)
        if h is None:                             # fallback: REST lap history, estimated times
            h = hist.parse(self.lmu.history(), incidents, session, standings, race_start)
        if not h.drivers or not h.laps:
            raise ValueError("LMU has no race history (load a race replay first)")
        self.history, self.mode = h, "history"
        self.sector_laps = sec.build(h) if h.exact else {}
        self.sector_index = sec.Index(self.sector_laps)
        self.timeline = hist.Timeline(h) if h.exact else None
        self._tyres = tyre_table(h) if h.exact else None
        self.frames = hist.frames_from_history(h)
        self.events = hist.derive_events(h)
        self.plan = None
        info = {"track": h.track, "session": h.session, "drivers": len(h.drivers),
                "source": h.source, "exact_times": h.exact,
                "race_start": round(h.race_start, 1), "race_end": round(h.race_end, 1),
                "incidents": sum(e.kind == "incident" for e in self.events),
                "lap_events": sum(e.confidence == "lap" for e in self.events)}
        self._set("history", f"{info['drivers']} drivers, {info['incidents']} incidents", 1.0)
        return info

    def analyze(self) -> list[Event]:
        if self.mode == "history":
            return self.events
        self._set("detect", "finding incidents, battles and passes")
        self.events = detect_all(self.frames, self.s.detect)
        self._set("detect", f"{len(self.events)} events", 1.0)
        return self.events

    def note_race_replay(self, replay_id: int) -> None:
        """Remember which library entry the loaded race is (the reference lap must come back to it)."""
        self.race_replay_id = replay_id
        try:
            self.race_replay_name = next((r["name"] for r in self.lmu.replays() if r["id"] == replay_id), "")
        except LMUApiError:
            self.race_replay_name = ""

    def reference_lap(self) -> ReferenceLap:
        """The focus driver's best qualifying lap and the qualifying replay it can be recorded from. Raises a
        ValueError that says what is missing (this is only asked for when direct.reference_lap is on)."""
        who = self.s.direct.focus_driver
        q = self.history.quali.get(self.focus_id()) if self.history else None
        if not q:
            raise ValueError("reference lap: no qualifying results were found for this race (the game's "
                             "Log/Results folder needs the qualifying file of the same event)")
        if q.best_lap <= 0 or q.best_lap_start < 0:
            raise ValueError(f"reference lap: {who} has no timed qualifying lap in the results file")
        if self.race_replay_id is None:
            raise ValueError("reference lap: load the replay again (dd load) so the race's library entry is known")
        reps = self.lmu.replays()
        race = next((r for r in reps if r["id"] == self.race_replay_id), None)
        if race is None:
            raise ValueError("reference lap: the race is no longer in the replay library")
        # the sessions of one event share a timestamp in the library
        cands = [r for r in reps if r["id"] != race["id"] and r["session"].lower().startswith("qual")
                 and r["track"] == race["track"] and r["timestamp"] == race["timestamp"]]
        if not cands:
            raise ValueError("reference lap: the qualifying replay of this event is not in the replay library")
        return ReferenceLap(cands[0]["id"], cands[0]["name"], who, q.best_lap, q.best_lap_start)

    def driving_events(self) -> list[Event]:
        """The focus driver's own mistakes as events, from the game's telemetry file for this race. Telemetry is
        recorded for the player's car only, so for anyone else (or without the file) there are simply none; this
        never fails planning."""
        d, h = self.s.direct, self.history
        self.mistakes, self.driving_note, self.session = [], "", None
        if not d.driving_feedback or not h or not h.exact:
            return []
        try:
            slot = self.focus_id()
            q = h.quali.get(slot)
            windows = [(e.t_start, e.t_end) for e in self.events if e.kind == "incident" and e.involves(slot)]
            span = (d.t_start if d.t_start is not None else float("-inf"),
                    d.t_end if d.t_end is not None else float("inf"))
            self.session, self.driving_note = driving.open_session(
                telemetry.dir_for(self.lmu.replay_folder()), h.track, d.focus_driver,
                [l.time for l in h.laps.get(slot, [])], q.best_lap if q else -1.0)
            ms = self.session.mistakes(d.mistake_min_loss_s, d.mistake_max, windows, span) if self.session else []
        except Exception as e:                           # missing file, locked file, odd data: no mistakes, plan on
            self.driving_note = f"driving analysis skipped ({type(e).__name__}: {str(e)[:120]})"
            return []
        self.mistakes = ms
        return [m.event(slot) for m in ms]

    def race_context(self):
        """Exact gaps and sectors for the loaded race (built once per race), or None without exact results."""
        h = self.history
        if not h or not h.exact:
            return None
        if self._ctx is None or self._ctx.h is not h:
            self._ctx = context.RaceContext(h)
        return self._ctx

    def locate_event(self, e: Event) -> str:
        """Where on track an event happened, as it is said: the corner when the focus driver's telemetry knows it ("at the
        Bus Stop", "at corner five"), else the timing sector of the car it is about ("in sector two"), else ''."""
        t = e.peak if e.peak is not None else e.t_start
        if self.session is not None and self.plan is not None and e.involves(self.plan.focus_id):
            try:
                label = self.session.where(t)
            except Exception:
                label = ""
            if label:
                return f"at {label}"
        ctx = self.race_context()
        return context.where_in_sector(ctx, e.drivers[0], t) if ctx and e.drivers else ""

    def make_plan(self) -> Plan:
        if self.s.cinematic.enabled:                       # a film: no boxes, no reference lap, no commentary
            self.plan = build_cinematic_plan(self.frames, self.events, self.focus_id(), self.s.direct,
                                             self.s.cinematic, self.s.camera)
            self._set("plan", str(self.plan.summary()), 1.0)
            return self.plan
        ref = self.reference_lap() if self.s.direct.reference_lap else None
        self.plan = build_plan(self.frames, self.events + self.driving_events(), self.focus_id(), self.s.direct,
                               self.s.camera, reference=ref)
        if ref and not any(i.replay_id is not None for i in self.plan.inlays):
            self.plan = None
            raise ValueError("reference lap: the video does not include the start of the race, so there is no "
                             "first lap to show it beside (clear direct.t_start or turn reference_lap off)")
        self._set("plan", str(self.plan.summary()), 1.0)
        return self.plan

    # ---- recording ------------------------------------------------
    def _push(self, fr, plan, mode="main", label="", caption=""):
        active = [e for e in plan.events if e.t_start <= fr.t <= e.t_end]
        panel = sec.panel(self.sector_laps, plan.focus_id, fr.t) if self.sector_laps else None
        d = self.s.direct
        h = self.history
        live = None
        if self.session is not None and d.live_delta:
            try:
                live = self.session.live(fr.t)
            except Exception:                              # a gap in the telemetry must never stop a recording
                live = None
        title = None
        if h is not None and d.title_card_s > 0 and plan.t0 + 0.5 <= fr.t <= plan.t0 + 0.5 + d.title_card_s:
            info = tracks.lookup(h.track)
            title = {"event": h.event if not any(c.isdigit() for c in h.event) else "", "track": h.track,
                     "place": info.get("place", ""), "date": h.date, "driver": d.focus_driver}
        self.overlay_state = build_state(fr, plan.focus_id, mode=mode, active_events=active,
                                         inlay_label=label, caption=caption, sectors=panel,
                                         timeline=self.timeline, sector_index=self.sector_index,
                                         ahead=d.tower_ahead, behind=d.tower_behind,
                                         race_info=(h.race_start, h.race_minutes, h.race_laps, h.total_laps)
                                         if h else None, tyres=self._tyres, live=live, title=title)
        self.on_overlay(self.overlay_state)

    def _push_label(self, focus_id: int, mode: str, label: str) -> None:
        self.overlay_state = build_state(None, focus_id, mode=mode, inlay_label=label)
        self.on_overlay(self.overlay_state)

    def _segment(self, start_t: float, end_t: float, focus_id: int, cues, mode: str, label: str,
                 stage: str, prog: tuple[float, float], camera: str | None = None,
                 actions=(), on_play=None, race_clock: bool = True, slow=(), slow_rate: float = 0.25) -> tuple[str, float]:
        """Record one stretch of replay. LMU exposes no clock during replays, so playback is driven
        by wall time after seek+play (verified: seek lands on the right moment). The overlay is
        rebuilt from race data at the same replay time. `slow` lists (start, end) replay stretches the game plays in
        its own slow motion, during which the replay clock runs at `slow_rate`. Cues that name another car move the
        camera to it. Returns (video path, lead-in seconds)."""
        h, plan = self.history, self.plan
        pts = hist._progress_points(h) if race_clock else None
        push = (lambda t: self._push(hist.frame_at(h, t, pts), plan, mode, label)) if race_clock else \
            (lambda t: self._push_label(focus_id, mode, label))     # a clip from another replay: no race data
        pending = list(cues)
        todo = sorted(actions, key=lambda a: a[0])       # (replay time, callable)
        self.game.seek(start_t)
        self.game.focus_driver(focus_id)
        first = camera or (pending.pop(0).group if pending else "trackside")
        self.game.set_camera(first)
        push(start_t)
        if self.hide_wanted:                     # a still picture: the hide is judged by what the click alone changed
            self.game.pause()                    # (a freshly opened replay is playing; play() below starts it again)
            time.sleep(0.3)
        if self.hide_wanted and not self.set_game_ui(True):     # re-checked on the picture itself, right before recording
            reason = self.ui_failure()
            msg = "the game's UI is showing right before recording" + (f" ({reason})" if reason else "")
            if self.s.direct.require_hidden_ui:
                raise RuntimeError(msg + ". Nothing was recorded for this clip.")
            self._set("warning", msg)
        rec_wall = self._now()
        self.obs.start_record(str(self._out_dir))
        self.game.play()
        play_wall = self._now()
        if on_play:
            on_play()
        lead_in = play_wall - rec_wall + self.s.direct.play_latency
        lat = self.s.direct.play_latency
        speeds = []                                        # (replay time, rate) changes, in order
        for a, b in sorted(slow):
            if a >= start_t and b <= end_t:
                speeds += [(a, slow_rate), (b, 1.0)]
        # the replay clock, piecewise: each speed command takes effect `lat` after it is sent (as play does)
        # (from the start this is start_t + elapsed - lat: the overlay waits behind the picture until it moves)
        anchor_wall, anchor_rt, old_rate, rate = play_wall, start_t - lat, 1.0, 1.0

        def replay_time(now: float) -> float:
            dt = now - anchor_wall
            return anchor_rt + min(dt, lat) * old_rate + max(0.0, dt - lat) * rate

        watched = focus_id
        while not self.cancel:
            # where the replay really is: it starts moving play_latency after the play command returns. The overlay
            # used to run on the command clock, which put it that much AHEAD of the picture.
            now = self._now()
            rt = replay_time(now)
            if speeds and speeds[0][0] <= rt:
                _, new = speeds.pop(0)
                self.game.slow() if new < 1.0 else self.game.play()
                anchor_wall, anchor_rt, old_rate, rate = now, rt, rate, new
            while pending and pending[0].t <= rt:
                cue = pending.pop(0)
                if cue.focus_id != watched:                # a cut-away to another car, or back
                    self.game.focus_driver(cue.focus_id)
                    watched = cue.focus_id
                self.game.set_camera(cue.group)
            while todo and todo[0][0] <= rt:
                todo.pop(0)[1]()
            push(rt - self.s.direct.overlay_delay_s)
            self._set(stage, f"{mode} {rt:.0f}s", prog[0] + (prog[1] - prog[0]) * max(0.0, min(1.0, (rt - start_t) / max(1.0, end_t - start_t))))
            if rt >= end_t:
                break
            self._sleep(0.1)
        self.game.pause()
        return self.obs.stop_record(), lead_in

    def _open_replay(self, replay_id: int, name: str = "") -> None:
        """Leave the current replay and open another one from the library, waiting until it is up. The replay
        list is checked first, so a changed library (ids shift when a session is added) cannot open the wrong one."""
        reps = {r["id"]: r for r in self.lmu.replays()}
        if replay_id not in reps or (name and reps[replay_id]["name"] != name):
            raise RuntimeError(f"the replay library changed (expected '{name}' as entry {replay_id}); load the "
                               "replay again and re-plan")
        if self.lmu.replay_active():
            self.lmu.exit_to_main_menu()
            quiet = 0
            for _ in range(60):                         # out of the session for several checks in a row...
                self._sleep(1.0)
                quiet = 0 if self.lmu.replay_active() else quiet + 1
                if quiet >= 3:
                    break
            else:
                raise RuntimeError("the game did not leave the replay")
            self._sleep(10.0)                           # ...then let the menu settle: a replay requested while the game
            #                                             is still going back to the menu loads its data but never opens
        self.lmu.play(replay_id)
        for _ in range(60):                             # the game needs a while to open a replay
            self._sleep(3.0)
            if self.lmu.replay_active():
                break
        else:
            raise RuntimeError(f"replay '{name or replay_id}' did not open within 3 minutes")
        self._sleep(10.0)                               # the replay screen appears a little after the game reports it
        for _ in range(30):                             # ...and sometimes much later: wait until it is really there
            if self.screen_state() != "menu":
                break
            self._sleep(3.0)
        else:
            raise RuntimeError(f"replay '{name or replay_id}' is open but the game still shows its main menu")
        self._sleep(6.0)                                # let the replay screen finish drawing before anything clicks it
        self.ui_reset()                                 # a freshly loaded replay always shows its UI

    def _slot_in_game(self, driver: str) -> int:
        """The driver's slot in the replay that is open now (slots can differ between sessions)."""
        want = results._norm(driver)
        for s in self.lmu.standings():
            if results._norm(str(s.get("driverName", ""))) == want:
                return int(s["slotID"])
        raise RuntimeError(f"{driver} is not in the replay that was just opened")

    def _record_reference(self, ref: Inlay, k: int, n: int, hide, back: bool = True) -> None:
        """Record the reference lap from the qualifying replay, then go back to the race replay. This leaves the
        race replay freshly loaded, with its UI showing, ready for the normal clips and main pass."""
        self._set(f"record clip {k + 1}/{n}", f"opening the qualifying replay ({ref.replay_name})")
        self._open_replay(ref.replay_id, ref.replay_name)
        slot = self._slot_in_game(ref.focus_name)
        if hide:
            hide(True, "reference lap")
        ref.path, ref.offset = self._segment(ref.clip_start, ref.clip_end, slot, (), "pip", ref.label,
                                             f"record clip {k + 1}/{n}", (k / (n + 1), (k + 1) / (n + 1)),
                                             camera=ref.camera, race_clock=False)
        if self.cancel or not back:
            return
        self._set(f"record clip {k + 1}/{n}", "returning to the race replay")
        self._open_replay(self.race_replay_id, self.race_replay_name)

    def record(self) -> dict:
        """Record each PiP clip, then the main pass, on a replay-time clock. In "obs" compose mode OBS
        itself shows the clips as boxes during the main pass (no video re-encode afterwards)."""
        plan = self.plan
        assert plan and self.history, "analyze the race and make a plan first"
        self._out_dir = Path(self.s.obs.output_dir or self.workdir)
        self.obs.connect()
        n = len(plan.inlays)
        self.obs.set_scene(self.s.obs.main_scene)
        self.obs.refresh_overlay()
        mode = self.s.direct.hide_game_ui            # clips | all | off
        touched = speedo = False

        def ui(hidden: bool, why: str) -> bool:
            nonlocal touched
            touched = True
            if self.set_game_ui(hidden):
                self.hide_wanted = hidden
                return True
            reason = self.ui_failure()
            msg = f"could not {'hide' if hidden else 'show'} the game UI ({why})" + (f": {reason}" if reason else "")
            if hidden and self.s.direct.require_hidden_ui:
                raise RuntimeError(msg + ". Nothing was recorded. Make sure Le Mans Ultimate is not minimised and OBS "
                                         "can see it, then run again (or set direct.require_hidden_ui to false).")
            self._set("warning", msg)               # footage will just show the game's UI
            return False

        try:
            ref = next((i for i in plan.inlays if i.replay_id is not None), None)
            if ref:                                   # first: it needs the qualifying replay, then back to the race
                self._record_reference(ref, plan.inlays.index(ref), n, ui if mode in ("clips", "all") else None)
            if mode in ("clips", "all"):
                ui(True, "clips" if mode == "clips" else "before recording")
            for k, inl in enumerate(plan.inlays):
                if self.cancel:
                    break
                if inl is ref:
                    continue
                inl.path, inl.offset = self._segment(inl.clip_start, inl.clip_end, inl.focus_id, (), "pip",
                                                     inl.label, f"record clip {k + 1}/{n}",
                                                     (k / (n + 1), (k + 1) / (n + 1)), camera=inl.camera)
            if mode == "clips" and touched:
                ui(False, "main pass")                # the main pass keeps the game's UI
            elif mode == "all":
                ui(True, "main pass")                 # no-op while still hidden; retried if the clips lost it
            if mode == "all" and self.s.direct.telemetry and plan.style != "cinematic":   # the game's own speedo
                speedo = True
                if not self.set_telemetry(True):
                    self._set("warning", "could not show the game's speedo; no telemetry in the video")
            return self._record_main(plan, n)
        finally:
            self.hide_wanted = False
            self._stop_stray_recording()              # a failed job must not leave OBS recording to fill the disk
            if speedo:
                try:
                    self.set_telemetry(False)         # the UI toggle below needs the HUD back in a uniform state
                except Exception:
                    pass
            if touched or mode == "all":              # always leave the game as we found it
                try:
                    self.set_game_ui(False)
                except Exception:
                    pass

    def _stop_stray_recording(self) -> None:
        try:
            if self.obs.status().get("recording"):
                self.obs.stop_record()
        except Exception:
            pass

    def preflight(self) -> dict:
        """Everything a recording needs, checked in seconds instead of discovered 30 minutes in: a replay is open,
        OBS answers and can see the game, and the replay UI can really be hidden (tried, then put back)."""
        checks: list[dict] = []

        def add(name: str, ok: bool, detail: str = "") -> bool:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
            return bool(ok)

        add("replay open", self.lmu.replay_active() is True, "open the replay first (dd load)")
        if self.lmu.replay_active() is True:
            add("replay on screen", self.screen_state() != "menu",
                "the game reports a replay but is showing its main menu: run dd exit-replay, wait 20 seconds, then dd load again")
        add("race loaded", bool(self.history and self.plan), "load the replay and make a plan first")
        if self.plan and any(i.replay_id is not None for i in self.plan.inlays):
            try:
                self.reference_lap()
                add("qualifying replay", True)
            except (ValueError, LMUApiError) as e:
                add("qualifying replay", False, str(e))
        if self.plan:
            est = extras.estimate(self.plan)
            out_dir = self.s.obs.output_dir or str(self.workdir)
            free = extras.free_gb(out_dir)
            add("disk space", free >= est.disk_gb,
                f"{free:.0f} GB free in {out_dir}, this video needs about {est.disk_gb:.0f} GB")
        try:
            self.obs.connect()
            add("obs", True)
        except Exception as e:
            add("obs", False, str(e)[:200])
        if self.s.direct.hide_game_ui in ("clips", "all"):
            ok = self.set_game_ui(True)
            add("hide game UI", ok, "" if ok else self.ui_failure())
            if ok:
                add("show game UI", self.set_game_ui(False), self.ui_failure())
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    def rerecord_clips(self) -> str:
        """Record the picture-in-picture clips again (for example after changing their camera) and finish the
        video with the main pass, commentary and music that already exist. The new plan must put every box at the
        same time as before, because the commentary was written and voiced against those times."""
        plan = self.plan
        assert plan and self.history, "load the replay and make a plan first"
        old = json.loads(self.manifest_path.read_text())
        if len(old["inlays"]) != len(plan.inlays) or any(
                abs(a["show_at"] - b.show_at) > 0.5 for a, b in zip(old["inlays"], plan.inlays)):
            raise ValueError("the plan's picture-in-picture times differ from the recording; "
                             "re-recording just the clips would put the commentary out of step")
        plan.main_offset, self.main_path = old["main_offset"], old["main"]
        self._out_dir = Path(self.s.obs.output_dir or self.workdir)
        self.obs.connect()
        self.obs.set_scene(self.s.obs.main_scene)
        self.obs.refresh_overlay()
        n = len(plan.inlays)
        hidden = False
        try:
            if self.s.direct.hide_game_ui in ("clips", "all"):
                hidden = self.set_game_ui(True)
            for k, inl in enumerate(plan.inlays):
                if self.cancel:
                    raise RuntimeError("cancelled")
                if inl.replay_id is not None:         # recorded from another replay: keep the clip already made
                    inl.path, inl.offset = old["inlays"][k]["path"], old["inlays"][k]["offset"]
                    continue
                inl.path, inl.offset = self._segment(inl.clip_start, inl.clip_end, inl.focus_id, (), "pip",
                                                     inl.label, f"record clip {k + 1}/{n}",
                                                     (k / n, (k + 1) / n), camera=inl.camera)
        finally:
            if hidden:
                try:
                    self.set_game_ui(False)
                except Exception:
                    pass
        self._save_manifest()
        if self.low_memory() and self.s.direct.exit_replay_after_record:
            self._set("recorded", "Leaving the replay to free memory...", 1.0)
            try:
                if self.lmu.exit_to_main_menu():
                    for _ in range(20):
                        self._sleep(1.0)
                        if not self.low_memory():
                            break
            except Exception:
                pass
        if self.low_memory():
            self._set("recorded", "Recording saved. Close Le Mans Ultimate to free memory, then press "
                                  "'Produce video'.", 1.0)
            return self.main_path
        return self.produce(reuse_voice=True)

    def _raw_back(self, path: str, batch: str | None = None) -> str:
        """A finished job moves its raw recordings to _cleanup/<batch>/; bring one back to where the manifest says it
        was. OBS numbers its files again from the start once they have been moved away, so two races can have files of the
        same name in different batches: the manifest names the batch (`batch`), and without it a name found in more than one
        batch is refused rather than guessed (a guess once put another race's clips into a video). Raises if it is nowhere."""
        p = Path(path)
        if p.is_file():
            return str(p)
        if batch:
            f = p.parent / "_cleanup" / batch / p.name
            if f.is_file():
                shutil.move(str(f), str(p))
                return str(p)
        found = sorted(p.parent.glob(f"_cleanup/*/{p.name}"), key=lambda f: f.stat().st_mtime, reverse=True)
        if len(found) > 1 and not batch:
            raise FileNotFoundError(f"{p.name} is in {len(found)} _cleanup batches ({', '.join(f.parent.name for f in found)}) and "
                                    "this recording does not say which one is its own; move the right file next to the manifest "
                                    "path or point the manifest at it")
        if found and not batch:
            shutil.move(str(found[0]), str(p))
            return str(p)
        raise FileNotFoundError(f"{p.name} is not in {p.parent} or its _cleanup/{batch or '*'} folder")

    def _adopt_recording(self, skip: Inlay | None = None) -> None:
        """Take the last finished recording (main pass and clips) as the one to finish again: the plan must put every
        box where it was (the commentary was written against those times), and the raw files are brought back from
        _cleanup if the finished job moved them aside. `skip` is a clip that is about to be recorded anew."""
        plan = self.plan
        old = json.loads(self.manifest_path.read_text())
        if len(old["inlays"]) != len(plan.inlays) or any(
                abs(a["show_at"] - b.show_at) > 0.5 for a, b in zip(old["inlays"], plan.inlays)):
            raise ValueError("the plan's picture-in-picture times differ from the recording; "
                             "finishing it again would put the commentary out of step")
        batch = old.get("batch")
        plan.main_offset, self.main_path = old["main_offset"], self._raw_back(old["main"], batch)
        for inl, o in zip(plan.inlays, old["inlays"]):
            if inl is not skip:
                inl.path, inl.offset = self._raw_back(o["path"], batch), o["offset"]

    def redo_commentary(self) -> str:
        """Write the commentary again (new wording, new style), voice it (the current voice) and finish the video with
        the recording that already exists. Nothing is recorded, so the game is not needed."""
        assert self.plan and self.history, "load the replay and make a plan first"
        self._adopt_recording()
        self._save_manifest()
        if self.s.commentary.enabled:
            self.prepare_commentary()
        self._leave_replay_if_memory_is_tight()
        return self.produce(reuse_voice=False)

    def _leave_replay_if_memory_is_tight(self) -> None:
        """The encode needs memory the game is holding: leave the replay (as a recording does) when RAM is low."""
        if not (self.low_memory() and self.s.direct.exit_replay_after_record):
            return
        self._set("recorded", "Leaving the replay to free memory...", 1.0)
        try:
            if self.lmu.exit_to_main_menu():
                for _ in range(20):
                    self._sleep(1.0)
                    if not self.low_memory():
                        break
        except Exception:
            pass

    def rerecord_reference(self) -> str:
        """Record the reference lap again (for example because its game UI was not hidden) and finish the video with the
        main pass, the other clips, the commentary and the music that already exist. Only the qualifying replay is
        visited; the race replay is not reopened."""
        plan = self.plan
        assert plan and self.history, "load the replay and make a plan first"
        ref = next((i for i in plan.inlays if i.replay_id is not None), None)
        if ref is None:
            raise ValueError("this plan has no reference lap (set direct.reference_lap)")
        self._adopt_recording(skip=ref)
        self._out_dir = Path(self.s.obs.output_dir or self.workdir)
        self.obs.connect()
        self.obs.set_scene(self.s.obs.main_scene)
        self.obs.refresh_overlay()

        def hide(hidden: bool, why: str) -> bool:
            if self.set_game_ui(hidden):
                self.hide_wanted = hidden
                return True
            raise RuntimeError(f"could not {'hide' if hidden else 'show'} the game UI ({why})"
                               + (f": {self.ui_failure()}" if self.ui_failure() else "") + ". Nothing was recorded.")
        try:
            self._record_reference(ref, plan.inlays.index(ref), len(plan.inlays),
                                   hide if self.s.direct.hide_game_ui in ("clips", "all") else None, back=False)
        finally:
            self.hide_wanted = False
            self._stop_stray_recording()
            try:
                self.set_game_ui(False)
            except Exception:
                pass
        self._save_manifest()
        if self.low_memory() and self.s.direct.exit_replay_after_record:
            self._set("recorded", "Leaving the replay to free memory...", 1.0)
            try:
                if self.lmu.exit_to_main_menu():
                    for _ in range(20):
                        self._sleep(1.0)
                        if not self.low_memory():
                            break
            except Exception:
                pass
        return self.produce(reuse_voice=True)

    def _record_main(self, plan: Plan, n: int) -> dict:
        live = self.s.compose_mode == "obs"
        actions, cleanup = [], []
        scene, d = self.s.obs.main_scene, self.s.direct
        if live:                                   # OBS shows each clip as a box while the main pass records
            clips = [i for i in plan.inlays if i.path]
            handles = self.obs.setup_pip(scene, [i.path for i in clips], d.inlay_size, d.inlay_corner,
                                         border_pct=d.inlay_border_pct, border_color=d.inlay_border_color)
            cleanup.append(lambda: self.obs.pip_cleanup(handles))
            for inl, h in zip(clips, handles):
                actions.append((inl.show_at, lambda h=h, ms=int(inl.offset * 1000): self.obs.pip_show(h, ms)))
                actions.append((inl.show_until, lambda h=h: self.obs.pip_hide(h)))
        try:
            cine = plan.style == "cinematic"
            if cine:                                       # a film: the leaderboard/name card overlay stays off
                self.obs.set_item_enabled(scene, self.s.obs.overlay_source, False)
                cleanup.append(lambda: self.obs.set_item_enabled(scene, self.s.obs.overlay_source, True))
            main, plan.main_offset = self._segment(plan.t0, plan.t1, plan.focus_id, plan.cues, "main", "",
                                                   "record main", (n / (n + 1), 1.0), actions=actions,
                                                   slow=plan.slow if cine else (),
                                                   slow_rate=self.s.cinematic.slow_rate)
        finally:
            for fn in cleanup:
                try:
                    fn()
                except Exception:
                    pass
        self.main_path = main
        self._save_manifest()
        return {"main": main, "clips": [i.path for i in plan.inlays]}

    def _save_manifest(self) -> None:
        """What compositing needs, so a failed composite can be retried after a restart."""
        p = self.plan
        self.manifest_path.write_text(json.dumps({
            "main": self.main_path, "main_offset": p.main_offset, "focus_id": p.focus_id,
            "t0": p.t0, "t1": p.t1, "stem": self.final_stem(), "style": p.style, "slow": [list(s) for s in p.slow],
            "inlays": [{"path": i.path, "offset": i.offset, "show_at": i.show_at, "show_until": i.show_until,
                        "focus_id": i.focus_id, "camera": i.camera, "label": i.label, "slow": i.slow, "names": list(i.names),
                        "replay": i.replay, "replay_id": i.replay_id,
                        "replay_name": i.replay_name, "focus_name": i.focus_name} for i in p.inlays],
        }, indent=1))

    def restore_recording(self) -> bool:
        """Reload the last recording's manifest (used to retry compositing after a restart)."""
        try:
            m = json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            return False
        inl = [Inlay(Event("incident", i["show_at"], i["show_until"], [i["focus_id"]]), i["show_at"], i["show_until"],
                     i["show_at"], i["show_until"], i["focus_id"], i["camera"], i["label"], i["path"], i["offset"],
                     i.get("replay_id"), i.get("replay_name", ""), i.get("focus_name", ""), slow=i.get("slow", 1.0),
                     names=list(i.get("names", [])), replay=bool(i.get("replay", False)))
               for i in m["inlays"]]
        self.plan = Plan(m["focus_id"], m["t0"], m["t1"], inlays=inl, main_offset=m["main_offset"],
                         slow=[tuple(s) for s in m.get("slow", [])], style=m.get("style", "broadcast"))
        self.main_path = m["main"]
        self._saved_stem = m.get("stem", "")
        return True

    # ---- post ---------------------------------------------------
    def commentary_scenes(self) -> list:
        """The scenes (what happens when, with the facts to talk about) of the current plan."""
        plan = self.plan
        if not plan:
            raise ValueError("make a plan first")
        return cm.build_scenes(plan, self.frames, history=self.history, ctx=self.race_context(),
                               locate=self.locate_event, conditions=self.s.direct.conditions,
                               mistakes=self.mistakes)

    def save_script(self, lines: dict) -> dict:
        """Take a commentary script that was written elsewhere (one line per scene id), hold it to the rules the writer
        is held to (facts only, word budget, no assumed genders) and save it as the script. Returns the problems found;
        nothing is saved while there are any."""
        scenes = self.commentary_scenes()
        parsed = {int(k): str(v).strip() for k, v in lines.items()}
        errs = cm.validate(parsed, scenes, self.s.commentary)
        errs += [f"scene {i}: no such scene" for i in parsed if i not in {s.id for s in scenes}]
        if errs:
            return {"saved": False, "errors": errs}
        plan = self.plan
        script = {"t0": plan.t0, "t1": plan.t1, "scenes": cm.scenes_to_json(scenes),
                  "lines": {str(k): v for k, v in parsed.items()}}
        self.script_path.write_text(json.dumps(script, indent=1, ensure_ascii=False), encoding="utf-8")
        return {"saved": True, "lines": len(parsed), "errors": []}

    def prepare_commentary(self, client=None) -> dict:
        """Write the commentary script (light: one call to Claude) and save it, so the heavy voicing step
        can run later, after the game is closed - even after the app is restarted."""
        plan = self.plan
        scenes = self.commentary_scenes()
        if self.s.commentary.reuse_script and self.script_path.is_file():
            try:
                saved = json.loads(self.script_path.read_text(encoding="utf-8"))
                fits = (abs(saved["t0"] - plan.t0) < 1e-6 and abs(saved["t1"] - plan.t1) < 1e-6
                        and [(s["id"], round(s["t"], 1)) for s in saved["scenes"]] == [(s.id, round(s.t, 1)) for s in scenes])
            except (OSError, ValueError, KeyError):
                fits = False
            if fits:
                self._set("commentary", f"using the script already written ({len(saved['lines'])} lines)", 1.0)
                return saved
        self._set("commentary", f"writing {len(scenes)} scenes")
        lines = cm.generate_script(scenes, self.s.commentary, client=client)
        script = {"t0": plan.t0, "t1": plan.t1, "scenes": cm.scenes_to_json(scenes),
                  "lines": {str(k): v for k, v in lines.items()}}
        self.script_path.write_text(json.dumps(script, indent=1, ensure_ascii=False), encoding="utf-8")
        self._set("commentary", f"{len(lines)} lines written", 1.0)
        return script

    def _level(self, wav: Optional[str]) -> Optional[str]:
        """The commentary track compressed and lifted to the target loudness (the track itself is kept as it was voiced)."""
        c = self.s.commentary
        if not wav or not c.level_voice:
            return wav
        try:
            self._set("voicing", "levelling the commentary", 1.0)
            return compose.level_voice(wav, str(self.workdir / "commentary_leveled.wav"), c.voice_target_lufs)
        except Exception as e:                   # never lose a video to the levelling: use the track as it is
            self._set("warning", f"could not level the commentary: {str(e)[-200:]}")
            return wav

    def _voice_commentary(self, reuse: bool = False) -> Optional[str]:
        """Voice the saved script into one timed audio track (the memory-hungry step with Chatterbox).
        `reuse` keeps an existing track when it is newer than the script (nothing to say differently)."""
        if not (self.s.commentary.enabled and self.script_path.is_file()):
            return None
        done = self.workdir / "commentary.wav"
        stamp = self.workdir / "commentary.voice"
        c = self.s.commentary
        voice = "|".join(str(x) for x in (c.tts_engine, c.voice, c.edge_voice, c.exaggeration, c.fish_voice_id,
                                          c.fish_model, c.elevenlabs_voice_id, c.elevenlabs_model))
        same_voice = stamp.is_file() and stamp.read_text(encoding="utf-8") == voice     # a new voice means new audio
        if reuse and same_voice and done.is_file() and done.stat().st_mtime >= self.script_path.stat().st_mtime:
            self.commentary_wav = str(done)
            return str(done)
        script = json.loads(self.script_path.read_text(encoding="utf-8"))
        scenes = [cm.Scene(**d) for d in script["scenes"]]
        lines = {int(k): v for k, v in script["lines"].items()}
        wav = str(self.workdir / "commentary.wav")
        n = len([s for s in scenes if s.id in lines])
        self._set("voicing", f"0/{n} lines")
        cm.render_track(scenes, lines, self.s.commentary, script["t0"], script["t1"] - script["t0"], wav,
                        str(self.workdir / "tts"),
                        progress=lambda i, total: self._set("voicing", f"{i}/{total} lines", i / total))
        stamp.write_text(voice, encoding="utf-8")
        self.commentary_wav = wav
        return wav

    # ---- YouTube ---------------------------------------------------
    def youtube_authorize(self) -> dict:
        """Sign in to YouTube in the user's browser (they approve it there); the address is also shown in the status."""
        from . import youtube
        self._set("youtube", "Waiting for you to sign in to Google in your browser...", 0.0)
        r = youtube.authorize(self.s.youtube, announce=lambda url: self._set(
            "youtube", f"Waiting for you to sign in. If no browser opened, go to: {url}", 0.0))
        self._set("idle", f"YouTube connected ({r.get('channel') or 'channel name unavailable'})", 1.0)
        return r

    def youtube_draft(self, video: Optional[str] = None) -> dict:
        """Title/description/tags for the finished video, from the plan and the race (chapters at each box)."""
        from . import youtube
        video = video or self.final_path
        h, p = self.history, self.plan
        if not (h and p):
            raise ValueError("load the replay and make a plan first (the title and chapters come from them)")
        length = (compose.probe_duration(video) if video and Path(video).is_file() else p.t1 - p.t0)
        who = next((c.driver for c in self.frames[0].cars if c.id == p.focus_id), "Driver")
        last = self.frames[-1] if self.frames else None
        me = next((c for c in last.cars if c.id == p.focus_id), None) if last else None
        from . import facts as race_facts
        overall, in_class = race_facts.final_places(h, p.focus_id) if h.exact else (0, 0)
        finish = (f"Finished P{overall or me.place} overall ({me.car_class} P{in_class or me.class_place})."
                  if me and me.place else "")
        if p.style == "cinematic":                # a film: no commentary, and slow motion moves every chapter time
            event = f" ({h.event})" if h.event and not any(c.isdigit() for c in h.event) else ""
            music = self.s.cinematic.music_source != "off"
            desc = (f"A cinematic film of {who}'s race at {h.track}{event} in Le Mans Ultimate: long TV and trackside "
                    f"shots, cut-aways to the biggest moments in the field, and the key moments in the game's own slow "
                    f"motion. No commentary. {finish}".strip()
                    + ("\n\nThe music is AI-generated." if music else "") + "\nMade with DriverDirector."
                    + (f"\n\n{self.s.youtube.description_extra}" if self.s.youtube.description_extra else ""))
            title = self.s.youtube.title_template.format(driver=who, track=h.track, session=h.session.title())
            return {"title": f"{title} | Cinematic"[:100], "description": desc, "tags": self.s.youtube.tags + ["cinematic"],
                    "privacy": self.s.youtube.privacy, "video": video}
        d = youtube.draft_metadata(self.s.youtube, who, h.track, h.session.title(),
                                   extras.chapter_moments(p, h, self.mistakes), length, finish,
                                   music=self.s.music.source != "off", voice=self.s.commentary.enabled)
        d["video"] = video
        return d

    def publish_youtube(self, video: str, title: str, description: str, privacy: str, tags=None) -> dict:
        from . import youtube
        self._set("youtube", "uploading to YouTube", 0.0)

        def prog(done: int, total: int) -> None:
            self._set("youtube", f"uploading {done / 1048576:.0f} / {total / 1048576:.0f} MB", done / max(1, total))

        r = youtube.upload(self.s.youtube, video, title, description, privacy, tags, progress=prog)
        self.youtube_url = r["url"]
        self._set("done", r["url"], 1.0)
        return r

    def prepare_music(self) -> str:
        """Make (or find) the music loop ahead of time, so producing a video later does not wait for it."""
        cfg = self.s.music
        if cfg.source == "folder":
            loop = music.folder_loop(cfg.folder)
            if not loop:
                raise ValueError(f"no music files found in '{cfg.folder}'")
        elif cfg.source == "elevenlabs":
            self._set("music", "making the music with ElevenLabs (once; it is reused for later videos)", 0.0)
            loop = music.generate_elevenlabs(cfg)
        elif cfg.source == "ai":
            self._set("music", "generating the music (about 3 minutes per clip on CPU)", 0.0)
            loop = music.generate(cfg, self.s.commentary,
                                  lambda i, n, secs: self._set("music", f"clip {i}/{n} done ({secs:.0f}s)", i / n))
        else:
            raise ValueError("music is switched off")
        self._set("idle", f"music ready: {loop}", 1.0)
        return str(loop)

    def _add_music(self, wav: Optional[str], cfg=None, length: Optional[float] = None) -> tuple[Optional[str], Optional[list]]:
        """Lay the background music under the commentary. Returns (the audio to mix over the game sound,
        the speech windows the game sound should duck for). A music failure never loses the video."""
        cfg = cfg or self.s.music
        if cfg.source == "off" or not self.plan:
            return wav, None
        length = length or self.plan.t1 - self.plan.t0
        try:
            if cfg.source == "folder":
                loop = music.folder_loop(cfg.folder)
                if not loop:
                    raise RuntimeError(f"no music files found in '{cfg.folder}'")
            elif cfg.source == "elevenlabs":
                self._set("music", "making the music with ElevenLabs (once; it is reused for later videos)", 0.0)
                loop = music.generate_elevenlabs(cfg)
            else:
                self._set("music", "generating the music (once; it is reused for later videos)", 0.0)
                loop = music.generate(cfg, self.s.commentary,
                                      lambda i, n, secs: self._set("music", f"clip {i}/{n} done ({secs:.0f}s)", i / n))
            windows = compose.speech_windows(wav) if wav else []
            bed = music.build_bed(str(loop), length, windows, cfg, str(self.workdir / "music_bed.wav"))
            if not wav:
                return bed, []
            return music.mix(wav, bed, str(self.workdir / "soundtrack.wav")), windows
        except (RuntimeError, OSError) as e:
            self._set("warning", f"no background music this time: {str(e)[-300:]}")
            return wav, None

    def produce(self, reuse_voice: bool = False) -> str:
        """Finish the video from the saved recording: voice the commentary, then add it (and the PiP boxes
        in ffmpeg mode). Needs no game, so close LMU first to leave memory for the voice and encode."""
        if not (self.plan and self.main_path) and not self.restore_recording():
            raise ValueError("nothing recorded yet")
        if self.plan.style == "cinematic":
            return self.finish_cinematic(self.main_path)
        wav = self._level(self._voice_commentary(reuse_voice))
        wav, windows = self._add_music(wav)
        try:
            if self.s.compose_mode == "obs":                    # boxes are already in the recording
                if not wav:
                    self.final_path = self.main_path
                    self._set("done", self.main_path, 1.0)
                    return self.main_path
                self._set("compose", "adding commentary (video copied, not re-encoded)")
                out = unique_path(Path(self.main_path).with_name((self.final_stem() or Path(self.main_path).stem) + "_commentary.mp4"))
                compose.mux_commentary(self.main_path, wav, out, self.plan.main_offset, self.s.commentary.duck_db,
                                       windows=windows, voice_gain_db=self.s.commentary.voice_gain_db)
                self.final_path = out
                self._set("done", out, 1.0)
                self._tidy(out)
                return out
            return self.finish(self.main_path, wav, windows=windows)
        except RuntimeError as e:
            self._set("error", f"recording is saved ({self.main_path}); finishing failed - press Produce again: "
                               f"{str(e)[-300:]}")
            raise

    def final_stem(self) -> str:
        """`<track>_<date>_<session>_<driver>`, named from the race itself (OBS's own file names are not
        reliable: they can carry the name of an earlier session)."""
        h = self.history
        if not h or not h.track:
            return getattr(self, "_saved_stem", "")        # after a restart: the name saved with the recording
        parts = [_slug(h.track), h.date or time.strftime("%Y-%m-%d"), (h.session or "Race").title(),
                 _slug(self.s.direct.focus_driver)]
        return "_".join(p for p in parts if p)

    @property
    def output_dir(self) -> Path:
        return Path(self.s.obs.output_dir or self.workdir)

    def protected_paths(self) -> list[str]:
        """The recordings of a job that has not produced its video yet (never to be tidied away)."""
        try:
            m = json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            return []
        paths = [m.get("main")] + [i.get("path") for i in m.get("inlays", [])]
        newest = self.manifest_path.stat().st_mtime
        d = self.output_dir
        finished = d.is_dir() and any(cleanup.is_final(f) and f.stat().st_mtime >= newest - 1 for f in d.iterdir())
        return [] if finished else [p for p in paths if p]

    def rebuild_recording(self, folder: str) -> dict:
        """Write the recording info (what finishing a video again needs) for a race whose raw files are in `folder`, when the
        info itself is gone (a later recording overwrote it). The race must be loaded and planned. The files are matched to the
        plan's boxes in the order they were recorded (the boxes in plan order, the main pass last) and every match is checked
        against the length the box needs, so a wrong file is refused instead of ending up in a video. Lead-ins are the usual
        0.3 s (the real ones were not kept)."""
        plan = self.plan
        if not plan:
            raise ValueError("load the race and make a plan first")
        files = sorted(Path(folder).glob("*.mp4"), key=lambda f: f.stat().st_mtime)
        if len(files) != len(plan.inlays) + 1:
            raise ValueError(f"{len(files)} recordings in {folder}, but the plan has {len(plan.inlays)} boxes (and a main pass)")
        main = files[-1]
        want = plan.t1 - plan.t0
        if abs(compose.probe_duration(str(main)) - want) > 8:
            raise ValueError(f"the newest file ({main.name}) is {compose.probe_duration(str(main)):.0f} s, the main pass should be "
                             f"about {want:.0f} s")
        rows = []
        for inl, f in zip(plan.inlays, files[:-1]):
            need = (inl.show_until - inl.show_at) * (inl.slow if inl.slow < 1.0 else 1.0)
            got = compose.probe_duration(str(f))
            if got < need - 0.5 or got - need >= 4.0:
                raise ValueError(f"{f.name} is {got:.1f} s but the box '{inl.label[:30]}' needs {need:.1f} s")
            rows.append({"path": str(f).replace("\\", "/"), "offset": 0.33, "show_at": inl.show_at,
                         "show_until": inl.show_until})
        manifest = {"main": str(main).replace("\\", "/"), "main_offset": 0.32, "focus_id": plan.focus_id,
                    "t0": plan.t0, "t1": plan.t1, "inlays": rows}
        self.manifest_path.write_text(json.dumps(manifest, indent=1))
        return {"main": main.name, "clips": len(rows)}

    def estimate(self) -> dict:
        """How long recording and finishing will take and the disk they need, for the plan."""
        if not self.plan:
            raise ValueError("make a plan first")
        e = extras.estimate(self.plan, self.s.cinematic.slow_rate).as_dict()
        e["free_gb"] = round(extras.free_gb(self.s.obs.output_dir or str(self.workdir)), 1)
        return e

    def write_description(self, video: str) -> Optional[str]:
        """The YouTube title, description and chapters as a text file next to the video (never fails the job)."""
        try:
            d = self.youtube_draft(video)
            p = Path(video).with_suffix(".youtube.txt")
            p.write_text(f"{d['title']}\n\n{d['description']}\n\nTags: {', '.join(d['tags'])}\n", encoding="utf-8")
            return str(p)
        except Exception:
            return None

    def _cut_source(self, video: Optional[str]) -> str:
        video = video or self.final_path
        if not video or not Path(video).is_file():
            raise ValueError("no finished video to cut from: make the video first, or give its path")
        if not self.plan and not self.restore_recording():
            raise ValueError("nothing planned or recorded")
        return video

    def make_highlights(self, video: Optional[str] = None, target_s: float = 240.0) -> str:
        """A short cut of the finished video (with its commentary): the start, each incident and mistake, the pit stops
        and the finish. Reads only the video; the game is not needed."""
        video = self._cut_source(video)
        out = unique_path(Path(video).with_name(Path(video).stem + "_highlights.mp4"))
        self._set("highlights", "cutting the highlights", 0.0)
        extras.highlights(video, self.plan, self.history, out, self.mistakes, target_s,
                          encoders=["libx264"] if self.s.encoder == "cpu" else None,
                          progress=lambda fr, note: self._set("highlights", note, fr))
        self._set("done", out, 1.0)
        return out

    def make_shorts(self, video: Optional[str] = None) -> list[str]:
        """One vertical clip per picture-in-picture moment, into a folder next to the video."""
        video = self._cut_source(video)
        d = self.s.direct
        folder = Path(video).with_name(Path(video).stem + "_shorts")
        self._set("shorts", "making the shorts", 0.0)
        files = extras.shorts(video, self.plan, str(folder), d.inlay_size, d.inlay_corner, d.inlay_border_pct,
                              encoders=["libx264"] if self.s.encoder == "cpu" else None,
                              progress=lambda fr, note: self._set("shorts", note, fr))
        self._set("done", str(folder), 1.0)
        return files

    def sync_test(self, delays=(0.4, 1.0, 1.6), seconds: float = 18.0) -> str:
        """Record the same short stretch of the replay once per overlay delay and put them side by side, so the delay that
        lines the standings up with the picture can be picked by eye in a minute instead of by re-recording the race.
        Needs the replay open (like a recording). Ends with the game's UI shown again."""
        plan, h = self.plan, self.history
        assert plan and h, "load the replay and make a plan first"
        focus = plan.focus_id
        passes = [e for e in self.events if e.kind == "overtake" and e.involves(focus)
                  and plan.t0 <= (e.peak or e.t_start) <= plan.t1 - seconds]
        anyev = [i.event for i in plan.inlays if i.replay_id is None]
        ev = passes[0] if passes else (anyev[0] if anyev else None)
        mid = (ev.peak if ev.peak is not None else ev.t_start) if ev else plan.t0 + 120
        start = max(plan.t0, mid - seconds * 0.4)
        end = min(plan.t1, start + seconds)
        self._out_dir = Path(self.s.obs.output_dir or self.workdir)
        self.obs.connect()
        self.obs.set_scene(self.s.obs.main_scene)
        self.obs.refresh_overlay()
        if self.s.direct.hide_game_ui != "off" and not self.set_game_ui(True):
            raise RuntimeError("could not hide the game UI: " + (self.ui_failure() or "unknown reason"))
        keep, clips = self.s.direct.overlay_delay_s, []
        try:
            for k, delay in enumerate(delays):
                self.s.direct.overlay_delay_s = delay
                path, lead = self._segment(start, end, focus, [], "main", "", f"sync test {k + 1}/{len(delays)}",
                                           (k / len(delays), (k + 1) / len(delays)), camera="trackside")
                clips.append((delay, path, lead))
        finally:
            self.s.direct.overlay_delay_s = keep
            try:
                self.set_game_ui(False)
            except Exception:
                pass
            self._stop_stray_recording()
        out = unique_path(self._out_dir / f"sync_test_{time.strftime('%H%M%S')}.mp4")
        self._set("compose", "putting the takes side by side")
        extras.side_by_side(clips, out, seconds)
        self._set("done", out, 1.0)
        return out

    def _tidy(self, final: str) -> None:
        """After a verified final video: move this job's raw recordings aside and purge old batches. A problem
        here is reported but never fails the job."""
        if self.s.cleanup != "after_finish":
            return
        try:
            want = self.plan.t1 - self.plan.t0
            if self.plan.style == "cinematic":        # slow motion makes the film longer than the replay span
                want = video_length(self.plan, self.s.cinematic.slow_rate)
            got = compose.probe_duration(final)
            slack = 5.0 + 3.0 * len(self.plan.slow)       # each speed change lands about play_latency late
            if abs(got - want) > slack:
                self._set("warning", f"kept the raw recordings: the video is {got:.0f}s, expected {want:.0f}s")
                return
            out_dir = Path(final).parent
            raws = [self.main_path] + [i.path for i in self.plan.inlays]
            moved = cleanup.sweep([r for r in raws if r and Path(r).parent == out_dir], out_dir)
            if moved and self.manifest_path.is_file():     # keep what finishing this video again would need, with its raw files
                batch = Path(moved[0]).parent
                m = json.loads(self.manifest_path.read_text())
                m["batch"] = batch.name                    # which _cleanup batch holds this recording's raw files
                self.manifest_path.write_text(json.dumps(m, indent=1))
                (batch / "recording.json").write_text(json.dumps(m, indent=1))
            cleanup.purge(out_dir, self.s.cleanup_keep_days)
            if moved:
                self._set("done", final, 1.0)
        except Exception as e:
            self._set("warning", f"could not tidy the recordings: {str(e)[:200]}")

    def finish(self, main: str, commentary_wav: str | None = None, out: str | None = None, windows=None) -> str:
        plan = self.plan
        d = self.s.direct
        stem = self.final_stem()
        # the finished video goes where OBS records, even when the raw files were kept in a folder of their own
        home = Path(self.s.obs.output_dir) if self.s.obs.output_dir else Path(main).parent
        out = out or unique_path(home / ((stem or Path(main).stem) + ("" if stem else "_directed") + ".mp4"))
        self._set("compose", "ffmpeg")
        started = time.monotonic()

        def progress(frac: float, note: str = "") -> None:
            elapsed = time.monotonic() - started
            eta = f", about {max(1, round(elapsed / frac / 60 - elapsed / 60))} min left" if frac >= 0.03 and elapsed > 60 else ""
            self._set("compose", f"ffmpeg {note}".strip() + f" {frac * 100:.0f}%{eta}", frac)

        compose.compose(main, plan.inlays, out, plan.t0, progress=progress, chunk_s=self.s.compose_chunk_s,
                        size=d.inlay_size, corner=d.inlay_corner,
                        border_pct=d.inlay_border_pct, border_color=d.inlay_border_color,
                        commentary=commentary_wav, duck_db=self.s.commentary.duck_db,
                        voice_gain_db=self.s.commentary.voice_gain_db,
                        main_offset=plan.main_offset, windows=windows,
                        encoders=["libx264"] if self.s.encoder == "cpu" else None)
        self.final_path = out
        self._set("done", out, 1.0)
        self._tidy(out)
        self.write_description(out)
        return out

    def cinematic_title(self) -> list[str]:
        h, d = self.history, self.s.direct
        if h is None:
            return [d.focus_driver] if d.focus_driver else []
        info = tracks.lookup(h.track)
        event = h.event if h.event and not any(c.isdigit() for c in h.event) else ""
        where = ", ".join(x for x in (h.track, info.get("place", "")) if x)
        return [x for x in (event or h.track, where if event else info.get("place", ""), d.focus_driver) if x]

    def finish_cinematic(self, main: str) -> str:
        """The film: music bed of the film's real length (slow motion makes it longer than the replay span), graded,
        letterboxed, faded, with the opening title. No commentary, no boxes."""
        c = self.s.cinematic
        offset = self.plan.main_offset
        length = max(1.0, compose.probe_duration(main) - offset)
        mcfg = self.s.music.model_copy(update={"source": c.music_source, "prompt": c.music_prompt,
                                               "level_db": c.music_level_db})
        bed, _ = self._add_music(None, mcfg, length)
        stem = self.final_stem()
        home = Path(self.s.obs.output_dir) if self.s.obs.output_dir else Path(main).parent
        out = unique_path(home / ((stem or Path(main).stem) + "_cinematic.mp4"))
        self._set("compose", "ffmpeg cinematic")
        started = time.monotonic()

        def progress(frac: float, note: str = "") -> None:
            elapsed = time.monotonic() - started
            eta = f", about {max(1, round(elapsed / frac / 60 - elapsed / 60))} min left" if frac >= 0.03 and elapsed > 60 else ""
            self._set("compose", f"ffmpeg cinematic {frac * 100:.0f}%{eta}", frac)

        compose.compose_cinematic(main, out, offset, length, bed, progress=progress,
                                  encoders=["libx264"] if self.s.encoder == "cpu" else None,
                                  letterbox=c.letterbox, grade=c.grade, fade_s=c.fade_s,
                                  title=self.cinematic_title() if c.title else (), title_s=c.title_s,
                                  game_level_db=c.game_level_db)
        self.final_path = out
        self._set("done", out, 1.0)
        self._tidy(out)
        self.write_description(out)
        return out

    # ---- preview (no game needed) --------------------------------
    def preview(self, speed: float = 10.0) -> None:
        """Replay the captured frames through the overlay at `speed`x, to tune themes and plans."""
        plan = self.plan
        assert plan, "make_plan() first"
        start = time.time()
        for fr in self.frames:
            if fr.t < plan.t0:
                continue
            if fr.t > plan.t1 or self.cancel:
                break
            wait = (fr.t - plan.t0) / speed - (time.time() - start)
            if wait > 0:
                time.sleep(wait)
            inl = next((i for i in plan.inlays if i.show_at <= fr.t <= i.show_until), None)
            self._push(fr, plan, "main", inl.label if inl else "")
            self._set("preview", f"t={fr.t:.0f}", (fr.t - plan.t0) / max(1.0, plan.t1 - plan.t0))
        self._set("idle", "preview finished", 1.0)

    def run_all(self) -> str:
        """Write the commentary script, record clips + main pass, then produce the video. If memory is
        tight (game still open), stop after recording and wait for the user to close it and press Produce."""
        if self.s.commentary.enabled and not self.s.cinematic.enabled:
            self.prepare_commentary()
        paths = self.record()
        if self.low_memory() and self.s.direct.exit_replay_after_record:
            self._set("recorded", "Leaving the replay to free memory...", 1.0)
            try:
                if self.lmu.exit_to_main_menu():
                    for _ in range(20):                    # memory is released a few seconds after the exit
                        self._sleep(1.0)
                        if not self.low_memory():
                            break
            except Exception:
                pass                                       # fall through to asking the user to close the game
        if self.low_memory():
            self._set("recorded", "Recording saved. Close Le Mans Ultimate to free memory, then press "
                                  "'Produce video'.", 1.0)
            return paths["main"]
        return self.produce()

    compose = produce            # earlier name
