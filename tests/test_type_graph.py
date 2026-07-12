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

"""Tests for ADR-041 P0: the Clang type/reference AST parser, graph
augmentation, and graceful clang-absent degrade.

The parser is exercised against hand-built ``clang -ast-dump=json`` trees so
no compiler is required; the live subprocess path is integration-only."""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.buildsource.type_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    ClangTypeGraphExtractor,
    TypeEdge,
    _base_type_name,
    _merge_type_edges,
    augment_graph_with_types,
    parse_clang_ast_types,
)


def _field(name: str, qual_type: str) -> dict:
    return {"kind": "FieldDecl", "name": name, "type": {"qualType": qual_type}}


def _record(
    name: str, *, bases: list[dict] | None = None, inner: list[dict] | None = None
) -> dict:
    d: dict = {"kind": "CXXRecordDecl", "name": name, "inner": inner or []}
    if bases is not None:
        d["bases"] = bases
    return d


def _base(qual_type: str) -> dict:
    return {"type": {"qualType": qual_type}, "writtenAccess": "public"}


def _param(name: str, qual_type: str) -> dict:
    return {"kind": "ParmVarDecl", "name": name, "type": {"qualType": qual_type}}


def _method(
    name: str, mangled: str, params: list[dict], body_inner: list[dict] | None = None
) -> dict:
    return {
        "kind": "CXXMethodDecl",
        "name": name,
        "mangledName": mangled,
        "inner": [*params, {"kind": "CompoundStmt", "inner": body_inner or []}],
    }


def _ref_expr(kind: str, name: str, mangled: str = "") -> dict:
    ref: dict = {"kind": kind, "name": name}
    if mangled:
        ref["mangledName"] = mangled
    return {"kind": "DeclRefExpr", "referencedDecl": ref}


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


# ── _base_type_name ──────────────────────────────────────────────────────────


def test_base_type_name_strips_pointer_and_cv() -> None:
    assert _base_type_name("const detail::Impl *") == "detail::Impl"
    assert _base_type_name("ns::Widget &") == "ns::Widget"
    assert _base_type_name("ns::Widget &&") == "ns::Widget"
    assert _base_type_name("struct Foo") == "Foo"
    assert _base_type_name("int") == "int"
    assert _base_type_name("int[4]") == "int"
    assert _base_type_name("") == ""


# ── parse_clang_ast_types ─────────────────────────────────────────────────────


def test_base_class_edge() -> None:
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                _record("Base"),
                _record("Derived", bases=[_base("ns::Base")]),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    inherits = [e for e in edges if e.kind == "TYPE_INHERITS"]
    assert inherits == [
        TypeEdge("ns::Derived", "ns::Base", "TYPE_INHERITS", CONF_HIGH, "base")
    ]


def test_unqualified_base_and_field_types_resolve_to_enclosing_namespace() -> None:
    # clang's qualType is the *written* spelling, not fully qualified: a field
    # typed `Base` inside `namespace ns { struct Widget { Base *p; }; }` prints
    # as "Base", not "ns::Base" — resolve it against the enclosing scope so the
    # edge joins the L4-derived `type://ns::Base` node instead of creating a
    # disconnected `type://Base` (Codex review).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                _record("Base"),
                _record(
                    "Widget",
                    bases=[_base("Base")],
                    inner=[_field("p", "Base *")],
                ),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    inherits = [e for e in edges if e.kind == "TYPE_INHERITS"]
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert inherits[0].dst == "ns::Base"
    assert inherits[0].confidence == CONF_HIGH
    assert fields[0].dst == "ns::Base"
    assert fields[0].confidence == CONF_HIGH


def test_unresolvable_unqualified_type_stays_bare_with_reduced_confidence() -> None:
    # No declaration for "Unknown" anywhere in the TU -> cannot be resolved;
    # the edge is kept (best-effort) but flagged lower-confidence rather than
    # silently claiming a full qualification it doesn't have.
    ast = _tu(_record("Widget", inner=[_field("p", "Unknown *")]))
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge("Widget", "Unknown", "TYPE_HAS_FIELD_TYPE", CONF_REDUCED, "field")
    ]


