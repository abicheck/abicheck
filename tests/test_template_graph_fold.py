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

"""Tests for :mod:`abicheck.buildsource.template_graph_fold` -- the
``augment_graph_with_templates`` graph-folding half of G29 Phase 5 item 1.

Split out of ``test_template_graph.py`` (at its own AI-readiness 2000-line
hard cap) alongside the production split that carved
``template_graph_fold.py`` out of ``template_graph.py`` for the identical
line-cap reason (ADR-061 Phase 5 item 2). These are the tests that construct
:class:`~abicheck.buildsource.template_graph.TemplateInstantiation`/
:class:`~abicheck.buildsource.template_graph.TemplateArgUse` records by hand
and feed them straight to :func:`~abicheck.buildsource.template_graph_fold.
augment_graph_with_templates` -- pure graph-folding coverage, independent of
the AST-parsing half that stayed in ``test_template_graph.py``. The tests
that exercise parsing *and* folding together (real/hand-crafted
``clang -ast-dump=json`` shapes run through
:func:`~abicheck.buildsource.template_graph.parse_clang_ast_templates` then
folded) stayed in ``test_template_graph.py``, matching D10's "tests move
with their implementation" -- these are the ones whose implementation moved.
"""

from __future__ import annotations

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.buildsource.template_graph import TemplateArgUse, TemplateInstantiation
from abicheck.buildsource.template_graph_fold import (
    EDGE_DECL_INSTANTIATES_TEMPLATE,
    EDGE_INSTANTIATION_EMITS_SYMBOL,
    EDGE_TEMPLATE_USES_TYPE,
    NODE_TEMPLATE_DECL,
    NODE_TEMPLATE_INSTANTIATION,
    augment_graph_with_templates,
    template_decl_node_id,
    template_instantiation_node_id,
)

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


def test_augment_graph_enum_argument_mints_enum_type_node_not_record_type() -> None:
    """``TemplateArgUse.target_decl_kind`` (threaded through by
    ``_resolve_arg_targets``) must actually be consulted -- an enum template
    argument should mint an ``enum_type`` node, not the ``record_type``
    fallback every resolved argument used to get unconditionally (Codex
    review: ``_type_node_kind`` was computed but never called)."""
    graph = SourceGraphSummary()
    inst = TemplateInstantiation(
        kind="record",
        template_qname="Wrapper",
        label="Wrapper<internal::Color>",
        args=(TemplateArgUse("internal::Color", "internal::Color", "EnumDecl"),),
    )
    augment_graph_with_templates(graph, [inst])
    node_ids = {n.id: n for n in graph.nodes}
    assert node_ids["type://internal::Color"].kind == "enum_type"


def test_augment_graph_typedef_argument_mints_typedef_node() -> None:
    graph = SourceGraphSummary()
    inst = TemplateInstantiation(
        kind="record",
        template_qname="Wrapper",
        label="Wrapper<internal::Handle>",
        args=(TemplateArgUse("internal::Handle", "internal::Handle", "TypedefDecl"),),
    )
    augment_graph_with_templates(graph, [inst])
    node_ids = {n.id: n for n in graph.nodes}
    assert node_ids["type://internal::Handle"].kind == "typedef"


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
