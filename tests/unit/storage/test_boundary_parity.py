# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The two doors must agree: a constructor and `from_dict` accept the same values.

Every text guard in this package was first written for `from_dict`, on the
assumption that malformed input arrives as a parsed document. It also arrives
from an adapter building these objects directly, and review found the
constructor reproducing the document defects **four separate times** across
three rounds — diagnostics split into characters, provenance coerced,
attribute rows unpacked from a scalar, ledger keys collapsing.

Twice I responded by auditing "every guard for a constructor twin" by hand,
and twice the audit was incomplete: `EntityId`'s own identity fields and the
ledger's initial mappings were both missed. A hand audit is the wrong
instrument for an exhaustiveness question, so the parity is checked here
instead — for every field, a bad value must be treated the same way whichever
door it enters through.

A new field with a guard on one side and not the other fails this file rather
than waiting for a reviewer to find it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

from abicheck.storage.availability import (
    AvailabilityLedger,
    FactAvailability,
    FactStatus,
)
from abicheck.storage.canonical import semantic_digest
from abicheck.storage.identity import (
    EntityId,
    EntityKind,
    IdentityConflict,
    ObservationKind,
    OccurrenceId,
    OccurrenceSet,
)
from abicheck.storage.versioning import (
    ProducerIdentity,
    StorageVersions,
    check_reader_compatibility,
)

#: Values no identity-bearing or provenance field may accept. `True` and `1.0`
#: are here deliberately: both coerce to something a real value could equal.
BAD_VALUES: list[Any] = [1, 1.0, True, None, ["x"], {"k": 1}, b"b"]

#: `diagnostics` is a *sequence* field, so a list of strings is legitimate —
#: writing this file caught the first draft asserting otherwise. The parity
#: assertion still applies to every value; only the "must be refused" half is
#: field-dependent, which is the distinction worth keeping visible.
VALID_FOR_DIAGNOSTICS: list[Any] = [["x"]]


def _refused(call: Callable[[], object]) -> bool:
    try:
        call()
    except (TypeError, ValueError):
        return True
    return False


def _entity_field(field: str, value: Any) -> tuple[Callable[[], object], ...]:
    base = {"kind": "type", "qualified_name": "S"}
    if field == "qualified_name":
        ctor = lambda: EntityId(EntityKind.TYPE, value)  # noqa: E731
    else:
        ctor = lambda: EntityId(EntityKind.TYPE, "S", value)  # noqa: E731
    return ctor, lambda: EntityId.from_dict({**base, field: value})


def _occurrence_field(field: str, value: Any) -> tuple[Callable[[], object], ...]:
    entity = EntityId(EntityKind.TYPE, "S")
    document = {
        "entity": {"kind": "type", "qualified_name": "S"},
        "observation": "dwarf",
    }
    return (
        lambda: OccurrenceId(
            entity=entity, observation=ObservationKind.DWARF, **{field: value}
        ),
        lambda: OccurrenceId.from_dict({**document, field: value}),
    )


def _availability_field(field: str, value: Any) -> tuple[Callable[[], object], ...]:
    return (
        lambda: FactAvailability(FactStatus.PRESENT, **{field: value}),
        lambda: FactAvailability.from_dict({"status": "present", field: value}),
    )


@pytest.mark.parametrize("value", BAD_VALUES)
@pytest.mark.parametrize("field", ["qualified_name", "discriminator"])
def test_entity_identity_fields_agree(field: str, value: Any) -> None:
    ctor, parse = _entity_field(field, value)

    assert _refused(ctor) == _refused(parse), (
        f"EntityId.{field}={value!r}: constructor and from_dict disagree"
    )
    assert _refused(ctor), f"EntityId.{field}={value!r} should be refused"


@pytest.mark.parametrize("value", BAD_VALUES)
@pytest.mark.parametrize("field", ["container", "producer"])
def test_occurrence_site_fields_agree(field: str, value: Any) -> None:
    ctor, parse = _occurrence_field(field, value)

    assert _refused(ctor) == _refused(parse)
    assert _refused(ctor)