def test_global_scope_match_stays_high_confidence() -> None:
    # `_resolve_type_name("Base", [], ...)` resolves to "Base" unchanged
    # because Base is declared at global scope — the resolved spelling
    # equals the raw spelling not because resolution failed, but because
    # there was no namespace to add. A naive string-equality confidence
    # check would misread this as "unresolved" (Codex review).
    ast = _tu(_record("Base"), _record("Widget", bases=[_base("Base")]))
    edges = parse_clang_ast_types(ast)
    inherits = [e for e in edges if e.kind == "TYPE_INHERITS"]
    assert inherits == [TypeEdge("Widget", "Base", "TYPE_INHERITS", CONF_HIGH, "base")]


def test_field_type_resolves_against_own_record_scope() -> None:
    # A field naming a type nested in the *same* record (Outer::Inner
    # referenced as bare "Inner" from inside Outer) must resolve against the
    # record's own scope, not just the enclosing one (Codex review).
    ast = _tu(
        _record(
            "Outer",
            inner=[
                _record("Inner"),
                _field("x", "Inner"),
            ],
        )
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge("Outer", "Outer::Inner", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
    ]


def test_dst_file_resolved_even_when_type_declared_after_use() -> None:
    # The private type's own CXXRecordDecl appears *after* the field that
    # references it in this TU — the two-pass design (a full indexing pass
    # runs before any edge is built) must still resolve dst_file regardless
    # of declaration order.
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                _record("Widget", inner=[_field("p", "Impl *")]),
                {
                    "kind": "CXXRecordDecl",
                    "name": "Impl",
                    "loc": {"file": "src/detail/impl.h"},
                    "inner": [_field("x", "int")],
                },
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields[0].dst == "ns::Impl"
    assert fields[0].dst_file == "src/detail/impl.h"


def test_private_enum_field_type_is_indexed_and_qualified() -> None:
    # A field/param typed with a private *enum* (not a record) previously fell
    # through un-indexed: qualType prints the bare "Mode", and nothing tracked
    # its declaring file, so the edge carried no provenance (Codex review).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [
                {
                    "kind": "EnumDecl",
                    "name": "Mode",
                    "loc": {"file": "src/detail/mode.h"},
                    "inner": [],
                },
                _record("Widget", inner=[_field("m", "Mode")]),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge(
            "detail::Widget",
            "detail::Mode",
            "TYPE_HAS_FIELD_TYPE",
            CONF_HIGH,
            "field",
            "src/detail/mode.h",
        )
    ]


def test_private_typedef_param_type_is_indexed_and_qualified() -> None:
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [
                {
                    "kind": "TypedefDecl",
                    "name": "Handle",
                    "loc": {"file": "src/detail/handle.h"},
                    "inner": [],
                },
                _record(
                    "Widget",
                    inner=[_method("bar", "_ZN6Widget3barE", [_param("h", "Handle")])],
                ),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    params = [e for e in edges if e.kind == "DECL_HAS_TYPE"]
    assert params == [
        TypeEdge(
            "_ZN6Widget3barE",
            "detail::Handle",
            "DECL_HAS_TYPE",
            CONF_HIGH,
            "param",
            "src/detail/handle.h",
        )
    ]


def test_incomplete_declrefexpr_stub_resolves_to_full_declaration() -> None:
    # The PR's own headline scenario: `inline int f() { return detail::k; }`.
    # clang commonly emits an *incomplete* referencedDecl stub — no
    # mangledName/loc — even though the full VarDecl elsewhere in the TU
    # carries both. Keying the edge off the stub's bare identity ("k") means
    # it never matches the indexed declaration, so dst_file/defined_in_project
    # provenance was silently lost (Codex review).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "k",
                    "mangledName": "_ZN6detail1kE",
                    "loc": {"file": "src/detail/constants.h"},
                    "inner": [],
                }
            ],
        },
        {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": "_Z1fv",
            "inner": [
                {
                    "kind": "CompoundStmt",
                    "inner": [_ref_expr("VarDecl", "k")],  # incomplete stub
                }
            ],
        },
    )
    edges = parse_clang_ast_types(ast)
    refs = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert refs == [
        TypeEdge(
            "_Z1fv",
            "_ZN6detail1kE",
            "DECL_REFERENCES_DECL",
            CONF_REDUCED,
            "ref",
            "src/detail/constants.h",
        )
    ]


