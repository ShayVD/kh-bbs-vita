# Architecture proposal

Status: **proposal for review** (Phase 0). Nothing here is implemented yet.
Numbers come from [phase0-report.md](phase0-report.md); the target is pinned
in [target.md](target.md).

Two deliverables share one codebase:

1. **The port**: the main module and its nine overlays are statically
   recompiled to C, linked against our runtime (PSP firmware in HLE form), and
   run on a host: macOS first (SDL2 + OpenGL), then PS Vita (VitaSDK + vitaGL).
2. **The decompilation**: byte-matching C++ that replaces recompiled functions
   one at a time. kh1-decomp is the model.

Milestones this design is aimed at: **M1** boots to the first rendered frame
on PC; **M2** reaches the title screen on Vita.

## Ground rules carried into the design

- No game data, generated C or key material in git. Recompiled C is derived
  from the game's code, so it is generated into `build/` at build time from
  the user's `./game/`, exactly like kh1-decomp's `out/`.
  `tools/check_no_assets.sh` runs in CI and as a pre-commit hook.
- Every address is a ULUS10505 v1.00 address. Config that names addresses
  lives in one file (`bbs.toml`, the counterpart of `kh1.toml`), which pins the
  SHA-1s.
- `runtime/` and the generated code are platform-free (C11/C++17, no OS or GPU
  headers). Only `hosts/pc` and `hosts/vita` touch SDL, GL, VitaSDK or the OS.
  Code the two hosts share, such as the GL renderer, lives in `hosts/common/`.
- PPSSPP is the oracle: run as a separate process and diffed against, never
  linked (psprecomp's [ORACLE.md](../third_party/psprecomp/docs/ORACLE.md)
  rule, and needed for licensing anyway).

## Overview

```
 ./game/EBOOT.dec.ELF ─┐                   build/gen/            hosts/pc  (SDL2, GL 4.1 core)
 ./game/*_MODULE_*.ELF ─┼─► bbsrecomp ──►  main_000.c … main_NNN.c ─┐      hosts/vita (vitaGL, SceAudioOut,
 bbs.toml (entries,    ┘   (our fork of    ov_title_*.c …           │                  SceCtrl, SceAvcdec)
  names, overlays)          allegrexrecomp) dispatch tables          │             ▲
                                                                     ▼             │ host interface
 decomp/*.cpp (later) ──► strong symbols override weak ones ──►  bbs (static lib) ─┤ (video, audio,
                                                                     ▲             │  input, files,
 runtime/  cpu · mem · dispatch · overlays · scheduler · kernel HLE  │             │  threads, clock)
           ge (interpreter) · vfpu · sas/atrac/mpeg HLE · io/pgd ────┘─────────────┘
```

## What we take from psprecomp and what we write

psprecomp (MIT, `caca759`) is the starting point. We **fork it**
(github.com/ShayVD/psprecomp, used as a submodule or pinned clone, see open
questions) and keep the changes upstreamable where they are generic.

