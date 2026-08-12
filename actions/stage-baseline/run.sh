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
    tar --zstd -cf "$asset_name" -C "$BASELINE_PATH" .
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
