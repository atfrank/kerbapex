"""Shot planner: turns events + a focus driver into a Plan (main-pass camera cues + PiP inlays)."""
from __future__ import annotations

import random
from typing import Sequence

from .config import CameraSettings, DirectSettings
from .models import CameraCue, Event, Frame, Inlay, Plan, ReferenceLap


def _pick(weights: dict[str, int], rng: random.Random, avoid: str | None) -> str:
    pool = {g: w for g, w in weights.items() if w > 0 and g != avoid} or \
           {g: w for g, w in weights.items() if w > 0} or {"trackside": 1}
    groups, w = zip(*pool.items())
    return rng.choices(groups, weights=w)[0]


def _place_at(frames: Sequence[Frame], t: float, car_id: int) -> int | None:
    best = min(frames, key=lambda f: abs(f.t - t))
    for c in best.cars:
        if c.id == car_id:
            return c.place
    return None


def fmt_lap(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:06.3f}"


def reference_inlay(ref: ReferenceLap, focus_id: int, green: float, t0: float, t1: float) -> Inlay | None:
    """The box showing the focus driver's best qualifying lap from the green flag for one lap's length. The clip
    belongs to the qualifying replay (clip_start/clip_end are on ITS clock); show_at/show_until are race time.
    None when the video does not cover the start of the race (nothing to put it beside)."""
    if t0 > green + 1.0 or green >= t1 - 10.0:
        return None
    show_until = min(t1, green + ref.lap_time)
    lap = fmt_lap(ref.lap_time)
    ev = Event("reference", green, show_until, [focus_id], 0.0, lap, "exact", label=f"BEST QUALI LAP {lap}")
    return Inlay(ev, ref.lap_start, ref.lap_start + (show_until - green), green, show_until, focus_id, "driving",
                 ev.label, replay_id=ref.replay_id, replay_name=ref.replay_name, focus_name=ref.driver)


def build_plan(frames: Sequence[Frame], events: Sequence[Event], focus_id: int,
               direct: DirectSettings, cam: CameraSettings, seed: int = 7,
               reference: ReferenceLap | None = None) -> Plan:
    if not frames:
        raise ValueError("no frames to plan from")
    # open on the grid before the green flag; the replay clock starts at 0, so never go below that
    t0 = direct.t_start if direct.t_start is not None else max(frames[0].t - direct.pre_roll_s, min(frames[0].t, 0.0))
    t1 = direct.t_end if direct.t_end is not None else frames[-1].t
    rng = random.Random(seed)
    pace = 1.0 if direct.pace == "patient" else 0.6

    focus_events = [e for e in events if e.involves(focus_id) and e.t_end > t0 and e.t_start < t1
                    and e.confidence in ("exact", "est")]

    def context_weights(t: float) -> tuple[dict[str, int], float]:
        if t < t0 + direct.start_coverage_s:
            return {"trackside": 3, "driving": 2, "onboard": 1, "tv": 1}, 0.8
        if t > t1 - direct.finish_coverage_s:
            return {"trackside": 3, "driving": 2, "tv": 1}, 0.8
        active = [e for e in focus_events if e.t_start - 2 <= t <= e.t_end
                  and e.kind in ("battle", "overtake", "incident", "mistake")]
        if active:
            w = {"trackside": 3, "driving": 3, "onboard": 1, "tv": 1}
            if any(e.kind == "battle" and len(e.drivers) > 1 and e.drivers[1] == focus_id for e in active):
                w["rear"] = 2                # a car is hounding the driver: look back at it
            return w, 0.7
        return cam.weights, 1.0

    cues: list[CameraCue] = []
    t, prev = t0, None
    while t < t1:
        w, scale = context_weights(t)
        group = _pick(w, rng, prev)
        cues.append(CameraCue(round(t, 2), group, focus_id))
        t += max(cam.min_shot, cam.angle_duration * pace * scale)
        prev = group

    ref = reference_inlay(reference, focus_id, frames[0].t, t0, t1) if reference else None
    inlays = _select_inlays(frames, events, focus_id, direct, t0, t1, [ref] if ref else [])
    return Plan(focus_id, t0, t1, cues, inlays, list(events))


def incident_camera(e: Event) -> str | None:
    """The view that tells an incident best. One car on its own (a spin, a wall) is told from inside that car; several
    cars (contact, a pile-up) from the side of the track, where all of them are in frame. A pass is watched from the
    car that made it. Other events have no preferred view."""
    if e.kind == "incident":
        return "onboard" if len(e.drivers) == 1 else "trackside"
    if e.kind == "overtake":
        return "onboard"
    return None


