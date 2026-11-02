import subprocess
import time
from types import SimpleNamespace
from fastapi.testclient import TestClient

from driverdirector import commentary as cm
from driverdirector.config import Settings, CommentarySettings, DirectSettings
from driverdirector.director import Director
from driverdirector.server import create_app
from driverdirector.sources.simulator import Simulator


def demo_director():
    s = Settings()
    s.direct = DirectSettings(focus_driver="Alex Rivera")
    return Director(s, Simulator())


def wait_idle(c, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/api/status").json()
        if st["stage"] in ("scan", "detect") and st["progress"] < 1.0 or st["stage"] == "preview":
            time.sleep(0.1); continue
        return st
    raise AssertionError("timeout")


def test_api_flow(monkeypatch):
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: __import__("pathlib").Path(__import__("tempfile").mkdtemp()))
    c = TestClient(create_app(demo_director()))
    assert c.post("/api/scan").status_code == 200
    for _ in range(100):
        if c.get("/api/status").json()["frames"] > 3000: break
        time.sleep(0.1)
    roster = c.get("/api/roster").json()
    assert roster[0]["driver"]
    plan = c.post("/api/plan").json()
    assert plan["inlays"] >= 1 and plan["cues"] > 10
    assert c.get("/api/plan").json()["inlays"]
    assert c.get("/overlay").status_code == 200
    assert c.get("/theme/default/style.css").status_code == 200
    assert c.get("/theme/..%2f..%2findex.html/x").status_code in (404, 422)
    with c.websocket_connect("/ws") as ws:
        assert "mode" in ws.receive_json()


def test_commentary_validation_and_retry():
    d = demo_director()
    d.scan(); d.analyze(); d.make_plan()
    scenes = cm.build_scenes(d.plan, d.frames)
    assert scenes[0].kind == "start" and scenes[-1].kind == "finish"
    cfg = CommentarySettings()
    # first reply invents a lap time, second is clean
    calls = []
    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            txt = '[' + ','.join(f'{{"id":{s.id},"text":"He sets a 1:59.123 lap"}}' for s in scenes) + ']'
        else:
            txt = '[' + ','.join(f'{{"id":{s.id},"text":"Great stuff"}}' for s in scenes) + ']'
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=txt)])
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    lines = cm.generate_script(scenes, cfg, client=client)
    assert len(calls) == 2 and set(lines) == {s.id for s in scenes}
    assert cm.validate({0: "x " * 200}, scenes[:1], cfg)       # over budget flagged


def test_cli_client_flattens_retries_and_falls_back(tmp_path):
    from types import SimpleNamespace
    seen = []

    def runner(cmd, text):
        seen.append((cmd, text))
        bad_model = "--model" in cmd and "nonexistent" in cmd
        return SimpleNamespace(returncode=1 if bad_model else 0, stdout='[{"id": 0, "text": "ok"}]', stderr="bad model")

    c = cm.ClaudeCliClient("claude.exe", runner=runner)
    r = c.messages.create(model="nonexistent", max_tokens=10, messages=[
        {"role": "user", "content": "write"}, {"role": "assistant", "content": "[bad]"},
        {"role": "user", "content": "fix it"}])
    assert r.content[0].text.startswith("[{")
    assert len(seen) == 2 and "--model" in seen[0][0] and "--model" not in seen[1][0]   # retried w/o model
    assert all(x[0][:2] == ["claude.exe", "-p"] and "--tools" in x[0] for x in seen)     # headless, no tools
    assert "Previous answer of yours" in seen[0][1] and "fix it" in seen[0][1]           # prompt via stdin


def test_make_client_backend_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cm.make_client(CommentarySettings(backend="api"))
    monkeypatch.setattr(cm, "find_claude_cli", lambda p="": None)
    with pytest.raises(RuntimeError, match="CLI not found"):
        cm.make_client(CommentarySettings(backend="cli"))


