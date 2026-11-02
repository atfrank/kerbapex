"""Live overlay and coach, on synthetic data only (the game's header may not be copied, and real laps stay private)."""
import math
import struct

import pytest
from pathlib import Path

from stintradio import coach as C, laps as LP, layout as L
from stintradio.reader import Sample
from stintradio.service import LiveService

HEADER = """
#pragma pack( push, 4 )
enum class IP_Kind : uint8_t { A, B };
struct TelemVect3 { union { struct { double x, y, z; }; double data[3]; };
  void Set( const double a ) { x = a; } };
struct Inner { float a; unsigned char b[3]; };
struct Thing
{
  long mID;                    // 4
  double mTime;                // packed to 4: offset 4
  TelemVect3 mPos;             // 12
  unsigned long long mSteam;   // 36
  char *mName;                 // pointer, 44
  IP_Kind mKind;               // 52
  Inner mInner[2];             // 56 (8 bytes each)
  bool mA, mB;                 // 72, 73
  float_t mF;                  // 76
  double Method( long i ) const { return 0.0; }
};
#pragma pack( pop )
"""


def test_layout_parser_handles_the_header_constructs():
    st = L.parse(HEADER, pack=4)
    f = st["Thing"].fields
    assert f["mID"][0] == 0 and f["mTime"][0] == 4 and f["mPos"][0] == 12
    assert f["mSteam"][0] == 36 and f["mName"] == (44, "ptr", 1) and f["mKind"][0] == 52
    assert f["mInner"] == (56, "Inner", 2) and f["mA"][0] == 72 and f["mB"][0] == 73 and f["mF"][0] == 76
    assert st["Thing"].size == 80 and st["TelemVect3"].size == 24 and "Method" not in f


def _v(n: int) -> bytes:
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _fld(f: int, v) -> bytes:
    if isinstance(v, float):
        return _v(f << 3 | 5) + struct.pack("<f", v)
    if isinstance(v, int):
        return _v(f << 3) + _v(v)
    return _v(f << 3 | 2) + _v(len(v)) + v


def _msg(*fields) -> bytes:
    body = b"".join(_fld(f, v) for f, v in fields)
    return body


def test_coach_dave_files_are_read_and_partial_laps_dropped(tmp_path):
    head = _msg((1, b"BMW_M4_LMGT3_2023"), (2, b"Test Ring"), (3, b"race"))
    def lap(no, ms, last_frac):
        n = 400
        smp = [_msg((2, 1.0), (3, 0.0), (4, 200.0), (10, round(k / n * last_frac, 3)), (11, int(k / n * ms)))
               for k in range(n)]
        split = [ms // 3, ms // 3, ms - 2 * (ms // 3)]
        return _msg((1, no), (5, ms), (12, b"".join(_v(x) for x in split)), *[(6, s) for s in smp])
    data = b"".join(_v(len(m)) + m for m in (head, lap(1, 95000, 1.0), lap(2, 9000, 0.1)))
    p = tmp_path / "lemans_ultimate-1792937078022-BMW_M4_LMGT3_2023-Test Ring-race.delta"
    p.write_bytes(data)
    got = LP.read_delta(p)
    assert len(got) == 1                                       # the 9-second fragment is not a lap
    l = got[0]
    assert l.time == 95.0 and l.track == "Test Ring" and l.normalized and sum(l.sectors) == pytest.approx(95.0)
    assert LP.key(l.car) == LP.key("BMW M4 LMGT3")              # Coach Dave and the game name the car alike
    d = [s[0] for s in l.samples]
    assert all(b >= a for a, b in zip(d, d[1:])) and l.samples[10][1] == 200.0 and l.samples[10][3] == 100.0


def _lap(brake_at: float, time_s: float, n: int = 1000, length: float = 2000.0) -> LP.Lap:
    """A two-corner lap: flat out, brake hard at `brake_at` for corner one (apex 600 m), then corner two at 1500 m."""
    pts, t = [], 0.0
    for k in range(n):
        d = k / n * length
        if brake_at <= d < 600:
            v, b, th = 220 - (d - brake_at) / (600 - brake_at) * 140, 80, 0
        elif 600 <= d < 700:
            v, b, th = 80 + (d - 600) * 1.2, 0, 100
        elif 1380 <= d < 1500:
            v, b, th = 230 - (d - 1380) * 1.1, 70, 0
        elif 1500 <= d < 1600:
            v, b, th = 98 + (d - 1500), 0, 100
        else:
            v, b, th = 220, 0, 100
        pts.append([d, v, b, th, d, 0.0, round(t, 4)])
        t += (length / n) / (v / 3.6)
    return LP.Lap("Test Ring", "Car", round(t * time_s / max(t, 1e-6), 3), "live", pts)


def test_coach_reports_braking_too_early_with_its_measured_cost():
    ref, mine = _lap(480, 60.0), _lap(420, 61.0)
    c = C.Coach(ref, "Test Ring", 2000.0)
    assert [round(k.apex) for k in c.corners] == [600, 1500]
    assert round(c.corners[0].brake) == 480
    note = c.judge(mine, c.corners[0], 3)
    assert note and note.delta > 0 and note.brake_diff == pytest.approx(-60, abs=4)
    assert "braked 60 m earlier" in note.text or "braked 58 m earlier" in note.text or "braked 62 m earlier" in note.text
    assert not c.judge(ref, c.corners[0], 3).report            # the reference against itself: nothing to say


def _feed(svc, lap, track_len, lap_no, base, last=-1.0):
    for p in lap.samples:
        svc.step(Sample(t=base + p[6], lap=lap_no, lap_start=base, dist=p[0], speed=p[1], throttle=p[3], brake=p[2],
                        steer=0, gear=3, rpm=0, x=p[4], z=p[5], delta_best=0, invalid=False, track=lap.track,
                        car=lap.car, track_len=track_len, last_lap=last, best_lap=-1))


def test_service_saves_laps_coaches_corners_and_keeps_the_best_as_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    svc = LiveService("")
    ref, slow, fast = _lap(480, 60.0), _lap(420, 61.0), _lap(500, 59.0)
    b2 = ref.samples[-1][6] + 0.02                                             # lap 2 starts as lap 1 crosses the line
    _feed(svc, ref, 2000.0, 1, 0.0)
    assert svc.coach is None and svc.state()["status"] == "driving"            # no reference yet
    _feed(svc, slow, 2000.0, 2, b2, last=999.0)                                # lap 1 ends: becomes the reference
    assert len(svc.saved) == 1 and svc.ref_info["source"] == "live"
    assert svc.ref_info["time"] == pytest.approx(b2, abs=0.01)                 # timed from the game's lap starts
    notes = [n for n in svc.notes if n.lap == 2]
    assert notes and notes[-1].corner == "corner one" and notes[-1].delta > 0
    s = svc.state()
    assert s["delta"] > 0 and s["ref_window"] and s["cur_window"] and s["rolling"]
    assert all(-250 <= p[0] <= 450 for p in s["ref_window"])
    # a lap faster than the reference replaces it
    b3 = b2 + slow.samples[-1][6] + 0.02
    _feed(svc, fast, 2000.0, 3, b3)
    b4 = b3 + fast.samples[-1][6] + 0.02
    _feed(svc, fast, 2000.0, 4, b4)
    assert fast.samples[-1][6] < ref.samples[-1][6]
    assert svc.ref_info["time"] == pytest.approx(b4 - b3, abs=0.01) and len(svc.saved) == 3


def test_lap_builder_drops_invalid_and_reset_laps():
    b = LP.LapBuilder()
    lap = _lap(480, 60.0)
    mk = lambda p, n, inv=False, last=-1.0: Sample(p[6], n, 0, p[0], p[1], p[3], p[2], 0, 3, 0, p[4], p[5], 0, inv,
                                                     "T", "C", 2000.0, last, -1)
    T = lap.samples[-1][6] + 0.02
    mk2 = lambda p, n, start, inv=False: Sample(start + p[6], n, start, p[0], p[1], p[3], p[2], 0, 3, 0, p[4], p[5], 0,
                                                inv, "T", "C", 2000.0, -1, -1)
    for p in lap.samples:
        b.add(mk2(p, 1, 0.0, inv=p[0] > 900))                 # a track-limits invalidation during the lap
    assert b.add(mk2(lap.samples[0], 2, T)) is None
    for p in lap.samples[1:]:
        b.add(mk2(p, 2, T))
    done = b.add(mk2(lap.samples[0], 3, 2 * T))
    assert done and done.time == pytest.approx(T, abs=0.01)


def test_car_names_match_across_coach_dave_and_the_game():
    assert LP.same_car("Ginetta_G61Evo_2025", "Ginetta G61-LT-P325 Evo")
    assert LP.same_car("BMW_M4_LMGT3_2023", "BMW M4 LMGT3")
    assert not LP.same_car("BMW_M4_LMGT3_2023", "McLaren 720S LMGT3 Evo")
    assert not LP.same_car("Porsche_911RSR-19_2023", "Porsche 911 GT3 R LMGT3")


def test_countdown_is_timed_in_seconds_to_the_reference_braking_point():
    from stintradio import voice as V
    corner = C.Corner(1, "corner one", 900.0, 1100.0, 1000.0, 1250.0, 500.0, gear=2)
    cues, kmh, said = V.BrakeCues(countdown_from=4), 216.0, []      # 60 m/s
    d = 600.0
    while d < 1300:
        for clip in cues.due(d, kmh, 3000.0, [corner]):
            said.append((round((1000.0 - d) / 60.0, 2), clip))       # seconds to the braking point when said
        d += 60.0 * 0.02
    names = [c for _, c in said]
    assert names == ["brake_in_4", "n3", "n2", "n1", "brake", "gear2"]
    for (eta, clip), want in zip(said, [4, 3, 2, 1, 0, 0]):
        assert want <= eta <= want + 0.2, (clip, eta)                 # a touch early, never late
    # the next lap counts the same corner down again
    again = []
    for lap_d in [600.0 + 60 * 0.02 * k for k in range(200)]:
        again += cues.due(lap_d, kmh, 3000.0, [corner])
    assert again[:1] == ["brake_in_4"]


def test_no_countdown_when_slow_or_without_a_braking_point():
    from stintradio import voice as V
    lift = C.Corner(2, "corner two", 0, 2000.0, None, 2150.0, 1500.0, gear=5)
    slow = C.Corner(3, "corner three", 0, 2600.0, 2500.0, 2750.0, 2200.0, gear=3)
    cues = V.BrakeCues(4)
    assert cues.due(1900.0, 250.0, 3000.0, [lift]) == []                # a flat-out kink: nothing to count down
    assert cues.due(2440.0, 40.0, 3000.0, [slow]) == []                 # crawling (pits, after a spin)


def test_speaker_plays_in_order_and_skips_late_clips(tmp_path):
    import threading
    from stintradio import voice as V
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.wav").write_bytes(b"x")
    clock = {"t": 0.0}
    played, gate = [], threading.Event()
    def play(p):
        played.append(p[-5:-4])
        if p.endswith("a.wav"):
            clock["t"] += 1.0                                            # "a" takes a whole second to say
        gate.set() if p.endswith("c.wav") else None
    sp = V.Speaker(tmp_path, play=play, stale=0.6, now=lambda: clock["t"])
    sp.say("a"); sp.say("b")                                             # b is a second late once a has finished
    import time as _t; _t.sleep(0.2)
    sp.say("c")
    gate.wait(2)
    assert played == ["a", "c"]


def test_in_a_race_the_class_leaders_fastest_lap_becomes_the_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    svc = LiveService("")
    me, lead_slow, lead_fast = _lap(420, 61.0), _lap(470, 60.0), _lap(500, 59.0)
    svc.set_leader({"id": 3, "name": "Vinicius Medeiros#1147", "cls": "LMP3"})
    T = lambda lap: lap.samples[-1][6] + 0.02
    def lead(lap, n, base):
        for p in lap.samples:
            svc.step_leader(Sample(base + p[6], n, base, p[0], p[1], p[3], p[2], 0, 3, 0, p[4], p[5], 0, False,
                                   "Test Ring", "Duqueine D09", 2000.0, -1, -1))
    _feed(svc, me, 2000.0, 1, 0.0)                                             # my out-lap starts recording
    lead(lead_slow, 1, 0.0)
    lead(lead_fast, 2, T(lead_slow))
    lead(lead_fast, 3, T(lead_slow) + T(lead_fast))                           # lap 2 complete: the faster one
    assert svc.ref_info["source"] == "leader" and svc.ref_info["driver"] == "Vinicius Medeiros"
    assert svc.ref_info["time"] == pytest.approx(T(lead_fast), abs=0.02)
    assert not svc.saved                                                      # the leader's laps are not stored as mine
    svc.set_leader({"id": 7, "name": "Someone Else", "cls": "LMP3"})           # a new leader starts afresh
    assert svc.leader_builder.lap is None and svc.ref_info["source"] == "leader"


def test_short_shifts_are_found_and_called_when_still_in_the_gear():
    from stintradio import voice as V
    # a lap that changes up at ~7400 rpm everywhere except 2->3 at 6200 just after the corner at 600 m
    pts, gear, t = [], 1, 0.0
    shifts = {100: 2, 200: 3, 300: 4, 650: 3, 900: 4, 1100: 5, 1300: 6}
    for k in range(0, 1500):
        d = float(k)
        if k in shifts:
            gear = shifts[k]
        rpm = 6200 if 640 <= k < 650 else 7400 if (k + 1) in shifts else 5000
        pts.append([d, 150.0, 0, 100, d, 0.0, t, 0 if k in shifts else gear, rpm])
        t += 0.024
    if True:
        pts[599][7] = 2                                   # the corner: down to 2nd at 600 m
        for k in range(560, 650):
            pts[k][7] = 2
    lap = LP.Lap("T", "C", t, "live", pts)
    ss = C.short_shifts(lap, [600.0])
    assert [(round(s.dist), s.from_gear, s.to_gear, s.rpm) for s in ss] == [(651, 2, 3, 6200)]   # first reading in 3rd
    cues = V.BrakeCues(4)
    assert cues.shift_due(600.0, 150.0, 2, 1500.0, ss) == []            # 50 m away at 150 km/h: not yet
    assert cues.shift_due(642.0, 150.0, 2, 1500.0, ss) == ["short_shift"]
    assert cues.shift_due(645.0, 150.0, 2, 1500.0, ss) == []            # once per pass
    assert V.BrakeCues(4).shift_due(642.0, 150.0, 3, 1500.0, ss) == []  # already changed up: nothing to say


def test_elevenlabs_clips_are_made_once_and_never_trimmed_to_nothing(tmp_path, monkeypatch):
    import shutil, subprocess
    from stintradio import voice as V
    from stintradio.config import VoiceSettings as CommentarySettings
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    monkeypatch.setattr(V, "data_dir", lambda: tmp_path)
    monkeypatch.setattr("driverdirector.tts.cloud_key", lambda e: "k")
    src = tmp_path / "word.mp3"                    # a word with a gap in the middle, like "o-ne"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=300:d=0.25,apad=pad_dur=0.15[a];sine=f=300:d=0.3[b];[a][b]concat=n=2:v=0:a=1",
                    str(src)], check=True)
    calls = []
    def post(url, headers, body):
        calls.append(body["text"])
        return src.read_bytes()
    d = V.make_clips_elevenlabs(CommentarySettings(elevenlabs_voice_id="voice123"), 12.0, post=post)
    import wave
    with wave.open(str(d / "n1.wav")) as w:
        assert w.getnframes() / w.getframerate() > 0.6            # the gap did not end the clip
    n = len(calls)
    assert "Brake!" in calls and "Brake in four." in calls and n == len(V.phrases(6))
    V.make_clips_elevenlabs(CommentarySettings(elevenlabs_voice_id="voice123"), 12.0, post=post)
    assert len(calls) == n                                         # cached: nothing is sent again


