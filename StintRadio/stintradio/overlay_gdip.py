"""Paints the overlay's shapes (overlay_scene) into a 32-bit bitmap with per-pixel alpha, using GDI+ (part of Windows, reached
through ctypes, so no extra package): anti-aliased lines and text, and every pixel not drawn on stays fully clear. The bitmap
is premultiplied, which is what a layered window (UpdateLayeredWindow) wants, and it can be saved as a PNG without any window
existing, which is how the drawing is checked.
"""
from __future__ import annotations

import ctypes
import struct
import threading
import zlib
from ctypes import POINTER, byref, c_float, c_int, c_uint, c_void_p, c_wchar_p, wintypes
from pathlib import Path

PARGB = 0x000E200B                                # PixelFormat32bppPARGB
ANTIALIAS_8X8, PIXEL_OFFSET_HALF = 5, 4
ROUND_JOIN, ROUND_CAP, DASH = 2, 2, 1
NO_WRAP = 0x1000
ALIGN = {"left": 0, "center": 1, "right": 2}
STYLE = {"bold": 1, "bolditalic": 3}
FONTS = ("Segoe UI", "Arial")
BI_RGB, DIB_RGB = 0, 0


class _Startup(ctypes.Structure):
    _fields_ = [("version", c_uint), ("callback", c_void_p), ("no_thread", wintypes.BOOL), ("no_codecs", wintypes.BOOL)]


class _Point(ctypes.Structure):
    _fields_ = [("x", c_float), ("y", c_float)]


class _Rect(ctypes.Structure):
    _fields_ = [("x", c_float), ("y", c_float), ("w", c_float), ("h", c_float)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("size", c_uint), ("width", c_int), ("height", c_int), ("planes", ctypes.c_ushort), ("bits", ctypes.c_ushort),
                ("compression", c_uint), ("image_size", c_uint), ("xppm", c_int), ("yppm", c_int), ("used", c_uint), ("important", c_uint)]


_P, _PP, _I, _F = c_void_p, POINTER(c_void_p), c_int, c_float
_SIGNATURES = {
    "GdiplusStartup": [POINTER(ctypes.c_size_t), POINTER(_Startup), _P],
    "GdipCreateBitmapFromScan0": [_I, _I, _I, _I, _P, _PP], "GdipGetImageGraphicsContext": [_P, _PP], "GdipDisposeImage": [_P],
    "GdipDeleteGraphics": [_P], "GdipSetSmoothingMode": [_P, _I], "GdipSetPixelOffsetMode": [_P, _I], "GdipGraphicsClear": [_P, c_uint],
    "GdipCreateSolidFill": [c_uint, _PP], "GdipDeleteBrush": [_P], "GdipCreatePen1": [c_uint, _F, _I, _PP], "GdipDeletePen": [_P],
    "GdipSetPenLineJoin": [_P, _I], "GdipSetPenStartCap": [_P, _I], "GdipSetPenEndCap": [_P, _I], "GdipSetPenDashStyle": [_P, _I],
    "GdipCreatePath": [_I, _PP], "GdipDeletePath": [_P], "GdipAddPathLine2": [_P, POINTER(_Point), _I], "GdipAddPathArc": [_P, _F, _F, _F, _F, _F, _F],
    "GdipAddPathEllipse": [_P, _F, _F, _F, _F], "GdipClosePathFigure": [_P], "GdipDrawPath": [_P, _P, _P], "GdipFillPath": [_P, _P, _P],
    "GdipAddPathString": [_P, c_wchar_p, _I, _P, _I, _F, POINTER(_Rect), _P],
    "GdipCreateFontFamilyFromName": [c_wchar_p, _P, _PP], "GdipDeleteFontFamily": [_P], "GdipCreateStringFormat": [_I, _I, _PP],
    "GdipSetStringFormatAlign": [_P, _I], "GdipSetStringFormatLineAlign": [_P, _I],
}


