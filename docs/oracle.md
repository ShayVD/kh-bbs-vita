# PPSSPP as the oracle

What PPSSPP 1.20.4 (official macOS SDL build, `~/Applications/PPSSPPSDL.app`)
offers to `tools/oracle/`, found by probing it by hand (P2 Task 1 of
[the P2 plan](superpowers/plans/2026-10-18-p2-oracle-tooling.md),
2026-10-19). PPSSPP always runs as a separate process and is driven over its
WebSocket debugger. Nothing of its code is copied or linked (psprecomp's
[ORACLE.md](../third_party/psprecomp/docs/ORACLE.md) rule).

## Isolated PPSSPP home

The SDL build on macOS puts its memory stick, and with it `ppsspp.ini`, at
`$HOME/.config/ppsspp`, unless the user defaults key
`UserPreferredMemoryStickDirectoryPath` (domain `org.ppsspp.ppsspp`) is set.
AppKit writes window state to `~/Library/Saved Application State` under the
real home, not `$HOME`. `CFFIXED_USER_HOME` moves that, and every other
Foundation home lookup, too. So every run sets both.

Before every launch the session deletes and rewrites `$H/.config/ppsspp`
(at least `PSP/SAVEDATA` and `ppsspp.ini`) and removes the log file. Then
every run starts with no save data, with port 45045, and with an empty log
(`--log=` appends). `$KHBBS_ISO` is the path to the user's ISO, the same file
[setup.md](setup.md) passes to `tools/extract_game.sh`.

```bash
H=$PWD/build/oracle/ppsspp-home
rm -rf "${H:?}/.config/ppsspp" "${H:?}/ppsspp-log.txt"
mkdir -p "$H/.config/ppsspp/PSP/SYSTEM"
cat > "$H/.config/ppsspp/PSP/SYSTEM/ppsspp.ini" <<'EOF'
[General]
FirstRun = False
AutoRun = False
CheckForNewVersion = False
RemoteDebuggerOnStartup = True
RemoteISOPort = 45045
[Graphics]
SoftwareRenderer = True
[Log]
SCEKERNELLevel = 4
EOF
HOME=$H CFFIXED_USER_HOME=$H ~/Applications/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL \
    --log="$H/ppsspp-log.txt" "$KHBBS_ISO" &
PID=$!
```

Keys:

- Required: `RemoteDebuggerOnStartup` and `RemoteISOPort` (the debugger,
  below), `AutoRun = False` (stop at the entry, below), `SCEKERNELLevel = 4`
  (info level, for thread names; 5 is debug, the default 2 is error).
- `SoftwareRenderer = True` is needed only for frame capture. The call trace
  is the same either way.
- `FirstRun` and `CheckForNewVersion` only silence dialogs and the update
  check.

Notes:

- A minimal ini is enough. PPSSPP fills in every other key in memory. The
  session writes the ini itself before every launch, so it never depends on
  what PPSSPP saved.
- `--log=` needs an absolute path.
- Stop with `kill -KILL $PID`. Only ever signal PIDs the session started.
  On SIGTERM or SIGINT PPSSPP crashes while quitting (SIGABRT or SIGSEGV,
  exit code -6 or -11), and macOS then writes a `PPSSPPSDL-*.ips` crash
  report to `~/Library/Logs/DiagnosticReports`. SIGKILL writes none (checked:
  the folder listing was identical before and after a run) and saves nothing
  either.
- `PPSSPPSDL --help` lists no memory stick option. The app bundle is not
  touched.

Checked on this machine:

- None of `~/.config/ppsspp`, `~/Documents/PPSSPP`, `~/PSP`,
  `~/Library/Application Support/*PPSSPP*`, `~/Library/Preferences/*ppsspp*`,
  `~/Library/Caches`, `HTTPStorages` or `Containers` changed, and the
  `org.ppsspp.ppsspp` defaults domain stayed absent, over about 20 launches.
- One early launch with `HOME` alone (before `CFFIXED_USER_HOME` was added)
  created `~/Library/Saved Application State/org.ppsspp.ppsspp.savedState`.

## Remote debugger

| | |
|---|---|
| ini keys | `[General] RemoteDebuggerOnStartup = True`, `RemoteISOPort = 45045` |
| listens | `*:45045` (IPv6 socket, all interfaces, dual stack) |
| URL | `ws://127.0.0.1:45045/debugger` |
| subprotocol | `debugger.ppsspp.org` |
| handshake reply | `HTTP/1.1 101 OK`, `Server: PPSSPPServer v0.1` |

