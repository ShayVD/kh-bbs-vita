"""bbs_seeds.py on a synthetic ELF and bbs.toml. No game data."""
import hashlib, pathlib, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from elfgen import make_elf, words, SHF_ALLOC, SHF_EXECINSTR

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "bbs_seeds.py"

TEXT = 0x08804040
ELF = make_elf([
    (".text", TEXT, SHF_ALLOC | SHF_EXECINSTR, words(*([0x03E00008, 0] * 8))),
    # (function, 0) pairs, and a null entry, like the game's .cplinit
    (".cplinit", 0x08B30970, SHF_ALLOC, words(TEXT + 8, 0, TEXT + 16, 0, 0, 0)),
    (".rodata", 0x08B31348, SHF_ALLOC, words(TEXT + 24, TEXT + 32, 0x12345678)),
])


def toml(sha, entries="", tables=""):
    return f"""
[target]
eboot_sha1 = "{sha}"

[discovery]
entries = [
    0x{TEXT:08X},  # hand-added
    # BEGIN merge_misses
{entries}    # END merge_misses
]
{tables}
"""


class BbsSeeds(unittest.TestCase):
    def run_tool(self, toml_text, elf=ELF):
        d = tempfile.mkdtemp()
        p = pathlib.Path(d)
        (p / "bbs.toml").write_text(toml_text)
        (p / "e.elf").write_bytes(elf)
        r = subprocess.run([sys.executable, str(TOOL), str(p / "bbs.toml"), str(p / "e.elf"),
                            "-o", str(p / "seeds.txt")], capture_output=True, text=True)
        out = (p / "seeds.txt").read_text() if (p / "seeds.txt").exists() else ""
        return r, out

    def sha(self):
        return hashlib.sha1(ELF).hexdigest()

    def test_entries_and_tables(self):
        tables = """
[[discovery.tables]]
section = ".cplinit"
stride = 8

[[discovery.tables]]
addr = 0x08B31348
count = 2
"""
        r, out = self.run_tool(toml(self.sha(), entries=f"    0x{TEXT + 4:08X},\n", tables=tables))
        self.assertEqual(r.returncode, 0, r.stderr)
        got = [int(l, 16) for l in out.splitlines() if l and not l.startswith("#")]
        self.assertEqual(got, [TEXT, TEXT + 4, TEXT + 8, TEXT + 16, TEXT + 24, TEXT + 32])

    def test_sha_mismatch_refused(self):
        r, _ = self.run_tool(toml("0" * 40))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA-1", r.stderr)

    def test_entry_outside_code_refused(self):
        r, _ = self.run_tool(toml(self.sha(), entries="    0x08B31348,\n"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0x08B31348", r.stderr)

    def test_table_word_outside_code_refused(self):
        tables = '\n[[discovery.tables]]\naddr = 0x08B31348\ncount = 3\n'
        r, _ = self.run_tool(toml(self.sha(), tables=tables))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0x12345678", r.stderr)

    def test_unknown_section_refused(self):
        tables = '\n[[discovery.tables]]\nsection = ".nope"\n'
        r, _ = self.run_tool(toml(self.sha(), tables=tables))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".nope", r.stderr)


if __name__ == "__main__":
    unittest.main()
