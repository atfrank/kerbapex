"""What the driver does in each corner, over many sessions: for every track and car, and every corner of it, how many laps were seen,
how much time went there, and how often braking was early or late, the power came late, the wrong gear was used, or the line ran
wide. It is what the engineer's briefing before a session is made from. Only clean laps are counted (never an in-lap or out-lap).
Stored in %APPDATA%/StintRadio/corner_history.json; local only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .config import data_dir

MIN_LAPS = 3                 # a corner needs this many laps seen before it is worth a pointer
MIN_LOSS_S = 0.06            # and this many seconds lost on average
BRAKE_M = 5.0                # metres of braking difference that count as early or late
THROTTLE_S = 0.25            # seconds late back on the power that count
WIDE_M = 1.5                 # metres outside the reference line at the apex that count as running wide
MAX_KEYS = 40


class CornerHistory:
    def __init__(self, path: Optional[Path] = None, memory_only: bool = False):
        """memory_only: a history that is only kept for the session in hand (never read from or written to a file)."""
        self.memory_only = memory_only
        self.path = path or data_dir() / "corner_history.json"
        self.data: dict = {}
        if not memory_only:
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def save(self) -> None:
        if self.memory_only:
            return
        try:
            for k in sorted(self.data, key=lambda k: self.data[k].get("date", ""))[:-MAX_KEYS]:
                del self.data[k]
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def key(track: str, car: str) -> str:
        return f"{track}|{car}"

    def add_lap(self, track: str, car: str, notes: list, corners: list, track_len: float, lap_time: float = 0.0) -> None:
        """One clean lap's notes (one per corner judged). Corners are told apart by where on the lap they are, not by their number,
        so a new reference lap with a different numbering does not mix them up."""
        if not notes or not corners or track_len <= 0:
            return
        by_n = {c.n: c for c in corners}
        rec = self.data.setdefault(self.key(track, car), {"corners": {}, "laps": 0, "sessions": 0})
        rec["date"], rec["laps"] = time.strftime("%Y-%m-%d"), rec.get("laps", 0) + 1
        if lap_time > 0:
            rec["best"] = round(min(rec.get("best", 1e9), lap_time), 3)
        for n in notes:
            c = by_n.get(n.n)
            if c is None:
                continue
            k = str(round(c.apex / track_len * 100))
            r = rec["corners"].setdefault(k, {"name": n.corner, "n": 0, "loss": 0.0, "early": 0, "late": 0, "brake_sum": 0.0, "brake_n": 0,
                                              "throttle": 0, "throttle_sum": 0.0, "gear": 0, "gear_n": 0, "wide": 0, "wide_n": 0, "ref_gear": {}})
            r["name"], r["n"], r["loss"] = n.corner, r["n"] + 1, r["loss"] + float(n.delta)
            if n.brake_diff is not None:
                r["brake_n"] += 1
                r["brake_sum"] += float(n.brake_diff)
                r["early"] += n.brake_diff <= -BRAKE_M
                r["late"] += n.brake_diff >= BRAKE_M
            if n.throttle_s is not None and n.throttle_s >= THROTTLE_S:
                r["throttle"] += 1
                r["throttle_sum"] += float(n.throttle_s)
            if n.my_gear is not None and n.ref_gear is not None:
                r["gear_n"] += 1
                if n.my_gear != n.ref_gear:
                    r["gear"] += 1
                    g = str(n.ref_gear)
                    r["ref_gear"][g] = r["ref_gear"].get(g, 0) + 1
            if n.apex_off is not None:
                r["wide_n"] += 1
                r["wide"] += n.apex_off <= -WIDE_M
        self.save()

    def session_started(self, track: str, car: str) -> None:
        rec = self.data.setdefault(self.key(track, car), {"corners": {}, "laps": 0, "sessions": 0})
        rec["sessions"] = rec.get("sessions", 0) + 1
        self.save()

    def laps(self, track: str, car: str) -> int:
        return int((self.data.get(self.key(track, car)) or {}).get("laps", 0))

    def pointers(self, track: str, car: str, k: int = 3, min_laps: int = MIN_LAPS) -> list:
        """The corners that cost the most time, each with the habit behind it: [{name, loss, habit, amount, gear, laps}], worst first."""
        rec = self.data.get(self.key(track, car)) or {}
        out = []
        for key, r in (rec.get("corners") or {}).items():
            n = r.get("n", 0)
            if n < min_laps or r["loss"] / n < MIN_LOSS_S:
                continue
            p = {"key": key, "name": r["name"], "loss": r["loss"] / n, "habit": None, "amount": 0.0, "gear": None, "laps": n}
            share = lambda hits, seen: hits / seen if seen else 0.0
            cands = []
            if r["brake_n"] and share(r["early"], r["brake_n"]) >= 0.5:
                cands.append((share(r["early"], r["brake_n"]), "brake_early", abs(r["brake_sum"] / r["brake_n"])))
            if r["brake_n"] and share(r["late"], r["brake_n"]) >= 0.5:
                cands.append((share(r["late"], r["brake_n"]), "brake_late", abs(r["brake_sum"] / r["brake_n"])))
            if share(r["throttle"], n) >= 0.5:
                cands.append((share(r["throttle"], n), "throttle", r["throttle_sum"] / max(1, r["throttle"])))
            if r["gear_n"] and share(r["gear"], r["gear_n"]) >= 0.5:
                cands.append((share(r["gear"], r["gear_n"]), "gear", 0.0))
                p["gear"] = int(max(r["ref_gear"], key=r["ref_gear"].get)) if r["ref_gear"] else None
            if r["wide_n"] and share(r["wide"], r["wide_n"]) >= 0.5:
                cands.append((share(r["wide"], r["wide_n"]), "wide", 0.0))
            if cands:
                _share, p["habit"], p["amount"] = max(cands)
            out.append(p)
        return sorted(out, key=lambda p: -p["loss"])[:k]
