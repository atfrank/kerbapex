"""Driving-mistake analysis on synthetic telemetry files (same layout as the game's DuckDB files)."""
import duckdb
import pytest

from driverdirector import driving, telemetry as T
from driverdirector.models import Event

L = 3000.0           # synthetic track length, metres; one corner, apex at 1500 m
STEP = 0.01          # seconds per simulation step (100 Hz)


def v_ref(d):        # km/h: brakes from 1250 m down to 80 at the apex, back on the power by 1650 m
    if d < 1250:
        return 200.0
    if d < 1500:
        return 200 - 120 * (d - 1250) / 250
    return 80 + 120 * min(1.0, (d - 1500) / 150)


def v_deep(d):       # brakes later and harder, 62 at the apex, slow to build speed again
    if d < 1350:
        return 200.0
    if d < 1500:
        return 200 - 138 * (d - 1350) / 150
    return 62 + 138 * min(1.0, (d - 1500) / 350)


def v_slow(d):       # the same lap 12 percent slower everywhere, as in traffic
    return 0.88 * v_ref(d)


def v_out(d):
    return 110.0


def write_session(path, track, kind, driver, laps, off=7.0, untimed=()):
    """laps: list of speed profiles, driven back to back. `untimed`: lap indexes with no Lap Time row (an out lap)."""
    d, spd, brk, thr, dist, ends = 0.0, [], [], [], [], []
    step = 0
    for prof in laps:
        n0 = step
        while True:
            v = prof(d)
            spd.append(v)
            if step % 2 == 0:
                braking = (prof is not v_out) and (1250 <= d < 1500 if prof is not v_deep else 1350 <= d < 1500)
                brk.append(100.0 if braking else 0.0)
                thr.append(0.0 if d >= 1250 and d < 1530 and prof is not v_out else 100.0)
            if step % 10 == 0:
                dist.append(d)
            d += v / 3.6 * STEP
            step += 1
            if d >= L:
                d -= L
                break
        ends.append((step, (step - n0) * STEP))
    con = duckdb.connect(str(path))
    for name, vals in (("Ground Speed", spd), ("Brake Pos", brk), ("Throttle Pos", thr), ("Lap Dist", dist),
                       ("Path Lateral", [0.0] * len(dist)), ("Track Edge", [6.0] * len(dist))):
        con.execute(f'create table "{name}"(value DOUBLE)')
        csv = path.with_name(f"{name.replace(' ', '_')}.csv")           # bulk load: binding long lists is very slow
        csv.write_text(chr(10).join(["value"] + [repr(float(x)) for x in vals]), encoding="ascii")
        con.execute(f"copy \"{name}\" from '{csv.as_posix()}' (header true)")
        csv.unlink()
    con.execute("create table Lap(ts DOUBLE, value DOUBLE)")
    con.execute('create table "Lap Time"(ts DOUBLE, value DOUBLE)')
    con.execute("insert into Lap values (?, 0)", [off])
    for k, (end_step, t) in enumerate(ends, 1):
        ts = off + end_step * STEP
        con.execute("insert into Lap values (?, ?)", [ts, k])
        if (k - 1) not in untimed:
            con.execute('insert into "Lap Time" values (?, ?)', [ts, t])
    con.execute("create table metadata(key VARCHAR, value VARCHAR)")
    con.executemany("insert into metadata values (?, ?)",
                    [("DriverName", driver), ("SessionType", {"R": "Race", "Q": "Qualify"}[kind]), ("TrackName", track)])
    con.close()
    return [t for _, t in ends]


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "Telemetry"
    f.mkdir()
    return f


def make_event(folder, deep=True):
    qt = write_session(folder / "Test Ring_Q_2026-01-01T00_00_00Z.duckdb", "Test Ring", "Q", "Alex Hartley",
                       [v_out, v_ref], off=13.0, untimed=(0,))
    # race lap 1 is the opening lap (never judged); lap 2 has the mistake; lap 4 is slow everywhere
    rt = write_session(folder / "Test Ring_R_2026-01-01T01_00_00Z.duckdb", "Test Ring", "R", "Alex Hartley",
                       [v_ref, v_deep if deep else v_ref, v_ref, v_slow, v_ref], off=6.0)
    return qt[1], rt