class Gdip:
    """GDI+, started once for the process, with the one font family and text format the overlay uses."""

    _one = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "Gdip":
        with cls._lock:
            if cls._one is None:
                cls._one = cls()
            return cls._one

    def __init__(self) -> None:
        self.d = ctypes.WinDLL("gdiplus")
        for name, args in _SIGNATURES.items():
            f = getattr(self.d, name)
            f.argtypes, f.restype = args, c_int
        token = ctypes.c_size_t()
        self.call("GdiplusStartup", byref(token), byref(_Startup(version=1)), None)
        self.family = c_void_p()
        for name in FONTS:
            if self.d.GdipCreateFontFamilyFromName(name, None, byref(self.family)) == 0:
                break
        self.formats = {}
        for name, value in ALIGN.items():
            fmt = c_void_p()
            self.call("GdipCreateStringFormat", NO_WRAP, 0, byref(fmt))
            self.call("GdipSetStringFormatAlign", fmt, value)
            self.call("GdipSetStringFormatLineAlign", fmt, 1)
            self.formats[name] = fmt

    def call(self, name: str, *args) -> None:
        status = getattr(self.d, name)(*args)
        if status != 0:
            raise OSError(f"{name} failed with GDI+ status {status}")


def argb(c: tuple) -> int:
    """(r, g, b, a) to GDI+'s 0xAARRGGBB."""
    return (c[3] << 24) | (c[0] << 16) | (c[1] << 8) | c[2]


def png_bytes(bgra: bytes, w: int, h: int) -> bytes:
    """A PNG with a real alpha channel from premultiplied BGRA pixels."""
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for i in range(y * w * 4, (y + 1) * w * 4, 4):
            b, g, r, a = bgra[i:i + 4]
            if 0 < a < 255:
                r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
            rows += bytes((r, g, b, a))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


