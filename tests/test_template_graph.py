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

"""Tests for :mod:`abicheck.buildsource.template_graph` (G29 Phase 5 item 1).

Fixture shapes below were verified against real ``clang -ast-dump=json``
output while writing the parser (see the module's own docstring for the
exact findings); the ``@pytest.mark.integration`` tests at the bottom
re-verify end to end against a real compiler rather than only the
hand-crafted shapes.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.buildsource.template_graph import (
    EDGE_DECL_INSTANTIATES_TEMPLATE,
    EDGE_INSTANTIATION_EMITS_SYMBOL,
    EDGE_TEMPLATE_USES_TYPE,
    NODE_TEMPLATE_DECL,
    NODE_TEMPLATE_INSTANTIATION,
    ClangTemplateGraphExtractor,
    TemplateArgUse,
    TemplateInstantiation,
    augment_graph_with_templates,
    parse_clang_ast_templates,
    template_decl_node_id,
    template_instantiation_node_id,
)

# ── pure parser tests ────────────────────────────────────────────────────────


def _class_template_ast(*, explicit_detached: bool = False) -> dict:
    """A minimal ``ClassTemplateDecl Wrapper`` with one implicit instantiation
    ``Wrapper<internal::Detail>``, shaped exactly like real clang output
    (verified empirically — see the module docstring).

    ``explicit_detached=True`` additionally reproduces the explicit-
    instantiation quirk: a *second* specialization sharing the pattern's own
    ``Wrapper<int>`` id, once as an empty stub nested under the
    ``ClassTemplateDecl`` and once with full content detached as a top-level
    sibling.
    """
    detail_decl = {
        "id": "0xDETAIL",
        "kind": "CXXRecordDecl",
        "name": "Detail",
        "inner": [],
    }
    internal_ns = {
        "kind": "NamespaceDecl",
        "name": "internal",
        "inner": [detail_decl],
    }
    wrapper_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Wrapper",
        "completeDefinition": True,
        "inner": [],
    }
    detail_instantiation = {
        "id": "0xSPEC_DETAIL",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Wrapper",
        "completeDefinition": True,
        "inner": [
            {
                "kind": "TemplateArgument",
                "type": {"qualType": "internal::Detail"},
                "inner": [
                    {
                        "id": "0xREF1",
                        "kind": "RecordType",
                        "type": {"qualType": "internal::Detail"},
                        "decl": {
                            "id": "0xDETAIL",
                            "kind": "CXXRecordDecl",
                            "name": "Detail",
                        },
                    }
                ],
            },
            {
                "kind": "CXXMethodDecl",
                "name": "get",
                "mangledName": "_ZNK7WrapperIN8internal6DetailEE3getEv",
            },
        ],
    }
    class_template_children = [
        {"kind": "TemplateTypeParmDecl", "name": "T"},
        wrapper_pattern,
        detail_instantiation,
    ]
    top_level: list[dict] = []
    if explicit_detached:
        int_stub = {
            "id": "0xSPEC_INT",
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Wrapper",
        }
        class_template_children.append(int_stub)
        int_full = {
            "id": "0xSPEC_INT",
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Wrapper",
            "completeDefinition": True,
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": "int"}, "inner": []},
                {
                    "kind": "CXXMethodDecl",
                    "name": "get",
                    "mangledName": "_ZNK7WrapperIiE3getEv",
                },
            ],
        }
        top_level.append(int_full)

    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Wrapper",
        "inner": class_template_children,
    }
    return {
        "kind": "TranslationUnitDecl",
        "inner": [internal_ns, class_template, *top_level],
    }


def test_parses_implicit_class_template_instantiation() -> None:
    ast = _class_template_ast()
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    inst = out[0]
    assert inst.kind == "record"
    assert inst.template_qname == "Wrapper"
    assert inst.label == "Wrapper<internal::Detail>"
    assert inst.args == (TemplateArgUse("internal::Detail", "internal::Detail"),)
    assert inst.emitted_symbols == ("_ZNK7WrapperIN8internal6DetailEE3getEv",)


def test_parses_explicit_instantiation_detached_from_its_template_decl() -> None:
    """The explicit-instantiation quirk (Codex-review-shaped empirical
    finding): a second, full-content specialization detached as a top-level
    sibling, sharing its id with an empty stub nested under the real
    ClassTemplateDecl, must still resolve to that template."""
    ast = _class_template_ast(explicit_detached=True)
    out = parse_clang_ast_templates(ast)
    labels = {i.label: i for i in out}
    assert set(labels) == {"Wrapper<internal::Detail>", "Wrapper<int>"}
    int_inst = labels["Wrapper<int>"]
    assert int_inst.template_qname == "Wrapper"
    assert int_inst.args == (TemplateArgUse("int", None),)
    assert int_inst.emitted_symbols == ("_ZNK7WrapperIiE3getEv",)


def test_class_template_stub_with_no_full_definition_is_skipped() -> None:
    """A specialization id that is only ever seen as an empty stub (no
    matching ``completeDefinition: true`` occurrence anywhere) contributes
    no instantiation — degrade to no answer, never a guess."""
    ast = _class_template_ast(explicit_detached=True)
    # Drop the detached full definition, keeping only the stub.
    ast["inner"] = [
        n for n in ast["inner"] if n.get("kind") != "ClassTemplateSpecializationDecl"
    ]
    out = parse_clang_ast_templates(ast)
    assert {i.label for i in out} == {"Wrapper<internal::Detail>"}


def _function_template_ast() -> dict:
    pattern = {"kind": "FunctionDecl", "name": "identity", "inner": []}
    instantiation = {
        "kind": "FunctionDecl",
        "name": "identity",
        "mangledName": "_Z8identityIiET_S0_",
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}, "inner": []},
        ],
    }
    function_template = {
        "kind": "FunctionTemplateDecl",
        "name": "identity",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            pattern,
            instantiation,
        ],
    }
    ns = {"kind": "NamespaceDecl", "name": "api", "inner": [function_template]}
    return {"kind": "TranslationUnitDecl", "inner": [ns]}


def test_parses_function_template_instantiation_with_namespace_scope() -> None:
    ast = _function_template_ast()
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    inst = out[0]
    assert inst.kind == "function"
    assert inst.template_qname == "api::identity"
    assert inst.label == "api::identity<int>"
    assert inst.emitted_symbols == ("_Z8identityIiET_S0_",)
    assert inst.args == (TemplateArgUse("int", None),)


def test_function_template_pattern_itself_is_not_an_instantiation() -> None:
    """A FunctionDecl child with no mangledName is the uninstantiated
    pattern — never emitted as its own TemplateInstantiation."""
    ast = _function_template_ast()
    out = parse_clang_ast_templates(ast)
    assert all(i.label != "api::identity" for i in out)


def test_non_type_template_argument_has_no_target() -> None:
    """A literal (non-type) template argument -- ``value`` present, no
    ``type``/``decl`` -- resolves to a TemplateArgUse with no target."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "FixedArray",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {"kind": "CXXRecordDecl", "name": "FixedArray"},
                    {
                        "id": "0xSPEC",
                        "kind": "ClassTemplateSpecializationDecl",
                        "name": "FixedArray",
                        "completeDefinition": True,
                        "inner": [
                            {
                                "kind": "TemplateArgument",
                                "type": {"qualType": "int"},
                                "inner": [],
                            },
                            {"kind": "TemplateArgument", "value": 4},
                        ],
                    },
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].args == (
        TemplateArgUse("int", None),
        TemplateArgUse("4", None),
    )
    assert out[0].label == "FixedArray<int, 4>"