def incident_preroll(e: Event, direct: DirectSettings) -> float:
    """Seconds shown before an event's key moment. The game reports an incident at its first CONTACT, but a car that
    ends up in the wall has been out of control for a few seconds before that, so a lone car needs the longest lead."""
    if e.kind != "incident":
        return direct.inlay_preroll
    return direct.incident_preroll_single if len(e.drivers) == 1 else direct.incident_preroll_multi


def _names(frames, drivers, limit: int = 4) -> list[str]:
    """The surnames of the cars in a clip, for the strip under its box."""
    roster = {c.id: c.driver for c in frames[0].cars} if frames else {}
    out = []
    for i in drivers[:limit]:
        n = (roster.get(i) or "").split("#")[0].strip()
        if n:
            out.append(n.split()[-1])
    return out


def _select_inlays(frames, events, focus_id, direct: DirectSettings, t0, t1, fixed=()) -> list[Inlay]:
    scored = []
    passes = []
    for e in events:
        if (e.kind != "overtake" or not e.involves(focus_id) or e.confidence == "lap"
                or e.t_end < t0 or e.t_start > t1):
            continue
        passes.append((e.score, e))
    passes.sort(key=lambda x: -x[0])
    for e in events:
        if e.involves(focus_id) or e.kind in ("pit", "tracklimits", "penalty") or e.confidence == "lap":   # nothing to look at in a warning or a penalty
            continue
        if e.t_end < t0 or e.t_start > t1:
            continue
        mid = (e.t_start + e.t_end) / 2
        fp = _place_at(frames, mid, focus_id)
        ep = _place_at(frames, mid, e.drivers[0])
        near = 0.0
        if fp is not None and ep is not None:
            near = max(0.0, 3.0 - abs(fp - ep))          # events near the focus car matter more
        scored.append((e.score + near, e))
    scored.sort(key=lambda x: -x[0])

    chosen: list[Inlay] = list(fixed)      # already placed (the reference lap): the others keep clear of them

    def window(e: Event) -> tuple[float, float] | None:
        pre = incident_preroll(e, direct)
        if e.peak is not None:        # centre the box on the key moment of a long event
            show_at = max(t0, e.peak - pre)
            show_until = min(t1, e.peak + direct.inlay_max_s - direct.inlay_preroll,
                             max(e.t_end, e.peak + 4) + direct.inlay_postroll)
        else:
            show_at = max(t0, e.t_start - pre)
            show_until = min(t1, e.t_end + direct.inlay_postroll, show_at + direct.inlay_max_s)
        if show_until - show_at < 3.0:
            return None
        pad = direct.inlay_min_gap_s / 2          # breathing room between boxes, measured edge to edge
        if any(show_at < c.show_until + pad and c.show_at < show_until + pad for c in chosen):
            return None
        return show_at, show_until

    # the focus driver's own passes get an alternate view on top of the main camera (which follows them):
    # a pass they made is watched from the side of the track, a pass made on them from the passing car
    n_pass = 0
    for _, e in passes:
        if n_pass >= direct.max_pass_inlays:
            break
        w = window(e)
        if w is None:
            continue
        i_passed = e.drivers[0] == focus_id
        other = next((c.driver for c in frames[0].cars if c.id == e.drivers[i_passed]), "")
        other = other.split()[-1] if other else ""
        label = f"{'PASSES' if i_passed else 'PASSED BY'} {other}".strip().upper()[:30]
        chosen.append(Inlay(e, w[0], w[1], w[0], w[1], focus_id if i_passed else e.drivers[0],
                            "trackside" if i_passed else "onboard", label=label, names=_names(frames, e.drivers)))
        n_pass += 1

    n_other = 0
    for _, e in scored:
        if n_other >= direct.max_inlays:
            break
        w = window(e)
        if w is None:
            continue
        cam = incident_camera(e) or ("driving" if n_other % 2 else "trackside")
        chosen.append(Inlay(e, w[0], w[1], w[0], w[1], e.drivers[0], cam, label=(e.label or e.desc).upper(),
                            names=_names(frames, e.drivers)))
        n_other += 1

    # the driver's own mistakes, replayed in slow motion right after they happen (the commentary says "let's have another look")
    speed = min(0.9, max(0.25, direct.slow_mo_speed))
    pad = direct.inlay_min_gap_s / 2
    n_replay = 0
    for e in sorted((x for x in events if x.kind == "mistake" and x.involves(focus_id) and t0 <= x.t_start and x.t_end <= t1),
                    key=lambda x: -x.score):
        if n_replay >= direct.mistake_replays:
            break
        length = min(6.0, (e.t_end - e.t_start) + 3.0)
        show_at = e.t_end + 1.0
        show_until = min(t1, show_at + length / speed)
        if show_until - show_at < 3.0 or any(show_at < c.show_until + pad and c.show_at < show_until + pad for c in chosen):
            continue
        start = e.t_start - 1.5
        chosen.append(Inlay(e, start, start + (show_until - show_at) * speed, show_at, show_until, focus_id, "trackside",
                            label=("REPLAY " + (e.label or "")).strip(), slow=speed, replay=True))
        n_replay += 1

    mine = [i for i in chosen if i not in fixed and not i.replay]
    for inl in sorted(mine, key=lambda i: -i.event.score)[:max(0, direct.slow_mo_inlays)]:
        # same time on screen, shorter source clip centred on the key moment, stretched by the encoder
        peak = inl.event.peak if inl.event.peak is not None else (inl.event.t_start + inl.event.t_end) / 2
        peak = min(max(peak, inl.show_at), inl.show_until)
        inl.clip_start = peak - (peak - inl.show_at) * speed
        inl.clip_end = inl.clip_start + (inl.show_until - inl.show_at) * speed
        inl.slow = speed
    return sorted(chosen, key=lambda i: i.show_at)


