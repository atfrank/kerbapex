import struct

from driverdirector import raceroom as rr
from driverdirector.config import RaceRoomSettings


def _vcr(tmp_path, **kv):
    body = b"IRSR" + b"\x00" * 8
    for k, v in {"car_class": "DTM 2026", "duration": "447.257", "finish_position": "3", "session": "Race",
                 "timestamp": "1793051160", "track_name": "Zandvoort", "track_layout": "Grand Prix",
                 "car_model": "BMW M4", **kv}.items():
        body += struct.pack("<I", len(k)) + k.encode() + struct.pack("<I", len(v)) + v.encode()
    p = tmp_path / "2026-10-26 Zandvoort Grand Prix_1.Vcr"
    p.write_bytes(body.replace(b"IRSR\x00\x00\x00\x00\x00\x00\x00\x00", b"IRSR\x00\x00\x00\x00\x00\x00\x00\x00" + struct.pack("<I", 9), 1))
    return p


def test_header_and_library(tmp_path):
    p = _vcr(tmp_path)
    r = rr.replay_info(p)
    assert (r.track, r.session, r.finish, r.duration) == ("Zandvoort", "Race", 3, 447.257)
    assert rr.find_replay("Zandvoort Grand Prix_1", [r]) is r
    assert rr.list_replays(tmp_path)[0].name == p.stem


def test_camera_plan_alternates_and_covers_duration():
    cfg = RaceRoomSettings()
    plan = rr.camera_plan(300, cfg)
    assert plan[0].group == "external" and plan[0].t == 0
    assert all(a.group != b.group for a, b in zip(plan, plan[1:]))
    assert plan[-1].t < 300 - cfg.min_shot + 0.01
    assert all(b.t - a.t >= cfg.min_shot for a, b in zip(plan, plan[1:]))
    assert rr.camera_plan(300, cfg) == plan          # same seed, same plan


def test_camera_plan_steps_through_the_variants_of_each_group():
    plan = rr.camera_plan(1200, RaceRoomSettings())
    assert all(rr.CAMERA_FIRST.get(c.group, 1) <= c.presses <= rr.CAMERA_VARIANTS[c.group] for c in plan)
    assert {c.presses for c in plan if c.group == "onboard"} - {3}      # onboard reaches beyond its first usable camera
    assert all(c.presses == 1 for c in plan if c.group in ("external", "chase"))


def test_camera_angles_do_not_repeat_until_the_group_has_used_them_all():
    plan = rr.camera_plan(3600, RaceRoomSettings(), seed=3)
    assert not any(c.group in ("driving", "chase") for c in plan)     # the user does not want chase views
    for g in ("onboard", "cycle"):
        angles = [c.presses for c in plan if c.group == g]
        n = len(range(rr.CAMERA_FIRST.get(g, 1), rr.CAMERA_VARIANTS[g] + 1))
        assert len(angles) > n, g                                   # an hour of video goes round every group at least once
        for i in range(0, len(angles) - n + 1, n):
            assert len(set(angles[i:i + n])) == n, (g, angles)      # each round uses every angle once
        assert all(a != b for a, b in zip(angles, angles[1:])), (g, angles)
    singles = [c.t for c in plan if c.group in ("external", "chase")]
    assert len(singles) < len(plan) / 2                              # the single-view groups stay occasional


def test_camera_plan_takes_its_own_weights():
    cfg = RaceRoomSettings()
    main = rr.camera_plan(600, cfg, weights=cfg.main_weights)
    assert {c.group for c in main} == {"driving"}
    inset = rr.camera_plan(600, cfg, seed=2, weights=cfg.inset_weights)
    assert {c.group for c in inset} <= {"external", "cycle"} and not any(c.group == "driving" for c in inset)


def _marks(start: float, speed: float, n: int = 400):
    """Progress marks of a driver running at `speed` per second from raw time `start` (progress 0 at the first mark)."""
    return [(start + i * 0.25, i * 0.25 * speed) for i in range(n)]


def test_align_cuts_two_passes_to_the_same_moment():
    main = rr.Recording("a.mkv", offset=5.0, duration=80.0, plan=[], marks=_marks(5.0, 0.1))
    # the second pass started playing 2 s of replay time earlier (progress already 0.2 at its first mark)
    inset = rr.Recording("b.mkv", offset=6.0, duration=80.0, plan=[],
                         marks=[(t, p + 0.2) for t, p in _marks(6.0, 0.1)])
    main_start, inset_start, dur = rr.align(main, inset)
    # main raw 5.0 is replay time 0; inset raw 6.0 is replay time 2, so the inset must start 2 s later in the replay: it has
    # nothing before replay time 2, so the main picture starts 2 s later and the inset at its very first moment
    assert abs(main_start - 7.0) < 0.3 and abs(inset_start - 6.0) < 0.3
    assert dur <= 80.0 - 1.5


def test_align_falls_back_without_marks():
    main = rr.Recording("a.mkv", 5.0, 60.0, [], [])
    inset = rr.Recording("b.mkv", 7.0, 50.0, [], [])
    assert rr.align(main, inset) == (5.0, 7.0, 50.0)


def test_inset_command_puts_the_second_recording_in_the_top_right():
    from driverdirector import compose
    cmd = compose.build_cinematic_command("a.mkv", "o.mp4", 5.0, 60.0, None, size=(3440, 1440), letterbox=0.0, grade="none",
                                          inset="b.mkv", inset_offset=2.5, inset_size=0.3)
    assert cmd.count("-i") == 2 and "b.mkv" in cmd and "2.500" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=W-w-" in graph and "scale=1032:" in graph and "[0:a]" in graph


def test_main_picture_is_cockpit_views_without_chase():
    cfg = RaceRoomSettings()
    plan = rr.camera_plan(1000, cfg, weights=cfg.main_weights)
    assert {c.group for c in plan} == {"driving"}
    assert {c.presses for c in plan} == {4}                         # the Cockpit, the view from inside the car, every time


def test_driving_camera_counts_presses_from_where_it_is():
    game = rr.RaceRoomGame()
    sent = []
    game.press = lambda name, hold=0.08: sent.append(name)
    game.camera(rr.Cue(0, "driving", "INS", 4))                    # from elsewhere: Home, then four steps to the cockpit
    assert sent == ["HOME", "INS", "INS", "INS", "INS"]
    sent.clear()
    game.camera(rr.Cue(10, "driving", "INS", 1))                   # cockpit (4) -> hood (1) is one step round
    assert sent == ["INS"]
    sent.clear()
    game.camera(rr.Cue(20, "driving", "INS", 4))                   # hood -> cockpit is three
    assert sent == ["INS"] * 3
    sent.clear()
    game.camera(rr.Cue(30, "external", "PGDN", 1))
    game.camera(rr.Cue(40, "driving", "INS", 2))
    assert sent == ["PGDN", "HOME", "INS", "INS"]


def test_kerbapex_overlay_box_sits_in_its_corner():
    g = rr.kerbapex_layout(3440, 1440, 0.24, "bottom-right")
    assert g["w"] == 825 and g["h"] == 541 and g["alignment"] == 10 and g["x"] > 3300 and g["y"] > 1300
    t = rr.kerbapex_layout(1920, 1080, 0.2, "top-left")
    assert t["alignment"] == 5 and t["x"] < 100 and t["y"] < 100
