import json
import subprocess
import sys
from pathlib import Path

import pytest

from driverdirector import compose, gameui, music
from driverdirector import history as hist
from driverdirector import sectors as S
from driverdirector.config import MusicSettings, Settings
from driverdirector.lmu_api import LMUApiError
from driverdirector.models import Car, Frame
from driverdirector.overlay import build_state
from tests.test_sectors import mini as _mini

FX = Path(__file__).parent / "fixtures"


def mini():
    h = _mini()
    h.t_end = {s: {l.n: l.et for l in ls} for s, ls in h.laps.items()}      # finish-line times: progress points
    return h


def race(n=14):
    """n cars in order 1..n, each 1 s behind the one in front on a 4 km lap."""
    cars = [Car(i, f"D{i}", str(i), "GT3", place=i, class_place=i, laps=2, lap_dist=3000.0 - 50 * i) for i in range(1, n + 1)]
    return Frame(t=500.0, track_len=4000.0, cars=cars)


def test_tower_is_centred_on_the_focus_driver_five_ahead_five_behind():
    st = build_state(race(), 8)
    assert [r["pos"] for r in st["tower"]] == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    assert [r["focus"] for r in st["tower"]].index(True) == 5


def test_tower_window_slides_at_the_front_and_back_and_keeps_its_size():
    assert [r["pos"] for r in build_state(race(), 1)["tower"]] == list(range(1, 12))
    assert [r["pos"] for r in build_state(race(), 14)["tower"]] == list(range(4, 15))
    assert len(build_state(race(5), 3)["tower"]) == 5                 # fewer cars than rows


def test_tower_rows_carry_sector_colours_and_gaps():
    h = mini()
    laps = S.build(h)
    fr = Frame(t=150.0, track_len=4000.0, cars=[Car(1, "A", "1", "GT3", place=1, class_place=1, laps=1, lap_dist=2000.0),
                                                  Car(2, "B", "2", "GT3", place=2, class_place=2, laps=1, lap_dist=1900.0)])
    tl = hist.Timeline(h)
    st = build_state(fr, 1, timeline=tl, sector_index=S.Index(laps))
    a, b = st["tower"]
    assert a["focus"] and a["gap"] == ""
    assert b["gap"].startswith("+")                                    # B is behind A
    assert len(a["bars"]) == 3 and all(x is None or x["color"] in ("purple", "green", "orange") for x in a["bars"])


def test_sector_bars_fill_as_sectors_are_crossed_and_keep_the_previous_lap_until_then():
    idx = S.Index(S.build(mini()))
    early = idx.bars(1, 150.0 - 40)         # lap 2 (starts t=100): 10 s in, no sector crossed yet
    assert all(c is None or c["old"] for c in early)                   # last lap's colours stay, marked old
    mid = idx.bars(1, 100 + 30)             # S1 (24 s) crossed, S2 not
    assert mid[0] and not mid[0]["old"] and mid[1]["old"]
    done = idx.bars(1, 200 + 1)             # just finished lap 2: shown whole and fresh
    assert all(c and not c["old"] for c in done)


def test_gap_is_the_time_the_other_car_passes_the_same_point():
    tl = hist.Timeline(mini())
    me, other = 1.5, 1.5                    # same point: no gap
    assert tl.gap(1, me, 2, other, 140.0) == pytest.approx(tl.time_at(2, 1.5) - 140.0)
    assert isinstance(tl.gap(1, 1.0, 2, 4.5, 140.0), int)              # a lap or more apart: whole laps
    assert tl.time_at(1, 999.0) is None


def test_speedo_toggle_only_when_needed():
    calls = []

    class L:
        on = False

        def _call(self, m, path, *a, **k):
            if m == "GET":
                return {"speedo": self.on, "chat": False}
            calls.append(path)
            self.on = not self.on

    l = L()
    assert gameui.set_speedo(l, True) and calls == ["hud/toggle/speedo"]
    assert gameui.set_speedo(l, True) and len(calls) == 1             # already on: no second toggle
    assert gameui.set_speedo(l, False) and len(calls) == 2


def test_speedo_reports_failure():
    class L:
        def _call(self, m, path, *a, **k):
            if m == "GET":
                return {"speedo": False}
            raise LMUApiError("nope")

    assert gameui.set_speedo(L(), True) is False


def test_music_cache_key_changes_with_prompt():
    a, b = MusicSettings(), MusicSettings(prompt="calm piano")
    assert music.cache_key(a) != music.cache_key(b) and music.cache_key(a) == music.cache_key(MusicSettings())


def test_music_worker_compiles():
    subprocess.run([sys.executable, "-m", "py_compile", str(Path(music.__file__).with_name("music_worker.py"))], check=True)


def test_music_bed_loops_ducks_and_fades(tmp_path):
    ff = compose.ffmpeg_bin()
    loop = tmp_path / "loop.wav"
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-ar", "32000", "-ac", "1", str(loop)],
                   check=True, capture_output=True)
    out = tmp_path / "bed.wav"
    music.build_bed(str(loop), 10.0, [(4.0, 6.0)], MusicSettings(level_db=-30, duck_db=12), str(out))
    assert abs(compose.probe_duration(str(out)) - 10.0) < 0.1

    def level(a, b):
        r = subprocess.run([ff, "-hide_banner", "-ss", str(a), "-t", str(b - a), "-i", str(out), "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True)
        return float(r.stderr.split("mean_volume:")[1].split()[0])

    assert level(4.5, 5.5) < level(7.0, 8.0) - 6                       # ducked while the commentator speaks
    assert level(0.0, 0.3) < level(2.5, 3.5)                           # fades in


def test_music_folder_loop(tmp_path):
    ff = compose.ffmpeg_bin()
    d = tmp_path / "tracks"
    d.mkdir()
    for n in ("a", "b"):
        subprocess.run([ff, "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=5", str(d / f"{n}.wav")], check=True,
                       capture_output=True)
    p = music.folder_loop(str(d))
    assert p and p.is_file() and 6.5 < compose.probe_duration(str(p)) < 10.5     # two 5 s tracks, cross-faded
    assert music.folder_loop(str(tmp_path / "empty")) is None


def test_settings_roundtrip_has_music_and_telemetry():
    s = Settings()
    assert s.music.source == "ai" and s.direct.telemetry and (s.direct.tower_ahead, s.direct.tower_behind) == (5, 5)
    assert Settings.model_validate_json(s.model_dump_json()).music.clips == s.music.clips
