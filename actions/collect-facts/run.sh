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

# Recognizes a leading /, a Windows drive letter (C:\ or C:/), or a UNC
# path (\\server\share) as already-absolute. This script runs via Git
# Bash/MSYS on windows-latest, where most GITHUB_*-derived paths are
# already POSIX-style, but a workflow can still supply a native Windows
# absolute path (e.g. output: ${{ runner.temp }}\abicheck_inputs, since
# RUNNER_TEMP itself is Windows-style there) -- matching only `/*` treated
# that as relative and produced a nonsensical mixed path like
# /d/a/repo/C:\... when $(pwd)/ was prepended (Codex review).
_is_absolute_path() {
  case "$1" in
    /* | [A-Za-z]:[/\\]* | \\\\*) return 0 ;;
    *) return 1 ;;
  esac
}

# Git Bash/MSYS's own `pwd` reports its POSIX-style view of the filesystem
# (e.g. /d/a/repo/repo), not a Windows path. That is fine as long as
# everything downstream stays inside Git Bash, but ABICHECK_INPUTS_DIR/the
# plugin out=/public-roots= flags are read by the native Windows Python and
# Clang toolchain the Action installs, which has no notion of an MSYS root
# and resolves /d/... as a relative path (a literal "d" directory) under
# whatever the current drive happens to be -- silently writing the pack
# somewhere other than where phase: verify or the caller's build looks for
# it. cygpath -m converts to the mixed form (drive letter + forward
# slashes) both Git Bash and native Windows tools accept (Codex review).
_native_pwd() {
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*)
      if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$(pwd)"
        return
      fi
      ;;
  esac
  pwd
}

# Normalize an arbitrary path to the mixed form (drive letter + forward
# slashes) via cygpath -m, same rationale as _native_pwd above but for a
# path handed in rather than $(pwd). Needed because $CMPLR_ROOT (a native
# Windows env var a vendor batch/setup step sets, e.g. "C:\Program Files
# (x86)\Intel\oneAPI\compiler\latest") and Git Bash's own POSIX-style view
# of `command -v icx`/`icpx` are two different representations of
# potentially the same path -- comparing them as raw strings (as
# _bundled_llvm_cmake_prefix below used to) can never match even after
# accounting for separator differences alone (Codex review). A no-op
# outside MINGW/MSYS/CYGWIN or when cygpath is unavailable.
_normalize_win_path() {
  local path="$1"
  [[ -z "$path" ]] && return
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*)
      if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "$path" 2>/dev/null && return
      fi
      ;;
  esac
  printf '%s' "$path"
}

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
if ! _is_absolute_path "$OUTPUT"; then
  OUTPUT="$(_native_pwd)/$OUTPUT"
fi
PUBLIC_ROOTS="${INPUT_PUBLIC_ROOTS:-}"
LIBRARY="${INPUT_LIBRARY:-}"
EXTRACTOR="${INPUT_EXTRACTOR:-auto}"
COMPILER="${INPUT_COMPILER:-clang++}"
INSTALL_DEPS="${INPUT_INSTALL_DEPS:-true}"
LLVM_CMAKE_PREFIX="${INPUT_LLVM_CMAKE_PREFIX:-}"
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
# Fallback only -- see _llvm_major_from_predefined_macros below, which is
# the primary detection path and handles vendor compilers whose --version
# banner does not say "clang version N" at all (e.g. Intel's icpx/icx print
# "Intel(R) oneAPI DPC++/C++ Compiler 2024.0.0 ...", their own product
# version, not the underlying LLVM major).
_llvm_major_from_version_string() {
  local version_output="$1"
  printf '%s' "$version_output" | grep -oE 'clang version [0-9]+' | grep -oE '[0-9]+' | head -1
}

# Ask the compiler itself which Clang major it is, via the predefined
# __clang_major__ macro (`-dM -E` dumps every predefined macro; grep the one
# we want). This works for upstream/Apple/Debian Clang exactly like the
# --version parser above, but ALSO works for vendor compilers built on top
# of Clang whose --version banner reports a vendor product version instead
# of the LLVM major -- notably Intel's icpx/icx (oneAPI DPC++/C++ Compiler),
# whose --version says e.g. "2024.0.0", not any Clang/LLVM number at all.
# __clang_major__ always reports the real value the loading Clang actually
# is, which is exactly the thing that must match for the plugin to load
# (Codex review: regex-on---version alone cannot support icpx/icx).
# Empty output means the compiler doesn't behave like Clang here (e.g. gcc,
# or `-dM -E` itself failed) -- callers fall back to _llvm_major_from_version_string.
_llvm_major_from_predefined_macros() {
  local compiler="$1" defines
  defines=$("$compiler" -dM -E -x c++ - < /dev/null 2>/dev/null) || return 0
  # A successful dump that simply has no __clang_major__ (e.g. gcc, which
  # also supports -dM -E) is a successful EMPTY result, not a failure --
  # without the explicit `return 0`, the final grep's own no-match exit
  # status (1) would leak out as this function's own exit status (Codex
  # review). Callers here only check `-z "$major"`, not the exit status,
  # so this had no live bug today, but the function's contract should
  # still be "empty means nothing found", matching
  # _llvm_major_from_version_string's equivalent contract above (there,
  # incidentally, via a trailing `head -1` that always exits 0).
  printf '%s' "$defines" | grep -oE '#define __clang_major__ [0-9]+' | grep -oE '[0-9]+$'
  return 0
}

# Resolve a CMake prefix path for a vendor-bundled LLVM/Clang install, so
# the plugin builds against the toolchain's own matching dev files instead
# of a separately apt-installed clang-N/llvm-N-dev/libclang-N-dev that may
# not even exist for a vendor major version (Codex review: Intel's icpx/icx
# ship their own LLVM/Clang CMake package under $CMPLR_ROOT and are never
# available as Ubuntu apt packages). Priority: an explicit override (the
# llvm-cmake-prefix input) always wins; otherwise $CMPLR_ROOT (the env var
# Intel's oneAPI setvars.sh/environment sets to the compiler's own install
# root) auto-applies ONLY if it looks like it bundles an LLVM CMake package
# AND the resolved $COMPILER binary actually lives under that same root --
# a job can source oneAPI for unrelated tools while `compiler:` still
# resolves to a different, unrelated Clang (e.g. the default clang++), and
# building against $CMPLR_ROOT's LLVM in that case would produce a plugin
# that doesn't match the Clang that later loads it via -fplugin= (Codex
# review). Empty output means neither applies -- caller falls back to the
# apt-get install path.
#
# The $CMPLR_ROOT-derived path is always an installation root containing
# lib/cmake/{llvm,clang} (matching the llvm-cmake-prefix input's own
# documented contract in action.yml), and this function always returns the
# "lib/cmake" level one directory below that root. The explicit
# llvm-cmake-prefix override accepts EITHER shape for that same reason it
# exists as an escape hatch in the first place -- a user reasonably setting
# it to the already-resolved "lib/cmake" prefix (mirroring either
# $CMPLR_ROOT's own auto-detected internal shape, or this same script's
# existing $(llvm-config --cmakedir)/.. convention a few lines down) must
# not be double-suffixed into "lib/cmake/lib/cmake", which would make
# find_package(LLVM/Clang CONFIG) miss the vendor package entirely (Codex
# review). Disambiguated by directory existence rather than string shape:
# if $explicit/lib/cmake/llvm exists, treat $explicit as the root and
# append "lib/cmake"; else if $explicit/llvm exists, $explicit is already
# the "lib/cmake" level -- pass it through unmodified. Neither existing
# (e.g. the path doesn't exist yet) falls back to the documented root
# contract, matching the explicit branch's original fix.
_bundled_llvm_cmake_prefix() {
  local explicit="$1" cmplr_root="$2" compiler_path="$3"
  if [[ -n "$explicit" ]]; then
    if [[ -d "$explicit/lib/cmake/llvm" ]]; then
      printf '%s' "$explicit/lib/cmake"
    elif [[ -d "$explicit/llvm" ]]; then
      printf '%s' "$explicit"
    else
      printf '%s' "$explicit/lib/cmake"
    fi
    return
  fi
  # Normalize both to the same representation before comparing -- on a real
  # windows-latest runner, $CMPLR_ROOT (set natively by a vendor batch/
  # setup step, e.g. "C:\Program Files (x86)\Intel\oneAPI\compiler\latest")
  # and $(command -v "$COMPILER")'s Git-Bash-POSIX view of the same binary
  # can be two entirely different-looking paths, not just differently
  # separated -- a raw string/glob compare (even one accepting either
  # separator) can never match them (Codex review).
  cmplr_root=$(_normalize_win_path "$cmplr_root")
  compiler_path=$(_normalize_win_path "$compiler_path")
  # Still accept either separator after normalization: _normalize_win_path
  # is a no-op outside MINGW/MSYS/CYGWIN or without cygpath on PATH, so on
  # a plain POSIX host (or a Windows host missing cygpath) compiler_path
  # can still legitimately be backslash-separated (regression caught by
  # test_detects_cmplr_root_with_llvm_cmake_package on windows-latest CI).
  #
  # Require BOTH lib/cmake/llvm and lib/cmake/clang here, not just llvm
  # (Codex review): the plugin's CMakeLists.txt does
  # find_package(LLVM REQUIRED CONFIG) *and*
  # find_package(Clang REQUIRED CONFIG) -- a $CMPLR_ROOT with only a
  # partial SDK (LLVMConfig.cmake present, ClangConfig.cmake missing) would
  # otherwise return a prefix here, which makes _prepare_clang_plugin skip
  # its apt-get fallback entirely (INSTALL_DEPS is ignored once
  # bundled_cmake_prefix is non-empty) and then fail cmake configure --
  # strictly worse than the pre-auto-detect behavior, where apt-get would
  # at least have been attempted and might have supplied a working
  # libclang-$major-dev. This auto-detect path is inferred, not something
  # the caller opted into the way an explicit llvm-cmake-prefix is, so it
  # should not silently take over from a working fallback on a partial
  # match.
  if [[ -n "$cmplr_root" && -d "$cmplr_root/lib/cmake/llvm" \
      && -d "$cmplr_root/lib/cmake/clang" \
      && ( "$compiler_path" == "$cmplr_root/"* || "$compiler_path" == "$cmplr_root"\\* ) ]]; then
    printf '%s' "$cmplr_root/lib/cmake"
    return
  fi
}

# Choose the right remediation hint for a `cmake configure` failure while
# building the Clang plugin. The pre-existing message (the else branch)
# assumed apt-get was the only way to have reached this cmake invocation and
# told the user to check the libclang-<major>-dev package -- now that the
# vendor-bundled path (_bundled_llvm_cmake_prefix above) can also reach it,
# that hint is actively wrong there: no apt package was ever involved, so
# "install libclang-N-dev" sends a user with e.g. a broken/partial Intel
# oneAPI install chasing a package that doesn't exist for their toolchain.
_cmake_configure_failure_hint() {
  local bundled_cmake_prefix="$1" llvm_cmake_dir="$2" major="$3"
  if [[ -n "$bundled_cmake_prefix" ]]; then
    printf '%s' "the vendor-bundled LLVM/Clang CMake package at '$bundled_cmake_prefix' looks incomplete or mismatched for LLVM $major -- check that '$llvm_cmake_dir' actually contains a full LLVM+Clang CMake install (LLVMConfig.cmake, ClangConfig.cmake), or override llvm-cmake-prefix with a different path."
  else
    printf '%s' "see contrib/abicheck-clang-plugin/README.md#build (needs the full libclang-$major-dev package, not just clang-$major)."
  fi
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
#
# Removes only the two known pack-content items (abicheck/buildsource/
# inputs_pack.py's INPUTS_MANIFEST_NAME/SOURCE_FACTS_DIR), never the whole
# $OUTPUT tree: output is a user-controlled Action input, and a workflow
# that accidentally points it at an existing non-pack directory (the
# workspace root, a shared build/source directory) must not have this step
# recursively delete whatever is there (Codex review).
_reset_output_dir() {
  rm -f "$OUTPUT/manifest.json"
  rm -rf "$OUTPUT/source_facts"
  mkdir -p "$OUTPUT"
}

# Resolve a public-roots line to an absolute path, same rationale as
# OUTPUT above: ABICHECK_CC_HEADERS/the plugin's public-roots= flag are
# read by abicheck-cc/the plugin while they run with cwd set to the
# *build* directory (the documented CMake compiler-launcher recipe), not
# this script's own cwd. A relative root like the documented
# public-roots: "include" would then resolve against the wrong
# directory -- split_public_roots() (abicheck/buildsource/
# source_extractors/_argv.py) checks os.path.isdir() there and, finding
# nothing, misclassifies it as a file root instead of a directory root,
# so declarations under the real source-tree include/ never match. An
# already-absolute root, or one already ending in a path separator (a
# directory marker split_public_roots() honours even when the path
# doesn't exist yet), is passed through unchanged (Codex review).
_resolve_public_root() {
  local root="$1"
  if _is_absolute_path "$root"; then
    printf '%s' "$root"
  else
    printf '%s' "$(_native_pwd)/$root"
  fi
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
    local roots_joined="" root resolved
    while IFS= read -r root; do
      [[ -z "$root" ]] && continue
      resolved=$(_resolve_public_root "$root")
      roots_joined="${roots_joined:+$roots_joined$sep}$resolved"
    done <<< "$PUBLIC_ROOTS"
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
  local version_output="" major
  major=$(_llvm_major_from_predefined_macros "$COMPILER")
  if [[ -z "$major" ]]; then
    version_output=$("$COMPILER" --version 2>&1)
    major=$(_llvm_major_from_version_string "$version_output")
  fi
  [[ -n "$major" ]] || _fail "could not determine the Clang/LLVM major version '$COMPILER' is based on -- tried '__clang_major__' via '$COMPILER -dM -E -x c++ -' and parsing '$COMPILER --version':
$version_output"
  echo "detected LLVM major $major from $COMPILER"

  local bundled_cmake_prefix compiler_path
  compiler_path=$(command -v "$COMPILER" 2>/dev/null || true)
  bundled_cmake_prefix=$(_bundled_llvm_cmake_prefix "$LLVM_CMAKE_PREFIX" "${CMPLR_ROOT:-}" "$compiler_path")

  if [[ -n "$bundled_cmake_prefix" ]]; then
    # A vendor toolchain (e.g. Intel's icpx/icx oneAPI compilers) bundles
    # its own matching LLVM/Clang CMake package -- use it instead of
    # apt-get, which either doesn't carry that major at all or would build
    # against a *different* LLVM than the one $COMPILER actually loads
    # (Codex review).
    echo "using vendor-bundled LLVM/Clang CMake package at '$bundled_cmake_prefix' -- skipping apt-get install-deps."
  elif [[ -z "$LLVM_CMAKE_PREFIX" && -n "${CMPLR_ROOT:-}" ]]; then
    # $CMPLR_ROOT is set (a vendor environment, e.g. Intel oneAPI's
    # setvars.sh, was sourced) but auto-detection found no lib/cmake/llvm
    # under it -- worth calling out explicitly rather than silently falling
    # through to apt-get, since this is the expected outcome for a *stock*
    # Intel oneAPI DPC++/C++ Compiler install: its CMake packages are
    # IntelSYCL/IntelDPCPP (compiler-flag helpers for consuming projects,
    # found under $CMPLR_ROOT/lib/cmake/{IntelSYCL,IntelDPCPP}), not a
    # standard LLVM/Clang CMake export set -- so there is usually nothing
    # under $CMPLR_ROOT for auto-detection to find at all (Codex review: the
    # llvm-cmake-prefix doc previously asserted this auto-detects "true for
    # a sourced Intel oneAPI environment", which does not hold for the
    # actual package layout).
    echo "::notice::\$CMPLR_ROOT is set ('$CMPLR_ROOT') but no LLVM/Clang CMake package was found at '$CMPLR_ROOT/lib/cmake/llvm' -- this is expected for a stock Intel oneAPI DPC++/C++ Compiler install, which ships IntelSYCL/IntelDPCPP CMake modules there instead, not an LLVM/Clang plugin-development SDK. Falling back to apt-get, which will likely fail for a vendor LLVM major. If you have a matching LLVM+Clang CMake package available (e.g. built separately to match '$COMPILER'), set the llvm-cmake-prefix input to it."
  fi
  if [[ -z "$bundled_cmake_prefix" && "$INSTALL_DEPS" == "true" ]]; then
    if [[ "$(uname -s)" == "Linux" ]] && command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
      echo "::group::Install clang-$major dev packages for the plugin build"
      sudo apt-get update -qq
      sudo apt-get install -y -qq "clang-$major" "llvm-$major-dev" "libclang-$major-dev" > /dev/null \
        || _fail "failed to install clang-$major/llvm-$major-dev/libclang-$major-dev -- producer: clang-plugin needs the exact-major Clang development package (see contrib/abicheck-clang-plugin/README.md#build). If '$COMPILER' is a vendor toolchain that bundles its own LLVM/Clang (e.g. Intel's icpx/icx under \$CMPLR_ROOT), set the llvm-cmake-prefix input instead of relying on apt."
      echo "::endgroup::"
    else
      echo "::warning::install-deps: true but this OS/environment cannot apt-get install the Clang dev package automatically. Ensure the libclang-$major-dev equivalent is already installed, or set llvm-cmake-prefix to a vendor-bundled LLVM/Clang CMake package."
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
  if [[ -n "$bundled_cmake_prefix" ]]; then
    llvm_cmake_dir="$bundled_cmake_prefix/llvm"
  else
    llvm_cmake_dir=$(llvm-config-"$major" --cmakedir 2>/dev/null || llvm-config --cmakedir 2>/dev/null || true)
  fi
  local cmake_hint
  cmake_hint=$(_cmake_configure_failure_hint "$bundled_cmake_prefix" "$llvm_cmake_dir" "$major")
  cmake -S "$plugin_src" -B "$build_dir" \
    ${llvm_cmake_dir:+-DCMAKE_PREFIX_PATH="$llvm_cmake_dir/.."} \
    || _fail "cmake configure failed for the Clang plugin -- $cmake_hint"
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
    local root resolved
    while IFS= read -r root; do
      if [[ -n "$root" ]]; then
        resolved=$(_resolve_public_root "$root")
        plugin_flags="$plugin_flags -Xclang -plugin-arg-abicheck-facts -Xclang public-roots=$resolved"
      fi
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
  # phase: verify is itself the second half of a two-step choreography (or
  # the whole story for producer: replay) -- it always completes.
  _write_output "auto-completed" "true"
else
  case "$PRODUCER" in
    replay) _prepare_replay ;;
    wrapper) _prepare_wrapper ;;
    clang-plugin) _prepare_clang_plugin ;;
  esac
  if [[ "$PHASE" == "auto" ]] && _phase_needs_external_build_step "$PHASE" "$PRODUCER"; then
    # phase: auto structurally cannot run the caller's build, so for
    # wrapper/clang-plugin it only ever completes `prepare` here -- a
    # caller relying on phase: auto to mean "one step, done" would
    # otherwise get an unverified pack with no error (ADR-047 P0.2).
    # Fail loud with a job-summary notice *and* a machine-readable output
    # a caller can branch on, instead of a print-only notice.
    echo "::warning::phase: auto only completes both phases for producer: replay. For producer: $PRODUCER, phase: auto has only run 'prepare' -- add your build step here, then a second 'uses: .../collect-facts' step with phase: verify to check the collected pack. Downstream steps must check this Action's auto-completed output before assuming the pack is ready."
    _write_output "auto-completed" "false"
  else
    # phase: prepare (explicit, non-auto) is always the deliberate first
    # half of a two-step choreography the caller already knows about; and
    # phase: auto for producer: replay completes both phases in this one
    # step (_prepare_replay's own `ready: true` output already says so).
    _write_output "auto-completed" "true"
  fi
fi
