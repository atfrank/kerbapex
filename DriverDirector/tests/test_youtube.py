import base64
import hashlib
import http.server
import json
import threading
import urllib.parse
import urllib.request

import pytest
from fastapi.testclient import TestClient

from driverdirector import youtube as yt
from driverdirector.config import Settings, YouTubeSettings
from driverdirector.server import create_app
from tests.test_server_commentary import demo_director


class Mock:
    """Just enough of Google's OAuth, channels and resumable-upload endpoints."""

    def __init__(self):
        self.token_posts, self.received, self.fail_once, self.reject_synthetic = [], bytearray(), True, False
        self.init_bodies, self.size = [], 0
        mock = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if self.path == "/token":
                    mock.token_posts.append(dict(urllib.parse.parse_qsl(body.decode())))
                    return self._send(200, json.dumps({"access_token": "AT%d" % len(mock.token_posts),
                                                       "refresh_token": "RT", "expires_in": 3600}).encode())
                if self.path.startswith("/upload"):
                    meta = json.loads(body)
                    mock.init_bodies.append(meta)
                    if mock.reject_synthetic and "containsSyntheticMedia" in meta["status"]:
                        return self._send(400, b'{"error":{"message":"Unknown name containsSyntheticMedia"}}')
                    mock.size = int(self.headers["X-Upload-Content-Length"])
                    return self._send(200, headers={"Location": f"http://127.0.0.1:{self.server.server_port}/session"})
                self._send(404)

            def do_GET(self):
                if self.path.startswith("/api/channels"):
                    return self._send(200, json.dumps({"items": [{"snippet": {"title": "My Channel"}}]}).encode())
                self._send(404)

            def do_PUT(self):
                n = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(n)
                rng = self.headers["Content-Range"]
                if rng.startswith("bytes */"):                          # status query
                    last = len(mock.received) - 1
                    return self._send(308, headers={"Range": f"bytes=0-{last}"} if last >= 0 else {})
                if mock.fail_once and len(mock.received) >= 2000:
                    mock.fail_once = False
                    return self._send(503)
                mock.received += data
                if len(mock.received) >= mock.size:
                    return self._send(200, json.dumps({"id": "abc123", "status": {"privacyStatus": "private"}}).encode())
                self._send(308, headers={"Range": f"bytes=0-{len(mock.received) - 1}"})

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture()
def google(monkeypatch, tmp_path):
    m = Mock()
    monkeypatch.setattr(yt, "TOKEN_URL", m.base + "/token")
    monkeypatch.setattr(yt, "API", m.base + "/api")
    monkeypatch.setattr(yt, "UPLOAD", m.base + "/upload")
    monkeypatch.setattr("driverdirector.youtube.data_dir", lambda: tmp_path)
    secret = tmp_path / "client.json"
    secret.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    cfg = YouTubeSettings(client_secret_path=str(secret))
    yield m, cfg
    m.srv.shutdown()


