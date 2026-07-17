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
    if name in seen_names:
        # A repeated name would otherwise have its dump silently overwrite
        # the first entry at $OUTPUT_DIR/$name.abicheck.json while the
        # manifest still lists two artifact rows for it (Codex review).
        sys.exit(f"duplicate library name {name!r} (entry {i}) -- each entry needs a unique \"name\"")
    seen_names.add(name)
' "$LIBRARIES_JSON" 2>&1) || _fail "invalid libraries input: $LIBRARIES_ERROR"

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
' "$LIBRARIES_JSON")
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
' "$LIBRARIES_JSON")
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
