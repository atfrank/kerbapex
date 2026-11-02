"""Fish Audio / ElevenLabs voices: request shapes, key handling, retries (no network, no real keys)."""
import json
import urllib.error

import pytest

from driverdirector import tts
from driverdirector.config import CommentarySettings

AUDIO = b"ID3" + b"\x00" * 2000
SECRET = "test-secret-key-0123456789"


class FakeResp:
    def __init__(self, data): self.data = data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append({"url": req.full_url, "headers": {k.lower(): v for k, v in req.header_items()},
                      "body": json.loads(req.data)})
        return FakeResp(AUDIO)
    monkeypatch.setattr(tts.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    return calls


def lines():
    return [tts.Line(1, "Alex Hartley leads lap 2.", 0), tts.Line(2, "Contact at the Bus Stop!", 2)]


def test_fish_request_shape_and_excitement(tmp_path, monkeypatch, sent):
    monkeypatch.setenv("FISH_API_KEY", SECRET)
    cfg = CommentarySettings(tts_engine="fish", fish_voice_id="abc123", fish_model="s1")
    files, used = tts.synthesize(lines(), cfg, tmp_path)
    assert used == "fish" and files[1].read_bytes() == AUDIO and files[2].suffix == ".mp3"
    a, b = sent
    assert a["url"] == "https://api.fish.audio/v1/tts"
    assert a["headers"]["authorization"] == f"Bearer {SECRET}" and a["headers"]["model"] == "s1"
    assert a["body"]["reference_id"] == "abc123" and a["body"]["format"] == "mp3"
    assert a["body"]["prosody"]["speed"] == 1.0 and b["body"]["prosody"]["speed"] == 1.1     # excited line is quicker
    assert SECRET not in json.dumps([a["body"], b["body"]])                                  # the key is only a header


def test_fish_uses_the_default_voice_when_none_is_set(tmp_path, monkeypatch, sent):
    monkeypatch.setenv("FISH_API_KEY", SECRET)
    tts.synthesize(lines()[:1], CommentarySettings(tts_engine="fish"), tmp_path)
    assert "reference_id" not in sent[0]["body"]


def test_elevenlabs_request_shape_and_excitement(tmp_path, monkeypatch, sent):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    cfg = CommentarySettings(tts_engine="elevenlabs", elevenlabs_voice_id="voice/42")
    files, used = tts.synthesize(lines(), cfg, tmp_path)
    assert used == "elevenlabs" and set(files) == {1, 2}
    a, b = sent
    assert a["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice%2F42?output_format=mp3_44100_128"
    assert a["headers"]["xi-api-key"] == SECRET and "authorization" not in a["headers"]
    assert a["body"]["model_id"] == "eleven_multilingual_v2"
    assert a["body"]["voice_settings"]["stability"] > b["body"]["voice_settings"]["stability"]
    assert a["body"]["voice_settings"]["style"] < b["body"]["voice_settings"]["style"]
    assert a["body"]["voice_settings"]["speed"] < b["body"]["voice_settings"]["speed"] <= 1.2   # excited lines are quicker
    assert b["body"]["voice_settings"]["style"] >= 0.5 and b["body"]["voice_settings"]["stability"] <= 0.25


def test_a_missing_key_names_the_variable_and_nothing_is_sent(tmp_path, monkeypatch, sent):
    monkeypatch.delenv("FISH_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FISH_API_KEY is not set"):
        tts.synthesize(lines(), CommentarySettings(tts_engine="fish"), tmp_path)
    assert sent == []


def test_cloud_engines_are_never_chosen_automatically(tmp_path, monkeypatch, sent):
    monkeypatch.setenv("FISH_API_KEY", SECRET)
    monkeypatch.setattr(tts, "find_runtime", lambda cfg: None)
    monkeypatch.setattr(tts, "synth_edge", lambda l, c, w, p=None: {})
    _, used = tts.synthesize(lines(), CommentarySettings(tts_engine="auto"), tmp_path)
    assert used == "edge" and sent == []


def test_key_presence_reports_booleans_only(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", SECRET)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert tts.cloud_keys_present() == {"fish": True, "elevenlabs": False}


def test_errors_never_carry_the_key_and_rate_limits_are_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", SECRET)
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    n = {"calls": 0}

    def flaky(req, timeout=0):
        n["calls"] += 1
        if n["calls"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, __import__("io").BytesIO(b'{"message":"rate"}'))
        return FakeResp(AUDIO)
    monkeypatch.setattr(tts.urllib.request, "urlopen", flaky)
    files, _ = tts.synthesize(lines()[:1], CommentarySettings(tts_engine="fish"), tmp_path)
    assert n["calls"] == 3 and files[1].exists()

    def denied(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, __import__("io").BytesIO(b'{"message":"bad key"}'))
    monkeypatch.setattr(tts.urllib.request, "urlopen", denied)
    with pytest.raises(RuntimeError) as e:
        tts.synthesize(lines()[:1], CommentarySettings(tts_engine="fish"), tmp_path)
    assert "401" in str(e.value) and SECRET not in str(e.value)


def test_settings_never_hold_a_key():
    from driverdirector.config import Settings
    dumped = Settings().model_dump_json()
    assert "api_key" not in dumped.lower() and "sk-" not in dumped and "sk_" not in dumped


def test_the_expression_of_each_level_is_configurable_and_speed_is_capped(tmp_path, monkeypatch, sent):
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    cfg = CommentarySettings(tts_engine="elevenlabs",
                            elevenlabs_expression=[{"stability": 0.9, "style": 0.0, "speed": 0.95},
                                                   {"stability": 0.1, "style": 1.0, "speed": 1.5}])
    tts.synthesize([tts.Line(1, "calm", 0), tts.Line(2, "wild", 1), tts.Line(3, "wilder", 2)], cfg, tmp_path)
    s = [c["body"]["voice_settings"] for c in sent]
    assert s[0]["stability"] == 0.9 and s[0]["speed"] == 0.95
    assert s[1]["speed"] == 1.2 and s[1]["style"] == 1.0                    # capped at ElevenLabs' maximum
    assert s[2] == s[1]                                                       # a level beyond the list reuses the last


# ---- Eleven Music ------------------------------------------------------------------------------------------------
def test_eleven_music_request_shape_and_caching(tmp_path, monkeypatch):
    from driverdirector import music
    from driverdirector.config import MusicSettings
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    cfg = MusicSettings(source="elevenlabs", elevenlabs_model="music_v2_5", elevenlabs_seconds=45.0)
    sent = []

    def post(url, headers, body, tries=3, timeout=90.0):
        sent.append((url, headers, body, timeout))
        return AUDIO
    f = music.generate_elevenlabs(cfg, post=post)
    assert f.is_file() and f.read_bytes() == AUDIO and f.suffix == ".mp3"
    url, headers, body, timeout = sent[0]
    assert url == "https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128" and headers == {"xi-api-key": SECRET}
    assert body == {"prompt": cfg.prompt, "music_length_ms": 45000, "model_id": "music_v2_5", "force_instrumental": True}
    assert "seed" not in body                                          # the API refuses a seed together with a prompt
    assert music.generate_elevenlabs(cfg, post=post) == f and len(sent) == 1        # cached: no second charge
    assert music.cached_cloud_loop(cfg) == f
    music.generate_elevenlabs(cfg, seconds=20, post=post)                 # a different length is a different track
    assert len(sent) == 2 and sent[1][2]["music_length_ms"] == 20000
    music.generate_elevenlabs(cfg, seconds=9999, post=post)               # the limit is five minutes
    assert sent[2][2]["music_length_ms"] == 300000


def test_eleven_music_needs_its_key(tmp_path, monkeypatch):
    from driverdirector import music
    from driverdirector.config import MusicSettings
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY is not set"):
        music.generate_elevenlabs(MusicSettings(source="elevenlabs"), post=lambda *a, **k: AUDIO)
