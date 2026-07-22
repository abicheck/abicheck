#!/usr/bin/env bash
# Main entrypoint for the abicheck GitHub Action.
# Assembles the CLI command from INPUT_* environment variables,
# runs abicheck, captures the exit code, and sets outputs.
set -uo pipefail

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
    for item in $value; do
      CMD+=("$flag" "$item")
    done
  fi
}

# ADR-040 L1: the per-side header/include inputs map to the side-aware --header/
# --include flags, prefixing each value with old=/new= (e.g. --header old=inc).
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
    for item in $value; do
      CMD+=("$flag" "${side}=${item}")
    done
  fi
}

add_single_flag() {
  local flag="$1"
  local value="$2"
  if [[ -n "$value" ]]; then
    CMD+=("$flag" "$value")
  fi
}

# A directory, a file whose name matches a recognized package extension, or
# an extensionless RPM/Deb detected by magic bytes (mirrors package.py's
# is_package(), including its magic-byte fallback — abicheck/package.py:547-554
# — since classify_compare_operand() delegates to it regardless of filename;
# a name-suffix-only check here would still let the Action add
# --secondary-format for such an operand and have the CLI reject it, Codex
# review, PR #557). `compare` fans such an operand out through the release
# engine internally regardless of the Action's MODE, and the release engine
# rejects --secondary-format — used to skip the --secondary-format
# optimization for compare mode's PR-comment JSON rather than let it
# hard-fail a directory/package comparison that used to work.
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
# Build the abicheck command
# ---------------------------------------------------------------------------
CMD=(abicheck)

MODE="${INPUT_MODE:-compare}"

# ---------------------------------------------------------------------------
# Back-compat aliases: `estimate`/`audit` (pre-dry-run/scan-reshape inputs,
# Codex review). Removing these outright (rather than keeping them as
# functional aliases, like the existing `allow-build-query` no-op above)
# would silently break existing workflows that still set them: GitHub
# Actions drops an input the action.yml no longer declares with only a
# warning, so `estimate: true` would otherwise silently run a real scan
# instead of the preview it used to produce, and `audit: true` would
# silently stop forcing a baseline-less hygiene lint once a
# baseline/abi-baseline is configured -- a much worse failure mode than a
# hard error, since nothing signals that the step is no longer doing what
# the workflow author intended.
# ---------------------------------------------------------------------------
if [[ "$MODE" == "scan" && "${INPUT_ESTIMATE:-false}" == "true" ]]; then
  INPUT_DRY_RUN="true"
fi
FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"

# ---------------------------------------------------------------------------
# Baseline auto-fetch: resolve INPUT_ABI_BASELINE → INPUT_OLD_LIBRARY
#
# A fetch failure (missing release/token/asset) reports and continues rather
# than exiting 1 under --dry-run: dry-run is documented as "always exits 0"
# (action.yml), but this block runs before any mode branch ever consults
# INPUT_DRY_RUN, so an unavailable baseline used to hard-fail a preview run
# before it ever got the chance to no-op (Codex review). BASELINE_FILE is
# left unset in that case; the mode branches' existing required-input checks
# still apply if no other old-library/against source was given.
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

