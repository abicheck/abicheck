#!/usr/bin/env bash
# Main entrypoint for the abicheck GitHub Action.
# Assembles the CLI command from INPUT_* environment variables,
# runs abicheck, captures the exit code, and sets outputs.
set -uo pipefail

# `$OSTYPE` is a bash builtin, always set, no external command needed --
# Git Bash on GitHub's windows-latest runners reports "msys" (Cygwin
# reports "cygwin"), every other supported runner reports something else
# ("linux-gnu", "darwin*", ...). Computed once, used below by
# `_is_path_already_qualified` to gate Windows-only path forms (a
# drive-letter prefix, a leading backslash) behind actually running on
# Windows -- see that function's own docstring for why (Codex review,
# fresh evidence: a POSIX relative filename that happens to start with a
# single character followed by a literal `:`, e.g. `a:baseline.json`,
# would otherwise be misrecognized as an already-qualified Windows path on
# every platform, not just Windows).
case "$OSTYPE" in
  msys* | cygwin* | win32*) _RUNNING_ON_WINDOWS=true ;;
  *) _RUNNING_ON_WINDOWS=false ;;
esac

# Shared by every `$PWD`-anchoring decision below ($_PY_BIN canonicalization,
# `_report_query`'s report-path anchoring): a path is "already qualified" --
# must NOT get a `$PWD/` prefix -- when it's POSIX-absolute on any platform,
# or (Windows only) drive-absolute (`C:\...`), drive-relative (`C:foo`, no
# separator after the drive letter -- relative to that drive's own current
# directory, a distinct real Windows path form this script has no way to
# resolve either way, so a `$PWD/` prefix would be unconditionally wrong),
# UNC (`\\server\share\...`), or root-relative (`\foo`). Gated on
# `$_RUNNING_ON_WINDOWS` rather than applied unconditionally, since a bare
# `?:*`/`\\*` pattern would otherwise also match a genuine POSIX relative
# filename that happens to start with that shape (e.g. `a:baseline.json`).
_is_path_already_qualified() {
  case "$1" in
    /*) return 0 ;;
  esac
  if [[ "$_RUNNING_ON_WINDOWS" == "true" ]]; then
    case "$1" in
      ?:* | \\*) return 0 ;;
    esac
  fi
  return 1
}

# `mktemp`/`mktemp -d` return a path relative to `$TMPDIR` when that
# variable itself holds a relative value -- a real, if unusual, self-hosted
# runner configuration (confirmed directly: `TMPDIR=relbase mktemp` really
# does emit a relative path). Every one of this script's `mktemp` results
# that crosses into a `(cd "$_PY_SAFE_DIR" && ...)`-wrapped Python
# invocation -- as an argv path Python writes to, or as a base config path
# read back off disk -- resolves against that *new* CWD instead of the
# caller's, not the runner's working directory, on such a runner (first
# found for `$BASELINE_DIR`'s own derived paths; the release/compile-
# context config overlay paths below hit the identical shape, Codex
# review).
#
# Prefixes with `$PWD` only when `$1` is not already qualified -- the exact
# `_is_path_already_qualified` string check `base_source`'s own
# absolutization uses just above, not a `cd "$1" && pwd` subshell
# round-trip. That round-trip broke Windows/Git-Bash real CI runs (Codex
# review, fresh evidence): a bare `mktemp`-returned path there can be an
# MSYS-internal alias (e.g. `/tmp/...`) rather than the drive-mounted form
# (`/c/Users/.../Temp/...`) MSYS's own argv-to-native-path auto-translation
# recognizes -- `cd`/`pwd` never re-resolves that alias to the recognized
# form (no symlink is actually involved to dereference), so the once-good,
# translatable path was replaced with an untranslatable one, silently
# corrupted into a garbage native path (`\tmp\tmp.XXXX`, no drive) the
# moment it crossed into the Python subprocess. Plain string-prefixing
# leaves an already-qualified path (whichever alias form it is) exactly as
# `mktemp` produced it -- the same form every pre-existing, working
# invocation already relied on -- and only ever touches the genuinely
# relative case this helper exists to fix.
# Neutralize a workflow-controlled value before it is printed into a
# GitHub annotation or job-log line. Annotations and workflow commands are
# *line-delimited*, so any value carrying CR/LF ends the line it is printed
# inside and everything after it is parsed as a NEW workflow command --
# `depth: $'headers\n::add-mask::secret'` really did forge an `::add-mask::`
# of its own through the "Command:" line below (reproduced, then fixed;
# Codex review, PR #1233).
#
# The same helper, contract and escaping order as
# `validate-inputs.sh`'s own `_sanitize_annotation` -- `%` first, then
# CR/LF, matching `actions/toolkit`'s `escapeData`, because the runner
# percent-decodes a workflow command's message data and a value carrying
# the literal five characters `%0A::error::` would otherwise become a real
# line break after this script is done with it. `printf`, never `echo`: an
# `echo` under `xpg_echo` expands backslash escapes, turning a literal
# `\n::error::` into a real newline after the CR/LF collapse has already
# run.
#
# This sanitizes what is *displayed*. `CMD` itself keeps the exact bytes
# the CLI must receive: argv is not line-delimited, so a newline inside one
# argument is data there, not a command.
_sanitize_annotation() {
  printf '%s' "${1//%/%25}" | tr '\r\n' '  '
}

_mktemp_canonical() {
  if ! _is_path_already_qualified "$1"; then
    printf '%s\n' "$PWD/$1"
  else
    printf '%s\n' "$1"
  fi
}

# ---------------------------------------------------------------------------
# Helper: append a flag with value(s) to the command array.
# Prefer one item per line (a YAML block scalar, e.g. `headers: |`) — that
# supports path values containing spaces. A value with no newline falls back
# to legacy whitespace-splitting for backward compatibility with the
# documented single-line "space-separated" form; a space-containing path
# still cannot be expressed on a single line this way.
#
# Deliberately avoids process substitution (`< <(...)`) — a `while read`
# fed by a here-string (`<<<`) gets the same "no subshell, so CMD+=(...)
# survives the loop" property without it, and unlike process substitution
# is portable to macOS's stock (GPLv2-frozen) bash 3.2 and behaves
# consistently under Windows Git Bash.
# ---------------------------------------------------------------------------
# Helper shared by add_flag()/add_sided_flag(): splits a single-line legacy
# value on IFS whitespace into the global _SPLIT_ITEMS array, with pathname
# expansion (globbing) disabled for the split.
#
# Plain `for item in $value` (unquoted) performs BOTH word-splitting AND
# pathname expansion on the result -- add_flag_shlex_split()'s own fallback
# path already documents this exact risk for itself ("this naive fallback
# ... letting untrusted checkout content influence the compile context") and
# refuses to fall back rather than risk it, but add_flag()/add_sided_flag()
# had the identical unquoted pattern with no such guard: a caller-controlled
# single-line value of exactly "*" (or any string that happens to match a
# real path in the runner's own working directory) silently expanded to
# every file the glob matched instead of being passed through as the
# literal string (confirmed by direct execution; Codex review, PR #919).
# `set -f` (POSIX noglob) suppresses that expansion while leaving
# word-splitting intact, which is exactly what the legacy single-line form
# is documented to do. The prior glob setting is restored afterward rather
# than unconditionally re-enabled, in case the caller already had `set -f`
# in effect for its own reasons.
_split_legacy_value() {
  local value="$1"
  local restore_glob=0
  case $- in *f*) ;; *) restore_glob=1 ;; esac
  set -f
  _SPLIT_ITEMS=()
  local item
  for item in $value; do
    _SPLIT_ITEMS+=("$item")
  done
  if [[ "$restore_glob" -eq 1 ]]; then
    set +f
  fi
}

add_flag() {
  local flag="$1"
  local value="$2"
  local item
  if [[ -z "$value" ]]; then
    return
  fi
  if [[ "$value" == *$'\n'* ]]; then
    while IFS= read -r item; do
      [[ -n "$item" ]] && CMD+=("$flag" "$item")
    done <<< "$value"
  else
    _split_legacy_value "$value"
    for item in ${_SPLIT_ITEMS[@]+"${_SPLIT_ITEMS[@]}"}; do
      CMD+=("$flag" "$item")
    done
  fi
}

# ---------------------------------------------------------------------------
# Helper: like add_flag(), but a single-line value is split the way
# abicheck's own compiler-flags string splitting works server-side
# (quote-aware -- a value like -DMSG="hello world" stays one token, and an
# unquoted Windows path's backslashes survive intact), not add_flag()'s
# plain bash word-splitting (Codex review, PR #757: routing gcc-options
# through add_flag()'s unquoted `for item in $value` broke a quoted value
# into malformed tokens, since bash word-splitting treats `"` as a literal
# character once the string is already sitting in a variable, unlike a
# real shell command line). Used only for the gcc-options -> --compiler-
# -option conversion, where the CLI flag it now maps to used to be one
# scalar --gcc-options string abicheck itself shlex-split.
#
# Delegates to the real `abicheck._compiler_options.split_gcc_options`
# (imported, not reimplemented) via `python3`/`python` (`_PY_BIN`, resolved
# once above) rather than `eval`: xargs-style or eval-based quote parsing
# would either use its own, different quoting dialect or -- for eval --
# actually execute a `$(...)`/backtick command substitution embedded in
# untrusted Action input, which this must not do. Importing the real
# function (rather than an inline reimplementation) is deliberate: three
# earlier revisions of an inline copy each independently regressed a real
# case a review round caught (real POSIX escape sequences, `#`-as-comment
# truncation, unquoted Windows-path corruption -- see that function's own
# docstring for the full history) precisely because there were two copies
# of the same non-trivial tokenizer to keep in sync. `abicheck` is always
# importable here in the common case: action.yml's "Install abicheck" step
# runs `pip install` before "Run abicheck" invokes this script, so `_PY_BIN`
# (found via the same `command -v python3`/`python` PATH lookup pip itself
# resolved against) already has it on its import path -- verified once, up
# front, via `$_PY_BIN_HAS_ABICHECK` (see its own definition above), for the
# self-hosted-runner case where that assumption doesn't hold.
#
# Falls back to add_flag()'s plain whitespace split ONLY when doing so is
# provably equivalent to real quote-aware parsing -- the value contains
# none of `"`/`'`/`\`, so there is nothing for real parsing to interpret
# differently from bash's own unquoted word-splitting in the first place.
# When the real parser is unavailable AND the value actually needs one
# (Codex review, fresh evidence: an earlier revision fell back
# unconditionally, silently corrupting a quoted value like
# `-DMSG="hello world"` into malformed tokens under a different, wrong
# compile context instead of failing), this fails the Action loud rather
# than guess.
# ---------------------------------------------------------------------------
add_flag_shlex_split() {
  local flag="$1"
  local value="$2"
  local item split py_exit
  if [[ -z "$value" ]]; then
    return
  fi
  if [[ "$value" == *$'\n'* ]]; then
    # Multi-line (YAML block scalar): one line is already one full,
    # space-safe token -- add_flag()'s own multi-line handling, no shlex
    # parsing needed.
    add_flag "$flag" "$value"
    return
  fi
  if [[ -z "$_PY_BIN" || "$_PY_BIN_HAS_ABICHECK" != "true" ]]; then
    # add_flag()'s own `for item in $value` is unquoted, so beyond just
    # whitespace-splitting it also performs pathname (glob) EXPANSION --
    # `*`/`?`/`[` are not provably safe to fall back on the same way
    # quote/backslash characters aren't (Codex review, fresh evidence): a
    # configured value like `-DPATTERN=*` would silently rewrite to
    # whatever filenames exist in the current directory at the time this
    # runs (the analyzed, potentially PR-controlled checkout -- unlike the
    # real parser's own invocation, this naive fallback never `cd`s
    # anywhere), letting untrusted checkout content influence the compile
    # context.
    if [[ "$value" == *'"'* || "$value" == *"'"* || "$value" == *'\'* \
          || "$value" == *'*'* || "$value" == *'?'* || "$value" == *'['* ]]; then
      echo "::error::$flag value '$value' contains quoting/escaping or glob metacharacters that require abicheck's own parser to interpret correctly, but no working Python interpreter with abicheck importable is available on this runner (resolved interpreter: '${_PY_BIN:-<none found on PATH>}'). Refusing to fall back to plain whitespace splitting, which would silently produce a different, wrong compile context (and, for glob metacharacters, could expand based on files present in the analyzed checkout)."
      exit 1
    fi
    add_flag "$flag" "$value"
    return
  fi
  # $value is passed on stdin, not as a positional argv element (Codex
  # review, fresh evidence: a value containing a POSIX-style path segment,
  # e.g. -I/build/generated, triggered Git Bash/MSYS's automatic argv
  # path-conversion on the windows-latest CI runner when forwarded as a
  # positional arg to this native, non-MSYS python.exe -- silently
  # rewriting it into a Windows path, e.g. inserting "Program Files" and
  # its embedded space, before this script ever saw it. stdin content is
  # never subject to that conversion (only actual argv strings are), so
  # this sidesteps the whole class of corruption regardless of the exact
  # value shape that triggers it -- not just the one case that surfaced it.
  split="$(printf '%s' "$value" | (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import sys

from abicheck._compiler_options import split_gcc_options

for tok in split_gcc_options(sys.stdin.read()):
    print(tok)
'))"
  py_exit=$?
  # This script deliberately has no `set -e` (Codex review, fresh evidence):
  # split_gcc_options() raises ValueError on malformed quoting (e.g. an
  # unbalanced quote), which without this check would silently leave $split
  # empty and every requested compiler option dropped instead of failing --
  # an invalid configuration must not produce an apparently-successful
  # comparison under the wrong macros/include paths.
  if [[ $py_exit -ne 0 ]]; then
    echo "::error::$flag value '$value' could not be parsed (malformed quoting/escaping, e.g. an unbalanced quote) -- refusing to silently drop or corrupt the requested compiler options."
    exit 1
  fi
  while IFS= read -r item; do
    # Codex review, fresh evidence: on windows-latest, $_PY_BIN resolves to
    # native python.exe, whose print() writes CRLF line endings by default
    # (Python's text-mode stdout translates "\n" to os.linesep on write,
    # regardless of whether stdout is a console or -- as here -- a pipe).
    # bash's `read` only splits on LF, so it would otherwise leave a
    # trailing \r glued onto every token, corrupting each forwarded flag
    # (e.g. -DFOO=1 arrives as -DFOO=1\r). Harmless no-op on POSIX, where
    # this never appears in the first place.
    item="${item%$'\r'}"
    [[ -n "$item" ]] && CMD+=("$flag" "$item")
  done <<< "$split"
}

# ADR-040 L1: the per-side header/include inputs map to the side-aware --header/
# --include flags, prefixing each value with old=/new= (e.g. --header old=inc).
#
# A single-line value is word-split on whitespace (one flag per word) so a
# YAML input like `old-header: "a.h b.h"` still yields two `--header`
# entries -- this is deliberate for the genuinely *list*-valued inputs this
# function was written for (headers/includes/paths). It must NOT be used for
# a value that is a single opaque string that may itself contain spaces (a
# version label like "1.0 (release build)") -- use add_sided_scalar_flag
# for those instead, which passes the value through unsplit. Prefer
# newline-separated values over relying on word-splitting at all when a
# list input's own entries might contain spaces (e.g. a path).
add_sided_flag() {
  local flag="$1"
  local side="$2"
  local value="$3"
  local item
  if [[ -z "$value" ]]; then
    return
  fi
  if [[ "$value" == *$'\n'* ]]; then
    while IFS= read -r item; do
      [[ -n "$item" ]] && CMD+=("$flag" "${side}=${item}")
    done <<< "$value"
  else
    _split_legacy_value "$value"
    for item in ${_SPLIT_ITEMS[@]+"${_SPLIT_ITEMS[@]}"}; do
      CMD+=("$flag" "${side}=${item}")
    done
  fi
}

# Scalar counterpart of add_sided_flag: the value is a single opaque string
# (e.g. a version label) that must reach the CLI exactly as given, including
# any embedded whitespace -- never split into multiple flags. A version
# label like "1.0 (release build)" previously lost everything but its last
# whitespace-separated word when routed through add_sided_flag's word-split.
add_sided_scalar_flag() {
  local flag="$1"
  local side="$2"
  local value="$3"
  if [[ -z "$value" ]]; then
    return
  fi
  CMD+=("$flag" "${side}=${value}")
}

# Whether CMD already carries a literal "--config" token -- used by callers
# that may have already emitted their own `--config` (e.g.
# add_compile_context_flags's explicit-build-config merge, below) to skip a
# later unconditional `add_single_flag "--config" ...` that would otherwise
# add a conflicting second one.
_cmd_has_config_flag() {
  local _c
  for _c in "${CMD[@]}"; do
    [[ "$_c" == "--config" ]] && return 0
  done
  return 1
}

add_single_flag() {
  local flag="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    CMD+=("$flag" "$value")
  fi
}

# The public evidence ladder, exactly as the CLI's own `DepthParam` accepts
# it. `depth` is a raw, unvalidated workflow input and one of the few that
# reach `CMD` as a *value* rather than a path, so it is validated here
# rather than forwarded and left to the CLI: an unknown value is a usage
# error there anyway (exit 64, after Python and dependency setup), and
# anything off this list has no business being echoed into the job log.
# `depth: $'headers\n::add-mask::secret'` really did forge a workflow
# command of its own through the "Command:" line (reproduced, then fixed;
# Codex review, PR #1233): `_sanitize_annotation` closes that for every
# displayed value, and this closes it at the source for the one input the
# newly-widened release path made reachable.
#
# Sets `_DEPTH_LC` to the lowercased rung -- the CLI's own `DepthParam` is
# case-insensitive, so `depth: BUILD` is valid and must keep working -- or
# fails loud. Used by every mode that forwards `--depth`, so the rung means
# the same thing on all of them.
#
# Assigns a global rather than echoing its result, and callers must NOT
# wrap it in `$(...)`: a command substitution runs the function in a
# subshell, where `exit 1` ends only that subshell -- the script would sail
# on with the *error message* captured as the depth value and forward
# `--depth ::error::mode:...` to the CLI. (Caught by probing the forwarded
# value while fixing the injection below, which is the only reason it
# isn't still here: the annotation-forgery assertion alone passes either
# way.)
_DEPTH_LC=""
_resolve_depth_lc() {
  local raw="${1:-}"
  _DEPTH_LC=""
  if [[ -z "$raw" ]]; then
    return 0
  fi
  # Portable lowercasing: ${var,,} is bash-4+ only (see add_flag above).
  local lowered
  lowered=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')
  case "$lowered" in
    binary | headers | build | source)
      _DEPTH_LC="$lowered"
      ;;
    *)
      printf '%s\n' "::error::mode: ${MODE} does not accept depth: $(_sanitize_annotation "$raw") -- it must be one of binary, headers, build, or source."
      exit 1
      ;;
  esac
}

# Phase 7 (one-comparison-product.md §4.1/§4.2, ADR-037 D8.1): --ast-frontend/
# --compiler/--compiler-prefix/--compiler-option/--sysroot/--nostdinc/--lang
# are gone from `compare`/`dump` entirely (CONFIG class, no CLI override --
# `.abicheck.yml`'s `compile:` block is their only source now). This
# Action's own cross-compilation inputs (ast-frontend/gcc-path/gcc-prefix/
# gcc-options/sysroot/nostdinc/lang) still exist, so forwarding them now
# means synthesizing a `compile:` block into a project config the run reads
# via --config, instead of passing per-run flags. `dump`, single-pair
# `compare`, and `compare --no-baseline`'s single audited artifact all call
# this same `add_compile_context_flags` helper (ADR-068's Action-
# input-lifecycle amendment removed `mode: scan`, which used to be this
# helper's other caller, entirely). One real, accepted narrowing this
# brings: `compile.options` (below) rejects any entry containing whitespace,
# where the CLI's own now-removed --compiler-option flag once forwarded such
# a flag verbatim (see `gcc-options`'s own action.yml description for the
# full account).
#
# When the caller ALSO names their own build-config, this Action merges the
# two: the synthesized compile: overlay is folded into a COPY of the named
# build-config file (Action input wins on a key conflict), read and merged
# through the same `_merge_config_overlay_with_discovered_project_config`
# helper's "explicit" mode -- see that function's own docstring for why an
# explicit build-config is fully trusted (no key stripping), unlike the
# auto-discovered case below. An earlier revision rejected this combination
# outright as "mutually exclusive"; that was itself a real regression, since
# a workflow could legitimately combine both before Phase 7 demoted these
# flags to config (Codex review, PR #1159, second round).
#
# When build-config is NOT given, this Action's own synthesized overlay is
# not the whole config picture: `_resolve_compare_config` only ever reads
# ONE `--config` document (`config if config is not None else
# discover_project_config()`, `cli_compare_helpers.py`) -- an explicit
# `--config` fully replaces auto-discovery, it never augments it. Writing
# only the topology/compile keys to `--config` would therefore silently
# DROP the repository's own auto-discovered `.abicheck.yml` (severity/
# suppress/scope/bundle/... blocks) the moment any of these forwarding
# inputs is used, turning on e.g. `dso-only` into an accidental reset of
# every other project setting (Codex review, PR #1159). So this Action
# does the discovery itself, from the real project directory (`$PWD`,
# captured before any inline-Python invocation's own `cd "$_PY_SAFE_DIR"`)
# and merges the synthesized keys into a COPY of whatever
# `discover_project_config()` would have found -- Action inputs winning on
# a key conflict, exactly the same precedence an explicit build-config
# input already takes over auto-discovery. No project config found is the
# base case: the overlay alone is written, unchanged from before.
#
# When build-config IS given, callers pass `merge_mode="explicit"` (below)
# instead of running discovery at all: the base document is read directly
# from the named build-config file rather than found by walking up from a
# directory (Codex review, PR #1159, second round -- combining an explicit
# build-config with a topology/compile-context Action input used to be a
# hard rejection here, which was itself a real regression, since a
# workflow could combine both before Phase 7/7d demoted the corresponding
# flags to config). The two modes deliberately differ on trust, not just on
# how the base document is found: an auto-discovered `.abicheck.yml` is
# untrusted, repository-controlled content -- exactly what
# `cli_options.py`'s `compile.compiler` gate and ADR-032 D5's `build.query`
# gate exist to withhold "explicit --config, operator authorized this to
# run" status from -- so discover mode strips both keys before merging. An
# *explicit* build-config input is itself a deliberate operator action (the
# user told this Action to use this exact file), which is already the
# trusted case those same gates exist to allow -- `cli_options.py`'s own
# `explicit_config = build_config is not None` check draws the identical
# line. So explicit mode does NOT strip either key: a project that
# genuinely wants `build.query`/`compile.compiler` to run already opted in
# by naming this file, and stripping it here would silently discard
# behavior the user explicitly authorized.
_merge_config_overlay_with_discovered_project_config() {
  # $1: overlay JSON object (already built by the caller, e.g.
  #     '{"compile": {...}}' or '{"release": {...}, "gate": {...}}').
  # $2: output path to write the merged document to.
  # $3: "discover" mode (default, $4 omitted) -- a project directory to
  #     discover a `.abicheck.yml` from (walking up to the filesystem root,
  #     same as `discover_project_config()`). "explicit" mode ($4 ==
  #     "explicit") -- the path to the explicit build-config file to use as
  #     the base document directly, no discovery/walk.
  # $4: optional; "explicit" selects explicit-build-config mode described
  #     above. Omitted (or any other value) is the default discovery mode.
  # $5: optional; the --sources root, used two ways: (a) the root a
  #     discovered build.compile_db glob resolves against (inline.py:
  #     `sorted(sources.glob(cfg.compile_db))`), and (b) in "discover" mode,
  #     the root this function ALSO checks -- non-recursively, no walk-up --
  #     for its own separate .abicheck.yml, mirroring embed_build_source()'s
  #     own `build_config or discover_build_config(raw_sources)` selection
  #     (Codex review, fresh evidence): when --sources names a directory
  #     with its own config, the native CLI would use THAT file exclusively
  #     for build:/sources: (dump) or build:/sources:/compile:/source:/
  #     debug: (dump only -- see $6) whenever no explicit --config is
  #     given -- but this Action always ends up passing an explicit --config
  #     once any compile-context input is set, which permanently short-
  #     circuits that discovery (`build_config is not None`), silently
  #     dropping the sources root's own build settings. Passed by
  #     add_compile_context_flags (which knows $INPUT_SOURCES), omitted by
  #     add_release_topology_config_flags (compare's release fan-out never
  #     reads build.compile_db and never resolves a --sources tree at all, so
  #     there is no root to check against and the field is always stripped
  #     there, same as before).
  # $6: optional; "pairwise" when the caller's own MODE resolves compile:/
  #     source:(singular)/debug: PAIR-WIDE, applying the single shared
  #     --config to two independently-parsed operands (single-pair `compare
  #     OLD NEW` only -- resolve_compile_context() applies compile: "to
  #     both sides", cli_helpers_compare.py's resolved_cfg.source_method/
  #     debug_format are each resolved once for the whole comparison).
  #     Omitted (or any other value) means the caller's MODE resolves them
  #     SINGLE-SIDED (dump, compare --no-baseline's single audited artifact --
  #     neither has an "other side" these could leak into: dump has one
  #     operand, and the audit-only shape's own -H/-I apply only to the
  #     audited artifact, never to a baseline side it doesn't have). Only
  #     "pairwise" excludes compile:/source:/debug: from
  #     the sources-root block-replacement below -- everywhere else, a
  #     --sources tree's own config is the ONLY document those three blocks
  #     can meaningfully come from for that one operand, exactly matching
  #     embed_build_source()'s/merge_compile_config()'s own single-sided
  #     `build_config or discover_build_config(sources)` selection (Codex
  #     review, fresh evidence, PR #1171: the prior fix excluded all three
  #     unconditionally, which was correct for pairwise compare but silently
  #     regressed dump's own single-sided use of a --sources tree's
  #     compile:/debug: settings the moment any compile-context input was
  #     also set).
  # $7: optional; "true" selects a genuine per-field MERGE for `compile:`
  #     specifically (`abicheck.action_config_overlay.apply_sources_root_
  #     config_blocks`'s own `merge_compile` parameter) instead of the
  #     wholesale REPLACE every other promoted block still uses -- Codex
  #     review, fresh evidence, PR #1222 fourth round, second finding on
  #     this fix: within the single-sided ($6 omitted) bucket, `compile:`
  #     does NOT always resolve the same way. `dump`/`scan --against`
  #     genuinely select `compile:` from exactly ONE document (the
  #     `--sources` tree's own, when no explicit `--build-config` is given
  #     -- `cli_options.merge_compile_config`'s own `build_config if
  #     explicit_config else discover_build_config(sources)`, mutually
  #     exclusive alternatives), the same single-document shape as
  #     build:/sources:/source:/debug: -- pass "" (the default) there.
  #     `compare`'s own single-sided shape (a stored-snapshot old operand,
  #     `_compile_context_sources_pairwise`'s own docstring) is different:
  #     the checkout-root document's `compile:` is ALREADY independently
  #     resolved first, unconditionally
  #     (`cli_compare_helpers.resolve_compile_context(..., build_config=
  #     cfg_path, ...)`), and the live side's own `--sources` tree only
  #     folds ON TOP of that via a SECOND `merge_compile_config` call
  #     (`compare.py`'s `_maybe_dump_side`) -- a genuine two-stage MERGE,
  #     so pass "true" there. Meaningless (never read) when $6 is
  #     "pairwise", since `compile:` is excluded from `_sources_root_blocks`
  #     entirely in that case.
  local overlay_json="$1"
  local out_path="$2"
  local base_source="$3"
  local merge_mode="${4:-discover}"
  local sources_root="${5:-}"
  local sources_pairwise="${6:-}"
  local sources_merge_compile="${7:-}"
  # Codex review, PR #1159, third round: in "explicit" mode base_source is
  # the caller-supplied build-config input, which is very often a
  # checkout-relative path (e.g. `build-config: .abicheck.yml`) -- exactly
  # how a real workflow names it, and exactly what already works when passed
  # straight to the native CLI (which never changes directory). The merge
  # below runs inside `(cd "$_PY_SAFE_DIR" && ...)`, so a relative
  # base_source would resolve against that scratch directory instead of the
  # real Action working directory, and Python's own `Path(...).resolve()`
  # would then report "does not exist" for a file that is right there in the
  # checkout. Discover mode's own base_source ($PWD, passed by every caller)
  # is already absolute, so this is a no-op there -- but absolutize
  # unconditionally rather than special-casing on mode, since any future
  # caller passing a relative discover-mode directory would hit the exact
  # same bug class. Must happen here, in bash, before the value ever crosses
  # into the $_PY_SAFE_DIR-scoped Python subprocess -- not via a Python-side
  # directory trick, which would only fix this one call path and leave the
  # same mistake available to the next relative-path input.
  if ! _is_path_already_qualified "$base_source"; then
    base_source="$PWD/$base_source"
  fi
  # Same relative-path-vs-$_PY_SAFE_DIR bug class as base_source above:
  # $INPUT_SOURCES (add_compile_context_flags's own sources_root) is very
  # often a checkout-relative path (e.g. `sources: src`) too.
  if [[ -n "$sources_root" ]] && ! _is_path_already_qualified "$sources_root"; then
    sources_root="$PWD/$sources_root"
  fi
  if [[ -z "$_PY_BIN" || "$_PY_BIN_HAS_ABICHECK" != "true" ]]; then
    # Same "fail loud rather than silently produce a wrong compile/release
    # context" precedent as add_flag_shlex_split's own missing-interpreter
    # guard above: silently falling back to "just the overlay" here would
    # reintroduce the exact config-dropping bug this function exists to fix,
    # on precisely the runners least able to detect it.
    echo "::error::mode: ${MODE} needs a working Python interpreter with abicheck importable to merge this Action's synthesized config overlay with the repository's own auto-discovered .abicheck.yml (resolved interpreter: '${_PY_BIN:-<none found on PATH>}'). Refusing to silently drop the project's own config."
    # $out_path (the caller's already-created overlay, e.g.
    # $_COMPILE_CONTEXT_CONFIG_OVERLAY/$_RELEASE_TOPOLOGY_CONFIG_OVERLAY) is
    # named directly here rather than through a global -- this function is
    # shared by both callers, so cleaning up "whichever global just got set"
    # would need to guess which one, while $out_path always names the right
    # file regardless of caller (Codex review, fresh evidence: this exit
    # happens after the caller's own mktemp, before the main EXIT trap
    # further down is installed).
    _rm_overlay_on_early_exit "$out_path"
    exit 1
  fi
  (cd "$_PY_SAFE_DIR" \
   && ABICHECK_MERGE_MODE="$merge_mode" \
      ABICHECK_BASE_CONFIG_SOURCE="$base_source" \
      ABICHECK_OVERLAY_JSON="$overlay_json" \
      ABICHECK_SOURCES_ROOT="$sources_root" \
      ABICHECK_SOURCES_PAIRWISE="$sources_pairwise" \
      ABICHECK_SOURCES_MERGE_COMPILE="$sources_merge_compile" \
      PYTHONPATH= "$_PY_BIN" - "$out_path" <<'PYEOF'
# Discovers the real project .abicheck.yml (if any) the same way
# discover_project_config() does -- config_paths.find_config_in_dir(),
# walking from the project start directory up to the filesystem root, first
# match wins -- and merges the overlay JSON (read from an env var, not
# stdin -- stdin here is already this script's own source, fed by the
# caller's heredoc) into a shallow copy of it, one top-level key at a time
# (overlay wins on conflict within a shared top-level key; every other key
# the project config carries is passed through untouched). Writes the
# merged result as JSON, a valid YAML subset abicheck's own yaml.safe_load
# parses identically.
#
# Two trust/correctness properties this merge must preserve, since the
# result is always written to the path the caller passes via --config --
# every "was --config explicit" check in the engine treats that as operator
# authorization, regardless of how this file's content was assembled
# (Codex review, PR #1159):
#
# 1. An auto-discovered .abicheck.yml is untrusted, repository-controlled
#    content -- exactly what cli_options.py's compile.compiler gate and
#    ADR-032 D5's build.query gate exist to withhold executable-authorized
#    "explicit --config" status from. Copying either key into this
#    synthesized, always-explicit overlay would launder that untrusted
#    document into "operator authorized this to run" the moment any
#    unrelated Action input (dso-only, ast-frontend, ...) is set. So both
#    keys are stripped from the discovered document's copy before merging;
#    a project that genuinely wants either to run must supply it via this
#    Action's own explicit build-config input instead (a real, deliberate
#    operator action), never inherit it silently from repo-discovered
#    config.
# 2. A relative path inside the discovered config (e.g.
#    compile.include_dirs: [include]) resolves against that config's own
#    *project root* (config_paths.project_root_for_config), not against
#    wherever this scratch overlay file happens to be written (an
#    mktemp path, typically under /tmp). Left alone, merging silently
#    changes the base every relative include dir resolves against,
#    dropping headers from extraction with no diagnostic. So every
#    compile.include_dirs entry from the discovered document is rewritten
#    to an absolute path against the real project root before merging.
# 3. resource_limits.max_bundle_facts_decode_nodes is the pre-json.loads()
#    decode-bomb budget compare_bundle_facts.dispatch() only lets an
#    *explicit* --config raise past the conservative default (Codex
#    review, PR #1174) -- exactly the "explicit --config" status this
#    merged overlay would launder a discovered value into. Capped (not
#    stripped -- a lower value is never a decode-bomb risk) via the same
#    resolve_max_json_object_nodes_cfg() the CLI itself calls.
import json
import os
import sys
from pathlib import Path

import yaml

from abicheck.action_config_overlay import (
    apply_sources_root_config_blocks,
    discovered_compile_db_resolves,
    rebase_relative_config_paths,
    strip_untrusted_execution_keys,
    validate_base_config,
)
from abicheck.config_paths import (
    discover_build_config,
    find_config_in_dir,
)


def _gha_escape(text: object) -> str:
    # Codex review (P2), PR #1159: every value interpolated into an
    # ``::error::``/``::warning::`` workflow command below is untrusted --
    # a path derived from the Action's own build-config input (which a PR
    # author fully controls on a pull_request trigger, per action/AGENTS.md's
    # "treat every INPUT_*/GITHUB_* as untrusted" rule) or the text of a
    # PyYAML ParserError/ScannerError, which echoes attacker-controlled
    # config-file bytes verbatim. GitHub's runner parses workflow commands
    # line-by-line from stdout, so an embedded "\n" lets the remainder of
    # an untrusted value start a *new* command line (e.g. a smuggled
    # "::add-mask::..." or another "::error::..."), and an embedded "%"
    # would similarly corrupt a real property-escaped value if this text
    # were ever nested inside one. Escaping matches GitHub's own documented
    # message-data escaping (%->%25, CR->%0D, LF->%0A) so the annotation
    # always renders as the single, literal line intended -- never a
    # second, attacker-authored command.
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _validate_or_exit(doc: dict[str, object], source_path: Path) -> None:
    # Codex review, fresh evidence: a base document is loaded here via a
    # bare `yaml.safe_load` -- syntactically valid YAML, but never run
    # through the real strict-schema check (`BuildConfig._validate_
    # structure`, unknown keys/wrong-typed values) the native CLI's own
    # `load_build_config`/`discover_project_config` loading path always
    # applies. Left unchecked, a structurally invalid document (e.g.
    # `release: []` instead of a mapping, `compile.lang: 7` instead of a
    # string) can have its own invalid key silently REPLACED by this
    # Action's own overlay merge below (an Action input sharing the same
    # top-level key wins unconditionally) -- masking a real user config
    # error as if it had been valid all along, instead of the loud usage
    # error the equivalent native CLI invocation would raise. Validating
    # here, before any merge happens, surfaces the same error the user
    # would see running abicheck directly against this file. Shared with
    # actions/check-target/action.yml's own equivalent "Generate
    # assurance-overlay config" step via
    # abicheck.action_config_overlay.validate_base_config (Codex review,
    # second finding) so the two call sites can't independently drift on
    # what counts as a valid base document.
    try:
        validate_base_config(doc)
    except ValueError as exc:
        print(
            f"::error::the config at {_gha_escape(source_path)} is invalid: "
            f"{_gha_escape(exc)}. Refusing to silently proceed with (or "
            "merge on top of) a malformed project config -- fix the file "
            "or remove it.",
            file=sys.stderr,
        )
        sys.exit(1)


out_path = sys.argv[1]
overlay = json.loads(os.environ["ABICHECK_OVERLAY_JSON"])
merge_mode = os.environ.get("ABICHECK_MERGE_MODE", "discover")
base_source = os.environ["ABICHECK_BASE_CONFIG_SOURCE"]

base: dict[str, object] = {}
found_path: Path | None = None

if merge_mode == "explicit":
    # The user named this exact file via the Action's own build-config
    # input -- a deliberate operator action, not a directory walk. Read it
    # directly as the base document; no discovery, no fallback to "no
    # config found" (a missing/unreadable explicit build-config is a usage
    # error, matching the ordinary CLI's own explicit ``--config`` failure
    # mode).
    #
    # os.path.abspath, not Path.resolve(): the latter also dereferences a
    # symlink, which the native CLI's own --config never does (Click's
    # click.Path has no resolve_path=True here, so cli_options.py's
    # project_root_for_config(cfg) sees exactly the path the user passed).
    # A symlinked build-config (e.g. a checkout-level .abicheck.yml ->
    # /shared/config.yml) would otherwise have project_root_for_config()
    # anchor a relative compile.include_dirs entry under the symlink's
    # *target* directory instead of the logical location the user actually
    # named, silently parsing a different header surface (Codex review).
    found_path = Path(os.path.abspath(base_source))
    if not found_path.is_file():
        print(
            f"::error::the explicit build-config {_gha_escape(found_path)} "
            "does not exist or is not a file. Refusing to silently proceed "
            "as if it were unconfigured.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        loaded = yaml.safe_load(found_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"::error::failed to parse the explicit build-config "
            f"{_gha_escape(found_path)}: {_gha_escape(exc)}. Refusing to "
            "silently proceed as if it were unconfigured.",
            file=sys.stderr,
        )
        sys.exit(1)
    if isinstance(loaded, dict):
        _validate_or_exit(loaded, found_path)
        base = loaded
else:
    start = Path(base_source or ".").resolve()
    candidates = [start, *start.parents]
    for directory in candidates:
        found = find_config_in_dir(directory)
        if found is None:
            continue
        found_path = found
        try:
            loaded = yaml.safe_load(found.read_text(encoding="utf-8"))
        except Exception as exc:
            # Matching the ordinary CLI's own discover_project_config() path
            # (cli_helpers_compare.py): a malformed *discovered* config is a
            # real usage error there, not a silent "proceed as if unconfigured"
            # -- so this Action must not quietly drop every one of the
            # project's own settings (severity/suppress/scope/bundle/...)
            # just because a synthesized topology/compile-context input was
            # also set (Codex review, PR #1159).
            print(
                f"::error::failed to parse the discovered project config "
                f"{_gha_escape(found)}: {_gha_escape(exc)}. Refusing to "
                "silently proceed as if no project config existed -- fix "
                "the file or remove it.",
                file=sys.stderr,
            )
            sys.exit(1)
        if isinstance(loaded, dict):
            _validate_or_exit(loaded, found)
            base = loaded
        break

    # Codex review, fresh evidence: mirror embed_build_source()'s own
    # `build_config or discover_build_config(raw_sources)` selection
    # (abicheck/buildsource/embed.py). That function is what actually reads
    # build:/sources: for L3-L5 embedding, and it is a SEPARATE, narrower
    # lookup than the checkout-root walk just above -- non-recursive,
    # anchored at the --sources tree itself, never walking up to parents.
    # When no explicit --config is given, a --sources directory carrying its
    # own .abicheck.yml is used EXCLUSIVELY for those two blocks; the
    # checkout-root config found above (if any) is never even consulted for
    # them. Since this Action always ends up passing an explicit --config
    # once any compile-context input is set, that discovery would otherwise
    # never run at all (`build_config is not None`), silently dropping the
    # sources root's own build settings. Replicate the same outcome here:
    # when --sources names a directory with its own config file (distinct
    # from whatever was found above), its own build:/sources: blocks REPLACE
    # (not merge into) the checkout-root document's own such blocks --
    # exactly as if no explicit --config had been in the way.
    #
    # compile: is conditionally included too -- gated on
    # ABICHECK_SOURCES_PAIRWISE (see this shell function's own $6
    # docstring). A second Codex review (fresh evidence, PR #1159) caught
    # that it is pair-wide for single-pair `compare`, not per-side: compile:
    # flows through resolve_compile_context ("It applies to both sides",
    # cli_compare_helpers.py) -- promoting it from a NEW-only sources-root
    # config there would silently apply NEW-only settings to OLD's own
    # parsing too. But a THIRD review round (fresh evidence, PR #1171) found
    # that blanket exclusion regressed `dump`, which has no "other side"
    # for these to leak into (one operand, period) -- for that, and for the
    # audit-only `compare --no-baseline` shape (also no other side, its own
    # -H/-I applying only to the one audited artifact), `merge_compile_
    # config()`'s own docstring states the --sources tree's config IS the
    # intended, ONLY source of compile: for that one operand, exactly like
    # embed_build_source()'s build_config or discover_build_config(sources)
    # selection already is for build:/sources:. So only "pairwise" callers
    # (a genuinely two-sided `compare`) exclude the three; every other
    # caller (dump, the audit-only shape) keeps promoting them, matching
    # the ORIGINAL five-block version of this fix before the second review
    # round overcorrected it for every mode at once.
    sources_pairwise = os.environ.get("ABICHECK_SOURCES_PAIRWISE", "") == "pairwise"
    # Codex review, fresh evidence, PR #1222 fourth round, second finding:
    # within the single-sided bucket, `compile:` does NOT always resolve
    # the same way -- see this shell function's own $7 docstring and
    # `apply_sources_root_config_blocks`'s own `merge_compile` parameter
    # docstring for the exact distinction (dump/scan single-document
    # REPLACE vs. compare's checkout-then-sources two-stage MERGE).
    # P1 fix (Codex review, fresh evidence, PR #1222 eleventh round --
    # reverses the fourth round's own conclusion documented just below,
    # unchanged for historical context): `compile:` is no longer promoted
    # from the sources root for `mode: compare`'s single-sided shape at
    # all (see `_sources_root_blocks` below) -- `sources_merge_compile`
    # now only decides whether `source:`(singular)/`debug:` may be
    # promoted, unrelated to `compile:`'s own (unchanged) exclusion. See
    # `actions/check-target/action.yml`'s own identical fix and its
    # `_sources_merge_compile` comment for the full account of the P1
    # finding this closes -- `frontends/cli/commands/compare.py`'s
    # `_embed_inline_source_side` always independently folds the live
    # side's own `--sources` tree's `compile:` block on top of the CLI's
    # already-resolved compile context, unconditionally, regardless of
    # whether `--config`/`build-config` was explicit -- so this overlay
    # promoting/merging `compile:` too folds the sources-root document in
    # TWICE, applying a repeat-sensitive flag (`-include`, ...) twice in
    # the final compiler invocation.
    sources_merge_compile = (
        os.environ.get("ABICHECK_SOURCES_MERGE_COMPILE", "") == "true"
    )
    # `source:`(singular)/`debug:` are excluded from sources-root promotion
    # whenever `sources_merge_compile` is set (Codex review, P1, fresh
    # evidence, PR #1222 eighth round) -- that flag is true exactly for
    # `mode: compare`'s own single-sided shape (a stored-snapshot old
    # operand; see `_compile_context_sources_merge_compile`'s own
    # docstring), and unlike `compile:`, `compare`'s real pipeline never
    # resolves `source:`/`debug:` from a per-side `--sources` tree's own
    # document at all: `frontends/cli/commands/compare.py`'s
    # `_embed_inline_source_side` receives both as already-frozen arguments
    # (`_resolved_collect_mode`/`_resolved_debug`), computed once from the
    # checkout-side `resolved_cfg` before any per-side tree is even
    # considered, and forwards them verbatim to the nested dump invocation's
    # own private hooks of the same name -- `frontends/cli/
    # dump_debug_config.resolve_dump_debug_fields` only falls back to
    # resolving a tree's own document when `resolved_debug` is `None`,
    # which never happens on `compare`'s own call path. Promoting these two
    # blocks here for `mode: compare` would therefore let a sources-root
    # document control this run's collection depth (`source:`) or
    # debug-info extraction (`debug:`) in a way the real, non-overlay
    # `compare` invocation never permits. `dump`/`scan`
    # (`sources_merge_compile` unset) are unaffected: their own
    # single-document selection genuinely takes `source:`/`debug:` from the
    # `--sources` tree's own document exclusively
    # (`resolve_dump_debug_config`'s `build_config or
    # discover_build_config(sources)`), the identical shape `build:`/
    # `sources:` already use.
    # `compile:` is EXCLUDED here whenever `sources_merge_compile` is set
    # too now (Codex review, P1, fresh evidence, PR #1222 eleventh round):
    # this bucket now collapses to the SAME two-block set the pairwise
    # bucket already uses -- see `sources_merge_compile`'s own comment
    # above and `actions/check-target/action.yml`'s identical fix for the
    # full account.
    _sources_root_blocks = (
        ("build", "sources")
        if sources_pairwise or sources_merge_compile
        else ("build", "sources", "compile", "source", "debug")
    )
    sources_root_env = os.environ.get("ABICHECK_SOURCES_ROOT", "")
    if sources_root_env:
        sources_found = discover_build_config(Path(sources_root_env))
        if sources_found is not None and sources_found != found_path:
            try:
                sources_loaded = yaml.safe_load(
                    sources_found.read_text(encoding="utf-8")
                )
            except Exception as exc:
                print(
                    f"::error::failed to parse the discovered sources-root "
                    f"config {_gha_escape(sources_found)}: "
                    f"{_gha_escape(exc)}. Refusing to silently proceed as "
                    "if it were unconfigured.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Codex review, fresh evidence: an empty (or non-mapping,
            # e.g. a bare YAML list) --sources-root .abicheck.yml is not
            # "no config found" -- yaml.safe_load returns None/a non-dict
            # for one, but the file still EXISTS, so discover_build_config()
            # still selects it, and load_build_config()'s own `if not
            # isinstance(raw, dict): return BuildConfig()` treats that as an
            # empty (all-default) BuildConfig, not a fallback to whatever
            # else might otherwise apply. Gating this whole replacement on
            # `isinstance(sources_loaded, dict)` skipped it entirely for
            # that case, silently leaving the checkout-root document's own
            # build:/sources: (if any) in place -- exactly the settings the
            # native path would NOT have applied, since
            # discover_build_config()'s selection is exclusive.
            # `sources_loaded if isinstance(..., dict) else {}` makes an
            # empty/non-mapping file clear both blocks instead, matching
            # `load_build_config`'s own empty-BuildConfig outcome exactly.
            if isinstance(sources_loaded, dict):
                _validate_or_exit(sources_loaded, sources_found)
            # Rebase EACH document's own compile.include_dirs against ITS
            # OWN project root BEFORE merging (Codex review, fresh
            # evidence, PR #1222 fourth round): `compile:` is now a real
            # per-field MERGE inside apply_sources_root_config_blocks (see
            # that function's own docstring), not a wholesale block
            # replace -- so a merged include_dirs can hold entries from
            # BOTH documents. The single later rebase_relative_config_paths()
            # call (below, at module scope) anchors against only ONE root,
            # which would silently mis-resolve the OTHER document's own
            # relative entries now that compile: is no longer a
            # whole-document selection. Pre-resolving each document's own
            # entries to absolute paths here, before the merge, makes that
            # later single-anchor call a safe no-op for both (an
            # already-absolute path is left untouched).
            if found_path is not None:
                base = rebase_relative_config_paths(base, found_path=found_path)
            if isinstance(sources_loaded, dict):
                sources_loaded = rebase_relative_config_paths(
                    sources_loaded, found_path=sources_found
                )
            # "sources" (plural -- public_headers/exclude/graph) is a
            # DISTINCT top-level block from "source" (singular). Which
            # blocks get replaced depends on sources_pairwise, computed
            # above. Shared with actions/check-target/action.yml's own
            # "Generate assurance-overlay config" step via
            # abicheck.action_config_overlay.apply_sources_root_config_blocks
            # (Codex review, fresh evidence, PR #1222 third round) so the two
            # call sites' block-selection/empty-document semantics can't
            # independently drift the way the assurance-overlay step's own
            # entirely-missing promotion once did.
            base = apply_sources_root_config_blocks(
                base,
                sources_loaded,
                blocks=_sources_root_blocks,
                merge_compile=sources_merge_compile,
            )
            # found_path is reassigned to sources_found ONLY when compile:
            # was actually sourced from it (the `dump`/`scan` single-
            # document shape) -- it anchors compile.include_dirs resolution
            # below, and neither a pairwise NOR a `mode: compare`
            # single-sided caller's compile: block is ever sourced from the
            # sources root any more (per `_sources_root_blocks` above -- P1
            # fix, PR #1222 eleventh round: `mode: compare` used to
            # reassign `found_path` here too, back when it still merged
            # `compile:` from the sources root), so both must keep
            # resolving against whichever document actually supplied
            # base["compile"] (the checkout-root config, if any). Now that
            # both documents' own include_dirs entries are already
            # absolute (above), this reassignment only matters for the
            # `dump`/`scan` bucket -- the rebase call below is otherwise a
            # no-op regardless of which root it names.
            if not (sources_pairwise or sources_merge_compile):
                found_path = sources_found
        elif sources_found is None:
            # Codex review, P1, fresh evidence, PR #1222 ninth round: no
            # `.abicheck.yml` exists ANYWHERE in the `--sources` tree at
            # all -- distinct from the "a sources-root document exists
            # but is empty/non-mapping" case just above, which already
            # clears these same blocks via apply_sources_root_config_
            # blocks' own empty-document handling. The real pipeline
            # treats "no document" identically to "an empty document" for
            # this purpose: embed_build_source()'s own `cfg_path =
            # build_config or discover_build_config(raw_sources)` resolves
            # to `None` either way, so `cfg` stays `None` and every one of
            # build:/sources:(plural)/compile:/source:(singular)/debug: --
            # whichever this function's own `_sources_root_blocks` says
            # are sources-root-exclusive for this particular command/mode
            # -- falls back to a bare `BuildConfig()`'s pure defaults,
            # NEVER the checkout-root document's own values for those same
            # keys. Leaving `base` untouched here (as a previous round
            # did) let the checkout document's own build:/sources: survive
            # into the generated overlay, which `embed.py` then applies as
            # an explicit --config -- settings the real, non-overlay
            # `--sources <dir>` invocation would never have picked up.
            # Reuse the identical apply_sources_root_config_blocks
            # clearing with `sources_doc=None` (its own docstring: a
            # non-dict `sources_doc` clears every block in `blocks` except
            # a `merge_compile=True` empty-fold, which is a correct no-op
            # onto the checkout's own compile: -- see that function's
            # docstring for why `compile:` is the one exception even
            # here). Mirrors actions/check-target/action.yml's own
            # identical `elif _sources_found is None:` branch so the two
            # shared call sites cannot independently drift on this again.
            base = apply_sources_root_config_blocks(
                base,
                None,
                blocks=_sources_root_blocks,
                merge_compile=sources_merge_compile,
            )

# Discover mode's own base document is untrusted, repository-controlled
# content: strip (or, for the decode-node budget below, cap) each key that
# gates execution or a resource ceiling before merging (see this
# function's own docstring for the trust reasoning). Explicit mode's
# base document is exactly what the operator named via build-config --
# already the trusted case cli_options.py's own `explicit_config` check
# grants, so nothing here is stripped from it.
if merge_mode != "explicit":
    # build.query/compile.compiler stripping and the resource_limits cap are
    # identical in both spirit and implementation to
    # actions/check-target/action.yml's own "Generate assurance-overlay
    # config" step -- both fold a possibly-untrusted, auto-discovered base
    # document into a synthesized, always-explicit overlay, and both must
    # withhold the same executable-authorized status from it. Shared via
    # abicheck.action_config_overlay (see its own module docstring) so the
    # two can't silently drift; build.compile_db stays each caller's own
    # responsibility to resolve (via the shared
    # discovered_compile_db_resolves() -- see its own docstring) since only
    # the caller knows its own effective --sources root.
    base = strip_untrusted_execution_keys(base)
    if isinstance(base.get("build"), dict) and "compile_db" in base["build"]:
        # A different concern from build.query above (that one is a trust/
        # execution gate) -- here the field itself is harmless, but
        # abicheck/buildsource/embed.py's compile_db_explicit is derived
        # from "was *any* --config passed", not from where this one field's
        # value came from. Forwarding a merged overlay as an explicit
        # --config would silently promote a stale/inapplicable discovered
        # build.compile_db to "explicit" status (its miss must surface, no
        # falling through to inference/autodiscovery) purely because an
        # unrelated Action input (dso-only, gcc-path, ...) also happened to
        # trigger this overlay synthesis. This Action has no per-field
        # provenance channel to tell the CLI "this one came from an
        # auto-discovered document, not an operator's own --config", so the
        # only way to avoid the incorrect-hard-failure risk is to strip the
        # field -- UNLESS it demonstrably already resolves to a real file
        # (Codex review, fresh evidence, second round): stripping a
        # perfectly *usable* discovered compile_db is its own real cost
        # (buildsource/inline.py's own fallback chain -- auto-discovered
        # compile_commands.json, then inferred build-system query -- may
        # collect different or no L3-L5 evidence than the config's own
        # setting would have). `build.compile_db` is documented as a glob
        # *relative to the --sources root* (`inline.py`: `sorted(sources.
        # glob(cfg.compile_db))`), so it can only be validated when that
        # root is known -- `add_compile_context_flags`'s own call passes it
        # via ABICHECK_SOURCES_ROOT; add_release_topology_config_flags's
        # call (compare's release fan-out, which never reads build.
        # compile_db at all) leaves it empty, always stripping, matching
        # this field's own irrelevance there.
        _sources_root = os.environ.get("ABICHECK_SOURCES_ROOT", "")
        _compile_db_resolves = discovered_compile_db_resolves(
            base["build"]["compile_db"], _sources_root
        )
        if not _compile_db_resolves:
            stripped_build = dict(base["build"])
            del stripped_build["compile_db"]
            base["build"] = stripped_build
            print(
                "::warning::the discovered .abicheck.yml's build.compile_db was "
                "dropped from this Action's synthesized --config overlay -- "
                "forwarding it here would silently promote it to an explicit, "
                "must-not-be-missing compile-DB path (the field would otherwise "
                "fall back to inference); set build-config explicitly (naming a "
                "config you reviewed) to opt in.",
                file=sys.stderr,
            )
    # compile.compiler stripping and the resource_limits cap are handled by
    # strip_untrusted_execution_keys() above, alongside build.query -- see
    # that call's own comment.

if found_path is not None:
    base = rebase_relative_config_paths(base, found_path=found_path)

for key, value in overlay.items():
    if isinstance(value, dict) and isinstance(base.get(key), dict):
        merged = dict(base[key])
        merged.update(value)
        base[key] = merged
    else:
        base[key] = value

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(base, f)
PYEOF
  )
  local _merge_status=$?
  # No `set -e` in this script (established precedent, e.g.
  # add_flag_shlex_split above): a nonzero exit here must not be silently
  # swallowed, since that would leave the caller writing a possibly-partial
  # or missing overlay file and proceeding as if the merge had succeeded.
  if [[ $_merge_status -ne 0 ]]; then
    if [[ "$merge_mode" == "explicit" ]]; then
      echo "::error::failed to merge this Action's synthesized config overlay with the explicit build-config ${base_source} (see the error above). Refusing to silently proceed."
    else
      echo "::error::failed to merge this Action's synthesized config overlay with the repository's own auto-discovered .abicheck.yml (see the error above). Refusing to silently proceed."
    fi
    # Codex review, fresh evidence: this exit happens after the caller's own
    # mktemp (out_path already exists, possibly partially written by the
    # failed subprocess) and before the main EXIT trap further down installs
    # -- the same early-exit leak window _rm_overlay_on_early_exit exists to
    # close at every other such site in this file.
    _rm_overlay_on_early_exit "$out_path"
    exit 1
  fi
}

