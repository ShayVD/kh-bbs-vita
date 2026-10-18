"""unimpl_census.py on synthetic generated C. No game data."""
import pathlib, subprocess, sys, tempfile, unittest

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "unimpl_census.py"

# Shaped like the emitter's output: a comment per instruction, a delay slot's
# comment written between its branch's comment and the code the branch emits.
SAMPLE = """\
L_08804000: PSP_MARK(0x08804000u);
    /* 08804000  mtv       $a0, S000 */
    psp_unimplemented(0x08804000u, "mtv");
    /* 08804004  .word     0xD0468202 */
    psp_unimplemented(0x08804004u, "vfpu?");
    /* 08804008  bvf       0, 0x08804020 */
    { int _c = (0 /* unhandled branch */);
    /* 0880400C  mfv       $a1, S001 */
      psp_unimplemented(0x0880400Cu, "mfv");
      if (_c) goto L_08804020; }
    /* 08804010  bvtl      0x08804020 */
    if (0 /* unhandled branch */) {
    /* 08804014  nop */
    ;
    }
L_08804020:
    /* 08804020  break     0x7 */
    psp_unimplemented(0x08804020u, "break");
    /* 08804030  jal       0x08900000 */
    /* 08804034  addiu     $a0, $zero, 1 */
    r_a0 = 1;
    psp_dispatch(0x08900000u);  /* not discovered */
    /* 08804038  beq       $a0, $zero, 0x08900100 */
    { int _c = (r_a0 == r_zero);
    /* 0880403C  nop */
      ;
      if (_c) { psp_dispatch(0x08900100u); return; } }
    /* 08804040  sw        $a0, 0($sp) */
    psp_write32(r_sp + 0, r_a0);
    /* falls into 0x08804044, owned by another function */
    psp_dispatch(0x08804044u);  /* not discovered */
    PSP_SP_CHECK(0x08804044u);
    return;
"""

def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)

def gen(files):
    """A temp dir holding {name: text}; returns the TemporaryDirectory."""
    d = tempfile.TemporaryDirectory()
    for name, text in files.items():
        (pathlib.Path(d.name) / name).write_text(text)
    return d

class Census(unittest.TestCase):
    def lines(self, text, *args):
        if "/* 0" not in text:          # a real file always has instruction comments
            text = "    /* 08800000  nop */\n" + text
        with gen({"main_funcs_000.c": text}) as d:
            r = run(d, *args)
        return r.returncode, r.stdout.splitlines(), r

    def test_counts_and_gate(self):
        with gen({"main_funcs_000.c": SAMPLE,
                  "main_imports.c": 'psp_unimplemented(0x1u, "unlisted import");\n'}) as d:
            r = run(d)
        out = r.stdout.splitlines()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(out[0], "files 1")
        for line in ("unimplemented 4", "  mtv 1", "  mfv 1", "  break 1",
                     "  vfpu? 0xD0468202 1",
                     "unhandled branches 2", "  bvf 1", "  bvtl 1",
                     "undiscovered static targets 3", "  call 1", "  fallthrough 1", "  branch 1"):
            self.assertIn(line, out)
        self.assertNotIn("unlisted import", r.stdout)   # only main_funcs_*.c count

    def test_sites_lists_addresses(self):
        rc, out, _ = self.lines(SAMPLE, "--sites")
        for line in ("0x08804000 unimplemented mtv",
                     "0x08804004 unimplemented vfpu? 0xD0468202",
                     "0x0880400C unimplemented mfv",      # indented delay-slot site
                     "0x08804008 branch bvf",
                     "0x08804010 branch bvtl",            # `if (0 /* ... */) {` form
                     # the target, not the preceding (delay-slot) comment's address
                     "0x08900000 undiscovered call",
                     "0x08900100 undiscovered branch",
                     "0x08804044 undiscovered fallthrough"):
            self.assertIn(line, out)
        sites = [l for l in out if l.startswith("0x")]
        self.assertEqual(sites, sorted(sites, key=lambda l: int(l.split()[0], 16)))

    def test_clean_tree_passes(self):
        rc, out, r = self.lines("    /* 08804000  nop */\n    ;\n")
        self.assertEqual(rc, 0, r.stdout)
        self.assertIn("unimplemented 0", out)
        self.assertIn("unhandled branches 0", out)

    def test_undiscovered_alone_does_not_gate(self):
        rc, out, r = self.lines("    /* 08804000  jal 0x08900000 */\n"
                                "    psp_dispatch(0x08900000u);  /* not discovered */\n")
        self.assertEqual(rc, 0, r.stdout)
        self.assertIn("undiscovered static targets 1", out)

    def test_missing_dir_is_an_error(self):
        r = run("/nonexistent/gen")
        self.assertEqual(r.returncode, 2)

    def test_dir_without_main_funcs_is_an_error(self):
        with gen({"main_imports.c": "int x;\n"}) as d:
            r = run(d)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("no main_funcs_*.c", r.stderr)

    def test_sites_on_a_comment_line_are_counted(self):
        rc, out, _ = self.lines('    /* 08804000  nop */ psp_unimplemented(0x08804000u, "x");\n'
                                '    /* 08804004  nop */ { int _c = (0 /* unhandled branch */);\n')
        self.assertEqual(rc, 1)
        self.assertIn("unimplemented 1", out)
        self.assertIn("  x 1", out)
        self.assertIn("unhandled branches 1", out)

    def test_empty_file_is_an_error(self):
        with gen({"main_funcs_000.c": SAMPLE, "main_funcs_001.c": ""}) as d:
            r = run(d)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("main_funcs_001.c: no instruction comments (truncated?)", r.stderr)

    def test_two_calls_on_one_line_count_twice(self):
        rc, out, _ = self.lines('    psp_unimplemented(0x08804000u, "mtv"); '
                                'psp_unimplemented(0x08804004u, "mfv");\n')
        self.assertEqual(rc, 1)
        self.assertIn("unimplemented 2", out)

    def test_short_address_is_still_counted_and_gated(self):
        rc, out, _ = self.lines('    psp_unimplemented(0x8804008u, "mtv");\n')
        self.assertEqual(rc, 1)
        self.assertIn("unimplemented 1", out)
        self.assertIn("  mtv 1", out)

    def test_unparseable_site_is_counted_and_gated(self):
        rc, out, _ = self.lines('    psp_unimplemented(addr, name);\n')
        self.assertEqual(rc, 1)
        self.assertIn("unimplemented 1", out)
        self.assertIn("  <unparsed> 1", out)

    def test_unhandled_branch_in_any_form_is_counted(self):
        rc, out, _ = self.lines("    if (0 /* unhandled branch */) {\n"
                                "    if (_c && 1 /* unhandled branch: weird */) {\n")
        self.assertEqual(rc, 1)
        self.assertIn("unhandled branches 2", out)

    def test_any_word_comment_names_its_raw_word(self):
        rc, out, _ = self.lines("    /* 08804000  .word     0x12345678 */\n"
                                '    psp_unimplemented(0x08804000u, "?");\n')
        self.assertIn("  ? 0x12345678 1", out)

    def test_unknown_address_sorts_last(self):
        rc, out, _ = self.lines('    psp_unimplemented(addr, name);\n'
                                '    /* 08804000  nop */\n'
                                '    psp_unimplemented(0x08804000u, "mtv");\n', "--sites")
        sites = [l for l in out if l.startswith("0x")]
        self.assertEqual(sites[0].split()[0], "0x08804000")
        self.assertEqual(sites[-1].split()[0], "0x????????")

if __name__ == "__main__":
    unittest.main()
