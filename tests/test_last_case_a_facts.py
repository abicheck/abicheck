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

"""``Param.is_restrict`` and ``Variable.access`` — ADR-063 Phase 5's last two
case-(a) conversions (schema v40), the entries that empty
``KNOWN_UNCONVERTED_ELIGIBLE_FACTS``.

``Variable.access`` is the one registered fact whose value type is an enum,
so it needs the same non-JSON-native reconstruction ``elf_binding_fact``
does — a decoded bare ``"private"`` string reaching the legacy ``access``
field would break every reader that treats it as an ``AccessLevel``. That
reconstruction, and the "the allowlist is empty and must stay empty"
invariant, are what this file pins beyond the shared round-trip contract.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Fact,
    FactStatus,
    Function,
    Param,
    Variable,
)
from abicheck.model.fact_registry import FACT_REGISTRY, KNOWN_UNCONVERTED_ELIGIBLE_FACTS
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.fact_codec import _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS

_LEGACY = _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS - 1


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = {
        "library": "libfoo.so",
        "version": "v1",
        "functions": [],
        "variables": [],
        "types": [],
        "enums": [],
        "typedefs": [],
    }
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


def _minimal_dict(**overrides: object) -> dict:
    base: dict = {
        "library": "libtest.so",
        "version": "v1",
        "functions": [],
        "variables": [],
        "types": [],
        "enums": [],
        "typedefs": [],
    }
    base.update(overrides)
    return base


def _fn(params: list[Param]) -> Function:
    return Function(name="f", mangled="_Z1fPi", return_type="void", params=params)


class TestParamIsRestrictFact:
    @pytest.mark.parametrize("value", [True, False])
    def test_explicit_value_round_trips_present(self, value: bool) -> None:
        snap = _snap(functions=[_fn([Param(name="p", type="int*", is_restrict=value)])])
        p = _round_trip(snap).functions[0].params[0]
        assert p.is_restrict is value
        assert p.is_restrict_fact.status is FactStatus.PRESENT

    def test_omitted_is_not_collected(self) -> None:
        p = _round_trip(_snap(functions=[_fn([Param(name="p", type="int*")])]))
        param = p.functions[0].params[0]
        assert param.is_restrict is False
        assert param.is_restrict_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_unreliable_snapshot_downgrades_a_blanket_false(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_restrict_facts_reliable=False,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fPi",
                    "return_type": "void",
                    "params": [{"name": "p", "type": "int*", "is_restrict": False}],
                }
            ],
        )
        param = snapshot_from_dict(d).functions[0].params[0]
        assert param.is_restrict is False
        assert param.is_restrict_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_reliable_snapshot_keeps_a_real_true(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            clang_restrict_facts_reliable=True,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fPi",
                    "return_type": "void",
                    "params": [{"name": "p", "type": "int*", "is_restrict": True}],
                }
            ],
        )
        param = snapshot_from_dict(d).functions[0].params[0]
        assert param.is_restrict is True
        assert param.is_restrict_fact.status is FactStatus.PRESENT

    def test_is_va_list_still_round_trips_after_sharing_the_owner_tuple(self) -> None:
        # This batch moved Param's encode/decode wiring from one hardcoded
        # line to a per-owner tuple; Phase 0's own sibling must be unharmed.
        snap = _snap(
            functions=[_fn([Param(name="p", type="va_list", is_va_list=True)])]
        )
        param = _round_trip(snap).functions[0].params[0]
        assert param.is_va_list is True
        assert param.is_va_list_fact.status is FactStatus.PRESENT


class TestVariableAccessFact:
    @pytest.mark.parametrize("level", list(AccessLevel))
    def test_explicit_level_round_trips_as_a_real_enum_member(
        self, level: AccessLevel
    ) -> None:
        v = Variable(name="g", mangled="g", type="int", access=level)
        got = _round_trip(_snap(variables=[v])).variables[0]
        assert got.access is level
        assert got.access_fact.status is FactStatus.PRESENT
        # The elf_binding_fact-shaped trap: a bare decoded string would
        # compare equal to the member (AccessLevel is a str-Enum) while
        # failing every `.value`/identity-based reader.
        assert isinstance(got.access_fact.value, AccessLevel)
        assert got.access_fact.value.value == level.value

    def test_omitted_is_not_collected_at_the_public_default(self) -> None:
        got = _round_trip(
            _snap(variables=[Variable(name="g", mangled="g", type="int")])
        )
        assert got.variables[0].access is AccessLevel.PUBLIC
        assert got.variables[0].access_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_public_is_a_confirmed_fact(self) -> None:
        v = Variable(name="g", mangled="g", type="int", access=AccessLevel.PUBLIC)
        got = _round_trip(_snap(variables=[v])).variables[0]
        assert got.access_fact.status is FactStatus.PRESENT

    def test_legacy_unreliable_snapshot_downgrades_a_blanket_public(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            castxml_var_access_facts_reliable=False,
            variables=[
                {"name": "g", "mangled": "g", "type": "int", "access": "public"}
            ],
        )
        got = snapshot_from_dict(d).variables[0]
        assert got.access is AccessLevel.PUBLIC
        assert got.access_fact.status is FactStatus.NOT_COLLECTED

    def test_legacy_reliable_snapshot_keeps_a_real_private(self) -> None:
        d = _minimal_dict(
            schema_version=_LEGACY,
            castxml_var_access_facts_reliable=True,
            variables=[
                {"name": "g", "mangled": "g", "type": "int", "access": "private"}
            ],
        )
        got = snapshot_from_dict(d).variables[0]
        assert got.access is AccessLevel.PRIVATE
        assert got.access_fact.status is FactStatus.PRESENT
        assert isinstance(got.access_fact.value, AccessLevel)

    def test_explicit_unsupported_survives_round_trip(self) -> None:
        v = Variable(
            name="g", mangled="g", type="int", access_fact=Fact.unsupported("DWARF")
        )
        got = _round_trip(_snap(variables=[v])).variables[0]
        assert got.access_fact.status is FactStatus.UNSUPPORTED
        assert got.access is AccessLevel.PUBLIC


class TestPhase5ConversionIsComplete:
    """ADR-063 Phase 5's own closing condition, as an executable check."""

    def test_the_unconverted_allowlist_is_empty(self) -> None:
        assert KNOWN_UNCONVERTED_ELIGIBLE_FACTS == frozenset()

    def test_every_reference_flag_covered_field_is_registered(self) -> None:
        from abicheck.model.fact_registry import REFERENCE_FLAG_COVERAGE

        registered = {(e.owner, e.field) for e in FACT_REGISTRY.entries.values()}
        for flag, pairs in REFERENCE_FLAG_COVERAGE.items():
            for pair in pairs:
                assert pair in registered, f"{flag} gates unregistered {pair}"

    def test_schema_version_is_40_or_higher(self) -> None:
        assert SCHEMA_VERSION >= _MIN_SCHEMA_VERSION_FOR_LAST_CASE_A_FACTS == 40
