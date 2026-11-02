from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
import webbrowser

from .config import Settings
from .director import Director


def make_source(demo: bool, focus_provider=None):
    """Prefer LMU's native shared memory; fall back to the rF2 plugin buffers."""
    if demo:
        from .sources.simulator import Simulator
        return Simulator()
    from .sources.lmu_native import LMUSharedMemory
    try:
        return LMUSharedMemory(focus_provider)
    except OSError:
        from .sources.rf2shm import RF2SharedMemory
        return RF2SharedMemory()


def cmd_doctor() -> int:
    from .sources import rf2shm
    rep = rf2shm.layout_report()
    ok = rep["vehicle_size"] == rep["vehicle_expected"] and rep["info_size"] == rep["info_expected"]
    print("struct layout:", rep, "OK" if ok else "MISMATCH")
    s = Settings()
    from .lmu_api import LMUClient
    from .sources.lmu_native import LMUSharedMemory
    api = LMUClient(s.rest_url)
    print("REST API:", "reachable" if api.reachable() else "NOT reachable", "| replay active:", api.replay_active())
    try:
        src = LMUSharedMemory(api.focus)
        fr = src.poll()
        print("LMU_Data shared memory: connected, layout", src.check() or "OK",
              f"| {len(fr.cars)} cars, t={fr.t:.1f}, phase={fr.phase}, focus={fr.focus_id}" if fr else "| no session data")
    except Exception as e:
        print("LMU_Data shared memory: not available -", e)
    from .obs import Obs
    print("OBS:", Obs(s.obs).status())
    return 0


