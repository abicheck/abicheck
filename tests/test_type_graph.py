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

import hashlib

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.buildsource.type_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    RESOLUTION_REF_EXACT,
    RESOLUTION_REF_UNIQUE_CANDIDATE,
    RESOLUTION_REF_UNRESOLVED,
    ClangTypeGraphExtractor,
    TypeEdge,
    _base_type_name,
    _merge_type_edges,
    augment_graph_with_types,
    index_declared_type_files,
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


def test_param_type_identity_strips_macos_mach_o_underscore() -> None:
    # On Darwin, clang's own -ast-dump=json reports a C++ decl's mangledName
    # with the Mach-O ABI's extra linker-symbol-table underscore still
    # attached ("__ZN..." rather than "_ZN...") -- the same decoration
    # macho_metadata.py already strips off the *binary's* export table, so a
    # header_graph-seeded decl:// node for the same function is keyed on the
    # one-underscore form. Left unstripped here, a public function's own
    # DECL_HAS_TYPE edge to a private parameter type would land on a
    # different, never-public node and public_api_internal_dependency_added
    # would never fire for a function-rooted dependency on macOS.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "configure",
            "mangledName": "__ZN9configureEPN6detail4ImplE",
            "inner": [_param("p", "detail::Impl*")],
        },
    )
    edges = parse_clang_ast_types(ast)
    params = [e for e in edges if e.kind == "DECL_HAS_TYPE" and e.role == "param"]
    assert (
        TypeEdge(
            "_ZN9configureEPN6detail4ImplE",
            "detail::Impl",
            "DECL_HAS_TYPE",
            CONF_HIGH,
            "param",
        )
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


def test_param_type_resolves_callback_parameter_type_to_private_type() -> None:
    # A private type appearing as a *parameter* of a callback-shaped
    # template argument (std::function<void(detail::Impl)>) — not the whole
    # instantiation, not the return type — must still produce a direct edge.
    # Truncating "void (detail::Impl)" at its first top-level "(" would
    # discard the parameter list entirely and only ever see "void" (Codex
    # review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record(
            "Widget",
            inner=[
                _method(
                    "setCb",
                    "_ZN6Widget5setCbE",
                    [_param("cb", "std::function<void (detail::Impl)>")],
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


def test_return_type_resolves_when_return_is_callback_template() -> None:
    # A public function returning a callback-shaped template
    # (std::function<detail::Impl ()>) has its own function type spelled as
    # "std::function<detail::Impl ()> ()" — a naive find("(") stops at the
    # callback's *inner* parameter list instead of the outer function's own,
    # truncating mid-template (Codex review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "make_cb",
            "mangledName": "_Z7make_cbv",
            "type": {"qualType": "std::function<detail::Impl ()> ()"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    returns = [e for e in edges if e.role == "return"]
    assert (
        TypeEdge("_Z7make_cbv", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "return")
        in returns
    )
    assert any(e.dst == "std::function<detail::Impl ()>" for e in returns)


def test_return_type_resolves_trailing_return_type() -> None:
    # A trailing return type spells as "auto (Args) -> RetType" — the region
    # before the parameter list is just the literal "auto" placeholder, not
    # the real return type, so truncating at the first top-level "(" (as the
    # non-trailing case does) only ever sees "auto", excluded as a builtin
    # (Codex review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "make",
            "mangledName": "_Z4makev",
            "type": {"qualType": "auto () -> detail::Impl *"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    returns = [e for e in edges if e.role == "return"]
    assert returns == [
        TypeEdge("_Z4makev", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "return")
    ]


def test_param_type_resolves_direct_function_pointer_callback_parameter() -> None:
    # A *direct* (non-template) callback parameter, e.g.
    # `void set_cb(void (*cb)(detail::Impl))`, has two top-level paren
    # groups: the pointer-declarator wrapper "(*)" and the real parameter
    # list "(detail::Impl)". Only running the callback-parameter extraction
    # from inside _template_arg_types() (as std::function<...> uses) missed
    # this direct-callback shape entirely — it never goes through a template
    # argument at all (Codex review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record(
            "Widget",
            inner=[
                _method(
                    "set_cb",
                    "_ZN6Widget6set_cbE",
                    [_param("cb", "void (*)(detail::Impl)")],
                )
            ],
        ),
    )
    edges = parse_clang_ast_types(ast)
    params = [e for e in edges if e.kind == "DECL_HAS_TYPE" and e.role == "param"]
    assert (
        TypeEdge(
            "_ZN6Widget6set_cbE", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "param"
        )
        in params
    )


def test_field_type_resolves_pointer_to_member_data_owner() -> None:
    # A pointer-to-member-data field, e.g. `int detail::Impl::* p;`, names
    # both a member type ("int", builtin) and an owner class
    # ("detail::Impl") — the owner is the actual dependency exposed. The
    # plain trailing "*"-stripping in _base_type_name left a dangling
    # "detail::Impl::" that matched no indexed declaration (Codex review's
    # exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        _record("Widget", inner=[_field("p", "int detail::Impl::*")]),
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert (
        TypeEdge("Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
        in fields
    )


def test_return_type_resolves_when_return_is_function_pointer() -> None:
    # A function returning a function pointer whose own parameter list
    # names a private type, e.g. `void (*make_cb())(detail::Impl);`, spells
    # its own type as "void (*())(detail::Impl)" — the outer decl's own
    # (empty) argument list "()" is nested *inside* the return type's own
    # declarator group "(*())", not simply prefixed before a single
    # top-level paren the way an ordinary function is (Codex review's exact
    # example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "make_cb",
            "mangledName": "_Z7make_cbv",
            "type": {"qualType": "void (*())(detail::Impl)"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    returns = [e for e in edges if e.role == "return"]
    assert (
        TypeEdge("_Z7make_cbv", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "return")
        in returns
    )


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
    # ADR-041 P1 #4: the bare-name-but-unique-candidate fallback is a genuine
    # best-effort guess, so it stays CONF_REDUCED with a resolution label
    # distinguishing it from an id-index/already-complete exact match.
    assert refs[0].resolution == RESOLUTION_REF_UNIQUE_CANDIDATE


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
    assert refs[0].resolution == RESOLUTION_REF_UNRESOLVED


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


def test_compact_restrict_pointer_suffix_is_stripped() -> None:
    # A C API's `struct Impl * restrict p` spells as clang's compact
    # "struct Impl *restrict" (no space before "restrict", same glued-pointer
    # spelling as *const/*volatile) — only const/volatile were normalized,
    # so "restrict"/"__restrict"/"__restrict__" left a dangling suffix that
    # never matched the indexed declaration (Codex review).
    ast = _tu(_record("Widget", inner=[_field("p", "struct Widget *restrict")]))
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert fields == [
        TypeEdge("Widget", "Widget", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field")
    ]
    assert _base_type_name("detail::Impl *__restrict__") == "detail::Impl"
    assert _base_type_name("detail::Impl *__restrict") == "detail::Impl"


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


def test_declrefexpr_stub_id_disambiguates_identity_not_only_file() -> None:
    # a::k and b::k share the bare name "k" but *do* each have a distinct
    # mangled name (the common case for extern-linkage globals) — the id
    # lookup was only used to resolve dst_file, so an ambiguous-by-bare-name
    # stub still fell back to "k" (unresolved) as its *identity* even though
    # the same id lookup already pinned down exactly which declaration was
    # referenced (Codex review).
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "a",
            "inner": [
                {"kind": "VarDecl", "name": "k", "mangledName": "_ZN1a1kE", "id": "0x1"}
            ],
        },
        {
            "kind": "NamespaceDecl",
            "name": "b",
            "inner": [
                {"kind": "VarDecl", "name": "k", "mangledName": "_ZN1b1kE", "id": "0x2"}
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
                            # Incomplete stub: only the bare name and id, no
                            # mangledName — clang's common shape for a
                            # DeclRefExpr's referencedDecl.
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
    # ADR-041 P1 #4: an id-index match is deterministic/unambiguous, so this
    # now earns CONF_HIGH like an already-complete stub -- not the flat
    # CONF_REDUCED every DECL_REFERENCES_DECL edge used to get regardless of
    # how confidently its target was identified.
    assert refs == [
        TypeEdge("_Z1fv", "_ZN1b1kE", "DECL_REFERENCES_DECL", CONF_HIGH, "ref")
    ]
    assert refs[0].resolution == RESOLUTION_REF_EXACT


def test_id_hit_wins_over_coincidental_bare_name_in_decl_file() -> None:
    # CodeRabbit review: an unrelated global `k` with no mangled name at all
    # indexes decl_file["k"] = "global.hpp" (its identity IS the bare name).
    # A DIFFERENT DeclRefExpr stub (incomplete, no mangledName) also spells
    # "k" but its `id` resolves via id_index to b::k's real mangled identity.
    # The old code checked "ident not in decl_file" to decide whether the
    # stub was already complete -- since "k" coincidentally already exists in
    # decl_file (from the unrelated global), it skipped the id_index lookup
    # entirely and kept the wrong bare "k" identity. id_hit must win first,
    # regardless of what already happens to be in decl_file.
    ast = _tu(
        {"kind": "VarDecl", "name": "k"},  # global, no mangled name -> identity "k"
        {
            "kind": "NamespaceDecl",
            "name": "b",
            "inner": [
                {"kind": "VarDecl", "name": "k", "mangledName": "_ZN1b1kE", "id": "0x2"}
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
    assert refs == [
        TypeEdge("_Z1fv", "_ZN1b1kE", "DECL_REFERENCES_DECL", CONF_HIGH, "ref")
    ]
    assert refs[0].resolution == RESOLUTION_REF_EXACT


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


def test_block_scope_local_reference_carries_no_project_provenance() -> None:
    # A block-scope local (`int api() { int x; return x; }`) is a private
    # implementation detail that can never be reached from outside the
    # function — indexing it the same way a namespace-scope global is
    # indexed would let it be marked defined_in_project and reported by
    # public_to_internal_dependency as a hidden internal dependency of every
    # public function that happens to declare a local variable (Codex
    # review's exact example).
    ast = _tu(
        {
            "kind": "FunctionDecl",
            "name": "api",
            "mangledName": "_Z3apiv",
            "loc": {"file": "src/api.cpp"},
            "inner": [
                {
                    "kind": "CompoundStmt",
                    "inner": [
                        {
                            "kind": "DeclStmt",
                            "inner": [
                                {
                                    "kind": "VarDecl",
                                    "name": "x",
                                    "id": "0x1",
                                    "type": {"qualType": "int"},
                                }
                            ],
                        },
                        {
                            "kind": "ReturnStmt",
                            "inner": [_ref_expr("VarDecl", "x")],
                        },
                    ],
                }
            ],
        },
    )
    edges = parse_clang_ast_types(ast)
    ref_edges = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert len(ref_edges) == 1
    assert ref_edges[0].dst_file == ""

    graph = SourceGraphSummary()
    augment_graph_with_types(graph, ref_edges, frozenset({"src/api.cpp"}))
    node = next(n for n in graph.nodes if n.id == "decl://x")
    assert not node.attrs.get("defined_in_project")


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


def test_same_private_type_as_return_and_param_stays_role_distinct() -> None:
    # Codex review on PR #620: a function that both returns and takes the
    # same private type used to collapse onto one DECL_HAS_TYPE edge --
    # _dedupe_edges keyed on (src, dst, kind) alone, dropping whichever role
    # was emitted second (params, since return is emitted first) before the
    # edge ever reached augment_graph_with_types/add_edge. Both roles must
    # now survive as distinct TypeEdges.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "roundtrip",
            "mangledName": "_Z9roundtripN6detail4ImplE",
            "type": {"qualType": "detail::Impl (detail::Impl)"},
            "inner": [_param("x", "detail::Impl")],
        },
    )
    edges = parse_clang_ast_types(ast)
    has_type = [e for e in edges if e.kind == "DECL_HAS_TYPE" and e.dst == "detail::Impl"]
    roles = {e.role for e in has_type}
    assert roles == {"return", "param"}


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


def test_augment_graph_backfill_survives_serialization_round_trip() -> None:
    # ADR-046 D2 regression (Codex review on PR #620): the backfill above must
    # go through register_fact, not a direct existing.attrs[...] mutation --
    # a direct mutation is invisible to facts/resolved, so
    # ensure_facts_and_resolve silently drops it on the next
    # to_dict()/from_dict() round-trip (a persisted pack reload).
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

    reloaded = SourceGraphSummary.from_dict(graph.to_dict())
    node = next(n for n in reloaded.nodes if n.id == "type://ns::detail::Impl")
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


def test_extract_from_build_propagates_deadline_into_pool_workers(monkeypatch) -> None:
    """Codex review (PR #591): contextvars don't cross a ThreadPoolExecutor
    boundary, so a worker submitted from inside deadline.deadline_scope()
    used to see no active deadline at all — each clang subprocess call
    inside it would run to its full fixed 120s regardless of --budget. Same
    fix/pattern as call_graph.ClangCallGraphExtractor (shared
    _deadline_bound_worker helper)."""
    import abicheck.buildsource.call_graph as cg
    from abicheck import deadline

    monkeypatch.setenv("ABICHECK_CALL_GRAPH_JOBS", "2")
    monkeypatch.setattr(cg, "_call_graph_mem_cap", lambda: 2)
    seen_remaining: list[float | None] = []

    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)

    def _fake_extract(cu: CompileUnit) -> list[TypeEdge]:
        seen_remaining.append(deadline.remaining())
        return []

    monkeypatch.setattr(extractor, "_extract_from_compile_unit", _fake_extract)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
            CompileUnit(id="cu://c", source="c.cpp"),
        ]
    )
    with deadline.deadline_scope(30.0):
        extractor.extract_from_build(build)
    assert len(seen_remaining) == 3
    assert all(r is not None for r in seen_remaining), (
        "pool worker saw no active deadline (remaining()=None) — the scan "
        "deadline did not cross the executor boundary"
    )
    assert all(0 < r <= 30.0 for r in seen_remaining)


def test_extract_from_safe_args_deadline_exceeded_degrades_to_diagnostic(
    monkeypatch,
) -> None:
    # Codex review (PR #591): this pass is advisory (ADR-028 D3) — a
    # DeadlineExceeded from the now-bounded clang subprocess must degrade to
    # the same diagnostic+[] contract as any other probe failure, not
    # propagate and abort the whole L5 type-graph fold.
    import abicheck.buildsource.type_graph as tg
    from abicheck import deadline

    def _raise(*_a, **_k):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(tg.deadline, "run_bounded", _raise)
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)
    edges = extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert edges == []
    assert any("clang invocation failed" in d for d in extractor.diagnostics)


def test_extract_from_safe_args_bounded_by_local_cap_not_full_scan_budget(
    monkeypatch,
) -> None:
    """Codex review (PR #591), round 8: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=120 on this L5 clang call alone did nothing once a scan
    --budget was active: the call stayed bound by the FULL remaining scan
    budget instead of this pass's own 120s local cap, mirroring the
    identical call_graph.py regression. Assert the ContextVar deadline
    observed inside run_bounded is capped near the local cap, not the much
    larger outer scan budget."""
    import abicheck.buildsource.type_graph as tg
    from abicheck import deadline

    seen_remaining: list[float | None] = []

    def fake_run_bounded(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(tg.deadline, "run_bounded", fake_run_bounded)
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        extractor._extract_from_safe_args(["--", "foo.cpp"])

    assert seen_remaining
    assert seen_remaining[0] is not None and seen_remaining[0] <= 120.5


def test_extract_from_safe_args_rechecks_deadline_before_parsing_ast(
    monkeypatch,
) -> None:
    """Codex review (PR #591): the same post-subprocess gap as call_graph's
    identical fix — clang can exit successfully right as the budget expires,
    but json.loads()+parse_clang_ast_types() used to run unbounded. Must
    degrade to the advisory diagnostic+[] contract (ADR-028 D3), not raise."""
    import json as _json
    import time

    import abicheck.buildsource.type_graph as tg
    from abicheck import deadline

    ast = _tu(_record("Widget", inner=[_field("x", "int")]))

    def fake_run(*_a, **_k):
        # Simulate the budget running out while clang was still parsing: by
        # the time it exits successfully, the deadline has already passed.
        time.sleep(0.05)
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr(tg.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(tg.deadline, "run_bounded", fake_run)
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    with deadline.deadline_scope(0.03):
        edges = extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert edges == []
    assert any(
        "scan deadline exceeded before parsing clang AST" in d
        for d in extractor.diagnostics
    )


def test_extract_from_safe_args_rechecks_deadline_before_walking_ast(
    monkeypatch,
) -> None:
    """Codex review (PR #591, round 4): json.loads() on a huge L5 type-graph
    AST can itself consume the rest of the budget -- the existing pre-load
    deadline.check() doesn't catch that; must re-check again after the load,
    before the recursive parse_clang_ast_types() walk."""
    import json as _json
    import time

    import abicheck.buildsource.type_graph as tg
    from abicheck import deadline

    ast = _tu(_record("Widget", inner=[_field("x", "int")]))

    def fake_run(*_a, **_k):
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr(tg.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(tg.deadline, "run_bounded", fake_run)
    real_loads = tg.json.loads

    def _slow_loads(text):
        time.sleep(0.05)
        return real_loads(text)

    monkeypatch.setattr(tg.json, "loads", _slow_loads)
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    with deadline.deadline_scope(0.03):
        edges = extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert edges == []
    assert any(
        "scan deadline exceeded before walking clang AST" in d
        for d in extractor.diagnostics
    )


# ── ClangTypeGraphExtractor: graceful degrade ────────────────────────────────


def test_extractor_missing_clang_is_graceful() -> None:
    extractor = ClangTypeGraphExtractor(clang_bin="definitely-not-a-real-clang-binary")
    assert extractor.available() is False
    edges = extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert edges == []


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_clang(monkeypatch, *, proc: _FakeProc) -> None:
    import abicheck.buildsource.type_graph as tg

    monkeypatch.setattr(tg.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(tg.deadline, "run_bounded", lambda *_a, **_k: proc)


def test_extract_from_safe_args_nonzero_exit_records_diagnostic_but_salvages_edges(
    monkeypatch,
) -> None:
    # Ninth Codex review: clang can exit non-zero (real compile errors in the
    # necessarily-approximate replayed flags) while still printing a partial,
    # error-recovered AST dump. Edges are still salvaged (best effort), but a
    # diagnostic must be recorded regardless — extractor_pass_fully_covered
    # relies on `diagnostics` being non-empty to disqualify confirmed pass
    # coverage for this TU. Mirrors call_graph's identical fix.
    import json as _json

    ast = _tu(_record("Widget", inner=[_field("x", "int")]))
    _patch_clang(
        monkeypatch,
        proc=_FakeProc(_json.dumps(ast), stderr="error: bad thing", returncode=1),
    )
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    edges = extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert edges == []  # a bare int field emits no type edge
    assert any("exited 1" in d for d in extractor.diagnostics)


def test_extract_from_safe_args_zero_exit_records_no_diagnostic(monkeypatch) -> None:
    import json as _json

    ast = _tu(_record("Widget"))
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast), returncode=0))
    extractor = ClangTypeGraphExtractor(clang_bin="clang++")
    extractor._extract_from_safe_args(["--", "foo.cpp"])
    assert extractor.diagnostics == []


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


def test_field_default_member_initializer_reference_edge() -> None:
    # A default member initializer (`int x = detail::k;`) lives *under* the
    # FieldDecl node itself, not inside a function body. Recursing into a
    # FieldDecl with whatever enclosing_func the record itself had (empty
    # for a top-level record) meant the DeclRefExpr guard never saw a truthy
    # enclosing_func, so a reference in a default member initializer
    # produced no edge at all (CodeRabbit review).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("k")]},
        _record(
            "Widget",
            inner=[
                {
                    "kind": "FieldDecl",
                    "name": "x",
                    "type": {"qualType": "int"},
                    "inner": [_ref_expr("VarDecl", "k", "_ZN6detail1kE")],
                }
            ],
        ),
    )
    edges = parse_clang_ast_types(ast)
    refs = [e for e in edges if e.kind == "DECL_REFERENCES_DECL"]
    assert refs == [
        TypeEdge(
            "Widget::x", "_ZN6detail1kE", "DECL_REFERENCES_DECL", CONF_REDUCED, "ref"
        )
    ]


def test_type_alias_underlying_type_edge() -> None:
    # A public alias's *underlying* type was never emitted as a dependency
    # at all — only the alias's own name was indexed as a resolvable
    # target, so `using Handle = detail::Impl *;` produced no edge from
    # `Handle` to the private `detail::Impl` it actually wraps (Codex
    # review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "TypeAliasDecl",
            "name": "Handle",
            "type": {"qualType": "detail::Impl *"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    alias_edges = [e for e in edges if e.role == "alias"]
    assert alias_edges == [
        TypeEdge("Handle", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "alias")
    ]


def test_typedef_underlying_type_edge() -> None:
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "TypedefDecl",
            "name": "Handle",
            "type": {"qualType": "detail::Impl *"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    alias_edges = [e for e in edges if e.role == "alias"]
    assert alias_edges == [
        TypeEdge("Handle", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "alias")
    ]


def test_public_var_decl_own_type_edge() -> None:
    # A public/exported data declaration's own type was never emitted
    # either — only read when the VarDecl was the *target* of a DeclRefExpr,
    # never at its own declaration site, so `extern detail::Impl *g;`
    # produced no DECL_HAS_TYPE edge for the private pointee (Codex
    # review's exact example).
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "VarDecl",
            "name": "g",
            "mangledName": "_Z1g",
            "type": {"qualType": "detail::Impl *"},
            "inner": [],
        },
    )
    edges = parse_clang_ast_types(ast)
    var_edges = [e for e in edges if e.role == "var"]
    assert var_edges == [
        TypeEdge("_Z1g", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "var")
    ]


def test_block_scope_local_var_decl_gets_no_own_type_edge() -> None:
    # A block-scope local must not get a "var" DECL_HAS_TYPE edge for its
    # own type — it's a private implementation detail, same rationale as
    # excluding it from reference-edge provenance.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "FunctionDecl",
            "name": "api",
            "mangledName": "_Z3apiv",
            "inner": [
                {
                    "kind": "CompoundStmt",
                    "inner": [
                        {
                            "kind": "DeclStmt",
                            "inner": [
                                {
                                    "kind": "VarDecl",
                                    "name": "local",
                                    "id": "0x1",
                                    "type": {"qualType": "detail::Impl *"},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )
    edges = parse_clang_ast_types(ast)
    assert not [e for e in edges if e.role == "var"]


# ── index_declared_type_files ────────────────────────────────────────────────


def test_index_declared_type_files_returns_qualified_type_names() -> None:
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [_record("Widget", inner=[_field("x", "int")])],
        }
    )
    # Stamp a declaring file on the record (the shared _record helper in this
    # file doesn't set loc; do it directly here).
    ast["inner"][0]["inner"][0]["loc"] = {"file": "ns/widget.h"}
    assert index_declared_type_files(ast) == {"ns::Widget": "ns/widget.h"}


def test_index_declared_type_files_excludes_var_and_enum_constant_identities() -> None:
    # Codex review: `_index_declared_entities`'s `decl_file` output is shared
    # between type declarations (indexed in `name_index`) and var/enum-constant
    # identities (used only for DECL_REFERENCES_DECL resolution, never indexed
    # in `name_index`) — a namespace-scope constant must not leak into this
    # type-only wrapper's result and get mistaken for a record/enum/typedef.
    ast = _tu(
        {
            "kind": "VarDecl",
            "name": "k",
            "mangledName": "_ZN2ns1kE",
            "loc": {"file": "ns/consts.h"},
            "type": {"qualType": "const int"},
        },
        _record("Widget", inner=[_field("x", "int")]),
    )
    ast["inner"][1]["loc"] = {"file": "widget.h"}
    result = index_declared_type_files(ast)
    assert result == {"Widget": "widget.h"}
    assert "_ZN2ns1kE" not in result
    assert "k" not in result


# ── richer confidence/provenance: _resolve_type_name resolution tiers ───────


def test_scope_match_labeled_with_resolution_scope() -> None:
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                _record("Base"),
                _record("Widget", bases=[_base("Base")]),
            ],
        }
    )
    edges = parse_clang_ast_types(ast)
    inherits = [e for e in edges if e.kind == "TYPE_INHERITS"]
    assert len(inherits) == 1
    assert inherits[0].confidence == CONF_HIGH
    assert inherits[0].resolution == "scope"


def test_unresolved_type_labeled_with_resolution_unresolved() -> None:
    ast = _tu(_record("Widget", inner=[_field("p", "Unknown *")]))
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert len(fields) == 1
    assert fields[0].confidence == CONF_REDUCED
    assert fields[0].resolution == "unresolved"


def test_unrelated_scope_unique_candidate_gets_reduced_confidence_and_label() -> None:
    # Codex-review-style richer confidence (ADR-041 addendum): a bare name
    # that matches no enclosing scope in the walk, but is the only
    # same-named declaration anywhere in the TU, was previously folded into
    # the same flat CONF_HIGH tier as a real scope match — even though it's
    # a last-resort guess (the type could be structurally unrelated to the
    # referencing scope). Now flagged CONF_REDUCED with a distinct label.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "a", "inner": [_record("Helper")]},
        {
            "kind": "NamespaceDecl",
            "name": "c",
            "inner": [_record("Widget", inner=[_field("p", "Helper *")])],
        },
    )
    edges = parse_clang_ast_types(ast)
    fields = [e for e in edges if e.kind == "TYPE_HAS_FIELD_TYPE"]
    assert len(fields) == 1
    edge = fields[0]
    assert edge.dst == "a::Helper"
    assert edge.confidence == CONF_REDUCED
    assert edge.resolution == "unique_candidate"


def test_augment_graph_with_types_carries_resolution_into_edge_attrs() -> None:
    edges = [
        TypeEdge(
            "Widget",
            "a::Helper",
            "TYPE_HAS_FIELD_TYPE",
            CONF_REDUCED,
            "field",
            resolution="unique_candidate",
        ),
    ]
    graph = SourceGraphSummary()
    augment_graph_with_types(graph, edges)
    edge = next(e for e in graph.edges if e.kind == "TYPE_HAS_FIELD_TYPE")
    assert edge.attrs["resolution"] == "unique_candidate"
    assert edge.attrs["role"] == "field"


def test_extern_c_function_identity_matches_source_entity_fallback() -> None:
    # ADR-041 P1 #5 (Codex review): clang reports mangledName == name for an
    # extern "C"/C-linkage function (no real Itanium mangling), and
    # SourceEntity.identity() treats that as "no distinguishing mangled name"
    # -- falling back to qualified_name#signature_hash. The AST-replay layer
    # used to key this same function's DECL_HAS_TYPE src on the bare
    # mangled-or-name (identical to the bare name here), landing on a
    # different decl:// node than the L4 surface's own SOURCE_DECLARES node
    # for the same declaration.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Config")]},
        {
            "kind": "FunctionDecl",
            "name": "api",
            "mangledName": "api",
            "type": {"qualType": "int (detail::Config)"},
            "inner": [_param("x", "detail::Config")],
        },
    )
    edges = parse_clang_ast_types(ast)
    param_edges = [e for e in edges if e.role == "param"]
    assert len(param_edges) == 1
    expected_hash = hashlib.sha256(b"sig\x00int (detail::Config)").hexdigest()
    assert param_edges[0].src == f"api#sha256:{expected_hash}"


def test_real_mangled_function_identity_stays_the_mangled_name() -> None:
    # A genuinely mangled C++ function (mangledName != name) is unaffected --
    # the mangled name alone already matches SourceEntity.identity()'s primary
    # case, so no signature-hash suffix should be added.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Config")]},
        _record(
            "Widget",
            inner=[_method("bar", "_ZN6Widget3barE", [_param("x", "detail::Config")])],
        ),
    )
    edges = parse_clang_ast_types(ast)
    param_edges = [e for e in edges if e.role == "param"]
    assert len(param_edges) == 1
    assert param_edges[0].src == "_ZN6Widget3barE"


def test_extern_c_variable_type_edge_source_is_scope_qualified() -> None:
    # Codex review: namespace api { extern "C" detail::Impl *g; } -- clang
    # reports mangledName == name for the extern "C" variable (no real
    # Itanium mangling), and SourceEntity.identity() for a variable (which
    # never sets signature_hash) falls back to the bare qualified name
    # "api::g". The AST-replay layer used to key this VarDecl's own
    # DECL_HAS_TYPE edge on the unqualified bare name "g", landing on a
    # different decl:// node than the public SOURCE_DECLARES node for the
    # same declaration -- so reachability from the public variable never
    # reached the private pointee type.
    ast = _tu(
        {"kind": "NamespaceDecl", "name": "detail", "inner": [_record("Impl")]},
        {
            "kind": "NamespaceDecl",
            "name": "api",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "g",
                    "mangledName": "g",
                    "type": {"qualType": "detail::Impl *"},
                }
            ],
        },
    )
    edges = parse_clang_ast_types(ast)
    var_edges = [e for e in edges if e.role == "var"]
    assert var_edges == [
        TypeEdge("api::g", "detail::Impl", "DECL_HAS_TYPE", CONF_HIGH, "var")
    ]
