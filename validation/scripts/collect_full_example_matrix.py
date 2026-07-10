#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Aggregate full example validation into a per-case proof matrix.

The examples catalog intentionally spans different shapes: single-library
v1/v2 pairs, BTF fixtures, audit/cross-source snapshots, build/source-only
fixtures, and multi-library bundles.  A ``SKIP`` in one runner is acceptable
only when another runner owns that case and demonstrates the expected behavior.

This script turns runner outputs into one auditable matrix:

* ``COVERED`` means at least one owned lane demonstrated the expected behavior.
* ``UNRESOLVED`` means the case is known but no lane currently proves it.
* ``FAILED`` means a lane that should prove the case failed or errored.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_DIR / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"
SCHEMA_VERSION = "full_example_matrix.v1"

SPECIAL_PROOFS = {
    "btf": {
        "lane": "btf-fixture",
        "proof": "tests/test_workflow_kernel_accel.py::test_committed_btf_example_matches_ground_truth",
    },
    "g20": {
        "lane": "g20-crosscheck-fixtures",
        "proof": "tests/test_g20_catalog.py",
    },
    "l3l4l5": {
        "lane": "l3l4l5-fixtures",
        "proof": "tests/test_l3l4l5_examples.py",
    },
    "python_api": {
        "lane": "python-api-stub-pair",
        "proof": "tests/test_python_api_examples.py",
    },
    "reconcile": {
        "lane": "build-context-reconcile-fixture",
        "proof": "tests/test_diff_reconcile.py::test_case164_fixtures_reconcile",
    },
    "snapshot_pair": {
        "lane": "environment-drift-snapshot-pair",
        "proof": "tests/test_environment_drift.py::TestCase170Example",
    },
}


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: failed to load {path}: {exc}", file=sys.stderr)
        return None


