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
if [[ -n "$TESTED_SHA" && ! "$TESTED_SHA" =~ ^[0-9a-fA-F]{7,64}$ ]]; then
  _fail "'tested-sha' must be a hexadecimal commit SHA (got '$TESTED_SHA')."
fi

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
RUN_HEAD_SHA="$(python - "$RUN_JSON" <<'PYEOF'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
print(str(data.get("head_sha") or ""))
PYEOF
)"
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
python - "$WORK/pulls.raw" "$PULLS_JSON" <<'PYEOF'
import json
import sys

raw = open(sys.argv[1], encoding="utf-8").read()
decoder = json.JSONDecoder()
out = []
index = 0
while index < len(raw):
    while index < len(raw) and raw[index].isspace():
        index += 1
    if index >= len(raw):
        break
    value, index = decoder.raw_decode(raw, index)
    if isinstance(value, list):
        out.extend(value)
    else:
        out.append(value)
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"))
PYEOF

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
IFS=',' read -r -a _CONCLUSIONS <<< "${INPUT_ALLOWED_CONCLUSIONS:-success}"
if [[ ${#_CONCLUSIONS[@]} -eq 0 ]]; then
  VERIFY_ARGS+=(--allow-conclusion "")
else
  for _conclusion in "${_CONCLUSIONS[@]}"; do
    VERIFY_ARGS+=(--allow-conclusion "$_conclusion")
  done
fi

gh api "repos/$REPOSITORY/actions/runs/$RUN_ID/artifacts" > "$ARTIFACTS_JSON" \
  2>"$WORK/artifacts.err" \
  || _fail "could not list artifacts for run $RUN_ID: $(tr '\n' ' ' < "$WORK/artifacts.err")"
VERIFY_ARGS+=(--artifacts-json "$ARTIFACTS_JSON")

if ! python -m abicheck.frontends.action.cli "${VERIFY_ARGS[@]}"; then
  CODE="$(python - "$RESULT_JSON" <<'PYEOF'
import json
import sys

try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("code", ""))
except Exception:  # noqa: BLE001 - the refusal message is the real signal
    print("")
PYEOF
)"
  _out "refusal-code" "$CODE"
  _fail "refused to publish from run $RUN_ID (${CODE:-unknown})."
fi

{
  read -r PR_NUMBER
  read -r PR_HEAD_SHA
  read -r VERIFIED_TESTED_SHA
  read -r FROM_FORK
  read -r ARTIFACT_ID
} < <(python - "$RESULT_JSON" <<'PYEOF'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
print(result.get("pr_number", ""))
print(result.get("pr_head_sha", ""))
print(result.get("tested_sha", ""))
print("true" if result.get("from_fork") else "false")
print(result.get("artifact_id", ""))
PYEOF
)

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

_out "verified" "true"
_out "pr-number" "$PR_NUMBER"
_out "pr-head-sha" "$PR_HEAD_SHA"
_out "tested-sha" "$VERIFIED_TESTED_SHA"
_out "from-fork" "$FROM_FORK"
_out "artifact-path" "$DESTINATION"
_out "refusal-code" ""
echo "abicheck verify-source-run: run $RUN_ID verified for pull request #$PR_NUMBER."
