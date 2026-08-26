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