| Component | Decision | Changes needed for this game |
|---|---|---|
| `container.c`, `keys.c`, ISO9660/PBP/`~PSP` parsing | **Fork, as is** | none. The ISO reader is also reused at run time for `disc0:` |
| `decode.c` (Allegrex + VFPU decoder) | **Fork** | name the 121 unnamed VFPU encodings |
| `analyze.c` (function discovery) | **Fork** | seed only into `.text` (removes the 2,370 false functions); recognise `$at`-relative switch tables (417 sites); read extra entry points, vtables and `.cplinit` from `bbs.toml`; overlay mode (fixed base, external entry list, calls into main resolved against main's symbols) |
| `emit.c` (C emitter) | **Fork, substantial** | split output into ~200 translation units; registers in locals instead of the global struct (see Performance); weak `psp_func_*` for decomp overrides; overlay-window calls through the overlay router; fix `vfim` float printing and cross-function `goto`; inline VFPU ops with constant register indices |
| `cpu.h`, `mem.c` | **Fork, rework** | inline fast path for RAM, explicit slow path for VRAM, scratchpad and MMIO |
| `dispatch.c` + tracing instruments | **Fork, keep the instruments** | flat array table instead of lookup; overlay sub-tables; HLE thunk range |
| `vfpu.c` | **Fork, extend** | `mtv`/`mfv`, `vrot`, `vcmov`, `vcrsp`/`vqmul`, `vsge`/`vslt`, `vf2in`/`vf2iz`/…, plus PSP-accurate `vsin`/`vcos`/`vrsq`; FPU `round`/`floor`/`ceil.w.s` |
| `ctors.c` | **Fork** | run `.cplinit` (100 entries); `.ctors` is empty in this game |
| `crypto/` (AES, CMAC, KIRK CMD1) | **Fork** | used by our PGD tool (see File I/O) |
| `hle/sysmem.c`, `iofilemgr.c`, `sascore.c`, `display.c` | **Fork as starting points** | finish them against [phase0-imports.md](phase0-imports.md) |
| `hle/ge.c` interpretation half | **Fork as a starting point** | full vertex formats, T&L, all texture formats, block transfers, signals and callbacks |
| `hle/threadman.c` (run-to-completion) | **Replace** | real scheduler, see Threading |
| `render.c` / software rasterizer | **Keep as the oracle backend** | |
| GPU backend, texture/framebuffer caches | **Write** | |
| Thread scheduler, kernel objects, interrupts, alarms, vblank timing | **Write** | |
| `sceMpeg`, `sceAtrac3plus`, `sceUtility*`, `sceUmd*`, `scePower`, net/adhoc stubs | **Write** | |
| Overlay manager | **Write** | |
| File system provider, PGD handling | **Write** | |
| Hosts (pc, vita), build system, oracle tooling, decomp bridge | **Write** | |

## Memory

**Guest address space.** One flat host allocation backs PSP main RAM
(`0x08000000`–`0x0A000000`, 32 MB). Two more back VRAM
(`0x04000000`, 2 MB) and the scratchpad (`0x00010000`, 16 KB). Cache mirrors
(`0x4…`, `0x8…`) fold onto the same storage via `& 0x3FFFFFFF`, as psprecomp
does today.

**Translation** is an inline function the compiler can see through:

```c
static inline uint32_t rd32(uint32_t a) {
    uint32_t off = (a & 0x3FFFFFFFu) - 0x08000000u;
    if (__builtin_expect(off < PSP_RAM_SIZE, 1))
        return *(uint32_t *)(g_ram + off);        /* LE on every host */
    return mem_slow_rd32(a);                      /* VRAM, scratchpad, GE/MMIO, faults */
}
```

That costs one subtract, one compare and one load per guest access, and it is
the same on 64-bit macOS and 32-bit Vita. A PC-only build could reserve 4 GB of
address space and drop the compare. That is an optimisation to measure later,
not part of the design.

**Layout inside guest RAM** follows the real console, so guest pointers stay
valid and diffable against PPSSPP:

| Range | Owner |
|---|---|
| `0x08000000`–`0x08800000` | "kernel" partition: HLE-owned guest-visible objects (thread contexts the game may inspect, fake module info, MPEG/ATRAC work areas the firmware would own) |
| `0x08800000`–`0x08804000` | user partition, low |
| `0x08804000`–`0x09F3D7D0` | main module image, including the overlay window and the game's 19.4 MB `.bss` |
| `0x09F3D7D0`–`0x0A000000` | user partition, high: 778 KB for thread stacks and `sceKernelAllocPartitionMemory` |

The sysmem HLE manages the user partition with the console's placement rules,
so allocations land at the same addresses PPSSPP gives them. Host pointers
never go into guest memory. Kernel objects are referred to by `SceUID`
integers, as on hardware.

**How code reached through pointers is found.** Every recompiled function
*and every interior label* is registered in a dispatch table. psprecomp
already does this (34,239 entries), so a computed jump can land on any label.
The table becomes a flat array indexed by `(addr - 0x08804000) >> 2` over the
main image: 3.3 MB of `.text` is 831k slots, about 3.3 MB of pointers on Vita.
Behind it sit:

- the import-thunk range (`0x08B300A8`–`0x08B30970`), which dispatches
  straight to HLE handlers, so a function pointer to a firmware stub works;
- the overlay window, which routes to the resident overlay's own table (see
  below);
