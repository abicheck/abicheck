# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""What ``from_dict``/``to_dict`` accept, refuse, and round-trip.

Split out of ``test_availability.py`` when that file crossed this repo's
1200-line test cap. The split is by subject rather than by size: everything
here is about the *document* boundary — which malformed inputs are rejected
rather than coerced, and what a written document says about the object it came
from. The sibling file keeps the in-memory contracts (narrowing, lookup,
immutability, the gap/comparable partition).

Several of these exist because a serializer disagreed with its own reader, so
they are grouped where that comparison is easy to see.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.availability import (
    _GAP_STATUSES as _MODULE_GAP_STATUSES,
    AvailabilityLedger,
    Confidence,
    FactAvailability,
    FactStatus,
)
from abicheck.storage.identity import EntityId, OccurrenceId
from abicheck.storage.versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    StorageVersions,
    check_reader_compatibility,
)

_STATUSES = list(FactStatus)
_CONFIDENCES = list(Confidence)

#: Imported from the module under test rather than restated here. This list
#: previously lived only in this file, with a comment explaining the very
#: distinction `missing_families` was failing to make — so the tests knew
#: something the production predicate did not, and nothing could notice.
_GAP_STATUSES = sorted(_MODULE_GAP_STATUSES, key=lambda s: s.value)


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


class TestDuplicateOverridesAreRefused:
    """Codex review: serialized row order decided availability.

    Assignment kept the last row, so a ledger holding both a `failed` and a
    `present` override for one entity answered differently depending on array
    order — reversing it flipped `for_entity` from non-comparable to
    comparable.
    """

    @staticmethod
    def _rows() -> list[dict[str, object]]:
        return [
            {
                "family": "layout",
                "entity": "ns::Foo",
                "availability": {"status": "failed"},
            },
            {
                "family": "layout",
                "entity": "ns::Foo",
                "availability": {"status": "present"},
            },
        ]

    def test_duplicate_rows_are_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate availability override"):
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": self._rows(),
                }
            )

    def test_the_refusal_does_not_depend_on_row_order(self) -> None:
        """Both orderings refuse; neither silently wins."""
        for rows in (self._rows(), list(reversed(self._rows()))):
            with pytest.raises(ValueError, match="duplicate availability override"):
                AvailabilityLedger.from_dict(
                    {
                        "families": {},
                        "unknown_family_default": {"status": "not_collected"},
                        "overrides": rows,
                    }
                )

    def test_distinct_entities_in_one_family_are_fine(self) -> None:
        ledger = AvailabilityLedger.from_dict(
            {
                "families": {},
                "unknown_family_default": {"status": "not_collected"},
                "overrides": [
                    {
                        "family": "layout",
                        "entity": "ns::Foo",
                        "availability": {"status": "failed"},
                    },
                    {
                        "family": "layout",
                        "entity": "ns::Bar",
                        "availability": {"status": "present"},
                    },
                ],
            }
        )

        assert ledger.for_entity("layout", "ns::Foo").status is FactStatus.FAILED

    def test_the_same_entity_in_distinct_families_is_fine(self) -> None:
        ledger = AvailabilityLedger.from_dict(
            {
                "families": {},
                "unknown_family_default": {"status": "not_collected"},
                "overrides": [
                    {
                        "family": "layout",
                        "entity": "ns::Foo",
                        "availability": {"status": "failed"},
                    },
                    {
                        "family": "vtables",
                        "entity": "ns::Foo",
                        "availability": {"status": "present"},
                    },
                ],
            }
        )

        assert ledger.for_entity("layout", "ns::Foo").status is FactStatus.FAILED

    def test_an_ordinary_ledger_still_round_trips(self) -> None:
        """`to_dict` cannot emit duplicates, so writers are unaffected."""
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT))
        ledger.override("layout", "ns::Foo", FactAvailability(FactStatus.FAILED))

        assert AvailabilityLedger.from_dict(ledger.to_dict()).to_dict() == (
            ledger.to_dict()
        )


