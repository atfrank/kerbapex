import re

import pytest

from driverdirector import tts
from driverdirector.config import CommentarySettings
from driverdirector.speech import int_to_words, speakable


@pytest.mark.parametrize("text,expected", [
    ("Alex Hartley is P24 overall and P24 in class.", "Alex Hartley is position twenty-four overall and position twenty-four in class."),
    ("Sam Reyes is down in P25, Kim Ortega is P13.", "Sam Reyes is down in position twenty-five, Kim Ortega is position thirteen."),
    ("our driver, number 2, is running P17 in GT3.", "our driver, number two, is running position seventeen in GT three."),
    ("Alex Hartley (#2) takes the flag", "Alex Hartley (number two) takes the flag"),
    ("a 1:41.366 lap", "a one minute forty-one point three six six lap"),
    ("a 2:05 stop", "a two minutes five stop"),
    ("closest gap 0.1s, about 12s", "closest gap zero point one seconds, about twelve seconds"),
    ("21st, 3rd, 42nd, 12th", "twenty-first, third, forty-second, twelfth"),
    ("LMP2 beat GT3", "LMP two beat GT three"),
    ("P101 and lap 100", "position one hundred and one and lap one hundred"),
])
def test_numbers_become_words(text, expected):
    assert speakable(text) == expected


def test_no_digits_survive_and_plain_text_is_untouched():
    for t in ("Trouble up the road! P7, P24, GT3, #18, 1:41.366, 0.25s, 3rd, 1999", "No numbers here at all."):
        assert not re.search(r"\d", speakable(t))
    assert speakable("No numbers here at all.") == "No numbers here at all."
    assert [int_to_words(n) for n in (0, 13, 20, 21, 99, 100, 342, 1000)] == [
        "zero", "thirteen", "twenty", "twenty-one", "ninety-nine", "one hundred", "three hundred and forty-two", "one thousand"]


def test_the_voice_engines_receive_spelled_out_numbers(tmp_path, monkeypatch):
    got = {}
    monkeypatch.setattr(tts, "synth_edge", lambda lines, cfg, wd, progress=None: got.setdefault("edge", lines) and {})
    monkeypatch.setattr(tts, "synth_chatterbox", lambda lines, cfg, wd, progress=None: got.setdefault("cb", lines) and {})
    line = tts.Line(0, "Alex Hartley is P24 in GT3, number 2.")
    tts.synthesize([line], CommentarySettings(tts_engine="edge"), tmp_path)
    tts.synthesize([line], CommentarySettings(tts_engine="chatterbox"), tmp_path)
    for engine in ("edge", "cb"):
        assert got[engine][0].text == "Alex Hartley is position twenty-four in GT three, number two."
    assert line.text == "Alex Hartley is P24 in GT3, number 2."          # the stored/validated text keeps its digits
