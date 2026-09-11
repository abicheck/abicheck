#!/usr/bin/env bash
# Mode-aware validation of the Action's `mode`/`new-library`/`old-library`/
# `format`/`upload-sarif` inputs, run as the very first composite-action step —
# before Python setup, system-dependency installation (castxml/gcc/clang,
# action/install-deps.sh), or `pip install abicheck`.
#
# Why this exists: a real integration passed a multi-library release
# directory as `new-library` to a single-artifact mode (`mode: dump`), and
# requested `format: sarif` + `upload-sarif: true` on a non-compare step.
# Neither combination is supported — dump analyses exactly one artifact (it
# has no per-library fan-out the way `compare`'s release engine does), and
# a non-compare mode never produces a SARIF report — but previously nothing
# caught this until well after a multi-minute toolchain install and build,
# and the unsupported format silently fell back to `text` with only a
# `::warning::`, so a workflow that thought it was wiring up GitHub Code
# Scanning via SARIF got neither an error nor a SARIF report. Failing fast
# here, before any dependency install, surfaces the misconfiguration
# immediately and for free.
#
# action/run.sh independently re-checks the format/upload-sarif rules
# right before invoking abicheck (defense in depth for anyone invoking
# run.sh directly, e.g. in tests) — keep both in sync.
set -uo pipefail

MODE="${INPUT_MODE:-compare}"
FORMAT="${INPUT_FORMAT:-}"
NEW_LIBRARY="${INPUT_NEW_LIBRARY:-}"
NEW_LIBRARY_SET="${INPUT_NEW_LIBRARY_SET:-}"
OLD_LIBRARY="${INPUT_OLD_LIBRARY:-}"
UPLOAD_SARIF="${INPUT_UPLOAD_SARIF:-false}"
LANG_INPUT="${INPUT_LANG:-}"
AST_FRONTEND="${INPUT_AST_FRONTEND:-}"
GCC_PATH="${INPUT_GCC_PATH:-}"
GCC_PREFIX="${INPUT_GCC_PREFIX:-}"
GCC_OPTIONS="${INPUT_GCC_OPTIONS:-}"
SYSROOT="${INPUT_SYSROOT:-}"
NOSTDINC="${INPUT_NOSTDINC:-false}"
SNAPSHOT_COMPRESSION="${INPUT_SNAPSHOT_COMPRESSION:-}"
REQUIRE_COMPLETE_ANALYSIS="${INPUT_REQUIRE_COMPLETE_ANALYSIS:-false}"

# A directory, or a file whose name/magic bytes match a recognized package
# format (RPM, Deb, tar, conda, wheel) — mirrors action/run.sh's
# `_is_release_style_operand()` (abicheck/package.py's `is_package()`
# detection, including its magic-byte fallback for extensionless RPM/Deb).
# Duplicated rather than sourced so this validation step has zero
# dependency on run.sh's internal layout; tests/test_action_validate_inputs.py
# runs both copies against the same fixtures to catch drift between them.
_is_release_style_operand() {
  local path="$1"
  [[ -d "$path" ]] && return 0
  local lower
  lower=$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    *.rpm | *.deb | *.tar | *.tar.gz | *.tar.xz | *.tar.bz2 | *.tar.zst | *.tgz | *.conda | *.whl)
      return 0
      ;;
  esac
  [[ -f "$path" ]] || return 1
  local magic
  magic=$(od -An -tx1 -N 8 "$path" 2>/dev/null | tr -d ' \n')
  case "$magic" in
    edabeedb*) return 0 ;;          # RPM lead magic
    213c617263683e0a) return 0 ;;   # "!<arch>\n" (Deb ar archive)
  esac
  return 1
}

