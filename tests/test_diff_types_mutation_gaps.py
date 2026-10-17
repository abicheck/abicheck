"""Tests that kill mutmut survivors in ``abicheck/diff_types.py``.

Grouped by the five helpers that carried surviving mutants:

* ``_append_enum_scoped_changes`` (enum ``is_scoped`` transitions)
* ``_append_type_abstract_changes`` (record ``is_abstract`` transitions)
* ``_try_match_reserved_field`` (reserved-field-put-into-use matching)
* ``_new_field_change_kind`` (field addition kind for polymorphic types)
* ``_exported_elf_symbol_names`` (ELF dynamic-export name retention)
* ``_diff_removed_field`` (removed-field classification: reserved use,
  rename, type change, or plain removal)

Expected values come from each helper's docstring and the registry
description templates for the emitted kinds, not from running the code.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from abicheck.checker import ChangeKind
from abicheck.diff_types import (
    _append_enum_scoped_changes,
    _append_type_abstract_changes,
    _diff_removed_field,
    _exported_elf_symbol_names,
    _new_field_change_kind,
    _try_match_reserved_field,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, EnumType, Fact, RecordType, TypeField
from abicheck.model.elf_facts import ElfSymbol, SymbolType
from abicheck.model.identity import EntityId, EntityKind

# Each distinct, non-empty entity id; used to tell ``or`` from ``and``.
_ID_A = EntityId(scope=(), kind=EntityKind.TYPE, leaf_name="A")
_ID_B = EntityId(scope=(), kind=EntityKind.TYPE, leaf_name="B")

# Every (old, new) pair where the tri-state flag is unknown on at least one
# side, or both sides agree. None of these may emit a transition.
_NO_TRANSITION_PAIRS = [
    (None, None),
    (None, False),
    (None, True),
    (False, None),
    (True, None),
    (False, False),
    (True, True),
]

# entity_id resolution: ``old or new``.
_ENTITY_ID_CASES = [
    (None, None, None),
    (_ID_A, None, _ID_A),
    (None, _ID_B, _ID_B),
    (_ID_A, _ID_B, _ID_A),
]


# ── _append_enum_scoped_changes ─────────────────────────────────────────────


def _enum(is_scoped: bool | None, entity_id: EntityId | None = None) -> EnumType:
    return EnumType(name="Color", members=[], is_scoped=is_scoped, entity_id=entity_id)


_ENUM_BECAME_SCOPED_DESC = (
    "Enum became scoped: Color — unqualified enumerator lookup and implicit "
    "int conversion no longer compile"
)
_ENUM_LOST_SCOPED_DESC = (
    "Enum lost scoped status: Color — implicit int conversion silently reappears"
)


@pytest.mark.parametrize(("old", "new"), _NO_TRANSITION_PAIRS)
def test_enum_scoped_no_change_when_unknown_or_equal(old, new):
    changes: list = []
    _append_enum_scoped_changes(changes, "Color", _enum(old), _enum(new))
    assert changes == []


def test_enum_became_scoped_fields_are_exact():
    changes: list = []
    _append_enum_scoped_changes(changes, "Color", _enum(False), _enum(True))
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == ChangeKind.ENUM_BECAME_SCOPED
    assert ch.symbol == "Color"
    assert ch.description == _ENUM_BECAME_SCOPED_DESC
    assert ch.old_value == "unscoped"
    assert ch.new_value == "scoped"
    assert ch.entity_id is None


def test_enum_lost_scoped_fields_are_exact():
    changes: list = []
    _append_enum_scoped_changes(changes, "Color", _enum(True), _enum(False))
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == ChangeKind.ENUM_LOST_SCOPED
    assert ch.symbol == "Color"
    assert ch.description == _ENUM_LOST_SCOPED_DESC
    assert ch.old_value == "scoped"
    assert ch.new_value == "unscoped"
    assert ch.entity_id is None


@pytest.mark.parametrize(("old_id", "new_id", "expected"), _ENTITY_ID_CASES)
def test_enum_became_scoped_entity_id_is_old_or_new(old_id, new_id, expected):
    changes: list = []
    _append_enum_scoped_changes(
        changes, "Color", _enum(False, old_id), _enum(True, new_id)
    )
    assert len(changes) == 1
    assert changes[0].entity_id == expected


@pytest.mark.parametrize(("old_id", "new_id", "expected"), _ENTITY_ID_CASES)
def test_enum_lost_scoped_entity_id_is_old_or_new(old_id, new_id, expected):
    changes: list = []
    _append_enum_scoped_changes(
        changes, "Color", _enum(True, old_id), _enum(False, new_id)
    )
    assert len(changes) == 1
    assert changes[0].entity_id == expected


# ── _append_type_abstract_changes ───────────────────────────────────────────


def _rec(is_abstract: bool | None, entity_id: EntityId | None = None) -> RecordType:
    return RecordType(
        name="Shape",
        kind="class",
        size_bits=64,
        is_abstract=is_abstract,
        entity_id=entity_id,
    )


_TYPE_BECAME_ABSTRACT_DESC = (
    "Class became abstract: Shape — direct instantiation no longer compiles"
)
_TYPE_LOST_ABSTRACT_DESC = "Class lost abstract status: Shape"


@pytest.mark.parametrize(("old", "new"), _NO_TRANSITION_PAIRS)
def test_type_abstract_no_change_when_unknown_or_equal(old, new):
    changes: list = []
    _append_type_abstract_changes(changes, "Shape", _rec(old), _rec(new))
    assert changes == []


def test_type_became_abstract_fields_are_exact():
    changes: list = []
    _append_type_abstract_changes(changes, "Shape", _rec(False), _rec(True))
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == ChangeKind.TYPE_BECAME_ABSTRACT
    assert ch.symbol == "Shape"
    assert ch.description == _TYPE_BECAME_ABSTRACT_DESC
    assert ch.old_value == "instantiable"
    assert ch.new_value == "abstract"
    assert ch.entity_id is None


def test_type_lost_abstract_fields_are_exact():
    changes: list = []
    _append_type_abstract_changes(changes, "Shape", _rec(True), _rec(False))
    assert len(changes) == 1
    ch = changes[0]
    assert ch.kind == ChangeKind.TYPE_LOST_ABSTRACT
    assert ch.symbol == "Shape"
    assert ch.description == _TYPE_LOST_ABSTRACT_DESC
    assert ch.old_value == "abstract"
    assert ch.new_value == "instantiable"
    assert ch.entity_id is None


@pytest.mark.parametrize(("old_id", "new_id", "expected"), _ENTITY_ID_CASES)
def test_type_became_abstract_entity_id_is_old_or_new(old_id, new_id, expected):
    changes: list = []
    _append_type_abstract_changes(
        changes, "Shape", _rec(False, old_id), _rec(True, new_id)
    )
    assert len(changes) == 1
    assert changes[0].entity_id == expected


@pytest.mark.parametrize(("old_id", "new_id", "expected"), _ENTITY_ID_CASES)
def test_type_lost_abstract_entity_id_is_old_or_new(old_id, new_id, expected):
    changes: list = []
    _append_type_abstract_changes(
        changes, "Shape", _rec(True, old_id), _rec(False, new_id)
    )
    assert len(changes) == 1
    assert changes[0].entity_id == expected


# ── _try_match_reserved_field ───────────────────────────────────────────────


def _field(name: str, type_: str = "int", offset_bits: int | None = None) -> TypeField:
    return TypeField(name=name, type=type_, offset_bits=offset_bits)


def _index(fields: list[TypeField]):
    by_offset: dict[int, list[TypeField]] = defaultdict(list)
    by_type: dict[str, list[TypeField]] = defaultdict(list)
    for f in fields:
        if f.offset_bits is not None:
            by_offset[f.offset_bits].append(f)
        by_type[f.type].append(f)
    return by_offset, by_type


def _match(
    fname: str,
    f_old: TypeField,
    added: list[TypeField],
    *,
    reserved: set[str] | None = None,
    renamed: set[str] | None = None,
    entity_id: EntityId | None = None,
):
    by_offset, by_type = _index(added)
    return _try_match_reserved_field(
        fname,
        f_old,
        "S",
        by_offset,
        by_type,
        reserved if reserved is not None else set(),
        renamed if renamed is not None else set(),
        entity_id=entity_id,
    )


def test_reserved_match_by_offset_and_type_emits_used_reserved_field():
    f_old = _field("reserved0", offset_bits=64)
    added = [_field("newf", offset_bits=64)]
    reserved: set[str] = set()
    by_offset, by_type = _index(added)
    ch = _try_match_reserved_field(
        "reserved0",
        f_old,
        "S",
        by_offset,
        by_type,
        reserved,
        set(),
        entity_id=_ID_A,
    )
    assert ch is not None
    assert ch.kind == ChangeKind.USED_RESERVED_FIELD
    assert ch.symbol == "S"
    assert ch.description == "Reserved field put into use: S::reserved0 → newf"
    assert ch.old_value == "reserved0"
    assert ch.new_value == "newf"
    assert ch.entity_id == _ID_A
    # The consumed candidate is recorded so a second removal cannot reuse it.
    assert reserved == {"newf"}


def test_reserved_match_without_entity_id_leaves_it_none():
    ch = _match(
        "reserved0",
        _field("reserved0", offset_bits=64),
        [_field("newf", offset_bits=64)],
    )
    assert ch is not None
    assert ch.entity_id is None


@pytest.mark.parametrize(
    ("fname", "f_old", "added", "reserved", "renamed"),
    [
        # Non-reserved removed field: never a reserved-field match.
        ("x", _field("x", offset_bits=64), [_field("n", offset_bits=64)], None, None),
        # Offset set, no offset match, and offset-keyed fallback is skipped.
        (
            "reserved0",
            _field("reserved0", offset_bits=64),
            [_field("n", offset_bits=128)],
            None,
            None,
        ),
        # Offset-matched candidate already consumed by an earlier match.
        (
            "reserved0",
            _field("reserved0", offset_bits=64),
            [_field("n", offset_bits=64)],
            {"n"},
            None,
        ),
        # Offset-matched candidate consumed by a rename.
        (
            "reserved0",
            _field("reserved0", offset_bits=64),
            [_field("n", offset_bits=64)],
            None,
            {"n"},
        ),
        # Candidate is itself a placeholder: not a reserved -> real transition.
        (
            "reserved0",
            _field("reserved0", offset_bits=64),
            [_field("reserved1", offset_bits=64)],
            None,
            None,
        ),
        # DWARF-only fallback where the only same-type candidate is already
        # consumed by a reserved match.
        (
            "reserved0",
            _field("reserved0"),
            [_field("n")],
            {"n"},
            None,
        ),
        # DWARF-only fallback with no same-type added field at all.
        (
            "reserved0",
            _field("reserved0"),
            [_field("n", type_="long")],
            None,
            None,
        ),
        # DWARF-only fallback with nothing added at all.
        ("reserved0", _field("reserved0"), [], None, None),
        # DWARF-only fallback, candidate consumed by a rename.
        (
            "reserved0",
            _field("reserved0"),
            [_field("n")],
            None,
            {"n"},
        ),
    ],
)
def test_reserved_no_match_returns_none(fname, f_old, added, reserved, renamed):
    assert (
        _match(
            fname,
            f_old,
            added,
            reserved=reserved,
            renamed=renamed,
        )
        is None
    )


def test_reserved_offset_fallback_skipped_when_offset_known():
    """Offset known on the removed field: a same-type field at another
    offset must not be matched by type (the fallback is DWARF-only)."""
    ch = _match(
        "reserved0",
        _field("reserved0", offset_bits=64),
        [_field("n", offset_bits=128)],
    )
    assert ch is None


def test_reserved_dwarf_only_fallback_matches_by_type():
    """Offset unknown on the removed field: match the same-type added field."""
    ch = _match("reserved0", _field("reserved0"), [_field("newf")])
    assert ch is not None
    assert ch.kind == ChangeKind.USED_RESERVED_FIELD
    assert ch.new_value == "newf"


def test_reserved_dwarf_only_fallback_skips_consumed_and_takes_next():
    """Fallback loop: a candidate already in the reserved set is skipped and
    the next same-type field is the match (not the first one)."""
    reserved = {"first"}
    ch = _match(
        "reserved0",
        _field("reserved0"),
        [_field("first"), _field("second")],
        reserved=reserved,
    )
    assert ch is not None
    assert ch.new_value == "second"
    assert reserved == {"first", "second"}


def test_reserved_dwarf_only_fallback_skips_renamed_candidate():
    ch = _match(
        "reserved0",
        _field("reserved0"),
        [_field("renamed_one"), _field("plain")],
        renamed={"renamed_one"},
    )
    assert ch is not None
    assert ch.new_value == "plain"


@pytest.mark.parametrize(
    ("reserved", "renamed", "expected"),
    [
        (set(), set(), "newa"),
        ({"newa"}, set(), "newb"),
        (set(), {"newa"}, "newb"),
    ],
)
def test_reserved_fallback_candidate_exclusion_is_or_not_and(
    reserved, renamed, expected
):
    """A candidate is excluded when it is in *either* consumed set."""
    ch = _match(
        "reserved0",
        _field("reserved0"),
        [_field("newa"), _field("newb")],
        # Copies: the helper records consumed names into these sets, and a
        # parametrize literal is shared by every session in one process (mutmut).
        reserved=set(reserved),
        renamed=set(renamed),
    )
    assert ch is not None
    assert ch.new_value == expected


# ── _new_field_change_kind ──────────────────────────────────────────────────


def _record(
    kind: str,
    vtable: Fact[list[str]] | None = None,
    virtual_bases: Fact[list[str]] | None = None,
) -> RecordType:
    return RecordType(
        name="S",
        kind=kind,
        size_bits=64,
        vtable_fact=vtable,
        virtual_bases_fact=virtual_bases,
    )


@pytest.mark.parametrize(
    ("kind", "vtable", "virtual_bases", "expected"),
    [
        # Plain standard-layout struct: compatible addition.
        ("struct", None, None, ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE),
        (
            "struct",
            Fact.present([]),
            Fact.present([]),
            ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ),
        # Non-struct (class) is never the compatible case.
        ("class", None, None, ChangeKind.TYPE_FIELD_ADDED),
        # Polymorphic via vtable alone.
        ("struct", Fact.present(["vt"]), None, ChangeKind.TYPE_FIELD_ADDED),
        # Polymorphic via virtual bases alone.
        ("struct", None, Fact.present(["V"]), ChangeKind.TYPE_FIELD_ADDED),
        # Polymorphic via both.
        (
            "struct",
            Fact.present(["vt"]),
            Fact.present(["V"]),
            ChangeKind.TYPE_FIELD_ADDED,
        ),
    ],
)
def test_new_field_change_kind(kind, vtable, virtual_bases, expected):
    assert _new_field_change_kind(_record(kind, vtable, virtual_bases)) == expected


# ── _exported_elf_symbol_names ──────────────────────────────────────────────


def _snap_with_elf(library: str, symbols: list[ElfSymbol]) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        elf=ElfMetadata(symbols=symbols),
    )


_ELF_SYMBOLS = [
    ElfSymbol(name="foo", sym_type=SymbolType.FUNC),
    ElfSymbol(name="_ZNSt9exceptionD1Ev", sym_type=SymbolType.FUNC),
    ElfSymbol(name="bar", sym_type=SymbolType.OBJECT),
]


def test_exported_names_drop_transitive_runtime_for_ordinary_library():
    """Ordinary library: a transitive libstdc++ export is not its ABI."""
    snap = _snap_with_elf("libfoo.so.1", _ELF_SYMBOLS)
    names = _exported_elf_symbol_names(snap, symbol_types={"func"})
    assert names == {"foo"}


def test_exported_names_keep_stdlib_exports_when_inspecting_cxx_runtime():
    """Inspecting libstdc++ itself: its stdlib exports are the library's own
    ABI surface, so the transitive filter must be off for this snapshot."""
    snap = _snap_with_elf("libstdc++.so.6", _ELF_SYMBOLS)
    names = _exported_elf_symbol_names(snap, symbol_types={"func"})
    assert names == {"foo", "_ZNSt9exceptionD1Ev"}


def test_exported_names_filter_by_symbol_type():
    snap = _snap_with_elf("libstdc++.so.6", _ELF_SYMBOLS)
    assert _exported_elf_symbol_names(snap, symbol_types={"object"}) == {"bar"}


def test_exported_names_empty_without_elf():
    snap = AbiSnapshot(library="libfoo.so.1", version="1.0")
    assert _exported_elf_symbol_names(snap, symbol_types={"func"}) == set()


# ── _diff_removed_field ─────────────────────────────────────────────────────
#
# Contract (docstring + diff_types._diff_record_fields caller): a removed field
# is classified, in order, as a reserved field put into use (USED_RESERVED_FIELD),
# a pure rename at the same offset with an identical type (FIELD_RENAMED), a
# retype at the same offset (TYPE_FIELD_TYPE_CHANGED, the candidate is recorded
# as consumed so it is not also reported as an addition), or otherwise a plain
# TYPE_FIELD_REMOVED. Every emitted Change carries the record's qualified name,
# the removed field name and the compare-time entity id.


def _removed(
    fname: str,
    f_old: TypeField,
    added: list[TypeField],
    *,
    name: str = "S",
    reserved: tuple[str, ...] = (),
    renamed: tuple[str, ...] = (),
    qualified_name: str | None = None,
    entity_id: EntityId | None = None,
):
    """Call ``_diff_removed_field`` with fresh mutable containers per call.

    The function mutates the two consumed-name sets, so they are rebuilt from
    the immutable tuples here rather than shared across pytest sessions.
    """
    by_offset, by_type = _index(added)
    reserved_set = set(reserved)
    renamed_set = set(renamed)
    ch = _diff_removed_field(
        name,
        fname,
        f_old,
        by_offset,
        by_type,
        reserved_set,
        renamed_set,
        qualified_name=qualified_name,
        entity_id=entity_id,
    )
    return ch, reserved_set, renamed_set


@pytest.mark.parametrize(
    ("record", "fname", "description"),
    [
        ("S", "x", "Field removed: S::x"),
        ("Outer::Inner", "count", "Field removed: Outer::Inner::count"),
    ],
)
def test_removed_field_plain_removal_description_is_exact(record, fname, description):
    ch, _, _ = _removed(fname, _field(fname, offset_bits=32), [], name=record)
    assert ch is not None
    assert ch.kind == ChangeKind.TYPE_FIELD_REMOVED
    assert ch.symbol == record
    assert ch.description == description
    assert ch.field_name == fname


def test_removed_field_plain_removal_carries_qualified_name_and_entity_id():
    ch, reserved, renamed = _removed(
        "x",
        _field("x", offset_bits=32),
        [],
        qualified_name="ns::S",
        entity_id=_ID_A,
    )
    assert ch is not None
    assert ch.kind == ChangeKind.TYPE_FIELD_REMOVED
    assert ch.qualified_name == "ns::S"
    assert ch.entity_id == _ID_A
    assert reserved == set()
    assert renamed == set()


def test_removed_field_reserved_put_into_use_carries_entity_id():
    ch, reserved, _ = _removed(
        "reserved0",
        _field("reserved0", offset_bits=64),
        [_field("newf", offset_bits=64)],
        entity_id=_ID_A,
    )
    assert ch is not None
    assert ch.kind == ChangeKind.USED_RESERVED_FIELD
    assert ch.old_value == "reserved0"
    assert ch.new_value == "newf"
    assert ch.entity_id == _ID_A
    assert reserved == {"newf"}


def test_removed_field_reserved_dwarf_only_fallback_matches_by_type():
    # No offsets on either side: the match must come from the by-type index.
    ch, reserved, _ = _removed(
        "reserved0",
        _field("reserved0", type_="int"),
        [_field("newf", type_="int")],
    )
    assert ch is not None
    assert ch.kind == ChangeKind.USED_RESERVED_FIELD
    assert ch.new_value == "newf"
    assert reserved == {"newf"}


def test_removed_field_pure_rename_is_field_renamed_with_entity_id():
    ch, _, renamed = _removed(
        "old_name",
        _field("old_name", offset_bits=32),
        [_field("new_name", offset_bits=32)],
        name="S",
        entity_id=_ID_A,
    )
    assert ch is not None
    assert ch.kind == ChangeKind.FIELD_RENAMED
    assert ch.description == "Field renamed: S::old_name → new_name"
    assert ch.old_value == "old_name"
    assert ch.new_value == "new_name"
    assert ch.entity_id == _ID_A
    assert renamed == {"new_name"}


def test_removed_field_retype_is_type_changed_with_all_identity_fields():
    ch, _, renamed = _removed(
        "old_name",
        _field("old_name", type_="int", offset_bits=32),
        [_field("new_name", type_="long", offset_bits=32)],
        qualified_name="ns::S",
        entity_id=_ID_A,
    )
    assert ch is not None
    assert ch.kind == ChangeKind.TYPE_FIELD_TYPE_CHANGED
    assert ch.description == "Field type changed: S::old_name -> new_name"
    assert ch.old_value == "int"
    assert ch.new_value == "long"
    assert ch.qualified_name == "ns::S"
    assert ch.field_name == "old_name"
    assert ch.entity_id == _ID_A
    # The retyped candidate is consumed, so the caller will not also report
    # it as an added field.
    assert renamed == {"new_name"}


def test_removed_field_consumed_candidates_are_skipped():
    # new_name is already consumed by an earlier match at this offset, so the
    # retype search finds nothing and the removal stands on its own.
    ch, _, renamed = _removed(
        "old_name",
        _field("old_name", type_="int", offset_bits=32),
        [_field("new_name", type_="long", offset_bits=32)],
        renamed=("new_name",),
    )
    assert ch is not None
    assert ch.kind == ChangeKind.TYPE_FIELD_REMOVED
    assert renamed == {"new_name"}
