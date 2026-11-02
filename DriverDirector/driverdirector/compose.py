"""ffmpeg composition: main pass + PiP inlays + (optional) ducked commentary track."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Sequence

from .models import Inlay

def corner_xy(corner: str, margin: int) -> tuple[str, str]:
    """ffmpeg overlay x/y expressions for a corner, `margin` px in from the edges."""
    x = str(margin) if corner.endswith("left") else f"W-w-{margin}"
    y = str(margin) if corner.startswith("top") else f"H-h-{margin}"
    return x, y


def safe_color(c: str) -> str:
    """A colour for ffmpeg's pad filter. Only plain names / #RRGGBB are accepted (it goes into a filter string)."""
    c = (c or "white").strip()
    if re.fullmatch(r"[A-Za-z]{3,20}", c):
        return c
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", c):
        return "0x" + c.lstrip("#")
    return "white"


def _font() -> str | None:
    """A bold font for the tags on the boxes, as an ffmpeg filter value (the drive colon escaped), or None."""
    for name in ("segoeuib.ttf", "arialbd.ttf"):
        p = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if p.is_file():
            return str(p).replace("\\", "/").replace(":", "\\:")
    return None


def _plain(text: str) -> str:
    """Text safe to put in a drawtext filter: letters, digits, space and a few marks."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-/]", " ", text)).strip()


def tag_text(i: Inlay) -> str:
    if i.slow < 1.0:
        speed = "HALF SPEED" if abs(i.slow - 0.5) < 0.01 else f"{i.slow:g}X SPEED"
        return f"REPLAY - {speed}"
    return "REPLAY" if i.replay else ""


def probe_size(path: str) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h = (r.stdout.strip().split(",") + ["0", "0"])[:2]
    return int(w or 0), int(h or 0)


# Video encoders, best first. AMF/NVENC encode on the GPU and need little system RAM, which matters
# when the game and OBS are still running; libx264 is the fallback.
ENCODERS: dict[str, list[str]] = {
    "h264_amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "vbr_peak", "-b:v", "90M",
                 "-maxrate", "120M", "-bufsize", "240M", "-pix_fmt", "yuv420p"],
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "vbr", "-cq", "15", "-b:v", "90M", "-maxrate", "120M",
                   "-pix_fmt", "yuv420p"],
    "libx264": ["-c:v", "libx264", "-preset", "fast", "-crf", "14", "-maxrate", "120M", "-bufsize", "240M",
                "-threads", "4", "-x264-params", "rc-lookahead=20"],
}


def available_encoders(order=("h264_amf", "h264_nvenc", "libx264")) -> list[str]:
    try:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except (RuntimeError, OSError):
        return ["libx264"]
    return [e for e in order if e in out] or ["libx264"]


def ffmpeg_bin() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg not found on PATH")
    return p


def build_command(main: str, inlays: Sequence[Inlay], out: str, t0: float,
                  width: int | None = None, size: float = 0.36, corner: str = "top_right",
                  commentary: str | None = None, duck_db: float = 12.0,
                  border: int | None = None, main_offset: float = 0.0, encoder: str = "libx264",
                  border_pct: float = 0.30, border_color: str = "white", margin_pct: float = 1.2,
                  windows: Sequence[tuple[float, float]] | None = None, voice_gain_db: float = 0.0,
                  duration: float | None = None) -> list[str]:
    """Build the ffmpeg argv. Each inlay clip is shifted onto the main timeline by (show_at - t0).
    The box is `size` x the real video width (probed from `main` unless given); the border is `border_pct`
    percent of the width (or `border` px) and sits `margin_pct` percent in from the corner."""
    width = width or probe_size(main)[0] or 1920
    border = border if border is not None else max(4, round(width * border_pct / 100))
    margin = round(width * margin_pct / 100)
    clips = [i for i in inlays if i.path]
    cmd = ([ffmpeg_bin(), "-y"] + (["-ss", f"{main_offset:.3f}"] if main_offset > 0 else [])
           + (["-t", f"{duration:.3f}"] if duration else []) + ["-i", main])
    for i in clips:
        cmd += (["-ss", f"{i.offset:.3f}"] if i.offset > 0 else []) + ["-i", i.path]
    if commentary:
        cmd += ["-i", commentary]

    pw = int(width * size) // 2 * 2
    x, y = corner_xy(corner, margin)
    color = safe_color(border_color)
    parts: list[str] = []
    last = "0:v"
    font = _font()
    fs = max(14, int(pw * 0.045))
    for n, i in enumerate(clips, start=1):
        s = i.show_at - t0
        e = i.show_until - t0
        slow = i.slow if 0.1 <= i.slow < 1.0 else 1.0       # slow motion: a shorter source stretched over the same box time
        stretch = f"(PTS-STARTPTS)/{slow:.3f}" if slow < 1.0 else "PTS-STARTPTS"
        marks = ""
        if font:
            names = _plain(" - ".join(i.names)).upper()
            tag = _plain(tag_text(i)).upper()
            if names:                                    # who is in the clip: a strip along the bottom of the box
                marks += (f",drawtext=fontfile='{font}':text='{names}':fontsize={fs}:fontcolor=white:box=1:"
                          f"boxcolor=0x101218@0.82:boxborderw=8:x={border}+10:y=h-{border}-text_h-18")
            if tag:                                      # a replay is marked as one, top left
                marks += (f",drawtext=fontfile='{font}':text='{tag}':fontsize={fs}:fontcolor=white:box=1:"
                          f"boxcolor=0xE10600@0.92:boxborderw=8:x={border}+10:y={border}+10")
        fade = min(0.25, max(0.05, (e - s) / 6))
        parts.append(
            f"[{n}:v]trim=duration={(e - s) * slow:.3f},setpts={stretch}+{s:.3f}/TB,"
            f"scale={pw}:-2,pad=iw+{2 * border}:ih+{2 * border}:{border}:{border}:color={color}{marks},"
            f"format=yuva420p,fade=t=in:st={s:.3f}:d={fade:.2f}:alpha=1,fade=t=out:st={e - fade:.3f}:d={fade:.2f}:alpha=1[p{n}]"
        )
        parts.append(
            f"[{last}][p{n}]overlay={x}:{y}:eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'[v{n}]"
        )
        last = f"v{n}"

    maps = ["-map", f"[{last}]" if clips else "0:v"]
    if commentary:
        c = len(clips) + 1
        parts.append(f"[0:a]volume='{duck_expression(windows if windows is not None else speech_windows(commentary), duck_db)}':eval=frame[duck]")
        parts.append(f"[{c}:a]volume={voice_gain_db:.2f}dB[vo]")
        parts.append("[duck][vo]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[a]")
        maps += ["-map", "[a]"]
    else:
        maps += ["-map", "0:a?"]

    if parts:
        cmd += ["-filter_complex", ";".join(parts)]
    cmd += maps + ENCODERS[encoder] + ["-c:a", "aac", "-b:a", "320k", out]
    return cmd


def speech_windows(wav: str, noise_db: int = -38, min_gap: float = 0.5) -> list[tuple[float, float]]:
    """(start, end) of each stretch where the commentator speaks, found by inverting silencedetect."""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", wav, "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_gap}", "-f", "null", "-"],
                       capture_output=True, text=True)
    total = probe_duration(wav)
    silences, start = [], None
    for line in r.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            silences.append((start, float(line.split("silence_end:")[1].split()[0])))
            start = None
    if start is not None:
        silences.append((start, total))
    wins, cur = [], 0.0
    for a, b in silences:
        if a > cur + 0.05:
            wins.append((cur, a))
        cur = b
    if cur < total - 0.05:
        wins.append((cur, total))
    return wins


def duck_expression(windows: Sequence[tuple[float, float]], duck_db: float,
                    fade_in: float = 0.25, fade_out: float = 0.5) -> str:
    """ffmpeg `volume` expression: gain 1 normally, `duck_db` lower while speech plays, with smooth fades."""
    if not windows:
        return "1"
    depth = 1.0 - 10 ** (-abs(duck_db) / 20.0)
    ramps = [f"clip(min((t-{a - fade_in:.3f})/{fade_in},({b + fade_out:.3f}-t)/{fade_out}),0,1)" for a, b in windows]
    while len(ramps) > 1:        # balanced tree: a chain of one max() per window is too deep for ffmpeg after ~60 windows
        ramps = [f"max({ramps[i]},{ramps[i + 1]})" if i + 1 < len(ramps) else ramps[i] for i in range(0, len(ramps), 2)]
    return f"1-{depth:.4f}*{ramps[0]}"


def run(cmd: Sequence[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-2000:]}")


_PROGRESS_KEY = re.compile(r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time_us|out_time_ms|out_time|dup_frames|"
                           r"drop_frames|speed|progress)=")


def run_progress(cmd: Sequence[str], duration: float, cb=None) -> None:
    """Run ffmpeg and report how far along it is (0..1 of `duration` seconds of output) as it goes."""
    full = [cmd[0], "-progress", "pipe:1", "-nostats"] + list(cmd[1:])
    p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
    tail: list[str] = []
    for raw in p.stdout:
        line = raw.strip()
        if line.startswith(("out_time_us=", "out_time_ms=")):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if cb and duration > 0 and us >= 0:
                cb(min(1.0, us / 1e6 / duration))
        elif line and not _PROGRESS_KEY.match(line):
            tail = (tail + [line])[-40:]
    if p.wait():
        raise RuntimeError("ffmpeg failed:\n" + "\n".join(tail)[-2000:])


def _cuts(total: float, chunk_s: float, windows: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Chunk boundaries about `chunk_s` apart that never fall inside a picture-in-picture box (a box would be cut in two)."""
    wins = sorted(windows)
    cuts, t = [0.0], chunk_s
    while t < total - 0.3 * chunk_s:
        moved = True
        while moved:
            moved = False
            for s, e in wins:
                if s - 0.5 < t < e + 0.5:
                    t, moved = e + 0.5, True
        if t >= total - 0.3 * chunk_s:
            break
        cuts.append(t)
        t += chunk_s
    cuts.append(total)
    return list(zip(cuts, cuts[1:]))


