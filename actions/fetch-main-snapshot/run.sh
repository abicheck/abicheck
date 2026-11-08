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
WORKFLOW="$(basename "${INPUT_WORKFLOW:?workflow is required}")"

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
NAME=$("${CLI[@]}" snapshot-name "$BASE" --profile "${INPUT_PROFILE:-}")

deadline=$(( $(date +%s) + ${INPUT_WAIT_TIMEOUT:-0} ))
while :; do
  LOOKUP=()
  if ! gh api -X GET "repos/$REPO/actions/artifacts" -f "name=$NAME" -f per_page=100 \
       > "$WORK/artifacts.json" 2>"$WORK/err"; then
    cat "$WORK/err" >&2; echo '{"artifacts":[]}' > "$WORK/artifacts.json"; LOOKUP=(--lookup-failed)
  fi
  if ! gh api -X GET "repos/$REPO/actions/workflows/$WORKFLOW/runs" -f "head_sha=$BASE" \
       -f per_page=100 > "$WORK/runs.json" 2>"$WORK/err"; then
    cat "$WORK/err" >&2; echo '{"workflow_runs":[]}' > "$WORK/runs.json"; LOOKUP=(--lookup-failed)
  fi
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
grep -v '^base-sha=' "$WORK/resolve.out" >> "$GITHUB_OUTPUT"
[[ "$OUTCOME" == resolved ]] || _finish

ARTIFACT_ID=$(grep '^artifact-id=' "$WORK/resolve.out" | cut -d= -f2)
gh api "repos/$REPO/actions/artifacts/$ARTIFACT_ID/zip" > "$WORK/snapshot.zip" \
  || _fail "downloading artifact $ARTIFACT_ID failed."
DEST="${INPUT_DESTINATION:?}"
rm -rf "$DEST"
"${CLI[@]}" extract-artifact "$WORK/snapshot.zip" "$DEST"
SNAP=$(find "$DEST" -type f -name '*.json' | head -n1)
[[ -n "$SNAP" ]] || _fail "artifact $ARTIFACT_ID held no snapshot file."
echo "snapshot-path=$SNAP" >> "$GITHUB_OUTPUT"
_finish
