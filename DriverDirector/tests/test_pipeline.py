from driverdirector.config import CameraSettings, DetectSettings, DirectSettings
from driverdirector.detect import detect_all
from driverdirector.planner import build_plan
from driverdirector.sources.simulator import generate
from driverdirector.sources.base import FrameLog


def frames():
    return list(generate(600))


def test_detects_story():
    evs = detect_all(frames())
    kinds = [(e.kind, e.drivers[0]) for e in evs]
    assert ("incident", 5) in kinds          # spin
    assert ("incident", 3) in kinds          # contact/crawl
    assert any(e.kind == "overtake" and e.drivers[:2] == [2, 1] for e in evs)
    assert any(e.kind == "battle" and set(e.drivers) == {1, 2} for e in evs)
    # no duplicate incident for the same car in the same window
    inc5 = [e for e in evs if e.kind == "incident" and e.drivers[0] == 5]
    assert len(inc5) == 1


def test_plan_focus_and_inlays():
    fr = frames()
    evs = detect_all(fr)
    plan = build_plan(fr, evs, focus_id=1, direct=DirectSettings(), cam=CameraSettings())
    assert plan.cues and all(c.focus_id == 1 for c in plan.cues)
    assert all(a.t < b.t for a, b in zip(plan.cues, plan.cues[1:]))
    assert plan.inlays
    for i in plan.inlays:
        # inlays are for other people's action, plus the focus driver's own passes (an alternate view)
        assert not i.event.involves(1) or i.event.kind == "overtake"
        assert i.show_until - i.show_at <= DirectSettings().inlay_max_s + 1e-6
    shows = [i.show_at for i in plan.inlays]
    assert shows == sorted(shows)
    # no two consecutive cues use the same camera group
    assert all(a.group != b.group for a, b in zip(plan.cues, plan.cues[1:]))


def test_framelog_roundtrip(tmp_path):
    fr = frames()[:50]
    log = FrameLog(tmp_path / "x.jsonl")
    assert log.write(fr) == 50
    back = log.read()
    assert back[10].cars[0].driver == fr[10].cars[0].driver and back[10].t == fr[10].t


def test_focus_passes_get_alternate_view_inlays():
    from driverdirector.models import Event
    fr = frames()
    mid = fr[len(fr) // 2].t
    made = Event("overtake", mid - 4, mid + 4, [1, 2], 5.0, "A passes B for P1", "est", peak=mid)
    suffered = Event("overtake", mid + 120, mid + 128, [3, 1], 5.0, "C passes A for P2", "est", peak=mid + 124)
    plan = build_plan(fr, [made, suffered], focus_id=1, direct=DirectSettings(), cam=CameraSettings())
    by_ev = {id(i.event): i for i in plan.inlays}
    assert by_ev[id(made)].focus_id == 1 and by_ev[id(made)].camera == "trackside"       # my pass, seen from the side
    assert by_ev[id(suffered)].focus_id == 3 and by_ev[id(suffered)].camera == "onboard"  # their pass, from their car
    assert all(len(i.label) <= 30 for i in plan.inlays)
    off = build_plan(fr, [made, suffered], focus_id=1, direct=DirectSettings(max_pass_inlays=0), cam=CameraSettings())
    assert not off.inlays


def test_slow_motion_inlays():
    from driverdirector.compose import build_command
    fr = frames()
    evs = detect_all(fr)
    plan = build_plan(fr, evs, focus_id=1, direct=DirectSettings(slow_mo_inlays=2, slow_mo_speed=0.5), cam=CameraSettings())
    slow = [i for i in plan.inlays if i.slow < 1.0]
    assert 0 < len(slow) <= 2
    for i in slow:
        # same time on screen, half the source footage, centred so the key moment keeps its place in the box
        assert i.clip_end - i.clip_start == (i.show_until - i.show_at) * 0.5 or abs(
            (i.clip_end - i.clip_start) - (i.show_until - i.show_at) * 0.5) < 1e-6
    assert all(i.slow == 1.0 for i in build_plan(fr, evs, 1, DirectSettings(), CameraSettings()).inlays)
    slow[0].path = "clip.mp4"
    cmd = build_command("main.mp4", [slow[0]], "out.mp4", plan.t0, width=1920, encoder="libx264")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "/0.500" in graph and "trim=duration=" in graph


def test_race_clock_and_lap_counter():
    from driverdirector.overlay import race_state
    fr = frames()[300]
    leader = max(c.laps for c in fr.cars)
    s = race_state(fr, (fr.t - 100.0, 20.0, 0))                # timed race, 100 s in
    assert s["lap"] == leader + 1 and s["laps"] is None
    assert abs(s["left"] - (1200.0 - 100.0)) < 1e-6 and not s["final"]
    late = race_state(fr, (fr.t - 1300.0, 20.0, 0))            # clock run out: the leader's last lap
    assert late["left"] == 0.0 and late["final"]
    lim = race_state(fr, (0.0, 0.0, leader + 1))               # lap-limited race on its last lap
    assert lim["laps"] == leader + 1 and lim["final"] and lim["left"] is None
    assert race_state(fr, None) is None


def test_duck_expression_survives_many_speech_windows():
    import subprocess
    from driverdirector.compose import duck_expression, ffmpeg_bin
    wins = [(i * 10.0, i * 10.0 + 4.0) for i in range(300)]          # a long race has hundreds of commentary lines
    expr = duck_expression(wins, 12.0)
    r = subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "anullsrc=d=1", "-af",
                        f"volume='{expr}':eval=frame", "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-300:]