class TestTheSerializedFallbackIsTheEffectiveOne:
    """Codex review: three consumers of one field, and they disagreed.

    The construction door refuses a comparable `unknown_family_default`,
    `for_family` coerces one, and `to_dict` wrote the raw field. A ledger
    whose fallback was reassigned after construction therefore answered
    `not_collected` while serializing `present`: a document `from_dict`
    refuses to reload, and that a consumer without that validation would read
    as available evidence.

    A later round closed the assignment itself — `__setattr__` now applies
    the same rule the constructor does, so *plain* reassignment raises rather
    than being silently downgraded (`TestAReassignedFallbackIsRefused`
    below). The read-time coercion these tests cover is kept as the second
    line: it is what still holds if the state is reached some other way, and
    that is exactly how these tests now build it. Removing it because the
    door above it closed would leave the disagreement between `for_family`
    and `to_dict` live for any path that bypasses assignment.
    """

    @staticmethod
    def _reassigned(status: FactStatus) -> AvailabilityLedger:
        ledger = AvailabilityLedger()
        # Deliberately past `__setattr__`: a plain assignment is refused now,
        # and the property under test is what happens when the bad state
        # exists anyway.
        object.__setattr__(ledger, "unknown_family_default", FactAvailability(status))
        return ledger

    @pytest.mark.parametrize("status", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_a_reassigned_comparable_fallback_serializes_as_not_collected(
        self, status: FactStatus
    ) -> None:
        ledger = self._reassigned(status)

        assert ledger.to_dict()["unknown_family_default"] == {"status": "not_collected"}

    @pytest.mark.parametrize("status", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_the_document_round_trips(self, status: FactStatus) -> None:
        """The invariant that actually broke: write then read must work."""
        ledger = self._reassigned(status)

        reloaded = AvailabilityLedger.from_dict(ledger.to_dict())

        assert reloaded.for_family("undeclared").status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("status", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_serialization_agrees_with_lookup(self, status: FactStatus) -> None:
        """The general contract, not just the two statuses that broke it."""
        ledger = self._reassigned(status)

        assert (
            ledger.to_dict()["unknown_family_default"]
            == ledger.for_family("undeclared").to_dict()
        )

    @pytest.mark.parametrize("status", _GAP_STATUSES)
    def test_a_legitimate_fallback_is_serialized_untouched(
        self, status: FactStatus
    ) -> None:
        """Coercing the invalid case must not flatten the valid ones.

        A `FAILED` fallback carrying a parse error is exactly the evidence an
        earlier round of this module lost by substituting a bare
        `NOT_COLLECTED`; it must survive serialization intact.
        """
        fallback = FactAvailability(
            status, producer="castxml", diagnostics=("parse failed",)
        )
        ledger = AvailabilityLedger(unknown_family_default=fallback)

        assert ledger.to_dict()["unknown_family_default"] == fallback.to_dict()

    @pytest.mark.parametrize("status", list(FactStatus))
    def test_effective_fallback_is_the_one_definition(self, status: FactStatus) -> None:
        """Both consumers must read the same property, not re-derive it.

        Over every status, not only the two that broke: the contract is that
        serialization and lookup never disagree, whatever the field holds.
        """
        ledger = self._reassigned(status)
        effective = ledger.effective_unknown_family_default

        assert not effective.comparable
        assert ledger.for_family("undeclared") == effective
        assert ledger.to_dict()["unknown_family_default"] == effective.to_dict()


class TestScalarDiagnosticsAreRefused:
    """Codex review: a hand-edited string became one diagnostic per character.

    A string is a `Sequence`, so `tuple(str(d) for d in raw)` turned
    `"diagnostics": "parse error"` into eleven single-character entries and
    serialized it back that way — destroying the extraction error a reader
    needs for auditing, with no error anywhere.

    This is the only field in the package where the failure is silent. The
    sibling record lists reject a scalar already, but only incidentally: their
    elements must be mappings, so iterating a string fails on the first one.
    Diagnostics are strings, so char-iteration succeeds and looks like data.
    """

    @pytest.mark.parametrize(
        "value", ["parse error", "", b"bytes", 5, None, {"a": 1}, object()]
    )
    def test_a_non_sequence_or_string_is_refused(self, value: object) -> None:
        with pytest.raises(TypeError, match="diagnostics"):
            FactAvailability.from_dict({"status": "failed", "diagnostics": value})

    def test_the_reported_string_is_not_split(self) -> None:
        """The literal case: eleven characters, or a clear refusal."""
        with pytest.raises(TypeError):
            FactAvailability.from_dict(
                {"status": "failed", "diagnostics": "parse error"}
            )

    @pytest.mark.parametrize(
        "value",
        [["parse error"], ("a", "b"), [], ()],
    )
    def test_a_real_sequence_still_loads(self, value: object) -> None:
        loaded = FactAvailability.from_dict({"status": "failed", "diagnostics": value})

        assert loaded.diagnostics == tuple(value)  # type: ignore[arg-type]

    def test_an_absent_field_is_empty(self) -> None:
        assert FactAvailability.from_dict({"status": "failed"}).diagnostics == ()

    def test_a_well_formed_diagnostic_round_trips_intact(self) -> None:
        """The value the guard exists to protect: one message, not eleven."""
        original = FactAvailability(
            FactStatus.FAILED, diagnostics=("parse error at line 3",)
        )

        assert FactAvailability.from_dict(original.to_dict()) == original

    def test_the_sibling_record_lists_also_refuse_a_scalar(self) -> None:
        """Pinning the incidental rejection, so it cannot become silent too.

        These raise because their elements must be mappings, not because
        anything checks. If a future change made an element parseable from a
        string, they would acquire exactly this defect.
        """
        with pytest.raises((TypeError, ValueError, KeyError)):
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": "oops",
                }
            )


class TestDecisionKeysAreRejectedNotCoerced:
    """Codex review: insertion order could flip a comparability verdict.

    `{1: {"status": "failed"}, "1": {"status": "present"}}` — which a YAML
    loader or a Python adapter can produce — collapsed to one entry under
    `str()`, and *which* record survived depended on iteration order.
    Reversing it flipped `for_family("1")` from non-comparable to comparable,
    so a discarded `FAILED` record could license a conclusion.

    Same defect `canonical_form` already rejects for mapping keys, in the one
    place that had not adopted the rule.
    """

    def test_the_reported_collision_is_refused(self) -> None:
        with pytest.raises(TypeError, match="family name"):
            AvailabilityLedger.from_dict(
                {
                    "overrides": [],
                    "families": {
                        1: {"status": "failed"},
                        "1": {"status": "present"},
                    },
                }
            )

    @pytest.mark.parametrize("key", [1, 1.0, True, None, (1,)])
    def test_a_non_string_family_key_is_refused(self, key: object) -> None:
        with pytest.raises(TypeError, match="family name"):
            AvailabilityLedger.from_dict(
                {"families": {key: {"status": "present"}}, "overrides": []}
            )

    @pytest.mark.parametrize("field", ["family", "entity"])
    @pytest.mark.parametrize("key", [1, True, None])
    def test_a_non_string_override_key_is_refused(
        self, field: str, key: object
    ) -> None:
        row: dict[str, object] = {
            "family": "layout",
            "entity": "E1",
            "availability": {"status": "present"},
        }
        row[field] = key

        with pytest.raises(TypeError, match="override"):
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": [row],
                }
            )

    def test_a_well_formed_ledger_still_round_trips(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT))
        ledger.override("layout", "E1", FactAvailability(FactStatus.PARTIAL))

        assert AvailabilityLedger.from_dict(ledger.to_dict()) == ledger

    def test_the_informational_version_axis_still_parses_defensively(self) -> None:
        """The distinction is deliberate, so it is pinned rather than assumed.

        The ledger *rejects* a non-string key, because everything in it is
        read by a decision. `section_schema_versions` is one of the five
        informational axes — no decision reads them, and this repo's rule is
        that a hand-edited package must never abort a load — so it degrades
        instead. What it degrades *to* changed after four review rounds: it
        used to be `str(k)`, and is now "dropped", since no stringification
        of a non-string key is both injective and order-independent. The
        distinction being pinned here is unaffected: reject versus degrade.
        """
        versions = StorageVersions.from_dict(
            {
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
                "section_schema_versions": {1: 2},
            }
        )

        assert versions.section_schema_versions == {}


