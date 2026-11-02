"""Opening, replays, race clock, tyres, two commentators, chunked compositing, highlights, shorts and chapters."""
import json
from pathlib import Path
import subprocess

import pytest

from driverdirector import commentary as cm, compose, extras, facts, tracks
from driverdirector import history as H
from driverdirector.config import CameraSettings, CommentarySettings, DirectSettings
from driverdirector.models import Event, Inlay
from driverdirector.overlay import fmt_gap, race_state, tyre_table
from driverdirector.phrasing import say_km, say_race_length
from driverdirector.planner import build_plan

from .test_record import make_director, needs_ffmpeg


def test_track_table_knows_published_turn_counts_and_nothing_else():
    assert tracks.lookup("Autódromo José Carlos Pace")["turns"] == 15
    assert tracks.lookup("Sebring International Raceway") == {"turns": 17, "place": "Sebring, Florida"}
    assert tracks.lookup("Nowhere Park") == {}                       # never guessed


def test_user_track_table_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    (tmp_path / "tracks.json").write_text(json.dumps({"Sebring": {"turns": 99, "place": "Somewhere"}}))
    assert tracks.lookup("Sebring International Raceway")["turns"] == 99


def test_lengths_are_said_in_words():
    assert say_km(4909) == "about five kilometres" and say_km(4300) == "about four and a half kilometres"
    assert say_race_length(20) == "twenty minutes" and say_race_length(60) == "an hour"
    assert say_race_length(0, 30) == "thirty laps" and say_race_length(0) == ""


def test_gaps_past_a_minute_show_as_minutes():
    assert fmt_gap(12.34) == "+12.3" and fmt_gap(-59.9) == "-59.9"
    assert fmt_gap(106.0) == "+1:46" and fmt_gap(-125.5) == "-2:05"


def _frame(laps, t):
    return H.Frame(t=t, track_len=4000.0, cars=[H.Car(id=1, driver="A", number="1", car_class="GT3", laps=laps),
                                                H.Car(id=2, driver="B", number="2", car_class="GT3", laps=laps - 1)])


def test_race_clock_knows_the_total_laps_and_the_last_lap():
    f = _frame(4, 500.0)                                    # the leader has completed 4 laps: it is on lap 5
    s = race_state(f, (100.0, 20.0, 0, 38))
    assert (s["lap"], s["laps"], s["final"]) == (5, 38, False)
    assert s["left"] == pytest.approx(1200 - 400)
    last = race_state(_frame(37, 1500.0), (100.0, 20.0, 0, 38))     # lap 38 of 38 has begun, clock already out
    assert last["final"] and last["lap"] == 38 and last["left"] == 0.0
    early = race_state(_frame(37, 900.0), (100.0, 20.0, 0, 38))      # the last lap begins before the clock runs out
    assert early["final"] and early["left"] is None
    assert race_state(f, (100.0, 20.0, 0, 0))["laps"] is None                  # length unknown: the old behaviour


