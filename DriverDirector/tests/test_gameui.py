import pytest

from driverdirector import gameui
from driverdirector.config import DirectSettings, Settings
from driverdirector.director import Director
from driverdirector.lmu_api import DemoLMU
from tests.test_record import FakeClock, FakeObs, TracingGame, make_director
from driverdirector.lmu_api import LMUApiError


class FakeLmu:
    """Stands in for the game's /rest/hud: a middle click flips every component, like the real UI does."""
    def __init__(self, shown=True, works=True):
        self.shown, self.works = shown, works
    def _call(self, method, path, body=None):
        assert (method, path) == ("GET", "hud")
        return {k: self.shown for k in ("chat", "mfd", "speedo", "timing", "trackMap")}
    def click(self):
        if self.works:
            self.shown = not self.shown


def test_ui_hidden_reads_the_hud_state():
    assert gameui.ui_hidden(FakeLmu(shown=True)) is False
    assert gameui.ui_hidden(FakeLmu(shown=False)) is True
    mixed = FakeLmu(); mixed._call = lambda m, p, b=None: {"chat": True, "speedo": False}
    assert gameui.ui_hidden(mixed) is None                         # unknown: never guess a click from it

    broken = FakeLmu()
    def boom(*a, **k): raise LMUApiError("down")
    broken._call = boom
    assert gameui.ui_hidden(broken) is None


def test_set_game_ui_clicks_only_when_needed_and_verifies():
    lmu = FakeLmu(shown=True)
    clicks = []
    def click(): clicks.append(1); lmu.click()
    assert gameui.set_game_ui(lmu, True, click=click, sleep=lambda s: None) is True and len(clicks) == 1
    assert gameui.set_game_ui(lmu, True, click=click, sleep=lambda s: None) is True and len(clicks) == 1   # already hidden
    assert gameui.set_game_ui(lmu, False, click=click, sleep=lambda s: None) is True and len(clicks) == 2
    # a click that has no effect is retried once, then reported as failed (never loops forever)
    stuck = FakeLmu(shown=True, works=False); n = []
    assert gameui.set_game_ui(stuck, True, click=lambda: n.append(1) or stuck.click(), sleep=lambda s: None) is False
    assert len(n) == 2


def _run(tmp_path, monkeypatch, mode, inlays=1):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=inlays,
                                hide_game_ui=mode)
    d.analyze(); d.make_plan()
    d.set_game_ui = lambda hidden: d.game.log.append(("ui", hidden)) or True
    d.record()
    return d.game.log


def test_hide_for_clips_only(tmp_path, monkeypatch):
    log = _run(tmp_path, monkeypatch, "clips")
    ui = [i for i, x in enumerate(log) if x[0] == "ui"]
    seeks = [i for i, x in enumerate(log) if x[0] == "seek"]
    assert log[ui[0]] == ("ui", True) and ui[0] < seeks[0]                 # hidden before the first clip is recorded
    show = next(i for i in ui if log[i] == ("ui", False))
    assert seeks[0] < show < seeks[-1]                                     # shown again before the main pass seeks
    assert log[seeks[-1]] == ("seek", 100.0)
    assert log[-1] == ("ui", False)                                        # and left as we found it


def test_hide_for_everything(tmp_path, monkeypatch):
    log = _run(tmp_path, monkeypatch, "all")
    ui = [x for x in log if x[0] == "ui"]
    assert ui[0] == ("ui", True) and ui[-1] == ("ui", False) and ui.count(("ui", False)) == 1
    assert log.index(("ui", True)) < next(i for i, x in enumerate(log) if x[0] == "seek")


def test_never_touch_the_game_ui(tmp_path, monkeypatch):
    assert not any(x[0] == "ui" for x in _run(tmp_path, monkeypatch, "off"))


def test_game_ui_is_restored_even_if_recording_fails(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1, hide_game_ui="all")
    d.analyze(); d.make_plan()
    calls = []
    d.set_game_ui = lambda hidden: calls.append(hidden) or True
    def boom(*a, **k): raise RuntimeError("OBS died")
    d.obs.stop_record = boom
    with pytest.raises(RuntimeError):
        d.record()
    assert calls[0] is True and calls[-1] is False


def test_failure_to_hide_is_a_warning_when_not_required(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1, hide_game_ui="clips",
                                require_hidden_ui=False)
    d.analyze(); d.make_plan()
    d.set_game_ui = lambda hidden: False
    seen = []
    d.on_status = lambda st: seen.append(st) if st["stage"] == "warning" else None
    d.record()                                                              # still records
    assert seen and "could not hide" in seen[0]["message"]