def test_scenes_for_a_partial_slice_dont_claim_start_or_finish():
    d = demo_director()
    d.scan(); d.analyze()
    d.s.direct = DirectSettings(focus_driver="Alex Rivera", t_start=200.0, t_end=400.0)   # middle of a 600 s race
    d.make_plan()
    scenes = cm.build_scenes(d.plan, d.frames)
    assert scenes[0].kind == "start" and "in progress" in scenes[0].facts and "starting P" not in scenes[0].facts
    assert scenes[-1].kind == "end" and "takes the flag" not in scenes[-1].facts
    full = demo_director(); full.scan(); full.analyze(); full.make_plan()
    fs = cm.build_scenes(full.plan, full.frames)
    assert fs[0].facts.startswith("Race coverage begins") and fs[-1].kind == "finish"


def test_cli_not_logged_in_gives_the_fix():
    import pytest
    from types import SimpleNamespace
    c = cm.ClaudeCliClient("C:/x/claude.exe", runner=lambda cmd, text: SimpleNamespace(
        returncode=1, stdout="Not logged in · Please run /login", stderr=""))
    with pytest.raises(RuntimeError, match="auth login"):
        c.messages.create(model="", messages=[{"role": "user", "content": "hi"}])


def test_voice_resolution_and_dropdown(tmp_path):
    from driverdirector import tts
    root = tmp_path / "Runtime"
    (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "venv" / "Scripts" / "python.exe").write_text("")
    voices = tmp_path / "Voices" / "chatterbox"
    voices.mkdir(parents=True)
    for n in ("british-female-normal", "british-female-excited", "british-female-overexcited", "solo-normal"):
        (voices / f"{n}.wav").write_text("")
    cfg = CommentarySettings(chatterbox_runtime=str(root))
    opts = {o["value"]: o["label"] for o in tts.list_voices(cfg)}
    assert "" in opts and "british-female" in opts and "british-female-normal" in opts
    assert "solo" not in opts                                        # a family with one clip isn't an 'auto' choice
    rt = tts.find_runtime(cfg)
    name = lambda v, e: (tts._voice_path(CommentarySettings(voice=v), rt, e) or "default").replace("\\", "/").split("/")[-1]
    assert [name("british-female", e) for e in (0, 1, 2)] == [
        "british-female-normal.wav", "british-female-excited.wav", "british-female-overexcited.wav"]
    assert {name("british-female-normal", e) for e in (0, 1, 2)} == {"british-female-normal.wav"}
    assert name("", 1) == "default"


def test_chatterbox_refuses_when_memory_is_low(tmp_path, monkeypatch):
    import pytest
    from driverdirector import tts
    root = tmp_path / "Runtime"; (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "venv" / "Scripts" / "python.exe").write_text("")
    monkeypatch.setattr(tts, "free_ram_gb", lambda: 1.2)
    with pytest.raises(RuntimeError, match="Close Le Mans Ultimate"):
        tts.synth_chatterbox([tts.Line(0, "hi")], CommentarySettings(chatterbox_runtime=str(root)), tmp_path / "w")


def test_old_edge_voice_setting_is_migrated():
    c = CommentarySettings(voice="en-GB-RyanNeural")
    assert c.edge_voice == "en-GB-RyanNeural" and c.voice == "british-female-normal"


