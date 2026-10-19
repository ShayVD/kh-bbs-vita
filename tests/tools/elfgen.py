"""Build tiny little-endian ELF32 files for tool tests. No game data.

Section headers only: there are no program headers, so these files are for
tools that read sections (bbs_seeds.py), not for the loader or recompiler.
"""
import struct

SHT_PROGBITS, SHT_STRTAB, SHT_NOBITS = 1, 3, 8
SHF_ALLOC, SHF_EXECINSTR = 0x2, 0x4


def make_elf(sections):
    """sections: list of (name, addr, flags, data[, type[, size]]). Returns ELF
    bytes with those sections, a null section first and .shstrtab last.
    type defaults to SHT_PROGBITS. A SHT_NOBITS section takes no file bytes
    (its data is ignored) and its size is `size`, default 0."""
    secs = []
    for name, addr, flags, data, *opt in sections:
        typ = opt[0] if opt else SHT_PROGBITS
        if typ == SHT_NOBITS:
            data, size = b"", (opt[1] if len(opt) > 1 else 0)
        else:
            size = len(data)
        secs.append((name, addr, flags, data, typ, size))
    names = b"\0" + b"".join(s[0].encode() + b"\0" for s in secs) + b".shstrtab\0"
    body = b""
    offs = []
    base = 0x34
    for *_, data, _typ, _size in secs:
        offs.append(base + len(body))
        body += data + b"\0" * (-len(data) % 4)
    strtab_off = base + len(body)
    body += names + b"\0" * (-len(names) % 4)
    shoff = base + len(body)
    shdrs = [b"\0" * 40]
    name_off = 1
    for (name, addr, flags, _data, typ, size), off in zip(secs, offs):
        shdrs.append(struct.pack("<10I", name_off, typ, flags, addr, off, size, 0, 0, 4, 0))
        name_off += len(name) + 1
    shdrs.append(struct.pack("<10I", name_off, SHT_STRTAB, 0, 0, strtab_off, len(names), 0, 0, 1, 0))
    ehdr = (b"\x7fELF\x01\x01\x01" + b"\0" * 9 +
            struct.pack("<HHIIIIIHHHHHH", 2, 8, 1, 0, 0, shoff, 0, 0x34, 0, 0, 40,
                        len(shdrs), len(shdrs) - 1))
    return ehdr + body + b"".join(shdrs)


def words(*ws):
    return struct.pack("<%dI" % len(ws), *ws)