def test_engineer_debrief_names_the_costliest_corner_and_sets_the_focus():
    from stintradio.engineer import Engineer, say_laptime, say_delta
    assert say_laptime(103.424) == "one forty-three four" and say_laptime(61.9) == "one oh one nine"
    assert say_delta(0.23) == "two tenths" and say_delta(0.5) == "half a second" and say_delta(1.24) == "one point two"
    e = Engineer(seed=1)
    notes = [C.Note(4, "corner two", 0.12, None, [], "Corner two: +0.12s"),
             C.Note(4, "corner five", 0.31, -16.0, [], "Corner five: +0.31s - braked 16 m earlier")]
    msgs = e.lap_done(104.0, 103.7, "leader", 103.5, notes)
    assert "one forty-four flat" in msgs[0].text.lower() and "three tenths" in msgs[0].text.lower() and "leader" in msgs[0].text
    assert "turn five" in msgs[1].text.lower() and "braking about fifteen metres early" in msgs[1].text
    assert e.focus == 5 and e.focus_tip == "try to brake a bit later"
    assert "brake a bit later" in e.approaching(5, "corner five")[0].text.lower()
    assert e.approaching(2, "corner two") == []
    best = e.lap_done(103.2, 103.7, "leader", 103.5, [])
    assert "new" in best[0].text.lower() and e.focus is None
    for m in msgs + best:                                             # no assumed pronouns anywhere
        assert not any(w in f" {m.text.lower()} " for w in (" he ", " she ", " his ", " her "))


def test_engineer_race_calls_are_spaced_and_pressure_is_urgent():
    from stintradio.engineer import Engineer
    e = Engineer(seed=2)
    first = e.race(1000.0, 11, 1.4, 2.5, 0.3)
    assert first and first[0].text.startswith("P11") and "gap ahead one point four" in first[0].text
    assert e.race(1030.0, 11, 1.2, 2.4, 0.3) == []                       # not again so soon
    urgent = e.race(1040.0, 11, 1.2, 0.4, 0.8)
    assert urgent and urgent[0].priority == 0 and "behind" in urgent[0].text.lower()
    later = e.race(1130.0, 11, 0.7, 1.0, 0.3)
    assert "closing" in later[0].text


def test_engineer_only_talks_on_straights(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    svc = LiveService("")
    ref = _lap(480, 60.0)
    svc.coach = C.Coach(ref, "Test Ring", 2000.0)
    mk = lambda d, kmh, brk: Sample(0, 1, 0, d, kmh, 100, brk, 0, 4, 0, 0, 0, 0, False, "Test Ring", "Car", 2000.0, -1, -1)
    svc.sample = mk(1000.0, 220.0, 0)            # mid-straight between the corners
    assert svc.quiet()
    svc.sample = mk(420.0, 220.0, 0)             # under 3.5 s to the braking point at 480 m
    assert not svc.quiet()
    svc.sample = mk(500.0, 150.0, 80)            # braking
    assert not svc.quiet()
    svc.sample = mk(620.0, 100.0, 0)             # still in the corner (apex 600)
    assert not svc.quiet()


def test_radio_clips_are_made_once_and_never_fall_back_to_windows(tmp_path, monkeypatch):
    import shutil, subprocess
    from stintradio import radio as R
    from stintradio.config import VoiceSettings as CommentarySettings
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    monkeypatch.setattr(R, "data_dir", lambda: tmp_path)
    monkeypatch.setattr("driverdirector.tts.cloud_key", lambda e: "k")
    src = tmp_path / "speech.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=500:d=0.8", str(src)], check=True)
    sent = []
    def post(url, headers, body, tries=2, timeout=8.0):
        sent.append(body["text"])
        return src.read_bytes()
    r = R.Radio("elevenlabs", CommentarySettings(elevenlabs_voice_id="v1"), post=post)
    p = r.make("Box this lap.")
    assert p and p.is_file() and sent == ["Box this lap."]
    assert r.make("Box this lap.") == p and len(sent) == 1               # cached by its text
    import wave
    with wave.open(str(p)) as w:
        assert w.getnframes() / w.getframerate() > 0.9                   # speech plus the radio clicks
    def fail(*a, **k):
        raise RuntimeError("no network")
    windows_called = []
    r2 = R.Radio("elevenlabs", CommentarySettings(elevenlabs_voice_id="v2"), post=fail)
    def fake_windows(text, raw):
        windows_called.append(text)
        raw.write_bytes(src.read_bytes())
    r2._windows = fake_windows
    assert r2.make("Gap behind one point two.") is None and windows_called == []     # the clone voice or silence
    assert r2.errors == ["no network"]


