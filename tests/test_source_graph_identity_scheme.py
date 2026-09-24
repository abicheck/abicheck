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

"""Storage compatibility of the invariant-I1 graph ids (snapshot schema v50,
``SourceGraphSummary.schema_version`` 3): a pre-I1 graph still loads, and a
pre-I1/I1 pair is reported *not compared* -- never silently diffed."""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.buildsource.evidence_report import diff_embedded_build_source
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.compare.source_graph_identity_scheme import (
    source_graph_identity_mismatch,
)
from abicheck.model import AbiSnapshot
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import (
    GRAPH_IDENTITY_SCHEME_VERSION,
    SOURCE_GRAPH_VERSION,
    SourceGraphSummary,
)


@given(st.integers(1, 6), st.integers(1, 6))
def test_mismatch_iff_the_pair_straddles_the_i1_boundary(old: int, new: int) -> None:
    straddles = (old < GRAPH_IDENTITY_SCHEME_VERSION) != (
        new < GRAPH_IDENTITY_SCHEME_VERSION
    )
    reason = source_graph_identity_mismatch(old, new)
    assert (reason is not None) == straddles
    if reason is not None:
        assert "not compared" in reason


def test_new_graphs_are_written_under_the_i1_scheme() -> None:
    assert SOURCE_GRAPH_VERSION >= GRAPH_IDENTITY_SCHEME_VERSION
    assert SourceGraphSummary().to_dict()["schema_version"] == SOURCE_GRAPH_VERSION


def test_a_pre_i1_graph_loads_with_its_own_version() -> None:
    legacy = SourceGraphSummary(
        nodes=[GraphNode(id="decl://c_fn#sha256:ab", kind="source_decl", label="c_fn")]
    ).to_dict()
    legacy["schema_version"] = 2
    back = SourceGraphSummary.from_dict(legacy)
    assert back.schema_version == 2
    assert [n.id for n in back.nodes] == ["decl://c_fn#sha256:ab"]


def _graph(schema_version: int, *, mapped: bool) -> SourceGraphSummary:
    graph = SourceGraphSummary(
        nodes=[
            GraphNode(id="binary_symbol://_Z1av", kind="binary_symbol", label="_Z1av"),
            GraphNode(
                id="decl://_Z1av",
                kind="source_decl",
                label="a",
                attrs={"visibility": "public_header"},
            ),
        ],
        edges=[
            GraphEdge(
                src="decl://_Z1av",
                dst="binary_symbol://_Z1av",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            )
        ]
        if mapped
        else [],
    )
    graph.schema_version = schema_version
    return graph


def _run(old_version: int, new_version: int):
    def snap(graph: SourceGraphSummary) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            build_source=BuildSourcePack(root="", source_graph=graph),
            surface_graph=graph,
        )

    lines: list[str] = []
    changes, rows, _ = diff_embedded_build_source(
        None,
        None,
        None,
        None,
        "source",
        snap(_graph(new_version, mapped=False)),
        old_snapshot=snap(_graph(old_version, mapped=True)),
        on_output=lambda text: lines.append(text),
    )
    return changes, rows, lines


def test_straddling_pair_is_reported_not_compared() -> None:
    """The same real graph change (a dropped mapping edge) is a finding
    between two I1 graphs, and between two legacy graphs -- but across the
    boundary it is neither a finding nor silent: the L5 row says why."""
    for same in ((3, 3), (2, 2)):
        changes, rows, _ = _run(*same)
        assert changes, same
        assert not any("not compared" in str(r.get("detail", "")) for r in rows)

    changes, rows, lines = _run(2, 3)
    assert not changes
    l5 = [r for r in rows if r["layer"] == "L5_source_graph"]
    assert l5 and "not compared" in l5[0]["detail"]
    assert any("not compared" in line for line in lines)


def test_identity_aliases_survive_the_graph_table_encoding() -> None:
    """The compact v49+ graph encoding carries the alias map; dropping it
    would split a Mach-O-decorated or legacy-L4 spelling back into a second
    node on every stored snapshot."""
    from abicheck.model.graph_facts import GraphNode
    from abicheck.model.source_graph import SourceGraphSummary
    from abicheck.storage.graph_table_codec import (
        decode_graph_table,
        encode_graph_table,
    )

    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="decl://exit", kind="source_decl", label="exit"))
    graph.add_identity_alias("decl://_exit_decorated", "decl://exit")
    graph.add_identity_alias("decl://legacy#sig", "decl://exit")

    back = decode_graph_table(encode_graph_table(graph))

    assert back.identity_aliases == graph.identity_aliases
    assert back.resolve_node_id("decl://_exit_decorated") == "decl://exit"
