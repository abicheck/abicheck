#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# The three phases of actions/aggregate. Each is invoked as its own composite
# step so a failure names the phase it happened in, and so the compatibility
# exit code is captured in exactly one place.
set -euo pipefail

_fail() { echo "::error::$*"; exit 1; }

PHASE="${1:?usage: run.sh collect|aggregate|validate}"

# Refusals from the Python owners (EXIT_REFUSED). Distinguished from an
# ordinary crash so the message says which contract was broken.
_REFUSED=3

case "$PHASE" in
  collect)
    CHECKS="${INPUT_CHECKS:?checks input is required}"
    REPORTS_DIR="${INPUT_REPORTS_DIR:?reports-dir input is required}"
    MANIFEST_PATH="${INPUT_MANIFEST_PATH:?manifest-path input is required}"
    # The declaration goes through a file, never through a command line: it
    # is caller-supplied JSON that may contain any character, and an argv
    # round trip through a composite step's shell is exactly where a quote
    # in a check id would stop being data.
    DECL=$(mktemp)
    trap 'rm -f "$DECL"' EXIT
    printf '%s' "$CHECKS" > "$DECL"

    GATE_ARGS=()
    if [[ -n "${INPUT_GATE:-}" ]]; then
      GATE_ARGS=(--gate "$INPUT_GATE")
    fi

    rc=0
    # ${arr[@]+"${arr[@]}"}, not a bare "${arr[@]}": under macOS's stock
    # (GPLv2-frozen) bash 3.2's set -u, expanding an *empty* array as
    # "${arr[@]}" is itself treated as an unbound-variable reference (bash
    # 4.4+ special-cased this away). Every array below is legitimately empty
    # on a normal path, so a bare expansion aborts the step outright -- the
    # same trap action/run.sh already guards against throughout.
    python -m abicheck.frontends.action.cli collect-checks "$DECL" \
      --reports-dir "$REPORTS_DIR" \
      --manifest "$MANIFEST_PATH" \
      ${GATE_ARGS[@]+"${GATE_ARGS[@]}"} \
      --github-output "$GITHUB_OUTPUT" || rc=$?
    if [[ "$rc" == "$_REFUSED" ]]; then
      _fail "the check declaration could not be honoured (see above). This is a configuration error, not a compatibility result."
    elif [[ "$rc" != 0 ]]; then
      _fail "collecting the declared checks failed with exit $rc."
    fi
    ;;

  aggregate)
    REPORTS_DIR="${INPUT_REPORTS_DIR:?reports-dir input is required}"
    MANIFEST_PATH="${INPUT_MANIFEST_PATH:?manifest-path input is required}"
    OUTPUTS="${INPUT_OUTPUTS:-json=aggregate.json}"

    OUT_ARGS=()
    AGGREGATE_JSON=""
    for spec in $OUTPUTS; do
      case "$spec" in
        */*|*..*) _fail "outputs entry '$spec' names a path; only bare filenames inside reports-dir are accepted." ;;
      esac
      OUT_ARGS+=(-o "$spec")
      if [[ "$spec" == json=* ]]; then
        AGGREGATE_JSON="${spec#json=}"
      fi
    done
    [[ -n "$AGGREGATE_JSON" ]] || _fail "outputs must include a 'json=<file>' entry -- the JSON document is what every downstream consumer reads."

    # Run from inside the reports directory so each target's recorded
    # report_path is a bare filename beside the aggregate document. The
    # report-only publisher reads member reports only from the aggregate's
    # own directory and refuses an absolute path, so aggregating with an
    # absolute directory argument silently costs every per-target detail.
    #
    # aggregate's exit code is a COMPATIBILITY/coverage decision, not an
    # operational one: 0 pass, 1 coverage/quality, 2 API break, 4 ABI break.
    # It is captured and reported, never swallowed with `|| true` and never
    # allowed to fail this step. 64 is a usage error -- ours, and fatal.
    rc=0
    ( cd "$REPORTS_DIR" && abicheck aggregate . --manifest "$(cd "$(dirname "$MANIFEST_PATH")" && pwd)/$(basename "$MANIFEST_PATH")" ${OUT_ARGS[@]+"${OUT_ARGS[@]}"} ) || rc=$?
    if [[ "$rc" == 64 ]]; then
      _fail "abicheck aggregate rejected its inputs (usage error)."
    fi
    case "$rc" in
      0|1|2|4) ;;
      *) _fail "abicheck aggregate exited $rc, which is not one of its compatibility codes (0/1/2/4) -- treating it as an operational failure rather than a verdict." ;;
    esac
    {
      echo "compatibility-exit=$rc"
      echo "aggregate-path=$REPORTS_DIR/$AGGREGATE_JSON"
    } >> "$GITHUB_OUTPUT"
    ;;

  validate)
    AGGREGATE_PATH="${INPUT_AGGREGATE_PATH:?aggregate-path is required}"
    [[ -s "$AGGREGATE_PATH" ]] || _fail "aggregate document $AGGREGATE_PATH is missing or empty -- the analysis did not complete, which is never an empty finding set."
    rc=0
    python -m abicheck.frontends.action.cli validate-aggregate "$AGGREGATE_PATH" \
      --expected "${INPUT_EXPECTED:?expected count is required}" \
      --github-output "$GITHUB_OUTPUT" || rc=$?
    if [[ "$rc" == "$_REFUSED" ]]; then
      _fail "the aggregate document does not describe a real outcome (see above). Operational report loss must not reach a publisher disguised as a clean result."
    elif [[ "$rc" != 0 ]]; then
      _fail "validating the aggregate document failed with exit $rc."
    fi
    ;;

  *)
    _fail "unknown phase '$PHASE'"
    ;;
esac
