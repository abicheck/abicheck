# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``diff_cxx_rules.virtual_method_addition`` consulting the shared vtable-
evidence predicate (ADR-063 Track 2, 5B closure).

Before this restructuring, ``virtual_method_addition`` deferred to
``diff_types_vtable``'s ``TYPE_VTABLE_CHANGED`` detector whenever the raw
``vtable`` arrays merely differed -- on nothing but a docstring's word that
the sibling detector's own evidence heuristic would still fire. Anywhere
that heuristic finds *no* positive evidence (the owning class's own virtual
functions, size, and virtual bases all read identically on both sides),
``TYPE_VTABLE_CHANGED`` was never going to fire either, and the deferral
silently dropped the one coverage ``virtual_method_addition`` exists to
provide: a genuine blind-spot break reported as nothing at all.

The bug class this closes is "two detectors coupled only by prose, in two
separate files" -- so these tests exercise the *predicate integration*
directly (bypassing ``compare()``'s own type/symbol matching so the exact
evidence shape reaching ``vtable_transition_is_evidenced`` is pinned),
across the shape the fix changes (unevidenced) and the two shapes it must
leave alone (evidenced, and the pre-existing equal-arrays blind spot).
"""

from __future__ import annotations

from abicheck.compare.vtable_evidence import vtable_transition_is_evidenced
from abicheck.diff_cxx_rules import owner_class_of, virtual_method_addition
from abicheck.model import Function, RecordType, Visibility
from abicheck.type_reachability_spelling import _namespace_suffix_spellings

OWNER = "Widget"
MANGLED = "_ZN6Widget6resizeEv"


def _cls(vtable: list[str]) -> RecordType:
    return RecordType(
        name=OWNER,
        kind="class",
        size_bits=64,
        vtable=vtable,
        bases=[],
        virtual_bases=[],
    )


def _virtual_fn(*, visibility: Visibility = Visibility.PUBLIC) -> Function:
    return Function(
        name=f"{OWNER}::resize",
        mangled=MANGLED,
        return_type="void",
        visibility=visibility,
        is_virtual=True,
    )


def _is_evidenced(
    t_old: RecordType,
    t_new: RecordType,
    old_funcs: dict[str, Function],
    new_funcs: dict[str, Function],
) -> bool:
    return vtable_transition_is_evidenced(
        OWNER,
        t_old,
        t_new,
        old_funcs,
        new_funcs,
        owner_class_of=owner_class_of,
        namespace_suffix_spellings=_namespace_suffix_spellings,
    )


class TestUnevidencedDifferenceNoLongerSilentlyDropsCoverage:
    """The bug: raw arrays differ, but the sibling detector has no evidence
    to act on either -- both used to go silent."""

    def test_the_precondition_actually_holds(self) -> None:
        """Pin the premise: this shape really is 'not evidenced' per the
        shared predicate, not an accident of a different branch firing."""
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        f_new = _virtual_fn()
        # The class's own virtual-function set matches on both sides (the
        # symbol already existed, non-public, before becoming public) --
        # the one shape that defeats the "owned virtual functions differ"
        # evidence branch while the raw vtable arrays still differ.
        old_funcs = {MANGLED: _virtual_fn(visibility=Visibility.HIDDEN)}
        new_funcs = {MANGLED: f_new}
        assert not _is_evidenced(t_old, t_new, old_funcs, new_funcs)

    def test_falls_through_to_its_own_override_check_and_still_fires(self) -> None:
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        f_new = _virtual_fn()
        old_funcs = {MANGLED: _virtual_fn(visibility=Visibility.HIDDEN)}
        new_funcs = {MANGLED: f_new}
        change = virtual_method_addition(
            f_new,
            {OWNER},
            {OWNER: t_old},
            {OWNER: t_new},
            {},
            old_funcs,
            new_funcs,
        )
        assert change is not None
        assert change.kind.value == "virtual_method_added"

    def test_an_inherited_override_is_still_recognized_in_this_shape(self) -> None:
        """The fallthrough reaches the pre-existing override check, which
        must still suppress a genuine override of a base's virtual -- the
        restructuring only changed *whether* this code runs, not what it
        decides once reached."""
        base = "Base"
        t_old = _cls([])
        f_new = Function(
            name=f"{OWNER}::resize",
            mangled=MANGLED,
            return_type="void",
            visibility=Visibility.PUBLIC,
            is_virtual=True,
        )
        old_funcs = {MANGLED: _virtual_fn(visibility=Visibility.HIDDEN)}
        new_funcs = {MANGLED: f_new}
        old_types = {OWNER: t_old, base: RecordType(name=base, kind="class")}
        new_types = {
            OWNER: RecordType(
                name=OWNER,
                kind="class",
                size_bits=64,
                vtable=[f"{OWNER}::resize()"],
                bases=[base],
                virtual_bases=[],
            ),
            base: RecordType(name=base, kind="class"),
        }
        old_virtual_sigs = {base: {"resize()"}}
        change = virtual_method_addition(
            f_new,
            {OWNER, base},
            old_types,
            new_types,
            old_virtual_sigs,
            old_funcs,
            new_funcs,
        )
        assert change is None


class TestEvidencedDifferenceStillDefersAsBefore:
    """A genuine vtable growth (evidenced) must keep deferring to
    ``TYPE_VTABLE_CHANGED`` -- the restructuring must not turn this into a
    duplicate finding."""

    def test_the_precondition_actually_holds(self) -> None:
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        f_new = _virtual_fn()
        old_funcs: dict[str, Function] = {}
        new_funcs = {MANGLED: f_new}
        assert _is_evidenced(t_old, t_new, old_funcs, new_funcs)

    def test_defers_to_type_vtable_changed(self) -> None:
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        f_new = _virtual_fn()
        old_funcs: dict[str, Function] = {}
        new_funcs = {MANGLED: f_new}
        change = virtual_method_addition(
            f_new,
            {OWNER},
            {OWNER: t_old},
            {OWNER: t_new},
            {},
            old_funcs,
            new_funcs,
        )
        assert change is None


class TestVtableFactsUnreliableAlsoFallsThrough:
    """``diff_types_vtable`` declines outright on an unreliable snapshot pair
    (a legacy pre-v21 direct-clang snapshot) before ever consulting the
    evidence predicate -- ``virtual_method_addition`` must reach the
    identical conclusion for the identical reason, not just "not evidenced"."""

    def test_unreliable_facts_bypass_the_evidence_check(self) -> None:
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        f_new = _virtual_fn()
        old_funcs: dict[str, Function] = {}
        new_funcs = {MANGLED: f_new}
        # Same inputs as TestEvidencedDifferenceStillDefersAsBefore, which
        # defers -- but with vtable_facts_reliable=False, TYPE_VTABLE_CHANGED
        # would decline before ever reaching the evidence predicate, so this
        # function must not defer to a detector it knows will stay silent.
        change = virtual_method_addition(
            f_new,
            {OWNER},
            {OWNER: t_old},
            {OWNER: t_new},
            {},
            old_funcs,
            new_funcs,
            vtable_facts_reliable=False,
        )
        assert change is not None
        assert change.kind.value == "virtual_method_added"


class TestEqualArraysBlindSpotUnaffected:
    """The pre-existing, primary blind spot (DWARF/symbol-only snapshots
    that never populate ``vtable`` on either side at all) must be completely
    unaffected -- the new evidence check only runs when the raw arrays
    actually differ."""

    def test_still_fires_when_neither_side_captured_a_vtable(self) -> None:
        t_old = t_new = _cls([])
        f_new = _virtual_fn()
        change = virtual_method_addition(
            f_new,
            {OWNER},
            {OWNER: t_old},
            {OWNER: t_new},
            {},
            {},
            {MANGLED: f_new},
        )
        assert change is not None
        assert change.kind.value == "virtual_method_added"
