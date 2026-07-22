#!/usr/bin/env bash
# Resolves channel x target/bundle x profile against an already-staged
# baseline-set to one of ADR-047 Section 6's typed outcomes -- never a
# compatibility verdict, never silently degraded to "no baseline = compatible".
# See actions/resolve-baseline/action.yml for the input/output contract and
# actions/resolve-baseline/resolve_baseline.py for the pure resolution logic
# (abicheck/buildsource/baseline_set.py).
set -uo pipefail

_fail() {
  echo "::error::$1"
  exit 1
}

BASELINE_PATH="${INPUT_BASELINE_PATH:?baseline-path input is required}"
CHANNEL="${INPUT_CHANNEL:?channel input is required}"
KIND="${INPUT_KIND:-target}"
TARGET="${INPUT_TARGET:-}"
BUNDLE="${INPUT_BUNDLE:-}"
BUNDLE_MEMBERS="${INPUT_BUNDLE_MEMBERS:-[]}"
PROFILE="${INPUT_PROFILE:?profile input is required}"
REQUIRED="${INPUT_REQUIRED:-true}"
CANDIDATE_BUILD_OUTPUT="${INPUT_CANDIDATE_BUILD_OUTPUT:-}"
ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

case "$KIND" in
  target | bundle) ;;
  *) _fail "kind '$KIND' is not recognized. Use 'target' or 'bundle'." ;;
esac
case "$REQUIRED" in
  true | false) ;;
  *) _fail "required '$REQUIRED' is not recognized. Use 'true' or 'false'." ;;
esac
if [[ "$KIND" == "target" && -z "$TARGET" ]]; then
  _fail "target input is required when kind is 'target'."
fi
if [[ "$KIND" == "bundle" && -z "$BUNDLE" ]]; then
  _fail "bundle input is required when kind is 'bundle'."
fi

# Resolve BASELINE_PATH to a directory this run can read manifest.json from:
# use it directly if it already is one (the calling workflow already
# downloaded/restored it via actions/cache, actions/download-artifact, or gh
# release download -- this Action does not itself know how to fetch from a
# baseline channel's storage backend, that orchestration stays the caller's
# job per ADR-047 Section 10's storage-backend table), or extract it here if
# it's an archive.
_EXTRACT_DIR=""
if [[ -d "$BASELINE_PATH" ]]; then
  BASELINE_DIR="$BASELINE_PATH"
