# P4: Kernel and Scheduler, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The game's boot threads run, wait and wake as on a PSP, so `khbbs` follows PPSSPP's per-thread firmware-call streams through the boot. The brief's gate, "every per-thread stream matches through the first `sceGeListEnQueue`", cannot be met without PGD decryption (Decision 0: `DiscInfoBind` reads decrypted `BBS1.DAT` before `user_main` gets that far). So P4's gate is

```bash
PYTHONPATH=tools python3 -m oracle.oracle first-divergence --iso "$KHBBS_ISO" \
    --elf game/EBOOT.dec.ELF -n 400 --until sceKernelUnlockLwMutex:3
```

in deterministic time mode (`khbbs --time det`, the default) with `--map-uids` (which `oracle.py` always passes): all 42 records up to `DiscInfoBind`'s last call before what it does depends on decrypted data match, per thread (`root` 4, `user_main` 19, `DiscInfoBind` 19; measured, Task 18). The full gate (`--until sceGeListEnQueue:1`) is run and recorded too; it passes once the PGD files are decrypted, which is P5's (decided 2026-10-19, Decision 0). Any remaining difference is either fixed or listed with its cause in `docs/bringup.md` and accepted by the user.

**Architecture:** Our kernel lives in `runtime/kernel/` and registers its handlers over psprecomp's HLE table (re-registering a NID replaces it, `third_party/psprecomp/src/hle/hle.c:17-21`); the fork's run-to-completion `threadman.c` and its tests stay as they are. Every guest thread is a host thread with its own C stack; one baton (a host mutex plus one condition variable per thread) lets exactly one of them run guest code, so `psp_cpu` stays one global register file that a context switch copies out and in, together with the VFPU state that lives outside it. Host threads, locks and the clock come through a small host interface (`runtime/include/khbbs/host.h`), implemented for POSIX in `hosts/posix/`. Time is a virtual clock (deterministic by default) with a timer queue; loop back-edges in the generated code become safe points where due timers fire and higher-priority threads preempt. The fork gets four generic hooks only: VFPU context save/load, the back-edge safe point, a firmware-call counter, and user-partition placement in sysmem. Because PPSSPP calls them before the first `sceGeListEnQueue`, this plan also pulls a thin slice of P5 forward: `sceUmd*`, `scePower*`, `sceImpose*`, a few Kernel_Library/UtilsForUser calls, and a read-only ISO-backed file layer with async reads.

**Tech Stack:** C11, CMake ≥ 3.20 + Ninja, AppleClang 16, pthreads (only in `hosts/posix/`), psprecomp fork (`34bab3c` at the start), Python 3.11+ standard library for `tools/oracle/`, PPSSPP 1.20.4 as the external oracle (`~/Applications/PPSSPPSDL.app`, driven by `tools/oracle/`).

**Context an implementer needs:**
- Read `docs/architecture.md` ("Threading", "Memory", "Verification against PPSSPP"), `docs/oracle.md` ("Firmware-call source", "Pairing calls and returns", "Running it"), `docs/bringup.md` (the P3 entry) and this plan's **Decisions** before Task 1.
- Game files are never committed. `game/` and `build/` are gitignored. Run `tools/check_no_assets.sh` before every commit.
- Work on branch `p4-kernel-scheduler` in this repo (from `main` once P2 and P3 are merged; ask the user if they are not). Fork changes go on branch `khbbs` of `github.com/ShayVD/psprecomp`, committed inside `third_party/psprecomp`. Build and test the fork alone with `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`. `build/pc` builds the fork from its working tree, so local fork commits are in use before the pin moves; the pin matters for fresh checkouts and CI (Task 3).
- Repo build and tests: `cmake -S . -B build/pc -G Ninja && cmake --build build/pc && ctest --test-dir build/pc --output-on-failure`. Without the game: `cmake -S . -B build/ci -G Ninja -DKHBBS_WITH_GAME=OFF && cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`. Python tools: `python3 -m unittest discover -s tests/tools -v`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (fork commits too).
- `runtime/` and the generated code stay platform-free (C11 and stdio; `<setjmp.h>`, `<string.h>`, `<time.h>` are C11). Only `hosts/*` touch the OS. Threads, locks, the monotonic clock and sleeping go through `khbbs_host`.
- PPSSPP is an external oracle only. Never copy or link its code. Its *behaviour*, observed through `tools/oracle/` (and the research probe below), is fair to reproduce. Where a semantic below is a best reading rather than observed, the plan says **UNVERIFIED — the gate checks it** (or "not on the boot path" when the gate cannot reach it); the code carries the same comment.
- Every address refers to ULUS10505 v1.00.
- Struct layouts and error codes come from the public PSP SDK (pspsdk `src/kernel/pspkerror.h`, `pspthreadman.h`, `pspiofilemgr_stat.h`, `psptypes.h`) unless marked otherwise; the mutex and lightweight-mutex codes are not in pspsdk (see Task 5).
- **Research probe (2026-10-19, not committed).** A scratch script stopped PPSSPP at the entry, at `user_main`'s and `DiscInfoBind`'s first instruction and after a few calls, and dumped registers, each thread's `k0` block, its stack bottom and the LwMutex work area. The values quoted as "observed" below come from it (`build/p4-research/oracle_out.json`) and from the decoded PPSSPP log of the same boot (`build/p4-research/ppsspp-log-run1.txt`). Task 18 re-checks them with the committed tools.

**This plan's code was built and run before the plan was written** (2026-10-19, in a scratch copy of the repo and the fork, nothing committed): the fork tests (52, including the three new ones), the 20 runtime tests and the 276 Python tool tests pass, repeatedly (25 runs of the runtime suite under `ctest -j8`); the game, built with it, runs the 42 records of the P4 gate exactly as PPSSPP does (checked against the decoded research log), then stops at the PGD files as Decision 0 describes. Where a step below says "measured", that is where the number comes from. Code the plan gives only as a table or a list (some test cases, some handlers) was not part of that build.

**Baseline (measured 2026-10-19 at repo `84270e6`, psprecomp `34bab3c`):**
- psprecomp registers 109 HLE functions. ThreadManForUser has 24 (run to completion: `sceKernelStartThread` runs the thread inline, a wait that would block returns `0x800201A8`, `sceKernelDelayThread(CB)` returns at once). Mutexes, LwMutexes, mailboxes, alarms, system time, `sceKernelWakeupThread`, InterruptManager, `sceUmd*`, `scePower*`, `sceImpose*`, `sceIoGetstat`/`ReadAsync`/`WaitAsyncCB`/`Ioctl`/`Devctl` are not registered at all.
- `khbbs` (P3 command, `--budget 50000000`) stops on the budget with no dispatch miss. `DiscInfoBind` spins on `sceUmdGetDriveStat` (unregistered, returns 0) → `sceUmdWaitDriveStat` → `sceKernelLockLwMutexCB` → `sceIoOpen("umd1:")` → `0x80010002` → `sceKernelUnlockLwMutex` → `sceKernelDelayThreadCB(166670)` forever; `user_main` never resumes.
- psprecomp sysmem gives the game's first block (`sceKernelAllocPartitionMemory(2, …, 3, 0x1000, 0x1000)`) `0x08C00000`, inside the image `[0x08804018, 0x09F3D7D0)`; PPSSPP gives `0x09F3E000`.
- `module_start` runs with no thread object on a stack at `0x08400000` in the kernel partition, `$k0 = 0`. The "NULL write32 at 0x4 inside 0x08AE9700" in the P3 bring-up entry is really `sw $s5, 4($k0)` at `0x08A8BF3C` in `user_main` with `$k0 = 0` (`build/pc/gen/main_funcs_050.c:8655`, the only `$k0` use in the generated code); `0x08AE9700` is the game's `memset` and returns normally.
- The generated code has 5,592 `PSP_LOOP(` sites and 3,839 `psp_dispatch(` sites; `PSP_LOOP` compiles to nothing (no `PSPRECOMP_TRACE`).
- `runtime/trace.c` writes no `thread` field, so `trace_diff.py` compares one stream per side.
- PPSSPP's boot to the first `sceGeListEnQueue` is 74 firmware calls: `root` 4, `user_main` 28, `DiscInfoBind` 42 (decoded log, records 0–151). Section "PPSSPP's boot path" below lists them.

**PPSSPP's boot path (decoded log; thread, call = return):**

```
root:         0x35669D4C(0x06020010)=0  sceKernelSetCompilerVersion(0x30306)=0
              sceKernelCreateThread("user_main", 0x08A8BE6C, 0x13, 0x37000, 0x80004000, 0)=0x114
              sceKernelStartThread(0x114, 0x21, 0x09FFFED0)=0    <- returns only after DiscInfoBind blocks
user_main:    sceKernelAllocPartitionMemory(2, .., 3, 0x1000, 0x1000)=0x115  sceKernelGetBlockHeadAddr(0x115)=0x09F3E000
              sceKernelCreateMutex x3 (attr 0x300)=0x116..0x118
              sceImposeGetLanguageMode(0x09FFF650, 0x09FFF654)=0  sceImposeSetLanguageMode(1, 1)=0
              sceKernelCreateCallback(.., 0x08816C40, 0)=0x119  sceKernelRegisterExitCallback(0x119)=0
              sceKernelCreateEventFlag(.., 0x200, 1, 0)=0x11A  sceKernelCreateCallback(.., 0x08816C68, 0)=0x11B
              scePowerRegisterCallback(-1, 0x11B)=0  scePowerIsPowerOnline()=1
              sceUmdCheckMedium()=1  sceUmdActivate(1, "disc0:")=0  sceUmdWaitDriveStat(0x20)=0
              sceKernelCreateLwMutex(0x09A7A6C4, .., 0x100, 0, 0)=0
              sceKernelCreateThread("DiscInfoBind", 0x0880F750, 0x14, 0x1000, 0, 0)=0x11D  sceKernelStartThread(0x11D, 0, 0)=0
              sceKernelDelayThreadCB(33334)        <- the power callback 0x08816C68 runs here with (1, 0x10E4, 0); blocks
DiscInfoBind: sceIoGetstat(0x09FC8A20, 0x09FC8980)=0  sceUmdGetDriveStat()=0x32  sceKernelLockLwMutexCB(0x09A7A6C4, 1, 0)=0
              sceIoOpen("umd1:", 1)=3  sceKernelUnlockLwMutex(0x09A7A6C4, 1)=0  sceUmdGetDriveStat()=0x32
              sceKernelLockLwMutexCB=0  sceIoLseek(3, 0xE2B0, 0)=0xE2B0  sceIoReadAsync(3, 0x09A42600, 0x70)=0
              sceIoWaitAsyncCB(3, 0x09FC89B8)=0  sceKernelUnlockLwMutex=0
              3 x { sceIoGetstat(0x09FC8A20, 0x09FC8180)=0  sceIoOpen(0x09FC8A20, 0x40000001)=4..6
                    sceIoIoctl(fd, 0x04100001, key, 16, 0, 0)=0  sceUmdGetDriveStat()=0x32  sceKernelLockLwMutexCB=0
                    sceIoReadAsync(fd, 0x09FC8200, 0x12)=0  sceIoWaitAsyncCB(fd, 0x09FC81B8)=0  sceKernelUnlockLwMutex=0 }
              sceIoGetstat=0  sceUmdGetDriveStat()=0x32  sceKernelLockLwMutexCB=0  sceIoLseek(3, 0xAE270, 0)=0xAE270
              sceIoReadAsync(3, 0x09FC8200, 1)=0  sceIoWaitAsyncCB(3, 0x09FC81B8)=0  sceKernelUnlockLwMutex=0
              (entry returns: dormant)
user_main:    (DelayThreadCB returns 0)  sceKernelWaitThreadEndCB(0x11D, 0)=0  sceKernelDeleteThread(0x11D)=0
              sceKernelCreateCallback(.., 0x08813F4C, 0)=0x126  sceIoDevctl(.., 0x02415821, 0x09FFF640, 4, 0, 0)=0
              sceGeEdramGetAddr()=0x04000000  sceKernelCreateEventFlag(.., 0x200, 1, 0)=0x127
              sceGeSetCallback(0x09FFF620)=0  sceGeListEnQueue(0x08B54680, 0, -1, 0)=0x35000000   <- the gate
```

`root` returns from `module_start` while `DiscInfoBind` waits in its first `sceIoGetstat`, and PPSSPP deletes it (the thread-return path calls `sceKernelDeleteThread` internally).

**Observed thread layout (research probe):**

| | root | user_main | DiscInfoBind | idle0 |
|---|---|---|---|---|
| stack `[base, top)` | `0x09FFFC00`–`0x0A000000` | `0x09FC8C00`–`0x09FFFC00` | `0x09FC7C00`–`0x09FC8C00` | `0x083FF000`–`0x08400000` |
| `$k0` (= top − 0x100) | `0x09FFFF00` | `0x09FFFB00` | `0x09FC8B00` | `0x083FFF00` |
| `$a0`, `$a1` at entry | `0x21`, `0x09FFFED0` | `0x21`, `0x09FFFAD0` | `0`, `0` | — |
| `$sp` at entry | `0x09FFFE90` | `0x09FFFA90` | `0x09FC8AC0` | — |
| `$ra`, `$fp` at entry | `0x08000030`, `0xDEADBEEF` | `0x09FFFA90`, `0x09FFFA90` | `0x09FC8AC0`, `0x09FC8AC0` | `0x08000020` |

- `k0` block: words `k0+0x00`..`k0+0xF4` are 0 except `k0+0xC0` = thread uid and `k0+0xC8` = stack base; `k0+0xF8`..`k0+0xFF` keep the stack fill (`0xFF`). The whole stack is filled with `0xFF`, and the first word of the stack (its lowest address) is the thread uid.
- Arguments: `argp' = k0 − round_up(arglen, 16)`, `arglen` bytes copied there (the rest keeps the fill), `$sp = (argp' or k0) − 0x40`.
- Every started thread except `root` has a 16-byte stub at `$sp`: `0x03E00008 0x010C000C 0x1000FFFF 0x00000000` (`jr $ra`, a `syscall`, `b .`, `nop`), and `$ra = $fp = $sp`. Every other GPR and `hi`/`lo` is `0xDEADBEEF`.
- LwMutex work area at `0x09A7A6C4` after `sceKernelCreateLwMutex(…, 0x100, 0, 0)`: `00000000 00000000 00000100 00000000 0000011C 00000000 00000000 00000000`; after `DiscInfoBind` (uid `0x11D`) locks it with count 1: `00000001 0000011D 00000100 00000000 0000011C …`.

**Decisions taken in this plan:**
0. **The brief's gate needs PGD decryption, so P4's gate stops before it (a fact that contradicts the brief).** The brief expected PGD not to matter before the first `sceGeListEnQueue`. Measured with this plan's code: `DiscInfoBind` opens `disc0:/PSP_GAME/USRDIR/BBS1.DAT` (`sceIoOpen(…, 0x40000001)`), sets its key (`sceIoIoctl(fd, 0x04100001, key, 16)`) and reads 18 bytes, which PPSSPP decrypts and khbbs returns raw (`00 50 47 44 01 00 00 00 …`, "\0PGD"). PPSSPP's `DiscInfoBind` goes on to `BBS2.DAT` and `BBS3.DAT` and a sector read at `0xAE270`; khbbs's returns, and `user_main`, which waits for its result in a `sceKernelDelayThreadCB(33334)` loop, never reaches `sceGeListEnQueue`. Everything before that read matches PPSSPP (42 records). **Decided (user, 2026-10-19): option (a).** P4's gate is `--until sceKernelUnlockLwMutex:3`; the brief's gate (`--until sceGeListEnQueue:1`) moves to P5, which owns `tools/pgd_decrypt` (offline, keys from the gitignored `keys/`). The alternative, pulling PGD into P4, was declined. Task 18 runs and records both gates.
1. **Kernel over the fork's table.** `runtime/kernel/` registers every NID in the table below with `psp_hle_register`, after `psp_hle_init()`. This also re-registers every ThreadMan NID psprecomp registers (including `sceKernelSuspendThread`, `ResumeThread`, `GetThreadCurrentPriority`, `ChangeCurrentThreadAttr`, `GetThreadStackFreeSize`, which the game does not import), so no call reaches the fork's run-to-completion state. The fork's `threadman.c` and `tests/test_hle.c` are untouched.
2. **Fork hooks, each test-first in the fork:** `psp_vfpu_save`/`psp_vfpu_load` (Task 1); the back-edge safe point and `psp_hle_call_count()` (Task 2); `psp_sysmem_set_range`/`psp_sysmem_reserve` (Task 3). **`psp_hle_call_count()` is an addition to the brief's list:** deterministic time charges 1 µs per firmware call, and the fork's own handlers (`sceGe*`, `sceCtrl*`, …) never pass through our code; both HLE hooks are taken (trace and snapshot), so a counter in `psp_hle_call` is the smallest generic way to see every call.
3. **Back-edge: a countdown, not a flag.** `PSP_LOOP(a)` becomes `if (--psp_backedge_countdown < 0) psp_backedge(a)` (one read-modify-write and a branch; on x86 and ARM a single decrement-and-test of a global). The brief allowed either; the countdown is chosen because in deterministic mode every back-edge must count toward the clock and nothing outside the running thread could set a flag at the right moment, and because only the baton holder ever writes the counter, so there is no cross-thread write and no data race (a flag set by a timer thread would be one). The handler re-arms it to 63, so one call in 64 back-edges leaves the hot path, and in deterministic mode one such period is 1 µs (the brief's 1/64 µs per back-edge). With `PSPRECOMP_TRACE` and `PSPRECOMP_BACKEDGE` both set, a back-edge is recorded *and* serviced (record first): the brief's "trace keeps priority" is kept in that tracing still happens, and losing scheduling in a trace build would hang every busy-wait.
4. **The idle loop runs on `idle0`, not on the main host thread.** The brief puts "sleeps the main host thread" in real mode; but the main thread runs no guest code, and interrupt handlers that fire with nothing runnable must run on a thread named `idle0` (PPSSPP does, and their calls carry that name). So `idle0` is a scheduler thread at priority `0x80` (below every user priority), always ready, whose body (host code) advances the clock to the next timer (det) or sleeps until it (real), fires due timers, and detects the end of the run. The main thread only starts the scheduler, waits for "run over" and joins every host thread. `idle0` gets PPSSPP's stack (`0x083FF000`, 4 KB, `k0` `0x083FFF00`) in the kernel partition. PPSSPP's `idle1` is not created, but its name is counted for the `NAME#k` rule.
5. **Interrupt handlers** (vblank sub-interrupt, alarms) run on the current thread's host thread with its context saved and restored around them, with interrupts disabled, on a dedicated 4 KB interrupt stack in the kernel partition (`0x083FE000`–`0x083FF000`), keeping the current thread's `$k0` (so `[k0+0xC0]` names the interrupted thread, as PPSSPP's traces do). **UNVERIFIED — not on the boot path:** which stack PPSSPP gives a handler. Callbacks run on the waiting thread's own stack at its current `$sp` (UNVERIFIED, not on the boot path: no callback before the gate makes a firmware call).
6. **Thread layout as observed** (table above), which is more than the brief anticipated: the `k0` block also holds the stack base at `+0xC8`, the stack's first word holds the uid, non-root threads get `$ra = $fp = $sp` with the 16-byte stub at `$sp` (the brief said "`$ra` = the exit stub"; we write PPSSPP's stub and register `$sp` in the dispatch table to a host function that exits the thread), `root` gets `$ra = 0x08000030` (also registered), unset GPRs and `hi`/`lo` are `0xDEADBEEF` (PPSSPP's fill; it makes stack snapshots comparable), FPU and VFPU registers start at 0 (UNVERIFIED, not on the boot path). When `root`'s entry returns, `root` is deleted (observed). The stack is filled with `0xFF` at creation; the `k0` block, arguments and stub are written at start (UNVERIFIED — the gate's snapshot step checks `user_main`'s stack at `sceKernelStartThread:1`).
7. **Deterministic time:** 1 µs per firmware call (`psp_hle_call_count()`), 1 µs per 64 back-edges, and when only `idle0` is runnable the clock jumps to the next timer. A run can also stop on a **guest-time limit** (`khbbs --time-limit S`, new stop `KHBBS_TIME_LIMIT`, exit 3): a game waiting in a loop of delays makes no dispatches and jumps the clock forward, so `--budget` never ends it (measured: 4.9 GB of trace in 120 s at the PGD stop). `oracle.py` passes `--time-limit 10`. Vblank every 16,683 µs (59.94 Hz), the first at 16,683 µs. Async I/O completes a fixed 1,000 µs after it is issued (UNVERIFIED — the gate checks that it changes no per-thread stream). PPSSPP also makes `sceIoGetstat` block briefly (observed: `root` ran during `DiscInfoBind`'s first `sceIoGetstat`); khbbs does not model that, since it only reorders calls *between* threads, which per-thread comparison ignores. `--time real` uses the host monotonic clock instead and is never used for oracle runs.
8. **Stops redesigned** as the brief says: every guest host thread runs in a trampoline with its own `jmp_buf`; a stop (dispatch miss past `--keep-going`, budget, guest `break`, `sceKernelExitGame`, deadlock, or every thread exited) records the first reason, wakes the main thread, and every guest thread unwinds to its own trampoline. New reasons `KHBBS_DEADLOCK` (no runnable thread and no timer pending; exit status 6) and `KHBBS_EXIT_GAME` (exit status 0); `KHBBS_RETURNED` now means "every guest thread exited" (exit status 0).
9. **Sysmem placement:** user partition `[0x08800000, 0x0A000000)`, `[0x08800000, round_up(image end, 0x100))` reserved, thread stacks and `PSP_SMEM_High` blocks from the top. `sceKernelAllocPartitionMemory`'s partition id stays unchecked: only partition 2 is observed, and inventing an error for others could only hurt.
10. **Error codes** live in `runtime/kernel/errors.h` with a `KERR_` prefix. psprecomp's `hle.h` defines some `SCE_KERNEL_ERROR_*` macros with values that differ from pspsdk (`UNKNOWN_UID 0x800201A2` is pspsdk's `DORMANT`; `ILLEGAL_MEMBLOCK 0x800201A9` is `WAIT_CANCEL`, pspsdk has `0x800201B6`; `ILLEGAL_ADDR 0x80020005`, pspsdk has `0x800200D3`), so reusing the names would either clash or inherit wrong values. **Decided (user, 2026-10-19): fix them in the fork**, in Task 3 (Step 4b), to pspsdk's values (`src/user/pspkerror.h`: `UNKNOWN_UID 0x800200CB`, `ILLEGAL_MEMBLOCK 0x800201B6`, `ILLEGAL_ADDR 0x800200D3`; checked 2026-10-19). `runtime/kernel/` still uses its own `KERR_*` names.
11. **Scope change pulled from P5 (confirmed by the user, 2026-10-19):** `sceUmd*`, `scePowerRegisterCallback`/`UnregisterCallback`/`IsPowerOnline`/`GetPllClockFrequencyInt`/`GetBatteryLifePercent`, `sceImposeGet/SetLanguageMode`, `sceKernelMemcpy`/`Memset`, `sceKernelDcacheWritebackInvalidateRange`/`IcacheInvalidateRange`, the crt0 NID `0x35669D4C` (kept unnamed: no source confirms a name), and a read-only ISO-backed file layer (`runtime/iso9660.c`; `disc0:`, `umd0:`, `umd1:`; `sceIoOpen`/`Close`/`Read`/`Write` (stdout and stderr only)/`Lseek`/`ReadAsync`/`WaitAsync`/`WaitAsyncCB`/`PollAsync`/`Getstat`/`Ioctl`/`Devctl`). Only what the boot path calls; P5 replaces and extends it. Every other device (`ms0:`) answers `0x80010002` (not found) until P5. `sceIoIoctl(0x04100001)` (PGD key) returns 0 and records the key but does not decrypt (Decision 0 is where that shows). Do not decrypt ad hoc.
12. **`scePowerRegisterCallback` notifies the callback** at once with `0x000010E4` (AC power `0x1000`, battery present `0x80`, 100% `0x64`), so it runs at the thread's next `*CB` wait: observed (the brief did not mention it). It returns the slot (0 for the first `-1` request; observed 0).
13. **Not implemented because the game does not import them:** `sceIoGetAsyncStat` (the brief lists it), `sceKernelReceiveMbx`, `sceKernelRotateThreadReadyQueue`, `sceKernelSleepThreadCB`, `sceKernelReferMutexStatus`, `sceIoOpenAsync`. Implemented although not imported: the five ThreadMan NIDs of decision 1, `sceDisplayWaitVblank` and `sceDisplayWaitVblankStartCB` (psprecomp registers them), `sceKernelNotifyCallback`, `sceKernelReferSemaStatus`, `sceKernelPollEventFlag` (the brief lists them).
14. **Trace thread names** follow `capture_hle.py`'s rule: the k-th thread (k ≥ 2) created with a name already used is `NAME#k`, counting in creation order, with `idle0`, `idle1`, `root` counted first (PPSSPP's `hle.thread.list` at the entry). Calls made with no guest thread (unit tests calling `psp_hle_call` directly) carry `"thread":null`.
15. **`--until NAME:K`** is added to `trace_diff.py` (P2's `oracle.py` has no such option) and passed through by `oracle.py`. The cut is global, in each trace's own order (completion order on both sides): everything after the K-th call of NAME is dropped, on every thread. A stream that is then shorter on one side is a divergence. At P4's cut (`DiscInfoBind`'s third `sceKernelUnlockLwMutex`) `root` has ended and `user_main` sits in `sceKernelDelayThreadCB` on both sides; at the first `sceGeListEnQueue` every other boot thread has ended (`user_main` waited for `DiscInfoBind`'s end). So a global cut is exact for both gates. `oracle.py` also passes `--iso` and `--time det` to khbbs.
16. **Phase-0 table quirk:** `docs/phase0-imports.md` lists `sceKernelGetThreadId` (`0x293B45B8`) under Kernel_Library; it is a ThreadManForUser function. HLE dispatch is by NID, so we register it under ThreadManForUser.

**UNVERIFIED semantics (each marked in the code; the gate checks the ones on the boot path):** the `0xFF` fill at creation versus start; FPU/VFPU registers at thread start; `sceKernelReferThreadStatus` fields beyond the pspsdk layout (`runClocks` and the counts are 0); `sceKernelGetThreadStackFreeSize` (counts `0xFF` bytes above the uid word); priority 0 in `sceKernelChangeThreadPriority`; `sceKernelCheckCallback`'s return (1 if any ran); semaphore wake order when the head waiter wants more than is available (every waiter that fits is woken, in queue order); the interrupt stack; vblank timing (every 16,683 µs, `sceDisplayWaitVblankStart` waits for the next boundary, `sceDisplayWaitVblank` behaves the same); async I/O latency (1,000 µs); the raw `umd1:` result unit (sectors); `SceIoStat` mode and attribute bits for UMD files (`0x216D`, `0x25`; directories `0x116D`, `0x15`) and `st_private[0]` = start sector; `sceUmdGetDriveStat` before `sceUmdActivate` (`0x12`); `sceImposeGetLanguageMode` writing 1 and 1; the mutex/LwMutex error values (accepted as UNVERIFIED by the user, 2026-10-19; the oracle confirms them when the game reaches an error path).

## NIDs P4 implements

Call sites are direct `jal`s from `docs/phase0-imports.md`. "Boot" marks a call PPSSPP makes before the first `sceGeListEnQueue`. Task = the task that implements it.

| NID | Name | Library | Sites | Boot | Task |
|---|---|---|---:|:---:|---:|
| `6B30100F` | `sceKernelUnlockMutex` | ThreadManForUser | 519 | | 13 |
| `5BF4DD27` | `sceKernelLockMutexCB` | ThreadManForUser | 361 | | 13 |
| `54F5FB11` | `sceIoDevctl` | IoFileMgrForUser | 32 | yes | 16 |
| `B7D098C6` | `sceKernelCreateMutex` | ThreadManForUser | 26 | yes | 13 |
| `68DA9E36` | `sceKernelDelayThreadCB` | ThreadManForUser | 24 | yes | 10 |
| `F8170FBE` | `sceKernelDeleteMutex` | ThreadManForUser | 21 | | 13 |
| `3F53E640` | `sceKernelSignalSema` | ThreadManForUser | 18 | | 12 |
| `34B9FA9E` | `sceKernelDcacheWritebackInvalidateRange` | UtilsForUser | 18 | | 15 |
| `1839852A` | `sceKernelMemcpy` | Kernel_Library | 15 | | 15 |
| `82BC5777` | `sceKernelGetSystemTimeWide` | ThreadManForUser | 14 | | 10 |
| `278C0DF5` | `sceKernelWaitThreadEnd` | ThreadManForUser | 14 | | 8 |
| `1FB15A32` | `sceKernelSetEventFlag` | ThreadManForUser | 13 | | 12 |
| `4E3A1105` | `sceKernelWaitSema` | ThreadManForUser | 13 | | 12 |
| `109F50BC` | `sceIoOpen` | IoFileMgrForUser | 11 | yes | 16 |
| `6B4A146C` | `sceUmdGetDriveStat` | sceUmdUser | 10 | yes | 15 |
| `ACE946E8` | `sceIoGetstat` | IoFileMgrForUser | 10 | yes | 16 |
| `EF9E4C70` | `sceKernelDeleteEventFlag` | ThreadManForUser | 9 | | 12 |
| `810C4BC3` | `sceIoClose` | IoFileMgrForUser | 9 | | 16 |
| `27EB27B8` | `sceIoLseek` | IoFileMgrForUser | 9 | yes | 16 |
| `D6DA4BA1` | `sceKernelCreateSema` | ThreadManForUser | 8 | | 12 |
| `28B6489C` | `sceKernelDeleteSema` | ThreadManForUser | 8 | | 12 |
| `446D8DE6` | `sceKernelCreateThread` | ThreadManForUser | 8 | yes | 8 |
| `F475845D` | `sceKernelStartThread` | ThreadManForUser | 8 | yes | 8 |
| `AA73C935` | `sceKernelExitThread` | ThreadManForUser | 8 | | 8 |
| `9FA03CD3` | `sceKernelDeleteThread` | ThreadManForUser | 7 | yes | 8 |
| `9ACE131E` | `sceKernelSleepThread` | ThreadManForUser | 7 | | 8 |
| `6D212BAC` | `sceKernelWaitSemaCB` | ThreadManForUser | 7 | | 12 |
| `6A638D83` | `sceIoRead` | IoFileMgrForUser | 6 | | 16 |
| `35DBD746` | `sceIoWaitAsyncCB` | IoFileMgrForUser | 6 | yes | 16 |
| `CEADEB47` | `sceKernelDelayThread` | ThreadManForUser | 6 | | 10 |
| `402FCF22` | `sceKernelWaitEventFlag` | ThreadManForUser | 6 | | 12 |
| `812346E4` | `sceKernelClearEventFlag` | ThreadManForUser | 5 | | 12 |
| `55C20A00` | `sceKernelCreateEventFlag` | ThreadManForUser | 5 | yes | 12 |
| `EDBA5844` | `sceKernelDeleteCallback` | ThreadManForUser | 5 | | 9 |
| `5F10D406` | `sceKernelCpuResumeIntr` | Kernel_Library | 5 | | 11 |
| `293B45B8` | `sceKernelGetThreadId` | ThreadManForUser | 5 | | 8 |
| `A089ECA4` | `sceKernelMemset` | Kernel_Library | 5 | | 15 |
| `6AF9B50A` | `sceUmdCancelWaitDriveStat` | sceUmdUser | 5 | | 15 |
| `A0B5A7C2` | `sceIoReadAsync` | IoFileMgrForUser | 5 | yes | 16 |
| `092968F4` | `sceKernelCpuSuspendIntr` | Kernel_Library | 4 | | 11 |
| `8EF08FCE` | `sceUmdWaitDriveStat` | sceUmdUser | 4 | yes | 15 |
| `4A9E5E29` | `sceUmdWaitDriveStatCB` | sceUmdUser | 4 | | 15 |
| `349D6D6C` | `sceKernelCheckCallback` | ThreadManForUser | 3 | | 9 |
| `369ED59D` | `sceKernelGetSystemTimeLow` | ThreadManForUser | 3 | | 10 |
| `616403BA` | `sceKernelTerminateThread` | ThreadManForUser | 3 | | 8 |
| `D59EAD2F` | `sceKernelWakeupThread` | ThreadManForUser | 3 | | 8 |
| `63632449` | `sceIoIoctl` | IoFileMgrForUser | 3 | yes | 16 |
| `42EC03AC` | `sceIoWrite` | IoFileMgrForUser | 3 | | 16 |
| `7E65B999` | `sceKernelCancelAlarm` | ThreadManForUser | 2 | | 11 |
| `6652B8CA` | `sceKernelSetAlarm` | ThreadManForUser | 2 | | 11 |
| `71BC9871` | `sceKernelChangeThreadPriority` | ThreadManForUser | 2 | | 8 |
| `E81CAF8F` | `sceKernelCreateCallback` | ThreadManForUser | 2 | yes | 9 |
| `B011B11F` | `sceKernelLockMutex` | ThreadManForUser | 2 | | 13 |
| `0D81716A` | `sceKernelPollMbx` | ThreadManForUser | 2 | | 12 |
| `17C1684E` | `sceKernelReferThreadStatus` | ThreadManForUser | 2 | | 8 |
| `840E8133` | `sceKernelWaitThreadEndCB` | ThreadManForUser | 2 | yes | 8 |
| `56202973` | `sceUmdWaitDriveStatWithTimer` | sceUmdUser | 2 | | 15 |
| `984C27E7` | `sceDisplayWaitVblankStart` | sceDisplay | 2 | | 11 |
| `9C6EAAD7` | `sceDisplayGetVcount` | sceDisplay | 2 | | 11 |
| `19CFF145` | `sceKernelCreateLwMutex` | ThreadManForUser | 1 | yes | 13 |
| `60107536` | `sceKernelDeleteLwMutex` | ThreadManForUser | 1 | | 13 |
| `BEA46419` | `sceKernelLockLwMutex` | Kernel_Library | 1 | | 13 |
| `1FC64E09` | `sceKernelLockLwMutexCB` | Kernel_Library | 1 | yes | 13 |
| `37431849` | `sceKernelTryLockLwMutex_600` | Kernel_Library | 1 | | 13 |
| `15B6446B` | `sceKernelUnlockLwMutex` | Kernel_Library | 1 | yes | 13 |
| `8125221D` | `sceKernelCreateMbx` | ThreadManForUser | 1 | | 12 |
| `86255ADA` | `sceKernelDeleteMbx` | ThreadManForUser | 1 | | 12 |
| `E9B3061E` | `sceKernelSendMbx` | ThreadManForUser | 1 | | 12 |
| `FCCFAD26` | `sceKernelCancelWakeupThread` | ThreadManForUser | 1 | | 8 |
| `809CE29B` | `sceKernelExitDeleteThread` | ThreadManForUser | 1 | | 8 |
| `383F7BCC` | `sceKernelTerminateDeleteThread` | ThreadManForUser | 1 | | 8 |
| `58B1F937` | `sceKernelPollSema` | ThreadManForUser | 1 | | 12 |
| `A66B0120` | `sceKernelReferEventFlagStatus` | ThreadManForUser | 1 | | 12 |
| `328C546A` | `sceKernelWaitEventFlagCB` | ThreadManForUser | 1 | | 12 |
| `0DDCD2C9` | `sceKernelTryLockMutex` | ThreadManForUser | 1 | | 13 |
| `4AC57943` | `sceKernelRegisterExitCallback` | LoadExecForUser | 1 | yes | 9 |
| `05572A5F` | `sceKernelExitGame` | LoadExecForUser | 1 | | 7 |
| `FB8E22EC` | `sceKernelEnableSubIntr` | InterruptManager | 1 | | 11 |
| `CA04A2B9` | `sceKernelRegisterSubIntrHandler` | InterruptManager | 1 | | 11 |
| `D61E6961` | `sceKernelReleaseSubIntrHandler` | InterruptManager | 1 | | 11 |
| `91E4F6A7` | `sceKernelLibcClock` | UtilsForUser | 1 | | 10 |
| `71EC4271` | `sceKernelLibcGettimeofday` | UtilsForUser | 1 | | 10 |
| `27CC57F0` | `sceKernelLibcTime` | UtilsForUser | 1 | | 10 |
| `C2DF770E` | `sceKernelIcacheInvalidateRange` | UtilsForUser | 1 | | 15 |
| `35669D4C` | (unnamed, crt0 SDK-version setter) | SysMemUserForUser | 1 | yes | 15 |
| `C6183D47` | `sceUmdActivate` | sceUmdUser | 1 | yes | 15 |
| `46EBB729` | `sceUmdCheckMedium` | sceUmdUser | 1 | yes | 15 |
| `04B7766E` | `scePowerRegisterCallback` | scePower | 1 | yes | 15 |
| `DFA8BAF8` | `scePowerUnregisterCallback` | scePower | 1 | | 15 |
| `87440F5E` | `scePowerIsPowerOnline` | scePower | 1 | yes | 15 |
| `34F9C463` | `scePowerGetPllClockFrequencyInt` | scePower | 1 | | 15 |
| `2085D15D` | `scePowerGetBatteryLifePercent` | scePower | 1 | | 15 |
| `24FD7BCF` | `sceImposeGetLanguageMode` | sceImpose | 1 | yes | 15 |
| `36AA6E91` | `sceImposeSetLanguageMode` | sceImpose | 1 | yes | 15 |
| `E23EEC33` | `sceIoWaitAsync` | IoFileMgrForUser | 1 | | 16 |
| `3251EA56` | `sceIoPollAsync` | IoFileMgrForUser | 1 | | 16 |
| `9944F31F` | `sceKernelSuspendThread` | ThreadManForUser | 0 | | 8 |
| `75156E8F` | `sceKernelResumeThread` | ThreadManForUser | 0 | | 8 |
| `94AA61EE` | `sceKernelGetThreadCurrentPriority` | ThreadManForUser | 0 | | 8 |
| `EA748E31` | `sceKernelChangeCurrentThreadAttr` | ThreadManForUser | 0 | | 8 |
| `52089CA1` | `sceKernelGetThreadStackFreeSize` | ThreadManForUser | 0 | | 8 |
| `C11BA8C4` | `sceKernelNotifyCallback` | ThreadManForUser | 0 | | 9 |
| `BC6FEBC5` | `sceKernelReferSemaStatus` | ThreadManForUser | 0 | | 12 |
| `30FD48F0` | `sceKernelPollEventFlag` | ThreadManForUser | 0 | | 12 |
| `36CDFADE` | `sceDisplayWaitVblank` | sceDisplay | 0 | | 11 |
| `46F186C3` | `sceDisplayWaitVblankStartCB` | sceDisplay | 0 | | 11 |

Left to psprecomp (not re-registered): `sceKernelAllocPartitionMemory`, `FreePartitionMemory`, `GetBlockHeadAddr`, `SetCompilerVersion`, `Printf` (placement fixed through Task 3's API), `sceGe*`, `sceDisplaySetMode`/`SetFrameBuf`, `sceCtrl*`, `sceAudio*`, `sceSas*`, `StdioForUser`, `ModuleMgrForUser`, `sceKernelPowerLock`/`Unlock`/`Tick`, `sceKernelDcacheWritebackAll`/`WritebackInvalidateAll`, `sceIoDopen`/`Dread`/`Dclose`/`Rename` (ms0 host directory; P5).

## File map

| File | Responsibility |
|---|---|
| `third_party/psprecomp/include/psprecomp/vfpu.h`, `src/vfpu.c`, `src/vfpu_internal.h`, `src/vfpu_ops.c` | (fork) `psp_vfpu_ctx`, `psp_vfpu_save`/`load`/`ctx_init` |
| `third_party/psprecomp/include/psprecomp/dispatch.h`, `src/dispatch.c` | (fork) `psp_backedge_countdown`, `psp_backedge`, `psp_set_backedge`, `PSP_BACKEDGE_CHECK` |
| `third_party/psprecomp/tools/allegrexrecomp/emit.c` | (fork) `PSP_LOOP` under `PSPRECOMP_BACKEDGE` |
| `third_party/psprecomp/include/psprecomp/hle.h`, `src/hle/hle.c`, `src/hle/sysmem.c` | (fork) `psp_hle_call_count`, `psp_sysmem_set_range`, `psp_sysmem_reserve` |
| `third_party/psprecomp/tests/test_vfpu_ctx.c`, `test_backedge.c`, `test_sysmem.c`, `test_emit.c`, `tests/CMakeLists.txt` | (fork) tests |
| `tools/setup_toolchain.sh`, `docs/setup.md`, `docs/next-session.md` | the fork pin |
| `runtime/include/khbbs/host.h`, `runtime/host.c` | host interface (threads, mutex, condvar, clock) |
| `hosts/posix/host_posix.c`, `hosts/posix/include/khbbs/host_posix.h`, `hosts/posix/CMakeLists.txt` | POSIX backend (pthreads, `clock_gettime`) |
| `runtime/kernel/errors.h` | `KERR_*` error codes |
| `runtime/kernel/nids.h` | every NID P4 registers, by name |
| `runtime/kernel/khle.h` | `KENTER`/`KRET`/`KREG` helpers for handlers |
| `runtime/kernel/sched.h`, `sched.c` | threads, baton, context switch, ready queue, waits, `idle0`, stops |
| `runtime/kernel/ktime.h`, `ktime.c` | virtual clock, timer queue, back-edge service, Delay/SystemTime/Libc time |
| `runtime/kernel/thread.h`, `thread.c` | thread layout, sysmem placement, ThreadMan thread NIDs |
| `runtime/kernel/kernel.h`, `kernel.c`, `runtime/include/khbbs/kernel.h` | reset and registration of all families, `sceKernelExitGame` |
| `runtime/kernel/callback.c`, `callback.h` | callbacks, delivery, exit callback |
| `runtime/kernel/intr.c` | vblank, sub-interrupts, alarms, `sceDisplay` vblank NIDs, `CpuSuspendIntr` |
| `runtime/kernel/sema.c`, `evflag.c`, `mbx.c` | semaphores, event flags, mailboxes |
| `runtime/kernel/mutex.c` | mutexes and lightweight mutexes |
| `runtime/kernel/fw.c` | `sceUmd*`, `scePower*`, `sceImpose*`, Kernel_Library memcpy/memset, cache no-ops, crt0 NID |
| `runtime/iso9660.c`, `runtime/include/khbbs/iso9660.h` | read-only ISO 9660 reader |
| `runtime/kernel/io.c` | IoFileMgr over the ISO, async completion |
| `runtime/boot.c`, `runtime/include/khbbs/boot.h` | boot on `root`, new stop paths, time mode |
| `runtime/trace.c`, `runtime/include/khbbs/trace.h` | `"thread"` field |
| `runtime/CMakeLists.txt`, `CMakeLists.txt`, `hosts/pc/CMakeLists.txt`, `tests/runtime/CMakeLists.txt` | build wiring |
| `cmake/gen.cmake` | `-DPSPRECOMP_BACKEDGE` |
| `hosts/pc/main.c` | `--iso`, `--time`, host, kernel, exit codes |
| `tests/runtime/kcall.h`, `isogen.h` | test helpers (firmware calls from synthetic guest code; synthetic ISO) |
| `tests/runtime/test_host.c`, `test_sched.c`, `test_layout.c`, `test_boot.c`, `test_threadman.c`, `test_callback.c`, `test_time.c`, `test_intr.c`, `test_sync.c`, `test_mutex.c`, `test_trace.c`, `test_fw.c`, `test_iso9660.c`, `test_io.c` | runtime tests |
| `tests/tools/test_kernel_nids.py` | every `NID_` in `nids.h` is SHA-1 of its name |
| `tools/oracle/trace_diff.py`, `tools/oracle/oracle.py`, `tests/tools/test_trace_diff.py`, `tests/tools/test_oracle.py` | `--until`, khbbs `--iso`/`--time` |
| `docs/bringup.md`, `docs/plan.md`, `docs/architecture.md`, `docs/oracle.md`, `docs/next-session.md` | results |

---
### Task 1: VFPU context save and load (fork)

The pending `vpfxs`/`vpfxt`/`vpfxd` prefixes (`src/vfpu.c:12-13`) and the `vrnd*` generator (`src/vfpu_ops.c:324-325`) are file-static and have no getter. A context switch must carry them with `psp_cpu`.

**Files:**
- Modify: `third_party/psprecomp/include/psprecomp/vfpu.h` (after the prefix block)
- Modify: `third_party/psprecomp/src/vfpu.c`, `src/vfpu_internal.h`, `src/vfpu_ops.c`
- Create: `third_party/psprecomp/tests/test_vfpu_ctx.c`
- Modify: `third_party/psprecomp/tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_vfpu_ctx.c`:

```c
/* psp_vfpu_save/psp_vfpu_load: the VFPU state outside psp_cpu (pending
 * prefixes, the vrnd generator) moves with a context. Synthetic values only. */
#include "psprecomp/vfpu.h"
#include "psprecomp/cpu.h"

#include <stdio.h>

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
#define C000 0x00u
#define C020 0x02u
#define S100 0x04u                       /* matrix 1: draws do not touch C000/C020 */

static uint32_t draw(void) { psp_vrnd(PSP_VR_I, S100, 1); return psp_mfv(S100); }

int main(void) {
    float o[4];
    psp_vfpu_reset();
    const uint32_t r1 = draw(), r2 = draw(), r3 = draw();
    CHECK(r1 != r2 && r2 != r3, "the generator moves");

    psp_vfpu_ctx init, a, b;
    psp_vfpu_reset();
    psp_vfpu_ctx_init(&init);
    CHECK(!init.prefix_set[0] && !init.prefix_set[1] && !init.prefix_set[2], "init: nothing pending");

    /* A: one draw taken, a swizzle pending on vs. */
    draw();
    psp_vfpu_set_prefix(PSP_VPFX_S, 0x1B);                 /* [W, Z, Y, X] */
    psp_vfpu_save(&a);
    CHECK(a.prefix_set[PSP_VPFX_S] && a.prefix[PSP_VPFX_S] == 0x1B, "A holds the S prefix");

    /* B: two draws taken, a saturate-and-mask pending on vd. */
    psp_vfpu_reset();
    draw(); draw();
    psp_vfpu_set_prefix(PSP_VPFX_D, 0x00040D);             /* [0:1, -1:1, M, -] */
    psp_vfpu_save(&b);

    /* Back to A: its prefix applies to the next op, then its generator continues. */
    psp_vfpu_load(&a);
    CHECK(psp_vfpu_prefix_pending(), "A's prefix is pending again");
    set4(C000, 1, 2, 3, 4);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(EQ4(o, 4, 3, 2, 1), "A's swizzle: %g %g %g %g", o[0], o[1], o[2], o[3]);
    CHECK(draw() == r2, "A's second draw");

    /* B: its destination prefix, then its third draw. */
    psp_vfpu_load(&b);
    set4(C000, 1.5f, -2, 7, -0.25f);
    set4(C020, 9, 9, 9, 9);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    get4(C020, o);
    CHECK(EQ4(o, 1, -1, 9, -0.25f), "B's saturate and mask: %g %g %g %g", o[0], o[1], o[2], o[3]);
    CHECK(draw() == r3, "B's third draw");

    /* The init context draws from the seed and has nothing pending. */
    psp_vfpu_load(&init);
    CHECK(!psp_vfpu_prefix_pending(), "init loads with nothing pending");
    CHECK(draw() == r1, "init draws from the seed");

    /* A zero generator state would stick at zero: load() takes the seed instead. */
    psp_vfpu_ctx z = init;
    z.rnd = 0;
    psp_vfpu_load(&z);
    CHECK(draw() == r1, "a zero rnd state loads as the seed");

    printf("%s\n", failures ? "test_vfpu_ctx: FAILED" : "test_vfpu_ctx: ok");
    return failures ? 1 : 0;
}
```

Append to `third_party/psprecomp/tests/CMakeLists.txt`:

```cmake
add_executable(test_vfpu_ctx test_vfpu_ctx.c)
target_link_libraries(test_vfpu_ctx PRIVATE psprecomp)
add_test(NAME vfpu_ctx COMMAND test_vfpu_ctx)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: compile error, `unknown type name 'psp_vfpu_ctx'`.

- [ ] **Step 3: Implement**

In `include/psprecomp/vfpu.h`, after `void psp_vfpu_reset(void);`:

```c
/* The VFPU state that is not in psp_cpu: the pending prefixes and the vrnd*
 * generator. A host that runs several guest threads on the one psp_cpu saves
 * it with the registers at every switch. Plain data, no padding. */
typedef struct {
    uint32_t prefix[3];       /* vpfxs, vpfxt, vpfxd values (PSP_VPFX_S/_T/_D) */
    uint32_t prefix_set[3];   /* 1 where that prefix is pending */
    uint32_t rnd;             /* the vrnd* generator state; 0 loads as the seed */
} psp_vfpu_ctx;
void psp_vfpu_save(psp_vfpu_ctx *out);
void psp_vfpu_load(const psp_vfpu_ctx *in);
/* The state psp_vfpu_reset leaves: nothing pending, the generator at its seed. */
void psp_vfpu_ctx_init(psp_vfpu_ctx *out);
```

In `src/vfpu_internal.h`, after `void psp_vfpu__rnd_reset(void);`:

```c
/* The vrnd* generator state, for psp_vfpu_save/load (vfpu.c). set(0) sets the seed. */
uint32_t psp_vfpu__rnd_get(void);
void     psp_vfpu__rnd_set(uint32_t state);
uint32_t psp_vfpu__rnd_seed(void);
```

In `src/vfpu_ops.c`, after `psp_vfpu__rnd_reset`:

```c
uint32_t psp_vfpu__rnd_get(void) { return g_rnd; }
void     psp_vfpu__rnd_set(uint32_t state) { g_rnd = state ? state : RND_SEED; }
uint32_t psp_vfpu__rnd_seed(void) { return RND_SEED; }
```

In `src/vfpu.c`, after `psp_vfpu_prefix_pending`:

```c
void psp_vfpu_save(psp_vfpu_ctx *out) {
    for (int i = 0; i < 3; i++) {
        out->prefix[i] = g_prefix[i];
        out->prefix_set[i] = g_prefix_set[i] != 0;
    }
    out->rnd = psp_vfpu__rnd_get();
}

void psp_vfpu_load(const psp_vfpu_ctx *in) {
    for (int i = 0; i < 3; i++) {
        g_prefix[i] = in->prefix[i];
        g_prefix_set[i] = in->prefix_set[i] != 0;
    }
    psp_vfpu__rnd_set(in->rnd);
}

void psp_vfpu_ctx_init(psp_vfpu_ctx *out) {
    memset(out, 0, sizeof *out);
    out->rnd = psp_vfpu__rnd_seed();
}
```

- [ ] **Step 4: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: `100% tests passed`, including `vfpu_ctx`.

- [ ] **Step 5: Commit (fork)**

```bash
git -C third_party/psprecomp add include/psprecomp/vfpu.h src/vfpu.c src/vfpu_internal.h src/vfpu_ops.c \
    tests/test_vfpu_ctx.c tests/CMakeLists.txt
git -C third_party/psprecomp commit -m "vfpu: psp_vfpu_save/load for the state outside psp_cpu

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Back-edge safe point and firmware-call counter (fork)

**Files:**
- Modify: `third_party/psprecomp/include/psprecomp/dispatch.h`, `src/dispatch.c`
- Modify: `third_party/psprecomp/tools/allegrexrecomp/emit.c` (the generated header text, lines 1098-1115)
- Modify: `third_party/psprecomp/include/psprecomp/hle.h`, `src/hle/hle.c`
- Create: `third_party/psprecomp/tests/test_backedge.c`
- Modify: `third_party/psprecomp/tests/test_emit.c`, `tests/CMakeLists.txt`

- [ ] **Step 1: Write the failing tests**

`third_party/psprecomp/tests/test_backedge.c`:

```c
/* The back-edge safe point and the firmware-call counter. Synthetic only. */
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"

#include <stdio.h>

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); failures++; } } while (0)

static int hits;
static uint32_t last;
/* A handler that wants every third back-edge. */
static void on_edge(uint32_t addr) { hits++; last = addr; psp_backedge_countdown = 2; }
static void fake(void) { psp_ret(1); }

int main(void) {
    /* No handler: the first back-edge parks the counter, the rest cost a decrement. */
    for (int i = 0; i < 10; i++) PSP_BACKEDGE_CHECK(0x08804000u);
    CHECK(psp_backedge_countdown > 1000000, "default handler parks the countdown (%d)", (int)psp_backedge_countdown);

    /* Installing a handler arms the next back-edge. */
    psp_set_backedge(on_edge);
    for (uint32_t i = 0; i < 9; i++) PSP_BACKEDGE_CHECK(0x08804000u + 4 * i);
    CHECK(hits == 3, "back-edges 0, 3, 6 reach the handler (%d)", hits);
    CHECK(last == 0x08804018u, "the handler sees the target address (0x%08X)", last);

    /* Removing it parks the counter again. */
    psp_set_backedge(NULL);
    for (int i = 0; i < 10; i++) PSP_BACKEDGE_CHECK(0x08804000u);
    CHECK(hits == 3, "no calls after removal (%d)", hits);

    /* psp_dispatch_reset removes the handler too. */
    psp_set_backedge(on_edge);
    psp_dispatch_reset();
    PSP_BACKEDGE_CHECK(0x08804000u);
    CHECK(hits == 3, "reset removes the handler (%d)", hits);

    /* Every psp_hle_call counts, registered or not. */
    psp_hle_register_unnamed(0x11111111u, "Test", fake);
    uint64_t c0 = psp_hle_call_count();
    psp_hle_call(0x11111111u);
    psp_hle_call(0x11111111u);
    psp_hle_call(0x22222222u);                     /* unregistered: prints one line */
    CHECK(psp_hle_call_count() - c0 == 3, "three calls counted (%llu)",
          (unsigned long long)(psp_hle_call_count() - c0));

    printf("%s\n", failures ? "test_backedge: FAILED" : "test_backedge: ok");
    return failures ? 1 : 0;
}
```

In `tests/test_emit.c`, inside the `if (hdr) { ... }` block after the `#define r_sp` check, add:

```c
        expect_contains(hdr, "#  define PSP_EDGE_(a) PSP_BACKEDGE_CHECK(a)",
                        "PSPRECOMP_BACKEDGE makes a back-edge a safe point");
        expect_contains(hdr, "#  define PSP_EDGE_(a) ((void)0)",
                        "without PSPRECOMP_BACKEDGE a back-edge costs nothing");
        expect_contains(hdr, "#  define PSP_LOOP(a)  do { psp_trace_loop(a); PSP_EDGE_(a); } while (0)",
                        "with PSPRECOMP_TRACE a back-edge is recorded, then checked");
        expect_contains(hdr, "#  define PSP_LOOP(a)  PSP_EDGE_(a)",
                        "without PSPRECOMP_TRACE PSP_LOOP is only the check");
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_backedge test_backedge.c)
target_link_libraries(test_backedge PRIVATE psprecomp)
add_test(NAME backedge COMMAND test_backedge)

# Generated code with the safe point on (and with tracing too) still compiles.
foreach(_defs IN ITEMS "PSPRECOMP_BACKEDGE" "PSPRECOMP_BACKEDGE;PSPRECOMP_TRACE")
    string(REPLACE ";" "_" _n "${_defs}")
    list(TRANSFORM _defs PREPEND "-D" OUTPUT_VARIABLE _flags)
    add_test(NAME switch_cc_t_strlen_${_n}
             COMMAND ${CMAKE_C_COMPILER} -std=c11 -fsyntax-only ${_flags}
                     -I${CMAKE_SOURCE_DIR}/include t_strlen_funcs.c)
    set_tests_properties(switch_cc_t_strlen_${_n} PROPERTIES FIXTURES_REQUIRED switch_gen)
endforeach()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: compile error in `test_backedge.c` (`PSP_BACKEDGE_CHECK`, `psp_set_backedge`, `psp_hle_call_count` undeclared).

- [ ] **Step 3: The safe point in dispatch**

In `include/psprecomp/dispatch.h`, after `uint64_t psp_trace_loop_hits(void);`:

```c
/* ---- back-edge safe point -------------------------------------------------
 * Generated code built with -DPSPRECOMP_BACKEDGE runs PSP_BACKEDGE_CHECK on
 * every backward branch: a decrement and a test, and a call to psp_backedge
 * when the count goes negative. A host that schedules guest threads installs
 * a handler; the handler re-arms psp_backedge_countdown itself (to N-1 to be
 * called once every N back-edges). With no handler, psp_backedge parks the
 * count at INT32_MAX. Only the thread running guest code touches the count. */
extern int32_t psp_backedge_countdown;
typedef void (*psp_backedge_fn)(uint32_t addr);
void psp_backedge(uint32_t addr);
/* Install (arms the next back-edge) or remove (NULL) the handler. */
void psp_set_backedge(psp_backedge_fn fn);
#define PSP_BACKEDGE_CHECK(a) \
    do { if (--psp_backedge_countdown < 0) psp_backedge(a); } while (0)
```

In `src/dispatch.c`, after `uint64_t psp_trace_loop_hits(void) { ... }`:

```c
int32_t psp_backedge_countdown = INT32_MAX;
static psp_backedge_fn g_backedge;

void psp_set_backedge(psp_backedge_fn fn) {
    g_backedge = fn;
    psp_backedge_countdown = fn ? 0 : INT32_MAX;
}

void psp_backedge(uint32_t addr) {
    if (g_backedge) { g_backedge(addr); return; }
    psp_backedge_countdown = INT32_MAX;
}
```

and in `psp_dispatch_reset`, after `g_miss = NULL;`, add `psp_set_backedge(NULL);`.

- [ ] **Step 4: The emitted header**

In `tools/allegrexrecomp/emit.c`, replace the text from `"/* Build with -DPSPRECOMP_TRACE to record every function entry. Costs\n"` through `"#endif\n"` (the `PSP_ENTER`…`PSP_MARK` block) with:

```c
        "/* Build with -DPSPRECOMP_TRACE to record every function entry. Costs\n"
        " * one store per call when on, and nothing at all when off. A dispatch\n"
        " * miss then reports how the code arrived, not just where it went.\n"
        " * Build with -DPSPRECOMP_BACKEDGE to make every backward branch a safe\n"
        " * point (PSP_BACKEDGE_CHECK in dispatch.h): the host's psp_backedge\n"
        " * handler runs when its countdown runs out. With both, a back-edge is\n"
        " * recorded first, then checked. */\n"
        "#ifdef PSPRECOMP_BACKEDGE\n"
        "#  define PSP_EDGE_(a) PSP_BACKEDGE_CHECK(a)\n"
        "#else\n"
        "#  define PSP_EDGE_(a) ((void)0)\n"
        "#endif\n"
        "#ifdef PSPRECOMP_TRACE\n"
        "#  define PSP_ENTER(a) psp_trace_enter(a)\n"
        "#  define PSP_LOOP(a)  do { psp_trace_loop(a); PSP_EDGE_(a); } while (0)\n"
        "#  define PSP_SP_ENTER()  uint32_t _sp0 = r_sp\n"
        "#  define PSP_SP_CHECK(a) psp_trace_sp(a, _sp0, r_sp)\n"
        "#  define PSP_SP_CALL(t,a,b) psp_trace_sp_call(t, a, b)\n"
        "#  define PSP_MARK(a)  psp_trace_mark(a)\n"
        "#else\n"
        "#  define PSP_ENTER(a) ((void)0)\n"
        "#  define PSP_LOOP(a)  PSP_EDGE_(a)\n"
        "#  define PSP_SP_ENTER()  ((void)0)\n"
        "#  define PSP_SP_CHECK(a) ((void)0)\n"
        "#  define PSP_SP_CALL(t,a,b) ((void)0)\n"
        "#  define PSP_MARK(a)  ((void)0)\n"
        "#endif\n"
```

Every `PSP_LOOP` use site (`emit.c:895`, `:916`, `:991`, `:1017`) is a full statement (`PSP_LOOP(t);`, `if (_c) PSP_LOOP(t);`, `case …: PSP_LOOP(t); goto …;`), where a `do { } while (0)` is valid.

- [ ] **Step 5: Count firmware calls**

In `include/psprecomp/hle.h`, after `void psp_hle_call(uint32_t nid);`:

```c
/* Every psp_hle_call so far (registered or not), for a host that charges
 * guest time per firmware call. Never reset. */
uint64_t psp_hle_call_count(void);
```

In `src/hle/hle.c`, before `void psp_hle_call(uint32_t nid) {` add `static uint64_t g_ncalls;` and `uint64_t psp_hle_call_count(void) { return g_ncalls; }`, and make `g_ncalls++;` the first statement of `psp_hle_call`.

- [ ] **Step 6: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure && grep -c PSP_LOOP third_party/psprecomp/build/tests/t_strlen_funcs.c`
Expected: `100% tests passed` (including `backedge`, `emit`, `switch_cc_t_strlen_PSPRECOMP_BACKEDGE` and `switch_cc_t_strlen_PSPRECOMP_BACKEDGE_PSPRECOMP_TRACE`), then a count ≥ 1 (the compiled file really has a back-edge).

- [ ] **Step 7: Commit (fork)**

```bash
git -C third_party/psprecomp add include/psprecomp/dispatch.h src/dispatch.c tools/allegrexrecomp/emit.c \
    include/psprecomp/hle.h src/hle/hle.c tests/test_backedge.c tests/test_emit.c tests/CMakeLists.txt
git -C third_party/psprecomp commit -m "dispatch: back-edge safe point (PSPRECOMP_BACKEDGE); hle: call counter

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: User-partition placement in sysmem (fork), publish, bump the pin

**Files:**
- Modify: `third_party/psprecomp/src/hle/sysmem.c`, `include/psprecomp/hle.h`
- Create: `third_party/psprecomp/tests/test_sysmem.c`; Modify: `tests/CMakeLists.txt`
- Modify: `tools/setup_toolchain.sh:97`, `docs/setup.md:28`, `docs/next-session.md:13`

- [ ] **Step 1: Write the failing test**

`third_party/psprecomp/tests/test_sysmem.c`:

```c
/* psp_sysmem_set_range / psp_sysmem_reserve: the user partition around a
 * loaded image, with PPSSPP's placements for KH BBS's boot. Synthetic data. */
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <stdio.h>

static int failures;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); failures++; } } while (0)

static uint32_t call5(uint32_t nid, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t t0) {
    psp_cpu.r[PSP_REG_A0] = a0; psp_cpu.r[PSP_REG_A1] = a1;
    psp_cpu.r[PSP_REG_A2] = a2; psp_cpu.r[PSP_REG_A3] = a3;
    psp_cpu.r[PSP_REG_T0] = t0;
    psp_hle_call(nid);
    return psp_cpu.r[PSP_REG_V0];
}

int main(void) {
    CHECK(psp_mem_init() == 0, "memory");
    psp_hle_init();
    psp_sysmem_reset();
    psp_sysmem_set_range(0x08800000u, 0x0A000000u);
    CHECK(psp_sysmem_free() == 0x01800000u, "the whole range is free");
    /* KH BBS's image ends at 0x09F3D7D0; the host reserves up to the next 0x100. */
    CHECK(psp_sysmem_reserve(0x08800000u, 0x09F3D800u - 0x08800000u) == 0, "reserve the image");
    CHECK(psp_sysmem_reserve(0x09F3D000u, 0x1000u) == -1, "an overlap is refused");
    CHECK(psp_sysmem_reserve(0x087FF000u, 0x1000u) == -1, "below the range is refused");
    CHECK(psp_sysmem_reserve(0x09FFF000u, 0x2000u) == -1, "past the range is refused");
    CHECK(psp_sysmem_reserve(0x09F40000u, 0) == -1, "an empty reservation is refused");
    CHECK(psp_sysmem_free() == 0x0A000000u - 0x09F3D800u, "reserved bytes count as used");

    /* Stacks from the top, as PPSSPP places root (0x400) and user_main (0x37000). */
    CHECK(psp_sysmem_alloc(0x400u, 1) == 0x09FFFC00u, "root stack");
    CHECK(psp_sysmem_alloc(0x37000u, 1) == 0x09FC8C00u, "user_main stack");

    /* The game's block: partition 2, LowAligned (3), 0x1000 bytes, alignment 0x1000. */
    psp_mem_write_block(0x08000200u, "block", 6);
    uint32_t uid = call5(0x237DBD4Fu, 2, 0x08000200u, 3, 0x1000u, 0x1000u);
    CHECK(uid != 0 && !(uid & 0x80000000u), "AllocPartitionMemory succeeds (0x%X)", uid);
    CHECK(call5(0x9D9A5BA1u, uid, 0, 0, 0, 0) == 0x09F3E000u,
          "PPSSPP's address: the first 0x1000-aligned one past the image");
    CHECK(psp_sysmem_alloc(0x1000u, 1) == 0x09FC7C00u, "DiscInfoBind stack");

    /* A reservation has no uid: uid 0 names nothing, and release() leaves it. */
    CHECK(call5(0x9D9A5BA1u, 0, 0, 0, 0, 0) == 0, "GetBlockHeadAddr(0) finds no block");
    CHECK(call5(0xB6D61D02u, 0, 0, 0, 0, 0) != 0, "FreePartitionMemory(0) fails");
    psp_sysmem_release(0x08800000u);
    CHECK(psp_sysmem_reserve(0x08800000u, 0x100u) == -1, "release() does not free a reservation");

    /* reset() restores the default range and drops reservations. */
    psp_sysmem_reset();
    CHECK(psp_sysmem_free() == 0x0A000000u - 0x08C00000u, "reset: the default range, all free");

    printf("%s\n", failures ? "test_sysmem: FAILED" : "test_sysmem: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/CMakeLists.txt`:

```cmake
add_executable(test_sysmem test_sysmem.c)
target_link_libraries(test_sysmem PRIVATE psprecomp)
add_test(NAME sysmem COMMAND test_sysmem)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build third_party/psprecomp/build`
Expected: compile error (`psp_sysmem_set_range` undeclared).

- [ ] **Step 3: Implement**

In `include/psprecomp/hle.h`, after `void psp_sysmem_release(uint32_t addr);`:

```c
/* The user partition the allocator manages, [lo, hi). Call after
 * psp_sysmem_reset(), which restores the default [0x08C00000, 0x0A000000). */
void psp_sysmem_set_range(uint32_t lo, uint32_t hi);
/* Mark [addr, addr+size) used, with no uid (a loaded image, a host-owned
 * area). 0, or -1 if it is empty, leaves the range, overlaps a block, or the
 * table is full. Neither psp_sysmem_release nor FreePartitionMemory frees it. */
int  psp_sysmem_reserve(uint32_t addr, uint32_t size);
```

In `src/hle/sysmem.c`:
- add `int reserved;` to `mem_block` after `int used;`;
- in `find_uid`, skip reserved blocks: `if (g_block[i].used && !g_block[i].reserved && g_block[i].uid == uid) return &g_block[i];`;
- in `psp_sysmem_release`, the same: `if (g_block[i].used && !g_block[i].reserved && g_block[i].addr == addr) { ... }`;
- after `psp_sysmem_release`, add:

```c
void psp_sysmem_set_range(uint32_t lo, uint32_t hi) {
    g_heap_lo = lo;
    g_heap_hi = hi;
}

int psp_sysmem_reserve(uint32_t addr, uint32_t size) {
    if (!size || addr < g_heap_lo || addr > g_heap_hi || size > g_heap_hi - addr) return -1;
    if (overlaps(addr, size)) return -1;
    mem_block *b = alloc_slot();
    if (!b) return -1;
    b->uid = 0;
    b->addr = addr;
    b->size = size;
    b->used = 1;
    b->reserved = 1;
    snprintf(b->name, sizeof b->name, "reserved");
    return 0;
}
```

- [ ] **Step 4: Run the fork tests**

Run: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure`
Expected: `100% tests passed`, including `sysmem` and the untouched `hle`.

- [ ] **Step 4b: pspsdk's values for three error macros (decided 2026-10-19)**

`include/psprecomp/hle.h` gives three `SCE_KERNEL_ERROR_*` macros values that differ from pspsdk's `src/user/pspkerror.h` (github.com/pspdev/pspsdk; checked 2026-10-19). Change them:

```c
#define SCE_KERNEL_ERROR_ILLEGAL_ADDR    0x800200D3   /* pspsdk; was 0x80020005 */
#define SCE_KERNEL_ERROR_UNKNOWN_UID     0x800200CB   /* pspsdk; was 0x800201A2 (DORMANT) */
#define SCE_KERNEL_ERROR_ILLEGAL_MEMBLOCK 0x800201B6  /* pspsdk; was 0x800201A9 (WAIT_CANCEL) */
```

The fork's tests compare against the macros (`tests/test_hle.c:140,180`), not literal values, so add a check that pins the numbers, at the end of `test_sysmem.c`'s `main` before the summary:

```c
    CHECK(SCE_KERNEL_ERROR_UNKNOWN_UID == 0x800200CBu, "UNKNOWN_UID is pspsdk's");
    CHECK(SCE_KERNEL_ERROR_ILLEGAL_MEMBLOCK == 0x800201B6u, "ILLEGAL_MEMBLOCK is pspsdk's");
    CHECK(SCE_KERNEL_ERROR_ILLEGAL_ADDR == 0x800200D3u, "ILLEGAL_ADDR is pspsdk's");
    CHECK(call5(0xB6D61D02u /* sceKernelFreePartitionMemory */, 0xDEADBEEFu, 0, 0, 0, 0) == 0x800200CBu,
          "an unknown block uid returns UNKNOWN_UID");
```

Run it before changing `hle.h` (it fails on the three values), change the macros, then: `cmake --build third_party/psprecomp/build && ctest --test-dir third_party/psprecomp/build --output-on-failure` → `100% tests passed`. Commit it on its own in the fork, before Step 5:

```bash
git -C third_party/psprecomp add include/psprecomp/hle.h tests/test_sysmem.c
git -C third_party/psprecomp commit -m "hle: pspsdk's values for UNKNOWN_UID, ILLEGAL_MEMBLOCK, ILLEGAL_ADDR

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Commit, push the fork, bump the pin**

```bash
git -C third_party/psprecomp add src/hle/sysmem.c include/psprecomp/hle.h tests/test_sysmem.c tests/CMakeLists.txt
git -C third_party/psprecomp commit -m "sysmem: psp_sysmem_set_range and psp_sysmem_reserve

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git -C third_party/psprecomp push origin khbbs
NEW=$(git -C third_party/psprecomp rev-parse --short=7 HEAD)
sed -i '' "s/psprecomp 34bab3c khbbs/psprecomp $NEW khbbs/" tools/setup_toolchain.sh
sed -i '' "s/| \`34bab3c\` |/| \`$NEW\` |/" docs/setup.md
sed -i '' "s/pinned at 34bab3c/pinned at $NEW/" docs/next-session.md
git diff --stat
```

Expected: one changed line in each of the three files, each naming `$NEW`.

- [ ] **Step 6: The repo still builds and passes on the new pin**

```bash
cmake -S . -B build/pc -G Ninja && cmake --build build/pc && ctest --test-dir build/pc --output-on-failure
```

Expected: build and all tests pass. Nothing in the repo uses the new APIs yet, and without `-DPSPRECOMP_BACKEDGE` the generated code is unchanged.

- [ ] **Step 7: Commit**

```bash
git checkout -b p4-kernel-scheduler
tools/check_no_assets.sh
git add tools/setup_toolchain.sh docs/setup.md docs/next-session.md
git commit -m "Pin psprecomp $NEW (P4: VFPU context, back-edge safe point, sysmem placement)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The host interface and its POSIX backend

**Files:**
- Create: `runtime/include/khbbs/host.h`, `runtime/host.c`
- Create: `hosts/posix/include/khbbs/host_posix.h`, `hosts/posix/host_posix.c`, `hosts/posix/CMakeLists.txt`
- Modify: `runtime/CMakeLists.txt`, `CMakeLists.txt`, `tests/runtime/CMakeLists.txt`
- Create: `tests/runtime/test_host.c`

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_host.c`:

```c
/* The host interface and its POSIX backend: threads, a mutex plus condition
 * variable hand-off, the monotonic clock and sleeping. */
#include "khbbs/host.h"
#include "khbbs/host_posix.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static const khbbs_host *H;
static khbbs_host_mutex *m;
static khbbs_host_cond *cv[2];
static int turn, nlog;
static char logbuf[16];

/* Two threads take turns, strictly, through one mutex and two condvars:
 * the same hand-off the scheduler's baton uses. */
static void pingpong(void *p) {
    const int me = *(const int *)p;
    H->mutex_lock(m);
    for (int i = 0; i < 4; i++) {
        while (turn != me) H->cond_wait(cv[me], m);
        logbuf[nlog++] = (char)('a' + me);
        turn = !me;
        H->cond_signal(cv[!me]);
    }
    H->mutex_unlock(m);
}

int main(void) {
    CHECK(khbbs_host_get() == NULL, "no host before khbbs_host_set");
    khbbs_host_set(khbbs_host_posix());
    H = khbbs_host_get();
    CHECK(H == khbbs_host_posix(), "set, then get");
    CHECK(H->mutex_create(&m) == 0 && H->cond_create(&cv[0]) == 0 && H->cond_create(&cv[1]) == 0,
          "create a mutex and two condvars");

    static const int ids[2] = { 0, 1 };
    khbbs_host_thread *t0, *t1;
    CHECK(H->thread_start(&t0, pingpong, (void *)&ids[0], 1u << 20) == 0, "start thread 0");
    CHECK(H->thread_start(&t1, pingpong, (void *)&ids[1], 1u << 20) == 0, "start thread 1");
    CHECK(khbbs_host_posix_live_threads() == 2, "two threads alive");
    H->thread_join(t0);
    H->thread_join(t1);
    CHECK(khbbs_host_posix_live_threads() == 0, "both joined");
    logbuf[nlog] = 0;
    CHECK(strcmp(logbuf, "abababab") == 0, "strict alternation");

    uint64_t t = H->now_us();
    H->sleep_us(2000);
    uint64_t dt = H->now_us() - t;
    CHECK(dt >= 2000 && dt < 2000000, "sleep_us(2000) takes 2 ms to 2 s");
    CHECK(H->now_us() >= t, "the clock is monotonic");

    H->cond_destroy(cv[0]);
    H->cond_destroy(cv[1]);
    H->mutex_destroy(m);
    printf("%s\n", failures ? "test_host: FAILED" : "test_host: ok");
    return failures ? 1 : 0;
}
```

Append to `tests/runtime/CMakeLists.txt`:

```cmake
# Threaded tests: the POSIX host backend (pthreads). The kernel's internal
# headers (runtime/kernel/*.h) are included as "kernel/...".
if(UNIX)
    function(khbbs_kernel_test name)
        add_executable(test_${name} test_${name}.c)
        target_link_libraries(test_${name} PRIVATE khbbs_runtime khbbs_host_posix)
        target_include_directories(test_${name} PRIVATE ${PROJECT_SOURCE_DIR}/runtime)
        target_compile_options(test_${name} PRIVATE -Wall -Wextra)
        add_test(NAME ${name} COMMAND test_${name} WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
        set_tests_properties(${name} PROPERTIES TIMEOUT 60)
    endfunction()
    khbbs_kernel_test(host)
endif()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake -S . -B build/ci -G Ninja -DKHBBS_WITH_GAME=OFF`
Expected: CMake error, `Target "test_host" links to target "khbbs_host_posix" but the target was not found` (or a compile error on `khbbs/host.h`).

- [ ] **Step 3: The interface**

`runtime/include/khbbs/host.h`:

```c
/* What the platform-free runtime needs from the platform: host threads, a
 * mutex and condition variables (the scheduler's baton), a monotonic clock
 * and sleeping. A host fills in a khbbs_host and calls khbbs_host_set once,
 * before the first khbbs_boot. POSIX: hosts/posix (khbbs_host_posix()). A
 * Vita host supplies its own. All functions are required. */
#ifndef KHBBS_HOST_H
#define KHBBS_HOST_H

#include <stddef.h>
#include <stdint.h>

typedef struct khbbs_host_thread khbbs_host_thread;   /* opaque, the host's */
typedef struct khbbs_host_mutex  khbbs_host_mutex;
typedef struct khbbs_host_cond   khbbs_host_cond;

typedef struct {
    /* Run fn(arg) on a new host thread with at least stack_bytes of C stack.
     * 0 on success (*out set), -1 on failure. */
    int  (*thread_start)(khbbs_host_thread **out, void (*fn)(void *), void *arg, size_t stack_bytes);
    /* Wait until that thread's fn has returned, then free *t. */
    void (*thread_join)(khbbs_host_thread *t);
    /* A non-recursive mutex. create: 0 or -1. */
    int  (*mutex_create)(khbbs_host_mutex **out);
    void (*mutex_destroy)(khbbs_host_mutex *m);
    void (*mutex_lock)(khbbs_host_mutex *m);
    void (*mutex_unlock)(khbbs_host_mutex *m);
    /* A condition variable; wait may wake spuriously. create: 0 or -1. */
    int  (*cond_create)(khbbs_host_cond **out);
    void (*cond_destroy)(khbbs_host_cond *c);
    void (*cond_wait)(khbbs_host_cond *c, khbbs_host_mutex *m);
    void (*cond_signal)(khbbs_host_cond *c);
    /* Monotonic microseconds (any origin), and sleeping at least `us`. */
    uint64_t (*now_us)(void);
    void     (*sleep_us)(uint64_t us);
} khbbs_host;

void khbbs_host_set(const khbbs_host *h);
const khbbs_host *khbbs_host_get(void);    /* NULL until set */

#endif
```

`runtime/host.c`:

```c
#include "khbbs/host.h"

static const khbbs_host *g_host;

void khbbs_host_set(const khbbs_host *h) { g_host = h; }
const khbbs_host *khbbs_host_get(void) { return g_host; }
```

In `runtime/CMakeLists.txt`, add `host.c` to the `khbbs_runtime` sources.

- [ ] **Step 4: The POSIX backend**

`hosts/posix/include/khbbs/host_posix.h`:

```c
/* khbbs_host for POSIX systems (macOS, Linux): pthreads, CLOCK_MONOTONIC. */
#ifndef KHBBS_HOST_POSIX_H
#define KHBBS_HOST_POSIX_H

#include "khbbs/host.h"

const khbbs_host *khbbs_host_posix(void);
/* Threads started and not yet joined, for leak checks in tests. */
int khbbs_host_posix_live_threads(void);

#endif
```

`hosts/posix/host_posix.c`:

```c
#define _POSIX_C_SOURCE 200809L
#include "khbbs/host_posix.h"

#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <time.h>

struct khbbs_host_thread { pthread_t t; void (*fn)(void *); void *arg; };
struct khbbs_host_mutex  { pthread_mutex_t m; };
struct khbbs_host_cond   { pthread_cond_t c; };

static pthread_mutex_t g_live_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_live;

static void *run(void *p) {
    khbbs_host_thread *t = p;
    t->fn(t->arg);
    return NULL;
}

static int thread_start(khbbs_host_thread **out, void (*fn)(void *), void *arg, size_t stack) {
    khbbs_host_thread *t = calloc(1, sizeof *t);
    if (!t) return -1;
    t->fn = fn;
    t->arg = arg;
    pthread_attr_t a;
    pthread_attr_init(&a);
    if (stack) pthread_attr_setstacksize(&a, stack);      /* stack is a page multiple: 1 MB, 8 MB */
    int rc = pthread_create(&t->t, &a, run, t);
    pthread_attr_destroy(&a);
    if (rc) { free(t); return -1; }
    pthread_mutex_lock(&g_live_lock);
    g_live++;
    pthread_mutex_unlock(&g_live_lock);
    *out = t;
    return 0;
}

static void thread_join(khbbs_host_thread *t) {
    pthread_join(t->t, NULL);
    free(t);
    pthread_mutex_lock(&g_live_lock);
    g_live--;
    pthread_mutex_unlock(&g_live_lock);
}

static int mutex_create(khbbs_host_mutex **out) {
    khbbs_host_mutex *m = malloc(sizeof *m);
    if (!m || pthread_mutex_init(&m->m, NULL)) { free(m); return -1; }
    *out = m;
    return 0;
}
static void mutex_destroy(khbbs_host_mutex *m) { pthread_mutex_destroy(&m->m); free(m); }
static void mutex_lock(khbbs_host_mutex *m)    { pthread_mutex_lock(&m->m); }
static void mutex_unlock(khbbs_host_mutex *m)  { pthread_mutex_unlock(&m->m); }

static int cond_create(khbbs_host_cond **out) {
    khbbs_host_cond *c = malloc(sizeof *c);
    if (!c || pthread_cond_init(&c->c, NULL)) { free(c); return -1; }
    *out = c;
    return 0;
}
static void cond_destroy(khbbs_host_cond *c) { pthread_cond_destroy(&c->c); free(c); }
static void cond_wait(khbbs_host_cond *c, khbbs_host_mutex *m) { pthread_cond_wait(&c->c, &m->m); }
static void cond_signal(khbbs_host_cond *c) { pthread_cond_signal(&c->c); }

static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000u + (uint64_t)ts.tv_nsec / 1000u;
}

static void sleep_us(uint64_t us) {
    struct timespec ts = { (time_t)(us / 1000000u), (long)(us % 1000000u) * 1000L };
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {}
}

static const khbbs_host g_posix = {
    thread_start, thread_join,
    mutex_create, mutex_destroy, mutex_lock, mutex_unlock,
    cond_create, cond_destroy, cond_wait, cond_signal,
    now_us, sleep_us,
};

const khbbs_host *khbbs_host_posix(void) { return &g_posix; }

int khbbs_host_posix_live_threads(void) {
    pthread_mutex_lock(&g_live_lock);
    int n = g_live;
    pthread_mutex_unlock(&g_live_lock);
    return n;
}
```

`hosts/posix/CMakeLists.txt`:

```cmake
# The POSIX host backend: the only place pthreads and clock_gettime are used.
find_package(Threads REQUIRED)
add_library(khbbs_host_posix STATIC host_posix.c)
# Only khbbs/host.h is needed from the runtime, so no link to khbbs_runtime
# (a test linking both would list the runtime twice).
target_include_directories(khbbs_host_posix PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
                                                   ${PROJECT_SOURCE_DIR}/runtime/include)
target_link_libraries(khbbs_host_posix PUBLIC Threads::Threads)
target_compile_options(khbbs_host_posix PRIVATE -Wall -Wextra)
```

In the top-level `CMakeLists.txt`, after `add_subdirectory(runtime)`:

```cmake
if(UNIX)
    add_subdirectory(hosts/posix)   # pthreads backend for hosts/pc and the threaded tests
endif()
```

and in `hosts/pc/CMakeLists.txt` add `khbbs_host_posix` to `target_link_libraries(khbbs ...)`.

- [ ] **Step 5: Run the tests**

```bash
cmake -S . -B build/ci -G Ninja -DKHBBS_WITH_GAME=OFF && cmake --build build/ci && ctest --test-dir build/ci --output-on-failure
```

Expected: `100% tests passed`, including `host` (`test_host: ok`).

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git add runtime/include/khbbs/host.h runtime/host.c runtime/CMakeLists.txt hosts/posix CMakeLists.txt \
    hosts/pc/CMakeLists.txt tests/runtime/CMakeLists.txt tests/runtime/test_host.c
git commit -m "runtime: host interface (threads, baton primitives, clock); POSIX backend

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: The scheduler: baton, context switch, waits, timers, `idle0`

This is the heart of P4. A guest thread is a `khbbs_thread` slot plus a host thread running `trampoline()`. `g_cur` holds the baton. Kernel code runs with `g_lock` held; guest code runs without it. A thread that gives the baton away saves `psp_cpu`, the VFPU context and its interrupt flag, signals the next thread's condition variable and waits on its own until the baton comes back, then restores them. Rules every later task relies on:

- Every kernel entry point (HLE handler, back-edge service) takes the lock first (`khbbs_klock()`) and releases it before returning or running guest code.
- A handler sets its return value (`psp_ret`) *before* any call that may switch threads (`khbbs_sched_preempt`), and after `khbbs_sched_wait` returns.
- Waiting is `khbbs_sched_wait(&w)` with a `khbbs_wait` on the waiter's C stack; waking is `khbbs_sched_wake(t, result)`. The queue of an object is "every thread whose `wait` names it", ordered by `khbbs_waiters()`.
- `idle0` is always ready at priority `0x80`, so `pick_next()` never fails during a run.

**Files:**
- Create: `runtime/kernel/errors.h`, `runtime/kernel/sched.h`, `runtime/kernel/sched.c`, `runtime/kernel/ktime.h`, `runtime/kernel/ktime.c`
- Modify: `runtime/include/khbbs/boot.h` (stop reasons, time mode), `runtime/boot.c` (`khbbs_stop_name` only), `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_sched.c`; Modify: `tests/runtime/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_sched.c`:

```c
/* The scheduler on synthetic guest threads (C functions registered with
 * psp_register): priority order, FIFO within a priority, wait and wake with
 * preemption, deadlines on the virtual clock, deadlock, a stop on another
 * thread, and per-thread registers and VFPU state. No game data. */
#include "kernel/errors.h"
#include "kernel/ktime.h"
#include "kernel/sched.h"
#include "khbbs/host_posix.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/mem.h"
#include "psprecomp/vfpu.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

static char order[64];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }

static void reset(void) {
    norder = 0;
    order[0] = 0;
    CHECK(khbbs_sched_reset() == 0, "reset");
    khbbs_time_reset(KHBBS_TIME_DET);
}

/* A thread with an all-zero register file and a fresh VFPU context. */
static khbbs_thread *mk(const char *name, uint32_t prio, uint32_t entry) {
    khbbs_klock();
    khbbs_thread *t = khbbs_thread_alloc(name);
    t->prio = t->init_prio = prio;
    t->entry = entry;
    memset(&t->ctx.cpu, 0, sizeof t->ctx.cpu);
    psp_vfpu_ctx_init(&t->ctx.vfpu);
    t->ctx.intr = 1;
    CHECK(khbbs_sched_start(t) == 0, "start");
    khbbs_kunlock();
    return t;
}

static void sleep_forever(void) {
    khbbs_klock();
    khbbs_wait w = { .type = KW_SLEEP, .deadline = KHBBS_NO_DEADLINE };
    khbbs_sched_wait(&w);
    khbbs_kunlock();
}

/* 1: priority, then FIFO. */
static void fa(void) { mark('A'); }
static void fb(void) { mark('B'); }
static void fc(void) { mark('C'); }

/* 2: a higher-priority waiter preempts its waker. */
static khbbs_thread *g_h;
static void high(void) {
    mark('h');
    khbbs_klock();
    khbbs_wait w = { .type = KW_SLEEP, .deadline = KHBBS_NO_DEADLINE };
    int32_t r = khbbs_sched_wait(&w);
    khbbs_kunlock();
    CHECK(r == 7, "woken with the waker's result");
    mark('H');
}
static void low(void) {
    mark('l');
    khbbs_klock();
    khbbs_sched_wake(g_h, 7);
    khbbs_sched_preempt();                        /* h is higher: it runs now */
    khbbs_kunlock();
    mark('L');
}

/* 3: deadlines. Nothing else is runnable, so idle0 jumps the clock. */
static uint64_t g_t1, g_t2;
static void timed(void) {
    khbbs_klock();
    khbbs_wait w = { .type = KW_SEMA, .id = 1, .deadline = khbbs_now() + 1000 };
    int32_t r = khbbs_sched_wait(&w);
    g_t1 = khbbs_now();
    khbbs_wait d = { .type = KW_DELAY, .deadline = khbbs_now() + 500 };
    int32_t r2 = khbbs_sched_wait(&d);
    g_t2 = khbbs_now();
    khbbs_kunlock();
    CHECK(r == (int32_t)KERR_WAIT_TIMEOUT, "an object wait times out with WAIT_TIMEOUT");
    CHECK(r2 == 0, "a delay ends with 0");
}

/* 5: a stop on one thread unwinds every thread. */
static void stopper(void) { khbbs_sched_stop(KHBBS_GUEST_BREAK, 0x08804800u, 7); }

/* 6: registers and VFPU state stay with their thread. */
#define C000 0x00u
#define C020 0x02u
#define S100 0x04u
static void set4(uint32_t vreg, float a, float b, float c, float d) {
    int r[4];
    psp_vfpu_regs(vreg, 4, r);
    psp_cpu.v[r[0]] = a; psp_cpu.v[r[1]] = b; psp_cpu.v[r[2]] = c; psp_cpu.v[r[3]] = d;
}
static int is4(uint32_t vreg, float a, float b, float c, float d) {
    int r[4];
    psp_vfpu_regs(vreg, 4, r);
    return psp_cpu.v[r[0]] == a && psp_cpu.v[r[1]] == b && psp_cpu.v[r[2]] == c && psp_cpu.v[r[3]] == d;
}
static uint32_t draw(void) { psp_vrnd(PSP_VR_I, S100, 1); return psp_mfv(S100); }
static uint32_t r1, r2;
static khbbs_thread *g_a;
static void va(void) {
    draw();                                       /* A's generator: one step */
    psp_vfpu_set_prefix(PSP_VPFX_S, 0x1B);        /* [W, Z, Y, X] pending */
    psp_cpu.r[PSP_REG_S0] = 0xAAAA0000u;
    sleep_forever();                              /* B runs, then wakes us */
    CHECK(psp_cpu.r[PSP_REG_S0] == 0xAAAA0000u, "A keeps its s0");
    CHECK(psp_vfpu_prefix_pending(), "A's prefix survived B");
    set4(C000, 1, 2, 3, 4);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    CHECK(is4(C020, 4, 3, 2, 1), "A's swizzle applies after the switch");
    CHECK(draw() == r2, "A's generator continues where A left it");
}
static void vb(void) {
    CHECK(!psp_vfpu_prefix_pending(), "B does not see A's prefix");
    CHECK(draw() == r1, "B's generator starts at the seed");
    psp_vfpu_set_prefix(PSP_VPFX_D, 0x00040D);    /* [0:1, -1:1, M, -] pending */
    psp_cpu.r[PSP_REG_S0] = 0xBBBB0000u;
    khbbs_klock();
    khbbs_sched_wake(g_a, 0);
    khbbs_sched_preempt();                        /* A is higher: runs to its end */
    khbbs_kunlock();
    CHECK(psp_cpu.r[PSP_REG_S0] == 0xBBBB0000u, "B keeps its s0");
    set4(C000, 1.5f, -2, 7, -0.25f);
    set4(C020, 9, 9, 9, 9);
    psp_vunary(PSP_VU_MOV, C020, C000, 4);
    CHECK(is4(C020, 1, -1, 9, -0.25f), "B's destination prefix survived A");
    CHECK(draw() == r2, "B's second draw");
}

int main(void) {
    CHECK(psp_mem_init() == 0, "memory");
    khbbs_host_set(khbbs_host_posix());
    psp_register(0x08804100u, fa);
    psp_register(0x08804200u, fb);
    psp_register(0x08804300u, fc);
    psp_register(0x08804400u, high);
    psp_register(0x08804500u, low);
    psp_register(0x08804600u, timed);
    psp_register(0x08804700u, sleep_forever);
    psp_register(0x08804800u, stopper);
    psp_register(0x08804900u, va);
    psp_register(0x08804A00u, vb);

    reset();
    mk("a", 0x20, 0x08804100u);
    mk("b", 0x10, 0x08804200u);
    mk("c", 0x10, 0x08804300u);
    khbbs_result r = khbbs_sched_run();
    CHECK(r.why == KHBBS_RETURNED, "1: every thread exited");
    CHECK(strcmp(order, "BCA") == 0, "1: priority, then FIFO within a priority");
    CHECK(khbbs_host_posix_live_threads() == 0, "1: every host thread joined");

    reset();
    g_h = mk("h", 0x10, 0x08804400u);
    mk("l", 0x30, 0x08804500u);
    r = khbbs_sched_run();
    CHECK(r.why == KHBBS_RETURNED && strcmp(order, "hlHL") == 0, "2: wake + preempt runs the waiter first");

    reset();
    mk("t", 0x20, 0x08804600u);
    r = khbbs_sched_run();
    CHECK(r.why == KHBBS_RETURNED, "3: returned");
    CHECK(g_t1 == 1000 && g_t2 == 1500, "3: idle0 jumped the clock to each deadline exactly");

    reset();
    mk("f", 0x20, 0x08804700u);
    r = khbbs_sched_run();
    CHECK(r.why == KHBBS_DEADLOCK, "4: a wait nothing can end is a deadlock");
    CHECK(khbbs_host_posix_live_threads() == 0, "4: the waiter was unwound and joined");

    reset();
    mk("w", 0x10, 0x08804700u);                   /* waits forever, at a higher priority */
    mk("s", 0x20, 0x08804800u);
    r = khbbs_sched_run();
    CHECK(r.why == KHBBS_GUEST_BREAK && r.addr == 0x08804800u && r.code == 7,
          "5: a stop on another thread ends the run with its reason");
    CHECK(khbbs_host_posix_live_threads() == 0, "5: no host thread left");

    psp_vfpu_reset();
    r1 = draw();
    r2 = draw();
    reset();
    g_a = mk("va", 0x10, 0x08804900u);
    mk("vb", 0x20, 0x08804A00u);
    r = khbbs_sched_run();
    CHECK(r.why == KHBBS_RETURNED, "6: returned");

    printf("%s\n", failures ? "test_sched: FAILED" : "test_sched: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt`, after `khbbs_kernel_test(host)` add `khbbs_kernel_test(sched)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci`
Expected: compile error, `kernel/errors.h` not found.

- [ ] **Step 3: Stop reasons and the time mode**

In `runtime/include/khbbs/boot.h`, replace the `khbbs_stop` enum and `khbbs_boot_opts` with:

```c
typedef enum {
    KHBBS_RETURNED,      /* every guest thread exited (dormant or deleted) */
    KHBBS_DISPATCH_MISS,
    KHBBS_BUDGET,
    KHBBS_BAD_ARGS,      /* nothing was run: bad arguments, `path` too long, no host interface */
    KHBBS_GUEST_BREAK,   /* the guest executed `break`: see `code` */
    KHBBS_DEADLOCK,      /* no thread runnable and no timer pending */
    KHBBS_EXIT_GAME,     /* the game called sceKernelExitGame */
    KHBBS_TIME_LIMIT     /* guest time passed khbbs_boot_opts.time_limit_us */
} khbbs_stop;

/* Guest time (docs/architecture.md, "Threading"). DET: a virtual clock,
 * the same run gives the same trace; REAL: the host's monotonic clock. */
typedef enum { KHBBS_TIME_DET = 0, KHBBS_TIME_REAL } khbbs_time_mode;
```

```c
typedef struct {
    uint64_t budget;       /* indirect dispatches allowed, 0 = unlimited (see below) */
    uint32_t keep_going;   /* distinct miss targets to step over, 0 = stop at the first */
    khbbs_time_mode time;  /* KHBBS_TIME_DET by default (zeroed opts) */
    uint64_t time_limit_us; /* stop once guest time passes this, 0 = none. A loop of
                               delays makes no dispatches, so --budget alone cannot end it */
} khbbs_boot_opts;
```

In `runtime/boot.c`, `khbbs_stop_name`: replace the `KHBBS_RETURNED` case's string with `"all threads exited"` and add

```c
    case KHBBS_DEADLOCK:      return "deadlock (no runnable thread, no timer)";
    case KHBBS_EXIT_GAME:     return "the game called sceKernelExitGame";
    case KHBBS_TIME_LIMIT:    return "guest time limit reached";
```

- [ ] **Step 4: Error codes**

`runtime/kernel/errors.h`:

```c
/* Kernel error codes. 0x8002xxxx values up to 0x800201C2 and 0x8002032x are
 * pspsdk's src/kernel/pspkerror.h; 0x8001xxxx are 0x80010000 | newlib errno,
 * as the PSP's IoFileMgr returns them. The mutex and lightweight-mutex codes
 * (0x800201C3..0x800201CF) are not in pspsdk; they are the values published
 * for later firmware (uOFW's errors.h): UNVERIFIED, and no call on the boot
 * path returns one. A KERR_ prefix, because psprecomp's hle.h defines some
 * SCE_KERNEL_ERROR_* names with other values. */
#ifndef KHBBS_KERNEL_ERRORS_H
#define KHBBS_KERNEL_ERRORS_H

#define KERR_OK                     0x00000000u
#define KERR_ERROR                  0x80020001u
#define KERR_NOTIMP                 0x80020002u
#define KERR_ILLEGAL_CONTEXT        0x80020064u   /* called from an interrupt handler */
#define KERR_ILLEGAL_INTRCODE       0x80020065u
#define KERR_FOUND_HANDLER          0x80020067u
#define KERR_NOTFOUND_HANDLER       0x80020068u
#define KERR_ILLEGAL_ARGUMENT       0x800200D2u
#define KERR_ILLEGAL_ADDR           0x800200D3u
#define KERR_NO_MEMORY              0x80020190u
#define KERR_ILLEGAL_ATTR           0x80020191u
#define KERR_ILLEGAL_ENTRY          0x80020192u
#define KERR_ILLEGAL_PRIORITY       0x80020193u
#define KERR_ILLEGAL_STACK_SIZE     0x80020194u
#define KERR_ILLEGAL_MODE           0x80020195u
#define KERR_ILLEGAL_MASK           0x80020196u
#define KERR_ILLEGAL_THID           0x80020197u
#define KERR_UNKNOWN_THID           0x80020198u
#define KERR_UNKNOWN_SEMID          0x80020199u
#define KERR_UNKNOWN_EVFID          0x8002019Au
#define KERR_UNKNOWN_MBXID          0x8002019Bu
#define KERR_UNKNOWN_ALMID          0x8002019Fu
#define KERR_UNKNOWN_CBID           0x800201A1u
#define KERR_DORMANT                0x800201A2u
#define KERR_SUSPEND                0x800201A3u
#define KERR_NOT_DORMANT            0x800201A4u
#define KERR_NOT_SUSPEND            0x800201A5u
#define KERR_NOT_WAIT               0x800201A6u
#define KERR_CAN_NOT_WAIT           0x800201A7u
#define KERR_WAIT_TIMEOUT           0x800201A8u
#define KERR_WAIT_CANCEL            0x800201A9u
#define KERR_RELEASE_WAIT           0x800201AAu
#define KERR_THREAD_TERMINATED      0x800201ACu
#define KERR_SEMA_ZERO              0x800201ADu
#define KERR_SEMA_OVF               0x800201AEu
#define KERR_EVF_COND               0x800201AFu
#define KERR_EVF_MULTI              0x800201B0u
#define KERR_EVF_ILPAT              0x800201B1u
#define KERR_MBOX_NOMSG             0x800201B2u
#define KERR_WAIT_DELETE            0x800201B5u
#define KERR_ILLEGAL_SIZE           0x800201BCu
#define KERR_ILLEGAL_COUNT          0x800201BDu
#define KERR_MUTEX_NOT_FOUND        0x800201C3u
#define KERR_MUTEX_TRYLOCK_FAILED   0x800201C4u
#define KERR_MUTEX_NOT_LOCKED       0x800201C5u
#define KERR_MUTEX_LOCK_OVERFLOW    0x800201C6u
#define KERR_MUTEX_UNLOCK_UNDERFLOW 0x800201C7u
#define KERR_MUTEX_RECURSIVE        0x800201C8u   /* a non-recursive mutex locked again by its owner */
#define KERR_LWMUTEX_NOT_FOUND      0x800201CAu
#define KERR_LWMUTEX_TRYLOCK_FAILED 0x800201CBu
#define KERR_LWMUTEX_NOT_LOCKED     0x800201CCu
#define KERR_LWMUTEX_LOCK_OVERFLOW  0x800201CDu
#define KERR_LWMUTEX_UNLOCK_UNDERFLOW 0x800201CEu
#define KERR_LWMUTEX_RECURSIVE      0x800201CFu
#define KERR_UNSUP                  0x80020325u
#define KERR_ASYNC_BUSY             0x80020329u
#define KERR_NOASYNC                0x8002032Au
#define KERR_ERRNO_ENOENT           0x80010002u
#define KERR_ERRNO_EBADF            0x80010009u
#define KERR_ERRNO_EINVAL           0x80010016u
#define KERR_ERRNO_EMFILE           0x80010018u
#define KERR_ERRNO_EROFS            0x8001001Eu

#endif
```

- [ ] **Step 5: The clock and the timer queue**

`runtime/kernel/ktime.h`:

```c
/* Guest time: the virtual (or host) clock and the timer queue. Internal to
 * runtime/kernel. Every function except khbbs_now is called with the kernel
 * lock held. */
#ifndef KHBBS_KERNEL_KTIME_H
#define KHBBS_KERNEL_KTIME_H

#include "khbbs/boot.h"

#include <stdint.h>

#define KHBBS_DET_HLE_US       1u     /* det: every firmware call costs 1 µs */
#define KHBBS_BACKEDGE_PERIOD  64     /* back-edges per service; det: one period is 1 µs */
#define KHBBS_MAX_TIMERS       512

typedef void (*khbbs_timer_fn)(void *arg, uint64_t when);

void     khbbs_time_reset(khbbs_time_mode mode);   /* also empties the timer queue */
khbbs_time_mode khbbs_time_mode_get(void);
uint64_t khbbs_now(void);                          /* guest µs since boot */
/* now + *timeout_ptr, or KHBBS_NO_DEADLINE (sched.h) for a NULL pointer. */
uint64_t khbbs_deadline(uint32_t timeout_ptr);
/* Only idle0 runnable: det jumps the clock to t; real sleeps until t (the
 * lock is released while it sleeps). */
void     khbbs_time_idle_until(uint64_t t);
void     khbbs_time_backedge(void);                /* one back-edge period passed */
/* A one-shot timer. Returns its id, never 0; a full queue is fatal (the run
 * stops with a message). Equal times fire in the order they were added. */
uint32_t khbbs_timer_add(uint64_t when, khbbs_timer_fn fn, void *arg);
void     khbbs_timer_cancel(uint32_t id);          /* 0 or an unknown id: nothing */
uint64_t khbbs_timer_next(void);                   /* earliest time, or KHBBS_NO_DEADLINE */
/* Run every timer due by now, earliest first. A timer fn may add and cancel
 * timers and run guest code (khbbs_call_guest). */
void     khbbs_timer_fire_due(void);
int      khbbs_timer_count(void);
/* Guest-time limit (0 = none, the default after reset); past(t): t is beyond it. */
void     khbbs_time_set_limit(uint64_t us);
int      khbbs_time_past_limit(uint64_t t);

#endif
```

`runtime/kernel/ktime.c`:

```c
/* Guest time. Plan P4 Tasks 5 and 10; docs/architecture.md, "Threading".
 *
 * Deterministic mode: now = g_base + (firmware calls since boot) * 1 µs,
 * where g_base grows by 1 µs per back-edge period and jumps forward when only
 * idle0 is runnable. The same run gives the same clock every time. Real
 * mode: the host's monotonic clock since boot. */
#include "ktime.h"
#include "sched.h"
#include "khbbs/host.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <stdio.h>

static khbbs_time_mode g_mode;
static uint64_t g_base, g_hle0, g_real0;
static uint64_t g_limit;

typedef struct { uint64_t when; uint32_t id; khbbs_timer_fn fn; void *arg; } ktimer;
static ktimer g_tm[KHBBS_MAX_TIMERS];
static int g_ntm;
static uint32_t g_tm_id;

void khbbs_time_reset(khbbs_time_mode mode) {
    g_mode = mode;
    g_base = 0;
    g_hle0 = psp_hle_call_count();
    g_real0 = mode == KHBBS_TIME_REAL ? khbbs_host_get()->now_us() : 0;
    g_ntm = 0;
    g_tm_id = 0;
    g_limit = 0;
}

void khbbs_time_set_limit(uint64_t us) { g_limit = us; }
int  khbbs_time_past_limit(uint64_t t) { return g_limit && t > g_limit; }

khbbs_time_mode khbbs_time_mode_get(void) { return g_mode; }

uint64_t khbbs_now(void) {
    if (g_mode == KHBBS_TIME_REAL) return khbbs_host_get()->now_us() - g_real0;
    return g_base + (psp_hle_call_count() - g_hle0) * KHBBS_DET_HLE_US;
}

uint64_t khbbs_deadline(uint32_t timeout_ptr) {
    return timeout_ptr ? khbbs_now() + psp_read32(timeout_ptr) : KHBBS_NO_DEADLINE;
}

void khbbs_time_idle_until(uint64_t t) {
    const uint64_t now = khbbs_now();
    if (t <= now) return;
    if (g_mode == KHBBS_TIME_DET) { g_base += t - now; return; }
    khbbs_kunlock();
    khbbs_host_get()->sleep_us(t - now);
    khbbs_klock();
}

void khbbs_time_backedge(void) {
    if (g_mode == KHBBS_TIME_DET) g_base += 1;
}

uint32_t khbbs_timer_add(uint64_t when, khbbs_timer_fn fn, void *arg) {
    if (g_ntm == KHBBS_MAX_TIMERS) {
        fprintf(stderr, "khbbs: timer queue full (%d timers)\n", KHBBS_MAX_TIMERS);
        khbbs_sched_stop_locked(KHBBS_DEADLOCK, 0, 0);
    }
    if (++g_tm_id == 0) g_tm_id = 1;
    int i = g_ntm++;
    while (i > 0 && g_tm[i - 1].when > when) { g_tm[i] = g_tm[i - 1]; i--; }
    g_tm[i] = (ktimer){ when, g_tm_id, fn, arg };
    return g_tm_id;
}

void khbbs_timer_cancel(uint32_t id) {
    for (int i = 0; id && i < g_ntm; i++) {
        if (g_tm[i].id != id) continue;
        for (int j = i + 1; j < g_ntm; j++) g_tm[j - 1] = g_tm[j];
        g_ntm--;
        return;
    }
}

uint64_t khbbs_timer_next(void) { return g_ntm ? g_tm[0].when : KHBBS_NO_DEADLINE; }

void khbbs_timer_fire_due(void) {
    while (g_ntm && g_tm[0].when <= khbbs_now()) {
        const ktimer t = g_tm[0];
        for (int j = 1; j < g_ntm; j++) g_tm[j - 1] = g_tm[j];
        g_ntm--;
        t.fn(t.arg, t.when);
    }
}

int khbbs_timer_count(void) { return g_ntm; }
```

- [ ] **Step 6: The scheduler interface**

`runtime/kernel/sched.h`:

```c
/* The scheduler: PSP threads as host threads, one at a time (the baton).
 * docs/architecture.md, "Threading"; plan P4 Task 5. Internal to
 * runtime/kernel. Unless a comment says otherwise, every function is called
 * with the kernel lock held, by the thread holding the baton. */
#ifndef KHBBS_KERNEL_SCHED_H
#define KHBBS_KERNEL_SCHED_H

#include "khbbs/boot.h"
#include "khbbs/host.h"
#include "psprecomp/cpu.h"
#include "psprecomp/vfpu.h"

#include <setjmp.h>
#include <stdint.h>

#define KHBBS_MAX_THREADS  128
#define KHBBS_PRIO_IDLE    0x80u                 /* below every user priority (0x08..0x77) */
#define KHBBS_NO_DEADLINE  UINT64_MAX
#define KHBBS_HOST_STACK   ((size_t)8 << 20)     /* host C stack per guest thread */
#define KHBBS_UID_BASE     0x00040000u           /* kernel-object uids; sysmem's start at 0x10000 */

enum {                                           /* khbbs_thread.state */
    KTS_FREE = 0, KTS_DORMANT, KTS_READY, KTS_RUNNING, KTS_WAITING,
    KTS_SUSPENDED, KTS_WAITSUSPEND, KTS_ZOMBIE  /* zombie: exited and deleted, host thread not yet joined */
};

enum {                                           /* khbbs_wait.type: SceKernelThreadInfo.waitType below 0x100 */
    KW_NONE = 0, KW_SLEEP = 1, KW_DELAY = 2, KW_SEMA = 3, KW_EVENTFLAG = 4, KW_MBX = 5,
    KW_THREADEND = 9, KW_MUTEX = 12, KW_LWMUTEX = 13,
    KW_VBLANK = 0x100, KW_IO = 0x101, KW_UMD = 0x102   /* ours; reported as 0 (UNVERIFIED) */
};

typedef struct {
    psp_cpu_state cpu;
    psp_vfpu_ctx  vfpu;
    uint32_t      intr;          /* 1: interrupts enabled (sceKernelCpuSuspendIntr's state) */
} khbbs_context;

typedef struct khbbs_wait {
    int      type;               /* KW_* */
    uint32_t id;                 /* the object waited on, 0 for sleep and delay */
    int      cb;                 /* a *CB wait: callbacks are delivered while it waits */
    uint64_t deadline;           /* absolute guest µs, or KHBBS_NO_DEADLINE */
    uint32_t timeout_ptr;        /* guest SceUInt* that gets the time left, or 0 */
    uint32_t p[4];               /* the object's parameters (count wanted, bits, mode, out pointer) */
    /* After callbacks ran, try to satisfy the wait at once; 1 if it did (and
     * it set ->result). NULL: the wait only ends through a waker. */
    int    (*retry)(struct khbbs_wait *w);
    /* Set by the scheduler and the waker: */
    int      done;
    int32_t  result;
    uint64_t seq;                /* FIFO position */
    uint32_t timer;              /* the deadline's timer, 0 if none */
    struct khbbs_thread *thread; /* the waiter */
    struct khbbs_wait   *outer;  /* the wait this one is nested in (a wait inside a callback) */
} khbbs_wait;

typedef struct khbbs_thread {
    int        state;
    uint32_t   uid;
    char       name[32];
    char       trace_name[48];   /* NAME or NAME#k (Task 14) */
    uint32_t   entry, attr, init_prio, prio;
    uint32_t   stack_base, stack_size, gp, k0;
    uint32_t   wakeups;          /* sceKernelWakeupThread before sleep */
    int32_t    exit_status;
    uint64_t   ready_seq;        /* FIFO position in its priority */
    khbbs_wait *wait;            /* while waiting */
    khbbs_context ctx;           /* saved registers while not running */
    khbbs_host_thread *host;
    khbbs_host_cond   *cv;
    jmp_buf    unwind;           /* in trampoline(): stops, kills and exits land here */
    int        kill;             /* terminated: unwind when next woken */
    int        delete_on_exit;   /* root: deleted when its entry returns (observed) */
    void     (*body)(struct khbbs_thread *self);   /* host code instead of guest code (idle0) */
} khbbs_thread;

/* Lock. Not recursive. Guest code never runs with it held. */
void khbbs_klock(void);
void khbbs_kunlock(void);

/* Lifecycle. reset: called without the lock, not during a run; frees every
 * slot, recreates idle0; -1 if no host interface is set. run: main thread
 * only, without the lock; returns when the run is over, every host thread
 * joined. active: a run is in progress (any thread, no lock needed). */
int          khbbs_sched_reset(void);
khbbs_result khbbs_sched_run(void);
int          khbbs_sched_active(void);

khbbs_thread *khbbs_current(void);            /* the baton holder, NULL outside a run */
khbbs_thread *khbbs_idle(void);
const char   *khbbs_current_trace_name(void); /* lock not needed; NULL with no guest thread */
uint32_t      khbbs_uid_new(void);            /* kernel-object uids: one counter for every family */

/* Threads. alloc: a DORMANT slot with a new uid and its trace name, NULL if
 * full. by_uid: NULL for unknown, free and zombie slots. slot: iteration. */
khbbs_thread *khbbs_thread_alloc(const char *name);
khbbs_thread *khbbs_thread_by_uid(uint32_t uid);
khbbs_thread *khbbs_thread_slot(int i);       /* NULL if slot i is free */

/* DORMANT -> READY with t->ctx already set: spawns its host thread. 0 or -1. */
int  khbbs_sched_start(khbbs_thread *t);
/* Switch away if a thread with a strictly higher priority is ready. No-op
 * inside an interrupt handler (the service that ran it preempts after). */
void khbbs_sched_preempt(void);
/* READY thread t to the tail of its priority (after a priority change). */
void khbbs_sched_requeue(khbbs_thread *t);

/* Block the current thread on *w until a waker, the deadline or a kill ends
 * it; delivers callbacks first and between wake-ups if w->cb. Returns
 * w->result. KERR_ILLEGAL_CONTEXT inside an interrupt handler,
 * KERR_CAN_NOT_WAIT on idle0. Writes the time left to w->timeout_ptr.
 * Callers fill type, id, cb, deadline, timeout_ptr, p[] and retry; the rest
 * is the scheduler's. t->wait stays set for the whole wait (also while its
 * callbacks run); waiters are listed only while they are WAITING. */
int32_t khbbs_sched_wait(khbbs_wait *w);
/* End t's wait with `result` (no-op if it is not waiting). */
void khbbs_sched_wake(khbbs_thread *t, int32_t result);
/* Wake t for callbacks only: it runs them and waits again. */
void khbbs_sched_kick(khbbs_thread *t);
/* The threads waiting on (type, id), in queue order: priority then FIFO if
 * by_prio, else FIFO. out must hold KHBBS_MAX_THREADS. Returns the count. */
int  khbbs_waiters(int type, uint32_t id, int by_prio, khbbs_thread **out);
void khbbs_wake_all(int type, uint32_t id, int32_t result);

/* The current thread exits (del: and is deleted). Never returns. */
_Noreturn void khbbs_sched_exit(int32_t status, int del);
/* Terminate t (not current): DORMANT, its host thread unwinds. */
void khbbs_sched_kill(khbbs_thread *t, int32_t status);
/* Free a DORMANT or ZOMBIE slot that is not current: joins its host thread
 * (releasing the lock meanwhile). Does not release its stack. */
void khbbs_sched_free(khbbs_thread *t);

/* End the run: the first reason wins; every guest thread unwinds. The
 * calling thread must hold the baton. stop takes the lock; stop_locked
 * expects it held. Never return. */
_Noreturn void khbbs_sched_stop(khbbs_stop why, uint32_t addr, uint32_t code);
_Noreturn void khbbs_sched_stop_locked(khbbs_stop why, uint32_t addr, uint32_t code);

/* Run guest function `func` on the current thread with a0..a2 and sp, its
 * context saved and restored around it (callbacks, interrupt handlers).
 * as_intr: with interrupts disabled, as an interrupt handler. Returns $v0. */
uint32_t khbbs_call_guest(uint32_t func, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t sp, int as_intr);

void khbbs_ctx_save(khbbs_context *c);
void khbbs_ctx_restore(const khbbs_context *c);
uint32_t khbbs_intr_enabled(void);
void     khbbs_intr_set(uint32_t enabled);
int      khbbs_in_interrupt(void);

/* Callback delivery (callback.c installs it, Task 9): run t's pending
 * callbacks, return how many ran. NULL: no callbacks. */
typedef int (*khbbs_cb_hook)(khbbs_thread *t);
void khbbs_sched_set_cb_hook(khbbs_cb_hook fn);

#endif
```

- [ ] **Step 7: The scheduler**

`runtime/kernel/sched.c`:

```c
/* The scheduler. docs/architecture.md, "Threading"; plan P4 Task 5.
 *
 * Every guest thread is a host thread running trampoline(). g_cur holds the
 * baton: only that host thread runs guest code or touches kernel state.
 * Kernel code runs with g_lock held, guest code without it. A thread that
 * gives the baton away saves its context, sets g_cur, signals the next
 * thread's condvar and waits on its own until g_cur is itself again (or the
 * run stops, or it is terminated); then it restores its context.
 *
 * idle0 is a thread like the others at priority 0x80, always ready. Its body
 * is host code: when it holds the baton nothing else can run, so it advances
 * the clock to the next timer, fires it, and ends the run when every guest
 * thread has exited (KHBBS_RETURNED) or no timer can wake anything
 * (KHBBS_DEADLOCK). */
#include "sched.h"
#include "errors.h"
#include "ktime.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"

#include <stdio.h>
#include <string.h>

#define GUEST_RET 0x08000040u    /* $ra for khbbs_call_guest (thread.h: KHBBS_GUEST_RET) */

static const khbbs_host *H;
static khbbs_host_mutex *g_lock;
static khbbs_host_cond  *g_main_cv;
static khbbs_thread g_th[KHBBS_MAX_THREADS];
static khbbs_thread *g_cur, *g_idle;
static volatile int g_active;
static int g_stopping, g_over;
static khbbs_result g_res;
static uint64_t g_seq;
static uint32_t g_next_uid;
static uint32_t g_intr = 1;
static int g_in_intr;
static khbbs_cb_hook g_cb_hook;

/* NAME#k: the names used so far in this run and how often (Task 14). */
#define NAMES_MAX 256
static struct { char name[32]; uint32_t n; } g_names[NAMES_MAX];
static int g_nnames;

static void count_name(const char *name, char *out, size_t cap) {
    for (int i = 0; i < g_nnames; i++) {
        if (strcmp(g_names[i].name, name) != 0) continue;
        g_names[i].n++;
        if (out) snprintf(out, cap, "%s#%u", name, (unsigned)g_names[i].n);
        return;
    }
    if (g_nnames < NAMES_MAX) {
        snprintf(g_names[g_nnames].name, sizeof g_names[0].name, "%s", name);
        g_names[g_nnames++].n = 1;
    }
    if (out) snprintf(out, cap, "%s", name);
}

void khbbs_klock(void)   { H->mutex_lock(g_lock); }
void khbbs_kunlock(void) { H->mutex_unlock(g_lock); }

int khbbs_sched_active(void) { return g_active; }
khbbs_thread *khbbs_current(void) { return g_cur; }
khbbs_thread *khbbs_idle(void) { return g_idle; }
uint32_t khbbs_uid_new(void) { return g_next_uid++; }
void khbbs_sched_set_cb_hook(khbbs_cb_hook fn) { g_cb_hook = fn; }
uint32_t khbbs_intr_enabled(void) { return g_intr; }
void khbbs_intr_set(uint32_t enabled) { g_intr = enabled ? 1u : 0u; }
int khbbs_in_interrupt(void) { return g_in_intr > 0; }

const char *khbbs_current_trace_name(void) {
    return g_active && g_cur ? g_cur->trace_name : NULL;
}

void khbbs_ctx_save(khbbs_context *c) {
    c->cpu = psp_cpu;
    psp_vfpu_save(&c->vfpu);
    c->intr = g_intr;
}

void khbbs_ctx_restore(const khbbs_context *c) {
    psp_cpu = c->cpu;
    psp_vfpu_load(&c->vfpu);
    g_intr = c->intr;
}

/* ---- slots ---------------------------------------------------------------- */

static void join_host(khbbs_thread *t) {
    if (!t->host) return;
    khbbs_host_thread *h = t->host;
    t->host = NULL;
    H->mutex_unlock(g_lock);          /* the joined thread may still need the lock to finish */
    H->thread_join(h);
    H->mutex_lock(g_lock);
}

void khbbs_sched_free(khbbs_thread *t) {
    join_host(t);
    if (t->cv) H->cond_destroy(t->cv);
    memset(t, 0, sizeof *t);
}

khbbs_thread *khbbs_thread_alloc(const char *name) {
    for (int i = 0; i < KHBBS_MAX_THREADS; i++) {
        khbbs_thread *t = &g_th[i];
        if (t->state == KTS_ZOMBIE) khbbs_sched_free(t);
        if (t->state != KTS_FREE) continue;
        if (H->cond_create(&t->cv) != 0) return NULL;
        t->state = KTS_DORMANT;
        t->uid = khbbs_uid_new();
        snprintf(t->name, sizeof t->name, "%s", name ? name : "");
        count_name(t->name, t->trace_name, sizeof t->trace_name);
        return t;
    }
    return NULL;
}

khbbs_thread *khbbs_thread_by_uid(uint32_t uid) {
    for (int i = 0; uid && i < KHBBS_MAX_THREADS; i++)
        if (g_th[i].state != KTS_FREE && g_th[i].state != KTS_ZOMBIE && g_th[i].uid == uid) return &g_th[i];
    return NULL;
}

khbbs_thread *khbbs_thread_slot(int i) {
    return i >= 0 && i < KHBBS_MAX_THREADS && g_th[i].state != KTS_FREE ? &g_th[i] : NULL;
}

/* ---- the baton ------------------------------------------------------------ */

static khbbs_thread *pick_next(void) {
    khbbs_thread *best = NULL;
    for (int i = 0; i < KHBBS_MAX_THREADS; i++) {
        khbbs_thread *t = &g_th[i];
        if (t->state != KTS_READY && t->state != KTS_RUNNING) continue;
        if (!best || t->prio < best->prio || (t->prio == best->prio && t->ready_seq < best->ready_seq))
            best = t;
    }
    return best;
}

/* Wait until t holds the baton. On a stop or a kill, unwind instead. */
static void wait_baton(khbbs_thread *t) {
    while (g_cur != t && !g_stopping && !t->kill) H->cond_wait(t->cv, g_lock);
    if (g_stopping || t->kill) {
        H->mutex_unlock(g_lock);
        longjmp(t->unwind, 1);
    }
}

static void hand_to(khbbs_thread *next) {
    next->state = KTS_RUNNING;
    g_cur = next;
    H->cond_signal(next->cv);
}

/* Give the baton to next and wait for it to come back. The current thread
 * stays READY (and keeps its place at the head of its priority) if it was
 * running, or keeps the state its caller set (WAITING). */
static void switch_to(khbbs_thread *next) {
    khbbs_thread *me = g_cur;
    if (next == me) return;
    khbbs_ctx_save(&me->ctx);
    if (me->state == KTS_RUNNING) me->state = KTS_READY;
    hand_to(next);
    wait_baton(me);
    khbbs_ctx_restore(&me->ctx);
}

static void make_ready(khbbs_thread *t) {
    if (t->state == KTS_WAITSUSPEND) { t->state = KTS_SUSPENDED; return; }
    t->state = KTS_READY;
    t->ready_seq = ++g_seq;
}

void khbbs_sched_preempt(void) {
    if (g_in_intr || !g_cur) return;
    khbbs_thread *n = pick_next();
    if (n && n != g_cur && n->prio < g_cur->prio) switch_to(n);
}

void khbbs_sched_requeue(khbbs_thread *t) {
    if (t->state == KTS_READY) t->ready_seq = ++g_seq;
}

/* ---- waiting -------------------------------------------------------------- */

/* End wait w. Its thread becomes ready only if w is the wait it is blocked
 * in: an outer wait (the thread is in a callback, maybe waiting again inside
 * it) is only marked done and ends when the callback returns. */
static void wake_wait(khbbs_wait *w, int32_t result) {
    if (w->done) return;
    w->done = 1;
    w->result = result;
    if (w->timer) { khbbs_timer_cancel(w->timer); w->timer = 0; }
    khbbs_thread *t = w->thread;
    if (t->wait == w && (t->state == KTS_WAITING || t->state == KTS_WAITSUSPEND)) make_ready(t);
}

static void wait_timeout(void *arg, uint64_t when) {
    (void)when;
    khbbs_wait *w = arg;
    w->timer = 0;                               /* this timer is firing: nothing to cancel */
    wake_wait(w, w->type == KW_DELAY ? 0 : (int32_t)KERR_WAIT_TIMEOUT);
}

int32_t khbbs_sched_wait(khbbs_wait *w) {
    khbbs_thread *me = g_cur;
    if (g_in_intr) return (int32_t)KERR_ILLEGAL_CONTEXT;
    if (me == g_idle) return (int32_t)KERR_CAN_NOT_WAIT;
    w->thread = me;
    w->outer = me->wait;                        /* non-NULL: we are inside a callback of a *CB wait */
    w->done = 0;
    w->seq = ++g_seq;
    w->timer = w->deadline != KHBBS_NO_DEADLINE ? khbbs_timer_add(w->deadline, wait_timeout, w) : 0;
    me->wait = w;
    for (;;) {
        if (w->cb && g_cb_hook) {
            /* Callbacks run with the thread RUNNING, so no object grants it
             * anything meanwhile; retry() then takes what became available. */
            while (!w->done && g_cb_hook(me) > 0) {}
            if (!w->done && w->retry && w->retry(w)) w->done = 1;
        }
        if (w->done) break;
        me->state = KTS_WAITING;
        switch_to(pick_next());
        if (w->done) break;                    /* else: kicked for callbacks */
    }
    me->wait = w->outer;
    if (w->timer) { khbbs_timer_cancel(w->timer); w->timer = 0; }
    if (w->timeout_ptr) {
        const uint64_t now = khbbs_now();
        psp_write32(w->timeout_ptr, w->deadline > now ? (uint32_t)(w->deadline - now) : 0);
    }
    return w->result;
}

void khbbs_sched_wake(khbbs_thread *t, int32_t result) {
    if (t->wait) wake_wait(t->wait, result);
}

void khbbs_sched_kick(khbbs_thread *t) {
    if (t->state == KTS_WAITING && t->wait && t->wait->cb && !t->wait->done) make_ready(t);
}

static int before(const khbbs_thread *a, const khbbs_thread *b, int by_prio) {
    if (by_prio && a->prio != b->prio) return a->prio < b->prio;
    return a->wait->seq < b->wait->seq;
}

int khbbs_waiters(int type, uint32_t id, int by_prio, khbbs_thread **out) {
    int n = 0;
    for (int i = 0; i < KHBBS_MAX_THREADS; i++) {
        khbbs_thread *t = &g_th[i];
        if (t->state != KTS_WAITING && t->state != KTS_WAITSUSPEND) continue;
        if (!t->wait || t->wait->done || t->wait->type != type || t->wait->id != id) continue;
        out[n++] = t;
    }
    for (int i = 1; i < n; i++) {               /* insertion sort: n is small */
        khbbs_thread *x = out[i];
        int j = i;
        while (j > 0 && before(x, out[j - 1], by_prio)) { out[j] = out[j - 1]; j--; }
        out[j] = x;
    }
    return n;
}

void khbbs_wake_all(int type, uint32_t id, int32_t result) {
    khbbs_thread *q[KHBBS_MAX_THREADS];
    const int n = khbbs_waiters(type, id, 0, q);
    for (int i = 0; i < n; i++) khbbs_sched_wake(q[i], result);
}

/* ---- exit, kill, stop ----------------------------------------------------- */

/* The current thread is done: dormant (or zombie), its waiters woken, the
 * baton handed on. The caller then leaves the thread without waiting. */
static void finish(khbbs_thread *t, int32_t status, int del) {
    t->exit_status = status;
    t->state = del ? KTS_ZOMBIE : KTS_DORMANT;
    if (del && t->stack_base) { psp_sysmem_release(t->stack_base); t->stack_base = 0; }
    khbbs_wake_all(KW_THREADEND, t->uid, status);
    hand_to(pick_next());
}

_Noreturn void khbbs_sched_exit(int32_t status, int del) {
    khbbs_thread *me = g_cur;
    finish(me, status, del);
    H->mutex_unlock(g_lock);
    longjmp(me->unwind, 2);
}

void khbbs_sched_kill(khbbs_thread *t, int32_t status) {
    for (khbbs_wait *w = t->wait; w; w = w->outer)   /* its stack is about to unwind */
        if (w->timer) { khbbs_timer_cancel(w->timer); w->timer = 0; }
    t->wait = NULL;
    t->exit_status = status;
    t->state = KTS_DORMANT;
    t->kill = 1;
    H->cond_signal(t->cv);
    khbbs_wake_all(KW_THREADEND, t->uid, status);
}

_Noreturn void khbbs_sched_stop_locked(khbbs_stop why, uint32_t addr, uint32_t code) {
    khbbs_thread *me = g_cur;
    if (!g_stopping) {
        g_res.why = why;
        g_res.addr = addr;
        g_res.code = code;
        g_stopping = 1;
    }
    g_over = 1;
    H->cond_signal(g_main_cv);
    for (int i = 0; i < KHBBS_MAX_THREADS; i++)
        if (g_th[i].cv) H->cond_signal(g_th[i].cv);
    H->mutex_unlock(g_lock);
    longjmp(me->unwind, 1);
}

_Noreturn void khbbs_sched_stop(khbbs_stop why, uint32_t addr, uint32_t code) {
    H->mutex_lock(g_lock);
    khbbs_sched_stop_locked(why, addr, code);
}

/* ---- threads' host side --------------------------------------------------- */

static void trampoline(void *arg) {
    khbbs_thread *t = arg;
    H->mutex_lock(g_lock);
    if (setjmp(t->unwind) != 0) return;   /* stop, kill or exit: the lock is not held */
    wait_baton(t);
    khbbs_ctx_restore(&t->ctx);
    if (t->body) t->body(t);             /* idle0: leaves only through a stop */
    H->mutex_unlock(g_lock);
    psp_dispatch(t->entry);              /* a missing entry is a dispatch miss like any other */
    H->mutex_lock(g_lock);
    finish(t, (int32_t)psp_cpu.r[PSP_REG_V0], t->delete_on_exit);
    H->mutex_unlock(g_lock);
}

int khbbs_sched_start(khbbs_thread *t) {
    join_host(t);                        /* a restarted thread's old host thread has ended */
    t->kill = 0;
    t->wait = NULL;
    make_ready(t);
    if (H->thread_start(&t->host, trampoline, t, KHBBS_HOST_STACK) != 0) {
        t->host = NULL;
        t->state = KTS_DORMANT;
        return -1;
    }
    return 0;
}

uint32_t khbbs_call_guest(uint32_t func, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t sp, int as_intr) {
    khbbs_context saved;
    khbbs_ctx_save(&saved);
    psp_vfpu_ctx clean = saved.vfpu;     /* the handler starts with no prefix pending */
    clean.prefix_set[0] = clean.prefix_set[1] = clean.prefix_set[2] = 0;
    psp_vfpu_load(&clean);
    psp_cpu.r[PSP_REG_A0] = a0;
    psp_cpu.r[PSP_REG_A1] = a1;
    psp_cpu.r[PSP_REG_A2] = a2;
    psp_cpu.r[PSP_REG_SP] = sp;
    psp_cpu.r[PSP_REG_RA] = GUEST_RET;
    if (as_intr) { g_in_intr++; g_intr = 0; }
    H->mutex_unlock(g_lock);
    psp_dispatch(func);
    H->mutex_lock(g_lock);
    const uint32_t v0 = psp_cpu.r[PSP_REG_V0];
    if (as_intr) g_in_intr--;
    khbbs_ctx_restore(&saved);
    return v0;
}

static int any_live(void) {
    for (int i = 0; i < KHBBS_MAX_THREADS; i++) {
        if (&g_th[i] == g_idle) continue;
        switch (g_th[i].state) {
        case KTS_READY: case KTS_RUNNING: case KTS_WAITING: case KTS_SUSPENDED: case KTS_WAITSUSPEND:
            return 1;
        default:
            break;
        }
    }
    return 0;
}

static void idle_body(khbbs_thread *self) {
    for (;;) {
        if (!any_live()) khbbs_sched_stop_locked(KHBBS_RETURNED, 0, 0);
        khbbs_thread *n = pick_next();
        if (n != self) { switch_to(n); continue; }
        const uint64_t next = khbbs_timer_next();
        if (next == KHBBS_NO_DEADLINE) khbbs_sched_stop_locked(KHBBS_DEADLOCK, 0, 0);
        if (khbbs_time_past_limit(next)) khbbs_sched_stop_locked(KHBBS_TIME_LIMIT, 0, 0);
        khbbs_time_idle_until(next);
        khbbs_timer_fire_due();
    }
}

int khbbs_sched_reset(void) {
    H = khbbs_host_get();
    if (!H) return -1;
    if (!g_lock && (H->mutex_create(&g_lock) != 0 || H->cond_create(&g_main_cv) != 0)) return -1;
    for (int i = 0; i < KHBBS_MAX_THREADS; i++) {
        if (g_th[i].host) H->thread_join(g_th[i].host);   /* none after a run: run() joins them */
        if (g_th[i].cv) H->cond_destroy(g_th[i].cv);
    }
    memset(g_th, 0, sizeof g_th);
    g_cur = NULL;
    g_active = g_stopping = g_over = 0;
    memset(&g_res, 0, sizeof g_res);
    g_seq = 0;
    g_next_uid = KHBBS_UID_BASE;
    g_intr = 1;
    g_in_intr = 0;
    g_nnames = 0;
    H->mutex_lock(g_lock);
    g_idle = khbbs_thread_alloc("idle0");
    count_name("idle1", NULL, 0);       /* PPSSPP's second idle thread: counted, not created */
    if (g_idle) {
        g_idle->prio = g_idle->init_prio = KHBBS_PRIO_IDLE;
        g_idle->body = idle_body;
        g_idle->ctx.intr = 1;
        psp_vfpu_ctx_init(&g_idle->ctx.vfpu);
    }
    H->mutex_unlock(g_lock);
    return g_idle ? 0 : -1;
}

khbbs_result khbbs_sched_run(void) {
    H->mutex_lock(g_lock);
    g_active = 1;
    if (khbbs_sched_start(g_idle) != 0) {
        g_res.why = KHBBS_BAD_ARGS;
        g_over = 1;
    } else {
        hand_to(pick_next());
    }
    while (!g_over) H->cond_wait(g_main_cv, g_lock);
    H->mutex_unlock(g_lock);
    for (int i = 0; i < KHBBS_MAX_THREADS; i++) {
        if (!g_th[i].host) continue;
        H->thread_join(g_th[i].host);
        g_th[i].host = NULL;
    }
    H->mutex_lock(g_lock);
    g_active = 0;
    g_cur = NULL;
    H->mutex_unlock(g_lock);
    return g_res;
}
```

Notes for the implementer:
- `khbbs_sched_reset` keeps the lock and main condvar across runs (created once); set the host once per process.
- `finish()` hands the baton on but does not wait; the thread then returns from `trampoline()` (or `longjmp`s there from `khbbs_sched_exit`). Its host thread ends without the lock, so `join_host()` never deadlocks.
- `khbbs_sched_kill()` only marks and signals: the victim is in `wait_baton()` and unwinds by itself the next time it gets the lock.

In `runtime/CMakeLists.txt`, add `kernel/sched.c` and `kernel/ktime.c` to the sources and, after `target_include_directories(... PUBLIC ...)`:

```cmake
target_include_directories(khbbs_runtime PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
```

`sched.c` uses `psp_sysmem_release` (`psprecomp/hle.h`), already included.

- [ ] **Step 8: Run the tests**

```bash
cmake --build build/ci && ctest --test-dir build/ci --output-on-failure
```

Expected: `100% tests passed`; `test_sched: ok`. `test_boot` still passes: `boot.c` itself has not changed apart from the stop names, and the new `khbbs_boot_opts.time` field is zero in its compound literals.

- [ ] **Step 9: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel runtime/include/khbbs/boot.h runtime/boot.c runtime/CMakeLists.txt \
    tests/runtime/test_sched.c tests/runtime/CMakeLists.txt
git commit -m "kernel: host-thread-plus-baton scheduler, waits, virtual clock and timers, idle0

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: Thread layout and sysmem placement

Threads get PPSSPP's stack layout (the "Observed thread layout" table), and the user partition goes around the image. This fixes the P3 "NULL write32 at 0x4": `user_main` stores through `$k0` at `0x08A8BF3C` (`sw $s5, 4($k0)`), and `$k0` now points at its kernel block.

**Files:**
- Create: `runtime/kernel/thread.h`, `runtime/kernel/thread.c`
- Modify: `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_layout.c`; Modify: `tests/runtime/CMakeLists.txt`

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_layout.c`:

```c
/* Thread creation and start against the values PPSSPP shows for KH BBS's
 * boot (plan P4, "Observed thread layout"): stacks from the top of the user
 * partition, the k0 block, argument copies, $sp/$ra/$fp, the 0xFF fill, and
 * the game's first heap block past the image. Synthetic entry points. */
#include "kernel/errors.h"
#include "kernel/ktime.h"
#include "kernel/sched.h"
#include "kernel/thread.h"
#include "khbbs/host_posix.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define R(t, reg) ((t)->ctx.cpu.r[PSP_REG_##reg])

static void nop(void) {}

int main(void) {
    CHECK(psp_mem_init() == 0, "memory");
    psp_hle_init();
    khbbs_host_set(khbbs_host_posix());
    psp_register(0x08A8BD58u, nop);              /* stand-ins for the three entries */
    psp_register(0x08A8BE6Cu, nop);
    psp_register(0x0880F750u, nop);

    CHECK(khbbs_sched_reset() == 0, "reset");
    khbbs_time_reset(KHBBS_TIME_DET);
    khbbs_image img = { .entry = 0x08A8BD58u, .gp = 0x08BD97E0u, .lo = 0x08804018u, .hi = 0x09F3D7D0u };
    khbbs_klock();
    khbbs_mem_layout(&img);

    /* root: 0x400 bytes at the top, path argument of 0x21 bytes. */
    const char *path = "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN";
    int32_t err = 1;
    khbbs_thread *root = khbbs_thread_create("root", img.entry, 0x20, 0x400, KHBBS_ROOT_ATTR, img.gp, &err);
    CHECK(root && err == 0, "create root");
    CHECK(root->stack_base == 0x09FFFC00u && root->stack_size == 0x400u, "root stack 0x09FFFC00");
    CHECK(khbbs_thread_start_host(root, path, (uint32_t)strlen(path) + 1) == 0, "start root");
    CHECK(root->k0 == 0x09FFFF00u && R(root, K0) == 0x09FFFF00u, "root k0 0x09FFFF00");
    CHECK(R(root, A0) == 0x21u && R(root, A1) == 0x09FFFED0u, "root a0 0x21, a1 0x09FFFED0");
    CHECK(R(root, SP) == 0x09FFFE90u, "root sp 0x09FFFE90");
    CHECK(R(root, RA) == 0x08000030u && R(root, FP) == 0xDEADBEEFu, "root ra 0x08000030, fp unset");
    CHECK(R(root, GP) == 0x08BD97E0u && R(root, S0) == 0xDEADBEEFu && root->ctx.cpu.hi == 0xDEADBEEFu,
          "gp from the module; other registers 0xDEADBEEF");
    CHECK(strcmp(psp_mem_ptr(0x09FFFED0u, 0x21), path) == 0, "path copied to a1");
    CHECK(psp_read8(0x09FFFEF1u) == 0xFFu, "the bytes after the arguments keep the fill");
    CHECK(psp_read32(0x09FFFFC0u) == root->uid && psp_read32(0x09FFFFC8u) == 0x09FFFC00u,
          "k0 block: uid at +0xC0, stack base at +0xC8");
    CHECK(psp_read32(0x09FFFF00u) == 0 && psp_read32(0x09FFFFF4u) == 0 && psp_read32(0x09FFFFF8u) == 0xFFFFFFFFu,
          "k0 block zeroed up to +0xF8, the last 8 bytes keep the fill");
    CHECK(psp_read32(0x09FFFC00u) == root->uid && psp_read32(0x09FFFC04u) == 0xFFFFFFFFu,
          "stack bottom: uid, then fill");

    /* user_main: 0x37000 bytes below root, the same 0x21-byte argument. */
    khbbs_thread *um = khbbs_thread_create("user_main", 0x08A8BE6Cu, 0x13, 0x37000, 0x80004000u, img.gp, &err);
    CHECK(um && um->stack_base == 0x09FC8C00u, "user_main stack 0x09FC8C00");
    CHECK(khbbs_thread_start(um, 0x21, 0x09FFFED0u) == 0, "start user_main");
    CHECK(R(um, K0) == 0x09FFFB00u && R(um, A0) == 0x21u && R(um, A1) == 0x09FFFAD0u, "user_main k0, a0, a1");
    CHECK(R(um, SP) == 0x09FFFA90u && R(um, RA) == 0x09FFFA90u && R(um, FP) == 0x09FFFA90u,
          "user_main sp = ra = fp = 0x09FFFA90");
    CHECK(psp_read32(0x09FFFA90u) == 0x03E00008u && psp_read32(0x09FFFA94u) == 0x010C000Cu &&
          psp_read32(0x09FFFA98u) == 0x1000FFFFu && psp_read32(0x09FFFA9Cu) == 0,
          "the return stub at sp");
    CHECK(psp_lookup(0x09FFFA90u) != NULL, "the stub address dispatches (to thread exit)");
    CHECK(strcmp(psp_mem_ptr(0x09FFFAD0u, 0x21), path) == 0, "argument copied from the guest");

    /* The game's first block (psprecomp's handler, our placement). */
    psp_mem_write_block(0x08000200u, "UserSbrk", 9);
    psp_cpu.r[PSP_REG_A0] = 2; psp_cpu.r[PSP_REG_A1] = 0x08000200u; psp_cpu.r[PSP_REG_A2] = 3;
    psp_cpu.r[PSP_REG_A3] = 0x1000u; psp_cpu.r[PSP_REG_T0] = 0x1000u;
    psp_hle_call(0x237DBD4Fu);
    psp_cpu.r[PSP_REG_A0] = psp_cpu.r[PSP_REG_V0];
    psp_hle_call(0x9D9A5BA1u);
    CHECK(psp_cpu.r[PSP_REG_V0] == 0x09F3E000u, "AllocPartitionMemory block at 0x09F3E000");

    /* DiscInfoBind: 0x1000 bytes below user_main, no arguments. */
    khbbs_thread *dib = khbbs_thread_create("DiscInfoBind", 0x0880F750u, 0x14, 0x1000, 0, img.gp, &err);
    CHECK(dib && dib->stack_base == 0x09FC7C00u, "DiscInfoBind stack 0x09FC7C00");
    CHECK(khbbs_thread_start(dib, 0, 0) == 0, "start DiscInfoBind");
    CHECK(R(dib, K0) == 0x09FC8B00u && R(dib, A0) == 0 && R(dib, A1) == 0 && R(dib, SP) == 0x09FC8AC0u,
          "DiscInfoBind k0 0x09FC8B00, no args, sp 0x09FC8AC0");

    /* idle0 lives in the kernel partition, as PPSSPP's does. */
    khbbs_thread *idle = khbbs_idle();
    CHECK(idle->k0 == 0x083FFF00u && psp_read32(0x083FFFC0u) == idle->uid &&
          psp_read32(0x083FFFC8u) == 0x083FF000u, "idle0's k0 block at 0x083FFF00");

    /* Errors. */
    CHECK(!khbbs_thread_create("x", 0x08A8BE6Cu, 0x07, 0x1000, 0, img.gp, &err) &&
          err == (int32_t)KERR_ILLEGAL_PRIORITY, "priority below 0x08");
    CHECK(!khbbs_thread_create("x", 0x08A8BE6Cu, 0x78, 0x1000, 0, img.gp, &err) &&
          err == (int32_t)KERR_ILLEGAL_PRIORITY, "priority above 0x77");
    CHECK(!khbbs_thread_create("x", 0x08A8BE6Cu, 0x20, 0x100, 0, img.gp, &err) &&
          err == (int32_t)KERR_ILLEGAL_STACK_SIZE, "stack below 0x200");
    CHECK(!khbbs_thread_create("x", 0x00001000u, 0x20, 0x1000, 0, img.gp, &err) &&
          err == (int32_t)KERR_ILLEGAL_ENTRY, "entry outside RAM");
    CHECK(khbbs_thread_start(dib, 0, 0) == (int32_t)KERR_NOT_DORMANT, "start twice");
    khbbs_kunlock();

    /* The three run (each entry returns at once) and the run ends cleanly. */
    khbbs_result r = khbbs_sched_run();
    CHECK(r.why == KHBBS_RETURNED, "every thread exited");
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");

    printf("%s\n", failures ? "test_layout: FAILED" : "test_layout: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(layout)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci`
Expected: compile error, `kernel/thread.h` not found.

- [ ] **Step 3: Implement the layout**

`runtime/kernel/thread.h`:

```c
/* Guest threads: PPSSPP-compatible stack layout and the ThreadManForUser
 * thread functions. Internal to runtime/kernel; lock held for every call. */
#ifndef KHBBS_KERNEL_THREAD_H
#define KHBBS_KERNEL_THREAD_H

#include "sched.h"
#include "khbbs/loader.h"

#define KHBBS_USER_LO         0x08800000u   /* the user partition */
#define KHBBS_USER_HI         0x0A000000u
#define KHBBS_IDLE_STACK      0x083FF000u   /* idle0: 4 KB in the kernel partition (PPSSPP's) */
#define KHBBS_IDLE_STACK_SIZE 0x1000u
#define KHBBS_INTR_STACK_TOP  0x083FF000u   /* interrupt handlers: 0x083FE000..0x083FF000 */
#define KHBBS_ROOT_RA         0x08000030u   /* PPSSPP's $ra for root */
#define KHBBS_IDLE_RA         0x08000020u   /* PPSSPP's $ra for idle0 */
#define KHBBS_GUEST_RET       0x08000040u   /* $ra for callbacks and interrupt handlers */
#define KHBBS_ROOT_PRIO       0x20u
#define KHBBS_ROOT_STACK      0x400u
#define KHBBS_ROOT_ATTR       0x80000000u   /* PSP_THREAD_ATTR_USER; UNVERIFIED for root */
#define KHBBS_REG_FILL        0xDEADBEEFu   /* PPSSPP's value for registers a new thread does not set */

#define PSP_THREAD_ATTR_NO_FILLSTACK 0x00100000u   /* pspthreadman.h */

/* Sysmem: the user partition around the image; idle0's stack and k0 block;
 * the $ra addresses registered in the dispatch table. */
void khbbs_mem_layout(const khbbs_image *img);
/* A DORMANT thread with its stack allocated high and filled. NULL and *err
 * = KERR_ILLEGAL_PRIORITY (outside 0x08..0x77), KERR_ILLEGAL_STACK_SIZE
 * (< 0x200), KERR_ILLEGAL_ENTRY (outside RAM) or KERR_NO_MEMORY. */
khbbs_thread *khbbs_thread_create(const char *name, uint32_t entry, uint32_t prio,
                                  uint32_t stack_size, uint32_t attr, uint32_t gp, int32_t *err);
/* Make t READY with its k0 block, copied arguments (from guest argp), return
 * stub and registers. 0, KERR_NOT_DORMANT, KERR_ILLEGAL_ADDR (bad argp),
 * KERR_ILLEGAL_SIZE (arguments do not fit) or KERR_NO_MEMORY. */
int32_t khbbs_thread_start(khbbs_thread *t, uint32_t arglen, uint32_t argp);
/* The same for root at boot: arguments from host memory, $ra = KHBBS_ROOT_RA. */
int32_t khbbs_thread_start_host(khbbs_thread *t, const void *args, uint32_t arglen);
/* The ThreadManForUser thread NIDs (Task 8). */
void khbbs_thread_register(void);

#endif
```

`runtime/kernel/thread.c` (the layout part; Task 8 appends the handlers):

```c
/* Guest threads. Plan P4 Tasks 6 and 8. The layout reproduces what PPSSPP
 * shows for KH BBS's boot (plan, "Observed thread layout"). */
#include "thread.h"
#include "errors.h"
#include "ktime.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <string.h>

/* PPSSPP's thread-return stub at a new thread's $sp: jr $ra; syscall; b .; nop */
static const uint32_t RET_STUB[4] = { 0x03E00008u, 0x010C000Cu, 0x1000FFFFu, 0x00000000u };

/* The guest jumped to a thread's return address: the entry function returned
 * through it. (A C return from the entry ends the thread in trampoline().) */
static void thread_return(void) {
    khbbs_klock();
    khbbs_sched_exit((int32_t)psp_cpu.r[PSP_REG_V0], khbbs_current()->delete_on_exit);
}

static void guest_ret(void) {}           /* KHBBS_GUEST_RET: the C return already happened */

static void fill_stack(khbbs_thread *t) {
    if (!(t->attr & PSP_THREAD_ATTR_NO_FILLSTACK)) {
        uint8_t *p = psp_mem_ptr(t->stack_base, t->stack_size);
        if (p) memset(p, 0xFF, t->stack_size);
    }
    psp_write32(t->stack_base, t->uid);  /* observed: the stack's first word is the uid */
}

static void write_k0_block(khbbs_thread *t) {
    t->k0 = t->stack_base + t->stack_size - 0x100u;
    uint8_t *p = psp_mem_ptr(t->k0, 0xF8u);
    if (p) memset(p, 0, 0xF8u);          /* observed: the last 8 bytes keep the fill */
    psp_write32(t->k0 + 0xC0u, t->uid);
    psp_write32(t->k0 + 0xC8u, t->stack_base);
}

static void init_regs(khbbs_thread *t, uint32_t a0, uint32_t a1, uint32_t sp, uint32_t ra, uint32_t fp) {
    psp_cpu_state *c = &t->ctx.cpu;
    memset(c, 0, sizeof *c);             /* FPU and VFPU at 0: UNVERIFIED */
    for (int i = 1; i < PSP_NUM_GPR; i++) c->r[i] = KHBBS_REG_FILL;
    c->hi = c->lo = KHBBS_REG_FILL;
    c->r[PSP_REG_A0] = a0;
    c->r[PSP_REG_A1] = a1;
    c->r[PSP_REG_SP] = sp;
    c->r[PSP_REG_K0] = t->k0;
    c->r[PSP_REG_GP] = t->gp;
    c->r[PSP_REG_RA] = ra;
    c->r[PSP_REG_FP] = fp;
    psp_vfpu_ctx_init(&t->ctx.vfpu);
    t->ctx.intr = 1;
}

void khbbs_mem_layout(const khbbs_image *img) {
    psp_sysmem_reset();
    psp_sysmem_set_range(KHBBS_USER_LO, KHBBS_USER_HI);
    const uint32_t end = (img->hi + 0xFFu) & ~0xFFu;
    if (end > KHBBS_USER_LO && end <= KHBBS_USER_HI)
        psp_sysmem_reserve(KHBBS_USER_LO, end - KHBBS_USER_LO);
    psp_register(KHBBS_ROOT_RA, thread_return);
    psp_register(KHBBS_GUEST_RET, guest_ret);

    khbbs_thread *idle = khbbs_idle();
    idle->stack_base = KHBBS_IDLE_STACK;
    idle->stack_size = KHBBS_IDLE_STACK_SIZE;
    idle->gp = img->gp;
    fill_stack(idle);
    write_k0_block(idle);
    init_regs(idle, 0, 0, idle->k0, KHBBS_IDLE_RA, KHBBS_REG_FILL);
}

khbbs_thread *khbbs_thread_create(const char *name, uint32_t entry, uint32_t prio,
                                  uint32_t stack_size, uint32_t attr, uint32_t gp, int32_t *err) {
    if (prio < 0x08u || prio > 0x77u) { *err = (int32_t)KERR_ILLEGAL_PRIORITY; return NULL; }
    if (stack_size < 0x200u) { *err = (int32_t)KERR_ILLEGAL_STACK_SIZE; return NULL; }
    if (entry < PSP_RAM_BASE || entry >= PSP_RAM_BASE + PSP_RAM_SIZE) { *err = (int32_t)KERR_ILLEGAL_ENTRY; return NULL; }
    const uint32_t size = (stack_size + 0xFFu) & ~0xFFu;
    const uint32_t base = psp_sysmem_alloc(size, 1);
    if (!base) { *err = (int32_t)KERR_NO_MEMORY; return NULL; }
    khbbs_thread *t = khbbs_thread_alloc(name);
    if (!t) { psp_sysmem_release(base); *err = (int32_t)KERR_NO_MEMORY; return NULL; }
    t->entry = entry;
    t->init_prio = t->prio = prio;
    t->attr = attr;
    t->gp = gp;
    t->stack_base = base;
    t->stack_size = size;
    fill_stack(t);                       /* at creation: UNVERIFIED (the gate's snapshot checks) */
    *err = 0;
    return t;
}

static int32_t start(khbbs_thread *t, const void *host_args, uint32_t argp, uint32_t arglen, int root) {
    if (t->state != KTS_DORMANT) return (int32_t)KERR_NOT_DORMANT;
    write_k0_block(t);
    uint32_t a1 = 0, below = t->k0;
    if (arglen && (host_args || argp)) {
        const uint32_t n = (arglen + 15u) & ~15u;
        if (n + 0x40u + 16u > t->k0 - t->stack_base) return (int32_t)KERR_ILLEGAL_SIZE;
        a1 = t->k0 - n;
        const void *src = host_args ? host_args : psp_mem_ptr(argp, arglen);
        if (!src) return (int32_t)KERR_ILLEGAL_ADDR;
        memmove(psp_mem_ptr(a1, arglen), src, arglen);
        below = a1;
    }
    const uint32_t sp = below - 0x40u;
    if (root) {
        init_regs(t, arglen, a1, sp, KHBBS_ROOT_RA, KHBBS_REG_FILL);
    } else {
        for (int i = 0; i < 4; i++) psp_write32(sp + 4u * (uint32_t)i, RET_STUB[i]);
        psp_register(sp, thread_return);
        init_regs(t, arglen, a1, sp, sp, sp);
    }
    t->wakeups = 0;
    t->exit_status = 0;
    return khbbs_sched_start(t) == 0 ? 0 : (int32_t)KERR_NO_MEMORY;
}

int32_t khbbs_thread_start(khbbs_thread *t, uint32_t arglen, uint32_t argp) {
    return start(t, NULL, argp, arglen, 0);
}

int32_t khbbs_thread_start_host(khbbs_thread *t, const void *args, uint32_t arglen) {
    return start(t, args, 0, arglen, 1);
}
```

Add `kernel/thread.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_layout: ok`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/thread.h runtime/kernel/thread.c runtime/CMakeLists.txt \
    tests/runtime/test_layout.c tests/runtime/CMakeLists.txt
git commit -m "kernel: PPSSPP-compatible thread layout (k0 block, args, stub); user partition around the image

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Stop paths and the boot thread `root`

`khbbs_boot` now creates `root` (priority `0x20`, 0x400-byte stack, the boot path as its argument) and runs the scheduler. Every stop goes through `khbbs_sched_stop`. `BOOT_ARGP`/`BOOT_STACK_TOP` go away. This task also creates the kernel's registration entry point, `nids.h`, the handler helpers and the NID check, and moves `sceKernelExitGame` onto a stop.

**Files:**
- Create: `runtime/kernel/kernel.h`, `runtime/kernel/kernel.c`, `runtime/include/khbbs/kernel.h`, `runtime/kernel/nids.h`, `runtime/kernel/khle.h`
- Modify: `runtime/boot.c`, `runtime/include/khbbs/boot.h` (the doc comment), `runtime/CMakeLists.txt`
- Modify: `tests/runtime/test_boot.c`, `tests/runtime/CMakeLists.txt`
- Modify: `hosts/pc/main.c`
- Create: `tests/tools/test_kernel_nids.py`

- [ ] **Step 1: Write the failing tests**

`tests/tools/test_kernel_nids.py`:

```python
"""Every NID_<name> in runtime/kernel/nids.h is SHA-1(name)[0:4], little-endian:
a typo in a NID or a name cannot survive. NIDs that are not SHA-1 based use
the NIDX_ prefix and are not checked."""
import pathlib, re, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from import_report import nid_of  # noqa: E402

DEFINE = re.compile(r"^#define NID_(\w+)\s+0x([0-9A-F]{8})u\b", re.M)


class KernelNids(unittest.TestCase):
    def test_every_nid_matches_its_name(self):
        text = (ROOT / "runtime" / "kernel" / "nids.h").read_text()
        pairs = DEFINE.findall(text)
        self.assertGreater(len(pairs), 100)
        for name, nid in pairs:
            with self.subTest(name=name):
                self.assertEqual(int(nid, 16), nid_of(name), f"NID_{name}")

    def test_no_duplicates(self):
        text = (ROOT / "runtime" / "kernel" / "nids.h").read_text()
        nids = [nid for _, nid in DEFINE.findall(text)]
        self.assertEqual(len(nids), len(set(nids)))


if __name__ == "__main__":
    unittest.main()
```

In `tests/runtime/test_boot.c`:
- add `#include "kernel/sched.h"`, `#include "kernel/thread.h"` and `#include "khbbs/host_posix.h"`;
- replace `two_wild` with a version that records `$v0`/`$v1` after the first stepped-over dispatch (after a run, `psp_cpu` holds whatever the last thread to run left, so the check moves inside the guest):

```c
static uint32_t stepped_v0 = 1, stepped_v1 = 1;
static void two_wild(void) {
    psp_cpu.r[PSP_REG_V0] = 0x1234u;
    psp_cpu.r[PSP_REG_V1] = 0x5678u;
    psp_dispatch(0x0BAD0000u);
    stepped_v0 = psp_cpu.r[PSP_REG_V0];
    stepped_v1 = psp_cpu.r[PSP_REG_V1];
    psp_dispatch(0x0BAD0004u);
}
```

- add these guest functions after `look_at_vfpu`:

```c
/* A stop raised on a thread other than root. */
static void child_wild(void) { psp_dispatch(0x0BAD0100u); }
static void child_break(void) { psp_break(0x08804C00u, 0); }
static void child_spin(void) { for (;;) psp_dispatch(0x08804300u); }
static void spawn(uint32_t entry) {
    khbbs_klock();
    int32_t err;
    khbbs_thread *t = khbbs_thread_create("child", entry, 0x10, 0x1000, 0, psp_cpu.r[PSP_REG_GP], &err);
    if (t && khbbs_thread_start(t, 0, 0) == 0)
        khbbs_sched_preempt();             /* the child is higher: it runs now and stops the run */
    khbbs_kunlock();
}
static void spawn_wild(void)  { spawn(0x08804B00u); }
static void spawn_break(void) { spawn(0x08804C00u); }
static void spawn_spin(void)  { spawn(0x08804D00u); }
static void waits_forever(void) {
    khbbs_klock();
    khbbs_wait w = { .type = KW_SLEEP, .deadline = KHBBS_NO_DEADLINE };
    khbbs_sched_wait(&w);
    khbbs_kunlock();
}
```

- at the start of `main`, before `psp_mem_init`:

```c
    khbbs_image none = { .entry = 0x08804100u };
    CHECK(khbbs_boot(&none, "x", NULL).why == KHBBS_BAD_ARGS, "no host interface: nothing runs");
    khbbs_host_set(khbbs_host_posix());
```

- add the registrations after the existing ones:

```c
    psp_register(0x08804B00u, child_wild);
    psp_register(0x08804C00u, child_break);
    psp_register(0x08804D00u, child_spin);
    psp_register(0x08804E00u, spawn_wild);
    psp_register(0x08804F00u, spawn_break);
    psp_register(0x08805000u, spawn_spin);
    psp_register(0x08805100u, waits_forever);
```

- replace `CHECK(seen_a1 == 0x08000100u, "a1 = argp");` and the next two lines with (the root layout of Task 6: 27 bytes of path round to 0x20 below `k0` `0x09FFFF00`):

```c
    CHECK(seen_a1 == 0x09FFFEE0u, "a1 = k0 - round_up(arglen, 16)");
    const char *g = psp_mem_ptr(0x09FFFEE0u, (uint32_t)strlen(path) + 1);
    CHECK(g && strcmp(g, path) == 0, "path in guest memory");
```

- replace `CHECK(psp_cpu.r[PSP_REG_V0] == 0 && psp_cpu.r[PSP_REG_V1] == 0, "a stepped-over call returns 0, 0");` with `CHECK(stepped_v0 == 0 && stepped_v1 == 0, "a stepped-over call returns 0, 0");`;
- replace `CHECK(r.why == KHBBS_RETURNED && psp_vfpu_prefix_pending(), "a run can end with a prefix pending");` with `CHECK(r.why == KHBBS_RETURNED, "leave_vfpu_state ran");`;
- before `char longpath[400];`, add:

```c
    /* Stops on a non-root thread end the run with their reason, and every
     * host thread is joined (no leak across boots in one process). */
    img.entry = 0x08804E00u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_DISPATCH_MISS && r.addr == 0x0BAD0100u, "a miss on a child thread stops the run");
    CHECK(khbbs_host_posix_live_threads() == 0, "after a child's miss: no host thread left");
    aborts_outside_boot("stopped at a child's miss");
    img.entry = 0x08804F00u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_GUEST_BREAK && r.addr == 0x08804C00u && r.code == 0, "a child's break stops the run");
    CHECK(khbbs_host_posix_live_threads() == 0, "after a child's break: no host thread left");
    img.entry = 0x08805000u;
    r = khbbs_boot(&img, path, &(khbbs_boot_opts){ .budget = 100 });
    CHECK(r.why == KHBBS_BUDGET, "the budget counts dispatches on every thread");
    CHECK(khbbs_host_posix_live_threads() == 0, "after the budget: no host thread left");
    img.entry = 0x08805100u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_DEADLOCK, "root waiting for nothing is a deadlock");
    CHECK(strcmp(khbbs_stop_name(r.why), "deadlock (no runnable thread, no timer)") == 0, "deadlock has a name");
    CHECK(khbbs_host_posix_live_threads() == 0, "after a deadlock: no host thread left");
    img.entry = 0x08804100u;
    r = khbbs_boot(&img, path, NULL);
    CHECK(r.why == KHBBS_RETURNED, "a clean boot after all that");
```

In `tests/runtime/CMakeLists.txt`, delete the `if(UNIX) ... test_boot ... endif()` block and add `khbbs_kernel_test(boot)` to the threaded tests (it forks, and it is UNIX-only already).

- [ ] **Step 2: Run them and watch them fail**

```bash
python3 -m unittest tests.tools.test_kernel_nids -v; cmake --build build/ci
```

Expected: the Python test errors (`nids.h` does not exist); `test_boot.c` fails to compile (`khbbs_host_posix` linkage, then `a1` and the stop checks fail once it links against the old `boot.c`).

- [ ] **Step 3: NIDs and helpers**

`runtime/kernel/nids.h` (every NID the kernel registers; `tests/tools/test_kernel_nids.py` checks each against SHA-1 of its name):

```c
/* Firmware NIDs registered by runtime/kernel. NID_<name> is SHA-1(name)[0:4]
 * little-endian (checked by tests/tools/test_kernel_nids.py); NIDX_ marks a
 * NID that is not SHA-1 based. */
#ifndef KHBBS_KERNEL_NIDS_H
#define KHBBS_KERNEL_NIDS_H

/* ThreadManForUser: threads */
#define NID_sceKernelCreateThread             0x446D8DE6u
#define NID_sceKernelStartThread              0xF475845Du
#define NID_sceKernelExitThread               0xAA73C935u
#define NID_sceKernelExitDeleteThread         0x809CE29Bu
#define NID_sceKernelTerminateThread          0x616403BAu
#define NID_sceKernelTerminateDeleteThread    0x383F7BCCu
#define NID_sceKernelDeleteThread             0x9FA03CD3u
#define NID_sceKernelSleepThread              0x9ACE131Eu
#define NID_sceKernelWakeupThread             0xD59EAD2Fu
#define NID_sceKernelCancelWakeupThread       0xFCCFAD26u
#define NID_sceKernelWaitThreadEnd            0x278C0DF5u
#define NID_sceKernelWaitThreadEndCB          0x840E8133u
#define NID_sceKernelGetThreadId              0x293B45B8u
#define NID_sceKernelReferThreadStatus        0x17C1684Eu
#define NID_sceKernelChangeThreadPriority     0x71BC9871u
#define NID_sceKernelGetThreadCurrentPriority 0x94AA61EEu
#define NID_sceKernelChangeCurrentThreadAttr  0xEA748E31u
#define NID_sceKernelGetThreadStackFreeSize   0x52089CA1u
#define NID_sceKernelSuspendThread            0x9944F31Fu
#define NID_sceKernelResumeThread             0x75156E8Fu
#define NID_sceKernelDelayThread              0xCEADEB47u
#define NID_sceKernelDelayThreadCB            0x68DA9E36u
/* callbacks */
#define NID_sceKernelCreateCallback           0xE81CAF8Fu
#define NID_sceKernelDeleteCallback           0xEDBA5844u
#define NID_sceKernelCheckCallback            0x349D6D6Cu
#define NID_sceKernelNotifyCallback           0xC11BA8C4u
#define NID_sceKernelRegisterExitCallback     0x4AC57943u
/* semaphores, event flags, message boxes */
#define NID_sceKernelCreateSema               0xD6DA4BA1u
#define NID_sceKernelDeleteSema               0x28B6489Cu
#define NID_sceKernelSignalSema               0x3F53E640u
#define NID_sceKernelWaitSema                 0x4E3A1105u
#define NID_sceKernelWaitSemaCB               0x6D212BACu
#define NID_sceKernelPollSema                 0x58B1F937u
#define NID_sceKernelReferSemaStatus          0xBC6FEBC5u
#define NID_sceKernelCreateEventFlag          0x55C20A00u
#define NID_sceKernelDeleteEventFlag          0xEF9E4C70u
#define NID_sceKernelSetEventFlag             0x1FB15A32u
#define NID_sceKernelClearEventFlag           0x812346E4u
#define NID_sceKernelWaitEventFlag            0x402FCF22u
#define NID_sceKernelWaitEventFlagCB          0x328C546Au
#define NID_sceKernelPollEventFlag            0x30FD48F0u
#define NID_sceKernelReferEventFlagStatus     0xA66B0120u
#define NID_sceKernelCreateMbx                0x8125221Du
#define NID_sceKernelDeleteMbx                0x86255ADAu
#define NID_sceKernelSendMbx                  0xE9B3061Eu
#define NID_sceKernelPollMbx                  0x0D81716Au
/* mutexes and lightweight mutexes */
#define NID_sceKernelCreateMutex              0xB7D098C6u
#define NID_sceKernelDeleteMutex              0xF8170FBEu
#define NID_sceKernelLockMutex                0xB011B11Fu
#define NID_sceKernelLockMutexCB              0x5BF4DD27u
#define NID_sceKernelTryLockMutex             0x0DDCD2C9u
#define NID_sceKernelUnlockMutex              0x6B30100Fu
#define NID_sceKernelCreateLwMutex            0x19CFF145u
#define NID_sceKernelDeleteLwMutex            0x60107536u
#define NID_sceKernelLockLwMutex              0xBEA46419u
#define NID_sceKernelLockLwMutexCB            0x1FC64E09u
#define NID_sceKernelTryLockLwMutex_600       0x37431849u
#define NID_sceKernelUnlockLwMutex            0x15B6446Bu
/* time, alarms, interrupts, vblank */
#define NID_sceKernelGetSystemTimeLow         0x369ED59Du
#define NID_sceKernelGetSystemTimeWide        0x82BC5777u
#define NID_sceKernelSetAlarm                 0x6652B8CAu
#define NID_sceKernelCancelAlarm              0x7E65B999u
#define NID_sceKernelLibcClock                0x91E4F6A7u
#define NID_sceKernelLibcTime                 0x27CC57F0u
#define NID_sceKernelLibcGettimeofday         0x71EC4271u
#define NID_sceKernelRegisterSubIntrHandler   0xCA04A2B9u
#define NID_sceKernelReleaseSubIntrHandler    0xD61E6961u
#define NID_sceKernelEnableSubIntr            0xFB8E22ECu
#define NID_sceKernelCpuSuspendIntr           0x092968F4u
#define NID_sceKernelCpuResumeIntr            0x5F10D406u
#define NID_sceDisplayWaitVblankStart         0x984C27E7u
#define NID_sceDisplayWaitVblankStartCB       0x46F186C3u
#define NID_sceDisplayWaitVblank              0x36CDFADEu
#define NID_sceDisplayGetVcount               0x9C6EAAD7u
/* boot-path firmware outside the kernel (pulled forward from P5) */
#define NID_sceUmdCheckMedium                 0x46EBB729u
#define NID_sceUmdActivate                    0xC6183D47u
#define NID_sceUmdGetDriveStat                0x6B4A146Cu
#define NID_sceUmdWaitDriveStat               0x8EF08FCEu
#define NID_sceUmdWaitDriveStatWithTimer      0x56202973u
#define NID_sceUmdWaitDriveStatCB             0x4A9E5E29u
#define NID_sceUmdCancelWaitDriveStat         0x6AF9B50Au
#define NID_scePowerRegisterCallback          0x04B7766Eu
#define NID_scePowerUnregisterCallback        0xDFA8BAF8u
#define NID_scePowerIsPowerOnline             0x87440F5Eu
#define NID_scePowerGetPllClockFrequencyInt   0x34F9C463u
#define NID_scePowerGetBatteryLifePercent     0x2085D15Du
#define NID_sceImposeGetLanguageMode          0x24FD7BCFu
#define NID_sceImposeSetLanguageMode          0x36AA6E91u
#define NID_sceKernelMemcpy                   0x1839852Au
#define NID_sceKernelMemset                   0xA089ECA4u
#define NID_sceKernelDcacheWritebackInvalidateRange 0x34B9FA9Eu
#define NID_sceKernelIcacheInvalidateRange    0xC2DF770Eu
#define NID_sceKernelExitGame                 0x05572A5Fu
#define NIDX_crt0_sdk_version                 0x35669D4Cu   /* SysMemUserForUser, 6.x NID, name unknown */
/* IoFileMgrForUser */
#define NID_sceIoOpen                         0x109F50BCu
#define NID_sceIoClose                        0x810C4BC3u
#define NID_sceIoRead                         0x6A638D83u
#define NID_sceIoWrite                        0x42EC03ACu
#define NID_sceIoLseek                        0x27EB27B8u
#define NID_sceIoReadAsync                    0xA0B5A7C2u
#define NID_sceIoWaitAsync                    0xE23EEC33u
#define NID_sceIoWaitAsyncCB                  0x35DBD746u
#define NID_sceIoPollAsync                    0x3251EA56u
#define NID_sceIoGetstat                      0xACE946E8u
#define NID_sceIoIoctl                        0x63632449u
#define NID_sceIoDevctl                       0x54F5FB11u

#endif
```

`runtime/kernel/khle.h`:

```c
/* Helpers for firmware handlers in runtime/kernel. A handler takes the
 * kernel lock first and releases it on every way out. */
#ifndef KHBBS_KERNEL_KHLE_H
#define KHBBS_KERNEL_KHLE_H

#include "errors.h"
#include "nids.h"
#include "sched.h"
#include "psprecomp/hle.h"

#define KENTER() khbbs_klock()
/* Return v to the guest. */
#define KRET(v) do { psp_ret((uint32_t)(v)); khbbs_kunlock(); return; } while (0)
/* Return v, then let a thread this call made ready and that outranks the
 * caller run first (the caller sees v when it resumes). */
#define KRET_PREEMPT(v) do { psp_ret((uint32_t)(v)); khbbs_sched_preempt(); khbbs_kunlock(); return; } while (0)
/* Register `fn` for NID_<name> under `lib`, with the name the NID test checks. */
#define KREG(lib, name, fn) psp_hle_register(NID_##name, lib, #name, fn)

/* A uid argument where 0 means the calling thread. */
static inline khbbs_thread *kthread_or_self(uint32_t uid) {
    return uid ? khbbs_thread_by_uid(uid) : khbbs_current();
}

#endif
```

- [ ] **Step 4: Kernel reset and registration**

`runtime/include/khbbs/kernel.h`:

```c
/* Our PSP kernel (runtime/kernel): threads, synchronisation, time, and the
 * boot path's firmware. Its handlers replace psprecomp's for the same NIDs. */
#ifndef KHBBS_KERNEL_H
#define KHBBS_KERNEL_H

/* Register every kernel handler. Call once at start-up, after psp_hle_init(). */
void khbbs_kernel_register(void);

#endif
```

`runtime/kernel/kernel.h`:

```c
/* Kernel reset, called by khbbs_boot. Internal. */
#ifndef KHBBS_KERNEL_KERNEL_H
#define KHBBS_KERNEL_KERNEL_H

#include "khbbs/boot.h"
#include "khbbs/loader.h"

/* Every family back to its boot state: scheduler (with idle0), clock, the
 * user partition, objects. Without the lock; -1 if no host interface. */
int khbbs_kernel_reset(const khbbs_image *img, khbbs_time_mode mode);

#endif
```

`runtime/kernel/kernel.c`:

```c
/* Reset and registration of every kernel family. Plan P4; each later task
 * adds its family's reset and register call here, in task order. */
#include "kernel.h"
#include "khbbs/kernel.h"
#include "khle.h"
#include "ktime.h"
#include "thread.h"

int khbbs_kernel_reset(const khbbs_image *img, khbbs_time_mode mode) {
    if (khbbs_sched_reset() != 0) return -1;
    khbbs_time_reset(mode);
    khbbs_klock();
    khbbs_mem_layout(img);
    khbbs_kunlock();
    return 0;
}

/* The game is finished: end the run (never returns, so never traced, as on PPSSPP). */
static void hle_ExitGame(void) { khbbs_sched_stop(KHBBS_EXIT_GAME, 0, 0); }

void khbbs_kernel_register(void) {
    KREG("LoadExecForUser", sceKernelExitGame, hle_ExitGame);
}
```

Add `kernel/kernel.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 5: Boot on `root`**

In `runtime/boot.c`: delete `BOOT_ARGP`, `BOOT_STACK_TOP`, `g_stop`, `g_res`, `g_running` and the `<setjmp.h>` include; include `"kernel/kernel.h"`, `"kernel/sched.h"`, `"kernel/thread.h"` and `"khbbs/host.h"`; add `static uint32_t g_misses;`; then replace `on_miss`'s last three statements, `psp_break`, `on_budget` and `khbbs_boot` with:

```c
static void on_miss(uint32_t addr) {
    if (seen_before(addr)) {             /* only reachable for a stepped-over target */
        step_over();
        return;
    }
    const uint32_t order = g_misses++;
    const int stepped = g_misses <= g_keep_going;
    khbbs_misslog_record(addr, order, stepped);
    if (stepped) {
        if (!khbbs_misslog_active())
            fprintf(stderr, "khbbs: stepped over dispatch miss at 0x%08X (ra 0x%08X)\n",
                    addr, psp_cpu.r[PSP_REG_RA]);
        step_over();
        return;                          /* psp_dispatch returns to its caller */
    }
    khbbs_sched_stop(KHBBS_DISPATCH_MISS, addr, 0);
}

/* The guest's own trap. Most game breaks are the compiler's integer
 * divide-by-zero check (code 7), which in a recompiled run more likely means
 * a wrong register value, i.e. a translation bug, than a bug in the game. */
void psp_break(uint32_t addr, uint32_t code) {
    fflush(stdout);
    fprintf(stderr, "khbbs: guest break at 0x%08X (code 0x%05X)\n", addr, code);
    if (!khbbs_sched_active()) {         /* no run to stop: behave like psp_unimplemented */
        psp_miss_context();
        khbbs_trace_flush();
        abort();
    }
    khbbs_sched_stop(KHBBS_GUEST_BREAK, addr, code);
}

static void on_budget(void) { khbbs_sched_stop(KHBBS_BUDGET, 0, 0); }

khbbs_result khbbs_boot(const khbbs_image *img, const char *path, const khbbs_boot_opts *opts) {
    const uint64_t budget = opts ? opts->budget : 0;
    khbbs_result bad = { .why = KHBBS_BAD_ARGS };
    if (!img || !path) {
        fprintf(stderr, "khbbs: boot called with NULL image or path\n");
        return bad;
    }
    const size_t n = strlen(path) + 1;
    if (n > KHBBS_BOOT_PATH_MAX + 1) {
        fprintf(stderr, "khbbs: boot path too long (%zu bytes, max %d)\n", n - 1, KHBBS_BOOT_PATH_MAX);
        return bad;
    }
    if (!khbbs_host_get()) {
        fprintf(stderr, "khbbs: no host interface (call khbbs_host_set first)\n");
        return bad;
    }

    psp_cpu_reset();
    psp_vfpu_reset();                    /* prefixes and the vrnd generator; the trap handler stays */
    if (khbbs_kernel_reset(img, opts ? opts->time : KHBBS_TIME_DET) != 0) return bad;
    khbbs_klock();
    int32_t err = 0;
    khbbs_thread *root = khbbs_thread_create("root", img->entry, KHBBS_ROOT_PRIO, KHBBS_ROOT_STACK,
                                             KHBBS_ROOT_ATTR, img->gp, &err);
    if (root) root->delete_on_exit = 1;  /* observed: PPSSPP deletes root when module_start returns */
    const int started = root && khbbs_thread_start_host(root, path, (uint32_t)n) == 0;
    khbbs_kunlock();
    if (!started) {
        fprintf(stderr, "khbbs: cannot create the root thread (0x%08X)\n", (uint32_t)err);
        return bad;
    }

    g_keep_going = opts ? opts->keep_going : 0;
    g_misses = 0;
    g_nseen = 0;
    psp_trace_reset();                   /* "last" must not name a function of an earlier run */
    psp_set_miss_handler(on_miss);
    /* psp_dispatch only counts while a budget is armed, so "unlimited" arms
     * UINT64_MAX (never reached) to keep result.calls meaningful. Always set:
     * this also clears a limit left by an earlier boot. */
    psp_dispatch_set_budget(budget ? budget : UINT64_MAX, on_budget);

    khbbs_result r = khbbs_sched_run();  /* every guest thread has ended when it returns */
    r.calls = psp_dispatch_calls();
    r.misses = g_misses;
    /* psprecomp has no getter, so the previous miss handler cannot be
     * restored; NULL restores its default (report and abort). */
    psp_set_miss_handler(NULL);
    g_keep_going = 0;
    psp_dispatch_set_budget(0, NULL);
    khbbs_trace_flush();
    return r;
}
```

In `runtime/include/khbbs/boot.h`, update the doc comment above `khbbs_boot`: "The module runs on a guest thread `root` (priority 0x20, 0x400-byte stack at the top of the user partition) as `module_start(argc, argp)` with `argp` = `path` copied below its `k0` block (`docs/superpowers/plans/2026-10-19-p4-kernel-scheduler.md`, Task 6). The run ends when every guest thread has exited (`KHBBS_RETURNED`), at a stop (miss past `keep_going`, budget, guest `break`, `sceKernelExitGame`) or at a deadlock; every guest host thread is joined before it returns. A host interface must be set (`khbbs_host_set`), else `KHBBS_BAD_ARGS`. `psp_break` outside a run aborts." Keep the paragraphs on `keep_going`, the budget and the miss handler.

- [ ] **Step 6: The PC host**

In `hosts/pc/main.c`: include `"khbbs/host_posix.h"` and `"khbbs/kernel.h"`; right after `psp_hle_init();` add

```c
    khbbs_kernel_register();             /* our kernel over psprecomp's table */
    khbbs_host_set(khbbs_host_posix());
```

Replace the exit-status part of the usage text (from `"exit status: 0 the entry point returned; ...` to `"2 bad usage; 1 other failure\n"`) with:

```c
            "exit status: 0 every guest thread exited or the game called sceKernelExitGame;\n"
            "3 the run stopped at its first dispatch miss or on budget; 4 it carried on past a\n"
            "miss (--keep-going) and then ended for any reason, so only its first miss is\n"
            "trustworthy; 5 the guest executed `break` (integer divide by zero, overflow or an\n"
            "assert) and no miss was stepped over before it; 6 deadlock (no runnable thread and\n"
            "no timer pending); 2 bad usage; 1 other failure\n");
```

and the last two `return` lines of `main` with:

```c
    if (r.why == KHBBS_GUEST_BREAK) return 5;
    if (r.why == KHBBS_DEADLOCK) return 6;
    return r.why == KHBBS_RETURNED || r.why == KHBBS_EXIT_GAME ? 0 : 3;
```

- [ ] **Step 7: Run the tests**

```bash
python3 -m unittest tests.tools.test_kernel_nids -v
cmake -S . -B build/ci -G Ninja -DKHBBS_WITH_GAME=OFF && cmake --build build/ci && ctest --test-dir build/ci --output-on-failure
cmake --build build/pc && ctest --test-dir build/pc --output-on-failure
```

Expected: `OK` (2 tests); `100% tests passed` in both trees, `test_boot: ok`.

- [ ] **Step 8: Smoke-run the real game**

```bash
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 200000 \
    > build/p4-t7.out 2> build/p4-t7.err; echo "exit $?"; tail -1 build/p4-t7.out
```

Expected: `khbbs: stopped: dispatch budget exhausted  (200000 dispatches)` and exit 3, as before this task: `module_start` now runs on `root`, but the game's `sceKernelCreateThread`/`StartThread` are still psprecomp's run-to-completion ones until Task 8 registers ours, so `DiscInfoBind` still spins.

- [ ] **Step 9: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/kernel.h runtime/kernel/kernel.c runtime/kernel/nids.h runtime/kernel/khle.h \
    runtime/include/khbbs/kernel.h runtime/boot.c runtime/include/khbbs/boot.h runtime/CMakeLists.txt \
    tests/runtime/test_boot.c tests/runtime/CMakeLists.txt tests/tools/test_kernel_nids.py hosts/pc/main.c
git commit -m "boot: module_start on thread root; stops end every guest thread; deadlock and ExitGame stops

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 8: ThreadManForUser thread functions

**Files:**
- Modify: `runtime/kernel/thread.c` (append the handlers), `runtime/kernel/sched.h`, `runtime/kernel/sched.c` (export `khbbs_sched_make_ready`), `runtime/kernel/kernel.c`
- Create: `tests/runtime/kcall.h`, `tests/runtime/test_threadman.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics** (pspsdk `pspthreadman.h` prototypes; "uid 0" = the calling thread only where the table says so):

| Function | Arguments | Returns | Blocks | Errors |
|---|---|---|---|---|
| `sceKernelCreateThread` | name, entry, initPriority, stackSize, attr, option (ignored) | new uid | no | `ILLEGAL_PRIORITY` (outside 0x08..0x77), `ILLEGAL_STACK_SIZE` (< 0x200), `ILLEGAL_ENTRY` (outside RAM), `NO_MEMORY`. Stack rounded up to 0x100, allocated high, filled `0xFF` unless attr has `NO_FILLSTACK` (0x00100000), uid in its first word. `$gp` = the caller's `$gp`. |
| `sceKernelStartThread` | uid, arglen, argp | 0 | no; preempts the caller if the new thread has a higher priority | `ILLEGAL_THID` (0 or idle0), `UNKNOWN_THID`, `NOT_DORMANT`, `ILLEGAL_ADDR` (bad argp), `ILLEGAL_SIZE` (arguments do not fit). Layout: Task 6. |
| `sceKernelExitThread` | status | does not return | — | `ILLEGAL_CONTEXT` in an interrupt handler. Thread DORMANT; `WaitThreadEnd` waiters get `status`. |
| `sceKernelExitDeleteThread` | status | does not return | — | as Exit; the thread is deleted (stack freed, uid unknown afterwards). |
| `sceKernelTerminateThread` | uid | 0 | no; may preempt (woken waiters) | `ILLEGAL_THID` (self, idle0), `UNKNOWN_THID`, `DORMANT`. Target DORMANT with exit status `THREAD_TERMINATED` (0x800201AC; UNVERIFIED), its waits cancelled; its `WaitThreadEnd` waiters get that status. |
| `sceKernelTerminateDeleteThread` | uid | 0 | as Terminate | as Terminate; then deleted. |
| `sceKernelDeleteThread` | uid | 0 | no | `ILLEGAL_THID` (self, idle0), `UNKNOWN_THID`, `NOT_DORMANT`. Frees the stack and the uid. |
| `sceKernelSleepThread` | — | 0 | yes, until `WakeupThread`; returns at once (consuming one) if wakeups are pending | `ILLEGAL_CONTEXT`. |
| `sceKernelWakeupThread` | uid | 0 | no; may preempt | `UNKNOWN_THID`, `DORMANT`. Wakes a sleeping target, else counts one wakeup. |
| `sceKernelCancelWakeupThread` | uid (0 = self) | the pending wakeup count, then set to 0 | no | `UNKNOWN_THID`. |
| `sceKernelWaitThreadEnd` | uid, SceUInt *timeout (µs, may be NULL) | the target's exit status | yes, until the target exits (at once if DORMANT); writes the time left to *timeout | `UNKNOWN_THID`, `ILLEGAL_THID` (self; UNVERIFIED), `WAIT_TIMEOUT`. |
| `sceKernelWaitThreadEndCB` | uid, timeout | as above | as above, delivering callbacks while it waits (not when it returns at once; UNVERIFIED) | as above. |
| `sceKernelGetThreadId` | — | the calling thread's uid | no | — |
| `sceKernelReferThreadStatus` | uid (0 = self), SceKernelThreadInfo *info | 0 | no | `UNKNOWN_THID`. Writes `min(info->size, 0x68)` bytes after the size word, which it leaves alone. Layout below. |
| `sceKernelChangeThreadPriority` | uid (0 = self), priority (0 = the caller's current; UNVERIFIED) | 0 | no; reschedules (either thread may now outrank the caller) | `UNKNOWN_THID`, `ILLEGAL_PRIORITY`. A READY target moves to the tail of its new priority. |
| `sceKernelGetThreadCurrentPriority` | — | the caller's priority | no | — |
| `sceKernelChangeCurrentThreadAttr` | clearBits, setBits | 0 | no | — (attr = (attr & ~clear) \| set) |
| `sceKernelGetThreadStackFreeSize` | uid (0 = self) | bytes from the stack's second word up that still hold `0xFF` (UNVERIFIED) | no | `UNKNOWN_THID` |
| `sceKernelSuspendThread` | uid | 0 | no | `ILLEGAL_THID` (self, idle0), `UNKNOWN_THID`, `DORMANT`, `SUSPEND` (already). READY → SUSPENDED, WAITING → WAITSUSPEND. |
| `sceKernelResumeThread` | uid | 0 | no; may preempt | `UNKNOWN_THID`, `NOT_SUSPEND`. SUSPENDED → READY (tail), WAITSUSPEND → WAITING. |

`SceKernelThreadInfo` (pspsdk, 0x68 bytes): `+0x00 size`, `+0x04 name[32]`, `+0x24 attr`, `+0x28 status` (RUNNING 1, READY 2, WAITING 4, SUSPEND 8, DORMANT 16; WAITSUSPEND = 4|8), `+0x2C entry`, `+0x30 stack` (the base, lowest address), `+0x34 stackSize`, `+0x38 gpReg`, `+0x3C initPriority`, `+0x40 currentPriority`, `+0x44 waitType` (the `KW_*` value below 0x100, else 0), `+0x48 waitId`, `+0x4C wakeupCount`, `+0x50 exitStatus`, `+0x54 runClocks` (8 bytes), `+0x5C intrPreemptCount`, `+0x60 threadPreemptCount`, `+0x64 releaseCount` (the last four 0; UNVERIFIED).

- [ ] **Step 1: The test helper**

`tests/runtime/kcall.h`:

```c
/* Firmware calls from synthetic guest code in runtime tests, and the boot
 * boilerplate. Header-only, one include per test. Guest scratch memory for
 * tests: 0x08100000..0x08200000 (kernel partition, unused). */
#ifndef KHBBS_TEST_KCALL_H
#define KHBBS_TEST_KCALL_H

#include "kernel/nids.h"
#include "khbbs/boot.h"
#include "khbbs/host_posix.h"
#include "khbbs/kernel.h"
#include "psprecomp/dispatch.h"
#include "psprecomp/hle.h"
#include "psprecomp/mem.h"

#include <stdarg.h>
#include <string.h>

/* Call NID with n arguments (a0-a3, then t0-t3; pass them as uint32_t);
 * returns $v0 as the guest sees it when the call returns. */
static inline uint32_t kcall(uint32_t nid, int n, ...) {
    va_list ap;
    va_start(ap, n);
    for (int i = 0; i < n && i < 8; i++) {
        const uint32_t v = va_arg(ap, unsigned);
        if (i < 4) psp_cpu.r[PSP_REG_A0 + i] = v;
        else       psp_cpu.r[PSP_REG_T0 + i - 4] = v;
    }
    va_end(ap);
    psp_hle_call(nid);
    return psp_cpu.r[PSP_REG_V0];
}

/* Writes the NUL-terminated s at guest address addr; returns addr. */
static inline uint32_t gstr(uint32_t addr, const char *s) {
    psp_mem_write_block(addr, s, (uint32_t)strlen(s) + 1);
    return addr;
}

static inline int ktest_init(void) {
    if (psp_mem_init() != 0) return -1;
    psp_hle_init();
    khbbs_kernel_register();
    khbbs_host_set(khbbs_host_posix());
    return 0;
}

/* Boots `entry` (a C function registered with psp_register) as root. */
static inline khbbs_result ktest_boot(uint32_t entry, const khbbs_boot_opts *opts) {
    khbbs_image img = { .entry = entry, .gp = 0x08BD97E0u, .lo = 0x08804000u, .hi = 0x08900000u };
    return khbbs_boot(&img, "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN", opts);
}

#endif
```

- [ ] **Step 2: Write the failing test**

`tests/runtime/test_threadman.c`:

```c
/* ThreadManForUser thread functions from synthetic guest threads. */
#include "kcall.h"
#include "kernel/errors.h"
#include "kernel/sched.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static char order[32];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }
static void clear(void) { norder = 0; order[0] = 0; }

#define NAME 0x08100000u
static uint32_t create(const char *name, uint32_t entry, uint32_t prio) {
    return CALL(sceKernelCreateThread, 6, gstr(NAME, name), entry, prio, 0x1000u, 0u, 0u);
}

/* 1: a higher-priority thread runs inside StartThread; ExitThread's status
 * reaches WaitThreadEnd; a dormant thread starts again. */
static void child_exits(void) { mark('c'); CALL(sceKernelExitThread, 1, 0x1234u); mark('X'); }
static void t_start(void) {
    uint32_t uid = create("child", 0x08805000u, 0x10);
    CHECK(uid >= KHBBS_UID_BASE && uid < 0x80000000u, "1: CreateThread returns a uid");
    mark('r');
    CHECK(CALL(sceKernelStartThread, 3, uid, 0u, 0u) == 0, "1: StartThread");
    mark('R');
    CHECK(CALL(sceKernelWaitThreadEnd, 2, uid, 0u) == 0x1234u, "1: WaitThreadEnd on a dormant thread: its status");
    CHECK(CALL(sceKernelStartThread, 3, uid, 0u, 0u) == 0, "1: a dormant thread starts again");
    CHECK(CALL(sceKernelDeleteThread, 1, uid) == 0, "1: DeleteThread");
    CHECK(CALL(sceKernelDeleteThread, 1, uid) == KERR_UNKNOWN_THID, "1: deleted uid is unknown");
}

/* 2: arguments are copied below k0. */
static uint32_t g_a0, g_a1, g_k0;
static char g_args[8];
static void child_args(void) {
    g_a0 = psp_cpu.r[PSP_REG_A0];
    g_a1 = psp_cpu.r[PSP_REG_A1];
    g_k0 = psp_cpu.r[PSP_REG_K0];
    memcpy(g_args, psp_mem_ptr(g_a1, 5), 5);
}
static void t_args(void) {
    uint32_t uid = create("args", 0x08805100u, 0x10);
    CALL(sceKernelStartThread, 3, uid, 5u, gstr(0x08100100u, "abcd"));
    CHECK(g_a0 == 5 && g_a1 == g_k0 - 0x10 && memcmp(g_args, "abcd", 5) == 0, "2: 5 bytes at k0 - 0x10");
    CHECK(psp_read32(g_k0 + 0xC0) == uid, "2: k0 + 0xC0 is the uid");
}

/* 3: WaitThreadEnd blocks until the target exits; the waiter outranks it. */
static uint32_t g_target, g_waited;
static void waiter(void) {
    g_waited = CALL(sceKernelWaitThreadEnd, 2, g_target, 0u);
    psp_cpu.r[PSP_REG_V0] = 0;                    /* the waiter's own exit status: 0 */
}
static void target7(void) { mark('t'); psp_cpu.r[PSP_REG_V0] = 7; }    /* returns 7 from its entry */
static void t_wait_end(void) {
    g_target = create("target", 0x08805200u, 0x30);
    uint32_t w = create("waiter", 0x08805300u, 0x10);
    CALL(sceKernelStartThread, 3, g_target, 0u, 0u);   /* lower than root: not yet */
    CALL(sceKernelStartThread, 3, w, 0u, 0u);          /* higher: runs and blocks */
    mark('r');
    CHECK(CALL(sceKernelWaitThreadEnd, 2, w, 0u) == 0, "3: root waits for the waiter");
    CHECK(g_waited == 7 && strcmp(order, "rt") == 0, "3: the waiter got the target's return value");
}

/* 4: sleep and wakeup counts. */
static uint32_t g_sleeper;
static void sleeper(void) { mark('s'); CALL0(sceKernelSleepThread); mark('S'); }
static void t_sleep(void) {
    uint32_t me = CALL0(sceKernelGetThreadId);
    CHECK(CALL(sceKernelWakeupThread, 1, me) == 0, "4: wake myself (counts)");
    CHECK(CALL0(sceKernelSleepThread) == 0, "4: a pending wakeup: sleep returns at once");
    CALL(sceKernelWakeupThread, 1, me);
    CALL(sceKernelWakeupThread, 1, me);
    CHECK(CALL(sceKernelCancelWakeupThread, 1, 0u) == 2, "4: CancelWakeupThread returns the count");
    CHECK(CALL(sceKernelCancelWakeupThread, 1, 0u) == 0, "4: and clears it");
    g_sleeper = create("sleeper", 0x08805400u, 0x10);
    CALL(sceKernelStartThread, 3, g_sleeper, 0u, 0u);
    mark('r');
    CALL(sceKernelWakeupThread, 1, g_sleeper);          /* the sleeper outranks root: runs now */
    mark('R');
    CHECK(strcmp(order, "srSR") == 0, "4: WakeupThread preempts for a higher-priority sleeper");
}

/* 5: terminate. */
static void t_terminate(void) {
    uint32_t a = create("a", 0x08805400u, 0x10), b = create("b", 0x08805400u, 0x10);
    CALL(sceKernelStartThread, 3, a, 0u, 0u);
    CALL(sceKernelStartThread, 3, b, 0u, 0u);
    CHECK(CALL(sceKernelTerminateThread, 1, a) == 0, "5: terminate a sleeping thread");
    CHECK(CALL(sceKernelWaitThreadEnd, 2, a, 0u) == KERR_THREAD_TERMINATED, "5: its exit status");
    CHECK(CALL(sceKernelTerminateThread, 1, a) == KERR_DORMANT, "5: terminating it again");
    CHECK(CALL(sceKernelTerminateDeleteThread, 1, b) == 0, "5: terminate and delete");
    CHECK(CALL(sceKernelReferThreadStatus, 2, b, 0x08100200u) == KERR_UNKNOWN_THID, "5: b is gone");
    CHECK(CALL(sceKernelTerminateThread, 1, CALL0(sceKernelGetThreadId)) == KERR_ILLEGAL_THID, "5: not myself");
}

/* 6: ReferThreadStatus. */
static void t_refer(void) {
    uint32_t uid = create("refer", 0x08805400u, 0x10);
    const uint32_t info = 0x08100200u;
    psp_write32(info, 0x68);
    CHECK(CALL(sceKernelReferThreadStatus, 2, uid, info) == 0, "6: refer a dormant thread");
    CHECK(strcmp(psp_mem_ptr(info + 4, 6), "refer") == 0, "6: name");
    CHECK(psp_read32(info + 0x28) == 16 && psp_read32(info + 0x2C) == 0x08805400u, "6: dormant, entry");
    CHECK(psp_read32(info + 0x34) == 0x1000 && psp_read32(info + 0x3C) == 0x10 && psp_read32(info + 0x40) == 0x10,
          "6: stack size, priorities");
    CALL(sceKernelStartThread, 3, uid, 0u, 0u);          /* runs, sleeps */
    CALL(sceKernelReferThreadStatus, 2, uid, info);
    CHECK(psp_read32(info + 0x28) == 4 && psp_read32(info + 0x44) == 1, "6: waiting, wait type sleep");
    psp_write32(info, 0x08);                              /* a short size: only one word after it */
    psp_write32(info + 0x08, 0xCAFEF00Du);
    CALL(sceKernelReferThreadStatus, 2, 0u, info);
    CHECK(psp_read32(info) == 0x08 && psp_read32(info + 0x08) == 0xCAFEF00Du, "6: size honoured");
    CALL(sceKernelTerminateDeleteThread, 1, uid);
}

/* 7: errors. */
static void t_errors(void) {
    CHECK(CALL(sceKernelCreateThread, 6, gstr(NAME, "x"), 0x08805400u, 0x78u, 0x1000u, 0u, 0u) ==
          KERR_ILLEGAL_PRIORITY, "7: priority 0x78");
    CHECK(CALL(sceKernelStartThread, 3, 0x7FFFFFFFu, 0u, 0u) == KERR_UNKNOWN_THID, "7: start an unknown uid");
    CHECK(CALL(sceKernelStartThread, 3, 0u, 0u, 0u) == KERR_ILLEGAL_THID, "7: start uid 0");
    uint32_t me = CALL0(sceKernelGetThreadId);
    CHECK(CALL(sceKernelStartThread, 3, me, 0u, 0u) == KERR_NOT_DORMANT, "7: start a running thread");
    CHECK(CALL(sceKernelDeleteThread, 1, me) == KERR_ILLEGAL_THID, "7: delete myself");
}

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, child_exits);
    psp_register(0x08805100u, child_args);
    psp_register(0x08805200u, target7);
    psp_register(0x08805300u, waiter);
    psp_register(0x08805400u, sleeper);
    static const struct { uint32_t addr; void (*fn)(void); const char *name; } T[] = {
        { 0x08806000u, t_start, "start" }, { 0x08806100u, t_args, "args" },
        { 0x08806200u, t_wait_end, "wait_end" }, { 0x08806300u, t_sleep, "sleep" },
        { 0x08806400u, t_terminate, "terminate" }, { 0x08806500u, t_refer, "refer" },
        { 0x08806600u, t_errors, "errors" },
    };
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) psp_register(T[i].addr, T[i].fn);
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) {
        clear();
        khbbs_result r = ktest_boot(T[i].addr, NULL);
        if (r.why != KHBBS_RETURNED) { printf("FAIL %s: stopped: %s\n", T[i].name, khbbs_stop_name(r.why)); failures++; }
        if (i == 0) CHECK(strcmp(order, "rcRc") == 0, "1: child ran inside StartThread, twice");
        if (khbbs_host_posix_live_threads() != 0) { printf("FAIL %s: host threads left\n", T[i].name); failures++; }
    }
    printf("%s\n", failures ? "test_threadman: FAILED" : "test_threadman: ok");
    return failures ? 1 : 0;
}
```

`t_sleep`'s sleeper and `t_refer`'s thread are left sleeping when `root` returns in some cases: `t_sleep`'s sleeper finishes ('S'), `t_refer` deletes its thread. `t_terminate` leaves none. So every scenario ends with `KHBBS_RETURNED`.

Further cases to add to the same file, each as its own root function with the checks named here:
- `t_priority`: root (0x20) creates T (0x30, marks 't') and starts it; `ChangeThreadPriority(T, 0x10)` → `'t'` is marked before the call returns; `ChangeThreadPriority(0, 0x40)` with a second ready thread U (0x30) → U runs before the call returns; `GetThreadCurrentPriority()` → `0x40`; `ChangeThreadPriority(T, 0x07)` → `ILLEGAL_PRIORITY`.
- `t_exit_delete`: a child calls `ExitDeleteThread(5)`; root's `WaitThreadEnd(child)` (started before, child higher priority so it is already gone) → `UNKNOWN_THID`; a second child waited on while it runs (lower priority, root waits) → `5`, and `ReferThreadStatus` on it afterwards → `UNKNOWN_THID`.
- `t_suspend`: root creates T (0x30, entry `child_exits`) and starts it (READY, not run). `SuspendThread(T)` → 0, again → `SUSPEND`; `SuspendThread(self)` → `ILLEGAL_THID`; `ResumeThread(T)` → 0, again → `NOT_SUSPEND`; `WaitThreadEnd(T)` → `0x1234`. Then S (0x10, entry `sleeper`) started → it marks `s` and sleeps; `SuspendThread(S)` → 0 and `ReferThreadStatus(S)` shows status 12 (waiting + suspended); `WakeupThread(S)` → S becomes SUSPENDED (status 8) and has not marked `S`; `ResumeThread(S)` → `S` is marked before the call returns.
- `t_stack_free`: a child (0x10, 0x1000-byte stack) calls `GetThreadStackFreeSize(0)` → `0xEBC` exactly (a synthetic C entry never touches its guest stack, so the fill runs from the base + 4 up to the stub at `$sp` = `k0 - 0x40`: `0x1000 - 0x100 - 0x40 - 4`); `ChangeCurrentThreadAttr(0, 0x4000)` then `ReferThreadStatus(0)` shows `attr & 0x4000`.
- `t_return_deletes_root`: a child (lower priority) that root starts calls `ReferThreadStatus(root_uid)` after root returned → `UNKNOWN_THID` (root is deleted, as on PPSSPP). Root passes its uid through a global.

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(threadman)`.

- [ ] **Step 3: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R threadman --output-on-failure`
Expected: FAIL: psprecomp's `sceKernelStartThread` runs the child inline and the first scenario's order and the error checks do not hold.

- [ ] **Step 4: Export make-ready**

In `runtime/kernel/sched.h`, after `khbbs_sched_requeue`:

```c
/* t (WAITING, WAITSUSPEND or SUSPENDED by the caller's doing) becomes READY
 * at the tail of its priority; a WAITSUSPEND thread becomes SUSPENDED. */
void khbbs_sched_make_ready(khbbs_thread *t);
```

and in `sched.c`, after `make_ready`: `void khbbs_sched_make_ready(khbbs_thread *t) { make_ready(t); }`.

- [ ] **Step 5: The handlers**

Append to `runtime/kernel/thread.c` (and add `#include "khle.h"` at the top):

```c
/* ---- ThreadManForUser: threads (Task 8) --------------------------------- */

static void hle_CreateThread(void) {
    KENTER();
    char name[32];
    psp_str(psp_arg(0), name, sizeof name);
    int32_t err = 0;
    khbbs_thread *t = khbbs_thread_create(name, psp_arg(1), psp_arg(2), psp_arg(3), psp_arg(4),
                                          psp_cpu.r[PSP_REG_GP], &err);
    if (!t) KRET(err);
    KRET(t->uid);
}

static void hle_StartThread(void) {
    KENTER();
    const uint32_t uid = psp_arg(0);
    khbbs_thread *t = khbbs_thread_by_uid(uid);
    if (!uid || (t && t == khbbs_idle())) KRET(KERR_ILLEGAL_THID);
    if (!t) KRET(KERR_UNKNOWN_THID);
    const int32_t r = khbbs_thread_start(t, psp_arg(1), psp_arg(2));
    if (r) KRET(r);
    KRET_PREEMPT(0);
}

static void hle_ExitThread(void) {
    KENTER();
    if (khbbs_in_interrupt()) KRET(KERR_ILLEGAL_CONTEXT);
    khbbs_sched_exit((int32_t)psp_arg(0), 0);
}

static void hle_ExitDeleteThread(void) {
    KENTER();
    if (khbbs_in_interrupt()) KRET(KERR_ILLEGAL_CONTEXT);
    khbbs_sched_exit((int32_t)psp_arg(0), 1);
}

/* An existing thread other than the caller and idle0; *err otherwise. */
static khbbs_thread *other(uint32_t uid, uint32_t *err) {
    khbbs_thread *t = khbbs_thread_by_uid(uid);
    if (!t) { *err = KERR_UNKNOWN_THID; return NULL; }
    if (t == khbbs_current() || t == khbbs_idle()) { *err = KERR_ILLEGAL_THID; return NULL; }
    return t;
}

static void delete_thread(khbbs_thread *t) {
    if (t->stack_base) psp_sysmem_release(t->stack_base);
    khbbs_sched_free(t);
}

static void hle_DeleteThread(void) {
    KENTER();
    uint32_t err = 0;
    khbbs_thread *t = other(psp_arg(0), &err);
    if (!t) KRET(err);
    if (t->state != KTS_DORMANT) KRET(KERR_NOT_DORMANT);
    delete_thread(t);
    KRET(0);
}

static void terminate(int del) {
    KENTER();
    uint32_t err = 0;
    khbbs_thread *t = other(psp_arg(0), &err);
    if (!t) KRET(err);
    if (t->state == KTS_DORMANT) KRET(KERR_DORMANT);
    khbbs_sched_kill(t, (int32_t)KERR_THREAD_TERMINATED);   /* status UNVERIFIED */
    if (del) delete_thread(t);
    KRET_PREEMPT(0);
}
static void hle_TerminateThread(void)       { terminate(0); }
static void hle_TerminateDeleteThread(void) { terminate(1); }

static void hle_SleepThread(void) {
    KENTER();
    khbbs_thread *me = khbbs_current();
    if (me->wakeups) { me->wakeups--; KRET(0); }
    khbbs_wait w = { .type = KW_SLEEP, .deadline = KHBBS_NO_DEADLINE };
    KRET(khbbs_sched_wait(&w));
}

static void hle_WakeupThread(void) {
    KENTER();
    khbbs_thread *t = khbbs_thread_by_uid(psp_arg(0));
    if (!t) KRET(KERR_UNKNOWN_THID);
    if (t->state == KTS_DORMANT) KRET(KERR_DORMANT);
    if ((t->state == KTS_WAITING || t->state == KTS_WAITSUSPEND) && t->wait && t->wait->type == KW_SLEEP)
        khbbs_sched_wake(t, 0);
    else
        t->wakeups++;
    KRET_PREEMPT(0);
}

static void hle_CancelWakeupThread(void) {
    KENTER();
    khbbs_thread *t = kthread_or_self(psp_arg(0));
    if (!t) KRET(KERR_UNKNOWN_THID);
    const uint32_t n = t->wakeups;
    t->wakeups = 0;
    KRET(n);
}

static void wait_end(int cb) {
    KENTER();
    khbbs_thread *t = khbbs_thread_by_uid(psp_arg(0));
    const uint32_t tp = psp_arg(1);
    if (!t) KRET(KERR_UNKNOWN_THID);
    if (t == khbbs_current()) KRET(KERR_ILLEGAL_THID);
    if (t->state == KTS_DORMANT) KRET(t->exit_status);
    khbbs_wait w = { .type = KW_THREADEND, .id = t->uid, .cb = cb, .timeout_ptr = tp,
                     .deadline = khbbs_deadline(tp) };
    KRET(khbbs_sched_wait(&w));
}
static void hle_WaitThreadEnd(void)   { wait_end(0); }
static void hle_WaitThreadEndCB(void) { wait_end(1); }

static void hle_GetThreadId(void) { KENTER(); KRET(khbbs_current()->uid); }

static uint32_t status_bits(const khbbs_thread *t) {
    switch (t->state) {
    case KTS_RUNNING:     return 1;
    case KTS_READY:       return 2;
    case KTS_WAITING:     return 4;
    case KTS_SUSPENDED:   return 8;
    case KTS_WAITSUSPEND: return 4 | 8;
    default:              return 16;    /* DORMANT */
    }
}

static void put32(uint8_t *b, int off, uint32_t v) {
    b[off] = (uint8_t)v; b[off + 1] = (uint8_t)(v >> 8); b[off + 2] = (uint8_t)(v >> 16); b[off + 3] = (uint8_t)(v >> 24);
}

static void hle_ReferThreadStatus(void) {
    KENTER();
    khbbs_thread *t = kthread_or_self(psp_arg(0));
    const uint32_t info = psp_arg(1);
    if (!t) KRET(KERR_UNKNOWN_THID);
    uint8_t b[0x68];
    memset(b, 0, sizeof b);
    memcpy(b + 0x04, t->name, strlen(t->name));
    put32(b, 0x24, t->attr);
    put32(b, 0x28, status_bits(t));
    put32(b, 0x2C, t->entry);
    put32(b, 0x30, t->stack_base);
    put32(b, 0x34, t->stack_size);
    put32(b, 0x38, t->gp);
    put32(b, 0x3C, t->init_prio);
    put32(b, 0x40, t->prio);
    const int waiting = t->wait && (t->state == KTS_WAITING || t->state == KTS_WAITSUSPEND);
    put32(b, 0x44, waiting && t->wait->type < 0x100 ? (uint32_t)t->wait->type : 0);
    put32(b, 0x48, waiting ? t->wait->id : 0);
    put32(b, 0x4C, t->wakeups);
    put32(b, 0x50, (uint32_t)t->exit_status);
    const uint32_t size = psp_read32(info);
    const uint32_t n = size < sizeof b ? size : (uint32_t)sizeof b;
    if (n > 4) psp_mem_write_block(info + 4, b + 4, n - 4);
    KRET(0);
}

static void hle_ChangeThreadPriority(void) {
    KENTER();
    khbbs_thread *t = kthread_or_self(psp_arg(0));
    uint32_t prio = psp_arg(1);
    if (!t || t == khbbs_idle()) KRET(KERR_UNKNOWN_THID);
    if (prio == 0) prio = khbbs_current()->prio;          /* UNVERIFIED */
    if (prio < 0x08u || prio > 0x77u) KRET(KERR_ILLEGAL_PRIORITY);
    t->prio = prio;
    khbbs_sched_requeue(t);
    psp_ret(0);
    /* Either thread may now outrank the caller: preempt() compares the
     * caller with the best ready thread. */
    khbbs_sched_preempt();
    khbbs_kunlock();
}

static void hle_GetThreadCurrentPriority(void) { KENTER(); KRET(khbbs_current()->prio); }

static void hle_ChangeCurrentThreadAttr(void) {
    KENTER();
    khbbs_thread *me = khbbs_current();
    me->attr = (me->attr & ~psp_arg(0)) | psp_arg(1);
    KRET(0);
}

static void hle_GetThreadStackFreeSize(void) {
    KENTER();
    khbbs_thread *t = kthread_or_self(psp_arg(0));
    if (!t) KRET(KERR_UNKNOWN_THID);
    uint32_t n = 0;                                        /* UNVERIFIED: counts the 0xFF fill */
    while (n + 4 < t->stack_size && psp_read8(t->stack_base + 4 + n) == 0xFFu) n++;
    KRET(n);
}

static void hle_SuspendThread(void) {
    KENTER();
    uint32_t err = 0;
    khbbs_thread *t = other(psp_arg(0), &err);
    if (!t) KRET(err);
    switch (t->state) {
    case KTS_DORMANT:     KRET(KERR_DORMANT);
    case KTS_SUSPENDED:
    case KTS_WAITSUSPEND: KRET(KERR_SUSPEND);
    case KTS_WAITING:     t->state = KTS_WAITSUSPEND; break;
    default:              t->state = KTS_SUSPENDED; break;
    }
    KRET(0);
}

static void hle_ResumeThread(void) {
    KENTER();
    khbbs_thread *t = khbbs_thread_by_uid(psp_arg(0));
    if (!t) KRET(KERR_UNKNOWN_THID);
    if (t->state == KTS_WAITSUSPEND) { t->state = KTS_WAITING; KRET(0); }
    if (t->state != KTS_SUSPENDED) KRET(KERR_NOT_SUSPEND);
    khbbs_sched_make_ready(t);
    KRET_PREEMPT(0);
}

void khbbs_thread_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelCreateThread, hle_CreateThread);
    KREG(L, sceKernelStartThread, hle_StartThread);
    KREG(L, sceKernelExitThread, hle_ExitThread);
    KREG(L, sceKernelExitDeleteThread, hle_ExitDeleteThread);
    KREG(L, sceKernelTerminateThread, hle_TerminateThread);
    KREG(L, sceKernelTerminateDeleteThread, hle_TerminateDeleteThread);
    KREG(L, sceKernelDeleteThread, hle_DeleteThread);
    KREG(L, sceKernelSleepThread, hle_SleepThread);
    KREG(L, sceKernelWakeupThread, hle_WakeupThread);
    KREG(L, sceKernelCancelWakeupThread, hle_CancelWakeupThread);
    KREG(L, sceKernelWaitThreadEnd, hle_WaitThreadEnd);
    KREG(L, sceKernelWaitThreadEndCB, hle_WaitThreadEndCB);
    KREG(L, sceKernelGetThreadId, hle_GetThreadId);
    KREG(L, sceKernelReferThreadStatus, hle_ReferThreadStatus);
    KREG(L, sceKernelChangeThreadPriority, hle_ChangeThreadPriority);
    KREG(L, sceKernelGetThreadCurrentPriority, hle_GetThreadCurrentPriority);
    KREG(L, sceKernelChangeCurrentThreadAttr, hle_ChangeCurrentThreadAttr);
    KREG(L, sceKernelGetThreadStackFreeSize, hle_GetThreadStackFreeSize);
    KREG(L, sceKernelSuspendThread, hle_SuspendThread);
    KREG(L, sceKernelResumeThread, hle_ResumeThread);
}
```

`ResumeThread` of a `WAITSUSPEND` thread whose wait already ended is handled by `make_ready`: `wake_wait` turns `WAITSUSPEND` into `SUSPENDED`, which `ResumeThread` then makes `READY`.

In `runtime/kernel/kernel.c`, `khbbs_kernel_register`: add `khbbs_thread_register();` as its first line.

- [ ] **Step 6: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_threadman: ok`.

- [ ] **Step 7: The real game: `user_main` has its own `$k0`**

```bash
cmake --build build/pc
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 200000 \
    --trace build/p4-t8.jsonl > build/p4-t8.out 2> build/p4-t8.err; echo "exit $?"; tail -1 build/p4-t8.out
grep -c "first bad access: write32 at 0x00000004" build/p4-t8.err
grep -c '"name":"sceKernelStartThread"' build/p4-t8.jsonl
```

Expected: `khbbs: stopped: dispatch budget exhausted`; `0` bad writes at `0x4` (the P3 fault is gone: `user_main`'s `$k0` is `0x09FFFB00`); `1` `sceKernelStartThread` record, `user_main`'s for `DiscInfoBind`. Root's stays open: `user_main` blocks in `sceKernelWaitThreadEndCB` (its `sceKernelDelayThreadCB` is still psprecomp's no-op), and `DiscInfoBind` (priority 0x14, above root's 0x20) spins without ever blocking until Tasks 10 and 15.

- [ ] **Step 8: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/thread.c runtime/kernel/sched.h runtime/kernel/sched.c runtime/kernel/kernel.c \
    tests/runtime/kcall.h tests/runtime/test_threadman.c tests/runtime/CMakeLists.txt
git commit -m "kernel: ThreadManForUser thread functions on the scheduler

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Callbacks

**Files:**
- Create: `runtime/kernel/callback.h`, `runtime/kernel/callback.c`
- Modify: `runtime/kernel/kernel.c`, `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_callback.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics.** A callback belongs to the thread that created it. `sceKernelNotifyCallback` (and the kernel's own notifications: the power callback, Task 15) adds one to its notify count and stores the argument (the latest wins). Pending callbacks run only on their owner thread: when it enters or sits in a `*CB` wait, and in `sceKernelCheckCallback`; in creation order; as `func(count, notifyArg, common)` on the owner's stack at its current `$sp` (UNVERIFIED), with its context saved around the call. Count and argument reset before the call. A non-zero return deletes the callback. After callbacks the wait goes on (its deadline unchanged); if what it waits for became available meanwhile, `retry` takes it.

| Function | Arguments | Returns | Blocks | Errors |
|---|---|---|---|---|
| `sceKernelCreateCallback` | name, func, common | uid | no | `NO_MEMORY` (64 callbacks) |
| `sceKernelDeleteCallback` | uid | 0 | no | `UNKNOWN_CBID` |
| `sceKernelNotifyCallback` | uid, arg | 0 | no; the owner, if in a `*CB` wait, is made ready and may preempt | `UNKNOWN_CBID` |
| `sceKernelCheckCallback` | — | 1 if any callback ran, else 0 (UNVERIFIED) | no (runs them now) | `ILLEGAL_CONTEXT` in an interrupt handler |
| `sceKernelRegisterExitCallback` (LoadExecForUser) | uid | 0 | no | `UNKNOWN_CBID`. Stored; nothing notifies it before P8's exit path. |

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_callback.c`:

```c
/* Callbacks: notify, CheckCallback, delivery inside *CB waits only, deletion
 * by a non-zero return, the exit callback. */
#include "kcall.h"
#include "kernel/errors.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static int ran;
static uint32_t seen[3], seen_thread, cb_ret;
static void cb(void) {
    ran++;
    seen[0] = psp_cpu.r[PSP_REG_A0]; seen[1] = psp_cpu.r[PSP_REG_A1]; seen[2] = psp_cpu.r[PSP_REG_A2];
    seen_thread = psp_read32(psp_cpu.r[PSP_REG_K0] + 0xC0);
    psp_cpu.r[PSP_REG_V0] = cb_ret;
}

static uint32_t g_root, g_cb;
static void notifier(void) { CALL(sceKernelNotifyCallback, 2, g_cb, 0x77u); }

static void t_callbacks(void) {
    g_root = CALL0(sceKernelGetThreadId);
    g_cb = CALL(sceKernelCreateCallback, 3, gstr(0x08100000u, "cb"), 0x08805000u, 0xC0FFEEu);
    CHECK(g_cb >= 0x00040000u && g_cb < 0x80000000u, "CreateCallback returns a uid");

    CHECK(CALL0(sceKernelCheckCallback) == 0 && ran == 0, "nothing pending: CheckCallback runs nothing");
    CALL(sceKernelNotifyCallback, 2, g_cb, 0x11u);
    CALL(sceKernelNotifyCallback, 2, g_cb, 0x22u);
    CHECK(ran == 0, "a notify alone runs nothing");
    CHECK(CALL0(sceKernelCheckCallback) == 1 && ran == 1, "CheckCallback runs it once");
    CHECK(seen[0] == 2 && seen[1] == 0x22u && seen[2] == 0xC0FFEEu, "func(count, latest arg, common)");
    CHECK(seen_thread == g_root, "it runs on the owner thread (k0 names it)");

    /* Delivery inside a *CB wait: a lower thread notifies, root runs it, then waits on. */
    uint32_t n = CALL(sceKernelCreateThread, 6, gstr(0x08100000u, "notifier"), 0x08805100u, 0x30u, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, n, 0u, 0u);
    ran = 0;
    CHECK(CALL(sceKernelWaitThreadEndCB, 2, n, 0u) == 0, "WaitThreadEndCB returns the notifier's status");
    CHECK(ran == 1 && seen[0] == 1 && seen[1] == 0x77u, "the callback ran inside the wait");

    /* A plain wait does not deliver. */
    n = CALL(sceKernelCreateThread, 6, gstr(0x08100000u, "notifier"), 0x08805100u, 0x30u, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, n, 0u, 0u);
    ran = 0;
    CALL(sceKernelWaitThreadEnd, 2, n, 0u);
    CHECK(ran == 0, "WaitThreadEnd (no CB) leaves it pending");
    CHECK(CALL0(sceKernelCheckCallback) == 1 && ran == 1, "until CheckCallback");

    /* A non-zero return deletes it. */
    cb_ret = 1;
    CALL(sceKernelNotifyCallback, 2, g_cb, 0u);
    CALL0(sceKernelCheckCallback);
    CHECK(CALL(sceKernelNotifyCallback, 2, g_cb, 0u) == KERR_UNKNOWN_CBID, "deleted by its return value");
    cb_ret = 0;

    uint32_t c2 = CALL(sceKernelCreateCallback, 3, gstr(0x08100000u, "exit"), 0x08805000u, 0u);
    CHECK(kcall(NID_sceKernelRegisterExitCallback, 1, c2) == 0, "RegisterExitCallback");
    CHECK(kcall(NID_sceKernelRegisterExitCallback, 1, 0x7FFFFFFFu) == KERR_UNKNOWN_CBID, "unknown exit callback");
    CHECK(CALL(sceKernelDeleteCallback, 1, c2) == 0, "DeleteCallback");
    CHECK(CALL(sceKernelDeleteCallback, 1, c2) == KERR_UNKNOWN_CBID, "DeleteCallback twice");
}

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, cb);
    psp_register(0x08805100u, notifier);
    psp_register(0x08806000u, t_callbacks);
    khbbs_result r = ktest_boot(0x08806000u, NULL);
    CHECK(r.why == KHBBS_RETURNED, "returned");
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    printf("%s\n", failures ? "test_callback: FAILED" : "test_callback: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(callback)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R callback --output-on-failure`
Expected: FAIL (psprecomp's `sceKernelCreateCallback` never fires; `sceKernelNotifyCallback` and `sceKernelCheckCallback` are unimplemented).

- [ ] **Step 3: Implement**

`runtime/kernel/callback.h`:

```c
/* Callbacks. Internal; lock held. */
#ifndef KHBBS_KERNEL_CALLBACK_H
#define KHBBS_KERNEL_CALLBACK_H

#include <stdint.h>

void khbbs_cb_reset(void);              /* drops every callback, installs the scheduler hook */
void khbbs_cb_register(void);
/* Notify callback uid with arg (the kernel's own notifications). 0 or
 * KERR_UNKNOWN_CBID. The caller preempts afterwards if it can. */
uint32_t khbbs_cb_notify(uint32_t uid, uint32_t arg);

#endif
```

`runtime/kernel/callback.c`:

```c
/* Callbacks. Plan P4 Task 9. */
#include "callback.h"
#include "khle.h"

#include <string.h>

#define MAX_CBS 64

typedef struct {
    int      used;
    uint32_t uid, owner, func, common;
    uint32_t count, arg;     /* pending notifications and the latest argument */
    uint64_t seq;            /* creation order */
    char     name[32];
} kcb;

static kcb g_cb[MAX_CBS];
static uint64_t g_cb_seq;
static uint32_t g_exit_cb;

static kcb *find(uint32_t uid) {
    for (int i = 0; uid && i < MAX_CBS; i++)
        if (g_cb[i].used && g_cb[i].uid == uid) return &g_cb[i];
    return NULL;
}

/* The scheduler's hook: run t's pending callbacks, oldest first. */
static int deliver(khbbs_thread *t) {
    int ran = 0;
    for (;;) {
        kcb *c = NULL;
        for (int i = 0; i < MAX_CBS; i++)
            if (g_cb[i].used && g_cb[i].owner == t->uid && g_cb[i].count && (!c || g_cb[i].seq < c->seq))
                c = &g_cb[i];
        if (!c) return ran;
        const uint32_t uid = c->uid, count = c->count, arg = c->arg;
        c->count = 0;
        c->arg = 0;
        const uint32_t v0 = khbbs_call_guest(c->func, count, arg, c->common, psp_cpu.r[PSP_REG_SP], 0);
        ran++;
        kcb *after = find(uid);              /* it may have deleted itself */
        if (after && v0 != 0) after->used = 0;
    }
}

uint32_t khbbs_cb_notify(uint32_t uid, uint32_t arg) {
    kcb *c = find(uid);
    if (!c) return KERR_UNKNOWN_CBID;
    c->count++;
    c->arg = arg;
    khbbs_thread *owner = khbbs_thread_by_uid(c->owner);
    if (owner) khbbs_sched_kick(owner);
    return 0;
}

void khbbs_cb_reset(void) {
    memset(g_cb, 0, sizeof g_cb);
    g_cb_seq = 0;
    g_exit_cb = 0;
    khbbs_sched_set_cb_hook(deliver);
}

static void hle_CreateCallback(void) {
    KENTER();
    for (int i = 0; i < MAX_CBS; i++) {
        kcb *c = &g_cb[i];
        if (c->used) continue;
        memset(c, 0, sizeof *c);
        c->used = 1;
        c->uid = khbbs_uid_new();
        c->owner = khbbs_current()->uid;
        psp_str(psp_arg(0), c->name, sizeof c->name);
        c->func = psp_arg(1);
        c->common = psp_arg(2);
        c->seq = ++g_cb_seq;
        KRET(c->uid);
    }
    KRET(KERR_NO_MEMORY);
}

static void hle_DeleteCallback(void) {
    KENTER();
    kcb *c = find(psp_arg(0));
    if (!c) KRET(KERR_UNKNOWN_CBID);
    c->used = 0;
    KRET(0);
}

static void hle_NotifyCallback(void) {
    KENTER();
    KRET_PREEMPT(khbbs_cb_notify(psp_arg(0), psp_arg(1)));
}

static void hle_CheckCallback(void) {
    KENTER();
    if (khbbs_in_interrupt()) KRET(KERR_ILLEGAL_CONTEXT);
    KRET(deliver(khbbs_current()) > 0 ? 1 : 0);
}

static void hle_RegisterExitCallback(void) {
    KENTER();
    if (!find(psp_arg(0))) KRET(KERR_UNKNOWN_CBID);
    g_exit_cb = psp_arg(0);
    KRET(0);
}

void khbbs_cb_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelCreateCallback, hle_CreateCallback);
    KREG(L, sceKernelDeleteCallback, hle_DeleteCallback);
    KREG(L, sceKernelNotifyCallback, hle_NotifyCallback);
    KREG(L, sceKernelCheckCallback, hle_CheckCallback);
    KREG("LoadExecForUser", sceKernelRegisterExitCallback, hle_RegisterExitCallback);
}
```

In `runtime/kernel/kernel.c`: include `"callback.h"`; in `khbbs_kernel_reset`, after `khbbs_mem_layout(img);` add `khbbs_cb_reset();`; in `khbbs_kernel_register` add `khbbs_cb_register();`. Add `kernel/callback.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_callback: ok`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/callback.h runtime/kernel/callback.c runtime/kernel/kernel.c runtime/CMakeLists.txt \
    tests/runtime/test_callback.c tests/runtime/CMakeLists.txt
git commit -m "kernel: callbacks, delivered in *CB waits and sceKernelCheckCallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Time: delays, system time, the back-edge service, real mode

**Files:**
- Modify: `runtime/kernel/ktime.h`, `runtime/kernel/ktime.c`, `runtime/kernel/kernel.c`, `runtime/boot.c`
- Modify: `cmake/gen.cmake`, `hosts/pc/main.c`
- Create: `tests/runtime/test_time.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics:**

| Function | Arguments | Returns | Blocks | Notes |
|---|---|---|---|---|
| `sceKernelDelayThread` | µs | 0 | yes, until now + µs (0 still yields to any ready thread) | `ILLEGAL_CONTEXT` in a handler |
| `sceKernelDelayThreadCB` | µs | 0 | as above, delivering callbacks; the deadline does not move | |
| `sceKernelGetSystemTimeLow` | — | low 32 bits of the guest clock (µs since boot) | no | |
| `sceKernelGetSystemTimeWide` | — | the 64-bit clock in `$v0:$v1` | no | |
| `sceKernelLibcClock` | — | the guest clock (µs, low 32 bits) | no | psprecomp used the host's `clock()` |
| `sceKernelLibcTime` | time_t *t (may be NULL) | seconds since 1970; also stored at *t | no | det: 1262304000 (2010-01-01T00:00:00Z) at boot + guest seconds; real: the host's `time()` at boot + guest seconds |
| `sceKernelLibcGettimeofday` | timeval *tv, timezone *tz (ignored) | 0; `tv = {sec, usec}` from the same wall clock | no | |

The back-edge handler runs every 64 back-edges on the thread running guest code: one period of deterministic time, then, unless interrupts are disabled or it is inside an interrupt handler, due timers fire and a higher-priority ready thread preempts.

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_time.c`:

```c
/* Delays on the virtual clock, preemption at a back-edge safe point, the
 * clock calls, and real mode. */
#include "kcall.h"
#include "kernel/errors.h"
#include "kernel/ktime.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static char order[16];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }

static uint64_t wide(void) {
    uint32_t lo = CALL0(sceKernelGetSystemTimeWide);
    return (uint64_t)psp_cpu.r[PSP_REG_V1] << 32 | lo;
}
static uint32_t spawn(uint32_t entry, uint32_t prio) {
    uint32_t t = CALL(sceKernelCreateThread, 6, gstr(0x08100000u, "t"), entry, prio, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, t, 0u, 0u);
    return t;
}

/* 1: two delays end in deadline order. */
static void d1000(void) { CALL(sceKernelDelayThread, 1, 1000u); mark('a'); }
static void d500(void)  { CALL(sceKernelDelayThread, 1, 500u); mark('b'); }
static uint64_t g_end;
static void t_delays(void) {
    uint32_t a = spawn(0x08805000u, 0x10), b = spawn(0x08805100u, 0x10);
    CALL(sceKernelWaitThreadEnd, 2, a, 0u);
    CALL(sceKernelWaitThreadEnd, 2, b, 0u);
    g_end = wide();
    CHECK(strcmp(order, "ba") == 0, "1: the shorter delay ends first");
    CHECK(g_end >= 1000 && g_end < 1100, "1: about 1000 µs passed");
}

/* 2: a busy-waiting thread is preempted at a back-edge when a delay ends. */
static volatile int g_flag;
static void flagger(void) { CALL(sceKernelDelayThread, 1, 500u); g_flag = 1; }
static void t_backedge(void) {
    g_flag = 0;
    spawn(0x08805200u, 0x10);                 /* higher than root; it delays at once */
    uint64_t spins = 0;
    while (!g_flag) { PSP_BACKEDGE_CHECK(0x08806100u); spins++; }
    CHECK(spins >= 490ull * 64 && spins < 510ull * 64, "2: preempted after ~500 µs of back-edges");
    CHECK(wide() >= 500, "2: the clock moved with the back-edges");
}

/* 3: the clock calls. */
static void t_clock(void) {
    uint32_t a = CALL0(sceKernelGetSystemTimeLow), b = CALL0(sceKernelGetSystemTimeLow);
    CHECK(b - a == 1, "3: each firmware call costs 1 µs in det mode");
    CHECK(CALL(sceKernelLibcTime, 1, 0x08100100u) == 1262304000u && psp_read32(0x08100100u) == 1262304000u,
          "3: det wall clock starts at 2010-01-01");
    CHECK(CALL(sceKernelLibcGettimeofday, 2, 0x08100200u, 0u) == 0 && psp_read32(0x08100200u) == 1262304000u &&
          psp_read32(0x08100204u) < 1000000u, "3: gettimeofday");
    CHECK(CALL0(sceKernelLibcClock) > a, "3: LibcClock is the guest clock");
}

/* 4: DelayThreadCB delivers a pending callback and keeps its deadline. */
static int cb_ran;
static void cb(void) { cb_ran++; psp_cpu.r[PSP_REG_V0] = 0; }
static void t_delay_cb(void) {
    uint32_t c = CALL(sceKernelCreateCallback, 3, gstr(0x08100000u, "cb"), 0x08805300u, 0u);
    CALL(sceKernelNotifyCallback, 2, c, 1u);
    uint64_t t0 = wide();
    CHECK(CALL(sceKernelDelayThreadCB, 1, 2000u) == 0 && cb_ran == 1, "4: the callback ran inside the delay");
    CHECK(wide() - t0 >= 2000, "4: and the delay still took its time");
}

/* 5: real mode sleeps on the host clock. */
static void t_real(void) { CALL(sceKernelDelayThread, 1, 3000u); }

/* 6: the guest-time limit ends a loop of delays, and a busy loop. */
static void t_delay_forever(void) { for (;;) CALL(sceKernelDelayThread, 1, 1000u); }
static void t_spin_forever(void)  { for (;;) PSP_BACKEDGE_CHECK(0x08806600u); }

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, d1000);
    psp_register(0x08805100u, d500);
    psp_register(0x08805200u, flagger);
    psp_register(0x08805300u, cb);
    psp_register(0x08806000u, t_delays);
    psp_register(0x08806100u, t_backedge);
    psp_register(0x08806200u, t_clock);
    psp_register(0x08806300u, t_delay_cb);
    psp_register(0x08806400u, t_real);
    psp_register(0x08806500u, t_delay_forever);
    psp_register(0x08806600u, t_spin_forever);

    CHECK(ktest_boot(0x08806000u, NULL).why == KHBBS_RETURNED, "1 returned");
    const uint64_t first = g_end;
    norder = 0;
    CHECK(ktest_boot(0x08806000u, NULL).why == KHBBS_RETURNED && g_end == first,
          "1 again: det mode gives the same clock");
    CHECK(ktest_boot(0x08806100u, NULL).why == KHBBS_RETURNED, "2 returned");
    CHECK(ktest_boot(0x08806200u, NULL).why == KHBBS_RETURNED, "3 returned");
    CHECK(ktest_boot(0x08806300u, NULL).why == KHBBS_RETURNED, "4 returned");
    const uint64_t t0 = khbbs_host_posix()->now_us();
    CHECK(ktest_boot(0x08806400u, &(khbbs_boot_opts){ .time = KHBBS_TIME_REAL }).why == KHBBS_RETURNED, "5 returned");
    CHECK(khbbs_host_posix()->now_us() - t0 >= 3000, "5: real mode waited 3 ms of host time");
    CHECK(ktest_boot(0x08806500u, &(khbbs_boot_opts){ .time_limit_us = 50000 }).why == KHBBS_TIME_LIMIT,
          "6: delays stop at the time limit");
    CHECK(ktest_boot(0x08806600u, &(khbbs_boot_opts){ .time_limit_us = 2000 }).why == KHBBS_TIME_LIMIT,
          "6: a busy loop stops at the time limit");
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    printf("%s\n", failures ? "test_time: FAILED" : "test_time: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(time)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R time --output-on-failure`
Expected: FAIL: psprecomp's `sceKernelDelayThread` returns at once ("1: the shorter delay ends first"), `t_backedge` would spin forever, so the test times out after 60 s (the `TIMEOUT` set in `khbbs_kernel_test`).

- [ ] **Step 3: Implement**

In `runtime/kernel/ktime.h`, before `#endif`:

```c
/* The back-edge handler (psp_set_backedge), installed by khbbs_boot for the
 * run. Called on the thread running guest code, without the lock. */
void khbbs_backedge(uint32_t addr);
void khbbs_time_register(void);
```

In `runtime/kernel/ktime.c`: include `"khle.h"`, `"psprecomp/dispatch.h"` and `<time.h>`; add `static uint64_t g_epoch_us;` and, at the end of `khbbs_time_reset`:

```c
    /* The wall clock at boot. Det: a fixed 2010-01-01T00:00:00Z, so a run
     * is reproducible; real: the host's. */
    g_epoch_us = mode == KHBBS_TIME_REAL ? (uint64_t)time(NULL) * 1000000u : 1262304000ull * 1000000u;
```

Then append:

```c
/* ---- the back-edge service -------------------------------------------------- */

void khbbs_backedge(uint32_t addr) {
    (void)addr;
    psp_backedge_countdown = KHBBS_BACKEDGE_PERIOD - 1;
    khbbs_klock();
    khbbs_time_backedge();
    if (khbbs_time_past_limit(khbbs_now())) khbbs_sched_stop_locked(KHBBS_TIME_LIMIT, 0, 0);
    if (khbbs_intr_enabled() && !khbbs_in_interrupt()) {
        khbbs_timer_fire_due();
        khbbs_sched_preempt();
    }
    khbbs_kunlock();
}

/* ---- ThreadManForUser and UtilsForUser: time ---------------------------------- */

static void delay(int cb) {
    KENTER();
    khbbs_wait w = { .type = KW_DELAY, .cb = cb, .deadline = khbbs_now() + psp_arg(0) };
    KRET(khbbs_sched_wait(&w));
}
static void hle_DelayThread(void)   { delay(0); }
static void hle_DelayThreadCB(void) { delay(1); }

static void hle_GetSystemTimeLow(void) { KENTER(); KRET((uint32_t)khbbs_now()); }

static void hle_GetSystemTimeWide(void) {
    KENTER();
    const uint64_t t = khbbs_now();
    psp_cpu.r[PSP_REG_V1] = (uint32_t)(t >> 32);
    KRET((uint32_t)t);
}

static uint64_t wall_us(void) { return g_epoch_us + khbbs_now(); }

static void hle_LibcClock(void) { KENTER(); KRET((uint32_t)khbbs_now()); }

static void hle_LibcTime(void) {
    KENTER();
    const uint32_t s = (uint32_t)(wall_us() / 1000000u);
    if (psp_arg(0)) psp_write32(psp_arg(0), s);
    KRET(s);
}

static void hle_LibcGettimeofday(void) {
    KENTER();
    const uint32_t tv = psp_arg(0);
    const uint64_t w = wall_us();
    if (tv) {
        psp_write32(tv, (uint32_t)(w / 1000000u));
        psp_write32(tv + 4, (uint32_t)(w % 1000000u));
    }
    KRET(0);
}

void khbbs_time_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelDelayThread, hle_DelayThread);
    KREG(L, sceKernelDelayThreadCB, hle_DelayThreadCB);
    KREG(L, sceKernelGetSystemTimeLow, hle_GetSystemTimeLow);
    KREG(L, sceKernelGetSystemTimeWide, hle_GetSystemTimeWide);
    KREG("UtilsForUser", sceKernelLibcClock, hle_LibcClock);
    KREG("UtilsForUser", sceKernelLibcTime, hle_LibcTime);
    KREG("UtilsForUser", sceKernelLibcGettimeofday, hle_LibcGettimeofday);
}
```

In `runtime/kernel/kernel.c`, `khbbs_kernel_register`: add `khbbs_time_register();`.

In `runtime/boot.c`: include `"kernel/ktime.h"`; right after `psp_dispatch_set_budget(budget ? budget : UINT64_MAX, on_budget);` add `psp_set_backedge(khbbs_backedge);` and `khbbs_time_set_limit(opts ? opts->time_limit_us : 0);`, and after `psp_dispatch_set_budget(0, NULL);` add `psp_set_backedge(NULL);`.

In `cmake/gen.cmake`, after the `target_compile_options(khbbs_recomp ...)` line:

```cmake
# Loop back-edges are scheduling safe points (psprecomp's PSPRECOMP_BACKEDGE;
# plan P4 Task 2): due timers fire and higher-priority threads preempt there.
target_compile_definitions(khbbs_recomp PRIVATE PSPRECOMP_BACKEDGE)
```

In `hosts/pc/main.c`: add `khbbs_time_mode tmode = KHBBS_TIME_DET;`, the option

```c
        else if (!strcmp(argv[i], "--time") && i + 1 < argc) {
            const char *m = argv[++i];
            if (!strcmp(m, "det")) tmode = KHBBS_TIME_DET;
            else if (!strcmp(m, "real")) tmode = KHBBS_TIME_REAL;
            else return usage();
        }
```

and the option

```c
        else if (!strcmp(argv[i], "--time-limit") && i + 1 < argc) {
            if (!khbbs_parse_count(argv[++i], &tlimit)) return usage();   /* guest seconds */
        }
```

with `uint64_t tlimit = 0;`; pass `.time = tmode, .time_limit_us = tlimit * 1000000u` in the `khbbs_boot_opts` literal; add `[--time det|real] [--time-limit S]` to the usage line plus `"--time: det (default) runs on a virtual clock, the same every run; real on the host clock\n"` and `"--time-limit S: stop after S seconds of guest time (a game waiting in delays makes no dispatches, so --budget does not end it)\n"`. A time-limit stop exits 3, like the budget.

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_time: ok`.

- [ ] **Step 5: The real game: `user_main`'s delay now ends**

```bash
cmake --build build/pc
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 200000 \
    --trace build/p4-t10.jsonl > build/p4-t10.out 2> build/p4-t10.err; echo "exit $?"; tail -1 build/p4-t10.out
grep -c '"name":"sceKernelDelayThreadCB","ra":"0x0880F6F8"' build/p4-t10.jsonl
grep -c '"name":"sceKernelStartThread"' build/p4-t10.jsonl
```

Expected: `khbbs: stopped: dispatch budget exhausted`; `1` (`user_main`'s `sceKernelDelayThreadCB(33334)`, called from `0x0880F6F4`, returned after really waiting); `2` (root's `sceKernelStartThread` now returns once `user_main` and `DiscInfoBind` block in their delays). `user_main` then waits in `sceKernelWaitThreadEndCB` for `DiscInfoBind`, which still loops (166,670 µs per round) until Task 15 answers `sceUmdGetDriveStat`.

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/ktime.h runtime/kernel/ktime.c runtime/kernel/kernel.c runtime/boot.c cmake/gen.cmake \
    hosts/pc/main.c tests/runtime/test_time.c tests/runtime/CMakeLists.txt
git commit -m "kernel: delays, system time and libc time on the guest clock; back-edge safe points; --time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: Vblank, the vblank sub-interrupt, alarms, interrupt masking

**Files:**
- Create: `runtime/kernel/intr.c`, `runtime/kernel/intr.h`
- Modify: `runtime/kernel/kernel.c`, `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_intr.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics.** Vblanks happen at every multiple of 16,683 µs of guest time (59.94 Hz; the first at 16,683). A vblank timer is armed only while someone needs it (a thread waiting for vblank, or an enabled sub-interrupt handler), so an idle game with none does not keep the clock busy. At a vblank: every vblank waiter wakes, then every registered and enabled sub-interrupt handler of `PSP_VBLANK_INT` (30) runs, in sub-interrupt number order, as `handler(subno, arg)` on the current thread (`idle0` if nothing else is runnable) with interrupts disabled, on the interrupt stack (`0x083FE000`–`0x083FF000`, `$sp` = `0x083FEFC0`). Alarm handlers run the same way as `next = handler(common)`; `next` > 0 re-arms the alarm `next` µs after the time it was due (UNVERIFIED: after when it was due, not after it ran), 0 ends it. Firmware calls that would block return `ILLEGAL_CONTEXT` inside a handler. Interrupts are delivered at back-edge safe points and from `idle0`, never while the running thread has them disabled.

| Function | Arguments | Returns | Blocks | Errors |
|---|---|---|---|---|
| `sceDisplayWaitVblankStart` | — | 0 | until the next vblank | `ILLEGAL_CONTEXT` |
| `sceDisplayWaitVblankStartCB` | — | 0 | as above, delivering callbacks | |
| `sceDisplayWaitVblank` | — | 0 | as `WaitVblankStart` (no vblank interval modelled; UNVERIFIED) | |
| `sceDisplayGetVcount` | — | vblanks since boot (guest time / 16,683) | no | |
| `sceKernelRegisterSubIntrHandler` | intno, subno, handler, arg | 0 | no | `ILLEGAL_INTRCODE` (intno ≠ 30 or subno ≥ 16), `FOUND_HANDLER` (already registered). Registered disabled. |
| `sceKernelEnableSubIntr` | intno, subno | 0 | no | `ILLEGAL_INTRCODE`, `NOTFOUND_HANDLER` |
| `sceKernelReleaseSubIntrHandler` | intno, subno | 0 | no | `ILLEGAL_INTRCODE`, `NOTFOUND_HANDLER` |
| `sceKernelSetAlarm` | clock (µs), handler, common | alarm uid | no | `NO_MEMORY` (32 alarms) |
| `sceKernelCancelAlarm` | uid | 0 | no | `UNKNOWN_ALMID` |
| `sceKernelCpuSuspendIntr` (Kernel_Library) | — | the previous state (1 enabled, 0 disabled) | no | per thread, saved with its context |
| `sceKernelCpuResumeIntr` (Kernel_Library) | state | (void) | no | |

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_intr.c`:

```c
/* Vblank waits and counts, the vblank sub-interrupt on idle0 and on a busy
 * thread, interrupt masking, alarms, and blocking inside a handler. */
#include "kcall.h"
#include "kernel/errors.h"
#include "kernel/sched.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static uint64_t wide(void) {
    uint32_t lo = CALL0(sceKernelGetSystemTimeWide);
    return (uint64_t)psp_cpu.r[PSP_REG_V1] << 32 | lo;
}

static int hits;
static uint32_t h_a0, h_a1, h_sp, h_err;
static char h_thread[48];
static void vbl_handler(void) {
    hits++;
    h_a0 = psp_cpu.r[PSP_REG_A0];
    h_a1 = psp_cpu.r[PSP_REG_A1];
    h_sp = psp_cpu.r[PSP_REG_SP];
    snprintf(h_thread, sizeof h_thread, "%s", khbbs_current_trace_name());
    h_err = CALL(sceKernelDelayThread, 1, 10u);           /* blocking inside a handler */
}

/* 1: vblank waits. */
static void t_vblank(void) {
    CHECK(CALL0(sceDisplayGetVcount) == 0, "1: vcount 0 at boot");
    CHECK(CALL0(sceDisplayWaitVblankStart) == 0, "1: wait");
    uint64_t t = wide();
    CHECK(t >= 16683 && t < 16683 + 10, "1: the first vblank is at 16683 µs");
    CALL0(sceDisplayWaitVblankStart);
    CHECK(CALL0(sceDisplayGetVcount) == 2, "1: two vblanks counted");
}

/* 2: a sub-interrupt handler runs on idle0 while root delays. */
static void t_subintr(void) {
    CHECK(CALL(sceKernelRegisterSubIntrHandler, 4, 30u, 0u, 0x08805000u, 0xABu) == 0, "2: register");
    CHECK(CALL(sceKernelRegisterSubIntrHandler, 4, 30u, 0u, 0x08805000u, 0xABu) == KERR_FOUND_HANDLER, "2: twice");
    CHECK(CALL(sceKernelRegisterSubIntrHandler, 4, 31u, 0u, 0x08805000u, 0u) == KERR_ILLEGAL_INTRCODE, "2: intno 31");
    CHECK(CALL(sceKernelEnableSubIntr, 2, 30u, 1u) == KERR_NOTFOUND_HANDLER, "2: enable an unregistered one");
    CALL(sceKernelDelayThread, 1, 20000u);
    CHECK(hits == 0, "2: registered but not enabled: never runs");
    CHECK(CALL(sceKernelEnableSubIntr, 2, 30u, 0u) == 0, "2: enable");
    CALL(sceKernelDelayThread, 1, 34000u);                 /* vblanks at 33366 and 50049 */
    CHECK(hits == 2, "2: two vblanks, two calls");
    CHECK(h_a0 == 0 && h_a1 == 0xABu, "2: handler(subno, arg)");
    CHECK(strcmp(h_thread, "idle0") == 0, "2: on idle0 when nothing else runs");
    CHECK(h_sp >= 0x083FE000u && h_sp < 0x083FF000u, "2: on the interrupt stack");
    CHECK(h_err == KERR_ILLEGAL_CONTEXT, "2: a blocking call in a handler fails");
    CHECK(CALL(sceKernelReleaseSubIntrHandler, 2, 30u, 0u) == 0, "2: release");
    CHECK(CALL(sceKernelReleaseSubIntrHandler, 2, 30u, 0u) == KERR_NOTFOUND_HANDLER, "2: release twice");
}

/* 3: on a busy thread, at a back-edge; masked while interrupts are off. */
static void t_busy(void) {
    hits = 0;
    CALL(sceKernelRegisterSubIntrHandler, 4, 30u, 2u, 0x08805000u, 0u);
    CALL(sceKernelEnableSubIntr, 2, 30u, 2u);
    const uint32_t flag = CALL0(sceKernelCpuSuspendIntr);
    CHECK(flag == 1, "3: interrupts were enabled");
    for (int i = 0; i < 20000 * 64; i++) PSP_BACKEDGE_CHECK(0x08806200u);   /* 20 ms of guest time */
    CHECK(hits == 0, "3: masked: no handler ran");
    psp_cpu.r[PSP_REG_S0] = 0x5A5A5A5Au;
    kcall(NID_sceKernelCpuResumeIntr, 1, flag);
    while (hits == 0) PSP_BACKEDGE_CHECK(0x08806200u);
    CHECK(strcmp(h_thread, "root") == 0, "3: the handler ran on the interrupted thread");
    CHECK(psp_cpu.r[PSP_REG_S0] == 0x5A5A5A5Au, "3: the thread's registers are back");
    CALL(sceKernelReleaseSubIntrHandler, 2, 30u, 2u);
}

/* 4: alarms. */
static int alarms;
static uint64_t alarm_t[4];
static void alarm_handler(void) {
    alarm_t[alarms++] = wide();                            /* a non-blocking call is fine in a handler */
    psp_cpu.r[PSP_REG_V0] = alarms < 3 ? 500u : 0u;        /* re-arm twice, then end */
}
static void t_alarm(void) {
    const uint64_t t0 = wide();
    uint32_t a = CALL(sceKernelSetAlarm, 3, 1000u, 0x08805100u, 0x1234u);
    CHECK(a >= 0x00040000u && a < 0x80000000u, "4: SetAlarm returns a uid");
    CALL(sceKernelDelayThread, 1, 3000u);
    CHECK(alarms == 3, "4: due at +1000, re-armed at +1500 and +2000, then done");
    CHECK(alarm_t[0] - t0 >= 1000 && alarm_t[0] - t0 < 1010, "4: the first call is on time");
    CHECK(alarm_t[1] - alarm_t[0] >= 495 && alarm_t[1] - alarm_t[0] <= 505, "4: re-armed 500 µs after it was due");
    CHECK(CALL(sceKernelCancelAlarm, 1, a) == KERR_UNKNOWN_ALMID, "4: a finished alarm is gone");
    uint32_t b = CALL(sceKernelSetAlarm, 3, 1000u, 0x08805100u, 0u);
    CHECK(CALL(sceKernelCancelAlarm, 1, b) == 0, "4: cancel before it fires");
    CALL(sceKernelDelayThread, 1, 2000u);
    CHECK(alarms == 3, "4: a cancelled alarm never runs");
}

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, vbl_handler);
    psp_register(0x08805100u, alarm_handler);
    psp_register(0x08806000u, t_vblank);
    psp_register(0x08806100u, t_subintr);
    psp_register(0x08806200u, t_busy);
    psp_register(0x08806300u, t_alarm);
    const uint32_t tests[] = { 0x08806000u, 0x08806100u, 0x08806200u, 0x08806300u };
    for (size_t i = 0; i < sizeof tests / sizeof tests[0]; i++) {
        khbbs_result r = ktest_boot(tests[i], NULL);
        if (r.why != KHBBS_RETURNED) { printf("FAIL test %zu: %s\n", i + 1, khbbs_stop_name(r.why)); failures++; }
    }
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    printf("%s\n", failures ? "test_intr: FAILED" : "test_intr: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(intr)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R intr --output-on-failure`
Expected: FAIL (`sceDisplayWaitVblankStart`, the sub-interrupt and alarm NIDs are unimplemented: "1: the first vblank is at 16683 µs" fails).

- [ ] **Step 3: Implement**

`runtime/kernel/intr.h`:

```c
/* Vblank, sub-interrupts, alarms, interrupt masking. Internal; lock held. */
#ifndef KHBBS_KERNEL_INTR_H
#define KHBBS_KERNEL_INTR_H

#define KHBBS_VBLANK_US 16683u          /* 59.94 Hz */

void khbbs_intr_reset(void);
void khbbs_intr_register(void);

#endif
```

`runtime/kernel/intr.c`:

```c
/* Vblank, the vblank sub-interrupt, alarms, interrupt masking. Plan P4 Task 11. */
#include "intr.h"
#include "khle.h"
#include "ktime.h"
#include "thread.h"

#include <string.h>

#define PSP_VBLANK_INT 30u
#define MAX_SUBINTR    16
#define MAX_ALARMS     32
#define INTR_SP        (KHBBS_INTR_STACK_TOP - 0x40u)

typedef struct { int registered, enabled; uint32_t handler, arg; } subintr;
typedef struct { int used; uint32_t uid, handler, common, timer; } kalarm;

static subintr g_vbl[MAX_SUBINTR];
static uint32_t g_vbl_timer;           /* the next vblank's timer, 0 if none */
static kalarm g_alarm[MAX_ALARMS];

static int handlers_on(void) {
    for (int i = 0; i < MAX_SUBINTR; i++)
        if (g_vbl[i].registered && g_vbl[i].enabled) return 1;
    return 0;
}

static void on_vblank(void *arg, uint64_t when);

static void arm_vblank(void) {
    if (g_vbl_timer) return;
    const uint64_t next = (khbbs_now() / KHBBS_VBLANK_US + 1) * KHBBS_VBLANK_US;
    g_vbl_timer = khbbs_timer_add(next, on_vblank, NULL);
}

static void on_vblank(void *arg, uint64_t when) {
    (void)arg;
    (void)when;
    g_vbl_timer = 0;
    khbbs_wake_all(KW_VBLANK, 0, 0);
    for (uint32_t i = 0; i < MAX_SUBINTR; i++)
        if (g_vbl[i].registered && g_vbl[i].enabled)
            khbbs_call_guest(g_vbl[i].handler, i, g_vbl[i].arg, 0, INTR_SP, 1);
    if (handlers_on()) arm_vblank();
}

void khbbs_intr_reset(void) {
    memset(g_vbl, 0, sizeof g_vbl);
    memset(g_alarm, 0, sizeof g_alarm);
    g_vbl_timer = 0;                    /* the timer queue was emptied by khbbs_time_reset */
}

/* ---- sceDisplay: vblank ----------------------------------------------------- */

static void wait_vblank(int cb) {
    KENTER();
    arm_vblank();
    khbbs_wait w = { .type = KW_VBLANK, .cb = cb, .deadline = KHBBS_NO_DEADLINE };
    KRET(khbbs_sched_wait(&w));
}
static void hle_WaitVblankStart(void)   { wait_vblank(0); }
static void hle_WaitVblankStartCB(void) { wait_vblank(1); }
static void hle_GetVcount(void) { KENTER(); KRET((uint32_t)(khbbs_now() / KHBBS_VBLANK_US)); }

/* ---- InterruptManager ----------------------------------------------------- */

static subintr *sub(uint32_t intno, uint32_t no) {
    return intno == PSP_VBLANK_INT && no < MAX_SUBINTR ? &g_vbl[no] : NULL;
}

static void hle_RegisterSubIntrHandler(void) {
    KENTER();
    subintr *s = sub(psp_arg(0), psp_arg(1));
    if (!s) KRET(KERR_ILLEGAL_INTRCODE);
    if (s->registered) KRET(KERR_FOUND_HANDLER);
    *s = (subintr){ 1, 0, psp_arg(2), psp_arg(3) };
    KRET(0);
}

static void hle_EnableSubIntr(void) {
    KENTER();
    subintr *s = sub(psp_arg(0), psp_arg(1));
    if (!s) KRET(KERR_ILLEGAL_INTRCODE);
    if (!s->registered) KRET(KERR_NOTFOUND_HANDLER);
    s->enabled = 1;
    arm_vblank();
    KRET(0);
}

static void hle_ReleaseSubIntrHandler(void) {
    KENTER();
    subintr *s = sub(psp_arg(0), psp_arg(1));
    if (!s) KRET(KERR_ILLEGAL_INTRCODE);
    if (!s->registered) KRET(KERR_NOTFOUND_HANDLER);
    memset(s, 0, sizeof *s);
    KRET(0);
}

/* ---- alarms ----------------------------------------------------------------- */

static void on_alarm(void *arg, uint64_t when) {
    kalarm *a = arg;
    a->timer = 0;
    const uint32_t uid = a->uid;
    const uint32_t next = khbbs_call_guest(a->handler, a->common, 0, 0, INTR_SP, 1);
    if (!a->used || a->uid != uid) return;         /* cancelled by its own handler */
    if (next) a->timer = khbbs_timer_add(when + next, on_alarm, a);
    else a->used = 0;
}

static void hle_SetAlarm(void) {
    KENTER();
    for (int i = 0; i < MAX_ALARMS; i++) {
        kalarm *a = &g_alarm[i];
        if (a->used) continue;
        a->used = 1;
        a->uid = khbbs_uid_new();
        a->handler = psp_arg(1);
        a->common = psp_arg(2);
        a->timer = khbbs_timer_add(khbbs_now() + psp_arg(0), on_alarm, a);
        KRET(a->uid);
    }
    KRET(KERR_NO_MEMORY);
}

static void hle_CancelAlarm(void) {
    KENTER();
    for (int i = 0; i < MAX_ALARMS; i++) {
        kalarm *a = &g_alarm[i];
        if (!a->used || a->uid != psp_arg(0)) continue;
        khbbs_timer_cancel(a->timer);
        memset(a, 0, sizeof *a);
        KRET(0);
    }
    KRET(KERR_UNKNOWN_ALMID);
}

/* ---- Kernel_Library: interrupt masking (per thread) -------------------------- */

static void hle_CpuSuspendIntr(void) {
    KENTER();
    const uint32_t prev = khbbs_intr_enabled();
    khbbs_intr_set(0);
    KRET(prev);
}

static void hle_CpuResumeIntr(void) {
    KENTER();
    khbbs_intr_set(psp_arg(0));
    KRET(0);
}

void khbbs_intr_register(void) {
    KREG("sceDisplay", sceDisplayWaitVblankStart, hle_WaitVblankStart);
    KREG("sceDisplay", sceDisplayWaitVblankStartCB, hle_WaitVblankStartCB);
    KREG("sceDisplay", sceDisplayWaitVblank, hle_WaitVblankStart);
    KREG("sceDisplay", sceDisplayGetVcount, hle_GetVcount);
    KREG("InterruptManager", sceKernelRegisterSubIntrHandler, hle_RegisterSubIntrHandler);
    KREG("InterruptManager", sceKernelEnableSubIntr, hle_EnableSubIntr);
    KREG("InterruptManager", sceKernelReleaseSubIntrHandler, hle_ReleaseSubIntrHandler);
    KREG("ThreadManForUser", sceKernelSetAlarm, hle_SetAlarm);
    KREG("ThreadManForUser", sceKernelCancelAlarm, hle_CancelAlarm);
    KREG("Kernel_Library", sceKernelCpuSuspendIntr, hle_CpuSuspendIntr);
    KREG("Kernel_Library", sceKernelCpuResumeIntr, hle_CpuResumeIntr);
}
```

In `runtime/kernel/kernel.c`: include `"intr.h"`; in `khbbs_kernel_reset` add `khbbs_intr_reset();` after `khbbs_cb_reset();`; in `khbbs_kernel_register` add `khbbs_intr_register();`. Add `kernel/intr.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_intr: ok`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/intr.h runtime/kernel/intr.c runtime/kernel/kernel.c runtime/CMakeLists.txt \
    tests/runtime/test_intr.c tests/runtime/CMakeLists.txt
git commit -m "kernel: vblank at 59.94 Hz, the vblank sub-interrupt, alarms, per-thread interrupt masking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Semaphores, event flags, mailboxes

**Files:**
- Create: `runtime/kernel/sema.c`, `runtime/kernel/evflag.c`, `runtime/kernel/mbx.c`, `runtime/kernel/sync.h`
- Modify: `runtime/kernel/kernel.c`, `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_sync.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics** (attr `0x100` = waiters queued by priority, else FIFO; deleting an object wakes its waiters with `WAIT_DELETE`; every blocking call takes `SceUInt *timeout` (µs, NULL = forever) and returns `WAIT_TIMEOUT` when it runs out, writing the time left back):

| Function | Arguments | Returns | Blocks | Errors |
|---|---|---|---|---|
| `sceKernelCreateSema` | name, attr, initCount, maxCount, option | uid | no | `ILLEGAL_COUNT` (init < 0, max ≤ 0, init > max), `NO_MEMORY` (128) |
| `sceKernelDeleteSema` | uid | 0 | no; may preempt | `UNKNOWN_SEMID` |
| `sceKernelSignalSema` | uid, signal | 0 | no; wakes every waiter whose count now fits, in queue order (UNVERIFIED: a waiter that does not fit does not hold back those behind it); may preempt | `UNKNOWN_SEMID`, `ILLEGAL_COUNT` (signal ≤ 0), `SEMA_OVF` (count + signal > max) |
| `sceKernelWaitSema` | uid, need, timeout | 0 | while count < need | `UNKNOWN_SEMID`, `ILLEGAL_COUNT` (need ≤ 0 or > max), `WAIT_TIMEOUT`, `WAIT_DELETE` |
| `sceKernelWaitSemaCB` | as above | 0 | as above, delivering callbacks | as above |
| `sceKernelPollSema` | uid, need | 0 (and takes need) | no | `UNKNOWN_SEMID`, `ILLEGAL_COUNT`, `SEMA_ZERO` (count < need) |
| `sceKernelReferSemaStatus` | uid, SceKernelSemaInfo *info | 0 | no | `UNKNOWN_SEMID`. `{size, name[32], attr, initCount, currentCount, maxCount, numWaitThreads}`, 0x38 bytes; `min(size, 0x38)` written, size kept |
| `sceKernelCreateEventFlag` | name, attr, initPattern, option | uid | no | `NO_MEMORY` (128). attr `0x200` = several threads may wait |
| `sceKernelDeleteEventFlag` | uid | 0 | no; may preempt | `UNKNOWN_EVFID` |
| `sceKernelSetEventFlag` | uid, bits | 0 | no; pattern \|= bits, then each waiter in queue order whose condition holds gets the pattern in *outBits, its clear mode is applied, and it wakes (later waiters see the cleared pattern); may preempt | `UNKNOWN_EVFID` |
| `sceKernelClearEventFlag` | uid, bits | 0 | no; pattern &= bits (bits to keep) | `UNKNOWN_EVFID` |
| `sceKernelWaitEventFlag` | uid, bits, wait (`0x00` AND, `0x01` OR, `+0x10` clear all, `+0x20` clear the bits waited for), u32 *outBits, timeout | 0, *outBits = the pattern before clearing | until the condition holds | `UNKNOWN_EVFID`, `EVF_ILPAT` (bits 0), `ILLEGAL_MODE` (other mode bits, or both clear bits; UNVERIFIED), `EVF_MULTI` (someone waits and attr lacks 0x200), `WAIT_TIMEOUT`, `WAIT_DELETE` |
| `sceKernelWaitEventFlagCB` | as above | as above | as above, delivering callbacks | as above |
| `sceKernelPollEventFlag` | uid, bits, wait, outBits | 0 | no | as Wait without timeout; `EVF_COND` (not met; *outBits = the pattern) |
| `sceKernelReferEventFlagStatus` | uid, SceKernelEventFlagInfo *info | 0 | no | `UNKNOWN_EVFID`. `{size, name[32], attr, initPattern, currentPattern, numWaitThreads}`, 0x34 bytes |
| `sceKernelCreateMbx` | name, attr, option | uid | no | `NO_MEMORY` (32). attr `0x400` = messages ordered by priority |
| `sceKernelDeleteMbx` | uid | 0 | no | `UNKNOWN_MBXID` |
| `sceKernelSendMbx` | uid, SceKernelMsgPacket *msg | 0 | no | `UNKNOWN_MBXID`, `NO_MEMORY` (64 queued; UNVERIFIED). FIFO append, or with attr `0x400` inserted after every message whose priority byte (`msg+4`) is ≤ its own. The packet's `next` field is not written (UNVERIFIED). `sceKernelReceiveMbx` is not imported, so no thread ever waits on a mailbox. |
| `sceKernelPollMbx` | uid, SceKernelMsgPacket **out | 0, *out = the first message (removed) | no | `UNKNOWN_MBXID`, `MBOX_NOMSG` |

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_sync.c` (the representative cases; the list after it names the rest, each its own root function in the same file):

```c
/* Semaphores, event flags and mailboxes from synthetic guest threads. */
#include "kcall.h"
#include "kernel/errors.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static char order[16];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }
static uint32_t spawn(uint32_t entry, uint32_t prio) {
    uint32_t t = CALL(sceKernelCreateThread, 6, gstr(0x08100000u, "t"), entry, prio, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, t, 0u, 0u);
    return t;
}

static uint32_t g_sema, g_evf, g_res[4];

/* Semaphore: a waiter wakes when signalled, with preemption; counts; timeout. */
static void sema_waiter(void) { g_res[0] = CALL(sceKernelWaitSema, 3, g_sema, 2u, 0u); mark('w'); }
static void t_sema(void) {
    g_sema = CALL(sceKernelCreateSema, 5, gstr(0x08100000u, "s"), 0u, 1u, 3u, 0u);
    CHECK(g_sema >= 0x00040000u && g_sema < 0x80000000u, "sema: create");
    uint32_t w = spawn(0x08805000u, 0x10);            /* wants 2, has 1: waits */
    mark('r');
    CHECK(CALL(sceKernelSignalSema, 2, g_sema, 1u) == 0, "sema: signal");   /* 2 now: the waiter preempts */
    mark('R');
    CHECK(g_res[0] == 0 && strcmp(order, "rwR") == 0, "sema: the waiter took 2 inside SignalSema");
    CHECK(CALL(sceKernelPollSema, 2, g_sema, 1u) == KERR_SEMA_ZERO, "sema: empty");
    CHECK(CALL(sceKernelSignalSema, 2, g_sema, 4u) == KERR_SEMA_OVF, "sema: overflow");
    CHECK(CALL(sceKernelSignalSema, 2, g_sema, 3u) == 0 && CALL(sceKernelPollSema, 2, g_sema, 3u) == 0, "sema: poll 3");
    psp_write32(0x08100100u, 500);
    const uint32_t r = CALL(sceKernelWaitSema, 3, g_sema, 1u, 0x08100100u);
    CHECK(r == KERR_WAIT_TIMEOUT && psp_read32(0x08100100u) == 0, "sema: timeout, 0 µs left");
    CHECK(CALL(sceKernelWaitSema, 3, g_sema, 4u, 0u) == KERR_ILLEGAL_COUNT, "sema: need > max");
    CHECK(CALL(sceKernelDeleteSema, 1, g_sema) == 0 && CALL(sceKernelSignalSema, 2, g_sema, 1u) == KERR_UNKNOWN_SEMID,
          "sema: delete");
    CALL(sceKernelWaitThreadEnd, 2, w, 0u);
}

/* Event flag: AND/OR, clear modes, outBits, waking in order. */
static void evf_and(void) { g_res[1] = CALL(sceKernelWaitEventFlag, 5, g_evf, 0x3u, 0x20u, 0x08100200u, 0u); mark('a'); }
static void evf_or(void)  { g_res[2] = CALL(sceKernelWaitEventFlag, 5, g_evf, 0x4u, 0x01u, 0x08100204u, 0u); mark('o'); }
static void t_evflag(void) {
    norder = 0;
    g_evf = CALL(sceKernelCreateEventFlag, 4, gstr(0x08100000u, "e"), 0x200u, 0x1u, 0u);
    spawn(0x08805100u, 0x10);                         /* waits for 0x1 AND 0x2, clears them */
    spawn(0x08805200u, 0x10);                         /* waits for 0x4 OR, no clear */
    CHECK(CALL(sceKernelSetEventFlag, 2, g_evf, 0x2u) == 0, "evf: set 0x2");   /* 0x3: AND waiter wakes */
    CHECK(g_res[1] == 0 && psp_read32(0x08100200u) == 0x3u && strcmp(order, "a") == 0, "evf: AND met, outBits 0x3");
    CALL(sceKernelSetEventFlag, 2, g_evf, 0x4u);
    CHECK(strcmp(order, "ao") == 0 && psp_read32(0x08100204u) == 0x4u, "evf: OR met; AND's clear removed 0x3");
    CHECK(CALL(sceKernelPollEventFlag, 4, g_evf, 0x8u, 0u, 0x08100208u) == KERR_EVF_COND &&
          psp_read32(0x08100208u) == 0x4u, "evf: poll not met");
    CHECK(CALL(sceKernelClearEventFlag, 2, g_evf, 0x0u) == 0 &&
          CALL(sceKernelPollEventFlag, 4, g_evf, 0x4u, 1u, 0u) == KERR_EVF_COND, "evf: clear keeps only the given bits");
    CHECK(CALL(sceKernelWaitEventFlag, 5, g_evf, 0u, 0u, 0u, 0u) == KERR_EVF_ILPAT, "evf: zero bits");
    psp_write32(0x08100300u, 0x34);
    CHECK(CALL(sceKernelReferEventFlagStatus, 2, g_evf, 0x08100300u) == 0 &&
          psp_read32(0x08100300u + 0x24) == 0x200u && psp_read32(0x08100300u + 0x28) == 0x1u &&
          psp_read32(0x08100300u + 0x2C) == 0u, "evf: refer attr, init, current");
    CHECK(CALL(sceKernelDeleteEventFlag, 1, g_evf) == 0, "evf: delete");
}

/* Mailbox: FIFO and priority order. */
static void t_mbx(void) {
    uint32_t m = CALL(sceKernelCreateMbx, 3, gstr(0x08100000u, "m"), 0x400u, 0u);
    psp_write8(0x08100404u, 5); psp_write8(0x08100414u, 1); psp_write8(0x08100424u, 5);
    CALL(sceKernelSendMbx, 2, m, 0x08100400u);
    CALL(sceKernelSendMbx, 2, m, 0x08100410u);
    CALL(sceKernelSendMbx, 2, m, 0x08100420u);
    uint32_t got[3];
    for (int i = 0; i < 3; i++) { CALL(sceKernelPollMbx, 2, m, 0x08100500u); got[i] = psp_read32(0x08100500u); }
    CHECK(got[0] == 0x08100410u && got[1] == 0x08100400u && got[2] == 0x08100420u, "mbx: by priority, FIFO within");
    CHECK(CALL(sceKernelPollMbx, 2, m, 0x08100500u) == KERR_MBOX_NOMSG, "mbx: empty");
    CHECK(CALL(sceKernelDeleteMbx, 1, m) == 0 && CALL(sceKernelPollMbx, 2, m, 0x08100500u) == KERR_UNKNOWN_MBXID,
          "mbx: delete");
}

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, sema_waiter);
    psp_register(0x08805100u, evf_and);
    psp_register(0x08805200u, evf_or);
    static const struct { uint32_t addr; void (*fn)(void); } T[] = {
        { 0x08806000u, t_sema }, { 0x08806100u, t_evflag }, { 0x08806200u, t_mbx },
    };
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) {
        psp_register(T[i].addr, T[i].fn);
        norder = 0;
        khbbs_result r = ktest_boot(T[i].addr, NULL);
        if (r.why != KHBBS_RETURNED) { printf("FAIL case %zu: %s\n", i, khbbs_stop_name(r.why)); failures++; }
    }
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    printf("%s\n", failures ? "test_sync: FAILED" : "test_sync: ok");
    return failures ? 1 : 0;
}
```

Further cases, each a root function in the same file:
- `t_sema_order`: FIFO sema (attr 0): A (0x30) waits first (root `DelayThread(100)` lets it), then B (0x10) waits; `SignalSema(1)` → A gets it (B stays waiting; check through `ReferSemaStatus`: currentCount 0, numWaitThreads 1). The same with attr `0x100` → B gets it first.
- `t_sema_fit`: count 0, A waits for 3, B waits for 1; `SignalSema(1)` wakes B (A does not fit and does not hold B back).
- `t_sema_delete`: a waiter gets `WAIT_DELETE` when the semaphore is deleted.
- `t_sema_cb`: `WaitSemaCB` delivers a pending callback before blocking, then takes the count when another thread signals.
- `t_evf_multi`: attr 0: a second waiter gets `EVF_MULTI`; attr `0x200`: both wait.
- `t_evf_clearall`: wait mode `0x11` (OR + clear all) leaves pattern 0.
- `t_evf_timeout`: `WaitEventFlag` with a 300 µs timeout returns `WAIT_TIMEOUT`.
- `t_evf_delete`: a waiter gets `WAIT_DELETE`.
- `t_evf_cb`: `WaitEventFlagCB` delivers a callback, then wakes on `SetEventFlag`.

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(sync)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R sync --output-on-failure`
Expected: FAIL (psprecomp's `sceKernelWaitSema` returns `WAIT_TIMEOUT` instead of blocking: "sema: the waiter took 2 inside SignalSema").

- [ ] **Step 3: Implement the semaphore (the representative family)**

`runtime/kernel/sync.h`:

```c
/* Semaphores, event flags, mailboxes. Internal; lock held. */
#ifndef KHBBS_KERNEL_SYNC_H
#define KHBBS_KERNEL_SYNC_H

void khbbs_sema_reset(void);
void khbbs_sema_register(void);
void khbbs_evflag_reset(void);
void khbbs_evflag_register(void);
void khbbs_mbx_reset(void);
void khbbs_mbx_register(void);

#endif
```

`runtime/kernel/sema.c`:

```c
/* Semaphores. Plan P4 Task 12. */
#include "khle.h"
#include "ktime.h"
#include "sync.h"

#include <string.h>

#define MAX_SEMAS 128
#define ATTR_PRIORITY 0x100u

typedef struct { int used; uint32_t uid, attr; int32_t init, count, max; char name[32]; } ksema;
static ksema g_sema[MAX_SEMAS];

static ksema *find(uint32_t uid) {
    for (int i = 0; uid && i < MAX_SEMAS; i++)
        if (g_sema[i].used && g_sema[i].uid == uid) return &g_sema[i];
    return NULL;
}

void khbbs_sema_reset(void) { memset(g_sema, 0, sizeof g_sema); }

/* Every waiter whose need fits takes it, in queue order (UNVERIFIED). */
static void grant(ksema *s) {
    khbbs_thread *q[KHBBS_MAX_THREADS];
    const int n = khbbs_waiters(KW_SEMA, s->uid, (s->attr & ATTR_PRIORITY) != 0, q);
    for (int i = 0; i < n && s->count > 0; i++) {
        const int32_t need = (int32_t)q[i]->wait->p[0];
        if (need > s->count) continue;
        s->count -= need;
        khbbs_sched_wake(q[i], 0);
    }
}

/* After callbacks: take the count if it is there now. */
static int retry(khbbs_wait *w) {
    ksema *s = find(w->id);
    if (!s) { w->result = (int32_t)KERR_WAIT_DELETE; return 1; }
    if ((int32_t)w->p[0] > s->count) return 0;
    s->count -= (int32_t)w->p[0];
    w->result = 0;
    return 1;
}

static void hle_CreateSema(void) {
    KENTER();
    const int32_t init = (int32_t)psp_arg(2), max = (int32_t)psp_arg(3);
    if (init < 0 || max <= 0 || init > max) KRET(KERR_ILLEGAL_COUNT);
    for (int i = 0; i < MAX_SEMAS; i++) {
        ksema *s = &g_sema[i];
        if (s->used) continue;
        memset(s, 0, sizeof *s);
        s->used = 1;
        s->uid = khbbs_uid_new();
        psp_str(psp_arg(0), s->name, sizeof s->name);
        s->attr = psp_arg(1);
        s->init = s->count = init;
        s->max = max;
        KRET(s->uid);
    }
    KRET(KERR_NO_MEMORY);
}

static void hle_DeleteSema(void) {
    KENTER();
    ksema *s = find(psp_arg(0));
    if (!s) KRET(KERR_UNKNOWN_SEMID);
    const uint32_t uid = s->uid;
    s->used = 0;
    khbbs_wake_all(KW_SEMA, uid, (int32_t)KERR_WAIT_DELETE);
    KRET_PREEMPT(0);
}

static void hle_SignalSema(void) {
    KENTER();
    ksema *s = find(psp_arg(0));
    const int32_t n = (int32_t)psp_arg(1);
    if (!s) KRET(KERR_UNKNOWN_SEMID);
    if (n <= 0) KRET(KERR_ILLEGAL_COUNT);
    if (n > s->max - s->count) KRET(KERR_SEMA_OVF);
    s->count += n;
    grant(s);
    KRET_PREEMPT(0);
}

static void wait_sema(int cb) {
    KENTER();
    ksema *s = find(psp_arg(0));
    const int32_t need = (int32_t)psp_arg(1);
    const uint32_t tp = psp_arg(2);
    if (!s) KRET(KERR_UNKNOWN_SEMID);
    if (need <= 0 || need > s->max) KRET(KERR_ILLEGAL_COUNT);
    if (s->count >= need) { s->count -= need; KRET(0); }
    khbbs_wait w = { .type = KW_SEMA, .id = s->uid, .cb = cb, .timeout_ptr = tp,
                     .deadline = khbbs_deadline(tp), .p = { (uint32_t)need }, .retry = retry };
    KRET(khbbs_sched_wait(&w));
}
static void hle_WaitSema(void)   { wait_sema(0); }
static void hle_WaitSemaCB(void) { wait_sema(1); }

static void hle_PollSema(void) {
    KENTER();
    ksema *s = find(psp_arg(0));
    const int32_t need = (int32_t)psp_arg(1);
    if (!s) KRET(KERR_UNKNOWN_SEMID);
    if (need <= 0) KRET(KERR_ILLEGAL_COUNT);
    if (s->count < need) KRET(KERR_SEMA_ZERO);
    s->count -= need;
    KRET(0);
}

static void hle_ReferSemaStatus(void) {
    KENTER();
    ksema *s = find(psp_arg(0));
    const uint32_t info = psp_arg(1);
    if (!s) KRET(KERR_UNKNOWN_SEMID);
    khbbs_thread *q[KHBBS_MAX_THREADS];
    const uint32_t f[5] = { s->attr, (uint32_t)s->init, (uint32_t)s->count, (uint32_t)s->max,
                            (uint32_t)khbbs_waiters(KW_SEMA, s->uid, 0, q) };
    uint8_t b[0x38];
    memset(b, 0, sizeof b);
    memcpy(b + 4, s->name, strlen(s->name));
    for (int i = 0; i < 5; i++) memcpy(b + 0x24 + 4 * i, &f[i], 4);   /* little-endian hosts */
    const uint32_t size = psp_read32(info);
    const uint32_t n = size < sizeof b ? size : (uint32_t)sizeof b;
    if (n > 4) psp_mem_write_block(info + 4, b + 4, n - 4);
    KRET(0);
}

void khbbs_sema_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelCreateSema, hle_CreateSema);
    KREG(L, sceKernelDeleteSema, hle_DeleteSema);
    KREG(L, sceKernelSignalSema, hle_SignalSema);
    KREG(L, sceKernelWaitSema, hle_WaitSema);
    KREG(L, sceKernelWaitSemaCB, hle_WaitSemaCB);
    KREG(L, sceKernelPollSema, hle_PollSema);
    KREG(L, sceKernelReferSemaStatus, hle_ReferSemaStatus);
}
```

- [ ] **Step 4: Event flags and mailboxes**

`runtime/kernel/evflag.c`:

```c
/* Event flags. Plan P4 Task 12. */
#include "khle.h"
#include "ktime.h"
#include "sync.h"

#include <string.h>

#define MAX_EVFS       128
#define EVF_ATTR_PRI   0x100u
#define EVF_ATTR_MULTI 0x200u
#define EVF_WAIT_OR    0x01u
#define EVF_CLEARALL   0x10u
#define EVF_CLEARPAT   0x20u

typedef struct { int used; uint32_t uid, attr, init, bits; char name[32]; } kevf;
static kevf g_evf[MAX_EVFS];

static kevf *find(uint32_t uid) {
    for (int i = 0; uid && i < MAX_EVFS; i++)
        if (g_evf[i].used && g_evf[i].uid == uid) return &g_evf[i];
    return NULL;
}

void khbbs_evflag_reset(void) { memset(g_evf, 0, sizeof g_evf); }

static int met(uint32_t pattern, uint32_t bits, uint32_t mode) {
    return mode & EVF_WAIT_OR ? (pattern & bits) != 0 : (pattern & bits) == bits;
}

/* The waiter gets the pattern, then its clear mode applies. */
static void take(kevf *e, uint32_t bits, uint32_t mode, uint32_t out) {
    if (out) psp_write32(out, e->bits);
    if (mode & EVF_CLEARALL) e->bits = 0;
    else if (mode & EVF_CLEARPAT) e->bits &= ~bits;
}

static int bad_mode(uint32_t mode) {                  /* UNVERIFIED: both clear bits is an error */
    return (mode & ~(EVF_WAIT_OR | EVF_CLEARALL | EVF_CLEARPAT)) != 0 ||
           (mode & (EVF_CLEARALL | EVF_CLEARPAT)) == (EVF_CLEARALL | EVF_CLEARPAT);
}

static int retry(khbbs_wait *w) {                     /* p = { bits, mode, out } */
    kevf *e = find(w->id);
    if (!e) { w->result = (int32_t)KERR_WAIT_DELETE; return 1; }
    if (!met(e->bits, w->p[0], w->p[1])) return 0;
    take(e, w->p[0], w->p[1], w->p[2]);
    w->result = 0;
    return 1;
}

static void hle_CreateEventFlag(void) {
    KENTER();
    for (int i = 0; i < MAX_EVFS; i++) {
        kevf *e = &g_evf[i];
        if (e->used) continue;
        memset(e, 0, sizeof *e);
        e->used = 1;
        e->uid = khbbs_uid_new();
        psp_str(psp_arg(0), e->name, sizeof e->name);
        e->attr = psp_arg(1);
        e->init = e->bits = psp_arg(2);
        KRET(e->uid);
    }
    KRET(KERR_NO_MEMORY);
}

static void hle_DeleteEventFlag(void) {
    KENTER();
    kevf *e = find(psp_arg(0));
    if (!e) KRET(KERR_UNKNOWN_EVFID);
    const uint32_t uid = e->uid;
    e->used = 0;
    khbbs_wake_all(KW_EVENTFLAG, uid, (int32_t)KERR_WAIT_DELETE);
    KRET_PREEMPT(0);
}

static void hle_SetEventFlag(void) {
    KENTER();
    kevf *e = find(psp_arg(0));
    if (!e) KRET(KERR_UNKNOWN_EVFID);
    e->bits |= psp_arg(1);
    khbbs_thread *q[KHBBS_MAX_THREADS];
    const int n = khbbs_waiters(KW_EVENTFLAG, e->uid, (e->attr & EVF_ATTR_PRI) != 0, q);
    for (int i = 0; i < n; i++) {
        const khbbs_wait *w = q[i]->wait;
        if (!met(e->bits, w->p[0], w->p[1])) continue;
        take(e, w->p[0], w->p[1], w->p[2]);
        khbbs_sched_wake(q[i], 0);
    }
    KRET_PREEMPT(0);
}

static void hle_ClearEventFlag(void) {
    KENTER();
    kevf *e = find(psp_arg(0));
    if (!e) KRET(KERR_UNKNOWN_EVFID);
    e->bits &= psp_arg(1);                           /* the bits to keep */
    KRET(0);
}

/* mode 0: wait, 1: wait delivering callbacks, 2: poll. */
static void wait_evf(int mode_kind) {
    KENTER();
    kevf *e = find(psp_arg(0));
    const uint32_t bits = psp_arg(1), mode = psp_arg(2), out = psp_arg(3);
    const uint32_t tp = mode_kind == 2 ? 0 : psp_arg(4);
    if (!e) KRET(KERR_UNKNOWN_EVFID);
    if (!bits) KRET(KERR_EVF_ILPAT);
    if (bad_mode(mode)) KRET(KERR_ILLEGAL_MODE);
    khbbs_thread *q[KHBBS_MAX_THREADS];
    if (mode_kind != 2 && !(e->attr & EVF_ATTR_MULTI) && khbbs_waiters(KW_EVENTFLAG, e->uid, 0, q) > 0)
        KRET(KERR_EVF_MULTI);
    if (met(e->bits, bits, mode)) { take(e, bits, mode, out); KRET(0); }
    if (mode_kind == 2) {
        if (out) psp_write32(out, e->bits);
        KRET(KERR_EVF_COND);
    }
    khbbs_wait w = { .type = KW_EVENTFLAG, .id = e->uid, .cb = mode_kind == 1, .timeout_ptr = tp,
                     .deadline = khbbs_deadline(tp), .p = { bits, mode, out }, .retry = retry };
    KRET(khbbs_sched_wait(&w));
}
static void hle_WaitEventFlag(void)   { wait_evf(0); }
static void hle_WaitEventFlagCB(void) { wait_evf(1); }
static void hle_PollEventFlag(void)   { wait_evf(2); }

static void hle_ReferEventFlagStatus(void) {
    KENTER();
    kevf *e = find(psp_arg(0));
    const uint32_t info = psp_arg(1);
    if (!e) KRET(KERR_UNKNOWN_EVFID);
    khbbs_thread *q[KHBBS_MAX_THREADS];
    const uint32_t f[4] = { e->attr, e->init, e->bits, (uint32_t)khbbs_waiters(KW_EVENTFLAG, e->uid, 0, q) };
    uint8_t b[0x34];
    memset(b, 0, sizeof b);
    memcpy(b + 4, e->name, strlen(e->name));
    for (int i = 0; i < 4; i++) memcpy(b + 0x24 + 4 * i, &f[i], 4);   /* little-endian hosts */
    const uint32_t size = psp_read32(info);
    const uint32_t n = size < sizeof b ? size : (uint32_t)sizeof b;
    if (n > 4) psp_mem_write_block(info + 4, b + 4, n - 4);
    KRET(0);
}

void khbbs_evflag_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelCreateEventFlag, hle_CreateEventFlag);
    KREG(L, sceKernelDeleteEventFlag, hle_DeleteEventFlag);
    KREG(L, sceKernelSetEventFlag, hle_SetEventFlag);
    KREG(L, sceKernelClearEventFlag, hle_ClearEventFlag);
    KREG(L, sceKernelWaitEventFlag, hle_WaitEventFlag);
    KREG(L, sceKernelWaitEventFlagCB, hle_WaitEventFlagCB);
    KREG(L, sceKernelPollEventFlag, hle_PollEventFlag);
    KREG(L, sceKernelReferEventFlagStatus, hle_ReferEventFlagStatus);
}
```

`runtime/kernel/mbx.c` (32 mailboxes, each a host-side queue of up to 64 packet addresses):

```c
/* Message boxes. Plan P4 Task 12. sceKernelReceiveMbx is not imported, so
 * no thread waits on one: Send queues, Poll dequeues. */
#include "khle.h"
#include "sync.h"

#include <string.h>

#define MAX_MBX     32
#define MAX_MSGS    64
#define MBX_MSG_PRI 0x400u

typedef struct { int used; uint32_t uid, attr; uint32_t msg[MAX_MSGS]; int n; char name[32]; } kmbx;
static kmbx g_mbx[MAX_MBX];

static kmbx *find(uint32_t uid) {
    for (int i = 0; uid && i < MAX_MBX; i++)
        if (g_mbx[i].used && g_mbx[i].uid == uid) return &g_mbx[i];
    return NULL;
}

void khbbs_mbx_reset(void) { memset(g_mbx, 0, sizeof g_mbx); }

static void hle_CreateMbx(void) {
    KENTER();
    for (int i = 0; i < MAX_MBX; i++) {
        kmbx *m = &g_mbx[i];
        if (m->used) continue;
        memset(m, 0, sizeof *m);
        m->used = 1;
        m->uid = khbbs_uid_new();
        psp_str(psp_arg(0), m->name, sizeof m->name);
        m->attr = psp_arg(1);
        KRET(m->uid);
    }
    KRET(KERR_NO_MEMORY);
}

static void hle_DeleteMbx(void) {
    KENTER();
    kmbx *m = find(psp_arg(0));
    if (!m) KRET(KERR_UNKNOWN_MBXID);
    m->used = 0;
    KRET(0);
}

static void hle_SendMbx(void) {
    KENTER();
    kmbx *m = find(psp_arg(0));
    const uint32_t msg = psp_arg(1);
    if (!m) KRET(KERR_UNKNOWN_MBXID);
    if (m->n == MAX_MSGS) KRET(KERR_NO_MEMORY);
    int at = m->n;
    if (m->attr & MBX_MSG_PRI) {
        const uint8_t pri = psp_read8(msg + 4);
        while (at > 0 && psp_read8(m->msg[at - 1] + 4) > pri) at--;
    }
    memmove(&m->msg[at + 1], &m->msg[at], (size_t)(m->n - at) * sizeof m->msg[0]);
    m->msg[at] = msg;
    m->n++;
    KRET(0);
}

static void hle_PollMbx(void) {
    KENTER();
    kmbx *m = find(psp_arg(0));
    if (!m) KRET(KERR_UNKNOWN_MBXID);
    if (!m->n) KRET(KERR_MBOX_NOMSG);
    psp_write32(psp_arg(1), m->msg[0]);
    memmove(&m->msg[0], &m->msg[1], (size_t)(m->n - 1) * sizeof m->msg[0]);
    m->n--;
    KRET(0);
}

void khbbs_mbx_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelCreateMbx, hle_CreateMbx);
    KREG(L, sceKernelDeleteMbx, hle_DeleteMbx);
    KREG(L, sceKernelSendMbx, hle_SendMbx);
    KREG(L, sceKernelPollMbx, hle_PollMbx);
}
```

In `runtime/kernel/kernel.c`: include `"sync.h"`; add `khbbs_sema_reset(); khbbs_evflag_reset(); khbbs_mbx_reset();` to the reset after `khbbs_intr_reset();` and `khbbs_sema_register(); khbbs_evflag_register(); khbbs_mbx_register();` to the registration. Add `kernel/sema.c`, `kernel/evflag.c`, `kernel/mbx.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 5: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_sync: ok`.

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/sync.h runtime/kernel/sema.c runtime/kernel/evflag.c runtime/kernel/mbx.c \
    runtime/kernel/kernel.c runtime/CMakeLists.txt tests/runtime/test_sync.c tests/runtime/CMakeLists.txt
git commit -m "kernel: semaphores, event flags and message boxes with blocking waits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Mutexes and lightweight mutexes

**Files:**
- Create: `runtime/kernel/mutex.c`, `runtime/kernel/mutex.h`
- Modify: `runtime/kernel/kernel.c`, `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_mutex.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics.** attr `0x100` = waiters by priority, `0x200` = recursive. A mutex is free when its count is 0. Unlocking to 0 hands it straight to the first waiter in queue order, with that waiter's count, and wakes it. A lightweight mutex keeps its state in the caller's 32-byte work area (`SceKernelLwMutexWork`, the field order of the PSP SDK's structure, confirmed by the probe): `+0x00 count`, `+0x04 owner thread uid`, `+0x08 attr`, `+0x0C numWaitThreads`, `+0x10 uid`, `+0x14..+0x1F` 0. The kernel object (found through `+0x10`) holds the name and the waiters.

| Function | Arguments | Returns | Blocks | Errors |
|---|---|---|---|---|
| `sceKernelCreateMutex` | name, attr, initCount, option | uid | no | `ILLEGAL_COUNT` (init < 0, or > 1 without recursive), `NO_MEMORY` (128). init > 0 makes the caller the owner. |
| `sceKernelDeleteMutex` | uid | 0 | no; may preempt | `MUTEX_NOT_FOUND` |
| `sceKernelLockMutex` | uid, count, timeout | 0 | while another thread owns it | `MUTEX_NOT_FOUND`, `ILLEGAL_COUNT` (≤ 0, or > 1 without recursive), `MUTEX_RECURSIVE` (owner locks a non-recursive one again), `MUTEX_LOCK_OVERFLOW`, `WAIT_TIMEOUT`, `WAIT_DELETE` |
| `sceKernelLockMutexCB` | as above | 0 | as above, delivering callbacks | as above |
| `sceKernelTryLockMutex` | uid, count | 0 | no | as Lock, and `MUTEX_TRYLOCK_FAILED` if another thread owns it |
| `sceKernelUnlockMutex` | uid, count | 0 | no; may preempt | `MUTEX_NOT_FOUND`, `ILLEGAL_COUNT` (≤ 0), `MUTEX_NOT_LOCKED` (not owned by the caller; UNVERIFIED for another owner), `MUTEX_UNLOCK_UNDERFLOW` (count > held) |
| `sceKernelCreateLwMutex` | work, name, attr, initCount, option | 0 (observed) | no | `ILLEGAL_COUNT`, `NO_MEMORY` (128). Writes the work area: count = init, owner = init ? caller : 0, attr, 0, a new uid (observed: `0, 0, 0x100, 0, 0x11C` for init 0, attr 0x100). |
| `sceKernelDeleteLwMutex` | work | 0 | no; may preempt | `LWMUTEX_NOT_FOUND`. Waiters get `WAIT_DELETE`; the work area is left as is. |
| `sceKernelLockLwMutex` | work, count, timeout | 0 | while another thread owns it; `numWaitThreads` counts it meanwhile | `LWMUTEX_NOT_FOUND`, `ILLEGAL_COUNT`, `LWMUTEX_RECURSIVE`, `LWMUTEX_LOCK_OVERFLOW`, `WAIT_TIMEOUT`, `WAIT_DELETE` |
| `sceKernelLockLwMutexCB` | as above | 0 | as above, delivering callbacks | as above |
| `sceKernelTryLockLwMutex_600` | work, count | 0 | no | as Lock, and `LWMUTEX_TRYLOCK_FAILED` |
| `sceKernelUnlockLwMutex` | work, count | 0 | no; may preempt | `LWMUTEX_NOT_FOUND`, `ILLEGAL_COUNT`, `LWMUTEX_NOT_LOCKED`, `LWMUTEX_UNLOCK_UNDERFLOW` |

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_mutex.c`:

```c
/* Mutexes and lightweight mutexes: ownership, recursion, handover order,
 * timeouts, deletion, and the LwMutex work area as PPSSPP shows it. */
#include "kcall.h"
#include "kernel/errors.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static char order[16];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }
static uint32_t spawn(uint32_t entry, uint32_t prio) {
    uint32_t t = CALL(sceKernelCreateThread, 6, gstr(0x08100000u, "t"), entry, prio, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, t, 0u, 0u);
    return t;
}

static uint32_t g_m;
#define WORK 0x08100400u
static void locker_a(void) { CALL(sceKernelLockMutex, 3, g_m, 1u, 0u); mark('A'); CALL(sceKernelUnlockMutex, 2, g_m, 1u); }
static void locker_b(void) { CALL(sceKernelLockMutex, 3, g_m, 1u, 0u); mark('B'); CALL(sceKernelUnlockMutex, 2, g_m, 1u); }

/* Handover order: A (low) waits first, B (high) second. */
static void handover(uint32_t attr, const char *want) {
    norder = 0;
    g_m = CALL(sceKernelCreateMutex, 4, gstr(0x08100000u, "m"), attr, 1u, 0u);   /* root owns it */
    uint32_t a = spawn(0x08805000u, 0x30);
    CALL(sceKernelDelayThread, 1, 100u);            /* A runs and blocks */
    uint32_t b = spawn(0x08805100u, 0x10);           /* B runs and blocks */
    CALL(sceKernelUnlockMutex, 2, g_m, 1u);
    CALL(sceKernelWaitThreadEnd, 2, a, 0u);
    CALL(sceKernelWaitThreadEnd, 2, b, 0u);
    CHECK(strcmp(order, want) == 0, attr ? "mutex: priority handover" : "mutex: FIFO handover");
    CALL(sceKernelDeleteMutex, 1, g_m);
}

static void t_mutex(void) {
    uint32_t m = CALL(sceKernelCreateMutex, 4, gstr(0x08100000u, "r"), 0x300u, 0u, 0u);
    CHECK(CALL(sceKernelLockMutex, 3, m, 1u, 0u) == 0 && CALL(sceKernelLockMutex, 3, m, 2u, 0u) == 0, "recursive lock");
    CHECK(CALL(sceKernelUnlockMutex, 2, m, 4u) == KERR_MUTEX_UNLOCK_UNDERFLOW, "underflow");
    CHECK(CALL(sceKernelUnlockMutex, 2, m, 3u) == 0, "unlock all three");
    CHECK(CALL(sceKernelUnlockMutex, 2, m, 1u) == KERR_MUTEX_NOT_LOCKED, "not locked");
    uint32_t n = CALL(sceKernelCreateMutex, 4, gstr(0x08100000u, "n"), 0u, 0u, 0u);
    CALL(sceKernelLockMutex, 3, n, 1u, 0u);
    CHECK(CALL(sceKernelLockMutex, 3, n, 1u, 0u) == KERR_MUTEX_RECURSIVE, "non-recursive relock");
    CHECK(CALL(sceKernelLockMutex, 3, n, 2u, 0u) == KERR_ILLEGAL_COUNT, "count 2 without recursion");
    CHECK(CALL(sceKernelCreateMutex, 4, gstr(0x08100000u, "x"), 0u, 2u, 0u) == KERR_ILLEGAL_COUNT, "init 2 without recursion");
    CALL(sceKernelDeleteMutex, 1, n);
    CHECK(CALL(sceKernelLockMutex, 3, n, 1u, 0u) == KERR_MUTEX_NOT_FOUND, "deleted");
    handover(0x000u, "AB");
    handover(0x100u, "BA");
}

static void lw_other(void) {
    CHECK(CALL(sceKernelTryLockLwMutex_600, 2, WORK, 1u) == KERR_LWMUTEX_TRYLOCK_FAILED, "lw: trylock while owned");
    CHECK(CALL(sceKernelUnlockLwMutex, 2, WORK, 1u) == KERR_LWMUTEX_NOT_LOCKED, "lw: unlock by a non-owner");
    mark('o');
    CHECK(CALL(sceKernelLockLwMutexCB, 3, WORK, 1u, 0u) == 0, "lw: the waiter gets it");
    mark('O');
    CHECK(psp_read32(WORK) == 1 && psp_read32(WORK + 4) == CALL0(sceKernelGetThreadId) && psp_read32(WORK + 12) == 0,
          "lw: handed over: count 1, owner me, nobody waiting");
    CALL(sceKernelUnlockLwMutex, 2, WORK, 1u);
}

static void t_lwmutex(void) {
    norder = 0;
    CHECK(CALL(sceKernelCreateLwMutex, 5, WORK, gstr(0x08100000u, "lw"), 0x100u, 0u, 0u) == 0, "lw: create returns 0");
    const uint32_t uid = psp_read32(WORK + 0x10);
    CHECK(psp_read32(WORK) == 0 && psp_read32(WORK + 4) == 0 && psp_read32(WORK + 8) == 0x100u &&
          psp_read32(WORK + 12) == 0 && uid >= 0x00040000u && psp_read32(WORK + 0x14) == 0,
          "lw: work area as PPSSPP leaves it");
    const uint32_t me = CALL0(sceKernelGetThreadId);
    CHECK(CALL(sceKernelLockLwMutex, 3, WORK, 1u, 0u) == 0 && psp_read32(WORK) == 1 && psp_read32(WORK + 4) == me,
          "lw: locked: count 1, owner root");
    CHECK(CALL(sceKernelLockLwMutex, 3, WORK, 1u, 0u) == KERR_LWMUTEX_RECURSIVE, "lw: non-recursive relock");
    uint32_t o = spawn(0x08805200u, 0x30);
    CALL(sceKernelDelayThread, 1, 100u);             /* o runs, fails its trylock, blocks */
    CHECK(psp_read32(WORK + 12) == 1, "lw: numWaitThreads 1 while o waits");
    mark('r');
    CHECK(CALL(sceKernelUnlockLwMutex, 2, WORK, 1u) == 0, "lw: unlock");
    CALL(sceKernelWaitThreadEnd, 2, o, 0u);
    CHECK(strcmp(order, "orO") == 0, "lw: o waited until root unlocked");
    CHECK(psp_read32(WORK) == 0 && psp_read32(WORK + 4) == 0, "lw: free again");
    CHECK(CALL(sceKernelDeleteLwMutex, 1, WORK) == 0, "lw: delete");
    CHECK(CALL(sceKernelLockLwMutex, 3, WORK, 1u, 0u) == KERR_LWMUTEX_NOT_FOUND, "lw: deleted");
}

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, locker_a);
    psp_register(0x08805100u, locker_b);
    psp_register(0x08805200u, lw_other);
    psp_register(0x08806000u, t_mutex);
    psp_register(0x08806100u, t_lwmutex);
    CHECK(ktest_boot(0x08806000u, NULL).why == KHBBS_RETURNED, "mutex run");
    CHECK(ktest_boot(0x08806100u, NULL).why == KHBBS_RETURNED, "lwmutex run");
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    printf("%s\n", failures ? "test_mutex: FAILED" : "test_mutex: ok");
    return failures ? 1 : 0;
}
```

Further cases in the same file: `LockMutex` with a 300 µs timeout while another thread owns it → `WAIT_TIMEOUT` and 0 written back; a waiter on a deleted mutex → `WAIT_DELETE`; `LockMutexCB` delivers a pending callback then takes the mutex when it is unlocked during the callback by... the owner (another thread) later; `TryLockMutex` on a free mutex → 0; a recursive LwMutex (attr `0x200`) locked twice → work count 2; `CreateLwMutex` with init 1 → owner = caller.

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(mutex)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R mutex --output-on-failure`
Expected: FAIL (the mutex NIDs are unimplemented: "recursive lock").

- [ ] **Step 3: Implement**

`runtime/kernel/mutex.h`:

```c
/* Mutexes and lightweight mutexes. Internal; lock held. */
#ifndef KHBBS_KERNEL_MUTEX_H
#define KHBBS_KERNEL_MUTEX_H

void khbbs_mutex_reset(void);
void khbbs_mutex_register(void);

#endif
```

`runtime/kernel/mutex.c`:

```c
/* Mutexes and lightweight mutexes. Plan P4 Task 13. Both share one lock
 * algorithm over a "state" (count and owner): a kernel object for a mutex,
 * the guest's work area for an LwMutex. */
#include "mutex.h"
#include "khle.h"
#include "ktime.h"

#include <string.h>

#define MAX_MUTEXES    128
#define ATTR_PRIORITY  0x100u
#define ATTR_RECURSIVE 0x200u

#define LW_COUNT 0x00u
#define LW_OWNER 0x04u
#define LW_ATTR  0x08u
#define LW_NWAIT 0x0Cu
#define LW_UID   0x10u

typedef struct {
    int      used, lw;
    uint32_t uid, attr, work;     /* work: the LwMutex work area (lw only) */
    uint32_t owner;               /* mutex only; an LwMutex keeps it in work */
    int32_t  count;
    char     name[32];
} kmutex;

static kmutex g_mx[MAX_MUTEXES];

void khbbs_mutex_reset(void) { memset(g_mx, 0, sizeof g_mx); }

static kmutex *find(uint32_t uid, int lw) {
    for (int i = 0; uid && i < MAX_MUTEXES; i++)
        if (g_mx[i].used && g_mx[i].lw == lw && g_mx[i].uid == uid) return &g_mx[i];
    return NULL;
}
static kmutex *find_lw(uint32_t work) {
    kmutex *m = find(psp_read32(work + LW_UID), 1);
    return m && m->work == work ? m : NULL;
}

static int32_t get_count(const kmutex *m)  { return m->lw ? (int32_t)psp_read32(m->work + LW_COUNT) : m->count; }
static uint32_t get_owner(const kmutex *m) { return m->lw ? psp_read32(m->work + LW_OWNER) : m->owner; }
static void set_state(kmutex *m, int32_t count, uint32_t owner) {
    if (m->lw) { psp_write32(m->work + LW_COUNT, (uint32_t)count); psp_write32(m->work + LW_OWNER, owner); }
    else { m->count = count; m->owner = owner; }
}
static void add_waiting(kmutex *m, int32_t d) {
    if (m->lw) psp_write32(m->work + LW_NWAIT, psp_read32(m->work + LW_NWAIT) + (uint32_t)d);
}

#define E(m, mutex_err, lw_err) ((m)->lw ? (lw_err) : (mutex_err))

/* 1 taken, 0 owned by another thread, or an error code (negative as int32). */
static int32_t try_take(kmutex *m, uint32_t me, int32_t n) {
    const int32_t c = get_count(m);
    if (c == 0) { set_state(m, n, me); return 1; }
    if (get_owner(m) != me) return 0;
    if (!(m->attr & ATTR_RECURSIVE)) return (int32_t)E(m, KERR_MUTEX_RECURSIVE, KERR_LWMUTEX_RECURSIVE);
    if (c + n < c) return (int32_t)E(m, KERR_MUTEX_LOCK_OVERFLOW, KERR_LWMUTEX_LOCK_OVERFLOW);
    set_state(m, c + n, me);
    return 1;
}

static int retry(khbbs_wait *w) {                  /* p = { count } */
    kmutex *m = find(w->id, w->type == KW_LWMUTEX);
    if (!m) { w->result = (int32_t)KERR_WAIT_DELETE; return 1; }
    const int32_t r = try_take(m, khbbs_current()->uid, (int32_t)w->p[0]);
    if (r == 0) return 0;
    w->result = r == 1 ? 0 : r;
    return 1;
}

static uint32_t check_count(const kmutex *m, int32_t n) {
    if (n <= 0 || (n > 1 && !(m->attr & ATTR_RECURSIVE))) return KERR_ILLEGAL_COUNT;
    return 0;
}

/* Lock: mode 0 waits, 1 waits delivering callbacks, 2 only tries. */
static void do_lock(kmutex *m, uint32_t not_found, int32_t n, uint32_t tp, int mode) {
    if (!m) KRET(not_found);
    const uint32_t bad = check_count(m, n);
    if (bad) KRET(bad);
    const int32_t r = try_take(m, khbbs_current()->uid, n);
    if (r == 1) KRET(0);
    if (r < 0) KRET(r);
    if (mode == 2) KRET(E(m, KERR_MUTEX_TRYLOCK_FAILED, KERR_LWMUTEX_TRYLOCK_FAILED));
    khbbs_wait w = { .type = m->lw ? KW_LWMUTEX : KW_MUTEX, .id = m->uid, .cb = mode == 1, .timeout_ptr = tp,
                     .deadline = khbbs_deadline(tp), .p = { (uint32_t)n }, .retry = retry };
    add_waiting(m, 1);
    const int32_t res = khbbs_sched_wait(&w);
    kmutex *still = find(w.id, w.type == KW_LWMUTEX);
    if (still) add_waiting(still, -1);
    KRET(res);
}

static void do_unlock(kmutex *m, uint32_t not_found, int32_t n) {
    if (!m) KRET(not_found);
    if (n <= 0) KRET(KERR_ILLEGAL_COUNT);
    const uint32_t me = khbbs_current()->uid;
    const int32_t c = get_count(m);
    if (c == 0 || get_owner(m) != me) KRET(E(m, KERR_MUTEX_NOT_LOCKED, KERR_LWMUTEX_NOT_LOCKED));
    if (n > c) KRET(E(m, KERR_MUTEX_UNLOCK_UNDERFLOW, KERR_LWMUTEX_UNLOCK_UNDERFLOW));
    set_state(m, c - n, c - n ? me : 0);
    if (c - n == 0) {
        khbbs_thread *q[KHBBS_MAX_THREADS];
        if (khbbs_waiters(m->lw ? KW_LWMUTEX : KW_MUTEX, m->uid, (m->attr & ATTR_PRIORITY) != 0, q) > 0) {
            set_state(m, (int32_t)q[0]->wait->p[0], q[0]->uid);   /* straight to the first waiter */
            khbbs_sched_wake(q[0], 0);
        }
    }
    KRET_PREEMPT(0);
}

static kmutex *new_mutex(int lw) {
    for (int i = 0; i < MAX_MUTEXES; i++)
        if (!g_mx[i].used) { memset(&g_mx[i], 0, sizeof g_mx[i]); g_mx[i].used = 1; g_mx[i].lw = lw; return &g_mx[i]; }
    return NULL;
}

static void hle_CreateMutex(void) {
    KENTER();
    const uint32_t attr = psp_arg(1);
    const int32_t init = (int32_t)psp_arg(2);
    if (init < 0 || (init > 1 && !(attr & ATTR_RECURSIVE))) KRET(KERR_ILLEGAL_COUNT);
    kmutex *m = new_mutex(0);
    if (!m) KRET(KERR_NO_MEMORY);
    m->uid = khbbs_uid_new();
    psp_str(psp_arg(0), m->name, sizeof m->name);
    m->attr = attr;
    set_state(m, init, init ? khbbs_current()->uid : 0);
    KRET(m->uid);
}

static void delete_mutex(kmutex *m) {
    const uint32_t uid = m->uid;
    const int type = m->lw ? KW_LWMUTEX : KW_MUTEX;
    m->used = 0;
    khbbs_wake_all(type, uid, (int32_t)KERR_WAIT_DELETE);
}

static void hle_DeleteMutex(void) {
    KENTER();
    kmutex *m = find(psp_arg(0), 0);
    if (!m) KRET(KERR_MUTEX_NOT_FOUND);
    delete_mutex(m);
    KRET_PREEMPT(0);
}

static void hle_LockMutex(void)    { KENTER(); do_lock(find(psp_arg(0), 0), KERR_MUTEX_NOT_FOUND, (int32_t)psp_arg(1), psp_arg(2), 0); }
static void hle_LockMutexCB(void)  { KENTER(); do_lock(find(psp_arg(0), 0), KERR_MUTEX_NOT_FOUND, (int32_t)psp_arg(1), psp_arg(2), 1); }
static void hle_TryLockMutex(void) { KENTER(); do_lock(find(psp_arg(0), 0), KERR_MUTEX_NOT_FOUND, (int32_t)psp_arg(1), 0, 2); }
static void hle_UnlockMutex(void)  { KENTER(); do_unlock(find(psp_arg(0), 0), KERR_MUTEX_NOT_FOUND, (int32_t)psp_arg(1)); }

static void hle_CreateLwMutex(void) {
    KENTER();
    const uint32_t work = psp_arg(0), attr = psp_arg(2);
    const int32_t init = (int32_t)psp_arg(3);
    if (init < 0 || (init > 1 && !(attr & ATTR_RECURSIVE))) KRET(KERR_ILLEGAL_COUNT);
    kmutex *m = new_mutex(1);
    if (!m) KRET(KERR_NO_MEMORY);
    m->uid = khbbs_uid_new();
    m->work = work;
    m->attr = attr;
    psp_str(psp_arg(1), m->name, sizeof m->name);
    for (uint32_t off = 0; off < 0x20u; off += 4) psp_write32(work + off, 0);
    psp_write32(work + LW_ATTR, attr);
    psp_write32(work + LW_UID, m->uid);
    set_state(m, init, init ? khbbs_current()->uid : 0);
    KRET(0);
}

static void hle_DeleteLwMutex(void) {
    KENTER();
    kmutex *m = find_lw(psp_arg(0));
    if (!m) KRET(KERR_LWMUTEX_NOT_FOUND);
    delete_mutex(m);
    KRET_PREEMPT(0);
}

static void hle_LockLwMutex(void)    { KENTER(); do_lock(find_lw(psp_arg(0)), KERR_LWMUTEX_NOT_FOUND, (int32_t)psp_arg(1), psp_arg(2), 0); }
static void hle_LockLwMutexCB(void)  { KENTER(); do_lock(find_lw(psp_arg(0)), KERR_LWMUTEX_NOT_FOUND, (int32_t)psp_arg(1), psp_arg(2), 1); }
static void hle_TryLockLwMutex(void) { KENTER(); do_lock(find_lw(psp_arg(0)), KERR_LWMUTEX_NOT_FOUND, (int32_t)psp_arg(1), 0, 2); }
static void hle_UnlockLwMutex(void)  { KENTER(); do_unlock(find_lw(psp_arg(0)), KERR_LWMUTEX_NOT_FOUND, (int32_t)psp_arg(1)); }

void khbbs_mutex_register(void) {
    const char *L = "ThreadManForUser";
    KREG(L, sceKernelCreateMutex, hle_CreateMutex);
    KREG(L, sceKernelDeleteMutex, hle_DeleteMutex);
    KREG(L, sceKernelLockMutex, hle_LockMutex);
    KREG(L, sceKernelLockMutexCB, hle_LockMutexCB);
    KREG(L, sceKernelTryLockMutex, hle_TryLockMutex);
    KREG(L, sceKernelUnlockMutex, hle_UnlockMutex);
    KREG(L, sceKernelCreateLwMutex, hle_CreateLwMutex);
    KREG(L, sceKernelDeleteLwMutex, hle_DeleteLwMutex);
    KREG("Kernel_Library", sceKernelLockLwMutex, hle_LockLwMutex);
    KREG("Kernel_Library", sceKernelLockLwMutexCB, hle_LockLwMutexCB);
    KREG("Kernel_Library", sceKernelTryLockLwMutex_600, hle_TryLockLwMutex);
    KREG("Kernel_Library", sceKernelUnlockLwMutex, hle_UnlockLwMutex);
}
```

The `retry` of a CB wait runs on the waiting thread, so `khbbs_current()` there is the waiter.

In `runtime/kernel/kernel.c`: include `"mutex.h"`; add `khbbs_mutex_reset();` after `khbbs_mbx_reset();` and `khbbs_mutex_register();` to the registration. Add `kernel/mutex.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_mutex: ok`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/mutex.h runtime/kernel/mutex.c runtime/kernel/kernel.c runtime/CMakeLists.txt \
    tests/runtime/test_mutex.c tests/runtime/CMakeLists.txt
git commit -m "kernel: mutexes and lightweight mutexes (work area as PPSSPP shows it)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 14: The `thread` field in khbbs's trace

`trace_diff.py` aligns per thread only when every record of both traces has a `thread` (`tools/oracle/trace_diff.py:288`). Names follow `capture_hle.py`'s rule (Decision 14); `khbbs_thread_alloc` already computes them (Task 5).

**Files:**
- Modify: `runtime/trace.c`, `runtime/include/khbbs/trace.h`
- Modify: `tests/runtime/test_trace.c`, `tests/runtime/CMakeLists.txt`
- Modify: `docs/oracle.md` ("Comparing against khbbs")

`tests/tools/` has no khbbs-format fixture to update: `test_trace_diff.py` builds its own records with `rec()` and `test_oracle.py`'s fake khbbs writes `{}` (checked with `grep -l ret_hi tests/tools/*.py`: only `test_capture_hle.py` and `test_trace_diff.py`, neither reads khbbs output).

- [ ] **Step 1: Write the failing test**

In `tests/runtime/test_trace.c`:
- in `want1` and `want2`, replace the closing `"\"ret_hi\":\"0x00000001\"}\n"` with `"\"ret_hi\":\"0x00000001\",\"thread\":null}\n"` (calls made with no run have no thread);
- add `#include "kcall.h"`, `#include "kernel/sched.h"` and, before `main`:

```c
/* Calls made on guest threads carry the thread's name, NAME#k for the k-th
 * thread with a taken name (idle0, idle1 and root are counted first). */
static void named(void) { kcall(0x109F50BCu, 0); }       /* the fake sceIoOpen */
static void spawn_named(void) {
    const char *names[] = { "worker", "worker", "root", "idle0", "a\"b\\c" };
    for (int i = 0; i < 5; i++) {
        uint32_t t = kcall(NID_sceKernelCreateThread, 6, gstr(0x08100000u, names[i]), 0x08805000u, 0x10u, 0x1000u, 0u, 0u);
        kcall(NID_sceKernelStartThread, 3, t, 0u, 0u);
    }
    kcall(0x109F50BCu, 0);
}
```

- at the start of `main`, replace `psp_cpu_reset();` with `CHECK(ktest_init() == 0, "init"); psp_cpu_reset();` (the fake `sceIoOpen` registered next replaces ours for this test);
- before `remove("t_trace.jsonl");` at the end, add:

```c
    psp_register(0x08805000u, named);
    psp_register(0x08806000u, spawn_named);
    CHECK(khbbs_trace_open("t_trace.jsonl") == 0, "open for the threaded run");
    CHECK(ktest_boot(0x08806000u, NULL).why == KHBBS_RETURNED, "threaded run");
    CHECK(khbbs_trace_close() == 0, "close 3");
    char big[8192] = {0};
    f = fopen("t_trace.jsonl", "rb");
    if (f) { big[fread(big, 1, sizeof big - 1, f)] = 0; fclose(f); }
    const char *want_threads[] = { "\"thread\":\"worker\"}", "\"thread\":\"worker#2\"}", "\"thread\":\"root#2\"}",
                                   "\"thread\":\"idle0#2\"}", "\"thread\":\"a\\\"b\\\\c\"}", "\"thread\":\"root\"}" };
    for (int i = 0; i < 6; i++) CHECK(strstr(big, want_threads[i]) != NULL, want_threads[i]);
```

- in `tests/runtime/CMakeLists.txt`, delete the `test_trace` block and add `khbbs_kernel_test(trace)` to the threaded tests.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R trace --output-on-failure`
Expected: FAIL on "first line exact" (no `thread` field yet).

- [ ] **Step 3: Implement**

In `runtime/trace.c`, include `"kernel/sched.h"` and `<string.h>`, and replace the block that writes `ret`/`ret_hi` with:

```c
    if (n > 0 && (size_t)n < sizeof buf) {
        int m = snprintf(buf + n, sizeof buf - (size_t)n,
                         "],\"ret\":\"0x%08X\",\"ret_hi\":\"0x%08X\",\"thread\":", ret_lo, ret_hi);
        n = m < 0 ? m : n + m;
    }
    if (n > 0 && (size_t)n < sizeof buf) {
        const char *t = khbbs_current_trace_name();
        int m = t ? json_string(buf + n, sizeof buf - (size_t)n, t)
                  : snprintf(buf + n, sizeof buf - (size_t)n, "null");
        n = m < 0 ? m : n + m;
    }
    if (n > 0 && (size_t)n < sizeof buf) {
        int m = snprintf(buf + n, sizeof buf - (size_t)n, "}\n");
        n = m < 0 ? m : n + m;
    }
```

and add before `on_call`:

```c
/* s as a JSON string ("..." with ", \ and control or non-ASCII bytes
 * escaped). Thread names are the game's bytes, unlike function names.
 * Returns the length written, or -1 if it does not fit. */
static int json_string(char *out, size_t cap, const char *s) {
    size_t n = 0;
    if (cap < 3) return -1;
    out[n++] = '"';
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        char esc[8];
        int k;
        if (*p == '"' || *p == '\\') k = snprintf(esc, sizeof esc, "\\%c", *p);
        else if (*p < 0x20 || *p >= 0x7F) k = snprintf(esc, sizeof esc, "\\u%04X", *p);
        else { esc[0] = (char)*p; esc[1] = 0; k = 1; }
        if (n + (size_t)k + 2 > cap) return -1;
        memcpy(out + n, esc, (size_t)k);
        n += (size_t)k;
    }
    out[n++] = '"';
    out[n] = 0;
    return (int)n;
}
```

In `runtime/include/khbbs/trace.h`, change the format line to `{"seq":N,"nid":"0x...","name":"..."|null,"ra":"0x...","args":[8 x "0x..."],"ret":"0x...","ret_hi":"0x...","thread":"NAME"|null}` and add a bullet: "- thread is the calling guest thread's name, `NAME#k` for the k-th thread (k ≥ 2) created with a name already used, counting `idle0`, `idle1` and `root` first, as `tools/oracle/capture_hle.py` names PPSSPP's threads; interrupt handlers' calls carry the interrupted thread (`idle0` when nothing else ran); null outside a run."

In `docs/oracle.md`, "Pairing calls and returns", replace the sentence "`khbbs` must name its threads by the same rule once it has threads (P4), or per-thread comparison pairs the wrong streams." with "`khbbs` names its threads by the same rule (`runtime/kernel/sched.c`, `count_name`), so both traces have per-thread streams."

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_trace: ok`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add runtime/trace.c runtime/include/khbbs/trace.h tests/runtime/test_trace.c tests/runtime/CMakeLists.txt docs/oracle.md
git commit -m "trace: each record names its thread (NAME#k, as capture_hle does)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Boot-path firmware outside the kernel (pulled forward from P5)

**Confirm the scope change (Decision 11) with the user before starting this task.**

**Files:**
- Create: `runtime/kernel/fw.c`, `runtime/kernel/fw.h`
- Modify: `runtime/kernel/kernel.c`, `runtime/CMakeLists.txt`
- Create: `tests/runtime/test_fw.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics:**

| Function | Arguments | Returns | Blocks | Notes |
|---|---|---|---|---|
| `sceUmdCheckMedium` | — | 1 | no | a disc is always in (observed 1) |
| `sceUmdActivate` | mode, drive name | 0 | no; wakes drive-state waiters whose bits now match; may preempt | drive state becomes `0x32` (PRESENT 0x02, INITED 0x10, READY 0x20; observed) |
| `sceUmdGetDriveStat` | — | the drive state: `0x12` before activation (UNVERIFIED), `0x32` after (observed) | no | |
| `sceUmdWaitDriveStat` | stat bits | 0 | until `state & stat` | `WAIT_CANCEL` if cancelled |
| `sceUmdWaitDriveStatWithTimer` | stat, timeout µs (0 = none; UNVERIFIED) | 0 | as above | `WAIT_TIMEOUT` |
| `sceUmdWaitDriveStatCB` | stat, timeout µs | 0 | as above, delivering callbacks | `WAIT_TIMEOUT` |
| `sceUmdCancelWaitDriveStat` | — | 0 | no; every drive-state waiter gets `WAIT_CANCEL` | |
| `scePowerRegisterCallback` | slot (-1 = first free), callback uid | the slot | no | notifies the callback with `0x10E4` (observed; it runs at the owner's next `*CB` wait); `UNKNOWN_CBID`; `ILLEGAL_ARGUMENT` for a slot outside -1..15 or taken (UNVERIFIED) |
| `scePowerUnregisterCallback` | slot | 0 | no | `ILLEGAL_ARGUMENT` (UNVERIFIED) |
| `scePowerIsPowerOnline` | — | 1 | no | observed |
| `scePowerGetPllClockFrequencyInt` | — | 222 | no | observed |
| `scePowerGetBatteryLifePercent` | — | 100 | no | consistent with `0x10E4`'s 0x64 |
| `sceImposeGetLanguageMode` | int *lang, int *button | 0; writes 1, 1 (UNVERIFIED; the game then sets 1, 1) | no | |
| `sceImposeSetLanguageMode` | lang, button | 0 | no | stored for Get |
| `sceKernelMemcpy` (Kernel_Library) | dst, src, n | dst | no | overlapping copies behave as memmove |
| `sceKernelMemset` (Kernel_Library) | dst, c, n | dst | no | |
| `sceKernelDcacheWritebackInvalidateRange`, `sceKernelIcacheInvalidateRange` (UtilsForUser) | addr, size | 0 (void) | no | no caches to maintain |
| `0x35669D4C` (SysMemUserForUser, unnamed) | the SDK version | 0 | no | crt0's SDK-version setter (observed 0) |

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_fw.c`:

```c
/* The boot path's firmware outside the kernel: UMD drive state, power,
 * impose, Kernel_Library memcpy/memset, cache no-ops, the crt0 NID. */
#include "kcall.h"
#include "kernel/errors.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)
#define CALL0(name) kcall(NID_##name, 0)

static char order[16];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }
static uint32_t spawn(uint32_t entry, uint32_t prio) {
    uint32_t t = CALL(sceKernelCreateThread, 6, gstr(0x08100000u, "t"), entry, prio, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, t, 0u, 0u);
    return t;
}

static uint32_t g_res;
static void umd_waiter(void) { g_res = CALL(sceUmdWaitDriveStat, 1, 0x20u); mark('w'); }
static void umd_never(void)  { g_res = CALL(sceUmdWaitDriveStat, 1, 0x01u); mark('n'); }
static int cb_ran;
static uint32_t cb_args[3];
static void cb(void) {
    cb_ran++;
    cb_args[0] = psp_cpu.r[PSP_REG_A0]; cb_args[1] = psp_cpu.r[PSP_REG_A1]; cb_args[2] = psp_cpu.r[PSP_REG_A2];
    psp_cpu.r[PSP_REG_V0] = 0;
}

static void t_umd(void) {
    CHECK(CALL0(sceUmdCheckMedium) == 1, "umd: a disc is in");
    CHECK(CALL0(sceUmdGetDriveStat) == 0x12u, "umd: present and inited before activation");
    CHECK(CALL(sceUmdWaitDriveStatWithTimer, 2, 0x20u, 1000u) == KERR_WAIT_TIMEOUT, "umd: timer runs out");
    uint32_t w = spawn(0x08805000u, 0x10);                  /* waits for READY */
    mark('r');
    CHECK(CALL(sceUmdActivate, 2, 1u, gstr(0x08100100u, "disc0:")) == 0, "umd: activate");
    mark('R');
    CHECK(g_res == 0 && strcmp(order, "rwR") == 0, "umd: the waiter woke inside Activate");
    CHECK(CALL0(sceUmdGetDriveStat) == 0x32u, "umd: 0x32 once activated");
    CHECK(CALL(sceUmdWaitDriveStat, 1, 0x20u) == 0, "umd: already ready: returns at once");
    uint32_t n = spawn(0x08805100u, 0x10);                  /* waits for NOT_PRESENT: never */
    CHECK(CALL0(sceUmdCancelWaitDriveStat) == 0, "umd: cancel");
    CALL(sceKernelWaitThreadEnd, 2, n, 0u);
    CHECK(g_res == KERR_WAIT_CANCEL, "umd: the cancelled waiter got WAIT_CANCEL");
    uint32_t c = CALL(sceKernelCreateCallback, 3, gstr(0x08100000u, "cb"), 0x08805200u, 0u);
    CALL(sceKernelNotifyCallback, 2, c, 5u);
    CHECK(CALL(sceUmdWaitDriveStatCB, 2, 0x01u, 500u) == KERR_WAIT_TIMEOUT && cb_ran == 1,
          "umd: the CB wait ran the callback, then timed out");
    CALL(sceKernelWaitThreadEnd, 2, w, 0u);
}

static void t_power(void) {
    cb_ran = 0;
    uint32_t c = CALL(sceKernelCreateCallback, 3, gstr(0x08100000u, "power"), 0x08805200u, 0x77u);
    CHECK(CALL(scePowerRegisterCallback, 2, 0xFFFFFFFFu, c) == 0, "power: first free slot is 0");
    CHECK(cb_ran == 0, "power: notified, not yet run");
    CALL(sceKernelDelayThreadCB, 1, 10u);
    CHECK(cb_ran == 1 && cb_args[0] == 1 && cb_args[1] == 0x10E4u && cb_args[2] == 0x77u,
          "power: run at the next CB wait with (1, 0x10E4, common)");
    CHECK(CALL(scePowerRegisterCallback, 2, 0xFFFFFFFFu, c) == 1, "power: next free slot is 1");
    CHECK(CALL(scePowerRegisterCallback, 2, 0xFFFFFFFFu, 0x7FFFFFFFu) == KERR_UNKNOWN_CBID, "power: unknown callback");
    CHECK(CALL(scePowerUnregisterCallback, 1, 0u) == 0, "power: unregister");
    CHECK(CALL0(scePowerIsPowerOnline) == 1 && CALL0(scePowerGetPllClockFrequencyInt) == 222 &&
          CALL0(scePowerGetBatteryLifePercent) == 100, "power: online, 222 MHz, 100%");
}

static void t_misc(void) {
    CHECK(CALL(sceImposeGetLanguageMode, 2, 0x08100200u, 0x08100204u) == 0 &&
          psp_read32(0x08100200u) == 1 && psp_read32(0x08100204u) == 1, "impose: 1, 1");
    CALL(sceImposeSetLanguageMode, 2, 2u, 0u);
    CALL(sceImposeGetLanguageMode, 2, 0x08100200u, 0x08100204u);
    CHECK(psp_read32(0x08100200u) == 2 && psp_read32(0x08100204u) == 0, "impose: what was set");
    gstr(0x08100300u, "0123456789ABCDEF");
    CHECK(CALL(sceKernelMemcpy, 3, 0x08100400u, 0x08100300u, 16u) == 0x08100400u &&
          memcmp(psp_mem_ptr(0x08100400u, 16), "0123456789ABCDEF", 16) == 0, "memcpy");
    CHECK(CALL(sceKernelMemcpy, 3, 0x08100302u, 0x08100300u, 8u) == 0x08100302u &&
          memcmp(psp_mem_ptr(0x08100300u, 10), "0101234567", 10) == 0, "memcpy overlapping: as memmove");
    CHECK(CALL(sceKernelMemset, 3, 0x08100400u, 0xABu, 4u) == 0x08100400u && psp_read32(0x08100400u) == 0xABABABABu,
          "memset");
    CHECK(CALL(sceKernelDcacheWritebackInvalidateRange, 2, 0x08100000u, 64u) == 0 &&
          CALL(sceKernelIcacheInvalidateRange, 2, 0x08100000u, 64u) == 0, "cache no-ops");
    CHECK(kcall(NIDX_crt0_sdk_version, 1, 0x06020010u) == 0, "crt0's SDK-version call returns 0");
}

static void t_exit(void) { CALL0(sceKernelExitGame); mark('X'); }

int main(void) {
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, umd_waiter);
    psp_register(0x08805100u, umd_never);
    psp_register(0x08805200u, cb);
    psp_register(0x08806000u, t_umd);
    psp_register(0x08806100u, t_power);
    psp_register(0x08806200u, t_misc);
    psp_register(0x08806300u, t_exit);
    CHECK(ktest_boot(0x08806000u, NULL).why == KHBBS_RETURNED, "umd run");
    CHECK(ktest_boot(0x08806100u, NULL).why == KHBBS_RETURNED, "power run");
    CHECK(ktest_boot(0x08806200u, NULL).why == KHBBS_RETURNED, "misc run");
    norder = 0;
    CHECK(ktest_boot(0x08806300u, NULL).why == KHBBS_EXIT_GAME && norder == 0, "ExitGame ends the run there");
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    printf("%s\n", failures ? "test_fw: FAILED" : "test_fw: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt` add `khbbs_kernel_test(fw)`.

- [ ] **Step 2: Run it and watch it fail**

Run: `cmake --build build/ci && ctest --test-dir build/ci -R fw --output-on-failure`
Expected: FAIL ("umd: a disc is in": the NID is unimplemented and returns 0).

- [ ] **Step 3: Implement**

`runtime/kernel/fw.h`:

```c
/* The boot path's firmware outside the kernel (pulled forward from P5).
 * Internal; lock held. */
#ifndef KHBBS_KERNEL_FW_H
#define KHBBS_KERNEL_FW_H

void khbbs_fw_reset(void);
void khbbs_fw_register(void);

#endif
```

`runtime/kernel/fw.c`:

```c
/* sceUmdUser, scePower, sceImpose, Kernel_Library memcpy/memset, cache
 * no-ops and crt0's SDK-version call. Plan P4 Task 15: only what KH BBS's
 * boot calls before its first sceGeListEnQueue; P5 owns the rest. */
#include "fw.h"
#include "callback.h"
#include "khle.h"
#include "ktime.h"

#include <string.h>

#define UMD_NOT_PRESENT 0x01u
#define UMD_PRESENT     0x02u
#define UMD_INITED      0x10u
#define UMD_READY       0x20u
#define POWER_SLOTS     16
#define POWER_INFO      0x000010E4u   /* AC power 0x1000 | battery present 0x80 | 100% 0x64: observed */

static uint32_t g_umd;
static uint32_t g_power[POWER_SLOTS];
static uint32_t g_lang, g_button;

void khbbs_fw_reset(void) {
    g_umd = UMD_PRESENT | UMD_INITED;   /* before sceUmdActivate: UNVERIFIED */
    memset(g_power, 0, sizeof g_power);
    g_lang = 1;
    g_button = 1;
}

/* ---- sceUmdUser ----------------------------------------------------------- */

static void hle_UmdCheckMedium(void)  { KENTER(); KRET(1); }
static void hle_UmdGetDriveStat(void) { KENTER(); KRET(g_umd); }

static void wake_umd(void) {
    khbbs_thread *q[KHBBS_MAX_THREADS];
    const int n = khbbs_waiters(KW_UMD, 0, 0, q);
    for (int i = 0; i < n; i++)
        if (g_umd & q[i]->wait->p[0]) khbbs_sched_wake(q[i], 0);
}

static void hle_UmdActivate(void) {
    KENTER();
    g_umd |= UMD_READY;
    wake_umd();
    KRET_PREEMPT(0);
}

static int umd_retry(khbbs_wait *w) {
    if (!(g_umd & w->p[0])) return 0;
    w->result = 0;
    return 1;
}

static void wait_umd(uint32_t stat, uint32_t timeout_us, int cb) {
    if (g_umd & stat) KRET(0);
    khbbs_wait w = { .type = KW_UMD, .cb = cb, .p = { stat }, .retry = umd_retry,
                     .deadline = timeout_us ? khbbs_now() + timeout_us : KHBBS_NO_DEADLINE };
    KRET(khbbs_sched_wait(&w));
}
static void hle_UmdWaitDriveStat(void)          { KENTER(); wait_umd(psp_arg(0), 0, 0); }
static void hle_UmdWaitDriveStatWithTimer(void) { KENTER(); wait_umd(psp_arg(0), psp_arg(1), 0); }
static void hle_UmdWaitDriveStatCB(void)        { KENTER(); wait_umd(psp_arg(0), psp_arg(1), 1); }

static void hle_UmdCancelWaitDriveStat(void) {
    KENTER();
    khbbs_wake_all(KW_UMD, 0, (int32_t)KERR_WAIT_CANCEL);
    KRET_PREEMPT(0);
}

/* ---- scePower ------------------------------------------------------------- */

static void hle_PowerRegisterCallback(void) {
    KENTER();
    int32_t slot = (int32_t)psp_arg(0);
    const uint32_t cb = psp_arg(1);
    if (slot == -1) {
        for (slot = 0; slot < POWER_SLOTS && g_power[slot]; slot++) {}
    }
    if (slot < 0 || slot >= POWER_SLOTS || g_power[slot]) KRET(KERR_ILLEGAL_ARGUMENT);
    const uint32_t r = khbbs_cb_notify(cb, POWER_INFO);
    if (r) KRET(r);
    g_power[slot] = cb;
    KRET_PREEMPT((uint32_t)slot);
}

static void hle_PowerUnregisterCallback(void) {
    KENTER();
    const uint32_t slot = psp_arg(0);
    if (slot >= POWER_SLOTS) KRET(KERR_ILLEGAL_ARGUMENT);
    g_power[slot] = 0;
    KRET(0);
}

static void hle_PowerIsPowerOnline(void)       { KENTER(); KRET(1); }
static void hle_PowerGetPllClockFrequency(void) { KENTER(); KRET(222); }
static void hle_PowerGetBatteryLifePercent(void) { KENTER(); KRET(100); }

/* ---- sceImpose ------------------------------------------------------------ */

static void hle_ImposeGetLanguageMode(void) {
    KENTER();
    if (psp_arg(0)) psp_write32(psp_arg(0), g_lang);
    if (psp_arg(1)) psp_write32(psp_arg(1), g_button);
    KRET(0);
}

static void hle_ImposeSetLanguageMode(void) {
    KENTER();
    g_lang = psp_arg(0);
    g_button = psp_arg(1);
    KRET(0);
}

/* ---- Kernel_Library, UtilsForUser, crt0 ------------------------------------- */

static void hle_Memcpy(void) {
    KENTER();
    const uint32_t dst = psp_arg(0), src = psp_arg(1), n = psp_arg(2);
    void *d = psp_mem_ptr(dst, n);
    const void *s = psp_mem_ptr(src, n);
    if (d && s) memmove(d, s, n);
    else if (dst <= src) for (uint32_t i = 0; i < n; i++) psp_write8(dst + i, psp_read8(src + i));
    else for (uint32_t i = n; i-- > 0;) psp_write8(dst + i, psp_read8(src + i));
    KRET(dst);
}

static void hle_Memset(void) {
    KENTER();
    const uint32_t dst = psp_arg(0), n = psp_arg(2);
    void *d = psp_mem_ptr(dst, n);
    if (d) memset(d, (int)(psp_arg(1) & 0xFFu), n);
    else for (uint32_t i = 0; i < n; i++) psp_write8(dst + i, (uint8_t)psp_arg(1));
    KRET(dst);
}

static void hle_CacheNop(void) { KENTER(); KRET(0); }
static void hle_Crt0SdkVersion(void) { KENTER(); KRET(0); }

void khbbs_fw_register(void) {
    KREG("sceUmdUser", sceUmdCheckMedium, hle_UmdCheckMedium);
    KREG("sceUmdUser", sceUmdActivate, hle_UmdActivate);
    KREG("sceUmdUser", sceUmdGetDriveStat, hle_UmdGetDriveStat);
    KREG("sceUmdUser", sceUmdWaitDriveStat, hle_UmdWaitDriveStat);
    KREG("sceUmdUser", sceUmdWaitDriveStatWithTimer, hle_UmdWaitDriveStatWithTimer);
    KREG("sceUmdUser", sceUmdWaitDriveStatCB, hle_UmdWaitDriveStatCB);
    KREG("sceUmdUser", sceUmdCancelWaitDriveStat, hle_UmdCancelWaitDriveStat);
    KREG("scePower", scePowerRegisterCallback, hle_PowerRegisterCallback);
    KREG("scePower", scePowerUnregisterCallback, hle_PowerUnregisterCallback);
    KREG("scePower", scePowerIsPowerOnline, hle_PowerIsPowerOnline);
    KREG("scePower", scePowerGetPllClockFrequencyInt, hle_PowerGetPllClockFrequency);
    KREG("scePower", scePowerGetBatteryLifePercent, hle_PowerGetBatteryLifePercent);
    KREG("sceImpose", sceImposeGetLanguageMode, hle_ImposeGetLanguageMode);
    KREG("sceImpose", sceImposeSetLanguageMode, hle_ImposeSetLanguageMode);
    KREG("Kernel_Library", sceKernelMemcpy, hle_Memcpy);
    KREG("Kernel_Library", sceKernelMemset, hle_Memset);
    KREG("UtilsForUser", sceKernelDcacheWritebackInvalidateRange, hle_CacheNop);
    KREG("UtilsForUser", sceKernelIcacheInvalidateRange, hle_CacheNop);
    psp_hle_register_unnamed(NIDX_crt0_sdk_version, "SysMemUserForUser", hle_Crt0SdkVersion);
}
```

In `runtime/kernel/kernel.c`: include `"fw.h"`; add `khbbs_fw_reset();` after `khbbs_mutex_reset();` and `khbbs_fw_register();` to the registration. Add `kernel/fw.c` to `runtime/CMakeLists.txt`.

- [ ] **Step 4: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_fw: ok`.

- [ ] **Step 5: Commit**

```bash
tools/check_no_assets.sh
git add runtime/kernel/fw.h runtime/kernel/fw.c runtime/kernel/kernel.c runtime/CMakeLists.txt \
    tests/runtime/test_fw.c tests/runtime/CMakeLists.txt
git commit -m "kernel: sceUmd drive state, scePower, sceImpose, memcpy/memset, cache no-ops (boot path, from P5)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Read-only ISO file layer and IoFileMgr with async reads

**Files:**
- Create: `runtime/iso9660.c`, `runtime/include/khbbs/iso9660.h`, `runtime/kernel/io.c`, `runtime/kernel/io.h`
- Modify: `runtime/kernel/errors.h` (EIO), `runtime/kernel/kernel.c`, `runtime/CMakeLists.txt`, `hosts/pc/main.c`
- Create: `tests/runtime/isogen.h`, `tests/runtime/test_iso9660.c`, `tests/runtime/test_io.c`; Modify: `tests/runtime/CMakeLists.txt`

**Semantics.** Devices `disc0:`, `umd0:`, `umd1:` read the ISO given with `khbbs --iso`. A path after the device (`disc0:/PSP_GAME/...`, case-insensitive, `;1` ignored) opens a file, positions and sizes in bytes; `sce_lbn0x<lba>_size0x<bytes>` after the device opens that byte range. The device alone (`umd1:`, as `DiscInfoBind` does) opens the whole disc raw: positions, `Read` sizes and results are in 2,048-byte sectors (UNVERIFIED for `umd0:`/`disc0:`; observed for `umd1:` through `sceIoLseek(3, 0xE2B0)` = `0xE2B0` and 1-sector reads). Every other device answers `KERR_ERRNO_ENOENT` (P5 adds `ms0:`). File descriptors start at 3 (observed). Async reads complete 1,000 µs of guest time after they are issued (UNVERIFIED), copying at completion.

| Function | Arguments | Returns | Blocks | Errors |
|---|---|---|---|---|
| `sceIoOpen` | path, flags, mode | fd | no | `ENOENT` (no ISO, unknown device, no such file), `EROFS` (write flags: `flags & 3` ≠ 1, or `0x100`/`0x200`/`0x400`; UNVERIFIED), `EMFILE` (64) |
| `sceIoClose` | fd | 0 | no | `EBADF` |
| `sceIoRead` | fd, buf, size | bytes (raw: sectors) read, clamped at the end | no | `EBADF`, `EIO` |
| `sceIoWrite` | fd, buf, size | size for fd 1 (stdout) and 2 (stderr) | no | `EBADF` for every other fd |
| `sceIoLseek` | fd, (pad), offset in `$a2:$a3`, whence in `$t0` (0 set, 1 current, 2 end) | the new position, 64-bit in `$v0:$v1` (raw: sectors) | no | `EBADF`, `EINVAL` (negative result, bad whence) |
| `sceIoReadAsync` | fd, buf, size | 0 | no; completion scheduled | `EBADF`, `ASYNC_BUSY` (one pending) |
| `sceIoWaitAsync` | fd, s64 *res | 0, *res = the read's result | until the pending read completes | `EBADF`, `NOASYNC` (nothing issued since the last wait) |
| `sceIoWaitAsyncCB` | fd, res | as above | as above, delivering callbacks | as above |
| `sceIoPollAsync` | fd, res | 0 and *res if done, 1 if still pending | no | `EBADF`, `NOASYNC` |
| `sceIoGetstat` | path, SceIoStat *st | 0 | no | `ENOENT`. `SceIoStat` (0x58 bytes): `+0x00 st_mode` (`0x216D` file, `0x116D` directory), `+0x04 st_attr` (`0x25` file, `0x15` directory), `+0x08 st_size` (64-bit), `+0x10/+0x20/+0x30` ctime/atime/mtime as `ScePspDateTime` (u16 year, month, day, hour, minute, second; u32 µs) from the directory record, `+0x40 st_private[0]` = the start sector, `[1..5]` = 0. Mode, attribute and `st_private` UNVERIFIED (the gate's snapshot step checks them). |
| `sceIoIoctl` | fd, cmd, in, inlen, out, outlen | 0 for `0x04100001` (stores the 16-byte PGD key; no decryption, Decision 11) | no | `EBADF`; other commands print once and return `UNSUP` (UNVERIFIED) |
| `sceIoDevctl` | device, cmd, in, inlen, out, outlen | 0 for `0x02415821` (memory-stick insert/eject callback: the uid at `in` is stored, not notified; P5 decides) | no | other commands print once and return `UNSUP` (UNVERIFIED) |

- [ ] **Step 1: Write the failing tests**

`tests/runtime/isogen.h`:

```c
/* A synthetic 24-sector ISO 9660 image for tests (no game data):
 *   /PSP_GAME/SYSDIR/EBOOT.BIN  sector 21, "HELLO WORLD" (11 bytes)
 *   /PSP_GAME/PARAM.SFO          sectors 22-23, 3000 bytes, byte i = i & 0xFF
 * PVD at 16, terminator at 17, directories at 18 (root), 19, 20. */
#ifndef KHBBS_TEST_ISOGEN_H
#define KHBBS_TEST_ISOGEN_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void iso_both16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = p[1]; p[3] = p[0]; }
static void iso_both32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; i++) { p[i] = (uint8_t)(v >> (8 * i)); p[7 - i] = (uint8_t)(v >> (8 * i)); }
}
/* One directory record at dir + at; returns the next offset. */
static int iso_rec(uint8_t *dir, int at, uint32_t lba, uint32_t size, int isdir, const char *name, int nl) {
    const int len = 33 + nl + (nl % 2 == 0 ? 1 : 0);
    uint8_t *r = dir + at;
    memset(r, 0, (size_t)len);
    r[0] = (uint8_t)len;
    iso_both32(r + 2, lba);
    iso_both32(r + 10, size);
    r[18] = 109; r[19] = 3; r[20] = 4; r[21] = 12; r[22] = 30; r[23] = 15;   /* 2009-03-04 12:30:15 */
    r[25] = isdir ? 2 : 0;
    iso_both16(r + 28, 1);
    r[32] = (uint8_t)nl;
    memcpy(r + 33, name, (size_t)nl);
    return at + len;
}
static int isogen_write(const char *path) {
    static uint8_t img[24 * 2048];
    memset(img, 0, sizeof img);
    uint8_t *pvd = img + 16 * 2048;
    pvd[0] = 1; memcpy(pvd + 1, "CD001", 5); pvd[6] = 1;
    iso_both32(pvd + 80, 24);
    iso_both16(pvd + 128, 2048);
    iso_rec(pvd, 156, 18, 2048, 1, "\0", 1);
    uint8_t *term = img + 17 * 2048;
    term[0] = 255; memcpy(term + 1, "CD001", 5); term[6] = 1;
    uint8_t *d = img + 18 * 2048;
    int at = iso_rec(d, 0, 18, 2048, 1, "\0", 1);
    at = iso_rec(d, at, 18, 2048, 1, "\1", 1);
    iso_rec(d, at, 19, 2048, 1, "PSP_GAME", 8);
    d = img + 19 * 2048;
    at = iso_rec(d, 0, 19, 2048, 1, "\0", 1);
    at = iso_rec(d, at, 18, 2048, 1, "\1", 1);
    at = iso_rec(d, at, 22, 3000, 0, "PARAM.SFO;1", 11);
    iso_rec(d, at, 20, 2048, 1, "SYSDIR", 6);
    d = img + 20 * 2048;
    at = iso_rec(d, 0, 20, 2048, 1, "\0", 1);
    at = iso_rec(d, at, 19, 2048, 1, "\1", 1);
    iso_rec(d, at, 21, 11, 0, "EBOOT.BIN;1", 11);
    memcpy(img + 21 * 2048, "HELLO WORLD", 11);
    for (int i = 0; i < 3000; i++) img[22 * 2048 + i] = (uint8_t)i;
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    const int ok = fwrite(img, 1, sizeof img, f) == sizeof img;
    return fclose(f) == 0 && ok ? 0 : -1;
}

#endif
```

`tests/runtime/test_iso9660.c`:

```c
/* The ISO 9660 reader on a synthetic image. */
#include "isogen.h"
#include "khbbs/iso9660.h"

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)

int main(void) {
    khbbs_iso_entry e;
    CHECK(khbbs_iso_open("t_iso_missing.iso") == -1, "a missing file");
    CHECK(isogen_write("t_iso.iso") == 0, "write the image");
    CHECK(khbbs_iso_open("t_iso.iso") == 0 && khbbs_iso_is_open(), "open");
    CHECK(khbbs_iso_sectors() == 24, "volume size");
    CHECK(khbbs_iso_lookup("/PSP_GAME/SYSDIR/EBOOT.BIN", &e) == 0 && e.lba == 21 && e.size == 11 && !e.is_dir,
          "a file");
    CHECK(khbbs_iso_lookup("psp_game/sysdir/eboot.bin", &e) == 0 && e.lba == 21, "case and leading slash do not matter");
    CHECK(khbbs_iso_lookup("/PSP_GAME/SYSDIR", &e) == 0 && e.is_dir && e.lba == 20, "a directory");
    CHECK(khbbs_iso_lookup("/PSP_GAME/PARAM.SFO", &e) == 0 && e.lba == 22 && e.size == 3000 && e.date[0] == 109,
          "a two-sector file, its date");
    CHECK(khbbs_iso_lookup("", &e) == 0 && e.is_dir && e.lba == 18, "the root");
    CHECK(khbbs_iso_lookup("/PSP_GAME/NOPE", &e) == -1, "missing");
    CHECK(khbbs_iso_lookup("/PSP_GAME/SYSDIR/EBOOT.BIN/X", &e) == -1, "a file is not a directory");
    char buf[16] = {0};
    CHECK(khbbs_iso_read(21u * 2048, buf, 11) == 0 && memcmp(buf, "HELLO WORLD", 11) == 0, "read bytes");
    uint8_t two[2];
    CHECK(khbbs_iso_read(22u * 2048 + 2047, two, 2) == 0 && two[0] == 0xFF && two[1] == 0x00, "read across a sector");
    CHECK(khbbs_iso_read(24u * 2048 - 1, two, 2) == -1, "past the end");
    FILE *f = fopen("t_not_iso.bin", "wb");
    static uint8_t zeros[40000];
    if (f) { fwrite(zeros, 1, sizeof zeros, f); fclose(f); }
    CHECK(khbbs_iso_open("t_not_iso.bin") == -1 && !khbbs_iso_is_open(), "not an ISO");
    remove("t_iso.iso");
    remove("t_not_iso.bin");
    printf("%s\n", failures ? "test_iso9660: FAILED" : "test_iso9660: ok");
    return failures ? 1 : 0;
}
```

`tests/runtime/test_io.c`:

```c
/* IoFileMgr over the synthetic ISO: files, the raw device, async reads that
 * block the caller and let other threads run, Getstat, ioctl and devctl. */
#include "isogen.h"
#include "kcall.h"
#include "kernel/errors.h"
#include "khbbs/iso9660.h"

#include <stdio.h>

static int failures;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); failures++; } } while (0)
#define CALL(name, n, ...) kcall(NID_##name, n, __VA_ARGS__)

static char order[16];
static int norder;
static void mark(char c) { order[norder++] = c; order[norder] = 0; }
static void other(void) { mark('o'); }

#define P 0x08100000u
#define BUF 0x08110000u
#define RES 0x08120000u

static uint64_t lseek(uint32_t fd, uint64_t off, uint32_t whence) {
    uint32_t lo = CALL(sceIoLseek, 5, fd, 0u, (uint32_t)off, (uint32_t)(off >> 32), whence);
    return (uint64_t)psp_cpu.r[PSP_REG_V1] << 32 | lo;
}

static void t_io(void) {
    const uint32_t fd = CALL(sceIoOpen, 3, gstr(P, "disc0:/PSP_GAME/SYSDIR/EBOOT.BIN"), 1u, 0u);
    CHECK(fd == 3, "the first fd is 3");
    CHECK(CALL(sceIoRead, 3, fd, BUF, 100u) == 11 && memcmp(psp_mem_ptr(BUF, 11), "HELLO WORLD", 11) == 0,
          "read clamps at the end of the file");
    CHECK(lseek(fd, 6, 0) == 6, "lseek set");
    CHECK(lseek(fd, 0, 2) == 11, "lseek end");
    CHECK(lseek(fd, 6, 0) == 6 && CALL(sceIoRead, 3, fd, BUF, 5u) == 5 &&
          memcmp(psp_mem_ptr(BUF, 5), "WORLD", 5) == 0, "read after lseek");

    /* Async: the caller blocks in WaitAsyncCB; a lower thread runs meanwhile. */
    lseek(fd, 0, 0);
    uint32_t t = CALL(sceKernelCreateThread, 6, gstr(P + 0x100, "other"), 0x08805000u, 0x30u, 0x1000u, 0u, 0u);
    CALL(sceKernelStartThread, 3, t, 0u, 0u);
    CHECK(CALL(sceIoReadAsync, 3, fd, BUF, 5u) == 0, "ReadAsync");
    CHECK(CALL(sceIoReadAsync, 3, fd, BUF, 5u) == KERR_ASYNC_BUSY, "one at a time");
    CHECK(CALL(sceIoPollAsync, 2, fd, RES) == 1, "pending");
    mark('r');
    CHECK(CALL(sceIoWaitAsyncCB, 2, fd, RES) == 0 && psp_read32(RES) == 5 && psp_read32(RES + 4) == 0,
          "WaitAsyncCB: 5 bytes");
    mark('R');
    CHECK(strcmp(order, "roR") == 0, "the lower thread ran while we waited");
    CHECK(CALL(sceIoWaitAsync, 2, fd, RES) == KERR_NOASYNC, "nothing pending any more");
    CHECK(CALL(sceIoIoctl, 6, fd, 0x04100001u, gstr(P + 0x200, "0123456789abcdef"), 16u, 0u, 0u) == 0,
          "ioctl 0x04100001 (PGD key) accepted");
    CHECK(CALL(sceIoClose, 1, fd) == 0 && CALL(sceIoClose, 1, fd) == KERR_ERRNO_EBADF, "close, twice");

    /* The raw device: sectors. */
    const uint32_t raw = CALL(sceIoOpen, 3, gstr(P, "umd1:"), 1u, 0u);
    CHECK(raw == 3, "fd 3 is free again");
    CHECK(lseek(raw, 21, 0) == 21, "raw lseek in sectors");
    CHECK(CALL(sceIoRead, 3, raw, BUF, 1u) == 1 && memcmp(psp_mem_ptr(BUF, 11), "HELLO WORLD", 11) == 0,
          "raw read: one sector");
    CHECK(lseek(raw, 0, 1) == 22, "the position moved one sector");
    CALL(sceIoClose, 1, raw);

    /* Getstat. */
    CHECK(CALL(sceIoGetstat, 2, gstr(P, "disc0:/PSP_GAME/PARAM.SFO"), BUF) == 0, "getstat a file");
    CHECK(psp_read32(BUF) == 0x216Du && psp_read32(BUF + 4) == 0x25u && psp_read32(BUF + 8) == 3000 &&
          psp_read32(BUF + 12) == 0 && psp_read32(BUF + 0x40) == 22, "mode, attr, size, start sector");
    CHECK(psp_read16(BUF + 0x10) == 2009 && psp_read16(BUF + 0x12) == 3 && psp_read16(BUF + 0x14) == 4 &&
          psp_read16(BUF + 0x16) == 12 && psp_read16(BUF + 0x18) == 30 && psp_read16(BUF + 0x1A) == 15,
          "the date from the directory record");
    CHECK(CALL(sceIoGetstat, 2, gstr(P, "umd0:/PSP_GAME/SYSDIR"), BUF) == 0 && psp_read32(BUF) == 0x116Du,
          "getstat a directory");
    CHECK(CALL(sceIoGetstat, 2, gstr(P, "disc0:/NOPE"), BUF) == KERR_ERRNO_ENOENT, "getstat missing");

    /* LBN paths, other devices, write flags, stdout. */
    const uint32_t lbn = CALL(sceIoOpen, 3, gstr(P, "disc0:/sce_lbn0x15_size0xB"), 1u, 0u);
    CHECK(CALL(sceIoRead, 3, lbn, BUF, 64u) == 11 && memcmp(psp_mem_ptr(BUF, 11), "HELLO WORLD", 11) == 0,
          "an sce_lbn path reads that byte range");
    CALL(sceIoClose, 1, lbn);
    CHECK(CALL(sceIoOpen, 3, gstr(P, "ms0:/PSP/SAVEDATA/x"), 1u, 0u) == KERR_ERRNO_ENOENT, "ms0: is P5's");
    CHECK(CALL(sceIoOpen, 3, gstr(P, "disc0:/NOPE.BIN"), 1u, 0u) == KERR_ERRNO_ENOENT, "missing file");
    CHECK(CALL(sceIoOpen, 3, gstr(P, "disc0:/PSP_GAME/PARAM.SFO"), 0x602u, 0u) == KERR_ERRNO_EROFS, "the disc is read-only");
    CHECK(CALL(sceIoWrite, 3, 1u, gstr(P, "io: stdout ok\n"), 14u) == 14, "write to stdout");
    CHECK(CALL(sceIoDevctl, 6, gstr(P, "mscmhc0:"), 0x02415821u, RES, 4u, 0u, 0u) == 0, "devctl 0x02415821");
}

int main(void) {
    CHECK(isogen_write("t_io.iso") == 0 && khbbs_iso_open("t_io.iso") == 0, "the image");
    CHECK(ktest_init() == 0, "init");
    psp_register(0x08805000u, other);
    psp_register(0x08806000u, t_io);
    CHECK(ktest_boot(0x08806000u, NULL).why == KHBBS_RETURNED, "returned");
    CHECK(khbbs_host_posix_live_threads() == 0, "joined");
    khbbs_iso_close();
    remove("t_io.iso");
    printf("%s\n", failures ? "test_io: FAILED" : "test_io: ok");
    return failures ? 1 : 0;
}
```

In `tests/runtime/CMakeLists.txt`, add `khbbs_kernel_test(io)` to the threaded tests and, with the plain ones:

```cmake
add_executable(test_iso9660 test_iso9660.c)
target_link_libraries(test_iso9660 PRIVATE khbbs_runtime)
add_test(NAME iso9660 COMMAND test_iso9660 WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(test_iso9660 PRIVATE -Wall -Wextra)
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cmake --build build/ci`
Expected: compile error, `khbbs/iso9660.h` not found.

- [ ] **Step 3: The ISO reader**

`runtime/include/khbbs/iso9660.h`:

```c
/* A read-only ISO 9660 image (the game's UMD), through stdio. One image at a
 * time. Paths are "/A/B/C" or "A/B/C", case-insensitive, ";1" ignored. */
#ifndef KHBBS_ISO9660_H
#define KHBBS_ISO9660_H

#include <stdint.h>

typedef struct {
    uint32_t lba;        /* first sector */
    uint32_t size;       /* bytes */
    int      is_dir;
    uint8_t  date[7];    /* years since 1900, month, day, hour, minute, second, GMT offset */
} khbbs_iso_entry;

int      khbbs_iso_open(const char *path);   /* 0, or -1 (message on stderr) */
void     khbbs_iso_close(void);
int      khbbs_iso_is_open(void);
uint32_t khbbs_iso_sectors(void);            /* volume size in 2048-byte sectors */
int      khbbs_iso_lookup(const char *path, khbbs_iso_entry *out);   /* 0 or -1; "" is the root */
int      khbbs_iso_read(uint64_t offset, void *dst, uint32_t len);   /* 0, or -1 outside the image */

#endif
```

`runtime/iso9660.c`:

```c
/* ISO 9660, read-only, enough for a PSP UMD image: the primary volume
 * descriptor at sector 16 and directory records. Plan P4 Task 16. */
#include "khbbs/iso9660.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#define SECTOR 2048u

static FILE *g_f;
static uint32_t g_sectors;
static khbbs_iso_entry g_root;

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

/* fseek takes a long, which is 32 bits on some hosts: step in 1 GB pieces. */
static int seek64(uint64_t off) {
    if (fseek(g_f, 0, SEEK_SET) != 0) return -1;
    while (off > 0x40000000u) {
        if (fseek(g_f, 0x40000000L, SEEK_CUR) != 0) return -1;
        off -= 0x40000000u;
    }
    return fseek(g_f, (long)off, SEEK_CUR);
}

int khbbs_iso_read(uint64_t offset, void *dst, uint32_t len) {
    if (!g_f || offset + len > (uint64_t)g_sectors * SECTOR) return -1;
    if (seek64(offset) != 0) return -1;
    return fread(dst, 1, len, g_f) == len ? 0 : -1;
}

static void entry_from(const uint8_t *r, khbbs_iso_entry *e) {
    e->lba = le32(r + 2);
    e->size = le32(r + 10);
    e->is_dir = (r[25] & 2) != 0;
    memcpy(e->date, r + 18, 7);
}

void khbbs_iso_close(void) {
    if (g_f) fclose(g_f);
    g_f = NULL;
    g_sectors = 0;
}

int khbbs_iso_is_open(void) { return g_f != NULL; }
uint32_t khbbs_iso_sectors(void) { return g_sectors; }

int khbbs_iso_open(const char *path) {
    khbbs_iso_close();
    g_f = fopen(path, "rb");
    if (!g_f) { fprintf(stderr, "khbbs: cannot open ISO %s\n", path); return -1; }
    uint8_t pvd[SECTOR];
    g_sectors = 17;                                   /* enough to read the descriptor */
    if (khbbs_iso_read(16u * SECTOR, pvd, SECTOR) != 0 || pvd[0] != 1 || memcmp(pvd + 1, "CD001", 5) != 0 ||
        le16(pvd + 128) != SECTOR) {
        fprintf(stderr, "khbbs: %s is not an ISO 9660 image\n", path);
        khbbs_iso_close();
        return -1;
    }
    g_sectors = le32(pvd + 80);
    entry_from(pvd + 156, &g_root);
    return 0;
}

/* The record's name without ";version" and a trailing ".", compared
 * case-insensitively with want[0..n). */
static int name_is(const uint8_t *rec, const char *want, size_t n) {
    const size_t nl = rec[32];
    const char *nm = (const char *)rec + 33;
    if (nl == 1 && (nm[0] == 0 || nm[0] == 1)) return 0;       /* "." and ".." */
    size_t m = 0;
    while (m < nl && nm[m] != ';') m++;
    if (m > 0 && nm[m - 1] == '.') m--;
    if (m != n) return 0;
    for (size_t i = 0; i < n; i++)
        if (tolower((unsigned char)nm[i]) != tolower((unsigned char)want[i])) return 0;
    return 1;
}

static int find_in(const khbbs_iso_entry *dir, const char *name, size_t n, khbbs_iso_entry *out) {
    uint8_t sec[SECTOR];
    for (uint32_t off = 0; off < dir->size; off += SECTOR) {
        if (khbbs_iso_read((uint64_t)dir->lba * SECTOR + off, sec, SECTOR) != 0) return -1;
        for (uint32_t i = 0; i < SECTOR;) {
            const uint8_t len = sec[i];
            if (len == 0) break;                      /* padding to the next sector */
            if (len < 34 || i + len > SECTOR) return -1;
            if (name_is(sec + i, name, n)) { entry_from(sec + i, out); return 0; }
            i += len;
        }
    }
    return -1;
}

int khbbs_iso_lookup(const char *path, khbbs_iso_entry *out) {
    if (!g_f) return -1;
    khbbs_iso_entry cur = g_root;
    const char *p = path;
    while (*p == '/') p++;
    while (*p) {
        const char *slash = strchr(p, '/');
        const size_t n = slash ? (size_t)(slash - p) : strlen(p);
        if (!cur.is_dir || find_in(&cur, p, n, &cur) != 0) return -1;
        p += n;
        while (*p == '/') p++;
    }
    *out = cur;
    return 0;
}
```

Add `iso9660.c` to `runtime/CMakeLists.txt`, and to `runtime/kernel/errors.h`: `#define KERR_ERRNO_EIO 0x80010005u`.

- [ ] **Step 4: IoFileMgr**

`runtime/kernel/io.h`:

```c
/* IoFileMgrForUser over the ISO. Internal; lock held. */
#ifndef KHBBS_KERNEL_IO_H
#define KHBBS_KERNEL_IO_H

void khbbs_io_reset(void);     /* closes every fd; the ISO stays open */
void khbbs_io_register(void);

#endif
```

`runtime/kernel/io.c`:

```c
/* IoFileMgrForUser over the ISO: disc0:, umd0:, umd1:. Plan P4 Task 16;
 * P5 replaces and extends it (ms0:, PGD decryption). */
#include "io.h"
#include "khle.h"
#include "ktime.h"
#include "khbbs/iso9660.h"

#include <stdio.h>
#include <string.h>

#define MAX_FD      64
#define FIRST_FD    3
#define SECTOR      2048u
#define ASYNC_US    1000u       /* UNVERIFIED: PPSSPP's latency differs; only order matters per thread */

typedef struct {
    int      used, raw;         /* raw: the whole disc; positions and sizes in sectors at the API */
    uint64_t base, size, pos;   /* bytes in the image */
    uint8_t  key[16];
    int      has_key;
    int      busy, done;        /* an async read issued / completed and not yet collected */
    uint32_t abuf, alen, timer;
    uint64_t ares;
} kfd;

static kfd g_fd[MAX_FD];
static uint32_t g_ms_cb;        /* devctl 0x02415821's callback, for P5 */

void khbbs_io_reset(void) {
    memset(g_fd, 0, sizeof g_fd);
    g_ms_cb = 0;
}

static kfd *fd_of(uint32_t fd) {
    return fd >= FIRST_FD && fd < MAX_FD && g_fd[fd].used ? &g_fd[fd] : NULL;
}

/* disc0:/umd0:/umd1: + rest. 1 and *rest set for an ISO device, else 0. */
static int iso_device(const char *path, const char **rest) {
    static const char *dev[] = { "disc0:", "umd0:", "umd1:" };
    for (int i = 0; i < 3; i++) {
        size_t n = strlen(dev[i]);
        if (strncmp(path, dev[i], n) == 0) { *rest = path + n; return 1; }
    }
    return 0;
}

/* Resolve a guest path: 0 and the byte range (raw for the bare device), or an error. */
static uint32_t resolve(const char *path, uint64_t *base, uint64_t *size, int *raw, khbbs_iso_entry *ent) {
    const char *rest;
    if (!khbbs_iso_is_open() || !iso_device(path, &rest)) return KERR_ERRNO_ENOENT;
    while (*rest == '/') rest++;
    unsigned lba, len;
    char tail;
    *raw = 0;
    if (!*rest) {
        *raw = 1;
        *base = 0;
        *size = (uint64_t)khbbs_iso_sectors() * SECTOR;
        return 0;
    }
    if (sscanf(rest, "sce_lbn0x%x_size0x%x%c", &lba, &len, &tail) == 2) {
        *base = (uint64_t)lba * SECTOR;
        *size = len;
        return 0;
    }
    if (khbbs_iso_lookup(rest, ent) != 0) return KERR_ERRNO_ENOENT;
    *base = (uint64_t)ent->lba * SECTOR;
    *size = ent->size;
    return 0;
}

/* Read up to n API units at the position; returns units read, or an error. */
static uint32_t do_read(kfd *f, uint32_t buf, uint32_t n) {
    const uint64_t want = f->raw ? (uint64_t)n * SECTOR : n;
    const uint64_t left = f->pos < f->size ? f->size - f->pos : 0;
    const uint32_t bytes = (uint32_t)(want < left ? want : left);
    void *dst = psp_mem_ptr(buf, bytes);
    if (bytes && (!dst || khbbs_iso_read(f->base + f->pos, dst, bytes) != 0)) return KERR_ERRNO_EIO;
    f->pos += bytes;
    return f->raw ? bytes / SECTOR : bytes;
}

static void hle_Open(void) {
    KENTER();
    char path[256];
    psp_str(psp_arg(0), path, sizeof path);
    const uint32_t flags = psp_arg(1);
    uint64_t base, size;
    int raw;
    khbbs_iso_entry ent;
    const uint32_t err = resolve(path, &base, &size, &raw, &ent);
    if (err) KRET(err);
    if ((flags & 3u) != 1u || (flags & 0x700u)) KRET(KERR_ERRNO_EROFS);
    for (uint32_t fd = FIRST_FD; fd < MAX_FD; fd++) {
        if (g_fd[fd].used) continue;
        g_fd[fd] = (kfd){ .used = 1, .raw = raw, .base = base, .size = size };
        KRET(fd);
    }
    KRET(KERR_ERRNO_EMFILE);
}

static void on_async(void *arg, uint64_t when) {
    (void)when;
    kfd *f = arg;
    const uint32_t fd = (uint32_t)(f - g_fd);
    f->timer = 0;
    f->ares = do_read(f, f->abuf, f->alen);
    f->busy = 0;
    f->done = 1;
    khbbs_wake_all(KW_IO, fd, 0);
}

static void hle_ReadAsync(void) {
    KENTER();
    kfd *f = fd_of(psp_arg(0));
    if (!f) KRET(KERR_ERRNO_EBADF);
    if (f->busy) KRET(KERR_ASYNC_BUSY);
    f->abuf = psp_arg(1);
    f->alen = psp_arg(2);
    f->busy = 1;
    f->done = 0;
    f->timer = khbbs_timer_add(khbbs_now() + ASYNC_US, on_async, f);
    KRET(0);
}

static int async_retry(khbbs_wait *w) { kfd *f = fd_of(w->id); w->result = 0; return !f || f->done; }

static void wait_async(int cb) {
    KENTER();
    const uint32_t fd = psp_arg(0), res = psp_arg(1);
    kfd *f = fd_of(fd);
    if (!f) KRET(KERR_ERRNO_EBADF);
    if (!f->busy && !f->done) KRET(KERR_NOASYNC);
    if (!f->done) {
        khbbs_wait w = { .type = KW_IO, .id = fd, .cb = cb, .deadline = KHBBS_NO_DEADLINE, .retry = async_retry };
        const int32_t r = khbbs_sched_wait(&w);
        if (r) KRET(r);
        f = fd_of(fd);
        if (!f) KRET(KERR_ERRNO_EBADF);
    }
    if (res) { psp_write32(res, (uint32_t)f->ares); psp_write32(res + 4, (uint32_t)(f->ares >> 32)); }
    f->done = 0;
    KRET(0);
}
static void hle_WaitAsync(void)   { wait_async(0); }
static void hle_WaitAsyncCB(void) { wait_async(1); }

static void put_date(uint32_t at, const uint8_t d[7]) {
    const uint16_t v[6] = { (uint16_t)(1900 + d[0]), d[1], d[2], d[3], d[4], d[5] };
    for (int i = 0; i < 6; i++) psp_write16(at + 2u * (uint32_t)i, v[i]);
    psp_write32(at + 12, 0);
}

static void hle_Getstat(void) {
    KENTER();
    char path[256];
    psp_str(psp_arg(0), path, sizeof path);
    const uint32_t st = psp_arg(1);
    uint64_t base, size;
    int raw;
    khbbs_iso_entry ent;
    memset(&ent, 0, sizeof ent);
    const uint32_t err = resolve(path, &base, &size, &raw, &ent);
    if (err || raw) KRET(KERR_ERRNO_ENOENT);
    uint8_t *p = psp_mem_ptr(st, 0x58);
    if (!p) KRET(KERR_ILLEGAL_ADDR);
    memset(p, 0, 0x58);
    psp_write32(st + 0x00, ent.is_dir ? 0x116Du : 0x216Du);   /* UNVERIFIED */
    psp_write32(st + 0x04, ent.is_dir ? 0x15u : 0x25u);       /* UNVERIFIED */
    psp_write32(st + 0x08, (uint32_t)size);
    psp_write32(st + 0x0C, (uint32_t)(size >> 32));
    put_date(st + 0x10, ent.date);
    put_date(st + 0x20, ent.date);
    put_date(st + 0x30, ent.date);
    psp_write32(st + 0x40, (uint32_t)(base / SECTOR));       /* st_private[0]: the start sector, UNVERIFIED */
    KRET(0);
}
```

and, in the same file after `hle_Getstat`:

```c
static void hle_Close(void) {
    KENTER();
    kfd *f = fd_of(psp_arg(0));
    if (!f) KRET(KERR_ERRNO_EBADF);
    if (f->timer) khbbs_timer_cancel(f->timer);
    memset(f, 0, sizeof *f);
    KRET(0);
}

static void hle_Read(void) {
    KENTER();
    kfd *f = fd_of(psp_arg(0));
    if (!f) KRET(KERR_ERRNO_EBADF);
    KRET(do_read(f, psp_arg(1), psp_arg(2)));
}

static void hle_Write(void) {
    KENTER();
    const uint32_t fd = psp_arg(0), n = psp_arg(2);
    FILE *out = fd == 1 ? stdout : fd == 2 ? stderr : NULL;
    if (!out) KRET(KERR_ERRNO_EBADF);
    const void *src = psp_mem_ptr(psp_arg(1), n);
    if (n && (!src || fwrite(src, 1, n, out) != n)) KRET(KERR_ERRNO_EIO);
    KRET(n);
}

static void hle_Lseek(void) {
    KENTER();
    kfd *f = fd_of(psp_arg(0));
    if (!f) KRET(KERR_ERRNO_EBADF);
    const int64_t off = (int64_t)((uint64_t)psp_arg(2) | (uint64_t)psp_arg(3) << 32);
    const uint32_t whence = psp_arg(4);
    const int64_t unit = f->raw ? SECTOR : 1;
    int64_t at;
    switch (whence) {
    case 0:  at = off * unit; break;
    case 1:  at = (int64_t)f->pos + off * unit; break;
    case 2:  at = (int64_t)f->size + off * unit; break;
    default: KRET(KERR_ERRNO_EINVAL);
    }
    if (at < 0) KRET(KERR_ERRNO_EINVAL);
    f->pos = (uint64_t)at;
    const uint64_t r = f->pos / (uint64_t)unit;
    psp_cpu.r[PSP_REG_V1] = (uint32_t)(r >> 32);
    KRET((uint32_t)r);
}

static void hle_PollAsync(void) {
    KENTER();
    kfd *f = fd_of(psp_arg(0));
    const uint32_t res = psp_arg(1);
    if (!f) KRET(KERR_ERRNO_EBADF);
    if (!f->busy && !f->done) KRET(KERR_NOASYNC);
    if (f->busy) KRET(1);
    if (res) { psp_write32(res, (uint32_t)f->ares); psp_write32(res + 4, (uint32_t)(f->ares >> 32)); }
    f->done = 0;
    KRET(0);
}

/* Unknown ioctl/devctl commands: say so once per command. */
static int first_time(uint32_t cmd) {
    static uint32_t seen[16];
    static int n;
    for (int i = 0; i < n; i++) if (seen[i] == cmd) return 0;
    if (n < 16) seen[n++] = cmd;
    return 1;
}

static void hle_Ioctl(void) {
    KENTER();
    kfd *f = fd_of(psp_arg(0));
    const uint32_t cmd = psp_arg(1), in = psp_arg(2);
    if (!f) KRET(KERR_ERRNO_EBADF);
    if (cmd == 0x04100001u) {                        /* PGD key: kept, not used (P5 decrypts) */
        const void *k = psp_mem_ptr(in, 16);
        if (k) { memcpy(f->key, k, 16); f->has_key = 1; }
        KRET(0);
    }
    if (first_time(cmd)) fprintf(stderr, "khbbs: sceIoIoctl 0x%08X not implemented\n", cmd);
    KRET(KERR_UNSUP);
}

static void hle_Devctl(void) {
    KENTER();
    const uint32_t cmd = psp_arg(1), in = psp_arg(2);
    if (cmd == 0x02415821u) {                        /* memory stick insert/eject callback: kept for P5 */
        g_ms_cb = in ? psp_read32(in) : 0;
        KRET(0);
    }
    if (first_time(cmd)) fprintf(stderr, "khbbs: sceIoDevctl 0x%08X not implemented\n", cmd);
    KRET(KERR_UNSUP);
}

void khbbs_io_register(void) {
    const char *L = "IoFileMgrForUser";
    KREG(L, sceIoOpen, hle_Open);
    KREG(L, sceIoClose, hle_Close);
    KREG(L, sceIoRead, hle_Read);
    KREG(L, sceIoWrite, hle_Write);
    KREG(L, sceIoLseek, hle_Lseek);
    KREG(L, sceIoReadAsync, hle_ReadAsync);
    KREG(L, sceIoWaitAsync, hle_WaitAsync);
    KREG(L, sceIoWaitAsyncCB, hle_WaitAsyncCB);
    KREG(L, sceIoPollAsync, hle_PollAsync);
    KREG(L, sceIoGetstat, hle_Getstat);
    KREG(L, sceIoIoctl, hle_Ioctl);
    KREG(L, sceIoDevctl, hle_Devctl);
}
```

In `runtime/kernel/kernel.c`: include `"io.h"`; add `khbbs_io_reset();` after `khbbs_fw_reset();` and `khbbs_io_register();` to the registration. Add `kernel/io.c` to `runtime/CMakeLists.txt`.

In `hosts/pc/main.c`: include `"khbbs/iso9660.h"`; add `const char *iso = NULL;`, the option `else if (!strcmp(argv[i], "--iso") && i + 1 < argc) iso = argv[++i];`, after loading the ELF `if (iso && khbbs_iso_open(iso) != 0) return 1;`, `[--iso <game.iso>]` in the usage line and the sentence `"--iso: the UMD image for disc0:, umd0: and umd1: (without it every disc file is missing)\n"`.

- [ ] **Step 5: Run the tests**

Run: `cmake --build build/ci && ctest --test-dir build/ci --output-on-failure`
Expected: `100% tests passed`; `test_iso9660: ok`, `test_io: ok` (plus one line `io: stdout ok` in the log).

- [ ] **Step 6: The real game, up to the PGD files**

```bash
cmake --build build/pc
perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --iso "$KHBBS_ISO" \
    --budget 200000 --time-limit 10 --trace build/p4-t16.jsonl > build/p4-t16.out 2> build/p4-t16.err; echo "exit $?"
tail -1 build/p4-t16.out
grep -o '"name":[^,]*\|"thread":"[^"]*"' build/p4-t16.jsonl | paste - - | uniq -c | head -50
```

Expected (measured with this plan's code in a scratch build, 2026-10-19): `khbbs: stopped: guest time limit reached`, exit 3, within a second or two. The first 42 records are PPSSPP's boot path in PPSSPP's own order: `root`'s four, `user_main`'s nineteen up to `sceKernelStartThread(DiscInfoBind)`, and `DiscInfoBind`'s nineteen up to its third `sceKernelUnlockLwMutex`, after it read the first 18 bytes of `disc0:/PSP_GAME/USRDIR/BBS1.DAT` (`00 50 47 44` "\0PGD": the file is PGD-encrypted). Then `DiscInfoBind` returns (it cannot parse the header; PPSSPP, which decrypts PGD after the `0x04100001` ioctl, goes on to `BBS2.DAT`), and `user_main` calls `sceKernelDelayThreadCB(33334)` from `0x0880F6F4` again and again, waiting for a result that never comes. Without `--time-limit` that loop makes no dispatches and runs until the alarm (a 4.9 GB trace in 120 s).

- [ ] **Step 7: Commit**

```bash
tools/check_no_assets.sh
git add runtime/iso9660.c runtime/include/khbbs/iso9660.h runtime/kernel/io.h runtime/kernel/io.c \
    runtime/kernel/errors.h runtime/kernel/kernel.c runtime/CMakeLists.txt hosts/pc/main.c \
    tests/runtime/isogen.h tests/runtime/test_iso9660.c tests/runtime/test_io.c tests/runtime/CMakeLists.txt
git commit -m "io: read-only ISO 9660 layer; disc0:/umd0:/umd1: with async reads on the guest clock; --iso

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: `trace_diff.py --until` and khbbs's options in `oracle.py`

**Files:**
- Modify: `tools/oracle/trace_diff.py`, `tools/oracle/oracle.py`
- Modify: `tests/tools/test_trace_diff.py`, `tests/tools/test_oracle.py`
- Modify: `docs/oracle.md` ("Comparing against khbbs", "Running it")

`oracle.py` is P2's (Task 8 of the P2 plan). If its argument handling differs from what is quoted here, keep its structure and add the same things: `--until` passed to `trace_diff`, and `--iso ISO --time det --time-limit S` on the khbbs command line.

- [ ] **Step 1: Write the failing tests**

Append to `class TraceDiff` in `tests/tools/test_trace_diff.py`:

```python
    def test_until_cuts_both_traces(self):
        a = [rec(0, DELAY, [1], 0), rec(1, CREATE, [1, 2], 0x40000), rec(2, DELAY, [9], 0)]
        b = [rec(0, DELAY, [1], 0), rec(1, CREATE, [1, 2], 0x40000), rec(2, DELAY, [7], 0)]
        r = self.diff(a, b, "--until", "0x446D8DE6:1")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("2 of 2 records match", r.stdout)
        self.assertIn("cut after the 1st 0x446D8DE6: A record 1, B record 1", r.stdout)

    def test_until_counts_calls(self):
        a = [rec(0, DELAY, [1], 0), rec(1, DELAY, [2], 0), rec(2, DELAY, [3], 0)]
        b = [rec(0, DELAY, [1], 0), rec(1, DELAY, [2], 0), rec(2, DELAY, [4], 0)]
        self.assertEqual(self.diff(a, b, "--until", "sceKernelDelayThread:2").returncode, 0)
        self.assertEqual(self.diff(a, b, "--until", "sceKernelDelayThread:3").returncode, 1)

    def test_until_not_reached(self):
        a = [rec(0, DELAY, [1], 0)]
        b = [rec(0, DELAY, [1], 0), rec(1, CREATE, [1, 2], 0x40000)]
        r = self.diff(a, b, "--until", "sceKernelCreateThread:1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("A never reached the 1st sceKernelCreateThread", r.stdout)

    def test_until_short_stream_is_a_divergence(self):
        a = [rec(0, DELAY, [1], 0, thread="main"), rec(1, CREATE, [1, 2], 0x40000, thread="main")]
        b = [rec(0, DELAY, [1], 0, thread="main"), rec(1, DELAY, [5], 0, thread="io"),
             rec(2, CREATE, [1, 2], 0x40000, thread="main")]
        r = self.diff(a, b, "--until", "sceKernelCreateThread:1")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("first divergence at record 0 of thread io", r.stdout)

    def test_until_bad_spec(self):
        t = [rec(0, DELAY, [1], 0)]
        for spec in ("sceKernelDelayThread", "sceKernelDelayThread:0", ":1"):
            with self.subTest(spec=spec):
                self.assertEqual(self.diff(t, t, "--until", spec).returncode, 2)
```

In `tests/tools/test_oracle.py`, in `test_commands`, change the expected khbbs command to

```python
            ["perl", "-e", "alarm shift; exec @ARGV", "120", "khbbs", "--elf", "EBOOT.ELF",
             "--iso", "game.iso", "--time", "det", "--time-limit", "10", "--trace", k, "--budget", "200000"],
```

and add:

```python
    def test_until_goes_to_trace_diff(self):
        r = FakeRunner()
        code, _, _ = self.run_oracle(r, "--until", "sceGeListEnQueue:1")
        self.assertEqual(code, 0)
        self.assertEqual(r.by_tool()["trace_diff"][-2:], ["--until", "sceGeListEnQueue:1"])
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: the new tests fail (`unrecognized arguments: --until`), and `test_commands` fails on the khbbs command.

- [ ] **Step 3: Implement `--until`**

In `tools/oracle/trace_diff.py`:
- in the module docstring, after the `-n N` paragraph, add: "--until NAME_OR_NID:K cuts both traces after the K-th call of NAME (inclusive), each in its own order (completion order on both sides), on every thread, before comparing. A trace that reached it has really ended there, so a stream that is then shorter on one side is a divergence. A trace that did not reach it (within -n) is reported, and the run exits 1." and add `[--until NAME_OR_NID:K]` to the usage line;
- add after `ignore_spec`:

```python
def until_spec(text):
    key, _, k = text.rpartition(":")
    try:
        nid = hle_sigs.parse_nid(key) if key else None
        k = int(k)
    except ValueError:
        nid = None
    if nid is None or k < 1:
        raise argparse.ArgumentTypeError(f"expected NAME_OR_NID:K with K >= 1, got {text!r}")
    return key, nid, k


def cut_until(recs, nid, k):
    """(records up to and including the k-th call of nid, its index or None)."""
    seen = 0
    for i, r in enumerate(recs):
        if val(r["nid"]) == nid:
            seen += 1
            if seen == k:
                return recs[:i + 1], i
    return recs, None


def ordinal(k):
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"
```

- give `walk` an `ended` parameter: `def walk(a, b, n, differ, per_thread, ended=None):` and replace its first `ended = {...}` line with `ended = ended or {"A": n is None or len(a) < n, "B": n is None or len(b) < n}`;
- in `main`, add the option `ap.add_argument("--until", type=until_spec, metavar="NAME_OR_NID:K", help="cut both traces after the K-th call of NAME")`, and replace the lines from `per_thread = ...` to `print(f"{matched} of {compared} ...")` with:

```python
    ended, missing = None, []
    if o.until:
        key, nid, k = o.until
        a, ia = cut_until(a, nid, k)
        b, ib = cut_until(b, nid, k)
        ended = {"A": ia is not None or o.n is None or len(a) < o.n,
                 "B": ib is not None or o.n is None or len(b) < o.n}
        missing = [side for side, i in (("A", ia), ("B", ib)) if i is None]
        if not missing:
            print(f"cut after the {ordinal(k)} {key}: A record {ia}, B record {ib}")
    per_thread = all(r.get("thread") is not None for r in a + b)
    differ = Differ(sigs, o.ignore_arg, o.map_uids, o.compare_ra)
    matched, compared, notes, divergence = walk(a, b, o.n, differ, per_thread, ended)
    mapped = f"; {differ.learned} uid pairs learned" if o.map_uids else ""
    print(f"{matched} of {compared} records match"
          f" ({'per-thread streams' if per_thread else 'one stream each'}{mapped})")
    for side in missing:
        window = f" within its first {o.n} records" if o.n else ""
        print(f"{side} never reached the {ordinal(o.until[2])} {o.until[0]}{window}")
```

  and make the end of `main` return 1 when anything is missing: replace `if divergence is None: return 0` with `if divergence is None: return 1 if missing else 0`.

- [ ] **Step 4: Pass it through `oracle.py`**

In `tools/oracle/oracle.py`:
- the khbbs command: `cmd = ["perl", "-e", ALARM, str(o.alarm), str(o.khbbs), "--elf", o.elf, "--iso", o.iso, "--time", "det", "--time-limit", str(o.time_limit), "--trace", str(out["khbbs"]), "--budget", str(o.budget)]`, with the option `fd.add_argument("--time-limit", type=positive, default=10, help="khbbs guest seconds (default 10; PPSSPP's first sceGeListEnQueue is at 34 ms)")`;
- the trace_diff step: `module("trace_diff", out["khbbs"], out["ppsspp"], "--map-uids", "-n", o.n, *(["--until", o.until] if o.until else []))`;
- the option: `fd.add_argument("--until", metavar="NAME:K", help="compare only up to the K-th call of NAME (trace_diff --until)")`;
- the docstring's step 1 gains "with --iso ISO and --time det", step 4 gains "[--until NAME:K]", and the usage gains `[--until NAME:K]`.

In `docs/oracle.md`, "Running it": add `--until NAME:K` to the options paragraph ("compare only up to and including the K-th call of NAME, cutting both traces on every thread; P4's gate uses `--until sceGeListEnQueue:1`"), and note that khbbs is run with `--iso` and `--time det`.

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest discover -s tests/tools -v`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
tools/check_no_assets.sh
git add tools/oracle/trace_diff.py tools/oracle/oracle.py tests/tools/test_trace_diff.py tests/tools/test_oracle.py docs/oracle.md
git commit -m "oracle: trace_diff --until NAME:K; khbbs runs with --iso and --time det

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: The gate, bring-up log, docs, PR

**Files:** fixes as the gate requires (each with a test in the matching `tests/runtime/test_*.c`); `docs/bringup.md`, `docs/plan.md`, `docs/architecture.md`, `docs/next-session.md`, this plan (copied to `docs/superpowers/plans/2026-10-19-p4-kernel-scheduler.md` if it is not already there).

- [ ] **Step 1: Clean build, every test**

```bash
rm -rf build/pc
cmake -S . -B build/pc -G Ninja && cmake --build build/pc && ctest --test-dir build/pc --output-on-failure
ctest --test-dir third_party/psprecomp/build --output-on-failure
python3 -m unittest discover -s tests/tools -v
python3 tools/unimpl_census.py build/pc/gen
```

Expected: everything passes; the census still prints `unimplemented 0` and `unhandled branches 0` (P4 changed no translation).

- [ ] **Step 2: Run the P4 gate**

```bash
PYTHONPATH=tools python3 -m oracle.oracle first-divergence --iso "$KHBBS_ISO" \
    --elf game/EBOOT.dec.ELF -n 400 --until sceKernelUnlockLwMutex:3
```

Expected: exit 0 and

```
cut after the 3rd sceKernelUnlockLwMutex: A record 41, B record 41
42 of 42 records match (per-thread streams; 9 uid pairs learned)
```

This was measured before the plan was written: this plan's code, built in a scratch copy, run on the game with `--time-limit 10`, against PPSSPP's decoded research log converted with `capture_hle.parse`. The cut is `DiscInfoBind`'s last call before what it does depends on decrypted PGD data (Decision 0). The 42 records are `root` 4, `user_main` 19 (through `sceKernelStartThread(DiscInfoBind)`), `DiscInfoBind` 19, in the same completion order on both sides. The 9 uid pairs: 2 threads, the heap block, 3 mutexes, 2 callbacks, 1 event flag (the fds are 3 and 4 on both sides, so nothing is learned from them).

- [ ] **Step 2b: Run the brief's full gate and record why it stops**

```bash
PYTHONPATH=tools python3 -m oracle.oracle first-divergence --iso "$KHBBS_ISO" \
    --elf game/EBOOT.dec.ELF -n 400 --until sceGeListEnQueue:1
```

Expected without PGD decryption (exit 1):

```
43 of 44 records match (per-thread streams; 9 uid pairs learned)
A never reached the 1st sceGeListEnQueue within its first 400 records
first divergence at record 20 of thread user_main (A seq 43, B seq 66)
differing: nid
```

(`user_main` calls `sceKernelDelayThreadCB` again where PPSSPP's goes on to `sceKernelWaitThreadEndCB`, because khbbs's `DiscInfoBind` gave up on `BBS1.DAT`'s encrypted header.) With decrypted `BBS1`–`3.DAT` (P5's `tools/pgd_decrypt`, or pulled forward if the user decides so) the expected report is `cut after the 1st sceGeListEnQueue: A record 73, B record 73` and `74 of 74 records match (per-thread streams; 12 uid pairs learned)`: 74 = `root` 4, `user_main` 28, `DiscInfoBind` 42; 12 = the 9 above plus a callback, an event flag and the GE list id.

- [ ] **Step 3: Work through each divergence**

For the first divergence the P4 gate (Step 2) shows, in order:
1. Read both records and the three before them on that thread. Decide what produced the difference: an argument or return of ours (fix the handler, with a new case in the family's test that reproduces it on synthetic data), a missing or extra call (look at the guest code between the previous call and this one: `grep -n "psp_func_<ra-function>  --" build/pc/gen/*.c` and the instruction comments), or a stack address (a thread layout difference: check Task 6's numbers against the probe table).
2. If the difference depends on bytes read after `sceIoIoctl(…, 0x04100001, …)` (PGD-encrypted content), stop and ask the user (Decision 11).
3. If it is PPSSPP-specific behaviour we chose not to model (e.g. `sceIoGetstat` timing, which per-thread comparison should not see) and it changes a per-thread stream, write it down with its cause and ask the user whether to model it or accept it.
4. Rebuild, rerun Step 2, repeat until it exits 0 or every remaining difference is listed with its cause and accepted by the user. Rerun Step 2b at the end and record its report.

Commit each fix separately: `git commit -m "kernel: <what> (oracle: <call> on <thread>)"` with the attribution line.

- [ ] **Step 4: Check the UNVERIFIED layout and stat values with snapshots**

```bash
for ev in sceKernelStartThread:1 sceImposeSetLanguageMode:1 sceIoOpen:1; do
  PYTHONPATH=tools python3 -m oracle.oracle first-divergence --iso "$KHBBS_ISO" \
      --elf game/EBOOT.dec.ELF -n 400 --until sceKernelUnlockLwMutex:3 --snapshot-at "$ev" \
      --out-dir "build/oracle-$ev" | tee "build/oracle-$ev.txt"
done
```

Read `mem_diff`'s ranges in each report:
- `sceKernelStartThread:1` (root about to start `user_main`): root's stack `0x09FFFC00`–`0x0A000000` and `user_main`'s freshly created stack `0x09FC8C00`–`0x09FFFC00` should not differ, except words the crt0 code stored from registers PPSSPP fills differently after firmware calls (it writes `0xDEADBEEF`-style garbage into `$a0`–`$t9` after a syscall, khbbs leaves the handler's values): list any such words. A difference in the `0xFF` fill or the uid word means the fill-at-create choice (Decision 6) is wrong.
- `sceImposeSetLanguageMode:1`: `0x09FFF650` and `0x09FFF654` (written by `sceImposeGetLanguageMode`) must match.
- `sceIoOpen:1` (`DiscInfoBind` about to open `umd1:`, after its first `sceIoGetstat`): the `SceIoStat` that call wrote at `0x09FC8980`–`0x09FC89D8` must match (mode, attribute, size, dates, `st_private`). If later calls reused that stack area first, take the snapshot at `sceUmdGetDriveStat:1` instead.
Fix what differs in the matching task's code and test, then rerun the gate.

- [ ] **Step 5: Where the run goes now**

```bash
perl -e 'alarm shift; exec @ARGV' 280 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --iso "$KHBBS_ISO" \
    --budget 50000000 --time-limit 60 --miss-log build/misses-p4.jsonl --keep-going 1000 > build/p4-run.out 2> build/p4-run.err
echo "exit $?"; tail -1 build/p4-run.out
grep -c "unimplemented firmware call" build/p4-run.err
grep "unimplemented firmware call" build/p4-run.err | sort | uniq -c | sort -rn | head -20
```

Record the stop reason, dispatches, the unimplemented NIDs by count and any dispatch misses.

- [ ] **Step 6: Bring-up entry**

Append to `docs/bringup.md` a `## P4, scheduler and boot threads (<date>)` entry in the P3 entry's style: fork pin and build time; the exact gate commands and their reports (Step 2: matched records, uid pairs, any accepted difference with its cause; Step 2b: where the full boot stops and that it is the PGD files); the snapshot findings of Step 4 (which UNVERIFIED items are now observed, which still are); the Step 5 command, its stop and the top unimplemented calls; what each family's test covers; and what this means for P5 (PGD, `ms0:`, the rest of IoFileMgr, `sceUtility*`) and P6 (the GE list the gate ends on).

In the P3 entry, replace the paragraph "Before that, a bad write32 at `0x00000004` happens inside `0x08AE9700`, called from crt0 at `0x08A8C0EC` (`ra 0x08A8C0F4`), with a NULL base register. It is the first thing P4 should look at, together with the sysmem placement." with: "Before that, a bad write32 at `0x00000004`: `sw $s5, 4($k0)` at `0x08A8BF3C` in `user_main`, with `$k0 = 0` because khbbs gave threads no kernel block (`0x08AE9700`, the call just before it, is the game's `memset` and returns normally; the dump's "last fn 0" comes from a build without `PSPRECOMP_TRACE`). P4 fixed it: every thread's `$k0` points at a block at the top of its stack, as on PPSSPP." Also fix the "What this means" bullet that names `0x08AE9700`.

- [ ] **Step 7: Roadmap and architecture**

`docs/plan.md`: link P4's row to this plan (`[Kernel and scheduler](superpowers/plans/2026-10-19-p4-kernel-scheduler.md)`), add the scope change to its "Delivers" cell ("plus the boot path's `sceUmd*`, `scePower*`, `sceImpose*` and a read-only ISO file layer, pulled from P5") and mark it **done** with a link to the bring-up entry, with "Done when" changed to the gate the user accepted (Decision 0); trim P5's "Delivers" accordingly (it still owns `ms0:`, PGD, the rest of IoFileMgr) and add the brief's original P4 gate (per-thread streams match through the first `sceGeListEnQueue`) to P5's "Done when" if that is what the user chose.

`docs/architecture.md`:
- "Threading": the host interface (`runtime/include/khbbs/host.h`, `hosts/posix`); `idle0` as the idle loop and where interrupt handlers run; the back-edge safe point as a countdown (`PSP_BACKEDGE_CHECK`, every 64 back-edges, 1 µs each in deterministic mode) instead of `if (g_pending) psp_service();`; stops unwinding every guest thread to its trampoline.
- "Memory": in the kernel partition row, add "`0x08000020`/`0x30`/`0x40` return addresses registered in the dispatch table, `idle0`'s stack `0x083FF000`–`0x08400000`, the interrupt stack `0x083FE000`–`0x083FF000`"; in the user partition rows, "thread stacks from the top (`root` at `0x09FFFC00`), `PSP_SMEM_Low` blocks above the image (the first at `0x09F3E000`)".
- "Build layout": `runtime/kernel/`, `hosts/posix/`.

- [ ] **Step 8: Next-session prompt**

Rewrite `docs/next-session.md` for the session after: where we are (P4 in review or merged, the gate result, where the run stops now), the rules (unchanged), the pin, and the next step (P5: write its detailed plan from what Step 5 found, starting with PGD and `ms0:`).

- [ ] **Step 9: Commit and open the PR**

```bash
tools/check_no_assets.sh
git add docs/bringup.md docs/plan.md docs/architecture.md docs/next-session.md docs/superpowers/plans/
git commit -m "docs: P4 bring-up results, roadmap, architecture, next-session prompt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin p4-kernel-scheduler
gh pr create --base main --title "P4: kernel and scheduler" --body-file build/pr-body.md
```

Write `build/pr-body.md` first (it stays out of git): what the fork gained (with the pin), the scheduler design in five lines, the families and NIDs implemented (count), the scope change from P5, the gate command and its result, the accepted differences, the UNVERIFIED items that remain, and how to reproduce (`tools/setup_toolchain.sh psprecomp`, the build, the tests, the gate). End it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
