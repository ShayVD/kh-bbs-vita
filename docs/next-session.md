# Next session prompt

Paste this to start the next session. Rewritten at the end of every session.

---

We're continuing the KH Birth by Sleep (PSP, ULUS10505 v1.00) → native PC/Vita port at ~/Projects/kh-bbs-vita (remote github.com/ShayVD/kh-bbs-vita). P3 (recompiler fidelity) is in review as the PR from branch `p3-recompiler-fidelity`; if it is merged, start from main. Sister project for conventions: ~/Projects/kh1-decomp.

Read first, in order: docs/plan.md (roadmap P1–P10), docs/bringup.md (the P1 and P3 entries), docs/architecture.md, docs/setup.md, docs/superpowers/plans/2026-10-18-p2-oracle-tooling.md (the P2 plan, not started), docs/superpowers/plans/2026-10-18-p3-recompiler-fidelity.md (the plan format and what P3 did).

## Where we are (end of session 2026-10-19)

- `cmake -S . -B build/pc -G Ninja && cmake --build build/pc` recompiles game/EBOOT.dec.ELF with our psprecomp fork (third_party/psprecomp, branch khbbs, pinned at e75f505) seeded from bbs.toml, and builds build/pc/hosts/pc/khbbs in ~13 s.
- `python3 tools/unimpl_census.py build/pc/gen` reports 0 untranslated sites and 0 unhandled branches (36 guest `break` sites, 55 undiscovered static call targets).
- Run: `perl -e 'alarm shift; exec @ARGV' 280 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --budget 50000000 --miss-log build/misses.jsonl --keep-going 1000`. Don't pass `--trace` at that budget (17.5 GB); use `--budget 200000` for traces. Exit codes: 0 returned, 3 first miss/budget, 4 after stepped-over misses, 5 guest break.
- It runs with zero dispatch misses until the budget: the `DiscInfoBind` thread loops on `sceIoOpen("umd1:")` → not found + `sceKernelDelayThreadCB`, and psprecomp's run-to-completion threads never switch back to `user_main`. A NULL write32 at 0x4 happens earlier inside 0x08AE9700 (called from crt0 at 0x08A8C0EC). psprecomp's sysmem still allocates at 0x08C00000, inside the game image.
- Dispatch misses are folded into bbs.toml with `python3 tools/merge_misses.py build/misses.jsonl` (it only trusts the first miss of a keep-going run unless `--all`).
- SNC's pspas.exe under wibo-macos hung in uninterruptible state last session (possibly after lldb attempts). If `tools/toolchain/wibo-macos tools/toolchain/pspsnc_1.2.7503.0/pspas.exe` still hangs after a reboot, say so before relying on it.
- The VFPU code has many semantics marked UNVERIFIED in the fork (prefix lane wrap for narrow ops, vcmp's untouched cc bits, vcmov kept lanes under vpfxd, sin/cos last bits, vrot/vqmul lane conventions, conversion saturation and denormals, vsrt/vsgn NaN and ±0). None of it runs on the boot path yet.

## This session

1. Execute P2 (docs/superpowers/plans/2026-10-18-p2-oracle-tooling.md) with subagent-driven development: a fresh implementer per task, then a spec check, then a code-quality review, with every review finding fixed before the next task. Task 1 is a spike against the real PPSSPP 1.20.4 (~/Applications/PPSSPPSDL.app) in an isolated home under build/oracle/ppsspp-home; do not touch my PPSSPP settings. Then the gate: the first N firmware calls of PPSSPP and khbbs are diffed automatically and the first divergence is reported.
2. Write the detailed P4 plan (kernel and scheduler: host-thread-plus-baton scheduler, the ThreadMan/LwMutex/event-flag/callback objects the boot path uses, DelayThread/vblank timing, deterministic time, sysmem placement around the image, the NULL write in 0x08AE9700), using P2's trace diff as the acceptance check. Show me the plan.
3. Append a bring-up entry for the first oracle comparison, open a PR for P2, and rewrite docs/next-session.md for the session after (include it in the PR).

## Rules

- No game data, generated C, traces or keys in git. Run tools/check_no_assets.sh before every commit.
- Feature branches and PRs into main. Never push to main or force-push. I merge.
- Fork changes are committed on the fork's khbbs branch and pushed. Then bump the pin in tools/setup_toolchain.sh and docs/setup.md.
- runtime/ and generated code stay platform-free; only hosts/* touch OS or GPU APIs.
- PPSSPP is only an external oracle; never link or copy its code.
- Ask before installing anything new.
- Every address refers to ULUS10505 v1.00.
