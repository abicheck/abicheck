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

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.buildsource.template_graph import (
    ClangTemplateGraphExtractor,
    TemplateArgUse,
    TemplateInstantiation,
    parse_clang_ast_templates,
)
from abicheck.buildsource.template_graph_fold import (
    EDGE_DECL_INSTANTIATES_TEMPLATE,
    EDGE_INSTANTIATION_EMITS_SYMBOL,
    NODE_TEMPLATE_DECL,
    NODE_TEMPLATE_INSTANTIATION,
    augment_graph_with_templates,
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
    assert inst.args == (
        TemplateArgUse("internal::Detail", "internal::Detail", "CXXRecordDecl"),
    )
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


def test_ctor_dtor_symbol_variants_match_real_compiled_binary_exports() -> None:
    """``_ctor_dtor_symbol_variants`` -- verified against real clang AST +
    real compiled-object ``nm`` output (Codex review, fresh evidence):
    clang's AST only ever reports the ``C1``/``D1`` complete-object
    mangling, but the compiled binary separately exports ``C2`` alongside
    every ``C1``, and ``D0``/``D2`` alongside every (virtual) ``D1``."""
    from abicheck.buildsource.template_graph import _ctor_dtor_symbol_variants

    assert _ctor_dtor_symbol_variants("_ZN3BoxIiEC1Ev", is_ctor=True) == (
        "_ZN3BoxIiEC2Ev",
    )
    assert _ctor_dtor_symbol_variants("_ZN3BoxIiEC1Eii", is_ctor=True) == (
        "_ZN3BoxIiEC2Eii",
    )
    assert _ctor_dtor_symbol_variants("_ZN3BoxIiED1Ev", is_ctor=False) == (
        "_ZN3BoxIiED0Ev",
        "_ZN3BoxIiED2Ev",
    )
    # No marker found (not a ctor/dtor-shaped mangling) -- degrades to no
    # derived variants rather than guessing.
    assert _ctor_dtor_symbol_variants("_ZN3BoxIiE5valueE", is_ctor=True) == ()


def test_ctor_dtor_symbol_variants_does_not_mistake_class_name_for_the_marker() -> None:
    """A real correctness bug in an earlier revision (Codex review, second
    round, fresh evidence), confirmed against real clang output: a class
    literally named ``C1Evil<int>`` mangles to ``_ZN6C1EvilIiEC1Ev``, and a
    naive ``"C1E"`` substring search finds the *class name*'s own embedded
    ``"C1E"`` first (inside ``6C1Evil``), not the real ctor code that
    follows it -- deriving ``_ZN6C2EvilIiEC1Ev``, which happens to be the
    real constructor mangling of the genuinely different class
    ``C2Evil<int>`` if that class also exists and is exported. The
    structural (length-prefix-aware) locator must skip the whole
    ``6C1Evil`` identifier as one unit and correctly derive this class's
    own ``C2`` sibling instead."""
    from abicheck.buildsource.template_graph import _ctor_dtor_symbol_variants

    assert _ctor_dtor_symbol_variants("_ZN6C1EvilIiEC1Ev", is_ctor=True) == (
        "_ZN6C1EvilIiEC2Ev",
    )


def test_class_template_instantiation_includes_constructor_sibling_symbol() -> None:
    """End to end through :func:`parse_clang_ast_templates`: an
    instantiated constructor's ``emitted_symbols`` includes both the
    AST-reported ``C1`` mangling and the derived ``C2`` sibling the real
    binary also exports (Codex review, fresh evidence)."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Box",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "CXXRecordDecl",
                        "name": "Box",
                        "completeDefinition": True,
                        "inner": [],
                    },
                    {
                        "id": "0xSPEC_BOX_CTOR",
                        "kind": "ClassTemplateSpecializationDecl",
                        "name": "Box",
                    },
                ],
            },
            {
                "id": "0xSPEC_BOX_CTOR",
                "kind": "ClassTemplateSpecializationDecl",
                "name": "Box",
                "completeDefinition": True,
                "inner": [
                    {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "int"},
                        "inner": [],
                    },
                    {
                        "kind": "CXXConstructorDecl",
                        "name": "Box",
                        "mangledName": "_ZN3BoxIiEC1Ev",
                    },
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].emitted_symbols == ("_ZN3BoxIiEC1Ev", "_ZN3BoxIiEC2Ev")


def test_class_template_instantiation_includes_static_data_member_symbol() -> None:
    """An instantiated *static data member* carries its mangled name on a
    ``VarDecl`` child, not one of the member-function kinds -- Codex review,
    verified empirically against real clang AST output (``template
    <typename T> struct Box { static T value; };`` explicitly instantiated
    as ``Box<int>`` puts ``_ZN3BoxIiE5valueE`` on a direct ``VarDecl``
    child). An earlier revision only scanned member-function kinds, so this
    symbol was silently dropped from ``emitted_symbols`` even though the
    instantiation genuinely emits it."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Box",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "CXXRecordDecl",
                        "name": "Box",
                        "completeDefinition": True,
                        "inner": [],
                    },
                    {
                        "id": "0xSPEC_BOX_INT",
                        "kind": "ClassTemplateSpecializationDecl",
                        "name": "Box",
                    },
                ],
            },
            {
                "id": "0xSPEC_BOX_INT",
                "kind": "ClassTemplateSpecializationDecl",
                "name": "Box",
                "completeDefinition": True,
                "inner": [
                    {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "int"},
                        "inner": [],
                    },
                    {
                        "kind": "VarDecl",
                        "name": "value",
                        "mangledName": "_ZN3BoxIiE5valueE",
                    },
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].label == "Box<int>"
    assert out[0].emitted_symbols == ("_ZN3BoxIiE5valueE",)