- If 45045 is taken, PPSSPP silently listens on a random port (and would
  save that port into the ini if it quit cleanly). Check with
  `lsof -nP -iTCP:45045 -sTCP:LISTEN`. After a run `netstat -an` shows a
  `TIME_WAIT` on our side of the connection: it sits on our ephemeral port
  and does not block 45045. The session's check binds with `SO_REUSEADDR`,
  so a `TIME_WAIT` on PPSSPP's side (for example after it crashed) does not
  count either.
- From reading PPSSPP's source (not checked with a packet capture): while
  this server runs, PPSSPP sends its local IP address and port to its own
  matchmaking host (`/match/update?local=IP&port=PORT`), every 9 minutes. It
  is built in and cannot be turned off.
- Large replies (`memory.read`, buffers) arrive as fragmented WebSocket
  messages (continuation frames). The client must join them.

## Catching the first call

`AutoRun = False` makes PPSSPP load the game and stop before its first
instruction. The port opens before the game is loaded, so the first
`cpu.status` replies can be `paused: true, pc: 0`. About half a second after
launch it reads `stepping: true, pc: 145276248` (`0x08A8BD58`, the ELF entry),
`ticks: 0`. A new connection also receives a `cpu.stepping` event then. The
only threads are `root` (uid 275), `idle0` and `idle1`.

Breakpoints added now (or even before the game has loaded) are kept;
`cpu.resume` then runs the game. In every run the first call recorded is the
crt0 `0x35669D4C` stub at `0x08B305A0` with `a0 0x06020010`, then
`sceKernelSetCompilerVersion` (`a0 0x00030306`),
`sceKernelCreateThread(user_main, 0x08A8BE6C, 0x13, 0x37000, ...)`, and
`sceKernelStartThread`: the order [bringup.md](bringup.md) records for
`khbbs`.

`game.reset` is not needed and not reliable. Sent while stepping it replied,
and then PPSSPP stopped answering (it needed SIGKILL). Sent while running with
`break: true` it replied, but no reset happened within a few seconds.

## Numbers

- JSON fields (addresses, register values, `ticks`, thread `id`s) are
  unsigned decimal integers.
- `S`/`R` log fields are 8 lowercase hex digits without `0x`.
- In the `sceKernelCreateThread` info line the uid before `=` is decimal, the
  arguments are hex except the stack size, which is decimal. Convert the uid
  to 8-digit hex to join it with `S`/`R` lines.
- PPSSPP's uids: `root` 275 = `0x113`, `idle0` 272 = `0x110`, `idle1` 273 =
  `0x111`. They were the same in every run (and so were the game's threads:
  `user_main` 276, `DiscInfoBind` 285, ...), but read them from
  `hle.thread.list` at the entry stop rather than hard-coding them.

## Messages we use

Requests are `{"event": NAME, "ticket": T, ...}`. A reply carries the same
`event` and echoes `ticket` unchanged (we send strings). Other events arrive
at any time without a ticket.

| Event | Request keys | Reply keys |
|---|---|---|
| `version` | optional `name`, `version` | `name` (`"PPSSPP"`), `version` (`"v1.20.4"`) |
| `game.status` | | `game` (`{id, version, title}` or `null`), `paused` |
| `cpu.status` | | `stepping`, `paused`, `pc`, `ticks` |
| `cpu.stepping` | | no reply (see below) |
| `cpu.resume` | | no reply (see below) |
| `cpu.getAllRegs` | optional `thread` | `categories: [{id, name, registerNames, uintValues, floatValues}]` |
| `cpu.getReg` | `name` | `category`, `register`, `uintValue`, `floatValue` |
| `cpu.evaluate` | `expression` | `uintValue`, `floatValue` |
| `cpu.breakpoint.add` | `address`; optional `enabled`, `log`, `condition`, `logFormat` | (empty reply) |
| `cpu.breakpoint.remove` | `address` | (empty reply) |
| `cpu.breakpoint.list` | | `breakpoints: [{address, enabled, log, condition, logFormat, symbol, code}]` |
| `memory.read` | `address`, `size`; optional `replacements` | `base64` |
| `hle.thread.list` | | `threads: [{id, name, status, statuses, pc, entry, initialStackSize, currentStackSize, priority, waitType, isCurrent}]` |
| `gpu.stats.feed` | optional `enable` (default true) | no reply; one `gpu.stats.get` event per flip |
| `broadcast.config.set` | `disallowed` | `disallowed` |
| `gpu.buffer.screenshot` | `type` (`"uri"`/`"base64"`) | fails here, see Frames |

