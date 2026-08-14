#!/usr/bin/env bash
set +e
set -o pipefail

ROOT=$(pwd)
OUT="$ROOT/icx-intel-llvm-sdk-results"
mkdir -p "$OUT"
exec > >(tee "$OUT/probe.log") 2>&1

source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1
ICPX=$(command -v icpx)
ICX=$(command -v icx)
PRODUCT_FRONTEND=/opt/intel/oneapi/compiler/latest/bin/compiler/clang

"$ICPX" --version | tee "$OUT/product-icpx-version.txt"
echo | "$ICPX" -dM -E -x c++ - \
  | grep -E '__clang_(major|minor|patchlevel|version)__|__INTEL_(LLVM|CLANG)_COMPILER' \
  | sort | tee "$OUT/product-version-macros.txt"
sha256sum "$ICPX" "$PRODUCT_FRONTEND" | tee "$OUT/product-compiler-sha256.txt"

ARCHIVE="$OUT/sycl_linux_2026-07-24.tar.gz"
URL=https://github.com/intel/llvm/releases/download/nightly-2026-07-24/sycl_linux.tar.gz
EXPECTED=60e1c2b706dcd643c6c04a5b5663d1a05885b03f265e2c29efe9dc5b5366986c
wget -q --show-progress --progress=dot:giga -O "$ARCHIVE" "$URL" \
  >"$OUT/download.stdout" 2>"$OUT/download.stderr"
echo $? > "$OUT/download.rc"
sha256sum "$ARCHIVE" | tee "$OUT/archive-sha256.txt"
ACTUAL=$(sha256sum "$ARCHIVE" | awk '{print $1}')
[[ "$ACTUAL" == "$EXPECTED" ]]
echo $? > "$OUT/archive-verify.rc"

tar -tzf "$ARCHIVE" > "$OUT/archive-list.txt" 2>"$OUT/archive-list.stderr"
echo $? > "$OUT/archive-list.rc"
grep -E '(^|/)(LLVMConfig.cmake|ClangConfig.cmake|FrontendPluginRegistry.h|ASTContext.h|llvm-config.h|libclang-cpp|libLLVM|bin/(clang\+\+|clang|icpx))$' \
  "$OUT/archive-list.txt" | tee "$OUT/sdk-candidates-in-archive.txt"

SDK="$OUT/sdk"
mkdir -p "$SDK"
tar -xzf "$ARCHIVE" -C "$SDK" >"$OUT/extract.stdout" 2>"$OUT/extract.stderr"
echo $? > "$OUT/extract.rc"

find "$SDK" -type f \( \
  -name LLVMConfig.cmake -o \
  -name ClangConfig.cmake -o \
  -name FrontendPluginRegistry.h -o \
  -name ASTContext.h -o \
  -name llvm-config.h -o \
  -name 'libclang-cpp.so*' -o \
  -name 'libLLVM*.so*' \
\) -print | sort | tee "$OUT/sdk-dev-files.txt"

NIGHTLY_CLANGXX=$(find "$SDK" -type f -path '*/bin/clang++' -perm -111 | head -1)
NIGHTLY_CLANG=$(find "$SDK" -type f -path '*/bin/clang' -perm -111 | head -1)
printf 'nightly_clang=%s\nnightly_clangxx=%s\n' "$NIGHTLY_CLANG" "$NIGHTLY_CLANGXX" \
  | tee "$OUT/nightly-paths.txt"

if [[ -n "$NIGHTLY_CLANGXX" ]]; then
  "$NIGHTLY_CLANGXX" --version | tee "$OUT/nightly-clang-version.txt"
  echo | "$NIGHTLY_CLANGXX" -dM -E -x c++ - \
    | grep -E '__clang_(major|minor|patchlevel|version)__|__INTEL_(LLVM|CLANG)_COMPILER' \
    | sort | tee "$OUT/nightly-version-macros.txt"
  sha256sum "$NIGHTLY_CLANGXX" "$NIGHTLY_CLANG" | tee "$OUT/nightly-compiler-sha256.txt"
fi

