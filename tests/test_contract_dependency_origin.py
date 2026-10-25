"""The dependency-internal presence rule moves only unreachable presence findings.

:func:`exclude_unreachable_dependency_findings` takes each side's unreachable
dependency names. A ``TYPE_ADDED`` is judged on the new side, a ``TYPE_REMOVED``
on the old, and any other kind, or a finding already out of contract or a
public root, is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from abicheck.contract_relevance_types import (
    NON_ENTITY_RELEVANCE,
    ContractAssurance,
    ContractRelevance,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.contract_dependency_origin import (
    DEPENDENCY_INTERNAL_UNREACHABLE_REASON,
    exclude_unreachable_dependency_findings,
)


@dataclass(frozen=True)
class _Change:
    kind: ChangeKind
    symbol: str


@dataclass(frozen=True)
class _Decision:
    relevance: ContractRelevance
    reason_code: str | None
    assurance: ContractAssurance


def _in_contract(reason: str | None = "all_mode_normalized_entity") -> _Decision:
    return _Decision(ContractRelevance.IN_CONTRACT, reason, ContractAssurance.COMPLETE)


def _run(changes: list[_Change], decisions: list[_Decision]) -> list[_Decision]:
    return exclude_unreachable_dependency_findings(
        changes,
        decisions,
        old_names={"tag_old", "shared"},
        new_names={"tag_new", "shared"},
    )


def test_unreachable_added_and_removed_types_are_moved_out_of_contract() -> None:
    changes = [
        _Change(ChangeKind.TYPE_ADDED, "tag_new"),
        _Change(ChangeKind.TYPE_REMOVED, "tag_old"),
    ]
    out = _run(changes, [_in_contract(), _in_contract()])
    assert [d.relevance for d in out] == [ContractRelevance.PROVEN_OUT_OF_CONTRACT] * 2
    assert [d.reason_code for d in out] == [DEPENDENCY_INTERNAL_UNREACHABLE_REASON] * 2
    assert all(d.assurance is ContractAssurance.COMPLETE for d in out)


@pytest.mark.parametrize(
    "change",
    [
        # Added on the new side only: judged against new names, not old.
        _Change(ChangeKind.TYPE_ADDED, "tag_old"),
        # Removed on the old side only: judged against old names, not new.
        _Change(ChangeKind.TYPE_REMOVED, "tag_new"),
        # Not a presence kind: a layout change on the same type is left alone.
        _Change(ChangeKind.TYPE_SIZE_CHANGED, "tag_old"),
    ],
)
def test_other_findings_keep_their_decision(change: _Change) -> None:
    original = _in_contract()
    out = _run([change], [original])
    assert out[0] == original


def test_public_root_and_non_entity_decisions_are_never_overridden() -> None:
    public = _in_contract("public_root_membership")
    non_entity = _Decision(NON_ENTITY_RELEVANCE, None, ContractAssurance.COMPLETE)
    out = _run(
        [
            _Change(ChangeKind.TYPE_ADDED, "tag_new"),
            _Change(ChangeKind.TYPE_ADDED, "tag_new"),
        ],
        [public, non_entity],
    )
    assert out == [public, non_entity]


def test_an_already_excluded_finding_keeps_its_reason() -> None:
    toolchain = _Decision(
        ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        "toolchain_runtime_origin",
        ContractAssurance.COMPLETE,
    )
    out = _run([_Change(ChangeKind.TYPE_ADDED, "tag_new")], [toolchain])
    assert out == [toolchain]
