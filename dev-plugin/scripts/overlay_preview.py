#!/usr/bin/env python3
"""Renders the native overlay to PNG files over a dark, a bright and a busy backdrop, for looking at it without the game and
without any window: every shape and theme on one contact sheet per theme, and the same frames without a backdrop.

  python dev-plugin/scripts/overlay_preview.py OUT_DIR
"""
from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "StintRadio"))

from stintradio import overlay_scene as sc  # noqa: E402
from stintradio.overlay_gdip import Canvas  # noqa: E402

SHAPES = [("wide", 760, 360), ("square", 520, 520), ("tall", 380, 640), ("banner", 960, 230), ("default", 640, 420)]


def made_up_track(n: int = 400) -> dict:
    """A track with some corners in it, closed, 4 km long."""
    pts = []
    for k in range(n):
        a = 2 * math.pi * k / n
        r = 500 + 160 * math.sin(3 * a) + 90 * math.cos(5 * a + 1) + 40 * math.sin(9 * a)
        pts.append([round(r * math.cos(a) * 1.5, 1), round(r * math.sin(a), 1)])
    looks = ["slow right", "fast left", "medium left", "left hairpin", "medium right", "fast right"]
    zones = [{"from": f, "to": f + 140.0, "n": i + 1, "name": f"Turn {i + 1}", "look": looks[i], "gear": (2, 4, 3, 2, 3, 5)[i]}
             for i, f in enumerate((300.0, 950.0, 1600.0, 2300.0, 3100.0, 3700.0))]
    return {"id": "demo", "source": "reference", "track": "Demo", "from": 0.0, "to": 4000.0, "closed": True, "pts": pts, "zones": zones}


def driving(dist: float = 1500.0) -> sc.Model:
    m = sc.Model()
    m.set_track(made_up_track())
    race = {"track": "Demo", "delta": -0.27, "focus": [{"n": 5, "name": "turn five", "loss": 0.31, "tip": "brake 10 m later"},
                                                       {"n": 4, "name": "the hairpin", "loss": 0.22, "tip": "power earlier"},
                                                       {"n": 2, "name": "corner two", "loss": 0.15, "tip": "use 3rd gear"}], "sector_delta": {"now": [-0.14, 0.09, None], "active": 2, "running": -0.04},
            "map": {"id": "demo", "dist": dist, "cars": [
                {"d": dist + 70, "n": "Max Rival", "p": 3, "side": -1, "pit": False}, {"d": dist - 85, "n": "Sam Chaser", "p": 5, "side": 1, "pit": False},
                {"d": dist + 180, "n": "Jo Near", "p": 2, "side": 0, "pit": False}, {"d": 200.0, "n": "Far Away", "p": 1, "side": 0, "pit": False},
                {"d": 3000.0, "n": "Pit Car", "p": 9, "side": 0, "pit": True}]}}
    m.apply(race, 0.0)
    return m


def busy_backdrop(w: int, h: int, seed: int = 7) -> list:
    """Road grey, grass, sky and bright blobs, something like a game frame at its worst."""
    rnd = random.Random(seed)
    items = [("rect", 0, 0, w, h * 0.45, 0, (150, 195, 240, 255), None, 0, False), ("rect", 0, h * 0.45, w, h * 0.55, 0, (70, 120, 50, 255), None, 0, False),
             ("poly", [(w * 0.1, h), (w * 0.45, h * 0.45), (w * 0.55, h * 0.45), (w * 0.9, h)], (75, 75, 80, 255))]
    for _ in range(40):
        c = tuple(rnd.randrange(256) for _ in range(3)) + (255,)
        items.append(("dot", rnd.randrange(w), rnd.randrange(h), rnd.randrange(6, 40), c, None, 0))
    return items


def backdrops(w: int, h: int) -> dict:
    return {"dark": [("rect", 0, 0, w, h, 0, (12, 14, 18, 255), None, 0, False)], "bright": [("rect", 0, 0, w, h, 0, (250, 250, 245, 255), None, 0, False)],
            "busy": busy_backdrop(w, h)}


def main(argv=None) -> int:
    out = Path((argv or sys.argv[1:] or ["."])[0])
    out.mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        gap = 16
        sheet_w = sum(s[1] for s in SHAPES[:3]) + 4 * gap
        rows = [(s, b) for b in ("busy", "bright", "dark") for s in (SHAPES[:3], SHAPES[3:])]
        sheet_h = gap + sum(max(s[2] for s in row) + gap for row, _ in rows)
        sheet = Canvas(max(sheet_w, 960 + 640 + 3 * gap), sheet_h)
        sheet.draw([("rect", 0, 0, sheet.w, sheet.h, 0, (60, 60, 64, 255), None, 0, False)])
        y = gap
        for row, back in rows:
            x = gap
            for _, w, h in row:
                c = Canvas(w, h)
                m = driving()
                c.paint("k", backdrops(w, h)[back] + sc.static_items(w, h, m.track, True), sc.dynamic_items(m, w, h, theme))
                _blit(sheet, c, x, y)
                c.close()
                x += w + gap
            y += max(s[2] for s in row) + gap
        sheet.save_png(out / f"overlay_{theme}.png")
        sheet.close()
    for name, m, edit in (("edit", driving(), True), ("waiting", sc.Model(), False), ("noborder", driving(), False)):
        c = Canvas(640, 420)
        c.paint("k", backdrops(640, 420)["busy"] + sc.static_items(640, 420, m.track, name != "noborder"), sc.dynamic_items(m, 640, 420, "dark", edit))
        c.save_png(out / f"overlay_{name}.png")
        c.close()
    c = Canvas(640, 420)
    m = driving()
    static = sc.static_items(640, 420, m.track, True)
    c.paint("k", static, sc.dynamic_items(m, 640, 420, "dark"))
    c.save_png(out / "overlay_clear_background.png")
    t0 = time.perf_counter()
    frames = 100
    for i in range(frames):
        m.me.shown = (m.me.shown + 3) % 4000
        c.paint("k", static, sc.dynamic_items(m, 640, 420, "dark"))
    print(f"a frame at 640x420 takes {(time.perf_counter() - t0) / frames * 1000:.1f} ms")
    c.close()
    return 0


def _blit(sheet: Canvas, c: Canvas, x: int, y: int) -> None:
    """Copy an opaque canvas into the sheet."""
    import ctypes
    for row in range(c.h):
        ctypes.memmove(sheet.bits.value + ((y + row) * sheet.w + x) * 4, c.bits.value + row * c.w * 4, c.w * 4)


if __name__ == "__main__":
    sys.exit(main())
