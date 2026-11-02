"""StintRadio's data for Claude: a small MCP server (stdio, JSON-RPC, no dependencies) over the recorded RaceRoom data stream
and the driver's saved laps. Read-only: sessions are opened read-only with SQLite's authoriser allowing only SELECT, and
nothing here talks to the game or the network.

Run by `python -m stintradio.mcp_server` (the plugin's .mcp.json starts it through scripts/sr_mcp.py)."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import laps as LP
from .r3e_fields import FIELDS
from .recorder import latest_session, sessions_dir

PROTOCOL = "2024-11-05"
MAX_ROWS = 200
MAX_CHARS = 12000

FIELD_NOTES = """Columns of the `frames` table (one row ~10 a second while driving; t = unix seconds):
- lap_distance (m round the lap), lap_distance_fraction, completed_laps, track_sector (1-3), lap_time_current_self (s),
  lap_time_previous_self, lap_time_best_self, time_delta_best_self (s, - = ahead of best), time_delta_front/behind (s),
  position, position_class, current_lap_valid, session_time_remaining (s), session_phase (5 green, 6 chequered), number_of_laps
- car_speed (m/s; x3.6 = km/h), gear (-1 reverse, 0 neutral), engine_rps (rad/s; x9.549 = rpm), max_engine_rps, upshift_rps
- throttle, throttle_raw, brake, brake_raw, clutch, steer_input_raw (-1..1) (all 0..1), brake_bias, abs_setting, tc settings
  (aid_settings_*, traction_control_*), pit_limiter, engine_map_setting
- fuel_left, fuel_capacity, fuel_per_lap (litres); engine_temp, engine_oil_temp (C); oil/fuel pressure; turbo_pressure
- tire_wear_0..3, tire_temp_<i>_current_temp_<0..2> (inner/middle/outer), tire_pressure_0..3, tire_grip_0..3, tire_dirt_0..3,
  brake_temp_<i>_current_temp, tire_load_0..3  (tyre index 0 FL, 1 FR, 2 RL, 3 RR)
- player_position_x/y/z (m), player_local_g_force_x/y/z (m/s^2: x sideways, z along), player_steering_force,
  player_steering_force_percentage (force feedback output, 1.0 = full scale), player_game_simulation_time (s)
