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

"""RecordType's second Phase 5 batch of ``Fact[T]`` case-(b) conversions
(schema v31, ADR-063 D7 — see
docs/contribute/plans/one-semantic-pipeline.md's Phase 5 section):
``is_abstract``, ``data_size_bits``, ``is_standard_layout``,
``is_trivially_copyable``, ``qualified_name``, ``source_header``.

Every one of these six fields shares the identical "None already
unambiguously means not captured" shape ``RecordType.is_final`` (Phase 5's
first conversion, ``test_recordtype_is_final_fact.py``) already established
— no reliability flag, a direct None-vs-real-value bridge — so this file
parametrizes the same round-trip contract across all six rather than
hand-duplicating six near-identical test classes.
"""

from __future__ import annotations

import json

import pytest

from abicheck.model import AbiSnapshot, Fact, FactStatus, RecordType
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict

#: (field name, a real, non-None sample value of that field's own type).
_CASE_B_FIELDS: tuple[tuple[str, object], ...] = (
    ("is_abstract", True),
    ("data_size_bits", 128),
    ("is_standard_layout", False),
    ("is_trivially_copyable", True),
    ("qualified_name", "ns::Widget"),
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


class TestRecordTypeCaseBFactRoundTrip:
    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_fresh_snapshot_round_trips_explicit_value(
        self, field_name: str, value: object
    ) -> None:
        rec = RecordType(name="Widget", kind="class", **{field_name: value})
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_omitted_field_round_trips_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        rec = RecordType(name="Widget", kind="class")
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_explicit_unsupported_fact_survives_round_trip(
        self, field_name: str, _value: object
    ) -> None:
        rec = RecordType(
            name="Gapped",
            kind="struct",
            **{f"{field_name}_fact": Fact.unsupported("DWARF-only")},
        )
        r = _round_trip(_make_snap(types=[rec])).types[0]
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.UNSUPPORTED
        assert fact.diagnostics == ("DWARF-only",)
        assert getattr(r, field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_legacy_pre_v31_snapshot_with_real_value_backfills_present(
        self, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=30,
            types=[{"name": "Foo", "kind": "class", field_name: value}],
        )
        r = snapshot_from_dict(d).types[0]
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("field_name,_value", _CASE_B_FIELDS)
    def test_legacy_pre_v31_snapshot_with_none_value_backfills_not_collected(
        self, field_name: str, _value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=30,
            types=[{"name": "Foo", "kind": "class"}],
        )
        r = snapshot_from_dict(d).types[0]
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self, field_name: str, value: object
    ) -> None:
        # A v31+ document already commits to serializing each of these six
        # *_fact siblings whenever RecordType emits one — a missing key on a
        # document at or above that threshold means malformed/hand-authored,
        # not legacy (mirrors is_final_fact's own identical test).
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            types=[{"name": "Foo", "kind": "class", field_name: value}],
        )
        snap = snapshot_from_dict(d)
        fact = getattr(snap.types[0], f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED
        assert getattr(snap.types[0], field_name) is None

    @pytest.mark.parametrize("field_name,value", _CASE_B_FIELDS)
    def test_snapshot_to_dict_encodes_status_as_plain_string(
        self, field_name: str, value: object
    ) -> None:
        rec = RecordType(
            name="Foo", kind="class", **{f"{field_name}_fact": Fact.present(value)}
        )
        d = snapshot_to_dict(_make_snap(types=[rec]))
        assert d["types"][0][f"{field_name}_fact"]["status"] == "present"

    def test_schema_version_is_31_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 31
