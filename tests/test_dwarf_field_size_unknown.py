"""Unknown field type sizes are ``None``, never ``0`` (known-gaps: byte_size).

``FieldInfo.byte_size`` is ``int | None``: ``None`` is unknown, ``0`` is a
real size (a flexible array member, a zero-size aggregate). Rules tested for
the whole class, not only the reported input:

* the size rule fires only when both sizes are known and differ, checked
  exhaustively over ``{None, 0, 4, 8}^2`` against an oracle written here, not
  against the implementation's helper;
* rename pairing does not let an unknown size block a pair, checked the same
  way over the same domain;
* the DWARF producer records ``None`` for an unevaluable member type size and a
  real ``0`` for a flexible array of a known element;
* a serialized ``None`` round-trips as ``None``, a stored ``0`` as ``0``, and a
  legacy member with no ``byte_size`` key decodes to ``None``.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from abicheck import dwarf_metadata as dm
from abicheck.compare.type_layout_diff import (
    _existing_field_changes,
    _removed_field_changes,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.dwarf_facts import FieldInfo, StructLayout
from abicheck.snapshot_platform_blocks import dwarf_from_dict
from abicheck.storage.snapshot_encode import _encode_value

_DOMAIN: tuple[int | None, ...] = (None, 0, 4, 8)
_PAIRS = list(itertools.product(_DOMAIN, repeat=2))


def _field(name: str, offset: int, size: int | None) -> FieldInfo:
    return FieldInfo(name=name, type_name="int", byte_offset=offset, byte_size=size)


def _type_size_changes(old: int | None, new: int | None) -> list[Any]:
    changes = _existing_field_changes(
        "S", {"f": _field("f", 0, old)}, {"f": _field("f", 0, new)}
    )
    return [c for c in changes if c.kind is ChangeKind.STRUCT_FIELD_TYPE_CHANGED]


# --- Size rule -------------------------------------------------------------


@pytest.mark.parametrize(("old", "new"), _PAIRS)
def test_size_rule_fires_iff_both_known_and_differ(
    old: int | None, new: int | None
) -> None:
    # Oracle: written independently of the implementation.
    expected = old is not None and new is not None and old != new
    assert bool(_type_size_changes(old, new)) is expected


def test_real_zero_to_known_size_is_a_change() -> None:
    # The gap this closes: a flexible array member growing from 0 to N.
    (change,) = _type_size_changes(0, 8)
    assert change.description == "Field type changed: S::f int(0B) → int(8B)"


def test_unknown_size_never_fabricates_a_change() -> None:
    assert _type_size_changes(None, 8) == []
    assert _type_size_changes(8, None) == []
    assert _type_size_changes(None, 0) == []


# --- Rename pairing --------------------------------------------------------


def _pairs_as_rename(old: int | None, new: int | None) -> bool:
    changes = _removed_field_changes(
        "S",
        {"count": _field("count", 4, old)},
        {"flags": _field("flags", 4, new)},
    )
    kinds = [c.kind for c in changes]
    assert kinds in ([ChangeKind.FIELD_RENAMED], [ChangeKind.STRUCT_FIELD_REMOVED])
    return kinds == [ChangeKind.FIELD_RENAMED]


@pytest.mark.parametrize(("old", "new"), _PAIRS)
def test_rename_pairing_blocked_only_by_two_known_unequal_sizes(
    old: int | None, new: int | None
) -> None:
    # Oracle: an unknown side is compatible; two known sizes must be equal.
    expected = old is None or new is None or old == new
    assert _pairs_as_rename(old, new) is expected


def test_rename_pairs_across_unknown_size_side() -> None:
    assert _pairs_as_rename(None, 4) is True
    assert _pairs_as_rename(4, None) is True


def test_rename_still_blocked_by_known_size_mismatch() -> None:
    assert _pairs_as_rename(0, 4) is False
    assert _pairs_as_rename(4, 8) is False


# --- DWARF producer --------------------------------------------------------


class _Attr:
    def __init__(self, value: Any) -> None:
        self.value = value


class _Die:
    def __init__(
        self,
        tag: str,
        offset: int,
        attrs: dict[str, Any] | None = None,
        children: tuple[_Die, ...] = (),
    ) -> None:
        self.tag = tag
        self.offset = offset
        self.attributes = {k: _Attr(v) for k, v in (attrs or {}).items()}
        self._children = list(children)

    def iter_children(self) -> Any:
        return iter(self._children)


class _CU:
    cu_offset = 0


@pytest.fixture
def _refs(monkeypatch: pytest.MonkeyPatch) -> None:
    # DW_AT_type values hold the target DIE itself, so resolution is identity.
    monkeypatch.setattr(
        dm, "_resolve_ref", lambda die, attr, cu: die.attributes[attr].value
    )


def _size_of(member: _Die) -> int | None:
    return dm._resolve_type(member, _CU(), {})[1]


@pytest.mark.usefixtures("_refs")
def test_member_without_type_attribute_has_unknown_size() -> None:
    assert _size_of(_Die("DW_TAG_member", 1)) is None


@pytest.mark.usefixtures("_refs")
def test_base_type_with_byte_size_has_that_size() -> None:
    int_t = _Die("DW_TAG_base_type", 2, {"DW_AT_name": "int", "DW_AT_byte_size": 4})
    assert _size_of(_Die("DW_TAG_member", 3, {"DW_AT_type": int_t})) == 4


@pytest.mark.usefixtures("_refs")
def test_base_type_without_byte_size_has_unknown_size() -> None:
    opaque = _Die("DW_TAG_base_type", 2, {"DW_AT_name": "opaque"})
    assert _size_of(_Die("DW_TAG_member", 3, {"DW_AT_type": opaque})) is None


@pytest.mark.usefixtures("_refs")
def test_flexible_array_of_known_element_is_real_zero() -> None:
    int_t = _Die("DW_TAG_base_type", 2, {"DW_AT_name": "int", "DW_AT_byte_size": 4})
    flex = _Die(
        "DW_TAG_array_type",
        3,
        {"DW_AT_type": int_t},
        children=(_Die("DW_TAG_subrange_type", 4),),  # no count: unbounded
    )
    assert _size_of(_Die("DW_TAG_member", 5, {"DW_AT_type": flex})) == 0


@pytest.mark.usefixtures("_refs")
def test_array_of_unknown_element_is_unknown_size() -> None:
    opaque = _Die("DW_TAG_base_type", 2, {"DW_AT_name": "opaque"})
    arr = _Die(
        "DW_TAG_array_type",
        3,
        {"DW_AT_type": opaque},
        children=(_Die("DW_TAG_subrange_type", 4, {"DW_AT_count": 4}),),
    )
    assert _size_of(_Die("DW_TAG_member", 5, {"DW_AT_type": arr})) is None


@pytest.mark.usefixtures("_refs")
def test_explicit_zero_byte_size_stays_zero() -> None:
    empty = _Die("DW_TAG_structure_type", 2, {"DW_AT_name": "E", "DW_AT_byte_size": 0})
    assert _size_of(_Die("DW_TAG_member", 3, {"DW_AT_type": empty})) == 0


# --- Snapshot encode / decode ----------------------------------------------


def test_none_round_trips_and_stored_zero_stays_zero() -> None:
    layout = StructLayout(
        name="S",
        byte_size=8,
        alignment=4,
        fields=[_field("a", 0, None), _field("b", 4, 0), _field("c", 8, 4)],
    )
    encoded = json.loads(json.dumps(_encode_value(layout)))
    assert [f["byte_size"] for f in encoded["fields"]] == [None, 0, 4]
    decoded = dwarf_from_dict({"structs": {"S": encoded}}).structs["S"]
    assert [f.byte_size for f in decoded.fields] == [None, 0, 4]


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ({"name": "f", "byte_offset": 0}, None),  # legacy: size key absent
        ({"name": "f", "byte_offset": 0, "byte_size": None}, None),
        ({"name": "f", "byte_offset": 0, "byte_size": 0}, 0),  # stored 0 stays 0
        ({"name": "f", "byte_offset": 0, "byte_size": 8}, 8),
    ],
)
def test_legacy_member_decodes_missing_size_as_unknown(
    member: dict[str, object], expected: int | None
) -> None:
    legacy = {"structs": {"S": {"name": "S", "byte_size": 8, "fields": [member]}}}
    (field,) = dwarf_from_dict(legacy).structs["S"].fields
    assert field.byte_size == expected