LLVM_CONFIG=$(find "$SDK" -type f -name LLVMConfig.cmake | head -1)
CLANG_CONFIG=$(find "$SDK" -type f -name ClangConfig.cmake | head -1)
PLUGIN_HEADER=$(find "$SDK" -type f -name FrontendPluginRegistry.h | head -1)
LLVM_CONFIG_HEADER=$(find "$SDK" -type f -name llvm-config.h | head -1)
printf 'llvm_config=%s\nclang_config=%s\nplugin_header=%s\nllvm_config_header=%s\n' \
  "$LLVM_CONFIG" "$CLANG_CONFIG" "$PLUGIN_HEADER" "$LLVM_CONFIG_HEADER" \
  | tee "$OUT/sdk-resolution.txt"

CMAKE_RC=127
DIRECT_RC=127
PRODUCT_SMOKE_RC=127
NIGHTLY_SMOKE_RC=127
PRODUCT_DUMP_RC=127
PRODUCT_SCAN_RC=127
NIGHTLY_DUMP_RC=127
NIGHTLY_SCAN_RC=127
PLUGIN=""

if [[ -n "$LLVM_CONFIG" && -n "$CLANG_CONFIG" ]]; then
  LLVM_CMAKE_DIR=$(dirname "$LLVM_CONFIG")
  CLANG_CMAKE_DIR=$(dirname "$CLANG_CONFIG")
  PREFIX=$(dirname "$LLVM_CMAKE_DIR")
  BUILD="$OUT/build-cmake"
  cmake -S contrib/abicheck-clang-plugin -B "$BUILD" -G Ninja \
    -DCMAKE_BUILD_TYPE=Debug \
    -DCMAKE_C_COMPILER="$ICX" \
    -DCMAKE_CXX_COMPILER="$ICPX" \
    -DLLVM_DIR="$LLVM_CMAKE_DIR" \
    -DClang_DIR="$CLANG_CMAKE_DIR" \
    -DCMAKE_CXX_FLAGS="-fno-rtti -stdlib=libc++ -O0 -g" \
    -DCMAKE_MODULE_LINKER_FLAGS="-stdlib=libc++" \
    >"$OUT/cmake.stdout" 2>"$OUT/cmake.stderr"
  CMAKE_RC=$?
  echo "$CMAKE_RC" > "$OUT/cmake.rc"
  cmake --build "$BUILD" -v >"$OUT/cmake-build.stdout" 2>"$OUT/cmake-build.stderr"
  echo $? > "$OUT/cmake-build.rc"
  [[ -f "$BUILD/libabicheck-facts.so" ]] && PLUGIN="$BUILD/libabicheck-facts.so"
fi

if [[ -z "$PLUGIN" && -n "$PLUGIN_HEADER" && -n "$LLVM_CONFIG_HEADER" ]]; then
  CLANG_INCLUDE=${PLUGIN_HEADER%/clang/Frontend/FrontendPluginRegistry.h}
  LLVM_INCLUDE=${LLVM_CONFIG_HEADER%/llvm/Config/llvm-config.h}
  DIRECT_BUILD="$OUT/build-direct"
  mkdir -p "$DIRECT_BUILD"
  "$ICPX" -std=c++17 -shared -fPIC -fno-rtti -stdlib=libc++ -O0 -g \
    -I"$CLANG_INCLUDE" -I"$LLVM_INCLUDE" \
    -D_GNU_SOURCE -D__STDC_CONSTANT_MACROS -D__STDC_FORMAT_MACROS -D__STDC_LIMIT_MACROS \
    contrib/abicheck-clang-plugin/AbicheckFactsPlugin.cpp \
    -o "$DIRECT_BUILD/libabicheck-facts.so" \
    >"$OUT/direct-build.stdout" 2>"$OUT/direct-build.stderr"
  DIRECT_RC=$?
  echo "$DIRECT_RC" > "$OUT/direct-build.rc"
  [[ -f "$DIRECT_BUILD/libabicheck-facts.so" ]] && PLUGIN="$DIRECT_BUILD/libabicheck-facts.so"
fi

