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

"""Observed graph evidence is persisted; projections are recomputed
(evidence-entity-model Phase 5c).

``compare.surface_graph.build_public_surface_facts`` projects the
snapshot's own records into ``declaration``/``type``/``symbol`` nodes and
``declares``/``references``/``declares_linker_name`` edges. Those must never
reach a stored snapshot, and a reader that needs them rebuilds them from the
records and gets what it had before the save.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.compare.surface_graph import (
    EDGE_EVIDENCE_CLASS,
    build_public_surface_facts,
)
from abicheck.model.graph_evidence_class import (
    PUBLIC_SURFACE_FACTS_PRODUCER,
    RECOMPUTABLE_FACT_PRODUCERS,
)
from abicheck.model.graph_facts import GraphEdge, GraphFact, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import load_snapshot, save_snapshot
from abicheck.storage.graph_section_codec import GraphSection
from abicheck.storage.graph_table_codec import (
    decode_graph_table,
    encode_graph_table,
    graph_table_to_legacy_dict,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "header_graph"
_PATHS = sorted(_FIXTURES.glob("*.json"))


def _builder_view(graph: SourceGraphSummary) -> tuple[set[Any], set[Any]]:
    """The builder's own contribution, as comparable sets."""
    nodes = {
        (n.id, n.kind, json.dumps(f.attrs, sort_keys=True))
        for n in graph.nodes
        for f in n.facts
        if f.producer == PUBLIC_SURFACE_FACTS_PRODUCER
    }
    edges = {
        e.relation_key()
        for e in graph.edges
        if any(f.producer == PUBLIC_SURFACE_FACTS_PRODUCER for f in e.facts)
    }
    return nodes, edges


def _with_facts(path: Path):  # type: ignore[no-untyped-def]
    snap = load_snapshot(path)
    graph = snap.surface_graph
    assert isinstance(graph, SourceGraphSummary)
    build_public_surface_facts(snap, graph)
    graph.finalize()
    return snap, graph


def test_fixtures_really_gain_projections() -> None:
    # Vacuity guard: the builder must add something on these fixtures, or
    # every test below passes by comparing a graph with itself.
    for path in _PATHS:
        _snap, graph = _with_facts(path)
        nodes, edges = _builder_view(graph)
        assert nodes and edges, path.name


@pytest.mark.parametrize("path", _PATHS, ids=lambda p: p.stem)
class TestStoredSnapshotHoldsObservedEvidenceOnly:
    def test_no_recomputable_fact_or_projection_edge_is_written(
        self, path: Path, tmp_path: Path
    ) -> None:
        snap, _graph = _with_facts(path)
        out = tmp_path / "s.json"
        save_snapshot(snap, out)
        payload = json.loads(out.read_text(encoding="utf-8"))["sections"]["graph"][
            "payload"
        ]["surface_graph"]
        legacy = graph_table_to_legacy_dict(payload)
        producers = {
            f["producer"] for e in legacy["nodes"] + legacy["edges"] for f in e["facts"]
        }
        assert not producers & RECOMPUTABLE_FACT_PRODUCERS
        assert not {e["edge"] for e in legacy["edges"]} & set(EDGE_EVIDENCE_CLASS)
        assert not {"declaration", "type", "symbol"} & {
            n["kind"] for n in legacy["nodes"]
        }

    def test_graph_plus_facts_persists_exactly_the_graph(
        self, path: Path, tmp_path: Path
    ) -> None:
        with_facts, _ = _with_facts(path)
        plain = load_snapshot(path)
        _ = plain.surface_graph
        a, b = tmp_path / "facts.json", tmp_path / "plain.json"
        save_snapshot(with_facts, a)
        save_snapshot(plain, b)
        assert a.read_bytes() == b.read_bytes()

    def test_a_query_rebuilds_the_same_projection_after_reload(
        self, path: Path, tmp_path: Path
    ) -> None:
        snap, graph = _with_facts(path)
        before = _builder_view(graph)
        out = tmp_path / "s.json"
        save_snapshot(snap, out)
        reloaded = load_snapshot(out)
        loaded_graph = reloaded.surface_graph
        assert isinstance(loaded_graph, SourceGraphSummary)
        assert _builder_view(loaded_graph) == (set(), set())  # really not stored
        build_public_surface_facts(reloaded, loaded_graph)  # on demand
        assert _builder_view(loaded_graph) == before


# ── generated graphs mixing observed and recomputable facts ─────────────

_producer = st.sampled_from(["header_ast_l2", "clang", PUBLIC_SURFACE_FACTS_PRODUCER])
_facts = st.lists(
    st.builds(
        GraphFact,
        producer=_producer,
        confidence=st.sampled_from(["high", "unknown"]),
        attrs=st.dictionaries(
            st.sampled_from(["a", "b"]), st.integers(0, 2), max_size=2
        ),
    ),
    min_size=1,
    max_size=3,
)


@st.composite
def _mixed_graphs(draw: st.DrawFn) -> SourceGraphSummary:
    g = SourceGraphSummary()
    ids = draw(
        st.lists(st.sampled_from(["decl://a", "decl://b", "header://h"]), max_size=4)
    )
    for node_id in ids:
        g.add_node(GraphNode(id=node_id, kind="k", label=node_id, facts=draw(_facts)))
    for _ in range(draw(st.integers(0, 5))):
        g.add_edge(
            GraphEdge(
                src=draw(st.sampled_from(ids or ["decl://x"])),
                dst=draw(st.sampled_from(["decl://a", "decl://y"])),
                kind=draw(st.sampled_from(["declares", "DECL_HAS_TYPE"])),
                facts=draw(_facts),
            )
        )
    return g.finalize()


def _strip_recomputable(legacy: dict[str, Any]) -> dict[str, Any]:
    """Independent oracle: drop recomputable facts from the to_dict() form,
    and any entity left with none."""
    out = copy.deepcopy(legacy)
    for key in ("nodes", "edges"):
        kept = []
        for entity in out[key]:
            entity["facts"] = [
                f
                for f in entity["facts"]
                if f["producer"] not in RECOMPUTABLE_FACT_PRODUCERS
            ]
            if entity["facts"]:
                kept.append(entity)
        out[key] = kept
    return out


@settings(max_examples=200, deadline=None)
@given(_mixed_graphs())
def test_decode_encode_equals_the_graph_without_its_projections(
    graph: SourceGraphSummary,
) -> None:
    payload = json.loads(json.dumps(encode_graph_table(graph)))
    # Through the same D8 section wrapper a stored pre-v49 graph went
    # through (its canonical form sorts attr keys, which orders conflicts).
    stored = GraphSection(surface_graph=_strip_recomputable(graph.to_dict()))
    expected = SourceGraphSummary.from_dict(
        json.loads(json.dumps(stored.to_document()["surface_graph"]))
    )
    assert decode_graph_table(payload).to_dict() == expected.to_dict()
