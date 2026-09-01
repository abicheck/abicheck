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

"""ADR-063 Phase 5's third batch: ``EnumType.qualified_name``/
``source_header`` -> ``Fact[str | None]`` (schema v32). Mirrors
``RecordType``'s own identical fields exactly (same shape, same "None
already unambiguously means not captured" bridge) -- see
``test_recordtype_case_b_facts.py`` for the same parametrized pattern.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import AbiSnapshot, EnumType, Fact, FactStatus
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict

_CASE_B_FIELDS: tuple[tuple[str, object], ...] = (
    ("qualified_name", "ns::Color"),
    ("source_header", "widget.h"),
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


class TestEnumTypeCaseBFactRoundTrip:
    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_fresh_snapshot_round_trips_explicit_value(
        self, field_name: str, value: object
    ) -> None:
        rec = EnumType(name="Color", **{field_name: value})
        r = _round_trip(_make_snap(enums=[rec])).enums[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_omitted_field_round_trips_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        rec = EnumType(name="Color")
        r = _round_trip(_make_snap(enums=[rec])).enums[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_explicit_unsupported_fact_survives_round_trip(
        self, field_name: str, _value: object
    ) -> None:
        rec = EnumType(
            name="Gapped", **{f"{field_name}_fact": Fact.unsupported("DWARF-only")}
        )
        r = _round_trip(_make_snap(enums=[rec])).enums[0]
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.UNSUPPORTED
        assert fact.diagnostics == ("DWARF-only",)
        assert getattr(r, field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_legacy_pre_v32_snapshot_with_real_value_backfills_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=31,
            enums=[{"name": "Color", field_name: value}],
        )
        r = snapshot_from_dict(d).enums[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_legacy_pre_v32_snapshot_with_none_value_backfills_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        d = _minimal_dict(schema_version=31, enums=[{"name": "Color"}])
        r = snapshot_from_dict(d).enums[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            enums=[{"name": "Color", field_name: value}],
        )
        snap = snapshot_from_dict(d)
        fact = getattr(snap.enums[0], f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED
        assert getattr(snap.enums[0], field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_snapshot_to_dict_encodes_status_as_plain_string(
        self, field_name: str, value: object
    ) -> None:
        rec = EnumType(name="Color", **{f"{field_name}_fact": Fact.present(value)})
        d = snapshot_to_dict(_make_snap(enums=[rec]))
        assert d["enums"][0][f"{field_name}_fact"]["status"] == "present"

    def test_schema_version_is_32_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 32