def test_a_long_call_waits_for_a_long_enough_straight(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    svc = LiveService("")
    svc.coach = C.Coach(_lap(480, 60.0), "Test Ring", 2000.0)
    svc.sample = Sample(0, 1, 0, 1050.0, 220.0, 100, 0, 0, 4, 0, 0, 0, 0, False, "Test Ring", "Car", 2000.0, -1, -1)
    # next braking point (corner two, ~1380 m) is ~5.4 s away at 220 km/h
    assert svc.quiet(2.0) and svc.quiet(4.0) and not svc.quiet(5.0)


def _note(lap, n, delta=0.1, my_gear=None, ref_gear=None, held=None, short_to=None, brake_diff=None, causes=(), apex_off=None):
    return C.Note(lap, f"corner {['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven'][n]}", delta, brake_diff, list(causes), "",
                  n=n, my_gear=my_gear, ref_gear=ref_gear, held=held, short_to=short_to, apex_off=apex_off)


def test_engineer_spots_a_habit_and_coaches_it_before_the_corner():
    from stintradio.engineer import Engineer
    e = Engineer(seed=3)
    lap1 = e.lap_done(104.0, 103.0, "leader", None, [_note(1, 4, 0.2, my_gear=3, ref_gear=2)])
    assert not any(m.kind == "habit" for m in lap1)                 # once is not a habit
    lap2 = e.lap_done(104.0, 103.0, "leader", None, [_note(2, 4, 0.25, my_gear=3, ref_gear=2)])
    habit = [m for m in lap2 if m.kind == "habit"]
    assert habit and "a gear high in turn four" in habit[0].text and "Try second" in habit[0].text
    assert e.focus == 4 and "try second gear" in e.approaching(4, "corner four")[0].text.lower()
    lap3 = e.lap_done(104.0, 103.0, "leader", None, [_note(3, 4, 0.22, my_gear=3, ref_gear=2)])
    assert not any(m.kind == "habit" for m in lap3) and e.focus == 4  # said once, then only the reminder
    e.lap_done(103.5, 103.0, "leader", None, [_note(4, 4, 0.0, my_gear=2, ref_gear=2)])
    fixed = e.lap_done(103.2, 103.0, "leader", None, [_note(5, 4, 0.0, my_gear=2, ref_gear=2)])
    assert any(m.kind == "praise" and "turn four" in m.text.lower() for m in fixed)


def test_short_shift_and_braking_habits_are_named_with_what_to_try():
    from stintradio.engineer import Engineer
    e = Engineer(seed=4)
    for lap in (1, 2, 3):
        notes = [_note(lap, 5, 0.3, held=True, short_to=3),
                 _note(lap, 1, 0.1, brake_diff=-18 if lap != 2 else -2)]
        msgs = e.lap_done(104.0, 103.0, "live", None, notes)
    issues = e.patterns()
    kinds = [(i["corner"], i["kind"]) for i in issues]
    assert kinds[0] == (5, "short_shift")                             # the costlier habit is worked on first
    assert (1, "brake_early") in kinds
    assert "short-shift to third on the exit" in e.approaching(5, "corner five")[0].text.lower()
    deb = next(i["debrief"] for i in issues if i["kind"] == "brake_early")
    assert "braking early into turn one" in deb and "two of the last three laps" in deb


def test_settings_carry_over_from_driverdirector(tmp_path):
    from stintradio.config import Settings
    p = tmp_path / "settings.json"
    p.write_text('{"live": {"voice_engine": "elevenlabs", "style": "engineer"}, '
                 '"commentary": {"elevenlabs_voice_id": "mine", "elevenlabs_model": "eleven_multilingual_v2"}}')
    s = Settings.from_driverdirector(p)
    assert s.live.voice_engine == "elevenlabs" and s.voice.elevenlabs_voice_id == "mine" and s.port == 6420
    assert Settings.from_driverdirector(tmp_path / "none.json") is None


def test_app_routes():
    from fastapi.testclient import TestClient
    from stintradio.config import Settings
    from stintradio.server import create_app
    c = TestClient(create_app(Settings()))
    assert c.get("/api/state").json()["status"] == "stopped"
    assert c.post("/api/stop").json() == {"running": False}
    assert "StintRadio" in c.get("/").text


def test_reminder_is_said_every_other_lap_at_most():
    from stintradio.engineer import Engineer
    e = Engineer(seed=1)
    e.focus, e.focus_tip = 5, "brake later"
    e._laps = 3
    assert e.approaching(5, "corner five")
    assert e.approaching(5, "corner five") == []
    e._laps = 4
    assert e.approaching(5, "corner five") == []
    e._laps = 5
    assert e.approaching(5, "corner five")


def test_countdown_variety_starts_at_different_points_and_never_overlaps():
    from types import SimpleNamespace as NS
    from stintradio.voice import BrakeCues
    cues = BrakeCues(4, True, variety=True, seed=3)
    corner = NS(n=1, brake=1000.0, apex=1100.0, gear=3)
    starts = set()
    for lap in range(30):
        said = []
        for d in range(0, 1000, 5):                      # 50 m/s: 0.1 s per step
            said += cues.due(float(d), 180.0, 5000.0, [corner])
        for d in range(1000, 1200, 10):                  # through the corner: ready for the next pass
            cues.due(float(d), 180.0, 5000.0, [corner])
        assert said.count("brake") == 1
        starts.add(said[0])
    assert len(starts) >= 3


# ---- race awareness (racewatch) ----------------------------------------------------------------------------------
def _snap(**kw):
    me = {"id": 1, "place": 3, "cls": "Hypercar", "me": True, "dist": 1000.0, "in_pits": False, "laps": 0,
          "penalties": 0, "finish": 0, "pit_state": 0, "flag": 0}
    s = {"session": 10, "t": 0.0, "end_t": 0.0, "max_laps": 10, "phase": 5, "yellow_state": 0, "sector_flags": [0, 0, 0],
         "raining": 0.0, "wetness": 0.0, "track_len": 5000.0, "me": me, "cars": [me], "fuel": 50.0, "fuel_cap": 90.0,
         "wear": [1.0] * 4, "impact_t": 0.0, "impact_mag": 0.0, "lap": 1, "tyre": "Medium"}
    me.update(kw.pop("me", {}))
    s.update(kw)
    return s


def _laps(rw, n, fuel0=50.0, use=3.0, lap_s=100.0):
    out = []
    for k in range(1, n + 1):
        out += rw.update(_snap(lap=k + 1, t=k * lap_s, fuel=fuel0 - use * k, me={"laps": k}))
    return out


def test_fuel_short_of_the_flag_gives_the_last_lap_to_box():
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    rw.update(_snap(lap=1, t=0.0, fuel=22.0))
    msgs = _laps(rw, 3, fuel0=22.0)
    assert rw.per_lap() == pytest.approx(3.0)
    text = " ".join(m.text for m in msgs)
    assert "You'll need a stop" in text and "box by" in text


def test_fuel_comfortable_is_only_a_nice_to_have_and_quiet_drops_it():
    from stintradio.racewatch import RaceWatch
    for talk, expect in (("normal", True), ("quiet", False)):
        rw = RaceWatch(talk)
        rw.update(_snap(lap=1, t=0.0, fuel=90.0))
        msgs = [m for m in _laps(rw, 3, fuel0=90.0, use=1.0) if m.kind == "fuel"]
        assert bool(msgs) == expect
        assert all(m.priority == 2 for m in msgs)


def test_fill_on_pit_entry_is_sized_to_the_end_and_capped():
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    rw.update(_snap(lap=1, t=0.0, fuel=50.0))
    _laps(rw, 3, use=3.0)                                          # 41 litres left, 7 laps to go
    out = rw.update(_snap(lap=4, t=350.0, fuel=20.0, me={"laps": 3, "in_pits": True}))
    fill = [m for m in out if m.kind == "fuel"]
    assert fill and "litres" in fill[0].text


def test_laps_to_go_flags_and_penalties_are_called_once():
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    rw.update(_snap(lap=8, me={"laps": 7}))
    out = rw.update(_snap(lap=9, me={"laps": 8}))
    assert any("Two laps to go" in m.text for m in out)
    assert not any("Two laps" in m.text for m in rw.update(_snap(lap=9, me={"laps": 8})))
    out = rw.update(_snap(phase=6, me={"laps": 8, "penalties": 1, "flag": 6}))
    texts = " ".join(m.text for m in out)
    assert "Safety car" in texts and "penalty" in texts and "Blue flag" in texts


def test_rain_and_wet_track_and_tyre_wear_either_direction():
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    assert any("started to rain" in m.text for m in rw.update(_snap(raining=0.2)))
    assert any("wets" in m.text for m in rw.update(_snap(raining=0.4, wetness=0.5)))
    for fresh, half in ((1.0, [0.5, 0.5, 0.5, 0.4]), (0.0, [0.5, 0.5, 0.5, 0.6])):    # counts down, or counts up
        rw = RaceWatch()
        rw.update(_snap(wear=[fresh] * 4))
        out = rw.update(_snap(wear=half))
        assert any("half gone" in m.text for m in out) and any("rear right" in m.text or "front left" in m.text for m in out)


def test_faster_class_car_closing_from_behind_is_called_once():
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    gt = {"id": 2, "place": 9, "cls": "LMGT3", "me": False, "dist": 700.0, "in_pits": False}
    me_d = 1000.0
    s1 = _snap(t=10.0, cars=[gt])
    s1["me"]["dist"] = me_d
    rw.update(s1)
    gt2 = dict(gt, dist=gt["dist"] + 30)              # 30 m closed in the second: gap 270 m, closing 30 m/s
    s2 = _snap(t=11.0, cars=[gt2])
    s2["me"]["dist"] = me_d
    out = rw.update(s2)
    assert len(out) == 1 and out[0].priority == 0 and "LMGT3" in out[0].text
    s3 = _snap(t=12.0, cars=[dict(gt, dist=gt["dist"] + 60)])
    s3["me"]["dist"] = me_d
    assert not rw.update(s3)


# ---- push-to-talk and asking the engineer -------------------------------------------------------------------------
def test_button_names_and_wheel_buttons_are_understood():
    from stintradio.ptt import Button
    assert Button("F9").vk == 0x78 and Button("rctrl").vk == 0xA3 and Button("a").vk == 0x41
    assert Button("joy1:5").joy == (0, 4)
    with pytest.raises(ValueError):
        Button("banana")


def test_push_to_talk_records_only_while_held_and_ignores_taps(tmp_path):
    from stintradio.ptt import PushToTalk
    clip = tmp_path / "ask.wav"
    clip.write_bytes(b"x")
    state = {"down": False, "t": 0.0, "log": []}

    class Btn:
        def down(self): return state["down"]

    class Rec:
        def start(self): state["log"].append("start")
        def stop(self): state["log"].append("stop"); return clip

    got = []
    ptt = PushToTalk(Btn(), Rec(), got.append, poll=0, clock=lambda: state["t"])
    seq = [(False, 0), (True, 0), (True, 2.0), (False, 2.0),          # held two seconds: a question
           (True, 3.0), (False, 3.1), (False, 4.0)]                    # a tap: dropped
    it = iter(seq)

    def tick():
        try:
            state["down"], state["t"] = next(it)
        except StopIteration:
            ptt.stop_flag = True
    ptt.button.down = lambda: (tick(), state["down"])[1]
    ptt.run()
    import time as _t; _t.sleep(0.1)
    assert state["log"] == ["start", "stop", "start", "stop"] and got == [clip]


def test_transcribe_answer_and_clip_is_deleted(tmp_path):
    from types import SimpleNamespace
    from stintradio import ask
    d = tmp_path / "clip"
    d.mkdir()
    wav = d / "ask.wav"
    wav.write_bytes(b"RIFF")
    seen = {}

    def post(url, headers, body):
        seen.update(url=url, key=headers["xi-api-key"], body=body)
        return b'{"text": "How much fuel do I have?"}'

    class Client:
        def __init__(self): self.messages = self
        def create(self, model="", messages=()):
            seen["prompt"] = messages[0]["content"]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Forty litres, about " + "word " * 60)])

    svc = LiveService(str(tmp_path))
    out = ask.handle(wav, svc, "k", Client(), post=post)
    assert seen["url"].endswith("speech-to-text") and seen["key"] == "k" and b"RIFF" in seen["body"]
    assert "How much fuel" in seen["prompt"] and "No live data" in seen["prompt"]
    assert out.reply.startswith("Forty litres") and len(out.reply.split()) <= ask.MAX_WORDS and out.job is None
    assert not wav.exists()
    wav2 = d.with_name("c2") / "a.wav"
    wav2.parent.mkdir()
    wav2.write_bytes(b"x")
    assert ask.handle(wav2, svc, "k", Client(), post=lambda *a: b'{"text": "uh"}') is None


def test_facts_include_fuel_and_laps_to_go():
    from stintradio import ask
    from stintradio.racewatch import RaceWatch
    svc = LiveService("x")
    svc.watch = RaceWatch()
    svc.snap = _snap(fuel=40.0, me={"laps": 2})
    svc.watch.update(_snap(lap=1, t=0.0, fuel=50.0))
    _laps(svc.watch, 3)
    text = ask.facts(svc)
    assert "Fuel 40.0 litres" in text and "laps to go" in text and "litres a lap" in text


# ---- jobs and memory ----------------------------------------------------------------------------------------------
def test_answer_parses_job_and_note_and_falls_back_to_plain_text():
    from types import SimpleNamespace
    from stintradio import ask

    def client(text):
        c = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **k: c))
    a = ask.answer("q", "f", client('Sure. {"reply": "Will do.", "job": {"when": "laps", "n": 3, "topic": "tyres"}, '
                                    '"remember": "call me Alex", "forget": null}'))
    assert a.reply == "Will do." and a.job["topic"] == "tyres" and a.remember == "call me Alex" and a.forget is None
    assert ask.answer("q", "f", client("Just text.")).reply == "Just text."


def test_jobs_accept_only_the_fixed_shapes():
    from stintradio import jobs
    snap = _snap(me={"laps": 4})
    ok = jobs.parse({"when": "laps", "n": 3, "topic": "tyres"}, snap)
    assert ok and ok.at == 7
    assert jobs.parse({"when": "minutes", "n": 10, "topic": "fuel"}, snap).at == snap["t"] + 600
    assert jobs.parse({"when": "when", "cond": "rain"}, snap)
    for bad in ({"when": "laps", "n": 3, "topic": "setup"}, {"when": "when", "cond": "pit_menu"},
                {"when": "when", "cond": "gap_behind_below", "value": 99}, {"when": "laps", "n": 0, "topic": "fuel"},
                {"when": "run", "cmd": "del"}, "tyres"):
        assert jobs.parse(bad, snap) is None