ABI_BASELINE="${INPUT_ABI_BASELINE:-}"
if [[ -n "$ABI_BASELINE" \
   && ( "$MODE" == "compare" || "$MODE" == "scan" ) \
   && ! ( "$MODE" == "scan" && "$FORCE_AUDIT_ONLY" == "true" ) ]]; then
  BASELINE_DIR=$(mktemp -d)
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
    if [[ "$ABI_BASELINE" == "latest-release" ]]; then
      echo "::group::Fetch ABI baseline from latest release"
      # ${arr[@]+"${arr[@]}"}, not a bare "${arr[@]}": under macOS's stock
      # (GPLv2-frozen) bash 3.2's set -u, expanding an *empty* array as
      # "${arr[@]}" is itself treated as an unbound-variable reference (bash
      # 4.4+ special-cased this away) -- the same portability trap
      # add_flag()'s callers already guard against elsewhere in this file
      # (Codex review).
      if ! gh release download ${_GH_REPO_FLAG[@]+"${_GH_REPO_FLAG[@]}"} --pattern '*.abicheck.json' -D "$BASELINE_DIR"; then
        _baseline_unavailable "No ABI baseline found in latest release. Run 'abicheck dump path/to/libfoo.so -o libfoo.abicheck.json' in your release workflow and upload the resulting *.abicheck.json file as a release asset."
      fi
      echo "::endgroup::"
    else
      # Treat as a tag name
      echo "::group::Fetch ABI baseline from release $ABI_BASELINE"
      if ! gh release download "$ABI_BASELINE" ${_GH_REPO_FLAG[@]+"${_GH_REPO_FLAG[@]}"} --pattern '*.abicheck.json' -D "$BASELINE_DIR"; then
        _baseline_unavailable "No ABI baseline found in release '$ABI_BASELINE'. Ensure the release has a *.abicheck.json asset."
      fi
      echo "::endgroup::"
    fi
    # Require exactly one *.abicheck.json in the download dir: `head -1`
    # picking an arbitrary match on a multi-asset release could silently
    # compare against the wrong library and produce an invalid verdict
    # (Codex review). Built via a while/read loop (not `mapfile`, a bash 4+
    # builtin) for macOS's stock (GPLv2-frozen) bash 3.2, same portability
    # constraint as add_flag() above. An empty result also covers the
    # download itself failing and _baseline_unavailable returning instead
    # of exiting (e.g. under --dry-run).
    BASELINE_FILES=()
    while IFS= read -r _found; do
      [[ -n "$_found" ]] && BASELINE_FILES+=("$_found")
    done <<< "$(find "$BASELINE_DIR" -name '*.abicheck.json' 2>/dev/null)"
    if [[ ${#BASELINE_FILES[@]} -eq 1 ]]; then
      BASELINE_FILE="${BASELINE_FILES[0]}"
    elif [[ ${#BASELINE_FILES[@]} -eq 0 ]]; then
      _baseline_unavailable "No *.abicheck.json file found after download."
    else
      _baseline_unavailable "Multiple *.abicheck.json assets found (${BASELINE_FILES[*]}); ambiguous which is the baseline. Publish exactly one *.abicheck.json asset per release, or pass abi-baseline a direct file path instead."
    fi
  fi
  if [[ -n "$BASELINE_FILE" ]]; then
    echo "Using ABI baseline: $BASELINE_FILE"
    # compare consumes the baseline as old-library; scan consumes it as --against.
    if [[ "$MODE" == "scan" ]]; then
      INPUT_AGAINST="$BASELINE_FILE"
    else
      INPUT_OLD_LIBRARY="$BASELINE_FILE"
    fi
  elif [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    # The fetch was tolerated above (dry-run never hard-fails on it), but if
    # no other old-library/against was independently given there is nothing
    # left to preview -- report and stop here rather than falling through to
    # `${INPUT_OLD_LIBRARY:?...}` below, whose bash parameter-expansion abort
    # would itself violate the documented "dry-run always exits 0" contract.
    if [[ "$MODE" == "scan" && -z "${INPUT_AGAINST:-}" ]] ||
       [[ "$MODE" == "compare" && -z "${INPUT_OLD_LIBRARY:-}" ]]; then
      echo "::notice::--dry-run: no ABI baseline could be resolved and no other old-library/against was given, so there is nothing to preview."
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
  add_flag "-I" "${INPUT_INCLUDE:-}"
  add_flag "-I" "${INPUT_NEW_INCLUDE:-}"
  add_single_flag "--version" "${INPUT_NEW_VERSION:-}"
  add_single_flag "--lang" "${INPUT_LANG:-}"
  add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"
  add_single_flag "--gcc-path" "${INPUT_GCC_PATH:-}"
  add_single_flag "--gcc-prefix" "${INPUT_GCC_PREFIX:-}"
  add_single_flag "--gcc-options" "${INPUT_GCC_OPTIONS:-}"
  add_single_flag "--sysroot" "${INPUT_SYSROOT:-}"

  if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then
    CMD+=(--nostdinc)
  fi

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
  add_single_flag "--config" "${INPUT_BUILD_CONFIG:-}"
  add_single_flag "--depth" "${INPUT_DEPTH:-}"
  if [[ "${INPUT_ALLOW_BUILD_QUERY:-false}" == "true" ]]; then
    CMD+=(--allow-build-query)
  fi

  # dry-run performs no analysis and writes nothing, so it is mutually
  # exclusive with -o/--output on the CLI -- skip the output file entirely
  # when set, rather than passing both and letting the CLI reject it.
  if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    CMD+=(--dry-run)
  else
    # Output file — required for dump in action context (otherwise stdout)
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-abicheck-baseline.json}"
    CMD+=(-o "$OUTPUT_FILE")
  fi

elif [[ "$MODE" == "compare" ]]; then
  # ── Compare mode ─────────────────────────────────────────────────────────
  # old-library/new-library may be single binaries/snapshots, or directories/
  # packages — the `compare` CLI command fans out to a per-library comparison
  # automatically in the latter case (ADR-037 D7), so this one branch covers
  # both; the package-specific options below are simply ignored (with a
  # stderr warning from the CLI) when the operands are a single pair.
  CMD+=(compare)
  CMD+=("${INPUT_OLD_LIBRARY:?old-library is required for compare mode}")
  CMD+=("${INPUT_NEW_LIBRARY:?new-library is required}")

  add_flag "-H" "${INPUT_HEADER:-}"
  add_sided_flag "--header" "old" "${INPUT_OLD_HEADER:-}"
  add_sided_flag "--header" "new" "${INPUT_NEW_HEADER:-}"
  add_flag "-I" "${INPUT_INCLUDE:-}"
  add_sided_flag "--include" "old" "${INPUT_OLD_INCLUDE:-}"
  add_sided_flag "--include" "new" "${INPUT_NEW_INCLUDE:-}"
  add_sided_flag "--version" "old" "${INPUT_OLD_VERSION:-}"
  add_sided_flag "--version" "new" "${INPUT_NEW_VERSION:-}"
  add_single_flag "--lang" "${INPUT_LANG:-}"
  add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"

  # Format — for SARIF, always write to a file so upload-sarif can find it.
  # sarif/html are rejected by the CLI itself (a clear UsageError, exit 64)
  # when the operands are directories/packages — surfaced as VERDICT=ERROR
  # below via the generic CLI-error detection, no separate fallback needed.
  FORMAT="${INPUT_FORMAT:-markdown}"
  CMD+=(--format "$FORMAT")

  # dry-run performs no analysis and writes nothing, so it is mutually
  # exclusive with -o/--output AND --secondary-output/--secondary-format on
  # the CLI -- skip both entirely when set, rather than passing them and
  # letting the CLI reject the combination.
  DRY_RUN="${INPUT_DRY_RUN:-false}"
  if [[ "$DRY_RUN" == "true" ]]; then
    CMD+=(--dry-run)
  else
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-}"
    if [[ "$FORMAT" == "sarif" && -z "$OUTPUT_FILE" ]]; then
      OUTPUT_FILE="abicheck-results.sarif"
    fi
    if [[ -n "$OUTPUT_FILE" ]]; then
      CMD+=(-o "$OUTPUT_FILE")
    fi

    # Render a second, always-unfiltered JSON report from this same run for
    # the sticky PR comment (--secondary-format), instead of re-invoking
    # abicheck a second time just to get JSON. Only needed when the primary
    # format isn't already JSON — a json primary is reused as-is (see
    # _can_reuse_primary_json below). The per-library release fan-out
    # (directory/package operands) rejects --secondary-format, so it's
    # skipped there too, falling back to the rerun path in
    # _maybe_post_pr_comment (Codex review).
    if [[ "$FORMAT" != "json" ]] \
       && ! _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
       && ! _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
      PR_JSON=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-pr-json.XXXXXX")
      CMD+=(--secondary-format json --secondary-output "$PR_JSON")
    fi
  fi

  add_single_flag "--policy" "${INPUT_POLICY:-}"
  add_single_flag "--policy-file" "${INPUT_POLICY_FILE:-}"
  add_single_flag "--suppress" "${INPUT_SUPPRESS:-}"

  # Severity configuration
  add_single_flag "--severity-preset" "${INPUT_SEVERITY_PRESET:-}"
  add_single_flag "--severity-addition" "${INPUT_SEVERITY_ADDITION:-}"

  if [[ "${INPUT_FOLLOW_DEPS:-false}" == "true" ]]; then
    CMD+=(--follow-deps)
    add_flag "--search-path" "${INPUT_SEARCH_PATH:-}"
    add_single_flag "--ld-library-path" "${INPUT_LD_LIBRARY_PATH:-}"
  fi

  # Scoped comparison (ADR-043): --used-by/--required-symbol(s) contracts.
  # The CLI itself enforces --used-by vs --required-symbol/--required-symbols
  # mutual exclusivity (a UsageError, surfaced as VERDICT=ERROR below via the
  # generic CLI-error detection) -- not re-validated here.
  add_flag "--used-by" "${INPUT_USED_BY:-}"
  if [[ "${INPUT_VERIFY_RUNTIME:-false}" == "true" ]]; then
    CMD+=(--verify-runtime)
  fi
  add_flag "--required-symbol" "${INPUT_REQUIRED_SYMBOL:-}"
  add_single_flag "--required-symbols" "${INPUT_REQUIRED_SYMBOLS:-}"

  # Note: --gcc-path, --gcc-prefix, --gcc-options, --sysroot, --nostdinc are
  # dump-only flags. In compare mode abicheck performs the dump internally
  # when an input is a binary, but these cross-compilation flags are not
  # exposed on the compare CLI. They are only passed in dump mode.

  # Package-specific options — only meaningful (and only forwarded) when
  # old-library/new-library are directories or packages; gated here rather
  # than left to the CLI's own single-file warning so a plain single-pair
  # compare doesn't get a spurious "-j/--jobs ignored" warning on every run
  # just because jobs defaults to '0'.
  if _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
     || _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
    add_sided_flag "--debug-info" "old" "${INPUT_DEBUG_INFO1:-}"
    add_sided_flag "--debug-info" "new" "${INPUT_DEBUG_INFO2:-}"
    add_sided_flag "--devel-pkg" "old" "${INPUT_DEVEL_PKG1:-}"
    add_sided_flag "--devel-pkg" "new" "${INPUT_DEVEL_PKG2:-}"

    if [[ "${INPUT_DSO_ONLY:-false}" == "true" ]]; then
      CMD+=(--dso-only)
    fi
    if [[ "${INPUT_INCLUDE_PRIVATE_DSO:-false}" == "true" ]]; then
      CMD+=(--include-private-dso)
    fi
    if [[ "${INPUT_KEEP_EXTRACTED:-false}" == "true" ]]; then
      CMD+=(--keep-extracted)
    fi
    if [[ "${INPUT_FAIL_ON_REMOVED_LIBRARY:-false}" == "true" ]]; then
      CMD+=(--fail-on-removed-library)
    fi
    add_single_flag "--jobs" "${INPUT_JOBS:-0}"
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
  # anything else (sarif), not a silent fallback — see the scan branch's
  # format check above.
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
  # anything else (sarif), not a silent fallback — see the scan branch's
  # format check above.
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

elif [[ "$MODE" == "scan" ]]; then
  # ── Scan mode (source-intelligence orchestrator) ─────────────────────────
  # One front-end over dump/compare: always-on pattern + cross-source tier,
  # then the pinned evidence level, optionally compared against --against.
  # ARTIFACT is a positional argument (not --binary); absence of --against is
  # already a one-build audit, presence of --against is already audit+compare
  # — there is no separate --audit/--mode/--source-method/--estimate flag any
  # more (CLI simplification).
  CMD+=(scan)
  SCAN_ARTIFACT="${INPUT_NEW_LIBRARY:?new-library (the scanned binary or .abi.json) is required for scan mode}"
  # scan has no per-library fan-out (unlike compare) — a directory/package
  # is normally caught early by action/validate-inputs.sh, before any
  # dependency install; re-checked here for anyone invoking run.sh directly
  # (e.g. tests) without that step.
  if _is_release_style_operand "$SCAN_ARTIFACT"; then
    echo "::error::mode: scan does not accept a directory or package for new-library ('$SCAN_ARTIFACT') — scan analyses exactly one artifact. Use mode: compare against a directory/package for a multi-library binary comparison instead."
    exit 1
  fi
  CMD+=("$SCAN_ARTIFACT")

  # -H/-I are side-aware on scan: a bare value applies to both ARTIFACT and
  # the --against side; old-header/old-include and new-header/new-include
  # scope to one side only (ADR-040 L1) so a candidate-only header doesn't
  # leak into the baseline side's parse (Codex review).
  add_flag "-H" "${INPUT_HEADER:-}"
  add_sided_flag "-H" "old" "${INPUT_OLD_HEADER:-}"
  add_sided_flag "-H" "new" "${INPUT_NEW_HEADER:-}"
  add_flag "-I" "${INPUT_INCLUDE:-}"
  add_sided_flag "-I" "old" "${INPUT_OLD_INCLUDE:-}"
  add_sided_flag "-I" "new" "${INPUT_NEW_INCLUDE:-}"

  # Build-source evidence inputs (L3/L4/L5)
  add_single_flag "--sources" "${INPUT_SOURCES:-}"
  add_single_flag "--build-info" "${INPUT_BUILD_INFO:-}"
  add_single_flag "--compile-db" "${INPUT_COMPILE_DB:-}"
  # scan's config flag is --config (not --build-config, which does not exist on
  # scan and hard-fails with exit 64). dump uses --config for the same input.
  add_single_flag "--config" "${INPUT_BUILD_CONFIG:-}"
  # Omitting --against is already a one-build audit-only run; the preferred
  # way to force one for a single step is to simply not set against/
  # abi-baseline there. The deprecated `audit: true` back-compat alias
  # (above) achieves the same by skipping --against outright even when
  # against/abi-baseline resolved to a value elsewhere in the workflow.
  if [[ "$FORCE_AUDIT_ONLY" != "true" ]]; then
    add_single_flag "--against" "${INPUT_AGAINST:-}"
  fi
  add_single_flag "--lang" "${INPUT_LANG:-}"

  # Level selection — the modern --depth dial (omit for 'auto'). The deprecated
  # --mode/--source-method passthrough was removed; use depth.
  add_single_flag "--depth" "${INPUT_DEPTH:-}"

  # Focusing + guards + policy
  add_single_flag "--since" "${INPUT_SINCE:-}"
  add_flag "--changed-path" "${INPUT_CHANGED_PATH:-}"
  add_single_flag "--budget" "${INPUT_BUDGET:-}"
  add_single_flag "--risk-rules" "${INPUT_RISK_RULES:-}"
  add_flag "--crosscheck" "${INPUT_CROSSCHECK:-}"

  if [[ "${INPUT_ALLOW_BUILD_QUERY:-false}" == "true" ]]; then
    CMD+=(--allow-build-query)
  fi

  # Format — scan only supports text and json. Normally caught early by
  # action/validate-inputs.sh; re-checked here (hard error, not a silent
  # fallback — a fallback here used to make a misconfigured `format: sarif`
  # + `upload-sarif: true` scan step silently produce neither an error nor
  # a SARIF report) for anyone invoking run.sh directly without that step.
  FORMAT="${INPUT_FORMAT:-text}"
  if [[ "$FORMAT" != "text" && "$FORMAT" != "json" ]]; then
    echo "::error::mode: scan does not support format: $FORMAT. Only 'text' and 'json' are supported."
    exit 1
  fi
  CMD+=(--format "$FORMAT")

  # dry-run maps directly to --dry-run (the cost-projection formerly under
  # the separate --estimate flag is folded into the general dry-run report).
  # A dry run writes nothing, so skip -o/--output entirely when it's set
  # (they are mutually exclusive on scan).
  if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    CMD+=(--dry-run)
  else
    OUTPUT_FILE="${INPUT_OUTPUT_FILE:-}"
    if [[ -n "$OUTPUT_FILE" ]]; then
      CMD+=(-o "$OUTPUT_FILE")
    fi
  fi

else
  echo "::error::Unknown mode '$MODE'. Use 'compare', 'dump', 'scan', 'deps-tree', or 'deps-compare'."
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
  # shellcheck disable=SC2206
  CMD+=($INPUT_EXTRA_ARGS)
fi

echo "::group::abicheck $MODE"
echo "Command: ${CMD[*]}"
echo ""

ABICHECK_EXIT=0
ABICHECK_OUTPUT=""
STDERR_FILE=$(mktemp)
trap 'rm -f "$STDERR_FILE"; rm -rf "${_BASELINE_CLEANUP:-}"' EXIT

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

_is_cli_error() {
  echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try |Traceback|click\.)'
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

elif [[ "$MODE" == "scan" ]]; then
  # scan exit codes: 0=compatible/advisory, 2=API break, 4=ABI break,
  # 5=budget overflow. Click usage errors also use exit 2 — distinguish via stderr.
  if [[ $ABICHECK_EXIT -eq 2 ]] && echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try )'; then
    VERDICT="ERROR"
    echo "::error::abicheck scan failed due to a CLI argument or configuration error (exit code 2)."
    echo "::error::Check the command and inputs above. This is NOT an API break — the scan did not run."
  else
    case $ABICHECK_EXIT in
      0) VERDICT="COMPATIBLE" ;;
      2) VERDICT="API_BREAK" ;;
      4) VERDICT="BREAKING" ;;
      5) VERDICT="BUDGET_OVERFLOW" ;;
      *)
        VERDICT="ERROR"
        if _is_cli_error; then
          echo "::error::abicheck scan failed due to a CLI error (exit code $ABICHECK_EXIT)."
        fi
        ;;
    esac
  fi

