"""Set-up and technique suggestions worked out from a recorded session (read-only): brake bias from wheel lock-ups,
shift timing from the revs at each full-throttle upshift, and minimum speeds in the slow corners.

Everything comes from what was measured; a suggestion is only made when the numbers show it. The brake-bias advice says
which way to move it (towards the rear or the front), never a value, so it does not depend on how the game scales it."""
from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path
from typing import Optional

RPS_TO_RPM = 60.0 / (2 * 3.141592653589793)
COLUMNS = ("t", "completed_laps", "lap_distance", "layout_length", "lap_time_current_self", "car_speed", "engine_rps",
           "max_engine_rps", "upshift_rps", "gear", "throttle_raw", "brake_raw", "steer_input_raw", "in_pitlane",
           "tire_rps_0", "tire_rps_1", "tire_rps_2", "tire_rps_3", "abs_setting")
BRAKING = 0.25            # brake pedal (0..1) that counts as braking
LOCK_SLIP = 0.12          # a wheel turning this much slower than the car is going: locked or sliding
BIN_M = 10.0


def _frames(path: Path) -> list[dict]:
    db = sqlite3.connect(str(path), timeout=5)
    try:
        have = {r[1] for r in db.execute("PRAGMA table_info(frames)")}
        cols = [c for c in COLUMNS if c in have]
        rows = db.execute(f"SELECT {','.join(cols)} FROM frames ORDER BY t").fetchall()
    finally:
        db.close()
    return [dict(zip(cols, r)) for r in rows]


def lap_groups(frames: list[dict]) -> dict[int, dict]:
    """Every lap in the session: its frames, its time (the recorder's own clock; the game's timer resets on a cut) and
    whether it is clean (all the way round, never in the pit lane)."""
    groups: dict[int, list[dict]] = {}
    for f in frames:
        if f.get("lap_distance") is None or f["lap_distance"] < 0:
            continue
        groups.setdefault(int(f["completed_laps"]), []).append(f)
    out = {}
    for k, g in groups.items():
        length = max(f.get("layout_length") or 0 for f in g)
        span = max(f["lap_distance"] for f in g) - min(f["lap_distance"] for f in g)
        pits = any(f.get("in_pitlane") == 1 for f in g)
        out[k] = {"frames": g, "time": g[-1]["t"] - g[0]["t"],
                  "clean": bool(length and span >= 0.95 * length and not pits and len(g) >= 100)}
    return out


def _laps(frames: list[dict]) -> dict[int, list[dict]]:
    """The clean laps, within 8 % of the quickest."""
    groups = {k: v for k, v in lap_groups(frames).items() if v["clean"]}
    if not groups:
        return {}
    best = min(v["time"] for v in groups.values())
    return {k: v["frames"] for k, v in groups.items() if v["time"] <= best * 1.08}


# ---- brake bias ------------------------------------------------------------------------------------------------------
def calibration(frames: list[dict]) -> Optional[list[float]]:
    """Each wheel's speed per unit of car speed while rolling straight with the pedals off, from the whole session, so tyre
    size does not matter. None until there is enough of it."""
    cal = [[] for _ in range(4)]
    for f in frames:
        v = f.get("car_speed") or 0
        if v > 15 and (f.get("brake_raw") or 0) < 0.01 and (f.get("throttle_raw") or 0) < 0.5 and abs(f.get("steer_input_raw") or 0) < 0.05:
            for k in range(4):
                w = abs(f.get(f"tire_rps_{k}") or 0)
                if w:
                    cal[k].append(w / v)
    if any(len(c) < 50 for c in cal):
        return None
    return [statistics.median(c) for c in cal]


