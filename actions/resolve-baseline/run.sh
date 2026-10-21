#!/usr/bin/env bash
# Resolves channel x target/bundle x profile against an already-staged
# baseline-set to one of ADR-047 Section 6's typed outcomes -- never a
# compatibility verdict, never silently degraded to "no baseline = compatible".
# See actions/resolve-baseline/action.yml for the input/output contract and
# actions/resolve-baseline/resolve_baseline.py for the pure resolution logic
# (abicheck/buildsource/baseline_set.py).
set -euo pipefail

_fail() {
  echo "::error::$1"
  exit 1
}

# Like _fail, but for a failure that has a real resolution outcome (as
# opposed to a usage/input error that never reached resolution logic at
# all) -- writes the same typed outcome/bootstrap/message shape
# resolve_baseline.py's own outputs use before failing the job, so a
# caller inspecting this Action's outputs (or running under
# continue-on-error) can distinguish "baseline archive was malformed" from
# an unrelated input/runner failure instead of seeing no outputs at all
# (Codex review). Callers may embed $BASELINE_PATH in $message since it is
# newline-guarded below, same as $CHANNEL.
_fail_ambiguous() {
  local message="$1"
  {
    echo "outcome=ambiguous"
    echo "bootstrap=false"
    echo "manifest-path="
    echo "snapshot-path="
    echo "binaries-dir="
    echo "binary-paths={}"
    echo "member-snapshots-dir="
    echo "member-header-evidence=none"
    echo "archive-digest="
    echo "archive-digest-verified=false"
    echo "archive-attestation-verified=false"
    echo "message=$message"
    echo "channel=$CHANNEL"
  } >> "${GITHUB_OUTPUT:-/dev/null}"
  _fail "$message"
}

BASELINE_PATH="${INPUT_BASELINE_PATH:?baseline-path input is required}"
CHANNEL="${INPUT_CHANNEL:?channel input is required}"
KIND="${INPUT_KIND:-target}"
TARGET="${INPUT_TARGET:-}"
BUNDLE="${INPUT_BUNDLE:-}"
BUNDLE_MEMBERS="${INPUT_BUNDLE_MEMBERS:-[]}"
PROFILE="${INPUT_PROFILE:?profile input is required}"
REQUIRED="${INPUT_REQUIRED:-true}"
CANDIDATE_BUILD_OUTPUT="${INPUT_CANDIDATE_BUILD_OUTPUT:-}"
EXPECTED_PROJECT_REF="${INPUT_EXPECTED_PROJECT_REF:-}"
EXPECTED_BASELINE_GENERATION="${INPUT_EXPECTED_BASELINE_GENERATION:-}"
EXPECTED_ARCHIVE_DIGEST="${INPUT_EXPECTED_ARCHIVE_DIGEST:-}"
ALLOW_NEW_TARGET="${INPUT_ALLOW_NEW_TARGET:-false}"
STAGE_MEMBER_SNAPSHOTS="${INPUT_STAGE_MEMBER_SNAPSHOTS:-false}"
VERIFY_ATTESTATION="${INPUT_VERIFY_BASELINE_ATTESTATION:-false}"
ATTESTATION_REPO="${INPUT_BASELINE_ATTESTATION_REPO:-}"
ATTESTATION_SIGNER_WORKFLOW="${INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW:-}"
ACTION_PATH="${ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# A newline in channel (or baseline-path, which _fail_ambiguous may embed
# in a message written to $GITHUB_OUTPUT) would corrupt the key=value
# lines this script appends there -- rejecting it up front is simpler and
# equally safe as heredoc-encoding it, and both are always meant to be
# short identifiers/paths anyway (Codex/CodeRabbit review).
case "$CHANNEL" in
  *$'\n'*) _fail "channel input must not contain a newline." ;;
esac
case "$BASELINE_PATH" in
  *$'\n'*) _fail "baseline-path input must not contain a newline." ;;
esac
case "$KIND" in
  target | bundle | members) ;;
  *) _fail "kind '$KIND' is not recognized. Use 'target', 'bundle', or 'members'." ;;
esac
case "$REQUIRED" in
  true | false) ;;
  *) _fail "required '$REQUIRED' is not recognized. Use 'true' or 'false'." ;;
esac
case "$ALLOW_NEW_TARGET" in
  true | false) ;;
  *) _fail "allow-new-target '$ALLOW_NEW_TARGET' is not recognized. Use 'true' or 'false'." ;;
