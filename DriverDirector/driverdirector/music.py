"""Background music: an AI-generated (MusicGen, run locally, no API key) or user-supplied instrumental, looped
under the whole video and ducked under the commentator.

The generated piece is cached by prompt, so the (slow, ~5x realtime on CPU) generation happens once and
every later video reuses it. Everything here is post-production: it needs no game.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import tts
from .compose import duck_expression, ffmpeg_bin, run
from .config import MusicSettings, data_dir

HERE = Path(__file__).parent
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
_DONE = re.compile(r"DONE\s+(\d+)\s+([\d.]+)")


def cache_dir() -> Path:
    p = data_dir() / "music"
    p.mkdir(parents=True, exist_ok=True)
    return p


def hf_homes() -> list[Path]:
    """Where a MusicGen download may be: our own folder, and the same folder as the packaged desktop app sees it."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    homes = [local / "DriverDirector" / "hf"]
    homes += sorted((local / "Packages").glob("Claude_*/LocalCache/Local/DriverDirector/hf")) if local.name else []
    return homes


def find_model(cfg: MusicSettings) -> Optional[str]:
    """Folder of a downloaded copy of cfg.model, or None (the worker then downloads it)."""
    if Path(cfg.model).is_dir():
        return cfg.model
    slug = "models--" + cfg.model.replace("/", "--")
    for home in hf_homes():
        snaps = sorted((home / "hub" / slug / "snapshots").glob("*"))
        if snaps and (snaps[-1] / "model.safetensors").exists():
            return str(snaps[-1])
    return None