- a miss handler that, in development builds, logs the target, the caller and
  the guest stack to `build/dispatch_misses.txt` and keeps going if it can.
  `tools/merge_misses.py` folds that log into `bbs.toml`'s extra entry
  points, so the next recompile discovers the code properly. In release
  builds a miss is fatal and reported.

Seeds for discovery, beyond the entry point: the `.cplinit` table, vtables
(runs of `.text` pointers in `.rodata`/`.data`, found by a scanner that only
accepts targets inside `.text` that start with a plausible prologue), switch
tables once recognised, and the miss log.

## Overlays

The nine `USRDIR/MODULE/*.ELF` files are separate recompilation units: same
emitter, prefix `ov_<name>_`, their own small dispatch tables. Calls from an
overlay into main are ordinary C calls, because main's functions are linkable
symbols. **Calls from main into the window** (52 direct `jal`/`j` plus 12 data
pointers, found in Phase 0) are emitted as `ovl_call(addr)`. That looks the
target up in the table of whichever overlay is resident.

**Which overlay is resident** is decided by content, not by watching file
names. When the game invalidates the icache over the window, or when a dispatch
lands in the window with no overlay active, the runtime hashes the window's
first page and selects the overlay whose ELF matches. A mismatch (an overlay
we did not recompile, or one modified at run time) is a loud error. On the
real console the overlay's `.bss` is zeroed by the game's loader, so we do not
touch it.

## Threading

**The model: PSP semantics, executed by host threads, one at a time.** The
Allegrex is single-core. The PSP kernel runs the highest-priority ready thread
and switches only inside kernel calls and interrupts. The game's 48 ThreadMan
imports (mutexes at 880 call sites, event flags, semaphores, callbacks,
alarms, mailboxes) depend on exactly those rules. So:

- **Each PSP thread is a host thread** with its own host C stack (needed,
  because recompiled code nests real C calls) and its own guest stack in guest
  memory.
- **A single "CPU baton"** (mutex + one condition variable per thread) means
  exactly one guest thread runs at any moment. Because only one runs,
  `psp_cpu` stays one global register file: a context switch copies registers
  out to the outgoing thread's context and in from the incoming one. No
  thread-local storage on the hot path, which matters on Vita.
- **The scheduler is ours and deterministic**: strict priority, FIFO within a
  priority, wait queues per kernel object with the PSP's FIFO/priority
  attributes, callbacks delivered only in `*CB` waits and
  `sceKernelCheckCallback`. Switch points are the HLE calls that can block or
  wake: wait, signal, delay, sleep, create/start, exit.
- **Interrupts** (vblank sub-interrupt registered through `InterruptManager`,
  alarms, GE finish/signal callbacks) are raised by a host timer and delivered
  at the next safe point. Recompiled code only reaches HLE calls on its own
  schedule, and a guest busy-wait on a flag written by the vblank handler would
  never get there. So the emitter adds a one-load check on loop back-edges
  (`if (g_pending) psp_service();`). psprecomp already instruments back-edges.
- **Time** has two modes. *Real-time* uses the host clock and a 59.94 Hz
  vblank. *Deterministic* uses a virtual clock advanced by a fixed cost per
  back-edge and per HLE call, with vblank every N units: same run, same trace,
  every time. Oracle comparisons use the deterministic mode.

Fibers or `ucontext` would avoid host threads, but `ucontext` is deprecated on
macOS, and Vita fibers would make the PC and Vita builds diverge. Host threads
plus the baton are portable and debuggable. A fiber backend can be dropped in
behind the same scheduler later if switch cost shows up in profiles.

