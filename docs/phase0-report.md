# Phase 0 report: what is on the disc and what psprecomp makes of it

Target: ULUS10505 v1.00 (pinned in [target.md](target.md)). Tools: psprecomp
`caca759` (`third_party/psprecomp`), pspdecrypt `c156627`
(`third_party/pspdecrypt`, GPL-3.0, run only as a separate process), and
[`tools/import_report.py`](../tools/import_report.py). Every number below can
be regenerated from the user's own dump with
[`tools/extract_game.sh`](../tools/extract_game.sh) followed by the commands
quoted in each section.

## Summary

- The main executable `EBOOT.BIN` is a `~PSP` module, encrypted with decrypt
  mode 9 and tag `0xD91613F0`. `BOOT.BIN` is 3,708,616 zero bytes, so it is no
  use. **psprecomp cannot decrypt it**: its KIRK CMD1 core is done, but the
  `~PSP` tag layer that builds the CMD1 header is not
  (`allegrexrecomp decrypt` says so and stops). pspdecrypt decrypts it on the
  first try, and the result is exactly the size the header declares.
- The decrypted module is a fixed-address `ET_EXEC` ELF (not a relocatable
  PRX): `.text` is 3.17 MB at `0x08804040`, and the image plus its 19.4 MB of
  `.bss` covers `0x08804000`–`0x09F3D7D0`.
- psprecomp finds **27,225 functions**, but 2,370 of them sit outside
  `.text`. Those are false positives from its heuristic data-pointer seeding.
  The code it reaches covers 90.7% of `.text`. Its emitter writes 84 MB of C.
  AppleClang rejects that C with 3 errors, all emitter bugs. With those
  patched, it compiles.
- **VFPU**: 16,519 of the 831,357 words in `.text` (2.0%) are VFPU
  instructions; 11,091 of those are vector loads and stores. 654 VFPU sites in
  `.text` emit as traps today. Eight opcodes account for most of them
  (`mtv`, `vrot`, `vcmov`, `mfv`, `vcrsp`, `vsge`/`vslt`, `vf2in`), plus 121
  encodings the decoder does not name. 35 FPU rounding instructions also trap.
- **Imports**: 281 firmware functions in 27 libraries, every one with at least
  one call site. psprecomp registers 97 of them, some only as stubs, and 184
  are missing. The full list is in [phase0-imports.md](phase0-imports.md).
- **Runtime code loading**: the game loads no PRX of its own.
  It does load nine plain-ELF **overlays** from `USRDIR/MODULE/` into a
  284,716-byte `.overlays` window at `0x08B8BF04`, and firmware AV modules
  through `sceUtilityLoadModule` (MPEG at least).
