"""The dependency-internal rule moves only findings on unreachable internals.

:func:`exclude_unreachable_dependency_findings` takes each side's
:class:`DependencyInternals`. An added entity is judged on the new side, a
removed one on the old, a modified one on both; a layout kind, a finding
already out of contract, and a symbol the caller declared public are left
alone.
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
    DependencyInternals,
    exclude_unreachable_dependency_findings,
)


@dataclass(frozen=True)
class _Change:
    kind: ChangeKind
    symbol: str
    new_value: str | None = None


@dataclass(frozen=True)
class _Decision:
    relevance: ContractRelevance
    reason_code: str | None
    assurance: ContractAssurance


def _in_contract(reason: str | None = "all_mode_normalized_entity") -> _Decision:
    return _Decision(ContractRelevance.IN_CONTRACT, reason, ContractAssurance.COMPLETE)


_OLD = DependencyInternals(
    types=frozenset({"tag_old", "shared"}),
    typedefs=frozenset({"td_old", "td_shared"}),
    symbols=frozenset({"_Zold", "_Zshared"}),
    overload_stems=frozenset({"stem"}),
)
_NEW = DependencyInternals(
    types=frozenset({"tag_new", "shared"}),
    typedefs=frozenset({"td_new", "td_shared"}),
    symbols=frozenset({"_Znew", "_Zshared"}),
    overload_stems=frozenset({"stem"}),
)


def _run(
    changes: list[_Change], decisions: list[_Decision], is_declared_public=None
) -> list[_Decision]:
    return exclude_unreachable_dependency_findings(
        changes,
        decisions,
        old=_OLD,
        new=_NEW,
        is_declared_public=is_declared_public,
    )


_MOVED = [
    _Change(ChangeKind.TYPE_ADDED, "tag_new"),
    _Change(ChangeKind.TYPE_REMOVED, "tag_old"),
    _Change(ChangeKind.TAG_TYPE_RENAMED, "tag_old", new_value="tag_new"),
    _Change(ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API, "tag_old"),
    _Change(ChangeKind.TYPEDEF_REMOVED, "td_old"),
    _Change(ChangeKind.TYPEDEF_BASE_CHANGED, "td_shared"),
    _Change(ChangeKind.HIDDEN_FRIEND_ADDED, "_Znew"),
    _Change(ChangeKind.HIDDEN_FRIEND_REMOVED, "_Zold"),
    _Change(ChangeKind.VAR_ADDED, "_Znew"),
    _Change(ChangeKind.FUNC_REMOVED_ELF_ONLY, "_Zold"),
    _Change(ChangeKind.FUNC_PARAMS_CHANGED, "_Zshared"),
    _Change(ChangeKind.OVERLOAD_SET_REROUTED, "stem"),
]


@pytest.mark.parametrize("change", _MOVED, ids=lambda c: f"{c.kind.value}:{c.symbol}")
def test_unreachable_internal_findings_are_moved_out_of_contract(
    change: _Change,
) -> None:
    out = _run([change], [_in_contract("public_root_membership")])
    assert out[0].relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
    assert out[0].reason_code == DEPENDENCY_INTERNAL_UNREACHABLE_REASON
    assert out[0].assurance is ContractAssurance.COMPLETE


@pytest.mark.parametrize(
    "change",
    [
        # Added on the new side only: judged against new names, not old.
        _Change(ChangeKind.TYPE_ADDED, "tag_old"),
        # Removed on the old side only: judged against old names, not new.
        _Change(ChangeKind.TYPE_REMOVED, "tag_new"),
        # Not a presence kind: a layout change on the same type is left alone.
        _Change(ChangeKind.TYPE_SIZE_CHANGED, "tag_old"),
        # A rename is judged on both names: the new one is not internal.
        _Change(ChangeKind.TAG_TYPE_RENAMED, "tag_old", new_value="lib_type"),
        # A modified typedef or function must be internal on both sides.
        _Change(ChangeKind.TYPEDEF_BASE_CHANGED, "td_old"),
        _Change(ChangeKind.FUNC_PARAMS_CHANGED, "_Zold"),
        # Symbols and typedefs are separate keyspaces from record names.
        _Change(ChangeKind.TYPE_ADDED, "_Znew"),
        _Change(ChangeKind.FUNC_ADDED, "tag_new"),
        # Not a function or variable entity.
        _Change(ChangeKind.SYMBOL_BINDING_CHANGED, "_Zshared"),
    ],
)
def test_other_findings_keep_their_decision(change: _Change) -> None:
    original = _in_contract()
    out = _run([change], [original])
    assert out[0] == original


def test_declared_public_and_non_entity_decisions_are_never_overridden() -> None:
    public = _in_contract("public_root_membership")
    non_entity = _Decision(NON_ENTITY_RELEVANCE, None, ContractAssurance.COMPLETE)
    declared = _Change(ChangeKind.TYPE_ADDED, "tag_new")
    out = _run(
        [declared, _Change(ChangeKind.TYPE_ADDED, "tag_new")],
        [public, non_entity],
        is_declared_public=lambda change: change is declared,
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
