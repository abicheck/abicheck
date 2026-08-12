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
asset_name="${asset_template//\{profile\}/${PROFILE:-}}"

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
    # rather than requiring an undeclared runner prerequisite.
    if command -v zstd >/dev/null 2>&1 && _tar_zstd_works; then
      tar --zstd -cf "$asset_name" -C "$BASELINE_PATH" .
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
      tar -cf "$asset_name.tmp-payload" -C "$BASELINE_PATH" .
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
" "$asset_name.tmp-payload" "$asset_name"
      # "./$asset_name.tmp-payload", not a bare "$asset_name.tmp-payload"
      # -- rm's own arg parser treats a leading '-' as a run of short
      # options, not part of a filename (e.g. a resolved
      # "-nightly.tar.zst.tmp-payload" fails with "rm: invalid option --
      # 'n'"). A custom asset-name-template resolving to a name starting
      # with '-' packages fine up to this point -- '-' is a perfectly
      # legal leading filename character on every real filesystem, and
      # this script's own newline/CR/path-separator/drive-prefix/'#' guard
      # above does not reject it -- but this cleanup would otherwise abort
      # the whole step under `set -euo pipefail` even though the archive
      # was already built successfully, the same class of gap the upload
      # step in publish-baseline.yml already guards against for this
      # supported filename case (Codex review).
      rm -f "./$asset_name.tmp-payload"
    fi
    ;;
  *.tar.gz | *.tgz)
    tar -czf "$asset_name" -C "$BASELINE_PATH" .
    ;;
  *.tar)
    tar -cf "$asset_name" -C "$BASELINE_PATH" .
    ;;
  *)
    echo "::error::asset-name-template resolved to '$asset_name', which has no recognized archive extension (.tar.zst/.tar.gz/.tgz/.tar) -- resolve-baseline/root-action.yml's baseline-set consumers pick their extractor from this exact suffix, so an unrecognized one would silently produce an asset nothing downstream can extract." >&2
    exit 1
    ;;
esac

echo "asset-name=$asset_name" >> "$GITHUB_OUTPUT"
