#!/usr/bin/env python3
"""Write the recompiler's seeds file from bbs.toml.

  tools/bbs_seeds.py bbs.toml game/EBOOT.dec.ELF -o build/pc/gen/seeds.txt

Checks the ELF against [target].eboot_sha1, then writes one address per
line: discovery.entries first, then every non-zero word of each
discovery.tables entry. A table is a whole ELF section (`section`) or a
range (`addr` + `count`), read every `stride` bytes (default 4).

Every address must lie in an executable section. bbs.toml is maintained by
hand and by tools/merge_misses.py, and a typo there must fail the build
rather than seed data as code. Every way the inputs can be wrong ends in one
line on stderr and exit status 1; no output file is left behind.
"""
import argparse, hashlib, os, pathlib, struct, sys, tomllib

SHF_EXECINSTR = 0x4
SHT_NULL, SHT_NOBITS = 0, 8
SHDR_SIZE = 40
TABLE_KEYS = {"section", "addr", "count", "stride"}


def sections(elf):
    """The ELF's section headers as dicts: name, type, flags, addr, offset, size.
    Raises ValueError when the header or section table does not fit the file."""
    if len(elf) < 0x34 or elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError("not a little-endian ELF32 file")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x2E)
    if shentsize != SHDR_SIZE:
        raise ValueError(f"ELF section header size is {shentsize}, expected {SHDR_SIZE}")
    if not 0 < shstrndx < shnum:
        raise ValueError(f"ELF shstrndx {shstrndx} is not a section (shnum {shnum})")
    if shoff + shnum * SHDR_SIZE > len(elf):
        raise ValueError("ELF section header table runs past the end of the file")
    raw = [struct.unpack_from("<10I", elf, shoff + i * SHDR_SIZE) for i in range(shnum)]
    for i, (_, typ, _, _, off, size, *_) in enumerate(raw):
        if typ not in (SHT_NULL, SHT_NOBITS) and off + size > len(elf):
            raise ValueError(f"ELF section {i} runs past the end of the file")
    stroff, strsize = raw[shstrndx][4], raw[shstrndx][5]
    strtab = elf[stroff:stroff + strsize]
    out = []
    for i, (name, typ, flags, addr, off, size, *_) in enumerate(raw):
        end = strtab.find(b"\0", name)
        if end < 0:
            raise ValueError(f"ELF section {i} has a bad name offset")
        out.append({"name": strtab[name:end].decode("utf-8", "replace"), "type": typ,
                    "flags": flags, "addr": addr, "offset": off, "size": size})
    return out


def exec_ranges(secs):
    return [(s["addr"], s["addr"] + s["size"]) for s in secs
            if s["flags"] & SHF_EXECINSTR and s["size"]]


def in_code(addr, ranges):
    return addr % 4 == 0 and any(lo <= addr < hi for lo, hi in ranges)


def _int(v, where):
    """v as an int. TOML booleans and floats are not addresses or counts."""
    if type(v) is not int:
        raise ValueError(f"{where}: expected an integer, got {v!r}")
    return v


def read_word(elf, secs, addr):
    for s in secs:
        if s["type"] != SHT_NOBITS and s["addr"] <= addr and addr + 4 <= s["addr"] + s["size"]:
            return struct.unpack_from("<I", elf, s["offset"] + addr - s["addr"])[0]
    raise ValueError(f"0x{addr:08X} is not in any section with file data")


