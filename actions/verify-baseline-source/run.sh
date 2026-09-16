#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# The GitHub API half of actions/verify-baseline-source. Every DECISION lives
# in abicheck.frontends.action.baseline_source; this script only fetches the
# documents those decisions are made from, and keeps "the call failed" apart
# from "the thing does not exist" -- which is the one distinction a bare
# `|| true` destroys, and the reason a transient API error must never read as
# "no baseline, therefore compatible".
set -euo pipefail

_fail() { echo "::error::$*"; exit 1; }
_REFUSED=3

MODE="${INPUT_MODE:-producer-run}"
REPO="${INPUT_EXPECT_REPOSITORY:?expect-repository is required}"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

_emit_ineligible() {
  echo "eligible=false" >> "$GITHUB_OUTPUT"
  exit "${1:-0}"
}

case "$MODE" in
  tag)
    TAG="${INPUT_TAG:?tag input is required when mode is 'tag'}"
    case "$TAG" in
      *$'\n'*|*/*) _fail "tag '$TAG' contains a newline or a path separator." ;;
    esac

    # Three outcomes, three different shapes -- and they are told apart by
    # the HTTP status, not by an empty body. `gh api` exits 1 for both a 404
    # and a network/auth failure, so the status is read explicitly.
    # `set +e` around the call, not `$( ... ; echo $?)`: command substitution
    # inherits `set -e`, so a failing gh would abort the subshell before the
    # status could be echoed and take the whole script with it -- turning the
    # "this ref does not exist" case, which is a normal answer, into a crash.
    set +e
    gh api "repos/$REPO/git/ref/tags/$TAG" --cache 0s \
      > "$WORK/ref.json" 2>"$WORK/ref.err"
    status=$?
    set -e
    LOOKUP_FLAG=()
    if [[ "$status" != 0 ]]; then
      : > "$WORK/ref.json"
      if grep -qi 'not found\|HTTP 404' "$WORK/ref.err"; then
        # A genuine "this ref does not exist": leave ref.json empty, which the
        # decision layer reads as not_a_tag.
        :
      else
        cat "$WORK/ref.err" >&2
        LOOKUP_FLAG=(--lookup-failed)
      fi
    fi

    TAGOBJ_ARGS=()
    if [[ -s "$WORK/ref.json" ]]; then
      obj_type=$(python3 -I -c '
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
obj = doc.get("object") or {}
print(obj.get("type", ""), obj.get("sha", ""))
' "$WORK/ref.json") || _fail "could not read the git-ref response."
      set -- $obj_type
      if [[ "${1:-}" == "tag" ]]; then
        # Annotated tag: peel it. Failing to fetch this is a lookup failure,
        # never "not a tag" -- the ref demonstrably exists.
        if ! gh api "repos/$REPO/git/tags/${2}" > "$WORK/tagobj.json" 2>&1; then
          cat "$WORK/tagobj.json" >&2
          : > "$WORK/tagobj.json"
          LOOKUP_FLAG=(--lookup-failed)
        fi
        TAGOBJ_ARGS=(--tag-object-json "$WORK/tagobj.json")
      fi
    fi

    rc=0
    # ${arr[@]+"${arr[@]}"}, not a bare "${arr[@]}": under macOS's stock
    # (GPLv2-frozen) bash 3.2's set -u, expanding an *empty* array as
    # "${arr[@]}" is itself treated as an unbound-variable reference (bash
    # 4.4+ special-cased this away). Every array below is legitimately empty
    # on a normal path, so a bare expansion aborts the step outright -- the
    # same trap action/run.sh already guards against throughout.
    python -m abicheck.frontends.action.cli verify-tag "$TAG" \
      --ref-json "$WORK/ref.json" \
      ${TAGOBJ_ARGS[@]+"${TAGOBJ_ARGS[@]}"} \
      ${LOOKUP_FLAG[@]+"${LOOKUP_FLAG[@]}"} \
      --built-sha "${INPUT_BUILT_SHA:-}" \
      --github-output "$GITHUB_OUTPUT" || rc=$?
    if [[ "$rc" == "$_REFUSED" ]]; then
      _emit_ineligible 0
    elif [[ "$rc" != 0 ]]; then
      _fail "verifying tag '$TAG' failed with exit $rc."
    fi
    echo "eligible=true" >> "$GITHUB_OUTPUT"
    ;;

  producer-run)
    WORKFLOW="${INPUT_WORKFLOW:?workflow input is required when mode is 'producer-run'}"
    QUERY=(-f status=completed)
    [[ -z "${INPUT_EXPECT_EVENT:-}" ]] || QUERY+=(-f "event=${INPUT_EXPECT_EVENT}")
    [[ -z "${INPUT_EXPECT_HEAD_SHA:-}" ]] || QUERY+=(-f "head_sha=${INPUT_EXPECT_HEAD_SHA}")
    [[ -z "${INPUT_EXPECT_HEAD_BRANCH:-}" ]] || QUERY+=(-f "branch=${INPUT_EXPECT_HEAD_BRANCH}")

    LOOKUP_FLAG=()
    if ! gh api -X GET "repos/$REPO/actions/workflows/$(basename "$WORKFLOW")/runs" \
         ${QUERY[@]+"${QUERY[@]}"} --paginate --jq '.workflow_runs' > "$WORK/raw.json" 2>"$WORK/err"; then
      cat "$WORK/err" >&2
      echo '[]' > "$WORK/raw.json"
      LOOKUP_FLAG=(--lookup-failed)
    fi
    # --paginate emits one array per page back to back, which is not a JSON
    # document; the existing flatten-pages command concatenates them.
    python -m abicheck.frontends.action.cli flatten-pages "$WORK/raw.json" "$WORK/runs.json"

    JOBS_ARGS=()
    if [[ -n "${INPUT_REQUIRED_JOBS:-}" && ${#LOOKUP_FLAG[@]} -eq 0 ]]; then
      # One jobs document per candidate. A run whose jobs cannot be fetched is
      # left out of the map, which the decision layer reports as
      # jobs-unavailable -- an unchecked requirement is not a satisfied one.
      : > "$WORK/jobs.ndjson"
      for run_id in $(python3 -I -c '
import json, sys
for row in json.load(open(sys.argv[1], encoding="utf-8")):
    if isinstance(row, dict) and row.get("id"):
        print(row["id"])
' "$WORK/runs.json"); do
        if gh api "repos/$REPO/actions/runs/$run_id/jobs" --paginate --jq '.jobs' \
             > "$WORK/jobs-$run_id.json" 2>/dev/null; then
          printf '%s\t%s\n' "$run_id" "$WORK/jobs-$run_id.json" >> "$WORK/jobs.ndjson"
        fi
      done
      python3 -I -c '
import json, sys
out = {}
try:
    lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
except OSError:
    lines = []
decoder = json.JSONDecoder()
for line in lines:
    if not line.strip():
        continue
    run_id, _, path = line.partition("\t")
    text = open(path, encoding="utf-8").read()
    pages, index = [], 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        pages.extend(value if isinstance(value, list) else [value])
    out[run_id] = {"jobs": pages}
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"))
' "$WORK/jobs.ndjson" "$WORK/jobs.json"
      JOBS_ARGS=(--jobs-json "$WORK/jobs.json")
    fi

    ALLOW_FLAG=()
    if [[ "${INPUT_ALLOW_UNRELATED_JOB_FAILURES:-false}" == "true" ]]; then
      [[ -n "${INPUT_REQUIRED_JOBS:-}" ]] || _fail "allow-unrelated-job-failures requires required-jobs -- without it nothing would still be checking that the baseline's own evidence was produced."
      ALLOW_FLAG=(--allow-unrelated-job-failures)
    fi

    rc=0
    python -m abicheck.frontends.action.cli select-producer-run "$WORK/runs.json" \
      --expect-repository "$REPO" \
      --expect-workflow "$WORKFLOW" \
      --expect-event "${INPUT_EXPECT_EVENT:-}" \
      --expect-head-sha "${INPUT_EXPECT_HEAD_SHA:-}" \
      --expect-head-branch "${INPUT_EXPECT_HEAD_BRANCH:-}" \
      --allowed-conclusions "${INPUT_ALLOWED_CONCLUSIONS:-success}" \
      --required-jobs "${INPUT_REQUIRED_JOBS:-}" \
      ${ALLOW_FLAG[@]+"${ALLOW_FLAG[@]}"} \
      ${JOBS_ARGS[@]+"${JOBS_ARGS[@]}"} \
      ${LOOKUP_FLAG[@]+"${LOOKUP_FLAG[@]}"} \
      --github-output "$GITHUB_OUTPUT" || rc=$?
    if [[ "$rc" == "$_REFUSED" ]]; then
      # Not a step failure: "no eligible baseline" and "the lookup failed" are
      # both real states a caller must report as an unavailable baseline
      # rather than crash on. The outcome output says which.
      _emit_ineligible 0
    elif [[ "$rc" != 0 ]]; then
      _fail "selecting a producer run failed with exit $rc."
    fi
    echo "eligible=true" >> "$GITHUB_OUTPUT"
    ;;

  *)
    _fail "mode '$MODE' is not recognized. Use 'tag' or 'producer-run'."
    ;;
esac