_COMPILE_CONTEXT_CONFIG_OVERLAY=""
# Same script-global-for-cleanup shape as _COMPILE_CONTEXT_CONFIG_OVERLAY
# above -- add_release_topology_config_flags's own synthesized overlay
# (Codex review, fresh evidence): a function-local `overlay` was created
# under $RUNNER_TEMP but never reached the main EXIT trap further down,
# leaking one file per run on a persistent self-hosted runner.
_RELEASE_TOPOLOGY_CONFIG_OVERLAY=""

# Codex review, fresh evidence: the main EXIT trap that cleans up both
# overlays above (further down, once STDERR_FILE etc. are also in scope)
# is installed well after either overlay's own mktemp. On the SUCCESS path
# that is harmless -- the script keeps running until it reaches (and is
# replaced by) that later trap, well before the real exit. But a handful of
# `exit 1` calls between an overlay's own mktemp and that later trap
# installation (missing-interpreter, `_mktemp_canonical` failure) terminate
# the whole script from exactly that window, with only the earlier,
# overlay-unaware `rm -rf "$_PY_SAFE_DIR"' EXIT` trap active -- leaking the
# just-created overlay. An earlier revision tried re-arming a broad EXIT
# trap immediately after each mktemp instead of this per-site cleanup; that
# also cleans up on the *success* path's own early exit (this function
# returning to a caller that itself exits right after CMD assembly, without
# ever invoking the real command that reads the file) which is exactly the
# shape every test harness here uses, so it was reverted -- an unconditional
# early trap cannot distinguish "the process is exiting because of a
# failure in this window" from "the process's caller simply hasn't gotten
# around to consuming the file yet". Removing the file explicitly at each
# known early-`exit 1` site (matching Codex's own suggested alternative)
# only fires on the actual failure paths, leaving the success path
# untouched.
_rm_overlay_on_early_exit() {
  rm -f "${1:-}"
}

# Codex review, fresh evidence, PR #1171 (eighth round): whether
# $INPUT_OLD_LIBRARY, as resolved by the time this runs (a direct operand,
# or an --abi-baseline auto-fetch's $BASELINE_FILE already folded into it
# above), is a stored snapshot rather than a live binary -- a stored
# snapshot/dump (whether the caller named one directly, or it's what an
# auto-fetched baseline always resolves to) does no header/debug extraction
# at all, so it has nothing for a --sources tree's own compile:/source:/
# debug: settings to reach.
#
# resolve_input()'s own dispatch order (abicheck/workflows/
# input_resolution.py) checks native-binary magic bytes FIRST, before any
# text/JSON sniffing -- and *every* non-binary shape it goes on to accept
# (a JSON snapshot, with or without leading whitespace before compression
# or the `{`; an ABICC Perl dump; a raw BTF/CTF blob; a symvers file) is
# equally extraction-free for this decision's purposes: none of them are
# parsed via -H/-I/ast-frontend/a compile: block at all. So rather than
# re-deriving each of those formats' own sniffing rules one at a time here
# (this function had two prior, narrower rounds: extension-only, then
# JSON/gzip/zstd-magic-only -- each fixed one Codex-reported gap and left
# the next one for the round after), the two prior rounds are collapsed
# into their actual invariant: NOT a recognized live-binary format IS a
# stored operand, full stop. Native-binary magic bytes only, mirroring
# `_is_release_style_operand`'s own `od -An -tx1` idiom for the same
# reason it uses one: a bash string can't hold an embedded NUL, so the
# magic bytes must be read as hex, not as text.
_old_library_is_stored_snapshot() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  local magic4
  magic4=$(od -An -tx1 -N 4 "$path" 2>/dev/null | tr -d ' \n')
  case "$magic4" in
    7f454c46 | \
    4d5a* | \
    feedface | feedfacf | cefaedfe | cffaedfe | \
    cafebabe | bebafeca | cafebabf | bfbafeca)
      # ELF / PE ("MZ") / Mach-O (32-bit, 64-bit, universal, universal-64,
      # either byte order) -- a live binary regardless of its own filename
      # or of anything the content that follows this magic might resemble.
      return 1
      ;;
  esac
  # Anything else this Action can plausibly receive as old-library (JSON,
  # compressed JSON, an ABICC Perl dump, a raw BTF/CTF blob, symvers, or
  # simply unrecognized content) performs no live header/debug extraction
  # of its own -- stored, from this decision's point of view.
  return 0
}

# Whether the caller's own $MODE resolves a --sources tree's compile:/
# source:(singular)/debug: blocks PAIR-WIDE (echoes "pairwise") or
# SINGLE-SIDED (echoes ""), for
# _merge_config_overlay_with_discovered_project_config's own $6 -- see that
# function's docstring. Only single-pair `compare OLD NEW` with a genuinely
# *live* OLD operand is pairwise: both operands are then independently-
# parsed live headers under the SAME resolved compile context. `dump` has
# one operand, period, live or not; the audit-only shape (`compare
# --no-baseline`, old-library empty) has no OLD side at all; and a
# two-sided `compare` whose OLD operand is a stored snapshot or resolved
# ABI baseline (_old_library_is_stored_snapshot) does no header/debug
# extraction on that side either (Codex review, fresh evidence, PR #1171,
# fifth round: the earlier `$MODE == "compare"` check alone was too coarse
# and silently discarded a live NEW-with-`--sources` operand's own
# compile:/source:/debug: settings whenever OLD was a stored/baseline
# snapshot) -- none of these three shapes has an "other side" a --sources
# tree's own compile:/debug: could leak into, so all three stay
# single-sided.
#
# The release-style directory/package `compare` fan-out never calls
# add_compile_context_flags at all (it rejects every compile-context input
# outright), so it never reaches this helper either.
_compile_context_sources_pairwise() {
  if [[ "$MODE" == "compare" && -n "${INPUT_OLD_LIBRARY:-}" ]] \
     && ! _old_library_is_stored_snapshot "${INPUT_OLD_LIBRARY:-}"; then
    echo "pairwise"
  fi
}

# Whether the caller's own single-sided `compile:` resolution (see
# _compile_context_sources_pairwise's own docstring for what "single-sided"
# covers) is a genuine two-stage MERGE (echoes "true") or a single-document
# EXCLUSIVE selection/REPLACE (echoes "", the default) -- Codex review,
# fresh evidence, PR #1222 fourth round, second finding: within the
# single-sided bucket, `compile:` does NOT always resolve the same way.
# `dump`/`scan --against` (`$MODE != "compare"`) call `merge_compile_config`
# exactly ONCE, where `build_config`/`sources` are mutually exclusive
# alternatives (`cli_options.merge_compile_config`'s own `cfg = build_config
# if explicit_config else discover_build_config(sources)`) -- the
# `--sources` tree's own document supplies `compile:` EXCLUSIVELY when no
# explicit `--build-config` is given, the identical single-document shape
# `build:`/`sources:`/`source:`/`debug:` already use, so this echoes ""
# (REPLACE) for them. `compare`'s TWO-SIDED shape (`$MODE == "compare"` with
# old-library or abi-baseline actually set -- which, per
# _compile_context_sources_pairwise's own docstring, reaches this
# single-sided bucket at all only via a stored-snapshot old operand) is
# different: `cli_compare_helpers.py`'s own `resolve_compile_context(...,
# build_config=cfg_path, ...)` ALWAYS independently resolves the
# checkout-root document's `compile:` block FIRST, unconditionally, and the
# live side's own `--sources` tree only folds ON TOP of that via a SECOND
# `merge_compile_config` call (`compare.py`'s `_maybe_dump_side`) -- a
# genuine two-stage MERGE, so this echoes "true" for it.
#
# P1 fix, PR #1222 eleventh round (Codex review, fresh evidence): this
# flag's OWN "true" value no longer causes `_sources_root_blocks` to
# promote/merge `compile:` from the sources root at the overlay-generation
# layer any more -- `_embed_inline_source_side` (its real name today; the
# `_maybe_dump_side` reference above predates a rename) performs that
# second merge stage unconditionally, regardless of whether `--config` was
# explicit, so this overlay ALSO folding the sources root's `compile:` in
# duplicated it. This function keeps echoing "true" for the identical
# `mode: compare` condition purely because `source:`(singular)/`debug:`
# promotion still depends on it (see the caller's own comment) -- it is no
# longer read for any `compile:`-specific decision.
#
# The audit-only shape (old-library/abi-baseline BOTH omitted, `compare
# --no-baseline`) also reaches the single-sided bucket, but is NOT the
# stored-snapshot-old case above and does NOT go through
# `resolve_compile_context` at all: `compare_no_baseline.py`'s own
# `_resolve_no_baseline_invocation` resolves the checkout-root config only
# for scope/severity/assurance, and the candidate's own compile: comes from
# `workflows.no_baseline_compare.resolve_no_baseline_candidate` ->
# `resolve_side_snapshot` -- the exact same per-side primitive `dump`
# resolves through, with no second, checkout-root-first merge stage at all
# (verified by reading that call chain -- no `compile=` override is ever
# threaded through, so a checkout-root `compile:` block plays no part here).
# So this must NOT echo "true" for the audit-only shape (a real regression
# found via `test_action_compile_context_old_library_liveness.py`'s own
# `TestAuditOnlyCompareStaysSingleSidedUnconditionally`, which this
# unconditional `$MODE == "compare"` check failed): it echoes "true" only
# when compare actually has an old side to two-stage-merge against. See
# `apply_sources_root_config_blocks`'s own `merge_compile` parameter
# docstring for the full account.
_compile_context_sources_merge_compile() {
  if [[ "$MODE" == "compare" \
        && ( -n "${INPUT_OLD_LIBRARY:-}" || -n "${INPUT_ABI_BASELINE:-}" ) ]]; then
    echo "true"
  fi
}

