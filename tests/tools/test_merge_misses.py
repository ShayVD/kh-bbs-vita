"""merge_misses.py on a synthetic miss log, bbs.toml and ELF. No game data."""
import contextlib, io, json, os, pathlib, shutil, subprocess, sys, tempfile, tomllib, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
import merge_misses
from elfgen import make_elf, words, SHF_ALLOC, SHF_EXECINSTR

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "merge_misses.py"
TEXT = 0x08804040
ELF = make_elf([(".text", TEXT, SHF_ALLOC | SHF_EXECINSTR, words(*([0] * 64)))])

TOML = """[target]
eboot_sha1 = "x"

[discovery]
entries = [
    0x08804040,  # hand-added
    # BEGIN merge_misses: tools/merge_misses.py adds entries here
    0x08804080,  # miss, ra 0x08804000
    # END merge_misses
]
"""


def miss(t, ra=0x08804010, order=0, stepped=False, seq=0, last="null"):
    """One log line, laid out exactly as runtime/misslog.c writes it
    (tests/runtime/test_misslog.c checks the C side against the same text)."""
    return (f'{{"seq":{seq},"order":{order},"stepped":{"true" if stepped else "false"},'
            f'"target":"0x{t:08X}","ra":"0x{ra:08X}","sp":"0x083FFFC0","last":{last}}}')


class MergeBase(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def run_tool(self, log, toml=TOML, elf=ELF, extra=()):
        """log: list of lines or raw bytes; toml: str or bytes. Returns the
        process and the bbs.toml bytes afterwards."""
        d = self.dir
        raw = toml if isinstance(toml, bytes) else toml.encode()
        (d / "bbs.toml").write_bytes(raw)
        (d / "m.jsonl").write_bytes(log if isinstance(log, bytes) else ("\n".join(log) + "\n").encode())
        args = [sys.executable, str(TOOL), str(d / "m.jsonl"), "--toml", str(d / "bbs.toml"), *extra]
        if elf is not None:
            (d / "e.elf").write_bytes(elf)
            args += ["--elf", str(d / "e.elf")]
        self.raw = raw
        return subprocess.run(args, capture_output=True, text=True), (d / "bbs.toml").read_bytes()

    def assertRefused(self, r, after, needle):
        """Exit 1, one clean stderr line naming the problem, bbs.toml untouched."""
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertEqual(after, self.raw, "bbs.toml was modified")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         sorted(["bbs.toml", "m.jsonl", "e.elf"][:3 if (self.dir / "e.elf").exists() else 2]),
                         "stray files left behind")


