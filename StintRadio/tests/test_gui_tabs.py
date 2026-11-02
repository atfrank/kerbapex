"""The logic behind the control panel's Overlay and Tools tabs (the windows themselves are looked at, not tested here)."""
import json

from stintradio import gui_tabs as T


def test_a_shape_keeps_the_area_and_takes_the_proportions():
    g = {"anchor": "bottom-right", "w": 640, "h": 420, "dx": 30, "dy": 40}
    sq = T.shape_geom(g, "square")
    assert abs(sq["w"] / sq["h"] - 1.0) < 0.03 and abs(sq["w"] * sq["h"] - 640 * 420) / (640 * 420) < 0.03
    assert (sq["anchor"], sq["dx"], sq["dy"]) == ("bottom-right", 30, 40)             # place is untouched
    banner = T.shape_geom(g, "banner")
    assert banner["w"] / banner["h"] > 3.5
    assert T.shape_geom(g, "nonsense") == g                                          # an unknown shape changes nothing
    assert T.shape_geom(None, "wide")["anchor"] == "top-right"                       # no layout yet: the default one


def test_size_and_place_are_kept_in_range():
    g = {"anchor": "top-left", "w": 640, "h": 420, "dx": 5, "dy": 6}
    assert T.sized(g, "99999", "5") == {**g, "w": 1800, "h": 160}
    assert T.sized(g, "700", "300") == {**g, "w": 700, "h": 300}
    assert T.placed(g, "bottom-left", "-3", "12") == {**g, "anchor": "bottom-left", "dx": 0, "dy": 12}
    assert T.placed(g, "somewhere", 1, 2)["anchor"] == "top-left"                    # a bad corner is ignored


def test_the_nearest_named_shape():
    assert T.shape_name({"anchor": "top-left", "w": 760, "h": 360, "dx": 0, "dy": 0}) == "wide"
    assert T.shape_name({"anchor": "top-left", "w": 500, "h": 520, "dx": 0, "dy": 0}) == "square"
    assert T.shape_name({"anchor": "top-left", "w": 380, "h": 640, "dx": 0, "dy": 0}) == "tall"
    assert T.shape_name({"anchor": "top-left", "w": 1000, "h": 250, "dx": 0, "dy": 0}) == "banner"


def test_remembered_choices(tmp_path):
    f = tmp_path / "gui.json"
    assert T.load_prefs(f) == {}
    T.save_prefs({"engine": "edge"}, f)
    assert T.load_prefs(f) == {"engine": "edge"}
    f.write_text("not json", encoding="utf-8")
    assert T.load_prefs(f) == {}                                                     # a damaged file is just forgotten


def test_the_setup_check_names_what_is_missing_and_what_to_do():
    rows = T.setup_checks(False, {"RaceRoom": False}, {"live": {"voice_engine": "elevenlabs", "ptt": True}}, {}, None, None, False)
    bad = {what: fix for ok, what, fix in rows if not ok}
    assert "StintRadio is running" in bad and "Press Start" in bad["StintRadio is running"]
    assert any("ELEVENLABS_API_KEY" in w for w in bad) and any("ffmpeg" in w for w in bad) and any("Claude CLI" in w for w in bad)
    assert any("voice clips" in w for w in bad)
    good = T.setup_checks(True, {"RaceRoom": True}, {"live": {"voice_engine": "windows", "ptt": False}}, {}, "ffmpeg", None, None)
    assert all(ok for ok, _w, _f in good) and not any("ELEVENLABS" in w for _ok, w, _f in good)
    assert not T.setup_checks(True, {}, None, {}, "ffmpeg", None, None)[0][0]        # unreadable settings are the first problem


def test_the_sound_check_log_is_summarised_per_program():
    log = "\n".join([
        "10:00:00.000 monitor start",
        "10:00:01.000 SOUND  python.exe (pid 7) starts, level 0.3",
        "10:00:02.500 quiet  python.exe (pid 7) after 1.50s",
        "10:00:03.000 SOUND  python.exe (pid 7) starts, level 0.3",
        "10:00:03.500 quiet  python.exe (pid 7) after 0.50s",
        "10:00:01.000 SOUND  RRRE64.exe (pid 9) starts, level 0.4",
        "10:00:11.000 quiet  RRRE64.exe (pid 9) after 10.00s",
        "10:00:12.000 monitor end"])
    assert T.summarise_audiomon(log) == ["RRRE64.exe: 1 sounds, 10.0 s audible", "python.exe: 2 sounds, 2.0 s audible"]
    assert T.summarise_audiomon("") == []


def test_an_open_remote_session_is_flagged_but_an_idle_host_is_not():
    idle = '"remoting_host.exe","4784","Services","0","12,000 K"\n"System","4","Services","0","10 K"'
    live = idle + '\n"remoting_desktop.exe","7000","Console","1","30,000 K"\n"AnyDesk.exe","9","Console","1","1 K"'
    assert T.remote_sessions(idle) == [] and T.remote_sessions("") == []                  # the host service alone sends nothing
    assert T.remote_sessions(live) == ["Chrome Remote Desktop", "AnyDesk"]
    ok = T.setup_checks(True, {"RaceRoom": True}, {"live": {}}, {}, "ffmpeg", None, None, [])
    assert all(r[0] for r in ok)
    bad = [r for r in T.setup_checks(True, {"RaceRoom": True}, {"live": {}}, {}, "ffmpeg", None, None, ["Chrome Remote Desktop"]) if not r[0]]
    assert len(bad) == 1 and "Chrome Remote Desktop" in bad[0][1] and "twice" in bad[0][2]


