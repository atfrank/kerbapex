"""The native see-through overlay: layout, motion and what is drawn (pure); the painter's pixels and the window's flags on Windows.
Nothing here ever shows a window."""
from __future__ import annotations

import math
import sys
import zlib

import pytest

from stintradio import overlay_scene as sc
from stintradio.overlay_window import MAX_H, MAX_W, MIN_H, MIN_W, SHAPES


def ring(n=100, r=500.0):
    return {"id": "t1", "source": "lap", "from": 0.0, "to": 3000.0, "closed": True,
            "pts": [[round(r * math.cos(2 * math.pi * k / n), 1), round(r * math.sin(2 * math.pi * k / n), 1)] for k in range(n)],
            "zones": [{"from": 500.0, "to": 700.0, "n": 1, "name": "Turn 1"}, {"from": 2900.0, "to": 100.0, "n": 2, "name": "Turn 2"}]}


def driving(dist=400.0, delta=-0.12):
    m = sc.Model()
    m.set_track(ring())
    m.apply({"track": "T", "delta": delta, "sector_delta": {"now": [-0.1, None, None], "active": 1, "running": 0.2},
             "map": {"id": "t1", "dist": dist, "cars": [{"d": dist + 50, "n": "A Ahead", "p": 1, "side": -1, "pit": False},
                                                        {"d": dist - 60, "n": "B Behind", "p": 3, "side": 1, "pit": False},
                                                        {"d": 2000.0, "n": "P Parked", "p": 9, "side": 0, "pit": True}]}}, 0.0)
    return m


def walk(items):
    """Every point a shape reaches, as (x, y)."""
    for it in items:
        if it[0] == "rect":
            yield it[1], it[2]
            yield it[1] + it[3], it[2] + it[4]
        elif it[0] == "poly":
            yield from it[1]
        elif it[0] == "line":
            yield from it[1]
        elif it[0] == "dot":
            yield it[1], it[2]
        elif it[0] == "text":
            yield it[2], it[3]
            yield it[2] + it[4], it[3] + it[5]


SIZES = [(s[1], s[2]) for s in SHAPES] + [(MIN_W, MIN_H), (MAX_W, MAX_H), (640, 420), (MIN_W, MAX_H), (MAX_W, MIN_H)]


@pytest.mark.parametrize("w,h", SIZES)
def test_layout_stays_inside_the_window_and_nothing_overlaps(w, h):
    lay = sc.layout(w, h)
    boxes = [lay["delta"], lay["map"], lay["next"], *lay["chips"]] + ([lay["focus"]] if lay["focus"] else [])
    for x, y, bw, bh in boxes:
        assert x >= 0 and y >= 0 and x + bw <= w + 0.01 and y + bh <= h + 0.01 and bw >= 0 and bh >= 0
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if min(a[2], a[3], b[2], b[3]) <= 0.01:
                continue
            assert a[0] + a[2] <= b[0] + 0.01 or b[0] + b[2] <= a[0] + 0.01 or a[1] + a[3] <= b[1] + 0.01 or b[1] + b[3] <= a[1] + 0.01, (a, b)


def test_layout_follows_the_shape_of_the_window():
    assert sc.layout(960, 230)["name"] == "banner"
    assert sc.layout(760, 360)["name"] == "wide"
    assert sc.layout(520, 520)["name"] == "stacked" and sc.layout(380, 640)["name"] == "tall"
    assert sc.layout(380, 640)["chips"][0][2] > sc.layout(520, 520)["chips"][0][2] * 0.6     # the chips stay readable when narrow


def test_a_long_corner_name_shrinks_instead_of_running_over_the_map():
    m = driving()
    m.track["zones"] = [{"from": 500.0, "to": 700.0, "name": "A Very Long Corner Name Indeed Through The Forest Section"}]
    lay = sc.layout(760, 360)
    item = next(i for i in sc.dynamic_items(m, 760, 360, "dark") if i[0] == "text" and i[1].startswith("A Very Long"))
    assert len(item[1]) * item[6] * sc.CHAR_W <= lay["next"][2] + 0.01


@pytest.mark.parametrize("w,h", SIZES)
def test_the_numbers_fit_their_boxes_at_any_size(w, h):
    lay = sc.layout(w, h)
    assert len("+00.00") * lay["delta_size"] * sc.CHAR_W <= lay["delta"][2] + 0.01
    assert len("S1 -0.00") * lay["chip_size"] * sc.CHAR_W <= lay["chips"][0][2]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_reads_on_any_background(theme):
    th = sc.THEMES[theme]
    for name in ("ink", "good", "bad", "warn", "dim"):
        assert sc.contrast(th[name], th["halo"]) >= 4.5, name        # its outline carries it over a bright or a dark game
    assert sc.contrast(th["ink"], th["chip"]) >= 7
    for fill in (th["good"], th["bad"], th["chip"]):
        assert sc.contrast(sc.ink_on(fill), fill) >= 4.5


