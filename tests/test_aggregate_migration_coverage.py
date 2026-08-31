"""Migration-edge coverage for the ADR-061 aggregation package split."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from abicheck.workflows.aggregate.gate import scan_severity_gate_paths
from abicheck.workflows.aggregate.load import _load_report_file


def test_aggregate_findings_facade_executes_after_test_collection() -> None:
    """Exercise the facade itself, not only its already-imported objects."""
    module_name = "abicheck.aggregate_findings"
    previous = sys.modules.pop(module_name, None)
    try:
        facade = importlib.import_module(module_name)
        assert facade.ReportFinding.__module__.endswith("aggregate.reconcile")
        assert facade.FindingMatrixEntry.__module__.endswith("aggregate.matrix")
    finally:
        if previous is not None:
            sys.modules[module_name] = previous


def test_scan_severity_paths_reject_non_scan_envelope() -> None:
    assert scan_severity_gate_paths({"severity": {"exit_code": 0}}) == []


def test_scan_severity_paths_find_service_scan_envelope() -> None:
    payload = {"scan_schema_version": "1.9", "report": {"diff": {"severity": {}}}}
    assert scan_severity_gate_paths(payload) == [("report", "diff")]


@pytest.mark.parametrize("reason", [None, {"kind": 42}])
def test_null_verdict_without_structured_reason_is_not_not_comparable(
    tmp_path: Path, reason: object
) -> None:
    report = tmp_path / "abi-report-linux.json"
    report.write_text(json.dumps({"verdict": None, "reason": reason}), encoding="utf-8")

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.reason == "report carried no ABI verdict"


def test_not_comparable_report_preserves_declared_contract_coverage(
    tmp_path: Path,
) -> None:
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": None,
                "reason": {"kind": "scope_mismatch"},
                "contract_coverage_exit_contribution": 0,
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.contract_coverage_declared


@pytest.mark.parametrize(
    ("verdict", "report_exit_code", "category"),
    [
        ("BUDGET_OVERFLOW", 5, "budget_overflow"),
        ("EVIDENCE_CONTRACT_ERROR", 1, "evidence_contract_error"),
    ],
)
def test_scan_abort_verdicts_force_a_blocking_gate(
    tmp_path: Path, verdict: str, report_exit_code: int, category: str
) -> None:
    """`scan`'s own two abort verdicts aren't `Verdict` members, so without
    dedicated handling `_load_report_file` never reaches `GateInfo.from_
    scan_report` for them (it only calls that after `parse_report_verdict`
    succeeds) -- the abort would read as an unavailable/verdictless report
    a required-target policy could silently tolerate, instead of the real
    failure it is (Codex review, fresh evidence).

    The gate's own `exit_code` is always `1` (`COVERAGE_INCOMPLETE_EXIT`),
    never *report_exit_code* itself -- `GateInfo.from_scan_report` already
    normalizes every scan exit outside {0, 2, 4} to that value, and the
    aggregate's own published contract has no exit 5 (Codex review, fresh
    evidence: an earlier revision leaked scan's raw budget-overflow code
    straight into the aggregate result).
    """
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": verdict,
                "exit_code": report_exit_code,
                "diff": {"exit": {"code": report_exit_code, "reasons": [category]}},
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    from abicheck.change_registry_types import Verdict

    assert loaded.verdict is Verdict.BREAKING
    assert loaded.reason is None
    assert loaded.gate is not None
    assert loaded.gate.blocking is True
    assert loaded.gate.exit_code == 1
    assert loaded.gate.blocking_categories == (category,)
