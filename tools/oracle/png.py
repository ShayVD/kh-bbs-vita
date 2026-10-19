"""Read and write PNG files as rows of RGBA8 bytes, standard library only.

Reads 8-bit RGB and RGBA, non-interlaced, with all five filter types; every
chunk's CRC is checked. Writes RGBA with filter 0. That covers PPSSPP's
screenshots and what our backends write.
"""
import pathlib, struct, zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {2: 3, 6: 4}                    # colour type -> bytes per pixel (8-bit)


def read(path):
    """(width, height, rows): rows are `bytes` of width * 4 RGBA8 values.
    Raises ValueError for a broken or unsupported file."""
    data = pathlib.Path(path).read_bytes()
    if data[:8] != SIGNATURE:
        raise ValueError(f"{path}: no PNG signature")
    pos, ihdr, idat = 8, None, []
    while True:
        if pos + 12 > len(data):
            raise ValueError(f"{path}: truncated (no IEND)")
        length, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise ValueError(f"{path}: truncated {kind!r} chunk")
        (crc,) = struct.unpack_from(">I", data, pos + 8 + length)
        if zlib.crc32(kind + body) != crc:
            raise ValueError(f"{path}: bad CRC in {kind!r} chunk")
        pos += 12 + length
        if kind == b"IHDR":
            ihdr = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if ihdr is None or len(ihdr) != 13:
        raise ValueError(f"{path}: no IHDR")
    width, height, depth, ctype, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8 or ctype not in CHANNELS or comp or filt or interlace:
        raise ValueError(f"{path}: unsupported PNG (depth {depth}, colour type {ctype}, "
                         f"interlace {interlace}); want 8-bit RGB or RGBA, not interlaced")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise ValueError(f"{path}: bad image data: {e}") from None
    bpp = CHANNELS[ctype]
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise ValueError(f"{path}: {len(raw)} bytes of image data, expected {height * (stride + 1)}")
    rows, prev = [], bytes(stride)
    for y in range(height):
        start = y * (stride + 1)
        row = unfilter(raw[start], bytearray(raw[start + 1:start + 1 + stride]), prev, bpp, path)
        rows.append(bytes(row) if bpp == 4 else rgb_to_rgba(row))
        prev = row
    return width, height, rows


def unfilter(ftype, row, prev, bpp, path="PNG"):
    """Undo filter `ftype` on `row` (a bytearray, changed in place) given the
    unfiltered row above, `prev`."""
    n = len(row)
    if ftype == 0:
        pass
    elif ftype == 1:
        for i in range(bpp, n):
            row[i] = (row[i] + row[i - bpp]) & 0xFF
    elif ftype == 2:
        for i in range(n):
            row[i] = (row[i] + prev[i]) & 0xFF
    elif ftype == 3:
        for i in range(n):
            a = row[i - bpp] if i >= bpp else 0
            row[i] = (row[i] + ((a + prev[i]) >> 1)) & 0xFF
    elif ftype == 4:
        for i in range(n):
            if i >= bpp:
                a, c = row[i - bpp], prev[i - bpp]
            else:
                a = c = 0
            b = prev[i]
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            row[i] = (row[i] + pred) & 0xFF
    else:
        raise ValueError(f"{path}: bad filter type {ftype}")
    return row


def rgb_to_rgba(row):
    out = bytearray(len(row) // 3 * 4)
    out[0::4], out[1::4], out[2::4] = row[0::3], row[1::3], row[2::3]
    out[3::4] = b"\xff" * (len(row) // 3)
    return bytes(out)


def write(path, width, height, rows):
    """Write `rows` (height rows of width * 4 RGBA8 bytes) as an RGBA PNG."""
    if len(rows) != height or any(len(r) != width * 4 for r in rows):
        raise ValueError(f"want {height} rows of {width * 4} bytes")
    raw = b"".join(b"\0" + bytes(r) for r in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    pathlib.Path(path).write_bytes(SIGNATURE + chunk(b"IHDR", ihdr) +
                                   chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
