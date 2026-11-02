"""Presses every button of the KerbApex control panel against a throwaway StintRadio and checks what happens.

  python packaging/check_panel.py

It uses its own settings folder and port (6491), so a running StintRadio, its settings and its overlay are never touched, and the
panel window is never shown. It is silent (no key, no voice, no countdown) and its overlay watches for a game that does not exist, so
nothing is ever drawn on the screen or said, even with a game running. The panel runs its real event loop, as it does for you.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PORT = 6491
HOME = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="kerbapex_check_")
os.environ["APPDATA"] = TMP                                   # before anything is imported: the panel and what it starts use this folder
os.environ.pop("ELEVENLABS_API_KEY", None)                    # no key: the throwaway engineer cannot make or say anything
(Path(TMP) / "StintRadio").mkdir()
(Path(TMP) / "StintRadio" / "settings.json").write_text(json.dumps({"port": PORT, "live": {
    "lan": False, "voice_engine": "elevenlabs", "voice": False, "countdown": "off", "talk": "quiet", "record": False, "ptt": False}}), encoding="utf-8")
sys.path.insert(0, str(HOME))

from stintradio import gui, housekeeping  # noqa: E402
from stintradio.gui import Api  # noqa: E402
from stintradio.gui_tabs import shape_geom  # noqa: E402

_real_overlay_command = gui.overlay_command
gui.overlay_command = lambda game="raceroom", *a, **k: _real_overlay_command("KerbApexNoSuchGame.exe", *a, **k)     # never over a real game
results = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   ({detail})" if detail else ""), flush=True)
    return ok


def until(fn, seconds: float = 20.0, step: float = 0.4):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            v = fn()
        except Exception:                                      # noqa: BLE001
            v = None
        if v:
            return v
        time.sleep(step)
    return None


def count(kind: str) -> int:
    """How many of this kind of program belong to the TEST copy (its port): yours is never counted, and never touched."""
    return len(housekeeping.roots(housekeeping.processes(), kind, PORT))


api = Api(PORT)
panel = gui.Panel()
panel.withdraw()
panel.engine.set("native")
for left_over in housekeeping.roots(housekeeping.processes(), "overlay", PORT) + housekeeping.roots(housekeeping.processes(), "browser", PORT):
    housekeeping.kill([left_over["pid"]])                      # a test overlay (port 6491) left by an earlier run; yours (port 6420) is never touched
time.sleep(1.0)
servers_before, overlays_before = count("server"), count("overlay")
print(f"(test data folder: {TMP}; {servers_before} StintRadio and {overlays_before} overlay already running, left alone)", flush=True)


def state() -> dict:
    return api.safe("GET", "/api/state") or {}


def overlay() -> dict:
    return api.safe("GET", "/api/overlay") or {}


def body() -> None:
    try:
        time.sleep(2.5)
        check("starts with StintRadio stopped", not panel.status.get("app"))
        check("Start is enabled, Stop is not", not panel.b_app_on.instate(["disabled"]) and panel.b_app_off.instate(["disabled"]))

        # --- StintRadio and its engineer, together
        panel.after(0, panel.start_app)
        check("Start: StintRadio comes up", bool(until(lambda: api.safe("GET", "/api/health"), 30)))
        check("the panel notices it is running", bool(until(lambda: panel.status.get("app"), 8)))
        check("Start is off now (no double start)", panel.b_app_on.instate(["disabled"]))
        second = subprocess.run([sys.executable, "-m", "stintradio"], cwd=HOME, capture_output=True, text=True, timeout=60, env=os.environ)
        check("a second StintRadio refuses to start", "already running" in second.stdout, second.stdout.strip()[:60])
        check("only one new StintRadio server is running", count("server") == servers_before + 1, f"{count('server') - servers_before} new")
        check("Start also turns the engineer on", bool(until(lambda: state().get("status") in ("waiting", "driving", "rolling"), 15)), str(state().get("status")))
        check("the panel shows the engineer on", bool(until(lambda: panel.status.get("coach") in ("waiting", "driving", "rolling"), 8)), str(panel.status.get("coach")))

        # --- overlay: one, and only one, however often it is pressed
        panel.after(0, panel.start_overlay)
        check("Start overlay: the overlay window reports in", bool(until(lambda: overlay().get("alive"), 25)))
        check("Start overlay: exactly one new overlay", count("overlay") == overlays_before + 1, f"{count('overlay') - overlays_before} new")
        panel.status["overlay"] = False                        # pressed again before the panel has noticed: the overlay itself must refuse
        panel.after(0, lambda: (setattr(panel.b_ov_on, "hold_until", 0), panel.start_overlay()))
        check("Start overlay twice: the second one goes away", bool(until(lambda: count("overlay") == overlays_before + 1, 20)), f"{count('overlay') - overlays_before} new")
        again = subprocess.run([sys.executable, "-m", "stintradio", "overlay", "--url", f"http://127.0.0.1:{PORT}/overlay"], cwd=HOME,
                               capture_output=True, text=True, timeout=60, env=os.environ)
        check("a second overlay refuses to start", "already running" in again.stdout, again.stdout.strip()[:70])
        panel.after(0, panel.toggle_edit)
        check("Move / resize: unlocks", bool(until(lambda: overlay().get("edit") is True, 8)))
        panel.status["edit"] = True
        panel.after(0, panel.toggle_edit)
        check("Move / resize: locks again", bool(until(lambda: overlay().get("edit") is False, 8)))

        # --- the Overlay tab
        tab = panel.overlay_tab
        geom = {"anchor": "bottom-left", "w": 700, "h": 300, "dx": 40, "dy": 50}
        panel.after(0, lambda: tab.send_geom(geom))
        check("Overlay tab: size and place reach StintRadio", bool(until(lambda: overlay().get("geom") == geom, 8)))
        panel.after(0, lambda: tab.send_geom(shape_geom(tab.geom(), "square")))
        g = until(lambda: (lambda x: x if x and abs(x["w"] / x["h"] - 1) < 0.05 else None)(overlay().get("geom")), 8) or {}
        check("Overlay tab: Square shape", bool(g), f"{g.get('w')}x{g.get('h')}")
        panel.after(0, lambda: tab.put({"theme": "light", "brand": False, "opacity": 0.7}))
        st = until(lambda: (lambda o: o if o.get("theme") == "light" and o.get("opacity") == 0.7 and o.get("brand") is False else None)(overlay()), 8)
        check("Overlay tab: theme, border and solidity reach StintRadio", bool(st))

        # --- the Tools tab
        tools = panel.tools_tab
        for label, fn in (("Driving suggestions", tools.tips), ("Force-feedback advice", tools.ffb), ("Check my setup", tools.check_setup)):
            panel.after(0, lambda f=fn: f())
            shown = until(lambda: (lambda t: t if "Pick a tool" not in t and "…" not in t[:60] else None)(tools.text.get("1.0", "end")), 40)
            check(f"Tools: {label} answers", bool(shown), (shown or "no answer").strip().splitlines()[0][:70])

        # --- clean up: strays, then stop
        panel.after(0, panel.clear_strays)
        check("Clear stray overlays: it closes the overlay", bool(until(lambda: "Every overlay is closed" in panel.note.get() and count("overlay") == overlays_before, 40)),
              panel.note.get()[:70])
        panel.after(0, lambda: (setattr(panel.b_ov_on, "hold_until", 0), panel.start_overlay()))
        check("Start overlay again after clearing: one", bool(until(lambda: overlay().get("alive") and count("overlay") == overlays_before + 1, 25)),
              f"alive={overlay().get('alive')}, quit={overlay().get('quit')}, {count('overlay') - overlays_before} new, note: {panel.note.get()[:50]}, "
              f"overlay procs: {[(p['pid'], p['parent']) for p in housekeeping.processes() if p['kind'] == 'overlay' and p['port'] == PORT]}")
        panel.after(0, panel.stop_overlay)
        check("Stop overlay: it closes", bool(until(lambda: not overlay().get("alive") and count("overlay") == overlays_before, 25)))
        panel.after(0, panel.stop_app)
        check("Stop: StintRadio exits", bool(until(lambda: api.safe("GET", "/api/health") is None, 20)))
        check("nothing of the test is left running", bool(until(lambda: count("server") == servers_before and count("overlay") == overlays_before, 15)),
              f"{count('server') - servers_before} servers, {count('overlay') - overlays_before} overlays extra")
    finally:
        api.safe("POST", "/api/quit", {})
        panel.after(0, panel.quit)


panel.after(500, lambda: threading.Thread(target=body, daemon=True).start())
panel.mainloop()
panel.stop_poll = True
bad = [r for r in results if not r[0]]
print(f"\n{len(results) - len(bad)} of {len(results)} checks passed", flush=True)
sys.exit(1 if bad else 0)
