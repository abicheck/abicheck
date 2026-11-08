#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Dump (or adopt) one default-branch snapshot and name it for its commit.
set -euo pipefail

_fail() { echo "::error::$*"; exit 1; }

NAME=$(python -m abicheck.frontends.action.cli snapshot-name "${SNAPSHOT_SHA:?}" \
  --profile "${INPUT_PROFILE:-}") || _fail "cannot name a snapshot for ${SNAPSHOT_SHA}"
OUT_DIR="${RUNNER_TEMP:-/tmp}/abicheck-main-snapshot"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/snapshot.json"

if [[ -n "${INPUT_SNAPSHOT:-}" ]]; then
  [[ -z "${INPUT_LIBRARY:-}" ]] || _fail "set either 'library' or 'snapshot', not both."
  [[ -f "$INPUT_SNAPSHOT" ]] || _fail "snapshot '$INPUT_SNAPSHOT' does not exist."
  cp "$INPUT_SNAPSHOT" "$OUT"
else
  [[ -n "${INPUT_LIBRARY:-}" ]] || _fail "set 'library' (or 'snapshot')."
  ARGS=("$INPUT_LIBRARY")
  while IFS= read -r line; do
    [[ -z "${line// }" ]] || ARGS+=(-H "$line")
  done <<< "${INPUT_HEADER:-}"
  while IFS= read -r line; do
    [[ -z "${line// }" ]] || ARGS+=("$line")
  done <<< "${INPUT_DUMP_ARGS:-}"
  ARGS+=(--label "$SNAPSHOT_SHA" -o "$OUT")
  abicheck dump "${ARGS[@]}"
fi

{
  echo "artifact-name=$NAME"
  echo "snapshot-path=$OUT"
} >> "$GITHUB_OUTPUT"