def test_telemetry_clock_and_laps_are_read_like_the_game_files(folder):
    _, rt = make_event(folder)
    race = T.load(folder / "Test Ring_R_2026-01-01T01_00_00Z.duckdb")
    assert [l.n for l in race.laps] == [1, 2, 3, 4, 5]
    assert race.laps[0].start == pytest.approx(6.0, abs=0.02)            # sample 0 is at the first Lap row
    tr = T.Trace(T.lap_trace(race, race.laps[1].start, race.laps[1].end))
    assert tr.s[0][0] < 30 and tr.s[-1][0] > L - 80 and tr.s[0][6] == pytest.approx(race.laps[1].start, abs=0.05)
    q = T.load(folder / "Test Ring_Q_2026-01-01T00_00_00Z.duckdb")
    assert len(q.laps) == 1 and q.laps[0].n == 2                          # the untimed out lap is not matched by position


def test_an_over_fast_entry_is_found_with_measured_causes(folder):
    qbest, rt = make_event(folder)
    ms, note = driving.find_mistakes(folder, "Test Ring", "Alex Hartley", rt, qbest, 0.25, 6)
    assert note == "compared with your best qualifying lap"
    assert [m.lap for m in ms] == [2]                                     # not the clean laps, not the slow lap
    m = ms[0]
    assert m.loss > 0.5 and 1100 <= m.d0 <= 1500 and m.d1 >= 1600
    assert m.causes[0].startswith("went in too deep") and "faster than your best qualifying lap" in m.causes[0]
    assert any(c.startswith("had a poor exit") for c in m.causes)
    assert "lap 2" in m.desc and f"lost {m.loss:.1f} seconds" in m.desc
    ev = m.event(7)
    assert (ev.kind, ev.drivers, ev.confidence, ev.peak) == ("mistake", [7], "exact", m.t1) and ev.score >= 4.25


def test_a_uniformly_slow_lap_is_not_blamed(folder):
    qbest, rt = make_event(folder, deep=False)
    ms, _ = driving.find_mistakes(folder, "Test Ring", "Alex Hartley", rt, qbest, 0.25, 6)
    assert ms == []                                                       # time was lost on lap 4, but with no cause


def test_without_qualifying_telemetry_the_best_race_lap_is_the_reference(folder):
    qbest, rt = make_event(folder)
    (folder / "Test Ring_Q_2026-01-01T00_00_00Z.duckdb").unlink()
    ms, note = driving.find_mistakes(folder, "Test Ring", "Alex Hartley", rt, qbest, 0.25, 6)
    assert note == "compared with your best race lap" and [m.lap for m in ms] == [2]


def test_a_file_is_matched_by_its_laps_and_its_driver(folder):
    qbest, rt = make_event(folder)
    ms, note = driving.find_mistakes(folder, "Test Ring", "Someone Else", rt, qbest, 0.25, 6)
    assert ms == [] and "no telemetry file" in note and "player's own car" in note
    ms, note = driving.find_mistakes(folder, "Test Ring", "Alex Hartley", [101.1, 102.2, 103.3], qbest, 0.25, 6)
    assert ms == [] and "no telemetry file" in note                       # another race's lap times
    assert driving.find_mistakes(None, "Test Ring", "Alex Hartley", rt, qbest, 0.25, 6)[0] == []


def test_selection_spreads_mentions_and_leaves_incidents_to_the_incident_story():
    mk = lambda lap, t, loss: driving.Mistake(lap, 0, 100, t, t + 5, loss, ["x"], "w", "d")
    found = [mk(2, 100, 1.0), mk(2, 110, 0.9), mk(3, 300, 0.5), mk(4, 500, 0.4), mk(5, 700, 0.3)]
    kept = driving.select(found, 3)
    assert [m.t0 for m in kept] == [100, 300, 500]                        # 110 is too close to 100; worst three, in order
    assert [m.t0 for m in driving.select(found, 5, windows=[(295, 310)])] == [100, 500, 700]


