#!/usr/bin/env bash
# Collects L3/L4/L5 build/source facts using the right producer for the
# caller's build, replacing the hand-rolled shell scripts + separately
# pinned producer version that real integrations were writing on their own.
#
# Producer decision tree (mirrors docs/user-guide/producing-source-facts.md):
#   own the toolchain image and second-parse cost hurts?  -> clang-plugin (opt-in only)
#   have/can-generate a compile database?                  -> replay
#   otherwise                                               -> wrapper
#
# Wrapper and clang-plugin need the *caller's own build command* to run
# between collection and verification -- this script cannot invoke that
# build itself. See the `phase` input in action.yml.
set -uo pipefail

PHASE="${INPUT_PHASE:-auto}"
PRODUCER_IN="${INPUT_PRODUCER:-auto}"
SOURCES="${INPUT_SOURCES:-.}"
OUTPUT="${INPUT_OUTPUT:-abicheck_inputs}"
PUBLIC_ROOTS="${INPUT_PUBLIC_ROOTS:-}"
LIBRARY="${INPUT_LIBRARY:-}"
EXTRACTOR="${INPUT_EXTRACTOR:-auto}"
COMPILER="${INPUT_COMPILER:-clang++}"
INSTALL_DEPS="${INPUT_INSTALL_DEPS:-true}"
ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

_fail() {
  echo "::error::$1"
  exit 1
}

# ---------------------------------------------------------------------------
# Pure helpers (no side effects -- tests/test_action_collect_facts.py sources
# this region directly, the same "parse the real file" discipline as
# action/run.sh's helper functions).
# ---------------------------------------------------------------------------

# Auto-detect replay vs wrapper by inspecting SOURCES for an existing compile
# database or a build system that can emit one. Never returns clang-plugin --
# "own the toolchain image" cannot be inferred, it is opt-in only.
_detect_producer() {
  local src="$1"
  if [[ -f "$src/compile_commands.json" ]]; then
    echo "replay"
    return
  fi
  if find "$src" -maxdepth 3 -name compile_commands.json 2>/dev/null | grep -q .; then
    echo "replay"
    return
  fi
  if [[ -f "$src/CMakeLists.txt" || -f "$src/WORKSPACE" || -f "$src/WORKSPACE.bazel" ]]; then
    # cmake/bazel can emit a compile DB on request -- replay's own
    # build-system query (documented in producing-source-facts.md) handles
    # generating it, no wrapper needed.
    echo "replay"
    return
  fi
  echo "wrapper"
}

# Extract the LLVM/Clang major version number from a `clang --version`-style
# string (e.g. "clang version 18.1.3" -> "18"). Empty output means unparsable.
_llvm_major_from_version_string() {
  local version_output="$1"
  printf '%s' "$version_output" | grep -oE 'clang version [0-9]+' | grep -oE '[0-9]+' | head -1
}

# Validate the phase/producer combination the way action.yml documents it:
# phase=auto only completes standalone for producer=replay.
_phase_needs_external_build_step() {
  local phase="$1" producer="$2"
  [[ "$phase" == "prepare" || "$phase" == "auto" ]] && [[ "$producer" == "wrapper" || "$producer" == "clang-plugin" ]]
}

_write_env() {
  local name="$1" value="$2"
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    echo "${name}=${value}" >> "$GITHUB_ENV"
  fi
}

_write_output() {
  local name="$1" value="$2"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "${name}=${value}" >> "$GITHUB_OUTPUT"
  fi
}

# ---------------------------------------------------------------------------
# Resolve producer
# ---------------------------------------------------------------------------
case "$PRODUCER_IN" in
  auto)
    PRODUCER=$(_detect_producer "$SOURCES")
    echo "producer: auto -> $PRODUCER (no compile database/build system found under '$SOURCES' means wrapper; found one means replay)"
    ;;
  replay | wrapper | clang-plugin)
    PRODUCER="$PRODUCER_IN"
    ;;
  *)
    _fail "producer '$PRODUCER_IN' is not recognized. Use 'auto', 'replay', 'wrapper', or 'clang-plugin'."
    ;;
esac

case "$PHASE" in
  auto | prepare | verify) ;;
  *) _fail "phase '$PHASE' is not recognized. Use 'auto', 'prepare', or 'verify'." ;;
esac

if [[ "$PHASE" == "verify" && "$PRODUCER" == "replay" ]]; then
  echo "::notice::producer: replay collects inline at dump/scan/compare time -- there is nothing for phase: verify to check here. Pass sources: $SOURCES directly to the next abicheck step."