class TestProvenanceIsRejectedNotCoerced:
    """Codex review: `recipe: 1` and `recipe: "1"` became the same record.

    `recipe` and `producer` are the fields that decide whether two `PRESENT`
    records may be compared, so erasing a distinction between them makes
    invalid evidence look equivalent to valid evidence — a worse outcome than
    the coercion's size suggests.
    """

    @pytest.mark.parametrize(
        "field", ["producer", "producer_version", "recipe", "scope"]
    )
    @pytest.mark.parametrize("value", [1, 1.0, True, None, ["r1"], {"r": 1}])
    def test_a_non_string_provenance_field_is_refused(
        self, field: str, value: object
    ) -> None:
        with pytest.raises(TypeError, match=field):
            FactAvailability.from_dict({"status": "present", field: value})

    def test_the_reported_collapse_no_longer_happens(self) -> None:
        """`1` and `"1"` must not deserialize to the same record."""
        with pytest.raises(TypeError, match="recipe"):
            FactAvailability.from_dict({"status": "present", "recipe": 1})

        assert (
            FactAvailability.from_dict({"status": "present", "recipe": "1"}).recipe
            == "1"
        )

    def test_absent_fields_still_default_to_empty(self) -> None:
        loaded = FactAvailability.from_dict({"status": "present"})

        assert (loaded.producer, loaded.producer_version) == ("", "")
        assert (loaded.recipe, loaded.scope) == ("", "")

    def test_a_fully_populated_record_round_trips(self) -> None:
        original = FactAvailability(
            FactStatus.PRESENT,
            producer="clang",
            producer_version="18.1.0",
            recipe="r1",
            scope="headers-only",
        )

        assert FactAvailability.from_dict(original.to_dict()) == original