def test_bright_track_line_has_a_dark_casing():
    assert sc.contrast(sc.LINE, sc.CASING) >= 7 and sc.contrast(sc.REF_LINE, sc.CASING) >= 7
    lines = sc.track_items(ring(), sc.fit(ring()["pts"], (0, 0, 300, 300)))
    assert lines[0][3] == sc.CASING and lines[0][4] > lines[1][4]    # the casing is wider, and underneath
    assert sc.track_items({**ring(), "source": "reference"}, lambda p: p)[1][3] == sc.REF_LINE


def test_spot_places_cars_by_distance_and_wraps_a_closed_lap():
    t = {"pts": [[0, 0], [10, 0], [10, 10], [0, 10]], "from": 0.0, "to": 400.0, "closed": True}
    assert sc.spot(t, 0) == (0, 0) and sc.spot(t, 50) == (5, 0) and sc.spot(t, 450) == (5, 0) and sc.spot(t, -100) == (0, 10)
    part = {"pts": [[0, 0], [10, 0], [20, 0]], "from": 100.0, "to": 300.0, "closed": False}
    assert sc.spot(part, 200) == (10, 0) and sc.spot(part, 50) is None and sc.spot(part, 301) is None


def test_fit_keeps_the_track_in_its_box_and_turns_it_if_that_helps():
    tall = [[0, 0], [100, 0], [100, 1000], [0, 1000]]
    box = (20, 30, 400, 100)
    pts = [sc.fit(tall, box)(p) for p in tall]
    assert all(20 <= x <= 420 and 30 <= y <= 130 for x, y in pts)
    assert max(p[0] for p in pts) - min(p[0] for p in pts) > 300                    # turned a quarter: it fills the wide box
    assert sc.fit([[0, 0], [10, 10]], (0, 0, 100, 100))((10, 10))[1] < sc.fit([[0, 0], [10, 10]], (0, 0, 100, 100))((0, 0))[1]     # y up the page


def test_zones_wrap_the_start_line_and_light_up_as_you_approach():
    z = {"from": 2900.0, "to": 100.0, "name": "T"}
    assert sc.zone_now(z, 50.0, 3000.0) == (2850.0, True) and sc.zone_now(z, 2950.0, 3000.0)[1] is True
    assert sc.zone_now(z, 1000.0, 3000.0) == (1900.0, False)
    assert sc.zone_lit(1000, False) == 0 and sc.zone_lit(150, False) == 0.5 and sc.zone_lit(0, True) == 1
    zones = ring()["zones"]
    assert sc.next_zone(zones, 400.0, 3000.0)["z"]["name"] == "Turn 1"
    assert sc.next_zone(zones, 600.0, 3000.0)["inside"] is True
    assert sc.next_zone(zones, 800.0, 3000.0)["z"]["name"] == "Turn 2"
    assert sc.next_text({"z": zones[0], "ahead": 123.0, "inside": False}) == "Next braking · Turn 1 · 120 m"
    assert sc.next_text({"z": zones[0], "ahead": 0, "inside": True}) == "Braking zone · Turn 1"
    assert sc.next_text(None) == ""


def test_a_car_is_dead_reckoned_between_readings_and_crosses_the_line_cleanly():
    c = sc.Mover()
    c.reading(100.0, 0.0, 3000.0)
    assert c.shown == 100.0 and c.v == 0.0
    c.reading(150.0, 1.0, 3000.0)
    assert c.v == 50.0
    for i in range(30):
        c.move(0.033, 1.0 + i * 0.033, 3000.0)
    assert 150 < c.shown < 210                                       # past the reading, at its speed
    c = sc.Mover()
    c.reading(2990.0, 0.0, 3000.0)
    c.reading(30.0, 1.0, 3000.0)                                     # over the start line: 40 m, not back round the lap
    assert c.v == 40.0
    for i in range(10):
        shown = c.move(0.033, 1.0 + i * 0.033, 3000.0)
        assert shown >= 2990.0 or shown < 100.0


