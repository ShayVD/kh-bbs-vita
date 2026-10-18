"""bbs_seeds.py on a synthetic ELF and bbs.toml. No game data."""
import hashlib, pathlib, struct, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from elfgen import make_elf, words, SHF_ALLOC, SHF_EXECINSTR, SHT_NOBITS

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "bbs_seeds.py"

TEXT = 0x08804040
CPL = 0x08B30970      # .cplinit: 3 words * 2 = 24 bytes
PTRS = 0x08B30A00     # .ptrs: two plain code pointers
ROD = 0x08B31348      # .rodata: two code pointers, then a non-pointer
ELF = make_elf([
    (".text", TEXT, SHF_ALLOC | SHF_EXECINSTR, words(*([0x03E00008, 0] * 8))),
    # (function, 0) pairs, and a null entry, like the game's .cplinit
    (".cplinit", CPL, SHF_ALLOC, words(TEXT + 8, 0, TEXT + 16, 0, 0, 0)),
    (".ptrs", PTRS, SHF_ALLOC, words(TEXT + 4, TEXT + 12)),
    (".rodata", ROD, SHF_ALLOC, words(TEXT + 24, TEXT + 32, 0x12345678)),
    (".bss", 0x08B40000, SHF_ALLOC, b"", SHT_NOBITS, 64),
])
DUP_ELF = make_elf([
    (".text", TEXT, SHF_ALLOC | SHF_EXECINSTR, words(*([0x03E00008, 0] * 8))),
    (".tab", CPL, SHF_ALLOC, words(TEXT + 8)),
    (".tab", PTRS, SHF_ALLOC, words(TEXT + 4)),
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


def sha(elf=ELF):
    return hashlib.sha1(elf).hexdigest()


def table(body):
    return "\n[[discovery.tables]]\n" + body + "\n"


def patched(elf, off, fmt, *vals):
    b = bytearray(elf)
    struct.pack_into(fmt, b, off, *vals)
    return bytes(b)


class BbsSeeds(unittest.TestCase):
    def run_tool(self, toml_text, elf=ELF, stale=False, out_is_dir=False):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = pathlib.Path(d.name)
        (p / "bbs.toml").write_text(toml_text)
        (p / "e.elf").write_bytes(elf)
        if stale:
            (p / "seeds.txt").write_text("0xDEADBEEF\n")
        if out_is_dir:
            (p / "seeds.txt").mkdir()
        r = subprocess.run([sys.executable, str(TOOL), str(p / "bbs.toml"), str(p / "e.elf"),
                            "-o", str(p / "seeds.txt")], capture_output=True, text=True)
        self.assertFalse((p / "seeds.txt.tmp").exists())
        self.out = p / "seeds.txt"
        out = self.out.read_text() if self.out.is_file() else None
        return r, out

    def ok(self, toml_text, elf=ELF):
        r, out = self.run_tool(toml_text, elf)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [int(l, 16) for l in out.splitlines() if l and not l.startswith("#")]

    def refuse(self, toml_text, *needles, elf=ELF):
        """Exit 1, one-line message naming every needle, no traceback, and no
        output file, even when a stale one from an earlier run was there."""
        r, out = self.run_tool(toml_text, elf, stale=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertIsNone(out, "output file must not be created")

    # ---- what it writes

    def test_entries_and_tables(self):
        tables = table('section = ".cplinit"\nstride = 8') + table(f"addr = 0x{ROD:08X}\ncount = 2")
        got = self.ok(toml(sha(), entries=f"    0x{TEXT + 4:08X},\n", tables=tables))
        self.assertEqual(got, [TEXT, TEXT + 4, TEXT + 8, TEXT + 16, TEXT + 24, TEXT + 32])

    def test_section_default_stride_is_4(self):
        got = self.ok(toml(sha(), tables=table('section = ".ptrs"')))
        self.assertEqual(got, [TEXT, TEXT + 4, TEXT + 12])

    def test_uppercase_sha_accepted(self):
        self.assertEqual(self.ok(toml(sha().upper())), [TEXT])

    # ---- target and addresses

    def test_sha_mismatch_refused(self):
        self.refuse(toml("0" * 40), "e.elf: SHA-1")

    def test_failure_deletes_stale_output(self):
        r, out = self.run_tool(toml("0" * 40), stale=True)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.out.exists())

    def test_failed_write_leaves_no_tmp(self):
        # os.replace onto a directory fails after the tmp file was written
        r, _ = self.run_tool(toml(sha()), out_is_dir=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)

    def test_entry_outside_code_refused(self):
        self.refuse(toml(sha(), entries="    0x08B31348,\n"), "0x08B31348", "entries[1]")

    def test_misaligned_entry_refused(self):
        self.refuse(toml(sha(), entries=f"    0x{TEXT + 2:08X},\n"), f"0x{TEXT + 2:08X}")

    def test_table_word_outside_code_refused(self):
        self.refuse(toml(sha(), tables=table(f"addr = 0x{ROD:08X}\ncount = 3")),
                    "0x12345678", "discovery.tables[0]")

    # ---- table rules

    def tbl(self, body, *needles, elf=ELF):
        self.refuse(toml(sha(elf), tables=table(body)), "discovery.tables[0]", *needles, elf=elf)

    def test_unknown_section_refused(self):
        self.tbl('section = ".nope"', ".nope")

    def test_unknown_key_refused(self):
        self.tbl('section = ".ptrs"\nstrid = 4', "strid")

    def test_section_and_addr_refused(self):
        self.tbl(f'section = ".ptrs"\naddr = 0x{PTRS:08X}\ncount = 1', "exactly one")

    def test_neither_section_nor_addr_refused(self):
        self.tbl("stride = 4", "exactly one")

    def test_addr_without_count_refused(self):
        self.tbl(f"addr = 0x{PTRS:08X}", "count")

    def test_section_with_count_refused(self):
        self.tbl('section = ".ptrs"\ncount = 1', "count")

    def test_bad_stride_refused(self):
        for s in ("true", "0", "-4", "6", '"4"', "4.0"):
            with self.subTest(stride=s):
                self.tbl(f'section = ".ptrs"\nstride = {s}', "stride")

    def test_bad_count_refused(self):
        for c in ("0", "-1", "true", "2.0", '"2"'):
            with self.subTest(count=c):
                self.tbl(f"addr = 0x{PTRS:08X}\ncount = {c}", "count")

    def test_bad_addr_refused(self):
        for a in ("true", '"0x08B30A00"', f"{PTRS + 2}"):
            with self.subTest(addr=a):
                self.tbl(f"addr = {a}\ncount = 1", "addr")

    def test_section_size_not_multiple_of_stride_refused(self):
        self.tbl('section = ".cplinit"\nstride = 16', "stride")

    def test_table_with_no_seeds_refused(self):
        self.tbl(f"addr = 0x{CPL + 12:08X}\ncount = 3", "no code pointers")

    def test_range_outside_sections_refused(self):
        self.tbl(f"addr = 0x{PTRS:08X}\ncount = 3", "not in any section")

    def test_nobits_section_refused(self):
        self.tbl('section = ".bss"', ".bss")

    def test_duplicate_section_name_refused(self):
        self.tbl('section = ".tab"', ".tab", elf=DUP_ELF)

    def test_error_names_the_failing_table(self):
        tables = table('section = ".ptrs"') + table('section = ".nope"')
        self.refuse(toml(sha(), tables=tables), "discovery.tables[1]")

    # ---- config shape

    def test_missing_target_refused(self):
        self.refuse("[discovery]\nentries = []\n", "target")

    def test_missing_sha_key_refused(self):
        self.refuse("[target]\n[discovery]\nentries = []\n", "eboot_sha1")

    def test_non_string_sha_refused(self):
        self.refuse("[target]\neboot_sha1 = 12\n", "eboot_sha1")

    def test_toml_syntax_error_refused(self):
        self.refuse("[target\neboot_sha1 = 1\n", "bbs.toml")

    def test_entries_string_refused(self):
        self.refuse(f'[target]\neboot_sha1 = "{sha()}"\n[discovery]\nentries = "0x08804040"\n', "entries")

    def test_bad_entry_elements_refused(self):
        for e in ('"0x08804040"', "134234176.0", "true"):
            with self.subTest(entry=e):
                self.refuse(f'[target]\neboot_sha1 = "{sha()}"\n[discovery]\nentries = [{e}]\n', "entries[0]")

    def test_tables_as_single_table_refused(self):
        self.refuse(f'[target]\neboot_sha1 = "{sha()}"\n[discovery.tables]\nsection = ".ptrs"\n', "tables")

    def test_discovery_not_a_table_refused(self):
        self.refuse(f'discovery = 3\n[target]\neboot_sha1 = "{sha()}"\n', "discovery")

    def test_missing_files_refused(self):
        r = subprocess.run([sys.executable, str(TOOL), "/nonexistent.toml", "/nonexistent.elf",
                            "-o", "/nonexistent/out"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    # ---- malformed ELF (the SHA-1 of the damaged file is what bbs.toml pins)

    def damaged(self, elf, *needles):
        """ELF errors are prefixed with the ELF's path, not bbs.toml's."""
        self.refuse(toml(sha(elf)), "e.elf: ", *needles, elf=elf)
        r, _ = self.run_tool(toml(sha(elf)), elf)
        self.assertNotIn("bbs.toml:", r.stderr)

    def test_not_an_elf_refused(self):
        self.damaged(b"hello" * 20, "ELF")

    def test_bad_shentsize_refused(self):
        self.damaged(patched(ELF, 0x2E, "<H", 41), "section header")

    def test_bad_shstrndx_refused(self):
        self.damaged(patched(ELF, 0x32, "<H", 0), "shstrndx")

    def test_section_table_past_end_refused(self):
        self.damaged(patched(ELF, 0x20, "<I", len(ELF) - 40), "section header")

    def test_section_data_past_end_refused(self):
        shoff = struct.unpack_from("<I", ELF, 0x20)[0]
        self.damaged(patched(ELF, shoff + 40 + 20, "<I", 0x100000), "section 1")

    def test_truncated_header_refused(self):
        self.damaged(ELF[:0x20], "ELF")


if __name__ == "__main__":
    unittest.main()
