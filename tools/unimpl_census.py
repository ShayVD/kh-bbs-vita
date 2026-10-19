#!/usr/bin/env python3
"""Count what the recompiler could not translate, in the generated C.

  tools/unimpl_census.py build/pc/gen            # summary; exit 1 if anything is left
  tools/unimpl_census.py build/pc/gen --sites    # also one line per site

Reads main_funcs_*.c only (the code of .text); exit 2 if there are none.
Gated, so a non-zero count fails the run:
  - psp_unimplemented(...) sites, by mnemonic; unnamed encodings by raw word
  - branches the emitter has no condition for ("unhandled branch")
Reported, not gated:
  - static targets discovery never reached: a call, a branch, or a fall into
    a body owned by another function (all go through psp_dispatch)
  - guest `break` sites (psp_break), by code: the game's own traps. Code 7 is the
    compiler's integer divide-by-zero check, 6 overflow, 0 a plain assert
Every occurrence is counted; one the patterns cannot parse shows as <unparsed>
rather than vanishing, so a change in the emitter's output cannot hide a site.
"""
import argparse, collections, pathlib, re, sys

# The comment the emitter writes before every instruction: address, mnemonic, operands.
COMMENT = re.compile(r"/\* ([0-9A-F]{8})  (\S+)\s*(.*?)\s*\*/")
UNIMPL_MARK = "psp_unimplemented("
UNIMPL = re.compile(r'psp_unimplemented\(0x([0-9A-Fa-f]{1,8})u, "([^"]*)"\)')
BREAK = re.compile(r"psp_break\(0x([0-9A-Fa-f]{1,8})u, 0x([0-9A-Fa-f]{1,5})u\)")
UNHANDLED_MARK = "unhandled branch"
# psp_dispatch to a static target: tagged (a call, or a fall into another body),
# or untagged inside a non-likely branch: `if (_c) { psp_dispatch(0x...u); return; }`.
UNDISCOVERED = re.compile(r"psp_dispatch\(0x([0-9A-Fa-f]{1,8})u\);  /\* not discovered \*/")
UNDISCOVERED_BRANCH = re.compile(r"if \(_c\) \{ psp_dispatch\(0x([0-9A-Fa-f]{1,8})u\);")
FALLS_INTO = re.compile(r"/\* falls into 0x[0-9A-Fa-f]+, owned by another function \*/")
KINDS = (("unimplemented", "unimplemented"), ("branch", "unhandled branches"),
         ("undiscovered", "undiscovered static targets"),
         ("break", "guest break sites"))
UNKNOWN = "????????"


def source_files(gen):
    return sorted(pathlib.Path(gen).glob("main_funcs_*.c"))


def scan(paths, bare=None):
    """Yield (kind, addr, what) for every site in the given files. An
    unimplemented site carries its own address. A branch's is from the comment
    that precedes it. A static target's is the target: the comment before it
    is unreliable, because a delay slot's comment is written before the call.
    A file with no instruction comment at all is appended to `bare`."""
    for path in paths:
        last = (UNKNOWN, "?", "")       # the latest instruction comment
        prev = ""                       # the latest non-blank line
        seen = False
        for line in path.read_text(errors="replace").splitlines():
            c = COMMENT.search(line)
            if c:
                seen = True
                last = c.groups()
                line = line[:c.start()] + line[c.end():]    # sites may share its line
            parsed = 0
            for u in UNIMPL.finditer(line):
                parsed += 1
                addr, what = u.group(1).upper().zfill(8), u.group(2)
                if last[1] == ".word" and last[0] == addr:
                    what = f"{what} {last[2]}"
                yield "unimplemented", addr, what
            for _ in range(line.count(UNIMPL_MARK) - parsed):
                yield "unimplemented", last[0], "<unparsed>"
            for u in BREAK.finditer(line):
                yield "break", u.group(1).upper().zfill(8), f"code 0x{int(u.group(2), 16):05X}"
            for _ in range(line.count(UNHANDLED_MARK)):
                yield "branch", last[0], last[1]
            d = UNDISCOVERED.search(line)
            if d:
                what = "fallthrough" if FALLS_INTO.search(prev) else "call"
                yield "undiscovered", d.group(1).upper().zfill(8), what
            else:
                b = UNDISCOVERED_BRANCH.search(line)
                if b:
                    yield "undiscovered", b.group(1).upper().zfill(8), "branch"
            if line.strip():
                prev = line
        if not seen and bare is not None:
            bare.append(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gen", help="the generated-code directory, e.g. build/pc/gen")
    ap.add_argument("--sites", action="store_true", help="also list every site")
    a = ap.parse_args(argv)
    if not pathlib.Path(a.gen).is_dir():
        print(f"{a.gen}: not a directory", file=sys.stderr)
        return 2

    paths = source_files(a.gen)
    if not paths:
        print(f"{a.gen}: no main_funcs_*.c", file=sys.stderr)
        return 2

    counts = {k: collections.Counter() for k, _ in KINDS}
    sites = []
    bare = []
    for kind, addr, what in scan(paths, bare):
        counts[kind][what] += 1
        sites.append((addr, kind, what))
    if bare:
        for path in bare:
            print(f"{path}: no instruction comments (truncated?)", file=sys.stderr)
        return 2

    print(f"files {len(paths)}")
    for kind, title in KINDS:
        print(f"{title} {sum(counts[kind].values())}")
        for what, n in counts[kind].most_common():
            print(f"  {what} {n}")
    if a.sites:
        def order(site):                # unknown addresses last
            return (site[0] == UNKNOWN, 0 if site[0] == UNKNOWN else int(site[0], 16), site[1], site[2])
        for addr, kind, what in sorted(sites, key=order):
            print(f"0x{addr} {kind} {what}")
    return 1 if sum(counts["unimplemented"].values()) + sum(counts["branch"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