class MergeMisses(MergeBase):
    def test_adds_new_sorted_dedup(self):
        # 0x08804040 is hand-added, 0x08804080 already merged, 0x08804060 seen twice.
        r, after = self.run_tool([miss(0x088040C0), miss(0x08804060), miss(0x08804060, ra=0x08804020),
                                  miss(0x08804040), miss(0x08804080)])
        self.assertEqual(r.returncode, 0, r.stderr)
        text = after.decode()
        entries = tomllib.loads(text)["discovery"]["entries"]
        self.assertEqual(entries, [0x08804040, 0x08804060, 0x08804080, 0x088040C0])
        self.assertIn("0x08804060,  # miss, ra 0x08804010", text)     # the first miss's ra
        self.assertIn("added 2", r.stdout)

    def test_second_run_changes_nothing(self):
        log = [miss(0x088040C0), miss(0x08804060)]
        r, once = self.run_tool(log)
        self.assertEqual(r.returncode, 0, r.stderr)
        r, twice = self.run_tool(log, toml=once)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(once, twice)
        self.assertIn("added 0", r.stdout)

    def test_block_is_resorted(self):
        toml = TOML.replace("    0x08804080,  # miss, ra 0x08804000\n",
                            "    0x088040C0,  # miss, ra 0x1\n    0x08804060,  # miss, ra 0x2\n")
        r, after = self.run_tool([miss(0x08804080)], toml=toml)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tomllib.loads(after.decode())["discovery"]["entries"],
                         [0x08804040, 0x08804060, 0x08804080, 0x088040C0])
        self.assertIn("0x08804060,  # miss, ra 0x2", after.decode())     # old comments survive

    def test_outside_the_block_is_byte_identical(self):
        # Odd but valid TOML around the block: CRLF, tabs, trailing spaces,
        # non-ASCII, a comment after END, other tables, no final newline.
        head = '# café   header  \r\n[target]\r\neboot_sha1 = "x"\t\r\n\r\n[discovery]\r\nentries = [\r\n    0x08804040,\r\n'
        tail = ']\r\n\r\n[[discovery.tables]]\r\nsection = ".cplinit" # ü\r\nstride = 8'
        toml = (head + "    # BEGIN merge_misses: x\r\n    # END merge_misses\r\n" + tail).encode()
        r, after = self.run_tool([miss(0x08804060)], toml=toml)
        self.assertEqual(r.returncode, 0, r.stderr)
        h, t = head.encode() + b"    # BEGIN merge_misses: x\r\n", b"    # END merge_misses\r\n" + tail.encode()
        self.assertTrue(after.startswith(h) and after.endswith(t), after)
        self.assertEqual(after[len(h):-len(t)], b"    0x08804060,  # miss, ra 0x08804010\r\n")

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = merge_misses.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_default_elf_is_used_when_none_is_given(self):
        (self.dir / "bbs.toml").write_text(TOML)
        (self.dir / "m.jsonl").write_text(miss(0x0BAD0000) + "\n" + miss(0x08804060) + "\n")
        (self.dir / "e.elf").write_bytes(ELF)
        old = merge_misses.DEFAULT_ELF
        merge_misses.DEFAULT_ELF = self.dir / "e.elf"
        self.addCleanup(setattr, merge_misses, "DEFAULT_ELF", old)
        rc, out, err = self.run_main([str(self.dir / "m.jsonl"), "--toml", str(self.dir / "bbs.toml")])
        self.assertEqual(rc, 0, err)
        text = (self.dir / "bbs.toml").read_text()
        self.assertIn("0x08804060", text)
        self.assertNotIn("0x0BAD0000", text)      # a wild target never reaches bbs.toml
        self.assertIn("0x0BAD0000", out)

    def test_default_elf_missing_is_refused(self):
        (self.dir / "bbs.toml").write_text(TOML)
        (self.dir / "m.jsonl").write_text(miss(0x0BAD0000) + "\n")
        old = merge_misses.DEFAULT_ELF
        merge_misses.DEFAULT_ELF = self.dir / "no" / "EBOOT.dec.ELF"
        self.addCleanup(setattr, merge_misses, "DEFAULT_ELF", old)
        rc, out, err = self.run_main([str(self.dir / "m.jsonl"), "--toml", str(self.dir / "bbs.toml")])
        self.assertEqual(rc, 1)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("--elf", err)
        self.assertEqual((self.dir / "bbs.toml").read_text(), TOML)

    def test_default_elf_path_is_the_repos_game_dir(self):
        self.assertEqual(merge_misses.DEFAULT_ELF,
                         pathlib.Path(__file__).resolve().parents[2] / "game" / "EBOOT.dec.ELF")

    def test_only_first_misses_are_merged_unless_all(self):
        # A run: 0x...60 first, then 0x...C0 after a stepped-over miss. Another run
        # misses 0x...A0 first.
        log = [miss(0x08804060, stepped=True), miss(0x088040C0, order=1, seq=1),
               miss(0x088040A0, order=0, seq=2)]
        r, after = self.run_tool(log)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tomllib.loads(after.decode())["discovery"]["entries"],
                         [0x08804040, 0x08804060, 0x08804080, 0x088040A0])
        self.assertIn("skipped (after a stepped-over miss, rerun to confirm): 0x088040C0", r.stdout)
        r, after = self.run_tool(log, extra=["--all"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tomllib.loads(after.decode())["discovery"]["entries"],
                         [0x08804040, 0x08804060, 0x08804080, 0x088040A0, 0x088040C0])
        self.assertNotIn("skipped", r.stdout)

    def test_a_target_trusted_in_any_run_is_merged(self):
        # order 1 in one run, order 0 in another: the second run vouches for it.
        r, after = self.run_tool([miss(0x08804060, order=2), miss(0x08804060, order=0, ra=0x08804020)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"0x08804060,  # miss, ra 0x08804020", after)

    def test_already_listed_target_is_reported(self):
        r, after = self.run_tool([miss(0x08804040), miss(0x08804080)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already listed but still missed: 0x08804040 (stale build?)", r.stdout)
        self.assertIn("already listed but still missed: 0x08804080 (stale build?)", r.stdout)
        self.assertEqual(after, self.raw)

    def test_old_format_log_warns_once(self):
        old = '{"seq":0,"target":"0x08804060","ra":"0x08804010","sp":"0x0","last":null}'
        r, after = self.run_tool([old, old.replace("60", "A0")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.strip().splitlines(),
                         ["merge_misses: old-format miss log: treating every line as trusted"])
        self.assertIn(b"0x08804060", after)
        self.assertIn(b"0x088040A0", after)
        r, _ = self.run_tool([miss(0x08804060)])
        self.assertEqual(r.stderr, "")

    def test_real_log_line_with_a_last_function(self):
        line = ('{"seq":3,"order":0,"stepped":false,"target":"0x08804060","ra":"0x08A8BE28",'
                '"sp":"0x083FFFC0","last":"0x08A8BD58"}')
        r, after = self.run_tool([line])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"0x08804060,  # miss, ra 0x08A8BE28", after)

    def test_symlinked_bbs_toml_is_written_through(self):
        real = self.dir / "real.toml"
        link = self.dir / "link.toml"
        real.write_text(TOML)
        link.symlink_to(real)
        (self.dir / "m.jsonl").write_text(miss(0x08804060) + "\n")
        (self.dir / "e.elf").write_bytes(ELF)
        rc, out, err = self.run_main([str(self.dir / "m.jsonl"), "--toml", str(link), "--elf", str(self.dir / "e.elf")])
        self.assertEqual(rc, 0, err)
        self.assertTrue(link.is_symlink())
        self.assertIn("0x08804060", real.read_text())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         ["e.elf", "link.toml", "m.jsonl", "real.toml"])

    def test_file_mode_is_kept(self):
        (self.dir / "bbs.toml").write_text(TOML)
        os.chmod(self.dir / "bbs.toml", 0o640)
        (self.dir / "m.jsonl").write_text(miss(0x08804060) + "\n")
        (self.dir / "e.elf").write_bytes(ELF)
        rc, out, err = self.run_main([str(self.dir / "m.jsonl"), "--toml", str(self.dir / "bbs.toml"), "--elf", str(self.dir / "e.elf")])
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.dir / "bbs.toml").stat().st_mode & 0o777, 0o640)

    def test_outside_code_reported_not_added(self):
        r, after = self.run_tool([miss(0x0BAD0000)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(b"0x0BAD0000", after)
        self.assertIn("0x0BAD0000", r.stdout)
        self.assertEqual(after, self.raw)

    def test_misaligned_reported_not_added(self):
        r, after = self.run_tool([miss(0x08804062)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(b"0x08804062", after)
        self.assertIn("0x08804062", r.stdout)

    def test_empty_log_is_fine(self):
        r, after = self.run_tool([])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(after, self.raw)

    def test_null_ra_and_last(self):
        line = json.dumps({"seq": 0, "target": "0x08804060", "ra": None, "last": "0x08804044"})
        r, after = self.run_tool([line])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"0x08804060,  # miss, ra unknown", after)


class MergeRefusals(MergeBase):
    def test_missing_end_marker(self):
        r, after = self.run_tool([miss(0x08804060)], toml='[discovery]\nentries = []\n')
        self.assertRefused(r, after, "BEGIN merge_misses")      # no markers at all: BEGIN is reported first
        r, after = self.run_tool([miss(0x08804060)], toml=TOML.replace("    # END merge_misses\n", ""))
        self.assertRefused(r, after, "END merge_misses")

    def test_missing_begin_marker(self):
        r, after = self.run_tool([miss(0x08804060)], toml=TOML.replace("    # BEGIN merge_misses: tools/merge_misses.py adds entries here\n", ""))
        self.assertRefused(r, after, "BEGIN merge_misses")

    def test_markers_reversed_or_repeated(self):
        b = "    # BEGIN merge_misses: x\n"
        e = "    # END merge_misses\n"
        base = "[discovery]\nentries = [\n%s]\n"
        r, after = self.run_tool([miss(0x08804060)], toml=base % (e + b))
        self.assertRefused(r, after, "before")
        r, after = self.run_tool([miss(0x08804060)], toml=base % (b + e + b + e))
        self.assertRefused(r, after, "more than one")

    def test_markers_not_inside_entries(self):
        toml = TOML + '\n[other]\nxs = [\n    # BEGIN merge_misses: x\n    # END merge_misses\n]\n'
        toml = toml.replace("    # BEGIN merge_misses: tools/merge_misses.py adds entries here\n    0x08804080,  # miss, ra 0x08804000\n    # END merge_misses\n", "")
        r, after = self.run_tool([miss(0x08804060)], toml=toml)
        self.assertRefused(r, after, "not inside discovery.entries")

    def test_foreign_line_in_block(self):
        toml = TOML.replace("    # END merge_misses", "    # a note\n    # END merge_misses")
        r, after = self.run_tool([miss(0x08804060)], toml=toml)
        self.assertRefused(r, after, "a note")

    def test_bbs_toml_that_does_not_parse(self):
        r, after = self.run_tool([miss(0x08804060)], toml=TOML + "[[[\n")
        self.assertRefused(r, after, "bbs.toml")

    def test_log_line_not_json(self):
        r, after = self.run_tool([miss(0x08804060), "{not json", miss(0x08804080)])
        self.assertRefused(r, after, "m.jsonl:2")
        r, after = self.run_tool(["[1, 2]"])
        self.assertRefused(r, after, "m.jsonl:1")

    def test_truncated_last_line(self):
        # A run killed mid-write leaves half a line; say so rather than skip it.
        r, after = self.run_tool((miss(0x08804060) + "\n" + miss(0x088040C0)[:-9]).encode())
        self.assertRefused(r, after, "m.jsonl:2")

    def test_log_not_utf8(self):
        r, after = self.run_tool(b'\xff\xfe{"target":"0x08804060"}\n')
        self.assertRefused(r, after, "not UTF-8")

    def test_target_not_a_hex_string(self):
        for bad in (123, None, "zz", "0x", "08804060", "0x123456789", "0x08804060\n# x", ["0x1"]):
            with self.subTest(target=bad):
                r, after = self.run_tool([json.dumps({"target": bad, "ra": "0x1"})])
                self.assertRefused(r, after, "'target'")
        r, after = self.run_tool([json.dumps({"ra": "0x1"})])
        self.assertRefused(r, after, "'target'")

    def test_order_and_stepped_must_have_their_types(self):
        for bad in ({"order": -1}, {"order": "0"}, {"order": True}, {"stepped": "yes"}, {"stepped": 1}):
            with self.subTest(bad=bad):
                r, after = self.run_tool([json.dumps({"target": "0x08804060", "ra": "0x1", **bad})])
                self.assertRefused(r, after, "m.jsonl:1")

    def test_ra_cannot_smuggle_text_into_the_comment(self):
        r, after = self.run_tool([json.dumps({"target": "0x08804060", "ra": "0x1\n[evil]"})])
        self.assertRefused(r, after, "'ra'")

    def test_bad_elf(self):
        r, after = self.run_tool([miss(0x08804060)], elf=b"not an elf")
        self.assertRefused(r, after, "e.elf")

    def test_missing_files(self):
        r, after = self.run_tool([miss(0x08804060)], extra=["--toml", str(self.dir / "nope.toml")])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        (self.dir / "m.jsonl").unlink()
        (self.dir / "e.elf").write_bytes(ELF)
        r = subprocess.run([sys.executable, str(TOOL), str(self.dir / "m.jsonl"), "--toml", str(self.dir / "bbs.toml"),
                            "--elf", str(self.dir / "e.elf")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual((self.dir / "bbs.toml").read_bytes(), self.raw)


if __name__ == "__main__":
    unittest.main()
