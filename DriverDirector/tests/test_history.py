import json
from pathlib import Path

from driverdirector import history as H
from driverdirector.config import CameraSettings, DirectSettings
from driverdirector.planner import build_plan

FIX = Path(__file__).parent / "fixtures" / "interlagos_race.json"


def load():
    d = json.loads(FIX.read_text())
    return H.parse(d["history"], d["incidents"], d["session"], d["standings"])


def test_parse_and_times():
    h = load()
    assert len(h.drivers) == 29 and h.incidents
    assert h.race_start > 0 and h.race_end > h.race_start
    s = next(iter(h.t_end))
    laps = sorted(h.t_end[s])
    assert all(h.t_end[s][a] < h.t_end[s][b] for a, b in zip(laps, laps[1:]))   # monotonic


def test_events_and_plan():
    h = load()
    ev = H.derive_events(h)
    assert {e.confidence for e in ev} == {"exact", "lap"}
    assert all(e.confidence == "exact" for e in ev if e.kind == "incident")
    focus = h.by_name("Alex Hartley")
    plan = build_plan(H.frames_from_history(h), ev, focus, DirectSettings(), CameraSettings())
    assert plan.inlays
    for i in plan.inlays:                                   # only second-accurate events become PiP boxes
        assert i.event.confidence == "exact" and not i.event.involves(focus)
    wins = H.scan_windows(ev, focus)
    assert wins and all(a < b for a, b in wins)


def test_race_start_override():
    d = json.loads(FIX.read_text())
    h = H.parse(d["history"], d["incidents"], d["session"], d["standings"], race_start=100.0)
    assert h.race_start == 100.0


def test_results_xml_exact_times():
    from driverdirector import results
    d = json.loads(FIX.read_text())
    h = results.parse(str(FIX.with_name("interlagos_results.xml")), d["standings"])
    assert h.exact and h.source == "xml" and len(h.drivers) == 29
    assert abs(h.race_start - 125.9) < 1.0 and h.race_end > 1900     # lap 1 starts at the green flag, after the formation lap
    ev = H.derive_events(h)
    assert {"incident", "battle", "overtake", "penalty"} <= {e.kind for e in ev}
    assert all(e.confidence in ("exact", "est") for e in ev)
    # the XML incident times are the same as the REST list, i.e. one shared time axis
    rest = {round(i["et"], 1) for i in d["incidents"]}
    assert len(rest & {round(i.et, 1) for i in h.incidents}) / len(rest) > 0.8
    focus = h.by_name("Alex Hartley")
    plan = build_plan(H.frames_from_history(h), ev, focus, DirectSettings(), CameraSettings())
    assert plan.inlays and all(not i.event.involves(focus) or i.event.kind == "overtake" for i in plan.inlays)
    for i in plan.inlays:
        assert i.show_at <= (i.event.peak or i.show_at) <= i.show_until      # box covers the key moment


def test_find_results_by_incident_fingerprint(tmp_path):
    import shutil
    from driverdirector import results
    d = json.loads(FIX.read_text())
    shutil.copy(FIX.with_name("interlagos_results.xml"), tmp_path / "a.xml")
    got = results.find_results(tmp_path, "Autódromo José Carlos Pace", [i["et"] for i in d["incidents"]])
    assert got and got.endswith("a.xml")
    assert results.find_results(tmp_path, "Autódromo José Carlos Pace", [9999.0]) is None


def test_lap_et_is_the_lap_start_so_the_race_ends_after_the_last_lap_not_at_its_start():
    from driverdirector import results
    d = json.loads(FIX.read_text())
    h = results.parse(str(FIX.with_name("interlagos_results.xml")), d["standings"])
    rows = [ls for ls in h.laps.values() if ls and ls[0].time > 0]
    starts = {round(l[0].et - l[0].time, 0) for l in rows}
    assert len(starts) == 1                                  # every car's lap 1 begins at the green flag
    for slot, ls in h.laps.items():                          # a car's laps run back to back from the green flag
        done = [l for l in ls if l.time > 0 and l.et]
        if len(done) == len(ls) and len(ls) > 3:
            assert abs(ls[-1].et - (h.race_start + sum(l.time for l in ls))) < 3.0, slot
    assert h.race_end > h.race_start + 1800
