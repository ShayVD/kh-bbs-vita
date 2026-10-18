#!/usr/bin/env bash
# Refuse to commit game data or key material. Fails if any tracked or staged
# file is an ELF/PRX/~PSP module, a disc image, a PGD or bbsa archive, a key
# file, or larger than 512 KiB. Run in CI and as a pre-commit hook:
#   ln -s ../../tools/check_no_assets.sh .git/hooks/pre-commit
# Same rules as kh1-decomp's script, plus the PSP container formats.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

filesize() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }

fail=0
while IFS= read -r -d '' f; do
  [[ -f "$f" ]] || continue
  size=$(filesize "$f")
  magic=$(head -c 4 "$f" | od -An -tx1 | tr -d ' \n')
  case "$magic" in
    7f454c46) echo "refused: $f is an ELF binary"; fail=1; continue ;;
    7e505350) echo "refused: $f is a ~PSP module"; fail=1; continue ;;
    7e534345) echo "refused: $f is a ~SCE module"; fail=1; continue ;;
    00504744) echo "refused: $f is a PGD-encrypted file"; fail=1; continue ;;
    00504250) echo "refused: $f is a PBP"; fail=1; continue ;;
    62627361) echo "refused: $f is a bbsa archive"; fail=1; continue ;;
    4b4c3445) echo "refused: $f is a KL4E-compressed module"; fail=1; continue ;;
  esac
  if head -c 40000 "$f" | tail -c +32770 | head -c 5 | grep -q "CD001"; then
    echo "refused: $f looks like a disc image"; fail=1
  elif [[ "$f" == keys/* || "$f" == *_keys.txt ]] && [[ "$f" != *example* ]]; then
    echo "refused: $f looks like a key file"; fail=1
  elif (( size > 524288 )); then
    echo "refused: $f is $size bytes (game data?)"; fail=1
  fi
done < <(git ls-files -z --cached)
exit $fail
