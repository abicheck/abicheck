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
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_DIR / "examples"
GROUND_TRUTH = EXAMPLES_DIR / "ground_truth.json"
SCHEMA_VERSION = "full_example_matrix.v2"

ARTIFACT_CONTRACTS = {
    "gcc": ("tests/validate_examples.py", "validate_examples.v2"),
    "clang": ("tests/validate_examples.py", "validate_examples.v2"),
    "build_source": ("tests/validate_examples.py", "validate_examples.v2"),
    "runtime": (
        "validation/scripts/run_example_runtime_smoke.py",
        "example_runtime_smoke.v1",
    ),
    "bundle": (
        "validation/scripts/run_bundle_examples.py",
        "bundle_examples.v1",
    ),
    "special_cli": (
        "validation/scripts/run_special_cli_examples.py",
        "special_cli_examples.v2",
    ),
}
BUILD_SOURCE_PROOF_CASES = {
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
}
PROOF_ARTIFACT_RUNNER = "validation/scripts/run_example_owner_proofs.py"
PROOF_ARTIFACT_SCHEMA = "example_owner_proofs.v1"

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
    "kabi": {
        "lane": "kabi-symvers-fixtures",
        "proof": "tests/test_kabi_examples.py",
    },
}


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: failed to load {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict):
        print(
            f"warning: {path} top-level JSON is {type(payload).__name__}, "
            "expected an object",
            file=sys.stderr,
        )
        return None
    return payload


