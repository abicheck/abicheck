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


def test_base_type_name_strips_array_bounds_before_pointer_suffix() -> None:
    # An array of pointers ("detail::Impl *[4]") ends in "]", not "*", so the
    # suffix-stripping loop never fires before a one-shot bracket strip *after*
    # the loop — leaving the trailing "*" behind (Codex review).
    assert _base_type_name("detail::Impl *[4]") == "detail::Impl"


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


def test_template_specialization_field_not_attributed_to_primary_template() -> None:
    # A ClassTemplateSpecializationDecl's "name" is the *primary* template's
    # bare name (clang doesn't fold template args into it), so a naive walk
    # would emit a TYPE_HAS_FIELD_TYPE edge from the shared "Holder" node for
    # an internal-only specialization's field, misattributing that one
    # instantiation's private-type dependency to the public generic template
    # itself (Codex review). No edge should name "detail::Impl" as reached
    # from "Holder".
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [_record("Impl")],
        },
        {
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Holder",
            "inner": [_field("value", "detail::Impl")],
        },
    )
    edges = parse_clang_ast_types(ast)
    assert not any(e.dst == "detail::Impl" for e in edges)
    assert not any(e.kind == "TYPE_HAS_FIELD_TYPE" for e in edges)


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


def test_field_type_resolves_template_argument_to_private_type() -> None:
    # The common PImpl/container pattern: a field typed
    # `std::unique_ptr<detail::Impl>` only resolving the *whole* instantiation
    # spelling as one endpoint hides the actual dependency on the private
    # `detail::Impl` — resolving template arguments too must additionally
    # produce a direct, resolved edge to it (Codex review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record("Widget", inner=[_field("p", "std::unique_ptr<detail::Impl>")]),
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert (
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
        in fields
    )
    assert any(e.dst == "std::unique_ptr<detail::Impl>" for e in fields)


def test_field_type_resolves_nested_template_argument_to_private_type() -> None:
    # A nested instantiation (`std::vector<std::unique_ptr<detail::Impl>>`)
    # must still reach the innermost private type.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record(
            "Widget", inner=[_field("v", "std::vector<std::unique_ptr<detail::Impl>>")]
        ),
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert (
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
        in fields
    )


def test_param_type_resolves_template_argument_to_private_type() -> None:
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record(
            "Widget",
            inner=[
                _method(
                    "bar",
                    "_ZN6Widget3barE",
                    [_param("p", "std::shared_ptr<detail::Impl>")],
                )
            ],
        ),
    )
    edges = parse_clang_ast_types(ast)
    params = [e for e in edges if e.kind == "DECL_HAS_TYPE" and e.role == "param"]
    assert (
        TypeEdge("_ZN6Widget3barE", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "param")
        in params
    )


def test_base_type_name_strips_callback_signature_argument() -> None:
    # std::function<detail::Impl ()>'s single template argument spells as
    # the written function-signature form "detail::Impl ()", not a plain
    # type name — the "()" parameter-list suffix wasn't recognized by any
    # existing stripping rule, so "Impl ()" was looked up as a type literally
    # named that (Codex review).
    assert _base_type_name("detail::Impl ()") == "detail::Impl"


def test_param_type_resolves_callback_template_argument_to_private_type() -> None:
    # A callback-shaped parameter (std::function<detail::Impl ()>) must
    # still resolve a direct edge to the private return type it wraps, and
    # the outer instantiation's own edge must not be corrupted by treating
    # the argument's nested "(" as ending the *outer* type spelling.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record(
            "Widget",
            inner=[
                _method(
                    "setCb",
                    "_ZN6Widget5setCbE",
                    [_param("cb", "std::function<detail::Impl ()>")],
                )
            ],
        ),
    )
    edges = parse_clang_ast_types(ast)
    params = [e for e in edges if e.kind == "DECL_HAS_TYPE" and e.role == "param"]
    assert (
        TypeEdge(
            "_ZN6Widget5setCbE", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "param"
        )
        in params
    )
    assert any(e.dst == "std::function<detail::Impl ()>" for e in params)


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