def test_ambiguous_declrefexpr_stub_keeps_original_identity() -> None:
    # Two different variables share the bare name "k" in different scopes;
    # an incomplete stub referencing one of them cannot be disambiguated by
    # bare name alone, so the resolver must not guess — it keeps the stub's
    # own (unresolved) identity rather than picking either candidate.
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "a",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "k",
                    "mangledName": "_ZN1a1kE",
                    "loc": {"file": "src/a.h"},
                    "inner": [],
                }
            ],
        },
        {
            "kind": "NamespaceDecl",
            "name": "b",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "k",
                    "mangledName": "_ZN1b1kE",
                    "loc": {"file": "src/b.h"},
                    "inner": [],
                }
            ],
        },
        {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": "_Z1fv",
            "inner": [{"kind": "CompoundStmt", "inner": [_ref_expr("VarDecl", "k")]}],
        },
    )
    edges = parse_clang_ast_types(ast)
    refs = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert refs == [
        TypeEdge("_Z1fv", "k", "DECL_REFERENCES_DECL", CONF_REDUCED, "ref", "")
    ]


def test_field_type_edge_excludes_builtins() -> None:
    ast = _tu(
        _record(
            "Widget",
            inner=[
                _field("p", "detail::Impl *"),
                _field("count", "int"),
            ],
        )
    )
    edges = parse_clang_ast_types(ast)
    field_edges = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert field_edges == [
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
    ]


def test_param_type_edge() -> None:
    ast = _tu(
        _record(
            "Widget",
            inner=[_method("bar", "_ZN6Widget3barE", [_param("x", "detail::Config")])],
        )
    )
    edges = parse_clang_ast_types(ast)
    param_edges = [e for e in edges if e.kind == "DECL_HAS_TYPE"]
    assert param_edges == [
        TypeEdge(
            "_ZN6Widget3barE", "detail::Config", "DECL_HAS_TYPE", CONF_HIGH, "param"
        )
    ]


def test_body_reference_edge_for_variable() -> None:
    ast = _tu(
        _record(
            "Widget",
            inner=[
                _method(
                    "bar",
                    "_ZN6Widget3barE",
                    [],
                    body_inner=[_ref_expr("VarDecl", "g_counter", "_ZL9g_counter")],
                )
            ],
        )
    )
    edges = parse_clang_ast_types(ast)
    ref_edges = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert ref_edges == [
        TypeEdge(
            "_ZN6Widget3barE",
            "_ZL9g_counter",
            "DECL_REFERENCES_DECL",
            CONF_REDUCED,
            "ref",
        )
    ]


def test_body_reference_ignores_function_call_targets() -> None:
    # A DeclRefExpr referencing a FunctionDecl is the call graph's job
    # (DECL_CALLS_DECL), not this module's — must not double-emit as a reference.
    ast = _tu(
        _record(
            "Widget",
            inner=[
                _method(
                    "bar",
                    "_ZN6Widget3barE",
                    [],
                    body_inner=[_ref_expr("FunctionDecl", "helper", "_Z6helperv")],
                )
            ],
        )
    )
    edges = parse_clang_ast_types(ast)
    assert not any(e.kind == "DECL_REFERENCES_DECL" for e in edges)


def test_dedup_by_src_dst_kind() -> None:
    ast = _tu(
        _record(
            "Widget",
            inner=[
                _field("p", "detail::Impl *"),
                _field("q", "detail::Impl &"),  # same base type -> same edge
            ],
        )
    )
    edges = parse_clang_ast_types(ast)
    field_edges = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert len(field_edges) == 1