def cmd_raceroom(args: list[str]) -> int:
    """RaceRoom mode from the command line (see raceroom.py). `make` plays the replay in the game and records it."""
    import json
    from . import raceroom as rr
    from .obs import Obs
    s = Settings.load()
    cmd = args[0] if args else "list"
    if cmd == "list":
        for r in rr.list_replays(include_best="--all" in args):
            print(f"{r.name:70} {r.session:11} {r.duration / 60:5.1f} min  P{r.finish or '-'}  {r.car}")
        return 0
    obs = Obs(s.obs)
    if cmd == "scene":
        print("added:", rr.ensure_scene(obs, s.raceroom) or "nothing (already set up)")
        return 0
    if cmd == "make":
        info = rr.find_replay(args[1])
        if "--start" in args:
            s.raceroom.t_start = float(args[args.index("--start") + 1]) * 60
        if "--minutes" in args:
            s.raceroom.t_end = s.raceroom.t_start + float(args[args.index("--minutes") + 1]) * 60
        game = rr.RaceRoomGame(s.raceroom.process)
        out = s.obs.output_dir or str(Path.home() / "Videos" / "DriverDirector")
        cfg = s.raceroom
        srv = live = None
        url = ""
        if cfg.overlay and "--no-overlay" not in args:   # the leaderboard: the LMU overlay page, fed from the telemetry
            from .raceroom_overlay import LiveOverlay, OverlayServer
            srv = OverlayServer(cfg.overlay_port, alpha=cfg.overlay_alpha)
            srv.start()
            url = srv.url(*obs._canvas(), cfg.overlay_zoom)
            live = LiveOverlay(cfg.driver, srv.push)
        rr.ensure_scene(obs, cfg, url)
        kurl = args[args.index("--kerbapex") + 1] if "--kerbapex" in args else ""
        if kurl:                                      # the KerbApex overlay (and, from the same address, its engineer) in the main picture
            rr.ensure_kerbapex(obs, cfg, kurl)
        else:
            obs.set_item_enabled(cfg.scene, cfg.kerbapex_source, False)
        if "--restart" in args:
            print("restarting RaceRoom", flush=True)
            game.restart()
        try:
            from .raceroom_telemetry import R3EMemory
            tmem = R3EMemory()                        # only exists once the game runs, so open it after the restart
        except OSError:
            tmem = None
        two = cfg.inset and "--no-inset" not in args     # the standard video: onboard main picture + other views in a box
        say = lambda f, m: print(f"{f:4.0%} {m}", flush=True)
        inset = None
        try:
            rec = rr.record(info, game, obs, cfg, load="--no-load" not in args, telemetry=tmem,
                            live=live if tmem else None, progress=say, weights=cfg.main_weights if two else None)
            print("recorded:", rec.raw, flush=True)
            if two:                                   # the same replay again, other cameras, no overlay
                print("second pass: other views", flush=True)
                obs.set_item_enabled(cfg.scene, cfg.kerbapex_source, False)      # the box of other views carries no overlay
                if srv:
                    srv.push({"mode": "main", "tower": [], "focus": None, "race": None})
                game.restart()
                try:
                    from .raceroom_telemetry import R3EMemory
                    tmem = R3EMemory()
                except OSError:
                    tmem = None
                inset = rr.record(info, game, obs, cfg, load="--no-load" not in args, telemetry=tmem, progress=say,
                                  seed=2, weights=cfg.inset_weights)
                print("recorded:", inset.raw, flush=True)
        finally:
            if srv:
                srv.stop()
        video = rr.finish(info, rec, cfg, out, progress=lambda f, m="": print(f"finish {f:4.0%}", flush=True), inset=inset)
        print("video:", video)
        if "--stay" not in args:                      # a replay loops for ever: close the game when the video is made
            game.quit()
        return 0
    if cmd == "upload":                               # upload VIDEO NAME [--privacy private|unlisted|public]
        import subprocess
        from . import youtube
        video, info = args[1], rr.find_replay(args[2])
        privacy = args[args.index("--privacy") + 1] if "--privacy" in args else "private"
        length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                                      capture_output=True, text=True, check=True).stdout.strip() or 0)
        d = rr.draft(info, s.raceroom, s.youtube, rr.find_report(info), length=length)
        shown = [0.0]

        def prog(done, total):
            if time.time() - shown[0] > 30:
                shown[0] = time.time()
                print(f"uploaded {done / max(total, 1):.0%}", flush=True)
        res = youtube.upload(s.youtube, video, d["title"], d["description"], privacy=privacy, tags=d["tags"], progress=prog)
        print(json.dumps({"title": d["title"], **res}, indent=2))
        return 0
    if cmd == "draft":
        info = rr.find_replay(args[2])
        d = rr.draft(info, s.raceroom, s.youtube, rr.find_report(info), length=0.0)
        print(json.dumps(d, indent=2))
        return 0
    print("raceroom: list | scene | make NAME [--no-load] [--start M] [--minutes M] [--restart] [--stay] [--no-overlay] [--no-inset] [--kerbapex URL] | upload VIDEO NAME [--privacy private] | draft VIDEO NAME")
    return 2


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "raceroom":                # its own options (--minutes ...) are not the server's
        return cmd_raceroom(argv[1:])
    ap = argparse.ArgumentParser(prog="driverdirector")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "doctor", "raceroom"])
    ap.add_argument("rest", nargs="*", help="raceroom: list | scene | make NAME [--no-load] [--minutes M] | draft VIDEO NAME")
    ap.add_argument("--demo", action="store_true", help="use the built-in simulated race (no game needed)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    if a.command == "doctor":
        return cmd_doctor()
    if a.command == "raceroom":
        return cmd_raceroom(a.rest)

    import uvicorn
    from . import gameui
    from .camera import DryRunGame, RestGame
    from .obs import DryRunObs, Obs
    from .server import create_app

    s = Settings() if a.demo else Settings.load()
    if a.demo:
        from .lmu_api import DemoLMU
        d = Director(s, make_source(True), DryRunGame(), DryRunObs(), DemoLMU())
    else:
        d = Director(s, None, None, Obs(s.obs))
        d.game = RestGame(d.lmu, s.camera)
        def picture():                                # what OBS sees of the game, to verify the UI really toggled
            return gameui.screen_probe(d.obs.screenshot(s.obs.capture_source, 960))
        ui = gameui.UiController(probe=picture,
                                 shown=lambda: gameui.ui_shown(d.obs.screenshot(s.obs.capture_source, 960)))
        d.set_game_ui = ui.set
        d.ui_failure = lambda: ui.last_error
        d.ui_reset = ui.reset

        def screen_state() -> str:
            try:
                return "menu" if gameui.looks_like_main_menu(d.obs.screenshot(s.obs.capture_source, 480)) else "replay"
            except Exception:                         # OBS not connected or no picture: cannot tell, do not block
                return "unknown"
        d.screen_state = screen_state
        d.set_telemetry = lambda on: gameui.set_speedo(d.lmu, on)
        d.source = make_source(False, d.lmu.focus)
    app = create_app(d)
    try:                                              # lets the Claude plugin find (and start) this install
        from .config import data_dir
        (data_dir() / "home.txt").write_text(str(Path(__file__).resolve().parent.parent), encoding="utf-8")
    except OSError:
        pass
    url = f"http://{a.host}:{s.overlay_port}"
    print(f"DriverDirector: control panel {url}/   OBS browser source {url}/overlay (1920x1080)")
    if not a.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=a.host, port=s.overlay_port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