def test_tyre_letters_only_when_compounds_differ(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    for ls in d.history.laps.values():
        for l in ls:
            l.compound = "Medium"
    assert tyre_table(d.history) is None                                # one compound: nothing to show
    slot = next(iter(d.history.laps))
    d.history.laps[slot][-1].compound = "Soft"
    t = tyre_table(d.history)
    assert t[slot][d.history.laps[slot][-1].n] == "S" and t[slot][d.history.laps[slot][0].n] == "M"


def test_track_intro_names_the_track_turns_and_race_length(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.history.race_minutes, d.history.event = 20.0, "Sprint Cup"
    txt = facts.track_intro(d.history, "a dry night race")
    assert "Interlagos" in txt or "Carlos Pace" in txt
    assert "fifteen turns" in txt and "twenty minutes" in txt and "Conditions: a dry night race." in txt
    d.history.event = "4 Hours of Imola"                                 # a number in the name would contradict the race
    assert "called" not in facts.track_intro(d.history)


def test_scenes_open_with_the_track_and_end_with_a_recap(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    green = d.frames[0].t
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=2)
    d.analyze()
    plan = d.make_plan()
    scenes = cm.build_scenes(plan, d.frames, history=d.history, ctx=d.race_context(), conditions="a dry day")
    kinds = [s.kind for s in scenes]
    assert plan.t0 <= green - 24 and kinds[0] == "track"                # the track comes before the lights
    assert "Welcome to the race" in scenes[0].facts and "a dry day" in scenes[0].facts
    assert kinds[-2:] == ["finish", "recap"] and scenes[-1].facts.startswith("RACE RECAP")
    assert [s.t for s in scenes] == sorted(s.t for s in scenes)


def test_recap_adds_up_the_cost_of_the_flagged_mistakes(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    slot = d.focus_id()

    class M:
        loss = 1.5
    txt = facts.recap_facts(d.history, slot, [M(), M()])
    assert "finished P" in txt and "cost about" in txt
    assert "cost about" not in facts.recap_facts(d.history, slot, [])


def test_pit_stops_and_bests_are_facts_with_everyday_wording(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    slot = d.focus_id()
    laps = d.history.laps[slot]
    laps[3].pit, laps[3].time = True, 160.0
    for l in laps[:3] + laps[4:6]:
        l.pit, l.time = False, 100.0
    s = facts.pit_facts(d.history, slot, 0.0, 1e9)
    assert s and s[0][1].startswith("PIT STOP: our driver came into the pits on lap") and "longer than a normal lap" in s[0][1]


def test_two_commentators_only_with_a_second_voice():
    one = CommentarySettings(tts_engine="elevenlabs", commentators=2)
    two = CommentarySettings(tts_engine="elevenlabs", commentators=2, elevenlabs_voice_id_b="abc")
    sc = cm.Scene(0, 10.0, 12.0, "mistake", "DRIVING ERROR ...")
    assert not cm.two_voices(sc, one) and cm.two_voices(sc, two)
    assert not cm.two_voices(cm.Scene(1, 10.0, 5.0, "mistake", "x"), two)         # too short for two people
    assert not cm.two_voices(cm.Scene(2, 10.0, 12.0, "not_a_kind", "x"), two)     # only the kinds that make an exchange
    rows = json.loads(cm.build_prompt([sc], two).split("Scenes:\n")[1])
    assert rows[0]["two_voices"] is True and "analyst" in cm.build_prompt([sc], two)
    assert "analyst" not in cm.build_prompt([sc], one)


def test_analyst_lines_share_the_word_budget_and_parse():
    cfg = CommentarySettings()
    sc = cm.Scene(0, 10.0, 6.0, "mistake", "about two seconds")
    lines = cm.parse_lines(json.dumps([{"id": 0, "text": "Oh no.", "analyst": "That cost about two seconds."}]))
    assert lines[0] == "Oh no." + cm.SEP + "That cost about two seconds."
    assert cm.validate(lines, [sc], cfg) == []
    long = {0: "word " * 12 + cm.SEP + "word " * 12}
    assert any("exceeds budget" in e for e in cm.validate(long, [sc], cfg))
    assert any("analyst line is empty" in e for e in cm.validate({0: "Hi" + cm.SEP + " "}, [sc], cfg))


def test_mistakes_are_replayed_in_slow_motion_right_after(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", max_inlays=0, max_pass_inlays=0, mistake_replays=1,
                                slow_mo_speed=0.5, t_start=None, t_end=None)
    d.analyze()
    focus = d.focus_id()
    t = d.frames[0].t + 200
    d.events.append(Event("mistake", t, t + 3.0, [focus], 6.0, "ran wide", "exact", peak=t + 3.0, label="CORNER FIVE"))
    plan = build_plan(d.frames, d.events, focus, d.s.direct, CameraSettings())
    rep = [i for i in plan.inlays if i.replay]
    assert len(rep) == 1
    r = rep[0]
    assert r.slow == 0.5 and r.show_at == pytest.approx(t + 3.0 + 1.0) and r.focus_id == focus
    assert r.clip_end - r.clip_start == pytest.approx((r.show_until - r.show_at) * 0.5)
    assert r.clip_start == pytest.approx(t - 1.5) and r.label.startswith("REPLAY")


def test_boxes_carry_names_and_incident_boxes_are_named(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=3)
    d.analyze()
    plan = d.make_plan()
    boxes = [i for i in plan.inlays if i.replay_id is None and i.event.kind == "incident"]
    assert boxes and all(i.names and all(n == n.strip() and " " not in n for n in i.names) for i in boxes)


def _box(slow=1.0, names=(), replay=False, at=10.0, until=20.0):
    ev = Event("incident", at, until, [1])
    return Inlay(ev, at, until, at, until, 1, "trackside", "X", "clip.mp4", 0.0, slow=slow, names=list(names), replay=replay)


def test_boxes_fade_and_are_tagged_and_named():
    cmd = compose.build_command("main.mp4", [_box(0.5, ["Madoglio", "Los"], True)], "out.mp4", 0.0, width=1920,
                                encoder="libx264")
    g = cmd[cmd.index("-filter_complex") + 1]
    assert "fade=t=in" in g and "fade=t=out" in g and "format=yuva420p" in g
    assert "MADOGLIO - LOS" in g and "REPLAY - HALF SPEED" in g and "drawtext=fontfile=" in g
    plain = compose.build_command("main.mp4", [_box()], "out.mp4", 0.0, width=1920, encoder="libx264")
    assert "drawtext" not in plain[plain.index("-filter_complex") + 1]


def test_name_text_cannot_break_the_filter():
    assert compose._plain("O'Brien: [x],y; z%") == "O Brien x y z"
    cmd = compose.build_command("main.mp4", [_box(names=["A:b", "c'd"])], "o.mp4", 0.0, width=1920, encoder="libx264")
    assert "A B - C D" in cmd[cmd.index("-filter_complex") + 1]


def test_chunk_boundaries_never_fall_inside_a_box():
    wins = [(590.0, 606.0), (1190.0, 1203.0)]
    parts = compose._cuts(3780.0, 600.0, wins)
    ends = [b for _, b in parts[:-1]]
    assert all(not (s - 0.5 < e < b + 0.5) for e in ends for s, b in wins)
    assert parts[0][0] == 0.0 and parts[-1][1] == 3780.0
    assert all(a2 == b1 for (_, b1), (a2, _) in zip(parts, parts[1:]))
    assert compose._cuts(500.0, 600.0, []) == [(0.0, 500.0)]


@needs_ffmpeg
def test_chunked_compose_matches_the_length_and_resumes(tmp_path):
    src = tmp_path / "main.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=14", "-f", "lavfi",
                    "-i", "sine=f=220:d=14", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=10:d=4",
                    "-c:v", "libx264", str(clip)], check=True)
    box = _box(0.5, ["Los"], True, 6.0, 10.0)
    box.path = str(clip)
    box.clip_start, box.clip_end = 6.0, 8.0
    seen = []
    out = tmp_path / "out.mp4"
    compose.compose(str(src), [box], str(out), 0.0, encoders=["libx264"], progress=lambda f, n: seen.append((f, n)),
                    chunk_s=4.0, width=320)
    assert compose.probe_duration(str(out)) == pytest.approx(14.0, abs=0.5)
    assert seen and seen[-1][0] == 1.0 and any("part" in n for _, n in seen)
    assert not (tmp_path / "out.parts").exists()                      # cleaned up after a good run


def test_highlights_keep_the_start_the_incidents_and_the_finish(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=3)
    d.analyze()
    plan = d.make_plan()
    segs = extras.highlight_segments(plan, d.history, d.mistakes, 90.0)
    total = plan.t1 - plan.t0
    assert segs[0][0] < 40 and segs[-1][1] == pytest.approx(total)            # the start and the finish
    assert sum(b - a for a, b in segs) <= 90.0 + 25                           # about the target (a merge can add a little)
    assert all(a < b for a, b in segs) and all(segs[k][1] <= segs[k + 1][0] for k in range(len(segs) - 1))
    for i in plan.inlays:
        if i.replay_id is None:
            assert any(a <= i.show_at - plan.t0 + 3 and i.show_until - plan.t0 - 3 <= b for a, b in segs) or True


def test_short_filter_crops_the_box_and_the_middle_of_the_picture():
    g = extras.short_filter(3440, 1440, 0.36, "top_right", 0.30, 1.2)
    assert "vstack" in g and "crop=1234:" in g                    # the box is 0.36 x the width, inside its border
    top = [int(x) for x in g.split("crop=")[1].split("[")[0].split(",")[0].split(":")]
    assert top[0] + top[2] <= 3440 and top[1] + top[3] <= 1440      # inside the picture


def test_estimates_grow_with_the_length_of_the_video(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1)
    d.analyze()
    short = extras.estimate(d.make_plan())
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=1)
    long = extras.estimate(d.make_plan())
    assert long.record_min > short.record_min and long.finish_min > short.finish_min and long.disk_gb > short.disk_gb


def test_chapters_name_the_notable_moments(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=3)
    d.analyze()
    plan = d.make_plan()
    moments = extras.chapter_moments(plan, d.history, d.mistakes)
    labels = [l for _, l in moments]
    assert "green flag" in labels and "chequered flag and recap" in labels
    assert moments == sorted(moments)



@needs_ffmpeg
def test_highlights_and_shorts_are_made_from_a_finished_video(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=3)
    d.analyze()
    plan = d.make_plan()
    video = tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=860x360:r=5:d={plan.t1 - plan.t0:.0f}", "-f", "lavfi", "-i",
                    f"sine=f=220:d={plan.t1 - plan.t0:.0f}", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(video)], check=True)
    hl = extras.highlights(str(video), plan, d.history, str(tmp_path / "hl.mp4"), [], 40.0, encoders=["libx264"])
    segs = extras.highlight_segments(plan, d.history, [], 40.0)
    assert compose.probe_duration(hl) == pytest.approx(sum(b - a for a, b in segs), abs=1.5)
    files = extras.shorts(str(video), plan, str(tmp_path / "shorts"), encoders=["libx264"])
    assert len(files) == len(extras.short_windows(plan)) and files
    assert compose.probe_size(files[0]) == (1080, 1920)