def test_stray_programs_are_told_apart_and_counted_once(monkeypatch):
    from stintradio import housekeeping as H
    cases = [
        (r'"C:\x\StintRadio\.venv\Scripts\python.exe" -m stintradio overlay --game raceroom', "overlay"),
        (r'C:\x\StintRadio\.venv\Scripts\pythonw.exe -m stintradio gui', "panel"),
        (r'C:\x\StintRadio\.venv\Scripts\pythonw.exe -m stintradio --start', "server"),
        (r'C:\x\StintRadio\.venv\Scripts\pythonw.exe -m stintradio', "server"),
        (r'msedge.exe --app=http://127.0.0.1:6420/overlay --user-data-dir=x', "browser"),
        (r'python.exe -m pytest', "other"), (r'python.exe -m stintradiofoo', "other")]
    for cmd, want in cases:
        assert H.kind(cmd) == want, cmd
    procs = [{"pid": 1, "parent": 0, "kind": "overlay"}, {"pid": 2, "parent": 1, "kind": "overlay"},        # one overlay = launcher + child
             {"pid": 3, "parent": 0, "kind": "overlay"}, {"pid": 4, "parent": 3, "kind": "overlay"},
             {"pid": 5, "parent": 0, "kind": "server"}]
    assert [p["pid"] for p in H.roots(procs, "overlay")] == [1, 3] and len(H.roots(procs, "server")) == 1


def test_clearing_overlays_asks_politely_first_and_only_forces_what_is_left(monkeypatch):
    from stintradio import housekeeping as H
    killed, asked = [], []
    left = [[{"pid": 7, "parent": 0, "kind": "overlay", "port": 6420}, {"pid": 8, "parent": 0, "kind": "overlay", "port": 6491}], []]
    monkeypatch.setattr(H, "processes", lambda: left[0] if left else [])
    monkeypatch.setattr(H, "kill", lambda pids, tree=True: killed.extend(pids))
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(H.time, "monotonic", lambda: next(ticks) * 0.5)
    n = H.clear_overlays(lambda: asked.append("quit"), lambda: asked.append("resume"), wait=2.0)
    assert asked == ["quit", "resume"] and killed == [7] and n == 1                    # forced, and the other StintRadio's overlay (port 6491) not touched
    killed.clear()
    left.clear()
    assert H.clear_overlays(lambda: None, None, wait=2.0) == 0 and killed == []        # it went away when asked: nothing forced


def test_stopping_a_stintradio_by_force_ends_only_the_one_on_its_own_port(monkeypatch):
    from stintradio import gui, housekeeping as H
    ended = []
    monkeypatch.setattr(H, "listener_pid", lambda port: {6491: 4242}.get(port))
    monkeypatch.setattr(H, "kill", lambda pids, tree=True: ended.extend(pids))
    gui.kill_app_process(6491)
    assert ended == [4242]
    gui.kill_app_process(6420)                                      # nothing listens there in this test: nothing is ended
    assert ended == [4242]


def test_overlays_belong_to_the_stintradio_whose_address_they_use():
    from stintradio import housekeeping as H
    assert H.port_of(r'python.exe -m stintradio overlay --game raceroom') == 6420
    assert H.port_of(r'python.exe -m stintradio overlay --url http://127.0.0.1:6491/overlay') == 6491
    assert H.port_of(r'msedge.exe --app=http://127.0.0.1:6499/overlay --user-data-dir=x') == 6499
    procs = [{"pid": 1, "parent": 0, "kind": "overlay", "port": 6420}, {"pid": 2, "parent": 0, "kind": "overlay", "port": 6491},
             {"pid": 3, "parent": 0, "kind": "server", "port": 6420}]
    assert [p["pid"] for p in H.roots(procs, "overlay", 6420)] == [1] and [p["pid"] for p in H.roots(procs, "overlay", 6491)] == [2]
    assert len(H.roots(procs, "overlay")) == 2 and len(H.roots(procs, "server", 6491)) == 1      # servers are told apart by what they listen on


def test_what_the_panel_starts_uses_the_windowless_python(tmp_path):
    from stintradio import gui
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    exe = str(tmp_path / "python.exe")
    assert gui.windowless(exe) == str(tmp_path / "pythonw.exe")
    assert gui.app_command(False, exe) == [str(tmp_path / "pythonw.exe"), "-m", "stintradio"]
    assert gui.overlay_command("lmu", False, exe)[0] == str(tmp_path / "pythonw.exe")
    assert gui.windowless("py.exe") == "py.exe" and gui.windowless(str(tmp_path / "other.exe")) == str(tmp_path / "other.exe")
    solo = tmp_path / "x"
    solo.mkdir()
    (solo / "python.exe").write_bytes(b"")
    assert gui.windowless(str(solo / "python.exe")) == str(solo / "python.exe")          # no pythonw beside it: unchanged


def test_a_packaged_panel_starts_the_engine_program_beside_it_and_knows_it_by_name():
    from stintradio import gui, housekeeping as H
    exe = r"C:\Program Files\KerbApex\KerbApex.exe"
    assert gui.app_command(True, exe) == [r"C:\Program Files\KerbApex\StintRadio.exe"]
    assert gui.overlay_command("lmu", True, exe, "native", 6491) == [r"C:\Program Files\KerbApex\StintRadio.exe", "overlay", "--game", "lmu",
                                                                  "--url", "http://127.0.0.1:6491/overlay", "--engine", "native"]
    assert H.kind(r'"C:\Program Files\KerbApex\StintRadio.exe"') == "server"
    assert H.kind(r'"C:\Program Files\KerbApex\StintRadio.exe" overlay --game raceroom --engine native') == "overlay"
    assert H.kind(r'"C:\Program Files\KerbApex\KerbApex.exe"') == "panel"
