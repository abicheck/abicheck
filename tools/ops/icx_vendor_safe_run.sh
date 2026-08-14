#!/usr/bin/env bash
set +e
set -o pipefail

ROOT=$(pwd)
OUT="$ROOT/icx-plugin-vendor-safe-results"
mkdir -p "$OUT"
exec > >(tee "$OUT/probe.log") 2>&1

source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1
ICPX=$(command -v icpx)
ICX=$(command -v icx)
LLVM_PREFIX=/usr/lib/llvm-22
BASE_SRC="$ROOT/contrib/abicheck-clang-plugin"

"$ICPX" --version | tee "$OUT/icpx-version.txt"
echo | "$ICPX" -dM -E -x c++ - \
  | grep -E '__clang_(major|minor|patchlevel|version)__|__INTEL_(LLVM|CLANG)_COMPILER' \
  | sort | tee "$OUT/icpx-version-macros.txt"

build_variant() {
  local mode="$1"
  local dir="$OUT/$mode"
  local src="$dir/src"
  local build="$dir/build"
  mkdir -p "$dir"
  cp -a "$BASE_SRC" "$src"
  python tools/ops/icx_vendor_safe_patch.py \
    "$mode" "$src/AbicheckFactsPlugin.cpp" \
    >"$dir/patch.stdout" 2>"$dir/patch.stderr"
  echo $? > "$dir/patch.rc"

  cmake -S "$src" -B "$build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Debug \
    -DCMAKE_C_COMPILER="$ICX" \
    -DCMAKE_CXX_COMPILER="$ICPX" \
    -DCMAKE_PREFIX_PATH="$LLVM_PREFIX" \
    -DCMAKE_CXX_FLAGS="-fno-rtti -stdlib=libc++ -O0 -g" \
    -DCMAKE_MODULE_LINKER_FLAGS="-stdlib=libc++" \
    >"$dir/cmake.stdout" 2>"$dir/cmake.stderr"
  echo $? > "$dir/cmake.rc"
  cmake --build "$build" -v >"$dir/build.stdout" 2>"$dir/build.stderr"
  echo $? > "$dir/build.rc"

  local plugin="$build/libabicheck-facts.so"
  if [[ ! -f "$plugin" ]]; then
    echo 127 > "$dir/smoke.rc"
    echo 127 > "$dir/dump.rc"
    echo 127 > "$dir/scan.rc"
    return
  fi
  sha256sum "$plugin" > "$dir/plugin-sha256.txt"
  ldd "$plugin" > "$dir/plugin-ldd.txt" 2>&1

  mkdir -p "$dir/fixture/include" "$dir/fixture/facts"
  cat > "$dir/fixture/include/widget.hpp" <<'CPP'
#pragma once
namespace demo {
struct widget { int value; };
int answer(widget);
}
CPP
  cat > "$dir/fixture/widget.cpp" <<'CPP'
#include "widget.hpp"
int demo::answer(widget w) { return w.value + 42; }
CPP

  "$ICPX" -std=c++17 -fPIC -shared -I"$dir/fixture/include" \
    -fplugin="$plugin" \
    -Xclang -plugin-arg-abicheck-facts -Xclang "out=$dir/fixture/facts" \
    -Xclang -plugin-arg-abicheck-facts -Xclang "public-roots=$dir/fixture/include" \
    -Xclang -plugin-arg-abicheck-facts -Xclang "library=icx_widget" \
    "$dir/fixture/widget.cpp" -o "$dir/fixture/libwidget.so" \
    >"$dir/smoke.stdout" 2>"$dir/smoke.stderr"
  local smoke_rc=$?
  echo "$smoke_rc" > "$dir/smoke.rc"
  find "$dir/fixture/facts" -type f -print -exec wc -c {} \; \
    > "$dir/facts-files.txt" 2>&1

  if [[ "$smoke_rc" -eq 0 && "$mode" != "noop" ]]; then
    abicheck dump "$dir/fixture/libwidget.so" \
      --build-info "$dir/fixture/facts" \
      -o "$dir/fixture/baseline.json" \
      >"$dir/dump.stdout" 2>"$dir/dump.stderr"
    local dump_rc=$?
    echo "$dump_rc" > "$dir/dump.rc"

    if [[ "$dump_rc" -eq 0 ]]; then
      abicheck scan "$dir/fixture/libwidget.so" \
        --against "$dir/fixture/baseline.json" \
        --build-info "$dir/fixture/facts" \
        --depth source --format json \
        >"$dir/scan.stdout" 2>"$dir/scan.stderr"
      echo $? > "$dir/scan.rc"
    else
      echo 127 > "$dir/scan.rc"
    fi
  else
    echo 127 > "$dir/dump.rc"
    echo 127 > "$dir/scan.rc"
  fi
}

build_variant nocontext
build_variant nocontext-nopp
build_variant noop

{
  printf 'variant\tpatch\tcmake\tbuild\tsmoke\tdump\tscan\n'
  for mode in nocontext nocontext-nopp noop; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$mode" \
      "$(cat "$OUT/$mode/patch.rc")" \
      "$(cat "$OUT/$mode/cmake.rc")" \
      "$(cat "$OUT/$mode/build.rc")" \
      "$(cat "$OUT/$mode/smoke.rc")" \
      "$(cat "$OUT/$mode/dump.rc")" \
      "$(cat "$OUT/$mode/scan.rc")"
  done
} | tee "$OUT/result-matrix.tsv"

for mode in nocontext nocontext-nopp noop; do
  echo "===== $mode smoke stderr ====="
  tail -160 "$OUT/$mode/smoke.stderr" 2>/dev/null
  echo "===== $mode facts ====="
  cat "$OUT/$mode/facts-files.txt" 2>/dev/null
done

exit 0