def test_corner_names_come_from_the_confirmed_table_and_a_user_file(tmp_path, monkeypatch):
    assert driving.corner_name("Circuit de Spa-Francorchamps", 6620) == "the Bus Stop"
    assert driving.corner_name("Circuit de Spa-Francorchamps", 3000) == ""
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    (tmp_path / "corners.json").write_text('{"Test Ring": [[1400, 1600, "the Hairpin"]]}', encoding="utf-8")
    assert driving.corner_name("Test Ring", 1500) == "the Hairpin"


# ---- planning and narration ---------------------------------------------------------------------------------
from .test_record import make_director


class FakeSession:
    """Stands in for a driving.Session: fixed mistakes, and a fixed corner wherever the focus car is asked about."""
    def __init__(self, ms, corner="corner two"):
        self.ms, self.corner = ms, corner

    def mistakes(self, *a, **k):
        return self.ms

    def where(self, t):
        return self.corner


def _mistake(green, lap=2):
    return driving.Mistake(lap, 1250, 1700, green + 40, green + 46, 1.3, ["went in too deep (32 km/h faster)"], "the Bus Stop",
                           "lap 2, the Bus Stop: went in too deep (32 km/h faster); lost 1.3 seconds")


def test_mistakes_become_events_cues_and_a_scene_after_the_moment(tmp_path, monkeypatch):
    from driverdirector import commentary as cm
    d, clock = make_director(tmp_path, monkeypatch)
    green = d.frames[0].t
    d.s.direct.t_start, d.s.direct.t_end = green - 5, green + 120
    m = _mistake(green)
    monkeypatch.setattr(driving, "open_session", lambda *a, **k: (FakeSession([m]), "compared with your best qualifying lap"))
    d.analyze()
    plan = d.make_plan()
    ev = next(e for e in plan.events if e.kind == "mistake")
    assert ev.involves(plan.focus_id) and (ev.t_start, ev.t_end) == (m.t0, m.t1)
    assert d.mistakes == [m] and d.driving_note == "compared with your best qualifying lap"
    assert not any(i.event is ev for i in plan.inlays)                    # told by the narrator, not a picture-in-picture
    scene = next(s for s in cm.build_scenes(plan, d.frames, history=d.history) if s.kind == "mistake")
    assert scene.t >= m.t1                                                # spoken once the viewer has seen it
    assert "went in too deep" in scene.facts and "1.3" in scene.facts and "TV commentator" in scene.facts


