#!/usr/bin/env python3
"""Compile probe functions and compare them with the game, word for word.

Each function named func_XXXXXXXX in the source is compared with the bytes of
the decrypted EBOOT at 0xXXXXXXXX. Words that carry relocations in the object
(jal targets, %hi/%lo halves) are compared with their relocated field masked,
so a match means "same instructions, same registers, same scheduling".

  tools/probe_match.py game/EBOOT.dec.ELF tests/toolchain/probes.c \
      --cc snc --flags=-O2 [--flags=-O3 ...]

--cc snc  runs tools/toolchain/pspsnc_1.2.7503.0/pspsnc.exe through wibo-macos.
--cc gcc  runs Sony psp-gcc 1.3.1 (GCC 3.3.3) in the toolchain container (tools/tc).
"""
import argparse
import pathlib
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TC = ROOT / "tools" / "toolchain"

R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16 = 4, 5, 6


def compile_probe(cc, flags, src, out):
    if cc == "snc":
        cmd = [str(TC / "wibo-macos"), str(TC / "pspsnc_1.2.7503.0" / "pspsnc.exe"),
               "-c", *flags, "-o", str(out), str(src)]
    else:
        rel = lambda p: "/work/" + str(pathlib.Path(p).resolve().relative_to(ROOT))
        gcc = "/work/tools/toolchain/psp-gcc-1.3.1/bin"
        cmd = [str(ROOT / "tools" / "tc"), f"{gcc}/psp-gcc", f"-B{gcc}/", "-G0", "-c",
               *flags, "-o", rel(out), rel(src)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def elf_sections(d):
    shoff, = struct.unpack_from("<I", d, 0x20)
    shnum, shstrndx = struct.unpack_from("<HH", d, 0x30)
    sh = [struct.unpack_from("<IIIIIIIIII", d, shoff + i * 40) for i in range(shnum)]
    names = sh[shstrndx][4]
    return [(d[names + s[0]:].split(b"\0")[0].decode(), s) for s in sh]


def object_functions(path):
    """{name: (words, {word index: reloc type})} for every FUNC symbol."""
    d = path.read_bytes()
    secs = elf_sections(d)
    funcs = {}
    for _, s in secs:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        strtab = secs[s[6]][1][4]
        for i in range(s[5] // 16):
            nm, val, size, info, _, shndx = struct.unpack_from("<IIIBBH", d, s[4] + i * 16)
            name = d[strtab + nm:].split(b"\0")[0].decode()
            if info & 0xF == 2 and name.startswith("func_") and size:
                sec = secs[shndx][1]
                words = list(struct.unpack_from(f"<{size // 4}I", d, sec[4] + val))
                funcs[name] = (shndx, val, words, {})
    for _, s in secs:
        if s[1] != 9:  # SHT_REL
            continue
        target = s[7]
        for i in range(s[5] // 8):
            off, info = struct.unpack_from("<II", d, s[4] + i * 8)
            for shndx, val, words, rel in funcs.values():
                if shndx == target and val <= off < val + 4 * len(words):
                    rel[(off - val) // 4] = info & 0xFF
    return {n: (w, r) for n, (_, _, w, r) in funcs.items()}


def game_words(elf, addr, n):
    d = elf.read_bytes()
    for _, s in elf_sections(d):
        if s[1] == 1 and s[3] <= addr < s[3] + s[5]:
            return list(struct.unpack_from(f"<{n}I", d, s[4] + addr - s[3]))
    raise SystemExit(f"0x{addr:08X} is not in a PROGBITS section of {elf}")


def masked(w, rtype):
    if rtype == R_MIPS_26:
        return w & 0xFC000000
    if rtype in (R_MIPS_HI16, R_MIPS_LO16):
        return w & 0xFFFF0000
    return w


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("elf", type=pathlib.Path)
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("--cc", choices=["snc", "gcc"], default="snc")
    ap.add_argument("--flags", action="append", default=None,
                    help="one flag set per use, e.g. --flags=-O2 --flags='-O3 -Xfastmath'")
    a = ap.parse_args()
    ok = True
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
        for fl in a.flags or ["-O2"]:
            obj = pathlib.Path(tmp) / "probe.o"
            compile_probe(a.cc, fl.split(), a.source, obj)
            res = []
            for name, (words, rel) in sorted(object_functions(obj).items()):
                gw = game_words(a.elf, int(name[5:], 16), len(words))
                same = all(masked(x, rel.get(i)) == masked(y, rel.get(i))
                           for i, (x, y) in enumerate(zip(words, gw)))
                res.append(f"{name[5:]}:{'match' if same else 'DIFF'}")
                ok &= same
            print(f"{a.cc} {fl:12} {' '.join(res)}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    (ROOT / "build").mkdir(exist_ok=True)
    main()
