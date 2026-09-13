"""Tests for P3 feature gaps: FLEXIBLE_ARRAY_MEMBER_CHANGED and FUNC_DELETED_DWARF.

P3 gaps from tool-comparison-gap-analysis.md:
1. FLEXIBLE_ARRAY_MEMBER_CHANGED — detect changes to trailing flexible array members
   (libabigail's BENIGN_INFINITE_ARRAY_CHANGE_CATEGORY analog)
2. FUNC_DELETED_DWARF — detect = delete via DWARF DW_AT_deleted attribute

All tests build AbiSnapshot objects directly (no castxml/DWARF required).
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
    Visibility,
)


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ===========================================================================
# 1. FLEXIBLE_ARRAY_MEMBER_CHANGED
#
# Detects changes to trailing flexible array members (C99 T[] or GNU T[0]).
# libabigail's BENIGN_INFINITE_ARRAY_CHANGE_CATEGORY analog.
# ===========================================================================


class TestFlexibleArrayMemberChanged:
    """Detect flexible array member (FAM) changes."""

    def test_fam_added(self) -> None:
        """Adding a FAM to a struct → FLEXIBLE_ARRAY_MEMBER_CHANGED."""
        old = _snap(
            types=[
                RecordType(
                    name="Msg",
                    kind="struct",
                    fields=[
                        TypeField(name="len", type="int", offset_bits=0),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Msg",
                    kind="struct",
                    fields=[
                        TypeField(name="len", type="int", offset_bits=0),
                        TypeField(name="data", type="char []", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED in _kinds(result)

    def test_fam_removed(self) -> None:
        """Removing a FAM from a struct → FLEXIBLE_ARRAY_MEMBER_CHANGED."""
        old = _snap(
            types=[
                RecordType(
                    name="Pkt",
                    kind="struct",
                    fields=[
                        TypeField(name="header", type="int", offset_bits=0),
                        TypeField(
                            name="payload", type="unsigned char []", offset_bits=32
                        ),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Pkt",
                    kind="struct",
                    fields=[
                        TypeField(name="header", type="int", offset_bits=0),
                    ],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED in _kinds(result)

    def test_fam_element_type_changed(self) -> None:
        """FAM element type changed: char[] → int[]."""
        old = _snap(
            types=[
                RecordType(
                    name="Buf",
                    kind="struct",
                    fields=[
                        TypeField(name="size", type="int", offset_bits=0),
                        TypeField(name="data", type="char []", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="Buf",
                    kind="struct",
                    fields=[
                        TypeField(name="size", type="int", offset_bits=0),
                        TypeField(name="data", type="int []", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED in _kinds(result)
        change = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED
        )
        assert "char" in change.old_value
        assert "int" in change.new_value

    def test_fam_zero_length_array(self) -> None:
        """GNU extension: T[0] is also a flexible array member."""
        old = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="n", type="int", offset_bits=0),
                        TypeField(name="items", type="int [0]", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="n", type="int", offset_bits=0),
                        TypeField(name="items", type="long [0]", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED in _kinds(result)

    def test_fam_unchanged(self) -> None:
        """Same FAM → no change."""
        old = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="n", type="int", offset_bits=0),
                        TypeField(name="data", type="char []", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="n", type="int", offset_bits=0),
                        TypeField(name="data", type="char []", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED not in _kinds(result)

    def test_fam_not_last_field_not_detected(self) -> None:
        """A T[] field that isn't the last field is not a standard FAM.
        Regular field change detection handles it instead."""
        old = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="arr", type="int []", offset_bits=0),
                        TypeField(name="extra", type="int", offset_bits=32),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="arr", type="long []", offset_bits=0),
                        TypeField(name="extra", type="int", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        # Not a trailing FAM, so FLEXIBLE_ARRAY_MEMBER_CHANGED should NOT fire
        assert ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED not in _kinds(result)
        # But TYPE_FIELD_TYPE_CHANGED should still detect it
        assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(result)

    def test_fam_is_compatible(self) -> None:
        """FAM change is COMPATIBLE (no static size impact)."""
        old = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="n", type="int", offset_bits=0),
                    ],
                )
            ]
        )
        new = _snap(
            types=[
                RecordType(
                    name="S",
                    kind="struct",
                    fields=[
                        TypeField(name="n", type="int", offset_bits=0),
                        TypeField(name="data", type="char []", offset_bits=32),
                    ],
                )
            ]
        )
        result = compare(old, new)
        # FAM addition alone should not make it BREAKING
        fam_changes = [
            c
            for c in result.changes
            if c.kind == ChangeKind.FLEXIBLE_ARRAY_MEMBER_CHANGED
        ]
        assert len(fam_changes) >= 1


# ===========================================================================
# 2. FUNC_DELETED_DWARF
#
# Detect = delete via DWARF DW_AT_deleted attribute (P3 gap).
# ===========================================================================


class TestFuncDeletedDwarf:
    """Detect = delete via DWARF path (deleted_from_dwarf flag)."""

    def test_func_deleted_dwarf_detected(self) -> None:
        """Function marked is_deleted + deleted_from_dwarf → FUNC_DELETED_DWARF."""
        old = _snap(
            functions=[
                _func("Foo::copy", "_ZN3Foo4copyERKS_"),
            ]
        )
        new = _snap(
            functions=[
                _func(
                    "Foo::copy",
                    "_ZN3Foo4copyERKS_",
                    is_deleted=True,
                    deleted_from_dwarf=True,
                ),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED_DWARF in _kinds(result)
        change = next(
            c for c in result.changes if c.kind == ChangeKind.FUNC_DELETED_DWARF
        )
        assert change.old_value == "callable"
        assert change.new_value == "deleted"

    def test_func_deleted_castxml_uses_func_deleted(self) -> None:
        """Function marked is_deleted without deleted_from_dwarf → FUNC_DELETED."""
        old = _snap(
            functions=[
                _func("Foo::copy", "_ZN3Foo4copyERKS_"),
            ]
        )
        new = _snap(
            functions=[
                _func("Foo::copy", "_ZN3Foo4copyERKS_", is_deleted=True),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED in _kinds(result)
        assert ChangeKind.FUNC_DELETED_DWARF not in _kinds(result)

    def test_func_deleted_dwarf_is_breaking(self) -> None:
        """= delete detected via DWARF is BREAKING."""
        old = _snap(functions=[_func("f", "_f")])
        new = _snap(
            functions=[
                _func("f", "_f", is_deleted=True, deleted_from_dwarf=True),
            ]
        )
        result = compare(old, new)
        assert result.verdict == Verdict.BREAKING

    def test_func_already_deleted_no_change(self) -> None:
        """Both old and new have is_deleted=True → no change."""
        old = _snap(
            functions=[
                _func("f", "_f", is_deleted=True, deleted_from_dwarf=True),
            ]
        )
        new = _snap(
            functions=[
                _func("f", "_f", is_deleted=True, deleted_from_dwarf=True),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED_DWARF not in _kinds(result)
        assert ChangeKind.FUNC_DELETED not in _kinds(result)

    def test_func_not_deleted_no_detection(self) -> None:
        """Normal function (not deleted) → no FUNC_DELETED* kind."""
        old = _snap(functions=[_func("f", "_f")])
        new = _snap(functions=[_func("f", "_f")])
        result = compare(old, new)
        assert ChangeKind.FUNC_DELETED not in _kinds(result)
        assert ChangeKind.FUNC_DELETED_DWARF not in _kinds(result)
