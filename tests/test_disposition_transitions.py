"""ADR-067 D2: the moves a disposition record may make after it is decided.

Exhaustive over every disposition and both flags, against a table written
here (not the module's own sets). Then the ledger-level contract: one
consumer scope, applied once; a repeat of the same scope is a no-op; a second,
different scope (the per-consumer intersection bug) raises.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.policy.disposition_transitions import exclude_consumer_only, scope_out
from abicheck.policy.disposition_types import Disposition, DispositionRecord

G, N, S, OOC, U, D = (
    Disposition.GATING,
    Disposition.NON_GATING,
    Disposition.SUPPRESSED,
    Disposition.OUT_OF_CONTRACT,
    Disposition.UNRESOLVED_RELEVANCE,
    Disposition.DEDUPLICATED,
)

#: from -> (to, gate_excluded becomes True?)
SCOPE_OUT = {
    G: (N, True),
    N: (N, True),
    D: (D, True),
    S: (S, False),
    OOC: (OOC, False),
    U: (U, False),
}
CONSUMER_ONLY = {
    G: (N, True),
    S: (S, True),
    N: (N, False),
    D: (D, False),
    OOC: (OOC, False),
    U: (U, False),
}


def _rec(disposition: Disposition, excluded: bool, scoped: bool) -> DispositionRecord:
    return DispositionRecord(
        kind="func_removed",
        symbol="f",
        disposition=disposition,
        application_point="test",
        gate_excluded=excluded,
        scope_decided=scoped,
    )


def test_the_tables_cover_every_disposition() -> None:
    assert set(SCOPE_OUT) == set(Disposition) == set(CONSUMER_ONLY)


@pytest.mark.parametrize(
    ("fn", "table"), [(scope_out, SCOPE_OUT), (exclude_consumer_only, CONSUMER_ONLY)]
)
def test_each_move_matches_the_table(fn, table) -> None:  # type: ignore[no-untyped-def]
    for disp, excluded, scoped in itertools.product(
        Disposition, (False, True), (False, True)
    ):
        before = _rec(disp, excluded, scoped)
        after = fn(before)
        to, marks = table[disp]
        assert after.disposition is to
        assert after.gate_excluded == (excluded or marks)
        # Nothing else about the record moves.
        assert (
            after.kind,
            after.symbol,
            after.application_point,
            after.scope_decided,
        ) == (
            before.kind,
            before.symbol,
            before.application_point,
            before.scope_decided,
        )


def test_moves_are_idempotent() -> None:
    for fn in (scope_out, exclude_consumer_only):
        for disp, excluded in itertools.product(Disposition, (False, True)):
            once = fn(_rec(disp, excluded, False))
            assert fn(once) == once


def _promotable_ledger():  # type: ignore[no-untyped-def]
    """A gating finding the consumer uses, and an out-of-contract one an
    explicit scope promoted (ADR-049 §4.3) that the consumer does not use."""
    from abicheck.contract_relevance_types import (
        EXPLICIT_SCOPE_REASON_CODE,
        ContractRelevance,
    )
    from abicheck.policy.disposition_ledger import DispositionLedger
    from tests.test_disposition_scope_matrix import (
        _empty_result,
        _fixture_change,
        _record_initially,
    )

    result = _empty_result()
    ledger = DispositionLedger()
    used = _fixture_change(G, 0)
    promoted = _fixture_change(OOC, 1)
    _record_initially(ledger, used, G, result)
    _record_initially(ledger, promoted, OOC, result)
    assert [r.disposition for r in ledger.records] == [G, OOC]
    promoted.contract_relevance = ContractRelevance.IN_CONTRACT
    promoted.contract_reason_code = EXPLICIT_SCOPE_REASON_CODE
    return ledger, result, used


@pytest.mark.parametrize("order", ["promote_then_scope", "scope_then_promote"])
def test_promotion_and_scope_commute(order: str) -> None:
    ledger, result, used = _promotable_ledger()
    if order == "promote_then_scope":
        ledger.refresh_promoted(result)
        ledger.apply_scope([used])
    else:
        ledger.apply_scope([used])
        ledger.refresh_promoted(result)
    # Promoted into the contract, but this consumer does not use it.
    assert [(r.disposition, r.gate_excluded) for r in ledger.records] == [
        (G, False),
        (N, True),
    ]


def test_same_scope_twice_is_a_no_op_and_a_second_scope_raises() -> None:
    ledger, _result, used = _promotable_ledger()
    ledger.apply_scope([used])
    first = ledger.records
    ledger.apply_scope([used])
    assert ledger.records == first
    with pytest.raises(RuntimeError, match="second scope"):
        ledger.apply_scope([])


def test_a_pipeline_with_two_aliasing_steps_is_rejected_when_built() -> None:
    from types import SimpleNamespace

    from abicheck.policy.finding_disposition.default_pipeline import DEFAULT_PIPELINE
    from abicheck.policy.finding_disposition.pipeline import DispositionPipeline

    aliasing = [
        s for s in DEFAULT_PIPELINE.steps if getattr(s, "establishes_kept_alias", False)
    ]
    assert len(aliasing) == 1  # the production pipeline is valid
    extra = SimpleNamespace(name="second", establishes_kept_alias=True)
    with pytest.raises(ValueError, match="more than one step"):
        DispositionPipeline([*DEFAULT_PIPELINE.steps, extra])  # type: ignore[list-item]
