"""Commentary never assumes a driver's gender: such lines are rejected so they get rewritten."""
import pytest

from driverdirector import commentary as cm
from driverdirector.config import CommentarySettings


def check(text, facts="INCIDENT: R DeathShot hits the barrier hard in sector three."):
    sc = cm.Scene(0, 10.0, 8.0, "incident", facts)
    return cm.validate({0: text}, [sc], CommentarySettings())


@pytest.mark.parametrize("text", [
    "The man who was running third has a huge problem!",
    "He is out of the race.",
    "She hits the wall hard, and her race is over.",
    "What a moment for the lady in third!",
    "That guy will not be happy.",
    "A bad day for him.",
])
def test_lines_that_assume_a_gender_are_rejected(text):
    errs = check(text)
    assert errs and "assumes a driver's gender" in errs[0]


@pytest.mark.parametrize("text", [
    "R DeathShot hits the barrier hard! The driver who was running third has a huge problem.",
    "That is a big one in sector three, and their race is in trouble!",
    "Oh, that is a hard hit! Not our driver, thankfully.",
    "Manchester is nowhere near this circuit, and neither is the Miami round.",       # \b: no false hits inside words
])
def test_gender_free_lines_pass(text):
    assert check(text) == []


def test_a_name_that_really_contains_such_a_word_is_allowed():
    facts = "INCIDENT: Guy Martin hits the barrier."
    assert check("Guy Martin is in the wall!", facts) == []
    assert check("Guy Martin is in the wall, and the man is out!", facts)             # "man" is still not in the facts


def test_the_prompt_names_the_words_to_avoid():
    p = " ".join(cm.build_prompt([cm.Scene(0, 10.0, 5.0, "x", "f")], CommentarySettings()).split())
    assert "no \"the man\"" in p or "no he, she" in p and "\"the man\"" in p


# ---- the Claude CLI that writes the commentary can be signed out ------------------------------------------------------
from types import SimpleNamespace


@pytest.mark.parametrize("out", [
    "Failed to authenticate: OAuth session expired and could not be refreshed",
    "Not logged in. Please run /login",
])
def test_a_signed_out_cli_is_reported_as_such_with_what_to_do(out):
    client = cm.ClaudeCliClient("claude.exe", runner=lambda cmd, text: SimpleNamespace(returncode=1, stdout=out, stderr=""))
    with pytest.raises(RuntimeError, match=r"signed out.*auth login --claudeai"):
        client.messages.create(model="", messages=[{"role": "user", "content": "hi"}])


def test_an_ordinary_cli_failure_is_not_blamed_on_the_login():
    client = cm.ClaudeCliClient("claude.exe", runner=lambda cmd, text: SimpleNamespace(returncode=2, stdout="", stderr="boom"))
    with pytest.raises(RuntimeError, match="claude CLI failed"):
        client.messages.create(model="", messages=[{"role": "user", "content": "hi"}])


def test_the_login_status_is_read_not_changed(monkeypatch):
    import subprocess

    def fake(cmd, **k):
        assert cmd[1:] == ["auth", "status"]                           # only ever asks for the status
        return SimpleNamespace(stdout='{"loggedIn": false, "authMethod": "none"}')
    monkeypatch.setattr(subprocess, "run", fake)
    assert cm.claude_logged_in("claude.exe") is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="not json"))
    assert cm.claude_logged_in("claude.exe") is None
