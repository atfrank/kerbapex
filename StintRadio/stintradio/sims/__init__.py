"""Sim adapters: the plug-in boundary between a racing sim and KerbApex. See docs/ADAPTERS.md."""
from . import contract as C
from .base import SimAdapter
from .contract import CONTRACT_VERSION, Sample
from .registry import any_available, detect, discover, get, listing, open_sim, register

__all__ = ["C", "SimAdapter", "Sample", "CONTRACT_VERSION", "register", "discover", "get", "detect", "any_available", "open_sim", "listing"]
