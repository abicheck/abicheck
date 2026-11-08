#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# delta-on-missing-base: build (docs/reference/main-snapshot.md). No stored
# snapshot describes the PR's base commit, so build that commit here: check
# it out into its own worktree beside the PR checkout (the PR's build
# outputs stay untouched) and run the user's delta-base-build-command there.
# action/run.sh calls this only AFTER the gate comparison has run, and then
# dumps the result with the gate's new-side evidence inputs
# (abicheck.frontends.action.delta_base_dump).
#
# The command is sandboxed from everything that outlives it in the job:
#   - GITHUB_ENV / GITHUB_PATH / GITHUB_OUTPUT / GITHUB_STEP_SUMMARY point at
#     throwaway files, so it cannot change a later step's environment or
#     forge this script's result;
#   - no token or Action input is in its environment, and the checkout's
#     persisted credentials are blanked for the git commands it runs;
#   - the result goes to $RESULT_FILE, which the command is not told about.
#
# Every result is a success of this script: `outcome=built` (+ `root=`), or
# `outcome=build_failed` with the `stage` that failed. A failed base build
# never changes the run's exit code and never reads as a clean delta.
#
# Inputs (env): BASE_SHA, ABICHECK_BASE_BUILD_COMMAND, DEST, RESULT_FILE,
# REPO_DIR (the PR checkout's top level).
set -uo pipefail

_out() { printf '%s=%s\n' "$1" "$2" >> "$RESULT_FILE"; }
# Only a validated SHA ever reaches a workflow command (a newline in it would
# forge one); every message below is otherwise fixed text.
_SHORT=unknown
_failed() {  # $1 = stage, $2 = message
  printf '%s\n' "::warning::abicheck delta base build (${_SHORT}) failed at ${1}: ${2} The comment says so; the gate is unaffected."
  _out outcome build_failed
  _out stage "$1"
  exit 0
}

: "${RESULT_FILE:?}" "${DEST:?}"
[[ "${BASE_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || _failed checkout "no full base commit SHA."
_SHORT="${BASE_SHA:0:7}"
_cmd="${ABICHECK_BASE_BUILD_COMMAND:-}"
[[ -n "${_cmd//[[:space:]]/}" ]] || _failed build "delta-base-build-command is empty."
cd "${REPO_DIR:-.}" 2>/dev/null || _failed checkout "the PR checkout directory is not accessible."

if ! git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
  git fetch --no-tags --depth 1 origin "$BASE_SHA" >/dev/null 2>&1 \
    || _failed checkout "commit ${BASE_SHA} is not in the local history and could not be fetched."
fi
git worktree remove --force "$DEST" >/dev/null 2>&1 || true
rm -rf "$DEST"
git worktree add --detach --force "$DEST" "$BASE_SHA" >/dev/null 2>&1 \
  || _failed checkout "git worktree add ${BASE_SHA} failed."

junk=$(mktemp -d "${RUNNER_TEMP:-/tmp}/abicheck-delta-build-env.XXXXXX") \
  || _failed build "no scratch directory for the build's runner files."
: > "$junk/env"; : > "$junk/path"; : > "$junk/output"; : > "$junk/summary"

printf '%s\n' "::group::Build ${_SHORT} (delta-base-build-command)"
rc=0
(
  cd "$DEST" || exit 1
  # Tokens and every Action input stay out of the build's environment.
  unset GH_TOKEN GITHUB_TOKEN ACTIONS_RUNTIME_TOKEN ACTIONS_ID_TOKEN_REQUEST_TOKEN \
        ACTIONS_ID_TOKEN_REQUEST_URL RESULT_FILE ABICHECK_BASE_BUILD_COMMAND
  while IFS= read -r _name; do unset "$_name"; done < <(compgen -e | grep -E '^(INPUT_|ABICHECK_DELTA_)' || true)
  export GITHUB_ENV="$junk/env" GITHUB_PATH="$junk/path" \
         GITHUB_OUTPUT="$junk/output" GITHUB_STEP_SUMMARY="$junk/summary"
  # actions/checkout persists its token as an http extraheader; blank it for
  # every git command the build runs (it can still read .git/config, which
  # is why the docs recommend persist-credentials: false).
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.https://github.com/.extraheader GIT_CONFIG_VALUE_0=
  export ABICHECK_BASE_SHA="$BASE_SHA"
  exec bash -e -o pipefail -c "$_cmd"
) || rc=$?
printf '%s\n' "::endgroup::"
rm -rf "$junk"
[[ "$rc" == 0 ]] || _failed build "delta-base-build-command exited ${rc}."
_out outcome built
_out root "$DEST"
