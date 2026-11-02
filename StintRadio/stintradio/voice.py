"""Spoken cues while driving: "brake in four... three... two... one... brake... second gear".

Speech made on the spot would arrive too late, so every phrase is made once as a short WAV with the Windows voice
(offline, no service involved) and cached; a player thread plays them in order and drops any that are already late.
The countdown is in seconds, from the car's distance to the reference lap's braking point and its current speed."""
from __future__ import annotations

import queue
import random
import urllib.parse
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .config import data_dir

NUMBERS = ["zero", "one", "two", "three", "four", "five", "six"]
ORDINALS = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"]


def phrases(countdown_from: int) -> dict[str, str]:
    """clip name -> text."""
    out = {f"brake_in_{n}": f"brake in {NUMBERS[n]}" for n in range(1, 7)}
    out.update({f"n{n}": NUMBERS[n] for n in range(1, 7)})
    out["brake"] = "brake"
    out["short_shift"] = "short shift"
    out["throttle"] = "throttle"
    out.update({f"gear{g}": f"{ORDINALS[g]} gear" for g in range(1, 9)})
    return out


def clip_dir(rate: int, boost: float = 0.0) -> Path:
    return data_dir() / "live_voice" / f"windows_rate{rate}_boost{boost:g}"


def make_clips(rate: int = 3, boost: float = 12.0, run=subprocess.run) -> Path:
    """Make any missing clips with the Windows voice (System.Speech), all in one PowerShell call, then trim and boost
    them (compressed and lifted close to full scale, so they carry over the engine)."""
    d = clip_dir(rate, boost)
    d.mkdir(parents=True, exist_ok=True)
    todo = {k: v for k, v in phrases(6).items() if not (d / f"{k}.wav").is_file()}
    if todo:
        lines = ["Add-Type -AssemblyName System.Speech", "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
                 f"$s.Rate = {int(rate)}"]
        for k, text in todo.items():
            p = str(d / f"{k}.wav").replace("'", "''")
            lines += [f"$s.SetOutputToWaveFile('{p}')", f"$s.Speak('{text}')"]
        lines.append("$s.SetOutputToNull()")
        run(["powershell", "-NoProfile", "-Command", "; ".join(lines)], check=True, capture_output=True,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        for k in todo:                               # the Windows voice pads each phrase; every 0.1 s counts here
            _trim(d / f"{k}.wav", run, boost)
    return d


def eleven_dir(voice_id: str, boost: float) -> Path:
    return data_dir() / "live_voice" / f"eleven_{voice_id}_boost{boost:g}"


def make_clips_elevenlabs(cfg, boost: float = 12.0, post=None, run=subprocess.run) -> Path:
    """The same phrases in the user's ElevenLabs voice (commentary.elevenlabs_voice_id), made once and cached. The
    phrases (short fixed words, nothing about the user) are sent to ElevenLabs; the API key comes from the environment."""
    from driverdirector import tts
    d = eleven_dir(cfg.elevenlabs_voice_id, boost)
    d.mkdir(parents=True, exist_ok=True)
    todo = {k: v for k, v in phrases(6).items() if not (d / f"{k}.wav").is_file()}
    if not todo:
        return d
    key = tts.cloud_key("elevenlabs")
    post = post or tts._post_audio
    for k, text in todo.items():
        spoken = (text[0].upper() + text[1:]) + ("!" if k in ("brake", "short_shift", "throttle") else ".")
        body = {"text": spoken, "model_id": cfg.elevenlabs_model,
                "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3, "speed": 1.15,
                                   "use_speaker_boost": True}}
        audio = post("https://api.elevenlabs.io/v1/text-to-speech/"
                     f"{urllib.parse.quote(cfg.elevenlabs_voice_id, safe='')}?output_format=mp3_44100_128",
                     {"xi-api-key": key}, body)
        mp3 = d / f"{k}.mp3"
        mp3.write_bytes(audio)
        _trim(mp3, run, boost, out=d / f"{k}.wav")
        mp3.unlink(missing_ok=True)
    return d


