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


# make_module_elf's layout (made up, inside the PSP's user RAM).
MODULE_TEXT = 0x08804000
MODULE_INFO = 0x08900000
MODULE_LIBSTUB = 0x08900100
MODULE_STRINGS = 0x08900200
MODULE_NIDS = 0x08900300
MODULE_STUBS = 0x08900400


def make_module_elf(imports, calls=None, tail_calls=()):
    """A module with an import table, the way import_report.py reads it:
    `.rodata.sceModuleInfo`, one `.lib.stub` entry per library, a
    `.rodata.sceNid` table, 8-byte stubs in `.sceStub.text` (from
    MODULE_STUBS, in import order), and a `.text` (at MODULE_TEXT) of
    `jal stub; nop` pairs, then `j stub; nop` pairs.

    imports: list of (library, [nid, ...]). calls / tail_calls: stub indices
    (counted over all libraries), each giving one jal / j; calls defaults to
    one jal per stub."""
    nstubs = sum(len(nids) for _, nids in imports)
    calls = range(nstubs) if calls is None else calls
    strings, libstub, nidtab = b"", b"", b""
    k = 0
    for lib, nids in imports:
        name_p = MODULE_STRINGS + len(strings)
        strings += lib.encode() + b"\0"
        # name, version, attr, entry length in words, var count, func count, nids, stubs
        libstub += struct.pack("<IHHBBHII", name_p, 0x0011, 0x0009, 5, 0, len(nids),
                               MODULE_NIDS + 4 * k, MODULE_STUBS + 8 * k)
        nidtab += words(*nids)
        k += len(nids)
    # attr, version, name[28], gp, ent_top, ent_end, stub_top, stub_end
    info = struct.pack("<HH28sIIIII", 0, 0x0101, b"synthetic", 0, 0, 0,
                       MODULE_LIBSTUB, MODULE_LIBSTUB + len(libstub))
    stubs = words(*[0x03E00008, 0] * nstubs)                 # jr ra; nop
    text = b"".join(words(op << 26 | (MODULE_STUBS + 8 * i) >> 2 & 0x3FFFFFF, 0)
                    for op, idx in ((3, calls), (2, tail_calls)) for i in idx)
    return make_elf([
        (".text", MODULE_TEXT, SHF_ALLOC | SHF_EXECINSTR, text),
        (".rodata.sceModuleInfo", MODULE_INFO, SHF_ALLOC, info),
        (".lib.stub", MODULE_LIBSTUB, SHF_ALLOC, libstub),
        (".rodata.sceResident", MODULE_STRINGS, SHF_ALLOC, strings),
        (".rodata.sceNid", MODULE_NIDS, SHF_ALLOC, nidtab),
        (".sceStub.text", MODULE_STUBS, SHF_ALLOC | SHF_EXECINSTR, stubs),
    ])
