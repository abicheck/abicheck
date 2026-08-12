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
    if command -v zstd >/dev/null 2>&1; then
      tar --zstd -cf "$asset_name" -C "$BASELINE_PATH" .
    else
      echo "::notice::'zstd' executable not found on PATH -- falling back to Python's zstandard package to build $asset_name." >&2
      python3 -c "import zstandard" >/dev/null 2>&1 || pip install --quiet zstandard
      tar -cf "$asset_name.tmp-payload" -C "$BASELINE_PATH" .
      python3 -c "
import sys
import zstandard

src, dst = sys.argv[1], sys.argv[2]
cctx = zstandard.ZstdCompressor()
with open(src, 'rb') as inp, open(dst, 'wb') as out, cctx.stream_writer(out) as writer:
    writer.write(inp.read())
" "$asset_name.tmp-payload" "$asset_name"
      rm -f "$asset_name.tmp-payload"
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