class Canvas:
    """One bitmap of w x h, in a DIB section so that a window can show it. A layer of what never moves is kept and copied
    under every frame, so only what moves is drawn each time."""

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h, self.gp = w, h, Gdip.get()
        self.gdi = ctypes.WinDLL("gdi32")
        self.gdi.CreateCompatibleDC.argtypes, self.gdi.CreateCompatibleDC.restype = [_P], _P
        self.gdi.CreateDIBSection.argtypes = [_P, POINTER(_BitmapInfo), c_uint, _PP, _P, c_uint]
        self.gdi.CreateDIBSection.restype = _P
        self.gdi.SelectObject.argtypes, self.gdi.SelectObject.restype = [_P, _P], _P
        self.gdi.DeleteObject.argtypes, self.gdi.DeleteDC.argtypes = [_P], [_P]
        self.dc = self.gdi.CreateCompatibleDC(None)
        info = _BitmapInfo(size=ctypes.sizeof(_BitmapInfo), width=w, height=-h, planes=1, bits=32, compression=BI_RGB)   # top-down
        self.bits = c_void_p()
        self.dib = self.gdi.CreateDIBSection(self.dc, byref(info), DIB_RGB, byref(self.bits), None, 0)
        if not self.dib:
            self.gdi.DeleteDC(self.dc)
            raise OSError("could not make the overlay's bitmap")
        self.old = self.gdi.SelectObject(self.dc, self.dib)
        self.bitmap, self.g = c_void_p(), c_void_p()
        self.size = w * h * 4
        self.gp.call("GdipCreateBitmapFromScan0", w, h, w * 4, PARGB, self.bits, byref(self.bitmap))
        self.gp.call("GdipGetImageGraphicsContext", self.bitmap, byref(self.g))
        self.gp.call("GdipSetSmoothingMode", self.g, ANTIALIAS_8X8)
        self.gp.call("GdipSetPixelOffsetMode", self.g, PIXEL_OFFSET_HALF)
        self.layer, self.layer_key = None, object()

    def close(self) -> None:
        if self.dc:
            self.gp.call("GdipDeleteGraphics", self.g)
            self.gp.call("GdipDisposeImage", self.bitmap)
            self.gdi.SelectObject(self.dc, self.old)
            self.gdi.DeleteObject(self.dib)
            self.gdi.DeleteDC(self.dc)
            self.dc = None

    def paint(self, key, static_items: list, moving_items: list) -> None:
        """A frame: the kept layer (drawn again when `key` changes), then what moves on top. Everything else stays clear."""
        if key != self.layer_key:
            self.gp.call("GdipGraphicsClear", self.g, 0)
            self.draw(static_items)
            self.layer = ctypes.string_at(self.bits, self.size)
            self.layer_key = key
        ctypes.memmove(self.bits, self.layer, self.size)
        self.draw(moving_items)

    def draw(self, items: list) -> None:
        for it in items:
            getattr(self, "_" + it[0])(*it[1:])

    def pixel(self, x: int, y: int) -> tuple:
        """(r, g, b, a) of a pixel, as a layered window receives it: premultiplied."""
        b, g, r, a = ctypes.string_at(self.bits.value + (y * self.w + x) * 4, 4)
        return r, g, b, a

    def pixels(self) -> bytes:
        """The whole bitmap, BGRA, premultiplied."""
        return ctypes.string_at(self.bits, self.size)

    def save_png(self, path) -> None:
        Path(path).write_bytes(png_bytes(self.pixels(), self.w, self.h))

    # ---- the shapes (see overlay_scene) ----------------------------------------------------------------------------------

    def _with_path(self, build, fill, stroke=None, stroke_w=0.0, dashed=False) -> None:
        path = c_void_p()
        self.gp.call("GdipCreatePath", 1, byref(path))         # winding: the letters' overlaps stay filled
        try:
            build(path)
            if stroke is not None and stroke_w > 0:
                pen = c_void_p()
                self.gp.call("GdipCreatePen1", argb(stroke), float(stroke_w), 2, byref(pen))
                try:
                    self.gp.call("GdipSetPenLineJoin", pen, ROUND_JOIN)
                    if dashed:
                        self.gp.call("GdipSetPenDashStyle", pen, DASH)
                    else:
                        self.gp.call("GdipSetPenStartCap", pen, ROUND_CAP)
                        self.gp.call("GdipSetPenEndCap", pen, ROUND_CAP)
                    self.gp.call("GdipDrawPath", self.g, pen, path)
                finally:
                    self.gp.call("GdipDeletePen", pen)
            if fill is not None:
                brush = c_void_p()
                self.gp.call("GdipCreateSolidFill", argb(fill), byref(brush))
                try:
                    self.gp.call("GdipFillPath", self.g, brush, path)
                finally:
                    self.gp.call("GdipDeleteBrush", brush)
        finally:
            self.gp.call("GdipDeletePath", path)

    def _add_points(self, path, pts: list, closed: bool) -> None:
        arr = (_Point * len(pts))(*[_Point(float(x), float(y)) for x, y in pts])
        self.gp.call("GdipAddPathLine2", path, arr, len(pts))
        if closed:
            self.gp.call("GdipClosePathFigure", path)

    def _rect(self, x, y, w, h, radius, fill, stroke, stroke_w, dashed) -> None:
        def build(path) -> None:
            if radius <= 0:
                self._add_points(path, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)
                return
            d = float(min(radius * 2, w, h))
            for ax, ay, start in ((x, y, 180.0), (x + w - d, y, 270.0), (x + w - d, y + h - d, 0.0), (x, y + h - d, 90.0)):
                self.gp.call("GdipAddPathArc", path, float(ax), float(ay), d, d, start, 90.0)
            self.gp.call("GdipClosePathFigure", path)
        # a shape's own fill must not be doubled along an outline, so the fill goes on after and the line last
        self._with_path(build, fill)
        if stroke is not None and stroke_w > 0:
            self._with_path(build, None, stroke, stroke_w, dashed)

    def _poly(self, pts, fill) -> None:
        self._with_path(lambda path: self._add_points(path, pts, True), fill)

    def _line(self, pts, closed, colour, width) -> None:
        self._with_path(lambda path: self._add_points(path, pts, closed), None, colour, width)

    def _dot(self, x, y, r, fill, stroke, stroke_w) -> None:
        self._with_path(lambda path: self.gp.call("GdipAddPathEllipse", path, float(x - r), float(y - r), float(2 * r), float(2 * r)), fill)
        if stroke is not None and stroke_w > 0:
            self._with_path(lambda path: self.gp.call("GdipAddPathEllipse", path, float(x - r), float(y - r), float(2 * r), float(2 * r)), None, stroke, stroke_w)

    def _text(self, text, x, y, w, h, size, style, align, fill, outline, outline_w) -> None:
        box = _Rect(float(x), float(y), float(w), float(h))
        add = lambda path: self.gp.call("GdipAddPathString", path, text, -1, self.gp.family, STYLE[style], float(size), byref(box), self.gp.formats[align])
        self._with_path(add, fill, outline, outline_w * 2 if outline is not None else 0.0)