class TestASequenceShapedFamilyTableIsRefused:
    """Codex review: the previous round's finding, through a different door.

    `dict()` accepts a sequence of pairs and collapses duplicate names
    *before* any key validation runs, so rows declaring one family `failed`
    then `present` resolved as comparable while the reverse order resolved as
    non-comparable. Rejecting non-string keys does nothing when the container
    itself dedupes first — the shape has to be checked before the keys are.
    """

    ROWS = [("layout", {"status": "failed"}), ("layout", {"status": "present"})]

    def test_the_reported_sequence_is_refused(self) -> None:
        with pytest.raises(TypeError, match="families must be a mapping"):
            AvailabilityLedger.from_dict({"families": self.ROWS, "overrides": []})

    def test_row_order_can_no_longer_decide_a_verdict(self) -> None:
        """Both orders must fail the same way, not disagree."""
        with pytest.raises(TypeError):
            AvailabilityLedger.from_dict({"families": self.ROWS, "overrides": []})
        with pytest.raises(TypeError):
            AvailabilityLedger.from_dict(
                {"families": list(reversed(self.ROWS)), "overrides": []}
            )

    @pytest.mark.parametrize(
        "value", ["layout", [1, 2], (("a", {}),), 5, None, {("a",)}]
    )
    def test_a_non_mapping_families_value_is_refused(self, value: object) -> None:
        with pytest.raises(TypeError, match="families"):
            AvailabilityLedger.from_dict({"families": value, "overrides": []})

    def test_a_real_mapping_still_loads(self) -> None:
        ledger = AvailabilityLedger.from_dict(
            {
                "families": {"layout": {"status": "present"}},
                "overrides": [],
                "unknown_family_default": {"status": "not_collected"},
            }
        )

        assert ledger.for_family("layout").status is FactStatus.PRESENT

    def test_an_absent_collection_is_refused_rather_than_read_as_empty(self) -> None:
        """This test previously pinned the opposite, and was the bug.

        It asserted that an absent `families` key parses to `{}` — which is
        exactly the reading this package's third invariant forbids: an empty
        collection claims "the producer ran and established nothing is
        there". A truncated ledger that keeps a `PRESENT` family and loses
        its override rows would then have `for_entity` answer with the
        comparable family record, licensing a compatibility conclusion from
        damage (Codex review).

        `to_dict` writes both collections unconditionally, so an absent one
        means the document did not come from this writer.
        """
        for document in ({}, {"families": {}}, {"overrides": []}):
            with pytest.raises(ValueError, match="missing required field"):
                AvailabilityLedger.from_dict(document)

        # The control: both present and empty is still a real, loadable
        # ledger that establishes there is nothing in either collection.
        assert (
            AvailabilityLedger.from_dict(
                {
                    "families": {},
                    "unknown_family_default": {"status": "not_collected"},
                    "overrides": [],
                }
            ).families
            == {}
        )


