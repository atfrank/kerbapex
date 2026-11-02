import json
from pathlib import Path

from driverdirector import results, sectors as S
from driverdirector.history import Driver, Lap, RaceHistory

FX = Path(__file__).parent / "fixtures"


def mini():
    """Two drivers, three laps each; B's lap 2 is invalid (only S1 recorded)."""
    h = RaceHistory({1: Driver(1, "A", "1", "GT3"), 2: Driver(2, "B", "2", "GT3")}, {}, [], 0.0)
    def lap(n, et, s, t): return Lap(n, 1, t, False, False, et, (), s)
    h.laps = {
        1: [lap(1, 100, (25.0, 50.0, 25.0), 100.0), lap(2, 200, (24.0, 50.5, 25.5), 100.0), lap(3, 300, (24.5, 49.0, 26.5), 100.0)],
        2: [lap(1, 101, (26.0, 49.0, 25.0), 100.0), lap(2, 202, (23.0, -1, -1), -1.0), lap(3, 302, (25.0, 48.0, 27.0), 100.0)],
    }
    h.exact = True
    return h


def test_colours_follow_timing_screen_rules():
    laps = S.build(mini())
    a1, a2, a3 = (laps[(1, n)] for n in (1, 2, 3))
    assert [c.color for c in a1.cells] == ["purple", "purple", "purple"]       # first laps set the bests
    assert a2.cells[0].color == "purple"       # 24.0 beats everyone so far
    assert a2.cells[1].color == "orange"       # 50.5 is slower than his own 50.0
    assert a3.cells[1].color in ("purple", "green")                            # 49.0 beats his own 50.0
    b1 = laps[(2, 1)]
    assert b1.cells[0].color == "green"        # B's first S1 is his personal best, though A's 25.0 is quicker
    assert b1.cells[1].color == "purple"       # 49.0 beats the class best of 50.0


def test_invalid_lap_is_estimated_flagged_and_never_sets_a_best():
    laps = S.build(mini())
    b2 = laps[(2, 2)]
    assert not b2.valid and b2.cells[0].time == 23.0 and not b2.cells[0].est       # recorded S1 is kept
    assert b2.cells[1].est and b2.cells[2].est
    total = sum(c.time for c in b2.cells)
    assert abs(total - (202 - 101)) < 1e-6                                          # estimates fill the true lap duration
    assert b2.cells[1].color != "purple" and b2.cells[2].color != "purple"          # an estimate never takes purple
    # B's invalid 23.0 S1 looks purple by comparison, but must not become the benchmark for later laps
    assert laps[(2, 3)].cells[0].color != "purple" or laps[(2, 3)].cells[0].time < 23.0
    assert laps[(1, 3)].cells[0].color != "purple"                                  # 24.5 vs A's 24.0 best: not purple


def test_panel_fills_in_as_sectors_are_crossed():
    laps = S.build(mini())
    a2 = laps[(1, 2)]
    t_mid = a2.crossings[0] + 1.0                       # just after S1 of lap 2
    p = S.panel(laps, 1, t_mid)
    assert p["lap"] == 2 and p["cells"][0] and p["cells"][1] is None and p["lap_time"] is None
    p = S.panel(laps, 1, 200.0 + 2.0)                   # just after the line: finished lap held on screen
    assert p["lap"] == 2 and all(p["cells"]) and p["lap_time"]["time"] == 100.0
    p = S.panel(laps, 1, 200.0 + 30.0)                  # later: now showing lap 3 in progress
    assert p["lap"] == 3 and p["lap_time"] is None


def test_real_race_sectors():
    raw = json.loads((FX / "interlagos_race.json").read_text())
    h = results.parse(str(FX / "interlagos_results.xml"), raw["standings"])
    laps = S.build(h)
    assert len(laps) == 456              # unfinished final laps (no time, no next lap) have no end
    invalid = [ls for ls in laps.values() if not ls.valid]
    assert len(invalid) == 55 and all(any(c is not None for c in ls.cells) for ls in invalid)
    est = [c for ls in laps.values() for c in ls.cells if c and c.est]
    assert est and all(c.color != "purple" for c in est)
    for ls in laps.values():
        got = [c.time for c in ls.cells if c]
        if ls.valid:                                                                 # recorded sectors add up to the lap
            assert abs(sum(got) - ls.lap_time.time) < 0.05, (ls.slot, ls.lap)
        elif len(got) == 3 and ls.lap_time:                                          # estimates fill the lap exactly
            assert abs(sum(got) - ls.lap_time.time) < 1e-6, (ls.slot, ls.lap)
    assert all(ls.lap_time.est for ls in invalid if ls.lap_time and not any(
        l.time > 0 for l in h.laps[ls.slot] if l.n == ls.lap))
    # purple always strictly improves the class best so far (valid, recorded times only)
    for k in range(3):
        best = None
        for ls in sorted(laps.values(), key=lambda x: x.crossings[k] or 1e9):
            c = ls.cells[k]
            if not c or ls.crossings[k] is None:
                continue
            if c.color == "purple":
                assert best is None or c.time < best
            if ls.valid and not c.est:
                best = c.time if best is None else min(best, c.time)


def test_overlay_state_carries_panel():
    from driverdirector.overlay import build_state
    st = build_state(None, 1, sectors={"lap": 3})
    assert st["sectors"] == {"lap": 3} or st["sectors"] is None     # no frame -> empty state shape still has the key
    assert "sectors" in st
