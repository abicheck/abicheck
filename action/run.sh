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

# Resolved once, up front, so every code path below (including the
# baseline-set fallback further down, which needs it before this script's
# own later _report_query definition would otherwise provide it) can use
# it. On a Windows runner, actions/setup-python may expose only
# python.exe/`python` to Git Bash, not `python3` -- an unconditional
# `python3` call in a fallback path that only exercises when a release has
# no single-snapshot asset would otherwise fail with "command not found"
# on exactly the runners this fallback exists to serve (Codex review).
_PY_BIN="$(command -v python3 || command -v python || true)"

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
    existing_url=$(printf '%s' "$assets_json" | "$_PY_BIN" -c '
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
' "$asset_name")
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
      "$_PY_BIN" -c '
import sys
from pathlib import Path
from abicheck.package import TarExtractor

TarExtractor._safe_extract_zst_tar(Path(sys.argv[1]), Path(sys.argv[2]))
' "$archive_path" "$extracted_dir" \
        || { _baseline_unavailable "failed to extract baseline-set archive '$asset_name' (.tar.zst) -- it is truncated or corrupted, or this runner has neither a 'zstd' command-line tool nor the Python 'zstandard' package available."; return 1; }
      ;;
    *.tar.gz | *.tgz | *.tar)
      "$_PY_BIN" -c '
import sys
from pathlib import Path
from abicheck.package import TarExtractor

