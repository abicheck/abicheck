"""Tests that kill mutmut survivors in the field-diff helpers of ``abicheck/diff_types.py``.

Covers ``_diff_type_field_pair`` (per-field type/offset/bit-field transitions),
``_diff_type_fields`` (record field-list walk: entity and qualified-name
threading, trailing flexible-array-member skips, reserved-field fallback),
``_index_added_fields`` (added-field indexes) and ``_trailing_fam_name``.

Expected values come from the change-kind description templates (which embed
``{name}``, ``{detail}``, ``{old}``, ``{new}``) and from each helper's
docstring, not from running the code. Parametrize values are plain keys; the
mutable model objects are built inside each test so that no test shares state
with another when mutmut runs several pytest sessions in one process.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind
from abicheck.diff_types import (
    _diff_type_field_pair,
    _diff_type_fields,
    _index_added_fields,
    _trailing_fam_name,
)
from abicheck.model import RecordType, TypeField
from abicheck.model.identity import EntityId, EntityKind


def _eid(key: str | None) -> EntityId | None:
    if key is None:
        return None
    return EntityId(scope=(), kind=EntityKind.TYPE, leaf_name=key)


def _f(
    name: str,
    type_: str,
    offset: int | None = None,
    *,
    is_bitfield: bool = False,
    bitfield_bits: int | None = None,
) -> TypeField:
    return TypeField(
        name=name,
        type=type_,
        offset_bits=offset,
        is_bitfield=is_bitfield,
        bitfield_bits=bitfield_bits,
    )


def _rec(
    fields: list[TypeField],
    *,
    qualified: str | None = None,
    entity: str | None = None,
) -> RecordType:
    return RecordType(
        name="S",
        kind="struct",
        fields=fields,
        qualified_name=qualified,
        entity_id=_eid(entity),
    )


# ── _diff_type_field_pair ───────────────────────────────────────────────────


_PAIR_CASES = [
    # (kind, old field kwargs, new field kwargs, old_value, new_value)
    (
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ("int", None),
        ("long", None),
        "int",
        "long",
    ),
    (
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ("int", 0),
        ("int", 32),
        "0",
        "32",
    ),
    (
        ChangeKind.FIELD_BITFIELD_CHANGED,
        ("unsigned int", 0, True, 3),
        ("unsigned int", 0, True, 5),
        "bits=3",
        "bits=5",
    ),
]


def _pair_fields(spec: tuple) -> TypeField:
    if len(spec) == 2:
        return _f("a", spec[0], spec[1])
    return _f("a", spec[0], spec[1], is_bitfield=spec[2], bitfield_bits=spec[3])


@pytest.mark.parametrize(
    ("kind", "old_spec", "new_spec", "old_v", "new_v"), _PAIR_CASES
)
def test_pair_single_transition_fields_are_exact(
    kind, old_spec, new_spec, old_v, new_v
):
    changes = _diff_type_field_pair(
        "S",
        "a",
        _pair_fields(old_spec),
        _pair_fields(new_spec),
        qualified_name="ns::S",
        entity_id=_eid("A"),
    )
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == kind
    assert ch.symbol == "S"
    assert ch.entity_id == _eid("A")
    assert ch.old_value == old_v
    assert ch.new_value == new_v
    # The description is rendered from {name}::{detail}; a dropped or None
    # name/detail argument shows up as the literal "None".
    assert "S::a" in ch.description
    assert "None" not in ch.description


def test_pair_all_three_transitions_emit_in_order():
    old = _f("a", "int", 0, is_bitfield=True, bitfield_bits=3)
    new = _f("a", "long", 32, is_bitfield=True, bitfield_bits=5)
    kinds = [c.kind for c in _diff_type_field_pair("S", "a", old, new)]
    assert kinds == [
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.FIELD_BITFIELD_CHANGED,
    ]


@pytest.mark.parametrize("entity_key", [None, "A"])
def test_pair_identical_field_emits_nothing(entity_key):
    old = _f("a", "int", 0)
    new = _f("a", "int", 0)
    assert _diff_type_field_pair("S", "a", old, new, entity_id=_eid(entity_key)) == []


def test_pair_unknown_offset_is_not_reported():
    # offset_bits None on either side means "unknown", never an offset change.
    old = _f("a", "int", None)
    new = _f("a", "int", 64)
    assert _diff_type_field_pair("S", "a", old, new) == []


# ── _diff_type_fields: entity and qualified-name threading ──────────────────


_ENTITY_CASES = [
    # (old record entity key, new record entity key, expected key)
    (None, None, None),
    ("A", None, "A"),
    (None, "B", "B"),
    ("A", "B", "A"),
]

_QUALIFIED_CASES = [
    (None, None, None),
    ("ns::Old", None, "ns::Old"),
    (None, "ns::New", "ns::New"),
    ("ns::Old", "ns::New", "ns::New"),
]


@pytest.mark.parametrize(("old_ent", "new_ent", "expected"), _ENTITY_CASES)
def test_record_entity_id_is_old_or_new_for_every_field_change(
    old_ent, new_ent, expected
):
    old = _rec([_f("a", "int", 0), _f("x", "int", 32)], entity=old_ent)
    new = _rec([_f("a", "long", 0)], entity=new_ent)
    changes = _diff_type_fields("S", old, new)
    kinds = {c.kind for c in changes}
    # One type change on "a" (pair path) and one removal of "x" (removal path).
    assert kinds == {ChangeKind.TYPE_FIELD_TYPE_CHANGED, ChangeKind.TYPE_FIELD_REMOVED}
    for ch in changes:
        assert ch.entity_id == _eid(expected)


@pytest.mark.parametrize(("old_ent", "new_ent", "expected"), _ENTITY_CASES)
def test_record_entity_id_threads_into_added_field(old_ent, new_ent, expected):
    old = _rec([_f("a", "int", 0)], entity=old_ent)
    new = _rec([_f("a", "int", 0), _f("b", "int", 32)], entity=new_ent)
    changes = _diff_type_fields("S", old, new)
    assert len(changes) == 1
    assert changes[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
    assert changes[0].entity_id == _eid(expected)


@pytest.mark.parametrize(("old_q", "new_q", "expected"), _QUALIFIED_CASES)
def test_record_qualified_name_is_new_or_old(old_q, new_q, expected):
    old = _rec([_f("a", "int", 0), _f("x", "int", 32)], qualified=old_q)
    new = _rec([_f("a", "long", 0)], qualified=new_q)
    changes = _diff_type_fields("S", old, new)
    assert len(changes) == 2
    for ch in changes:
        assert ch.qualified_name == expected


def test_removed_field_change_is_exact():
    old = _rec([_f("a", "int", 0), _f("x", "int", 32)], qualified="ns::S", entity="A")
    new = _rec([_f("a", "int", 0)], qualified="ns::S", entity="A")
    changes = _diff_type_fields("S", old, new)
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == ChangeKind.TYPE_FIELD_REMOVED
    assert ch.field_name == "x"
    assert ch.symbol == "S"
    assert "S::x" in ch.description
    assert ch.entity_id == _eid("A")
    assert ch.qualified_name == "ns::S"


# ── _diff_type_fields: trailing flexible-array-member handling ──────────────


@pytest.mark.parametrize(
    "fields",
    [
        [("n", "int", 0), ("p", "char []", 32)],
        [("a", "int", 0), ("b", "int", 32), ("p", "char []", 64)],
    ],
)
def test_trailing_fam_name_is_last_field_only(fields):
    built = [_f(n, t, o) for n, t, o in fields]
    assert _trailing_fam_name(built) == "p"


@pytest.mark.parametrize(
    "fields",
    [
        [],
        [("a", "int", 0), ("b", "int", 32)],
        [("p", "char []", 0), ("a", "int", 32)],
    ],
)
def test_trailing_fam_name_none_without_trailing_fam(fields):
    built = [_f(n, t, o) for n, t, o in fields]
    assert _trailing_fam_name(built) is None


def test_removed_trailing_fam_is_not_reported_as_removed_field():
    old = _rec([_f("n", "int", 0), _f("p", "char []", 32)])
    new = _rec([_f("n", "int", 0)])
    changes = _diff_type_fields("S", old, new)
    assert not [c for c in changes if c.field_name == "p"]


def test_added_trailing_fam_is_not_reported_as_added_field():
    old = _rec([_f("n", "int", 0)])
    new = _rec([_f("n", "int", 0), _f("p", "char []", 32)])
    changes = _diff_type_fields("S", old, new)
    assert not [c for c in changes if c.field_name == "p"]


def test_shared_trailing_fam_type_change_is_not_a_field_type_change():
    # Both sides keep "p" as the trailing FAM: its type is handled by the
    # flexible-array-member detector, not by the per-field type comparison.
    old = _rec([_f("n", "int", 0), _f("p", "char []", 32)])
    new = _rec([_f("n", "int", 0), _f("p", "long []", 32)])
    changes = _diff_type_fields("S", old, new)
    assert not [
        c
        for c in changes
        if c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED and c.field_name == "p"
    ]


def test_demoted_trailing_fam_type_change_is_reported():
    # "p" stops being the trailing member, so its type change must be reported.
    old = _rec([_f("n", "int", 0), _f("p", "char []", 32)])
    new = _rec([_f("n", "int", 0), _f("p", "long", 32), _f("e", "int", 64)])
    changes = _diff_type_fields("S", old, new)
    typed = [c for c in changes if c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED]
    assert [c.field_name for c in typed] == ["p"]
    assert typed[0].old_value == "char []"
    assert typed[0].new_value == "long"


def test_promoted_trailing_fam_type_change_is_reported():
    # "p" was an ordinary field and becomes the trailing FAM: the FAM skip
    # needs both sides, so the ordinary type change is still reported.
    old = _rec([_f("n", "int", 0), _f("p", "long", 32)])
    new = _rec([_f("n", "int", 0), _f("p", "char []", 32)])
    changes = _diff_type_fields("S", old, new)
    typed = [c for c in changes if c.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED]
    assert [c.field_name for c in typed] == ["p"]


# ── _diff_type_fields: reserved-field fallback by type ──────────────────────


def test_reserved_field_put_into_use_matches_untyped_added_field():
    # No offsets (DWARF-only shape): the removed reserved slot is matched to
    # the added non-reserved field of the same type.
    old = _rec([_f("reserved0", "int")])
    new = _rec([_f("x", "int")])
    changes = _diff_type_fields("S", old, new)
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == ChangeKind.USED_RESERVED_FIELD
    assert ch.old_value == "reserved0"
    assert ch.new_value == "x"


def test_reserved_field_does_not_match_another_reserved_addition():
    old = _rec([_f("reserved0", "int")])
    new = _rec([_f("reserved1", "int")])
    changes = _diff_type_fields("S", old, new)
    assert ChangeKind.USED_RESERVED_FIELD not in {c.kind for c in changes}
    removed = [c for c in changes if c.kind == ChangeKind.TYPE_FIELD_REMOVED]
    assert [c.field_name for c in removed] == ["reserved0"]


# ── _index_added_fields ─────────────────────────────────────────────────────


def test_index_added_fields_by_offset_and_by_type():
    old_fields = {"a": _f("a", "int", 0)}
    new_fields = {
        "a": _f("a", "int", 0),
        "b": _f("b", "long", 32),
        "reserved0": _f("reserved0", "int", 64),
        "c": _f("c", "int"),
    }
    by_offset, by_type = _index_added_fields(old_fields, new_fields)
    assert {k: [f.name for f in v] for k, v in by_offset.items()} == {
        32: ["b"],
        64: ["reserved0"],
    }
    # Reserved names never serve as fallback candidates; untyped-offset
    # fields still do, and an unchanged field is never indexed.
    assert {k: [f.name for f in v] for k, v in by_type.items()} == {
        "long": ["b"],
        "int": ["c"],
    }
