"""P1: vtable reordering severity (abicc #66).

Explicit severity test: TYPE_VTABLE_CHANGED must be in BREAKING_KINDS.
Tests the relationship between vtable changes and BREAKING verdict.

This supplements the existing TestVtableReorderingSeverity in test_issues_e1_e4.py
with more granular severity-focused tests.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.model import AbiSnapshot, Function, Param, RecordType


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


class TestVtableSeverity:
    """TYPE_VTABLE_CHANGED must always be BREAKING (abicc #66)."""

    def test_type_vtable_changed_in_breaking_kinds(self) -> None:
        """TYPE_VTABLE_CHANGED must be in BREAKING_KINDS set."""
        assert ChangeKind.TYPE_VTABLE_CHANGED in BREAKING_KINDS

    def test_vtable_reorder_verdict_breaking(self) -> None:
        """Reordering vtable entries → BREAKING verdict."""
        old = _snap(types=[RecordType(
            name="Base", kind="class",
            vtable=["_ZN4Base4drawEv", "_ZN4Base6resizeEv"],
        )])
        new = _snap(types=[RecordType(
            name="Base", kind="class",
            vtable=["_ZN4Base6resizeEv", "_ZN4Base4drawEv"],
        )])
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in {c.kind for c in result.changes}
        assert result.verdict == Verdict.BREAKING

    def test_vtable_entry_removed_is_breaking(self) -> None:
        """Removing a vtable entry → BREAKING."""
        old = _snap(types=[RecordType(
            name="Widget", kind="class",
            vtable=["_ZN6Widget4drawEv", "_ZN6Widget5paintEv"],
        )])
        new = _snap(types=[RecordType(
            name="Widget", kind="class",
            vtable=["_ZN6Widget4drawEv"],
        )])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_vtable_entry_added_is_breaking(self) -> None:
        """Adding a vtable entry shifts indices of subsequent entries → BREAKING."""
        old = _snap(types=[RecordType(
            name="Widget", kind="class",
            vtable=["_ZN6Widget4drawEv"],
        )])
        new = _snap(types=[RecordType(
            name="Widget", kind="class",
            vtable=["_ZN6Widget4drawEv", "_ZN6Widget5paintEv"],
        )])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_vtable_unchanged_no_change(self) -> None:
        """Identical vtable → no TYPE_VTABLE_CHANGED emitted."""
        old = _snap(types=[RecordType(
            name="Engine", kind="class",
            vtable=["_ZN6Engine4initEv", "_ZN6Engine3runEv"],
        )])
        new = _snap(types=[RecordType(
            name="Engine", kind="class",
            vtable=["_ZN6Engine4initEv", "_ZN6Engine3runEv"],
        )])
        result = compare(old, new)
        assert not result.changes

    def test_vtable_change_kind_value(self) -> None:
        """TYPE_VTABLE_CHANGED enum value is 'type_vtable_changed'."""
        assert ChangeKind.TYPE_VTABLE_CHANGED.value == "type_vtable_changed"


class TestVtableOverrideSlotReuse:
    """case185: an override that reuses its base's slot must not fire
    TYPE_VTABLE_CHANGED, even though the slot's mangled entry renames from
    base to derived. Mirrors diff_cxx_rules.virtual_method_addition()'s own
    exemption for the identical relationship
    (diff_cxx_rules.vtable_slot_is_override_reuse)."""

    def test_same_signature_override_reusing_slot_is_not_vtable_changed(self) -> None:
        old = _snap(
            types=[RecordType(
                name="Derived", kind="class", bases=["Base"],
                vtable=["_ZN4Base5paintEi"],
            )],
            functions=[Function(
                name="Base::paint", mangled="_ZN4Base5paintEi",
                return_type="int", params=[Param(name="x", type="int")],
                is_virtual=True,
            )],
        )
        new = _snap(
            types=[RecordType(
                name="Derived", kind="class", bases=["Base"],
                vtable=["_ZN7Derived5paintEi"],
            )],
            functions=[Function(
                name="Derived::paint", mangled="_ZN7Derived5paintEi",
                return_type="int", params=[Param(name="x", type="int")],
                is_virtual=True,
            )],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED not in kinds

    def test_different_signature_same_name_still_fires(self) -> None:
        """The negative twin: same method name but a different parameter
        list has no matching virtual_signature_key, so it's a genuine new
        slot, not a reuse -- must still be reported."""
        old = _snap(
            types=[RecordType(
                name="Derived", kind="class", bases=["Base"],
                vtable=["_ZN4Base5paintEi"],
            )],
            functions=[Function(
                name="Base::paint", mangled="_ZN4Base5paintEi",
                return_type="int", params=[Param(name="x", type="int")],
                is_virtual=True,
            )],
        )
        new = _snap(
            types=[RecordType(
                name="Derived", kind="class", bases=["Base"],
                vtable=["_ZN4Base5paintEi", "_ZN7Derived5paintEd"],
            )],
            functions=[
                Function(
                    name="Base::paint", mangled="_ZN4Base5paintEi",
                    return_type="int", params=[Param(name="x", type="int")],
                    is_virtual=True,
                ),
                Function(
                    name="Derived::paint", mangled="_ZN7Derived5paintEd",
                    return_type="int", params=[Param(name="x", type="double")],
                    is_virtual=True,
                ),
            ],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_VTABLE_CHANGED in kinds
