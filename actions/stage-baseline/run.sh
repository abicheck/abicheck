#!/usr/bin/env bash
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Packages a baseline-set directory into a single archive, encoded per the
# resolved asset name's own extension. Single source of truth for this
# logic -- publish-baseline.yml's "Package baseline-set" step calls this
# Action instead of keeping its own copy, so a fix here (e.g. a new
# supported extension) reaches every caller at once.
set -euo pipefail

# `command -v zstd` alone doesn't prove `tar --zstd` actually works --
# some tar builds (a BSD/older tar without the zstd filter compiled in)
# reject `--zstd` as an unrecognized option even with a standalone zstd
# CLI present on PATH, which would otherwise fail the fast path outright
# before the Python fallback ever gets a chance to run (CodeRabbit
# nitpick). A real trial archive, not just `tar --zstd --help` (whose
# exit behavior for an unrecognized option varies across tar
# implementations), is the only reliable proof.
_tar_zstd_works() {
  local probe_dir
  probe_dir="$(mktemp -d)"
  printf 'x' > "$probe_dir/f"
  local ok=1
  if tar --zstd -cf "$probe_dir/out.tar.zst" -C "$probe_dir" f >/dev/null 2>&1; then
    ok=0
  fi
  rm -rf "$probe_dir"
  return "$ok"
}

if [[ -z "${BASELINE_PATH:-}" ]]; then
  echo "::error::baseline-path is required." >&2
  exit 1
fi
if [[ ! -d "$BASELINE_PATH" ]]; then
  echo "::error::baseline-path '$BASELINE_PATH' does not exist or is not a directory." >&2
  exit 1
fi
# A caller passing an existing-but-wrong directory (empty, or the wrong
# selection entirely) previously packaged and reported success anyway --
# the failure only surfaced much later, opaquely, when resolve-baseline
# couldn't find manifest.json inside the extracted archive. Every real
# baseline-set has one at its root (actions/baseline's own "Dump
# baseline-set" step always writes one); requiring it here turns a
# confusing downstream failure into an immediate, actionable one at the
# point the mistake was actually made (Codex review).
if [[ ! -f "$BASELINE_PATH/manifest.json" ]]; then
  echo "::error::baseline-path '$BASELINE_PATH' has no manifest.json at its root -- this does not look like a baseline-set directory (actions/baseline's own 'Dump baseline-set' step always writes one). Refusing to package and publish a directory that resolve-baseline could never actually resolve anything from." >&2
  exit 1
fi
# Reject any symlink under baseline-path BEFORE packaging -- both
# actions/resolve-baseline/run.sh and the root Action's baseline-set
# fallback (action/run.sh's _try_baseline_set_fallback) now reject ANY
# symlink found after extracting a baseline-set archive, so a source
# directory that already contains one would package and report success
# here while producing an asset neither canonical consumer can actually
# use (Codex review). A baseline-set has no legitimate reason to contain
# a symlink -- structurally reject it at the source, not just on the
# extraction side. Command substitution, not piped into `grep -q`, for
# the same SIGPIPE/pipefail-misreport reason documented at
# resolve-baseline/run.sh's own identical check.
_source_symlinks="$(find "$BASELINE_PATH" -type l)"
if [[ -n "$_source_symlinks" ]]; then
  echo "::error::baseline-path '$BASELINE_PATH' contains a symlink, which is not supported -- baseline-set archives must contain only plain files/directories (the same structural rule actions/resolve-baseline and the root Action's baseline-set fallback both enforce on extraction). Symlinks found:"$'\n'"$_source_symlinks" >&2
  exit 1
fi

# NOTE: the default is intentionally NOT embedded as
# "${ASSET_NAME_TEMPLATE:-abicheck-baseline-{profile}.tar.zst}" -- bash's
# ${VAR:-default} parses the default text looking for its own closing '}',
# and a literal, unescaped '}' inside that text (from "{profile}")
# terminates the expansion early, silently mangling the result to
# "abicheck-baseline-{profile.tar.zst}" (the same bug found and fixed in
# action/run.sh's _try_baseline_set_fallback -- see that function's own
# comment). Computing the default separately avoids the parse ambiguity
# entirely.
asset_template="${ASSET_NAME_TEMPLATE:-}"
if [[ -z "$asset_template" ]]; then
  asset_template='abicheck-baseline-{profile}.tar.zst'
