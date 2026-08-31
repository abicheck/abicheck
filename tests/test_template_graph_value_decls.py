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

"""Split out of ``test_template_graph.py`` (at its own 2000-line hard cap)
-- TEMPLATE_USES_DECL (G29 Phase 5 item 1 follow-up): a pointer-to-
function/variable or reference NTTP argument. Fixture shapes verified
against real ``clang -ast-dump=json`` output (see
``template_graph_value_decls.py``'s own docstring); the
``@pytest.mark.integration`` test at the bottom re-verifies end to end
against a real compiler.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.buildsource.template_graph import (
    TemplateArgUse,
    TemplateInstantiation,
    parse_clang_ast_templates,
)
from abicheck.buildsource.template_graph_fold import (
    EDGE_TEMPLATE_USES_DECL,
    EDGE_TEMPLATE_USES_TYPE,
    NODE_SOURCE_DECL,
    augment_graph_with_templates,
    template_instantiation_node_id,
)

# ── TEMPLATE_USES_DECL: pointer-to-function/variable and reference NTTPs ───


def _function_pointer_nttp_ast() -> dict:
    """``template <void (*Fn)(int)> struct Callback;`` instantiated
    ``Callback<&ns::handler>`` -- shaped exactly like real clang output
    (verified empirically against Clang 18, see
    ``template_graph._template_arg_use``'s own docstring): the
    ``TemplateArgument`` node carries a top-level ``decl`` stub directly (no
    ``type``/``value`` of its own, no nesting under ``inner``), and
    ``ns::handler``'s own top-level ``FunctionDecl`` -- reachable elsewhere
    in the same TU, sharing the identical clang id with the stub -- carries
    the ``mangledName`` the stub itself omits.
    """
    handler_decl = {
        "id": "0xHANDLER",
        "kind": "FunctionDecl",
        "name": "handler",
        "mangledName": "_ZN2ns7handlerEi",
        "type": {"qualType": "void (int)"},
        "inner": [],
    }
    ns = {"kind": "NamespaceDecl", "name": "ns", "inner": [handler_decl]}
    callback_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Callback",
        "completeDefinition": True,
        "inner": [],
    }
    callback_instantiation = {
        "id": "0xSPEC_CALLBACK",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Callback",
        "completeDefinition": True,
        "inner": [
            {
                "kind": "TemplateArgument",
                "decl": {
                    "id": "0xHANDLER",
                    "kind": "FunctionDecl",
                    "name": "handler",
                    "type": {"qualType": "void (int)"},
                },
            },
            {
                "kind": "CXXMethodDecl",
                "name": "call",
                "mangledName": "_ZN8CallbackIXadL_ZN2ns7handlerEiEEE4callEi",
            },
        ],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Callback",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Fn"},
            callback_pattern,
            callback_instantiation,
        ],
    }
    return {"kind": "TranslationUnitDecl", "inner": [ns, class_template]}


def test_function_pointer_nttp_resolves_to_the_target_functions_identity() -> None:
    """A pointer-to-function NTTP argument resolves target_qname to the
    exact identity call_graph.py's own ``decl://`` node for that same
    function would use (its mangled name), not merely a placeholder id or
    the bare unqualified name -- and target_decl_kind records FunctionDecl
    so augment_graph_with_templates routes it onto TEMPLATE_USES_DECL, not
    TEMPLATE_USES_TYPE."""
    ast = _function_pointer_nttp_ast()
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    inst = out[0]
    # The label uses the *resolved* identity, not the bare "handler" spelling
    # -- see _arg_label_spelling's own docstring for why the bare spelling
    # would let two distinct same-named-callee instantiations collide.
    assert inst.label == "Callback<_ZN2ns7handlerEi>"
    assert inst.args == (TemplateArgUse("handler", "_ZN2ns7handlerEi", "FunctionDecl"),)


def test_pointer_to_variable_nttp_resolves_to_the_target_variables_identity() -> None:
    """A pointer-to-variable NTTP is the same bare ``{"kind":
    "TemplateArgument", "decl": {...VarDecl...}}`` shape as the
    pointer-to-function case (verified empirically) -- must resolve the
    same way."""
    global_decl = {
        "id": "0xGLOBAL",
        "kind": "VarDecl",
        "name": "global",
        "mangledName": "_ZN2ns6globalE",
        "type": {"qualType": "int"},
    }
    ns = {"kind": "NamespaceDecl", "name": "ns", "inner": [global_decl]}
    s_pattern = {
        "kind": "CXXRecordDecl",
        "name": "S",
        "completeDefinition": True,
        "inner": [],
    }
    s_instantiation = {
        "id": "0xSPEC_S",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "S",
        "completeDefinition": True,
        "inner": [
            {
                "kind": "TemplateArgument",
                "decl": {
                    "id": "0xGLOBAL",
                    "kind": "VarDecl",
                    "name": "global",
                    "type": {"qualType": "int"},
                },
            },
        ],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "S",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Ptr"},
            s_pattern,
            s_instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [ns, class_template]}
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].args == (TemplateArgUse("global", "_ZN2ns6globalE", "VarDecl"),)


def test_reference_nttp_resolves_to_the_target_variables_identity() -> None:
    """``template <int& R> struct Holder;`` instantiated ``Holder<ns::global>``
    -- a *reference*, not pointer, NTTP -- produces the identical bare
    ``{"kind": "TemplateArgument", "decl": {...VarDecl...}}`` shape as the
    pointer-to-variable case above (verified empirically against Clang 18:
    no ``refersToEnclosingVariableOrCapture``/pointer-specific marker
    distinguishes the two at this node), so the same resolution path
    applies unchanged."""
    global_decl = {
        "id": "0xGLOBAL",
        "kind": "VarDecl",
        "name": "global",
        "mangledName": "_ZN2ns6globalE",
        "type": {"qualType": "int"},
    }
    ns = {"kind": "NamespaceDecl", "name": "ns", "inner": [global_decl]}
    holder_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [],
    }
    holder_instantiation = {
        "id": "0xSPEC_HOLDER",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [
            {
                "kind": "TemplateArgument",
                "decl": {
                    "id": "0xGLOBAL",
                    "kind": "VarDecl",
                    "name": "global",
                    "type": {"qualType": "int"},
                },
            },
        ],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Holder",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "R"},
            holder_pattern,
            holder_instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [ns, class_template]}
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].args == (TemplateArgUse("global", "_ZN2ns6globalE", "VarDecl"),)
    assert out[0].label == "Holder<_ZN2ns6globalE>"


def test_block_scope_extern_function_nttp_target_resolves() -> None:
    """``void outer() { extern void target(); } Holder<&target>`` -- clang
    nests a block-scope function declaration under its enclosing function's
    own AST subtree purely as a matter of scoping syntax (verified
    empirically), but ``target`` still has real external linkage (Codex
    review: unlike a local *variable*, a block-scope *function* declaration
    is never scoped out just because it's lexically nested) -- must resolve
    the same way a namespace-scope declaration would, not drop the whole
    instantiation as unresolved."""
    target_decl = {
        "id": "0xTARGET",
        "kind": "FunctionDecl",
        "name": "target",
        "mangledName": "_Z6targetv",
        "type": {"qualType": "void ()"},
        "storageClass": "extern",
        "inner": [],
    }
    outer = {
        "kind": "FunctionDecl",
        "name": "outer",
        "type": {"qualType": "void ()"},
        "inner": [target_decl],
    }
    holder_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [],
    }
    holder_instantiation = {
        "id": "0xSPEC_HOLDER",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [
            {
                "kind": "TemplateArgument",
                "decl": {
                    "id": "0xTARGET",
                    "kind": "FunctionDecl",
                    "name": "target",
                    "type": {"qualType": "void ()"},
                },
            },
        ],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Holder",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Fn"},
            holder_pattern,
            holder_instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [outer, class_template]}
    out = parse_clang_ast_templates(ast)
    assert len(out) == 1
    assert out[0].args == (TemplateArgUse("target", "_Z6targetv", "FunctionDecl"),)
    assert out[0].label == "Holder<_Z6targetv>"


def test_member_function_templates_of_unresolvable_specializations_are_dropped() -> (
    None
):
    """A member function template (``g``) instantiated once per
    specialization of ``H<&A::f>``/``H<&B::f>`` -- two specializations
    that differ only in an unresolvable class-member NTTP argument (both
    spelled bare ``"f"``) -- must not collide onto one shared ``H::g``
    ``template_decl`` node, merging their distinct emitted symbols (Codex
    review, fresh evidence: verified against real clang output that an
    out-of-line member-template definition on two such specializations
    both resolved to the identical bare "H"). An earlier revision of this
    fix suffixed the bare name with the specialization's own clang node
    id as a disambiguator, but that id is a process-local address, not
    stable across separate compiler invocations of the identical TU
    (Codex review, fresh evidence, verified empirically) -- so both
    member instantiations are dropped entirely instead, the same
    "degrade, never guess" discipline the class-level instantiation
    itself already gets."""

    def _spec(spec_id: str, decl_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "H",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "decl": {
                        "id": decl_id,
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "type": {"qualType": "void ()"},
                    },
                },
            ],
        }

    spec_a = _spec("0xSPEC_A", "0xA_F")
    spec_b = _spec("0xSPEC_B", "0xB_F")
    h_pattern = {
        "kind": "CXXRecordDecl",
        "name": "H",
        "completeDefinition": True,
        "inner": [],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "H",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Ptr"},
            h_pattern,
            spec_a,
            spec_b,
        ],
    }

    def _g(parent_id: str, mangled: str) -> dict:
        return {
            "kind": "FunctionTemplateDecl",
            "name": "g",
            "parentDeclContextId": parent_id,
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "U"},
                {
                    "kind": "FunctionDecl",
                    "name": "g",
                    "mangledName": mangled,
                    "type": {"qualType": "int (int)"},
                    "inner": [],
                },
            ],
        }

    g_a = _g("0xSPEC_A", "_ZN1H1gIiEET_S1_a")
    g_b = _g("0xSPEC_B", "_ZN1H1gIiEET_S1_b")
    ast = {"kind": "TranslationUnitDecl", "inner": [class_template, g_a, g_b]}
    out = parse_clang_ast_templates(ast)
    assert out == []


def test_two_class_member_nttp_targets_drop_rather_than_collide() -> None:
    """``H<&A::f>`` and ``H<&B::f>`` -- two distinct class-member NTTP
    targets sharing a bare member name -- must not collide onto one
    ``H<f>`` label (Codex review, fresh evidence: real clang emits a
    ``CXXMethodDecl``/``FieldDecl`` ``decl`` reference for a class-member
    NTTP target, a *kind* `index_value_decls` never indexes at all --
    unlike an unindexed free function/variable, which the earlier
    unresolved-decl-argument fix already caught, a narrower
    `_VALUE_DECL_KINDS`-only check let this kind through unflagged, since
    its raw decl kind was never in that set to begin with). Both
    instantiations should drop entirely rather than fall back to a
    colliding bare spelling -- a member-function/static-data-member NTTP
    target is a known, documented gap (`index_value_decls`'s own
    docstring), not something this module resolves."""
    a_f = {
        "id": "0xA_F",
        "kind": "CXXMethodDecl",
        "name": "f",
        "type": {"qualType": "void ()"},
    }
    b_f = {
        "id": "0xB_F",
        "kind": "CXXMethodDecl",
        "name": "f",
        "type": {"qualType": "void ()"},
    }

    def _h_spec(spec_id: str, decl: dict) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "H",
            "completeDefinition": True,
            "inner": [{"kind": "TemplateArgument", "decl": decl}],
        }

    h_pattern = {
        "kind": "CXXRecordDecl",
        "name": "H",
        "completeDefinition": True,
        "inner": [],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "H",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Ptr"},
            h_pattern,
            _h_spec("0xSPEC_A", a_f),
            _h_spec("0xSPEC_B", b_f),
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [class_template]}
    out = parse_clang_ast_templates(ast)
    assert out == []


def test_two_var_template_specializations_stay_distinct() -> None:
    """``H<&ns::v<1>>`` and ``H<&ns::v<2>>`` -- two distinct instantiations
    of ``template <int N> int v = N;`` -- must not collide onto one
    ``H<v>`` label (Codex review, fresh evidence: real clang emits
    ``VarTemplateSpecializationDecl`` for a variable-template
    specialization's ``decl`` target, distinct from plain ``VarDecl`` --
    verified against real clang output that its full occurrences carry a
    genuinely distinct ``mangledName`` per instantiation, just like
    ``VarDecl``)."""
    v1 = {
        "id": "0xV1",
        "kind": "VarTemplateSpecializationDecl",
        "name": "v",
        "mangledName": "_ZN2ns1vILi1EEE",
        "type": {"qualType": "int"},
    }
    v2 = {
        "id": "0xV2",
        "kind": "VarTemplateSpecializationDecl",
        "name": "v",
        "mangledName": "_ZN2ns1vILi2EEE",
        "type": {"qualType": "int"},
    }
    var_template = {"kind": "VarTemplateDecl", "name": "v", "inner": [v1, v2]}
    ns = {"kind": "NamespaceDecl", "name": "ns", "inner": [var_template]}
    h_pattern = {
        "kind": "CXXRecordDecl",
        "name": "H",
        "completeDefinition": True,
        "inner": [],
    }

    def _h_spec(spec_id: str, decl_id: str) -> dict:
        return {
            "id": spec_id,
            "kind": "ClassTemplateSpecializationDecl",
            "name": "H",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "decl": {
                        "id": decl_id,
                        "kind": "VarTemplateSpecializationDecl",
                        "name": "v",
                        "type": {"qualType": "int"},
                    },
                },
            ],
        }

    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "H",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Ptr"},
            h_pattern,
            _h_spec("0xSPEC1", "0xV1"),
            _h_spec("0xSPEC2", "0xV2"),
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [ns, class_template]}
    out = parse_clang_ast_templates(ast)
    labels = {i.label for i in out}
    assert len(out) == 2
    assert labels == {"H<_ZN2ns1vILi1EEE>", "H<_ZN2ns1vILi2EEE>"}


def test_function_pointer_nttp_unresolved_target_drops_the_whole_instantiation() -> (
    None
):
    """The decl stub's id names a declaration this TU's AST never actually
    walks (e.g. a C++17 address-of-local-static NTTP, which
    ``index_value_decls`` deliberately never descends into, or a genuinely
    absent target) -- ``_has_unresolved_decl_argument`` (Codex review, see
    its own docstring) drops the *whole* instantiation rather than record it
    with an unresolved ``TemplateArgUse(..., None, None)``: falling back to
    the bare ``spelling`` for the instantiation's own label would let two
    genuinely distinct unresolved decl targets sharing a bare name collide
    onto one node, the same failure mode the opaque-argument guard already
    exists to prevent."""
    handler_stub_only = {
        "kind": "TemplateArgument",
        "decl": {
            "id": "0xNEVER_INDEXED",
            "kind": "FunctionDecl",
            "name": "handler",
            "type": {"qualType": "void (int)"},
        },
    }
    callback_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Callback",
        "completeDefinition": True,
        "inner": [],
    }
    callback_instantiation = {
        "id": "0xSPEC_CALLBACK",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Callback",
        "completeDefinition": True,
        "inner": [handler_stub_only],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Callback",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Fn"},
            callback_pattern,
            callback_instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [class_template]}
    out = parse_clang_ast_templates(ast)
    assert out == []


def test_decl_argument_with_no_name_drops_the_whole_instantiation() -> None:
    """A ``decl`` stub carrying no ``name`` at all (not observed in real
    clang output, but the AST-shape space this module doesn't fully
    enumerate) is unspellable -- ``_is_opaque_template_argument`` treats a
    nameless ``decl`` the same as no ``decl`` at all (CodeRabbit review), so
    the whole instantiation is skipped rather than recorded with one fewer
    argument than reality (the same discipline the opaque template-template-
    argument case already gets -- an instantiation record this module can't
    fully spell shouldn't exist at all, since a silently-shorter ``args``
    tuple would misrepresent the real instantiation's arity)."""
    nameless_stub = {
        "kind": "TemplateArgument",
        "decl": {"id": "0xANON", "kind": "FunctionDecl", "name": ""},
    }
    callback_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Callback",
        "completeDefinition": True,
        "inner": [],
    }
    callback_instantiation = {
        "id": "0xSPEC_CALLBACK",
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Callback",
        "completeDefinition": True,
        "inner": [nameless_stub],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Callback",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Fn"},
            callback_pattern,
            callback_instantiation,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [class_template]}
    out = parse_clang_ast_templates(ast)
    assert out == []


def test_two_instantiations_sharing_only_a_bare_callee_name_stay_distinct() -> None:
    """``Holder<&ns1::f>`` and ``Holder<&ns2::f>`` -- two genuinely distinct
    instantiations whose NTTP targets merely *share* an unqualified callee
    name across namespaces -- must not collide onto one
    ``template_instantiation`` node (the exact collision risk the G29
    Phase 5 plan doc flagged as the reason TEMPLATE_USES_DECL stayed
    unimplemented for a prior investigation round: a naive bare-spelling
    label would merge both instantiations' own args/emitted_symbols onto
    one shared node)."""

    def _holder_ast(ns_name: str, mangled: str) -> tuple[dict, dict]:
        f_decl = {
            "id": f"0xF_{ns_name}",
            "kind": "FunctionDecl",
            "name": "f",
            "mangledName": mangled,
            "type": {"qualType": "void ()"},
            "inner": [],
        }
        ns = {"kind": "NamespaceDecl", "name": ns_name, "inner": [f_decl]}
        holder_instantiation = {
            "id": f"0xSPEC_{ns_name}",
            "kind": "ClassTemplateSpecializationDecl",
            "name": "Holder",
            "completeDefinition": True,
            "inner": [
                {
                    "kind": "TemplateArgument",
                    "decl": {
                        "id": f"0xF_{ns_name}",
                        "kind": "FunctionDecl",
                        "name": "f",
                        "type": {"qualType": "void ()"},
                    },
                },
            ],
        }
        return ns, holder_instantiation

    ns1, spec1 = _holder_ast("ns1", "_ZN3ns11fEv")
    ns2, spec2 = _holder_ast("ns2", "_ZN3ns21fEv")
    holder_pattern = {
        "kind": "CXXRecordDecl",
        "name": "Holder",
        "completeDefinition": True,
        "inner": [],
    }
    class_template = {
        "kind": "ClassTemplateDecl",
        "name": "Holder",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "Fn"},
            holder_pattern,
            spec1,
            spec2,
        ],
    }
    ast = {"kind": "TranslationUnitDecl", "inner": [ns1, ns2, class_template]}
    out = parse_clang_ast_templates(ast)
    labels = {i.label for i in out}
    assert len(out) == 2
    assert labels == {"Holder<_ZN3ns11fEv>", "Holder<_ZN3ns21fEv>"}


