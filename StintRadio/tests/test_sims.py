"""The sim adapter contract: registry, drop-in discovery, validators, the conformance check and the demo sim."""
from types import SimpleNamespace

import pytest

from stintradio import sims
from stintradio.sims import C, SimAdapter, registry
from stintradio.sims.check import check, report
from stintradio.sims.demo import DemoAdapter


def test_builtins_registered():
    ids = set(sims.discover(force=True))
    assert {"raceroom", "lmu", "demo"} <= ids


def test_demo_passes_the_check():
    res = check(DemoAdapter(speed_up=20), seconds=0.5, hz=40)
    assert res["errors"] == [], res
    assert "passed" in report(res, "demo")


def test_demo_is_not_auto_detected():
    assert registry.get("demo").auto_detect is False
    assert sims.open_sim(SimpleNamespace(game="demo")).id == "demo"


def test_unknown_sim_is_a_clear_error():
    with pytest.raises(OSError, match="no such sim adapter"):
        sims.open_sim(SimpleNamespace(game="nope"))


def test_capabilities_missing_are_listed():
    off = C.lacking(DemoAdapter.capabilities)
    assert C.RACE_SNAPSHOT in off and C.SAMPLE not in off


def test_validator_catches_wrong_units():
    s = DemoAdapter().sample()
    assert C.check_sample(s) == []
    s.throttle, s.speed = 0.4, 9999          # a 0..1 fraction would also pass; speed out of range does not
    assert any("speed" in p for p in C.check_sample(s))
    s.speed, s.dist = 100, s.track_len * 3
    assert any("dist" in p for p in C.check_sample(s))
    assert C.check_sample("nope")[0].startswith("sample() must return")


def test_session_info_and_snapshot_validators():
    assert C.check_session_info(DemoAdapter().session_info()) == []
    assert C.check_session_info({"type": 1})
    assert C.check_snapshot({"me": {}}) and C.check_snapshot(None) == []
    assert C.check_gaps((1, None, 2.0)) == [] and C.check_gaps([1, 2])
    assert C.check_setup({"abs": "x"})


def test_dropin_adapter_is_found_and_bad_one_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = registry.adapters_dir()
    d.mkdir(parents=True)
    (d / "good.py").write_text("from stintradio.sims import SimAdapter\n"
                               "class G(SimAdapter):\n    id='good'\n    name='Good'\n", encoding="utf-8")
    (d / "bad.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    try:
        found = sims.discover(force=True)
        assert "good" in found and "demo" in found
        assert any("bad.py" in e and "boom" in e for e in registry.errors)
    finally:
        monkeypatch.undo()
        sims.discover(force=True)


def test_template_is_a_valid_adapter_file():
    from pathlib import Path
    cls = registry.load_file(Path(registry.__file__).with_name("template.py"))[0]
    assert cls.id == "mysim" and issubclass(cls, SimAdapter)


def test_builtin_wrappers_pass_through(monkeypatch):
    from stintradio.sims.builtin import RaceRoomAdapter
    inner = SimpleNamespace(TURN_SIGN=-1, last_frame=b"x", sample=lambda vid=None: None, player_name=lambda: "A",
                            session_info=lambda: None, session_best=lambda: None, class_leader=lambda: None,
                            race_snapshot=lambda: None, race_gaps=lambda: None, setup=lambda: {})
    a = RaceRoomAdapter(inner)
    assert a.TURN_SIGN == -1 and a.last_frame == b"x" and a.player_name() == "A" and C.RECORDING in a.capabilities
    with pytest.raises(AttributeError):
        a.nothing_like_this
