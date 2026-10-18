# Development setup

What a fresh checkout needs on the Mac host, where each piece lives and which
version is pinned. Nothing listed here is committed: third-party clones,
compilers and game files are all gitignored.

## One command

```bash
tools/setup_toolchain.sh
tools/extract_game.sh ~/Games/dumps/kh-bbs/"Kingdom Hearts - Birth by Sleep (USA) (En,Fr,Es).iso" game
```

The first fetches and checks (SHA-256) the compilers, clones and builds the
pinned third-party tools. The second extracts and decrypts the modules and
checks them against [`tools/target.sha1`](../tools/target.sha1).

## Third-party clones (`third_party/`, gitignored)

Pinned in `tools/setup_toolchain.sh`. Bump the commit there and here
together.

| Directory | Upstream | Commit | Licence | Used for |
|---|---|---|---|---|
| `third_party/psprecomp` | github.com/sp00nznet/psprecomp | `caca759` | MIT | recompiler (`allegrexrecomp`) and runtime base |
| `third_party/pspdecrypt` | github.com/John-K/pspdecrypt | `c156627` | GPL-3.0 | decrypting `EBOOT.BIN`, **as a separate process only**; it is never linked |
| `third_party/vdpm` | VitaSDK package manager | (as installed) | | installing VitaSDK packages |

pspdecrypt links Homebrew's prebuilt `openssl@3` (already installed); the
script finds it with `brew --prefix openssl@3`.

When we start changing psprecomp (plan P1), the clone's `origin` moves to our
fork and the pin follows our fork's commits. It stays a gitignored clone
either way.

## Compilers (`tools/toolchain/`, gitignored)

Fetched from decomp.me's compiler archive (github.com/decompme/compilers,
release `compilers`), the same source kh1-decomp uses for ee-gcc.

| Id | What | Runs via |
|---|---|---|
| `pspsnc_1.2.7503.0` | SN Systems SNC for PSP, **the game's compiler** (see [toolchain.md](toolchain.md)) | `tools/toolchain/wibo-macos` (native, x86_64 under Rosetta) |
| `psp-gcc-1.3.1` | Sony SDK GCC 3.3.3 + allegrex patches, binutils 2.14 | the `khbbs-toolchain` container (`tools/tc`) |
| `psp-gcc-1.7.1` | Sony SDK GCC 3.3.3 (allegrex 2.2.2); its tarball has an empty `as` | container; kept for comparison only |
| `wibo` 1.2.0 | Win32 loader (decompals/wibo), `wibo-macos` and `wibo-i686` | |

## Toolchain container

`tools/docker/toolchain.Dockerfile` is a Debian 12 amd64 image with the i386
C runtime, `binutils-mips-linux-gnu` and python3. It runs under the existing
colima VM (Virtualization.framework, aarch64, amd64 via Rosetta). `tools/tc`
runs one command in it, with the repo mounted at `/work` and the current
directory preserved:

```bash
tools/tc mips-linux-gnu-objdump -d -M no-aliases build/probe/m.o
```

## Applications and SDKs

| Tool | Version | Location | Notes |
|---|---|---|---|
| PPSSPP | 1.20.4 (official macOS build, notarized, Developer ID "Millionth Line AB") | `~/Applications/PPSSPPSDL.app` | the oracle; run as a separate process |
| Temurin JDK | 21.0.12.1+1 (SHA-256 checked against Adoptium) | `~/Tools/jdk-21.0.12.1+1/Contents/Home` | for Ghidra; set `JAVA_HOME` to it |
| Ghidra | 12.1.3 (SHA-256 checked against the release notes) | `~/Tools/ghidra_12.1.3_PUBLIC` | |
| ghidra-allegrex | v21.4 (built for Ghidra 12.1.3) | `~/Tools/ghidra_12.1.3_PUBLIC/Ghidra/Extensions/ghidra-allegrex` | headless import of `EBOOT.dec.ELF` selects `Allegrex:LE:32:default` |
| VitaSDK | 2026.08 | `$VITASDK` = `/usr/local/vitasdk` | vitaGL, vitaShaRK, libmathneon, SDL2, imgui, … installed |
| Homebrew SDL2, openssl@3 | prebuilt | `/opt/homebrew` | no source builds |

Headless Ghidra:

```bash
JAVA_HOME=~/Tools/jdk-21.0.12.1+1/Contents/Home \
  ~/Tools/ghidra_12.1.3_PUBLIC/support/analyzeHeadless <project-dir> <name> \
  -import game/EBOOT.dec.ELF
```

## Hygiene

- `tools/check_no_assets.sh` runs in CI. Install it as a pre-commit hook with
  `ln -s ../../tools/check_no_assets.sh .git/hooks/pre-commit`.
- Scratch output goes under `build/` (gitignored).
