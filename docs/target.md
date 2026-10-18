# Pinned target

Every address in this project (function names such as `psp_func_08A8BD58`,
data labels, overlay load addresses, trace logs, tests) refers to this exact
version. A different serial, a reprint or a patched EBOOT is a different
target and needs its own pin.

| Field | Value |
|---|---|
| Title | KINGDOM HEARTS Birth by Sleep |
| Serial (DISC_ID) | **ULUS10505** (USA, En/Fr/Es) |
| DISC_VERSION / APP_VER | **1.00** / 01.00 |
| PSP_SYSTEM_VER | 6.20 (firmware the disc's updater carries) |
| Main module | `MainApp`, module version 1.1, SDK version `0x06020010` |
| Dump | Redump-style ISO, `Kingdom Hearts - Birth by Sleep (USA) (En,Fr,Es).iso` |

## Disc image

| | |
|---|---|
| Size | 1,767,473,152 bytes |
| SHA-1 | `0a61928fc0eaa78f169b4a172ba6165ecf8564a0` |
| SHA-256 | `d4efd3d8709333760d8b7a3d1eb62fc2db074ca0d2a44983ca03d26c4db8d8ec` |
| MD5 | `962cce6f637464af8fc144c7eda97df0` |

## Executable modules

File names are those `tools/extract_game.sh` writes into `./game/`
(gitignored). The same hashes live in machine-readable form in
[`tools/target.sha1`](../tools/target.sha1), which the script checks.

| File (in `./game/`) | Disc path | Size | SHA-1 | Notes |
|---|---|---:|---|---|
| `PSP_GAME_SYSDIR_EBOOT.BIN` | `/PSP_GAME/SYSDIR/EBOOT.BIN` | 3,708,960 | `6b6107caa3a7bf1e9b640a3be18b7447e57bfc9d` | `~PSP`, encrypted, decrypt mode 9, tag `0xD91613F0` |
| `EBOOT.dec.ELF` | (decrypted EBOOT.BIN) | 3,708,616 | `fd68f8dcd544fc2ae20028dedf59a67a6f012d38` | **The recompilation input.** ELF32 MIPS `ET_EXEC`, fixed address, entry `0x08A8BD58` |
| `PSP_GAME_SYSDIR_BOOT.BIN` | `/PSP_GAME/SYSDIR/BOOT.BIN` | 3,708,616 | `3860dc0c23189092c74b613533468d3ab11dc0be` | All zero bytes (blanked retail copy, unusable) |
| `PSP_GAME_SYSDIR_OPNSSMP.BIN` | `/PSP_GAME/SYSDIR/OPNSSMP.BIN` | 880 | `5fdaae055b65fc92ed9e90ed1167a5542abc2798` | `~PSP`, mode 4, tag `0x457B0CF0`; decrypts to a 543-byte KL4E-compressed stub. Not game code |
| `PSP_GAME_USRDIR_MODULE_TITLE.ELF` | `/PSP_GAME/USRDIR/MODULE/TITLE.ELF` | 99,540 | `7a74bc556cc6a7dc38f8d483591062cee3a76634` | Overlay, plain ELF, loads at `0x08B8BF08` |
| `PSP_GAME_USRDIR_MODULE_MENU_CAMP.ELF` | `…/MODULE/MENU_CAMP.ELF` | 172,932 | `52270e934662df001238287d26d4f102a3c4eaba` | Overlay, `0x08B8BF08` |
| `PSP_GAME_USRDIR_MODULE_MENU_TERM.ELF` | `…/MODULE/MENU_TERM.ELF` | 99,740 | `9244e4890cf8a570f73f73796a97e2b74a395fc6` | Overlay, `0x08B8BF08` |
| `PSP_GAME_USRDIR_MODULE_MISSION.ELF` | `…/MODULE/MISSION.ELF` | 147,760 | `644d7986e1f2b2d865de8b4112efc73ca6fb6761` | Overlay, `0x08B8BF08` |
| `PSP_GAME_USRDIR_MODULE_SHOP.ELF` | `…/MODULE/SHOP.ELF` | 78,720 | `0956e9f32aad5e78d29ecf91c8997c3dcf897da5` | Overlay, `0x08B8BF08` |
| `PSP_GAME_USRDIR_MODULE_CHARA_REPORT.ELF` | `…/MODULE/CHARA_REPORT.ELF` | 181,984 | `267035ff404be2a69cac191884cba6e0536c2a65` | Overlay, `0x08B8BF08` |
| `PSP_GAME_USRDIR_MODULE_BATTLE_DICE.ELF` | `…/MODULE/BATTLE_DICE.ELF` | 284,952 | `e275e18a81c9f2849db9f7cd81c1659d62a7ed25` | Overlay, `0x08B8BF10` |
| `PSP_GAME_USRDIR_MODULE_FRUIT_BALL.ELF` | `…/MODULE/FRUIT_BALL.ELF` | 99,660 | `5ec4d119f2c316e5adc8f1cae187e6ac4e3e16de` | Overlay, `0x08B8BF10` |
| `PSP_GAME_USRDIR_MODULE_KEYBLADE_RIDE.ELF` | `…/MODULE/KEYBLADE_RIDE.ELF` | 205,516 | `227540ae79e54228be42bbb08c822ad819729d5b` | Overlay, `0x08B8BF10` |

Not pinned, because they are not code and not inputs to the recompiler:
`/PSP_GAME/SYSDIR/UPDATE/*` (Sony's 6.20 firmware updater, never loaded by
the game) and the `BBS0.DAT`–`BBS4.DAT` data archives. The disc hash covers
them.

## Main module memory map (from the decrypted ELF)

| Range | Section(s) | Size |
|---|---|---:|
| `0x08804000` | `.reginfo` (`$gp` = `0x08BD97E0`, but `.sdata`/`.sbss` are empty) | |
| `0x08804018`–`0x08804020` | `.init` | 8 |
| `0x08804040`–`0x08B2FE34` | `.text` | 3,325,428 |
| `0x08B2FE34` | `.fini` | 4 |
| `0x08B2FE38`–`0x08B300A8` | `.lib.ent`, `.lib.stub` marks | |
| `0x08B300A8`–`0x08B30970` | `.sceStub.text` (281 import thunks) | 2,248 |
| `0x08B30970`–`0x08B30C90` | `.cplinit` (100 C++ static-init entries) | 800 |
| `0x08B30C90` | `.rodata.sceModuleInfo` | 52 |
| `0x08B31348`–`0x08B5BCA0` | `.rodata` | 174,424 |
| `0x08B5BCA0`–`0x08B88B67` | `.data` | 183,975 |
| `0x08B88B68`–`0x08B8BF04` | `.linkonce.d` | 13,212 |
| **`0x08B8BF04`–`0x08BD1730`** | **`.overlays`** (NOBITS, overlay window) | 284,716 |
| `0x08BD1730`–`0x08BD17F0` | `.eh_frame`, `.ctors`, `.dtors` | |
| `0x08BD1800`–`0x09F3D7D0` | `.bss` | 20,365,264 |

The image therefore covers `0x08804000`–`0x09F3D7D0` of the 24 MB user
partition (`0x08800000`–`0x0A000000`), leaving 796,720 bytes (778 KB) for thread
stacks, firmware allocations and the AV modules.

## Reproducing

```bash
tools/extract_game.sh ~/Games/dumps/kh-bbs/"Kingdom Hearts - Birth by Sleep (USA) (En,Fr,Es).iso" game
```

The script refuses a disc whose SHA-1 differs from the one above, then checks
every extracted module.
