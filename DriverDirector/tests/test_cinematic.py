import json

import pytest

from driverdirector import compose
from driverdirector.config import DirectSettings
from driverdirector.planner import video_length
from tests.test_record import TracingGame, make_director, needs_ffmpeg, FakeObs


class StampedGame(TracingGame):
    """Logs every call with the fake wall clock."""
    def _stamp(self, *what): self.log.append(what + (self.clock.now(),))
    def slow(self): self._stamp("slow")
    def play(self): self._stamp("play"); self.clock.t += 0.25
    def focus_driver(self, car_id, poll=None): self._stamp("focus", car_id); return True


class ItemObs(FakeObs):
    def set_item_enabled(self, scene, source, enabled): self.log.append(("item", source, enabled)); return True


def cinematic_director(tmp_path, monkeypatch, t_start=None, t_end=None):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.cinematic.enabled = True
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=t_start, t_end=t_end)
    d.game, d.obs = StampedGame(clock), ItemObs(clock, tmp_path)
    return d, clock


def test_cinematic_plan_is_long_shots_with_cutaways_and_slow_moments(tmp_path, monkeypatch):
    d, _ = cinematic_director(tmp_path, monkeypatch)
    d.analyze()
    plan = d.make_plan()
    c = d.s.cinematic
    assert plan.style == "cinematic" and plan.inlays == []                   # no boxes, no reference lap
    assert 0 < len(plan.slow) <= c.slow_moments
    for (a, b), (x, _) in zip(plan.slow, plan.slow[1:]):
        assert b < x                                                          # slow stretches never overlap
    away = [k for k, cue in enumerate(plan.cues) if cue.focus_id != plan.focus_id]
    assert 0 < len(away) <= c.cutaways
    for k in away:                                                            # a cut-away is told from the trackside
        assert plan.cues[k].group == "trackside" and plan.cues[k + 1].focus_id == plan.focus_id
    gaps = [b.t - a.t for a, b in zip(plan.cues, plan.cues[1:])]
    assert sum(gaps) / len(gaps) >= c.min_shot                                # long shots, not broadcast cutting
    s = plan.summary()
    assert s["style"] == "cinematic" and s["slow_moments"] == len(plan.slow) and s["cutaways"] == len(away)
    assert video_length(plan, 0.25) > plan.t1 - plan.t0


@needs_ffmpeg
def test_recording_plays_slow_motion_on_the_replay_clock_and_hides_the_overlay(tmp_path, monkeypatch):
    d, clock = cinematic_director(tmp_path, monkeypatch, 100.0, 190.0)
    d.analyze()
    plan = d.make_plan()
    plan.slow = [(130.0, 136.0)]                                             # 6 replay seconds at quarter speed
    d.s.cinematic.slow_rate = 0.25
    d.record()
    log = d.game.log
    slow_at = next(x[1] for x in log if x[0] == "slow")
    back_at = next(x[1] for x in log if x[0] == "play" and x[1] > slow_at)
    lat = d.s.direct.play_latency                                             # the game keeps its old speed this long
    assert back_at - slow_at == pytest.approx(lat + (6.0 - lat) / 0.25, abs=0.6)   # real seconds the slow stretch takes
    first_play = next(x[1] for x in log if x[0] == "play")
    assert slow_at - first_play == pytest.approx(30.0 + lat + 0.25, abs=0.5)  # reaches replay 130 s on time
    starts = [x[1] for x in d.obs.log if x[0] == "start"]
    assert len(starts) == 1                                                   # main pass only: no clips
    assert ("item", d.s.obs.overlay_source, False) in d.obs.log and ("item", d.s.obs.overlay_source, True) in d.obs.log
    assert not any(x[0] == "telemetry" for x in log)


def test_finishing_command_grades_letterboxes_titles_and_mixes_music(monkeypatch):
    monkeypatch.setattr(compose, "_font", lambda: "C\\:/Windows/Fonts/segoeuib.ttf")
    cmd = compose.build_cinematic_command("main.mp4", "out.mp4", 0.5, 120.0, "bed.wav", size=(3440, 1440),
                                          title=["Le Mans Ultimate", "Fuji Speedway, Japan", "Alex Hartley"])
    fc = cmd[cmd.index("-filter_complex") + 1]
    bar = compose.letterbox_bar(3440, 1440, 2.76)
    assert bar == 97 and f"h={bar}" in fc and "colorbalance" in fc and "noise=" in fc
    assert fc.count("drawtext") == 3 and "LE MANS ULTIMATE" in fc
    assert "fade=t=in:st=0:d=2.00" in fc and "fade=t=out:st=118.00" in fc
    assert "volume=-6.00dB" in fc and "amix=inputs=2" in fc
    assert cmd[cmd.index("-t") + 1] == "120.000" and "bed.wav" in cmd
    plain = compose.build_cinematic_command("main.mp4", "out.mp4", 0.0, 60.0, None, size=(1920, 1080),
                                            letterbox=0, grade="none", fade_s=0, title=())
    fcp = plain[plain.index("-filter_complex") + 1]
    assert "drawbox" not in fcp and "amix" not in fcp and "[0:v]null[v]" in fcp
    assert compose.letterbox_bar(1920, 1080, 2.39) == 138 and compose.letterbox_bar(3440, 1440, 2.0) == 0


def test_saved_recording_remembers_it_is_cinematic(tmp_path, monkeypatch):
    d, _ = cinematic_director(tmp_path, monkeypatch, 100.0, 190.0)
    d.analyze()
    plan = d.make_plan()
    plan.slow = [(130.0, 136.0)]
    d.main_path = str(tmp_path / "main.mp4")
    d._save_manifest()
    m = json.loads(d.manifest_path.read_text())
    assert m["style"] == "cinematic" and m["slow"] == [[130.0, 136.0]]
    d.plan = None
    assert d.restore_recording() and d.plan.style == "cinematic" and d.plan.slow == [(130.0, 136.0)]


def test_film_description_has_no_chapters_or_commentary_and_cleanup_expects_the_slow_motion(tmp_path, monkeypatch):
    d, _ = cinematic_director(tmp_path, monkeypatch)
    d.analyze()
    plan = d.make_plan()
    y = d.youtube_draft()
    assert y["title"].endswith("| Cinematic") and "cinematic" in y["tags"]
    assert "Chapters" not in y["description"] and "commentary voice" not in y["description"]
    assert "No commentary" in y["description"] and "Made with DriverDirector" in y["description"]
    # the finished film is longer than the replay span by the slow motion; that is what cleanup must expect
    film = video_length(plan, d.s.cinematic.slow_rate)
    seen = []
    monkeypatch.setattr(compose, "probe_duration", lambda p: film)
    monkeypatch.setattr("driverdirector.cleanup.sweep", lambda raws, out: seen.append(raws) or [])
    d.main_path = str(tmp_path / "main.mp4")
    d._set = lambda stage, msg="", prog=0.0: seen.append((stage, msg)) if stage == "warning" else None
    d._tidy(str(tmp_path / "film.mp4"))
    assert not any(isinstance(x, tuple) for x in seen)                       # no "kept the raw recordings" warning
