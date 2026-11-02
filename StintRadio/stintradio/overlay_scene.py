"""What the native overlay draws, as plain data: the layout for any window shape, the cars' motion between readings (the same
as web/trackmap.js) and a list of shapes for the painter. No window and no drawing here, so all of it can be tested.

There is no panel behind anything: what is not drawn is the game. So everything has to read on ANY background: solid chips,
outlined text, a dark casing under a bright track line.

Shapes (colours are (r, g, b, a), a 0-255; a colour of None means no fill or no line):
  ("rect", x, y, w, h, radius, fill, stroke, stroke_w, dashed)
  ("poly", [(x, y), ...], fill)
  ("line", [(x, y), ...], closed, colour, width)
  ("dot",  x, y, r, fill, stroke, stroke_w)
  ("text", string, x, y, w, h, size, style, align, fill, outline, outline_w)     style "bold" | "bolditalic"; align "left" | "center" | "right"
"""
from __future__ import annotations

import math
from typing import Callable, Optional

LIT_M, TRAIL_M, EASE_S, PREDICT_S, MAX_MS = 300.0, 160.0, 0.22, 1.6, 110.0
STALE_AFTER = 3                                   # missed readings before the numbers are marked as the last data
GRIP = 56                                         # the bottom-right square that resizes, in edit mode (as overlay_window.GRIP)
CHAR_W = 0.56                                     # the width of a character of bold Segoe UI, in ems, near enough to lay out with

THEMES = {
    "dark": {"chip": (7, 9, 13, 235), "chip_edge": (135, 148, 166, 255), "ink": (243, 246, 250, 255), "halo": (0, 0, 0, 255),
             "dim": (190, 200, 214, 255), "good": (52, 224, 137, 255), "bad": (255, 93, 108, 255), "warn": (255, 200, 61, 255)},
    "light": {"chip": (247, 248, 251, 240), "chip_edge": (93, 106, 124, 255), "ink": (11, 14, 20, 255), "halo": (255, 255, 255, 255),
              "dim": (45, 55, 72, 255), "good": (11, 125, 70, 255), "bad": (190, 30, 48, 255), "warn": (140, 95, 0, 255)},
}
CASING, LINE, REF_LINE = (5, 7, 12, 215), (245, 248, 252, 255), (95, 185, 255, 255)
ZONE, KERB, WHITE, DARK = (255, 70, 90), (225, 6, 0, 255), (244, 244, 244, 255), (5, 7, 12, 255)
FRAME = ((124, 108, 255), (42, 125, 225), (225, 6, 0))     # the gradient of the branded border, top-left to bottom-right
TRAIL = (150, 135, 255)


# ---- colours ------------------------------------------------------------------------------------------------------------

