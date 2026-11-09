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

# base-equivalent-paths (main_snapshot_ancestor): a malformed declaration is
# a configuration error, refused before anything is looked up. A value with
# only blank or comment lines leaves the rule off.
EQUIV_PATHS="${INPUT_BASE_EQUIVALENT_PATHS:-}"
WALK=false
if [[ -n "${EQUIV_PATHS//[[:space:]]/}" ]]; then
  specs=$("${CLI[@]}" equivalent-pathspecs --paths "$EQUIV_PATHS" | tr '\0' '\n') \
    || _fail "base-equivalent-paths is not usable (the reason is above)."
  [[ -n "$specs" ]] && WALK=true
fi

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

_field() {  # $1 = key, $2 = file: the last value written for it
  grep "^$1=" "$2" | tail -n1 | cut -d= -f2- || true
}

# The path-equivalent ancestor walk's candidates for $BASE, into
# $WORK/plan.out. A shallow clone whose history ends before the walk could
# finish is deepened once along the base; if that fails the plan says
# `history_unavailable` -- the walk never guesses past what it cannot see.
_plan_walk() {
  local attempt
  for attempt in 1 2; do
    : > "$WORK/plan.out"
    if ! "${CLI[@]}" plan-ancestor-walk --base-sha "$BASE" --paths "$EQUIV_PATHS" \
         --branch "$BRANCH" --github-output "$WORK/plan.out"; then
      printf 'exhausted=history_unavailable\nexhausted-message=No path-equivalent ancestor: the history of `%s@%s` is not in this checkout (use `fetch-depth: 0`).\n' \
        "$BRANCH" "${BASE:0:7}" > "$WORK/plan.out"
    fi
    [[ "$attempt" == 1 && "$(_field exhausted "$WORK/plan.out")" == history_unavailable ]] || return 0
    [[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" == true ]] || return 0
    echo "deepening the shallow checkout along $BASE for base-equivalent-paths"
    git fetch -q --no-tags --no-recurse-submodules --deepen=51 origin "$BASE" 2>/dev/null \
      || git fetch -q --no-tags --no-recurse-submodules --deepen=51 origin "refs/heads/$BRANCH" 2>/dev/null \
      || { echo "::warning::abicheck: could not deepen the checkout for base-equivalent-paths."; return 0; }
  done
}

# Record how the walk ended without a usable ancestor: the base's own
# outcome stands, its message gains the walk's reason.
_walk_exhausted() {  # $1 = walk result, $2 = its sentence; outcome already written
  local msg
  msg=$(grep '^message=' "$GITHUB_OUTPUT" | tail -n1 | cut -d= -f2-)
  printf 'ancestor-walk=%s\nmessage=%s %s\n' "$1" "$msg" "$2" >> "$GITHUB_OUTPUT"
}

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
  # Refused before anything is restored: a job reading a cache scope that
  # `$BRANCH` pushes do not own (another PR base, a merge queue) never
  # resolves from the cache.
  rc=0
  "${CLI[@]}" cache-scope-check --event "${EVENT_NAME:-}" --ref "${GITHUB_REF:-}" \
    --base-ref "${PR_BASE_REF:-}" --branch "$BRANCH" --github-output "$WORK/scope.out" || rc=$?
  [[ "$rc" == 0 || "$rc" == 3 ]] || _fail "cache-scope-check failed with exit $rc."
  if [[ "$rc" == 3 ]]; then
    cat "$WORK/scope.out" >> "$GITHUB_OUTPUT"
    _finish
  fi
  STAGE="${GITHUB_WORKSPACE:-$PWD}/.abicheck-main-snapshot-cache"
  [[ ! -e "$STAGE" ]] || _fail "$STAGE already exists; it is reserved for the restored cache entry."
  CACHE_SHA="$BASE"
  KEY=$("${CLI[@]}" snapshot-cache-key "$BASE" --profile "${INPUT_PROFILE:-}")
  if [[ "$WALK" == true ]]; then
    # Which commits have an entry is known only from the caches API (the
    # restore step takes one exact key), so the walk lists exact keys in
    # `refs/heads/$BRANCH` -- the only scope the producer saves to. Needs
    # `actions: read`; a failed listing is an explicit lookup_failed.
    _has_entry() {  # $1 = exact key; 0 present, 1 absent, 2 listing failed
      local keys
      keys=$(gh api -X GET "repos/$REPO/actions/caches" -f "key=$1" -f "ref=refs/heads/$BRANCH" \
        -f per_page=100 --paginate --jq '.actions_caches[].key' 2>"$WORK/err") \
        || { cat "$WORK/err" >&2; return 2; }
      grep -Fxq -- "$1" <<<"$keys"
    }
    rc=0; _has_entry "$KEY" || rc=$?
    if [[ "$rc" == 2 ]]; then
      printf 'outcome=lookup_failed\nmessage=Delta unavailable: listing the cache entries failed (base-equivalent-paths with storage: cache needs `actions: read`).\n' >> "$GITHUB_OUTPUT"
      _finish
    fi
    if [[ "$rc" == 1 ]]; then
      _plan_walk
      found="" result=""
      while IFS= read -r cand; do
        [[ -n "$cand" ]] || continue
        ckey=$("${CLI[@]}" snapshot-cache-key "$cand" --profile "${INPUT_PROFILE:-}")
        rc=0; _has_entry "$ckey" || rc=$?
        if [[ "$rc" == 0 ]]; then found="$cand"; KEY="$ckey"; break; fi
        if [[ "$rc" == 2 ]]; then result=lookup_failed; break; fi
      done < <(grep '^candidate=' "$WORK/plan.out" | cut -d= -f2)
      if [[ -z "$found" ]]; then
        printf 'outcome=base_not_snapshotted\nmessage=%s\n' \
          "Delta unavailable: no cache entry for \`$BRANCH@${BASE:0:7}\`." >> "$GITHUB_OUTPUT"
        if [[ "$result" == lookup_failed ]]; then
          _walk_exhausted lookup_failed "No path-equivalent ancestor: listing the cache entries failed during the walk."
        else
          _walk_exhausted "$(_field exhausted "$WORK/plan.out")" "$(_field exhausted-message "$WORK/plan.out")"
        fi
        _finish
      fi
      CACHE_SHA="$found"
      echo "ancestor-walk=found" >> "$GITHUB_OUTPUT"
      echo "base $BASE has no cache entry; its path-equivalent ancestor $found has one"
    fi
  fi
  {
    echo "outcome=lookup_failed"
    echo "message=Delta unavailable: the cache lookup did not complete."
    echo "cache-key=$KEY"
    echo "cache-sha=$CACHE_SHA"
  } >> "$GITHUB_OUTPUT"
  echo "base $BASE: restoring cache key $KEY"
  exit 0
fi

_lookup() {  # $1 = endpoint, $2 = jq path, $3 = output; paginated, flattened
  if gh api -X GET "$1" "${@:4}" -f per_page=100 --paginate --jq "$2" \
       > "$WORK/pages.json" 2>"$WORK/err"; then
    "${CLI[@]}" flatten-pages "$WORK/pages.json" "$3"
  else
    cat "$WORK/err" >&2; echo '[]' > "$3"; return 1
  fi
}

# The artifact lookup for commit $1 into $WORK/resolve.out; prints its outcome.
# $2 (optional) is the PR's base when $1 is a path-equivalent ancestor of it:
# every provenance check still binds the artifact to $1.
_resolve_artifact() {
  local sha="$1" equivalent="${2:-}" name rc=0
  local -a lookup=() extra=()
  # errexit does not reach into this function's $(...) callers: fail
  # explicitly, or an empty name would read as "no snapshot".
  name=$("${CLI[@]}" snapshot-name "$sha" --profile "${INPUT_PROFILE:-}") \
    || _fail "no artifact name for $sha (profile '${INPUT_PROFILE:-}')." >&2
  _lookup "repos/$REPO/actions/artifacts" '.artifacts' "$WORK/artifacts.json" \
    -f "name=$name" >&2 || lookup=(--lookup-failed)
  _lookup "repos/$REPO/actions/workflows/$WORKFLOW/runs" '.workflow_runs' "$WORK/runs.json" \
    -f "head_sha=$sha" >&2 || lookup=(--lookup-failed)
  [[ -n "$equivalent" ]] && extra=(--equivalent-to "$equivalent")
  [[ "$MODE" == merge-group ]] && extra+=(--merge-group)
  : > "$WORK/resolve.out"
  "${CLI[@]}" resolve-snapshot "$WORK/artifacts.json" "$WORK/runs.json" \
    --repository "$REPO" --workflow ".github/workflows/$WORKFLOW" --branch "$BRANCH" \
    --base-sha "$sha" --artifact-name "$name" \
    ${extra[@]+"${extra[@]}"} ${lookup[@]+"${lookup[@]}"} \
    --github-output "$WORK/resolve.out" >&2 || rc=$?
  # Called inside $(...): the annotation goes to stderr, or the caller's
  # command substitution would swallow it.
  [[ "$rc" == 0 || "$rc" == 3 ]] || _fail "resolve-snapshot failed with exit $rc." >&2
  local outcome
  outcome=$(_field outcome "$WORK/resolve.out")
  [[ -n "$outcome" ]] || _fail "resolve-snapshot wrote no outcome." >&2
  printf '%s\n' "$outcome"
}

SNAPSHOT_SHA="$BASE"
deadline=$(( $(date +%s) + ${INPUT_WAIT_TIMEOUT:-0} ))
while :; do
  OUTCOME=$(_resolve_artifact "$BASE")
  if [[ "$OUTCOME" == base_pending && $(date +%s) -lt $deadline ]]; then
    echo "snapshot run for $BASE still in progress; waiting"; sleep 30; continue
  fi
  break
done
if [[ "$OUTCOME" != resolved && "$WALK" == true ]] \
   && "${CLI[@]}" ancestor-walkable "$OUTCOME"; then
  cp "$WORK/resolve.out" "$WORK/base-resolve.out"
  _plan_walk
  found="" result=""
  while IFS= read -r cand; do
    [[ -n "$cand" ]] || continue
    cand_outcome=$(_resolve_artifact "$cand" "$BASE")
    case "$cand_outcome" in
      resolved) found="$cand"; break ;;
      lookup_failed) result=lookup_failed; break ;;
    esac
  done < <(grep '^candidate=' "$WORK/plan.out" | cut -d= -f2)
  if [[ -n "$found" ]]; then
    OUTCOME=resolved SNAPSHOT_SHA="$found"
    echo "ancestor-walk=found" >> "$GITHUB_OUTPUT"
    echo "base $BASE has no snapshot; using its path-equivalent ancestor $found"
  else
    grep -v '^base-sha=' "$WORK/base-resolve.out" >> "$GITHUB_OUTPUT"
    if [[ "$result" == lookup_failed ]]; then
      _walk_exhausted lookup_failed "No path-equivalent ancestor: a snapshot lookup failed during the walk (API error)."
    else
      _walk_exhausted "$(_field exhausted "$WORK/plan.out")" "$(_field exhausted-message "$WORK/plan.out")"
    fi
    _finish
  fi
fi
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
printf 'snapshot-path=%s\nsnapshot-sha=%s\n' "$SNAP" "$SNAPSHOT_SHA" >> "$GITHUB_OUTPUT"
_finish
