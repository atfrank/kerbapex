"""The StintRadio app: the coach service behind a small local HTTP API, and the live page at / (for the Steam overlay)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .config import Settings, data_dir
from . import laps, sims
from .service import LiveService, find_game_dir

WEB = Path(__file__).parent / "web"


def create_app(s: Settings) -> FastAPI:
    app = FastAPI(title="StintRadio")
    live: dict = {"svc": None, "waiting": False}

    # the overlay window's layout (shape, size, place) and its edit mode: kept here so the window and its page agree, and
    # so a layout set by dragging it is there next time
    overlay_file = data_dir() / "overlay.json"
    overlay: dict = {"edit": False, "brand": True, "border": False, "theme": "dark", "geom": None, "opacity": None, "quit": False}
    overlay_seen = {"at": 0.0}                 # when the overlay window last said it is there
    try:
        overlay.update({k: v for k, v in json.loads(overlay_file.read_text(encoding="utf-8")).items() if k in ("brand", "border", "geom", "theme", "opacity")})
    except (OSError, ValueError):
        pass

    def service() -> LiveService:
        if live["svc"] is None or not live["svc"].game_dir:      # a test replay may have made one without it
            gd = find_game_dir(s.live.game_dir) or ""
            if not gd and not sims.any_available(s.live):
                raise HTTPException(400, "No supported sim was found; start RaceRoom, or set live.game_dir to the Le Mans Ultimate "
                                         "install folder (or install a sim adapter: python -m stintradio.sims list)")
            if live["svc"] is None:
                live["svc"] = LiveService(gd, s.live, s.voice)
            live["svc"].game_dir = gd
        live["svc"].cfg, live["svc"].commentary_cfg = s.live, s.voice
        return live["svc"]

    def start_when_ready(every: float = 5.0) -> None:
        """Keep trying until the game's live data is there (the app may start before the game does)."""
        live["waiting"] = True
        try:
            while live["waiting"]:
                try:
                    service().start()
                    return
                except (OSError, HTTPException):
                    time.sleep(every)
        finally:
            live["waiting"] = False

    app.state.start_when_ready = lambda: threading.Thread(target=start_when_ready, daemon=True).start()

    @app.post("/api/start")
    def start(body: dict | None = Body(default=None)):
        """Start the coach. With {"wait": true} it does not need the game yet: it keeps trying until the game's live data is there."""
        if body and body.get("wait"):
            running = live["svc"] is not None and live["svc"].thread is not None and live["svc"].thread.is_alive()
            if not running and not live["waiting"]:
                app.state.start_when_ready()
            return {"waiting": not running, "running": running}
        try:
            return service().start()
        except OSError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/stop")
    def stop():
        live["waiting"] = False
        return live["svc"].stop() if live["svc"] else {"running": False}

    @app.get("/api/facts")
    def facts():
        """What the engineer would be told right now when asked a question, and the last questions and answers."""
        from . import ask
        svc = live["svc"]
        if svc is None:
            return {"facts": "", "qa": []}
        return {"facts": ask.facts(svc), "qa": svc.qa[-8:]}

    @app.get("/api/tips")
    def tips_report(session: str = ""):
        """Brake bias, shift timing and minimum-speed suggestions from the newest recorded session (or the named one)."""
        from . import tips
        from .recorder import sessions_dir
        path = (sessions_dir() / Path(session).name) if session else tips.latest()
        if path is None or not path.is_file():
            return {"lines": ["No recorded session yet."]}
        return tips.analyse(path)

    @app.get("/api/briefing")
    def briefing(kind: str = "", track: str = "", car: str = "", say: bool = False):
        """What the engineer would say at the start of a session, from earlier runs only (to hear it without driving: say=true)."""
        svc = live["svc"]
        if svc is None:
            svc = LiveService(find_game_dir(s.live.game_dir) or "", s.live, s.voice)          # for the text only: it is not started
        return svc.preview_briefing(kind or None, track or None, car or None, say)

    @app.get("/api/ffb")
    def ffb_status():
        """The force-feedback verdict so far, the wheel profile and the change it would make (nothing is written)."""
        from . import ffb_apply as FA
        svc = live["svc"]
        stats = svc.ffb.stats() if svc else None
        found = FA.profiles()
        out = {"stats": stats, "profiles": {n: {"strength": p["strength"], "device": p["device"]} for n, p in found.items()},
               "game_running": FA.game_running()}
        try:
            name = FA.pick_profile(found, s.live.ffb_profile)
        except FA.FFBError as e:
            out["profile_problem"] = str(e)
            return out
        cur = found[name]["strength"]
        out["profile"] = name
        out["strength"] = cur
        out["proposed"] = FA.propose(cur, stats["scale"]) if stats else None
        return out

    @app.post("/api/ffb/apply")
    def ffb_apply(body: dict):
        """Write the proposed FFB Strength into the wheel profile. Needs {"confirm": true}; RaceRoom must be closed."""
        from . import ffb_apply as FA
        if body.get("confirm") is not True:
            raise HTTPException(400, "confirm: true is required; look at /api/ffb first")
        status = ffb_status()
        if status.get("profile_problem"):
            raise HTTPException(400, status["profile_problem"])
        if status["proposed"] is None or status["proposed"] == status["strength"]:
            raise HTTPException(400, "no change is called for (the force feedback looks well set, or there is not enough data)")
        try:
            return FA.apply(FA.profiles()[status["profile"]]["file"], status["proposed"])
        except FA.FFBError as e:
            raise HTTPException(400, str(e))

    LOCAL = {"127.0.0.1", "::1", "localhost", "testclient"}
    OPEN_TO_LAN = {"/race", "/api/race", "/api/track"}   # all a second screen needs: read-only

    @app.middleware("http")
    async def lan_guard(request: Request, call_next):
        """Other devices on the network (the laptop) may only read the race page and its data; everything else, the
        controls included, answers only on this PC."""
        host = request.client.host if request.client else ""
        if host not in LOCAL and not (request.method == "GET" and request.url.path in OPEN_TO_LAN):
            return JSONResponse({"detail": "this address only serves /race to other devices"}, status_code=403)
        return await call_next(request)

    @app.get("/api/race")
    def race():
        if live["svc"]:
            out = live["svc"].race_view()
            if live["waiting"] and out.get("status") == "stopped":
                out["status"] = "waiting"
            return out
        return {"status": "waiting for the game" if live["waiting"] else "stopped"}

    @app.get("/api/track")
    def track():
        """The drawing behind the race page's track map; {} until there is one."""
        return live["svc"].track_map() if live["svc"] else {}

    @app.get("/api/overlay")
    def overlay_get():
        return {**overlay, "alive": time.time() - overlay_seen["at"] < 4.0}

    @app.put("/api/overlay")
    async def overlay_put(request: Request):
        """The overlay window reports its edit mode and layout; only this PC may (see lan_guard)."""
        body = await request.json()
        if isinstance(body.get("edit"), bool):
            overlay["edit"] = body["edit"]
        if isinstance(body.get("brand"), bool):
            overlay["brand"] = body["brand"]
        if isinstance(body.get("border"), bool):
            overlay["border"] = body["border"]
        if "opacity" in body:                              # how see-through the whole window is; null = the window's own default
            op = body["opacity"]
            overlay["opacity"] = None if op is None else (round(min(1.0, max(0.3, float(op))), 2) if isinstance(op, (int, float)) and not isinstance(op, bool) else overlay["opacity"])
        if body.get("theme") in ("light", "dark"):
            overlay["theme"] = body["theme"]
        if isinstance(body.get("quit"), bool):
            overlay["quit"] = body["quit"]                 # true asks the overlay window to close itself
        if body.get("ping"):
            overlay_seen["at"] = time.time()
        if body.get("gone"):
            overlay_seen["at"] = 0.0                       # the overlay window says goodbye: not "alive" any more
        g = body.get("geom")
        if isinstance(g, dict) and all(isinstance(g.get(k), (int, float)) for k in ("w", "h", "dx", "dy")) \
                and g.get("anchor") in ("top-left", "top-right", "bottom-left", "bottom-right"):
            overlay["geom"] = {k: g[k] for k in ("anchor", "w", "h", "dx", "dy")}
        try:
            overlay_file.write_text(json.dumps({"brand": overlay["brand"], "border": overlay["border"], "theme": overlay["theme"], "geom": overlay["geom"], "opacity": overlay["opacity"]}), encoding="utf-8")
        except OSError:
            pass
        return {**overlay, "alive": time.time() - overlay_seen["at"] < 4.0}

    app.state.exit = lambda: os._exit(0)            # replaced in tests

    @app.post("/api/quit")
    def quit_app():
        """Stop the coach and close this program (the control panel's Stop button); only this PC may (see lan_guard)."""
        if live["svc"]:
            try:
                live["svc"].stop()
            except Exception:
                pass
        threading.Timer(0.4, lambda: app.state.exit()).start()
        return {"quitting": True}

    @app.get("/overlay")
    def overlay_page():
        """The compact map, delta and sectors for the window laid over the game (python -m stintradio overlay)."""
        return FileResponse(WEB / "overlay.html", headers={"Cache-Control": "no-store"})

    @app.get("/trackmap.js")
    def trackmap_js():
        return FileResponse(WEB / "trackmap.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @app.get("/race")
    def race_page():
        return FileResponse(WEB / "race.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def state():
        if live["svc"]:
            out = live["svc"].state()
            if live["waiting"] and out.get("status") == "stopped":
                out["status"] = "waiting"                           # asked to start, the game's data is not there yet
            return out
        return {"status": "waiting" if live["waiting"] else "stopped"}

    @app.post("/api/replay")
    def replay(body: dict):
        """Play a stored lap through the coach: {"track", "car", "rank" (0 = fastest), "track_len", "speed"}."""
        found = sorted((laps.load(p) for p in laps.stored(body.get("track", ""), body.get("car", ""))), key=lambda l: l.time)
        rank = int(body.get("rank", 1))
        if len(found) <= rank:
            raise HTTPException(404, "no such stored lap")
        svc = live["svc"]
        if svc is None:
            svc = live["svc"] = LiveService("", s.live, s.voice)
        return svc.replay(found[rank], float(body.get("track_len", 0) or 0), float(body.get("speed", 1.0)))

    @app.post("/api/import-coach-dave")
    def import_coach_dave():
        return laps.import_coach_dave()

    @app.get("/api/settings")
    def get_settings():
        return s.model_dump()

    @app.put("/api/settings")
    def put_settings(body: dict):
        """Replace the settings (they apply from the next start of the coach)."""
        try:
            new = Settings.model_validate(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        s.live, s.voice = new.live, new.voice
        s.save()
        return s.model_dump()

    @app.get("/api/health")
    def health():
        return {"app": "StintRadio", "game_dir": find_game_dir(s.live.game_dir) or "",
                "voice_engine": s.live.voice_engine, "style": s.live.style}

    @app.get("/")
    def page():
        return FileResponse(WEB / "index.html")

    return app
