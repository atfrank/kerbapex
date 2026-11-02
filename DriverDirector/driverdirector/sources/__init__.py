"""Telemetry sources. Each yields `Frame`s; `poll()` returns the latest or None."""
from .base import Source, FrameLog
from .simulator import Simulator

__all__ = ["Source", "FrameLog", "Simulator"]
