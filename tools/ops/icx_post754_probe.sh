#!/usr/bin/env bash
set +e
set -o pipefail

ROOT=$(pwd)
OUT="$ROOT/post754-results"
FIX="$ROOT/icx-wrapper-fixture"
mkdir -p "$OUT" "$FIX/include"
exec > >(tee "$OUT/probe.log") 2>&1

# oneAPI's setvars.sh can return non-zero after successfully populating the
# environment on a hosted runner. Treat the resolved compiler, not that return
# code, as the actual readiness check for this experiment.
set +u
source /opt/intel/oneapi/setvars.sh --force >/dev/null 2>&1
SETVARS_RC=$?
set -u
printf '%s\n' "$SETVARS_RC" > "$OUT/setvars.rc"

ICPX=$(command -v icpx 2>/dev/null)
if [[ -z "$ICPX" ]]; then
  echo "icpx was not resolved after sourcing oneAPI" >&2
  exit 1
fi

"$ICPX" --version | tee "$OUT/icpx-version.txt"
echo | "$ICPX" -dM -E -x c++ - \
  | grep -E '__clang_(major|minor|patchlevel|version)__|__INTEL_(LLVM|CLANG)_COMPILER' \
  | sort | tee "$OUT/icpx-version-macros.txt"
sha256sum "$ICPX" /opt/intel/oneapi/compiler/latest/bin/compiler/clang \
  | tee "$OUT/icpx-binaries-sha256.txt"

cat > "$FIX/include/widget.hpp" <<'CPP'
#pragma once
namespace demo {
struct widget { int value; };
int answer(widget);
}
CPP
cat > "$FIX/widget.cpp" <<'CPP'
#include "widget.hpp"
int demo::answer(widget w) { return w.value + 42; }
CPP

run_collect() {
  local label="$1"
  local phase="$2"
  local producer="$3"
  local install_deps="$4"
  local llvm_prefix="$5"
  local plugin_artifact="$6"
  local plugin_sha="$7"
  local output="$8"
  local env_file="$OUT/${label}.github-env"
  local output_file="$OUT/${label}.github-output"
  : > "$env_file"
  : > "$output_file"

  env \
    INPUT_PHASE="$phase" \
    INPUT_PRODUCER="$producer" \
    INPUT_SOURCES="$ROOT" \
    INPUT_OUTPUT="$output" \
    INPUT_PUBLIC_ROOTS="$FIX/include" \
    INPUT_LIBRARY="icx_widget" \
    INPUT_EXTRACTOR="clang" \
    INPUT_COMPILER="$ICPX" \
    INPUT_PLUGIN_ARTIFACT="$plugin_artifact" \
    INPUT_PLUGIN_ARTIFACT_SHA256="$plugin_sha" \
    INPUT_INSTALL_DEPS="$install_deps" \
    INPUT_LLVM_CMAKE_PREFIX="$llvm_prefix" \
    ACTION_PATH="$ROOT/actions/collect-facts" \
    GITHUB_ENV="$env_file" \
    GITHUB_OUTPUT="$output_file" \
    bash "$ROOT/actions/collect-facts/run.sh" \
    > "$OUT/${label}.stdout" \
    2> "$OUT/${label}.stderr"
  local rc=$?
  printf '%s\n' "$rc" > "$OUT/${label}.rc"
  echo "[$label] rc=$rc"
  tail -80 "$OUT/${label}.stderr" 2>/dev/null || true
  return "$rc"
}

# 1. The new safety guard should reject stock ICX before apt/upstream LLVM is
#    treated as a compatible plugin SDK.
run_collect \
  guardrail prepare clang-plugin true "" "" "" \
  "$OUT/guardrail-pack"
GUARDRAIL_RC=$?

# 2. An explicit prefix is operator-controlled and therefore reaches the build,
#    but an upstream LLVM 22 SDK is still not Intel's exact fork. The stronger
#    smoke test must reject the resulting plugin before a real project build.
run_collect \
  explicit-prefix prepare clang-plugin false \
  /usr/lib/llvm-22/lib/cmake "" "" \
  "$OUT/explicit-prefix-pack"
EXPLICIT_PREFIX_RC=$?

