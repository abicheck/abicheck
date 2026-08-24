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

"""Several same-bare-name typedef removals stay individually distinguishable.

``Change.symbol`` for a typedef is deliberately bare (a header-mode dumper
spells a *reference* to a typedef bare in a signature, and
``diff_filtering._enrich_affected_symbols`` matches on that) — so the
qualified spelling has to reach the reader some other way. This pins the whole
chain that makes five ``value_type`` removals tellable apart: the description,
the generic ``(kind, description)`` dedup that would otherwise collapse them,
and the canonical finding id downstream consumers key on.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.finding_identity import report_canonical_finding_id
from abicheck.model import AbiSnapshot

_OWNERS = [
    "tbb::detail::d1::concurrent_vector",
    "tbb::detail::d1::concurrent_queue",
    "tbb::detail::d1::concurrent_map",
    "tbb::detail::d2::concurrent_vector",
    "tbb::detail::d2::concurrent_queue",
]


def _old() -> AbiSnapshot:
    return AbiSnapshot(
        library="libtbb.so",
        version="2021",
        typedefs={"value_type": "int"},
        typedefs_qualified={f"{owner}::value_type": "int" for owner in _OWNERS},
    )


def _new() -> AbiSnapshot:
    return AbiSnapshot(
        library="libtbb.so", version="2022", typedefs={}, typedefs_qualified={}
    )


def _removals() -> list[object]:
    return [
        c
        for c in compare(_old(), _new()).changes
        if c.kind is ChangeKind.TYPEDEF_REMOVED
    ]


def test_every_qualified_declaration_is_reported_once() -> None:
    removals = _removals()
    assert len(removals) == len(_OWNERS)


def test_symbol_stays_bare_for_affected_symbol_attribution() -> None:
    assert {c.symbol for c in _removals()} == {"value_type"}  # type: ignore[attr-defined]


def test_descriptions_disambiguate_by_qualified_owner() -> None:
    descriptions = {c.description for c in _removals()}  # type: ignore[attr-defined]
    assert len(descriptions) == len(_OWNERS)
    for owner in _OWNERS:
        assert any(f"{owner}::value_type" in d for d in descriptions)


def test_canonical_finding_ids_are_distinct() -> None:
    ids = {report_canonical_finding_id(c) for c in _removals()}
    assert len(ids) == len(_OWNERS)
