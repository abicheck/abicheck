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
NEW_LIBRARY_SET="${INPUT_NEW_LIBRARY_SET:-}"
OLD_LIBRARY="${INPUT_OLD_LIBRARY:-}"
UPLOAD_SARIF="${INPUT_UPLOAD_SARIF:-false}"
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
  scan)
    # new-library-set (ADR-056, --artifact-set) is mutually exclusive with
    # new-library (the single-artifact positional) -- carved out first so
    # the directory/package rejection just below only ever applies to a
    # bare new-library value, never to the dedicated set input (which is
    # allowed to be a directory).
    if [[ -n "$NEW_LIBRARY" && -n "$NEW_LIBRARY_SET" ]]; then
      _fail "mode: scan cannot take both new-library and new-library-set -- new-library-set audits a *set* of libraries with no old side (ADR-056), new-library scans exactly one artifact (optionally against/abi-baseline). Set only one."
    fi
    if [[ -n "$NEW_LIBRARY_SET" ]]; then
      # --artifact-set is audit-only at the CLI level too (no old side for
      # a set) -- fail here, before Python setup/dependency install, rather
      # than let the CLI's own UsageError surface only after that (same
      # fail-fast rationale as every other check in this script).
      if [[ -n "${INPUT_AGAINST:-}" || -n "${INPUT_ABI_BASELINE:-}" ]]; then
        _fail "mode: scan with new-library-set does not support against/abi-baseline -- new-library-set is audit-only (no old side to compare a set against, ADR-056). Remove against/abi-baseline, or use new-library (a single artifact) for a baseline comparison instead."
      fi
      # --artifact-set dry-run/estimate IS implemented (CLI cleanup phase
      # two, PR 5: cli_scan._run_artifact_set previews the set instead of
      # rejecting --dry-run outright) -- no preflight rejection needed
      # here any more; dry-run: true + new-library-set (and the deprecated
      # estimate: true alias, which run.sh converts to INPUT_DRY_RUN=true
      # downstream) both reach the real dry-run path in run.sh.
      # cli_scan._run_artifact_set rejects old=/new= header/include scoping
      # outright (no old side for a set) -- old-header/old-include are
      # meaningless here; run.sh maps new-header/new-include to bare flags
      # instead (Codex review, keeping this script and run.sh synchronized).
      if [[ -n "${INPUT_OLD_HEADER:-}" || -n "${INPUT_OLD_INCLUDE:-}" ]]; then
        _fail "mode: scan with new-library-set does not support old-header/old-include -- new-library-set is audit-only (no old side, ADR-056)."
      fi
    elif [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; then
      _fail "mode: scan does not accept a directory or package for new-library ('$NEW_LIBRARY') — scan analyses exactly one artifact (a binary or a JSON snapshot), it has no per-library fan-out. Point new-library at a single library, use new-library-set to audit a set with no old side, or use mode: compare against a directory/package for a multi-library binary comparison."
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
    # The L2 compile-context inputs (ast-frontend/gcc-*/sysroot/nostdinc)
    # are rejected outright by run.sh for a directory/package operand — the
    # per-library release fan-out never threads a CompileContext to each
    # pair's header dump — so mirror that check here too (Codex review):
    # without it, a workflow with a slow dependency-install step still
    # passes this fail-fast validation and only errors after setup begins,
    # reopening the exact silent-fallback-until-late-failure bug this
    # script exists to prevent. "auto" is the documented no-op spelling of
    # ast-frontend (same default resolution as leaving it unset) and must
    # not trip this the way a real frontend choice does — mirrors run.sh.
    if { [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; } \
       || { [[ -n "$OLD_LIBRARY" ]] && _is_release_style_operand "$OLD_LIBRARY"; }; then
      if [[ (-n "$AST_FRONTEND" && "$AST_FRONTEND" != "auto") \
            || -n "$GCC_PATH" || -n "$GCC_PREFIX" || -n "$GCC_OPTIONS" \
            || -n "$SYSROOT" || "$NOSTDINC" == "true" ]]; then
        _fail "mode: compare with a directory/package operand (old-library='$OLD_LIBRARY', new-library='$NEW_LIBRARY') does not support ast-frontend/gcc-path/gcc-prefix/gcc-options/sysroot/nostdinc -- the per-library fan-out never threads the L2 compile context to each pair's header dump, so the requested context would silently never be applied and headers could be parsed under the wrong macros/sysroot/frontend. Compare the libraries individually (mode: compare with single-file operands) to use them."
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

# used-by/required-symbol/required-symbols: compare mode only
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
_scoped_input_names=(used-by required-symbol required-symbols)
_scoped_input_values=("$_USED_BY" "$_REQUIRED_SYMBOL" "$_REQUIRED_SYMBOLS")
_scoped_input_unset_values=("" "" "")
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
   && "$MODE" != "compare" && "$MODE" != "scan" ]]; then
  _warn "baseline-profile/baseline-target/baseline-asset-name-template are set but have no effect: they only apply to mode: compare or mode: scan (mode is '$MODE')."
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

