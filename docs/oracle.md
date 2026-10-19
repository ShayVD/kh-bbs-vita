# PPSSPP as the oracle

What PPSSPP 1.20.4 (official macOS SDL build, `~/Applications/PPSSPPSDL.app`)
offers to `tools/oracle/`, found by probing it by hand (P2 Task 1,
2026-10-19). PPSSPP always runs as a separate process and is driven over its
WebSocket debugger. Nothing of its code is copied or linked.

## Isolated PPSSPP home

The SDL build on macOS puts its memory stick (and with it `ppsspp.ini`) at
`$HOME/.config/ppsspp`, unless the user defaults key
`UserPreferredMemoryStickDirectoryPath` (domain `org.ppsspp.ppsspp`) is set.
AppKit writes window state to `~/Library/Saved Application State` using the
real home, not `$HOME`. `CFFIXED_USER_HOME` moves that, and every other
Foundation home lookup, as well. So every run sets both:

```bash
H=$PWD/build/oracle/ppsspp-home
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
```

- A minimal ini like this is enough. PPSSPP fills in every other key and
  rewrites the file when it quits (on SIGTERM; SIGKILL skips the save).
- The memory stick tree (`PSP/SAVEDATA`, `PSP/SYSTEM/CACHE`, ...) is created
  under `$H/.config/ppsspp`. It starts with no save data, and the game's boot
  path depends on that.
- `--log=` needs an absolute path and appends, so delete the file first.
- Stop PPSSPP with SIGTERM (it exits within a second or two), and only PIDs we
  started. SIGKILL if it hangs (see `game.reset` below).
- `PPSSPPSDL --help` lists no memory stick option. The bundle is not touched.
- Checked on this machine: none of `~/.config/ppsspp`, `~/Documents/PPSSPP`,
  `~/Library/Application Support/*PPSSPP*`, `~/Library/Preferences/*ppsspp*`,
  `~/Library/Caches`, `HTTPStorages`, `Containers` or `defaults read
  org.ppsspp.ppsspp` changed over the rest of the spike (about 20 launches). One early launch with
  `HOME` alone (before `CFFIXED_USER_HOME` was added) created
  `~/Library/Saved Application State/org.ppsspp.ppsspp.savedState`.

## Remote debugger

| | |
|---|---|
| ini keys | `[General] RemoteDebuggerOnStartup = True`, `RemoteISOPort = 45045` |
| listens | `*:45045` (IPv6 socket, all interfaces, dual stack) |
| URL | `ws://127.0.0.1:45045/debugger` |
| subprotocol | `debugger.ppsspp.org` |
| server | `HTTP/1.1 101 OK`, `Server: PPSSPPServer v0.1` |

- If 45045 is taken, PPSSPP silently listens on a random port and saves that
  port into the ini on exit. Check with `lsof -nP -iTCP:45045 -sTCP:LISTEN`.
- PPSSPP's source registers the local IP and port with its own matchmaking
  host whenever this server runs (built in, not configurable). Nothing to do
  about it, but worth knowing.
- The port opens before the game is loaded. The first `cpu.status` replies
  can be `paused: true, pc: 0`; wait for `stepping: true` (see below).
- Large replies (`memory.read`, buffers) arrive as fragmented WebSocket
  messages (continuation frames); the client must reassemble them.

## Catching the first call

`[General] AutoRun = False` makes PPSSPP load the game and stop before the
first instruction: `cpu.status` gives `stepping: true, pc: 0x08A8BD58`
(the ELF entry), `ticks: 0`, and a new connection receives
`{"event":"cpu.stepping","pc":145276248,"ticks":0}`. The only thread is
`root` (uid 275). Breakpoints added now (or even before the game has loaded)
are kept; `cpu.resume` then runs the game. In every run the first call
recorded is the crt0 `0x35669D4C` stub at `0x08B305A0` with `a0 0x06020010`,
then `sceKernelSetCompilerVersion` (`a0 0x00030306`),
`sceKernelCreateThread(user_main, 0x08A8BE6C, 0x13, 0x37000, ...)`, and
`sceKernelStartThread`, the order `docs/bringup.md` records for `khbbs`.

