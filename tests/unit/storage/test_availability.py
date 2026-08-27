# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D3's explicit fact availability.

The invariant under test throughout: **a comparison may never infer safety
from an empty collection.** Every test here is a way of asking whether the
primitive still makes that unwritable.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.availability import (
    _GAP_STATUSES as _MODULE_GAP_STATUSES,
    AvailabilityLedger,
    Confidence,
    FactAvailability,
    FactStatus,
)

_STATUSES = list(FactStatus)
_CONFIDENCES = list(Confidence)

#: Imported from the module under test rather than restated here. This list
#: previously lived only in this file, with a comment explaining the very
#: distinction `missing_families` was failing to make — so the tests knew
#: something the production predicate did not, and nothing could notice.
_GAP_STATUSES = sorted(_MODULE_GAP_STATUSES, key=lambda s: s.value)


class TestComparablePredicate:
    """`comparable` is the one place "may I rely on this?" is answered."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (FactStatus.PRESENT, True),
            (FactStatus.PARTIAL, True),
            (FactStatus.NOT_COLLECTED, False),
            (FactStatus.UNSUPPORTED, False),
            (FactStatus.FAILED, False),
            (FactStatus.NOT_APPLICABLE, False),
        ],
    )
    def test_every_status_has_a_pinned_answer(
        self, status: FactStatus, expected: bool
    ) -> None:
        assert FactAvailability(status).comparable is expected

    def test_the_predicate_is_an_allowlist_not_a_denylist(self) -> None:
        """A status this predicate has not heard of must not read as usable.

        Spelling it as an allowlist is what makes a future sixth status
        non-comparable by default. A denylist (`status is not FAILED`) would
        silently admit it, which is the failure mode the whole module exists
        to prevent — dressed up as a one-line convenience.
        """
        comparable = {s for s in _STATUSES if FactAvailability(s).comparable}

        assert comparable == {FactStatus.PRESENT, FactStatus.PARTIAL}

    @pytest.mark.parametrize("status", _STATUSES)
    def test_confidence_never_overrides_status(self, status: FactStatus) -> None:
        """Low confidence is not the same claim as absent evidence."""
        assert (
            FactAvailability(status, confidence=Confidence.UNKNOWN).comparable
            is FactAvailability(status).comparable
        )


class TestEstablishesAbsence:
    def test_only_present_licenses_reading_an_empty_collection_as_empty(self) -> None:
        licensing = {s for s in _STATUSES if FactAvailability(s).establishes_absence}

        assert licensing == {FactStatus.PRESENT}

    def test_partial_is_comparable_but_does_not_establish_absence(self) -> None:
        """The distinction that keeps partial coverage honest.

        Under partial coverage the covered part is known and the rest is
        unknown — so an empty collection is ambiguous, even though the
        evidence that does exist is usable.
        """
        partial = FactAvailability(FactStatus.PARTIAL)

        assert partial.comparable
        assert not partial.establishes_absence


class TestNarrowingNeverWidens:
    """A per-entity override may report worse availability, never better."""

    @given(st.sampled_from(_GAP_STATUSES), st.sampled_from(_STATUSES))
    def test_an_override_can_never_fill_a_family_level_evidence_gap(
        self, family: FactStatus, entity: FactStatus
    ) -> None:
        """No override makes a family that lacks evidence comparable.

        Scoped to the three *gap* statuses on purpose. `NOT_APPLICABLE` is
        also non-comparable but is not a gap — it says the family is
        meaningless here, so an entity that genuinely carries the fact
        supersedes it rather than being suppressed by it (see
        `test_a_real_entity_fact_supersedes_a_not_applicable_family`). A
        property written over all six statuses conflates those two readings,
        which is what the first version of this test did.
        """
        result = FactAvailability(family).narrowed(FactAvailability(entity))

        assert not result.comparable

    def test_an_optimistic_override_cannot_defeat_the_family(self) -> None:
        family = FactAvailability(FactStatus.NOT_COLLECTED)
        entity = FactAvailability(FactStatus.PRESENT, producer="clang")

        result = family.narrowed(entity)

        assert result.status is FactStatus.NOT_COLLECTED
        assert not result.comparable

    def test_a_pessimistic_override_is_honoured(self) -> None:
        family = FactAvailability(FactStatus.PRESENT, producer="clang")
        entity = FactAvailability(FactStatus.FAILED, diagnostics=("parse error",))

        result = family.narrowed(entity)

        assert result.status is FactStatus.FAILED
        assert "parse error" in result.diagnostics

    def test_not_applicable_does_not_drag_a_present_family_down(self) -> None:
        """A family that does not apply is not a gap.

        Vtable facts for a C-only entity are `not_applicable`; letting that
        outrank a family-level `present` would report a whole artifact as
        short of evidence because one entity legitimately has none.
        """
        family = FactAvailability(FactStatus.PRESENT)
        entity = FactAvailability(FactStatus.NOT_APPLICABLE)

        assert family.narrowed(entity).status is FactStatus.PRESENT

    def test_a_real_entity_fact_supersedes_a_not_applicable_family(self) -> None:
        family = FactAvailability(FactStatus.NOT_APPLICABLE)
        entity = FactAvailability(FactStatus.PRESENT, producer="dwarf")

        assert family.narrowed(entity).status is FactStatus.PRESENT

    @given(st.sampled_from(_CONFIDENCES), st.sampled_from(_CONFIDENCES))
    def test_confidence_narrows_too(
        self, family: Confidence, entity: Confidence
    ) -> None:
        result = FactAvailability(FactStatus.PRESENT, confidence=family).narrowed(
            FactAvailability(FactStatus.PRESENT, confidence=entity)
        )

        order = [Confidence.HIGH, Confidence.REDUCED, Confidence.UNKNOWN]
        assert order.index(result.confidence) >= max(
            order.index(family), order.index(entity)
        )

    def test_diagnostics_from_both_sides_are_kept(self) -> None:
        """Both explain the outcome; dropping either loses the reason."""
        result = FactAvailability(
            FactStatus.PRESENT, diagnostics=("family note",)
        ).narrowed(FactAvailability(FactStatus.FAILED, diagnostics=("entity note",)))

        assert result.diagnostics == ("family note", "entity note")

    def test_diagnostics_are_not_duplicated(self) -> None:
        shared = FactAvailability(FactStatus.PRESENT, diagnostics=("same",))

        assert shared.narrowed(shared).diagnostics == ("same",)

    @given(st.sampled_from(_STATUSES))
    def test_narrowing_with_itself_is_idempotent(self, status: FactStatus) -> None:
        record = FactAvailability(status, producer="clang", recipe="r1")

        assert record.narrowed(record) == record


class TestImmutability:
    def test_a_record_cannot_be_mutated_in_place(self) -> None:
        """An availability record states a completed extraction.

        Mutability would let one consumer's narrowing be visible to another
        that had already read the record.
        """
        record = FactAvailability(FactStatus.PRESENT)

        with pytest.raises(dataclasses.FrozenInstanceError):
            record.status = FactStatus.FAILED  # type: ignore[misc]

    def test_diagnostics_are_normalized_to_a_tuple(self) -> None:
        record = FactAvailability(FactStatus.FAILED, diagnostics=["a", "b"])

        assert record.diagnostics == ("a", "b")

    def test_a_non_status_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            FactAvailability("present")  # type: ignore[arg-type]


class TestLedgerLookupIsTotal:
    def test_an_undeclared_family_defaults_to_not_collected(self) -> None:
        """The one case where inferring availability would be a guess."""
        ledger = AvailabilityLedger()

        answer = ledger.for_family("vtables")

        assert answer.status is FactStatus.NOT_COLLECTED
        assert not answer.comparable

    def test_an_entity_with_no_override_inherits_its_family(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT, producer="dwarf"))

        assert ledger.for_entity("layout", "ns::Foo").status is FactStatus.PRESENT

    def test_an_override_narrows_rather_than_replaces(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.NOT_COLLECTED))
        ledger.override("layout", "ns::Foo", FactAvailability(FactStatus.PRESENT))

        assert ledger.for_entity("layout", "ns::Foo").status is FactStatus.NOT_COLLECTED

    def test_an_override_is_scoped_to_its_own_family(self) -> None:
        ledger = AvailabilityLedger()
        for family in ("layout", "vtables"):
            ledger.declare(family, FactAvailability(FactStatus.PRESENT))
        ledger.override("layout", "ns::Foo", FactAvailability(FactStatus.FAILED))

        assert ledger.for_entity("vtables", "ns::Foo").status is FactStatus.PRESENT

    @given(
        st.lists(
            st.tuples(st.sampled_from(["a", "b", "c"]), st.sampled_from(_STATUSES)),
            max_size=8,
        )
    )
    def test_lookup_answers_for_every_family_name(
        self, declarations: list[tuple[str, FactStatus]]
    ) -> None:
        ledger = AvailabilityLedger()
        for family, status in declarations:
            ledger.declare(family, FactAvailability(status))

        for family in ("a", "b", "c", "never-declared"):
            assert isinstance(ledger.for_family(family), FactAvailability)
            assert isinstance(ledger.for_entity(family, "x"), FactAvailability)


class TestMissingFamilies:
    def test_required_families_without_usable_evidence_are_reported(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("binary", FactAvailability(FactStatus.PRESENT))
        ledger.declare("layout", FactAvailability(FactStatus.FAILED))

        assert ledger.missing_families(["binary", "layout", "graph"]) == (
            "graph",
            "layout",
        )

    @given(st.permutations(["graph", "layout", "binary"]))
    def test_the_report_is_order_independent(self, required: list[str]) -> None:
        """A diagnostic must not vary with the caller's iteration order."""
        ledger = AvailabilityLedger()
        ledger.declare("binary", FactAvailability(FactStatus.PRESENT))

        assert ledger.missing_families(required) == ("graph", "layout")

    def test_a_fully_covered_requirement_reports_nothing(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("binary", FactAvailability(FactStatus.PRESENT))
        ledger.declare("layout", FactAvailability(FactStatus.PARTIAL))

        assert ledger.missing_families(["binary", "layout"]) == ()

    def test_comparable_families_matches_the_predicate(self) -> None:
        ledger = AvailabilityLedger()
        for name, status in zip(
            ("f0", "f1", "f2", "f3", "f4", "f5"), _STATUSES, strict=True
        ):
            ledger.declare(name, FactAvailability(status))

        assert ledger.comparable_families() == frozenset({"f0", "f1"})


class TestNarrowingMergesIdentifyingFieldsPerField:
    """Codex review: whole-record selection lost information both ways.

    `other if other.producer else self` discarded an override's own `scope`
    when it named no producer, and erased the family's `recipe`/
    `producer_version` when it named only a producer. Neither loss is
    cosmetic: the first misstates evidence scope, the second makes
    interchangeability unanswerable.
    """

    def test_an_override_scope_survives_without_a_producer(self) -> None:
        family = FactAvailability(
            FactStatus.PRESENT, producer="clang", recipe="r1", scope="all"
        )
        override = FactAvailability(FactStatus.PARTIAL, scope="headers-only")

        result = family.narrowed(override)

        assert result.scope == "headers-only"
        assert result.status is FactStatus.PARTIAL
        # Unstated fields still inherit.
        assert result.producer == "clang"
        assert result.recipe == "r1"

    def test_a_producer_only_override_does_not_erase_recipe_or_version(
        self,
    ) -> None:
        family = FactAvailability(
            FactStatus.PRESENT,
            producer="clang",
            producer_version="18.1.0",
            recipe="r1",
            scope="all",
        )
        override = FactAvailability(FactStatus.PARTIAL, producer="castxml")

        result = family.narrowed(override)

        assert result.producer == "castxml"
        assert result.recipe == "r1"
        assert result.producer_version == "18.1.0"
        assert result.scope == "all"

    @given(
        st.sampled_from(["", "castxml"]),
        st.sampled_from(["", "r2"]),
        st.sampled_from(["", "headers-only"]),
        st.sampled_from(["", "20.1"]),
    )
    def test_each_field_is_the_override_when_stated_else_the_family(
        self, producer: str, recipe: str, scope: str, version: str
    ) -> None:
        family = FactAvailability(
            FactStatus.PRESENT,
            producer="clang",
            producer_version="18.1.0",
            recipe="r1",
            scope="all",
        )
        override = FactAvailability(
            FactStatus.PRESENT,
            producer=producer,
            producer_version=version,
            recipe=recipe,
            scope=scope,
        )

        result = family.narrowed(override)

        assert result.producer == (producer or "clang")
        assert result.producer_version == (version or "18.1.0")
        assert result.recipe == (recipe or "r1")
        assert result.scope == (scope or "all")

    def test_no_field_is_ever_silently_blanked(self) -> None:
        """A field set on either side must never come back empty."""
        family = FactAvailability(
            FactStatus.PRESENT, producer="clang", recipe="r1", scope="all"
        )

        for override in (
            FactAvailability(FactStatus.PARTIAL),
            FactAvailability(FactStatus.PARTIAL, producer="castxml"),
            FactAvailability(FactStatus.PARTIAL, scope="headers-only"),
            FactAvailability(FactStatus.PARTIAL, recipe="r2"),
        ):
            result = family.narrowed(override)
            assert result.producer and result.recipe and result.scope


class TestReadDoorsValidateTheirLookupKeys:
    """Every write door refused a non-string key; the read doors did not.

    That asymmetry is not merely inconsistent, it is unsafe in one
    direction. A key that can never match resolves *past* whatever is
    stored: with a `PRESENT` family and a `FAILED` override under
    `("layout", "1")`, `for_entity("layout", 1)` answered
    `PRESENT`/comparable, silently skipping the failed evidence rather
    than reporting it (Codex review).
    """

    @staticmethod
    def _ledger() -> AvailabilityLedger:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(status=FactStatus.PRESENT))
        ledger.override("layout", "1", FactAvailability(status=FactStatus.FAILED))
        return ledger

    def test_the_reported_case_no_longer_licenses_a_conclusion(self) -> None:
        """The consequence, stated before the guard that prevents it."""
        ledger = self._ledger()

        assert ledger.for_entity("layout", "1").status is FactStatus.FAILED

        with pytest.raises(TypeError, match="entity must be a string"):
            ledger.for_entity("layout", 1)

    @pytest.mark.parametrize(
        "key",
        [
            pytest.param(1, id="int"),
            pytest.param(1.0, id="float"),
            pytest.param(True, id="bool"),
            pytest.param(None, id="none"),
            pytest.param(("layout",), id="tuple"),
            pytest.param(b"layout", id="bytes"),
        ],
    )
    def test_both_halves_of_both_read_doors_refuse_a_non_string(
        self, key: object
    ) -> None:
        """Parametrized over the key kinds, not just the reported `int`.

        Each half is asserted separately: `for_entity` validates the family
        itself rather than leaning on its own `for_family` call, which is
        reached only when no override matches — so relying on it would make
        the family check conditional on the very lookup it validates.
        """
        ledger = self._ledger()

        with pytest.raises(TypeError, match="family must be a string"):
            ledger.for_family(key)
        with pytest.raises(TypeError, match="family must be a string"):
            ledger.for_entity(key, "1")
        with pytest.raises(TypeError, match="entity must be a string"):
            ledger.for_entity("layout", key)

    def test_the_family_half_is_checked_even_when_an_override_matches(self) -> None:
        """The case a `for_family`-delegated check would have missed.

        If the family check only happened via the `for_family` call, a
        lookup whose override half matched would return before ever
        reaching it.
        """
        ledger = self._ledger()

        with pytest.raises(TypeError, match="family must be a string"):
            ledger.for_entity(1, "1")

    def test_read_and_write_doors_now_agree(self) -> None:
        """The invariant behind the fix, rather than another instance of it.

        A key kind the ledger refuses to *store* under must not be one it
        accepts a *lookup* for — otherwise the two doors disagree about what
        a key is, which is what let a stored record be skipped.
        """
        ledger = self._ledger()
        avail = FactAvailability(status=FactStatus.PRESENT)

        for door in (
            lambda: ledger.declare(1, avail),
            lambda: ledger.override("layout", 1, avail),
            lambda: ledger.for_family(1),
            lambda: ledger.for_entity("layout", 1),
        ):
            with pytest.raises(TypeError):
                door()

    def test_valid_lookups_are_untouched(self) -> None:
        """The control: the guard must not narrow what already worked."""
        ledger = self._ledger()

        assert ledger.for_family("layout").status is FactStatus.PRESENT
        assert ledger.for_entity("layout", "1").status is FactStatus.FAILED
        assert ledger.for_entity("layout", "other").status is FactStatus.PRESENT
        assert ledger.for_family("undeclared").status is FactStatus.NOT_COLLECTED


