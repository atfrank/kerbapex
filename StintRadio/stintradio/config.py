"""Settings, persisted as JSON in %APPDATA%/StintRadio."""
from __future__ import annotations

import json
import os
from pathlib import Path
from pydantic import BaseModel, Field


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "StintRadio"
    p.mkdir(parents=True, exist_ok=True)
    return p


class VoiceSettings(BaseModel):
    """The ElevenLabs voice the engineer speaks with. The API key is read only from ELEVENLABS_API_KEY in the
    environment; it is never part of the settings and never saved or logged."""
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"    # the voice in ElevenLabs' own examples
    elevenlabs_model: str = "eleven_multilingual_v2"     # for the fixed countdown phrases


class LiveSettings(BaseModel):
    """The race engineer and corner coach while driving (page at /; pin it in the Steam overlay over a fullscreen game)."""
    game: str = "auto"                 # auto (whichever installed sim is running) | raceroom | lmu | demo | the id of any sim adapter you added
    replay_session: str = ""           # game "replay": the recorded session (sessions\*.sqlite) the engineer replays while a game replay plays (used by Studio)
    learn: bool = True                 # False: nothing this run sees is saved (laps, corner history, memory); used for video runs
    game_dir: str = ""                 # Le Mans Ultimate install folder; blank = find it in the Steam libraries
    reference: str = "leader"          # leader: in races, the time to beat is the fastest lap anyone has set (RaceRoom), or the fastest recorded lap of your class leader (LMU) |
                                       # best: always your own best lap (or a Coach Dave lap)
    style: str = "engineer"            # engineer: race-engineer radio (lap debrief, one focus corner, race gaps), on the
                                       # straights only | countdown: a countdown before every braking point
    countdown: str = "focus"           # engineer style: focus (only the corner to work on) | all | off
    talk: str = "normal"               # how much the engineer says unprompted: quiet (urgent calls only) | normal | chatty
    corner_guide: int = 3              # engineer style: name and describe every corner before it ("Turn three, fast right.") for this many laps
                                       # once a reference lap exists, for a driver who does not know the track yet; 0 = off
    briefing: bool = True              # engineer style: before each session (practice, qualifying, race) a short briefing from your earlier
                                       # runs here: the lap to beat and the corners to work on, said as advice for laps not yet driven
    coach_laps: int = 5                # engineer style: full coaching for this many laps, then only what is needed (a lap time
                                       # that changed, a big loss, a habit still there) plus a call when a corner is nailed
    driver_name: str = ""            # what the engineer calls you ("Alex, try to brake a bit later"); blank = your name in the game (RaceRoom)
    throttle_cue: str = "focus"       # "throttle" where the reference gets back on the power: focus (the corner to work on and the
                                       # guide laps) | all | off
    ffb_profile: str = ""            # the RaceRoom wheel profile (ControlSet\\NAME.rcs) whose FFB Strength may be changed;
                                       # blank = the only profile with a real FFB device
    record: bool = True                # RaceRoom: write the whole live data stream to a session file (%APPDATA%\StintRadio\sessions)
    record_hz: float = 10.0            # ... this many rows a second
    ffb_advice: bool = True            # RaceRoom: say if the force feedback strength looks too high (clipping) or too low
    ask_tools: bool = True             # spoken questions: Claude may query the recorded data stream and saved laps (slower, ~10-25 s)
    ptt: bool = False                  # push-to-talk: hold the button, ask the engineer something, hear the answer
    ptt_button: str = "rctrl"          # a key (f9, rctrl, space...) or a wheel button (joy1:5 = device 1, button 5)
    ptt_mode: str = "toggle"           # toggle: press once to start asking, press again to stop (hands stay on the wheel) | hold
    ptt_max_s: float = 12.0            # toggle mode stops by itself after this many seconds
    ptt_beep: bool = True              # a short tone when recording starts (high) and stops (low)
    ptt_mic: str = ""                  # microphone name as Windows lists it; blank = the first one found
    ptt_language: str = ""             # spoken language code for the question (en, nl); blank = detect
    radio_effect: bool = True          # team-radio sound on the engineer's calls
    engineer_model: str = "eleven_multilingual_v2"   # ElevenLabs model for the engineer's sentences (truest to a cloned voice)
    voice: bool = True                 # say "brake in four, three, two, one, brake, second gear" before each braking point
    countdown_from: int = 4            # seconds before the reference's braking point the countdown starts (1..6)
    speak_gear: bool = True            # after "brake", the gear the reference lap used in the corner
    short_shift: bool = True           # "short shift" where the reference changes up early out of a corner
    voice_engine: str = "windows"      # windows (offline) | elevenlabs (voice.elevenlabs_voice_id; phrases are made once
                                       # with ELEVENLABS_API_KEY and cached)
    lan: bool = False                  # let other devices on the home network (a laptop) open the read-only race page /race
    radio_engine: str = ""             # voice for the engineer's sentences: chatterbox (free, on this PC, cloned from your
                                       # countdown words) | elevenlabs | windows; blank = the same as voice_engine
    chatterbox_voice: str = ""         # a reference WAV of your voice for Chatterbox; blank = made from the countdown clips
    chatterbox_threads: int = 4        # CPU threads Chatterbox may use (low, so the game keeps its frames)
    voice_rate: int = 3                # Windows voice speed (-10..10)
    voice_boost_db: float = 12.0       # how much the cues are compressed and lifted (louder over the engine; 0 = as spoken)
    voice_drive_db: float = 6.0        # a second stage on every cue: hard compression and soft clipping (about 5 dB louder; 0 = off)
    countdown_variety: bool = True     # vary the countdowns (full, short, just "brake") and the reminders instead of the same every lap


class Settings(BaseModel):
    live: LiveSettings = Field(default_factory=LiveSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    port: int = 6420                   # next to DriverDirector (6410); LMU's own REST API is 6397

    @staticmethod
    def _current() -> Path:
        return data_dir() / "settings.json"

    def save(self) -> None:
        if os.environ.get("KERBAPEX_VIDEO"):          # a video run uses its own settings and never writes the user's
            return
        self._current().write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Settings":
        """Last-used settings; on the very first run, the live and voice settings carried over from DriverDirector
        (where this coach used to live), else defaults."""
        try:
            return cls.model_validate_json(cls._current().read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            try:                                          # say so: settings quietly falling back to defaults changes the voice
                with open(data_dir() / "stintradio.log", "a", encoding="utf-8") as log:
                    log.write(f"settings.json could not be read ({type(e).__name__}: {str(e)[:200]}); using the DriverDirector or default settings\n")
            except OSError:
                pass
        s = cls.from_driverdirector() or cls()
        try:
            s.save()
        except OSError:
            pass
        return s

    @classmethod
    def from_driverdirector(cls, path: Path | None = None) -> "Settings | None":
        if path is None:
            path = Path(os.environ.get("APPDATA") or str(Path.home() / ".config")) / "DriverDirector" / "settings.json"
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        com = d.get("commentary") or {}
        voice = {k: com[k] for k in ("elevenlabs_voice_id", "elevenlabs_model") if com.get(k)}
        try:
            return cls(live=LiveSettings(**(d.get("live") or {})), voice=VoiceSettings(**voice))
        except ValueError:
            return None