def test_nested_namespace_qualifies_names() -> None:
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "outer",
            "inner": [
                {
                    "kind": "NamespaceDecl",
                    "name": "inner",
                    "inner": [
                        _record("Base"),
                        _record("Widget", bases=[_base("outer::inner::Base")]),
                    ],
                },
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    inherits = [e for e in edges if e.kind == "TYPE_INHERITS"]
    assert inherits[0].src == "outer::inner::Widget"
    assert inherits[0].dst == "outer::inner::Base"


def test_non_dict_and_empty_ast_produce_no_edges() -> None:
    assert parse_clang_ast_types({}) == []
    assert (
        parse_clang_ast_types({"kind": "TranslationUnitDecl", "inner": [None, 42, "x"]})
        == []
    )


# ── augment_graph_with_types ─────────────────────────────────────────────────


def test_augment_graph_with_types_adds_nodes_and_edges() -> None:
    graph = SourceGraphSummary()
    edges = [
        TypeEdge("ns::Widget", "ns::Base", "TYPE_INHERITS", CONF_HIGH, "base"),
        TypeEdge(
            "ns::Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field"
        ),
        TypeEdge(
            "_ZN2ns6Widget3barEi", "detail::Config", "DECL_HAS_TYPE", CONF_HIGH, "param"
        ),
        TypeEdge(
            "_ZN2ns6Widget3barEi",
            "_ZN2ns10g_counterE",
            "DECL_REFERENCES_DECL",
            CONF_REDUCED,
            "ref",
        ),
    ]
    added = augment_graph_with_types(graph, edges)
    assert added == 4
    kinds = {e.kind for e in graph.edges}
    assert kinds == {
        "TYPE_INHERITS",
        "TYPE_HAS_FIELD_TYPE",
        "DECL_HAS_TYPE",
        "DECL_REFERENCES_DECL",
    }
    node_kinds = {n.id: n.kind for n in graph.nodes}
    assert node_kinds["type://ns::Widget"] == "record_type"
    assert node_kinds["type://detail::Impl"] == "record_type"
    assert node_kinds["decl://_ZN2ns6Widget3barEi"] == "source_decl"
    assert node_kinds["decl://_ZN2ns10g_counterE"] == "source_decl"


def test_augment_graph_marks_dst_defined_in_project() -> None:
    # The main case this module exists for: a public type field/base/reference
    # reaching a *private* entity that L4 never surfaced (L4 only captures the
    # public-reachable surface). Without a defined_in_project marker the new
    # node is unannotated and public_to_internal_dependency cannot classify it
    # as internal (Codex review).
    graph = SourceGraphSummary()
    edges = [
        TypeEdge(
            "ns::Widget",
            "ns::detail::Impl",
            "TYPE_HAS_FIELD_TYPE",
            CONF_HIGH,
            "field",
            dst_file="src/detail/impl.h",
        ),
    ]
    project_files = frozenset({"src/detail/impl.h"})
    augment_graph_with_types(graph, edges, project_files)
    node = next(n for n in graph.nodes if n.id == "type://ns::detail::Impl")
    assert node.attrs.get("defined_in_project") is True
    assert node.attrs.get("def_file") == "src/detail/impl.h"


def test_augment_graph_does_not_mark_non_project_dst() -> None:
    graph = SourceGraphSummary()
    edges = [
        TypeEdge(
            "ns::Widget",
            "std::vector",
            "TYPE_HAS_FIELD_TYPE",
            CONF_HIGH,
            "field",
            dst_file="/usr/include/c++/vector",
        ),
    ]
    project_files = frozenset({"src/detail/impl.h"})
    augment_graph_with_types(graph, edges, project_files)
    node = next(n for n in graph.nodes if n.id == "type://std::vector")
    assert "defined_in_project" not in node.attrs


def test_augment_graph_with_types_dedupes_by_key() -> None:
    graph = SourceGraphSummary()
    edge = TypeEdge("ns::Widget", "ns::Base", "TYPE_INHERITS", CONF_HIGH, "base")
    assert augment_graph_with_types(graph, [edge]) == 1
    assert augment_graph_with_types(graph, [edge]) == 0
    assert len(graph.edges) == 1


def test_augment_graph_preserves_existing_richer_node() -> None:
    # A node already folded from L4 (e.g. with real visibility) must not be
    # clobbered by the AST-only pass (first-writer-wins, per add_node).
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(
            id="type://ns::Widget",
            kind="record_type",
            label="ns::Widget",
            provenance="source_abi",
            confidence=CONF_HIGH,
            attrs={"visibility": "public_header"},
        )
    )
    edges = [TypeEdge("ns::Widget", "ns::Base", "TYPE_INHERITS", CONF_HIGH, "base")]
    augment_graph_with_types(graph, edges)
    node = next(n for n in graph.nodes if n.id == "type://ns::Widget")
    assert node.attrs.get("visibility") == "public_header"
    assert node.provenance == "source_abi"


