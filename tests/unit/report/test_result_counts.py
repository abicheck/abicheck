from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import Change
from abicheck.policy.disposition_ledger import Disposition
from abicheck.policy.severity import IssueCategory
from abicheck.report.disposition_audit import DispositionAudit
from abicheck.report.finding import ReportFinding
from abicheck.report.result_counts import compute_result_counts
from abicheck.report.review_groups import build_review_groups


def _f(kind: ChangeKind, verdict: Verdict = Verdict.COMPATIBLE) -> ReportFinding:
    return ReportFinding(
        Change(kind, kind.value, kind.value), verdict, IssueCategory.ADDITION
    )


def test_populations_distinguish_surface_runtime_hygiene_and_groups() -> None:
    findings = [_f(ChangeKind.FUNC_ADDED), _f(ChangeKind.IMPORTED_SYMBOL_REMOVED)]
    hygiene = _f(ChangeKind.PUBLIC_NOT_EXPORTED)
    hygiene.change.cross_source_evolution = CrossSourceEvolution.PERSISTENT
    findings.append(hygiene)
    groups = build_review_groups(findings)
    audit = DispositionAudit(5, 1, ((Disposition.GATING.value, 1),), (), ())
    counts = compute_result_counts(
        findings,
        groups,
        audit,
        observed_changes=[finding.change for finding in findings],
    )
    assert counts.raw_detected == 5
    assert (counts.retained, counts.gating, counts.non_gating) == (3, 1, 2)
    assert counts.public_additions == 1
    assert counts.runtime_dependency == 1
    assert counts.hygiene_persistent == 1
    assert counts.detected_public_additions == 1


def test_suppressed_removals_remain_in_detected_operation_population() -> None:
    retained: list[ReportFinding] = []
    removals = [
        Change(ChangeKind.FUNC_REMOVED, f"gone_{index}", "removed")
        for index in range(100)
    ]
    audit = DispositionAudit(100, 0, ((Disposition.SUPPRESSED.value, 100),), (), ())
    counts = compute_result_counts(retained, (), audit, observed_changes=removals)
    assert counts.retained == 0 and counts.gating == 0
    assert counts.detected_public_removals == 100


def test_hygiene_lifecycle_populations_do_not_become_new_runtime_risk() -> None:
    findings: list[ReportFinding] = []
    for state, total in (
        (CrossSourceEvolution.RESOLVED, 3),
        (CrossSourceEvolution.PERSISTENT, 33),
    ):
        for index in range(total):
            finding = _f(ChangeKind.PUBLIC_NOT_EXPORTED)
            finding.change.symbol = f"N::f_{state.value}_{index}"
            finding.change.cross_source_evolution = state
            findings.append(finding)
    counts = compute_result_counts(
        findings,
        build_review_groups(findings),
        DispositionAudit(36, 0, (), (), ()),
        observed_changes=[finding.change for finding in findings],
    )
    assert counts.hygiene_introduced == 0
    assert counts.hygiene_resolved == 3
    assert counts.hygiene_persistent == 33
    assert counts.runtime_dependency == 0