def test_timed_job_reports_once_and_condition_job_rearms():
    from stintradio import jobs
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    rw.update(_snap(wear=[1.0] * 4))
    book = jobs.JobBook()
    book.add(jobs.parse({"when": "laps", "n": 2, "topic": "tyres"}, _snap(me={"laps": 4})))
    book.add(jobs.parse({"when": "when", "cond": "rain"}, _snap()))
    assert book.due(_snap(me={"laps": 5}), rw, None) == []
    out = book.due(_snap(me={"laps": 6}, wear=[0.4] * 4), rw, None)
    assert len(out) == 1 and "sixty percent worn" in out[0].text
    assert book.due(_snap(me={"laps": 7}), rw, None) == []
    assert [m.text for m in book.due(_snap(raining=0.3), rw, None)] == ["As you asked: rain."]
    assert book.due(_snap(raining=0.3), rw, None) == []                       # not again while it keeps raining
    book.due(_snap(raining=0.0), rw, None)
    assert len(book.due(_snap(raining=0.3), rw, None)) == 1                   # again after it stopped


def test_memory_keeps_notes_and_last_session_between_runs(tmp_path):
    from stintradio.memory import Memory
    path = tmp_path / "m.json"
    m = Memory(path)
    assert m.remember("Call me Alex") and not m.remember("call me alex") and not m.remember("x")
    m.session_done("Algarve", "Ginetta", 103.4, 12, False)
    again = Memory(path)
    assert again.notes == ["Call me Alex"] and "Alex" in again.facts("Algarve", "Ginetta")
    assert "best lap" in again.facts("Algarve", "Ginetta") and "best lap" not in again.facts("Monza", "Ginetta")
    assert again.forget("alex") == 1 and Memory(path).notes == []


# ---- stint plan ---------------------------------------------------------------------------------------------------
def test_plan_places_stops_as_late_as_fuel_allows_and_sizes_the_last_fill():
    from stintradio.plan import make_plan, say_plan
    p = make_plan(laps_done=0, togo=30, fuel=30.0, cap=60.0, use=2.0)         # 15 laps in the tank, 30 to go
    assert [s.lap for s in p.stops] == [14] and p.feasible
    assert p.stops[0].fill == pytest.approx(2.0 * (16 + 1) - 2.0)               # 16 to go + a lap in hand, 2 L left at the stop
    p = make_plan(0, 40, 30.0, 40.0, 2.0)                                       # tank holds 20 laps: two stops
    assert [s.lap for s in p.stops] == [14, 33]
    assert p.stops[0].fill == pytest.approx(38.0) and p.stops[1].fill == pytest.approx(14.0)
    assert "two stops" in say_plan(p)
    assert make_plan(0, 10, 30.0, 40.0, 2.0).stops == [] and "No stop" in say_plan(make_plan(0, 10, 30.0, 40.0, 2.0))
    assert not make_plan(0, 50, 5.0, 4.0, 2.0).feasible


def test_box_this_lap_is_called_on_the_lap_to_come_in():
    from stintradio.racewatch import RaceWatch
    rw = RaceWatch()
    rw.update(_snap(lap=1, t=0.0, fuel=22.0, max_laps=10))
    out = _laps(rw, 5, fuel0=22.0)                                             # 3 L a lap; the plan puts the stop at lap 6
    assert rw.plan.stops[0].lap == 6
    boxes = [m for m in out if "Box this lap" in m.text]
    assert len(boxes) == 1 and boxes[0].priority == 0


# ---- RaceRoom reader ---------------------------------------------------------------------------------------------
def _r3e_frame(**kw):
    import struct
    from stintradio import r3e_reader as R
    b = bytearray(R.SIZE)
    def put(name, fmt, v, at=None):
        struct.pack_into("<" + fmt, b, R.O[name] if at is None else at, v)
    struct.pack_into("<2i", b, 0, 3, 6)
    put("num_cars", "i", 2)
    put("control", "i", 0)
    put("sim_time", "d", 100.0)
    put("dist", "f", 1234.5); put("layout_length", "f", 4011.0)
    put("lap_cur", "f", 40.0); put("lap_prev", "f", 91.5); put("lap_best", "f", 90.9)
    put("speed", "f", 50.0); put("rps", "f", 600.0); put("gear", "i", 4)
    put("throttle_raw", "f", 0.8); put("brake_raw", "f", 0.0)
    put("completed", "i", 2); put("lap_valid", "i", 1); put("model_id", "i", 7); put("slot_id", "i", 11)
    put("session_type", "i", 2); put("position", "i", 2); put("delta_front", "f", 1.5); put("delta_behind", "f", 0.4)
    put("time_total", "f", 1800.0); put("time_left", "f", 1500.0); put("laps", "i", -1)
    put("fuel", "f", 30.0); put("fuel_cap", "f", 60.0)
    b[R.O["track"]:R.O["track"] + 6] = b"Zolder"
    for k, (slot, place) in enumerate([(5, 1), (11, 2)]):
        o = R.DRIVERS_AT + k * R.RECORD
        b[o:o + 4] = b"Name"
        struct.pack_into("<i", b, o + R.D["slot_id"], slot)
        struct.pack_into("<i", b, o + R.D["place"], place)
    for k, v in kw.items():
        put(k, "i" if isinstance(v, int) else "f", v)
    return bytes(b)


def _r3e_reader(frame):
    from stintradio import r3e_reader as R
    r = object.__new__(R.R3EReader)
    r.cars, r.classes = {7: "Test Car"}, {}
    r.frame = lambda: frame
    return r


def test_r3e_sample_reads_the_players_car():
    s = _r3e_reader(_r3e_frame()).sample()
    assert s.track == "Zolder" and s.car == "Test Car" and s.lap == 3 and s.gear == 4
    assert abs(s.speed - 180.0) < 1e-3 and abs(s.rpm - 600 * 60 / 6.283185307) < 1
    assert abs(s.throttle - 80.0) < 1e-3 and s.brake == 0 and s.dist == 1234.5 and s.track_len == 4011.0
    assert s.last_lap == 91.5 and abs(s.lap_start - 60.0) < 1e-6


def test_r3e_nothing_in_a_menu_or_replay():
    assert _r3e_reader(_r3e_frame(menus=1)).sample() is None
    assert _r3e_reader(_r3e_frame(replay=1)).sample() is None


def test_r3e_race_gaps_and_snapshot():
    r = _r3e_reader(_r3e_frame())
    place, ahead, behind = r.race_gaps()
    assert place == 2 and abs(ahead - 1.5) < 1e-6 and abs(behind - 0.4) < 1e-6
    snap = r.race_snapshot()
    assert snap["session"] == 10 and snap["me"]["me"] and snap["me"]["place"] == 2
    assert snap["end_t"] == 1800.0 and abs((snap["end_t"] - snap["t"]) - 1500.0) < 1e-6
    assert snap["phase"] == 0 and len(snap["cars"]) == 2
    from stintradio.racewatch import RaceWatch
    RaceWatch().update(snap)                      # the race watcher accepts it


def _bend_lap(angle_deg, kmh):
    """A lap along x, with a bend of `angle_deg` (+ = counter-clockwise in x/z) around 500 m, slowest there."""
    import math
    pts, x, z, h = [], 0.0, 0.0, 0.0
    for d in range(0, 1001, 5):
        if 450 <= d < 550:
            h += math.radians(angle_deg) / 20
        x += 5 * math.cos(h)
        z += 5 * math.sin(h)
        v = kmh if 480 <= d <= 520 else 200
        pts.append([float(d), v, 0.0, 100.0, x, z, d / 50.0])
    return LP.Lap("t", "c", 20.0, "live", pts)


def test_corner_look_names_speed_and_side():
    from stintradio.coach import describe
    assert describe(_bend_lap(-60, 60), 500, 1) == "slow right"
    assert describe(_bend_lap(60, 170), 500, 1) == "fast left"
    assert describe(_bend_lap(150, 60), 500, 1) == "left hairpin"
    assert describe(_bend_lap(5, 60), 500, 1) == "slow kink"
    assert describe(_bend_lap(-60, 60), 500, 0) == "slow corner"        # direction unknown: speed only


def test_engineer_uses_the_drivers_name_on_some_calls():
    from stintradio.engineer import Engineer, Msg
    e = Engineer(seed=3, driver="Alex")
    said = [e.address(Msg("Try to brake a bit later.", kind="focus")).text for _ in range(20)]
    assert any(t == "Alex, try to brake a bit later." for t in said) and any(t.startswith("Try") for t in said)
    assert e.address(Msg("Brake in four.", kind="lap")).text == "Brake in four."          # not on every kind of call
    assert Engineer(seed=3).address(Msg("Try.", kind="focus")).text == "Try."             # no name set: unchanged


def test_throttle_cue_and_brake_pressure_habit():
    from stintradio import voice
    from stintradio.engineer import CHECKS
    c = C.Corner(1, "corner one", 100, 300, 200, 450, 0, None, throttle_at=350)
    cues = voice.BrakeCues()
    assert cues.throttle_due(345, 100, 5, 4000, [c]) == ["throttle"]       # about to reach the pick-up point, off the power
    assert cues.throttle_due(347, 100, 5, 4000, [c]) == []                 # once per pass
    cues2 = voice.BrakeCues()
    assert cues2.throttle_due(345, 100, 80, 4000, [c]) == []               # already on it: nothing to say
    soft = C.Note(3, "corner one", 0.2, None, [], "", brake_peak=55.0, ref_peak=85.0)
    assert CHECKS["brake_soft"](soft) and not CHECKS["brake_soft"](C.Note(3, "corner one", 0.2, None, [], "", brake_peak=80.0, ref_peak=85.0))
    assert CHECKS["apex"](C.Note(3, "c", 0.2, None, ["missed the apex (3.0 metres off the line, 5 km/h slower)"], ""))


def test_guide_varies_and_carries_the_tip():
    from stintradio.engineer import Engineer
    e = Engineer(seed=2)
    texts = [e.guide(5, "right hairpin", 92.0).text for _ in range(8)]
    assert len(set(texts)) >= 3 and all("five" in t and "hairpin" in t for t in texts)
    assert "Brake a bit later." in e.guide(3, "fast left", 60.0, "brake a bit later").text
    assert e.guide(2, "", None).text.startswith(("Turn two", "Coming up, turn two"))


def test_line_comment_and_apex_checks():
    from stintradio.engineer import Engineer, CHECKS
    wide = C.Note(2, "corner five", 0.1, None, [], "", n=5, apex_off=-3.0, entry_off=3.0)
    assert CHECKS["apex"](wide) and CHECKS["early_turn"](wide)
    e = Engineer(seed=1)
    m = e.line_comment([C.Note(2, "corner two", 0.0, None, [], "", n=2, apex_off=-0.5), wide])
    assert "turn five" in m.text.lower() and ("wide" in m.text.lower())
    late = C.Note(2, "corner four", 0.1, None, [], "", n=4, throttle_diff=20.0, throttle_s=0.4)
    assert CHECKS["throttle"](late)


def test_line_offset_sign_inside_is_positive():
    from stintradio.coach import line_offset
    # a reference running straight along +x; the driver's lap is 2 m to the counter-clockwise (left, sign +1) side
    ref = LP.Lap("t", "c", 10.0, "live", [[float(d), 100.0, 0.0, 100.0, float(d), 0.0, d / 30.0] for d in range(0, 400, 5)])
    mine = LP.Lap("t", "c", 10.0, "live", [[float(d), 100.0, 0.0, 100.0, float(d), 2.0, d / 30.0] for d in range(0, 400, 5)])
    assert abs(line_offset(mine, ref, 200, +1, 1) - 2.0) < 1e-6      # left-hand corner, left of the line = inside
    assert abs(line_offset(mine, ref, 200, -1, 1) + 2.0) < 1e-6      # right-hand corner: that is the outside
    assert line_offset(mine, ref, 200, 0, 1) is None


