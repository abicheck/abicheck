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
        ("NOT_COMPARABLE", 6, "not_comparable"),
        ("BUNDLE_INCOMPLETE", 1, "extraction_error"),
    ],
)
def test_scan_abort_verdicts_force_a_blocking_gate(
    tmp_path: Path, verdict: str, report_exit_code: int, category: str
) -> None:
    """`scan`'s own four abort verdicts aren't `Verdict` members, so without
    dedicated handling `_load_report_file` never reaches `GateInfo.from_
    scan_report` for them (it only calls that after `parse_report_verdict`
    succeeds) -- the abort would read as an unavailable/verdictless report
    a required-target policy could silently tolerate, instead of the real
    failure it is (Codex review, fresh evidence -- `NOT_COMPARABLE`/
    `BUNDLE_INCOMPLETE` were the two of these four still missing from
    `_scan_abort_categories`, silently discarding a blocking
    `run_outcome.operational` for either one).

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


def test_scan_report_with_a_real_verdict_dispatches_to_the_scan_reader_first(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a native `scan` report carries its own
    top-level `run_outcome` (ADR-063 Phase 7) but no top-level `severity`
    block -- a severity-scheme `scan --against` nests its gate at
    `diff.severity` instead. `_load_report_file` previously tried
    `GateInfo.from_report_data` FIRST for every report: its own "no
    `severity` -> read `run_outcome` alone" branch returned straight from
    the (here, forged) root `run_outcome` without ever reaching
    `GateInfo.from_scan_report`, the only reader that validates/cross-checks
    the nested `diff.severity` gate against it. A forged root
    `run_outcome.gate: "none"` alongside a real nested `diff.severity.
    exit_code: 4` must fail closed (`_MalformedGate`), not silently read as
    a nonblocking gate.
    """
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.9",
                "verdict": "BREAKING",
                "diff": {
                    "severity": {
                        "exit_code": 4,
                        "blocking": True,
                        "blocking_categories": ["abi_breaking"],
                    }
                },
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": "BREAKING",
                    "assurance": None,
                    "gate": "none",
                    "operational": "none",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is None
    assert loaded.reason is not None and "malformed" in loaded.reason