def test_a_stopped_feed_keeps_the_last_data_and_stops_predicting():
    m = driving()
    for _ in range(sc.STALE_AFTER):
        m.apply(None, 1.0)
    assert not m.stale
    m.apply({"status": "stopped"}, 2.0)
    assert m.stale and m.delta == -0.12 and m.track and m.cars
    assert any(i[0] == "text" and i[1] == "last data" for i in sc.dynamic_items(m, 640, 420, "dark"))
    assert not m.wants_track()                                       # no point asking while nothing answers
    c = m.me
    c.v, c.target, c.shown = 50.0, 100.0, 100.0
    assert c.move(0.1, 5.0, 3000.0, stale=True) == 100.0
    m.apply({"track": "T", "map": {"id": "t2", "dist": 1.0, "cars": []}}, 3.0)
    assert not m.stale and m.wants_track() and not m.cars            # back; a car gone from the feed goes from the map


def test_chips_say_what_the_page_says():
    chips = sc.chip_states({"now": [-0.1, 0.01, None], "active": 2, "running": 0.2})
    assert [c["state"] for c in chips] == ["up", "even", "down"]
    assert [c["value"] for c in chips] == ["−0.10", "+0.01", "+0.20"] and chips[2]["live"] and not chips[0]["live"]
    assert [c["state"] for c in sc.chip_states(None)] == ["none"] * 3
    assert sc.delta_style(None, sc.THEMES["dark"])[0] == "-"
    assert sc.delta_style(0.2, sc.THEMES["dark"]) == ("+0.20", sc.THEMES["dark"]["bad"])
    assert sc.delta_style(-0.2, sc.THEMES["dark"]) == ("−0.20", sc.THEMES["dark"]["good"])
    assert sc.delta_style(0.01, sc.THEMES["dark"]) == ("+0.01", sc.THEMES["dark"]["ink"])


def test_kerb_stripes_alternate_and_are_cut_to_the_strip():
    items = sc.kerb_stripes(0, 0, 100, 8, 10)
    assert items[0][0] == "rect" and items[0][6] == sc.WHITE and len(items) > 4
    assert all(0 <= x <= 100 and 0 <= y <= 8 for it in items[1:] for x, y in it[1])


@pytest.mark.parametrize("w,h", SIZES)
@pytest.mark.parametrize("edit", [False, True])
def test_everything_drawn_is_inside_the_window(w, h, edit):
    m = driving()
    assert m.me.shown is not None
    items = sc.static_items(w, h, m.track, True) + sc.dynamic_items(m, w, h, "dark", edit)
    for x, y in walk(items):
        assert -1 <= x <= w + 1 and -1 <= y <= h + 1, (x, y)


def test_the_border_can_be_turned_off_and_the_map_waits_for_a_track():
    assert sc.static_items(640, 420, None, False) == []
    assert any(i[0] == "text" and i[1] == "KERB" for i in sc.static_items(640, 420, None, True))
    texts = [i[1] for i in sc.dynamic_items(sc.Model(), 640, 420, "light") if i[0] == "text"]
    assert "The map draws itself as you drive" in texts and "-" in texts


def test_a_lit_zone_stands_out_from_a_far_one():
    def zone_lines(dist):
        m = driving(dist)
        return [i for i in sc.dynamic_items(m, 640, 420, "dark") if i[0] == "line" and i[3][:3] == sc.ZONE and i[3][3] >= 150]
    near, far = zone_lines(490.0)[0], zone_lines(1500.0)[0]
    assert near[3][3] > far[3][3] and near[4] > far[4]


def test_names_never_land_on_each_other_or_on_your_car():
    taken = [(90, 90, 110, 110)]
    labels = [{"p": (130.0, 100.0), "name": "A One", "colour": sc.WHITE, "rank": 0}, {"p": (131.0, 101.0), "name": "B Two", "colour": sc.WHITE, "rank": 0},
              {"p": (132.0, 100.0), "name": "C Three", "colour": sc.WHITE, "rank": 1}]
    out = sc.car_labels(labels, taken, 640, 12.0)
    boxes = [(x, y, x + w, y + h) for _, x, y, w, h, _ in out] + [taken[0]]
    assert out and out[0][0] == "One"
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    assert sc.car_labels([{"p": (1.0, 5.0), "name": "Edge", "colour": sc.WHITE, "rank": 0}], [], 20, 12.0) == []      # no room: no name


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_outlined_text_contrasts_with_its_outline(theme):
    m = driving()
    alone = driving()
    alone.apply({"track": "T", "delta": 0.0, "map": {"id": "t1", "dist": 400.0, "cars": [{"d": 560.0, "n": "N Neutral", "p": 4, "side": 0, "pit": False}]}}, 1.0)
    texts = [i for model in (m, alone) for i in sc.dynamic_items(model, 640, 420, theme) if i[0] == "text" and i[10] is not None]
    assert {"Ahead", "Behind", "Neutral"} <= {i[1] for i in texts}
    for i in texts:
        assert sc.contrast(i[9], i[10]) >= 4.5, i[1]