def luminance(c: tuple) -> float:
    def part(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * part(c[0]) + 0.7152 * part(c[1]) + 0.0722 * part(c[2])


def contrast(a: tuple, b: tuple) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink_on(fill: tuple) -> tuple:
    """Black or white, whichever reads better on a solid fill."""
    return max((DARK, (255, 255, 255, 255)), key=lambda c: contrast(c, fill))


# ---- layout -------------------------------------------------------------------------------------------------------------

def split_next(box: tuple, u: float) -> tuple:
    """The block under the numbers: the next turn, and the corners to work on. In a wide, short band they sit side by side (the next turn
    on the left); in a narrow, tall block the list goes under the next turn; with no room for both, the next turn is alone."""
    x, y, w, h = box
    gap = u * 1.2
    if w >= h * 4.5 and h >= u * 7:                      # a band under the map: side by side
        left = w * 0.42 - gap / 2
        return (x, y, left, h), (x + left + gap, y, w - left - gap, h)
    if h >= u * 9:                                       # a column: the next turn, then the list
        nh = min(u * 7.5, h * 0.38)
        return (x, y, w, nh), (x, y + nh + u * 0.6, w, h - nh - u * 0.6)
    return box, None


def layout(w: int, h: int) -> dict:
    """Where everything goes in a window of this size, like overlay.html: stacked, a column beside the map when wide, big
    numbers beside the map when very wide. Rectangles are (x, y, w, h); `chips` are three of them."""
    u = min(w * 0.01, h * 0.017)                  # one unit that follows the window's size, whatever its shape
    pad, gap = u * 2.6, u * 1.2
    iw, ih = w - 2 * pad, h - 2 * pad
    ratio = w / h
    chip_h = u * 4.2
    if ratio >= 3:                                # a banner: bigger numbers beside the map
        chip_h = u * 6
        left = (iw - gap) * 1.3 / 2.3
        delta_h = min(ih * 0.5, u * 11)
        chips_y = pad + delta_h + gap
        chip_w = (left - 2 * gap) / 3
        chips = [(pad + i * (chip_w + gap), chips_y, chip_w, chip_h) for i in range(3)]
        delta = (pad, pad, left, delta_h)
        map_box = (pad + left + gap, pad, iw - left - gap, ih)
        ny = chips_y + chip_h + gap
        nxt = (pad, ny, left, max(0.0, pad + ih - ny))
        name = "banner"
    elif ratio >= 1.8:                            # wide: the numbers in a column on the left
        left = (iw - gap) * 0.9 / 2.9
        delta_h = u * 7.5
        chip_w = left
        top = pad + max(0.0, (ih - delta_h - 3 * chip_h - 3 * gap - u * 7) / 2)
        chips = [(pad, top + delta_h + gap + i * (chip_h + gap), chip_w, chip_h) for i in range(3)]
        delta = (pad, top, left, delta_h)
        map_box = (pad + left + gap, pad, iw - left - gap, ih)
        ny = chips[2][1] + chip_h + gap
        nxt = (pad, ny, left, max(0.0, pad + ih - ny))
        name = "wide"
    elif ratio < 0.8:                             # tall: too narrow for delta and chips side by side
        chip_h = u * 5.5
        delta_h = u * 9
        chip_w = (iw - 2 * gap) / 3
        y = pad + delta_h + gap
        chips = [(pad + i * (chip_w + gap), y, chip_w, chip_h) for i in range(3)]
        delta = (pad, pad, iw, delta_h)
        next_h = u * 29
        my = y + chip_h + gap
        map_box = (pad, my, iw, max(1.0, pad + ih - next_h - gap - my))
        nxt = (pad, pad + ih - next_h, iw, next_h)
        name = "tall"
    else:                                         # stacked: delta and chips on top, the map, the next corner underneath
        delta_h = u * 7
        delta_w = min(iw * 0.34, u * 22)
        area = iw - delta_w - gap
        chip_w = (area - 2 * gap) / 3
        y = pad + (delta_h - chip_h) / 2
        chips = [(pad + delta_w + gap + i * (chip_w + gap), y, chip_w, chip_h) for i in range(3)]
        delta = (pad, pad, delta_w, delta_h)
        next_h = u * 10
        my = pad + delta_h + gap
        map_box = (pad, my, iw, max(1.0, ih - delta_h - next_h - 2 * gap))
        nxt = (pad, pad + ih - next_h, iw, next_h)
        name = "stacked"
    nxt, focus = split_next(nxt, u)
    return {"name": name, "u": u, "delta": delta, "chips": chips, "map": map_box, "next": nxt, "focus": focus,
            "delta_size": fit_size("+00.00", delta[2], delta[3] * 0.92), "chip_size": fit_size("S1 -0.00  ", chips[0][2], chips[0][3] * 0.62),
            "next_size": max(1.0, min(max(u * 2.5, 12.0), nxt[3] * 0.85))}


def fit_size(sample: str, w: float, h: float) -> float:
    """The biggest font size at which this text stays inside (w, h)."""
    return max(1.0, min(h, w / (len(sample) * CHAR_W)))


# ---- the map, in pure functions (web/trackmap.js) -------------------------------------------------------------------------

def spot(t: dict, d: float) -> Optional[tuple]:
    """Lap distance to a place on the drawing, between the two points around it (None off the end of a part-lap drawing)."""
    pts, n = t["pts"], len(t["pts"])
    if t.get("closed"):
        i = (d % t["to"]) / t["to"] * n
    else:
        if d < t["from"] or d > t["to"]:
            return None
        i = (d - t["from"]) / max(1.0, t["to"] - t["from"]) * (n - 1)
    a = int(i) % n
    b = (a + 1) % n if t.get("closed") else min(a + 1, n - 1)
    f = i - math.floor(i)
    return pts[a][0] + (pts[b][0] - pts[a][0]) * f, pts[a][1] + (pts[b][1] - pts[a][1]) * f


def fit(pts: list, box: tuple, pad: float = 10.0) -> Callable:
    """A function from drawing to window pixels that fits the track in the box, turned a quarter if that makes it noticeably bigger."""
    bx, by, bw, bh = box
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    aw, ah = max(1.0, bw - 2 * pad), max(1.0, bh - 2 * pad)
    flat, turned = min(aw / w, ah / h), min(aw / h, ah / w)
    cx, cy, mx, my = (x0 + x1) / 2, (y0 + y1) / 2, bx + bw / 2, by + bh / 2
    if turned > flat * 1.15:
        return lambda p: (mx + (p[1] - cy) * turned, my + (p[0] - cx) * turned)
    return lambda p: (mx + (p[0] - cx) * flat, my - (p[1] - cy) * flat)


def zone_now(z: dict, me: float, lap: float) -> tuple:
    """(metres until the zone starts, inside it now)."""
    if lap:
        inside = (me - z["from"]) % lap < (z["to"] - z["from"]) % lap
        return (z["from"] - me) % lap, inside
    return z["from"] - me, z["from"] <= me <= z["to"]


def next_zone(zones: list, me: float, lap: float) -> Optional[dict]:
    """The zone you are in, else the next one ahead: {"z", "ahead", "inside"}."""
    best = None
    for z in zones or []:
        ahead, inside = zone_now(z, me, lap)
        if inside or (ahead >= 0 and (not best or ahead < best["ahead"])):
            if inside or not best or not best["inside"]:
                best = {"z": z, "ahead": ahead, "inside": inside}
    return best


def next_text(nx: Optional[dict]) -> str:
    if not nx:
        return ""
    if nx["inside"]:
        return f"Braking zone · {nx['z']['name']}"
    return f"Next braking · {nx['z']['name']} · {round(nx['ahead'] / 10) * 10} m"


GEARS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}


