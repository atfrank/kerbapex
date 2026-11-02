"""The Studio tab's pure parts: the replay list, the command line, the finished video's path."""
from stintradio import studio as st

LIST = """2026-11-01 Circuit Zandvoort - Modern Grand Prix_12                    Race         14.7 min  P24  Mercedes-AMG GT3 2020 DTM
2026-11-01 Hungaroring Grand Prix_3                                    Qualifying    4.7 min  P1  BMW 320i E36 Super Touring
not a row
"""


def test_replay_list_is_parsed():
    rows = st.parse_replays(LIST)
    assert [r["session"] for r in rows] == ["Race", "Qualifying"]
    assert rows[0]["name"] == "2026-11-01 Circuit Zandvoort - Modern Grand Prix_12"
    assert rows[0]["minutes"] == 14.7 and rows[0]["place"] == "P24" and rows[0]["car"] == "Mercedes-AMG GT3 2020 DTM"


def test_command_and_video_line():
    c = st.command("make", "X", "--restart", python="py")
    assert c == ["py", "-m", "driverdirector", "raceroom", "make", "X", "--restart"]
    assert st.video_from("video: C:/v/a.mp4") == "C:/v/a.mp4" and st.video_from("recorded: x") is None


def test_estimate_is_longer_for_two_passes():
    assert st.estimate_minutes(16.5) > st.estimate_minutes(16.5, two_passes=False) > 16


def test_last_races_skips_qualifying_and_made_ones():
    rows = st.parse_replays(LIST + "2026-11-01 Circuit Zolder Grand Prix_7   Race   16.6 min  P11  Tatuus F4\n")
    assert [r["name"] for r in st.last_races(5, rows)] == ["2026-11-01 Circuit Zandvoort - Modern Grand Prix_12", "2026-11-01 Circuit Zolder Grand Prix_7"]
    assert len(st.last_races(5, rows, made={rows[0]["name"]})) == 1
    assert st.place_number("P24") == 24 and st.place_number("-") == 0


def test_a_studio_run_is_never_taken_for_a_stray_server():
    from stintradio import housekeeping as H
    assert H.kind("python.exe -m stintradio studio make X") == "other"
    assert H.kind("pythonw.exe -m stintradio --video s.sqlite --port 6421") == "server"