class TestTheConstructorAppliesTheDocumentRules:
    """Codex review, twice: every guard was written for `from_dict` only.

    Malformed input does not only arrive as a parsed document — it arrives
    from an adapter building these objects from dynamically sourced data, and
    the constructor reproduced the same defects exactly. Validating at the
    document boundary is not enough for a publicly constructible type.

    These live in the *documents* file deliberately: they are the same rules
    those documents are parsed under, checked at the other door.
    """

    def test_a_scalar_diagnostics_string_is_refused(self) -> None:
        with pytest.raises(TypeError, match="diagnostics"):
            FactAvailability(FactStatus.FAILED, diagnostics="parse error")

    @pytest.mark.parametrize(
        "field", ["producer", "producer_version", "recipe", "scope"]
    )
    def test_non_string_provenance_is_refused(self, field: str) -> None:
        with pytest.raises(TypeError, match=field):
            FactAvailability(FactStatus.PRESENT, **{field: 1})

    def test_the_pre_existing_status_guard_still_fires(self) -> None:
        """Pinned because adding validation is how you shadow validation.

        The first attempt at this fix added a *second* `__post_init__`, which
        silently replaced the one checking `status`/`confidence` — caught by
        that guard's own existing test. The rules are merged into one method.
        """
        with pytest.raises(TypeError, match="status must be a FactStatus"):
            FactAvailability("present")  # type: ignore[arg-type]

    def test_a_well_formed_record_is_unaffected(self) -> None:
        record = FactAvailability(
            FactStatus.FAILED,
            producer="dwarf",
            recipe="r1",
            diagnostics=["parse error"],
        )

        assert record.diagnostics == ("parse error",)
        assert FactAvailability.from_dict(record.to_dict()) == record

    @pytest.mark.parametrize("key", [1, None, True])
    def test_ledger_mutators_validate_their_keys(self, key: object) -> None:
        ledger = AvailabilityLedger()

        with pytest.raises(TypeError, match="family"):
            ledger.declare(key, FactAvailability(FactStatus.PRESENT))  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="override"):
            ledger.override(key, "E1", FactAvailability(FactStatus.PRESENT))  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="override"):
            ledger.override("layout", key, FactAvailability(FactStatus.PRESENT))  # type: ignore[arg-type]


class TestAMalformedDocumentDegradesToTheFailClosedValue:
    """A versions block that is not a mapping states nothing, and must read so.

    ``StorageVersions.from_dict`` degrades rather than raising, because this
    module's contract is that a malformed *informational* field never aborts a
    load. The direction of the degrade is the whole finding: returning
    ``StorageVersions()`` — the dataclass defaults — makes a malformed block
    read as "written by exactly this build" and pass
    ``check_reader_compatibility``, while the same nothing spelled as an empty
    mapping yields ``UNSTATED_VERSION`` and is refused. Degrading has to land
    on the fail-closed value, not the optimistic one.
    """

    @pytest.mark.parametrize("raw", ["x", ["x"], 1, None])
    def test_a_non_mapping_reads_as_unstated(self, raw: Any) -> None:
        versions = StorageVersions.from_dict(raw)
        assert versions == StorageVersions.from_dict({})
        assert not check_reader_compatibility(versions).readable

    def test_the_writer_defaults_would_have_been_readable(self) -> None:
        """Pins why the distinction matters, not just that it exists."""
        assert check_reader_compatibility(StorageVersions()).readable

    @pytest.mark.parametrize(
        "cls", [FactAvailability, AvailabilityLedger, EntityId, OccurrenceId]
    )
    @pytest.mark.parametrize("raw", ["x", ["x"], 1, None])
    def test_a_decision_bearing_document_is_refused_cleanly(
        self, cls: Any, raw: Any
    ) -> None:
        """Never a raw ``AttributeError`` from inside the parse.

        A caller distinguishing "malformed package" from "this reader is
        broken" catches ``TypeError``/``ValueError``; an ``AttributeError``
        escaping ``.get`` on a scalar reads as the second when it is the
        first.
        """
        with pytest.raises((TypeError, ValueError)):
            cls.from_dict(raw)


