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
from types import MappingProxyType
from typing import Any

import pytest

from abicheck.storage.availability import (
    AvailabilityLedger,
    Confidence,
    FactAvailability,
    FactStatus,
)
from abicheck.storage.canonical import canonical_json, semantic_digest
from abicheck.storage.identity import (
    EntityId,
    EntityKind,
    IdentityConflict,
    ObservationKind,
    OccurrenceId,
    OccurrenceSet,
    elf_symbol_occurrence,
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
    loaded = AvailabilityLedger.from_dict(
        {"families": {}, "overrides": [], "unknown_family_default": None}
    )
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


class TestOnlyAStringSectionKeySurvives:
    """The fourth answer this field has had, and the one that ends the class.

    Three rules for stringifying a non-`str` key each survived one review
    round and then produced the next finding: `str(k)` collided with the
    survivor decided by traversal order; sorting fixed the collapse but not
    the conversion, so a `frozenset` key still varied with `PYTHONHASHSEED`;
    an allowlist of stable scalars fixed *that* and left `{1: 2}`,
    `{1.0: 2}` and `{True: 2}` — the same mapping in Python — with three
    section names and three digests.

    There is no stringification that is both injective and
    order-independent, because Python's key *equality* does not distinguish
    the spellings its `str()` does. Keeping only what JSON can carry removes
    the question rather than answering it again.
    """

    @pytest.mark.parametrize(
        "key",
        [1, 1.0, True, 0.0, -0.0, None, frozenset({"a", "b"}), b"k", (1, 2), object()],
    )
    def test_a_non_string_key_is_dropped(self, key: Any) -> None:
        emitted = StorageVersions(section_schema_versions={key: 1}).to_dict()

        assert "section_schema_versions" not in emitted

    @pytest.mark.parametrize("mapping", [{1: 2}, {1.0: 2}, {True: 2}])
    def test_equal_mappings_cannot_disagree(self, mapping: Any) -> None:
        """`{1: 2}`, `{1.0: 2}` and `{True: 2}` are one mapping in Python.

        Whatever the rule is, three spellings of one value must not produce
        three addresses. They produce one now because none of them is kept —
        which is a blunter answer than making them agree, and the only one
        that does not depend on a stringification rule holding.
        """
        assert (
            len(
                {
                    semantic_digest(
                        StorageVersions(section_schema_versions=m).to_dict()
                    )
                    for m in ({1: 2}, {1.0: 2}, {True: 2})
                }
            )
            == 1
        )
        assert (
            "section_schema_versions"
            not in StorageVersions(section_schema_versions=mapping).to_dict()
        )

    def test_a_string_key_is_kept(self) -> None:
        """Dropping the rest must not disturb the only shape JSON produces."""
        versions = StorageVersions(section_schema_versions={"entities": 3, "attrs": 1})

        assert versions.to_dict()["section_schema_versions"] == {
            "attrs": 1,
            "entities": 3,
        }
        assert StorageVersions.from_dict(versions.to_dict()) == versions

    def test_a_mixed_mapping_keeps_only_the_string_keys(self) -> None:
        versions = StorageVersions(
            section_schema_versions={"entities": 3, 7: 9, frozenset({"a"}): 4}
        )

        assert versions.to_dict()["section_schema_versions"] == {"entities": 3}

    @pytest.mark.slow
    def test_the_digest_is_the_same_in_every_process(self) -> None:
        """What the rule is for, measured across real interpreters.

        A `frozenset` key rendered its members in hash order, so one logical
        block addressed differently per process. It is dropped now, but the
        property worth pinning is the digest, not the mechanism.
        """
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


class TestASurrogatePairCannotShareAnAddress:
    """The ASCII payload's one blind spot.

    JSON escapes a non-BMP scalar *as* a surrogate pair, so the scalar and
    the two-code-unit string that spells it render identically — two unequal
    strings, one address, in a store that addresses by content. Worse than a
    plain collision: `canonical_json` *does* distinguish them, so the stored
    document and its address disagree about whether they differ.

    Refused rather than escaped. A lone surrogate is a real POSIX path this
    module supports; a pair is a scalar spelled the UTF-16 way, and inventing
    an escape for it would address content by a rule no other implementation
    could re-derive — giving up the property the ASCII payload was chosen
    for.
    """

    SCALAR = chr(0x1F600)
    PAIR = chr(0xD83D) + chr(0xDE00)

    def test_the_two_are_not_equal_to_begin_with(self) -> None:
        """The premise: if these were equal, sharing an address would be right."""
        assert self.PAIR != self.SCALAR
        assert len(self.PAIR) == 2
        assert len(self.SCALAR) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"k": PAIR},
            {PAIR: 1},
            [PAIR],
            {"a": [{"b": PAIR}]},
            {"a": {PAIR}},
        ],
    )
    def test_a_pair_is_refused_at_any_depth(self, payload: Any) -> None:
        with pytest.raises(ValueError, match="surrogate pair"):
            semantic_digest(payload)

    def test_the_scalar_itself_is_fine(self) -> None:
        assert semantic_digest({"k": self.SCALAR}).startswith("sha256:")

    def test_a_lone_surrogate_is_still_supported(self) -> None:
        """The documented case this must not break: a real POSIX path."""
        assert semantic_digest({"p": "/src/caf\udce9.h"}).startswith("sha256:")
        assert semantic_digest({"p": self.SCALAR + "\udce9"}).startswith("sha256:")

    def test_canonical_json_still_distinguishes_them(self) -> None:
        """Which is why sharing an address was a disagreement, not a choice."""
        assert canonical_json({"k": self.PAIR}) != canonical_json({"k": self.SCALAR})


