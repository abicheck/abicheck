#!/usr/bin/env bash
# Dumps a set of libraries into a baseline-set: one .abicheck.json per
# library plus a manifest.json (actions/baseline/build_manifest.py) --
# see actions/baseline/action.yml for the rationale. Read-only: never
# commits or pushes; publishing the result is the calling workflow's job.
set -uo pipefail

# NOTE: the ${VAR:?message} message must not contain a literal '{' or '}' --
# bash's ${...} parser is not brace-depth-aware for the message text, so it
# closes the expansion at the FIRST literal '}' it sees and treats anything
# after that as trailing text appended to the assignment (a real bug this
# script hit during testing: the JSON example below silently corrupted
# LIBRARIES_JSON). Keep this message brace-free; the JSON shape is
# documented in action.yml instead.
LIBRARIES_JSON="${INPUT_LIBRARIES:?libraries input is required -- a JSON array of library entries, see action.yml}"
OUTPUT_DIR="${INPUT_OUTPUT_DIR:-.abicheck-baseline}"
PROJECT_REF="${INPUT_PROJECT_REF:-}"
PROFILE="${INPUT_PROFILE:-}"
BUILD_INFO="${INPUT_BUILD_INFO:-}"
DEPTH="${INPUT_DEPTH:-}"
BASELINE_GENERATION="${INPUT_BASELINE_GENERATION:-}"
PREVIOUS_MANIFEST="${INPUT_PREVIOUS_MANIFEST:-}"
VALIDATION="${INPUT_VALIDATION:-strict}"
SNAPSHOT_COMPRESSION="${INPUT_SNAPSHOT_COMPRESSION:-none}"
ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

_fail() {
  echo "::error::$1"
  exit 1
}

case "$VALIDATION" in
  strict | none) ;;
  *) _fail "validation '$VALIDATION' is not recognized. Use 'strict' or 'none'." ;;
esac

case "$BASELINE_GENERATION" in
  '' | [0-9]*) ;;
  *) _fail "baseline-generation '$BASELINE_GENERATION' is not a non-negative integer." ;;
esac

# ADR-059: the canonical storage suffix implied by snapshot-compression --
# every library in one run uses the same encoding (action.yml's own
# contract), so this is computed once. `dump`'s own --compression flag
# would also accept a mismatched explicit value against a *different*
# canonical suffix as a hard error (resolve_write_compression) -- keeping
# the -o path's suffix and --compression in agreement here is what avoids
# ever hitting that path.
case "$SNAPSHOT_COMPRESSION" in
  none) SNAPSHOT_SUFFIX=".abicheck.json" ;;
  gzip) SNAPSHOT_SUFFIX=".abicheck.json.gz" ;;
  zstd) SNAPSHOT_SUFFIX=".abicheck.json.zst" ;;
  *) _fail "snapshot-compression '$SNAPSHOT_COMPRESSION' is not recognized. Use 'none', 'gzip', or 'zstd'." ;;
esac