# Workflow-command injection defense (bug class
# `trust_boundary.shell_workflow_injection`; #705 -> #758).
#
# Every message below interpolates at least one INPUT_* value, and those are
# workflow-controlled. A GitHub annotation is line-delimited, so a value
# carrying a newline ends the annotation and whatever follows is parsed as a
# *new* workflow command: `jobs: "1\n::error::spoofed"` emits a spoofed
# error, and `::set-output`/`::add-mask` are reachable the same way. Command
# substitution is not the risk here (the value is expanded once, into a
# double-quoted string, and bash does not re-expand it) -- line breaks are.
#
# Collapsing CR/LF in the one place every annotation is emitted covers each
# interpolation site in this file at once, including the ones that predate
# this helper, rather than asking every future message to remember.
#
# `%` is escaped first, because the runner *percent-decodes* a workflow
# command's message data: a value carrying the literal five characters
# `%0A::error::` holds no CR/LF for the collapse below to find, and the
# runner turns it into a real line break after this script has finished
# with it. Escaping to `%25` makes the decode round-trip back to a literal
# `%` for the reader instead. This is `actions/toolkit`'s own `escapeData`
# order (`%` then CR/LF), followed exactly so an escape introduced here is
# never itself re-escaped (CodeRabbit review, CWE-117).
#
# `printf`, never `echo`: with `xpg_echo` on -- a build-time default on some
# bash builds, and settable through `BASHOPTS`/`BASH_ENV` -- `echo` expands
# backslash escapes in its argument, so a value carrying the *literal* five
# characters `\n::error::` passes the CR/LF collapse above (it holds no real
# newline to collapse) and is then turned into one by the emitter itself.
# Reproducible against this script with `bash -O xpg_echo` (Codex review).
# `printf '%s\n'` treats the value as data under every shell option, which
# is why the format string is fixed and the message is an argument.
_sanitize_annotation() {
  printf '%s' "${1//%/%25}" | tr '\r\n' '  '
}

_fail() {
  printf '%s\n' "::error::$(_sanitize_annotation "$1")"
  exit 1
}

_warn() {
  printf '%s\n' "::warning::$(_sanitize_annotation "$1")"
}

# against, estimate, audit: retired outright -- each existed only for the
# now-removed mode: scan, so none of them can ever do anything on any mode
# any more. Failing rather than warning names the replacement, matching the
# "hard removal, no deprecation window" treatment new-library-set/crosscheck/
# risk-rules get below (ADR-068 D8). Checked here, before the mode-specific
# case block below, so a workflow that still sets a retired input alongside
# an audit-only-shape-specific input (since/budget/...) is told about the
# retired input first -- matching action/run.sh's own ordering (CodeRabbit
# review, PR #1223).
if [[ -n "${INPUT_AGAINST:-}" ]]; then
  _fail "against is no longer supported (it applied only to the now-removed mode: scan). Set old-library (or abi-baseline) to the same value under mode: compare instead."
fi
if [[ "${INPUT_ESTIMATE:-false}" == "true" ]]; then
  _fail "estimate is no longer supported (it applied only to the now-removed mode: scan, as a dry-run alias). Set dry-run: 'true' instead, which applies to every mode."
fi
if [[ "${INPUT_AUDIT:-false}" == "true" ]]; then
  _fail "audit is no longer supported (it applied only to the now-removed mode: scan, forcing an audit-only run). Under mode: compare, simply omit old-library and abi-baseline to run an audit-only compare --no-baseline; set severity-preset (e.g. 'default') if this job should still gate on a BREAKING/API_BREAK-classified finding the way mode: scan's own audit mode always did."
fi