run_flow() {
  local label="$1" compiler="$2"
  local dir="$OUT/$label"
  mkdir -p "$dir/include" "$dir/facts"
  cat > "$dir/include/widget.hpp" <<'CPP'
#pragma once
namespace demo {
struct widget { int value; };
int answer(widget);
}
CPP
  cat > "$dir/widget.cpp" <<'CPP'
#include "widget.hpp"
int demo::answer(widget w) { return w.value + 42; }
CPP

  "$compiler" -std=c++17 -fPIC -shared -I"$dir/include" \
    -fplugin="$PLUGIN" \
    -Xclang -plugin-arg-abicheck-facts -Xclang "out=$dir/facts" \
    -Xclang -plugin-arg-abicheck-facts -Xclang "public-roots=$dir/include" \
    -Xclang -plugin-arg-abicheck-facts -Xclang "library=${label}_widget" \
    "$dir/widget.cpp" -o "$dir/libwidget.so" \
    >"$dir/smoke.stdout" 2>"$dir/smoke.stderr"
  local smoke_rc=$?
  echo "$smoke_rc" > "$dir/smoke.rc"
  find "$dir/facts" -type f -print -exec wc -c {} \; > "$dir/facts-files.txt" 2>&1

  local dump_rc=127 scan_rc=127
  if [[ "$smoke_rc" -eq 0 ]]; then
    abicheck dump "$dir/libwidget.so" --build-info "$dir/facts" \
      -o "$dir/baseline.json" >"$dir/dump.stdout" 2>"$dir/dump.stderr"
    dump_rc=$?
    if [[ "$dump_rc" -eq 0 ]]; then
      abicheck scan "$dir/libwidget.so" --against "$dir/baseline.json" \
        --build-info "$dir/facts" --depth source --format json \
        >"$dir/scan.stdout" 2>"$dir/scan.stderr"
      scan_rc=$?
    fi
  fi
  echo "$dump_rc" > "$dir/dump.rc"
  echo "$scan_rc" > "$dir/scan.rc"
}

if [[ -n "$PLUGIN" ]]; then
  realpath "$PLUGIN" | tee "$OUT/plugin-path.txt"
  sha256sum "$PLUGIN" | tee "$OUT/plugin-sha256.txt"
  ldd "$PLUGIN" | tee "$OUT/plugin-ldd.txt"
  run_flow product "$ICPX"
  PRODUCT_SMOKE_RC=$(cat "$OUT/product/smoke.rc")
  PRODUCT_DUMP_RC=$(cat "$OUT/product/dump.rc")
  PRODUCT_SCAN_RC=$(cat "$OUT/product/scan.rc")
  if [[ -n "$NIGHTLY_CLANGXX" ]]; then
    run_flow nightly "$NIGHTLY_CLANGXX"
    NIGHTLY_SMOKE_RC=$(cat "$OUT/nightly/smoke.rc")
    NIGHTLY_DUMP_RC=$(cat "$OUT/nightly/dump.rc")
    NIGHTLY_SCAN_RC=$(cat "$OUT/nightly/scan.rc")
  fi
fi

{
  printf 'test\trc\n'
  printf 'download\t%s\n' "$(cat "$OUT/download.rc")"
  printf 'archive_verify\t%s\n' "$(cat "$OUT/archive-verify.rc")"
  printf 'extract\t%s\n' "$(cat "$OUT/extract.rc")"
  printf 'cmake_configure\t%s\n' "$CMAKE_RC"
  printf 'direct_build\t%s\n' "$DIRECT_RC"
  printf 'product_smoke\t%s\n' "$PRODUCT_SMOKE_RC"
  printf 'product_dump\t%s\n' "$PRODUCT_DUMP_RC"
  printf 'product_scan\t%s\n' "$PRODUCT_SCAN_RC"
  printf 'nightly_smoke\t%s\n' "$NIGHTLY_SMOKE_RC"
  printf 'nightly_dump\t%s\n' "$NIGHTLY_DUMP_RC"
  printf 'nightly_scan\t%s\n' "$NIGHTLY_SCAN_RC"
} | tee "$OUT/result-matrix.tsv"

for f in "$OUT"/*.stderr "$OUT"/*/*.stderr; do
  [[ -f "$f" ]] || continue
  echo "===== $f ====="
  tail -160 "$f"
done

exit 0
