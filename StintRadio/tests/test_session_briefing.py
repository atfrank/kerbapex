"""The engineer knows the pit lane and new sessions, and briefs the driver before a session from earlier runs only."""
from types import SimpleNamespace

import pytest

from stintradio import briefing as BR, coach as C, laps as LP
from stintradio.engineer import Engineer
from stintradio.history import CornerHistory
from stintradio.reader import Sample
from stintradio.service import LiveService
from stintradio.session import SessionWatch

from test_stintradio import _lap


def info(**kw):
    d = {"type": 2, "iteration": 1, "phase": 3, "laps": 0, "time_left": -1.0, "time_total": 900.0, "max_laps": 0, "in_pits": False, "pit_state": 0}
    d.update(kw)
    return d


def names(events):
    return [e[0] for e in events]


# ---- the session watcher ---------------------------------------------------------------------------------------------------

def test_a_new_session_is_a_new_track_car_type_or_number_or_the_same_one_started_over():
    w = SessionWatch()
    ev = w.update(info(), "Ring", "GT3")
    assert names(ev) == ["new_session"] and ev[0][1] == {"kind": "race", "restart": False}
    assert w.update(info(laps=1), "Ring", "GT3") == [] and w.update(info(laps=2), "Ring", "GT3") == []
    ev = w.update(info(laps=0), "Ring", "GT3")                           # the lap count went back down: started over
    assert names(ev) == ["new_session"] and ev[0][1]["restart"] is True
    assert names(w.update(info(iteration=2), "Ring", "GT3")) == ["new_session"]       # the next race of the weekend
    assert names(w.update(info(iteration=2, type=0), "Ring", "GT3")) == ["new_session"]    # practice after it
    assert names(w.update(info(iteration=2, type=0), "Other Ring", "GT3")) == ["new_session"]
    t = SessionWatch()
    t.update(info(type=1, time_left=300.0), "Ring", "GT3")
    t.update(info(type=1, time_left=250.0), "Ring", "GT3")
    ev = t.update(info(type=1, time_left=600.0), "Ring", "GT3")           # a timed session's clock went back up
    assert names(ev) == ["new_session"] and ev[0][1]["restart"] is True
    assert SessionWatch().update(None, "Ring", "GT3") == []               # no information: no opinion


def test_the_pit_lane_needs_half_a_second_and_a_stop_is_told_from_a_start_in_the_pits():
    w = SessionWatch()
    w.update(info(), "Ring", "GT3")
    for _ in range(4):
        assert w.update(info(in_pits=True), "Ring", "GT3") == []           # four readings are not yet a stop
    assert w.update(info(in_pits=False), "Ring", "GT3") == [] and not w.in_pits
    for _ in range(4):
        w.update(info(in_pits=True), "Ring", "GT3")
    ev = w.update(info(in_pits=True), "Ring", "GT3")
    assert names(ev) == ["pit_in"] and w.in_pits
    for _ in range(4):
        w.update(info(in_pits=False), "Ring", "GT3")
    ev = w.update(info(in_pits=False), "Ring", "GT3")
    assert names(ev) == ["pit_out"] and ev[0][1] == {"first": False} and not w.in_pits
    s = SessionWatch()                                                       # a practice that starts in the pit lane
    s.update(info(type=0, in_pits=True), "Ring", "GT3")
    assert s.in_pits
    out = []
    for _ in range(6):
        out += s.update(info(type=0, in_pits=False), "Ring", "GT3")
    assert names(out) == ["pit_out"] and out[0][1] == {"first": True}      # the first way out is just the start


# ---- what the driver does in each corner, over many sessions --------------------------------------------------------------

def notes(delta=0.3, brake=-9.0, n=1, throttle=None, gear=None):
    return SimpleNamespace(n=n, corner=f"corner {n}", delta=delta, brake_diff=brake, throttle_s=throttle, my_gear=gear[0] if gear else None,
                           ref_gear=gear[1] if gear else None, apex_off=None)