def test_augment_graph_decl_argument_mints_source_decl_node_not_type_node() -> None:
    graph = SourceGraphSummary()
    inst = TemplateInstantiation(
        kind="record",
        template_qname="Callback",
        label="Callback<handler>",
        args=(TemplateArgUse("handler", "_ZN2ns7handlerEi", "FunctionDecl"),),
    )
    added = augment_graph_with_templates(graph, [inst])
    assert added == 2  # DECL_INSTANTIATES_TEMPLATE + TEMPLATE_USES_DECL

    node_ids = {n.id: n for n in graph.nodes}
    decl_id = "decl://_ZN2ns7handlerEi"
    assert node_ids[decl_id].kind == NODE_SOURCE_DECL
    # Never a type:// node for a TEMPLATE_USES_DECL target.
    assert "type://_ZN2ns7handlerEi" not in node_ids

    tinst_id = template_instantiation_node_id("Callback<handler>")
    edge_kinds = {(e.src, e.dst, e.kind) for e in graph.edges}
    assert (tinst_id, decl_id, EDGE_TEMPLATE_USES_DECL) in edge_kinds
    assert not any(e.kind == EDGE_TEMPLATE_USES_TYPE for e in graph.edges)


def test_augment_graph_decl_argument_joins_the_same_node_a_type_argument_would_share() -> (
    None
):
    """Two instantiations resolving TEMPLATE_USES_DECL to the identical
    identity string share one source_decl node, the same shared-node-id
    join every other producer in this graph uses."""
    graph = SourceGraphSummary()
    a = TemplateInstantiation(
        kind="record",
        template_qname="Callback",
        label="Callback<handler>",
        args=(TemplateArgUse("handler", "_ZN2ns7handlerEi", "FunctionDecl"),),
    )
    b = TemplateInstantiation(
        kind="record",
        template_qname="OtherCallback",
        label="OtherCallback<handler>",
        args=(TemplateArgUse("handler", "_ZN2ns7handlerEi", "FunctionDecl"),),
    )
    augment_graph_with_templates(graph, [a, b])
    decl_nodes = [n for n in graph.nodes if n.kind == NODE_SOURCE_DECL]
    assert len(decl_nodes) == 1