add_compile_context_flags() {
  # $1: "true" to also fold the `lang` input into the synthesized overlay
  # (dump and single-pair compare, including the audit-only shape -- the
  # CLI itself has no literal --lang flag at all any more, Phase 7 -- see
  # _compile_context_sources_pairwise's own docstring above -- all take
  # --lang here now).
  local include_lang="${1:-true}"
  # action.yml maps an omitted `lang` input to INPUT_LANG=c++ -- that is the
  # *default*, not a user override, so it must not by itself count as "lang
  # was explicitly requested" (CodeRabbit review, PR #1146, finding #6): a
  # non-empty INPUT_LANG only counts when it differs from that default.
  # `auto` is the documented no-op spelling of ast-frontend -- it resolves
  # to exactly what leaving the input unset resolves to -- so it is
  # excluded here for the same reason the `c++` lang default is: a run that
  # configures nothing must synthesize nothing. Without this, `ast-frontend:
  # auto` alone produced an empty `{"compile": {}}` overlay and put a
  # `--config` on the command line that was not there before, routing a
  # discovered project `.abicheck.yml` through this function's own
  # merge-and-strip path instead of letting the CLI discover it directly.
  # Harmless-looking and not: `discover` mode strips `compile.compiler`
  # (see `_merge_config_overlay_with_discovered_project_config`'s trust
  # distinction), so a project that set one could silently lose it. The
  # release path made this reachable where it previously was not, but the
  # fix belongs here rather than at that call site -- the single-pair path
  # has the same behavior and `auto` means the same thing on both (Codex
  # review, PR #1233; its own stated mechanism, a `compile.lang: c++` in
  # the overlay, is not what this produced -- the empty overlay and the
  # `--config` it carries are).
  local _ast_frontend_requested="${INPUT_AST_FRONTEND:-}"
  if [[ "$_ast_frontend_requested" == "auto" ]]; then
    _ast_frontend_requested=""
  fi
  if [[ -z "${_ast_frontend_requested}${INPUT_GCC_PATH:-}${INPUT_GCC_PREFIX:-}${INPUT_GCC_OPTIONS:-}${INPUT_SYSROOT:-}" \
        && "${INPUT_NOSTDINC:-false}" != "true" \
        && ( "$include_lang" != "true" || -z "${INPUT_LANG:-}" || "${INPUT_LANG:-}" == "c++" ) ]]; then
    return 0
  fi
  # Codex review, fresh evidence, PR #1154 follow-up ("Reject configs
  # supplied through extra-args"): a project config can also arrive via the
  # documented, supported `extra-args: --config PATH` passthrough. This used
  # to be rejected right here, specific to this function's own inputs -- now
  # superseded by the general `_cmd_has_config_flag && _extra_args_has_
  # config_flag` check just before `extra-args` is appended (see that call
  # site's own docstring near the bottom of this script), which catches the
  # identical collision for every `--config`-synthesizing path (dso-only/
  # fail-on-removed-library/compile-context) in one place instead of one
  # narrower copy per function.
  if [[ -z "$_COMPILE_CONTEXT_CONFIG_OVERLAY" ]]; then
    if [[ -z "$_PY_BIN" ]]; then
      # Same "fail loud rather than silently produce a wrong compile
      # context" precedent as add_flag_shlex_split's and
      # _merge_config_overlay_with_discovered_project_config's own
      # missing-interpreter guards -- and the same isolation requirement as
      # every other inline-Python invocation in this file (Codex review, PR
      # #1159, fourth round: this generator was launching a bare `python3`
      # from the checked-out repository instead of the resolved, isolated
      # `$_PY_BIN`/`$_PY_SAFE_DIR` interpreter every other invocation here
      # uses -- a missing interpreter on Windows runners exposing only
      # `python`, and, more seriously, a code-execution risk on a
      # `pull_request` workflow where a fork-controlled `sitecustomize.py`
      # committed into the checkout could execute during a bare
      # same-directory `python3`'s own interpreter startup, before this
      # script's body runs).
      echo "::error::mode: ${MODE} needs a working Python interpreter on PATH to synthesize the compile: config overlay from this Action's cross-compilation inputs (resolved interpreter: '${_PY_BIN:-<none found on PATH>}')."
      exit 1
    fi
    # The eight raw input values are passed on stdin, NUL-separated, not as
    # env vars (Codex review, fresh evidence, PR #1162: windows-latest CI
    # failure, this function only). A value shaped like a POSIX absolute
    # path (e.g. `gcc-path: /opt/gcc-14/bin/g++`, a real, common
    # cross-compilation input) triggered Git Bash/MSYS's automatic path
    # conversion when forwarded as an env var to the resolved `$_PY_BIN` --
    # silently rewriting it into a Windows path (inserting "Program Files"
    # and its embedded space) before this script ever saw it, e.g.
    # `/opt/gcc-14/bin/g++` -> `C:/Program Files/Git/opt/gcc-14/bin/g++`.
    # Only actual argv/envp entries are subject to that conversion -- stdin
    # content never is (the same fix already applied to
    # add_flag_shlex_split()'s single-value case above) -- so this
    # sidesteps the whole class regardless of which of the eight fields
    # triggers it.
    #
    # The script itself is written to its own $RUNNER_TEMP-anchored file
    # rather than fed to `"$_PY_BIN" -` via a heredoc: a heredoc redirects
    # stdin to the program text for that invocation, which would collide
    # with (and silently discard) the piped NUL-separated data above -- a
    # heredoc attached to a command always wins that command's own stdin
    # redirection, so the data pipe would never reach the program at all
    # (PR #1162's own finding). A real script file, passed as an argv
    # argument instead, leaves stdin free for the data; $RUNNER_TEMP (not a
    # bare `mktemp`) keeps the file itself in a form every native
    # interpreter can open directly, including this one, matching the
    # convention every other mktemp call in this file already follows (see
    # PR_JSON/PR_BODY).
    local _compile_context_helper_py
    _compile_context_helper_py=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-compile-context-helper.XXXXXX")
    cat > "$_compile_context_helper_py" <<'PYEOF'
# Synthesizes a minimal .abicheck.yml `compile:` block (as JSON, a valid
# YAML subset abicheck's own yaml.safe_load parses identically) from this
# Action's cross-compilation inputs -- the config-only replacement for the
# per-run flags Phase 7 removed from compare/dump. Printed to stdout (not
# written directly to the overlay path) so the caller can merge it with the
# repository's own auto-discovered .abicheck.yml before writing the final
# file -- see _merge_config_overlay_with_discovered_project_config's own
# docstring for why an unmerged overlay would silently drop that config.
import json
import shlex
import sys

(
    include_lang,
    lang,
    frontend,
    gcc_path,
    gcc_prefix,
    gcc_options,
    sysroot,
    nostdinc,
) = sys.stdin.buffer.read().split(b"\0")[:8]
include_lang = include_lang.decode("utf-8")
lang = lang.decode("utf-8")
frontend = frontend.decode("utf-8")
gcc_path = gcc_path.decode("utf-8")
gcc_prefix = gcc_prefix.decode("utf-8")
gcc_options = gcc_options.decode("utf-8")
sysroot = sysroot.decode("utf-8")
nostdinc = nostdinc.decode("utf-8")

compile_blk: dict[str, object] = {}
if include_lang == "true":
    if lang:
        compile_blk["lang"] = lang
if frontend and frontend != "auto":
    compile_blk["frontend"] = frontend
# compile.compiler merges the former --compiler/--compiler-prefix pair
# (Phase 7 -- one-comparison-product.md §4.1's "MERGE" disposition for
# --compiler-prefix): a full compiler path is the more specific of the two,
# so it wins on the rare workflow that names both.
compiler = gcc_path or gcc_prefix
if compiler:
    compile_blk["compiler"] = compiler
if gcc_options:
    # CodeRabbit review, PR #1146, finding #7: BuildConfig.from_dict()
    # (abicheck/buildsource/build_config.py) rejects any compile.options
    # list item containing whitespace -- each entry must already be one
    # complete argv atom, the same contract add_flag_shlex_split (above)
    # already honors for a multi-line (YAML block scalar) value: one line is
    # one complete, space-safe token, never shlex-split further. Unconditional
    # `shlex.split()` here previously ignored that line-per-token
    # convention and instead re-tokenized the whole multi-line value on
    # every whitespace character regardless of line breaks -- for an
    # ordinary multi-line value that diverged from those token
    # boundaries, and for a deliberately-spaced line (e.g. one meant as a
    # single, later-rejected whitespace-bearing atom) it silently
    # *accepted* the split instead of surfacing BuildConfig's own
    # whitespace-rejection error, which is the outcome the unsplit-multiline
    # handling above produces for the identical input shape.
    # A single-line value keeps `shlex.split()`: that spelling is the
    # direct config-key replacement for the old scalar `--gcc-options`
    # flag, which was always shell-quoting-aware.
    compile_blk["options"] = (
        [line for line in gcc_options.splitlines() if line]
        if "\n" in gcc_options
        else shlex.split(gcc_options)
    )
if sysroot:
    compile_blk["sysroot"] = sysroot
if nostdinc == "true":
    compile_blk["nostdinc"] = True
json.dump({"compile": compile_blk}, sys.stdout)
PYEOF
    local _compile_overlay_json
    _compile_overlay_json=$(printf '%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' \
      "$include_lang" "${INPUT_LANG:-}" "${INPUT_AST_FRONTEND:-}" "${INPUT_GCC_PATH:-}" \
      "${INPUT_GCC_PREFIX:-}" "${INPUT_GCC_OPTIONS:-}" "${INPUT_SYSROOT:-}" "${INPUT_NOSTDINC:-false}" |
    (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" "$_compile_context_helper_py"))
    # $? (not PIPESTATUS -- this script's own `set -uo pipefail`, already in
    # effect for the whole file, already makes the pipeline's exit status
    # the interpreter's own, so a second mechanism would be redundant) is
    # the helper's exit status, not the printf's (main-branch parallel fix,
    # reconciled here: a failed interpreter invocation would otherwise fall
    # through to the unconditional CMD+=(--config ...) below with an empty
    # $_compile_overlay_json, which the merge step's own `json.loads` would
    # then raise an uncaught JSONDecodeError on rather than failing with a
    # clean ::error:: -- the same "explicit input deserves a loud rejection,
    # not a silent wrong result" precedent this function's own module
    # docstring already sets for the release-operand guard).
    local _compile_context_overlay_rc=$?
    rm -f "$_compile_context_helper_py"
    if [[ "$_compile_context_overlay_rc" -ne 0 || -z "$_compile_overlay_json" ]]; then
      echo "::error::mode: ${MODE} could not synthesize the compile: config block from the ast-frontend/gcc-*/sysroot/nostdinc${include_lang:+/lang} inputs (interpreter exit ${_compile_context_overlay_rc}). Running without it would parse headers under the wrong compile context, so the step fails instead of continuing silently."
      exit 1
    fi
    _COMPILE_CONTEXT_CONFIG_OVERLAY=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-compile-context.XXXXXX")
    if ! _COMPILE_CONTEXT_CONFIG_OVERLAY=$(_mktemp_canonical "$_COMPILE_CONTEXT_CONFIG_OVERLAY"); then
      _rm_overlay_on_early_exit "$_COMPILE_CONTEXT_CONFIG_OVERLAY"
      exit 1
    fi
    if [[ -n "${INPUT_BUILD_CONFIG:-}" ]]; then
      # An explicit build-config input is a deliberate operator action --
      # merge this Action's synthesized compile: overlay into a COPY of
      # that file (Action input wins on a key conflict), fully trusted (no
      # stripping of compile.compiler -- see
      # _merge_config_overlay_with_discovered_project_config's own
      # docstring for the trust distinction), instead of the previous hard
      # rejection of this combination (Codex review, PR #1159, second
      # round: a real regression for any workflow that combined both
      # before Phase 7 demoted these flags to config).
      _merge_config_overlay_with_discovered_project_config \
        "$_compile_overlay_json" "$_COMPILE_CONTEXT_CONFIG_OVERLAY" \
        "${INPUT_BUILD_CONFIG}" "explicit" "${INPUT_SOURCES:-}" \
        "$(_compile_context_sources_pairwise)" \
        "$(_compile_context_sources_merge_compile)"
    else
      _merge_config_overlay_with_discovered_project_config \
        "$_compile_overlay_json" "$_COMPILE_CONTEXT_CONFIG_OVERLAY" "$PWD" \
        "discover" "${INPUT_SOURCES:-}" "$(_compile_context_sources_pairwise)" \
        "$(_compile_context_sources_merge_compile)"
    fi
  fi
  CMD+=(--config "$_COMPILE_CONTEXT_CONFIG_OVERLAY")
}

# Phase 7d (one-comparison-product.md §4.1, ADR-068 D5): `compare`'s
# `--dso-only`/`--include-private-dso`/`--fail-on-removed-library` are gone
# entirely (CONFIG class, no CLI override -- `.abicheck.yml`'s `release:`/
# `gate:` blocks are their only source now). This Action's own
# dso-only/include-private-dso/fail-on-removed-library inputs still exist,
# so forwarding them now means synthesizing a small config overlay the run
# reads via --config, the same "--config is NOT one of the flags the
# release fan-out rejects" precedent add_compile_context_flags already
# established for the compile: block above. Called from the
# release-style-operand branch in `mode: compare`, AFTER both
# `add_compile_context_flags` and that branch's own `add_single_flag
# "--config" "$INPUT_BUILD_CONFIG"` have already run -- so by the time this
# function runs CMD may already carry either a raw, un-merged "--config
# $INPUT_BUILD_CONFIG" pair or this Action's own compile-context overlay
# (the latter reachable on this shape since the compile-context inputs
# stopped being rejected for a directory/package operand). Rather than
# rejecting either combination outright (an earlier
# revision did, as "mutually exclusive" -- a real regression, since a
# workflow could legitimately combine both before Phase 7d demoted these
# flags to config, Codex review, PR #1159, second round), this function
# finds and removes that already-added pair and replaces it with the merged
# overlay: the topology keys folded into a COPY of the user's own explicit
# build-config (Action input wins on a key conflict), through
# `_merge_config_overlay_with_discovered_project_config`'s "explicit" mode
# -- fully trusted, no key stripping, since naming build-config is itself a
# deliberate operator action (see that function's own docstring). Any OTHER
# "--config" already in CMD at this point (one that doesn't match
# INPUT_BUILD_CONFIG, or with no build-config input given at all) is a real
# caller bug, not a user input to accommodate -- that case still fails
# loud rather than silently producing a two---config command line.
add_release_topology_config_flags() {
  # dso-only/include-private-dso/fail-on-removed-library deliberately carry
  # no declared default in action.yml (Codex review, fresh evidence): an
  # explicit `false` from the workflow must be able to override a
  # discovered/explicit .abicheck.yml's own release.dso_only/
  # release.include_private_dso/gate.fail_on_removed_library: true --
  # collapsing "omitted" and "explicit false" onto the same value (the old
  # `${INPUT_DSO_ONLY:-false}` shape, which also made this early return fire
  # whenever all three read as "false") would make that override silently
  # unreachable. So this guard -- and the overlay-generation below -- keys
  # off whether each input was actually GIVEN (non-empty string) at all,
  # never off its truthiness.
  if [[ -z "${INPUT_DSO_ONLY:-}" \
        && -z "${INPUT_INCLUDE_PRIVATE_DSO:-}" \
        && -z "${INPUT_FAIL_ON_REMOVED_LIBRARY:-}" ]]; then
    return 0
  fi
  local _config_idx=-1 _i
  for ((_i = 0; _i < ${#CMD[@]}; _i++)); do
    if [[ "${CMD[_i]}" == "--config" ]]; then
      _config_idx=$_i
      break
    fi
  done
  # The base document this function's own release:/gate: overlay is merged
  # into. Two legitimate predecessors can already sit on the command line
  # by the time this runs, and each is folded onto rather than rejected:
  #
  #   * the raw "--config $INPUT_BUILD_CONFIG" pair the release-style
  #     branch's own `add_single_flag` added, or
  #   * `add_compile_context_flags`'s synthesized overlay -- reachable for a
  #     directory/package operand ever since the compile-context inputs
  #     stopped being rejected for that shape (they are threaded through the
  #     release fan-out; see that branch's own comment). That overlay has
  #     *already* folded in whichever base applied (an explicit
  #     build-config, or the auto-discovered project .abicheck.yml), so it
  #     is merged into as "explicit"/fully trusted and the base is not
  #     re-discovered -- doing so would drop this Action's own compile:
  #     block back out of the final document.
  #
  # Any OTHER "--config" already in CMD is a real caller bug, not a user
  # input to accommodate -- that case still fails loud rather than silently
  # producing a two---config command line.
  local _release_base="" _release_base_mode="discover"
  if [[ -n "${INPUT_BUILD_CONFIG:-}" ]]; then
    _release_base="${INPUT_BUILD_CONFIG}"
    _release_base_mode="explicit"
  else
    _release_base="$PWD"
  fi
  if [[ $_config_idx -ge 0 ]]; then
    local _existing_config="${CMD[$((_config_idx + 1))]:-}"
    # `${...:-}`, not a bare expansion: this function is extracted and run
    # standalone (without the script's own global initialization) by
    # `tests/test_action_release_topology_config.py`'s harness, under `set
    # -u` (Codex-review precedent: every other extracted-region reference
    # in this file is written the same defensive way).
    if [[ -n "${_COMPILE_CONTEXT_CONFIG_OVERLAY:-}" \
          && "$_existing_config" == "${_COMPILE_CONTEXT_CONFIG_OVERLAY:-}" ]]; then
      _release_base="$_COMPILE_CONTEXT_CONFIG_OVERLAY"
      _release_base_mode="explicit"
    elif [[ -n "${INPUT_BUILD_CONFIG:-}" && "$_existing_config" == "${INPUT_BUILD_CONFIG}" ]]; then
      :
    else
      echo "::error::internal: add_release_topology_config_flags called after --config was already added to the command line for a reason other than build-config or this Action's own compile-context overlay -- this is a bug in run.sh, not a user input problem."
      exit 1
    fi
    # Remove the predecessor pair -- it is replaced below by the merged
    # overlay (which folds its content in, so nothing the caller asked for
    # is dropped).
    unset "CMD[$_config_idx]" "CMD[$((_config_idx + 1))]"
    CMD=("${CMD[@]}")
  fi
  if [[ -z "$_PY_BIN" ]]; then
    # Same isolation requirement as every other inline-Python invocation in
    # this file, and the same missing-interpreter fail-loud precedent as
    # add_compile_context_flags's sibling guard above (Codex review, PR
    # #1159, fourth round: this generator was launching a bare `python3`
    # from the checked-out repository instead of the resolved, isolated
    # `$_PY_BIN`/`$_PY_SAFE_DIR` interpreter -- a missing interpreter on
    # Windows runners exposing only `python`, and, more seriously, a
    # code-execution risk on a `pull_request` workflow where a
    # fork-controlled `sitecustomize.py` committed into the checkout could
    # execute during a bare same-directory `python3`'s own interpreter
    # startup, before this script's body runs).
    echo "::error::mode: ${MODE} needs a working Python interpreter on PATH to synthesize the release:/gate: config overlay from this Action's release-topology inputs (resolved interpreter: '${_PY_BIN:-<none found on PATH>}')."
    exit 1
  fi
  local _release_overlay_json
  _release_overlay_json=$(cd "$_PY_SAFE_DIR" \
  && ABICHECK_RELEASE_DSO_ONLY="${INPUT_DSO_ONLY:-}" \
  ABICHECK_RELEASE_INCLUDE_PRIVATE_DSO="${INPUT_INCLUDE_PRIVATE_DSO:-}" \
  ABICHECK_GATE_FAIL_ON_REMOVED_LIBRARY="${INPUT_FAIL_ON_REMOVED_LIBRARY:-}" \
  PYTHONPATH= "$_PY_BIN" - <<'PYEOF'
# Synthesizes a minimal .abicheck.yml `release:`/`gate:` block (as JSON, a
# valid YAML subset abicheck's own yaml.safe_load parses identically) from
# this Action's release-topology inputs -- the config-only replacement for
# the per-run flags Phase 7d removed from compare's directory/package
# fan-out. Printed to stdout (not written directly to the overlay path) so
# the caller can merge it with the repository's own auto-discovered
# .abicheck.yml before writing the final file -- see
# _merge_config_overlay_with_discovered_project_config's own docstring for
# why an unmerged overlay would silently drop that config.
import json
import os
import sys

# Each of these three env vars is "" (not "false") when the corresponding
# Action input was never given at all -- action.yml deliberately declares
# no default for any of them (Codex review, fresh evidence) so that an
# explicit `false` from the workflow can still override a discovered/
# explicit .abicheck.yml's own true value, instead of being silently
# indistinguishable from "not set". Only an explicitly-given value (either
# spelling) is written into the overlay; omitted means "let whatever the
# base config already says stand", not "force false".
doc: dict[str, object] = {}
release_blk: dict[str, object] = {}
_dso_only = os.environ.get("ABICHECK_RELEASE_DSO_ONLY", "")
if _dso_only:
    release_blk["dso_only"] = _dso_only == "true"
_include_private_dso = os.environ.get("ABICHECK_RELEASE_INCLUDE_PRIVATE_DSO", "")
if _include_private_dso:
    release_blk["include_private_dso"] = _include_private_dso == "true"
if release_blk:
    doc["release"] = release_blk
_fail_on_removed = os.environ.get("ABICHECK_GATE_FAIL_ON_REMOVED_LIBRARY", "")
if _fail_on_removed:
    doc["gate"] = {"fail_on_removed_library": _fail_on_removed == "true"}
json.dump(doc, sys.stdout)
PYEOF
  )
  # $RUNNER_TEMP (not a bare `mktemp`), matching the convention every other
  # mktemp call in this file already follows (PR_JSON/PR_BODY, the compile-
  # context overlay above) -- a bare `mktemp` can resolve under Git Bash's
  # own MSYS-internal /tmp mount, a spelling only Git Bash processes
  # reliably translate back to a real filesystem path. Script-global (not
  # `local`), like _COMPILE_CONTEXT_CONFIG_OVERLAY, so the main EXIT trap
  # further down can actually clean it up (Codex review, fresh evidence).
  _RELEASE_TOPOLOGY_CONFIG_OVERLAY=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-release-topology.XXXXXX")
  if ! _RELEASE_TOPOLOGY_CONFIG_OVERLAY=$(_mktemp_canonical "$_RELEASE_TOPOLOGY_CONFIG_OVERLAY"); then
    _rm_overlay_on_early_exit "$_RELEASE_TOPOLOGY_CONFIG_OVERLAY"
    exit 1
  fi
  if [[ "$_release_base_mode" == "explicit" ]]; then
    _merge_config_overlay_with_discovered_project_config \
      "$_release_overlay_json" "$_RELEASE_TOPOLOGY_CONFIG_OVERLAY" "$_release_base" "explicit"
  else
    _merge_config_overlay_with_discovered_project_config \
      "$_release_overlay_json" "$_RELEASE_TOPOLOGY_CONFIG_OVERLAY" "$_release_base"
  fi
  CMD+=(--config "$_RELEASE_TOPOLOGY_CONFIG_OVERLAY")
}

# A directory, a file whose name matches a recognized package extension, or
# an extensionless RPM/Deb detected by magic bytes (mirrors package.py's
# is_package(), including its magic-byte fallback — abicheck/package.py:547-554
# — since classify_compare_operand() delegates to it regardless of filename;
# a name-suffix-only check here would misidentify such an operand, Codex
# review, PR #557). `compare` fans such an operand out through the release
# engine internally regardless of the Action's MODE. Since CLI cleanup phase
# two, PR E, the release engine supports --write directly (json/markdown/
# junit, the same set --format itself accepts there) -- this helper is no
# longer needed to skip the --write PR-comment JSON injection, but stays in
# use for the release-only flags below (--output-dir, --dso-only, ...).
_is_release_style_operand() {
  local path="$1"
  [[ -d "$path" ]] && return 0
  # Portable lowercasing: ${path,,} is bash-4+ only, but this script also
  # supports macOS's stock (GPLv2-frozen) bash 3.2 (see add_flag above).
  local lower
  lower=$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    *.rpm | *.deb | *.tar | *.tar.gz | *.tar.xz | *.tar.bz2 | *.tar.zst | *.tgz | *.conda | *.whl)
      return 0
      ;;
  esac
  [[ -f "$path" ]] || return 1
  # Extensionless RPM (0xedabeedb lead magic) / Deb (ar archive "!<arch>\n")
  # packages — read the first 8 bytes as hex (binary-safe; a bash string
  # would truncate at an embedded NUL) and compare.
  local magic
  magic=$(od -An -tx1 -N 8 "$path" 2>/dev/null | tr -d ' \n')
  case "$magic" in
    edabeedb*) return 0 ;;          # RPM lead magic (first 4 bytes)
    213c617263683e0a) return 0 ;;   # "!<arch>\n" (Deb ar archive, 8 bytes)
  esac
  return 1
}

# ---------------------------------------------------------------------------
# Value-taking CLI options, derived from the INSTALLED abicheck (ADR-070 D3)
# ---------------------------------------------------------------------------
#
# `_extra_args_is_value_option NAME` answers whether NAME consumes a following
# token as its own value (Click `nargs != 0`, not a boolean flag) for the
# command this run actually invokes. The `extra-args` tokenizer needs that to
# tell a real flag from some other option's literal value.
#
# This was two hand-maintained `case` lists (here and in
# `actions/check-target/action.yml`), justified by a comment claiming no live
# `abicheck` was reachable at run time. That claim was false: `action.yml`
# installs abicheck at step 3 and runs this script at step 4, and
# `$_PY_BIN_HAS_ABICHECK` below already proves the import works. The lists
# drifted exactly as an unowned copy does -- twelve option names retired from
# the CLI entirely, four live `dump`/`deps` options missing, and a `-j`
# cluster terminal `compare` has never had -- none of which the guarding test
# could see, because it checked one direction against one command. See
# `docs/contribute/plans/action-cli-surface-drift.md` and ADR-070.
#
# Deriving is not merely tidier than a snapshot, it is *more correct*: the
# answer matches the abicheck version the workflow installed, which may be
# older or newer than this Action's own checkout. No committed artifact can be
# right about that. (`action/validate-inputs.sh` is the one shell here that
# genuinely runs pre-install; a generated artifact is permitted there, and
# only there.)
#
# Scoped to the command `MODE` selects rather than a union over all of them:
# `--compression` is real on `dump` and absent from `compare`, so a union
# would reintroduce the surplus-entry failure mode (a name treated as
# value-taking on a command that has no such option swallows the next real
# flag as its value).
_cli_command_path() {
  case "${MODE:-compare}" in
    dump) printf 'dump' ;;
    deps-tree) printf 'deps tree' ;;
    deps-compare) printf 'deps compare' ;;
    *) printf 'compare' ;;
  esac
}

# Holds the derived set as `|--a|--b|...|` so membership is one pure-bash
# substring test (no associative arrays, which are bash 4+; this file targets
# stock bash 3.2 per the conventions in `action/AGENTS.md`).
_CLI_VALUE_OPTIONS=""
_CLI_VALUE_OPTIONS_READY="false"
# "true" only once a real query succeeded; `false` means UNDETERMINED, which
# `_require_cli_value_options_or_fail` turns into a hard failure when it
# matters. Never conflate it with "this command has no value-taking options".
_CLI_VALUE_OPTIONS_DERIVED="false"

# Populate `$_CLI_VALUE_OPTIONS` once. Called eagerly at top level, after the
# interpreter preflight establishes `$_PY_BIN_HAS_ABICHECK`, so that every
# later `$(_extra_args_options)` *command substitution* inherits the result
# rather than re-deriving it -- a subshell cannot write the cache back to its
# parent. The lazy call from `_extra_args_is_value_option` is the fallback for
# a direct invocation of these helpers (unit tests source only this file's
# function-definition region, which ends before that preflight).
#
# Runs under the same `$_PY_SAFE_DIR`/cleared-`PYTHONPATH` isolation as every
# other `abicheck`-importing call in this file: this imports a real abicheck
# submodule, and doing so from the untrusted checkout would reopen the
# sys.path/sitecustomize code-execution path that isolation exists to close.
_cli_value_options_init() {
  [[ "$_CLI_VALUE_OPTIONS_READY" == "true" ]] && return 0
  _CLI_VALUE_OPTIONS_READY="true"
  if [[ "${_PY_BIN_HAS_ABICHECK:-false}" != "true" || -z "${_PY_SAFE_DIR:-}" || -z "${_PY_BIN:-}" ]]; then
    # Undetermined, NOT "nothing is value-taking" -- see
    # `_require_cli_value_options_or_fail` below for why that distinction is
    # load-bearing and why this cannot be an opaque-token fallback.
    # Deliberately also NOT a baked list, which would reinstate exactly the
    # drift this derivation removes, in the one path production never
    # exercises.
    _CLI_VALUE_OPTIONS_DERIVED="false"
    return 0
  fi
  local _derived _rc=0
  # shellcheck disable=SC2016  # the inline script is deliberately unexpanded.
  _derived=$( (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import sys
import click
from abicheck.cli import main

node = main
for segment in sys.argv[1:]:
    commands = getattr(node, "commands", None)
    if not commands or segment not in commands:
        raise SystemExit("no such command: " + " ".join(sys.argv[1:]))
    node = commands[segment]
for param in node.params:
    if isinstance(param, click.Option) and not param.is_flag and param.nargs != 0:
        for spelling in param.opts:
            print(spelling)
' $(_cli_command_path)) ) || _rc=$?
  if [[ "$_rc" -ne 0 || -z "$_derived" ]]; then
    _CLI_VALUE_OPTIONS_DERIVED="false"
    return 0
  fi
  _CLI_VALUE_OPTIONS="|$(printf '%s' "$_derived" | tr '\n' '|')"
  _CLI_VALUE_OPTIONS_DERIVED="true"
}

# Fail the run when the option table could not be derived AND `extra-args`
# actually needs one (Codex review, PR #1234, P2 -- this replaces an
# opaque-token fallback an earlier revision of ADR-070 D3 wrongly prescribed
# as safe).
#
# **Why an opaque fallback is not safe, concretely.** Treating every token as
# opaque is not merely "under-recognition". `extra-args: --version --dry-run`
# is argv Click accepts by consuming `--dry-run` as `--version`'s own value,
# leaving `dry_run=False` and running a perfectly normal comparison (verified
# directly against the installed CLI). An opaque tokenizer instead reports a
# real `--dry-run` flag -- so `_extra_args_has_dry_run_flag` answers true,
# this script skips its `--write json=`/`-o` injection as it must for a real
# dry run, and the comparison then runs for real while the requested output
# is never written and the report-reading floors go blind. That is a silent
# wrong outcome, not a visible one, and it is an *over*-detection of
# `--dry-run` rather than under-detection of anything.
#
# So the undetermined case fails loudly instead. Scoped to a non-empty
# `extra-args` on purpose: with no `extra-args` there is nothing to tokenize
# and no decision to get wrong, so a runner whose `python3` cannot import
# abicheck keeps working exactly as before for every invocation that does not
# use the escape hatch. That keeps this from being a blanket hard failure on
# the documented self-hosted mismatch `$_PY_BIN_HAS_ABICHECK` already warns
# about (see its own `::warning::`), while still refusing to guess precisely
# when a guess would change what runs.
_require_cli_value_options_or_fail() {
  [[ "${_CLI_VALUE_OPTIONS_DERIVED:-false}" == "true" ]] && return 0
  [[ -z "${INPUT_EXTRA_ARGS:-}" ]] && return 0
  echo "::error::extra-args is set, but this step cannot determine which abicheck CLI options take a value, so it cannot tell a real flag in extra-args from another option's literal value (resolved interpreter: '${_PY_BIN:-<none found on PATH>}'; it must be able to import abicheck). Guessing is not safe: treating every token as opaque would misread 'extra-args: --version --dry-run' -- which the CLI accepts as --version's own value -- as a real --dry-run, then skip this step's output/sidecar injection while a full comparison ran, silently producing no report. Install abicheck into the interpreter that 'command -v python3' resolves on this runner, or remove extra-args." >&2
  exit 1
}

_extra_args_is_value_option() {
  _cli_value_options_init
  [[ -n "$_CLI_VALUE_OPTIONS" ]] || return 1
  [[ "$_CLI_VALUE_OPTIONS" == *"|$1|"* ]]
}

# Expand Click-style clustered short flags (`-vH` for `-v -H`) into their
# constituent single-character options, printed one per line, when *token*
# is exactly such a cluster; returns 1 (no output) otherwise, so the caller
# falls through to treating the token as an ordinary opaque one.
#
# The only short options across every command this Action invokes are one
# boolean flag (`-v`) and three value-taking ones (`-H`/`-I`/`-o`, mirroring
# `_extra_args_is_value_option` above) -- so the only cluster shape that
# needs expanding is zero or more `v`s followed by exactly one of those
# three, with nothing else after it. (`-j` was listed here as a fourth until
# the option-table audit: `compare` has no `-j` at all -- `jobs` was retired
# with ADR-068 D5 -- so expanding a `-vj` cluster invented an option Click
# itself would reject.) A cluster with anything else attached
# after the value char (`-vHfoo`, an *attached* inline value) is left
# unexpanded on purpose: Click parses that form as `-v -Hfoo`, which does
# NOT consume a following token as `-H`'s value at all, so leaving the
# whole thing as one opaque bare token already produces the correct
# outcome here (nothing downstream needs the attached value itself, only
# whether the *next* raw token gets consumed). Any other shape (an unknown
# character, more than one value char) is also left unexpanded, the same
# safe-by-construction direction `_extra_args_is_value_option`'s own
# docstring describes: worst case a token is under-recognized, never
# mis-recognized as consuming something it doesn't.
_extra_args_expand_short_clusters() {
  local _tok="$1" _rest _last _flags _n _k
  case "$_tok" in
    -[a-zA-Z][a-zA-Z]*) ;;
    *) return 1 ;;
  esac
  _rest="${_tok#-}"
  _last="${_rest: -1}"
  # Derived, not listed (ADR-070 D3): a cluster is only worth expanding when
  # its last character is a value-taking short option, which is exactly what
  # `_extra_args_is_value_option` now answers from the installed CLI. The
  # hand-listed form of this set carried `j` long after `compare` lost `-j`
  # with `jobs` (ADR-068 D5), so it expanded `-vj` into an option Click
  # itself rejects.
  _extra_args_is_value_option "-$_last" || return 1
  _flags="${_rest%?}"
  _n=${#_flags}
  for ((_k = 0; _k < _n; _k++)); do
    [[ "${_flags:_k:1}" == "v" ]] || return 1
  done
  for ((_k = 0; _k < _n; _k++)); do
    printf -- '-v\n'
  done
  printf -- '-%s\n' "$_last"
  return 0
}

# The one place `extra-args` is walked with option/value awareness. Every
# other extra-args-inspecting helper in this file builds on this rather than
# re-scanning raw tokens itself -- three independent Codex review rounds
# each found a real flag/value confusion in what used to be separate, ad hoc
# per-flag token scans (`--output --dry-run` misread as an effective dry
# run; `--suppress -o --dry-run` misread as *not* one, since a bare-token
# scan can't tell a coincidental `-o`-shaped *value* from the real `-o`
# flag; `--output --write` misread as requesting a `--write` secondary when
# `--write` here is actually `--output`'s own filename value) -- the root
# cause was the same in all three: no reader knew which preceding token, if
# any, had already consumed the one being inspected as its argument.
#
# Emits one `NAME<TAB>VALUE` line per option occurrence: `VALUE` is empty
# for a flag/unknown token, the joined half of a single-token `--foo=value`
# form, or the following token for a bare two-token `--foo value`/`-f value`
# form when `--foo`/`-f` is a known value-taking option
# (`_extra_args_is_value_option`). A value-taking option with nothing after
# it (malformed CLI usage the real CLI itself rejects) still gets one row,
# empty value, rather than being silently dropped.
#
# Splits the same way the command line itself does, not by matching the raw
# string: `CMD+=($INPUT_EXTRA_ARGS)` is an unquoted expansion, so bash
# word-splits on IFS -- space, tab AND newline -- and an `extra-args: |`
# YAML literal block (or anything with a tab) produces real, separate
# tokens a literal-space substring check would not see (Codex review,
# historical). `set --` reuses that identical splitting, so this can never
# disagree with the real argv about what a token is; it also inherits the
# same pathname expansion, deliberately, for the same reason.
#
# Still not full shell-quoting parsing (an exotically quoted option evades
# this) and not aware of a short option's concatenated-value form (`-jVALUE`
# for `-j VALUE`) -- both pre-existing limits of every extra-args scan in
# this file, carried forward rather than silently narrowed by this
# refactor.
#
# Clustered bare short flags (`-vH` for `-v -H`, Click's own short-option
# clustering) ARE handled, via `_extra_args_expand_short_clusters` below --
# a fourth Codex review round (P1, fresh evidence) found that `-vH --write`
# (a literal header path spelled "--write") left `-vH` an unrecognized
# token and the following literal `--write` misread as a real flag, wrongly
# suppressing this script's own internal JSON sidecar the same unsafe
# direction the three collisions above already existed to close. A cluster
# ending in an *attached* value (`-vHfoo`) is deliberately left as an opaque
# token rather than expanded: nothing in that form consumes the next raw
# token at all, so leaving it unexpanded is already the correct outcome,
# and reaching for its inline value here would just be the concatenated-
# value gap this comment already documents as out of scope.
#
# Cluster expansion happens INLINE in the single stateful loop below, never
# as a separate pre-pass over the raw token list -- a fifth Codex review
# round (P1, fresh evidence) found that a pre-pass expanding every token
# up front cannot tell a real cluster from a *value* token that merely
# looks like one (`-H -vH --write`: `-vH` here is `-H`'s own consumed
# value, a literal string, not a cluster to expand), which corrupted a
# perfectly ordinary value into extra synthetic options and could just as
# easily flip a real `--write` the other way. Expansion is only even
# attempted once `$_pending` is confirmed empty, i.e. the current token is
# not already claimed as a preceding option's value.
_extra_args_options() {
  local _arg _pending="" _cluster _line
  # shellcheck disable=SC2086  # word-splitting is the point; see above.
  set -- ${INPUT_EXTRA_ARGS:-}
  for _arg in "$@"; do
    if [[ -n "$_pending" ]]; then
      printf '%s\t%s\n' "$_pending" "$_arg"
      _pending=""
      continue
    fi
    if [[ "$_arg" == --*=* ]]; then
      printf '%s\t%s\n' "${_arg%%=*}" "${_arg#*=}"
      continue
    fi
    if _extra_args_is_value_option "$_arg"; then
      _pending="$_arg"
      continue
    fi
    if _cluster="$(_extra_args_expand_short_clusters "$_arg")"; then
      while IFS= read -r _line; do
        [[ -z "$_line" ]] && continue
        if _extra_args_is_value_option "$_line"; then
          _pending="$_line"
        else
          printf '%s\t\n' "$_line"
        fi
      done <<<"$_cluster"
      continue
    fi
    printf '%s\t\n' "$_arg"
  done
  [[ -n "$_pending" ]] && printf '%s\t\n' "$_pending"
}

# Whether the user's own `extra-args` passthrough already requests
# `--write` (documented, supported usage — `extra-args` is a general CLI
# escape hatch).
#
# Fed through a `<<<` here-string rather than `< <(...)` process
# substitution (Codex review, P1, fresh evidence): this file's own
# conventions prefer a here-string over process substitution where one
# works, for the stock bash 3.2 contributors and macOS runners carry (this
# and its three siblings below are the only new process-substitution use
# this refactor introduced).
_extra_args_has_write_flag() {
  local _name _value
  while IFS=$'\t' read -r _name _value; do
    [[ "$_name" == "--write" ]] && return 0
  done <<<"$(_extra_args_options)"
  return 1
}

# Would injecting our own internal `--write json=$PR_JSON` conflict with a
# `--write` the user's own `extra-args` already requests? $1 is "release"
# for a directory/package operand, "single" otherwise.
#
# This used to be a blanket "any user --write at all" check, on the theory
# that `--write` was a scalar Click option where a repeated occurrence
# silently drops the earlier one -- but `--write` is documented and
# live-verified as *repeatable* for a single-pair/audit-only operand
# (`compare --help-all`: "Repeatable: pass --write more than once to emit
# several artifacts from the same analysis"; confirmed live: `--write
# markdown=a.md --write json=b.json` produces both files, and even two
# `--write json=...` at different paths both render). So for a single-pair
# or audit-only run, the ONLY real conflict is the user's own `--write`
# already being `json=...` -- appending a second, redundant JSON write
# would work but serves no purpose (`_extra_args_write_json_path` already
# recovers that same path for `_json_report_src` to read). A release
# (directory/package) operand is the one genuine constraint: only one
# `--write` is supported there at all (Codex review, PR #1210, round 9 --
# the blanket check previously left every audit-only scan combining
# `extra-args: --write markdown=...`/`--write sarif=...` with no JSON
# sidecar at all, publishing the generic COMPATIBLE/ERROR-adjacent verdict
# `_resolve_clean_exit_verdict` needs a real report to avoid).
_extra_args_write_would_conflict() {
  if [[ "$1" == "release" ]]; then
    _extra_args_has_write_flag
    return
  fi
  [[ -n "$(_extra_args_write_json_path)" ]]
}

# Same shape again, for a third defect (Codex review, P1, fresh evidence):
# `extra-args --config ci.yml` is a documented, general passthrough escape
# hatch that predates this PR's own Phase 7d work -- but `add_release_
# topology_config_flags`/`add_compile_context_flags` now *also* append
# their own synthesized `--config $overlay` to `$CMD` whenever a dso-only/
# fail-on-removed-library/compile-context input is set, appended earlier
# than `extra-args` (line ~2620 below). Click keeps the *last* repeated
# `--config`, so the user's own passthrough one -- appended after -- would
# silently win over, and drop, every setting this Action just synthesized
# and carefully merged (a removed-library gate can become a false green).
# Before this PR, these inputs were plain independent CLI flags (`--dso-
# only`, etc.) with no `--config` of their own, so the identical `extra-
# args --config` workflow never collided -- a real regression Phase 7d's
# flag-to-config demotion introduced, not a pre-existing edge case.
# Checked once, right before `extra-args` is appended, the same call-site
# shape `_extra_args_has_write_flag`/`_extra_args_has_dry_run_flag` already
# use; fails loud with an actionable message (use `build-config:` instead)
# rather than silently doing the wrong thing -- merging a *third*,
# argv-quoting-fragile config source into the overlay was judged a
# materially larger, riskier change than refusing an ambiguous combination
# that has no established meaning to preserve.
_extra_args_has_config_flag() {
  local _name _value
  while IFS=$'\t' read -r _name _value; do
    [[ "$_name" == "--config" ]] && return 0
  done <<<"$(_extra_args_options)"
  return 1
}

# Same shape as `_extra_args_has_write_flag` above, for the sibling defect
# (Codex review, P2, fresh evidence): `INPUT_DRY_RUN` is a dedicated Action
# input, but an *effective* dry run reached only through `extra-args
# --dry-run` leaves `INPUT_DRY_RUN` false, so the compare command
# assembly still takes its non-dry-run branch and forwards `-o
# "$OUTPUT_FILE"`/injects `--write json=$PR_JSON` -- both of which the CLI
# itself rejects alongside a real `--dry-run` (`dry_run.
# reject_dry_run_with_output`/`frontends.cli.options.secondary_output.
# reject_incoherent_secondary_output`), turning what should be a clean
# dry-run preview into a usage error (exit 64). Checked before the PR_JSON
# sidecar injection in both modes, the same way `_extra_args_has_write_flag`
# already is.
_extra_args_has_dry_run_flag() {
  local _name _value
  while IFS=$'\t' read -r _name _value; do
    [[ "$_name" == "--dry-run" ]] && return 0
  done <<<"$(_extra_args_options)"
  return 1
}

# Per ADR-068's re-scoping (`mode: scan` and its own CLI are both retired
# outright), this Action keeps no compatible CLI-flag surface for
# `extra-args` beyond `compare`'s own real option set: a flag `compare`
# genuinely lacks raises `compare`'s own real, correct Click usage error,
# the same outcome any unsupported `extra-args` flag has always gotten.

# Extract a user-supplied `--write json=PATH`/`--write=json=PATH` path from
# extra-args, printing it (and nothing else) when found. Empty output means
# "no such flag" -- callers treat that as "cannot tell", same as every other
# report-discovery helper here.
#
# Why this exists (Codex review, PR #798): when the primary FORMAT isn't
# json and the user's own extra-args already carries `--write`,
# `_extra_args_has_write_flag` above correctly suppresses the internal
# `PR_JSON` injection (so the two `--write`s don't collide and Click's
# last-flag-wins doesn't silently drop the user's own path) -- but that
# means `_json_report_src` had no JSON source to fall back to at all, so
# `annotate: true` silently emitted nothing even though the user's own
# `--write` destination held a perfectly good report the whole time. This
# recovers that path so `_json_report_src` can read it directly, instead of
# either rejecting the combination outright or (worse) silently doing
# nothing.
#
# `--write` is a scalar (non-`multiple=True`) Click option: a repeated
# `--write` resolves to the *last* occurrence, whatever its format, not the
# first `json=...` one found (Codex review, P2, PR #1071) -- so this keeps
# scanning the whole `extra-args` list and only remembers the most recent
# match, clearing it again if a later `--write` isn't `json=...` (matching
# Click's real resolved value, which could just as well be a non-JSON
# format last).
_extra_args_write_json_path() {
  local _name _value _found=""
  while IFS=$'\t' read -r _name _value; do
    if [[ "$_name" == "--write" ]]; then
      case "$_value" in
        json=*)
          _found="${_value#json=}"
          ;;
        *)
          _found=""
          ;;
      esac
    fi
  done <<<"$(_extra_args_options)"
  [[ -n "$_found" ]] || return 1
  printf '%s' "$_found"
}

# The real `--format` value `abicheck` runs with, accounting for `extra-args`
# overriding this script's own `--format "$FORMAT"` flag.
#
# Each mode's own command-assembly section puts `--format "$FORMAT"` (derived
# from the Action's `format:` input) on `CMD` first and `$INPUT_EXTRA_ARGS`
# last; Click resolves a repeated option by keeping only the *last*
# occurrence. So `format: text` with `extra-args: --format json` really does
# run with JSON output, even though this script's own `$FORMAT` variable
# still reads "text" everywhere else. Every JSON-detection site gating on
# `$FORMAT == json` (`_STDOUT_JSON_FILE`'s own stdout capture,
# `_json_report_src`'s `OUTPUT_FILE` branch) needs this *effective* value,
# not the nominal one, or it silently fails to recognize a report that is
# genuinely JSON on disk/stdout (ADR-064's own "effective-format-override"
# gap, previously left as a documented limitation rather than fixed).
#
# Falls back to the nominal `$FORMAT` when extra-args carries no `--format`
# of its own -- the ordinary, unoverridden case.
_effective_format() {
  local _name _value _found="${FORMAT:-}"
  while IFS=$'\t' read -r _name _value; do
    [[ "$_name" == "--format" ]] && _found="$_value"
  done <<<"$(_extra_args_options)"
  printf '%s' "$_found"
}

# ---------------------------------------------------------------------------
# Build the abicheck command
# ---------------------------------------------------------------------------
CMD=(abicheck)

MODE="${INPUT_MODE:-compare}"

# Resolved once, up front, so every code path below (including the
# baseline-set fallback further down, which needs it before this script's
# own later _report_query definition would otherwise provide it) can use
# it. On a Windows runner, actions/setup-python may expose only
# python.exe/`python` to Git Bash, not `python3` -- an unconditional
# `python3` call in a fallback path that only exercises when a release has
# no single-snapshot asset would otherwise fail with "command not found"
# on exactly the runners this fallback exists to serve (Codex review).
_PY_BIN="$(command -v python3 || command -v python || true)"
# `command -v` can return a path relative to the CURRENT working directory
# when PATH itself contains a relative entry (e.g. a self-hosted runner
# configured with PATH=tools:$PATH) -- a real, if unusual, configuration.
# Every inline Python invocation below runs as `(cd "$_PY_SAFE_DIR" && ...
# "$_PY_BIN" ...)`, so a relative $_PY_BIN would resolve against the new
# CWD after that `cd`, not the directory it was actually found relative to,
# making a genuinely working, abicheck-capable interpreter falsely resolve
# as unusable (Codex review, fresh evidence). Anchored to $PWD here, before
# any `cd` happens, using the same portable absolute-path check
# (`_report_query` below) already uses for the identical reason.
if [[ -n "$_PY_BIN" ]] && ! _is_path_already_qualified "$_PY_BIN"; then
  _PY_BIN="$PWD/$_PY_BIN"