What runs **without** the baton, in parallel on the Vita's other cores: the GE
renderer, audio mixing and decoding, and movie decoding. They only touch
guest memory the game has handed them, under the same synchronisation the
firmware calls imply (list stall addresses, `sceGeDrawSync`, SAS/ATRAC
buffers).

## Graphics: GE display lists to OpenGL / vitaGL

`sceGu` is statically linked game code and is recompiled like everything else.
We implement `sceGe_user` (all 11 imports): lists queued with
`sceGeListEnQueue`, stall updates, sync, signals and finish callbacks.

Two layers, kept apart as psprecomp's RENDERER.md argues:

1. **GE interpreter** (`runtime/ge/`, platform-free). It walks lists (JUMP,
   CALL/RET, BJUMP, stall), tracks the full register state, decodes vertices
   for every vertex format (position/normal/UV/colour widths, weights, morph,
   index buffers), runs bezier/spline tessellation, and performs **transform,
   lighting, skinning and morphing on the CPU** into a canonical vertex
   stream. It resolves texture and CLUT state into keys, and emits draw
   batches to a backend through the interface psprecomp defines, extended
   with texture, blend, depth, stencil, fog, alpha and colour test, logic op
   and scissor state.
2. **Backends** behind that interface:
   - `software` (psprecomp's rasterizer): the reference, used in tests and CI
     without a GPU;
   - `gl` (`hosts/common/gl/`): one implementation for both hosts. It targets
     a deliberately small subset, so the same code runs on macOS GL 4.1 core
     and on vitaGL: VBO streaming, FBOs, `GL_RGBA`/`GL_RGB565` textures, and
     GLSL ES 1.00-compatible shaders generated per state key (vitaGL compiles
     GLSL through vitaShaRK, and we cache compiled shaders on disk).

   Inside the GL backend:
   - **Texture cache**: key = address + format + size + CLUT address/format,
     validated by a fast hash of the source bytes (sampled for large
     textures). It decodes swizzled, CLUT4/8, 16-bit, 32-bit and DXT1/3/5 to
     GL formats on the CPU.
   - **Framebuffer cache**: every VRAM framebuffer address the game renders to
     (`FBP`/`FBW`/`PSM`) gets an FBO. A texture whose address matches a cached
     framebuffer binds the FBO's texture (render to texture). Block transfers
     and CPU reads of VRAM trigger a readback into guest VRAM, which the
     memory slow path makes detectable.
   - **Present**: `sceDisplaySetFrameBuf` names the framebuffer to show, and
     the host scales its FBO to the window. On Vita, 960×544 is exactly 2×
     the PSP's 480×272.

CPU-side T&L comes first because there is then one implementation of GE
semantics (shared by the software oracle and the GPU path) and its output can
be compared vertex by vertex. If Vita profiling shows T&L to be a bottleneck,
skinning and lighting move into generated vertex shaders behind the same
interface.

**Testing graphics before the game boots**: PPSSPP's GE debugger records
frame dumps (display lists plus the memory they reference). `tools/gedump/`
will replay such a dump through our interpreter and backends, so the renderer
can be developed and regression-tested in isolation, with PPSSPP's own
screenshot of that frame as the expected image. We write our own parser for
the dump file format and copy no PPSSPP code.

## VFPU

- **Phase 1: plain C**, in `runtime/vfpu/`, from psprecomp's `vfpu.c`.
  Register file as 128 floats with PSP matrix/row/column aliasing, prefix
  state, and the missing ops listed in the report. Transcendentals
  (`vsin`/`vcos` take quarter-turn units, `vrsq`, `vexp2`, `vlog2`) get
  PSP-accurate implementations, checked against values captured from PPSSPP
  and, for the hard cases, against pspautotests' published expectations.
- **The emitter specialises**: register indices and vector sizes are compile
  time constants in recompiled code, so it emits inline code on fixed array
  slots instead of `psp_vmul(4, 4, 36, 1)`. Compilers vectorise much of this
  on their own.
- **Phase 2: NEON** for the hot ops (`vmmul`, `vtfm`, `vdot`, `vscl`,
  `lv.q`/`sv.q` moves), under `#if defined(__ARM_NEON)` in the runtime. NEON is
  a CPU feature, not a platform one, and **Apple Silicon has NEON**, so the
  NEON path is developed and checked on the Mac against the plain C path
  before it ever runs on Vita.

## Audio and video

- **`sceAudio`** (13 imports): channels with PSP sample counts and
  blocking semantics feed a runtime mixer. The mixer hands 48 kHz stereo
  blocks to the host sink (SDL2 audio on PC, `sceAudioOut` on Vita).
  `OutputBlocking` blocks the guest thread in the scheduler, not the host
  audio thread.
- **`sceSasCore`** (27 imports, 21 in psprecomp): VAG ADPCM voices, ADSR,
  pitch, loops, effects; we finish psprecomp's version.
- **`sceAtrac3plus`** (9 imports): BGM and voice. This needs an ATRAC3+
  decoder on both hosts (the Vita has no ATRAC3+ hardware decoder exposed to
  apps). The candidate is FFmpeg's `atrac3plus` decoder (LGPL-2.1), built
  from source as a minimal static library with only the decoders we need.
  **This is a licensing decision for you** (see Open questions).
- **`sceMpeg`** (29 imports): PSMF demux in the runtime. H.264 decoding sits
  behind a host interface: VideoToolbox on macOS (a system framework, nothing
  to install) and `sceAvcdec` hardware on Vita. Movie audio goes through the
  same ATRAC3+ path.

## File I/O

- **Devices**: `disc0:` / `umd0:` are served from the user's **ISO directly**,
  through psprecomp's ISO9660 reader. That means no 1.7 GB extraction, exact
  LBA semantics for `sceIoDevctl`/`sceIoLseek`, and the same file on PC and
  Vita (`ux0:data/khbbs/disc.iso`). An extracted directory under `./game/`
  works as an alternative root for debugging. `ms0:` maps to a host
  directory (`./save/ms0/`, `ux0:data/khbbs/ms0/`). `host0:`, `fatms0:` and
  `umd1:` alias as the strings in the EBOOT require.
- **Sync and async**: `sceIoReadAsync`/`WaitAsyncCB`/`PollAsync` run on an I/O
  worker. Completion wakes the guest thread through the scheduler, with
  PSP-like latency in deterministic mode.
- **PGD (`BBS1`–`3.DAT`)**: the game sets the key with
  `sceIoIoctl(fd, 0x04100001, key, 16)` and then reads plaintext. Proposal:
  an **install step**, `tools/pgd_decrypt`, written on psprecomp's KIRK/AES
  code with PGD keys loaded from the gitignored `keys/` file, writes
  `game/BBS1.DAT.plain`… once. It validates each file against the PGD header's
  MAC, and PPSSPP reading the same file is the oracle. At run time, the ioctl
  handler checks that the key the game passes matches the one the plaintext
  was made with, then serves reads from the plaintext with the PGD data
  offset applied. That keeps crypto out of the runtime and the Vita's load
  times down. The key itself comes from the game's memory at run time and is
  never committed. Where it sits in the EBOOT is a Phase 1 question.
- **Savedata** (`sceUtilitySavedata*`): a native implementation of the
  load/save/list/size modes writing `ms0:/PSP/SAVEDATA/ULUS10505DATA*/` with
  `PARAM.SFO` + `DATA.BIN`, unencrypted. The dialog modes complete
  immediately at first, later with a native imgui overlay.
  **GamedataInstall** reports success without copying, since our disc access
  is already fast.
- **MsgDialog/OSK/Netconf**: state machines that complete with sensible
  defaults first, and native UI later. **Net/Adhoc** (32 imports): report "no
  network / no partner found" until multiplayer is in scope.

## Decompiled functions replacing recompiled ones

The recompiled build is complete from day one. The decomp chips away at it.

1. **Link-time override.** The emitter declares every `psp_func_XXXXXXXX`
   `__attribute__((weak))`. A function in `decomp/` built for the port
   provides a strong symbol of the same name: a generated **guest-ABI thunk**
   that reads arguments from `$a0`–`$a3`/`$t0`–`$t3`/FPU registers and the
   guest stack, calls the native C++ function and writes `$v0`/`$f0`. The
   dispatch table holds `&psp_func_X`, so indirect calls pick up the override
   too. `bbs.toml` lists which functions are decompiled. The build checks that
   none of them is still emitted as strong.
2. **Calls the other way**: decompiled code calling a function that is not
   decompiled yet goes through a generated wrapper,
   `call_guest<RetT>(0x08812345, args…)`, that sets the guest registers and
   calls the recompiled function.
3. **Pointers.** On a 64-bit Mac, native pointers do not fit the game's 32-bit
   struct layouts. Decompiled code that the port compiles therefore uses
   `gptr<T>` for pointers stored in guest memory: a 4-byte guest address with
   `operator->`/`*`/`[]` translating through `g_ram`. In the matching build
   (original compiler, PSP target), the same header defines `gptr<T>` as
   `T*`, so the source is identical and still matches. This is kh1-decomp's
   `docs/portability.md` rule 1
   ("no absolute addresses, platform calls through wrappers"), extended with
   one PSP-specific rule. The game is C++, which is what makes the template
   possible.
4. **Byte matching** uses kh1-decomp's machinery: splat for the PSP target
   (the EBOOT is a fixed-address `ET_EXEC`, which splat handles well), the
   original compiler once identified, objdiff, `layout.tsv`/`functions.tsv`,
   and patch-in-place verification that rebuilds a byte-identical
   `EBOOT.dec.ELF`. A function is *done* when it matches **and** the port
   passes the oracle checks with its override enabled.
5. **Differential check per function**: a development build can run the
   decompiled and the recompiled body of the same function back to back on a
   copy-on-write snapshot of guest memory and registers, and report the first
   difference. Expensive, so it is opt-in per function.

## Verification against PPSSPP (built early)

Started in Phase 1, before the first frame, because static recompilation
fails quietly. `tools/oracle/`:

| Check | Our side | PPSSPP side | Compared by |
|---|---|---|---|
| **HLE call trace** | every firmware call: thread, name, args, return, guest `$ra` | PPSSPP's HLE logging (per-module log levels) | `tools/oracle/trace_diff.py`, aligned per thread, tolerant of timing-only differences |
| **Function-entry trace** | psprecomp's `PSP_ENTER` instrument, filtered by address range | breakpoints/logpoints set through PPSSPP's WebSocket debugger API | first divergent call |
| **Memory snapshots** | dump of `0x08800000`–`0x0A000000` + VRAM at event *N* (Nth vblank, Nth `sceGeListEnQueue`, a chosen PC) | `memory.read` over the WebSocket API at the same event | region diff annotated with `bbs.toml` symbols |
| **Frames** | framebuffer capture from the software and GL backends at frame *N* | PPSSPP screenshot / GE frame dump at frame *N* | image diff (PSNR and a mismatch mask) |
| **GE replay** | `tools/gedump` replays a PPSSPP frame dump through our GE | the dump itself | same image diff, no game boot needed |

The WebSocket client is about 150 lines of standard-library Python, so there
is nothing extra to install beyond PPSSPP itself. The deterministic time mode
makes our side reproducible. PPSSPP is not deterministic across runs, so the
comparison keys on events (call sequence, frame index), not on wall time.

## Build layout

Following kh1-decomp's conventions where they apply (a single config file,
generated files under `build/`, a no-assets check, `docs/progress.md`,
self-tests that need no game data):