# public-header-dir: dump and scan modes only (the CLI's own
# --public-header-dir flag exists on those two subcommands only; compare has
# no equivalent). run.sh's compare/deps-tree/deps-compare branches never
# forward it, so a caller setting it there would have the input silently
# discarded without this warning (Codex review).
_PUBLIC_HEADER_DIR="${INPUT_PUBLIC_HEADER_DIR:-}"
if [[ -n "$_PUBLIC_HEADER_DIR" && "$MODE" != "dump" && "$MODE" != "scan" ]]; then
  _warn "public-header-dir is set but has no effect: it only applies to mode: dump or mode: scan (mode is '$MODE')."
fi

# build-target: dump and scan modes only, same restriction and reasoning as
# public-header-dir directly above (the CLI's own --build-target flag exists
# on those two subcommands only; compare has no equivalent). run.sh's
# compare/deps-tree/deps-compare branches never forward it (Codex review).
_BUILD_TARGET="${INPUT_BUILD_TARGET:-}"
if [[ -n "$_BUILD_TARGET" && "$MODE" != "dump" && "$MODE" != "scan" ]]; then
  _warn "build-target is set but has no effect: it only applies to mode: dump or mode: scan (mode is '$MODE')."
fi

# new-library-set: scan mode only (ADR-056). The scan-mode arm above already
# fails outright on an invalid combination (new-library also set, or
# against/abi-baseline also set) -- this only covers the inert case (set on
# a different mode entirely).
if [[ -n "$NEW_LIBRARY_SET" && "$MODE" != "scan" ]]; then
  _warn "new-library-set is set but has no effect: it only applies to mode: scan (mode is '$MODE')."
fi

# bundle-system-providers: the cross-library bundle-analysis layer, reached
# by mode: compare (directory/package operands) and mode: scan (only with
# new-library-set) -- inert everywhere else, including a mode: scan run
# that uses the ordinary new-library input: run.sh's scan branch only
# forwards --bundle-system-providers inside the new-library-set branch, so
# a scalar scan silently drops it rather than erroring -- without this
# check the Action succeeds while quietly discarding the caller's setting
# (Codex review).
if [[ -n "${INPUT_BUNDLE_SYSTEM_PROVIDERS:-}" ]]; then
  if [[ "$MODE" != "compare" && "$MODE" != "scan" ]]; then
    _warn "bundle-system-providers is set but has no effect: it only applies to mode: compare or mode: scan (mode is '$MODE')."
  elif [[ "$MODE" == "scan" && -z "$NEW_LIBRARY_SET" ]]; then
    _warn "bundle-system-providers is set but has no effect: with mode: scan it only applies when new-library-set is also set (a scalar new-library scan has no bundle-analysis layer to extend)."
  elif [[ "$MODE" == "compare" && ( -n "$NEW_LIBRARY" || -n "$OLD_LIBRARY" ) ]] \
    && ! { { [[ -n "$NEW_LIBRARY" ]] && _is_release_style_operand "$NEW_LIBRARY"; } \
         || { [[ -n "$OLD_LIBRARY" ]] && _is_release_style_operand "$OLD_LIBRARY"; }; }; then
    # cli_compare_helpers.py only reaches bundle analysis on the
    # directory/package dispatch -- a scalar (single-file) compare
    # operand pair silently discards this the same way a scalar scan
    # does (Codex review). Only fires once an operand is actually set --
    # compare's own required-input check is what should own an entirely
    # missing old-library/new-library, not this warning.
    _warn "bundle-system-providers is set but has no effect: with mode: compare it only applies to a directory/package operand (old-library='$OLD_LIBRARY', new-library='$NEW_LIBRARY') -- a single-file compare has no bundle-analysis layer to extend."
  fi
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

# build-info + compile-db, scan mode: the same conflict `action/run.sh`
# rejects when it assembles argv, checked here so it fails before Python
# setup, dependency install and toolchain provisioning -- this script's whole
# reason for existing (Codex review). Kept scan-only for the same reason
# run.sh keeps it scan-only: scan is the mode whose behavior changed, having
# forwarded both operands and preferred compile-db, while compare and dump
# have always resolved this pair by the documented build-info-wins fallback.
if [[ "$MODE" == "scan" && -n "${INPUT_BUILD_INFO:-}" && -n "${INPUT_COMPILE_DB:-}" ]]; then
  _fail "build-info ('${INPUT_BUILD_INFO}') and compile-db ('${INPUT_COMPILE_DB}') are both set for mode: scan, but they now name the same operand -- abicheck's scan --compile-db flag was removed and --build-info accepts a build directory, a compile_commands.json, or a pre-captured pack. scan previously took both and preferred compile-db, so keeping only one silently would change which build context is analyzed. Set exactly one (a compile_commands.json path is a valid build-info value)."
fi
