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

"""The replay type graph keys its entities by the Phase 1 (I1) node ids.

Evidence-entity-model plan, gap: ``contract_evidence_collect`` kept its own
``decl:``/``record:``/``enum:``/``typedef:`` node keys, a second identity
scheme beside ``model.graph_entity_identity``'s one-ID-per-entity rule. That
scheme merged what I1 keeps apart (two ODR-distinct records sharing one
qualified name became one node) and could not be joined with any other
graph. The contract now:

1. every canonical node of a persisted replay graph **is** the entity's
   ``snapshot_identities`` node id -- set equality, over an exhaustive
   small-domain enumeration of the identity shapes I1 distinguishes (the
   oracle is ``snapshot_identities``, not the collector);
2. entities I1 keeps separate never share a replay node, and entities I1
   merges never split into two;
3. a context persisted by the *old* code (``contract_evidence`` schema 1,
   ``tests/fixtures/contract_context_v1``, recorded from ``origin/main``
   before the change) still loads, and both replay procedures give the
   decisions the old code gave on it; a fresh context under the new
   encoding re-evaluates the same findings to those same decisions too
   (a differential oracle between the two encodings).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.contract_context import finding_key
from abicheck.contract_context_io import (
    persisted_context_from_dict,
    persisted_context_to_dict,
)
from abicheck.contract_evidence_collect import build_type_graph
from abicheck.contract_relevance_types import ContractMode
from abicheck.contract_replay import (
    reevaluate_from_evidence,
    replay_original_decisions,
)
from abicheck.finding_identity import report_finding_id
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.model.graph_entity_identity import snapshot_identities
from abicheck.serialization import load_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "contract_context_v1"
MODES = ("public", "exports")

#: Lookup-spelling tiers; every other node of a persisted graph is canonical.
_SPELLING_PREFIXES = ("name:", "alias:")


def _canonical_nodes(snap: AbiSnapshot) -> set[str]:
    graph = build_type_graph(snap)
    return {n for n in graph.nodes if not n.startswith(_SPELLING_PREFIXES)}


def _i1_ids(snap: AbiSnapshot) -> set[str]:
    ids = snapshot_identities(snap)
    return {
        i.node_id
        for i in (
            *ids.functions,
            *ids.variables,
            *ids.records,
            *ids.enums,
            *ids.typedefs.values(),
        )
    }


def _record(name: str, qualified: str | None = None, field: str = "x") -> RecordType:
    return RecordType(
        name=name,
        qualified_name=qualified,
        kind="struct",
        size_bits=32,
        fields=[TypeField(name=field, type="int")],
    )


#: One entry per identity shape I1 distinguishes; each is
#: ``(bucket, factory)`` so a combination can hold two of a kind.
_POOL: list[tuple[str, object]] = [
    ("types", lambda: _record("A")),
    ("types", lambda: _record("A", field="y")),  # ODR duplicate of the above
    ("types", lambda: _record("A", "ns::A")),
    ("types", lambda: _record("A", "other::A")),
    ("enums", lambda: EnumType(name="A", members=[EnumMember(name="K", value=0)])),
    ("typedefs", lambda: ("A", "int")),  # a typedef beside an unrelated tag
    ("typedefs", lambda: ("A", "struct A")),  # typedef struct A A
    ("functions", lambda: Function(name="f", mangled="_Z1fv", return_type="int")),
    ("functions", lambda: Function(name="f", mangled="", return_type="int")),
    (
        "functions",
        lambda: Function(
            name="f",
            mangled="",
            return_type="int",
            params=[Param(name="a", type="A *")],
        ),
    ),
    ("variables", lambda: Variable(name="f", mangled="f", type="A")),
]


def _snapshot(combo: tuple[int, ...]) -> AbiSnapshot:
    snap = AbiSnapshot(library="libp.so", version="1")
    for index in combo:
        bucket, factory = _POOL[index]
        value = factory()  # type: ignore[operator]
        if bucket == "typedefs":
            alias, target = value
            snap.typedefs[alias] = target
        else:
            getattr(snap, bucket).append(value)
    return snap


_COMBOS = [
    combo
    for size in (1, 2, 3)
    for combo in itertools.combinations(range(len(_POOL)), size)
    # A dict holds one typedef per alias; two typedef entries in one combo
    # would silently test one of them.
    if sum(_POOL[i][0] == "typedefs" for i in combo) <= 1
]


def test_canonical_replay_nodes_are_exactly_the_i1_ids() -> None:
    """Set equality over every combination, batched so one failure names
    every disagreeing shape at once."""
    assert len(_COMBOS) > 100  # vacuity guard on the enumeration itself
    bad = []
    for combo in _COMBOS:
        snap = _snapshot(combo)
        expected, got = _i1_ids(snap), _canonical_nodes(snap)
        if expected != got:
            bad.append((combo, sorted(expected), sorted(got)))
    assert not bad, bad[:5]


def test_entities_i1_keeps_apart_never_share_a_replay_node() -> None:
    """The ODR pair, the unrelated same-named typedef, and two unmangled
    overloads each stay distinct nodes; the I1-merged ``typedef struct A A``
    stays one."""
    odr = _snapshot((0, 1))
    assert len(_canonical_nodes(odr)) == 2
    assert all(n.startswith("unresolved://type/") for n in _canonical_nodes(odr))

    tag_vs_typedef = _snapshot((0, 5))
    assert _canonical_nodes(tag_vs_typedef) == {"type://A", "type://A#typedef"}

    same_tag = _snapshot((0, 6))
    assert _canonical_nodes(same_tag) == {"type://A"}

    overloads = _snapshot((8, 9))
    assert len(_canonical_nodes(overloads)) == 2


def test_fresh_report_carries_the_new_encoding_version() -> None:
    from abicheck.contract_relevance_types import CONTRACT_EVIDENCE_SCHEMA_VERSION
    from abicheck.schemas import REPORT_SCHEMA_VERSION

    assert CONTRACT_EVIDENCE_SCHEMA_VERSION == 2
    assert REPORT_SCHEMA_VERSION == "5.6"


def _fixture_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    return load_snapshot(FIXTURE / "old.json"), load_snapshot(FIXTURE / "new.json")


def _findings(mode: str):  # type: ignore[no-untyped-def]
    old, new = _fixture_pair()
    result = compare(old, new, contract_evaluation=True, contract_mode=mode)
    assert result.contract_context is not None
    return result, list(result.changes) + list(result.out_of_surface_changes)


def _decisions(ctx, changes) -> dict[str, dict[str, str]]:  # type: ignore[no-untyped-def]
    replayed = replay_original_decisions(ctx)
    out: dict[str, dict[str, str]] = {}
    for mode in ContractMode:
        decided = reevaluate_from_evidence(
            ctx, changes, mode=mode, finding_id=report_finding_id
        )
        for change in changes:
            key = finding_key(change, report_finding_id)
            row = out.setdefault(key, {})
            if key in replayed:
                row["replayed"] = replayed[key].value
            row[mode.value] = decided[key].relevance.value
    return out


@pytest.mark.parametrize("mode", MODES)
def test_a_stored_pre_change_context_replays_as_the_old_code_did(mode: str) -> None:
    report = json.loads((FIXTURE / f"report_{mode}.json").read_text())
    ctx = persisted_context_from_dict(report["contract_context"])
    assert ctx.contract_evidence.schema_version == 1
    nodes = {
        n for entry in ctx.contract_evidence.providers for n in entry.type_graph.nodes
    }
    assert any(n.startswith("record:") for n in nodes)  # really the old encoding
    _result, changes = _findings(mode)
    expected = json.loads((FIXTURE / f"decisions_{mode}.json").read_text())
    assert expected and all("replayed" in row for row in expected.values())
    assert _decisions(ctx, changes) == expected


@pytest.mark.parametrize("mode", MODES)
def test_a_fresh_context_reevaluates_as_the_old_encoding_did(mode: str) -> None:
    result, changes = _findings(mode)
    ctx = persisted_context_from_dict(
        json.loads(json.dumps(persisted_context_to_dict(result.contract_context)))
    )
    expected = json.loads((FIXTURE / f"decisions_{mode}.json").read_text())
    assert _decisions(ctx, changes) == expected


@pytest.mark.parametrize(
    ("node", "category"),
    [
        ("decl://_Z1fv", "decl"),
        ("unresolved://decl/x", "decl"),
        ("type://A", "type"),
        ("type://A#typedef", "type"),
        ("unresolved://type/x", "type"),
        ("decl:f", "decl"),
        ("record:A", "type"),
        ("enum:A", "type"),
        ("typedef:A", "type"),
        ("name:A", None),
        ("alias:A", None),
        ("vtable://A", None),
    ],
)
def test_node_category_covers_both_encodings(node: str, category: str | None) -> None:
    from abicheck.policy.contract_graph_encoding import graph_node_category

    assert graph_node_category(node) == category


def test_a_schema1_node_with_an_empty_spelling_indexes_nothing() -> None:
    from abicheck.contract_evidence import TypeGraphSnapshot
    from abicheck.policy.contract_graph_encoding import graph_node_index

    graph = TypeGraphSnapshot(nodes=("decl:", "record:A"), edges=())
    assert graph_node_index(graph) == {"A": {"record:A"}}
