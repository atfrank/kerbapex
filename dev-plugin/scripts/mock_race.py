#!/usr/bin/env python3
"""The race page with a simulated race, to look at it without the game: a made-up track driven through the real
LiveService (a throwaway settings folder, nothing else is touched), served on a spare port.

  mock_race.py [--port 6496]                                  serves until stopped: open http://127.0.0.1:6496/race
  mock_race.py --shot OUT.png [--size 1366x768] [--wait 6] [--after 0] [--path /race]    a headless Edge or Chrome screenshot, then exits
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / "StintRadio" / ".venv" / "Scripts" / "python.exe"
if sys.prefix == sys.base_prefix and VENV.exists():         # the app's packages live in its venv
    sys.exit(subprocess.call([str(VENV), __file__, *sys.argv[1:]]))
TMP = tempfile.mkdtemp(prefix="kdev_mock_")
os.environ["APPDATA"] = TMP                       # before the app is imported: it must never see the real settings
sys.path[:0] = [str(ROOT / "StintRadio"), str(ROOT / "DriverDirector")]

from stintradio import laps as LP                 # noqa: E402
from stintradio.reader import Sample              # noqa: E402
from stintradio.service import LiveService        # noqa: E402

assert str(LP.laps_dir()).startswith(TMP), "the mock would touch real settings"
TRACK, CAR, HZ = "Mock Ring", "Mock GT3", 20
NAMES = ["Kimi Xu", "Sara Lind", "Ben Wang", "Al Roy", "Noor Haddad", "Ines Costa", "Tom Okafor"]


def course(n: int = 3000) -> tuple[list, float]:
    """A wiggly closed curve: (x, z, metres along it) every step, with distances rescaled to a lap."""
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        r = 700 * (1 + 0.32 * math.sin(3 * a) + 0.14 * math.cos(5 * a + 1))
        pts.append((1.3 * r * math.cos(a), r * math.sin(a)))
    out, d = [], 0.0
    for k, (x, z) in enumerate(pts):
        if k:
            d += math.hypot(x - pts[k - 1][0], z - pts[k - 1][1])
        out.append((x, z, d))
    return out, d + math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])


def drive(pts: list) -> list:
    """Speeds a car could do round it (m/s), braking and accelerating into and out of the slow parts."""
    n, v = len(pts), []
    for k in range(n):
        ax, az, bx, bz, cx, cz = *pts[k - 12][:2], *pts[k][:2], *pts[(k + 12) % n][:2]
        h1, h2 = math.atan2(bz - az, bx - ax), math.atan2(cz - bz, cx - bx)
        turn = abs((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
        run = math.hypot(cx - ax, cz - az) / 2
        v.append(min(78.0, math.sqrt(16 * run / max(turn, 1e-3))))
    for _ in range(2):
        for k in range(2 * n - 1, -1, -1):           # no braking harder than 22 m/s^2
            i, j, gap = k % n, (k + 1) % n, math.hypot(pts[(k + 1) % n][0] - pts[k % n][0], pts[(k + 1) % n][1] - pts[k % n][1])
            v[i] = min(v[i], math.sqrt(v[j] ** 2 + 2 * 22 * gap))
        for k in range(2 * n):                       # no accelerating harder than 7 m/s^2
            i, j, gap = k % n, (k - 1) % n, math.hypot(pts[k % n][0] - pts[(k - 1) % n][0], pts[k % n][1] - pts[(k - 1) % n][1])
            v[i] = min(v[i], math.sqrt(v[j] ** 2 + 2 * 7 * gap))
    return v


class Mock:
    def __init__(self) -> None:
        self.pts, self.length = course()
        self.v = drive(self.pts)
        self.svc = LiveService("")
        self.lap, self.base, self.t, self.i = 1, 0.0, 0.0, 0
        self.fuel = 62.0
        self.others = [{"name": n, "off": 90.0 + 130.0 * k, "pace": 1.0 + 0.004 * (3 - k)} for k, n in enumerate(NAMES)]
        while self.lap == 1 or self.i < len(self.pts) // 3:     # a whole lap to make the reference, then a third of the next
            self.tick(1 / HZ, pace=1.0 if self.lap == 1 else 0.985)

    def tick(self, dt: float, pace: float = 0.985) -> None:
        n = len(self.pts)
        self.t += dt
        adv = self.v[self.i] * pace * dt
        while adv > 0:                               # along the course by this tick's metres
            gap = self.length / n
            self.i += 1
            adv -= gap
            if self.i >= n:
                self.i, self.lap, self.base = 0, self.lap + 1, self.t
        x, z, d = self.pts[self.i]
        now, soon = self.v[self.i], self.v[(self.i + 8) % n]
        brake = 90.0 if soon < now - 0.15 else 0.0
        self.fuel = max(3.0, self.fuel - dt * 0.03)
        s = Sample(t=self.t, lap=self.lap, lap_start=self.base, dist=d, speed=now * 3.6 * pace, throttle=0.0 if brake else 100.0,
                   brake=brake, steer=0, gear=4, rpm=6000, x=x, z=z, delta_best=0.0, invalid=False, track=TRACK, car=CAR,
                   track_len=self.length, last_lap=100.0, best_lap=99.0, tyre_temp=(88.0, 91.0, 84.0, 102.0), fuel=self.fuel)
        self.svc.step(s)

    def snapshot(self) -> None:
        d = self.pts[self.i][2]
        cars = [{"id": 0, "name": "You", "cls": "GT3", "place": 4, "me": True, "dist": d, "laps": self.lap - 1, "in_pits": False,
                 "penalties": 0, "flag": 0}]
        for k, o in enumerate(self.others):
            place = k + 1 if k < 3 else k + 2
            cars.append({"id": k + 1, "name": o["name"], "cls": "GT3", "place": place, "me": False, "in_pits": k == 5,
                         "dist": (d + (3 - k) * o["off"] + self.t * (o["pace"] - 1) * 40) % self.length, "laps": self.lap - 1,
                         "penalties": 0, "flag": 0})
        self.svc.snap = {"session": 10, "t": self.t, "end_t": 3600.0, "sector_flags": [0, 0, 0], "fuel": self.fuel, "fuel_cap": 90.0,
                         "wear": [0.1, 0.1, 0.2, 0.2], "track_len": self.length, "me": cars[0], "cars": cars}
        self.svc.gaps = (4, 2.4, 1.1)
        self.svc.gap_hist.append((self.t, 2.4 - 0.002 * self.t, 1.1 + 0.002 * self.t))

    def run(self) -> None:
        count = 0
        self.snapshot()
        while True:
            self.tick(1 / HZ)
            count += 1
            if count % HZ == 0:
                self.snapshot()
            time.sleep(1 / HZ)


OVERLAY = {"edit": False, "brand": True, "geom": None}   # what the overlay window reads and sets


def serve(mock: Mock, port: int) -> ThreadingHTTPServer:
    page = ROOT / "StintRadio" / "stintradio" / "web" / "race.html"

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/race":
                body, kind = page.read_bytes(), "text/html; charset=utf-8"
            elif self.path == "/api/overlay":
                body, kind = json.dumps(OVERLAY).encode(), "application/json"
            elif self.path == "/overlay":
                body, kind = (page.parent / "overlay.html").read_bytes(), "text/html; charset=utf-8"
            elif self.path == "/trackmap.js":
                body, kind = (page.parent / "trackmap.js").read_bytes(), "text/javascript"
            elif self.path == "/api/race":
                body, kind = json.dumps(mock.svc.race_view()).encode(), "application/json"
            elif self.path == "/api/track":
                body, kind = json.dumps(mock.svc.track_map()).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            n = int(self.headers.get("Content-Length") or 0)
            OVERLAY.update({k: v for k, v in json.loads(self.rfile.read(n) or b"{}").items() if k in ("edit", "brand", "geom")})
            body = json.dumps(OVERLAY).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def browser() -> str | None:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    return shutil.which("msedge") or shutil.which("chrome")


def shoot(port: int, out: str, size: str, wait: int, path: str = "/race") -> int:
    exe = browser()
    if not exe:
        print("no Edge or Chrome found")
        return 1
    w, h = size.split("x")
    # virtual time lets the page poll and animate for `wait` seconds before the picture is taken
    r = subprocess.run([exe, "--headless=new", "--disable-gpu", f"--user-data-dir={TMP}/browser", f"--window-size={w},{h}",
                        f"--virtual-time-budget={wait * 1000}", f"--screenshot={Path(out).resolve()}", f"http://127.0.0.1:{port}{path}"],
                       capture_output=True, text=True, timeout=120)
    print(f"{out}: {'written' if Path(out).exists() else 'NOT written: ' + r.stderr[-400:]}")
    return 0 if Path(out).exists() else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="mock_race")
    ap.add_argument("--port", type=int, default=6496)
    ap.add_argument("--shot")
    ap.add_argument("--size", default="1366x768")
    ap.add_argument("--path", default="/race", help="the page to photograph, e.g. /overlay")
    ap.add_argument("--wait", type=int, default=6)
    ap.add_argument("--after", type=float, default=0.0, help="seconds of the race to let pass before the screenshot")
    a = ap.parse_args()
    mock = Mock()
    threading.Thread(target=mock.run, daemon=True).start()
    srv = serve(mock, a.port)
    print(f"http://127.0.0.1:{a.port}/race  ({len(mock.svc.track_map().get('zones', []))} braking zones)", flush=True)
    try:
        if a.shot:
            time.sleep(a.after)
            return shoot(a.port, a.shot, a.size, a.wait, a.path)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        srv.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
