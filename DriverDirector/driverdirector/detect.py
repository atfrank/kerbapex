"""Event detection over a recorded frame sequence: battles, overtakes, incidents, penalties, pit stops."""
from __future__ import annotations

from collections import deque
from typing import Sequence

from .config import DetectSettings
from .models import Event, Frame


def _pos_bonus(place: int) -> float:
    return max(0.0, 6 - place) * 0.5


def gaps_in_order(frame: Frame, same_class: bool):
    """Yield (ahead, behind, gap_seconds) for each adjacent on-track pair."""
    groups: dict[str, list] = {}
    for c in frame.cars:
        if c.in_pits or c.finished:
            continue
        groups.setdefault(c.car_class if same_class else "", []).append(c)
    for cars in groups.values():
        cars.sort(key=lambda c: -c.total_dist(frame.track_len))
        for a, b in zip(cars, cars[1:]):
            d = a.total_dist(frame.track_len) - b.total_dist(frame.track_len)
            yield a, b, d / max(b.speed, 15.0)


def detect_battles(frames: Sequence[Frame], s: DetectSettings) -> list[Event]:
    open_: dict[frozenset, dict] = {}
    done: list[Event] = []

    def close(key, st):
        dur = st["last"] - st["start"]
        if dur >= s.battle_min_s:
            a, b = st["lead"], st["chase"]
            score = 2.0 + min(dur, 90) / 15 + (s.battle_gap_s - st["min_gap"]) * 2 + _pos_bonus(st["place"])
            done.append(Event("battle", st["start"], st["last"], [st["chase"], st["lead"]],
                              round(score, 2), f"{dur:.0f}s battle for P{st['place']}"))

    for fr in frames:
        seen = set()
        for a, b, g in gaps_in_order(fr, s.same_class_only):
            if g > s.battle_gap_s:
                continue
            key = frozenset((a.id, b.id))
            seen.add(key)
            st = open_.get(key)
            if st is None:
                open_[key] = dict(start=fr.t, last=fr.t, min_gap=g, lead=a.id, chase=b.id, place=a.place)
            else:
                st.update(last=fr.t, min_gap=min(st["min_gap"], g), lead=a.id, chase=b.id,
                          place=min(st["place"], a.place))
        for key in list(open_):
            if key not in seen and fr.t - open_[key]["last"] > s.battle_merge_s:
                close(key, open_.pop(key))
    for key, st in open_.items():
        close(key, st)
    return done


def detect_overtakes(frames: Sequence[Frame], s: DetectSettings) -> list[Event]:
    out: list[Event] = []
    prev: dict[tuple[int, int], bool] = {}
    cooldown: dict[frozenset, float] = {}
    for fr in frames:
        close_cars = []
        for a, b, g in gaps_in_order(fr, s.same_class_only):
            if g < 3.0:
                close_cars.append((a, b))
        cur: dict[tuple[int, int], bool] = {}
        for a, b in close_cars:
            cur[(a.id, b.id)] = True
        for (ahead, behind) in cur:
            # pair (behind, ahead) was previously the other way round => a pass
            if prev.get((behind, ahead)):
                key = frozenset((ahead, behind))
                if fr.t - cooldown.get(key, -99) < 10:
                    continue
                cooldown[key] = fr.t
                car = next(c for c in fr.cars if c.id == ahead)
                loser = next(c for c in fr.cars if c.id == behind)
                if car.speed < 20 or loser.speed < 20:   # passing a stopped/crawling car isn't a race pass
                    continue
                out.append(Event("overtake", fr.t - 3, fr.t + 3, [ahead, behind],
                                 round(4.0 + _pos_bonus(car.place), 2), f"pass for P{car.place}"))
        prev = cur
    return out


def detect_incidents(frames: Sequence[Frame], s: DetectSettings) -> list[Event]:
    out: list[Event] = []
    hist: dict[int, deque] = {}
    active: dict[int, dict] = {}
    last: dict[int, tuple[float, float]] = {}
    cooldown: dict[int, float] = {}
    for fr in frames:
        for c in fr.cars:
            h = hist.setdefault(c.id, deque(maxlen=40))
            h.append((fr.t, c.speed))
            prev = last.get(c.id)
            last[c.id] = (fr.t, c.speed)
            if c.in_pits:
                st = active.pop(c.id, None)
                if st:
                    _close_incident(out, st, fr.t, s)
                continue
            st = active.get(c.id)
            if st is None:
                if prev is None or fr.t <= prev[0] or fr.t - cooldown.get(c.id, -99) < 10:
                    continue
                decel = (prev[1] - c.speed) / (fr.t - prev[0])
                recent_max = max((v for t, v in h if fr.t - t <= 6), default=0)
                hard = decel > s.crash_decel and prev[1] > 20
                stopped = c.speed < s.stopped_speed and recent_max > 25 and not fr.yellow
                if hard or stopped:
                    other = _nearby(fr, c)
                    active[c.id] = dict(start=fr.t, car=c.id, place=c.place, hard=hard,
                                        other=other, calm=0.0)
            else:
                if c.speed > 20 and fr.t - st["start"] > 4.0:
                    st["calm"] += last_t(h)
                else:
                    st["calm"] = 0.0
                if st["calm"] >= 2.0 or fr.t - st["start"] > 60:
                    _close_incident(out, active.pop(c.id), fr.t, s)
                    cooldown[c.id] = fr.t
    for st in active.values():
        _close_incident(out, st, frames[-1].t, s)
    return [e for e in out if e.score >= s.min_incident_score]


def last_t(h) -> float:
    return h[-1][0] - h[-2][0] if len(h) > 1 else 0.2


def _nearby(fr: Frame, c):
    for o in fr.cars:
        if o.id != c.id and not o.in_pits and abs(o.lap_dist - c.lap_dist) < 20 and o.laps == c.laps:
            return o.id
    return None


def _close_incident(out, st, t_end, s):
    dur = t_end - st["start"]
    score = 3.0 + min(dur, 40) / 10 + (2.0 if st["hard"] else 0) + (1.5 if st["other"] else 0) + _pos_bonus(st["place"])
    drivers = [st["car"]] + ([st["other"]] if st["other"] else [])
    kind = "contact" if st["other"] else "off/spin"
    out.append(Event("incident", st["start"] - 1.0, t_end + 2.0, drivers, round(score, 2),
                     f"{kind}, {dur:.0f}s"))


def detect_penalties_and_pits(frames: Sequence[Frame]) -> list[Event]:
    out: list[Event] = []
    pen: dict[int, int] = {}
    pit: dict[int, bool] = {}
    for fr in frames:
        for c in fr.cars:
            if c.id in pen and c.penalties > pen[c.id]:
                out.append(Event("penalty", fr.t, fr.t + 8, [c.id], 3.5 + _pos_bonus(c.place), "penalty"))
            pen[c.id] = c.penalties
            if pit.get(c.id) is False and c.in_pits:
                out.append(Event("pit", fr.t, fr.t + 25, [c.id], 1.0 + _pos_bonus(c.place), "pit entry"))
            pit[c.id] = c.in_pits
    return out


def detect_all(frames: Sequence[Frame], s: DetectSettings | None = None) -> list[Event]:
    s = s or DetectSettings()
    evs = (detect_battles(frames, s) + detect_overtakes(frames, s)
           + detect_incidents(frames, s) + detect_penalties_and_pits(frames))
    return sorted(evs, key=lambda e: e.t_start)