- **Compiler** (superseded, see [toolchain.md](toolchain.md): it is SNC): the build is mostly C++ with an embedded Lua 5.1.2. The game
  code was assembled by a non-GNU assembler (98.6% of register moves are `or`,
  not GNU as's `addu`). crt0 reports compiler version `0x030306`. Taken
  together this points to SN Systems' toolchain (ProDG) rather than a stock
  psp-gcc. One GCC-built object is present. No symbol table is present, but
  Windows source paths and assert strings are.
- **Data**: `BBS0.DAT` is a plain `bbsa` v6 archive that also serves as the
  index. **`BBS1`–`BBS3.DAT` are PGD-encrypted**, and the game hands the
  firmware a 16-byte key through `sceIoIoctl(fd, 0x04100001, key, 16)`.
  `BBS4.DAT` holds PSMF movies.

## 1. Files, encryption and code modules

`allegrexrecomp ls` lists 34 entries. The ones that matter:

| Path | Size | What it is |
|---|---:|---|
| `SYSDIR/EBOOT.BIN` | 3,708,960 | `~PSP` encrypted module `MainApp` v1.1, mode 9, tag `0xD91613F0`, SDK `0x06020010`, 2 segments |
| `SYSDIR/BOOT.BIN` | 3,708,616 | All zero bytes. Same length as the decrypted EBOOT, but blanked on retail discs |
| `SYSDIR/OPNSSMP.BIN` | 880 | `~PSP`, mode 4, tag `0x457B0CF0`. Decrypts to 543 bytes of KL4E-compressed data, so it is a tiny firmware-side module. The game imports nothing from it. HLE can ignore it |
| `SYSDIR/UPDATE/` | 28.9 MB | Sony's 6.20 firmware updater. Not used |
| `USRDIR/MODULE/*.ELF` | 78 KB–285 KB each | Nine **plain** (unencrypted) overlays, see below |
| `USRDIR/BBS0.DAT`–`BBS4.DAT` | 1.65 GB total | Data archives, see section 4 |

**Decryption.** `allegrexrecomp decrypt` finds no key file. Even with one it
could not proceed, because DECRYPT.md lists "the mode 9 and mode 10
transforms that build a CMD1 header from a `~PSP` header" as still to do.
pspdecrypt reports `Decryption successful for tag D91613F0 with type 2`, and
the output passes the checks DECRYPT.md asks for:

1. It is exactly 3,708,616 bytes, the `elf size` in the header.
2. It parses as ELF32 MIPS with 30 sections.
3. Its entry point `0x08A8BD58` is inside `.text`.
4. `allegrexrecomp cover` decodes 99.97% of `.text` words, with a code-shaped
   opcode histogram (`lw` 15.3%, `or` 11.7%, `addiu` 9.3%, `sw` 9.2%,
   `jal` 6.1%).
5. Its SHA-1 is pinned in [target.md](target.md).

`tools/extract_game.sh` runs pspdecrypt as an external process. No
pspdecrypt code or key material is in this repo.

**Overlays.** `TITLE`, `MENU_CAMP`, `MENU_TERM`, `MISSION`, `SHOP`,
`CHARA_REPORT`, `BATTLE_DICE`, `FRUIT_BALL` and `KEYBLADE_RIDE` are ELF files
with:

- `e_type` `0xFFA8` (not a standard type), the PSP's Allegrex `e_flags`
  (`0x10A23001`), `e_entry` 0;
- one `PT_LOAD`, and one `PROGBITS` section named after the overlay
  (`.title`, `.menu_camp`, …), sometimes with a small `.<name>.bss`;
- **no relocations, no imports and no exports**.

All nine are linked at `0x08B8BF08` or `0x08B8BF10`, which is inside the main
module's `.overlays` NOBITS section (`0x08B8BF04`, 284,716 bytes). The largest
overlay, `BATTLE_DICE`, needs 284,704 bytes, so the window is sized to fit it
exactly. The overlays call straight into the main module at fixed addresses
(TITLE: 1,362 of its 1,710 `jal`s target main `.text`). The traffic goes both
ways: **main contains 52 direct `jal`/`j` instructions into the overlay window
(36 distinct targets) and 12 data words pointing into it**. So main calls
overlay functions at fixed addresses, and the target depends on which overlay
is resident.
The main module refers to them by lower-case file name
(`title.elf`, `menu_camp.elf`, … and `disc0:/PSP_GAME/USRDIR/MODULE/`). It
reads them with ordinary `sceIo` calls: `ModuleMgrForUser` is imported only for
`GetModuleId`, `GetModuleIdByAddress`, `StopModule`, `UnloadModule` and one
6.x NID (`0x8F2DF740`), and **`sceKernelLoadModule` is not imported at all**.
`UtilsForUser.sceKernelIcacheInvalidateRange` has one call site, presumably
after an overlay load.

**Firmware modules loaded at run time.** `sceUtilityLoadModule` has two call
sites. One passes the constant `0x303` (the MPEG base module). The other loops
over a table in `.bss`. Which other module IDs the game loads (net, adhoc,
SAS, ATRAC3+) will be read from a trace in Phase 1. On our side these loads
are no-ops: the HLE implements the functions directly.

Per-overlay discovery (`allegrexrecomp funcs`, functions / share of the image
reached): BATTLE_DICE 1,694 / 69%, KEYBLADE_RIDE 659 / 61%, CHARA_REPORT 642 / 58%,
MENU_CAMP 500 / 48%, TITLE 321 / 46%, MENU_TERM 213 / 34%, FRUIT_BALL 195 / 42%,
MISSION 164 / 21%, SHOP 123 / 31%. None of them use the VFPU. Coverage is low because psprecomp seeds discovery at `e_entry` = 0. The real entry
points are reached through pointers from the main module and have to be
supplied by us (see [architecture.md](architecture.md#overlays)).

## 2. Function discovery and C output (main module)

```
allegrexrecomp funcs game/EBOOT.dec.ELF
allegrexrecomp emit  game/EBOOT.dec.ELF <outdir> bbs
```

| Metric | Value |
|---|---|
| `.text` | `0x08804040`–`0x08B2FE34`, 3,325,428 bytes, 831,357 words |
| Seeds | entry + 2 exports + 26,625 "scanned data pointers (heuristic)" |
| Functions found | **27,225**; 2,370 of them start outside `.text` (in `.rodata`/`.data`/stub tables), so roughly **24,855 real** |
| Code reached | 3,018,804 bytes, 90.7% of `.text` |
| Largest function | reported as `0x08B638E8`, 13,236 bytes, which is in `.data` and so a false positive |
| Interior labels | 34,239 dispatchable entries (every label can be reached by `psp_dispatch`) |
| Computed jumps | 417 `jr $reg` sites in `.text` (switch tables), **0 tables resolved** |
| Emitted C | `bbs_funcs.c` 84.7 MB, `bbs_funcs.h` 0.8 MB, `bbs_imports.c` 30 KB, in 0.8 s |

**Switch tables.** Every switch looks like this:

```
sltiu  $a2, $a0, 12            ; bound check
beq    $a2, $zero, default
sll    $a0, $a0, 2
lui    $at, 0x8B3
addu   $at, $at, $a0
lw     $at, 5416($at)          ; table at 0x08B31528 in .rodata
jr     $at
```

psprecomp's jump-table matcher does not recognise this `$at`-relative form,
which is why none resolve. The code still runs, because every label is
registered and `jr $at` becomes `psp_dispatch`. It is slower, though, and it
leaves case targets that no other path reaches undiscovered. Recognising the
form is a small fork change with a large payoff.

**Does the C compile?** Compiling `bbs_funcs.c` with AppleClang 16 at `-O0`
fails with exactly three errors, all emitter bugs:

1. `psp_vimm(0, -1f)` and `psp_vimm(0, 2f)` (at `0x08AF98F0`, `0x08AF994C`,
   both real `.text`): `vfim` immediates that happen to be integral are
   printed without a decimal point.
2. `goto L_08B5FA64` across functions, inside the false-positive "function"
   `0x08B46510` (11 instructions, 103,768 bytes, in `.rodata`).

With those three lines patched, the whole file compiles cleanly. As a single
84 MB translation unit, though, it takes 7 min 12 s at `-O0` on one core
(421 s CPU) and produces a 34.9 MB object, and optimised builds will be
slower still. The emitter has to split its output into many files (see
[architecture.md](architecture.md#build-layout)).

### VFPU

Counted directly over `.text` words by major opcode:

| Class | Words |
|---|---:|
| `lv.q` / `sv.q` | 4,870 / 5,174 |
| `lv.s` / `sv.s` | 549 / 498 |
| VFPU4 (single-operand, `vrot`, `vcst`, `vf2i`/`vi2f`, `vcmov`, …) | 2,078 |
| VFPU6 (`vmmul`, `vtfm`, `vcrsp`/`vqmul`, `vmmov`, `vmidt`, …) | 961 |
| VFPU1 (`vmul`, `vdot`, `vscl`, `vhdp`, `vcrs`, `vdet`) | 896 |
| VFPU0 (`vadd`, `vsub`, `vdiv`, …) | 413 |
| VFPU5 (`vpfx*`, `viim`, `vfim`) | 395 |
| COP2 (`mfv`, `mtv`, `bvf`/`bvt`, …) | 352 |
| VFPU3 (`vcmp`, `vmin`/`vmax`, `vsge`/`vslt`, …) | 292 |
| VFPU7 (`vnop`, `vflush`, `vsync`) | 41 |
| **Total** | **16,519 (1.99% of `.text`)** |

psprecomp's own summary ("1,764 of 754,701 instructions are VFPU and emit as
traps") mixes in the false-positive functions. Restricted to `.text`, the
emitter's `psp_unimplemented` sites are:

| Unsupported in `.text` | Sites |
|---|---:|
| `mtv` | 217 |
| `vrot` | 177 |
| VFPU encodings the decoder does not name (`vfpu?`) | 121 |
| `vcmov` | 61 |
| `mfv` | 32 |
| `vcrsp` | 30 |
| `vsge`, `vslt` | 6, 6 |
| `vf2in` | 4 |
| **VFPU total** | **654** |
| `round.w.s`, `floor.w.s`, `ceil.w.s` (FPU, not VFPU) | 20, 14, 1 |
| `break` (genuine traps in the game) | 36 |

psprecomp counts 1,082 functions (4%) that touch the VFPU. Everything else in the table is
already emitted as calls into `vfpu.c`, so the remaining work is about ten
opcodes plus the unnamed encodings.

### Imports

281 functions in 27 libraries. The per-NID table, with call-site counts and
status, is in [phase0-imports.md](phase0-imports.md). By library:

| Library | Imports | In psprecomp | Missing | Notes |
|---|---:|---:|---:|---|
| ThreadManForUser | 48 | 18 | 30 | Mutexes (`LockMutexCB` 361 sites, `UnlockMutex` 519), alarms, mailboxes, `SleepThread`/`WakeupThread`, `WaitSemaCB` |
| sceMpeg | 29 | 0 | 29 | Movies (PSMF, H.264 + ATRAC3+), YCbCr decode path |
| sceSasCore | 27 | 21 | 6 | |
| sceUtility | 24 | 0 | 24 | Savedata, MsgDialog, OSK, Netconf, GamedataInstall, LoadModule |
| IoFileMgrForUser | 23 | 9 | 14 | Async I/O (`ReadAsync`, `WaitAsyncCB`), `Devctl` (32 sites), `Ioctl` (PGD key), `Getstat` |
| sceAudio | 13 | 9 | 4 | |
| sceGe_user | 11 | 11 | 0 | |
| sceNetAdhocMatching / Adhocctl / Adhoc / Net / WlanDrv | 11 / 10 / 6 / 4 / 1 | 0 | 32 | Ad-hoc multiplayer (Mirage Arena) |
| sceAtrac3plus | 9 | 0 | 9 | BGM and voice |
| Kernel_Library | 9 | 3 | 6 | LwMutex, `Memcpy`, `Memset` |
| sceUmdUser | 7 | 0 | 7 | Drive status. Trivial to fake |
| UtilsForUser | 7 | 5 | 2 | Cache range ops |
| SysMemUserForUser | 6 | 5 | 1 | |
| scePower | 6 | 0 | 6 | Clock frequency. Trivial |
| sceDisplay | 6 | 2 | 4 | `WaitVblankStart`, `GetVcount`, `GetFrameBuf`, `GetFramePerSec` |
| ModuleMgrForUser | 5 | 4 | 1 | |
| sceSuspendForUser | 5 | 3 | 2 | |
| sceCtrl, StdioForUser, InterruptManager, LoadExecForUser, sceImpose, sceRtc | 3, 3, 3, 2, 2, 1 | 2, 3, 0, 2, 0, 0 | | `InterruptManager` = vblank sub-interrupt handler |

Missing that block the first frame: the mutex family, `sceDisplay`
vblank/vcount, `sceIoDevctl`/`Getstat`/async reads, `sceIoIoctl` (PGD),
`sceUmd*` and `sceKernelGetSystemTime*`. Missing that can wait until after the
title screen: `sceMpeg`, `sceAtrac3plus`, `sceUtility` dialogs, net/adhoc.

Names: 257 NIDs resolve from pspsdk's stub files, 20 more (6.x additions such
as `sceKernelLockMutexCB`, `sceKernelTryLockLwMutex_600`,
`sceUtilityGamedataInstall*`, `sceMpegAvc*YCbCr`) by SHA-1 against
[`tools/nid_names.txt`](../tools/nid_names.txt). Four are not SHA-1-derived
6.x NIDs:

- `SysMemUserForUser 0x35669D4C`: crt0 calls it with `0x06020010`, so it is
  the SDK-version setter.
- `ModuleMgrForUser 0x8F2DF740`, `scePower 0xEBD177D6` and
  `sceAtrac3plus 0x132F1ECA`: one or two call sites each. To be identified
  from a trace.

## 3. Compiler fingerprints and leftover symbols

> **Update (Phase 1 setup):** probe compiles settled the question differently
> from the hypothesis below. The game code is **SNC** (pspsnc 1.2.7503.0 at
> `-O2` matches all probes; psp-gcc does not). See [toolchain.md](toolchain.md).
> The text below is kept as the Phase 0 record.

**No symbols.** There is no `.symtab`. The only names are the import NIDs and the standard system export entry
(two functions, by convention `module_start`/`module_stop`, plus module
variables). Leftovers that
will help naming:

- Windows source paths for the middleware: `c:/BBS/Libs/rd/common/CDev/Engine/Memory/modules/{ComplexLink,SystemHeap}.cpp`,
  `c:/BBS/Libs/rd/sd/CDev/Engine/Sd/{SdHeap.cpp,Driver/Bank.cpp,Driver/ConfigReader.cpp,Driver/PSP/AudioPSP.cpp,Driver/PSP/AtracDecoderPSP.cpp}`,
  `…/Sd/Driver/../SdUnitHeap.h`, and `SoundSystem : Version 24/Jan/2008`.
- Assert formats `%s(%d): <assert> (%s) %s` and `%s(%d):[%s] <assert> (%s) %s`,
  10 occurrences each. These are probably `__FILE__`/`__LINE__` asserts, and
  the strings they pass will name the source files of many functions.
- **Lua 5.1.2** (`$Lua: Lua 5.1.2 Copyright (C) 1994-2007 Lua.org, PUC-Rio $`,
  metamethod names, `name conflict for module '%s'`). About 177 sectors of
  `BBS0.DAT` start with Lua bytecode (`\x1bLua`). Lua's source is public (MIT)
  and identifies a large block of functions for free.
- Hundreds of script and asset names (`StartExusiaCamera`, `event/event_%s.bin`,
  `arc/preset/exsetptn.bin`, `movie/OPN.pmf`, shop tables).

**Language.** C++ throughout: `std::bad_alloc`, `std::bad_cast`,
`std::bad_typeid`, `std::type_info`, a `.cplinit` table of 100 static
constructor pointers, and `.linkonce.d` (COMDAT data, so templates or inline
statics). `.ctors`/`.dtors` are empty: each holds only the `-1`/`0` sentinel
pair, so static construction runs from `.cplinit`.

**Which compiler.** The evidence:

| Observation | Points to |
|---|---|
| Register moves are `or rd, rs, $zero` 94,059 times and `addu rd, rs, $zero` 1,359 times. GNU as expands `move` to `addu` on MIPS32, and pspsdk/psp-gcc homebrew shows `addu` | a non-GNU assembler: SN Systems |
| `li` is `ori rd, $zero, imm`; switch tables are loaded through `$at` (`lui $at; addu; lw $at, lo($at)`) | assembler macro expansion of `lw $at, table($reg)`, i.e. a GCC-style compiler driving a macro assembler |
| `sceKernelSetCompilerVersion(0x00030306)` in crt0 | read as major/minor/patch, GCC 3.3.6 (assuming the SDK packs `__GNUC__` the usual way; not verified) |
| A `.gcc_compiled_long32` marker string | GCC MIPS EABI with 32-bit `long` |
| `.eh_frame` with exactly 3 FDEs (`0x08AF2F90`, `0x08AF3564`, `0x08AF3AD8`) | one small object built with GCC's unwinder, probably SDK or runtime library code |
| `.cplinit` section, and runtime messages `a pure virtual function was called`, `terminate() called by the exception handling mechanism`, `returned from a user-defined terminate() routine` | not GCC libsupc++'s wording ("pure virtual method called"). These look like the SN/EDG-style C++ runtime |
| Prologues: `$ra` stored at the top of the frame in 3,410 functions, at offset 0 in 2,570, mid-frame in 3,341 | at least two code generators or flag sets (the SDK libraries, the game, the middleware) |

**Working hypothesis**: the game and middleware were built with the SN Systems
ProDG toolchain for PSP, i.e. SN's GCC-3.3-based compiler, SN's assembler and
SN's linker (probably the source of `.cplinit` and `.overlays`). SCE libraries and some
runtime pieces come from Sony's GCC-built SDK. SNC, SN's own EDG-based
compiler, is the alternative for the parts with the EDG-style runtime strings,
but I have not confirmed whether SNC output contains `$at` macro sequences. That is checked the way
kh1-decomp did it (docs/toolchain.md there): compile probe functions with each
candidate compiler and compare instruction for instruction. **This has to be
settled before splat/decomp work starts.** Neither candidate compiler is
installed or freely redistributable, so that step will need your input.

## 4. Data archives and how they are loaded (high level)

| File | Size | Header | Content (sector-start magics, sampled over every sector) |
|---|---:|---|---|
| `BBS0.DAT` | 688 MB | `bbsa`, version 6, a table of category records (`PC`, `NPC`, `ETC`, `MAP`, `SYS`, …) | MPEG-PS packs (`00 00 01 BA`, 85k sectors), `PAM` (animation), `SEDB` (sound), `PMO` (models), `@CTD` (text), `ARC` (nested archives), `EAD`, `TIM2`, `TXA` (textures), Lua 5.1 bytecode |
| `BBS1.DAT` | 209 MB | `\0PGD`: **encrypted** | |
| `BBS2.DAT` | 209 MB | `\0PGD`: **encrypted** | |
| `BBS3.DAT` | 235 MB | `\0PGD`: **encrypted** | |
| `BBS4.DAT` | 307 MB | `bbs4.dat`, version 6 | Three `PSMF0015` movies |

Loading, as far as the main module shows it:

- Archives are opened by the printf pattern `disc0:/PSP_GAME/USRDIR/BBS%d.DAT`,
  and `BBS0`'s `bbsa` header is the directory for all of them. The OpenKH
  community has documented the BBSA layout. We will cross-check against that
  rather than reverse it again.
- Right after opening, three call sites issue
  `sceIoIoctl(fd, 0x04100001, key, 16, NULL, 0)`. That is the firmware's
  "set PGD key" command for UMD files: from then on, `sceIoRead` on that fd
  returns decrypted data. **Our file layer must reproduce this.** The key is
  game data, never committed. See
  [architecture.md](architecture.md#file-io).
- Reads go through `sceIoReadAsync` + `sceIoWaitAsyncCB` on a streaming thread,
  guarded by `UMD_SAMPLE_IO_SEMA` / `File/UmdAccess`. `sceIoDevctl` (32 sites)
  covers UMD and memory-stick queries.
- Saves go to `ms0:/PSP/SAVEDATA/ULUS10505DATA/` through `sceUtilitySavedata*`.
  There is also an optional data install (`sceUtilityGamedataInstall*`,
  `/PSP_GAME/INSDIR`).

## 5. What this means for the plan

1. **Decryption is solved for our purposes**: we use pspdecrypt offline. The
   PGD layer of `BBS1`–`3` is the only crypto left inside the game's run, and
   it is handled at install time (architecture.md).
2. **psprecomp is a good base but needs a fork.** The changes needed for this
   game: restrict seeds to `.text`, recognise `$at` switch tables, emit split
   translation units, fix the two codegen bugs, add the missing VFPU/FPU ops,
   and recompile overlays as separate tables.
3. **The firmware surface is the bulk of the work**: 184 missing imports, plus
   a real thread scheduler (psprecomp has none, and this game relies on
   mutexes, callbacks and alarms).
4. **Before any decomp work, the compiler has to be identified** (SN ProDG
   GCC 3.3.6 vs SNC vs mixed).

## Not done in Phase 0

- No PPSSPP run yet, so nothing is cross-checked against a reference trace.
  The first-frame plan needs PPSSPP installed (and, for the Ghidra side,
  Java + Ghidra + ghidra-allegrex). Ask before installing.
- The emitted C was not linked or run. That needs the runtime work this report
  scopes.
- The PGD key source (static in `.rodata`/`.data` or computed) has not been
  traced.
