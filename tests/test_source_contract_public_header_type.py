"""A source-contract finding on a public-header type no export reaches.

Bug class: a header-only entity (a class template with no exported
instantiation) is declared in a public header, so consumer source names it,
but no exported function reaches it. Public-surface reachability called it
``non-public-type`` and the contract evaluator excluded the finding as out of
contract, so removing or changing the template's default argument scored
``NO_CHANGE`` once the header graph made the surface resolvable.

Oracle, from the C++ rule rather than the implementation: a consumer compiles
against what a public header declares, whether or not the binary exports
anything that mentions it. So an API (source) break on a type whose origin is
the public header is in the ``public`` contract; the same unreachable type
from a private header stays excluded, and a binary-only (``BREAKING``) kind
on the unreachable public-header type keeps the reachability answer.
"""

from __future__ import annotations

import pytest

from abicheck.contract_evaluation import evaluate_change_contract_relevance
from abicheck.contract_relevance_types import ContractMode, ContractRelevance
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import API_BREAK_KINDS, BREAKING_KINDS
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.policy.public_surface_closure import resolve_public_surface


def _surface(unreached_origin: ScopeOrigin):
    # One exported, typed public function (so the surface has typed roots) and
    # a record nothing exported reaches.
    api = Function(
        name="api",
        mangled="_Z3apii",
        return_type="int",
        params=[Param(name="a", type="int")],
        visibility=Visibility.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    used = RecordType(
        name="Used", kind="struct", size_bits=32, origin=ScopeOrigin.PUBLIC_HEADER
    )
    unreached = RecordType(
        name="Buf", kind="struct", size_bits=32, origin=unreached_origin
    )
    snap = AbiSnapshot(
        library="l", version="1", functions=[api], types=[used, unreached]
    )
    return resolve_public_surface(snap)


# Three independently chosen source-contract kinds on a type subject.
_SOURCE_KINDS = (
    ChangeKind.TEMPLATE_PARAM_DEFAULT_CHANGED,
    ChangeKind.TYPE_REMOVED,
    ChangeKind.FIELD_RENAMED,
)


@pytest.mark.parametrize("kind", _SOURCE_KINDS, ids=lambda k: k.value)
def test_source_break_on_public_header_type_is_in_contract(kind: ChangeKind) -> None:
    assert kind in API_BREAK_KINDS
    s = _surface(ScopeOrigin.PUBLIC_HEADER)
    decision = evaluate_change_contract_relevance(
        Change(kind=kind, symbol="Buf", description=""), s, s, mode=ContractMode.PUBLIC
    )
    assert decision.relevance is ContractRelevance.IN_CONTRACT


@pytest.mark.parametrize("kind", _SOURCE_KINDS, ids=lambda k: k.value)
def test_source_break_on_private_header_type_stays_excluded(kind: ChangeKind) -> None:
    s = _surface(ScopeOrigin.PRIVATE_HEADER)
    decision = evaluate_change_contract_relevance(
        Change(kind=kind, symbol="Buf", description=""), s, s, mode=ContractMode.PUBLIC
    )
    assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT


def test_binary_break_on_unreached_public_header_type_keeps_reachability() -> None:
    kind = ChangeKind.TYPE_SIZE_CHANGED
    assert kind in BREAKING_KINDS
    s = _surface(ScopeOrigin.PUBLIC_HEADER)
    decision = evaluate_change_contract_relevance(
        Change(kind=kind, symbol="Buf", description=""), s, s, mode=ContractMode.PUBLIC
    )
    assert decision.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT
