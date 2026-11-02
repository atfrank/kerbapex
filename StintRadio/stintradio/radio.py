"""The engineer's radio: a sentence is spoken (the user's ElevenLabs voice with its fast model, else the Windows voice),
made to sound like team radio (narrow band, a little compression, a click of noise either side) and cached by its text,
so a repeated call costs nothing the second time. Making it runs in the background; the player decides when to say it."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from .config import data_dir

FLAGS = 0x08000000 if sys.platform == "win32" else 0
RADIO = ("highpass=f=320,lowpass=f=3300,acompressor=threshold=-22dB:ratio=5:attack=2:release=80,"
         "loudnorm=I=-12:TP=-0.8:LRA=5,volume=4dB,alimiter=limit=0.93:level=false")


def radio_dir() -> Path:
    return data_dir() / "live_voice" / "radio"


def _ff() -> str:
    from driverdirector.compose import ffmpeg_bin
    return ffmpeg_bin()


def radio_filter(src: Path, out: Path, effect: bool = True, run=subprocess.run) -> Path:
    """Band-limit and level the speech; with `effect`, a short burst of radio noise before and after it."""
    if effect:
        fc = (f"anoisesrc=d=0.07:c=pink:a=0.25,highpass=f=800,lowpass=f=3000,aresample=44100[n1];"
              f"anoisesrc=d=0.09:c=pink:a=0.2,highpass=f=800,lowpass=f=3000,aresample=44100[n2];"
              f"[0:a]aresample=44100,aformat=channel_layouts=mono,{RADIO}[v];[n1][v][n2]concat=n=3:v=0:a=1[a]")
        cmd = [_ff(), "-loglevel", "error", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[a]", "-ar", "44100",
               str(out)]
    else:
        cmd = [_ff(), "-loglevel", "error", "-y", "-i", str(src), "-af", "loudnorm=I=-12:TP=-0.8,volume=4dB,"
               "alimiter=limit=0.93:level=false", "-ar", "44100", str(out)]
    run(cmd, check=True, capture_output=True, creationflags=FLAGS)
    return out


def chatterbox_python() -> Optional[Path]:
    """The Python of LMU Director's Chatterbox runtime, if it is installed."""
    py = Path(os.environ.get("LOCALAPPDATA", "")) / "LMUTools" / "LMUDirector" / "Runtime" / "venv" / "Scripts" / "python.exe"
    return py if py.is_file() else None


def reference_clip(clips: Path, out: Path, run=subprocess.run) -> Optional[Path]:
    """A reference for the clone: the engineer's own countdown words (made once in the user's ElevenLabs voice, so already
    paid for) joined into one clip of about twelve seconds. None if there are none."""
    if out.is_file():
        return out
    files = sorted(clips.glob("*.wav"))
    if not files:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [_ff(), "-loglevel", "error", "-y"]
    for f in files:
        args += ["-i", str(f)]
    ins = "".join(f"[{i}:a]aresample=24000,aformat=channel_layouts=mono[a{i}];" for i in range(len(files)))
    cat = "".join(f"[a{i}]" for i in range(len(files)))
    run(args + ["-filter_complex", f"{ins}{cat}concat=n={len(files)}:v=0:a=1,atrim=0:14,loudnorm=I=-20[o]", "-map", "[o]",
                str(out)], check=True, capture_output=True, creationflags=FLAGS)
    return out