- Errors: `{"event":"error","message":"...","level":2,"ticket":T}`. Level 2
  is PPSSPP's "error" severity. Unknown events give `"Bad message: unknown
  event"`.
- `cpu.stepping` and `cpu.resume` never reply on success. Each is followed by
  an event of the same name without a ticket: `cpu.stepping` `{pc, ticks}`
  (optionally `reason`, `relatedAddress`), `cpu.resume` `{}`. `cpu.resume`
  while running fails with `"CPU not stepping"` (with the ticket).
  `cpu.stepping` while already stepping sends nothing at all, so check
  `cpu.status` before waiting for a `cpu.stepping` event.
- `cpu.status`: `paused` is true before the game is loaded. `ticks` counts
  CPU cycles (222 MHz, inferred: 40 s of emulation gave about 8.9e9).
- `cpu.getAllRegs`: category 0 is `GPR` with 35 entries. Indices 0-31 are
  `zero`..`ra` (`a0` = 4, `t0` = 8, `ra` = 31), 32 is `pc`, 33 `hi`, 34
  `lo`. Category 1 is `FPU` (32), 2 is `VFPU` (128).
- `cpu.evaluate` with `[k0+0xc0]` gives the current thread uid.
- `cpu.breakpoint.add`: `enabled` means the breakpoint stops execution;
  `log` means it writes its `logFormat` line to the log. Both default to
  false when omitted. In `logFormat`, `{expr}` is replaced by the value of an
  expression (registers, `[addr]` reads a word); register values print as 8
  hex digits. A breakpoint at the same address is replaced. Nothing is
  validated: address `0x10` and condition `"a0 =="` were accepted silently.
  The reply has the ticket and no other keys. 2,000 adds, sent one after
  another, took 0.07 s.
- `cpu.breakpoint.remove` gives the same empty reply, also when there was no
  breakpoint.
- `memory.read`: see Memory below.
- `broadcast.config.set` with `{"disallowed":{"logger":true,"input":true}}`
  drops `log` and `input.analog` events. Send it after the game is loaded:
  before that, PPSSPP rejects the key with `"Unsupported 'disallowed' object
  key 'logger'"`. Never set `"stepping": true`: that drops the `cpu.stepping`
  events the session waits for.
- `log` events (`{timestamp, header, message, level, channel}`) are
  **unusable in 1.20.4**. After the first 1024 log messages on a connection
  the broadcaster re-sends stale entries from the start of its ring buffer
  instead of the new ones (in one 20 s capture 94% of `log` events were
  repeats, out of order). Read PPSSPP's `--log=` file instead.

A stopping breakpoint stops every thread; read registers with
`cpu.getAllRegs`, then send `cpu.resume`. Each stop costs about 22 ms (one
host frame), so about 45 stops per second: fine for stopping at an event,
too slow for tracing every call.

## Memory

- Valid ranges: RAM `0x08000000`..`0x0A000000` (32 MiB; the game uses
  `0x08800000` up), VRAM from `0x04000000` (2 MiB; reads past that hit its
  mirrors), scratchpad `0x00010000` (16 KiB). The `0x4...` and `0x8...`
  mirrors read the same bytes (checked for 1 MiB at `0x48804000`).
- Errors: `"Invalid address"` (bad start), `"Invalid size"` (range runs past
  the end), `"Missing 'size' parameter"`, `"CPU not started"`.
- No size cap beyond the valid range: 1 MiB took 5 ms, 24 MiB
  (`0x08800000`..`0x0A000000`) about 1 s.
- `memory.read` also answers while the game runs, but the CPU keeps writing
  during the read, so the result is torn and differs per run. Snapshots are
  taken while stepping.
- `replacements` (default true) puts back the original instructions where
  PPSSPP has patched code with its own markers (JIT block entries, replaced
  functions). Keep the default; with `false`, 5,299 words of the first MiB
  of the EBOOT differed.

### Snapshots