def cache_key(cfg: MusicSettings) -> str:
    blob = json.dumps([cfg.model, cfg.prompt, cfg.seconds, cfg.clips, cfg.seed], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def cached_loop(cfg: MusicSettings) -> Optional[Path]:
    p = cache_dir() / f"ai-{cache_key(cfg)}.wav"
    return p if p.is_file() else None


def generate(cfg: MusicSettings, commentary_cfg, progress: Callable[[int, int, float], None] = lambda *a: None) -> Path:
    """Run MusicGen in LMU Director's Python (it ships torch + transformers). Returns the cached loop file."""
    done = cached_loop(cfg)
    if done:
        return done
    rt = tts.find_runtime(commentary_cfg)
    if not rt:
        raise RuntimeError("AI music needs LMU Director's runtime (torch + transformers); "
                           "install LMU Director, or set the music source to 'folder'.")
    free = tts.free_ram_gb()
    if free < 4.0:
        raise RuntimeError(f"only {free:.1f} GB RAM free; AI music needs about 4 GB. Close the game and retry.")
    out = cache_dir() / f"ai-{cache_key(cfg)}.wav"
    tmp = out.with_suffix(".part.wav")
    args = {"prompt": cfg.prompt, "seconds": min(30.0, cfg.seconds), "clips": cfg.clips, "seed": cfg.seed,
            "model": find_model(cfg) or cfg.model, "hf_home": str(hf_homes()[0]), "out": str(tmp), "xfade": 3.0}
    p = subprocess.Popen([str(rt["python"]), str(HERE / "music_worker.py"), json.dumps(args)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    tail: list[str] = []
    for line in p.stdout:                              # the worker prints one whole line per finished clip
        m = _DONE.search(line)
        if m:
            progress(int(m[1]), cfg.clips, float(m[2]))
        tail = (tail + [line.strip()])[-12:]
    if p.wait() or not tmp.is_file():
        raise RuntimeError("music generation failed:\n" + "\n".join(t for t in tail if t)[-1500:])
    tmp.replace(out)
    return out


def cloud_key(cfg: MusicSettings, seconds: Optional[float] = None) -> str:
    secs = min(300.0, max(3.0, seconds if seconds is not None else cfg.elevenlabs_seconds))
    blob = json.dumps([cfg.elevenlabs_model, cfg.prompt, secs], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def cached_cloud_loop(cfg: MusicSettings) -> Optional[Path]:
    p = cache_dir() / f"eleven-{cloud_key(cfg)}.mp3"
    return p if p.is_file() else None


def generate_elevenlabs(cfg: MusicSettings, seconds: Optional[float] = None, post=None) -> Path:
    """An instrumental from Eleven Music (POST /v1/music; paid plans), made once and cached by prompt, model and length.
    The API key comes only from the environment (ELEVENLABS_API_KEY)."""
    secs = min(300.0, max(3.0, seconds if seconds is not None else cfg.elevenlabs_seconds))
    out = cache_dir() / f"eleven-{cloud_key(cfg, secs)}.mp3"
    if out.is_file():
        return out
    key = tts.cloud_key("elevenlabs")
    body = {"prompt": cfg.prompt, "music_length_ms": int(secs * 1000), "model_id": cfg.elevenlabs_model,
            "force_instrumental": True}            # no vocals, guaranteed; a seed cannot be combined with a prompt
    audio = (post or tts._post_audio)("https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128",
                                      {"xi-api-key": key}, body, tries=2, timeout=420.0)
    tmp = out.with_suffix(".part.mp3")
    tmp.write_bytes(audio)
    tmp.replace(out)
    return out


def folder_loop(folder: str) -> Optional[Path]:
    """The user's own tracks (a royalty-free folder), joined into one file to loop. None if there are none."""
    files = sorted(f for f in Path(folder).expanduser().glob("*") if f.suffix.lower() in AUDIO_EXT) if folder else []
    if not files:
        return None
    key = hashlib.sha1("|".join(f"{f}:{f.stat().st_mtime_ns}" for f in files).encode()).hexdigest()[:12]
    out = cache_dir() / f"folder-{key}.wav"
    if out.is_file():
        return out
    cmd = [ffmpeg_bin(), "-y"]
    for f in files[:12]:
        cmd += ["-i", str(f)]
    n = len(files[:12])
    if n > 1:                                          # [0][1]->x1, [x1][2]->x2 ...
        parts, last = [], "0:a"
        for i in range(1, n):
            parts.append(f"[{last}][{i}:a]acrossfade=d=3[x{i}]")
            last = f"x{i}"
        cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]"]
    run(cmd + ["-ar", "48000", "-ac", "2", str(out)])
    return out


def mean_db(path: str) -> float:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+)", r.stderr)
    return float(m[1]) if m else -20.0


def build_bed(loop: str, length: float, windows: Sequence[tuple[float, float]], cfg: MusicSettings, out: str,
              offset: float = 0.0) -> str:
    """The finished music track, `length` seconds long: looped, set to `cfg.level_db` (mean level), faded in
    and out, and ducked `cfg.duck_db` while the commentator speaks (`windows`, shifted by `offset`)."""
    gain = cfg.level_db - mean_db(loop)
    duck = duck_expression([(a + offset, b + offset) for a, b in windows], cfg.duck_db, fade_in=0.35, fade_out=0.8)
    fade_out = min(4.0, max(0.5, length / 4))
    af = (f"aresample=48000,aformat=channel_layouts=stereo,"
          f"volume={gain:.2f}dB,volume='{duck}':eval=frame,afade=t=in:d={min(2.0, length / 4):.2f},"
          f"afade=t=out:st={max(0.0, length - fade_out):.2f}:d={fade_out:.2f},alimiter=limit=0.9")
    run([ffmpeg_bin(), "-y", "-stream_loop", "-1", "-i", loop, "-t", f"{length:.3f}", "-af", af, out])
    return out


def mix(commentary: Optional[str], bed: str, out: str) -> str:
    """Commentary and music as one audio file (the compositor mixes this over the game sound)."""
    if not commentary:
        return bed
    run([ffmpeg_bin(), "-y", "-i", commentary, "-i", bed, "-filter_complex",
         "[0:a]aresample=48000,aformat=channel_layouts=stereo[c];[1:a]aresample=48000[m];"
         "[c][m]amix=inputs=2:duration=longest:normalize=0[a]", "-map", "[a]", out])
    return out