def _results_by_case(data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not data:
        return {}
    results = data.get("results", [])
    if not isinstance(results, list):
        return {}
    return {
        str(r.get("case_id") or r.get("name") or r.get("case")): r
        for r in results
        if isinstance(r, dict)
    }


def _ground_truth_digest() -> str:
    return hashlib.sha256(GROUND_TRUTH.read_bytes()).hexdigest()


def _artifact_errors(
    label: str,
    data: dict[str, Any] | None,
    *,
    expected_cases: set[str],
) -> list[str]:
    """Return contract violations for one required full-matrix artifact."""
    if data is None:
        return [f"{label}: artifact is missing, unreadable, or malformed"]

    errors: list[str] = []
    expected_runner, expected_schema = ARTIFACT_CONTRACTS[label]
    if data.get("runner") != expected_runner:
        errors.append(
            f"{label}: runner={data.get('runner')!r}, expected {expected_runner!r}"
        )
    if data.get("schema_version") != expected_schema:
        errors.append(
            f"{label}: schema_version={data.get('schema_version')!r}, "
            f"expected {expected_schema!r}"
        )
    if data.get("ground_truth_sha256") != _ground_truth_digest():
        errors.append(f"{label}: ground_truth_sha256 does not match this checkout")
    expected_ground_truth_cases = (
        len(json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"])
        if label == "build_source"
        else len(expected_cases)
    )
    if data.get("ground_truth_cases") != expected_ground_truth_cases:
        errors.append(
            f"{label}: ground_truth_cases={data.get('ground_truth_cases')!r}, "
            f"expected {expected_ground_truth_cases}"
        )

    results = data.get("results")
    if not isinstance(results, list):
        errors.append(f"{label}: results must be a list")
        return errors

    case_ids = [
        str(row.get("case_id") or row.get("name") or row.get("case"))
        for row in results
        if isinstance(row, dict)
    ]
    counts = Counter(case_ids)
    duplicates = sorted(case_id for case_id, count in counts.items() if count > 1)
    actual_cases = set(case_ids)
    missing = sorted(expected_cases - actual_cases)
    unexpected = sorted(actual_cases - expected_cases)
    if len(case_ids) != len(results):
        errors.append(f"{label}: every result must be an object with a case id")
    if duplicates:
        errors.append(f"{label}: duplicate case ids: {', '.join(duplicates)}")
    if missing:
        errors.append(f"{label}: missing case ids: {', '.join(missing)}")
    if unexpected:
        errors.append(f"{label}: unexpected case ids: {', '.join(unexpected)}")
    if data.get("selected_cases") != len(expected_cases):
        errors.append(
            f"{label}: selected_cases={data.get('selected_cases')!r}, "
            f"expected {len(expected_cases)}"
        )

    if label in {"gcc", "clang"}:
        if data.get("toolchain") != label:
            errors.append(
                f"{label}: toolchain={data.get('toolchain')!r}, expected {label!r}"
            )
        if data.get("artifact_variants") != ["debug-headers"]:
            errors.append(
                f"{label}: artifact_variants={data.get('artifact_variants')!r}, "
                "expected ['debug-headers']"
            )
        allowed_statuses = {"PASS", "FAIL", "XFAIL", "SKIP", "ERROR"}
        bad_statuses = {"FAIL", "ERROR", "BUILD_ERROR"}
    elif label == "build_source":
        if data.get("artifact_variants") != ["build-source"]:
            errors.append(
                "build_source: artifact_variants="
                f"{data.get('artifact_variants')!r}, expected ['build-source']"
            )
        allowed_statuses = {"PASS", "FAIL", "XFAIL", "SKIP", "ERROR"}
        bad_statuses = {"FAIL", "ERROR", "BUILD_ERROR"}
    elif label == "runtime":
        if data.get("build_type") != "Debug":
            errors.append(
                f"runtime: build_type={data.get('build_type')!r}, expected 'Debug'"
            )
        allowed_statuses = {
            "DEMONSTRATED",
            "NO_RUNTIME_SIGNAL",
            "BASELINE_SIGNAL",
            "SKIP",
            "BUILD_ERROR",
        }
        bad_statuses = {"BUILD_ERROR"}
    else:
        if data.get("platform") != "linux":
            errors.append(
                f"{label}: platform={data.get('platform')!r}, expected 'linux'"
            )
        allowed_statuses = {"PASS", "FAIL", "ERROR"}
        bad_statuses = {"FAIL", "ERROR"}
    unknown_statuses = sorted(
        {
            str(row.get("status"))
            for row in results
            if isinstance(row, dict) and row.get("status") not in allowed_statuses
        }
    )
    if unknown_statuses:
        errors.append(f"{label}: unknown statuses: {', '.join(unknown_statuses)}")

    # ``summary`` in the gcc/clang artifacts mixes status counts (PASS/FAIL/...)
    # with diagnostic counters (KINDS_MISMATCH, CATEGORY_COLLAPSED) that
    # validate_examples.py adds alongside them (see _summary_counts). Those
    # counters are not row statuses, so they must be validated against their
    # own per-row signals, not folded into the status-count recomputation —
    # otherwise every artifact with a diagnostic counter fails this check.
    actual_summary = dict(
        Counter(
            str(row.get("status"))
            for row in results
            if isinstance(row, dict) and row.get("status") is not None
        )
    )
    declared_summary = data.get("summary") or {}
    declared_status_summary = (
        {k: v for k, v in declared_summary.items() if k in allowed_statuses}
        if isinstance(declared_summary, dict)
        else declared_summary
    )
    if declared_status_summary != actual_summary:
        errors.append(
            f"{label}: summary={data.get('summary')!r}, recomputed {actual_summary!r}"
        )
    if isinstance(declared_summary, dict):
        declared_diagnostics = {
            k: v for k, v in declared_summary.items() if k not in allowed_statuses
        }
        actual_diagnostics = {}
        kinds_mismatch = sum(
            1
            for row in results
            if isinstance(row, dict) and row.get("kinds_strict") == "mismatch"
        )
        if kinds_mismatch:
            actual_diagnostics["KINDS_MISMATCH"] = kinds_mismatch
        category_collapsed = sum(
            1
            for row in results
            if isinstance(row, dict) and row.get("category_strict") == "collapsed"
        )
        if category_collapsed:
            actual_diagnostics["CATEGORY_COLLAPSED"] = category_collapsed
        if declared_diagnostics != actual_diagnostics:
            errors.append(
                f"{label}: diagnostic summary={declared_diagnostics!r}, "
                f"recomputed {actual_diagnostics!r}"
            )
    bad_cases = sorted(
        str(row.get("case_id") or row.get("name") or row.get("case"))
        for row in results
        if isinstance(row, dict) and row.get("status") in bad_statuses
    )
    if bad_cases:
        errors.append(f"{label}: failing runner statuses for: {', '.join(bad_cases)}")
    return errors


def _proof_artifact_errors(data: dict[str, Any] | None) -> list[str]:
    """Return contract violations for the dedicated-owner proof artifact."""
    if data is None:
        return ["proofs: artifact is missing, unreadable, or malformed"]

    errors: list[str] = []
    if data.get("runner") != PROOF_ARTIFACT_RUNNER:
        errors.append(
            f"proofs: runner={data.get('runner')!r}, expected {PROOF_ARTIFACT_RUNNER!r}"
        )
    if data.get("schema_version") != PROOF_ARTIFACT_SCHEMA:
        errors.append(
            f"proofs: schema_version={data.get('schema_version')!r}, "
            f"expected {PROOF_ARTIFACT_SCHEMA!r}"
        )
    if data.get("ground_truth_sha256") != _ground_truth_digest():
        errors.append("proofs: ground_truth_sha256 does not match this checkout")

    results = data.get("results")
    if not isinstance(results, list):
        errors.append("proofs: results must be a list")
        return errors
    owners = [str(row.get("owner")) for row in results if isinstance(row, dict)]
    counts = Counter(owners)
    expected_owners = set(SPECIAL_PROOFS)
    actual_owners = set(owners)
    duplicates = sorted(owner for owner, count in counts.items() if count > 1)
    missing = sorted(expected_owners - actual_owners)
    unexpected = sorted(actual_owners - expected_owners)
    if len(owners) != len(results):
        errors.append("proofs: every result must be an object with an owner")
    if duplicates:
        errors.append(f"proofs: duplicate owners: {', '.join(duplicates)}")
    if missing:
        errors.append(f"proofs: missing owners: {', '.join(missing)}")
    if unexpected:
        errors.append(f"proofs: unexpected owners: {', '.join(unexpected)}")
    if data.get("selected_owners") != len(expected_owners):
        errors.append(
            f"proofs: selected_owners={data.get('selected_owners')!r}, "
            f"expected {len(expected_owners)}"
        )

    unknown_statuses = sorted(
        {
            str(row.get("status"))
            for row in results
            if isinstance(row, dict) and row.get("status") not in {"PASS", "FAIL"}
        }
    )
    if unknown_statuses:
        errors.append(f"proofs: unknown statuses: {', '.join(unknown_statuses)}")
    failed = sorted(
        str(row.get("owner"))
        for row in results
        if isinstance(row, dict)
        and (row.get("status") != "PASS" or row.get("returncode") != 0)
    )
    if failed:
        errors.append(f"proofs: failing owners: {', '.join(failed)}")
    actual_summary = dict(
        Counter(
            str(row.get("status"))
            for row in results
            if isinstance(row, dict) and row.get("status") is not None
        )
    )
    if data.get("summary") != actual_summary:
        errors.append(
            f"proofs: summary={data.get('summary')!r}, recomputed {actual_summary!r}"
        )
    return errors


def _case_owner(name: str, entry: dict[str, Any]) -> str:
    if entry.get("skip") and "BTF" in str(entry.get("reason", "")):
        return "btf"
    if entry.get("mode") == "audit":
        return "g20"
    if entry.get("mode") == "reconcile":
        return "reconcile"
    if entry.get("mode") == "snapshot-pair":
        return "snapshot_pair"
    if "old.json" in (entry.get("fixtures") or []):
        return "l3l4l5"
    if set(entry.get("fixtures") or []) == {"v1.symvers", "v2.symvers"}:
        return "kabi"
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
        "kinds_strict": result.get("kinds_strict"),
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
        # No lane passed and none unexpectedly failed: every lane that ran
        # reached the case's documented, reviewed known_gap. XFAIL is only
        # reachable when ground_truth.json carries an actual known_gap
        # explanation (see _evaluate_verdict), so this is a fully accounted-
        # for state — every lane's behavior is understood — not an unproven
        # one. Some cases (e.g. case111) have no evidence tier that currently
        # reaches the canonical verdict at all; that is a real, tracked
        # detector gap, not a reason to leave the case perpetually
        # UNRESOLVED. Distinguish it from an ordinary PASS via the
        # "known-gap-xfail" proof_lane so coverage-by-provenance stays honest
        # about which cases were proven by a lane vs. by a reviewed gap.
        return "COVERED", "known-gap-xfail", reasons
    return "UNRESOLVED", "none", f"{name}: no PASS lane"


def _special_cli_status(
    result: dict[str, Any] | None,
) -> tuple[str, str, str]:
    lane = "special-abicheck-cli"
    if result is None:
        return "UNRESOLVED", lane, "special CLI runner did not report this case"
    if result.get("status") == "PASS":
        return "COVERED", lane, ""
    if result.get("status") in {"FAIL", "ERROR"}:
        return "FAILED", lane, str(result.get("message", ""))
    return "UNRESOLVED", lane, str(result.get("message", ""))


def build_matrix(
    *,
    gcc: dict[str, Any] | None,
    clang: dict[str, Any] | None,
    bundle: dict[str, Any] | None,
    special_cli: dict[str, Any] | None,
    runtime: dict[str, Any] | None,
    build_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]
    gcc_results = _results_by_case(gcc)
    clang_results = _results_by_case(clang)
    bundle_results = _results_by_case(bundle)
    special_cli_results = _results_by_case(special_cli)
    runtime_results = _results_by_case(runtime)
    build_source_results = _results_by_case(build_source)

    rows: list[dict[str, Any]] = []
    for name, entry in sorted(gt.items()):
        owner = _case_owner(name, entry)
        lanes = [
            _lane_record("gcc-debug-headers", gcc_results.get(name)),
            _lane_record("clang-debug-headers", clang_results.get(name)),
            _lane_record("build-source", build_source_results.get(name)),
        ]
        runtime_lane = runtime_results.get(name)

        if owner == "single-library":
            status, proof_lane, note = _single_library_status(name, lanes)
            if proof_lane == "build-source":
                provenance = "abicheck-cli-workflow"
            elif proof_lane == "known-gap-xfail":
                # Proven by the case's own reviewed known_gap + source_smoke
                # oracle, not by any evidence tier reaching the canonical
                # verdict — deliberately excluded from direct_coverage below.
                provenance = "known-gap-oracle"
            else:
                provenance = "compiler"
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
            provenance = "abicheck-cli-workflow"
        elif owner in SPECIAL_PROOFS:
            special_result = special_cli_results.get(name)
            lanes.append(_lane_record("special-abicheck-cli", special_result))
            status, proof_lane, note = _special_cli_status(special_result)
            provenance = "abicheck-cli-workflow"
        else:  # pragma: no cover - defensive future-proofing
            status, proof_lane, note = "UNRESOLVED", owner, "unknown owner"
            provenance = "unknown"

        proof_lane_record = next(
            (lane for lane in lanes if lane["lane"] == proof_lane), None
        )
        row = {
            "case_id": name,
            "owner": owner,
            "expected": entry.get("expected"),
            "min_evidence": entry.get("min_evidence"),
            "status": status,
            "proof_lane": proof_lane,
            "provenance": provenance,
            "note": note,
            # Coverage-by-verdict alone can hide a wrong-detector-kind pass
            # (right severity, unrelated ChangeKind). Surface the winning
            # lane's strict-kinds signal so "COVERED" isn't read as "fully
            # calibrated" when kinds_strict == "mismatch".
            "kinds_strict": (proof_lane_record or {}).get("kinds_strict"),
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
    covered_provenance = Counter(
        row["provenance"] for row in rows if row["status"] == "COVERED"
    )
    direct_covered = sum(
        covered_provenance.get(key, 0) for key in ("compiler", "abicheck-cli-workflow")
    )
    unresolved = [row for row in rows if row["status"] == "UNRESOLVED"]
    failed = [row for row in rows if row["status"] == "FAILED"]
    kind_mismatch_cases = sorted(
        row["case_id"] for row in rows if row.get("kinds_strict") == "mismatch"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "runner": "validation/scripts/collect_full_example_matrix.py",
        "ground_truth_sha256": _ground_truth_digest(),
        "ground_truth_cases": len(gt),
        "summary": dict(sorted(counts.items())),
        "owners": dict(sorted(owners.items())),
        "coverage_by_provenance": dict(sorted(covered_provenance.items())),
        "direct_coverage": {
            "covered": direct_covered,
            "total": len(gt),
            "percent": round(100 * direct_covered / len(gt), 1),
        },
        "unresolved_cases": [row["case_id"] for row in unresolved],
        "failed_cases": [row["case_id"] for row in failed],
        # Non-blocking by design (see docs/development/examples-validation-runbook.md):
        # a case here still counts as COVERED, but its winning lane matched
        # the verdict without producing the calibrated ChangeKind. Triage
        # target for known_detector_gap entries.
        "kind_mismatch_cases": kind_mismatch_cases,
        "results": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gcc", type=Path, required=True, help="validate_examples gcc JSON"
    )
    parser.add_argument(
        "--clang", type=Path, required=True, help="validate_examples clang JSON"
    )
    parser.add_argument(
        "--bundle", type=Path, required=True, help="run_bundle_examples JSON"
    )
    parser.add_argument(
        "--special-cli",
        type=Path,
        required=True,
        help="run_special_cli_examples JSON",
    )
    parser.add_argument(
        "--runtime", type=Path, required=True, help="runtime smoke JSON"
    )
    parser.add_argument(
        "--build-source",
        type=Path,
        required=True,
        help="validate_examples build-source proof JSON",
    )
    parser.add_argument(
        "--proofs", type=Path, required=True, help="dedicated-owner proof JSON"
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--allow-unresolved",
        action="store_true",
        help="Return success even when unresolved cases remain.",
    )
    args = parser.parse_args(argv)

    gcc = _load_json(args.gcc)
    clang = _load_json(args.clang)
    bundle = _load_json(args.bundle)
    special_cli = _load_json(args.special_cli)
    runtime = _load_json(args.runtime)
    build_source = _load_json(args.build_source)
    proofs = _load_json(args.proofs)
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]
    all_cases = set(ground_truth)
    bundle_cases = {
        name
        for name, entry in ground_truth.items()
        if _case_owner(name, entry) == "bundle"
    }
    special_cli_cases = {
        name
        for name, entry in ground_truth.items()
        if _case_owner(name, entry) not in {"single-library", "bundle"}
    }
    artifact_errors = [
        error
        for label, data, expected_cases in (
            ("gcc", gcc, all_cases),
            ("clang", clang, all_cases),
            ("runtime", runtime, all_cases),
            ("build_source", build_source, BUILD_SOURCE_PROOF_CASES),
            ("bundle", bundle, bundle_cases),
            ("special_cli", special_cli, special_cli_cases),
        )
        for error in _artifact_errors(label, data, expected_cases=expected_cases)
    ]
    artifact_errors.extend(_proof_artifact_errors(proofs))
    matrix = build_matrix(
        gcc=gcc,
        clang=clang,
        bundle=bundle,
        special_cli=special_cli,
        runtime=runtime,
        build_source=build_source,
    )
    matrix["artifact_errors"] = artifact_errors
    text = json.dumps(matrix, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print("Full example matrix:", json.dumps(matrix["summary"], sort_keys=True))
    if matrix["unresolved_cases"]:
        print("UNRESOLVED:", ", ".join(matrix["unresolved_cases"]), file=sys.stderr)
    if matrix["failed_cases"]:
        print("FAILED:", ", ".join(matrix["failed_cases"]), file=sys.stderr)
    for error in artifact_errors:
        print("ARTIFACT ERROR:", error, file=sys.stderr)
    if not args.out:
        print(text)

    if matrix["failed_cases"] or artifact_errors:
        return 1
    if matrix["unresolved_cases"] and not args.allow_unresolved:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