The event is "just before the K-th call to firmware function NAME"
(`NAME:K`, or `0xNID:K` for a NID khbbs has not implemented), because it
means the same thing on both sides. Both write RAM `0x08000000`..`0x0A000000`
raw plus a sidecar `FILE.json` (`base`, `size`, `event`, `side`, `nid`,
`ra`):

    mkdir -p build/oracle        # khbbs does not create the directory
    build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 200000 \
        --snapshot-at sceKernelCreateThread:1 --snapshot build/oracle/khbbs-mem.bin
    tools/oracle/snapshot.py --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF \
        --at sceKernelCreateThread:1 -o build/oracle/ppsspp-mem.bin
    tools/oracle/mem_diff.py build/oracle/khbbs-mem.bin build/oracle/ppsspp-mem.bin \
        --elf game/EBOOT.dec.ELF --ignore 0x08000000-0x08800000

`mem_diff.py` joins differing bytes into one range when at most `--gap`
(default 16) equal bytes lie between them. A range never crosses a section,
the edge of the image or partition, or an `--ignore` hole. Gaps between
sections inside the image are labelled `image (no section)`.

Both snapshot tools delete old copies of the dump and its sidecar first, and
both again if writing fails. They refuse an output path that is one of their
inputs. khbbs only warns when the snapshot never fired or failed (its exit
status is the run's), so callers check that the sidecar exists.

khbbs takes it in psprecomp's pre-call hook, PPSSPP at a stopping breakpoint
on the import stub (about 1 s). At `sceKernelCreateThread:1` both had the
same `ra` (`0x08A8BE30`), and `.data`, `.bss` and the rest of the image
matched. What differed:

- The kernel partition (680,429 bytes): PPSSPP's own kernel structures.
  khbbs keeps its boot arguments and first stack there. Ignore it.
- `.sceStub.text` (787 bytes): PPSSPP writes a `syscall` into each stub.
- `.text`, 51 bytes in 9 ranges: PPSSPP code patches that `replacements`
  did not put back (`0x68......` and `0x6A......` words, around the entry
  function `0x08A8BD58` and at `0x0881EF68`, `0x08AE94FC`, `0x08AE95D0`,
  `0x08AE9700`). Two of the ranges, near the entry function, span several
  words: 16 bytes from `0x08A8BD8C` and 52 bytes from `0x08A8BDCC`.
- `0x09FFFC00`..`0x0A000000` (776 bytes): PPSSPP's root thread stack (filled
  with `0xFF`, thread uid `0x113` at its bottom). khbbs's stack is elsewhere.

## Firmware-call source

**Choice: non-stopping log breakpoints on every import stub and on every
return site, read from PPSSPP's `--log=` file.**

- Stubs: the 281 entries of the EBOOT's import table (`.sceStub.text` from
  `0x08B300A8`, 8 bytes each), read with
  [tools/import_report.py](../tools/import_report.py). All 281 have `jal`
  callers. Each gets `enabled: false, log: true`, `logFormat`
  `S {pc} {[k0+0xc0]} {ra} {a0} {a1} {a2} {a3} {t0} {t1} {t2} {t3}`.
  PPSSPP rewrites each stub as `jr ra; syscall`, so at the stub the arguments
  are intact.
- Return sites: every `jal stub` + 8, from a scan of the executable sections
  (`import_report.py` already counts `jal` sites per stub): 1,686 sites. The
  EBOOT has no `j` tail calls to stubs, and the only data words pointing at
  stubs are the import table's own. Each gets `enabled: false, log: true`,
  `condition: "pc == ra"`, `logFormat` `R {pc} {[k0+0xc0]} {v0} {v1}`. The
  condition filters out arrivals at that address that are not returns from
  the stub (a branch to the instruction after a call).
- `[k0+0xc0]` is the thread uid: PPSSPP points each thread's `k0` at a block
  at the top of its stack and stores the uid at `+0xC0`.
- Names: the stub address gives the NID, and
  [tools/nid_names.txt](../tools/nid_names.txt) the name. Thread names come
  from the `sceKernelCreateThread` info lines and `hle.thread.list`.
- `v0` is the return value. `v1` is the high word of 64-bit returns
  (`sceKernelGetSystemTimeWide`); after 32-bit returns it is leftover data.
- Cost: 20 s of boot gave 79,348 calls (about 4,000 per second), each paired
  with its return (the 8 left open were threads asleep at the end), and
  emulation stayed at full speed (1,196 flips in 20 s).

### Parsing the log file

