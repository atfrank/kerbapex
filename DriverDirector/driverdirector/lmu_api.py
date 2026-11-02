"""Client for LMU's built-in REST API (default http://127.0.0.1:6397).

Endpoints used were read from the game's own swagger-schema.json:
  GET  /rest/watch/replays                      replay library
  GET  /rest/watch/play/{id}                    load a replay
  GET  /rest/replay/isActive                    replay running?
  GET/PUT /rest/watch/focus[/{slotid}]          focused car
  PUT  /rest/watch/replaytime/{time}            seek
  PUT  /rest/watch/replayCommand/{command}      transport
  GET  /rest/watch/getIncidentsList/{minGap}    game-detected incidents
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


class LMUApiError(RuntimeError):
    pass


def fix_mojibake(s: str) -> str:
    """The API double-encodes UTF-8 ('AutÃ³dromo'); undo that when it round-trips cleanly."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


_SUFFIX = re.compile(r"\s+([PQR])(\d)?\s+(\d+)$")      # " R1 2" / " Q1 3" / " P1 22"


def describe_replay(r: dict) -> dict:
    meta = r.get("metadata", {}) or {}
    name = fix_mojibake(r.get("replayName", ""))
    track = _SUFFIX.sub("", name)
    ts = r.get("timestamp", 0)
    return {
        "id": r["id"],
        "name": name,
        "track": track,
        "session": (meta.get("session") or "").title(),
        "event": meta.get("eventTitle") or ("Hosted" if meta.get("eventType") == "Hosted" else ""),
        "type": meta.get("eventType", ""),
        "split": meta.get("splitNo"),
        "size_mb": round(r.get("size", 0) / 1_048_576, 1),
        "date": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None,
        "timestamp": ts,
    }