def next_lines(nx: Optional[dict]) -> tuple:
    """The next turn as two lines: "TURN 5 · 240 m" and what it is like, "medium left · 3rd gear" (the second may be empty)."""
    if not nx:
        return "", ""
    z = nx["z"]
    label = f"TURN {z['n']}" if z.get("n") else str(z.get("name", ""))
    first = f"{label} · braking" if nx["inside"] else f"{label} · {round(nx['ahead'] / 10) * 10} m"
    bits = [b for b in (z.get("look") or "", f"{GEARS.get(z.get('gear'), z.get('gear'))} gear" if z.get("gear") else "") if b]
    return first, " · ".join(bits)


def focus_items(box: Optional[tuple], rows: list, th: dict, u: float) -> list:
    """The corners to work on, worst first: "T5  brake 10 m later  0.31 s"."""
    if not box or not rows or box[3] < u * 3:
        return []
    x, y, w, h = box
    n = min(len(rows), 3)
    line = h / (n + 1)
    items = [_text("WORK ON", (x, y, w, line), min(line * 0.85, u * 3.0), "left", th["dim"], th["halo"])]
    for i, r in enumerate(rows[:n]):
        text = f"T{r.get('n', '?')}  {r.get('tip', '')}  {float(r.get('loss', 0.0)):.2f} s"
        items.append(_text(text, (x, y + (i + 1) * line, w, line), min(fit_size(text, w, line * 0.95), u * 4.4), "left", th["ink"], th["halo"]))
    return items


def zone_lit(ahead: float, inside: bool) -> float:
    """0 far away, growing over the last LIT_M metres, 1 in the zone."""
    return 1.0 if inside else (1 - ahead / LIT_M) if 0 <= ahead < LIT_M else 0.0


def road_to(d: float, me: float, lap: float) -> float:
    return min((d - me) % lap, (me - d) % lap) if lap else abs(d - me)


class Mover:
    """A car on the map: a new reading comes once a second, it is dead-reckoned at its speed and eased between them."""

    def __init__(self) -> None:
        self.target, self.shown, self.at, self.v = None, None, 0.0, 0.0

    def reading(self, d: float, now: float, lap: float) -> None:
        if self.target is None:
            self.at, self.shown, self.v = now, d, 0.0
        elif d != self.target:
            step = d - self.target
            if lap and abs(step) > lap / 2:           # over the start line
                step -= math.copysign(lap, step)
            dt = now - self.at
            if dt > 0.05:
                self.v = max(0.0, min(MAX_MS, step / dt))
            self.at = now
        self.target = d

    def move(self, dt: float, now: float, lap: float, stale: bool = False) -> float:
        want = self.target
        if not stale and self.v:
            want += self.v * min(PREDICT_S, now - self.at)
        off = want - self.shown
        if lap:
            off %= lap
            if off > lap / 2:
                off -= lap
        self.shown += off * (1 - math.exp(-dt / EASE_S))
        if lap:
            self.shown %= lap
        return self.shown


