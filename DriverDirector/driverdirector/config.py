"""Settings and templates (saved presets), persisted as JSON in %APPDATA%/DriverDirector."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from pydantic import BaseModel, Field, model_validator


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "DriverDirector"
    p.mkdir(parents=True, exist_ok=True)
    return p


class CameraSettings(BaseModel):
    # Weights 0..4 (Off .. Frequent), mirroring LMU Director's camera-group weighting.
    weights: dict[str, int] = {"trackside": 2, "onboard": 1, "driving": 3, "tv": 1, "rear": 0}
    angle_duration: float = 9.0        # seconds a camera holds before rotating
    min_shot: float = 4.0
    # Camera names accepted by /rest/replay/CameraController/setCameraByName (read from the game
    # executable and verified live). Each group rotates through its list.
    cameras: dict[str, list[str]] = {
        "trackside": ["CycleGroup1", "CycleGroup2", "CycleGroup3", "CycleGroup4"],
        "onboard": ["Dashcam", "RoofCam", "FrontBumper", "CockpitSide"],
        "driving": ["COCKPIT", "BonnetCamera", "NoseCamera"],
        "tv": ["TracksideCycle"],
        # looking backwards: only used while a car is right behind the focus driver (see planner.context_weights)
        "rear": ["RearBumper", "ReverseRoofCamera"],
    }
    # Every one of these names is listed in the game's own UI (Bin/UI.zip, the camera menu).


class DetectSettings(BaseModel):
    battle_gap_s: float = 1.0
    battle_min_s: float = 8.0
    battle_merge_s: float = 3.0
    same_class_only: bool = True
    crash_decel: float = 18.0          # m/s^2 sudden deceleration => contact/crash
    stopped_speed: float = 5.0         # m/s considered stopped on track
    min_incident_score: float = 3.0


class DirectSettings(BaseModel):
    focus_driver: str = ""
    pace: str = "patient"              # patient | urgent
    max_inlays: int = 8
    max_pass_inlays: int = 4           # extra boxes for passes the focus driver made or suffered
    inlay_max_s: float = 12.0
    inlay_min_gap_s: float = 25.0
    inlay_preroll: float = 3.0
    inlay_postroll: float = 3.0
    live_delta: bool = True            # show the live gap to the reference lap and the corner name (needs the telemetry file)
    title_card_s: float = 8.0          # seconds the title card (event, track, driver) stays up at the start; 0 = none
    conditions: str = ""               # said in the opening, e.g. "a dry night race" (the results file has no weather)
    mistake_replays: int = 2           # slow-motion replays of the driver's own worst mistakes, shown right after each one
    slow_mo_inlays: int = 0            # how many of the highest-scoring incident/pass boxes play in slow motion
    slow_mo_speed: float = 0.5         # playback speed of those boxes (0.25 to 0.9)
    incident_preroll_single: float = 6.0   # a lone car: it is out of control some seconds before it hits anything
    incident_preroll_multi: float = 4.0    # several cars: the first contact is the start
    inlay_size: float = 0.36           # width as a fraction of the video width
    inlay_border_pct: float = 0.30     # border thickness, % of the video width (0.3 = ~10 px at 3440)
    inlay_border_color: str = "white"  # colour name or #RRGGBB
    inlay_corner: str = "top_right"    # top_left | top_right | bottom_left | bottom_right
    play_latency: float = 0.3          # seconds between the play command and the replay moving
    overlay_delay_s: float = 0.0       # hold the leaderboard/overlay back by this much behind the picture (tuning)
    exit_replay_after_record: bool = True   # leave the replay when recording ends, to free RAM for the voice
    hide_game_ui: str = "all"          # hide LMU's replay UI/HUD (our overlay replaces it): all | clips | off
    require_hidden_ui: bool = True     # refuse to start recording if the UI cannot be hidden (else only warn)
    telemetry: bool = True             # keep the game's own speedo (gear, speed, rpm, pedals) on in the main pass
    tower_ahead: int = 5               # leaderboard rows shown ahead of / behind the focus driver
    tower_behind: int = 5
    pre_roll_s: float = 30.0           # seconds of grid/formation lap shown before the green flag (if the replay has them)
    start_coverage_s: float = 45.0
    finish_coverage_s: float = 40.0
    driving_feedback: bool = True      # narrate the focus driver's own mistakes (needs the game's telemetry file)
    mistake_min_loss_s: float = 0.25   # a stretch must cost at least this much against the reference lap
    mistake_max: int = 6               # at most this many mentions per video (the worst ones)
    reference_lap: bool = False        # show the focus driver's best qualifying lap in a box during the race's first lap
    t_start: float | None = None       # restrict to a replay range (session seconds)
    t_end: float | None = None


class ObsSettings(BaseModel):
    url: str = "ws://127.0.0.1:4455"
    password: str = ""
    output_dir: str = ""
    main_scene: str = "DriverDirector"
    exe_path: str = ""                 # blank = auto-detect OBS Studio
    launch_scene: str = "DriverDirector"   # scene OBS opens on when launched from here ("" = last used)
    capture_source: str = "LMU Director Capture"   # game-capture source inside the scene
    overlay_source: str = "DriverDirector Overlay"   # the leaderboard/name-card browser source (hidden in a cinematic film)
    crop_top: float = 0.0              # optional: fractions of the capture to hide (off by default)
    crop_bottom: float = 0.0


class CommentarySettings(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _migrate_voice(cls, d):
        """Older settings stored an edge-tts voice name in `voice`."""
        if isinstance(d, dict) and re.match(r"^[a-z]{2}-[A-Z]{2}-", str(d.get("voice", ""))):
            d = dict(d, edge_voice=d["voice"], voice="british-female-normal")
        return d

    enabled: bool = True
    backend: str = "cli"               # cli: your Claude Code login (no API key) | api: ANTHROPIC_API_KEY
    model: str = "claude-sonnet-5-5"   # "" = the CLI/API default
    cli_path: str = ""                 # blank = auto-detect the Claude CLI
    tts_engine: str = "auto"           # auto: Chatterbox if installed, else edge | chatterbox | edge | fish | elevenlabs
    # Cloud voices send the commentary TEXT to that service. The API keys are read only from the environment
    # (FISH_API_KEY / ELEVENLABS_API_KEY); they are never part of the settings and never saved or logged.
    fish_voice_id: str = ""            # Fish Audio voice (reference_id); blank = the service's default voice
    fish_model: str = "s2.1-pro"       # s1 | s2-pro | s2.1-pro | s2.1-pro-free (free developer tier)
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"    # the voice in ElevenLabs' own examples; see /api/voices/test
    elevenlabs_model: str = "eleven_multilingual_v2"
    # How each excitement level (normal, excited, overexcited) is voiced: lower stability = more varied and expressive,
    # higher style = more exaggerated, speed above 1 = quicker (ElevenLabs allows up to 1.2).
    elevenlabs_expression: list[dict] = [{"stability": 0.40, "style": 0.25, "speed": 1.05},
                                         {"stability": 0.28, "style": 0.50, "speed": 1.12},
                                         {"stability": 0.20, "style": 0.70, "speed": 1.18}]
    voice: str = "british-female-normal"   # Chatterbox reference voice: clip name, base name (auto), path or empty
    edge_voice: str = "en-GB-RyanNeural"
    exaggeration: float = 0.5          # Chatterbox emotion; excited lines get +0.2, overexcited +0.4
    cfg_weight: float = 0.5
    temperature: float = 0.8
    device: str = "cpu"                # cpu | cuda | auto
    chatterbox_runtime: str = ""       # blank = LMU Director's runtime folder
    style: str = ("live, excited TV motorsport commentary: present tense, vivid and punchy, quick reactions, "
                  "rhetorical questions, replay-style analysis after a mistake, an exclamation mark when the moment "
                  "earns one, plain everyday language, never invents numbers")
    words_per_second: float = 2.6
    # Two commentators: a play-by-play voice (the voice above) and an analyst who explains. It needs a SECOND voice for
    # the engine in use below; without one the commentary stays with a single voice.
    reuse_script: bool = False         # keep a commentary script that was supplied (see /api/commentary/script) instead of
                                       # writing a new one, as long as it still fits the plan (same scenes at the same times)
    commentators: int = 1              # 1 | 2
    voice_b: str = ""                  # Chatterbox reference voice for the analyst
    edge_voice_b: str = ""             # edge-tts voice for the analyst
    fish_voice_id_b: str = ""          # Fish Audio voice for the analyst
    elevenlabs_voice_id_b: str = ""    # ElevenLabs voice for the analyst
    duck_db: float = 18.0              # how far the game sound drops while the commentator speaks
    voice_gain_db: float = 0.0         # extra gain on the commentary in the finished video, on top of the levelling below
    level_voice: bool = True           # compress the commentary and lift it to voice_target_lufs, so it stands clear of the game
    voice_target_lufs: float = -13.0   # loudness of what is spoken (the game's own sound is around -17 on average)


class MusicSettings(BaseModel):
    source: str = "ai"                 # ai: generated locally with MusicGen | elevenlabs: Eleven Music (cloud) | folder | off
    prompt: str = ("energetic modern motorsport broadcast background music, driving electronic rock, "
                   "steady drums, pulsing bass, instrumental, no vocals")
    model: str = "facebook/musicgen-small"
    seconds: float = 30.0              # length of each generated clip (MusicGen's maximum is 30)
    clips: int = 2                     # clips cross-faded into one loop (more = less repetition, slower to make)
    seed: int = 1
    folder: str = ""                   # music folder for source = folder (wav/mp3/flac/ogg/m4a)
    level_db: float = -32.0            # mean level of the music in the finished video (game + voice are ~ -19)
    duck_db: float = 8.0               # how far it drops while the commentator speaks
    # source = elevenlabs: needs a paid ElevenLabs plan and ELEVENLABS_API_KEY in the environment. The prompt above is
    # sent to the service; the track is made once and cached, like the local one.
    elevenlabs_model: str = "music_v2_5"   # music_v1 | music_v2 | music_v2_5
    elevenlabs_seconds: float = 120.0      # length of the track that is looped under the video (3 to 300)


class YouTubeSettings(BaseModel):
    client_secret_path: str = ""       # OAuth client JSON (type "Desktop app") from your Google Cloud project
    privacy: str = "private"           # private | unlisted | public: what a new upload is set to
    title_template: str = "{driver} | {track} {session} | Le Mans Ultimate"
    description_extra: str = ""        # appended to every description
    tags: list[str] = ["Le Mans Ultimate", "LMU", "sim racing", "replay", "race"]
    category_id: str = "20"            # 20 = Gaming
    made_for_kids: bool = False
    synthetic_media_flag: bool = True  # mark the video as containing AI-generated (voice/music) content


class CinematicSettings(BaseModel):
    """A film of the race instead of a broadcast: no commentary, no leaderboard or boxes, long TV-style shots, chosen
    moments played in real slow motion by the game itself, music, letterbox bars and a colour grade."""
    enabled: bool = False
    shot_s: float = 14.0               # average shot length (each varies by +-25%)
    min_shot: float = 8.0
    weights: dict[str, int] = {"tv": 3, "trackside": 4, "driving": 1, "onboard": 1}   # groups of camera.cameras
    cutaways: int = 4                  # big incidents elsewhere in the field the film cuts away to (0 = stay on the driver)
    cutaway_min_score: float = 2.0     # how big an incident must be (event score) to cut away to it
    slow_moments: int = 4              # moments the game plays in slow motion (its own slow speed, real frames)
    slow_before_s: float = 1.5         # replay seconds of slow motion before the key moment
    slow_after_s: float = 2.5          # ... and after it (4 replay seconds = 32 s of film at 1/8 speed)
    slow_rate: float = 0.125           # the game's slow motion runs at 1/8 speed (measured 2026-10-25: 0.117-0.125)
    letterbox: float = 2.76            # picture aspect ratio inside black bars (0 = none; the 21:9 source is 2.39)
    grade: str = "film"                # film: gentle contrast, cooler shadows, warmer highlights, vignette, grain | none
    fade_s: float = 2.0                # fade from and to black at the start and end
    title: bool = True                 # event, track and driver over the opening shot
    title_s: float = 7.0
    music_source: str = "ai"           # ai | elevenlabs | folder | off (independent of the broadcast music setting)
    music_prompt: str = ("cinematic motorsport film score, slow build, wide strings, deep pulsing synth, big drums, "
                         "emotional and epic, instrumental, no vocals")
    music_level_db: float = -20.0      # the music leads ...
    game_level_db: float = -6.0        # ... and the game sound sits under it


class RaceRoomSettings(BaseModel):
    """The RaceRoom Racing Experience mode: a broadcast-style video of a replay (cameras cycled with the replay's own keys,
    no live race data), made without commentary."""
    process: str = "RRRE64.exe"
    driver: str = ""                   # the video is about this driver (a race replay's header lists the whole grid)
    scene: str = "RaceRoom"                       # OBS scene with a game capture of the RaceRoom window
    capture_source: str = "RaceRoom Capture"
    audio_source: str = "LMU Game Audio"          # the desktop-audio source shared from the LMU scene (added to the scene if present)
    # Camera groups (the replay's own keys) and how often each is used, 0 = never .. 4 = often.
    # driving (Insert) is off here: it is only used for the main picture, see main_weights.
    weights: dict[str, int] = {"external": 2, "cycle": 2, "chase": 0, "driving": 0, "onboard": 4}
    # The standard video: cockpit views as the main picture (the driving group's Cockpit and Hood, never its Chase) and,
    # in a box in the top right corner, other views (the replay is played twice; `weights` above is only used with --no-inset).
    inset: bool = True
    main_weights: dict[str, int] = {"driving": 1}
    inset_weights: dict[str, int] = {"external": 2, "cycle": 3}
    inset_size: float = 0.30                      # width of the box as a fraction of the picture
    shot_s: float = 16.0                          # seconds a camera holds (each varies by +-20%)
    min_shot: float = 8.0
    hide_ui: bool = True                          # H hides the replay bar for a clean picture
    title_s: float = 7.0                          # opening title (driver, track, session); 0 = none
    fade_s: float = 1.5                           # fade from and to black
    t_start: float = 0.0                          # replay seconds to skip at the start
    t_end: float | None = None                    # replay second to stop at (None = the whole replay)
    title_template: str = "{driver} | {track} {session} | RaceRoom Racing Experience"
    tags: list[str] = ["RaceRoom", "RaceRoom Racing Experience", "R3E", "sim racing", "replay"]
    # The leaderboard and stats overlay (the LMU overlay page, fed from RaceRoom's telemetry while the replay plays).
    overlay: bool = True
    overlay_source: str = "RaceRoom Overlay"      # OBS browser source added to the scene
    overlay_port: int = 6411                      # local port of its tiny server (6410 is the main DriverDirector server)
    kerbapex_source: str = "KerbApex Overlay"     # OBS browser source showing the KerbApex overlay (track map, delta, next turn, corners to work on)
    kerbapex_size: float = 0.24                   # its width as a fraction of the picture
    kerbapex_corner: str = "bottom-right"         # top-left | top-right | bottom-left | bottom-right
    overlay_alpha: float = 0.15                   # opacity of the overlay's boxes (0 = text only, 1 = solid)
    overlay_zoom: float = 1.25                     # the layout is drawn for 1080p; this scales it up on a 3440x1440 canvas


class Settings(BaseModel):
    raceroom: RaceRoomSettings = Field(default_factory=RaceRoomSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)
    cinematic: CinematicSettings = Field(default_factory=CinematicSettings)
    detect: DetectSettings = Field(default_factory=DetectSettings)
    direct: DirectSettings = Field(default_factory=DirectSettings)
    obs: ObsSettings = Field(default_factory=ObsSettings)
    commentary: CommentarySettings = Field(default_factory=CommentarySettings)
    music: MusicSettings = Field(default_factory=MusicSettings)
    youtube: YouTubeSettings = Field(default_factory=YouTubeSettings)
    overlay_theme: str = "default"
    overlay_port: int = 6410          # distinct from LMU Director (6399, which Windows also reserves here)
    rest_url: str = "http://127.0.0.1:6397"
    steam_app_id: int = 2399420        # Le Mans Ultimate
    compose_chunk_s: float = 600.0     # a video longer than 1.5 x this is made in parts of about this length that can be resumed (0 = never)
    encoder: str = "auto"              # ffmpeg route only: auto = GPU if available, else CPU | cpu
    cleanup: str = "after_finish"       # after_finish: move a finished video's raw recordings aside | off
    cleanup_keep_days: float = 7.0     # moved recordings are really deleted after this many days
    compose_mode: str = "ffmpeg"       # ffmpeg: composite afterwards (default, proven) | obs: OBS shows PiP live

    # ---- persistence -------------------------------------------------
    @staticmethod
    def _current() -> Path:
        return data_dir() / "settings.json"

    def save(self) -> None:
        self._current().write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Settings":
        """Last-used settings (falls back to defaults if missing or from an incompatible version)."""
        try:
            return cls.model_validate_json(cls._current().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()

    @staticmethod
    def _path(name: str) -> Path:
        return data_dir() / "templates" / f"{name}.json"

    def save_template(self, name: str) -> Path:
        p = self._path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        d = self.model_dump()
        d["obs"] = {}  # connection details are deliberately not part of templates
        p.write_text(json.dumps(d, indent=2))
        return p

    @classmethod
    def load_template(cls, name: str) -> "Settings":
        d = json.loads(cls._path(name).read_text())
        d.pop("obs", None)
        return cls(**d)

    @staticmethod
    def list_templates() -> list[str]:
        d = data_dir() / "templates"
        return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
