# Bring-up log

One entry per run that taught us something. Newest last. Commands are exact;
traces stay in build/ and are never committed.

## P1, first boot (2026-10-18)

- Build: psprecomp `b925bd2`, `--split 64`, `-O1`: 11.5 s wall for a clean
  `cmake --build build/pc` (107 steps, 8 cores; 71 s user).
- Command: `perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --trace build/trace-p1.jsonl --budget 50000000`
- Loaded `[0x08804018, 0x09F3D7D0)`, entry `0x08A8BD58`, gp `0x08BD97E0`.
- Stopped: dispatch miss at `0x08A8BE6C`, after 2 dispatches and 3 traced
  firmware calls (exit 3, well inside the alarm).
- First firmware calls: `0x35669D4C` (SysMemUserForUser, the crt0 SDK-version
  setter, a0 `0x06020010`), `sceKernelSetCompilerVersion` (a0 `0x00030306`),
  `sceKernelCreateThread` (entry `0x08A8BE6C`, priority `0x13`, stack
  `0x37000`, returned uid `0x00040000`), then `sceKernelStartThread`
  (`0xF475845D`), which never returned and so is not in the trace: it
  dispatched the new thread's entry and that missed.
- Unimplemented calls hit: `0x35669D4C` x1 (returned 0, harmless here).
- Cause of the miss: `0x08A8BE6C` is the crt0 user-main thread entry, reached
  only through a `lui`/`addiu` pointer passed to `sceKernelCreateThread`.
  The recompiler's function discovery never seeded it, so nothing from
  `0x08A8BE6C` to `0x08A8C0FC` was emitted at all.
- What this means for P2-P4: the toolchain, loader, HLE trace and boot path
  work end to end on the real EBOOT. The first blocker is recompiler coverage
  (P3): code reached only via function pointers (thread entries, callbacks,
  vtables) needs extra entry-point seeds or pointer-pair discovery before we
  see the game's own code run. `0x35669D4C` should get a trivial
  implementation alongside.
- Found on the way, for P4: psprecomp's sysmem heap starts at `0x08C00000`,
  inside the game image `[0x08804018, 0x09F3D7D0)`. The one allocation in
  this run (uid `0x10000` at `0x09FC9000`) was psprecomp's internal
  `psp_sysmem_alloc(from_high)` for the `0x37000`-byte thread stack inside
  `sceKernelCreateThread`, not a game `sceKernelAllocPartitionMemory`; it
  lands outside the image only because it allocates from the top. Low
  allocations from `0x08C00000` would overlap the image, so the fork needs an
  API to place the user partition around the loaded image.

## P3, recompiler fidelity (2026-10-19)

- Build: psprecomp `e75f505` (35 commits on `b925bd2`), `--split 64`,
  `-O1`, `--seeds build/pc/gen/seeds.txt` from `bbs.toml`: 12.8 s wall for
  a clean `cmake --build build/pc` (117 steps, 69 s user).
- Command: `perl -e 'alarm shift; exec @ARGV' 280 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 50000000 --miss-log build/misses.jsonl --keep-going 1000`
  (no `--trace` at this budget: the HLE trace grows by about 660 bytes
  per dispatch, 17.5 GB for 50M dispatches).
- Discovery, P1 → P3 (`allegrexrecomp funcs`):

  | | P1 (`b925bd2`) | P3 (`e75f505`) |
  |---|---|---|
  | seeds | 23,056 | 12,645 used: entry, 2 exports, 12,128 of 23,053 data pointers, 415 address pairs, 99 from `bbs.toml` |
  | functions | 24,853 | 12,905 |
  | `.text` reached | 87.4% | 88.9% |
  | switch tables | 0 of 417 `jr` sites | 376 tables, 11,210 targets; 35 `jr` sites unresolved |
  | switch cases | — | 4,059 as `goto` inside the function, 40 as tail calls |

  The address-pair scan found crt0's `user_main` (`0x08A8BE6C`, the P1
  stop). 10,925 jump-table words were dropped from the data-pointer
  seeds (21 restored), so case blocks stay inside their function; code a
  function walks itself (loop heads) is a label, not a new function.
  Code reached only by falling past an always-taken `b` is no longer
  walked (4,320 bytes).
