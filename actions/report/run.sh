#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# actions/report -- publish an already-produced abicheck report (ADR-073).
#
# This script marshals inputs and makes the GitHub API calls. Every decision
# -- what to render, how to bound it, whether to create/update/clear/skip --
# is made by `abicheck.frontends.action.cli`, which is importable, unit
# tested, and needs no credentials. Two properties this file must keep:
#
#   * It runs no analysis. No compare, no dump, no compiler, no build query.
#     `tests/test_action_report_contract.py` fails if that changes.
#   * Report text never becomes a shell word. The body is written to a file
#     by Python, and the API request document is JSON that Python produced;
#     this script passes file paths, never content.
set -euo pipefail

_fail() {
  echo "::error::abicheck report: $1" >&2
  exit 1
}

_out() {
  # `GITHUB_OUTPUT` is absent when this script is exercised outside a runner.
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

REPORT="${INPUT_REPORT:-}"
[[ -n "$REPORT" ]] || _fail "the 'report' input is required."
[[ -f "$REPORT" ]] || _fail "report '$REPORT' does not exist or is not a file."

# `post-on` wins when given: see the input's own description for why both
# spellings exist (an unquoted `on:` key is a YAML 1.1 boolean).
ON="${INPUT_POST_ON:-}"
if [[ -z "$ON" ]]; then
  ON="${INPUT_ON:-changes}"
fi
case "$ON" in
  always | changes | never) ;;
  *) _fail "'on' must be always, changes or never (got '$ON')." ;;
esac

DETAIL="${INPUT_DETAIL:-standard}"
case "$DETAIL" in
  summary | standard | full) ;;
  *) _fail "'detail' must be summary, standard or full (got '$DETAIL')." ;;
esac

DRY_RUN="${INPUT_DRY_RUN:-false}"
REPOSITORY="${INPUT_REPOSITORY:-${GITHUB_REPOSITORY:-}}"
PR_NUMBER="${INPUT_PR_NUMBER:-}"

# The identity is our own sticky key, never a path or an endpoint, so its
# only requirement is that it be non-empty and stable.
IDENTITY="${INPUT_COMMENT_IDENTITY:-}"
if [[ -z "$IDENTITY" ]]; then
  IDENTITY="abicheck:${INPUT_PROFILE:-default}"
fi

WORK="$(mktemp -d "${RUNNER_TEMP:-/tmp}/abicheck-report.XXXXXX")"
BODY="$WORK/body.md"
REQUEST="$WORK/request.json"
SUMMARY="$WORK/summary.md"
PLAN="$WORK/plan.json"
EXISTING="$WORK/existing.ndjson"