class ChatterboxVoice:
    """Chatterbox (free, on this PC) as a long-lived worker process: started on the first sentence, one sentence at a
    time. It runs at below-normal priority on a few threads so the game keeps its frames."""
    def __init__(self, voice: Optional[Path] = None, threads: int = 4, device: str = "cpu", timeout: float = 90.0):
        self.voice, self.threads, self.device, self.timeout = voice, threads, device, timeout
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.last_secs = 0.0
        self._replies: "queue.Queue[dict]" = queue.Queue()
        self._n = 0

    def _start(self) -> None:
        py = chatterbox_python()
        if py is None:
            raise OSError("LMU Director's Chatterbox runtime was not found")
        root = py.parents[2]
        env = dict(os.environ, HF_HOME=str(root / "hf-cache"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   PYTHONIOENCODING="utf-8")
        low = 0x00004000 if sys.platform == "win32" else 0           # BELOW_NORMAL_PRIORITY_CLASS
        self.proc = subprocess.Popen([str(py), str(Path(__file__).with_name("chatterbox_server.py")), self.device,
                                      str(self.threads)], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     creationflags=FLAGS | low)
        self._replies = queue.Queue()
        threading.Thread(target=self._read, args=(self.proc, self._replies), daemon=True).start()
        first = self._replies.get(timeout=self.timeout * 2)
        if not first.get("ready"):
            raise RuntimeError("Chatterbox did not start")

    @staticmethod
    def _read(proc, replies) -> None:
        for line in proc.stdout:                    # progress text shares the stream; answers start with @@
            if line.startswith("@@"):
                try:
                    replies.put(json.loads(line[2:]))
                except ValueError:
                    pass
        replies.put({"ok": False, "error": "Chatterbox stopped"})

    def speak(self, text: str, out: Path, exaggeration: float = 0.5) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self._n += 1
            job = {"id": self._n, "text": text, "out": str(out), "voice": str(self.voice) if self.voice else None,
                   "exaggeration": exaggeration}
            self.proc.stdin.write(json.dumps(job) + "\n")
            self.proc.stdin.flush()
            r = self._replies.get(timeout=self.timeout)
            if not r.get("ok"):
                raise RuntimeError(r.get("error", "Chatterbox failed"))
            self.last_secs = r.get("secs", 0.0)

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


class Radio:
    def __init__(self, engine: str = "elevenlabs", commentary_cfg=None, model: str = "eleven_flash_v2_5",
                 effect: bool = True, rate: int = 2, post=None, run=subprocess.run, chatterbox: Optional[ChatterboxVoice] = None):
        self.engine, self.cfg, self.model, self.effect, self.rate = engine, commentary_cfg, model, effect, rate
        self.post, self.run = post, run
        self.chatterbox = chatterbox
        self.errors: list[str] = []

    def path_for(self, text: str) -> Path:
        if self.engine == "chatterbox":
            voice = "chatterbox" + (self.chatterbox.voice.name if self.chatterbox and self.chatterbox.voice else "")
        else:
            voice = self.cfg.elevenlabs_voice_id if self.engine == "elevenlabs" and self.cfg else f"windows{self.rate}"
        h = hashlib.sha1(f"{voice}|{self.model}|{self.effect}|{text}".encode()).hexdigest()[:16]
        return radio_dir() / f"{h}.wav"

    def make(self, text: str) -> Optional[Path]:
        """The radio clip for `text` (made now, or from the cache). None if no voice could make it."""
        out = self.path_for(text)
        if out.is_file():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_suffix(".raw")
        try:
            if self.engine == "chatterbox":
                if self.chatterbox is None:
                    return None
                try:
                    wav = raw.with_suffix(".cb.wav")
                    self.chatterbox.speak(text, wav)
                    wav.replace(raw)
                except Exception as e:                   # free voice only: no paid or generic fallback
                    self.errors.append(str(e)[:200])
                    raw.with_suffix(".cb.wav").unlink(missing_ok=True)
                    return None
            elif self.engine == "elevenlabs":
                if self.cfg is None:                     # no voice settings: silence, never a generic computer voice
                    self.errors.append("no ElevenLabs voice settings")
                    return None
                try:
                    self._eleven(text, raw)
                except Exception as e:                   # the user's voice or nothing
                    self.errors.append(str(e)[:200])
                    return None
            else:
                self._windows(text, raw)                 # only when the Windows voice was chosen on purpose
            radio_filter(raw, out, self.effect, self.run)
            return out
        except (OSError, subprocess.CalledProcessError) as e:
            self.errors.append(str(e)[:200])
            return None
        finally:
            raw.unlink(missing_ok=True)

    def _eleven(self, text: str, raw: Path) -> None:
        from driverdirector import tts
        post = self.post or tts._post_audio
        body = {"text": text, "model_id": self.model,
                "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "style": 0.15, "speed": 1.05,
                                   "use_speaker_boost": True}}
        audio = post("https://api.elevenlabs.io/v1/text-to-speech/"
                     f"{urllib.parse.quote(self.cfg.elevenlabs_voice_id, safe='')}?output_format=mp3_44100_128",
                     {"xi-api-key": tts.cloud_key("elevenlabs")}, body, tries=3, timeout=15.0)
        raw.write_bytes(audio)

    def _windows(self, text: str, raw: Path) -> None:
        p = str(raw.with_suffix(".wav")).replace("'", "''")
        t = text.replace("'", "''")
        self.run(["powershell", "-NoProfile", "-Command",
                  f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate = {int(self.rate)}; $s.SetOutputToWaveFile('{p}'); $s.Speak('{t}'); $s.SetOutputToNull()"],
                 check=True, capture_output=True, creationflags=FLAGS)
        raw.with_suffix(".wav").replace(raw)

    def submit(self, text: str, done: Callable[[Path], None]) -> None:
        """Make the clip in the background and hand it to `done` when it is ready."""
        def work():
            p = self.make(text)
            if p is not None:
                done(p)
        threading.Thread(target=work, daemon=True).start()
