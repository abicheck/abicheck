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
EVIDENCE_PRODUCER="${INPUT_EVIDENCE_PRODUCER:-}"
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

COLLECT_VERIFY_RAN="${COLLECT_VERIFY_RAN:-false}"
COLLECT_VERIFY_OUTCOME="${COLLECT_VERIFY_OUTCOME:-}"
COLLECT_VERIFY_READY="${COLLECT_VERIFY_READY:-}"
COLLECT_REPLAY_RAN="${COLLECT_REPLAY_RAN:-false}"
COLLECT_REPLAY_OUTCOME="${COLLECT_REPLAY_OUTCOME:-}"

ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPORT_OUT="check-target-report.json"

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

# ── Evidence-availability signal feeding effective_depth degradation ───────
EVIDENCE_OK="false"
DEGRADED_REASON=""
case "$EVIDENCE_PRODUCER" in
  wrapper | clang-plugin)
    if [[ "$COLLECT_VERIFY_RAN" == "true" && "$COLLECT_VERIFY_OUTCOME" == "success" && "$COLLECT_VERIFY_READY" == "true" ]]; then
      EVIDENCE_OK="true"
    else
      DEGRADED_REASON="collect_facts_not_ready"
    fi
    ;;
  replay)
    if [[ "$COLLECT_REPLAY_RAN" == "true" && "$COLLECT_REPLAY_OUTCOME" == "success" ]]; then
      EVIDENCE_OK="true"
    else
      DEGRADED_REASON="source_replay_failed"
    fi
    ;;
  *)
    DEGRADED_REASON="no_evidence_producer_configured"
    ;;
esac

ENVELOPE_ARGS=(
  --mode "$MODE"
  --report-out "$REPORT_OUT"
  --name "$NAME"
  --profile-id "$PROFILE"
  --baseline-channel "$BASELINE_CHANNEL"
  --requested-depth "$REQUESTED_DEPTH"
  --gate-mode "$GATE_MODE"
  --evidence-ok "$EVIDENCE_OK"
  --degraded-reason "$DEGRADED_REASON"
  --project "$PROJECT"
  --head-sha "$HEAD_SHA"
  --base-ref "$BASE_REF"
  --action-version "$ACTION_VERSION"
)
if [[ "$MODE" == "augment" ]]; then
  ENVELOPE_ARGS+=(--report-in "$ANALYSIS_REPORT_PATH")
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