- `BKP` lines are notice level on channel `JIT`, so they appear at the
  default log levels.
- The file is flushed after every line: a session can tail it while PPSSPP
  runs (checked: 3 lines after 3 stops, 120 more over 2 s of running).
- Breakpoint lines: `BKP PC=[0-9a-f]{8}: ([SR]) (.+)$`. Split the rest on
  single spaces. `S`: `pc uid ra a0 a1 a2 a3 t0 t1 t2 t3`.
  `R`: `pc uid v0 v1`.
- Thread creation: `^.*\b(\d+)=sceKernelCreateThread\((.*?), ` gives the
  decimal uid and the name. It sits between that call's `S` and `R`, and the
  `R`'s `v0` equals the uid.
- File order is the only ordering; timestamps (`mm:ss:mmm`) have 1 ms
  resolution. Skip every other line.

Sample lines (every value made up):

```
00:12:117              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30010: S 08b30010 00000113 08a90104 00000040 09fff000 deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef
00:12:117              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90104: R 08a90104 00000113 00000000 deadbeef
00:12:118              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30028: S 08b30028 00000113 08a90230 08b51200 08a90400 00000020 00004000 80000000 00000000 08b40000 08b7a000
00:12:118              I[SCEKERNEL]: HLE/sceKernelThread.cpp:1902 290=sceKernelCreateThread(worker, 08a90400, 00000020, 16384, 80000000, 00000000)
00:12:118              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90230: R 08a90230 00000113 00000122 08b60000
00:12:118              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30030: S 08b30030 00000113 08a90240 00000122 00000000 00000000 deadbeef deadbeef deadbeef deadbeef deadbeef
00:12:119              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30048: S 08b30048 00000122 08a90410 deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef
00:12:119              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90410: R 08a90410 00000122 1234abcd 00000005
00:12:119              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90240: R 08a90240 00000113 00000000 deadbeef
00:12:120              N[BOOT]: UI/EmuScreen.cpp:391 Booted disc0:/sample.iso...
```

### Pairing calls and returns

- Keep a stack of open `S` lines per thread uid. An `R` closes the topmost
  open `S` on its thread with the same `ra` as the `R`'s `pc`.
