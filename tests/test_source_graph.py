# Copyright 2026 Nikolay Petrov
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

"""Tests for ADR-031 L5 source graph: schema round-trip, the build-evidence
graph builder (Phase 2), the structural diff (Phase 5 seed), and pack +
CLI wiring."""

from __future__ import annotations

import json

from abicheck.buildsource.build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    LinkUnit,
    Target,
    TargetKind,
)
from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerConfidence
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph import (
    EDGE_KINDS,
    EVIDENCE_TIER_L5,
    NODE_KINDS,
    SOURCE_GRAPH_VERSION,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    build_source_graph,
    diff_source_graph,
    diff_source_graph_findings,
    fold_source_edges,
    mark_source_edges_extractor_coverage,
)
from abicheck.checker_policy import RISK_KINDS, ChangeKind


def _sample_build() -> BuildEvidence:
    b = BuildEvidence(generated_files=["gen/config.h"])
    b.targets.append(
        Target(
            id="target://libfoo",
            name="foo",
            kind=TargetKind.SHARED_LIBRARY,
            source_files=["src/foo.cpp", "gen/config.h"],
            public_headers=["include/foo.h"],
            dependencies=["target://libbar", "sys://pthread"],
            confidence=Confidence.HIGH,
        )
    )
    b.targets.append(Target(id="target://libbar", name="bar"))
    b.compile_units.append(
        CompileUnit(
            id="cu://foo",
            source="src/foo.cpp",
            output="foo.o",
            target_id="target://libfoo",
            abi_relevant_flags=["-fvisibility=hidden", "-std=c++20"],
        )
    )
    return b


# ── Phase 2: build_source_graph ────────────────────────────────────────────


def test_build_graph_emits_expected_nodes_and_edges() -> None:
    g = build_source_graph(_sample_build())
    kinds = {n.kind for n in g.nodes}
    assert "target" in kinds
    assert "source" in kinds
    assert "header" in kinds
    assert "compile_unit" in kinds
    assert "build_option" in kinds
    # gen/config.h is in generated_files → typed generated_file, not source.
    assert "generated_file" in kinds
    # A dependency that is not one of our own targets is an external_dependency.
    assert "external_dependency" in kinds

    edge_kinds = {e.kind for e in g.edges}
    assert "TARGET_HAS_SOURCE" in edge_kinds
    assert "TARGET_HAS_PUBLIC_HEADER" in edge_kinds
    assert "TARGET_DEPENDS_ON" in edge_kinds
    assert "COMPILE_UNIT_BUILDS_SOURCE" in edge_kinds
    assert "COMPILE_UNIT_USES_OPTION" in edge_kinds


def test_build_graph_node_and_edge_kinds_are_in_schema() -> None:
    g = build_source_graph(_sample_build())
    assert all(n.kind in NODE_KINDS for n in g.nodes)
    assert all(e.kind in EDGE_KINDS for e in g.edges)


def test_generated_source_typed_generated_file_not_source() -> None:
    g = build_source_graph(_sample_build())
    config = next(n for n in g.nodes if n.label == "gen/config.h")
    assert config.kind == "generated_file"
    assert config.attrs.get("generated") is True


def test_compile_unit_option_edges_match_flags() -> None:
    g = build_source_graph(_sample_build())
    opt_edges = [e for e in g.edges if e.kind == "COMPILE_UNIT_USES_OPTION"]
    targets = {e.dst for e in opt_edges}
    assert "build_option://-fvisibility=hidden" in targets
    assert "build_option://-std=c++20" in targets
    # Option edges carry high confidence (derived from exact argv).
    assert all(e.confidence == "high" for e in opt_edges)


def test_coverage_counts_populated() -> None:
    g = build_source_graph(_sample_build())
    assert g.coverage["targets"] == 2
    assert g.coverage["compile_units"] == 1
    # No call/include extraction in Phase 2 — explicitly marked not-collected.
    assert g.coverage["call_edges"]["collected"] is False
    assert g.coverage["include_edges"]["collected"] is False


def test_compile_unit_emits_object_edge() -> None:
    # ADR-041 P1 #2: the object/link provenance graph.
    g = build_source_graph(_sample_build())
    assert "object_file" in {n.kind for n in g.nodes}
    obj_edges = [e for e in g.edges if e.kind == "COMPILE_UNIT_EMITS_OBJECT"]
    assert obj_edges == [
        e for e in obj_edges if e.src == "cu://foo" and e.dst == "object://foo.o"
    ]
    assert len(obj_edges) == 1


def _build_with_link_unit(**link_kwargs: object) -> BuildEvidence:
    b = _sample_build()
    b.link_units.append(
        LinkUnit(
            id="link://libfoo.so",
            target_id="target://libfoo",
            output="libfoo.so",
            kind="shared_library",
            inputs=["foo.o", "libbar.a"],
            **link_kwargs,
        )
    )
    return b


def test_link_unit_node_and_target_edge() -> None:
    g = build_source_graph(_build_with_link_unit())
    link_node = next(n for n in g.nodes if n.id == "link://libfoo.so")
    assert link_node.kind == "link_unit"
    assert any(
        e.kind == "TARGET_HAS_LINK_UNIT"
        and e.src == "target://libfoo"
        and e.dst == "link://libfoo.so"
        for e in g.edges
    )


def test_link_unit_input_classified_object_vs_static_library() -> None:
    g = build_source_graph(_build_with_link_unit())
    node_by_id = {n.id: n for n in g.nodes}
    input_edges = {
        e.dst
        for e in g.edges
        if e.kind == "LINK_UNIT_HAS_INPUT" and e.src == "link://libfoo.so"
    }
    assert "object://foo.o" in input_edges
    assert "static_library://libbar.a" in input_edges
    assert node_by_id["object://foo.o"].kind == "object_file"
    assert node_by_id["static_library://libbar.a"].kind == "static_library"


def test_link_unit_input_object_merges_with_compile_unit_emitted_object() -> None:
    # The same "foo.o" both a compile unit emits and a link unit consumes must
    # land on the *same* node -- so a dependency traced to one object
    # correlates across both slices, not a disconnected duplicate.
    g = build_source_graph(_build_with_link_unit())
    object_nodes = [n for n in g.nodes if n.id == "object://foo.o"]
    assert len(object_nodes) == 1


def test_link_unit_version_script_node_and_edge() -> None:
    g = build_source_graph(_build_with_link_unit(version_script="exports.map"))
    vnode = next(n for n in g.nodes if n.id == "version_script://exports.map")
    assert vnode.kind == "version_script"
    assert any(
        e.kind == "LINK_UNIT_USES_VERSION_SCRIPT"
        and e.src == "link://libfoo.so"
        and e.dst == "version_script://exports.map"
        for e in g.edges
    )


def test_link_unit_exports_symbol_via_source_abi() -> None:
    # LINK_UNIT_EXPORTS_SYMBOL is added once a source_abi surface resolves
    # which symbols the owning target exports (Phase 3-4), correlating the
    # link unit _fold_link_provenance already created with those symbols.
    surface = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    surface.mappings["source_decl_to_binary_symbol"] = {"foo_api": "_Z7foo_apiv"}
    surface.reachable_functions = [
        SourceEntity(
            id="foo_api",
            kind="function",
            qualified_name="foo_api",
            visibility="public_header",
        )
    ]
    g = build_source_graph(_build_with_link_unit(), surface)
    link_exports = [e for e in g.edges if e.kind == "LINK_UNIT_EXPORTS_SYMBOL"]
    assert any(
        e.src == "link://libfoo.so" and e.dst == "binary_symbol://_Z7foo_apiv"
        for e in link_exports
    )


def test_build_graph_is_deterministic() -> None:
    b = _sample_build()
    assert build_source_graph(b).graph_id == build_source_graph(b).graph_id


def test_empty_build_yields_empty_graph() -> None:
    g = build_source_graph(BuildEvidence())
    assert g.nodes == []
    assert g.edges == []
    assert g.coverage["targets"] == 0