if [[ "$DRY_RUN" != "true" ]]; then
  [[ -n "${GH_TOKEN:-}" ]] || _fail "the 'github-token' input is required unless dry-run is true."
  [[ -n "$REPOSITORY" ]] || _fail "could not determine the repository to publish into."
  # Validated before it is ever interpolated into an API path. Neither value
  # is attacker-controlled in a correct setup, but a publisher's whole job is
  # to be correct when its inputs are not.
  [[ "$REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] \
    || _fail "'repository' must look like owner/repo (got '$REPOSITORY')."
  [[ "$PR_NUMBER" =~ ^[0-9]+$ ]] \
    || _fail "'pr-number' must be a positive integer (got '$PR_NUMBER'); resolve it through the API, not from an artifact."
  command -v gh >/dev/null 2>&1 || _fail "the GitHub CLI (gh) is required to publish."

  # One JSON object per line. `--jq` emits compact JSON, so a body's own
  # newlines are escaped inside the string and cannot break the framing.
  if ! gh api --paginate "repos/$REPOSITORY/issues/$PR_NUMBER/comments" \
      --jq '.[] | {id: .id, body: .body}' > "$EXISTING" 2>"$WORK/list.err"; then
    _fail "could not list existing comments on $REPOSITORY#$PR_NUMBER (need 'pull-requests: write'): $(tr '\n' ' ' < "$WORK/list.err")"
  fi
else
  : > "$EXISTING"
fi

RENDER_ARGS=(
  comment "$REPORT"
  --identity "$IDENTITY"
  --run-id "${GITHUB_RUN_ID:-}"
  --run-attempt "${GITHUB_RUN_ATTEMPT:-1}"
  --sha "${INPUT_SHA:-}"
  --detail "$DETAIL"
  --on "$ON"
  --max-comment-bytes "${INPUT_MAX_COMMENT_BYTES:-60000}"
  --max-summary-bytes "${INPUT_MAX_SUMMARY_BYTES:-900000}"
  --existing-comments "$EXISTING"
  --body-out "$BODY"
  --request-out "$REQUEST"
  --summary-out "$SUMMARY"
  --plan-out "$PLAN"
)
# `if` blocks rather than `[[ ... ]] && ...`: under `set -e` a one-line
# guard whose test is false is itself a failing statement, so the whole
# script would exit as soon as any optional input was left empty.
if [[ -n "${INPUT_RUN_LABEL:-}" ]]; then
  RENDER_ARGS+=(--run-label "$INPUT_RUN_LABEL")
fi
if [[ -n "${INPUT_REPORT_URL:-}" ]]; then
  RENDER_ARGS+=(--report-url "$INPUT_REPORT_URL")
fi
if [[ -n "${INPUT_REPORT_ARTIFACT_URL:-}" ]]; then
  RENDER_ARGS+=(--report-artifact-url "$INPUT_REPORT_ARTIFACT_URL")
fi
if [[ -n "${INPUT_PATH_PREFIX:-}" ]]; then
  RENDER_ARGS+=(--path-prefix "$INPUT_PATH_PREFIX")
fi
if [[ "${INPUT_GATE_API_BREAK:-false}" == "true" ]]; then
  RENDER_ARGS+=(--gate-api-break)
fi
if [[ "${INPUT_GATE_BREAKING:-true}" == "false" ]]; then
  RENDER_ARGS+=(--no-gate-breaking)
fi

# A rendering failure is a publication failure, never a compatibility
# result: it fails the step loudly rather than degrading to "nothing to say".
if ! python -m abicheck.frontends.action.cli "${RENDER_ARGS[@]}"; then
  _out "posted" "false"
  _out "skipped-reason" "render-failed"
  _fail "could not render the report at '$REPORT'."
fi

# One read, four values. The plan document is written by this repository's
# own code, but it is still parsed with a real JSON reader rather than a
# grep: a field re-derived by pattern matching is a second opinion about
# what the planner decided.
{
  read -r PLAN_ACTION
  read -r PLAN_COMMENT_ID
  read -r PLAN_REASON
  read -r BODY_BYTES
} < <(python -m abicheck.frontends.action.cli emit-fields "$PLAN" action comment_id skipped_reason body_bytes)

_out "body-path" "$BODY"
_out "body-bytes" "$BODY_BYTES"

if [[ "${INPUT_JOB_SUMMARY:-true}" == "true" && -s "$SUMMARY" && -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$SUMMARY" >> "$GITHUB_STEP_SUMMARY"
fi

if [[ "$PLAN_ACTION" == "skip" ]]; then
  echo "abicheck report: nothing published (${PLAN_REASON:-no-changes})."
  _out "posted" "false"
  _out "skipped-reason" "$PLAN_REASON"
  _out "comment-url" ""
  exit 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "abicheck report: dry run — would $PLAN_ACTION; body written to $BODY."
  _out "posted" "false"
  _out "skipped-reason" "dry-run"
  _out "comment-url" ""
  exit 0
fi

# --- publish ---------------------------------------------------------------
# A failure here fails the step with its own message and posted=false. It is
# never downgraded to a warning and never reported as a clean compatibility
# result: the two live on different channels on purpose.
RESPONSE="$WORK/response.json"
if [[ "$PLAN_ACTION" == "create" ]]; then
  if ! gh api -X POST "repos/$REPOSITORY/issues/$PR_NUMBER/comments" \
      --input "$REQUEST" > "$RESPONSE" 2>"$WORK/post.err"; then
    _out "posted" "false"
    _fail "failed to post the comment to $REPOSITORY#$PR_NUMBER (need 'pull-requests: write'): $(tr '\n' ' ' < "$WORK/post.err")"
  fi
else
  [[ -n "$PLAN_COMMENT_ID" ]] || _fail "internal: plan '$PLAN_ACTION' names no comment to update."
  if ! gh api -X PATCH "repos/$REPOSITORY/issues/comments/$PLAN_COMMENT_ID" \
      --input "$REQUEST" > "$RESPONSE" 2>"$WORK/post.err"; then
    _out "posted" "false"
    _fail "failed to update comment $PLAN_COMMENT_ID on $REPOSITORY#$PR_NUMBER (need 'pull-requests: write'): $(tr '\n' ' ' < "$WORK/post.err")"
  fi
fi

COMMENT_URL="$(python -m abicheck.frontends.action.cli emit-fields --tolerant "$RESPONSE" html_url 2>/dev/null || true)"
echo "abicheck report: ${PLAN_ACTION}d comment on $REPOSITORY#$PR_NUMBER."
_out "posted" "true"
_out "comment-url" "$COMMENT_URL"
_out "skipped-reason" ""