def test_ffb_advice_strong_weak_and_good():
    from stintradio.ffb import FFBWatch, judge
    assert judge([0.5] * 100) is None                                       # too little data
    strong = judge([1.0] * 300 + [0.6] * 1500)                              # clipping 17 % of the time
    assert strong.kind == "strong" and strong.scale < 1.0
    assert judge([0.3] * 1600).kind == "weak" and judge([0.3] * 1600).scale > 1.0
    assert judge([0.7] * 1000 + [0.85] * 800).kind == "good"
    w = FFBWatch()
    for x in [1.0] * 300 + [0.6] * 1500:
        w.add(120.0, x)
    msgs = w.lap_done()
    assert msgs and "clipping" in msgs[0].text and "percent" in msgs[0].text and msgs[0].kind == "ffb"
    for x in [1.0] * 300 + [0.6] * 1500:
        w.add(120.0, x)
    assert w.lap_done() == []                                               # same advice: not repeated at once
    w2 = FFBWatch()
    for x in [0.9] * 2000:
        w2.add(5.0, x)                                                      # crawling: says nothing about the setting
    assert w2.stats() is None


def test_r3e_sample_carries_the_force_feedback_output():
    import struct
    from stintradio import r3e_reader as R
    b = bytearray(_r3e_frame())
    struct.pack_into("<d", b, R.O["ffb"], -0.82)
    assert abs(_r3e_reader(bytes(b)).sample().ffb + 0.82) < 1e-9


# ---- force-feedback profile edit ---------------------------------------------------------------------------------
RCS = (b'Name="Wheel"\r\nFFB Device Name="Some Base"\r\nFFB Strength="0.5" // Strength of Force Feedback. Range 0.0 to 1.5.\r\n'
       b'FFB Smoothing="0.25" // keep\r\nFS_TM_PARAM_FF="-1" // Overall FFB Strength\r\n')


def test_ffb_apply_changes_only_the_strength_with_a_backup(tmp_path):
    from stintradio import ffb_apply as FA
    f = tmp_path / "Wheel.rcs"
    f.write_bytes(RCS)
    res = FA.apply(f, 0.45, running=lambda: False, now=lambda: 1_700_000_000)
    new = f.read_bytes()
    assert new == RCS.replace(b'FFB Strength="0.5"', b'FFB Strength="0.45"')            # one value, comment and CRLF kept
    assert Path(res["backup"]).read_bytes() == RCS and res["old"] == 0.5 and res["new"] == 0.45


def test_ffb_apply_refuses_while_the_game_runs_or_out_of_range(tmp_path):
    from stintradio import ffb_apply as FA
    f = tmp_path / "Wheel.rcs"
    f.write_bytes(RCS)
    with pytest.raises(FA.FFBError):
        FA.apply(f, 0.45, running=lambda: True)
    with pytest.raises(FA.FFBError):
        FA.apply(f, 0.0, running=lambda: False)
    assert f.read_bytes() == RCS and list(tmp_path.glob("*.bak-*")) == []              # nothing touched


def test_ffb_profile_choice_and_step_size(tmp_path):
    from stintradio import ffb_apply as FA
    (tmp_path / "A.rcs").write_bytes(RCS)
    (tmp_path / "B.rcs").write_bytes(RCS.replace(b"Some Base", b"none"))
    found = FA.profiles(tmp_path)
    assert FA.pick_profile(found) == "A"                       # the only one with a real device
    (tmp_path / "C.rcs").write_bytes(RCS)
    with pytest.raises(FA.FFBError):
        FA.pick_profile(FA.profiles(tmp_path))                 # two candidates: it will not guess
    assert FA.pick_profile(FA.profiles(tmp_path), "C") == "C"
    assert FA.propose(0.5, 0.6) == 0.45 and FA.propose(0.5, 1.5) == 0.55 and FA.propose(1.45, 1.1) == 1.5


def test_r3e_sample_and_lap_carry_steering_g_tyres_fuel_and_sectors():
    import struct
    from stintradio import r3e_reader as R
    b = bytearray(_r3e_frame())
    struct.pack_into("<d", b, R.O["g_local"], 12.5)                    # sideways
    struct.pack_into("<d", b, R.O["g_local"] + 16, -7.25)              # along the car
    for k in range(4):
        struct.pack_into("<3f", b, R.O["tyre_temp"] + 24 * k, 70.0 + k, 80.0 + k, 90.0 + k)
    struct.pack_into("<3f", b, R.O["sectors_prev"], 30.0, 61.0, 91.5)
    struct.pack_into("<f", b, R.O["steer"], -0.4)
    s = _r3e_reader(bytes(b)).sample()
    assert (s.g_lat, s.g_lon, s.steer) == (12.5, -7.25, pytest.approx(-0.4))
    assert s.tyre_temp == (80.0, 81.0, 82.0, 83.0) and s.fuel == pytest.approx(30.0) and s.sectors == pytest.approx((30.0, 61.0, 91.5))
    lb = LP.LapBuilder()
    from dataclasses import replace
    for i in range(300):
        lb.add(replace(s, lap=3, lap_start=60.0, t=60.0 + i * 0.3, dist=1.0 + i * 13, last_lap=-1.0, invalid=False))
    done = lb.add(replace(s, lap=4, lap_start=151.5, t=151.5, dist=2.0))
    assert done is not None and done.sectors == [30.0, 61.0, 91.5]
    row = done.samples[10]
    assert len(row) == 15 and row[9] == pytest.approx(-0.4) and row[11:13] == [12.5, -7.25] and row[13] == [80, 81, 82, 83]


def test_ask_facts_include_corners_gaps_and_force_feedback():
    from types import SimpleNamespace as NS
    from stintradio import ask
    from stintradio.ffb import FFBWatch
    c = C.Corner(5, "corner five", 100, 300, 200, 450, 0, None, brake_peak=92.0, look="right hairpin")
    w = FFBWatch()
    for _ in range(2000):
        w.add(120.0, 1.0)
    svc = NS(sample=None, snap={"end_t": 1800.0, "t": 900.0, "me": {"place": 9, "cls": "GT3", "laps": 3, "in_pits": False, "penalties": 0},
                   "cars": [1] * 20, "fuel": 30.0, "fuel_cap": 60.0, "tyre": "", "raining": 0.0, "wetness": 0.0, "phase": 0},
             watch=None, ref_info={}, engineer=NS(focus_name="", driver="Alex"),
             memory=None, said_text=[], gaps=(9, 0.5, 0.1), coach=NS(corners=[c]), notes=[], ffb=w)
    text = ask.facts(svc)
    assert "Turn 5 is a right hairpin at 300 m into the lap" in text and "pedal peaking at 92 percent" in text and "car ahead 0.5 s" in text
    assert "Time left in the session: 15 minutes" in text and "Force feedback: verdict strong" in text and "Alex" in text


def test_push_to_talk_toggle_press_starts_and_next_press_stops(tmp_path):
    from stintradio.ptt import PushToTalk
    clip = tmp_path / "ask.wav"
    clip.write_bytes(b"x")
    state = {"down": False, "t": 0.0, "log": []}

    class Rec:
        def start(self): state["log"].append("start")
        def stop(self): state["log"].append("stop"); return clip

    got, beeps = [], []
    ptt = PushToTalk(object(), Rec(), got.append, poll=0, clock=lambda: state["t"], mode="toggle", max_s=12.0,
                     beep=beeps.append)
    # press (with chatter), released, 3 s of talking, press again to stop; then a press that times out after 12 s
    seq = [(False, 0), (True, 0.0), (False, 0.05), (True, 0.1), (False, 0.15),     # one press, chatter ignored
           (False, 3.0), (True, 3.1), (False, 3.2),                               # second press stops it
           (True, 5.0), (False, 5.1), (False, 20.0)]                              # third starts; 12 s later it stops itself
    it = iter(seq)

    def tick():
        try:
            state["down"], state["t"] = next(it)
        except StopIteration:
            ptt.stop_flag = True
    ptt.button = type("B", (), {"down": staticmethod(lambda: (tick(), state["down"])[1])})()
    ptt.run()
    import time as _t; _t.sleep(0.1)
    assert state["log"] == ["start", "stop", "start", "stop"] and len(got) == 2
    assert beeps == [True, False, True, False]


def test_pressing_the_button_cuts_the_engineer_off(tmp_path):
    from stintradio import voice
    played, stopped = [], []
    clip = tmp_path / "a.wav"
    clip.write_bytes(b"x")
    sp = voice.Speaker(tmp_path, play=played.append, stop=lambda: stopped.append(1))
    sp.say_file(clip, expires=30.0, gate=lambda secs=0: False)         # waiting for a straight
    ep = sp.epoch
    sp.interrupt()
    assert stopped == [1] and sp.items == [] and sp.epoch == ep + 1     # playing stopped, the queue emptied

    from stintradio.ptt import PushToTalk
    calls = []

    class Rec:
        def start(self): calls.append("rec")
        def stop(self): return None
    p = PushToTalk(object(), Rec(), lambda c: None, poll=0, on_start=lambda: calls.append("cut"))
    p._begin()
    assert calls == ["cut", "rec"]                                      # the engineer is cut off before recording starts


def test_engineer_can_look_things_up_before_answering():
    from types import SimpleNamespace as NS
    from stintradio import ask, tools
    mine = LP.Lap("t", "c", 20.0, "live", [[float(d), 100.0 + d / 100, 0.0, 100.0, float(d), 0.0, d / 30.0, 3, 6000, 0.1, 0.0, 5.0, 0.0, [80] * 4, 30.0]
                                          for d in range(0, 1000, 5)])
    ref = LP.Lap("t", "c", 19.0, "live", [[float(d), 102.0 + d / 100, 0.0, 100.0, float(d), 0.0, d / 31.0, 3, 6000, 0.1, 0.0, 5.0, 0.0, [80] * 4, 30.0]
                                         for d in range(0, 1000, 5)])
    svc = NS(coach=NS(ref=ref, corners=[]), recent_laps={4: mine}, lap_log=[(4, 20.0)], sample=None, engineer=None)
    text = tools.run(svc, {"tool": "lap_at", "lap": "last", "at_m": 500, "window_m": 50})
    assert "gear 3" in text and "Reference lap there" in text
    cmp_ = tools.run(svc, {"tool": "compare", "a": "last", "b": "best", "from_m": 0, "to_m": 900})
    assert "was +" in cmp_ and "took" in cmp_
    assert "no turn 9" in tools.run(svc, {"tool": "corner", "n": 9}).lower() or "There is no turn 9" in tools.run(svc, {"tool": "corner", "n": 9})
    assert tools.parse_request('{"tool": "laps"}') == {"tool": "laps"} and tools.parse_request('{"reply": "x", "job": null}') is None

    prompts, replies = [], iter(['{"tool": "lap_at", "lap": "last", "at_m": 500}', '{"reply": "Third gear there.", "job": null}'])

    class Client:
        def __init__(self): self.messages = self
        def create(self, model="", messages=()):
            prompts.append(messages[0]["content"])
            return NS(content=[NS(type="text", text=next(replies))])
    out = ask.answer("What gear was I in at 500 metres?", "facts", Client(), lookup=lambda r: tools.run(svc, r))
    assert out.reply == "Third gear there." and len(prompts) == 2 and "gear 3" in prompts[1] and "tool" in prompts[0].lower()


