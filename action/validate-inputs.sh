#!/usr/bin/env bash
# Mode-aware validation of the Action's `mode`/`new-library`/`old-library`/
# `format`/`upload-sarif` inputs, run as the very first composite-action step —
# before Python setup, system-dependency installation (castxml/gcc/clang,
# action/install-deps.sh), or `pip install abicheck`.
#
# Why this exists: a real integration passed a multi-library release
# directory as `new-library` to `mode: scan` (and separately to `mode:
# dump`), and requested `format: sarif` + `upload-sarif: true` on a scan
# step. Neither combination is supported — scan/dump analyse exactly one
# artifact (they have no per-library fan-out the way `compare`'s release
# engine does), and scan only emits text/json — but previously nothing
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
OLD_LIBRARY="${INPUT_OLD_LIBRARY:-}"
UPLOAD_SARIF="${INPUT_UPLOAD_SARIF:-false}"

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

_fail() {
  echo "::error::$1"
  exit 1
}

_warn() {
  echo "::warning::$1"
}

case "$MODE" in
  dump)
    if [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; then
      _fail "mode: dump does not accept a directory or package for new-library ('$NEW_LIBRARY') — dump snapshots exactly one library, it has no per-library fan-out. Dump each library individually (one step per binary, or a matrix), or switch to mode: compare with a directory/package operand, which fans out to a per-library comparison automatically."
    fi
    ;;
  scan)
    if [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; then
      _fail "mode: scan does not accept a directory or package for new-library ('$NEW_LIBRARY') — scan analyses exactly one artifact (a binary or a JSON snapshot), it has no per-library fan-out. Point new-library at a single library, or use mode: compare against a directory/package for a multi-library binary comparison."
    fi
    # Allowlist, not a denylist: any value other than scan's two real
    # formats (including a typo like 'xml', not just the known-bad
    # sarif/html) must be caught here too, not just downstream in the CLI.
    if [[ -n "$FORMAT" && "$FORMAT" != "text" && "$FORMAT" != "json" ]]; then
      _fail "mode: scan does not support format: $FORMAT — only 'text' and 'json' are supported. (An unsupported format used to silently fall back to 'text', which is especially misleading paired with upload-sarif: you would get neither an error nor a SARIF report.) Set format to 'text' or 'json', or switch to mode: compare for SARIF output."
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
      elif [[ "$FORMAT" != "json" && "$FORMAT" != "markdown" && "$FORMAT" != "sarif" \
            && "$FORMAT" != "html" && "$FORMAT" != "junit" && "$FORMAT" != "review" ]]; then
        _fail "mode: compare does not support format: $FORMAT — only 'json', 'markdown', 'sarif', 'html', 'junit', and 'review' are supported."
      fi
    fi
    ;;
  *)
    # An unrecognized mode (e.g. a typo like 'scna') has no arm above, so
    # without this catch-all the case falls through silently and every
    # other check in this script is skipped -- Python setup, dependency
    # install, and pip install would all still run before run.sh's own
    # "Unknown mode" check finally reports it. Mirrors run.sh's message
    # verbatim.
    _fail "Unknown mode '$MODE'. Use 'compare', 'dump', 'scan', 'deps-tree', or 'deps-compare'."
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

# debug-info1/2, devel-pkg1/2, dso-only, include-private-dso, keep-extracted,
# fail-on-removed-library, jobs: compare mode, directory/package operands only
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

_bool_input_names=(dso-only include-private-dso keep-extracted fail-on-removed-library)
_bool_input_values=(
  "${INPUT_DSO_ONLY:-false}"
  "${INPUT_INCLUDE_PRIVATE_DSO:-false}"
  "${INPUT_KEEP_EXTRACTED:-false}"
  "${INPUT_FAIL_ON_REMOVED_LIBRARY:-false}"
)
for _i in "${!_bool_input_names[@]}"; do
  if [[ "${_bool_input_values[$_i]}" == "true" ]] && { [[ "$MODE" != "compare" ]] || [[ "$_RELEASE_STYLE_OPERAND" != "true" ]]; }; then
    _warn "${_bool_input_names[$_i]} is set but has no effect: it only applies to mode: compare with a directory/package old-library/new-library operand (mode is '$MODE')."
  fi