class TestTheInformationalMappingAxisRoundTrips:
    """`section_schema_versions` is a mapping, and was written verbatim.

    The two fail-closed axes already held "what is written reads back the
    same way". This one did not: `{1: 1}` reloaded as `{"1": 1}`,
    `{"x": "bad"}` as `{"x": 0}`, and mixed key types raised from `sorted`
    part-way through serialization (Codex review). It is normalized on the
    way out by the same rules its own reader applies.
    """

    @pytest.mark.parametrize(
        "raw",
        [{1: 1}, {"x": "bad"}, {1: 1, "1": 2}, {"a": 3, "b": 1}, {"s": True}],
    )
    def test_the_document_is_what_its_reader_produces(self, raw: Any) -> None:
        emitted = StorageVersions(section_schema_versions=raw).to_dict()

        assert StorageVersions.from_dict(emitted).to_dict() == emitted

    def test_serialization_does_not_raise_on_mixed_key_types(self) -> None:
        """The half that was not a wrong value but an aborted write.

        Mixed key types used to raise from `sorted` part-way through the
        write. The non-string key is dropped now rather than stringified —
        see `TestOnlyAStringSectionKeySurvives` in the parity file for why —
        but the property this test is about is that the write completes.
        """
        emitted = StorageVersions(section_schema_versions={1: 1, "b": 2}).to_dict()

        assert emitted["section_schema_versions"] == {"b": 2}

    def test_a_well_formed_mapping_is_untouched(self) -> None:
        """Normalizing the malformed case must not disturb the valid one."""
        versions = StorageVersions(section_schema_versions={"entities": 3})

        assert versions.to_dict()["section_schema_versions"] == {"entities": 3}


class TestAReassignedFallbackIsRefused:
    """The assignment door, closed after the read-time coercion was.

    `AvailabilityLedger` is a mutable dataclass with public fields, so the
    guards that ran once at construction were bypassed by
    `ledger.unknown_family_default = ...`. A non-record there made
    `for_family` and `to_dict` raise `AttributeError` at
    `fallback.comparable` (Codex review), and a *comparable* record was
    silently downgraded at read rather than refused — the same value the
    constructor rejects outright, treated two different ways depending on
    which door it came through.
    """

    @pytest.mark.parametrize("value", ["bad", 1, None, ["x"], {"status": "present"}])
    def test_a_non_record_is_refused(self, value: Any) -> None:
        ledger = AvailabilityLedger()

        with pytest.raises(TypeError):
            ledger.unknown_family_default = value

    @pytest.mark.parametrize("status", [FactStatus.PRESENT, FactStatus.PARTIAL])
    def test_a_comparable_record_is_refused(self, status: FactStatus) -> None:
        """The same rule the constructor applies, at the same strength."""
        ledger = AvailabilityLedger()

        with pytest.raises(ValueError, match="must not be comparable"):
            ledger.unknown_family_default = FactAvailability(status)

    @pytest.mark.parametrize("status", _GAP_STATUSES)
    def test_a_legitimate_fallback_still_assigns(self, status: FactStatus) -> None:
        """Refusing the invalid case must not close the valid one."""
        ledger = AvailabilityLedger()

        ledger.unknown_family_default = FactAvailability(status)

        assert ledger.for_family("undeclared").status is status

    def test_the_other_fields_are_guarded_on_reassignment_too(self) -> None:
        """One door, not one field: the mappings are rebindable as well."""
        ledger = AvailabilityLedger()

        with pytest.raises(TypeError):
            ledger.families = ["layout"]
        with pytest.raises(TypeError):
            ledger.overrides = {("layout", "E1"): "not a record"}