```
bbs.toml                 target pins (SHA-1s), .text range, overlays, extra entries,
                         names (the functions.tsv role), decompiled-function list
CMakeLists.txt           top level; KHBBS_HOST=pc|vita
cmake/                   gen.cmake (runs bbsrecomp), vita.cmake (uses $VITASDK toolchain)
recomp/                  bbsrecomp: our fork of allegrexrecomp (or the psprecomp submodule)
runtime/                 platform-free: cpu, mem, dispatch, overlays, sched, kernel/,
                         ge/, vfpu/, audio/, mpeg/, io/, utility/, include/host.h
hosts/common/gl/         GL backend shared by both hosts
hosts/pc/                SDL2 window, input, audio, VideoToolbox, main()
hosts/vita/              vitaGL, SceCtrl, SceAudioOut, SceAvcdec, VPK packaging
decomp/                  matching C++ (later), built two ways: original compiler + native
tools/                   extract_game.sh, import_report.py, check_no_assets.sh,
                         pgd_decrypt, oracle/, gedump/, merge_misses.py
tests/                   runtime unit tests on synthetic data (VFPU, GE, scheduler, mem)
docs/                    target, phase reports, architecture, progress
build/                   (gitignored) gen/*.c, objects, binaries, traces
```

- **Generated code** is split by address range into about 200 translation
  units of roughly 400 KB each. They compile in parallel, rebuild
  incrementally when only `bbs.toml` entries change in one range, and avoid
  the 7-minute single-file compile seen in Phase 0. The generated code is
  built `-O2` with `-ffp-contract=off`, which keeps PSP float behaviour,
  exactly as kh1-decomp's portability rule 7 requires.
