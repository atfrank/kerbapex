"""Writes assets/kerbapex.ico: the KerbApex mark (an apex chevron over a strip of kerb stripes) as a PNG-in-ICO at 256, 64, 48, 32 and 16 px.
Standard library only.   python packaging/make_icon.py"""
import struct
import sys
import zlib
from pathlib import Path

BG = (11, 14, 20)
KERB, WHITE, APEX, EDGE = (225, 6, 0), (244, 244, 244), (124, 108, 255), (74, 168, 255)


def pixel(x: float, y: float):
    """The colour at (x, y) in 0..1, or None outside the rounded square (transparent)."""
    r = 0.18
    cx, cy = min(max(x, r), 1 - r), min(max(y, r), 1 - r)
    if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
        return None
    if y > 0.70:                                                     # the kerb: red and white diagonal stripes
        return KERB if int((x + y) * 9) % 2 == 0 else WHITE
    # the apex: a chevron, violet on the left going to blue on the right
    ax, ay, w = 0.5, 0.20, 0.10
    for d in (abs(x - ax) * 1.15 + ay,):                             # inverted V: y = ay + 1.15 * |x - ax|
        if abs(y - d) < w * 0.75 and 0.14 < x < 0.86:
            t = (x - 0.14) / 0.72
            return tuple(int(APEX[i] + (EDGE[i] - APEX[i]) * t) for i in range(3))
    return BG


def png(size: int) -> bytes:
    ss = 3                                                           # 3x3 samples a pixel: smooth edges
    rows = []
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            acc, a = [0, 0, 0], 0
            for sj in range(ss):
                for si in range(ss):
                    c = pixel((i + (si + 0.5) / ss) / size, (j + (sj + 0.5) / ss) / size)
                    if c is not None:
                        a += 1
                        for k in range(3):
                            acc[k] += c[k]
            if a:
                row += bytes([acc[0] // a, acc[1] // a, acc[2] // a, 255 * a // (ss * ss)])
            else:
                row += bytes([0, 0, 0, 0])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ico(sizes=(256, 64, 48, 32, 16)) -> bytes:
    images = [png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, body = b"", b""
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s, 0, 0, 1, 32, len(img), offset + len(body))
        body += img
    return head + entries + body


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "assets" / "kerbapex.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(ico())
    (out.parent / "kerbapex.png").write_bytes(png(256))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