class Model:
    """What the overlay knows: the latest readings and the drawing. The last data stays when the feed stops."""

    def __init__(self) -> None:
        self.track, self.me, self.cars, self.delta, self.sectors, self.focus = None, None, {}, None, None, []
        self.missed, self.map_id = 0, None

    @property
    def stale(self) -> bool:
        return self.missed > STALE_AFTER

    @property
    def lap(self) -> float:
        return self.track["to"] if self.track and self.track.get("closed") else 0.0

    def set_track(self, t: dict) -> None:
        if t and t.get("pts"):                        # nothing new: the drawing on screen stays
            self.track = t

    def wants_track(self) -> bool:
        return bool(self.map_id) and self.map_id != (self.track or {}).get("id") and not self.stale

    def apply(self, s: Optional[dict], now: float) -> None:
        """One answer of /api/race (None when it could not be read)."""
        if not s or not s.get("track"):
            self.missed += 1
            return
        self.missed = 0
        self.delta, self.sectors, self.focus = s.get("delta"), s.get("sector_delta"), s.get("focus") or []
        m = s.get("map")
        if not m:
            return
        self.map_id = m.get("id")
        if self.me is None:
            self.me = Mover()
        self.me.reading(m["dist"], now, self.lap)
        seen = set()
        for k in m.get("cars") or []:
            key = f"{k.get('p')}|{k.get('n')}"
            seen.add(key)
            c = self.cars.get(key)
            if c is None:
                c = self.cars[key] = Mover()
            c.reading(k["d"], now, self.lap)
            c.name, c.side, c.pit = k.get("n", ""), k.get("side", 0), bool(k.get("pit"))
        for key in [k for k in self.cars if k not in seen]:
            del self.cars[key]

    def step(self, dt: float, now: float) -> None:
        for c in ([self.me] if self.me else []) + list(self.cars.values()):
            c.move(dt, now, self.lap, self.stale)


# ---- what to draw -------------------------------------------------------------------------------------------------------

def _mix(a: tuple, b: tuple, f: float) -> tuple:
    return tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3)) + (255,)


def _clip_x(pts: list, lo: float, hi: float) -> list:
    """A polygon cut to lo <= x <= hi."""
    for inside, cross in ((lambda p: p[0] >= lo, lo), (lambda p: p[0] <= hi, hi)):
        out = []
        for i, p in enumerate(pts):
            q = pts[i - 1]
            if inside(p) != inside(q):
                f = (cross - q[0]) / (p[0] - q[0])
                out.append((cross, q[1] + (p[1] - q[1]) * f))
            if inside(p):
                out.append(p)
        pts = out
    return pts


def kerb_stripes(x: float, y: float, w: float, h: float, band: float) -> list:
    """A strip of slanted red and white kerb, solid, cut square at both ends."""
    items = [("rect", x, y, w, h, 0, WHITE, None, 0, False)]
    k = -h
    while k < w + h:
        pts = _clip_x([(x + k + h, y), (x + k + h + band, y), (x + k + band, y + h), (x + k, y + h)], x, x + w)
        if len(pts) >= 3:
            items.append(("poly", pts, KERB))
        k += 2 * band
    return items


def border_items(w: int, h: int, u: float) -> list:
    """The KerbApex border: kerb stripes top and bottom and a gradient edge down the sides."""
    kh, edge = max(4.0, u * 1.5), max(2.0, u * 0.45)
    items = kerb_stripes(0, 0, w, kh, max(5.0, u * 1.6)) + kerb_stripes(0, h - kh, w, kh, max(5.0, u * 1.6))
    steps = 24
    for side_x in (0.0, w - edge):
        for i in range(steps):
            f = (i / (steps - 1)) * 0.6 + (0.4 if side_x else 0.0)     # along the diagonal, as the page's 135 degree gradient
            col = _mix(FRAME[0], FRAME[1], f / 0.55) if f < 0.55 else _mix(FRAME[1], FRAME[2], (f - 0.55) / 0.45)
            sy = kh + (h - 2 * kh) * i / steps
            items.append(("rect", side_x, sy, edge, (h - 2 * kh) / steps + 0.5, 0, col, None, 0, False))
    return items


