from fastapi.testclient import TestClient

from driverdirector.config import Settings
from driverdirector.director import Director
from driverdirector.lmu_api import DemoLMU, describe_replay, fix_mojibake
from driverdirector.server import create_app
from driverdirector.sources.simulator import Simulator


def test_describe_and_mojibake():
    raw = {"id": 0, "metadata": {"eventTitle": "LMGT3 Sprint Cup", "session": "RACE", "splitNo": 3,
           "eventType": "daily"}, "replayName": "AutÃ³dromo JosÃ© Carlos Pace R1 2",
           "size": 258846242, "timestamp": 1791425654}
    d = describe_replay(raw)
    assert d["track"] == "Autódromo José Carlos Pace"
    assert d["session"] == "Race" and d["size_mb"] == 246.9
    assert fix_mojibake("plain ascii") == "plain ascii"
    assert fix_mojibake("Autódromo") == "Autódromo"          # already-correct text is left alone


def test_replay_endpoints():
    app = create_app(Director(Settings(), Simulator(), lmu=DemoLMU()))
    c = TestClient(app)
    rows = c.get("/api/replays").json()
    assert rows[0]["timestamp"] >= rows[-1]["timestamp"]
    assert c.post("/api/replays/2/load").json() == {"loading": 2}
    assert c.get("/api/game").json()["replay_active"] is True


def test_obs_launcher(monkeypatch, tmp_path):
    from driverdirector import launcher
    exe = tmp_path / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True); exe.write_text("")
    assert launcher.find_obs(str(exe)) == str(exe)                     # configured path wins
    calls = []
    sentinel = tmp_path / "appdata" / "obs-studio" / ".sentinel"
    sentinel.mkdir(parents=True)
    (sentinel / "run_left-by-a-forced-close").write_text("")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(launcher, "obs_running", lambda: False)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    assert launcher.launch_obs(str(exe), "DriverDirector") is True
    cmd, kw = calls[0]
    assert cmd == [str(exe), "--minimize-to-tray", "--scene", "DriverDirector"]
    assert kw["cwd"] == str(exe.parent)                                # OBS must start in its own bin folder
    assert not list(sentinel.iterdir())                                # no "Crash Detected" dialog on start
    monkeypatch.setattr(launcher, "obs_running", lambda: True)         # never start a second copy
    assert launcher.launch_obs(str(exe)) is False and len(calls) == 1
    monkeypatch.setattr(launcher, "obs_running", lambda: False)
    monkeypatch.setattr(launcher, "find_obs", lambda p="": None)
    import pytest
    with pytest.raises(OSError, match="not found"):
        launcher.launch_obs("")


def test_obs_launch_endpoint(monkeypatch):
    from driverdirector import launcher
    started = []
    monkeypatch.setattr(launcher, "obs_running", lambda: False)
    monkeypatch.setattr(launcher, "launch_obs", lambda path="", scene="": started.append((path, scene)) or True)
    c = TestClient(create_app(Director(Settings(), Simulator(), lmu=DemoLMU())))
    assert c.get("/api/game").json()["obs_running"] is False
    assert c.post("/api/obs/launch").json() == {"launched": True, "already_running": False}
    assert started == [("", "DriverDirector")]


def test_exit_to_main_menu_follows_the_games_own_exit_sequence():
    from driverdirector.lmu_api import LMUApiError, LMUClient
    calls = []

    class Fake(LMUClient):
        def __init__(self, state):
            super().__init__(); self.state = state
        def _call(self, method, path, body=None, root=False):
            calls.append((method, path, body, root))
            if path == "sessions/GetGameState":
                if self.state is None:
                    raise LMUApiError("Game is not in an active session")
                return {k: v for k, v in self.state.items() if k != "replay"}     # (the real call says isReplayActive false even in a replay)
            if path == "replay/isActive":
                return bool(self.state and self.state.get("replay", True))
            return None

    import driverdirector.lmu_api as m
    m.time.sleep = lambda s: None
    # a plain replay: just NAV_TO_MAIN_MENU, URL-only (no body), on the /navigation service
    assert Fake({"MultiStintState": "DISABLED"}).exit_to_main_menu() is True
    assert calls[-1] == ("POST", "navigation/action/NAV_TO_MAIN_MENU", None, True)
    assert [c[1] for c in calls] == ["sessions/GetGameState", "replay/isActive", "navigation/action/NAV_TO_MAIN_MENU"]
    # a LIVE session (not a replay) is never exited: the user is driving
    calls.clear()
    assert Fake({"replay": False, "MultiStintState": "MONITOR_MENU"}).exit_to_main_menu() is False
    assert "navigation/action/NAV_TO_MAIN_MENU" not in [c[1] for c in calls]       # nothing was sent
    calls.clear()
    # a replay that is open is left even though the state call says isReplayActive false
    assert Fake({"isReplayActive": False, "MultiStintState": "DISABLED"}).exit_to_main_menu() is True
    assert Fake({"replay": False, "MultiStintState": "MONITOR_MENU"}).live_session() is True
    assert Fake({"isReplayActive": False, "MultiStintState": "DISABLED"}).live_session() is False
    assert Fake(None).live_session() is False
    calls.clear()
    # a multi-stint session goes "back to event" first, like the game's UI
    calls.clear()
    assert Fake({"MultiStintState": "DRIVING"}).exit_to_main_menu() is True
    assert [c[1] for c in calls] == ["sessions/GetGameState", "replay/isActive", "navigation/action/NAV_BACK_TO_EVENT",
                                     "navigation/action/NAV_TO_MAIN_MENU"]
    assert all(c[2] is None for c in calls)                      # never a request body
    # at the menus: nothing is sent
    calls.clear()
    assert Fake(None).exit_to_main_menu() is False and [c[1] for c in calls] == ["sessions/GetGameState"]


def test_exit_replay_endpoint_and_automatic_exit_after_recording(tmp_path, monkeypatch):
    from tests.test_record import make_director
    c = TestClient(create_app(Director(Settings(), Simulator(), lmu=DemoLMU())))
    assert c.post("/api/game/exit_replay").json() == {"exited": True}
    # after recording with memory tight, the director leaves the replay and then carries on to produce
    d, clock = make_director(tmp_path, monkeypatch)
    d.analyze(); d.make_plan()
    state = {"low": True, "exited": 0}
    d.low_memory = lambda: state["low"]
    d.lmu.exit_to_main_menu = lambda: state.__setitem__("exited", state["exited"] + 1) or state.__setitem__("low", False) or True
    d.commentary_wav = None
    d.s.commentary.enabled = False
    d.produce = lambda: "produced.mp4"
    assert d.run_all() == "produced.mp4" and state["exited"] == 1
    # if memory is still tight after leaving the replay, it waits for the user instead of risking a crash
    state.update(low=True, exited=0)
    d.lmu.exit_to_main_menu = lambda: state.__setitem__("exited", state["exited"] + 1) or True
    out = d.run_all()
    assert out != "produced.mp4" and d.status["stage"] == "recorded"
