#!/usr/bin/env bash
# Fetch the PSP compilers, wibo and the third-party clones this project uses.
# Everything lands in gitignored directories: tools/toolchain/ and third_party/.
# Re-running is safe; finished steps are skipped. See docs/setup.md.
#
#   tools/setup_toolchain.sh            all of it
#   tools/setup_toolchain.sh compilers  only tools/toolchain/
#   tools/setup_toolchain.sh clones     only third_party/ (psprecomp and pspdecrypt)
#   tools/setup_toolchain.sh psprecomp  only clone and build third_party/psprecomp
#   tools/setup_toolchain.sh pspdecrypt only clone and build third_party/pspdecrypt
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
tc=$root/tools/toolchain
what=${1:-all}

sha256() { shasum -a 256 "$1" | cut -d' ' -f1; }

fetch() {  # url dest sha256
  local url=$1 dest=$2 want=$3
  if [[ -f "$dest" && "$(sha256 "$dest")" == "$want" ]]; then return; fi
  echo "fetching $(basename "$dest")"
  curl -fsSL -o "$dest.part" "$url"
  local have; have=$(sha256 "$dest.part")
  [[ "$have" == "$want" ]] || { echo "error: $dest: sha256 $have, expected $want" >&2; exit 1; }
  mv "$dest.part" "$dest"
}

compilers() {
  mkdir -p "$tc/dl"
  local base=https://github.com/decompme/compilers/releases/download/compilers
  # id                 sha256 of the decomp.me tarball
  while read -r id sum; do
    fetch "$base/$id.tar.gz" "$tc/dl/$id.tar.gz" "$sum"
    [[ -d "$tc/$id" ]] || { mkdir -p "$tc/$id"; tar -xzf "$tc/dl/$id.tar.gz" -C "$tc/$id"; }
  done <<'EOF'
pspsnc_1.2.7503.0 0d10a4b387e45b62369eea21b6693fc5517335033bf65e65aa7b543a916f4d6d
psp-gcc-1.3.1     788b6d2c0ed920da6b1e5a350d23dd7ea05820526e23f9b5b967b3b238ea2e2e
psp-gcc-1.7.1     55d4267b1d74855157501d5fd61ed31b68d961a737306b5831b582a2d58f3484
EOF
  # wibo runs the Win32 SNC binaries; wibo-macos runs natively (x86_64, Rosetta).
  local wb=https://github.com/decompals/wibo/releases/download/1.2.0
  fetch "$wb/wibo-macos" "$tc/wibo-macos" 2b3000ef6a7a490c24ccd71967735ae0005e218922e51806cca1b8d77fd3cf7c
  fetch "$wb/wibo-i686"  "$tc/wibo-i686"  2575d3b0a2f408b2c2b0850db56f1af5d005a138394a6774eba77b6708ecc304
  chmod +x "$tc/wibo-macos" "$tc/wibo-i686"
  # Linux-only pieces (psp-gcc 3.3.3, mips binutils) run in a container.
  if command -v docker >/dev/null; then
    docker build -q --platform linux/amd64 -t khbbs-toolchain \
      -f "$root/tools/docker/toolchain.Dockerfile" "$root/tools/docker" >/dev/null
    echo "ok: khbbs-toolchain image (tools/tc)"
  else
    echo "warning: docker not found; psp-gcc and objdump (tools/tc) will not work" >&2
  fi
  echo "ok: compilers in tools/toolchain/"
}

clone() {  # url dir commit branch [upstream-url]
  # `commit` is an abbreviated SHA. A server will not fetch an abbreviation,
  # so a stale clone fetches the branch that holds it (or every branch, when
  # `branch` is empty) and then checks it out. Anything short of HEAD being
  # the pinned commit is an error.
  local url=$1 dir=$root/third_party/$2 commit=$3 branch=$4 upstream=${5:-}
  mkdir -p "$root/third_party"
  if [[ ! -d "$dir/.git" ]]; then git clone -q "$url" "$dir"; fi
  if [[ -n "$upstream" ]] && ! git -C "$dir" remote get-url upstream >/dev/null 2>&1; then
    git -C "$dir" remote add upstream "$upstream"
  fi
  local head; head=$(git -C "$dir" rev-parse HEAD)
  if [[ "${head:0:${#commit}}" != "$commit" ]]; then
    if ! git -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null; then
      if [[ -n "$branch" ]]; then
        git -C "$dir" fetch -q origin "$branch" ||
          { echo "error: third_party/$2: cannot fetch branch $branch from origin" >&2; exit 1; }
      else
        git -C "$dir" fetch -q origin ||
          { echo "error: third_party/$2: cannot fetch from origin" >&2; exit 1; }
      fi
    fi
    git -C "$dir" checkout -q "$commit" ||
      { echo "error: third_party/$2: cannot check out $commit${branch:+ (branch $branch)}" >&2; exit 1; }
    head=$(git -C "$dir" rev-parse HEAD)
  fi
  # The full SHA's prefix, not `rev-parse --short`, which lengthens an
  # abbreviation that has become ambiguous.
  if [[ "${head:0:${#commit}}" != "$commit" ]]; then
    echo "error: third_party/$2 is at ${head:0:12}, the pin is $commit" >&2
    exit 1
  fi
  echo "ok: third_party/$2 at $commit"
}

# Pinned commits; bump them here and in docs/setup.md together.
psprecomp() {
  # Our fork (branch khbbs) of github.com/sp00nznet/psprecomp, based on caca759.
  # Built as the top-level project, so its own tests are built too: CI runs
  # them with ctest --test-dir third_party/psprecomp/build.
  clone https://github.com/ShayVD/psprecomp psprecomp e75f505 khbbs https://github.com/sp00nznet/psprecomp
  cmake -S "$root/third_party/psprecomp" -B "$root/third_party/psprecomp/build" >/dev/null
  cmake --build "$root/third_party/psprecomp/build" >/dev/null
  echo "ok: psprecomp built (with its tests)"
}

pspdecrypt() {
  clone https://github.com/John-K/pspdecrypt pspdecrypt c156627 ""
  local ssl; ssl=$(brew --prefix openssl@3 2>/dev/null || echo /usr)
  make -s -C "$root/third_party/pspdecrypt" -j8 \
    CFLAGS="-O2 -I$ssl/include" CXXFLAGS="-O2 -std=c++11 -I$ssl/include" EXTRA_FLAG="-L$ssl/lib"
  echo "ok: pspdecrypt built"
}

clones() { psprecomp; pspdecrypt; }

case $what in
  all) compilers; clones ;;
  compilers) compilers ;;
  clones) clones ;;
  psprecomp) psprecomp ;;
  pspdecrypt) pspdecrypt ;;
  *) echo "usage: $0 [all|compilers|clones|psprecomp|pspdecrypt]" >&2; exit 2 ;;
esac
