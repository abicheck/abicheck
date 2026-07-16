"""P1: vtable reordering severity (abicc #66).

Explicit severity test: TYPE_VTABLE_CHANGED must be in BREAKING_KINDS.
Tests the relationship between vtable changes and BREAKING verdict.

This supplements the existing TestVtableReorderingSeverity in test_issues_e1_e4.py
with more granular severity-focused tests.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.diff_cxx_rules import _owner_descends_from, vtable_slot_is_override_reuse
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

    def test_same_signature_unrelated_owner_not_treated_as_reuse(self) -> None:
        """A signature match alone must not suppress the change: a vtable
        entry swapping to a same-signature virtual from a class that is NOT
        in Derived's hierarchy is a genuine, unrelated slot replacement, not
        an override-reuse. Guards against a same-name/params collision
        between two unrelated hierarchies falsely reading as compatible.

        Verified at the helper level rather than via full compare(): this
        scenario (one symbol removed, one added, nothing else) is also where
        diff_filtering.py's unrelated add/remove-pair dedup independently
        collapses the end-to-end output before a suppressed-or-not
        TYPE_VTABLE_CHANGED would be observable either way, so a
        compare()-level assertion wouldn't isolate this specific guard.
        """
        old_funcs = {"_ZN4Base5paintEi": Function(
            name="Base::paint", mangled="_ZN4Base5paintEi",
            return_type="int", params=[Param(name="x", type="int")], is_virtual=True,
        )}
        new_funcs = {"_ZN6Other5paintEi": Function(
            name="Other::paint", mangled="_ZN6Other5paintEi",
            return_type="int", params=[Param(name="x", type="int")], is_virtual=True,
        )}
        assert not vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi", "_ZN6Other5paintEi", old_funcs, new_funcs, {}, {},
        )

    def test_sibling_base_same_signature_not_treated_as_reuse(self) -> None:
        """Both owners can independently sit somewhere in the diffed class's
        base set without one genuinely overriding the other: a class with
        sibling bases (Derived : Base1, Base2), or one whose base list itself
        changed (Derived : Base1 -> Derived : Base2), could have a slot swap
        from Base1::foo() to an unrelated, same-signature Base2::foo()
        without either being an override of the other. Base2 does not
        descend from Base1, so this must not be treated as a reuse.
        """
        old_funcs = {"_ZN5Base14fooEv": Function(
            name="Base1::foo", mangled="_ZN5Base14fooEv",
            return_type="void", is_virtual=True,
        )}
        new_funcs = {"_ZN5Base24fooEv": Function(
            name="Base2::foo", mangled="_ZN5Base24fooEv",
            return_type="void", is_virtual=True,
        )}
        old_types = {"Derived": RecordType(
            name="Derived", kind="class", bases=["Base1"], vtable=["_ZN5Base14fooEv"],
        )}
        new_types = {"Derived": RecordType(
            name="Derived", kind="class", bases=["Base2"], vtable=["_ZN5Base24fooEv"],
        )}
        assert not vtable_slot_is_override_reuse(
            "_ZN5Base14fooEv", "_ZN5Base24fooEv", old_funcs, new_funcs, old_types, new_types,
        )

    def test_identical_slot_entry_is_trivially_a_reuse(self) -> None:
        """The old_entry == new_entry fast path: an unchanged slot is
        trivially a 'reuse' (nothing to suppress a real change for)."""
        assert vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi", "_ZN4Base5paintEi", {}, {}, {}, {},
        )

    def test_different_signature_returns_false_directly(self) -> None:
        """virtual_signature_key mismatch short-circuits to False, independent
        of owner/hierarchy -- exercised directly since a differing vtable
        length (as in the compare()-level negative-twin test) never reaches
        this helper at all (_diff_type_vtable only calls it when both
        vtables are the same length)."""
        old_funcs = {"_ZN4Base5paintEi": Function(
            name="Base::paint", mangled="_ZN4Base5paintEi",
            return_type="int", params=[Param(name="x", type="int")], is_virtual=True,
        )}
        new_funcs = {"_ZN7Derived5paintEd": Function(
            name="Derived::paint", mangled="_ZN7Derived5paintEd",
            return_type="int", params=[Param(name="x", type="double")], is_virtual=True,
        )}
        assert not vtable_slot_is_override_reuse(
            "_ZN4Base5paintEi", "_ZN7Derived5paintEd", old_funcs, new_funcs, {}, {},
        )

    def test_unresolvable_owner_returns_false(self) -> None:
        """A Function whose owner can't be determined (no '::' in its name
        and an unparseable mangled symbol) must not be treated as a reuse --
        there is nothing to verify an override edge against."""
        old_funcs = {"paint": Function(
            name="paint", mangled="not_a_mangled_name",
            return_type="int", params=[Param(name="x", type="int")], is_virtual=True,
        )}
        new_funcs = {"paint2": Function(
            name="paint", mangled="also_not_mangled",
            return_type="int", params=[Param(name="x", type="int")], is_virtual=True,
        )}
        assert not vtable_slot_is_override_reuse(
            "paint", "paint2", old_funcs, new_funcs, {}, {},
        )


class TestOwnerDescendsFrom:
    """Direct coverage of diff_cxx_rules._owner_descends_from()'s branches."""

    def test_owner_equals_ancestor(self) -> None:
        assert _owner_descends_from("Base", "Base", {})

    def test_leaf_names_match_across_qualification(self) -> None:
        """A qualified owner and a bare-leaf ancestor with the same leaf
        component are treated as the same class (CastXML records bases as
        bare leaves; DWARF records the qualified form)."""
        assert _owner_descends_from("ns::Base", "Base", {})

    def test_unrelated_leaf_and_unresolvable_type_returns_false(self) -> None:
        assert not _owner_descends_from("Other", "Base", {})
