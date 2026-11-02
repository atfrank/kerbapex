"""A recorded session replayed over a game replay: finding the recording, finding the moment, rebuilding the frame."""
import sqlite3
import struct

import pytest

from stintradio import sessionreplay as sr
from stintradio.r3e_fields import FIELDS
from stintradio.r3e_reader import O, SIZE, R3EReader
from stintradio.recorder import schema_sql


def make_session(path, laps=3, per_lap=50, place=7, race=2, started="1793577527.0"):
    c = sqlite3.connect(path)
    for s in schema_sql():
        c.execute(s)
    c.executemany("INSERT INTO meta VALUES (?,?)", [("track", "Circuit Zandvoort"), ("layout", "Modern"), ("car", "GT3"), ("driver", "Alex Hartley"), ("started", started)])
    names = [n for n, _, _ in FIELDS]
    for lap in range(laps):
        for k in range(per_lap):
            row = {"t": 1000.0 + lap * 100 + k, "completed_laps": lap, "lap_distance": 50.0 * k, "throttle_raw": k / per_lap, "brake_raw": 0.0,
                   "session_type": race, "position": place, "layout_length": 2500.0, "car_speed": 50.0, "gear": 4, "num_cars": 2}
            cols = ["t"] + [n for n in names if n in row and n != "t"]
            c.execute(f"INSERT INTO frames ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[n] for n in cols])
    c.commit()
    c.close()


@pytest.fixture
def reader(tmp_path):
    p = tmp_path / "20261101-185847_circuit-zandvoort_gt3.sqlite"
    make_session(p)
    r = object.__new__(sr.SessionReplayReader)
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    r.db, r.path = c, str(p)
    r.meta = dict(c.execute("SELECT key, value FROM meta"))
    r.cols = [x[1] for x in c.execute("PRAGMA table_info(frames)")]
    rows = c.execute("SELECT rowid, completed_laps, lap_distance FROM frames ORDER BY rowid").fetchall()
    import itertools
    r.ids, r.keys = [x[0] for x in rows], list(itertools.accumulate((x[1] * sr.LAP + x[2] for x in rows), max))
    r.driver, r.row = "alex hartley", 0
    return r


def test_moment_is_found_from_laps_and_distance(reader):
    assert reader.row_for(0 * sr.LAP + 0.0) == 0
    assert reader.row_for(1 * sr.LAP + 500.0) == 50 + 10
    assert reader.row_for(99 * sr.LAP) == 149                      # past the end: the last frame
    assert reader.row_for(-5) == 0


def test_frame_is_rebuilt_with_the_recorded_pedals(reader):
    b = reader.rebuild(25)
    assert len(b) == SIZE
    assert struct.unpack_from("<f", b, O["throttle_raw"])[0] == pytest.approx(0.5)
    assert b[O["track"]:O["track"] + 17].startswith(b"Circuit Zandvoort")


def test_player_found_by_name_not_slot(reader):
    cars = [{"id": 1, "name": "Other Person", "me": True, "laps": 9, "dist": 1.0}, {"id": 2, "name": "Alex Hartley", "me": False, "laps": 1, "dist": 500.0}]
    assert reader._player_car(cars)["id"] == 2


def test_recording_is_matched_to_the_replay(tmp_path):
    make_session(tmp_path / "20261101-185847_a.sqlite", place=24)
    make_session(tmp_path / "20261101-193141_b.sqlite", place=11)
    make_session(tmp_path / "20261031-120000_c.sqlite", place=11)
    found = sr.find_session(tmp_path, "2026-11-01 Circuit Zandvoort - Modern Grand Prix_12", 0.5, 24)
    assert found and found.endswith("_a.sqlite")
    assert sr.find_session(tmp_path, "2026-11-01 Circuit Zolder Grand Prix_2", 0.5, 24) is None
    assert sr.find_session(tmp_path, "2026-11-01 Circuit Zandvoort - Modern Grand Prix_12", 0.5, 3) is None      # no such finish


def test_adapter_registered_and_not_auto():
    from stintradio import sims
    c = sims.get("replay")
    assert c.auto_detect is False and "sample" in c.capabilities