done

_JOBS="${INPUT_JOBS:-0}"
if [[ "$_JOBS" != "0" ]] && { [[ "$MODE" != "compare" ]] || [[ "$_RELEASE_STYLE_OPERAND" != "true" ]]; }; then
  _warn "jobs is set but has no effect: it only applies to mode: compare with a directory/package old-library/new-library operand (mode is '$MODE')."
fi

# used-by/verify-runtime/required-symbol/required-symbols: compare mode only
# (ADR-043 scoped-comparison contracts). --used-by and --required-symbol/
# --required-symbols are mutually exclusive on the CLI itself, but that
# UsageError only surfaces after Python setup/dependency install/pip
# install -- fail here instead, before any of that, matching this script's
# whole reason for existing (G30 P1.3, resolving the S22/S23 root-Action
# gap: check-target's kind: app-consumer/plugin-contract route through
# these two flags).
_USED_BY="${INPUT_USED_BY:-}"
_REQUIRED_SYMBOL="${INPUT_REQUIRED_SYMBOL:-}"
_REQUIRED_SYMBOLS="${INPUT_REQUIRED_SYMBOLS:-}"
if [[ -n "$_USED_BY" && ( -n "$_REQUIRED_SYMBOL" || -n "$_REQUIRED_SYMBOLS" ) ]]; then
  _fail "used-by is mutually exclusive with required-symbol/required-symbols -- set only one contract per check."
fi
_scoped_input_names=(used-by verify-runtime required-symbol required-symbols)
_scoped_input_values=("$_USED_BY" "${INPUT_VERIFY_RUNTIME:-false}" "$_REQUIRED_SYMBOL" "$_REQUIRED_SYMBOLS")
_scoped_input_unset_values=("" "false" "" "")
for _i in "${!_scoped_input_names[@]}"; do
  if [[ "${_scoped_input_values[$_i]}" != "${_scoped_input_unset_values[$_i]}" && "$MODE" != "compare" ]]; then
    _warn "${_scoped_input_names[$_i]} is set but has no effect: it only applies to mode: compare (mode is '$MODE')."
  fi
done

# abi-baseline: compare mode (used as old-library) or scan mode (used as the
# scan baseline) only.
_ABI_BASELINE="${INPUT_ABI_BASELINE:-}"
if [[ -n "$_ABI_BASELINE" && "$MODE" != "compare" && "$MODE" != "scan" ]]; then
  _warn "abi-baseline is set but has no effect: it only applies to mode: compare or mode: scan (mode is '$MODE')."
fi

# estimate, audit: deprecated scan-mode-only aliases.
if [[ "${INPUT_ESTIMATE:-false}" == "true" && "$MODE" != "scan" ]]; then
  _warn "estimate is set but has no effect: it only applies to mode: scan (mode is '$MODE')."
fi
if [[ "${INPUT_AUDIT:-false}" == "true" && "$MODE" != "scan" ]]; then
  _warn "audit is set but has no effect: it only applies to mode: scan (mode is '$MODE')."
fi

if [[ "$UPLOAD_SARIF" == "true" && "$MODE" != "compare" ]]; then
  _fail "upload-sarif is only meaningful with mode: compare (single-pair operands) — mode: $MODE never produces a SARIF report to upload. Remove upload-sarif, or switch to mode: compare."
fi

if [[ "$UPLOAD_SARIF" == "true" && "$FORMAT" != "sarif" ]]; then
  _fail "upload-sarif requires format: sarif (got '${FORMAT:-markdown}') — without it there is no SARIF report for the upload-sarif step to find."
fi