@pytest.mark.parametrize("value", BAD_VALUES)
@pytest.mark.parametrize(
    "field", ["producer", "producer_version", "recipe", "scope", "diagnostics"]
)
def test_availability_text_fields_agree(field: str, value: Any) -> None:
    ctor, parse = _availability_field(field, value)

    assert _refused(ctor) == _refused(parse), (
        f"FactAvailability.{field}={value!r}: constructor and from_dict disagree"
    )
    if field == "diagnostics" and any(value == ok for ok in VALID_FOR_DIAGNOSTICS):
        assert not _refused(ctor), "a real sequence of strings must still load"
    else:
        assert _refused(ctor), f"{field}={value!r} should be refused"


@pytest.mark.parametrize("value", BAD_VALUES)
def test_ledger_family_keys_agree(value: Any) -> None:
    """Covers the constructor, `from_dict`, and the mutator in one place."""
    record = FactAvailability(FactStatus.PRESENT)

    def _declare() -> object:
        ledger = AvailabilityLedger()
        ledger.declare(value, record)
        return ledger

    assert _refused(lambda: AvailabilityLedger(families={value: record}))
    assert _refused(
        lambda: AvailabilityLedger.from_dict(
            {"families": {value: {"status": "present"}}}
        )
    )
    assert _refused(_declare)


@pytest.mark.parametrize("value", BAD_VALUES)
def test_ledger_override_keys_agree(value: Any) -> None:
    record = FactAvailability(FactStatus.PRESENT)

    def _override(family: Any, entity: Any) -> object:
        ledger = AvailabilityLedger()
        ledger.override(family, entity, record)
        return ledger

    assert _refused(lambda: AvailabilityLedger(overrides={(value, "E"): record}))
    assert _refused(lambda: _override(value, "E"))
    assert _refused(lambda: _override("layout", value))


def test_a_well_formed_value_is_accepted_by_both_doors() -> None:
    """The parity must not be satisfied by refusing everything."""
    record = FactAvailability(
        FactStatus.PRESENT, producer="clang", recipe="r1", diagnostics=["note"]
    )
    assert FactAvailability.from_dict(record.to_dict()) == record

    occurrence = OccurrenceId(
        entity=EntityId(EntityKind.TYPE, "S", "v1"),
        observation=ObservationKind.DWARF,
        container="a.o",
        producer="clang",
        attributes=(("size", "8"),),
    )
    assert OccurrenceId.from_dict(occurrence.to_dict()) == occurrence

    ledger = AvailabilityLedger(families={"layout": record})
    ledger.declare("graph", record)
    ledger.override("layout", "E1", record)
    assert AvailabilityLedger.from_dict(ledger.to_dict()) == ledger


#: Containers that are not mappings but still *iterate* into values every key
#: check accepts — the shape that made `AvailabilityLedger(families=["layout"])`
#: construct successfully and fail later inside `for_family`/`to_dict`.
NOT_MAPPINGS: list[Any] = ["layout", ["layout"], ("layout",), 1, None]


@pytest.mark.parametrize("value", NOT_MAPPINGS)
def test_ledger_containers_agree(value: Any) -> None:
    """Validating the keys a container yields is not validating the container.

    A list, a tuple and a string all yield a valid-looking family name, so
    every key guard passed and only the first real lookup failed — with an
    `AttributeError` about a missing `get`, from a ledger that had already
    been accepted.
    """
    assert _refused(lambda: AvailabilityLedger(families=value))
    assert _refused(lambda: AvailabilityLedger(overrides=value))
    assert _refused(lambda: AvailabilityLedger.from_dict({"families": value}))
    assert _refused(lambda: AvailabilityLedger.from_dict({"overrides": value}))


#: `None` is deliberately absent: at the document door an explicit `null` for
#: a nested object means "not stated", which is exactly what an absent key
#: means, and the value it degrades to (`NOT_COLLECTED`) is the fail-closed
#: one. The constructor has no "unstated" to express, so it refuses. The
#: asymmetry is pinned below rather than smoothed over.
NOT_RECORDS: list[Any] = ["x", ["x"], 1, {"status": "nope"}]


