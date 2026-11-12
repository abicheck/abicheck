#!/usr/bin/env bash
# Build the old/new fixtures, run the old consumers against both library
# versions, and run abicheck on each pair. Usage: run_experiment.sh [OUT_DIR]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$(mktemp -d)}"
CC="${CC:-gcc}"
CFLAGS="-g -O0 -fno-strict-aliasing"
mkdir -p "$OUT"/{exposed,opaque}/{v1,v2} "$OUT/bin"

for v in 1 2; do
  def=""; [ "$v" = 2 ] && def="-DREC_V2"
  $CC $CFLAGS -shared -fPIC $def -I"$HERE/exposed/v$v" "$HERE/exposed/rec.c" \
      -Wl,-soname,librec.so -o "$OUT/exposed/v$v/librec.so"
  $CC $CFLAGS -shared -fPIC $def -I"$HERE/opaque/v$v" "$HERE/opaque/orec.c" \
      -Wl,-soname,liborec.so -o "$OUT/opaque/v$v/liborec.so"
done

# Consumers are compiled and linked ONCE, against v1.
for c in reader_b reader_a accessor_user; do
  $CC $CFLAGS -I"$HERE/exposed/v1" "$HERE/consumers/$c.c" -L"$OUT/exposed/v1" -lrec -o "$OUT/bin/$c"
done
$CC $CFLAGS -I"$HERE/opaque/v1" "$HERE/consumers/opaque_user.c" -L"$OUT/opaque/v1" -lorec -o "$OUT/bin/opaque_user"

echo "consumer        lib=v1  lib=v2"
for c in reader_b reader_a accessor_user; do
  r1=$(LD_LIBRARY_PATH="$OUT/exposed/v1" "$OUT/bin/$c")
  r2=$(LD_LIBRARY_PATH="$OUT/exposed/v2" "$OUT/bin/$c")
  printf "%-15s %-7s %s\n" "$c" "$r1" "$r2"
done
r1=$(LD_LIBRARY_PATH="$OUT/opaque/v1" "$OUT/bin/opaque_user")
r2=$(LD_LIBRARY_PATH="$OUT/opaque/v2" "$OUT/bin/opaque_user")
printf "%-15s %-7s %s\n" opaque_user "$r1" "$r2"

for pair in exposed:rec opaque:orec; do
  d="${pair%%:*}"; h="${pair##*:}"
  echo "== abicheck compare ($d) =="
  set +e
  abicheck compare "$OUT/$d/v1/lib$h.so" "$OUT/$d/v2/lib$h.so" \
    --header "old=$HERE/$d/v1/$h.h" --header "new=$HERE/$d/v2/$h.h" \
    -o "json=$OUT/$d.json" >"$OUT/$d.txt" 2>"$OUT/$d.err"
  echo "exit=$?"
  set -e
done
echo "outputs in $OUT"
