# P3: Recompiler Fidelity, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every instruction in the main module's `.text` that discovery reaches is translated, and every piece of code the boot path jumps to was discovered. Concretely: zero `psp_unimplemented` sites and zero unhandled branches in the generated C, zero VFPU prefix traps at run time, and zero dispatch misses on the boot path.

**Architecture:** Most of the work is in our psprecomp fork (`third_party/psprecomp`, branch `khbbs`). Discovery gets two new seed sources: `lui`/`addiu` (and `lui`/`ori`) address pairs in code, and a `--seeds FILE` list generated from a new `bbs.toml`. It also learns SNC's `$at` switch idiom. The emitter turns recovered switches into C `switch` statements and handles the VFPU branches. The decoder names every VFPU encoding the game uses, and the VFPU runtime gains operand prefixes plus the missing ops. This repo adds `bbs.toml`, `tools/bbs_seeds.py`, `tools/unimpl_census.py`, `tools/merge_misses.py`, a dispatch-miss log in `runtime/` and a guest-`break` stop.

**Tech Stack:** C11, CMake ≥ 3.16 + Ninja, AppleClang 16, psprecomp fork (`b925bd2` at the start), Python 3.11+ standard library only (`tomllib`), SNC's `pspas.exe` through `wibo-macos` as the reference assembler for encodings.

**Context an implementer needs:**
- Read `docs/architecture.md` (sections "How code reached through pointers is found", VFPU), `docs/phase0-report.md` §2 and `docs/bringup.md`.
- Game files are never committed. `game/` and `build/` are gitignored. Run `tools/check_no_assets.sh` before every commit.
- Work on branch `p3-recompiler-fidelity` in this repo. Fork changes go on branch `khbbs` of `github.com/ShayVD/psprecomp`, committed inside `third_party/psprecomp`. Build and test the fork alone with `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `runtime/` and the generated code stay platform-free; only `hosts/*` touches the OS.
- PPSSPP is an external oracle only. Never copy its code. The semantics below come from the published VFPU documentation and the encodings from SNC's assembler. Where a semantic is a best reading rather than verified, the code says so in a comment, and P2's oracle tooling checks it later.
- **Encoding reference.** SNC's own assembler produces the test words:
  ```bash
  printf '\t.text\n\tvrot.q C000, S010, [-s,c,0,0]\n' > build/t.s
  tools/toolchain/wibo-macos tools/toolchain/pspsnc_1.2.7503.0/pspas.exe -o build/t.o build/t.s
  ```
  It prints nothing on success. Read the word back from `.text` of `build/t.o` (a Python one-liner with `struct` works). SNC spells `vuc2i` as `vuc2ifs`, and its prefix syntax is `vpfxs -X, |Y|, 1, 1/2`.

**Baseline (measured 2026-10-18 at psprecomp `b925bd2`):** 24,853 functions; 87.4% of `.text` reached; 23,056 seeds; 417 `jr $reg` sites with 0 tables resolved. In `build/pc/gen/main_funcs_*.c`: `psp_unimplemented` sites `mtv` 217, `vrot` 177, `vfpu?` 121, `vcmov` 61, `break` 36, `mfv` 32, `vcrsp` 30, `round.w.s` 20, `floor.w.s` 14, `vsge` 6, `vslt` 6, `vf2in` 4, `ceil.w.s` 1; 47 branches emitted as `0 /* unhandled branch */` (`bvf` 32, `bvt` 10, `bvtl` 5). The 121 `vfpu?` are `vnop` 41, VFPU0 sub-op 7 (`vdiv`) 17, `vfad` 17, `vi2uc` 17, `vuc2i` 13, `vs2i` 5, `vrndf2` 3, `vocp` 3, `vi2s` 2, `vbfy1` 2 and `vrndf1` 1.

**Silent bugs found while planning, fixed here:** the decoder puts `vdiv` at VFPU0 sub-op 4 (SNC assembles `vdiv.s` as `0x63C02000`, sub-op 7); a pending `vpfx*` prefix makes the next VFPU op skip itself with only a warning (373 prefix words in `.text`); `vcmp` evaluates conditions 8–15 as false; `bvf`/`bvt` are never taken; `trunc.w.s` (862 sites) is a C cast, undefined for NaN and out-of-range values; `vf2i*`/`vi2f` drop their scale exponent (22 scaled sites); `mfv`/`mtv` ignore bit 7, which selects the control-register forms; the fork library is built without `-ffp-contract=off`. None of `jalr rd≠$ra`, a `jr` delay slot that writes its own target, `mfvc`/`mtvc` or `cvt.w.s` occurs in `.text`, so those need no work beyond correct decoding.

**Decisions taken in this plan:**
- `break` is the game's own trap (assert), not a translation gap. It is emitted as `psp_break(addr, code)` and stops `khbbs` cleanly with a "guest break" reason, so it is not a `psp_unimplemented` site.
- `bbs.toml` is parsed in Python (`tomllib`), not C. `tools/bbs_seeds.py` resolves it into a plain address list, so the fork only learns `--seeds FILE` and stays generic.
- A recognised switch keeps its case blocks inside the function (they are walked as blocks, not promoted to entries), and its `jr` becomes a C `switch`.
- The SNC switch matcher requires the `sltiu` bound and rejects the whole table if any entry inside the bound is not code in `.text`.
- VFPU random numbers (`vrnd*`) are a deterministic xorshift, not the PSP's generator, which is unknown. Determinism matters more than matching for now.

## File map

| File | Responsibility |
|---|---|
| `tools/unimpl_census.py` | count `psp_unimplemented`, unhandled branches and undiscovered calls in generated C; the P3 gate |
| `tests/tools/test_unimpl_census.py`, `test_bbs_seeds.py`, `test_merge_misses.py`, `tests/tools/elfgen.py` | Python unit tests, synthetic data only |
| `bbs.toml` | target pin, extra entries, pointer tables |
| `tools/bbs_seeds.py` | `bbs.toml` + ELF → seeds file |
| `tools/merge_misses.py` | miss log → `bbs.toml` `discovery.entries` |
| `cmake/gen.cmake` | generate seeds, pass `--seeds` |
| `runtime/misslog.c`, `runtime/include/khbbs/misslog.h` | JSON-lines dispatch-miss log |
| `runtime/boot.c`, `runtime/include/khbbs/boot.h` | boot options (budget, keep-going), miss count, guest-break stop |
| `runtime/unimplemented.c`, `runtime/include/khbbs/unimplemented.h` | `psp_break`, fatal VFPU trap handler |
| `hosts/pc/main.c` | `--miss-log`, `--keep-going` |
| `tests/runtime/test_misslog.c`, `tests/runtime/test_boot.c` | runtime tests |
| `third_party/psprecomp/tools/allegrexrecomp/analyze.[ch]` | (fork) address pairs, SNC switch tables, `a_jtable` |
| `third_party/psprecomp/tools/allegrexrecomp/seeds.[ch]` | (fork) seeds-file reader |
| `third_party/psprecomp/tools/allegrexrecomp/main.c` | (fork) wire seeds, `--seeds`, option parsing |
| `third_party/psprecomp/tools/allegrexrecomp/decode.[ch]` | (fork) VFPU decoding fixes, `vimm5` field |
| `third_party/psprecomp/tools/allegrexrecomp/emit.c` | (fork) switch emission, VFPU/FPU ops, `bvf`/`bvt`, `psp_break` |
| `third_party/psprecomp/include/psprecomp/vfpu.h`, `src/vfpu.c`, `src/vfpu_internal.h`, `src/vfpu_ops.c` | (fork) prefixes, new ops |
| `third_party/psprecomp/include/psprecomp/recomp_rt.h` | (fork) `psp_cvt_w` |
| `third_party/psprecomp/tests/test_analyze_pairs.c`, `test_seeds.c`, `test_switch.c`, `test_decode_vfpu.c`, `test_vfpu_prefix.c`, `test_vfpu_ops.c`, `test_emit_vfpu.c`, `test_fpu_cvt.c` | (fork) tests |
| `tools/setup_toolchain.sh`, `docs/setup.md` | the fork pin |
| `docs/bringup.md`, `docs/plan.md`, `docs/next-session.md` | results |

---

### Task 1: The census tool (the P3 gate)

**Files:**
- Create: `tools/unimpl_census.py`
- Create: `tests/tools/test_unimpl_census.py`
- Modify: `.github/workflows/checks.yml` (job `no-game-data`, step "Tool scripts parse")

- [ ] **Step 1: Write the failing test**

`tests/tools/test_unimpl_census.py`:

```python
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
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: FAIL (the tool does not exist; returncode 2 from Python, assertions fail).

- [ ] **Step 3: Write the tool**

`tools/unimpl_census.py`:

```python
#!/usr/bin/env python3
"""Count what the recompiler could not translate, in the generated C.

  tools/unimpl_census.py build/pc/gen            # summary; exit 1 if anything is left
  tools/unimpl_census.py build/pc/gen --sites    # also one line per site

Reads main_funcs_*.c only (the code of .text). Gated, so a non-zero count
fails the run:
  - psp_unimplemented(...) sites, by mnemonic; unnamed encodings by raw word
  - branches the emitter has no condition for ("0 /* unhandled branch */")
Reported, not gated:
  - static calls to code discovery never reached ("/* not discovered */")
"""
import argparse, collections, pathlib, re, sys

# The comment the emitter writes before every instruction: address, mnemonic, operands.
COMMENT = re.compile(r"/\* ([0-9A-F]{8})  (\S+)\s*(.*?) \*/")
UNIMPL = re.compile(r'psp_unimplemented\(0x([0-9A-F]{8})u, "([^"]*)"\)')
UNHANDLED = "0 /* unhandled branch */"
UNDISCOVERED = re.compile(r"psp_dispatch\(0x([0-9A-F]{8})u\);  /\* not discovered \*/")
KINDS = (("unimplemented", "unimplemented"), ("branch", "unhandled branches"),
         ("undiscovered", "undiscovered static calls"))


def scan(gen):
    """Yield (kind, addr, what) for every site in gen/main_funcs_*.c. The
    address is the instruction's, from the comment that precedes it."""
    for path in sorted(pathlib.Path(gen).glob("main_funcs_*.c")):
        last = ("????????", "?", "")
        for line in path.read_text(errors="replace").splitlines():
            c = COMMENT.search(line)
            if c:
                last = c.groups()
                continue
            u = UNIMPL.search(line)
            if u:
                what = u.group(2)
                if what == "vfpu?" and last[1] == ".word":
                    what = f"vfpu? {last[2]}"
                yield "unimplemented", u.group(1), what
            elif UNHANDLED in line:
                yield "branch", last[0], last[1]
            elif UNDISCOVERED.search(line):
                yield "undiscovered", last[0], "call"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gen", help="the generated-code directory, e.g. build/pc/gen")
    ap.add_argument("--sites", action="store_true", help="also list every site")
    a = ap.parse_args(argv)
    if not pathlib.Path(a.gen).is_dir():
        print(f"{a.gen}: not a directory", file=sys.stderr)
        return 2

    counts = {k: collections.Counter() for k, _ in KINDS}
    sites = []
    for kind, addr, what in scan(a.gen):
        counts[kind][what] += 1
        sites.append((addr, kind, what))

    for kind, title in KINDS:
        print(f"{title} {sum(counts[kind].values())}")
        for what, n in counts[kind].most_common():
            print(f"  {what} {n}")
    if a.sites:
        for addr, kind, what in sorted(sites):
            print(f"0x{addr} {kind} {what}")
    return 1 if sum(counts["unimplemented"].values()) + sum(counts["branch"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: `OK` (4 tests).

- [ ] **Step 5: Record the baseline on the real build**

Run: `python3 tools/unimpl_census.py build/pc/gen`
Expected: `unimplemented 735`, then `mtv 217` … `ceil.w.s 1` as in **Baseline** above, `unhandled branches 47`, exit status 1.

- [ ] **Step 6: Run the tests in CI**

In `.github/workflows/checks.yml`, replace the "Tool scripts parse" step's `run` with:

```yaml
        run: |
          python3 -m py_compile tools/import_report.py tools/unimpl_census.py
          bash -n tools/extract_game.sh tools/check_no_assets.sh
          python3 -m unittest discover -s tests/tools -v
