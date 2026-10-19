"""mem_diff.py on synthetic 4 KiB dumps and a synthetic ELF. Every value is made up."""
import collections, contextlib, io, json, pathlib, subprocess, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
from elfgen import SHF_ALLOC, SHF_EXECINSTR, SHT_NOBITS, make_elf  # noqa: E402
from oracle import mem_diff  # noqa: E402

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "oracle" / "mem_diff.py"
Result = collections.namedtuple("Result", "returncode stdout stderr")

BASE, SIZE = 0x087FFC00, 0x1000          # 1 KiB of kernel partition, then 3 KiB of user partition
TEXT, DATA, BSS = 0x08800000, 0x08800200, 0x08800300
RODATA, IMAGE_END = 0x08800480, 0x08800500   # a gap before .rodata: "image (no section)"
MERGED = (0x08800210, 0x08800218)        # 7 equal bytes apart: one range with the default gap
SEPARATE = 0x087FFD00                    # kernel partition
IGNORED = 0x08800900                     # user partition, inside IGNORE
IGNORE = "0x08800800-0x08800A00"


def sidecar(side, base=BASE, size=SIZE):
    return {"base": "0x%08X" % base, "size": size, "event": "sceKernelCreateThread:1",
            "side": side, "nid": "0x446D8DE6", "ra": "0x08A8BE30"}