esac
case "$STAGE_MEMBER_SNAPSHOTS" in
  true | false) ;;
  *) _fail "stage-member-snapshots '$STAGE_MEMBER_SNAPSHOTS' is not recognized. Use 'true' or 'false'." ;;
esac
# expected-archive-digest pins the archive file's own bytes (sha256:<hex> or
# bare hex). Validated here, not only inside the helper, so a malformed pin
# fails as an input error even when baseline-path is not an archive at all.
if [[ -n "$EXPECTED_ARCHIVE_DIGEST" ]] && ! [[ "$EXPECTED_ARCHIVE_DIGEST" =~ ^([Ss][Hh][Aa]256:)?[0-9A-Fa-f]{64}$ ]]; then
  _fail "expected-archive-digest '$EXPECTED_ARCHIVE_DIGEST' is not a SHA-256 digest. Use 'sha256:<64 hex characters>' or the 64 hex characters alone."
fi
# verify-baseline-attestation (opt-in): the archive's GitHub artifact
# attestation is checked with `gh attestation verify` before extraction.
# The repo/signer inputs only mean something when it is on, so setting them
# with it off is refused rather than silently ignored.
case "$VERIFY_ATTESTATION" in
  true | false) ;;
  *) _fail "verify-baseline-attestation '$VERIFY_ATTESTATION' is not recognized. Use 'true' or 'false'." ;;
esac
if [[ "$VERIFY_ATTESTATION" == "false" && ( -n "$ATTESTATION_REPO" || -n "$ATTESTATION_SIGNER_WORKFLOW" ) ]]; then
  _fail "baseline-attestation-repo/baseline-attestation-signer-workflow are set but verify-baseline-attestation is 'false', so nothing would be verified. Set verify-baseline-attestation: true, or unset them."