fi

# The JSON-report reader `_report_query` shells out to. A real file in this
# Action's own directory rather than a heredoc, so its ~300 lines of report
# semantics are reachable from pytest/mypy/ruff -- see report_query.py's own
# module docstring for the full rationale, including why it must version with
# the Action rather than with the installed `abicheck` package.
#
# Resolved from ${BASH_SOURCE[0]} (this script's own path), not from a
# $GITHUB_ACTION_PATH-style environment variable: an env-supplied path to an
# executable script is a forgeable channel, the same class this file already
# rejected twice for the evidence-contract marker (see `_EXIT_EVIDENCE_
# CONTRACT_ERROR`'s own comment below). Resolved once here, beside $_PY_BIN
# above, so the tests that extract `_report_query` in isolation can pick this
# line up the same way they already pick up the $_PY_BIN line.
_REPORT_QUERY_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/report_query.py"

# ---------------------------------------------------------------------------
# Security: `python -c`/`python -m` insert this process's current working
# directory ('' in sys.path, i.e. wherever this script's caller checked out
# code for abicheck to analyze -- untrusted on a `pull_request`-triggered
# workflow, since a PR author controls the checked-out tree) as sys.path[0],
# and Python's automatic `site` module processing (which runs during
# interpreter *startup*, before any `-c` script body gets to run a single
# line of its own) auto-imports a discoverable `sitecustomize.py`/
# `usercustomize.py` from anywhere on the resulting `sys.path` -- including
# a `PYTHONPATH` entry resolved relative to that same untrusted checkout.
# Any inline script below that imports a real `abicheck` submodule would,
# without mitigation, prefer a same-named module/package the checked-out
# tree happens to contain (e.g. a malicious PR adding its own
# `abicheck/_compiler_options.py`, or a top-level `sitecustomize.py`) over
# the actual, trusted, pip-installed package -- executing attacker-
# controlled code inside this Action's own process (Codex review, fresh
# evidence, empirically confirmed for both shapes).
#
# Three earlier revisions of this mitigation each tried to *filter*
# sys.path from *inside* the `-c` script body after the fact (strip the
# resolved CWD; strip a resolved PYTHONPATH=. entry; strip any descendant
# path, not just an exact match; pair every call site with `-S` plus a
# manual `site.main()` re-run to also outrun the sitecustomize auto-import
# window) -- each fixed a real, independently-confirmed gap the previous
# one left open, but the cumulative `-S` + manual `site.main()` re-
# processing broke real `abicheck` importability on a `windows-latest` CI
# runner for reasons that could not be fully root-caused remotely (exit
# code 2 from the wrapping bash invocation, no further diagnostic
# available). Rather than a fourth patch on the same fragile foundation,
# this revision removes the foundation's own premise: instead of trying to
# clean up `sys.path` *after* Python has already started resolving it
# (racing `site`'s own automatic sitecustomize import), every inline script
# that imports an `abicheck` module now runs from a freshly created, empty
# temporary directory with `PYTHONPATH` cleared for that one invocation --
# so the untrusted checkout is never on `sys.path` in the first place, at
# any point during interpreter startup or after. `-S` and the whole
# `sys.path`-filtering script are no longer needed: normal, unmodified
# `site` processing runs, and it can only ever find a *real*
# `sitecustomize.py` (if any) from actual site-packages, never one placed
# in the checkout. A script with no `abicheck` import (e.g. the plain
# `json`/`sys` JSON-parsing snippet elsewhere in this file) has nothing to
# shadow and does not need this.
#
# The real, pip-installed `abicheck` package is never located inside the
# analyzed repository's own checkout, so neither the temp-directory CWD nor
# the cleared `PYTHONPATH` can remove it -- even this action's own self-
# referential dogfooding CI installs it via a plain, non-editable
# `pip install <path>` (action.yml's "Install abicheck" step), which
# copies into site-packages, and a `pip install -e .` (editable) install's
# own import-hook mechanism (a `.pth` file registering a `sys.meta_path`
# finder, confirmed directly against a real editable install) is likewise
# unaffected by either change -- neither depends on `sys.path` carrying
# the checkout, or on `PYTHONPATH` being set, to resolve `abicheck`.
#
# Fails the Action outright if a fresh, private directory can't be created
# (Codex review, fresh evidence) -- falling back to a pre-existing shared
# directory (e.g. bare `${TMPDIR:-/tmp}`) would silently reintroduce the
# exact risk this mechanism exists to close: on a constrained or shared
# self-hosted runner, that directory is neither guaranteed empty nor
# private, so a same-named `abicheck` package or `sitecustomize.py`
# planted (or left over) there could shadow the real package again. A
# `mktemp -d` failure is rare enough, and this variable is needed by every
# `--gcc-options`-forwarding and baseline-set-archive-extraction code path
# below, that failing loud here is strictly better than silently degrading
# the one guarantee this whole mechanism provides.
if ! _PY_SAFE_DIR="$(mktemp -d)"; then
  echo "::error::failed to create a private temporary directory (mktemp -d) -- required to safely run abicheck's own inline Python helpers without risking a checked-out repository shadowing the installed package."
  exit 1
fi
# Registered immediately, not left to the main EXIT trap much further down
# this script (Codex review, fresh evidence): an early exit -- argument
# validation, a no-baseline dry-run success, any error path before this
# script reaches that later trap -- would otherwise leave $_PY_SAFE_DIR
# behind uncleaned, accumulating private temporary directories across
# repeated invocations on a persistent self-hosted runner. A later `trap
# ... EXIT` (this script's main one) replaces this handler outright rather
# than chaining it, which is fine here: that later trap already includes
# `${_PY_SAFE_DIR:-}` in its own cleanup, so nothing is lost when it
# installs -- this one only needs to cover the gap before it does.
trap 'rm -rf "$_PY_SAFE_DIR"' EXIT

# On most runners this is exactly the interpreter action.yml's own "Install
# abicheck" step just `pip install`ed into, since `pip` itself resolves
# against the same PATH lookup -- but a self-hosted runner can expose
# `pip`/`abicheck` from one Python environment while `command -v python3`
# above resolves a *different* one (e.g. a system Python ahead of a pyenv
# shim on PATH, Codex review, fresh evidence). Checked once, up front,
# rather than assumed: `add_flag_shlex_split` (below) fails loud instead of
# silently invoking a `$_PY_BIN` that can't import `abicheck` and dropping
# or corrupting every requested `--gcc-options` token. Run through the same
# `$_PY_SAFE_DIR`/cleared-`PYTHONPATH` isolation as every other `abicheck`-
# importing invocation in this file (Codex review, fresh evidence, second
# round): this preflight itself imports `abicheck`, so running it from the
# untrusted checkout with an inherited `PYTHONPATH` before `$_PY_SAFE_DIR`
# existed would have reopened the exact code-execution path the isolation
# elsewhere in this file exists to close -- `$_PY_SAFE_DIR` is therefore
# created (immediately above) before this check ever runs, not after.
_PY_BIN_HAS_ABICHECK="false"
if [[ -n "$_PY_BIN" ]] \
  && (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c "import abicheck") >/dev/null 2>&1; then
  _PY_BIN_HAS_ABICHECK="true"
elif [[ -n "$_PY_BIN" ]]; then
  echo "::warning::resolved Python interpreter '$_PY_BIN' cannot import abicheck (a self-hosted runner may expose a different python3 on PATH than the one abicheck was installed into) -- --gcc-options/--compiler-option requiring quoting/escaping will fail rather than risk a wrong compile context."
fi

# Derive the value-taking CLI options for this run's command ONCE, here, now
# that `$_PY_BIN_HAS_ABICHECK` and `$_PY_SAFE_DIR` both exist and `$MODE` is
# resolved. Every later `$(_extra_args_options)` runs in a command-substitution
# subshell, which inherits this but cannot populate it, so deriving eagerly
# turns what would be one Python call per tokenizer invocation into one per
# run. When it cannot be derived at all, `_require_cli_value_options_or_fail`
# decides whether that is fatal -- it is, exactly when `extra-args` is set.
_cli_value_options_init
_require_cli_value_options_or_fail

# ---------------------------------------------------------------------------
# `against`/`estimate`/`audit`/`mode: scan` retirement (ADR-068's
# Action-input-lifecycle amendment; D8, hard removal, no deprecation
# window). Each existed only to serve the now-removed `mode: scan` --
# `action/validate-inputs.sh` rejects all four before Python setup/toolchain
# install for the composite Action's own callers; this is the identical
# check for anyone invoking run.sh directly (e.g. tests), same rationale as
# every other validate-inputs.sh mirror in this file.
#
# `against`/`estimate`/`audit` are checked BEFORE `mode: scan` itself
# (CodeRabbit review, fresh evidence) -- matching
# `action/validate-inputs.sh`'s own order (its retired-input checks run
# ahead of its `case "$MODE" in scan) ...` arm), so the same combination of
# inputs reports the identical error from either script. An earlier
# revision of this block checked `mode: scan` first, which for e.g.
# `mode: scan` + `against: old.so` reported "mode: scan is no longer
# supported" here while validate-inputs.sh's preflight -- which runs first
# in the real composite Action, so a caller normally never reaches this
# copy at all -- reported "against is no longer supported" for the
# identical inputs.
if [[ -n "${INPUT_AGAINST:-}" ]]; then
  echo "::error::against is no longer supported (it applied only to the now-removed mode: scan). Set old-library (or abi-baseline) to the same value under mode: compare instead."
  exit 1
fi
if [[ "${INPUT_ESTIMATE:-false}" == "true" ]]; then
  echo "::error::estimate is no longer supported (it applied only to the now-removed mode: scan, as a dry-run alias). Set dry-run: 'true' instead, which applies to every mode."
  exit 1
fi
if [[ "${INPUT_AUDIT:-false}" == "true" ]]; then
  echo "::error::audit is no longer supported (it applied only to the now-removed mode: scan, forcing an audit-only run). Under mode: compare, simply omit old-library and abi-baseline to run an audit-only compare --no-baseline; set severity-preset (e.g. 'default') if this job should still gate on a BREAKING/API_BREAK-classified finding the way mode: scan's own audit mode always did."
  exit 1
fi
if [[ "$MODE" == "scan" ]]; then
  if [[ ( -n "${INPUT_AGAINST:-}" || -n "${INPUT_ABI_BASELINE:-}" ) && "${INPUT_AUDIT:-false}" != "true" ]]; then
    echo "::error::mode: scan is no longer supported (ADR-068). Replacement for a baseline scan: mode: compare with old-library set to the same baseline (against/abi-baseline both map onto old-library/abi-baseline unchanged), and new-library unchanged."
  else
    echo "::error::mode: scan is no longer supported (ADR-068). Replacement for an audit-only scan (no baseline, or audit: true): mode: compare with old-library and abi-baseline both omitted -- new-library alone runs an audit-only compare --no-baseline. This candidate-side audit no longer gates a CI job on a BREAKING/API_BREAK-classified finding by default the way mode: scan did -- set severity-preset (e.g. 'default') to restore that gating; without it the step always exits 0/passes regardless of what the audit finds."
  fi
  exit 1
fi
# new-library-set/risk-rules/crosscheck applied only to the now fully-
# removed mode: scan (ADR-068 D8, hard removal) -- with that mode gone,
# none of the three can ever do anything on any mode any more. Rejected
# here, before any mode dispatch, the identical checks
# action/validate-inputs.sh already runs (this is the copy for anyone
# invoking run.sh directly, e.g. tests).
if [[ -n "${INPUT_NEW_LIBRARY_SET:-}" ]]; then
  echo "::error::new-library-set is no longer supported (ADR-068 (b): scan --artifact-set, and mode: scan itself, are both retired). Preserving its per-member manifest/coverage accounting needs ADR-065 S3's package component inventories, which are not implemented. Remove new-library-set; compare each library individually, or wait for ADR-065 S3."
  exit 1
fi
if [[ -n "${INPUT_RISK_RULES:-}" ]]; then
  echo "::error::risk-rules is no longer supported (ADR-068 (b): scan --risk-rules, and mode: scan itself, are both retired). An omitted depth now resolves to the fixed 'headers' rung, the same default compare always used; set depth: source (or build) explicitly to pin the evidence level a risk profile used to escalate to. Remove risk-rules."
  exit 1
fi
if [[ -n "${INPUT_CROSSCHECK:-}" ]]; then
  echo "::error::crosscheck is no longer supported (ADR-068 (b): scan --crosscheck's KEY=error promotion syntax, and mode: scan itself, are both retired -- superseded, not dropped outright: every cross-source check already reaches compare as an ordinary ChangeKind, so --policy/.abicheck.yml's policy.overrides.<CHANGE_KIND>: error already lets you control any one check's severity). Remove crosscheck and use policy.overrides instead."
  exit 1
fi

# `build-target` is retired on every mode now, not just the already fully
# retired `scan`: `scan --build-target` went first (ADR-068 (b), the
# now-removed mode: scan retirement above), and `dump --build-target` --
# which this input mapped to for `mode: dump` -- was retired next, once
# that removal resolved the routing hazard that had deferred it
# (`frontends/cli/options/rulings.py`'s former deferred ruling). Checked
# unconditionally, before any mode dispatch, the identical check
# `action/validate-inputs.sh` already runs (this is the copy for anyone
# invoking run.sh directly, e.g. tests). Put root target(s) in
# `.abicheck.yml`'s `build.targets` instead (Bazel only so far) --
# `mode: dump`'s own `config`/`sources` inputs reach it exactly as they did
# before this input existed.
if [[ -n "${INPUT_BUILD_TARGET:-}" ]]; then
  echo "::error::build-target is retired on every mode (ADR-068 (b) retired it for scan --build-target; dump --build-target, which this input mapped to for mode: dump, was retired next). Put the root target(s) in .abicheck.yml's build.targets instead, and pass the config with mode: dump's build-config: input (or let it auto-discover from sources:)."
  exit 1
fi

# Replaces every literal (non-glob) occurrence of $2 in $1 with $3, via
# prefix/suffix parameter-expansion pattern REMOVAL (`%%`/`#`) plus plain
# string concatenation for the inserted text -- NOT
# `${haystack//$needle/$replacement}`'s replacement-TEXT position, whose
# '&' Bash 5.2 default `patsub_replacement` shopt gives the special
# "insert the matched pattern text" meaning (like sed's `&` backreference,
# reproduced directly against real Bash 5.2.21). A baseline-profile
# containing a literal '&' (e.g. "linux&asan") would otherwise silently
# expand to "...linux{profile}asan..." instead of the literal string on
# Bash 5.2+, while resolving correctly on Bash 3.2 (macOS stock, no such
# interpretation) -- the SAME template/profile pair resolving to two
# DIFFERENT asset names purely depending on which runner published vs.
# consumed it (Codex review). `%%`/`#` pattern-removal carries no such
# special-character semantics in either position.
_substitute_literal() {
  local haystack="$1" needle="$2" replacement="$3" result=""
  while [[ "$haystack" == *"$needle"* ]]; do
    result+="${haystack%%"$needle"*}$replacement"
    haystack="${haystack#*"$needle"}"
  done
  printf '%s' "$result$haystack"
}

# ---------------------------------------------------------------------------
# Baseline auto-fetch: resolve INPUT_ABI_BASELINE → INPUT_OLD_LIBRARY
#
# A fetch failure (missing release/token/asset) reports and continues rather
# than exiting 1 under --dry-run: an unresolved baseline is the one
# deliberate exception action.yml's dry-run description carves out (tolerate
# it rather than hard-fail, since a preview shouldn't require the comparison
# already be resolvable) -- but this block runs before any mode branch ever
# consults INPUT_DRY_RUN, so an unavailable baseline used to hard-fail a
# preview run before it ever got the chance to no-op (Codex review).
# BASELINE_FILE is left unset in that case; the mode branches' existing
# required-input checks still apply if no other old-library/against source
# was given.
# ---------------------------------------------------------------------------
_baseline_unavailable() {
  local message="$1"
  if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    echo "::warning::$message (continuing: --dry-run performs no analysis and never exits nonzero for an unresolved baseline)"
    return 0
  fi
  echo "::error::$message"
  exit 1
}

