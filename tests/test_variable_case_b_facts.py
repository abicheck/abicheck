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

"""ADR-063 Phase 5's fourth batch: ``Variable.source_header``/
``alignment_bits``/``elf_binding`` -> ``Fact[...]`` (schema v33). Mirrors
``RecordType``'s/``EnumType``'s own identical case-(b) fields exactly (same
"None already unambiguously means not captured" bridge) -- see
``test_recordtype_case_b_facts.py`` for the same parametrized pattern.

``elf_binding`` differs from the other two in one respect: its value type
is the ``SymbolBinding`` enum, not a plain JSON-safe scalar, so its round
trip also proves the decoded ``Fact[...].value`` comes back a real
``SymbolBinding`` member, not a bare string (``storage/fact_codec.py``'s
``decode_variable_facts`` does this reconstruction explicitly).
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import AbiSnapshot, Fact, FactStatus, SymbolBinding, Variable
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict

_CASE_B_FIELDS: tuple[tuple[str, object], ...] = (
    ("source_header", "widget.h"),
    ("alignment_bits", 64),
    ("elf_binding", SymbolBinding.GLOBAL),
)


def _make_snap(**kwargs: object) -> AbiSnapshot:
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


def _var_dict_value(field_name: str, value: object) -> object:
    # elf_binding is serialized as its plain string value (SymbolBinding is
    # a str subclass), matching how the legacy (non-Fact) field round-trips.
    return value.value if isinstance(value, SymbolBinding) else value


class TestVariableCaseBFactRoundTrip:
    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_fresh_snapshot_round_trips_explicit_value(
        self, field_name: str, value: object
    ) -> None:
        var = Variable(
            name="g_widget", mangled="g_widget", type="int", **{field_name: value}
        )
        r = _round_trip(_make_snap(variables=[var])).variables[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_omitted_field_round_trips_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        var = Variable(name="g_widget", mangled="g_widget", type="int")
        r = _round_trip(_make_snap(variables=[var])).variables[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_explicit_unsupported_fact_survives_round_trip(
        self, field_name: str, _value: object
    ) -> None:
        var = Variable(
            name="g_gapped",
            mangled="g_gapped",
            type="int",
            **{f"{field_name}_fact": Fact.unsupported("DWARF-only")},
        )
        r = _round_trip(_make_snap(variables=[var])).variables[0]
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.UNSUPPORTED
        assert fact.diagnostics == ("DWARF-only",)
        assert getattr(r, field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_legacy_pre_v33_snapshot_with_real_value_backfills_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=32,
            variables=[
                {
                    "name": "g_widget",
                    "mangled": "g_widget",
                    "type": "int",
                    field_name: _var_dict_value(field_name, value),
                }
            ],
        )
        r = snapshot_from_dict(d).variables[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_legacy_pre_v33_snapshot_with_none_value_backfills_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=32,
            variables=[{"name": "g_widget", "mangled": "g_widget", "type": "int"}],
        )
        r = snapshot_from_dict(d).variables[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            variables=[
                {
                    "name": "g_widget",
                    "mangled": "g_widget",
                    "type": "int",
                    field_name: _var_dict_value(field_name, value),
                }
            ],
        )
        snap = snapshot_from_dict(d)
        fact = getattr(snap.variables[0], f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED
        assert getattr(snap.variables[0], field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_snapshot_to_dict_encodes_status_as_plain_string(
        self, field_name: str, value: object
    ) -> None:
        var = Variable(
            name="g_widget",
            mangled="g_widget",
            type="int",
            **{f"{field_name}_fact": Fact.present(value)},
        )
        d = snapshot_to_dict(_make_snap(variables=[var]))
        assert d["variables"][0][f"{field_name}_fact"]["status"] == "present"

    def test_elf_binding_fact_value_is_a_real_symbolbinding_member_not_a_bare_string(
        self,
    ) -> None:
        var = Variable(
            name="g_widget",
            mangled="g_widget",
            type="int",
            elf_binding=SymbolBinding.WEAK,
        )
        r = _round_trip(_make_snap(variables=[var])).variables[0]
        assert r.elf_binding_fact.value is SymbolBinding.WEAK
        assert r.elf_binding is SymbolBinding.WEAK

    def test_schema_version_is_33_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 33