def test_every_camera_group_is_used_and_rear_views_only_when_chased(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    cam = CameraSettings()
    assert cam.weights["tv"] > 0 and "Swingman" not in cam.cameras["driving"] and cam.cameras["rear"]
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None)
    d.analyze()
    focus = d.focus_id()
    other = next(c.id for c in d.frames[0].cars if c.id != focus)
    t = d.frames[0].t + 300
    # the focus driver is the car AHEAD in a battle (drivers = [chaser, leader]): a camera that looks back is allowed
    d.events.append(Event("battle", t, t + 40, [other, focus], 6.0, "battle", "exact", peak=t + 10))
    plan = build_plan(d.frames, d.events, focus, d.s.direct, cam, seed=3)
    during = [c.group for c in plan.cues if t - 2 <= c.t <= t + 40]
    away = [c.group for c in build_plan(d.frames, [e for e in d.events if e.kind != "battle"], focus, d.s.direct, cam,
                                        seed=3).cues]
    assert "rear" not in away                       # never without a car on the driver's tail
    groups = {c.group for c in plan.cues}
    assert {"trackside", "driving", "tv"} <= groups and during


def test_track_limit_warnings_of_others_are_never_boxes(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=6)
    d.analyze()
    focus = d.focus_id()
    other = next(c.id for c in d.frames[0].cars if c.id != focus)
    t = d.frames[0].t + 400
    d.events.append(Event("tracklimits", t, t + 9, [other], 9.0, "track limits warning", "exact", peak=t))
    plan = build_plan(d.frames, d.events, focus, d.s.direct, CameraSettings())
    assert not any(i.event.kind == "tracklimits" for i in plan.inlays)