def mark_items(w: int, h: int, u: float, border: bool = False) -> list:
    """The KerbApex wordmark on a solid plate in the bottom-right corner (above the border's stripes when there is one)."""
    kh, edge = (max(4.0, u * 1.5), max(2.0, u * 0.45)) if border else (0.0, 0.0)
    size = max(8.0, u * (1.9 if border else 1.7))
    mw, mh = size * 6.4, size * 1.5
    mx, my = w - edge - mw - u * 0.8, h - kh - mh - u * (0.5 if border else 0.15)
    items = []
    items.append(("rect", mx, my, mw, mh, mh * 0.25, (7, 9, 13, 235), None, 0, False))
    half, inset = mw * 0.5, size * 0.17           # the text boxes overlap by the padding GDI+ puts round a string, so the word stays whole
    items.append(("text", "KERB", mx, my, half + inset, mh, size, "bolditalic", "right", (255, 60, 50, 255), None, 0))
    items.append(("text", "APEX", mx + half - inset, my, mw - half + inset, mh, size, "bolditalic", "left", (243, 246, 250, 255), None, 0))
    return items


def track_items(t: dict, view: Callable) -> list:
    """What never moves on the map: a dark casing under a bright line, and the start line."""
    pts = [view(p) for p in t["pts"]]
    closed = bool(t.get("closed"))
    items = [("line", pts, closed, CASING, 7.0), ("line", pts, closed, REF_LINE if t.get("source") == "reference" else LINE, 2.4)]
    if closed and len(pts) > 1:
        (x, y), (x2, y2) = pts[0], pts[1]
        n = math.hypot(x2 - x, y2 - y) or 1.0
        nx, ny = -(y2 - y) / n, (x2 - x) / n
        seg = [(x - nx * 9, y - ny * 9), (x + nx * 9, y + ny * 9)]
        items += [("line", seg, False, DARK, 6.0), ("line", seg, False, WHITE, 3.0)]
    return items


def static_items(w: int, h: int, track: Optional[dict], brand: bool = True, border: bool = False) -> list:
    """What only changes with the window, the track, the mark or the border: drawn once, kept and copied under every frame."""
    lay = layout(w, h)
    items = (border_items(w, h, lay["u"]) if border else []) + (mark_items(w, h, lay["u"], border) if brand else [])
    if track:
        items += track_items(track, fit(track["pts"], lay["map"]))
    return items


def _text(s: str, box: tuple, size: float, align: str, fill: tuple, halo: tuple, style: str = "bold") -> tuple:
    return ("text", s, box[0], box[1], box[2], box[3], size, style, align, fill, halo, max(1.5, size * 0.11))


def delta_style(d: Optional[float], th: dict) -> tuple:
    """(text, colour) of the big delta."""
    if d is None:
        return "-", th["ink"]
    return ("+" if d >= 0 else "−") + f"{abs(d):.2f}", th["bad"] if d > 0.05 else th["good"] if d < -0.05 else th["ink"]


def chip_states(sd: Optional[dict]) -> list:
    """For S1, S2 and S3: {"label", "value", "state" ("up" | "down" | "even" | "none"), "live"}, as the page's chips."""
    out = []
    for i in range(3):
        live, v = False, None
        if sd:
            now = sd.get("now") or [None] * 3
            live = i == sd.get("active") and now[i] is None
            v = sd.get("running") if live else now[i]
        state = "none" if v is None else "up" if v < -0.02 else "down" if v > 0.02 else "even"
        out.append({"label": f"S{i + 1}", "value": None if v is None else ("−" if v <= 0 else "+") + f"{abs(v):.2f}", "state": state, "live": live and v is not None})
    return out


