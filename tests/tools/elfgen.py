"""Build tiny little-endian ELF32 files for tool tests. No game data."""
import struct

SHT_PROGBITS, SHT_STRTAB, SHT_NOBITS = 1, 3, 8
SHF_ALLOC, SHF_EXECINSTR = 0x2, 0x4


def make_elf(sections):
    """sections: list of (name, addr, flags, data bytes). Returns ELF bytes
    with those sections, a null section first and .shstrtab last."""
    names = b"\0" + b"".join(n.encode() + b"\0" for n, *_ in sections) + b".shstrtab\0"
    body = b""
    offs = []
    base = 0x34
    for _, _, _, data in sections:
        offs.append(base + len(body))
        body += data + b"\0" * (-len(data) % 4)
    strtab_off = base + len(body)
    body += names + b"\0" * (-len(names) % 4)
    shoff = base + len(body)
    shdrs = [b"\0" * 40]
    name_off = 1
    for (name, addr, flags, data), off in zip(sections, offs):
        shdrs.append(struct.pack("<10I", name_off, SHT_PROGBITS, flags, addr, off, len(data), 0, 0, 4, 0))
        name_off += len(name) + 1
    shdrs.append(struct.pack("<10I", name_off, SHT_STRTAB, 0, 0, strtab_off, len(names), 0, 0, 1, 0))
    ehdr = (b"\x7fELF\x01\x01\x01" + b"\0" * 9 +
            struct.pack("<HHIIIIIHHHHHH", 2, 8, 1, 0, 0, shoff, 0, 0x34, 0, 0, 40,
                        len(shdrs), len(shdrs) - 1))
    return ehdr + body + b"".join(shdrs)


def words(*ws):
    return struct.pack("<%dI" % len(ws), *ws)
