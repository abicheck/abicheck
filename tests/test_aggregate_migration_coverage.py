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

    `verdict` stays `None` (unavailable), never a synthetic `BREAKING`: a
    scan that aborted before comparing never produced an ABI-break finding,
    so a compatibility verdict/analyzed-target count must not be invented for
    it (Codex review, fresh evidence: an earlier revision reported
    `compatibility.verdict: "BREAKING"` and a complete analyzed-target count
    for a comparison that never ran) -- the gate still counts toward the CI
    decision via `AggregateResult._forced_gate_targets` instead.
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

    assert loaded.verdict is None
    assert loaded.reason == f"scan aborted before completing a comparison ({category})"
    assert loaded.gate is not None
    assert loaded.gate.blocking is True
    assert loaded.gate.exit_code == 1
    assert loaded.gate.blocking_categories == (category,)


@pytest.mark.parametrize("prior_contribution", [2, 4])
def test_late_budget_overflow_preserves_a_real_prior_break_in_the_gate_exit_code(
    tmp_path: Path, prior_contribution: int
) -> None:
    """A *late* `_BudgetOverflow` (`attach_prior_on_budget_overflow`) carries
    the ordinary compatibility contribution already computed before the
    abort fired through into `diff.exit`'s own `compatibility_contribution`
    field, even though `code` itself is always the dominant budget-overflow
    code (5) by `ExitDecision`'s own design. Downgrading that preserved
    contribution to a bare `COVERAGE_INCOMPLETE_EXIT` (1) would hide a real
    ABI/API break already found before the abort from a severity-aware
    aggregate consumer (Codex review, fresh evidence).
    """
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "diff": {
                    "exit": {
                        "code": 5,
                        "reasons": ["budget_overflow"],
                        "budget_overflow_contribution": 5,
                        "compatibility_contribution": prior_contribution,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == prior_contribution
    assert loaded.gate.blocking is True
    assert loaded.gate.blocking_categories == ("budget_overflow",)


def test_early_budget_overflow_with_no_prior_contribution_stays_at_the_coverage_floor(
    tmp_path: Path,
) -> None:
    """An early abort (no baseline compare ran yet, so every PR-G1
    contribution is genuinely `0`) must not be inflated -- the floor stays
    `COVERAGE_INCOMPLETE_EXIT` (1), matching the pre-existing behaviour this
    fix must not regress."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "diff": {
                    "exit": {
                        "code": 5,
                        "reasons": ["budget_overflow"],
                        "budget_overflow_contribution": 5,
                        "compatibility_contribution": 0,
                        "contract_coverage_contribution": 0,
                        "analysis_assurance_contribution": 0,
                        "crosscheck_promotion_contribution": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.gate is not None
    assert loaded.gate.exit_code == 1


def test_malformed_prior_contribution_is_ignored_not_trusted(tmp_path: Path) -> None:
    """A `compatibility_contribution` outside the aggregate's own valid gate
    scheme ({0, 1, 2, 4}) -- a corrupt or hand-edited report -- must not be
    passed straight through; fail closed to the coverage floor instead of
    manufacturing an exit code the aggregate's own contract doesn't allow."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "diff": {
                    "exit": {
                        "code": 5,
                        "reasons": ["budget_overflow"],
                        "compatibility_contribution": 3,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.gate is not None
    assert loaded.gate.exit_code == 1


def test_late_budget_overflow_preserves_orthogonal_axes_separately(
    tmp_path: Path,
) -> None:
    """A late abort's preserved `contract_coverage_contribution`/
    `analysis_assurance_contribution` must reach `_LoadedReport`'s own
    orthogonal `contract_coverage_exit`/`analysis_assurance_exit` fields,
    not just the folded gate `exit_code` -- `AggregateResult.
    contract_coverage_exit`/`.analysis_assurance_exit` (and their own
    `..._targets` lists) read *these* fields, never the gate, so folding
    the preserved axes only into the gate left both reading `0` with an
    empty target list for a report that genuinely declared `1` on each
    (Codex review, fresh evidence).
    """
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "diff": {
                    "exit": {
                        "code": 5,
                        "reasons": ["budget_overflow"],
                        "budget_overflow_contribution": 5,
                        "contract_coverage_contribution": 1,
                        "analysis_assurance_contribution": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.contract_coverage_exit == 1
    assert loaded.contract_coverage_incomplete is True
    assert loaded.contract_coverage_declared is True
    assert loaded.analysis_assurance_exit == 1


def test_late_budget_overflow_orthogonal_axes_default_to_zero_when_absent(
    tmp_path: Path,
) -> None:
    """An early abort (no prior decision at all) must not fabricate a
    declared contract-coverage/analysis-assurance contribution -- both
    axes stay at their honest `0`/undeclared defaults, matching the
    pre-existing behaviour this fix must not regress."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "diff": {"exit": {"code": 5, "reasons": ["budget_overflow"]}},
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.contract_coverage_exit == 0
    assert loaded.contract_coverage_incomplete is False
    assert loaded.contract_coverage_declared is False
    assert loaded.analysis_assurance_exit == 0


def test_artifact_set_budget_overflow_preserves_a_member_s_prior_break(
    tmp_path: Path,
) -> None:
    """A `scan --artifact-set` abort report (`ScanSetResult.to_dict()`) has
    no `diff` key at all -- its own top-level `verdict` reads
    `"BUDGET_OVERFLOW"` whenever any member overflows
    (`_aggregate_scan_set_verdict`), but each member's own preserved
    decision nests at `per_artifact[i].report.exit` instead
    (`ScanArtifactResult.to_dict()` wrapping the typed API's `ScanResult.
    report` envelope) -- a different shape than the single-binary `scan`
    CLI's `diff.exit`. Reading only the single-binary shape silently
    dropped every member's preserved contribution for a set-level abort
    (Codex review, fresh evidence).
    """
    report = tmp_path / "abi-report-bundle.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "per_artifact": [
                    {
                        "artifact": "libclean.so",
                        "scan_schema_version": "1.23",
                        "verdict": "COMPATIBLE",
                        "exit_code": 0,
                        "findings": 0,
                        "layers": [],
                        "confidence": {},
                        "estimate": [],
                        "report": {},
                    },
                    {
                        "artifact": "libbroken.so",
                        "scan_schema_version": "1.23",
                        "verdict": "BUDGET_OVERFLOW",
                        "exit_code": 5,
                        "findings": 0,
                        "layers": [],
                        "confidence": {},
                        "estimate": [],
                        "report": {
                            "scan_schema_version": "1.23",
                            "exit": {
                                "code": 5,
                                "reasons": ["budget_overflow"],
                                "budget_overflow_contribution": 5,
                                "compatibility_contribution": 4,
                                "contract_coverage_contribution": 1,
                                "analysis_assurance_contribution": 1,
                            },
                        },
                    },
                ],
                "bundle_findings": [],
                "bundle_finding_count": 0,
                "bundle_verdict": None,
                "bundle_incomplete": False,
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 4
    assert loaded.contract_coverage_exit == 1
    assert loaded.contract_coverage_declared is True
    assert loaded.analysis_assurance_exit == 1


def test_typed_api_root_report_exit_preserves_a_real_prior_break(
    tmp_path: Path,
) -> None:
    """A caller of the typed Python API (`abicheck.service.run_scan`) that
    dumps `ScanResult.to_dict()` straight to disk, rather than going
    through the native CLI, gets a report with no `diff` key at all -- the
    preserved decision for a late abort nests at the document *root*'s own
    `report.exit` (`ScanResult.report`), a third shape distinct from both
    the CLI's `diff.exit` and an artifact-set member's `per_artifact[i].
    report.exit` (Codex review, fresh evidence).
    """
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "findings": 0,
                "layers": [],
                "confidence": {},
                "estimate": [],
                "report": {
                    "scan_schema_version": "1.23",
                    "exit": {
                        "code": 5,
                        "reasons": ["budget_overflow"],
                        "budget_overflow_contribution": 5,
                        "compatibility_contribution": 4,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 4


def test_artifact_set_completed_member_exit_code_counts_without_a_report_exit(
    tmp_path: Path,
) -> None:
    """A set-level abort that fires *after* every member already finished
    normally (e.g. the shared budget expires during the post-member bundle
    audit, `service_scan.run_scan_set`) preserves `per_artifact` with real,
    completed member results -- but a completed member's own `ScanResult.
    report` is `{}` (no nested `exit` block at all, since it never
    aborted): the real result lives only in that member's bare top-level
    `exit_code`. Must still be picked up (Codex review, fresh evidence:
    `per_artifact` is not discarded on this exact set-level overflow path,
    per `run_scan_set`'s own `per_artifact=per_artifact` on this branch).
    """
    report = tmp_path / "abi-report-bundle.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "per_artifact": [
                    {
                        "artifact": "libclean.so",
                        "scan_schema_version": "1.23",
                        "verdict": "COMPATIBLE",
                        "exit_code": 0,
                        "findings": 0,
                        "layers": [],
                        "confidence": {},
                        "estimate": [],
                        "report": {},
                    },
                    {
                        "artifact": "libapibreak.so",
                        "scan_schema_version": "1.23",
                        "verdict": "API_BREAK",
                        "exit_code": 2,
                        "findings": 3,
                        "layers": [],
                        "confidence": {},
                        "estimate": [],
                        "report": {},
                    },
                ],
                "bundle_findings": [],
                "bundle_finding_count": 0,
                "bundle_verdict": None,
                "bundle_incomplete": False,
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 2


def test_artifact_set_evidence_contract_error_member_alongside_a_real_break_still_gates_its_own_category(
    tmp_path: Path,
) -> None:
    """`_aggregate_scan_set_verdict` (ADR-056 D3, service_scan.py) deliberately
    keeps a stronger real `API_BREAK`/`BREAKING` verdict at a `scan
    --artifact-set` set's own root even when one member aborted with
    `EVIDENCE_CONTRACT_ERROR` alongside it -- a real break must not be
    hidden behind an evidence-completeness verdict. But that means the
    root-level `verdict` string this loader checks first no longer names
    the aborted member's category at all, so before this fix the target's
    gate silently dropped "evidence_contract_error" despite that member
    never completing a comparison (Codex review, fresh evidence). The
    real severity (exit 2/4) was already correct through
    `GateInfo.from_scan_report`'s mapped-code branch -- only the category
    label was missing.
    """
    report = tmp_path / "abi-report-bundle.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "API_BREAK",
                "exit_code": 2,
                "per_artifact": [
                    {
                        "artifact": "libapibreak.so",
                        "scan_schema_version": "1.23",
                        "verdict": "API_BREAK",
                        "exit_code": 2,
                        "findings": 3,
                        "layers": [],
                        "confidence": {},
                        "estimate": [],
                        "report": {},
                    },
                    {
                        "artifact": "libincomplete.so",
                        "scan_schema_version": "1.23",
                        "verdict": "EVIDENCE_CONTRACT_ERROR",
                        "exit_code": 1,
                        "findings": 0,
                        "layers": [],
                        "confidence": {},
                        "estimate": [],
                        "report": {},
                    },
                ],
                "bundle_findings": [],
                "bundle_finding_count": 0,
                "bundle_verdict": None,
                "bundle_incomplete": False,
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is not None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 2
    assert loaded.gate.blocking is True
    assert "evidence_contract_error" in loaded.gate.blocking_categories
