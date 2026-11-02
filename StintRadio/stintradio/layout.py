"""Struct layout of LMU's live shared memory ("LMU_Data"), computed from the official header that the game installs in
`<game>/Support/SharedMemoryInterface/`. Studio 397's licence does not allow redistributing that header, so it is read
from the user's own install every time instead of being copied here. The result is checked against the offsets
DriverDirector found by hand before the header existed (scoring at 1632, cars at 2192, 584 bytes each)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

PRIM = {"double": (8, "d"), "float": (4, "f"), "long": (4, "i"), "int": (4, "i"), "unsigned long": (4, "I"),
        "unsigned int": (4, "I"), "short": (2, "h"), "unsigned short": (2, "H"), "char": (1, "c"),
        "signed char": (1, "b"), "unsigned char": (1, "B"), "bool": (1, "?"), "uint8_t": (1, "B"), "int8_t": (1, "b"),
        "uint16_t": (2, "H"), "int16_t": (2, "h"), "uint32_t": (4, "I"), "int32_t": (4, "i"), "uint64_t": (8, "Q"),
        "int64_t": (8, "q"), "size_t": (8, "Q"), "HWND": (8, "Q"), "float_t": (4, "f"), "double_t": (8, "d"),
        "long long": (8, "q"), "unsigned long long": (8, "Q")}
PTR = 8


@dataclass
class Struct:
    name: str
    size: int
    align: int
    fields: dict = field(default_factory=dict)      # name -> (offset, type, count)


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _bodies(src: str) -> dict[str, str]:
    """name -> body of every `struct Name { ... };` (brace matched, so nested unions/methods stay inside)."""
    out = {}
    for m in re.finditer(r"\bstruct\s+(\w+)\s*(?::[^{;]*)?\{", src):
        i, depth = m.end(), 1
        while depth and i < len(src):
            depth += {"{": 1, "}": -1}.get(src[i], 0)
            i += 1
        out.setdefault(m.group(1), src[m.end():i - 1])
    return out


def _decls(body: str) -> list[tuple[str, str, int, bool]]:
    """(type, name, count, is_pointer) for each data member; methods and nested blocks are skipped."""
    depth, cur = 0, ""
    for ch in body:                                   # drop anything inside nested braces (method bodies, unions)
        if ch == "{":
            depth += 1
            if depth == 1:
                cur += ";"
            continue
        if ch == "}":
            depth -= 1
            continue
        if depth == 0:
            cur += ch
    out = []
    for st in cur.split(";"):
        st = " ".join(st.split())
        if not st or "(" in st or st.startswith(("public", "private", "protected", "friend", "static", "using",
                                                 "enum", "typedef", "union", "struct")):
            continue
        st = re.sub(r"\s*=\s*[^,]*", "", st)             # default member initialisers
        m = re.match(r"((?:unsigned\s+|signed\s+)?(?:long\s+long|[\w:]+))\s*(\*?)\s*(.+)$", st.replace("const ", ""))
        if not m:
            continue
        typ, star, names = " ".join(m.group(1).split()), m.group(2), m.group(3)
        for nm in names.split(","):
            nm = nm.strip()
            ptr = bool(star) or nm.startswith("*")
            nm = nm.lstrip("*").strip()
            dims = [int(d) for d in re.findall(r"\[\s*(\d+)\s*\]", nm)]
            nm = re.sub(r"\[.*", "", nm).strip()
            if not re.fullmatch(r"\w+", nm):
                continue
            n = 1
            for d in dims:
                n *= d
            out.append((typ, nm, n, ptr))
    return out


def parse(src: str, pack: int = 4) -> dict[str, Struct]:
    """Layouts of the plugin structs (the header packs them to 4 bytes). TelemVect3 is a union of three doubles."""
    src = _strip(src)
    enums = {name: PRIM.get(base, (4, "i"))[0] for name, base in re.findall(r"enum\s+class\s+(\w+)\s*:\s*(\w+)", src)}
    bodies, done = _bodies(src), {}

    def build(name: str) -> Struct:
        if name in done:
            return done[name]
        if name == "TelemVect3":
            done[name] = Struct(name, 24, min(8, pack), {"x": (0, "double", 1), "y": (8, "double", 1), "z": (16, "double", 1)})
            return done[name]
        off, al_max, fl = 0, 1, {}
        for typ, nm, n, ptr in _decls(bodies[name]):
            if ptr:
                size, al = PTR, PTR
            elif typ in PRIM:
                size, al = PRIM[typ][0], PRIM[typ][0]
            elif typ in enums:
                size, al = enums[typ], enums[typ]
            elif typ in bodies:
                s = build(typ)
                size, al = s.size, s.align
            else:
                raise ValueError(f"unknown type {typ} in {name}")
            al = min(al, pack)
            off = (off + al - 1) // al * al
            fl[nm] = (off, "ptr" if ptr else typ, n)
            off += size * n
            al_max = max(al_max, al)
        size = (off + al_max - 1) // al_max * al_max
        done[name] = Struct(name, size, al_max, fl)
        return done[name]

    for name in bodies:
        try:
            build(name)
        except (ValueError, KeyError):
            pass                                          # structs we do not need may use types we do not model
    return done


@dataclass
class Layout:
    scoring_info: int            # ScoringInfoV01
    vehicle_scoring: int         # VehicleScoringInfoV01[104]
    telemetry: int               # SharedMemoryTelemetryData (activeVehicles, playerVehicleIdx, playerHasVehicle, ...)
    telem_info: int              # first TelemInfoV01
    total: int
    structs: dict


def header_dir(game_dir: str | Path) -> Path:
    return Path(game_dir) / "Support" / "SharedMemoryInterface"


def compute(game_dir: str | Path) -> Layout:
    """Offsets inside LMU_Data. SharedMemoryInterface.hpp's own structs (after the header's `pack(pop)`) use the
    compiler's default 8-byte packing; the plugin structs inside them are packed to 4."""
    src = (header_dir(game_dir) / "InternalsPlugin.hpp").read_text(encoding="utf-8", errors="replace")
    st = parse(src, pack=4)
    app = st["ApplicationStateV01"]
    generic = 16 * 4 + 4 + 4                       # SharedMemoryEvent events[SME_MAX = 16]; long gameVersion; float FFB
    generic = (generic + app.align - 1) // app.align * app.align + app.size
    generic = (generic + 3) // 4 * 4
    scoring = generic + 5 * 260                    # SharedMemoryPathData: char[MAX_PATH] x 5
    si, vs, ti = st["ScoringInfoV01"], st["VehicleScoringInfoV01"], st["TelemInfoV01"]
    vehicles = (scoring + si.size + 7) // 8 * 8 + 8          # size_t scoringStreamSize, 8-byte aligned
    tel = (vehicles + 104 * vs.size + 65536 + 7) // 8 * 8    # end of SharedMemoryScoringData (holds a size_t)
    telem_info = (tel + 3 + ti.align - 1) // ti.align * ti.align
    return Layout(scoring, vehicles, tel, telem_info, telem_info + 104 * ti.size, st)


def verify(lay: Layout) -> str | None:
    """None when the computed layout matches what was verified against the live game before."""
    if lay.scoring_info != 1632 or lay.vehicle_scoring != 2192 or lay.structs["VehicleScoringInfoV01"].size != 584:
        return (f"layout differs from the verified one (scoring {lay.scoring_info}, cars {lay.vehicle_scoring}, "
                f"car size {lay.structs['VehicleScoringInfoV01'].size}); the game's header has changed")
    return None
