"""How people say times, speeds, gaps and distances aloud: no decimals, no "km/h" with a decimal, car lengths instead of
long runs of metres. Used wherever facts are handed to the narrator, so the commentary never has to read out a figure.
"""
from __future__ import annotations

from .speech import int_to_words


CAR_LENGTH_M = 5.0         # a GT3 or prototype is about this long: distances along the track are said in car lengths


def w(n: int) -> str:
    return int_to_words(int(n))


def say_seconds(x: float) -> str:
    """Time lost, as a person would say it: "about half a second", "about a second and a half"."""
    x = abs(x)
    if x < 0.4:
        return "a few tenths of a second"
    if x >= 60:
        return _say_minutes(x)
    q = round(x * 4) / 4
    if q < 2.0:
        return {0.5: "about half a second", 0.75: "about three quarters of a second", 1.0: "about a second",
                1.25: "just over a second", 1.5: "about a second and a half",
                1.75: "nearly two seconds"}.get(q, "about half a second")
    return f"about {w(round(x))} seconds"


def say_speed(kmh: float) -> str:
    """"about thirty kilometres an hour": rounded to the nearest five, never a decimal."""
    v = abs(kmh)
    if v < 7:
        return "a few kilometres an hour"
    return f"about {w(5 * round(v / 5))} kilometres an hour"


def say_along(m: float) -> str:
    """A distance along the track: car lengths once it is more than a few metres."""
    m = abs(m)
    if m < 12:
        return "a few metres"
    n = max(2, round(m / CAR_LENGTH_M))
    return f"about {w(n)} car lengths"


def say_across(m: float) -> str:
    """How far off a line, across the track."""
    m = abs(m)
    if m < 1.5:
        return "a metre or so"
    if m < 2.5:
        return "a couple of metres"
    return f"about {w(round(m))} metres"


def say_over(m: float) -> str:
    """How far past the edge of the track a car went."""
    if m < 0.6:
        return "just over the white line"
    if m < 2.0:
        return "a wheel or two beyond the edge of the track"
    return f"{say_across(m)} beyond the edge of the track"


def say_lap_position(d: float) -> str:
    """Where on the lap, for a stretch with no corner name: "about three and a half kilometres into the lap"."""
    halves = round(d / 500)
    if halves <= 0:
        return "right at the start of the lap"
    whole, half = divmod(halves, 2)
    if whole == 0:
        return "about half a kilometre into the lap"
    return f"about {w(whole)}{' and a half' if half else ''} kilometre{'s' if whole > 1 or half else ''} into the lap"


def join_and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def say_duration(seconds: float) -> str:
    """How long something went on: "a few seconds", "about thirty seconds", "about a minute and a half"."""
    s = abs(seconds)
    if s < 10:
        return "a few seconds"
    if s < 45:
        return f"about {w(5 * round(s / 5))} seconds"
    return _say_minutes(s)


def _say_minutes(s: float) -> str:
    """Anything from about forty-five seconds up, in minutes to the nearest half: "about a minute and a half"."""
    halves = max(2, round(s / 30))                         # in half minutes
    whole, half = divmod(halves, 2)
    if whole == 1:
        return "about a minute and a half" if half else "about a minute"
    return f"about {w(whole)}{' and a half' if half else ''} minutes"


def say_gap(seconds: float) -> str:
    """A gap between two cars: "a few tenths of a second", "about a second and a half", "about three and a half seconds"."""
    x = abs(seconds)
    if x < 2.0:
        return say_seconds(x)
    if x >= 60:                                            # past a minute people stop counting seconds
        return _say_minutes(x)
    whole, frac = divmod(round(x * 2) / 2, 1)
    return f"about {w(int(whole))}{' and a half' if frac else ''} seconds"


def say_km(metres: float) -> str:
    """A lap length the way people say it: "about four and a half kilometres" (nearest half)."""
    halves = max(1, round(metres / 500))
    whole, half = divmod(halves, 2)
    if whole == 0:
        return "about half a kilometre"
    return f"about {w(whole)}{' and a half' if half else ''} kilometres"


def say_race_length(minutes: float = 0.0, laps: int = 0) -> str:
    """The length of a race: "twenty minutes", "an hour", "an hour and a half", "thirty laps"."""
    if laps:
        return f"{w(laps)} laps"
    m = int(round(minutes))
    if m <= 0:
        return ""
    if m == 60:
        return "an hour"
    if m == 90:
        return "an hour and a half"
    if m > 60:
        hours, rest = divmod(m, 60)
        return f"{w(hours)} hour{'s' if hours > 1 else ''}" + (f" and {w(rest)} minutes" if rest else "")
    return f"{w(m)} minutes"