def lock_counts(frames: list[dict], base: list[float]) -> dict:
    """Braking zones in `frames` and how many had the front or the rear wheels locked or sliding."""
    zones, front, rear = 0, 0, 0
    run: list[dict] = []

    def close():
        nonlocal zones, front, rear
        if len(run) >= 5:
            zones += 1
            f_run = r_run = 0
            f_hit = r_hit = False
            for f in run:
                v = f["car_speed"]
                sf = 1 - (abs(f["tire_rps_0"]) / base[0] + abs(f["tire_rps_1"]) / base[1]) / 2 / v
                sr = 1 - (abs(f["tire_rps_2"]) / base[2] + abs(f["tire_rps_3"]) / base[3]) / 2 / v
                f_run = f_run + 1 if sf > LOCK_SLIP else 0
                r_run = r_run + 1 if sr > LOCK_SLIP else 0
                f_hit |= f_run >= 2
                r_hit |= r_run >= 2
            front += f_hit
            rear += r_hit
    for f in frames:
        braking = ((f.get("brake_raw") or 0) > BRAKING and (f.get("car_speed") or 0) > 14 and f.get("in_pitlane") != 1
                   and abs(f.get("steer_input_raw") or 0) < 0.25          # cornering makes the axles turn at different rates
                   and all(f.get(f"tire_rps_{k}") for k in range(4)))
        if braking:
            run.append(f)
        else:
            close()
            run = []
    close()
    return {"zones": zones, "front": front, "rear": rear}


def brake_bias(frames: list[dict]) -> dict:
    """Wheel speed against car speed while braking, over the whole session."""
    base = calibration(frames)
    if base is None:
        return {"zones": 0}
    out = lock_counts(frames, base)
    out["abs"] = any((f.get("abs_setting") or 0) > 0 for f in frames[:200])
    return out


def bias_direction(b: dict) -> Optional[str]:
    """"rear" if the fronts are locking much more than the rears (move the bias rearward), "front" for the reverse."""
    z = b.get("zones", 0)
    if z < 10:
        return None
    f, r = b["front"], b["rear"]
    if f / z >= 0.25 and f >= 2 * r:
        return "rear"
    if r / z >= 0.15 and r >= 2 * f:
        return "front"
    return None


def _bias_line(b: dict) -> str:
    z = b.get("zones", 0)
    if z < 12:
        return "Brake bias: not enough heavy braking recorded yet to judge."
    f, r, pct_f, pct_r = b["front"], b["rear"], b["front"] / z, b["rear"] / z
    note = " (the ABS working shows up here too)" if b.get("abs") else ""
    if pct_f >= 0.25 and f >= 2 * r:
        return (f"Brake bias: the front wheels locked or slid in {f} of {z} braking zones and the rears in {r}{note}. "
                "Move the bias towards the rear, a click or two, and see if the fronts stay alive. This is read from wheel speeds, so trust the feel of the car over it.")
    if pct_r >= 0.15 and r >= 2 * f:
        return (f"Brake bias: the rear wheels locked or slid in {r} of {z} braking zones and the fronts in {f}{note}. "
                "Move the bias towards the front a click or two. This is read from wheel speeds, so trust the feel of the car over it.")
    return (f"Brake bias looks fine: the fronts slid in {f} and the rears in {r} of {z} braking zones{note}. "
            "Leave it unless the car feels nervous or pushes under braking.")


# ---- shift timing ----------------------------------------------------------------------------------------------------
def shifting(frames: list[dict]) -> dict:
    """Revs just before each full-throttle upshift, against the game's own shift point and the limiter."""
    rows = []
    for i in range(3, len(frames)):
        a, b = frames[i - 1], frames[i]
        g0, g1 = a.get("gear") or 0, b.get("gear") or 0
        if g0 < 1 or g1 != g0 + 1 or b.get("in_pitlane") == 1:
            continue
        prev = frames[i - 3:i]
        if min((p.get("throttle_raw") or 0) for p in prev) < 0.9:
            continue
        top = max(p["engine_rps"] for p in prev)
        lim = b.get("max_engine_rps") or 0
        sp = a.get("upshift_rps") or 0
        if not lim or not sp:
            continue
        hit = sum(1 for p in prev if p["engine_rps"] >= 0.995 * lim) >= 2
        rows.append({"gear": g0, "rpm": top * RPS_TO_RPM, "diff": (top - sp) * RPS_TO_RPM, "limiter": hit,
                     "point": sp * RPS_TO_RPM})
    if len(rows) < 10:
        return {"shifts": len(rows)}
    per = {}
    for r in rows:
        per.setdefault(r["gear"], []).append(r["diff"])
    return {"rows": rows, "shifts": len(rows), "mean_diff": statistics.mean(r["diff"] for r in rows),
            "limiter": sum(r["limiter"] for r in rows), "point": statistics.median(r["point"] for r in rows),
            "per_gear": {g: round(statistics.mean(d)) for g, d in sorted(per.items()) if len(d) >= 3}}


