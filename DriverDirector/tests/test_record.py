import json
import shutil
import subprocess
from pathlib import Path

import pytest

from driverdirector import history as H, results
from driverdirector.camera import DryRunGame
from driverdirector.compose import probe_duration
from driverdirector.config import DirectSettings, Settings
from driverdirector.director import Director
from driverdirector.lmu_api import DemoLMU

FIX = Path(__file__).parent / "fixtures"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")


class FakeClock:
    def __init__(self): self.t = 1000.0
    def now(self): return self.t
    def sleep(self, d): self.t += d


class FakeObs:
    """Stands in for OBS: 'records' for as long as the fake clock ran, writing a real video."""
    def __init__(self, clock, tmp):
        self.clock, self.tmp, self.log, self.n = clock, tmp, [], 0
        self.started = None
    def connect(self): pass
    def refresh_overlay(self, name="x"): self.log.append(("refresh",))
    def set_scene(self, name): self.log.append(("scene", name))
    def start_record(self, directory=None):
        self.started = self.clock.now(); self.log.append(("start", self.started))

    # live compositing: every call is stamped with the fake wall clock
    def setup_pip(self, scene, files, size, corner, **kw):
        self.log.append(("setup_pip", list(files))); return [{"i": i} for i, _ in enumerate(files)]
    def pip_show(self, h, cursor_ms=0): self.log.append(("pip_show", h["i"], cursor_ms, self.clock.now()))
    def pip_hide(self, h): self.log.append(("pip_hide", h["i"], self.clock.now()))
    def pip_cleanup(self, handles): self.log.append(("pip_cleanup",))

    def stop_record(self):
        dur = max(1.0, self.clock.now() - self.started)
        self.n += 1
        out = self.tmp / f"rec{self.n}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=10:d={dur:.2f}",
                        "-f", "lavfi", "-i", f"sine=f=220:d={dur:.2f}", "-c:v", "libx264", "-c:a", "aac", str(out)], check=True)
        return str(out)
    def status(self): return {"connected": True, "recording": False}


class TracingGame(DryRunGame):
    def __init__(self, clock): super().__init__(); self.clock = clock
    def play(self): super().play(); self.clock.t += 0.25        # the play call takes a moment


def make_director(tmp_path, monkeypatch, mode="ffmpeg"):
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    clock = FakeClock()
    s = Settings()
    s.compose_mode = mode
    s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=2)
    d = Director(s, None, TracingGame(clock), FakeObs(clock, tmp_path), DemoLMU())
    d._now, d._sleep = clock.now, clock.sleep
    d._out_dir = tmp_path
    raw = json.loads((FIX / "interlagos_race.json").read_text())
    d.history = results.parse(str(FIX / "interlagos_results.xml"), raw["standings"])
    d.mode = "history"
    d.frames = H.frames_from_history(d.history)
    d.events = H.derive_events(d.history)
    return d, clock


def test_frame_at_orders_by_progress():
    raw = json.loads((FIX / "interlagos_race.json").read_text())
    h = results.parse(str(FIX / "interlagos_results.xml"), raw["standings"])
    f = H.frame_at(h, 600.0)
    assert len(f.cars) == 29 and [c.place for c in f.cars] == list(range(1, 30))
    assert f.cars[0].laps >= f.cars[-1].laps
    assert H.frame_at(h, 0.0).cars[0].laps == 0


@needs_ffmpeg
def test_record_and_compose_end_to_end(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch)
    d.analyze(); plan = d.make_plan()
    assert plan.inlays and plan.t1 - plan.t0 == pytest.approx(90.0)
    paths = d.record()
    # clips are recorded first, then the main pass: seek to its start, focus the driver, play
    log = d.game.log
    assert log[0] == ("seek", plan.inlays[0].clip_start)
    assert ("seek", 100.0) in log and ("focus", plan.focus_id) in log
    assert sum(1 for x in log if x[0] == "camera") >= len(plan.cues[:5])
    assert plan.main_offset == pytest.approx(0.25 + 0.3, abs=0.2)           # lead-in incl. play latency
    assert all(i.path and i.offset > 0 for i in plan.inlays)
    # each clip was recorded at its own replay time with the overlay in 'pip' mode
    seeks = [x[1] for x in log if x[0] == "seek"]
    assert seeks == [i.clip_start for i in plan.inlays] + [plan.t0]
    out = d.finish(paths["main"], None)
    assert probe_duration(out) == pytest.approx(90.0 - 0.0, abs=2.0)


def test_obs_live_compositing(tmp_path, monkeypatch):
    d, clock = make_director(tmp_path, monkeypatch, mode="obs")
    d.analyze(); plan = d.make_plan()
    paths = d.record()
    log = d.obs.log
    names = [x[0] for x in log]
    # clips are recorded first (2 starts for 1 clip + main), boxes are set up only before the main pass
    starts = [x for x in log if x[0] == "start"]
    assert len(starts) == 1 + len(plan.inlays)
    assert names.index("setup_pip") > names.index("start")                  # boxes are built after the clip pass
    main_start = starts[-1][1]
    show = next(x for x in log if x[0] == "pip_show")
    hide = next(x for x in log if x[0] == "pip_hide")
    inl = plan.inlays[0]
    # shown/hidden at the right moment of the main pass (wall time since main start ~ replay time - t0)
    assert show[3] - main_start == pytest.approx(inl.show_at - plan.t0, abs=2.0)
    assert hide[2] - main_start == pytest.approx(inl.show_until - plan.t0, abs=2.0)
    assert show[2] == int(inl.offset * 1000)                                  # clip lead-in skipped
    assert ("pip_cleanup",) in log                                             # test sources are always removed