fi
if [[ -n "$ATTESTATION_REPO" ]] && ! [[ "$ATTESTATION_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  _fail "baseline-attestation-repo '$ATTESTATION_REPO' is not an owner/repo name."
fi
if [[ -n "$ATTESTATION_SIGNER_WORKFLOW" ]] && ! [[ "$ATTESTATION_SIGNER_WORKFLOW" =~ ^[^[:space:]]+/\.github/workflows/[^[:space:]/]+$ ]]; then
  _fail "baseline-attestation-signer-workflow '$ATTESTATION_SIGNER_WORKFLOW' is not a workflow path. Use 'owner/repo/.github/workflows/<file>.yml'."
fi
if [[ "$VERIFY_ATTESTATION" == "true" && -z "$ATTESTATION_REPO" ]]; then
  ATTESTATION_REPO="${GITHUB_REPOSITORY:-}"
  if [[ -z "$ATTESTATION_REPO" ]]; then
    _fail "verify-baseline-attestation is 'true' but neither baseline-attestation-repo nor GITHUB_REPOSITORY names the repository that attested the archive."
  fi
fi
if [[ "$KIND" == "target" && -z "$TARGET" ]]; then
  _fail "target input is required when kind is 'target'."
fi
if [[ "$KIND" == "bundle" && -z "$BUNDLE" ]]; then
  _fail "bundle input is required when kind is 'bundle'."
fi
if [[ "$KIND" == "members" && ( -z "$BUNDLE_MEMBERS" || "$BUNDLE_MEMBERS" == "[]" ) ]]; then
  _fail "bundle-members input is required when kind is 'members' -- name every member this set must contain, so an absent one is a reported failure rather than a silently shorter set."
fi

# Resolve BASELINE_PATH to a directory this run can read manifest.json from:
# use it directly if it already is one (the calling workflow already
# downloaded/restored it via actions/cache, actions/download-artifact, or gh
# release download -- this Action does not itself know how to fetch from a
# baseline channel's storage backend, that orchestration stays the caller's
# job per ADR-047 Section 10's storage-backend table), or extract it here if
# it's an archive.
_EXTRACT_DIR=""
ARCHIVE_DIGEST=""
ARCHIVE_VERIFIED="false"
ATTESTATION_VERIFIED="false"
if [[ -d "$BASELINE_PATH" ]]; then
  # A digest pins archive bytes. A directory has none to check, so pinning one
  # is refused rather than silently ignored: the caller would believe the set
  # was verified when nothing was.
  if [[ -n "$EXPECTED_ARCHIVE_DIGEST" ]]; then
    _fail "expected-archive-digest is set but baseline-path '$BASELINE_PATH' is a directory, not an archive -- the digest pins an archive file's bytes, so there is nothing here to verify. Stage the set as its .tar.zst/.tar.gz/.tar archive, or unset expected-archive-digest."
  fi
  if [[ "$VERIFY_ATTESTATION" == "true" ]]; then
    _fail "verify-baseline-attestation is 'true' but baseline-path '$BASELINE_PATH' is a directory, not an archive -- an attestation covers an archive file's bytes, so there is nothing here to verify. Stage the set as its archive, or set verify-baseline-attestation: false."
  fi
  BASELINE_DIR="$BASELINE_PATH"
elif [[ -f "$BASELINE_PATH" ]]; then
  _EXTRACT_DIR=$(mktemp -d)
  BASELINE_DIR="$_EXTRACT_DIR"
  echo "::group::Extract baseline-set archive $BASELINE_PATH"
  # Stage the archive at a bash-native temp path before handing it to tar's
  # -f rather than passing $BASELINE_PATH directly: on a Windows runner,
  # $BASELINE_PATH is a native "C:\..." path, and GNU tar's archive-name
  # parser treats a colon-containing -f argument as a [user@]host:file
  # remote-tape spec (the classic drive-letter gotcha) instead of a local
  # file, failing with "Cannot connect to C: resolve failed". $_EXTRACT_DIR
  # (bash's own mktemp -d) never has this problem, so copying into it once
  # sidesteps the issue for every tar variant without depending on
  # --force-local support, which isn't guaranteed identical across GNU tar
  # and macOS's bsdtar.
  _ARCHIVE_COPY="$_EXTRACT_DIR.archive-input"
  # Every failure branch below uses _fail_ambiguous, not _fail: baseline-path
  # WAS a file that existed, so a staging, digest, extraction or format failure
  # here means the archive itself is truncated, corrupted, altered or unusable
  # -- the same class of malformed-baseline-set failure the manifest-shape check
  # below reports as ambiguous, not a bare runner/input failure a
  # continue-on-error caller can't tell apart from an unrelated problem.
  cp "$BASELINE_PATH" "$_ARCHIVE_COPY" \
    || _fail_ambiguous "failed to stage $BASELINE_PATH for extraction -- the archive may be truncated or unreadable."
  # The archive's encoding is chosen from its own suffix, the same mapping the
  # root Action's baseline-set fallback and stage-baseline use.
  case "$BASELINE_PATH" in
    *.tar.zst) _ARCHIVE_FORMAT="tar.zst" ;;
    *.tar.gz | *.tgz) _ARCHIVE_FORMAT="tar.gz" ;;
    *.tar) _ARCHIVE_FORMAT="tar" ;;
    *)
      rm -f "$_ARCHIVE_COPY" || true
      _fail_ambiguous "baseline-path '$BASELINE_PATH' is a file but not a recognized archive (.tar.zst/.tar.gz/.tgz/.tar)."
      ;;
  esac
  # One owner for the archive's trust decisions, in this order: verify the
  # bytes against expected-archive-digest (when pinned) BEFORE any member is
  # written, extract with TarExtractor's member validation, then reject every
  # symlink the archive planted. abicheck/workflows/baseline_archive.py
  # implements all three; the root Action calls the same entry point.
  # -I: the Python isolation invariant this Action applies to every
  # interpreter call against a caller-controlled workspace.
  # Attestation first (when opted in), on the same staged bytes the digest
  # check and extraction read, and before any member is written.
  if [[ "$VERIFY_ATTESTATION" == "true" ]]; then
    _ATTEST_EXIT=0
    _ATTEST_OUTPUT=$(bash "$ACTION_PATH/../../action/verify-baseline-attestation.sh" "$_ARCHIVE_COPY" "$ATTESTATION_REPO" "$ATTESTATION_SIGNER_WORKFLOW") || _ATTEST_EXIT=$?
    if [[ $_ATTEST_EXIT -ne 0 ]]; then
      rm -f "$_ARCHIVE_COPY" || true
      _ATTEST_MESSAGE=$(printf '%s\n' "$_ATTEST_OUTPUT" | sed -n 's/^message=//p')
      _fail_ambiguous "${_ATTEST_MESSAGE:-baseline-set archive attestation verification failed (exit $_ATTEST_EXIT).}"
    fi
    ATTESTATION_VERIFIED="true"
  fi
  _PREPARE_ARGS=(--archive "$_ARCHIVE_COPY" --dest "$BASELINE_DIR" --format "$_ARCHIVE_FORMAT")
  if [[ -n "$EXPECTED_ARCHIVE_DIGEST" ]]; then
    _PREPARE_ARGS+=(--expected-digest "$EXPECTED_ARCHIVE_DIGEST")
  fi
  set +e
  _PREPARE_OUTPUT=$(python3 -I -m abicheck.frontends.action.cli prepare-baseline-set "${_PREPARE_ARGS[@]}")
  _PREPARE_EXIT=$?
  set -e
  rm -f "$_ARCHIVE_COPY" || true
  if [[ $_PREPARE_EXIT -ne 0 ]]; then
    _PREPARE_MESSAGE=$(printf '%s\n' "$_PREPARE_OUTPUT" | sed -n 's/^message=//p')
    _fail_ambiguous "${_PREPARE_MESSAGE:-failed to prepare baseline-set archive $BASELINE_PATH (exit $_PREPARE_EXIT).}"
  fi
  BASELINE_DIR=$(printf '%s\n' "$_PREPARE_OUTPUT" | sed -n 's/^manifest-root=//p')
  ARCHIVE_DIGEST=$(printf '%s\n' "$_PREPARE_OUTPUT" | sed -n 's/^archive-digest=//p')
  ARCHIVE_VERIFIED=$(printf '%s\n' "$_PREPARE_OUTPUT" | sed -n 's/^verified=//p')
  echo "::endgroup::"
  # An archive may itself contain one nested directory (e.g. the profile-named
  # dir the archive was built from) rather than manifest.json at its root. The
  # helper descends into that single subdirectory; anything still lacking a
  # manifest is malformed, not "no baseline published yet" -- which is only the
  # not_found outcome reached when baseline-path doesn't exist at all, below.
  # Falling through would let a required: false caller treat a broken archive as
  # an ordinary bootstrap (Codex review), so this has a typed ambiguous outcome.
  if [[ ! -f "$BASELINE_DIR/manifest.json" ]]; then
    _fail_ambiguous "baseline-set archive $BASELINE_PATH does not contain a manifest.json at its root or in a single unambiguous subdirectory -- this archive is malformed, not simply an unpublished baseline."
  fi
