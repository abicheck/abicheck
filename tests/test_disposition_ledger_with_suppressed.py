# SPDX-License-Identifier: Apache-2.0
"""``DispositionLedger.with_suppressed``: the one way a decision made after a
ledger closed (the release fan-out's lockstep-SONAME suppression) relabels
already-recorded findings ``SUPPRESSED``.

Its contract, stated over every combination of how a finding can reach it --
the recorded anchor, an alias of it (the same observation produced by a
second consumer, sharing one ``dedupe_key``), or an object the ledger never
saw -- rather than one worked example. The oracle is the ledger's own
public reading, ``record_for``, which resolves aliases independently of the
relabel. An earlier version matched only the exact anchor objects, so an
alias passed in left its finding labelled as before.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.model.change import Change
from abicheck.policy.disposition_ledger import Disposition, DispositionLedger
from abicheck.policy.rule_provenance import RuleProvenance

_RULE = RuleProvenance(rule_id="lockstep_soname_bump", reason="release lockstep")


def _change(symbol: str) -> Change:
    return Change(kind=ChangeKind.SONAME_CHANGED, symbol=symbol, description="x")


def _ledger(n: int) -> tuple[DispositionLedger, list[Change], list[Change]]:
    """*n* recorded findings, each with one alias recorded under its key."""
    ledger = DispositionLedger()
    anchors = [_change(f"s{i}") for i in range(n)]
    aliases = [_change(f"s{i}") for i in range(n)]
    for i, (anchor, alias) in enumerate(zip(anchors, aliases, strict=True)):
        for c in (anchor, alias):
            ledger.record(
                c,
                Disposition.NON_GATING,
                application_point="compare",
                dedupe_key=f"k{i}",
            )
    return ledger, anchors, aliases


@pytest.mark.parametrize(
    "picks",
    # For each of three findings: not passed (0), its anchor (1), its alias
    # (2), or both (3) -- every combination.
    list(itertools.product(range(4), repeat=3)),
)
def test_every_passed_finding_and_only_those_is_suppressed(picks) -> None:
    ledger, anchors, aliases = _ledger(3)
    passed: list[Change] = []
    for pick, anchor, alias in zip(picks, anchors, aliases, strict=True):
        if pick in (1, 3):
            passed.append(anchor)
        if pick in (2, 3):
            passed.append(alias)
    passed.append(_change("never_recorded"))

    out = ledger.with_suppressed(passed, application_point="lockstep", rule=_RULE)

    for pick, anchor, alias in zip(picks, anchors, aliases, strict=True):
        for c in (anchor, alias):
            record = out.record_for(c)
            assert record is not None
            if pick:
                assert record.disposition is Disposition.SUPPRESSED
                assert record.application_point == "lockstep"
                assert record.rule is _RULE
                assert record.gate_excluded is True
            else:
                assert record.disposition is Disposition.NON_GATING
                assert record.application_point == "compare"
    assert out.record_for(passed[-1]) is None


def test_the_original_ledger_is_not_mutated() -> None:
    ledger, anchors, aliases = _ledger(2)
    ledger.with_suppressed([aliases[0]], application_point="lockstep", rule=_RULE)
    record = ledger.record_for(anchors[0])
    assert record is not None
    assert record.disposition is Disposition.NON_GATING


def test_the_suppression_rule_is_counted() -> None:
    ledger, _anchors, aliases = _ledger(2)
    out = ledger.with_suppressed(aliases, application_point="lockstep", rule=_RULE)
    assert sum(out.counts().values()) == out.detected_total
    assert out.counts()[Disposition.SUPPRESSED.value] == 2