class TestAnElfSymbolFlagIsABooleanNotATruthyValue:
    """A coercion two lines from the key it defeats.

    `elf_symbol_occurrence` encoded its two flags with `"1" if x else "0"`, so
    an adapter passing a parsed `"false"` produced `"1"` — the occurrence
    claimed the symbol was defined and default, took the same key as one built
    with `True`, and `OccurrenceSet.add` discarded it as a duplicate (Codex
    review). That is this module's one invariant — never drop an observation —
    broken by a truthiness test, not by the set.
    """

    BASE = {"name": "foo", "artifact_id": "lib.so"}

    @pytest.mark.parametrize(
        "value", ["false", "true", "", 1, 0, None, [], {"k": 1}, 1.0]
    )
    @pytest.mark.parametrize("field", ["defined", "default_version"])
    def test_a_non_boolean_flag_is_refused(self, field: str, value: Any) -> None:
        """`1` and `0` are refused too: a flag is not a parsed int.

        A caller holding an int has a value it has not finished parsing, and
        the point of the guard is to make that visible rather than to guess.
        """
        assert _refused(lambda: elf_symbol_occurrence(**self.BASE, **{field: value}))

    def test_the_string_false_no_longer_reads_as_true(self) -> None:
        """The exact reported shape, stated as the collision it caused."""
        with pytest.raises(TypeError):
            elf_symbol_occurrence(**self.BASE, defined="false", default_version="false")

    def test_real_flags_stay_distinct(self) -> None:
        """Refusing the coercion must not blur the values it was hiding."""
        cleared = elf_symbol_occurrence(
            **self.BASE, defined=False, default_version=False
        )
        set_ = elf_symbol_occurrence(**self.BASE, defined=True, default_version=True)

        assert cleared.key != set_.key
        assert dict(cleared.attributes)["defined"] == "0"
        assert dict(set_.attributes)["defined"] == "1"

    def test_both_are_retained_by_the_set(self) -> None:
        """The invariant the collision broke, asserted directly."""
        occurrences = OccurrenceSet()
        occurrences.add(elf_symbol_occurrence(**self.BASE, defined=False))
        occurrences.add(elf_symbol_occurrence(**self.BASE, defined=True))

        assert len(occurrences) == 2


