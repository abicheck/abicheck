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
from abicheck.compare.debug_layout_view import debug_layout_view
from abicheck.compare.type_layout_diff import (
    _existing_field_changes,
    _removed_field_changes,
    diff_struct_layouts,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.snapshot_platform_blocks import dwarf_from_dict
from abicheck.storage.dwarf_field_size_migration import (
    DWARF_FIELD_SIZE_GENERATION,
    migrate_dwarf_field_size_unknown,
)
from abicheck.storage.generation_migrations import (
    FACT_GENERATIONS_KEY,
    migrate_and_describe,
)
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


# --- Generation-58 migration: a stored 0 from an older build is unknown ----


def _dwarf_doc(sizes: list[int | None]) -> dict[str, Any]:
    fields = [
        {"name": f"m{i}", "type_name": "int", "byte_offset": 4 * i, "byte_size": s}
        for i, s in enumerate(sizes)
    ]
    layout = {"name": "S", "byte_size": 8, "alignment": 4, "fields": fields}
    return {"structs": {"S": layout}, "enums": {}, "base_types": {}}


def _sizes(doc: dict[str, Any]) -> list[int | None]:
    return [f["byte_size"] for f in doc["structs"]["S"]["fields"]]


def test_migration_reads_stored_zero_as_unknown_before_generation_58() -> None:
    doc = {"dwarf": _dwarf_doc([0, 8, None, 0])}
    migrated, notices = migrate_dwarf_field_size_unknown(doc, 57)
    assert _sizes(migrated["dwarf"]) == [None, 8, None, None]
    assert len(notices) == 1
    # A stored null under a pre-58 stamp (a re-saved migrated document) counts
    # too, so the notice is encoding-independent.
    assert "read 3 stored DWARF member size(s) of 0 as not observed" in notices[0]
    # Never mutated in place: the caller's document keeps its stored zeros.
    assert _sizes(doc["dwarf"]) == [0, 8, None, 0]


def test_migration_is_a_no_op_from_generation_58() -> None:
    doc = {"dwarf": _dwarf_doc([0, 8])}
    migrated, notices = migrate_dwarf_field_size_unknown(
        doc, DWARF_FIELD_SIZE_GENERATION
    )
    assert migrated is doc
    assert notices == []
    assert _sizes(migrated["dwarf"]) == [0, 8]


def test_migration_lowers_odr_conflict_variants_too() -> None:
    dwarf = _dwarf_doc([0])
    dwarf["struct_odr_conflicts"] = {"S": [_dwarf_doc([0])["structs"]["S"]]}
    migrated, _ = migrate_dwarf_field_size_unknown({"dwarf": dwarf}, 57)
    (variant,) = migrated["dwarf"]["struct_odr_conflicts"]["S"]
    assert [f["byte_size"] for f in variant["fields"]] == [None]


def test_load_migrates_a_generation_57_snapshot_and_reports_it() -> None:
    doc = {"dwarf": _dwarf_doc([0, 8])}
    doc[FACT_GENERATIONS_KEY] = {"extractor": 57, "resolver": 2, "source": 0}
    _, _, _, notices = migrate_and_describe(doc)
    assert any("read 1 stored DWARF member size(s) of 0" in n for n in notices)


def _layout_snapshot(size: int | None, generation: int) -> AbiSnapshot:
    layout = StructLayout(
        name="S",
        byte_size=8,
        alignment=4,
        fields=[_field("f", 0, size)],
    )
    snap = AbiSnapshot(
        library="libx.so.1",
        version="1",
        dwarf=DwarfMetadata(structs={"S": layout}),
    )
    doc = snapshot_to_dict(snap)
    doc[FACT_GENERATIONS_KEY] = {"extractor": generation, "resolver": 2, "source": 0}
    return snapshot_from_dict(doc)


def _size_findings(old: AbiSnapshot, new: AbiSnapshot) -> list[str]:
    changes = diff_struct_layouts(
        debug_layout_view(old).layout, debug_layout_view(new).layout
    )
    return [
        c.description for c in changes if c.kind is ChangeKind.STRUCT_FIELD_TYPE_CHANGED
    ]


def test_generation_57_zero_does_not_fabricate_a_size_change() -> None:
    """A stored 0 from a pre-58 baseline is unknown: no 0 -> 8 finding."""
    old = _layout_snapshot(0, generation=57)
    new = _layout_snapshot(8, generation=DWARF_FIELD_SIZE_GENERATION)
    assert _size_findings(old, new) == []


def test_generation_58_zero_to_known_size_fires() -> None:
    """A stored 0 from generation 58 is a real zero: 0 -> 8 is a change."""
    old = _layout_snapshot(0, generation=DWARF_FIELD_SIZE_GENERATION)
    new = _layout_snapshot(8, generation=DWARF_FIELD_SIZE_GENERATION)
    assert _size_findings(old, new) == ["Field type changed: S::f int(0B) → int(8B)"]


def test_generation_57_known_size_still_compares_known() -> None:
    """A pre-58 known size is not touched: 4 -> 8 still fires."""
    old = _layout_snapshot(4, generation=57)
    new = _layout_snapshot(8, generation=DWARF_FIELD_SIZE_GENERATION)
    assert _size_findings(old, new) == ["Field type changed: S::f int(4B) → int(8B)"]
