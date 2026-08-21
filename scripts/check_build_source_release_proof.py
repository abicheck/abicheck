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

"""Fail closed on the fixed build/source release-proof artifact.

The build/source artifact-mode lane is deliberately a small, fixed 10-case
proof rather than the full example catalog.  Its JSON summary is insufficient
as a gate: it can report ten PASSes while result rows are absent, duplicated,
or skipped.  This checker validates the rows themselves.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXPECTED_CASE_IDS = frozenset({
    "case01_symbol_removal",
    "case04_no_change",
    "case98_cxx_standard_floor_raised",
    "case105_concept_tightening",
    "case122_template_signature_uninstantiated",
    "case129_struct_return_convention",
    "case130_exceptions_mode_flip",
    "case131_rtti_mode_flip",
    "case132_threadsafe_statics_flip",
    "case133_tls_model_flip",
})
EXPECTED_RESULT_COUNT = len(EXPECTED_CASE_IDS)


def validate(data: object) -> list[str]:
    """Return every release-proof contract violation in *data*."""
    if not isinstance(data, dict):
        return ["artifact root must be a JSON object"]

    selected_cases = data.get("selected_cases")
    if selected_cases != EXPECTED_RESULT_COUNT:
        return [
            f"expected selected_cases={EXPECTED_RESULT_COUNT}, found {selected_cases!r}"
        ]

    results = data.get("results")
    if not isinstance(results, list):
        return ["artifact must contain a results array"]
    if len(results) != EXPECTED_RESULT_COUNT:
        return [
            f"expected exactly {EXPECTED_RESULT_COUNT} result rows, found {len(results)}"
        ]

    errors: list[str] = []
    case_ids: list[str] = []
    for index, row in enumerate(results, start=1):
        if not isinstance(row, dict):
            errors.append(f"result row {index} must be an object")
            continue

        case_id = row.get("case_id", row.get("name"))
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"result row {index} has missing or empty case_id")
        else:
            case_ids.append(case_id)

        status = row.get("status")
        if status != "PASS":
            errors.append(
                f"{case_id if isinstance(case_id, str) and case_id else f'result row {index}'} "
                f"must have status PASS, found {status!r}"
            )

    seen = set(case_ids)
    missing = sorted(EXPECTED_CASE_IDS - seen)
    unexpected = sorted(seen - EXPECTED_CASE_IDS)
    duplicates = sorted(case_id for case_id in seen if case_ids.count(case_id) > 1)
    if missing:
        errors.append("missing expected cases: " + ", ".join(missing))
    if unexpected:
        errors.append("unexpected cases: " + ", ".join(unexpected))
    if duplicates:
        errors.append("duplicate case rows: " + ", ".join(duplicates))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact",
        nargs="?",
        type=Path,
        default=Path("results/validate_examples-build-source.json"),
        help="build/source validate_examples JSON artifact",
    )
    args = parser.parse_args(argv)
    try:
        with args.artifact.open(encoding="utf-8") as handle:
            errors = validate(json.load(handle))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"ERROR: cannot read build/source release-proof artifact {args.artifact}: {exc}",
            file=sys.stderr,
        )
        return 1

    if errors:
        print("ERROR: build/source release proof failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"build/source release proof: {EXPECTED_RESULT_COUNT}/{EXPECTED_RESULT_COUNT} PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
