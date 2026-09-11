# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-063 Phase 5B / T9 (duplication-and-convergence-assessment.md Phase 6
item 4): the DWARF-specific per-translation-unit completeness signal.

``_DwarfSnapshotBuilder`` builds each record type from exactly ONE
compilation unit's own view of it (``_check_and_register_type_name``'s
"first definition wins" ODR handling) and discards every other CU's own
copy outright -- including whatever that CU independently saw about the
same class's virtual methods and bases. These tests drive the builder's
internals directly with fake DIE/CU objects (the same style
``test_dwarf_snapshot.py``'s own ``test_build_function_is_isolated_from_
elf_and_filter`` uses) rather than through a real compiler, because the
exact shape under test -- two real, non-declaration definitions of the
same qualified type disagreeing on their own member DIEs -- is something
real compilers produce opportunistically (a differing ``-g`` level, a TU
that never used a given virtual, ``-flimit-debug-info`` trimming) and is
not reliably reproducible through compiler flags alone; see
``compare/vtable_evidence.py``'s own "T9 second slice" docstring note for
the full false-positive account this closes.

See ``tests/test_vtable_evidence_guard.py``'s own
``TestPartialProducerClosesTheDwarfPerTuGap`` for the consumer-side half
(``compare.vtable_evidence.vtable_transition_is_evidenced`` declining on
the ``FactStatus.PARTIAL`` this module's producer-side machinery emits).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder
from abicheck.extract.dwarf_vtable_completeness import (
    finalize_vtable_evidence_completeness,
)
from abicheck.model import FactStatus

# ── fake DWARF fixture builders ──────────────────────────────────────────────


def _av(value: object) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _cu(offset: int) -> SimpleNamespace:
    return SimpleNamespace(cu_offset=offset)


def _virtual_method_die(mangled: str, *, offset: int) -> SimpleNamespace:
    return SimpleNamespace(
        tag="DW_TAG_subprogram",
        offset=offset,
        attributes={
            "DW_AT_virtuality": _av(1),
            "DW_AT_linkage_name": _av(mangled),
        },
        iter_children=lambda: iter(()),
    )


def _virtual_method_die_bare_name(name: str, *, offset: int) -> SimpleNamespace:
    """A virtual ``DW_TAG_subprogram`` with NEITHER linkage-name attribute
    -- the fallback path (``DW_AT_name`` only), which is NOT a unique
    identifier the way the primary mangled spelling is (two overloads can
    share one bare name)."""
    return SimpleNamespace(
        tag="DW_TAG_subprogram",
        offset=offset,
        attributes={
            "DW_AT_virtuality": _av(1),
            "DW_AT_name": _av(name),
        },
        iter_children=lambda: iter(()),
    )


def _record_die(
    *,
    offset: int,
    byte_size: int = 8,
    declaration: bool = False,
    children: list[Any] | None = None,
) -> SimpleNamespace:
    attributes: dict[str, Any] = {"DW_AT_byte_size": _av(byte_size)}
    if declaration:
        attributes["DW_AT_declaration"] = _av(True)
    return SimpleNamespace(
        tag="DW_TAG_structure_type",
        offset=offset,
        attributes=attributes,
        iter_children=lambda: iter(children or ()),
    )


def _builder() -> _DwarfSnapshotBuilder:
    """A ``_DwarfSnapshotBuilder`` with only the state
    ``_process_record_type_named``/``_finalize_vtable_evidence_
    completeness`` actually touch -- bypasses ``__init__`` (which opens an
    ELF), the same pattern ``test_dwarf_snapshot.py``'s own direct-method
    tests use.
    """
    builder = _DwarfSnapshotBuilder.__new__(_DwarfSnapshotBuilder)
    builder.types = []
    builder._seen_type_names = set()
    builder._logged_type_dups = set()
    builder._record_by_qualified_name = {}
    builder._record_die_index = {}
    builder._die_key_to_qualified_name = {}
    builder._base_edges_by_record = {}
    builder._virtual_base_edges_by_record = {}
    builder._vtable_evidence_conflicts = {}
    return builder


# ── tests ─────────────────────────────────────────────────────────────────


class TestNoDuplicateNoConflict:
    """The overwhelming common case -- a type DWARF only ever defines once
    -- must be completely unaffected: no conflict flagged, facts stay
    ``PRESENT``.
    """

    def test_single_definition_stays_present(self) -> None:
        builder = _builder()
        die = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(die, _cu(0), "A")

        assert builder._vtable_evidence_conflicts == {}
        finalize_vtable_evidence_completeness(builder)

        rec = builder._record_by_qualified_name["A"]
        assert rec.vtable == ["_ZN1A1fEv"]
        assert rec.vtable_fact is not None
        assert rec.vtable_fact.status is FactStatus.PRESENT

    def test_declaration_only_duplicate_is_never_compared(self) -> None:
        """A forward-declaration stub (``byte_size == 0`` and
        ``DW_AT_declaration``) carries no member children to compare and
        must never reach the completeness machinery at all -- it returns
        before ``_note_duplicate_record_evidence`` is even called."""
        builder = _builder()
        first = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(first, _cu(0), "A")

        stub = _record_die(offset=10, byte_size=0, declaration=True)
        builder._process_record_type_named(stub, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {}


class TestDuplicateAgreesNoConflict:
    """Two CUs' own definitions of the same class, observing the identical
    bases/virtual_bases/vtable membership, must not be flagged -- this is
    the ordinary ODR-duplicate case (the same header included from two
    TUs), not a completeness gap."""

    def test_identical_duplicate_stays_present(self) -> None:
        builder = _builder()
        first = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(first, _cu(0), "A")

        duplicate = _record_die(
            offset=20, children=[_virtual_method_die("_ZN1A1fEv", offset=21)]
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {}
        finalize_vtable_evidence_completeness(builder)
        rec = builder._record_by_qualified_name["A"]
        assert rec.vtable_fact is not None
        assert rec.vtable_fact.status is FactStatus.PRESENT

    def test_reordered_duplicate_stays_present(self) -> None:
        """Membership is compared as a SET, not an ordered list -- DWARF's
        per-CU child emission order is not guaranteed to agree, and that
        alone must not read as a completeness gap."""
        builder = _builder()
        first = _record_die(
            offset=1,
            children=[
                _virtual_method_die("_ZN1A1fEv", offset=2),
                _virtual_method_die("_ZN1A1gEv", offset=3),
            ],
        )
        builder._process_record_type_named(first, _cu(0), "A")

        duplicate = _record_die(
            offset=20,
            children=[
                _virtual_method_die("_ZN1A1gEv", offset=21),
                _virtual_method_die("_ZN1A1fEv", offset=22),
            ],
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {}


class TestDuplicateDisagreesFlagsPartial:
    """The real gap this slice closes: two CUs' own definitions of the
    same class disagree on their own bases/virtual_bases/vtable
    membership -- direct, within-snapshot proof that this binary's debug
    info is not uniformly complete for this class.
    """

    def test_vtable_only_disagreement_downgrades_only_vtable_fact(self) -> None:
        """Codex review finding on this PR: a duplicate that disagrees only
        on ``vtable`` must mark only ``vtable_fact`` as ``PARTIAL``.
        ``bases_fact``/``virtual_bases_fact`` (both empty on both DIEs here)
        actually agreed and must stay ``PRESENT`` -- a blanket per-record
        downgrade would let this one incomplete evidence family blind an
        otherwise fully-evidenced ``bases``/``virtual_bases`` comparison
        elsewhere on the same record.
        """
        builder = _builder()
        # Retained (first-seen) definition: only `f`.
        first = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(first, _cu(0), "A")

        # A later CU's own definition additionally observed `g` -- direct
        # proof the retained side's own vtable list is not complete.
        duplicate = _record_die(
            offset=20,
            children=[
                _virtual_method_die("_ZN1A1fEv", offset=21),
                _virtual_method_die("_ZN1A1gEv", offset=22),
            ],
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {"A": {"vtable"}}
        finalize_vtable_evidence_completeness(builder)

        rec = builder._record_by_qualified_name["A"]
        # The retained value itself is untouched -- only its `Fact[T]`
        # sibling's status changes; "first definition wins" for the VALUE
        # is unaffected by this slice, only its confidence is.
        assert rec.vtable == ["_ZN1A1fEv"]
        assert rec.vtable_fact is not None
        assert rec.vtable_fact.status is FactStatus.PARTIAL
        assert rec.vtable_fact.producer == "dwarf"
        assert rec.bases_fact is not None
        assert rec.bases_fact.status is FactStatus.PRESENT
        assert rec.virtual_bases_fact is not None
        assert rec.virtual_bases_fact.status is FactStatus.PRESENT

    def test_all_three_fields_disagreeing_downgrades_all_three(self) -> None:
        """The blanket case: when every one of bases/virtual_bases/vtable
        actually disagrees, all three siblings become ``PARTIAL`` -- this
        slice narrows the downgrade to disagreeing fields, it doesn't stop
        downgrading a field that genuinely disagreed.
        """
        base_die = SimpleNamespace(
            tag="DW_TAG_inheritance",
            offset=99,
            attributes={},
            iter_children=lambda: iter(()),
        )
        virtual_base_die = SimpleNamespace(
            tag="DW_TAG_inheritance",
            offset=98,
            attributes={"DW_AT_virtuality": _av(1)},
            iter_children=lambda: iter(()),
        )
        builder = _builder()
        builder._resolve_base_name_and_key = lambda child, CU: ("Base", None)  # type: ignore[method-assign]
        first = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(first, _cu(0), "A")

        duplicate = _record_die(
            offset=20,
            children=[
                base_die,
                virtual_base_die,
                _virtual_method_die("_ZN1A1fEv", offset=21),
                _virtual_method_die("_ZN1A1gEv", offset=22),
            ],
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {
            "A": {"bases", "virtual_bases", "vtable"}
        }
        finalize_vtable_evidence_completeness(builder)

        rec = builder._record_by_qualified_name["A"]
        assert rec.bases_fact is not None
        assert rec.bases_fact.status is FactStatus.PARTIAL
        assert rec.virtual_bases_fact is not None
        assert rec.virtual_bases_fact.status is FactStatus.PARTIAL
        assert rec.vtable_fact is not None
        assert rec.vtable_fact.status is FactStatus.PARTIAL

    def test_vptr_offset_bits_fact_is_deliberately_untouched(self) -> None:
        """See ``compare/vtable_evidence.py``'s own "NOT consulted here"
        note: ``vptr_offset_bits_fact`` needs its own separate, careful
        treatment (it is a circular derivation on this backend in the
        unresolved residual case, and ``diff_layout`` already gives
        ``PARTIAL`` a DIFFERENT meaning there) -- this slice must not
        overload that field's status as a drive-by extension."""
        builder = _builder()
        first = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(first, _cu(0), "A")
        duplicate = _record_die(
            offset=20,
            children=[
                _virtual_method_die("_ZN1A1fEv", offset=21),
                _virtual_method_die("_ZN1A1gEv", offset=22),
            ],
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")
        finalize_vtable_evidence_completeness(builder)

        rec = builder._record_by_qualified_name["A"]
        assert rec.vptr_offset_bits_fact is not None
        assert rec.vptr_offset_bits_fact.status is not FactStatus.PARTIAL

    def test_conflict_persists_even_when_a_third_cu_agrees(self) -> None:
        """Once flagged, a conflict is never un-flagged by a later,
        agreeing CU -- a real completeness gap was already directly
        observed, and a third CU happening to agree with the retained
        side says nothing about whether the retained side's own view is
        complete."""
        builder = _builder()
        first = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(first, _cu(0), "A")

        disagreeing = _record_die(
            offset=20,
            children=[
                _virtual_method_die("_ZN1A1fEv", offset=21),
                _virtual_method_die("_ZN1A1gEv", offset=22),
            ],
        )
        builder._process_record_type_named(disagreeing, _cu(1), "A")
        assert builder._vtable_evidence_conflicts == {"A": {"vtable"}}

        agreeing = _record_die(
            offset=30, children=[_virtual_method_die("_ZN1A1fEv", offset=31)]
        )
        builder._process_record_type_named(agreeing, _cu(2), "A")
        assert builder._vtable_evidence_conflicts == {"A": {"vtable"}}

    def test_bases_only_disagreement_flags_only_bases(self) -> None:
        """A conflict confined to ``bases`` (vtable/virtual_bases both
        empty on both DIEs, so they genuinely agree) must flag only
        ``"bases"`` -- per-field scoping (Codex review finding on this PR),
        not the whole record.

        ``_resolve_base_name_and_key`` is monkeypatched rather than faked
        via a real ``DW_AT_type`` reference: it resolves through
        pyelftools' own CU-relative ref arithmetic
        (``dwarf_utils.resolve_die_ref``), which needs a real
        ``DWARFInfo``/``CU`` pair this fixture-only test deliberately does
        not construct -- the base-name resolution itself is exercised
        elsewhere (the real-DWARF integration tests in
        ``test_dwarf_snapshot.py``); this test only needs SOME non-empty
        name back for a ``DW_TAG_inheritance`` child.
        """
        base_die = SimpleNamespace(
            tag="DW_TAG_inheritance",
            offset=99,
            attributes={},
            iter_children=lambda: iter(()),
        )
        builder = _builder()
        builder._resolve_base_name_and_key = lambda child, CU: ("Base", None)  # type: ignore[method-assign]
        first = _record_die(offset=1, children=[])
        builder._process_record_type_named(first, _cu(0), "A")

        duplicate = _record_die(offset=20, children=[base_die])
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {"A": {"bases"}}
        finalize_vtable_evidence_completeness(builder)

        rec = builder._record_by_qualified_name["A"]
        assert rec.bases_fact is not None
        assert rec.bases_fact.status is FactStatus.PARTIAL
        assert rec.virtual_bases_fact is not None
        assert rec.virtual_bases_fact.status is FactStatus.PRESENT
        assert rec.vtable_fact is not None
        assert rec.vtable_fact.status is FactStatus.PRESENT

    def test_duplicate_base_bare_name_multiplicity_mismatch_flags_bases(
        self,
    ) -> None:
        """Codex review finding on this PR: ``resolve_base_name_and_key``
        deliberately returns only the bare base name (its own docstring),
        so a legal class deriving from two distinct, differently-
        namespaced bases sharing one bare spelling (``D : one::A,
        two::A``) resolves both edges to the identical name ``"A"``. A
        plain-set comparison would collapse that multiplicity: a
        duplicate DIE genuinely missing one of the two occurrences would
        still read as an identical ``{"A"}`` against the retained side's
        own ``{"A"}``, silently swallowing a real completeness gap. The
        fix compares multisets (``collections.Counter``), so a count
        mismatch on an otherwise-identical name set is caught.
        """
        base_die = SimpleNamespace(
            tag="DW_TAG_inheritance",
            offset=99,
            attributes={},
            iter_children=lambda: iter(()),
        )
        builder = _builder()
        builder._resolve_base_name_and_key = lambda child, CU: ("A", None)  # type: ignore[method-assign]
        # Retained: two distinct DW_TAG_inheritance children, both
        # resolving (by bare name) to "A" -- e.g. `D : one::A, two::A`.
        first = _record_die(offset=1, children=[base_die, base_die])
        builder._process_record_type_named(first, _cu(0), "A")

        # Duplicate CU's own definition only captured one of the two --
        # a genuine completeness gap this set-based comparison used to
        # miss entirely (both sides' own *set* of names is identical:
        # {"A"}).
        duplicate = _record_die(offset=20, children=[base_die])
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {"A": {"bases"}}
        finalize_vtable_evidence_completeness(builder)
        rec = builder._record_by_qualified_name["A"]
        assert rec.bases_fact is not None
        assert rec.bases_fact.status is FactStatus.PARTIAL
        # The VALUE itself is untouched -- both entries survive, only the
        # confidence changes.
        assert rec.bases == ["A", "A"]

    def test_duplicate_base_bare_name_multiplicity_match_stays_present(
        self,
    ) -> None:
        """Negative control for the multiplicity fix above: when the
        duplicate DIE genuinely carries the SAME count of the
        identically-named base as the retained definition, this must NOT
        flag -- the multiset comparison must agree, not merely differ
        from the old set-based one in every direction."""
        base_die = SimpleNamespace(
            tag="DW_TAG_inheritance",
            offset=99,
            attributes={},
            iter_children=lambda: iter(()),
        )
        builder = _builder()
        builder._resolve_base_name_and_key = lambda child, CU: ("A", None)  # type: ignore[method-assign]
        first = _record_die(offset=1, children=[base_die, base_die])
        builder._process_record_type_named(first, _cu(0), "A")

        duplicate = _record_die(offset=20, children=[base_die, base_die])
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {}

    def test_duplicate_vtable_fallback_name_multiplicity_mismatch_flags_vtable(
        self,
    ) -> None:
        """Codex review finding on this PR (one round after the identical
        bases/virtual_bases fix): when neither ``DW_AT_linkage_name`` nor
        ``DW_AT_MIPS_linkage_name`` is present, ``duplicate_record_
        evidence_signature`` falls back to the bare ``DW_AT_name`` -- which,
        unlike the primary mangled spelling, is NOT a unique identifier
        (two overloaded virtual methods can share one bare name). A
        plain-set comparison would collapse that multiplicity exactly the
        same way the bases/virtual_bases one did; the fix is the identical
        ``Counter`` treatment.
        """
        builder = _builder()
        # Retained: two overloads of "f", both falling back to the bare
        # name (no linkage-name attribute on either).
        first = _record_die(
            offset=1,
            children=[
                _virtual_method_die_bare_name("f", offset=2),
                _virtual_method_die_bare_name("f", offset=3),
            ],
        )
        builder._process_record_type_named(first, _cu(0), "A")

        # Duplicate CU's own definition only captured one overload -- a
        # genuine completeness gap a plain-set comparison would miss
        # entirely (both sides' own *set* of names is identical: {"f"}).
        duplicate = _record_die(
            offset=20, children=[_virtual_method_die_bare_name("f", offset=21)]
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {"A": {"vtable"}}
        finalize_vtable_evidence_completeness(builder)
        rec = builder._record_by_qualified_name["A"]
        assert rec.vtable_fact is not None
        assert rec.vtable_fact.status is FactStatus.PARTIAL
        # The VALUE itself is untouched -- both entries survive.
        assert rec.vtable == ["f", "f"]

    def test_duplicate_vtable_fallback_name_multiplicity_match_stays_present(
        self,
    ) -> None:
        """Negative control: the SAME count of the identically-spelled
        fallback name on both sides must not flag."""
        builder = _builder()
        first = _record_die(
            offset=1,
            children=[
                _virtual_method_die_bare_name("f", offset=2),
                _virtual_method_die_bare_name("f", offset=3),
            ],
        )
        builder._process_record_type_named(first, _cu(0), "A")

        duplicate = _record_die(
            offset=20,
            children=[
                _virtual_method_die_bare_name("f", offset=21),
                _virtual_method_die_bare_name("f", offset=22),
            ],
        )
        builder._process_record_type_named(duplicate, _cu(1), "A")

        assert builder._vtable_evidence_conflicts == {}

    def test_two_unrelated_types_are_tracked_independently(self) -> None:
        """A conflict on one type must not spuriously flag an unrelated
        type that only ever agreed across its own CUs."""
        builder = _builder()
        a1 = _record_die(
            offset=1, children=[_virtual_method_die("_ZN1A1fEv", offset=2)]
        )
        builder._process_record_type_named(a1, _cu(0), "A")
        a2 = _record_die(
            offset=20,
            children=[
                _virtual_method_die("_ZN1A1fEv", offset=21),
                _virtual_method_die("_ZN1A1gEv", offset=22),
            ],
        )
        builder._process_record_type_named(a2, _cu(1), "A")

        b1 = _record_die(
            offset=3, children=[_virtual_method_die("_ZN1B1hEv", offset=4)]
        )
        builder._process_record_type_named(b1, _cu(0), "B")
        b2 = _record_die(
            offset=30, children=[_virtual_method_die("_ZN1B1hEv", offset=31)]
        )
        builder._process_record_type_named(b2, _cu(1), "B")

        assert builder._vtable_evidence_conflicts == {"A": {"vtable"}}
        finalize_vtable_evidence_completeness(builder)
        b_rec = builder._record_by_qualified_name["B"]
        assert b_rec.vtable_fact is not None
        assert b_rec.vtable_fact.status is FactStatus.PRESENT