# ---- cinematic ---------------------------------------------------------------------------------------------------
def _key_moment(e: Event) -> float:
    return e.peak if e.peak is not None else e.t_start


def build_cinematic_plan(frames: Sequence[Frame], events: Sequence[Event], focus_id: int, direct: DirectSettings,
                         cine, cam: CameraSettings, seed: int = 7) -> Plan:
    """A film of the race: long shots of the focus driver from the TV and trackside cameras, cut-aways to the biggest
    incidents elsewhere in the field, and the best moments played in the game's own slow motion. No boxes."""
    if not frames:
        raise ValueError("no frames to plan from")
    t0 = direct.t_start if direct.t_start is not None else max(frames[0].t - direct.pre_roll_s, min(frames[0].t, 0.0))
    t1 = direct.t_end if direct.t_end is not None else frames[-1].t
    rng = random.Random(seed)
    usable = [e for e in events if e.confidence in ("exact", "est") and t0 + 20 < _key_moment(e) < t1 - 10]

    # cut-aways: the biggest incidents that do not involve the driver, told from the side of the track
    away = sorted((e for e in usable if e.kind == "incident" and not e.involves(focus_id)
                   and e.score >= cine.cutaway_min_score), key=lambda e: -e.score)
    windows: list[tuple[float, float, Event]] = []
    for e in away:
        if len(windows) >= cine.cutaways:
            break
        a = _key_moment(e) - (direct.incident_preroll_single if len(e.drivers) == 1 else direct.incident_preroll_multi)
        b = max(e.t_end, _key_moment(e)) + 5.0
        if all(b < x - 10 or a > y + 10 for x, y, _ in windows):
            windows.append((a, b, e))
    windows.sort(key=lambda w: w[0])

    # slow motion: the driver's own incidents and passes first, then the cut-aways, biggest first
    mine = [e for e in usable if e.involves(focus_id) and e.kind in ("incident", "overtake")]
    candidates = sorted(mine, key=lambda e: -e.score) + [w[2] for w in sorted(windows, key=lambda w: -w[2].score)]
    slow: list[tuple[float, float]] = []
    for e in candidates:
        if len(slow) >= cine.slow_moments:
            break
        k = _key_moment(e)
        a, b = k - cine.slow_before_s, k + cine.slow_after_s
        if all(b < x - 15 or a > y + 15 for x, y in slow):
            slow.append((round(a, 2), round(b, 2)))
    slow.sort()

    cues: list[CameraCue] = []
    t, prev = t0, None
    wi = 0
    while t < t1:
        if wi < len(windows) and t >= windows[wi][0] - 0.5:
            a, b, e = windows[wi]
            cues.append(CameraCue(round(max(t, a), 2), "trackside", e.drivers[0]))   # cut away ...
            t, prev, wi = b, "trackside", wi + 1                                      # ... and come back after it
            continue
        group = _pick(cine.weights, rng, prev)
        cues.append(CameraCue(round(t, 2), group, focus_id))
        step = max(cine.min_shot, cine.shot_s * rng.uniform(0.75, 1.25))
        nxt = windows[wi][0] if wi < len(windows) else t1
        t = min(t + step, nxt) if nxt - t > cine.min_shot else nxt
        prev = group
    return Plan(focus_id, t0, t1, cues, [], list(events), slow=slow, style="cinematic")


def video_length(plan: Plan, rate: float) -> float:
    """Seconds of film the plan makes: slow stretches take 1/rate as long as they cover in the replay."""
    extra = sum((b - a) * (1.0 / max(0.05, rate) - 1.0) for a, b in plan.slow)
    return (plan.t1 - plan.t0) + extra