def test_voice_command_stops_and_starts_corner_coaching(tmp_path):
    from types import SimpleNamespace as NS
    from stintradio import ask
    assert ask.parse_command("Stop coaching") == "coaching_off"
    assert ask.parse_command("please pause the corner coaching for now") == "coaching_off"
    assert ask.parse_command("Start coaching again") == "coaching_on"
    assert ask.parse_command("coach me") == "coaching_on"
    assert ask.parse_command("stop coaching, then start coaching later") == "coaching_off"
    assert ask.parse_command("What position am I?") is None and ask.parse_command("how's my coaching going") is None

    d = tmp_path / "clip"
    d.mkdir()
    wav = d / "ask.wav"
    wav.write_bytes(b"x")
    out = ask.handle(wav, NS(), "k", client=None, post=lambda *a: b'{"text": "Stop the corner coaching please"}')
    assert out.command == "coaching_off" and out.reply == "Corner coaching off."          # no call to Claude at all

    from stintradio.engineer import Msg
    svc = LiveService(str(tmp_path))
    spoken = []
    svc.radio = NS(submit=lambda text, cb: spoken.append(text))
    svc.speaker = NS(epoch=0, interrupt=lambda: None)
    svc.coaching = False
    svc._radio(Msg("Turn three, fast right.", kind="guide"))
    svc._radio(Msg("Pressure behind, they're close.", kind="gap"))
    svc._radio(Msg("Corner coaching on.", kind="answer"))
    assert spoken == ["Pressure behind, they're close.", "Corner coaching on."]            # corner calls muted, the rest not


def test_recorder_writes_every_field_and_the_cars_to_sqlite(tmp_path):
    import sqlite3, struct
    from stintradio import r3e_reader as R
    from stintradio.recorder import Recorder, latest_session
    from stintradio.r3e_fields import FIELDS
    assert len(FIELDS) > 300 and ("throttle", 1500, "f") in FIELDS and len({n for n, _, _ in FIELDS}) == len(FIELDS)
    b = bytearray(_r3e_frame())
    struct.pack_into("<i", b, R.O["completed"], 2)
    rd = _r3e_reader(bytes(b))
    clock = {"t": 1000.0}
    rec = Recorder(hz=100.0, folder=tmp_path, clock=lambda: clock["t"])
    rec.offer(rd, bytes(b))
    clock["t"] += 1.5
    struct.pack_into("<i", b, R.O["completed"], 3)
    struct.pack_into("<f", b, R.O["lap_prev"], 91.5)
    rec.offer(rd, bytes(b))
    rec.close()
    db = sqlite3.connect(latest_session(tmp_path))
    assert db.execute("SELECT count(*) FROM frames").fetchone()[0] == 2
    row = db.execute("SELECT throttle_raw, fuel_left, lap_distance, completed_laps FROM frames ORDER BY t LIMIT 1").fetchone()
    assert row[0] == pytest.approx(0.8) and row[1] == pytest.approx(30.0) and row[2] == pytest.approx(1234.5) and row[3] == 2
    assert db.execute("SELECT count(*) FROM cars").fetchone()[0] == 4                   # 2 cars, at both seconds
    assert db.execute("SELECT lap, time FROM laps").fetchall() == [(3, pytest.approx(91.5))]
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    assert meta["track"] == "Zolder" and meta["car"] == "Test Car"
    db.close()
    import os
    for f in tmp_path.glob("*.sqlite*"):
        os.utime(f, (1000.0, 1000.0))
    assert Recorder(hz=10, folder=tmp_path, clock=lambda: 1000.0 + 90 * 86400).prune() is None
    assert list(tmp_path.glob("*.sqlite")) == []                                          # older than 60 days: pruned


def test_mcp_server_serves_the_recorded_stream_read_only(tmp_path, monkeypatch):
    import json as _json, struct
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from stintradio import mcp_server as M, r3e_reader as R
    from stintradio.recorder import Recorder
    b = _r3e_frame()
    rec = Recorder(hz=100.0)
    rec.offer(_r3e_reader(b), b)
    rec.close()

    def call(name, **args):
        r = M.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return r["result"]["content"][0]["text"], r["result"]["isError"]

    init = M.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert init["result"]["serverInfo"]["name"] == "stintradio" and init["result"]["protocolVersion"] == "2025-03-26"
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in M.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
    assert {"query", "live", "corners", "list_sessions", "lap_trace"} <= names
    text, err = call("query", sql="SELECT lap_distance, throttle_raw, fuel_left FROM frames")
    assert not err and _json.loads(text)["rows"] == [[1234.5, 0.8, 30.0]]
    assert "Only SELECT" in call("query", sql="DROP TABLE frames")[0]
    assert "error" in call("query", sql="SELECT 1; DELETE FROM frames")[0].lower() or "Only" in call("query", sql="SELECT 1; DELETE FROM frames")[0]
    still = call("query", sql="SELECT count(*) FROM frames")[0]
    assert _json.loads(still)["rows"] == [[1]]                                       # nothing was deleted
    live = _json.loads(call("live")[0])
    assert live["now"]["columns"][1] == "lap_distance" and len(live["cars"]["rows"]) == 2
    assert "Zolder" in call("list_sessions")[0]
    assert M.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}})["error"]["code"] == -32602


def test_mcp_ask_client_builds_a_locked_down_command(tmp_path):
    from stintradio import ask, ask_tools
    seen = {}

    def runner(cmd, text):
        seen["cmd"], seen["text"] = cmd, text
        return NS_(returncode=0, stdout='{"reply": "You were in third.", "job": null}', stderr="")
    from types import SimpleNamespace as NS_
    c = ask_tools.ClaudeMcpClient("claude.exe", runner=runner, config=tmp_path / "m.json")
    out = ask.answer("What gear?", "facts", c, native_tools=True)
    cmd = seen["cmd"]
    assert out.reply == "You were in third." and "stintradio tools" in seen["text"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd           # no file, shell or web tools
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert allowed.startswith("mcp__stintradio__") and "Bash" not in allowed and "Write" not in allowed
    cfg = ask_tools.mcp_config(tmp_path / "cfg.json")
    import json as _j
    assert _j.loads(cfg.read_text())["mcpServers"]["stintradio"]["args"] == ["-m", "stintradio.mcp_server"]


def test_radio_chatterbox_engine_uses_the_worker_and_never_falls_back(tmp_path, monkeypatch):
    import shutil, subprocess
    from stintradio import radio as R
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    monkeypatch.setattr(R, "data_dir", lambda: tmp_path)
    src = tmp_path / "speech.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=500:d=0.8", str(src)], check=True)

    class Fake:
        voice = None
        def __init__(self, fail=False):
            self.said, self.fail = [], fail
        def speak(self, text, out, exaggeration=0.5):
            if self.fail:
                raise RuntimeError("worker died")
            self.said.append(text)
            out.write_bytes(src.read_bytes())
    ok = Fake()
    r = R.Radio("chatterbox", None, chatterbox=ok)
    p = r.make("Box this lap.")
    assert p and p.is_file() and ok.said == ["Box this lap."]
    bad = R.Radio("chatterbox", None, chatterbox=Fake(fail=True))
    assert bad.make("Gap behind.") is None and bad.errors == ["worker died"]


def test_race_view_has_what_a_second_screen_needs():
    from collections import deque
    from stintradio.service import LiveService
    from stintradio.reader import Sample
    svc = LiveService("")
    svc.sample = Sample(10.0, 3, 0, 500.0, 180.0, 100, 0, 0, 4, 6000, 0, 0, 0.0, False, "Zandvoort", "GT3", 4000.0, 101.2, 100.9,
                        tyre_temp=(88.0, 90.0, 85.0, 86.0), fuel=31.5)
    svc.status = "driving"
    car = lambda place, name, me=False: {"id": place, "name": name, "cls": "DTM", "place": place, "place_class": place, "me": me,
                                         "dist": 0, "laps": 2, "in_pits": False, "gap_front": 0, "gap_behind": 0, "finish": 0, "speed": 0}
    svc.snap = {"session": 10, "t": 100.0, "end_t": 900.0, "max_laps": 1_000_000, "phase": 0, "yellow_state": 0,
                "sector_flags": [0, 1, 0], "raining": 0.0, "wetness": 0.0, "track_len": 4000.0,
                "me": {**car(2, "Alex Ford", True), "penalties": 1, "flag": 6, "in_pits": False, "laps": 2},
                "cars": [car(1, "Kimi Xu"), car(2, "Alex Ford", True), car(3, "Ben Wang")], "fuel": 31.5, "fuel_cap": 80.0,
                "wear": [0.1, 0.1, 0.2, 0.2], "impact_t": 0.0, "impact_mag": 0.0, "lap": 3, "tyre": ""}
    svc.gaps = (2, 1.8, 4.0)
    svc.gap_hist = deque([(0.0, 3.0, 4.0), (10.0, 1.8, 4.0)])
    v = svc.race_view()
    assert (v["place"], v["field"], v["ahead"], v["behind"]) == (2, 3, "Kimi Xu", "Ben Wang")
    assert v["gap_ahead"] == 1.8 and v["trend_ahead"] == -1.2 and v["trend_behind"] == 0.0     # closing on the car ahead
    assert v["blue"] and v["yellow"] and v["penalties"] == 1 and v["time_left"] == 800
    assert v["tyre_temp"] == [88, 90, 85, 86] and v["wear"] == [10, 10, 20, 20] and v["fuel"] == 31.5


def _ring(n: int = 500, length: float = 2000.0, top: float = 1.0) -> list:
    """Samples of a circular lap (counter-clockwise from x = r), or the first `top` of it."""
    r = length / (2 * math.pi)
    return [[k / n * length, 200.0, 0.0, 100.0, r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n), k * 0.1]
            for k in range(int(n * top))]


def test_track_outline_is_even_in_lap_distance_and_keeps_left_turns_left():
    from stintradio import trackmap as TM
    line = TM.outline(_ring(), 2000.0, n=100)
    assert line["closed"] and (line["from"], line["to"]) == (0.0, 2000.0) and len(line["pts"]) == 100
    r = 2000.0 / (2 * math.pi)
    assert line["pts"][25][0] == pytest.approx(0.0, abs=0.5) and line["pts"][25][1] == pytest.approx(r, abs=0.5)    # a quarter lap on
    flipped = TM.outline(_ring(), 2000.0, n=100, turn_sign=-1)
    assert flipped["pts"][10][0] == -line["pts"][10][0] and flipped["pts"][10][1] == line["pts"][10][1]
    part = TM.outline(_ring(top=0.3), 2000.0, n=50)                    # part of a lap: an open line, as far as it has got
    assert not part["closed"] and part["from"] == 0.0 and part["to"] == pytest.approx(600, abs=5)


def test_track_outline_has_nothing_to_draw_without_real_positions():
    from stintradio import trackmap as TM
    assert TM.outline([[k, 200, 0, 100, None, None, k * 0.1] for k in range(100)], 2000.0) is None     # Coach Dave
    assert TM.outline([[k, 200, 0, 100, 0.0, 0.0, k * 0.1] for k in range(100)], 2000.0) is None       # a replay of one
    assert TM.outline(_ring()[:5], 2000.0) is None
    wobble = _ring()                                                   # lap distance stepping back is skipped, not drawn
    wobble[100][0] = wobble[90][0]
    assert TM.outline(wobble, 2000.0)["closed"]


def test_map_cars_are_everyone_else_with_the_two_around_you_marked():
    from stintradio import trackmap as TM
    car = lambda place, name, dist=100.0, me=False, pits=False: {"name": name, "place": place, "me": me, "dist": dist, "in_pits": pits}
    snap = {"cars": [car(3, "Ben Wang #7", 300.0), car(1, "Kimi Xu", 900.0), car(2, "Me", 500.0, True), car(4, "Al Roy", 50.0, pits=True),
                     car(5, "No Reading", -1.0)]}
    got = TM.around(snap)
    assert [(c["n"], c["side"], c["pit"]) for c in got] == [("Kimi Xu", -1, False), ("Ben Wang", 1, False), ("Al Roy", 0, True)]
    assert TM.around(None) == []


