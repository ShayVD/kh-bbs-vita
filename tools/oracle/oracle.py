#!/usr/bin/env python3
"""Run khbbs and PPSSPP on the same boot and report where they disagree.

first-divergence runs, in order:
  1. khbbs under a perl alarm wrapper, with --budget and --trace OUT/khbbs.jsonl
     (and --snapshot-at EVENT --snapshot OUT/khbbs-mem.bin with --snapshot-at).
     Its output (megabytes of psprecomp warnings) goes to OUT/khbbs.log; its
     exit status and stop line are printed, not judged: 3 (budget or first miss) is the
     normal end here. Only a missing trace (or snapshot sidecar) is a failure.
  2. python3 -m oracle.capture_hle -n N -o OUT/ppsspp.jsonl (PPSSPP, isolated
     home under build/oracle/ppsspp-home).
  3. With --snapshot-at: python3 -m oracle.snapshot -o OUT/ppsspp-mem.bin.
  4. python3 -m oracle.trace_diff OUT/khbbs.jsonl OUT/ppsspp.jsonl --map-uids
     -n N; its report is printed as it is.
  5. With --snapshot-at: python3 -m oracle.mem_diff on both dumps with --elf
     and --ignore 0x08000000-0x08800000 (the kernel partition, docs/oracle.md
     "Snapshots"); its report is printed after trace_diff's. A memory
     difference (mem_diff exit 1) is reported as an "oracle: memory differs"
     line and does not change the exit status.

Every step is a subprocess started through one runner (injectable, for the
tests). The outputs of steps 1-3 (and khbbs.log) are removed before step 1, so a failed run
never leaves stale ones.

Exit status: trace_diff's (0 every compared record matches, 1 a divergence,
2 bad input), or 2 when a step fails (no khbbs trace or snapshot, capture or
PPSSPP snapshot exit non-zero, mem_diff exit 2).

Usage:
  PYTHONPATH=tools python3 -m oracle.oracle first-divergence [--iso ISO]
      --elf game/EBOOT.dec.ELF [-n 200] [--budget 200000]
      [--snapshot-at NAME:K] [--out-dir build/oracle] [--alarm 120]
      [--timeout 120] [--khbbs build/pc/hosts/pc/khbbs]
--iso defaults to $KHBBS_ISO.
"""
import argparse, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
BUILD = ROOT / "build"
HOME = BUILD / "oracle" / "ppsspp-home"
KHBBS = BUILD / "pc" / "hosts" / "pc" / "khbbs"
ALARM = "alarm shift; exec @ARGV"
KERNEL = "0x08000000-0x08800000"        # kernel partition: PPSSPP's own structures


class StepFailed(Exception):
    pass


def run(cmd, log=None):
    """Run `cmd` with tools/ on PYTHONPATH and return its exit status. With
    `log`, its stdout and stderr go to that file instead of ours."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TOOLS), env.get("PYTHONPATH")]))
    sys.stdout.flush()                       # keep our lines before the child's
    if log is None:
        return subprocess.run(cmd, env=env).returncode
    with open(log, "w", encoding="utf-8") as f:
        return subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT).returncode


def module(name, *args):
    return [sys.executable, "-m", f"oracle.{name}", *map(str, args)]


def outputs(o):
    d = pathlib.Path(o.out_dir)
    return {"khbbs": d / "khbbs.jsonl", "ppsspp": d / "ppsspp.jsonl",
            "khbbs_mem": d / "khbbs-mem.bin", "ppsspp_mem": d / "ppsspp-mem.bin",
            "khbbs_log": d / "khbbs.log"}


def sidecar(path):
    return pathlib.Path(str(path) + ".json")


def check(code, step):
    if code != 0:
        raise StepFailed(f"{step} failed (exit {code})")


def first_divergence(o, runner):
    out = outputs(o)
    out["khbbs"].parent.mkdir(parents=True, exist_ok=True)   # khbbs does not create it
    for f in out.values():
        f.unlink(missing_ok=True)
        sidecar(f).unlink(missing_ok=True)

    cmd = ["perl", "-e", ALARM, str(o.alarm), str(o.khbbs), "--elf", o.elf,
           "--trace", str(out["khbbs"]), "--budget", str(o.budget)]
    if o.snapshot_at:
        cmd += ["--snapshot-at", o.snapshot_at, "--snapshot", str(out["khbbs_mem"])]
    code = runner(cmd, log=out["khbbs_log"])
    try:
        text = out["khbbs_log"].read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    stop = [line for line in text.splitlines() if line.startswith("khbbs: stopped:")]
    print(f"oracle: khbbs exited with {code}; " + (stop[-1] if stop else "no stop line")
          + f"; its output is in {out['khbbs_log']}")
    if not out["khbbs"].is_file() or out["khbbs"].stat().st_size == 0:
        raise StepFailed(f"khbbs wrote no trace to {out['khbbs']} (exit {code})")
    if o.snapshot_at and not sidecar(out["khbbs_mem"]).is_file():
        raise StepFailed(f"khbbs wrote no snapshot at {o.snapshot_at} (no {sidecar(out['khbbs_mem'])})")

    code = runner(module("capture_hle", "--iso", o.iso, "--elf", o.elf, "-n", o.n,
                         "-o", out["ppsspp"], "--home", HOME, "--timeout", o.timeout))
    check(code, "capture_hle")
    if o.snapshot_at:
        code = runner(module("snapshot", "--iso", o.iso, "--elf", o.elf, "--at", o.snapshot_at,
                             "-o", out["ppsspp_mem"], "--home", HOME, "--timeout", o.timeout))
        check(code, "snapshot")

    status = runner(module("trace_diff", out["khbbs"], out["ppsspp"], "--map-uids", "-n", o.n))
    if o.snapshot_at:
        code = runner(module("mem_diff", out["khbbs_mem"], out["ppsspp_mem"], "--elf", o.elf,
                             "--ignore", KERNEL))
        if code == 1:
            print(f"oracle: memory differs at {o.snapshot_at} outside the kernel partition "
                  "(see mem_diff's report above)")
        elif code != 0:
            raise StepFailed(f"mem_diff failed (exit {code})")
    return status


def positive(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def main(argv=None, runner=run):
    ap = argparse.ArgumentParser(prog="oracle", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    fd = sub.add_parser("first-divergence", help="trace both sides and report the first difference")
    fd.add_argument("--iso", default=os.environ.get("KHBBS_ISO"), help="default: $KHBBS_ISO")
    fd.add_argument("--elf", required=True)
    fd.add_argument("-n", type=positive, default=200, help="records compared (default 200)")
    fd.add_argument("--budget", type=positive, default=200000,
                    help="khbbs dispatch budget (default 200000; its trace grows ~660 bytes a dispatch)")
    fd.add_argument("--snapshot-at", metavar="EVENT", help="also compare RAM just before NAME:K or 0xNID:K")
    fd.add_argument("--out-dir", default=str(BUILD / "oracle"))
    fd.add_argument("--alarm", type=positive, default=120, help="seconds before khbbs is killed")
    fd.add_argument("--timeout", type=float, default=120.0, help="seconds for each PPSSPP step")
    fd.add_argument("--khbbs", default=str(KHBBS))
    o = ap.parse_args(argv)
    if not o.iso:
        ap.error("--iso is required when $KHBBS_ISO is not set")
    try:
        return first_divergence(o, runner)
    except (StepFailed, OSError) as e:
        print(f"oracle: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
