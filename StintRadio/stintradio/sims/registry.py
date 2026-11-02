"""Finds the sim adapters and opens the right one.

Where adapters come from, in this order (a later one with the same id replaces an earlier one):
  1. the built-in ones (raceroom, lmu) and the demo,
  2. Python packages that declare an entry point in the group `stintradio.sims`,
  3. single .py files dropped into %APPDATA%\\StintRadio\\adapters (no install step; `python -m stintradio.sims new <id>` makes one).
A broken adapter never stops the others: its error is kept in `errors` and shown by `python -m stintradio.sims list`."""
from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
from typing import Optional

from .base import SimAdapter

_adapters: dict[str, type] = {}
errors: list[str] = []
_loaded = False


def adapters_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "StintRadio" / "adapters"


def register(cls: type) -> type:
    """Add an adapter class (usable as a decorator)."""
    if not (inspect.isclass(cls) and issubclass(cls, SimAdapter) and cls.id):
        raise TypeError("an adapter is a SimAdapter subclass with an id")
    _adapters[cls.id] = cls
    return cls


def _classes_in(module) -> list[type]:
    return [c for _, c in inspect.getmembers(module, inspect.isclass)
            if issubclass(c, SimAdapter) and c is not SimAdapter and c.id and c.__module__ == module.__name__]


def discover(force: bool = False) -> dict[str, type]:
    global _loaded
    if _loaded and not force:
        return _adapters
    _loaded = True
    _adapters.clear()
    errors.clear()
    from .builtin import LMUAdapter, RaceRoomAdapter, SessionReplayAdapter
    from .demo import DemoAdapter
    for c in (RaceRoomAdapter, LMUAdapter, SessionReplayAdapter, DemoAdapter):
        register(c)
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="stintradio.sims"):
            try:
                obj = ep.load()
                for c in ([obj] if inspect.isclass(obj) else _classes_in(obj)):
                    register(c)
            except Exception as e:                                      # a bad plug-in is reported, not fatal
                errors.append(f"entry point {ep.name}: {type(e).__name__}: {e}")
    except Exception as e:
        errors.append(f"entry points: {e}")
    d = adapters_dir()
    for f in sorted(d.glob("*.py")) if d.is_dir() else []:
        try:
            for c in load_file(f):
                register(c)
        except Exception as e:
            errors.append(f"{f.name}: {type(e).__name__}: {e}")
    return _adapters


def load_file(path) -> list[type]:
    """The adapter classes defined in one .py file."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"stintradio_adapter_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    found = _classes_in(mod)
    if not found:
        raise ValueError("no SimAdapter subclass with an id in this file")
    return found


def get(sim_id: str) -> type:
    a = discover()
    if sim_id not in a:
        raise KeyError(f"unknown sim '{sim_id}' (installed: {', '.join(sorted(a))})")
    return a[sim_id]


def detect(cfg=None) -> Optional[type]:
    """The first auto-detectable adapter whose live data is there now (built-ins first)."""
    for c in discover().values():
        if c.auto_detect and _safe_available(c, cfg):
            return c
    return None


def _safe_available(c, cfg) -> bool:
    try:
        return bool(c.available(cfg))
    except Exception as e:
        errors.append(f"{c.id}.available(): {type(e).__name__}: {e}")
        return False


def any_available(cfg=None) -> bool:
    want = getattr(cfg, "game", "auto") or "auto"
    if want != "auto":
        return want in discover() and _safe_available(discover()[want], cfg)
    return detect(cfg) is not None


def open_sim(cfg=None, **opts) -> SimAdapter:
    """The adapter for live.game (an id, or "auto" = whichever sim's data is there now). Raises OSError with a clear
    message when there is none."""
    want = getattr(cfg, "game", "auto") or "auto"
    if want != "auto":
        if want not in discover():
            raise OSError(f"live.game is '{want}' but there is no such sim adapter (installed: {', '.join(sorted(discover()))})")
        return discover()[want].open(cfg, **opts)
    c = detect(cfg)
    if c is None:
        hints = " or ".join(a.hint().rstrip(".") for a in discover().values() if a.auto_detect)
        raise OSError(f"No supported sim was found. {hints}.")
    return c.open(cfg, **opts)


def listing() -> list[dict]:
    return [dict(c.describe(), contract=c.contract, auto_detect=c.auto_detect, running=_safe_available(c, None)) for c in discover().values()]
