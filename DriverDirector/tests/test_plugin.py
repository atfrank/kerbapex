import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from driverdirector.server import create_app
from tests.test_server_commentary import demo_director

PLUGIN = Path(__file__).resolve().parents[1] / "claude-plugin"


def load_dd():
    spec = importlib.util.spec_from_file_location("dd", PLUGIN / "scripts" / "dd.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(demo_director()), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    dd = load_dd()
    monkeypatch.setattr(dd, "BASE", f"http://127.0.0.1:{port}")
    yield dd
    srv.should_exit = True
    t.join(5)


def test_manifest_and_skills_are_well_formed():
    m = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert m["name"] == "driverdirector" and m["description"]
    for name in ("setup", "direct", "manage", "publish"):
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n") and f"name: {name}" in text and "description:" in text
    direct = (PLUGIN / "skills" / "direct" / "SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in direct          # starting a long recording is the user's call
    publish = (PLUGIN / "skills" / "publish" / "SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in publish and "clear yes" in publish   # publishing is never automatic


def test_deep_merge_only_touches_given_keys():
    dd = load_dd()
    a = {"direct": {"x": 1, "y": 2}, "music": {"source": "ai"}}
    assert dd.deep_merge(a, {"direct": {"y": 5}}) == {"direct": {"x": 1, "y": 5}, "music": {"source": "ai"}}


def test_helper_reports_app_not_running(capsys):
    dd = load_dd()
    dd.BASE = "http://127.0.0.1:9"
    with pytest.raises(SystemExit) as e:
        dd.main(["status"])
    assert e.value.code == 1 and "not running" in capsys.readouterr().err


def test_helper_against_a_running_app(server, capsys):
    dd = server
    dd.main(["status"])
    assert json.loads(capsys.readouterr().out)["stage"] in ("idle", "done")
    dd.main(["settings", '{"music":{"level_db":-28},"direct":{"telemetry":false}}'])
    s = json.loads(capsys.readouterr().out)
    assert s["music"]["level_db"] == -28 and s["direct"]["telemetry"] is False and s["music"]["source"] == "ai"
    dd.main(["settings"])
    assert json.loads(capsys.readouterr().out)["music"]["level_db"] == -28           # it was saved, not just echoed
    dd.main(["health"])
    h = json.loads(capsys.readouterr().out)
    assert h["app_running"] and "claude_cli" in h and h["music"]["source"] == "ai"
    with pytest.raises(SystemExit):
        dd.main(["settings", "{not json"])
    dd.main(["watch", "1"])                                                       # idle: returns at once
    assert "idle" in capsys.readouterr().out