- **PC**: `cmake -S . -B build/pc -G Ninja -DKHBBS_HOST=pc -DKHBBS_GAME=game`.
  AppleClang, SDL2 from Homebrew (already installed), the GL framework,
  VideoToolbox.
- **Vita**: `cmake -S . -B build/vita -G Ninja -DKHBBS_HOST=vita
  -DCMAKE_TOOLCHAIN_FILE=$VITASDK/share/vita.toolchain.cmake`. Builds
  `eboot.bin` and a `.vpk` (the VPK contains only our code, and the game files
  are read from `ux0:data/khbbs/`). It uses VitaSDK's vitaGL, vitaShaRK,
  libmathneon and SDL2 (input fallback) packages, already installed.
- **CI** (GitHub Actions, no game data): `check_no_assets.sh`, the runtime
  unit tests on macOS and Linux, and the Vita toolchain build of the runtime
  and hosts.

## Performance notes (what decides Vita viability)

- **Registers in locals.** psprecomp's emitted code reads and writes
  `psp_cpu.r[]` in a global struct. Every guest store through `g_ram` may
  alias it in the compiler's eyes, so registers are reloaded constantly. The
  fork loads the GPRs a function uses into locals at entry and spills them at
  calls, returns and HLE boundaries (the N64Recomp approach). This is the
  single biggest win expected.
