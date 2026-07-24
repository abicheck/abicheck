#!/usr/bin/env bash
# Writes the ADR-047 §7 report envelope and owns check-target's own
# composite exit code -- this step always runs (never continue-on-error),
# regardless of whether the earlier resolve/collect-facts/analysis steps
# succeeded, failed, or were skipped. See action.yml for the step
# orchestration and report_envelope.py for the pure-logic-backed CLI this
# script drives.
set -euo pipefail

_fail() {
  echo "::error::$1"
  exit 1
}

NAME="${INPUT_NAME:?}"
PROFILE="${INPUT_PROFILE:?}"
BASELINE_CHANNEL="${INPUT_BASELINE_CHANNEL:?}"
REQUESTED_DEPTH="${INPUT_REQUESTED_DEPTH:?}"
GATE_MODE="${INPUT_GATE_MODE:-local}"
PROJECT="${INPUT_PROJECT:-}"
HEAD_SHA="${INPUT_HEAD_SHA:-}"
BASE_REF="${INPUT_BASE_REF:-}"
ACTION_VERSION="${INPUT_ACTION_VERSION:-}"

RESOLVE_RAN="${RESOLVE_RAN:-false}"
RESOLVE_OUTCOME="${RESOLVE_OUTCOME:-}"
RESOLVE_BOOTSTRAP="${RESOLVE_BOOTSTRAP:-false}"
RESOLVE_MESSAGE="${RESOLVE_MESSAGE:-}"

ANALYSIS_RAN="${ANALYSIS_RAN:-false}"
ANALYSIS_REPORT_PATH="${ANALYSIS_REPORT_PATH:-}"
# The nested root Action's own real process exit code (its `exit-code`
# output) -- some of its gates (e.g. --fail-on-removed-library on a
# directory/package compare) take effect as a dedicated exit code that
# overrides the persisted severity scheme rather than feeding into it, so
# the report body alone can under-report the real outcome (Codex review).
# Defensively defaulted to 0 for anything not a clean non-negative integer
# (an empty output when the nested step never reached its own exit-code
# step, or any unexpected content) rather than letting a malformed value
# reach report_envelope.py's --analysis-exit-code (an argparse int).
ANALYSIS_EXIT_CODE_RAW="${ANALYSIS_EXIT_CODE:-0}"
if [[ "$ANALYSIS_EXIT_CODE_RAW" =~ ^[0-9]+$ ]]; then
  ANALYSIS_EXIT_CODE="$ANALYSIS_EXIT_CODE_RAW"
else
  ANALYSIS_EXIT_CODE=0
fi

COLLECT_VERIFY_OUTCOME="${COLLECT_VERIFY_OUTCOME:-}"
COLLECT_REPLAY_OUTCOME="${COLLECT_REPLAY_OUTCOME:-}"

ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# A fixed "check-target-report.json" collides across multiple check-target
# invocations in the same job (e.g. the same target checked against two
# baseline channels, or several targets in one job without per-step output
# dirs) -- each call would overwrite the previous one's report file, so an
# earlier step's own `report-path` output would end up pointing at a LATER
# check's envelope by the time anything reads it (Codex review). Scope the
# filename to this check's own identity components instead; `tr -c` maps
# any character outside the safe identifier charset to `_` so an
# unsanitized NAME/PROFILE/BASELINE_CHANNEL can never escape the workspace
# directory or collide with an unrelated file.
_slug() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}
# `tr -c` is lossy: name "a"/profile "b-c" and name "a-b"/profile "c" (same
# channel/depth) both slug to "a-b-c" -- harmless for a single check-target
# invocation writing its own report file, but check-project.yml downloads
# every matrix cell's report into ONE shared flat directory with
# merge-multiple: true (abicheck's own `collect_reports` globs `*.json`
# non-recursively, so per-cell subdirectories aren't an option there), and
# actions/download-artifact resolves a same-named file across separate
# artifacts last-writer-wins -- two colliding identities would silently
# overwrite one another's report before `aggregate` ever reads it (Codex
# review). Append a short content hash of the *original*, unsanitized
# identity tuple -- mirroring check-project.yml's own injective
# artifact-name sanitizer -- so slugs that collapse under `tr` still produce
# distinct filenames.
_IDENTITY_DIGEST="$(
  printf '%s\x1f%s\x1f%s\x1f%s' "$NAME" "$PROFILE" "$BASELINE_CHANNEL" "$REQUESTED_DEPTH" \
    | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:12])'
)"
REPORT_OUT="check-target-report-$(_slug "$NAME")-$(_slug "$PROFILE")-$(_slug "$BASELINE_CHANNEL")-$(_slug "$REQUESTED_DEPTH")-${_IDENTITY_DIGEST}.json"