def test_the_history_finds_the_corners_and_habits_that_come_back_lap_after_lap(tmp_path):
    h = CornerHistory(tmp_path / "h.json")
    corners = [SimpleNamespace(n=1, apex=500.0), SimpleNamespace(n=2, apex=1200.0), SimpleNamespace(n=3, apex=1700.0)]
    for lap in range(5):
        h.add_lap("Ring", "GT3", [notes(0.30, -9.0, 1), notes(0.20, 2.0, 2, throttle=0.4), notes(0.01, 0.0, 3)], corners, 2000.0, 92.0 + lap)
    h.add_lap("Ring", "GT3", [notes(0.5, -20.0, 1)], [SimpleNamespace(n=1, apex=500.0)], 2000.0, 90.0)
    p = h.pointers("Ring", "GT3")
    assert [x["name"] for x in p] == ["corner 1", "corner 2"]                 # corner 3 costs nothing: no pointer
    assert p[0]["habit"] == "brake_early" and 8 <= p[0]["amount"] <= 11
    assert p[1]["habit"] == "throttle" and p[1]["amount"] == pytest.approx(0.4)
    assert h.laps("Ring", "GT3") == 6 and h.data["Ring|GT3"]["best"] == 90.0
    assert CornerHistory(tmp_path / "h.json").pointers("Ring", "GT3")[0]["name"] == "corner 1"     # it is kept
    assert h.pointers("Ring", "Other car") == []
    g = CornerHistory(tmp_path / "g.json")
    for _ in range(4):
        g.add_lap("Ring", "GT3", [notes(0.2, 0.0, 1, gear=(2, 3))], corners[:1], 2000.0)
    assert g.pointers("Ring", "GT3")[0]["habit"] == "gear" and g.pointers("Ring", "GT3")[0]["gear"] == 3
    few = CornerHistory(tmp_path / "f.json")
    few.add_lap("Ring", "GT3", [notes(0.5, -9.0, 1)], corners[:1], 2000.0)
    assert few.pointers("Ring", "GT3") == []                                   # one lap is not a habit


# ---- the briefing: advice for laps not yet driven ----------------------------------------------------------------------------

PAST_OF_THIS_SESSION = ("you lost", "you just", "that lap", "last lap", "just now", "you were", "you braked", "you set a")


def test_the_briefing_talks_about_laps_ahead_and_never_about_laps_of_this_session():
    ptrs = [{"name": "turn five", "habit": "brake_early", "amount": 9.3, "loss": 0.3, "gear": None},
            {"name": "the hairpin", "habit": "throttle", "amount": 0.4, "loss": 0.2, "gear": None},
            {"name": "the esses", "habit": "gear", "amount": 0, "loss": 0.2, "gear": 3}]
    race = BR.briefing("race", 92.4, {"best": 93.1, "laps": 8, "date": "2026-10-30"}, ptrs, 12, {"laps": 12, "minutes": 0})
    low = race.text.lower()
    assert race.kind == "brief" and low.startswith("race briefing. twelve laps.") and "the lap to beat is one thirty-two four" in low
    assert "at turn five" in low and "ten metres early" in low and "then at the hairpin" in low
    assert "esses" not in low                                                  # a race gets two pointers, no more
    assert "first lap" in low and not any(w in low for w in PAST_OF_THIS_SESSION) and not any(ch.isdigit() for ch in low)
    practice = BR.briefing("practice", None, {"best": 93.1, "laps": 8}, ptrs, 12)
    assert "last time out here you set a one thirty-three one" in practice.text.lower() and "third gear" in practice.text.lower()
    assert BR.briefing("qualifying", 90.0, None, [], 0).text.lower().count("out-lap") == 1
    first = BR.briefing("practice", None, None, [], 0).text.lower()
    assert "haven't got much on you" in first and "lap to beat" not in first
    assert "consistent" in BR.briefing("race", 90.0, None, [], 7).text.lower()


def test_pit_calls_are_for_a_race_and_say_what_is_useful():
    assert BR.pit_in("race", 34).text == "In the pits. The tyres are about thirty percent worn. I'll stay quiet until you're back out."
    assert BR.pit_in("race", None).text == "In the pits. I'll stay quiet until you're back out."
    assert "out-lap" in BR.pit_out("race").text and "next lap" in BR.pit_out("race").text


# ---- the service: new sessions, the grid, the pit lane --------------------------------------------------------------------