class TestRequiredFamiliesMustBeACollection:
    """A bare `str` is an iterable of `str`, so every per-item check passes.

    `missing_families("layout")` answered `('a', 'l', 'o', 't', 'u', 'y')`
    and omitted the real failed family — the coverage check that exists to
    *find* gaps reporting six that do not exist and missing the one that
    does (Codex review). Confidently answered about the wrong thing, which
    is worse than raising.
    """

    @staticmethod
    def _ledger() -> AvailabilityLedger:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(status=FactStatus.FAILED))
        return ledger

    def test_the_reported_case_is_refused(self) -> None:
        with pytest.raises(TypeError, match="collection of keys"):
            self._ledger().missing_families("layout")

    def test_bytes_is_refused_too(self) -> None:
        """Rejected here rather than left to the item guard.

        `bytes` yields `int`, which the per-family key guard already raises
        on — but that is the item guard's accident, not this one's intent,
        and depending on it would make the container rule true only by
        coincidence.
        """
        with pytest.raises(TypeError, match="collection of keys"):
            self._ledger().missing_families(b"layout")

    @pytest.mark.parametrize(
        "required",
        [
            pytest.param(["layout"], id="list"),
            pytest.param(("layout",), id="tuple"),
            pytest.param({"layout"}, id="set"),
            # A factory, not a live iterator: pytest.mark.parametrize
            # evaluates its argvalues once at *collection* time, so a bare
            # `iter(["layout"])` here is a single shared, stateful object —
            # any double collection or double run of this test in the same
            # process (mutmut's own harness does this) exhausts it before
            # the assertion below ever sees it, making the test fail for a
            # reason that has nothing to do with the guard under test.
            # Deferring construction to run time gives every actual test
            # run its own fresh iterator, matching every other param here.
            pytest.param(lambda: iter(["layout"]), id="iterator"),
        ],
    )
    def test_real_collections_still_work(self, required: object) -> None:
        """The control. A generator is included because the guard must test
        the container, not consume it — a check that iterated to decide
        would leave a caller's iterator empty.
        """
        if callable(required):
            required = required()
        assert self._ledger().missing_families(required) == ("layout",)

    def test_sibling_doors_refuse_a_bare_string_whether_empty_or_not(self) -> None:
        """This test previously asserted a claim that was false.

        It said `missing_families` was "the package's only door of its
        kind, provably", because a per-item guard already covers a
        collection whose items are not strings — `extend("abc")` raises on
        `"a"`. That reasoning holds only for a *non-empty* string.
        `extend("")` iterated zero times and silently produced an empty
        set, and `group_by_entity("")` returned `{}` (Codex review; the
        second was not reported, and came out of re-checking the claim the
        first falsified).

        **A per-item guard is never a container guard, because an empty
        container has no items.** Both doors now check the container, so
        the empty and non-empty cases are asserted together — the pair is
        the point, since testing only the non-empty one is what let the
        original claim look proven.
        """
        from abicheck.storage.identity import OccurrenceSet, group_by_entity

        for scalar in ("abc", "", b"ab", b""):
            with pytest.raises(TypeError):
                OccurrenceSet().extend(scalar)
            with pytest.raises(TypeError):
                group_by_entity(scalar)


