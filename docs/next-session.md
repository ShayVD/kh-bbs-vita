# Next session prompt

Paste this to start the next session. Rewritten at the end of every session.

---

We're continuing the KH Birth by Sleep (PSP, ULUS10505 v1.00) → native PC/Vita port at ~/Projects/kh-bbs-vita (remote github.com/ShayVD/kh-bbs-vita). P1, P2 (PRs #7, #8) and P3 (PR #5) are merged. The P4 plan is in the PR from branch `p4-kernel-scheduler-plan`; if it is merged, start from main. Sister project for conventions: ~/Projects/kh1-decomp.

Read first, in order: docs/plan.md, docs/superpowers/plans/2026-10-19-p4-kernel-scheduler.md (the P4 plan; read its header and Decisions before anything else), docs/bringup.md (the P1, P3 and "P2, first oracle comparison" entries), docs/oracle.md, docs/architecture.md, docs/setup.md.

## Where we are (end of session 2026-10-19)

- `cmake -S . -B build/pc -G Ninja && cmake --build build/pc` builds khbbs (~13 s) with the psprecomp fork pinned at 34bab3c (branch khbbs). CMake ≥ 3.20.
- Tests: `python3 -m unittest discover -s tests/tools` (268 OK), `ctest --test-dir build/pc --output-on-failure` (16/16).
- P2's gate works: `PYTHONPATH=tools python3 -m oracle.oracle first-divergence --iso "$KHBBS_ISO" --elf game/EBOOT.dec.ELF -n 200` (about 3 s; `KHBBS_ISO` is ~/Games/dumps/kh-bbs/"Kingdom Hearts - Birth by Sleep (USA) (En,Fr,Es).iso"). Result: 4 of 5 records match. The first divergence is record 4: `sceKernelGetBlockHeadAddr` returns 0x08C00000 in khbbs (psprecomp's sysmem allocates inside the image) and 0x09F3E000 in PPSSPP. `--snapshot-at sceKernelCreateThread:1` adds a RAM diff: `.data` and `.bss` match. Frame capture (`tools/oracle/frame.py`) reads PPSSPP's VRAM.
- The P4 plan has 18 tasks: fork hooks (VFPU context save/load, back-edge countdown, call counter, sysmem range/reserve, pspsdk error values), then the host interface and POSIX backend, the baton scheduler with `idle0`, thread layout (PPSSPP's k0 block, arg copy, stub), stop paths with per-thread trampolines, ThreadMan, callbacks, time (det/real, `--time-limit`), vblank, alarms, sync objects, mutexes and LwMutexes, the trace `thread` field, the P5 slice (UMD, power, impose, an ISO9660 file layer with async reads), `trace_diff --until`, and the gate. Its core code was built and run once in a scratch copy, where the 42 records of the P4 gate matched PPSSPP's decoded log. The scratch copy was not kept: the tasks rebuild it.
- Decided on 2026-10-19:
  - P4's gate is `--until sceKernelUnlockLwMutex:3`. The first `sceGeListEnQueue` gate moves to P5, because it needs PGD decryption.
  - The P5 slice is pulled into P4.
  - The three wrong `SCE_KERNEL_ERROR_*` values are fixed in the fork, in P4 Task 3.
  - The mutex and LwMutex error codes stay UNVERIFIED.
- Correction to the P3 bring-up entry, made in P4 Task 18: the "NULL write32 at 0x4 inside 0x08AE9700" is really `sw $s5, 4($k0)` at 0x08A8BF3C in `user_main`, with `$k0 = 0`. 0x08AE9700 is a memset.
- Disk is about 95% full. build/oracle holds ~650 MB of old traces (`run-*.jsonl`, `khbbs.jsonl` 131 MB), safe to delete. A khbbs trace is ~660 bytes per dispatch.

## This session

1. Before anything, check that no other Claude session is still writing to ~/Projects/kh-bbs-vita: list the sessions and look for files changing that you did not touch. Last session, a background agent of the previous session kept committing for an hour after its session showed "idle".
2. Execute the P4 plan with subagent-driven development: a fresh implementer per task, then a spec check, then a code-quality review, with every review finding fixed before the next task. Only one agent writes to a checkout at a time. Reviewers build in their own scratch dirs. Work on branch `p4-kernel-scheduler` from main.
3. At the end: run both gates (Task 18), write the bring-up entry, open the PR, and rewrite docs/next-session.md in that PR.

## Rules

- No game data, generated C, traces or keys in git. Run tools/check_no_assets.sh before every commit.
- Feature branches and PRs into main. Never push to main or force-push. I merge.
- Fork changes are committed on the fork's khbbs branch and pushed. Then bump the pin in tools/setup_toolchain.sh and docs/setup.md.
- runtime/ and generated code stay platform-free; only hosts/* touch OS or GPU APIs.
- PPSSPP is only an external oracle; never link or copy its code. Run it only through tools/oracle (isolated home, SIGKILL only PIDs you started).
- SNC's pspas.exe hung once; always run it under the perl alarm wrapper.
- Ask before installing anything new.
- Every address refers to ULUS10505 v1.00.