@needs_ffmpeg
def test_commentary_ducks_the_game_audio(tmp_path):
    """Game = 300 Hz tone; commentary = quiet 1 kHz bursts. The tone must drop while the burst plays."""
    import re
    from driverdirector.compose import compose

    def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
    game, speech, out = tmp_path / "g.mp4", tmp_path / "s.wav", tmp_path / "o.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=14", "-f", "lavfi", "-i", "sine=f=300:d=14", "-af", "volume=0.5",
       "-c:v", "libx264", "-c:a", "aac", str(game))
    ff("-f", "lavfi", "-i", "sine=f=1000:d=14", "-af", "volume='if(between(t,4,7),0.12,0)':eval=frame", str(speech))
    compose(str(game), [], str(out), 0.0, commentary=str(speech), duck_db=12, encoders=["libx264"], width=320)

    def tone(ss, dur):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(ss), "-t", str(dur), "-i", str(out), "-af",
                            "bandpass=f=300:width_type=h:w=60,volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        return float(re.search(r"mean_volume: (-?[\d.]+) dB", r).group(1))
    depth = tone(9, 4) - tone(5, 1.5)
    assert 8 <= depth <= 26, depth


@needs_ffmpeg
def test_mux_commentary_copies_the_video_stream(tmp_path):
    """Adding commentary must not re-encode (or need memory for) the video."""
    from driverdirector.compose import mux_commentary

    def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
    main, wav, out = tmp_path / "m.mp4", tmp_path / "c.wav", tmp_path / "o.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=10:d=8", "-f", "lavfi", "-i", "sine=f=300:d=8", "-c:v", "libx264",
       "-c:a", "aac", str(main))
    ff("-f", "lavfi", "-i", "sine=f=1000:d=8", "-af", "volume='if(between(t,2,4),0.2,0)':eval=frame", str(wav))
    mux_commentary(str(main), str(wav), str(out), main_offset=0.3, duck_db=12)

    def probe(f, entry):
        return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", f"stream={entry}",
                               "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout.strip()
    assert probe(out, "codec_name") == "h264" and probe(out, "nb_frames") == probe(main, "nb_frames")
    assert probe(out, "width") == "320"
    assert abs(probe_duration(str(out)) - probe_duration(str(main))) < 0.5


def test_unique_path_never_overwrites(tmp_path):
    from driverdirector.director import unique_path
    a = tmp_path / "v.mp4"
    assert unique_path(a) == str(a)
    a.write_text("x"); (tmp_path / "v (2).mp4").write_text("x")
    assert unique_path(a) == str(tmp_path / "v (3).mp4")


@needs_ffmpeg
def test_inlay_box_size_border_and_position_use_the_real_video_width(tmp_path):
    """Main is 1280 wide (not the old 1920 assumption): the box must be size*1280 wide with a visible border."""
    from driverdirector.compose import compose
    from driverdirector.models import Event, Inlay

    def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
    main, clip, out = tmp_path / "m.mp4", tmp_path / "c.mp4", tmp_path / "o.mp4"
    ff("-f", "lavfi", "-i", "color=c=0x0000ff:s=1280x720:r=10:d=6", "-f", "lavfi", "-i", "sine=d=6", "-c:v", "libx264",
       "-c:a", "aac", "-pix_fmt", "yuv420p", str(main))
    ff("-f", "lavfi", "-i", "color=c=0x00ff00:s=640x360:r=10:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
    inl = Inlay(Event("incident", 2, 5, [1]), 2.0, 5.0, 2.0, 5.0, 1, "trackside", "X", path=str(clip))
    compose(str(main), [inl], str(out), 0.0, encoders=["libx264"], size=0.36, border_pct=0.6, border_color="red",
            corner="top_right")                                   # no width passed: it must be probed from `main`

    def frame(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return [raw[i:i + 1280 * 3] for i in range(0, 1280 * 3 * 720, 1280 * 3)]

    def box(rows, pred):
        xs = [x for row in rows for x in range(1280) if pred(row[x * 3], row[x * 3 + 1], row[x * 3 + 2])]
        ys = [y for y, row in enumerate(rows) if any(pred(row[x * 3], row[x * 3 + 1], row[x * 3 + 2]) for x in range(0, 1280, 2))]
        return (min(xs), max(xs), min(ys), max(ys)) if xs else None

    during = frame(3.0)
    green = box(during, lambda r, g, b: g > 200 and r < 60 and b < 60)       # the clip
    red = box(during, lambda r, g, b: r > 200 and g < 60 and b < 60)         # the border
    assert green and red
    assert abs((green[1] - green[0] + 1) - 460) <= 4                          # int(1280*0.36)//2*2 = 460, not ~345 (1920 basis)
    border_px = max(4, round(1280 * 0.6 / 100))
    assert abs((red[1] - red[0] + 1) - (460 + 2 * border_px)) <= 4            # border is visible on every side
    assert red[0] < green[0] and red[1] > green[1] and red[2] < green[2]
    assert abs((1280 - 1 - red[1]) - round(1280 * 0.012)) <= 3                # sits in from the right edge
    assert box(frame(0.5), lambda r, g, b: r > 200 and g < 60 and b < 60) is None   # and only while the box is shown


def test_border_colour_is_sanitised_for_the_filter_graph():
    from driverdirector.compose import safe_color
    assert safe_color("white") == "white" and safe_color("#e10600") == "0xe10600" and safe_color("e10600") == "0xe10600"
    assert safe_color("red;[x]movie=/etc/passwd") == "white" and safe_color("") == "white"
