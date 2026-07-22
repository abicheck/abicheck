#!/usr/bin/env python3
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
"""CLI wrapper backing ``actions/resolve-baseline/run.sh`` (G30 P1.2,
ADR-047 §4/§6).

Resolves ``channel × target/bundle × profile`` against an already-staged
baseline-set directory -- the calling workflow is responsible for
downloading/restoring the right physical baseline-set for the requested
channel (see ``action.yml``'s ``baseline-path`` input doc); this script only
resolves *within* that directory. Thin argparse wrapper around
``abicheck.buildsource.baseline_set``'s pure resolver -- prints
``key=value`` lines on stdout that ``run.sh`` forwards to ``GITHUB_OUTPUT``,
the same pattern ``actions/baseline/build_manifest.py`` uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from abicheck.buildsource.baseline_set import (
    ResolveOutcome,
    ResolveResult,
    resolve_bundle,
    resolve_target,
)

#: Usage error, matching the repo-wide convention documented in AGENTS.md
#: ("64 = usage error (bad flags/inputs)").
_EXIT_USAGE_ERROR = 64


def _print_outputs(result: ResolveResult) -> None:
    # One line per GITHUB_OUTPUT key. `message` is always built from f-strings
    # with no embedded newlines (see baseline_set.py), so a plain key=value
    # line is safe here -- no GITHUB_OUTPUT heredoc delimiter needed.
    print(f"outcome={result.outcome}")
    print(f"bootstrap={'true' if result.bootstrap else 'false'}")
    print(f"manifest-path={result.manifest_path or ''}")
    print(f"snapshot-path={result.snapshot_path or ''}")
    print(f"binaries-dir={result.binaries_dir or ''}")
    print(f"binary-paths={json.dumps(result.binary_paths)}")
    print(f"message={result.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=["target", "bundle"])
    parser.add_argument("--name", required=True, help="target id or bundle id")
    parser.add_argument(
        "--members",
        default="[]",
        help="JSON array of member target ids (kind: bundle only)",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--required", required=True, choices=["true", "false"])
    parser.add_argument(
        "--candidate-evidence-producer",
        default="",
        help='JSON object {"kind", "tool", "version"}, or empty to skip the '
        "incompatible_evidence check",
    )
    args = parser.parse_args(argv)

    candidate_evidence_producer = None
    if args.candidate_evidence_producer:
        try:
            candidate_evidence_producer = json.loads(args.candidate_evidence_producer)
        except json.JSONDecodeError as exc:
            print(
                f"::error::--candidate-evidence-producer is not valid JSON: {exc}",
                file=sys.stderr,
            )
            return _EXIT_USAGE_ERROR
        if not isinstance(candidate_evidence_producer, dict):
            print(
                "::error::--candidate-evidence-producer must be a JSON object",
                file=sys.stderr,
            )
            return _EXIT_USAGE_ERROR

    required = args.required == "true"

    if args.kind == "target":
        result = resolve_target(
            args.baseline_dir,
            target=args.name,
            profile=args.profile,
            required=required,
            candidate_evidence_producer=candidate_evidence_producer,
        )
    else:
        try:
            members_raw = json.loads(args.members)
        except json.JSONDecodeError as exc:
            print(f"::error::--members is not valid JSON: {exc}", file=sys.stderr)
            return _EXIT_USAGE_ERROR
        if not isinstance(members_raw, list) or not members_raw:
            print(
                "::error::--members must be a non-empty JSON array for --kind bundle",
                file=sys.stderr,
            )
            return _EXIT_USAGE_ERROR
        result = resolve_bundle(
            args.baseline_dir,
            bundle=args.name,
            members=[str(m) for m in members_raw],
            profile=args.profile,
            required=required,
            candidate_evidence_producer=candidate_evidence_producer,
        )

    _print_outputs(result)

    if result.outcome == ResolveOutcome.RESOLVED:
        return 0
    if result.outcome == ResolveOutcome.NOT_FOUND and result.bootstrap:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