# ---------------------------------------------------------------------------
# Baseline-set fallback: when no single *.abicheck.json asset was found on
# the release, but baseline-profile was given, try a release-contract
# baseline-set archive instead (abicheck-baseline-<profile>.tar.zst,
# published by publish-baseline.yml -- see docs/reference/publish-baseline.md).
# This is the "single-snapshot and baseline-set protocols" unification: the
# original abi-baseline contract (one *.abicheck.json[.gz|.zst] asset) still
# works completely unchanged and takes priority when present; this fallback
# only ever runs when that search found nothing.
#
# Sets BASELINE_FILE (a script-global, matching the surrounding block's
# style) on success. On any failure, routes through _baseline_unavailable
# (dry-run-tolerant: warns and returns rather than exiting) and leaves
# BASELINE_FILE empty. Reads $ABI_BASELINE/$_GH_REPO_FLAG from the enclosing
# scope, same as the single-snapshot search above it.
# ---------------------------------------------------------------------------
_try_baseline_set_fallback() {
  local baseline_target="${INPUT_BASELINE_TARGET:-}"
  if [[ -z "$baseline_target" ]]; then
    _baseline_unavailable "baseline-profile is set (${INPUT_BASELINE_PROFILE}) but baseline-target is not -- both are required to resolve one target's snapshot from a release-contract baseline-set archive."
    return 1
  fi
  if [[ -z "$_PY_BIN" ]]; then
    _baseline_unavailable "neither 'python3' nor 'python' is available on PATH -- cannot extract or resolve a release-contract baseline-set archive."
    return 1
  fi
  # NOTE: the default is intentionally NOT embedded as
  # "${INPUT_BASELINE_ASSET_NAME_TEMPLATE:-abicheck-baseline-{profile}.tar.zst}"
  # -- bash's ${VAR:-default} parses the default text looking for its own
  # closing '}', and a literal, unescaped '}' inside that text (from
  # "{profile}") terminates the expansion early, silently mangling the
  # result to "abicheck-baseline-{profile.tar.zst}" (reproduced directly;
  # caught by this function's own tests). Computing the default separately
  # avoids the parse ambiguity entirely.
  local asset_template="${INPUT_BASELINE_ASSET_NAME_TEMPLATE:-}"
  if [[ -z "$asset_template" ]]; then
    asset_template='abicheck-baseline-{profile}.tar.zst'
  fi
  local asset_name
  asset_name="$(_substitute_literal "$asset_template" '{profile}' "$INPUT_BASELINE_PROFILE")"
  asset_name="$(_substitute_literal "$asset_name" '{generation}' "${INPUT_BASELINE_GENERATION:-}")"

  # NOT `gh release download --pattern`: that flag is a glob (Go's
  # filepath.Match), not a literal-filename lookup -- a custom
  # baseline-asset-name-template containing a glob metacharacter ('*',
  # '?', '[', ']') would otherwise silently fail to match its own asset,
  # or (worse) match an unrelated one. An earlier fix backslash-escaped
  # those characters before use as --pattern, but that is *itself* wrong
  # on a Windows runner: Go's path/filepath.Match disables escaping on
  # Windows entirely (backslash is the OS path separator there instead),
  # so the escaped pattern would fail to match on exactly the runners the
  # earlier fix's own sibling ($_PY_BIN resolution, above) exists to
  # support (Codex review). Exact-name lookup through the release API
  # sidesteps glob semantics -- and therefore this whole platform split --
  # entirely: list the release's real assets and match $asset_name as a
  # literal string, the same technique publish-baseline.yml's own "Upload
  # release asset" step already uses for the identical "does this exact
  # asset already exist" question. Python, not jq, for the JSON parse --
  # this composite Action installs no jq (self-hosted runners need not
  # have it either; see $_PY_BIN's own "Python, not jq" precedent further
  # down this file).
  echo "::group::Fetch release-contract baseline-set '$asset_name'"
  local set_download_dir="$BASELINE_DIR/baseline-set-download"
  mkdir -p "$set_download_dir"
  local assets_json=""
  if [[ "$ABI_BASELINE" == "latest-release" ]]; then
    assets_json=$(gh release view ${_GH_REPO_FLAG[@]+"${_GH_REPO_FLAG[@]}"} --json assets 2>/dev/null) || assets_json=""
  else
    assets_json=$(gh release view "$ABI_BASELINE" ${_GH_REPO_FLAG[@]+"${_GH_REPO_FLAG[@]}"} --json assets 2>/dev/null) || assets_json=""
  fi
  local existing_url=""
  if [[ -n "$assets_json" ]]; then
    # Isolated the same way as every abicheck-importing invocation, even
    # though this one only uses stdlib `json`/`sys` (Codex review, fresh
    # evidence): the sitecustomize.py auto-import vector this mechanism
    # guards against fires during interpreter *startup*, before a single
    # line of this script body runs -- it doesn't depend on what (if
    # anything) the body itself imports, so "no abicheck import" was never
    # a reason to skip the isolation.
    existing_url=$(printf '%s' "$assets_json" | (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import json
import sys

name = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for asset in data.get("assets") or []:
    if asset.get("name") == name:
        print(asset.get("apiUrl") or "")
        break
' "$asset_name"))
  fi

  # A fixed, platform-safe local filename, NOT "$set_download_dir/$asset_name"
  # -- $asset_name is only guaranteed to be a legal filename on whatever
  # platform PUBLISHED it. A documented literal metacharacter in a custom
  # baseline-asset-name-template (e.g. '?', which the exact-name lookup
  # above deliberately no longer forbids -- see this function's own comment
  # on why it stopped rejecting glob metacharacters) is legal on a Linux
  # publisher's filesystem but reserved on NTFS, so a Windows consumer's
  # `>` redirection into a same-named local file would fail outright even
  # though the exact-name lookup itself succeeded (Codex review). The
  # archive's real encoding is still selected from $asset_name's own
  # suffix (the case dispatch below), never from this local filename.
  local archive_path="$set_download_dir/downloaded-baseline-set"
  if [[ -n "$existing_url" ]]; then
    # .apiUrl (the authenticated REST API asset endpoint), not .url (the
    # unauthenticated browser-download URL) -- mirrors publish-baseline.yml's
    # own identical download step and its own reasoning for why (a private
    # caller repository's browser-download URL doesn't reliably work through
    # `gh api`).
    gh api "$existing_url" -H 'Accept: application/octet-stream' > "$archive_path" 2>/dev/null \
      || rm -f "$archive_path"
  fi
  echo "::endgroup::"

  if [[ ! -f "$archive_path" ]]; then
    _baseline_unavailable "No *.abicheck.json baseline asset, and no baseline-set archive '$asset_name' either, found in the release. Publish a single *.abicheck.json[.gz|.zst] snapshot asset (abi-baseline's original single-library contract), or a release-contract baseline-set archive whose name matches baseline-asset-name-template ('$asset_template')."
    return 1
  fi

  local extracted_dir="$BASELINE_DIR/baseline-set-extracted"
  mkdir -p "$extracted_dir"
  # Delegates to abicheck.package.TarExtractor's own safe extraction (member
  # validation: rejects path traversal, symlink escapes, device/FIFO
  # entries) -- the same extractor actions/resolve-baseline/run.sh uses for
  # an identically-shaped baseline-set archive, rather than reimplementing
  # extraction safety here.
  case "$asset_name" in
    *.tar.zst)
      (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import sys
from pathlib import Path
from abicheck.package import TarExtractor

TarExtractor._safe_extract_zst_tar(Path(sys.argv[1]), Path(sys.argv[2]))
' "$archive_path" "$extracted_dir") \
        || { _baseline_unavailable "failed to extract baseline-set archive '$asset_name' (.tar.zst) -- it is truncated or corrupted, or this runner has neither a 'zstd' command-line tool nor the Python 'zstandard' package available."; return 1; }
      ;;
    *.tar.gz | *.tgz | *.tar)
      (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import sys
from pathlib import Path
from abicheck.package import TarExtractor

TarExtractor._safe_extract(Path(sys.argv[1]), Path(sys.argv[2]))
' "$archive_path" "$extracted_dir") \
        || { _baseline_unavailable "failed to extract baseline-set archive '$asset_name' -- it is truncated or corrupted, or contains a disallowed member (path traversal, a symlink escaping the extraction root, or a device/FIFO entry)."; return 1; }
      ;;
    *)
      _baseline_unavailable "baseline-set archive '$asset_name' is not a recognized archive format (.tar.zst/.tar.gz/.tgz/.tar)."
      return 1
      ;;
  esac

  # Reject any symlink the archive planted -- TarExtractor's own member
  # validation only rejects a symlink escaping the extraction root, not one
  # that stays inside it, but actions/resolve-baseline/run.sh (the canonical
  # baseline-set consumer) rejects ANY symlink at all, since a baseline-set
  # has no legitimate reason to contain one. Without this, the same archive
  # could be silently accepted here (root Action fallback) while
  # check-target/resolve-baseline would reject it as ambiguous -- two
  # consumers of the identical unified baseline-set protocol disagreeing on
  # whether the same archive is usable (Codex review). Command substitution,
  # not piped into `grep -q`, for the same SIGPIPE/pipefail-misreport reason
  # documented at resolve-baseline/run.sh's own identical check.
  _symlinks=$(find "$extracted_dir" -type l)
  if [[ -n "$_symlinks" ]]; then
    _baseline_unavailable "baseline-set archive '$asset_name' contains a symlink, which is not supported -- baseline-set archives must contain only plain files/directories."
    return 1
  fi

  # An archive may contain one nested directory (the profile-named dir it
  # was built from) rather than manifest.json at its root -- mirrors
  # actions/resolve-baseline/run.sh's identical single-subdirectory descent.
  local manifest_root="$extracted_dir"
  if [[ ! -f "$manifest_root/manifest.json" ]]; then
    local subdirs=()
    while IFS= read -r -d '' d; do subdirs+=("$d"); done \
      < <(find "$extracted_dir" -mindepth 1 -maxdepth 1 -type d -print0)
    if [[ ${#subdirs[@]} -eq 1 && -f "${subdirs[0]}/manifest.json" ]]; then
      manifest_root="${subdirs[0]}"
    fi
  fi

  local resolve_output
  resolve_output=$(cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import sys
from abicheck.buildsource.baseline_set import resolve_target

# argv[4] (INPUT_BASELINE_GENERATION) is already validated as empty-or-
# entirely-digits by the caller before this fallback is ever reached, so
# int() here cannot raise -- expected_baseline_generation=None (the
# resolve_target default) when unset, meaning this consumer has no
# generation expectation, same as expected_project_ref="" above it.
expected_generation = int(sys.argv[4]) if sys.argv[4] else None
result = resolve_target(
    sys.argv[1],
    target=sys.argv[2],
    profile=sys.argv[3],
    required=True,
    expected_baseline_generation=expected_generation,
)
snapshot_path = result.snapshot_path
if not snapshot_path:
    snapshot_path = ""
print("outcome=" + result.outcome)
print("message=" + result.message)
print("snapshot_path=" + snapshot_path)
' "$manifest_root" "$baseline_target" "$INPUT_BASELINE_PROFILE" "${INPUT_BASELINE_GENERATION:-}")
  local resolve_outcome resolve_message resolve_snapshot
  resolve_outcome=$(printf '%s\n' "$resolve_output" | sed -n 's/^outcome=//p')
  resolve_message=$(printf '%s\n' "$resolve_output" | sed -n 's/^message=//p')
  resolve_snapshot=$(printf '%s\n' "$resolve_output" | sed -n 's/^snapshot_path=//p')

  if [[ "$resolve_outcome" == "resolved" && -n "$resolve_snapshot" ]]; then
    BASELINE_FILE="$resolve_snapshot"
    echo "Resolved target '$baseline_target' at profile '$INPUT_BASELINE_PROFILE' from baseline-set archive '$asset_name'."
    return 0
  fi
  _baseline_unavailable "could not resolve target '$baseline_target' at profile '$INPUT_BASELINE_PROFILE' from baseline-set archive '$asset_name' (outcome: $resolve_outcome): $resolve_message"
  return 1
}

# Fail-fast pairing check for baseline-profile/baseline-target/abi-baseline,
# re-checked here for anyone invoking run.sh directly (e.g. tests) without
# validate-inputs.sh's own copy of this exact check -- AGENTS.md's "keep
# validate-inputs.sh and run.sh in sync" convention. Unpaired without this:
# baseline-target set but baseline-profile is not (_try_baseline_set_fallback
# below only checks this from INSIDE its own body, reached only via the
# BASELINE_FILES elif below, which keys off baseline-profile alone -- a
# baseline-target set alone never even calls that function to hit its own
# check); or baseline-profile/baseline-target set but abi-baseline is not
# (the whole auto-fetch block below, and therefore
# _try_baseline_set_fallback, only ever runs inside the `-n "$ABI_BASELINE"`
# gate immediately following this check -- without abi-baseline, a fetch is
# never even attempted). Either shape silently discards baseline-target
# instead of erroring, letting a separately-supplied old-library/against run
# in its place (Codex review).
ABI_BASELINE="${INPUT_ABI_BASELINE:-}"
if [[ -n "${INPUT_BASELINE_PROFILE:-}" && -z "${INPUT_BASELINE_TARGET:-}" ]]; then
  echo "::error::baseline-profile is set ('${INPUT_BASELINE_PROFILE}') but baseline-target is not -- both are required to resolve one target's snapshot from a release-contract baseline-set archive."
  exit 1
fi
if [[ -n "${INPUT_BASELINE_TARGET:-}" && -z "${INPUT_BASELINE_PROFILE:-}" ]]; then
  echo "::error::baseline-target is set ('${INPUT_BASELINE_TARGET}') but baseline-profile is not -- both are required to resolve one target's snapshot from a release-contract baseline-set archive."
  exit 1
fi
if [[ ( -n "${INPUT_BASELINE_PROFILE:-}" || -n "${INPUT_BASELINE_TARGET:-}" ) && -z "$ABI_BASELINE" ]]; then
  echo "::error::baseline-profile/baseline-target are set but abi-baseline is not -- the release-contract baseline-set fallback is only reached while resolving abi-baseline (a release tag or 'latest-release'), so without it these inputs can never trigger a fetch."
  exit 1
fi
# Same "entirely digits, or empty" check actions/baseline/run.sh applies to
# its own baseline-generation input -- [0-9]* alone is not anchored to
# "only digits" as a bash case glob (it matches any string that merely
# STARTS with a digit, e.g. "3x"), and resolve_target()'s own
# expected_baseline_generation now raises ValueError for anything that
# isn't a genuine non-negative int, which would otherwise surface as an
# uncaught Python traceback from _try_baseline_set_fallback's inline
# script instead of this Action's own typed error message.
case "${INPUT_BASELINE_GENERATION:-}" in
  '') ;;
  *[!0-9]*)
    echo "::error::baseline-generation '${INPUT_BASELINE_GENERATION}' is not a non-negative integer."
    exit 1
    ;;
  [0-9]*) ;;
esac

if [[ -n "$ABI_BASELINE" && "$MODE" == "compare" ]]; then
  BASELINE_DIR=$(mktemp -d)
  # Canonicalized to an absolute path immediately (Codex review, fresh
  # evidence): `mktemp -d` returns a path relative to `$TMPDIR` when that
  # variable itself holds a relative value (a real, if unusual, self-hosted
  # runner configuration -- confirmed directly: `TMPDIR=relbase mktemp -d`
  # really does emit a relative path). Every path derived from
  # `$BASELINE_DIR` by string concatenation below (`set_download_dir`,
  # `extracted_dir`, `archive_path`, `manifest_root`) is passed as a
  # positional argument into a `(cd "$_PY_SAFE_DIR" && ...)`-wrapped Python
  # invocation elsewhere in this function -- a relative path there resolves
  # against the *new* CWD instead of the original one, making an otherwise
  # valid baseline-set archive read as corrupt or missing. Resolving once
  # here, at the source, fixes every path derived from it without touching
  # each call site individually.
  if ! BASELINE_DIR=$(cd "$BASELINE_DIR" && pwd); then
    echo "::error::failed to canonicalize the baseline working directory '$BASELINE_DIR' -- refusing to continue with an unresolved path."
    exit 1
  fi
  # Clean up temp dir on exit (combined with STDERR_FILE cleanup later)
  _BASELINE_CLEANUP="$BASELINE_DIR"
  BASELINE_FILE=""
  if [[ -f "$ABI_BASELINE" ]]; then
    # Direct file path — use it as-is (any name, e.g. abi-baseline.json), no
    # download and no *.abicheck.json pattern match (which would reject a
    # normal .json name; the input doc promises a path is used directly).
    BASELINE_FILE="$ABI_BASELINE"
  else
    # gh release download relies on local git repo context (README: "the
    # latest release in the project") when no -R/--repo is given -- a job
    # that never ran actions/checkout (e.g. comparing downloaded release
    # artifacts only) has none, so the documented auto-fetch would fail
    # before it even reaches a missing-asset error. Pass -R whenever we
    # know the repo, same rationale as _gh_pr_comment_fallback above
    # (Codex review).
    _GH_REPO_FLAG=()
    [[ -n "${GITHUB_REPOSITORY:-}" ]] && _GH_REPO_FLAG=(-R "$GITHUB_REPOSITORY")
    # ADR-059 (Codex review): a release baseline may be stored under any of
    # the three canonical snapshot suffixes (dump --compression writes
    # .abicheck.json.gz/.abicheck.json.zst, not just plain .abicheck.json)
    # -- gh's --pattern flag is a repeatable stringArray, so pass one per
    # suffix rather than only matching the uncompressed form.
    _ABI_JSON_PATTERNS=(
      --pattern '*.abicheck.json'
      --pattern '*.abicheck.json.gz'
      --pattern '*.abicheck.json.zst'
    )
    if [[ "$ABI_BASELINE" == "latest-release" ]]; then
      echo "::group::Fetch ABI baseline from latest release"
      # ${arr[@]+"${arr[@]}"}, not a bare "${arr[@]}": under macOS's stock
      # (GPLv2-frozen) bash 3.2's set -u, expanding an *empty* array as
      # "${arr[@]}" is itself treated as an unbound-variable reference (bash
      # 4.4+ special-cased this away) -- the same portability trap
      # add_flag()'s callers already guard against elsewhere in this file
      # (Codex review).
      if ! gh release download ${_GH_REPO_FLAG[@]+"${_GH_REPO_FLAG[@]}"} "${_ABI_JSON_PATTERNS[@]}" -D "$BASELINE_DIR"; then
        # Don't fail immediately when baseline-profile is set -- this release
        # may instead publish a release-contract baseline-set archive
        # (abicheck-baseline-<profile>.tar.zst) rather than a single
        # *.abicheck.json asset; fall through to the BASELINE_FILES check
        # below, which finds nothing here and routes into
        # _try_baseline_set_fallback instead of erroring on this search
        # alone.
        if [[ -z "${INPUT_BASELINE_PROFILE:-}" ]]; then
          _baseline_unavailable "No ABI baseline found in latest release. Run 'abicheck dump path/to/libfoo.so -o libfoo.abicheck.json' in your release workflow and upload the resulting *.abicheck.json (or compressed .abicheck.json.gz/.abicheck.json.zst) file as a release asset."
        fi
      fi
      echo "::endgroup::"
    else
      # Treat as a tag name
      echo "::group::Fetch ABI baseline from release $ABI_BASELINE"
      if ! gh release download "$ABI_BASELINE" ${_GH_REPO_FLAG[@]+"${_GH_REPO_FLAG[@]}"} "${_ABI_JSON_PATTERNS[@]}" -D "$BASELINE_DIR"; then
        # See the latest-release branch's identical comment above.
        if [[ -z "${INPUT_BASELINE_PROFILE:-}" ]]; then
          _baseline_unavailable "No ABI baseline found in release '$ABI_BASELINE'. Ensure the release has a *.abicheck.json (or compressed .abicheck.json.gz/.abicheck.json.zst) asset."
        fi
      fi
      echo "::endgroup::"
    fi
    # Require exactly one *.abicheck.json[.gz|.zst] in the download dir:
    # `head -1` picking an arbitrary match on a multi-asset release could
    # silently compare against the wrong library and produce an invalid
    # verdict (Codex review). Built via a while/read loop (not `mapfile`, a
    # bash 4+ builtin) for macOS's stock (GPLv2-frozen) bash 3.2, same
    # portability constraint as add_flag() above. An empty result also
    # covers the download itself failing and _baseline_unavailable
    # returning instead of exiting (e.g. under --dry-run).
    BASELINE_FILES=()
    while IFS= read -r _found; do
      [[ -n "$_found" ]] && BASELINE_FILES+=("$_found")
    done <<< "$(find "$BASELINE_DIR" \( -name '*.abicheck.json' -o -name '*.abicheck.json.gz' -o -name '*.abicheck.json.zst' \) 2>/dev/null)"
    if [[ ${#BASELINE_FILES[@]} -eq 1 ]]; then
      BASELINE_FILE="${BASELINE_FILES[0]}"
    elif [[ ${#BASELINE_FILES[@]} -eq 0 && -n "${INPUT_BASELINE_PROFILE:-}" ]]; then
      # No single-snapshot asset -- try a release-contract baseline-set
      # archive instead (unifies the two release-baseline protocols; see
      # _try_baseline_set_fallback's own comment above).
      _try_baseline_set_fallback || true
    elif [[ ${#BASELINE_FILES[@]} -eq 0 ]]; then
      _baseline_unavailable "No *.abicheck.json (or compressed .abicheck.json.gz/.abicheck.json.zst) file found after download. If this release instead publishes a release-contract baseline-set archive (abicheck-baseline-<profile>.tar.zst), set baseline-profile and baseline-target to fetch from it."
    else
      _baseline_unavailable "Multiple *.abicheck.json assets found (${BASELINE_FILES[*]}); ambiguous which is the baseline. Publish exactly one *.abicheck.json asset per release, or pass abi-baseline a direct file path instead."
    fi
  fi
  if [[ -n "$BASELINE_FILE" ]]; then
    echo "Using ABI baseline: $BASELINE_FILE"
    INPUT_OLD_LIBRARY="$BASELINE_FILE"
  elif [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    # The fetch was tolerated above (dry-run never hard-fails on an
    # unresolved baseline), but if no other old-library was independently
    # given there is nothing left to preview -- report and stop here rather
    # than silently falling through to the audit-only (--no-baseline) shape,
    # which a caller who genuinely wanted a baseline compare did not ask for.
    if [[ -z "${INPUT_OLD_LIBRARY:-}" ]]; then
      echo "::notice::--dry-run: no ABI baseline could be resolved and no other old-library was given, so there is nothing to preview."
      exit 0
    fi
  fi
fi

if [[ "$MODE" == "dump" ]]; then
  # ── Dump mode ───────────────────────────────────────────────────────────
  CMD+=(dump)
  # The library is an optional positional: a source-only dump
  # (`abicheck dump --sources ./src -o out.json`) needs no binary. Require
  # either a binary OR a source/build-evidence input.
  if [[ -n "${INPUT_NEW_LIBRARY:-}" ]]; then
    # dump has no per-library fan-out (unlike compare) — a directory/package
    # is normally caught early by action/validate-inputs.sh, before any
    # dependency install; re-checked here for anyone invoking run.sh
    # directly (e.g. tests) without that step.
    if _is_release_style_operand "${INPUT_NEW_LIBRARY}"; then
      echo "::error::mode: dump does not accept a directory or package for new-library ('${INPUT_NEW_LIBRARY}') — dump snapshots exactly one library. Dump each library individually, or use mode: compare with a directory/package operand instead."
      exit 1
    fi
    CMD+=("${INPUT_NEW_LIBRARY}")
  elif [[ -z "${INPUT_SOURCES:-}${INPUT_BUILD_INFO:-}${INPUT_COMPILE_DB:-}" ]]; then
    echo "::error::dump mode requires new-library, or one of sources/build-info/compile-db for a source-only dump."
    exit 1
  fi

  add_flag "-H" "${INPUT_HEADER:-}"
  add_flag "-H" "${INPUT_NEW_HEADER:-}"
  # `public-header-dir` has no dedicated dump flag any more -- `dump` derives
  # declaration provenance from -H/--header itself (a directory entry tags
  # everything under it public), so forward it as one more -H root.
  add_flag "-H" "${INPUT_PUBLIC_HEADER_DIR:-}"
  add_flag "-I" "${INPUT_INCLUDE:-}"
  add_flag "-I" "${INPUT_NEW_INCLUDE:-}"
  add_single_flag "--version" "${INPUT_NEW_VERSION:-}"
  add_compile_context_flags true

  if [[ "${INPUT_FOLLOW_DEPS:-false}" == "true" ]]; then
    CMD+=(--follow-deps)
    add_flag "--search-path" "${INPUT_SEARCH_PATH:-}"
    add_single_flag "--ld-library-path" "${INPUT_LD_LIBRARY_PATH:-}"
  fi

  # Build-source evidence (L3/L4/L5) embedded inline in the snapshot. A snapshot
  # dumped with --sources/--build-info carries its build/source findings into any
  # later `compare` (including one run from this Action). `compile-db` has no
  # dedicated dump flag — fold it into --build-info, which accepts a
  # compile_commands.json. (See action input `build-info`.)
  add_single_flag "--sources" "${INPUT_SOURCES:-}"
  add_single_flag "--build-info" "${INPUT_BUILD_INFO:-${INPUT_COMPILE_DB:-}}"
  # Skipped when add_compile_context_flags above already merged build-config
  # into a synthesized compile: overlay and added --config itself (the
  # explicit-build-config-plus-compile-context case) -- adding it again here
  # would emit a conflicting second --config for the CLI to reject.
  if ! _cmd_has_config_flag; then
    add_single_flag "--config" "${INPUT_BUILD_CONFIG:-}"
  fi
  # `--build-target` is retired (see the unconditional `INPUT_BUILD_TARGET`
  # check above, which exits before this command-assembly code ever runs
  # with a non-empty value) -- `.abicheck.yml`'s `build.targets` is the
  # only route left.
  _resolve_depth_lc "${INPUT_DEPTH:-}"
  add_single_flag "--depth" "$_DEPTH_LC"
  # `allow-build-query` (the `--allow-build-query` dump flag it fed) is a
  # deprecated no-op removed outright in CLI cleanup H1 -- the input stays
  # registered in action.yml for back-compat, but nothing is forwarded to
  # the CLI for it any more.

  # dry-run performs no analysis and writes nothing, so it is mutually
  # exclusive with -o/--output on the CLI -- skip the output file entirely
  # when set, rather than passing both and letting the CLI reject it.
  if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    CMD+=(--dry-run)
  else
    # Output file — required for dump in action context (otherwise stdout)
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-abicheck-baseline.json}"
    CMD+=(-o "$OUTPUT_FILE")
    add_single_flag "--compression" "${INPUT_SNAPSHOT_COMPRESSION:-}"
  fi

elif [[ "$MODE" == "compare" ]]; then
  # ── Compare mode ─────────────────────────────────────────────────────────
  # old-library/new-library may be single binaries/snapshots, or directories/
  # packages — the `compare` CLI command fans out to a per-library comparison
  # automatically in the latter case (ADR-037 D7), so this one branch covers
  # both; the package-specific options below are simply ignored (with a
  # stderr warning from the CLI) when the operands are a single pair.
  #
  # Audit-only shape (ADR-068's Action-input-lifecycle amendment; the
  # replacement for legacy `mode: scan` with no baseline, or `audit: true`):
  # old-library and abi-baseline both omitted routes to `compare --no-baseline
  # new-library` -- a one-sided audit against the candidate's own public
  # surface, reporting no old/new compatibility verdict at all. Validated
  # upstream (`action/validate-inputs.sh`) and re-checked here (the
  # identical check, for anyone invoking run.sh directly, e.g. tests, same
  # as every other validate-inputs.sh mirror in this file): new-library is
  # required in this shape, and since/changed-path/budget are rejected as
  # usage errors (compare --no-baseline doesn't implement revision-range
  # evidence scoping or the wall-clock guard, ADR-068 D2).
  _NO_BASELINE=false
  if [[ -z "${INPUT_OLD_LIBRARY:-}" && -z "${INPUT_ABI_BASELINE:-}" ]]; then
    _NO_BASELINE=true
  fi
  if [[ "$_NO_BASELINE" == "true" ]]; then
    if [[ -n "${INPUT_SINCE:-}" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support since -- compare --no-baseline does not implement revision-range evidence scoping (ADR-068 D2 rejects --since as a usage error with no baseline). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports since, or drop since for this audit-only run."
      exit 1
    fi
    if [[ -n "${INPUT_CHANGED_PATH:-}" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support changed-path -- compare --no-baseline does not implement revision-range evidence scoping (ADR-068 D2 rejects --changed-path as a usage error with no baseline). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports changed-path, or drop changed-path for this audit-only run."
      exit 1
    fi
    if [[ -n "${INPUT_BUDGET:-}" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support budget -- compare --no-baseline's wall-clock guard is not wired to this path yet (ADR-068 D2 rejects --budget as a usage error with no baseline). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports budget, or drop budget for this audit-only run."
      exit 1
    fi
    if [[ "${INPUT_FOLLOW_DEPS:-false}" == "true" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support follow-deps -- compare --no-baseline's DT_NEEDED dependency walk is not wired to this path yet (rejected outright by the CLI, abicheck/frontends/cli/commands/no_baseline_rulings.py). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports follow-deps, or drop follow-deps for this audit-only run."
      exit 1
    fi
    if [[ -n "${INPUT_USED_BY:-}" || -n "${INPUT_USED_BY_MANIFEST:-}" \
          || -n "${INPUT_REQUIRED_SYMBOL:-}" || -n "${INPUT_REQUIRED_SYMBOLS:-}" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support used-by/used-by-manifest/required-symbol/required-symbols -- these scope a two-sided comparison to what a real consumer uses, and compare --no-baseline has no old/new pair to scope (rejected outright by the CLI, abicheck/frontends/cli/commands/no_baseline_rulings.py). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports consumer scoping, or drop these inputs for this audit-only run."
      exit 1
    fi
    if [[ -n "${INPUT_OLD_HEADER:-}" || -n "${INPUT_OLD_INCLUDE:-}" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support old-header/old-include -- there is no OLD side for this evidence to describe, and the CLI rejects an explicitly OLD-scoped --header/--include outright rather than silently dropping it (abicheck/frontends/cli/commands/no_baseline_rulings.py's _reject_old_sided_inputs). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports old-header/old-include, or drop these inputs for this audit-only run."
      exit 1
    fi
    # old-version's Action-level default is the literal placeholder 'old'
    # (action.yml), always present even when the caller never set it --
    # inert here, not a usage error, same as validate-inputs.sh's mirror
    # check (Codex review, PR #1223, round 11).
    if [[ -n "${INPUT_OLD_VERSION:-}" && "${INPUT_OLD_VERSION}" != "old" ]]; then
      echo "::error::mode: compare without a baseline (old-library/abi-baseline both omitted) does not support old-version -- there is no OLD side to label, and the CLI rejects an explicitly OLD-scoped --version outright rather than silently dropping it (abicheck/frontends/cli/commands/no_baseline_rulings.py's _reject_old_sided_inputs). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports old-version, or drop old-version for this audit-only run."
      exit 1
    fi
  fi
  CMD+=(compare)
  if [[ "$_NO_BASELINE" == "true" ]]; then
    if _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
      echo "::error::mode: compare's audit-only shape (old-library/abi-baseline both omitted) does not accept a directory or package for new-library ('${INPUT_NEW_LIBRARY:-}') — an audit-only run analyses exactly one artifact, it has no per-library fan-out. Point new-library at a single library, or set old-library (or abi-baseline) to run a directory/package comparison instead."
      exit 1
    fi
    CMD+=(--no-baseline "${INPUT_NEW_LIBRARY:?new-library is required for an audit-only compare (old-library/abi-baseline both omitted)}")
  else
    CMD+=("${INPUT_OLD_LIBRARY}")
    CMD+=("${INPUT_NEW_LIBRARY:?new-library is required}")
  fi

  # -H/-I: no OLD side at all in the audit-only shape -- old-header/
  # old-include would be a CLI usage error ("the OLD side is declared
  # absent, so there is no baseline for this evidence to describe") and are
  # rejected upfront above, before reaching this point, rather than
  # silently dropped here. public-header-dir has no dedicated `compare`
  # flag (unlike legacy `scan`) -- it folds into -H, the same directory
  # provenance-and-extraction-scope role a bare -H root already plays.
  # old-version is rejected upfront above too, same as old-header/
  # old-include -- only new-version is forwarded on this shape.
  if [[ "$_NO_BASELINE" == "true" ]]; then
    add_flag "-H" "${INPUT_HEADER:-}"
    add_flag "-H" "${INPUT_NEW_HEADER:-}"
    add_flag "-H" "${INPUT_PUBLIC_HEADER_DIR:-}"
    add_flag "-I" "${INPUT_INCLUDE:-}"
    add_flag "-I" "${INPUT_NEW_INCLUDE:-}"
    add_sided_scalar_flag "--version" "new" "${INPUT_NEW_VERSION:-}"
  else
    add_flag "-H" "${INPUT_HEADER:-}"
    add_sided_flag "--header" "old" "${INPUT_OLD_HEADER:-}"
    add_sided_flag "--header" "new" "${INPUT_NEW_HEADER:-}"
    add_flag "-I" "${INPUT_INCLUDE:-}"
    add_sided_flag "--include" "old" "${INPUT_OLD_INCLUDE:-}"
    add_sided_flag "--include" "new" "${INPUT_NEW_INCLUDE:-}"
    add_sided_scalar_flag "--version" "old" "${INPUT_OLD_VERSION:-}"
    add_sided_scalar_flag "--version" "new" "${INPUT_NEW_VERSION:-}"
  fi
  # The L2 compile-context inputs (lang/ast-frontend/gcc-*/sysroot/nostdinc)
  # reach the CLI as a synthesized `--config` `compile:` overlay (Phase 7
  # removed the individual --ast-frontend/--compiler/... flags from
  # `compare` entirely), and the per-library release fan-out *does* thread
  # that both-sides compile context to every pair's header dump --
  # `cli_resolve.resolve_directory_compile_context` runs the identical
  # `resolve_compile_context` call the single-pair path uses, folding the
  # project `.abicheck.yml` `compile:` block the same way. So a
  # directory/package operand takes the same treatment as a single pair
  # here; the guard that used to reject these inputs for that shape was an
  # un-updated restatement of a CLI restriction that had already been
  # lifted, which made a release comparison through this Action strictly
  # less capable than the same comparison run through the CLI directly.
  #
  # The one compile-context input the CLI still refuses for this shape is a
  # *sided* `--ast-frontend old=/new=` override
  # (`cli_resolve._reject_compile_context_for_set_inputs`): "parse the old
  # library's headers with a different frontend than the new one" has no
  # per-library-pair-within-a-release meaning. This Action exposes no sided
  # spelling of any of these inputs at all -- `ast-frontend` is a single
  # scalar folded into one both-sides `compile:` block -- so there is
  # nothing here that could be silently dropped by that rejection; a sided
  # override typed into `extra-args` reaches the CLI verbatim and fails
  # there, loudly, as its own UsageError.
  add_compile_context_flags true

  # Build/source evidence (--depth build/source) — new (candidate) side only.
  # The old side's evidence, if any, already lives in whatever
  # old-library/abi-baseline snapshot was resolved (e.g. a baseline archive
  # built with its own embedded build_source) — this Action has no live old-
  # side source tree to point --sources/--build-info at in compare mode, so
  # these inputs scope to `new=` unconditionally rather than exposing a
  # second old-sources/old-build-info input pair. Previously silently
  # dropped in compare mode (only dump forwarded them) — a real gap
  # flagged by review: a --depth build/source compare request had no way to
  # actually reach the CLI's evidence flags at all.
  #
  # --sources/--build-info/--depth are skipped entirely when either operand
  # is a directory/package: the CLI's per-library release fan-out (ADR-037
  # D7) doesn't collect inline build/source evidence and rejects those three
  # outright for that shape (_reject_evidence_flags_for_set_inputs) —
  # passing them here would turn every directory/package (e.g.
  # check-target's kind: bundle) comparison into a hard usage error instead
  # of the intended comparison (Codex review).
  #
  # --config is NOT one of the flags that rejection covers (cli_resolve.py's
  # set-input evidence-flags allowlist lists only depth/sources/build_info)
  # — the release fan-out still consumes the project
  # .abicheck.yml for severity/scope/suppression/exit-code settings
  # (_resolve_compare_config runs before the directory/package dispatch), so
  # it stays unconditional; an earlier fix lumped it in with the three
  # rejected flags and silently dropped a bundle caller's build-config
  # (Codex review, second round). Skipped only when add_compile_context_flags
  # above already merged build-config into a synthesized compile: overlay and
  # added --config itself (the explicit-build-config-plus-compile-context
  # case) -- which, now that the compile-context inputs are forwarded for a
  # directory/package operand too, is reachable on *both* operand shapes,
  # not just the single-pair one. `_cmd_has_config_flag` is what keeps that
  # from double-adding --config, and the caller's build-config is not lost
  # to the skip: the overlay add_compile_context_flags wrote is a merge of
  # this Action's compile: block into a COPY of that very file (Action
  # input wins on a key conflict). The release-topology overlay below folds
  # onto whichever of the two this leaves on the command line -- see
  # add_release_topology_config_flags.
  if ! _cmd_has_config_flag; then
    add_single_flag "--config" "${INPUT_BUILD_CONFIG:-}"
  fi
  # CLI cleanup phase two, PR J: --bundle-system-providers/--bundle-cohort
  # removed from the CLI (and this Action input retired with them) -- the
  # cross-library bundle-analysis layer's system-provider allow-list
  # extension and cohort declarations are sourced only from
  # build-config's own .abicheck.yml `bundle:` block now, which --config
  # above already forwards unconditionally.
  # Validated and lowercased once, for both operand shapes: the rung a
  # workflow pins means the same thing either side of this branch, and an
  # unknown value must reach neither `CMD` nor the job log on either. Not
  # `local` -- this runs in the top-level script body, not a function.
  _resolve_depth_lc "${INPUT_DEPTH:-}"
  _depth_lc="$_DEPTH_LC"
  if _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
     || _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
    # Inline build/source evidence is the one thing genuinely unservable
    # here: `cli_resolve._reject_evidence_flags_for_set_inputs` rejects
    # --sources/--build-info/--dump-manifest for a set input, because the
    # per-library fan-out collects none of it, so forwarding them would be
    # a hard usage error and dropping them would run the comparison without
    # the requested evidence -- missing a source-only break while still
    # reporting a clean result. Fail loud instead (Codex review).
    #
    # `--depth` is deliberately NOT part of this guard, on any rung. It
    # left `cli_resolve.py`'s set-input evidence-flag table when the
    # fan-out moved onto `service.run_compare` (naming that constant in
    # full here would trip `test_every_run_sh_input_var_is_set_by_action_
    # yml`, which scans this file for `INPUT_*` reads and cannot tell a
    # prose mention from a real variable): a member may itself be a pre-dumped snapshot
    # carrying embedded L3/L4/L5 evidence (`dump --sources`/`--build-info`),
    # which satisfies `build`/`source` with no inline collection at all, so
    # rejecting those rungs here denied a genuinely reachable configuration
    # -- and reachability is a property of the members, not of the operand's
    # cardinality. A member that cannot reach the pinned rung says so as
    # that member's own error (ADR-064's exit-7 axis), rather than being
    # pre-judged for its neighbours. Keeping a rung rejection here was the
    # same defect this whole change removes, one guard further down the
    # file (Codex review, PR #1233).
    if [[ -n "${INPUT_SOURCES:-}" || -n "${INPUT_BUILD_INFO:-}" || -n "${INPUT_COMPILE_DB:-}" ]]; then
      echo "::error::mode: compare with a directory/package operand (a release/bundle comparison) does not support inline --sources/--build-info/--compile-db evidence -- the CLI's per-library release fan-out never collects it, so the requested evidence would silently never be gathered and a source-only break could be missed. Compare the libraries individually (mode: compare with single-file operands) to collect inline evidence, or pre-dump each member with 'dump --sources/--build-info' so its snapshot already carries it (a directory of such snapshots satisfies --depth build/source with no inline collection at all)."
      exit 1
    fi
    # Every rung of the public ladder (binary, headers, build, source) is
    # forwarded verbatim, exactly as the single-pair branch below forwards
    # it: `cli_compare_options._resolve_depth_for_set_inputs` rejects no
    # rung -- it returns the requested rung for the fan-out to forward, with
    # the shortfall answered per member downstream
    # (`policy/depth_evidence_contract.py`, ADR-064's exit-7 axis) and the
    # ceiling applied by `policy.depth_projection.project_pair_to_depth`.
    # That axis is narrower than an earlier revision of this comment
    # claimed (Codex review): it fires only for a pinned `build`/`source`
    # rung, and only for a *live* side this run extracts. A member that is
    # already a serialized snapshot was never extracted by this run, so
    # there is no shortfall to report for it -- the same pre-dumped-member
    # case that makes those rungs reachable here at all.
    add_single_flag "--depth" "$_depth_lc"
  else
    add_sided_flag "--sources" "new" "${INPUT_SOURCES:-}"
    add_sided_flag "--build-info" "new" "${INPUT_BUILD_INFO:-${INPUT_COMPILE_DB:-}}"
    add_single_flag "--depth" "$_depth_lc"
    # --since/--changed-path (ADR-068 Phase 2c) were previously silently
    # dropped in compare mode -- only the scan branch forwarded them,
    # despite this page's own docs already claiming `mode: compare` "takes
    # the identical depth/since/changed-path/sources/build-info inputs
    # mode: scan does" (Codex review, fresh evidence: `--since`'s
    # scope-narrowing value was silently ignored and a pinned `--depth
    # source` replayed the whole target instead of the PR's changed files,
    # exactly the unrelated-findings/expensive-CI-run risk the docs were
    # written to avoid). Single-pair-only, matching --sources/--build-info/
    # --depth build/source above: the release fan-out doesn't collect
    # build/source evidence for a directory/package operand at all, so
    # there is nothing for --since/--changed-path to scope there either.
    # Rejected upstream for the audit-only shape (validate-inputs.sh and
    # its direct-invocation mirror above), so this is only ever reached
    # with a baseline present.
    if [[ "$_NO_BASELINE" != "true" ]]; then
      add_single_flag "--since" "${INPUT_SINCE:-}"
      add_flag "--changed-path" "${INPUT_CHANGED_PATH:-}"
    fi
  fi

  # Format — for SARIF, always write to a file so upload-sarif can find it.
  # sarif/html are rejected by the CLI itself (a clear UsageError, exit 64)
  # when the operands are directories/packages — surfaced as VERDICT=ERROR
  # below via the generic CLI-error detection, no separate fallback needed.
  FORMAT="${INPUT_FORMAT:-markdown}"
  CMD+=(--format "$FORMAT")

  # Computed here, not only after extra-args are appended to CMD below, so
  # the PR_JSON sidecar-injection decision a few lines down (which runs
  # before that later, general-purpose computation) can already see an
  # `extra-args --format` override -- see `_effective_format`'s own
  # docstring (Codex review, PR #998, fresh evidence: the general-purpose
  # computation runs too late for this mode's own injection decision).
  _EFFECTIVE_FORMAT="$(_effective_format)"

  # dry-run performs no analysis and writes nothing, so it is mutually
  # exclusive with -o/--output AND --write on
  # the CLI -- skip both entirely when set, rather than passing them and
  # letting the CLI reject the combination.
  DRY_RUN="${INPUT_DRY_RUN:-false}"
  if [[ "$DRY_RUN" == "true" ]]; then
    CMD+=(--dry-run)
  elif _extra_args_has_dry_run_flag; then
    # An *effective* dry run reached only through `extra-args --dry-run`
    # (Codex review, P2, fresh evidence): the dedicated `--dry-run` token is
    # already in `extra-args` and gets appended later, so nothing more is
    # added here -- `-o`/`--write` are just as mutually exclusive with a
    # passthrough `--dry-run` as with the dedicated input, and the PR_JSON
    # sidecar-injection guard alone (added first) wasn't the whole branch:
    # `-o "$OUTPUT_FILE"` above it had the identical gap.
    :
  else
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-}"
    # Gated on the effective format, not the nominal one (Codex review, PR
    # #998, fresh evidence): `format: sarif` overridden by `extra-args
    # --format json` (or any other non-sarif format) really does write
    # non-SARIF content, and naming that file `abicheck-results.sarif` by
    # default -- the exact path a workflow's own upload-sarif step (gated
    # on the Action's nominal `format: sarif` input, which this shell
    # variable cannot change) looks for -- would have silently fed
    # mismatched content to CodeQL. Leaving `OUTPUT_FILE` unset here when
    # the effective format isn't sarif means the upload step instead finds
    # no file at all, a loud failure rather than a silent one.
    if [[ "${_EFFECTIVE_FORMAT:-$FORMAT}" == "sarif" && -z "$OUTPUT_FILE" ]]; then
      OUTPUT_FILE="abicheck-results.sarif"
    fi
    if [[ -n "$OUTPUT_FILE" ]]; then
      CMD+=(-o "$OUTPUT_FILE")
    fi

    # Render a second, always-unfiltered JSON report from this same run for
    # the sticky PR comment (--write), instead of re-invoking
    # abicheck a second time just to get JSON. Only needed when the primary
    # format isn't already JSON — a json primary is reused as-is (see
    # _can_reuse_primary_json below).
    #
    # CLI cleanup phase two, PR E: the per-library release fan-out
    # (directory/package operands) now supports --write directly --
    # json/markdown/junit only, the same set --format itself accepts there,
    # which is exactly what this injection ever requests. The release
    # engine renders the JSON from the same already-computed per-library
    # results its primary (markdown, by default) render uses, without
    # re-running any library's comparison, matching how --write already
    # worked for a single-pair operand. Unlike single-pair, though, "only
    # one --write is supported there" (compare --help-all) -- so whether
    # injecting our own would conflict with the user's own extra-args
    # --write genuinely depends on which operand shape this is; see
    # _extra_args_write_would_conflict's own docstring.
    #
    # Gated on `$_EFFECTIVE_FORMAT`, not the nominal `$FORMAT`: a `format:
    # json` step whose own extra-args overrides to a non-json format really
    # does run without JSON output, and skipping this injection because the
    # *nominal* format looked already-JSON left such a run with no JSON
    # report anywhere (Codex review, PR #998, fresh evidence).
    #
    # An effective dry run via `extra-args --dry-run` never reaches this
    # branch at all -- the `elif` above it returns before `OUTPUT_FILE`/`-o`/
    # this injection are considered, so there is no separate dry-run check
    # needed here.
    _write_kind="single"
    if _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
       || _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
      _write_kind="release"
    fi
    if [[ "${_EFFECTIVE_FORMAT:-${FORMAT:-}}" != "json" ]] \
       && ! _extra_args_write_would_conflict "$_write_kind"; then
      PR_JSON=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-pr-json.XXXXXX")
      CMD+=(--write "json=$PR_JSON")
    fi
  fi

  # `--policy` takes both operands now: a built-in profile name, or a policy
  # document (a path, or a packaged built-in like `security`). A policy-file
  # input therefore *is* the policy for this run and outranks the profile,
  # exactly as the removed `--policy-file` flag did.
  add_single_flag "--policy" "${INPUT_POLICY_FILE:-${INPUT_POLICY:-}}"
  add_single_flag "--suppress" "${INPUT_SUPPRESS:-}"

  # Severity configuration. Audit-only: --severity-preset is ALSO the
  # ADR-068 2026-09-10 amendment's audit-gate opt-in (`compare --no-baseline`
  # only gates on a candidate-side finding when a preset other than
  # `info-only` is in effect). Unlike an earlier draft of this migration,
  # this step never injects a preset on the caller's behalf: the ADR-068
  # 2026-09-11 amendment states the audit-gate opt-in is the *caller's*
  # migration step, not something `run.sh` does implicitly (a job that
  # relied on legacy `mode: scan`'s own default gating must itself pass
  # `severity-preset: default` when migrating -- see the amendment's
  # "audit-gate trap" section). So this is the exact same pass-through as
  # the baseline shape below: forward whatever preset the caller asked for
  # (the dedicated input, `extra-args --severity-preset ...`, or neither),
  # and change nothing else.
  add_single_flag "--severity-preset" "${INPUT_SEVERITY_PRESET:-}"
  # --budget: time guard (e.g. 15m, 900s, 1h); the step FAILS on overflow
  # (exit 5) -- a budget never silently shrinks scope. Baseline shape only --
  # rejected upstream for the audit-only shape (compare --no-baseline's
  # wall-clock guard isn't wired to that path, ADR-068 D2).
  if [[ "$_NO_BASELINE" != "true" ]]; then
    add_single_flag "--budget" "${INPUT_BUDGET:-}"
  fi

  # require-complete-analysis: RETIRED (rulings.py deferred-option followup
  # -- hard removal, no deprecation window). The CLI's own
  # --require-complete-analysis flag is gone entirely (config-only now,
  # .abicheck.yml's assurance.require_complete); validate-inputs.sh already
  # rejects a non-empty/false input before this step ever runs, so this is
  # defense in depth for anyone invoking run.sh directly (same rationale as
  # every other pre-validated guard in this file).
  if [[ "${INPUT_REQUIRE_COMPLETE_ANALYSIS:-false}" != "false" ]]; then
    echo "::error::require-complete-analysis ('${INPUT_REQUIRE_COMPLETE_ANALYSIS}') was removed and is no longer forwarded — set assurance.require_complete: true in your .abicheck.yml and pass that file as build-config instead, then remove this input."
    exit 1
  fi

  if [[ "${INPUT_FOLLOW_DEPS:-false}" == "true" ]]; then
    CMD+=(--follow-deps)
    add_flag "--search-path" "${INPUT_SEARCH_PATH:-}"
    add_single_flag "--ld-library-path" "${INPUT_LD_LIBRARY_PATH:-}"
  fi

  # Scoped comparison (ADR-043): --used-by/--required-symbol contracts.
  # The CLI itself enforces --used-by vs --required-symbol mutual
  # exclusivity (a UsageError, surfaced as VERDICT=ERROR below via the
  # generic CLI-error detection) -- not re-validated here.
  add_flag "--used-by" "${INPUT_USED_BY:-}"
  add_flag "--used-by-manifest" "${INPUT_USED_BY_MANIFEST:-}"
  add_flag "--required-symbol" "${INPUT_REQUIRED_SYMBOL:-}"
  # ADR-068 D5 / plan Phase 7h: the CLI's own --required-symbols FILE flag
  # is gone -- --required-symbol now accepts '@FILE' as one of its
  # repeatable values. The Action's required-symbols input is unchanged;
  # only its translation to the CLI moves.
  if [[ -n "${INPUT_REQUIRED_SYMBOLS:-}" ]]; then
    CMD+=(--required-symbol "@${INPUT_REQUIRED_SYMBOLS}")
  fi

  # Package-specific options — only meaningful (and only forwarded) when
  # old-library/new-library are directories or packages; gated here rather
  # than left to the CLI's own single-file warning.
  if _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
     || _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
    add_sided_flag "--debug-info" "old" "${INPUT_DEBUG_INFO1:-}"
    add_sided_flag "--debug-info" "new" "${INPUT_DEBUG_INFO2:-}"
    add_sided_flag "--devel-pkg" "old" "${INPUT_DEVEL_PKG1:-}"
    add_sided_flag "--devel-pkg" "new" "${INPUT_DEVEL_PKG2:-}"

    # Phase 7d: --dso-only/--include-private-dso/--fail-on-removed-library
    # are gone from the CLI -- synthesized into a --config overlay instead
    # (see add_release_topology_config_flags's own docstring for why this
    # can't just call add_single_flag). --keep-extracted is gone outright
    # (one-comparison-product.md §4.1, ADR-068 D5) -- extraction cleanup is
    # now unconditional, so there is no Action input for it any more.
    add_release_topology_config_flags
  fi

elif [[ "$MODE" == "deps-tree" ]]; then
  # ── deps-tree mode (Linux ELF) ───────────────────────────────────────────
  CMD+=(deps tree)
  CMD+=("${INPUT_NEW_LIBRARY:?new-library is required for deps-tree mode}")

  add_single_flag "--sysroot" "${INPUT_SYSROOT:-}"
  add_flag "--search-path" "${INPUT_SEARCH_PATH:-}"
  add_single_flag "--ld-library-path" "${INPUT_LD_LIBRARY_PATH:-}"

  # Format — deps-tree supports markdown, json, and html (`deps tree
  # --help`; html renders via cli_stack.py's stack_to_html). Hard error on
  # anything else (sarif), not a silent fallback.
  FORMAT="${INPUT_FORMAT:-markdown}"
  if [[ "$FORMAT" != "markdown" && "$FORMAT" != "json" && "$FORMAT" != "html" ]]; then
    echo "::error::mode: deps-tree does not support format: $FORMAT. Only 'markdown', 'json', and 'html' are supported."
    exit 1
  fi
  CMD+=(--format "$FORMAT")

  if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    CMD+=(--dry-run)
  else
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-}"
    if [[ -n "$OUTPUT_FILE" ]]; then
      CMD+=(-o "$OUTPUT_FILE")
    fi
  fi

elif [[ "$MODE" == "deps-compare" ]]; then
  # ── deps-compare mode (Linux ELF) → `deps compare` ──────────────────────
  CMD+=(deps compare)
  CMD+=("${INPUT_NEW_LIBRARY:?new-library (binary path) is required for deps-compare mode}")
  CMD+=(--old-root "${INPUT_OLD_ROOT:?old-root is required for deps-compare mode}")
  CMD+=(--new-root "${INPUT_NEW_ROOT:?new-root is required for deps-compare mode}")

  add_flag "--search-path" "${INPUT_SEARCH_PATH:-}"
  add_single_flag "--ld-library-path" "${INPUT_LD_LIBRARY_PATH:-}"

  # Format — deps-compare supports markdown, json, and html (`deps compare
  # --help`; html renders via cli_stack.py's stack_to_html). Hard error on
  # anything else (sarif), not a silent fallback.
  FORMAT="${INPUT_FORMAT:-markdown}"
  if [[ "$FORMAT" != "markdown" && "$FORMAT" != "json" && "$FORMAT" != "html" ]]; then
    echo "::error::mode: deps-compare does not support format: $FORMAT. Only 'markdown', 'json', and 'html' are supported."
    exit 1
  fi
  CMD+=(--format "$FORMAT")

  if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    CMD+=(--dry-run)
  else
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-}"
    if [[ -n "$OUTPUT_FILE" ]]; then
      CMD+=(-o "$OUTPUT_FILE")
    fi
  fi

else
  echo "::error::Unknown mode '$MODE'. Use 'compare', 'dump', 'deps-tree', or 'deps-compare'."
  exit 1
fi

if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then
  CMD+=(-v)
fi

# ---------------------------------------------------------------------------
# Run abicheck
# ---------------------------------------------------------------------------
# Append extra-args (pass-through CLI arguments)
if [[ -n "${INPUT_EXTRA_ARGS:-}" ]]; then
  # A synthesized --config (from dso-only/fail-on-removed-library/compile-
  # context inputs) already in $CMD, colliding with the user's own
  # extra-args --config, would silently lose to Click's last-repeated-flag
  # rule -- see _extra_args_has_config_flag's own docstring for the full
  # account. Caught here, once, right before the append that would create
  # the collision.
  if _cmd_has_config_flag && _extra_args_has_config_flag; then
    echo "::error::extra-args passes its own --config, which conflicts with the --config this Action already synthesized from a dso-only/fail-on-removed-library/include-private-dso/compile-context input (both cannot be honored -- Click keeps only the last one, silently dropping the other). Use the 'build-config' input instead of 'extra-args: --config ...' when combining a synthesized setting with a project config file; it merges with, rather than replaces, the synthesized overlay."
    exit 1
  fi
  # A flag `compare` genuinely lacks -- `--pattern-verdicts` included --
  # raises `compare`'s own real, correct Click usage error (ADR-068's
  # re-scoping: this Action keeps no compatible CLI-flag surface with the
  # now-removed `mode: scan`). Every mode appends `extra-args` the same
  # plain way.
  # shellcheck disable=SC2206
  CMD+=($INPUT_EXTRA_ARGS)
fi
# --- END: extra-args append block ---
# (CodeRabbit review, PR #1172, round 20: a dedicated, code-shaped sentinel
# so a test extracting this region by text boundary has a stable marker
# that a rewording of the surrounding prose can't silently break.)

# Recomputed here (idempotently -- the compare branch already computed it
# above, right after its own `$FORMAT` was set, so its own PR_JSON
# sidecar-injection decisions could see it too) for every JSON-detection
# site below that needs the real format this invocation runs with rather
# than the nominal `$FORMAT` -- see `_effective_format`'s own docstring.
# Also the only assignment for modes with no earlier one of their own
# (dump has no `$FORMAT` at all; deps-tree/deps-compare have one but no
# sidecar-injection decision that needs it early).
_EFFECTIVE_FORMAT="$(_effective_format)"

echo "::group::abicheck $MODE"
printf '%s\n' "Command: $(_sanitize_annotation "${CMD[*]}")"
echo ""

ABICHECK_EXIT=0
ABICHECK_OUTPUT=""
STDERR_FILE=$(mktemp)
#: PR_JSON (Codex review) is created well after this trap is installed --
#: either by the primary CMD's own --write
#: (compare/scan, non-JSON primary format) or by `_maybe_post_pr_comment`'s
#: reuse-or-rerun fallback -- but bash re-evaluates a single-quoted trap
#: string at EXIT time, so referencing it here (like `_STDOUT_JSON_FILE`/
#: `_BASELINE_CLEANUP` already do) cleans it up whenever it exists, even on
#: a non-PR-comment run or `pr-comment-on: never` where the temp file was
#: still created but never posted. Without this, a persistent self-hosted
#: runner accumulates one JSON report per scan run indefinitely.
#: _COMPILE_CONTEXT_CONFIG_OVERLAY/_RELEASE_TOPOLOGY_CONFIG_OVERLAY (Codex
#: review, fresh evidence): both are created well before this trap is
#: installed too (add_compile_context_flags/add_release_topology_config_
#: flags run while CMD is still being assembled), same "referencing a
#: not-yet-existing empty-string global is a no-op, not an error" property
#: as PR_JSON above -- a run that never enables either input just cleans
#: up an empty path.
trap 'rm -f "$STDERR_FILE" "${_STDOUT_JSON_FILE:-}" "${PR_JSON:-}" "${_COMPILE_CONTEXT_CONFIG_OVERLAY:-}" "${_RELEASE_TOPOLOGY_CONFIG_OVERLAY:-}"; rm -rf "${_BASELINE_CLEANUP:-}" "${_PY_SAFE_DIR:-}"' EXIT

# `_json_report_src`/`_extra_args_write_json_path` below trust `OUTPUT_
# FILE`/a user-supplied `--write json=PATH` purely on "the file exists and
# is non-empty" -- both are pure *write* destinations for this invocation
# (`CMD+=(-o "$OUTPUT_FILE")` above), but if either path already held
# content BEFORE this invocation (a stale file from a previous step, or
# one a PR author committed into the checked-out tree -- `INPUT_EXTRA_ARGS`
# and its own `--write` path are PR-controlled per this file's own threat
# model) and `abicheck` then fails before overwriting it, every
# downstream consumer of that file (annotations, coverage/severity/
# verdict queries, the sticky PR comment) would silently read stale or
# attacker-controlled content as if it were this run's own report.
#
# A first fix here deleted any pre-existing content at both paths before
# `${CMD[@]}` ran -- reverted (Codex review, fresh evidence): `OUTPUT_
# FILE`/the `--write` destination are still just `INPUT_*` values, and
# nothing here can prove they don't happen to collide with a real *input*
# path (`old-library`/`new-library`/a baseline file/etc, whether by an
# honest misconfiguration or a crafted `extra-args`) -- unconditionally
# unlinking a user-controlled path before Click has even validated the
# invocation risks destroying the very input the comparison needed,
# unconditionally and irrecoverably, which is strictly worse than the
# staleness bug it was fixing. Fixed non-destructively instead: record
# each path's (mtime, size) fingerprint before running, and only trust it
# afterward if that fingerprint changed (or the path didn't exist before).
# Python, not `stat -c`/`stat -f` (GNU vs. BSD/macOS spell this
# differently and this script already leans on `_PY_BIN` for exactly this
# class of portability need -- see `_report_query`'s own docstring).
_file_fingerprint() {
  # Empty output means "does not exist" -- a fingerprint that can never
  # equal a real file's, so "did not exist before, exists now" always
  # reads as changed without a separate existence check.
  [[ -n "$_PY_BIN" && -n "${1:-}" ]] || return 0
  local _fingerprint_path="$1"
  # The Python process deliberately runs outside the untrusted checkout, but
  # report destinations are still relative to the action's original working
  # directory. Anchor before entering $_PY_SAFE_DIR so its stat target keeps
  # the same meaning as abicheck's output path.
  if ! _is_path_already_qualified "$_fingerprint_path"; then
    _fingerprint_path="$PWD/$_fingerprint_path"
  fi
  (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import os, sys
try:
    st = os.stat(sys.argv[1])
except OSError:
    pass
else:
    print(f"{st.st_mtime_ns}:{st.st_size}")
' "$_fingerprint_path") 2>/dev/null
}
_output_file_pre_fp=""
if [[ -n "${OUTPUT_FILE:-}" ]]; then
  _output_file_pre_fp="$(_file_fingerprint "$OUTPUT_FILE")"
fi
_extra_write_json_path="$(_extra_args_write_json_path || true)"
_extra_write_json_pre_fp=""
if [[ -n "$_extra_write_json_path" ]]; then
  _extra_write_json_pre_fp="$(_file_fingerprint "$_extra_write_json_path")"
fi

if [[ -n "${OUTPUT_FILE:-}" ]]; then
  # Output goes to file; capture stderr separately for error detection
  "${CMD[@]}" 2>"$STDERR_FILE" || ABICHECK_EXIT=$?
  if [[ -s "$STDERR_FILE" ]]; then
    cat "$STDERR_FILE" >&2
  fi
else
  # Capture stdout for job summary; stderr goes to temp file
  ABICHECK_OUTPUT=$("${CMD[@]}" 2>"$STDERR_FILE") || ABICHECK_EXIT=$?
  echo "$ABICHECK_OUTPUT"
  if [[ -s "$STDERR_FILE" ]]; then
    cat "$STDERR_FILE" >&2
  fi
fi
echo "::endgroup::"

# ---------------------------------------------------------------------------
# Map exit code to verdict
# ---------------------------------------------------------------------------
STDERR_CONTENT=""
if [[ -s "$STDERR_FILE" ]]; then
  STDERR_CONTENT=$(cat "$STDERR_FILE")
fi

# `format: json` with no `output-file` is the documented stdout mode: the
# report exists only in $ABICHECK_OUTPUT, so it is persisted once here for the
# report queries below.
#
# Gated on `$_EFFECTIVE_FORMAT`, not the nominal `$FORMAT` -- an `extra-args`
# `--format json` override (under `format: text`/`markdown`) really does
# produce JSON on stdout, and this capture used to miss it entirely (ADR-064's
# "effective-format-override" gap; see `_effective_format`'s own docstring).
#
# In the *parent* shell, deliberately. Every caller reads the path through
# `_src=$(_json_report_src)`, and a command substitution runs in a subshell —
# so creating the file lazily inside that function wrote the memo to a shell
# that then exited, making each of the three callers mint its own copy and
# leaving the EXIT trap with an empty path to clean up. On a persistent
# self-hosted runner that leaks a full report copy per lookup (Codex review).
_STDOUT_JSON_FILE=""
if [[ "${_EFFECTIVE_FORMAT:-${FORMAT:-}}" == "json" && "${ABICHECK_OUTPUT:-}" == "{"* ]]; then
  _STDOUT_JSON_FILE=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-stdout-json.XXXXXX")
  printf '%s' "$ABICHECK_OUTPUT" > "$_STDOUT_JSON_FILE"
fi

_is_cli_error() {
  echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try |Traceback|click\.)'
}

# The JSON report this run produced, if any — the primary output when
# format=json, or (the common case: default format=markdown) the
# always-unfiltered secondary JSON the compare-mode command setup above
# already asks the same invocation to write via --write. Empty
# when neither exists. One function because three separate decisions below
# read the same report and must not disagree about which one it is.
#
# The stdout-mode file above is the third source, already materialised — the
# function only ever *reads* a path, so it stays safe to call from a command
# substitution. Without that file, every decision below took its "no report"
# fallback for the one configuration that keeps the report on stdout.
_json_report_src() {
  # `OUTPUT_FILE`/the discovered `--write json=PATH` are pure write
  # destinations that can pre-exist this invocation (see the fingerprint
  # bookkeeping around the `${CMD[@]}` call above for why) -- trusted only
  # when non-empty AND its (mtime, size) fingerprint changed since just
  # before `${CMD[@]}` ran (or it didn't exist then at all, i.e. its pre-
  # fingerprint was empty). `PR_JSON` (always a fresh mktemp this run) and
  # `_STDOUT_JSON_FILE` (this run's own captured stdout) need no such
  # check -- neither can be a pre-existing file.
  #
  # `${_output_file_pre_fp+x}`/`${_extra_write_json_pre_fp+x}` (POSIX
  # parameter-expansion existence tests, not bash-4.2+'s `-v` -- this repo
  # targets macOS's stock bash 3.2 too) distinguish "the pre-run bookkeeping
  # ran and found no file there" (set, empty) from "the bookkeeping never
  # ran at all" -- several existing tests (`test_action_run_sh_severity_
  # summary.py`, `test_action_run_sh_pr_json.py`, ...) extract `_json_
  # report_src` and its sibling helpers as an isolated snippet, deliberately
  # never executing the `${CMD[@]}` invocation section this bookkeeping
  # lives in -- so in that narrower context the freshness variables are
  # never assigned at all, not even to "". Enforcing freshness there would
  # silently reject every report those tests hand it (Codex review, fresh
  # evidence -- the fingerprint feature caught two of its own consuming
  # tests as a false positive, not a real staleness case). Degrading to the
  # pre-fingerprint "exists and non-empty" rule exactly when the bookkeeping
  # never ran preserves this file's real, in-production freshness guarantee
  # unchanged, since the real script always assigns both variables (even to
  # "") before `_json_report_src` can ever be called.
  #
  # `${_EFFECTIVE_FORMAT:-${FORMAT:-}}`, not a bare `${FORMAT:-}`, for the
  # same reason as the freshness variables just above: the real script
  # always sets `_EFFECTIVE_FORMAT` before this function can be called (see
  # `_effective_format`'s own docstring for why the nominal `$FORMAT` alone
  # misses an `extra-args --format json` override), while the isolated
  # extraction tests above set only `$FORMAT` and rely on the fallback to
  # keep behaving exactly as before this fix.
  if [[ "${_EFFECTIVE_FORMAT:-${FORMAT:-}}" == "json" && -n "${OUTPUT_FILE:-}" && -s "${OUTPUT_FILE:-}" ]] \
     && { [[ -z "${_output_file_pre_fp+x}" ]] \
          || [[ "$(_file_fingerprint "$OUTPUT_FILE")" != "$_output_file_pre_fp" ]]; }; then
    echo "${OUTPUT_FILE}"
  elif [[ -n "${PR_JSON:-}" && -s "${PR_JSON:-}" ]]; then
    echo "${PR_JSON}"
  elif [[ -n "${_STDOUT_JSON_FILE:-}" ]]; then
    echo "${_STDOUT_JSON_FILE}"
  elif [[ -n "${_extra_write_json_path:-}" && -s "${_extra_write_json_path:-}" ]] \
       && { [[ -z "${_extra_write_json_pre_fp+x}" ]] \
            || [[ "$(_file_fingerprint "$_extra_write_json_path")" != "$_extra_write_json_pre_fp" ]]; }; then
    # A user-supplied `--write json=PATH` in extra-args (see
    # `_extra_args_write_json_path`'s own docstring for why this is needed
    # rather than falling through to "no report").
    echo "$_extra_write_json_path"
  fi
  # Deliberately NOT a further fallback to a `format: sarif` OUTPUT_FILE
  # here, even though SARIF is well-formed JSON (Codex review, fresh
  # evidence, PR #1016): this function's contract is "a faithful,
  # unfiltered abicheck-native JSON report" -- `_can_reuse_primary_json`
  # trusts a non-empty answer here enough to `cp` it straight into
  # `PR_JSON` for `cli_pr_comment` to parse as one, and several `_report_
  # query` callers (severity_exit, coverage_where, annotations,
  # blocking_categories) would silently misread SARIF's absence of their
  # expected keys as "definitely no severity gate/no coverage gap/no
  # annotations" rather than "cannot tell". `_report_compat_verdict` used
  # to consult a SARIF `runs[0].properties.abiVerdict` of its own for that
  # reason, reading `_EFFECTIVE_FORMAT`/`OUTPUT_FILE` directly rather than
  # routing through this shared function; ADR-063 Track T8 retired that
  # fallback along with the rest of the boundary's verdict reconstruction,
  # so no reader consults a SARIF document for a verdict any more.
}

# Read one derived value out of the JSON report, whatever shape produced it.
#
# **Python, not jq.** The composite Action installs no `jq`; GitHub-hosted
# runners happen to ship it, self-hosted ones need not. On a runner without
# it, a JSON-format coverage-gated run had no signal at all -- the CLI
# deliberately prints no stderr notice when the report already carries the
# ledger -- so scan published ERROR and compare SEVERITY_ERROR for a run whose
# own report said otherwise (Codex review). Python is the dependency this
# Action really has: it runs `actions/setup-python`, and `abicheck` is itself
# a Python console script, so an interpreter exists in any run that got far
# enough to produce a report.
#
# One implementation, not a jq fast path with a Python fallback: two parsers
# for one question is the shape that drifts. Queries are named rather than
# passed as expressions, so a caller cannot inject one.
#
# The query semantics themselves live in `report_query.py` beside this file,
# not in a heredoc here -- see that module's docstring for why, and for the
# exit-code contract (0 answered / 1 cannot tell / 2 unknown query) this
# function's callers depend on. The move was verified behavior-preserving
# against the heredoc it replaced over ~247k adversarially-generated
# (document, query, argument) cases, with exactly two deliberate differences,
# both pinned by `tests/test_action_report_query.py`: an absent
# `analysis_assurance_exit_contribution` now answers "cannot tell" instead of
# a defaulted `0` (`_assurance_gated` tests for an exact "1", which an empty
# answer fails identically, so the gate is unchanged -- what changes is that
# "the report did not say" stops being spelled the same way as "the report
# said no"), and the `assurance_status` query, which no caller asked for, is
# gone rather than carried over. Everything else answers byte-for-byte what
# the heredoc answered, malformed documents included.
#
# Report shapes nest the ledger differently and BOTH reach here: `compare`
# writes it at the top level, while an `--against`-style summary sits under
# `diff`. Every query looks in both.
#
# _PY_BIN itself is resolved once, near the top of this script (before
# MODE's dry-run/back-compat block) -- not here -- so the baseline-set
# fallback (which runs long before this function is ever reached) can use
# the same resolved interpreter too.
_report_query() {
  # $1 = report path, $2 = query name, $3 = optional query-specific argument
  # (only the "annotations" query reads it, as a "1"/"" additions flag).
  # Prints nothing when the report cannot be read or parsed, which every
  # caller treats as "cannot tell" rather than as an answer.
  [[ -n "$_PY_BIN" && -n "${1:-}" && -f "$_REPORT_QUERY_PY" ]] || return 1
  # Isolated the same way as every other Python invocation in this file
  # (Codex review, fresh evidence): the sitecustomize.py auto-import vector
  # fires during interpreter *startup*, before the script body ever runs a
  # single line -- it doesn't depend on what the body imports, only on where
  # the interpreter starts. `-I` (isolated mode) is what a script *file*
  # needs and a `-` stdin script did not: running a file puts the file's own
  # directory on sys.path[0], where stdin put the CWD, so the `cd
  # "$_PY_SAFE_DIR"` below no longer covers it on its own. `-I` drops both
  # that entry and the user site directory; `report_query.py` imports only
  # `json`/`sys`, so it loses nothing it needs.
  #
  # $1 is NOT reliably absolute -- unlike $_STDOUT_JSON_FILE (mktemp-
  # rooted), $OUTPUT_FILE (this function's other caller shape, via
  # $_json_report_src) can be a bare user-supplied INPUT_OUTPUT_FILE value
  # or a relative default (e.g. "abicheck-baseline.json"), relative to the
  # workflow's own working directory -- which the `cd "$_PY_SAFE_DIR"`
  # below would otherwise resolve it against instead (the identical class
  # of bug this same pass already fixed for $BASELINE_DIR). Anchored to
  # $PWD *before* that cd, since that's the correct base directory at this
  # point in the script.
  local report_path="$1"
  if ! _is_path_already_qualified "$report_path"; then
    report_path="$PWD/$report_path"
  fi
  (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -I "$_REPORT_QUERY_PY" "$report_path" "$2" "${3:-}") 2>/dev/null
}

# CLI cleanup phase two, PR E: the Action's own annotation renderer. Reads
# the persisted `annotations` array (schema 2.43/2.44) off whichever JSON
# report this run produced -- the same `_json_report_src` every other
# post-processing decision in this script already reads -- instead of
# asking `abicheck` itself to render `::error`/`::warning`/`::notice`
# workflow commands to its own stderr via `--annotate`. Works uniformly for
# a single-library `compare` (top-level `annotations`) and a
# directory/package release `compare` (`libraries[].annotations`), since
# the `annotations` query above already flattens both shapes -- and for a
# release operand this also means no second per-library comparison is ever
# run just to render annotations, the same "no comparison re-run" this
# whole persisted-report design exists for.
#
# Deliberately does not touch `scan --against`: that report carries no
# `annotations` field as of this schema version (the query prints nothing
# for it, not an error), so this is a genuine no-op there rather than a
# scoped-out branch to maintain.
_emit_annotations() {
  if [[ "${INPUT_ANNOTATE:-false}" != "true" ]]; then
    # `annotate-additions: true` alone, with `annotate` left at its
    # default `false`, used to be a hard CLI usage error
    # (`--annotate-additions requires --annotate`, removed along with the
    # flags themselves). An Action input has no equivalent usage-error
    # mechanism, but silently rendering nothing for this combination is
    # still a real, surprising behaviour change from that (CodeRabbit
    # review) -- say so instead.
    if [[ "${INPUT_ANNOTATE_ADDITIONS:-false}" == "true" ]]; then
      echo "::notice title=abicheck annotate::annotate-additions is true but annotate is false, so no annotations are rendered. Set annotate: true as well."
    fi
    return 0
  fi
  # compare's audit-only shape (old-library/abi-baseline both omitted) has
  # no `annotations` array in its own report schema at all (it has no
  # additions/removals to annotate, only candidate-side `findings`) -- the
  # `annotations` report query below would already silently print nothing
  # for it, which reads as "requested but nothing found" rather than "not
  # supported on this shape". Say so explicitly instead (Codex review, PR
  # #1223, round 11) -- the same "never silently emit nothing" treatment
  # every other annotate gap above already gets.
  if [[ "$MODE" == "compare" && "${_NO_BASELINE:-false}" == "true" ]]; then
    echo "::notice title=abicheck annotate::annotate is not supported for compare's audit-only shape (old-library/abi-baseline both omitted) -- its report has no additions/removals to annotate, only candidate-side findings (see the JSON report's findings[]). No annotations are rendered."
    return 0
  fi
  local _src _additions
  _src=$(_json_report_src)
  if [[ -z "$_src" ]]; then
    # A user-supplied `--write FORMAT=PATH` in extra-args targeting a
    # non-json FORMAT (markdown/junit/sarif/html/review) leaves genuinely
    # no JSON report anywhere in one real remaining case: a directory/
    # package (release) operand, where only one `--write` is supported at
    # all (`compare --help-all`), so the compare/scan command-assembly
    # sections above deliberately do NOT append a second, internal `--write
    # json=...` alongside the user's own there (`_extra_args_write_would_
    # conflict`'s own docstring has the full account, including why a
    # single-pair/audit-only operand does NOT reach this branch at all
    # anymore -- `--write` is repeatable there, so this script's own
    # injection now runs alongside the user's non-json one instead of being
    # unconditionally suppressed, Codex review, PR #1210, round 9). Unlike
    # the `json=` case `_extra_args_write_json_path` recovers, there is
    # nothing to discover here, so say so rather than silently emitting
    # nothing (Codex review, fresh evidence).
    #
    # Gated on the effective format, not the nominal one (Codex review, PR
    # #998, fresh evidence): `format: json` overridden by `extra-args
    # --format text` (say, alongside its own `--write markdown=...`) really
    # does leave no JSON report anywhere -- `_src` above is correctly
    # empty -- but the nominal check here suppressed this very diagnostic
    # explaining why, since it still believed the primary was JSON.
    if [[ "${_EFFECTIVE_FORMAT:-${FORMAT:-}}" != "json" ]] && _extra_args_has_write_flag \
       && [[ -z "$(_extra_args_write_json_path)" ]]; then
      echo "::notice title=abicheck annotate::annotate/annotate-additions requested, but the primary format isn't json and extra-args' own --write targets a non-json format -- no JSON report is available to render annotations from. Use format: json, or point --write at json=PATH instead."
    fi
    return 0
  fi
  _additions="0"
  [[ "${INPUT_ANNOTATE_ADDITIONS:-false}" == "true" ]] && _additions="1"
  # Deliberately NOT captured via $(...) -- each printed line is a real
  # GitHub Actions workflow command and must reach the actual log/stdout,
  # not be swallowed into a shell variable the way every other
  # `_report_query` caller above wants it.
  _report_query "$_src" annotations "$_additions"
}

# Did ADR-049's orthogonal contract-coverage axis contribute to this exit?
#
# Since ADR-049 Phase 7, a run passing --contract (via extra-args)
# whose selected contract domain cannot be closed on the available evidence
# contributes exit 1 — independently of the compatibility verdict, which the
# axis deliberately never rewrites. Without asking, `scan` published
# verdict=ERROR (an operational failure) and `compare` SEVERITY_ERROR (a
# severity-policy failure) for what is neither.
#
# One signal, and only one: the JSON report's own
# `contract_coverage_exit_contribution` field. Absent a `--contract` the
# field reads 0 and the mapping below is exactly what it was.
#
# ADR-063 Track T8 removed the stderr-text fallback this function used to
# carry (a `grep 'Contract coverage incomplete'` over `$STDERR_CONTENT`,
# plus a second grep excluding the `contract.unresolved=warn`-accepted
# wording, since the notice is worded identically for both). Reconstructing
# an axis contribution by regex-matching rendered prose is exactly the
# raw-exit/stderr reconstruction that track retires: the structured
# `run_outcome`/JSON contract is the boundary's one source of truth, and
# the absence of structured data means this axis *cannot be claimed to have
# fired*, not that it may be guessed at from a diagnostic line. So an
# unreadable/absent JSON report answers "not gated" here. `_report_query`
# prints nothing (empty string) exactly in that case.
_coverage_gated() {
  local _src _contribution
  _src=$(_json_report_src)
  _contribution=$(_report_query "$_src" coverage_contribution)
  [[ -n "$_contribution" && "$_contribution" == "1" ]]
}

# Did P0.4's orthogonal analysis-assurance axis
# (`assurance.require_complete`, analysis_assurance.py) contribute to this
# exit?
#
# History, briefly (fuller account retained in git blame/PR history rather
# than repeated here): this predicate used to gate on a dedicated
# `require-complete-analysis` Action input first, since the JSON report's
# `analysis_assurance` block used to attach unconditionally (status
# included) regardless of whether gating was ever requested -- so a present,
# non-"complete" status alone couldn't tell "this run asked to gate on it"
# apart from "this run's evidence happens to be partial and nobody asked".
# Three earlier revisions tried to infer that request from other signals
# (an unanchored stderr grep, a `$CMD`-array token scan, an `extra-args`-
# scoped token scan) and each was a real, Codex-found forgery/collision bug
# before the dedicated input replaced all three.
#
# `require-complete-analysis` is now retired entirely (this PR): the CLI's
# only remaining source for the setting is `.abicheck.yml`'s
# `assurance.require_complete: true`, a config-only path
# `validate-inputs.sh` cannot see or forward as a boolean the way it could a
# real Action input. Re-deriving "was gating requested" from an Action-level
# signal is therefore no longer possible in the way the retired input made
# possible -- and it no longer needs to be: `analysis_assurance_exit_
# contribution` (schema 2.40) is *already* self-describing the exact way
# `contract_coverage_exit_contribution` is for `_coverage_gated` above --
# `cli_compare_helpers`/`checker.compare` compute it as 0 unless the
# *resolved* `assurance.require_complete` (wherever it came from -- CLI flag
# while that existed, or the config file now) was true AND the run's own
# evidence was incomplete, folding both "was this asked for" and "did it
# fire" into the one number the CLI's own exit code was itself floored by.
# Reading that field directly is therefore not a weaker substitute for the
# retired input check -- it is the more general form the input check was
# only ever approximating, and it is correct for every source `assurance.
# require_complete` can be given from (CLI or config), not only the one the
# retired input covered.
#
# Read from the structured report alone, the same "cannot claim it fired"
# contract `_coverage_gated` states: an unreadable/absent report, or an
# absent field on an older report, means "not gated by this axis" rather
# than a guess reconstructed from rendered prose (ADR-063 Track T8 retired
# exactly that class of stderr/diagnostic reconstruction for this axis's
# sibling).
_assurance_gated() {
  local _src _contribution
  _src=$(_json_report_src)
  _contribution=$(_report_query "$_src" assurance_contribution)
  [[ -n "$_contribution" && "$_contribution" == "1" ]]
}

# Why the JSON report this run was supposed to produce could not be read, as
# one token from `report_query.py`'s own vocabulary: ok / absent / unreadable
# / unparseable / not_object / empty / no_result. Prints nothing when the source
# itself could not be located at all (no `$_json_report_src`), which is a
# *fifth* state -- "nothing to classify" -- and the one the dry-run and
# no-JSON-output paths legitimately reach.
#
# This exists because every axis predicate above deliberately answers "not
# gated by this axis" for a report it cannot read, and that was the right
# call: ADR-063 Track T8 retired the alternative (reconstructing an axis from
# the CLI's own stderr prose) after a PR-controlled build script was shown
# able to forge those notices. But "I cannot establish that this axis failed"
# is not "this axis passed", and the exit-0 dispatch was reading it as the
# latter -- `_resolve_clean_exit_verdict` opened with VERDICT="COMPATIBLE"
# and only ever *escalated* from a report it could read, so an absent or
# corrupt report published "No binary ABI break detected" for a run whose
# result nothing had established. Asking the separate question here, rather
# than changing what the axis predicates answer, keeps the anti-forgery
# contract exactly as it was.
_report_validity() {
  local _src
  _src=$(_json_report_src)
  [[ -n "$_src" ]] || return 0
  _report_query "$_src" report_validity
}

# Did the *caller* ask for a JSON report -- and so, is its absence a failure?
#
# `_json_report_src` answers "is there a readable one", which conflates two
# very different situations at exit 0: the run produced no JSON where the user
# asked for one, versus the run was never asked for JSON at all.
#
# Every mode in which the *caller* asked for JSON, and only those:
#
#   * `format: json` -- whether it lands in `output-file` or on stdout. Keying
#     on `$OUTPUT_FILE` as well left the documented stdout mode uncovered, so
#     an exit-0 run that printed nothing still published COMPATIBLE (Codex
#     review, P2, reproduced). Requesting json *is* the request; where it lands
#     is a separate choice.
#   * `extra-args --write json=PATH` -- a caller-supplied destination, no less
#     requested for arriving through the passthrough.
#
# The one exclusion is the internal `$PR_JSON` sidecar, which this script
# injects for its own PR-comment and annotation rendering whenever the primary
# format is not json. That sidecar is an implementation detail of this Action,
# it already has its own diagnostic when it turns up missing ("no JSON report
# is available"), and treating its absence as "this run established no result"
# conflates a convenience artifact with the comparison's outcome.
#
# Note this cannot key on `$_STDOUT_JSON_FILE`: that variable is only set when
# stdout actually *started with* `{`, so it is empty in precisely the failure
# case this predicate exists to catch.
#
# The narrowing matters for a second reason, which is why it is stated here
# rather than left to the call site: at exit 0 the process exit code is itself
# evidence, not the absence of it. It is the second of the two sources "How
# `run.sh` resolves the verdict it publishes" (action/AGENTS.md) deliberately
# keeps -- the kernel-reported answer to "what did the invocation itself say"
# -- and `compare` exiting 0 means its own gate did not fire. So an
# unreadable *sidecar* leaves the tier unknown, which is a real and separate
# problem (see that section's own note on the residual), but it does not leave
# the run's acceptance unknown. Only a JSON report the user explicitly
# requested and did not get is an operational failure of this step.
_json_report_expected() {
  [[ "${_EFFECTIVE_FORMAT:-${FORMAT:-}}" == "json" ]] \
    || [[ -n "${_extra_write_json_path:-}" ]]
}

# The analysis-assurance axis's own four-way answer (see `report_query.py`'s
# `assurance_axis`): gated / not_gated / absent_legacy_schema /
# contradictory. `_assurance_gated` above stays the gate -- this is the
# *provenance* of that answer, which is what distinguishes an older report
# that predates `analysis_assurance_exit_contribution` (schema 2.40) from a
# current one that should carry it and does not. Prints nothing when there is
# no report to ask.
_assurance_axis() {
  local _src
  _src=$(_json_report_src)
  [[ -n "$_src" ]] || return 0
  _report_query "$_src" assurance_axis
}

# A report claiming schema 2.40 or newer while omitting the assurance
# contribution it is then required to carry. That is an internally
# inconsistent *result*, not a passing assurance check, so it fails the step
# on its own -- the one case where an absent field is treated as a failure
# rather than as "cannot tell", and it is safe to do so precisely because the
# report's own version claim is what rules out the legacy explanation.
_assurance_axis_contradictory() {
  [[ "$(_assurance_axis)" == "contradictory" ]]
}

# ADR-065 S2's completeness axis (D6 under --on-incomplete-scope block, D7
# -- no comparison completed -- under every setting), read the way
# `_coverage_gated` reads its own: the structured report's already-folded
# contribution is the *sole* answer. An earlier revision fell back to
# grepping the CLI's stderr notice when no readable JSON report existed
# (and excluded its `warn`-accepted form by its "Accepted by
# --on-incomplete-scope" wording); Codex found that the diagnostic embeds
# member failure reasons -- PR-controlled filenames, tool output -- so a
# hostile value could spell that phrase and suppress a real `block`
# contribution, the same forgeable-prose class ADR-063 Track T8 retired
# for the coverage and assurance axes. No structured data therefore means
# "not gated by this axis": the process exit still fails the step, only
# the SCOPE_INCOMPLETE label is withheld.
_scope_gated() {
  local _src _contribution
  _src=$(_json_report_src)
  _contribution=$(_report_query "$_src" scope_contribution)
  [[ "$_contribution" == "1" ]]
}

# Informational sibling (Codex review): the report recorded an incomplete
# scope, gating or accepted under `--on-incomplete-scope warn`. Read from the
# structured report only, like `_scope_gated`, and never a failure signal --
# it only decides whether the summary names the unchecked members.
_scope_incomplete() {
  local _src
  _src=$(_json_report_src)
  [[ "$(_report_query "$_src" scope_incomplete)" == "1" ]]
}

# The evidence-contract axis (ADR-037 D5 -- a *pinned* --depth whose required
# source evidence was never collected) has its own dedicated process exit code,
# checked directly in the `case $ABICHECK_EXIT in ...` dispatch below -- no
# helper predicate needed.
# cli-mirror: abicheck/policy/exit_decision_precedence.py::EXIT_EVIDENCE_CONTRACT_ERROR
# (Previously cited `scan_engine._EvidenceContractError` and
# `_EXIT_EVIDENCE_CONTRACT_ERROR = 7` "in cli_scan.py". Both modules were
# deleted with ADR-068's retirement of `scan`; the constant is engine-level
# now, and the value is unchanged at 7.) Earlier revisions tried a
# stderr marker line, then a marker-file path passed as an environment
# variable; both were shown forgeable by a PR-controlled build script
# running as part of this very scan's own evidence collection (three Codex
# review rounds, PR #1032 -- see ADR-064 for the full account). A process's
# own exit code, reported to its parent by the OS kernel via `wait()`, is
# the one channel nothing this run spawns can forge.
#
# `--artifact-set` shares the identical code 7 as of 2026-09-04
# (`service_scan._aggregate_scan_set_verdict`) rather than the generic exit
# 1 it used to floor at for this axis -- the cli-cleanup-phase-two plan had
# recorded this as "does NOT generalize" (no per-member OS exit code to
# report for a multi-member in-process scan), but that reasoning conflated
# "the set has no per-*member* exit code" with "the set process itself has
# no room for a dedicated code" -- the *set's* own single process exit was
# never anything but this generic 1 with no other consumer, so redirecting
# it to 7 needed no new design, only re-checking an old conclusion against
# the actual code.

# The compatibility axis's own exit code, from the JSON report's severity gate
# (`severity.exit_code`, schema 2.3). Computed by abicheck *before* the
# coverage fold, so it is what tells a shared exit 1 apart: a severity
# category gating, or coverage alone.
#
# A readable report with **no** `severity` block answers `0`, not "unknown".
# The block is emitted only when the resolved scheme is `severity`
# (`cli_compare_helpers` passes `severity_config` on exactly that condition),
# so its absence means the legacy scheme -- whose compare exit codes are
# 0/2/4 and never 1. The compatibility axis therefore contributed 0 to a
# legacy exit 1, by construction. Treating the absent block as "cannot tell"
# classified every coverage-gated run under the *default* scheme as
# SEVERITY_ERROR (Codex review).
#
# Empty only when there is no readable report at all -- no file, or one that
# cannot be parsed, in which case `_report_query` prints nothing. That is the
# genuine "cannot tell", and the caller keeps its established verdict rather
# than guessing.
#
# ADR-063 Track T8 removed the rendered-**text** fallback this function used
# to end with (a `sed` over the CLI's own `severity gate: exit N ...
# blocking: ...` line, reached when no JSON was readable -- the common shape
# for `scan`, whose documented default `format: text` writes no JSON
# sidecar). Scraping a renderer's prose to recover a gate exit is the
# textual reconstruction that track retires; the structured
# `run_outcome.gate` axis is the boundary's contract, with the legacy
# `severity_exit` field as its structured predecessor. With neither
# present, this function answers the empty "no signal" its callers already
# handle -- they keep the verdict the exit-code dispatch established.
_severity_gate_exit() {
  local _src _gate
  _src=$(_json_report_src)
  # ADR-063 Phase 7 (D6): prefer the report's own `run_outcome.gate` axis --
  # already the compatibility-policy gate this function exists to answer,
  # for both schemes (a legacy-scheme report still populates it, folded from
  # `legacy_exit_code(verdict)` -- see `policy/outcome.py`'s own
  # `run_outcome_dict_for_diff_result` docstring), so this needs no
  # scheme-specific handling of its own. Falls through to the pre-existing
  # structured `severity_exit` field below whenever the axis is absent (an
  # older abicheck), and to the empty "no signal" when there is no readable
  # JSON at all.
  _gate=$(_report_query "$_src" run_outcome gate)
  case "$_gate" in
    none) echo 0; return ;;
    addition_quality) echo 1; return ;;
    potential_breaking) echo 2; return ;;
    abi_breaking) echo 4; return ;;
  esac
  _report_query "$_src" severity_exit
}

# The categories the published gate blames, read from the JSON report's own
# `severity.blocking_categories`. Used by the scan final gate to tell a
# severity-configured block (which the user asked to be an error) from a
# promoted cross-check (which keeps following fail-on-api-break) -- a real
# gate decision (see the unconditional FINAL_EXIT check below), not merely a
# display detail, so it must never be reconstructed from rendered prose
# (ADR-063 Track T8). With no readable JSON this answers empty; the scan
# mode's own PR_JSON sidecar injection (unconditional as of Track T8, not
# gated on `pr-comment`) is what keeps JSON available for the mainline
# `scan --against` path this gate applies to.
_severity_gate_categories() {
  local _src
  _src=$(_json_report_src)
  _report_query "$_src" blocking_categories
}



# The compatibility verdict the report itself published, read only from
# structured JSON: `run_outcome.compatibility` first, then the legacy
# `compat_verdict` field.
#
# ADR-063 Track T8 removed the two textual reconstruction layers this
# function used to end with -- a SARIF `runs[0].properties.abiVerdict`
# lookup for a `format: sarif` run with no JSON anywhere, and a `sed -E`
# over the rendered markdown/text report's own `Verdict:`/`**Verdict**`
# line. Both re-derived the verdict from a renderer's output rather than
# from the boundary's structured contract, which is precisely what that
# track retires. When no JSON report is readable this function now prints
# nothing, and its two callers (`_escalate_verdict_to_report`,
# `_resolve_clean_exit_verdict`) already treat an empty answer as "no
# signal": each guards on an exact `BREAKING`/`API_BREAK`
# (/`COMPATIBLE_WITH_RISK`) equality that empty fails, so the verdict the
# `case $ABICHECK_EXIT in ...` dispatch established from the process exit
# code is published unchanged. A verdict is stated by the report or not at
# all -- it is never guessed from prose.
# Prints `run_outcome.operational` and succeeds when it names an operational
# status at all (anything but `none`/absent -- ADR-065 D7's own
# `no_comparison_completed` included: an exit 4 whose report says nothing
# was compared cannot be a compatibility break either); fails otherwise.
# Read from the report, never inferred from the exit code.
_operational_failure_status() {
  local _op
  _op=$(_report_query "$(_json_report_src)" run_outcome operational 2>/dev/null || true)
  case "$_op" in
    ""|none) return 1 ;;
    *) echo "$_op"; return 0 ;;
  esac
}

_report_compat_verdict() {
  local _src _answer _operational
  _src=$(_json_report_src)
  # ADR-063 Phase 7 (D6): the report's own `run_outcome.compatibility` is
  # this exact fact (`result.verdict`, unconditionally) under its canonical
  # name -- preferred over the raw `verdict` field lookup
  # below, which stays as this function's fallback for a report from an
  # older abicheck (no `run_outcome` block) or a synthetic report whose
  # `run_outcome.compatibility` is `null` (no real comparison ever ran, so
  # `_report_query` prints nothing for it -- `compat_verdict` may still hold
  # an operational sentinel string those reports use instead).
  #
  # Only trusted when `run_outcome.operational` is absent or `none` (Codex
  # review, fresh evidence): a directory/package release can legitimately
  # carry BOTH axes at once -- one library's real `BREAKING` result
  # (`compatibility`) alongside a *different* library's failed extraction
  # (`operational: extraction_error`), with the release's own top-level
  # `verdict` sentinel (`"ERROR"`) recording exactly that combination
  # (`policy.outcome.run_outcome_dict_for_release`'s own docstring:
  # `compatibility` is deliberately never the release's reported sentinel).
  # Returning `compatibility` here unconditionally let a real operational
  # failure silently launder into a plain compatibility break -- the
  # escalation path below (`_escalate_verdict_to_report`) would then claim
  # the severity policy produced this run's exit, hiding that a library
  # never finished comparing at all. Falling through to the legacy
  # `compat_verdict` query in that case preserves this function's original,
  # correct behavior for a release report (its raw `verdict` field is the
  # operational sentinel, which this function's own callers already know
  # not to escalate on).
  _operational=$(_report_query "$_src" run_outcome operational)
  if [[ -z "$_operational" || "$_operational" == "none" ]]; then
    _answer=$(_report_query "$_src" run_outcome compatibility)
    if [[ -n "$_answer" ]]; then
      echo "$_answer"
      return
    fi
  fi
  _report_query "$_src" compat_verdict
}

# Exit 0 is not the same fact as "no break was found". Under a demoting
# severity scheme -- `--severity-preset info-only`, or any `--severity-*`
# putting the breaking categories below `error` -- abicheck deliberately
# publishes 0 while its own report still says BREAKING/API_BREAK: the user
# asked for the finding to be *reported and not gated*. Mapping that exit
# straight to COMPATIBLE made the Action's `verdict` output and job summary
# claim no ABI break was detected, which is the one thing the report says it
# did detect (Codex review).
#
# So the published verdict follows the report, and the *gate* is what the
# severity policy switches off: ADVISORY_BREAK below suppresses the
# fail-on-breaking / fail-on-api-break blocks, so `info-only` keeps not
# failing the step exactly as before. Both modes resolve it here rather than
# only the one the review named -- compare's exit 0 has always had the same
# two possible causes, it just had no severity-aware scan beside it to make
# the asymmetry visible.
ADVISORY_BREAK=false
# The compatibility tier the *gate* follows. Empty means "same as VERDICT" —
# only an escalation (see `_escalate_verdict_to_report`) makes the two differ.
GATE_TIER=""
_resolve_clean_exit_verdict() {
  local _v _no_baseline_audit _validity
  VERDICT="COMPATIBLE"
  # An audit-only (no-baseline) dry run writes no JSON report at all --
  # `compare --dry-run` performs no analysis and only previews the command
  # it would have run (Codex review, fresh evidence): the `no_baseline_audit`
  # query below reads that (absent) report and prints nothing, so without
  # this check the fallthrough below would silently default to
  # VERDICT=COMPATIBLE -- "No binary ABI break detected" for a preview that
  # never compared anything, let alone the candidate's own public surface.
  # Checked first and returns early, before the no-baseline-audit report
  # query (which would find no report to read) and before
  # `_report_compat_verdict` (same reason). Two-sided dry-run is a
  # pre-existing, out-of-scope gap this fix does not touch -- see the
  # verdict output's own description, which documents only the audit-only
  # shape's dry-run behavior.
  if [[ "${_NO_BASELINE:-false}" == "true" ]] \
    && { [[ "${INPUT_DRY_RUN:-false}" == "true" ]] || _extra_args_has_dry_run_flag; }; then
    VERDICT="DRY_RUN"
    echo "::notice::--dry-run: this is a preview of the command that would run -- no analysis was performed and there is no candidate-side finding to report. Drop --dry-run to run the audit for real."
    return
  fi
  # A no-baseline audit's own `verdict` field is always null (no comparison
  # ran at all), so `_report_compat_verdict` below prints nothing for it and
  # this function would otherwise silently default to VERDICT=COMPATIBLE --
  # "No binary ABI break detected" for a run that never compared two builds
  # (Codex review, PR #1210, round 6). Checked first and returns early:
  # AUDIT_CLEAN (no candidate-side findings) or AUDIT_RISK (findings
  # present, none of them gated -- this function only runs on exit 0).
  # The report is the only thing that can support a compatibility claim on
  # this path, so establish that one exists and parses BEFORE any branch
  # below can default to COMPATIBLE. Every remaining reader here
  # (`no_baseline_audit`, `_report_compat_verdict`) answers empty for an
  # unreadable document and falls through to the initial COMPATIBLE above --
  # which is how an absent, truncated, non-object or `{}` report published
  # "No binary ABI break detected" for a run that established nothing. A
  # missing report *source* is not this case (dry-run and the no-JSON-output
  # shapes reach it legitimately, and `_report_validity` prints nothing for
  # it); a source that exists and does not parse is.
  # A JSON report the caller explicitly asked for, that is absent or carries no
  # result, is an operational failure of this step -- checked before any branch
  # below can default to COMPATIBLE. Every remaining reader here
  # (`no_baseline_audit`, `_report_compat_verdict`) answers empty for such a
  # document and falls through to the initial COMPATIBLE above, which is how a
  # truncated, non-object or `{}` report.json published "No binary ABI break
  # detected" for a run whose result nothing had read.
  if _json_report_expected; then
    _validity=$(_report_validity)
    if [[ -z "$_validity" ]]; then
      # No source to classify at all: the "died between the exit code and the
      # report" shape, which no valid-JSON fixture can stand in for and which
      # is the likeliest of these to occur in practice.
      VERDICT="REPORT_UNREADABLE"
      echo "::error::abicheck exited 0, but the JSON report this run requested is missing or empty -- so nothing read this run's result, and this step will not report one. Check for a killed step, a full disk, or an output path another process removed or truncated."
      return
    fi
    if [[ "$_validity" == "empty" ]]; then
      # `{}` is a *known* artifact rather than a corruption -- a PR-comment
      # re-run can leave one behind when the primary run wrote no report -- so
      # it gets its own message rather than being lumped in below. It is still
      # not a result.
      VERDICT="REPORT_UNREADABLE"
      echo "::error::abicheck exited 0, but the JSON report requested via format: json is an empty object, carrying no verdict, no findings and no gate -- so nothing read this run's result. This usually means the report was never written and a placeholder was read in its place."
      return
    fi
    if [[ "$_validity" == "no_result" ]]; then
      # Parsed, non-empty, and still carries no abicheck result -- a
      # `{"error": ...}` a wrapper wrote, or a lone `report_schema_version`
      # from an interrupted write. Its own message because "could not be read"
      # would misdescribe a document that read perfectly well and simply
      # answers nothing (Codex review, P2).
      VERDICT="REPORT_UNREADABLE"
      echo "::error::abicheck exited 0, and the JSON report requested via format: json parsed cleanly but carries no abicheck result -- no verdict, no run_outcome, no findings. So nothing read this run's result, and this step will not report one. Check whether another tool wrote to that path, or whether the write was interrupted."
      return
    fi
    if [[ "$_validity" != "ok" ]]; then
      VERDICT="REPORT_UNREADABLE"
      echo "::error::abicheck exited 0, but the JSON report this run requested could not be read ($_validity) -- so nothing read this run's result, and this step will not report one. Check for a failed/killed step, a full disk, or an output path another process overwrote."
      return
    fi
  fi
  # A readable report whose assurance key pair is broken, checked HERE rather
  # than only at the FINAL_EXIT fold below (Codex review, P2, reproduced):
  # that fold runs after the verdict output, the job summary and the PR comment
  # have all been published, so the contradiction failed the step while
  # publishing `verdict=COMPATIBLE` and "No binary ABI break detected" -- exit 1
  # beside a false clean result, which a workflow branching on the output (or
  # using continue-on-error) reads as a pass. The whole point of this axis is
  # that an unreportable assurance result must not read as a passing one, so it
  # has to be decided before anything is emitted. The FINAL_EXIT check stays as
  # the non-exit-0 path's cover; at exit 0 it is now redundant, which is the
  # correct direction for a gate.
  if _assurance_axis_contradictory; then
    VERDICT="REPORT_UNREADABLE"
    echo "::error::abicheck exited 0, but its JSON report claims a schema version carrying analysis_assurance_exit_contribution and omits it while reporting an analysis_assurance block -- so whether the analysis-assurance gate fired cannot be established from it. That is an invalid report, not a passing assurance check, and this step will not report a compatibility result from it."
    return
  fi
  _no_baseline_audit=$(_report_query "$(_json_report_src)" no_baseline_audit)
  if [[ "$_no_baseline_audit" == "clean" ]]; then
    VERDICT="AUDIT_CLEAN"
    return
  elif [[ "$_no_baseline_audit" == "findings" ]]; then
    VERDICT="AUDIT_RISK"
    return
  fi
  _v=$(_report_compat_verdict)
  if [[ "$_v" == "BREAKING" || "$_v" == "API_BREAK" ]]; then
    VERDICT="$_v"
    ADVISORY_BREAK=true
    echo "::notice::abicheck reports $_v, but the configured severity policy resolved this run to exit 0 — the step is not failed. Raise the category to \`error\` to gate on it."
  elif [[ "$_v" == "COMPATIBLE_WITH_RISK" ]]; then
    # R1 (CLI-audit): exit 0 was previously hard-mapped to VERDICT=COMPATIBLE
    # unconditionally, only escalating when the report said BREAKING/
    # API_BREAK -- so a report the CLI itself classified
    # COMPATIBLE_WITH_RISK (a real, gate-worthy tier the CLI's own exit-code
    # doc names alongside COMPATIBLE/NO_CHANGE as "0 = compatible") still
    # published `verdict: COMPATIBLE` and a "No binary ABI break detected"
    # summary, silently dropping every risk finding from the Action's own
    # output even though the JSON report carried them in full. This is not
    # an advisory *break* the severity policy demoted (ADVISORY_BREAK stays
    # false: nothing here is gated by fail-on-breaking/fail-on-api-break,
    # which never match this tier), just a verdict the exit-0 branch must
    # not silently launder into a plain COMPATIBLE.
    VERDICT="$_v"
  fi
}

# Compatibility tiers, most severe last. Only these four are ranked: every
# other verdict (ERROR, BUDGET_OVERFLOW, SEVERITY_ERROR, ...) is a different
# axis and must never be escalated away by this comparison.
_verdict_rank() {
  case "$1" in
    BREAKING) echo 4 ;;
    API_BREAK) echo 3 ;;
    COMPATIBLE_WITH_RISK) echo 2 ;;
    COMPATIBLE) echo 1 ;;
    *) echo 0 ;;
  esac
}

# Why the step was blocked, when that is not what the verdict says.
#
# Two ways they diverge. A severity category configured as `error` gates at
# exit 1 and 2 alike, so it can be the real cause behind an API_BREAK or
# BREAKING verdict and fails the step regardless of the fail-on flags. And
# since `_escalate_verdict_to_report` publishes the report's (more severe)
# verdict while GATE_TIER keeps the tier that gated, an escalated verdict
# names a break that is *not* why the run failed. Both branches emit this,
# rather than one keeping its own copy -- the API_BREAK branch had the note
# and BREAKING did not, which is exactly how escalation produced a failing
# summary that mentioned only the ABI break.
# Accepted under the default `--on-incomplete-scope warn` (Codex review):
# nothing gated, but the verdict covers the compared members only, so the
# summary names the gap -- on a push, or with PR comments off, it is the only
# UI. Prints nothing for a complete scope or one `_scope_gated` already noted.
_scope_accepted_note() {
  _scope_incomplete || return 0
  _scope_gated && return 0
  local _scope_accepted_where
  _scope_accepted_where=$(_report_query "$(_json_report_src)" scope_where 2>/dev/null || true)
  echo ">"
  echo "> ℹ️ The comparison scope was **not fully checked** (ADR-065), accepted under \`scope.on_incomplete: warn\` (the default)${_scope_accepted_where:+: \`$_scope_accepted_where\`}. The compatibility verdict above covers the compared members only — see \`comparison_scope\` in the JSON report."
}

_blocking_gate_note() {
  local _cats
  _cats=$(_severity_gate_categories | tr ',' '\n' \
    | sed 's/^ *//;s/ *$//' | grep -v '^promoted_crosscheck$' | grep -v '^$' | paste -sd, -)
  if [[ -n "$_cats" ]]; then
    echo ">"
    if [[ "${GATE_TIER:-$VERDICT}" == "SEVERITY_ERROR" ]]; then
      echo "> ⚠️ Also blocked by severity policy: \`$_cats\` configured as \`error\`. This fails the step independently of \`fail-on-breaking\`/\`fail-on-api-break\`."
    else
      # Only the SEVERITY_ERROR tier bypasses the fail-on flags. At the
      # API_BREAK/BREAKING tiers the severity policy is what produced the
      # exit, but whether the *step* fails still follows those flags -- the
      # unconditional claim was wrong for two of the three tiers (CodeRabbit).
      echo "> ⚠️ Also blocked by severity policy: \`$_cats\` configured as \`error\`, which is what produced exit ${ABICHECK_EXIT}. Whether this step fails still follows \`fail-on-breaking\`/\`fail-on-api-break\` for the \`${GATE_TIER:-$VERDICT}\` tier."
    fi
  fi
  # The coverage axis is orthogonal, so it is reported on its own terms rather
  # than only when it happens to own GATE_TIER: with both axes firing at exit 1
  # the severity tier wins the slot and the missing provider went unmentioned
  # entirely (Codex).
  if _coverage_gated && [[ "$GATE_TIER" != "COVERAGE_INCOMPLETE" ]]; then
    echo ">"
    echo "> ⚠️ Contract coverage also contributed to this run's exit$(_coverage_where_suffix). Orthogonal to the compatibility verdict and to the severity policy — see \`contract_coverage_failures\` in the JSON report."
  fi
  # P0.4's analysis-assurance axis, mirroring the coverage block immediately
  # above and for the identical reason (Codex review): orthogonal, so it is
  # reported on its own terms rather than only when it happens to own
  # GATE_TIER -- with a coincident severity/coverage tier winning the slot,
  # the assurance gap would otherwise go unmentioned entirely.
  if _assurance_gated && [[ "$GATE_TIER" != "ANALYSIS_INCOMPLETE" ]]; then
    echo ">"
    echo "> ⚠️ Analysis assurance also contributed to this run's exit. Orthogonal to the compatibility verdict and to the severity policy — see \`analysis_assurance\` in the JSON report."
  fi
  # ADR-065 S2's completeness axis, mirroring the two blocks above for the
  # identical reason: orthogonal, so it is reported on its own terms.
  if _scope_gated && [[ "$GATE_TIER" != "SCOPE_INCOMPLETE" ]]; then
    echo ">"
    echo "> ⚠️ The comparison scope also contributed to this run's exit (ADR-065: an unchecked selected member under scope.on_incomplete: block, or no comparison completed). Orthogonal to the compatibility verdict and to the severity policy — see \`comparison_scope\` in the JSON report."
  else
    _scope_accepted_note
  fi
  [[ -n "$GATE_TIER" && "$GATE_TIER" != "$VERDICT" ]] || return 0
  echo ">"
  if [[ "$GATE_TIER" == "COVERAGE_INCOMPLETE" ]]; then
    # ADR-049's coverage axis is *orthogonal*: it never rewrites a
    # compatibility verdict or a gate contribution, and calling it a severity
    # failure is exactly the confusion the axis exists to avoid. Escalation
    # also displaces the COVERAGE_INCOMPLETE summary branch, taking its
    # missing-provider explanation with it -- so render that here rather than
    # leave the reader with a bare tier name (Codex review).
    echo "> ℹ️ Verdict escalated from the report: the compatibility finding above was demoted by the severity policy, and what actually produced this run's exit ${ABICHECK_EXIT} is the orthogonal contract-coverage axis$(_coverage_where_suffix). That is **not** an ABI/API break and **not** a severity-policy failure -- the compatibility verdict is unchanged. Supply the missing evidence, or accept incomplete assurance with \`contract.unresolved: warn\`."
  elif [[ "$GATE_TIER" == "SCOPE_INCOMPLETE" ]]; then
    # ADR-065's completeness axis, same orthogonal-axis shape as the two
    # branches around it.
    echo "> ℹ️ Verdict escalated from the report: the compatibility finding above was demoted by the severity policy, and what actually produced this run's exit ${ABICHECK_EXIT} is the orthogonal completeness axis (ADR-065: an unchecked selected member under scope.on_incomplete: block, or no comparison completed). That is **not** an ABI/API break and **not** a severity-policy failure -- the compatibility verdict covers the compared members only. See \`comparison_scope\` in the JSON report."
  elif [[ "$GATE_TIER" == "ANALYSIS_INCOMPLETE" ]]; then
    # P0.4's assurance axis, mirroring the COVERAGE_INCOMPLETE branch
    # immediately above -- same orthogonal-axis shape, different evidence
    # question (completeness of this run's own evidence, not closure of a
    # selected --contract domain).
    echo "> ℹ️ Verdict escalated from the report: the compatibility finding above was demoted by the severity policy, and what actually produced this run's exit ${ABICHECK_EXIT} is the orthogonal analysis-assurance axis. That is **not** an ABI/API break and **not** a severity-policy failure -- the compatibility verdict is unchanged. Set assurance.require_complete: false (or omit it) in .abicheck.yml to accept incomplete assurance, or see \`analysis_assurance\` in the JSON report for what fell short."
  elif [[ -z "$_cats" ]] && _severity_gate_categories | grep -q 'promoted_crosscheck'; then
    # A promoted `--crosscheck KEY=error` raises the published gate the same
    # way a severity category does, but it is not one: `_severity_gate_
    # categories` filters the pseudo-category out of `$_cats` above, and the
    # final gate deliberately leaves it subject to `fail-on-api-break` rather
    # than blocking unconditionally. Naming the severity policy here pointed
    # the reader at a mechanism that did not fire, and at a knob that would
    # not change the outcome (Codex review).
    echo "> ℹ️ Verdict escalated from the report: a promoted \`--crosscheck\` gated this run at \`$GATE_TIER\` (exit ${ABICHECK_EXIT}), so that tier -- not the verdict above -- is what \`fail-on-*\` applies to. This is a cross-check promotion, not a severity category: it follows \`fail-on-api-break\`."
  else
    echo "> ℹ️ Verdict escalated from the report: the severity policy gated this run at \`$GATE_TIER\` (exit ${ABICHECK_EXIT}), so that tier -- not the verdict above -- is what \`fail-on-*\` applies to."
  fi
}

# `: \`old/export_table\`` when the report names which provider fell short,
# empty otherwise. Shared so the COVERAGE_INCOMPLETE verdict branch and the
# escalated-verdict note above cannot drift into describing the same axis
# differently -- the escalated path silently lost this detail entirely.
_coverage_where_suffix() {
  local _where
  _where=$(_report_query "$(_json_report_src)" coverage_where)
  [[ -n "$_where" ]] && printf ': `%s`' "$_where"
}

# Exit 0 is not the only exit a severity policy can understate, which is what
# `_resolve_clean_exit_verdict` above fixed for exit 0 alone. A policy that
# demotes `abi_breaking` below `error` while something else still gates --
# an error-level `--crosscheck KEY=error`, or `potential_breaking: error` --
# exits 2 with a report that still says BREAKING. Mapping exit 2 straight to
# API_BREAK then published a source-level break for a binary ABI break
# (Codex review).
#
# So the published verdict follows the report whenever the report is the more
# severe of the two. The *gate* deliberately does not move with it: GATE_TIER
# keeps the tier the exit code actually gated at, because the severity policy
# switching a break's gate off is precisely what the user asked for -- letting
# an escalated BREAKING verdict reach `fail-on-breaking` (default true) would
# re-gate the very finding the policy demoted. Truth in the output, the user's
# policy in the gate.
_escalate_verdict_to_report() {
  local _v
  _v=$(_report_compat_verdict)
  # Only a *break* may escalate. This exists to stop the published verdict
  # understating what was detected, so a COMPATIBLE report is never an
  # escalation over anything -- without this guard it outranked the
  # non-compatibility verdicts (COVERAGE_INCOMPLETE, SEVERITY_ERROR) and
  # overwrote them with COMPATIBLE, which is the opposite of the point.
  [[ "$_v" == "BREAKING" || "$_v" == "API_BREAK" ]] || return 0
  if (( $(_verdict_rank "$_v") > $(_verdict_rank "$VERDICT") )); then
    echo "::notice::abicheck's report records $_v while the severity policy resolved this run to exit ${ABICHECK_EXIT} (gated as ${VERDICT}); publishing the report's verdict. The step still gates at ${VERDICT}."
    GATE_TIER="$VERDICT"
    VERDICT="$_v"
  fi
}

if [[ "$MODE" == "deps-compare" ]]; then
  # deps-compare exit codes: 0=PASS, 1=WARN, 4=FAIL
  if _is_cli_error; then
    VERDICT="ERROR"
    echo "::error::abicheck deps-compare failed due to a CLI error (exit code $ABICHECK_EXIT)."
  else
    case $ABICHECK_EXIT in
      0) VERDICT="PASS" ;;
      1) VERDICT="WARN" ;;
      4) VERDICT="FAIL" ;;
      *) VERDICT="ERROR" ;;
    esac
  fi

elif [[ "$MODE" == "deps-tree" ]]; then
  # deps-tree exit codes: 0=OK, 1=missing deps/symbols
  if _is_cli_error; then
    VERDICT="ERROR"
    echo "::error::abicheck deps-tree failed due to a CLI error (exit code $ABICHECK_EXIT)."
  else
    case $ABICHECK_EXIT in
      0) VERDICT="PASS" ;;
      1) VERDICT="FAIL" ;;
      *) VERDICT="ERROR" ;;
    esac
  fi

elif [[ "$MODE" == "dump" ]]; then
  # dump exit codes: 0=success, anything else=error.
  # dump never produces API_BREAK/BREAKING/SEVERITY_ERROR verdicts.
  if [[ $ABICHECK_EXIT -eq 0 ]]; then
    VERDICT="COMPATIBLE"
  else
    VERDICT="ERROR"
    if _is_cli_error; then
      echo "::error::abicheck dump failed due to a CLI argument or configuration error (exit code $ABICHECK_EXIT)."
    else
      echo "::error::abicheck dump failed (exit code $ABICHECK_EXIT)."
    fi
  fi

else
  # compare exit codes: 0=compatible, 1=severity error, 2=API_BREAK,
  # 3=AUDIT_GATE (ADR-068 2026-09-10 amendment, `--no-baseline`'s own
  # orthogonal audit-gate axis -- see the arm below), 4=BREAKING,
  # 8=REMOVED_LIBRARY (directory/package operands with
  # fail-on-removed-library set), 16=NOT_COMPARABLE (a scope/profile
  # mismatch -- `comparability.py`, `cli_compare_helpers.py`'s single-pair
  # path (baseline and audit-only shapes alike) and `cli_compare_release_
  # helpers.py`'s release fan-out all use this code -- Codex review P2, PR
  # #1160, four rounds: the 16 arm was missing entirely at one point, so a
  # request hitting it fell into the generic `*) VERDICT="ERROR"` case
  # below, which made `_maybe_post_pr_comment`'s own ERROR guard skip
  # posting the report). Click also uses exit code 2 for
  # usage/argument errors — detect via stderr.
  if [[ $ABICHECK_EXIT -eq 2 ]] && echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try )'; then
    VERDICT="ERROR"
    echo "::error::abicheck failed due to a CLI argument or configuration error (exit code 2)."
    echo "::error::Check the command and inputs above. This is NOT an API break — the check did not run."
  else
    case $ABICHECK_EXIT in
      0) _resolve_clean_exit_verdict ;;
      1)
        if _is_cli_error; then
          VERDICT="ERROR"
          echo "::error::abicheck failed due to a CLI argument or configuration error (exit code 1)."
          echo "::error::Check the command and inputs above."
        elif _coverage_gated || _assurance_gated || _scope_gated; then
          # `compare` shares exit 1 between up to four independent axes
          # (severity policy, ADR-049 contract coverage, P0.4 analysis
          # assurance), so the report's pre-fold `severity.exit_code` is
          # what tells them apart rather than a guess. Only when the
          # severity gate itself did not produce 1 is this run gated by
          # coverage and/or assurance *alone*.
          _sev_exit=$(_severity_gate_exit)
          if [[ "$_sev_exit" == "0" ]]; then
            if _coverage_gated; then
              VERDICT="COVERAGE_INCOMPLETE"
              echo "::warning::abicheck could not close the selected contract domain on the available evidence (exit code 1). This is NOT an ABI/API break and NOT a severity-policy failure — the compatibility verdict is unchanged."
              if _assurance_gated; then
                echo "::warning::abicheck also reports incomplete analysis assurance under assurance.require_complete; see analysis_assurance in the JSON report."
              fi
              if _scope_gated; then
                echo "::warning::abicheck also reports an incompletely checked comparison scope (ADR-065); see comparison_scope in the JSON report."
              fi
            elif _scope_gated; then
              # ADR-065 S2's completeness axis alone: an unchecked selected
              # member under --on-incomplete-scope block, or a run that
              # completed no comparison at all (D7, under every setting).
              # Same "not a break, not a severity-policy failure" shape as
              # the coverage branch above.
              VERDICT="SCOPE_INCOMPLETE"
              echo "::warning::abicheck's comparison scope was not fully checked (exit code 1): a selected member went unchecked under scope.on_incomplete: block, or no comparison completed at all. This is NOT an ABI/API break and NOT a severity-policy failure — the compatibility verdict covers the compared members only; see comparison_scope in the JSON report."
              if _assurance_gated; then
                echo "::warning::abicheck also reports incomplete analysis assurance under assurance.require_complete; see analysis_assurance in the JSON report."
              fi
            else
              # P0.4's orthogonal analysis-assurance axis alone (no
              # contract-coverage gap this run) -- same "not a break, not a
              # severity-policy failure" shape as the coverage branch above.
              VERDICT="ANALYSIS_INCOMPLETE"
              echo "::warning::abicheck's own evidence was not fully complete under assurance.require_complete (exit code 1). This is NOT an ABI/API break and NOT a severity-policy failure — the compatibility verdict is unchanged; see analysis_assurance in the JSON report for what fell short."
            fi
          else
            # Either severity gated too, or there is no readable JSON report
            # to tell. Keep the established verdict rather than overwrite it
            # on a guess, and say that coverage/assurance also contributed.
            VERDICT="SEVERITY_ERROR"
            if _coverage_gated; then
              echo "::warning::abicheck also reports incomplete contract coverage for the selected --contract domain; see contract_coverage_failures in the JSON report."
            fi
            if _assurance_gated; then
              echo "::warning::abicheck also reports incomplete analysis assurance under assurance.require_complete; see analysis_assurance in the JSON report."
            fi
            if _scope_gated; then
              echo "::warning::abicheck also reports an incompletely checked comparison scope (ADR-065); see comparison_scope in the JSON report."
            fi
          fi
        elif [[ "${_NO_BASELINE:-false}" == "true" ]]; then
          # The audit-only shape (`compare --no-baseline`) has no bare
          # severity-driven exit-1 source at all (its own four orthogonal
          # axes are audit_gate=3, contract_coverage=1, analysis_assurance=1,
          # evidence_contract=7 -- never a bare severity-gate 1,
          # `docs/reference/exit-codes.md`'s `compare --no-baseline`
          # section), unlike a two-sided compare's legacy severity scheme,
          # which has no OTHER source for exit 1 at all (the final `else`
          # below). So this reads the report's own pre-fold
          # `severity.exit_code`/`run_outcome.gate` directly rather than
          # assuming severity the way the two-sided catch-all below does: a
          # real signal still means SEVERITY_ERROR, but "not a CLI error,
          # and none of severity/coverage/assurance/scope claim it" is an
          # unattributed operational failure -- ERROR, not a severity-policy
          # guess an audit-only run could never actually produce.
          _sev_exit=$(_severity_gate_exit)
          if [[ "$_sev_exit" != "0" && -n "$_sev_exit" ]]; then
            VERDICT="SEVERITY_ERROR"
          else
            VERDICT="ERROR"
          fi
        else
          VERDICT="SEVERITY_ERROR"
        fi
        if [[ "$VERDICT" != "ERROR" ]]; then
          _escalate_verdict_to_report
        fi
        ;;
      2) VERDICT="API_BREAK"; _escalate_verdict_to_report ;;
      3)
        # ADR-068's 2026-09-10 amendment: `compare --no-baseline`'s own
        # orthogonal audit-gate axis (`policy/audit_gate_exit.py`). Exit `3`
        # is unambiguous -- it is the one code this axis alone contributes
        # (never folded together with another axis's own contribution the
        # way exit `1` is shared between severity/coverage/assurance/scope
        # above), so no report-based disambiguation is needed here. This
        # arm is reached through the audit-only shape (old-library/
        # abi-baseline both omitted) whenever the caller also opted in via
        # `--severity-preset` (any value but `info-only`) -- the caller's
        # own migration step per ADR-068's 2026-09-11 amendment, not
        # something this file injects.
        # This is NOT a two-sided compatibility verdict: an audit reports no
        # `changes`/compatibility verdict at all (ADR-068 D2) -- the axis
        # only says a real, BREAKING/API_BREAK-classified candidate-side
        # finding was present against the candidate's own public surface
        # while a severity preset (other than `info-only`) was in effect.
        VERDICT="AUDIT_GATE"
        echo "::warning::abicheck --no-baseline reports a gating audit finding (exit code 3): a real BREAKING/API_BREAK-classified finding was found against the candidate's own public surface while a severity preset was in effect. This is NOT a two-sided compatibility verdict — no baseline was compared; see the JSON report's findings[] for what gated."
        ;;
      4)
        # A release/bundle-facts run floors its exit at 4 for an operational
        # failure too (a library that failed to extract or compare, ADR-065
        # D1) -- `run_outcome.operational` names it. That is not (only) a
        # compatibility break and must not be waived by fail-on-breaking:
        # false (Codex review), so it takes the non-waivable ERROR path.
        if _op=$(_operational_failure_status); then
          VERDICT="ERROR"
          echo "::error::abicheck reports an operational failure (run_outcome.operational: $_op): at least one library failed to extract or compare, so exit code 4 is not a plain compatibility verdict and is not waived by fail-on-breaking. See the JSON report's per-library results / extraction_failures."
        else
          VERDICT="BREAKING"
        fi
        ;;
      7)
        # ADR-037 D5's evidence-contract axis (`policy/exit_decision_
        # precedence.py`'s `EXIT_EVIDENCE_CONTRACT_ERROR`, ADR-064): a
        # pinned `--depth build`/`--depth source` whose live extraction did
        # not reach it -- unconditional and un-spoofable, shared by
        # `compare` (a two-sided run) and `compare --no-baseline` alike
        # (`report/no_baseline.py`'s own `evidence_contract` axis).
        # Before this arm existed, this exit code fell into the generic
        # `*) VERDICT="ERROR"` case below -- which does still fail the step
        # via the top-level `VERDICT == "ERROR"` branch further down, so
        # this arm changes the label, not whether the step fails.
        VERDICT="EVIDENCE_CONTRACT_ERROR"
        echo "::error::abicheck aborted: this run's evidence contract could not be satisfied (ADR-037 D5, exit code 7). This is NOT a CLI usage error and NOT an ABI/API break — see the command's own error message above for the exact cause (e.g. a pinned --depth/--source-method needing source evidence that was never collected)."
        ;;
      5)
        # ADR-068 §3 #19's budget-overflow abort (`--budget`,
        # `cli_compare_fold.py`'s own `sys.exit(5)`). Reachable for a
        # baseline compare's own `--budget` forwarding (this file's own
        # compare-command assembly, gated on `_NO_BASELINE`) -- before this
        # arm existed, exit 5 fell into the generic `*) VERDICT="ERROR"`
        # case below, publishing `ERROR` instead of the documented
        # `BUDGET_OVERFLOW` and skipping the budget-specific summary/comment
        # handling (Codex review, PR #1210, round 10 -- an audit-only
        # request never reaches this exit code at all: `compare
        # --no-baseline` rejects `--budget` outright, and this Action's own
        # preflight rejects the input upfront for that shape before ever
        # invoking the CLI).
        VERDICT="BUDGET_OVERFLOW"
        echo "::warning::abicheck exceeded the configured --budget (exit code 5). Pin a shallower --depth or raise the budget; a budget never silently shrinks scope."
        ;;
      8) VERDICT="REMOVED_LIBRARY" ;;
      16) VERDICT="NOT_COMPARABLE" ;;
      *) VERDICT="ERROR" ;;
    esac
  fi
