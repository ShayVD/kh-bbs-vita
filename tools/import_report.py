#!/usr/bin/env python3
"""Report the firmware imports of a decrypted PSP module.

Reads the module's own import table (sceModuleInfo -> stub entries), counts
`jal` call sites per stub in executable sections, names each NID and marks
whether psprecomp's runtime registers it.

Names come from two places, both outside this repo and passed as arguments:
  --pspsdk DIR     a pspsdk checkout (BSD); its IMPORT_FUNC stub lines give
                   (library, NID, name) triples
  --psprecomp DIR  a psprecomp checkout; psp_hle_register(...) calls in
                   src/hle/*.c give the NIDs its runtime implements
  --names FILE     extra candidate names (tools/nid_names.txt)
A name is only trusted if SHA-1(name)[0:4] (little endian) equals the NID, or
if it comes from a pspsdk stub (some later firmware NIDs are not SHA-1 based).

Usage:
  tools/import_report.py MODULE.ELF --pspsdk DIR --psprecomp DIR [--md]
"""
import argparse
import collections
import hashlib
import pathlib
import re
import struct
import sys


def nid_of(name):
    return struct.unpack("<I", hashlib.sha1(name.encode()).digest()[:4])[0]


def load_elf(path):
    d = pathlib.Path(path).read_bytes()
    if d[:4] != b"\x7fELF":
        sys.exit(f"{path}: not an ELF (decrypt it first)")
    (_, _, _, _, entry, phoff, shoff, _, _, _, phn, _, shn, shstr) = struct.unpack_from(
        "<16sHHIIIIIHHHHHH", d)
    sh = [struct.unpack_from("<IIIIIIIIII", d, shoff + i * 40) for i in range(shn)]
    names = sh[shstr][4]
    secs = {}
    for s in sh:
        n = d[names + s[0]:].split(b"\0")[0].decode()
        secs[n] = dict(type=s[1], flags=s[2], addr=s[3], off=s[4], size=s[5])
    return d, secs


def reader(d, secs):
    def v2o(addr):
        for s in secs.values():
            if s["type"] == 1 and s["addr"] <= addr < s["addr"] + s["size"]:
                return s["off"] + addr - s["addr"]
        raise KeyError(hex(addr))

    def u32(addr):
        return struct.unpack_from("<I", d, v2o(addr))[0]

    def cstr(addr):
        o = v2o(addr)
        return d[o:d.index(b"\0", o)].decode()

    return u32, cstr


def imports(d, secs):
    u32, cstr = reader(d, secs)
    mi = secs[".rodata.sceModuleInfo"]["addr"]
    stub_top, stub_end = u32(mi + 0x2C), u32(mi + 0x30)
    out = []
    a = stub_top
    while a < stub_end:
        name_p = u32(a)
        ent_len = d[reader_off(secs, a + 8)]
        nfunc = struct.unpack_from("<H", d, reader_off(secs, a + 10))[0]
        nids_p, funcs_p = u32(a + 12), u32(a + 16)
        lib = cstr(name_p) if name_p else "?"
        for i in range(nfunc):
            out.append((lib, u32(nids_p + 4 * i), funcs_p + 8 * i))
        a += 4 * ent_len
    return out


def reader_off(secs, addr):
    for s in secs.values():
        if s["type"] == 1 and s["addr"] <= addr < s["addr"] + s["size"]:
            return s["off"] + addr - s["addr"]
    raise KeyError(hex(addr))