def test_target_confidence_maps_onto_node_and_edges() -> None:
    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://red",
            source_files=["a.cpp"],
            confidence=Confidence.REDUCED,
        )
    )
    b.targets.append(
        Target(
            id="target://unk",
            source_files=["b.cpp"],
            confidence=Confidence.UNKNOWN,
        )
    )
    g = build_source_graph(b)
    by_id = {n.id: n for n in g.nodes}
    assert by_id["target://red"].confidence == "reduced"
    assert by_id["target://unk"].confidence == "unknown"


def test_blank_source_path_is_skipped() -> None:
    # A degenerate empty path in source_files must not create a stray "" node.
    b = BuildEvidence()
    b.targets.append(Target(id="target://t", source_files=["", "real.cpp"]))
    g = build_source_graph(b)
    assert not any(n.id == "source://" for n in g.nodes)
    assert any(n.label == "real.cpp" for n in g.nodes)


def test_compile_unit_without_source_emits_no_source_edge() -> None:
    b = BuildEvidence()
    b.compile_units.append(CompileUnit(id="cu://nosrc", source=""))
    g = build_source_graph(b)
    assert any(n.id == "cu://nosrc" for n in g.nodes)
    assert not any(e.kind == "COMPILE_UNIT_BUILDS_SOURCE" for e in g.edges)


# ── Phases 3-4: enrich from the L4 source surface ───────────────────────────


def _entity(
    qn: str,
    kind: str,
    *,
    mangled: str = "",
    path: str = "include/foo.h",
    origin: str = "PUBLIC_HEADER",
    conf: LayerConfidence = LayerConfidence.HIGH,
) -> SourceEntity:
    return SourceEntity(
        id=qn,
        kind=kind,
        qualified_name=qn,
        mangled_name=mangled,
        source_location=SourceLocation(path=path, line=1, origin=origin),
        visibility="public_header",
        confidence=conf,
    )


def _sample_surface() -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    s.reachable_declarations.append(
        _entity("foo::bar", "function", mangled="_ZN3foo3barEv")
    )
    s.reachable_types.append(_entity("foo::Widget", "record"))
    s.reachable_types.append(_entity("foo::Color", "enum"))
    s.reachable_types.append(_entity("foo::Alias", "typedef"))
    s.reachable_macros.append(
        _entity("FOO_VERSION", "macro", conf=LayerConfidence.REDUCED)
    )
    # Keyed by entity identity (the mangled name for C++), exactly as
    # link_source_abi/relink_surface_exports persist it — not by qualified_name.
    s.mappings["source_decl_to_binary_symbol"] = {"_ZN3foo3barEv": "_ZN3foo3barEv"}
    s.mappings["source_type_to_debug_type"] = {"foo::Widget": "struct foo::Widget"}
    return s


