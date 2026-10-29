# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Namesake findings in ``compare.finding_dedup``: two records or enums that
share a leaf name in different namespaces.

The header tier labels a type finding with its bare leaf, so two namesakes
produce findings that look identical. Each test builds an old/new
``AbiSnapshot`` pair holding the namesakes and states the outcome against an
oracle written here: findings that concern one entity merge into one
survivor; findings that concern two namesakes both survive. The oracle never
calls a ``finding_dedup`` helper.

The domain is the ordered pairs of distinct scopes drawn from a small fixed
set (global, ``a``, ``a::n``, ``b``), all sharing the leaf ``Ctx`` (or
``Color`` for enums). That covers a global namesake next to a namespaced one,
a namespace nested in another, and two siblings.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.finding_dedup import (
    deduplicate_ast_dwarf,
    deduplicate_cross_detector,
)
from abicheck.dwarf_metadata import DwarfMetadata, StructLayout
from abicheck.model import AbiSnapshot, EnumMember, EnumType, RecordType
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
)
from abicheck.model.change_catalog.kinds import ChangeKind as K
from abicheck.model.identity import EntityId, EntityKind, Namespace

Scope = tuple[str, ...]

#: Global, one level deep, two levels deep, and a sibling namespace.
_SCOPES: tuple[Scope, ...] = ((), ("a",), ("a", "n"), ("b",))
#: Every ordered pair of distinct scopes: each is a namesake pair (first, second).
_NAMESAKE_PAIRS: list[tuple[Scope, Scope]] = list(itertools.permutations(_SCOPES, 2))

_CTX = "Ctx"
_COLOR = "Color"


def _qualified(scope: Scope, leaf: str) -> str:
    return "::".join([*scope, leaf])


def _entity(scope: Scope, leaf: str, kind: EntityKind = EntityKind.TYPE) -> EntityId:
    return EntityId(
        scope=tuple(Namespace(name=n) for n in scope), kind=kind, leaf_name=leaf
    )


def _rank(kind: K) -> int:
    """Verdict strictness: 4 breaking, 3 API break, 2 risk, 1 compatible."""
    if kind in BREAKING_KINDS:
        return 4
    if kind in API_BREAK_KINDS:
        return 3
    if kind in RISK_KINDS:
        return 2
    return 1


def _record_snapshot(
    records: list[tuple[Scope, int]], *, leaf: str = _CTX
) -> AbiSnapshot:
    """One record per ``(scope, size in bits)``, under the shared *leaf*.

    The DWARF view carries the same records under their qualified names, the
    way a debug-info producer would, so the cross-tier bridge has both sides.
    """
    structs = {
        _qualified(scope, leaf): StructLayout(
            name=_qualified(scope, leaf), byte_size=bits // 8
        )
        for scope, bits in records
    }
    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        dwarf=DwarfMetadata(has_dwarf=True, structs=structs),
    )
    for scope, bits in records:
        snap.declarations.types.append(
            RecordType(
                name=leaf,
                kind="struct",
                size_bits=bits,
                qualified_name=_qualified(scope, leaf),
                entity_id=_entity(scope, leaf),
            )
        )
    return snap


def _enum_snapshot(scopes: list[Scope]) -> AbiSnapshot:
    snap = AbiSnapshot(library="lib.so", version="1")
    for scope in scopes:
        snap.declarations.enums.append(
            EnumType(
                name=_COLOR,
                qualified_name=_qualified(scope, _COLOR),
                members=[EnumMember(name="RED", value=0)],
                underlying_type="int",
                entity_id=_entity(scope, _COLOR, EntityKind.ENUM),
            )
        )
    return snap


def _type_size(scope: Scope, *, stamp: bool) -> Change:
    """The header tier's size finding: bare symbol, entity identity, and
    (when *stamp*) the producer's qualified spelling."""
    return Change(
        K.TYPE_SIZE_CHANGED,
        _CTX,
        f"{_CTX} size changed",
        "64",
        "128",
        entity_id=_entity(scope, _CTX),
        qualified_name=_qualified(scope, _CTX) if stamp else None,
    )


def _struct_size(scope: Scope) -> Change:
    """The DWARF tier's size finding for the same record (qualified symbol)."""
    return Change(
        K.STRUCT_SIZE_CHANGED,
        _qualified(scope, _CTX),
        f"{_qualified(scope, _CTX)} size changed",
        "8",
        "16",
    )


def _type_field_removed(scope: Scope, *, stamp: bool) -> Change:
    return Change(
        K.TYPE_FIELD_REMOVED,
        _CTX,
        "field x removed",
        field_name="x",
        entity_id=_entity(scope, _CTX),
        qualified_name=_qualified(scope, _CTX) if stamp else None,
    )


