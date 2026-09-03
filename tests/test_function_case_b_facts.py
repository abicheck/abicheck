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

"""ADR-063 Phase 5's fifth batch: ``Function``'s own ten case-(b) fields
(``contract_attributes``, ``is_explicit``, ``is_hidden_friend``,
``source_header``, ``is_variadic``, ``exception_spec``, ``is_override``,
``hidden_friend_owner``, ``elf_binding``, ``is_compiler_generated``)
-> ``Fact[...]`` (schema v34). Mirrors ``Variable``'s own identical case-(b)
fields exactly (same "None already unambiguously means not captured"
bridge) -- see ``test_variable_case_b_facts.py`` for the same parametrized
pattern, including ``elf_binding``'s ``SymbolBinding`` reconstruction.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import AbiSnapshot, Fact, FactStatus, Function, SymbolBinding
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict

_CASE_B_FIELDS: tuple[tuple[str, object], ...] = (
    ("contract_attributes", ["nonnull"]),
    ("is_explicit", True),
    ("is_hidden_friend", True),
    ("source_header", "widget.h"),
    ("is_variadic", True),
    ("exception_spec", "throw()"),
    ("is_override", True),
    ("hidden_friend_owner", "ns::Foo"),
    ("elf_binding", SymbolBinding.GLOBAL),
    ("is_compiler_generated", True),
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


def _func_dict_value(field_name: str, value: object) -> object:
    # elf_binding is serialized as its plain string value (SymbolBinding is
    # a str subclass), matching how the legacy (non-Fact) field round-trips.
    return value.value if isinstance(value, SymbolBinding) else value


class TestFunctionCaseBFactRoundTrip:
    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_fresh_snapshot_round_trips_explicit_value(
        self, field_name: str, value: object
    ) -> None:
        fn = Function(
            name="f", mangled="_Z1fv", return_type="void", **{field_name: value}
        )
        r = _round_trip(_make_snap(functions=[fn])).functions[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_omitted_field_round_trips_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        fn = Function(name="f", mangled="_Z1fv", return_type="void")
        r = _round_trip(_make_snap(functions=[fn])).functions[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_explicit_unsupported_fact_survives_round_trip(
        self, field_name: str, _value: object
    ) -> None:
        fn = Function(
            name="g",
            mangled="_Z1gv",
            return_type="void",
            **{f"{field_name}_fact": Fact.unsupported("DWARF-only")},
        )
        r = _round_trip(_make_snap(functions=[fn])).functions[0]
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.UNSUPPORTED
        assert fact.diagnostics == ("DWARF-only",)
        assert getattr(r, field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_legacy_pre_v34_snapshot_with_real_value_backfills_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=33,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fv",
                    "return_type": "void",
                    field_name: _func_dict_value(field_name, value),
                }
            ],
        )
        r = snapshot_from_dict(d).functions[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_legacy_pre_v34_snapshot_with_none_value_backfills_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=33,
            functions=[{"name": "f", "mangled": "_Z1fv", "return_type": "void"}],
        )
        r = snapshot_from_dict(d).functions[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            functions=[
                {
                    "name": "f",
                    "mangled": "_Z1fv",
                    "return_type": "void",
                    field_name: _func_dict_value(field_name, value),
                }
            ],
        )
        snap = snapshot_from_dict(d)
        fact = getattr(snap.functions[0], f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED
        assert getattr(snap.functions[0], field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_snapshot_to_dict_encodes_status_as_plain_string(
        self, field_name: str, value: object
    ) -> None:
        fn = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            **{f"{field_name}_fact": Fact.present(value)},
        )
        d = snapshot_to_dict(_make_snap(functions=[fn]))
        assert d["functions"][0][f"{field_name}_fact"]["status"] == "present"

    def test_elf_binding_fact_value_is_a_real_symbolbinding_member_not_a_bare_string(
        self,
    ) -> None:
        fn = Function(
            name="f",
            mangled="_Z1fv",
            return_type="void",
            elf_binding=SymbolBinding.WEAK,
        )
        r = _round_trip(_make_snap(functions=[fn])).functions[0]
        assert r.elf_binding_fact.value is SymbolBinding.WEAK
        assert r.elf_binding is SymbolBinding.WEAK

    def test_schema_version_is_34_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 34