- Census (`tools/unimpl_census.py build/pc/gen`): **0** `psp_unimplemented`
  (was 725) and **0** unhandled branches (was 47). 55 static calls go to
  undiscovered targets (dispatch at run time, none reached here). 36 guest
  `break` sites are now `psp_break` stops: 27 are `break 7` after
  `beql divisor,$zero` (the compiler's integer divide-by-zero trap), 9 are
  `break 0`.
- Stopped: dispatch budget exhausted after 50,000,000 dispatches, exit 3.
  **No dispatch misses** (the miss log is empty), no untranslated
  instruction, no VFPU trap.
- Boot path (first 200,000 dispatches traced, 589,623 firmware calls):
  crt0 (`0x35669D4C`, `sceKernelSetCompilerVersion`), `sceKernelCreateThread`
  of `user_main`, which runs: `sceKernelAllocPartitionMemory` (uid `0x10001`
  at `0x08C00000`, inside the game image, the known sysmem issue),
  `sceKernelGetBlockHeadAddr`, three `sceKernelCreateMutex`, two callbacks
  (`GAME_EXIT` among them) with `sceKernelRegisterExitCallback`, an event
  flag, then `sceKernelCreateThread("DiscInfoBind", 0x0880F750)`. That
  thread loops forever: `0x6B4A146C`, `0x8EF08FCE`,
  `sceKernelLockLwMutexCB`, `sceIoOpen("umd1:")` → `0x80010002` (no such
  device), `0x15B6446B`, `sceKernelDelayThreadCB(166670)`, repeat. Under
  psprecomp's run-to-completion threads it never yields, so `user_main`
  never resumes.
- Before that, a bad write32 at `0x00000004` happens inside `0x08AE9700`,
  called from crt0 at `0x08A8C0EC` (`ra 0x08A8C0F4`), with a NULL base
  register. It is the first thing P4 should look at, together with the
  sysmem placement.
- Silent miscompiles fixed on the way, each with a regression test:
  - the game's `strlen` (`0x08AEA170`) skipped its final `subu $v0` (a
    loop entry fell into a tail owned as a label by its parent and the old
    emitter returned silently), so string lengths were garbage: before the
    fix `user_main` added 140 MB to a pointer;
  - `vdiv` was decoded at VFPU0 sub-op 4 (SNC assembles it at 7);
  - all 24 `vcst` loaded 0.0 (the index is in bits 20..16, not `vs`);
  - `vidt.q` put its 1 in lane 0 for every column (135 sites build rotation
    matrices with it);
  - the 47 `bvf`/`bvt` branches were never taken;
  - a pending `vpfx*` prefix made the next VFPU op skip itself;
  - `vcmp` conditions 8–15 were always false;
  - `trunc.w.s` (742 sites) was a C cast, undefined for NaN and
    out-of-range values;
  - `vf2iz`/`vi2f` dropped their scale exponent;
  - the runtime library was built with fused multiply-add.
- What this means for P2, P4, P5:
  - P4 next: a real scheduler (the `DiscInfoBind` loop must yield to
    `user_main`), sysmem placement outside the image, the NULL write in
    `0x08AE9700`, and the unimplemented ThreadMan NIDs above.
  - P5: `umd1:` and `disc0:` devices so `sceIoOpen` succeeds.
  - P2: the VFPU semantics marked UNVERIFIED in the fork (prefix lane wrap
    for narrow ops, vcmp's untouched cc bits, vcmov's kept lanes under
    `vpfxd`, sin/cos last bits, vrot/vqmul lane conventions, conversion
    saturation and denormals, vsrt/vsgn NaN and ±0) are the first oracle
    checks. None of this code runs on the boot path yet, so unit tests are
    its only verification so far.
- Tooling notes: SNC's `pspas.exe` under `wibo-macos` started hanging in
  uninterruptible (`U`) state partway through the session (after lldb
  attempts); the stuck processes survive `kill -9`. It probably needs a
  reboot. Every encoding P3 relies on was assembled with it earlier and is
  pinned in the tests.