`game.reset` is not needed and is not reliable: sent while stepping it
replied and then PPSSPP stopped answering (needed SIGKILL); sent while running
with `break: true` it replied but no reset was seen within a few seconds.

## Messages we use

Requests are `{"event": NAME, "ticket": T, ...}`; replies carry the same
`event` and echo `ticket` unchanged (we send strings). Errors are
`{"event":"error","message":"...","level":2,"ticket":T}`; unknown events give
`"Bad message: unknown event"`. Other events (`log`, `cpu.stepping`,
`cpu.resume`, `game.start`, `input.analog`, ...) arrive at any time without a
ticket. Numbers are plain JSON integers (addresses as unsigned decimals).

| Event | Request fields | Reply |
|---|---|---|
| `version` | optional `name`, `version` | `name: "PPSSPP"`, `version: "v1.20.4"` |
| `game.status` | none | `game: {id: "ULUS10505", version: "1.00", title}` or `null`; `paused` (pause menu, not stepping) |
| `cpu.status` | none | `stepping`, `paused` (true before the game is loaded), `pc`, `ticks` (CPU cycles, 222 MHz) |
| `cpu.stepping` | none | **no reply.** A `cpu.stepping` event `{pc, ticks}` follows (optional `reason`, `relatedAddress`). Sent while already stepping: nothing at all |
| `cpu.resume` | none | **no reply** on success; a `cpu.resume` event `{}` follows. While running: error `"CPU not stepping"` with the ticket |
| `cpu.getAllRegs` | optional `thread` | `categories`: `[{id: 0, name: "GPR", registerNames, uintValues, floatValues}, {id: 1, "FPU", 32}, {id: 2, "VFPU", 128}]`. GPR indices 0-31 are `zero`..`ra` (`a0` = 4, `t0` = 8, `ra` = 31), 32 `pc`, 33 `hi`, 34 `lo` |
| `cpu.getReg` | `name` | `category`, `register`, `uintValue`, `floatValue` |
| `cpu.evaluate` | `expression` | `uintValue`, `floatValue` (`[k0+0xc0]` gives the current thread uid) |
| `cpu.breakpoint.add` | `address`; optional `enabled`, `log`, `condition`, `logFormat` | empty reply. Replaces a breakpoint at the same address. Not validated: address `0x10` and condition `"a0 =="` were accepted silently |
| `cpu.breakpoint.remove` | `address` | empty reply (also when none existed) |
| `cpu.breakpoint.list` | none | `breakpoints: [{address, enabled, log, condition, logFormat, symbol, code}]` |
| `memory.read` | `address`, `size`; optional `replacements` | `base64`. No size cap beyond the valid range: 1 MiB in 5 ms, 24 MiB (`0x08800000`..`0x0A000000`) in about 1 s. Errors `"Invalid address"`, `"Invalid size"` (range past the end), `"Missing 'size' parameter"`, `"CPU not started"`. Works while running too |
| `hle.thread.list` | none | `threads: [{id, name, status, statuses: ["running"/"ready"/"wait"...], pc, entry, initialStackSize, currentStackSize, priority, waitType, isCurrent}]` |
| `gpu.stats.feed` | optional `enable` | no reply; one `gpu.stats.get` event per flip (`fps`, `vblanksPerSecond`, `info`, `timing`) |
| `broadcast.config.set` | `disallowed: {logger, game, stepping, input}` | `disallowed` echoed; use it to drop `log` and `input.analog` events |
| `gpu.buffer.screenshot` | `type: "uri"` or `"base64"` | see Frames: fails here |

Events:

- `log`: `{timestamp: "mm:ss:mmm", header, message, level, channel}`.
  **Unusable in 1.20.4**: after the first 1024 log messages on a connection
  the broadcaster re-sends stale entries from the start of its ring buffer
  instead of the new ones (in a 20 s boot capture 94% of `log` events were
  repeats, out of order). Read PPSSPP's `--log=` file instead.