@pytest.mark.parametrize("value", NOT_RECORDS)
def test_ledger_record_slots_agree(value: Any) -> None:
    """A stored record is checked where it is assigned, not where it is read.

    Nothing consults a family's record until a decision needs one, so a value
    that is not a `FactAvailability` survived construction and surfaced from
    inside `comparable`/`narrowed`, on whichever branch happened to reach it.
    """
    assert _refused(lambda: AvailabilityLedger(families={"layout": value}))
    assert _refused(lambda: AvailabilityLedger(overrides={("layout", "E"): value}))
    assert _refused(lambda: AvailabilityLedger(unknown_family_default=value))
    assert _refused(lambda: AvailabilityLedger.from_dict({"families": {"l": value}}))
    assert _refused(
        lambda: AvailabilityLedger.from_dict({"unknown_family_default": value})
    )


def test_a_null_nested_record_reads_as_unstated_not_as_a_record() -> None:
    """The one place the two doors deliberately differ, pinned.

    `unknown_family_default: null` is a document saying "not stated", and it
    degrades to the same `NOT_COLLECTED` an absent key gives — the
    fail-closed value, so nothing is licensed by the tolerance. A constructor
    has no unstated state to express, so `None` there is a mistake and is
    refused.
    """
    loaded = AvailabilityLedger.from_dict({"unknown_family_default": None})
    assert loaded.unknown_family_default.status is FactStatus.NOT_COLLECTED
    assert not loaded.unknown_family_default.comparable
    assert _refused(lambda: AvailabilityLedger(unknown_family_default=None))


@pytest.mark.parametrize("value", BAD_VALUES)
def test_conflict_reason_agrees(value: Any) -> None:
    """The class this file did not cover when it was written.

    `IdentityConflict` was the last text field guarded at one door only — the
    constructor took anything and `from_dict` coerced with `str()`. It is
    covered here now for the reason this file exists: an exhaustiveness
    question is not answered by remembering to check.
    """
    pair = (
        OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            container="a.o",
        ),
        OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            container="b.o",
        ),
    )
    document = {"reason": value, "occurrences": [o.to_dict() for o in pair]}

    assert _refused(lambda: IdentityConflict(reason=value, occurrences=pair))
    assert _refused(lambda: IdentityConflict.from_dict(document))


def test_a_well_formed_conflict_is_accepted_by_both_doors() -> None:
    """The parity must not be satisfied by refusing everything."""
    pair = (
        OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            container="a.o",
        ),
        OccurrenceId(
            entity=EntityId(EntityKind.TYPE, "S"),
            observation=ObservationKind.DWARF,
            container="b.o",
        ),
    )
    conflict = IdentityConflict(reason="size", occurrences=pair)

    assert IdentityConflict.from_dict(conflict.to_dict()) == conflict


#: Values no *vocabulary* field may accept. The string spelling is first on
#: purpose: it is what the document carries, so it is the one a caller is
#: most likely to hand the constructor by mistake — and the one `from_dict`
#: legitimately accepts, which is why the two doors differ here in what they
#: take and must not differ in what they *reject*.
NOT_ENUM_MEMBERS: list[Any] = ["type", "dwarf", 1, None, ["x"], object()]


@pytest.mark.parametrize("value", NOT_ENUM_MEMBERS)
def test_entity_kind_is_a_vocabulary_member(value: Any) -> None:
    """A field whose type is the vocabulary, not a string.

    The text guards were added one door at a time and this field was not one
    of them, so `EntityId(kind="type", ...)` constructed and then failed on
    `.value` from inside `key` and `to_dict` — an object that cannot reach
    its own serialized form (Codex review).
    """
    assert _refused(lambda: EntityId(value, "S"))


@pytest.mark.parametrize("value", NOT_ENUM_MEMBERS)
def test_observation_kind_is_a_vocabulary_member(value: Any) -> None:
    """The sibling the report did not name, fixed with it."""
    assert _refused(
        lambda: OccurrenceId(entity=EntityId(EntityKind.TYPE, "S"), observation=value)
    )


@pytest.mark.parametrize("value", NOT_RECORDS)
def test_the_ledger_mutators_check_the_record_too(value: Any) -> None:
    """A guard added at the constructor must reach the mutators.

    `_availability` was applied in `__post_init__` and not in `declare`/
    `override`, so the same malformed record was refused at one door and
    stored at another — the shape this file exists to catch, reappearing for
    a guard added *by* this file's own round (Codex review). `override` is
    the sibling the report did not name.
    """

    def _declare() -> object:
        ledger = AvailabilityLedger()
        ledger.declare("layout", value)
        return ledger

    def _override() -> object:
        ledger = AvailabilityLedger()
        ledger.override("layout", "E1", value)
        return ledger

    assert _refused(_declare)
    assert _refused(_override)


