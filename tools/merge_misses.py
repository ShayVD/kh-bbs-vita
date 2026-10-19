#!/usr/bin/env python3
"""Fold khbbs's dispatch-miss log into bbs.toml's discovery.entries.

  tools/merge_misses.py build/misses.jsonl [--toml bbs.toml] [--elf game/EBOOT.dec.ELF] [--all]

The entries between the "# BEGIN merge_misses" and "# END merge_misses"
lines of bbs.toml are kept sorted; a new target goes in with the return
address of its first miss as a comment. Targets already listed anywhere in
discovery.entries are reported as "already listed but still missed" (the
build that ran is probably stale) and skipped. Targets that are not 4-byte
aligned, or lie outside the executable sections of the ELF, are reported and
not added: a miss there is a wild jump or data called as code, and seeding it
would hide the real bug (and the next build would fail in bbs_seeds.py).
The ELF is --elf, default game/EBOOT.dec.ELF in the repo; the tool refuses to
run without one.

Only the first miss of each run ("order": 0 in the log) is fully trustworthy:
after a stepped-over miss the run carries on with made-up results. Later
misses are listed as skipped, to be confirmed by another run; --all merges
them anyway. A log line without an "order" field is from the old format: it is
treated as trusted, with one warning on stderr.

Only the lines between the markers are ever rewritten; every other byte of
bbs.toml is kept as it is. Every way the inputs can be wrong (no markers, a
log line that is not JSON or has no hex "target", a bad ELF, a block that
holds anything but entry lines) ends in one line on stderr and exit status 1,
with bbs.toml untouched. The result is parsed before it is written.
"""
import argparse, json, os, pathlib, re, shutil, sys, tempfile, tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bbs_seeds import ElfError, sections, exec_ranges, in_code  # noqa: E402

DEFAULT_ELF = pathlib.Path(__file__).resolve().parents[1] / "game" / "EBOOT.dec.ELF"
BEGIN = re.compile(r"^\s*# BEGIN merge_misses")
END = re.compile(r"^\s*# END merge_misses")
ENTRY = re.compile(r"^\s*(0x[0-9A-Fa-f]{1,8}),(\s*#.*)?$")
HEX32 = re.compile(r"0x[0-9A-Fa-f]{1,8}")


class MergeError(Exception):
    """An input is unusable; the message is the one line we print."""


def read_log(path):
    """{target: (ra text, trusted)}. ra is from the target's first order-0 line,
    else its first line; trusted means some run missed it as its first miss."""
    first = {}
    old_format = False
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
        old_format = old_format or "order" not in m
        order = m.get("order", 0)
        if type(order) is not int or order < 0:
            raise MergeError(f"{where}: 'order' must be a non-negative integer, got {order!r}")
        if type(m.get("stepped", False)) is not bool:
            raise MergeError(f"{where}: 'stepped' must be true or false, got {m['stepped']!r}")
        t = int(t, 16)
        if t not in first or (order == 0 and not first[t][1]):
            first[t] = (ra, order == 0)
    return first, old_format


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


def merge(toml_path, log_path, elf_path, merge_all=False):
    """Returns (new bbs.toml text or None if unchanged, report lines, warning lines)."""
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

    try:
        code = exec_ranges(sections(pathlib.Path(elf_path).read_bytes()))
    except ElfError as ex:
        raise MergeError(f"{elf_path}: {ex}") from None
    misses, old_format = read_log(log_path)

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
    for t, (ra, trusted) in sorted(misses.items()):
        if t in known or t in block:
            report.append(f"already listed but still missed: 0x{t:08X} (stale build?)")
        elif not trusted and not merge_all:
            report.append(f"skipped (after a stepped-over miss, rerun to confirm): 0x{t:08X} (ra {ra})")
        elif t % 4:
            report.append(f"not added: 0x{t:08X} (ra {ra}) is not 4-byte aligned")
        elif not in_code(t, code):
            report.append(f"not added: 0x{t:08X} (ra {ra}) is outside the executable sections")
        else:
            how = "miss" if trusted else "miss after a stepped-over miss"
            block[t] = f"    0x{t:08X},  # {how}, ra {ra}"
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
    warnings = ["old-format miss log: treating every line as trusted"] if old_format else []
    return (new if new != text else None), report, warnings


def write_atomic(path, text):
    """Replace `path` (or, if it is a symlink, the file it points to) with
    `text`: write a temporary file beside it, sync it, rename it over."""
    real = os.path.realpath(path)
    fd, tmp = tempfile.mkstemp(prefix=".merge_misses.", dir=os.path.dirname(real))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(real, tmp)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--toml", default="bbs.toml")
    ap.add_argument("--elf", help=f"default {DEFAULT_ELF}")
    ap.add_argument("--all", action="store_true",
                    help="also merge misses that came after a stepped-over miss")
    a = ap.parse_args(argv)
    try:
        elf = a.elf or str(DEFAULT_ELF)
        if not a.elf and not DEFAULT_ELF.is_file():
            raise MergeError(f"no --elf given and {DEFAULT_ELF} does not exist")
        new, report, warnings = merge(a.toml, a.log, elf, a.all)
        if new is not None:
            write_atomic(a.toml, new)
    except MergeError as ex:
        print(ex, file=sys.stderr)
        return 1
    except OSError as ex:
        print(f"merge_misses: {ex}", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"merge_misses: {w}", file=sys.stderr)
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