def cached_eleven(boost: float, prefer: str = "") -> Optional[Path]:
    """A complete set of ElevenLabs cue clips that is already on disk (the configured voice's if there, else the newest),
    for when new ones cannot be made. None if there is none."""
    root = data_dir() / "live_voice"
    need = ("brake.wav", "brake_in_1.wav", "n1.wav", "gear1.wav", "short_shift.wav")
    dirs = [d for d in root.glob(f"eleven_*_boost{boost:g}") if all((d / n).is_file() for n in need)]
    if not dirs:
        return None
    mine = [d for d in dirs if d.name == eleven_dir(prefer, boost).name]
    return mine[0] if mine else max(dirs, key=lambda d: d.stat().st_mtime)


def silent_folder() -> Path:
    """An empty cue folder: the player skips clips that are not there, so the cues stay quiet."""
    d = data_dir() / "live_voice" / "silent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_clips_chatterbox(cb, boost: float = 12.0, run=subprocess.run) -> Path:
    """The same phrases spoken by Chatterbox (`radio.ChatterboxVoice`, free, on this PC), made once and cached per reference
    voice, so the cues and the engineer's sentences are one voice."""
    tag = cb.voice.stem if cb.voice else "default"
    d = data_dir() / "live_voice" / f"chatterbox_{tag}_boost{boost:g}"
    d.mkdir(parents=True, exist_ok=True)
    for k, text in phrases(6).items():
        if (d / f"{k}.wav").is_file():
            continue
        spoken = (text[0].upper() + text[1:]) + ("!" if k in ("brake", "short_shift", "throttle") else ".")
        raw = d / f"{k}.raw.wav"
        cb.speak(spoken, raw, exaggeration=0.6)
        _trim(raw, run, boost, out=d / f"{k}.wav")
        raw.unlink(missing_ok=True)
    return d


def _trim(wav: Path, run=subprocess.run, boost: float = 0.0, out: Optional[Path] = None) -> None:
    from driverdirector.compose import ffmpeg_bin
    import wave as _wave
    tmp = wav.with_suffix(".trim.wav")
    flags = 0x08000000 if sys.platform == "win32" else 0
    # silence is only ever cut from the very start and (reversed) the very end: a pause inside a word ("o-ne") stays
    lead = "silenceremove=start_periods=1:start_threshold={t}dB:start_silence=0.02"
    ends = f"{lead.format(t=-45)},areverse,{lead.format(t=-45)},areverse"
    loud = (f",apad=pad_dur=0.4,loudnorm=I={-24 + min(boost, 18):g}:TP=-0.5:LRA=4,volume={max(0.0, boost - 6):g}dB,"
            f"alimiter=limit=0.94:attack=0.5:release=15:level=false,areverse,{lead.format(t=-50)},areverse"
            if boost > 0 else "")
    try:
        run([ffmpeg_bin(), "-loglevel", "error", "-y", "-i", str(wav), "-af", ends + loud + ",aresample=44100",
             str(tmp)], check=True, capture_output=True, creationflags=flags)
        with _wave.open(str(tmp)) as w:
            if w.getnframes() / max(1, w.getframerate()) < 0.15:
                raise OSError("trimming removed the speech")
        tmp.replace(out or wav)
    except (OSError, subprocess.CalledProcessError):
        tmp.unlink(missing_ok=True)                  # keep the clip whole rather than lose it
        if out is not None and not out.is_file():
            run([ffmpeg_bin(), "-loglevel", "error", "-y", "-i", str(wav), str(out)], check=True,
                capture_output=True, creationflags=flags)


def master(src: Path, drive: float = 6.0, run=subprocess.run) -> Path:
    """A louder copy of a cue (next to it, in a folder named for the drive): compressed hard, pushed into a soft clip and
    limited, which lifts the average level some 5 dB over what level-matching alone reaches and keeps the 2-3 kHz speech
    band forward, so it carries over the engine. Made once; clips are never changed. 0 = use the clip as it is."""
    if drive <= 0:
        return src
    from driverdirector.compose import ffmpeg_bin
    out = src.parent / f"master{drive:g}" / src.name
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    chain = ("highpass=f=200,equalizer=f=2600:t=q:w=0.9:g=4,acompressor=threshold=-26dB:ratio=8:attack=1:release=40:makeup=8,"
             f"volume={drive:g}dB,asoftclip=type=tanh,alimiter=limit=0.97:level=false,volume=-1.2dB")
    tmp = out.with_suffix(".tmp.wav")
    try:
        run([ffmpeg_bin(), "-loglevel", "error", "-y", "-i", str(src), "-af", chain, "-ar", "44100", str(tmp)],
            check=True, capture_output=True, creationflags=0x08000000 if sys.platform == "win32" else 0)
        tmp.replace(out)
        return out
    except (OSError, subprocess.CalledProcessError):
        tmp.unlink(missing_ok=True)
        return src


