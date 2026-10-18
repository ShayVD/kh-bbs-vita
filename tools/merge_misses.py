#!/usr/bin/env python3
"""Fold khbbs's dispatch-miss log into bbs.toml's discovery.entries.

  tools/merge_misses.py build/misses.jsonl [--toml bbs.toml] [--elf game/EBOOT.dec.ELF]

The entries between the "# BEGIN merge_misses" and "# END merge_misses"
lines of bbs.toml are kept sorted; a new target goes in with the return
address of its first miss as a comment. Targets already listed anywhere in
discovery.entries are skipped. Targets that are not 4-byte aligned are
reported and not added. With --elf, a target outside the executable sections
is reported and not added too: a miss there is a wild jump or data called as
code, and seeding it would hide the real bug.

Only the lines between the markers are ever rewritten; every other byte of
bbs.toml is kept as it is. Every way the inputs can be wrong (no markers, a
log line that is not JSON or has no hex "target", a bad ELF, a block that
holds anything but entry lines) ends in one line on stderr and exit status 1,
with bbs.toml untouched. The result is parsed before it is written.
"""
import argparse, json, os, pathlib, re, shutil, sys, tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bbs_seeds import ElfError, sections, exec_ranges, in_code  # noqa: E402

BEGIN = re.compile(r"^\s*# BEGIN merge_misses")
END = re.compile(r"^\s*# END merge_misses")
ENTRY = re.compile(r"^\s*(0x[0-9A-Fa-f]{1,8}),(\s*#.*)?$")
HEX32 = re.compile(r"0x[0-9A-Fa-f]{1,8}")


class MergeError(Exception):
    """An input is unusable; the message is the one line we print."""


def read_log(path):
    """{target: ra text of its first miss} in file order."""
    first = {}
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as ex:
        raise MergeError(f"{path}: not UTF-8 ({ex})") from None
    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        where = f"{path}:{n}"
        try:
            m = json.loads(line)
        except (ValueError, RecursionError) as ex:
            raise MergeError(f"{where}: not valid JSON ({ex})") from None
        if not isinstance(m, dict):
            raise MergeError(f"{where}: expected a JSON object")
        t = m.get("target")
        if not isinstance(t, str) or not HEX32.fullmatch(t):
            raise MergeError(f"{where}: 'target' must be a hex string like \"0x08804040\", got {t!r}")
        ra = m.get("ra")
        if ra is None:
            ra = "unknown"
        elif not isinstance(ra, str) or not HEX32.fullmatch(ra):
            raise MergeError(f"{where}: 'ra' must be a hex string, got {ra!r}")
        first.setdefault(int(t, 16), ra)
    return first


def find_block(lines, toml_path):
    """Indexes of the BEGIN and END marker lines; there must be exactly one pair."""
    b = [i for i, l in enumerate(lines) if BEGIN.match(l)]
    e = [i for i, l in enumerate(lines) if END.match(l)]
    if not b:
        raise MergeError(f"{toml_path}: no '# BEGIN merge_misses' line (needed inside discovery.entries)")
    if not e:
        raise MergeError(f"{toml_path}: no '# END merge_misses' line (needed inside discovery.entries)")
    if len(b) > 1 or len(e) > 1:
        raise MergeError(f"{toml_path}: more than one BEGIN/END merge_misses marker")
    if e[0] < b[0]:
        raise MergeError(f"{toml_path}: '# END merge_misses' comes before '# BEGIN merge_misses'")
    return b[0], e[0]


def merge(toml_path, log_path, elf_path):
    """Returns (new bbs.toml text or None if unchanged, report lines)."""
    raw = pathlib.Path(toml_path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as ex:
        raise MergeError(f"{toml_path}: not UTF-8 ({ex})") from None
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as ex:
        raise MergeError(f"{toml_path}: {ex}") from None
    entries = cfg.get("discovery", {}).get("entries", [])
    if not isinstance(entries, list) or any(type(e) is not int for e in entries):
        raise MergeError(f"{toml_path}: discovery.entries must be an array of integers")
    known = set(entries)

    code = None
    if elf_path:
        try:
            code = exec_ranges(sections(pathlib.Path(elf_path).read_bytes()))
        except ElfError as ex:
            raise MergeError(f"{elf_path}: {ex}") from None
    misses = read_log(log_path)

    lines = text.split("\n")            # not splitlines(): that also splits on \f, \v, U+2028
    b, e = find_block(lines, toml_path)
    eol = "\r" if lines[b].endswith("\r") else ""
    block = {}
    for l in lines[b + 1:e]:
        s = l.rstrip("\r")
        if not s.strip():
            continue
        m = ENTRY.match(s)
        if not m:
            raise MergeError(f"{toml_path}: only '0xADDR,  # comment' lines may sit between the "
                             f"merge_misses markers, not: {s.strip()!r}")
        block.setdefault(int(m.group(1), 16), s)

    report, added = [], 0
    for t, ra in sorted(misses.items()):
        if t in known or t in block:
            continue
        if t % 4:
            report.append(f"not added: 0x{t:08X} (ra {ra}) is not 4-byte aligned")
        elif code is not None and not in_code(t, code):
            report.append(f"not added: 0x{t:08X} (ra {ra}) is outside the executable sections")
        else:
            block[t] = f"    0x{t:08X},  # miss, ra {ra}"
            added += 1

    new_lines = lines[:b + 1] + [block[t] + eol for t in sorted(block)] + lines[e:]
    new = "\n".join(new_lines)
    try:
        got = tomllib.loads(new).get("discovery", {}).get("entries", [])
    except tomllib.TOMLDecodeError as ex:
        raise MergeError(f"{toml_path}: the merged file does not parse ({ex}); not written") from None
    if not set(block) <= set(got):
        raise MergeError(f"{toml_path}: the merge_misses markers are not inside discovery.entries")
    report.append(f"added {added}, {len(block)} merged entries in total")
    return (new if new != text else None), report


def write_atomic(path, text):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--toml", default="bbs.toml")
    ap.add_argument("--elf")
    a = ap.parse_args(argv)
    try:
        new, report = merge(a.toml, a.log, a.elf)
        if new is not None:
            write_atomic(a.toml, new)
    except MergeError as ex:
        print(ex, file=sys.stderr)
        return 1
    except OSError as ex:
        print(f"merge_misses: {ex}", file=sys.stderr)
        return 1
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