fi

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_prepare_replay() {
  [[ -d "$SOURCES" ]] || _fail "sources '$SOURCES' does not exist."
  local has_db=""
  if [[ -f "$SOURCES/compile_commands.json" ]] || find "$SOURCES" -maxdepth 3 -name compile_commands.json 2>/dev/null | grep -q .; then
    has_db="yes"
  fi
  if [[ -z "$has_db" ]]; then
    echo "::notice::No compile_commands.json found under '$SOURCES'. abicheck infers and runs the build-system query itself (cmake/bazel/make) at dump/scan/compare time when it sees --sources -- no separate collection step is needed here."
  fi
  echo "producer: replay needs no separate collection step -- pass sources: $SOURCES directly to dump/scan/compare (--depth build/source selects how much of it is used)."
  _write_output "producer" "replay"
  _write_output "mode" "inline"
  _write_output "pack-path" ""
  _write_output "ready" "true"
  _write_output "producer-version" ""
}

_prepare_wrapper() {
  if [[ "$INSTALL_DEPS" == "true" ]]; then
    bash "$(dirname "${BASH_SOURCE[0]}")/../../action/install-deps.sh" || true
  fi
  mkdir -p "$OUTPUT"
  _write_env "ABICHECK_INPUTS_DIR" "$OUTPUT"
  _write_env "ABICHECK_CC_EXTRACTOR" "$EXTRACTOR"
  [[ -n "$LIBRARY" ]] && _write_env "ABICHECK_CC_LIBRARY" "$LIBRARY"
  if [[ -n "$PUBLIC_ROOTS" ]]; then
    # ABICHECK_CC_HEADERS takes one root; the wrapper docs show a single
    # value -- join multiple lines with ':' (PATH-style), the wrapper's own
    # documented convention for multi-root scoping.
    local roots_joined
    roots_joined=$(printf '%s' "$PUBLIC_ROOTS" | tr '\n' ':' | sed 's/:$//')
    _write_env "ABICHECK_CC_HEADERS" "$roots_joined"
  fi
  echo "::notice::producer: wrapper prepared -- front your build command with 'abicheck-cc' (e.g. CC=\"abicheck-cc gcc\" CXX=\"abicheck-cc g++\", or CMAKE_CXX_COMPILER_LAUNCHER=abicheck-cc), run your build next, then call this Action again with phase: verify to check the collected pack at '$OUTPUT'."
  _write_output "producer" "wrapper"
  _write_output "mode" "pack"
  _write_output "pack-path" "$OUTPUT"
  _write_output "ready" "false"
  _write_output "producer-version" ""
}