def _struct_field_removed(scope: Scope) -> Change:
    return Change(
        K.STRUCT_FIELD_REMOVED,
        f"{_qualified(scope, _CTX)}::x",
        "field x removed",
        field_name="x",
    )


def _entities(changes: list[Change]) -> list[EntityId | None]:
    return [c.entity_id for c in changes]


# ---------------------------------------------------------------------------
# Header-tier namesakes: the exact-key pass keys an ambiguous entity by its
# qualified identity, so identical bare findings of two namesakes both stay.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_identical_findings_of_two_namesakes_both_survive(
    first: Scope, second: Scope
) -> None:
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 128), (second, 128)])
    changes = [_type_size(first, stamp=False), _type_size(second, stamp=False)]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert _entities(survivors) == [_entity(first, _CTX), _entity(second, _CTX)]


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_repeated_finding_of_one_namesake_merges_beside_its_twin(
    first: Scope, second: Scope
) -> None:
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 128), (second, 128)])
    changes = [
        _type_size(first, stamp=False),
        _type_size(first, stamp=False),
        _type_size(second, stamp=False),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert _entities(survivors) == [_entity(first, _CTX), _entity(second, _CTX)]


# ---------------------------------------------------------------------------
# Cross-tier namesakes: a header finding for ``Ctx`` (bare) and a DWARF finding
# for ``a::Ctx`` (qualified) are one observation of the same record. Each
# namesake contributes exactly one survivor, whether or not the header side
# carries its qualified spelling.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stamp", [True, False], ids=["stamped", "unstamped"])
@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_cross_tier_copies_of_namesakes_merge_one_per_record(
    first: Scope, second: Scope, stamp: bool
) -> None:
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 128), (second, 128)])
    changes = [
        _type_size(first, stamp=stamp),
        _type_size(second, stamp=stamp),
        _struct_size(first),
        _struct_size(second),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert [c.kind for c in survivors] == [K.TYPE_SIZE_CHANGED, K.TYPE_SIZE_CHANGED]
    assert _entities(survivors) == [_entity(first, _CTX), _entity(second, _CTX)]


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_dwarf_copy_of_one_namesake_is_not_dropped_for_the_other(
    first: Scope, second: Scope
) -> None:
    # Only the second namesake has a header finding. The DWARF finding for the
    # first has no AST twin, so it must survive; the second's DWARF copy merges.
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 128), (second, 128)])
    changes = [
        _type_size(second, stamp=False),
        _struct_size(first),
        _struct_size(second),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert [c.symbol for c in survivors] == [_CTX, _qualified(first, _CTX)]


@pytest.mark.parametrize("stamp", [True, False], ids=["stamped", "unstamped"])
@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_field_removed_of_namesakes_merges_on_the_parent_identity(
    first: Scope, second: Scope, stamp: bool
) -> None:
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 64), (second, 64)])
    changes = [
        _type_field_removed(first, stamp=stamp),
        _type_field_removed(second, stamp=stamp),
        _struct_field_removed(first),
        _struct_field_removed(second),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert [c.kind for c in survivors] == [K.TYPE_FIELD_REMOVED, K.TYPE_FIELD_REMOVED]
    assert _entities(survivors) == [_entity(first, _CTX), _entity(second, _CTX)]


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_field_removed_on_one_namesake_does_not_absorb_the_others_dwarf_copy(
    first: Scope, second: Scope
) -> None:
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 64), (second, 64)])
    changes = [
        _type_field_removed(second, stamp=False),
        _struct_field_removed(first),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert [c.symbol for c in survivors] == [_CTX, _qualified(first, _CTX) + "::x"]


# ---------------------------------------------------------------------------
# Enum namesakes: same-kind dedup and the cross-detector enum bridge.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_enum_member_removed_of_two_namesakes_both_survive(
    first: Scope, second: Scope
) -> None:
    snap = _enum_snapshot([first, second])
    changes = [
        Change(
            K.ENUM_MEMBER_REMOVED,
            f"{_COLOR}::RED",
            "RED removed",
            entity_id=_entity(first, _COLOR, EntityKind.ENUM),
        ),
        Change(
            K.ENUM_MEMBER_REMOVED,
            f"{_COLOR}::RED",
            "RED removed",
            entity_id=_entity(second, _COLOR, EntityKind.ENUM),
        ),
    ]
    survivors = deduplicate_ast_dwarf(changes, snap, snap)
    assert _entities(survivors) == [
        _entity(first, _COLOR, EntityKind.ENUM),
        _entity(second, _COLOR, EntityKind.ENUM),
    ]


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_enum_cross_tier_copies_bridge_per_namesake(
    first: Scope, second: Scope
) -> None:
    snap = _enum_snapshot([first, second])
    header_first = Change(
        K.ENUM_MEMBER_VALUE_CHANGED,
        f"{_COLOR}::RED",
        "header tier",
        "0",
        "1",
        entity_id=_entity(first, _COLOR, EntityKind.ENUM),
    )
    header_second = Change(
        K.ENUM_MEMBER_VALUE_CHANGED,
        f"{_COLOR}::RED",
        "header tier",
        "0",
        "1",
        entity_id=_entity(second, _COLOR, EntityKind.ENUM),
    )
    dwarf_first = Change(
        K.ENUM_MEMBER_VALUE_CHANGED,
        f"{_qualified(first, _COLOR)}::RED",
        "dwarf tier",
        "0",
        "1",
    )
    dwarf_second = Change(
        K.ENUM_MEMBER_VALUE_CHANGED,
        f"{_qualified(second, _COLOR)}::RED",
        "dwarf tier",
        "0",
        "1",
    )
    survivors = deduplicate_cross_detector(
        [header_first, header_second, dwarf_first, dwarf_second], snap, snap
    )
    # A global enum's label is its bare spelling, so its survivor carries no
    # qualified_name; the identity is then the symbol itself.
    assert [c.qualified_name or c.symbol for c in survivors] == [
        f"{_qualified(first, _COLOR)}::RED",
        f"{_qualified(second, _COLOR)}::RED",
    ]