def test_track_map_learns_from_the_lap_then_draws_the_reference_with_its_braking_zones(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    svc = LiveService("")
    assert svc.track_map() == {}                                       # nothing yet
    ref, slow = _lap(480, 60.0), _lap(420, 61.0)
    half = LP.Lap(ref.track, ref.car, 0.0, "live", ref.samples[:500])
    _feed(svc, half, 2000.0, 1, 0.0)
    m = svc.track_map()
    assert m["source"] == "learning" and not m["closed"] and m["to"] == pytest.approx(1000, abs=5) and m["zones"] == []
    assert svc.race_view()["map"]["id"] == m["id"] and svc.race_view()["map"]["dist"] == pytest.approx(998, abs=2)
    svc = LiveService("")
    b2 = ref.samples[-1][6] + 0.02
    _feed(svc, ref, 2000.0, 1, 0.0)
    _feed(svc, slow, 2000.0, 2, b2)                                    # the first lap is now the reference
    m = svc.track_map()
    assert m["source"] == "reference" and m["closed"] and len(m["pts"]) == 400
    assert [(round(z["from"]), round(z["to"])) for z in m["zones"]] == [(480, 600), (1380, 1500)]
    assert svc.race_view()["map"]["id"] == m["id"] and svc.track_map() is svc.track_map()   # not remade each poll


def test_track_map_uses_your_own_lap_when_the_reference_has_no_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(LP, "data_dir", lambda: tmp_path)
    own = _lap(480, 70.0)
    LP.save(own)                                                       # a slower lap of your own, stored earlier
    cd = _lap(480, 50.0)
    LP.save(LP.Lap(cd.track, cd.car, 50.0, "coachdave", [p[:4] + [None, None] + p[6:] for p in cd.samples]))
    svc = LiveService("")
    _feed(svc, LP.Lap(own.track, own.car, 0.0, "live", own.samples[:100]), 2000.0, 1, 0.0)
    assert svc.ref_info["source"] == "coachdave"
    m = svc.track_map()
    assert m["source"] == "lap" and m["closed"] and [round(z["from"]) for z in m["zones"]] == [480, 1380]   # zones from Coach Dave's braking


def test_other_devices_may_only_read_the_race_page():
    from fastapi.testclient import TestClient
    from stintradio.config import Settings
    from stintradio.server import create_app
    app = create_app(Settings())
    lan = TestClient(app, client=("10.0.0.5", 50000))
    assert lan.get("/api/race").status_code == 200 and lan.get("/race").status_code == 200
    assert lan.get("/api/track").json() == {}
    assert lan.get("/api/state").status_code == 403 and lan.get("/api/settings").status_code == 403
    assert lan.post("/api/stop").status_code == 403 and lan.put("/api/settings", json={}).status_code == 403
    assert lan.post("/race").status_code == 403
    pc = TestClient(app)
    assert pc.get("/api/state").status_code == 200


def test_engineer_praises_a_nailed_corner_once_and_quietens_after_settling_in():
    from stintradio.engineer import Engineer
    e = Engineer(seed=1, settle=2)
    good = _note(3, 5, -0.12, brake_diff=2.0, apex_off=0.4)
    m = e.nailed(good, 100.0)
    assert m is not None and m.kind == "praise" and "turn five" in m.text.lower() and "spot on" in m.text.lower()
    assert e.nailed(_note(3, 6, -0.2), 101.0) is None                      # one per lap
    assert e.nailed(_note(4, 5, -0.2), 140.0) is None                      # not the same corner again so soon
    assert e.nailed(_note(4, 6, 0.2), 140.0) is None                       # lost time: not a nailed corner
    assert e.nailed(_note(4, 7, 0.0, brake_diff=-18), 140.0) is None       # a known habit showing
    # past the settle-in laps a steady lap time and a small loss are left unsaid
    for lap in (1, 2):
        e.lap_done(104.0, 103.0, "leader", None, [_note(lap, 2, 0.1)])
    e.lap_done(104.1, 103.0, "leader", None, [_note(3, 2, 0.1)])        # every third lap is still said
    quiet = e.lap_done(104.0, 103.0, "leader", None, [_note(4, 2, 0.1)])
    assert not [m for m in quiet if m.kind in ("lap", "debrief")]
    news = e.lap_done(102.0, 103.0, "leader", None, [_note(5, 2, 0.1)])      # quicker than the reference: said
    assert any(m.kind == "lap" for m in news)


def test_race_lap_call_measures_against_the_fastest_lap_of_the_race():
    from stintradio.engineer import Engineer
    e = Engineer(seed=1)
    off = e.lap_done(95.0, 93.5, "fastest", None, [])
    assert "the fastest lap" in off[0].text and "off" in off[0].text.lower() or "the fastest lap" in off[0].text
    top = e.lap_done(93.5, 93.5, "fastest", None, [])
    assert "fastest lap" in top[0].text.lower() and ("race" in top[0].text.lower() or "set" in top[0].text.lower())


def _tips_db(tmp_path, front_lock: bool):
    import sqlite3
    cols = ("t REAL, completed_laps INT, lap_distance REAL, layout_length REAL, lap_time_current_self REAL, car_speed REAL, "
            "engine_rps REAL, max_engine_rps REAL, upshift_rps REAL, gear INT, throttle_raw REAL, brake_raw REAL, "
            "steer_input_raw REAL, in_pitlane INT, tire_rps_0 REAL, tire_rps_1 REAL, tire_rps_2 REAL, tire_rps_3 REAL, abs_setting INT")
    tmp_path.mkdir(exist_ok=True)
    p = tmp_path / "s.sqlite"
    db = sqlite3.connect(p)
    db.execute(f"CREATE TABLE frames ({cols})")
    t = 0.0
    for lap in range(5):
        for k in range(1000):                              # 1000 m lap at 10 frames a second = 100 s
            d = float(k)
            braking = 40 <= k % 100 < 60
            v = 40.0 - (25.0 if 430 <= k < 450 else 0.0)        # a slow corner at 430-450 m
            w = v / 0.33
            fl = w * (0.7 if (braking and front_lock) else 1.0)
            gear = 3 if k % 100 < 50 else 4
            db.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (t, lap, d, 1000.0, t, v, 600.0, 700.0, 650.0, gear, 0.4 if not braking else 0.0,
                        0.8 if braking else 0.0, 0.0, 0, -fl, -fl, -w, -w, 0))
            t += 0.1
    db.commit()
    db.close()
    return p


def test_tips_flag_front_lockups_and_find_the_slow_corner(tmp_path):
    from stintradio import tips
    r = tips.analyse(_tips_db(tmp_path / "a", True))
    assert r["brake_bias"]["front"] >= 4 and "towards the rear" in r["lines"][0]
    assert r["min_speeds"] and r["min_speeds"][0]["best"] == 54 and 400 <= r["min_speeds"][0]["at_m"] <= 460
    clean = tips.analyse(_tips_db(tmp_path / "b", False))
    assert clean["brake_bias"]["front"] == 0 and "looks fine" in clean["lines"][0]


def test_engineer_uses_the_other_drivers_first_names():
    from stintradio.engineer import Engineer, first_name
    assert first_name("Ian Atkinson") == "Ian" and first_name("IAN ATKINSON") == "Ian" and first_name("speedy#42") == "speedy"
    e = Engineer(seed=1)
    out = e.race(0.0, 8, 1.8, 0.4, 0.3, "Ian Atkinson", "Martin Dwyer")
    assert any("Martin" in m.text for m in out if m.kind == "gap")
    assert any("Ian one point eight ahead" in m.text and "Martin" in m.text for m in out if m.kind == "race")


def test_battle_finds_where_the_car_ahead_takes_time():
    from stintradio.battle import Battle, describe, SEGMENTS
    L = 3000.0

    def pos(t, secs_per_seg):                         # lap distance after t seconds, given the time each segment takes
        lap = sum(secs_per_seg)
        laps, r = divmod(t, lap)
        d = 0.0
        for s in secs_per_seg:
            if r <= s:
                return int(laps), d + r / s * (L / SEGMENTS)
            r -= s
            d += L / SEGMENTS
        return int(laps), L

    mine = [8.0] * SEGMENTS
    ahead = [8.0] * SEGMENTS
    ahead[4] = 7.8                                    # the car ahead is 0.2 s quicker through segment 4 only
    b = Battle()
    for sec in range(1, 400):
        t = float(sec)
        a_laps, a_d = pos(t + 3.0, ahead)             # it starts a few seconds up the road
        b.update(t, pos(t, mine), (a_laps, a_d), "Ian Atkinson", L)
    sm = b.summary()
    assert sm and sm["worst"] == 4 and 0.1 < sm["loss"] < 0.3 and sm["name"] == "Ian Atkinson"
    corners = [type("C", (), {"apex": 1100.0, "name": "corner five"})()]
    assert describe(4, L, corners) == "turn five"
    from stintradio.engineer import Engineer
    m = Engineer(seed=1).battle(sm["name"], "turn five", sm["loss"], "turn two", sm["gain"], sm["net"])
    assert "Ian" in m.text and "turn five" in m.text.lower()


def _exp_frames(laps=8, lock_until=3, quicker_from=4):
    frames, t = [], 0.0
    for lap in range(laps):
        n = 1000 if lap < quicker_from else 997                     # three tenths quicker once the change is made
        for k in range(n):
            braking = 40 <= k % 100 < 60
            w = 40.0 / 0.33
            locked = braking and lap < lock_until
            frames.append({"t": t, "completed_laps": lap, "lap_distance": k * 3.0, "layout_length": 3000.0, "car_speed": 40.0,
                           "throttle_raw": 0.4 if not braking else 0.0, "brake_raw": 0.8 if braking else 0.0,
                           "steer_input_raw": 0.0, "in_pitlane": 0, "tire_rps_0": -w * (0.7 if locked else 1.0),
                           "tire_rps_1": -w * (0.7 if locked else 1.0), "tire_rps_2": -w, "tire_rps_3": -w, "gear": 3})
            t += 0.1
    return frames


def test_setup_watcher_waits_for_a_change_to_settle():
    from stintradio.experiments import Watcher
    w = Watcher()
    assert w.update({"brake_bias": 0.55, "abs": -1}, 0.0) == []
    assert w.update({"brake_bias": 0.56, "abs": -1}, 1.0) == []         # a click: not settled yet
    assert w.update({"brake_bias": 0.57, "abs": -1}, 2.0) == []         # another click restarts the wait
    assert w.update({"brake_bias": 0.57, "abs": -1}, 4.5) == [("brake_bias", 0.55, 0.57)]
    assert w.update({"brake_bias": 0.57, "abs": -1}, 9.0) == []


def test_experiment_compares_laps_before_and_after_a_change():
    from stintradio import experiments as EX
    frames = _exp_frames()
    exp = EX.Experiment("brake_bias", 0.55, 0.57, 3)                    # changed during the lap that began with 3 completed
    assert not exp.ready(5) and exp.ready(6)
    r = EX.evaluate(frames, exp)
    assert r["ok"] and r["d_avg"] < -0.2 and r["front_before"] > 3 and r["front_after"] == 0 and r["locks_known"]
    assert EX.verdict(r) == "keep"
    from stintradio.engineer import Engineer
    m = Engineer(seed=1).experiment_result(exp.label, r, "keep")
    assert "brake bias" in m.text and "quicker" in m.text and "Keep it" in m.text
    r2 = dict(r, d_avg=0.4, front_after=r["front_before"] + 3)
    assert EX.verdict(r2) == "revert"
    assert EX.evaluate(_exp_frames(laps=4), EX.Experiment("abs", 1, 2, 3))["ok"] is False


def test_engineer_proposes_and_gives_shift_advice_in_words():
    from stintradio.engineer import Engineer, rpm_say
    e = Engineer(seed=1)
    assert "towards the rear" in e.propose("rear").text and "front wheels are locking" in e.propose("rear").text
    assert rpm_say(7000) == "seven thousand" and rpm_say(6940) == "sixty-nine hundred"
    s = e.shift_advice(3, 6940, 7280)
    assert "third" in s.text and ("sooner" in s.text or "go up at" in s.text) and "seventy-three hundred" in s.text


