# P1: Build Skeleton and First Boot, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CMake build that recompiles the user's `game/EBOOT.dec.ELF` into C under `build/`, links it with the runtime and runs it as a PC executable, `khbbs`. `khbbs` boots the game, writes every firmware call to a JSON-lines trace, and reports where and why it stopped.

**Architecture:** Our psprecomp fork (a gitignored clone in `third_party/psprecomp`, see `docs/setup.md`) gets four small changes: `vfim` literals, `.text`-only heuristic seeds, split output files and an HLE trace hook. This repo adds a platform-free `runtime/` (ELF loader, boot, trace writer) and `hosts/pc/main.c`, and the top-level CMake ties them together. There is no window, threading or graphics yet. Those are P4–P7 in `docs/plan.md`.

**Tech Stack:** C11, CMake ≥ 3.16 + Ninja, AppleClang 16, psprecomp `caca759` (fork), Python 3.14 (only for existing tools).

**Context an implementer needs:**
- Read `docs/architecture.md` (sections Memory, Build layout) and `docs/phase0-report.md` §2.
- Game files are never committed. `game/` and `build/` are gitignored. Run `tools/check_no_assets.sh` before every commit.
- Work on branch `p1-build-skeleton` in this repo. Fork changes go on branch `khbbs` of `github.com/ShayVD/psprecomp`, committed inside `third_party/psprecomp`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

| File | Responsibility |
|---|---|
| `third_party/psprecomp/tools/allegrexrecomp/emit.c` | (fork) `vfim` literal fix, `--split` output |
| `third_party/psprecomp/tools/allegrexrecomp/emit.h` | (fork) `emit_opts.split` |
| `third_party/psprecomp/tools/allegrexrecomp/main.c` | (fork) `.text`-only heuristic seeds, `--split N` flag |
| `third_party/psprecomp/src/hle/hle.c`, `include/psprecomp/hle.h` | (fork) `psp_hle_set_trace()` hook, remove leftover debug print |
| `third_party/psprecomp/tests/test_emit_vfim.c`, `test_emit_split.c`, `test_hle_trace.c` | (fork) tests for the above |
| `CMakeLists.txt` | top level: options, psprecomp, runtime, generated code, host |
| `cmake/gen.cmake` | runs `allegrexrecomp emit` into `build/<host>/gen/` |
| `runtime/CMakeLists.txt` | `khbbs_runtime` static library |
| `runtime/include/khbbs/loader.h`, `runtime/loader.c` | load an ELF's `PT_LOAD` segments into guest RAM, read entry and `$gp` |
| `runtime/include/khbbs/boot.h`, `runtime/boot.c` | set up registers, run the entry point, catch stop conditions |
| `runtime/include/khbbs/trace.h`, `runtime/trace.c` | JSON-lines HLE trace writer |
| `tests/runtime/test_loader.c`, `test_trace.c`, `tests/runtime/CMakeLists.txt` | runtime unit tests, synthetic data only |
| `hosts/pc/CMakeLists.txt`, `hosts/pc/main.c` | `khbbs` command-line host |
| `docs/bringup.md` | log of what `khbbs` does on the real game, run by run |
| `.github/workflows/checks.yml` | add a job building and testing the runtime (no game) |

---

### Task 1: Point the psprecomp clone at our fork

Needs the user's go-ahead first: it creates a public fork on their GitHub account.

**Files:** none in this repo yet (Task 6 updates the pins).

- [ ] **Step 1: Create the fork and add it as `origin`**

```bash
cd third_party/psprecomp
gh repo fork sp00nznet/psprecomp --clone=false --remote=false
git remote rename origin upstream
git remote add origin https://github.com/ShayVD/psprecomp.git
git fetch -q origin
git checkout -b khbbs caca759
git push -u origin khbbs
```

Expected: `git -C third_party/psprecomp remote -v` lists `origin` (ShayVD) and `upstream` (sp00nznet), and the branch is `khbbs` at `caca759`.

- [ ] **Step 2: Confirm the upstream tests still pass on the branch**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build`
Expected: `100% tests passed, 0 tests failed out of 10`

---

### Task 2: Emit `vfim` immediates as valid C (fork)

`vfim` loads a half-float. The emitter prints it with `%.9gf`, which gives `-1f` for integral values, and that is not a C literal. Phase 0 found two such sites (`0x08AF98F0`, `0x08AF994C`).

**Files:**
- Create: `third_party/psprecomp/tests/test_emit_vfim.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt` (append)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.c:438-441`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_emit_vfim.c`:

```c
/* vfim with an integral value must still emit a valid C float expression.
 * Synthetic code only, no game data. */
#include "analyze.h"
#include "emit.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BASE 0x08804000u

/* vfim.s S000, 0xBC00 (half -1.0) ; jr $ra ; nop */
static const uint32_t CODE[] = { 0xDF80BC00u, 0x03E00008u, 0x00000000u };

int main(void) {
    uint8_t code[sizeof CODE];
    for (size_t i = 0; i < sizeof CODE / 4; i++)
        for (int b = 0; b < 4; b++) code[i * 4 + b] = (uint8_t)(CODE[i] >> (8 * b));

    a_analysis an;
    memset(&an, 0, sizeof an);
    an.code = code;
    an.base = BASE;
    an.size = (uint32_t)sizeof code;
    uint32_t seed = BASE;
    if (a_discover(&an, &seed, 1) != 0 || an.nfuncs != 1) { printf("FAIL discovery\n"); return 1; }

    emit_opts o;
    memset(&o, 0, sizeof o);
    o.outdir = ".";
    o.prefix = "t_vfim";
    o.module = "synthetic";
    if (a_emit(&an, &o) != 0) { printf("FAIL emit\n"); return 1; }

    FILE *f = fopen("./t_vfim_funcs.c", "rb");
    if (!f) { printf("FAIL open\n"); return 1; }
    static char src[1 << 16];
    src[fread(src, 1, sizeof src - 1, f)] = '\0';
    fclose(f);

    int failures = 0;
    if (!strstr(src, "psp_vimm(0, (float)-1);")) {
        printf("FAIL expected psp_vimm(0, (float)-1);\n");
        failures++;
    }
    if (strstr(src, "-1f)")) { printf("FAIL invalid literal -1f\n"); failures++; }
    printf("%s\n", failures ? "test_emit_vfim: FAILED" : "test_emit_vfim: ok");
    return failures ? 1 : 0;
}
```

Append to `third_party/psprecomp/tests/CMakeLists.txt`:

```cmake
add_executable(test_emit_vfim test_emit_vfim.c)
target_link_libraries(test_emit_vfim PRIVATE allegrex_core)
add_test(NAME emit_vfim COMMAND test_emit_vfim)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build -R emit_vfim --output-on-failure`
Expected: FAIL with `expected psp_vimm(0, (float)-1);`

- [ ] **Step 3: Fix the emitter**

In `emit.c`, replace

```c
    case A_VFIM:
        fprintf(f, "%spsp_vimm(%u, %.9gf);\n", ind, in->vd,
                (double)half_to_float((uint16_t)in->imm));
        return;
```

with

```c
    case A_VFIM:
        /* A cast, not an `f` suffix: "%.9g" prints integral values without a
         * decimal point, and "-1f" is not a C literal. */
        fprintf(f, "%spsp_vimm(%u, (float)%.9g);\n", ind, in->vd,
                (double)half_to_float((uint16_t)in->imm));
        return;
