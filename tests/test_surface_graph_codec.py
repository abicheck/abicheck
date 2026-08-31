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

"""``AbiSnapshot.surface_graph`` persistence (ADR-063 Phase 3 D5, schema
v29) -- the plan's own "Tests" section for this phase names a populated-graph
round trip and an aliasing dedup as required, not deferred, regressions."""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict


def _graph_with_one_node() -> SourceGraphSummary:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://x", kind="declaration", label="x"))
    g.add_edge(GraphEdge(src="decl://x", dst="decl://y", kind="references"))
    return g


class TestNoGraph:
    """A snapshot predating this field (or one whose headers were never
    parsed) round-trips exactly as it always did -- no key at all, not a
    null."""

    def test_key_absent_on_encode(self) -> None:
        s = AbiSnapshot(library="libfoo.so", version="1.0")
        d = snapshot_to_dict(s)
        assert "surface_graph" not in d

    def test_stays_none_on_decode(self) -> None:
        s = AbiSnapshot(library="libfoo.so", version="1.0")
        d = snapshot_to_dict(s)
        assert snapshot_from_dict(d).surface_graph is None


class TestPopulatedGraphRoundTrip:
    """The plan's own required regression: construct a snapshot with a real,
    non-empty ``surface_graph``, write it, read it back, assert the reloaded
    object is a real ``SourceGraphSummary`` with the same nodes/edges -- not
    ``None`` and not a bare ``dict`` (today's writer never round-trips a
    graph through plain ``asdict()`` at all; this field needs the identical
    special-casing ``build_source.source_graph`` already has)."""

    def test_reloaded_object_is_a_source_graph_summary(self) -> None:
        s = AbiSnapshot(
            library="libfoo.so", version="1.0", surface_graph=_graph_with_one_node()
        )
        d = json.loads(json.dumps(snapshot_to_dict(s)))
        reloaded = snapshot_from_dict(d)
        assert isinstance(reloaded.surface_graph, SourceGraphSummary)

    def test_nodes_and_edges_survive(self) -> None:
        s = AbiSnapshot(
            library="libfoo.so", version="1.0", surface_graph=_graph_with_one_node()
        )
        d = json.loads(json.dumps(snapshot_to_dict(s)))
        reloaded = snapshot_from_dict(d)
        assert reloaded.surface_graph is not None
        assert reloaded.surface_graph.has_node("decl://x")
        assert [(e.src, e.dst, e.kind) for e in reloaded.surface_graph.edges] == [
            ("decl://x", "decl://y", "references")
        ]

    def test_json_serializable(self) -> None:
        # The whole point of to_dict() over asdict(): must survive a real
        # json.dumps, not just Python-object equality.
        s = AbiSnapshot(
            library="libfoo.so", version="1.0", surface_graph=_graph_with_one_node()
        )
        text = json.dumps(snapshot_to_dict(s))
        assert "decl://x" in text


class TestSharedInstanceIdentity:
    """The shared-assembly claim itself: when ``surface_graph`` and
    ``build_source.source_graph`` are literally the same object, the graph
    is written once (not twice) and the alias is restored -- ``is``, not
    just equal content -- on load."""

    def test_encode_dedups_the_aliased_pack_key(self) -> None:
        graph = _graph_with_one_node()
        pack = BuildSourcePack(root=Path(""), source_graph=graph)
        s = AbiSnapshot(
            library="libfoo.so", version="1.0", surface_graph=graph, build_source=pack
        )
        d = snapshot_to_dict(s)
        assert "surface_graph" in d
        assert "source_graph" not in d["build_source"]

    def test_decode_restores_the_alias_by_identity(self) -> None:
        graph = _graph_with_one_node()
        pack = BuildSourcePack(root=Path(""), source_graph=graph)
        s = AbiSnapshot(
            library="libfoo.so", version="1.0", surface_graph=graph, build_source=pack
        )
        d = json.loads(json.dumps(snapshot_to_dict(s)))
        reloaded = snapshot_from_dict(d)
        assert reloaded.build_source is not None
        assert reloaded.surface_graph is not None
        assert reloaded.build_source.source_graph is reloaded.surface_graph

    def test_two_genuinely_separate_graphs_both_survive(self) -> None:
        # Not the ordinary case (nothing in production builds two distinct
        # graphs today), but the codec must not silently drop or merge one
        # when they happen to differ -- only dedup an actual identity match.
        surface_only = _graph_with_one_node()
        l5_only = SourceGraphSummary()
        l5_only.add_node(GraphNode(id="decl://z", kind="declaration", label="z"))
        pack = BuildSourcePack(root=Path(""), source_graph=l5_only)
        s = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            surface_graph=surface_only,
            build_source=pack,
        )
        d = json.loads(json.dumps(snapshot_to_dict(s)))
        assert "source_graph" in d["build_source"]
        reloaded = snapshot_from_dict(d)
        assert reloaded.surface_graph is not None
        assert reloaded.build_source is not None
        # The encoder's own dedup is identity-gated (module docstring:
        # "only dedup an actual identity match") -- when the two graphs
        # genuinely differ, both are independently encoded, and decode
        # must not collapse them into one: that would silently discard
        # every node/edge the nested (L3-L5) graph carries in favor of the
        # unrelated top-level (public-surface) one (Codex review, PR #962).
        assert reloaded.build_source.source_graph is not reloaded.surface_graph
        assert {n.id for n in reloaded.surface_graph.nodes} == {
            n.id for n in surface_only.nodes
        }
        assert {n.id for n in reloaded.build_source.source_graph.nodes} == {
            n.id for n in l5_only.nodes
        }


class TestLegacyDocumentNeverAliasesForward:
    """A document written before this field existed carries no top-level
    ``surface_graph`` key -- its nested ``build_source.source_graph`` must
    NOT be promoted to ``AbiSnapshot.surface_graph``: that graph predates
    the public-surface builder and was never populated with the edges a
    query traverses, so aliasing it forward would silently skip the
    intentional approximate-backfill path in favor of a graph that resolves
    to a smaller surface than either the backfill or the pre-Phase-3
    traversal would."""

    def test_legacy_document_has_no_top_level_key(self) -> None:
        graph = _graph_with_one_node()
        pack = BuildSourcePack(root=Path(""), source_graph=graph)
        s = AbiSnapshot(library="libfoo.so", version="1.0", build_source=pack)
        d = snapshot_to_dict(s)
        assert "surface_graph" not in d
        assert "source_graph" in d["build_source"]

    def test_surface_graph_stays_none_after_load(self) -> None:
        graph = _graph_with_one_node()
        pack = BuildSourcePack(root=Path(""), source_graph=graph)
        s = AbiSnapshot(library="libfoo.so", version="1.0", build_source=pack)
        d = json.loads(json.dumps(snapshot_to_dict(s)))
        reloaded = snapshot_from_dict(d)
        assert reloaded.surface_graph is None

    def test_build_source_side_is_unaffected(self) -> None:
        graph = _graph_with_one_node()
        pack = BuildSourcePack(root=Path(""), source_graph=graph)
        s = AbiSnapshot(library="libfoo.so", version="1.0", build_source=pack)
        d = json.loads(json.dumps(snapshot_to_dict(s)))
        reloaded = snapshot_from_dict(d)
        assert reloaded.build_source is not None
        assert reloaded.build_source.source_graph is not None
        assert reloaded.build_source.source_graph.has_node("decl://x")