class LMUClient:
    def __init__(self, base_url: str = "http://127.0.0.1:6397", timeout: float = 5.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: bytes | None = None, root: bool = False) -> Any:
        """`root=True` addresses the game's non-REST services (e.g. /navigation/...)."""
        prefix = "" if root else "/rest"
        req = urllib.request.Request(f"{self.base}{prefix}/{path.lstrip('/')}", method=method, data=body,
                                     headers={"Content-Type": "application/json"} if body is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise LMUApiError(e.read().decode("utf-8", "replace") or f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LMUApiError(f"LMU not reachable at {self.base} ({e})") from None
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return body

    # ---- library -----------------------------------------------
    def replays(self) -> list[dict]:
        data = self._call("GET", "watch/replays") or []
        return [describe_replay(r) for r in data]

    def play(self, replay_id: int) -> Any:
        return self._call("GET", f"watch/play/{int(replay_id)}")

    # ---- state -------------------------------------------------
    def reachable(self) -> bool:
        try:
            self._call("GET", "replay/isActive")
            return True
        except LMUApiError as e:
            return "not reachable" not in str(e)

    def replay_active(self) -> bool | None:
        try:
            return bool(self._call("GET", "replay/isActive"))
        except LMUApiError:
            return None

    def live_session(self) -> bool | None:
        """True when the game is in a session that is NOT a replay (someone is driving or in the garage): it must be left
        alone. False in the menus or a replay, None when it cannot be told."""
        if self.replay_active():
            return False                                   # a replay the app opened is not a live session
        try:
            state = self._call("GET", "sessions/GetGameState")
        except LMUApiError:
            return False                                   # "not in an active session"
        return True if isinstance(state, dict) and state else None

    def focus(self) -> int | None:
        try:
            v = self._call("GET", "watch/focus")
            return int(v) if v is not None and int(v) >= 0 else None
        except (LMUApiError, ValueError, TypeError):
            return None

    # ---- control -----------------------------------------------
    def set_focus(self, slot_id: int) -> None:
        self._call("PUT", f"watch/focus/{int(slot_id)}")

    def seek(self, t: float) -> None:
        self._call("PUT", f"watch/replaytime/{t}")

    def command(self, cmd: str) -> None:
        self._call("PUT", f"watch/replayCommand/{cmd}")

    def in_session(self) -> bool:
        """True while a session or replay is loaded (the game answers 'not in a session' at the menus)."""
        try:
            self._call("GET", "replay/isActive")
            return True
        except LMUApiError:
            return False

    def exit_to_main_menu(self) -> bool:
        """Leave the current session or replay exactly as the game's own Exit button does (read from its UI
        source): GET sessions/GetGameState, then POST navigation/action/NAV_BACK_TO_EVENT (multi-stint only)
        and POST navigation/action/NAV_TO_MAIN_MENU. Both POSTs are URL-only: no request body.
        Returns False (and does nothing) when the game is not in a session."""
        try:
            state = self._call("GET", "sessions/GetGameState") or {}
        except LMUApiError:
            return False                                   # 503 "not in an active session": nothing to exit
        if self.replay_active() is not True:               # `replay/isActive`, what every replay flow relies on (the state call's own
            return False                                   # isReplayActive stays false while a replay is open). No replay open and
                                                           # a session running means the user is driving: never exit that
        if isinstance(state, dict) and state.get("MultiStintState", "DISABLED") not in ("DISABLED", None):
            try:
                self._call("POST", "navigation/action/NAV_BACK_TO_EVENT", root=True)
            except LMUApiError:
                pass
            time.sleep(0.5)
        self._call("POST", "navigation/action/NAV_TO_MAIN_MENU", root=True)
        return True

    def set_camera(self, name: str) -> None:
        self._call("POST", f"replay/CameraController/setCameraByName/{name}")

    def camera_info(self) -> dict:
        return self._call("GET", "replay/CameraController/getCameraInfo") or {}

    def incidents(self, min_gap: float = 2.0) -> Any:
        return self._call("GET", f"watch/getIncidentsList/{min_gap}")

    def replay_folder(self) -> str | None:
        """Folder holding the replays (its sibling Log/Results holds the race result files)."""
        try:
            v = self._call("GET", "watch/replay/getReplayFolder")
            if isinstance(v, str) and v:
                return v.strip('"')
            if isinstance(v, dict):
                return next((x for x in v.values() if isinstance(x, str)), None)
        except LMUApiError:
            pass
        try:
            raw = self._call("GET", "watch/replays") or []
            return raw[0].get("replayDirectory") if raw else None
        except LMUApiError:
            return None

    def history(self) -> dict:
        return self._call("GET", "watch/standings/history") or {}

    def session_info(self) -> dict:
        return self._call("GET", "watch/sessionInfo") or {}

    def standings(self) -> list[dict]:
        return self._call("GET", "watch/standings") or []


class DemoLMU:
    """Stand-in for --demo so the replay browser can be exercised without the game."""

    def __init__(self) -> None:
        self.loaded: int | None = None

    def replays(self) -> list[dict]:
        base = [(0, "Interlagos", "Race", "LMGT3 Sprint Cup", 247.0, 1791425654),
                (1, "Interlagos", "Qualify", "LMGT3 Sprint Cup", 72.8, 1791425654),
                (2, "Lusail Short Circuit", "Race", "ELMS Sprint Trophy", 295.3, 1791359246),
                (3, "Circuit de la Sarthe", "Race", "LMGT3 Fixed", 126.1, 1790651840)]
        return [{"id": i, "name": f"{t} {s[0]}1 1", "track": t, "session": s, "event": e, "type": "daily",
                 "split": 1, "size_mb": mb, "date": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                 "timestamp": ts} for i, t, s, e, mb, ts in base]

    def play(self, replay_id: int):
        self.loaded = replay_id

    def reachable(self) -> bool: return True
    def replay_active(self): return self.loaded is not None
    def focus(self): return None
    def set_focus(self, slot_id): pass
    def seek(self, t): pass
    def command(self, cmd): pass
    def set_camera(self, name): pass
    def exit_to_main_menu(self): self.loaded = None; return True
    def live_session(self): return False
    def camera_info(self): return {}
    def incidents(self, min_gap=2.0): return []
    def history(self): return {}
    def replay_folder(self): return None
    def session_info(self): return {}
    def standings(self): return []
