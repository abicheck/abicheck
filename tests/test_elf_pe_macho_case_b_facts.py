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

"""ADR-063 Phase 5's seventh batch: the three binary-format dataclasses' own
case-(b) fields -> ``Fact[...]`` (schema v36): ``ElfMetadata.dynamic_flags``/
``has_init``/``has_fini``, ``PeMetadata.delay_imports``,
``MachoMetadata.rpaths``. Schema-version-driven rather than backend-driven
(each of the three sub-blocks is parsed by exactly one backend), but
otherwise the identical "None already unambiguously means not captured"
case-(b) bridge every prior batch this phase established -- see
``test_variable_case_b_facts.py`` for the sibling parametrized pattern this
file mirrors.

``dynamic_flags`` differs from the other four in one respect: its value
type is ``frozenset[str] | None``, not a plain JSON-safe scalar/dict/list,
so its round trip also proves the decoded ``Fact[...].value`` comes back a
real ``frozenset``, not a bare list (``snapshot_platform_blocks.
elf_from_dict`` does this reconstruction explicitly, mirroring
``decode_variable_facts``'s ``SymbolBinding`` reconstruction).
"""

from __future__ import annotations

import json

import pytest

from abicheck.elf_metadata import ElfMetadata
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot, Fact, FactStatus
from abicheck.pe_metadata import PeMetadata
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict

_CASE_B_FIELDS: tuple[tuple[str, str, object], ...] = (
    ("elf", "dynamic_flags", frozenset({"NOW", "ORIGIN"})),
    ("elf", "has_init", True),
    ("elf", "has_fini", False),
    ("pe", "delay_imports", {"foo.dll": ["Bar"]}),
    ("macho", "rpaths", ["@loader_path/../lib"]),
)

_OWNER_CLASS = {"elf": ElfMetadata, "pe": PeMetadata, "macho": MachoMetadata}


def _make_snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = {"library": "libfoo.so", "version": "v1"}
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


def _minimal_dict(**overrides: object) -> dict:
    base: dict = {"library": "libtest.so", "version": "v1"}
    base.update(overrides)
    return base


def _block_dict_value(value: object) -> object:
    # dynamic_flags is serialized as a plain (sorted) list -- JSON has no
    # frozenset -- matching how the legacy (non-Fact) field round-trips.
    return sorted(value) if isinstance(value, frozenset) else value


class TestElfPeMachoCaseBFactRoundTrip:
    @pytest.mark.parametrize("owner_attr,field_name,value", _CASE_B_FIELDS)
    def test_fresh_snapshot_round_trips_explicit_value(
        self, owner_attr: str, field_name: str, value: object
    ) -> None:
        block = _OWNER_CLASS[owner_attr](**{field_name: value})
        r = getattr(_round_trip(_make_snap(**{owner_attr: block})), owner_attr)
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("owner_attr,field_name,_value", _CASE_B_FIELDS)
    def test_omitted_field_round_trips_not_collected(
        self, owner_attr: str, field_name: str, _value: object
    ) -> None:
        block = _OWNER_CLASS[owner_attr]()
        r = getattr(_round_trip(_make_snap(**{owner_attr: block})), owner_attr)
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("owner_attr,field_name,_value", _CASE_B_FIELDS)
    def test_explicit_unsupported_fact_survives_round_trip(
        self, owner_attr: str, field_name: str, _value: object
    ) -> None:
        block = _OWNER_CLASS[owner_attr](
            **{f"{field_name}_fact": Fact.unsupported("not captured on this host")}
        )
        r = getattr(_round_trip(_make_snap(**{owner_attr: block})), owner_attr)
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.UNSUPPORTED
        assert fact.diagnostics == ("not captured on this host",)
        assert getattr(r, field_name) is None

    @pytest.mark.parametrize("owner_attr,field_name,value", _CASE_B_FIELDS)
    def test_legacy_pre_v36_snapshot_with_real_value_backfills_present(
        self, owner_attr: str, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=35,
            **{owner_attr: {field_name: _block_dict_value(value)}},
        )
        r = getattr(snapshot_from_dict(d), owner_attr)
        assert getattr(r, field_name) == value
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.PRESENT
        assert fact.value == value

    @pytest.mark.parametrize("owner_attr,field_name,_value", _CASE_B_FIELDS)
    def test_legacy_pre_v36_snapshot_with_none_value_backfills_not_collected(
        self, owner_attr: str, field_name: str, _value: object
    ) -> None:
        d = _minimal_dict(schema_version=35, **{owner_attr: {}})
        r = getattr(snapshot_from_dict(d), owner_attr)
        assert getattr(r, field_name) is None
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED

    @pytest.mark.parametrize("owner_attr,field_name,value", _CASE_B_FIELDS)
    def test_current_schema_missing_fact_key_is_not_collected_not_present(
        self, owner_attr: str, field_name: str, value: object
    ) -> None:
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            **{owner_attr: {field_name: _block_dict_value(value)}},
        )
        snap = snapshot_from_dict(d)
        r = getattr(snap, owner_attr)
        fact = getattr(r, f"{field_name}_fact")
        assert fact.status is FactStatus.NOT_COLLECTED
        assert getattr(r, field_name) is None

    @pytest.mark.parametrize("owner_attr,field_name,value", _CASE_B_FIELDS)
    def test_snapshot_to_dict_encodes_status_as_plain_string(
        self, owner_attr: str, field_name: str, value: object
    ) -> None:
        block = _OWNER_CLASS[owner_attr](**{f"{field_name}_fact": Fact.present(value)})
        d = snapshot_to_dict(_make_snap(**{owner_attr: block}))
        assert d[owner_attr][f"{field_name}_fact"]["status"] == "present"

    def test_dynamic_flags_fact_value_is_a_real_frozenset_not_a_bare_list(self) -> None:
        elf = ElfMetadata(dynamic_flags=frozenset({"ORIGIN", "BIND_NOW"}))
        r = _round_trip(_make_snap(elf=elf)).elf
        assert isinstance(r.dynamic_flags_fact.value, frozenset)
        assert r.dynamic_flags_fact.value == frozenset({"ORIGIN", "BIND_NOW"})
        assert isinstance(r.dynamic_flags, frozenset)

    def test_schema_version_is_36_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 36