def test_source_abi_builds_public_reachability_slice() -> None:
    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["include/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    g = build_source_graph(b, source_abi=_sample_surface())
    edge_kinds = {e.kind for e in g.edges}
    # target -> header -> decl -> exported symbol, plus target -> symbol.
    assert "TARGET_HAS_PUBLIC_HEADER" in edge_kinds
    assert "SOURCE_DECLARES" in edge_kinds
    assert "SOURCE_DECL_MAPS_TO_SYMBOL" in edge_kinds
    assert "BINARY_EXPORTS_SYMBOL" in edge_kinds
    assert "SOURCE_TYPE_MAPS_TO_DEBUG_TYPE" in edge_kinds
    assert all(e.kind in EDGE_KINDS for e in g.edges)
    assert all(n.kind in NODE_KINDS for n in g.nodes)


def test_ordinary_function_decl_node_marked_not_consumer_compiled() -> None:
    """An ordinary out-of-line function (kind="function", no sibling
    inline/template entity) gets consumer_compiled_body=False -- its body is
    compiled into the library binary only, never into consumer code
    (Codex review, ADR-044 P1 item 1 follow-up)."""
    b = BuildEvidence()
    b.targets.append(Target(id="target://libfoo", confidence=Confidence.HIGH))
    surface = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    surface.reachable_declarations.append(
        _entity("foo::bar", "function", mangled="_ZN3foo3barEv")
    )
    g = build_source_graph(b, source_abi=surface)
    decl_nodes = [n for n in g.nodes if n.kind == "source_decl"]
    assert len(decl_nodes) == 1
    assert decl_nodes[0].attrs["consumer_compiled_body"] is False


def test_inline_function_decl_node_marked_consumer_compiled_despite_id_collision() -> (
    None
):
    """clang.py always emits a plain "function" entity for a public-header
    function *and*, when it has a body, a sibling "inline" entity sharing the
    same identity() (mangled name) -- both collide onto the same graph node
    id, and add_node keeps only the first writer's (the "function" entity's,
    since reachable_declarations is iterated first) attrs. Without computing
    consumer_compiled_body from the full identity set up front, the winning
    node would read decl_kind="function" and lose the inline signal entirely
    (Codex review)."""
    b = BuildEvidence()
    b.targets.append(Target(id="target://libfoo", confidence=Confidence.HIGH))
    surface = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    surface.reachable_declarations.append(
        _entity("foo::inl", "function", mangled="_ZN3foo3inlEv")
    )
    surface.reachable_inline_bodies.append(
        _entity("foo::inl", "inline", mangled="_ZN3foo3inlEv")
    )
    g = build_source_graph(b, source_abi=surface)
    decl_nodes = [n for n in g.nodes if n.kind == "source_decl"]
    assert len(decl_nodes) == 1
    assert decl_nodes[0].attrs["decl_kind"] == "function"
    assert decl_nodes[0].attrs["consumer_compiled_body"] is True


def test_cpp_decl_maps_to_symbol_with_identity_keyed_mapping() -> None:
    # Regression (Codex): the persisted source_decl_to_binary_symbol map is keyed
    # by entity identity (mangled name for C++), so build_source_graph must look
    # it up by identity, not qualified_name, or the decl->symbol edge is dropped
    # for every C++ symbol (qualified_name != mangled name).
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    map_edges = [e for e in g.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"]
    assert len(map_edges) == 1
    decl_ids = {n.id for n in g.nodes if n.kind == "source_decl"}
    sym_ids = {n.id for n in g.nodes if n.kind == "binary_symbol"}
    assert map_edges[0].src in decl_ids
    assert map_edges[0].dst in sym_ids


def test_source_abi_type_kind_dispatch() -> None:
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    kinds = {n.label: n.kind for n in g.nodes}
    assert kinds["foo::Widget"] == "record_type"
    assert kinds["foo::Color"] == "enum_type"
    assert kinds["foo::Alias"] == "typedef"
    assert kinds["FOO_VERSION"] == "macro"


def test_source_abi_coverage_counts_decls_and_mappings() -> None:
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    assert g.coverage["source_decls"] == 1
    assert g.coverage["binary_symbol_mappings"] == 1


def test_source_abi_decl_without_symbol_has_no_mapping_edge() -> None:
    s = SourceAbiSurface(library="l", target_id="target://t")
    s.reachable_declarations.append(_entity("foo::unshipped", "function"))
    # no entry in source_decl_to_binary_symbol
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert not any(e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" for e in g.edges)
    assert any(n.kind == "source_decl" for n in g.nodes)


def test_source_abi_materializes_missing_target() -> None:
    # The surface names a target the (empty) build evidence never enumerated.
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    target = next((n for n in g.nodes if n.id == "target://libfoo"), None)
    assert target is not None
    assert target.kind == "target"
    assert target.provenance == "source_abi"


def test_source_abi_edges_carry_source_provenance() -> None:
    g = build_source_graph(BuildEvidence(), source_abi=_sample_surface())
    src_edges = [e for e in g.edges if e.kind == "SOURCE_DECLARES"]
    assert src_edges
    assert all(e.provenance == "source_abi" for e in src_edges)


def test_source_abi_degenerate_inputs_handled() -> None:
    # No target_id (so no BINARY_EXPORTS_SYMBOL owner), a decl with no source
    # location (so no SOURCE_DECLARES edge), and a blank symbol mapping value
    # (skipped) must all be tolerated without error.
    s = SourceAbiSurface(library="l", target_id="")
    s.reachable_declarations.append(
        SourceEntity(
            id="d",
            kind="function",
            qualified_name="loose",
            source_location=None,
            confidence=LayerConfidence.UNKNOWN,
        )
    )
    s.mappings["source_decl_to_binary_symbol"] = {"loose": "", "other": "_Zsym"}
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert not any(e.kind == "SOURCE_DECLARES" for e in g.edges)
    assert not any(e.kind == "BINARY_EXPORTS_SYMBOL" for e in g.edges)
    # The blank mapping value is skipped; the real one becomes a symbol node.
    assert any(n.kind == "binary_symbol" and n.label == "_Zsym" for n in g.nodes)


# ── PR1: source_edges fold (ADR-038 C.9) ────────────────────────────────────


def test_fold_source_edges_call_edge_creates_decl_nodes() -> None:
    g = SourceGraphSummary()
    added = fold_source_edges(
        g,
        [
            {
                "edge": "DECL_CALLS_DECL",
                "src": "_ZN3foo3barEv",
                "dst": "_ZN3foo3bazEv",
                "provenance": "clang-plugin",
                "confidence": "high",
                "attrs": {"call_kind": "direct"},
            }
        ],
    )
    assert added == 1
    call_edges = [e for e in g.edges if e.kind == "DECL_CALLS_DECL"]
    assert len(call_edges) == 1
    assert call_edges[0].src == "decl://_ZN3foo3barEv"
    assert call_edges[0].dst == "decl://_ZN3foo3bazEv"
    assert call_edges[0].provenance == "clang-plugin"
    assert call_edges[0].attrs == {"call_kind": "direct"}
    assert {n.id for n in g.nodes} == {"decl://_ZN3foo3barEv", "decl://_ZN3foo3bazEv"}
    assert all(n.kind == "source_decl" for n in g.nodes)


def test_fold_source_edges_decl_has_type_maps_decl_and_type_nodes() -> None:
    g = SourceGraphSummary()
    fold_source_edges(
        g, [{"edge": "DECL_HAS_TYPE", "src": "foo::field", "dst": "foo::Widget"}]
    )
    src_node = next(n for n in g.nodes if n.id == "decl://foo::field")
    dst_node = next(n for n in g.nodes if n.id == "type://foo::Widget")
    assert src_node.kind == "source_decl"
    assert dst_node.kind == "record_type"


def test_fold_source_edges_type_inherits_maps_both_sides_to_type_nodes() -> None:
    g = SourceGraphSummary()
    fold_source_edges(
        g, [{"edge": "TYPE_INHERITS", "src": "foo::Derived", "dst": "foo::Base"}]
    )
    assert all(n.kind == "record_type" for n in g.nodes)


def test_fold_source_edges_dedupes_against_call_graph_pass() -> None:
    """An edge already folded by a separate call/type-graph pass must not be
    duplicated -- first-writer-wins via add_edge's (src, dst, kind) key."""
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://a", kind="source_decl", provenance="call_graph"))
    g.add_node(GraphNode(id="decl://b", kind="source_decl", provenance="call_graph"))
    g.add_edge(
        GraphEdge(
            src="decl://a",
            dst="decl://b",
            kind="DECL_CALLS_DECL",
            provenance="call_graph",
            confidence="high",
        )
    )
    added = fold_source_edges(
        g,
        [{"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "confidence": "reduced"}],
    )
    assert added == 0
    call_edges = [e for e in g.edges if e.kind == "DECL_CALLS_DECL"]
    assert len(call_edges) == 1
    assert call_edges[0].provenance == "call_graph"  # first writer wins


def test_fold_source_edges_skips_malformed_rows() -> None:
    g = SourceGraphSummary()
    added = fold_source_edges(
        g,
        [
            {"edge": "", "src": "a", "dst": "b"},
            {"edge": "DECL_CALLS_DECL", "src": "", "dst": "b"},
            {"edge": "DECL_CALLS_DECL", "src": "a", "dst": ""},
            "not-a-dict",
            {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b"},
        ],
    )
    assert added == 1
    assert len(g.edges) == 1


def test_fold_source_edges_rejects_kind_outside_dependency_edge_kinds() -> None:
    # DEPENDENCY_EDGE_KINDS, not the broader EDGE_KINDS (CodeRabbit review):
    # source_edges only ever carries the five decl/type-dependency kinds, so
    # an unrelated/forward-incompatible kind must not silently fall through
    # to the decl/decl default node mapping.
    g = SourceGraphSummary()
    added = fold_source_edges(
        g, [{"edge": "TARGET_DEPENDS_ON", "src": "a", "dst": "b"}]
    )
    assert added == 0
    assert g.nodes == []
    assert g.edges == []


def test_fold_source_edges_marks_dst_defined_in_project() -> None:
    # The Codex-flagged gap (PR #555): without dst_file -> defined_in_project
    # marking, a callee/reference/type that only ever appears as a
    # source_edges endpoint has no project provenance, so
    # is_internal_dependency_node can't recognize it.
    g = SourceGraphSummary()
    fold_source_edges(
        g,
        [
            {
                "edge": "DECL_CALLS_DECL",
                "src": "_ZN3api8publicFnEv",
                "dst": "_ZN6detail6helperEv",
                "attrs": {"dst_file": "src/detail/helper.h"},
            }
        ],
        frozenset({"src/detail/helper.h"}),
    )
    dst_node = next(n for n in g.nodes if n.id == "decl://_ZN6detail6helperEv")
    assert dst_node.attrs.get("defined_in_project") is True
    assert dst_node.attrs.get("def_file") == "src/detail/helper.h"
    src_node = next(n for n in g.nodes if n.id == "decl://_ZN3api8publicFnEv")
    assert not src_node.attrs.get("defined_in_project")


def test_fold_source_edges_does_not_mark_when_dst_file_outside_project() -> None:
    g = SourceGraphSummary()
    fold_source_edges(
        g,
        [
            {
                "edge": "DECL_CALLS_DECL",
                "src": "a",
                "dst": "b",
                "attrs": {"dst_file": "/usr/include/vector"},
            }
        ],
        frozenset({"src/detail/helper.h"}),
    )
    dst_node = next(n for n in g.nodes if n.id == "decl://b")
    assert not dst_node.attrs.get("defined_in_project")


def test_fold_source_edges_backfills_existing_node_unless_visibility_set() -> None:
    # Mirrors augment_graph_with_types's backfill behavior: a node already
    # present without visibility gets defined_in_project backfilled; a node
    # carrying real L4 visibility evidence is never overridden.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://b", kind="source_decl", provenance="earlier"))
    g.add_node(
        GraphNode(
            id="decl://c",
            kind="source_decl",
            provenance="source_abi",
            attrs={"visibility": "public_header"},
        )
    )
    fold_source_edges(
        g,
        [
            {
                "edge": "DECL_CALLS_DECL",
                "src": "a",
                "dst": "b",
                "attrs": {"dst_file": "src/detail/helper.h"},
            },
            {
                "edge": "DECL_CALLS_DECL",
                "src": "a",
                "dst": "c",
                "attrs": {"dst_file": "src/detail/helper.h"},
            },
        ],
        frozenset({"src/detail/helper.h"}),
    )
    assert next(n for n in g.nodes if n.id == "decl://b").attrs.get(
        "defined_in_project"
    )
    assert not next(n for n in g.nodes if n.id == "decl://c").attrs.get(
        "defined_in_project"
    )


def test_fold_source_edges_type_edge_dst_file_marks_project_node() -> None:
    # Unlike the C++ plugin (which never resolves a type spelling to a
    # file), the Python inline extractor resolves dst_file uniformly for
    # every edge kind -- this must be honored regardless of kind.
    g = SourceGraphSummary()
    fold_source_edges(
        g,
        [
            {
                "edge": "TYPE_INHERITS",
                "src": "ns::Derived",
                "dst": "ns::Base",
                "attrs": {"dst_file": "src/detail/base.h"},
            }
        ],
        frozenset({"src/detail/base.h"}),
    )
    dst_node = next(n for n in g.nodes if n.id == "type://ns::Base")
    assert dst_node.attrs.get("defined_in_project") is True


def test_build_source_graph_folds_surface_source_edges() -> None:
    s = _sample_surface()
    s.source_edges = [
        {"edge": "DECL_CALLS_DECL", "src": "_ZN3foo3barEv", "dst": "_ZN3foo3quxEv"}
    ]
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert any(e.kind == "DECL_CALLS_DECL" for e in g.edges)


def test_build_source_graph_marks_source_edges_dst_defined_in_project() -> None:
    build = BuildEvidence(
        targets=[Target(id="target://libfoo", name="libfoo")],
        compile_units=[
            CompileUnit(
                id="cu://src/detail/helper.cpp",
                target_id="target://libfoo",
                source="src/detail/helper.cpp",
            )
        ],
    )
    s = _sample_surface()
    s.source_edges = [
        {
            "edge": "DECL_CALLS_DECL",
            "src": "_ZN3foo3barEv",
            "dst": "_ZN6detail6helperEv",
            "attrs": {"dst_file": "src/detail/helper.cpp"},
        }
    ]
    g = build_source_graph(build, source_abi=s)
    dst_node = next(n for n in g.nodes if n.id == "decl://_ZN6detail6helperEv")
    assert dst_node.attrs.get("defined_in_project") is True


#: The one source_edges producer whose coverage genuinely matches a full,
#: unfiltered call/type-graph replay (source_graph._FULL_WALK_SOURCE_EDGES_PRODUCER).
_FULL_WALK_PRODUCER_FACT_SET = {"producer": "abicheck-cc-clang-extractor"}


def test_mark_source_edges_extractor_coverage_when_complete() -> None:
    """A caller that folds source_edges but never runs a call/type-graph
    replay (e.g. Flow-2 pack ingestion) must still translate a
    confirmed-complete rollup into extractor_passes coverage, or the
    decl-dependency crosscheck reads the graph as "no pass ever ran"
    (Codex review)."""
    s = _sample_surface()
    # "complete" requires entities_present (coverage_state_for_family) --
    # non-empty source_edges backs that claim, else it's the legacy-drop
    # scenario a sibling test guards against.
    s.source_edges = [
        {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "confidence": "high"},
    ]
    s.coverage["fact_family_states"] = {"source_edges": "complete"}
    s.coverage["fact_set"] = _FULL_WALK_PRODUCER_FACT_SET
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert g.extractor_passes["call_graph"] is True
    assert g.extractor_passes["type_graph"] is True


def test_mark_source_edges_extractor_coverage_legacy_complete_with_no_edges_not_trusted() -> (
    None
):
    # Codex review, PR #555: coverage["fact_family_states"] predates
    # SourceAbiSurface.source_edges (ADR-038 C.8 vs. C.9), so a pre-C.9
    # source_abi.json can carry source_edges: "complete" while its
    # serializer had nowhere to persist the actual edges -- from_dict()
    # defaults the missing key to []. That must not read as confirmed-zero
    # coverage: it's a schema-version gap, not an "empty-confirmed" run.
    s = _sample_surface()
    s.coverage["fact_family_states"] = {"source_edges": "complete"}
    s.coverage["fact_set"] = _FULL_WALK_PRODUCER_FACT_SET
    assert s.source_edges == []  # the legacy-drop scenario
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert "call_graph" not in g.extractor_passes
    assert "type_graph" not in g.extractor_passes


def test_mark_source_edges_extractor_coverage_empty_confirmed_also_counts() -> None:
    s = _sample_surface()
    s.coverage["fact_family_states"] = {"source_edges": "empty-confirmed"}
    s.coverage["fact_set"] = _FULL_WALK_PRODUCER_FACT_SET
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert g.extractor_passes["call_graph"] is True


def test_mark_source_edges_extractor_coverage_skips_when_incomplete() -> None:
    s = _sample_surface()
    s.coverage["fact_family_states"] = {"source_edges": "partial"}
    s.coverage["fact_set"] = _FULL_WALK_PRODUCER_FACT_SET
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert "call_graph" not in g.extractor_passes
    assert "type_graph" not in g.extractor_passes


def test_mark_source_edges_extractor_coverage_handles_none_surface_and_malformed_states() -> (
    None
):
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, None)  # must not raise
    assert g.extractor_passes == {}

    s = _sample_surface()
    s.coverage["fact_family_states"] = "not-a-dict"
    s.coverage["fact_set"] = _FULL_WALK_PRODUCER_FACT_SET
    mark_source_edges_extractor_coverage(g, s)  # must not raise
    assert g.extractor_passes == {}


def test_mark_source_edges_extractor_coverage_degrades_when_family_states_missing() -> (
    None
):
    # Codex review, PR #555: a third-party/hand-edited surface (or a
    # pre-C.8 schema) can carry source_edges with no/malformed
    # fact_family_states at all. That must not read as "return unmarked" --
    # the exact same raw-edge-presence-fallback gap a known non-full-walk
    # producer has -- when source_edges actually folded real edges.
    s = _sample_surface()
    s.source_edges = [
        {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "confidence": "high"},
    ]
    assert "fact_family_states" not in s.coverage
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert "call_graph" not in g.extractor_passes
    assert "type_graph" not in g.extractor_passes
    assert g.degraded_passes["call_graph"] is True
    assert g.degraded_passes["type_graph"] is True

    # Malformed (non-dict) fact_family_states behaves identically.
    s2 = _sample_surface()
    s2.source_edges = [
        {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "confidence": "high"},
    ]
    s2.coverage["fact_family_states"] = "not-a-dict"
    g2 = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g2, s2)
    assert "call_graph" not in g2.extractor_passes
    assert g2.degraded_passes["call_graph"] is True
    assert g2.degraded_passes["type_graph"] is True


def test_mark_source_edges_extractor_coverage_not_trusted_for_plugin_producer() -> None:
    # Codex review, PR #555: the ADR-038 C.8 clang plugin's source_edges only
    # walks call/reference bodies for classify()-accepted (public-header)
    # functions and never emits DECL_HAS_TYPE for a typedef/variable's type --
    # aliasing it to full call_graph/type_graph trust would hide a genuinely
    # new dependency added inside a private helper's body.
    s = _sample_surface()
    s.source_edges = [
        {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "confidence": "high"},
    ]
    s.coverage["fact_family_states"] = {"source_edges": "complete"}
    s.coverage["fact_set"] = {"producer": "abicheck-clang-plugin"}
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert "call_graph" not in g.extractor_passes
    assert "type_graph" not in g.extractor_passes
    # Codex review: a non-full-walk producer that DID fold real edges must be
    # stamped degraded, not left entirely unmarked -- an unmarked pass falls
    # back to raw edge presence in _common_dependency_edge_kinds, which a
    # scoped producer's edges cannot safely vouch for a project-wide zero.
    assert g.degraded_passes["call_graph"] is True
    assert g.degraded_passes["type_graph"] is True


def test_mark_source_edges_extractor_coverage_not_trusted_when_producer_unknown() -> (
    None
):
    # A missing/disagreeing rolled-up fact_set (pre-C.8 producer, mixed pack)
    # must not be treated as "safe to assume the full-walk producer" -- the
    # gate requires a positive, unambiguous signal.
    s = _sample_surface()
    s.source_edges = [
        {"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b", "confidence": "high"},
    ]
    s.coverage["fact_family_states"] = {"source_edges": "complete"}
    assert "fact_set" not in s.coverage or not s.coverage.get("fact_set")
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert "call_graph" not in g.extractor_passes
    assert "type_graph" not in g.extractor_passes
    assert g.degraded_passes["call_graph"] is True
    assert g.degraded_passes["type_graph"] is True


def test_mark_source_edges_extractor_coverage_no_degraded_stamp_when_no_edges_folded() -> (
    None
):
    # A non-full-walk producer whose source_edges folded NOTHING (empty list
    # -- e.g. "partial"/"failed"/"unsupported" states, or a legacy-drop
    # surface) must not gain a spurious degraded stamp either -- there is
    # nothing here to distrust, and marking it would be noise.
    s = _sample_surface()
    s.coverage["fact_family_states"] = {"source_edges": "partial"}
    s.coverage["fact_set"] = {"producer": "abicheck-clang-plugin"}
    assert s.source_edges == []
    g = SourceGraphSummary()
    mark_source_edges_extractor_coverage(g, s)
    assert "call_graph" not in g.extractor_passes
    assert "call_graph" not in g.degraded_passes
    assert "type_graph" not in g.degraded_passes


def test_build_graph_without_surface_is_phase2_only() -> None:
    g = build_source_graph(_sample_build())
    assert not any(n.kind == "source_decl" for n in g.nodes)
    assert not any(e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" for e in g.edges)


def test_source_abi_round_trip_and_determinism() -> None:
    s = _sample_surface()
    g = build_source_graph(BuildEvidence(), source_abi=s)
    assert (
        SourceGraphSummary.from_dict(g.to_dict()).compute_graph_id()
        == g.compute_graph_id()
    )
    assert build_source_graph(BuildEvidence(), source_abi=s).graph_id == g.graph_id


# ── Phase 5: graph-derived risk findings (D6) ───────────────────────────────


def _surface_with(
    decls, mapping, *, generated_header=None, target="target://libfoo"
) -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id=target)
    for qn, path in decls:
        s.reachable_declarations.append(
            SourceEntity(
                id=qn,
                kind="function",
                qualified_name=qn,
                source_location=SourceLocation(
                    path=path, line=1, origin="PUBLIC_HEADER"
                ),
                visibility="public_header",
                confidence=LayerConfidence.HIGH,
            )
        )
    s.mappings["source_decl_to_binary_symbol"] = dict(mapping)
    return s


def _build_with_public_header(headers=("inc/foo.h",), generated=()) -> BuildEvidence:
    b = BuildEvidence(generated_files=list(generated))
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=list(headers),
            confidence=Confidence.HIGH,
        )
    )
    return b


def test_all_three_graph_kinds_are_risk() -> None:
    for k in (
        ChangeKind.PUBLIC_REACHABILITY_CHANGED,
        ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
        ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
    ):
        assert k in RISK_KINDS


def test_findings_mapping_changed_for_persisting_decl() -> None:
    b = _build_with_public_header()
    old = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"})
    )
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"})
    )
    findings = diff_source_graph_findings(old, new)
    assert len(findings) == 1
    c = findings[0]
    assert c.kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED
    assert c.old_value == "_Zb" and c.new_value == "_Zb2"
    # CLI audit finding: source_location should localize to the declaration's
    # actual declaring file, not the generic evidence-tier tag, when the
    # graph resolves one via a SOURCE_DECLARES edge (it does here).
    assert c.source_location == "inc/foo.h"