```

- [ ] **Step 4: Run all fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build`
Expected: `100% tests passed` (11 tests)

- [ ] **Step 5: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tests/test_emit_vfim.c tests/CMakeLists.txt tools/allegrexrecomp/emit.c
git commit -m "emit: print vfim immediates as (float) casts so integral values compile"
```

---

### Task 3: Keep heuristic pointer seeds inside `.text` (fork)

For a fixed-address `ET_EXEC` module, discovery seeds from "anything in the data that looks like a pointer into the image". That image is the whole segment, so `.rodata`/`.data` words become function seeds. Phase 0 counted 2,370 false functions this way, including the one with a cross-function `goto` that broke compilation.

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/main.c` (inside `load_and_discover`, the `if (nptr == 0 && e->nsegments)` block)

- [ ] **Step 1: Record the current baseline on the real game**

Run:
```bash
third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp funcs game/EBOOT.dec.ELF --list \
  | python3 -c "import sys; print(sum(1 for l in sys.stdin if l.startswith('0x') and not 0x08804018 <= int(l.split()[0], 16) < 0x08B2FE38))"
```
Expected: `2370`

- [ ] **Step 2: Filter the scanned seeds**

In `main.c`, directly after

```c
                nptr = a_scan_data_pointers(an, b->data + data_off, region_len,
                                            seeds + nseeds, avail);
                if (nptr > avail) nptr = avail;
```

insert

```c
                /* A heuristic pointer is only trusted as a *code* seed if it
                 * lands in .text (or .init, which crt0 reaches by address).
                 * Pointers into .rodata/.data are strings, tables and
                 * vtables-of-data, and seeding them produced thousands of
                 * "functions" made of data. */
                int kept = 0;
                for (int i = 0; i < nptr; i++) {
                    uint32_t p = seeds[nseeds + i];
                    int in_text = p >= e->text_addr && p < e->text_addr + e->text_size;
                    int is_init = p == e->seg[0].addr;
                    if (in_text || is_init) seeds[nseeds + kept++] = p;
                }
                nptr = kept;
```

- [ ] **Step 3: Rebuild and re-measure**

Run the command from Step 1 again.
Expected: `0` functions outside `.text`/`.init`. Also run `allegrexrecomp funcs game/EBOOT.dec.ELF | head -12` and note the new `functions:` count and `reached:` percentage. Expected: about 24,800 functions, `reached` close to 90%. Write both numbers in the commit message.

- [ ] **Step 4: Run the fork tests**

Run: `ctest --test-dir third_party/psprecomp/build`
Expected: all pass.

- [ ] **Step 5: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/main.c
git commit -m "funcs: only seed heuristic data pointers that land in .text

On KH BBS (ULUS10505) this removes 2,370 functions discovered in .rodata/.data
(<new count> functions remain, <pct>% of .text reached)."
```

---

### Task 4: Split the generated C into N files (fork)

One 84 MB translation unit takes 7 minutes at `-O0`. `--split N` writes `<prefix>_funcs_000.c` … `<prefix>_funcs_<N-1>.c` with contiguous slices of the functions, plus `<prefix>_register.c`. N is fixed so the build system knows the outputs in advance. Every file is written even when it holds no functions.

**Files:**
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.h` (`emit_opts`)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.c` (`a_emit`, the "Functions" and "Registration" parts)
- Modify: `third_party/psprecomp/tools/allegrexrecomp/main.c` (`cmd_emit`, `main`)
- Modify: `third_party/psprecomp/tests/test_emit.c` (zero `emit_opts`)
- Create: `third_party/psprecomp/tests/test_emit_split.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_emit_split.c`:

```c
/* --split: three tiny functions into two files plus a registration file.
 * Synthetic code only, no game data. */
#include "analyze.h"
#include "emit.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define BASE 0x08804000u

/* f0: jal f1 ; nop ; jr ra ; nop     f1: jal f2 ; nop ; jr ra ; nop     f2: jr ra ; nop */
static const uint32_t CODE[] = {
    0x0C000000u | ((BASE + 16) >> 2 & 0x3FFFFFF), 0, 0x03E00008u, 0,
    0x0C000000u | ((BASE + 32) >> 2 & 0x3FFFFFF), 0, 0x03E00008u, 0,
    0x03E00008u, 0,
};

static int failures;

static char *slurp(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    static char buf[4][1 << 16];
    static int k;
    char *b = buf[k++ & 3];
    b[fread(b, 1, (1 << 16) - 1, f)] = '\0';
    fclose(f);
    return b;
}

static void expect(int cond, const char *what) {
    if (!cond) { printf("FAIL %s\n", what); failures++; }
}

