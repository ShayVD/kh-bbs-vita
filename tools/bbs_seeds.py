#!/usr/bin/env python3
"""Write the recompiler's seeds file from bbs.toml.

  tools/bbs_seeds.py bbs.toml game/EBOOT.dec.ELF -o build/pc/gen/seeds.txt

Checks the ELF against [target].eboot_sha1, then writes one address per
line: discovery.entries first, then every non-zero word of each
discovery.tables entry. A table is a whole ELF section (`section`) or a
range (`addr` + `count`), read every `stride` bytes (default 4).

Every address must lie in an executable section. bbs.toml is maintained by
hand and by tools/merge_misses.py, and a typo there must fail the build
rather than seed data as code.
"""
import argparse, hashlib, struct, sys, tomllib

SHF_EXECINSTR = 0x4
SHT_NOBITS = 8


def sections(elf):
    """The ELF's section headers as dicts: name, type, flags, addr, offset, size."""
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError("not a little-endian ELF32 file")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x2E)
    raw = [struct.unpack_from("<10I", elf, shoff + i * shentsize) for i in range(shnum)]
    stroff = raw[shstrndx][4]
    out = []
    for name, typ, flags, addr, off, size, *_ in raw:
        end = elf.index(b"\0", stroff + name)
        out.append({"name": elf[stroff + name:end].decode(), "type": typ, "flags": flags,
                    "addr": addr, "offset": off, "size": size})
    return out


def exec_ranges(secs):
    return [(s["addr"], s["addr"] + s["size"]) for s in secs
            if s["flags"] & SHF_EXECINSTR and s["size"]]


def in_code(addr, ranges):
    return addr % 4 == 0 and any(lo <= addr < hi for lo, hi in ranges)


def read_word(elf, secs, addr):
    for s in secs:
        if s["type"] != SHT_NOBITS and s["addr"] <= addr and addr + 4 <= s["addr"] + s["size"]:
            return struct.unpack_from("<I", elf, s["offset"] + addr - s["addr"])[0]
    raise ValueError(f"0x{addr:08X} is not in any section with file data")


def table_words(elf, secs, t):
    stride = int(t.get("stride", 4))
    if "section" in t:
        match = [s for s in secs if s["name"] == t["section"]]
        if not match:
            raise ValueError(f"discovery.tables: no section {t['section']}")
        start, count = match[0]["addr"], match[0]["size"] // stride
    else:
        start, count = int(t["addr"]), int(t["count"])
    return [read_word(elf, secs, start + i * stride) for i in range(count)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("toml")
    ap.add_argument("elf")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    with open(a.toml, "rb") as f:
        cfg = tomllib.load(f)
    elf = open(a.elf, "rb").read()
    want = cfg["target"]["eboot_sha1"].lower()
    got = hashlib.sha1(elf).hexdigest()
    if got != want:
        print(f"{a.elf}: SHA-1 {got} does not match bbs.toml's {want}", file=sys.stderr)
        return 1

    try:
        secs = sections(elf)
        code = exec_ranges(secs)
        disc = cfg.get("discovery", {})
        out = []
        for e in disc.get("entries", []):
            if not in_code(int(e), code):
                raise ValueError(f"discovery.entries: 0x{int(e):08X} is not in an executable section")
            out.append(int(e))
        for t in disc.get("tables", []):
            for w in table_words(elf, secs, t):
                if w == 0:
                    continue
                if not in_code(w, code):
                    where = t.get("section") or f"0x{int(t['addr']):08X}"
                    raise ValueError(f"discovery.tables {where}: 0x{w:08X} is not in an executable section")
                out.append(w)
    except (ValueError, KeyError) as ex:
        print(f"{a.toml}: {ex}", file=sys.stderr)
        return 1

    with open(a.output, "w") as f:
        f.write("# generated from bbs.toml by tools/bbs_seeds.py; do not edit\n")
        f.writelines(f"0x{x:08X}\n" for x in out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
