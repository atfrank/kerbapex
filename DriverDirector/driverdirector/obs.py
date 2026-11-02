"""OBS Studio control over obs-websocket v5 (default ws://127.0.0.1:4455)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .config import ObsSettings


_NAMED = {"white": "ffffff", "black": "000000", "red": "e10600", "yellow": "ffd400", "orange": "ff9f1a",
          "green": "2fd67b", "blue": "1f6feb", "purple": "c05bff"}


def compose_color(c: str) -> str:
    """'white' / '#RRGGBB' -> 'RRGGBB' hex (OBS wants a number)."""
    c = (c or "white").strip().lower()
    if c in _NAMED:
        return _NAMED[c]
    c = c.lstrip("#").removeprefix("0x")
    return c if len(c) == 6 and all(ch in "0123456789abcdef" for ch in c) else "ffffff"


class DryRunObs:
    def __init__(self) -> None:
        self.log: list[tuple] = []

    def connect(self): self.log.append(("connect",))
    def set_scene(self, name): self.log.append(("scene", name))
    def start_record(self, directory=None): self.log.append(("start", directory))
    def stop_record(self) -> str: self.log.append(("stop",)); return ""
    def status(self) -> dict: return {"connected": False, "recording": False, "dry_run": True}

    def refresh_overlay(self, name="DriverDirector Overlay"): self.log.append(("refresh",))
    def set_item_enabled(self, scene, source, enabled): self.log.append(("item", source, enabled)); return True

    # live-compositing no-ops (recorded in `log` so tests can assert on them)
    def setup_pip(self, scene, files, size, corner, **kw):
        self.log.append(("setup_pip", list(files))); return [{"i": i} for i, _ in enumerate(files)]
    def pip_show(self, h, cursor_ms=0): self.log.append(("pip_show", h["i"], cursor_ms))
    def pip_hide(self, h): self.log.append(("pip_hide", h["i"]))
    def pip_cleanup(self, handles): self.log.append(("pip_cleanup",))


class Obs:
    def __init__(self, cfg: ObsSettings):
        self.cfg = cfg
        self._c = None

    def connect(self) -> None:
        import obsws_python as obs
        u = urlparse(self.cfg.url)
        self._c = obs.ReqClient(host=u.hostname or "127.0.0.1", port=u.port or 4455,
                                password=self.cfg.password, timeout=5)

    def _req(self):
        if self._c is None:
            self.connect()
        return self._c

    def set_scene(self, name: str) -> None:
        self._req().set_current_program_scene(name)

    def start_record(self, directory: str | None = None) -> None:
        c = self._req()
        d = directory or self.cfg.output_dir
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)
            c.set_record_directory(d)
        c.start_record()

    def stop_record(self) -> str:
        return self._req().stop_record().output_path

    def set_capture_crop(self, scene: str, source: str, top: float = 0.0, bottom: float = 0.0,
                         left: float = 0.0, right: float = 0.0) -> dict:
        """Crop the game capture inside `scene` (fractions of its size) and scale it to fill the
        canvas, hiding the game's replay UI strips. Only this scene item is changed."""
        c = self._req()
        item = c.get_scene_item_id(scene, source).scene_item_id
        t = c.get_scene_item_transform(scene, item).scene_item_transform
        sw, sh = t["sourceWidth"], t["sourceHeight"]
        v = c.get_video_settings()
        c.set_scene_item_transform(scene, item, {
            "cropTop": int(sh * top), "cropBottom": int(sh * bottom),
            "cropLeft": int(sw * left), "cropRight": int(sw * right),
            "boundsType": "OBS_BOUNDS_SCALE_OUTER", "boundsAlignment": 0,
            "boundsWidth": float(v.base_width), "boundsHeight": float(v.base_height),
            "positionX": v.base_width / 2, "positionY": v.base_height / 2, "alignment": 0,
        })
        return {"source": [sw, sh], "canvas": [v.base_width, v.base_height]}

    def set_item_enabled(self, scene: str, source: str, enabled: bool) -> bool:
        """Show or hide one source in a scene (e.g. the overlay for a cinematic film). False if it is not there."""
        c = self._req()
        try:
            item = c.get_scene_item_id(scene, source).scene_item_id
        except Exception:
            return False
        c.set_scene_item_enabled(scene, item, enabled)
        return True

    def screenshot(self, source: str, width: int = 960) -> bytes:
        import base64
        r = self._req().get_source_screenshot(source, "png", width, int(width * 9 / 21.5), -1)
        return base64.b64decode(r.image_data.split(",", 1)[1])

    # ---- live compositing (PiP clips + commentary played by OBS itself) ------------------
    PIP_PREFIX = "DriverDirector PiP"
    _RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
    _STOP = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"

    def _canvas(self) -> tuple[int, int]:
        v = self._req().get_video_settings()
        return v.base_width, v.base_height

    def _make_input(self, scene: str, name: str, kind: str, settings: dict) -> int:
        c = self._req()
        try:
            c.remove_input(name)                       # stale copy from an earlier run
        except Exception:
            pass
        return c.create_input(scene, name, kind, settings, False).scene_item_id

    def setup_pip(self, scene: str, files: list[str], size: float, corner: str, margin: int | None = None,
                  border: int | None = None, border_pct: float = 0.30, border_color: str = "white") -> list[dict]:
        """One hidden media source (+ white border) per clip, placed in `corner` of the canvas."""
        c = self._req()
        W, H = self._canvas()
        pw = int(W * size)
        ph = int(pw * H / W)
        border = border if border is not None else max(4, round(W * border_pct / 100))
        margin = margin if margin is not None else round(W * 0.012)
        rgb = int(compose_color(border_color), 16)
        x = margin if corner.endswith("left") else W - pw - margin
        y = margin if corner.startswith("top") else H - ph - margin
        handles = []
        for i, f in enumerate(files, start=1):
            bname, mname = f"{self.PIP_PREFIX} Border {i}", f"{self.PIP_PREFIX} {i}"
            b = self._make_input(scene, bname, "color_source_v3",
                                 {"color": 0xFF000000 | rgb, "width": pw + 2 * border, "height": ph + 2 * border})
            m = self._make_input(scene, mname, "ffmpeg_source",
                                 {"local_file": f, "looping": False, "close_when_inactive": False,
                                  "restart_on_activate": False, "is_local_file": True})
            c.set_scene_item_transform(scene, b, {"positionX": float(x - border), "positionY": float(y - border)})
            c.set_scene_item_transform(scene, m, {"positionX": float(x), "positionY": float(y), "alignment": 5,
                                                  "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
                                                  "boundsWidth": float(pw), "boundsHeight": float(ph)})
            handles.append({"scene": scene, "border": b, "media": m, "mname": mname, "bname": bname})
        return handles

    def pip_show(self, h: dict, cursor_ms: int = 0) -> None:
        c = self._req()
        c.set_scene_item_enabled(h["scene"], h["border"], True)
        c.set_scene_item_enabled(h["scene"], h["media"], True)
        c.trigger_media_input_action(h["mname"], self._RESTART)
        if cursor_ms > 0:
            c.set_media_input_cursor(h["mname"], cursor_ms)

    def pip_hide(self, h: dict) -> None:
        c = self._req()
        c.trigger_media_input_action(h["mname"], self._STOP)
        c.set_scene_item_enabled(h["scene"], h["media"], False)
        c.set_scene_item_enabled(h["scene"], h["border"], False)

    def pip_cleanup(self, handles: list[dict]) -> None:
        c = self._req()
        for h in handles:
            for n in (h["mname"], h["bname"]):
                try:
                    c.remove_input(n)
                except Exception:
                    pass

    def refresh_overlay(self, name: str = "DriverDirector Overlay") -> None:
        """Reload the overlay browser source without cache (picks up theme changes). Best effort."""
        try:
            self._req().press_input_properties_button(name, "refreshnocache")
        except Exception:
            pass

    def status(self) -> dict:
        try:
            r = self._req().get_record_status()
            return {"connected": True, "recording": r.output_active}
        except Exception as e:  # connection refused, auth failure...
            return {"connected": False, "recording": False, "error": str(e)}