@pytest.mark.parametrize("value", NOT_RECORDS)
def test_the_nested_entity_is_a_record(value: Any) -> None:
    """A record slot holding another record, not a scalar field.

    `OccurrenceId.__post_init__` validated its own text and vocabulary fields
    and not the entity it wraps, so a parsed mapping or a string survived
    construction and surfaced from `key`/`to_dict`/`OccurrenceSet.add`
    (Codex review).
    """
    assert _refused(
        lambda: OccurrenceId(entity=value, observation=ObservationKind.DWARF)
    )


@pytest.mark.parametrize("value", NOT_MAPPINGS)
def test_every_document_door_refuses_a_non_mapping(value: Any) -> None:
    """Cleanly, not as an `AttributeError` from inside `.get`.

    A caller separating "malformed package" from "broken reader" catches
    `TypeError`/`ValueError`. The availability documents were fixed for this
    a round earlier; the identity ones still called `.get` first (Codex
    review), which is the same fix landing at one site and not its siblings.
    """
    for door in (
        EntityId.from_dict,
        OccurrenceId.from_dict,
        IdentityConflict.from_dict,
        OccurrenceSet.from_dict,
    ):
        assert _refused(lambda door=door: door(value)), f"{door.__qualname__}"


@pytest.mark.parametrize("value", [1, 1.0, True, None, {"k": 1}, b"b", ["x"]])
def test_a_diagnostic_entry_is_not_coerced(value: Any) -> None:
    """The container guard checked the sequence and coerced its members.

    `diagnostics: [1, null]` became `("1", "None")` and was written back as
    apparently valid diagnostic text — manufacturing the extraction error a
    reader audits with, which is the whole reason this field is guarded
    (Codex review).
    """
    assert _refused(lambda: FactAvailability(FactStatus.PRESENT, diagnostics=[value]))
    assert _refused(
        lambda: FactAvailability.from_dict(
            {"status": "present", "diagnostics": [value]}
        )
    )


def test_a_real_diagnostic_list_still_loads() -> None:
    """The parity must not be satisfied by refusing everything."""
    record = FactAvailability(FactStatus.FAILED, diagnostics=["parse error", "x"])

    assert record.diagnostics == ("parse error", "x")
    assert FactAvailability.from_dict(record.to_dict()) == record


#: One entry per public document-bearing class: a factory taking a value, and
#: the class itself. The values are deliberately the *accepted-but-odd* ones —
#: a rejected value never reaches serialization, so it cannot break the
#: property below.
_ROUND_TRIP_CASES: list[tuple[str, Callable[[Any], Any], Any]] = [
    (
        "versions.normalization_recipe",
        lambda v: StorageVersions(normalization_recipe=v),
        None,
    ),
    (
        "versions.extractor_generation",
        lambda v: StorageVersions(extractor_generation=v),
        None,
    ),
    (
        "versions.resolver_generation",
        lambda v: StorageVersions(resolver_generation=v),
        None,
    ),
    (
        "versions.source_schema_version",
        lambda v: StorageVersions(source_schema_version=v),
        None,
    ),
    (
        "versions.source_producer_generation",
        lambda v: StorageVersions(source_producer_generation=v),
        None,
    ),
    (
        "versions.section_schema_versions",
        lambda v: StorageVersions(section_schema_versions={"s": v}),
        None,
    ),
    ("producer.name", lambda v: ProducerIdentity(name=v), None),
    ("producer.version", lambda v: ProducerIdentity(version=v), None),
    ("producer.binary_digest", lambda v: ProducerIdentity(binary_digest=v), None),
]

#: Values a *directly constructed* informational field can legitimately hold
#: while still being odd enough to serialize differently from how it reads.
ODD_INFORMATIONAL_VALUES: list[Any] = [
    1,
    1.5,
    True,
    "1",
    "x",
    ["x"],
    0,
    "",
    None,
    {"a": 1, "b": 2},
]


