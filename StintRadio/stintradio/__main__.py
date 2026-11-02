from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import Settings, data_dir


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "gui":                     # the KerbApex control panel (a window of its own)
        from .gui import main as gui
        return gui(args[1:])
    if args and args[0] == "studio":                  # make videos of RaceRoom replays (with the KerbApex overlay and the engineer)
        from .studio import main as studio
        return studio(args[1:])
    if args and args[0] == "overlay":                 # the window over the game; the server must already be running
        from .overlay_window import main as overlay
        return overlay(args[1:])
    ap = argparse.ArgumentParser(prog="stintradio")
    ap.add_argument("--host", default=None, help="address to listen on (default: this PC only, or all with live.lan)")
    ap.add_argument("--start", action="store_true", help="start the coach as soon as the game's live data is there")
    ap.add_argument("--video", metavar="SESSION", help="a video run: replay this recorded session (a recorded session file) through the engineer while a "
                    "game replay plays; saves nothing and does not touch the settings")
    ap.add_argument("--port", type=int, default=None, help="listen on this port (with --video: default 6421)")
    a = ap.parse_args(argv)
    if a.video:
        os.environ["KERBAPEX_VIDEO"] = "1"
    if sys.stdout is None or sys.stderr is None:      # started without a console (pythonw, e.g. at Windows sign-in)
        log = open(data_dir() / "stintradio.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log

    import uvicorn
    from .server import create_app

    s = Settings.load()
    if a.video:
        s.port, s.live.game, s.live.replay_session = a.port or 6421, "replay", a.video
        s.live.record, s.live.learn, s.live.lan = False, False, False
    elif a.port:
        s.port = a.port
    from .single import acquire
    if not acquire(f"StintRadioServer-{s.port}"):
        print(f"StintRadio is already running on port {s.port}.")
        return 0
    host = a.host or ("0.0.0.0" if s.live.lan else "127.0.0.1")
    app = create_app(s)
    try:                                              # lets the Claude plugin find (and start) this install
        (data_dir() / "home.txt").write_text(str(Path(__file__).resolve().parent.parent), encoding="utf-8")
    except OSError:
        pass
    if a.start:
        app.state.start_when_ready()
    print(f"StintRadio: live page http://{'127.0.0.1' if host == '0.0.0.0' else host}:{s.port}/"
          + (" (race page for other devices: /race on this PC's address)" if host == "0.0.0.0" else ""))
    uvicorn.run(app, host=host, port=s.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
