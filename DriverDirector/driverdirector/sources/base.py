from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional, Protocol

from ..models import Frame


class Source(Protocol):
    def poll(self) -> Optional[Frame]: ...
    def close(self) -> None: ...


class FrameLog:
    """JSONL capture of frames: the 'high-fidelity live capture' equivalent.
    One scan of a replay is written here, then detection/planning work from the file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, frames: Iterable[Frame]) -> int:
        n = 0
        with self.path.open("w", encoding="utf-8") as f:
            for fr in frames:
                f.write(json.dumps(fr.to_json(), separators=(",", ":")) + "\n")
                n += 1
        return n

    def read(self) -> list[Frame]:
        with self.path.open(encoding="utf-8") as f:
            return [Frame.from_json(json.loads(line)) for line in f if line.strip()]
