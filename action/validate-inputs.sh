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

if [[ "$UPLOAD_SARIF" == "true" && "$MODE" != "compare" ]]; then
  _fail "upload-sarif is only meaningful with mode: compare (single-pair operands) — mode: $MODE never produces a SARIF report to upload. Remove upload-sarif, or switch to mode: compare."
fi

if [[ "$UPLOAD_SARIF" == "true" && "$FORMAT" != "sarif" ]]; then
  _fail "upload-sarif requires format: sarif (got '${FORMAT:-markdown}') — without it there is no SARIF report for the upload-sarif step to find."
fi
