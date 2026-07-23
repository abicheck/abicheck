#!/usr/bin/env bash
# Fails fast on an unsupported check-target input combination, before Python
# setup or any nested Action runs -- mirrors action/validate-inputs.sh's
# rationale for the root Action. See action.yml for the full input contract.
set -euo pipefail

_fail() {
  echo "::error::$1"
  exit 64
}

KIND="${INPUT_KIND:-target}"
TARGET_KIND="${INPUT_TARGET_KIND:-library}"
NAME="${INPUT_NAME:-}"
BUNDLE_MEMBERS="${INPUT_BUNDLE_MEMBERS:-[]}"
BASELINE_CHANNEL="${INPUT_BASELINE_CHANNEL:?baseline-channel input is required}"
BASELINE_PATH="${INPUT_BASELINE_PATH:-}"
GATE_MODE="${INPUT_GATE_MODE:-local}"
REQUESTED_DEPTH="${INPUT_REQUESTED_DEPTH:?requested-depth input is required}"
EVIDENCE_PRODUCER="${INPUT_EVIDENCE_PRODUCER:-}"
CONSUMER_BINARY="${INPUT_CONSUMER_BINARY:-}"
CONTRACT_FILE="${INPUT_CONTRACT_FILE:-}"

case "$KIND" in
  target | bundle) ;;
  *) _fail "kind '$KIND' is not recognized. Use 'target' or 'bundle'." ;;
esac
case "$TARGET_KIND" in
  library | app-consumer | plugin-contract) ;;
  *) _fail "target-kind '$TARGET_KIND' is not recognized. Use 'library', 'app-consumer', or 'plugin-contract'." ;;
esac
case "$GATE_MODE" in
  local | deferred | advisory) ;;
  *) _fail "gate-mode '$GATE_MODE' is not recognized. Use 'local', 'deferred', or 'advisory'." ;;
esac
case "$REQUESTED_DEPTH" in
  binary | headers | build | source) ;;
  *) _fail "requested-depth '$REQUESTED_DEPTH' is not recognized. Use 'binary', 'headers', 'build', or 'source'." ;;
esac
case "$EVIDENCE_PRODUCER" in
  "" | wrapper | clang-plugin | replay) ;;
  *) _fail "evidence-producer '$EVIDENCE_PRODUCER' is not recognized. Use '' (none), 'wrapper', 'clang-plugin', or 'replay' -- a misspelled value silently skips fact collection instead of failing loud." ;;
esac
if [[ -z "$NAME" ]]; then
  _fail "name input is required."
fi
if [[ "$KIND" == "bundle" ]]; then
  if [[ "$TARGET_KIND" != "library" ]]; then
    _fail "target-kind must be 'library' when kind is 'bundle' -- app-consumer/plugin-contract are single-target concepts."
  fi
  if [[ "$BUNDLE_MEMBERS" == "[]" ]]; then
    _fail "bundle-members must be a non-empty JSON array when kind is 'bundle'."
  fi
  if [[ "$REQUESTED_DEPTH" == "build" || "$REQUESTED_DEPTH" == "source" ]]; then
    # kind: bundle always compares directories (the resolved binaries-dir vs.
    # the candidate bundle directory), which routes through the CLI's
    # per-library release fan-out -- that fan-out never collects inline
    # build/source evidence and the root Action's run.sh now fails loud
    # rather than silently dropping a build/source-depth request for a
    # directory operand (Codex review). Failing here, before resolve-
    # baseline/collect-facts even run, gives a clearer and cheaper error
    # than letting the nested analysis step fail later.
    _fail "requested-depth '$REQUESTED_DEPTH' is not supported when kind is 'bundle' -- a bundle compares directories, which the CLI's per-library release fan-out never collects inline build/source evidence for. Use requested-depth: binary or headers for a bundle check, or kind: target to compare one library at build/source depth."
  fi
fi
if [[ "$TARGET_KIND" == "app-consumer" && -z "$CONSUMER_BINARY" ]]; then
  _fail "consumer-binary is required when target-kind is 'app-consumer'."
fi
if [[ "$TARGET_KIND" == "plugin-contract" && -z "$CONTRACT_FILE" ]]; then
  _fail "contract-file is required when target-kind is 'plugin-contract'."
fi
if [[ "$BASELINE_CHANNEL" == "none" && "$TARGET_KIND" != "library" ]]; then
  # baseline-channel: none routes the analysis step to `scan` (a one-build
  # audit), but the root Action's --used-by/--required-symbols contract
  # scoping only exists in its `compare` branch -- `scan` has no equivalent
  # flag at all. Without this check, an app-consumer/plugin-contract check
  # with no baseline would silently run as a plain unscoped scan under the
  # contract target's name and could pass without ever checking the
  # consumer/plugin contract it claims to (Codex review).
  _fail "target-kind '$TARGET_KIND' is not supported with baseline-channel: none -- a single-build audit (scan) has no --used-by/--required-symbols equivalent to scope the contract check against. Use target-kind: library for a baseline-channel: none audit, or set a real baseline-channel to run the scoped compare."
fi
if [[ "$BASELINE_CHANNEL" != "none" && -z "$BASELINE_PATH" ]]; then
  _fail "baseline-path is required when baseline-channel is not 'none'."
fi

exit 0
