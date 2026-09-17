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
# actions/verify-source-run -- run selection for a trusted publisher
# (ADR-073).
#
# The division of labour is deliberate and is what makes the checks
# testable: this script performs the GitHub API calls and writes each
# response to a file; `abicheck.frontends.action.cli verify-run` decides
# whether those responses describe a run this publisher may report on, and
# `... extract-artifact` unpacks the archive under hard limits. Nothing from
# the artifact is executed, imported, or used to choose a path or endpoint.
set -euo pipefail

_fail() {
  echo "::error::abicheck verify-source-run: $1" >&2
  _out "verified" "false"
  exit 1
}

_out() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"
  fi
}

RUN_ID="${INPUT_SOURCE_RUN_ID:-}"
[[ "$RUN_ID" =~ ^[0-9]+$ ]] || _fail "'source-run-id' must be a positive integer (got '$RUN_ID')."

REPOSITORY="${INPUT_EXPECT_REPOSITORY:-${GITHUB_REPOSITORY:-}}"
[[ "$REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] \
  || _fail "'expect-repository' must look like owner/repo (got '$REPOSITORY')."

ARTIFACT_NAME="${INPUT_ARTIFACT_NAME:-}"
[[ -n "$ARTIFACT_NAME" ]] || _fail "the 'artifact-name' input is required."

TESTED_SHA="${INPUT_TESTED_SHA:-}"
# Full-length only. Verification compares this against the pull request's
# head and the analysed commit's parents, both of which the API states in
# full, by exact equality -- an abbreviated SHA matches neither and was
# refused as `unassociated-tested-sha` every time, which reads as "this
# commit is not yours" rather than "say it in full". Refusing it here says
# the true thing, and prefix-matching instead would be a weakening: a short
# prefix is ambiguous by construction, and this value decides which commit
# a trusted comment claims was analysed.
if [[ -n "$TESTED_SHA" && ! "$TESTED_SHA" =~ ^([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$ ]]; then
  _fail "'tested-sha' must be a full 40- or 64-character commit SHA (got '$TESTED_SHA'); an abbreviated SHA cannot be verified against the pull request."
fi

# A relative member of the extracted artifact, never a path this step
# joins itself: the whole point is that the provenance comes out of the
# artifact the run already produced. Rejected here rather than in Python so
# a typo reads as this Action's input error.
PROVENANCE_FROM="${INPUT_PROVENANCE_FROM:-}"
REPORT_FROM="${INPUT_REPORT_FROM:-}"

# Both name a member INSIDE the extracted artifact, and both are then
# concatenated onto $DESTINATION. A `..` component, a leading `/`, or a
# backslash would name something outside the extraction the bounded
# extractor just staged -- the one place a member name may not be allowed
# to reach. Refused rather than normalized: a workflow author who wrote one
# meant something, and quietly resolving it elsewhere is how the caller
# ends up reading a file nobody checked.
_require_member_name() {
  local label="$1" value="$2"
  [[ -n "$value" ]] || return 0
  case "$value" in
    /*|*'\'*) _fail "'$label' must be a path inside the artifact, not '$value'." ;;
  esac
  local component
  while IFS= read -r component; do
    # An `if`, not a trailing `&&`: a `[[ ]] && _fail` as the loop's last
    # statement makes the LOOP's status that of the final (false) test, so
    # the function returns non-zero for every *valid* name -- refusing the
    # ordinary case while still accepting nothing extra. Caught by the
    # accept-half of this guard's own tests, which is why they exist.
    if [[ "$component" == ".." || "$component" == "." ]]; then
      _fail "'$label' must not contain a '$component' component (got '$value')."
    fi
  done < <(printf '%s\n' "${value//\//$'\n'}")
  return 0
}
_require_member_name "provenance-from" "$PROVENANCE_FROM"
_require_member_name "report-from" "$REPORT_FROM"

# Exactly `true` or `false`. Treating every other spelling as `false` means
# a typo (`ture`, `True`, an unset-but-intended expression expanding empty)
# silently DISABLES the requirement -- the one direction a misreading must
# never take, since the whole point of the flag is to refuse a run whose
# provenance is missing.
REQUIRE_PROVENANCE="${INPUT_REQUIRE_PROVENANCE:-true}"
case "$REQUIRE_PROVENANCE" in
  true|false) ;;
  *) _fail "'require-provenance' must be exactly 'true' or 'false' (got '$REQUIRE_PROVENANCE')." ;;
esac
for _relative in "$PROVENANCE_FROM" "$REPORT_FROM"; do
  case "$_relative" in
    /*|*..*|*$'\n'*) _fail "'provenance-from'/'report-from' must be a relative path inside the artifact with no '..' segment (got '$_relative')." ;;
  esac
done

CLAIMED_PR="${INPUT_CLAIMED_PR_NUMBER:-}"
if [[ -n "$CLAIMED_PR" && ! "$CLAIMED_PR" =~ ^[0-9]+$ ]]; then
  _fail "'claimed-pr-number' must be a positive integer (got '$CLAIMED_PR')."
fi

# Checked here rather than left to Click, so a typo reports as this
# Action's own input error instead of surfacing as an opaque "refused to
# publish" after several API calls have already been made.
if [[ -n "${INPUT_EXPECT_RUN_ATTEMPT:-}" && ! "$INPUT_EXPECT_RUN_ATTEMPT" =~ ^[0-9]+$ ]]; then
  _fail "'expect-run-attempt' must be a positive integer (got '$INPUT_EXPECT_RUN_ATTEMPT')."
fi

command -v gh >/dev/null 2>&1 || _fail "the GitHub CLI (gh) is required."

WORK="$(mktemp -d "${RUNNER_TEMP:-/tmp}/abicheck-verify-run.XXXXXX")"
RUN_JSON="$WORK/run.json"
PULLS_JSON="$WORK/pulls.json"
COMMIT_JSON="$WORK/commit.json"
ARTIFACTS_JSON="$WORK/artifacts.json"
RESULT_JSON="$WORK/result.json"
ARCHIVE="$WORK/artifact.zip"

gh api "repos/$REPOSITORY/actions/runs/$RUN_ID" > "$RUN_JSON" 2>"$WORK/run.err" \
  || _fail "could not read run $RUN_ID in $REPOSITORY (need 'actions: read'): $(tr '\n' ' ' < "$WORK/run.err")"

# The run's own head SHA, read with a JSON parser rather than a grep. Every
# later request is built from this value, so it is also shape-checked: an
# API response that somehow carried a non-SHA here must not reach a URL.
RUN_HEAD_SHA="$(python -m abicheck.frontends.action.cli emit-fields "$RUN_JSON" head_sha)"
[[ "$RUN_HEAD_SHA" =~ ^[0-9a-fA-F]{7,64}$ ]] \
  || _fail "run $RUN_ID reports no usable head SHA."

# The pull-request association comes from the API, never from the artifact.
# `/commits/{sha}/pulls` is used rather than the run document's own
# `pull_requests` array, which is empty for a fork's pull request -- the
# exact case this whole design exists for.
gh api --paginate "repos/$REPOSITORY/commits/$RUN_HEAD_SHA/pulls" \
  --jq '.' > "$WORK/pulls.raw" 2>"$WORK/pulls.err" \
  || _fail "could not resolve the pull request for $RUN_HEAD_SHA: $(tr '\n' ' ' < "$WORK/pulls.err")"
# `--paginate` concatenates one array per page; flatten them into one.
python -m abicheck.frontends.action.cli flatten-pages "$WORK/pulls.raw" "$PULLS_JSON"

VERIFY_ARGS=(
  verify-run
  --run-json "$RUN_JSON"
  --associated-pulls-json "$PULLS_JSON"
  --expect-repository "$REPOSITORY"
  --expect-run-id "$RUN_ID"
  --artifact-name "$ARTIFACT_NAME"
  --out "$RESULT_JSON"
)
if [[ -n "${INPUT_EXPECT_WORKFLOW:-}" ]]; then
  VERIFY_ARGS+=(--expect-workflow "$INPUT_EXPECT_WORKFLOW")
fi
if [[ -n "${INPUT_EXPECT_EVENT:-}" ]]; then
  VERIFY_ARGS+=(--expect-event "$INPUT_EXPECT_EVENT")
fi
if [[ -n "${INPUT_EXPECT_RUN_ATTEMPT:-}" ]]; then
  VERIFY_ARGS+=(--expect-run-attempt "$INPUT_EXPECT_RUN_ATTEMPT")
fi
if [[ -n "$CLAIMED_PR" ]]; then
  VERIFY_ARGS+=(--claimed-pr-number "$CLAIMED_PR")
fi
if [[ -n "$TESTED_SHA" ]]; then
  VERIFY_ARGS+=(--tested-sha "$TESTED_SHA")
  if [[ "$TESTED_SHA" != "$RUN_HEAD_SHA" ]]; then
    # A merge commit was analysed. Its parents are what establish that it
    # really is a merge of this pull request's head, so fetch it.
    gh api "repos/$REPOSITORY/commits/$TESTED_SHA" > "$COMMIT_JSON" 2>"$WORK/commit.err" \
      || _fail "could not read the analysed commit $TESTED_SHA: $(tr '\n' ' ' < "$WORK/commit.err")"
    VERIFY_ARGS+=(--tested-commit-json "$COMMIT_JSON")
  fi
fi

# `--allow-conclusion ''` means "any conclusion"; the CLI drops empty values.
# `${VAR-default}` (no colon), NOT `${VAR:-default}`: the input is documented
# as "empty allows any", and `:-` substitutes on empty as well as unset, so
# an explicitly empty value silently became `success` and the any-conclusion
# branch below was unreachable -- a publisher could never report a producer
# run that failed, which is one of this input's two stated uses.
if [[ -z "${INPUT_ALLOWED_CONCLUSIONS+set}" ]]; then
  INPUT_ALLOWED_CONCLUSIONS=success
fi
IFS=',' read -r -a _CONCLUSIONS <<< "$INPUT_ALLOWED_CONCLUSIONS"
if [[ ${#_CONCLUSIONS[@]} -eq 0 || -z "${_CONCLUSIONS[0]}" ]]; then
  VERIFY_ARGS+=(--allow-conclusion "")
else
  for _conclusion in "${_CONCLUSIONS[@]}"; do
    VERIFY_ARGS+=(--allow-conclusion "$_conclusion")
  done
fi

# `--paginate`, like the pull-request listing above: this endpoint pages at
# 30, so a run publishing more artifacts than that refused the named one as
# `artifact-not-found` purely for being on page two.
gh api --paginate "repos/$REPOSITORY/actions/runs/$RUN_ID/artifacts" --jq '.artifacts' > "$WORK/artifacts.raw" \
  2>"$WORK/artifacts.err" \
  || _fail "could not list artifacts for run $RUN_ID: $(tr '\n' ' ' < "$WORK/artifacts.err")"
# `--jq '.artifacts'` emits one array per page, concatenated -- the same
# shape the pull-request listing produces, so it goes through the same
# flattener rather than a second opinion about how pages join.
python -m abicheck.frontends.action.cli flatten-pages "$WORK/artifacts.raw" "$ARTIFACTS_JSON"
VERIFY_ARGS+=(--artifacts-json "$ARTIFACTS_JSON")

if ! python -m abicheck.frontends.action.cli "${VERIFY_ARGS[@]}"; then
  # `|| true`: this runs on the refusal path, where `_fail`'s own message is
  # the real signal. A result document we cannot read here must not preempt
  # it under `set -e` and leave the step failing with nothing said.
  CODE="$(python -m abicheck.frontends.action.cli emit-fields --tolerant "$RESULT_JSON" code || true)"
  _out "refusal-code" "$CODE"
  _fail "refused to publish from run $RUN_ID (${CODE:-unknown})."
fi

{
  read -r PR_NUMBER
  read -r PR_HEAD_SHA
  read -r VERIFIED_TESTED_SHA
  read -r FROM_FORK
  read -r ARTIFACT_ID
} < <(python -m abicheck.frontends.action.cli emit-fields "$RESULT_JSON" pr_number pr_head_sha tested_sha from_fork artifact_id)

[[ "$ARTIFACT_ID" =~ ^[0-9]+$ ]] \
  || _fail "the verified result names no downloadable artifact id."

# Downloaded from the artifact id belonging to *this* run, which
# `select_artifact` re-established against each entry's own
# `workflow_run.id` rather than trusting the URL the listing came from.
gh api "repos/$REPOSITORY/actions/artifacts/$ARTIFACT_ID/zip" > "$ARCHIVE" \
  2>"$WORK/download.err" \
  || _fail "could not download artifact $ARTIFACT_ID: $(tr '\n' ' ' < "$WORK/download.err")"

DESTINATION="${INPUT_DESTINATION:-abicheck-source-artifact}"
if ! python -m abicheck.frontends.action.cli extract-artifact \
    "$ARCHIVE" "$DESTINATION" \
    --max-total-bytes "${INPUT_MAX_TOTAL_BYTES:-67108864}" \
    --max-entry-bytes "${INPUT_MAX_ENTRY_BYTES:-33554432}" \
    --max-entries "${INPUT_MAX_ENTRIES:-2000}" \
    --max-ratio "${INPUT_MAX_RATIO:-200}"; then
  _out "refusal-code" "artifact-refused"
  _fail "refused the artifact from run $RUN_ID."
fi

# ── the analysed commit, read out of the artifact just extracted ─────────
#
# This is the single-pass half of ADR-073's provenance flow. The commit a
# `pull_request` producer actually built is an ephemeral merge commit that
# no API endpoint names, so the producer records it in the aggregate
# document's `analysis_context` block; it only becomes readable *after* the
# artifact is extracted, which is why it is read here rather than passed in
# as `tested-sha`.
#
# The previous shape of this -- a caller running the whole Action once to
# get the artifact, parsing a sidecar file in its own privileged shell, then
# running the Action a second time with `tested-sha` set -- downloaded the
# same bytes twice with no guarantee the second copy was the first, and put
# artifact parsing in the trusted job. Both are closed by doing it here: one
# download, and every field shape-checked by an importable owner before it
# can reach a step output.
# Three distinguishable states, not two. An explicit `tested-sha` input was
# verified above against the very same rules, but reporting it as `run-head`
# tells a caller the Action fell back to the run's own head commit -- which
# is exactly the claim that must stay separable from "a caller stated this".
if [[ -n "$TESTED_SHA" ]]; then
  TESTED_SHA_SOURCE="input"
else
  TESTED_SHA_SOURCE="run-head"
fi
PROVENANCE_STATE="not-requested"
if [[ -n "$PROVENANCE_FROM" ]]; then
  PROVENANCE_STATE="absent"
  REQUIRE_FLAG="--require"
  if [[ "$REQUIRE_PROVENANCE" == "false" ]]; then
    REQUIRE_FLAG="--no-require"
  fi
  if ! python -m abicheck.frontends.action.cli read-analysis-context \
      "$DESTINATION/$PROVENANCE_FROM" --out "$WORK/context.json" \
      --max-bytes "${INPUT_MAX_ENTRY_BYTES:-33554432}" "$REQUIRE_FLAG"; then
    CODE="$(python -m abicheck.frontends.action.cli emit-fields --tolerant "$WORK/context.json" code || true)"
    _out "refusal-code" "${CODE:-analysis-context-unreadable}"
    _fail "the producer's analysis context could not be read (${CODE:-unknown})."
  fi
  {
    read -r CTX_PRESENT
    read -r CTX_RECORDS
    read -r CTX_TESTED_SHA
  } < <(python -m abicheck.frontends.action.cli emit-fields --tolerant \
          "$WORK/context.json" present records_tested_sha tested_sha)

  if [[ "$CTX_PRESENT" == "true" && "$CTX_RECORDS" == "true" ]]; then
    PROVENANCE_STATE="recorded"
    # Already shape-validated by the owner above; re-asserted here because
    # this value is about to be interpolated into a URL.
    [[ "$CTX_TESTED_SHA" =~ ^([0-9a-fA-F]{40}|[0-9a-fA-F]{64})$ ]] \
      || _fail "the recorded analysed commit is not a full SHA."
    COMMIT_ARGS=()
    if [[ "$CTX_TESTED_SHA" != "$RUN_HEAD_SHA" ]]; then
      gh api "repos/$REPOSITORY/commits/$CTX_TESTED_SHA" > "$COMMIT_JSON" 2>"$WORK/commit.err" \
        || _fail "could not read the analysed commit $CTX_TESTED_SHA: $(tr '\n' ' ' < "$WORK/commit.err")"
      COMMIT_ARGS+=(--tested-commit-json "$COMMIT_JSON")
    fi
    # The run and the pull request come from the FIRST pass's own result, so
    # the identity checked here is the identity verified there.
    if ! python -m abicheck.frontends.action.cli verify-tested-sha \
        --run-json "$RUN_JSON" --result-json "$RESULT_JSON" \
        --tested-sha "$CTX_TESTED_SHA" \
        ${COMMIT_ARGS[@]+"${COMMIT_ARGS[@]}"} \
        --out "$WORK/tested.json"; then
      CODE="$(python -m abicheck.frontends.action.cli emit-fields --tolerant "$WORK/tested.json" code || true)"
      _out "refusal-code" "$CODE"
      _fail "the producer's analysed commit does not belong to this pull request (${CODE:-unknown})."
    fi
    VERIFIED_TESTED_SHA="$(python -m abicheck.frontends.action.cli emit-fields "$WORK/tested.json" tested_sha)"
    TESTED_SHA_SOURCE="analysis-context"
  fi
fi

# ── the report location, returned WITH the identity it was checked under ──
#
# Returned together, so the document a caller renders is the one whose
# context was verified -- not a path the caller reassembled from
# `artifact-path` and a filename, which can name a member that was never
# checked. A verified run whose report is missing gets an explicit
# unavailable-analysis answer rather than a path that does not resolve.
REPORT_PATH=""
REPORT_AVAILABLE="false"
if [[ -n "$REPORT_FROM" ]]; then
  if [[ -s "$DESTINATION/$REPORT_FROM" ]]; then
    REPORT_PATH="$DESTINATION/$REPORT_FROM"
    REPORT_AVAILABLE="true"
  else
    echo "::notice::the verified run produced no $REPORT_FROM; the analysis is unavailable, which is not a clean compatibility result."
  fi
fi

_out "verified" "true"
_out "pr-number" "$PR_NUMBER"
_out "pr-head-sha" "$PR_HEAD_SHA"
_out "tested-sha" "$VERIFIED_TESTED_SHA"
_out "tested-sha-source" "$TESTED_SHA_SOURCE"
_out "provenance" "$PROVENANCE_STATE"
_out "report-path" "$REPORT_PATH"
_out "report-available" "$REPORT_AVAILABLE"
# `${:-false}` so a result document that resolved no pull request still
# publishes a boolean here, which is what this output promised before the
# field emitter started answering "no value" for an absent key.
_out "from-fork" "${FROM_FORK:-false}"
_out "artifact-path" "$DESTINATION"
_out "refusal-code" ""
echo "abicheck verify-source-run: run $RUN_ID verified for pull request #$PR_NUMBER."