def test_source_graph_coverage_reports_type_and_reference_edges() -> None:
    graph = SourceGraphSummary()
    graph.add_edge(GraphEdge(src="a", dst="b", kind="TYPE_INHERITS"))
    graph.add_edge(GraphEdge(src="a", dst="c", kind="DECL_REFERENCES_DECL"))
    graph.finalize()
    assert graph.coverage["type_edges"]["collected"] is True
    assert graph.coverage["type_edges"]["count"] == 1
    assert graph.coverage["reference_edges"]["collected"] is True
    assert graph.coverage["reference_edges"]["count"] == 1


def test_source_graph_coverage_honest_when_no_type_edges() -> None:
    graph = SourceGraphSummary()
    graph.finalize()
    assert graph.coverage["type_edges"]["collected"] is False
    assert graph.coverage["reference_edges"]["collected"] is False


# ── _merge_type_edges ─────────────────────────────────────────────────────────


def test_merge_type_edges_prefers_stronger_confidence() -> None:
    weak = TypeEdge(
        "Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_REDUCED, "field"
    )
    strong = TypeEdge(
        "Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field"
    )
    assert _merge_type_edges(weak, strong).confidence == CONF_HIGH
    assert _merge_type_edges(strong, weak).confidence == CONF_HIGH


def test_merge_type_edges_fills_missing_dst_file() -> None:
    # One TU doesn't include the header declaring the private dst (no
    # dst_file); another TU does. The richer provenance must survive the
    # merge regardless of which TU is seen first (Codex review).
    no_file = TypeEdge(
        "Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field", ""
    )
    with_file = TypeEdge(
        "Widget",
        "detail::Impl",
        "TYPE_HAS_FIELD_TYPE",
        CONF_HIGH,
        "field",
        "src/detail/impl.h",
    )
    assert _merge_type_edges(no_file, with_file).dst_file == "src/detail/impl.h"
    assert _merge_type_edges(with_file, no_file).dst_file == "src/detail/impl.h"


def test_extract_from_build_merges_richer_edge_across_compile_units(
    monkeypatch,
) -> None:
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)

    def _fake_extract(cu: CompileUnit) -> list[TypeEdge]:
        if cu.source == "src/a.cpp":
            # This TU doesn't see detail::Impl's declaration.
            return [
                TypeEdge(
                    "Widget",
                    "detail::Impl",
                    "TYPE_HAS_FIELD_TYPE",
                    CONF_HIGH,
                    "field",
                    "",
                )
            ]
        # This TU includes the private header and resolves the file.
        return [
            TypeEdge(
                "Widget",
                "detail::Impl",
                "TYPE_HAS_FIELD_TYPE",
                CONF_HIGH,
                "field",
                "src/detail/impl.h",
            )
        ]

    monkeypatch.setattr(extractor, "_extract_from_compile_unit", _fake_extract)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    edges = extractor.extract_from_build(build)
    assert len(edges) == 1
    assert edges[0].dst_file == "src/detail/impl.h"


# ── ClangTypeGraphExtractor: graceful degrade ────────────────────────────────


def test_extractor_missing_clang_is_graceful() -> None:
    extractor = ClangTypeGraphExtractor(clang_bin="definitely-not-a-real-clang-binary")
    assert extractor.available() is False
    edges = extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert edges == []
    assert extractor.diagnostics
