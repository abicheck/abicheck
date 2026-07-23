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
  target | bundle) ;;
  *) _fail "kind '$KIND' is not recognized. Use 'target' or 'bundle'." ;;
esac
case "$REQUIRED" in
  true | false) ;;
  *) _fail "required '$REQUIRED' is not recognized. Use 'true' or 'false'." ;;
esac
if [[ "$KIND" == "target" && -z "$TARGET" ]]; then
  _fail "target input is required when kind is 'target'."
fi
if [[ "$KIND" == "bundle" && -z "$BUNDLE" ]]; then
  _fail "bundle input is required when kind is 'bundle'."
fi

# Resolve BASELINE_PATH to a directory this run can read manifest.json from:
# use it directly if it already is one (the calling workflow already
# downloaded/restored it via actions/cache, actions/download-artifact, or gh
# release download -- this Action does not itself know how to fetch from a
# baseline channel's storage backend, that orchestration stays the caller's
# job per ADR-047 Section 10's storage-backend table), or extract it here if
# it's an archive.
_EXTRACT_DIR=""
if [[ -d "$BASELINE_PATH" ]]; then
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
  # WAS a file that existed, so a staging/extraction/format failure here
  # means the archive itself is truncated, corrupted, or unusable -- the
  # same class of malformed-baseline-set failure the no-manifest/ambiguous-
  # subdirectory and symlink checks below already report as ambiguous, not
  # a bare runner/input failure a continue-on-error caller can't tell apart
  # from an unrelated problem (Codex review, third round).
  cp "$BASELINE_PATH" "$_ARCHIVE_COPY" \
    || _fail_ambiguous "failed to stage $BASELINE_PATH for extraction -- the archive may be truncated or unreadable."
  case "$BASELINE_PATH" in
    *.tar.zst)
      # GNU tar's --zstd filters through an external `zstd` binary this
      # composite Action never installs (only setup-python + pip install
      # abicheck) -- on a minimal/self-hosted runner without one, tar fails
      # before resolution even though the archive itself is perfectly
      # valid, misreporting a good baseline as corrupt (Codex review, fifth
      # round). Delegate to abicheck.package.TarExtractor's own
      # _safe_extract_zst_tar (already installed by the earlier "Install
      # abicheck" step) instead of reimplementing the same zstd-vs-
      # zstandard fallback here: it tries the Python 'zstandard' package
      # first, falls back to a system zstd binary, and -- unlike a plain
      # `tarfile.extractall()` on Python <3.12, which has no member-path
      # validation at all before PEP 706's `filter="data"` -- validates
      # every member (rejects `..`-escaping paths, symlink-target escapes,
      # and device/FIFO entries) before extracting, on every supported
      # Python version, not just 3.12+ (Codex review, sixth round: a
      # naive Python-side zstd fallback on Python 3.10/3.11 without a
      # system zstd binary could extract a `../`-escaping member outside
      # $BASELINE_DIR before the symlink/manifest checks below even run).
      python3 -c '
import sys
from pathlib import Path
from abicheck.package import TarExtractor

TarExtractor._safe_extract_zst_tar(Path(sys.argv[1]), Path(sys.argv[2]))
' "$_ARCHIVE_COPY" "$BASELINE_DIR" \
        || _fail_ambiguous "failed to extract $BASELINE_PATH (.tar.zst) -- the archive is truncated or corrupted, or this runner has neither a 'zstd' command-line tool nor the Python 'zstandard' package available (install one of them, e.g. 'apt-get install zstd' or 'pip install zstandard')."
      ;;
    *.tar.gz | *.tgz | *.tar)
      # Delegate to TarExtractor._safe_extract instead of plain `tar -x`:
      # bare tar has no member validation at all, so a malformed archive
      # could plant a `..`-escaping path, a symlink escaping $BASELINE_DIR,
      # or a device/FIFO entry on disk before the symlink/manifest checks
      # below even run -- the same class of risk the .tar.zst branch above
      # already closed by routing through this same extractor (Codex
      # review). `tarfile.open`'s default mode auto-detects gzip vs. plain
      # tar from the file itself, so one call handles both extensions.
      python3 -c '
import sys
from pathlib import Path
from abicheck.package import TarExtractor

