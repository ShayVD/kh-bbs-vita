# The game's compiler

**Result: the game code was compiled with SN Systems SNC for PSP, at `-O2`
(or `-O3`; they are not told apart yet). It is not Sony's psp-gcc.** This
corrects the Phase 0 working hypothesis ("SN's GCC 3.3.6"), which was
inferred from crt0's `sceKernelSetCompilerVersion(0x00030306)` and turned out
to be wrong for the game code.

## Method

Same as kh1-decomp: hand-write C for small functions of the pinned EBOOT, compile
it with each candidate, and compare word for word, with relocated fields
masked (`jal` targets, `%hi`/`%lo` halves). The probes are in
[`tests/toolchain/probes.c`](../tests/toolchain/probes.c). They are our own
code, written from the disassembly, and contain no game bytes.

```bash
python3 tools/probe_match.py game/EBOOT.dec.ELF tests/toolchain/probes.c --cc snc --flags=-O1 --flags=-O2 --flags=-O3
python3 tools/probe_match.py game/EBOOT.dec.ELF tests/toolchain/probes.c --cc gcc --flags=-O1 --flags=-O2
```

| Compiler | Flags | `088040B0` 3-arg call | `08804210` store + cond. call | `08804238` call | `08804364` list unlink |
|---|---|---|---|---|---|
| pspsnc 1.2.7503.0 | `-O1` | DIFF | DIFF | match | DIFF |
| **pspsnc 1.2.7503.0** | **`-O2`** | **match** | **match** | **match** | **match** |
| pspsnc 1.2.7503.0 | `-O3` | match | match | match | match |
| psp-gcc 1.3.1 (GCC 3.3.3) | `-O1`, `-O2` | DIFF | DIFF | match | DIFF |

The tell-tale differences, also visible across the whole EBOOT:

| Idiom | SNC | psp-gcc | Game `.text` |
|---|---|---|---|
| register move | `or rd, rs, $zero` | `addu rd, rs, $zero` | 94,059 `or` / 1,359 `addu` |
| saved registers | `$s*` from `0($sp)` up, `$ra` after them | `$ra` at the top of the save area | both shapes, mostly SNC's |
| `li 0` in a delay slot | `ori rd, $zero, 0` | `addiu`/`move` | `ori` |
| switch | `sltiu; beq; sll; lui $at; addu $at; lw $at; jr $at; nop` | different scheduling, no `$at` | the SNC form, exactly |

## What is not SNC

The 1,359 `addu` moves sit in about 100 functions, mostly in the
`0x08A…` range around the entry point. crt0 (`0x08A8BD58`) is one of them. It
is the code that reports compiler version `0x030306`, which is consistent with
Sony's SDK startup and library objects being GCC-built and linked into an SNC
game. Those functions will be matched with psp-gcc (or treated as SDK library
code and not decompiled, the way kh1-decomp treats its SCE libraries). Which
functions they are will be mapped when the decomp layout is generated.

## Still open

- **`-O2` versus `-O3`**: needs probes where inlining or unrolling differs.
- **The exact SNC build**: 1.2.7503.0 is the only PSP SNC on decomp.me. The
  game (SDK 6.20, 2010) may have used a later build. Bigger probes (C++
  methods, float/VFPU code, larger switches) will show whether 1.2.7503.0 is
  close enough. If not, we need another SNC build.
- **Flags beyond `-O`** (`-Xfastmath`, `-Xsched`, char signedness, …): set
  per probe as the decomp starts.