else
  # compare exit codes: 0=compatible, 1=severity error, 2=API_BREAK,
  # 4=BREAKING, 8=REMOVED_LIBRARY (directory/package operands with
  # fail-on-removed-library set). Click also uses exit code 2 for
  # usage/argument errors — detect via stderr.
  if [[ $ABICHECK_EXIT -eq 2 ]] && echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try )'; then
    VERDICT="ERROR"
    echo "::error::abicheck failed due to a CLI argument or configuration error (exit code 2)."
    echo "::error::Check the command and inputs above. This is NOT an API break — the check did not run."
  else
    case $ABICHECK_EXIT in
      0) VERDICT="COMPATIBLE" ;;
      1)
        if _is_cli_error; then
          VERDICT="ERROR"
          echo "::error::abicheck failed due to a CLI argument or configuration error (exit code 1)."
          echo "::error::Check the command and inputs above."
        else
          VERDICT="SEVERITY_ERROR"
        fi
        ;;
      2) VERDICT="API_BREAK" ;;
      4) VERDICT="BREAKING" ;;
      8) VERDICT="REMOVED_LIBRARY" ;;
      *) VERDICT="ERROR" ;;
    esac
  fi
fi

echo "abicheck verdict: $VERDICT (exit code $ABICHECK_EXIT)"