def jumps(d, secs):
    """Yield (pc, op, target) for every j (op 2) and jal (op 3) in executable sections."""
    for s in secs.values():
        if s["type"] == 1 and s["flags"] & 4:
            ws = struct.unpack_from(f"<{s['size'] // 4}I", d, s["off"])
            for i, w in enumerate(ws):
                if w >> 26 in (2, 3):
                    pc = s["addr"] + 4 * i
                    yield pc, w >> 26, ((pc + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)


def call_counts(d, secs):
    return collections.Counter(t for _, op, t in jumps(d, secs) if op == 3)


def sdk_names(root):
    names = {}
    if root:
        pat = re.compile(r'IMPORT_FUNC\s+"([^"]+)"\s*,\s*(0x[0-9A-Fa-f]+)\s*,\s*(\w+)')
        for f in pathlib.Path(root).rglob("*.S"):
            for lib, nid, name in pat.findall(f.read_text(errors="replace")):
                names.setdefault(int(nid, 16), name)
    return names


def extra_names(path):
    by_hash, pinned = [], {}
    if path:
        for line in pathlib.Path(path).read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) == 2:
                pinned[int(parts[0], 16)] = parts[1] + "?"
            else:
                by_hash.append(parts[0])
    return {nid_of(n): n for n in by_hash}, pinned


def runtime_nids(root):
    reg = {}
    if root:
        pat = re.compile(r'psp_hle_register(?:_unnamed)?\(\s*(0x[0-9A-Fa-f]+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]*)")?')
        for f in pathlib.Path(root, "src", "hle").glob("*.c"):
            for nid, lib, name in pat.findall(f.read_text()):
                reg[int(nid, 16)] = name or None
    return reg


def resolve_name(nid, rt, sdk, hashed, pinned):
    """The name of `nid`, or None: psprecomp's runtime, then pspsdk, then the
    names file, each only if SHA-1 matches (pspsdk names are trusted as they
    are), then a pinned name (ends in '?')."""
    name = rt.get(nid) or sdk.get(nid) or hashed.get(nid)
    if name and nid_of(name) != nid and nid not in sdk:
        name = None
    return name or pinned.get(nid)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module")
    ap.add_argument("--pspsdk")
    ap.add_argument("--psprecomp")
    ap.add_argument("--names")
    ap.add_argument("--md", action="store_true", help="markdown tables grouped by library")
    a = ap.parse_args()

    d, secs = load_elf(a.module)
    imps = imports(d, secs)
    calls = call_counts(d, secs)
    sdk = sdk_names(a.pspsdk)
    rt = runtime_nids(a.psprecomp)
    hashed, pinned = extra_names(a.names)

    rows = []
    for lib, nid, stub in imps:
        name = resolve_name(nid, rt, sdk, hashed, pinned)
        rows.append((lib, nid, stub, name or "?", calls[stub], nid in rt))

    bylib = collections.OrderedDict()
    for r in rows:
        bylib.setdefault(r[0], []).append(r)
    tot_called = sum(1 for r in rows if r[4])
    tot_impl = sum(1 for r in rows if r[5])
    tot_called_missing = sum(1 for r in rows if r[4] and not r[5])
    if a.md:
        print(f"{len(rows)} imported functions in {len(bylib)} libraries; "
              f"{tot_called} have at least one direct call site; "
              f"{tot_impl} are registered in psprecomp's runtime; "
              f"{tot_called_missing} are called and missing.\n")
        print("| Library | Imports | Called | In psprecomp | Called and missing |")
        print("|---|---:|---:|---:|---:|")
        for lib, rs in bylib.items():
            print(f"| {lib} | {len(rs)} | {sum(1 for r in rs if r[4])} | "
                  f"{sum(1 for r in rs if r[5])} | {sum(1 for r in rs if r[4] and not r[5])} |")
        for lib, rs in bylib.items():
            print(f"\n### {lib}\n\n| NID | Name | Call sites | psprecomp |\n|---|---|---:|---|")
            for _, nid, stub, name, n, impl in sorted(rs, key=lambda r: r[3]):
                print(f"| `{nid:08X}` | `{name}` | {n} | {'implemented' if impl else 'missing'} |")
    else:
        for lib, nid, stub, name, n, impl in rows:
            print(f"{lib:24} {nid:08X} {stub:08X} {n:5} {'impl' if impl else '----'}  {name}")


if __name__ == "__main__":
    main()