def _signature(main: str, inlays, t0: float, chunk_s: float, kw: dict) -> str:
    st = os.stat(main)
    body = {"main": [main, st.st_size, int(st.st_mtime)], "t0": round(t0, 3), "chunk": chunk_s,
            "kw": {k: v for k, v in kw.items() if k not in ("commentary", "windows", "voice_gain_db", "duck_db")},
            "inlays": [[i.path, i.offset, i.show_at, i.show_until, i.slow, list(i.names), i.replay] for i in inlays]}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _compose_chunked(main, inlays, out, t0, encoders, progress, chunk_s, kw) -> str:
    """The video in parts of about `chunk_s` seconds, joined without re-encoding, then the commentary mixed over the whole
    (a quick audio-only pass). A part that is finished stays on disk, so a failure part-way does not lose the work, and
    running it again carries on from the next part."""
    off = kw.get("main_offset", 0.0)
    total = max(1.0, probe_duration(main) - off)
    clips = [i for i in inlays if i.path]
    parts = _cuts(total, chunk_s, [(i.show_at - t0, i.show_until - t0) for i in clips])
    work = Path(out).with_suffix(".parts")
    sig = _signature(main, clips, t0, chunk_s, kw)
    meta_path = work / "parts.json"
    meta: dict = {}
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        pass
    if meta.get("sig") != sig:
        shutil.rmtree(work, ignore_errors=True)
        meta = {"sig": sig, "encoder": "", "done": []}
    work.mkdir(parents=True, exist_ok=True)
    done_len = 0.0
    errors: list[str] = []
    for k, (c0, c1) in enumerate(parts):
        f = work / f"part_{k:03d}.mp4"
        if k in meta["done"] and f.is_file():
            done_len += c1 - c0
            if progress:
                progress(0.95 * done_len / total, f"part {k + 1}/{len(parts)} (kept from before)")
            continue
        sub_kw = {k2: v for k2, v in kw.items() if k2 not in ("commentary", "windows", "voice_gain_db", "duck_db")}
        sub_kw["main_offset"] = off + c0
        inside = [i for i in clips if i.show_at - t0 >= c0 - 0.01 and i.show_until - t0 <= c1 + 0.01]
        cb = (lambda fr, k=k, c0=c0, c1=c1, base=done_len: progress(0.95 * (base + fr * (c1 - c0)) / total,
                                                                   f"part {k + 1}/{len(parts)}")) if progress else None
        order = [meta["encoder"]] if meta["encoder"] else list(encoders or available_encoders())
        for enc in order:
            try:
                run_progress(build_command(main, inside, str(f), t0 + c0, encoder=enc, duration=c1 - c0, **sub_kw),
                             c1 - c0, cb)
                meta["encoder"] = enc                    # every part must use the encoder the first one worked with
                break
            except RuntimeError as e:
                errors.append(f"[{enc}] {str(e)[-400:]}")
        else:
            raise RuntimeError("all encoders failed:\n" + "\n".join(errors))
        meta["done"].append(k)
        meta_path.write_text(json.dumps(meta))
        done_len += c1 - c0
    listing = work / "parts.txt"
    listing.write_text("".join("file '" + str(work / f"part_{k:03d}.mp4").replace("\\", "/").replace("'", "'\\''") + "'\n"
                               for k in range(len(parts))), encoding="utf-8")
    joined = str(work / "joined.mp4") if kw.get("commentary") else out
    if progress:
        progress(0.96, "joining the parts")
    run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", joined])
    if kw.get("commentary"):
        if progress:
            progress(0.98, "mixing the commentary")
        mux_commentary(joined, kw["commentary"], out, 0.0, kw.get("duck_db", 12.0), windows=kw.get("windows"),
                       voice_gain_db=kw.get("voice_gain_db", 0.0))
    shutil.rmtree(work, ignore_errors=True)
    if progress:
        progress(1.0, "done")
    return out


