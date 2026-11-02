"""The Claude CLI that writes the commentary: finding it from the user's terminal, and reporting a signed-out login."""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from driverdirector import commentary as cm

DD = Path(__file__).resolve().parents[1] / "claude-plugin" / "scripts" / "dd.py"


@pytest.fixture
def dd():
    spec = importlib.util.spec_from_file_location("dd_helper", DD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    """A packaged desktop app: the real CLI is under Local/Packages/Claude_x/LocalCache/Roaming; the short Roaming path
    is what only the app itself sees (and is absent here)."""
    appdata, local = tmp_path / "Roaming", tmp_path / "Local"
    real = local / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / "claude-code" / "2.1.295" / "d2e2"
    real.mkdir(parents=True)
    (real / "claude.exe").write_bytes(b"x")
    old = local / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude" / "claude-code" / "2.1.293" / "83cb"
    old.mkdir(parents=True)
    (old / "claude.exe").write_bytes(b"x")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return appdata, local, real / "claude.exe"


def test_the_terminal_path_is_the_real_package_path_with_forward_slashes(packaged):
    appdata, local, real = packaged
    short = appdata / "Claude" / "claude-code" / "2.1.295" / "d2e2" / "claude.exe"       # what only the app sees
    got = cm.terminal_path(str(short))
    assert got == str(real).replace("\\", "/") and "Packages/Claude_abc123/LocalCache/Roaming" in got
    assert "\\" not in got
    other = "D:/tools/claude/claude.exe"                                                   # not a desktop-app path: unchanged
    assert cm.terminal_path(other) == other


def test_the_signed_out_error_names_the_path_that_works_in_a_normal_terminal(packaged):
    appdata, local, real = packaged
    short = appdata / "Claude" / "claude-code" / "2.1.295" / "d2e2" / "claude.exe"
    client = cm.ClaudeCliClient(str(short), runner=lambda cmd, text: SimpleNamespace(
        returncode=1, stdout="Failed to authenticate: OAuth session expired", stderr=""))
    with pytest.raises(RuntimeError) as e:
        client.messages.create(model="", messages=[{"role": "user", "content": "hi"}])
    msg = str(e.value)
    assert "Packages/Claude_abc123" in msg and "auth login --claudeai" in msg and "PowerShell" in msg


def run(dd, monkeypatch, capsys, logged_in):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=json.dumps({"loggedIn": logged_in}), stderr="", returncode=0))
    dd.cmd_claude_login([])
    return json.loads(capsys.readouterr().out)


def test_signed_out_prints_the_commands_for_both_terminals(dd, packaged, monkeypatch, capsys):
    appdata, local, real = packaged
    r = run(dd, monkeypatch, capsys, False)
    assert r["logged_in"] is False and r["cli"] == str(real)                         # the newest version, the real path
    gb, ps = r["sign_in"]["git_bash"], r["sign_in"]["powershell"]
    assert gb == f'"{str(real).replace(chr(92), "/")}" auth login --claudeai' and "\\" not in gb
    assert ps == f'& "{real}" auth login --claudeai'
    assert "own terminal" in r["sign_in"]["note"]


def test_signed_in_needs_no_command(dd, packaged, monkeypatch, capsys):
    r = run(dd, monkeypatch, capsys, True)
    assert r["logged_in"] is True and r["sign_in"] is None


def test_the_helper_only_ever_reads_the_status(dd, packaged, monkeypatch, capsys):
    seen = []

    def fake(cmd, **k):
        seen.append(cmd[1:])
        return SimpleNamespace(stdout='{"loggedIn": false}', stderr="", returncode=0)
    monkeypatch.setattr(subprocess, "run", fake)
    dd.cmd_claude_login([])
    assert seen == [["auth", "status"]]                                              # it never starts a login itself


def test_no_cli_is_reported_plainly(dd, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "a"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "b"))
    with pytest.raises(SystemExit):
        dd.cmd_claude_login([])
