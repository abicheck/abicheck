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

"""ADR-063 Phase 5's sixth batch: ``AbiSnapshot.ast_resolved_standard`` ->
``Fact[str | None]`` (schema v35) -- the last case-(b) field outside the
four declaration dataclasses (``RecordType``, ``EnumType``, ``Variable``,
``Function``). Mirrors their identical case-(b) fields exactly (same "None
already unambiguously means not captured" bridge) -- see
``test_variable_case_b_facts.py`` for the same parametrized pattern,
narrowed to one field since ``AbiSnapshot`` is a single top-level object,
not a list.
"""

from __future__ import annotations

import json

from abicheck.model import AbiSnapshot, Fact, FactStatus
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict


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


class TestAbiSnapshotCaseBFactRoundTrip:
    def test_fresh_snapshot_round_trips_explicit_value(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so", version="v1", ast_resolved_standard="gnu++20"
        )
        r = _round_trip(snap)
        assert r.ast_resolved_standard == "gnu++20"
        assert r.ast_resolved_standard_fact.status is FactStatus.PRESENT
        assert r.ast_resolved_standard_fact.value == "gnu++20"

    def test_omitted_field_round_trips_not_collected(self) -> None:
        snap = AbiSnapshot(library="libfoo.so", version="v1")
        r = _round_trip(snap)
        assert r.ast_resolved_standard is None
        assert r.ast_resolved_standard_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_unsupported_fact_survives_round_trip(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="v1",
            ast_resolved_standard_fact=Fact.unsupported("DWARF-only"),
        )
        r = _round_trip(snap)
        assert r.ast_resolved_standard_fact.status is FactStatus.UNSUPPORTED
        assert r.ast_resolved_standard_fact.diagnostics == ("DWARF-only",)
        assert r.ast_resolved_standard is None

    def test_legacy_pre_v35_snapshot_with_real_value_backfills_present(self) -> None:
        d = _minimal_dict(schema_version=34, ast_resolved_standard="gnu++17")
        r = snapshot_from_dict(d)
        assert r.ast_resolved_standard == "gnu++17"
        assert r.ast_resolved_standard_fact.status is FactStatus.PRESENT
        assert r.ast_resolved_standard_fact.value == "gnu++17"

    def test_legacy_pre_v35_snapshot_with_none_value_backfills_not_collected(
        self,
    ) -> None:
        d = _minimal_dict(schema_version=34)
        r = snapshot_from_dict(d)
        assert r.ast_resolved_standard is None
        assert r.ast_resolved_standard_fact.status is FactStatus.NOT_COLLECTED

    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self,
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION, ast_resolved_standard="gnu++17"
        )
        snap = snapshot_from_dict(d)
        assert snap.ast_resolved_standard_fact.status is FactStatus.NOT_COLLECTED
        assert snap.ast_resolved_standard is None

    def test_snapshot_to_dict_encodes_status_as_plain_string(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="v1",
            ast_resolved_standard_fact=Fact.present("gnu++20"),
        )
        d = snapshot_to_dict(snap)
        assert d["ast_resolved_standard_fact"]["status"] == "present"

    def test_schema_version_is_35_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 35