def compose(main: str, inlays: Sequence[Inlay], out: str, t0: float, encoders=None, progress=None,
            chunk_s: float = 0.0, **kw) -> str:
    """Composite with the first encoder that works (GPU first, then CPU). `progress(fraction, note)` is called as it goes;
    a long video (over 1.5 x `chunk_s`) is made in parts that can be carried on after a failure."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    total = max(1.0, probe_duration(main) - kw.get("main_offset", 0.0))
    if chunk_s and total > 1.5 * chunk_s:
        return _compose_chunked(main, inlays, out, t0, encoders, progress, chunk_s, kw)
    errors = []
    cb = (lambda fr: progress(fr, "")) if progress else None
    for enc in encoders or available_encoders():
        try:
            run_progress(build_command(main, inlays, out, t0, encoder=enc, **kw), total, cb)
            return out
        except RuntimeError as e:
            errors.append(f"[{enc}] {str(e)[-400:]}")
    raise RuntimeError("all encoders failed:\n" + "\n".join(errors))


def measure_lufs(path: str) -> float | None:
    """Integrated loudness of an audio file in LUFS (None when it is too short or silent to tell)."""
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    found = re.findall(r"I:\s+(-?[\d.]+)\s+LUFS", r.stderr)
    try:
        v = float(found[-1]) if found else None
    except ValueError:
        return None
    return v if v is not None and v > -60 else None


def level_voice(src: str, out: str, target_lufs: float = -13.0, max_gain_db: float = 20.0) -> str:
    """Make the commentary track clearly louder than the game under it: even out the peaks (a speech compressor), then lift
    it to `target_lufs` (measured on what is spoken, not the silence between) and catch what is left with a limiter. A plain
    gain cannot do this: speech peaks hit the limiter first and most of the extra volume is lost."""
    tmp = out + ".comp.wav"
    run([ffmpeg_bin(), "-y", "-i", src, "-af",
         "highpass=f=70,acompressor=threshold=-22dB:ratio=3.5:attack=4:release=80:makeup=3dB", tmp])
    try:
        lufs = measure_lufs(tmp)
        gain = max(-6.0, min(max_gain_db, target_lufs - lufs)) if lufs is not None else 0.0
        run([ffmpeg_bin(), "-y", "-i", tmp, "-af", f"volume={gain:.2f}dB,alimiter=limit=0.95:level=disabled", out])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return out


def mux_commentary(main: str, wav: str, out: str, main_offset: float = 0.0, duck_db: float = 12.0,
                   windows: Sequence[tuple[float, float]] | None = None, voice_gain_db: float = 0.0) -> str:
    """Add commentary to a finished recording WITHOUT touching the video (stream copy): only the audio is
    re-encoded, so this needs almost no memory and takes seconds. The commentary is delayed by the
    recording's lead-in (`main_offset`) instead of trimming the video."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    delay = int(max(0.0, main_offset) * 1000)
    duck = duck_expression([(a + main_offset, b + main_offset) for a, b in (windows if windows is not None else speech_windows(wav))],
                           duck_db)
    fg = (f"[0:a:0]volume='{duck}':eval=frame[duck];"
          f"[1:a]adelay={delay}|{delay},volume={voice_gain_db:.2f}dB[cm];"
          f"[duck][cm]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.97[a]")
    run([ffmpeg_bin(), "-y", "-i", main, "-i", wav, "-filter_complex", fg, "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "320k", out])
    return out


def probe_duration(path: str) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


# ---- cinematic finishing ------------------------------------------------------------------------------------------
GRADES = {
    # gentle contrast, a little less colour, cooler shadows and warmer highlights, a soft vignette and fine grain
    "film": "eq=contrast=1.07:saturation=0.9:gamma=0.98,colorbalance=rs=-0.03:bs=0.04:rh=0.04:bh=-0.03,"
            "vignette=angle=PI/5,noise=alls=3:allf=t",
    "none": "",
}


def letterbox_bar(width: int, height: int, ratio: float) -> int:
    """Height of each black bar that shows the picture at `ratio` (0 or a ratio not wider than the frame: none)."""
    if not ratio or width / height >= ratio:
        return 0
    return max(0, round((height - width / ratio) / 2))


def _title_filters(lines: Sequence[str], font: str | None, start: float, length: float, height: int) -> str:
    """The opening title: up to three lines, centred in the lower third, fading in and out."""
    if not font or not lines or length <= 2:
        return ""
    a, b = start, start + length
    alpha = f"if(lt(t,{a:.2f}),0,if(lt(t,{a + 1:.2f}),t-{a:.2f},if(lt(t,{b - 1:.2f}),1,if(lt(t,{b:.2f}),{b:.2f}-t,0))))"
    sizes = [int(height * 0.055), int(height * 0.03), int(height * 0.025)]
    out, y = [], int(height * 0.62)
    for i, line in enumerate(lines[:3]):
        text = _plain(line).upper() if i == 0 else _plain(line)
        if not text:
            continue
        fs = sizes[i]
        out.append(f"drawtext=fontfile='{font}':text='{text}':fontsize={fs}:fontcolor=white:alpha='{alpha}':"
                   f"x=(w-text_w)/2:y={y}:shadowcolor=black@0.6:shadowx=2:shadowy=2")
        y += int(fs * 1.5)
    return ",".join(out)


def build_cinematic_command(main: str, out: str, main_offset: float, duration: float, music: str | None,
                            encoder: str = "libx264", size: tuple[int, int] | None = None, letterbox: float = 2.76,
                            grade: str = "film", fade_s: float = 2.0, title: Sequence[str] = (), title_s: float = 7.0,
                            game_level_db: float = -6.0, inset: str | None = None, inset_offset: float = 0.0,
                            inset_size: float = 0.30) -> list[str]:
    """One pass over the raw recording: grade, letterbox, fade from/to black, the opening title, and the game sound
    lowered under the music (`music` is a finished bed of the same length, or None). With `inset` (a second recording of the
    same stretch, starting `inset_offset` s in) it is shown in a bordered box in the top right corner, `inset_size` x the
    width; its own sound is not used."""
    w, h = size or probe_size(main)
    w, h = w or 3440, h or 1440
    cmd = [ffmpeg_bin(), "-y"] + (["-ss", f"{main_offset:.3f}"] if main_offset > 0 else []) + \
          ["-t", f"{duration:.3f}", "-i", main]
    if inset:
        cmd += (["-ss", f"{inset_offset:.3f}"] if inset_offset > 0 else []) + ["-t", f"{duration:.3f}", "-i", inset]
    if music:
        cmd += ["-i", music]
    vf = [f for f in (GRADES.get(grade, ""),) if f]
    bar = letterbox_bar(w, h, letterbox)
    if bar:
        vf.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    t = _title_filters(title, _font(), 1.5, title_s, h)
    if t:
        vf.append(t)
    if fade_s > 0:
        vf.append(f"fade=t=in:st=0:d={fade_s:.2f},fade=t=out:st={max(0.0, duration - fade_s):.2f}:d={fade_s:.2f}")
    if inset:
        border, margin = max(4, round(w * 0.003)), round(w * 0.012)
        x, y = corner_xy("top_right", margin)
        parts = [f"[1:v]setpts=PTS-STARTPTS,scale={int(w * inset_size) // 2 * 2}:-2,"
                 f"pad=iw+{2 * border}:ih+{2 * border}:{border}:{border}:color=white[ins]",
                 f"[0:v][ins]overlay={x}:{y}:eof_action=pass,{','.join(vf) if vf else 'null'}[v]"]
    else:
        parts = [f"[0:v]{','.join(vf) if vf else 'null'}[v]"]
    music_in = 2 if inset else 1
    af = f"volume={game_level_db:.2f}dB"
    if fade_s > 0:
        af += f",afade=t=in:d={fade_s:.2f},afade=t=out:st={max(0.0, duration - fade_s):.2f}:d={fade_s:.2f}"
    if music:
        parts.append(f"[0:a]aresample=48000,{af}[g]")
        parts.append(f"[g][{music_in}:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    else:
        parts.append(f"[0:a]{af}[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]"]
    return cmd + ENCODERS[encoder] + ["-c:a", "aac", "-b:a", "320k", out]


def compose_cinematic(main: str, out: str, main_offset: float, duration: float, music: str | None,
                      encoders=None, progress=None, **kw) -> str:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    errors = []
    cb = (lambda fr: progress(fr, "")) if progress else None
    for enc in encoders or available_encoders():
        try:
            run_progress(build_cinematic_command(main, out, main_offset, duration, music, encoder=enc, **kw), duration, cb)
            return out
        except RuntimeError as e:
            errors.append(f"[{enc}] {str(e)[-400:]}")
    raise RuntimeError("all encoders failed:\n" + "\n".join(errors))
