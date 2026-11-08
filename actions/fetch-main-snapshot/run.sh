#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# The git + GitHub API half of actions/fetch-main-snapshot. Every DECISION is
# in abicheck.frontends.action.main_snapshot; this script only gathers facts,
# and keeps "the call failed" (lookup_failed) apart from "nothing is there".
# A non-resolved outcome is a normal result: the step succeeds and reports it.
set -euo pipefail

_fail() { echo "::error::$*"; exit 1; }
CLI=(python -m abicheck.frontends.action.cli)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
BRANCH="${INPUT_BRANCH:-main}"
STORAGE="${INPUT_STORAGE:-artifact}"
case "$STORAGE" in
  artifact) WORKFLOW="$(basename "${INPUT_WORKFLOW:?workflow is required with storage: artifact}")" ;;
  cache) ;;
  *) _fail "storage must be artifact or cache (got '$STORAGE')." ;;
esac

MODE="${INPUT_MODE:-auto}"
if [[ "$MODE" == auto ]]; then
  if [[ "${EVENT_NAME:-}" == merge_group ]]; then MODE=merge-group; else MODE=merge-commit; fi
fi

PARENTS="" MERGE_BASES=""
case "$MODE" in
  merge-commit)
    PARENTS=$(git rev-list --parents -n1 HEAD 2>/dev/null | cut -d' ' -f2- || true)
    # A parent listed but absent from a shallow clone is no usable base.
    for p in $PARENTS; do git cat-file -e "$p^{commit}" 2>/dev/null || PARENTS=""; done
    ;;
  head)
    REF="origin/$BRANCH"
    git rev-parse -q --verify "$REF" >/dev/null \
      || git fetch -q origin "$BRANCH:refs/remotes/origin/$BRANCH" 2>/dev/null \
      || REF="$BRANCH"
    MERGE_BASES=$(git merge-base --all "$REF" HEAD 2>/dev/null | tr '\n' ' ' || true)
    ;;
  merge-group) ;;
  *) _fail "mode must be auto, merge-commit, head or merge-group (got '$MODE')." ;;
esac

_finish() {  # outcome is already in $GITHUB_OUTPUT; mirror it to the summary
  local msg
  msg=$(grep '^message=' "$GITHUB_OUTPUT" | tail -n1 | cut -d= -f2-)
  echo "::notice::abicheck delta base: ${msg}"
  exit 0
}

rc=0
"${CLI[@]}" select-base --mode "$MODE" --head-parents "$PARENTS" \
  --merge-bases "$MERGE_BASES" --merge-group-base "${MERGE_GROUP_BASE:-}" \
  --branch "$BRANCH" --github-output "$WORK/base.out" || rc=$?
cat "$WORK/base.out" >> "$GITHUB_OUTPUT"
[[ "$rc" == 0 || "$rc" == 3 ]] || _fail "select-base failed with exit $rc."
[[ "$rc" == 0 ]] || _finish
BASE=$(grep '^base-sha=' "$WORK/base.out" | cut -d= -f2)

if [[ "$STORAGE" == cache ]]; then
  # The cache is restored by the next (actions/cache/restore) step and
  # checked by verify-cache.sh. Until then the outcome is a failed lookup,
  # so a step that never ran cannot leave `resolved` behind with no file.
  KEY=$("${CLI[@]}" snapshot-cache-key "$BASE" --profile "${INPUT_PROFILE:-}")
  STAGE="${GITHUB_WORKSPACE:-$PWD}/.abicheck-main-snapshot-cache"
  [[ ! -e "$STAGE" ]] || _fail "$STAGE already exists; it is reserved for the restored cache entry."
  {
    echo "outcome=lookup_failed"
    echo "message=Delta unavailable: the cache lookup did not complete."
    echo "cache-key=$KEY"
  } >> "$GITHUB_OUTPUT"
  echo "base $BASE: restoring cache key $KEY"
  exit 0
