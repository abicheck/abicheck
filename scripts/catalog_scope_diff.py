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

"""Report-only: which catalog cases change verdict under the public contract.

Compares a legacy-scope ``validate_examples.py --json`` report (each case's
authored scope; unscoped unless it opts in) with a ``--contract-scope public``
report of the same lane (the default scoped compare users get) and writes a
Markdown table of the cases whose actual verdict or status differs. Ground
truth is authored unscoped, so a difference is a measurement, not a failure:
for example case180's change is excluded under public scoping. Always exits 0
unless a report cannot be read. Pure stdlib.

    python scripts/catalog_scope_diff.py results/validate_examples-debug-headers.json \\
        results/validate_examples-public.json > results/scope-diff.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

Row = tuple[str, str, str]  # (status, actual verdict, expected)


def load(path: Path) -> dict[str, Row]:
    """``case_id -> (status, actual, expected)`` from one report."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(r.get("case_id") or r.get("name")): (
            str(r.get("status")),
            str(r.get("got")),
            str(r.get("expected")),
        )
        for r in data.get("results", [])
    }


def differences(legacy: dict[str, Row], public: dict[str, Row]) -> list[str]:
    """Sorted case ids whose status or actual verdict differs."""
    return sorted(
        c for c in set(legacy) & set(public) if legacy[c][:2] != public[c][:2]
    )


def render(legacy: dict[str, Row], public: dict[str, Row]) -> str:
    diff = differences(legacy, public)
    lines = [
        "## Catalog: public contract vs legacy scope (report-only)",
        "",
        f"{len(diff)} of {len(set(legacy) & set(public))} cases differ.",
        "",
    ]
    if diff:
        lines += [
            "| Case | Expected | Legacy | Public |",
            "|---|---|---|---|",
        ]
        for c in diff:
            (ls, la, exp), (ps, pa, _) = legacy[c], public[c]
            lines.append(f"| {c} | {exp} | {la} ({ls}) | {pa} ({ps}) |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("legacy", type=Path)
    ap.add_argument("public", type=Path)
    args = ap.parse_args(argv)
    sys.stdout.write(render(load(args.legacy), load(args.public)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
