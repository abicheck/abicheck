# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""check_stripped_fp.py — false-positive guard for the stripped-headers lane.

Stripping debug info can only *remove* ABI signal (field offsets, calling
convention, packing), never add a real break. So the sound, blockable invariant
for a full-catalog stripped run is: a case the debug ground truth calls
COMPATIBLE / NO_CHANGE must never come out BREAKING when stripped. Missed
breaks (BREAKING→COMPATIBLE, e.g. case129) are expected evidence loss and are
reported, not failed — that backlog is tracked separately.

Usage:
    python tests/check_stripped_fp.py results/validate_examples-stripped-headers.json

Exit codes:
    0  no stripped false positives
    1  one or more cases gained a spurious BREAKING under stripping
    2  input/usage error
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"

# Verdicts the ground truth may declare as "not a break".
_COMPATIBLE_EXPECTED = {"COMPATIBLE", "NO_CHANGE"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: check_stripped_fp.py <stripped-results.json>", file=sys.stderr)
        return 2
    results_path = Path(argv[0])
    if not results_path.exists():
        print(f"ERROR: {results_path} not found", file=sys.stderr)
        return 2

    gt = _load(GROUND_TRUTH)["verdicts"]
    data = _load(results_path)

    false_positives: list[str] = []
    downgrades: list[str] = []
    for r in data.get("results", []):
        case = r.get("case_id") or r.get("name")
        got = (r.get("got") or "").upper()
        entry = gt.get(case, {})
        expected = (entry.get("expected") or "").upper()
        if r.get("status") in {"SKIP", "ERROR"} or not got:
            continue
        if expected in _COMPATIBLE_EXPECTED and got == "BREAKING":
            false_positives.append(f"{case}: expected {expected} got {got}")
        elif expected == "BREAKING" and got in _COMPATIBLE_EXPECTED:
            downgrades.append(f"{case}: {expected}→{got} (evidence lost by stripping)")

    if downgrades:
        print(f"Stripped downgrades (expected evidence loss, reported): {len(downgrades)}")
        for d in downgrades:
            print(f"  - {d}")
    if false_positives:
        print(f"\nERROR: stripped false positives: {len(false_positives)}", file=sys.stderr)
        for fp in false_positives:
            print(f"  - {fp}", file=sys.stderr)
        return 1
    print("\nStripped FP guard: no spurious breaks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