# ---------------------------------------------------------------------------
# Set outputs
# ---------------------------------------------------------------------------
{
  echo "verdict=$VERDICT"
  echo "exit-code=$ABICHECK_EXIT"
  # Only emit report-path when a real report file was produced
  if [[ -n "${OUTPUT_FILE:-}" && -f "${OUTPUT_FILE}" ]]; then
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
    if [[ "$MODE" == "scan" ]]; then
      echo "## abicheck Source-Intelligence Scan Report"
    else
      echo "## abicheck ABI Compatibility Report"
    fi
    echo ""

    case $VERDICT in
      COMPATIBLE)
        echo "> **Verdict: COMPATIBLE** — No binary ABI break detected."
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
        # --secondary-format/--secondary-output, so it's already populated
        # by this point without a second run (Codex review). Falls back to
        # the generic message when neither is available or jq is missing.
        _blocking_categories=""
        _json_src=""
        if [[ "${FORMAT:-}" == "json" && -n "${OUTPUT_FILE:-}" && -s "${OUTPUT_FILE:-}" ]]; then
          _json_src="${OUTPUT_FILE}"
        elif [[ -n "${PR_JSON:-}" && -s "${PR_JSON:-}" ]]; then
          _json_src="${PR_JSON}"
        fi
        if [[ -n "$_json_src" ]] && command -v jq >/dev/null 2>&1; then
          _blocking_categories=$(jq -r '(.severity.blocking_categories // []) | join(", ")' "$_json_src" 2>/dev/null)
        fi
        if [[ -n "$_blocking_categories" ]]; then
          echo "> **Verdict: SEVERITY_ERROR** ⚠️ — Blocked by severity policy: \`$_blocking_categories\` configured as \`error\`. This is a policy gate, not necessarily an ABI/API break — see the report below for each finding's actual compatibility."
        else
          echo "> **Verdict: SEVERITY_ERROR** ⚠️ — Severity-level issue detected (see severity configuration)."
        fi
        ;;
      API_BREAK)
        echo "> **Verdict: API_BREAK** — Source-level API break detected. Recompilation required."
        ;;
      BREAKING)
        echo "> **Verdict: BREAKING** — Binary ABI break detected. Existing binaries will fail at runtime."
        ;;
      REMOVED_LIBRARY)
        echo "> **Verdict: REMOVED_LIBRARY** — A library present in the old package is missing from the new package."
        ;;
      BUDGET_OVERFLOW)
        echo "> **Verdict: BUDGET_OVERFLOW** ⏱️ — Scan exceeded the configured \`budget\`. Pin a shallower level (--depth) or raise the budget; a budget never silently shrinks scope."
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
    esac

    echo ""
    echo "| Property | Value |"
    echo "|----------|-------|"
    if [[ "$MODE" == "compare" ]]; then
      echo "| Old | \`${INPUT_OLD_LIBRARY:-}\` (${INPUT_OLD_VERSION:-old}) |"
      echo "| New | \`${INPUT_NEW_LIBRARY:-}\` (${INPUT_NEW_VERSION:-new}) |"
      echo "| Policy | ${INPUT_POLICY:-strict_abi} |"
    elif [[ "$MODE" == "deps-compare" ]]; then
      echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
      echo "| Old root | \`${INPUT_OLD_ROOT:-}\` |"
      echo "| New root | \`${INPUT_NEW_ROOT:-}\` |"
    elif [[ "$MODE" == "scan" ]]; then
      echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
      if [[ -n "${INPUT_AGAINST:-}" ]]; then
        echo "| Against | \`${INPUT_AGAINST}\` |"
      fi
      if [[ -n "${INPUT_SOURCES:-}" ]]; then
        echo "| Sources | \`${INPUT_SOURCES}\` |"
      fi
      echo "| Depth | ${INPUT_DEPTH:-auto} |"
    elif [[ "$MODE" == "deps-tree" ]]; then
      echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
    fi
    echo "| Mode | $MODE |"
    echo "| Format | ${FORMAT:-markdown} |"
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
    if [[ -n "$ABICHECK_OUTPUT" ]]; then
      echo "<details>"
      echo "<summary>Full report</summary>"
      echo ""
      if [[ "${FORMAT:-markdown}" == "markdown" ]]; then
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
  # report. It must already be JSON, written to a non-empty file, and free of
  # display filters (--show-only / --stat) that hide gated changes from the
  # comment (which _build_json_cmd strips for exactly that reason).
  [[ "${FORMAT:-}" == "json" ]] || return 1
  [[ -n "${OUTPUT_FILE:-}" && -s "${OUTPUT_FILE:-}" ]] || return 1
  local arg
  for arg in ${CMD[@]+"${CMD[@]}"}; do
    case "$arg" in
      --show-only | --show-only=* | --stat) return 1 ;;
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
      --show-only)
        # Display filter ("limit displayed changes", does NOT affect exit codes).
        # Keeping it would hide gated breaks from the comment while the check
        # still fails red — drop it (and its value) so the comment sees the
        # full change set the gate acted on.
        ((i++))  # skip the flag's value too
        ;;
      --show-only=*)
        : # same display filter, inline value form — drop it for the re-run.
        ;;
      --stat)
        : # display-only flag (no value); it suppresses the changes array in
          # JSON, which the comment parser needs — drop it for the re-run.
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
  [[ "${INPUT_DRY_RUN:-false}" == "true" ]] && return 0
  [[ "${INPUT_PR_COMMENT_ON:-changes}" == "never" ]] && return 0
  [[ "$VERDICT" == "ERROR" ]] && return 0
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
    : # Already populated by the primary run's --secondary-format (compare
      # mode, non-json primary format) — nothing left to do.
  elif _can_reuse_primary_json; then
    # The primary run already produced a faithful JSON report — reuse it instead
    # of re-running the whole comparison.
    cp "$OUTPUT_FILE" "$PR_JSON"
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

  # Link the workflow run (where the full JSON/SARIF report is uploaded as an
  # artifact) so a condensed/truncated comment always points at the full detail.
  local run_url=""
  if [[ -n "${GITHUB_SERVER_URL:-}" && -n "${GITHUB_REPOSITORY:-}" && -n "${GITHUB_RUN_ID:-}" ]]; then
    run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
  fi

  python3 -m abicheck.cli_pr_comment "$PR_JSON" \
    --sha "${head_sha:-${GITHUB_SHA:-}}" \
    --detail "${INPUT_PR_COMMENT_DETAIL:-standard}" \
    --on "${INPUT_PR_COMMENT_ON:-changes}" \
    --run-label "run #${GITHUB_RUN_NUMBER:-?}" \
    ${run_url:+--report-url "$run_url"} \
    ${PR_GATE_ARGS[@]+"${PR_GATE_ARGS[@]}"} \
    -o "$PR_BODY" || true

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