- **Direct calls stay direct**; only real indirect calls pay for dispatch. The
  switch-table fix turns 417 dispatch lookups into C `switch` statements.
- The Vita's Cortex-A9 (444 MHz, in-order dual issue) against the Allegrex at
  333 MHz leaves little headroom for game logic. The plan to manage that:
  measure on PC with an instruction-count proxy, then on hardware early (M2);
  NEON VFPU; GE, audio and video on the other cores; and native replacements
  for hot functions (memcpy/memset-like loops, matrix code) before the decomp
  reaches them.

## Decisions (review of PR #1)

1. **psprecomp stays a gitignored clone**, pinned by commit in
   `tools/setup_toolchain.sh` and documented in [setup.md](setup.md). Our
   changes go to a fork that the clone tracks.
2. **ATRAC3+ and H.264**: an LGPL FFmpeg decoder subset, built from source, is
   accepted.
3. **PGD**: decrypt `BBS1`–`3.DAT` **once, offline**, into `./game/`.
4. **Compilers**: fetched from decomp.me's archive. The game is SNC
   ([toolchain.md](toolchain.md)).
5. **Tools**: PPSSPP 1.20.4, JDK 21, Ghidra 12.1.3 and ghidra-allegrex are
   installed ([setup.md](setup.md)).
