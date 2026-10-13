"""Pins today's exit code for a per-axis matrix on each precedence path.

Known gap ``scalar-and-release-compare-still-fold-the-exit-code-in-two``: the
scalar ``compare`` exit and the directory/package release exit are decided by
two resolvers that share some axes and differ on others. Before either is
moved onto a shared owner, these tests record what each path returns for every
combination of its inputs. The expected values are written out literally from
the precedence documented in ``policy/exit_decision_precedence.py`` (not
computed by the helpers under test), so a change in either path's precedence
fails here instead of silently moving an exit code.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import DiffResult
from abicheck.checker_policy import Verdict
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.exit_decision import ExitReason
from abicheck.policy.exit_decision_precedence import (
    resolve_compare_exit_decision_with_abort_axes,
    resolve_release_exit_decision,
)

# Compatibility-axis exit per verdict under the legacy scheme, as documented in
# docs/reference/exit-codes.md (not read back from severity.py).
_LEGACY_CODE = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}


def _scalar(verdict: Verdict, *, evidence: bool, budget: bool):
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=[],
        verdict=verdict,
        evidence_contract_error=evidence,
        budget_overflow=budget,
    )
    return resolve_compare_exit_decision_with_abort_axes(
        result, EffectiveGate.from_severity(None)
    )


class TestScalarCompareExitMatrix:
    """Scalar path: compatibility verdict x evidence-contract x budget-overflow."""

    @pytest.mark.parametrize(
        ("verdict", "evidence", "budget"),
        list(itertools.product(_LEGACY_CODE, (False, True), (False, True))),
    )
    def test_code_and_reasons(
        self, verdict: Verdict, evidence: bool, budget: bool
    ) -> None:
        # Literal precedence: evidence-contract (7) beats budget overflow (5),
        # which beats the ordinary verdict fold.
        if evidence:
            expected_code, expected = 7, (ExitReason.EVIDENCE_CONTRACT_ERROR,)
        elif budget:
            expected_code, expected = 5, (ExitReason.BUDGET_OVERFLOW,)
        elif _LEGACY_CODE[verdict] == 0:
            expected_code, expected = 0, (ExitReason.CLEAN,)
        else:
            expected_code = _LEGACY_CODE[verdict]
            expected = (ExitReason.COMPATIBILITY_GATE,)
        decision = _scalar(verdict, evidence=evidence, budget=budget)
        assert decision.code == expected_code
        assert decision.reasons == expected


# Release axes, in the order the release resolver names them.
_RELEASE_VERDICT_CONTRIBUTIONS = (0, 1, 2, 4)
_RELEASE_OPERATIONAL = (0, 4)
_RELEASE_COVERAGE = (0, 1)
_RELEASE_EVIDENCE = (0, 7)


def _release_expected(
    *,
    not_comparable: bool,
    severity: bool,
    verdict_or_severity: int,
    removed: bool,
    operational: int,
    coverage: int,
    evidence: int,
) -> tuple[int, frozenset[ExitReason]]:
    """Literal precedence for the release path (see the resolver's docstring).

    not_comparable (16) > evidence-contract (7), where a proven removal (8)
    that is active outranks evidence > under severity, a proven removal (8)
    beats the fold > under legacy, a nonzero verdict/operational fold beats
    a proven removal, which is checked only once both are 0 > otherwise the
    max of every fold axis.
    """
    removal_active_over_evidence = removed and (
        severity or (verdict_or_severity == 0 and operational == 0)
    )
    if not_comparable:
        return 16, frozenset({ExitReason.NOT_COMPARABLE})
    if evidence and removal_active_over_evidence:
        return 8, frozenset({ExitReason.REMOVED_REQUIRED_LIBRARY})
    if evidence:
        return 7, frozenset({ExitReason.EVIDENCE_CONTRACT_ERROR})
    if severity and removed:
        return 8, frozenset({ExitReason.REMOVED_REQUIRED_LIBRARY})
    if not severity and verdict_or_severity == 0 and operational == 0 and removed:
        return 8, frozenset({ExitReason.REMOVED_REQUIRED_LIBRARY})
    axes = {
        ExitReason.COMPATIBILITY_GATE: verdict_or_severity,
        ExitReason.CONTRACT_COVERAGE: coverage,
        ExitReason.OPERATIONAL_ERROR: operational,
    }
    code = max(axes.values())
    if code == 0:
        return 0, frozenset({ExitReason.CLEAN})
    return code, frozenset(reason for reason, value in axes.items() if value == code)


class TestReleaseExitMatrix:
    """Release path: every combination of its own axes, both schemes."""

    @pytest.mark.parametrize(
        (
            "not_comparable",
            "severity",
            "verdict_or_severity",
            "removed",
            "operational",
            "coverage",
            "evidence",
        ),
        list(
            itertools.product(
                (False, True),
                (False, True),
                _RELEASE_VERDICT_CONTRIBUTIONS,
                (False, True),
                _RELEASE_OPERATIONAL,
                _RELEASE_COVERAGE,
                _RELEASE_EVIDENCE,
            )
        ),
    )
    def test_code_and_reasons(
        self,
        not_comparable: bool,
        severity: bool,
        verdict_or_severity: int,
        removed: bool,
        operational: int,
        coverage: int,
        evidence: int,
    ) -> None:
        expected_code, expected_reasons = _release_expected(
            not_comparable=not_comparable,
            severity=severity,
            verdict_or_severity=verdict_or_severity,
            removed=removed,
            operational=operational,
            coverage=coverage,
            evidence=evidence,
        )
        decision = resolve_release_exit_decision(
            not_comparable=not_comparable,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=verdict_or_severity,
            removed_required_library=removed,
            contract_coverage_contribution=coverage,
            operational_error_contribution=operational,
            evidence_contract_error_contribution=evidence,
        )
        assert decision.code == expected_code
        assert frozenset(decision.reasons) == expected_reasons


class TestSharedAxesAgreeAtOneMember:
    """Cardinality-one agreement on the axes both paths can express.

    A one-member release must exit exactly as the scalar compare of that
    member does. Covers the verdict fold and the evidence-contract axis, the
    two axes both resolvers take as inputs. Scoped to legacy scheme with no
    release-only axis set, since those have no scalar input.
    """

    @pytest.mark.parametrize(
        ("verdict", "evidence"),
        list(itertools.product(_LEGACY_CODE, (False, True))),
    )
    def test_release_of_one_matches_scalar(
        self, verdict: Verdict, evidence: bool
    ) -> None:
        scalar = _scalar(verdict, evidence=evidence, budget=False)
        release = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=False,
            verdict_or_severity_contribution=_LEGACY_CODE[verdict],
            evidence_contract_error_contribution=7 if evidence else 0,
        )
        assert release.code == scalar.code
        assert frozenset(release.reasons) == frozenset(scalar.reasons)


class TestEvidenceOwnerKeepsOrdinaryContributions:
    """The shared evidence owner, on both paths, decides the code (7) and
    names only its own reason, but keeps the ordinary fold it dominated for
    explainability -- the release path already did, the scalar path now does
    too. The code itself is unchanged for every input.
    """

    def test_scalar_keeps_the_gate_it_dominated(self) -> None:
        decision = _scalar(Verdict.BREAKING, evidence=True, budget=False)
        assert decision.code == 7
        assert decision.reasons == (ExitReason.EVIDENCE_CONTRACT_ERROR,)
        assert decision.compatibility_contribution == 4

    def test_release_keeps_the_gate_it_dominated(self) -> None:
        decision = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=False,
            verdict_or_severity_contribution=4,
            evidence_contract_error_contribution=7,
        )
        assert decision.code == 7
        assert decision.reasons == (ExitReason.EVIDENCE_CONTRACT_ERROR,)
        assert decision.compatibility_contribution == 4
