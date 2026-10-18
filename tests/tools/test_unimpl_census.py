"""unimpl_census.py on synthetic generated C. No game data."""
import pathlib, subprocess, sys, tempfile, unittest

TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "unimpl_census.py"

SAMPLE = """\
L_08804000: PSP_MARK(0x08804000u);
    /* 08804000  mtv       $a0, S000 */
    psp_unimplemented(0x08804000u, "mtv");
    /* 08804004  .word     0xD0468202 */
    psp_unimplemented(0x08804004u, "vfpu?");
    /* 08804008  bvf       0, 0x08804020 */
    { int _c = (0 /* unhandled branch */);
    /* 0880400C  jal       0x08900000 */
    psp_dispatch(0x08900000u);  /* not discovered */
"""

def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)

class Census(unittest.TestCase):
    def test_counts_and_gate(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "main_funcs_000.c").write_text(SAMPLE)
            (pathlib.Path(d) / "main_imports.c").write_text('psp_unimplemented(0x1u, "unlisted import");\n')
            r = run(d)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("unimplemented 2", r.stdout)
            self.assertIn("  mtv 1", r.stdout)
            self.assertIn("  vfpu? 0xD0468202 1", r.stdout)
            self.assertIn("unhandled branches 1", r.stdout)
            self.assertIn("  bvf 1", r.stdout)
            self.assertIn("undiscovered static calls 1", r.stdout)
            self.assertNotIn("unlisted import", r.stdout)   # only main_funcs_*.c count

    def test_sites_lists_addresses(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "main_funcs_000.c").write_text(SAMPLE)
            r = run(d, "--sites")
            self.assertIn("0x08804004 unimplemented vfpu? 0xD0468202", r.stdout)
            self.assertIn("0x08804008 branch bvf", r.stdout)

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "main_funcs_000.c").write_text("    /* 08804000  nop */\n    ;\n")
            r = run(d)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("unimplemented 0", r.stdout)

    def test_missing_dir_is_an_error(self):
        r = run("/nonexistent/gen")
        self.assertEqual(r.returncode, 2)

if __name__ == "__main__":
    unittest.main()