- car_damage_engine/transmission/aerodynamics/suspension (0..1), penalties_*, num_penalties, flags_* (yellow, blue, black ...)
- pit_state, in_pitlane, pit_window_*, num_pitstops
Tables: `cars` (t, slot, name, class, place, dist, speed, laps, in_pit, gap_front, gap_behind; once a second, all cars),
`laps` (lap, time, s1, s2, s3, valid, ended), `meta` (track, layout, car, class, driver, started)."""

TOOLS = [
    {"name": "list_sessions", "description": "The recorded RaceRoom sessions (newest first): file, track, car, when, rows.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "schema", "description": "What is in a session database and what the columns mean.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "query", "description": "Run a read-only SQL SELECT on a recorded session (default: the newest). " + FIELD_NOTES,
     "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}, "session": {"type": "string"}},
                     "required": ["sql"]}},
    {"name": "live", "description": "The latest recorded values (the last second or so of driving): the car, the lap, the "
     "tyres, the fuel, the position and the cars around. Pass `fields` to pick columns.",
     "inputSchema": {"type": "object", "properties": {"fields": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "driving_tips", "description": "Suggestions worked out from a recorded session: brake bias (wheel lock-ups), shift "
     "timing (revs at each upshift against the game's shift point and the limiter) and minimum speeds in the slow corners "
     "(best lap against average). Default: the newest session.",
     "inputSchema": {"type": "object", "properties": {"session": {"type": "string"}}}},
    {"name": "corners", "description": "The corner map of the driver's reference (fastest saved) lap for a track and car: "
     "turn number, shape, distance, braking point, brake peak, gear, throttle point.",
     "inputSchema": {"type": "object", "properties": {"track": {"type": "string"}, "car": {"type": "string"}}}},
    {"name": "stored_laps", "description": "The driver's saved laps (all sessions) for a track and car, fastest first, or "
     "everything saved if no track is given.", "inputSchema": {"type": "object", "properties": {
         "track": {"type": "string"}, "car": {"type": "string"}}}},
    {"name": "lap_trace", "description": "Channels of a saved lap between two lap distances, thinned out: distance, speed km/h, "
     "brake %, throttle %, gear, rpm, steering, g, time into the lap. `rank` 0 = fastest.",
     "inputSchema": {"type": "object", "properties": {"track": {"type": "string"}, "car": {"type": "string"},
                                                       "rank": {"type": "integer"}, "from_m": {"type": "number"},
                                                       "to_m": {"type": "number"}}, "required": ["from_m", "to_m"]}},
]


# ---- helpers ------------------------------------------------------------------------------------------------------
def _session_path(name: Optional[str]) -> Optional[Path]:
    if name:
        p = sessions_dir() / Path(name).name
        return p if p.is_file() else None
    return latest_session()


def _open_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=5)         # not mode=ro: a WAL file needs its -shm; query_only + the authoriser do the guarding
    db.execute("PRAGMA query_only=ON")

    def auth(action, *_):
        ok = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE)
        return sqlite3.SQLITE_OK if action in ok else sqlite3.SQLITE_DENY
    db.set_authorizer(auth)
    return db


def _rows(db: sqlite3.Connection, sql: str, params=()) -> dict:
    cur = db.execute(sql, params)
    names = [d[0] for d in cur.description or []]
    rows = cur.fetchmany(MAX_ROWS + 1)
    more = len(rows) > MAX_ROWS
    rows = [[round(v, 4) if isinstance(v, float) else v for v in r] for r in rows[:MAX_ROWS]]
    return {"columns": names, "rows": rows, "truncated": more}


def _text(obj: Any) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + " ...[cut: ask for fewer rows or columns]"


def _combo(args: dict) -> tuple:
    track, car = args.get("track", ""), args.get("car", "")
    if track and car:
        return track, car
    base = LP.laps_dir()
    best = None
    for p in base.glob("*/*/*.json") if base.is_dir() else []:       # the newest saved lap's track and car
        if best is None or p.stat().st_mtime > best.stat().st_mtime:
            best = p
    if best is None:
        return track, car
    return track or best.parent.parent.name, car or best.parent.name


# ---- the tools ----------------------------------------------------------------------------------------------------
def tool_list_sessions(args: dict) -> Any:
    out = []
    for p in sorted(sessions_dir().glob("*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)[:int(args.get("limit", 10))]:
        try:
            db = _open_ro(p)
            meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
            n = db.execute("SELECT count(*) FROM frames").fetchone()[0]
            db.close()
            out.append({"session": p.name, "track": meta.get("track"), "car": meta.get("car"), "rows": n,
                        "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(float(meta.get("started", 0))))})
        except (sqlite3.Error, ValueError):
            continue
    return out or "No sessions recorded yet (drive in RaceRoom with StintRadio running)."


def tool_schema(args: dict) -> Any:
    return {"notes": FIELD_NOTES, "frame_columns": [n for n, _, _ in FIELDS]}


def tool_query(args: dict) -> Any:
    p = _session_path(args.get("session"))
    if p is None:
        return "No recorded session found."
    sql = str(args.get("sql", "")).strip().rstrip(";")
    if not sql.lower().startswith(("select", "with")):
        return "Only SELECT queries are allowed."
    db = _open_ro(p)
    try:
        return _rows(db, sql)
    except sqlite3.Error as e:
        return f"SQL error: {e}"
    finally:
        db.close()


def tool_live(args: dict) -> Any:
    p = latest_session()
    if p is None:
        return "No recorded session found."
    db = _open_ro(p)
    try:
        names = {n for n, _, _ in FIELDS}
        fields = [f for f in args.get("fields", []) if f in names] or [
            "lap_distance", "completed_laps", "car_speed", "gear", "throttle", "brake", "steer_input_raw", "fuel_left",
            "fuel_per_lap", "position", "time_delta_front", "time_delta_behind", "time_delta_best_self", "tire_wear_0",
            "tire_wear_1", "tire_wear_2", "tire_wear_3", "tire_temp_0_current_temp_1", "tire_temp_1_current_temp_1",
            "tire_temp_2_current_temp_1", "tire_temp_3_current_temp_1", "engine_temp", "engine_oil_temp", "brake_bias",
            "session_time_remaining", "number_of_laps"]
        row = _rows(db, f"SELECT t, {','.join(chr(34) + f + chr(34) for f in fields)} FROM frames ORDER BY t DESC LIMIT 1")
        cars = _rows(db, "SELECT name, class, place, dist, speed, laps, gap_front, gap_behind FROM cars WHERE t = "
                         "(SELECT max(t) FROM cars) ORDER BY place")
        row["age_s"] = round(time.time() - row["rows"][0][0], 1) if row["rows"] else None
        return {"now": row, "cars": cars}
    except sqlite3.Error as e:
        return f"SQL error: {e}"
    finally:
        db.close()


def _best(args: dict) -> Optional[LP.Lap]:
    track, car = _combo(args)
    found = []
    for p in LP.stored_any(track, car) if track else []:
        try:
            found.append((float(p.stem.split("_")[1]), p))
        except (IndexError, ValueError):
            continue
    found.sort()
    rank = int(args.get("rank", 0))
    return LP.load(found[rank][1]) if len(found) > rank else None


def tool_driving_tips(args: dict) -> Any:
    from . import tips
    p = _session_path(args.get("session"))
    return tips.analyse(p) if p else {"lines": ["No recorded session yet."]}


def tool_corners(args: dict) -> Any:
    from .coach import Coach
    lap = _best(args)
    if lap is None:
        return "No saved lap for that track and car."
    length = lap.samples[-1][0] if lap.samples else 0
    co = Coach(lap, lap.track, length, 1)
    return {"track": lap.track, "car": lap.car, "reference_time_s": lap.time, "corners": [
        {"turn": c.n, "shape": c.look, "apex_m": round(c.apex), "brake_m": None if c.brake is None else round(c.brake),
         "brake_peak_percent": None if c.brake_peak is None else round(c.brake_peak),
         "gear": c.gear, "throttle_on_m": None if c.throttle_at is None else round(c.throttle_at)} for c in co.corners]}


def tool_stored_laps(args: dict) -> Any:
    base = LP.laps_dir()
    if not args.get("track"):
        return {f"{t.parent.name}/{t.name}": sorted(float(p.stem.split("_")[1]) for p in t.glob("*.json"))[:5]
                for t in sorted(base.glob("*/*")) if t.is_dir()}
    track, car = _combo(args)
    return {"track": track, "car": car, "lap_times_s": sorted(float(p.stem.split("_")[1]) for p in LP.stored_any(track, car))}


def tool_lap_trace(args: dict) -> Any:
    lap = _best(args)
    if lap is None:
        return "No saved lap for that track and car."
    lo, hi = float(args["from_m"]), float(args["to_m"])
    pts = [p for p in lap.samples if lo <= p[0] <= hi]
    step = max(1, len(pts) // 40)
    cols = ["dist_m", "speed_kmh", "brake_pct", "throttle_pct", "gear", "rpm", "steer", "g_side", "t_s"]
    rows = [[round(p[0]), round(p[1]), round(p[2]), round(p[3]), p[7] if len(p) > 7 else None, p[8] if len(p) > 8 else None,
             p[9] if len(p) > 9 else None, round(p[11] / 9.81, 2) if len(p) > 11 else None, round(p[6], 2)] for p in pts[::step]]
    return {"lap_time_s": lap.time, "columns": cols, "rows": rows}


HANDLERS = {"list_sessions": tool_list_sessions, "schema": tool_schema, "query": tool_query, "live": tool_live,
            "driving_tips": tool_driving_tips, "corners": tool_corners, "stored_laps": tool_stored_laps, "lap_trace": tool_lap_trace}


# ---- JSON-RPC over stdio ------------------------------------------------------------------------------------------
def handle(msg: dict) -> Optional[dict]:
    """The reply to one JSON-RPC message (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        return {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": proto, "capabilities": {"tools": {}},
                                                         "serverInfo": {"name": "stintradio", "version": "0.1"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        fn = HANDLERS.get(params.get("name"))
        if fn is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}}
        try:
            text, err = _text(fn(params.get("arguments") or {})), False
        except Exception as e:
            text, err = f"{type(e).__name__}: {e}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": err}}
    if mid is None:
        return None                                   # a notification (initialized, cancelled ...)
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
