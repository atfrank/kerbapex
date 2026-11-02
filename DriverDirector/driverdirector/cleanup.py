"""Keeps the recordings folder tidy.

OBS writes every recording (the main pass and each picture-in-picture clip) under its own default name, and a long
race is 14 GB of raw footage. Only the finished video is wanted afterwards. Nothing here deletes anything outright:

  sweep()    moves intermediate recordings into `<folder>/_cleanup/<timestamp>/` (still recoverable);
  purge()    deletes quarantined batches older than N days (the only place anything is really removed).

A finished video is recognised by its name (`..._directed`, `..._commentary`, or the app's own
`<track>_<date>_<session>_<driver>` scheme) and is never swept.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Iterable

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".flv", ".webm"}
FINAL_RE = re.compile(r"(_directed|_commentary)( \(\d+\))?\.\w+$|_\d{4}-\d{2}-\d{2}_")
QUARANTINE = "_cleanup"


def _key(p: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def is_final(p: str | Path) -> bool:
    return bool(FINAL_RE.search(Path(p).name))


def candidates(out_dir: str | Path, protect: Iterable[str | Path] = ()) -> list[dict]:
    """Video files in the folder that are not finished videos and not in `protect`."""
    d = Path(out_dir)
    keep = {_key(x) for x in protect if x}
    out = []
    for p in sorted(d.iterdir()) if d.is_dir() else []:
        if p.is_dir() or p.suffix.lower() not in VIDEO_EXT or is_final(p) or _key(p) in keep:
            continue
        st = p.stat()
        out.append({"path": str(p), "size": st.st_size, "modified": st.st_mtime})
    return out


def sweep(paths: Iterable[str | Path], out_dir: str | Path) -> list[str]:
    """Move the files into a timestamped quarantine folder. Returns the new locations."""
    dest = Path(out_dir) / QUARANTINE / time.strftime("%Y%m%d-%H%M%S")
    moved = []
    for p in map(Path, paths):
        if not p.is_file() or is_final(p):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / p.name
        n = 2
        while target.exists():
            target = dest / f"{p.stem} ({n}){p.suffix}"
            n += 1
        shutil.move(str(p), str(target))
        moved.append(str(target))
    return moved


def purge(out_dir: str | Path, older_than_days: float = 7.0) -> list[str]:
    """Delete quarantined batches older than `older_than_days` (0 = everything quarantined). Returns what went."""
    q = Path(out_dir) / QUARANTINE
    gone = []
    for batch in sorted(q.iterdir()) if q.is_dir() else []:
        if batch.is_dir() and (time.time() - batch.stat().st_mtime) >= older_than_days * 86400:
            shutil.rmtree(batch, ignore_errors=True)
            gone.append(str(batch))
    if q.is_dir() and not any(q.iterdir()):
        q.rmdir()
    return gone


def quarantined_size(out_dir: str | Path) -> int:
    q = Path(out_dir) / QUARANTINE
    return sum(f.stat().st_size for f in q.rglob("*") if f.is_file()) if q.is_dir() else 0
