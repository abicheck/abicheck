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
PREVIOUS_MANIFEST="${INPUT_PREVIOUS_MANIFEST:-}"
VALIDATION="${INPUT_VALIDATION:-strict}"
ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

_fail() {
  echo "::error::$1"
  exit 1
}

case "$VALIDATION" in
  strict | none) ;;
  *) _fail "validation '$VALIDATION' is not recognized. Use 'strict' or 'none'." ;;
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
  find "$OUTPUT_DIR" -maxdepth 1 -name '*.abicheck.json' -delete
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
# Emit one row per library (name, artifact, header, include -- header/
# include default to empty, never absent, so the bash read below always
# gets four fields), delimited by ASCII Unit Separator (\x1f) rather than a
# tab: bash's word-splitting always treats a literal tab in IFS as "IFS
# whitespace" and collapses adjacent/empty fields regardless of what IFS is
# set to, so a library with `include` set but `header` omitted (an adjacent
# empty field) would silently shift include's value into header. \x1f is not
# whitespace to bash, so empty fields between delimiters are preserved.
# Python does the JSON parsing; bash just loops.
while IFS=$'\x1f' read -r name artifact header include; do
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
  CMD+=(-o "$OUTPUT_DIR/$name.abicheck.json")
  if ! "${CMD[@]}"; then
    _fail "dump failed for library '$name' ($artifact) -- see the command output above."
  fi
done < <(python3 -c '
import json, sys
for e in json.loads(sys.argv[1]):
    print("\x1f".join([
        e["name"],
        e["artifact"],
        e.get("header", ""),
        e.get("include", ""),
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
    snap="$OUTPUT_DIR/$name.abicheck.json"
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