fi
# NOT "${asset_template//\{profile\}/${PROFILE:-}}" -- Bash 5.2's default
# `patsub_replacement` shopt gives '&' in a ${var//pattern/REPLACEMENT}
# replacement's TEXT the special "insert the matched pattern text" meaning
# (like sed's `&` backreference), so a profile containing a literal '&'
# (e.g. "linux&asan") would silently expand to "...linux{profile}asan..."
# instead of the literal string -- reproduced directly against real Bash
# 5.2.21. Bash 3.2 (macOS stock) has no such interpretation, so the SAME
# template/profile pair would resolve to two DIFFERENT asset names
# depending purely on which runner published vs. consumed it (Codex
# review). `_substitute_literal` sidesteps the whole replacement-TEXT
# position entirely via prefix/suffix pattern-REMOVAL (`%%`/`#`, which
# carry no such special-character semantics) plus plain string
# concatenation for the inserted text.
_substitute_literal() {
  local haystack="$1" needle="$2" replacement="$3" result=""
  while [[ "$haystack" == *"$needle"* ]]; do
    result+="${haystack%%"$needle"*}$replacement"
    haystack="${haystack#*"$needle"}"
  done
  printf '%s' "$result$haystack"
}
asset_name="$(_substitute_literal "$asset_template" '{profile}' "${PROFILE:-}")"
asset_name="$(_substitute_literal "$asset_name" '{generation}' "${GENERATION:-}")"