def shift_plan(frames: list[dict]) -> list[dict]:
    """The best upshift point per gear, from the car's own acceleration: at full throttle in a straight line, how hard it
    pulls (change of speed per second) at each speed in each gear. The best time to change up is the speed at which the
    next gear pulls at least as hard as this one. Needs speeds where both gears were used, which your own early and late
    shifts provide. Each entry: gear, best_rpm, your_rpm (median of your shifts), shifts."""
    acc: dict[int, dict[int, list[float]]] = {}
    ratio: dict[int, list[float]] = {}
    for i in range(1, len(frames)):
        a, b = frames[i - 1], frames[i]
        g = a.get("gear") or 0
        if g < 1 or b.get("gear") != g or a.get("in_pitlane") == 1:
            continue
        if min(a.get("throttle_raw") or 0, b.get("throttle_raw") or 0) < 0.95 or max(a.get("brake_raw") or 0, b.get("brake_raw") or 0) > 0.01:
            continue
        if abs(a.get("steer_input_raw") or 0) > 0.15 or abs(b.get("steer_input_raw") or 0) > 0.15:
            continue
        dt = b["t"] - a["t"]
        v = (a["car_speed"] + b["car_speed"]) / 2
        if not 0.05 < dt < 0.2 or v < 5:
            continue
        acc.setdefault(g, {}).setdefault(int(v * 3.6 // 3), []).append((b["car_speed"] - a["car_speed"]) / dt)
        if a.get("engine_rps"):
            ratio.setdefault(g, []).append(a["engine_rps"] * RPS_TO_RPM / v)
    mine: dict[int, list[float]] = {}
    for r in shifting(frames).get("rows", []):
        mine.setdefault(r["gear"], []).append(r["rpm"])
    top = max((f.get("max_engine_rps") or 0) for f in frames[:200]) * RPS_TO_RPM
    out = []
    for g in sorted(acc):
        if g + 1 not in acc or g not in ratio or len(mine.get(g, [])) < 5:
            continue
        both = sorted(set(acc[g]) & set(acc[g + 1]))
        both = [k for k in both if len(acc[g][k]) >= 4 and len(acc[g + 1][k]) >= 4]
        cross = None
        for j, k in enumerate(both):
            ahead = both[j:j + 2]
            if all(statistics.mean(acc[g + 1][x]) >= statistics.mean(acc[g][x]) for x in ahead):
                cross = k
                break
        if cross is None:
            continue
        best = (cross * 3 + 1.5) / 3.6 * statistics.median(ratio[g])
        if top:
            best = min(best, top * 0.995)
        out.append({"gear": g, "best_rpm": round(best, -1), "your_rpm": round(statistics.median(mine[g]), -1), "shifts": len(mine[g])})
    return out


def _plan_lines(plan: list[dict]) -> list[str]:
    off = [p for p in plan if abs(p["your_rpm"] - p["best_rpm"]) >= 300]
    lines = []
    for p in sorted(off, key=lambda p: -abs(p["your_rpm"] - p["best_rpm"]))[:3]:
        ordinal = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh"][p["gear"]] if p["gear"] < 8 else str(p["gear"])
        if p["your_rpm"] < p["best_rpm"]:
            lines.append(f"Shift point, {ordinal} gear: the car pulls hardest until about {p['best_rpm']:.0f} rpm and you change up at "
                         f"{p['your_rpm']:.0f}. Hold it longer.")
        else:
            lines.append(f"Shift point, {ordinal} gear: the next gear takes over at about {p['best_rpm']:.0f} rpm and you hold it to "
                         f"{p['your_rpm']:.0f}. Change up sooner.")
    if plan and not off:
        lines.append("Shift points: every gear is within 300 rpm of where the car pulls hardest.")
    return lines


def _shift_line(s: dict) -> str:
    n = s.get("shifts", 0)
    if n < 10:
        return "Shift timing: not enough full-throttle upshifts recorded yet to judge."
    d, lim, pt = s["mean_diff"], s["limiter"], round(s["point"], -1)
    if lim >= max(3, 0.15 * n):
        return (f"Shift timing: you bounced off the limiter on {lim} of {n} upshifts. Shift about "
                f"{round(abs(min(d, -100)), -1):.0f} rpm sooner, near the game's shift point of {pt:.0f}.")
    if d < -400:
        return (f"Shift timing: on average you change up {abs(round(d, -1)):.0f} rpm below the game's shift point "
                f"({pt:.0f} rpm), so you're leaving the top of the power unused. Hold each gear a little longer.")
    if d > 300:
        return (f"Shift timing: you change up about {round(d, -1):.0f} rpm past the game's shift point ({pt:.0f} rpm), "
                "into the part where the engine is already tailing off. Shift a touch sooner.")
    return f"Shift timing is good: within {abs(round(d, -1)):.0f} rpm of the game's shift point ({pt:.0f} rpm) on average."


# ---- minimum speeds --------------------------------------------------------------------------------------------------
def _bins(lap: list[dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for f in lap:
        k = int(f["lap_distance"] // BIN_M)
        v = (f.get("car_speed") or 0) * 3.6
        out[k] = min(out.get(k, v), v)
    return out


def _slow_points(bins: dict[int, float]) -> list[int]:
    """Bins that are the slowest within 120 m either side and at least 25 km/h under the fastest (not at the lap's seam) within 400 m."""
    if not bins:
        return []
    last, found = max(bins), []
    at = lambda k: bins.get(k)
    for k in range(last + 1):
        v = at(k)
        if v is None:
            continue
        near = [at(j) for j in range(max(0, k - 12), min(last, k + 12) + 1) if at(j) is not None]
        far = [at(j) for j in range(max(0, k - 40), min(last, k + 40) + 1) if at(j) is not None]
        if v <= min(near) and max(far) - v >= 25 and 5 <= k <= last - 5:
            if not found or k - found[-1] > 12:
                found.append(k)
    return found


def min_speeds(laps: dict[int, list[dict]]) -> list[dict]:
    """For every slow corner of the quickest lap: its minimum speed on each lap, so the spread shows what is possible."""
    if len(laps) < 3:
        return []
    best = min(laps, key=lambda k: laps[k][-1]["t"] - laps[k][0]["t"])
    points = _slow_points(_bins(laps[best]))
    per_lap = {k: _bins(g) for k, g in laps.items()}
    out = []
    for n, p in enumerate(points, 1):
        mins = []
        for k, b in per_lap.items():
            win = [b[j] for j in range(p - 6, p + 7) if j in b]
            if win:
                mins.append(min(win))
        if len(mins) >= 3:
            out.append({"corner": n, "at_m": int(p * BIN_M), "best": round(max(mins)), "mean": round(statistics.mean(mins)),
                        "lowest": round(min(mins)), "laps": len(mins)})
    return out


def _speed_lines(corners: list[dict]) -> list[str]:
    worst = sorted((c for c in corners if c["best"] - c["mean"] >= 4), key=lambda c: c["mean"] - c["best"])[:4]
    if not corners:
        return ["Minimum speeds: need at least three clean laps."]
    if not worst:
        return ["Minimum speeds are consistent: every slow corner is within 4 km/h of your best lap, on average."]
    return [f"Minimum speed, slow point {c['corner']} ({c['at_m']} m round the lap): your best lap carried {c['best']} km/h "
            f"through the middle, you average {c['mean']} (lowest {c['lowest']}). That speed is there; aim to hold it."
            for c in worst]


# ---- the lot ---------------------------------------------------------------------------------------------------------
def analyse(path: Path) -> dict:
    frames = _frames(path)
    if len(frames) < 300:
        return {"lines": ["Not enough recorded driving yet. Do a few laps first."], "frames": len(frames)}
    laps = _laps(frames)
    b, s, c, plan = brake_bias(frames), shifting(frames), min_speeds(laps), shift_plan(frames)
    s = {k: v for k, v in s.items() if k != "rows"}
    return {"session": path.name, "laps": len(laps), "brake_bias": b, "shifting": s, "shift_plan": plan, "min_speeds": c,
            "lines": [_bias_line(b), _shift_line(s)] + _plan_lines(plan) + _speed_lines(c)}


def latest(folder: Optional[Path] = None) -> Optional[Path]:
    from .recorder import latest_session
    return latest_session(folder)