fi

echo "abicheck verdict: $VERDICT (exit code $ABICHECK_EXIT)"

# Whether `format: sarif` + `upload-sarif: true` was requested but the
# *effective* format (an `extra-args --format` override) isn't sarif -- see
# the `report-path` output block below for the full rationale. Computed
# once, outside the `{ ... } >> "$GITHUB_OUTPUT"` redirect: a workflow-command
# annotation (`::warning::`) echoed *inside* that block would be silently
# swallowed into the environment file as a bogus, undeclared record instead
# of reaching the Actions log -- exactly the fate this diagnostic exists to
# avoid for the upload it explains (Codex review, PR #998, fresh evidence).
_SARIF_UPLOAD_FORMAT_MISMATCH=0
if [[ "${FORMAT:-}" == "sarif" && "${INPUT_UPLOAD_SARIF:-false}" == "true" \
   && "${_EFFECTIVE_FORMAT:-$FORMAT}" != "sarif" ]]; then
  _SARIF_UPLOAD_FORMAT_MISMATCH=1
  echo "::warning title=abicheck upload-sarif::format: sarif and upload-sarif: true were requested, but extra-args overrode --format away from sarif, so the real output is not SARIF. Skipping the SARIF upload (report-path withheld) rather than uploading mismatched content."
fi