_maybe_post_pr_comment

# ---------------------------------------------------------------------------
# Determine final exit code based on user preferences
# ---------------------------------------------------------------------------
FINAL_EXIT=0

if [[ "$VERDICT" == "ERROR" ]]; then
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

elif [[ "$MODE" == "scan" ]]; then
  # scan: BREAKING/API_BREAK follow the fail-on flags; a budget overflow always
  # fails the step (the budget is a guard that must not be silently swallowed).
  if [[ "$VERDICT" == "BREAKING" && "${INPUT_FAIL_ON_BREAKING:-true}" == "true" ]]; then
    echo "::error::ABI break detected by scan. Set fail-on-breaking: false to continue despite breaks."
    FINAL_EXIT=1
  fi

  # API_BREAK (scan exit 2) covers baseline/source API breaks AND a cross-check
  # the user promoted with --crosscheck KEY=error (the scan CLI maps both to
  # exit 2). They share one tier, so fail-on-api-break gates them uniformly — we
  # cannot tell from the exit code alone whether a promoted check fired, so
  # keying off the crosscheck flag would wrongly fail an unrelated API break
  # when fail-on-api-break is false (Codex review).
  if [[ "$VERDICT" == "API_BREAK" && "${INPUT_FAIL_ON_API_BREAK:-false}" == "true" ]]; then
    echo "::error::API/source break detected by scan (includes promoted --crosscheck=error gates). Set fail-on-api-break: false to ignore."
    FINAL_EXIT=1
  fi

  if [[ "$VERDICT" == "BUDGET_OVERFLOW" ]]; then
    echo "::error::Scan exceeded its budget. Pin a shallower level or raise the budget."
    FINAL_EXIT=1
  fi

else
  # compare mode: BREAKING/API_BREAK follow fail-on flags; REMOVED_LIBRARY
  # only appears when --fail-on-removed-library was passed to the CLI
  # (directory/package operands only).
  if [[ "$VERDICT" == "BREAKING" && "${INPUT_FAIL_ON_BREAKING:-true}" == "true" ]]; then
    echo "::error::ABI break detected. Set fail-on-breaking: false to continue despite breaks."
    FINAL_EXIT=1
  fi

  if [[ "$VERDICT" == "API_BREAK" && "${INPUT_FAIL_ON_API_BREAK:-false}" == "true" ]]; then
    echo "::error::API break detected. Set fail-on-api-break: false to ignore API-level breaks."
    FINAL_EXIT=1
  fi

  if [[ "$VERDICT" == "REMOVED_LIBRARY" ]]; then
    echo "::error::Library removed between old and new package. Set fail-on-removed-library: false to allow."
    FINAL_EXIT=1
  fi

  # Severity-driven exit code 1 (from --severity-* flags)
  if [[ "$VERDICT" == "SEVERITY_ERROR" ]]; then
    echo "::error::Severity-level error detected by abicheck."
    FINAL_EXIT=1
  fi
fi

exit $FINAL_EXIT