int main(void) {
    uint8_t code[sizeof CODE];
    for (size_t i = 0; i < sizeof CODE / 4; i++)
        for (int b = 0; b < 4; b++) code[i * 4 + b] = (uint8_t)(CODE[i] >> (8 * b));

    a_analysis an;
    memset(&an, 0, sizeof an);
    an.code = code;
    an.base = BASE;
    an.size = (uint32_t)sizeof code;
    uint32_t seed = BASE;
    expect(a_discover(&an, &seed, 1) == 0 && an.nfuncs == 3, "three functions discovered");

    emit_opts o;
    memset(&o, 0, sizeof o);
    o.outdir = ".";
    o.prefix = "t_split";
    o.module = "synthetic";
    o.split = 2;
    expect(a_emit(&an, &o) == 0, "emit succeeds");

    char *p0 = slurp("./t_split_funcs_000.c");
    char *p1 = slurp("./t_split_funcs_001.c");
    char *reg = slurp("./t_split_register.c");
    expect(p0 && p1 && reg, "all three files exist");
    if (p0 && p1 && reg) {
        expect(strstr(p0, "void psp_func_08804000(void)") != NULL, "f0 in part 0");
        expect(strstr(p0, "void psp_func_08804010(void)") != NULL, "f1 in part 0");
        expect(strstr(p1, "void psp_func_08804020(void)") != NULL, "f2 in part 1");
        expect(strstr(p1, "void psp_func_08804000(void) {") == NULL, "f0 not repeated in part 1");
        expect(strstr(reg, "void psp_recomp_register(void)") != NULL, "register function");
        expect(strstr(reg, "psp_register(0x08804020u, psp_func_08804020);") != NULL, "f2 registered");
        expect(strstr(p0, "psp_recomp_register") == NULL, "no registration in parts");
    }
    printf("%s\n", failures ? "test_emit_split: FAILED" : "test_emit_split: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_emit_split test_emit_split.c)
target_link_libraries(test_emit_split PRIVATE allegrex_core)
add_test(NAME emit_split COMMAND test_emit_split)
```

In `tests/test_emit.c`, replace `    emit_opts o;` with

```c
    emit_opts o;
    memset(&o, 0, sizeof o);
```

- [ ] **Step 2: Run it and watch it fail to compile**

Run: `cmake --build third_party/psprecomp/build`
Expected: error `no member named 'split' in 'emit_opts'`

- [ ] **Step 3: Add the option**

In `emit.h`, add to `emit_opts` after `int nimports;`:

```c
    /* 0 or 1: one <prefix>_funcs.c with registration at the end (default).
     * N > 1: <prefix>_funcs_000.c .. <prefix>_funcs_<N-1>.c, each a
     * contiguous slice of the functions, plus <prefix>_register.c. All N
     * files are always written, so a build system can list them up front. */
    int split;
```

- [ ] **Step 4: Implement the split in `a_emit`**

In `emit.c`, replace everything from the line `    /* Functions */` through the `fclose(f);` that follows `free(c.entries);` with:

```c
    /* Functions */
    const int nparts = o->split > 1 ? o->split : 1;
    const int per = an->nfuncs ? (an->nfuncs + nparts - 1) / nparts : 0;

    ectx c;
    c.an = an;
    c.is_label = (uint8_t *)calloc(an->nwords ? an->nwords : 1, 1);
    c.is_slot  = (uint8_t *)calloc(an->nwords ? an->nwords : 1, 1);
    c.entries = NULL;
    c.nentries = c.centries = 0;
    if (!c.is_label || !c.is_slot) {
        free(c.is_label); free(c.is_slot);
        return -1;
    }

    FILE *f = NULL;
    for (int part = 0; part < nparts; part++) {
        if (nparts == 1)
            snprintf(path, sizeof path, "%s/%s_funcs.c", o->outdir, o->prefix);
        else
            snprintf(path, sizeof path, "%s/%s_funcs_%03d.c", o->outdir, o->prefix, part);
        f = fopen(path, "w");
        if (!f) {
            fprintf(stderr, "cannot write %s\n", path);
            free(c.is_label); free(c.is_slot); free(c.entries);
            return -1;
        }
        fprintf(f,
            "/* Generated by allegrexrecomp -- do not edit.\n"
            " *\n"
            " * Module: %s   --   %d functions, %llu instructions (part %d of %d).\n"
            " */\n"
            "#include \"%s_funcs.h\"\n",
            o->module ? o->module : "(unknown)", an->nfuncs,
            (unsigned long long)an->insns, part + 1, nparts, o->prefix);
        c.out = f;
        int lo = part * per, hi = lo + per;
        if (hi > an->nfuncs) hi = an->nfuncs;
        for (int i = lo; i < hi; i++) {
            c.func = &an->funcs[i];
            emit_function(&c, &an->funcs[i]);
        }
        if (part != nparts - 1 || nparts > 1) { fclose(f); f = NULL; }
    }

    /* Registration: appended to the single file, or its own file when split. */
    if (nparts > 1) {
        snprintf(path, sizeof path, "%s/%s_register.c", o->outdir, o->prefix);
        f = fopen(path, "w");
        if (!f) {
            fprintf(stderr, "cannot write %s\n", path);
            free(c.is_label); free(c.is_slot); free(c.entries);
            return -1;
        }
        fprintf(f, "/* Generated by allegrexrecomp -- do not edit. */\n"
                   "#include \"%s_funcs.h\"\n\n", o->prefix);
        /* Interior-label entry points live in the part files; declare them here. */
        for (int i = 0; i < c.nentries; i++)
            fprintf(f, "void psp_at_%08X(void);\n", c.entries[i]);
    }
    fprintf(f,
        "\n/* ---------------------------------------------------------------\n"
        " * Populate the dispatch table. Indirect calls -- function pointers,\n"
        " * vtables, callbacks, switch tables — resolve through this.\n"
        " * ------------------------------------------------------------- */\n"
        "void psp_recomp_register(void) {\n");
    for (int i = 0; i < an->nfuncs; i++)
        fprintf(f, "    psp_register(0x%08Xu, psp_func_%08X);\n",
                an->funcs[i].addr, an->funcs[i].addr);
    /* Interior labels last, so a real function entry always wins a collision. */
    for (int i = 0; i < c.nentries; i++)
        fprintf(f, "    psp_register_label(0x%08Xu, psp_at_%08X);\n",
                c.entries[i], c.entries[i]);
    fprintf(f, "}\n");

    free(c.is_label);
    free(c.is_slot);
    free(c.entries);
    fclose(f);
```

Then make the final `printf("wrote %s/%s_funcs.c ...` line report the part count:

```c
    if (nparts > 1)
        printf("wrote %s/%s_funcs_000..%03d.c + %s_register.c   (%d functions, %d interior entries)\n",
               o->outdir, o->prefix, nparts - 1, o->prefix, an->nfuncs, c.nentries);
    else
        printf("wrote %s/%s_funcs.c   (%d functions, %d interior entries)\n",
               o->outdir, o->prefix, an->nfuncs, c.nentries);
```

(This replaces the existing first `printf("wrote %s/%s_funcs.c ...`. `c.nentries` is still valid after `free(c.entries)`, because only the array is freed.)

- [ ] **Step 5: Add `--split N` to the CLI**

In `main.c`, change the signature `static int cmd_emit(const char *path, const char *outdir, const char *prefix)` to

```c
static int cmd_emit(const char *path, const char *outdir, const char *prefix, int split)
```

and after `o.nimports = nimp;` add `o.split = split;`. In `main`, replace the `emit` branch with:

```c
    if (!strcmp(cmd, "emit")) {
        if (argc < 4) return usage();
        int split = 0, npos = argc;
        for (int i = 4; i < argc; i++)
            if (!strcmp(argv[i], "--split") && i + 1 < argc) { split = atoi(argv[i + 1]); npos = i; break; }
        return cmd_emit(argv[2], argv[3], npos > 4 ? argv[4] : NULL, split);
    }
```

and in `usage()` change the emit line to `allegrexrecomp emit    <file> <outdir> [prefix] [--split N]`.

- [ ] **Step 6: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build`
Expected: `100% tests passed` (12 tests)

- [ ] **Step 7: Check it on the game**

Run:
```bash
mkdir -p build/gen-check
third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp emit game/EBOOT.dec.ELF build/gen-check main --split 64 | tail -3
ls build/gen-check | wc -l
```
Expected: `wrote build/gen-check/main_funcs_000..063.c + main_register.c`; 67 files (64 parts + register + imports + header).

- [ ] **Step 8: Commit (in the fork)**

```bash
cd third_party/psprecomp
git add tools/allegrexrecomp/emit.c tools/allegrexrecomp/emit.h tools/allegrexrecomp/main.c \
        tests/test_emit.c tests/test_emit_split.c tests/CMakeLists.txt
git commit -m "emit: --split N writes N part files plus a registration file"
```

---

### Task 5: An HLE trace hook (fork)

The oracle diff (P2) needs every firmware call with its arguments, return value and caller. This adds a single optional callback in `psp_hle_call`, and removes a leftover debug `fprintf` for NID `0x237DBD4F`.

**Files:**
- Modify: `third_party/psprecomp/include/psprecomp/hle.h`
- Modify: `third_party/psprecomp/src/hle/hle.c` (`psp_hle_call`)
- Create: `third_party/psprecomp/tests/test_hle_trace.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_hle_trace.c`:

```c
/* The trace hook sees name, arguments, return value and $ra for registered
 * and unregistered NIDs alike. Synthetic, no game data. */
#include "psprecomp/hle.h"
#include "psprecomp/cpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static struct { uint32_t nid; const char *name; uint32_t a0, t3, ret, ra; int n; } seen;

static void hook(uint32_t nid, const char *name, const uint32_t args[8], uint32_t ret, uint32_t ra) {
    seen.nid = nid; seen.name = name; seen.a0 = args[0]; seen.t3 = args[7];
    seen.ret = ret; seen.ra = ra; seen.n++;
}

static void fake(void) { psp_ret(psp_arg(0) + 1); }

int main(void) {
    psp_cpu_reset();
    psp_hle_register(0x109F50BCu, "IoFileMgrForUser", "sceIoOpen", fake);
    psp_hle_set_trace(hook);

    psp_cpu.r[PSP_REG_A0] = 41;
    psp_cpu.r[PSP_REG_T3] = 0x1234;
    psp_cpu.r[PSP_REG_RA] = 0x08804100u;
    psp_hle_call(0x109F50BCu);
    CHECK(seen.n == 1, "hook called once");
    CHECK(seen.nid == 0x109F50BCu, "nid");
    CHECK(seen.name && !strcmp(seen.name, "sceIoOpen"), "name");
    CHECK(seen.a0 == 41 && seen.t3 == 0x1234, "args captured before the call");
    CHECK(seen.ret == 42, "return value");
    CHECK(seen.ra == 0x08804100u, "caller $ra");

    psp_hle_call(0xDEADBEEFu);
    CHECK(seen.n == 2 && seen.name == NULL && seen.ret == 0, "unregistered NID traced with NULL name");

    psp_hle_set_trace(NULL);
    psp_hle_call(0x109F50BCu);
    CHECK(seen.n == 2, "hook removed");

    printf("%s\n", failures ? "test_hle_trace: FAILED" : "test_hle_trace: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_hle_trace test_hle_trace.c)
target_link_libraries(test_hle_trace PRIVATE psprecomp)
add_test(NAME hle_trace COMMAND test_hle_trace)
```

- [ ] **Step 2: Run it and watch it fail to compile**

Run: `cmake --build third_party/psprecomp/build`
Expected: error `implicit declaration of function 'psp_hle_set_trace'`

- [ ] **Step 3: Declare the hook**

In `hle.h`, after `void psp_hle_call(uint32_t nid);` add:

```c
/* Optional observer for every firmware call, for oracle traces. `args` are
 * $a0-$a3 and $t0-$t3 as they were on entry; `ret` is $v0 afterwards; `ra`
 * is the caller's return address. `name` is NULL for an unregistered NID.
 * Pass NULL to remove. */
typedef void (*psp_hle_trace_fn)(uint32_t nid, const char *name,
                                 const uint32_t args[8], uint32_t ret, uint32_t ra);
void psp_hle_set_trace(psp_hle_trace_fn fn);
```

- [ ] **Step 4: Implement it**

In `hle.c`, replace the whole `void psp_hle_call(uint32_t nid) { ... }` function with:

```c
static psp_hle_trace_fn g_trace;

void psp_hle_set_trace(psp_hle_trace_fn fn) { g_trace = fn; }

void psp_hle_call(uint32_t nid) {
    uint32_t args[8], ra = psp_cpu.r[PSP_REG_RA];
    if (g_trace) {
        for (int i = 0; i < 4; i++) args[i] = psp_cpu.r[PSP_REG_A0 + i];
        for (int i = 0; i < 4; i++) args[4 + i] = psp_cpu.r[PSP_REG_T0 + i];
    }
    for (int i = 0; i < g_count; i++) {
        if (g_entry[i].nid == nid) {
            g_fn[i]();
            if (psp_cpu.r[PSP_REG_V0] == 0) note_zero(nid, g_entry[i].name);
            if (g_trace) g_trace(nid, g_entry[i].name, args, psp_cpu.r[PSP_REG_V0], ra);
            return;
        }
    }
    note_zero(nid, NULL);

    /* Unimplemented. Naming the function is the whole point — bringing a game
     * up is largely the process of watching this message stop appearing.
     * Returning 0 rather than aborting is deliberate: many firmware calls are
     * advisory, and one that genuinely needed the result will fail visibly
     * soon after, with this line already in the log. */
    fprintf(stderr, "psprecomp: unimplemented firmware call 0x%08X\n", nid);
    psp_ret(0);
    if (g_trace) g_trace(nid, NULL, args, 0, ra);
}
```

- [ ] **Step 5: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build`
Expected: `100% tests passed` (13 tests)

- [ ] **Step 6: Commit and push the fork**

```bash
cd third_party/psprecomp
git add include/psprecomp/hle.h src/hle/hle.c tests/test_hle_trace.c tests/CMakeLists.txt
git commit -m "hle: optional trace hook for every firmware call; drop leftover debug print"
git push origin khbbs
git rev-parse --short HEAD
```

Note the printed commit hash for Task 6.

---

### Task 6: Pin the fork in this repo

**Files:**
- Modify: `tools/setup_toolchain.sh` (the `clones()` function)
- Modify: `docs/setup.md` (third-party table)

- [ ] **Step 1: Point the setup script at the fork**

In `tools/setup_toolchain.sh`, replace

```bash
  clone https://github.com/sp00nznet/psprecomp psprecomp caca759
```

with (using the hash from Task 5 Step 6 in place of `<hash>`):

```bash
  # Our fork (branch khbbs) of github.com/sp00nznet/psprecomp, based on caca759.
  clone https://github.com/ShayVD/psprecomp psprecomp <hash>
```

- [ ] **Step 2: Update the docs table**

In `docs/setup.md`, change the psprecomp row to:

```markdown
| `third_party/psprecomp` | github.com/ShayVD/psprecomp, branch `khbbs` (fork of sp00nznet/psprecomp `caca759`) | `<hash>` | MIT | recompiler (`allegrexrecomp`) and runtime base |
```

and delete the paragraph starting "When we start changing psprecomp (plan P1)".

- [ ] **Step 3: Verify a clean re-run**

Run: `tools/setup_toolchain.sh clones`
Expected: `ok: third_party/psprecomp at <hash>`, `ok: psprecomp built`

- [ ] **Step 4: Commit (this repo, branch `p1-build-skeleton`)**

```bash
git checkout -b p1-build-skeleton
tools/check_no_assets.sh
git add tools/setup_toolchain.sh docs/setup.md
git commit -m "Pin psprecomp to our fork (khbbs branch)"
```

---

### Task 7: Top-level CMake and the runtime library with an ELF loader

**Files:**
- Create: `CMakeLists.txt`, `runtime/CMakeLists.txt`, `tests/runtime/CMakeLists.txt`
- Create: `runtime/include/khbbs/loader.h`, `runtime/loader.c`
- Test: `tests/runtime/test_loader.c`

- [ ] **Step 1: Write the top-level `CMakeLists.txt`**

```cmake
# khbbs: Kingdom Hearts Birth by Sleep (ULUS10505 v1.00) static recompilation.
# See docs/architecture.md. Game files come from KHBBS_GAME_DIR (default ./game)
# and generated code goes to the build tree; neither is ever committed.
cmake_minimum_required(VERSION 3.16)
project(khbbs C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(KHBBS_HOST "pc" CACHE STRING "Host to build: pc or vita")
set(KHBBS_GAME_DIR "${CMAKE_SOURCE_DIR}/game" CACHE PATH "Directory with EBOOT.dec.ELF (tools/extract_game.sh)")
set(PSPRECOMP_DIR "${CMAKE_SOURCE_DIR}/third_party/psprecomp" CACHE PATH "psprecomp fork checkout")
option(KHBBS_WITH_GAME "Recompile the game and build the host (needs KHBBS_GAME_DIR)" ON)

if(NOT EXISTS "${PSPRECOMP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "psprecomp not found at ${PSPRECOMP_DIR}; run tools/setup_toolchain.sh clones")
endif()
add_subdirectory("${PSPRECOMP_DIR}" psprecomp EXCLUDE_FROM_ALL)

add_subdirectory(runtime)

enable_testing()
add_subdirectory(tests/runtime)

if(KHBBS_WITH_GAME)
    include(cmake/gen.cmake)
    add_subdirectory(hosts/${KHBBS_HOST})
endif()
```

- [ ] **Step 2: Write the runtime and test CMake files**

`runtime/CMakeLists.txt`:

```cmake
# Platform-free runtime: no OS, window or GPU headers here (docs/architecture.md).
add_library(khbbs_runtime STATIC
    loader.c
)
target_include_directories(khbbs_runtime PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(khbbs_runtime PUBLIC psprecomp)
target_compile_options(khbbs_runtime PRIVATE -Wall -Wextra)
```

`tests/runtime/CMakeLists.txt`:

```cmake
# Runtime unit tests: synthetic inputs only, never game data.
add_executable(test_loader test_loader.c)
target_link_libraries(test_loader PRIVATE khbbs_runtime)
add_test(NAME loader COMMAND test_loader WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
```

- [ ] **Step 3: Write the failing loader test**

`tests/runtime/test_loader.c`:

```c
/* Loader: PT_LOAD segments land at their vaddr, the bss tail is zeroed, and
 * entry and $gp are reported. A synthetic ELF built here; no game data. */
#include "khbbs/loader.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static void put16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }

/* Layout: ehdr @0, phdr @0x34, payload @0x60 (8 bytes), .reginfo @0x68 (24 bytes),
 * shstrtab @0x80, shdrs @0xA0 (3 entries). */
static size_t build_elf(uint8_t *b) {
    memset(b, 0, 0x200);
    memcpy(b, "\x7f" "ELF\x01\x01\x01", 7);
    put16(b + 0x10, 2);            /* ET_EXEC */
    put16(b + 0x12, 8);            /* EM_MIPS */
    put32(b + 0x14, 1);
    put32(b + 0x18, 0x08804004u);  /* e_entry */
    put32(b + 0x1C, 0x34);         /* e_phoff */
    put32(b + 0x20, 0xA0);         /* e_shoff */
    put16(b + 0x28, 0x34);         /* e_ehsize */
    put16(b + 0x2A, 0x20);         /* e_phentsize */
    put16(b + 0x2C, 1);            /* e_phnum */
    put16(b + 0x2E, 0x28);         /* e_shentsize */
    put16(b + 0x30, 3);            /* e_shnum */
    put16(b + 0x32, 2);            /* e_shstrndx */
    uint8_t *ph = b + 0x34;
    put32(ph + 0, 1);              /* PT_LOAD */
    put32(ph + 4, 0x60);
    put32(ph + 8, 0x08804000u);
    put32(ph + 16, 8);             /* filesz */
    put32(ph + 20, 16);            /* memsz: 8 bytes of bss */
    put32(b + 0x60, 0x11223344u);
    put32(b + 0x64, 0x55667788u);
    put32(b + 0x68 + 20, 0x08BD97E0u); /* .reginfo ri_gp_value */
    memcpy(b + 0x80, "\0.reginfo\0.shstrtab\0", 20);
    uint8_t *sh = b + 0xA0 + 0x28;     /* section 1: .reginfo */
    put32(sh + 0, 1);
    put32(sh + 4, 0x70000006u);
    put32(sh + 16, 0x68);
    put32(sh + 20, 24);
    sh += 0x28;                        /* section 2: .shstrtab */
    put32(sh + 0, 10);
    put32(sh + 4, 3);
    put32(sh + 16, 0x80);
    put32(sh + 20, 20);
    return 0xA0 + 3 * 0x28;
}

int main(void) {
    uint8_t elf[0x200];
    size_t n = build_elf(elf);
    FILE *f = fopen("t_loader.elf", "wb");
    fwrite(elf, 1, n, f);
    fclose(f);

    CHECK(psp_mem_init() == 0, "memory");
    psp_write32(0x08804008u, 0xFFFFFFFFu);   /* must be zeroed as bss */

    khbbs_image img;
    CHECK(khbbs_load_elf("t_loader.elf", &img) == 0, "load succeeds");
    CHECK(img.entry == 0x08804004u, "entry");
    CHECK(img.gp == 0x08BD97E0u, "gp from .reginfo");
    CHECK(img.lo == 0x08804000u && img.hi == 0x08804010u, "image extent");
    CHECK(psp_read32(0x08804000u) == 0x11223344u, "segment byte 0");
    CHECK(psp_read32(0x08804004u) == 0x55667788u, "segment byte 4");
    CHECK(psp_read32(0x08804008u) == 0 && psp_read32(0x0880400Cu) == 0, "bss zeroed");

    CHECK(khbbs_load_elf("does-not-exist.elf", &img) != 0, "missing file fails");

    printf("%s\n", failures ? "test_loader: FAILED" : "test_loader: ok");
    return failures ? 1 : 0;
}
```

- [ ] **Step 4: Write the loader header**

`runtime/include/khbbs/loader.h`:

```c
/* Load a fixed-address PSP ELF (ET_EXEC, as KH BBS's decrypted EBOOT is)
 * into guest memory. Relocatable PRX loading is not needed by this game. */
#ifndef KHBBS_LOADER_H
#define KHBBS_LOADER_H

#include <stdint.h>

typedef struct {
    uint32_t entry;   /* e_entry */
    uint32_t gp;      /* .reginfo ri_gp_value, 0 if absent */
    uint32_t lo, hi;  /* lowest vaddr and end of the highest PT_LOAD (memsz) */
} khbbs_image;

/* Returns 0 on success, -1 on any error (message on stderr). psp_mem_init()
 * must have been called. */
int khbbs_load_elf(const char *path, khbbs_image *out);

#endif
```

- [ ] **Step 5: Run the test and watch it fail to link**

Run: `cmake -S . -B build/pc -G Ninja -DKHBBS_WITH_GAME=OFF && cmake --build build/pc`
Expected: link error, undefined symbol `_khbbs_load_elf` (`runtime/loader.c` does not exist yet, so first create it empty: `touch runtime/loader.c`).

- [ ] **Step 6: Implement the loader**

`runtime/loader.c`:

```c
#include "khbbs/loader.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

static uint8_t *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    rewind(f);
    uint8_t *b = n > 0 ? (uint8_t *)malloc((size_t)n) : NULL;
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    fclose(f);
    if (b) *len = (size_t)n;
    return b;
}

static uint32_t find_gp(const uint8_t *d, size_t len) {
    uint32_t shoff = rd32(d + 0x20);
    uint16_t shentsize = rd16(d + 0x2E), shnum = rd16(d + 0x30);
    if (!shoff || shentsize < 0x28 || shoff + (size_t)shnum * shentsize > len) return 0;
    for (uint16_t i = 0; i < shnum; i++) {
        const uint8_t *sh = d + shoff + (size_t)i * shentsize;
        if (rd32(sh + 4) == 0x70000006u /* SHT_MIPS_REGINFO */ && rd32(sh + 20) >= 24 &&
            rd32(sh + 16) + 24 <= len)
            return rd32(d + rd32(sh + 16) + 20);
    }
    return 0;
}

int khbbs_load_elf(const char *path, khbbs_image *out) {
    size_t len = 0;
    uint8_t *d = slurp(path, &len);
    if (!d) { fprintf(stderr, "khbbs: cannot read %s\n", path); return -1; }
    if (len < 0x34 || memcmp(d, "\x7f" "ELF\x01\x01", 6) != 0 || rd16(d + 0x12) != 8) {
        fprintf(stderr, "khbbs: %s is not a little-endian 32-bit MIPS ELF\n", path);
        free(d);
        return -1;
    }
    uint32_t phoff = rd32(d + 0x1C);
    uint16_t phentsize = rd16(d + 0x2A), phnum = rd16(d + 0x2C);
    if (phentsize < 0x20 || phoff + (size_t)phnum * phentsize > len) {
        fprintf(stderr, "khbbs: %s: bad program headers\n", path);
        free(d);
        return -1;
    }
    out->entry = rd32(d + 0x18);
    out->gp = find_gp(d, len);
    out->lo = 0xFFFFFFFFu;
    out->hi = 0;
    int loaded = 0;
    for (uint16_t i = 0; i < phnum; i++) {
        const uint8_t *ph = d + phoff + (size_t)i * phentsize;
        if (rd32(ph) != 1 /* PT_LOAD */) continue;
        uint32_t off = rd32(ph + 4), vaddr = rd32(ph + 8), filesz = rd32(ph + 16), memsz = rd32(ph + 20);
        uint8_t *dst = memsz ? (uint8_t *)psp_mem_ptr(vaddr, memsz) : NULL;
        if (memsz && (!dst || filesz > memsz || off + (size_t)filesz > len)) {
            fprintf(stderr, "khbbs: %s: segment at 0x%08X (+0x%X) does not fit guest memory\n",
                    path, vaddr, memsz);
            free(d);
            return -1;
        }
        if (memsz) {
            memcpy(dst, d + off, filesz);
            memset(dst + filesz, 0, memsz - filesz);
        }
        if (vaddr < out->lo) out->lo = vaddr;
        if (vaddr + memsz > out->hi) out->hi = vaddr + memsz;
        loaded++;
    }
    free(d);
    if (!loaded) { fprintf(stderr, "khbbs: %s has no PT_LOAD segment\n", path); return -1; }
    return 0;
}
```

- [ ] **Step 7: Run the test**

Run: `cmake --build build/pc && ctest --test-dir build/pc --output-on-failure`
Expected: `test_loader: ok`, `100% tests passed`

- [ ] **Step 8: Commit**

```bash
tools/check_no_assets.sh
git add CMakeLists.txt runtime tests/runtime
git commit -m "CMake skeleton and runtime ELF loader"
```

---

### Task 8: HLE trace writer and boot

**Files:**
- Create: `runtime/include/khbbs/trace.h`, `runtime/trace.c`
- Create: `runtime/include/khbbs/boot.h`, `runtime/boot.c`
- Modify: `runtime/CMakeLists.txt` (add sources)
- Test: `tests/runtime/test_trace.c`, modify `tests/runtime/CMakeLists.txt`

- [ ] **Step 1: Write the failing trace test**

`tests/runtime/test_trace.c`:

```c
/* The trace writer emits one JSON object per firmware call, in a format
 * tools/oracle (P2) will parse. Synthetic, no game data. */
#include "khbbs/trace.h"
#include "psprecomp/hle.h"
#include "psprecomp/cpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static void fake(void) { psp_ret(7); }

int main(void) {
    psp_cpu_reset();
    psp_hle_register(0x109F50BCu, "IoFileMgrForUser", "sceIoOpen", fake);
    CHECK(khbbs_trace_open("t_trace.jsonl") == 0, "open");
    psp_cpu.r[PSP_REG_A0] = 0x08900000u;
    psp_cpu.r[PSP_REG_RA] = 0x08804100u;
    psp_hle_call(0x109F50BCu);
    psp_hle_call(0xDEADBEEFu);
    khbbs_trace_close();

    char buf[1024] = {0};
    FILE *f = fopen("t_trace.jsonl", "rb");
    CHECK(f != NULL, "file written");
    if (f) { buf[fread(buf, 1, sizeof buf - 1, f)] = 0; fclose(f); }
    const char *want1 =
        "{\"seq\":0,\"nid\":\"0x109F50BC\",\"name\":\"sceIoOpen\",\"ra\":\"0x08804100\","
        "\"args\":[\"0x08900000\",\"0x00000000\",\"0x00000000\",\"0x00000000\","
        "\"0x00000000\",\"0x00000000\",\"0x00000000\",\"0x00000000\"],\"ret\":\"0x00000007\"}\n";
    CHECK(strncmp(buf, want1, strlen(want1)) == 0, "first line exact");
    CHECK(strstr(buf, "{\"seq\":1,\"nid\":\"0xDEADBEEF\",\"name\":null,") != NULL, "unknown NID line");
    CHECK(khbbs_trace_count() == 2, "count");

    printf("%s\n", failures ? "test_trace: FAILED" : "test_trace: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/runtime/CMakeLists.txt`:

```cmake
add_executable(test_trace test_trace.c)
target_link_libraries(test_trace PRIVATE khbbs_runtime)
add_test(NAME trace COMMAND test_trace WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
```

- [ ] **Step 2: Write `trace.h`**

```c
/* JSON-lines trace of firmware calls: one object per line,
 *   {"seq":N,"nid":"0x...","name":"..."|null,"ra":"0x...","args":[8 x "0x..."],"ret":"0x..."}
 * The format is the contract with tools/oracle (P2); change both together. */
#ifndef KHBBS_TRACE_H
#define KHBBS_TRACE_H

#include <stdint.h>

int  khbbs_trace_open(const char *path);   /* installs the psprecomp hook */
void khbbs_trace_close(void);              /* removes it and flushes */
uint64_t khbbs_trace_count(void);

#endif
```

- [ ] **Step 3: Run the test and watch it fail to link**

Add `trace.c` to `runtime/CMakeLists.txt` sources (create the file empty), then run `cmake --build build/pc`.
Expected: undefined symbols `_khbbs_trace_open` and friends.

- [ ] **Step 4: Implement `trace.c`**

```c
#include "khbbs/trace.h"
#include "psprecomp/hle.h"

#include <stdio.h>

static FILE *g_out;
static uint64_t g_seq;

static void on_call(uint32_t nid, const char *name, const uint32_t args[8], uint32_t ret, uint32_t ra) {
    if (!g_out) return;
    fprintf(g_out, "{\"seq\":%llu,\"nid\":\"0x%08X\",\"name\":", (unsigned long long)g_seq++, nid);
    if (name) fprintf(g_out, "\"%s\"", name); else fputs("null", g_out);
    fprintf(g_out, ",\"ra\":\"0x%08X\",\"args\":[", ra);
    for (int i = 0; i < 8; i++) fprintf(g_out, "%s\"0x%08X\"", i ? "," : "", args[i]);
    fprintf(g_out, "],\"ret\":\"0x%08X\"}\n", ret);
}

int khbbs_trace_open(const char *path) {
    g_out = fopen(path, "w");
    if (!g_out) { fprintf(stderr, "khbbs: cannot write trace %s\n", path); return -1; }
    g_seq = 0;
    psp_hle_set_trace(on_call);
    return 0;
}

void khbbs_trace_close(void) {
    psp_hle_set_trace(NULL);
    if (g_out) fclose(g_out);
    g_out = NULL;
}

uint64_t khbbs_trace_count(void) { return g_seq; }
```

- [ ] **Step 5: Run the tests**

Run: `cmake --build build/pc && ctest --test-dir build/pc --output-on-failure`
Expected: `test_trace: ok`, `test_loader: ok`

- [ ] **Step 6: Write `boot.h` and `boot.c`**

`boot` has no unit test of its own: it only wires psprecomp calls together, and Task 9 runs it on the game. Keep it this small.

`runtime/include/khbbs/boot.h`:

```c
/* Run a loaded module's entry point until it returns or the run must stop. */
#ifndef KHBBS_BOOT_H
#define KHBBS_BOOT_H

#include "khbbs/loader.h"
#include <stdint.h>

typedef enum { KHBBS_RETURNED, KHBBS_DISPATCH_MISS, KHBBS_BUDGET } khbbs_stop;

typedef struct {
    khbbs_stop why;
    uint32_t   addr;       /* the missed target, for KHBBS_DISPATCH_MISS */
    uint64_t   calls;      /* psp_dispatch_calls() at the stop */
} khbbs_result;

/* `budget` caps indirect dispatches (0 = unlimited). The module gets
 * module_start(argc, argp) with argp = `path` placed in guest memory. */
khbbs_result khbbs_boot(const khbbs_image *img, const char *path, uint64_t budget);

const char *khbbs_stop_name(khbbs_stop s);

#endif
```

`runtime/boot.c`:

```c
#include "khbbs/boot.h"
#include "psprecomp/cpu.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"

#include <setjmp.h>
#include <string.h>

/* Boot-time scratch in the "kernel" partition (docs/architecture.md, Memory):
 * the argument string and the initial stack. The game's own threads get
 * stacks from the sysmem HLE later; this stack only runs module_start. */
#define BOOT_ARGP      0x08000100u
#define BOOT_STACK_TOP 0x08400000u

static jmp_buf g_stop;
static khbbs_result g_res;

static void on_miss(uint32_t addr) {
    g_res.why = KHBBS_DISPATCH_MISS;
    g_res.addr = addr;
    longjmp(g_stop, 1);
}

static void on_budget(void) {
    g_res.why = KHBBS_BUDGET;
    longjmp(g_stop, 1);
}

khbbs_result khbbs_boot(const khbbs_image *img, const char *path, uint64_t budget) {
    memset(&g_res, 0, sizeof g_res);
    psp_cpu_reset();
    size_t n = strlen(path) + 1;
    memcpy(psp_mem_ptr(BOOT_ARGP, (uint32_t)n), path, n);
    psp_cpu.r[PSP_REG_A0] = (uint32_t)n;
    psp_cpu.r[PSP_REG_A1] = BOOT_ARGP;
    psp_cpu.r[PSP_REG_SP] = BOOT_STACK_TOP - 0x40;
    psp_cpu.r[PSP_REG_GP] = img->gp;
    psp_cpu.r[PSP_REG_RA] = 0;
    psp_set_miss_handler(on_miss);
    if (budget) psp_dispatch_set_budget(budget, on_budget);

    if (setjmp(g_stop) == 0) {
        psp_dispatch(img->entry);
        g_res.why = KHBBS_RETURNED;
    }
    g_res.calls = psp_dispatch_calls();
    return g_res;
}

const char *khbbs_stop_name(khbbs_stop s) {
    switch (s) {
    case KHBBS_RETURNED:      return "entry point returned";
    case KHBBS_DISPATCH_MISS: return "dispatch miss";
    case KHBBS_BUDGET:        return "dispatch budget exhausted";
    }
    return "?";
}
```

Add `boot.c` to `runtime/CMakeLists.txt`, so the sources are `loader.c trace.c boot.c`.

- [ ] **Step 7: Build and test**

Run: `cmake --build build/pc && ctest --test-dir build/pc`
Expected: builds without warnings, 2 tests pass.

- [ ] **Step 8: Commit**

```bash
tools/check_no_assets.sh
git add runtime tests/runtime
git commit -m "Runtime: JSON-lines HLE trace and boot with stop conditions"
```

---

### Task 9: Generate the game code and the `khbbs` PC host

**Files:**
- Create: `cmake/gen.cmake`
- Create: `hosts/pc/CMakeLists.txt`, `hosts/pc/main.c`
- Create: `docs/bringup.md`

- [ ] **Step 1: Write `cmake/gen.cmake`**

```cmake
# Recompile the user's EBOOT into ${KHBBS_GEN_DIR}. The output is derived from
# the game's code, so it only ever lives in the build tree.
set(KHBBS_ELF "${KHBBS_GAME_DIR}/EBOOT.dec.ELF")
if(NOT EXISTS "${KHBBS_ELF}")
    message(FATAL_ERROR "${KHBBS_ELF} not found. Run tools/extract_game.sh <iso> ${KHBBS_GAME_DIR}, "
                        "or configure with -DKHBBS_WITH_GAME=OFF to build only the runtime and tests.")
endif()

set(KHBBS_SPLIT 64)
set(KHBBS_GEN_DIR "${CMAKE_BINARY_DIR}/gen")
set(KHBBS_GEN_SRCS "${KHBBS_GEN_DIR}/main_register.c" "${KHBBS_GEN_DIR}/main_imports.c")
math(EXPR _last "${KHBBS_SPLIT} - 1")
foreach(i RANGE 0 ${_last})
    if(i LESS 10)
        set(_n "00${i}")
    elseif(i LESS 100)
        set(_n "0${i}")
    else()
        set(_n "${i}")
    endif()
    list(APPEND KHBBS_GEN_SRCS "${KHBBS_GEN_DIR}/main_funcs_${_n}.c")
endforeach()

add_custom_command(
    OUTPUT ${KHBBS_GEN_SRCS} "${KHBBS_GEN_DIR}/main_funcs.h"
    COMMAND ${CMAKE_COMMAND} -E make_directory "${KHBBS_GEN_DIR}"
    COMMAND $<TARGET_FILE:allegrexrecomp> emit "${KHBBS_ELF}" "${KHBBS_GEN_DIR}" main --split ${KHBBS_SPLIT}
    DEPENDS allegrexrecomp "${KHBBS_ELF}"
    COMMENT "Recompiling ${KHBBS_ELF}"
    VERBATIM)

add_library(khbbs_recomp STATIC ${KHBBS_GEN_SRCS})
target_include_directories(khbbs_recomp PRIVATE "${KHBBS_GEN_DIR}")
target_link_libraries(khbbs_recomp PUBLIC psprecomp)
# -ffp-contract=off keeps PSP float semantics (no fused multiply-add); -w
# because generated code trips every unused-label/variable warning by design.
target_compile_options(khbbs_recomp PRIVATE -O1 -ffp-contract=off -w)
```

- [ ] **Step 2: Write the host**

`hosts/pc/CMakeLists.txt`:

```cmake
add_executable(khbbs main.c)
target_link_libraries(khbbs PRIVATE khbbs_runtime khbbs_recomp psprecomp)
target_compile_options(khbbs PRIVATE -Wall -Wextra)
```

`hosts/pc/main.c`:

```c
/* khbbs (PC, P1): load the decrypted EBOOT, run it, report why it stopped.
 * No window yet; that is P7. */
#include "khbbs/boot.h"
#include "khbbs/loader.h"
#include "khbbs/trace.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void psp_recomp_register(void);  /* generated */

static int usage(void) {
    fprintf(stderr, "usage: khbbs --elf <EBOOT.dec.ELF> [--trace <file.jsonl>] [--budget N]\n");
    return 2;
}

int main(int argc, char **argv) {
    const char *elf = NULL, *trace = NULL;
    uint64_t budget = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--elf") && i + 1 < argc) elf = argv[++i];
        else if (!strcmp(argv[i], "--trace") && i + 1 < argc) trace = argv[++i];
        else if (!strcmp(argv[i], "--budget") && i + 1 < argc) budget = strtoull(argv[++i], NULL, 0);
        else return usage();
    }
    if (!elf) return usage();

    if (psp_mem_init() != 0) { fprintf(stderr, "khbbs: out of memory\n"); return 1; }
    psp_hle_init();
    psp_recomp_register();
    if (trace && khbbs_trace_open(trace) != 0) return 1;

    khbbs_image img;
    if (khbbs_load_elf(elf, &img) != 0) return 1;
    printf("khbbs: loaded %s  [0x%08X, 0x%08X)  entry 0x%08X  gp 0x%08X\n",
           elf, img.lo, img.hi, img.entry, img.gp);

    khbbs_result r = khbbs_boot(&img, "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN", budget);
    printf("khbbs: stopped: %s", khbbs_stop_name(r.why));
    if (r.why == KHBBS_DISPATCH_MISS) printf(" at 0x%08X", r.addr);
    printf("  (%llu dispatches, %llu firmware calls)\n",
           (unsigned long long)r.calls, (unsigned long long)khbbs_trace_count());
    if (r.why == KHBBS_DISPATCH_MISS) psp_miss_context();
    khbbs_trace_close();
    return r.why == KHBBS_RETURNED ? 0 : 3;
}
```

- [ ] **Step 3: Configure and build with the game**

Run:
```bash
cmake -S . -B build/pc -G Ninja -DKHBBS_WITH_GAME=ON
time cmake --build build/pc
```
Expected: "Recompiling …/EBOOT.dec.ELF", 64 + 2 generated files compile, and `build/pc/hosts/pc/khbbs` links. Write the wall-clock build time in `docs/bringup.md` (Step 5). If it takes more than 10 minutes, raise `KHBBS_SPLIT` to 128 and note both times.

- [ ] **Step 4: Run it**

Run:
```bash
build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --trace build/trace-p1.jsonl --budget 50000000 2>build/khbbs-p1.stderr
wc -l build/trace-p1.jsonl
head -5 build/trace-p1.jsonl
grep -c "unimplemented firmware call" build/khbbs-p1.stderr
```
Expected: the `loaded` line shows `[0x08804000, 0x09F3D7D0)  entry 0x08A8BD58  gp 0x08BD97E0`. The first trace lines are crt0's `sceKernelSetCompiledSdkVersion…` (NID `0x35669D4C`, `a0 0x06020010`) and `sceKernelSetCompilerVersion` (`0xF77D77CB`, `a0 0x00030306`). How far it gets after that is the point of the run. **Any** stop reason is a valid outcome for P1. Record it.

- [ ] **Step 5: Write `docs/bringup.md`**

```markdown
# Bring-up log

One entry per run that taught us something. Newest last. Commands are exact;
traces stay in build/ and are never committed.

## P1, first boot (YYYY-MM-DD)

- Build: psprecomp `<hash>`, `--split 64`, `-O1`: <minutes> to build from clean.
- Command: `build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --trace build/trace-p1.jsonl --budget 50000000`
- Loaded `[0x08804000, 0x09F3D7D0)`, entry `0x08A8BD58`, gp `0x08BD97E0`.
- Stopped: <reason> at <address>, after <N> dispatches and <M> firmware calls.
- First 10 firmware calls: <names, from the trace>.
- Unimplemented calls hit: <list from khbbs-p1.stderr, with counts>.
- What this means for P2–P4: <one or two sentences>.
```

Fill every `<…>` from the run's actual output. Do not leave any unfilled.

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git status --short   # must list no files under game/ or build/
git add cmake hosts/pc docs/bringup.md
git commit -m "Generate game code at build time; khbbs PC host runs the EBOOT"
```

---

### Task 10: CI builds and tests the runtime without the game

**Files:**
- Modify: `.github/workflows/checks.yml`

- [ ] **Step 1: Add a job**

Append under `jobs:` in `.github/workflows/checks.yml`:

```yaml
  runtime:
    # Runtime unit tests on synthetic data; no game files exist in CI.
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - name: Ninja
        run: command -v ninja || brew install ninja
      - name: Fetch pinned psprecomp fork
        run: tools/setup_toolchain.sh clones
      - name: Build runtime and tests
        run: |
          cmake -S . -B build/ci -G Ninja -DKHBBS_WITH_GAME=OFF
          cmake --build build/ci
      - name: Test
        run: ctest --test-dir build/ci --output-on-failure
```

`setup_toolchain.sh clones` also builds pspdecrypt, which needs openssl@3. GitHub's macOS runners have it in Homebrew. If the job fails there, split `clones` into `psprecomp` and `pspdecrypt` subcommands and call only the first.

- [ ] **Step 2: Check it locally the way CI will**

Run: `rm -rf build/ci && cmake -S . -B build/ci -G Ninja -DKHBBS_WITH_GAME=OFF && cmake --build build/ci && ctest --test-dir build/ci`
Expected: 2 tests pass, and nothing reads `game/`.

- [ ] **Step 3: Commit, push and open the PR**

```bash
tools/check_no_assets.sh
git add .github/workflows/checks.yml
git commit -m "CI: build and test the runtime without game data"
git push -u origin p1-build-skeleton
gh pr create --base main --title "P1: build skeleton and first boot" \
  --body "Implements docs/superpowers/plans/2026-10-18-p1-build-skeleton.md. Bring-up result in docs/bringup.md."
```

---

## Self-review notes

- Coverage against `docs/plan.md` P1: fork fixes (Tasks 2–5), pin (6), CMake + runtime (7–8), generated code + host + boot report (9), CI (10). The HLE trace format is defined in Task 8 and is the contract with P2.
- Out of scope on purpose (YAGNI for P1): `bbs.toml`, threads, overlays, switch tables, any HLE beyond psprecomp's own. psprecomp's `threadman` runs threads to completion, so the first run will probably stop early. Recording where is the deliverable.
- Names used across tasks: `khbbs_image`, `khbbs_load_elf`, `khbbs_trace_open/close/count`, `khbbs_boot`, `khbbs_result`, `khbbs_stop_name`, `psp_hle_set_trace`, `psp_hle_trace_fn`, `emit_opts.split`. Each is defined once, in the task that introduces it.