class TestUnknownFamilyFallbackCannotBeComparable:
    """Codex review: one stored field could defeat the whole module's rule.

    `from_dict` reads `unknown_family_default` from the package, so a
    malformed or hand-edited ledger stating `present` made *every* undeclared
    family read as usable — and `missing_families` report no gap at all.
    """

    @pytest.mark.parametrize("status", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_a_comparable_fallback_is_refused_at_construction(
        self, status: FactStatus
    ) -> None:
        with pytest.raises(ValueError, match="must not be comparable"):
            AvailabilityLedger(unknown_family_default=FactAvailability(status))

    @pytest.mark.parametrize("status", ["present", "partial"])
    def test_a_comparable_fallback_is_refused_on_deserialization(
        self, status: str
    ) -> None:
        """The path the finding is actually about: a stored package."""
        with pytest.raises(ValueError, match="must not be comparable"):
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "overrides": [],
                    "unknown_family_default": {"status": status},
                }
            )

    @pytest.mark.parametrize(
        "status",
        [
            FactStatus.NOT_COLLECTED,
            FactStatus.UNSUPPORTED,
            FactStatus.FAILED,
            FactStatus.NOT_APPLICABLE,
        ],
    )
    def test_every_non_comparable_fallback_stays_choosable(
        self, status: FactStatus
    ) -> None:
        """Only the two conclusion-licensing statuses are refused.

        A caller may still say "most families are not applicable to this
        artifact kind" or "this producer cannot answer" — those are real,
        useful defaults and none of them lets absence imply safety.
        """
        ledger = AvailabilityLedger(unknown_family_default=FactAvailability(status))

        assert ledger.for_family("never-declared").status is status

    @staticmethod
    def _with_bad_fallback(status: FactStatus) -> AvailabilityLedger:
        """Reach the bad state past the assignment guard, on purpose.

        A plain `ledger.unknown_family_default = ...` is refused now — see
        `test_availability_documents.py::TestAReassignedFallbackIsRefused`.
        The read-time coercion below is the second line, so these tests must
        build the state some other way to exercise it at all. Deleting them
        because the door above closed would leave `for_family` and `to_dict`
        free to disagree again for any path that bypasses assignment.
        """
        ledger = AvailabilityLedger()
        object.__setattr__(ledger, "unknown_family_default", FactAvailability(status))
        return ledger

    def test_post_construction_reassignment_is_coerced_at_read(self) -> None:
        """The ledger is mutable by design, so construction checks aren't enough.

        `for_family` re-checks rather than trusting, and errs toward "no
        conclusion".
        """
        ledger = self._with_bad_fallback(FactStatus.PRESENT)

        answer = ledger.for_family("layout")

        assert not answer.comparable
        assert answer.status is FactStatus.NOT_COLLECTED

    def test_the_gap_is_still_reported_after_reassignment(self) -> None:
        ledger = self._with_bad_fallback(FactStatus.PARTIAL)

        assert ledger.missing_families(["layout", "graph"]) == ("graph", "layout")

    def test_for_entity_inherits_the_coerced_fallback(self) -> None:
        ledger = self._with_bad_fallback(FactStatus.PRESENT)

        assert not ledger.for_entity("layout", "ns::Foo").comparable

    def test_a_declared_family_is_unaffected(self) -> None:
        """The guard must only reach families nobody declared."""
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT, producer="dwarf"))

        assert ledger.for_family("layout").comparable
        assert ledger.for_family("layout").producer == "dwarf"

    def test_a_default_ledger_round_trips(self) -> None:
        """The refusal must not make an ordinary ledger unloadable."""
        ledger = AvailabilityLedger()
        ledger.declare("binary", FactAvailability(FactStatus.PRESENT))

        restored = AvailabilityLedger.from_dict(ledger.to_dict())

        assert restored.to_dict() == ledger.to_dict()


