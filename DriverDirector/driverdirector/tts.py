"""Text-to-speech engines for commentary.

* chatterbox - the open-source Chatterbox model (Resemble AI), the engine LMU Director uses. It runs in a
  separate Python 3.11 process (LMU Director's runtime venv works) that exits when done, so the ~3 GB
  model never sits in memory next to the game or the video encode. CPU speed is ~4x real time.
* edge - Microsoft's online edge-tts voices; instant, but more robotic.
* fish / elevenlabs - cloud voices. Only chosen explicitly (the commentary text leaves this PC), with the API key
  taken from FISH_API_KEY / ELEVENLABS_API_KEY in the environment. They need no RAM, unlike Chatterbox.

Per-line voices follow the excitement of the moment (normal / excited / overexcited reference clips).
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import CommentarySettings
from .speech import speakable

WORKER = Path(__file__).with_name("chatterbox_worker.py")
_DONE = re.compile(r"DONE \d+ [\d.]+")
MIN_FREE_GB = 4.0                        # Chatterbox needs roughly this much free RAM on CPU


@dataclass
class Line:
    id: int
    text: str
    excite: int = 0                      # 0 normal, 1 excited, 2 overexcited
    voice: int = 0                       # 0 = the main commentator, 1 = the analyst (second voice)


def free_ram_gb() -> float:
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tphys", ctypes.c_ulonglong),
                    ("aphys", ctypes.c_ulonglong), ("tpage", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong),
                    ("tvirt", ctypes.c_ulonglong), ("avirt", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    m = MS()
    m.l = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.aphys / 1024 ** 3


def find_runtime(cfg: CommentarySettings) -> Optional[dict]:
    """Locate a Python env with chatterbox-tts + model cache (LMU Director's, unless configured)."""
    root = Path(cfg.chatterbox_runtime) if cfg.chatterbox_runtime else \
        Path(os.environ.get("LOCALAPPDATA", "")) / "LMUTools" / "LMUDirector" / "Runtime"
    py = root / "venv" / "Scripts" / "python.exe"
    if not py.is_file():
        return None
    return {"python": py, "hf_home": root / "hf-cache", "voices": root.parent / "Voices" / "chatterbox"}


_SUFFIXES = ("-overexcited", "-excited", "-normal")


def list_voices(cfg: CommentarySettings) -> list[dict]:
    """Dropdown options: the model default, each reference clip played on every line, and each voice
    family in 'auto' mode (picks the normal / excited / overexcited clip to match the moment)."""
    opts = [{"value": "", "label": "Model default voice"}]
    rt = find_runtime(cfg)
    stems = sorted(p.stem for p in rt["voices"].glob("*.wav")) if rt and rt["voices"].is_dir() else []
    families = sorted({st[: -len(sfx)] for st in stems for sfx in _SUFFIXES if st.endswith(sfx)})
    for fam in families:
        if sum(1 for st in stems if st.startswith(fam + "-")) > 1:
            opts.append({"value": fam, "label": f"{fam}  (auto: matches the excitement of each moment)"})
    opts += [{"value": st, "label": f"{st}  (this clip on every line)"} for st in stems]
    return opts


def _voice_path(cfg: CommentarySettings, rt: dict, excite: int) -> Optional[str]:
    """Reference clip for a line. `voice` is a base like 'british-female' (+ -normal/-excited/-overexcited),
    a full clip name, a file path, or empty for the model's default voice."""
    v = cfg.voice
    if not v or v.startswith("("):
        return None
    if os.path.isfile(v):
        return v
    suffix = ["normal", "excited", "overexcited"][max(0, min(2, excite))]
    for cand in (f"{v}-{suffix}", v):
        p = rt["voices"] / f"{cand}.wav"
        if p.is_file():
            return str(p)
    return None


def synth_chatterbox(lines: list[Line], cfg: CommentarySettings, workdir: Path,
                     progress: Callable[[int, int], None] | None = None) -> dict[int, Path]:
    rt = find_runtime(cfg)
    if not rt:
        raise RuntimeError("Chatterbox runtime not found (install LMU Director's voice engine or set "
                           "commentary.chatterbox_runtime)")
    free = free_ram_gb()
    if free < MIN_FREE_GB:
        raise RuntimeError(f"only {free:.1f} GB RAM free; Chatterbox needs about {MIN_FREE_GB:.0f} GB. "
                           "Close Le Mans Ultimate (and heavy apps) and try again.")
    workdir.mkdir(parents=True, exist_ok=True)
    exag = {0: cfg.exaggeration, 1: min(1.0, cfg.exaggeration + 0.2), 2: min(1.2, cfg.exaggeration + 0.4)}
    order = sorted(lines, key=lambda l: (_voice_path(cfg, rt, l.excite) or "", l.id))   # group by voice
    out = {l.id: workdir / f"line_{l.id}.wav" for l in lines}
    job = {"device": cfg.device, "threads": os.cpu_count() or 8, "cfg_weight": cfg.cfg_weight,
           "temperature": cfg.temperature,
           "lines": [{"text": l.text, "out": str(out[l.id]), "voice": _voice_path(cfg, rt, l.excite),
                      "exaggeration": exag[l.excite]} for l in order]}
    jp = workdir / "job.json"
    jp.write_text(json.dumps(job), encoding="utf-8")
    env = dict(os.environ, HF_HOME=str(rt["hf_home"]), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               PYTHONIOENCODING="utf-8")
    # stderr is merged into stdout and drained line by line: the model prints lots of progress text, and a
    # separately-read stderr pipe fills up, blocking the worker forever (this deadlocked once).
    p = subprocess.Popen([str(rt["python"]), str(WORKER), str(jp)], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=0x08000000 if sys.platform == "win32" else 0)
    done, tail = 0, []
    for raw in p.stdout:                                   # the worker prints one DONE line per finished line
        marks = len(_DONE.findall(raw))          # a marker can share a line with progress-bar text
        for _ in range(marks):
            done += 1
            if progress:
                progress(done, len(lines))
        if not marks and raw.strip():
            tail = (tail + [raw.rstrip()[-200:]])[-12:]
    if p.wait():
        raise RuntimeError(f"Chatterbox failed ({p.returncode}): " + " | ".join(tail)[-500:])
    missing = [i for i, f in out.items() if not f.is_file()]
    if missing:
        raise RuntimeError(f"Chatterbox produced no audio for lines {missing}")
    return out


def synth_edge(lines: list[Line], cfg: CommentarySettings, workdir: Path,
               progress: Callable[[int, int], None] | None = None) -> dict[int, Path]:
    import edge_tts
    workdir.mkdir(parents=True, exist_ok=True)
    out = {}
    for n, l in enumerate(lines, 1):
        f = workdir / f"line_{l.id}.mp3"
        asyncio.run(edge_tts.Communicate(l.text, cfg.edge_voice).save(str(f)))
        out[l.id] = f
        if progress:
            progress(n, len(lines))
    return out


CLOUD = {"fish": "FISH_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}      # engine -> environment variable


def cloud_key(engine: str) -> str:
    name = CLOUD[engine]
    key = os.environ.get(name, "").strip()
    if not key:
        raise RuntimeError(f"{name} is not set. Set it as a Windows user environment variable (the app never stores "
                           "keys), then restart this app and the program that started it.")
    return key


def cloud_keys_present() -> dict[str, bool]:
    """Which cloud voice keys the environment has (True/False only; the keys themselves are never exposed)."""
    return {e: bool(os.environ.get(n, "").strip()) for e, n in CLOUD.items()}


def _post_audio(url: str, headers: dict, body: dict, tries: int = 3, timeout: float = 90.0) -> bytes:
    """POST a JSON body, return the audio bytes. Retries rate limits and server errors; error messages carry the
    service's answer but never the request headers (they hold the API key)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={**headers, "Content-Type": "application/json",
                                          "User-Agent": "DriverDirector/0.1"})
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                audio = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503) and n < tries - 1:
                time.sleep(2.0 * (n + 1))
                continue
            raise RuntimeError(f"the voice service answered {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if n < tries - 1:
                time.sleep(2.0 * (n + 1))
                continue
            raise RuntimeError(f"could not reach the voice service ({e})") from None
        if len(audio) < 1000:
            raise RuntimeError("the voice service returned no audio")
        return audio
    raise RuntimeError("the voice service did not answer")


def _synth_cloud(engine: str, lines: list[Line], cfg: CommentarySettings, workdir: Path,
                 progress: Callable[[int, int], None] | None) -> dict[int, Path]:
    key = cloud_key(engine)
    workdir.mkdir(parents=True, exist_ok=True)
    out: dict[int, Path] = {}
    for n, l in enumerate(lines, 1):
        e = max(0, min(2, l.excite))
        if engine == "fish":
            body = {"text": l.text, "format": "mp3", "latency": "normal",
                    "prosody": {"speed": (1.0, 1.05, 1.1)[e]}}
            if cfg.fish_voice_id:
                body["reference_id"] = cfg.fish_voice_id
            audio = _post_audio("https://api.fish.audio/v1/tts",
                                {"Authorization": f"Bearer {key}", "model": cfg.fish_model}, body)
        else:
            x = cfg.elevenlabs_expression[min(e, len(cfg.elevenlabs_expression) - 1)]
            body = {"text": l.text, "model_id": cfg.elevenlabs_model,
                    "voice_settings": {"stability": x["stability"], "similarity_boost": 0.75, "style": x["style"],
                                       "speed": min(1.2, x["speed"]), "use_speaker_boost": True}}
            audio = _post_audio("https://api.elevenlabs.io/v1/text-to-speech/"
                                f"{urllib.parse.quote(cfg.elevenlabs_voice_id, safe='')}?output_format=mp3_44100_128",
                                {"xi-api-key": key}, body)
        f = workdir / f"line_{l.id}.mp3"
        f.write_bytes(audio)
        out[l.id] = f
        if progress:
            progress(n, len(lines))
    return out


def list_elevenlabs_voices() -> list[dict]:
    """The voices the user's ElevenLabs account can speak with: [{id, name, category, labels, description}]. The key is
    read from the environment like every other call and never returned."""
    key = cloud_key("elevenlabs")
    req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key,
                                                                              "User-Agent": "DriverDirector/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"the voice service answered {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
        raise RuntimeError(f"could not list the voices ({e})") from None
    return [{"id": v.get("voice_id", ""), "name": v.get("name", ""), "category": v.get("category", ""),
             "labels": v.get("labels") or {}, "description": (v.get("description") or "")[:160]}
            for v in data.get("voices", [])]


def resolved_engine(cfg: CommentarySettings) -> str:
    return ("chatterbox" if find_runtime(cfg) else "edge") if cfg.tts_engine == "auto" else cfg.tts_engine


def has_second_voice(cfg: CommentarySettings) -> bool:
    """True when two commentators are asked for AND the engine in use has a voice chosen for the analyst."""
    if cfg.commentators < 2:
        return False
    engine = resolved_engine(cfg)
    return bool({"chatterbox": cfg.voice_b, "edge": cfg.edge_voice_b, "fish": cfg.fish_voice_id_b,
                 "elevenlabs": cfg.elevenlabs_voice_id_b}.get(engine, ""))


def second_voice(cfg: CommentarySettings) -> CommentarySettings:
    """The settings with the analyst's voice swapped in, to voice the analyst's lines the same way."""
    return cfg.model_copy(update={"voice": cfg.voice_b or cfg.voice, "edge_voice": cfg.edge_voice_b or cfg.edge_voice,
                                  "fish_voice_id": cfg.fish_voice_id_b or cfg.fish_voice_id,
                                  "elevenlabs_voice_id": cfg.elevenlabs_voice_id_b or cfg.elevenlabs_voice_id})


def synthesize(lines: list[Line], cfg: CommentarySettings, workdir: Path,
               progress: Callable[[int, int], None] | None = None) -> tuple[dict[int, Path], str]:
    """Voice every line. Lines for the analyst (voice = 1) are voiced with the second voice, in a second pass."""
    main = [l for l in lines if not l.voice]
    other = [l for l in lines if l.voice]
    total = len(lines)
    out, engine = _synthesize(main, cfg, workdir, (lambda i, n: progress(i, total)) if progress and other else progress)         if main else ({}, resolved_engine(cfg))
    if other:
        base = len(main)
        more, _ = _synthesize(other, second_voice(cfg), workdir / "b",
                              (lambda i, n: progress(base + i, total)) if progress else None)
        out.update(more)
    return out, engine


def _synthesize(lines: list[Line], cfg: CommentarySettings, workdir: Path,
                progress: Callable[[int, int], None] | None = None) -> tuple[dict[int, Path], str]:
    """Voice every line with the configured engine. Returns ({id: audio file}, engine used).
    Numbers are spelled out first: the models read digit strings badly ("24" came out as "2, 4")."""
    lines = [Line(l.id, speakable(l.text), l.excite) for l in lines]
    engine = resolved_engine(cfg)
    if engine in CLOUD:
        return _synth_cloud(engine, lines, cfg, workdir, progress), engine
    if engine == "chatterbox":
        return synth_chatterbox(lines, cfg, workdir, progress), "chatterbox"
    return synth_edge(lines, cfg, workdir, progress), "edge"
