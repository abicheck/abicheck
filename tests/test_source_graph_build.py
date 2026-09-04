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

"""Tests for ADR-061 Phase 5 item 2's construction half: build_source_graph()
and its private helpers (folding ADR-029 BuildEvidence + an optional ADR-030
SourceAbiSurface into a SourceGraphSummary), split out of test_source_graph.py
along with the production module split (source_graph_build.py)."""

from __future__ import annotations

from abicheck.buildsource.build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    LinkUnit,
    Target,
    TargetKind,
)
from abicheck.buildsource.model import LayerConfidence
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph_build import build_source_graph
from abicheck.buildsource.source_graph_build_source_abi import (
    fold_source_edges,
    mark_source_edges_extractor_coverage,
)
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import EDGE_KINDS, NODE_KINDS, SourceGraphSummary


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
