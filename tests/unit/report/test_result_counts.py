from abicheck.checker_policy import ChangeKind, CrossSourceEvolution, Verdict
from abicheck.checker_types import Change
from abicheck.policy.disposition_ledger import Disposition
from abicheck.policy.severity import IssueCategory
from abicheck.report.disposition_audit import DispositionAudit
from abicheck.report.finding import ReportFinding
from abicheck.report.result_counts import compute_result_counts
from abicheck.report.review_compute import compact_evidence_summary
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


class _FakeResult:
    """Minimal stand-in carrying only the attribute the function reads."""

    def __init__(self, layers: tuple[str, ...]) -> None:
        self.layer_coverage = [
            {"layer": layer, "status": "present"} for layer in layers
        ]


#: Every layer `compact_evidence_summary` branches on, so the enumeration
#: below is exhaustive over its real input domain rather than sampling it.
_SUMMARY_LAYERS = ("L0", "L1", "L2", "L4_source_abi")


def test_evidence_summary_names_every_present_layer_and_every_absent_limit() -> None:
    """Exhaustive over all 16 present/absent combinations of the four layers.

    The oracle is written from the documented contract -- a layer that is
    present is named, a layer whose absence bounds the conclusion is listed
    as a limit -- not from the function's own if-chain.
    """
    expected_names = {
        "L0": "binary exports",
        "L2": "public headers/signatures",
        "L1": "debug-derived compiled layout",
    }
    failures: list[str] = []
    for mask in range(1 << len(_SUMMARY_LAYERS)):
        present = tuple(
            layer for i, layer in enumerate(_SUMMARY_LAYERS) if mask & (1 << i)
        )
        text = compact_evidence_summary(_FakeResult(present))
        for layer, name in expected_names.items():
            if (layer in present) != (name in text):
                failures.append(f"{present}: {name!r} presence wrong in {text!r}")
        # A run naming no present layer must still say what it rests on,
        # never render an empty claim.
        if not any(layer in present for layer in expected_names):
            if "recorded snapshot facts" not in text:
                failures.append(f"{present}: no fallback basis in {text!r}")
        if ("L1" not in present) != ("no debug-derived layout verification" in text):
            failures.append(f"{present}: L1 limit wrong in {text!r}")
        if ("L4_source_abi" not in present) != ("no source replay" in text):
            failures.append(f"{present}: L4 limit wrong in {text!r}")
        if not text.strip():
            failures.append(f"{present}: empty summary")
    assert not failures, "evidence summary contract violated:\n" + "\n".join(failures)


def test_evidence_summary_ignores_layers_that_are_not_present() -> None:
    """A recorded-but-failed layer must not be claimed as evidence."""
    result = _FakeResult(())
    result.layer_coverage = [
        {"layer": "L1", "status": "failed"},
        {"layer": "L0", "status": "present"},
        "not-a-dict",
    ]
    text = compact_evidence_summary(result)
    assert "binary exports" in text
    assert "debug-derived compiled layout" not in text
    assert "no debug-derived layout verification" in text


def test_evidence_summary_handles_a_result_with_no_layer_coverage() -> None:
    class _Bare:
        pass

    text = compact_evidence_summary(_Bare())
    assert "recorded snapshot facts" in text