def test_findings_reachability_ignores_brand_new_or_removed_decls() -> None:
    # A decl id absent from the OTHER side entirely (not merely absent from
    # its public closure) is a brand-new/removed declaration, not a
    # persisting one whose reachability state changed. "Entering the
    # closure" is a trivial, expected consequence of being newly added —
    # nothing risky about a symbol being public from birth — and that event
    # is already reported (at the correct COMPATIBLE severity) by the
    # ordinary addition/removal findings elsewhere in the pipeline.
    b = _build_with_public_header()
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::gone", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::new", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::new") not in kinds_syms
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::gone") not in kinds_syms


def test_findings_reachability_fires_for_persisting_decl_crossing_boundary() -> None:
    # foo::b exists on BOTH sides (same identity, so the same "decl://foo::b"
    # node id) but is only linked to a public header on the new side — an
    # existing declaration crossing the public/private boundary, the
    # genuinely risk-worthy signal this finding exists for.
    b = _build_with_public_header()
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "")], {"foo::a": "_Za"}
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::b") in kinds_syms


def test_findings_reachability_fires_when_persisting_decl_leaves_closure() -> None:
    b = _build_with_public_header()
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "")], {"foo::a": "_Za"}
        ),
    )
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::b") in kinds_syms


def test_findings_empty_baseline_does_not_spam_reachability() -> None:
    # An empty old graph must not flag every new declaration as "entered".
    b = _build_with_public_header()
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    )
    findings = diff_source_graph_findings(SourceGraphSummary(), new)
    assert not any(c.kind == ChangeKind.PUBLIC_REACHABILITY_CHANGED for c in findings)


