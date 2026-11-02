import os
import time

from driverdirector import cleanup


def touch(d, name, size=10):
    p = d / name
    p.write_bytes(b"x" * size)
    return p


def test_finished_videos_are_recognised_by_name():
    for n in ("Sebring_2026-10-08_Race_Alex-Hartley.mp4", "x-R1 (4)_directed.mp4", "x-R1_commentary.mp4"):
        assert cleanup.is_final(n)
    for n in ("LusailInternationalCircuit-20261007-R1 (17).mp4", "LusailInternationalCircuit-20261007-R1.mkv"):
        assert not cleanup.is_final(n)


def test_candidates_skip_finals_protected_files_and_non_videos(tmp_path):
    raw, clip = touch(tmp_path, "Lusail-20261007-R1 (1).mp4"), touch(tmp_path, "Lusail-20261007-R1 (2).mp4")
    touch(tmp_path, "Interlagos_2026-10-08_Race_Alex-Hartley.mp4")
    touch(tmp_path, "notes.txt")
    got = {os.path.basename(c["path"]) for c in cleanup.candidates(tmp_path, protect=[clip])}
    assert got == {raw.name}


def test_sweep_moves_never_deletes_and_never_takes_a_final(tmp_path):
    raw = touch(tmp_path, "Lusail-20261007-R1 (1).mp4")
    final = touch(tmp_path, "Interlagos_2026-10-08_Race_Alex-Hartley.mp4")
    moved = cleanup.sweep([raw, final], tmp_path)
    assert len(moved) == 1 and os.path.isfile(moved[0]) and not raw.exists() and final.exists()
    assert cleanup.quarantined_size(tmp_path) == 10


def test_purge_only_removes_old_batches(tmp_path):
    a = touch(tmp_path, "a-R1 (1).mp4")
    cleanup.sweep([a], tmp_path)
    assert cleanup.purge(tmp_path, older_than_days=7) == []                # fresh: kept
    batch = next((tmp_path / cleanup.QUARANTINE).iterdir())
    old = time.time() - 8 * 86400
    os.utime(batch, (old, old))
    assert len(cleanup.purge(tmp_path, older_than_days=7)) == 1
    assert not (tmp_path / cleanup.QUARANTINE).exists()


def _director(tmp_path, monkeypatch):
    from driverdirector.config import DirectSettings
    from tests.test_record import make_director
    d, _ = make_director(tmp_path, monkeypatch)
    d.s.direct = DirectSettings(focus_driver="Alex Hartley", t_start=100.0, t_end=190.0, max_inlays=1)
    d.analyze(); d.make_plan()
    d.s.obs.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    return d


def test_final_video_is_named_after_the_race(tmp_path, monkeypatch):
    from driverdirector.history import RaceHistory
    d = _director(tmp_path, monkeypatch)
    d.history = RaceHistory({}, {}, [], 0.0, track="Aut\u00f3dromo Jos\u00e9 Carlos Pace", session="RACE", date="2026-10-08")
    assert d.final_stem() == "Autodromo-Jose-Carlos-Pace_2026-10-08_Race_Alex-Hartley"
    assert cleanup.is_final(d.final_stem() + ".mp4")                       # and it is never mistaken for a raw recording


def test_tidy_moves_raw_recordings_only_after_the_video_checks_out(tmp_path, monkeypatch):
    from driverdirector import compose
    d = _director(tmp_path, monkeypatch)
    out = tmp_path / "out"
    main, clip = touch(out, "Lusail-20261007-R1 (1).mp4"), touch(out, "Lusail-20261007-R1 (2).mp4")
    final = touch(out, "Track_2026-10-08_Race_Alex-Hartley.mp4")
    d.main_path = str(main)
    d.plan.inlays[0].path = str(clip)
    want = d.plan.t1 - d.plan.t0
    monkeypatch.setattr(compose, "probe_duration", lambda p: want - 60)    # truncated video: keep the evidence
    d._tidy(str(final))
    assert main.exists() and clip.exists() and d.status["stage"] == "warning"
    monkeypatch.setattr(compose, "probe_duration", lambda p: want + 0.4)
    d._tidy(str(final))
    assert not main.exists() and not clip.exists() and final.exists()
    d.s.cleanup = "off"
    again = touch(out, "Lusail-20261007-R1 (3).mp4"); d.main_path = str(again)
    d._tidy(str(final))
    assert again.exists()


def test_an_unfinished_jobs_recordings_are_protected(tmp_path, monkeypatch):
    import json
    d = _director(tmp_path, monkeypatch)
    out = tmp_path / "out"
    main = touch(out, "Lusail-20261007-R1 (1).mp4")
    d.manifest_path = tmp_path / "last.json"
    d.manifest_path.write_text(json.dumps({"main": str(main), "inlays": []}))
    assert d.protected_paths() == [str(main)]
    touch(out, "Track_2026-10-08_Race_Alex-Hartley.mp4")                    # its video now exists
    assert d.protected_paths() == []