_prepare_clang_plugin() {
  command -v "$COMPILER" >/dev/null 2>&1 || _fail "compiler '$COMPILER' not found on PATH -- producer: clang-plugin needs the loading Clang available to detect and match its LLVM major."
  local version_output major
  version_output=$("$COMPILER" --version 2>&1)
  major=$(_llvm_major_from_version_string "$version_output")
  [[ -n "$major" ]] || _fail "could not parse an LLVM major version from '$COMPILER --version':
$version_output"
  echo "detected LLVM major $major from $COMPILER"

  if [[ "$INSTALL_DEPS" == "true" ]]; then
    if [[ "$(uname -s)" == "Linux" ]] && command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
      echo "::group::Install clang-$major dev packages for the plugin build"
      sudo apt-get update -qq
      sudo apt-get install -y -qq "clang-$major" "llvm-$major-dev" "libclang-$major-dev" > /dev/null \
        || _fail "failed to install clang-$major/llvm-$major-dev/libclang-$major-dev -- producer: clang-plugin needs the exact-major Clang development package (see contrib/abicheck-clang-plugin/README.md#build)."
      echo "::endgroup::"
    else
      echo "::warning::install-deps: true but this OS/environment cannot apt-get install the Clang dev package automatically. Ensure the libclang-$major-dev equivalent is already installed."
    fi
  fi

  # Build FROM the already-checked-out copy of this same commit (ACTION_PATH
  # is inside the abicheck/abicheck checkout GitHub Actions made for THIS
  # step) -- not a separately pinned plugin_ref. One `uses: .../collect-facts
  # @<sha>` pin covers both the scanner and the plugin it builds, closing the
  # "version pinned in two places" gap a hand-rolled integration hits.
  local plugin_src="$ACTION_PATH/../../contrib/abicheck-clang-plugin"
  [[ -d "$plugin_src" ]] || _fail "expected the Clang plugin source at '$plugin_src' (relative to this Action's own checkout) -- the abicheck/abicheck checkout this Action runs from looks incomplete."
  local build_dir
  build_dir=$(mktemp -d)
  echo "::group::Build the Clang facts plugin (LLVM $major)"
  if ! command -v cmake >/dev/null 2>&1; then
    _fail "cmake not found -- required to build the Clang plugin (contrib/abicheck-clang-plugin)."
  fi
  local llvm_cmake_dir
  llvm_cmake_dir=$(llvm-config-"$major" --cmakedir 2>/dev/null || llvm-config --cmakedir 2>/dev/null || true)
  cmake -S "$plugin_src" -B "$build_dir" \
    ${llvm_cmake_dir:+-DCMAKE_PREFIX_PATH="$llvm_cmake_dir/.."} \
    || _fail "cmake configure failed for the Clang plugin -- see contrib/abicheck-clang-plugin/README.md#build (needs the full libclang-$major-dev package, not just clang-$major)."
  cmake --build "$build_dir" || _fail "cmake build failed for the Clang plugin."
  echo "::endgroup::"

  local plugin_so
  plugin_so=$(find "$build_dir" -maxdepth 2 -name 'libabicheck-facts.*' | head -1)
  [[ -n "$plugin_so" ]] || _fail "Clang plugin build did not produce libabicheck-facts.* under '$build_dir'."

  # Smoke-test: the plugin must at least load into the compiler without
  # crashing, on a trivial translation unit, before we hand its path to the
  # caller's real build.
  local smoke_dir smoke_src
  smoke_dir=$(mktemp -d)
  smoke_src="$smoke_dir/smoke.cpp"
  printf 'int abicheck_smoke_test() { return 0; }\n' > "$smoke_src"
  mkdir -p "$OUTPUT"
  if ! "$COMPILER" -std=c++17 \
      -fplugin="$plugin_so" \
      -Xclang -plugin-arg-abicheck-facts -Xclang "out=$OUTPUT" \
      -fsyntax-only "$smoke_src" 2>"$smoke_dir/stderr.log"; then
    cat "$smoke_dir/stderr.log" >&2
    _fail "the built Clang plugin failed to load on a smoke-test translation unit -- see the compiler output above. This usually means '$COMPILER' is not the same LLVM major ($major) the plugin was built against."
  fi
  echo "Clang plugin smoke test passed ($plugin_so loads into $COMPILER)."

  # Assemble the exact flags the caller's build needs to add, one -Xclang
  # pair per public root (public-roots= is repeatable per the plugin docs).
  local plugin_flags="-fplugin=$plugin_so -Xclang -plugin-arg-abicheck-facts -Xclang out=$OUTPUT"
  if [[ -n "$PUBLIC_ROOTS" ]]; then
    local root
    while IFS= read -r root; do
      [[ -n "$root" ]] && plugin_flags="$plugin_flags -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=$root"
    done <<< "$PUBLIC_ROOTS"
  fi
  [[ -n "$LIBRARY" ]] && plugin_flags="$plugin_flags -Xclang -plugin-arg-abicheck-facts -Xclang library=$LIBRARY"

  _write_env "ABICHECK_PLUGIN_SO" "$plugin_so"
  _write_env "ABICHECK_PLUGIN_FLAGS" "$plugin_flags"
  echo "::notice::producer: clang-plugin ready at $plugin_so. Add these flags to your compile command (also exported as \$ABICHECK_PLUGIN_FLAGS): $plugin_flags -- run your build next, then call this Action again with phase: verify to check the collected pack at '$OUTPUT'."
  _write_output "producer" "clang-plugin"
  _write_output "mode" "pack"
  _write_output "pack-path" "$OUTPUT"
  _write_output "ready" "false"
  _write_output "producer-version" "llvm-$major"
}

_verify_pack() {
  [[ -d "$OUTPUT" ]] || _fail "phase: verify found no pack directory at '$OUTPUT' -- did the build step between phase: prepare and phase: verify actually run (and write ABICHECK_INPUTS_DIR/-fplugin's out= there)?"
  local file_count
  file_count=$(find "$OUTPUT" -type f | wc -l | tr -d ' ')
  if [[ "$file_count" -eq 0 ]]; then
    _fail "phase: verify found pack directory '$OUTPUT' but it is empty -- see the producing-source-facts.md 'public-roots must match how headers resolve' trap: a wrong ABICHECK_CC_HEADERS/public-roots= silently yields nothing to collect from, even though the build otherwise looked fine."
  fi
  echo "pack at '$OUTPUT' contains $file_count file(s)."
  _write_output "producer" "$PRODUCER"
  _write_output "mode" "pack"
  _write_output "pack-path" "$OUTPUT"
  _write_output "ready" "true"
  _write_output "producer-version" ""
}

if [[ "$PHASE" == "verify" ]]; then
  if [[ "$PRODUCER" == "replay" ]]; then
    _write_output "producer" "replay"
    _write_output "mode" "inline"
    _write_output "pack-path" ""
    _write_output "ready" "true"
    _write_output "producer-version" ""
  else
    _verify_pack
  fi
else
  case "$PRODUCER" in
    replay) _prepare_replay ;;
    wrapper) _prepare_wrapper ;;
    clang-plugin) _prepare_clang_plugin ;;
  esac
  if [[ "$PHASE" == "auto" ]] && _phase_needs_external_build_step "$PHASE" "$PRODUCER"; then
    echo "::notice::phase: auto only completes both phases for producer: replay. For producer: $PRODUCER, add a build step here, then a second 'uses: .../collect-facts' step with phase: verify."
  fi
fi
