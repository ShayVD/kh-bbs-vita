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