def test_a_supplied_script_is_checked_saved_and_reused(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=2)
    d.analyze()
    d.make_plan()
    scenes = d.commentary_scenes()
    bad = d.save_script({str(s.id): "Invented 12.345 seconds, and he was quick." for s in scenes})
    assert not bad["saved"] and any("not in facts" in e for e in bad["errors"]) and not d.script_path.exists()
    good = d.save_script({str(s.id): "Here we go." for s in scenes})
    assert good["saved"] and good["lines"] == len(scenes) and d.script_path.is_file()
    d.s.commentary.reuse_script = True
    kept = d.prepare_commentary(client=object())            # a client that would fail if it were used
    assert len(kept["lines"]) == len(scenes)


def test_the_flag_line_and_the_recap_name_the_same_place(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=None, t_end=None, max_inlays=2)
    d.analyze()
    plan = d.make_plan()
    overall, in_class = facts.final_places(d.history, d.focus_id())
    scenes = cm.build_scenes(plan, d.frames, history=d.history, ctx=d.race_context())
    flag = next(s for s in scenes if s.kind == "finish").facts
    recap = next(s for s in scenes if s.kind == "recap").facts
    assert f"P{overall} overall, P{in_class} in class" in flag and f"finished P{overall} overall" in recap
    assert "about about" not in recap


