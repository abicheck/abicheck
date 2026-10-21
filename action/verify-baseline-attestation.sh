#!/usr/bin/env bash
# Verify a baseline-set archive's GitHub artifact attestation before anything
# is extracted from it (ADR-047 amendment, "Baseline-set archive provenance").
#
# Shared by the root Action (action/run.sh) and actions/resolve-baseline, so
# the two consumers apply one rule. Opt-in: callers invoke this only when
# verify-baseline-attestation is 'true'.
#
# Usage: verify-baseline-attestation.sh <archive> <repo> [<signer-workflow>]
#   <repo>             owner/repo whose workflow attested the archive
#   <signer-workflow>  optional [host/]owner/repo/.github/workflows/<file>
#                      that must have produced the attestation
#
# Prints `message=<text>` on failure. Exit codes, all fail-closed:
#   0  attestation verified          2  gh (GitHub CLI) not on PATH
#   3  verification refused          64 usage error
# A missing gh is never treated as "nothing to verify": the caller asked for a
# provenance check, and skipping it would report an unverified archive as
# verified.
set -uo pipefail

archive="${1:-}"
repo="${2:-}"
signer="${3:-}"

if [[ -z "$archive" || ! -f "$archive" ]]; then
  echo "message=baseline-set archive '$archive' does not exist, so its attestation cannot be verified."
  exit 64
fi
if ! [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "message=baseline-attestation-repo '$repo' is not an owner/repo name."
  exit 64
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "message=verify-baseline-attestation is 'true' but the GitHub CLI (gh) is not on PATH, so the baseline-set archive's attestation cannot be verified. Install gh on the runner, or set verify-baseline-attestation: false."
  exit 2
fi

args=(attestation verify "$archive" --repo "$repo")
if [[ -n "$signer" ]]; then
  args+=(--signer-workflow "$signer")
fi
output=""
if ! output=$(gh "${args[@]}" 2>&1); then
  # gh's own explanation goes to the log; the message stays one line.
  printf '%s\n' "$output" >&2
  echo "message=baseline-set archive attestation verification failed (gh attestation verify --repo $repo${signer:+ --signer-workflow $signer}). The archive was not extracted."
  exit 3
fi
echo "verified=true"
exit 0