TarExtractor._safe_extract(Path(sys.argv[1]), Path(sys.argv[2]))
' "$archive_path" "$extracted_dir" \
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
  resolve_output=$("$_PY_BIN" -c '
import sys
from abicheck.buildsource.baseline_set import resolve_target

result = resolve_target(sys.argv[1], target=sys.argv[2], profile=sys.argv[3], required=True)
snapshot_path = result.snapshot_path
if not snapshot_path:
    snapshot_path = ""
print("outcome=" + result.outcome)
print("message=" + result.message)
print("snapshot_path=" + snapshot_path)
' "$manifest_root" "$baseline_target" "$INPUT_BASELINE_PROFILE")
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
    # compare consumes the baseline as old-library; scan consumes it as --against.
    if [[ "$MODE" == "scan" ]]; then
      INPUT_AGAINST="$BASELINE_FILE"
    else
      INPUT_OLD_LIBRARY="$BASELINE_FILE"
    fi
  elif [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
    # The fetch was tolerated above (dry-run never hard-fails on an
    # unresolved baseline), but if no other old-library/against was
    # independently given there is nothing left to preview -- report and
    # stop here rather than falling through to `${INPUT_OLD_LIBRARY:?...}`
    # below, whose bash parameter-expansion abort would turn this specific,
    # deliberately-tolerated case into a hard failure after all.
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
  add_flag "--public-header-dir" "${INPUT_PUBLIC_HEADER_DIR:-}"
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
    add_single_flag "--compression" "${INPUT_SNAPSHOT_COMPRESSION:-}"
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

  # The L2 compile-context flags (--ast-frontend/--gcc-*/--sysroot/
  # --nostdinc) are rejected outright by the CLI (a UsageError, exit 64)
  # for directory/package operands — the per-library release fan-out
  # doesn't thread a CompileContext to each pair's header dump. Gate them
  # to the single-pair path, same as the release-only flags below are
  # gated the other way. Fail loud (::error:: + exit 1) rather than warn
  # and continue, matching the evidence-flags guard just below (Codex
  # review): a warning alone lets the comparison run to a green verdict
  # with headers parsed under the wrong macros/sysroot/frontend, which is
  # exactly the silent-wrong-result failure mode the evidence-flags guard
  # was already fixed to avoid for the analogous --depth build/source
  # case — an explicitly-configured compile-context input deserves the
  # same treatment as an explicitly-configured evidence input.
  if _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
     || _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
    # "auto" is the documented no-op spelling of ast-frontend (resolves to
    # the same default castxml selection as leaving the input unset, per
    # its description above) -- a workflow that spells it out explicitly
    # requests nothing the release fan-out could actually drop, so it must
    # not trip this guard (Codex review, second round).
    if [[ (-n "${INPUT_AST_FRONTEND:-}" && "${INPUT_AST_FRONTEND:-}" != "auto") \
          || -n "${INPUT_GCC_PATH:-}" || -n "${INPUT_GCC_PREFIX:-}" \
          || -n "${INPUT_GCC_OPTIONS:-}" || -n "${INPUT_SYSROOT:-}" \
          || "${INPUT_NOSTDINC:-false}" == "true" ]]; then
      echo "::error::mode: compare with a directory/package operand (a release/bundle comparison) does not support ast-frontend/gcc-path/gcc-prefix/gcc-options/sysroot/nostdinc -- the per-library fan-out never threads the L2 compile context to each pair's header dump, so the requested context would silently never be applied and headers could be parsed under the wrong macros/sysroot/frontend. Compare the libraries individually (mode: compare with single-file operands) to use them."
      exit 1
    fi
  else
    add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"
    add_single_flag "--gcc-path" "${INPUT_GCC_PATH:-}"
    add_single_flag "--gcc-prefix" "${INPUT_GCC_PREFIX:-}"
    add_single_flag "--gcc-options" "${INPUT_GCC_OPTIONS:-}"
    add_single_flag "--sysroot" "${INPUT_SYSROOT:-}"

    if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then
      CMD+=(--nostdinc)
    fi
  fi

  # Build/source evidence (--depth build/source) — new (candidate) side only.
  # The old side's evidence, if any, already lives in whatever
  # old-library/abi-baseline snapshot was resolved (e.g. a baseline archive
  # built with its own embedded build_source) — this Action has no live old-
  # side source tree to point --sources/--build-info at in compare mode, so
  # these inputs scope to `new=` unconditionally rather than exposing a
  # second old-sources/old-build-info input pair. Previously silently
  # dropped in compare mode (only dump/scan forwarded them) — a real gap
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
  # (Codex review, second round).
  add_single_flag "--config" "${INPUT_BUILD_CONFIG:-}"
  # bundle-system-providers reaches the cross-library bundle-analysis layer
  # (ADR-023) the same way --config does above: unconditional, since the CLI
  # itself already treats it as a no-op (silently ignored) for a single-pair
  # operand rather than rejecting it outright. Previously not wired to the
  # Action at all, even though compare's CLI has carried
  # --bundle-system-providers since ADR-023 (a pre-existing gap, not scoped
  # to ADR-056 — added here since this pass is wiring the input from scratch
  # anyway; see ADR-056/G34's Action-wiring correction).
  add_single_flag "--bundle-system-providers" "${INPUT_BUNDLE_SYSTEM_PROVIDERS:-}"
  if _is_release_style_operand "${INPUT_OLD_LIBRARY:-}" \
     || _is_release_style_operand "${INPUT_NEW_LIBRARY:-}"; then
    # A caller that explicitly asked for build/source-depth evidence (via
    # --depth build/source, or by supplying --sources/--build-info/
    # --compile-db directly) against a directory/package operand would
    # otherwise have that request silently dropped: the flags above are
    # skipped rather than forwarded, so the comparison would quietly run
    # without the requested evidence and could miss a source-only break
    # while still reporting a clean/normal result -- fail loud instead
    # (Codex review; --depth binary/headers is fine to drop silently, since
    # nothing was actually requested that this shape can't provide).
    if [[ "${INPUT_DEPTH:-}" == "build" || "${INPUT_DEPTH:-}" == "source" \
       || -n "${INPUT_SOURCES:-}" || -n "${INPUT_BUILD_INFO:-}" || -n "${INPUT_COMPILE_DB:-}" ]]; then
      echo "::error::mode: compare with a directory/package operand (a release/bundle comparison) does not support --depth build/source or inline --sources/--build-info/--compile-db evidence -- the CLI's per-library release fan-out never collects it, so the requested evidence would silently never be gathered and a source-only break could be missed. Compare the libraries individually (mode: compare with single-file operands) to use build/source-depth evidence."
      exit 1
    fi
  else
    add_sided_flag "--sources" "new" "${INPUT_SOURCES:-}"
    add_sided_flag "--build-info" "new" "${INPUT_BUILD_INFO:-${INPUT_COMPILE_DB:-}}"
    add_single_flag "--depth" "${INPUT_DEPTH:-}"
  fi

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
  SCAN_ARTIFACT_SET="${INPUT_NEW_LIBRARY_SET:-}"
  if [[ -n "$SCAN_ARTIFACT_SET" ]]; then
    # ADR-056: audit a *set* of libraries with no old side, as one
    # operation. Mutually exclusive with new-library and against/
    # abi-baseline — normally caught early by action/validate-inputs.sh,
    # before any dependency install; re-checked here for anyone invoking
    # run.sh directly (e.g. tests) without that step.
    if [[ -n "${INPUT_NEW_LIBRARY:-}" ]]; then
      echo "::error::mode: scan cannot take both new-library and new-library-set — new-library-set audits a *set* of libraries with no old side (ADR-056), new-library scans exactly one artifact. Set only one."
      exit 1
    fi
    if [[ -n "${INPUT_AGAINST:-}" || -n "${INPUT_ABI_BASELINE:-}" ]]; then
      # Without this, the later "omit --against for new-library-set" logic
      # (below) would silently downgrade an explicitly-requested baseline
      # compare into an audit-only run instead of rejecting it -- a direct
      # run.sh caller (bypassing validate-inputs.sh's own copy of this
      # check) would get a successful result for a different operation
      # than requested (Codex review).
      echo "::error::mode: scan with new-library-set does not support against/abi-baseline — new-library-set is audit-only (no old side to compare a set against, ADR-056). Remove against/abi-baseline, or use new-library (a single artifact) for a baseline comparison instead."
      exit 1
    fi
    CMD+=(--artifact-set "$SCAN_ARTIFACT_SET")
    add_single_flag "--bundle-system-providers" "${INPUT_BUNDLE_SYSTEM_PROVIDERS:-}"
  else
    SCAN_ARTIFACT="${INPUT_NEW_LIBRARY:?new-library (the scanned binary or .abi.json) is required for scan mode, unless new-library-set is given}"
    # scan has no per-library fan-out (unlike compare) — a directory/package
    # is normally caught early by action/validate-inputs.sh, before any
    # dependency install; re-checked here for anyone invoking run.sh directly
    # (e.g. tests) without that step.
    if _is_release_style_operand "$SCAN_ARTIFACT"; then
      echo "::error::mode: scan does not accept a directory or package for new-library ('$SCAN_ARTIFACT') — scan analyses exactly one artifact. Use new-library-set to audit a set with no old side, or mode: compare against a directory/package for a multi-library binary comparison instead."
      exit 1
    fi
    CMD+=("$SCAN_ARTIFACT")
  fi

  if [[ -n "$SCAN_ARTIFACT_SET" ]]; then
    # --artifact-set has no old side, so cli_scan._run_artifact_set rejects
    # old=/new= scoping outright -- old-header/old-include are meaningless
    # here (reject loudly rather than let the CLI's own UsageError surface
    # only after toolchain install) and new-header/new-include map to the
    # bare flags, not "-H new=..."/"-I new=..." (Codex review).
    if [[ -n "${INPUT_OLD_HEADER:-}" || -n "${INPUT_OLD_INCLUDE:-}" ]]; then
      echo "::error::mode: scan with new-library-set does not support old-header/old-include -- new-library-set is audit-only (no old side, ADR-056)."
      exit 1
    fi
    add_flag "-H" "${INPUT_HEADER:-}"
    add_flag "-H" "${INPUT_NEW_HEADER:-}"
    add_flag "-I" "${INPUT_INCLUDE:-}"
    add_flag "-I" "${INPUT_NEW_INCLUDE:-}"
  else
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
  fi

  # --public-header-dir is not side-aware on the CLI (unlike -H/-I above),
  # so it's forwarded once regardless of which branch above ran.
  add_flag "--public-header-dir" "${INPUT_PUBLIC_HEADER_DIR:-}"

  # Cross-compiler flags -- documented root-Action inputs, but previously
  # only wired to dump mode's branch (Codex review, same gap as compare
  # mode above).
  add_single_flag "--gcc-path" "${INPUT_GCC_PATH:-}"
  add_single_flag "--gcc-prefix" "${INPUT_GCC_PREFIX:-}"
  add_single_flag "--gcc-options" "${INPUT_GCC_OPTIONS:-}"
  add_single_flag "--sysroot" "${INPUT_SYSROOT:-}"

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
  # --artifact-set is audit-only at the CLI level too (ADR-056) — normally
  # caught early by validate-inputs.sh; skip forwarding here as well for
  # anyone invoking run.sh directly.
  if [[ "$FORCE_AUDIT_ONLY" != "true" && -z "$SCAN_ARTIFACT_SET" ]]; then
    add_single_flag "--against" "${INPUT_AGAINST:-}"
  fi
  add_single_flag "--lang" "${INPUT_LANG:-}"
  add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"
  add_single_flag "--gcc-path" "${INPUT_GCC_PATH:-}"
  add_single_flag "--gcc-prefix" "${INPUT_GCC_PREFIX:-}"
  add_single_flag "--gcc-options" "${INPUT_GCC_OPTIONS:-}"
  add_single_flag "--sysroot" "${INPUT_SYSROOT:-}"

  if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then
    CMD+=(--nostdinc)
  fi

  # Level selection — the modern --depth dial (omit for 'auto'). The deprecated
  # --mode/--source-method passthrough was removed; use depth.
  add_single_flag "--depth" "${INPUT_DEPTH:-}"

  # Focusing + guards + policy
  add_single_flag "--since" "${INPUT_SINCE:-}"
  add_flag "--changed-path" "${INPUT_CHANGED_PATH:-}"
  add_single_flag "--budget" "${INPUT_BUDGET:-}"
  add_single_flag "--risk-rules" "${INPUT_RISK_RULES:-}"
  add_flag "--crosscheck" "${INPUT_CROSSCHECK:-}"
  # `scan --against` takes @policy_options (--policy/--policy-file/
  # --suppress) the same as compare, but this branch never forwarded them —
  # the only way to apply a policy or suppression file to a scan step was
  # the generic extra-args passthrough (Codex review / reported gap).
  #
  # Only forwarded when a baseline is actually present (same condition as
  # the --against forwarding above): cli_scan.py's
  # _reject_comparison_only_flags() hard-rejects any of these three when
  # --against was not given, and `policy` has a non-empty action.yml
  # default (`strict_abi`) -- forwarding it unconditionally would turn
  # --policy strict_abi into an always-present explicit flag and break
  # every existing audit-only (no baseline) scan step with a usage error
  # (Codex review, P1).
  if [[ "$FORCE_AUDIT_ONLY" != "true" && -z "$SCAN_ARTIFACT_SET" && -n "${INPUT_AGAINST:-}" ]]; then
    add_single_flag "--policy" "${INPUT_POLICY:-}"
    add_single_flag "--policy-file" "${INPUT_POLICY_FILE:-}"
    add_single_flag "--suppress" "${INPUT_SUPPRESS:-}"
  fi

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
trap 'rm -f "$STDERR_FILE" "${_STDOUT_JSON_FILE:-}"; rm -rf "${_BASELINE_CLEANUP:-}"' EXIT

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
# In the *parent* shell, deliberately. Every caller reads the path through
# `_src=$(_json_report_src)`, and a command substitution runs in a subshell —
# so creating the file lazily inside that function wrote the memo to a shell
# that then exited, making each of the three callers mint its own copy and
# leaving the EXIT trap with an empty path to clean up. On a persistent
# self-hosted runner that leaks a full report copy per lookup (Codex review).
_STDOUT_JSON_FILE=""
if [[ "${FORMAT:-}" == "json" && "${ABICHECK_OUTPUT:-}" == "{"* ]]; then
  _STDOUT_JSON_FILE=$(mktemp "${RUNNER_TEMP:-/tmp}/abicheck-stdout-json.XXXXXX")
  printf '%s' "$ABICHECK_OUTPUT" > "$_STDOUT_JSON_FILE"
fi

_is_cli_error() {
  echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try |Traceback|click\.)'
}

# The JSON report this run produced, if any — the primary output when
# format=json, or (the common case: default format=markdown) the
# always-unfiltered secondary JSON the compare-mode command setup above
# already asks the same invocation to write via --secondary-format. Empty
# when neither exists. One function because three separate decisions below
# read the same report and must not disagree about which one it is.
#
# The stdout-mode file above is the third source, already materialised — the
# function only ever *reads* a path, so it stays safe to call from a command
# substitution. Without that file, every decision below took its "no report"
# fallback for the one configuration that keeps the report on stdout.
_json_report_src() {
  if [[ "${FORMAT:-}" == "json" && -n "${OUTPUT_FILE:-}" && -s "${OUTPUT_FILE:-}" ]]; then
    echo "${OUTPUT_FILE}"
  elif [[ -n "${PR_JSON:-}" && -s "${PR_JSON:-}" ]]; then
    echo "${PR_JSON}"
  elif [[ -n "${_STDOUT_JSON_FILE:-}" ]]; then
    echo "${_STDOUT_JSON_FILE}"
  fi
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
# The two modes nest the ledger differently and BOTH reach here: `compare`
# writes it at the top level, while `ScanOutcome.to_dict()` puts the
# comparison summary under `diff`. Each query below looks in both.
#
# _PY_BIN itself is resolved once, near the top of this script (before
# MODE's dry-run/back-compat block) -- not here -- so the baseline-set
# fallback (which runs long before this function is ever reached) can use
# the same resolved interpreter too.
_report_query() {
  # $1 = report path, $2 = query name. Prints nothing when the report cannot
  # be read or parsed, which every caller treats as "cannot tell" rather than
  # as an answer.
  [[ -n "$_PY_BIN" && -n "${1:-}" ]] || return 1
  "$_PY_BIN" - "$1" "$2" <<'PYQUERY' 2>/dev/null
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        report = json.load(fh)
except Exception:
    raise SystemExit(1)
if not isinstance(report, dict):
    raise SystemExit(1)
nested = report.get("diff")
nested = nested if isinstance(nested, dict) else {}


def _either(key, default):
    """The value from the compare shape, else the scan shape, else *default*."""
    value = report.get(key)
    if value is None:
        value = nested.get(key, default)
    return default if value is None else value


def _severity():
    # Compare keeps its gate at the document root; a severity-scheme
    # `scan --against` nests it under `diff` (scan schema 1.9+), exactly
    # as it nests the coverage ledger `_either` already reaches for. Root
    # first so a compare report is unaffected.
    block = report.get("severity")
    if not isinstance(block, dict):
        block = nested.get("severity")
    return block if isinstance(block, dict) else {}


query = sys.argv[2]
if query == "coverage_contribution":
    print(_either("contract_coverage_exit_contribution", 0))
elif query == "severity_exit":
    # An absent `severity` block is the legacy scheme, whose exit codes are
    # 0/2/4 for compare and 0/2/4/5/6 for scan -- never 1 either way -- so
    # the compatibility axis contributed 0 by construction. Only an
    # unreadable report is "cannot tell", and that exits above without
    # printing.
    print(_severity().get("exit_code", 0))
elif query == "compat_verdict":
    # The *compatibility* axis's own verdict, which a severity scheme never
    # rewrites -- `scan_engine` explicitly leaves a BREAKING/API_BREAK label
    # alone when the gate demotes the exit code, and `compare` reports
    # `result.verdict` unconditionally. It is therefore the only signal that
    # tells a genuinely clean run from a break the user chose not to gate on.
    print(_either("verdict", "") or "")
elif query == "blocking_categories":
    print(", ".join(str(c) for c in (_severity().get("blocking_categories") or [])))
elif query == "coverage_where":
    print(
        ", ".join(
            sorted(
                {
                    "{}/{}".format(f.get("side"), f.get("provider"))
                    for f in (_either("contract_coverage_failures", []) or [])
                    if isinstance(f, dict)
                }
            )
        )
    )
else:
    raise SystemExit(2)
PYQUERY
}

# Did ADR-049's orthogonal contract-coverage axis contribute to this exit?
#
# Since ADR-049 Phase 7, a run passing --contract-evaluation (via extra-args)
# whose selected contract domain cannot be closed on the available evidence
# contributes exit 1 — independently of the compatibility verdict, which the
# axis deliberately never rewrites. Without asking, `scan` published
# verdict=ERROR (an operational failure) and `compare` SEVERITY_ERROR (a
# severity-policy failure) for what is neither.
#
# Two signals, mirroring where abicheck itself puts the answer: the JSON
# report carries `contract_coverage_exit_contribution`, and for every other
# renderer — which omits the ledger — the same fact is announced on stderr.
# Absent both (no --contract-evaluation), the field reads 0 and the mapping
# below is exactly what it was.
#
# The JSON answer is AUTHORITATIVE when readable, and the stderr grep is
# reached only when it is not (Codex review, P1): an earlier revision fell
# through to the stderr grep unconditionally whenever the JSON read "0", not
# just when the JSON was unreadable. `contract.unresolved: warn` deliberately
# zeroes the exit contribution while still wording the stderr notice as
# "Contract coverage incomplete..." (just with an "Accepted by
# contract.unresolved: warn" effect clause, per `_coverage_message`) — so
# whenever both a readable JSON *and* that stderr notice existed together
# (true for every markdown-format single-pair `compare`, and, since this
# same commit's own P1 fix, every non-JSON release `compare` too), the grep
# still matched and defeated the acceptance mechanism entirely. `_report_
# query` prints nothing (empty string) only when the report is genuinely
# unreadable/absent — that emptiness, not the printed value, is what decides
# whether the stderr fallback is even consulted.
_coverage_gated() {
  local _src _contribution
  _src=$(_json_report_src)
  _contribution=$(_report_query "$_src" coverage_contribution)
  if [[ -n "$_contribution" ]]; then
    [[ "$_contribution" == "1" ]]
    return
  fi
  echo "$STDERR_CONTENT" | grep -q 'Contract coverage incomplete'
}

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
# Falls back to the **text** report when there is no JSON to read. That is not
# an edge case for `scan`: `format: text` is the Action's documented default
# and scan writes no JSON sidecar, so `_json_report_src` is empty and the
# query answered nothing -- publishing ERROR (an operational failure) for a
# severity-policy result on the most common invocation there is (Codex
# review). The CLI prints its own gate on that path (`cli_scan_helpers.
# _severity_gate_lines`), so the fact is present; it just is not JSON.
#
# Only a *blocking* gate line is matched, and it is mapped to the same
# non-zero the JSON branch would yield. A passing gate prints "pass" and is
# left to answer 0 through the absent-block rule below, exactly as a
# legacy-scheme run does.
_severity_gate_exit() {
  local _src _answer
  _src=$(_json_report_src)
  _answer=$(_report_query "$_src" severity_exit)
  if [[ -n "$_answer" ]]; then
    echo "$_answer"
    return
  fi
  # `severity gate: exit N — blocking: <categories>`
  sed -n 's/.*severity gate: exit \([0-9][0-9]*\).*blocking:.*/\1/p' \
    <<<"$(_text_report_content)" | head -1
}

# The text report, wherever this invocation put it. `format: text` with an
# `output-file` writes the report to that file and leaves stdout empty, so a
# stdout-only search still published ERROR for a severity-policy result
# (Codex review) -- the same defect as the JSON-only search before it, one
# level down.
_text_report_content() {
  if [[ "${FORMAT:-}" != "json" && -n "${OUTPUT_FILE:-}" && -s "${OUTPUT_FILE:-}" ]]; then
    cat "${OUTPUT_FILE}"
  else
    printf '%s' "${ABICHECK_OUTPUT:-}"
  fi
}

# The categories the published gate blames, from JSON when there is one and
# otherwise from the text gate line. Used by the scan final gate to tell a
# severity-configured block (which the user asked to be an error) from a
# promoted cross-check (which keeps following fail-on-api-break).
_severity_gate_categories() {
  local _src _answer
  _src=$(_json_report_src)
  _answer=$(_report_query "$_src" blocking_categories)
  if [[ -n "$_answer" ]]; then
    echo "$_answer"
    return
  fi
  # Not anchored on the em-dash the renderer happens to use: the exit-code
  # reader above does not require it either, and a separator that only one of
  # the two greps depends on is a difference waiting to bite under a
  # different locale or renderer tweak.
  sed -n 's/.*severity gate: exit [0-9][0-9]*[^:]*blocking: *\(.*\)$/\1/p' \
    <<<"$(_text_report_content)" | head -1
}



# The compatibility verdict the report itself published, JSON first and
# otherwise the rendered report -- the same two-source rule as the severity
# gate readers above, and for the same reason: `format: text` is the Action's
# documented default for scan and writes no JSON sidecar.
#
# The label spelling is shared by both renderers (`Verdict: BREAKING` in the
# scan text footer, ``**Verdict:** 💥 `BREAKING`  — …`` in the compare
# markdown header), so one pattern reads both. Only uppercase-free filler is
# allowed between the two halves, which is what stops a `COMPATIBLE` verdict
# line from matching on a later "breaking" word in its own explanatory tail.
_report_compat_verdict() {
  local _src _answer
  _src=$(_json_report_src)
  _answer=$(_report_query "$_src" compat_verdict)
  if [[ -n "$_answer" ]]; then
    echo "$_answer"
    return
  fi
  # `sed -E`, not the basic-regex `\(a\|b\)` the other readers here get away
  # with not needing: BSD sed (macOS runners, which this Action supports and
  # CI covers) has no alternation in BRE at all, so the pattern silently
  # matched nothing there and every demoted break read as COMPATIBLE on
  # macOS while passing on Linux. `-E` is accepted by both.
  #
  # `Verdict(:|**)` covers both spellings, because the per-library release
  # fan-out has no third option: `--secondary-format` is rejected for a
  # directory/package operand, so a markdown release compare reaches this
  # fallback with no JSON at all -- and its renderer writes the verdict as a
  # table row, `| **Verdict** | 💥 \`BREAKING\` |`, with no colon. Matching
  # only the colon form left every release compare unescalated: an exit-2
  # release whose own report said BREAKING still published API_BREAK, which is
  # the whole thing this reconciliation exists to prevent (Codex review). The
  # delimiter is still required rather than dropped -- a bare `Verdict`
  # followed by uppercase-free filler would match prose.
  sed -nE 's/.*Verdict(:|\*\*)[^A-Z]*(API_BREAK|BREAKING).*/\2/p' \
    <<<"$(_text_report_content)" | head -1
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
  local _v
  VERDICT="COMPATIBLE"
  _v=$(_report_compat_verdict)
  if [[ "$_v" == "BREAKING" || "$_v" == "API_BREAK" ]]; then
    VERDICT="$_v"
    ADVISORY_BREAK=true
    echo "::notice::abicheck reports $_v, but the configured severity policy resolved this run to exit 0 — the step is not failed. Raise the category to \`error\` to gate on it."
  fi
}

# Compatibility tiers, most severe last. Only these three are ranked: every
# other verdict (ERROR, BUDGET_OVERFLOW, SEVERITY_ERROR, ...) is a different
# axis and must never be escalated away by this comparison.
_verdict_rank() {
  case "$1" in
    BREAKING) echo 3 ;;
    API_BREAK) echo 2 ;;
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
_blocking_gate_note() {
  local _cats
  _cats=$(_severity_gate_categories | tr ',' '\n' \
    | sed 's/^ *//;s/ *$//' | grep -v '^promoted_crosscheck$' | grep -v '^$' | paste -sd, -)
  if [[ -n "$_cats" ]]; then
    echo ">"
    if [[ "${GATE_TIER:-$VERDICT}" == "SEVERITY_ERROR" || "$MODE" == "scan" ]]; then
      # `scan` is the second case that bypasses the flags at *every* tier: its
      # final branch detects the real severity category and sets FINAL_EXIT=1
      # unconditionally, so claiming the flags still decide would be the exact
      # opposite of what happened (Codex review).
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

elif [[ "$MODE" == "scan" ]]; then
  # scan exit codes: 0=compatible/advisory, 1=severity error or incomplete
  # contract coverage (see below), 2=API break, 4=ABI break, 5=budget
  # overflow, 6=not_comparable. Click usage errors also use exit 2 —
  # distinguish via stderr.
  if [[ $ABICHECK_EXIT -eq 2 ]] && echo "$STDERR_CONTENT" | grep -qE '(^Usage:|^Error:|^Try )'; then
    VERDICT="ERROR"
    echo "::error::abicheck scan failed due to a CLI argument or configuration error (exit code 2)."
    echo "::error::Check the command and inputs above. This is NOT an API break — the scan did not run."
  else
    case $ABICHECK_EXIT in
      0) _resolve_clean_exit_verdict ;;
      1)
        # `scan` exit 1 now has three possible sources, not one. It used to
        # be coverage-only ("scan's own verdict codes are 0/2/4/5, so 1 can
        # only come from the orthogonal contract-coverage axis"), but a
        # severity-scheme `scan --against` gates natively at 1 on an
        # error-level addition/quality finding — so that reasoning would
        # publish ERROR for a severity-policy result, or drop the severity
        # gate when coverage happened to contribute too (Codex review).
        # A crash also exits 1 and must still stay ERROR.
        #
        # Resolved the same way, and in the same order, as the compare branch
        # below: the report's pre-fold `severity.exit_code` tells the axes
        # apart rather than a guess.
        _sev_exit=$(_severity_gate_exit)
        if _is_cli_error; then
          VERDICT="ERROR"
          echo "::error::abicheck scan failed due to a CLI error (exit code 1)."
        elif [[ "$_sev_exit" != "0" && -n "$_sev_exit" ]]; then
          VERDICT="SEVERITY_ERROR"
          if _coverage_gated; then
            echo "::warning::abicheck scan also reports incomplete contract coverage for the selected --contract domain; see contract_coverage_failures in the JSON report."
          fi
        elif _coverage_gated; then
          VERDICT="COVERAGE_INCOMPLETE"
          echo "::warning::abicheck scan could not close the selected contract domain on the available evidence (exit code 1). This is NOT an ABI or API break — the compatibility verdict is unchanged; the contract-coverage axis is reporting that part of the surface could not be checked."
        else
          VERDICT="ERROR"
        fi
        # Exit 1 carries a demoted break just as exit 2 does: a policy that
        # puts `abi_breaking` below `error` while an addition/quality finding
        # stays at `error` gates at 1 with a report that still says BREAKING
        # (Codex review). ERROR is left alone -- that is an operational
        # failure, not a gated compatibility result, and `_verdict_rank`
        # ranks it 0 only because it must never be escalated *from* here.
        if [[ "$VERDICT" != "ERROR" ]]; then
          _escalate_verdict_to_report
        fi
        ;;
      2) VERDICT="API_BREAK"; _escalate_verdict_to_report ;;
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
      0) _resolve_clean_exit_verdict ;;
      1)
        if _is_cli_error; then
          VERDICT="ERROR"
          echo "::error::abicheck failed due to a CLI argument or configuration error (exit code 1)."
          echo "::error::Check the command and inputs above."
        elif _coverage_gated; then
          # `compare` shares exit 1 between two independent axes, so the
          # report's pre-fold `severity.exit_code` is what tells them apart
          # rather than a guess. Only when the severity gate itself did not
          # produce 1 is this run gated by coverage *alone*.
          _sev_exit=$(_severity_gate_exit)
          if [[ "$_sev_exit" == "0" ]]; then
            VERDICT="COVERAGE_INCOMPLETE"
            echo "::warning::abicheck could not close the selected contract domain on the available evidence (exit code 1). This is NOT an ABI/API break and NOT a severity-policy failure — the compatibility verdict is unchanged."
          else
            # Either severity gated too, or there is no readable JSON report
            # to tell. Keep the established verdict rather than overwrite it
            # on a guess, and say that coverage also contributed.
            VERDICT="SEVERITY_ERROR"
            echo "::warning::abicheck also reports incomplete contract coverage for the selected --contract domain; see contract_coverage_failures in the JSON report."
          fi
        else
          VERDICT="SEVERITY_ERROR"
        fi
        if [[ "$VERDICT" != "ERROR" ]]; then
          _escalate_verdict_to_report
        fi
        ;;
      2) VERDICT="API_BREAK"; _escalate_verdict_to_report ;;
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
        # the generic message when no report is readable.
        # Through `_severity_gate_categories`, which falls back to the text
        # report's own gate line. A scan on the default `format: text` has no
        # JSON at all, so a JSON-only lookup printed the bare "severity-level
        # issue" message the comment above says this exists to avoid -- the
        # same JSON-only assumption that had to be fixed in the verdict
        # mapping and then again in its text fallback.
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
      # new-library-set (ADR-056, --artifact-set) has no INPUT_NEW_LIBRARY
      # value at all -- render the set operand instead so the summary
      # doesn't show an empty Binary row for every artifact-set run
      # (Codex review).
      if [[ -n "${INPUT_NEW_LIBRARY_SET:-}" ]]; then
        echo "| Artifact set | \`${INPUT_NEW_LIBRARY_SET}\` |"
      else
        echo "| Binary | \`${INPUT_NEW_LIBRARY:-}\` |"
      fi
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
  if [[ "${GATE_TIER:-$VERDICT}" == "BREAKING" && "${INPUT_FAIL_ON_BREAKING:-true}" == "true" \
        && "$ADVISORY_BREAK" != "true" ]]; then
    echo "::error::ABI break detected by scan. Set fail-on-breaking: false to continue despite breaks."
    FINAL_EXIT=1
  fi

  # API_BREAK (scan exit 2) covers baseline/source API breaks AND a cross-check
  # the user promoted with --crosscheck KEY=error (the scan CLI maps both to
  # exit 2). They share one tier, so fail-on-api-break gates them uniformly — we
  # cannot tell from the exit code alone whether a promoted check fired, so
  # keying off the crosscheck flag would wrongly fail an unrelated API break
  # when fail-on-api-break is false (Codex review).
  if [[ "${GATE_TIER:-$VERDICT}" == "API_BREAK" && "${INPUT_FAIL_ON_API_BREAK:-false}" == "true" \
        && "$ADVISORY_BREAK" != "true" ]]; then
    echo "::error::API/source break detected by scan (includes promoted --crosscheck=error gates). Set fail-on-api-break: false to ignore."
    FINAL_EXIT=1
  fi

  if [[ "$VERDICT" == "BUDGET_OVERFLOW" ]]; then
    echo "::error::Scan exceeded its budget. Pin a shallower level or raise the budget."
    FINAL_EXIT=1
  fi

  # A severity-scheme `scan --against` gates natively at exit 1 on an
  # error-level addition/quality finding, so scan can now publish
  # SEVERITY_ERROR. Without this branch the mapping above named the verdict
  # and the step still succeeded -- the explicitly configured policy gate
  # silently did nothing (Codex review).
  #
  # Unconditional, exactly like the compare branch below: fail-on-breaking /
  # fail-on-api-break select which *compatibility* tiers gate, and a severity
  # policy is not one of those tiers -- it is the user having already said
  # "this category is an error". Routing it through a compatibility flag
  # would let fail-on-api-break: false switch off an addition gate that has
  # nothing to do with API breaks.
  if [[ "${GATE_TIER:-$VERDICT}" == "SEVERITY_ERROR" ]]; then
    echo "::error::Severity-level error detected by abicheck scan."
    FINAL_EXIT=1
  fi

  # A severity gate does not only produce exit 1. `potential_breaking=error`
  # gates at 2 and `abi_breaking=error` at 4, and those map to the API_BREAK /
  # BREAKING labels above -- where `fail-on-api-break` defaults to *false*, so
  # a category the user explicitly configured as an error let the step succeed
  # (Codex review). The verdict label is left alone (it is the right one for
  # the compatibility axis); what changes is that an explicitly-configured
  # severity block is not subject to a compatibility flag.
  #
  # A promoted cross-check is deliberately excluded: it also raises the
  # published gate (`_promote_published_gate`), but it is not a severity
  # category and keeps following fail-on-api-break, as it did before.
  _sev_cats=$(_severity_gate_categories)
  _sev_cats_real=$(tr ',' '\n' <<<"$_sev_cats" | sed 's/^ *//;s/ *$//' \
    | grep -v '^promoted_crosscheck$' | grep -v '^$' | head -1)
  if [[ -n "$_sev_cats_real" && "$VERDICT" != "SEVERITY_ERROR" ]]; then
    echo "::error::abicheck scan is blocked by severity policy (${_sev_cats_real} configured as error); see the severity gate in the report."
    FINAL_EXIT=1
  fi

  # ADR-049 Phase 7's contract-coverage axis is orthogonal and unconditional
  # -- no fail-on-* flag disables it, matching the SEVERITY_ERROR tier above
  # (AGENTS.md: "no fail-on-* condition at all... a coverage failure raises
  # a clean 0 to 1 and can never lower a gate's 2/4"). Reads `_coverage_
  # gated()` DIRECTLY rather than keying off VERDICT/GATE_TIER=="COVERAGE_
  # INCOMPLETE" (an earlier revision did the latter, Codex review): the
  # max-fold means a higher-priority axis (a real BREAKING/API_BREAK) wins
  # the VERDICT/GATE_TIER label and the CLI's own exit code, so that label
  # never reaches "COVERAGE_INCOMPLETE" even though contract_coverage_
  # exit_contribution genuinely is 1 -- with fail-on-breaking: false, that
  # left the coverage floor completely unenforced. `_coverage_gated()`
  # already reads the report's own zeroed contribution under `contract.
  # unresolved: warn`, so this stays inert in that case regardless.
  if _coverage_gated; then
    echo "::error::abicheck scan could not close the selected --contract domain on the available evidence; see contract_coverage_failures in the JSON report. Accept incomplete assurance with contract.unresolved: warn to allow."
    FINAL_EXIT=1
  fi

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

  # Severity-driven exit code 1 (from --severity-* flags)
  if [[ "${GATE_TIER:-$VERDICT}" == "SEVERITY_ERROR" ]]; then
    echo "::error::Severity-level error detected by abicheck."
    FINAL_EXIT=1
  fi

  # ADR-049 Phase 7's contract-coverage axis, unconditional exactly like the
  # scan-mode check above and for the same reason -- see that comment. Reads
  # `_coverage_gated()` directly rather than the VERDICT/GATE_TIER label, so
  # a BREAKING/API_BREAK exit that outranks the coverage axis in the max-fold
  # (and, with the matching fail-on-* flag false, would otherwise leave the
  # step green) cannot silently swallow it.
  if _coverage_gated; then
    echo "::error::abicheck could not close the selected --contract domain on the available evidence; see contract_coverage_failures in the JSON report. Accept incomplete assurance with contract.unresolved: warn to allow."
    FINAL_EXIT=1
  fi
fi

exit $FINAL_EXIT