def chip_items(box: tuple, chip: dict, size: float, th: dict) -> list:
    """A solid chip, so it reads on any background: green or red when it says something, the theme's colour when not."""
    x, y, w, h = box
    fill = {"up": th["good"], "down": th["bad"]}.get(chip["state"], th["chip"])
    ink = ink_on(fill)
    stroke = ink if chip["live"] else th["chip_edge"]
    items = [("rect", x, y, w, h, h * 0.28, fill, stroke, max(1.0, h * 0.05), chip["live"])]
    text = chip["label"] if chip["value"] is None else f"{chip['label']} {chip['value']}"
    tri = h * 0.3
    tx = x + h * 0.25
    if chip["state"] in ("up", "down"):           # an arrow as well as the colour
        cy, half = y + h / 2, tri / 2
        items.append(("poly", [(tx, cy + half), (tx + tri, cy + half), (tx + tri / 2, cy - half)] if chip["state"] == "up"
                      else [(tx, cy - half), (tx + tri, cy - half), (tx + tri / 2, cy + half)], ink))
        tx += tri
    items.append(("text", text, tx, y, x + w - tx, h, size, "bold", "center", ink, None, 0))
    return items


def car_labels(labels: list, taken: list, w: int, size: float) -> list:
    """Names for the cars around you, never on top of each other: to the right of the dot, else the left, else over it."""
    out = []
    for lab in sorted(labels, key=lambda a: a["rank"])[:6]:
        name = lab["name"].split(" ")[-1]
        tw, (px, py) = len(name) * size * CHAR_W, lab["p"]
        top = py - size * 0.7
        def free(x: float) -> bool:
            return x > 2 and x + tw < w - 2 and not any(x - 2 < b[2] and x + tw + 2 > b[0] and top < b[3] and top + size * 1.4 > b[1] for b in taken)
        x = next((c for c in (px + 8, px - 8 - tw, px - tw / 2) if free(c)), None)
        if x is None:
            continue
        taken.append((x - 2, top, x + tw + 2, top + size * 1.4))
        out.append((name, x, top, tw, size * 1.4, lab["colour"]))
    return out


def map_items(model: Model, box: tuple, view: Callable, w: int, u: float, th: dict) -> list:
    """What moves on the map: the braking zones, your trail, the cars and their names."""
    t, me = model.track, model.me.shown
    lap = model.lap
    place = lambda d: (lambda p: p and view(p))(spot(t, d))
    items = []
    for z in t.get("zones") or []:                # faint all round the lap, lighting up over the last LIT_M metres before you reach one
        ahead, inside = zone_now(z, me, lap)
        lit = zone_lit(ahead, inside)
        span = (z["to"] - z["from"]) % lap if lap else z["to"] - z["from"]
        pts = [p for p in (place(z["from"] + span * k / 12) for k in range(13)) if p]
        if len(pts) < 2:
            continue
        if lit > 0:
            items.append(("line", pts, False, ZONE + (round(90 * lit),), 9 + 9 * lit))
        items.append(("line", pts, False, DARK[:3] + (200,), 7 + 4 * lit))
        items.append(("line", pts, False, ZONE + (round(150 + 105 * lit),), 4 + 4 * lit))
    drawn = me if lap else max(t["from"], min(t["to"], me))     # a part-lap drawing lags the car: wait at its tip
    prev = place(drawn)
    for k in range(1, 17):                        # a fading trail behind your car
        p = place(drawn - TRAIL_M * k / 16) if prev else None
        if not p:
            break
        items.append(("line", [prev, p], False, TRAIL + (round(220 * (1 - k / 17)),), 4.5))
        prev = p
    labels = []
    for c in model.cars.values():
        p = place(c.shown)
        if not p:
            continue
        col = (150, 160, 175, 255) if c.pit else th["good"] if c.side < 0 else th["warn"] if c.side > 0 else (225, 232, 242, 255)
        items.append(("dot", p[0], p[1], 5.0 if c.side else 3.8, col, DARK, 1.4))
        if not c.pit and c.name and (c.side or road_to(c.shown, me, lap) < 200):
            labels.append({"p": p, "name": c.name, "colour": col if c.side else th["ink"], "rank": 0 if c.side else 1})
    taken = []
    mine = place(drawn)
    if mine:
        items.append(("dot", mine[0], mine[1], 12.0, None, TRAIL + (120,), 4.0))
        items.append(("dot", mine[0], mine[1], 7.0, (255, 255, 255, 255), DARK, 2.0))
        taken.append((mine[0] - 10, mine[1] - 10, mine[0] + 10, mine[1] + 10))
    size = max(10.0, u * 1.9)
    for name, x, y, tw, th_, col in car_labels(labels, taken, w, size):
        items.append(_text(name, (x, y, tw + size, th_), size, "left", col, th["halo"]))
    return items