```

- [ ] **Step 7: Commit**

```bash
tools/check_no_assets.sh
git add tools/unimpl_census.py tests/tools/test_unimpl_census.py .github/workflows/checks.yml
git commit -m "tools: unimpl_census.py counts untranslated sites in generated code"
```

---

### Task 2: Seed discovery from `lui`/`addiu` address pairs (fork)

The P1 boot stopped at `0x08A8BE6C`, crt0's main-thread entry. crt0 builds it as `lui $a1, 0x08A9; addiu $a1, $a1, -0x4194` at `0x08A8BE20` and hands it to `sceKernelCreateThread`. No `jal` and no data word names it. A scan of `.text` for such pairs finds 414 code addresses, 359 of them undiscovered today.

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/analyze.h` (declare `a_scan_address_pairs`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/analyze.c` (`gpr_written`, `a_scan_address_pairs`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/main.c` (`load_and_discover`, `cmd_funcs`)
- Create: `third_party/psprecomp/tests/test_analyze_pairs.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_analyze_pairs.c`:

```c
/* Address pairs: lui/addiu and lui/ori that build a code address become seeds.
 * Synthetic code only, no game data. */
#include "analyze.h"

#include <stdio.h>
#include <string.h>

#define BASE 0x08804000u

static uint32_t lui(int rt, uint32_t imm) { return 0x3C000000u | (uint32_t)rt << 16 | (imm & 0xFFFF); }
static uint32_t addiu(int rt, int rs, int imm) {
    return 0x24000000u | (uint32_t)rs << 21 | (uint32_t)rt << 16 | ((uint32_t)imm & 0xFFFF);
}
static uint32_t ori(int rt, int rs, uint32_t imm) {
    return 0x34000000u | (uint32_t)rs << 21 | (uint32_t)rt << 16 | (imm & 0xFFFF);
}
static uint32_t lw(int rt, int imm, int base) {
    return 0x8C000000u | (uint32_t)base << 21 | (uint32_t)rt << 16 | ((uint32_t)imm & 0xFFFF);
}
#define HI(a) (((a) + 0x8000u) >> 16)
#define LO(a) ((int)(int16_t)((a) & 0xFFFF))
#define JR_RA 0x03E00008u
#define A0 4
#define A1 5
#define T0 8

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); failures++; } } while (0)

int main(void) {
    /* f0 builds three addresses: one with addiu, one with ori (a different
     * register for the low half), and one whose lui is clobbered first. A
     * fourth pair points outside the scan range. f1 and f2 are reachable only
     * through those pointers. */
    const uint32_t F1 = BASE + 0x40, F2 = BASE + 0x48, DATA = BASE + 0x80;
    uint32_t w[0x90 / 4];
    memset(w, 0, sizeof w);
    int i = 0;
    w[i++] = lui(A0, HI(F1));            /* 0x00 */
    w[i++] = addiu(A0, A0, LO(F1));      /* 0x04 -> F1 */
    w[i++] = lui(T0, F2 >> 16);          /* 0x08 */
    w[i++] = ori(A1, T0, F2 & 0xFFFF);   /* 0x0C -> F2 (rt != rs) */
    w[i++] = lui(A0, 0x0880);            /* 0x10 */
    w[i++] = lw(A0, 0, A0);              /* 0x14 clobbers $a0: not a pair */
    w[i++] = addiu(A1, A0, 0x4040);      /* 0x18 would be BASE+0x40 if accepted */
    w[i++] = lui(A0, HI(DATA));          /* 0x1C */
    w[i++] = addiu(A0, A0, LO(DATA));    /* 0x20 -> DATA: outside the scan range */
    w[i++] = JR_RA;                      /* 0x24 */
    w[i++] = 0;                          /* 0x28 */
    w[0x40 / 4] = JR_RA; w[0x44 / 4] = 0;   /* f1 */
    w[0x48 / 4] = JR_RA; w[0x4C / 4] = 0;   /* f2 */

    uint8_t code[sizeof w];
    for (size_t k = 0; k < sizeof w / 4; k++)
        for (int b = 0; b < 4; b++) code[k * 4 + b] = (uint8_t)(w[k] >> (8 * b));

    a_analysis an;
    memset(&an, 0, sizeof an);
    an.code = code;
    an.base = BASE;
    an.size = (uint32_t)sizeof code;
    an.scan_base = BASE;
    an.scan_size = 0x50;                 /* ".text"; DATA lies beyond it */

    uint32_t found[8];
    int n = a_scan_address_pairs(&an, found, 8);
    CHECK(n == 2, "expected 2 pair targets, got %d", n);
    int has_f1 = 0, has_f2 = 0;
    for (int k = 0; k < n && k < 8; k++) {
        if (found[k] == F1) has_f1 = 1;
        if (found[k] == F2) has_f2 = 1;
    }
    CHECK(has_f1, "lui/addiu target 0x%08X found", F1);
    CHECK(has_f2, "lui/ori target 0x%08X found", F2);
    CHECK(a_scan_address_pairs(&an, NULL, 0) == 2, "counting pass agrees");

    /* Seeded with the pairs, discovery finds f1 and f2 as functions. */
    uint32_t seeds[3] = { BASE, F1, F2 };
    CHECK(a_discover(&an, seeds, 3) == 0, "discover");
    int got = 0;
    for (int k = 0; k < an.nfuncs; k++)
        if (an.funcs[k].addr == F1 || an.funcs[k].addr == F2) got++;
    CHECK(got == 2, "both pointer-only functions discovered (%d)", got);
    a_analysis_free(&an);

    printf("%s\n", failures ? "test_analyze_pairs: FAILED" : "test_analyze_pairs: ok");
    return failures ? 1 : 0;
}
```

Append to `third_party/psprecomp/tests/CMakeLists.txt`:

```cmake
add_executable(test_analyze_pairs test_analyze_pairs.c)
target_link_libraries(test_analyze_pairs PRIVATE allegrex_core)
add_test(NAME analyze_pairs COMMAND test_analyze_pairs)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: build FAILS with an implicit declaration / undefined symbol `a_scan_address_pairs`.

- [ ] **Step 3: Declare the scan**

In `analyze.h`, after the declaration of `a_scan_data_pointers`, add:

```c
/* Scan the code (the scan range, normally .text) for addresses built in
 * registers: `lui rX, hi` followed within a few instructions by
 * `addiu rY, rX, lo` or `ori rY, rX, lo`, with rX not rewritten in between.
 *
 * This is how a statically linked module passes a function pointer: crt0
 * hands its main-thread entry to sceKernelCreateThread this way, and so do
 * callback and handler registrations. No `jal` and no data word names those
 * functions, so neither the call harvest nor the data scan sees them.
 *
 * Like a_scan_data_pointers this is a heuristic. A target is kept only if it
 * lies inside the scan range, is word-aligned, is not an import stub and
 * decodes as a valid instruction. `out` receives candidates (duplicates
 * possible); returns the number found, which may exceed `max`. */
int a_scan_address_pairs(const a_analysis *an, uint32_t *out, int max);
```

- [ ] **Step 4: Implement it**

In `analyze.c`, after `fetch()`, add the register-write helper (Task 5 reuses it):

```c
/* The general-purpose register an instruction writes, or -1 if it writes
 * none. Used by the matchers below to ask "was this register set again in
 * between?", so it must not miss a writer; every GPR-writing op the decoder
 * names is listed. */
static int gpr_written(const a_insn *in) {
    switch (in->op) {
    case A_SLL: case A_SRL: case A_SRA: case A_SLLV: case A_SRLV: case A_SRAV:
    case A_ROTR: case A_ROTRV: case A_MOVZ: case A_MOVN: case A_MFHI: case A_MFLO:
    case A_CLZ: case A_CLO: case A_ADD: case A_ADDU: case A_SUB: case A_SUBU:
    case A_AND: case A_OR: case A_XOR: case A_NOR: case A_SLT: case A_SLTU:
    case A_MAX: case A_MIN: case A_JALR:
    case A_WSBH: case A_WSBW: case A_SEB: case A_SEH: case A_BITREV:
        return in->rd;
    case A_ADDI: case A_ADDIU: case A_SLTI: case A_SLTIU: case A_ANDI: case A_ORI:
    case A_XORI: case A_LUI: case A_EXT: case A_INS:
    case A_LB: case A_LH: case A_LWL: case A_LW: case A_LBU: case A_LHU: case A_LWR:
    case A_LL: case A_SC:
    case A_MFC0: case A_CFC0: case A_MFIC: case A_MFC1: case A_CFC1:
    case A_MFV: case A_MFVC:
        return in->rt;
    case A_JAL: case A_BLTZAL: case A_BGEZAL: case A_BLTZALL: case A_BGEZALL:
        return PSP_RA_INDEX;
    default:
        return -1;
    }
}
```

After `a_scan_data_pointers`, add:

```c
/* How far a low half may follow its lui. Compilers schedule the pair close
 * together; SNC's crt0 has it two instructions apart. */
#define PAIR_WINDOW 16

int a_scan_address_pairs(const a_analysis *an, uint32_t *out, int max) {
    const uint32_t sbase = an->scan_size ? an->scan_base : an->base;
    const uint32_t ssize = an->scan_size ? an->scan_size : an->size;
    int found = 0;

    for (uint32_t off = 0; off + 4 <= ssize; off += 4) {
        const uint32_t a = sbase + off;
        if (!a_in_range(an, a)) continue;
        a_insn hi;
        a_decode(fetch(an, a), a, &hi);
        if (hi.op != A_LUI || hi.rt == 0) continue;

        int stop_after = -1;          /* instructions left before control leaves */
        for (int i = 1; i <= PAIR_WINDOW && stop_after != 0; i++) {
            const uint32_t b = a + (uint32_t)i * 4;
            if (!a_in_range(an, b)) break;
            if (stop_after > 0) stop_after--;
            a_insn lo;
            a_decode(fetch(an, b), b, &lo);

            if ((lo.op == A_ADDIU || lo.op == A_ORI) && lo.rs == hi.rt) {
                /* addiu's immediate is sign-extended and ori's zero-extended;
                 * the decoder already did that, so plain addition is right. */
                const uint32_t t = ((uint32_t)hi.imm << 16) + (uint32_t)lo.imm;
                a_insn probe;
                if (t >= sbase && t - sbase < ssize && (t & 3) == 0 &&
                    !is_import_stub(an, t) && a_in_range(an, t) &&
                    a_decode(fetch(an, t), t, &probe) && probe.op != A_INVALID) {
                    if (found < max && out) out[found] = t;
                    found++;
                }
                /* One lui can feed several low halves (lui; addiu a0; addiu
                 * a1), so keep looking unless this one replaced the high half. */
                if (lo.rt == hi.rt) break;
                continue;
            }
            if (gpr_written(&lo) == hi.rt) break;            /* high half gone */
            /* An unconditional transfer ends the straight line after its
             * delay slot; a pair does not span into unrelated code. */
            if ((lo.is_jump && !lo.is_call) || lo.is_return ||
                (lo.is_indirect && !lo.is_call))
                stop_after = 1;
        }
    }
    return found;
}
```

- [ ] **Step 5: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: `100% tests passed` (the new `analyze_pairs` included).

- [ ] **Step 6: Collect seed statistics in a struct and add the pairs**

In `main.c`, above `load_and_discover`, add:

```c
/* Where discovery's seeds came from, for the `funcs` report. */
typedef struct {
    int nseeds;      /* total */
    int nexports;
    int nptr;        /* relocations, or scanned data pointers */
    int scanned;     /* nptr came from the heuristic data scan */
    int npair;       /* lui/addiu and lui/ori address pairs in code */
} seed_stats;
```

Change the signature to

```c
static int load_and_discover(const char *path, psp_blob *b, elf_info *e,
                             a_analysis *an, seed_stats *st) {
```

and replace each `if (out_scanned) *out_scanned = 1;` with `st->scanned = 1;`. Directly before `int rc = a_discover(an, seeds, nseeds);` insert:

```c
    /* Function pointers built in registers (see a_scan_address_pairs). Kept
     * only if they land in an executable section, like the data pointers. */
    int npair = a_scan_address_pairs(an, NULL, 0);
    if (npair > 0) {
        uint32_t *bigger = (uint32_t *)realloc(seeds, (size_t)(nseeds + npair) * sizeof *seeds);
        if (!bigger) { free(seeds); psp_blob_free(b); return -1; }
        seeds = bigger;
        npair = a_scan_address_pairs(an, seeds + nseeds, npair);
        int kept = 0;
        for (int i = 0; i < npair; i++)
            if (seed_in_code(e, seeds[nseeds + i])) seeds[nseeds + kept++] = seeds[nseeds + i];
        npair = kept;
        nseeds += npair;
    }
```

Replace the `if (out_nseeds) …` block at the end with:

```c
    st->nseeds = nseeds;
    st->nexports = nexports;
    st->nptr = nptr;
    st->npair = npair;
    return 0;
```

Start the function with `memset(st, 0, sizeof *st);`. In `cmd_funcs`, replace the four locals with `seed_stats st;`, call `load_and_discover(path, &b, &e, &an, &st)`, and change the seeds line to:

```c
    printf("seeds:      %d (entry 0x%08X + %d exports + %d %s + %d address pairs)\n",
           st.nseeds, e.entry, st.nexports, st.nptr,
           st.scanned ? "scanned data pointers (heuristic)" : "relocations", st.npair);
```

(the `relocations` label is the existing non-heuristic wording; keep the original if it differs). In `cmd_emit`, declare `seed_stats st;` and pass `&st`.

- [ ] **Step 7: Check the real game**

Run:
```bash
cmake --build third_party/psprecomp/build
third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp funcs game/EBOOT.dec.ELF | sed -n 3,6p
third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp funcs game/EBOOT.dec.ELF --list | grep -ci '^0x08A8BE6C'
```
Expected: the seeds line reports about 414 address pairs; `functions:` is above 24,853 and `reached:` above 87.4%; the grep prints `1`. Write the three numbers into your report.

- [ ] **Step 8: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/analyze.h tools/allegrexrecomp/analyze.c tools/allegrexrecomp/main.c \
        tests/test_analyze_pairs.c tests/CMakeLists.txt
git commit -m "analyze: seed discovery from lui/addiu and lui/ori address pairs in code"
```

---

### Task 3: `--seeds FILE` for `funcs` and `emit` (fork)

**Files:**
- Create: `third_party/psprecomp/tools/allegrexrecomp/seeds.h`, `seeds.c`
- Modify: `third_party/psprecomp/tools/allegrexrecomp/CMakeLists.txt` (add `seeds.c` to `allegrex_core`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/main.c`
- Create: `third_party/psprecomp/tests/test_seeds.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_seeds.c`:

```c
/* The seeds-file reader. Synthetic files only. */
#include "seeds.h"

#include <stdio.h>
#include <stdlib.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static const char *write_tmp(const char *name, const char *text) {
    FILE *f = fopen(name, "w");
    if (!f) return NULL;
    fputs(text, f);
    fclose(f);
    return name;
}

int main(void) {
    uint32_t *v = NULL;
    int n = -1;

    const char *ok = write_tmp("t_seeds_ok.txt",
        "# generated\n"
        "0x08A8BE6C\n"
        "\n"
        "   0x08804040   # with a comment\n"
        "0x0880A5DC\r\n");
    CHECK(a_read_seed_file(ok, &v, &n) == 0, "valid file parses");
    CHECK(n == 3, "three seeds");
    CHECK(v && v[0] == 0x08A8BE6Cu && v[1] == 0x08804040u && v[2] == 0x0880A5DCu, "values in order");
    free(v);

    v = NULL;
    const char *bad = write_tmp("t_seeds_bad.txt", "0x08804040\nnot-an-address\n");
    CHECK(a_read_seed_file(bad, &v, &n) != 0, "garbage line rejected");
    CHECK(v == NULL, "nothing returned on error");

    const char *neg = write_tmp("t_seeds_neg.txt", "-1\n");
    CHECK(a_read_seed_file(neg, &v, &n) != 0, "negative rejected");
    const char *big = write_tmp("t_seeds_big.txt", "0x100000000\n");
    CHECK(a_read_seed_file(big, &v, &n) != 0, "over 32 bits rejected");
    const char *trail = write_tmp("t_seeds_trail.txt", "0x08804040 0x08804044\n");
    CHECK(a_read_seed_file(trail, &v, &n) != 0, "two values on a line rejected");

    const char *empty = write_tmp("t_seeds_empty.txt", "# nothing\n\n");
    CHECK(a_read_seed_file(empty, &v, &n) == 0 && n == 0, "empty file is fine");
    free(v);

    CHECK(a_read_seed_file("t_seeds_does_not_exist.txt", &v, &n) != 0, "missing file is an error");

    printf("%s\n", failures ? "test_seeds: FAILED" : "test_seeds: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_seeds test_seeds.c)
target_link_libraries(test_seeds PRIVATE allegrex_core)
add_test(NAME seeds COMMAND test_seeds)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL, `seeds.h` not found.

- [ ] **Step 3: Write the reader**

`tools/allegrexrecomp/seeds.h`:

```c
/* Extra discovery seeds from a file: one address per line ("0x08A8BE6C"),
 * '#' to end of line is a comment, blank lines are ignored. khbbs generates
 * this from its bbs.toml; any project can write one by hand. */
#ifndef ALLEGREX_SEEDS_H
#define ALLEGREX_SEEDS_H

#include <stdint.h>

/* On success returns 0 and hands back a malloc'd array (free it) and its
 * length. On a missing file or a malformed line, prints the reason with the
 * line number, sets *out to NULL and returns -1. */
int a_read_seed_file(const char *path, uint32_t **out, int *n);

#endif
```

`tools/allegrexrecomp/seeds.c`:

```c
/* See seeds.h. */
#include "seeds.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int a_read_seed_file(const char *path, uint32_t **out, int *n) {
    *out = NULL;
    *n = 0;
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot read seeds file %s\n", path); return -1; }

    uint32_t *v = NULL;
    int cnt = 0, cap = 0, lineno = 0, rc = 0;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        lineno++;
        char *hash = strchr(line, '#');
        if (hash) *hash = '\0';
        char *p = line;
        while (isspace((unsigned char)*p)) p++;
        if (*p == '\0') continue;

        char *end = NULL;
        errno = 0;
        unsigned long long val = isdigit((unsigned char)*p) ? strtoull(p, &end, 0) : 0;
        if (end) while (isspace((unsigned char)*end)) end++;
        if (!end || end == p || *end != '\0' || errno || val > 0xFFFFFFFFull) {
            fprintf(stderr, "%s:%d: not an address\n", path, lineno);
            rc = -1;
            break;
        }
        if (cnt == cap) {
            int ncap = cap ? cap * 2 : 64;
            uint32_t *nv = (uint32_t *)realloc(v, (size_t)ncap * sizeof *nv);
            if (!nv) { fprintf(stderr, "%s: out of memory\n", path); rc = -1; break; }
            v = nv;
            cap = ncap;
        }
        v[cnt++] = (uint32_t)val;
    }
    fclose(f);
    if (rc) { free(v); return -1; }
    *out = v;
    *n = cnt;
    return 0;
}
```

Add `seeds.c` to the `add_library(allegrex_core STATIC …)` list in `tools/allegrexrecomp/CMakeLists.txt`.

- [ ] **Step 4: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: all pass, including `seeds`.

- [ ] **Step 5: Wire `--seeds` into discovery**

In `main.c`: `#include "seeds.h"`. Add `int nfile;` (seeds taken from the file) and `int nfile_ignored;` to `seed_stats`. Change `load_and_discover` to take `const char *seeds_path` after `path`. Directly after the address-pair block from Task 2 add:

```c
    /* Seeds from a file (bbs.toml in khbbs): entries reached through
     * pointers no scan can see. A seed outside the executable sections is
     * almost certainly a typo, so it is reported and skipped. */
    if (seeds_path) {
        uint32_t *fs = NULL;
        int nfs = 0;
        if (a_read_seed_file(seeds_path, &fs, &nfs) != 0) { free(seeds); psp_blob_free(b); return -1; }
        uint32_t *bigger = (uint32_t *)realloc(seeds, (size_t)(nseeds + nfs + 1) * sizeof *seeds);
        if (!bigger) { free(fs); free(seeds); psp_blob_free(b); return -1; }
        seeds = bigger;
        for (int i = 0; i < nfs; i++) {
            if (seed_in_code(e, fs[i]) && (fs[i] & 3) == 0) { seeds[nseeds++] = fs[i]; st->nfile++; }
            else {
                fprintf(stderr, "%s: 0x%08X is not in an executable section, ignored\n", seeds_path, fs[i]);
                st->nfile_ignored++;
            }
        }
        free(fs);
    }
```

Add `+ %d from file` to the seeds line in `cmd_funcs` (argument `st.nfile`) and, if `st.nfile_ignored`, print `seeds file: %d ignored` on its own line. Give `cmd_funcs` and `cmd_emit` a `const char *seeds_path` parameter and pass it through.

- [ ] **Step 6: Parse options in one place**

Replace the `funcs` and `emit` branches of `main()` with:

```c
    if (!strcmp(cmd, "funcs")) {
        int list = 0;
        const char *seeds = NULL;
        int first = parse_opts(argc, argv, 3, NULL, &seeds, &list);
        if (first < 0) return 2;
        if (first != 3) return usage();          /* exactly one positional */
        return cmd_funcs(argv[2], list, seeds);
    }
    if (!strcmp(cmd, "emit")) {
        if (argc < 4) return usage();
        int split = 0;
        const char *seeds = NULL;
        int first = parse_opts(argc, argv, 4, &split, &seeds, NULL);
        if (first < 0) return 2;
        if (first > 5) return usage();           /* file, outdir, optional prefix */
        return cmd_emit(argv[2], argv[3], first > 4 ? argv[4] : NULL, split, seeds);
    }
```

and add above `main()`:

```c
/* Options after the positional arguments of `funcs` and `emit`, in any
 * order. A NULL out-pointer means the command does not take that option.
 * Returns the index of the first option (argc if none), so the caller can
 * check how many positionals it got, or -1 after printing what is wrong. */
static int parse_opts(int argc, char **argv, int from,
                      int *split, const char **seeds, int *list) {
    int first = argc;
    for (int i = from; i < argc; i++) {
        if (argv[i][0] != '-') {
            if (first < argc) { fprintf(stderr, "unexpected argument %s\n", argv[i]); return -1; }
            continue;
        }
        if (first == argc) first = i;
        if (list && !strcmp(argv[i], "--list")) { *list = 1; continue; }
        if (seeds && !strcmp(argv[i], "--seeds") && i + 1 < argc) { *seeds = argv[++i]; continue; }
        if (split && !strcmp(argv[i], "--split") && i + 1 < argc) {
            char *end = NULL;
            long v = strtol(argv[i + 1], &end, 10);
            if (!*argv[i + 1] || *end || v < 1 || v > 999) {
                fprintf(stderr, "--split must be between 1 and 999\n");
                return -1;
            }
            *split = (int)v;
            i++;
            continue;
        }
        fprintf(stderr, "unknown or incomplete option %s\n", argv[i]);
        return -1;
    }
    return first;
}
```

Update the usage text: `funcs <file> [--list] [--seeds FILE]` and `emit <file> <outdir> [prefix] [--split N] [--seeds FILE]`.

- [ ] **Step 7: Check the CLI by hand**

Run:
```bash
cmake --build third_party/psprecomp/build
R=third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp
printf '0x08A8BE6C\n0x08B31528\n' > build/t_seeds.txt
$R funcs game/EBOOT.dec.ELF --seeds build/t_seeds.txt | sed -n 3p
$R emit game/EBOOT.dec.ELF build/t_gen main --split 0; echo "rc=$?"
$R funcs game/EBOOT.dec.ELF --bogus; echo "rc=$?"
```
Expected: the seeds line shows `+ 1 from file`, stderr says `0x08B31528 is not in an executable section, ignored`; `--split 0` prints the range error with `rc=2`; `--bogus` prints `unknown or incomplete option --bogus` with `rc=2`. Delete `build/t_seeds.txt` and `build/t_gen`.

- [ ] **Step 8: Run all fork tests and commit**

```bash
cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure
cd third_party/psprecomp
git add tools/allegrexrecomp/seeds.h tools/allegrexrecomp/seeds.c tools/allegrexrecomp/CMakeLists.txt \
        tools/allegrexrecomp/main.c tests/test_seeds.c tests/CMakeLists.txt
git commit -m "funcs/emit: --seeds FILE adds extra discovery entries"
```

---

### Task 4: `bbs.toml` and the seeds it generates

**Files:**
- Create: `bbs.toml`
- Create: `tools/bbs_seeds.py`
- Create: `tests/tools/elfgen.py`, `tests/tools/test_bbs_seeds.py`
- Modify: `cmake/gen.cmake`
- Modify: `.github/workflows/checks.yml` (py_compile list)

- [ ] **Step 1: Write a synthetic-ELF helper for the tests**

`tests/tools/elfgen.py`:

```python
"""Build tiny little-endian ELF32 files for tool tests. No game data."""
import struct

SHT_PROGBITS, SHT_STRTAB, SHT_NOBITS = 1, 3, 8
SHF_ALLOC, SHF_EXECINSTR = 0x2, 0x4


def make_elf(sections):
    """sections: list of (name, addr, flags, data bytes). Returns ELF bytes
    with those sections, a null section first and .shstrtab last."""
    names = b"\0" + b"".join(n.encode() + b"\0" for n, *_ in sections) + b".shstrtab\0"
    body = b""
    offs = []
    base = 0x34
    for _, _, _, data in sections:
        offs.append(base + len(body))
        body += data + b"\0" * (-len(data) % 4)
    strtab_off = base + len(body)
    body += names + b"\0" * (-len(names) % 4)
    shoff = base + len(body)
    shdrs = [b"\0" * 40]
    name_off = 1
    for (name, addr, flags, data), off in zip(sections, offs):
        shdrs.append(struct.pack("<10I", name_off, SHT_PROGBITS, flags, addr, off, len(data), 0, 0, 4, 0))
        name_off += len(name) + 1
    shdrs.append(struct.pack("<10I", name_off, SHT_STRTAB, 0, 0, strtab_off, len(names), 0, 0, 1, 0))
    ehdr = (b"\x7fELF\x01\x01\x01" + b"\0" * 9 +
            struct.pack("<HHIIIIIHHHHHH", 2, 8, 1, 0, 0, shoff, 0, 0x34, 0, 0, 40,
                        len(shdrs), len(shdrs) - 1))
    return ehdr + body + b"".join(shdrs)


def words(*ws):
    return struct.pack("<%dI" % len(ws), *ws)
```

- [ ] **Step 2: Write the failing test**

`tests/tools/test_bbs_seeds.py`:

```python
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
```

- [ ] **Step 3: Run it and watch it fail**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: the five `BbsSeeds` tests FAIL (no tool).

- [ ] **Step 4: Write the tool**

`tools/bbs_seeds.py`:

```python
#!/usr/bin/env python3
"""Write the recompiler's seeds file from bbs.toml.

  tools/bbs_seeds.py bbs.toml game/EBOOT.dec.ELF -o build/pc/gen/seeds.txt

Checks the ELF against [target].eboot_sha1, then writes one address per
line: discovery.entries first, then every non-zero word of each
discovery.tables entry. A table is a whole ELF section (`section`) or a
range (`addr` + `count`), read every `stride` bytes (default 4).

Every address must lie in an executable section. bbs.toml is maintained by
hand and by tools/merge_misses.py, and a typo there must fail the build
rather than seed data as code.
"""
import argparse, hashlib, struct, sys, tomllib

SHF_EXECINSTR = 0x4
SHT_NOBITS = 8


def sections(elf):
    """The ELF's section headers as dicts: name, type, flags, addr, offset, size."""
    if elf[:4] != b"\x7fELF" or elf[4] != 1 or elf[5] != 1:
        raise ValueError("not a little-endian ELF32 file")
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x2E)
    raw = [struct.unpack_from("<10I", elf, shoff + i * shentsize) for i in range(shnum)]
    stroff = raw[shstrndx][4]
    out = []
    for name, typ, flags, addr, off, size, *_ in raw:
        end = elf.index(b"\0", stroff + name)
        out.append({"name": elf[stroff + name:end].decode(), "type": typ, "flags": flags,
                    "addr": addr, "offset": off, "size": size})
    return out


def exec_ranges(secs):
    return [(s["addr"], s["addr"] + s["size"]) for s in secs
            if s["flags"] & SHF_EXECINSTR and s["size"]]


def in_code(addr, ranges):
    return addr % 4 == 0 and any(lo <= addr < hi for lo, hi in ranges)


def read_word(elf, secs, addr):
    for s in secs:
        if s["type"] != SHT_NOBITS and s["addr"] <= addr and addr + 4 <= s["addr"] + s["size"]:
            return struct.unpack_from("<I", elf, s["offset"] + addr - s["addr"])[0]
    raise ValueError(f"0x{addr:08X} is not in any section with file data")


def table_words(elf, secs, t):
    stride = int(t.get("stride", 4))
    if "section" in t:
        match = [s for s in secs if s["name"] == t["section"]]
        if not match:
            raise ValueError(f"discovery.tables: no section {t['section']}")
        start, count = match[0]["addr"], match[0]["size"] // stride
    else:
        start, count = int(t["addr"]), int(t["count"])
    return [read_word(elf, secs, start + i * stride) for i in range(count)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("toml")
    ap.add_argument("elf")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    with open(a.toml, "rb") as f:
        cfg = tomllib.load(f)
    elf = open(a.elf, "rb").read()
    want = cfg["target"]["eboot_sha1"].lower()
    got = hashlib.sha1(elf).hexdigest()
    if got != want:
        print(f"{a.elf}: SHA-1 {got} does not match bbs.toml's {want}", file=sys.stderr)
        return 1

    try:
        secs = sections(elf)
        code = exec_ranges(secs)
        disc = cfg.get("discovery", {})
        out = []
        for e in disc.get("entries", []):
            if not in_code(int(e), code):
                raise ValueError(f"discovery.entries: 0x{int(e):08X} is not in an executable section")
            out.append(int(e))
        for t in disc.get("tables", []):
            for w in table_words(elf, secs, t):
                if w == 0:
                    continue
                if not in_code(w, code):
                    where = t.get("section") or f"0x{int(t['addr']):08X}"
                    raise ValueError(f"discovery.tables {where}: 0x{w:08X} is not in an executable section")
                out.append(w)
    except (ValueError, KeyError) as ex:
        print(f"{a.toml}: {ex}", file=sys.stderr)
        return 1

    with open(a.output, "w") as f:
        f.write("# generated from bbs.toml by tools/bbs_seeds.py; do not edit\n")
        f.writelines(f"0x{x:08X}\n" for x in out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: `OK` (9 tests).

- [ ] **Step 6: Write `bbs.toml`**

```toml
# KH Birth by Sleep, ULUS10505 v1.00. Every address in this file is a v1.00
# address. tools/bbs_seeds.py turns [discovery] into the recompiler's seeds
# file at build time; tools/merge_misses.py adds to discovery.entries.

[target]
# SHA-1 of game/EBOOT.dec.ELF, as in tools/target.sha1.
eboot_sha1 = "fd68f8dcd544fc2ae20028dedf59a67a6f012d38"

[discovery]
# Function entries that only a pointer reaches, and that neither the jal
# harvest nor the address-pair and data-pointer scans find. One per line,
# with where the pointer comes from.
entries = [
    # BEGIN merge_misses: tools/merge_misses.py adds entries here
    # END merge_misses
]

# Tables of code pointers: every non-zero word, `stride` bytes apart, is a
# function entry. A table is a whole section, or `addr` + `count`.

[[discovery.tables]]
# 100 static constructors as (function, 0) pairs, run by crt0. .ctors is
# empty in this game. The data-pointer scan already finds 99 of them (the
# 100th slot is null); listing the table makes that independent of the scan.
section = ".cplinit"
stride = 8
```

- [ ] **Step 7: Generate the seeds in the build**

In `cmake/gen.cmake`, after `set(KHBBS_GEN_DIR …)`, add:

```cmake
find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)   # tomllib
set(KHBBS_TOML "${PROJECT_SOURCE_DIR}/bbs.toml")
set(KHBBS_SEEDS "${KHBBS_GEN_DIR}/seeds.txt")
add_custom_command(
    OUTPUT "${KHBBS_SEEDS}"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${KHBBS_GEN_DIR}"
    COMMAND Python3::Interpreter "${PROJECT_SOURCE_DIR}/tools/bbs_seeds.py"
            "${KHBBS_TOML}" "${KHBBS_ELF}" -o "${KHBBS_SEEDS}"
    DEPENDS "${PROJECT_SOURCE_DIR}/tools/bbs_seeds.py" "${KHBBS_TOML}" "${KHBBS_ELF}"
    COMMENT "Seeds from bbs.toml"
    VERBATIM)
```

In the existing emit `add_custom_command`, append `--seeds "${KHBBS_SEEDS}"` to the `allegrexrecomp emit` command line and add `"${KHBBS_SEEDS}"` to its `DEPENDS`.

Check it. `build/pc` builds `third_party/psprecomp` from its working tree, so the local fork commits from Task 3 (`--seeds`) are already in use; the pin only matters for fresh checkouts and CI (Task 16).

Run: `cmake --build build/pc && grep -c '^0x' build/pc/gen/seeds.txt`
Expected: `Seeds from bbs.toml`, then the recompile and a successful link; the count is `99`.

- [ ] **Step 8: Add the tool to CI's parse check and commit**

Add `tools/bbs_seeds.py` to the `py_compile` line in `.github/workflows/checks.yml`.

```bash
tools/check_no_assets.sh
git add bbs.toml tools/bbs_seeds.py tests/tools/elfgen.py tests/tools/test_bbs_seeds.py cmake/gen.cmake .github/workflows/checks.yml
git commit -m "bbs.toml: discovery seeds (entries, .cplinit) fed to the recompiler"
```

---

### Task 5: Recognise SNC's `$at` switch tables in discovery (fork)

Every switch in this game is:

```
sltiu  $a2, $a0, 12            ; bound
beq    $a2, $zero, default
sll    $a0, $a0, 2
lui    $at, 0x08B3
addu   $at, $at, $a0
lw     $at, 0x1528($at)        ; the table's low half is the lw offset
jr     $at
```

The fork's matcher wants `lui`+`addiu` for the table base, so it resolves none of the 417. This task resolves the table while walking (so the case blocks become blocks of the same function rather than new functions) and records each table for the emitter (Task 6).

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/analyze.h` (`a_jtable`, new fields, `a_find_jtable`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/analyze.c` (`resolve_jump_table`, `trace_function`, `a_discover`, `a_analysis_free`)
- Create: `third_party/psprecomp/tests/test_switch.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_switch.c`:

```c
/* SNC switch tables: recovered during discovery, case blocks kept inside the
 * function. Synthetic code only, no game data. */
#include "analyze.h"
#include "emit.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BASE  0x08804000u
#define TABLE (BASE + 0x100u)        /* "rodata": in the image, past .text */

static uint32_t R(int op, int rs, int rt, int imm) {
    return (uint32_t)op << 26 | (uint32_t)rs << 21 | (uint32_t)rt << 16 | ((uint32_t)imm & 0xFFFF);
}
static uint32_t sltiu(int rt, int rs, int imm) { return R(0x0B, rs, rt, imm); }
static uint32_t beq(int rs, int rt, uint32_t pc, uint32_t target) {
    return R(0x04, rs, rt, (int)((target - (pc + 4)) >> 2));
}
static uint32_t sll(int rd, int rt, int sa) { return (uint32_t)rt << 16 | (uint32_t)rd << 11 | (uint32_t)sa << 6; }
static uint32_t lui(int rt, uint32_t imm) { return R(0x0F, 0, rt, (int)imm); }
static uint32_t addu(int rd, int rs, int rt) { return (uint32_t)rs << 21 | (uint32_t)rt << 16 | (uint32_t)rd << 11 | 0x21; }
static uint32_t lw(int rt, int imm, int base) { return R(0x23, base, rt, imm); }
static uint32_t jr(int rs) { return (uint32_t)rs << 21 | 0x08; }
static uint32_t ori(int rt, int rs, int imm) { return R(0x0D, rs, rt, imm); }
#define JR_RA 0x03E00008u
#define AT 1
#define V0 2
#define A0 4
#define A2 6

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL: " __VA_ARGS__); printf("\n"); failures++; } } while (0)

static uint8_t g_code[0x110];

static void build(uint32_t case2_target) {
    uint32_t w[0x110 / 4];
    memset(w, 0, sizeof w);
    w[0x00 / 4] = sltiu(A2, A0, 3);
    w[0x04 / 4] = beq(A2, 0, BASE + 0x04, BASE + 0x34);
    w[0x08 / 4] = sll(A0, A0, 2);
    w[0x0C / 4] = lui(AT, (TABLE + 0x8000u) >> 16);
    w[0x10 / 4] = addu(AT, AT, A0);
    w[0x14 / 4] = lw(AT, (int)(int16_t)(TABLE & 0xFFFF), AT);
    w[0x18 / 4] = jr(AT);
    w[0x1C / 4] = 0;
    w[0x20 / 4] = ori(V0, 0, 10); w[0x24 / 4] = JR_RA; w[0x28 / 4] = 0;   /* case 0 */
    w[0x2C / 4] = JR_RA;          w[0x30 / 4] = ori(V0, 0, 11);           /* case 1 */
    w[0x34 / 4] = JR_RA;          w[0x38 / 4] = ori(V0, 0, 0);            /* default */
    w[0x3C / 4] = JR_RA;          w[0x40 / 4] = ori(V0, 0, 12);           /* case 2 */
    w[0x100 / 4] = BASE + 0x20;
    w[0x104 / 4] = BASE + 0x2C;
    w[0x108 / 4] = case2_target;
    for (size_t k = 0; k < sizeof w / 4; k++)
        for (int b = 0; b < 4; b++) g_code[k * 4 + b] = (uint8_t)(w[k] >> (8 * b));
}

static void setup(a_analysis *an) {
    memset(an, 0, sizeof *an);
    an->code = g_code;
    an->base = BASE;
    an->size = sizeof g_code;
    an->scan_base = BASE;
    an->scan_size = 0x44;             /* .text */
    an->image = g_code;
    an->image_base = BASE;
    an->image_size = sizeof g_code;
}

int main(void) {
    a_analysis an;
    uint32_t seed = BASE;

    build(BASE + 0x3C);
    setup(&an);
    CHECK(a_discover(&an, &seed, 1) == 0, "discover");
    CHECK(an.nfuncs == 1, "cases stay inside the function, got %d functions", an.nfuncs);
    CHECK(an.njtables == 1, "one table, got %d", an.njtables);
    CHECK(an.nindirects == 0, "no unresolved computed jump, got %d", an.nindirects);
    const a_jtable *jt = a_find_jtable(&an, BASE + 0x18);
    CHECK(jt != NULL, "table found at the jr");
    if (jt) {
        CHECK(jt->table == TABLE, "table address 0x%08X", jt->table);
        CHECK(jt->count == 3, "three entries, got %u", jt->count);
        CHECK(an.jt_targets[jt->first] == BASE + 0x20 &&
              an.jt_targets[jt->first + 1] == BASE + 0x2C &&
              an.jt_targets[jt->first + 2] == BASE + 0x3C, "targets in table order");
    }
    CHECK(an.owner[(0x3C) / 4] == BASE, "case 2 owned by the switch's function");
    CHECK(an.nfuncs == 1 && !an.funcs[0].has_indirect, "function not marked indirect");
    CHECK(a_find_jtable(&an, BASE + 0x14) == NULL, "no table at other addresses");
    a_analysis_free(&an);

    /* An entry inside the bound that is not code in .text rejects the whole
     * table: the jr stays a computed jump. */
    build(TABLE);                       /* points into "rodata" */
    setup(&an);
    CHECK(a_discover(&an, &seed, 1) == 0, "discover (bad table)");
    CHECK(an.njtables == 0, "bad table rejected, got %d", an.njtables);
    CHECK(an.nindirects == 1, "jr left as a computed jump");
    a_analysis_free(&an);

    printf("%s\n", failures ? "test_switch: FAILED" : "test_switch: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_switch test_switch.c)
target_link_libraries(test_switch PRIVATE allegrex_core)
add_test(NAME switch COMMAND test_switch)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile: `a_jtable`, `njtables`, `a_find_jtable` unknown.

- [ ] **Step 3: Declare the table record**

In `analyze.h`, before the `a_analysis` typedef:

```c
/* A switch table recovered at a `jr $rN` site. Its targets, in table order
 * (duplicates kept), are an->jt_targets[first .. first + count). */
typedef struct {
    uint32_t site;      /* the jr */
    uint32_t table;     /* the table, in .rodata */
    uint32_t first;
    uint32_t count;     /* the bound from the sltiu */
} a_jtable;
```

In `a_analysis`, after `ntable_targets`, add:

```c
    a_jtable *jtables;          /* sorted by site */
    int       njtables;
    uint32_t *jt_targets;
    int       njt_targets;
```

After `a_in_range`'s declaration:

```c
/* The table recovered for the jr at `site`, or NULL. */
const a_jtable *a_find_jtable(const a_analysis *an, uint32_t site);
```

- [ ] **Step 4: Rewrite the matcher**

In `analyze.c`, replace `resolve_jump_table` (and its comment block's description of the idiom) with:

```c
/* The nearest instruction before `from` that writes `reg`, within
 * JT_WINDOW. Returns its address and decodes it into *out, or returns 0. */
static uint32_t last_writer(const a_analysis *an, uint32_t from, int reg, a_insn *out) {
    for (int i = 1; i <= JT_WINDOW; i++) {
        uint32_t a = from - (uint32_t)i * 4;
        if (!a_in_range(an, a)) return 0;
        a_decode(fetch(an, a), a, out);
        if (gpr_written(out) == reg) return a;
    }
    return 0;
}

/* The constant `reg` holds just before `from`, if it was built by `lui`
 * alone or by `lui` + `addiu`/`ori`. */
static int const_before(const a_analysis *an, uint32_t from, int reg, uint32_t *val) {
    a_insn w;
    uint32_t a = last_writer(an, from, reg, &w);
    if (!a) return 0;
    if (w.op == A_LUI) { *val = (uint32_t)w.imm << 16; return 1; }
    if (w.op == A_ADDIU || w.op == A_ORI) {
        a_insn h;
        if (!last_writer(an, a, w.rs, &h) || h.op != A_LUI) return 0;
        *val = ((uint32_t)h.imm << 16) + (uint32_t)w.imm;
        return 1;
    }
    return 0;
}

static int in_scan(const a_analysis *an, uint32_t addr) {
    const uint32_t sbase = an->scan_size ? an->scan_base : an->base;
    const uint32_t ssize = an->scan_size ? an->scan_size : an->size;
    return addr >= sbase && addr - sbase < ssize && (addr & 3) == 0 && a_in_range(an, addr);
}

/* Recover the table behind the `jr` at `site`. On success appends its
 * entries to `out`, sets *table_out and returns the count; otherwise
 * returns 0 and leaves `out` as it was.
 *
 *   lw   $rN, lo($base)     the jr's register, loaded from the table
 *   addu $base, $x, $y      one of $x/$y is the table's high half (lui),
 *                           or lui+addiu; the other is the scaled index
 *   sltiu $t, $key, N       the bound, required
 *
 * SNC writes the table's low half as the lw offset (`lui $at; addu $at,
 * $at, $idx; lw $at, lo($at)`); other compilers fold it into an addiu and
 * use offset 0. Both are covered because the lw offset is always added.
 * Every entry inside the bound must be an instruction in the scan range
 * (.text), or the whole table is rejected. */
static int resolve_jump_table(a_analysis *an, uint32_t site, u32list *out, uint32_t *table_out) {
    if (!an->image) return 0;
    a_insn jr;
    a_decode(fetch(an, site), site, &jr);

    a_insn lw;
    const uint32_t lw_addr = last_writer(an, site, jr.rs, &lw);
    if (!lw_addr || lw.op != A_LW) return 0;

    a_insn add;
    const uint32_t add_addr = last_writer(an, lw_addr, lw.rs, &add);
    if (!add_addr || add.op != A_ADDU) return 0;
    uint32_t base;
    if (!const_before(an, add_addr, add.rs, &base) &&
        !const_before(an, add_addr, add.rt, &base))
        return 0;
    const uint32_t table = base + (uint32_t)lw.imm;

    uint32_t limit = 0;
    for (int i = 1; i <= JT_WINDOW * 2; i++) {
        uint32_t a = site - (uint32_t)i * 4;
        if (!a_in_range(an, a)) break;
        a_insn in;
        a_decode(fetch(an, a), a, &in);
        if (in.op == A_SLTIU && in.imm > 0 && (uint32_t)in.imm <= JT_MAX_ENTRIES) {
            limit = (uint32_t)in.imm;
            break;
        }
    }
    if (limit < 2) return 0;

    const int start = out->n;
    for (uint32_t i = 0; i < limit; i++) {
        uint32_t entry = image_read32(an, table + i * 4);
        a_insn probe;
        if (!in_scan(an, entry) || !a_decode(fetch(an, entry), entry, &probe) ||
            probe.op == A_INVALID) {
            out->n = start;
            return 0;
        }
        u32_push(out, entry);
    }
    *table_out = table;
    return (int)limit;
}
```

Note `image_read32` returns 0 for an address outside the image; 0 is not in the scan range, so a table running off the image is rejected.

- [ ] **Step 5: Resolve while walking**

Add to `walk_ctx`:

```c
    /* Switch tables recovered so far, and their targets. */
    struct jtlist *jtables;
    u32list       *jt_targets;
```

and above `walk_ctx`:

```c
typedef struct jtlist { a_jtable *v; int n, cap; } jtlist;

static int jt_push(jtlist *l, a_jtable t) {
    if (l->n == l->cap) {
        int cap = l->cap ? l->cap * 2 : 64;
        a_jtable *v = (a_jtable *)realloc(l->v, (size_t)cap * sizeof *v);
        if (!v) return -1;
        l->v = v;
        l->cap = cap;
    }
    l->v[l->n++] = t;
    return 0;
}
```

In `trace_function`, replace the `if (in.is_indirect) { … }` block with:

```c
            if (in.is_indirect) {
                /* `jr $rN`. A recovered switch table turns it into ordinary
                 * control flow: its targets are blocks of this function,
                 * claimed exactly like conditional-branch targets. */
                uint32_t table = 0;
                const int first = c->jt_targets->n;
                const int n = resolve_jump_table(an, a, c->jt_targets, &table);
                if (n > 0) {
                    a_jtable jt = { a, table, (uint32_t)first, (uint32_t)n };
                    jt_push(c->jtables, jt);
                    for (int k = first; k < first + n; k++) {
                        const uint32_t t = c->jt_targets->v[k];
                        const uint32_t ti = word_index(an, t);
                        if ((c->seen[ti] & SEEN_CODE) && !c->entry_map[ti]) u32_push(c->cross, t);
                        else u32_push(&blocks, t);
                    }
                    break;
                }
                /* Unresolved: record the site and stop, as before. */
                has_indirect = 1;
                if (!u32_contains(c->indirects, a)) u32_push(c->indirects, a);
                break;
            }
```

`resolve_jump_table` needs `an` non-const: `trace_function` already has `a_analysis *an`.

- [ ] **Step 6: Rounds, results and cleanup**

In `a_discover`:
- declare `jtlist jtables = { 0 }; u32list jt_targets = { 0 };` next to the other lists and add `&jtables, &jt_targets` to the `walk_ctx` initialiser, in field order.
- In the `round > 0` block, delete the loop that calls `resolve_jump_table` over `indirects` (tables are now resolved in the walk) and change the early exit to `if (!cross.n) break;`. Keep `indirects.n = 0;`.
- In the re-walk reset (next to `nfuncs = 0;`) add `jtables.n = 0; jt_targets.n = 0;`.
- After the rounds loop, before `qsort(funcs …)`:

```c
    /* Sorted by site for a_find_jtable. */
    qsort(jtables.v, (size_t)jtables.n, sizeof *jtables.v, cmp_jtable);
    an->jtables = jtables.v;
    an->njtables = jtables.n;
    an->jt_targets = jt_targets.v;
    an->njt_targets = jt_targets.n;
    an->ntables = jtables.n;
    an->ntable_targets = jt_targets.n;
```

with, next to `cmp_func`:

```c
static int cmp_jtable(const void *a, const void *b) {
    uint32_t x = ((const a_jtable *)a)->site, y = ((const a_jtable *)b)->site;
    return (x > y) - (x < y);
}

const a_jtable *a_find_jtable(const a_analysis *an, uint32_t site) {
    int lo = 0, hi = an->njtables - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (an->jtables[mid].site == site) return &an->jtables[mid];
        if (an->jtables[mid].site < site) lo = mid + 1; else hi = mid - 1;
    }
    return NULL;
}
```

- At the start of `a_discover`, also zero the four new output fields. In `a_analysis_free`, `free(an->jtables); free(an->jt_targets);`, set both to `NULL` and both counts to 0.

- [ ] **Step 7: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: all pass, `switch` included.

- [ ] **Step 8: Check the real game**

Run: `third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp funcs game/EBOOT.dec.ELF | grep -E 'indirect|functions|reached'`
Expected: `indirect:` shows at least 400 tables resolved and correspondingly few unresolved computed-jump sites; `reached` goes up. Report the numbers. For up to five sites still unresolved, run `allegrexrecomp dis game/EBOOT.dec.ELF <site-0x20> 12` and say in the report why each did not match.

- [ ] **Step 9: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/analyze.h tools/allegrexrecomp/analyze.c tests/test_switch.c tests/CMakeLists.txt
git commit -m "analyze: resolve SNC \$at switch tables while walking; record them for the emitter"
```

---

### Task 6: Emit recovered switches as C `switch` statements (fork)

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.c` (`emit_function`: label pass and the `jr $rN` case)
- Modify: `third_party/psprecomp/tests/test_switch.c` (append an emit check)

- [ ] **Step 1: Write the failing test**

In `test_switch.c`, before the "bad table" part of `main`, after `a_analysis_free(&an);` of the first case, insert:

```c
    /* The emitter turns the table into a switch with gotos. */
    build(BASE + 0x3C);
    setup(&an);
    CHECK(a_discover(&an, &seed, 1) == 0, "discover for emit");
    emit_opts o;
    memset(&o, 0, sizeof o);
    o.outdir = ".";
    o.prefix = "t_switch";
    o.module = "synthetic";
    CHECK(a_emit(&an, &o) == 0, "emit");
    FILE *f = fopen("./t_switch_funcs.c", "rb");
    static char src[1 << 16];
    size_t len = f ? fread(src, 1, sizeof src - 1, f) : 0;
    src[len] = '\0';
    if (f) fclose(f);
    CHECK(strstr(src, "{ uint32_t _sw = r_at;") != NULL, "target read before the delay slot");
    CHECK(strstr(src, "case 0x08804020u: goto L_08804020;") != NULL, "case 0");
    CHECK(strstr(src, "case 0x0880402Cu: goto L_0880402C;") != NULL, "case 1");
    CHECK(strstr(src, "case 0x0880403Cu: goto L_0880403C;") != NULL, "case 2");
    CHECK(strstr(src, "default: psp_dispatch(_sw); return;") != NULL, "default dispatches");
    CHECK(strstr(src, "psp_dispatch(r_at);") == NULL, "no bare computed dispatch left");
    CHECK(strstr(src, "L_0880403C:") != NULL, "case 2 label emitted");
    a_analysis_free(&an);
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build -R switch --output-on-failure`
Expected: FAIL `target read before the delay slot` (the emitter still writes `psp_dispatch(r_at)`).

- [ ] **Step 3: Mark table targets as labels**

In `emit_function`, in the first loop ("Pass 1"), after the `in.is_jump` label line, add:

```c
        if (in.is_indirect && !in.is_call && !in.is_return) {
            const a_jtable *jt = a_find_jtable(an, a);
            for (uint32_t k = 0; jt && k < jt->count; k++) {
                const uint32_t t = an->jt_targets[jt->first + k];
                if (owned_by(an, t, owner) && !is_function(an, t))
                    c->is_label[widx(an, t)] = 1;
            }
        }
```

- [ ] **Step 4: Emit the switch**

Replace the `if (in.is_indirect) { /* jr $rN — computed jump */ … }` block with:

```c
        if (in.is_indirect) {                    /* jr $rN — computed jump */
            const a_jtable *jt = a_find_jtable(an, a);
            if (jt) {
                /* A recovered switch. The hardware reads the register before
                 * the delay slot runs, so capture it first. */
                fprintf(f, "    { uint32_t _sw = %s;\n", RN[in.rs]);
                if (have_slot) { comment(c, &slot); emit_simple(c, &slot, "      "); }
                fprintf(f, "      switch (_sw) {\n");
                for (uint32_t k = 0; k < jt->count; k++) {
                    const uint32_t t = an->jt_targets[jt->first + k];
                    int dup = 0;
                    for (uint32_t j = 0; j < k && !dup; j++)
                        dup = an->jt_targets[jt->first + j] == t;
                    if (dup) continue;               /* case labels must be distinct */
                    if (owned_by(an, t, owner) && !is_function(an, t))
                        fprintf(f, "      case 0x%08Xu: goto L_%08X;\n", t, t);
                    else if (is_function(an, t))
                        fprintf(f, "      case 0x%08Xu: psp_func_%08X(); return;\n", t, t);
                    else
                        fprintf(f, "      case 0x%08Xu: psp_dispatch(0x%08Xu); return;\n", t, t);
                }
                fprintf(f, "      default: psp_dispatch(_sw); return;\n      }\n    }\n");
            } else {
                if (have_slot) { comment(c, &slot); emit_simple(c, &slot, "    "); }
                fprintf(f, "    psp_dispatch(%s);\n    return;\n", RN[in.rs]);
            }
            if (have_slot && c->is_label[widx(an, a + 4)]) emit_slot_alias(c, a, &slot, 0);
            a += 4;
            continue;
        }
```

- [ ] **Step 5: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: all pass.

- [ ] **Step 6: Check that the real game still compiles and runs as before**

Run: `cmake --build build/pc && perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 50000000`
Expected: the build succeeds with no compiler errors (watch for duplicate `case` values and undefined labels). The run gets past `0x08A8BE6C` (Task 2), and wherever it stops is reported. Note the stop in your report; it is not a failure of this task.

- [ ] **Step 7: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/emit.c tests/test_switch.c
git commit -m "emit: recovered switch tables become C switch statements"
```

---

### Task 7: Dispatch-miss log, `--keep-going` and `merge_misses.py`

**Files:**
- Create: `runtime/include/khbbs/misslog.h`, `runtime/misslog.c`
- Modify: `runtime/CMakeLists.txt` (add `misslog.c`)
- Modify: `runtime/include/khbbs/boot.h`, `runtime/boot.c`
- Modify: `hosts/pc/main.c`
- Create: `tests/runtime/test_misslog.c`; Modify: `tests/runtime/CMakeLists.txt`, `tests/runtime/test_boot.c`
- Create: `tools/merge_misses.py`, `tests/tools/test_merge_misses.py`
- Modify: `.github/workflows/checks.yml` (py_compile list)

- [ ] **Step 1: Write the failing runtime tests**

`tests/runtime/test_misslog.c`:

```c
/* The dispatch-miss log. No game data. */
#include "khbbs/misslog.h"
#include "psprecomp/cpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

int main(void) {
    const char *path = "t_misses.jsonl";
    remove(path);
    CHECK(khbbs_misslog_open(path) == 0, "open");
    psp_cpu.r[PSP_REG_RA] = 0x08A8BE28u;
    psp_cpu.r[PSP_REG_SP] = 0x083FFFC0u;
    khbbs_misslog_record(0x08A8BE6Cu);
    psp_cpu.r[PSP_REG_RA] = 0x08804010u;
    khbbs_misslog_record(0x08900000u);
    CHECK(khbbs_misslog_count() == 2, "two misses counted");
    CHECK(khbbs_misslog_close() == 0, "close");

    FILE *f = fopen(path, "r");
    char a[256] = "", b[256] = "";
    CHECK(f && fgets(a, sizeof a, f) && fgets(b, sizeof b, f), "two lines");
    if (f) fclose(f);
    CHECK(strstr(a, "\"target\":\"0x08A8BE6C\"") && strstr(a, "\"ra\":\"0x08A8BE28\"") &&
          strstr(a, "\"sp\":\"0x083FFFC0\"") && strstr(a, "\"seq\":0"), "first line fields");
    CHECK(strstr(b, "\"target\":\"0x08900000\"") && strstr(b, "\"seq\":1"), "second line");

    /* Re-opening appends, so several runs can share one log. */
    CHECK(khbbs_misslog_open(path) == 0, "reopen");
    khbbs_misslog_record(0x08800000u);
    khbbs_misslog_close();
    f = fopen(path, "r");
    int lines = 0;
    char buf[256];
    while (f && fgets(buf, sizeof buf, f)) lines++;
    if (f) fclose(f);
    CHECK(lines == 3, "appended");

    khbbs_misslog_record(0x1u);          /* closed: a no-op, not a crash */
    printf("%s\n", failures ? "test_misslog: FAILED" : "test_misslog: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt` add a `test_misslog` executable and test the same way as the existing `test_trace` (same link libraries).

In `tests/runtime/test_boot.c`, every `khbbs_boot(&img, path, N)` becomes `khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = N })`, and the NULL cases pass `NULL` opts. Add, before the long-path check:

```c
    /* keep_going: log and step over misses, then return normally. */
    img.entry = 0x08804400u;   /* two wild dispatches, then returns */
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 2 });
    CHECK(r.why == KHBBS_RETURNED && r.misses == 2, "two misses stepped over");
    CHECK(psp_cpu.r[PSP_REG_V0] == 0, "a stepped-over call returns 0");
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .keep_going = 1 });
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0004u && r.misses == 2,
          "the miss past the limit stops the run");
```

with, next to `wild`:

```c
static void two_wild(void) {
    psp_cpu.r[PSP_REG_V0] = 0x1234u;
    psp_dispatch(0x0BAD0000u);
    psp_dispatch(0x0BAD0004u);
}
```

registered with `psp_register(0x08804400u, two_wild);`.

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build build/pc`
Expected: FAIL to compile (`misslog.h` missing, `khbbs_boot_opts` unknown).

- [ ] **Step 3: Write the log**

`runtime/include/khbbs/misslog.h`:

```c
/* Dispatch-miss log for tools/merge_misses.py: one JSON line per miss,
 *   {"seq":0,"target":"0x08A8BE6C","ra":"0x08A8BE28","sp":"0x083FFFC0","last":"0x08A8BD58"}
 * `last` is the most recently entered recompiled function when the generated
 * code is built with PSPRECOMP_TRACE, otherwise null. The file is opened for
 * appending, so several runs can share it. */
#ifndef KHBBS_MISSLOG_H
#define KHBBS_MISSLOG_H

#include <stdint.h>

int      khbbs_misslog_open(const char *path);   /* 0 or -1 */
void     khbbs_misslog_record(uint32_t target);  /* no-op when closed */
uint64_t khbbs_misslog_count(void);              /* recorded since open */
int      khbbs_misslog_close(void);              /* 0, or -1 if a write failed */

#endif
```

`runtime/misslog.c`:

```c
#include "khbbs/misslog.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"

#include <stdio.h>

static FILE *g_out;
static uint64_t g_n;
static int g_err;

int khbbs_misslog_open(const char *path) {
    khbbs_misslog_close();
    g_out = fopen(path, "a");
    if (!g_out) { fprintf(stderr, "khbbs: cannot write miss log %s\n", path); return -1; }
    setvbuf(g_out, NULL, _IOLBF, 0);   /* the run may abort right after */
    g_n = 0;
    g_err = 0;
    return 0;
}

void khbbs_misslog_record(uint32_t target) {
    if (!g_out) return;
    const uint32_t last = psp_trace_last();
    char lastbuf[16];
    if (last) snprintf(lastbuf, sizeof lastbuf, "\"0x%08X\"", last);
    if (fprintf(g_out, "{\"seq\":%llu,\"target\":\"0x%08X\",\"ra\":\"0x%08X\",\"sp\":\"0x%08X\",\"last\":%s}\n",
                (unsigned long long)g_n, target, psp_cpu.r[PSP_REG_RA], psp_cpu.r[PSP_REG_SP],
                last ? lastbuf : "null") < 0)
        g_err = 1;
    g_n++;
}

uint64_t khbbs_misslog_count(void) { return g_n; }

int khbbs_misslog_close(void) {
    if (g_out && fclose(g_out) != 0) g_err = 1;
    g_out = NULL;
    int rc = g_err ? -1 : 0;
    g_err = 0;
    return rc;
}
```

Check `psp_trace_last()`'s declaration in `third_party/psprecomp/include/psprecomp/dispatch.h`; if it is not declared there, declare it in `misslog.c` as `uint32_t psp_trace_last(void);` with a comment that psprecomp defines it in `src/dispatch.c`.

Add `misslog.c` to the `khbbs_runtime` sources in `runtime/CMakeLists.txt`.

- [ ] **Step 4: Boot options and keep-going**

In `runtime/include/khbbs/boot.h`, add to `khbbs_result` a field `uint32_t misses;   /* dispatch misses, including any stepped over */`, and replace the `khbbs_boot` declaration and its comment's `budget` sentence with:

```c
typedef struct {
    uint64_t budget;       /* indirect dispatches allowed, 0 = unlimited (see below) */
    uint32_t keep_going;   /* misses to step over before stopping, 0 = stop at the first */
} khbbs_boot_opts;

/* … existing comment, with: "`opts` may be NULL (no budget, stop at the
 * first miss). A stepped-over miss returns from the dispatch with $v0 and
 * $v1 zeroed, as if the callee did nothing; that is wrong, but it lets one
 * run collect several misses. Every miss goes to the miss log if one is
 * open." */
khbbs_result khbbs_boot(const khbbs_image *img, const char *path, const khbbs_boot_opts *opts);
```

In `runtime/boot.c`, `#include "khbbs/misslog.h"`, add `static uint32_t g_keep_going;`, and replace `on_miss` with:

```c
static void on_miss(uint32_t addr) {
    khbbs_misslog_record(addr);
    g_res.misses++;
    if (g_res.misses <= g_keep_going) {
        psp_cpu.r[PSP_REG_V0] = 0;
        psp_cpu.r[PSP_REG_V1] = 0;
        return;                          /* psp_dispatch returns to its caller */
    }
    g_res.why = KHBBS_DISPATCH_MISS;
    g_res.addr = addr;
    longjmp(g_stop, 1);
}
```

In `khbbs_boot`, take `const khbbs_boot_opts *opts`, compute `uint64_t budget = opts ? opts->budget : 0;` and set `g_keep_going = opts ? opts->keep_going : 0;` before `psp_set_miss_handler`.

- [ ] **Step 5: Host flags**

In `hosts/pc/main.c`: `#include "khbbs/misslog.h"`; usage becomes `usage: khbbs --elf <EBOOT.dec.ELF> [--trace <file.jsonl>] [--budget N] [--miss-log <file.jsonl>] [--keep-going N]`. Parse `--miss-log` like `--trace`, and `--keep-going` exactly like `--budget` (same digit/`strtoull` checks, and reject values above `UINT32_MAX`). Open the log after the trace (`if (misslog && khbbs_misslog_open(misslog) != 0) return 1;`), pass `&(khbbs_boot_opts){ .budget = budget, .keep_going = (uint32_t)keep }` to `khbbs_boot`, print `, %u misses` inside the parenthesis when `r.misses`, and close the log next to the trace (`if (misslog && khbbs_misslog_close() != 0) fprintf(stderr, "khbbs: miss log write failed\n");`).

- [ ] **Step 6: Run the runtime tests**

Run: `cmake --build build/pc && ctest --test-dir build/pc -R 'boot|misslog|trace|loader' --output-on-failure`
Expected: all pass.

- [ ] **Step 7: Write the failing merge test**

`tests/tools/test_merge_misses.py`:

```python
"""merge_misses.py on a synthetic miss log, bbs.toml and ELF. No game data."""
import json, pathlib, subprocess, sys, tempfile, tomllib, unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
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


def miss(t, ra=0x08804010):
    return json.dumps({"seq": 0, "target": f"0x{t:08X}", "ra": f"0x{ra:08X}", "sp": "0x0", "last": None})


class MergeMisses(unittest.TestCase):
    def run_tool(self, log_lines, toml=TOML, elf=True):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "bbs.toml").write_text(toml)
        (d / "m.jsonl").write_text("\n".join(log_lines) + "\n")
        (d / "e.elf").write_bytes(ELF)
        args = [sys.executable, str(TOOL), str(d / "m.jsonl"), "--toml", str(d / "bbs.toml")]
        if elf:
            args += ["--elf", str(d / "e.elf")]
        r = subprocess.run(args, capture_output=True, text=True)
        return r, (d / "bbs.toml").read_text()

    def test_adds_new_sorted_dedup(self):
        # 0x08804040 is hand-added, 0x08804080 already merged, 0x08804060 seen twice.
        r, text = self.run_tool([miss(0x088040C0), miss(0x08804060), miss(0x08804060),
                                 miss(0x08804040), miss(0x08804080)])
        self.assertEqual(r.returncode, 0, r.stderr)
        entries = tomllib.loads(text)["discovery"]["entries"]
        self.assertEqual(entries, [0x08804040, 0x08804060, 0x08804080, 0x088040C0])
        self.assertIn("0x08804060,  # miss, ra 0x08804010", text)
        self.assertIn("added 2", r.stdout)

    def test_outside_code_reported_not_added(self):
        r, text = self.run_tool([miss(0x0BAD0000)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("0x0BAD0000", text)
        self.assertIn("0x0BAD0000", r.stdout)

    def test_missing_markers_refused(self):
        r, _ = self.run_tool([miss(0x08804060)], toml='[discovery]\nentries = []\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("END merge_misses", r.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 8: Run it and watch it fail**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: the `MergeMisses` tests FAIL (no tool).

- [ ] **Step 9: Write the merge tool**

`tools/merge_misses.py`:

```python
#!/usr/bin/env python3
"""Fold khbbs's dispatch-miss log into bbs.toml's discovery.entries.

  tools/merge_misses.py build/misses.jsonl [--toml bbs.toml] [--elf game/EBOOT.dec.ELF]

The entries between the "# BEGIN merge_misses" and "# END merge_misses"
lines of bbs.toml are kept sorted; a new target goes in with the return
address of its first miss as a comment. Targets already listed anywhere in
discovery.entries are skipped. With --elf, a target outside the executable
sections is reported and not added: a miss there is a wild jump or data
called as code, and seeding it would hide the real bug.
"""
import argparse, json, pathlib, re, sys, tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bbs_seeds import sections, exec_ranges, in_code  # noqa: E402

BEGIN = re.compile(r"^\s*# BEGIN merge_misses")
END = re.compile(r"^\s*# END merge_misses")
ENTRY = re.compile(r"^\s*(0x[0-9A-Fa-f]+),(.*)$")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--toml", default="bbs.toml")
    ap.add_argument("--elf")
    a = ap.parse_args(argv)

    text = pathlib.Path(a.toml).read_text()
    known = set(tomllib.loads(text).get("discovery", {}).get("entries", []))
    code = exec_ranges(sections(open(a.elf, "rb").read())) if a.elf else None

    first_ra = {}
    for line in pathlib.Path(a.log).read_text().splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        t = int(m["target"], 16)
        first_ra.setdefault(t, m.get("ra"))

    lines = text.splitlines(keepends=True)
    try:
        b = next(i for i, l in enumerate(lines) if BEGIN.match(l))
        e = next(i for i, l in enumerate(lines) if END.match(l) and i > b)
    except StopIteration:
        print(f"{a.toml}: needs '# BEGIN merge_misses' and '# END merge_misses' lines in discovery.entries",
              file=sys.stderr)
        return 1

    block = {}
    for l in lines[b + 1:e]:
        m = ENTRY.match(l)
        if m:
            block[int(m.group(1), 16)] = l if l.endswith("\n") else l + "\n"
    added = 0
    for t, ra in sorted(first_ra.items()):
        if t in known or t in block:
            continue
        if code is not None and not in_code(t, code):
            print(f"not added: 0x{t:08X} (ra {ra}) is outside the executable sections")
            continue
        block[t] = f"    0x{t:08X},  # miss, ra {ra}\n"
        added += 1

    new = "".join(lines[:b + 1]) + "".join(block[t] for t in sorted(block)) + "".join(lines[e:])
    tomllib.loads(new)                      # never write a file that does not parse
    pathlib.Path(a.toml).write_text(new)
    print(f"added {added}, {len(block)} merged entries in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 10: Run all tool tests, add to CI, commit**

Run: `python3 -m unittest discover -s tests/tools -v` → `OK`.

Add `tools/merge_misses.py` to the `py_compile` line in `.github/workflows/checks.yml`.

```bash
tools/check_no_assets.sh
git add runtime/include/khbbs/misslog.h runtime/misslog.c runtime/CMakeLists.txt runtime/include/khbbs/boot.h \
        runtime/boot.c hosts/pc/main.c tests/runtime/test_misslog.c tests/runtime/CMakeLists.txt \
        tests/runtime/test_boot.c tools/merge_misses.py tests/tools/test_merge_misses.py .github/workflows/checks.yml
git commit -m "runtime: dispatch-miss log and --keep-going; tools/merge_misses.py folds misses into bbs.toml"
```

---

### Task 8: Decode every VFPU encoding the game uses (fork)

Fixes: `vdiv` is VFPU0 sub-op 7, `vsbn` is 2, and sub-op 4 is not an instruction. Names the VFPU7 and VFPU9 tables, `vwbn`, `vqmul`, `vnop`/`vsync`/`vflush`, and `mfvc`/`mtvc`. Adds `vimm5`, the 5-bit field at bits 20..16 that `vrot`, `vcmov`, the conversions and the `bv*` branches use.

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/decode.h` (ops, `vimm5`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/decode.c` (OPINFO, `decode_cop2`, cases 0x18, 0x34, 0x3C, 0x3F)
- Modify: `third_party/psprecomp/tests/test_decode.c:221-224` (the old `vdiv` assertion)
- Create: `third_party/psprecomp/tests/test_decode_vfpu.c`; Modify: `tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

Every word below was assembled by SNC's `pspas` (see Context), except the `vuc2i` word, which SNC spells `vuc2ifs` (same encoding, `0xD0380100`), and `0x63890808`, a `vdiv` from the game's `.text` at `0x08804BE0`.

`third_party/psprecomp/tests/test_decode_vfpu.c`:

```c
/* VFPU decoding against SNC pspas encodings. No game data. */
#include "decode.h"

#include <stdio.h>
#include <string.h>

static int failures;

static void dec(uint32_t w, a_op want, const char *name) {
    a_insn in;
    a_decode(w, 0x08900000u, &in);
    if (in.op != want || strcmp(a_mnemonic(in.op), name) != 0) {
        printf("FAIL 0x%08X: got %s, want %s\n", w, a_mnemonic(in.op), name);
        failures++;
    }
}

#define FIELD(w, expr, label)                                             \
    do { a_insn in; a_decode((w), 0x08900000u, &in);                      \
         if (!(expr)) { printf("FAIL 0x%08X: %s\n", (w), label); failures++; } } while (0)

int main(void) {
    dec(0xFFFF0000u, A_VNOP,   "vnop");
    dec(0xFFFF0320u, A_VSYNC,  "vsync");
    dec(0xFFFF040Du, A_VFLUSH, "vflush");
    dec(0x63C02000u, A_VDIV,   "vdiv");      /* vdiv.s S000, S001, S002 */
    dec(0x63828180u, A_VDIV,   "vdiv");      /* vdiv.q */
    dec(0x63890808u, A_VDIV,   "vdiv");      /* from the game */
    dec(0x61402000u, A_VSBN,   "vsbn");
    dec(0x62000000u, A_VFPU_UNKNOWN, "vfpu?");   /* VFPU0 sub-op 4 is not an instruction */

    /* VFPU7 */
    dec(0xD0200000u, A_VRNDS,  "vrnds");
    dec(0xD0210000u, A_VRNDI,  "vrndi");
    dec(0xD0220000u, A_VRNDF1, "vrndf1");
    dec(0xD0238080u, A_VRNDF2, "vrndf2");
    dec(0xD0320180u, A_VF2H,   "vf2h");
    dec(0xD0330100u, A_VH2F,   "vh2f");
    dec(0xD0360100u, A_VSBZ,   "vsbz");
    dec(0xD0370100u, A_VLGB,   "vlgb");
    dec(0xD0380100u, A_VUC2I,  "vuc2i");
    dec(0xD0390100u, A_VC2I,   "vc2i");
    dec(0xD03A0100u, A_VUS2I,  "vus2i");
    dec(0xD03B0100u, A_VS2I,   "vs2i");
    dec(0xD03C8180u, A_VI2UC,  "vi2uc");
    dec(0xD03D8180u, A_VI2C,   "vi2c");
    dec(0xD03E8180u, A_VI2US,  "vi2us");
    dec(0xD03F0180u, A_VI2S,   "vi2s");

    /* VFPU9 */
    dec(0xD0408180u, A_VSRT1,  "vsrt1");
    dec(0xD0418180u, A_VSRT2,  "vsrt2");
    dec(0xD0428180u, A_VBFY1,  "vbfy1");
    dec(0xD0438180u, A_VBFY2,  "vbfy2");
    dec(0xD0448180u, A_VOCP,   "vocp");
    dec(0xD0450100u, A_VSOCP,  "vsocp");
    dec(0xD0468180u, A_VFAD,   "vfad");
    dec(0xD0478180u, A_VAVG,   "vavg");
    dec(0xD0488180u, A_VSRT3,  "vsrt3");
    dec(0xD0498180u, A_VSRT4,  "vsrt4");
    dec(0xD04A8180u, A_VSGN,   "vsgn");
    dec(0xD0508300u, A_VMFVC,  "vmfvc");
    dec(0xD0510083u, A_VMTVC,  "vmtvc");
    dec(0xD0598180u, A_VT4444, "vt4444");
    dec(0xD05A8180u, A_VT5551, "vt5551");
    dec(0xD05B8180u, A_VT5650, "vt5650");

    /* VFPU4 conversions, vcmov, VFPU6 */
    dec(0xD2038180u, A_VF2IN,  "vf2in");
    dec(0xD2208180u, A_VF2IZ,  "vf2iz");
    dec(0xD2410100u, A_VF2IU,  "vf2iu");
    dec(0xD2620100u, A_VF2ID,  "vf2id");
    dec(0xD2908180u, A_VI2F,   "vi2f");
    dec(0xD2A00100u, A_VCMOV,  "vcmov");
    dec(0xD2AE8180u, A_VCMOV,  "vcmov");
    dec(0xF2828100u, A_VCRSP,  "vcrsp");    /* .t */
    dec(0xF2828180u, A_VQMUL,  "vqmul");    /* .q, same rs */
    dec(0xF3B18180u, A_VROT,   "vrot");
    dec(0x6F028180u, A_VSGE,   "vsge");
    dec(0x6F828180u, A_VSLT,   "vslt");

    /* COP2 moves: bit 7 of the register field selects a control register. */
    dec(0x48640001u, A_MFV,    "mfv");
    dec(0x48E40066u, A_MTV,    "mtv");
    dec(0x48640083u, A_MFVC,   "mfvc");
    dec(0x48E40083u, A_MTVC,   "mtvc");

    FIELD(0x48640001u, in.rt == 4 && in.vd == 0x01, "mfv $a0, S010 fields");
    FIELD(0x48E40066u, in.rt == 4 && in.vd == 0x66, "mtv $a0, S123 fields");
    FIELD(0xF3B18180u, in.vimm5 == 0x11 && in.vsize == 4 && in.vs == 0x01, "vrot.q [-s,c,0,0] code");
    FIELD(0xF3A08100u, in.vimm5 == 0x00 && in.vsize == 3, "vrot.t [c,s,s] code");
    FIELD(0xD2AE8180u, (in.vimm5 & 0xF) == 0x0E && in.vsize == 4, "vcmovf.q cc 6");
    FIELD(0xD2A00100u, (in.vimm5 & 0xF) == 0x00 && in.vsize == 1, "vcmovt.s cc 0");
    FIELD(0xD2038180u, in.vimm5 == 3, "vf2in scale 3");
    FIELD(0xD2908180u, in.vimm5 == 16, "vi2f scale 16");
    FIELD(0xF2828100u, in.vsize == 3, "vcrsp is a triple");

    printf("%s\n", failures ? "test_decode_vfpu: FAILED" : "test_decode_vfpu: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_decode_vfpu test_decode_vfpu.c)
target_link_libraries(test_decode_vfpu PRIVATE allegrex_core)
add_test(NAME decode_vfpu COMMAND test_decode_vfpu)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile (`A_VNOP` and the other new ops undeclared).

- [ ] **Step 3: Declare the ops and the field**

In `decode.h`, in `a_insn` after `vsize`:

```c
    /* VFPU bits 20..16, decoded for every word: the vrot lane code, vcmov's
     * true/false bit and condition, the conversion scale exponent, and the
     * bv* branches' condition index (bits 20..18). */
    uint8_t  vimm5;
```

In the `a_op` enum, replace the comment line and `A_VIIM,` that follow the VFPU6 group with:

```c
    A_VQMUL,
    /* VFPU0 sub-op 2. */
    A_VSBN,
    /* VFPU7 (opcode 0x34, rs=1): random numbers, half floats, packing. */
    A_VRNDS, A_VRNDI, A_VRNDF1, A_VRNDF2, A_VF2H, A_VH2F, A_VSBZ, A_VLGB,
    A_VUC2I, A_VC2I, A_VUS2I, A_VS2I, A_VI2UC, A_VI2C, A_VI2US, A_VI2S,
    /* VFPU9 (opcode 0x34, rs=2): lane sorts, butterflies, horizontal ops. */
    A_VSRT1, A_VSRT2, A_VBFY1, A_VBFY2, A_VOCP, A_VSOCP, A_VFAD, A_VAVG,
    A_VSRT3, A_VSRT4, A_VSGN, A_VMFVC, A_VMTVC, A_VT4444, A_VT5551, A_VT5650,
    /* Opcode 0x3F: pipeline control, no architectural effect here. */
    A_VNOP, A_VSYNC, A_VFLUSH,
    /* Any VFPU encoding we recognise as VFPU but do not yet name. Emitting
     * these is a hard error rather than a silent wrong answer. */
    A_VIIM,
```

(`A_VFIM, A_VFPU_UNKNOWN,` stay where they are.)

- [ ] **Step 4: Names**

In `decode.c`'s `OPINFO`, add (formats as shown):

```c
    [A_VQMUL]  = { "vqmul",  F_VD_VS_VT }, [A_VSBN]   = { "vsbn",   F_VD_VS_VT },
    [A_VRNDS]  = { "vrnds",  F_VD_VS },    [A_VRNDI]  = { "vrndi",  F_VD_VS },
    [A_VRNDF1] = { "vrndf1", F_VD_VS },    [A_VRNDF2] = { "vrndf2", F_VD_VS },
    [A_VF2H]   = { "vf2h",   F_VD_VS },    [A_VH2F]   = { "vh2f",   F_VD_VS },
    [A_VSBZ]   = { "vsbz",   F_VD_VS },    [A_VLGB]   = { "vlgb",   F_VD_VS },
    /* SNC's assembler spells this one "vuc2ifs"; the encoding is the same. */
    [A_VUC2I]  = { "vuc2i",  F_VD_VS },    [A_VC2I]   = { "vc2i",   F_VD_VS },
    [A_VUS2I]  = { "vus2i",  F_VD_VS },    [A_VS2I]   = { "vs2i",   F_VD_VS },
    [A_VI2UC]  = { "vi2uc",  F_VD_VS },    [A_VI2C]   = { "vi2c",   F_VD_VS },
    [A_VI2US]  = { "vi2us",  F_VD_VS },    [A_VI2S]   = { "vi2s",   F_VD_VS },
    [A_VSRT1]  = { "vsrt1",  F_VD_VS },    [A_VSRT2]  = { "vsrt2",  F_VD_VS },
    [A_VBFY1]  = { "vbfy1",  F_VD_VS },    [A_VBFY2]  = { "vbfy2",  F_VD_VS },
    [A_VOCP]   = { "vocp",   F_VD_VS },    [A_VSOCP]  = { "vsocp",  F_VD_VS },
    [A_VFAD]   = { "vfad",   F_VD_VS },    [A_VAVG]   = { "vavg",   F_VD_VS },
    [A_VSRT3]  = { "vsrt3",  F_VD_VS },    [A_VSRT4]  = { "vsrt4",  F_VD_VS },
    [A_VSGN]   = { "vsgn",   F_VD_VS },    [A_VMFVC]  = { "vmfvc",  F_VD_VS },
    [A_VMTVC]  = { "vmtvc",  F_VD_VS },    [A_VT4444] = { "vt4444", F_VD_VS },
    [A_VT5551] = { "vt5551", F_VD_VS },    [A_VT5650] = { "vt5650", F_VD_VS },
    [A_VNOP]   = { "vnop",   F_NONE },     [A_VSYNC]  = { "vsync",  F_NONE },
    [A_VFLUSH] = { "vflush", F_NONE },
```

Check that `A_VWBN` already has an OPINFO entry; add `[A_VWBN] = { "vwbn", F_VD_VS },` if not.

- [ ] **Step 5: Decode**

In `a_decode`, after `in.vsize = …`, add `in.vimm5 = (uint8_t)((word >> 16) & 0x1F);`.

In `decode_cop2`, replace the `0x03` and `0x07` cases with:

```c
    /* Bit 7 of the register field selects the control registers (128..143):
     * mfvc/mtvc share mfv/mtv's sub-opcode. */
    case 0x03: return (w & 0x80) ? A_MFVC : A_MFV;
    case 0x07: return (w & 0x80) ? A_MTVC : A_MTV;
```

Replace `case 0x18:`'s inner switch, and delete the comment claiming `vdiv` is sub-op 4:

```c
    case 0x18:
        /* VFPU0. SNC assembles vdiv.s as 0x63C02000: sub-opcode 7. An
         * earlier revision put vdiv at 4, which is not an instruction, and
         * left every real vdiv (17 sites in ULUS10505) undecoded. */
        switch ((word >> 23) & 7) {
        case 0: op = A_VADD; break;
        case 1: op = A_VSUB; break;
        case 2: op = A_VSBN; break;
        case 7: op = A_VDIV; break;
        default: op = A_VFPU_UNKNOWN; break;
        }
        break;
```

In `case 0x34:`, replace the `/* VFPU7 (rs=1) and VFPU9 (rs=2) … */ default:` part with:

```c
        case 0x01:                                  /* VFPU7 */
            switch (RT_F(word)) {
            case 0x00: op = A_VRNDS;  break;
            case 0x01: op = A_VRNDI;  break;
            case 0x02: op = A_VRNDF1; break;
            case 0x03: op = A_VRNDF2; break;
            case 0x12: op = A_VF2H;   break;
            case 0x13: op = A_VH2F;   break;
            case 0x16: op = A_VSBZ;   break;
            case 0x17: op = A_VLGB;   break;
            case 0x18: op = A_VUC2I;  break;
            case 0x19: op = A_VC2I;   break;
            case 0x1A: op = A_VUS2I;  break;
            case 0x1B: op = A_VS2I;   break;
            case 0x1C: op = A_VI2UC;  break;
            case 0x1D: op = A_VI2C;   break;
            case 0x1E: op = A_VI2US;  break;
            case 0x1F: op = A_VI2S;   break;
            default:   op = A_VFPU_UNKNOWN; break;
            }
            break;
        case 0x02:                                  /* VFPU9 */
            switch (RT_F(word)) {
            case 0x00: op = A_VSRT1;  break;
            case 0x01: op = A_VSRT2;  break;
            case 0x02: op = A_VBFY1;  break;
            case 0x03: op = A_VBFY2;  break;
            case 0x04: op = A_VOCP;   break;
            case 0x05: op = A_VSOCP;  break;
            case 0x06: op = A_VFAD;   break;
            case 0x07: op = A_VAVG;   break;
            case 0x08: op = A_VSRT3;  break;
            case 0x09: op = A_VSRT4;  break;
            case 0x0A: op = A_VSGN;   break;
            case 0x10: op = A_VMFVC;  break;
            case 0x11: op = A_VMTVC;  break;
            case 0x19: op = A_VT4444; break;
            case 0x1A: op = A_VT5551; break;
            case 0x1B: op = A_VT5650; break;
            default:   op = A_VFPU_UNKNOWN; break;
            }
            break;
        case 0x18: case 0x19: case 0x1A: case 0x1B:
        case 0x1C: case 0x1D: case 0x1E: case 0x1F:
            op = A_VWBN; break;
        default:   op = A_VFPU_UNKNOWN; break;
```

In `case 0x3C:` change `case 0x14: op = A_VCRSP; break;` to:

```c
        /* vcrsp.t and vqmul.q share rs; the width tells them apart. */
        case 0x14: op = (in.vsize == 4) ? A_VQMUL : A_VCRSP; break;
```

Split `0x3F` out of the `case 0x1C: … case 0x3F:` group:

```c
    case 0x3F:
        /* VFPU7 control words: exact encodings, nothing else is defined. */
        if (word == 0xFFFF0000u)      op = A_VNOP;
        else if (word == 0xFFFF0320u) op = A_VSYNC;
        else if (word == 0xFFFF040Du) op = A_VFLUSH;
        else                          op = A_VFPU_UNKNOWN;
        break;
```

- [ ] **Step 6: Fix the old assertion**

In `tests/test_decode.c` (around line 221), replace the comment and `dec(0x62000000, 0x08900000, A_VDIV, …)` with:

```c
    /* vdiv is VFPU0 sub-opcode 7: SNC's assembler encodes vdiv.s S000, S001,
     * S002 as 0x63C02000. Sub-opcode 4 is not an instruction. */
    dec(0x63C02000, 0x08900000, A_VDIV, "vdiv (VFPU0 sub-opcode 7)");
```

If `test_decode.c` also asserts that 0x3F or the old `vfpu?` words are unknown, update those to the names above. Keep the `0xD4000000` (opcode 0x35) unknown assertion.

- [ ] **Step 7: Run all fork tests and the census**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: all pass.

Then `cmake --build build/pc && python3 tools/unimpl_census.py build/pc/gen`.
Expected: no `vfpu? 0x…` lines any more; the newly named ops (`vnop`, `vfad`, `vi2uc`, …) appear by name instead, and `vdiv` does not appear (it already has an emitter case). The total is unchanged or lower. Record it.

- [ ] **Step 8: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/decode.h tools/allegrexrecomp/decode.c tests/test_decode.c tests/test_decode_vfpu.c tests/CMakeLists.txt
git commit -m "decode: vdiv is VFPU0 sub-op 7; name VFPU7/VFPU9, vqmul, vnop/vsync/vflush, mfvc/mtvc"
```

---

### Task 9: VFPU prefixes, `vcmp`'s 16 conditions, `bvf`/`bvt`, a fatal trap (fork + runtime)

Today a pending `vpfxs`/`vpfxt`/`vpfxd` makes the next op skip itself, `vcmp` treats conditions 8–15 as false, and the 47 `bv*` branches are never taken. This task implements the prefixes and those conditions, emits the branches, makes any remaining VFPU trap fatal in `khbbs`, and builds the library with `-ffp-contract=off`.

Prefix semantics (from the VFPU documentation; the encodings are checked against `pspas`):
- `vpfxs`/`vpfxt`, 20 bits. For lane i: bits `2i+1..2i` pick a source lane (0–3, any lane of the row/column even for a narrower op), bit `8+i` is abs, bit `12+i` is constant, bit `16+i` is negate. With the constant bit set, the value is `{0, 1, 2, 1/2}` (abs bit clear) or `{3, 1/3, 1/4, 1/6}` (abs bit set), indexed by the lane-pick bits, then negated if bit `16+i` is set. `vpfxs X, Y, Z, W` = `0xE4` is the identity; `vpfxs -X, |Y|, 1, 1/2` = `0x01C2D4`.
- `vpfxd`, 12 bits. For lane i: bits `2i+1..2i` saturate (1 → [0, 1], 3 → [−1, 1], 0 and 2 → none); bit `8+i` masks the write. `vpfxd 0:1, -1:1, M,` = `0x00040D`.
- Every VFPU op that computes consumes all three. Reading lanes beyond the op's width wraps within the row/column; that reading is not verified against hardware and is marked so in the code.

**Files:**
- Create: `third_party/psprecomp/src/vfpu_internal.h`
- Modify: `third_party/psprecomp/src/vfpu.c`, `include/psprecomp/vfpu.h`
- Modify: `third_party/psprecomp/CMakeLists.txt` (`-ffp-contract=off` on `psprecomp`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.c` (`branch_cond`)
- Create: `third_party/psprecomp/tests/test_vfpu_prefix.c`, `test_emit_vfpu.c`; Modify: `tests/CMakeLists.txt`
- Modify: `runtime/unimplemented.c`, `runtime/include/khbbs/unimplemented.h`, `hosts/pc/main.c`

- [ ] **Step 1: Write the failing prefix test**

`third_party/psprecomp/tests/test_vfpu_prefix.c`:

```c
/* VFPU operand prefixes and vcmp conditions. Synthetic values only. */
#include "psprecomp/vfpu.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); failures++; } } while (0)

static void set4(uint32_t vreg, float a, float b, float c, float d) {
    int r[4];
    psp_vfpu_regs(vreg, 4, r);
    psp_cpu.v[r[0]] = a; psp_cpu.v[r[1]] = b; psp_cpu.v[r[2]] = c; psp_cpu.v[r[3]] = d;
}
static void get4(uint32_t vreg, float out[4]) {
    int r[4];
    psp_vfpu_regs(vreg, 4, r);
    for (int i = 0; i < 4; i++) out[i] = psp_cpu.v[r[i]];
}
#define EQ4(o, a, b, c, d) ((o)[0] == (a) && (o)[1] == (b) && (o)[2] == (c) && (o)[3] == (d))

static int traps;
static void on_trap(uint32_t addr, const char *what) { (void)addr; (void)what; traps++; }

#define C000 0x00u
#define C010 0x01u
#define C020 0x02u

int main(void) {
    float o[4];
    psp_vfpu_reset();

    /* Identity prefix: same as none. */
    set4(C000, 1, 2, 3, 4); set4(C010, 10, 20, 30, 40);
    psp_vfpu_set_prefix(PSP_VPFX_S, 0xE4);
    psp_vfpu_set_prefix(PSP_VPFX_T, 0xE4);
    psp_vadd(C020, C000, C010, 4);
    get4(C020, o);
    CHECK(EQ4(o, 11, 22, 33, 44), "identity prefixes: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* Swizzle [W, Z, Y, X] on the source of vmov.q. */
    psp_vfpu_set_prefix(PSP_VPFX_S, 0x1B);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(EQ4(o, 4, 3, 2, 1), "swizzle wzyx: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* The prefix was consumed: the next op is unprefixed. */
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(EQ4(o, 1, 2, 3, 4), "prefix consumed: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* [-X, |Y|, 1, 1/2] (pspas: 0xDC01C2D4). */
    set4(C000, 2, -3, 9, 9);
    psp_vfpu_set_prefix(PSP_VPFX_S, 0x01C2D4);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(EQ4(o, -2, 3, 1, 0.5f), "neg/abs/constants: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* The other constant table: abs bit set, constant bit set, picks 0..3. */
    psp_vfpu_set_prefix(PSP_VPFX_S, 0xF000u | 0x0F00u | 0xE4u);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(o[0] == 3.0f && o[1] == 1.0f / 3.0f && o[2] == 0.25f && o[3] == 1.0f / 6.0f,
          "second constant table: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* Destination: [0:1, -1:1, M, -] (pspas: 0xDE00040D). */
    set4(C000, 1.5f, -2, 7, -0.25f);
    set4(C020, 9, 9, 9, 9);
    psp_vfpu_set_prefix(PSP_VPFX_D, 0x00040D);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(EQ4(o, 1, -1, 9, -0.25f), "saturate and mask: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* T prefix applies to vt, not vs. */
    set4(C000, 1, 1, 1, 1); set4(C010, 1, 2, 3, 4);
    psp_vfpu_set_prefix(PSP_VPFX_T, 0x1B);
    psp_vsub(C020, C000, C010, 4);
    get4(C020, o);
    CHECK(EQ4(o, -3, -2, -1, 0), "t prefix: %g %g %g %g", o[0], o[1], o[2], o[3]);

    /* vcmp conditions 8..15 test vs alone. */
    set4(C000, 0.0f, NAN, INFINITY, 5.0f);
    psp_vcmp(9, C000, C000, 4);                    /* EN: is NaN */
    CHECK((psp_cpu.vfpu_cc & 0xF) == 0x2 && (psp_cpu.vfpu_cc & 0x10) && !(psp_cpu.vfpu_cc & 0x20),
          "vcmp EN: cc=0x%X", psp_cpu.vfpu_cc);
    psp_vcmp(8, C000, C000, 4);                    /* EZ */
    CHECK((psp_cpu.vfpu_cc & 0xF) == 0x1, "vcmp EZ: cc=0x%X", psp_cpu.vfpu_cc);
    psp_vcmp(11, C000, C000, 4);                   /* ES: NaN or inf */
    CHECK((psp_cpu.vfpu_cc & 0xF) == 0x6, "vcmp ES: cc=0x%X", psp_cpu.vfpu_cc);
    psp_vcmp(15, C000, C000, 4);                   /* NS */
    CHECK((psp_cpu.vfpu_cc & 0xF) == 0x9, "vcmp NS: cc=0x%X", psp_cpu.vfpu_cc);

    /* An op without prefix support traps through the handler when one is pending. */
    psp_vfpu_set_trap_handler(on_trap);
    psp_vfpu_set_prefix(PSP_VPFX_S, 0x1B);
    psp_vmmov(0x00u, 0x04u, 4);
    CHECK(traps == 1, "matrix op with a pending prefix traps (%d)", traps);
    CHECK(!psp_vfpu_prefix_pending(), "the trap consumed the prefix");
    psp_vfpu_set_trap_handler(NULL);

    printf("%s\n", failures ? "test_vfpu_prefix: FAILED" : "test_vfpu_prefix: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_vfpu_prefix test_vfpu_prefix.c)
target_link_libraries(test_vfpu_prefix PRIVATE psprecomp)
add_test(NAME vfpu_prefix COMMAND test_vfpu_prefix)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile (`PSP_VPFX_S`, `psp_vfpu_set_trap_handler` undeclared).

- [ ] **Step 3: Public API**

In `include/psprecomp/vfpu.h`, replace the prefix block's declarations with:

```c
/* Prefix state. vpfxs/vpfxt rewrite the next op's vs/vt operand (lane
 * select, abs, constant, negate); vpfxd saturates and masks its writes.
 * Every VFPU op that computes consumes all three. */
enum { PSP_VPFX_S = 0, PSP_VPFX_T = 1, PSP_VPFX_D = 2 };
void psp_vfpu_set_prefix(int which, uint32_t value);
int  psp_vfpu_prefix_pending(void);
void psp_vfpu_reset(void);

/* What happens when the VFPU meets something it cannot execute (an op with
 * no prefix support while a prefix is pending, an unknown vunary selector).
 * NULL restores the default: print the first few and count them. A host
 * that must not run on with wrong values installs a handler that stops. */
typedef void (*psp_vfpu_trap_fn)(uint32_t addr, const char *what);
void psp_vfpu_set_trap_handler(psp_vfpu_trap_fn fn);
```

Update the file's header comment: prefixes are now implemented for the element-wise ops (list them), and the matrix ops still trap.

- [ ] **Step 4: Internal helpers**

`src/vfpu_internal.h`:

```c
/* Internal to the VFPU implementation (vfpu.c, vfpu_ops.c): operand access
 * with prefixes applied. Every op reads its sources through vfpu_read, writes
 * through vfpu_write, and ends with vfpu_end. */
#ifndef PSPRECOMP_VFPU_INTERNAL_H
#define PSPRECOMP_VFPU_INTERNAL_H

#include "psprecomp/vfpu.h"

/* `n` lanes of `vreg` as operand `which` (PSP_VPFX_S or PSP_VPFX_T), with
 * that operand's pending prefix applied. */
void vfpu_read(int which, uint32_t vreg, int n, float out[4]);
/* `n` lanes with no prefix, e.g. a destination's old value. */
void vfpu_read_raw(uint32_t vreg, int n, float out[4]);
/* Write `n` lanes through the pending destination prefix. */
void vfpu_write(uint32_t vreg, int n, const float in[4]);
/* The same for integer results: the write mask applies, saturation does not. */
void vfpu_write_bits(uint32_t vreg, int n, const uint32_t in[4]);
/* Consume the prefixes. */
void vfpu_end(void);
/* For ops without prefix support: 1 to go ahead, or 0 after trapping (and
 * consuming the prefixes) if one is pending. */
int vfpu_no_prefix(const char *what);
/* Report through the trap handler. */
void vfpu_trap(const char *what);

#endif
```

- [ ] **Step 5: Implement them in `vfpu.c`**

At the top of `src/vfpu.c` include `"vfpu_internal.h"`. Add `static psp_vfpu_trap_fn g_trap_fn;` and:

```c
void psp_vfpu_set_trap_handler(psp_vfpu_trap_fn fn) { g_trap_fn = fn; }

void vfpu_trap(const char *what) {
    if (g_trap_fn) { g_trap_fn(psp_cpu.pc, what); return; }
    psp_vfpu_unimplemented(psp_cpu.pc, what);
}
```

and change `psp_vfpu_unimplemented` to call `g_trap_fn` first when one is set (then return). Replace `take_prefixes` with:

```c
static const float PFX_CONST[2][4] = {
    { 0.0f, 1.0f, 2.0f, 0.5f },
    { 3.0f, 1.0f / 3.0f, 0.25f, 1.0f / 6.0f },
};

/* All four lanes of the row or column `vreg` names, starting at its first
 * lane and wrapping, so a prefix can pick any of them for a narrower op.
 * The wrap is our reading of the documentation, not verified on hardware;
 * P2's oracle checks it. */
static void lanes4(uint32_t vreg, int n, int out[4]) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    if (n == 4) { memcpy(out, r, sizeof r); return; }
    const int mtx = (vreg >> 2) & 7, col = vreg & 3;
    const int transpose = n == 1 ? 0 : (vreg >> 5) & 1;
    const int row = n == 1 ? (vreg >> 5) & 3 : n == 2 ? (vreg >> 5) & 2 : (vreg >> 6) & 1;
    for (int i = 0; i < 4; i++) {
        const int step = (row + i) & 3;
        out[i] = transpose ? mtx * 4 + step * 32 + col : mtx * 4 + col * 32 + step;
    }
}

void vfpu_read_raw(uint32_t vreg, int n, float out[4]) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    for (int i = 0; i < n; i++) out[i] = psp_cpu.v[r[i]];
}

void vfpu_read(int which, uint32_t vreg, int n, float out[4]) {
    if (!g_prefix_set[which]) { vfpu_read_raw(vreg, n, out); return; }
    int r[4];
    lanes4(vreg, n, r);
    const uint32_t p = g_prefix[which];
    for (int i = 0; i < n; i++) {
        const int pick = (p >> (2 * i)) & 3;
        const int abs  = (p >> (8 + i)) & 1;
        const int cst  = (p >> (12 + i)) & 1;
        const int neg  = (p >> (16 + i)) & 1;
        uint32_t bits = psp_f32_to_bits(cst ? PFX_CONST[abs][pick] : psp_cpu.v[r[pick]]);
        if (!cst && abs) bits &= 0x7FFFFFFFu;      /* on the bits, so NaNs keep their payload */
        if (neg) bits ^= 0x80000000u;
        out[i] = psp_bits_to_f32(bits);
    }
}

static float sat0(float v);
static float sat1(float v);

void vfpu_write(uint32_t vreg, int n, const float in[4]) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    const uint32_t p = g_prefix_set[PSP_VPFX_D] ? g_prefix[PSP_VPFX_D] : 0;
    for (int i = 0; i < n; i++) {
        if ((p >> (8 + i)) & 1) continue;          /* write mask */
        float v = in[i];
        switch ((p >> (2 * i)) & 3) {
        case 1: v = sat0(v); break;
        case 3: v = sat1(v); break;
        default: break;
        }
        psp_cpu.v[r[i]] = v;
    }
}

void vfpu_write_bits(uint32_t vreg, int n, const uint32_t in[4]) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    const uint32_t p = g_prefix_set[PSP_VPFX_D] ? g_prefix[PSP_VPFX_D] : 0;
    for (int i = 0; i < n; i++)
        if (!((p >> (8 + i)) & 1)) psp_cpu.v[r[i]] = psp_bits_to_f32(in[i]);
}

void vfpu_end(void) { memset(g_prefix_set, 0, sizeof g_prefix_set); }

int vfpu_no_prefix(const char *what) {
    if (!psp_vfpu_prefix_pending()) return 1;
    vfpu_end();
    vfpu_trap(what);
    return 0;
}
```

Move the existing `sat0`/`sat1` definitions above `vfpu_write` (and drop the forward declarations) if the compiler complains about order.

- [ ] **Step 6: Route the existing ops through them**

- `BINOP`: replace the body with reads via `vfpu_read(PSP_VPFX_S, vs, size, s)` and `vfpu_read(PSP_VPFX_T, vt, size, t)`, compute `d[i] = (expr)` over `size` lanes using `a = s[i]`, `b = t[i]`, then `vfpu_write(vd, size, d); vfpu_end();`. Keep the comment about reading all sources before writing.
- `psp_vdot`: read s and t through `vfpu_read`, sum in lane order, `vfpu_write(vd, 1, &sum)`, `vfpu_end()`.
- `psp_vscl`: `vfpu_read(PSP_VPFX_S, vs, size, s)`, `vfpu_read(PSP_VPFX_T, vt, 1, t)`, `d[i] = s[i] * t[0]`, write, end.
- `psp_vunary`: `vfpu_read(PSP_VPFX_S, vs, size, s)`, compute, `vfpu_write`, `vfpu_end`; the default case calls `vfpu_end(); vfpu_trap("vunary"); return;`.
- `psp_vcmp`: read through `vfpu_read`, add conditions 8–15 to its switch, and `vfpu_end()` at the end:

```c
        case 8:  r = (a == 0.0f);                 break;   /* EZ */
        case 9:  r = isnan(a);                    break;   /* EN */
        case 10: r = isinf(a);                    break;   /* EI */
        case 11: r = isnan(a) || isinf(a);        break;   /* ES */
        case 12: r = (a != 0.0f);                 break;   /* NZ */
        case 13: r = !isnan(a);                   break;   /* NN */
        case 14: r = !isinf(a);                   break;   /* NI */
        case 15: r = !(isnan(a) || isinf(a));     break;   /* NS */
```
  (`isnan`/`isinf` need `<math.h>`, already included.)
- `psp_vidt`, `psp_vcst`: build the lanes in a `float d[4]`, `vfpu_write`, `vfpu_end`.
- `psp_vimm`: keep the raw write, then `vfpu_end()`.
- The matrix ops (`vmidt`, `vmzero`, `vmone`, `vmmov`, `vmscl`, `vtfm`, `vmmul`): replace `take_prefixes(psp_cpu.pc, "…")` with `vfpu_no_prefix("…")`.

`psp_vfpu_reset` leaves the trap handler alone, so a host's handler survives a reset; say so in its comment.

- [ ] **Step 7: No fused multiply-add in the runtime**

In `third_party/psprecomp/CMakeLists.txt`, after `add_library(psprecomp STATIC …)`:

```cmake
# PSP floats are never fused: a*b+c rounds twice on the Allegrex and the VFPU.
# Clang contracts within expressions by default, which changes results.
if(CMAKE_C_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(psprecomp PRIVATE -ffp-contract=off)
endif()
```

- [ ] **Step 8: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: all pass. If the old `test_vfpu.c` asserts that a prefixed op traps, change it to assert the prefixed result instead (the trap is gone for element-wise ops by design), and keep a trap assertion for a matrix op.

- [ ] **Step 9: Write the failing emitter test (`bvf`/`bvt`)**

`third_party/psprecomp/tests/test_emit_vfpu.c`:

```c
/* VFPU and FPU emission: one instruction per function, checked as text.
 * Synthetic code only, no game data. Tasks 10-15 add cases here. */
#include "analyze.h"
#include "emit.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BASE 0x08804000u
static int failures;

/* Emit `word` at BASE, a nop (its delay slot, if it is a branch), then
 * jr $ra; nop twice, and return the generated C. A test branch targets
 * BASE+16, the second jr $ra (offset 3). */
static const char *emit_one(uint32_t word) {
    static char src[1 << 16];
    uint32_t w[8] = { word, 0, 0x03E00008u, 0, 0x03E00008u, 0, 0, 0 };
    uint8_t code[sizeof w];
    for (size_t i = 0; i < 8; i++)
        for (int b = 0; b < 4; b++) code[i * 4 + b] = (uint8_t)(w[i] >> (8 * b));
    a_analysis an;
    memset(&an, 0, sizeof an);
    an.code = code;
    an.base = BASE;
    an.size = sizeof code;
    uint32_t seed = BASE;
    src[0] = '\0';
    if (a_discover(&an, &seed, 1) != 0) return src;
    emit_opts o;
    memset(&o, 0, sizeof o);
    o.outdir = ".";
    o.prefix = "t_emit_vfpu";
    o.module = "synthetic";
    if (a_emit(&an, &o) == 0) {
        FILE *f = fopen("./t_emit_vfpu_funcs.c", "rb");
        if (f) { src[fread(src, 1, sizeof src - 1, f)] = '\0'; fclose(f); }
    }
    a_analysis_free(&an);
    return src;
}

static void expect(uint32_t word, const char *needle) {
    const char *src = emit_one(word);
    if (!strstr(src, needle)) { printf("FAIL 0x%08X: expected \"%s\"\n", word, needle); failures++; }
    if (strstr(src, "psp_unimplemented") || strstr(src, "unhandled branch")) {
        printf("FAIL 0x%08X: still untranslated\n", word);
        failures++;
    }
}

int main(void) {
    /* bvf 0 / bvt 2 / bvtl 5, all to BASE+16. cc index = rt >> 2. */
    expect(0x49000003u, "!((psp_cpu.vfpu_cc >> 0) & 1)");
    expect(0x49090003u, "((psp_cpu.vfpu_cc >> 2) & 1)");
    expect(0x49170003u, "((psp_cpu.vfpu_cc >> 5) & 1)");

    printf("%s\n", failures ? "test_emit_vfpu: FAILED" : "test_emit_vfpu: ok");
    return failures ? 1 : 0;
}
```

The three words are `bvf 0`, `bvt 2` and `bvtl 5` with offset 3. Before relying on them, confirm with `pspas` (`bvf 0, lbl` / `bvt 2, lbl` / `bvtl 5, lbl` where `lbl` is 4 instructions ahead) that the condition index sits in bits 20..18 and the type in bits 17..16; if `pspas` disagrees, use its words and its field layout here and in Step 10.

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_emit_vfpu test_emit_vfpu.c)
target_link_libraries(test_emit_vfpu PRIVATE allegrex_core)
add_test(NAME emit_vfpu COMMAND test_emit_vfpu)
```

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build -R emit_vfpu --output-on-failure`
Expected: FAIL (`unhandled branch`).

- [ ] **Step 10: Emit the VFPU branches**

In `emit.c`'s `branch_cond`, before `default:`:

```c
    /* VFPU condition branches: bits 20..18 pick a vfpu_cc bit (0-3 lanes,
     * 4 any, 5 all, as vcmp writes them). */
    case A_BVF: case A_BVFL:
        snprintf(buf, n, "!((psp_cpu.vfpu_cc >> %u) & 1)", (unsigned)(in->vimm5 >> 2)); break;
    case A_BVT: case A_BVTL:
        snprintf(buf, n, "((psp_cpu.vfpu_cc >> %u) & 1)", (unsigned)(in->vimm5 >> 2)); break;
```

Run the fork tests → all pass.

- [ ] **Step 11: A VFPU trap stops `khbbs`**

In `runtime/include/khbbs/unimplemented.h`, declare `void khbbs_install_traps(void);` with the comment "Make every VFPU trap fatal (through psp_unimplemented). Call once at start-up." In `runtime/unimplemented.c`:

```c
#include "psprecomp/vfpu.h"

static void vfpu_trap(uint32_t addr, const char *what) {
    char buf[64];
    snprintf(buf, sizeof buf, "VFPU %s", what);
    psp_unimplemented(addr, buf);
}

void khbbs_install_traps(void) { psp_vfpu_set_trap_handler(vfpu_trap); }
```

In `hosts/pc/main.c`, call `khbbs_install_traps();` right after `psp_hle_init();`.

- [ ] **Step 12: Rebuild, census, commit both repos**

Run: `cmake --build build/pc && ctest --test-dir build/pc --output-on-failure && python3 tools/unimpl_census.py build/pc/gen`
Expected: tests pass; `unhandled branches 0`.

```bash
cd third_party/psprecomp
git add src/vfpu_internal.h src/vfpu.c include/psprecomp/vfpu.h CMakeLists.txt tools/allegrexrecomp/emit.c \
        tests/test_vfpu_prefix.c tests/test_emit_vfpu.c tests/test_vfpu.c tests/CMakeLists.txt
git commit -m "vfpu: operand prefixes, vcmp conditions 8-15, bvf/bvt branches, trap handler, no FMA"
cd ../..
tools/check_no_assets.sh
git add runtime/unimplemented.c runtime/include/khbbs/unimplemented.h hosts/pc/main.c
git commit -m "runtime: VFPU traps are fatal in khbbs"
```

---

### Task 10: `mfv`, `mtv`, `vnop`, `vsync`, `vflush` (fork)

**Files:**
- Create: `third_party/psprecomp/src/vfpu_ops.c`; Modify: `CMakeLists.txt` (add it to `psprecomp`)
- Modify: `include/psprecomp/vfpu.h`, `tools/allegrexrecomp/emit.c`
- Create: `tests/test_vfpu_ops.c`; Modify: `tests/test_emit_vfpu.c`, `tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing tests**

`third_party/psprecomp/tests/test_vfpu_ops.c`:

```c
/* VFPU ops added in P3. Synthetic values only. Tasks 11-13 add cases. */
#include "psprecomp/vfpu.h"
#include "psprecomp/recomp_rt.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); failures++; } } while (0)

static void set4(uint32_t vreg, int n, const float *v) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    for (int i = 0; i < n; i++) psp_cpu.v[r[i]] = v[i];
}
static void get(uint32_t vreg, int n, float *out) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    for (int i = 0; i < n; i++) out[i] = psp_cpu.v[r[i]];
}
static uint32_t bits(float f) { return psp_f32_to_bits(f); }

static void test_moves(void) {
    /* mtv/mfv move bits, not values: a NaN payload and -0 survive. */
    psp_mtv(0x66u, 0x7FC00001u);
    CHECK(psp_mfv(0x66u) == 0x7FC00001u, "NaN payload round trip: 0x%08X", psp_mfv(0x66u));
    psp_mtv(0x01u, 0x80000000u);
    CHECK(psp_mfv(0x01u) == 0x80000000u, "-0 round trip");
    float s;
    get(0x01u, 1, &s);
    CHECK(bits(s) == 0x80000000u, "mtv wrote S010");
}

int main(void) {
    psp_vfpu_reset();
    test_moves();
    printf("%s\n", failures ? "test_vfpu_ops: FAILED" : "test_vfpu_ops: ok");
    return failures ? 1 : 0;
}
```

(`set4` is unused until Task 11; mark it `static inline` or add `(void)set4;` in `main` to keep the build warning-free.)

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_vfpu_ops test_vfpu_ops.c)
target_link_libraries(test_vfpu_ops PRIVATE psprecomp)
add_test(NAME vfpu_ops COMMAND test_vfpu_ops)
```

In `test_emit_vfpu.c`'s `main`, add:

```c
    expect(0x48640001u, "r_a0 = psp_mfv(1);");          /* mfv $a0, S010 */
    expect(0x48E40066u, "psp_mtv(102, r_a0);");         /* mtv $a0, S123 */
    expect(0xFFFF0000u, ";  /* vnop */");
    expect(0xFFFF0320u, ";  /* vsync */");
    expect(0xFFFF040Du, ";  /* vflush */");
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile (`psp_mfv` undeclared).

- [ ] **Step 3: Runtime**

In `include/psprecomp/vfpu.h`:

```c
/* mfv/mtv: move a single register's bits to or from a GPR. */
uint32_t psp_mfv(uint32_t vd);
void     psp_mtv(uint32_t vd, uint32_t bits);
```

`src/vfpu_ops.c`:

```c
/* psprecomp: the VFPU ops added for KH BBS (P3). See vfpu.h. */
#include "psprecomp/vfpu.h"
#include "psprecomp/recomp_rt.h"
#include "vfpu_internal.h"

#include <math.h>
#include <string.h>

uint32_t psp_mfv(uint32_t vd) {
    int r[4];
    psp_vfpu_regs(vd, 1, r);
    return psp_f32_to_bits(psp_cpu.v[r[0]]);
}

void psp_mtv(uint32_t vd, uint32_t bits) {
    int r[4];
    psp_vfpu_regs(vd, 1, r);
    psp_cpu.v[r[0]] = psp_bits_to_f32(bits);
}
```

Add `src/vfpu_ops.c` to `add_library(psprecomp STATIC …)`.

- [ ] **Step 4: Emitter**

In `emit_simple`, before `case A_VPFXS:`:

```c
    /* Moves between a GPR and a VFPU single, as bits. */
    case A_MFV:
        if (DEST_ZERO(in->rt)) { fprintf(f, "%s;\n", ind); return; }
        fprintf(f, "%s%s = psp_mfv(%u);\n", ind, rt, in->vd); return;
    case A_MTV:
        fprintf(f, "%spsp_mtv(%u, %s);\n", ind, in->vd, rt); return;
    /* Pipeline control. Nothing to model. */
    case A_VNOP: case A_VSYNC: case A_VFLUSH:
        fprintf(f, "%s;  /* %s */\n", ind, a_mnemonic(in->op)); return;
```

- [ ] **Step 5: Run the fork tests and commit**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure` → all pass.

```bash
cd third_party/psprecomp
git add src/vfpu_ops.c CMakeLists.txt include/psprecomp/vfpu.h tools/allegrexrecomp/emit.c \
        tests/test_vfpu_ops.c tests/test_emit_vfpu.c tests/CMakeLists.txt
git commit -m "vfpu: mfv, mtv, vnop, vsync, vflush"
```

---

### Task 11: `vrot`, `vcmov`, `vcrsp`, `vqmul`, `vsge`, `vslt`; exact quarter-turn sin/cos (fork)

Semantics:
- `vrot.{p,t,q} vd, vs, code`: angle = `vs` (single, quarter turns). `code` bits 4..0: bit 4 negates the sine, bits 3..2 are the sine lane, bits 1..0 the cosine lane. If the two lanes are equal, every lane gets the sine and then the cosine lane gets the cosine; otherwise all lanes are 0 except those two. (`vrot.q C000, S010, [-s,c,0,0]` → code `0x11`; `vrot.t …, [c,s,s]` → `0x00`.)
- `vcmov{t,f}.{s,p,t,q} vd, vs, cc`: code bits 3..0 = tf (bit 3, set for `vcmovf`) and cc (bits 2..0). cc 0–5: one `vfpu_cc` bit decides for all lanes; cc 6: lane i uses bit i; cc 7: nothing moves. `vcmovt` moves when the bit is 1, `vcmovf` when it is 0.
- `vcrsp.t`: cross product `[s1t2−s2t1, s2t0−s0t2, s0t1−s1t0]`. `vqmul.q`: quaternion product, `(x, y, z, w)` with `w` the real part: `[s0t3+s1t2−s2t1+s3t0, −s0t2+s1t3+s2t0+s3t1, s0t1−s1t0+s2t3+s3t2, −s0t0−s1t1−s2t2+s3t3]`.
- `vsge`: `s >= t ? 1 : 0`; `vslt`: `s < t ? 1 : 0`; a NaN operand gives 0 for both.
- `vsin`/`vcos`/`vnsin`/`vrot` are exact at whole quarter turns: 0, ±1 exactly, no `-4.37e-8` for `cos(1)`.

**Files:** `include/psprecomp/vfpu.h`, `src/vfpu_ops.c`, `src/vfpu.c` (sin/cos), `src/vfpu_internal.h` (`vfpu_sincos_q`), `tools/allegrexrecomp/emit.c`, `tests/test_vfpu_ops.c`, `tests/test_emit_vfpu.c`

- [ ] **Step 1: Write the failing tests**

In `test_vfpu_ops.c`, add before `main` and call from `main`:

```c
static void test_rot_cmov_crsp(void) {
    float o[4];
    const float one = 1.0f, half = 0.5f;

    set4(0x01u, 1, &one);                        /* S010 = 1 quarter turn */
    psp_vrot(0x00u, 0x01u, 0x11u, 4);            /* [-s, c, 0, 0] */
    get(0x00u, 4, o);
    CHECK(o[0] == -1.0f && o[1] == 0.0f && o[2] == 0.0f && o[3] == 0.0f,
          "vrot.q [-s,c,0,0] at 1: %g %g %g %g", o[0], o[1], o[2], o[3]);
    psp_vrot(0x00u, 0x01u, 0x00u, 3);            /* [c, s, s] */
    get(0x00u, 3, o);
    CHECK(o[0] == 0.0f && o[1] == 1.0f && o[2] == 1.0f, "vrot.t [c,s,s] at 1: %g %g %g", o[0], o[1], o[2]);
    set4(0x01u, 1, &half);
    psp_vrot(0x00u, 0x01u, 0x04u, 2);            /* [c, s] at 45 degrees */
    get(0x00u, 2, o);
    CHECK(fabsf(o[0] - 0.70710678f) < 1e-6f && fabsf(o[1] - 0.70710678f) < 1e-6f, "vrot.p at 0.5");

    float v;
    const float two = 2.0f, three = 3.0f, neg = -1.0f;
    set4(0x01u, 1, &two); psp_vunary(PSP_VU_COS, 0x02u, 0x01u, 1); get(0x02u, 1, &v);
    CHECK(v == -1.0f, "vcos(2) = -1 exactly, got %g", v);
    set4(0x01u, 1, &three); psp_vunary(PSP_VU_SIN, 0x02u, 0x01u, 1); get(0x02u, 1, &v);
    CHECK(v == -1.0f, "vsin(3) = -1 exactly, got %g", v);
    set4(0x01u, 1, &neg); psp_vunary(PSP_VU_SIN, 0x02u, 0x01u, 1); get(0x02u, 1, &v);
    CHECK(v == -1.0f, "vsin(-1) = -1, got %g", v);
    set4(0x01u, 1, &one); psp_vunary(PSP_VU_COS, 0x02u, 0x01u, 1); get(0x02u, 1, &v);
    CHECK(v == 0.0f, "vcos(1) = 0 exactly, got %g", v);

    /* vcmov: cc bit 2 set, bit 0 clear. */
    const float src[4] = { 1, 2, 3, 4 }, dst[4] = { 9, 9, 9, 9 };
    psp_cpu.vfpu_cc = 0x04u;
    set4(0x01u, 4, src); set4(0x00u, 4, dst);
    psp_vcmov(0x00u, 0x01u, 0x2u, 4);            /* vcmovt cc2: moves */
    get(0x00u, 4, o);
    CHECK(o[0] == 1 && o[3] == 4, "vcmovt on a set bit moves");
    set4(0x00u, 4, dst);
    psp_vcmov(0x00u, 0x01u, 0x0u, 4);            /* vcmovt cc0: does not */
    get(0x00u, 4, o);
    CHECK(o[0] == 9 && o[3] == 9, "vcmovt on a clear bit keeps");
    psp_vcmov(0x00u, 0x01u, 0x8u, 4);            /* vcmovf cc0: moves */
    get(0x00u, 4, o);
    CHECK(o[0] == 1, "vcmovf on a clear bit moves");
    set4(0x00u, 4, dst);
    psp_cpu.vfpu_cc = 0x05u;                     /* lanes 0 and 2 */
    psp_vcmov(0x00u, 0x01u, 0x6u, 4);            /* vcmovt per lane */
    get(0x00u, 4, o);
    CHECK(o[0] == 1 && o[1] == 9 && o[2] == 3 && o[3] == 9, "per-lane vcmovt: %g %g %g %g", o[0], o[1], o[2], o[3]);
    set4(0x00u, 4, dst);
    psp_vcmov(0x00u, 0x01u, 0x7u, 4);            /* cc 7: never */
    get(0x00u, 4, o);
    CHECK(o[0] == 9, "cc 7 never moves");

    /* vcrsp: x cross y = z. vqmul: i * j = k. */
    const float x[3] = { 1, 0, 0 }, y[3] = { 0, 1, 0 };
    set4(0x01u, 3, x); set4(0x02u, 3, y);
    psp_vcrsp(0x00u, 0x01u, 0x02u);
    get(0x00u, 3, o);
    CHECK(o[0] == 0 && o[1] == 0 && o[2] == 1, "x cross y = z: %g %g %g", o[0], o[1], o[2]);
    const float qi[4] = { 1, 0, 0, 0 }, qj[4] = { 0, 1, 0, 0 }, q1[4] = { 0, 0, 0, 1 };
    set4(0x01u, 4, qi); set4(0x02u, 4, qj);
    psp_vqmul(0x00u, 0x01u, 0x02u);
    get(0x00u, 4, o);
    CHECK(o[0] == 0 && o[1] == 0 && o[2] == 1 && o[3] == 0, "i*j = k: %g %g %g %g", o[0], o[1], o[2], o[3]);
    set4(0x02u, 4, q1);
    psp_vqmul(0x00u, 0x01u, 0x02u);
    get(0x00u, 4, o);
    CHECK(o[0] == 1 && o[3] == 0, "i*1 = i");

    /* vsge / vslt, with a NaN lane. */
    const float a[4] = { 1, 2, NAN, 4 }, b[4] = { 2, 2, 0, 3 };
    set4(0x01u, 4, a); set4(0x02u, 4, b);
    psp_vsge(0x00u, 0x01u, 0x02u, 4);
    get(0x00u, 4, o);
    CHECK(o[0] == 0 && o[1] == 1 && o[2] == 0 && o[3] == 1, "vsge: %g %g %g %g", o[0], o[1], o[2], o[3]);
    psp_vslt(0x00u, 0x01u, 0x02u, 4);
    get(0x00u, 4, o);
    CHECK(o[0] == 1 && o[1] == 0 && o[2] == 0 && o[3] == 0, "vslt: %g %g %g %g", o[0], o[1], o[2], o[3]);
}
```

In `test_emit_vfpu.c`'s `main`, add (words from `pspas`):

```c
    expect(0xF3B18180u, "psp_vrot(0, 1, 17, 4);");
    expect(0xD2AE8180u, "psp_vcmov(0, 1, 14, 4);");
    expect(0xF2828100u, "psp_vcrsp(0, 1, 2);");
    expect(0xF2828180u, "psp_vqmul(0, 1, 2);");
    expect(0x6F028180u, "psp_vsge(0, 1, 2, 4);");
    expect(0x6F828180u, "psp_vslt(0, 1, 2, 4);");
```

Check that the `vs`/`vt` numbers in those strings are what the decoder yields for the words (`C010` → 1, `C020` → 2); adjust the expected strings if the register fields decode differently, never the words.

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile (`psp_vrot` undeclared).

- [ ] **Step 3: Exact quarter-turn sin/cos**

In `vfpu_internal.h` declare `void vfpu_sincos_q(float a, float *s, float *c);`. In `vfpu.c`:

```c
/* Sine and cosine of an angle in quarter turns. Whole quarter turns are
 * exact (0, +-1), as on the PSP: rotation matrices built from vsin/vcos/vrot
 * must have real zeros in them. Otherwise computed in double. Matching the
 * PSP's last-bit results elsewhere is P2's oracle work. */
void vfpu_sincos_q(float a, float *s, float *c) {
    if (isnan(a) || isinf(a)) { *s = *c = NAN; return; }
    double x = fmod((double)a, 4.0);
    if (x < 0) x += 4.0;
    if (x == floor(x)) {
        static const float S[4] = { 0.0f, 1.0f, 0.0f, -1.0f };
        static const float C[4] = { 1.0f, 0.0f, -1.0f, 0.0f };
        const int k = (int)x & 3;
        *s = S[k];
        *c = C[k];
        return;
    }
    const double r = x * 1.5707963267948966;
    *s = (float)sin(r);
    *c = (float)cos(r);
}
```

In `psp_vunary` use it: `case PSP_VU_SIN: { float s_, c_; vfpu_sincos_q(a, &s_, &c_); r = s_; } break;`, and the same for `COS` (`r = c_`) and `NSIN` (`r = -s_`).

- [ ] **Step 4: The ops**

Declare in `vfpu.h`:

```c
/* vrot: lane code (bits 20..16) and width; see vfpu_ops.c. */
void psp_vrot(uint32_t vd, uint32_t vs, uint32_t code, int size);
/* vcmovt/vcmovf: code = tf (bit 3) | cc (bits 2..0). */
void psp_vcmov(uint32_t vd, uint32_t vs, uint32_t code, int size);
void psp_vcrsp(uint32_t vd, uint32_t vs, uint32_t vt);   /* .t */
void psp_vqmul(uint32_t vd, uint32_t vs, uint32_t vt);   /* .q */
void psp_vsge(uint32_t vd, uint32_t vs, uint32_t vt, int size);
void psp_vslt(uint32_t vd, uint32_t vs, uint32_t vt, int size);
```

Append to `src/vfpu_ops.c`:

```c
/* vrot: cosine into one lane, sine into another (or into every other lane
 * when both codes name the same lane), zeros elsewhere. code bit 4 negates
 * the sine. */
void psp_vrot(uint32_t vd, uint32_t vs, uint32_t code, int size) {
    float a[4], d[4], s, c;
    vfpu_read(PSP_VPFX_S, vs, 1, a);
    vfpu_sincos_q(a[0], &s, &c);
    if (code & 0x10) s = -s;
    const int sl = (int)(code >> 2) & 3, cl = (int)code & 3;
    for (int i = 0; i < size; i++) d[i] = (sl == cl) ? s : 0.0f;
    if (sl != cl && sl < size) d[sl] = s;
    if (cl < size) d[cl] = c;
    vfpu_write(vd, size, d);
    vfpu_end();
}

/* vcmovt (tf = 0) moves when the condition bit is 1, vcmovf when it is 0.
 * cc 0-5 is one vfpu_cc bit for all lanes, 6 is each lane's own bit, 7 never
 * moves. Lanes that do not move keep their value. */
void psp_vcmov(uint32_t vd, uint32_t vs, uint32_t code, int size) {
    const uint32_t tf = (code >> 3) & 1, cc = code & 7;
    float s[4], d[4];
    vfpu_read(PSP_VPFX_S, vs, size, s);
    vfpu_read_raw(vd, size, d);
    for (int i = 0; i < size && cc != 7; i++) {
        const uint32_t bit = (psp_cpu.vfpu_cc >> (cc < 6 ? cc : (uint32_t)i)) & 1;
        if (bit ^ tf) d[i] = s[i];
    }
    vfpu_write(vd, size, d);
    vfpu_end();
}

void psp_vcrsp(uint32_t vd, uint32_t vs, uint32_t vt) {
    float s[4], t[4], d[4];
    vfpu_read(PSP_VPFX_S, vs, 3, s);
    vfpu_read(PSP_VPFX_T, vt, 3, t);
    d[0] = s[1] * t[2] - s[2] * t[1];
    d[1] = s[2] * t[0] - s[0] * t[2];
    d[2] = s[0] * t[1] - s[1] * t[0];
    vfpu_write(vd, 3, d);
    vfpu_end();
}

/* Quaternions as (x, y, z, w), w the real part. */
void psp_vqmul(uint32_t vd, uint32_t vs, uint32_t vt) {
    float s[4], t[4], d[4];
    vfpu_read(PSP_VPFX_S, vs, 4, s);
    vfpu_read(PSP_VPFX_T, vt, 4, t);
    d[0] =  s[0] * t[3] + s[1] * t[2] - s[2] * t[1] + s[3] * t[0];
    d[1] = -s[0] * t[2] + s[1] * t[3] + s[2] * t[0] + s[3] * t[1];
    d[2] =  s[0] * t[1] - s[1] * t[0] + s[2] * t[3] + s[3] * t[2];
    d[3] = -s[0] * t[0] - s[1] * t[1] - s[2] * t[2] + s[3] * t[3];
    vfpu_write(vd, 4, d);
    vfpu_end();
}

static void compare_set(uint32_t vd, uint32_t vs, uint32_t vt, int size, int ge) {
    float s[4], t[4], d[4];
    vfpu_read(PSP_VPFX_S, vs, size, s);
    vfpu_read(PSP_VPFX_T, vt, size, t);
    for (int i = 0; i < size; i++)
        d[i] = (ge ? s[i] >= t[i] : s[i] < t[i]) ? 1.0f : 0.0f;   /* NaN: false, 0 */
    vfpu_write(vd, size, d);
    vfpu_end();
}
void psp_vsge(uint32_t vd, uint32_t vs, uint32_t vt, int size) { compare_set(vd, vs, vt, size, 1); }
void psp_vslt(uint32_t vd, uint32_t vs, uint32_t vt, int size) { compare_set(vd, vs, vt, size, 0); }
```

- [ ] **Step 5: Emitter**

In `emit_simple`, next to `A_VMMUL`:

```c
    case A_VROT:
        fprintf(f, "%spsp_vrot(%u, %u, %u, %u);\n", ind, in->vd, in->vs, in->vimm5, in->vsize); return;
    case A_VCMOV:
        fprintf(f, "%spsp_vcmov(%u, %u, %u, %u);\n", ind, in->vd, in->vs, in->vimm5 & 0xFu, in->vsize); return;
    case A_VCRSP:
        fprintf(f, "%spsp_vcrsp(%u, %u, %u);\n", ind, in->vd, in->vs, in->vt); return;
    case A_VQMUL:
        fprintf(f, "%spsp_vqmul(%u, %u, %u);\n", ind, in->vd, in->vs, in->vt); return;
    case A_VSGE:
        fprintf(f, "%spsp_vsge(%u, %u, %u, %u);\n", ind, in->vd, in->vs, in->vt, in->vsize); return;
    case A_VSLT:
        fprintf(f, "%spsp_vslt(%u, %u, %u, %u);\n", ind, in->vd, in->vs, in->vt, in->vsize); return;
```

- [ ] **Step 6: Run the fork tests and commit**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure` → all pass.

```bash
cd third_party/psprecomp
git add include/psprecomp/vfpu.h src/vfpu_ops.c src/vfpu.c src/vfpu_internal.h tools/allegrexrecomp/emit.c \
        tests/test_vfpu_ops.c tests/test_emit_vfpu.c
git commit -m "vfpu: vrot, vcmov, vcrsp, vqmul, vsge, vslt; exact sin/cos at whole quarter turns"
```

---

### Task 12: Float/int conversions with scale, and the packing family (fork)

**Files:** `include/psprecomp/recomp_rt.h` (`psp_cvt_w`), `include/psprecomp/vfpu.h`, `src/vfpu_ops.c`, `src/vfpu.c` (drop `PSP_VU_F2IZ`/`I2F`), `tools/allegrexrecomp/emit.c`, `tests/test_vfpu_ops.c`, `tests/test_emit_vfpu.c`

`psp_cvt_w` is shared with the FPU (Task 14). Rounding modes are MIPS FCR31's: 0 nearest-even, 1 toward zero, 2 toward +∞, 3 toward −∞. `vf2in`/`vf2iz`/`vf2iu`/`vf2id` are modes 0/1/2/3 after scaling by 2^scale. NaN and +∞ give `0x7FFFFFFF`, −∞ gives `0x80000000`, and out-of-range values saturate. `vi2f` divides by 2^scale.

Packing (VFPU7; lanes hold integer bits). `vi2uc.q`: each lane → `x < 0 ? 0 : x >> 23`, four bytes into one word. `vi2c.q`: `x >> 24` into bytes. `vi2us.{p,q}`: `x < 0 ? 0 : x >> 15`, two halfwords per word. `vi2s.{p,q}`: `x >> 16` into halfwords. `vuc2i.s`: byte i → `(b * 0x01010101) >> 1` into lane i of a quad. `vc2i.s`: byte i → `b << 24`. `vus2i.{s,p}`: halfword i → `h << 15`. `vs2i.{s,p}`: halfword i → `h << 16`. The source width is the instruction's; the destination width follows from it.

- [ ] **Step 1: Write the failing tests**

In `test_vfpu_ops.c`, add and call from `main`:

```c
static void set_bits(uint32_t vreg, int n, const uint32_t *b) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    for (int i = 0; i < n; i++) psp_cpu.v[r[i]] = psp_bits_to_f32(b[i]);
}
static void get_bits(uint32_t vreg, int n, uint32_t *b) {
    int r[4];
    psp_vfpu_regs(vreg, n, r);
    for (int i = 0; i < n; i++) b[i] = psp_f32_to_bits(psp_cpu.v[r[i]]);
}

static void test_conversions(void) {
    uint32_t o[4];
    const float f[4] = { 1.3f, -1.3f, 2.5f, -2.5f };
    set4(0x01u, 4, f);
    psp_vf2i(0, 0x00u, 0x01u, 0, 4);             /* vf2in: nearest-even */
    get_bits(0x00u, 4, o);
    CHECK(o[0] == 1 && (int32_t)o[1] == -1 && o[2] == 2 && (int32_t)o[3] == -2, "vf2in");
    psp_vf2i(1, 0x00u, 0x01u, 3, 4);             /* vf2iz, scale 3: trunc(x * 8) */
    get_bits(0x00u, 4, o);
    CHECK(o[0] == 10 && (int32_t)o[1] == -10 && o[2] == 20 && (int32_t)o[3] == -20, "vf2iz scale 3");
    psp_vf2i(2, 0x00u, 0x01u, 0, 2);             /* vf2iu: up */
    get_bits(0x00u, 2, o);
    CHECK(o[0] == 2 && (int32_t)o[1] == -1, "vf2iu");
    psp_vf2i(3, 0x00u, 0x01u, 0, 2);             /* vf2id: down */
    get_bits(0x00u, 2, o);
    CHECK(o[0] == 1 && (int32_t)o[1] == -2, "vf2id");
    const float big[4] = { 3e9f, -3e9f, NAN, INFINITY };
    set4(0x01u, 4, big);
    psp_vf2i(1, 0x00u, 0x01u, 0, 4);
    get_bits(0x00u, 4, o);
    CHECK(o[0] == 0x7FFFFFFFu && o[1] == 0x80000000u && o[2] == 0x7FFFFFFFu && o[3] == 0x7FFFFFFFu,
          "saturation: %08X %08X %08X %08X", o[0], o[1], o[2], o[3]);

    const uint32_t ints[2] = { 0x00010000u, 0xFFFF0000u };
    set_bits(0x01u, 2, ints);
    psp_vi2f(0x00u, 0x01u, 16, 2);               /* vi2f scale 16 */
    float g[2];
    get(0x00u, 2, g);
    CHECK(g[0] == 1.0f && g[1] == -1.0f, "vi2f scale 16: %g %g", g[0], g[1]);

    /* Packing round trips. */
    const uint32_t q[4] = { 0x7F800000u, 0x00800000u, 0x80000000u, 0x3FFFFFFFu };
    set_bits(0x01u, 4, q);
    psp_vpack(PSP_VP_I2UC, 0x00u, 0x01u, 4);
    get_bits(0x00u, 1, o);
    CHECK(o[0] == 0x7F0001FFu, "vi2uc: 0x%08X", o[0]);
    psp_vpack(PSP_VP_I2C, 0x00u, 0x01u, 4);
    get_bits(0x00u, 1, o);
    CHECK(o[0] == 0x3F80007Fu, "vi2c: 0x%08X", o[0]);

    const uint32_t packed = 0x80FF0001u;
    set_bits(0x01u, 1, &packed);
    psp_vpack(PSP_VP_UC2I, 0x00u, 0x01u, 1);
    get_bits(0x00u, 4, o);
    CHECK(o[0] == 0x00808080u && o[1] == 0 && o[2] == 0x7FFFFFFFu && o[3] == 0x40404040u,
          "vuc2i: %08X %08X %08X %08X", o[0], o[1], o[2], o[3]);
    psp_vpack(PSP_VP_C2I, 0x00u, 0x01u, 1);
    get_bits(0x00u, 4, o);
    CHECK(o[0] == 0x01000000u && o[2] == 0xFF000000u && o[3] == 0x80000000u, "vc2i");

    const uint32_t halves = 0x8000FFFFu;
    set_bits(0x01u, 1, &halves);
    psp_vpack(PSP_VP_S2I, 0x00u, 0x01u, 1);
    get_bits(0x00u, 2, o);
    CHECK(o[0] == 0xFFFF0000u && o[1] == 0x80000000u, "vs2i: %08X %08X", o[0], o[1]);
    psp_vpack(PSP_VP_US2I, 0x00u, 0x01u, 1);
    get_bits(0x00u, 2, o);
    CHECK(o[0] == 0x7FFF8000u && o[1] == 0x40000000u, "vus2i: %08X %08X", o[0], o[1]);

    const uint32_t two[2] = { 0x12345678u, 0xFEDCBA98u };
    set_bits(0x01u, 2, two);
    psp_vpack(PSP_VP_I2S, 0x00u, 0x01u, 2);
    get_bits(0x00u, 1, o);
    CHECK(o[0] == 0xFEDC1234u, "vi2s.p: 0x%08X", o[0]);
    psp_vpack(PSP_VP_I2US, 0x00u, 0x01u, 2);
    get_bits(0x00u, 1, o);
    CHECK(o[0] == 0x00002468u, "vi2us.p (negative lane clamps to 0): 0x%08X", o[0]);
}
```

In `test_emit_vfpu.c`'s `main`, add:

```c
    expect(0xD2038180u, "psp_vf2i(0, 0, 1, 3, 4);");    /* vf2in.q C000, C010, 3 */
    expect(0xD2208180u, "psp_vf2i(1, 0, 1, 0, 4);");    /* vf2iz.q */
    expect(0xD2410100u, "psp_vf2i(2, 0, 1, 1, 1);");    /* vf2iu.s S000, S010, 1 */
    expect(0xD2620100u, "psp_vf2i(3, 0, 1, 2, 1);");    /* vf2id.s, 2 */
    expect(0xD2908180u, "psp_vi2f(0, 1, 16, 4);");      /* vi2f.q, 16 */
    expect(0xD03C8180u, "psp_vpack(PSP_VP_I2UC, 0, 1, 4);");
    expect(0xD0380100u, "psp_vpack(PSP_VP_UC2I, 0, 1, 1);");
    expect(0xD03B0100u, "psp_vpack(PSP_VP_S2I, 0, 1, 1);");
    expect(0xD03F0180u, "psp_vpack(PSP_VP_I2S, 0, 1, 2);");
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile.

- [ ] **Step 3: `psp_cvt_w`**

In `include/psprecomp/recomp_rt.h`, add `#include <math.h>` next to `<stdint.h>`, and after `psp_fcmp`:

```c
/* Float to int32 as the PSP converts: rounding mode `rm` uses the MIPS FCR31
 * encoding (0 nearest-even, 1 toward zero, 2 toward +inf, 3 toward -inf).
 * NaN and +inf give 0x7FFFFFFF, -inf gives 0x80000000, and finite values
 * beyond int32 saturate. (Our reading of the hardware; P2's oracle checks
 * it.) Written without fenv, so the host's rounding mode does not matter,
 * and without a C float-to-int cast of an out-of-range value, which is
 * undefined behaviour. Takes a double so callers can pre-scale exactly. */
static inline int32_t psp_cvt_w(double x, int rm) {
    if (x != x) return INT32_MAX;
    double r;
    switch (rm & 3) {
    case 1:  r = x < 0 ? ceil(x) : floor(x); break;
    case 2:  r = ceil(x);  break;
    case 3:  r = floor(x); break;
    default: {
        r = floor(x);
        const double frac = x - r;
        if (frac > 0.5 || (frac == 0.5 && fmod(r, 2.0) != 0.0)) r += 1.0;
        break;
    }
    }
    if (r >= 2147483647.0) return INT32_MAX;
    if (r <= -2147483648.0) return INT32_MIN;
    return (int32_t)r;
}
```

- [ ] **Step 4: VFPU conversions and packing**

Declare in `vfpu.h`:

```c
/* vf2in/vf2iz/vf2iu/vf2id (rm 0/1/2/3, see psp_cvt_w) after scaling by
 * 2^scale; vi2f divides by 2^scale. */
void psp_vf2i(int rm, uint32_t vd, uint32_t vs, uint32_t scale, int size);
void psp_vi2f(uint32_t vd, uint32_t vs, uint32_t scale, int size);

/* VFPU7 integer narrowing and widening. `size` is the source width. */
enum { PSP_VP_I2UC, PSP_VP_I2C, PSP_VP_I2US, PSP_VP_I2S,
       PSP_VP_UC2I, PSP_VP_C2I, PSP_VP_US2I, PSP_VP_S2I };
void psp_vpack(int op, uint32_t vd, uint32_t vs, int size);
```

Append to `src/vfpu_ops.c`:

```c
void psp_vf2i(int rm, uint32_t vd, uint32_t vs, uint32_t scale, int size) {
    float s[4];
    uint32_t d[4];
    vfpu_read(PSP_VPFX_S, vs, size, s);
    for (int i = 0; i < size; i++)
        d[i] = (uint32_t)psp_cvt_w(ldexp((double)s[i], (int)scale), rm);   /* exact in double */
    vfpu_write_bits(vd, size, d);
    vfpu_end();
}

void psp_vi2f(uint32_t vd, uint32_t vs, uint32_t scale, int size) {
    float s[4], d[4];
    vfpu_read(PSP_VPFX_S, vs, size, s);
    for (int i = 0; i < size; i++)
        d[i] = (float)ldexp((double)(int32_t)psp_f32_to_bits(s[i]), -(int)scale);
    vfpu_write(vd, size, d);
    vfpu_end();
}

void psp_vpack(int op, uint32_t vd, uint32_t vs, int size) {
    float sf[4];
    uint32_t s[4], d[4] = { 0, 0, 0, 0 };
    int dn;
    vfpu_read(PSP_VPFX_S, vs, size, sf);
    for (int i = 0; i < size; i++) s[i] = psp_f32_to_bits(sf[i]);
    switch (op) {
    case PSP_VP_I2UC:
        if (size != 4) goto bad;
        dn = 1;
        for (int i = 0; i < 4; i++)
            d[0] |= ((int32_t)s[i] < 0 ? 0u : (s[i] >> 23) & 0xFFu) << (8 * i);
        break;
    case PSP_VP_I2C:
        if (size != 4) goto bad;
        dn = 1;
        for (int i = 0; i < 4; i++) d[0] |= ((s[i] >> 24) & 0xFFu) << (8 * i);
        break;
    case PSP_VP_I2US:
        if (size != 2 && size != 4) goto bad;
        dn = size / 2;
        for (int i = 0; i < size; i++)
            d[i / 2] |= ((int32_t)s[i] < 0 ? 0u : (s[i] >> 15) & 0xFFFFu) << (16 * (i & 1));
        break;
    case PSP_VP_I2S:
        if (size != 2 && size != 4) goto bad;
        dn = size / 2;
        for (int i = 0; i < size; i++) d[i / 2] |= ((s[i] >> 16) & 0xFFFFu) << (16 * (i & 1));
        break;
    case PSP_VP_UC2I:
        if (size != 1) goto bad;
        dn = 4;
        for (int i = 0; i < 4; i++) d[i] = (((s[0] >> (8 * i)) & 0xFFu) * 0x01010101u) >> 1;
        break;
    case PSP_VP_C2I:
        if (size != 1) goto bad;
        dn = 4;
        for (int i = 0; i < 4; i++) d[i] = ((s[0] >> (8 * i)) & 0xFFu) << 24;
        break;
    case PSP_VP_US2I:
        if (size != 1 && size != 2) goto bad;
        dn = size * 2;
        for (int i = 0; i < dn; i++) d[i] = ((s[i / 2] >> (16 * (i & 1))) & 0xFFFFu) << 15;
        break;
    case PSP_VP_S2I:
        if (size != 1 && size != 2) goto bad;
        dn = size * 2;
        for (int i = 0; i < dn; i++) d[i] = ((s[i / 2] >> (16 * (i & 1))) & 0xFFFFu) << 16;
        break;
    default:
        goto bad;
    }
    vfpu_write_bits(vd, dn, d);
    vfpu_end();
    return;
bad:
    vfpu_end();
    vfpu_trap("vpack width");
}
```

In `src/vfpu.c`'s `psp_vunary`, delete the `PSP_VU_F2IZ` and `PSP_VU_I2F` cases (they ignored the scale). Keep the enum values in `vfpu.h` but mark them "unused, see psp_vf2i/psp_vi2f" so the numbering of the rest does not move; if `tests/test_vfpu.c` uses them, port those checks to `psp_vf2i(1, …, 0, …)` / `psp_vi2f(…, 0, …)`.

- [ ] **Step 5: Emitter**

Remove `A_VF2IZ` and `A_VI2F` from the VFPU4 unary `case` list and its table `U[]`. Add:

```c
    /* Conversions with a scale exponent (bits 20..16). The four f2i forms
     * are consecutive in a_op and map to rounding modes 0..3 in order. */
    case A_VF2IN: case A_VF2IZ: case A_VF2IU: case A_VF2ID:
        fprintf(f, "%spsp_vf2i(%d, %u, %u, %u, %u);\n", ind, (int)(in->op - A_VF2IN),
                in->vd, in->vs, in->vimm5, in->vsize); return;
    case A_VI2F:
        fprintf(f, "%spsp_vi2f(%u, %u, %u, %u);\n", ind, in->vd, in->vs, in->vimm5, in->vsize); return;
    case A_VI2UC: case A_VI2C: case A_VI2US: case A_VI2S:
    case A_VUC2I: case A_VC2I: case A_VUS2I: case A_VS2I: {
        static const struct { a_op op; const char *sel; } P[] = {
            { A_VI2UC, "PSP_VP_I2UC" }, { A_VI2C, "PSP_VP_I2C" },
            { A_VI2US, "PSP_VP_I2US" }, { A_VI2S, "PSP_VP_I2S" },
            { A_VUC2I, "PSP_VP_UC2I" }, { A_VC2I, "PSP_VP_C2I" },
            { A_VUS2I, "PSP_VP_US2I" }, { A_VS2I, "PSP_VP_S2I" },
        };
        for (size_t k = 0; k < sizeof P / sizeof P[0]; k++)
            if (P[k].op == in->op) {
                fprintf(f, "%spsp_vpack(%s, %u, %u, %u);\n", ind, P[k].sel, in->vd, in->vs, in->vsize);
                return;
            }
        break;
    }
```

Add a static assertion next to it so a reordered enum cannot silently swap rounding modes:

```c
_Static_assert(A_VF2IZ == A_VF2IN + 1 && A_VF2IU == A_VF2IN + 2 && A_VF2ID == A_VF2IN + 3,
               "psp_vf2i's rounding mode is the op's offset from A_VF2IN");
```

- [ ] **Step 6: Run the fork tests and commit**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure` → all pass.

```bash
cd third_party/psprecomp
git add include/psprecomp/recomp_rt.h include/psprecomp/vfpu.h src/vfpu_ops.c src/vfpu.c \
        tools/allegrexrecomp/emit.c tests/test_vfpu_ops.c tests/test_emit_vfpu.c tests/test_vfpu.c
git commit -m "vfpu: vf2in/z/u/d and vi2f honour the scale; vi2uc/vi2c/vi2us/vi2s/vuc2i/vc2i/vus2i/vs2i"
```

---

### Task 13: VFPU9 lane ops and the random-number ops (fork)

Semantics, n = source width:
- `vfad`: one lane, the sum of the n lanes, left to right. `vavg`: that sum / n.
- `vocp`: `1 − s[i]`. `vsocp.s` → pair `[sat0(1−s0), sat0(s0)]`; `vsocp.p` → quad `[sat0(1−s0), sat0(s0), sat0(1−s1), sat0(s1)]`.
- `vbfy1.p` `[s0+s1, s0−s1]`, `.q` adds `[s2+s3, s2−s3]`. `vbfy2.q` `[s0+s2, s1+s3, s0−s2, s1−s3]`.
- `vsrt1.q` `[min(s0,s1), max(s0,s1), min(s2,s3), max(s2,s3)]`; `vsrt2.q` `[min(s0,s3), min(s1,s2), max(s1,s2), max(s0,s3)]`; `vsrt3.q` `[max(s0,s1), min(s0,s1), max(s2,s3), min(s2,s3)]`; `vsrt4.q` `[max(s0,s3), max(s1,s2), min(s1,s2), min(s0,s3)]`.
- `vsgn`: `−1`, `0` or `1` by sign (NaN → 0).
- `vrnds.s vs` seeds the generator with the bits of `vs`. `vrndi` writes random int bits, `vrndf1` floats in [1, 2), `vrndf2` in [2, 4). The generator is xorshift32, deterministic, reset by `psp_vfpu_reset` to a fixed seed (see Decisions).

`vsbn`, `vf2h`, `vh2f`, `vsbz`, `vlgb`, `vt4444`/`vt5551`/`vt5650`, `vmfvc`/`vmtvc` and `vwbn` do not occur in `.text`. They stay named and trapping; Task 16 implements any that the census shows.

**Files:** `include/psprecomp/vfpu.h`, `src/vfpu_ops.c`, `src/vfpu.c` (`psp_vfpu_reset`), `tools/allegrexrecomp/emit.c`, `tests/test_vfpu_ops.c`, `tests/test_emit_vfpu.c`

- [ ] **Step 1: Write the failing tests**

In `test_vfpu_ops.c`, add and call from `main`:

```c
static void test_lane_ops(void) {
    float o[4];
    const float s[4] = { 1, 4, 2, 8 };
    set4(0x01u, 4, s);
    psp_vlane(PSP_VL_FAD, 0x00u, 0x01u, 4);  get(0x00u, 1, o);  CHECK(o[0] == 15, "vfad %g", o[0]);
    psp_vlane(PSP_VL_AVG, 0x00u, 0x01u, 4);  get(0x00u, 1, o);  CHECK(o[0] == 3.75f, "vavg %g", o[0]);
    psp_vlane(PSP_VL_OCP, 0x00u, 0x01u, 4);  get(0x00u, 4, o);
    CHECK(o[0] == 0 && o[1] == -3 && o[2] == -1 && o[3] == -7, "vocp");
    psp_vlane(PSP_VL_BFY1, 0x00u, 0x01u, 4); get(0x00u, 4, o);
    CHECK(o[0] == 5 && o[1] == -3 && o[2] == 10 && o[3] == -6, "vbfy1.q");
    psp_vlane(PSP_VL_BFY2, 0x00u, 0x01u, 4); get(0x00u, 4, o);
    CHECK(o[0] == 3 && o[1] == 12 && o[2] == -1 && o[3] == -4, "vbfy2.q");
    psp_vlane(PSP_VL_SRT1, 0x00u, 0x01u, 4); get(0x00u, 4, o);
    CHECK(o[0] == 1 && o[1] == 4 && o[2] == 2 && o[3] == 8, "vsrt1");
    psp_vlane(PSP_VL_SRT2, 0x00u, 0x01u, 4); get(0x00u, 4, o);
    CHECK(o[0] == 1 && o[1] == 2 && o[2] == 4 && o[3] == 8, "vsrt2");
    psp_vlane(PSP_VL_SRT3, 0x00u, 0x01u, 4); get(0x00u, 4, o);
    CHECK(o[0] == 4 && o[1] == 1 && o[2] == 8 && o[3] == 2, "vsrt3");
    psp_vlane(PSP_VL_SRT4, 0x00u, 0x01u, 4); get(0x00u, 4, o);
    CHECK(o[0] == 8 && o[1] == 4 && o[2] == 2 && o[3] == 1, "vsrt4");
    const float g[4] = { -2, 0, 3, NAN };
    set4(0x01u, 4, g);
    psp_vlane(PSP_VL_SGN, 0x00u, 0x01u, 4);  get(0x00u, 4, o);
    CHECK(o[0] == -1 && o[1] == 0 && o[2] == 1 && o[3] == 0, "vsgn");
    const float h = 0.25f;
    set4(0x01u, 1, &h);
    psp_vlane(PSP_VL_SOCP, 0x00u, 0x01u, 1); get(0x00u, 2, o);
    CHECK(o[0] == 0.75f && o[1] == 0.25f, "vsocp.s");

    /* Random: deterministic after reset or reseed, and in range. */
    psp_vfpu_reset();
    psp_vrnd(PSP_VR_F1, 0x00u, 4); get(0x00u, 4, o);
    float first[4];
    memcpy(first, o, sizeof o);
    for (int i = 0; i < 4; i++) CHECK(o[i] >= 1.0f && o[i] < 2.0f, "vrndf1 in [1,2): %g", o[i]);
    psp_vfpu_reset();
    psp_vrnd(PSP_VR_F1, 0x00u, 4); get(0x00u, 4, o);
    CHECK(memcmp(o, first, sizeof o) == 0, "same sequence after reset");
    psp_vrnd(PSP_VR_F2, 0x00u, 4); get(0x00u, 4, o);
    for (int i = 0; i < 4; i++) CHECK(o[i] >= 2.0f && o[i] < 4.0f, "vrndf2 in [2,4): %g", o[i]);
    const uint32_t seed = 0x12345678u;
    set_bits(0x01u, 1, &seed);
    psp_vrnds(0x01u);
    uint32_t a[4], b[4];
    psp_vrnd(PSP_VR_I, 0x00u, 4); get_bits(0x00u, 4, a);
    psp_vrnds(0x01u);
    psp_vrnd(PSP_VR_I, 0x00u, 4); get_bits(0x00u, 4, b);
    CHECK(memcmp(a, b, sizeof a) == 0 && a[0] != a[1], "vrnds reseeds");
}
```

In `test_emit_vfpu.c`'s `main`, add:

```c
    expect(0xD0468180u, "psp_vlane(PSP_VL_FAD, 0, 1, 4);");    /* vfad.q S000, C010 */
    expect(0xD0448180u, "psp_vlane(PSP_VL_OCP, 0, 1, 4);");
    expect(0xD0428180u, "psp_vlane(PSP_VL_BFY1, 0, 1, 4);");
    expect(0xD0220000u, "psp_vrnd(PSP_VR_F1, 0, 1);");         /* vrndf1.s S000 */
    expect(0xD0238080u, "psp_vrnd(PSP_VR_F2, 0, 4);");         /* vrndf2.q C000 */
    expect(0x63C02000u, "psp_vdiv(0, 32, 64, 1);");             /* vdiv.s S000, S001, S002 */
```

All words are from `pspas`. `S001` and `S002` are register fields `0x20` and `0x40` (row bits 6..5), which is why `vdiv.s` prints `32, 64`.

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: FAIL to compile.

- [ ] **Step 3: Runtime**

Declare in `vfpu.h`:

```c
/* VFPU9 lane ops. `size` is the source width. */
enum { PSP_VL_FAD, PSP_VL_AVG, PSP_VL_OCP, PSP_VL_SOCP, PSP_VL_BFY1, PSP_VL_BFY2,
       PSP_VL_SRT1, PSP_VL_SRT2, PSP_VL_SRT3, PSP_VL_SRT4, PSP_VL_SGN };
void psp_vlane(int op, uint32_t vd, uint32_t vs, int size);

/* Random numbers: a deterministic xorshift32, not the PSP's generator. */
enum { PSP_VR_I, PSP_VR_F1, PSP_VR_F2 };
void psp_vrnds(uint32_t vs);
void psp_vrnd(int kind, uint32_t vd, int size);
```

Append to `src/vfpu_ops.c`:

```c
static float lmin(float a, float b) { return a < b ? a : b; }
static float lmax(float a, float b) { return a > b ? a : b; }
static float lsat0(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

void psp_vlane(int op, uint32_t vd, uint32_t vs, int size) {
    float s[4], d[4] = { 0, 0, 0, 0 };
    int dn = size;
    vfpu_read(PSP_VPFX_S, vs, size, s);
    switch (op) {
    case PSP_VL_FAD: case PSP_VL_AVG: {
        float sum = 0.0f;
        for (int i = 0; i < size; i++) sum += s[i];
        d[0] = op == PSP_VL_AVG ? sum / (float)size : sum;
        dn = 1;
        break;
    }
    case PSP_VL_OCP:
        for (int i = 0; i < size; i++) d[i] = 1.0f - s[i];
        break;
    case PSP_VL_SOCP:
        if (size > 2) goto bad;
        dn = size * 2;
        for (int i = 0; i < size; i++) { d[2 * i] = lsat0(1.0f - s[i]); d[2 * i + 1] = lsat0(s[i]); }
        break;
    case PSP_VL_BFY1:
        if (size != 2 && size != 4) goto bad;
        for (int i = 0; i < size; i += 2) { d[i] = s[i] + s[i + 1]; d[i + 1] = s[i] - s[i + 1]; }
        break;
    case PSP_VL_BFY2:
        if (size != 4) goto bad;
        d[0] = s[0] + s[2]; d[1] = s[1] + s[3]; d[2] = s[0] - s[2]; d[3] = s[1] - s[3];
        break;
    case PSP_VL_SRT1:
        if (size != 4) goto bad;
        d[0] = lmin(s[0], s[1]); d[1] = lmax(s[0], s[1]); d[2] = lmin(s[2], s[3]); d[3] = lmax(s[2], s[3]);
        break;
    case PSP_VL_SRT2:
        if (size != 4) goto bad;
        d[0] = lmin(s[0], s[3]); d[1] = lmin(s[1], s[2]); d[2] = lmax(s[1], s[2]); d[3] = lmax(s[0], s[3]);
        break;
    case PSP_VL_SRT3:
        if (size != 4) goto bad;
        d[0] = lmax(s[0], s[1]); d[1] = lmin(s[0], s[1]); d[2] = lmax(s[2], s[3]); d[3] = lmin(s[2], s[3]);
        break;
    case PSP_VL_SRT4:
        if (size != 4) goto bad;
        d[0] = lmax(s[0], s[3]); d[1] = lmax(s[1], s[2]); d[2] = lmin(s[1], s[2]); d[3] = lmin(s[0], s[3]);
        break;
    case PSP_VL_SGN:
        for (int i = 0; i < size; i++) d[i] = s[i] > 0.0f ? 1.0f : s[i] < 0.0f ? -1.0f : 0.0f;
        break;
    default:
        goto bad;
    }
    vfpu_write(vd, dn, d);
    vfpu_end();
    return;
bad:
    vfpu_end();
    vfpu_trap("vlane width");
}

#define RND_SEED 0x2F6B4C1Du
static uint32_t g_rnd = RND_SEED;

void vfpu_rnd_reset(void) { g_rnd = RND_SEED; }

static uint32_t rnd_next(void) {
    uint32_t x = g_rnd;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return g_rnd = x;
}

void psp_vrnds(uint32_t vs) {
    float s[4];
    vfpu_read_raw(vs, 1, s);
    const uint32_t b = psp_f32_to_bits(s[0]);
    g_rnd = b ? b : RND_SEED;                 /* xorshift must not start at 0 */
    vfpu_end();
}

void psp_vrnd(int kind, uint32_t vd, int size) {
    uint32_t d[4];
    for (int i = 0; i < size; i++) {
        const uint32_t r = rnd_next();
        d[i] = kind == PSP_VR_F1 ? (0x3F800000u | (r >> 9))      /* [1, 2) */
             : kind == PSP_VR_F2 ? (0x40000000u | (r >> 9))      /* [2, 4) */
             : r;
    }
    vfpu_write_bits(vd, size, d);
    vfpu_end();
}
```

Declare `void vfpu_rnd_reset(void);` in `vfpu_internal.h` and call it from `psp_vfpu_reset` in `vfpu.c`.

- [ ] **Step 4: Emitter**

```c
    case A_VFAD: case A_VAVG: case A_VOCP: case A_VSOCP: case A_VBFY1: case A_VBFY2:
    case A_VSRT1: case A_VSRT2: case A_VSRT3: case A_VSRT4: case A_VSGN: {
        static const struct { a_op op; const char *sel; } L[] = {
            { A_VFAD, "PSP_VL_FAD" },   { A_VAVG, "PSP_VL_AVG" },   { A_VOCP, "PSP_VL_OCP" },
            { A_VSOCP, "PSP_VL_SOCP" }, { A_VBFY1, "PSP_VL_BFY1" }, { A_VBFY2, "PSP_VL_BFY2" },
            { A_VSRT1, "PSP_VL_SRT1" }, { A_VSRT2, "PSP_VL_SRT2" }, { A_VSRT3, "PSP_VL_SRT3" },
            { A_VSRT4, "PSP_VL_SRT4" }, { A_VSGN, "PSP_VL_SGN" },
        };
        for (size_t k = 0; k < sizeof L / sizeof L[0]; k++)
            if (L[k].op == in->op) {
                fprintf(f, "%spsp_vlane(%s, %u, %u, %u);\n", ind, L[k].sel, in->vd, in->vs, in->vsize);
                return;
            }
        break;
    }
    case A_VRNDS:
        fprintf(f, "%spsp_vrnds(%u);\n", ind, in->vs); return;
    case A_VRNDI:
        fprintf(f, "%spsp_vrnd(PSP_VR_I, %u, %u);\n", ind, in->vd, in->vsize); return;
    case A_VRNDF1:
        fprintf(f, "%spsp_vrnd(PSP_VR_F1, %u, %u);\n", ind, in->vd, in->vsize); return;
    case A_VRNDF2:
        fprintf(f, "%spsp_vrnd(PSP_VR_F2, %u, %u);\n", ind, in->vd, in->vsize); return;
```

Check `A_VDIV`'s existing emitter case is reached now (it is, since Task 8 decodes it); the `expect` line above confirms it.

- [ ] **Step 5: Run the fork tests and commit**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure` → all pass.

```bash
cd third_party/psprecomp
git add include/psprecomp/vfpu.h src/vfpu_ops.c src/vfpu.c src/vfpu_internal.h tools/allegrexrecomp/emit.c \
        tests/test_vfpu_ops.c tests/test_emit_vfpu.c
git commit -m "vfpu: VFPU9 lane ops (vfad, vavg, vocp, vsocp, vbfy, vsrt, vsgn) and deterministic vrnd*"
```

---

### Task 14: FPU `round`/`trunc`/`ceil`/`floor`/`cvt.w.s` (fork)

**Files:** `tools/allegrexrecomp/emit.c`, Create: `tests/test_fpu_cvt.c`; Modify: `tests/test_emit_vfpu.c`, `tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing tests**

`third_party/psprecomp/tests/test_fpu_cvt.c`:

```c
/* psp_cvt_w: the FPU's float-to-int conversions. Synthetic values only. */
#include "psprecomp/recomp_rt.h"

#include <math.h>
#include <stdio.h>

static int failures;
static void eq(double x, int rm, int32_t want, const char *what) {
    int32_t got = psp_cvt_w(x, rm);
    if (got != want) { printf("FAIL %s: cvt(%.9g, %d) = %d, want %d\n", what, x, rm, got, want); failures++; }
}

int main(void) {
    eq(2.5, 0, 2, "round half to even (down)");
    eq(3.5, 0, 4, "round half to even (up)");
    eq(-2.5, 0, -2, "round -2.5");
    eq(-0.5, 0, 0, "round -0.5");
    eq(0.49999997f, 0, 0, "round just below a half");
    eq(2.7, 1, 2, "trunc");
    eq(-2.7, 1, -2, "trunc negative");
    eq(2.1, 2, 3, "ceil");
    eq(-2.7, 2, -2, "ceil negative");
    eq(-2.1, 3, -3, "floor negative");
    eq(2147483520.0f, 1, 2147483520, "largest float below 2^31");
    eq(2147483648.0f, 1, INT32_MAX, "2^31 saturates");
    eq(-2147483648.0f, 1, INT32_MIN, "-2^31 is exact");
    eq(1e10, 0, INT32_MAX, "big positive");
    eq(-1e10, 0, INT32_MIN, "big negative");
    eq(NAN, 0, INT32_MAX, "NaN");
    eq(INFINITY, 3, INT32_MAX, "+inf");
    eq(-INFINITY, 2, INT32_MIN, "-inf");
    printf("%s\n", failures ? "test_fpu_cvt: FAILED" : "test_fpu_cvt: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_fpu_cvt test_fpu_cvt.c)
target_link_libraries(test_fpu_cvt PRIVATE psprecomp m)
add_test(NAME fpu_cvt COMMAND test_fpu_cvt)
```

(Drop `m` if the existing tests do not link it explicitly and macOS builds fine without it; keep it if Linux CI needs it.)

In `test_emit_vfpu.c`'s `main`, add (words from `pspas`, `$f0, $f2`):

```c
    expect(0x4600100Cu, "psp_cpu.f[0] = psp_bits_to_f32((uint32_t)psp_cvt_w(psp_cpu.f[2], 0));");  /* round.w.s */
    expect(0x4600100Du, "psp_cvt_w(psp_cpu.f[2], 1)");     /* trunc.w.s */
    expect(0x4600100Eu, "psp_cvt_w(psp_cpu.f[2], 2)");     /* ceil.w.s */
    expect(0x4600100Fu, "psp_cvt_w(psp_cpu.f[2], 3)");     /* floor.w.s */
    expect(0x46001024u, "psp_cvt_w(psp_cpu.f[2], (int)(psp_cpu.fcr31 & 3))");   /* cvt.w.s */
```

(`0x46001024` is `cvt.w.s $f0, $f2`: funct `0x24`; confirm with `pspas`.)

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build -R 'fpu_cvt|emit_vfpu' --output-on-failure`
Expected: `fpu_cvt` passes already (Task 12 added `psp_cvt_w`); `emit_vfpu` FAILS on the round/ceil/floor cases.

- [ ] **Step 3: Emitter**

Replace the `case A_CVT_W_S: case A_TRUNC_W_S:` block with:

```c
    /* Float to word. Rounding mode as in FCR31 (0 nearest, 1 zero, 2 up,
     * 3 down); cvt.w.s uses the current mode. psp_cvt_w saturates the way
     * the PSP does and never casts an out-of-range float, which C leaves
     * undefined. */
    case A_ROUND_W_S: case A_TRUNC_W_S: case A_CEIL_W_S: case A_FLOOR_W_S: case A_CVT_W_S: {
        const char *rm = in->op == A_ROUND_W_S ? "0" : in->op == A_TRUNC_W_S ? "1"
                       : in->op == A_CEIL_W_S  ? "2" : in->op == A_FLOOR_W_S ? "3"
                       : "(int)(psp_cpu.fcr31 & 3)";
        fprintf(f, "%spsp_cpu.f[%u] = psp_bits_to_f32((uint32_t)psp_cvt_w(psp_cpu.f[%u], %s));\n",
                ind, in->fd, in->fs, rm);
        return;
    }
```

- [ ] **Step 4: Run the fork tests and commit**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure` → all pass.

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/emit.c tests/test_fpu_cvt.c tests/test_emit_vfpu.c tests/CMakeLists.txt
git commit -m "emit: round/trunc/ceil/floor/cvt.w.s through psp_cvt_w (PSP saturation, no UB)"
```

---

### Task 15: `break` stops the run as a guest break (fork + runtime)

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.c` (`A_BREAK`, the generated header's declarations)
- Modify: `third_party/psprecomp/tests/test_emit_vfpu.c`
- Modify: `runtime/include/khbbs/boot.h`, `runtime/boot.c`, `runtime/include/khbbs/unimplemented.h`, `hosts/pc/main.c`, `tests/runtime/test_boot.c`

- [ ] **Step 1: Write the failing tests**

In `test_emit_vfpu.c`'s `main`, add:

```c
    expect(0x0001C00Du, "psp_break(0x08804000u, 0x00700u);");   /* break 0x700 */
```

(`break` is SPECIAL funct `0x0D` with the code in bits 25..6: `0x700 << 6 | 0x0D` = `0x0001C00D`.)

In `tests/runtime/test_boot.c`, next to the other synthetic functions:

```c
static void asserts(void) { psp_break(0x08804500u, 7); }
```

register it with `psp_register(0x08804500u, asserts);`, include `"khbbs/unimplemented.h"`, and add before the long-path check:

```c
    img.entry = 0x08804500u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_GUEST_BREAK && r.addr == 0x08804500u && r.code == 7, "guest break stops the run");
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build -R emit_vfpu --output-on-failure`
Expected: FAIL (`psp_unimplemented(…, "break")`). `cmake --build build/pc` also fails (`KHBBS_GUEST_BREAK`, `psp_break` undeclared).

- [ ] **Step 3: Emitter**

Replace the `A_BREAK` case with:

```c
    case A_BREAK:
        /* The game's own trap (asserts). Not a translation gap: the host
         * decides what a guest break means. */
        fprintf(f, "%spsp_break(0x%08Xu, 0x%05Xu);\n", ind, in->addr, (in->raw >> 6) & 0xFFFFF); return;
```

In `emit_header`, after the `psp_unimplemented` declaration, add `"void psp_break(uint32_t addr, uint32_t code);\n"` and mention it in the "Provided by the host" comment.

- [ ] **Step 4: Runtime**

In `runtime/include/khbbs/unimplemented.h`, declare:

```c
/* The guest executed `break` (an assert in the game). During khbbs_boot this
 * stops the run with KHBBS_GUEST_BREAK; outside it, it aborts like
 * psp_unimplemented. Defined in boot.c, which owns the stop. */
void psp_break(uint32_t addr, uint32_t code);
```

In `boot.h`, add `KHBBS_GUEST_BREAK` to `khbbs_stop` (comment: "the game executed `break`, usually a failed assert") and `uint32_t code;  /* the break code, for KHBBS_GUEST_BREAK */` to `khbbs_result`, and say in the `addr` comment that it is also the break's address.

In `boot.c`, `#include "khbbs/unimplemented.h"`, add `static int g_running;`, set `g_running = 1;` right before `if (setjmp(g_stop) == 0)` and `g_running = 0;` right after the `if` block, and add:

```c
void psp_break(uint32_t addr, uint32_t code) {
    fflush(stdout);
    fprintf(stderr, "khbbs: guest break at 0x%08X (code 0x%05X)\n", addr, code);
    if (!g_running) {
        psp_miss_context();
        khbbs_trace_flush();
        abort();
    }
    g_res.why = KHBBS_GUEST_BREAK;
    g_res.addr = addr;
    g_res.code = code;
    longjmp(g_stop, 1);
}
```

(add `#include <stdlib.h>`). In `khbbs_stop_name` add `case KHBBS_GUEST_BREAK: return "guest break (game assertion)";`. In `hosts/pc/main.c`, print ` at 0x%08X (code 0x%05X)` for `KHBBS_GUEST_BREAK`, and call `psp_miss_context()` for it as for a miss.

- [ ] **Step 5: Run everything and commit both repos**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure && cmake --build build/pc && ctest --test-dir build/pc --output-on-failure`
Expected: all pass.

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/emit.c tests/test_emit_vfpu.c
git commit -m "emit: break is psp_break(addr, code), a host-defined guest trap"
cd ../..
tools/check_no_assets.sh
git add runtime/include/khbbs/boot.h runtime/boot.c runtime/include/khbbs/unimplemented.h hosts/pc/main.c tests/runtime/test_boot.c
git commit -m "runtime: guest break stops khbbs with its address and code"
```

---

### Task 16: Publish the fork, bump the pin, drive the census to zero

**Files:** `tools/setup_toolchain.sh:74`, `docs/setup.md:28`; fork files as the census requires.

- [ ] **Step 1: Push the fork and bump the pin**

```bash
git -C third_party/psprecomp push origin khbbs
NEW=$(git -C third_party/psprecomp rev-parse --short=7 HEAD)
sed -i '' "s/psprecomp b925bd2/psprecomp $NEW/" tools/setup_toolchain.sh
sed -i '' "s/| \`b925bd2\` |/| \`$NEW\` |/" docs/setup.md
git diff tools/setup_toolchain.sh docs/setup.md
```

Expected: one changed line in each file, both naming `$NEW`.

- [ ] **Step 2: Clean build and census**

```bash
rm -rf build/pc
cmake -S . -B build/pc -G Ninja && cmake --build build/pc && ctest --test-dir build/pc --output-on-failure
python3 tools/unimpl_census.py build/pc/gen
```

Expected: build and tests pass. Record the census. Discovery now reaches more code than at the baseline, so new mnemonics can appear.

- [ ] **Step 3: Implement whatever is left, one mnemonic per commit**

For each mnemonic the census still lists, most sites first:
1. Get its encoding from `pspas` (Context) and, with `python3 tools/unimpl_census.py build/pc/gen --sites`, one real site; disassemble around it with `third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp dis game/EBOOT.dec.ELF <addr-0x10> 8`.
2. If it decodes as `vfpu?`: add the op to `decode.h`/`decode.c` and a `dec(...)` line to `test_decode_vfpu.c`.
3. Write the runtime test first (in `test_vfpu_ops.c`, hand-computed values from the documented semantics, with a comment naming the source and marking anything unverified for P2), watch it fail, implement in `src/vfpu_ops.c` through `vfpu_read`/`vfpu_write`/`vfpu_end`, then add an `expect(...)` line to `test_emit_vfpu.c` and the emitter case.
4. Run the fork tests, commit in the fork (`vfpu: <op>`), rebuild `build/pc`, re-run the census.

Stop when the census prints `unimplemented 0` and `unhandled branches 0` and exits 0.

- [ ] **Step 4: If Step 3 added fork commits, push and bump again**

Repeat Step 1 with `b925bd2` replaced by the previous pin.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add tools/setup_toolchain.sh docs/setup.md
git commit -m "Pin psprecomp $NEW (P3: discovery seeds, switch tables, VFPU/FPU ops)"
```

---

### Task 17: Zero dispatch misses on the boot path

The boot path is everything `khbbs` runs before it stops for a reason that is not a dispatch miss or an untranslated instruction (a budget, an unimplemented firmware call that ends the run, a guest break, or the alarm).

- [ ] **Step 1: Run and collect misses**

```bash
rm -f build/misses.jsonl
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF \
  --trace build/trace-p3.jsonl --budget 50000000 --miss-log build/misses.jsonl --keep-going 1000
wc -l build/misses.jsonl
```

- [ ] **Step 2: Look at every miss before merging it**

For each distinct `target`: disassemble the target and its caller (`allegrexrecomp dis game/EBOOT.dec.ELF <target> 8` and `… <ra-0x20> 12`). Decide which it is:
- a function reached through a pointer (a store of a `lui`/`addiu` value into a struct, a vtable slot, a callback): merge it;
- a wild jump (target not in `.text`, or in the middle of an instruction stream that does not start a function): do not merge; find the bad value's source instead, since it is a bug elsewhere (a stepped-over earlier miss, an HLE return value, a VFPU result).

Misses after the first stepped-over one may be consequences of it; deal with the first ones first.

- [ ] **Step 3: Merge, rebuild, repeat**

```bash
python3 tools/merge_misses.py build/misses.jsonl --elf game/EBOOT.dec.ELF
git diff bbs.toml
cmake --build build/pc
```

Edit each new `bbs.toml` line's comment to say what the pointer is (e.g. `# thread entry, passed to sceKernelCreateThread at 0x08A9…`). Go back to Step 1 until a run with `--keep-going 0` stops for a reason other than a dispatch miss and logs no misses.

- [ ] **Step 4: Confirm the gates**

```bash
python3 tools/unimpl_census.py build/pc/gen; echo "census rc=$?"
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF \
  --trace build/trace-p3.jsonl --budget 50000000 --miss-log build/misses-final.jsonl
test ! -s build/misses-final.jsonl && echo "no misses"
```

Expected: `census rc=0`, `no misses`, and a stop reason other than a dispatch miss or an untranslated instruction. Record the stop, the dispatch count and the number of traced firmware calls.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add bbs.toml
git commit -m "bbs.toml: entries for pointer-only functions on the boot path"
```

(Skip the commit if no entries were needed.)

---

### Task 18: Bring-up log, roadmap, next-session prompt, PR

- [ ] **Step 1: Append to `docs/bringup.md`**

A new `## P3, recompiler fidelity (2026-10-18)` entry in the P1 entry's style: fork pin and build time; the exact command; discovery before/after (seeds by source, functions, `.text` reached, tables resolved, computed jumps left); census before/after; where the run stops now, after how many dispatches and traced calls; the first firmware calls past crt0 that matter; what each `bbs.toml` entry is; and what this means for P2/P4.

- [ ] **Step 2: Update `docs/plan.md`**

Link P3's row to this plan (as P1's is), and adjust "Done when" if the decisions above changed it (the added gates: unhandled branches, VFPU traps).

- [ ] **Step 3: Rewrite `docs/next-session.md`**

For the next session: where we are (P3 merged or in review, the new stop), the known issues carried forward, and the next step (P2 execution using `docs/superpowers/plans/2026-10-18-p2-oracle-tooling.md`).

- [ ] **Step 4: Commit and open the PR**

```bash
tools/check_no_assets.sh
git add docs/bringup.md docs/plan.md docs/next-session.md docs/superpowers/plans/
git commit -m "docs: P3 bring-up results, roadmap link, next-session prompt"
git push -u origin p3-recompiler-fidelity
gh pr create --base main --title "P3: recompiler fidelity" --body-file build/pr-body.md
```

Write `build/pr-body.md` first (it stays out of git). It lists: what changed in the fork (with the pin), the gates and their results, the silent bugs fixed, the new stop, and how to reproduce (`tools/setup_toolchain.sh psprecomp`, the build, the census, the run), and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
