#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# onedal-make-baseline.sh — reproduce the oneDAL ABI baseline snapshots locally,
# outside CI. Point it at an already-built oneDAL release tree; it dumps one
# abicheck snapshot per shared library (symbol + DWARF, no header parse).
#
# Usage:
#   onedal-make-baseline.sh <release-lib-dir> <version> [out-dir]
#
# Example (after `.ci/scripts/build.sh ... --debug symbols`):
#   onedal-make-baseline.sh __release_lnx/daal/latest/lib/intel64 2026.0.0
#
# Requires: abicheck on PATH (`pip install abicheck`).
set -euo pipefail

LIBDIR="${1:?usage: onedal-make-baseline.sh <release-lib-dir> <version> [out-dir]}"
VERSION="${2:?version required, e.g. 2026.0.0}"
OUT="${3:-.abicheck/baseline-${VERSION}}"

command -v abicheck >/dev/null || { echo "abicheck not found on PATH (pip install abicheck)"; exit 1; }
[[ -d "$LIBDIR" ]] || { echo "not a directory: $LIBDIR"; exit 1; }

mkdir -p "$OUT"
found=0
for so in "$LIBDIR"/libonedal*.so.*; do
  # Real files only — skip the unversioned .so and .so.4 symlinks.
  [[ -f "$so" && ! -L "$so" ]] || continue
  base="$(basename "$so")"
  stem="${base%%.so.*}"                 # libonedal_core.so.4 → libonedal_core
  echo "dumping $base → $OUT/$stem.abi.json"
  abicheck dump "$so" \
    --version "$VERSION" \
    --git-tag "$VERSION" \
    -o "$OUT/$stem.abi.json"
  found=$((found + 1))
done

[[ "$found" -gt 0 ]] || { echo "no libonedal*.so.* found under $LIBDIR"; exit 1; }
echo "wrote $found baseline snapshot(s) to $OUT/"
