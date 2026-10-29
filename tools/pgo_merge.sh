#!/usr/bin/env bash
# Merges the training run's profiles (an ISSN_PGO=generate build writes <user>/pgo/issn-*.profraw)
# into pgo-windows/issn-windows.profdata, which the next `bash build.sh` uses (MSYS2 CLANG64:
# llvm-profdata comes with clang).
#   bash tools/pgo_merge.sh /c/Users/Joao/Downloads/issn-windows/user/pgo
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
src=${1:?usage: tools/pgo_merge.sh <folder with issn-*.profraw>}
shopt -s nullglob
files=("$src"/issn-*.profraw)
(( ${#files[@]} )) || { echo "No issn-*.profraw in $src" >&2; exit 1; }
mkdir -p pgo-windows
llvm-profdata merge -o pgo-windows/issn-windows.profdata "${files[@]}"
llvm-profdata show pgo-windows/issn-windows.profdata | tail -3
echo "pgo-windows/issn-windows.profdata from ${#files[@]} run(s): bash build.sh uses it from now on"