@pytest.mark.integration
def test_real_clang_function_pointer_nttp_resolves_and_joins_source_decl(
    tmp_path,
) -> None:
    """Re-verifies TEMPLATE_USES_DECL end to end against a real compiler,
    for a pointer-to-function, a pointer-to-variable, *and* a reference-to-
    variable NTTP, rather than only the hand-crafted fixtures above.

    Pins ``--target=x86_64-linux-gnu`` regardless of host OS -- the CI
    Windows lane's own bundled ``clang++`` defaults to the MSVC target
    (Itanium ``_Z...`` vs. MSVC ``?...`` mangling, the same divergence
    ``AGENTS.md``'s "Known gaps" entry documents for ``msvc_scope_
    components``), so without pinning, this test's label assertions --
    which check a specific mangled spelling, not the mangling scheme
    itself -- fail on that lane alone (confirmed via a real Windows CI run:
    `KeyError: 'Callback<_ZN2ns7handlerEi>'`). `-fsyntax-only` over this
    self-contained, no-``#include`` source needs no target-specific
    standard library, so cross-targeting is safe here.
    """
    clang_bin = shutil.which("clang++") or shutil.which("clang")
    if clang_bin is None:
        pytest.skip("clang++ not found in PATH")
    src = tmp_path / "t.cpp"
    src.write_text(
        "namespace ns { void handler(int) {} int global = 0; }\n"
        "template <void (*Fn)(int)> struct Callback { void call(int x) { Fn(x); } };\n"
        "Callback<&ns::handler> cb;\n"
        "template <int* Ptr> struct Holder {};\n"
        "Holder<&ns::global> h;\n"
        "template <int& Ref> struct RefHolder { int get() { return Ref; } };\n"
        "RefHolder<ns::global> rh;\n"
    )
    result = subprocess.run(
        [
            clang_bin,
            "--target=x86_64-linux-gnu",
            "-std=c++17",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    import json

    ast = json.loads(result.stdout)
    out = parse_clang_ast_templates(ast)
    labels = {i.label: i for i in out}
    # The label uses the resolved identity, not the bare callee name -- see
    # _arg_label_spelling's own docstring.
    callback_inst = labels["Callback<_ZN2ns7handlerEi>"]
    assert callback_inst.args == (
        TemplateArgUse("handler", "_ZN2ns7handlerEi", "FunctionDecl"),
    )
    holder_inst = labels["Holder<_ZN2ns6globalE>"]
    assert holder_inst.args == (TemplateArgUse("global", "_ZN2ns6globalE", "VarDecl"),)
    refholder_inst = labels["RefHolder<_ZN2ns6globalE>"]
    assert refholder_inst.args == (
        TemplateArgUse("global", "_ZN2ns6globalE", "VarDecl"),
    )

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    edge_kinds = {(e.src, e.dst, e.kind) for e in graph.edges}
    assert (
        template_instantiation_node_id("Callback<_ZN2ns7handlerEi>"),
        "decl://_ZN2ns7handlerEi",
        EDGE_TEMPLATE_USES_DECL,
    ) in edge_kinds
    assert (
        template_instantiation_node_id("RefHolder<_ZN2ns6globalE>"),
        "decl://_ZN2ns6globalE",
        EDGE_TEMPLATE_USES_DECL,
    ) in edge_kinds
    assert (
        template_instantiation_node_id("Holder<_ZN2ns6globalE>"),
        "decl://_ZN2ns6globalE",
        EDGE_TEMPLATE_USES_DECL,
    ) in edge_kinds