def table_words(elf, secs, t, where):
    """The words of one discovery.tables entry. `where` names it in errors."""
    if not isinstance(t, dict):
        raise ValueError(f"{where}: expected a table")
    bad = sorted(set(t) - TABLE_KEYS)
    if bad:
        raise ValueError(f"{where}: unknown key {bad[0]!r} (allowed: {', '.join(sorted(TABLE_KEYS))})")
    if ("section" in t) == ("addr" in t):
        raise ValueError(f"{where}: give exactly one of 'section' and 'addr'")
    stride = _int(t.get("stride", 4), f"{where}.stride")
    if stride <= 0 or stride % 4:
        raise ValueError(f"{where}.stride: {stride} is not a positive multiple of 4")
    if "section" in t:
        if "count" in t:
            raise ValueError(f"{where}: 'count' only goes with 'addr' (a section is read whole)")
        name = t["section"]
        if not isinstance(name, str):
            raise ValueError(f"{where}.section: expected a string, got {name!r}")
        match = [s for s in secs if s["name"] == name]
        if not match:
            raise ValueError(f"{where}: no section {name}")
        if len(match) > 1:
            raise ValueError(f"{where}: more than one section is named {name}")
        s = match[0]
        if s["type"] == SHT_NOBITS:
            raise ValueError(f"{where}: section {name} has no file data")
        if s["size"] % stride:
            raise ValueError(f"{where}: section {name} size {s['size']} is not a multiple of stride {stride}")
        return [struct.unpack_from("<I", elf, s["offset"] + i)[0] for i in range(0, s["size"], stride)]
    if "count" not in t:
        raise ValueError(f"{where}: 'addr' needs 'count'")
    start = _int(t["addr"], f"{where}.addr")
    count = _int(t["count"], f"{where}.count")
    if start < 0 or start % 4:
        raise ValueError(f"{where}.addr: 0x{start:X} is not a 4-byte-aligned address")
    if count <= 0:
        raise ValueError(f"{where}.count: {count} is not positive")
    try:
        return [read_word(elf, secs, start + i * stride) for i in range(count)]
    except ValueError as ex:
        raise ValueError(f"{where}: {ex}") from None


def resolve(cfg, elf, path):
    """The seed addresses for `cfg` (parsed bbs.toml) against `elf`."""
    target = cfg.get("target")
    if not isinstance(target, dict):
        raise ValueError("missing [target] table")
    want = target.get("eboot_sha1")
    if want is None:
        raise ValueError("missing key 'eboot_sha1' in [target]")
    if not isinstance(want, str):
        raise ValueError(f"[target].eboot_sha1: expected a string, got {want!r}")
    got = hashlib.sha1(elf).hexdigest()
    if got != want.lower():
        raise ValueError(f"{path}: SHA-1 {got} does not match bbs.toml's {want.lower()}")

    secs = sections(elf)
    code = exec_ranges(secs)
    disc = cfg.get("discovery", {})
    if not isinstance(disc, dict):
        raise ValueError("[discovery] is not a table")
    entries = disc.get("entries", [])
    tables = disc.get("tables", [])
    if not isinstance(entries, list):
        raise ValueError("discovery.entries: expected an array of addresses")
    if not isinstance(tables, list):
        raise ValueError("discovery.tables: expected an array of tables ([[discovery.tables]])")
    out = []
    for i, e in enumerate(entries):
        where = f"discovery.entries[{i}]"
        e = _int(e, where)
        if not in_code(e, code):
            raise ValueError(f"{where}: 0x{e:08X} is not a 4-byte-aligned address in an executable section")
        out.append(e)
    for i, t in enumerate(tables):
        where = f"discovery.tables[{i}]"
        found = 0
        for w in table_words(elf, secs, t, where):
            if w == 0:
                continue
            if not in_code(w, code):
                raise ValueError(f"{where}: word 0x{w:08X} is not a 4-byte-aligned address in an executable section")
            out.append(w)
            found += 1
        if not found:
            raise ValueError(f"{where}: no code pointers (every word is zero)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("toml")
    ap.add_argument("elf")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    try:
        cfg = tomllib.loads(pathlib.Path(a.toml).read_text(encoding="utf-8"))
        elf = pathlib.Path(a.elf).read_bytes()
        out = resolve(cfg, elf, a.elf)
        tmp = a.output + ".tmp"
        with open(tmp, "w") as f:
            f.write("# generated from bbs.toml by tools/bbs_seeds.py; do not edit\n")
            f.writelines(f"0x{x:08X}\n" for x in out)
        os.replace(tmp, a.output)
    except tomllib.TOMLDecodeError as ex:
        print(f"{a.toml}: {ex}", file=sys.stderr)
        return 1
    except KeyError as ex:
        print(f"{a.toml}: missing key {ex} in bbs.toml", file=sys.stderr)
        return 1
    except OSError as ex:
        print(f"bbs_seeds: {ex}", file=sys.stderr)
        return 1
    except (ValueError, TypeError, AttributeError, IndexError, struct.error) as ex:
        print(f"{a.toml}: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
