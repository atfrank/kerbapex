"""Read-only probe of Assetto Corsa EVO shared memory (Local\\acevo_pmf_graphics / _static).
Offsets are computed from the community header (pack 4) and checked against its static_asserts.
Usage: python acevo_probe.py HEADER [seconds]   -> prints a sample every second."""
import ctypes
import mmap
import re
import struct
import sys
import time

HDR = open(sys.argv[1], encoding="utf-8").read()
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 10

PRIM = {"int": (4, "i"), "int32_t": (4, "i"), "uint32_t": (4, "I"), "unsigned int": (4, "I"), "float": (4, "f"),
        "double": (8, "d"), "bool": (1, "?"), "char": (1, "c"), "uint8_t": (1, "B"), "int8_t": (1, "b"),
        "short": (2, "h"), "unsigned short": (2, "H"), "uint16_t": (2, "H"), "int16_t": (2, "h"),
        "uint64_t": (8, "Q"), "int64_t": (8, "q")}
for t in re.findall(r"typedef\s+int\s+(\w+);", HDR):
    PRIM[t] = (4, "i")

# strip comments
src = re.sub(r"/\*.*?\*/", "", HDR, flags=re.S)
src = re.sub(r"//[^\n]*", "", src)
structs = {}
for name, body in re.findall(r"struct\s+(\w+)\s*\{(.*?)\n\s*\};", src, flags=re.S):
    fields = []
    for decl in body.split(";"):
        decl = re.sub(r"=\s*[^;]*$", "", decl.strip()).replace("{}", "").strip()
        if not decl:
            continue
        m = re.match(r"((?:unsigned\s+)?\w+)\s+(\w+)((?:\[\d+\])*)$", decl)
        if not m:
            raise SystemExit(f"cannot parse in {name}: {decl!r}")
        dims = [int(x) for x in re.findall(r"\[(\d+)\]", m.group(3))]
        fields.append((m.group(2), m.group(1), dims))
    structs[name] = fields

layout = {}


def size_align(t):
    if t in PRIM:
        s = PRIM[t][0]
        return s, min(s, 4)
    lay = compute(t)
    return lay["size"], lay["align"]


def compute(name):
    if name in layout:
        return layout[name]
    off, maxal, fl = 0, 1, {}
    for fname, t, dims in structs[name]:
        s, al = size_align(t)
        n = 1
        for d in dims:
            n *= d
        off = (off + al - 1) // al * al
        fl[fname] = (off, t, dims)
        off += s * n
        maxal = max(maxal, al)
    size = (off + maxal - 1) // maxal * maxal
    layout[name] = {"size": size, "align": maxal, "fields": fl}
    return layout[name]


for n in structs:
    compute(n)
for n, sz in re.findall(r"static_assert\(sizeof\((\w+)\)\s*==\s*(\d+)", src):
    if layout[n]["size"] != int(sz):
        raise SystemExit(f"layout mismatch {n}: {layout[n]['size']} != {sz}")
G, ST = layout["SPageFileGraphicEvo"], layout["SPageFileStaticEvo"]


def open_map(tag, size):
    k = ctypes.windll.kernel32
    k.OpenFileMappingW.restype = ctypes.c_void_p
    h = k.OpenFileMappingW(0x0004, False, tag)              # FILE_MAP_READ only
    if not h:
        return None
    k.CloseHandle(ctypes.c_void_p(h))
    return mmap.mmap(-1, size, tagname=tag, access=mmap.ACCESS_READ)


def get(buf, lay, fname, sub=None):
    off, t, dims = lay["fields"][fname]
    if sub:
        so, t, dims = layout[t]["fields"][sub]
        off += so
    if t == "char":
        raw = buf[off:off + (dims[0] if dims else 1)]
        return raw.split(b"\0")[0].decode("utf-8", "replace")
    fmt = PRIM[t][1]
    n = 1
    for d in dims:
        n *= d
    vals = struct.unpack_from("<" + fmt * n, buf, off)
    return vals if dims else vals[0]


g = open_map("Local\\acevo_pmf_graphics", G["size"])
s = open_map("Local\\acevo_pmf_static", ST["size"])
print("graphics page", "found" if g else "NOT FOUND", G["size"], "bytes; static page", "found" if s else "NOT FOUND", ST["size"])
if not g:
    sys.exit(0)
if s:
    print("static:", {f: get(s, ST, f) for f in ("sm_version", "ac_evo_version", "session", "session_name", "track",
                                                "track_configuration", "track_length_m", "is_online", "number_of_sessions")})
STATUS = {0: "OFF", 1: "REPLAY", 2: "LIVE", 3: "PAUSE"}
end = time.time() + DUR
last = None
while time.time() < end:
    pid = get(g, G, "packetId")
    n = get(g, G, "active_cars")
    ids = get(g, G, "car_ids")
    coords = get(g, G, "car_coordinates")
    cars = [((ids[2 * i], ids[2 * i + 1]), tuple(round(c, 1) for c in coords[3 * i:3 * i + 3])) for i in range(n)]
    foc = (get(g, G, "focused_car_id_a"), get(g, G, "focused_car_id_b"))
    ply = (get(g, G, "player_car_id_a"), get(g, G, "player_car_id_b"))
    fidx = next((i for i, c in enumerate(cars) if c[0] == foc), None)
    row = {"packet": pid, "status": STATUS.get(get(g, G, "status"), get(g, G, "status")),
           "focused_idx": fidx, "focused_is_player": foc == ply,
           "driver": (get(g, G, "driver_name") + " " + get(g, G, "driver_surname")).strip(),
           "car": get(g, G, "car_model"), "pos": f"{get(g, G, 'current_pos')}/{get(g, G, 'total_drivers')}",
           "lap": get(g, G, "total_lap_count"), "lap_ms": get(g, G, "current_lap_time_ms"),
           "npos": round(get(g, G, "npos"), 4), "speed": get(g, G, "display_speed_kmh"),
           "tod": f"{get(g, G, 'time_of_day_hours')}:{get(g, G, 'time_of_day_minutes'):02d}:{get(g, G, 'time_of_day_seconds'):02d}",
           "phase": get(g, G, "session_state", "phase_name"), "time_left": get(g, G, "session_state", "time_left"),
           "sess_lap": f"{get(g, G, 'session_state', 'current_lap')}/{get(g, G, 'session_state', 'total_lap')}",
           "total_time": get(g, G, "timing_state", "total_time"), "active_cars": n,
           "car0": cars[0][1] if cars else None, "car1": cars[1][1] if len(cars) > 1 else None}
    print(row, flush=True)
    time.sleep(1)