def edit_items(w: int, h: int, u: float, th: dict) -> list:
    """Edit mode: a faint sheet over the whole window, since a fully clear pixel would let the mouse through to the game,
    a bright dashed edge, a hint, and the corner that resizes."""
    warn = (255, 200, 61, 255)
    edge = max(2.0, u * 0.5)
    items = [("rect", 0, 0, w, h, 0, (20, 24, 32, 70), None, 0, False),
             ("rect", edge / 2, edge / 2, w - edge, h - edge, 0, None, warn, edge, True),
             ("poly", [(w, h - GRIP), (w, h), (w - GRIP, h)], warn)]
    hint = "EDIT · drag to move · corner to resize · Ctrl+Alt+O to lock"
    size = fit_size(hint, w * 0.86, max(10.0, u * 2.1))
    pw, ph = min(w * 0.92, len(hint) * size * CHAR_W + 2 * size), size * 1.8
    px, py = (w - pw) / 2, u * 2.4
    items.append(("rect", px, py, pw, ph, ph / 2, warn, None, 0, False))
    items.append(("text", hint, px, py, pw, ph, size, "bold", "center", DARK, None, 0))
    return items


def dynamic_items(model: Model, w: int, h: int, theme: str, edit: bool = False) -> list:
    """Everything that changes between frames."""
    th, lay = THEMES.get(theme, THEMES["light"]), layout(w, h)
    items = []
    text, col = delta_style(model.delta, th)
    items.append(_text(text, lay["delta"], lay["delta_size"], "center" if lay["name"] == "banner" else "left", col, th["halo"]))
    for box, chip in zip(lay["chips"], chip_states(model.sectors)):
        items += chip_items(box, chip, lay["chip_size"], th)
    if model.track and model.me and model.me.shown is not None:
        view = fit(model.track["pts"], lay["map"])
        items += map_items(model, lay["map"], view, w, lay["u"], th)
        nx = next_zone(model.track.get("zones"), model.me.shown, model.lap)
        if nx and lay["next"][3] > 4:
            near = nx["inside"] or nx["ahead"] < LIT_M
            first, second = next_lines(nx)
            nxbox = lay["next"]
            colour = th["bad"] if near else th["ink"]
            if second and nxbox[3] >= lay["u"] * 4.2:           # room for two lines: the turn, then what it is like
                h1 = nxbox[3] * 0.58
                items.append(_text(first, (nxbox[0], nxbox[1], nxbox[2], h1), min(fit_size(first, nxbox[2], h1), lay["u"] * 4.8), "left", colour, th["halo"]))
                items.append(_text(second, (nxbox[0], nxbox[1] + h1, nxbox[2], nxbox[3] - h1),
                                   min(fit_size(second, nxbox[2], nxbox[3] - h1), lay["u"] * 3.2), "left", th["dim"], th["halo"]))
            else:                                               # a long text gets smaller, it does not run over the map
                text = first + (" · " + second if second and nxbox[2] > lay["u"] * 30 else "")
                items.append(_text(text, nxbox, min(lay["next_size"], fit_size(text, nxbox[2], nxbox[3])), "left", colour, th["halo"]))
        items += focus_items(lay["focus"], model.focus, th, lay["u"])
    elif not model.track:
        mx, my, mw, mh = lay["map"]
        items.append(_text("The map draws itself as you drive", (mx, my, mw, mh), fit_size("The map draws itself as you drive", mw, lay["u"] * 2.2),
                           "center", th["ink"], th["halo"]))
    if model.stale:
        size = max(9.0, lay["u"] * 1.9)
        items.append(_text("last data", (w - size * 6 - lay["u"] * 3, lay["u"] * 2, size * 6, size * 1.5), size, "right", th["warn"], th["halo"]))
    if edit:
        items += edit_items(w, h, lay["u"], th)
    return items


def rounded(items: list) -> list:
    """The shapes with coordinates to a tenth of a pixel, so two frames that look the same compare equal and need no repaint."""
    def r(v):
        if isinstance(v, float):
            return round(v, 1)
        if isinstance(v, list):
            return [tuple(round(c, 1) for c in p) for p in v]
        return v
    return [tuple(r(v) for v in it) for it in items]
