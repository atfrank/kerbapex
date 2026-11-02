from driverdirector import raceroom_overlay as ro
from driverdirector import raceroom_telemetry as tel


def _driver(i, name, place, laps, dist, speed=40.0, pit=False, number=0):
    return tel.DriverTelemetry(i, name, place, place, dist, dist / 3600, 0, 0, 0, 1, laps, True, [-1.0] * 3, [-1.0] * 3,
                               -1, -1, pit, 0, speed, number)


def _frame(*cars):
    return tel.TelemetryFrame("T", "L", list(cars))


def test_positions_follow_laps_then_distance_not_the_games_place_counter():
    tf = _frame(_driver(0, "A One", 1, 3, 100.0), _driver(1, "B Two", 2, 3, 900.0), _driver(2, "C Lap", 3, 4, 50.0))
    f = ro.to_frame(tf, 10.0)
    assert [c.driver for c in sorted(f.cars, key=lambda c: c.place)] == ["C Lap", "B Two", "A One"]
    assert round(f.track_len) == 3600


def test_timeline_gap_to_the_car_in_front_is_how_long_ago_it_passed_here():
    tl = ro.LiveTimeline()
    tl.length = 3600.0
    for t in range(0, 11):                           # car 1 is 5 s ahead of car 0 at the same speed
        tl.add(float(t), {0: (t * 0.05, 40.0), 1: ((t + 5) * 0.05, 40.0)})
    g = tl.gap(0, 10 * 0.05, 1, 15 * 0.05, 10.0)
    assert round(g, 1) == -5.0                       # negative: the other is ahead
    assert tl.gap(1, 15 * 0.05, 0, 10 * 0.05, 10.0) > 0     # behind: estimated from distance over speed


def test_live_overlay_builds_the_tower_with_the_focus_car_and_gaps():
    pushed = []
    live = ro.LiveOverlay("Alex Hartley", pushed.append)
    for t in range(6):
        tf = _frame(_driver(0, "Lead Car", 1, 2, 800.0 + t * 40, 40.0, number=7),
                    _driver(1, "Alex Hartley", 2, 2, 600.0 + t * 40, 40.0, number=11),
                    _driver(2, "Back Marker", 3, 2, 300.0 + t * 40, 40.0))
        state = live.update(tf, float(t))
    assert pushed and pushed[-1] is state
    assert [r["name"] for r in state["tower"]] == ["Lead Car", "Alex Hartley", "Back Marker"]
    assert state["tower"][1]["focus"] and state["focus"]["name"] == "Alex Hartley" and state["focus"]["speed_kmh"] == 144
    assert state["focus"]["ahead"] == "Lead Car" and state["race"]["lap"] == 3
    assert state["tower"][0]["gap"] and state["tower"][2]["gap"]      # gaps to the focus car, both directions


def test_overlay_url_carries_the_canvas_and_zoom():
    assert ro.OverlayServer(6411).url(3440, 1440, 1.4) == "http://localhost:6411/overlay?theme=default&w=3440&h=1440&zoom=1.4&bars=0"


def test_overlay_url_can_ask_for_see_through_boxes():
    assert ro.OverlayServer(6411, alpha=0.15).url(3440, 1440, 1.25).endswith("zoom=1.25&bars=0&alpha=0.15")
