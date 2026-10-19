"""The firmware signature table tools/oracle/hle_sigs.txt (format in its
header): which argument registers and which return registers of a call mean
something."""
import collections, pathlib

from import_report import nid_of

SIGS = pathlib.Path(__file__).with_name("hle_sigs.txt")
RETS = ("i", "u", "l", "v", "d")

Sig = collections.namedtuple("Sig", "name args ret")   # args: register indices


def parse_nid(text):
    """A NID from "0x1234ABCD" or a function name (its SHA-1 NID)."""
    return int(text, 16) if text.lower().startswith("0x") else nid_of(text)


def load(path=SIGS):
    """{nid: Sig}. Raises OSError, or ValueError naming the bad line."""
    sigs = {}
    for n, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("#")[0].split()
        if not parts:
            continue
        try:
            key, nargs, ret = parts
            if ret not in RETS:
                raise ValueError(f"return type {ret!r} is not one of {', '.join(RETS)}")
            if "," in nargs:
                args = tuple(int(i) for i in nargs.split(","))
                if any(not 0 <= i < 8 for i in args) or len(set(args)) != len(args):
                    raise ValueError("argument indexes must be distinct and 0-7")
            elif 0 <= int(nargs) <= 8:
                args = tuple(range(int(nargs)))
            else:
                raise ValueError("argument count outside 0-8")
            nid = parse_nid(key)
        except ValueError as e:
            raise ValueError(f"{path}:{n}: {e}: {line.strip()!r}") from None
        if nid in sigs:
            raise ValueError(f"{path}:{n}: NID 0x{nid:08X} listed twice ({sigs[nid].name}, {key})")
        sigs[nid] = Sig(None if key.lower().startswith("0x") else key, args, ret)
    return sigs