# ---------------------------------------------------------------------------
# Oracle-level guard: the dedup verdict never weakens. A merged group keeps a
# finding whose verdict is at least that of every finding it absorbed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), _NAMESAKE_PAIRS)
def test_namesake_merge_keeps_the_stricter_verdict(first: Scope, second: Scope) -> None:
    old = _record_snapshot([(first, 64), (second, 64)])
    new = _record_snapshot([(first, 128), (second, 128)])
    changes = [
        _type_size(first, stamp=False),
        _struct_size(first),
        _type_size(second, stamp=False),
        _struct_size(second),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    for scope in (first, second):
        # The findings that concern this namesake: its header finding (by
        # entity) and its DWARF copy (by qualified symbol).
        group = [
            c
            for c in changes
            if c.entity_id == _entity(scope, _CTX)
            or c.symbol == _qualified(scope, _CTX)
        ]
        kept = [c for c in survivors if c.entity_id == _entity(scope, _CTX)]
        assert len(kept) == 1
        assert _rank(kept[0].kind) == max(_rank(c.kind) for c in group)


@pytest.mark.parametrize("scope", _SCOPES)
def test_lone_record_without_a_namesake_still_bridges_its_dwarf_copy(
    scope: Scope,
) -> None:
    # Control for the namesake cases above: with no second declaration sharing
    # the leaf, the bare header spelling bridges to the DWARF qualified copy as
    # it always has, so the record is reported once.
    old = _record_snapshot([(scope, 64)])
    new = _record_snapshot([(scope, 128)])
    changes = [_type_size(scope, stamp=False), _struct_size(scope)]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert [c.kind for c in survivors] == [K.TYPE_SIZE_CHANGED]
    assert _entities(survivors) == [_entity(scope, _CTX)]


# ---------------------------------------------------------------------------
# Ambiguity is counted across both snapshots. A name that is unique on each
# side but names a different declaration on each is still ambiguous to a
# reader of the combined report, so the namesakes must not merge.
# ---------------------------------------------------------------------------


def test_namesakes_split_across_the_two_snapshots_both_survive() -> None:
    # ``a::Ctx`` exists only in the old snapshot and ``b::Ctx`` only in the new.
    old = _record_snapshot([(("a",), 64)])
    new = _record_snapshot([(("b",), 128)])
    changes = [
        _type_size(("a",), stamp=False),
        _type_size(("b",), stamp=False),
    ]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert _entities(survivors) == [_entity(("a",), _CTX), _entity(("b",), _CTX)]


def test_enum_namesakes_split_across_the_two_snapshots_keep_their_own_spelling() -> (
    None
):
    old = _enum_snapshot([("a",)])
    new = _enum_snapshot([("b",)])
    changes = [
        Change(
            K.ENUM_MEMBER_REMOVED,
            f"{_COLOR}::RED",
            "RED removed",
            entity_id=_entity(("a",), _COLOR, EntityKind.ENUM),
        ),
        Change(
            K.ENUM_MEMBER_REMOVED,
            f"{_COLOR}::RED",
            "RED removed",
            entity_id=_entity(("b",), _COLOR, EntityKind.ENUM),
        ),
    ]
    survivors = deduplicate_cross_detector(changes, old, new)
    assert [c.qualified_name for c in survivors] == ["a::Color::RED", "b::Color::RED"]


@pytest.mark.parametrize("side", ["old", "new"])
def test_record_bridge_reads_the_snapshot_that_holds_the_namesake(side: str) -> None:
    # The DWARF copy ``a::Ctx`` pairs with the header finding only through the
    # record bridge, which is built from whichever snapshot declares ``a::Ctx``.
    holder = _record_snapshot([(("a",), 64)])
    empty = AbiSnapshot(library="lib.so", version="1")
    old, new = (holder, empty) if side == "old" else (empty, holder)
    changes = [_type_size(("a",), stamp=False), _struct_size(("a",))]
    survivors = deduplicate_ast_dwarf(changes, old, new)
    assert [c.kind for c in survivors] == [K.TYPE_SIZE_CHANGED]


def test_enum_finding_already_qualified_keeps_its_bridged_spelling() -> None:
    # A single, unambiguous enum whose finding already spells the qualified name:
    # the bridge leaves that spelling in place rather than clearing it.
    snap = _enum_snapshot([("ns",)])
    header = Change(
        K.ENUM_MEMBER_REMOVED,
        "ns::Color::RED",
        "header tier",
        entity_id=_entity(("ns",), _COLOR, EntityKind.ENUM),
    )
    dwarf = Change(K.ENUM_MEMBER_REMOVED, "ns::Color::RED", "dwarf tier")
    survivors = deduplicate_cross_detector([header, dwarf], snap, snap)
    assert [c.qualified_name for c in survivors] == ["ns::Color::RED"]


def test_same_entity_findings_with_different_old_values_both_survive() -> None:
    # Only the old value differs: two observations, not one.
    changes = [
        Change(K.FUNC_ADDED, "f_a", "added", "1", "2"),
        Change(K.FUNC_ADDED, "f_a", "added", "0", "2"),
    ]
    assert len(deduplicate_ast_dwarf(changes)) == 2


# ---------------------------------------------------------------------------
# A field's parent is matched by its record, not by a nested record that shares
# the field's name: ``P::x`` has a field ``x`` of its own, distinct from ``P``'s.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stamp", [True, False], ids=["stamped", "unstamped"])
@pytest.mark.parametrize("dwarf_first", [True, False], ids=["dwarf-first", "ast-first"])
@pytest.mark.parametrize("scope", _SCOPES)
def test_nested_record_field_is_not_absorbed_by_its_enclosing_records_field(
    scope: Scope, dwarf_first: bool, stamp: bool
) -> None:
    enclosing = _type_field_removed(scope, stamp=stamp)
    nested = Change(
        K.STRUCT_FIELD_REMOVED,
        f"{_qualified(scope, _CTX)}::x::x",
        "field x of the nested record removed",
        field_name="x",
    )
    changes = [nested, enclosing] if dwarf_first else [enclosing, nested]
    survivors = deduplicate_ast_dwarf(changes)
    assert [c.symbol for c in survivors] == [c.symbol for c in changes]


@pytest.mark.parametrize(
    ("dwarf_new", "survivor_count"),
    [
        pytest.param("8", 1, id="transitions-agree-one-finding"),
        pytest.param("4", 2, id="transitions-disagree-both-stay"),
    ],
)
@pytest.mark.parametrize("scope", _SCOPES)
def test_field_offset_copies_pair_only_when_their_transitions_agree(
    scope: Scope, dwarf_new: str, survivor_count: int
) -> None:
    # The header offset moves 0 -> 64 bits; the DWARF copy moves 0 -> 8 bytes
    # (= 64 bits, agrees) or 0 -> 4 bytes (= 32 bits, disagrees).
    ast = Change(
        K.TYPE_FIELD_OFFSET_CHANGED,
        _CTX,
        "offset moved",
        "0",
        "64",
        field_name="x",
        entity_id=_entity(scope, _CTX),
    )
    dwarf = Change(
        K.STRUCT_FIELD_OFFSET_CHANGED,
        f"{_qualified(scope, _CTX)}::x",
        "offset moved",
        "0",
        dwarf_new,
        field_name="x",
    )
    snap = _record_snapshot([(scope, 64)])
    survivors = deduplicate_ast_dwarf([ast, dwarf], snap, snap)
    assert len(survivors) == survivor_count
    assert survivors[0].kind is K.TYPE_FIELD_OFFSET_CHANGED