def test_operational_error_preserves_a_completed_librarys_real_verdict(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (second round): a release's top-level
    `verdict: "ERROR"` names only the OPERATIONALLY failed library -- when a
    sibling library completed with a real result, `run_outcome.
    compatibility` already preserves it. Forcing `Verdict.BREAKING`
    unconditionally discarded that real result; the gate's own exit-4 floor
    stays unconditional either way (an operational failure blocks
    regardless of what else completed cleanly)."""
    from abicheck.change_registry_types import Verdict

    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "ERROR",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [
                    {"name": "a", "verdict": "ERROR"},
                    {"name": "b", "verdict": "COMPATIBLE_WITH_RISK"},
                ],
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": "COMPATIBLE_WITH_RISK",
                    "assurance": None,
                    "gate": "none",
                    "operational": "extraction_error",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is Verdict.COMPATIBLE_WITH_RISK
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 4
    assert loaded.gate.blocking_categories == ("operational_error",)


def test_late_budget_overflow_preserves_a_real_completed_compatibility_verdict(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (second round): a *late* `BUDGET_
    OVERFLOW`/`EVIDENCE_CONTRACT_ERROR` abort can also carry a real
    completed verdict in `run_outcome.compatibility` -- not only
    `BUNDLE_INCOMPLETE`. Reading it unconditionally (not gated to one
    sentinel) recovers it here too, with no regression for a report where
    nothing genuinely completed (that case has no real `compatibility` to
    read, so `_run_outcome_compatibility_verdict` still returns `None`)."""
    from abicheck.change_registry_types import Verdict

    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": "API_BREAK",
                    "assurance": None,
                    "gate": "none",
                    "operational": "budget_overflow",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is Verdict.API_BREAK
    assert loaded.reason is None
    assert loaded.gate is not None
    assert loaded.gate.blocking_categories == ("budget_overflow",)


def test_not_comparable_refusal_with_run_outcome_blocks_via_operational_axis_only(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: `report.not_comparable.
    not_comparable_document()` always writes a top-level `run_outcome`
    (`compatibility: null`, `gate: none`, `operational: not_comparable`) for
    this exact shape -- read it directly rather than fabricating
    `Verdict.BREAKING`/exit 4 unconditionally. The orthogonal fold floors at
    exit 1 ("only the operational axis blocks"), consistent with every
    other operational-failure sentinel in this module. A report with NO
    `run_outcome` (pre-2.48) still gets the old forced exit-4/BREAKING
    shape -- see `TestNotComparableReportsBlockAggregation` in
    `tests/test_aggregate.py`, which is pinned to that exact fallback and
    must keep passing unchanged."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": None,
                "reason": {"kind": "scope_mismatch", "message": "scope drift"},
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": None,
                    "assurance": None,
                    "gate": "none",
                    "operational": "not_comparable",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 1
    assert loaded.gate.blocking_categories == ("not_comparable",)
    assert loaded.reason is not None and "scope_mismatch" in loaded.reason


def test_not_comparable_refusal_with_a_malformed_run_outcome_fails_closed_not_crashing(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: `_run_outcome_gate_and_operational`
    raises `_MalformedGate` (rather than returning `None`) for a PRESENT
    but schema-invalid `run_outcome` -- every other branch that calls it
    wraps the call in a `try`/`except _MalformedGate`, but this refusal
    branch previously called it bare. A corrupt `run_outcome` on a
    `verdict: null` + `reason.kind` refusal must land the target
    unavailable with a malformed-gate reason, not raise an exception out of
    `_load_report_file` and abort the whole aggregation command."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": None,
                "reason": {"kind": "scope_mismatch", "message": "scope drift"},
                "run_outcome": {"gate": "not_a_real_value", "operational": "none"},
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is None
    assert loaded.reason is not None and "malformed" in loaded.reason


def test_bundle_incomplete_preserves_the_completed_members_compatibility_verdict(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: `BUNDLE_INCOMPLETE` is the one abort
    sentinel of the four where a real comparison DID complete -- it fires
    only after every member scanned cleanly and just the cross-library
    bundle audit itself never ran. `run_outcome.compatibility` already
    preserves the worst completed member's real verdict; forcing
    `verdict=None` the way a true abort does would discard it and wrongly
    report the target as unavailable/unanalyzed even though it has a real,
    already-established result.
    """
    from abicheck.change_registry_types import Verdict

    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUNDLE_INCOMPLETE",
                "exit_code": 1,
                "per_artifact": [
                    {
                        "artifact": "a.so",
                        "verdict": "COMPATIBLE_WITH_RISK",
                        "exit_code": 0,
                    }
                ],
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": "COMPATIBLE_WITH_RISK",
                    "assurance": None,
                    "gate": "none",
                    "operational": "extraction_error",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is Verdict.COMPATIBLE_WITH_RISK
    assert loaded.reason is None
    assert loaded.gate is not None
    assert loaded.gate.blocking is True
    assert loaded.gate.blocking_categories == ("extraction_error",)


def test_bundle_incomplete_with_a_truncated_run_outcome_does_not_recover_a_verdict(
    tmp_path: Path,
) -> None:
    """CodeRabbit review, fresh evidence: a bare
    `{"run_outcome": {"compatibility": "BREAKING"}}` -- missing `gate`/
    `operational`/`schema_version`/`lifecycle` -- must not earn the
    opportunistic verdict-recovery this window's other fix added. Only a
    schema-COMPLETE `run_outcome` may make a `BUNDLE_INCOMPLETE` report
    read as analyzed and preserve its findings/digest; a truncated one
    falls back to the same `verdict=None`/`findings=None` shape a true
    abort gets."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.23",
                "verdict": "BUNDLE_INCOMPLETE",
                "exit_code": 1,
                "run_outcome": {"compatibility": "BREAKING"},
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.findings is None
    assert loaded.effective_config_digest is None
    assert (
        loaded.reason
        == "scan aborted before completing a comparison (extraction_error)"
    )


def test_release_lowercase_not_comparable_is_recognized_as_a_blocking_refusal(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a `compare-release` summary's own
    lowercase `"not_comparable"` sentinel (ADR-050 D2) is a real string, not
    JSON `null` -- distinct from `scan`'s uppercase `NOT_COMPARABLE` and from
    a native `compare`'s `verdict: null` + `reason.kind` shape, so it was
    caught by neither special-case branch and fell through to the generic
    "report carried no ABI verdict" unavailable reading, silently discarding
    a blocking `run_outcome.operational: "not_comparable"`.
    """
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "not_comparable",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [],
                "exit": {"code": 16, "not_comparable_contribution": 1},
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": None,
                    "assurance": None,
                    "gate": "none",
                    "operational": "not_comparable",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.reason == "not comparable (release refused comparison)"
    assert loaded.gate is not None
    assert loaded.gate.blocking is True
    assert loaded.gate.blocking_categories == ("not_comparable",)


def test_pre_2_48_release_refusal_with_no_severity_or_run_outcome_still_blocks(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a genuinely pre-2.48 `compare-release`
    summary (neither `severity` nor `run_outcome`) still refused the
    comparison via the legacy `"not_comparable"` sentinel. `GateInfo.
    from_report_data` legitimately returns `None` for that shape -- that
    must not read as gate-less/unavailable, letting an optional or
    tolerated-unexpected target pass."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "not_comparable",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [],
                "exit": {"not_comparable_contribution": 1},
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.blocking is True
    assert loaded.gate.blocking_categories == ("not_comparable",)


def test_release_refusal_preserves_findings_from_a_completed_sibling(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: when one library refuses comparison but
    a sibling library or the global bundle/matrix comparison completed,
    `run_outcome.compatibility` is non-null and the target is marked
    analyzed -- `_format_release_json` can still emit real `bundle_
    findings`/`matrix_findings` in this state, and dropping them (mirroring
    the `ERROR`/scan-abort branches' own incomplete-findings preservation)
    would lose them from cross-profile reconciliation."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "not_comparable",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [],
                "bundle_findings": [
                    {
                        "kind": "func_removed",
                        "symbol": "sym",
                        "description": "d",
                        "affected_libraries": ["a.so"],
                    }
                ],
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": "BREAKING",
                    "assurance": None,
                    "gate": "abi_breaking",
                    "operational": "not_comparable",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.findings is not None
    assert not loaded.findings.complete
    assert len(loaded.findings.findings) == 1


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


def test_artifact_set_budget_overflow_root_still_names_a_sibling_members_evidence_error(
    tmp_path: Path,
) -> None:
    """`_aggregate_scan_set_verdict`'s own step 1 makes any member's
    `BUDGET_OVERFLOW` dominate the set-level `verdict` unconditionally,
    even when a *different* member aborted with `EVIDENCE_CONTRACT_ERROR`
    for an unrelated reason. The root-abort branch above hardcodes only
    the one category matching the root `verdict` string
    (`scan_abort_category`), so before this fix the sibling member's own
    `evidence_contract_error` category was silently dropped from the
    gate -- the same class of gap `_member_abort_categories` closed for
    the normal-verdict path, but reached here too since the budget-
    dominant branch returns before that helper is ever consulted (Codex
    review, fresh evidence).
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
                    {
                        "artifact": "libtimedout.so",
                        "scan_schema_version": "1.23",
                        "verdict": "BUDGET_OVERFLOW",
                        "exit_code": 5,
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

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.blocking is True
    assert "budget_overflow" in loaded.gate.blocking_categories
    assert "evidence_contract_error" in loaded.gate.blocking_categories


def test_operational_error_with_null_compatibility_does_not_fabricate_a_verdict(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (third round): a valid, schema-complete
    `run_outcome` block whose `compatibility` is legitimately JSON `null`
    (e.g. `build_operational_error_report`'s own extraction-failure report:
    `compatibility: null`, `gate: none`, `operational: extraction_error`)
    must NOT be treated the same as a genuinely absent block. Forcing
    `Verdict.BREAKING` for this shape fabricated an ABI-break verdict and an
    "analyzed" target count for a comparison that never ran. The gate's own
    exit-4 floor stays unconditional either way."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "ERROR",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [],
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": None,
                    "assurance": None,
                    "gate": "none",
                    "operational": "extraction_error",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 4
    assert loaded.gate.blocking is True
    assert loaded.gate.blocking_categories == ("operational_error",)


def test_legacy_error_release_with_no_run_outcome_still_forces_breaking(
    tmp_path: Path,
) -> None:
    """A genuinely pre-2.48 release ERROR report (no `run_outcome` at all)
    still forces the original synthetic `Verdict.BREAKING` -- confirms the
    null-compatibility fix above didn't widen to cover the legacy no-
    run_outcome case too, which must keep its original forced-blocking
    shape exactly (pinned by `tests/test_aggregate.py`'s own
    `TestNotComparableReportsBlockAggregation`-adjacent expectations for
    this branch)."""
    from abicheck.change_registry_types import Verdict

    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "ERROR",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [{"name": "a", "verdict": "ERROR"}],
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is Verdict.BREAKING
    assert loaded.gate is not None
    assert loaded.gate.exit_code == 4


def test_operational_error_with_a_malformed_run_outcome_fails_closed_not_breaking(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a *present but schema-invalid*
    `run_outcome` (missing required keys here) is a third case, distinct
    from both "absent" and "valid" -- `_has_valid_run_outcome_block` reads
    `False` for it the same as a genuinely absent block, so without an
    explicit malformed-check this silently fell through to the legacy
    fabricated-`Verdict.BREAKING` path instead of failing the target
    unavailable/malformed like every other structured-`run_outcome` reader
    in this module (the null-verdict/`reason.kind` refusal branch and the
    release lowercase not_comparable branch both already do this)."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "ERROR",
                "old_dir": "/old",
                "new_dir": "/new",
                "libraries": [],
                "run_outcome": {"compatibility": "BREAKING"},
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.verdict is None
    assert loaded.gate is None
    assert loaded.reason is not None and "malformed" in loaded.reason


def test_scan_abort_honors_a_structured_gate_the_legacy_blocks_miss(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a `BUDGET_OVERFLOW` report can carry a
    valid `run_outcome` that preserves a completed ABI-breaking gate even
    though its legacy `diff.exit`/member contribution blocks are absent --
    the gate previously computed solely from `_scan_abort_prior_exit`
    (which found nothing here), loading as exit 1/`budget_overflow` only
    instead of retaining exit 4 and the `abi_breaking` category the
    structured gate actually recorded."""
    report = tmp_path / "abi-report-linux.json"
    report.write_text(
        json.dumps(
            {
                "scan_schema_version": "1.24",
                "verdict": "BUDGET_OVERFLOW",
                "exit_code": 5,
                "run_outcome": {
                    "schema_version": "1",
                    "compatibility": "BREAKING",
                    "assurance": None,
                    "gate": "abi_breaking",
                    "operational": "budget_overflow",
                    "lifecycle": "existing",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_report_file(report, prefix="abi-report-")

    assert loaded.gate is not None
    assert loaded.gate.exit_code == 4
    assert "abi_breaking" in loaded.gate.blocking_categories