def test_class_template_instantiation_ignores_non_static_field() -> None:
    """A direct-child ``VarDecl`` scan must not pick up an ordinary field:
    a non-static field has no linkage, so clang never gives it a
    ``mangledName`` -- the existing truthiness guard on ``mangledName``
    already excludes it without needing kind-specific filtering, verified
    here so the static-data-member fix above can't regress into treating
    every field as an emitted symbol."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Box",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "CXXRecordDecl",
                        "name": "Box",
                        "completeDefinition": True,
                        "inner": [],
                    },
                    {
                        "id": "0xSPEC_BOX_INT2",
                        "kind": "ClassTemplateSpecializationDecl",
                        "name": "Box",
                        "completeDefinition": True,
                        "inner": [
                            {
                                "kind": "TemplateArgument",
                                "type": {"qualType": "int"},
                                "inner": [],
                            },
                            {"kind": "FieldDecl", "name": "member"},
                        ],
                    },
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].emitted_symbols == ()


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


def test_explicit_function_specialization_detached_from_its_template_decl() -> None:
    """An *explicit* function-template specialization (``template<> int
    foo<int>(int)``) exhibits the same detachment quirk this module's
    docstring already documents for class templates: clang nests an
    unmangled stub (no ``TemplateArgument`` child, no content) under the
    ``FunctionTemplateDecl``, then detaches the real, mangled content as a
    top-level sibling sharing the stub's own id (Codex review, empirically
    confirmed against real clang AST output). Must still resolve to one
    instantiation, not be silently skipped as "the pattern itself"."""
    stub = {"kind": "FunctionDecl", "name": "foo", "id": "0xSPEC_FOO"}
    pattern = {"kind": "FunctionDecl", "name": "foo", "inner": []}
    function_template = {
        "kind": "FunctionTemplateDecl",
        "name": "foo",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            pattern,
            stub,
        ],
    }
    detached_full = {
        "kind": "FunctionDecl",
        "name": "foo",
        "id": "0xSPEC_FOO",  # same id as the nested stub above
        "mangledName": "_Z3fooIiEiT_",
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [function_template, detached_full]}
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    inst = out[0]
    assert inst.kind == "function"
    assert inst.template_qname == "foo"
    assert inst.label == "foo<int>"
    assert inst.emitted_symbols == ("_Z3fooIiEiT_",)


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


def test_variadic_pack_arguments_are_flattened_not_dropped() -> None:
    """A variadic template's pack argument is *itself* one
    ``TemplateArgument`` node (``isPack: true``, no ``type``/``value`` of
    its own) whose real per-element arguments are nested one level deeper
    in its own ``inner`` -- verified empirically against real clang AST
    output (Codex review): ``Pack<int>`` and ``Pack<double>`` both produce
    this pack-wrapper shape, and treating the wrapper as a plain,
    unspellable ``TemplateArgument`` (an earlier revision) dropped the
    whole pack, collapsing both instantiations onto the identical,
    argument-less label ``"Pack"``."""

    def pack_spec(spec_id: str, arg_type: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Pack",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "isPack": True,
                    "inner": [
                        {"kind": "TemplateArgument", "type": {"qualType": arg_type}},
                    ],
                },
            ],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Pack",
                "inner": [
                    pack_spec("0xPINT", "int"),
                    pack_spec("0xPDOUBLE", "double"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    by_label = {i.label: i for i in out}
    assert set(by_label) == {"Pack<int>", "Pack<double>"}
    assert by_label["Pack<int>"].args == (TemplateArgUse("int"),)
    assert by_label["Pack<double>"].args == (TemplateArgUse("double"),)


def test_template_template_argument_instantiation_is_skipped_not_merged() -> None:
    """A template-template argument (a template passed as a template
    argument, e.g. ``Use<A>``/``Use<B>`` for ``template <template <typename>
    class C> struct Use;``) produces a completely bare ``TemplateArgument``
    node -- no ``type``, ``value``, ``isPack``, or ``inner`` at all --
    verified empirically against real clang AST output (Codex review):
    clang's ``-ast-dump=json`` serializes zero identifying information for
    this argument shape, so ``Use<A>`` and ``Use<B>`` are indistinguishable
    from the dump alone. Silently dropping the opaque argument (an earlier
    revision) would still emit a TemplateInstantiation for each -- both
    reducing to the identical, argument-less label ``"Use"`` and merging two
    genuinely distinct instantiations' emitted-symbol/type-dependency edges
    onto one shared node. The fix skips the instantiation entirely rather
    than record a wrong, merged identity."""

    def use_spec(spec_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Use",
            "completeDefinition": True,
            "inner": [{"kind": "TemplateArgument"}],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Use",
                "inner": [
                    use_spec("0xUA"),
                    use_spec("0xUB"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert out == []


def test_auto_nttp_bare_value_argument_instantiation_is_skipped_not_merged() -> None:
    """An ``auto`` non-type template parameter (``template <auto V> struct
    A;``) whose deduced type is a scoped enum produces a bare ``{"value":
    N}`` ``TemplateArgument`` -- no ``type`` field distinguishing the
    *deduced* type at all -- verified empirically against real clang 18 AST
    output (Codex review): ``A<E1::X>`` and ``A<E2::X>`` (two different
    enum types sharing the same underlying value ``1``) both reduce to the
    identical label ``"A<1>"`` if the bare value is trusted, colliding onto
    one graph node and merging their genuinely distinct emitted-symbol
    edges. The fix skips the instantiation entirely rather than record a
    wrong, merged identity -- the same discipline already applied to an
    opaque template-template argument."""

    def a_spec(spec_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "completeDefinition": True,
            "inner": [{"kind": "TemplateArgument", "value": 1}],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "inner": [
                    {
                        "kind": "NonTypeTemplateParmDecl",
                        "name": "V",
                        "type": {"qualType": "auto"},
                    },
                    {"kind": "CXXRecordDecl", "name": "A", "inner": []},
                    a_spec("0xA_E1"),
                    a_spec("0xA_E2"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert out == []


def test_plain_int_nttp_bare_value_argument_still_resolves() -> None:
    """A concrete, non-``auto`` NTTP (``template <int V>``) carries no such
    ambiguity -- its type is fixed and known from the parameter declaration
    itself, so the identical bare ``{"value": N}`` shape means the
    identical, single-typed argument every time. Only an ``auto`` NTTP
    parameter triggers the skip above; this, the far more common shape,
    must keep resolving normally."""

    def a_spec(spec_id: str, value: int) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "A",
            "completeDefinition": True,
            "inner": [{"kind": "TemplateArgument", "value": value}],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "inner": [
                    {
                        "kind": "NonTypeTemplateParmDecl",
                        "name": "V",
                        "type": {"qualType": "int"},
                    },
                    {"kind": "CXXRecordDecl", "name": "A", "inner": []},
                    a_spec("0xA_1", 1),
                    a_spec("0xA_2", 2),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    labels = {i.label for i in out}
    assert labels == {"A<1>", "A<2>"}


def test_out_of_line_member_template_definition_recovers_owner_scope() -> None:
    """An out-of-line member-template *definition* (``template <class T>
    void C::f(T) {}``, the class's own declaration ``template <class T>
    void f(T);`` staying in-class) detaches as a *separate*, top-level
    ``FunctionTemplateDecl`` -- not nested inside ``CXXRecordDecl:C`` at
    all -- confirmed empirically against real clang AST output (Codex
    review, fresh evidence): its own children resolve (via the existing
    id-keyed join) to the identical instantiated symbols the correctly-
    scoped in-class declaration already captures, so this node's own bare
    structural scope (``[]``) would otherwise mint a *second*,
    incorrectly-scoped ``"f"`` template_qname that could coincidentally
    merge with an unrelated global template also named ``f``. Clang emits
    ``parentDeclContextId`` on exactly this node shape -- the real
    semantic owner's own id -- which this module must resolve through
    ``id_to_qname`` the same way any other decl id already is."""
    in_class_decl = {
        "kind": "FunctionTemplateDecl",
        "name": "f",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {"kind": "CXXMethodDecl", "name": "f", "inner": []},
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZN1C1fIiEEvT_",
                "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int"}}],
            },
        ],
    }
    # The out-of-line definition: a separate, top-level FunctionTemplateDecl
    # carrying parentDeclContextId pointing at C's own id -- its own child
    # is directly mangled (bypassing the unmangled-stub join, which is
    # already covered elsewhere) so this test stays focused on the qname
    # recovery mechanism itself.
    out_of_line_def = {
        "kind": "FunctionTemplateDecl",
        "name": "f",
        "parentDeclContextId": "0xC_ID",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {
                "kind": "CXXMethodDecl",
                "name": "f",
                "mangledName": "_ZN1C1fIiEEvT_",
                "inner": [{"kind": "TemplateArgument", "type": {"qualType": "int"}}],
            },
        ],
    }
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "C",
                "id": "0xC_ID",
                "inner": [in_class_decl],
            },
            out_of_line_def,
        ],
    }
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert function_insts
    qnames = {i.template_qname for i in function_insts}
    assert qnames == {"C::f"}  # never the bare, unowned "f"


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
    assert out[0].args == (
        TemplateArgUse("internal::DetailAlias", "internal::Detail", "CXXRecordDecl"),
    )


def test_pointer_wrapped_argument_resolves_the_nested_decl() -> None:
    """A pointer/reference/array/cv-qualified template argument nests its
    ``RecordType``/``decl`` one or more wrapper levels deep instead of as a
    direct ``TemplateArgument`` child -- ``Box<internal::Detail *>``
    produces ``TemplateArgument -> PointerType -> RecordType -> decl``
    (verified empirically against real clang AST output, Codex review: an
    earlier revision only checked *direct* children, so a wrapped argument's
    target_qname stayed unset and emitted no TEMPLATE_USES_TYPE edge)."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "internal",
                "inner": [
                    {"id": "0xDETAIL_ID", "kind": "CXXRecordDecl", "name": "Detail"},
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
                                "type": {"qualType": "internal::Detail *"},
                                "inner": [
                                    {
                                        "kind": "PointerType",
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
                                    }
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].args == (
        TemplateArgUse("internal::Detail *", "internal::Detail", "CXXRecordDecl"),
    )


def test_nested_specialization_argument_disambiguated_by_its_own_args() -> None:
    """Two distinct specializations of the *same* template
    (``Wrapper<int>``/``Wrapper<double>``), each used as a nested template
    argument to a second template (``Outer<Wrapper<int>>``/
    ``Outer<Wrapper<double>>``), must resolve to two distinct
    ``target_qname``s -- not collide on the shared *bare*,
    unparameterized ``ClassTemplateSpecializationDecl.name`` ("Wrapper") both
    specializations carry (Codex review, empirically confirmed against real
    clang AST output before this fix: both nested arguments resolved to the
    identical bare ``"Wrapper"``)."""

    def wrapper_spec(spec_id: str, arg_type: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Wrapper",
            "completeDefinition": True,
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": arg_type}},
            ],
        }

    def outer_spec(spec_id: str, arg_spelling: str, wrapper_spec_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Outer",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "type": {"qualType": arg_spelling},
                    "inner": [
                        {
                            "kind": "RecordType",
                            "decl": {
                                "id": wrapper_spec_id,
                                "kind": "ClassTemplateSpecializationDecl",
                                "name": "Wrapper",
                            },
                        }
                    ],
                },
            ],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Wrapper",
                "inner": [
                    wrapper_spec("0xWINT", "int"),
                    wrapper_spec("0xWDOUBLE", "double"),
                ],
            },
            {
                "kind": "ClassTemplateDecl",
                "name": "Outer",
                "inner": [
                    outer_spec("0xOINT", "Wrapper<int>", "0xWINT"),
                    outer_spec("0xODOUBLE", "Wrapper<double>", "0xWDOUBLE"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    by_label = {i.label: i for i in out}
    outer_int = by_label["Outer<Wrapper<int>>"]
    outer_double = by_label["Outer<Wrapper<double>>"]
    assert outer_int.args[0].target_qname == "Wrapper<int>"
    assert outer_double.args[0].target_qname == "Wrapper<double>"
    assert outer_int.args[0].target_qname != outer_double.args[0].target_qname


def test_outer_argument_naming_an_opaque_nested_specialization_stays_unresolved() -> (
    None
):
    """A nested specialization whose *own* argument is opaque to the JSON
    AST parser (a template-template argument -- see
    :func:`_is_opaque_template_argument`) must not collapse to its bare,
    unparameterized primary-template name when it's itself used as an
    *outer* template argument (Codex review, fresh evidence, verified
    empirically against real clang AST output): ``Outer<Use<A>>`` and
    ``Outer<Use<B>>`` -- where ``Use<A>``/``Use<B>`` both take an opaque
    template-template argument -- previously both resolved their outer
    argument's ``target_qname`` to the identical ambiguous ``"Use"``, so
    both would emit a ``TEMPLATE_USES_TYPE`` edge to the *same* graph node,
    falsely merging dependencies on two genuinely distinct specializations.
    The outer instantiation *nodes* themselves stay correctly distinct
    (each keeps its own full spelling); only the argument's own
    ``target_qname`` must degrade to unresolved (``None``, no edge) rather
    than a merged one."""

    def use_spec(spec_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Use",
            "completeDefinition": True,
            "inner": [
                # An opaque template-template argument: no type/value/isPack.
                {"kind": "TemplateArgument"},
            ],
        }

    def outer_spec(spec_id: str, arg_spelling: str, use_spec_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Outer",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "type": {"qualType": arg_spelling},
                    "inner": [
                        {
                            "kind": "RecordType",
                            "decl": {
                                "id": use_spec_id,
                                "kind": "ClassTemplateSpecializationDecl",
                                "name": "Use",
                            },
                        }
                    ],
                },
            ],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Use",
                "inner": [use_spec("0xUSEA"), use_spec("0xUSEB")],
            },
            {
                "kind": "ClassTemplateDecl",
                "name": "Outer",
                "inner": [
                    outer_spec("0xOA", "Use<A>", "0xUSEA"),
                    outer_spec("0xOB", "Use<B>", "0xUSEB"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    by_label = {i.label: i for i in out}
    outer_a = by_label["Outer<Use<A>>"]
    outer_b = by_label["Outer<Use<B>>"]
    # The outer instantiation nodes themselves stay correctly distinct...
    assert outer_a.label != outer_b.label
    # ...but the argument's own target must be unresolved, not a merged
    # "Use" both would otherwise share.
    assert outer_a.args[0].target_qname is None
    assert outer_b.args[0].target_qname is None


def test_instantiation_file_inherits_sticky_location_from_an_earlier_sibling() -> None:
    """Clang emits ``loc.file`` only on the very *first* node with a location
    in a TU -- every later sibling (including a ``ClassTemplateSpecialization
    Decl``/``FunctionDecl`` this module builds a :class:`TemplateInstantiation`
    from) carries none at all (Codex review, empirically confirmed against
    real clang output: a real two-declaration single-file TU records
    ``loc.file`` on the first top-level declaration only). An earlier
    revision called the stateless ``_node_file`` directly at each
    instantiation site, which only ever sees the file on that first
    declaration -- every other instantiation's ``file``/``defined_in_project``
    silently stayed unset. The sticky value from an *earlier* top-level
    sibling (an unrelated ``NamespaceDecl`` opening the file) must carry
    forward to a later sibling with no ``loc`` of its own."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "api",
                "loc": {"file": "t.cpp"},
                "inner": [],
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
                            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                        ],
                    },
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].file == "t.cpp"


def test_specialization_file_prefers_the_full_definition_over_the_header_stub() -> None:
    """When a template is declared in a header but explicitly specialized in
    a ``.cpp``, clang emits the header-located, loc-less stub (nested under
    the primary ``ClassTemplateDecl``, whose own inherited sticky file is
    the *header*, since the header's declarations are visited first) before
    the detached full specialization -- which DOES carry its own explicit
    ``loc.file`` (the real ``.cpp``) -- confirmed empirically against a real
    clang dump of ``template <typename T> struct C { ... };`` (header) plus
    ``template <> struct C<int> { ... };`` (cpp): the id-keyed file index
    must prefer the full definition's own explicit location over the stub's
    inherited one, or the specialization is permanently misattributed to
    the header and loses its ``defined_in_project`` provenance entirely
    (Codex review, fresh evidence)."""
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "C",
                "loc": {"file": "prim.hpp"},
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {"kind": "CXXRecordDecl", "name": "C"},
                    # The stub: same id as the full definition below, no
                    # loc of its own -- inherits the header's sticky file.
                    {
                        "id": "0xSPEC",
                        "kind": "ClassTemplateSpecializationDecl",
                        "name": "C",
                    },
                ],
            },
            # The detached full definition: same id, but its OWN explicit
            # loc.file is the real .cpp where the specialization is written.
            {
                "id": "0xSPEC",
                "kind": "ClassTemplateSpecializationDecl",
                "name": "C",
                "completeDefinition": True,
                "loc": {"file": "impl.cpp"},
                "inner": [
                    {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].file == "impl.cpp"


def test_nested_type_inside_specialization_disambiguated_by_its_own_args() -> None:
    """A ``ClassTemplateSpecializationDecl``'s own *nested* declarations
    (e.g. ``Wrapper<int>::Nested``) must scope under the specialization's
    own arguments, not its bare, unparameterized name -- otherwise two
    distinct specializations' nested types (``Wrapper<int>::Nested`` vs.
    ``Wrapper<double>::Nested``) both index as the identical bare
    ``"Wrapper::Nested"`` and collide onto one type node (Codex review,
    empirically confirmed against real clang AST output)."""

    def wrapper_spec(spec_id: str, arg_type: str, nested_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Wrapper",
            "completeDefinition": True,
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": arg_type}},
                {"id": nested_id, "kind": "CXXRecordDecl", "name": "Nested"},
            ],
        }

    def box_spec(spec_id: str, arg_spelling: str, nested_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Box",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "type": {"qualType": arg_spelling},
                    "inner": [
                        {
                            "kind": "RecordType",
                            "decl": {
                                "id": nested_id,
                                "kind": "CXXRecordDecl",
                                "name": "Nested",
                            },
                        }
                    ],
                },
            ],
        }

    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Wrapper",
                "inner": [
                    wrapper_spec("0xWINT", "int", "0xNESTED_INT"),
                    wrapper_spec("0xWDOUBLE", "double", "0xNESTED_DOUBLE"),
                ],
            },
            {
                "kind": "ClassTemplateDecl",
                "name": "Box",
                "inner": [
                    box_spec("0xBINT", "Wrapper<int>::Nested", "0xNESTED_INT"),
                    box_spec("0xBDOUBLE", "Wrapper<double>::Nested", "0xNESTED_DOUBLE"),
                ],
            },
        ],
    }
    out = parse_clang_ast_templates(ast)
    by_label = {i.label: i for i in out}
    box_int = by_label["Box<Wrapper<int>::Nested>"]
    box_double = by_label["Box<Wrapper<double>::Nested>"]
    assert box_int.args[0].target_qname == "Wrapper<int>::Nested"
    assert box_double.args[0].target_qname == "Wrapper<double>::Nested"
    assert box_int.args[0].target_qname != box_double.args[0].target_qname


def _overloaded_function_template_ast() -> dict:
    """Two distinct ``FunctionTemplateDecl`` nodes both named ``f`` (real
    overloads, differing by arity), each instantiated with ``T=int`` --
    matches real clang output (verified empirically): the parser gives both
    the identical *label* ``"f<int>"`` since a label is built only from
    template arguments, never arity/signature."""
    one_arg = {
        "kind": "FunctionTemplateDecl",
        "name": "f",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {
                "kind": "FunctionDecl",
                "name": "f",
                "type": {"qualType": "T (T)"},
                "inner": [],
            },
            {
                "kind": "FunctionDecl",
                "name": "f",
                "mangledName": "_Z1fIiET_S0_",
                "inner": [
                    {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                ],
            },
        ],
    }
    two_arg = {
        "kind": "FunctionTemplateDecl",
        "name": "f",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {
                "kind": "FunctionDecl",
                "name": "f",
                "type": {"qualType": "T (T, T)"},
                "inner": [],
            },
            {
                "kind": "FunctionDecl",
                "name": "f",
                "mangledName": "_Z1fIiET_S0_S0_",
                "inner": [
                    {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                ],
            },
        ],
    }
    return {"kind": "TranslationUnitDecl", "inner": [one_arg, two_arg]}


def test_overloaded_function_templates_get_distinct_instantiation_nodes() -> None:
    """Two overloads of the same function template (``f<T>(T)`` vs.
    ``f<T>(T,T)``), both instantiated with identical template arguments,
    produce the identical *label* -- but must not collapse onto one graph
    node (Codex review, empirically confirmed against real clang output:
    before this fix, only one DECL_INSTANTIATES_TEMPLATE edge survived for
    two genuinely distinct instantiations)."""
    ast = _overloaded_function_template_ast()
    out = parse_clang_ast_templates(ast)
    assert len(out) == 2
    assert {i.label for i in out} == {"f<int>"}  # identical label, by design
    assert {i.emitted_symbols for i in out} == {
        ("_Z1fIiET_S0_",),
        ("_Z1fIiET_S0_S0_",),
    }

    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="binary_symbol://_Z1fIiET_S0_", kind="binary_symbol"))
    graph.add_node(
        GraphNode(id="binary_symbol://_Z1fIiET_S0_S0_", kind="binary_symbol")
    )
    augment_graph_with_templates(graph, out)
    inst_nodes = [n for n in graph.nodes if n.kind == NODE_TEMPLATE_INSTANTIATION]
    assert len(inst_nodes) == 2  # not collapsed onto one node
    assert {n.id for n in inst_nodes} == {
        template_instantiation_node_id("f<int>", "_Z1fIiET_S0_"),
        template_instantiation_node_id("f<int>", "_Z1fIiET_S0_S0_"),
    }
    emits_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_INSTANTIATION_EMITS_SYMBOL
    }
    assert emits_edges == {
        (
            template_instantiation_node_id("f<int>", "_Z1fIiET_S0_"),
            "binary_symbol://_Z1fIiET_S0_",
        ),
        (
            template_instantiation_node_id("f<int>", "_Z1fIiET_S0_S0_"),
            "binary_symbol://_Z1fIiET_S0_S0_",
        ),
    }
    # The two overloads' abstract *declarations* must also stay distinct --
    # the instantiation-id fix above didn't close this: both DECL_INSTANTIATES_
    # TEMPLATE edges previously still terminated at one shared template_decl://f
    # node, since template_qname is name-only ("f" for both) (Codex review,
    # empirically confirmed against real clang output).
    decl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    assert len(decl_nodes) == 2
    assert decl_nodes == {
        template_decl_node_id("f", "TemplateTypeParmDecl|T (T)"),
        template_decl_node_id("f", "TemplateTypeParmDecl|T (T, T)"),
    }
    instantiates_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_DECL_INSTANTIATES_TEMPLATE
    }
    assert instantiates_edges == {
        (
            template_instantiation_node_id("f<int>", "_Z1fIiET_S0_"),
            template_decl_node_id("f", "TemplateTypeParmDecl|T (T)"),
        ),
        (
            template_instantiation_node_id("f<int>", "_Z1fIiET_S0_S0_"),
            template_decl_node_id("f", "TemplateTypeParmDecl|T (T, T)"),
        ),
    }


def test_darwin_double_underscore_mangled_name_joins_via_fallback() -> None:
    """On Darwin, clang's AST reports a mangled name with the extra Mach-O
    leading underscore attached (``__Z...``), but a ``binary_symbol`` node
    carries the already-stripped form. ``emitted_symbols`` now keeps
    clang's raw spelling; the strip happens as a guarded fallback inside
    :func:`augment_graph_with_templates` itself, not at collection time --
    see ``test_template_graph_identity.py``'s asm-label regression."""
    ast = _function_template_ast()
    # Simulate the Darwin mangling convention on the one instantiation
    # (inner[0]=TemplateTypeParmDecl, inner[1]=pattern, inner[2]=instantiation).
    ast["inner"][0]["inner"][0]["inner"][2]["mangledName"] = "__Z8identityIiET_S0_"
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].emitted_symbols == ("__Z8identityIiET_S0_",)  # raw, not stripped

    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(id="binary_symbol://_Z8identityIiET_S0_", kind="binary_symbol")
    )
    added = augment_graph_with_templates(graph, out)
    assert any(e.kind == EDGE_INSTANTIATION_EMITS_SYMBOL for e in graph.edges)
    assert added >= 1


def _cross_class_member_template_ast() -> dict:
    """Two unrelated class templates, each with an identically-named member
    function template ``apply``, both instantiated with the same argument --
    matches real clang output (verified empirically): a
    ``ClassTemplateSpecializationDecl``'s own nested ``FunctionTemplateDecl``
    needs the specialization's name in scope, or the two collapse onto one
    qname.

    The specialization's own ``apply`` shares the identical ``loc`` with the
    primary pattern's own ``apply`` -- matching real clang output for a
    genuinely shared member (this is what :func:`_primary_pattern_member_locs`
    keys its "shared vs. independently-authored" decision on, not merely
    structural position)."""

    def holder(class_name: str, mangled_prefix: str, loc_offset: int) -> dict:
        pattern_apply = {
            "kind": "FunctionTemplateDecl",
            "name": "apply",
            "loc": {"offset": loc_offset},
            "inner": [{"kind": "TemplateTypeParmDecl", "name": "U"}],
        }
        spec = {
            "id": f"0xSPEC_{class_name}",
            "kind": "ClassTemplateSpecializationDecl",
            "name": class_name,
            "completeDefinition": True,
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": "int"}},
                {
                    "kind": "FunctionTemplateDecl",
                    "name": "apply",
                    "loc": {"offset": loc_offset},
                    "inner": [
                        {"kind": "TemplateTypeParmDecl", "name": "U"},
                        {"kind": "FunctionDecl", "name": "apply", "inner": []},
                        {
                            "kind": "FunctionDecl",
                            "name": "apply",
                            "mangledName": f"_ZN3api{mangled_prefix}5applyIiEET_S3_",
                            "inner": [
                                {
                                    "kind": "TemplateArgument",
                                    "type": {"qualType": "int"},
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        return {
            "kind": "ClassTemplateDecl",
            "name": class_name,
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {"kind": "CXXRecordDecl", "name": class_name, "inner": [pattern_apply]},
                spec,
            ],
        }

    ns = {
        "kind": "NamespaceDecl",
        "name": "api",
        "inner": [
            holder("Holder", "6HolderIiE", 100),
            holder("Wrapper", "7WrapperIiE", 200),
        ],
    }
    return {"kind": "TranslationUnitDecl", "inner": [ns]}


def test_member_template_in_different_classes_does_not_collide_on_qname() -> None:
    """``api::Holder``'s and ``api::Wrapper``'s own member function template
    ``apply`` must resolve to distinct qnames -- both instantiated with the
    same argument previously resolved to the identical bare ``"api::apply"``
    (ClassTemplateSpecializationDecl isn't in _SCOPE_DECL_KINDS, so the
    specialization's own name never entered scope), so both instantiations'
    DECL_INSTANTIATES_TEMPLATE edge pointed at one shared template_decl node
    as if they instantiated the same template (Codex review, empirically
    confirmed against real clang output)."""
    ast = _cross_class_member_template_ast()
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert len(function_insts) == 2
    qnames = {i.template_qname for i in function_insts}
    assert qnames == {"api::Holder::apply", "api::Wrapper::apply"}

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    tdecl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    assert (
        template_decl_node_id("api::Holder::apply", "TemplateTypeParmDecl|")
        in tdecl_nodes
    )
    assert (
        template_decl_node_id("api::Wrapper::apply", "TemplateTypeParmDecl|")
        in tdecl_nodes
    )
    assert len([n for n in tdecl_nodes if "apply" in n]) == 2  # not one shared node


def test_member_template_in_explicit_specializations_does_not_collide_on_qname() -> (
    None
):
    """Two *explicit* class-template specializations each independently
    declaring their own same-named, same-signature member template must
    resolve to distinct ``template_decl`` identities -- Codex review,
    second round, fresh evidence, verified empirically against real clang
    AST output: ``template<> struct H<int> { template<typename U> void
    f(U); };`` and ``template<> struct H<double> { template<typename U>
    void f(U); };`` each write their *own* ``f`` from scratch (``H`` has no
    generic member ``f`` on its primary template at all), so ``H<int>::f``
    and ``H<double>::f`` are genuinely unrelated declarations that merely
    share a spelling and signature -- unlike
    :func:`test_member_template_in_different_classes_does_not_collide_on_qname`
    above (an *implicit* instantiation of one shared primary-pattern
    member, which correctly keeps sharing one ``template_decl`` node), an
    earlier revision still collapsed these two onto the identical bare
    qname ``"H::f"``."""

    def explicit_h_spec(
        spec_id: str, arg_type: str, mangled_prefix: str
    ) -> tuple[dict, dict]:
        """``(stub nested under ClassTemplateDecl, full content detached
        to a top-level sibling)`` -- the same explicit-specialization
        detachment quirk this module's docstring documents for
        class-level resolution. ``arg_type`` is *this* specialization's
        own outer template argument (``H<arg_type>``), distinct per call
        so the two specializations don't share an identity themselves."""
        stub = {"id": spec_id, "kind": "ClassTemplateSpecializationDecl", "name": "H"}
        full = {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "H",
            "completeDefinition": True,
            "inner": [
                {"kind": "TemplateArgument", "type": {"qualType": arg_type}},
                {
                    "kind": "FunctionTemplateDecl",
                    "name": "f",
                    "inner": [
                        {"kind": "TemplateTypeParmDecl", "name": "U"},
                        {"kind": "FunctionDecl", "name": "f", "inner": []},
                        {
                            "kind": "FunctionDecl",
                            "name": "f",
                            "mangledName": f"_ZN1H{mangled_prefix}1fIiEEvT_",
                            "inner": [
                                {
                                    "kind": "TemplateArgument",
                                    "type": {"qualType": "int"},
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        return stub, full

    int_stub, int_full = explicit_h_spec("0xSPEC_HINT", "int", "IiE")
    double_stub, double_full = explicit_h_spec("0xSPEC_HDOUBLE", "double", "IdE")
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "H",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {"kind": "CXXRecordDecl", "name": "H"},
                    int_stub,
                    double_stub,
                ],
            },
            int_full,
            double_full,
        ],
    }
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert len(function_insts) == 2
    qnames = {i.template_qname for i in function_insts}
    assert qnames == {"H<int>::f", "H<double>::f"}

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    tdecl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    assert len([n for n in tdecl_nodes if "::f" in n]) == 2  # not one shared node


def test_explicit_instantiation_definition_member_still_shares_the_primary_decl() -> (
    None
):
    """An *explicit instantiation definition* (``template struct
    Holder<int>;`` -- forcing the primary pattern's own shared body without
    writing a new one) detaches to a top-level sibling the *identical* way
    an explicit *specialization*'s own independently-authored content does
    (Codex review, second round, fresh evidence, empirically confirmed
    against real clang AST output): an earlier revision decided bare-vs-
    disambiguated scope from *where* a specialization was reached (nested
    under its own ClassTemplateDecl vs. a detached top-level sibling), on
    the assumption that only an explicit specialization detaches -- but a
    real ``template struct Holder<int>;`` combined with an implicit
    ``Holder<double>`` use produces exactly this shape too, and
    ``Holder<int>::apply``'s member is the *same* declaration as
    ``Holder<double>::apply``'s (``apply`` is written once, in the primary
    pattern) -- wrongly disambiguating it split one shared declaration into
    two. This module now decides from source identity (matching ``loc``
    against the primary pattern's own member -- see
    :func:`_primary_pattern_member_locs`) rather than structural position,
    so both must still resolve to the same qname/template_decl."""
    pattern_apply = {
        "kind": "FunctionTemplateDecl",
        "name": "apply",
        "loc": {"offset": 100},
        "inner": [{"kind": "TemplateTypeParmDecl", "name": "U"}],
    }

    def holder_member(mangled_prefix: str) -> dict:
        return {
            "kind": "FunctionTemplateDecl",
            "name": "apply",
            "loc": {"offset": 100},  # same source position as the pattern's own
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "U"},
                {"kind": "FunctionDecl", "name": "apply", "inner": []},
                {
                    "kind": "FunctionDecl",
                    "name": "apply",
                    "mangledName": f"_ZN6Holder{mangled_prefix}5applyI{mangled_prefix[1]}EET_S2_",
                    "inner": [
                        {"kind": "TemplateArgument", "type": {"qualType": "int"}}
                    ],
                },
            ],
        }

    # The explicit-instantiation-definition Holder<int>: an empty stub
    # nested under ClassTemplateDecl, full content (with the shared-loc
    # apply member) detached to a top-level sibling.
    int_stub = {
        "id": "0xSPEC_INT",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
    }
    int_full = {
        "id": "0xSPEC_INT",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
            holder_member("IiE"),
        ],
    }
    # The implicit Holder<double>: full content nested directly, same as
    # any ordinary implicit instantiation.
    double_full = {
        "id": "0xSPEC_DOUBLE",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "double"}},
            holder_member("IdE"),
        ],
    }
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "ClassTemplateDecl",
                "name": "Holder",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "CXXRecordDecl",
                        "name": "Holder",
                        "inner": [pattern_apply],
                    },
                    int_stub,
                    double_full,
                ],
            },
            int_full,
        ],
    }
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert len(function_insts) == 2
    qnames = {i.template_qname for i in function_insts}
    assert qnames == {"Holder::apply"}  # shared, not split by detachment

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    tdecl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    assert len([n for n in tdecl_nodes if "apply" in n]) == 1  # one shared node