# ---------------------------------------------------------------------------
# Set outputs
# ---------------------------------------------------------------------------
{
  echo "verdict=$VERDICT"
  echo "exit-code=$ABICHECK_EXIT"
  # Only emit report-path when a real report file was produced.
  #
  # Withheld even when one exists whenever the sarif/upload-sarif mismatch
  # above was detected -- this is action.yml's own upload-sarif step's
  # entire gate (`if: ... && steps.run-abicheck.outputs.report-path != ''`),
  # and that step's `if:` reads the Action's nominal `format` input, which a
  # shell-local `$_EFFECTIVE_FORMAT` cannot change: this closes both the
  # default-output-path case and an explicit `output-file:` case alike,
  # since either way `$OUTPUT_FILE` would hold real, non-SARIF content that
  # must never reach that step. No other output/behavior changes --
  # `report-path` for any other purpose than gating that one step is
  # unaffected.
  if [[ "$_SARIF_UPLOAD_FORMAT_MISMATCH" == "1" ]]; then
    echo "report-path="
  elif [[ -n "${OUTPUT_FILE:-}" && -f "${OUTPUT_FILE}" ]]; then
    echo "report-path=${OUTPUT_FILE}"
  else
    echo "report-path="
  fi
} >> "$GITHUB_OUTPUT"

