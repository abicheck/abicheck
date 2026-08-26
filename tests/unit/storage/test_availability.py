# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ADR-062 D3's explicit fact availability.

The invariant under test throughout: **a comparison may never infer safety
from an empty collection.** Every test here is a way of asking whether the
primitive still makes that unwritable.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.availability import (
    AvailabilityLedger,
    Confidence,
    FactAvailability,
    FactStatus,
)

_STATUSES = list(FactStatus)
_CONFIDENCES = list(Confidence)

#: Statuses that mean "evidence is missing for a reason", as opposed to
#: NOT_APPLICABLE, which means "there is nothing here to be missing".
_GAP_STATUSES = [
    FactStatus.NOT_COLLECTED,
    FactStatus.UNSUPPORTED,
    FactStatus.FAILED,
]


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


class TestRoundTrip:
    @given(
        st.sampled_from(_STATUSES),
        st.sampled_from(_CONFIDENCES),
        st.text(max_size=12),
        st.lists(st.text(max_size=12), max_size=3),
    )
    def test_availability_round_trips(
        self,
        status: FactStatus,
        confidence: Confidence,
        producer: str,
        diagnostics: list[str],
    ) -> None:
        record = FactAvailability(
            status=status,
            producer=producer,
            producer_version="1.0",
            recipe="r1",
            scope="all",
            confidence=confidence,
            diagnostics=tuple(diagnostics),
        )

        assert FactAvailability.from_dict(record.to_dict()) == record

    def test_defaults_are_omitted_and_restored(self) -> None:
        record = FactAvailability(FactStatus.PRESENT)

        assert record.to_dict() == {"status": "present"}
        assert FactAvailability.from_dict({"status": "present"}) == record

    def test_an_unknown_status_is_refused_not_downgraded(self) -> None:
        """Silently downgrading would report "no evidence" for real evidence.

        A wrong answer dressed as a conservative one is still wrong; refusing
        a package a reader cannot understand is `versioning.py`'s job, and
        this error is what routes the caller there.
        """
        with pytest.raises(ValueError, match="unknown fact status"):
            FactAvailability.from_dict({"status": "quantum"})

    def test_an_unknown_confidence_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown confidence"):
            FactAvailability.from_dict({"status": "present", "confidence": "vibes"})

    @given(st.permutations(["a", "b", "c"]))
    def test_ledger_serialization_is_order_independent(
        self, families: list[str]
    ) -> None:
        ledger = AvailabilityLedger()
        for family in families:
            ledger.declare(family, FactAvailability(FactStatus.PRESENT))

        reference = AvailabilityLedger()
        for family in ("a", "b", "c"):
            reference.declare(family, FactAvailability(FactStatus.PRESENT))

        assert ledger.to_dict() == reference.to_dict()

    def test_ledger_round_trips_with_overrides(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT, producer="dwarf"))
        ledger.override(
            "layout", "ns::Foo", FactAvailability(FactStatus.FAILED, diagnostics=("x",))
        )

        restored = AvailabilityLedger.from_dict(ledger.to_dict())

        assert restored.to_dict() == ledger.to_dict()
        assert restored.for_entity("layout", "ns::Foo").status is FactStatus.FAILED

    def test_overrides_serialize_as_a_list_not_a_nested_map(self) -> None:
        """Their natural key is a pair; no JSON object key carries one safely.

        A separator-joined `"family:entity"` key would collide the moment an
        entity name contained the separator — and C++ spellings contain every
        printable separator anyone reaches for.
        """
        ledger = AvailabilityLedger()
        ledger.override("layout", "ns::Foo::bar", FactAvailability(FactStatus.FAILED))

        payload = ledger.to_dict()

        assert isinstance(payload["overrides"], list)
        assert payload["overrides"][0]["entity"] == "ns::Foo::bar"

    def test_every_status_and_confidence_pair_round_trips(self) -> None:
        for status, confidence in itertools.product(_STATUSES, _CONFIDENCES):
            record = FactAvailability(status, confidence=confidence)
            assert FactAvailability.from_dict(record.to_dict()) == record


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
            AvailabilityLedger.from_dict({"unknown_family_default": {"status": status}})

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

    def test_post_construction_reassignment_is_coerced_at_read(self) -> None:
        """The ledger is mutable by design, so construction checks aren't enough.

        `declare`/`override` mutate it, so the fallback field can be
        reassigned too. `for_family` re-checks rather than trusting, and errs
        toward "no conclusion".
        """
        ledger = AvailabilityLedger()
        ledger.unknown_family_default = FactAvailability(FactStatus.PRESENT)

        answer = ledger.for_family("layout")

        assert not answer.comparable
        assert answer.status is FactStatus.NOT_COLLECTED

    def test_the_gap_is_still_reported_after_reassignment(self) -> None:
        ledger = AvailabilityLedger()
        ledger.unknown_family_default = FactAvailability(FactStatus.PARTIAL)

        assert ledger.missing_families(["layout", "graph"]) == ("graph", "layout")

    def test_for_entity_inherits_the_coerced_fallback(self) -> None:
        ledger = AvailabilityLedger()
        ledger.unknown_family_default = FactAvailability(FactStatus.PRESENT)

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