@pytest.mark.parametrize("value", ODD_INFORMATIONAL_VALUES)
@pytest.mark.parametrize(
    ("label", "build"), [(label, build) for label, build, _ in _ROUND_TRIP_CASES]
)
def test_a_document_is_what_its_own_reader_produces(
    label: str, build: Callable[[Any], Any], value: Any
) -> None:
    """The property three separate findings were instances of.

    Every one was "an informational field writes itself raw while `from_dict`
    normalizes it", found one field at a time — `section_schema_versions`,
    then the scalar axes, then the nested `ProducerIdentity`. Stating it once,
    over every field, is what stops the fourth.

    A field that reads back as absent must also be *written* as absent, so the
    check is on the emitted document rather than on the reloaded object.
    """
    emitted = build(value).to_dict()

    assert type(build(value)).from_dict(emitted).to_dict() == emitted


@pytest.mark.parametrize("value", ODD_INFORMATIONAL_VALUES)
def test_a_decision_reads_the_value_the_format_stores(value: Any) -> None:
    """Serialization agreeing with the reader is not enough on its own.

    `check_reader_compatibility` compared `extractor_generation` raw, so a
    directly constructed `"1"` reported drift against a reader generation of
    `0` while the same object after a round trip reported none — the advice
    depended on whether it had been serialized first (Codex review).
    """
    versions = StorageVersions(extractor_generation=value)
    reloaded = StorageVersions.from_dict(versions.to_dict())

    assert (
        check_reader_compatibility(
            versions, reader_extractor_generation=0
        ).semantics_differ
        == check_reader_compatibility(
            reloaded, reader_extractor_generation=0
        ).semantics_differ
    )


def test_a_real_generation_drift_is_still_reported() -> None:
    """The normalization must not flatten the signal it runs under."""
    versions = StorageVersions(extractor_generation=4)

    assert check_reader_compatibility(
        versions, reader_extractor_generation=3
    ).semantics_differ
    assert not check_reader_compatibility(
        versions, reader_extractor_generation=4
    ).semantics_differ


@pytest.mark.parametrize("value", NOT_RECORDS + [None])
def test_every_record_sequence_refuses_a_non_record(value: Any) -> None:
    """A sequence *of* records, not just a record slot.

    `IdentityConflict.occurrences` leaked `AttributeError` out of its sort,
    and `OccurrenceSet.add`/`extend` out of `occurrence.entity` (Codex
    review; the set was the sibling the report did not name).
    """
    good = OccurrenceId(
        entity=EntityId(EntityKind.TYPE, "S"), observation=ObservationKind.DWARF
    )
    other = OccurrenceId(
        entity=EntityId(EntityKind.TYPE, "S"),
        observation=ObservationKind.DWARF,
        container="b.o",
    )

    assert _refused(lambda: IdentityConflict(reason="r", occurrences=(good, value)))
    assert _refused(lambda: OccurrenceSet().add(value))
    assert _refused(lambda: OccurrenceSet().extend([value]))

    # And the control: a genuine pair still builds.
    assert len(IdentityConflict(reason="r", occurrences=(good, other)).occurrences) == 2


@pytest.mark.parametrize("value", NOT_MAPPINGS)
def test_a_conflicts_occurrence_container_is_a_sequence(value: Any) -> None:
    """A bare string is a `Sequence`, so it needs its own refusal."""
    assert _refused(lambda: IdentityConflict(reason="r", occurrences=value))


