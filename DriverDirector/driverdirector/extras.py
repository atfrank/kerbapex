"""Things made from a finished video and its plan, and estimates before one is made.

* chapter_moments  - the moments of the video for YouTube chapters (also written next to the video as a text file)
* highlights       - a short cut of the whole video: the start, every incident and mistake, the stops and the finish
* shorts           - one vertical clip (9:16) per picture-in-picture moment, the box on top and the picture below
* estimate         - how long recording and finishing will take and how much disk it needs, from the plan

None of them touches the game: they only read the finished video.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import compose as C
from . import facts as race_facts
from .models import Inlay, Plan


# ---- chapters -------------------------------------------------------------------------------------------------------
def _is_ref(i: Inlay) -> bool:
    return i.event.kind == "reference" or i.replay_id is not None


def chapter_moments(plan: Plan, h, mistakes=()) -> list[tuple[float, str]]:
    """(seconds into the video, label) for the notable moments: each box, the green flag, pit stops, the final lap and the
    finish. YouTube wants them at least ten seconds apart; the description builder thins them out."""
    out: list[tuple[float, str]] = []
    for i in plan.inlays:
        label = i.label.lower()
        if _is_ref(i):
            label = "best qualifying lap alongside lap one"
        elif i.replay:
            label = label.replace("replay ", "replay: ", 1) if label.startswith("replay ") else label
        out.append((i.show_at - plan.t0, label))
    if h is not None and h.exact:
        green = h.race_start
        if green - plan.t0 >= 10:
            out.append((green - plan.t0, "green flag"))
        for t, txt, _ in race_facts.pit_facts(h, plan.focus_id, plan.t0, plan.t1):
            out.append((t - 22 - plan.t0, "pit stop"))
        total = h.total_laps
        leader = min((s for s, ls in h.laps.items() if ls and max(l.n for l in ls) == total), default=None,
                     key=lambda s: max(l.et or 0 for l in h.laps[s])) if total else None
        if leader is not None and total > 1:
            start = h.t_end.get(leader, {}).get(total - 1)
            if start and plan.t0 < start < plan.t1 - 20:
                out.append((start - plan.t0, "final lap"))
    for m in mistakes:
        out.append((m.t1 + 0.5 - plan.t0, f"mistake: {m.where}"))
    out.append((plan.t1 - 26 - plan.t0, "chequered flag and recap"))
    return sorted(x for x in out if x[0] >= 0)


# ---- estimates ------------------------------------------------------------------------------------------------------
@dataclass
class Estimate:
    video_min: float
    record_min: float
    finish_min: float
    disk_gb: float

    def as_dict(self) -> dict:
        return {k: round(v, 1) for k, v in self.__dict__.items()}


def estimate(plan: Plan, slow_rate: float = 0.25) -> Estimate:
    """Rough times from what recent runs took: recording runs in real time (plus the box clips and, with a reference lap,
    the qualifying replay); finishing is the voice, the compositing (about 0.4 x the video length) and some fixed work.
    The disk figure is the raw recordings plus the finished video, with some room."""
    span = max(0.0, plan.t1 - plan.t0)
    # slow motion (cinematic) plays its stretches at slow_rate, so the film and its recording are that much longer
    video = (span + sum((b - a) * (1.0 / max(0.05, slow_rate) - 1.0) for a, b in plan.slow)) / 60
    clips = sum(max(0.0, (i.clip_end - i.clip_start)) for i in plan.inlays) / 60
    ref = any(i.replay_id is not None for i in plan.inlays)
    record = video + clips * 1.3 + 2.0 + (3.0 if ref else 0.0)
    finish = 6.0 + 0.45 * video
    disk = (0.35 * video) * 2.2 + 1.0
    return Estimate(video, record, finish, disk)


def free_gb(path: str | Path) -> float:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1024 ** 3


# ---- highlights -----------------------------------------------------------------------------------------------------
def highlight_segments(plan: Plan, h, mistakes=(), target_s: float = 240.0) -> list[tuple[float, float]]:
    """Video-time (start, end) stretches to keep, best first until `target_s` is used, then in video order and merged."""
    total = plan.t1 - plan.t0
    cand: list[tuple[int, float, float]] = []        # (priority: lower is kept first, start, end)
    green = (h.race_start - plan.t0) if h is not None else 30.0
    cand.append((0, max(0.0, green - 3), green + 18))
    cand.append((0, max(0.0, total - 45), total))
    for i in plan.inlays:
        if _is_ref(i):
            continue
        pr = 1 if i.event.kind == "incident" else 2 if i.replay else 3
        cand.append((pr, max(0.0, i.show_at - plan.t0 - 3), min(total, i.show_until - plan.t0 + 3)))
    for m in mistakes:
        cand.append((2, max(0.0, m.t1 - plan.t0 - 6), min(total, m.t1 - plan.t0 + 6)))
    if h is not None and h.exact:
        for t, _, _ in race_facts.pit_facts(h, plan.focus_id, plan.t0, plan.t1):
            cand.append((4, max(0.0, t - plan.t0 - 24), min(total, t - plan.t0 + 6)))
    kept: list[tuple[float, float]] = []
    used = 0.0
    for _, a, b in sorted(cand):
        if b - a < 2 or any(a < kb and ka < b for ka, kb in kept):      # overlaps something already kept: it is covered
            continue
        if used + (b - a) > target_s and kept:
            continue
        kept.append((a, b))
        used += b - a
    kept.sort()
    merged: list[list[float]] = []
    for a, b in kept:
        if merged and a - merged[-1][1] < 1.5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def highlights(video: str, plan: Plan, h, out: str, mistakes=(), target_s: float = 240.0, encoders=None,
               progress: Callable[[float, str], None] | None = None) -> str:
    """Cut the highlights from a finished video (with its commentary) into `out`."""
    segs = highlight_segments(plan, h, mistakes, target_s)
    if not segs:
        raise ValueError("nothing to cut")
    cmd0 = [C.ffmpeg_bin(), "-y"]
    for a, b in segs:                                  # one fast-seeking input per stretch, so a long video is not decoded through
        cmd0 += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video]
    fx = []
    for n, (a, b) in enumerate(segs):
        d = b - a
        f = min(0.2, d / 4)
        fx.append(f"[{n}:v]setpts=PTS-STARTPTS[v{n}]")
        fx.append(f"[{n}:a]asetpts=PTS-STARTPTS,afade=t=in:d={f:.2f},afade=t=out:st={d - f:.2f}:d={f:.2f}[a{n}]")
    fx.append("".join(f"[v{n}][a{n}]" for n in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=1[v][a]")
    total = sum(b - a for a, b in segs)
    errors = []
    for enc in encoders or C.available_encoders():
        try:
            C.run_progress(cmd0 + ["-filter_complex", ";".join(fx), "-map", "[v]", "-map", "[a]"] + C.ENCODERS[enc]
                           + ["-c:a", "aac", "-b:a", "192k", out], total,
                           (lambda fr: progress(fr, "highlights")) if progress else None)
            return out
        except RuntimeError as e:
            errors.append(f"[{enc}] {str(e)[-300:]}")
    raise RuntimeError("all encoders failed:\n" + "\n".join(errors))


# ---- vertical shorts ------------------------------------------------------------------------------------------------
def short_windows(plan: Plan, limit_s: float = 58.0) -> list[tuple[Inlay, float, float]]:
    """(box, start, end) in video seconds for each box worth a short (the reference-lap box is not)."""
    out = []
    for i in plan.inlays:
        if _is_ref(i):
            continue
        a, b = i.show_at - plan.t0 - 0.3, i.show_until - plan.t0 + 0.3
        out.append((i, max(0.0, a), min(b, max(0.0, a) + limit_s)))
    return out


def short_filter(W: int, H: int, size: float, corner: str, border_pct: float, margin_pct: float,
                 out_w: int = 1080, out_h: int = 1920) -> str:
    """The ffmpeg filtergraph for one short: the picture-in-picture box (cropped out of the finished video) on top, the main
    picture below it (a centre crop: the camera keeps the driver of interest in the middle)."""
    pw = int(W * size) // 2 * 2
    border = max(4, round(W * border_pct / 100))
    margin = round(W * margin_pct / 100)
    bx = margin if corner.endswith("left") else W - margin - (pw + 2 * border)
    by = margin if corner.startswith("top") else H - margin - (round(pw * H / W) // 2 * 2 + 2 * border)
    iw = pw - 4
    ih = (round(pw * H / W) // 2 * 2) - 4
    ix, iy = bx + border + 2, by + border + 2
    th = round(out_w * ih / iw) // 2 * 2
    bh = out_h - th
    cw = round(H * out_w / bh) // 2 * 2
    cx = max(0, (W - cw) // 2)
    return (f"[0:v]split=2[a][b];[a]crop={iw}:{ih}:{ix}:{iy},scale={out_w}:{th}[top];"
            f"[b]crop={min(cw, W)}:{H}:{cx}:0,scale={out_w}:{bh}[bot];[top][bot]vstack=inputs=2[v]")


def shorts(video: str, plan: Plan, out_dir: str, size: float = 0.36, corner: str = "top_right",
           border_pct: float = 0.30, margin_pct: float = 1.2, encoders=None,
           progress: Callable[[float, str], None] | None = None) -> list[str]:
    """One vertical clip per box into `out_dir`; returns the files."""
    W, H = C.probe_size(video)
    if not W or not H:
        raise RuntimeError("could not read the video's size")
    wins = short_windows(plan)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    flt = short_filter(W, H, size, corner, border_pct, margin_pct)
    files = []
    for n, (inl, a, b) in enumerate(wins, start=1):
        slug = re.sub(r"[^a-z0-9]+", "-", (inl.label or inl.event.kind).lower()).strip("-")[:40] or "moment"
        out = str(Path(out_dir) / f"{n:02d}_{slug}.mp4")
        errors = []
        for enc in encoders or C.available_encoders():
            try:
                C.run_progress([C.ffmpeg_bin(), "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", video,
                                "-filter_complex", flt, "-map", "[v]", "-map", "0:a?"] + C.ENCODERS[enc]
                               + ["-c:a", "aac", "-b:a", "160k", out], b - a,
                               (lambda fr, n=n: progress((n - 1 + fr) / len(wins), f"short {n}/{len(wins)}"))
                               if progress else None)
                files.append(out)
                break
            except RuntimeError as e:
                errors.append(f"[{enc}] {str(e)[-300:]}")
        else:
            raise RuntimeError("all encoders failed:\n" + "\n".join(errors))
    return files


# ---- overlay sync test -------------------------------------------------------------------------------------------------
def side_by_side(clips: Sequence[tuple[float, str, float]], out: str, seconds: float, panel_w: int = 960) -> str:
    """The takes of the sync test next to each other, each labelled with its delay. `clips` are (delay, file, lead-in
    seconds): the overlay's 16:9 area (the middle of the picture) of each, played together."""
    font = C._font()
    cmd = [C.ffmpeg_bin(), "-y"]
    for _, path, lead in clips:
        cmd += ["-ss", f"{lead:.3f}", "-t", f"{seconds:.3f}", "-i", path]
    W, H = C.probe_size(clips[0][1])
    cw = min(W, round(H * 16 / 9) // 2 * 2)
    cx = (W - cw) // 2
    fx = []
    for n, (delay, _, _) in enumerate(clips):
        label = ""
        if font:
            label = (f",drawtext=fontfile='{font}':text='overlay delay {delay:.1f} s':fontsize=34:fontcolor=white:box=1:"
                     "boxcolor=0xE10600@0.9:boxborderw=8:x=(w-text_w)/2:y=h-text_h-24")
        fx.append(f"[{n}:v]crop={cw}:{H}:{cx}:0,scale={panel_w}:-2{label}[p{n}]")
    fx.append("".join(f"[p{n}]" for n in range(len(clips))) + f"hstack=inputs={len(clips)}[v]")
    errors = []
    for enc in C.available_encoders():
        try:
            C.run_progress(cmd + ["-filter_complex", ";".join(fx), "-map", "[v]", "-an"] + C.ENCODERS[enc] + [out], seconds)
            return out
        except RuntimeError as e:
            errors.append(f"[{enc}] {str(e)[-300:]}")
    raise RuntimeError("all encoders failed:\n" + "\n".join(errors))
