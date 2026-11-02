"""Builds the JSON state pushed to the overlay (OBS browser source) from a live Frame."""
from __future__ import annotations

from typing import Optional

from .models import Event, Frame


def _gap(frame: Frame, me, other, timeline=None) -> float | None:
    if other is None:
        return None
    if timeline is not None:                                   # exact: when the other car passed this point
        t = frame.track_len or 1.0
        g = timeline.gap(me.id, me.laps * 3 + me.lap_dist / t * 3, other.id, other.laps * 3 + other.lap_dist / t * 3,
                         frame.t)
        return None if g is None or isinstance(g, int) else round(abs(g), 2)
    d = abs(other.total_dist(frame.track_len) - me.total_dist(frame.track_len))
    return round(d / max(me.speed, 15.0), 2)


TYRE_LETTER = {"soft": "S", "medium": "M", "hard": "H", "wet": "W", "intermediate": "I", "inter": "I"}


def tyre_table(h) -> dict | None:
    """slot -> {lap number: tyre letter}, for the standings. None unless two or more compounds were used in the race: with
    every car on the same tyre the column would only add clutter."""
    table = {s: {l.n: TYRE_LETTER.get(l.compound.lower(), l.compound[:1].upper()) for l in ls if l.compound}
             for s, ls in h.laps.items()}
    used = {v for t in table.values() for v in t.values()}
    return table if len(used) >= 2 else None


def fmt_interval(g: float) -> str:
    """The gap to the car in front, without a sign: tenths up to a minute, then minutes and seconds."""
    a = abs(g)
    return f"{a:.1f}" if a < 60 else f"{int(a // 60)}:{int(a % 60):02d}"


def fmt_gap(g: float) -> str:
    """A time gap for the standings: tenths up to a minute, then minutes and seconds ("+1:46")."""
    sign = "+" if g > 0 else "-"
    a = abs(g)
    return f"{sign}{a:.1f}" if a < 60 else f"{sign}{int(a // 60)}:{int(a % 60):02d}"


def _tower(frame: Frame, me, focus_id: int, timeline, sector_index, ahead: int, behind: int,
           tyres: dict | None = None, started: bool = True) -> list[dict]:
    """The cars around the focus driver: `ahead` in front and `behind` behind (the window slides to keep its size
    at the front and back of the field), each with its gap to the focus car and its sector colours."""
    order = sorted(frame.cars, key=lambda c: c.place)
    if me is None:
        return [{"id": c.id, "pos": c.place, "num": c.number, "name": c.driver, "cls": c.car_class,
                 "pit": c.in_pits, "focus": False, "gap": "", "bars": None} for c in order[:ahead + behind + 1]]
    size = ahead + behind + 1
    i = order.index(me)
    lo = max(0, min(i - ahead, len(order) - size))
    mine = me.laps * 3 + me.lap_dist / max(frame.track_len, 1.0) * 3
    rows = []
    in_front = {c.id: order[k - 1] for k, c in enumerate(order) if k > 0}     # the car directly ahead of each car
    for c in order[lo:lo + size]:
        gap = interval = ""
        car_ahead = in_front.get(c.id)
        if started and timeline is not None and car_ahead is not None:         # how far this car is behind the one in front
            g2 = timeline.gap(c.id, c.laps * 3 + c.lap_dist / max(frame.track_len, 1.0) * 3, car_ahead.id,
                              car_ahead.laps * 3 + car_ahead.lap_dist / max(frame.track_len, 1.0) * 3, frame.t)
            interval = f"{abs(g2)}L" if isinstance(g2, int) else fmt_interval(g2) if g2 is not None else ""
        if started and c is not me and timeline is not None:
            theirs = c.laps * 3 + c.lap_dist / max(frame.track_len, 1.0) * 3
            g = timeline.gap(focus_id, mine, c.id, theirs, frame.t)
            if isinstance(g, int):
                gap = f"{'+' if g > 0 else '-'}{abs(g)}L"
            elif g is not None:
                gap = fmt_gap(g)
        rows.append({"id": c.id, "pos": c.place, "num": c.number, "name": c.driver, "cls": c.car_class,
                     "pit": c.in_pits, "focus": c is me, "gap": gap, "int": interval,
                     "tyre": (tyres or {}).get(c.id, {}).get(c.laps + 1, ""),
                     "bars": sector_index.bars(c.id, frame.t) if sector_index is not None else None})
    return rows