def test_chatterbox_noisy_worker_does_not_deadlock(tmp_path, monkeypatch):
    """A worker that writes far more than a pipe buffer to stderr must not hang the parent."""
    import sys, textwrap
    from driverdirector import tts
    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent('''
        import json, sys
        job = json.load(open(sys.argv[1]))
        for i, l in enumerate(job["lines"]):
            sys.stderr.write("progress 100%% " * 40000)       # ~600 KB of stderr noise per line
            sys.stderr.flush()
            open(l["out"], "w").write("audio")
            print(f"DONE {i} 0.1", flush=True)
    '''))
    root = tmp_path / "Runtime"; (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "venv" / "Scripts" / "python.exe").write_text("")
    monkeypatch.setattr(tts, "WORKER", worker)
    monkeypatch.setattr(tts, "free_ram_gb", lambda: 16.0)
    real = subprocess.Popen
    monkeypatch.setattr(tts.subprocess, "Popen", lambda cmd, **kw: real([sys.executable] + cmd[1:], **kw))
    seen = []
    out = tts.synth_chatterbox([tts.Line(0, "a"), tts.Line(1, "b")], CommentarySettings(chatterbox_runtime=str(root)),
                               tmp_path / "w", progress=lambda i, n: seen.append((i, n)))
    assert set(out) == {0, 1} and all(f.is_file() for f in out.values()) and seen == [(1, 2), (2, 2)]


def test_real_worker_script_is_valid_python():
    """The real Chatterbox worker can't run in tests (needs torch + models), but it must at least compile."""
    import py_compile
    py_compile.compile(str(__import__("driverdirector.tts", fromlist=["x"]).WORKER), doraise=True)


def test_commentary_is_timed_to_the_inlay_box_and_labels_are_short():
    """The 5-minute test spoke about a battle 44 s before its box appeared, and the box label was a sentence."""
    import json
    from pathlib import Path
    from driverdirector import history as H, results
    from driverdirector.config import CameraSettings
    from driverdirector.planner import build_plan
    fx = Path(__file__).parent / "fixtures"
    raw = json.loads((fx / "interlagos_race.json").read_text())
    h = results.parse(str(fx / "interlagos_results.xml"), raw["standings"])
    ev, fr = H.derive_events(h), H.frames_from_history(h)
    plan = build_plan(fr, ev, h.by_name("Alex Hartley"), DirectSettings(t_start=100.0, t_end=400.0, max_inlays=4), CameraSettings())
    scenes = cm.build_scenes(plan, fr)
    assert plan.inlays
    for inl in plan.inlays:
        assert len(inl.label) <= 30, inl.label                              # fits the chip in the corner of the box
        if inl.event.kind in ("incident", "battle"):
            near = [s for s in scenes if s.kind == inl.event.kind and abs(s.t - (inl.show_at + 1)) < 1.5]
            assert near, (inl.label, inl.show_at, [round(s.t) for s in scenes])   # a line lands while the box is on screen
    battle = next((i for i in plan.inlays if i.event.kind == "battle"), None)
    if battle:
        assert battle.label.startswith("BATTLE FOR P")
        text = next(s.facts for s in scenes if s.kind == "battle")
        assert "timing lines" not in text and "stuck to the back of" in text      # no jargon for the voice to read out


def test_intro_talks_about_qualifying_before_any_racing():
    from driverdirector.history import Driver, Quali, RaceHistory
    d = demo_director()
    d.scan(); d.analyze(); d.make_plan()
    plan, focus = d.plan, d.plan.focus_id
    cars = {c.id: c for c in d.frames[0].cars}
    drivers = {i: Driver(i, c.driver, c.number, c.car_class, "", grid=n) for n, (i, c) in enumerate(cars.items(), 1)}
    quali = {i: Quali(n, n, 90.0 + n * 0.1) for n, i in enumerate(cars, 1)}
    h = RaceHistory(drivers, {}, [], 0.0, quali=quali)
    scenes = cm.build_scenes(plan, d.frames, history=h)
    intro = [s for s in scenes if s.kind == "intro"]
    assert len(intro) == 1 and intro[0].t == plan.t0 + 0.3
    assert "qualified P" in intro[0].facts and "starts from P" in intro[0].facts
    assert "nothing has happened on track" in intro[0].facts
    assert not any(s.kind == "start" for s in scenes)
    assert all(s.t >= plan.t0 + 18 for s in scenes if s.kind not in ("intro", "grid"))