class TestAnInformationalTextFieldDegradesRatherThanFabricates:
    """`str()` at a text door invents a value out of the shape of the input.

    Both doors of every informational text field used it, which produced two
    distinct defects from one line (Codex review): `null` became the literal
    producer name `"None"` — persisted, and indistinguishable from a producer
    that really is called that — and a mapping became its *insertion-ordered*
    `repr`, so two spellings of the same document produced different values.

    Degrading rather than rejecting is this module's informational contract.
    Degrading to **empty** rather than to a stringification is what makes the
    degrade honest: "not stated", instead of a value invented from the shape
    of the input.
    """

    @pytest.mark.parametrize("value", [None, 1, 1.5, True, ["x"], {"k": 1}, b"b"])
    @pytest.mark.parametrize(
        ("build", "read"),
        [
            (lambda v: ProducerIdentity.from_dict({"name": v}), lambda o: o.name),
            (lambda v: ProducerIdentity.from_dict({"version": v}), lambda o: o.version),
            (
                lambda v: ProducerIdentity.from_dict({"binary_digest": v}),
                lambda o: o.binary_digest,
            ),
            (
                lambda v: StorageVersions.from_dict({"normalization_recipe": v}),
                lambda o: o.normalization_recipe,
            ),
            (
                lambda v: StorageVersions.from_dict({"source_producer_generation": v}),
                lambda o: o.source_producer_generation,
            ),
        ],
    )
    def test_a_non_string_reads_as_unstated(
        self,
        build: Callable[[Any], Any],
        read: Callable[[Any], str],
        value: Any,
    ) -> None:
        assert read(build(value)) == ""

    def test_a_real_string_is_untouched(self) -> None:
        """Degrading the invalid case must not disturb the valid one."""
        producer = ProducerIdentity.from_dict(
            {"name": "castxml", "version": "0.7.0", "binary_digest": "abc"}
        )

        assert (producer.name, producer.version, producer.binary_digest) == (
            "castxml",
            "0.7.0",
            "abc",
        )

    def test_two_spellings_of_one_mapping_do_not_change_the_digest(self) -> None:
        """The half that matters most for a content-addressed store.

        A mapping stringified through `repr` carries its insertion order, so
        two documents a reader cannot tell apart addressed differently — the
        exact failure the canonical form exists to rule out, arriving through
        a field that looks like plain metadata.
        """
        left = ProducerIdentity.from_dict({"name": {"a": 1, "b": 2}})
        right = ProducerIdentity.from_dict({"name": {"b": 2, "a": 1}})

        assert left == right
        assert semantic_digest(left.to_dict()) == semantic_digest(right.to_dict())

    def test_a_fabricated_identity_is_never_persisted(self) -> None:
        """`null` must not round-trip into a producer literally named "None"."""
        emitted = ProducerIdentity.from_dict({"name": None}).to_dict()

        assert "name" not in emitted


class TestTheSectionVersionMappingIsNormalizedAtBothDoors:
    """The one mapping-valued informational axis, and both doors had a half.

    `to_dict` dereferenced whatever it held, so a directly constructed
    non-mapping raised `AttributeError` on serialization while the reader
    degraded the identical shape to `{}`. And `from_dict` inserted normalized
    keys in *source iteration order*, so a colliding pair kept a different
    survivor depending on how the caller's mapping was traversed — while
    `to_dict` sorted, for exactly that reason. One normalizer now serves both.
    """

    @pytest.mark.parametrize("raw", NOT_MAPPINGS + [{}])
    def test_a_non_mapping_serializes_as_absent(self, raw: Any) -> None:
        """Degraded, not raised: the reader degrades the same shape."""
        assert (
            "section_schema_versions"
            not in StorageVersions(section_schema_versions=raw).to_dict()
        )

    @pytest.mark.parametrize("raw", NOT_MAPPINGS)
    def test_a_non_mapping_reads_as_empty(self, raw: Any) -> None:
        assert (
            StorageVersions.from_dict(
                {"section_schema_versions": raw}
            ).section_schema_versions
            == {}
        )

    def test_a_colliding_key_pair_resolves_the_same_way_in_either_order(self) -> None:
        """The order dependence that reached a content address.

        `{1: 1, "1": 2}` collapses to one key under `str()`. Which value
        survived was decided by traversal order, so two spellings of one
        document reserialized differently and addressed differently.
        """
        left = StorageVersions.from_dict({"section_schema_versions": {1: 1, "1": 2}})
        right = StorageVersions.from_dict({"section_schema_versions": {"1": 2, 1: 1}})

        assert left == right
        assert semantic_digest(left.to_dict()) == semantic_digest(right.to_dict())

    @pytest.mark.parametrize(
        "colliding", [{1: 1, "1": 2}, {"1": 2, 1: 1}, {1: 5, "1": 2}, {"1": 2, 1: 5}]
    )
    def test_the_two_doors_agree_on_the_survivor(self, colliding: Any) -> None:
        """Not just deterministic — the same answer at both doors.

        Both traversal orders *and* both value orderings, because a single
        example passes against the bug: for `{1: 1, "1": 2}` the sorted
        writer and the insertion-order reader coincidentally agreed on `2`,
        so the first version of this test proved nothing. `{1: 5, "1": 2}`
        is the pair where sorting and last-wins disagree.
        """
        assert (
            StorageVersions(section_schema_versions=colliding).to_dict()[
                "section_schema_versions"
            ]
            == StorageVersions.from_dict(
                {"section_schema_versions": colliding}
            ).section_schema_versions
        )

    def test_a_real_mapping_survives_intact(self) -> None:
        """Normalizing the malformed case must not disturb the valid one."""
        versions = StorageVersions(section_schema_versions={"entities": 3, "attrs": 1})

        assert versions.to_dict()["section_schema_versions"] == {
            "attrs": 1,
            "entities": 3,
        }
        assert StorageVersions.from_dict(versions.to_dict()) == versions