def race_state(frame: Frame, race_info: tuple | None) -> dict | None:
    """Race lap and time left. `race_info` is (green-flag time, minutes limit, lap limit, laps the winner completed), 0 = no
    such limit / not known. The lap is the leader's. When the length of the race is known (a replay always knows it) the
    counter reads "LAP 7 / 38" and the last lap is exactly the winner's last; without it a timed race's last lap is the
    one in progress when the clock runs out."""
    if race_info is None or not frame.cars:
        return None
    start, minutes, laps = race_info[:3]
    total = race_info[3] if len(race_info) > 3 else 0
    lap = max(c.laps for c in frame.cars) + 1
    shown_total = laps or total or None
    out: dict = {"lap": min(lap, shown_total) if shown_total else lap, "laps": shown_total, "left": None, "final": False}
    if minutes > 0:
        left = minutes * 60.0 - max(0.0, frame.t - start)
        out["left"] = max(0.0, left)
        out["final"] = (lap >= total) if total else left <= 0.0
        if out["final"] and left > 0 and total:
            out["left"] = None                              # the last lap began before the clock ran out: no countdown
    elif shown_total and lap >= shown_total:
        out["final"] = True
    return out


def build_state(frame: Optional[Frame], focus_id: int, *, mode: str = "main",
                active_events: list[Event] | None = None, caption: str = "",
                inlay_label: str = "", sectors: dict | None = None, timeline=None, sector_index=None,
                ahead: int = 5, behind: int = 5, race_info: tuple | None = None, tyres: dict | None = None,
                live: dict | None = None, title: dict | None = None) -> dict:
    if frame is None:
        return {"mode": mode, "t": 0, "tower": [], "focus": None, "battle": None,
                "incident": None, "caption": caption, "inlay_label": inlay_label, "sectors": None, "race": None,
                "live": None, "title": None}
    me = next((c for c in frame.cars if c.id == focus_id), None)
    started = race_info is None or frame.t >= race_info[0]       # before the lights every car is still on the grid: no gaps yet
    tower = _tower(frame, me, focus_id, timeline, sector_index, ahead, behind, tyres, started)
    focus = battle = None
    if me:
        same = sorted((c for c in frame.cars if c.car_class == me.car_class), key=lambda c: c.class_place)
        car_ahead = next((c for c in same if c.class_place == me.class_place - 1), None)
        car_behind = next((c for c in same if c.class_place == me.class_place + 1), None)
        ga, gb = (_gap(frame, me, car_ahead, timeline), _gap(frame, me, car_behind, timeline))
        focus = {"name": me.driver, "num": me.number, "pos": me.place, "cls": me.car_class,
                 "class_pos": me.class_place, "last_lap": me.last_lap, "best_lap": me.best_lap,
                 "lap": me.laps + 1,
                 "ahead": car_ahead.driver if car_ahead else None, "gap_ahead": ga,
                 "behind": car_behind.driver if car_behind else None, "gap_behind": gb}
        pick = min(((ga, car_ahead, "ahead"), (gb, car_behind, "behind")),
                   key=lambda x: x[0] if x[0] is not None else 99)
        if pick[0] is not None and pick[0] < 2.0:
            battle = {"gap": pick[0], "rival": pick[1].driver, "rival_num": pick[1].number,
                      "side": pick[2]}
    incident = None
    for e in active_events or []:
        if e.kind in ("incident", "penalty"):
            incident = {"kind": e.kind, "text": e.desc}
            break
    return {"mode": mode, "t": frame.t, "race": race_state(frame, race_info), "flag": "yellow" if frame.yellow else "green",
            "tower": tower, "focus": focus, "battle": battle, "incident": incident,
            "caption": caption, "inlay_label": inlay_label, "sectors": sectors, "live": live, "title": title}
