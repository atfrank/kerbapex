"""Make commentary text speakable. TTS models read digit strings badly ("24" -> "2, 4"), so every number is
turned into words before it is voiced. The displayed/validated text keeps the digits.

    P24            -> "position twenty-four"   GT3        -> "GT three"
    #7 / number 2  -> "number seven" / "number two"
    1:41.366       -> "one minute forty-one point three six six"
    0.25s          -> "zero point two five seconds"
    21st / 3rd     -> "twenty-first" / "third"
"""
from __future__ import annotations

import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
        "twelve": "twelfth"}


def int_to_words(n: int) -> str:
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" and {int_to_words(r)}" if r else "")
    if n < 1_000_000:
        k, r = divmod(n, 1000)
        return f"{int_to_words(k)} thousand" + (f" {int_to_words(r)}" if r else "")
    return " ".join(_ONES[int(d)] for d in str(n))             # absurdly large: read the digits


def _ordinal(n: int) -> str:
    """twenty-first, third, fortieth, one hundred and first..."""
    w = int_to_words(n)
    head, sep, last = w.rpartition("-")
    if sep:                                                     # "twenty-one" -> "twenty-first"
        return head + "-" + _ORD.get(last, last + "th")
    prefix, _, base = w.rpartition(" ")
    prefix = prefix + " " if prefix else ""
    if base.endswith("y"):                                      # "twenty" -> "twentieth"
        return prefix + base[:-1] + "ieth"
    return prefix + _ORD.get(base, base + "th")


def _digits(s: str) -> str:
    return " ".join(_ONES[int(c)] for c in s)


def _decimal(whole: str, frac: str) -> str:
    return f"{int_to_words(int(whole))} point {_digits(frac)}"


def speakable(text: str) -> str:
    t = text
    # lap times first: 1:41.366 / 2:05
    t = re.sub(r"\b(\d+):(\d{2})\.(\d+)\b",
               lambda m: f"{int_to_words(int(m[1]))} minute{'s' if m[1] != '1' else ''} "
                         f"{int_to_words(int(m[2]))} point {_digits(m[3])}", t)
    t = re.sub(r"\b(\d+):(\d{2})\b",
               lambda m: f"{int_to_words(int(m[1]))} minute{'s' if m[1] != '1' else ''} {int_to_words(int(m[2]))}", t)
    # ordinals: 21st -> twenty-first
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: _ordinal(int(m[1])), t)
    # car numbers: "#7" -> "number seven"
    t = re.sub(r"#\s?(\d+)", lambda m: f"number {int_to_words(int(m[1]))}", t)
    # seconds: 0.25s / 12s -> "... seconds"
    t = re.sub(r"\b(\d+)\.(\d+)\s?s\b", lambda m: _decimal(m[1], m[2]) + " seconds", t)
    t = re.sub(r"\b(\d+)\s?s\b", lambda m: int_to_words(int(m[1])) + (" second" if m[1] == "1" else " seconds"), t)
    # other decimals
    t = re.sub(r"\b(\d+)\.(\d+)\b", lambda m: _decimal(m[1], m[2]), t)
    # race positions: P24 -> "position twenty-four" (a bare "P" is read as a sound, not a letter)
    t = re.sub(r"\bP(\d+)\b", lambda m: f"position {int_to_words(int(m[1]))}", t)
    # letters glued to digits: GT3, LMP2 -> "GT three", "LMP two"
    t = re.sub(r"\b([A-Za-z]{1,4})(\d+)\b", lambda m: f"{m[1]} {int_to_words(int(m[2]))}", t)
    # whatever integers remain
    t = re.sub(r"\d+", lambda m: int_to_words(int(m[0])), t)
    return re.sub(r"\s{2,}", " ", t).strip()
