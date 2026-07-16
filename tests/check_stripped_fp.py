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
from typing import Any

REPO_DIR = Path(__file__).parent.parent
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"

# Verdicts the ground truth may declare as "not a real ABI break".
# COMPATIBLE_WITH_RISK is included: the runtime-model-flip cases (case130–133)
# are risk-only, so a reduced-evidence run that reports BREAKING for one of them
# is still a spurious break the guard must catch.
_COMPATIBLE_EXPECTED = {"COMPATIBLE", "NO_CHANGE", "COMPATIBLE_WITH_RISK"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _toolchain_family(name: str | None) -> str:
    """Producer family ("gcc"|"clang"|"") from a compiler path/name string."""
    name = (name or "").lower()
    if "clang" in name:
        return "clang"
    if "gcc" in name or "g++" in name:
        return "gcc"
    return ""


def _case_is_cpp(case: str) -> bool:
    """Best-effort C vs C++ detection: does the case dir carry a .cpp/.hpp?

    Filesystem-only (no compiler invocation) — mirrors the same v1/v2/old-new/
    good-bad/libfoo naming conventions test_example_autodiscovery.py's layout
    detectors use, without needing to import that module here.
    """
    case_dir = REPO_DIR / "examples" / case
    if not case_dir.is_dir():
        return False
    return any(case_dir.rglob("*.cpp")) or any(case_dir.rglob("*.hpp"))


def _gap_applies(
    entry: dict[str, Any], case: str, platform: str, variant: str, data: dict[str, Any]
) -> bool:
    """Whether entry's known_gap is scoped to apply to this specific row.

    Mirrors tests/validate_examples.py::_gap_applies exactly (toolchain/
    platform/variant scoping) so a gap that's only documented for one
    producer/platform/variant doesn't blanket-exempt a real regression on a
    different one -- absent scoping fields apply everywhere (back-compat).
    """
    toolchains = entry.get("known_gap_toolchains")
    if toolchains:
        is_cpp = _case_is_cpp(case)
        compiler = data.get("compiler_cxx") if is_cpp else data.get("compiler_c")
        if _toolchain_family(compiler) not in toolchains:
            return False
    platforms = entry.get("known_gap_platforms")
    if platforms and platform not in platforms:
        return False
    variants = entry.get("known_gap_variants")
    if variants and variant not in variants:
        return False
    return True


def _classify_results(
    rows: list[dict[str, Any]], gt: dict[str, Any], label: str, data: dict[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    """Split result rows into (false_positives, downgrades, errors) messages."""
    false_positives: list[str] = []
    downgrades: list[str] = []
    errors: list[str] = []
    for r in rows:
        case = str(r.get("case_id") or r.get("name") or "")
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
        # A case with a known_gap already reports BREAKING under *full* debug
        # evidence too (validate_examples.py/test_example_autodiscovery.py
        # XFAIL it there) -- this invariant only guards against a *reduced*
        # mode manufacturing a break full evidence doesn't already show, so a
        # known_gap case's BREAKING here isn't something this mode invented.
        # Only exempt it when the gap is actually scoped to apply to this row
        # (toolchain/platform/variant) -- a gap documented for e.g. macOS/clang
        # only must not mask a genuine Linux/gcc regression on the same case.
        platform = r.get("platform") or data.get("platform", "")
        # _result_to_json (validate_examples.py) writes the artifact variant to
        # both "mode" and "variant" (mode is the JSON artifact schema's
        # canonical field name) -- prefer "mode" so a results file that only
        # carries one of the two still resolves correctly, only falling back
        # to the CLI label when a row has neither (Codex review).
        variant = r.get("mode") or r.get("variant") or label
        if (
            entry.get("known_gap")
            and _gap_applies(entry, case, platform, variant, data)
            and expected in _COMPATIBLE_EXPECTED
            and got == "BREAKING"
        ):
            downgrades.append(
                f"{case}: {expected}→{got} (known_gap, not evidence loss in {label} mode)"
            )
            continue
        if expected in _COMPATIBLE_EXPECTED and got == "BREAKING":
            false_positives.append(f"{case}: expected {expected} got {got}")
        elif expected == "BREAKING" and got in _COMPATIBLE_EXPECTED:
            downgrades.append(f"{case}: {expected}→{got} (evidence lost in {label} mode)")
    return false_positives, downgrades, errors


def _report(
    label: str, false_positives: list[str], downgrades: list[str], errors: list[str]
) -> int:
    """Print the guard report and return the process exit code."""
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
    false_positives, downgrades, errors = _classify_results(
        data.get("results", []), gt, label, data
    )
    return _report(label, false_positives, downgrades, errors)


if __name__ == "__main__":
    raise SystemExit(main())
