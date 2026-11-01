"""Pins today's exit code for a per-axis matrix on each precedence path.

Known gap ``scalar-and-release-compare-still-fold-the-exit-code-in-two``: the
scalar ``compare`` exit and the directory/package release exit are two ladders
that both delegate their dominant axes to one rank table
(``policy/exit_rank.py``, tested on its own in ``test_exit_rank_table.py``).
These tests record what each ladder returns for every combination of its
inputs, and ``TestOneMemberCellDiff`` pins the cells where the two disagree at
N=1. The expected values are written out literally from the precedence
documented in ``policy/exit_decision_precedence.py`` (not computed by the
helpers under test), so a change in either path's precedence fails here
instead of silently moving an exit code.
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


def _scalar_with_contributions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    severity: bool,
    compat: int,
    coverage: int,
    assurance: int,
    additions: int,
    unused: int,
    evidence: bool,
    budget: bool,
):
    """The scalar ladder with each fold axis's derived contribution pinned."""
    import dataclasses

    from abicheck.policy import (
        acknowledgment_gate,
        analysis_assurance,
        contract_coverage_exit,
        severity as severity_mod,
        unused_rules_gate,
    )

    monkeypatch.setattr(
        contract_coverage_exit, "coverage_exit_floor", lambda r: coverage
    )
    monkeypatch.setattr(
        analysis_assurance,
        "analysis_assurance_exit_contribution",
        lambda r, require_complete=False: assurance,
    )
    monkeypatch.setattr(
        acknowledgment_gate, "additions_review_exit_contribution", lambda r: additions
    )
    monkeypatch.setattr(
        unused_rules_gate,
        "unused_rules_exit_contribution",
        lambda r, today=None: unused,
    )
    gate = EffectiveGate.from_severity(None)
    verdict = {0: Verdict.NO_CHANGE, 2: Verdict.API_BREAK, 4: Verdict.BREAKING}.get(
        compat, Verdict.NO_CHANGE
    )
    if severity:
        gate = dataclasses.replace(gate, severity=object())
        monkeypatch.setattr(severity_mod, "compute_exit_code", lambda *a, **k: compat)
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=[],
        verdict=verdict,
        evidence_contract_error=evidence,
        budget_overflow=budget,
    )
    return resolve_compare_exit_decision_with_abort_axes(result, gate)


_N1_CELLS = [
    (severity, compat, coverage, assurance, additions, unused, evidence, budget)
    for severity in (False, True)
    for compat in ((0, 1, 2, 3, 4) if severity else (0, 2, 4))
    for coverage, assurance, additions, unused, evidence, budget in itertools.product(
        (0, 1), (0, 1), (0, 1), (0, 1), (False, True), (False, True)
    )
]


class TestOneMemberCellDiff:
    """Every N=1 cell of the two ladders, compared field for field.

    The release ladder is given the same contributions with its release-only
    axes empty (not comparable, removed library, operational error, scope).
    The two ladders agree on every cell except one family: a budget overflow
    the evidence contract does not outrank. The release ladder has no budget
    input, so it cannot express the scalar's exit 5. That family awaits a
    maintainer ruling under ADR-064 (known gap
    ``scalar-and-release-compare-still-fold-the-exit-code-in-two``); this
    test pins it so the diff can neither grow nor silently close.
    """

    @pytest.mark.parametrize(
        (
            "severity",
            "compat",
            "coverage",
            "assurance",
            "additions",
            "unused",
            "evidence",
            "budget",
        ),
        _N1_CELLS,
    )
    def test_cell(
        self,
        monkeypatch: pytest.MonkeyPatch,
        severity: bool,
        compat: int,
        coverage: int,
        assurance: int,
        additions: int,
        unused: int,
        evidence: bool,
        budget: bool,
    ) -> None:
        scalar = _scalar_with_contributions(
            monkeypatch,
            severity=severity,
            compat=compat,
            coverage=coverage,
            assurance=assurance,
            additions=additions,
            unused=unused,
            evidence=evidence,
            budget=budget,
        )
        release = resolve_release_exit_decision(
            not_comparable=False,
            severity_scheme_active=severity,
            verdict_or_severity_contribution=compat,
            contract_coverage_contribution=coverage,
            analysis_assurance_contribution=assurance,
            additions_review_contribution=additions,
            unused_rules_contribution=unused,
            evidence_contract_error_contribution=7 if evidence else 0,
        )
        if budget and not evidence:
            # The pinned disagreement: scalar exits 5, release has no axis.
            assert (scalar.code, scalar.reasons) == (5, (ExitReason.BUDGET_OVERFLOW,))
            assert release.code == max(compat, coverage, assurance, additions, unused)
            assert release.budget_overflow_contribution == 0
        else:
            assert scalar == release

    def test_cell_count(self) -> None:
        # 2 schemes' verdict codes (3 legacy + 5 severity) x 2^6 binary axes.
        assert len(_N1_CELLS) == 512
        assert sum(1 for c in _N1_CELLS if c[7] and not c[6]) == 128


class TestNotComparableAtOneMember:
    """The scalar comparability refusal and a one-member release refusal are
    the same rank-table axis: exit 16, reason ``not_comparable`` only."""

    def test_scalar_refusal_equals_release_refusal(self) -> None:
        from abicheck.policy.exit_decision_precedence import (
            resolve_compare_not_comparable_exit_decision,
        )

        scalar = resolve_compare_not_comparable_exit_decision()
        release = resolve_release_exit_decision(
            not_comparable=True,
            severity_scheme_active=False,
            verdict_or_severity_contribution=0,
        )
        assert (scalar.code, scalar.reasons) == (16, (ExitReason.NOT_COMPARABLE,))
        assert scalar == release