def test_failure_to_hide_stops_before_recording_by_default(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1, hide_game_ui="all")
    d.analyze(); d.make_plan()
    d.set_game_ui = lambda hidden: False if hidden else True
    d.ui_failure = lambda: "the click was sent but the game's UI did not change"
    with pytest.raises(RuntimeError, match="Nothing was recorded.*|did not change"):
        d.record()
    assert not any(x[0] == "seek" for x in d.game.log)                     # stopped before the first clip


def test_all_mode_hides_again_before_the_main_pass_if_the_clips_lost_it(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1, hide_game_ui="all")
    d.analyze(); d.make_plan()
    calls = []
    d.set_game_ui = lambda hidden: calls.append(hidden) or True
    d.record()
    assert calls[:2] == [True, True]                                       # before the clips, and asked again for the main pass


class Screen:
    """A picture of the game the controller can look at: the UI makes the two regions very different."""
    def __init__(self, works=True):
        self.shown, self.works = True, works
    def probe(self):
        return [200 if not self.shown else 20] * 100
    def click(self):
        if self.works:
            self.shown = not self.shown


def test_ui_controller_verifies_against_the_picture_and_tracks_state():
    sc = Screen(); n = []
    ui = gameui.UiController(click=lambda: n.append(1) or sc.click(), probe=sc.probe, sleep=lambda s: None)
    assert ui.set(True) is True and ui.hidden and len(n) == 1 and not sc.shown
    assert ui.set(True) is True and len(n) == 1                            # already hidden: no click
    assert ui.set(False) is True and sc.shown and len(n) == 2
    ui.reset(); sc.shown = False                                           # a reloaded replay shows its UI again
    assert ui.hidden is False


def test_ui_controller_reports_a_click_that_changed_nothing():
    sc = Screen(works=False)
    ui = gameui.UiController(click=sc.click, probe=sc.probe, sleep=lambda s: None)
    assert ui.set(True) is False and not ui.hidden and "did not change" in ui.last_error


def test_ui_controller_reports_why_the_click_failed():
    def boom(): raise RuntimeError("the game window is minimized")
    ui = gameui.UiController(click=boom, probe=Screen().probe, sleep=lambda s: None)
    assert ui.set(True) is False and "minimized" in ui.last_error


def test_ui_controller_without_a_picture_trusts_the_click_but_says_so():
    n = []
    ui = gameui.UiController(click=lambda: n.append(1), probe=None, sleep=lambda s: None)
    assert ui.set(True) is True and ui.verified is False and len(n) == 1


def test_preflight_lists_what_is_wrong(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, hide_game_ui="all")
    d.analyze(); d.make_plan()
    d.set_game_ui = lambda hidden: hidden is False
    d.ui_failure = lambda: "the game window is minimized"
    r = d.preflight()
    assert not r["ok"]
    bad = [c for c in r["checks"] if not c["ok"]]
    assert any(c["name"] == "hide game UI" and "minimized" in c["detail"] for c in bad)


def test_a_failed_recording_does_not_leave_obs_recording(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1, hide_game_ui="off")
    d.analyze(); d.make_plan()
    stops = []
    d.obs.status = lambda: {"connected": True, "recording": True}      # still recording when the job dies
    real_stop = d.obs.stop_record
    def boom_once():
        if not stops:
            stops.append("boom"); raise RuntimeError("LMU not reachable")
        stops.append("stopped"); return real_stop()
    d.obs.stop_record = boom_once
    with pytest.raises(RuntimeError):
        d.record()
    assert stops[-1] == "stopped"



def test_hide_is_not_blocked_when_the_picture_check_misreads_a_hidden_frame():
    """On a pale track the picture check says 'shown' even when hidden; the counted toggle wins."""
    state = {"ui": True}
    scene = [200] * 100
    def probe():
        return ([40] * 30 + scene[30:]) if state["ui"] else list(scene)
    clicks = []
    def click():
        clicks.append(1); state["ui"] = not state["ui"]
    ui = gameui.UiController(click=click, probe=probe, sleep=lambda s: None, shown=lambda: True)
    assert ui.set(True) is True and state["ui"] is False and len(clicks) == 1
    assert ui.set(True) is True and state["ui"] is False and len(clicks) == 3   # a test click and its undo


def test_picture_saying_hidden_corrects_the_count():
    clicks = []
    ui = gameui.UiController(click=lambda: clicks.append(1), probe=lambda: [0] * 10, sleep=lambda s: None,
                             shown=lambda: False)
    assert ui.set(True) is True and clicks == []
