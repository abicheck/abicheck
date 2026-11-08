#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# delta-on-missing-base: build (docs/reference/main-snapshot.md). No stored
# snapshot describes the PR's base commit, so build that commit here: check
# it out into its own worktree beside the PR checkout (the PR's build
# outputs stay untouched) and run the user's delta-base-build-command there.
# action/run.sh then dumps the result with the gate's new-side evidence
# inputs (abicheck.frontends.action.delta_base_dump).
#
# Every result is a successful step: `built`, or `build_failed` with the
# stage that failed. A failed base build never changes the run's exit code
# and never reads as a clean delta -- run.sh turns it into the comment's
# `base_build_failed` note. The step is given no token (action.yml passes
# none): the build runs the base commit's own code.
#
# Inputs (env): BASE_SHA, ABICHECK_BASE_BUILD_COMMAND, DEST, GITHUB_OUTPUT.
set -uo pipefail

_out() { printf '%s=%s\n' "$1" "$2" >> "$GITHUB_OUTPUT"; }
# Only a validated SHA ever reaches a workflow command (a newline in it would
# forge one); every message below is otherwise fixed text.
_SHORT=unknown
_failed() {  # $1 = stage, $2 = message
  printf '%s\n' "::warning::abicheck delta base build (${_SHORT}) failed at ${1}: ${2} The comment says so; the gate is unaffected."
  _out outcome build_failed
  _out stage "$1"
  exit 0
}

[[ "${BASE_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || _failed checkout "no full base commit SHA."
_SHORT="${BASE_SHA:0:7}"
_cmd="${ABICHECK_BASE_BUILD_COMMAND:-}"
[[ -n "${_cmd//[[:space:]]/}" ]] \
  || _failed build "delta-base-build-command is empty."

if ! git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
  git fetch --no-tags --depth 1 origin "$BASE_SHA" >/dev/null 2>&1 \
    || _failed checkout "commit ${BASE_SHA} is not in the local history and could not be fetched."
fi
rm -rf "${DEST:?}"
git worktree add --detach --force "$DEST" "$BASE_SHA" >/dev/null 2>&1 \
  || _failed checkout "git worktree add ${BASE_SHA} failed."

printf '%s\n' "::group::Build ${_SHORT} (delta-base-build-command)"
rc=0
(cd "$DEST" && ABICHECK_BASE_SHA="$BASE_SHA" bash -e -o pipefail -c "$_cmd") || rc=$?
printf '%s\n' "::endgroup::"
[[ "$rc" == 0 ]] || _failed build "delta-base-build-command exited ${rc}."
_out outcome built
_out root "$DEST"
