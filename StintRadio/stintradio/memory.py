"""What the engineer remembers between sessions, in %APPDATA%/StintRadio/memory.json: short notes the driver asked it to
keep ("call me Alex", "don't talk about tyres before lap five") and, for each car and track, the last session's best
lap and lap count. Local only; the notes are shown to Claude with the live facts when the driver asks something."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .config import data_dir

MAX_NOTES, MAX_NOTE_CHARS, MAX_SESSIONS = 20, 160, 30


class Memory:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or data_dir() / "memory.json"
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.notes: list[str] = [str(n) for n in d.get("notes", [])][-MAX_NOTES:]
        self.sessions: dict = d.get("sessions", {})

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps({"notes": self.notes, "sessions": self.sessions}, indent=1), encoding="utf-8")
        except OSError:
            pass

    def remember(self, note: str) -> bool:
        note = " ".join(str(note).split())[:MAX_NOTE_CHARS]
        if len(note) < 3 or note.lower() in (n.lower() for n in self.notes):
            return False
        self.notes = (self.notes + [note])[-MAX_NOTES:]
        self.save()
        return True

    def forget(self, text: str) -> int:
        """Drop the notes containing `text`; returns how many."""
        keep = [n for n in self.notes if text.lower() not in n.lower()]
        gone, self.notes = len(self.notes) - len(keep), keep
        if gone:
            self.save()
        return gone

    def session_done(self, track: str, car: str, best: float, laps: int, race: bool) -> None:
        if laps < 1 or best <= 0:
            return
        self.sessions[f"{track}|{car}"] = {"best": round(best, 3), "laps": laps, "race": race,
                                           "date": time.strftime("%Y-%m-%d")}
        for k in sorted(self.sessions, key=lambda k: self.sessions[k]["date"])[:-MAX_SESSIONS]:
            del self.sessions[k]
        self.save()

    def last(self, track: str, car: str) -> Optional[dict]:
        return self.sessions.get(f"{track}|{car}")

    def facts(self, track: str = "", car: str = "") -> str:
        lines = []
        if self.notes:
            lines.append("Things the driver asked you to remember: " + " | ".join(self.notes))
        prev = self.last(track, car)
        if prev:
            from .engineer import say_laptime
            lines.append(f"Last time here in this car ({prev['date']}): best lap {say_laptime(prev['best'])}, {prev['laps']} laps.")
        return "\n".join(lines)