def test_sticky_file_state_carries_across_sibling_declarations() -> None:
    # clang emits loc.file only on the *first* declaration in a file; a later
    # sibling in the same file carries no "file" key at all (sticky
    # semantics, mirroring call_graph._node_file's own doc comment). The
    # sticky state must be threaded from one sibling call to the next in
    # every loop, not reset to the parent-supplied value for each sibling
    # independently, or every declaration after the first in a file loses
    # its dst_file (Codex review).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [
                {
                    "kind": "CXXRecordDecl",
                    "name": "Base",
                    "loc": {"file": "src/detail/shared.h", "line": 3},
                    "inner": [],
                },
                {
                    "kind": "CXXRecordDecl",
                    "name": "Impl",
                    "loc": {"line": 10},  # no "file" -- sticky from Base
                    "inner": [],
                },
            ],
        },
        _record("Widget", inner=[_field("p", "detail::Impl *")]),
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge(
            "Widget",
            "detail::Impl",
            "TYPE_HAS_FIELD_TYPE",
            CONF_HIGH,
            "field",
            "src/detail/shared.h",
        )
    ]


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


def test_compact_const_pointer_suffix_is_stripped() -> None:
    # clang glues a top-level cv-qualified pointer directly to the star
    # ("detail::Impl *const", not "detail::Impl * const") — Codex review.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record("Widget", inner=[_field("p", "detail::Impl *const")]),
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
    ]


def test_compact_volatile_pointer_suffix_is_stripped() -> None:
    ast = _tu(_record("Widget", inner=[_field("p", "Widget *volatile")]))
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge("Widget", "Widget", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
    ]


def test_declrefexpr_stub_disambiguated_by_clang_id() -> None:
    # a::k and b::k share the bare name "k" and neither has a mangled name
    # (e.g. internal-linkage constants), so identity resolution alone cannot
    # tell them apart. clang's referencedDecl stub still carries the node's
    # own "id" though, shared with the full declaration elsewhere in the
    # same TU — use it to pick the *referenced* declaration's file, not
    # whichever same-named one was indexed first (Codex review).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "a",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "k",
                    "id": "0x1",
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
                    "id": "0x2",
                    "loc": {"file": "src/b.h"},
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
                    "inner": [
                        {
                            "kind": "DeclRefExpr",
                            "referencedDecl": {
                                "kind": "VarDecl",
                                "name": "k",
                                "id": "0x2",
                            },
                        }
                    ],
                }
            ],
        },
    )
    edges = parse_clang_ast_types(ast)
    refs = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert len(refs) == 1
    assert refs[0].dst_file == "src/b.h"


def test_field_type_edge_excludes_builtins() -> None:
    # detail::Impl is declared elsewhere in the same TU (as any real clang AST
    # would have it, at least forward-declared) so it resolves confidently;
    # "int" is a builtin and excluded regardless.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record(
            "Widget",
            inner=[
                _field("p", "detail::Impl *"),
                _field("count", "int"),
            ],
        ),
    )
    edges = parse_clang_ast_types(ast)
    field_edges = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert field_edges == [
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
    ]


def test_field_type_with_no_matching_declaration_stays_reduced_confidence() -> None:
    # A qualified-looking spelling with no matching declaration anywhere in
    # the TU (e.g. it lives in a header this fixture doesn't model) cannot be
    # verified — the old "contains ::  ⇒ CONF_HIGH" shortcut used to
    # over-claim confidence here (Codex review); it must stay CONF_REDUCED.
    ast = _tu(_record("Widget", inner=[_field("p", "detail::Impl *")]))
    edges = parse_clang_ast_types(ast)
    field_edges = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert field_edges == [
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_REDUCED, "field")
    ]


def test_param_type_edge() -> None:
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Config")]},
        _record(
            "Widget",
            inner=[_method("bar", "_ZN6Widget3barE", [_param("x", "detail::Config")])],
        ),
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


def test_partially_qualified_type_resolves_against_enclosing_scope() -> None:
    # clang writes a *partially* qualified spelling exactly as the source
    # wrote it: a field typed `detail::Impl` inside `namespace ns { namespace
    # detail { ... } }` prints as "detail::Impl", not "ns::detail::Impl". The
    # old "contains :: => already fully qualified" shortcut left this
    # disconnected from the L4-derived `type://ns::detail::Impl` node (Codex
    # review) — the PR's own example, `namespace ns { namespace detail {
    # struct Impl; } struct Widget { detail::Impl *p; }; }`.
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
                _record("Widget", inner=[_field("p", "detail::Impl *")]),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge(
            "ns::Widget", "ns::detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field"
        )
    ]