else
  # Neither a directory nor a file -- this is the not_found case, not a
  # usage error: the calling workflow may legitimately have nothing staged
  # yet (e.g. the very first release-contract publish). Let
  # resolve_baseline.py's own not_found/bootstrap handling decide based on
  # `required`, rather than hard-failing here regardless of that input.
  BASELINE_DIR="$BASELINE_PATH"
fi

CANDIDATE_EVIDENCE_PRODUCER=""
if [[ -n "$CANDIDATE_BUILD_OUTPUT" ]]; then
  if [[ ! -f "$CANDIDATE_BUILD_OUTPUT" ]]; then
    _fail "candidate-build-output '$CANDIDATE_BUILD_OUTPUT' does not exist."
  fi
  # -I: defense in depth -- this snippet only imports stdlib json/sys today,
  # but isolating every python3 -c invocation this Action runs against a
  # caller-controlled workspace is the invariant, not "isolate the ones that
  # happen to import abicheck today."
  CANDIDATE_EVIDENCE_PRODUCER=$(python3 -I -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
print(json.dumps(data.get("evidence_producer") or {}))
' "$CANDIDATE_BUILD_OUTPUT") || _fail "failed to read evidence_producer from $CANDIDATE_BUILD_OUTPUT."
fi

RESOLVE_ARGS=(
  --baseline-dir "$BASELINE_DIR"
  --kind "$KIND"
  --profile "$PROFILE"
  --required "$REQUIRED"
)
if [[ "$KIND" == "target" ]]; then
  RESOLVE_ARGS+=(--name "$TARGET")
elif [[ "$KIND" == "members" ]]; then
  # A member set has no single target/bundle id of its own -- the set IS the
  # subject -- so --name carries the channel purely so diagnostics say which
  # set failed.
  RESOLVE_ARGS+=(--name "$CHANNEL" --members "$BUNDLE_MEMBERS")
else
  RESOLVE_ARGS+=(--name "$BUNDLE" --members "$BUNDLE_MEMBERS")
fi
if [[ -n "$CANDIDATE_EVIDENCE_PRODUCER" ]]; then
  RESOLVE_ARGS+=(--candidate-evidence-producer "$CANDIDATE_EVIDENCE_PRODUCER")
fi
if [[ -n "$EXPECTED_PROJECT_REF" ]]; then
  RESOLVE_ARGS+=(--expected-project-ref "$EXPECTED_PROJECT_REF")
fi
if [[ -n "$EXPECTED_BASELINE_GENERATION" ]]; then
  RESOLVE_ARGS+=(--expected-baseline-generation "$EXPECTED_BASELINE_GENERATION")
fi
if [[ "$KIND" == "target" || "$KIND" == "members" ]]; then
  RESOLVE_ARGS+=(--allow-new-target "$ALLOW_NEW_TARGET")
fi
# Per-bundle-member baseline header staging. Staged under a directory of
# this run's own making, never inside $BASELINE_DIR: a baseline-set
# directory a caller restored from a cache/archive may be re-uploaded or
# re-packaged verbatim by a later step, and adding a member-snapshots/ tree
# to it would make that published baseline-set carry a second copy of every
# member's snapshot, invisible to its own manifest.json/content-digest.
MEMBER_STAGING_DIR=""
if [[ "$KIND" == "bundle" && "$STAGE_MEMBER_SNAPSHOTS" == "true" ]]; then
  MEMBER_STAGING_DIR=$(mktemp -d)
  RESOLVE_ARGS+=(--stage-member-snapshots "$MEMBER_STAGING_DIR")
fi

echo "::group::Resolve baseline ($CHANNEL / $KIND / $PROFILE)"
set +e
# -I: resolve_baseline.py imports abicheck.buildsource.baseline_set -- same
# workspace-shadowing risk as the extraction snippets above (a caller-set
# PYTHONPATH could otherwise substitute a shadow abicheck/buildsource/
# baseline_set.py during every resolution, not just during extraction).
# resolve_baseline.py itself does no sys.path manipulation, so isolating it
# is safe: Python still adds $ACTION_PATH (this Action's own, trusted
# directory) as sys.path[0] for a script invocation regardless of -I.
RESOLVE_STDOUT=$(python3 -I "$ACTION_PATH/resolve_baseline.py" "${RESOLVE_ARGS[@]}")
RESOLVE_EXIT=$?
set -e
echo "$RESOLVE_STDOUT"
echo "::endgroup::"

{
  echo "$RESOLVE_STDOUT"
  echo "channel=$CHANNEL"
  echo "archive-digest=$ARCHIVE_DIGEST"
  echo "archive-digest-verified=$ARCHIVE_VERIFIED"
  echo "archive-attestation-verified=$ATTESTATION_VERIFIED"
} >> "${GITHUB_OUTPUT:-/dev/null}"

OUTCOME=$(echo "$RESOLVE_STDOUT" | sed -n 's/^outcome=//p')
MESSAGE=$(echo "$RESOLVE_STDOUT" | sed -n 's/^message=//p')
BOOTSTRAP=$(echo "$RESOLVE_STDOUT" | sed -n 's/^bootstrap=//p')

# NOTE: deliberately no cleanup of $_EXTRACT_DIR here. A `resolved` outcome's
# snapshot-path/binaries-dir outputs point *inside* it -- a downstream step
# in this same job (e.g. the root Action's compare step) still needs to read
# from there. The runner reclaims the whole temp area when the job ends.
if [[ $RESOLVE_EXIT -eq 64 ]]; then
  _fail "resolve-baseline usage error: $MESSAGE"
elif [[ "$OUTCOME" == "not_found" && "$BOOTSTRAP" == "true" ]]; then
  echo "::notice::baseline not found for channel '$CHANNEL' ($MESSAGE) -- bootstrap: required=false, treating as an advisory 'no baseline yet' pass."
  exit 0
elif [[ "$OUTCOME" == "new_target" ]]; then
  echo "::notice::target not found in channel '$CHANNEL''s baseline-set ($MESSAGE) -- allow-new-target: true, treating as an advisory 'new target' pass."
  exit 0
elif [[ $RESOLVE_EXIT -ne 0 ]]; then
  _fail "resolve-baseline failed ($OUTCOME): $MESSAGE"
fi

echo "resolved baseline: outcome=$OUTCOME"
exit 0