def test_pit_strategy_facts_cover_fuel_tyres_and_the_window():
    from types import SimpleNamespace
    from stintradio import ask
    snap = {"me": {"laps": 6}, "stops": 0, "pit_window": (2, 20, 40), "end_t": 3600.0, "t": 600.0, "pit_min_s": 30.0,
            "last_stop_s": -1.0, "game_fuel_per_lap": 2.5, "fuel": 20.0, "fuel_cap": 100.0, "wear": [0.9, 0.91, 0.93, 0.92]}
    watch = SimpleNamespace(laps_to_go=lambda s: 20.0, per_lap=lambda: 2.5)
    svc = SimpleNamespace(worn_pct=lambda s: [10.0, 10.0, 8.0, 8.0], wear_rate=lambda: 1.2)
    text = ask.pit_facts(svc, snap, watch)
    assert "window from 20 to 40 minutes" in text and "8.0 laps of fuel" in text and "not enough" in text
    assert "1.2 percent a lap" in text and "33 percent worn at the flag" in text and "Not known" in text
    assert ask.pit_facts(svc, None, watch) == ""


def test_sector_deltas_show_up_or_down_per_sector():
    ref = _lap(480, 60.0)                                  # 2000 m in 60 s
    svc = LiveService("")
    svc.coach = C.Coach(ref, "Test Ring", 2000.0)
    svc.sector_bounds["Test Ring"] = [700.0, 1400.0]
    # a lap that is 0.3 s quicker in sector 1, level in sector 2, 0.5 s slower in sector 3 (finishes in 60.2 s)
    def at(d):
        base = svc.coach.ref_elapsed(d)
        return base - 0.3 if d < 700 else (base - 0.3 if d < 1400 else base - 0.3 + 0.5 * (d - 1400) / 600)
    samples = [[d, 100, 0, 100, 0, 0, at(d)] for d in range(0, 2000, 5)]
    part = svc.sector_deltas([s for s in samples if s[0] <= 1000], "Test Ring")
    assert part[0] == pytest.approx(-0.3, abs=0.05) and part[1] is None and part[2] is None
    full = svc.sector_deltas(samples, "Test Ring", final=60.2)
    assert full[0] == pytest.approx(-0.3, abs=0.05) and abs(full[1]) < 0.05 and full[2] == pytest.approx(0.5, abs=0.05)
    assert svc.sector_deltas(samples, "Unknown Ring") == []          # bounds not learnt yet: no claim


def test_sector_bounds_are_learnt_from_a_lap_with_sector_times():
    svc = LiveService("")
    lap = _lap(480, 60.0)
    end = lap.samples[-1][6]
    lap.sectors = [end / 3, end * 2 / 3, end]
    svc._learn_sectors("Test Ring", lap)
    b = svc.sector_bounds["Test Ring"]
    assert len(b) == 2 and 0 < b[0] < b[1] < 2000


def test_speaker_logs_each_sound_it_plays(tmp_path, monkeypatch):
    import time as _t
    from stintradio import voice as V
    monkeypatch.setenv("APPDATA", str(tmp_path))
    clip = tmp_path / "brake.wav"
    clip.write_bytes(b"x")
    played = []
    sp = V.Speaker(tmp_path, play=played.append)
    sp.say("brake")
    for _ in range(100):
        if played:
            break
        _t.sleep(0.02)
    _t.sleep(0.1)
    log = (tmp_path / "StintRadio" / "audio.log").read_text(encoding="utf-8")
    assert "play brake" in log and "done brake" in log


def test_overlay_window_placement_follows_the_game_window_corner():
    from stintradio.overlay_window import placement, parse_size
    game = (100, 50, 2100, 1250)
    assert placement(game, (500, 400), "top-right", 20) == (1580, 70)
    assert placement(game, (500, 400), "top-left", 20) == (120, 70)
    assert placement(game, (500, 400), "bottom-right", 20) == (1580, 830)
    assert placement(game, (500, 400), "bottom-left", 20) == (120, 830)
    assert parse_size("520x380") == (520, 380)


def test_overlay_and_map_script_are_served_to_this_pc_only(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from fastapi.testclient import TestClient
    from stintradio.config import Settings
    from stintradio.server import create_app
    c = TestClient(create_app(Settings()))
    assert c.get("/overlay").status_code == 200 and "trackmap.js" in c.get("/overlay").text
    assert c.get("/trackmap.js").status_code == 200


def test_overlay_layout_snaps_to_the_nearest_corner_and_round_trips():
    from stintradio.overlay_window import geom_for, rect_for
    game = (100, 50, 2100, 1250)
    g = geom_for(1500, 80, 500, 400, game)                      # dropped near the top right
    assert g["anchor"] == "top-right" and g["dx"] == 100 and g["dy"] == 30
    assert rect_for(g, game) == (1500, 80, 500, 400)
    g = geom_for(150, 800, 500, 400, game)                      # near the bottom left
    assert g["anchor"] == "bottom-left" and rect_for(g, game) == (150, 800, 500, 400)
    moved = (0, 0, 1920, 1080)                                  # the game window moved: the overlay keeps its corner offsets
    assert rect_for(g, moved)[0] == 50 and rect_for(g, moved)[1] == 1080 - 400 - 50


def test_overlay_sizes_are_kept_in_range_and_shapes_keep_their_area():
    from stintradio.overlay_window import clamp_size, scaled, next_shape, SHAPES
    assert clamp_size(10, 10) == (240, 160) and clamp_size(9999, 9999) == (1800, 1100)
    g = {"anchor": "top-right", "w": 760, "h": 360, "dx": 24, "dy": 24}
    assert scaled(g, 1.1)["w"] == 836
    n = next_shape(g)
    assert abs(n["w"] / n["h"] - 1.0) < 0.05                    # wide -> square
    assert abs(n["w"] * n["h"] - 760 * 360) / (760 * 360) < 0.05
    seen = {round(g["w"] / g["h"], 1)}
    for _ in range(len(SHAPES)):
        g = next_shape(g)
        seen.add(round(g["w"] / g["h"], 1))
    assert len(seen) >= 4                                       # the cycle visits every shape


def test_overlay_state_is_kept_and_saved_by_the_server(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from fastapi.testclient import TestClient
    from stintradio.config import Settings
    from stintradio.server import create_app
    c = TestClient(create_app(Settings()))
    assert c.get("/api/overlay").json() == {"edit": False, "brand": True, "border": False, "theme": "dark", "geom": None, "opacity": None, "quit": False, "alive": False}
    geom = {"anchor": "bottom-left", "w": 600, "h": 400, "dx": 30, "dy": 40}
    assert c.put("/api/overlay", json={"edit": True, "brand": False, "border": True, "theme": "dark", "geom": geom}).json()["geom"] == geom
    c.put("/api/overlay", json={"geom": {"anchor": "nowhere", "w": 1, "h": 1, "dx": 0, "dy": 0}})   # nonsense is ignored
    assert c.put("/api/overlay", json={"opacity": 5}).json()["opacity"] == 1.0         # kept between 0.3 and 1
    assert c.put("/api/overlay", json={"opacity": 0.1}).json()["opacity"] == 0.3
    assert c.put("/api/overlay", json={"opacity": "x"}).json()["opacity"] == 0.3        # nonsense is ignored
    assert c.put("/api/overlay", json={"opacity": 0.8}).json()["opacity"] == 0.8
    assert c.get("/api/overlay").json()["geom"] == geom
    again = TestClient(create_app(Settings()))                  # a new start: the layout and border come back, edit mode does not
    assert again.get("/api/overlay").json() == {"edit": False, "brand": False, "border": True, "theme": "dark", "geom": geom, "opacity": 0.8, "quit": False, "alive": False}


def test_overlay_heartbeat_quit_and_app_quit(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from fastapi.testclient import TestClient
    from stintradio.config import Settings
    from stintradio.server import create_app
    app = create_app(Settings())
    exited = []
    app.state.exit = lambda: exited.append(1)
    c = TestClient(app)
    assert c.get("/api/overlay").json()["alive"] is False
    c.put("/api/overlay", json={"ping": True})
    assert c.get("/api/overlay").json()["alive"] is True          # the overlay window said it is there
    assert c.put("/api/overlay", json={"quit": True}).json()["quit"] is True
    assert c.post("/api/quit").json() == {"quitting": True}
    import time as _t
    for _ in range(40):
        if exited:
            break
        _t.sleep(0.05)
    assert exited


def test_gui_settings_form_merges_without_touching_other_settings():
    from stintradio import gui
    base = {"live": {"talk": "normal", "coach_laps": 5, "ptt": False, "driver_name": "", "voice_boost_db": 18.0, "other": "kept"},
            "voice": {"elevenlabs_voice_id": "abc"}, "port": 6420}
    out = gui.merge_form(base, {("live", "talk"): "chatty", ("live", "coach_laps"): "8", ("live", "ptt"): True,
                                ("live", "driver_name"): "  Alex ", ("live", "voice_boost_db"): "12.5"})
    assert out["live"]["talk"] == "chatty" and out["live"]["coach_laps"] == 8 and out["live"]["ptt"] is True
    assert out["live"]["driver_name"] == "Alex" and out["live"]["voice_boost_db"] == 12.5
    assert out["live"]["other"] == "kept" and out["voice"] == {"elevenlabs_voice_id": "abc"} and out["port"] == 6420
    assert base["live"]["talk"] == "normal"                       # the input is not changed
    from stintradio.config import Settings
    Settings.model_validate(out)


def test_gui_commands_and_log_tail(tmp_path):
    from stintradio import gui
    assert gui.app_command(False, "py.exe") == ["py.exe", "-m", "stintradio"]
    assert gui.app_command(True, "StintRadio.exe") == ["StintRadio.exe"]
    assert gui.overlay_command("lmu", False, "py.exe") == ["py.exe", "-m", "stintradio", "overlay", "--game", "lmu"]
    assert gui.overlay_command("raceroom", True, "S.exe") == ["StintRadio.exe", "overlay", "--game", "raceroom"]
    f = tmp_path / "x.log"
    f.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
    t = gui.tail(f, 3)
    assert t.splitlines() == ["line 497", "line 498", "line 499"]
    assert gui.tail(tmp_path / "missing.log") == "(no file yet)"


def test_overlay_windows_take_over_a_layout_set_from_the_control_panel():
    from stintradio.overlay_native import Layout
    from stintradio.overlay_window import clean_geom
    default = {"anchor": "top-right", "w": 640, "h": 420, "dx": 24, "dy": 24}
    lay = Layout(dict(default), default)
    new = {"anchor": "bottom-left", "w": 500, "h": 500, "dx": 10, "dy": 20}
    assert lay.adopt(new, now=100.0) and lay.geom == new
    assert not lay.adopt(new, now=101.0)                                     # nothing new
    lay.key("bigger")                                                        # a hotkey: ignore the (older) server layout for a moment
    mine = dict(lay.geom)
    assert not lay.adopt(new, now=lay.quiet_until - 1) and lay.geom == mine
    assert lay.adopt(new, now=lay.quiet_until + 1) and lay.geom == new       # later it is taken over again
    lay.editing = True
    assert not lay.adopt(default, now=lay.quiet_until + 9)                   # never while the user drags it
    assert clean_geom({"anchor": "nowhere", "w": 1, "h": 1, "dx": 0, "dy": 0}) is None
    assert clean_geom({"anchor": "top-left", "w": 99999, "h": 5, "dx": -4, "dy": "7"}) == {"anchor": "top-left", "w": 1800, "h": 160, "dx": 0, "dy": 7}