def test_typedef_alias_argument_resolves_through_to_the_real_record() -> None:
    """clang's own printer resolves a ``using``/typedef alias argument
    straight to the underlying record's ``decl`` -- no typedef-chain
    following needed here (verified empirically, module docstring)."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "internal",
                "inner": [
                    {"kind": "CXXRecordDecl", "name": "Detail", "inner": []},
                ],
            },
            {
                "kind": "ClassTemplateDecl",
                "name": "Box",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {"kind": "CXXRecordDecl", "name": "Box"},
                    {
                        "id": "0xSPEC",
                        "kind": "ClassTemplateSpecializationDecl",
                        "name": "Box",
                        "completeDefinition": True,
                        "inner": [
                            {
                                "kind": "TemplateArgument",
                                # Alias spelling, resolved decl points at the
                                # real record (clang's own behavior).
                                "type": {"qualType": "internal::DetailAlias"},
                                "inner": [
                                    {
                                        "kind": "RecordType",
                                        "decl": {
                                            "id": "0xDETAIL_ID",
                                            "kind": "CXXRecordDecl",
                                            "name": "Detail",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }
    # Give the CXXRecordDecl an explicit id matching the decl reference.
    ast["inner"][0]["inner"][0]["id"] = "0xDETAIL_ID"
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].args == (TemplateArgUse("internal::DetailAlias", "internal::Detail"),)


# ── graph augmentation tests ─────────────────────────────────────────────────


def test_augment_graph_creates_instantiation_and_template_decl_nodes() -> None:
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(
            id="binary_symbol://_ZNK7WrapperIN8internal6DetailEE3getEv",
            kind="binary_symbol",
            label="_ZNK7WrapperIN8internal6DetailEE3getEv",
        )
    )
    inst = TemplateInstantiation(
        kind="record",
        template_qname="Wrapper",
        label="Wrapper<internal::Detail>",
        args=(TemplateArgUse("internal::Detail", "internal::Detail"),),
        emitted_symbols=("_ZNK7WrapperIN8internal6DetailEE3getEv",),
    )
    added = augment_graph_with_templates(graph, [inst])
    assert (
        added == 3
    )  # DECL_INSTANTIATES_TEMPLATE + TEMPLATE_USES_TYPE + INSTANTIATION_EMITS_SYMBOL

    node_ids = {n.id: n for n in graph.nodes}
    tdecl_id = template_decl_node_id("Wrapper")
    tinst_id = template_instantiation_node_id("Wrapper<internal::Detail>")
    assert node_ids[tdecl_id].kind == NODE_TEMPLATE_DECL
    assert node_ids[tinst_id].kind == NODE_TEMPLATE_INSTANTIATION

    edge_kinds = {(e.src, e.dst, e.kind) for e in graph.edges}
    assert (tinst_id, tdecl_id, EDGE_DECL_INSTANTIATES_TEMPLATE) in edge_kinds
    assert (
        tinst_id,
        "type://internal::Detail",
        EDGE_TEMPLATE_USES_TYPE,
    ) in edge_kinds
    assert (
        tinst_id,
        "binary_symbol://_ZNK7WrapperIN8internal6DetailEE3getEv",
        EDGE_INSTANTIATION_EMITS_SYMBOL,
    ) in edge_kinds


def test_augment_graph_skips_symbol_edge_when_not_exported() -> None:
    """An instantiated member the graph carries no binary_symbol node for
    (never ODR-used / inlined away / no binary evidence loaded) gets no
    INSTANTIATION_EMITS_SYMBOL edge -- ADR-057 D1's join-by-shared-node-id
    rule, reapplied."""
    graph = SourceGraphSummary()
    inst = TemplateInstantiation(
        kind="function",
        template_qname="identity",
        label="identity<int>",
        emitted_symbols=("_Z8identityIiET_S0_",),
    )
    augment_graph_with_templates(graph, [inst])
    assert not any(e.kind == EDGE_INSTANTIATION_EMITS_SYMBOL for e in graph.edges)


def test_augment_graph_unresolved_argument_gets_no_uses_type_edge() -> None:
    graph = SourceGraphSummary()
    inst = TemplateInstantiation(
        kind="record",
        template_qname="Wrapper",
        label="Wrapper<int>",
        args=(TemplateArgUse("int", None),),
    )
    augment_graph_with_templates(graph, [inst])
    assert not any(e.kind == EDGE_TEMPLATE_USES_TYPE for e in graph.edges)


def test_augment_graph_two_instantiations_share_one_template_decl_node() -> None:
    graph = SourceGraphSummary()
    a = TemplateInstantiation(
        kind="record", template_qname="Wrapper", label="Wrapper<int>"
    )
    b = TemplateInstantiation(
        kind="record", template_qname="Wrapper", label="Wrapper<internal::Detail>"
    )
    augment_graph_with_templates(graph, [a, b])
    tdecl_nodes = [n for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL]
    assert len(tdecl_nodes) == 1
    edges_to_tdecl = [
        e for e in graph.edges if e.kind == EDGE_DECL_INSTANTIATES_TEMPLATE
    ]
    assert len(edges_to_tdecl) == 2
    assert {e.dst for e in edges_to_tdecl} == {tdecl_nodes[0].id}


# ── real-toolchain regression (integration marker: needs clang) ─────────────


@pytest.mark.integration
def test_real_clang_class_and_function_template_instantiations(tmp_path) -> None:
    """Round-trips real ``clang -ast-dump=json`` output for both a class and
    a function template, including the explicit-instantiation detachment
    quirk, a typedef-aliased argument, and member-symbol emission — the
    exact findings this module's docstring documents, re-verified end to
    end rather than only against the hand-crafted fixtures above.
    """
    clang_bin = shutil.which("clang++") or shutil.which("clang")
    if clang_bin is None:
        pytest.skip("clang++ not found in PATH")
    src = tmp_path / "t.cpp"
    src.write_text(
        "namespace internal { struct Detail { int x; }; "
        "using DetailAlias = Detail; }\n"
        "template <typename T> struct Wrapper { T value; T get() const { return value; } };\n"
        "template struct Wrapper<int>;\n"
        "namespace api {\n"
        "  Wrapper<internal::DetailAlias> make();\n"
        "  Wrapper<internal::DetailAlias> make() "
        "{ return Wrapper<internal::DetailAlias>{}; }\n"
        "}\n"
        "template <typename T> T identity(T x) { return x; }\n"
        "int use() { return identity(3); }\n"
    )
    extractor = ClangTemplateGraphExtractor(clang_bin=clang_bin)
    assert extractor.available()
    result = subprocess.run(
        [
            clang_bin,
            "-std=c++17",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    ast = json.loads(result.stdout)
    out = parse_clang_ast_templates(ast)
    labels = {i.label: i for i in out}
    assert "Wrapper<int>" in labels
    assert "Wrapper<internal::Detail>" in labels
    assert "identity<int>" in labels
    detail_inst = labels["Wrapper<internal::Detail>"]
    assert detail_inst.args[0].target_qname == "internal::Detail"
    assert detail_inst.emitted_symbols  # at least the instantiated `get` member
