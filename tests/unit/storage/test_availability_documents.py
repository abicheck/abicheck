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

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.availability import (
    _GAP_STATUSES as _MODULE_GAP_STATUSES,
    AvailabilityLedger,
    Confidence,
    FactAvailability,
    FactStatus,
)
from abicheck.storage.versioning import (
    COMPARISON_CONTRACT_VERSION,
    PACKAGE_FORMAT_VERSION,
    StorageVersions,
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
            AvailabilityLedger.from_dict({"overrides": self._rows()})

    def test_the_refusal_does_not_depend_on_row_order(self) -> None:
        """Both orderings refuse; neither silently wins."""
        for rows in (self._rows(), list(reversed(self._rows()))):
            with pytest.raises(ValueError, match="duplicate availability override"):
                AvailabilityLedger.from_dict({"overrides": rows})

    def test_distinct_entities_in_one_family_are_fine(self) -> None:
        ledger = AvailabilityLedger.from_dict(
            {
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
                ]
            }
        )

        assert ledger.for_entity("layout", "ns::Foo").status is FactStatus.FAILED

    def test_the_same_entity_in_distinct_families_is_fine(self) -> None:
        ledger = AvailabilityLedger.from_dict(
            {
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
                ]
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

    `__post_init__` refuses a comparable `unknown_family_default`,
    `for_family` coerces one, and `to_dict` wrote the raw field. A ledger
    whose fallback is reassigned after construction — a plain attribute
    assignment, which runs no `__post_init__`, on a class that is mutable by
    design — therefore answered `not_collected` while serializing `present`:
    a document `from_dict` refuses to reload, and that a consumer without
    that validation would read as available evidence.
    """

    @staticmethod
    def _reassigned(status: FactStatus) -> AvailabilityLedger:
        ledger = AvailabilityLedger()
        ledger.unknown_family_default = FactAvailability(status)
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

    @pytest.mark.parametrize(
        "status",
        [FactStatus.NOT_COLLECTED, FactStatus.UNSUPPORTED, FactStatus.FAILED],
    )
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
            AvailabilityLedger.from_dict({"overrides": "oops"})


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
                    "families": {
                        1: {"status": "failed"},
                        "1": {"status": "present"},
                    }
                }
            )

    @pytest.mark.parametrize("key", [1, 1.0, True, None, (1,)])
    def test_a_non_string_family_key_is_refused(self, key: object) -> None:
        with pytest.raises(TypeError, match="family name"):
            AvailabilityLedger.from_dict({"families": {key: {"status": "present"}}})

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
            AvailabilityLedger.from_dict({"overrides": [row]})

    def test_a_well_formed_ledger_still_round_trips(self) -> None:
        ledger = AvailabilityLedger()
        ledger.declare("layout", FactAvailability(FactStatus.PRESENT))
        ledger.override("layout", "E1", FactAvailability(FactStatus.PARTIAL))

        assert AvailabilityLedger.from_dict(ledger.to_dict()) == ledger

    def test_the_informational_version_axis_still_parses_defensively(self) -> None:
        """The distinction is deliberate, so it is pinned rather than assumed.

        `section_schema_versions` keeps its `str()` because it is one of the
        five informational axes: no decision reads them, and this repo's rule
        is that a hand-edited package must not abort a load. Everything in the
        availability ledger *is* read by a decision, which is why it rejects.
        """
        versions = StorageVersions.from_dict(
            {
                "package_format_version": PACKAGE_FORMAT_VERSION,
                "comparison_contract_version": COMPARISON_CONTRACT_VERSION,
                "section_schema_versions": {1: 2},
            }
        )

        assert versions.section_schema_versions == {"1": 2}


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
            AvailabilityLedger.from_dict({"families": self.ROWS})

    def test_row_order_can_no_longer_decide_a_verdict(self) -> None:
        """Both orders must fail the same way, not disagree."""
        with pytest.raises(TypeError):
            AvailabilityLedger.from_dict({"families": self.ROWS})
        with pytest.raises(TypeError):
            AvailabilityLedger.from_dict({"families": list(reversed(self.ROWS))})

    @pytest.mark.parametrize(
        "value", ["layout", [1, 2], (("a", {}),), 5, None, {("a",)}]
    )
    def test_a_non_mapping_families_value_is_refused(self, value: object) -> None:
        with pytest.raises(TypeError, match="families"):
            AvailabilityLedger.from_dict({"families": value})

    def test_a_real_mapping_still_loads(self) -> None:
        ledger = AvailabilityLedger.from_dict(
            {"families": {"layout": {"status": "present"}}}
        )

        assert ledger.for_family("layout").status is FactStatus.PRESENT

    def test_an_absent_families_key_is_empty(self) -> None:
        assert AvailabilityLedger.from_dict({}).families == {}