class TestAnOverrideCannotManufactureAnUndeclaredFamily:
    """Codex review: the round-4 fallback fix opened this path.

    `NOT_APPLICABLE` is a permitted fallback and sits *below* `PRESENT` in the
    status order — deliberately, so an entity that genuinely carries a fact
    supersedes a family declared inapplicable. That supersession rule is only
    sound when a family record actually says "inapplicable"; with no record at
    all, the same ordering let an override alone produce a comparable answer.
    """

    def test_an_override_cannot_upgrade_an_undeclared_family(self) -> None:
        ledger = AvailabilityLedger(
            unknown_family_default=FactAvailability(FactStatus.NOT_APPLICABLE)
        )
        ledger.override(
            "layout", "ns::Foo", FactAvailability(FactStatus.PRESENT, producer="dwarf")
        )

        answer = ledger.for_entity("layout", "ns::Foo")

        assert not answer.comparable
        assert answer.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize(
        "fallback",
        [
            FactStatus.NOT_COLLECTED,
            FactStatus.UNSUPPORTED,
            FactStatus.FAILED,
            FactStatus.NOT_APPLICABLE,
        ],
    )
    @pytest.mark.parametrize("override", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_no_fallback_and_override_pair_yields_a_comparable_answer(
        self, fallback: FactStatus, override: FactStatus
    ) -> None:
        """The property, over every permitted fallback and both upgrades."""
        ledger = AvailabilityLedger(unknown_family_default=FactAvailability(fallback))
        ledger.override("f", "e", FactAvailability(override))

        assert not ledger.for_entity("f", "e").comparable

    def test_a_declared_not_applicable_is_still_superseded(self) -> None:
        """The round-1 supersession rule must survive this fix.

        A family explicitly declared inapplicable, with an entity that really
        does carry the fact, still resolves to the entity's answer. Only the
        *undeclared* case is blocked.
        """
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.NOT_APPLICABLE))
        ledger.override(
            "layout", "ns::Foo", FactAvailability(FactStatus.PRESENT, producer="dwarf")
        )

        answer = ledger.for_entity("layout", "ns::Foo")

        assert answer.status is FactStatus.PRESENT
        assert answer.comparable

    def test_a_declared_family_still_narrows_downward(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT, producer="dwarf"))
        ledger.override("layout", "ns::Foo", FactAvailability(FactStatus.FAILED))

        assert ledger.for_entity("layout", "ns::Foo").status is FactStatus.FAILED

    def test_an_undeclared_family_without_an_override_keeps_the_fallback(
        self,
    ) -> None:
        """The fallback still means what it says where nothing overrides it."""
        ledger = AvailabilityLedger(
            unknown_family_default=FactAvailability(FactStatus.NOT_APPLICABLE)
        )

        assert ledger.for_entity("f", "e").status is FactStatus.NOT_APPLICABLE
        assert ledger.for_family("f").status is FactStatus.NOT_APPLICABLE


