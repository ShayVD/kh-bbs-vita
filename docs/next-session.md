# Next session prompt

Paste this to start the next session. Rewritten at the end of every session.

---

We're continuing the KH Birth by Sleep (PSP, ULUS10505 v1.00) → native PC/Vita port at ~/Projects/kh-bbs-vita (remote github.com/ShayVD/kh-bbs-vita). P1 is merged (PR #3). Sister project for conventions: ~/Projects/kh1-decomp.

Read first, in this order: docs/plan.md (roadmap P1–P10), docs/bringup.md (what the first boot did), docs/architecture.md, docs/setup.md, docs/toolchain.md, docs/phase0-report.md. The P1 plan in docs/superpowers/plans/2026-10-18-p1-build-skeleton.md shows the plan format we use.

## Where we are (end of session 2026-10-18)

- `cmake -S . -B build/pc -G Ninja && cmake --build build/pc` recompiles game/EBOOT.dec.ELF via our psprecomp fork (third_party/psprecomp, gitignored clone of github.com/ShayVD/psprecomp, branch khbbs, pinned at b925bd2 in tools/setup_toolchain.sh) and builds build/pc/hosts/pc/khbbs in ~12 s.
- Run: `perl -e 'alarm shift; exec @ARGV' 120 build/pc/hosts/pc/khbbs --elf game/EBOOT.dec.ELF --trace build/trace.jsonl --budget 50000000` (macOS has no `timeout`; a guest busy-wait never dispatches, so always use the alarm).
- It stops on a dispatch miss at 0x08A8BE6C: crt0's main-thread entry, passed to sceKernelCreateThread as a lui/addiu-built pointer. Function discovery never seeded it, so no code was emitted there.
- Known issues for later plans:
  - psprecomp's sysmem heap starts at 0x08C00000, inside the game image [0x08804018, 0x09F3D7D0) (P4).
  - NID 0x35669D4C (the crt0 SDK-version setter) is unimplemented.
  - The miss context has no function trace unless the generated code is built with PSPRECOMP_TRACE.

## This session

1. Write the detailed plan for P3 (recompiler fidelity) in docs/superpowers/plans/, using the writing-plans skill and the P1 plan's format. It covers:
   - discovering code reached only through pointers: lui/addiu (and lui/ori) address-pair seeding into executable sections, plus extra entry points/vtables/.cplinit seeds from a new bbs.toml;
   - a dispatch-miss log plus tools/merge_misses.py;
   - recognising the SNC `$at` switch tables (sltiu; beq; sll; lui $at; addu $at; lw $at; jr $at; 417 sites);
   - the missing VFPU ops (mtv, mfv, vrot, vcmov, vcrsp, vsge, vslt, vf2in, plus the 121 unnamed encodings) and FPU round/floor/ceil.w.s, with unit tests.

   P3 is done when there are zero psp_unimplemented sites in .text and zero dispatch misses on the boot path.
2. Write the plan for P2 (PPSSPP oracle tooling: WebSocket client, HLE-trace capture and trace_diff.py against our JSON-lines trace, memory snapshot diff, frame diff). PPSSPP 1.20.4 is at ~/Applications/PPSSPPSDL.app.
3. Show me both plans, then execute P3 with subagent-driven development: a fresh implementer per task, then a spec check, then a code-quality review, with every review finding fixed before the next task. In P1 every quality review found real bugs; keep that discipline.
4. After P3, re-run khbbs, append a new entry to docs/bringup.md, and open a PR.
5. At the end of the session, rewrite docs/next-session.md for the session after, and include it in the PR.

## Rules

- No game data, generated C, traces or keys in git. Run tools/check_no_assets.sh before every commit.
- Feature branches and PRs into main. Never push to main or force-push. I merge.
- Fork changes are committed on the fork's khbbs branch and pushed. Then bump the pin in tools/setup_toolchain.sh and docs/setup.md.
- runtime/ and generated code stay platform-free; only hosts/* touch OS or GPU APIs.
- PPSSPP is only an external oracle; never link or copy its code.
- Ask before installing anything new.
- Every address refers to ULUS10505 v1.00.