def _results_by_case(data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not data:
        return {}
    return {
        str(r.get("case_id") or r.get("name") or r.get("case")): r
        for r in data.get("results", [])
    }


def _case_owner(name: str, entry: dict[str, Any]) -> str:
    if entry.get("skip") and "BTF" in str(entry.get("reason", "")):
        return "btf"
    if entry.get("mode") == "audit" or entry.get("expected_crosscheck_kinds"):
        return "g20"
    if entry.get("mode") == "reconcile":
        return "reconcile"
    if entry.get("mode") == "snapshot-pair":
        return "snapshot_pair"
    if "old.json" in (entry.get("fixtures") or []):
        return "l3l4l5"
    if entry.get("stub_pair"):
        return "python_api"
    if entry.get("bundle") is True or entry.get("category") == "bundle":
        return "bundle"
    return "single-library"


def _lane_record(label: str, result: dict[str, Any] | None) -> dict[str, Any]:
    if result is None:
        return {"lane": label, "status": "MISSING"}
    return {
        "lane": label,
        "status": result.get("status"),
        "expected": result.get("expected"),
        "got": result.get("got"),
        "message": result.get("message", ""),
    }


def _single_library_status(
    name: str,
    lanes: list[dict[str, Any]],
) -> tuple[str, str, str]:
    failed = [
        lane for lane in lanes if lane["status"] in {"FAIL", "ERROR", "BUILD_ERROR"}
    ]
    if failed:
        return "FAILED", failed[0]["lane"], failed[0].get("message", "")
    passes = [lane for lane in lanes if lane["status"] == "PASS"]
    if passes:
        return "COVERED", passes[0]["lane"], ""
    xfails = [lane for lane in lanes if lane["status"] == "XFAIL"]
    if xfails:
        reasons = "; ".join(
            f"{lane['lane']}: {lane.get('message', '')}" for lane in xfails
        )
        return "UNRESOLVED", "xfail", reasons
    return "UNRESOLVED", "none", f"{name}: no PASS lane"


def _special_status(owner: str, present: bool) -> tuple[str, str, str]:
    proof = SPECIAL_PROOFS[owner]
    if present:
        return "COVERED", proof["lane"], proof["proof"]
    return "UNRESOLVED", proof["lane"], f"proof not supplied: {proof['proof']}"


def build_matrix(
    *,
    gcc: dict[str, Any] | None,
    clang: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
    proof_g20: bool,
    proof_l3l4l5: bool,
    proof_btf: bool,
    proof_python_api: bool,
    proof_reconcile: bool = False,
    proof_snapshot_pair: bool = False,
) -> dict[str, Any]:
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]
    gcc_results = _results_by_case(gcc)
    clang_results = _results_by_case(clang)
    bundle_results = _results_by_case(bundle)
    runtime_results = _results_by_case(runtime)

    rows: list[dict[str, Any]] = []
    for name, entry in sorted(gt.items()):
        owner = _case_owner(name, entry)
        lanes = [
            _lane_record("gcc-debug-headers", gcc_results.get(name)),
            _lane_record("clang-debug-headers", clang_results.get(name)),
        ]
        runtime_lane = runtime_results.get(name)

        if owner == "single-library":
            status, proof_lane, note = _single_library_status(name, lanes)
        elif owner == "bundle":
            bundle_result = bundle_results.get(name)
            lanes.append(_lane_record("bundle-compare-release", bundle_result))
            if bundle_result is None:
                status, proof_lane, note = (
                    "UNRESOLVED",
                    "bundle-compare-release",
                    "bundle runner did not report this case",
                )
            elif bundle_result.get("status") == "PASS":
                status, proof_lane, note = "COVERED", "bundle-compare-release", ""
            elif bundle_result.get("status") in {"FAIL", "ERROR"}:
                status, proof_lane, note = (
                    "FAILED",
                    "bundle-compare-release",
                    bundle_result.get("message", ""),
                )
            else:
                status, proof_lane, note = (
                    "UNRESOLVED",
                    "bundle-compare-release",
                    bundle_result.get("message", ""),
                )
        elif owner == "btf":
            status, proof_lane, note = _special_status("btf", proof_btf)
        elif owner == "g20":
            status, proof_lane, note = _special_status("g20", proof_g20)
        elif owner == "l3l4l5":
            status, proof_lane, note = _special_status("l3l4l5", proof_l3l4l5)
        elif owner == "python_api":
            status, proof_lane, note = _special_status("python_api", proof_python_api)
        elif owner == "reconcile":
            status, proof_lane, note = _special_status("reconcile", proof_reconcile)
        elif owner == "snapshot_pair":
            status, proof_lane, note = _special_status(
                "snapshot_pair", proof_snapshot_pair
            )
        else:  # pragma: no cover - defensive future-proofing
            status, proof_lane, note = "UNRESOLVED", owner, "unknown owner"

        row = {
            "case_id": name,
            "owner": owner,
            "expected": entry.get("expected")
            or entry.get("expected_bundle_verdict")
            or entry.get("expected_crosscheck_kinds"),
            "min_evidence": entry.get("min_evidence"),
            "status": status,
            "proof_lane": proof_lane,
            "note": note,
            "lanes": lanes,
        }
        if runtime_lane is not None:
            row["runtime_smoke"] = {
                "status": runtime_lane.get("status"),
                "message": runtime_lane.get("message", ""),
            }
        rows.append(row)

    counts = Counter(row["status"] for row in rows)
    owners = Counter(row["owner"] for row in rows)
    unresolved = [row for row in rows if row["status"] == "UNRESOLVED"]
    failed = [row for row in rows if row["status"] == "FAILED"]
    return {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/collect_full_example_matrix.py",
        "ground_truth_cases": len(gt),
        "summary": dict(sorted(counts.items())),
        "owners": dict(sorted(owners.items())),
        "unresolved_cases": [row["case_id"] for row in unresolved],
        "failed_cases": [row["case_id"] for row in failed],
        "results": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gcc", type=Path, help="validate_examples gcc JSON")
    parser.add_argument("--clang", type=Path, help="validate_examples clang JSON")
    parser.add_argument("--bundle", type=Path, help="run_bundle_examples JSON")
    parser.add_argument("--runtime", type=Path, help="runtime smoke JSON")
    parser.add_argument("--proof-g20", action="store_true")
    parser.add_argument("--proof-l3l4l5", action="store_true")
    parser.add_argument("--proof-btf", action="store_true")
    parser.add_argument("--proof-python-api", action="store_true")
    parser.add_argument("--proof-reconcile", action="store_true")
    parser.add_argument("--proof-snapshot-pair", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="Return success even when unresolved cases remain.",
    )
    args = parser.parse_args(argv)

    matrix = build_matrix(
        gcc=_load_json(args.gcc),
        clang=_load_json(args.clang),
        bundle=_load_json(args.bundle),
        runtime=_load_json(args.runtime),
        proof_g20=args.proof_g20,
        proof_l3l4l5=args.proof_l3l4l5,
        proof_btf=args.proof_btf,
        proof_python_api=args.proof_python_api,
        proof_reconcile=args.proof_reconcile,
        proof_snapshot_pair=args.proof_snapshot_pair,
    )
    text = json.dumps(matrix, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print("Full example matrix:", json.dumps(matrix["summary"], sort_keys=True))
    if matrix["unresolved_cases"]:
        print("UNRESOLVED:", ", ".join(matrix["unresolved_cases"]), file=sys.stderr)
    if matrix["failed_cases"]:
        print("FAILED:", ", ".join(matrix["failed_cases"]), file=sys.stderr)
    if not args.out:
        print(text)

    if matrix["failed_cases"]:
        return 1
    if matrix["unresolved_cases"] and not args.allow_unresolved:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
