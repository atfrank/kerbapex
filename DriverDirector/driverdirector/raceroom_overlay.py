"""The leaderboard and stats overlay for RaceRoom videos: the LMU overlay page (web/overlay) as an OBS browser source, fed live
from RaceRoom's shared memory while the replay plays. RaceRoom has no recorded history to look gaps up in, so gaps come from a
timeline built as the replay runs: exact to the car in front (when it passed this point), estimated from distance and speed to
a car behind."""
from __future__ import annotations

import asyncio
import bisect
import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect   # module level: FastAPI resolves
from fastapi.responses import FileResponse                                    # the annotations from here

from . import overlay
from .models import Car, Frame
from .raceroom_telemetry import TelemetryFrame

WEB = Path(__file__).parent / "web"


def track_length(tf: TelemetryFrame) -> float:
    """Lap length from the cars' distance and fraction (the shared memory has no length field we decode)."""
    vals = sorted(d.lap_distance / d.lap_fraction for d in tf.drivers if d.lap_fraction > 0.05)
    return vals[len(vals) // 2] if vals else 3500.0


def progress(laps: int, dist: float, length: float) -> float:
    return laps * 3 + dist / max(length, 1.0) * 3        # the LMU overlay counts three timing lines a lap


class LiveTimeline:
    """Same interface as history.Timeline, built while the replay plays. Each car's progress is sampled over time; the gap to a
    car ahead is how long ago it passed where this car is now."""

    def __init__(self) -> None:
        self._t: dict[int, list[float]] = {}
        self._p: dict[int, list[float]] = {}
        self._speed: dict[int, float] = {}
        self.length = 3500.0

    def add(self, t: float, cars: dict[int, tuple[float, float]]) -> None:
        for slot, (prog, speed) in cars.items():
            ts, ps = self._t.setdefault(slot, []), self._p.setdefault(slot, [])
            if ps and prog < ps[-1] - 0.5:                 # the replay wrapped: start again
                ts.clear()
                ps.clear()
            if not ps or prog > ps[-1]:
                ts.append(t)
                ps.append(prog)
            self._speed[slot] = speed

    def time_at(self, slot: int, prog: float) -> Optional[float]:
        ts, ps = self._t.get(slot), self._p.get(slot)
        if not ps or prog > ps[-1] or prog < ps[0]:
            return None
        i = bisect.bisect_left(ps, prog)
        if i == 0:
            return ts[0]
        p0, p1 = ps[i - 1], ps[i]
        return ts[i] if p1 <= p0 else ts[i - 1] + (ts[i] - ts[i - 1]) * (prog - p0) / (p1 - p0)

    def gap(self, me: int, me_progress: float, other: int, other_progress: float, t: float):
        """Seconds `other` is ahead of (negative) or behind (positive) `me`; whole laps (an int) from a lap away."""
        laps = (other_progress - me_progress) / 3.0
        if abs(laps) >= 1.0:
            return -int(laps) if laps > 0 else int(-laps)
        if other_progress >= me_progress:
            when = self.time_at(other, me_progress)
            if when is not None:
                return when - t
        metres = abs(other_progress - me_progress) / 3.0 * self.length          # no history yet: distance over speed
        speed = max(self._speed.get(other if other_progress < me_progress else me, 0.0), 15.0)
        gap = metres / speed
        return gap if other_progress < me_progress else -gap


def to_frame(tf: TelemetryFrame, t: float) -> Frame:
    """The telemetry as the cars of the LMU data model. Positions follow the order on track (laps, then distance): the
    game's own place counter lags at the timing lines."""
    length = track_length(tf)
    ranked = sorted(tf.drivers, key=lambda d: (-d.laps, -d.lap_distance))
    cars = [Car(id=d.index, driver=d.name, number=str(d.car_number or ""), car_class="", place=i + 1, class_place=i + 1,
                laps=d.laps, lap_dist=d.lap_distance, speed=d.speed, in_pits=d.pit_lane, best_lap=d.best_lap,
                last_lap=d.last_lap) for i, d in enumerate(ranked)]
    return Frame(t=t, track_len=length, cars=cars)


class LiveOverlay:
    """Turns telemetry frames into overlay states and hands them to a sink (the websocket broadcast)."""

    def __init__(self, focus_name: str, push, ahead: int = 6, behind: int = 4) -> None:
        self.focus_name, self.push = focus_name, push
        self.ahead, self.behind = ahead, behind
        self.timeline = LiveTimeline()
        self.t0: Optional[float] = None

    def update(self, tf: TelemetryFrame, now: float) -> dict:
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        frame = to_frame(tf, t)
        self.timeline.length = frame.track_len
        self.timeline.add(t, {c.id: (progress(c.laps, c.lap_dist, frame.track_len), c.speed) for c in frame.cars})
        me = next((c for c in frame.cars if c.driver.lower().endswith(self.focus_name.split()[-1].lower())), None)
        state = overlay.build_state(frame, me.id if me else -1, timeline=self.timeline, ahead=self.ahead,
                                    behind=self.behind, race_info=(0.0, 0, 0, 0))
        if state.get("focus") and me:
            state["focus"]["speed_kmh"] = round(me.speed * 3.6)
        self.push(state)
        return state


class OverlayServer:
    """Serves the overlay page and its websocket on a local port, in a background thread. `push(state)` broadcasts."""

    def __init__(self, port: int = 6411, theme: str = "default", alpha: float | None = None) -> None:
        self.port, self.theme, self.alpha = port, theme, alpha
        self.state: dict = {"mode": "main", "tower": [], "focus": None, "race": None}
        self._clients: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._thread: Optional[threading.Thread] = None

    def _app(self):
        app = FastAPI(title="RaceRoom overlay")

        @app.on_event("startup")
        async def _up() -> None:
            self._loop = asyncio.get_running_loop()

        @app.websocket("/ws")
        async def ws(sock: WebSocket):
            await sock.accept()
            self._clients.add(sock)
            try:
                await sock.send_text(json.dumps(self.state))
                while True:
                    await sock.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(sock)

        @app.get("/theme/{name}/{file}")
        def theme_file(name: str, file: str):
            base = WEB / "overlay" / "themes"
            p = (base / name / file).resolve()
            if p.is_file() and base.resolve() in p.parents:
                return FileResponse(p)
            raise HTTPException(404)

        @app.get("/overlay")
        def page():
            return FileResponse(WEB / "overlay" / "index.html")
        return app

    def start(self) -> None:
        import time
        import uvicorn
        self._server = uvicorn.Server(uvicorn.Config(self._app(), host="127.0.0.1", port=self.port, log_level="error"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return
            time.sleep(0.1)
        raise RuntimeError(f"the overlay server did not start on port {self.port}")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True

    async def _broadcast(self, msg: str) -> None:
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                self._clients.discard(ws)

    def push(self, state: dict) -> None:
        self.state = state
        if self._loop and self._clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(json.dumps(state)), self._loop)

    def url(self, width: int, height: int, zoom: float) -> str:
        look = "" if self.alpha is None else f"&alpha={self.alpha}"
        return f"http://localhost:{self.port}/overlay?theme={self.theme}&w={width}&h={height}&zoom={zoom}&bars=0{look}"
