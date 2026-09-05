#!/usr/bin/env bash
# Build the v1/ and v2/ bundle directories for case84. Linux only (relies
# on ld -soname). On macOS/Windows the case is skipped in autodiscovery.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf v1 v2
mkdir -p v1 v2

# v1 — all three libs at SONAME .so.1
gcc -shared -fPIC -Wl,-soname,libonedal_core.so.1   onedal_core.c   -o v1/libonedal_core.so.1
gcc -shared -fPIC -Wl,-soname,libonedal_thread.so.1 onedal_thread.c -o v1/libonedal_thread.so.1
gcc -shared -fPIC -Wl,-soname,libonedal_dpc.so.1    onedal_dpc.c    -o v1/libonedal_dpc.so.1

# v2 — core and dpc bumped to .so.2, thread (deliberately) NOT bumped
gcc -shared -fPIC -Wl,-soname,libonedal_core.so.2   onedal_core.c   -o v2/libonedal_core.so.2
gcc -shared -fPIC -Wl,-soname,libonedal_thread.so.1 onedal_thread.c -o v2/libonedal_thread.so.1
gcc -shared -fPIC -Wl,-soname,libonedal_dpc.so.2    onedal_dpc.c    -o v2/libonedal_dpc.so.2

echo "v1/:" && ls -1 v1/
echo "v2/:" && ls -1 v2/
