# Implementation roadmap

Milestones: **M1, first rendered frame on PC** (what the game draws matches
PPSSPP's frame at the same point), then **M2, title screen on Vita**. The work
is split into sub-plans. Each one produces something that builds, runs and is
tested on its own. Detailed, step-by-step plans live in
[`docs/superpowers/plans/`](superpowers/plans/) and are written one or two
ahead of the work, not all up front, because each sub-plan's findings change
the next.

Design reference: [architecture.md](architecture.md). Facts:
[phase0-report.md](phase0-report.md), [toolchain.md](toolchain.md).

| # | Sub-plan | Delivers | Done when | Depends on |
|---|---|---|---|---|
| **P1** | [Build skeleton and first boot](superpowers/plans/2026-10-18-p1-build-skeleton.md) | psprecomp fork with emitter fixes (`vfim`, `.text`-only seeds, split output, HLE trace hook); CMake build that recompiles `./game/EBOOT.dec.ELF` into `build/`; platform-free loader/boot/trace in `runtime/`; a `khbbs` PC executable that runs the game until it stops | `khbbs` boots the EBOOT, writes an HLE trace (JSON lines), and reports where and why it stopped | — |
| **P2** | [Oracle tooling](superpowers/plans/2026-10-18-p2-oracle-tooling.md) | `tools/oracle/`: PPSSPP WebSocket client, HLE-call trace capture from PPSSPP, `trace_diff.py`, memory snapshot on both sides plus region diff, frame capture plus image diff | the first N HLE calls of PPSSPP and `khbbs` are diffed automatically and the first divergence is reported | P1 |
| **P3** | [Recompiler fidelity](superpowers/plans/2026-10-18-p3-recompiler-fidelity.md) | `$at` switch-table recognition (cases emitted as C `switch`/`goto`); `lui`/`addiu` address-pair seeds; extra entry points and `.cplinit` seeds from `bbs.toml`; dispatch-miss log plus `merge_misses.py`; VFPU prefixes and the missing VFPU ops (`mtv`, `mfv`, `vrot`, `vcmov`, `vcrsp`, `vqmul`, `vsge`, `vslt`, conversions, VFPU7/VFPU9) and FPU `round`/`floor`/`ceil`/`trunc.w.s`, each with unit tests (values hand-derived; PPSSPP-captured checks follow in P2); `break` as a guest-break stop | zero `psp_unimplemented` sites and zero unhandled branches in `.text`; zero dispatch misses on the boot path (**done**, see [bringup.md](bringup.md)) | P1 |
| **P4** | Kernel and scheduler | host-thread-plus-baton scheduler; threads, semaphores, event flags, mutexes, LwMutexes, mailboxes, callbacks, alarms, `DelayThread`, `SystemTime`; the vblank sub-interrupt; deterministic time; sysmem placement that matches PPSSPP | the game's boot threads run and wait/wake in the same order as PPSSPP (P2 diff) through the first `sceGeListEnQueue` | P1, P2 |
| **P5** | Files and data | ISO-backed `disc0:`/`umd0:`, async I/O, `Devctl`/`Getstat`, `sceUmd*`, `ms0:` directory; `tools/pgd_decrypt` (offline, keys from `keys/`), the `0x04100001` ioctl; savedata load/list as "no save" | the game reads its first assets from `BBS0`–`3.DAT` with byte-identical results to PPSSPP | P4 |
| **P6** | GE, display, first frame (**M1**) | GE interpreter complete enough for boot screens (lists, stall, sync, signals, all vertex formats in use, CPU T&L, textures with CLUT/swizzle); `sceDisplay*`; the software backend captures the displayed framebuffer | **M1**: the first non-black frame matches PPSSPP's screenshot within the image-diff threshold | P3, P4, P5 |
| P7 | PC window and GL backend | SDL2 window, input, `hosts/common/gl` backend with texture and framebuffer caches; GL vs software frame diff | the boot sequence runs live in a window at full speed | P6 |
| P8 | Audio and movies | `sceAudio` mixer, SAS, ATRAC3+ and H.264 via the LGPL FFmpeg subset; `sceMpeg` for the opening movie | opening movie and title music play | P7 |
| P9 | Overlays and utility | overlay recompilation plus router (`TITLE.ELF` first); MsgDialog/OSK/savedata dialogs | the title menu (an overlay) is reachable on PC | P3, P7 |
| P10 | Vita host (**M2**) | `hosts/vita` (vitaGL, SceCtrl, SceAudioOut, SceAvcdec), VPK; registers-in-locals emitter change; NEON VFPU | **M2**: title screen on a Vita | P7–P9 |

The decompilation track (splat layout for SNC, objdiff, the `gptr<T>`
bridge, weak-symbol overrides) starts once P3 is done. It does not block M1.