def test_findings_generated_header_reaches_public_api() -> None:
    # A public header that is also a generated file → reaches public API.
    old = build_source_graph(_build_with_public_header(headers=("inc/foo.h",)))
    new = build_source_graph(
        _build_with_public_header(
            headers=("inc/foo.h", "gen/config.h"), generated=("gen/config.h",)
        )
    )
    findings = diff_source_graph_findings(old, new)
    gen = [
        c for c in findings if c.kind == ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API
    ]
    assert len(gen) == 1
    assert "gen/config.h" in gen[0].symbol


def test_owner_unchanged_across_different_absolute_checkout_roots() -> None:
    # Two independent checkouts of the *same* tree (e.g. a benchmark's old/
    # new directories, or two separate CI job workspaces) share no absolute
    # root. The declaring file's path relative to its own tree is identical
    # ("inc/foo.h"/"inc/bar.h" in both), so this must NOT look like every
    # file moved just because the checkout root differs (regression for the
    # false positive this produced across most of examples/, since the
    # catalog's own v1/v2 fixture convention is exactly this shape).
    old = build_source_graph(
        _build_with_public_header(
            headers=("/old_root/inc/foo.h", "/old_root/inc/bar.h")
        ),
        source_abi=_surface_with(
            [("foo::a", "/old_root/inc/foo.h"), ("foo::c", "/old_root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(
            headers=("/new_root/inc/foo.h", "/new_root/inc/bar.h")
        ),
        source_abi=_surface_with(
            [("foo::a", "/new_root/inc/foo.h"), ("foo::c", "/new_root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    assert not any(
        c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED for c in findings
    )


def test_owner_changed_when_relative_path_actually_moves() -> None:
    # A genuine relocation *within* the same tree (same root, different
    # relative path) must still fire — only the checkout-root difference is
    # meant to be ignored, not a real declaration move.
    b = _build_with_public_header(
        headers=("/root/inc/foo.h", "/root/inc/bar.h", "/root/inc/baz.h"),
    )
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/baz.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    owner = [
        c for c in findings if c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED
    ]
    assert len(owner) == 1
    assert owner[0].symbol == "_Zc"
    # CLI audit finding: source_location should localize to the symbol's new
    # declaring file, not the generic evidence-tier tag -- this family always
    # has one on hand (it's the whole point of the finding).
    assert owner[0].source_location == "/root/inc/baz.h"


def test_owner_changed_when_sole_declaring_file_is_renamed_both_sides() -> None:
    # When every exported symbol on a side declares in the SAME file, the
    # common prefix spans the whole path including the filename. If
    # _common_prefix_len didn't reserve the filename segment, both sides
    # would strip down to an empty "scheme://" key and a same-shape rename
    # (foo.h -> bar.h on both sides) would be missed entirely.
    old = build_source_graph(
        _build_with_public_header(headers=("/root/inc/foo.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/foo.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(headers=("/root/inc/bar.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/inc/bar.h"), ("foo::c", "/root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    owner_syms = {
        c.symbol
        for c in findings
        if c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED
    }
    assert owner_syms == {"_Za", "_Zc"}


def test_owner_unchanged_when_one_side_single_file_other_multi_file() -> None:
    # Asymmetric shapes: old has every symbol in one file (so its own common
    # prefix would include the filename before the fix), new spreads them
    # across two files. Declarations didn't actually move, so nothing should
    # fire even though the two sides' "common prefix" lengths differ.
    old = build_source_graph(
        _build_with_public_header(headers=("/root/inc/foo.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/foo.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(headers=("/root2/inc/foo.h", "/root2/inc/bar.h")),
        source_abi=_surface_with(
            [("foo::a", "/root2/inc/foo.h"), ("foo::c", "/root2/inc/foo.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    assert not any(
        c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED for c in findings
    )


def test_owner_unchanged_when_only_one_persisting_symbol_declares() -> None:
    # A side with exactly ONE declaring file has no sibling entry to compute
    # a shared directory prefix against, so the "reserve the filename" rule
    # (case04-style) never engaged and the raw absolute path was compared —
    # "case03/old/lib.h" vs "case03/new/lib.h" looked like a real move for
    # any case whose only persisting exported symbol shares its header with
    # no other symbol (a single-symbol library, or a brand-new symbol added
    # alongside the one persisting symbol). Must fall back to basename-only
    # identity, same as the multi-symbol cases below it.
    old = build_source_graph(
        _build_with_public_header(headers=("/root/old/lib.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/old/lib.h")],
            {"foo::a": "_Za"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(headers=("/root/new/lib.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/new/lib.h"), ("foo::b", "/root/new/lib.h")],
            {"foo::a": "_Za", "foo::b": "_Zb"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    assert not any(
        c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED for c in findings
    )


def test_findings_identical_graphs_yield_nothing() -> None:
    b = _build_with_public_header()
    g = build_source_graph(
        b, source_abi=_surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    )
    assert diff_source_graph_findings(g, g) == []


def test_compare_graph_cli_surfaces_findings() -> None:
    # `graph compare` (deleted CLI command, ADR-043) was a thin wrapper over
    # `diff_source_graph`/`diff_source_graph_findings` — exercise those
    # directly; the L5 graph is now an internal consequence of `--depth
    # source` rather than a separate command.
    b = _build_with_public_header()
    old = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"})
    )
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"})
    )
    findings = diff_source_graph_findings(old, new)
    assert findings
    assert findings[0].kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED


# ── Finalize: build-option→symbol flow, include drift, localization ─────────


def test_build_option_reaches_public_symbol_edges_and_finding() -> None:
    def _build(flags):
        b = BuildEvidence()
        b.targets.append(
            Target(
                id="target://libfoo",
                public_headers=["inc/foo.h"],
                confidence=Confidence.HIGH,
            )
        )
        b.compile_units.append(
            CompileUnit(
                id="cu://foo",
                source="src/foo.cpp",
                target_id="target://libfoo",
                abi_relevant_flags=flags,
            )
        )
        return b

    surf = _surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    old = build_source_graph(_build(["-std=c++20"]), source_abi=surf)
    new = build_source_graph(
        _build(["-std=c++20", "-fvisibility=hidden"]), source_abi=surf
    )
    assert any(e.kind == "BUILD_OPTION_AFFECTS_SYMBOL" for e in new.edges)
    bo = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL
    ]
    assert len(bo) == 1
    assert "-fvisibility=hidden" in bo[0].symbol
    assert bo[0].source_location == f"[{EVIDENCE_TIER_L5}]"


def test_build_option_reaches_public_symbol_ignores_reused_flag_on_new_target() -> None:
    # A new target reusing a pre-existing flag must NOT raise the finding — that
    # is symbol-level churn, not flag drift (only a *new* flag is interesting).
    def _build(targets):
        b = BuildEvidence()
        for tid, hdr in targets:
            b.targets.append(
                Target(id=tid, public_headers=[hdr], confidence=Confidence.HIGH)
            )
            b.compile_units.append(
                CompileUnit(
                    id=f"cu://{tid}",
                    source=f"src/{tid}.cpp",
                    target_id=tid,
                    abi_relevant_flags=["-std=c++20"],
                )
            )
        return b

    old_surf = _surface_with(
        [("foo::a", "inc/foo.h")], {"foo::a": "_Za"}, target="target://foo"
    )
    new_surf = _surface_with(
        [("bar::b", "inc/bar.h")], {"bar::b": "_Zb"}, target="target://bar"
    )
    old = build_source_graph(
        _build([("target://foo", "inc/foo.h")]), source_abi=old_surf
    )
    new = build_source_graph(
        _build([("target://foo", "inc/foo.h"), ("target://bar", "inc/bar.h")]),
        source_abi=new_surf,
    )
    bo = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL
    ]
    # -std=c++20 already existed in the old graph → no flag-drift finding.
    assert bo == []


def test_include_graph_public_header_drift_finding() -> None:
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)
    # The old side must have *confirmed* include-graph coverage (a pass that
    # ran and genuinely found nothing) for its absence to be trusted evidence
    # — otherwise this is indistinguishable from an older snapshot that never
    # collected include data at all, and "entered" would be a coverage
    # artifact, not a real drift (Codex review; _include_graph_covered).
    old.extractor_passes["include_graph"] = True
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert len(inc) == 1
    assert inc[0].symbol == "inc/foo.h"


def test_include_graph_public_header_drift_suppressed_without_old_coverage() -> None:
    # The exact false-positive Codex flagged: an old snapshot with no
    # include-graph data at all (never collected, or clang unavailable) vs a
    # new one that has it must NOT report every header in the new side as
    # newly "entered" — that's a coverage artifact, not a real change.
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)  # no include data, no confirmed pass at all
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert inc == []


def test_include_graph_public_header_drift_suppressed_for_narrowed_new_side() -> None:
    # A narrowed new side (a PR/--since scan folding only the changed compile
    # units) only examined a subset of the project. It must not report public
    # headers outside that subset as having "left" the include graph just
    # because its narrowed pass has real, but partial, edges (Codex review;
    # _include_graph_fully_covered).
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h", "inc/bar.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    b.compile_units.append(
        CompileUnit(id="cu://bar", source="src/bar.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)
    augment_graph_with_includes(
        old, {"cu://foo": ["inc/foo.h"], "cu://bar": ["inc/bar.h"]}
    )
    old.finalize()
    # New side only re-examined src/foo.cpp (a narrowed PR-diff scan) — its
    # include graph genuinely has "inc/bar.h" missing, but only because that
    # TU was never walked, not because the header stopped being included.
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.narrowed_passes["include_graph"] = True
    new.narrowed_scope["include_graph"] = frozenset({"src/foo.cpp"})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert inc == []


def test_include_graph_public_header_drift_trusted_for_matching_narrowed_scope() -> (
    None
):
    # Two sides narrowed to the *identical* scope examined the exact same
    # compile units, so a header appearing in one but not the other within
    # that shared scope is real drift, not a coverage gap.
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)
    old.narrowed_passes["include_graph"] = True
    old.narrowed_scope["include_graph"] = frozenset({"src/foo.cpp"})
    old.finalize()
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.narrowed_passes["include_graph"] = True
    new.narrowed_scope["include_graph"] = frozenset({"src/foo.cpp"})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert len(inc) == 1
    assert inc[0].symbol == "inc/foo.h"


def test_localize_symbol_walks_the_graph() -> None:
    from abicheck.buildsource.source_graph import localize_symbol

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["include/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    g = build_source_graph(b, source_abi=_sample_surface())
    result = localize_symbol(g, "_ZN3foo3barEv")
    assert result["found"] is True
    assert "target://libfoo" in result["exported_by_targets"]
    assert "foo::bar" in result["source_declarations"]
    assert any("foo.h" in h for h in result["declared_in_headers"])


def test_localize_symbol_absent_returns_empty() -> None:
    from abicheck.buildsource.source_graph import localize_symbol

    result = localize_symbol(build_source_graph(BuildEvidence()), "_Zmissing")
    assert result["found"] is False
    assert result["exported_by_targets"] == []


def test_explain_finding_cli() -> None:
    # `graph explain` (deleted CLI command, ADR-043) was a thin wrapper over
    # `localize_symbol` (+ `_resolve_symbol_from_report` for --finding-id) —
    # exercise those directly.
    from abicheck.buildsource.source_graph import localize_symbol

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["include/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    g = build_source_graph(b, source_abi=_sample_surface())

    result = localize_symbol(g, "_ZN3foo3barEv")
    assert result["found"] is True
    assert "target://libfoo" in result["exported_by_targets"]
    assert "foo::bar" in result["source_declarations"]


def test_explain_finding_resolves_symbol_from_report(tmp_path) -> None:
    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"changes": [{"symbol": "_ZN3foo3barEv"}]}))

    assert _resolve_symbol_from_report(report, "0") == "_ZN3foo3barEv"


# The deleted `graph explain` command's "no --symbol and no resolvable
# --report/--finding-id" usage error (`test_explain_finding_requires_a_symbol`)
# was pure CLI-argument plumbing with no surviving entry point —
# `localize_symbol`/`_resolve_symbol_from_report` both already require a
# symbol string to be passed in, so there's nothing left to call directly for
# this scenario.


def test_resolve_symbol_from_report_variants(tmp_path) -> None:
    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "r.json"
    report.write_text(
        json.dumps(
            {
                "changes": [
                    {"symbol": "_ZN3foo3barEv"},
                    {"symbol": "_ZN3foo3bazEv"},
                ]
            }
        )
    )
    # index lookup
    assert _resolve_symbol_from_report(report, "1") == "_ZN3foo3bazEv"
    # substring match
    assert _resolve_symbol_from_report(report, "bar") == "_ZN3foo3barEv"
    # out-of-range index → empty
    assert _resolve_symbol_from_report(report, "9") == ""
    # no match → empty
    assert _resolve_symbol_from_report(report, "nope") == ""


def test_resolve_symbol_from_report_unreadable(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _resolve_symbol_from_report

    with pytest.raises(click.ClickException):
        _resolve_symbol_from_report(tmp_path / "missing.json", "0")


def test_resolve_symbol_from_report_non_object(tmp_path) -> None:
    # A valid-but-non-object report (a bare JSON list) must raise a Click error,
    # not an unhandled AttributeError from `.get(...)`.
    import click
    import pytest

    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "list.json"
    report.write_text(json.dumps([{"symbol": "_Zx"}]))
    with pytest.raises(click.ClickException, match="must contain a JSON object"):
        _resolve_symbol_from_report(report, "0")


def test_resolve_symbol_from_report_non_list_changes(tmp_path) -> None:
    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "r.json"
    report.write_text(json.dumps({"changes": "not-a-list"}))
    assert _resolve_symbol_from_report(report, "0") == ""


def test_explain_finding_text_symbol_absent() -> None:
    # `graph explain`'s text-mode "not present" notice was just a `found`-flag
    # check on `localize_symbol`'s result — covered directly by
    # `test_localize_symbol_absent_returns_empty` above (`result["found"] is
    # False`); no CLI-level replacement needed.
    from abicheck.buildsource.source_graph import localize_symbol

    result = localize_symbol(build_source_graph(BuildEvidence()), "_Zmissing")
    assert result["found"] is False


def test_load_source_graph_invalid_pack_dir(tmp_path) -> None:
    # A directory that is not a valid evidence pack yields an actionable error.
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    with pytest.raises(click.ClickException):
        _load_source_graph(tmp_path)


def test_graph_helpers_backcompat_reexport_from_cli_buildsource() -> None:
    """The helpers moved to ``cli_graph`` when the graph group was extracted, but
    the historical ``from abicheck.cli_buildsource import _load_source_graph``
    path stays alive via a lazy ``__getattr__`` shim (no import cycle). Pin it so
    the back-compat surface can't silently regress; an unknown attr still raises.
    """
    import pytest

    from abicheck import cli_buildsource, cli_graph

    assert cli_buildsource._load_source_graph is cli_graph._load_source_graph
    assert (
        cli_buildsource._resolve_symbol_from_report
        is cli_graph._resolve_symbol_from_report
    )
    with pytest.raises(AttributeError):
        cli_buildsource._definitely_not_a_real_attr


# ── Phase 1: schema round-trip + content addressing ─────────────────────────


def test_round_trip_preserves_graph_id() -> None:
    g = build_source_graph(_sample_build())
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.compute_graph_id() == g.compute_graph_id()
    assert len(restored.nodes) == len(g.nodes)
    assert len(restored.edges) == len(g.edges)


def test_extractor_passes_round_trips() -> None:
    # ADR-041 P0 slice 2 follow-up: extractor_passes must survive to_dict/
    # from_dict so a version diff loaded from a pack can still tell "the pass
    # ran, zero edges" from "the pass never ran".
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.extractor_passes["type_graph"] = True
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.extractor_passes == {"type_graph": True}


def test_narrowed_passes_round_trips() -> None:
    # Eleventh Codex review: narrowed_passes must survive to_dict/from_dict so
    # a version diff loaded from a pack can still tell a narrowed (PR/--since
    # -scoped) pass's edges from a confirmed full pass's.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.narrowed_passes["type_graph"] = True
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.narrowed_passes == {"type_graph": True}
    assert restored.extractor_passes == {}


def test_narrowed_scope_round_trips() -> None:
    # Fourteenth Codex review: narrowed_scope must survive to_dict/from_dict so
    # a version diff loaded from a pack can still tell "narrowed to the same
    # TUs" from "narrowed but to different, disjoint code".
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.narrowed_passes["type_graph"] = True
    g.narrowed_scope["type_graph"] = frozenset({"src/a.cpp", "src/b.cpp"})
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.narrowed_scope == {
        "type_graph": frozenset({"src/a.cpp", "src/b.cpp"})
    }


def test_degraded_passes_round_trips() -> None:
    # Sixteenth Codex review: degraded_passes must survive to_dict/from_dict so
    # a version diff loaded from a pack can still tell "ran unnarrowed but hit
    # per-TU diagnostics" from a clean confirmed pass.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.degraded_passes["type_graph"] = True
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.degraded_passes == {"type_graph": True}
    assert restored.extractor_passes == {}
    assert restored.narrowed_passes == {}


def test_graph_id_order_independent() -> None:
    a = SourceGraphSummary()
    a.add_node(GraphNode(id="x", kind="target"))
    a.add_node(GraphNode(id="y", kind="source"))
    a.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    b = SourceGraphSummary()
    b.add_node(GraphNode(id="y", kind="source"))
    b.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    b.add_node(GraphNode(id="x", kind="target"))
    assert a.compute_graph_id() == b.compute_graph_id()


def test_add_node_and_edge_dedupe() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.add_node(GraphNode(id="x", kind="target"))
    g.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    g.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    assert len(g.nodes) == 1
    assert len(g.edges) == 1


def test_from_dict_forward_compatible_with_unknown_fields() -> None:
    # A hand-edited / newer summary with an unknown node kind and extra keys
    # must load, not abort (evidence/CLAUDE.md forward-compat rule).
    data = {
        "schema_version": SOURCE_GRAPH_VERSION + 99,
        "nodes": [{"id": "n1", "kind": "future_kind", "future_attr": 1}],
        "edges": [{"edge": "FUTURE_EDGE", "src": "n1", "dst": "n2"}],
        "unknown_top_level": True,
    }
    g = SourceGraphSummary.from_dict(data)
    assert g.nodes[0].kind == "future_kind"
    assert g.edges[0].kind == "FUTURE_EDGE"


def test_indexes_localize_by_target_and_file() -> None:
    g = build_source_graph(_sample_build())
    idx = g.to_dict()["indexes"]
    assert "target://libfoo" in idx["by_target"]
    assert any(k.startswith("header://") for k in idx["by_file"])


def test_indexes_cover_forward_looking_symbol_and_decl_kinds() -> None:
    # Phases 3-4 will emit binary_symbol / source_decl nodes; the index already
    # localizes by them so a finding can be traced once those land.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://foo", kind="source_decl"))
    g.add_node(GraphNode(id="sym://_Z3foov", kind="binary_symbol"))
    g.add_edge(
        GraphEdge(
            src="decl://foo", dst="sym://_Z3foov", kind="SOURCE_DECL_MAPS_TO_SYMBOL"
        )
    )
    idx = g.indexes()
    assert "sym://_Z3foov" in idx["by_binary_symbol"]
    assert "decl://foo" in idx["by_source_decl"]


def test_to_dict_fills_graph_id_when_unset() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    assert g.graph_id == ""  # not finalized
    assert g.to_dict()["graph_id"].startswith("sha256:")


# ── Phase 5 seed: structural diff ───────────────────────────────────────────


def test_diff_detects_added_and_removed() -> None:
    old = build_source_graph(_sample_build())
    b2 = _sample_build()
    b2.targets.append(Target(id="target://libbaz", name="baz"))
    new = build_source_graph(b2)
    delta = diff_source_graph(old, new)
    assert delta.changed
    assert any(n.id == "target://libbaz" for n in delta.added_nodes)
    assert not delta.removed_nodes


def test_diff_identical_graphs_no_change() -> None:
    g = build_source_graph(_sample_build())
    delta = diff_source_graph(g, g)
    assert not delta.changed
    assert delta.to_dict()["counts"]["added_nodes"] == 0


# ── Pack + CLI wiring ───────────────────────────────────────────────────────


def test_pack_round_trips_source_graph(tmp_path) -> None:
    pack = BuildSourcePack.empty(tmp_path / "p.evidence")
    pack.source_graph = build_source_graph(_sample_build())
    pack.write()
    loaded = BuildSourcePack.load(tmp_path / "p.evidence")
    assert loaded.source_graph is not None
    assert loaded.source_graph.graph_id == pack.source_graph.graph_id


def test_pack_drops_stale_graph_when_recollected(tmp_path) -> None:
    root = tmp_path / "p.evidence"
    pack = BuildSourcePack.empty(root)
    pack.source_graph = build_source_graph(_sample_build())
    pack.write()
    # Re-write without a graph: the stale file must be removed.
    pack2 = BuildSourcePack.load(root)
    pack2.source_graph = None
    pack2.write()
    assert not (root / "graph" / "source_graph_summary.json").is_file()
    assert BuildSourcePack.load(root).source_graph is None


def _collect_graph_pack(
    tmp_path, name: str, *, two_units: bool = False, source_graph: str = "summary"
):
    """Build a BuildSourcePack the way the deleted `collect --compile-db ...
    --source-graph summary -o <dir>` command used to, via the still-live
    `_run_adapters`/`_collect_source_graph`/`_build_coverage` engine functions
    (orphaned from any CLI command but otherwise unchanged, ADR-043)."""
    import datetime as _dt

    from abicheck import __version__ as _abicheck_version
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.cli_buildsource_helpers import (
        _build_coverage,
        _collect_source_graph,
        _run_adapters,
    )

    src = tmp_path / f"{name}.cpp"
    src.write_text("int x(){return 1;}\n")
    entries = [
        {
            "directory": str(tmp_path),
            "file": str(src),
            "command": f"c++ -std=c++20 -fvisibility=hidden -c {src} -o {name}.o",
        }
    ]
    if two_units:
        src2 = tmp_path / f"{name}2.cpp"
        src2.write_text("int y(){return 2;}\n")
        entries.append(
            {
                "directory": str(tmp_path),
                "file": str(src2),
                "command": f"c++ -std=c++20 -c {src2} -o {name}2.o",
            }
        )
    cdb = tmp_path / f"{name}_cc.json"
    cdb.write_text(json.dumps(entries))

    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    _run_adapters(
        merged,
        extractors,
        compile_db=cdb,
        build_dir=None,
        cmake=False,
        ninja=False,
        ninja_compdb=None,
        bazel_cquery=None,
        bazel_aquery=None,
        make_dry_run=None,
        binary=None,
        read_compiler_record=False,
        build_system="generic",
        record_bazel_inputs=False,
        verbose=False,
    )
    has_build = bool(merged.compile_units or merged.targets)
    graph, graph_detail = _collect_source_graph(
        merged,
        extractors,
        source_graph=source_graph,
        changed_paths=(),
        kythe_entries=None,
        codeql_results=None,
        codeql_extends_results=None,
        surface=None,
        clang_bin="clang",
    )

    out = tmp_path / f"{name}.evidence"
    pack = BuildSourcePack.empty(
        out,
        abicheck_version=_abicheck_version,
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    pack.manifest.extractors = extractors
    if has_build:
        pack.build_evidence = merged
    if graph is not None:
        pack.source_graph = graph
    pack.manifest.coverage = _build_coverage(
        merged, has_build, None, "", graph, graph_detail
    )
    pack.write()
    return pack, out


def test_collect_evidence_summary_writes_graph_and_coverage(tmp_path) -> None:
    pack, out = _collect_graph_pack(tmp_path, "foo")
    assert (out / "graph" / "source_graph_summary.json").is_file()
    reloaded = BuildSourcePack.load(out)
    assert reloaded.source_graph is not None
    l5 = reloaded.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH)
    assert l5 is not None
    assert l5.status == CoverageStatus.PRESENT


def test_compare_graph_cli_reports_diff() -> None:
    # `graph compare` (deleted CLI command) was a thin wrapper over
    # `diff_source_graph` — exercise it directly.
    old = SourceGraphSummary()
    old.add_node(GraphNode(id="target://a", kind="target", label="a"))
    new = build_source_graph(_sample_build())

    delta = diff_source_graph(old, new)
    assert delta.changed
    assert len(delta.added_nodes) >= 1


def test_compare_graph_identical() -> None:
    g = build_source_graph(_sample_build())
    delta = diff_source_graph(g, g)
    assert not delta.changed


def test_compare_graph_missing_graph_errors(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    with pytest.raises(click.ClickException):
        _load_source_graph(tmp_path / "nope.json")


def test_compare_graph_accepts_pack_directories_and_shows_removals(tmp_path) -> None:
    # The richer pack as OLD and the smaller as NEW exercises the removed-node /
    # removed-edge branches of the structural diff.
    from abicheck.cli_graph import _load_source_graph

    big_pack, big_dir = _collect_graph_pack(tmp_path, "big", two_units=True)
    small_pack, small_dir = _collect_graph_pack(tmp_path, "small", two_units=False)
    old_graph = _load_source_graph(big_dir)
    new_graph = _load_source_graph(small_dir)
    delta = diff_source_graph(old_graph, new_graph)
    assert delta.removed_nodes or delta.removed_edges


def test_compare_graph_pack_without_graph_errors(tmp_path) -> None:
    # A pack collected without a source graph has no L5 graph → actionable error.
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    _pack, out = _collect_graph_pack(tmp_path, "nograph", source_graph="off")
    with pytest.raises(click.ClickException, match="no L5 source graph"):
        _load_source_graph(out)


def test_compare_graph_malformed_json_errors(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(click.ClickException, match="Cannot read source graph"):
        _load_source_graph(bad)


def test_compare_graph_non_object_json_errors(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]")
    with pytest.raises(click.ClickException, match="must contain a JSON object"):
        _load_source_graph(arr)


def test_compare_graph_rejects_non_graph_json_object(tmp_path) -> None:
    # An unrelated JSON object (e.g. a pack manifest) must fail with an
    # actionable error, not be read as an empty graph (CodeRabbit review).
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"build_source_pack_version": 1, "coverage": []}))
    with pytest.raises(click.ClickException, match="not a source graph summary"):
        _load_source_graph(manifest)


def test_collect_evidence_summary_without_build_is_partial(tmp_path) -> None:
    # --source-graph summary with no build adapter inputs yields an empty graph;
    # the L5 coverage row must read PARTIAL (ran, produced nothing), not PRESENT.
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.cli_buildsource_helpers import _build_coverage, _collect_source_graph

    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    graph, graph_detail = _collect_source_graph(
        merged,
        extractors,
        source_graph="summary",
        changed_paths=(),
        kythe_entries=None,
        codeql_results=None,
        codeql_extends_results=None,
        surface=None,
        clang_bin="clang",
    )
    coverage = _build_coverage(merged, False, None, "", graph, graph_detail)
    l5 = next(c for c in coverage if c.layer == DataLayer.L5_SOURCE_GRAPH.value)
    assert l5.status == CoverageStatus.PARTIAL