fi
NAME=$("${CLI[@]}" snapshot-name "$BASE" --profile "${INPUT_PROFILE:-}")

_lookup() {  # $1 = endpoint, $2 = jq path, $3 = output; paginated, flattened
  if gh api -X GET "$1" "${@:4}" -f per_page=100 --paginate --jq "$2" \
       > "$WORK/pages.json" 2>"$WORK/err"; then
    "${CLI[@]}" flatten-pages "$WORK/pages.json" "$3"
  else
    cat "$WORK/err" >&2; echo '[]' > "$3"; return 1
  fi
}

deadline=$(( $(date +%s) + ${INPUT_WAIT_TIMEOUT:-0} ))
while :; do
  LOOKUP=()
  _lookup "repos/$REPO/actions/artifacts" '.artifacts' "$WORK/artifacts.json" \
    -f "name=$NAME" || LOOKUP=(--lookup-failed)
  _lookup "repos/$REPO/actions/workflows/$WORKFLOW/runs" '.workflow_runs' "$WORK/runs.json" \
    -f "head_sha=$BASE" || LOOKUP=(--lookup-failed)
  : > "$WORK/resolve.out"
  rc=0
  "${CLI[@]}" resolve-snapshot "$WORK/artifacts.json" "$WORK/runs.json" \
    --repository "$REPO" --workflow ".github/workflows/$WORKFLOW" --branch "$BRANCH" \
    --base-sha "$BASE" --artifact-name "$NAME" \
    $([[ "$MODE" == merge-group ]] && echo --merge-group) \
    ${LOOKUP[@]+"${LOOKUP[@]}"} --github-output "$WORK/resolve.out" || rc=$?
  [[ "$rc" == 0 || "$rc" == 3 ]] || _fail "resolve-snapshot failed with exit $rc."
  OUTCOME=$(grep '^outcome=' "$WORK/resolve.out" | cut -d= -f2)
  if [[ "$OUTCOME" == base_pending && $(date +%s) -lt $deadline ]]; then
    echo "snapshot run for $BASE still in progress; waiting"; sleep 30; continue
  fi
  break
done
if [[ "$OUTCOME" != resolved ]]; then
  grep -v '^base-sha=' "$WORK/resolve.out" >> "$GITHUB_OUTPUT"
  _finish
fi

# `resolved` is published only once the snapshot is on disk. The extraction
# goes into a fresh directory of its own under `destination`, so nothing the
# caller already keeps there is touched.
_unavailable() {
  {
    echo "outcome=lookup_failed"
    echo "message=Delta unavailable: $1"
  } >> "$GITHUB_OUTPUT"
  _finish
}
ARTIFACT_ID=$(grep '^artifact-id=' "$WORK/resolve.out" | cut -d= -f2)
gh api "repos/$REPO/actions/artifacts/$ARTIFACT_ID/zip" > "$WORK/snapshot.zip" 2>"$WORK/err" \
  || { cat "$WORK/err" >&2; _unavailable "downloading snapshot artifact $ARTIFACT_ID failed."; }
mkdir -p "${INPUT_DESTINATION:?}"
DEST=$(mktemp -d "$INPUT_DESTINATION/snapshot.XXXXXX")
"${CLI[@]}" extract-artifact "$WORK/snapshot.zip" "$DEST" \
  --max-total-bytes 1073741824 --max-entry-bytes 1073741824 \
  || _unavailable "snapshot artifact $ARTIFACT_ID could not be extracted safely."
SNAP="$DEST/snapshot.json"
[[ -f "$SNAP" ]] || _unavailable "snapshot artifact $ARTIFACT_ID holds no snapshot.json."
grep -v '^base-sha=' "$WORK/resolve.out" >> "$GITHUB_OUTPUT"
echo "snapshot-path=$SNAP" >> "$GITHUB_OUTPUT"
_finish