def test_named_cars_and_pit_cars():
    texts = [i[1] for i in sc.dynamic_items(driving(), 640, 420, "dark") if i[0] == "text"]
    assert "Ahead" in texts and "Behind" in texts and "Parked" not in texts


def test_edit_mode_covers_the_window_so_it_can_take_the_mouse():
    sheet = sc.edit_items(640, 420, 6.4, sc.THEMES["dark"])[0]
    assert sheet[:5] == ("rect", 0, 0, 640, 420) and sheet[6][3] > 0
    assert not any(i[:3] == ("rect", 0, 0) for i in sc.dynamic_items(driving(), 640, 420, "dark", False))


def test_frames_that_look_the_same_compare_equal():
    m = driving()
    a, b = sc.rounded(sc.dynamic_items(m, 640, 420, "dark")), sc.rounded(sc.dynamic_items(m, 640, 420, "dark"))
    assert a == b
    m.me.shown += 5
    assert sc.rounded(sc.dynamic_items(m, 640, 420, "dark")) != a


# ---- the painter: real pixels in memory, no window -----------------------------------------------------------------------

windows = pytest.mark.skipif(sys.platform != "win32", reason="GDI+ and layered windows are Windows-only")


@pytest.fixture
def canvas():
    from stintradio.overlay_gdip import Canvas
    c = Canvas(640, 420)
    yield c
    c.close()


@windows
def test_what_is_not_drawn_is_fully_clear_and_a_chip_is_solid(canvas):
    m = driving()
    canvas.paint("k", sc.static_items(640, 420, m.track, True), sc.dynamic_items(m, 640, 420, "dark"))
    lay = sc.layout(640, 420)
    x, y, w, h = lay["next"]
    assert canvas.pixel(int(x + w - 3), int(y - 3))[3] == 0          # empty space shows the game
    cx, cy, cw, ch = lay["chips"][0]
    assert canvas.pixel(int(cx + 4), int(cy + ch / 2))[3] == 255     # a chip hides what is behind it
    px = canvas.pixels()
    clear = sum(1 for i in range(3, len(px), 4) if px[i] == 0)
    assert clear > 0.6 * 640 * 420                                   # most of the window is see-through


@windows
def test_pixels_are_premultiplied_for_a_layered_window(canvas):
    m = driving()
    canvas.paint("k", sc.static_items(640, 420, m.track, True), sc.dynamic_items(m, 640, 420, "dark", edit=True))
    px = canvas.pixels()
    partial = 0
    for i in range(0, len(px), 4):
        b, g, r, a = px[i:i + 4]
        assert r <= a and g <= a and b <= a
        partial += 0 < a < 255
    assert partial > 100                                             # the edges really are smooth


@windows
def test_the_kept_layer_is_redrawn_only_when_its_key_changes(canvas):
    red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
    rect = lambda c: [("rect", 10, 10, 50, 50, 0, c, None, 0, False)]
    canvas.paint("a", rect(red), [])
    assert canvas.pixel(30, 30) == (255, 0, 0, 255)
    canvas.paint("a", rect(blue), [("rect", 100, 100, 20, 20, 0, blue, None, 0, False)])
    assert canvas.pixel(30, 30) == (255, 0, 0, 255) and canvas.pixel(110, 110) == (0, 0, 255, 255)
    canvas.paint("a", rect(blue), [])
    assert canvas.pixel(110, 110)[3] == 0                            # what moved does not leave a mark
    canvas.paint("b", rect(blue), [])
    assert canvas.pixel(30, 30) == (0, 0, 255, 255)