- A blocking call's `R` comes after other threads' lines (above: thread
  `0x113`'s call at `08a90240` returns after thread `0x122` ran).
- Callbacks run inside a waiting `...CB` call on the same thread, so their
  `S`/`R` pairs sit inside the outer call's `S` and `R`.
- Interrupt handlers run on whatever thread was current (often `idle0`,
  uid 272) and their calls carry that uid. If that thread has an open call,
  the handler's pairs nest inside it the same way.
- An `R` with no open `S` should not happen: report it.
- Some `S` lines never get an `R`: calls that do not return
  (`sceKernelExitThread`, `sceKernelExitDeleteThread`) and threads still
  asleep when the capture stops.
- Records carry the thread's name, not its uid. Names need not be unique,
  so the k-th thread (k >= 2) created with a name already taken is called
  `NAME#k`, counting in creation order (the threads of `hle.thread.list` at
  the entry stop first). `khbbs` must name its threads by the same rule once
  it has threads (P4), or per-thread comparison pairs the wrong streams.

### Why not the alternatives

- **HLE debug log lines** (`HLELevel`/`SCE*Level = 5`). The format is
  `ret=name(args)[: note]`, with `ret` in decimal, `%08x` or as
  `SCE_KERNEL_ERROR_...`, arguments formatted per function (strings printed,
  some decimal, some hex, not always 8), and `K ` before kernel-internal
  calls. Functions that log at verbose level, or not at all, print nothing
  in this release build, so only 53.7% of calls produce a line. 21 functions
  never log, among them `sceKernelCpuSuspendIntr`, `sceKernelCpuResumeIntr`,
  `sceKernelSleepThread`, `sceKernelWakeupThread`,
  `sceKernelGetSystemTimeWide`, `sceKernelLockLwMutex`,
  `sceKernelUnlockLwMutex` and `sceKernelDcacheWritebackAll`. The lines
  carry no thread (only `Context switch: a -> b (uidA->uidB, ...)` lines at
  debug level). PPSSPP's own helpers also appear (`K
  0=sceKernelDeleteThread(...)` from the root thread's return path,
  `__IoAsyncFinish`, `ASYNC ...`); the stub breakpoints never see them.
- **The WebSocket `log` event**: broken, see above.
- **Stopping breakpoints** with `cpu.getAllRegs`: exact, but one stop each
  at `S` and `R`, about 22 calls per second.

Not covered: code the game loads at run time (overlays, modules) calls
through other stubs. The EBOOT imports no `sceKernelLoadModule` (only
`sceUtilityLoadModule`, whose firmware modules PPSSPP emulates), and no
module was loaded in the first 20 s. Such code needs its own stub and
return-site breakpoints once it is loaded.

### Comparing against khbbs

- Unused argument slots hold whatever was in the register (not always
  `deadbeef`), and that differs between emulators. The number of arguments
  per function must come from a per-function table. `t0`-`t3` are arguments
  5-8.
- Thread uids and heap addresses differ by implementation (`user_main` is
  276 in PPSSPP and `0x00040000` in `khbbs`). Map them (threads by name) or
  ignore them; never compare them raw.

[tools/oracle/trace_diff.py](../tools/oracle/trace_diff.py) does this
comparison and prints the first record where two traces differ. It reads
the per-function table
[tools/oracle/hle_sigs.txt](../tools/oracle/hle_sigs.txt): one line per
function, `NAME NARGS RET` (or `0xNID NARGS RET` for a NID that is not SHA-1
of a name). `NARGS` is a count or a list of register indices (`0,2,3,4` for
`sceIoLseek`, whose 64-bit offset skips `a1`). `RET` is `i` (32 bits in
`v0`), `l` (64 bits in `v0:v1`), `v` (not compared), `u` (a kernel-object
UID or fd) or `d` (like `i`, for a call that frees the UID or fd in its first
argument: `Delete*`, `sceIoClose`, ...). With `--map-uids` it learns pairs
from `u` returns only, and a `d` call that returns 0 on both sides drops the
pair holding its argument, so a reused UID or fd can be learned again. Heap
addresses (`sceKernelGetBlockHeadAddr`) are still compared raw: their
placement is part of what the oracle checks. A function with no line is
compared on every argument and `v0`, and the report says so. Usage and the
`-n` window rule are in the script's docstring.

## Frames

- `gpu.buffer.screenshot` reads the host window's back buffer (window size,
  with scaling), not the PSP framebuffer. On this Mac it fails with `"Could
  not download output"` on Vulkan, OpenGL and the software renderer alike,
  and so does `gpu.buffer.renderColor`. Per the API, `type: "base64"` would
  give raw pixels (`width` = stride, `height`, `flipped`, `format` such as
  `R8G8B8A8_UNORM`) and `type: "uri"` a PNG `data:` URI.
- What works: `SoftwareRenderer = True` (PPSSPP then draws into emulated
  VRAM), stop at the frame, `memory.read` the displayed buffer.
  `sceDisplaySetFrameBuf(topaddr, bufferwidth, pixelformat, sync)` names it.
  KH BBS alternates `0x04000000`/`0x04044000`, width 512, format 1, so one
  frame is `512 * 272 * 2` bytes; crop to 480x272.
- Pixel formats (little endian), per the PSP GE pixel-format
  documentation; format 1 checked against the frame-300 capture:
  - 0: 565, 16 bits: R bits 0-4, G 5-10, B 11-15.
  - 1: 5551, 16 bits (A1B5G5R5): R bits 0-4, G 5-9, B 10-14, A bit 15.
  - 2: 4444, 16 bits: R in the low nibble, then G, B, A.
  - 3: 8888, 32 bits: bytes R, G, B, A.

  The alpha bits are ignored for display.
- Frame N is the Nth `sceDisplaySetFrameBuf` call, a count the session keeps
  itself. Put a stopping breakpoint on its stub (`0x08B307B8`) and resume
  N-1 times; at the Nth stop `a0` names the finished frame. Frame 300 was
  reached in 6.5 s. The game calls it about once per flip (1,184 calls
  against 1,196 `gpu.stats.get` feed events in 20 s; the 12 extra flips
  are unexplained, and the feed has no frame index).
  `sceDisplayWaitVblankStart` is called once, so it is no frame counter.
  A game that calls `sceDisplaySetFrameBuf` more than once per vblank
  shifts the count: frame N is then no longer the N-th frame shown.
- The trace also has a breakpoint on that stub, and adding a breakpoint
  replaces the one at the same address. So during frame capture it is
  `enabled: true, log: true` with the trace's `logFormat`: checked, it both
  stops and writes its line.
- Frame 300 shows the auto-save notice that waits for a button press (no
  save data on the fresh memory stick).

`tools/oracle/frame.py` grabs frame N this way: a stopping breakpoint on the
stub (no trace), `a0`/`a1`/`a2` read at the Nth stop, `bufferwidth * 272`
pixels read from `topaddr`, converted by `tools/oracle/vram.py` and cropped
to 480x272. `--raw FILE` keeps the VRAM bytes and `FILE.json`. Then
`tools/oracle/img_diff.py` compares two frames (khbbs writes its own from
P6):

    tools/oracle/frame.py --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF \
        --frame 600 -o build/oracle/ppsspp-frame-600.png
    tools/oracle/img_diff.py build/oracle/khbbs-frame-600.png \
        build/oracle/ppsspp-frame-600.png --mask build/oracle/frame-600-mask.png

Frame 600 (`0x04044000`, stride 512, format 1; about 22 s with the launch)
still shows the auto-save notice: white text in a red box, "OK" button.
Two runs gave identical PNGs.

## Determinism

PPSSPP's emulated time runs on its own cycle counter, so a boot from the same
fresh memory stick, with ini files differing only in graphics backend and
HLE log level, repeated exactly. Three captures (Vulkan, Vulkan with HLE
debug logging, software renderer) were each cut at 20 s of wall time, so
their lengths differ (79,348, 79,367 and 79,337 calls; at least 158,666
`S`/`R` lines). They agreed on every line of their common length, arguments
and return values included. The game stops at the first input prompt (an
auto-save notice), so only the boot up to there was tested. Real-time clock
calls, input, save data and I/O timing settings can make runs differ, and
wall-clock timing never matches `khbbs`. So comparisons key on events
(per-thread call order, frame index), never on time.

## Running it

[tools/oracle/oracle.py](../tools/oracle/oracle.py) `first-divergence`
runs both sides on the same boot and prints where they first disagree (the
P2 gate):

    PYTHONPATH=tools python3 -m oracle.oracle first-divergence \
        --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF -n 200 \
        [--snapshot-at sceKernelCreateThread:1]

In order: `khbbs` under the perl alarm wrapper with `--budget` and
`--trace`, `capture_hle -n N`, with `--snapshot-at` also `snapshot.py`,
then `trace_diff.py --map-uids -n N` and, with `--snapshot-at`,
`mem_diff.py --elf ELF --ignore 0x08000000-0x08800000` (the kernel
partition, see Snapshots). Each runs as `python3 -m oracle.X` with
`tools/` on `PYTHONPATH`, and both reports are printed as they are.

Options: `--iso` (default `$KHBBS_ISO`), `--elf`, `-n` (records compared,
default 200), `--budget` (khbbs dispatches, default 200,000), `--snapshot-at
NAME:K`, `--alarm` (seconds before khbbs is killed, default 120),
`--timeout` (seconds for each PPSSPP step, default 120), `--out-dir`
(default `build/oracle`), `--khbbs` (default `build/pc/hosts/pc/khbbs`).

The budget is low on purpose: the khbbs trace grows by about 660 bytes per
dispatch. At 200,000 dispatches it is 131 MB (589,623 firmware calls), far
more than any `-n` needs.

Outputs, all under `build/oracle/` and removed before every run:
`khbbs.jsonl`, `khbbs.log` (khbbs's stdout and stderr: about 20 MB of
psprecomp "unimplemented firmware call" lines at this budget),
`ppsspp.jsonl`, and with `--snapshot-at` `khbbs-mem.bin`, `ppsspp-mem.bin`
and their `.json` sidecars.

khbbs's exit status and stop line are printed, not judged: exit 3 (budget
or first miss) is the normal end. Exit status of `oracle.py`: `trace_diff`'s
(0 every compared record matches, 1 a divergence, 2 bad input), or 2 when a
step fails (khbbs wrote no trace or no snapshot sidecar, `capture_hle` or
`snapshot.py` exited non-zero, `mem_diff` exited 2). A memory difference is
printed as `oracle: memory differs at EVENT ...` after `mem_diff`'s report
and does not change the exit status.

It takes about 3 s with `-n 200` and about 4 s with `--snapshot-at
sceKernelCreateThread:1` (wall time on this Mac, PPSSPP launches included).
The first result is in [bringup.md](bringup.md), "P2, first oracle
comparison".
