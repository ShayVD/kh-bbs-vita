"""The import stubs of a PSP module and the return sites of their calls, for
the trace breakpoints (docs/oracle.md, "Firmware-call source").

The import table is read with tools/import_report.py, and names follow its
rule: psprecomp's runtime registrations, then tools/nid_names.txt, each only
if SHA-1 matches the NID, then a pinned name.
"""
import pathlib, sys

import import_report

ROOT = pathlib.Path(__file__).resolve().parents[2]
PSPRECOMP = ROOT / "third_party/psprecomp"
NAMES = ROOT / "tools/nid_names.txt"


def stubs(elf, psprecomp=PSPRECOMP, names=NAMES):
    """{stub_addr: (nid, name or None)} for every import of `elf`.
    `psprecomp` (a checkout) and `names` (a names file) may be None."""
    d, secs = import_report.load_elf(elf)
    rt = import_report.runtime_nids(psprecomp)
    hashed, pinned = import_report.extra_names(names)
    return {stub: (nid, import_report.resolve_name(nid, rt, {}, hashed, pinned))
            for _lib, nid, stub in import_report.imports(d, secs)}


def return_sites(elf, stub_addrs):
    """{site_addr: stub_addr}: the address after the delay slot of every
    `jal stub` in an executable section. A `j stub` (a tail call) has no
    return site of its own; they are counted and reported on stderr."""
    d, secs = import_report.load_elf(elf)
    stub_addrs = set(stub_addrs)
    sites, tail = {}, 0
    for pc, op, target in import_report.jumps(d, secs):
        if target in stub_addrs:
            if op == 3:
                sites[pc + 8] = target
            else:
                tail += 1
    if tail:
        print(f"imports: {tail} tail calls (j) into stubs; their returns are not traced",
              file=sys.stderr)
    return sites