# 3. Independently prove the new CMake keys themselves work and still produce
#    the same kind of upstream-header-built artifact we know is incompatible.
MANUAL_BUILD="$OUT/manual-upstream-plugin"
cmake -S "$ROOT/contrib/abicheck-clang-plugin" -B "$MANUAL_BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_COMPILER="$ICPX" \
  -DCMAKE_PREFIX_PATH=/usr/lib/llvm-22/lib/cmake \
  -DABICHECK_PLUGIN_RTTI=off \
  -DABICHECK_PLUGIN_STDLIB=libc++ \
  > "$OUT/manual-cmake.stdout" \
  2> "$OUT/manual-cmake.stderr"
MANUAL_CMAKE_RC=$?
printf '%s\n' "$MANUAL_CMAKE_RC" > "$OUT/manual-cmake.rc"

MANUAL_BUILD_RC=127
PLUGIN=""
PLUGIN_SHA=""
if [[ "$MANUAL_CMAKE_RC" -eq 0 ]]; then
  cmake --build "$MANUAL_BUILD" -v \
    > "$OUT/manual-build.stdout" \
    2> "$OUT/manual-build.stderr"
  MANUAL_BUILD_RC=$?
fi
printf '%s\n' "$MANUAL_BUILD_RC" > "$OUT/manual-build.rc"

if [[ "$MANUAL_BUILD_RC" -eq 0 && -f "$MANUAL_BUILD/libabicheck-facts.so" ]]; then
  PLUGIN=$(realpath "$MANUAL_BUILD/libabicheck-facts.so")
  PLUGIN_SHA=$(sha256sum "$PLUGIN" | awk '{print $1}')
  printf '%s\n' "$PLUGIN" > "$OUT/manual-plugin-path.txt"
  printf '%s  %s\n' "$PLUGIN_SHA" "$PLUGIN" > "$OUT/manual-plugin-sha256.txt"
  file "$PLUGIN" > "$OUT/manual-plugin-file.txt"
  ldd "$PLUGIN" > "$OUT/manual-plugin-ldd.txt" 2>&1
  nm -D --undefined-only "$PLUGIN" | c++filt \
    | grep -E 'std::__1|std::__cxx11|clang::|llvm::' \
    | head -250 > "$OUT/manual-plugin-undefined.txt"
fi

# 4. The artifact digest hook should accept the exact binary digest, while the
#    runtime/facts smoke test should still reject this incompatible artifact.
ARTIFACT_RC=127
if [[ -n "$PLUGIN" ]]; then
  run_collect \
    artifact prepare clang-plugin false "" "$PLUGIN" "$PLUGIN_SHA" \
    "$OUT/artifact-pack"
  ARTIFACT_RC=$?
else
  printf '%s\n' "$ARTIFACT_RC" > "$OUT/artifact.rc"
fi

# 5. Exercise the supported portable ICX path end-to-end:
#    prepare wrapper -> real icpx compile -> validate pack -> dump -> scan.
run_collect \
  wrapper-prepare prepare wrapper false "" "" "" \
  "$FIX/abicheck_inputs"
WRAPPER_PREPARE_RC=$?

if [[ "$WRAPPER_PREPARE_RC" -eq 0 ]]; then
  # Apply only the ABICHECK_* variables exported by the prepare step. Avoid
  # eval/source so values remain data rather than shell syntax.
  while IFS='=' read -r key value; do
    case "$key" in
      ABICHECK_*) export "$key=$value" ;;
    esac
  done < "$OUT/wrapper-prepare.github-env"
fi

WRAPPER_BUILD_RC=127
if [[ "$WRAPPER_PREPARE_RC" -eq 0 ]]; then
  abicheck-cc "$ICPX" \
    -std=c++17 -g -fPIC -shared \
    -I"$FIX/include" \
    "$FIX/widget.cpp" \
    -o "$FIX/libwidget.so" \
    > "$OUT/wrapper-build.stdout" \
    2> "$OUT/wrapper-build.stderr"
  WRAPPER_BUILD_RC=$?
fi
printf '%s\n' "$WRAPPER_BUILD_RC" > "$OUT/wrapper-build.rc"
if [[ -d "$FIX/abicheck_inputs" ]]; then
  find "$FIX/abicheck_inputs" -type f -print -exec wc -c {} \; \
    > "$OUT/wrapper-facts-files.txt" 2>&1
fi

WRAPPER_VERIFY_RC=127
if [[ "$WRAPPER_BUILD_RC" -eq 0 ]]; then
  run_collect \
    wrapper-verify verify wrapper false "" "" "" \
    "$FIX/abicheck_inputs"
  WRAPPER_VERIFY_RC=$?
