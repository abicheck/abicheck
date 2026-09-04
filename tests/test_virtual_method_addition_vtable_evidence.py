# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``diff_cxx_rules.virtual_method_addition`` consulting the shared vtable-
evidence predicate (ADR-063 Track 2, 5B closure).

Before this restructuring, ``virtual_method_addition`` deferred to
``diff_types_vtable``'s ``TYPE_VTABLE_CHANGED`` detector whenever the raw
``vtable`` arrays merely differed -- on nothing but a docstring's word that
the sibling detector's own evidence heuristic would still fire. The fix
makes that coupling a real function call (``compare.vtable_evidence.
vtable_transition_is_evidenced``) instead of a assumption stated only in
prose.

An early revision of this fix additionally let the *fallthrough* branch
(taken when the shared predicate finds no evidence) reach a case it should
never reach at all: a virtual method that already existed in the old ABI
under an identical mangled name, merely promoted from hidden to public
visibility. Promoting visibility adds no new vtable slot, but that shape
is *exactly* the one case where the shared predicate's own "class's own
virtual functions" evidence branch reads "unchanged" (the method's mangled
name already appears in both sides' owned-signature sets) despite the raw
``vtable`` arrays differing -- so naively falling through there fabricated
a BREAKING ``VIRTUAL_METHOD_ADDED`` for a compatible export change (Codex
review on PR #1049, fresh evidence). ``virtual_method_addition`` now
special-cases that shape explicitly (checking ``old_funcs`` directly)
rather than relying on it being merely implied by the predicate's absence
of a positive signal.

These tests exercise the *predicate integration* directly (bypassing
``compare()``'s own type/symbol matching so the exact evidence shape
reaching ``vtable_transition_is_evidenced`` is pinned), across: the
already-existed guard (the bug class this closes), a genuine addition that
stays evidenced (unaffected), the ``vtable_facts_reliable=False`` bypass
(a real, independent gap this restructuring also closes), and the
pre-existing equal-arrays blind spot (must stay untouched).
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


class TestAlreadyExistingHiddenVirtualPromotedToPublicIsNotAnAddition:
    """The bug class this closes: a virtual method that already existed
    (just not public) must never be reported as VIRTUAL_METHOD_ADDED, even
    though its raw vtable arrays differ and the shared evidence predicate
    finds nothing to act on for exactly that reason."""

    def test_the_precondition_actually_holds(self) -> None:
        """Pin the premise: this shape really is 'not evidenced' per the
        shared predicate -- it is the *reason* an explicit guard is needed,
        not merely a hypothetical."""
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        old_funcs = {MANGLED: _virtual_fn(visibility=Visibility.HIDDEN)}
        new_funcs = {MANGLED: _virtual_fn()}
        assert not _is_evidenced(t_old, t_new, old_funcs, new_funcs)

    def test_is_not_reported_as_a_virtual_method_addition(self) -> None:
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
        assert change is None

    def test_still_not_reported_when_vtable_facts_are_unreliable(self) -> None:
        """The already-existed guard is unconditional -- it must not be
        bypassed by the vtable_facts_reliable=False fallthrough either,
        since it isn't a vtable-evidence question at all."""
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
            vtable_facts_reliable=False,
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


class TestVtableFactsUnreliableFallsThroughToTheOverrideCheck:
    """``diff_types_vtable`` declines outright on an unreliable snapshot pair
    (a legacy pre-v21 direct-clang snapshot) before ever consulting the
    evidence predicate -- ``virtual_method_addition`` must reach the
    identical conclusion for the identical reason. This is the one real,
    independent gap the restructuring closes for a *genuinely new* symbol:
    the shared predicate alone always reads such a symbol as evidenced when
    facts are reliable (see TestEvidencedDifferenceStillDefersAsBefore), so
    only the reliability bypass can route a truly new virtual through this
    function's own override check."""

    def test_unreliable_facts_bypass_the_evidence_check_and_still_fire(self) -> None:
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

    def test_an_inherited_override_is_still_recognized_in_this_shape(self) -> None:
        """The fallthrough reaches the pre-existing override check, which
        must still suppress a genuine override of a base's virtual -- the
        restructuring only changed *whether* this code runs, not what it
        decides once reached. Uses a genuinely new mangled symbol (not the
        already-existed shape above) so this actually exercises the
        override check via the fallthrough, not the early guard."""
        base = "Base"
        t_new_owner = RecordType(
            name=OWNER,
            kind="class",
            size_bits=64,
            vtable=[f"{OWNER}::resize()"],
            bases=[base],
            virtual_bases=[],
        )
        old_types = {
            OWNER: _cls([]),
            base: RecordType(name=base, kind="class", bases=[], virtual_bases=[]),
        }
        new_types = {
            OWNER: t_new_owner,
            base: RecordType(name=base, kind="class", bases=[], virtual_bases=[]),
        }
        f_new = _virtual_fn()
        old_funcs: dict[str, Function] = {}
        new_funcs = {MANGLED: f_new}
        old_virtual_sigs = {base: {"resize()"}}
        change = virtual_method_addition(
            f_new,
            {OWNER, base},
            old_types,
            new_types,
            old_virtual_sigs,
            old_funcs,
            new_funcs,
            vtable_facts_reliable=False,
        )
        assert change is None


class TestReliableButUnevidencedFallthroughBranchCompleteness:
    """Pins the ``vtable_transition_is_evidenced(...)`` call's own ``False``
    branch when ``vtable_facts_reliable`` is left at its default ``True`` --
    the one shape none of the classes above exercise.

    Per ``virtual_method_addition``'s own docstring, this branch is not
    reachable through the real ``diff_symbols.py`` call site for a
    genuinely new virtual: the predicate's "class's own virtual functions"
    branch always evidences a symbol that is present in ``new_funcs`` and
    genuinely absent from ``old_funcs`` (see ``TestEvidencedDifference
    StillDefersAsBefore``), and the one case it does *not* evidence -- an
    identical mangled name already present in ``old_funcs`` -- is
    intercepted earlier by the already-existed guard. Both real callers
    (``diff_symbols.py``) always pass a ``new_funcs`` that already contains
    ``f_new`` under its own mangled key (it is *sourced from* that same
    map), so this exact combination cannot arise from that call site.

    This test constructs it directly anyway, purely for branch-coverage
    completeness on the fallthrough logic itself: pass a ``new_funcs`` that
    (synthetically, unlike any real caller) does *not* include ``f_new`` at
    all, so the predicate's owned-function sets read equal (both empty) on
    both sides while the already-existed guard still does not fire (``f_new
    .mangled`` is absent from ``old_funcs`` too). Confirms the fallthrough
    reaches the override check -- and fires -- exactly the same way the
    ``vtable_facts_reliable=False`` bypass already does.
    """

    def test_the_predicate_itself_reads_unevidenced_here(self) -> None:
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        # Neither side's function map mentions f_new (or anything else) at
        # all -- both owned-signature sets are empty, so the predicate's
        # "class's own virtual functions" branch reads "unchanged" without
        # any already-existed mangled-name collision at all.
        assert not _is_evidenced(t_old, t_new, {}, {})

    def test_falls_through_and_still_fires(self) -> None:
        t_old, t_new = _cls([]), _cls([f"{OWNER}::resize()"])
        f_new = _virtual_fn()
        change = virtual_method_addition(
            f_new,
            {OWNER},
            {OWNER: t_old},
            {OWNER: t_new},
            {},
            {},  # old_funcs: f_new.mangled absent -- already-existed guard doesn't fire
            {},  # new_funcs: deliberately omits f_new itself (see class docstring)
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
