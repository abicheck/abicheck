#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Dump (or adopt) one default-branch snapshot and name it for its commit.
set -euo pipefail

_fail() { echo "::error::$*"; exit 1; }

NAME=$(python -m abicheck.frontends.action.cli snapshot-name "${SNAPSHOT_SHA:?}" \
  --profile "${INPUT_PROFILE:-}") || _fail "cannot name a snapshot for ${SNAPSHOT_SHA}"
# A snapshot names a commit, so it must have been built from that commit.
if HEAD_SHA=$(git rev-parse HEAD 2>/dev/null) && [[ "$HEAD_SHA" != "$SNAPSHOT_SHA" ]]; then
  _fail "the checkout is $HEAD_SHA, not $SNAPSHOT_SHA; a snapshot is published only for the commit it was built from."
fi
mkdir -p "${RUNNER_TEMP:-/tmp}/abicheck-main-snapshot"
OUT_DIR=$(mktemp -d "${RUNNER_TEMP:-/tmp}/abicheck-main-snapshot/run.XXXXXX")
OUT="$OUT_DIR/snapshot.json"

if [[ -n "${INPUT_SNAPSHOT:-}" ]]; then
  [[ -z "${INPUT_LIBRARY:-}" ]] || _fail "set either 'library' or 'snapshot', not both."
  [[ -f "$INPUT_SNAPSHOT" ]] || _fail "snapshot '$INPUT_SNAPSHOT' does not exist."
  python -I -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
    "$INPUT_SNAPSHOT" 2>/dev/null || _fail "snapshot '$INPUT_SNAPSHOT' is not a readable JSON document."
  cp "$INPUT_SNAPSHOT" "$OUT"
else
  [[ -n "${INPUT_LIBRARY:-}" ]] || _fail "set 'library' (or 'snapshot')."
  ARGS=("$INPUT_LIBRARY")
  while IFS= read -r line; do
    [[ -z "${line// }" ]] || ARGS+=(-H "$line")
  done <<< "${INPUT_HEADER:-}"
  # One argument per line; a line holding an option and its value
  # (`-I include`) is split on whitespace into both.
  while IFS= read -r line; do
    read -r -a words <<< "$line"
    ARGS+=(${words[@]+"${words[@]}"})
  done <<< "${INPUT_DUMP_ARGS:-}"
  ARGS+=(--label "$SNAPSHOT_SHA" -o "$OUT")
  abicheck dump "${ARGS[@]}"
fi

{
  echo "artifact-name=$NAME"
  echo "snapshot-path=$OUT"
} >> "$GITHUB_OUTPUT"