class TestTheStateIsCanonicalNotJustTheDocument:
    """`AGENTS.md` invariant 4, at the second place this branch has broken it.

    Normalizing on the way out alone left two objects that serialize to one
    document and one digest comparing **unequal**, with different `repr`s —
    so equality and every diagnostic depended on malformed input the format
    deliberately discards (Codex review).

    The invariant already says this in its own words: "a claim about the
    stored *state*, not only about accessors: a canonical view over
    non-canonical state leaves `__eq__` and `repr` exposed". It was written
    down after `OccurrenceSet` kept insertion order in state behind a sorted
    `__iter__`, and then not applied here.
    """

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ({"normalization_recipe": 1}, {"normalization_recipe": None}),
            ({"extractor_generation": 1.5}, {"extractor_generation": "x"}),
            ({"resolver_generation": -1}, {"resolver_generation": None}),
            ({"source_schema_version": "2"}, {"source_schema_version": 0}),
            ({"source_producer_generation": 7}, {"source_producer_generation": ["x"]}),
            ({"section_schema_versions": ["x"]}, {"section_schema_versions": {}}),
            ({"package_format_version": "x"}, {"package_format_version": -1}),
            (
                {"comparison_contract_version": 1.5},
                {"comparison_contract_version": None},
            ),
        ],
    )
    def test_values_that_serialize_alike_compare_alike(
        self, left: Any, right: Any
    ) -> None:
        a, b = StorageVersions(**left), StorageVersions(**right)

        assert a.to_dict() == b.to_dict(), "premise: these serialize identically"
        assert a == b
        assert repr(a) == repr(b)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ({"name": 1}, {"name": None}),
            ({"version": ["x"]}, {"version": {"k": 1}}),
            ({"binary_digest": 0}, {"binary_digest": b"x"}),
        ],
    )
    def test_the_nested_record_canonicalizes_too(self, left: Any, right: Any) -> None:
        """A record that serializes equal and compares unequal is the same bug."""
        a, b = ProducerIdentity(**left), ProducerIdentity(**right)

        assert a.to_dict() == b.to_dict()
        assert a == b

    def test_a_well_formed_object_is_untouched(self) -> None:
        """Canonicalizing the malformed case must not disturb the valid one."""
        versions = StorageVersions(
            normalization_recipe="canonical/1",
            extractor_generation=4,
            section_schema_versions={"entities": 3},
            producer=ProducerIdentity(name="castxml", version="0.7.0"),
        )

        assert versions.normalization_recipe == "canonical/1"
        assert versions.extractor_generation == 4
        assert versions.section_schema_versions == {"entities": 3}
        assert versions.producer.name == "castxml"
        assert StorageVersions.from_dict(versions.to_dict()) == versions

    def test_the_reader_still_refuses_an_unstated_document(self) -> None:
        """Normalizing state must not turn a fail-closed axis into a pass."""
        assert check_reader_compatibility(StorageVersions()).readable
        assert not check_reader_compatibility(StorageVersions.from_dict({})).readable
        assert not check_reader_compatibility(
            StorageVersions(package_format_version="x")
        ).readable


class TestTheSectionMappingIsFrozenAndDropsUnstatedEntries:
    """A frozen record must not expose a mutable field, and `0` means unstated.

    Normalizing in `__post_init__` closed the constructor; the one field whose
    value is a *container* stayed mutable through it, so
    `versions.section_schema_versions["x"] = "bad"` bypassed every normalizer
    and left the object serializing like a normalized twin while comparing
    unequal to it (Codex review). Same defect as the round before, reached
    past the fix for it.

    Separately, a zero count is `_stated_count`'s own "unstated", and the
    entry was still written: `{"layout": "bad"}` reserialized as
    `{"layout": 0}` and took a different digest from a document stating no
    section version at all, though neither states one. The scalar axes
    already omit an unstated value.
    """

    def test_the_field_cannot_be_mutated(self) -> None:
        versions = StorageVersions(section_schema_versions={"entities": 1})

        with pytest.raises(TypeError):
            versions.section_schema_versions["x"] = "bad"  # type: ignore[index]

    def test_the_caller_s_own_mapping_cannot_reach_the_state(self) -> None:
        """Freezing the field is not enough if it wraps the caller's dict."""
        supplied = {"entities": 1}
        versions = StorageVersions(section_schema_versions=supplied)

        supplied["entities"] = 99
        supplied["extra"] = 5

        assert dict(versions.section_schema_versions) == {"entities": 1}

    @pytest.mark.parametrize("value", ["bad", 0, -1, 1.5, None, [], True])
    def test_an_entry_stating_nothing_is_dropped(self, value: Any) -> None:
        emitted = StorageVersions(section_schema_versions={"layout": value}).to_dict()

        assert "section_schema_versions" not in emitted

    @pytest.mark.parametrize("value", ["bad", 0, -1, 1.5, None])
    def test_it_addresses_the_same_as_stating_nothing(self, value: Any) -> None:
        """The consequence, not just the shape of the document."""
        assert semantic_digest(
            StorageVersions(section_schema_versions={"layout": value}).to_dict()
        ) == semantic_digest(StorageVersions().to_dict())

    def test_a_usable_entry_beside_an_unusable_one_survives(self) -> None:
        """Dropping the unstated entry must not drop its neighbour."""
        versions = StorageVersions(
            section_schema_versions={"entities": 3, "layout": "bad"}
        )

        assert versions.to_dict()["section_schema_versions"] == {"entities": 3}

    def test_a_real_mapping_still_round_trips(self) -> None:
        versions = StorageVersions(section_schema_versions={"entities": 3, "attrs": 1})

        assert dict(versions.section_schema_versions) == {"attrs": 1, "entities": 3}
        assert StorageVersions.from_dict(versions.to_dict()) == versions
        assert versions == StorageVersions(
            section_schema_versions={"attrs": 1, "entities": 3}
        )

    def test_the_document_carries_a_plain_dict(self) -> None:
        """`to_dict` is what a serializer consumes; it must not hand out a proxy."""
        emitted = StorageVersions(section_schema_versions={"entities": 3}).to_dict()

        assert type(emitted["section_schema_versions"]) is dict