def test_namespace_scoped_explicit_instantiation_detached_outside_namespace_still_shares() -> (
    None
):
    """A namespace-scoped explicit instantiation written with a *qualified*
    name outside its namespace (``template struct api::Holder<int>;``)
    detaches its full-content sibling as a direct ``TranslationUnitDecl``
    child -- not nested inside ``NamespaceDecl:api`` at all -- confirmed
    empirically against real clang 18 AST output. The stub (always reached
    correctly nested inside its own ``ClassTemplateDecl``, itself correctly
    nested inside the namespace) already registers the right id ->
    ``"api::Holder"`` mapping; this module must reuse that registration for
    both the shared-member lookup *and* the scope prefix used to build the
    member's own qname, rather than trusting this walk's own structural
    scope (``[]`` for a node reached outside the namespace) -- otherwise
    ``api::Holder<int>::apply`` and ``api::Holder<double>::apply`` (the
    same syntactic, shared member) wrongly split onto two different
    qnames (Codex review, fresh evidence)."""
    pattern_apply = {
        "kind": "FunctionTemplateDecl",
        "name": "apply",
        "loc": {"offset": 100},
        "inner": [{"kind": "TemplateTypeParmDecl", "name": "U"}],
    }

    def holder_member(mangled_prefix: str) -> dict:
        return {
            "kind": "FunctionTemplateDecl",
            "name": "apply",
            "loc": {"offset": 100},  # same source position as the pattern's own
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "U"},
                {"kind": "FunctionDecl", "name": "apply", "inner": []},
                {
                    "kind": "FunctionDecl",
                    "name": "apply",
                    "mangledName": f"_ZN3api6Holder{mangled_prefix}5applyI{mangled_prefix[1]}EET_S3_",
                    "inner": [
                        {"kind": "TemplateArgument", "type": {"qualType": "int"}}
                    ],
                },
            ],
        }

    # The explicit-instantiation stub for api::Holder<int>: nested normally
    # inside its own ClassTemplateDecl, which is itself nested inside the
    # api namespace.
    int_stub = {
        "id": "0xSPEC_INT",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
    }
    # The detached full content: same id, but placed as a DIRECT
    # TranslationUnitDecl child -- outside NamespaceDecl:api entirely,
    # matching real clang output for the qualified-name-outside-namespace
    # explicit instantiation syntax.
    int_full = {
        "id": "0xSPEC_INT",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
            holder_member("IiE"),
        ],
    }
    # api::Holder<double>: an ordinary implicit instantiation, nested
    # normally inside the namespace.
    double_full = {
        "id": "0xSPEC_DOUBLE",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "double"}},
            holder_member("IdE"),
        ],
    }
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "api",
                "inner": [
                    {
                        "kind": "ClassTemplateDecl",
                        "name": "Holder",
                        "inner": [
                            {"kind": "TemplateTypeParmDecl", "name": "T"},
                            {
                                "kind": "CXXRecordDecl",
                                "name": "Holder",
                                "inner": [pattern_apply],
                            },
                            int_stub,
                            double_full,
                        ],
                    },
                ],
            },
            int_full,  # detached OUTSIDE the namespace
        ],
    }
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert len(function_insts) == 2
    qnames = {i.template_qname for i in function_insts}
    assert qnames == {"api::Holder::apply"}  # shared, not split by detachment

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    tdecl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    assert len([n for n in tdecl_nodes if "apply" in n]) == 1  # one shared node


