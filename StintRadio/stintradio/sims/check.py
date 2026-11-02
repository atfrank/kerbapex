"""Conformance check: runs an adapter against the contract and says what is wrong, what works and what is switched off."""
from __future__ import annotations

import time

from . import contract as C
from .base import SimAdapter


def check(adapter: SimAdapter, seconds: float = 3.0, hz: float = 10.0, speed_up_ok: bool = True) -> dict:
    """-> {"errors": [...], "warnings": [...], "ok": [...], "samples": n}. Errors break the contract; warnings are
    things that will work badly."""
    errors, warnings, ok = [], [], []
    caps = set(adapter.capabilities)
    if C.SAMPLE not in caps:
        errors.append("capabilities must include 'sample'")
    if not adapter.id or not adapter.name:
        errors.append("id and name must be set")
    if getattr(adapter, "contract", C.CONTRACT_VERSION) != C.CONTRACT_VERSION:
        warnings.append(f"written for contract {adapter.contract}, this is {C.CONTRACT_VERSION}")

    samples, problems = [], {}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s = adapter.sample()
        if s is not None:
            samples.append(s)
            for p in C.check_sample(s):
                problems[p] = problems.get(p, 0) + 1
        time.sleep(1.0 / hz)
    if not samples:
        errors.append("sample() returned None for the whole check: is the car on track (drive, then run it again)?")
    for p, n in problems.items():
        errors.append(f"sample: {p} (x{n})")
    if samples and not problems:
        ok.append(f"{len(samples)} samples, all inside the contract")
        if any(b.t < a.t - 1e-6 for a, b in zip(samples, samples[1:])):
            errors.append("sample.t went backwards")
        if len({s.track for s in samples}) > 1 or len({s.car for s in samples}) > 1:
            warnings.append("track or car changed during the check")
        if len({round(s.x, 1) for s in samples}) <= 1 and max(s.speed for s in samples) > 5:
            warnings.append("x/z do not change while the car moves: the track map needs real world positions")
        if all(s.delta_best == 0 for s in samples):
            warnings.append("delta_best is always 0: the overlay shows no delta unless the engine has a reference lap")
    if adapter.TURN_SIGN == 0:
        warnings.append("TURN_SIGN is 0: left/right turn names are worked out from the first lap")

    for cap, fn, validator in ((C.SESSION_INFO, adapter.session_info, C.check_session_info),
                               (C.RACE_SNAPSHOT, adapter.race_snapshot, C.check_snapshot),
                               (C.RACE_GAPS, adapter.race_gaps, C.check_gaps),
                               (C.SETUP, adapter.setup, C.check_setup)):
        try:
            value = fn()
        except Exception as e:
            (errors if cap in caps else warnings).append(f"{cap}: raised {type(e).__name__}: {e}")
            continue
        for p in validator(value):
            errors.append(f"{cap}: {p}")
        if cap in caps and (value is None or value == {}):
            warnings.append(f"{cap} is declared but returned nothing just now (fine outside a session)")
        if cap not in caps and value not in (None, {}):
            warnings.append(f"{cap} returns data but is not in capabilities, so the engine will ignore it")
        if cap in caps and value not in (None, {}) and not any(e.startswith(f"{cap}:") for e in errors):
            ok.append(f"{cap}: fine")
    if C.RECORDING in caps and not hasattr(adapter, "last_frame"):
        errors.append("'recording' is declared but the adapter has no last_frame")
    off = C.lacking(caps)
    return {"errors": errors, "warnings": warnings, "ok": ok, "samples": len(samples),
            "switched_off": off}


def report(result: dict, adapter_name: str = "") -> str:
    out = [f"Adapter check: {adapter_name}"] if adapter_name else []
    out += [f"  ok       {m}" for m in result["ok"]]
    out += [f"  WARNING  {m}" for m in result["warnings"]]
    out += [f"  ERROR    {m}" for m in result["errors"]]
    if result["switched_off"]:
        out.append("  Features this sim does not provide (so they stay off):")
        out += [f"    - {v}" for v in result["switched_off"].values()]
    out.append("  RESULT   " + ("FAILED" if result["errors"] else "passed"))
    return "\n".join(out)