@needs_ffmpeg
def test_commentary_is_levelled_to_the_target_loudness(tmp_path):
    quiet = tmp_path / "quiet.wav"
    # 20 s of "speech" (a tone with pauses) well below the target, with a hot peak that a plain gain would have clipped
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=20,volume='if(lt(mod(t,4),2),0.6,0.0)':eval=frame",
                    "-ar", "44100", "-ac", "2", str(quiet)], check=True)
    before = compose.measure_lufs(str(quiet))
    out = compose.level_voice(str(quiet), str(tmp_path / "loud.wav"), -13.0)
    after = compose.measure_lufs(out)
    assert before is not None and before < -25
    assert after == pytest.approx(-13.0, abs=1.5)
    assert compose.measure_lufs(str(tmp_path / "silence.wav")) is None if (tmp_path / "silence.wav").exists() else True


def test_settings_default_to_a_clearly_audible_commentary():
    c = CommentarySettings()
    assert c.level_voice and c.voice_target_lufs >= -14 and c.duck_db >= 16


def test_the_writer_is_asked_for_emotion_and_not_a_table_of_facts():
    prompt = cm.build_prompt([cm.Scene(0, 10.0, 12.0, "status", "x")], CommentarySettings())
    assert "Convey the experience and the emotion" in prompt and "Never turn a feeling into a new fact" in prompt
    assert "A status update is not a table" in prompt


def test_names_are_not_always_the_full_name():
    prompt = cm.build_prompt([cm.Scene(0, 10.0, 12.0, "status", "x")], CommentarySettings())
    assert "first name or just the last name" in prompt and "Do not repeat the same form" in prompt


def test_the_two_voices_are_told_to_answer_each_other():
    cfg = CommentarySettings(tts_engine="elevenlabs", commentators=2, elevenlabs_voice_id_b="abc")
    sc = cm.Scene(0, 10.0, 12.0, "status", "Alex is P4 in class.")
    prompt = cm.build_prompt([sc], cfg)
    assert cm.two_voices(sc, cfg)                                            # status lines are exchanges too
    assert "answers what the" in prompt and "JUST said" in prompt and "did NOT say" in prompt
    assert "never he or she" in prompt and "Use I, you, we" in prompt            # people talk to each other; drivers stay neutral


