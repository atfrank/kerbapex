"""What the engineer says before a session and around the pits. All of it is made from facts about EARLIER runs and from what is
known about the session that is about to begin: nothing here may speak as if laps of this session had been driven. The wording
is the future ("today", "first lap", "when you get to turn five"), never the past of this session.
"""
from __future__ import annotations

from typing import Optional

from driverdirector.phrasing import w as words

from .engineer import Msg, say_delta, say_laptime

OPENING = {"race": "Race briefing.", "qualifying": "Qualifying.", "practice": "Practice.", "warm-up": "Warm-up."}
CLOSING = {
    "race": "First lap, keep it clean and get some heat into the tyres before you push. Good luck.",
    "qualifying": "Use the out-lap to get the tyres up to temperature, then build to one clean push lap.",
    "practice": "Build up over the first laps and I'll start the debrief once you are settled.",
    "warm-up": "Use it to get the feel back, then keep it tidy.",
}


ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh"}


def metres(m: float) -> str:
    """A distance the way it is said: to the nearest five metres, at least five."""
    n = max(5, int(round(m / 5.0)) * 5)
    return f"{words(n)} metres"


def pointer(p: dict) -> str:
    """What to do at one corner, as advice for the laps ahead (the clause after "at turn five, ")."""
    h = p.get("habit")
    if h == "brake_early":
        return f"you tend to brake about {metres(p['amount'])} early, so trust the car and go a touch deeper"
    if h == "brake_late":
        return f"you tend to go in about {metres(p['amount'])} too deep, so brake a little earlier and carry more speed through the middle"
    if h == "throttle":
        return "you tend to be late back on the power, so be patient with the steering and get on it earlier"
    if h == "gear" and p.get("gear"):
        return f"use {ORDINAL.get(p['gear'], words(p['gear']))} gear, you have often been in a different one"
    if h == "wide":
        return "you tend to run wide at the apex, so aim to clip it"
    return f"that is where the most time has been, about {say_delta(p['loss'])} a lap"


def short_tip(p: dict) -> str:
    """What to do at a corner in a few words, for the overlay: "brake 10 m later", "power earlier"."""
    h = p.get("habit")
    m = max(5, int(round(p.get("amount", 0.0) / 5.0)) * 5)
    if h == "brake_early":
        return f"brake {m} m later"
    if h == "brake_late":
        return f"brake {m} m earlier"
    if h == "throttle":
        return "power earlier"
    if h == "gear" and p.get("gear"):
        return f"use {ORDINAL.get(p['gear'], str(p['gear']))} gear"
    if h == "wide":
        return "clip the apex"
    return "find time"


def race_length(race: Optional[dict]) -> str:
    if not race:
        return ""
    if race.get("laps", 0) and race["laps"] < 10_000:
        return f"{words(int(race['laps']))} laps."
    if race.get("minutes", 0):
        return f"{words(int(round(race['minutes'])))} minutes."
    return ""


def briefing(kind: str, target: Optional[float], last: Optional[dict], pointers: list, laps_known: int, race: Optional[dict] = None) -> Msg:
    """The spoken briefing at the start of a session.
    kind: practice, qualifying, race, warm-up. target: the lap time to beat (the reference). last: {best, laps, date} from last time
    here. pointers: from CornerHistory.pointers (worst first). laps_known: clean laps seen here before."""
    parts = [OPENING.get(kind, "Session briefing.")]
    length = race_length(race) if kind == "race" else ""
    if length:
        parts[0] += " " + length[0].upper() + length[1:]
    if target:
        parts.append(f"The lap to beat is {say_laptime(target)}.")
    elif last and last.get("best"):
        parts.append(f"Last time out here you set a {say_laptime(last['best'])}.")
    if pointers:
        n = 2 if kind == "race" else 3
        lead = ("Where the time is: at", "Then at", "And at")
        parts += [f"{lead[i]} {p['name']}, {pointer(p)}." for i, p in enumerate(pointers[:n])]
    elif laps_known >= 3:
        parts.append("Nothing big stands out from your earlier runs here, so aim to be consistent.")
    else:
        parts.append("I haven't got much on you at this track in this car yet, so use the early laps to learn it and I'll start with pointers as we go.")
    parts.append(CLOSING.get(kind, ""))
    return Msg(" ".join(p for p in parts if p), priority=1, expires=120.0, kind="brief")


def pit_in(kind: str, worn_pct: Optional[float] = None) -> Msg:
    """The car has come into the pit lane: coaching stops, and this says so."""
    text = "In the pits."
    if kind == "race" and worn_pct is not None and worn_pct >= 10:
        text += f" The tyres are about {words(int(round(worn_pct / 10.0)) * 10)} percent worn."
    return Msg(text + " I'll stay quiet until you're back out.", priority=1, expires=15.0, kind="pit")


def pit_out(kind: str) -> Msg:
    """The car has left the pit lane after a stop: the next lap is an out-lap and is not judged."""
    tail = "keep it clean in the traffic" if kind == "race" else "build up gently"
    return Msg(f"Out of the pits. This is an out-lap, so bring the tyres up to temperature and {tail}. The debrief starts again on the next lap.",
               priority=1, expires=20.0, kind="pit")