@windows
def test_png_keeps_the_alpha_channel(canvas, tmp_path):
    canvas.paint("k", [("rect", 0, 0, 10, 10, 0, (255, 0, 0, 255), None, 0, False)], [])
    canvas.save_png(tmp_path / "o.png")
    data = (tmp_path / "o.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[25] == 6        # colour type 6: RGBA
    raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
    assert raw[1:5] == bytes((255, 0, 0, 255)) and raw[1 + 639 * 4:1 + 640 * 4] == bytes(4)


# ---- the window: created, never shown ------------------------------------------------------------------------------------

def layout_state():
    from stintradio.overlay_native import Layout
    default = {"anchor": "top-right", "w": 640, "h": 420, "dx": 24, "dy": 24}
    return Layout(dict(default), default)


GAME = (0, 0, 1920, 1080)


def test_hotkeys_change_the_layout_and_say_what_to_save():
    lay = layout_state()
    assert lay.key("edit") == {"edit": True} and lay.editing
    assert lay.key("edit") == {"edit": False, "geom": lay.geom} and not lay.editing
    assert lay.key("brand") == {"brand": False} and lay.key("brand") == {"brand": True}          # the KerbApex mark
    assert lay.key("border") == {"border": True} and lay.key("theme") == {"theme": "dark"}        # the border, off until asked for
    w = lay.geom["w"]
    assert lay.key("bigger")["geom"]["w"] > w and lay.geom["w"] > w
    assert lay.key("shape") == {"geom": lay.geom}
    assert lay.key("reset") == {"geom": lay.default} and lay.geom == lay.default
    assert lay.key("quit") is None and lay.quit


def test_dragging_moves_snaps_and_saves_on_release():
    from stintradio.overlay_window import rect_for
    lay = layout_state()
    lay.key("edit")
    x, y, w, h = rect_for(lay.geom, GAME)
    assert lay.drag(True, (x - 50, y), GAME) is None and lay.held is None        # a press outside the window does nothing
    assert lay.drag(True, (x + 10, y + 10), GAME) is None and lay.held["mode"] == "move"
    assert lay.drag(True, (110, 910), GAME) is None
    assert lay.geom["anchor"] == "bottom-left"                                   # dropped near the other corner, it snaps there
    assert lay.drag(False, (110, 910), GAME) == {"geom": lay.geom} and lay.held is None
    assert lay.drag(False, (110, 910), GAME) is None


def test_the_corner_resizes_and_is_kept_in_range():
    from stintradio.overlay_window import MIN_W, rect_for
    lay = layout_state()
    lay.set_edit(True)
    x, y, w, h = rect_for(lay.geom, GAME)
    lay.drag(True, (x + w - 5, y + h - 5), GAME)
    assert lay.held["mode"] == "resize"
    lay.drag(True, (x + w + 95, y + h + 45), GAME)
    assert (lay.geom["w"], lay.geom["h"]) == (w + 100, h + 50)
    lay.drag(True, (x - 2000, y - 2000), GAME)
    assert lay.geom["w"] == MIN_W


def test_the_control_panel_can_turn_edit_mode_on_and_off():
    lay = layout_state()
    assert lay.set_edit(False) is False and lay.set_edit(True) is True and lay.editing and lay.set_edit(True) is False


def test_only_edit_mode_takes_the_mouse():
    from stintradio.overlay_native import ex_style
    from stintradio.overlay_window import WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT
    for on in (True, False):
        for flag in (WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST):
            assert ex_style(on) & flag
    assert ex_style(True) & WS_EX_TRANSPARENT and not ex_style(False) & WS_EX_TRANSPARENT


def test_the_feed_reads_the_race_asks_for_the_track_once_and_keeps_going_without_a_server():
    import threading
    from stintradio.overlay_native import Feed
    calls = []
    answers = {"/api/race": {"track": "T", "delta": 0.1, "map": {"id": "t1", "dist": 5.0, "cars": []}}, "/api/track": ring(), "/api/overlay": {"edit": True}}
    model = sc.Model()
    feed = Feed("http://x", model, threading.Lock())
    feed.call = lambda path, body=None, timeout=3.0: calls.append(path) or answers[path]
    feed.tick(1.0)
    assert model.track and model.delta == 0.1 and feed.take_state() == {"edit": True} and feed.take_state() is None
    feed.tick(2.0)
    assert calls.count("/api/track") == 1
    feed.send({"brand": False})
    feed.tick(3.0)
    assert feed.state is None                                                    # an answer older than what we are about to say is not believed
    feed.call = lambda path, body=None, timeout=3.0: None
    for i in range(sc.STALE_AFTER + 1):
        feed.tick(4.0 + i)
    assert model.stale and model.delta == 0.1


def test_engine_flag_picks_the_window_and_native_is_the_default(monkeypatch):
    from stintradio import gui, overlay_native, overlay_window, single
    monkeypatch.setattr(overlay_window.sys, "platform", "win32")
    monkeypatch.setattr(single, "acquire", lambda *a, **k: True)             # this test starts the overlay several times in one process
    seen = []
    monkeypatch.setattr(overlay_native, "run", lambda a: seen.append(a) or 7)
    assert overlay_window.main(["--engine", "native", "--png", "x.png"]) == 7 and seen[0].png == "x.png" and seen[0].opacity is None
    assert overlay_window.main([]) == 7 and len(seen) == 2                  # no flag: the see-through window
    monkeypatch.setattr(overlay_window, "edge_path", lambda: None)
    with pytest.raises(SystemExit, match="Edge"):
        overlay_window.main(["--engine", "edge"])
    assert len(seen) == 2
    assert gui.overlay_command("lmu", False, "py.exe", "native") == ["py.exe", "-m", "stintradio", "overlay", "--game", "lmu", "--engine", "native"]


@windows
def test_png_mode_draws_one_frame_from_the_server_answers(tmp_path):
    from stintradio.overlay_native import render_png
    out = tmp_path / "frame.png"
    race = {"track": "T", "delta": -0.3, "map": {"id": "t1", "dist": 10.0, "cars": []}}
    render_png(out, race, ring(), {"theme": "dark", "brand": False, "geom": {"w": 500, "h": 300}}, (640, 420))
    assert out.read_bytes()[:4] == b"\x89PNG" and int.from_bytes(out.read_bytes()[16:20], "big") == 500
    render_png(out, None, None, None, (640, 420))                                # StintRadio not up: still a frame
    assert int.from_bytes(out.read_bytes()[16:20], "big") == 640


@windows
def test_the_window_is_hidden_layered_and_takes_a_bitmap_without_ever_showing():
    from stintradio.overlay_gdip import Canvas
    from stintradio.overlay_native import OFF_SCREEN, NativeWindow, ex_style
    from stintradio.overlay_window import GWL_EXSTYLE
    win = NativeWindow()
    canvas = Canvas(200, 120)
    try:
        assert not win.u.IsWindowVisible(win.hwnd)
        assert win.u.GetWindowLongPtrW(win.hwnd, GWL_EXSTYLE) & ex_style(True) == ex_style(True)
        win.click_through(False)
        assert win.u.GetWindowLongPtrW(win.hwnd, GWL_EXSTYLE) & ex_style(True) == ex_style(False)
        canvas.paint("k", [], [("rect", 0, 0, 50, 50, 0, (255, 0, 0, 255), None, 0, False)])
        assert win.update(canvas, OFF_SCREEN, OFF_SCREEN)                        # the alpha bitmap is accepted by Windows
        win.pump()
        assert not win.u.IsWindowVisible(win.hwnd)
    finally:
        canvas.close()
        win.close()


@windows
def test_the_whole_loop_runs_off_screen_follows_the_game_and_closes(monkeypatch):
    """The real loop, window and bitmap, placed far outside every monitor; only the server, the keys and the game are made up."""
    import argparse
    from stintradio import overlay_native as nat
    from stintradio.overlay_window import Win
    far = (nat.OFF_SCREEN, nat.OFF_SCREEN, nat.OFF_SCREEN + 1920, nat.OFF_SCREEN + 1080)
    answers = {"/api/race": {"track": "T", "delta": -0.2, "map": {"id": "t1", "dist": 5.0, "cars": []}}, "/api/track": ring(),
               "/api/overlay": {"edit": False, "brand": False, "theme": "dark", "geom": None}}
    sent = []
    monkeypatch.setattr(nat.Feed, "call", lambda self, path, body=None, timeout=3.0: (sent.append(body) if body else None) or answers[path])
    monkeypatch.setattr(Win, "game_window", lambda self, exe: (1, far))
    monkeypatch.setattr(Win, "down", lambda self, *k: False)
    monkeypatch.setattr(Win, "pid", lambda self, h: 7)
    monkeypatch.setattr(Win, "foreground_pid", lambda self: 7)                   # the game is the window in front
    script = iter([[]] * 3 + [["shape"]] + [[]] * 3 + [["edit"]] + [[]] * 3 + [["edit"]] + [[]] * 3 + [["quit"]])
    monkeypatch.setattr(nat, "Hotkeys", lambda win: type("K", (), {"pressed": lambda self: next(script)})())
    drawn, styles = [], []
    real_update = nat.NativeWindow.update
    monkeypatch.setattr(nat.NativeWindow, "update", lambda self, c, x, y: drawn.append((real_update(self, c, x, y), x, y, c.w, c.h)) or True)
    real_ct = nat.NativeWindow.click_through
    monkeypatch.setattr(nat.NativeWindow, "click_through", lambda self, on: styles.append(on) or real_ct(self, on))
    a = argparse.Namespace(url="http://x/overlay", size="640x420", png=None, game="raceroom", corner="top-right", margin=24, reset=False, opacity=None)
    assert nat.run(a) == 0
    assert drawn and all(ok for ok, *_ in drawn)                                 # Windows took every frame
    assert all(x < 0 and y < 0 for _, x, y, *_ in drawn)                         # and none was anywhere near a monitor
    assert len({(w, h) for *_, w, h in drawn}) == 2                              # the shape key changed the size
    assert styles == [False, True]                                               # edit mode took the mouse and gave it back
    assert {"edit": True} in sent and sent[-1] == {"edit": False, "gone": True}


@windows
def test_a_frame_stays_cheap_enough_for_a_race():
    import time
    from stintradio.overlay_gdip import Canvas
    m = driving()
    c = Canvas(640, 420)
    try:
        static = sc.static_items(640, 420, m.track, True)
        t0 = time.perf_counter()
        for i in range(30):
            m.me.shown += 3
            c.paint("k", static, sc.rounded(sc.dynamic_items(m, 640, 420, "dark")))
        assert (time.perf_counter() - t0) / 30 < 0.033                           # generous: it measures about 3 ms; one frame's time at 30 a second
    finally:
        c.close()


def test_only_one_copy_of_a_program_can_hold_a_name():
    import sys
    import pytest
    from stintradio import single
    if sys.platform != "win32":
        pytest.skip("named mutexes are Windows-only")
    import ctypes
    name = "KerbApexTest-" + str(ctypes.windll.kernel32.GetCurrentProcessId())
    assert single.acquire(name)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = ctypes.c_void_p
    k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    ctypes.set_last_error(0)
    other = k.CreateMutexW(None, False, "Local\\" + name)                      # a second copy asking for the same name
    assert ctypes.get_last_error() == single.ERROR_ALREADY_EXISTS
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CloseHandle(other)
    assert single.acquire(name + "-other")                                    # another name is free


def test_the_panel_hands_the_overlay_its_own_port_and_the_coach_waits_for_the_game(tmp_path, monkeypatch):
    from stintradio import gui
    assert gui.overlay_command("raceroom", False, "py.exe", "native", 6420) == ["py.exe", "-m", "stintradio", "overlay", "--game", "raceroom", "--engine", "native"]
    assert gui.overlay_command("lmu", False, "py.exe", "edge", 6499) == ["py.exe", "-m", "stintradio", "overlay", "--game", "lmu", "--url", "http://127.0.0.1:6499/overlay"]
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from fastapi.testclient import TestClient
    from stintradio.config import Settings
    from stintradio.server import create_app
    app = create_app(Settings())
    started = []
    app.state.start_when_ready = lambda: started.append(1)
    c = TestClient(app)
    r = c.post("/api/start", json={"wait": True}).json()
    assert r == {"waiting": True, "running": False} and started == [1]            # no game needed: it waits for one


FOCUS = [{"n": 5, "name": "turn five", "loss": 0.31, "tip": "brake 10 m later"}, {"n": 8, "name": "the hairpin", "loss": 0.22, "tip": "power earlier"},
         {"n": 2, "name": "corner two", "loss": 0.15, "tip": "use 3rd gear"}]


def texts(items):
    return [i[1] for i in items if i[0] == "text"]


def test_the_next_turn_is_named_with_its_number_distance_look_and_gear():
    z = {"from": 1000.0, "to": 1140.0, "n": 5, "name": "turn five", "look": "medium left", "gear": 3}
    assert sc.next_lines({"z": z, "ahead": 243.0, "inside": False}) == ("TURN 5 · 240 m", "medium left · 3rd gear")
    assert sc.next_lines({"z": z, "ahead": 0.0, "inside": True}) == ("TURN 5 · braking", "medium left · 3rd gear")
    assert sc.next_lines({"z": {"from": 0, "to": 1, "n": 2, "name": "corner two"}, "ahead": 90.0, "inside": False}) == ("TURN 2 · 90 m", "")
    assert sc.next_lines(None) == ("", "")


@pytest.mark.parametrize("w,h", [(760, 360), (640, 420), (520, 520), (380, 640), (960, 230)])
def test_the_overlay_shows_the_next_turn_and_the_three_corners_to_work_on(w, h):
    m = driving()
    m.track["zones"] = [{"from": 1500.0 + 200, "to": 1700.0 + 140, "n": 5, "name": "turn five", "look": "medium left", "gear": 3}]
    m.focus = FOCUS
    lay = sc.layout(w, h)
    t = texts(sc.dynamic_items(m, w, h, "dark"))
    assert any(x.startswith("TURN 5") for x in t)                                        # the upcoming turn, always
    if lay["focus"]:                                                                    # the list, wherever the shape has room for it
        assert "WORK ON" in t and any(x.startswith("T5  brake 10 m later  0.31 s") for x in t) and any(x.startswith("T2  use 3rd gear") for x in t)
    else:
        assert w / h >= 3 or lay["next"][3] < lay["u"] * 9                              # only a very short block goes without
    for it in sc.dynamic_items(m, w, h, "dark"):
        if it[0] == "text" and (it[1][:1] == "T" and it[1][1:2].isdigit() or it[1] == "WORK ON") and lay["focus"]:
            fx, fy, fw, fh = lay["focus"]
            assert it[2] >= fx - 0.01 and it[2] + it[4] <= fx + fw + 0.01 and it[3] >= fy - 0.01 and it[3] + it[5] <= fy + fh + 0.01 + lay["u"]    # inside its block


def test_every_shape_but_a_very_short_one_has_room_for_the_work_on_list():
    for name, w, h in SHAPES:
        lay = sc.layout(w, h)
        assert lay["next"][3] > 0
        if name != "banner":
            assert lay["focus"] is not None, name
    assert sc.layout(640, 420)["focus"][3] > 40                                          # the default size: a readable list


def kerb_colours(items):
    return [i for i in items if i[0] == "poly" or (i[0] == "rect" and i[6] is not None and i[6][:3] == (225, 6, 0))]


@pytest.mark.parametrize("w,h", [(760, 360), (640, 420), (520, 520), (380, 640), (960, 230)])
def test_the_kerbapex_mark_can_be_shown_without_the_border(w, h):
    mark_only = sc.static_items(w, h, None, True, False)
    both = sc.static_items(w, h, None, True, True)
    border_only = sc.static_items(w, h, None, False, True)
    nothing = sc.static_items(w, h, None, False, False)
    words = [i[1] for i in mark_only if i[0] == "text"]
    assert words == ["KERB", "APEX"] and not kerb_colours(mark_only)              # the mark, and not a stripe of the border
    assert [i[1] for i in both if i[0] == "text"] == ["KERB", "APEX"] and len(both) > len(mark_only) + 20
    assert not [i for i in border_only if i[0] == "text"] and len(border_only) > 20
    assert nothing == []
    lay = sc.layout(w, h)
    plate = next(i for i in mark_only if i[0] == "rect")                           # the plate stays inside the window and clear of the corners' text
    assert plate[1] + plate[3] <= w + 0.01 and plate[2] + plate[4] <= h + 0.01 and plate[1] >= lay["map"][0]


@windows
def test_overlay_stays_behind_other_windows(monkeypatch):
    """Alt-tab to another program: the overlay hides (and comes back with the game)."""
    import argparse
    from stintradio import overlay_native as nat
    from stintradio.overlay_window import Win
    far = (nat.OFF_SCREEN, nat.OFF_SCREEN, nat.OFF_SCREEN + 1920, nat.OFF_SCREEN + 1080)
    answers = {"/api/race": {"track": "T", "delta": 0.0, "map": {"id": "t1", "dist": 5.0, "cars": []}}, "/api/track": ring(),
               "/api/overlay": {"edit": False, "brand": False, "theme": "dark", "geom": None}}
    monkeypatch.setattr(nat.Feed, "call", lambda self, path, body=None, timeout=3.0: answers[path])
    monkeypatch.setattr(Win, "game_window", lambda self, exe: (1, far))
    monkeypatch.setattr(Win, "down", lambda self, *k: False)
    monkeypatch.setattr(Win, "pid", lambda self, h: 7)
    front = iter([7] * 3 + [99] * 4 + [7] * 3)                                   # game, a browser, game again
    monkeypatch.setattr(Win, "foreground_pid", lambda self: next(front, 7))
    seen, script = [], iter([[]] * 12 + [["quit"]])
    monkeypatch.setattr(nat, "Hotkeys", lambda win: type("K", (), {"pressed": lambda self: next(script)})())
    real_show, real_hide = nat.NativeWindow.show, nat.NativeWindow.hide
    monkeypatch.setattr(nat.NativeWindow, "show", lambda self: seen.append("show") or real_show(self))
    monkeypatch.setattr(nat.NativeWindow, "hide", lambda self: seen.append("hide") or real_hide(self))
    monkeypatch.setattr(nat, "FRAME_S", 0.0)
    a = argparse.Namespace(url="http://x/overlay", size="640x420", png=None, game="raceroom", corner="top-right", margin=24, reset=False, opacity=None)
    nat.run(a)
    assert seen[:3] == ["show", "hide", "show"]