def test_partially_qualified_name_does_not_match_unrelated_type() -> None:
    # "detail::Impl" must only match a candidate ending "::detail::Impl" —
    # not an unrelated "other::Impl" that merely shares the leaf name.
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "other",
            "inner": [_record("Impl")],
        },
        _record("Widget", inner=[_field("p", "detail::Impl *")]),
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_REDUCED, "field")
    ]


def test_non_dict_and_empty_ast_produce_no_edges() -> None:
    assert parse_clang_ast_types({}) == []
    assert (
        parse_clang_ast_types({"kind": "TranslationUnitDecl", "inner": [None, 42, "x"]})
        == []
    )


def test_return_type_edge_for_private_type() -> None:
    # clang spells a function decl's own type as the whole signature
    # ("detail::Impl *()", return type immediately followed by the
    # parenthesized param list) — only ParmVarDecl children were read, so
    # `detail::Impl *make();` produced no DECL_HAS_TYPE edge at all (Codex
    # review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "make",
            "mangledName": "_Z4makev",
            "type": {"qualType": "detail::Impl *()"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    returns = [e for e in edges if e.role == "return"]
    assert returns == [
        TypeEdge("_Z4makev", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "return")
    ]


def test_return_type_edge_with_parameters() -> None:
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "make",
            "mangledName": "_Z4makei",
            "type": {"qualType": "detail::Impl *(int)"},
            "inner": [_param("n", "int")],
        },
    )
    edges = parse_clang_ast_types(ast)
    returns = [e for e in edges if e.role == "return"]
    assert returns == [
        TypeEdge("_Z4makei", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "return")
    ]


def test_builtin_return_type_produces_no_edge() -> None:
    ast = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": "_Z1fv",
            "type": {"qualType": "int ()"},
            "inner": [],
        }
    )
    edges = parse_clang_ast_types(ast)
    assert not [e for e in edges if e.role == "return"]


def test_leading_global_scope_qualifier_is_stripped_before_matching() -> None:
    # A field/base/param can spell a project type with a leading global-scope
    # qualifier ("::ns::detail::Impl *"); the index stores declarations
    # without it. Matching on the unstripped spelling built "::::ns::..." and
    # never joined the indexed node (Codex review's exact example).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
                _record("Widget", inner=[_field("p", "::ns::detail::Impl *")]),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge(
            "ns::Widget", "ns::detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field"
        )
    ]


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


def test_augment_graph_backfills_provenance_onto_earlier_created_node() -> None:
    # detail::Impl is first observed as the *src* of its own base-class edge
    # (no dst_file known there, so no provenance is set on creation), then
    # ns::Widget's field edge later establishes it as a project-internal
    # dst. The marker must be backfilled onto the already-existing node
    # rather than only applying at node-creation time (Codex review).
    graph = SourceGraphSummary()
    edges = [
        TypeEdge(
            "ns::detail::Impl", "ns::detail::Base", "TYPE_INHERITS", CONF_HIGH, "base"
        ),
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


def test_augment_graph_never_overrides_l4_visibility() -> None:
    # A node already carrying real L4 evidence (visibility) must never be
    # overwritten by this best-effort AST-only marker, even if a later edge
    # would otherwise qualify it as a project-internal backfill target.
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(
            id="type://ns::detail::Impl",
            kind="record_type",
            label="ns::detail::Impl",
            provenance="source_abi",
            confidence=CONF_HIGH,
            attrs={"visibility": "public_header"},
        )
    )
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
    assert "defined_in_project" not in node.attrs
    assert node.attrs.get("visibility") == "public_header"


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


def test_default_argument_reference_edge() -> None:
    # clang places a default-argument expression's DeclRefExpr *under* the
    # ParmVarDecl node itself (`int f(int x = detail::k)`); skipping the
    # ParmVarDecl subtree entirely (rather than just not re-emitting its
    # already-recorded type edge) silently dropped this reference (Codex
    # review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("k")]},
        {
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": "_Z1fi",
            "inner": [
                {
                    "kind": "ParmVarDecl",
                    "name": "x",
                    "type": {"qualType": "int"},
                    "inner": [_ref_expr("VarDecl", "k", "_ZN6detail1kE")],
                }
            ],
        },
    )
    edges = parse_clang_ast_types(ast)
    refs = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert refs == [
        TypeEdge("_Z1fi", "_ZN6detail1kE", "DECL_REFERENCES_DECL", CONF_REDUCED, "ref")
    ]