class TestASectionKeyHasAProcessStableName:
    """Hashable is not the property a content address needs.

    `str()` on a `frozenset` renders its members in hash order, which for
    strings varies with `PYTHONHASHSEED` — so one logical versions block
    emitted three different section names and three different semantic
    digests across three interpreters (Codex review). Sorting cannot repair
    it, because sorting runs after the conversion.

    Verified across real interpreters in
    `test_a_frozenset_key_is_unstable_across_processes` rather than only
    asserted here, since the whole claim is about behaviour this process
    cannot see.
    """

    @pytest.mark.parametrize("key", [frozenset({"a", "b"}), object(), (1, 2), b"k"])
    def test_an_unstable_key_is_dropped(self, key: Any) -> None:
        """`(1, 2)` and `b"k"` are dropped too, and that is deliberate.

        Their `str()` happens to be stable, so this is stricter than the
        stated rule requires. The allowlist is enumerated rather than
        inferred precisely so that a type nobody has reasoned about is
        excluded by default — a tuple of *frozensets* is not stable, and no
        rule short of a recursive walk separates it from `(1, 2)`. A JSON
        document never has such a key, so nothing real is lost.
        """
        emitted = StorageVersions(section_schema_versions={key: 1}).to_dict()

        assert "section_schema_versions" not in emitted

    @pytest.mark.parametrize("key", ["entities", 7, True, 1.5, None])
    def test_a_stable_key_is_kept(self, key: Any) -> None:
        """Dropping the unstable case must not drop the ordinary ones."""
        emitted = StorageVersions(section_schema_versions={key: 3}).to_dict()

        assert emitted["section_schema_versions"] == {str(key): 3}

    def test_a_mixed_mapping_keeps_only_the_stable_keys(self) -> None:
        versions = StorageVersions(
            section_schema_versions={"entities": 3, frozenset({"a", "b"}): 9}
        )

        assert versions.to_dict()["section_schema_versions"] == {"entities": 3}

    def test_a_frozenset_key_is_unstable_across_processes(self) -> None:
        """The premise, measured — not taken on trust.

        Three real interpreters with different `PYTHONHASHSEED` values. If
        this ever stops holding, the guard above is still harmless, but the
        reason recorded for it would be wrong, and a future reader should
        find that out from a failure rather than from the docstring.
        """
        script = "print(str(frozenset({'alpha', 'beta', 'gamma', 'delta', 'epsilon'})))"
        renderings = {
            subprocess.run(
                [sys.executable, "-c", script],
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for seed in ("1", "2", "3", "4", "5")
        }

        assert len(renderings) > 1, (
            "expected `str(frozenset)` to vary with PYTHONHASHSEED; if it no "
            f"longer does, the stated reason for _STABLE_KEY_TYPES is stale: {renderings}"
        )

    def test_the_digest_is_the_same_in_every_process(self) -> None:
        """What the guard is actually for, measured end to end."""
        script = (
            "from abicheck.storage.versioning import StorageVersions;"
            "from abicheck.storage.canonical import semantic_digest;"
            "print(semantic_digest(StorageVersions(section_schema_versions={"
            "frozenset({'alpha','beta','gamma'}): 1, 'entities': 3}).to_dict()))"
        )
        digests = {
            subprocess.run(
                [sys.executable, "-c", script],
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            for seed in ("1", "2", "3")
        }

        assert len(digests) == 1, digests
