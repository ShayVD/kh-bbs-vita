#!/usr/bin/env bash
# Extract and decrypt the executable modules of the pinned target
# (ULUS10505 v1.00, see docs/target.md) from your own disc image, and check
# every file against tools/target.sha1.
#
#   tools/extract_game.sh <disc.iso> <outdir>
#
# Tools are found through the environment, defaulting to the gitignored clones:
#   ALLEGREXRECOMP  third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp
#   PSPDECRYPT      third_party/pspdecrypt/pspdecrypt   (GPL-3.0, run as a separate process)
# The output directory must be gitignored (the default ./game/ is).
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <disc.iso> <outdir>" >&2
  exit 2
fi
iso=$1
out=$2
root=$(cd "$(dirname "$0")/.." && pwd)
recomp=${ALLEGREXRECOMP:-$root/third_party/psprecomp/build/tools/allegrexrecomp/allegrexrecomp}
decrypt=${PSPDECRYPT:-$root/third_party/pspdecrypt/pspdecrypt}
sums=$root/tools/target.sha1

for t in "$recomp" "$decrypt"; do
  [[ -x "$t" ]] || { echo "error: $t not found or not executable" >&2; exit 1; }
done
mkdir -p "$out"

echo "checking disc image (SHA-1)..."
have=$(shasum -a 1 "$iso" | cut -d' ' -f1)
want=$(awk '$2 == "disc.iso" {print $1}' "$sums")
if [[ "$have" != "$want" ]]; then
  echo "error: $iso has SHA-1 $have, expected $want (ULUS10505 v1.00)" >&2
  exit 1
fi

for f in /PSP_GAME/PARAM.SFO /PSP_GAME/SYSDIR/BOOT.BIN /PSP_GAME/SYSDIR/EBOOT.BIN \
         /PSP_GAME/SYSDIR/OPNSSMP.BIN /PSP_GAME/USRDIR/MODULE/; do
  "$recomp" extract "$iso" "$f" "$out" >/dev/null
done
"$decrypt" "$out/PSP_GAME_SYSDIR_EBOOT.BIN" -o "$out/EBOOT.dec.ELF"

echo "checking extracted modules (SHA-1)..."
(cd "$out" && grep -v ' disc.iso$' "$sums" | shasum -a 1 -c -)