case "$MODE" in
  scan)
    # ADR-068's Action-input-lifecycle amendment: `mode: scan` is removed
    # outright (D8, hard removal, no deprecation window) -- there is no
    # translation left to perform, only a clear error naming the
    # replacement for the caller's own shape. Checked here, before Python
    # setup/toolchain install, same fail-fast rationale as every other
    # check in this script; run.sh has no `scan` case left at all to fall
    # back on.
    if [[ ( -n "${INPUT_AGAINST:-}" || -n "${INPUT_ABI_BASELINE:-}" ) && "${INPUT_AUDIT:-false}" != "true" ]]; then
      _fail "mode: scan is no longer supported (ADR-068). Replacement for a baseline scan: mode: compare with old-library set to the same baseline (against/abi-baseline both map onto old-library/abi-baseline unchanged), and new-library unchanged."
    else
      _fail "mode: scan is no longer supported (ADR-068). Replacement for an audit-only scan (no baseline, or audit: true): mode: compare with old-library and abi-baseline both omitted -- new-library alone runs an audit-only compare --no-baseline. This candidate-side audit no longer gates a CI job on a BREAKING/API_BREAK-classified finding by default the way mode: scan did -- set severity-preset (e.g. 'default') to restore that gating; without it the step always exits 0/passes regardless of what the audit finds."
    fi
    ;;
  dump)
    if [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; then
      _fail "mode: dump does not accept a directory or package for new-library ('$NEW_LIBRARY') — dump snapshots exactly one library, it has no per-library fan-out. Dump each library individually (one step per binary, or a matrix), or switch to mode: compare with a directory/package operand, which fans out to a per-library comparison automatically."
    fi
    # ADR-059: mirrors the CLI's own --compression choices
    # (cli_options.snapshot_compression_option) -- forwarded to `dump`
    # unvalidated otherwise, so a typo'd value would only surface after a
    # multi-minute toolchain install and build (Codex review).
    if [[ -n "$SNAPSHOT_COMPRESSION" ]]; then
      case "$SNAPSHOT_COMPRESSION" in
        auto | none | gzip | zstd) ;;
        *)
          _fail "snapshot-compression '$SNAPSHOT_COMPRESSION' is not recognized. Use 'auto', 'none', 'gzip', or 'zstd'."
          ;;
      esac
    fi
    ;;
  deps-tree | deps-compare)
    # `abicheck deps tree`/`deps compare` both take a single BINARY, not a
    # directory/package -- the same per-artifact contract dump/scan have,
    # missing here let an unsupported compare-only operand pass this
    # fail-fast step and fail later in the CLI instead (Codex review).
    if [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; then
      _fail "mode: $MODE does not accept a directory or package for new-library ('$NEW_LIBRARY') — deps tree/deps compare analyse exactly one binary, they have no per-library fan-out. Point new-library at a single binary."
    fi
    if [[ -n "$FORMAT" && "$FORMAT" != "markdown" && "$FORMAT" != "json" && "$FORMAT" != "html" ]]; then
      _fail "mode: $MODE does not support format: $FORMAT — only 'markdown', 'json', and 'html' are supported."
    fi
    ;;
  compare)
    # Audit-only shape (ADR-068's Action-input-lifecycle amendment; the
    # replacement for legacy `mode: scan` with no baseline): old-library and
    # abi-baseline both omitted routes to `compare --no-baseline
    # new-library`, a one-sided audit against the candidate's own public
    # surface, reporting no old/new compatibility verdict at all. Checked
    # here, before Python setup and the toolchain install, the same
    # fail-fast rationale as every other check in this script -- run.sh
    # keeps its own copy of this exact check for anyone invoking it
    # directly (e.g. tests).
    if [[ -z "$OLD_LIBRARY" && -z "${INPUT_ABI_BASELINE:-}" ]]; then
      if [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; then
        _fail "mode: compare's audit-only shape (old-library/abi-baseline both omitted) does not accept a directory or package for new-library ('$NEW_LIBRARY') — an audit-only run analyses exactly one artifact, it has no per-library fan-out. Point new-library at a single library, or set old-library (or abi-baseline) to run a directory/package comparison instead."
      fi
      if [[ -n "${INPUT_SINCE:-}" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support since -- compare --no-baseline does not implement revision-range evidence scoping (ADR-068 D2 rejects --since as a usage error with no baseline). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports since, or drop since for this audit-only run."
      fi
      if [[ -n "${INPUT_CHANGED_PATH:-}" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support changed-path -- compare --no-baseline does not implement revision-range evidence scoping (ADR-068 D2 rejects --changed-path as a usage error with no baseline). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports changed-path, or drop changed-path for this audit-only run."
      fi
      if [[ -n "${INPUT_BUDGET:-}" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support budget -- compare --no-baseline's wall-clock guard is not wired to this path yet (ADR-068 D2 rejects --budget as a usage error with no baseline). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports budget, or drop budget for this audit-only run."
      fi
      if [[ "${INPUT_FOLLOW_DEPS:-false}" == "true" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support follow-deps -- compare --no-baseline's DT_NEEDED dependency walk is not wired to this path yet (rejected outright by the CLI, abicheck/frontends/cli/commands/no_baseline_rulings.py). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports follow-deps, or drop follow-deps for this audit-only run."
      fi
      if [[ -n "${INPUT_USED_BY:-}" || -n "${INPUT_USED_BY_MANIFEST:-}" \
            || -n "${INPUT_REQUIRED_SYMBOL:-}" || -n "${INPUT_REQUIRED_SYMBOLS:-}" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support used-by/used-by-manifest/required-symbol/required-symbols -- these scope a two-sided comparison to what a real consumer uses, and compare --no-baseline has no old/new pair to scope (rejected outright by the CLI, abicheck/frontends/cli/commands/no_baseline_rulings.py). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports consumer scoping, or drop these inputs for this audit-only run."
      fi
      if [[ -n "${INPUT_OLD_HEADER:-}" || -n "${INPUT_OLD_INCLUDE:-}" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support old-header/old-include -- there is no OLD side for this evidence to describe, and the CLI rejects an explicitly OLD-scoped --header/--include outright rather than silently dropping it (abicheck/frontends/cli/commands/no_baseline_rulings.py's _reject_old_sided_inputs). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports old-header/old-include, or drop these inputs for this audit-only run."
      fi
      # old-version's Action-level default is the literal placeholder
      # 'old' (action.yml), always present even when the caller never set
      # it -- indistinguishable from "not typed", so it is inert here
      # exactly as the CLI's own _SIDED_DEFAULTS treats it, not a usage
      # error. Only a real, non-default value is rejected (Codex review,
      # PR #1223, round 11: the unconditional truthiness check below this
      # comment previously rejected every audit-only invocation, since
      # INPUT_OLD_VERSION is never actually empty).
      if [[ -n "${INPUT_OLD_VERSION:-}" && "${INPUT_OLD_VERSION}" != "old" ]]; then
        _fail "mode: compare without a baseline (old-library/abi-baseline both omitted) does not support old-version -- there is no OLD side to label, and the CLI rejects an explicitly OLD-scoped --version outright rather than silently dropping it (abicheck/frontends/cli/commands/no_baseline_rulings.py's _reject_old_sided_inputs). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports old-version, or drop old-version for this audit-only run."
      fi
    fi
    # compare's full --format choice set is json|markdown|sarif|html|junit|
    # review (`abicheck compare --help-all`); a directory/package operand
    # fans out through the release engine, which narrows that to
    # cli.py's _RELEASE_FORMATS = {json, markdown, junit} (sarif/html/review
    # rejected — a clear UsageError, surfaced as VERDICT=ERROR by run.sh —
    # but only after Python/deps are installed). Mirror both allowlists
    # here so a bad value (a typo, or a release-only-invalid format like
    # sarif/html/review on a directory/package) is caught before that
    # install, not just downstream in the CLI.
    # tests/test_action_validate_inputs.py cross-checks these two sets
    # against the live CLI to catch drift.
    if [[ -n "$FORMAT" ]]; then
      if { [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; } \
         || { [[ -n "$OLD_LIBRARY" ]] && _is_release_style_operand "$OLD_LIBRARY"; }; then
        if [[ "$FORMAT" != "json" && "$FORMAT" != "markdown" && "$FORMAT" != "junit" ]]; then
          _fail "mode: compare does not support format: $FORMAT with a directory/package operand (old-library='$OLD_LIBRARY', new-library='$NEW_LIBRARY') — only 'json', 'markdown', and 'junit' are available for a directory/package comparison."
        fi
      elif [[ -z "$OLD_LIBRARY" && -z "${INPUT_ABI_BASELINE:-}" ]]; then
        # Audit-only shape (`compare --no-baseline`): the CLI's own
        # NO_BASELINE_UNSUPPORTED_FORMATS (abicheck/report/
        # no_baseline_document.py) rejects 'html' and 'review' outright --
        # both are two-sided-report renderers with no audit-only
        # equivalent. Caught here, before Python/toolchain install, rather
        # than only after setup as a CLI UsageError (Codex review).
        if [[ "$FORMAT" != "json" && "$FORMAT" != "markdown" && "$FORMAT" != "sarif" \
              && "$FORMAT" != "junit" && "$FORMAT" != "oneline" ]]; then
          _fail "mode: compare's audit-only shape (old-library/abi-baseline both omitted) does not support format: $FORMAT — only 'json', 'markdown', 'sarif', 'junit', and 'oneline' are available for compare --no-baseline (html and review are two-sided-only renderers). Set old-library (or abi-baseline) to run a real two-sided comparison, which supports html/review, instead."
        fi
      elif [[ "$FORMAT" != "json" && "$FORMAT" != "markdown" && "$FORMAT" != "sarif" \
            && "$FORMAT" != "html" && "$FORMAT" != "junit" && "$FORMAT" != "review" \
            && "$FORMAT" != "oneline" ]]; then
        _fail "mode: compare does not support format: $FORMAT — only 'json', 'markdown', 'sarif', 'html', 'junit', 'review', and 'oneline' are supported."
      fi
    fi
    # The L2 compile-context inputs (lang/ast-frontend/gcc-*/sysroot/
    # nostdinc) are rejected outright by run.sh for a directory/package
    # operand — the per-library release fan-out never threads a
    # CompileContext to each pair's header dump — so mirror that check here
    # too (Codex review): without it, a workflow with a slow dependency-
    # install step still passes this fail-fast validation and only errors
    # after setup begins, reopening the exact silent-fallback-until-late-
    # failure bug this script exists to prevent. "auto" is the documented
    # no-op spelling of ast-frontend (same default resolution as leaving it
    # unset) and must not trip this the way a real frontend choice does —
    # mirrors run.sh. Phase 7 (one-comparison-product.md §4.1) removed all
    # of these flags from compare's CLI, forwarded via a synthesized
    # --config compile: block instead (run.sh's add_compile_context_flags,
    # only reachable from the single-pair path) -- lang joined this group
    # then, since it would otherwise be silently dropped for a
    # directory/package operand rather than rejected.
    if { [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; } \
       || { [[ -n "$OLD_LIBRARY" ]] && _is_release_style_operand "$OLD_LIBRARY"; }; then
      # action.yml maps an omitted `lang` input to INPUT_LANG=c++ -- that is
      # the *default*, not a user override, so (mirroring the identical
      # "auto" carve-out for ast-frontend just above) it must not by itself
      # trip this guard (CodeRabbit review, PR #1146, finding #6; run.sh's
      # own release-operand predicate carries the same fix).
      if [[ (-n "$LANG_INPUT" && "$LANG_INPUT" != "c++") \
            || (-n "$AST_FRONTEND" && "$AST_FRONTEND" != "auto") \
            || -n "$GCC_PATH" || -n "$GCC_PREFIX" || -n "$GCC_OPTIONS" \
            || -n "$SYSROOT" || "$NOSTDINC" == "true" ]]; then
        _fail "mode: compare with a directory/package operand (old-library='$OLD_LIBRARY', new-library='$NEW_LIBRARY') does not support lang/ast-frontend/gcc-path/gcc-prefix/gcc-options/sysroot/nostdinc -- the per-library fan-out never threads the L2 compile context to each pair's header dump, so the requested context would silently never be applied and headers could be parsed under the wrong macros/sysroot/frontend. Compare the libraries individually (mode: compare with single-file operands) to use them."
      fi
    fi
    # P0.4: require-complete-analysis is rejected outright by run.sh for a
    # directory/package operand too -- the per-library release fan-out has
    # no single analysis_assurance result to gate on -- so mirror that
    # check here as well (same rationale as the compile-context guard
    # immediately above: without it, a slow dependency-install step still
    # runs before the request is rejected).
    if [[ "$REQUIRE_COMPLETE_ANALYSIS" == "true" ]] \
       && { { [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; } \
            || { [[ -n "$OLD_LIBRARY" ]] && _is_release_style_operand "$OLD_LIBRARY"; }; }; then
      _fail "mode: compare with a directory/package operand (old-library='$OLD_LIBRARY', new-library='$NEW_LIBRARY') does not support require-complete-analysis -- the CLI's per-library release fan-out has no single analysis_assurance result to gate on and rejects the flag outright. Compare the libraries individually (mode: compare with single-file operands) to use it."
    fi
    ;;
  *)
    # An unrecognized mode (e.g. a typo like 'scna') has no arm above, so
    # without this catch-all the case falls through silently and every
    # other check in this script is skipped -- Python setup, dependency
    # install, and pip install would all still run before run.sh's own
    # "Unknown mode" check finally reports it. Mirrors run.sh's message
    # verbatim.
    _fail "Unknown mode '$MODE'. Use 'compare', 'dump', 'deps-tree', or 'deps-compare'."
    ;;
esac

# Mode-scoped inputs: each of these is only forwarded/consumed in a subset
# of modes (per-input scope is already documented inline in action.yml's
# `description:` text), but setting one on an incompatible mode previously
# produced no feedback at all -- a silent no-op. These are legal-but-inert
# combinations, not errors, so warn (job-summary annotation) rather than
# fail the step outright.
_RELEASE_STYLE_OPERAND=false
if { [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; } \
   || { [[ -n "$OLD_LIBRARY" ]] && _is_release_style_operand "$OLD_LIBRARY"; }; then
  _RELEASE_STYLE_OPERAND=true
fi

# debug-info1/2, devel-pkg1/2, dso-only, include-private-dso,
# fail-on-removed-library: compare mode, directory/package operands only
# (action/run.sh's `_is_release_style_operand()` guard). Name/value kept as
# separate parallel arrays (not a single colon-joined string) since these
# values are often paths and may legitimately contain a colon themselves.
_pkg_input_names=(debug-info1 debug-info2 devel-pkg1 devel-pkg2)
_pkg_input_values=(
  "${INPUT_DEBUG_INFO1:-}"
  "${INPUT_DEBUG_INFO2:-}"
  "${INPUT_DEVEL_PKG1:-}"
  "${INPUT_DEVEL_PKG2:-}"
)
for _i in "${!_pkg_input_names[@]}"; do
  if [[ -n "${_pkg_input_values[$_i]}" ]] && { [[ "$MODE" != "compare" ]] || [[ "$_RELEASE_STYLE_OPERAND" != "true" ]]; }; then
    _warn "${_pkg_input_names[$_i]} is set but has no effect: it only applies to mode: compare with a directory/package old-library/new-library operand (mode is '$MODE')."
  fi
done

_bool_input_names=(dso-only include-private-dso fail-on-removed-library)
_bool_input_values=(
  "${INPUT_DSO_ONLY:-false}"
  "${INPUT_INCLUDE_PRIVATE_DSO:-false}"
  "${INPUT_FAIL_ON_REMOVED_LIBRARY:-false}"
)
for _i in "${!_bool_input_names[@]}"; do
  if [[ "${_bool_input_values[$_i]}" == "true" ]] && { [[ "$MODE" != "compare" ]] || [[ "$_RELEASE_STYLE_OPERAND" != "true" ]]; }; then
    _warn "${_bool_input_names[$_i]} is set but has no effect: it only applies to mode: compare with a directory/package old-library/new-library operand (mode is '$MODE')."
  fi
done

# used-by/required-symbol/required-symbols: compare mode only
# (ADR-043 scoped-comparison contracts). --used-by and --required-symbol/
# --required-symbols are mutually exclusive on the CLI itself, but that
# UsageError only surfaces after Python setup/dependency install/pip
# install -- fail here instead, before any of that, matching this script's
# whole reason for existing (G30 P1.3, resolving the S22/S23 root-Action
# gap: check-target's kind: app-consumer/plugin-contract route through
# these two flags).
_USED_BY="${INPUT_USED_BY:-}"
_USED_BY_MANIFEST="${INPUT_USED_BY_MANIFEST:-}"
_REQUIRED_SYMBOL="${INPUT_REQUIRED_SYMBOL:-}"
_REQUIRED_SYMBOLS="${INPUT_REQUIRED_SYMBOLS:-}"
if [[ ( -n "$_USED_BY" || -n "$_USED_BY_MANIFEST" ) && ( -n "$_REQUIRED_SYMBOL" || -n "$_REQUIRED_SYMBOLS" ) ]]; then
  _fail "used-by/used-by-manifest is mutually exclusive with required-symbol/required-symbols -- set only one contract per check."
fi
_scoped_input_names=(used-by used-by-manifest required-symbol required-symbols)
_scoped_input_values=("$_USED_BY" "$_USED_BY_MANIFEST" "$_REQUIRED_SYMBOL" "$_REQUIRED_SYMBOLS")
_scoped_input_unset_values=("" "" "" "")
for _i in "${!_scoped_input_names[@]}"; do
  if [[ "${_scoped_input_values[$_i]}" != "${_scoped_input_unset_values[$_i]}" && "$MODE" != "compare" ]]; then
    _warn "${_scoped_input_names[$_i]} is set but has no effect: it only applies to mode: compare (mode is '$MODE')."
  fi
done

# abi-baseline: compare mode (used as old-library, including the audit-only
# shape's own "have I got a baseline" check) only.
_ABI_BASELINE="${INPUT_ABI_BASELINE:-}"
if [[ -n "$_ABI_BASELINE" && "$MODE" != "compare" ]]; then
  _warn "abi-baseline is set but has no effect: it only applies to mode: compare (mode is '$MODE')."
fi

# baseline-profile/baseline-target: the release-contract baseline-set
# fallback for abi-baseline (only consulted when the release has no single
# *.abicheck.json asset) -- same mode scope as abi-baseline itself, plus a
# fail-fast pairing check so a caller who set one without the other finds
# out before any dependency install, not partway through the baseline
# fetch in run.sh (which enforces the identical pairing again at fetch time,
# since a direct run.sh invocation, e.g. in tests, bypasses this script).
_BASELINE_PROFILE="${INPUT_BASELINE_PROFILE:-}"
_BASELINE_TARGET="${INPUT_BASELINE_TARGET:-}"
_BASELINE_ASSET_NAME_TEMPLATE="${INPUT_BASELINE_ASSET_NAME_TEMPLATE:-}"
# Scope check keys off baseline-profile/baseline-target only, not
# baseline-asset-name-template: action.yml forwards that input's own
# manifest default ('abicheck-baseline-{profile}.tar.zst') unconditionally,
# so it's non-empty on every invocation regardless of whether the caller
# asked for the baseline-set fallback at all -- including it here warned on
# every ordinary dump/appcompat/deps-* run (Codex review).
if [[ ( -n "$_BASELINE_PROFILE" || -n "$_BASELINE_TARGET" ) \
   && "$MODE" != "compare" ]]; then
  _warn "baseline-profile/baseline-target/baseline-asset-name-template are set but have no effect: they only apply to mode: compare (mode is '$MODE')."
fi
if [[ -n "$_BASELINE_PROFILE" && -z "$_BASELINE_TARGET" ]]; then
  _fail "baseline-profile is set ('$_BASELINE_PROFILE') but baseline-target is not -- both are required to resolve one target's snapshot from a release-contract baseline-set archive."
fi
if [[ -n "$_BASELINE_TARGET" && -z "$_BASELINE_PROFILE" ]]; then
  _fail "baseline-target is set ('$_BASELINE_TARGET') but baseline-profile is not -- both are required to resolve one target's snapshot from a release-contract baseline-set archive."
fi
# run.sh only ever reaches _try_baseline_set_fallback from inside its
# `-n "$ABI_BASELINE"` fetch block -- baseline-profile/baseline-target set
# without abi-baseline can never trigger a fetch at all, silently falling
# through to whatever old-library/against the caller separately supplied
# instead (Codex review).
if [[ ( -n "$_BASELINE_PROFILE" || -n "$_BASELINE_TARGET" ) && -z "$_ABI_BASELINE" ]]; then
  _fail "baseline-profile/baseline-target are set but abi-baseline is not -- the release-contract baseline-set fallback is only reached while resolving abi-baseline (a release tag or 'latest-release'), so without it these inputs can never trigger a fetch."
fi

# public-header-dir: dump mode, and compare mode's audit-only (no-baseline)
# shape only -- the CLI's own --public-header-dir flag exists on `dump`
# only; a two-sided `compare` has no equivalent (its own -H already derives
# provenance AND extraction scope from a header root's directory semantics),
# but the audit-only translation folds it into -H's own union, the same way
# legacy `scan`'s identical audit-only shape did. run.sh's other branches
# never forward it, so a caller setting it there would have the input
# silently discarded without this warning (Codex review).
_PUBLIC_HEADER_DIR="${INPUT_PUBLIC_HEADER_DIR:-}"
_COMPARE_NO_BASELINE=false
if [[ "$MODE" == "compare" && -z "$OLD_LIBRARY" && -z "${INPUT_ABI_BASELINE:-}" ]]; then
  _COMPARE_NO_BASELINE=true
fi
if [[ -n "$_PUBLIC_HEADER_DIR" && "$MODE" != "dump" \
   && ! ( "$MODE" == "compare" && "$_COMPARE_NO_BASELINE" == "true" ) ]]; then
  _warn "public-header-dir is set but has no effect: it only applies to mode: dump, or mode: compare's audit-only (no old-library/abi-baseline) shape (mode is '$MODE')."
fi

# build-target: dump mode only (the CLI's own --build-target flag exists on
# that subcommand only; compare never had an equivalent). run.sh's
# compare/deps-tree/deps-compare branches never forward it (Codex review).
_BUILD_TARGET="${INPUT_BUILD_TARGET:-}"
if [[ -n "$_BUILD_TARGET" && "$MODE" != "dump" ]]; then
  _warn "build-target is set but has no effect: it only applies to mode: dump (mode is '$MODE')."
fi

# new-library-set: retired outright (ADR-068 (b): scan --artifact-set is
# gone, pending ADR-065 S3, and it applied only to the now-removed
# mode: scan) -- fails on every mode now, not just a scan-mode arm, since
# there is no longer a mode it could ever have affected.
if [[ -n "$NEW_LIBRARY_SET" ]]; then
  _fail "new-library-set is no longer supported (ADR-068 (b): scan --artifact-set, and mode: scan itself, are both retired). Preserving its per-member manifest/coverage accounting needs ADR-065 S3's package component inventories, which are not implemented. Remove new-library-set; compare each library individually, or wait for ADR-065 S3."
fi

# crosscheck: same shape as new-library-set directly above -- retired
# outright (ADR-068 (b): scan --crosscheck's KEY=error promotion syntax, and
# mode: scan itself, are both gone), and it applied only to the now-removed
# mode: scan (crosscheck has never been a `compare`/`dump`/`deps-tree`/
# `deps-compare` input) -- fails on every mode now.
if [[ -n "${INPUT_CROSSCHECK:-}" ]]; then
  _fail "crosscheck is no longer supported (ADR-068 (b): scan --crosscheck's KEY=error promotion syntax, and mode: scan itself, are both retired -- superseded, not dropped outright: every cross-source check already reaches compare as an ordinary ChangeKind, so --policy/.abicheck.yml's policy.overrides.<CHANGE_KIND>: error already lets you control any one check's severity). Remove crosscheck and use policy.overrides instead."
fi

# risk-rules: same shape -- retired outright (ADR-068 (b): scan
# --risk-rules and the risk-driven 'auto' depth escalation it fed, and
# mode: scan itself, are both gone), and it applied only to the now-removed
# mode: scan -- fails on every mode now.
if [[ -n "${INPUT_RISK_RULES:-}" ]]; then
  _fail "risk-rules is no longer supported (ADR-068 (b): scan --risk-rules, and mode: scan itself, are both retired). An omitted depth now resolves to the fixed 'headers' rung, the same default compare always used; set depth: source (or build) explicitly to pin the evidence level a risk profile used to escalate to. Remove risk-rules."
fi

# Removed inputs, kept registered in action.yml as tombstones and rejected
# here.
#
# Deleting an input from action.yml does not make a workflow that still sets
# it fail: GitHub drops the undeclared key before the composite action runs,
# leaving only an "Unexpected input(s)" line in the setup log and no
# annotation at the step. A pinned caller therefore keeps a setting in its
# workflow that has silently stopped doing anything -- reported by a real
# downstream integration (oneDAL) whose `jobs: 1` worker cap became inert on
# an abicheck bump with nothing failing. Re-declaring the input is what puts
# the removal in front of the caller; this block is what says so.
#
# Severity follows what the setting used to control:
#   - jobs was a tuning knob (worker count). Its removal changes resource
#     use, never a verdict, so warn rather than break a bump.
#   - bundle-system-providers configured which providers count as system
#     ones, i.e. real analysis semantics. Silently dropping that would
#     change findings, so it is a hard error with the migration named.
#
# bundle-system-providers' replacement is build-config's own .abicheck.yml
# `bundle.system_providers:` block (CLI cleanup phase two, PR J), which has
# no per-mode "inert" state left to warn about (build-config is
# unconditionally forwarded for every mode that can reach it).
if [[ -n "${INPUT_JOBS:-}" ]]; then
  _warn "jobs ('${INPUT_JOBS}') was removed (ADR-068 D5) and has no effect: abicheck's release fan-out auto-detects its worker count and clamps it to available memory, and the -j/--jobs flag it forwarded no longer exists. Remove jobs from your workflow. Expect higher wall time and peak RSS than a manually capped run."
fi
if [[ -n "${INPUT_BUNDLE_SYSTEM_PROVIDERS:-}" ]]; then
  _fail "bundle-system-providers ('${INPUT_BUNDLE_SYSTEM_PROVIDERS}') was removed and is no longer forwarded — leaving it set would silently analyse with a different system-provider allow-list than you asked for. Move the list to your .abicheck.yml's \`bundle.system_providers:\` block and pass that file as build-config, then remove this input."
fi

if [[ "$UPLOAD_SARIF" == "true" && "$MODE" != "compare" ]]; then
  _fail "upload-sarif is only meaningful with mode: compare (single-pair operands) — mode: $MODE never produces a SARIF report to upload. Remove upload-sarif, or switch to mode: compare."
fi

if [[ "$UPLOAD_SARIF" == "true" && "$FORMAT" != "sarif" ]]; then
  _fail "upload-sarif requires format: sarif (got '${FORMAT:-markdown}') — without it there is no SARIF report for the upload-sarif step to find."
fi
