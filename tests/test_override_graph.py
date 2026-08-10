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

"""Tests for ADR-041 P2 item 1: the Clang virtual-dispatch/class-hierarchy
override-edge parser, graph augmentation, and graceful clang-absent degrade.

The parser is exercised against hand-built ``clang -ast-dump=json`` trees
(verified against real ``clang++ -std=c++17 -Xclang -ast-dump=json`` output
during development — see this module's own docstring) so no compiler is
required here; the live subprocess path is integration-only."""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.override_graph import (
    RESOLUTION_OVERRIDE_CONFIRMED,
    RESOLUTION_OVERRIDE_SIGNATURE_MATCH,
    ClangOverrideGraphExtractor,
    OverrideEdge,
    _strip_exception_spec,
    augment_graph_with_overrides,
    parse_clang_ast_overrides,
)
from abicheck.buildsource.source_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphNode,
    SourceGraphSummary,
)


def _method(
    name: str,
    mangled: str,
    qual_type: str,
    *,
    is_virtual: bool = False,
    has_override_attr: bool = False,
) -> dict:
    d: dict = {
        "kind": "CXXMethodDecl",
        "name": name,
        "mangledName": mangled,
        "type": {"qualType": qual_type},
        "inner": [],
    }
    if is_virtual:
        d["virtual"] = True
    if has_override_attr:
        d["inner"].append({"kind": "OverrideAttr"})
    return d


def _record(name: str, *, bases: list[str] | None = None, inner: list[dict]) -> dict:
    d: dict = {"kind": "CXXRecordDecl", "name": name, "inner": inner}
    if bases:
        d["bases"] = [{"type": {"qualType": b}} for b in bases]
    return d


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


# ── _strip_exception_spec ────────────────────────────────────────────────


def test_strip_exception_spec_bare_noexcept() -> None:
    assert _strip_exception_spec("void () noexcept") == "void ()"


def test_strip_exception_spec_preserves_cv_and_ref_qualifiers() -> None:
    assert _strip_exception_spec("void () const noexcept") == "void () const"
    assert _strip_exception_spec("void () & noexcept") == "void () &"
    assert _strip_exception_spec("void () const & noexcept") == "void () const &"


def test_strip_exception_spec_throw_spec() -> None:
    assert _strip_exception_spec("int (int) throw()") == "int (int)"


def test_strip_exception_spec_dependent_noexcept_expression() -> None:
    # A parenthesized noexcept(...) expression may itself contain balanced
    # parens; the balance-counted strip must consume the whole group as one
    # unit, not stop at the first inner ")".
    assert _strip_exception_spec("void () noexcept(sizeof(int) == 4)") == "void ()"


def test_strip_exception_spec_no_exception_spec_is_unchanged() -> None:
    assert _strip_exception_spec("void ()") == "void ()"
    assert _strip_exception_spec("void (int)") == "void (int)"
    assert _strip_exception_spec("void () const") == "void () const"
    assert _strip_exception_spec("void () &") == "void () &"


def test_strip_exception_spec_no_qualifiers_and_no_parens() -> None:
    assert _strip_exception_spec("int") == "int"


# ── parse_clang_ast_overrides ────────────────────────────────────────────


