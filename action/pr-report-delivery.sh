#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# The producer half of the turnkey fork-PR reporting path (the
# `pr-comment-via` input; docs/use/fork-pr-reporting.md). Two phases, each
# its own composite step in action.yml:
#
#   resolve  -- before run.sh: decide whether this run's PR comment goes out
#               directly from this job or as an artifact for the trusted
#               report-pr.yml publisher. Exactly one answer per run, which is
#               what keeps a repository wiring both paths from double-posting.
#   stage    -- after run.sh: wrap the report run.sh staged into a one-target
#               aggregate document whose `analysis_context` records the
#               analysed commit, for actions/verify-source-run's
#               `provenance-from`/`report-from` to read back in one pass.
#
# This script never holds or needs a write token: the upload that follows is
# authenticated by the runner's own artifact credentials.
set -euo pipefail

_fail() { echo "::error::abicheck: $*"; exit 1; }
_out() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

# Same sanitization as action/run.sh's _sanitize_pr_comment_key and
# abicheck/pr_comment_render.py's sanitize_comment_key: trim, then every byte
# outside [A-Za-z0-9._-] becomes '-', max 64.
_sanitize_key() {
  local k="$1"
  k="${k#"${k%%[![:space:]]*}"}"
  k="${k%"${k##*[![:space:]]}"}"
  k=$(printf '%s' "$k" | LC_ALL=C tr -c 'A-Za-z0-9._-' '-')
  printf '%s' "${k:0:64}"
}

# The artifact name both sides derive when none is given. report-pr.yml
# carries the identical rule for its own `artifact-name`/`comment-key`.
_default_artifact_name() {
  local key
  key=$(_sanitize_key "${1:-}")
  if [[ -n "$key" ]]; then
    printf 'abicheck-pr-report-%s' "$key"
  else
    printf 'abicheck-pr-report'
  fi
}

# Whether the token this pull_request run received can post. GitHub hands a
# read-only token to a pull_request run from a fork and to a Dependabot PR,
# so `auto` sends exactly those through the artifact path. A missing head
# repository (a deleted fork) is treated as a fork: unknown is not "ours".
_token_is_read_only() {
  [[ "${GITHUB_EVENT_NAME:-}" == "pull_request" ]] || return 1
  [[ "${GITHUB_ACTOR_LOGIN:-}" == "dependabot[bot]" ]] && return 0
  [[ -z "${PR_HEAD_REPOSITORY:-}" ]] && return 0
  [[ "${PR_HEAD_REPOSITORY}" != "${GITHUB_REPOSITORY:-}" ]]
}

# Whether this run would comment at all. Mirrors run.sh's own early returns
# in _maybe_post_pr_comment that do not depend on the analysis result, so an
# `artifact` run that will never comment says `none` instead of promising an
# upload. The result-dependent ones (dry run, ERROR verdict) stay in run.sh,
# which simply stages nothing then.
_would_comment() {
  [[ "${INPUT_PR_COMMENT:-true}" == "true" ]] || return 1
  [[ "${INPUT_MODE:-compare}" == "compare" ]] || return 1
  [[ "${INPUT_PR_COMMENT_ON:-changes}" != "never" ]] || return 1
  case "${GITHUB_EVENT_NAME:-}" in
    pull_request | pull_request_target) return 0 ;;
    *) return 1 ;;
  esac
}

resolve() {
  local via="${INPUT_PR_COMMENT_VIA:-direct}" delivery
  case "$via" in
    direct) delivery="direct" ;;
    artifact) delivery="artifact" ;;
    auto)
      if _token_is_read_only; then delivery="artifact"; else delivery="direct"; fi
      ;;
    *) _fail "pr-comment-via must be 'direct', 'artifact' or 'auto' (got '$via')." ;;
  esac
  # `direct` is left entirely to run.sh, unchanged: it already decides for
  # itself whether this run comments.
  if [[ "$delivery" == "artifact" ]] && ! _would_comment; then
    delivery="none"
  fi

  local name="${INPUT_PR_COMMENT_ARTIFACT_NAME:-}"
  if [[ -z "$name" ]]; then
    name=$(_default_artifact_name "${INPUT_PR_COMMENT_KEY:-}")
  fi
  # upload-artifact refuses these outright, and `#` breaks its API call
  # (actions/upload-artifact#473); fail here, before the analysis runs.
  case "$name" in
    *[\"\:\<\>\|\*\?\\/\#]* | *$'\n'* | *$'\r'*)
      _fail "pr-comment-artifact-name '$name' contains a character artifact names cannot hold." ;;
  esac

  echo "abicheck: PR comment delivery: $delivery (pr-comment-via: $via)."
  _out "delivery" "$delivery"
  _out "artifact-name" "$name"
}

stage() {
  local dir="${PR_REPORT_DIR:?PR_REPORT_DIR is required}"
  [[ -d "$dir" ]] || _fail "staged PR report directory $dir does not exist."
  local context="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/abicheck-pr-analysis-context.json"
  # Outside $dir on purpose: `abicheck aggregate` reads every *.json in it
  # as a target (actions/aggregate/run.sh's record-context phase has the
  # full story).
  local rc=0
  python -m abicheck.frontends.action.cli record-analysis-context "$context" || rc=$?
  [[ "$rc" == 0 ]] || _fail "the analysis context could not be recorded (exit $rc); no PR report artifact will be uploaded."

  # One-target aggregate: the document actions/verify-source-run reads the
  # analysed commit from and actions/report renders. --discovered-only is
  # the honest declaration here -- this job states no expected inventory.
  # Its exit code is a compatibility decision (0/1/2/4), never this step's.
  rc=0
  (cd "$dir" && abicheck aggregate . --discovered-only --analysis-context "$context" \
      -o json=aggregate.json >/dev/null) || rc=$?
  case "$rc" in
    0 | 1 | 2 | 4) ;;
    *) _fail "abicheck aggregate exited $rc while wrapping the PR report; no PR report artifact will be uploaded." ;;
  esac
  [[ -s "$dir/aggregate.json" ]] || _fail "abicheck aggregate wrote no aggregate.json."
  rm -f "$context"
  _out "dir" "$dir"
}

case "${1:-}" in
  resolve) resolve ;;
  stage) stage ;;
  *) _fail "usage: pr-report-delivery.sh resolve|stage" ;;
esac
