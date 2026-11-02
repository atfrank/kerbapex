"""The live loop: read the car ~50x a second, build laps, keep the reference, coach each corner, and serve a small state
for the overlay page (/live), which can be pinned in the Steam overlay over a fullscreen game."""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from . import laps as LP, trackmap
from .coach import Coach
from .reader import LiveReader

STEAM_GAME = Path("steamapps") / "common" / "Le Mans Ultimate"


def find_game_dir(configured: str = "") -> Optional[str]:
    """The Le Mans Ultimate install: the configured folder, else Steam's default library, else any library listed in
    Steam's libraryfolders.vdf."""
    if configured and (Path(configured) / "Support").is_dir():
        return configured
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"]
    vdf = roots[0] / "steamapps" / "libraryfolders.vdf"
    try:
        roots += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace"))]
    except OSError:
        pass
    for r in roots:
        if (r / STEAM_GAME / "Support" / "SharedMemoryInterface").is_dir():
            return str(r / STEAM_GAME)
    return None


class LiveService:
    HZ = 50.0
    ROLL_S = 10.0

    def __init__(self, game_dir: str, cfg=None, commentary_cfg=None):
        self.game_dir = game_dir
        self.cfg = cfg
        self.commentary_cfg = commentary_cfg      # where the ElevenLabs voice id and model are set
        self.cues = None
        self.speaker = None
        self.engineer = None
        self.radio = None
        self.watch = None
        self.snap: Optional[dict] = None
        self.gap_hist: deque = deque(maxlen=40)       # (time, gap ahead, gap behind) once a second, for the trend
        self.gaps = None
        from .jobs import JobBook
        from .memory import Memory
        self.jobs = JobBook()
        self.memory = Memory()
        self.ptt = None
        self._said_focus: set = set()
        self._said_guide: set = set()
        self._chatterbox = None
        self.coaching = True             # corner coaching on/off by voice (race calls and answers always stay on)
        self.recent_laps: dict = {}          # lap number -> the Lap just driven (last few; for the engineer's lookups)
        self.lap_log: list = []              # (lap number, time) of the laps completed this session
        self.last_sectors: list = []         # cumulative sector times of the last completed lap
        self.sector_bounds: dict = {}        # track -> [distance of sector 1 end, sector 2 end] in metres
        self.last_sector_delta: list = []    # per-sector time against the reference on the last completed lap
        self.qa: list = []                   # the questions asked and the answers given (newest last)
        from .ffb import FFBWatch
        self.ffb = FFBWatch()
        self._guide_from: Optional[int] = None     # the lap the corner guide began on
        self._race_check = 0.0
        self.said_text: list = []
        self.reader: Optional[LiveReader] = None
        self.recorder = None
        self.thread: Optional[threading.Thread] = None
        self.stop_flag = False
        self.lock = threading.Lock()
        self.status, self.error = "stopped", ""
        self.sample = None
        self.builder = LP.LapBuilder()
        self.coach: Optional[Coach] = None
        self.ref_info: dict = {}
        self.combo: Optional[tuple] = None
        self.rolling: deque = deque()
        self.notes: deque = deque(maxlen=8)
        self.lap_obs: list = []
        self.judged: set = set()
        self.saved: list = []
        self.replaying = False             # a test replay of a stored lap: never saved again
        self.leader: Optional[dict] = None   # the class leader being recorded (races only)
        self.leader_builder = LP.LapBuilder()
        self.leader_laps: list = []
        from .battle import Battle
        self.battle = Battle()                      # where the time goes against the car directly ahead
        self.fastest_lap: Optional[float] = None   # the quickest lap anyone has set in this race (RaceRoom's session best)
        self._leader_check = 0.0
        from .experiments import Watcher
        self.setup_watch = Watcher()               # brake bias, ABS, ... : a change starts a before/after comparison
        self.experiment = None
        self.last_experiment = ""
        self._setup_check = 0.0
        self._prop_lap = -99
        self._shift_said: dict = {}
        self._exp_busy = False
        self.wear_hist: list = []                  # (laps completed, average tyre wear 0..1) at each line crossing
        self._map_laps: dict = {}                  # (track key, car key) -> the own lap drawn when the reference has no positions
        self._map_view: Optional[dict] = None      # the last track map made (they are only remade when the id changes)
        from .history import CornerHistory
        from .session import SessionWatch
        self.sessions = SessionWatch()             # which session this is, a new one starting, the pit lane
        self.corner_history = CornerHistory()      # what the driver does in each corner over many sessions (the briefing is made of it)
        self.session_history = CornerHistory(memory_only=True)    # ... and the same for this session only (the overlay's "work on" list)
        self._info: Optional[dict] = None          # the game's session information with the latest reading
        self._lap_pit = False                      # the lap in progress included the pit lane: it is not judged or saved
        self._brief_pending: Optional[dict] = None # a briefing is due when the car stands on the grid

    def _voice_on(self) -> None:
        """Make the clips (once; cached) and the player, if the voice is wanted."""
        c = self.cfg
        if c is None or not getattr(c, "voice", False):
            self.cues = self.speaker = None
            return
        from . import voice
        self.cues = voice.BrakeCues(c.countdown_from, c.speak_gear, variety=getattr(c, "countdown_variety", True))
        self.drive = getattr(c, "voice_drive_db", 6.0)
        if self.speaker is None:
            boost = getattr(c, "voice_boost_db", 12.0)
            folder = None
            self._chatterbox = self._make_chatterbox()
            if self._chatterbox is not None:                  # one voice: the cues are Chatterbox too
                try:
                    folder = voice.make_clips_chatterbox(self._chatterbox, boost)
                except Exception as e:
                    self.error = f"Chatterbox cues unavailable, using the cached ones: {str(e)[:160]}"
            if folder is None and getattr(c, "voice_engine", "windows") == "elevenlabs" and self.commentary_cfg is not None:
                try:
                    folder = voice.make_clips_elevenlabs(self.commentary_cfg, boost)
                except Exception as e:                    # no key, no network: the clone's cached clips, else quiet cues
                    folder = voice.cached_eleven(boost, self.commentary_cfg.elevenlabs_voice_id)
                    self.error = (f"ElevenLabs voice unavailable, using the cached clone clips: {str(e)[:140]}" if folder
                                  else f"ElevenLabs voice unavailable, cues are silent: {str(e)[:140]}")
            if folder is None and getattr(c, "voice_engine", "windows") == "elevenlabs":
                folder = voice.silent_folder()            # never a computer voice when the clone was asked for
            folder = folder or voice.make_clips(c.voice_rate, boost)
            self.speaker = voice.Speaker(voice.master_folder(folder, self.drive))
        if getattr(c, "style", "countdown") == "engineer":
            from .engineer import Engineer
            from .radio import Radio
            from .racewatch import RaceWatch
            self.engineer = Engineer(driver=self._driver_name(), settle=int(getattr(c, "coach_laps", 5)))
            self.watch = RaceWatch(getattr(c, "talk", "normal"))
            engine = self._radio_engine()
            cb = (self._chatterbox or self._make_chatterbox()) if engine == "chatterbox" else None
            self.radio = Radio(engine, self.commentary_cfg, getattr(c, "engineer_model", "eleven_flash_v2_5"),
                               getattr(c, "radio_effect", True), c.voice_rate, chatterbox=cb)
        else:
            self.engineer = self.radio = self.watch = None

    def _radio_engine(self) -> str:
        return getattr(self.cfg, "radio_engine", "") or getattr(self.cfg, "voice_engine", "windows")

    def _make_chatterbox(self):
        """The Chatterbox worker handle when the radio engine is chatterbox (its reference voice: live.chatterbox_voice, else
        made from the ElevenLabs countdown clips that already exist)."""
        c = self.cfg
        if self._radio_engine() != "chatterbox":
            return None
        from .radio import ChatterboxVoice, reference_clip
        from . import voice
        ref = Path(c.chatterbox_voice) if getattr(c, "chatterbox_voice", "") else None
        if ref is None and self.commentary_cfg is not None:
            try:
                ref = reference_clip(voice.eleven_dir(self.commentary_cfg.elevenlabs_voice_id,
                                                      getattr(c, "voice_boost_db", 12.0)),
                                     voice.data_dir() / "live_voice" / "chatterbox_reference.wav")
            except Exception as e:
                self.error = f"no Chatterbox reference voice: {str(e)[:120]}"
        if self._chatterbox is not None:
            self._chatterbox.close()
        return ChatterboxVoice(ref, getattr(c, "chatterbox_threads", 4))

    def _guiding(self, s) -> bool:
        """True during the corner-guide laps, when the name call carries the focus tip."""
        g = getattr(self.cfg, "corner_guide", 0)
        return g > 0 and self._guide_from is not None and s.lap - self._guide_from < g

    def _driver_name(self) -> str:
        """The first name the engineer uses: live.driver_name, else the player's name from the game (RaceRoom)."""
        name = (getattr(self.cfg, "driver_name", "") or "").strip()
        if not name:
            try:
                name = (self.reader.player_name() or "").strip()
            except Exception:
                name = ""
        return name.split()[0] if name else ""

    CORNER_KINDS = {"guide", "focus", "habit", "debrief", "line", "praise", "shift", "experiment"}

    def quiet(self, secs: float = 3.0) -> bool:
        """True on a straight: off the brakes, and the next braking point of the reference is far enough away to say
        a `secs`-second call before it (or the car is slow, in the pits or stopped). The engineer only talks then."""
        s, c = self.sample, self.coach
        if s is None:
            return False
        if s.speed < 60:
            return True
        if s.brake > 5:
            return False
        if c is None:
            return True
        v = s.speed / 3.6
        for k in c.corners:
            if k.brake is None:
                continue
            ahead = (k.brake - s.dist) % s.track_len if s.track_len else k.brake - s.dist
            if 0 <= ahead / v < max(3.5, secs + 0.8):
                return False
            if s.track_len and 0 <= (s.dist - k.apex) % s.track_len < 100:   # still in the corner
                return False
        return True

    def _radio(self, m, gated: bool = True) -> None:
        """Make the call in the background, then queue it: said on the next straight, dropped once it is stale."""
        if self.radio is None or self.speaker is None:
            return
        if not self.coaching and m.kind in self.CORNER_KINDS:
            return
        if self.engineer is not None:
            m = self.engineer.address(m)
        self.said_text.append(m.text)
        sp, prio, exp, drive = self.speaker, m.priority, m.expires, getattr(self, "drive", 0.0)
        from . import voice
        epoch = sp.epoch

        def queue(path):
            if sp.epoch == epoch:               # not if the driver cut the engineer off while it was being made
                sp.say_file(voice.master(path, drive), prio, exp, self.quiet if gated else None, m.kind)
        self.radio.submit(m.text, queue)

    # ---- control --------------------------------------------------------------------------------------------------
    def _learn(self) -> bool:
        """False in a video run: what it sees is a race already learned from, so nothing is saved a second time."""
        return bool(getattr(self.cfg, "learn", True))

    def _open_reader(self):
        """The sim adapter for the game that is running: live.game is a sim id (raceroom, lmu, demo, or any adapter that is
        installed, see sims/) or auto (whichever sim's live data is there now)."""
        from . import sims
        return sims.open_sim(self.cfg, game_dir=self.game_dir)

    def start(self) -> dict:
        if self.thread and self.thread.is_alive():
            return {"running": True}
        if self.thread and self.thread.is_alive():          # a test replay still playing
            self.stop_flag = True
            self.thread.join(2)
        self.reader = self._open_reader()                # raises a clear error if the live data is not there
        if self.recorder is not None:
            self.recorder.close()
        self.recorder = None
        if getattr(self.cfg, "record", True) and "recording" in getattr(self.reader, "capabilities", ()) and hasattr(self.reader, "last_frame"):
            from .recorder import Recorder
            self.recorder = Recorder(getattr(self.cfg, "record_hz", 10.0))
        try:
            self._voice_on()
        except Exception as e:                            # no voice is not a reason to lose the overlay
            self.error = f"voice off: {e}"
        with self.lock:                                   # nothing left over from a test replay
            self.notes.clear()
            self.rolling.clear()
            self.judged.clear()
            self.combo, self.coach, self.ref_info, self.sample = None, None, {}, None
            self.builder = LP.LapBuilder()
        self.stop_flag, self.status, self.error, self.replaying = False, "waiting", "", False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self._ptt_on()
        return {"running": True}

    # ---- push-to-talk: hold the button, ask, hear the answer on the radio ---------------------------------------------
    def _ptt_on(self) -> None:
        if self.ptt is not None:
            self.ptt.stop()
            self.ptt = None
        c = self.cfg
        if self.radio is None or not getattr(c, "ptt", False):
            return
        try:
            from . import ptt
            from driverdirector.compose import ffmpeg_bin
            self.ptt = ptt.PushToTalk(ptt.Button(c.ptt_button), ptt.Recorder(ffmpeg_bin(), c.ptt_mic), self._asked,
                                      mode=getattr(c, "ptt_mode", "hold"), max_s=getattr(c, "ptt_max_s", ptt.MAX_S),
                                      beep=ptt.beeper if getattr(c, "ptt_beep", True) else None,
                                      on_start=self._interrupt)
            self.ptt.start()
        except Exception as e:                            # a bad button name must not stop the coach
            self.error = f"push-to-talk off: {e}"

    def _interrupt(self) -> None:
        """A question is starting: stop whatever the engineer is saying and drop what was waiting to be said."""
        if self.speaker is not None:
            self.speaker.interrupt()

    def _asked(self, path) -> None:
        """A recorded question: transcribe it, have Claude answer from the live facts, say the answer."""
        from . import ask, ask_tools
        from .engineer import Msg
        try:
            from driverdirector import commentary, tts
            exe = commentary.find_claude_cli()
            if not exe:
                raise RuntimeError("the Claude CLI was not found")
            use_tools = bool(getattr(self.cfg, "ask_tools", False)) and self.recorder is not None
            client = (ask_tools.ClaudeMcpClient(exe) if use_tools else commentary.ClaudeCliClient(exe, timeout=40.0))
            ans = ask.handle(path, self, tts.cloud_key("elevenlabs"), client, getattr(self.cfg, "ptt_language", ""),
                             native_tools=use_tools)
        except Exception as e:
            self.error = f"ask failed: {str(e)[:200]}"
            return
        if ans is None:
            return
        if ans.command:
            self.coaching = ans.command == "coaching_on"
            if self.speaker is not None and not self.coaching:
                self.speaker.interrupt()            # what was queued for the corners goes too
        text = ans.reply
        self.qa.append({"question": getattr(ans, "question", ""), "reply": ans.reply})
        if ans.job:                                   # only the fixed job shapes get through; else say so
            from .jobs import parse
            job = parse(ans.job, self.snap)
            if job is None or not self.jobs.add(job):
                text = "Sorry, I can't set that one up."
        if ans.forget:
            self.memory.forget(ans.forget)
        if ans.remember:
            self.memory.remember(ans.remember)
        self._radio(Msg(text, priority=0, expires=30.0, kind="answer"), gated=False)

    def stop(self) -> dict:
        if self.ptt is not None:
            self.ptt.stop()
            self.ptt = None
        self.stop_flag = True
        self.status = "stopped"
        if self.radio is not None and self.radio.chatterbox is not None:
            self.radio.chatterbox.close()
        if self.recorder is not None:
            self.recorder.close()
        s = self.sample
        if s is not None and not self.replaying and self._learn():           # remembered for next time on this track and car
            self.memory.session_done(s.track, s.car, s.best_lap, max(0, s.lap - 1), bool(self.snap and self.snap["session"] >= 10))
        return {"running": False}

    # ---- loop -----------------------------------------------------------------------------------------------------
    def _loop(self) -> None:
        period = 1.0 / self.HZ
        while not self.stop_flag:
            t0 = time.perf_counter()
            try:
                smp = self.reader.sample()
                self._info = self.reader.session_info() if smp is not None and hasattr(self.reader, "session_info") else None
                self.step(smp)
                if self.recorder is not None and self.reader.last_frame is not None:
                    self.recorder.offer(self.reader, self.reader.last_frame)
                if self.engineer is not None and t0 - self._race_check > 1.0:
                    self._race_check = t0
                    g = self.gaps = self.reader.race_gaps()
                    if g:
                        self.gap_hist.append((t0, g[1], g[2]))
                    if g and self.sample is not None and self.sample.track_len:
                        near = self._neighbours()
                        frac = self.sample.dist / self.sample.track_len
                        for m in self.engineer.race(t0, g[0], g[1], g[2], frac, near[0], near[1]):
                            self._radio(m)
                    if self.watch is not None:
                        snap = self.snap = self.reader.race_snapshot()
                        if snap and g and g[1] is not None and g[1] < 12 and self.sample is not None and self.sample.track_len:
                            a = self._car_ahead(snap)
                            if a is not None and not a.get("in_pits"):
                                me = snap["me"]
                                self.battle.update(snap["t"], (me["laps"], me["dist"]), (a["laps"], a["dist"]), a["name"],
                                                   self.sample.track_len)
                        for m in (self.watch.update(snap) if snap else []) + self.jobs.due(snap, self.watch, self.gaps):
                            self._radio(m)
                if self.engineer is not None and t0 - self._setup_check > 0.5:
                    self._setup_check = t0
                    self._watch_setup()
                if getattr(self.cfg, "reference", "best") == "leader":
                    if t0 - self._leader_check > 1.0:      # who leads the class: checked once a second
                        self._leader_check = t0
                        self.set_leader(self.reader.class_leader())
                        self.fastest_lap = self.reader.session_best()
                    if self.leader:
                        self.step_leader(self.reader.sample(self.leader["id"]))
            except Exception as e:                       # never let one bad read end the session
                self.error = f"{type(e).__name__}: {e}"
            time.sleep(max(0.0, period - (time.perf_counter() - t0)))

    def _use(self, ref, track: str, track_len: float, who: str = "") -> None:
        self.coach = Coach(ref, track, track_len, getattr(self.reader, "TURN_SIGN", 0)) if ref else None
        if ref is not None:
            self._learn_sectors(track, self.coach.ref)        # a reference with sector times tells where the sectors end
        self.ref_info = ({"source": ref.source, "time": ref.time, "corners": len(self.coach.corners),
                          **({"driver": who} if who else {})} if ref else {})

    def _reference(self, s) -> None:
        """The reference lap: in a race (reference = leader), the class leader's fastest recorded lap once there is
        one; otherwise the fastest stored lap of this car on this track (yours or Coach Dave's)."""
        lead = [l for l in self.leader_laps if LP.key(l.track) == LP.key(s.track)]
        if lead:
            best = min(lead, key=lambda l: l.time)
            return self._use(best, s.track, s.track_len, getattr(best, "driver", ""))
        self._use(LP.best(s.track, s.car), s.track, s.track_len)

    # ---- the class leader (races) ---------------------------------------------------------------------------------
    def set_leader(self, car: Optional[dict]) -> None:
        with self.lock:
            if (car or {}).get("id") != (self.leader or {}).get("id"):
                self.leader_builder = LP.LapBuilder()      # a new leader: start recording their laps afresh
            self.leader = car

    def step_leader(self, ls) -> None:
        """One reading of the class leader's car; a completed lap faster than the reference becomes the reference."""
        if ls is None:
            return
        with self.lock:
            done = self.leader_builder.add(ls)
            if done is None:
                return
            done.source = "leader"
            done.driver = (self.leader or {}).get("name", "").split("#")[0].strip()
            self.leader_laps.append(done)
            cur = self.ref_info.get("time", 1e9) if self.ref_info.get("source") == "leader" else 1e9
            if done.time < cur and self.sample is not None:
                self._use(done, self.sample.track, self.sample.track_len, done.driver)

    def step(self, s) -> None:
        """One reading (None when not driving). Separate from the thread so it can be fed recorded data in tests."""
        with self.lock:
            if s is None:
                self.status = "waiting"
                self.sample = None
                return
            self.status = "driving"
            combo = (LP.key(s.track), LP.key(s.car))
            if combo != self.combo:
                self.combo = combo
                self.builder = LP.LapBuilder()
                self.rolling.clear()
                self.judged.clear()
                self._guide_from, self._said_guide = None, set()
                self._reference(s)
            self._session_step(s)                    # a new session, or the pit lane: before this reading is used
            prev_lap = self.builder.lap
            done = self.builder.add(s)
            if prev_lap is not None and s.lap != prev_lap:
                self.judged = set()
            if done is not None:                     # a lap that included the pit lane is not a lap: not judged, not saved, no debrief
                pit_lap, self._lap_pit = self._lap_pit, bool(self.sessions.in_pits)
                if pit_lap:
                    self.lap_obs = [n for n in self.lap_obs if n.lap != prev_lap]
                    done = None
            if done is not None:
                own = [float(p.stem.split("_")[1]) for p in LP.stored_any(s.track, s.car) if p.stem.startswith("live_")]
                best_before = min(own) if own else None
                ref_time, ref_src = self.ref_info.get("time"), self.ref_info.get("source", "")
                if (self.fastest_lap and getattr(self.cfg, "reference", "best") == "leader"
                        and (not ref_time or self.fastest_lap < ref_time)):
                    ref_time, ref_src = self.fastest_lap, "fastest"       # the race's quickest lap so far, when it beats the reference
                self.lap_log.append((prev_lap, done.time))
                self.recent_laps[prev_lap] = done
                for k in sorted(self.recent_laps)[:-6]:
                    del self.recent_laps[k]
                self.last_sectors = list(done.sectors)
                self._learn_sectors(s.track, done)
                self.last_sector_delta = self.sector_deltas(done.samples, s.track, final=done.time)
                if not self.replaying:
                    if self._learn():
                        self.saved.append(str(LP.save(done)))
                    if not self.coach or done.time < self.ref_info.get("time", 1e9):
                        self._reference(s)               # a new best lap becomes the reference
                if self.engineer is not None:
                    lap_notes = [n for n in self.lap_obs if n.lap == prev_lap]
                    self.lap_obs = [n for n in self.lap_obs if n.lap != prev_lap]
                    if not self.replaying and self.coach and self._learn():                 # what this lap showed goes into the history the briefings are made from
                        self.corner_history.add_lap(s.track, s.car, lap_notes, self.coach.corners, s.track_len, done.time)
                        self.session_history.add_lap(s.track, s.car, lap_notes, self.coach.corners, s.track_len, done.time)
                    for m in self.engineer.lap_done(done.time, ref_time, ref_src, best_before, lap_notes):
                        self._radio(m)
                    self._said_focus = set()
                    self._note_wear(prev_lap)
                    bm = self._battle_call(prev_lap)
                    if bm is not None:
                        self._radio(bm)
                    self._experiment_tick(prev_lap)
                    if getattr(self.cfg, "ffb_advice", True) and not self.replaying:
                        for m in self.ffb.lap_done(self.engineer.pick):
                            self._radio(m)
            self.sample = s
            in_pit = bool(self.sessions.in_pits) and self._info is not None       # in the pit lane: no corners, no countdowns
            if getattr(self.cfg, "ffb_advice", True):
                self.ffb.add(s.speed, s.ffb)
            self.rolling.append((s.t, s.brake, s.throttle, s.speed))
            while self.rolling and s.t - self.rolling[0][0] > self.ROLL_S:
                self.rolling.popleft()
            if self.coach and self.cues and self.speaker and self.coaching and not in_pit:
                mode = getattr(self.cfg, "countdown", "all") if self.engineer is not None else "all"
                if mode == "all":
                    corners = self.coach.corners
                elif mode == "focus" and self.engineer.focus:
                    corners = [c for c in self.coach.corners if c.n == self.engineer.focus]
                else:
                    corners = []
                for clip in self.cues.due(s.dist, s.speed, s.track_len, corners):
                    self.speaker.say(clip)
                if self.engineer is not None and self.engineer.focus:
                    fc = next((c for c in self.coach.corners if c.n == self.engineer.focus), None)
                    if (fc and fc.brake is not None and s.speed > 60 and (s.lap, fc.n) not in self._said_focus
                            and not self._guiding(s)):
                        eta = ((fc.brake - s.dist) % s.track_len if s.track_len else fc.brake - s.dist) / (s.speed / 3.6)
                        if 5.5 <= eta <= 7.5:            # early enough to be said before the countdown starts
                            self._said_focus.add((s.lap, fc.n))
                            for m in self.engineer.approaching(fc.n, fc.name):
                                self._radio(m, gated=False)
                guide = getattr(self.cfg, "corner_guide", 0)
                if self.engineer is not None and guide > 0:
                    if self._guide_from is None:
                        self._guide_from = s.lap
                    if s.lap - self._guide_from < guide and s.speed > 60:
                        for c in self.coach.corners:
                            if (s.lap, c.n) in self._said_guide:
                                continue
                            mark = c.brake if c.brake is not None else c.apex - 60      # a corner with no braking zone
                            eta = ((mark - s.dist) % s.track_len if s.track_len else mark - s.dist) / (s.speed / 3.6)
                            if 3.6 <= eta <= 8.5:          # as soon as it is in range; a close corner is still named late
                                self._said_guide.add((s.lap, c.n))
                                tip = ""
                                if c.n == self.engineer.focus and self.engineer.focus_tip:    # the tip rides on the name
                                    tip = self.engineer.focus_tip
                                    self._said_focus.add((s.lap, c.n))
                                    self.engineer._tip_lap[c.n] = self.engineer._laps
                                self._radio(self.engineer.guide(c.n, c.look, c.brake_peak, tip))
                tmode = getattr(self.cfg, "throttle_cue", "focus")
                if tmode != "off":
                    guiding = guide > 0 and self._guide_from is not None and s.lap - self._guide_from < guide
                    tc = (self.coach.corners if tmode == "all" or guiding else
                          [c for c in self.coach.corners if self.engineer is not None and c.n == self.engineer.focus])
                    for clip in self.cues.throttle_due(s.dist, s.speed, s.throttle, s.track_len, tc):
                        self.speaker.say(clip)
                if getattr(self.cfg, "short_shift", True):
                    for clip in self.cues.shift_due(s.dist, s.speed, s.gear, s.track_len, self.coach.shifts):
                        self.speaker.say(clip)
            if self.coach and not in_pit:
                cur = LP.Lap(s.track, s.car, 0.0, "live", self.builder.cur)
                for c in self.coach.corners:
                    if c.n not in self.judged and s.dist >= c.done_at:
                        self.judged.add(c.n)
                        note = self.coach.judge(cur, c, s.lap)
                        if note:
                            self.lap_obs.append(note)            # every corner, for the engineer's patterns
                            if self.engineer is not None and self.coaching and not self.replaying:
                                good = self.engineer.nailed(note, s.t)       # a corner done right: say so, with what was right
                                if good is not None:
                                    self._radio(good)
                        if note and not note.report:
                            note = None
                        if note:
                            self.notes.appendleft(note)
            self._brief_step(s)

    # ---- sessions, the pit lane and the briefing -------------------------------------------------------------------
    def _session_step(self, s) -> None:
        """A new session, or the car in or out of the pit lane, from the game's session information (RaceRoom)."""
        if self._info is None or self.replaying:
            return
        for ev, data in self.sessions.update(self._info, s.track, s.car):
            if ev == "new_session":
                self._new_session(s, data)
            elif ev == "pit_in":
                self._pit_in()
            elif ev == "pit_out":
                self._pit_out(data)
        if self.sessions.in_pits:
            self._lap_pit = True

    def _kind(self) -> str:
        from .session import KINDS
        return KINDS.get((self._info or {}).get("type"), "session")

    def _race_info(self) -> dict:
        i = self._info or {}
        laps = int(i.get("max_laps") or 0)
        return {"laps": laps if 0 < laps < 10_000 else 0, "minutes": (i.get("time_total") or 0) / 60.0 if laps <= 0 or laps >= 10_000 else 0}

    def _new_session(self, s, data: dict) -> None:
        """A different session begins: what belonged to the last one is forgotten, and a briefing is due."""
        from .battle import Battle
        from .engineer import Engineer
        from .experiments import Watcher
        from .history import CornerHistory
        from .racewatch import RaceWatch
        self.session_history = CornerHistory(memory_only=True)
        self.lap_log.clear()
        self.recent_laps.clear()
        self.lap_obs = []
        self.notes.clear()
        self.judged = set()
        self.saved = []
        self._said_focus, self._said_guide, self._guide_from = set(), set(), None
        self.wear_hist.clear()
        self.gap_hist.clear()
        self.gaps = None
        self.battle = Battle()
        self.last_sectors, self.last_sector_delta = [], []
        self.fastest_lap = None
        self.leader, self.leader_laps, self.leader_builder = None, [], LP.LapBuilder()
        self.setup_watch, self.experiment, self.last_experiment = Watcher(), None, ""
        self.builder = LP.LapBuilder()
        self.rolling.clear()
        self._lap_pit = False
        if self.engineer is not None:
            self.engineer = Engineer(driver=self._driver_name(), settle=int(getattr(self.cfg, "coach_laps", 5)))
        if self.watch is not None:
            self.watch = RaceWatch(getattr(self.cfg, "talk", "normal"))
        if self.speaker is not None:
            self.speaker.interrupt()                        # whatever was waiting to be said belonged to the last session
        if not self.replaying and self._learn():
            self.corner_history.session_started(s.track, s.car)
        self._reference(s)
        self._brief_pending = {"t": s.t, "kind": data.get("kind", "session")}

    def _pit_in(self) -> None:
        if self.engineer is None or self._kind() != "race":
            return                                          # in practice the car is in and out of the pits all the time: only understood, not said
        worn = self.worn_pct(self.snap) if self.snap else None
        from . import briefing as BR
        self._radio(BR.pit_in("race", sum(worn) / len(worn) if worn else None), gated=False)

    def _pit_out(self, data: dict) -> None:
        if self.engineer is None or self._kind() != "race" or data.get("first"):
            return
        from . import briefing as BR
        self._radio(BR.pit_out("race"), gated=False)

    def _brief_step(self, s) -> None:
        """The briefing, once, while the car stands on the grid (or in the pit lane at the start) before the first lap."""
        p = self._brief_pending
        if p is None:
            return
        if self.engineer is None or not getattr(self.cfg, "briefing", True) or s.t - p["t"] > 30.0:
            self._brief_pending = None                      # joined late, or no voice: no briefing for this session
            return
        if s.lap > 1 or s.speed > 15 or s.t - p["t"] < 1.5:
            return
        self._brief_pending = None
        m = self.briefing_for(s.track, s.car, p["kind"], self._race_info())
        if m is not None:
            self._radio(m, gated=False)

    def focus_list(self, k: int = 3) -> list:
        """The corners most worth working on, for the overlay: this session's own worst corners first (once they have been seen on two
        laps), then the ones earlier runs lost the most time in. [{n, name, loss, tip}], worst first, matched to the reference's numbers."""
        from . import briefing as BR
        s = self.sample
        if s is None or not self.coach or not s.track_len:
            return []
        out, seen = [], set()
        for p in self.session_history.pointers(s.track, s.car, k, min_laps=2) + self.corner_history.pointers(s.track, s.car, k + 2):
            if p["key"] in seen:
                continue
            seen.add(p["key"])
            c = next((c for c in self.coach.corners if str(round(c.apex / s.track_len * 100)) == p["key"]), None)
            if c is not None:
                out.append({"n": c.n, "name": c.name, "loss": round(p["loss"], 2), "tip": BR.short_tip(p)})
            if len(out) == k:
                break
        return out

    def briefing_for(self, track: str, car: str, kind: str = "race", race: Optional[dict] = None):
        """The briefing for a session of this kind on this track in this car, from earlier runs only."""
        from . import briefing as BR
        best = LP.best(track, car)
        target = (self.ref_info.get("time") if self.combo == (LP.key(track), LP.key(car)) else None) or (best.time if best else None)
        return BR.briefing(kind, target, self.memory.last(track, car), self.corner_history.pointers(track, car, 3),
                           self.corner_history.laps(track, car), race)

    def preview_briefing(self, kind: Optional[str] = None, track: Optional[str] = None, car: Optional[str] = None, say: bool = False) -> dict:
        """What the engineer would say at the start of a session now, for the track and car being driven (or the last ones): to hear
        it without driving. `say` also has it spoken."""
        if not track or not car:
            if self.sample is not None:
                track, car = self.sample.track, self.sample.car
            elif self.memory.sessions:
                track, car = list(self.memory.sessions)[-1].split("|", 1)
            else:
                return {"text": "", "error": "No track and car yet: drive a lap first, or give them."}
        kind = kind or (self._kind() if self._info else "race")
        m = self.briefing_for(track, car, kind, self._race_info() if kind == "race" else None)
        if say and m is not None and self.engineer is not None:
            self._radio(m, gated=False)
        return {"text": m.text if m else "", "track": track, "car": car, "kind": kind,
                "pointers": self.corner_history.pointers(track, car, 3), "spoken": bool(say and self.engineer is not None)}

    def replay(self, lap: LP.Lap, track_len: float, speed: float = 1.0) -> dict:
        """Play a stored lap through the coach in real time, as if driving it (to try the overlay without the game).
        The reference stays whatever is best for that car and track."""
        from .reader import Sample
        if self.thread and self.thread.is_alive():
            self.stop_flag = True
            self.thread.join(2)
        lap = lap.scaled(track_len)

        def run():
            base = 1000.0
            for n in (1, 2):                                     # lap 1 = the replayed lap; lap 2 starts as it ends
                last = lap.time if n == 2 else -1.0
                pts = lap.samples if n == 1 else lap.samples[:5]
                for i, p in enumerate(pts):
                    if self.stop_flag:
                        return
                    t = base + p[6] + (lap.time if n == 2 else 0.0)
                    self.step(Sample(t=t, lap=n, lap_start=base + (0 if n == 1 else lap.time), dist=p[0], speed=p[1],
                                     throttle=p[3], brake=p[2], steer=0.0, gear=0, rpm=0.0,
                                     x=p[4] or 0.0, z=p[5] or 0.0, delta_best=0.0, invalid=False, track=lap.track,
                                     car=lap.car, track_len=track_len, last_lap=last, best_lap=-1.0))
                    if i + 1 < len(pts):
                        time.sleep(max(0.0, (pts[i + 1][6] - p[6]) / speed))
            self.status = "stopped"

        try:
            self._voice_on()
        except Exception as e:
            self.error = f"voice off: {e}"
        self.stop_flag, self.replaying, self.combo = False, True, None
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return {"replaying": f"{lap.track} / {lap.car} {lap.time:.3f}s"}

    # ---- what the page shows --------------------------------------------------------------------------------------
    # ---- experiments: change something, then compare ----------------------------------------------------------------
    def _watch_setup(self) -> None:
        """A brake bias (or ABS, TC, engine map) change that has settled starts a before/after comparison."""
        s = self.sample
        if s is None or self.replaying or not hasattr(self.reader, "setup"):
            return
        from .experiments import Experiment
        done = s.lap - 1                                        # laps completed: the lap being driven now is not judged
        for name, old, new in self.setup_watch.update(self.reader.setup(), time.time()):
            if self.experiment is not None and self.experiment.name == name:
                self.experiment.change(new, done)
            else:
                self.experiment = Experiment(name, old, new, done)
                self._radio(self.engineer.experiment_started(self.experiment.label))

    def _experiment_tick(self, completed: int) -> None:
        """After each lap: judge a finished experiment, else suggest one, else say when to shift. Reads the session file in
        the background so the loop never waits on it."""
        rec = self.recorder
        if self.engineer is None or self.replaying or rec is None or rec.path is None or self._exp_busy:
            return
        self._exp_busy = True
        path = rec.path
        threading.Thread(target=self._experiment_work, args=(completed, path), daemon=True).start()

    def _experiment_work(self, completed: int, path) -> None:
        try:
            from . import experiments as EX, tips
            exp = self.experiment
            if exp is not None:
                if exp.ready(completed):
                    frames = tips._frames(path)
                    r = EX.evaluate(frames, exp)
                    if r["ok"]:
                        v = EX.verdict(r)
                        m = self.engineer.experiment_result(exp.label, r, v)
                        self.last_experiment = m.text
                        self._radio(m)
                        self.experiment = None
                    elif completed >= exp.lap + EX.TEST_LAPS + 3:    # still no clean laps to judge: give up quietly
                        self.experiment = None
                return
            if completed < 4:
                return
            frames = tips._frames(path)
            have_bias = (self.setup_watch.stable.get("brake_bias", -1) or -1) >= 0
            base = tips.calibration(frames)
            if have_bias and base is not None and completed - self._prop_lap >= 6:
                recent = [f for f in frames if f.get("completed_laps", 0) >= completed - 4]
                direction = tips.bias_direction(tips.lock_counts(recent, base))
                if direction:
                    self._prop_lap = completed
                    self._radio(self.engineer.propose(direction))
                    return
            if completed % 3 == 0:
                for p in tips.shift_plan(frames):
                    if abs(p["your_rpm"] - p["best_rpm"]) >= 400 and completed - self._shift_said.get(p["gear"], -99) >= 8:
                        self._shift_said[p["gear"]] = completed
                        self._radio(self.engineer.shift_advice(p["gear"], p["best_rpm"], p["your_rpm"]))
                        break
        except Exception as e:                                       # advice is optional: never let it hurt the session
            self.error = f"experiments: {type(e).__name__}: {e}"
        finally:
            self._exp_busy = False

    def worn_pct(self, snap) -> Optional[list]:
        """Each tyre's wear as percent worn (the game counts down from 1; the watcher works out the direction)."""
        if not snap or not snap.get("wear"):
            return None
        if self.watch is not None:
            self.watch.tyre_wear(snap)
            down = self.watch._wear_mode
        else:
            down = sum(snap["wear"]) / 4 > 0.5
        return [round((1 - x if down else x) * 100, 1) for x in snap["wear"]]

    def _note_wear(self, lap: int) -> None:
        worn = self.worn_pct(self.snap)
        if worn:
            self.wear_hist.append((lap, sum(worn) / len(worn)))
            del self.wear_hist[:-40]

    def wear_rate(self) -> Optional[float]:
        """Percent of tyre wear per lap over the current set (the history restarts when the tyres are changed)."""
        h = self.wear_hist
        start = 0
        for i in range(1, len(h)):
            if h[i][1] < h[i - 1][1] - 3:
                start = i
        h = h[start:]
        if len(h) < 2 or h[-1][0] <= h[0][0]:
            return None
        return (h[-1][1] - h[0][1]) / (h[-1][0] - h[0][0])

    @staticmethod
    def _car_ahead(snap) -> Optional[dict]:
        cars = sorted(snap["cars"], key=lambda c: c["place"])
        mine = next((i for i, c in enumerate(cars) if c["me"]), None)
        return cars[mine - 1] if mine else None

    def _neighbours(self) -> tuple:
        """The drivers directly ahead of and behind the player, from the last snapshot ((None, None) if unknown)."""
        snap = self.snap
        if not snap:
            return None, None
        cars = sorted(snap["cars"], key=lambda c: c["place"])
        mine = next((i for i, c in enumerate(cars) if c["me"]), None)
        if mine is None:
            return None, None
        pick = lambda k: cars[k]["name"] if 0 <= k < len(cars) else None
        return pick(mine - 1), pick(mine + 1)

    def _battle_call(self, lap: int):
        """After a lap in a race: where the car ahead is taking time (or where you are quicker), said every other lap."""
        g, snap = self.gaps, self.snap
        if (self.engineer is None or self.replaying or not snap or snap["session"] < 10 or not g or g[1] is None or g[1] > 6
                or not self.coach):
            return None
        sm = self.battle.summary()
        if sm is None or not self.battle.due(lap):
            return None
        from .battle import describe
        tl = self.sample.track_len if self.sample else 0
        where = lambda seg: describe(seg, tl, self.coach.corners) if tl else f"sector {1 + int(3 * seg / 12)}"
        return self.engineer.battle(sm["name"], where(sm["worst"]), sm["loss"], where(sm["best"]), sm["gain"], sm["net"])

    def _trend(self, k: int, secs: float = 12.0) -> Optional[float]:
        """How a gap has changed over the last `secs`: negative = closing, positive = growing; None without two readings."""
        h = [x for x in self.gap_hist if x[k] is not None]
        if len(h) < 2:
            return None
        t_now, now = h[-1][0], h[-1][k]
        old = next((x for x in h if t_now - x[0] <= secs), h[0])
        return None if t_now - old[0] < 3 else round(now - old[k], 2)

    def race_view(self) -> dict:
        """What a second screen needs during a race, in one small dict (the laptop page /race)."""
        with self.lock:
            s, snap, g = self.sample, self.snap, self.gaps
            out = {"status": self.status, "error": self.error, "radio": self.said_text[-3:],
                   "coaching": self.coaching, "notes": [n.text for n in list(self.notes)[:2]]}
            if s is None:
                return out
            out.update({"track": s.track, "car": s.car, "lap": s.lap, "speed": round(s.speed), "gear": s.gear,
                        "last_lap": s.last_lap, "best_lap": s.best_lap, "fastest_lap": self.fastest_lap, "invalid": s.invalid,
                        "delta": self.coach.delta(LP.Lap(s.track, s.car, 0.0, "live", self.builder.cur)) if self.coach and self.builder.cur else None,
                        "game_delta": round(s.delta_best, 3), "ref": self.ref_info,
                        "fuel": s.fuel if s.fuel >= 0 else None, "tyre_temp": [round(t) for t in s.tyre_temp] or None})
            out["map"] = {"id": self._map_source(s)[0], "dist": round(s.dist, 1), "cars": trackmap.around(snap)}
            out["focus"] = self.focus_list()
            if self.experiment is not None:
                out["experiment"] = f"Testing {self.experiment.label}: judging the next two clean laps"
            elif self.last_experiment:
                out["experiment"] = self.last_experiment
            if self.sectors_view():
                out["sectors"] = self.sectors_view()
            sv = self.sector_view(s)
            if sv:
                out["sector_delta"] = sv
            if snap:
                me, cars = snap["me"], sorted(snap["cars"], key=lambda c: c["place"])
                mine = next((i for i, c in enumerate(cars) if c["me"]), None)
                near = lambda k: (cars[k]["name"].split("#")[0].strip() if k is not None and 0 <= k < len(cars) else "")
                out.update({"race": snap["session"] >= 10, "place": me["place"], "field": len(cars), "class": me["cls"],
                            "penalties": me["penalties"], "in_pits": me["in_pits"], "laps_done": me["laps"],
                            "blue": me["flag"] == 6, "yellow": any(f > 0 for f in snap["sector_flags"]),
                            "wear": [round(x) for x in self.worn_pct(snap)] if snap.get("wear") else None,
                            "fuel_cap": snap["fuel_cap"]})
                if mine is not None:
                    out["ahead"], out["behind"] = near(mine - 1), near(mine + 1)
                if g:
                    out["gap_ahead"], out["gap_behind"] = g[1], g[2]
                    out["trend_ahead"], out["trend_behind"] = self._trend(1), self._trend(2)
                sm = self.battle.summary()
                if sm and self.sample and self.sample.track_len and self.coach and sm["loss"] >= 0.04:
                    from .battle import describe
                    out["losing_ahead"] = (f"{describe(sm['worst'], self.sample.track_len, self.coach.corners)}: "
                                           f"-{sm['loss']:.2f} s/lap vs {sm['name'].split('#')[0].split(' ')[0]}")
                w = self.watch
                if w is not None:
                    try:
                        use, togo = w.per_lap(), w.laps_to_go(snap)
                    except Exception:
                        use, togo = None, None
                    out["laps_to_go"] = None if togo is None else round(togo, 1)
                    if use and snap["fuel"] >= 0:
                        out["fuel_per_lap"] = round(use, 2)
                        out["fuel_laps"] = round(snap["fuel"] / use, 1)
                if snap["end_t"] > 0:
                    out["time_left"] = max(0.0, round(snap["end_t"] - snap["t"]))
            return out

    def _map_own_lap(self, s) -> Optional[LP.Lap]:
        """A whole lap of the player's with positions for this track and car (the same one from then on, so the drawing
        stays still): the newest driven this session, else the fastest stored."""
        got = self._map_laps.get(self.combo, False)
        if got:
            return got
        for n in sorted(self.recent_laps, reverse=True):
            lap = self.recent_laps[n]
            if LP.key(lap.track) == self.combo[0] and trackmap.has_positions(lap.samples):
                self._map_laps[self.combo] = lap
                return lap
        if got is False:                                     # the disk is only asked once
            try:
                found = LP.best(s.track, s.car, ("live",))
            except (OSError, ValueError, TypeError):
                found = None
            self._map_laps[self.combo] = found if found and trackmap.has_positions(found.samples) else None
        return self._map_laps[self.combo]

    def _map_source(self, s) -> tuple:
        """(id, what is drawn, samples) for the track map, best first: the reference lap's own line, an own whole lap,
        the lap being driven so far. The id changes when the drawing would, so a page fetches it only then."""
        where = f"{self.combo[0]}:{self.combo[1]}" if self.combo else ""
        ref = self.coach.ref if self.coach else None
        if ref is not None and trackmap.has_positions(ref.samples):
            return f"ref:{where}:{ref.source}:{ref.time}", "reference", ref.samples
        ref_t = f":{ref.time}" if ref is not None else ""
        own = self._map_own_lap(s) if self.combo else None
        if own is not None:
            return f"lap:{where}:{own.time}{ref_t}", "lap", own.samples
        cur = self.builder.cur
        if cur and trackmap.has_positions(cur):
            return f"learning:{where}:{self.builder.lap}:{len(cur) // 250}{ref_t}", "learning", cur
        return None, None, []

    def track_map(self) -> dict:
        """The drawing behind the race page's map (fetched when the id in race_view changes): the outline, what it is
        made from and the reference's braking zones. {} until there is something to draw."""
        with self.lock:
            s = self.sample
            if s is None:
                return {}
            mid, source, samples = self._map_source(s)
            if mid is None:
                return {}
            if self._map_view is not None and self._map_view["id"] == mid:
                return self._map_view
            line = trackmap.outline(samples, s.track_len, turn_sign=getattr(self.reader, "TURN_SIGN", 0))
            if line is None:
                return {}
            self._map_view = {"id": mid, "source": source, "track": s.track, **line,
                              "zones": trackmap.braking_zones(self.coach.corners) if self.coach else []}
            return self._map_view

    def _learn_sectors(self, track: str, lap) -> None:
        """Where the sectors end, in metres: the distance the lap had covered at its cumulative sector times."""
        sec = list(lap.sectors or [])
        if track in self.sector_bounds or len(sec) != 3 or not lap.samples:
            return
        pts = [(p[6], p[0]) for p in lap.samples]
        bounds = []
        for t in sec[:2]:
            i = next((k for k, (pt, _) in enumerate(pts) if pt >= t), None)
            if i is None or i == 0:
                return
            (t0, d0), (t1, d1) = pts[i - 1], pts[i]
            bounds.append(d0 + (d1 - d0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0))
        self.sector_bounds[track] = bounds

    def sector_deltas(self, samples: list, track: str, final: float = None) -> list:
        """Per-sector time against the reference (negative = faster = up) for a lap in progress or just done:
        [d1, d2, d3], None for a sector not reached yet. The third needs the finished lap's time (`final`)."""
        bounds = self.sector_bounds.get(track)
        if not self.coach or not bounds or len(samples) < 20:
            return []
        rt = self.coach.rt
        cum, out = [], []
        for b in bounds:
            if samples[-1][0] < b:
                break
            i = next(k for k, p in enumerate(samples) if p[0] >= b)
            cum.append(samples[i][6] - self.coach.ref_elapsed(samples[i][0]))
        if final is not None:
            cum.append(final - (self.coach.ref.time if self.coach.ref.time else rt.elapsed(rt.length)))
        prev = 0.0
        for c in cum:
            out.append(round(c - prev, 3))
            prev = c
        return out + [None] * (3 - len(out))

    def sector_view(self, s) -> Optional[dict]:
        """Up or down in each sector against the reference: this lap so far, and the last lap."""
        bounds = self.sector_bounds.get(s.track)
        if not bounds or not self.coach:
            return None
        cur = self.builder.cur
        done = self.sector_deltas(cur, s.track)
        live = sum(1 for x in done if x is not None)
        out = {"now": done or [None, None, None], "active": min(live, 2), "last": self.last_sector_delta or None}
        if done and live < 3 and len(cur) > 20:                  # the running part of the sector in progress
            start = bounds[live - 1] if live else 0.0
            i0 = next((k for k, p in enumerate(cur) if p[0] >= start), 0)
            out["running"] = round((cur[-1][6] - self.coach.ref_elapsed(cur[-1][0]))
                                   - (cur[i0][6] - self.coach.ref_elapsed(cur[i0][0])), 3)
        return out

    def sectors_view(self) -> list:
        return [round(x, 3) for x in self.last_sectors] if self.last_sectors else []

    def state(self, behind: float = 250.0, ahead: float = 450.0, step: float = 4.0) -> dict:
        with self.lock:
            s = self.sample
            out = {"status": self.status, "error": self.error, "coaching": self.coaching, "ref": self.ref_info, "radio": self.said_text[-4:],
                   "radio_errors": (self.radio.errors[-3:] if self.radio is not None else []),
                   "notes": [n.__dict__ for n in list(self.notes)[:4]], "saved_laps": len(self.saved)}
            if s is None:
                return out
            if self.ffb.stats():
                out["ffb"] = self.ffb.stats()
            out.update({"track": s.track, "car": s.car, "lap": s.lap, "dist": round(s.dist, 1),
                        "speed": round(s.speed), "gear": s.gear, "brake": round(s.brake), "throttle": round(s.throttle),
                        "track_len": s.track_len, "game_delta": round(s.delta_best, 3)})
            t_now = s.t
            out["rolling"] = [[round(t - t_now, 2), round(b), round(th), round(v)] for t, b, th, v in list(self.rolling)[::2]]
            cur = self.builder.cur
            if self.coach and cur:
                lap = LP.Lap(s.track, s.car, 0.0, "live", cur)
                out["delta"] = round(self.coach.delta(lap) or 0.0, 3)
                rt = self.coach.rt
                lo, hi = s.dist - behind, s.dist + ahead
                ref_pts, k = [], lo
                while k <= hi:
                    d = k % s.track_len if s.track_len else k
                    p = rt.at(d)
                    ref_pts.append([round(k - s.dist, 1), round(p[2]), round(p[3]), round(p[1])])
                    k += step
                out["ref_window"] = ref_pts
                mine = [[round(p[0] - s.dist, 1), round(p[2]), round(p[3]), round(p[1])] for p in cur
                        if lo <= p[0] <= s.dist][::3]
                out["cur_window"] = mine
            return out