def test_planning_never_fails_because_of_telemetry(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.analyze()
    monkeypatch.setattr(driving, "open_session", lambda *a, **k: (_ for _ in ()).throw(OSError("file is locked")))
    plan = d.make_plan()
    assert plan and d.mistakes == [] and "driving analysis skipped" in d.driving_note and "locked" in d.driving_note
    d.s.direct.driving_feedback = False
    d.make_plan()
    assert d.driving_note == ""


# ---- said the way people say it ----------------------------------------------------------------------------------
import re


def test_times_speeds_and_distances_are_said_in_everyday_units():
    assert driving.say_seconds(0.3) == "a few tenths of a second"
    assert driving.say_seconds(0.52) == "about half a second"
    assert driving.say_seconds(1.02) == "about a second"
    assert driving.say_seconds(1.58) == "about a second and a half"
    assert driving.say_seconds(2.6) == "about three seconds"
    assert driving.say_speed(32) == "about thirty kilometres an hour"
    assert driving.say_speed(-14) == "about fifteen kilometres an hour"
    assert driving.say_speed(3) == "a few kilometres an hour"
    assert driving.say_along(57) == "about eleven car lengths"
    assert driving.say_along(6) == "a few metres"
    assert driving.say_across(0.8) == "a metre or so" and driving.say_across(2.0) == "a couple of metres"
    assert driving.say_across(7.6) == "about eight metres"
    assert driving.say_over(0.3) == "just over the white line" and "wheel" in driving.say_over(1.2)
    assert driving.say_lap_position(3150) == "about three kilometres into the lap"
    assert driving.say_lap_position(3600) == "about three and a half kilometres into the lap"
    assert driving.say_lap_position(300) == "about half a kilometre into the lap"


def test_the_narrator_is_given_a_descriptive_sentence_without_digits_or_a_pronoun(folder):
    qbest, rt = make_event(folder)
    ms, _ = driving.find_mistakes(folder, "Test Ring", "Alex Hartley", rt, qbest, 0.25, 6)
    m = ms[0]
    assert m.desc != m.spoken and "km/h" in m.desc                        # the precise version stays for the plan
    assert not re.search(r"\d", m.spoken) and "km/h" not in m.spoken      # numbers are words, units are spoken
    assert m.spoken.startswith("On lap two, at corner one, the driver came in far too hot")
    assert "came in far too hot" in m.spoken and "kilometres an hour" in m.spoken
    assert re.search(r"That cost (about|a few|getting|just)", m.spoken)
    assert not re.search(r"(he|she|him|her|his|hers)", m.spoken.lower())
    assert m.event(7).desc == m.spoken                                     # the event the commentary reads carries it


def test_the_commentary_prompt_asks_for_vivid_speech_and_no_assumed_pronouns():
    from driverdirector import commentary as cm
    from driverdirector.config import CommentarySettings
    p = cm.build_prompt([cm.Scene(0, 10.0, 5.0, "mistake", "facts", 1)], CommentarySettings())
    flat = " ".join(p.split())                                       # the prompt wraps lines: compare on one line
    assert "assumes a driver's gender" in flat and "vivid" in flat and "turn them back into digits" in flat
    assert "excited" in CommentarySettings().style and "exclamation" in CommentarySettings().style
    assert "live television race commentary" in flat and "replay-analysis mode" in flat and "never begin two lines the same way" in flat


def test_a_big_loss_is_voiced_with_the_most_excitement(tmp_path, monkeypatch):
    from driverdirector import commentary as cm
    d, clock = make_director(tmp_path, monkeypatch)
    green = d.frames[0].t
    d.s.direct.t_start, d.s.direct.t_end = green - 5, green + 300
    small, big = _mistake(green), _mistake(green)
    small.loss, big.loss = 0.3, 1.6
    small.t0, small.t1, big.t0, big.t1 = green + 30, green + 36, green + 150, green + 156
    monkeypatch.setattr(driving, "open_session", lambda *a, **k: (FakeSession([small, big]), "x"))
    d.analyze()
    plan = d.make_plan()
    ex = {s.t: s.excite for s in cm.build_scenes(plan, d.frames, history=d.history) if s.kind == "mistake"}
    assert sorted(ex.values()) == [1, 2]


# ---- corners on any track, and where on the lap a moment was ------------------------------------------------------
def _ref_trace(folder):
    q = T.load(folder / "Test Ring_Q_2026-01-01T00_00_00Z.duckdb")
    return T.Trace(T.lap_trace(q, q.laps[0].start, q.laps[0].end)), q


def test_corners_are_found_and_numbered_on_any_track(folder):
    make_event(folder)
    ref, _ = _ref_trace(folder)
    apexes = driving.corner_apexes(ref)
    assert len(apexes) == 1 and abs(apexes[0] - 1500) < 60                       # the one slow corner of the test track
    assert driving.corner_label("Test Ring", apexes[0] + 40, apexes) == "corner one"
    assert driving.corner_label("Test Ring", 400, apexes) == ""                  # the straight: no corner to name
    assert driving.corner_label("Test Ring", 1500, []) == ""
    assert driving.corner_label("Circuit de Spa-Francorchamps", 6620, apexes) == "the Bus Stop"   # a known name wins


def test_the_session_says_which_corner_the_car_was_at_at_a_moment(folder):
    qbest, rt = make_event(folder)
    session, note = driving.open_session(folder, "Test Ring", "Alex Hartley", rt, qbest)
    assert session and note == "compared with your best qualifying lap"
    lap2 = next(l for l in session.race.laps if l.n == 2)
    tr = T.Trace(T.lap_trace(session.race, lap2.start, lap2.end))
    assert session.where(tr.s[tr.idx(1510)][6]) == "corner one"                  # at the apex
    assert session.where(tr.s[tr.idx(1950)][6]) == ""                           # well past it, back on the straight
    lap3 = session.race.laps[2]
    assert session.where(lap3.start + 2.0) == ""                                 # just after the line: no corner
    assert session.where(-5.0) == "" and session.where(1e9) == ""                # outside any timed lap
    none, why = driving.open_session(folder, "Test Ring", "Someone Else", rt, qbest)
    assert none is None and "no telemetry file" in why


# ---- the cars around: gaps and which way they are moving (any driver, any replay) ------------------------------------
from driverdirector import context as ctxmod
from driverdirector.phrasing import say_gap


def test_gaps_are_said_the_way_people_say_them():
    assert say_gap(0.2) == "a few tenths of a second" and say_gap(0.5) == "about half a second"
    assert say_gap(1.5) == "about a second and a half" and say_gap(2.0) == "about two seconds"
    assert say_gap(3.4) == "about three and a half seconds" and say_gap(5.1) == "about five seconds"


def test_the_cars_around_and_how_the_gaps_are_moving(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    ctx = ctxmod.RaceContext(d.history)
    focus = d.focus_id()
    t = d.history.race_start + 600
    around = ctx.around(t, focus)
    assert around["ahead"] is not None or around["behind"] is not None
    for key in ("ahead", "behind"):
        if around[key]:
            car, gap = around[key]
            assert car.id != focus and gap >= 0
    facts = ctxmod.around_facts(ctx, t, focus)
    assert facts and not re.search(r"\d", facts) and "km/h" not in facts
    assert re.search(r"(up the road|behind|back of)", facts) and facts.endswith(".")
    assert re.search(r"(closing|losing ground|pulling away|held steady)", facts)
    assert not re.search(r"(he|she|him|her|his|hers)", facts.lower())
    assert ctxmod.where_in_sector(ctx, focus, t) in ("in sector one", "in sector two", "in sector three")


def test_what_a_mistake_did_to_the_gaps(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    ctx = ctxmod.RaceContext(d.history)
    focus = d.focus_id()
    # over a few minutes of the race some gap moves by more than a quarter of a second: it must be reported, in words
    said = [ctxmod.gap_effect(ctx, t, t + 60, focus) for t in range(int(d.history.race_start) + 120, int(d.history.race_end) - 120, 60)]
    said = [x for x in said if x]
    assert said and all(x.startswith("In that stretch ") and not re.search(r"\d", x) for x in said)


# ---- the scenes carry gaps, trends and where on track it was --------------------------------------------------------
def test_status_scenes_say_who_is_around_and_which_way_the_time_is_moving(tmp_path, monkeypatch):
    from driverdirector import commentary as cm
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct.t_start, d.s.direct.t_end = None, None
    d.analyze()
    plan = d.make_plan()
    scenes = cm.build_scenes(plan, d.frames, history=d.history, ctx=d.race_context(), locate=d.locate_event)
    status = [x for x in scenes if x.kind == "status"]
    assert status
    story = [x for x in status if re.search(r"(up the road|behind|back of)", x.facts)]
    assert story, "no status scene mentions the cars around"
    assert any(re.search(r"(closing|losing ground|pulling away|held steady)", x.facts) for x in story)
    assert all("km/h" not in x.facts and not re.search(r"seconds? (gap|behind).*\d\.\d", x.facts) for x in story)
    # without the context the scenes are as before: just the names
    plain = [x for x in cm.build_scenes(plan, d.frames, history=d.history) if x.kind == "status"]
    assert plain and all("up the road" not in x.facts for x in plain)


def test_events_say_where_on_track_with_a_corner_for_the_focus_car_and_a_sector_for_anyone(tmp_path, monkeypatch):
    from driverdirector import commentary as cm
    d, clock = make_director(tmp_path, monkeypatch)
    d.analyze()
    focus = d.focus_id()
    other = next(c.id for c in d.frames[0].cars if c.id != focus)
    t = d.history.race_start + 300
    mine = Event("incident", t, t + 4, [focus], 9.0, "contact with the wall", peak=t)
    theirs = Event("incident", t + 100, t + 104, [other], 9.0, "spins", peak=t + 100)
    d.plan = d.make_plan()
    assert d.locate_event(theirs) in ("in sector one", "in sector two", "in sector three")    # no telemetry: the sector
    d.session = FakeSession([], "the Bus Stop")
    assert d.locate_event(mine) == "at the Bus Stop"                                          # telemetry: the corner
    assert d.locate_event(theirs).startswith("in sector")                                     # still the sector for others
    d.plan.events = [mine, theirs]
    d.plan.inlays = []
    d.plan.t0, d.plan.t1 = t - 20, t + 160                      # the plan's window must contain the events
    scenes = cm.build_scenes(d.plan, d.frames, history=d.history, ctx=d.race_context(), locate=d.locate_event)
    inc = [x for x in scenes if x.kind == "incident"]
    assert any("Where on track: at the Bus Stop." in x.facts for x in inc)


def test_a_mistake_scene_says_what_it_did_to_the_gaps(tmp_path, monkeypatch):
    from driverdirector import commentary as cm
    d, clock = make_director(tmp_path, monkeypatch)
    d.analyze()
    plan = d.make_plan()
    focus = plan.focus_id
    ctx = d.race_context()
    # find a minute of this race over which a gap really moved, and put the mistake there
    t0 = next(t for t in range(int(d.history.race_start) + 120, int(d.history.race_end) - 120, 30)
              if ctxmod.gap_effect(ctx, t, t + 40, focus))
    m = driving.Mistake(3, 0, 100, t0, t0 + 40, 0.8, ["x"], "the Bus Stop", "desc", "On lap three, at the Bus Stop, the driver braked late.")
    plan.events = [m.event(focus)]
    plan.inlays = []
    plan.t0, plan.t1 = t0 - 20, t0 + 120
    scene = next(x for x in cm.build_scenes(plan, d.frames, history=d.history, ctx=ctx) if x.kind == "mistake")
    assert "braked late." in scene.facts and "In that stretch" in scene.facts


def test_gap_wording_reads_naturally_in_every_sentence_it_is_used_in(tmp_path, monkeypatch):
    from driverdirector.phrasing import say_seconds
    assert say_seconds(1.75) == "nearly two seconds"                              # "taking nearly two seconds out of the gap"
    d, clock = make_director(tmp_path, monkeypatch)
    ctx = ctxmod.RaceContext(d.history)
    focus = d.focus_id()
    effects = [ctxmod.gap_effect(ctx, t, t + 60, focus) for t in range(int(d.history.race_start) + 100, int(d.history.race_end) - 100, 20)]
    for e in filter(None, effects):
        m = re.findall(r"from (about [a-z ]+?|a few tenths of a second|just over a second) to (about [a-z ]+?|a few tenths of a second|just over a second)(?: behind)?\b", e)
        assert all(a != b for a, b in m), e                                         # never "from X to X"
    car = next(c for c in ctx.frame(d.history.race_start + 100).cars if c.id != focus)
    car.driver = "BRUNO BURRITTO"
    assert ctxmod._name(car) == "Bruno Burritto"                                    # shouted names are title-cased
    car.driver = "R DeathShot"
    assert ctxmod._name(car) == "R DeathShot"
