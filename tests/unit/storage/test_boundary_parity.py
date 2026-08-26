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

from collections.abc import Callable
from typing import Any

import pytest

from abicheck.storage.availability import (
    AvailabilityLedger,
    FactAvailability,
    FactStatus,
)
from abicheck.storage.identity import (
    EntityId,
    EntityKind,
    IdentityConflict,
    ObservationKind,
    OccurrenceId,
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
