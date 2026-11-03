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

"""Full-catalog ratchet for the nightly ``validate_examples.py`` lanes.

``catalog/nightly_baseline.json`` maps each lane (artifact variant) to the
sorted case ids whose status was ``PASS`` and, separately, ``XFAIL``. Given
one or more ``validate_examples.py --json`` reports, this gate fails when

* a baselined ``PASS`` case is no longer ``PASS``,
* a baselined ``XFAIL`` case is now ``FAIL`` or ``ERROR``, or
* a baselined lane has no report at all (the lane never produced output),

and only *prints* improvements (a case newly ``PASS``/``XFAIL``), cases
missing from a report, and lanes not in the baseline, with the command that
re-records the baseline. Pure stdlib.

    python scripts/check_catalog_ratchet.py results/validate_examples-*.json
    python scripts/check_catalog_ratchet.py results/*.json --write-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "catalog" / "nightly_baseline.json"
SCHEMA = "catalog_nightly_baseline.v1"
TRACKED = ("PASS", "XFAIL")
#: Statuses a baselined XFAIL case may not fall to.
BROKEN = frozenset({"FAIL", "ERROR"})

Lanes = dict[str, dict[str, str]]  # lane -> case id -> status


def load_reports(paths: list[Path]) -> Lanes:
    """Each report's ``results`` grouped by lane (``mode``) and case id."""
    lanes: Lanes = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data.get("results", []):
            lane = str(row.get("mode") or row.get("variant"))
            case = str(row.get("case_id") or row.get("name"))
            lanes.setdefault(lane, {})[case] = str(row.get("status"))
    return lanes


def baseline_from(lanes: Lanes) -> dict[str, object]:
    """The baseline document recording *lanes*."""
    return {
        "schema_version": SCHEMA,
        "lanes": {
            lane: {
                status: sorted(c for c, s in cases.items() if s == status)
                for status in TRACKED
            }
            for lane, cases in sorted(lanes.items())
        },
    }


def compare(baseline: dict[str, object], lanes: Lanes) -> tuple[list[str], list[str]]:
    """``(regressions, notes)`` of *lanes* against *baseline*."""
    regressions: list[str] = []
    notes: list[str] = []
    recorded: dict[str, dict[str, list[str]]] = baseline.get("lanes", {})  # type: ignore[assignment]
    for lane, lists in sorted(recorded.items()):
        if lane not in lanes:
            regressions.append(f"{lane}: lane missing from the reports")
            continue
        got = lanes[lane]
        for status in TRACKED:
            for case in lists.get(status, []):
                now = got.get(case)
                if now is None:
                    notes.append(f"{lane}: {case} ({status}) not in the report")
                elif status == "PASS" and now != "PASS":
                    regressions.append(f"{lane}: {case} PASS -> {now}")
                elif status == "XFAIL" and now in BROKEN:
                    regressions.append(f"{lane}: {case} XFAIL -> {now}")
        known = {c for s in TRACKED for c in lists.get(s, [])}
        passed = set(lists.get("PASS", []))
        for case, now in sorted(got.items()):
            if now == "PASS" and case not in passed:
                notes.append(f"{lane}: {case} newly PASS (was {_was(lists, case)})")
            elif now == "XFAIL" and case not in known:
                notes.append(f"{lane}: {case} newly XFAIL")
    for lane in sorted(set(lanes) - set(recorded)):
        notes.append(f"{lane}: lane not in the baseline")
    return regressions, notes


def _was(lists: dict[str, list[str]], case: str) -> str:
    return "XFAIL" if case in lists.get("XFAIL", []) else "untracked"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args(argv)
    lanes = load_reports(args.reports)
    if args.write_baseline:
        doc = baseline_from(lanes)
        args.baseline.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        for lane, lists in doc["lanes"].items():  # type: ignore[union-attr]
            print(f"{lane}: {len(lists['PASS'])} PASS, {len(lists['XFAIL'])} XFAIL")
        return 0
    regressions, notes = compare(json.loads(args.baseline.read_text()), lanes)
    for line in notes:
        print(f"note: {line}")
    if notes:
        print(
            "re-record: python scripts/check_catalog_ratchet.py "
            + " ".join(str(p) for p in args.reports)
            + " --write-baseline"
        )
    for line in regressions:
        print(f"REGRESSION: {line}", file=sys.stderr)
    print(f"{len(regressions)} regression(s), {len(notes)} note(s)")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