# ── Decide which report_envelope.py mode this check needs ──────────────────
MODE=""
RESOLVE_ARGS=()
if [[ "$RESOLVE_RAN" == "true" && "$RESOLVE_OUTCOME" == "not_found" && "$RESOLVE_BOOTSTRAP" == "true" ]]; then
  MODE="bootstrap"
elif [[ "$RESOLVE_RAN" == "true" && "$RESOLVE_OUTCOME" != "resolved" ]]; then
  MODE="operational-error"
  OUTCOME="${RESOLVE_OUTCOME:-ambiguous}"
  MESSAGE="${RESOLVE_MESSAGE:-resolve-baseline did not produce an outcome.}"
  RESOLVE_ARGS+=(--resolve-outcome "$OUTCOME" --resolve-message "$MESSAGE")
elif [[ "$COLLECT_VERIFY_OUTCOME" == "failure" ]]; then
  # A wrapper/clang-plugin pack that failed phase: verify (e.g. missing or
  # empty) never reaches the analysis step at all (action.yml gates on this)
  # -- surfaced as its own typed operational error, not silently run against
  # an invalid pack or lost as an unexplained "no report" ambiguous below.
  MODE="operational-error"
  RESOLVE_ARGS+=(--resolve-outcome "ambiguous" --resolve-message "collect-facts phase: verify failed -- the wrapper/clang-plugin evidence pack is missing or invalid.")
elif [[ "$COLLECT_REPLAY_OUTCOME" == "failure" ]]; then
  MODE="operational-error"
  RESOLVE_ARGS+=(--resolve-outcome "ambiguous" --resolve-message "collect-facts phase: auto (replay) failed to resolve source evidence.")
elif [[ "$ANALYSIS_RAN" != "true" || -z "$ANALYSIS_REPORT_PATH" || ! -f "$ANALYSIS_REPORT_PATH" ]]; then
  # Baseline resolution succeeded (or was skipped for baseline-channel:
  # none), but the analysis step never produced a report -- a genuine
  # orchestration/infrastructure failure (e.g. the nested root Action
  # crashed before its own report-writing step), not a resolve-baseline
  # outcome. Surface it the same typed way, never silently as a clean pass.
  MODE="operational-error"
  RESOLVE_ARGS+=(--resolve-outcome "ambiguous" --resolve-message "the analysis step did not produce a report file (check-target-analysis.json).")
else
  MODE="augment"
fi

ENVELOPE_ARGS=(
  --mode "$MODE"
  --report-out "$REPORT_OUT"
  --name "$NAME"
  --profile-id "$PROFILE"
  --baseline-channel "$BASELINE_CHANNEL"
  --requested-depth "$REQUESTED_DEPTH"
  --gate-mode "$GATE_MODE"
  --project "$PROJECT"
  --head-sha "$HEAD_SHA"
  --base-ref "$BASE_REF"
  --action-version "$ACTION_VERSION"
)
if [[ "$MODE" == "augment" ]]; then
  ENVELOPE_ARGS+=(--report-in "$ANALYSIS_REPORT_PATH" --analysis-exit-code "$ANALYSIS_EXIT_CODE")
elif [[ "$MODE" == "bootstrap" ]]; then
  ENVELOPE_ARGS+=(--resolve-message "${RESOLVE_MESSAGE:-no baseline set exists yet for this channel.}")
else
  ENVELOPE_ARGS+=("${RESOLVE_ARGS[@]}")
fi

echo "::group::Write check-target report envelope (mode: $MODE)"
set +e
ENVELOPE_STDOUT=$(python3 "$ACTION_PATH/report_envelope.py" "${ENVELOPE_ARGS[@]}")
ENVELOPE_EXIT=$?
set -e
echo "$ENVELOPE_STDOUT"
echo "::endgroup::"

if [[ $ENVELOPE_EXIT -ne 0 ]]; then
  _fail "check-target failed to write its report envelope (see the group above)."
fi

if [[ "$RESOLVE_RAN" == "true" ]]; then
  OUTCOME_OUTPUT="$RESOLVE_OUTCOME"
else
  OUTCOME_OUTPUT="skipped"
fi
{
  # grep -v always has other lines to pass through (check-id/verdict/... are
  # always printed before exit-code=), but || true guards the pipeline's exit
  # status against pipefail anyway, matching resolve-baseline/run.sh's own
  # documented caution around grep inside `set -o pipefail`.
  echo "$ENVELOPE_STDOUT" | grep -v '^exit-code=' || true
  echo "outcome=$OUTCOME_OUTPUT"
} >> "${GITHUB_OUTPUT:-/dev/null}"

FINAL_EXIT=$(echo "$ENVELOPE_STDOUT" | sed -n 's/^exit-code=//p')
echo "check-target report: $REPORT_OUT (mode: $MODE, exit-code: $FINAL_EXIT)"
exit "${FINAL_EXIT:-1}"