class TestANonComparableFallbackKeepsItsEvidence:
    """Codex review: the previous guard was over-broad.

    Blocking the undeclared-family upgrade by replacing the base with a bare
    `NOT_COLLECTED` also discarded the fallback's own status, producer and
    diagnostics — so a `FAILED` fallback carrying a parse error resolved to an
    unannotated `NOT_COLLECTED` while `for_family` still reported the real
    failure. Only `NOT_APPLICABLE` ranks below `PRESENT`, so only it needed
    handling at all.
    """

    @pytest.mark.parametrize(
        "status", [FactStatus.NOT_COLLECTED, FactStatus.UNSUPPORTED, FactStatus.FAILED]
    )
    def test_an_already_worse_fallback_is_passed_through_untouched(
        self, status: FactStatus
    ) -> None:
        ledger = AvailabilityLedger(
            unknown_family_default=FactAvailability(
                status, producer="dwarf", diagnostics=("parse error",)
            )
        )
        ledger.override("layout", "ns::Foo", FactAvailability(FactStatus.PRESENT))

        answer = ledger.for_entity("layout", "ns::Foo")

        assert answer.status is status
        assert answer.producer == "dwarf"
        assert "parse error" in answer.diagnostics

    def test_for_entity_and_for_family_agree_on_the_fallback_evidence(self) -> None:
        """The tell that something was dropped: the two disagreed."""
        ledger = AvailabilityLedger(
            unknown_family_default=FactAvailability(
                FactStatus.FAILED, producer="dwarf", diagnostics=("parse error",)
            )
        )
        ledger.override("layout", "ns::Foo", FactAvailability(FactStatus.PRESENT))

        entity = ledger.for_entity("layout", "ns::Foo")
        family = ledger.for_family("layout")

        assert entity.status is family.status
        assert entity.producer == family.producer

    def test_not_applicable_still_blocks_the_upgrade_but_keeps_its_evidence(
        self,
    ) -> None:
        ledger = AvailabilityLedger(
            unknown_family_default=FactAvailability(
                FactStatus.NOT_APPLICABLE, producer="policy", diagnostics=("C only",)
            )
        )
        ledger.override("vtables", "e", FactAvailability(FactStatus.PRESENT))

        answer = ledger.for_entity("vtables", "e")

        assert not answer.comparable
        assert answer.status is FactStatus.NOT_COLLECTED
        # Only the one upgradeable status is substituted; nothing else is lost.
        assert answer.producer == "policy"
        assert "C only" in answer.diagnostics


