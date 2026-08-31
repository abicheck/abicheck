"""Migration-edge coverage for the ADR-061 aggregation package split."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from abicheck.workflows.aggregate.gate import scan_severity_gate_paths
from abicheck.workflows.aggregate.load import _load_report_file
from abicheck.workflows.aggregate.reconcile import resolve_report_change_identity


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


def test_report_entry_carries_no_entity_id_but_still_resolves() -> None:
    """``resolve_change_identity`` reads ``change.entity_id`` unconditionally
    for every non-batch-shaped finding (ADR-063 Phase 2's "resolve_change_identity
    consumes Change.entity_id" change) -- a report-derived ``_ReportChangeView``
    carries no such field, since ``_change_to_dict`` never serializes it. This
    must degrade gracefully (no ``entity:`` alias, since none was ever supplied)
    rather than raising ``AttributeError`` -- regression test for the field
    being entirely absent from ``_ReportChangeView``, which crashed every one
    of these report-derived lookups."""
    for entry in (
        {
            "kind": "func_removed",
            "symbol": "_ZN3lib3addEii",
            "description": "Function removed",
        },
        {
            "kind": "type_size_changed",
            "symbol": "Foo",
            "description": "size 8 -> 16",
            "old_value": "8",
            "new_value": "16",
        },
    ):
        identity = resolve_report_change_identity(dict(entry))
        assert identity.primary_id
        assert not any(a.startswith("entity:") for a in identity.aliases)


def test_batch_shaped_report_entry_never_needed_entity_id() -> None:
    """Negative control for the test above: a batch-shaped kind clears
    ``entity_id`` to ``None`` before ``change.entity_id`` would ever be read,
    so it was never exposed to the missing-field bug regardless of whether
    ``_ReportChangeView`` carried the attribute."""
    entry = {"kind": "visibility_leak", "symbol": "SomeType", "description": "d"}
    identity = resolve_report_change_identity(dict(entry))
    assert identity.primary_id
