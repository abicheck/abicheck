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

"""Behaviour pins for the two set/constant rewrites in this branch.

* ``graph_facts._compute_occurrences`` de-duplicates per-call-site
  occurrence ids through a set instead of a list scan. No producer emits the
  occurrence attrs yet, so nothing else reaches this path; the oracle here
  is the previous list-based algorithm, restated, over generated fact sets
  with deliberate duplicates.
* ``surface_facts``' legacy fallbacks return shared frozen ``Fact``
  constants. The answers must be unchanged for every ``Visibility`` and
  header-provenance combination, and sharing must be safe -- the returned
  values are immutable.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model import Function, Visibility
from abicheck.model.fact import FactStatus
from abicheck.model.graph_facts import (
    GraphEdge,
    GraphFact,
    _compute_occurrences,
    edge_occurrence_id,
)
from abicheck.model.surface_facts import (
    binary_exported,
    declared_in_headers,
    in_public_contract,
)

_attr_values = st.sampled_from(["a.h:1", "a.h:2", "cfg-x", "inst-1", None])
_attrs = st.dictionaries(
    st.sampled_from(
        [
            "source_location",
            "configuration_id",
            "instantiation_id",
            "callsite_id",
            "unrelated",
        ]
    ),
    _attr_values,
    max_size=4,
)


def _oracle(edge: GraphEdge) -> list[str]:
    rk = edge.relation_key()
    seen: list[str] = []
    for fact in edge.facts:
        oid = edge_occurrence_id(rk, fact.attrs)
        if oid is not None and oid not in seen:
            seen.append(oid)
    return sorted(seen)


@settings(max_examples=300, deadline=None)
@given(fact_attrs=st.lists(_attrs, max_size=8))
def test_occurrences_match_the_list_based_algorithm(fact_attrs: list[dict]) -> None:
    # Duplicate the list so equal occurrence ids are guaranteed to recur.
    facts = [
        GraphFact(producer=f"p{i}", attrs=a)
        for i, a in enumerate(fact_attrs + fact_attrs)
    ]
    edge = GraphEdge(src="a", dst="b", kind="DECL_CALLS_DECL", facts=facts)
    assert list(_compute_occurrences(edge)) == _oracle(edge)


def test_occurrences_deduplicate_and_sort() -> None:
    same = {"source_location": "x.c:3", "callsite_id": "c1"}
    other = {"source_location": "x.c:9", "callsite_id": "c2"}
    edge = GraphEdge(
        src="a",
        dst="b",
        kind="DECL_CALLS_DECL",
        facts=[
            GraphFact("p1", attrs=same),
            GraphFact("p2", attrs=other),
            GraphFact("p3", attrs=dict(same)),
        ],
    )
    out = list(_compute_occurrences(edge))
    assert len(out) == 2 and out == sorted(out)


def test_no_occurrence_attrs_means_no_occurrences() -> None:
    edge = GraphEdge(
        src="a",
        dst="b",
        kind="DECL_CALLS_DECL",
        facts=[GraphFact("p", attrs={"call_kind": "direct"})],
    )
    assert tuple(_compute_occurrences(edge)) == ()


def _fn(visibility: Visibility, *, header: str | None) -> Function:
    return Function(
        name="f",
        mangled="f",
        return_type="int",
        visibility=visibility,
        source_header=header,
    )


@pytest.mark.parametrize("visibility", list(Visibility))
@pytest.mark.parametrize("header", [None, "/inc/a.h"])
def test_legacy_fallback_answers_by_visibility(
    visibility: Visibility, header: str | None
) -> None:
    f = _fn(visibility, header=header)
    declared, contract, exported = (
        declared_in_headers(f),
        in_public_contract(f),
        binary_exported(f),
    )

    if header:
        assert (declared.status, declared.value) == (FactStatus.PARTIAL, True)
    else:
        assert declared.status is FactStatus.NOT_COLLECTED

    assert contract.status is FactStatus.PARTIAL
    assert contract.value is (visibility is Visibility.PUBLIC)
    assert exported.status is FactStatus.PARTIAL
    assert exported.value is (visibility is not Visibility.HIDDEN)


def test_shared_fallback_constants_are_immutable() -> None:
    a = in_public_contract(_fn(Visibility.PUBLIC, header=None))
    b = binary_exported(_fn(Visibility.PUBLIC, header=None))
    assert a is b, "the PARTIAL(True) fallback is one shared constant"
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.value = False  # type: ignore[misc]


def test_stored_fact_still_wins_over_the_fallback() -> None:
    f = _fn(Visibility.HIDDEN, header=None)
    stored = in_public_contract(_fn(Visibility.PUBLIC, header=None))
    f.in_public_contract_fact = stored
    assert in_public_contract(f) is stored