# ---------------------------------------------------------------------------
# Job Summary
# ---------------------------------------------------------------------------
if [[ "${INPUT_ADD_JOB_SUMMARY:-true}" == "true" && "$MODE" != "dump" ]]; then
  {
    # An audit-only run (compare --no-baseline) has no baseline and reports
    # no compatibility verdict at all -- the unconditional "ABI
    # Compatibility Report" heading recreated exactly the unsupported
    # compatibility claim the AUDIT_CLEAN/AUDIT_RISK verdict text below was
    # written to avoid (Codex review, fresh evidence).
    if [[ "$MODE" == "compare" && "${_NO_BASELINE:-false}" == "true" ]]; then
      echo "## abicheck ABI Audit Report"
    else
      echo "## abicheck ABI Compatibility Report"
    fi
    echo ""

    case $VERDICT in
      COMPATIBLE)
        echo "> **Verdict: COMPATIBLE** — No binary ABI break detected."
        _scope_accepted_note
        ;;
      COMPATIBLE_WITH_RISK)
        # R1 follow-up (Codex review, PR #1016): VERDICT can carry this tier
        # since _resolve_clean_exit_verdict stopped laundering it into plain
        # COMPATIBLE, but this dispatch had no matching arm -- a bash `case`
        # with no match and no `*)` default silently omits the whole banner,
        # so `add-job-summary: true` published a summary with every finding
        # table but no verdict line at all for this tier.
        echo "> **Verdict: COMPATIBLE_WITH_RISK** ⚠️ — Binary-compatible, but carries deployment risk; review advised (see findings below)."
        _scope_accepted_note
        ;;
      SEVERITY_ERROR)
        # SEVERITY_ERROR (exit code 1) means a severity-config category is
        # gating the check — it does NOT mean the checker found an ABI/API
        # break (that's BREAKING/API_BREAK above, different exit codes).
        # e.g. `severity-addition: error` blocks CI on a COMPATIBLE new
        # public API entry; naming the category here (via the JSON report's
        # `severity.blocking_categories`, ADR-042) tells the reader that up
        # front instead of leaving a bare "severity-level issue" that reads
        # like an unspecified break. Best-effort, and checks two possible
        # JSON sources: the primary output when FORMAT=json, or (the common
        # case: default FORMAT=markdown with PR comments on) $PR_JSON — the
        # always-unfiltered secondary JSON report the compare-mode command
        # setup above already asks the same abicheck invocation to write via
        # --write, so it's already populated
        # by this point without a second run (Codex review). Falls back to
        # the generic message when no report is readable. `_severity_gate_
        # categories` is JSON-only (ADR-063 Track T8) -- scan's own PR_JSON
        # sidecar injection is unconditional as of that same track (not
        # gated on `pr-comment`), which is what keeps this readable on the
        # default `format: text` invocation this comment used to have to
        # special-case a text fallback for.
        _blocking_categories=$(_severity_gate_categories)
        if [[ -n "$_blocking_categories" ]]; then
          echo "> **Verdict: SEVERITY_ERROR** ⚠️ — Blocked by severity policy: \`$_blocking_categories\` configured as \`error\`. This is a policy gate, not necessarily an ABI/API break — see the report below for each finding's actual compatibility."
        else
          echo "> **Verdict: SEVERITY_ERROR** ⚠️ — Severity-level issue detected (see severity configuration)."
        fi
        ;;
      API_BREAK)
        echo "> **Verdict: API_BREAK** — Source-level API break detected. Recompilation required."
        _blocking_gate_note
        if [[ "$ADVISORY_BREAK" == "true" ]]; then
          echo ">"
          echo "> ℹ️ Reported, not gated: the configured severity policy resolved this run to exit 0, so the step is **not** failed. Raise the blocking category to \`error\` to gate on it."
        fi
        ;;
      BREAKING)
        echo "> **Verdict: BREAKING** — Binary ABI break detected. Existing binaries will fail at runtime."
        # A BREAKING verdict is reachable by *escalation* now, in which case
        # the tier that actually blocked the step is not this one -- exit 1's
        # severity gate or exit 2's API tier. Without this the summary read
        # "Binary ABI break detected" on a step failed by an addition gate
        # (Codex review).
        _blocking_gate_note
        # The mirror, for the opposite direction: there, a gate failed a run
        # the fail-on flag would have let pass; here, the severity policy
        # resolved a real break to exit 0 and the step is green. Both are
        # cases where the step's outcome does not follow from the verdict
        # alone, and both have to say so.
        if [[ "$ADVISORY_BREAK" == "true" ]]; then
          echo ">"
          echo "> ℹ️ Reported, not gated: the configured severity policy resolved this run to exit 0, so the step is **not** failed. Raise the blocking category to \`error\` to gate on it."
        fi
        ;;
      REMOVED_LIBRARY)
        echo "> **Verdict: REMOVED_LIBRARY** — A library present in the old package is missing from the new package."
        ;;
      BUDGET_OVERFLOW)
        echo "> **Verdict: BUDGET_OVERFLOW** ⏱️ — Scan exceeded the configured \`budget\`. Pin a shallower level (--depth) or raise the budget; a budget never silently shrinks scope."
        ;;
      EVIDENCE_CONTRACT_ERROR)
        # Generic wording -- see the exit-1 dispatch's own comment on why
        # (two independent _EvidenceContractError raise sites, only one of
        # which is about a pinned depth/missing evidence).
        echo "> **Verdict: EVIDENCE_CONTRACT_ERROR** 🛑 — This scan's evidence contract could not be satisfied (ADR-037 D5). This is not a CLI usage error and not an ABI/API break; see the command's own error message above for the exact cause and remedy (e.g. supplying \`--sources\`/\`--build-info\` or dropping a \`--depth\` pin, or reconsidering an \`--abi3\` target that isn't a recognisable CPython extension module)."
        ;;
      NOT_COMPARABLE)
        echo "> **Verdict: NOT_COMPARABLE** 🛑 — The candidate and \`--against\` baseline were not extracted under a comparable profile/scope contract (ADR-050 D2), so no compatibility comparison ran. This is not an ABI/API break; see the JSON report's \`diff.reason\` for what mismatched."
        ;;
      AUDIT_GATE)
        # ADR-068's 2026-09-10 amendment: `compare --no-baseline`'s own
        # orthogonal audit-gate axis (exit code 3). This is an audit-only
        # run (no baseline was compared -- `mode: scan` with no
        # against/abi-baseline, or a direct `compare --no-baseline`) that
        # found a real, BREAKING/API_BREAK-classified finding against its
        # own candidate public surface while a severity preset (other than
        # \`info-only\`) was in effect, opting the run into gating. It is
        # explicitly **not** a two-sided compatibility verdict: no
        # additions/removals/compatibility verdict are ever reported by an
        # audit (ADR-068 D2) -- only the candidate-side findings themselves.
        echo "> **Verdict: AUDIT_GATE** 🛑 — A gating audit finding was detected against the candidate's own public surface (no baseline was compared). This is not a two-sided compatibility verdict; see the JSON report's \`findings[]\` for what gated. Pass \`severity-preset: info-only\` to stop gating on audit findings."
        ;;
      DRY_RUN)
        # An audit-only (no-baseline) dry run: `compare --dry-run` performs
        # no analysis and writes no report, so there is no candidate-side
        # finding (or absence of one) to claim either way -- distinct from
        # AUDIT_CLEAN, which asserts a real audit ran and found nothing.
        echo "> **Verdict: DRY_RUN** ℹ️ — This was a preview of the command that would run (\`--dry-run\`); no analysis was performed. This is not a compatibility verdict, and not AUDIT_CLEAN/AUDIT_RISK — see the command preview above for what a real run would do."
        ;;
      AUDIT_CLEAN)
        # Codex review, PR #1210, round 6: exit 0 on a no-baseline audit
        # with no candidate-side findings at all previously fell through to
        # the generic COMPATIBLE arm above ("No binary ABI break detected"),
        # which falsely implies a two-sided comparison ran. Distinct from
        # AUDIT_GATE the same way COMPATIBLE is distinct from BREAKING --
        # the exit-0 sibling of the same axis family.
        echo "> **Verdict: AUDIT_CLEAN** ✅ — No candidate-side finding against the candidate's own public surface (no baseline was compared). This is not a two-sided compatibility verdict — an audit reports no additions, removals, or compatibility verdict at all."
        ;;
      AUDIT_RISK)
        # Same audit-only shape as AUDIT_CLEAN above, but with a real
        # candidate-side finding that this run's own axes did not gate on
        # (no severity-preset opt-in, or a RISK-classified finding, which
        # policy/audit_gate_exit.py never gates regardless of preset) --
        # exit 0, but there is something to look at, unlike AUDIT_CLEAN.
        echo "> **Verdict: AUDIT_RISK** ⚠️ — A candidate-side finding was detected against the candidate's own public surface (no baseline was compared), but this run did not gate on it. This is not a two-sided compatibility verdict; see the JSON report's \`findings[]\`. Set \`severity-preset\` to a non-\`info-only\` value to gate on a future \`BREAKING\`/\`API_BREAK\`-classified finding like it."
        ;;
      COVERAGE_INCOMPLETE)
        # ADR-049's orthogonal contract-coverage axis (exit code 1). Naming
        # which provider fell short is the actionable part — "old/export_table"
        # tells the reader the old snapshot carries no export table, where a
        # bare "coverage incomplete" leaves them to go find out.
        _coverage_where=""
        _json_src=$(_json_report_src)
        _coverage_where=$(_report_query "$_json_src" coverage_where)
        if [[ -n "$_coverage_where" ]]; then
          echo "> **Verdict: COVERAGE_INCOMPLETE** ⚠️ — The selected \`--contract\` domain could not be closed on the available evidence: \`$_coverage_where\`. This is **not** an ABI/API break and **not** a severity-policy failure — the compatibility verdict is unchanged. Supply the missing evidence, or accept incomplete assurance with \`contract.unresolved: warn\` (which keeps the findings reported, and only zeroes this contribution)."
        else
          echo "> **Verdict: COVERAGE_INCOMPLETE** ⚠️ — The selected \`--contract\` domain could not be closed on the available evidence. This is **not** an ABI/API break and **not** a severity-policy failure — the compatibility verdict is unchanged. See \`contract_coverage_failures\` in the JSON report."
        fi
        ;;
      ANALYSIS_INCOMPLETE)
        # P0.4's orthogonal analysis-assurance axis (exit code 1,
        # --require-complete-analysis). `analysis_assurance.notes` names
        # what actually fell short (depth, TU/export accounting,
        # fact-set comparability, header-context drift, ...) the same way
        # `coverage_where` does for the contract-coverage sibling above.
        _assurance_notes=""
        _json_src=$(_json_report_src)
        _assurance_notes=$(_report_query "$_json_src" assurance_notes)
        if [[ -n "$_assurance_notes" ]]; then
          echo "> **Verdict: ANALYSIS_INCOMPLETE** ⚠️ — This run's own evidence was not fully complete: \`$_assurance_notes\`. This is **not** an ABI/API break and **not** a severity-policy failure — the compatibility verdict is unchanged. Set assurance.require_complete: false (or omit it) in .abicheck.yml to accept incomplete assurance, or see \`analysis_assurance\` in the JSON report for the full detail."
        else
          echo "> **Verdict: ANALYSIS_INCOMPLETE** ⚠️ — This run's own evidence was not fully complete. This is **not** an ABI/API break and **not** a severity-policy failure — the compatibility verdict is unchanged. See \`analysis_assurance\` in the JSON report."
        fi
        ;;
      SCOPE_INCOMPLETE)
        # ADR-065 S2's completeness axis (exit code 1). `comparison_scope`
        # names the unchecked members (or says no comparison completed), the
        # same way `coverage_where`/`assurance_notes` name what fell short
        # for their siblings above.
        _scope_where=""
        _json_src=$(_json_report_src)
        _scope_where=$(_report_query "$_json_src" scope_where)
        if [[ -n "$_scope_where" ]]; then
          echo "> **Verdict: SCOPE_INCOMPLETE** ⚠️ — The comparison scope was not fully checked: \`$_scope_where\` (ADR-065). This is **not** an ABI/API break and **not** a severity-policy failure — the compatibility verdict covers the compared members only. Supply the missing members, or accept an incompletely checked scope with \`scope.on_incomplete: warn\` (the default; a run that completed no comparison at all still fails)."
        else
          echo "> **Verdict: SCOPE_INCOMPLETE** ⚠️ — The comparison scope was not fully checked (ADR-065). This is **not** an ABI/API break and **not** a severity-policy failure — the compatibility verdict covers the compared members only. See \`comparison_scope\` in the JSON report."
        fi
        ;;
      PASS)
        echo "> **Verdict: PASS** — Binary loads and no harmful ABI changes detected."
        ;;
      WARN)
        echo "> **Verdict: WARN** ⚠️ — Binary loads but ABI risk detected in dependencies."
        ;;
      FAIL)
        echo "> **Verdict: FAIL** — Load failure or ABI break in dependency stack."
        ;;
      ERROR)
        echo "> **Verdict: ERROR** — abicheck encountered an error (exit code $ABICHECK_EXIT)."
        ;;
      REPORT_UNREADABLE)
        # An explicit arm rather than relying on the `case` falling through:
        # a bash `case` with no match prints nothing at all, which is exactly
        # how COMPATIBLE_WITH_RISK came to publish an empty summary (see that
        # arm's own comment above). A verdict that exists to say "no result
        # was established" must not be the one that renders as silence.
        echo "> **Verdict: REPORT_UNREADABLE** — abicheck exited 0 but produced no readable JSON report, so **no compatibility result was established**. This is not a pass; see the error annotation above."
        ;;
    esac

    echo ""
    echo "| Property | Value |"
    echo "|----------|-------|"
    if [[ "$MODE" == "compare" && "${_NO_BASELINE:-false}" == "true" ]]; then
      # Audit-only shape (old-library/abi-baseline both omitted): there is
      # no OLD side at all, so the Old/New pair below would be misleading --
      # show the single audited binary instead, the same shape legacy
      # `mode: scan`'s own audit-only summary row used.
      echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
      if [[ -n "${INPUT_SOURCES:-}" ]]; then
        echo "| Sources | \`${INPUT_SOURCES}\` |"
      fi
      printf '%s\n' "| Depth | $(_sanitize_annotation "${INPUT_DEPTH:-headers}") |"
    elif [[ "$MODE" == "compare" ]]; then
      echo "| Old | \`${INPUT_OLD_LIBRARY:-}\` (${INPUT_OLD_VERSION:-old}) |"
      echo "| New | \`${INPUT_NEW_LIBRARY:-}\` (${INPUT_NEW_VERSION:-new}) |"
      echo "| Policy | ${INPUT_POLICY:-strict_abi} |"
    elif [[ "$MODE" == "deps-compare" ]]; then
      echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
      echo "| Old root | \`${INPUT_OLD_ROOT:-}\` |"
      echo "| New root | \`${INPUT_NEW_ROOT:-}\` |"
    elif [[ "$MODE" == "deps-tree" ]]; then
      echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
    fi
    echo "| Mode | $MODE |"
    # The *effective* format (see `_effective_format`'s own docstring): an
    # `extra-args --format` override changes what the run actually produced,
    # and showing the nominal `format:` input here would mislabel the very
    # report rendered a few lines below (Codex review, PR #998, fresh
    # evidence).
    echo "| Format | ${_EFFECTIVE_FORMAT:-${FORMAT:-markdown}} |"
    if [[ -n "${OUTPUT_FILE:-}" ]]; then
      echo "| Report | \`${OUTPUT_FILE}\` |"
    fi
    echo ""

    # If output was captured (no output-file), include it in summary. A
    # markdown report is embedded as-is so GitHub renders its headings/
    # tables/bold text in the step summary, instead of being wrapped in a
    # code fence (which would make it display as literal ``` text). Every
    # other format (json/sarif/text/review/etc.) is genuinely verbatim
    # output, so it keeps the fence.
    #
    # Gated on the effective format too, for the same reason as the "Format"
    # row above: a `format: json` step overridden to `--format markdown` (or
    # the reverse) would otherwise embed the real output under the wrong
    # rendering rule.
    if [[ -n "$ABICHECK_OUTPUT" ]]; then
      echo "<details>"
      echo "<summary>Full report</summary>"
      echo ""
      if [[ "${_EFFECTIVE_FORMAT:-${FORMAT:-markdown}}" == "markdown" ]]; then
        echo "$ABICHECK_OUTPUT"
      else
        echo '```'
        echo "$ABICHECK_OUTPUT"
        echo '```'
      fi
      echo "</details>"
    fi
  } >> "$GITHUB_STEP_SUMMARY"
fi

# ---------------------------------------------------------------------------
# Sticky PR comment (content channel — never changes the red/green gate)
# ---------------------------------------------------------------------------
# Rebuild the run command with `--format json` so the comment renderer has a
# structured report, regardless of the format chosen for the main output.
_can_reuse_primary_json() {
  # Reuse the primary run's output as the comment's JSON report instead of
  # re-running the comparison — but only when it is a faithful, unfiltered
  # report. It must already be JSON, actually available somewhere
  # (_json_report_src, defined near the top of the script — it already
  # falls back from $OUTPUT_FILE through the stdout-mode $_STDOUT_JSON_FILE
  # to the run's own extra-args `--write json=PATH` sidecar; its middle
  # fallback, $PR_JSON, is always empty at this call site, since the caller
  # only reaches here after its own "already populated" check on PR_JSON
  # came back empty), and free of the --show-only display filter that hides
  # gated changes from the comment (which _build_json_cmd strips for
  # exactly that reason). --stat no longer exists as a CLI flag (CLI
  # cleanup phase two, PR 1) -- a $CMD array containing it would already
  # have failed the abicheck invocation itself before this script's
  # post-processing logic ever ran, so there is nothing left to check here.
  #
  # No blanket `$FORMAT == "json"` requirement (Codex review, fresh
  # evidence): a `format: text`/`markdown` primary run whose own extra-args
  # supplied `--write json=PATH` (the `_extra_write_json_path` branch
  # above) is exactly as faithful and unfiltered as a `format: json`
  # primary output — `_json_report_src` already only trusts that branch
  # when it names a real, fresh (fingerprint-checked) file, so there is
  # nothing left for this function to gate on beyond "did it find one at
  # all". Requiring `$FORMAT == "json"` on top of that rejected exactly
  # this faithful report and forced a full rerun instead — for the abi3
  # `_EvidenceContractError` raise site (unlike the pinned-depth one),
  # that rerun happens *after* candidate snapshot extraction, so it is not
  # the "cheap, precondition-only" rerun `_maybe_post_pr_comment`'s own
  # EVIDENCE_CONTRACT_ERROR comment describes -- it repeats real
  # depth/build/source work needlessly when the JSON this function should
  # have reused was sitting on disk the whole time.
  #
  # Codex review: the stdout-JSON case (format: json, no output-file) used
  # to fall through this check — it only ever looked at $OUTPUT_FILE, never
  # the already-materialized $_STDOUT_JSON_FILE — silently re-running the
  # whole scan/compare a second time just to get JSON that had already been
  # produced, for scan doubling potentially expensive --depth build/source
  # work and describing a separate, budget-metered run.
  # ADR-068 D4/Phase 5: --show-only is gone -- its equivalent is one of
  # --view's repeatable tokens (`--view show=...`), so only *that* token
  # (never `--view leaf`/`--view demangle`/etc., which change no content)
  # disqualifies reuse. Indexed, not a plain `for arg in` loop, since the
  # token-form `--view show=...` needs the *next* array element to see the
  # value; the inline `--view=show=...` form carries it in the same element.
  [[ -n "$(_json_report_src)" ]] || return 1
  local i
  for ((i = 0; i < ${#CMD[@]}; i++)); do
    case "${CMD[$i]}" in
      --view) [[ "${CMD[$((i + 1))]:-}" == show=* ]] && return 1 ;;
      --view=show=*) return 1 ;;
    esac
  done
  return 0
}

_build_json_cmd() {
  PR_CMD_JSON=()
  local i
  for ((i = 0; i < ${#CMD[@]}; i++)); do
    case "${CMD[$i]}" in
      --format | -o | --output | --output-file)
        ((i++))  # skip the flag's value too
        ;;
      --view)
        # ADR-068 D4/Phase 5: --show-only's replacement token. A
        # `--view show=...` occurrence is a display filter ("limit
        # displayed changes", does NOT affect exit codes) -- keeping it
        # would hide gated breaks from the comment while the check still
        # fails red, so drop it (and its value) so the comment sees the
        # full change set the gate acted on. Every other --view token
        # (leaf/impact/root-cause/demangle/no-demangle/patterns) changes
        # no content, only how it's grouped/spelled/explained, so it is
        # kept -- the loop below falls through to the default case for it.
        if [[ "${CMD[$((i + 1))]:-}" == show=* ]]; then
          ((i++))  # drop the token and its value
        else
          PR_CMD_JSON+=("${CMD[$i]}")
        fi
        ;;
      --write)
        # Codex review, fresh evidence: this rerun's whole purpose is one
        # clean JSON report at $PR_JSON via the -o appended below -- a
        # pre-existing --write left over from $CMD (the primary run's own
        # PR_JSON sidecar injection, present on every non-JSON-format
        # compare/scan invocation) is not just redundant here, it collides:
        # this function is only ever reached after the caller's own
        # "$PR_JSON already populated" check came back empty, i.e. the
        # primary run aborted before ever reaching its own --write
        # (NOT_COMPARABLE and other early-refusal verdicts never render any
        # output at all -- confirmed live). Keeping --write here re-adds
        # the identical "json=$PR_JSON" path this rerun's own -o also
        # targets, which the CLI hard-rejects (--write's PATH must differ
        # from --output/-o), so the rerun always failed and the comment was
        # silently skipped with a misleading "no JSON report produced"
        # warning. Drop it (and its value) unconditionally -- a --write to
        # some other path would be equally pointless to keep for a run
        # whose only output anyone reads is $PR_JSON.
        ((i++))  # skip the flag's value too
        ;;
      --view=show=*)
        : # same display filter, inline value form — drop it for the re-run.
        ;;
      *)
        PR_CMD_JSON+=("${CMD[$i]}")
        ;;
    esac
  done
  PR_CMD_JSON+=(--format json -o "$PR_JSON")
}

_maybe_post_pr_comment() {
  [[ "${INPUT_PR_COMMENT:-true}" == "true" ]] || return 0
  case "$MODE" in
    compare) ;;
    *) return 0 ;;
  esac
  # A dry run performed no real comparison -- posting a comment would either
  # show nothing (no PR_JSON) or silently trigger a second, real compare just
  # to produce one, defeating the point of --dry-run. Skip entirely.
  #
  # Also checks the effective dry run, not only the dedicated input (Codex
  # review, P2, fresh evidence): an earlier revision checked `INPUT_DRY_RUN`
  # alone, so a caller passing `--dry-run` through `extra-args` on a
  # pull_request run (PR comments enabled by default) still fell through
  # into this function's own JSON-acquisition path -- retaining `--dry-run`
  # while appending `--format json -o ...` for a second invocation the CLI
  # itself rejects (the identical `--dry-run`-vs-`-o`/`--write` conflict the
  # sidecar-injection guard above exists to avoid at the command-assembly
  # stage), before this function's own error handling turned that failure
  # into a misleading "no JSON report produced" warning instead of the
  # clean, silent skip a real dry run gets.
  { [[ "${INPUT_DRY_RUN:-false}" == "true" ]] || _extra_args_has_dry_run_flag; } && return 0
  [[ "${INPUT_PR_COMMENT_ON:-changes}" == "never" ]] && return 0
  [[ "$VERDICT" == "ERROR" ]] && return 0
  # A budget overflow exits 5 before any report is emitted, so neither --write
  # nor a JSON primary output was written -- there is no report to reuse, and
  # cli-mirror: abicheck/cli_compare_fold.py::_exit_on_budget_overflow
  # (Previously cited `abicheck/cli_scan.py`'s `_BudgetOverflow` handler, a
  # module ADR-068 deleted; `compare` owns the exit-5 path now.)
  # re-running would just re-execute the same budget-limited (and
  # potentially expensive) scan only to hit the identical overflow again
  # with nothing new to show for it (Codex review).
  [[ "$VERDICT" == "BUDGET_OVERFLOW" ]] && return 0
  # EVIDENCE_CONTRACT_ERROR deliberately does NOT get the same skip
  # (Codex review, fresh evidence): unlike BUDGET_OVERFLOW, this verdict
  # can still have a real JSON report worth reusing.
  #
  # Update (2026-09-03): this verdict now has two independent sources. A
  # single ARTIFACT sets it directly from `ABICHECK_EXIT == 7` (`cli_scan.
  # py`'s own dedicated `_EXIT_EVIDENCE_CONTRACT_ERROR` -- see that arm's
  # own comment for why), not from finding a JSON report -- for that
  # source, reaching this verdict does NOT prove a report exists (a
  # `--format text` run with no JSON secondary output produces none at
  # all). A `--artifact-set` member abort, by contrast, still sets it via
  # the exit-1 arm's own JSON-verdict check (restored after a Codex review
  # caught its removal as a real regression), which -- like the pre-round-4
  # design this comment originally described -- *does* prove a report
  # exists. Falling through to the normal reuse-or-rerun path below does
  # the right thing either way: `_can_reuse_primary_json` finds and
  # reuses a report when one exists (JSON primary, or a text primary with
  # a JSON `--write` secondary), and the `else` branch below re-runs for
  # JSON when none does -- exactly the same reuse-or-rerun contract every
  # other verdict here already relies on, so this axis needs no special
  # case of its own any more.
  case "${GITHUB_EVENT_NAME:-}" in
    pull_request | pull_request_target) ;;
    *)
      echo "abicheck: not a pull_request event; skipping PR comment."
      return 0
      ;;
  esac

  local event="${GITHUB_EVENT_PATH:-}"
  local pr_number="" head_sha=""
  if [[ -n "$event" && -f "$event" ]] && command -v jq >/dev/null 2>&1; then
    pr_number=$(jq -r '.pull_request.number // empty' "$event" 2>/dev/null)
    head_sha=$(jq -r '.pull_request.head.sha // empty' "$event" 2>/dev/null)
  fi
  if [[ -z "$pr_number" ]]; then
    echo "::warning::abicheck: could not determine the PR number; skipping PR comment."
    return 0
  fi

  echo "::group::abicheck PR comment"
  # Template-based mktemp (X's at the end) — portable across GNU and BSD/macOS,
  # unlike the GNU-only --suffix option.
  if [[ -z "${PR_JSON:-}" ]]; then
    PR_JSON=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-pr-json.XXXXXX")
  fi
  PR_BODY=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-pr-body.XXXXXX")
  if [[ -s "$PR_JSON" ]]; then
    : # Already populated by the primary run's --write (compare
      # or scan mode, non-json primary format) — nothing left to do.
  elif _can_reuse_primary_json; then
    # The primary run already produced a faithful JSON report — reuse it instead
    # of re-running the whole comparison.
    cp "$(_json_report_src)" "$PR_JSON"
  else
    _build_json_cmd
    # Re-run for JSON; a non-zero exit here is expected on breaks — the report
    # file is still written, so we ignore the status.
    "${PR_CMD_JSON[@]}" >/dev/null 2>/dev/null || true
  fi
  if [[ ! -s "$PR_JSON" ]]; then
    echo "::warning::abicheck: no JSON report produced; skipping PR comment."
    echo "::endgroup::"
    return 0
  fi

  # Mirror the step's gate: when fail-on-api-break is set, API/source breaks
  # turn the check red, so the comment must file them under Breaking too.
  PR_GATE_ARGS=()
  if [[ "${INPUT_FAIL_ON_API_BREAK:-false}" == "true" ]]; then
    PR_GATE_ARGS+=(--gate-api-break)
  fi
  # Mirror fail-on-breaking too (default true) — only affects the
  # analysis-incomplete bucket's blocking headline (Codex review): without
  # this, a policy override promoting a coverage-gap finding to
  # severity: "breaking" would always render the blocking headline even
  # when fail-on-breaking: false left the check green.
  if [[ "${INPUT_FAIL_ON_BREAKING:-true}" == "false" ]]; then
    PR_GATE_ARGS+=(--no-gate-breaking)
  fi

  # Link the workflow run (where the full JSON/SARIF report is uploaded as an
  # artifact) so a condensed/truncated comment always points at the full detail.
  local run_url=""
  if [[ -n "${GITHUB_SERVER_URL:-}" && -n "${GITHUB_REPOSITORY:-}" && -n "${GITHUB_RUN_ID:-}" ]]; then
    run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
  fi

  # compare's own JSON (baseline or audit-only alike) always carries a
  # "library" field -- no separate --subject fallback needed.
  local subject_args=()

  # Codex review: on a Windows Git Bash runner, `actions/setup-python`
  # exposes `python`/`python.exe` but not always `python3` -- a hard-coded
  # `python3` here would silently fail (swallowed by the trailing `|| true`
  # below), leaving PR_BODY empty and the comment skipped or an existing
  # sticky one deleted. `$_PY_BIN` is the same resolved interpreter every
  # other Python invocation in this script already uses.
  #
  # `-c '...runpy.run_module(...)...'` rather than the more obvious
  # `-m abicheck.cli_pr_comment`, so this can run from `$_PY_SAFE_DIR` (see
  # its own definition above) the same way every other `abicheck`-importing
  # invocation in this file does -- `-m` inserts the CWD into sys.path[0]
  # exactly like `-c` does, and this is the one inline-Python invocation in
  # this file that isn't already `-c`-shaped. Functionally identical to
  # `-m abicheck.cli_pr_comment` from click's own perspective (it reads
  # sys.argv[1:], unaffected by this); the only observable difference is
  # the program name `--help`/usage text shows ("-c" instead of the
  # resolved module path), which this Action does not depend on.
  (cd "$_PY_SAFE_DIR" && PYTHONPATH= "$_PY_BIN" -c '
import runpy

runpy.run_module("abicheck.cli_pr_comment", run_name="__main__")
' "$PR_JSON" \
    --sha "${head_sha:-${GITHUB_SHA:-}}" \
    --detail "${INPUT_PR_COMMENT_DETAIL:-standard}" \
    --on "${INPUT_PR_COMMENT_ON:-changes}" \
    --run-label "run #${GITHUB_RUN_NUMBER:-?}" \
    ${run_url:+--report-url "$run_url"} \
    ${PR_GATE_ARGS[@]+"${PR_GATE_ARGS[@]}"} \
    ${subject_args[@]+"${subject_args[@]}"} \
    -o "$PR_BODY") || true

  if [[ ! -s "$PR_BODY" ]]; then
    echo "abicheck: no comment to post (no changes / --on=${INPUT_PR_COMMENT_ON:-changes})."
    # Sticky mode: clear any prior comment so a once-dirty PR that is now clean
    # doesn't keep showing a stale BREAKING report.
    if [[ "${INPUT_PR_COMMENT_MODE:-update}" != "new" ]]; then
      _delete_sticky_pr_comment "$pr_number"
    fi
    echo "::endgroup::"
    return 0
  fi

  _post_pr_comment "$pr_number" "$PR_BODY"
  echo "::endgroup::"
}

# Hidden marker the renderer embeds; used to find OUR sticky comment.
PR_COMMENT_MARKER="<!-- abicheck-sticky-report -->"

_create_pr_comment() {
  # Create a fresh comment from a body file via the REST API (jq builds the
  # JSON payload so arbitrary markdown is escaped safely).
  local repo="$1" pr_number="$2" body_file="$3"
  jq -Rs '{body: .}' "$body_file" \
    | gh api -X POST "repos/$repo/issues/$pr_number/comments" --input - >/dev/null
}

_delete_sticky_pr_comment() {
  # Remove OUR previous sticky comment (located by marker) so a once-dirty PR
  # that is now clean stops showing a stale report.
  local pr_number="$1"
  local repo="${GITHUB_REPOSITORY:-}"
  if [[ -z "$repo" ]] || ! command -v jq >/dev/null 2>&1; then
    return 0
  fi
  local existing_id
  existing_id=$(gh api --paginate "repos/$repo/issues/$pr_number/comments" \
    --jq ".[] | select(.body | contains(\"$PR_COMMENT_MARKER\")) | .id" 2>/dev/null | tail -1)
  if [[ -n "$existing_id" ]]; then
    if gh api -X DELETE "repos/$repo/issues/comments/$existing_id" >/dev/null 2>&1; then
      echo "abicheck: cleared stale sticky comment $existing_id (no current changes)."
    fi
  fi
}

_gh_pr_comment_fallback() {
  # Porcelain fallback. Pass -R when we know the repo so it works without a
  # local checkout of the PR's repository (or after checking out a different one).
  local pr_number="$1" body_file="$2" repo="$3"
  if [[ -n "$repo" ]]; then
    gh pr comment "$pr_number" -R "$repo" --body-file "$body_file" \
      || echo "::warning::abicheck: failed to post PR comment (need 'pull-requests: write')."
  else
    gh pr comment "$pr_number" --body-file "$body_file" \
      || echo "::warning::abicheck: failed to post PR comment (need 'pull-requests: write')."
  fi
}

_post_pr_comment() {
  local pr_number="$1" body_file="$2"
  local repo="${GITHUB_REPOSITORY:-}"
  local mode="${INPUT_PR_COMMENT_MODE:-update}"

  # Without a known repo or jq we cannot use the REST path; fall back to the
  # porcelain command (which then resolves the repo from the local checkout).
  if [[ -z "$repo" ]] || ! command -v jq >/dev/null 2>&1; then
    _gh_pr_comment_fallback "$pr_number" "$body_file" "$repo"
    return 0
  fi

  # Sticky (update) mode: locate OUR previous comment by its hidden marker (not
  # merely the last comment by this token, which could belong to other
  # automation) and edit that specific comment in place.
  if [[ "$mode" != "new" ]]; then
    local existing_id
    existing_id=$(gh api --paginate "repos/$repo/issues/$pr_number/comments" \
      --jq ".[] | select(.body | contains(\"$PR_COMMENT_MARKER\")) | .id" 2>/dev/null | tail -1)
    if [[ -n "$existing_id" ]]; then
      if jq -Rs '{body: .}' "$body_file" \
          | gh api -X PATCH "repos/$repo/issues/comments/$existing_id" --input - >/dev/null 2>&1; then
        echo "abicheck: updated sticky comment $existing_id."
        return 0
      fi
      echo "::warning::abicheck: could not update comment $existing_id; posting a new one."
    fi
  fi

  # Create via the REST API (repo-qualified, so it works without a local clone
  # of the PR repo); fall back to the porcelain command with -R if that fails.
  _create_pr_comment "$repo" "$pr_number" "$body_file" 2>/dev/null \
    || _gh_pr_comment_fallback "$pr_number" "$body_file" "$repo"
}

_emit_annotations
_maybe_post_pr_comment

# ---------------------------------------------------------------------------
# Determine final exit code based on user preferences
# ---------------------------------------------------------------------------
FINAL_EXIT=0

if [[ "$VERDICT" == "REPORT_UNREADABLE" ]]; then
  # Checked ahead of every other arm, including ERROR: no fail-on-* input
  # governs it (none of them is a statement about whether the user wants an
  # unverifiable result reported as a pass), and there is no compatibility
  # tier to weigh it against. The ::error:: naming the specific defect was
  # already emitted where the verdict was set.
  FINAL_EXIT=1

elif [[ "$VERDICT" == "ERROR" ]]; then
  echo "::error::abicheck failed with exit code $ABICHECK_EXIT"
  FINAL_EXIT=1

elif [[ "$MODE" == "deps-compare" || "$MODE" == "deps-tree" ]]; then
  # deps-compare: FAIL always fails; WARN fails when fail-on-breaking is true
  # deps-tree: FAIL always fails the step
  if [[ "$VERDICT" == "FAIL" ]]; then
    echo "::error::Full-stack check failed (load failure or ABI break)."
    FINAL_EXIT=1
  elif [[ "$VERDICT" == "WARN" && "${INPUT_FAIL_ON_BREAKING:-true}" == "true" ]]; then
    echo "::warning::ABI risk detected in dependency stack. Set fail-on-breaking: false to allow."
    FINAL_EXIT=1
  fi

elif [[ "$MODE" == "dump" ]]; then
  # dump: a producer — non-zero is always an error (already mapped above)
  :

else
  # compare mode: BREAKING/API_BREAK follow fail-on flags; REMOVED_LIBRARY
  # only appears when --fail-on-removed-library was passed to the CLI
  # (directory/package operands only).
  if [[ "${GATE_TIER:-$VERDICT}" == "BREAKING" && "${INPUT_FAIL_ON_BREAKING:-true}" == "true" \
        && "$ADVISORY_BREAK" != "true" ]]; then
    echo "::error::ABI break detected. Set fail-on-breaking: false to continue despite breaks."
    FINAL_EXIT=1
  fi

  if [[ "${GATE_TIER:-$VERDICT}" == "API_BREAK" && "${INPUT_FAIL_ON_API_BREAK:-false}" == "true" \
        && "$ADVISORY_BREAK" != "true" ]]; then
    echo "::error::API break detected. Set fail-on-api-break: false to ignore API-level breaks."
    FINAL_EXIT=1
  fi

  if [[ "$VERDICT" == "REMOVED_LIBRARY" ]]; then
    echo "::error::Library removed between old and new package. Set fail-on-removed-library: false to allow."
    FINAL_EXIT=1
  fi

  # NOT_COMPARABLE (exit 16, ADR-050 D2) unconditionally fails the step: a
  # scope/profile mismatch means no compatibility comparison ran at all,
  # not that one ran and found (or didn't find) a break. This arm was
  # missing entirely before this fix (Codex review, PR #1160, round 7,
  # fresh evidence) -- before the exit-16 dispatch arm existed, that exit
  # code fell into the generic `VERDICT="ERROR"` case, which *does* fail
  # the step via this function's own first `if` branch; adding the correct,
  # more specific NOT_COMPARABLE verdict without also adding this check
  # silently regressed a real `mode: compare` scope mismatch to a passing
  # step.
  if [[ "$VERDICT" == "NOT_COMPARABLE" ]]; then
    # This cannot point at "the JSON report's diff.reason" (Codex review,
    # fresh evidence):
    # compare's own comparability-gate refusal (`_report_not_comparable()`)
    # raises before any DiffResult exists, so there is no `diff` key at all
    # -- its `--format json` document carries the mismatch detail at root
    # `reason` (schema 2.17), not nested under `diff`. And for every
    # human-facing format (markdown/html/review, the Action's own default),
    # that function writes no JSON document whatsoever -- not even into a
    # secondary `--write` this run injected -- so pointing at "the JSON
    # report" is doubly wrong for the common case: there usually isn't one.
    # The one place the mismatch detail is guaranteed to be is this
    # command's own stderr, already in the job log above this line.
    echo "::error::abicheck reported NOT_COMPARABLE: the two sides were not extracted under a comparable profile/scope contract. See the command's own error output above for what mismatched (or, with format: json, the JSON report's root reason field)."
    FINAL_EXIT=1
  fi

  # AUDIT_GATE (exit 3, ADR-068 2026-09-10 amendment) unconditionally fails
  # the step -- no fail-on-* flag governs it, matching NOT_COMPARABLE/
  # EVIDENCE_CONTRACT_ERROR above. Reachable here whenever this compare
  # invocation omitted old-library (the audit-only shape, `compare
  # --no-baseline`) and a real BREAKING/API_BREAK-classified candidate-side
  # finding was present while a severity preset (other than `info-only`)
  # was in effect (`--severity-preset` input, or `extra-args`).
  if [[ "$VERDICT" == "AUDIT_GATE" ]]; then
    echo "::error::abicheck --no-baseline reports a gating audit finding (exit code 3): see the JSON report's findings[] for what gated. This is NOT a compatibility break -- no baseline was compared."
    FINAL_EXIT=1
  fi

  # BUDGET_OVERFLOW (exit 5, `cli_compare_fold.py`'s own `sys.exit(5)`)
  # unconditionally fails the step too: `compare --help-all` documents
  # `--budget` as making a CI job "fail clearly (exit 5)" -- no fail-on-*
  # flag governs it. Reachable here for the dedicated `budget` input (a
  # baseline compare) or via the documented `extra-args` escape hatch on
  # any other compare shape. Missing entirely until this fix (Codex review,
  # PR #1210, round 11, fresh evidence): the round-10 fix that mapped exit 5
  # to the more specific `BUDGET_OVERFLOW` verdict (previously it fell into
  # the generic `VERDICT="ERROR"` case, which *does* fail the step via this
  # function's own first `if` branch) initially missed this matching
  # `FINAL_EXIT=1` check, so a budget overflow silently regressed from a
  # failing step to a passing one.
  if [[ "$VERDICT" == "BUDGET_OVERFLOW" ]]; then
    echo "::error::abicheck exceeded the configured --budget (exit code 5). Pin a shallower --depth or raise the budget; a budget never silently shrinks scope."
    FINAL_EXIT=1
  fi

  # EVIDENCE_CONTRACT_ERROR (exit 7, ADR-037 D5) unconditionally fails the
  # step too: no compatibility comparison ran at all, so no fail-on-*
  # flag governs it. This arm was missing entirely until this fix -- before
  # the exit-7 dispatch arm above existed for a native `mode: compare`/
  # `compare --no-baseline` request, that exit code fell into the generic
  # `VERDICT="ERROR"` case, which *does* fail the step via this function's
  # own first `if` branch; adding the correct, more specific
  # EVIDENCE_CONTRACT_ERROR verdict without also adding this check would
  # have silently regressed a real evidence-contract abort to a passing
  # step, exactly the class of gap the NOT_COMPARABLE comment above
  # describes for its own exit code.
  if [[ "$VERDICT" == "EVIDENCE_CONTRACT_ERROR" ]]; then
    echo "::error::abicheck aborted: this run's evidence contract could not be satisfied (ADR-037 D5) — see the command's own error message above for the exact cause."
    FINAL_EXIT=1
  fi

  # Severity-driven exit code 1 (from --severity-* flags)
  if [[ "${GATE_TIER:-$VERDICT}" == "SEVERITY_ERROR" ]]; then
    echo "::error::Severity-level error detected by abicheck."
    FINAL_EXIT=1
  fi

  # ADR-049 Phase 7's contract-coverage axis, unconditional for the same
  # "no compatibility comparison ran at all" reason as the checks above.
  # Reads `_coverage_gated()` directly rather than the VERDICT/GATE_TIER
  # label, so
  # a BREAKING/API_BREAK exit that outranks the coverage axis in the max-fold
  # (and, with the matching fail-on-* flag false, would otherwise leave the
  # step green) cannot silently swallow it.
  if _coverage_gated; then
    echo "::error::abicheck could not close the selected --contract domain on the available evidence; see contract_coverage_failures in the JSON report. Accept incomplete assurance with contract.unresolved: warn to allow."
    FINAL_EXIT=1
  fi

  # P0.4's analysis-assurance axis, unconditional exactly like the
  # contract-coverage check immediately above and for the same reason.
  if _assurance_gated; then
    echo "::error::abicheck's own evidence was not fully complete under assurance.require_complete; see analysis_assurance in the JSON report for what fell short."
    FINAL_EXIT=1
  fi

  # A report that claims a schema version whose contract includes the
  # assurance contribution, and then omits it. Unconditional for the same
  # reason as the axis check above, but a distinct failure: the axis did not
  # report a shortfall -- the report failed to report the axis at all, and
  # `_assurance_gated`'s deliberate "cannot tell means not gated" rule would
  # otherwise turn that into a silent pass. An older report that genuinely
  # predates the field answers `absent_legacy_schema` and is untouched here.
  if _assurance_axis_contradictory; then
    echo "::error::abicheck's JSON report claims a schema version that carries analysis_assurance_exit_contribution but omits it, so whether the analysis-assurance gate fired cannot be established from it. This is an invalid report, not a passing assurance check -- re-run, and report the inconsistency if it persists."
    FINAL_EXIT=1
  fi

  # ADR-065 S2's completeness axis, unconditional for the same reason: no
  # fail-on-* input governs it -- `--on-incomplete-scope block` was the
  # user's own choice, and a run that completed no comparison is never a
  # pass under any setting (D7).
  if _scope_gated; then
    echo "::error::abicheck's comparison scope was not fully checked (an unchecked selected member under scope.on_incomplete: block, or no comparison completed at all); see comparison_scope in the JSON report."
    FINAL_EXIT=1
  fi
fi

exit $FINAL_EXIT
