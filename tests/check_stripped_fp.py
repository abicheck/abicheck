# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""check_stripped_fp.py — false-positive guard for reduced-evidence artifact lanes.

A non-default artifact mode (stripped / release-without-debug / build-source)
changes the evidence available to the detector. It may legitimately *lose*
signal — a stripped or release binary drops the DWARF a layout/calling-convention
break needs — but it must never *manufacture* a real break. So the sound,
blockable invariant for any such full/partial run is: a case the debug ground
truth calls non-breaking (COMPATIBLE / NO_CHANGE / COMPATIBLE_WITH_RISK) must
never come out BREAKING in the reduced mode. Missed breaks (BREAKING→COMPATIBLE,
e.g. case129 stripped/release) are expected evidence loss and are reported, not
failed.

Usage:
    python tests/check_stripped_fp.py <results.json> [label]

Exit codes:
    0  no false positives in the reduced-evidence run
    1  one or more cases gained a spurious BREAKING
    2  input/usage error
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"

# Verdicts the ground truth may declare as "not a real ABI break".
# COMPATIBLE_WITH_RISK is included: the runtime-model-flip cases (case130–133)
# are risk-only, so a reduced-evidence run that reports BREAKING for one of them
# is still a spurious break the guard must catch.
_COMPATIBLE_EXPECTED = {"COMPATIBLE", "NO_CHANGE", "COMPATIBLE_WITH_RISK"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: check_stripped_fp.py <results.json> [label]", file=sys.stderr)
        return 2
    results_path = Path(argv[0])
    label = argv[1] if len(argv) > 1 else "stripped"
    if not results_path.exists():
        print(f"ERROR: {results_path} not found", file=sys.stderr)
        return 2

    gt = _load(GROUND_TRUTH)["verdicts"]
    data = _load(results_path)

    false_positives: list[str] = []
    downgrades: list[str] = []
    errors: list[str] = []
    for r in data.get("results", []):
        case = r.get("case_id") or r.get("name")
        got = (r.get("got") or "").upper()
        entry = gt.get(case, {})
        expected = (entry.get("expected") or "").upper()
        status = r.get("status")
        # SKIP is benign (tool/platform/feature unavailable). ERROR is NOT: the
        # validate run is invoked under `set +e`, so an ERROR row is the only
        # remaining signal that the reduced-evidence mode failed to produce a
        # verdict for a case. Ignoring it would let a crashed run pass the guard
        # green without ever checking the false-positive invariant — so treat
        # ERROR (and a missing verdict that is not a SKIP) as a guard failure.
        if status == "SKIP":
            continue
        if status == "ERROR" or not got:
            errors.append(f"{case}: status={status} ({r.get('message', '')[:120]})")
            continue
        if expected in _COMPATIBLE_EXPECTED and got == "BREAKING":
            false_positives.append(f"{case}: expected {expected} got {got}")
        elif expected == "BREAKING" and got in _COMPATIBLE_EXPECTED:
            downgrades.append(f"{case}: {expected}→{got} (evidence lost in {label} mode)")

    if downgrades:
        print(f"{label} downgrades (expected evidence loss, reported): {len(downgrades)}")
        for d in downgrades:
            print(f"  - {d}")

    failed = False
    if errors:
        print(f"\nERROR: {label} run did not produce a verdict for {len(errors)} case(s) "
              "(crash/compare failure — the FP invariant was never checked):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        failed = True
    if false_positives:
        print(f"\nERROR: {label} false positives: {len(false_positives)}", file=sys.stderr)
        for fp in false_positives:
            print(f"  - {fp}", file=sys.stderr)
        failed = True
    if failed:
        return 1
    print(f"\n{label} FP guard: no spurious breaks, no errored cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
