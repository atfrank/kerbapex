"""The reference lap: the focus driver's best qualifying lap, shown beside the race's first lap."""
import pytest

from driverdirector import history as H, results
from driverdirector.config import CameraSettings, DirectSettings
from driverdirector.history import Driver, Quali, RaceHistory
from driverdirector.models import Event, ReferenceLap
from driverdirector.planner import build_plan, fmt_lap

from .test_record import make_director, needs_ffmpeg

QUALI_XML = """<?xml version="1.0"?>
<rFactorXML><RaceResults><TrackCourse>Test Ring</TrackCourse><Qualify>
<Driver><Name>Alex Hartley</Name><Position>3</Position><ClassPosition>3</ClassPosition>
<Lap num="1" p="5" et="--.---" topspeed="270">--.----</Lap>
<Lap num="2" p="6" et="170.7800" s1="36.9" s2="70.3" s3="38.3" topspeed="272">145.6304</Lap>
<Lap num="3" p="3" et="316.3999" s1="36.7" s2="60.2" s3="38.2" topspeed="273">135.3202</Lap>
<Lap num="4" p="3" et="451.7200" topspeed="212">--.----</Lap>
<BestLapTime>135.3202</BestLapTime></Driver>
<Driver><Name>Out Lap Only</Name><Position>9</Position><ClassPosition>9</ClassPosition>
<Lap num="1" p="9" et="--.---" topspeed="200">100.5000</Lap>
<Lap num="2" p="9" et="150.0000" topspeed="200">--.----</Lap>
<BestLapTime>100.5000</BestLapTime></Driver>
<Driver><Name>No Time</Name><Position>10</Position><ClassPosition>10</ClassPosition>
<Lap num="1" p="9" et="--.---" topspeed="200">--.----</Lap><BestLapTime>-1</BestLapTime></Driver>
</Qualify></RaceResults></rFactorXML>"""


def test_best_lap_start_comes_from_the_lap_start_time(tmp_path):
    f = tmp_path / "q.xml"
    f.write_text(QUALI_XML, encoding="utf-8")
    h = RaceHistory({1: Driver(1, "Alex Hartley"), 2: Driver(2, "Out Lap Only"), 3: Driver(3, "No Time")}, {}, [], 0.0)
    q = results.parse_qualifying(str(f), h)
    assert (q[1].best_lap, q[1].best_lap_n, q[1].best_lap_start) == (135.3202, 3, pytest.approx(316.3999))
    # the lap whose start is unknown is placed from the start of the lap after it
    assert (q[2].best_lap_n, q[2].best_lap_start) == (1, pytest.approx(150.0 - 100.5))
    assert q[3].best_lap < 0 and q[3].best_lap_start < 0


def test_lap_time_format():
    assert fmt_lap(135.3202) == "2:15.320" and fmt_lap(61.5) == "1:01.500"


def _plan(tmp_path, monkeypatch, **direct):
    d, clock = make_director(tmp_path, monkeypatch)
    green = d.frames[0].t
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=green - 5, t_end=green + 80, max_inlays=3,
                                **direct)
    d.analyze()
    ref = ReferenceLap(1, "Interlagos Q1 1", "Alex Hartley", 72.5, 316.4)
    return d, clock, green, ref