# Reject a newline/carriage-return in the resolved name BEFORE creating any
# archive or writing to $GITHUB_OUTPUT -- profile/asset-name-template can
# both be influenced by external/PR-controlled metadata in some call
# chains, and an embedded newline surviving into the raw
# "asset-name=$asset_name" line below would let the runner parse the
# remainder as ADDITIONAL, attacker-chosen output key=value assignments (a
# real GitHub Actions output-injection vector -- e.g. a crafted value like
# "x"$'\n'"asset-name=other.tar.zst" overriding this step's own declared
# output). Also rejects a path separator: asset_name is meant to be a bare
# filename written into the current working directory, never a path
# escaping it (Codex review). Both '/' AND '\' are rejected, not just '/'
# -- on a Windows Git Bash runner, `\` is the OS path separator, so a
# resolved name like "..\outside.tar.zst" would otherwise pass this guard
# and let the native `tar`/Python fallback below write the archive outside
# the intended working directory; a drive-qualified prefix ("C:...") is
# rejected the same way for the identical reason (Codex review, second
# round). '#' is rejected too, for a reason specific to this asset's real
# consumer rather than this step itself: `gh release upload` treats
# anything after a literal '#' in a file argument as a DISPLAY LABEL, not
# part of the filename (documented `gh release upload --help` syntax,
# `<file>#<label>`) -- an asset name like "baseline#debug.tar.zst" would
# package and stage just fine here, but publish-baseline.yml's own
# first-time-publish `gh release upload "$RELEASE_TAG" "$ASSET_NAME"`
# call would then try to upload a nonexistent local file named just
# "baseline", stripped of everything from '#' onward, and fail outright
# (Codex review, third round).
case "$asset_name" in
  *$'\n'* | *$'\r'* | */* | *\\* | [A-Za-z]:* | *'#'*)
    echo "::error::asset-name-template resolved to a value containing a newline, carriage return, path separator ('/' or '\\'), a drive-qualified prefix, or '#' (which 'gh release upload' parses as a display-label separator, not a literal filename character) -- refusing to create an archive or write a GITHUB_OUTPUT line from it." >&2
    exit 1
    ;;
esac

# A pre-existing "./$asset_name" (a leftover from a PRIOR invocation of
# this same Action against the same baseline-path/cwd) is EXCLUDED from
# every tar invocation below via --exclude, not deleted from disk --
# staging the new output externally only prevents self-inclusion of the
# file THIS run is currently writing; it does nothing about a DIFFERENT,
# already-on-disk file left behind by an earlier run, which is otherwise
# an ordinary member of baseline-path as far as `tar -C "$BASELINE_PATH"
# .` is concerned and would silently be packaged INTO the new archive
# (Codex review, first round). An earlier version of this fix used
# `rm -f "./$asset_name"` instead, unconditionally and BEFORE validating
# asset_name's own archive suffix -- for an invalid custom
# asset-name-template that happens to alias an existing file inside
# baseline-path (e.g. a template resolving to "manifest.json" literally),
# that unconditional rm deleted the input BEFORE the suffix-validation
# case block below even ran, corrupting the supposedly read-only baseline
# directory before reporting the (unrelated) unsupported-suffix error;
# and even for a VALID suffix, deleting a name that aliases a genuine,
# unrelated source member (not a stale leftover output at all) would
# silently destroy real baseline-set content (Codex review, second
# round). `--exclude` never touches anything on disk regardless of
# validation order or what the name happens to alias -- GNU tar's
# unanchored (no "/") pattern matches the basename anywhere in the
# traversal, which is what's needed since $asset_name always lands
# directly in cwd (never nested, already validated: no "/" is a legal
# asset_name character), but baseline-path containing cwd rather than
# BEING cwd means the file's position relative to baseline-path can be
# nested. Verified directly against real GNU tar 1.35: `--exclude='out.tar'`
# excludes both `./out.tar` at the traversal root and `./sub/out.tar`
# nested one level down.
_exclude_asset_name=(--exclude="$asset_name")

# Every archive is built in a staging directory OUTSIDE $BASELINE_PATH,
# then moved into place as the final $asset_name -- building it directly
# at "$asset_name" (implicitly the current working directory) is only
# safe when $BASELINE_PATH is disjoint from cwd. When baseline-path IS the
# workspace itself, or any directory containing cwd (a real, reachable
# caller shape -- e.g. a workflow that runs this Action from inside the
# very tree it just checked out the baseline-set into), `tar -C
# "$BASELINE_PATH" .` would see its own output file appear mid-archive --
# GNU tar's own self-inclusion detection degrades this to a harmless
# skip-with-warning on this repo's tested tar version, but that is not a
# guaranteed cross-implementation behavior (Codex review), and relying on
# it would mean this step's stderr always carries a spurious "archive
# cannot contain itself" notice for this caller shape regardless.
#
# `mktemp -d`'s default location is NOT unconditionally guaranteed to be
# outside baseline-path, despite an earlier version of this comment's
# claim -- `mktemp -d` honors `$TMPDIR`, and a self-hosted runner (or a
# caller) can set `TMPDIR` to a path under baseline-path, or baseline-path
# itself could BE `$TMPDIR` (Codex review). Verified explicitly instead of
# assumed -- but NOT via a `realpath`-based STRING comparison (a first
# version of this fix tried that and a real windows-latest CI run caught
# it failing to detect an actual collision: `$BASELINE_PATH` arrives from
# the caller as a Windows-style backslash path, while `mktemp`'s own
# output is POSIX-style, and Git Bash/MSYS's `realpath`/Python's
# os.path.realpath do not reliably normalize the two to an identically
# comparable form -- a real, not hypothetical, cross-platform gap). A
# filesystem-IDENTITY check sidesteps path-string representation entirely:
# drop a marker file in the candidate staging directory, then ask `find`
# -- the SAME traversal mechanism `tar -C "$BASELINE_PATH" .` itself
# uses -- whether that marker is reachable from baseline-path at all. Two
# paths naming the same inode always agree regardless of which string
# form either one is spelled in. On a collision, retry once against a
# location that deliberately ignores `$TMPDIR` (plain `/tmp`) -- the one
# caller-configurable variable that could have caused it -- before giving
# up with an actionable error rather than silently proceeding into the
# exact self-inclusion bug this staging scheme exists to prevent.
_staging_dir_escapes_baseline() {
  local candidate="$1" marker found
  marker="$candidate/.stage-baseline-marker"
  : > "$marker"
  found="$(find "$BASELINE_PATH" -samefile "$marker" 2>/dev/null)"
  rm -f "$marker"
  [[ -z "$found" ]]
}
_make_staging_dir() {
  local candidate
  candidate="$(mktemp -d)"
  if ! _staging_dir_escapes_baseline "$candidate"; then
    rm -rf "$candidate"
    candidate="$(TMPDIR=/tmp mktemp -d)"
    if ! _staging_dir_escapes_baseline "$candidate"; then
      rm -rf "$candidate"
      echo "::error::could not create a staging directory outside baseline-path '$BASELINE_PATH' -- both \$TMPDIR-derived and plain /tmp-derived locations resolve inside it. Set TMPDIR to a location disjoint from baseline-path, or move baseline-path itself out from under /tmp." >&2
      return 1
    fi
  fi
  printf '%s\n' "$candidate"
}
_staging_dir="$(_make_staging_dir)" || exit 1
trap 'rm -rf "$_staging_dir"' EXIT

case "$asset_name" in
  *.tar.zst)
    # `tar --zstd` shells out to a separate `zstd` executable (confirmed
    # against real GNU tar 1.35: --help describes --zstd as "filter the
    # archive through zstd", and a runner without that binary on PATH
    # fails with "zstd: not found" even though tar itself recognizes the
    # flag) -- this composite Action, unlike actions/baseline, has no
    # dependency-install step, so a minimal/self-hosted runner without
    # zstd pre-installed would otherwise hard-fail on the DEFAULT
    # asset-name-template alone (Codex review). Falls back to Python's
    # `zstandard` package (already an abicheck core dependency, so it's
    # present on any runner abicheck itself installed onto -- and cheap to
    # install standalone otherwise) when the `zstd` CLI isn't available,
    # rather than requiring an undeclared runner prerequisite. Gated on
    # _tar_zstd_works alone, not also `command -v zstd` -- a tar build
    # with zstd support linked in directly needs no standalone zstd CLI
    # on PATH at all, and _tar_zstd_works's own real trial archive already
    # proves the whole path works regardless of which way tar gets its
    # zstd support (CodeRabbit review).
    if _tar_zstd_works; then
      tar --zstd "${_exclude_asset_name[@]}" -cf "$_staging_dir/archive" -C "$BASELINE_PATH" .
    else
      echo "::notice::'zstd'/'tar --zstd' not usable on this runner -- falling back to Python's zstandard package to build $asset_name." >&2
      # Resolved explicitly rather than assuming a bare `python3` --
      # Git Bash on Windows only ever resolves `python` on a stock CPython
      # layout (the same gap this repo's own reusable-workflow tests
      # already document for other steps), so a bare `python3` call here
      # would fail with a bare "command not found" on that platform even
      # though a usable interpreter exists (Codex review).
      PY="$(command -v python3 || command -v python || true)"
      if [[ -z "$PY" ]]; then
        echo "::error::neither 'zstd' nor a Python interpreter is available on PATH -- cannot build $asset_name. Install zstd on this runner, or use a .tar.gz/.tgz/.tar asset-name-template instead." >&2
        exit 1
      fi
      if ! "$PY" -c "import zstandard" >/dev/null 2>&1; then
        # A bare pip failure (e.g. PEP 668's "externally-managed-environment"
        # refusal on a system Python) previously surfaced as pip's own raw
        # error with no guidance -- give an actionable fallback instead
        # (Codex review).
        if ! "$PY" -m pip install --quiet zstandard; then
          echo "::error::'zstd' is absent and installing the Python 'zstandard' package failed -- install zstd on this runner, or use a .tar.gz/.tgz/.tar asset-name-template instead." >&2
          exit 1
        fi
      fi
      tar "${_exclude_asset_name[@]}" -cf "$_staging_dir/payload" -C "$BASELINE_PATH" .
      # Copied in bounded 1 MiB chunks, not a single inp.read() -- a
      # baseline-set archive can approach the multi-gigabyte GitHub
      # Release asset limit documented in docs/use/baseline-storage.md, so
      # buffering the whole uncompressed payload in memory before handing
      # it to zstandard's own stream_writer (which itself streams output
      # fine) can still OOM-kill a memory-constrained or self-hosted
      # runner (Codex review).
      "$PY" -c "
import sys
import zstandard

src, dst = sys.argv[1], sys.argv[2]
cctx = zstandard.ZstdCompressor()
with open(src, 'rb') as inp, open(dst, 'wb') as out, cctx.stream_writer(out) as writer:
    while True:
        chunk = inp.read(1024 * 1024)
        if not chunk:
            break
        writer.write(chunk)
" "$_staging_dir/payload" "$_staging_dir/archive"
      rm -f "$_staging_dir/payload"
    fi
    ;;
  *.tar.gz | *.tgz)
    tar "${_exclude_asset_name[@]}" -czf "$_staging_dir/archive" -C "$BASELINE_PATH" .
    ;;
  *.tar)
    tar "${_exclude_asset_name[@]}" -cf "$_staging_dir/archive" -C "$BASELINE_PATH" .
    ;;
  *)
    echo "::error::asset-name-template resolved to '$asset_name', which has no recognized archive extension (.tar.zst/.tar.gz/.tgz/.tar) -- resolve-baseline/root-action.yml's baseline-set consumers pick their extractor from this exact suffix, so an unrecognized one would silently produce an asset nothing downstream can extract." >&2
    exit 1
    ;;
esac

# "./$asset_name", not a bare "$asset_name" -- mv's own arg parser treats a
# leading '-' as a flag, not part of a filename, the same leading-dash
# safety this script's own newline/CR/path-separator/drive-prefix/'#'
# guard above already lets through as a legal filename (Codex review,
# same class as the earlier `rm -f "$asset_name.tmp-payload"` fix).
mv "$_staging_dir/archive" "./$asset_name"

echo "asset-name=$asset_name" >> "$GITHUB_OUTPUT"