class MemDiff(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.elf = self.tmp / "module.elf"
        self.elf.write_bytes(make_elf([
            (".text", TEXT, SHF_ALLOC | SHF_EXECINSTR, b"\0" * 0x200),
            (".data", DATA, SHF_ALLOC, b"\0" * 0x100),
            (".bss", BSS, SHF_ALLOC, b"", SHT_NOBITS, 0x100),
            (".rodata", RODATA, SHF_ALLOC, b"\0" * 0x80),
            (".comment", 0, 0, b"not loaded"),
        ]))
        self.a = bytearray(range(256)) * (SIZE // 256)

    def write(self, name, data, meta):
        """Write a dump and its sidecar; meta None: no sidecar (any old one is removed)."""
        path, side = self.tmp / name, self.tmp / (name + ".json")
        path.write_bytes(bytes(data))
        if meta is None:
            side.unlink(missing_ok=True)
        else:
            side.write_text(json.dumps(meta))
        return str(path)

    def planted(self, *addrs):
        b = bytearray(self.a)
        for addr in addrs:
            b[addr - BASE] ^= 0xFF
        return b

    def run_diff(self, b, *opts, a=None, meta_a=None, meta_b=None):
        """meta_a / meta_b: None for the default sidecar, False for none."""
        def meta(m, side):
            return sidecar(side) if m is None else None if m is False else m
        pa = self.write("a.bin", self.a if a is None else a, meta(meta_a, "khbbs"))
        pb = self.write("b.bin", b, meta(meta_b, "ppsspp"))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = mem_diff.main([pa, pb, *opts])
            except SystemExit as e:                     # argparse errors
                code = e.code
        return Result(code, out.getvalue(), err.getvalue())

    def ranges(self, stdout):
        """[(lo, hi, label)] from the range lines."""
        out = []
        for line in stdout.splitlines():
            parts = line.split()
            if parts and parts[0].startswith("0x") and "-" in parts[0]:
                lo, hi = (int(x, 16) for x in parts[0].split("-"))
                out.append((lo, hi, line.split("  ")[-1].strip()))
        return out

    def test_identical(self):
        r = self.run_diff(self.a, "--elf", str(self.elf))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("identical", r.stdout)

    def test_planted_differences(self):
        r = self.run_diff(self.planted(*MERGED, SEPARATE, IGNORED), "--elf", str(self.elf), "--ignore", IGNORE)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.ranges(r.stdout), [
            (SEPARATE, SEPARATE + 1, "kernel partition"),
            (MERGED[0], MERGED[1] + 1, ".data"),
        ])
        self.assertIn("3 bytes differ in 2 ranges (1 more inside --ignore)", r.stdout)
        summary = r.stdout.split("by section:")[1]
        self.assertRegex(summary, r"\.data\s+2 bytes in 1 range")
        self.assertRegex(summary, r"kernel partition\s+1 byte in 1 range")
        self.assertNotIn("user partition", summary)
        self.assertIn("khbbs", r.stdout)
        self.assertIn("ppsspp", r.stdout)

    def test_without_ignore_and_elf(self):
        r = self.run_diff(self.planted(*MERGED, SEPARATE, IGNORED))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.ranges(r.stdout), [
            (SEPARATE, SEPARATE + 1, "kernel partition"),
            (MERGED[0], MERGED[1] + 1, "user partition"),
            (IGNORED, IGNORED + 1, "user partition"),
        ])

    def test_only_ignored_differences(self):
        r = self.run_diff(self.planted(IGNORED), "--ignore", IGNORE, "--ignore", "0x08800000-0x08800010")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("0 bytes differ in 0 ranges (1 more inside --ignore)", r.stdout)

    def test_gap(self):
        r = self.run_diff(self.planted(*MERGED), "--gap", "6")
        self.assertEqual([x[:2] for x in self.ranges(r.stdout)],
                         [(MERGED[0], MERGED[0] + 1), (MERGED[1], MERGED[1] + 1)])
        r = self.run_diff(self.planted(*MERGED), "--gap", "7")
        self.assertEqual([x[:2] for x in self.ranges(r.stdout)], [(MERGED[0], MERGED[1] + 1)])

    def test_no_range_bridges_an_ignored_hole(self):
        lo, hi = 0x08800900, 0x08800908
        r = self.run_diff(self.planted(lo, hi), "--ignore", "0x08800903-0x08800905")
        self.assertEqual([x[:2] for x in self.ranges(r.stdout)], [(lo, lo + 1), (hi, hi + 1)])
        # Also when the hole holds an ignored difference, and when two holes touch.
        r = self.run_diff(self.planted(lo, lo + 4, hi), "--ignore", "0x08800903-0x08800904",
                          "--ignore", "0x08800904-0x08800906")
        self.assertEqual([x[:2] for x in self.ranges(r.stdout)], [(lo, lo + 1), (hi, hi + 1)])
        self.assertIn("(1 more inside --ignore)", r.stdout)
        # Without the hole they merge.
        r = self.run_diff(self.planted(lo, hi))
        self.assertEqual([x[:2] for x in self.ranges(r.stdout)], [(lo, hi + 1)])

    def test_malformed_elf(self):
        bad = bytearray(self.elf.read_bytes())
        bad[0x32:0x34] = (99).to_bytes(2, "little")      # e_shstrndx past the section table
        path = self.tmp / "bad.elf"
        path.write_bytes(bytes(bad))
        r = self.run_diff(self.a, "--elf", str(path))
        self.assertEqual(r.returncode, 2, r)
        self.assertIn("mem_diff: --elf", r.stderr)

    def test_ranges_split_at_section_boundaries(self):
        r = self.run_diff(self.planted(DATA - 2, DATA - 1, DATA, DATA + 1, BSS + 0x100, RODATA - 1,
                                       RODATA, IMAGE_END - 1, IMAGE_END), "--elf", str(self.elf))
        self.assertEqual(self.ranges(r.stdout), [
            (DATA - 2, DATA, ".text"), (DATA, DATA + 2, ".data"),
            (BSS + 0x100, BSS + 0x101, "image (no section)"),
            (RODATA - 1, RODATA, "image (no section)"),
            (RODATA, RODATA + 1, ".rodata"), (IMAGE_END - 1, IMAGE_END, ".rodata"),
            (IMAGE_END, IMAGE_END + 1, "user partition")])
        summary = r.stdout.split("by section:")[1]
        self.assertRegex(summary, r"image \(no section\)\s+2 bytes in 2 ranges")

    def test_runs_across_block_boundaries(self):
        with mock.patch.object(mem_diff, "BLOCK", 64):
            b = bytearray(self.a)
            for off in range(0x3F0, 0x450):           # spans two block edges
                b[off] ^= 1
            r = self.run_diff(b)
        self.assertEqual([x[:2] for x in self.ranges(r.stdout)], [(BASE + 0x3F0, BASE + 0x400),
                                                                  (BASE + 0x400, BASE + 0x450)])
        self.assertIn("96 bytes differ", r.stdout)

    def test_limit(self):
        r = self.run_diff(self.planted(*range(0x08800000, 0x08800100, 0x20)), "--limit", "3")
        self.assertEqual(len(self.ranges(r.stdout)), 3)
        self.assertIn("5 more ranges", r.stdout)

    def test_bad_input(self):
        bad = {
            "base": dict(meta_b=sidecar("ppsspp", base=BASE + 0x1000)),
            "size": dict(meta_b=sidecar("ppsspp", size=SIZE * 2)),
            "short file": dict(a=self.a[:-1]),
            "missing sidecar": dict(meta_a=False),
            "bad sidecar": dict(meta_a={"base": "zz", "size": SIZE}),
        }
        for what, kw in bad.items():
            with self.subTest(what):
                r = self.run_diff(self.a, **kw)
                self.assertEqual(r.returncode, 2, r)
                self.assertIn("mem_diff:", r.stderr)
        for spec in ("0x10", "0x20-0x10", "x-y", "0x08800000-"):
            with self.subTest(spec):
                self.assertEqual(self.run_diff(self.a, "--ignore", spec).returncode, 2)
        r = self.run_diff(self.a, "--elf", str(self.tmp / "a.bin"))
        self.assertEqual(r.returncode, 2)

    def test_different_events_warn(self):
        meta = dict(sidecar("ppsspp"), event="sceKernelCreateThread:2")
        r = self.run_diff(self.a, meta_b=meta)
        self.assertEqual(r.returncode, 0)
        self.assertIn("warning", r.stderr)

    def test_command_line(self):
        pa = self.write("a.bin", self.a, sidecar("khbbs"))
        pb = self.write("b.bin", self.planted(SEPARATE), sidecar("ppsspp"))
        r = subprocess.run([sys.executable, str(TOOL), pa, pb], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, str(TOOL), pa, pa], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