class TestNotApplicableIsNotAGap:
    """Codex review: `missing_families` asked "not comparable", not "missing".

    A generic evidence profile naturally requires families that do not apply
    to every artifact — vtable facts for a C-only library. Recording
    `NOT_APPLICABLE` exists to answer that; folding it in with the gap
    statuses produced a coverage gap for a ledger that had explicitly
    established none.

    The distinction already existed in this file, spelled exactly this way.
    It is now imported from the module, so the tests cannot again know
    something the production predicate does not.
    """

    def test_an_explicitly_inapplicable_family_is_not_missing(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("vtables", FactAvailability(FactStatus.NOT_APPLICABLE))

        assert ledger.missing_families(["vtables"]) == ()

    @pytest.mark.parametrize("status", _GAP_STATUSES)
    def test_every_gap_status_is_still_missing(self, status: FactStatus) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("graph", FactAvailability(status))

        assert ledger.missing_families(["graph"]) == ("graph",)

    def test_an_undeclared_family_is_still_missing(self) -> None:
        """Only an explicit declaration may say "not applicable".

        An undeclared family resolves through the fallback, which cannot be
        comparable and defaults to `NOT_COLLECTED` — so silence is a gap, not
        an exemption. This is the case that would make the fix dangerous if it
        were wrong, since it is how an unset ledger behaves.
        """
        assert AvailabilityLedger().missing_families(["anything"]) == ("anything",)

    def test_a_default_of_not_applicable_exempts_undeclared_families(self) -> None:
        """The one way silence becomes an exemption: say so in the fallback.

        `NOT_APPLICABLE` is a permitted `unknown_family_default` — the
        artifact-kind case the constructor guard deliberately leaves open —
        so a ledger for a C-only artifact can exempt what it never declares.
        """
        ledger = AvailabilityLedger(
            unknown_family_default=FactAvailability(FactStatus.NOT_APPLICABLE)
        )

        assert ledger.missing_families(["vtables", "graph"]) == ()

    def test_gap_and_comparable_statuses_do_not_overlap_or_exhaust(self) -> None:
        """The two sets are deliberately not complements of each other.

        Asserting it here keeps `NOT_APPLICABLE`'s third position explicit: a
        future status added to neither set is neither usable evidence nor a
        reported gap, which must be a deliberate choice rather than a default.
        """
        # Imported from `availability_status`, which owns them, rather than
        # through `availability`'s alias: the module split made that alias
        # unused there, and importing a name through a module that merely
        # re-exports it is how a test ends up pinned to a forwarding detail
        # instead of to the rule.
        from abicheck.storage.availability_status import (
            COMPARABLE_STATUSES,
            GAP_STATUSES as gaps,
        )

        assert not (COMPARABLE_STATUSES & gaps)
        assert set(FactStatus) - COMPARABLE_STATUSES - gaps == {
            FactStatus.NOT_APPLICABLE
        }

    def test_a_mixed_ledger_reports_only_the_real_gaps(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("binary", FactAvailability(FactStatus.PRESENT))
        ledger.declare("vtables", FactAvailability(FactStatus.NOT_APPLICABLE))
        ledger.declare("graph", FactAvailability(FactStatus.FAILED))

        assert ledger.missing_families(
            ["binary", "vtables", "graph", "never-declared"]
        ) == ("graph", "never-declared")


class TestAnAgreeingOverrideDoesNotContradictItsFallback:
    """Codex review: the upgrade guard was broader than the argument for it.

    `for_entity` substitutes `NOT_COLLECTED` for a `NOT_APPLICABLE` fallback on
    an undeclared family, to stop an override manufacturing availability
    nobody declared. Applied unconditionally, it also rewrote an override that
    *agreed*: a `NOT_APPLICABLE` fallback with a `NOT_APPLICABLE` override
    resolved to `NOT_COLLECTED`, so `for_family` said "nothing here to be
    missing" while `for_entity` reported a gap for the same explicit status.

    Same conflation `missing_families` had, reached from the other side.
    """

    @staticmethod
    def _ledger() -> AvailabilityLedger:
        return AvailabilityLedger(
            unknown_family_default=FactAvailability(FactStatus.NOT_APPLICABLE)
        )

    def test_a_matching_not_applicable_override_is_preserved(self) -> None:
        ledger = self._ledger()
        ledger.override("vtables", "E1", FactAvailability(FactStatus.NOT_APPLICABLE))

        assert ledger.for_entity("vtables", "E1").status is FactStatus.NOT_APPLICABLE

    def test_the_two_accessors_agree_about_the_same_explicit_status(self) -> None:
        """The invariant that actually broke, stated directly."""
        ledger = self._ledger()
        ledger.override("vtables", "E1", FactAvailability(FactStatus.NOT_APPLICABLE))

        assert (
            ledger.for_entity("vtables", "E1").status
            == ledger.for_family("vtables").status
        )

    @pytest.mark.parametrize("status", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_the_upgrade_is_still_blocked(self, status: FactStatus) -> None:
        """The guard's actual purpose must survive narrowing its condition."""
        ledger = self._ledger()
        ledger.override("vtables", "E1", FactAvailability(status))

        resolved = ledger.for_entity("vtables", "E1")
        assert not resolved.comparable
        assert resolved.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("status", _GAP_STATUSES)
    def test_a_worse_override_still_wins_on_its_own(self, status: FactStatus) -> None:
        """`narrowed` already blocks these; the substitution must not reshape them.

        `FAILED` is the case with teeth: an earlier round of this module lost a
        failure's diagnostics by substituting a bare `NOT_COLLECTED`, so a
        parse error must still arrive as a parse error.
        """
        ledger = self._ledger()
        ledger.override(
            "vtables", "E1", FactAvailability(status, diagnostics=("boom",))
        )

        resolved = ledger.for_entity("vtables", "E1")
        assert not resolved.comparable
        assert "boom" in resolved.diagnostics

    def test_a_declared_family_is_unaffected(self) -> None:
        """The guard only ever applied to undeclared families; keep it that way."""
        ledger = AvailabilityLedger()
        ledger.declare("vtables", FactAvailability(FactStatus.NOT_APPLICABLE))
        ledger.override("vtables", "E1", FactAvailability(FactStatus.NOT_APPLICABLE))

        assert ledger.for_entity("vtables", "E1").status is FactStatus.NOT_APPLICABLE


class TestProvenanceFollowsTheSurvivingStatus:
    """Codex review: a failure could be attributed to the wrong producer.

    The field-by-field merge always let a non-empty override value win, which
    is right when the override's status survives and wrong when it does not:
    narrowing `FAILED(producer="dwarf")` with `PRESENT(producer="clang",
    recipe="r1")` produced `FAILED(producer="clang", recipe="r1")` — a dwarf
    parse failure attributed to clang.
    """

    def test_a_failure_keeps_its_own_producer(self) -> None:
        result = FactAvailability(FactStatus.FAILED, producer="dwarf").narrowed(
            FactAvailability(FactStatus.PRESENT, producer="clang", recipe="r1")
        )

        assert result.status is FactStatus.FAILED
        assert result.producer == "dwarf"

    def test_not_collected_acquires_no_provenance(self) -> None:
        """The review's second example, which its proposed rule did not close.

        "Winner leads, loser fills gaps" still let `NOT_COLLECTED` inherit a
        producer, because the winner left the field blank. A status meaning
        nothing ran must name nobody.
        """
        result = FactAvailability(FactStatus.NOT_COLLECTED).narrowed(
            FactAvailability(
                FactStatus.PRESENT, producer="clang", recipe="r1", scope="all"
            )
        )

        assert result.status is FactStatus.NOT_COLLECTED
        assert (result.producer, result.recipe, result.scope) == ("", "", "")

    def test_not_applicable_keeps_a_peers_provenance(self) -> None:
        """Written expecting the opposite, and it failed — correctly.

        `NOT_APPLICABLE` reads like it belongs with `NOT_COLLECTED`: nothing to
        run, so nobody ran. But it is the *least* worse status, so it can only
        survive a merge against itself, and then the other record is also
        `NOT_APPLICABLE` — a peer's legitimate statement, not provenance
        inherited across a disagreement. Blanking it would discard
        information, which is the one direction this package may not err in.
        """
        result = FactAvailability(FactStatus.NOT_APPLICABLE).narrowed(
            FactAvailability(FactStatus.NOT_APPLICABLE, producer="clang")
        )

        assert result.status is FactStatus.NOT_APPLICABLE
        assert result.producer == "clang"

    def test_an_explicitly_stated_producer_survives(self) -> None:
        """Refusing *inherited* provenance must not erase a stated one."""
        result = FactAvailability(FactStatus.NOT_COLLECTED, producer="stated").narrowed(
            FactAvailability(FactStatus.PRESENT, producer="clang")
        )

        assert result.producer == "stated"

    @pytest.mark.parametrize("swap", [False, True])
    def test_a_stated_producer_survives_a_tie_in_either_order(self, swap: bool) -> None:
        """The blanking must not fire on a record stating the same status.

        A tie picks a winner by operand position, and the loser was blanked
        unconditionally — so two `NOT_COLLECTED` records, one naming a
        producer, kept it in one order and dropped it in the other
        (CodeRabbit review). Both orders are asserted rather than the one that
        reproduced, because "which side was written first" is exactly what a
        merge may not depend on.
        """
        stated = FactAvailability(FactStatus.NOT_COLLECTED, producer="stated")
        silent = FactAvailability(FactStatus.NOT_COLLECTED)
        left, right = (silent, stated) if swap else (stated, silent)

        result = left.narrowed(right)

        assert result.status is FactStatus.NOT_COLLECTED
        assert result.producer == "stated"

    @pytest.mark.parametrize("swap", [False, True])
    def test_a_tie_does_not_invent_provenance(self, swap: bool) -> None:
        """The control: neither side stating one must not produce one."""
        left = FactAvailability(FactStatus.NOT_COLLECTED)
        right = FactAvailability(FactStatus.NOT_COLLECTED)
        if swap:
            left, right = right, left

        assert left.narrowed(right).producer == ""

    @pytest.mark.parametrize("swap", [False, True])
    def test_provenance_still_does_not_cross_from_a_different_status(
        self, swap: bool
    ) -> None:
        """The rule the blanking exists for, unchanged by the tie fix.

        A `PRESENT` record's producer must not be inherited by a
        `NOT_COLLECTED` result: nothing ran, so there is no producer to name.
        """
        collected = FactAvailability(FactStatus.PRESENT, producer="clang")
        absent = FactAvailability(FactStatus.NOT_COLLECTED)
        left, right = (absent, collected) if swap else (collected, absent)

        result = left.narrowed(right)

        assert result.status is FactStatus.NOT_COLLECTED
        assert result.producer == ""

    @pytest.mark.parametrize("status", [FactStatus.UNSUPPORTED, FactStatus.FAILED])
    def test_a_gap_with_a_producer_still_names_it(self, status: FactStatus) -> None:
        """`UNSUPPORTED`/`FAILED` are gaps *with* a producer worth naming."""
        result = FactAvailability(status, producer="castxml").narrowed(
            FactAvailability(FactStatus.PRESENT, producer="clang")
        )

        assert result.producer == "castxml"

    def test_the_earlier_narrowing_fix_is_intact(self) -> None:
        """An override that narrows still wins, and still inherits.

        This is the case the previous review round added; the fix for this one
        must not undo it.
        """
        result = FactAvailability(
            FactStatus.PRESENT, producer="clang", recipe="r1", scope="all"
        ).narrowed(FactAvailability(FactStatus.PARTIAL, scope="headers-only"))

        assert result.status is FactStatus.PARTIAL
        assert (result.producer, result.recipe, result.scope) == (
            "clang",
            "r1",
            "headers-only",
        )

    def test_diagnostics_from_both_sides_are_kept(self) -> None:
        """Provenance narrowing must not drop evidence either side recorded."""
        result = FactAvailability(
            FactStatus.FAILED, producer="dwarf", diagnostics=("parse error",)
        ).narrowed(
            FactAvailability(FactStatus.PRESENT, producer="clang", diagnostics=("ok",))
        )

        assert set(result.diagnostics) == {"parse error", "ok"}


class TestARepeatedOverrideIsRefused:
    """Codex review: the mutation API had the deserializer's defect.

    `from_dict` rejects duplicate override rows, but two extraction passes
    calling `override()` for one entity silently discarded the first: a
    `FAILED` observation followed by `PRESENT` made `for_entity` comparable
    and dropped the failure permanently, while the reverse call order reached
    the opposite conclusion.
    """

    def test_a_second_override_for_one_entity_is_refused(self) -> None:
        ledger = AvailabilityLedger()
        ledger.override("layout", "E1", FactAvailability(FactStatus.FAILED))

        with pytest.raises(ValueError, match="already recorded"):
            ledger.override("layout", "E1", FactAvailability(FactStatus.PRESENT))

    def test_the_first_record_survives_the_refusal(self) -> None:
        """Refusing must not corrupt what was already there."""
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT))
        ledger.override(
            "layout", "E1", FactAvailability(FactStatus.FAILED, diagnostics=("boom",))
        )

        with pytest.raises(ValueError):
            ledger.override("layout", "E1", FactAvailability(FactStatus.PRESENT))

        resolved = ledger.for_entity("layout", "E1")
        assert resolved.status is FactStatus.FAILED
        assert "boom" in resolved.diagnostics

    def test_other_entities_and_families_are_unaffected(self) -> None:
        ledger = AvailabilityLedger()
        ledger.override("layout", "E1", FactAvailability(FactStatus.PRESENT))
        ledger.override("layout", "E2", FactAvailability(FactStatus.PARTIAL))
        ledger.override("graph", "E1", FactAvailability(FactStatus.FAILED))

        assert len(ledger.overrides) == 3

    def test_declare_deliberately_remains_last_wins(self) -> None:
        """Pinned because it is the same hazard, left alone on purpose.

        `declare` is documented "last declaration wins" — a stated contract
        rather than an accident — and it carries the same risk of a later
        `PRESENT` burying an earlier `FAILED`. Changing a documented behaviour
        belongs with whoever owns that contract, so it is pinned here to make
        the asymmetry deliberate and visible rather than silently divergent.
        """
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.FAILED))
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT))

        assert ledger.for_family("layout").status is FactStatus.PRESENT
