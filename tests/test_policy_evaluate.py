# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``policy.evaluate`` classifies hand-built ``DiffResult`` facts.

No comparison runs here: every ``DiffResult`` is constructed directly, so a
failure points at policy evaluation alone. The oracle is the kind registry's
own ``default_verdict`` (plus explicit expected tables for overrides), never
the implementation's helpers.
"""

from __future__ import annotations

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.checker_types import DiffResult
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.evaluate import (
    ClassifiedDiff,
    effective_verdict,
    evaluate,
    evaluated_changes,
)
from abicheck.policy_file import PolicyFile

_BUCKET_OF = {
    Verdict.BREAKING: "breaking",
    Verdict.API_BREAK: "source_breaks",
    Verdict.COMPATIBLE: "compatible",
    Verdict.COMPATIBLE_WITH_RISK: "risk",
}


def _diff(changes: list[Change], **kw: object) -> DiffResult:
    return DiffResult(
        old_version="1", new_version="2", library="libx.so", changes=changes, **kw
    )


def _change(kind: ChangeKind, symbol: str = "sym", **kw: object) -> Change:
    return Change(kind, symbol, f"{kind.value} on {symbol}", **kw)


def _registry_verdict(kind: ChangeKind) -> Verdict:
    meta = REGISTRY.get(kind.value)
    assert meta is not None
    return meta.default_verdict


def _bucket_names(classified: ClassifiedDiff, change: Change) -> list[str]:
    return [
        name
        for name in ("breaking", "source_breaks", "compatible", "risk", "not_evaluated")
        if any(c is change for c in getattr(classified, name))
    ]


@pytest.mark.parametrize("kind", list(ChangeKind), ids=lambda k: k.value)
def test_every_kind_lands_in_its_registry_bucket_under_strict_abi(
    kind: ChangeKind,
) -> None:
    change = _change(kind)
    expected = _registry_verdict(kind)
    classified = evaluate(_diff([change]))
    if expected in _BUCKET_OF:
        assert _bucket_names(classified, change) == [_BUCKET_OF[expected]]
    else:
        assert _bucket_names(classified, change) == []
    assert effective_verdict(_diff([change]), change) == expected


def test_buckets_partition_a_mixed_diff_in_changes_order() -> None:
    kinds = [
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_ADDED,
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_ADDED,
    ]
    changes = [_change(k, f"s{i}") for i, k in enumerate(kinds)]
    classified = evaluate(_diff(changes))
    assert classified.breaking == [changes[0], changes[2]]
    assert classified.compatible == [changes[1], changes[3]]
    assert classified.source_breaks == classified.risk == []


def test_policy_file_override_moves_a_kind_to_another_bucket() -> None:
    change = _change(ChangeKind.FUNC_REMOVED)
    pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
    classified = evaluate(_diff([change], policy_file=pf))
    assert classified.breaking == []
    assert classified.compatible == [change]


def test_frozen_namespace_guard_beats_a_demoting_override() -> None:
    change = _change(
        ChangeKind.FUNC_REMOVED, frozen_namespace_violation="**::detail::r1::*"
    )
    pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
    assert evaluate(_diff([change], policy_file=pf)).breaking == [change]


def test_per_finding_effective_verdict_wins_over_kind_category() -> None:
    change = _change(ChangeKind.FUNC_REMOVED, effective_verdict=Verdict.API_BREAK)
    classified = evaluate(_diff([change]))
    assert classified.breaking == []
    assert classified.source_breaks == [change]


@pytest.mark.parametrize(
    "relevance",
    [
        ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        ContractRelevance.UNKNOWN_UNPROVEN,
        ContractRelevance.UNKNOWN_UNRESOLVED,
    ],
)
def test_not_evaluated_findings_are_in_no_verdict_bucket(
    relevance: ContractRelevance,
) -> None:
    out = _change(ChangeKind.FUNC_REMOVED, "out", contract_relevance=relevance)
    scored = _change(ChangeKind.FUNC_REMOVED, "in")
    diff = _diff([out, scored])
    classified = evaluate(diff)
    assert classified.breaking == [scored]
    assert classified.not_evaluated == [out] == diff.not_evaluated
    assert evaluated_changes(diff) == [scored]


def test_evaluation_reads_the_current_diff_not_a_cached_one() -> None:
    """A caller that edits the result after classifying must see the edit."""
    change = _change(ChangeKind.FUNC_REMOVED)
    diff = _diff([change])
    assert evaluate(diff).breaking == [change]
    diff.policy_file = PolicyFile(
        overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE}
    )
    assert evaluate(diff).breaking == []
    diff.changes = []
    assert evaluate(diff) == ClassifiedDiff([], [], [], [], [])