def test_reference_box_opens_at_the_green_flag_and_others_keep_clear(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    focus = d.focus_id()
    # a battle early in the lap that would otherwise take the same corner of the screen
    d.events.append(Event("incident", green + 10, green + 18, [d.frames[0].cars[3].id], 9.0, "contact"))
    plan = build_plan(d.frames, d.events, focus, d.s.direct, CameraSettings(), reference=ref)
    box = next(i for i in plan.inlays if i.replay_id is not None)
    assert box.show_at == green and box.show_until == pytest.approx(min(green + 72.5, green + 80))
    assert (box.replay_id, box.clip_start, box.focus_name) == (1, 316.4, "Alex Hartley")
    assert box.clip_end - box.clip_start == pytest.approx(box.show_until - box.show_at)
    assert box.label == "BEST QUALI LAP 1:12.500"
    for o in plan.inlays:
        if o is not box:
            assert o.show_at >= box.show_until or o.show_until <= box.show_at - 12        # clear of it, with a gap
    assert plan.inlays == sorted(plan.inlays, key=lambda i: i.show_at)
    assert not any(e.kind == "reference" for e in plan.events)       # it is not a race event


def test_no_reference_box_when_the_video_misses_the_start(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.s.direct.t_start = green + 30
    plan = build_plan(d.frames, d.events, d.focus_id(), d.s.direct, CameraSettings(), reference=ref)
    assert all(i.replay_id is None for i in plan.inlays)


def test_make_plan_explains_what_is_missing(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch, reference_lap=True)
    with pytest.raises(ValueError, match="no qualifying results"):
        d.make_plan()
    d.history.quali = {d.focus_id(): Quali(1, 1, 72.5, 3, 316.4)}
    with pytest.raises(ValueError, match="library entry"):
        d.make_plan()
    d.note_race_replay(0)
    d.s.direct.t_start = green + 30
    with pytest.raises(ValueError, match="start of the race"):
        d.make_plan()
    assert d.plan is None
    d.s.direct.t_start = green - 5
    plan = d.make_plan()
    box = next(i for i in plan.inlays if i.replay_id is not None)
    assert (box.replay_id, box.replay_name) == (1, "Interlagos Q1 1")      # same event, Qualify session


def test_the_commentator_is_told_about_the_box(tmp_path, monkeypatch):
    from driverdirector import commentary as cm
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    plan = build_plan(d.frames, d.events, d.focus_id(), d.s.direct, CameraSettings(), reference=ref)
    scenes = cm.build_scenes(plan, d.frames, history=d.history)
    s = next(s for s in scenes if s.kind == "reference")
    assert "1:12.500" in s.facts and "qualifying" in s.facts


@needs_ffmpeg
def test_record_visits_the_qualifying_replay_and_comes_back(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch, reference_lap=True)
    d.lmu.loaded = 0                                           # the race (library entry 0) is open
    d.note_race_replay(0)
    d.history.quali = {d.focus_id(): Quali(1, 1, 72.5, 3, 316.4)}
    d.lmu.standings = lambda: [{"driverName": "Alex Hartley", "slotID": 77}]     # slot in the qualifying replay
    opened = []
    real_play = d.lmu.play
    d.lmu.play = lambda rid: (opened.append(rid), real_play(rid))[1]
    d.lmu.exit_to_main_menu = lambda: setattr(d.lmu, "loaded", None) or True
    resets = []
    d.ui_reset = lambda: resets.append(1)
    plan = d.make_plan()
    d.record()
    box = next(i for i in plan.inlays if i.replay_id is not None)
    log = d.game.log
    # qualifying replay first (clip at the lap's start, focusing the driver's slot THERE), then the race again
    assert opened == [1, 0] and d.lmu.loaded == 0 and len(resets) == 2
    assert log[0] == ("seek", 316.4) and log[1] == ("focus", 77) and log[2] == ("camera", "driving")
    assert ("seek", plan.t0) in log and ("focus", plan.focus_id) in log
    assert box.path and box.offset > 0
    assert all(i.path for i in plan.inlays)


@needs_ffmpeg
def test_a_changed_replay_library_stops_before_touching_the_game(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch, reference_lap=True)
    d.lmu.loaded = 0
    d.note_race_replay(0)
    d.history.quali = {d.focus_id(): Quali(1, 1, 72.5, 3, 316.4)}
    plan = d.make_plan()
    next(i for i in plan.inlays if i.replay_id is not None).replay_name = "Something Else Q1 9"
    d.lmu.standings = lambda: []
    with pytest.raises(RuntimeError, match="library changed"):
        d.record()
    assert d.lmu.loaded == 0                                   # still in the race replay


# ---- incident cameras, pre-roll and the overlay clock ---------------------------------------------------------------
from driverdirector.planner import incident_camera, incident_preroll


def test_incident_camera_follows_the_kind_of_incident():
    one = Event("incident", 100, 105, [3], 5.0, "hits the barrier", peak=102)
    two = Event("incident", 100, 105, [3, 4], 5.0, "contact", peak=102)
    pile = Event("incident", 100, 105, [3, 4, 5, 6], 8.0, "4-car incident", peak=102)
    assert incident_camera(one) == "onboard"                        # a lone car: from inside it
    assert incident_camera(two) == incident_camera(pile) == "trackside"      # several cars: all in frame from the side
    assert incident_camera(Event("overtake", 1, 9, [1, 2])) == "onboard"
    assert incident_camera(Event("battle", 1, 9, [1, 2])) is None


def test_a_lone_car_gets_the_longest_lead_in():
    d = DirectSettings()
    one = Event("incident", 100, 105, [3], peak=102)
    two = Event("incident", 100, 105, [3, 4], peak=102)
    assert incident_preroll(one, d) == 6.0 > incident_preroll(two, d) == 4.0 > d.inlay_preroll - 0.01
    assert incident_preroll(Event("battle", 1, 9, [1, 2]), d) == d.inlay_preroll


def test_planned_boxes_use_those_cameras_and_lead_ins(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    ids = [c.id for c in d.frames[0].cars]
    d.events = [Event("incident", green + 20, green + 25, [ids[5]], 9.0, "A hits the barrier", peak=green + 22),
                Event("incident", green + 50, green + 55, [ids[6], ids[7]], 9.0, "contact: B and C", peak=green + 52)]
    d.s.direct.t_end = green + 100
    plan = build_plan(d.frames, d.events, d.focus_id(), d.s.direct, CameraSettings())
    solo, duo = sorted(plan.inlays, key=lambda i: i.show_at)
    assert (solo.camera, solo.show_at) == ("onboard", green + 22 - 6)
    assert (duo.camera, duo.show_at) == ("trackside", green + 52 - 4)


@needs_ffmpeg
def test_the_overlay_runs_on_the_replay_clock_not_the_command_clock(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.s.direct.play_latency = 0.5
    seen = []
    d._push = lambda fr, plan, mode, label: seen.append(fr.t)
    d.analyze()
    plan = d.make_plan()
    d.set_game_ui = lambda hidden: True
    d.obs.start_record = lambda directory=None: setattr(d.obs, "started", clock.now())
    d._segment(plan.t0, plan.t0 + 5, plan.focus_id, (), "main", "", "t", (0, 1))
    # seen[0] is the pre-roll push at the seek target; seen[1] is the first pass of the loop, with no time elapsed
    # since the play command returned. The replay only starts moving 0.5 s after it, so the overlay must be 0.5 s
    # BEHIND the seek target there (it used to equal it, which put the leaderboard ahead of the picture).
    assert seen[0] == pytest.approx(plan.t0) and seen[1] == pytest.approx(plan.t0 - 0.5)
    d.s.direct.overlay_delay_s = 1.0
    seen.clear()
    d._segment(plan.t0, plan.t0 + 5, plan.focus_id, (), "main", "", "t", (0, 1))
    assert seen[1] == pytest.approx(plan.t0 - 0.5 - 1.0)


# ---- the replay must be on screen, not just reported open --------------------------------------------------------
def test_opening_a_replay_waits_for_the_main_menu_to_go_away(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.lmu.loaded = 0
    shown = iter(["menu", "menu", "menu", "replay", "replay"])
    d.screen_state = lambda: next(shown)
    resets = []
    d.ui_reset = lambda: resets.append(clock.now())
    t0 = clock.now()
    d._open_replay(1, "Interlagos Q1 1")
    assert d.lmu.loaded == 1 and resets and not list(shown) == []              # it looked until the menu was gone
    assert resets[0] - t0 >= 3 * 3.0                                            # three menu looks, 3 s apart, and then settled


def test_a_replay_that_never_leaves_the_menu_is_reported_not_recorded(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.lmu.loaded = 0
    d.screen_state = lambda: "menu"
    with pytest.raises(RuntimeError, match="still shows its main menu"):
        d._open_replay(1, "Interlagos Q1 1")


def test_preflight_names_the_stuck_menu(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.lmu.loaded = 0
    d.analyze()
    d.make_plan()
    d.screen_state = lambda: "menu"
    d.obs.connect = lambda: None
    r = d.preflight()
    bad = [c for c in r["checks"] if not c["ok"]]
    assert not r["ok"] and bad[0]["name"] == "replay on screen" and "main menu" in bad[0]["detail"]
    d.screen_state = lambda: "unknown"                                          # cannot tell: do not block
    assert next(c for c in d.preflight()["checks"] if c["name"] == "replay on screen")["ok"]


def test_the_menu_detector_tells_a_red_menu_panel_from_a_replay(tmp_path):
    import subprocess
    from driverdirector import gameui
    from driverdirector.compose import ffmpeg_bin

    def png(graph):
        f = tmp_path / "x.png"
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", graph, "-frames:v", "1", str(f)], check=True)
        return f.read_bytes()
    menu = png("color=c=0xff2046:s=480x200,drawbox=x=170:y=0:w=310:h=200:color=0x20242c:t=fill")   # red panel on the left
    replay = png("color=c=0x303840:s=480x200,drawbox=x=0:y=150:w=480:h=50:color=0x101010:t=fill")
    assert gameui.looks_like_main_menu(menu) and not gameui.looks_like_main_menu(replay)


# ---- the UI really is hidden when a clip is recorded; re-recording only the reference lap --------------------------
def test_the_controller_trusts_the_picture_over_its_own_belief():
    from driverdirector.gameui import UiController
    state = {"shown": True}
    clicks = []

    def click():
        clicks.append(1)
        state["shown"] = not state["shown"]
    ui = UiController(click=click, shown=lambda: state["shown"], sleep=lambda s: None)
    ui.hidden = True                                   # believes it is hidden, but the UI is on screen
    assert ui.set(True) and state["shown"] is False and len(clicks) == 1
    assert ui.set(True) and len(clicks) == 1           # already hidden: no click
    state["shown"] = True                              # something showed it again behind our back
    assert ui.set(True) and state["shown"] is False and len(clicks) == 2
    assert ui.set(False) and state["shown"] is True and len(clicks) == 3
    stuck = UiController(click=lambda: None, shown=lambda: True, sleep=lambda s: None, attempts=2)
    assert not stuck.set(True) and "still shown" in stuck.last_error


def test_ui_shown_reads_the_replay_toolbar(tmp_path):
    import subprocess
    from driverdirector import gameui
    from driverdirector.compose import ffmpeg_bin
    W, H = 960, 401
    x0, y0, bw, bh = W * 0.27, H * 0.88, W * 0.13, H * 0.09

    def png(marks):
        boxes = ",".join(f"drawbox=x={x0 + (i % 120) * bw / 120:.1f}:y={y0 + (i // 120) * bh / 30:.1f}:w=2:h=2:color=white:t=fill"
                         for i in marks) or "null"
        f = tmp_path / "ui.png"
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x202428:s={W}x{H},{boxes}",
                        "-frames:v", "1", str(f)], check=True)
        return f.read_bytes()
    assert gameui.ui_shown(png(gameui._BAR)) is True            # the toolbar's glyphs are there
    assert gameui.ui_shown(png([])) is False                    # an ordinary dark scene
    assert gameui.ui_shown(png(gameui._BAR[:6])) is None        # a few marks: not enough to say


def test_a_clip_is_not_recorded_while_the_ui_is_showing(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.analyze()
    plan = d.make_plan()
    d.hide_wanted = True
    d.set_game_ui = lambda hidden: False
    d.ui_failure = lambda: "the click did nothing"
    with pytest.raises(RuntimeError, match="UI is showing right before recording.*did nothing"):
        d._segment(plan.t0, plan.t0 + 3, plan.focus_id, (), "main", "", "t", (0, 1))
    assert d.obs.log == []                                      # OBS was never started
    d.s.direct.require_hidden_ui = False
    d._segment(plan.t0, plan.t0 + 3, plan.focus_id, (), "main", "", "t", (0, 1))
    assert d.status["stage"] in ("t", "warning") and any(x[0] == "start" for x in d.obs.log)


@needs_ffmpeg
def test_rerecording_only_the_reference_lap_brings_the_other_clips_back(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch, reference_lap=True)
    d.s.music.source = "off"
    d.low_memory = lambda: False                                # not this machine's real free RAM
    d.lmu.loaded = 0
    d.note_race_replay(0)
    d.history.quali = {d.focus_id(): Quali(1, 1, 72.5, 3, 316.4)}
    d.lmu.standings = lambda: [{"driverName": "Alex Hartley", "slotID": 77}]
    d.lmu.exit_to_main_menu = lambda: setattr(d.lmu, "loaded", None) or True
    plan = d.make_plan()
    d.record()
    first = {i.event.kind: i.path for i in plan.inlays}
    others = [i.path for i in plan.inlays if i.replay_id is None] + [d.main_path]
    # the finished job moves its raw recordings aside, as _tidy does
    aside = tmp_path / "_cleanup" / "20260101-000000"
    aside.mkdir(parents=True)
    import shutil
    for p in others:
        shutil.move(p, aside / Path(p).name)
    opened = []
    real_play = d.lmu.play
    d.lmu.play = lambda rid: (opened.append(rid), real_play(rid))[1]
    seen = {}
    from driverdirector import compose as C
    real_compose = C.compose

    def spy(main, inlays, out, t0, **kw):                     # what was on disk at the moment of compositing
        seen["main"] = Path(main).is_file()
        seen["clips"] = [Path(i.path).is_file() for i in inlays]
        return real_compose(main, inlays, out, t0, **kw)
    monkeypatch.setattr("driverdirector.director.compose.compose", spy)
    out = d.rerecord_reference()
    assert opened == [1] and d.lmu.loaded == 1                 # only the qualifying replay was visited; the race was not reopened
    assert seen["main"] and all(seen["clips"]) and len(seen["clips"]) == len(plan.inlays)   # all brought back from _cleanup
    ref_now = next(i for i in plan.inlays if i.replay_id is not None)
    assert ref_now.path and ref_now.path != first.get(ref_now.event.kind) and Path(out).is_file()

from pathlib import Path


def test_changing_the_voice_means_the_commentary_is_voiced_again(tmp_path, monkeypatch):
    import json as _json
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.script_path.write_text(_json.dumps({"t0": 0, "t1": 10, "scenes": [{"id": 0, "t": 1.0, "slot": 5.0, "kind": "start",
                                                                      "facts": "x", "excite": 0}],
                                          "lines": {"0": "Hello there"}}), encoding="utf-8")
    calls = []

    def fake_render(scenes, lines, cfg, t0, total, out, workdir, progress=None):
        calls.append(cfg.elevenlabs_voice_id)
        Path(out).write_bytes(b"wav")
        return out
    monkeypatch.setattr("driverdirector.director.cm.render_track", fake_render)
    d.s.commentary.tts_engine = "elevenlabs"
    d.s.commentary.elevenlabs_voice_id = "voice-A"
    assert d._voice_commentary(reuse=True) and calls == ["voice-A"]
    d._voice_commentary(reuse=True)
    assert calls == ["voice-A"]                                  # same voice: the audio is reused
    d.s.commentary.elevenlabs_voice_id = "voice-B"
    d._voice_commentary(reuse=True)
    assert calls == ["voice-A", "voice-B"]                       # a different voice: voiced again, not reused


def test_redoing_the_commentary_records_nothing(tmp_path, monkeypatch):
    d, clock, green, ref = _plan(tmp_path, monkeypatch)
    d.analyze()
    d.make_plan()
    calls = []
    d._adopt_recording = lambda skip=None: calls.append("adopt")
    d._save_manifest = lambda: calls.append("manifest")
    d.prepare_commentary = lambda *a, **k: calls.append("write")
    d.low_memory = lambda: True
    d.lmu.loaded = 0
    d.lmu.exit_to_main_menu = lambda: calls.append("leave") or setattr(d.lmu, "loaded", None) or True
    d.produce = lambda reuse_voice=False: calls.append(("produce", reuse_voice)) or "out.mp4"
    assert d.redo_commentary() == "out.mp4"
    assert calls == ["adopt", "manifest", "write", "leave", ("produce", False)]    # re-voiced, never reused
    assert d.obs.log == [] and d.game.log == []                                    # no OBS, no game control
