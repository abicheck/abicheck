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
# Resolve to an absolute path up front: the CMake compiler-launcher recipe
# this Action documents invokes abicheck-cc (and the Clang plugin's out=
# flag) with cwd set to the *build* directory, not this script's own cwd
# (the repo root) -- a relative ABICHECK_INPUTS_DIR/out= would then resolve
# under build/ instead of the top-level pack _reset_output_dir/phase: verify/
# pack-path all reference, so verification would report an empty pack even
# though the build was actually instrumented (Codex review).
case "$OUTPUT" in
  /*) ;;
  *) OUTPUT="$(pwd)/$OUTPUT" ;;
esac
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
  # -print -quit (not `| grep -q .`): under this script's `set -o pipefail`,
  # a nested tree with multiple compile_commands.json matches can make grep
  # exit right after the first line while find still has output queued --
  # find's next write() then gets SIGPIPE (exit 141), pipefail treats that
  # as the pipeline's failure even though grep itself matched, and this
  # `if` silently takes the false branch instead. `-quit` makes find stop
  # itself after the first match, so there is no pipe to race (Codex
  # review; reproduced locally with a 5000-entry tree).
  #
  # -maxdepth 2 (root or one immediate subdirectory below $src), not 3: the
  # inline replay path this producer: replay hands off to
  # (abicheck/buildsource/inline.py::_find_compile_db_in_dir) only ever
  # looks at $src/compile_commands.json or $src/<one-subdir>/
  # compile_commands.json -- a DB two levels deep would make this report
  # producer=replay/ready=true, but the actual replay collection step
  # would never find it and silently collect zero source facts (Codex
  # review).
  if [[ -n "$(find "$src" -maxdepth 2 -name compile_commands.json -print -quit 2>/dev/null)" ]]; then
    echo "replay"
    return
  fi
  # MODULE.bazel: the bzlmod-only layout Bazel 6+ projects use has no
  # WORKSPACE file at all -- omitting it here left auto-detection silently
  # inconsistent with abicheck/buildsource/build_query.py's own Bazel
  # marker set (WORKSPACE.bazel, WORKSPACE, and MODULE.bazel), which the
  # replay path actually queries (Codex review).
  if [[ -f "$src/CMakeLists.txt" || -f "$src/WORKSPACE" || -f "$src/WORKSPACE.bazel" || -f "$src/MODULE.bazel" ]]; then
    # cmake/bazel can emit a compile DB on request -- replay's own
    # build-system query (documented in producing-source-facts.md) handles
    # generating it, no wrapper needed.
    echo "replay"
    return
  fi
  # Checked last, after cmake/bazel (same priority order as
  # abicheck/buildsource/build_query.py's own _MARKERS -- a cmake project
  # often ships a convenience Makefile that just drives cmake, so cmake/
  # bazel take precedence when both are present). A bare Make/EPICS-style
  # tree with no compile_commands.json is still replay-capable: the inline
  # replay path auto-runs `make -B -n -k -w` and scrapes compile commands
  # from the transcript -- this bash heuristic used to assume Make couldn't
  # be replayed and fell through to wrapper, unnecessarily requiring
  # wrapper instrumentation for a build build_query.py already knows how to
  # query (Codex review).
  if [[ -f "$src/GNUmakefile" || -f "$src/makefile" || -f "$src/Makefile" ]]; then
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

# Start each pack from a genuinely empty directory. init_inputs_pack()
# (abicheck/buildsource/inputs_emit.py) is deliberately idempotent across
# repeated per-TU calls *within one build* -- it loads and preserves an
# existing manifest.json rather than resetting it -- but that means a stale
# pack left over from an earlier prepare/build/verify cycle (a reused
# workspace, or two cycles sharing the default abicheck_inputs path) is
# silently adopted here too: `mkdir -p` alone is a no-op on an already-
# existing directory, so old source_facts/*.jsonl TU records survive into
# this run. _verify_pack's TU-count check would then see the STALE nonzero
# count and report ready=true even if this run's build never actually
# invoked abicheck-cc/the plugin (Codex review).
_reset_output_dir() {
  rm -rf "$OUTPUT"
  mkdir -p "$OUTPUT"
}

# ---------------------------------------------------------------------------
# Resolve producer
# ---------------------------------------------------------------------------
case "$PRODUCER_IN" in
  auto)
    if [[ "$PHASE" == "verify" ]]; then
      # Re-running auto-detection here can silently resolve to a different
      # producer than the one your build was actually instrumented for --
      # e.g. a wrapper prepare followed by a build step that (as a side
      # effect) generates compile_commands.json would flip auto to replay,
      # and phase: verify would report ready=true having never checked the
      # wrapper's pack at all (CodeRabbit review). Every documented recipe
      # already threads the prepare step's resolved producer through
      # explicitly -- enforce that instead of trusting it.
      _fail "phase: verify needs the exact producer phase: prepare resolved, not producer: auto -- pass producer: \${{ steps.<prepare-step-id>.outputs.producer }}."
    fi
    # A misspelled/missing sources: path looks identical to "no compile
    # database found here" to _detect_producer, which silently resolves to
    # wrapper instead of erroring -- a workflow expecting replay from a real
    # compile-DB tree would silently get wrapper's very different setup
    # instead (Codex review). Same check _prepare_replay already does, run
    # up front so auto-detection can't paper over it.
    [[ -d "$SOURCES" ]] || _fail "sources '$SOURCES' does not exist."
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

# Validate before any preparation/build work starts -- an unrecognized
# extractor used to reach the wrapper env vars uncaught, and a misspelled
# install-deps value (e.g. "True", "yes") silently behaved as false since
# every check below is a literal `== "true"` string comparison
# (CodeRabbit review).
case "$EXTRACTOR" in
  auto | castxml | clang) ;;
  *) _fail "extractor '$EXTRACTOR' is not recognized. Use 'auto', 'castxml', or 'clang'." ;;
esac
case "$INSTALL_DEPS" in
  true | false) ;;
  *) _fail "install-deps '$INSTALL_DEPS' is not recognized. Use 'true' or 'false'." ;;
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
  # -print -quit, not `| grep -q .` -- same pipefail/SIGPIPE race as
  # _detect_producer above. -maxdepth 2, not 3 -- same discoverable-depth
  # mismatch with inline.py::_find_compile_db_in_dir as _detect_producer
  # above (Codex review).
  if [[ -f "$SOURCES/compile_commands.json" ]] \
    || [[ -n "$(find "$SOURCES" -maxdepth 2 -name compile_commands.json -print -quit 2>/dev/null)" ]]; then
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
    # A failed installer used to still report preparation success here
    # (a trailing "or true" swallowed it entirely), leaving the caller's
    # build to fail later with a confusing "abicheck-cc: command not
    # found", or -- worse -- to run uninstrumented and quietly produce an
    # empty pack (CodeRabbit review).
    bash "$(dirname "${BASH_SOURCE[0]}")/../../action/install-deps.sh" \
      || _fail "failed to install dependencies required by producer: wrapper -- see the output above."
  fi
  _reset_output_dir
  _write_env "ABICHECK_INPUTS_DIR" "$OUTPUT"
  _write_env "ABICHECK_CC_EXTRACTOR" "$EXTRACTOR"
  [[ -n "$LIBRARY" ]] && _write_env "ABICHECK_CC_LIBRARY" "$LIBRARY"
  if [[ -n "$PUBLIC_ROOTS" ]]; then
    # ABICHECK_CC_HEADERS takes one root; the wrapper docs show a single
    # value -- join multiple lines PATH-style. cc_wrapper.py splits this
    # with Python's os.pathsep, which is ';' on Windows and ':' everywhere
    # else -- a hardcoded ':' glued every root into one unsplit string on a
    # Windows runner instead of scoping to each of them (Codex review).
    local sep=':'
    case "$(uname -s)" in
      MINGW* | MSYS* | CYGWIN*) sep=';' ;;
    esac
    local roots_joined
    roots_joined=$(printf '%s' "$PUBLIC_ROOTS" | tr '\n' "$sep" | sed "s/${sep}\$//")
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
  # caller's real build. Writes its facts to an isolated scratch directory,
  # NOT $OUTPUT -- that is the real pack the caller's build populates next,
  # and phase: verify only checks "the pack has at least one file", so a
  # smoke-test record left sitting in $OUTPUT would make a pack that never
  # received real facts (build step skipped, wrong flags, wrong TU) look
  # ready anyway.
  local smoke_dir smoke_src smoke_out
  smoke_dir=$(mktemp -d)
  smoke_src="$smoke_dir/smoke.cpp"
  smoke_out="$smoke_dir/out"
  printf 'int abicheck_smoke_test() { return 0; }\n' > "$smoke_src"
  mkdir -p "$smoke_out"
  if ! "$COMPILER" -std=c++17 \
      -fplugin="$plugin_so" \
      -Xclang -plugin-arg-abicheck-facts -Xclang "out=$smoke_out" \
      -fsyntax-only "$smoke_src" 2>"$smoke_dir/stderr.log"; then
    cat "$smoke_dir/stderr.log" >&2
    _fail "the built Clang plugin failed to load on a smoke-test translation unit -- see the compiler output above. This usually means '$COMPILER' is not the same LLVM major ($major) the plugin was built against."
  fi
  echo "Clang plugin smoke test passed ($plugin_so loads into $COMPILER)."
  _reset_output_dir

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

  # The plugin's identity is fully fixed the moment it's built here -- the
  # caller's later build populates the *pack*, it never changes the plugin
  # binary itself -- so compute the complete documented identity (LLVM major
  # + a content digest of the built plugin) now rather than emitting a
  # partial value at prepare and clearing it at verify (CodeRabbit review).
  # Persisted via GITHUB_ENV so the separate phase: verify invocation of
  # this script (a fresh process) can re-emit the same value instead of
  # re-deriving or losing it.
  local plugin_digest
  plugin_digest=$(python3 -c '
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()[:12])
' "$plugin_so")
  local producer_version="llvm-$major+plugin-sha256-$plugin_digest"

  _write_env "ABICHECK_PLUGIN_SO" "$plugin_so"
  _write_env "ABICHECK_PLUGIN_FLAGS" "$plugin_flags"
  _write_env "ABICHECK_PRODUCER_VERSION" "$producer_version"
  echo "::notice::producer: clang-plugin ready at $plugin_so. Add these flags to your compile command (also exported as \$ABICHECK_PLUGIN_FLAGS): $plugin_flags -- run your build next, then call this Action again with phase: verify to check the collected pack at '$OUTPUT'."
  _write_output "producer" "clang-plugin"
  _write_output "mode" "pack"
  _write_output "pack-path" "$OUTPUT"
  _write_output "ready" "false"
  _write_output "producer-version" "$producer_version"
}

_verify_pack() {
  [[ -d "$OUTPUT" ]] || _fail "phase: verify found no pack directory at '$OUTPUT' -- did the build step between phase: prepare and phase: verify actually run (and write ABICHECK_INPUTS_DIR/-fplugin's out= there)?"
  local file_count
  file_count=$(find "$OUTPUT" -type f | wc -l | tr -d ' ')
  if [[ "$file_count" -eq 0 ]]; then
    _fail "phase: verify found pack directory '$OUTPUT' but it is empty -- see the producing-source-facts.md 'public-roots must match how headers resolve' trap: a wrong ABICHECK_CC_HEADERS/public-roots= silently yields nothing to collect from, even though the build otherwise looked fine."
  fi
  # A nonzero file count alone is not proof of real facts: init_inputs_pack()
  # writes manifest.json up front, before any TU is ever appended, so a build
  # that never actually routed through the wrapper/plugin (or whose every
  # extraction failed) still leaves a nonempty directory here -- only
  # source_facts/*.jsonl proves a TU was collected (Codex review). Run the
  # same validator dump --build-info uses downstream, so this is a real
  # pre-flight check instead of a bash reimplementation of it, and fail hard
  # on zero TU records instead of the silent warning the downstream consumer
  # gives a mis-instrumented build.
  local validate_out
  if ! validate_out=$(python3 -c '
import sys
from abicheck.buildsource.inputs_validate import validate_inputs_pack

try:
    report = validate_inputs_pack(sys.argv[1])
except (FileNotFoundError, ValueError) as exc:
    print(f"not a readable abicheck_inputs pack: {exc}")
    sys.exit(1)
if report.errors:
    print("pack validation error(s): " + "; ".join(report.errors))
    sys.exit(1)
if report.tu_count == 0:
    print(
        "pack directory exists (manifest.json present) but contains zero "
        "readable TU records under source_facts/ -- no translation unit "
        "ever appended facts to it (wrong extractor, the build never "
        "actually invoked the wrapper/plugin, or every extraction failed)."
    )
    sys.exit(1)
for w in report.warnings:
    print(f"::warning::{w}")
print(f"pack at {sys.argv[1]!r} contains {report.tu_count} TU record(s).")
' "$OUTPUT"); then
    _fail "$validate_out"
  fi
  echo "pack at '$OUTPUT' contains $file_count file(s). $validate_out"
  _write_output "producer" "$PRODUCER"
  _write_output "mode" "pack"
  _write_output "pack-path" "$OUTPUT"
  _write_output "ready" "true"
  # Re-emit whatever phase: prepare computed and persisted via GITHUB_ENV
  # (clang-plugin only -- its identity is fixed at build time) instead of
  # unconditionally clearing it here. Empty for wrapper: nothing about
  # *which compiler* the caller's build actually invoked is knowable from
  # this side (CodeRabbit review).
  _write_output "producer-version" "${ABICHECK_PRODUCER_VERSION:-}"
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
