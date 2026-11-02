"""HTTP + WebSocket server: control panel, REST API, and the overlay served as an OBS browser source."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, data_dir
from . import launcher
from .director import Director
from .lmu_api import LMUApiError

WEB = Path(__file__).parent / "web"


def create_app(director: Director) -> FastAPI:
    app = FastAPI(title="DriverDirector")
    clients: set[WebSocket] = set()
    loop_holder: dict = {}
    job: dict = {"thread": None}

    async def _broadcast(msg: str) -> None:
        for ws in list(clients):
            try:
                await ws.send_text(msg)
            except Exception:
                clients.discard(ws)

    def on_overlay(state: dict) -> None:
        loop = loop_holder.get("loop")
        if loop and clients:
            asyncio.run_coroutine_threadsafe(_broadcast(json.dumps(state)), loop)

    director.on_overlay = on_overlay

    @app.on_event("startup")
    async def _startup() -> None:
        loop_holder["loop"] = asyncio.get_running_loop()

    def run_job(fn, *a) -> dict:
        t = job["thread"]
        if t and t.is_alive():
            raise HTTPException(409, "a job is already running")
        director.cancel = False

        def wrap():
            try:
                fn(*a)
            except Exception as e:  # surface to the UI instead of dying silently
                director._set("error", f"{type(e).__name__}: {e}")
        job["thread"] = threading.Thread(target=wrap, daemon=True)
        job["thread"].start()
        return {"started": True}

    # ---- status / settings -----------------------------------
    @app.get("/api/status")
    def status():
        return {**director.status, "frames": len(director.frames), "events": len(director.events),
                "plan": director.plan.summary() if director.plan else None,
                "obs": director.obs.status(), "final": director.final_path}

    @app.get("/api/settings")
    def get_settings():
        return director.s.model_dump()

    @app.put("/api/settings")
    def put_settings(body: dict):
        director.s = Settings(**body)
        director.s.save()
        return director.s.model_dump()

    @app.get("/api/templates")
    def templates():
        return Settings.list_templates()

    @app.post("/api/templates/{name}")
    def save_template(name: str):
        return {"saved": str(director.s.save_template(name))}

    @app.post("/api/templates/{name}/load")
    def load_template(name: str):
        obs = director.s.obs
        director.s = Settings.load_template(name)
        director.s.obs = obs                      # connection details never come from templates
        return director.s.model_dump()

    @app.get("/api/themes")
    def themes():
        names = {p.name for p in (WEB / "overlay" / "themes").iterdir() if p.is_dir()}
        user = data_dir() / "themes"
        if user.exists():
            names |= {p.name for p in user.iterdir() if p.is_dir()}
        return sorted(names)

    # ---- replay library ----------------------------------
    @app.get("/api/replays")
    def replays():
        try:
            return sorted(director.lmu.replays(), key=lambda r: -r["timestamp"])
        except LMUApiError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/replays/{replay_id}/load")
    def load_replay(replay_id: int):
        try:
            director.lmu.play(replay_id)
        except LMUApiError as e:
            raise HTTPException(502, str(e))
        director.note_race_replay(replay_id)
        director.frames, director.events, director.plan = [], [], None   # new session: drop old analysis
        director.mode, director.history = "live", None
        director._set("replay", f"loading replay {replay_id}")
        return {"loading": replay_id}

    @app.get("/api/game")
    def game():
        active = director.lmu.replay_active()
        return {"running": launcher.is_running(), "obs_running": launcher.obs_running(),
                "api": director.lmu.reachable(),
                "replay_active": active,
                "live_session": director.lmu.live_session(),    # someone is driving: the app never touches such a session
                # what is on screen ("replay" | "menu" | "unknown"): the game can report a replay as open while it still
                # shows the main menu, so callers must look at the screen too
                "screen": director.screen_state() if active else None}

    @app.post("/api/game/exit_replay")
    def exit_replay():
        try:
            return {"exited": director.lmu.exit_to_main_menu()}
        except LMUApiError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/game/close")
    def close_game():
        return {"closing": launcher.close()}

    @app.post("/api/obs/launch")
    def launch_obs():
        o = director.s.obs
        try:
            started = launcher.launch_obs(o.exe_path, o.launch_scene)
        except OSError as e:
            raise HTTPException(500, str(e))
        return {"launched": started, "already_running": not started}

    @app.post("/api/game/launch")
    def launch_game():
        try:
            started = launcher.launch(director.s.steam_app_id)
        except OSError as e:
            raise HTTPException(500, str(e))
        return {"launched": started, "already_running": not started}

    # ---- pipeline ----------------------------------------
    @app.get("/api/roster")
    def roster():
        return director.roster()

    @app.post("/api/history")
    def history(body: dict | None = None):
        try:
            return director.load_history((body or {}).get("race_start"))
        except (ValueError, LMUApiError) as e:
            raise HTTPException(400 if isinstance(e, ValueError) else 502, str(e))

    @app.get("/api/focus_events")
    def focus_events():
        if not director.plan:
            raise HTTPException(404, "no plan")
        p = director.plan
        return [{"kind": e.kind, "t": round(e.t_start, 1), "desc": e.desc, "score": e.score,
                 "confidence": e.confidence} for e in p.events if e.involves(p.focus_id)]

    @app.post("/api/scan")
    def scan(body: dict | None = None):
        body = body or {}
        return run_job(director.scan, 8.0, body.get("log"))

    @app.post("/api/load_log")
    def load_log(body: dict):
        return {"frames": director.load_log(body["path"])}

    @app.post("/api/plan")
    def plan():
        if not director.frames:
            raise HTTPException(400, "scan or load a log first")
        try:
            director.analyze()
            return director.make_plan().summary()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/plan")
    def get_plan():
        if not director.plan:
            raise HTTPException(404, "no plan")
        p = director.plan
        return {"summary": p.summary(), "driving": {"note": director.driving_note, "mistakes": [
                    {"lap": m.lap, "at": round(m.t0, 1), "loss": round(m.loss, 2), "where": m.where,
                     "causes": m.causes, "desc": m.desc} for m in director.mistakes]},
                "cues": [asdict(c) for c in p.cues],
                "inlays": [{**asdict(i.event), "show_at": i.show_at, "show_until": i.show_until,
                            "camera": i.camera, "label": i.label, "slow": i.slow, "names": list(i.names),
                            "replay": i.replay} for i in p.inlays],
                "events": [asdict(e) for e in p.events]}

    @app.post("/api/preview")
    def preview(body: dict | None = None):
        return run_job(director.preview, float((body or {}).get("speed", 10)))

    @app.get("/api/cleanup")
    def cleanup_status():
        from . import cleanup
        d = director.output_dir
        return {"folder": str(d), "candidates": cleanup.candidates(d, director.protected_paths()),
                "quarantined_bytes": cleanup.quarantined_size(d), "keep_days": director.s.cleanup_keep_days}

    @app.post("/api/cleanup")
    def cleanup_run(body: dict | None = None):
        """{"sweep": true} moves the intermediate recordings aside; {"purge_days": N} deletes quarantined batches
        older than N days (0 = all of them). Finished videos and an unfinished job's recordings are never touched."""
        from . import cleanup
        body = body or {}
        d = director.output_dir
        out: dict = {"folder": str(d), "moved": [], "purged": []}
        if body.get("sweep"):
            out["moved"] = cleanup.sweep([c["path"] for c in cleanup.candidates(d, director.protected_paths())], d)
        if body.get("purge_days") is not None:
            out["purged"] = cleanup.purge(d, float(body["purge_days"]))
        out["quarantined_bytes"] = cleanup.quarantined_size(d)
        return out

    @app.post("/api/preflight")
    def preflight():
        return director.preflight()

    @app.post("/api/run")
    def run():
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        return run_job(director.run_all)

    @app.post("/api/rerecord_clips")
    def rerecord_clips():
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        if not director.manifest_path.is_file():
            raise HTTPException(400, "nothing recorded yet")
        return run_job(director.rerecord_clips)

    @app.post("/api/rerecord_reference")
    def rerecord_reference():
        """Record only the reference lap again, then finish the video with everything else as it was."""
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        if not director.manifest_path.is_file():
            raise HTTPException(400, "nothing recorded yet")
        return run_job(director.rerecord_reference)

    @app.post("/api/redo_commentary")
    def redo_commentary():
        """Write, voice and mix the commentary again over the last recording (nothing is recorded)."""
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        if not director.manifest_path.is_file():
            raise HTTPException(400, "nothing recorded yet")
        return run_job(director.redo_commentary)

    @app.post("/api/produce")
    def produce():
        if not (director.plan and director.main_path) and not director.restore_recording():
            raise HTTPException(400, "nothing recorded yet")
        return run_job(director.produce)

    @app.get("/api/commentary/scenes")
    def commentary_scenes():
        """The scenes of the current plan: when each happens, how long it has, and the facts to talk about."""
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        from . import commentary as cm
        scenes = director.commentary_scenes()
        wps = director.s.commentary.words_per_second
        return {"scenes": [{**d, "max_words": int(d["slot"] * wps)} for d in cm.scenes_to_json(scenes)]}

    @app.post("/api/commentary/script")
    def commentary_script(body: dict):
        """Supply the commentary script: {"lines": {"<scene id>": "<text>", ...}}. Checked like the writer's own output."""
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        r = director.save_script(body.get("lines") or {})
        if not r["saved"]:
            raise HTTPException(422, "; ".join(r["errors"][:12]))
        return r

    @app.post("/api/highlights")
    def highlights(body: dict | None = None):
        body = body or {}
        return run_job(director.make_highlights, body.get("video"), float(body.get("seconds", 240)))

    @app.post("/api/shorts")
    def shorts(body: dict | None = None):
        return run_job(director.make_shorts, (body or {}).get("video"))

    @app.post("/api/sync_test")
    def sync_test(body: dict | None = None):
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        delays = tuple(float(x) for x in (body or {}).get("delays", (0.4, 1.0, 1.6)))
        return run_job(director.sync_test, delays)

    @app.post("/api/recording/rebuild")
    def rebuild_recording(body: dict):
        """Rebuild the recording info of the planned race from the raw files in a folder (see Director.rebuild_recording)."""
        if not director.plan:
            raise HTTPException(400, "make a plan first")
        try:
            return director.rebuild_recording(body.get("folder", ""))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/estimate")
    def estimate():
        if not director.plan:
            raise HTTPException(404, "no plan")
        return director.estimate()

    # ---- YouTube -----------------------------------------------
    @app.get("/api/youtube")
    def youtube_status():
        from . import youtube
        return youtube.status(director.s.youtube)

    @app.post("/api/youtube/connect")
    def youtube_connect():
        from . import youtube
        try:
            youtube.load_client(director.s.youtube)                # fail fast, before opening a browser
        except youtube.YouTubeError as e:
            raise HTTPException(400, str(e))
        return run_job(director.youtube_authorize)

    @app.post("/api/youtube/disconnect")
    def youtube_disconnect():
        from . import youtube
        return {"disconnected": youtube.disconnect()}

    @app.get("/api/youtube/draft")
    def youtube_draft(video: str | None = None):
        try:
            return director.youtube_draft(video)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/youtube/upload")
    def youtube_upload(body: dict):
        """Publishes to the user's channel, so it only runs when the request says `confirm: true`."""
        from pathlib import Path as _P
        from . import youtube
        if body.get("confirm") is not True:
            raise HTTPException(400, "uploading publishes to your YouTube channel; send confirm=true after the "
                                     "person has agreed to this title, description and privacy")
        video, title = body.get("video") or director.final_path, (body.get("title") or "").strip()
        if not video or not _P(video).is_file():
            raise HTTPException(400, "no finished video to upload; pass video=<path>")
        if _P(video).suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
            raise HTTPException(400, "only video files can be uploaded")
        if not title:
            raise HTTPException(400, "a title is required")
        privacy = body.get("privacy") or director.s.youtube.privacy
        if privacy not in youtube.PRIVACY:
            raise HTTPException(400, f"privacy must be one of {youtube.PRIVACY}")
        return run_job(director.publish_youtube, video, title, body.get("description", ""), privacy,
                       body.get("tags"))

    @app.get("/api/tools")
    def tools():
        import shutil
        from . import commentary as cm
        exe = cm.find_claude_cli(director.s.commentary.cli_path)
        # claude_logged_in: False means the commentary writer cannot work until the user signs in again (`claude auth login`)
        return {"ffmpeg": shutil.which("ffmpeg"), "claude_cli": exe,
                "claude_cli_terminal_path": cm.terminal_path(exe) if exe else None,
                "claude_logged_in": cm.claude_logged_in(exe) if exe and director.s.commentary.backend == "cli" else None}

    @app.get("/api/music")
    def music_info():
        from . import music, tts
        cfg = director.s.music
        return {"source": cfg.source, "model_downloaded": music.find_model(cfg) is not None,
                "ready": (music.cached_cloud_loop(cfg) if cfg.source == "elevenlabs" else music.cached_loop(cfg)) is not None,
                "runtime": tts.find_runtime(director.s.commentary) is not None}

    @app.post("/api/music/prepare")
    def music_prepare():
        return run_job(director.prepare_music)

    @app.post("/api/music/test")
    def music_test(body: dict | None = None):
        """Make a short instrumental with Eleven Music (the prompt is sent to ElevenLabs; paid plans) so it can be heard
        before it is used. The track is cached, so asking again for the same prompt, model and length costs nothing."""
        from . import music
        b = body or {}
        secs = float(b.get("seconds", 30))
        over = {k: v for k, v in (("elevenlabs_model", b.get("model")), ("prompt", b.get("prompt"))) if v}
        cfg = director.s.music.model_copy(update=over)
        try:
            f = music.generate_elevenlabs(cfg, secs)
        except RuntimeError as e:
            raise HTTPException(502, str(e))
        return {"file": str(f), "bytes": f.stat().st_size, "model": cfg.elevenlabs_model}

    @app.get("/api/voices")
    def voices():
        from . import tts
        return {"voices": tts.list_voices(director.s.commentary),   # [{value, label}]
                "chatterbox": tts.find_runtime(director.s.commentary) is not None,
                "cloud_keys": tts.cloud_keys_present(),            # {engine: True/False}; never the keys themselves
                "free_ram_gb": round(tts.free_ram_gb(), 1)}

    @app.get("/api/voices/elevenlabs")
    def elevenlabs_voices():
        """The voices of the user's ElevenLabs account (to choose the analyst's voice)."""
        from . import tts
        try:
            return {"current": director.s.commentary.elevenlabs_voice_id, "voices": tts.list_elevenlabs_voices()}
        except RuntimeError as e:
            raise HTTPException(502, str(e))

    @app.post("/api/voices/test")
    def voice_test(body: dict | None = None):
        """Voice one short line with the chosen engine and save it, to hear a voice before a full render.
        Cloud engines send this one line of text to the service."""
        from . import tts
        b = body or {}
        engine = b.get("engine") or director.s.commentary.tts_engine
        text = (b.get("text") or "Alex Hartley carries too much speed into the Bus Stop and runs wide.")[:300]
        if engine not in ("chatterbox", "edge", *tts.CLOUD):
            raise HTTPException(400, "engine must be chatterbox, edge, fish or elevenlabs")
        cfg = director.s.commentary.model_copy(update={"tts_engine": engine})
        wd = data_dir() / "work" / "voice_test"
        try:
            files, used = tts.synthesize([tts.Line(0, text, int(b.get("excite", 0)))], cfg, wd)
        except RuntimeError as e:
            raise HTTPException(502, str(e))
        return {"engine": used, "file": str(files[0]), "bytes": files[0].stat().st_size}

    @app.post("/api/obs/crop")
    def obs_crop(body: dict | None = None):
        o = director.s.obs
        b = body or {}
        top, bottom = float(b.get("top", o.crop_top)), float(b.get("bottom", o.crop_bottom))
        if b.get("reset"):
            top = bottom = 0.0
        try:
            info = director.obs.set_capture_crop(o.main_scene, o.capture_source, top, bottom)
        except Exception as e:                       # OBS not running, scene/source missing...
            raise HTTPException(502, f"OBS: {e}")
        return {"top": top, "bottom": bottom, **info}

    @app.post("/api/cancel")
    def cancel():
        director.cancel = True
        return {"ok": True}

    # ---- overlay -----------------------------------------
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        clients.add(sock)
        try:
            await sock.send_text(json.dumps(director.overlay_state))
            while True:
                await sock.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(sock)

    @app.get("/theme/{name}/{file}")
    def theme_file(name: str, file: str):
        for base in (data_dir() / "themes", WEB / "overlay" / "themes"):
            p = (base / name / file).resolve()
            if p.is_file() and base.resolve() in p.parents:
                return FileResponse(p)
        raise HTTPException(404)

    @app.get("/overlay")
    def overlay_page():
        return FileResponse(WEB / "overlay" / "index.html")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
