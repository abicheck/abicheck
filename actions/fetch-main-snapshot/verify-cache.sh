#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# storage: cache -- check the entry actions/cache/restore put in the staging
# directory, copy the snapshot under `destination`, and remove the staging
# directory (run.sh refused to start if it already existed, so it is ours).
# Every decision is in abicheck.frontends.action.main_snapshot_cache.
set -euo pipefail

STAGE="${GITHUB_WORKSPACE:-$PWD}/.abicheck-main-snapshot-cache"
# CACHE_SHA is the commit whose entry was restored: the base, or with
# base-equivalent-paths its path-equivalent ancestor. Every check binds the
# entry to that commit; the base is named only in the message.
ENTRY_SHA="${CACHE_SHA:-${BASE_SHA:?}}"
EQUIV=()
[[ "$ENTRY_SHA" != "${BASE_SHA:?}" ]] && EQUIV=(--equivalent-to "$BASE_SHA")
rc=0
python -m abicheck.frontends.action.cli verify-cached-snapshot "$STAGE" \
  --repository "${REPO:?}" --branch "${INPUT_BRANCH:-main}" --base-sha "$ENTRY_SHA" \
  --key "${CACHE_KEY:?}" --cache-hit "${CACHE_HIT:-}" --matched-key "${CACHE_MATCHED_KEY:-}" \
  --destination "${INPUT_DESTINATION:?}" ${EQUIV[@]+"${EQUIV[@]}"} \
  --github-output "$GITHUB_OUTPUT" || rc=$?
rm -rf -- "$STAGE"
if [[ "$rc" != 0 && "$rc" != 3 ]]; then
  {
    echo "outcome=lookup_failed"
    echo "message=Delta unavailable: the cache entry could not be checked."
  } >> "$GITHUB_OUTPUT"
fi
msg=$(grep '^message=' "$GITHUB_OUTPUT" | tail -n1 | cut -d= -f2-)
echo "::notice::abicheck delta base: ${msg}"