class TestConfidenceComesFromRecordsThatCarryEvidence:
    """`Confidence` weights a `PRESENT`/`PARTIAL` fact, so only those may vote.

    Taking the worst of both unconditionally let a `NOT_APPLICABLE` family
    with `UNKNOWN` — a ledger saying "there is nothing here to be missing",
    not weak evidence — degrade a real `PRESENT`/`HIGH` entity override to
    `UNKNOWN` (Codex review).
    """

    def test_an_inapplicable_family_does_not_degrade_a_real_fact(self) -> None:
        family = FactAvailability(
            FactStatus.NOT_APPLICABLE, confidence=Confidence.UNKNOWN
        )
        override = FactAvailability(FactStatus.PRESENT, confidence=Confidence.HIGH)

        assert family.narrowed(override).status is FactStatus.PRESENT
        assert family.narrowed(override).confidence is Confidence.HIGH
        assert override.narrowed(family).confidence is Confidence.HIGH

    def test_two_real_facts_still_narrow_to_the_weaker(self) -> None:
        """The case the worst-of rule was written for, unchanged."""
        strong = FactAvailability(FactStatus.PRESENT, confidence=Confidence.HIGH)
        weak = FactAvailability(FactStatus.PARTIAL, confidence=Confidence.REDUCED)

        assert strong.narrowed(weak).confidence is Confidence.REDUCED
        assert weak.narrowed(strong).confidence is Confidence.REDUCED

    def test_a_gap_that_wins_does_not_advertise_high_confidence(self) -> None:
        """The direction this fix must not err in.

        When the surviving status is not usable evidence the old rule stands:
        confidence is inert there, and worst-of is the reading that cannot
        overclaim. A `FAILED` record must never carry `HIGH`.
        """
        present = FactAvailability(FactStatus.PRESENT, confidence=Confidence.HIGH)
        failed = FactAvailability(FactStatus.FAILED, confidence=Confidence.UNKNOWN)

        assert present.narrowed(failed).status is FactStatus.FAILED
        assert present.narrowed(failed).confidence is Confidence.UNKNOWN

    @pytest.mark.parametrize("left", list(FactStatus))
    @pytest.mark.parametrize("right", list(FactStatus))
    def test_the_merge_stays_order_independent(
        self, left: FactStatus, right: FactStatus
    ) -> None:
        """Over every status pair, not the four that were reasoned about."""
        a = FactAvailability(left, confidence=Confidence.HIGH)
        b = FactAvailability(right, confidence=Confidence.UNKNOWN)

        assert a.narrowed(b).confidence is b.narrowed(a).confidence
        assert a.narrowed(b).status is b.narrowed(a).status


class TestTheLedgerOwnsItsMappings:
    """A container admitted at the door must support the advertised operations.

    `AvailabilityLedger(families=MappingProxyType({}))` passed every guard and
    then made the documented mutator fail — `declare` raised `TypeError:
    'mappingproxy' object does not support item assignment` (Codex review).
    `StorageVersions` answers the same question by freezing, because its
    records are immutable; this one is mutable by design, so it owns a copy.
    """

    def test_a_read_only_mapping_still_supports_declare(self) -> None:
        ledger = AvailabilityLedger(families=MappingProxyType({}))

        ledger.declare("layout", FactAvailability(FactStatus.PRESENT))

        assert ledger.for_family("layout").status is FactStatus.PRESENT

    def test_a_read_only_mapping_still_supports_override(self) -> None:
        ledger = AvailabilityLedger(overrides=MappingProxyType({}))

        ledger.override("layout", "E1", FactAvailability(FactStatus.FAILED))

        assert ledger.for_entity("layout", "E1").status is FactStatus.FAILED

    def test_the_caller_s_mapping_cannot_reach_the_state(self) -> None:
        """Owning a copy closes the aliasing the guards could not see."""
        supplied = {"layout": FactAvailability(FactStatus.PRESENT)}
        ledger = AvailabilityLedger(families=supplied)

        supplied["sneak"] = "not a record"

        assert list(ledger.families) == ["layout"]
        assert AvailabilityLedger.from_dict(ledger.to_dict()) == ledger
