"""Publish a finished video to YouTube (Data API v3, resumable upload). Standard library only.

Setup the user does once, in their own Google account (DriverDirector never sees their Google password); the steps
are in SETUP_STEPS below and `dd youtube setup` walks through them. All three Google pieces must be in the SAME Cloud
project: the YouTube Data API v3, the consent screen with the user's account as a TEST USER (without it Google answers
"Access blocked" / access_denied), and the OAuth client of type Desktop app. An API key (AIza...) cannot be used.
The refresh token is kept in %APPDATA%\\DriverDirector\\youtube_token.json (delete the file to disconnect).

Videos upload as PRIVATE unless asked otherwise. Note: Google keeps uploads from API projects that have not passed
its API audit private whatever is requested; if so, switch the video to public in YouTube Studio.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import http.server
import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from .config import YouTubeSettings, data_dir

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube"]       # the last one lets videos.update change a description or visibility
EDIT_SCOPE = "https://www.googleapis.com/auth/youtube"
CHUNK = 32 * 1024 * 1024                      # must be a multiple of 256 KiB
PRIVACY = ("private", "unlisted", "public")

# The one-time setup in the user's own Google account, in order. Every step happens in ONE Cloud project.
SETUP_STEPS = [
    "Open console.cloud.google.com and pick (or create) one project for DriverDirector; check its name in the "
    "project picker at the top. Do every step below in that same project.",
    "APIs & Services > Library: search 'YouTube Data API v3' and press Enable.",
    "Google Auth Platform (or APIs & Services > OAuth consent screen): set it up as External, app name e.g. "
    "DriverDirector, your email as support and developer contact.",
    "Audience > Test users > Add users: add the Google account that owns your YouTube channel. This is the step that "
    "is easy to miss: without it Google shows 'Access blocked' / 'Error 403: access_denied' when you approve.",
    "Clients (or APIs & Services > Credentials) > Create client > OAuth client ID, type 'Desktop app'. Download the "
    "JSON (client_secret_....json). Do not use an API key (AIza...): it cannot upload.",
    "Run `dd youtube setup` (it finds the newest client_secret_*.json in Downloads, or give its path), then "
    "`dd youtube connect` and approve in the browser within 5 minutes.",
]
SIGN_IN_HINT = ("If Google showed 'Access blocked' or 'access_denied', add your Google account as a Test user on the "
                "consent screen (Audience > Test users) of the project the client file belongs to, check that the "
                "YouTube Data API v3 is enabled in that same project, then connect again.")


class YouTubeError(RuntimeError):
    pass


def token_path() -> Path:
    return data_dir() / "youtube_token.json"


# ---- low-level HTTP ------------------------------------------------------------------------------
def _request(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
             timeout: float = 120.0) -> tuple[int, dict, bytes]:
    u = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = cls(u.netloc, timeout=timeout)
    try:
        conn.request(method, u.path + (f"?{u.query}" if u.query else ""), body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def _json(method: str, url: str, headers=None, body: dict | None = None, form: dict | None = None):
    h = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json; charset=UTF-8"
    status, hdrs, raw = _request(method, url, h, data)
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {"raw": raw[:300].decode("utf-8", "replace")}
    if status >= 400:
        err = payload.get("error")
        msg = payload.get("error_description") or (err.get("message") if isinstance(err, dict) else err)
        raise YouTubeError(f"{status}: {msg or payload}")
    return payload


# ---- OAuth ---------------------------------------------------------------------------------------
def load_client(cfg: YouTubeSettings) -> dict:
    if not cfg.client_secret_path:
        raise YouTubeError("set youtube.client_secret_path to the OAuth client JSON from Google Cloud (see the "
                           "setup steps in the YouTube panel)")
    try:
        d = json.loads(Path(cfg.client_secret_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise YouTubeError(f"cannot read the OAuth client file: {e}")
    if "web" in d and "installed" not in d:
        raise YouTubeError("that OAuth client is of type 'Web application'; create one of type 'Desktop app' instead")
    c = d.get("installed") or d
    if not (c.get("client_id") and c.get("client_secret")):
        raise YouTubeError("that file has no client_id/client_secret; download the OAuth client of type 'Desktop app'")
    return c


def _save_token(tok: dict) -> None:
    p = token_path()
    p.write_text(json.dumps(tok), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _load_token() -> Optional[dict]:
    try:
        return json.loads(token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def authorize(cfg: YouTubeSettings, announce: Callable[[str], None] = lambda url: None,
              open_browser: Callable[[str], object] = webbrowser.open, timeout: float = 300.0) -> dict:
    """Run the browser sign-in (loopback redirect + PKCE) and keep the refresh token. The person signs in and
    approves in their own browser; `announce(url)` also reports the address in case no browser opens."""
    client = load_client(cfg)
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}
    done = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if q.get("state") == state and ("code" in q or "error" in q):
                got.update(q)
                body = b"<html><body style='font-family:sans-serif'><h3>DriverDirector is connected to YouTube.</h3>You can close this tab.</body></html>" \
                    if "code" in q else b"<html><body>Sign-in was cancelled.</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
                done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
        announce(url)
        open_browser(url)
        if not done.wait(timeout):
            raise YouTubeError("timed out waiting for the YouTube sign-in. " + SIGN_IN_HINT)
    finally:
        srv.shutdown()
        srv.server_close()
    if "code" not in got:
        raise YouTubeError(f"sign-in was not completed ({got.get('error', 'no code')}). " + SIGN_IN_HINT)
    tok = _json("POST", client.get("token_uri") or TOKEN_URL, form={
        "code": got["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if "refresh_token" not in tok:
        raise YouTubeError("Google returned no refresh token; remove DriverDirector at "
                           "myaccount.google.com/permissions and connect again")
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    tok["client_id"] = client["client_id"]        # a token only works with the client it was made for
    _save_token(tok)
    return {"authorized": True, "channel": channel_name(cfg)}


def access_token(cfg: YouTubeSettings) -> str:
    tok = _load_token()
    if not tok or "refresh_token" not in tok:
        raise YouTubeError("YouTube is not connected; press 'Connect YouTube' first")
    if tok.get("expires_at", 0) - 60 > time.time():
        return tok["access_token"]
    client = load_client(cfg)
    new = _json("POST", client.get("token_uri") or TOKEN_URL, form={
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    tok.update(new)
    tok["expires_at"] = time.time() + int(new.get("expires_in", 3600))
    _save_token(tok)
    return tok["access_token"]


def _auth(cfg: YouTubeSettings) -> dict:
    return {"Authorization": f"Bearer {access_token(cfg)}"}


def channel_name(cfg: YouTubeSettings) -> Optional[str]:
    """The channel uploads will go to, so the person can see which account is connected."""
    try:
        r = _json("GET", f"{API}/channels?part=snippet&mine=true", headers=_auth(cfg))
        return (r.get("items") or [{}])[0].get("snippet", {}).get("title")
    except YouTubeError:
        return None


def status(cfg: YouTubeSettings) -> dict:
    """Where the setup stands, with the one thing to do next."""
    client: dict = {}
    try:
        client = load_client(cfg)
        configured, problem = True, ""
    except YouTubeError as e:
        configured, problem = False, str(e)
    tok = _load_token()
    # A token made with another OAuth client (the client file was replaced) no longer refreshes.
    stale = bool(tok and client and tok.get("client_id") not in (None, client.get("client_id")))
    authorized = tok is not None and not stale
    can_edit = authorized and EDIT_SCOPE in (tok.get("scope") or "").split()
    channel = channel_name(cfg) if configured and authorized else None
    if not configured:
        nxt = "Do the one-time Google setup (setup_steps), then run `dd youtube setup`."
    elif not authorized:
        nxt = ("Run `dd youtube connect` and approve in your browser within 5 minutes" +
               (" (the saved sign-in belongs to a different client file)" if stale else "") +
               f". The project '{client.get('project_id', '?')}' must list your Google account as a Test user.")
    elif channel is None:
        nxt = "Connected, but the channel could not be read: run `dd youtube connect` again. " + SIGN_IN_HINT
    elif not can_edit:
        nxt = ("Connected for uploads only. To let DriverDirector change descriptions or visibility, "
               "run `dd youtube connect` again and approve the extra permission.")
    else:
        nxt = "Ready."
    return {"configured": configured, "problem": problem, "authorized": authorized, "channel": channel,
            "project_id": client.get("project_id"), "can_edit": can_edit, "next_step": nxt,
            **({"setup_steps": SETUP_STEPS} if not configured else {})}


def disconnect() -> bool:
    try:
        token_path().unlink()
        return True
    except OSError:
        return False


# ---- metadata ------------------------------------------------------------------------------------
def clock(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def draft_metadata(cfg: YouTubeSettings, driver: str, track: str, session: str, moments: list[tuple[float, str]],
                   length: float, finish: str = "", music: bool = True, voice: bool = True,
                   game: str = "Le Mans Ultimate") -> dict:
    """Title, description (with chapters) and tags for a video. `moments` are (seconds into the video, label)."""
    title = cfg.title_template.format(driver=driver, track=track, session=session or "Race")
    title = title.replace("<", "").replace(">", "")[:100].strip()
    lines = [f"{driver} in the {session or 'race'} at {track}, directed automatically from the replay in {game}."
             + (f" {finish}" if finish else "")]
    chapters = [(0.0, "Start")] + [(t, l.capitalize()) for t, l in sorted(moments) if t >= 10]
    chapters = [c for i, c in enumerate(chapters) if i == 0 or c[0] - chapters[i - 1][0] >= 10]
    if len(chapters) >= 3:                                  # YouTube only makes chapters from three or more
        lines += ["", "Chapters"] + [f"{clock(t)} {l}" for t, l in chapters]
    made = [w for w, on in (("The commentary voice", voice), ("the background music", music)) if on]
    ai = (f"{made[0]} and {made[1]} are AI-generated." if len(made) == 2 else
          f"{made[0][0].upper()}{made[0][1:]} is AI-generated." if made else "")
    lines += [""] + ([ai] if ai else []) + ["Made with DriverDirector."]
    if cfg.description_extra:
        lines += ["", cfg.description_extra]
    desc = "\n".join(lines).replace("<", "").replace(">", "")
    return {"title": title, "description": desc[:4900], "tags": list(cfg.tags),
            "privacy": cfg.privacy, "length": round(length)}


# ---- upload --------------------------------------------------------------------------------------
def upload(cfg: YouTubeSettings, path: str, title: str, description: str, privacy: str = "private",
           tags: list[str] | None = None, progress: Callable[[int, int], None] = lambda done, total: None,
           chunk: int = CHUNK, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Upload `path` with a resumable session (survives dropped connections). Returns {"id", "url", "privacy"}."""
    if privacy not in PRIVACY:
        raise YouTubeError(f"privacy must be one of {PRIVACY}")
    p = Path(path)
    if not p.is_file():
        raise YouTubeError(f"video not found: {path}")
    size = p.stat().st_size
    status_body = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(cfg.made_for_kids)}
    if cfg.synthetic_media_flag:
        status_body["containsSyntheticMedia"] = True
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags or [],
                        "categoryId": cfg.category_id}, "status": status_body}
    ctype = mimetypes.guess_type(path)[0] or "video/mp4"

    def start(m: dict) -> str:
        h = {**_auth(cfg), "Content-Type": "application/json; charset=UTF-8",
             "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": ctype}
        st, hd, raw = _request("POST", f"{UPLOAD}?uploadType=resumable&part=snippet,status", h, json.dumps(m).encode())
        if st >= 400:
            raise YouTubeError(f"{st}: {raw[:400].decode('utf-8', 'replace')}")
        if "location" not in hd:
            raise YouTubeError("YouTube did not return an upload address")
        return hd["location"]

    try:
        session = start(meta)
    except YouTubeError as e:
        if "containsSyntheticMedia" not in str(e):
            raise
        status_body.pop("containsSyntheticMedia")           # this API version does not know the field
        session = start(meta)

    sent, failures = 0, 0
    with p.open("rb") as f:
        while True:
            f.seek(sent)
            data = f.read(chunk)
            end = sent + len(data) - 1
            h = {**_auth(cfg), "Content-Length": str(len(data)), "Content-Type": ctype,
                 "Content-Range": f"bytes {sent}-{end}/{size}" if data else f"bytes */{size}"}
            try:
                st, hd, raw = _request("PUT", session, h, data, timeout=600.0)
            except (OSError, http.client.HTTPException):
                st, hd, raw = 599, {}, b""
            if st in (200, 201):
                vid = json.loads(raw)
                progress(size, size)
                return {"id": vid["id"], "url": f"https://youtu.be/{vid['id']}",
                        "privacy": vid.get("status", {}).get("privacyStatus", privacy)}
            if st == 308:                                   # chunk accepted; the server says how far it got
                failures = 0
                rng = hd.get("range", "")
                sent = int(rng.split("-")[1]) + 1 if "-" in rng else 0
                progress(sent, size)
                continue
            if st in (401, 403) and b"quota" in raw.lower():
                raise YouTubeError("YouTube's daily upload quota is used up; try again tomorrow")
            if st >= 500 or st == 599 or st == 401:
                failures += 1
                if failures > 8:
                    raise YouTubeError(f"upload kept failing ({st}); run it again to retry")
                sleep(min(60, 2 ** failures))
                st2, hd2, raw2 = _request("PUT", session, {**_auth(cfg), "Content-Length": "0",
                                                           "Content-Range": f"bytes */{size}"}, b"")
                if st2 in (200, 201):                       # it had actually finished
                    vid = json.loads(raw2)
                    progress(size, size)
                    return {"id": vid["id"], "url": f"https://youtu.be/{vid['id']}",
                            "privacy": vid.get("status", {}).get("privacyStatus", privacy)}
                if st2 == 308:                              # ask where the server is, and carry on from there
                    rng = hd2.get("range", "")
                    sent = int(rng.split("-")[1]) + 1 if "-" in rng else 0
                continue
            raise YouTubeError(f"{st}: {raw[:400].decode('utf-8', 'replace')}")