def test_strips_macos_mach_o_underscore_from_mangled_names() -> None:
    # Codex review, fresh evidence, verified against real
    # `clang++ --target=x86_64-apple-darwin` output: on Darwin, clang's own
    # -ast-dump=json reports mangledName with the Mach-O ABI's extra
    # linker-symbol-table underscore still attached ("__ZN..." not
    # "_ZN..."). Left unstripped, an override edge's identity would never
    # join the call/type graph's own node for the SAME method (both already
    # normalize via this exact helper) -- a disconnected duplicate node.
    ast = _tu(
        _record("B", inner=[_method("f", "__ZN1B1fEv", "void ()", is_virtual=True)]),
        _record(
            "D",
            bases=["B"],
            inner=[_method("f", "__ZN1D1fEv", "void ()", has_override_attr=True)],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge("_ZN1D1fEv", "_ZN1B1fEv", CONF_HIGH, RESOLUTION_OVERRIDE_CONFIRMED)
    ]


def test_confirmed_override_chain() -> None:
    # Base::run is the introducing virtual slot; Mid::run and Derived::run
    # both write `override` (OverrideAttr present) -- both edges must be
    # RESOLUTION_OVERRIDE_CONFIRMED/CONF_HIGH, and edges stay consecutive
    # (Derived->Mid, Mid->Base), not flattened to the ultimate root.
    ast = _tu(
        _record(
            "Base",
            inner=[
                _method("run", "_ZNK4Base3runEi", "int (int) const", is_virtual=True)
            ],
        ),
        _record(
            "Mid",
            bases=["Base"],
            inner=[
                _method(
                    "run", "_ZNK3Mid3runEi", "int (int) const", has_override_attr=True
                )
            ],
        ),
        _record(
            "Derived",
            bases=["Mid"],
            inner=[
                _method(
                    "run",
                    "_ZNK7Derived3runEi",
                    "int (int) const",
                    has_override_attr=True,
                ),
                # Unrelated overload (different signature) must not match.
                _method("run", "_ZN7Derived3runEif", "int (int, float)"),
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert set(edges) == {
        OverrideEdge(
            "_ZNK3Mid3runEi",
            "_ZNK4Base3runEi",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        ),
        OverrideEdge(
            "_ZNK7Derived3runEi",
            "_ZNK3Mid3runEi",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        ),
    }


def test_implicit_override_without_keyword_is_signature_match_only() -> None:
    # Real C++ makes this virtual/an override regardless of the keyword, but
    # without OverrideAttr this module cannot independently confirm it --
    # CONF_REDUCED/RESOLUTION_OVERRIDE_SIGNATURE_MATCH, not CONFIRMED.
    ast = _tu(
        _record(
            "Base",
            inner=[
                _method("run", "_ZNK4Base3runEi", "int (int) const", is_virtual=True)
            ],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[_method("run", "_ZNK7Derived3runEi", "int (int) const")],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZNK7Derived3runEi",
            "_ZNK4Base3runEi",
            CONF_REDUCED,
            RESOLUTION_OVERRIDE_SIGNATURE_MATCH,
        )
    ]


def test_multiple_inheritance_emits_edge_to_each_matching_base() -> None:
    ast = _tu(
        _record(
            "Multi1",
            inner=[_method("tick", "_ZN6Multi14tickEv", "void ()", is_virtual=True)],
        ),
        _record(
            "Multi2",
            inner=[_method("tick", "_ZN6Multi24tickEv", "void ()", is_virtual=True)],
        ),
        _record(
            "MultiDerived",
            bases=["Multi1", "Multi2"],
            inner=[
                _method(
                    "tick",
                    "_ZN12MultiDerived4tickEv",
                    "void ()",
                    has_override_attr=True,
                )
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert set(edges) == {
        OverrideEdge(
            "_ZN12MultiDerived4tickEv",
            "_ZN6Multi14tickEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        ),
        OverrideEdge(
            "_ZN12MultiDerived4tickEv",
            "_ZN6Multi24tickEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        ),
    }


def test_virtual_conversion_operator_override_emits_an_edge() -> None:
    # Codex review, fresh evidence, verified against real Clang 18 output:
    # a conversion operator (`operator int() const`) is its own node kind,
    # CXXConversionDecl -- NOT CXXMethodDecl -- so a CXXMethodDecl-only
    # collection filter silently dropped every conversion-operator override,
    # including a real, OverrideAttr-confirmed one.
    ast = _tu(
        _record(
            "Base",
            inner=[
                {
                    "kind": "CXXConversionDecl",
                    "name": "operator int",
                    "mangledName": "_ZNK4BasecviEv",
                    "type": {"qualType": "int () const"},
                    "virtual": True,
                    "inner": [],
                }
            ],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[
                {
                    "kind": "CXXConversionDecl",
                    "name": "operator int",
                    "mangledName": "_ZNK7DerivedcviEv",
                    "type": {"qualType": "int () const"},
                    "inner": [{"kind": "OverrideAttr"}],
                }
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZNK7DerivedcviEv",
            "_ZNK4BasecviEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_non_virtual_base_method_is_not_an_override_target() -> None:
    # Same (name, type.qualType) but the base method is never marked virtual
    # -- an unrelated ordinary member hiding, not an override.
    ast = _tu(
        _record("Base", inner=[_method("run", "_ZN4Base3runEv", "void ()")]),
        _record(
            "Derived",
            bases=["Base"],
            inner=[_method("run", "_ZN7Derived3runEv", "void ()")],
        ),
    )
    assert parse_clang_ast_overrides(ast) == []


def test_multiple_inheritance_skips_the_non_virtual_sibling_base() -> None:
    # Codex review, fresh evidence, verified against real Clang 17 output:
    # B1::f is virtual, B2::f is NOT, D : B1, B2 overrides f. clang accepts
    # D::f's `override` keyword (OverrideAttr present) since it overrides
    # B1::f -- but D::f does NOT override the unrelated, non-virtual B2::f;
    # it only hides it. Emitting an edge to B2::f as well as B1::f would be
    # a false virtual-dispatch candidate: qname's own slot being virtual
    # (via B1) must not be read as "every nearest declaring ancestor is a
    # real override target" -- each candidate's OWN slot must independently
    # be checked.
    ast = _tu(
        _record(
            "B1",
            inner=[_method("f", "_ZN2B11fEv", "void ()", is_virtual=True)],
        ),
        _record("B2", inner=[_method("f", "_ZN2B21fEv", "void ()")]),
        _record(
            "D",
            bases=["B1", "B2"],
            inner=[_method("f", "_ZN1D1fEv", "void ()", has_override_attr=True)],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZN1D1fEv",
            "_ZN2B11fEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_typedef_aliased_parameter_documents_known_gap() -> None:
    # Codex review, fresh evidence, verified against real Clang 17 output:
    # `typedef int I; virtual void f(I);` overridden by `void f(int)
    # override;` is a real, clang-confirmed override (OverrideAttr present)
    # that this matcher currently misses -- type.qualType spells "void (I)"
    # vs. "void (int)". A real fix needs re-assembling the signature from
    # each parameter's own desugaredQualType (only available per-parameter,
    # never at the whole-method level -- see the module's own docstring);
    # this test pins the current, honest miss (no edge, not a crash) so a
    # future fix has a concrete regression to flip.
    ast = _tu(
        _record(
            "Base",
            inner=[
                _method("f", "_ZN4Base1fEi", "void (I)", is_virtual=True),
            ],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[
                _method("f", "_ZN7Derived1fEi", "void (int)", has_override_attr=True)
            ],
        ),
    )
    assert parse_clang_ast_overrides(ast) == []


def test_same_named_local_classes_in_different_functions_documents_known_gap() -> None:
    # Codex review, fresh evidence, verified against real Clang 18 output:
    # a local class is scoped only by its enclosing namespace/class chain
    # here (mirroring type_graph.py's own _SCOPE_DECL_KINDS), never by its
    # enclosing FunctionDecl. Two DIFFERENT functions each locally
    # declaring same-named "B"/"D" hierarchies collide on that shared bare
    # identity -- confirmed empirically that a genuine, OverrideAttr-
    # confirmed override in the FIRST such function produces ZERO edges
    # once a second, unrelated same-named local hierarchy follows it in the
    # same translation unit. Not a narrow fix scoped to this module alone
    # (see the module's own docstring): `bases_of` itself is built from
    # type_graph.py's own TYPE_INHERITS edges, whose resolution has the
    # identical scope-blind collision. This test pins the current, honest
    # miss (no edge, not a crash) so a future cross-module fix has a
    # concrete regression to flip.
    def _func(name: str, *decls: dict) -> dict:
        return {
            "kind": "FunctionDecl",
            "name": name,
            "inner": [
                {
                    "kind": "CompoundStmt",
                    "inner": [{"kind": "DeclStmt", "inner": [d]} for d in decls],
                }
            ],
        }

    ast = _tu(
        _func(
            "func_one",
            _record(
                "B",
                inner=[
                    _method("f", "_ZZ8func_onevEN1B1fEv", "void ()", is_virtual=True)
                ],
            ),
            _record(
                "D",
                bases=["B"],
                inner=[
                    _method(
                        "f",
                        "_ZZ8func_onevEN1D1fEv",
                        "void ()",
                        has_override_attr=True,
                    )
                ],
            ),
        ),
        _func(
            "func_two",
            _record("B", inner=[_method("f", "_ZZ8func_twovEN1B1fEv", "void ()")]),
            _record(
                "D",
                bases=["B"],
                inner=[_method("f", "_ZZ8func_twovEN1D1fEv", "void ()")],
            ),
        ),
    )
    # The documented collision, not a crash -- the real override in
    # func_one is lost once func_two's unrelated, same-named, non-virtual
    # B/D pair overwrites it in the shared `declared` dict.
    assert parse_clang_ast_overrides(ast) == []


def test_override_reaches_through_non_redeclaring_intermediate_class() -> None:
    # Codex review, fresh evidence, verified against real Clang 17 output:
    # Base declares a virtual run(); Mid : Base does NOT redeclare it at
    # all; Derived : Mid overrides it. The real target is Base::run,
    # reached only by walking PAST the non-redeclaring Mid -- a direct-
    # bases-only lookup finds nothing (Mid has no "run" method at all).
    ast = _tu(
        _record(
            "Base",
            inner=[_method("run", "_ZN4Base3runEv", "void ()", is_virtual=True)],
        ),
        _record("Mid", bases=["Base"], inner=[]),
        _record(
            "Derived",
            bases=["Mid"],
            inner=[
                _method("run", "_ZN7Derived3runEv", "void ()", has_override_attr=True)
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZN7Derived3runEv",
            "_ZN4Base3runEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_override_reaches_through_two_non_redeclaring_intermediates() -> None:
    ast = _tu(
        _record(
            "Base",
            inner=[_method("run", "_ZN4Base3runEv", "void ()", is_virtual=True)],
        ),
        _record("Mid1", bases=["Base"], inner=[]),
        _record("Mid2", bases=["Mid1"], inner=[]),
        _record(
            "Derived",
            bases=["Mid2"],
            inner=[
                _method("run", "_ZN7Derived3runEv", "void ()", has_override_attr=True)
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZN7Derived3runEv",
            "_ZN4Base3runEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_noexcept_strengthened_override_still_matches() -> None:
    # Codex review, fresh evidence, verified against real Clang 17 output:
    # `virtual void run();` overridden by `void run() noexcept override;`
    # is a real, clang-confirmed override (OverrideAttr present) even
    # though the qualType strings differ ("void ()" vs "void () noexcept").
    ast = _tu(
        _record(
            "Base",
            inner=[_method("run", "_ZN4Base3runEv", "void ()", is_virtual=True)],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[
                _method(
                    "run",
                    "_ZN7Derived3runEv",
                    "void () noexcept",
                    has_override_attr=True,
                )
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZN7Derived3runEv",
            "_ZN4Base3runEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_throw_spec_normalizes_the_same_as_noexcept() -> None:
    ast = _tu(
        _record(
            "Base",
            inner=[
                _method("run", "_ZN4Base3runEi", "int (int) throw()", is_virtual=True)
            ],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[
                _method(
                    "run",
                    "_ZN7Derived3runEi",
                    "int (int) noexcept",
                    has_override_attr=True,
                )
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZN7Derived3runEi",
            "_ZN4Base3runEi",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_noexcept_normalization_preserves_cv_and_ref_qualifiers() -> None:
    # The exception spec strip must not eat the const/&/&& qualifiers that
    # precede it -- "void () const noexcept" -> "void () const", not
    # "void ()"; a const-qualified override must still NOT match a
    # non-const base of the same name.
    ast = _tu(
        _record(
            "Base",
            inner=[
                _method("run", "_ZN4Base3runEv", "void ()", is_virtual=True),
                _method("runc", "_ZNK4Base4runcEv", "void () const", is_virtual=True),
            ],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[
                _method(
                    "runc",
                    "_ZNK7Derived4runcEv",
                    "void () const noexcept",
                    has_override_attr=True,
                )
            ],
        ),
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZNK7Derived4runcEv",
            "_ZNK4Base4runcEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


def test_different_signature_does_not_match() -> None:
    ast = _tu(
        _record(
            "Base",
            inner=[_method("run", "_ZN4Base3runEi", "void (int)", is_virtual=True)],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[_method("run", "_ZN7Derived3runEf", "void (float)")],
        ),
    )
    assert parse_clang_ast_overrides(ast) == []


def test_constructors_and_destructors_excluded() -> None:
    # Only CXXMethodDecl/CXXConversionDecl participate --
    # CXXConstructorDecl/CXXDestructorDecl are deliberately out of scope for
    # this first slice (see the module's own docstring: the Itanium
    # two-symbol dtor mangling needs its own rule).
    ast = _tu(
        _record(
            "Base",
            inner=[
                {
                    "kind": "CXXDestructorDecl",
                    "name": "~Base",
                    "mangledName": "_ZN4BaseD1Ev",
                    "type": {"qualType": "void ()"},
                    "virtual": True,
                    "inner": [],
                }
            ],
        ),
        _record(
            "Derived",
            bases=["Base"],
            inner=[
                {
                    "kind": "CXXDestructorDecl",
                    "name": "~Derived",
                    "mangledName": "_ZN7DerivedD1Ev",
                    "type": {"qualType": "void ()"},
                    "inner": [{"kind": "OverrideAttr"}],
                }
            ],
        ),
    )
    assert parse_clang_ast_overrides(ast) == []


def test_class_with_no_bases_produces_no_edges() -> None:
    ast = _tu(
        _record(
            "Standalone",
            inner=[_method("run", "_ZN10Standalone3runEv", "void ()", is_virtual=True)],
        )
    )
    assert parse_clang_ast_overrides(ast) == []


def test_method_with_no_name_is_skipped() -> None:
    # Defensive: a malformed/synthetic node with no "name" must not crash
    # the walk or fabricate a bogus identity.
    ast = _tu(
        _record(
            "Base",
            inner=[
                {
                    "kind": "CXXMethodDecl",
                    "mangledName": "_ZN4Base3runEv",
                    "type": {"qualType": "void ()"},
                    "virtual": True,
                    "inner": [],
                }
            ],
        )
    )
    assert parse_clang_ast_overrides(ast) == []


def test_non_dict_and_empty_ast_produce_no_edges() -> None:
    assert parse_clang_ast_overrides({}) == []
    assert (
        parse_clang_ast_overrides(
            {"kind": "TranslationUnitDecl", "inner": [None, 42, "x"]}
        )
        == []
    )


def test_namespaced_hierarchy_qualifies_class_identities() -> None:
    ast = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "ns",
            "inner": [
                _record(
                    "Base",
                    inner=[
                        _method("run", "_ZN2ns4Base3runEv", "void ()", is_virtual=True)
                    ],
                ),
                _record(
                    "Derived",
                    bases=["ns::Base"],
                    inner=[
                        _method(
                            "run",
                            "_ZN2ns7Derived3runEv",
                            "void ()",
                            has_override_attr=True,
                        )
                    ],
                ),
            ],
        }
    )
    edges = parse_clang_ast_overrides(ast)
    assert edges == [
        OverrideEdge(
            "_ZN2ns7Derived3runEv",
            "_ZN2ns4Base3runEv",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]


# ── augment_graph_with_overrides ─────────────────────────────────────────


def test_augment_graph_adds_decl_nodes_and_edge() -> None:
    graph = SourceGraphSummary()
    edges = [
        OverrideEdge(
            "_ZNK7Derived3runEi",
            "_ZNK4Base3runEi",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]
    added = augment_graph_with_overrides(graph, edges)
    assert added == 1
    assert {n.id for n in graph.nodes} == {
        "decl://_ZNK7Derived3runEi",
        "decl://_ZNK4Base3runEi",
    }
    assert all(n.kind == "source_decl" for n in graph.nodes)
    (e,) = graph.edges
    assert e.src == "decl://_ZNK7Derived3runEi"
    assert e.dst == "decl://_ZNK4Base3runEi"
    assert e.kind == "METHOD_POSSIBLE_OVERRIDE"
    assert e.confidence == CONF_HIGH
    assert e.attrs.get("resolution") == RESOLUTION_OVERRIDE_CONFIRMED


def test_augment_graph_merges_onto_preexisting_decl_node() -> None:
    # An override edge's endpoint must land on the SAME decl:// node an
    # earlier DECL_CALLS_DECL/SOURCE_DECLARES pass already created, not a
    # disconnected duplicate.
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="decl://_ZNK4Base3runEi", kind="source_decl"))
    edges = [
        OverrideEdge(
            "_ZNK7Derived3runEi",
            "_ZNK4Base3runEi",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]
    augment_graph_with_overrides(graph, edges)
    assert len([n for n in graph.nodes if n.id == "decl://_ZNK4Base3runEi"]) == 1


def test_augment_graph_is_idempotent_on_reregistration() -> None:
    graph = SourceGraphSummary()
    edges = [
        OverrideEdge(
            "_ZNK7Derived3runEi",
            "_ZNK4Base3runEi",
            CONF_HIGH,
            RESOLUTION_OVERRIDE_CONFIRMED,
        )
    ]
    first = augment_graph_with_overrides(graph, edges)
    second = augment_graph_with_overrides(graph, edges)
    assert first == 1
    assert second == 0
    assert len(graph.edges) == 1


# ── ClangOverrideGraphExtractor: graceful degrade ────────────────────────


def test_extractor_missing_clang_returns_empty() -> None:
    ext = ClangOverrideGraphExtractor(clang_bin="definitely-not-a-real-clang-xyz")
    assert ext.available() is False
    assert ext._extract_from_safe_args(["--", "foo.cpp"]) == []
    assert (
        ext.extract_from_build(
            BuildEvidence(compile_units=[CompileUnit(id="cu://x", source="x.cpp")])
        )
        == []
    )
    assert ext.diagnostics


def test_extract_from_build_dedupes_across_compile_units(monkeypatch) -> None:
    ext = ClangOverrideGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(ext, "available", lambda: True)

    edge = OverrideEdge(
        "_ZNK7Derived3runEi",
        "_ZNK4Base3runEi",
        CONF_HIGH,
        RESOLUTION_OVERRIDE_CONFIRMED,
    )

    def _fake_extract(cu: CompileUnit) -> list[OverrideEdge]:
        return [edge]

    monkeypatch.setattr(ext, "_extract_from_compile_unit", _fake_extract)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
        ]
    )
    edges = ext.extract_from_build(build)
    assert edges == [edge]