else
  printf '%s\n' "$WRAPPER_VERIFY_RC" > "$OUT/wrapper-verify.rc"
fi

WRAPPER_DUMP_RC=127
WRAPPER_SCAN_RC=127
if [[ "$WRAPPER_VERIFY_RC" -eq 0 ]]; then
  abicheck dump "$FIX/libwidget.so" \
    --build-info "$FIX/abicheck_inputs" \
    -o "$FIX/baseline.json" \
    > "$OUT/wrapper-dump.stdout" \
    2> "$OUT/wrapper-dump.stderr"
  WRAPPER_DUMP_RC=$?
  if [[ "$WRAPPER_DUMP_RC" -eq 0 ]]; then
    abicheck scan "$FIX/libwidget.so" \
      --against "$FIX/baseline.json" \
      --build-info "$FIX/abicheck_inputs" \
      --depth source --format json \
      > "$OUT/wrapper-scan.stdout" \
      2> "$OUT/wrapper-scan.stderr"
    WRAPPER_SCAN_RC=$?
  fi
fi
printf '%s\n' "$WRAPPER_DUMP_RC" > "$OUT/wrapper-dump.rc"
printf '%s\n' "$WRAPPER_SCAN_RC" > "$OUT/wrapper-scan.rc"

cp -f "$FIX/baseline.json" "$OUT/" 2>/dev/null || true
cp -f "$FIX/libwidget.so" "$OUT/" 2>/dev/null || true
cp -a "$FIX/abicheck_inputs" "$OUT/wrapper-pack" 2>/dev/null || true

{
  printf 'probe\trc\texpected\n'
  printf 'guardrail\t%s\tnonzero\n' "$GUARDRAIL_RC"
  printf 'explicit_upstream_prefix\t%s\tnonzero\n' "$EXPLICIT_PREFIX_RC"
  printf 'manual_cmake_new_keys\t%s\t0\n' "$MANUAL_CMAKE_RC"
  printf 'manual_build_new_keys\t%s\t0\n' "$MANUAL_BUILD_RC"
  printf 'mismatched_artifact_smoke\t%s\tnonzero\n' "$ARTIFACT_RC"
  printf 'wrapper_prepare\t%s\t0\n' "$WRAPPER_PREPARE_RC"
  printf 'wrapper_build\t%s\t0\n' "$WRAPPER_BUILD_RC"
  printf 'wrapper_verify\t%s\t0\n' "$WRAPPER_VERIFY_RC"
  printf 'wrapper_dump\t%s\t0\n' "$WRAPPER_DUMP_RC"
  printf 'wrapper_scan\t%s\t0\n' "$WRAPPER_SCAN_RC"
} | tee "$OUT/result-matrix.tsv"

FAIL=0
[[ "$GUARDRAIL_RC" -ne 0 ]] || { echo "expected ICX guardrail refusal" >&2; FAIL=1; }
[[ "$EXPLICIT_PREFIX_RC" -ne 0 ]] || { echo "upstream prefix unexpectedly passed ICX smoke" >&2; FAIL=1; }
[[ "$MANUAL_CMAKE_RC" -eq 0 ]] || { echo "new CMake keys failed configure" >&2; FAIL=1; }
[[ "$MANUAL_BUILD_RC" -eq 0 ]] || { echo "new CMake keys failed build" >&2; FAIL=1; }
[[ "$ARTIFACT_RC" -ne 0 ]] || { echo "known-mismatched artifact unexpectedly passed smoke" >&2; FAIL=1; }
[[ "$WRAPPER_PREPARE_RC" -eq 0 ]] || { echo "wrapper prepare failed" >&2; FAIL=1; }
[[ "$WRAPPER_BUILD_RC" -eq 0 ]] || { echo "wrapper ICX build failed" >&2; FAIL=1; }
[[ "$WRAPPER_VERIFY_RC" -eq 0 ]] || { echo "wrapper pack validation failed" >&2; FAIL=1; }
[[ "$WRAPPER_DUMP_RC" -eq 0 ]] || { echo "wrapper pack dump ingest failed" >&2; FAIL=1; }
[[ "$WRAPPER_SCAN_RC" -eq 0 ]] || { echo "wrapper-backed scan failed" >&2; FAIL=1; }

exit "$FAIL"