# Validate the libraries JSON up front (name/artifact required per entry) --
# fail before any dump runs, not after the Nth one.
LIBRARIES_ERROR=$(python3 -c '
import json, sys
try:
    entries = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    sys.exit(f"not valid JSON: {exc}")
if not isinstance(entries, list) or not entries:
    sys.exit("must be a non-empty JSON array")
seen_names = set()
for i, e in enumerate(entries):
    if not isinstance(e, dict) or "name" not in e or "artifact" not in e:
        sys.exit(f"entry {i} must be an object with at least \"name\" and \"artifact\"")
    name = e["name"]
    if not isinstance(name, str) or not name:
        sys.exit(f"entry {i} has an invalid \"name\" {name!r} -- must be a non-empty string")
    # stage_binary (G30 P1.6, ADR-047 section 6/section 8 S14 correction):
    # optional, defaults to false when omitted -- a real boolean is required
    # so a truthy-but-wrong JSON value (e.g. the string "true") does not
    # silently skip binary staging for a bundle member.
    if "stage_binary" in e and not isinstance(e["stage_binary"], bool):
        bad_stage_binary = e["stage_binary"]
        sys.exit(f"entry {i} has an invalid \"stage_binary\" {bad_stage_binary!r} -- must be a boolean")
    # These strings are serialized below as newline-separated records whose
    # fields use ASCII Unit Separator. Reject either delimiter before
    # serialization so one field cannot inject a synthetic, unvalidated row.
    for field in ("artifact", "header", "include"):
        value = e.get(field, "")
        if not isinstance(value, str):
            sys.exit(f"entry {i} has an invalid \"{field}\" {value!r} -- must be a string")
        if "\n" in value or "\r" in value or "\x1f" in value:
            sys.exit(f"entry {i} has an invalid \"{field}\" {value!r} -- must not contain record delimiters")
    # Both run.sh ("$OUTPUT_DIR/$name.abicheck.json", bash string concat)
    # and build_manifest.py (output_dir / f"{name}.abicheck.json", pathlib)
    # build the per-library snapshot path directly from this string, so a
    # name containing a path separator or ".."/"." traversal segment -- or
    # an absolute path, which pathlib silently lets override the left-hand
    # side of the / operator entirely -- would write outside output_dir
    # instead of a same-directory snapshot (Codex review).
    if (
        "/" in name
        or "\\" in name
        or name in (".", "..")
        or any(ord(c) < 0x20 for c in name)
    ):
        sys.exit(f"entry {i} has an invalid \"name\" {name!r} -- must not contain a path separator, be \".\"/\"..\", or contain control characters")
    if name in seen_names:
        # A repeated name would otherwise have its dump silently overwrite
        # the first entry at $OUTPUT_DIR/$name.abicheck.json while the
        # manifest still lists two artifact rows for it (Codex review).
        sys.exit(f"duplicate library name {name!r} (entry {i}) -- each entry needs a unique \"name\"")
    seen_names.add(name)
' "$LIBRARIES_JSON" 2>&1) || _fail "invalid libraries input: $LIBRARIES_ERROR"

if [[ -d "$OUTPUT_DIR" ]]; then
  # Clear stale per-library snapshots/manifest left by an earlier run at
  # this same output-dir -- a library removed/renamed since that run would
  # otherwise leave its old *.abicheck.json sitting here: invisible to this
  # run's manifest.json/content-digest, but still physically present for a
  # caller that publishes/uploads the whole directory rather than iterating
  # manifest.json's artifact list (CodeRabbit review). Only removes the
  # files this script itself writes, never the whole directory, so an
  # output-dir that happens to already exist for an unrelated reason isn't
  # blown away.
  find "$OUTPUT_DIR" -maxdepth 1 \( -name '*.abicheck.json' -o -name '*.abicheck.json.gz' -o -name '*.abicheck.json.zst' \) -delete
  # Same reasoning, for staged bundle-member binaries (G30 P1.6): a member
  # dropped from stage_binary since an earlier run at this same output-dir
  # would otherwise leave its old binary sitting under binaries/, invisible
  # to this run's manifest but still present for a whole-directory upload.
  rm -rf "$OUTPUT_DIR/binaries"
  # Don't delete manifest.json if it IS the caller's previous-manifest -- a
  # workflow that restores the previous baseline set into output-dir before
  # regenerating (an in-place refresh) points previous-manifest at that same
  # file; deleting it here would make build_manifest.py unable to read it,
  # and since a provided-but-missing previous-manifest is now a hard failure,
  # that valid workflow would break instead of just losing freshness
  # detection like before (Codex review). `-ef` compares by inode, so it
  # works regardless of relative/absolute paths or symlinks, and is false
  # (safe to delete) whenever either side doesn't exist yet.
  if [[ -z "$PREVIOUS_MANIFEST" ]] || ! [[ "$OUTPUT_DIR/manifest.json" -ef "$PREVIOUS_MANIFEST" ]]; then
    rm -f "$OUTPUT_DIR/manifest.json"
  fi
fi
mkdir -p "$OUTPUT_DIR"

echo "::group::Dump baseline-set into $OUTPUT_DIR"
# Emit one row per library (name, artifact, header, include, stage_binary --
# header/include default to empty and stage_binary to "", never absent, so
# the bash read below always gets five fields), delimited by ASCII Unit
# Separator (\x1f) rather than a tab: bash's word-splitting always treats a
# literal tab in IFS as "IFS whitespace" and collapses adjacent/empty fields
# regardless of what IFS is set to, so a library with `include` set but
# `header` omitted (an adjacent empty field) would silently shift include's
# value into header. \x1f is not whitespace to bash, so empty fields between
# delimiters are preserved. Python does the JSON parsing; bash just loops.
while IFS=$'\x1f' read -r name artifact header include stage_binary; do
  [[ -z "$name" ]] && continue
  echo "-- $name ($artifact)"
  CMD=(abicheck dump "$artifact")
  if [[ -n "$header" ]]; then
    for h in $header; do CMD+=(-H "$h"); done
  fi
  if [[ -n "$include" ]]; then
    for i in $include; do CMD+=(-I "$i"); done
  fi
  [[ -n "$BUILD_INFO" ]] && CMD+=(--build-info "$BUILD_INFO")
  [[ -n "$DEPTH" ]] && CMD+=(--depth "$DEPTH")
  [[ -n "$PROJECT_REF" ]] && CMD+=(--version "$PROJECT_REF")
  [[ "$SNAPSHOT_COMPRESSION" != "none" ]] && CMD+=(--compression "$SNAPSHOT_COMPRESSION")
  CMD+=(-o "$OUTPUT_DIR/$name$SNAPSHOT_SUFFIX")
  if ! "${CMD[@]}"; then
    _fail "dump failed for library '$name' ($artifact) -- see the command output above."
  fi
  # stage_binary (G30 P1.6, ADR-047 section 6/section 8 S14 correction): a
  # bundle-scoped baseline must preserve each member's real ELF binary
  # alongside its snapshot -- abicheck/bundle.py's build_bundle_snapshot()
  # builds its cross-library graph from real ELF binaries and skips non-ELF
  # (including JSON snapshot) inputs, so a bundle baseline containing only
  # snapshots would silently produce no old-side bundle data. Staged under
  # $OUTPUT_DIR/binaries/$name (no extension -- resolve_bundle() parses the
  # file's own ELF header, never its name), reusing the same path-safety
  # guarantee $name already has (run.sh's own input validation above rejects
  # a name containing a path separator or a ".."/"." traversal segment).
  if [[ "$stage_binary" == "1" ]]; then
    mkdir -p "$OUTPUT_DIR/binaries"
    if ! cp "$artifact" "$OUTPUT_DIR/binaries/$name"; then
      _fail "failed to stage binary for library '$name' ($artifact) into $OUTPUT_DIR/binaries/ -- see the error above."
    fi
  fi
done < <(python3 -c '
import json, sys
for e in json.loads(sys.argv[1]):
    print("\x1f".join([
        e["name"],
        e["artifact"],
        e.get("header", ""),
        e.get("include", ""),
        "1" if e.get("stage_binary") else "",
    ]))
' "$LIBRARIES_JSON" | tr -d '\r')
# ^ Windows CPython opens stdout in text mode, so `print()` translates \n to
# \r\n there; bash `read` only strips the trailing \n, leaving a stray \r
# glued onto the last field of every row. For a row whose last field is
# meant to be empty (include omitted), that \r makes `[[ -n "$include" ]]`
# true, so an empty -I flag was silently added on Windows runners even
# though include was never set (caught by the windows-latest CI lane).
echo "::endgroup::"

if [[ "$VALIDATION" == "strict" ]]; then
  echo "::group::Self-compare validation (each snapshot against itself)"
  while IFS=$'\x1f' read -r name _artifact _header _include; do
    [[ -z "$name" ]] && continue
    snap="$OUTPUT_DIR/$name$SNAPSHOT_SUFFIX"
    if ! abicheck compare "$snap" "$snap" --format json > /dev/null; then
      _fail "self-compare failed for '$snap' -- the snapshot this run just wrote is not loadable/self-consistent. This should never happen; please report it."
    fi
  done < <(python3 -c '
import json, sys
for e in json.loads(sys.argv[1]):
    print(e["name"])
' "$LIBRARIES_JSON" | tr -d '\r')
  echo "all snapshots round-tripped cleanly."
  echo "::endgroup::"
fi

MANIFEST_PATH="$OUTPUT_DIR/manifest.json"
MANIFEST_ARGS=(
  --output-dir "$OUTPUT_DIR"
  --project-ref "$PROJECT_REF"
  --profile "$PROFILE"
  --libraries "$LIBRARIES_JSON"
  --manifest-out "$MANIFEST_PATH"
  --baseline-generation "$BASELINE_GENERATION"
)
[[ -n "$PREVIOUS_MANIFEST" ]] && MANIFEST_ARGS+=(--previous-manifest "$PREVIOUS_MANIFEST")

MANIFEST_STDOUT=$(python3 "$ACTION_PATH/build_manifest.py" "${MANIFEST_ARGS[@]}") \
  || _fail "manifest generation failed -- see output above."
echo "$MANIFEST_STDOUT"

{
  echo "baseline-path=$OUTPUT_DIR"
  echo "manifest-path=$MANIFEST_PATH"
  echo "$MANIFEST_STDOUT"
} >> "${GITHUB_OUTPUT:-/dev/null}"

echo "baseline-set written: $OUTPUT_DIR ($MANIFEST_PATH)"