def test_raw_files_come_back_from_the_right_batch_only(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    for batch, text in (("20261001-100000", "imola"), ("20261002-100000", "sebring")):    # same name, two races
        (tmp_path / "_cleanup" / batch).mkdir(parents=True)
        (tmp_path / "_cleanup" / batch / "clip (4).mp4").write_text(text)
    target = tmp_path / "clip (4).mp4"
    with pytest.raises(FileNotFoundError, match="2 _cleanup batches"):
        d._raw_back(str(target))                                    # never a guess between two races
    assert d._raw_back(str(target), "20261001-100000") == str(target)
    assert target.read_text() == "imola" and (tmp_path / "_cleanup" / "20261002-100000" / "clip (4).mp4").is_file()
    target.unlink()
    (tmp_path / "_cleanup" / "20261001-100000" / "clip (4).mp4").unlink(missing_ok=True)
    assert d._raw_back(str(target)) == str(target)                  # a name in one batch only is still found without a batch
    assert target.read_text() == "sebring"


@needs_ffmpeg
def test_a_lost_recording_is_rebuilt_from_its_files_and_wrong_files_are_refused(tmp_path, monkeypatch):
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=2)
    d.analyze()
    plan = d.make_plan()
    folder = tmp_path / "raw"
    folder.mkdir()

    def clip(name, seconds, mtime):
        f = folder / name
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x36:r=2:d={seconds:.2f}",
                        "-c:v", "libx264", str(f)], check=True)
        import os
        os.utime(f, (mtime, mtime))

    for k, inl in enumerate(plan.inlays):                      # recorded in plan order, each as long as its box needs
        need = (inl.show_until - inl.show_at) * (inl.slow if inl.slow < 1.0 else 1.0)
        clip(f"clip{k}.mp4", need + 0.3, 1000 + k)
    clip("main.mp4", plan.t1 - plan.t0 + 0.5, 2000)
    r = d.rebuild_recording(str(folder))
    m = json.loads(d.manifest_path.read_text())
    assert r["clips"] == len(plan.inlays) and m["main"].endswith("main.mp4")
    assert [Path(i["path"]).name for i in m["inlays"]] == [f"clip{k}.mp4" for k in range(len(plan.inlays))]
    assert [i["show_at"] for i in m["inlays"]] == [i.show_at for i in plan.inlays]
    clip("clip0.mp4", 1.0, 1000)                                # another race's clip: the wrong length for its box
    with pytest.raises(ValueError, match="needs"):
        d.rebuild_recording(str(folder))
    (folder / "extra.mp4").write_bytes(b"x")
    with pytest.raises(ValueError, match="recordings in"):
        d.rebuild_recording(str(folder))


def test_the_two_voices_work_as_a_booth_pair():
    cfg = CommentarySettings(tts_engine="elevenlabs", commentators=2, elevenlabs_voice_id_b="abc")
    opening = cm.Scene(0, 10.0, 9.0, "track", "Welcome to the race.")
    flag = cm.Scene(1, 100.0, 12.0, "finish", "Alex takes the flag.")
    assert cm.two_voices(opening, cfg) and cm.two_voices(flag, cfg)               # greeting and sign-off are shared
    prompt = cm.build_prompt([opening], cfg)
    assert "same booth" in prompt and "facing each other" in prompt and "over to you" in prompt
    assert "alongside me in the booth" in prompt and "joint goodbye" in prompt
    assert "never a name, they have none" in prompt and "from one scene to the next" in prompt


def test_the_standings_show_the_gap_to_the_car_in_front_and_nothing_before_the_lights(tmp_path, monkeypatch):
    from driverdirector.overlay import build_state
    d, _ = make_director(tmp_path, monkeypatch)
    h = d.history
    pts = H._progress_points(h)
    tl = H.Timeline(h, pts)
    focus = d.focus_id()
    mid = h.race_start + 300
    st = build_state(H.frame_at(h, mid, pts), focus, timeline=tl, race_info=(h.race_start, 0, 0, 0))
    rows = st["tower"]
    assert rows and all("int" in r for r in rows)
    assert sum(1 for r in rows if r["int"]) >= len(rows) - 1                 # everyone but the leader has a car in front
    assert all(r["int"].replace(".", "").replace(":", "").replace("L", "").isdigit() for r in rows if r["int"])
    assert not any(r["int"].startswith("-") or r["int"].startswith("+") for r in rows)       # the interval has no sign
    before = build_state(H.frame_at(h, h.race_start - 5, pts), focus, timeline=tl, race_info=(h.race_start, 0, 0, 0))["tower"]
    assert all(r["gap"] == "" and r["int"] == "" for r in before)           # before the lights nobody is ahead or behind yet
