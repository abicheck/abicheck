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

"""Tests for the declared use-case manifest and graph join (G29 Phase 4
slice 2, ADR-057 amendment)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.source_graph import (
    EDGE_KINDS,
    NODE_KINDS,
    GraphNode,
    SourceGraphSummary,
)
from abicheck.errors import UseCaseManifestError
from abicheck.impact.use_cases import (
    USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS,
    USE_CASE_PROVENANCE,
    UseCaseDefinition,
    build_use_case_graph,
    join_use_case_graph,
    load_use_case_manifest,
    parse_use_case_manifest,
    test_case_node_id as make_test_case_node_id,
    use_case_node_id,
)


def _library_graph() -> SourceGraphSummary:
    """A minimal library graph: one public entry (`train`), one exported
    symbol with no declaration (`_Z5evalv`), and one internal-only decl
    (`detail::helper`, never resolvable as an entrypoint)."""
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="decl://train",
            kind="source_decl",
            label="train",
            attrs={"visibility": "public_header"},
        )
    )
    g.add_node(
        GraphNode(
            id="decl://helper",
            kind="source_decl",
            label="detail::helper",
            attrs={"visibility": "source"},
        )
    )
    g.add_node(
        GraphNode(id="binary_symbol://_Z5evalv", kind="binary_symbol", label="_Z5evalv")
    )
    return g


# ── schema registration ───────────────────────────────────────────────────


def test_use_case_kinds_are_registered_in_the_graph_schema() -> None:
    assert USE_CASE_NODE_KINDS <= NODE_KINDS
    assert USE_CASE_EDGE_KINDS <= EDGE_KINDS
    assert USE_CASE_NODE_KINDS == {"use_case", "test_case"}
    assert USE_CASE_EDGE_KINDS == {
        "USE_CASE_USES_ENTRY",
        "TEST_COVERS_USE_CASE",
        "TRACE_OBSERVED_ENTRY",
        "TRACE_OBSERVED_EDGE",
    }


# ── manifest parsing ──────────────────────────────────────────────────────


def test_parse_empty_document_is_a_valid_empty_manifest() -> None:
    assert parse_use_case_manifest(None) == []
    assert parse_use_case_manifest([]) == []


def test_parse_a_valid_manifest() -> None:
    raw = [
        {
            "use_case": "training-workflow",
            "entrypoints": ["train", "_Z5evalv"],
            "tests": ["test_train_e2e"],
        },
        {"use_case": "no-entrypoints-declared"},
    ]
    defs = parse_use_case_manifest(raw)
    assert defs == [
        UseCaseDefinition(
            use_case="training-workflow",
            entrypoints=("train", "_Z5evalv"),
            tests=("test_train_e2e",),
        ),
        UseCaseDefinition(use_case="no-entrypoints-declared"),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        {"use_case": "not-a-list"},
        "also not a list",
        42,
    ],
)
def test_parse_rejects_a_non_list_top_level_document(raw: object) -> None:
    with pytest.raises(UseCaseManifestError, match="top-level document"):
        parse_use_case_manifest(raw)


def test_parse_rejects_a_non_mapping_entry() -> None:
    with pytest.raises(UseCaseManifestError, match="entry 0 must be a mapping"):
        parse_use_case_manifest(["just a string"])


@pytest.mark.parametrize(
    "raw_entry", [{}, {"use_case": ""}, {"use_case": "   "}, {"use_case": 5}]
)
def test_parse_rejects_a_missing_or_blank_use_case_name(raw_entry: dict) -> None:
    with pytest.raises(UseCaseManifestError, match="use_case"):
        parse_use_case_manifest([raw_entry])


def test_parse_rejects_a_non_list_entrypoints_field() -> None:
    with pytest.raises(UseCaseManifestError, match="entrypoints"):
        parse_use_case_manifest([{"use_case": "x", "entrypoints": "train"}])


def test_parse_rejects_a_non_string_entrypoints_element() -> None:
    with pytest.raises(UseCaseManifestError, match="entrypoints"):
        parse_use_case_manifest([{"use_case": "x", "entrypoints": ["train", 5]}])


def test_parse_rejects_a_non_list_tests_field() -> None:
    with pytest.raises(UseCaseManifestError, match="tests"):
        parse_use_case_manifest([{"use_case": "x", "tests": "test_train"}])


def test_load_use_case_manifest_from_disk(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text(
        "- use_case: training-workflow\n"
        "  entrypoints: [train]\n"
        "  tests: [test_train_e2e]\n"
    )
    defs = load_use_case_manifest(manifest)
    assert defs == [
        UseCaseDefinition(
            use_case="training-workflow",
            entrypoints=("train",),
            tests=("test_train_e2e",),
        )
    ]


def test_load_use_case_manifest_rejects_a_malformed_file(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("not_a_list: true\n")
    with pytest.raises(UseCaseManifestError, match="top-level document"):
        load_use_case_manifest(manifest)


def test_load_use_case_manifest_of_an_empty_file_is_empty(tmp_path: Path) -> None:
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text("")
    assert load_use_case_manifest(manifest) == []


# ── build_use_case_graph ──────────────────────────────────────────────────


def test_build_use_case_graph_emits_the_use_case_node() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow")], library
    )
    assert graph.has_node(use_case_node_id("training-workflow"))
    node = next(n for n in graph.nodes if n.id == use_case_node_id("training-workflow"))
    assert node.kind == "use_case"
    assert node.label == "training-workflow"
    assert node.provenance == USE_CASE_PROVENANCE


def test_build_use_case_graph_resolves_entrypoints_by_id_and_label() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow",
                entrypoints=("train", "binary_symbol://_Z5evalv"),
            )
        ],
        library,
    )
    edges = {e.dst for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"}
    assert edges == {"decl://train", "binary_symbol://_Z5evalv"}
    assert all(
        e.src == use_case_node_id("training-workflow")
        for e in graph.edges
        if e.kind == "USE_CASE_USES_ENTRY"
    )


def test_build_use_case_graph_skips_an_unresolvable_entrypoint_silently() -> None:
    """An entrypoint the library graph cannot resolve is dropped, not an
    error -- the same 'absence, never a wrong answer' discipline
    consumer_graph.py already follows."""
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow", entrypoints=("does_not_exist",)
            )
        ],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []
    # The use_case node itself is still emitted.
    assert graph.has_node(use_case_node_id("training-workflow"))


def test_build_use_case_graph_does_not_resolve_an_internal_declaration() -> None:
    """A non-public decl is never a valid entrypoint target, even if a
    manifest names it by label."""
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow", entrypoints=("detail::helper",)
            )
        ],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []


def test_build_use_case_graph_emits_test_case_nodes_and_edges() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", tests=("test_train_e2e",))],
        library,
    )
    assert graph.has_node(make_test_case_node_id("test_train_e2e"))
    (edge,) = [e for e in graph.edges if e.kind == "TEST_COVERS_USE_CASE"]
    assert edge.src == make_test_case_node_id("test_train_e2e")
    assert edge.dst == use_case_node_id("training-workflow")


def test_build_use_case_graph_emits_a_test_case_regardless_of_entrypoint_resolution() -> (
    None
):
    """Unlike an entrypoint, a test identifier has no graph node kind to
    fail to resolve against."""
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(
                use_case="training-workflow",
                entrypoints=("does_not_exist",),
                tests=("test_train_e2e",),
            )
        ],
        library,
    )
    assert [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"] == []
    assert [e for e in graph.edges if e.kind == "TEST_COVERS_USE_CASE"] != []


def test_build_use_case_graph_handles_several_use_cases_independently() -> None:
    library = _library_graph()
    graph = build_use_case_graph(
        [
            UseCaseDefinition(use_case="uc1", entrypoints=("train",)),
            UseCaseDefinition(use_case="uc2", entrypoints=("does_not_exist",)),
        ],
        library,
    )
    assert graph.has_node(use_case_node_id("uc1"))
    assert graph.has_node(use_case_node_id("uc2"))
    edges = [e for e in graph.edges if e.kind == "USE_CASE_USES_ENTRY"]
    assert [e.src for e in edges] == [use_case_node_id("uc1")]


# ── join_use_case_graph ───────────────────────────────────────────────────


def test_join_folds_the_use_case_node_onto_the_shared_entry_node() -> None:
    library = _library_graph()
    before_nodes = len(library.nodes)
    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    # The use-case graph contributes exactly one *new* node (the use_case
    # itself); its entry edge folds onto the library's existing decl node.
    assert len(joined.nodes) == before_nodes + 1
    entry = next(n for n in joined.nodes if n.id == "decl://train")
    assert {f.producer for f in entry.facts} >= {USE_CASE_PROVENANCE}


def test_join_does_not_mutate_the_library_graph() -> None:
    library = _library_graph()
    nodes, edges = len(library.nodes), len(library.edges)
    lib_entry = next(n for n in library.nodes if n.id == "decl://train")
    use_cases = build_use_case_graph(
        [UseCaseDefinition(use_case="training-workflow", entrypoints=("train",))],
        library,
    )
    joined = join_use_case_graph(library, use_cases)
    assert (len(library.nodes), len(library.edges)) == (nodes, edges)
    # Membership counts alone don't prove non-mutation: add_node's ADR-046
    # D2 merge mutates the *stored* node in place, so a shallow
    # re-registration would leave the library's own node carrying the
    # use-case fact while every count stayed identical.
    assert USE_CASE_PROVENANCE not in {f.producer for f in lib_entry.facts}
    joined_entry = next(n for n in joined.nodes if n.id == "decl://train")
    assert joined_entry is not lib_entry


def test_join_carries_over_coverage_honesty_flags() -> None:
    library = _library_graph()
    library.extractor_passes = {"call_graph": True}
    library.narrowed_passes = {"type_graph": True}
    library.degraded_passes = {"include_graph": True}
    library.coverage = {"source_decls": 3}
    joined = join_use_case_graph(library, build_use_case_graph([], library))
    assert joined.extractor_passes == {"call_graph": True}
    assert joined.narrowed_passes == {"type_graph": True}
    assert joined.degraded_passes == {"include_graph": True}
    assert joined.coverage == {"source_decls": 3}


def test_join_of_an_empty_use_case_graph_is_a_pure_copy() -> None:
    library = _library_graph()
    joined = join_use_case_graph(library, SourceGraphSummary())
    assert len(joined.nodes) == len(library.nodes)
    assert len(joined.edges) == len(library.edges)
    assert joined is not library


# ── end-to-end: a use_case node joined onto a public entry ────────────────


def test_end_to_end_use_case_joins_onto_the_public_entry_node() -> None:
    library = _library_graph()
    defs = parse_use_case_manifest(
        [
            {
                "use_case": "training-workflow",
                "entrypoints": ["train"],
                "tests": ["test_train_e2e"],
            }
        ]
    )
    use_cases = build_use_case_graph(defs, library)
    joined = join_use_case_graph(library, use_cases)

    uc = next(n for n in joined.nodes if n.id == use_case_node_id("training-workflow"))
    assert uc.kind == "use_case"
    entry_edges = [
        e for e in joined.edges if e.kind == "USE_CASE_USES_ENTRY" and e.src == uc.id
    ]
    assert [e.dst for e in entry_edges] == ["decl://train"]

    test_edges = [e for e in joined.edges if e.kind == "TEST_COVERS_USE_CASE"]
    assert [e.src for e in test_edges] == [make_test_case_node_id("test_train_e2e")]
    assert [e.dst for e in test_edges] == [uc.id]

    # The public entry node itself is unchanged in kind/identity -- the join
    # only adds an incoming edge and a fact, never rewrites it.
    entry_node = next(n for n in joined.nodes if n.id == "decl://train")
    assert entry_node.kind == "source_decl"
    assert entry_node.label == "train"