- `cpu.stepping` `{pc, ticks}` when execution stops (breakpoint or request);
  `cpu.resume` `{}` when it continues.

A stopping breakpoint (`enabled: true`) stops every thread; read registers
with `cpu.getAllRegs`, then send `cpu.resume`. Each stop costs about 22 ms
(one host frame), so about 45 stops per second: fine for stopping at an
event, too slow for tracing every call.

## Firmware-call source

**Choice: non-stopping log breakpoints on every import stub and on every
return site, read from PPSSPP's file log (`--log=FILE`).**

- One breakpoint per stub in `.sceStub.text` (281 stubs from `0x08B300A8`,
  all with `jal` callers), `enabled: false, log: true`, `logFormat`
  `S {pc} {[k0+0xc0]} {ra} {a0} {a1} {a2} {a3} {t0} {t1} {t2} {t3}`. PPSSPP
  rewrites each stub as `jr ra; syscall`, so at the stub the arguments are
  intact.
- One breakpoint per return site (`jal stub` + 8; 1686 sites; the EBOOT has
  no `j` tail calls to stubs, and the only data words pointing at stubs are
  the import table's own),
  `condition: "pc == ra"`, `logFormat` `R {pc} {[k0+0xc0]} {v0} {v1}`.
- `[k0+0xc0]` is the thread uid: PPSSPP points each thread's `k0` at a block
  at the top of its stack and stores the uid at `+0xC0`.
- Names: the stub address gives the NID (`tools/import_report.py`), and the
  NID the name (`tools/nid_names.txt`). Thread names: `[Log] SCEKERNELLevel
  = 4` logs every `uid=sceKernelCreateThread(name, ...)` line; `root` (275),
  `idle0` (272) and `idle1` (273) come from `hle.thread.list` at the entry
  stop.
- Pairing: per thread, an `R` line closes the newest open `S` whose `ra`
  equals its `pc`. A blocking call's `R` comes after other threads' lines.
  Callbacks run inside a waiting `...CB` call on the same thread and nest.
  Interrupt handlers run on whatever thread was current (often `idle0`,
  uid 272), so their calls carry that uid.
- `v0` is the return value. `v1` is the high word for 64-bit returns
  (`sceKernelGetSystemTimeWide`); for 32-bit returns it is leftover garbage.
  After a syscall PPSSPP fills `a0`-`t9`, `hi`, `lo` with `0xDEADBEEF`, so
  unused argument slots in `S` lines often read `deadbeef`.
- Cost: 20 s of boot gave 79,348 calls (about 4,000/s), every call paired
  with its return (the 8 left open were threads asleep at the end), and
  emulation stayed at full speed (1,196 flips in 20 s).

Line format in the file (fixed-width header; split on `BKP PC=`). Sample
lines, all values made up:

```
00:42:117              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30010: S 08b30010 00000113 08a90104 00000040 09fff000 deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef
00:42:117              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90104: R 08a90104 00000113 00000000 deadbeef
00:42:118              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30028: S 08b30028 00000113 08a90230 08b51200 08a90400 00000020 00004000 80000000 00000000 08b40000 08b7a000
00:42:118              I[SCEKERNEL]: HLE/sceKernelThread.cpp:1902 290=sceKernelCreateThread(worker, 08a90400, 00000020, 16384, 80000000, 00000000)
00:42:118              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90230: R 08a90230 00000113 00000122 08b60000
00:42:118              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30030: S 08b30030 00000113 08a90240 00000122 00000000 00000000 deadbeef deadbeef deadbeef deadbeef deadbeef
00:42:119              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08b30048: S 08b30048 00000122 08a90410 deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef deadbeef
00:42:119              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90410: R 08a90410 00000122 1234abcd 00000005
00:42:119              N[JIT]: Debugger/Breakpoints.cpp:309 BKP PC=08a90240: R 08a90240 00000113 00000000 deadbeef
00:42:120              N[BOOT]: UI/EmuScreen.cpp:391 Booted disc0:/sample.iso...
```

Why not the alternatives:

- **HLE debug log lines** (`[Log] HLELevel`/`SCE*Level = 5`). The format is
  `ret=name(args)[: note]`, with `ret` in decimal, `%08x` or as
  `SCE_KERNEL_ERROR_...`, arguments formatted per function (strings printed,
  some decimal, some hex, not always 8), `K ` before kernel-internal calls.
  Functions that log at verbose level, or not at all, print nothing in this
  release build, so only 53.7% of calls produce a line: 21 functions never
  log, among them `sceKernelCpuSuspendIntr`/
  `ResumeIntr`, `sceKernelSleepThread`, `sceKernelWakeupThread`,
  `sceKernelGetSystemTimeWide`, `sceKernelLockLwMutex`/`UnlockLwMutex`,
  `sceKernelDcacheWritebackAll`. No thread on the line (only `Context
  switch: a -> b (uidA->uidB, ...)` lines at debug level). PPSSPP's own
  helpers also appear (`K 0=sceKernelDeleteThread(...)` from the root
  thread's return path, `__IoAsyncFinish`, `ASYNC ...`), which the stub
  breakpoints never see.
- **The WebSocket `log` event**: broken, see above.
- **Stopping breakpoints** with `cpu.getAllRegs`: exact but about 22 calls
  per second with returns.

Not covered: calls from code the game loads at run time (overlays, modules)
go through other stubs. The EBOOT imports no `sceKernelLoadModule` (only
`sceUtilityLoadModule`, whose firmware modules PPSSPP emulates), and no
module was loaded in the first 20 s. Such code needs its own stub and
return-site breakpoints once it is loaded.

## Frames

- `gpu.buffer.screenshot` reads the host window's back buffer (window size,
  with scaling), not the PSP framebuffer, and on this Mac it fails with
  `"Could not download output"` on Vulkan, OpenGL and the software renderer
  alike; so does `gpu.buffer.renderColor`. Per the API, `type: "base64"`
  would give raw pixels (`width` = stride, `height`, `flipped`, `format`
  such as `R8G8B8A8_UNORM`) and `type: "uri"` a PNG `data:` URI.
- What works: `[Graphics] SoftwareRenderer = True` (PPSSPP then draws into
  emulated VRAM), stop at the frame, and `memory.read` the displayed buffer.
  `sceDisplaySetFrameBuf(topaddr, bufferwidth, pixelformat, sync)` says
  which: KH BBS alternates `0x04000000`/`0x04044000`, width 512, format 1
  (5551, 16 bits), so one frame is `512 * 272 * 2` bytes; crop to 480x272.
- Frame N = the Nth `sceDisplaySetFrameBuf` call: a stopping breakpoint on
  its stub (`0x08B307B8`), resumed N-1 times; at the Nth stop `a0` names the
  finished frame. Frame 300 was reached in 6.5 s. The game calls it once per
  flip (1,184 calls against 1,196 `gpu.stats.get` feed events in 20 s; the
  feed has no frame index, only a count). `sceDisplayWaitVblankStart` is
  called once, so it is no frame counter.
- At frame 300 the screen shows the auto-save notice that waits for a button
  press (no save data on the fresh memory stick).
- The software renderer did not change the call trace (identical to a
  Vulkan run for 79,337 calls).

## Determinism

PPSSPP's emulated time runs on its own cycle counter, so a boot with the same
ini and memory stick repeated exactly: three 20 s captures (Vulkan, Vulkan
with HLE debug logging, software renderer) agreed on every `S`/`R` line of
their common length (at least 158,666 lines), arguments and return values
included. This was only
tested up to the first input prompt. Real-time clock calls, input, save data
and I/O timing settings can make runs differ, and wall-clock timing never
matches `khbbs`. So comparisons still key on events (per-thread call order,
frame index), never on time.
