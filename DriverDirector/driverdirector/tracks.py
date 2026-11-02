"""Facts about the circuits, for the spoken opening: where it is and how many turns it has.

Only published figures are listed. The results file gives the exact lap length and the event name, but not the number of
turns, so those come from this table. A track that is not listed simply gets no turn count (nothing is guessed). Add or
correct entries in %APPDATA%/DriverDirector/tracks.json:
    {"<part of the track name>": {"turns": 17, "place": "Sebring, Florida"}}
The key is matched against the track name with everything but letters and digits removed, lower case.
"""
from __future__ import annotations

import json
import re
import unicodedata

# (part of the normalised track name, turns, where it is)
BUILT_IN: list[tuple[str, int, str]] = [
    ("sebring", 17, "Sebring, Florida"),
    ("enzoedino", 19, "Imola, Italy"),
    ("imola", 19, "Imola, Italy"),
    ("sarthe", 38, "Le Mans, France"),
    ("spafrancorchamps", 19, "Spa-Francorchamps, Belgium"),
    ("monza", 11, "Monza, Italy"),
    ("josecarlospace", 15, "Sao Paulo, Brazil"),
    ("interlagos", 15, "Sao Paulo, Brazil"),
    ("fuji", 16, "Mount Fuji, Japan"),
    ("bahrain", 15, "Sakhir, Bahrain"),
    ("americas", 20, "Austin, Texas"),
    ("algarve", 15, "Portimao, Portugal"),
    ("portimao", 15, "Portimao, Portugal"),
    ("lusail", 16, "Lusail, Qatar"),
]


def _norm(s: str) -> str:
    """Lower case letters and digits only, accents dropped ("Autódromo José Carlos Pace" -> "autodromojosecarlospace")."""
    ascii_ = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_.lower())


def _user_table() -> list[tuple[str, int, str]]:
    try:
        from .config import data_dir
        raw = json.loads((data_dir() / "tracks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for k, v in raw.items():
        try:
            out.append((_norm(k), int(v.get("turns", 0)), str(v.get("place", ""))))
        except (AttributeError, TypeError, ValueError):
            continue
    return out


def lookup(track: str) -> dict:
    """{'turns': int, 'place': str} for a track name, or {} when it is not known. The user's tracks.json wins."""
    name = _norm(track)
    for key, turns, place in _user_table() + BUILT_IN:
        if key and key in name:
            return {"turns": turns, "place": place}
    return {}