def master_folder(folder: Path, drive: float = 6.0, run=subprocess.run) -> Path:
    """Every cue in a folder, mastered (see `master`); the folder the player should use."""
    if drive <= 0:
        return folder
    for p in folder.glob("*.wav"):
        master(p, drive, run)
    return folder / f"master{drive:g}"


def _stop_sync() -> None:
    import winsound
    winsound.PlaySound(None, winsound.SND_PURGE)


def _play_sync(path: str) -> None:
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)


def play_log(line: str) -> None:
    """One line in the StintRadio data folder's audio.log (wall-clock time first): what was played, so a voice that is heard
    can be matched to the sound that made it. Best effort; the file is cut back when it grows past 1 MB."""
    try:
        from .config import data_dir
        p = data_dir() / "audio.log"
        if p.exists() and p.stat().st_size > 1_000_000:
            p.write_text("".join(p.read_text(encoding="utf-8", errors="replace").splitlines(True)[-2000:]), encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {line}\n")
    except OSError:
        pass


class Speaker:
    """The one audio channel (Windows plays one sound at a time). Countdown clips (`say`) go out at once, in order, and
    are dropped when `stale` seconds late. Radio calls (`say_file`) wait until `gate()` says it is quiet (a straight)
    and are dropped when they expire. The most urgent ready item plays first."""

    def __init__(self, folder: Path, play: Callable[[str], None] = _play_sync, stale: float = 0.6,
                 now: Callable[[], float] = time.monotonic, stop: Callable[[], None] = _stop_sync):
        self.folder, self.play, self.stale, self.now, self.stop_play = folder, play, stale, now, stop
        self.epoch = 0               # counts interruptions: a call made before one is dropped when it arrives after it
        self.items: list[dict] = []
        self.lock = threading.Lock()
        self.said: list[str] = []
        self._n = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _add(self, path: Path, name: str, prio: int, expires: float, gate) -> None:
        with self.lock:
            self._n += 1
            self.items.append({"path": path, "name": name, "prio": prio, "until": self.now() + expires,
                               "gate": gate, "n": self._n, "secs": _secs(path) if gate is not None else 0.0})

    def say(self, clip: str) -> None:
        self._add(self.folder / f"{clip}.wav", clip, -1, self.stale, None)

    def say_file(self, path: Path, prio: int = 1, expires: float = 20.0, gate=None, name: str = "") -> None:
        self._add(Path(path), name or Path(path).stem, prio, expires, gate)

    def interrupt(self) -> None:
        """Cut off what is being said and forget everything waiting (the driver is about to speak)."""
        with self.lock:
            self.epoch += 1
            self.items.clear()
        try:
            self.stop_play()
        except Exception:
            pass

    def _next(self) -> Optional[dict]:
        with self.lock:
            now = self.now()
            self.items = [i for i in self.items if i["until"] >= now]
            ready = [i for i in self.items if i["gate"] is None or _safe(i["gate"], i["secs"])]
            if not ready:
                return None
            pick = min(ready, key=lambda i: (i["prio"], i["n"]))
            self.items.remove(pick)
            return pick

    def _run(self) -> None:
        while True:
            item = self._next()
            if item is None:
                time.sleep(0.03)
                continue
            if item["path"].is_file():
                self.said.append(item["name"])
                play_log(f"play {item['name']} ({item['path'].parent.name}/{item['path'].name}, prio {item['prio']})")
                try:
                    self.play(str(item["path"]))
                except Exception as e:
                    play_log(f"play FAILED {item['name']}: {type(e).__name__}")
                play_log(f"done {item['name']}")


def _safe(gate, secs: float = 0.0) -> bool:
    """Ask the gate whether a clip of `secs` seconds can be said now."""
    try:
        return bool(gate(secs))
    except TypeError:
        return bool(gate())
    except Exception:
        return False


def _secs(path: Path) -> float:
    import wave
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / max(1, w.getframerate())
    except (OSError, EOFError, wave.Error):
        return 2.5


class BrakeCues:
    """Decides when to say what. `due(...)` is called every reading with the next corners; it returns the clips to say
    now (each at most once per pass of a corner)."""

    def __init__(self, countdown_from: int = 4, speak_gear: bool = True, lead: float = 0.15, variety: bool = False,
                 seed: Optional[int] = None):
        self.n = max(1, min(6, countdown_from))
        self.speak_gear = speak_gear
        self.lead = lead                      # say each a touch early: the voice takes a moment to start
        self.variety = variety                # a different countdown each time: full, from three or two, or just "brake"
        self.rng = random.Random(seed)
        self.plans: dict = {}                 # corner -> (where this pass's countdown starts, whether to say the gear)
        self.done: set = set()

    def due(self, dist: float, speed_kmh: float, track_len: float, corners) -> list[str]:
        out: list[str] = []
        v = speed_kmh / 3.6
        for c in corners:
            if c.brake is None:
                continue
            ahead = (c.brake - dist) % track_len if track_len else c.brake - dist
            since_apex = (dist - c.apex) % track_len if track_len else dist - c.apex
            if 0 <= since_apex < 120:                   # through the corner: ready to count it down next lap
                self.done = {k for k in self.done if k[0] != c.n}
                self.plans.pop(c.n, None)
                continue
            if ahead < 0:
                continue
            if v < 60 / 3.6:                    # too slow for a countdown to mean anything (pits, a spin)
                continue
            eta = ahead / v
            start, gear_on = self.n, True
            if self.variety:
                if c.n not in self.plans:
                    self.plans[c.n] = (self.rng.choice([self.n, self.n, min(self.n, 3), min(self.n, 2), 0]),
                                       self.rng.random() < 0.6)
                start, gear_on = self.plans[c.n]
            for k in range(self.n, -1, -1):
                key = (c.n, k)
                if k > start or key in self.done or eta > k + self.lead or eta < k - 0.7:
                    continue
                self.done.add(key)
                if k == 0:
                    out.append("brake")
                    if self.speak_gear and gear_on and c.gear:
                        out.append(f"gear{c.gear}")
                elif k == start:
                    out.append(f"brake_in_{k}")
                else:
                    out.append(f"n{k}")
        return out

    def throttle_due(self, dist: float, speed_kmh: float, throttle: float, track_len: float, corners) -> list[str]:
        """"throttle" a moment before the point where the reference gets back on the power after the apex, once per pass,
        if the driver is not on it yet."""
        out: list[str] = []
        v = max(speed_kmh / 3.6, 1.0)
        for c in corners:
            if c.throttle_at is None:
                continue
            ahead = (c.throttle_at - dist) % track_len if track_len else c.throttle_at - dist
            key = ("thr", c.n)
            if track_len and ahead > track_len - 150:           # just passed it: ready for the next lap
                self.done.discard(key)
                continue
            if key in self.done:
                continue
            if ahead / v <= self.lead + 0.1:
                self.done.add(key)
                if throttle < 30:
                    out.append("throttle")
        return out

    def shift_due(self, dist: float, speed_kmh: float, gear: int, track_len: float, shifts) -> list[str]:
        """"short shift" just before the point where the reference changes up early (for traction), if the car is
        still in the gear the reference shifts out of. Once per pass."""
        out: list[str] = []
        v = max(speed_kmh / 3.6, 1.0)
        for sh in shifts:
            ahead = (sh.dist - dist) % track_len if track_len else sh.dist - dist
            key = ("shift", round(sh.dist))
            if track_len and ahead > track_len - 200:          # just passed it: ready for the next lap
                self.done.discard(key)
                continue
            if key in self.done or gear != sh.from_gear:
                continue
            if ahead / v <= self.lead + 0.25:                   # the voice takes a moment; say it a touch early
                self.done.add(key)
                out.append("short_shift")
        return out