def approve(url):
    """Stands in for the person approving in their browser: Google redirects back with a code."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    urllib.request.urlopen(f"{q['redirect_uri']}/?code=CODE&state={q['state']}").read()
    approve.query = q


def test_sign_in_uses_pkce_and_keeps_only_a_refresh_token_file(google):
    m, cfg = google
    r = yt.authorize(cfg, open_browser=approve, timeout=10)
    assert r["authorized"] and r["channel"] == "My Channel"
    q, post = approve.query, m.token_posts[0]
    assert q["code_challenge_method"] == "S256" and q["access_type"] == "offline" and "youtube.upload" in q["scope"]
    digest = base64.urlsafe_b64encode(hashlib.sha256(post["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert digest == q["code_challenge"] and post["grant_type"] == "authorization_code"
    assert yt.status(cfg)["authorized"] and yt.status(cfg)["channel"] == "My Channel"
    assert yt.disconnect() and not yt.status(cfg)["authorized"]


def test_sign_in_rejects_a_forged_redirect(google):
    m, cfg = google

    def forged(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        try:
            urllib.request.urlopen(f"{q['redirect_uri']}/?code=EVIL&state=wrong").read()
        except Exception:
            pass

    with pytest.raises(yt.YouTubeError):
        yt.authorize(cfg, open_browser=forged, timeout=1.5)
    assert not m.token_posts


def test_expired_token_is_refreshed(google):
    m, cfg = google
    yt.authorize(cfg, open_browser=approve, timeout=10)
    tok = json.loads(yt.token_path().read_text())
    tok["expires_at"] = 0
    yt.token_path().write_text(json.dumps(tok))
    assert yt.access_token(cfg).startswith("AT") and m.token_posts[-1]["grant_type"] == "refresh_token"
    n = len(m.token_posts)
    yt.access_token(cfg)
    assert len(m.token_posts) == n                                     # still valid: no second refresh


def test_upload_is_chunked_resumes_after_a_server_error_and_is_private_by_default(google, tmp_path):
    m, cfg = google
    yt.authorize(cfg, open_browser=approve, timeout=10)
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(256)) * 40)                          # 10,240 bytes
    seen = []
    r = yt.upload(cfg, str(video), "T" * 150, "desc", tags=["a"], chunk=1000, progress=lambda d, t: seen.append(d),
                  sleep=lambda s: None)
    assert r == {"id": "abc123", "url": "https://youtu.be/abc123", "privacy": "private"}
    assert bytes(m.received) == video.read_bytes()                     # nothing lost or duplicated by the retry
    meta = m.init_bodies[0]
    assert meta["status"]["privacyStatus"] == "private" and len(meta["snippet"]["title"]) == 100
    assert meta["status"]["selfDeclaredMadeForKids"] is False and seen[-1] == 10240


def test_upload_drops_the_ai_flag_when_the_api_does_not_know_it(google, tmp_path):
    m, cfg = google
    m.reject_synthetic, m.fail_once = True, False
    yt.authorize(cfg, open_browser=approve, timeout=10)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 500)
    yt.upload(cfg, str(video), "t", "d", chunk=1000)
    assert "containsSyntheticMedia" in m.init_bodies[0]["status"]
    assert "containsSyntheticMedia" not in m.init_bodies[1]["status"]


def test_upload_validates_inputs(google, tmp_path):
    _, cfg = google
    with pytest.raises(yt.YouTubeError):
        yt.upload(cfg, str(tmp_path / "missing.mp4"), "t", "d")
    (tmp_path / "v.mp4").write_bytes(b"x")
    with pytest.raises(yt.YouTubeError):
        yt.upload(cfg, str(tmp_path / "v.mp4"), "t", "d", privacy="everyone")
    with pytest.raises(yt.YouTubeError):                                # not connected
        yt.upload(cfg, str(tmp_path / "v.mp4"), "t", "d")


def test_missing_client_file_gives_a_setup_hint():
    with pytest.raises(yt.YouTubeError, match="client_secret_path"):
        yt.load_client(YouTubeSettings())


def test_status_walks_through_the_setup(google, tmp_path, monkeypatch):
    m, cfg = google
    s = yt.status(YouTubeSettings())
    assert not s["configured"] and any("Test users" in step for step in s["setup_steps"])
    s = yt.status(cfg)
    assert s["configured"] and not s["authorized"] and "dd youtube connect" in s["next_step"]
    yt.authorize(cfg, open_browser=approve, timeout=10)
    s = yt.status(cfg)                                                  # the mock grants no edit scope
    assert s["authorized"] and s["channel"] == "My Channel" and not s["can_edit"] and "connect" in s["next_step"]
    tok = json.loads(yt.token_path().read_text())
    tok["scope"] = " ".join(yt.SCOPES)
    yt.token_path().write_text(json.dumps(tok))
    assert yt.status(cfg)["can_edit"] and yt.status(cfg)["next_step"] == "Ready."
    other = tmp_path / "other.json"                                     # client file replaced: the old token is stale
    other.write_text(json.dumps({"installed": {"client_id": "cid2", "client_secret": "sec2", "project_id": "p2"}}))
    s = yt.status(YouTubeSettings(client_secret_path=str(other)))
    assert not s["authorized"] and s["project_id"] == "p2" and "different client" in s["next_step"]


def test_web_client_and_sign_in_errors_explain_the_fix(google, tmp_path):
    _, cfg = google
    web = tmp_path / "web.json"
    web.write_text(json.dumps({"web": {"client_id": "w", "client_secret": "s"}}))
    with pytest.raises(yt.YouTubeError, match="Desktop app"):
        yt.load_client(YouTubeSettings(client_secret_path=str(web)))

    def denied(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        urllib.request.urlopen(f"{q['redirect_uri']}/?error=access_denied&state={q['state']}").read()

    with pytest.raises(yt.YouTubeError, match="Test user"):
        yt.authorize(cfg, open_browser=denied, timeout=10)


def test_draft_has_chapters_only_when_valid():
    cfg = YouTubeSettings()
    d = yt.draft_metadata(cfg, "Alex Hartley", "Interlagos", "Race",
                          [(536, "7-car incident"), (600, "5-car incident"), (606, "too close to the last")], 1959)
    assert d["title"] == "Alex Hartley | Interlagos Race | Le Mans Ultimate" and d["privacy"] == "private"
    lines = d["description"].splitlines()
    assert "0:00 Start" in lines and "8:56 7-car incident" in lines and "10:00 5-car incident" in lines
    assert not any("too close" in l for l in lines) and "AI-generated" in d["description"]
    assert "Chapters" not in yt.draft_metadata(cfg, "A", "T", "Race", [], 100)["description"]


def test_upload_endpoint_needs_explicit_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("driverdirector.config.data_dir", lambda: tmp_path)
    c = TestClient(create_app(demo_director()))
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    post = lambda **b: c.post("/api/youtube/upload", json=b).status_code
    assert post(video=str(v), title="t") == 400
    assert post(video=str(v), title="t", confirm="yes") == 400
    assert post(video=str(v), title="", confirm=True) == 400
    assert post(video=str(v), title="t", confirm=True, privacy="x") == 400
    assert post(video=str(tmp_path / "nope.mp4"), title="t", confirm=True) == 400
    assert c.post("/api/youtube/connect").status_code == 400            # no client file configured yet


def test_settings_default_is_private_and_roundtrips():
    s = Settings()
    assert s.youtube.privacy == "private"
    assert Settings.model_validate_json(s.model_dump_json()).youtube.category_id == "20"


def test_ai_disclosure_names_only_what_is_ai():
    cfg = YouTubeSettings()
    d = lambda **kw: yt.draft_metadata(cfg, "A", "T", "Race", [], 100, **kw)["description"]
    assert "commentary voice and the background music are AI-generated" in d(music=True, voice=True)
    assert "The commentary voice is AI-generated." in d(music=False, voice=True)
    assert "The background music is AI-generated." in d(music=True, voice=False)
    assert "AI-generated" not in d(music=False, voice=False) and "Made with DriverDirector" in d(music=False, voice=False)
