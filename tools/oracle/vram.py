"""Turn a PSP framebuffer read from VRAM into RGBA8 rows (docs/oracle.md,
"Frames").

The four sceDisplaySetFrameBuf pixel formats, little endian:
  0  565   R bits 0-4, G 5-10, B 11-15
  1  5551  R bits 0-4, G 5-9, B 10-14, A bit 15
  2  4444  R bits 0-3, G 4-7, B 8-11, A 12-15
  3  8888  bytes R, G, B, A
Alpha is ignored for display, so every output pixel has A = 255. Channels
are widened by repeating their top bits: 5-bit v as (v << 3) | (v >> 2),
6-bit as (v << 2) | (v >> 4), 4-bit as v * 17.
"""
import struct

BPP = {0: 2, 1: 2, 2: 2, 3: 4}


def _c5(v):
    return (v << 3) | (v >> 2)


def _c6(v):
    return (v << 2) | (v >> 4)


def _table(fmt):
    """The RGBA bytes of every 16-bit pixel value in format `fmt`."""
    if fmt == 0:
        rgb = lambda v: (_c5(v & 31), _c6(v >> 5 & 63), _c5(v >> 11 & 31))  # noqa: E731
    elif fmt == 1:
        rgb = lambda v: (_c5(v & 31), _c5(v >> 5 & 31), _c5(v >> 10 & 31))  # noqa: E731
    else:
        rgb = lambda v: ((v & 15) * 17, (v >> 4 & 15) * 17, (v >> 8 & 15) * 17)  # noqa: E731
    return [bytes((*rgb(v), 255)) for v in range(1 << 16)]


_TABLES = {}


def size(stride, height, fmt):
    """Bytes of a framebuffer `stride` pixels wide and `height` lines high."""
    return stride * height * BPP[fmt]


def vram_to_rgba(data, stride, width, height, fmt):
    """`height` rows of `width` * 4 RGBA8 bytes from `data`, a framebuffer
    with lines `stride` pixels apart (the first `width` pixels of each are
    kept). Raises ValueError for an unknown format, a stride below the width,
    or too little data."""
    if fmt not in BPP:
        raise ValueError(f"unknown pixel format {fmt}")
    if stride < width:
        raise ValueError(f"stride {stride} is less than the width {width}")
    need = size(stride, height, fmt)
    if len(data) < need:
        raise ValueError(f"{len(data)} bytes of framebuffer, expected {need}")
    line = stride * BPP[fmt]
    rows = []
    if fmt == 3:
        for y in range(height):
            row = bytearray(data[y * line:y * line + width * 4])
            row[3::4] = b"\xff" * width
            rows.append(bytes(row))
        return rows
    table = _TABLES.get(fmt) or _TABLES.setdefault(fmt, _table(fmt))
    for y in range(height):
        pixels = struct.unpack_from(f"<{width}H", data, y * line)
        rows.append(b"".join(table[v] for v in pixels))
    return rows
