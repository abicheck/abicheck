"""The shared dominant-axis rank table, tested on its own.

Both ``compare`` exit ladders (scalar pair and directory/package release)
delegate to ``policy/exit_rank.resolve_ranked``. The expected order and codes
below are written out literally from ``docs/reference/exit-codes.md`` and
ADR-064, not read back from ``COMPARE_EXIT_RANK``, so a reordering of the
table fails here before it moves an exit code on either path.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from abicheck.policy.exit_decision import (
    ExitDecision,
    ExitReason,
    resolve_exit_decision,
)
from abicheck.policy.exit_decision_precedence import (
    EXIT_BUDGET_OVERFLOW,
    EXIT_EVIDENCE_CONTRACT_ERROR,
    EXIT_RELEASE_NOT_COMPARABLE,
    EXIT_REMOVED_REQUIRED_LIBRARY,
)
from abicheck.policy.exit_rank import COMPARE_EXIT_RANK, resolve_ranked

# Literal oracle: highest rank first, with each axis's code and its own field.
_ORDER = (
    (ExitReason.NOT_COMPARABLE, 16, "not_comparable_contribution"),
    (ExitReason.REMOVED_REQUIRED_LIBRARY, 8, "removed_required_library_contribution"),
    (ExitReason.EVIDENCE_CONTRACT_ERROR, 7, "evidence_contract_error_contribution"),
    (ExitReason.BUDGET_OVERFLOW, 5, "budget_overflow_contribution"),
)
_FIELD = {reason: field for reason, _, field in _ORDER}
# Only the evidence contract is still recorded once outranked (found so on
# both ladders before the table existed; not a ruling).
_RECORDED_WHEN_OUTRANKED = {ExitReason.EVIDENCE_CONTRACT_ERROR}

_ORDINARY_FOLDS = [
    {
        "compatibility_contribution": compat,
        "contract_coverage_contribution": cov,
        "operational_error_contribution": op,
    }
    for compat, cov, op in itertools.product((0, 2, 4), (0, 1), (0, 4))
]


def _contributions(decision: ExitDecision) -> dict[str, int]:
    return {
        f.name: getattr(decision, f.name)
        for f in dataclasses.fields(decision)
        if f.name.endswith("_contribution")
    }


def test_table_is_the_documented_order_and_codes() -> None:
    assert [(axis.reason, axis.code) for axis in COMPARE_EXIT_RANK] == [
        (reason, code) for reason, code, _ in _ORDER
    ]
    assert {
        axis.reason for axis in COMPARE_EXIT_RANK if axis.recorded_when_outranked
    } == _RECORDED_WHEN_OUTRANKED


def test_ladder_constants_are_the_table_codes() -> None:
    # The ladders pass these constants; they must be the table's own numbers.
    assert (
        EXIT_RELEASE_NOT_COMPARABLE,
        EXIT_REMOVED_REQUIRED_LIBRARY,
        EXIT_EVIDENCE_CONTRACT_ERROR,
        EXIT_BUDGET_OVERFLOW,
    ) == (16, 8, 7, 5)


@pytest.mark.parametrize(
    "fired",
    [
        frozenset(subset)
        for size in range(len(_ORDER) + 1)
        for subset in itertools.combinations([r for r, _, _ in _ORDER], size)
    ],
)
@pytest.mark.parametrize("fold", _ORDINARY_FOLDS)
def test_every_subset_of_dominant_axes(
    fired: frozenset[ExitReason], fold: dict[str, int]
) -> None:
    ordinary = resolve_exit_decision(**fold)
    codes = {reason: code for reason, code, _ in _ORDER}
    decision = resolve_ranked(
        {reason: codes[reason] for reason in fired}, ordinary=ordinary
    )
    if not fired:
        assert decision == ordinary
        return
    winner = next(reason for reason, _, _ in _ORDER if reason in fired)
    assert decision.code == codes[winner]
    assert decision.reasons == (winner,)
    expected = _contributions(ordinary)
    expected[_FIELD[winner]] = codes[winner]
    for reason in fired - {winner}:
        if reason in _RECORDED_WHEN_OUTRANKED:
            expected[_FIELD[reason]] = codes[reason]
    assert _contributions(decision) == expected
    # ExitDecision's invariant: the code is the largest contribution.
    assert decision.code == max(_contributions(decision).values())


def test_zero_valued_axis_has_not_fired() -> None:
    ordinary = resolve_exit_decision(compatibility_contribution=2)
    assert (
        resolve_ranked(
            {ExitReason.NOT_COMPARABLE: 0, ExitReason.BUDGET_OVERFLOW: 0},
            ordinary=ordinary,
        )
        == ordinary
    )


def test_caller_code_is_used_for_the_winner() -> None:
    # A persisted release member may carry its own evidence number.
    decision = resolve_ranked(
        {ExitReason.EVIDENCE_CONTRACT_ERROR: 9},
        ordinary=resolve_exit_decision(compatibility_contribution=4),
    )
    assert (decision.code, decision.evidence_contract_error_contribution) == (9, 9)


@pytest.mark.parametrize(
    "reason",
    [
        ExitReason.COMPATIBILITY_GATE,
        ExitReason.CONTRACT_COVERAGE,
        ExitReason.OPERATIONAL_ERROR,
    ],
)
def test_a_fold_axis_is_not_a_dominant_axis(reason: ExitReason) -> None:
    with pytest.raises(ValueError, match="not a ranked dominant axis"):
        resolve_ranked(
            {reason: 4}, ordinary=resolve_exit_decision(compatibility_contribution=0)
        )


def test_winner_code_not_above_the_fold_is_refused() -> None:
    # The table never lets a dominant code tie or fall below what it keeps.
    with pytest.raises(ValueError, match="must strictly exceed"):
        resolve_ranked(
            {ExitReason.BUDGET_OVERFLOW: 4},
            ordinary=resolve_exit_decision(compatibility_contribution=4),
        )