elif [[ -f "$BASELINE_PATH" ]]; then
  _EXTRACT_DIR=$(mktemp -d)
  BASELINE_DIR="$_EXTRACT_DIR"
  echo "::group::Extract baseline-set archive $BASELINE_PATH"
  case "$BASELINE_PATH" in
    *.tar.zst)
      tar --zstd -xf "$BASELINE_PATH" -C "$BASELINE_DIR" \
        || _fail "failed to extract $BASELINE_PATH (tar --zstd)."
      ;;
    *.tar.gz | *.tgz)
      tar -xzf "$BASELINE_PATH" -C "$BASELINE_DIR" \
        || _fail "failed to extract $BASELINE_PATH (tar -xzf)."
      ;;
    *.tar)
      tar -xf "$BASELINE_PATH" -C "$BASELINE_DIR" \
        || _fail "failed to extract $BASELINE_PATH (tar -xf)."
      ;;
    *)
      _fail "baseline-path '$BASELINE_PATH' is a file but not a recognized archive (.tar.zst/.tar.gz/.tgz/.tar)."
      ;;
  esac
  echo "::endgroup::"
  # An archive may itself contain one nested directory (e.g. the
  # profile-named dir the archive was built from) rather than manifest.json
  # at its root -- if there's no manifest.json at the extraction root but
  # there's exactly one subdirectory, descend into it. 0 or >1 candidates is
  # left to resolve_baseline.py's own not_found/ambiguous handling rather
  # than guessed here.
  if [[ ! -f "$BASELINE_DIR/manifest.json" ]]; then
    _SUBDIRS=()
    while IFS= read -r -d '' d; do _SUBDIRS+=("$d"); done \
      < <(find "$BASELINE_DIR" -mindepth 1 -maxdepth 1 -type d -print0)
    if [[ ${#_SUBDIRS[@]} -eq 1 && -f "${_SUBDIRS[0]}/manifest.json" ]]; then
      BASELINE_DIR="${_SUBDIRS[0]}"
    fi
  fi
else
  # Neither a directory nor a file -- this is the not_found case, not a
  # usage error: the calling workflow may legitimately have nothing staged
  # yet (e.g. the very first release-contract publish). Let
  # resolve_baseline.py's own not_found/bootstrap handling decide based on
  # `required`, rather than hard-failing here regardless of that input.
  BASELINE_DIR="$BASELINE_PATH"
fi

CANDIDATE_EVIDENCE_PRODUCER=""
if [[ -n "$CANDIDATE_BUILD_OUTPUT" ]]; then
  if [[ ! -f "$CANDIDATE_BUILD_OUTPUT" ]]; then
    _fail "candidate-build-output '$CANDIDATE_BUILD_OUTPUT' does not exist."
  fi
  CANDIDATE_EVIDENCE_PRODUCER=$(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
print(json.dumps(data.get("evidence_producer") or {}))
' "$CANDIDATE_BUILD_OUTPUT") || _fail "failed to read evidence_producer from $CANDIDATE_BUILD_OUTPUT."
fi

RESOLVE_ARGS=(
  --baseline-dir "$BASELINE_DIR"
  --kind "$KIND"
  --profile "$PROFILE"
  --required "$REQUIRED"
)
if [[ "$KIND" == "target" ]]; then
  RESOLVE_ARGS+=(--name "$TARGET")
else
  RESOLVE_ARGS+=(--name "$BUNDLE" --members "$BUNDLE_MEMBERS")
fi
if [[ -n "$CANDIDATE_EVIDENCE_PRODUCER" ]]; then
  RESOLVE_ARGS+=(--candidate-evidence-producer "$CANDIDATE_EVIDENCE_PRODUCER")
fi

echo "::group::Resolve baseline ($CHANNEL / $KIND / $PROFILE)"
set +e
RESOLVE_STDOUT=$(python3 "$ACTION_PATH/resolve_baseline.py" "${RESOLVE_ARGS[@]}")
RESOLVE_EXIT=$?
set -e
echo "$RESOLVE_STDOUT"
echo "::endgroup::"

{
  echo "$RESOLVE_STDOUT"
  echo "channel=$CHANNEL"
} >> "${GITHUB_OUTPUT:-/dev/null}"

OUTCOME=$(echo "$RESOLVE_STDOUT" | sed -n 's/^outcome=//p')
MESSAGE=$(echo "$RESOLVE_STDOUT" | sed -n 's/^message=//p')
BOOTSTRAP=$(echo "$RESOLVE_STDOUT" | sed -n 's/^bootstrap=//p')

# NOTE: deliberately no cleanup of $_EXTRACT_DIR here. A `resolved` outcome's
# snapshot-path/binaries-dir outputs point *inside* it -- a downstream step
# in this same job (e.g. the root Action's compare step) still needs to read
# from there. The runner reclaims the whole temp area when the job ends.
if [[ $RESOLVE_EXIT -eq 64 ]]; then
  _fail "resolve-baseline usage error: $MESSAGE"
elif [[ "$OUTCOME" == "not_found" && "$BOOTSTRAP" == "true" ]]; then
  echo "::notice::baseline not found for channel '$CHANNEL' ($MESSAGE) -- bootstrap: required=false, treating as an advisory 'no baseline yet' pass."
  exit 0
elif [[ $RESOLVE_EXIT -ne 0 ]]; then
  _fail "resolve-baseline failed ($OUTCOME): $MESSAGE"
fi

echo "resolved baseline: outcome=$OUTCOME"
exit 0