# ── cross-TU extraction merge ────────────────────────────────────────────────


def test_extract_from_build_merges_richer_data_across_tus_instead_of_dropping_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two TUs instantiating the identical ``(kind, template_qname, label)``
    must merge -- not have the second TU's data silently dropped (Codex
    review, mirrors ``type_graph.py``'s own cross-TU ``_merge_type_edges``):
    one TU resolves an argument's ``target_qname``, another TU reaches an
    extra instantiated member the first TU's translation unit never used."""
    extractor = ClangTemplateGraphExtractor(clang_bin="clang++")
    monkeypatch.setattr(extractor, "available", lambda: True)

    per_unit = {
        "a.cpp": [
            TemplateInstantiation(
                kind="record",
                template_qname="Wrapper",
                label="Wrapper<internal::Detail>",
                args=(TemplateArgUse("internal::Detail", None),),
                emitted_symbols=("_ZN7WrapperIN8internal6DetailEE3getEv",),
                file="a.cpp",
            )
        ],
        "b.cpp": [
            TemplateInstantiation(
                kind="record",
                template_qname="Wrapper",
                label="Wrapper<internal::Detail>",
                args=(
                    TemplateArgUse(
                        "internal::Detail", "internal::Detail", "CXXRecordDecl"
                    ),
                ),
                emitted_symbols=("_ZN7WrapperIN8internal6DetailEE3setEi",),
                file="",
            )
        ],
    }
    monkeypatch.setattr(
        extractor,
        "_extract_from_compile_unit",
        lambda cu, *, diagnostics=None: per_unit[cu.source],
    )
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
        ]
    )
    out = extractor.extract_from_build(build)
    assert len(out) == 1
    merged = out[0]
    # The unresolved-in-a.cpp argument is filled in from b.cpp, not dropped.
    assert merged.args == (
        TemplateArgUse("internal::Detail", "internal::Detail", "CXXRecordDecl"),
    )
    # Both TUs' emitted members survive, a.cpp's ordered first.
    assert merged.emitted_symbols == (
        "_ZN7WrapperIN8internal6DetailEE3getEv",
        "_ZN7WrapperIN8internal6DetailEE3setEi",
    )
    # a.cpp's non-empty file wins over b.cpp's empty one.
    assert merged.file == "a.cpp"


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
        timeout=60,  # a hung compiler invocation must not hold CI (Codex review)
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