TarExtractor._safe_extract(Path(sys.argv[1]), Path(sys.argv[2]))
' "$_ARCHIVE_COPY" "$BASELINE_DIR" \
        || _fail_ambiguous "failed to extract $BASELINE_PATH -- the archive is truncated or corrupted, or contains a disallowed member (path traversal, a symlink escaping the extraction root, or a device/FIFO entry)."
      ;;
    *)
      rm -f "$_ARCHIVE_COPY" || true
      _fail_ambiguous "baseline-path '$BASELINE_PATH' is a file but not a recognized archive (.tar.zst/.tar.gz/.tgz/.tar)."
      ;;
  esac
  rm -f "$_ARCHIVE_COPY" || true
  echo "::endgroup::"
  # Reject any symlink the archive planted: a crafted/compromised baseline
  # archive could stage a symlink at the path a later resolved
  # snapshot-path/binaries-dir entry would follow, reading (or, chained with
  # a second archive, writing) outside $_EXTRACT_DIR -- a baseline-set has
  # no legitimate reason to contain one, so reject the whole extraction
  # rather than silently following it (CodeRabbit review). Uses
  # _fail_ambiguous, not _fail: a symlink-containing archive is a malformed
  # baseline-set, the same class of failure the manifest-shape check below
  # reports as ambiguous, not a bare usage error -- a caller inspecting
  # this Action's outputs (or running under continue-on-error) must see the
  # same typed outcome/bootstrap/message shape for it (Codex review).
  # Captured via command substitution, not piped into `grep -q`: under
  # `set -o pipefail`, `grep -q` exits as soon as it sees the first match
  # without draining find's remaining output, which can SIGPIPE find (exit
  # 141) -- pipefail then reports the pipeline's exit status as find's 141
  # (the rightmost *non-zero* exit among the pipeline's commands, since
  # grep's own exit was 0), so `if find ... | grep -q .` evaluates FALSE
  # and this guard never fires, letting a symlink-laden archive silently
  # resolve (Codex review, fifth round -- reproduced with an archive
  # containing hundreds of symlinks). Command substitution reads find's
  # full output with no downstream reader racing to close the pipe early,
  # so there's no SIGPIPE to misreport.
  _SYMLINKS=$(find "$BASELINE_DIR" -type l)
  if [[ -n "$_SYMLINKS" ]]; then
    _fail_ambiguous "baseline-set archive $BASELINE_PATH contains a symlink, which is not supported -- baseline-set archives must contain only plain files/directories."
  fi
  # An archive may itself contain one nested directory (e.g. the
  # profile-named dir the archive was built from) rather than manifest.json
  # at its root -- if there's no manifest.json at the extraction root but
  # there's exactly one subdirectory, descend into it.
  if [[ ! -f "$BASELINE_DIR/manifest.json" ]]; then
    _SUBDIRS=()
    while IFS= read -r -d '' d; do _SUBDIRS+=("$d"); done \
      < <(find "$BASELINE_DIR" -mindepth 1 -maxdepth 1 -type d -print0)
    if [[ ${#_SUBDIRS[@]} -eq 1 && -f "${_SUBDIRS[0]}/manifest.json" ]]; then
      BASELINE_DIR="${_SUBDIRS[0]}"
    else
      # 0 or >1 candidates -- this archive was actually provided but is
      # malformed/ambiguous, which is a different problem than "no baseline
      # published yet" (the legitimate not_found/bootstrap case, only ever
      # reached when baseline-path doesn't exist at all, below). Falling
      # through here would leave $BASELINE_DIR at the extraction root (no
      # manifest.json), and resolve_baseline.py would then report
      # not_found -- silently letting a `required: false` caller treat a
      # broken archive as an ordinary "nothing published yet" bootstrap
      # instead of a real extraction failure (Codex review). Uses
      # _fail_ambiguous, not _fail: this has a real resolution outcome
      # (ambiguous), not merely a usage error, so it must carry the same
      # typed outputs every other resolution failure does (Codex review,
      # second round).
      _fail_ambiguous "baseline-set archive $BASELINE_PATH does not contain a manifest.json at its root or in a single unambiguous subdirectory -- this archive is malformed, not simply an unpublished baseline."
    fi
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
  CANDIDATE_EVIDENCE_PRODUCER=$(python3 -c '
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
else
  RESOLVE_ARGS+=(--name "$BUNDLE" --members "$BUNDLE_MEMBERS")
fi
if [[ -n "$CANDIDATE_EVIDENCE_PRODUCER" ]]; then
  RESOLVE_ARGS+=(--candidate-evidence-producer "$CANDIDATE_EVIDENCE_PRODUCER")
fi

echo "::group::Resolve baseline ($CHANNEL / $KIND / $PROFILE)"
set +e
RESOLVE_STDOUT=$(python3 "$ACTION_PATH/resolve_baseline.py" "${RESOLVE_ARGS[@]}")
RESOLVE_EXIT=$?
set -e
echo "$RESOLVE_STDOUT"
echo "::endgroup::"

{
  echo "$RESOLVE_STDOUT"
  echo "channel=$CHANNEL"
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
elif [[ $RESOLVE_EXIT -ne 0 ]]; then
  _fail "resolve-baseline failed ($OUTCOME): $MESSAGE"
fi

echo "resolved baseline: outcome=$OUTCOME"
exit 0
