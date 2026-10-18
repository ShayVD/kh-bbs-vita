#!/usr/bin/env python3
"""Count what the recompiler could not translate, in the generated C.

  tools/unimpl_census.py build/pc/gen            # summary; exit 1 if anything is left
  tools/unimpl_census.py build/pc/gen --sites    # also one line per site

Reads main_funcs_*.c only (the code of .text). Gated, so a non-zero count
fails the run:
  - psp_unimplemented(...) sites, by mnemonic; unnamed encodings by raw word
  - branches the emitter has no condition for ("0 /* unhandled branch */")
Reported, not gated:
  - static calls to code discovery never reached ("/* not discovered */")
"""
import argparse, collections, pathlib, re, sys

# The comment the emitter writes before every instruction: address, mnemonic, operands.
COMMENT = re.compile(r"/\* ([0-9A-F]{8})  (\S+)\s*(.*?) \*/")
UNIMPL = re.compile(r'psp_unimplemented\(0x([0-9A-F]{8})u, "([^"]*)"\)')
UNHANDLED = "0 /* unhandled branch */"
UNDISCOVERED = re.compile(r"psp_dispatch\(0x([0-9A-F]{8})u\);  /\* not discovered \*/")
KINDS = (("unimplemented", "unimplemented"), ("branch", "unhandled branches"),
         ("undiscovered", "undiscovered static calls"))


def scan(gen):
    """Yield (kind, addr, what) for every site in gen/main_funcs_*.c. The
    address is the instruction's, from the comment that precedes it."""
    for path in sorted(pathlib.Path(gen).glob("main_funcs_*.c")):
        last = ("????????", "?", "")
        for line in path.read_text(errors="replace").splitlines():
            c = COMMENT.search(line)
            if c:
                last = c.groups()
                continue
            u = UNIMPL.search(line)
            if u:
                what = u.group(2)
                if what == "vfpu?" and last[1] == ".word":
                    what = f"vfpu? {last[2]}"
                yield "unimplemented", u.group(1), what
            elif UNHANDLED in line:
                yield "branch", last[0], last[1]
            elif UNDISCOVERED.search(line):
                yield "undiscovered", last[0], "call"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gen", help="the generated-code directory, e.g. build/pc/gen")
    ap.add_argument("--sites", action="store_true", help="also list every site")
    a = ap.parse_args(argv)
    if not pathlib.Path(a.gen).is_dir():
        print(f"{a.gen}: not a directory", file=sys.stderr)
        return 2

    counts = {k: collections.Counter() for k, _ in KINDS}
    sites = []
    for kind, addr, what in scan(a.gen):
        counts[kind][what] += 1
        sites.append((addr, kind, what))

    for kind, title in KINDS:
        print(f"{title} {sum(counts[kind].values())}")
        for what, n in counts[kind].most_common():
            print(f"  {what} {n}")
    if a.sites:
        for addr, kind, what in sorted(sites):
            print(f"0x{addr} {kind} {what}")
    return 1 if sum(counts["unimplemented"].values()) + sum(counts["branch"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