def sample(t, lap=1, speed=0.0, dist=5.0, track="Ring", car="GT3"):
    return Sample(t=t, lap=lap, lap_start=100.0, dist=dist, speed=speed, throttle=0, brake=0, steer=0, gear=0, rpm=0, x=0, z=0,
                  delta_best=0, invalid=False, track=track, car=car, track_len=2000.0, last_lap=-1, best_lap=-1)


def service(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    svc = LiveService("")
    svc.engineer = Engineer()
    svc.corner_history = CornerHistory(tmp_path / "h.json")
    svc.said = []
    svc._radio = lambda m, gated=True: svc.said.append(m)
    return svc


def test_the_briefing_is_said_once_on_the_grid_a_new_session_starts_fresh_and_a_late_joiner_gets_none(tmp_path, monkeypatch):
    svc = service(tmp_path, monkeypatch)
    svc._info = info()
    svc.step(sample(100.0))
    svc.step(sample(100.5))
    assert svc.said == []                                                     # not yet: the reference and the session settle first
    svc.step(sample(102.0))
    assert [m.kind for m in svc.said] == ["brief"] and "Race briefing" in svc.said[0].text
    svc.step(sample(103.0))
    svc.step(sample(150.0))
    assert len(svc.said) == 1                                                  # once
    old = svc.engineer
    svc.lap_log.append((1, 93.0))
    svc._info = info(iteration=2)                                              # the next race
    svc.step(sample(200.0))
    assert svc.engineer is not old and svc.lap_log == [] and svc._brief_pending is not None
    svc.step(sample(202.0))
    assert len(svc.said) == 2
    svc.said.clear()
    svc._info = info(iteration=3)                                              # started the coach in the middle of a race
    svc.step(sample(300.0, lap=6, speed=180.0))
    svc.step(sample(340.0, lap=6, speed=180.0))
    assert svc.said == [] and svc._brief_pending is None


def test_laps_with_the_pit_lane_in_them_are_not_judged_saved_or_debriefed(tmp_path, monkeypatch):
    svc = service(tmp_path, monkeypatch)
    ref = _lap(480, 60.0)
    base = 2000.0
    for lap_no in (1, 2, 3, 4):
        for p in ref.samples:
            pits = lap_no == 2 and 800 < p[0] < 1000                           # lap two goes through the pit lane
            svc._info = info(laps=lap_no - 1, in_pits=pits, type=0)
            svc.step(Sample(t=base + p[6], lap=lap_no, lap_start=base, dist=p[0], speed=p[1], throttle=p[3], brake=p[2], steer=0, gear=3,
                            rpm=0, x=p[4], z=p[5], delta_best=0, invalid=False, track="Ring", car="GT3", track_len=2000.0,
                            last_lap=-1, best_lap=-1))
        base += ref.samples[-1][6] + 0.02
    assert [n for n, _t in svc.lap_log] == [1, 3]                              # lap two is not a lap
    assert len(svc.saved) == 2 and svc.corner_history.laps("Ring", "GT3") <= 2
    assert not any(n.lap == 2 for n in svc.lap_obs) and not any(n.lap == 2 for n in svc.notes)


def test_the_pit_calls_are_made_in_a_race_only(tmp_path, monkeypatch):
    svc = service(tmp_path, monkeypatch)
    svc._info = info(type=2)
    svc._pit_in()
    svc._pit_out({"first": False})
    svc._pit_out({"first": True})
    assert [m.kind for m in svc.said] == ["pit", "pit"]
    svc.said.clear()
    svc._info = info(type=0)                                                   # practice: in and out all the time, said nothing
    svc._pit_in()
    svc._pit_out({"first": False})
    assert svc.said == []


def test_the_briefing_can_be_heard_without_driving(tmp_path, monkeypatch):
    svc = service(tmp_path, monkeypatch)
    svc.memory.sessions["Ring|GT3"] = {"best": 93.1, "laps": 8, "race": True, "date": "2026-10-30"}
    out = svc.preview_briefing(kind="practice")                                # no sample yet: the last track and car are used
    assert out["track"] == "Ring" and out["car"] == "GT3" and out["text"].startswith("Practice.") and out["spoken"] is False
    out = svc.preview_briefing(kind="race", say=True)
    assert out["spoken"] is True and [m.kind for m in svc.said] == ["brief"]
    assert LiveService("").preview_briefing()["text"] == ""                    # nothing known at all: it says so
