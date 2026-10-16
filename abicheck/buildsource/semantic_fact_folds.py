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

"""The one owner of the override, template and callback fact families.

Two producers read a clang AST: the L5 pass (``l5_ast_pass``, one dump per
compile unit) and the default L2 header pass (``header_graph_ast_projection``
and its streamed form, one parse of the header aggregate). Both used to keep
type and call edges; only L5 parsed and folded these three families, so a
headers-only graph had none of them although its AST held them (known gap
"The default L2 header projection drops template/override/callback facts").

This module is where both producers get them now, so the facts cannot
drift between the two:

* **parse** -- :func:`parse_semantic_facts` (a whole tree) and
  :class:`SemanticFactStream` (the same walks fed one top-level declaration
  at a time) drive each family's own parser: ``override_graph.OverrideInputs``,
  ``template_graph.TemplateParse``, ``callback_graph.CallbackParse``.
* **merge** -- :func:`merge_semantic_facts` folds several parses (L5's
  compile units, the header pass's bisected groups).
* **fold** -- :func:`fold_override_facts`, :func:`fold_virtual_dispatch_facts`,
  :func:`fold_template_facts` and :func:`fold_callback_facts` are the graph
  folds both producers call, in the order :data:`FOLD_ORDER` states.
  :func:`fold_header_semantic_facts` is the header pass's whole sequence.

What stays with each producer is only its coverage claim: the L5 folds in
``inline_graph_fold`` stamp the build passes (``override_graph`` ...), the
header pass stamps ``header_*`` passes (``model.source_graph_coverage.
SEMANTIC_FAMILIES``), since a header parse sees only header-written code.

Cost: the override facts reuse the type edges their producer already has
(no second type walk), and the template parse keeps compact records of the
specializations it joins (``template_graph._compact_full_node``), so the
streamed header path holds no instantiated member bodies.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..model.source_graph_coverage import (
    HEADER_CALLBACK_GRAPH_PASS,
    HEADER_OVERRIDE_GRAPH_PASS,
    HEADER_TEMPLATE_GRAPH_PASS,
    HEADER_VIRTUAL_DISPATCH_GRAPH_PASS,
)
from .callback_graph import (
    CallbackEdge,
    CallbackParse,
    augment_graph_with_callback_invocations,
    augment_graph_with_callback_registrations,
    merge_callback_edges,
)
from .override_graph import (
    OverrideEdge,
    OverrideInputs,
    augment_graph_with_overrides,
    merge_override_facts,
)
from .template_graph import (
    TemplateInstantiation,
    TemplateParse,
    merge_template_instantiations,
)
from .type_graph_walk import parse_clang_ast_types
from .virtual_dispatch_graph import augment_graph_with_virtual_dispatch

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .callback_graph import CallbackDispatchResult, CallbackRegistrationResult

#: Override edges, every virtual-method identity, every class declaring its
#: own virtual destructor (``override_graph.parse_clang_ast_override_facts``).
OverrideFacts = tuple[list[OverrideEdge], frozenset[str], frozenset[str]]

#: The header passes :func:`fold_header_semantic_facts` stamps.
HEADER_SEMANTIC_PASSES: tuple[str, ...] = (
    HEADER_OVERRIDE_GRAPH_PASS,
    HEADER_VIRTUAL_DISPATCH_GRAPH_PASS,
    HEADER_TEMPLATE_GRAPH_PASS,
    HEADER_CALLBACK_GRAPH_PASS,
)

#: Fold order, load-bearing for both producers: virtual dispatch joins the
#: call/type/override state folded before it, and the callback invocation
#: join reads the call fold's function-pointer ``DECL_CALLS_DECL`` edges.
FOLD_ORDER: tuple[str, ...] = ("override", "virtual_dispatch", "template", "callback")


@dataclass(frozen=True)
class SemanticFacts:
    """One parse's override, template and callback facts."""

    overrides: OverrideFacts = field(
        default_factory=lambda: ([], frozenset(), frozenset())
    )
    templates: list[TemplateInstantiation] = field(default_factory=list)
    callbacks: list[CallbackEdge] = field(default_factory=list)


def parse_semantic_facts(
    ast: dict[str, Any], type_edges: Sequence[Any] | None = None
) -> SemanticFacts:
    """Every family's facts from one whole tree.

    *type_edges* are the tree's own ``parse_clang_ast_types`` edges when the
    caller already has them (the header projection does); otherwise they are
    parsed here, inside the caller's ``type_graph.ast_derived_scope`` if any.
    """
    inputs = OverrideInputs()
    inputs.collect(ast)
    edges = parse_clang_ast_types(ast) if type_edges is None else type_edges
    templates = TemplateParse()
    templates.index(ast)
    templates.walk_classes(ast)
    templates.start_functions()
    templates.walk_functions(ast)
    callbacks = CallbackParse()
    callbacks.index(ast)
    callbacks.emit(ast)
    return merge_semantic_facts(
        [SemanticFacts(inputs.facts(edges), templates.result(), callbacks.result())]
    )


class SemanticFactStream:
    """:func:`parse_semantic_facts` over a document read one top-level
    declaration at a time (``header_graph_ast_stream``), never the tree.

    The parsers are multi-pass, each pass needing the whole TU's result of
    the one before, so the caller feeds every element to :meth:`index`, then
    every element to :meth:`walk`, then -- after :meth:`start_late` -- every
    element to :meth:`walk_late`, in document order each time.
    """

    def __init__(self) -> None:
        self._overrides = OverrideInputs()
        self._templates = TemplateParse()
        self._callbacks = CallbackParse()

    def index(self, element: dict[str, Any]) -> None:
        self._overrides.collect(element)
        self._templates.index(element)
        self._callbacks.index(element)

    def walk(self, element: dict[str, Any]) -> None:
        self._templates.walk_classes(element)
        self._callbacks.emit(element)

    def start_late(self) -> None:
        self._templates.start_functions()

    def walk_late(self, element: dict[str, Any]) -> None:
        self._templates.walk_functions(element)

    def finish(self, type_edges: Sequence[Any]) -> SemanticFacts:
        return merge_semantic_facts(
            [
                SemanticFacts(
                    self._overrides.facts(type_edges),
                    self._templates.result(),
                    self._callbacks.result(),
                )
            ]
        )


def merge_semantic_facts(parts: Iterable[SemanticFacts]) -> SemanticFacts:
    """Fold several parses' facts, in order, with each family's own merge.

    Applied to a single parse too (:func:`parse_semantic_facts`): one TU can
    yield two instantiations under one merge key (the same label reached
    through a primary and a partial-specialization pattern), and the L5
    pass always folds its per-TU results through these merges, so the
    header pass must as well or the two producers' facts would differ."""
    parts = list(parts)
    edges, methods, owners = merge_override_facts(p.overrides for p in parts)
    return SemanticFacts(
        (edges, frozenset(methods), frozenset(owners)),
        merge_template_instantiations(p.templates for p in parts),
        merge_callback_edges(p.callbacks for p in parts),
    )


def fold_override_facts(graph: SourceGraphSummary, facts: OverrideFacts) -> int:
    """``METHOD_POSSIBLE_OVERRIDE`` edges and the virtual-slot stamps; the
    number of edges added."""
    edges, virtual_methods, virtual_destructor_owners = facts
    return augment_graph_with_overrides(
        graph,
        edges,
        virtual_methods=frozenset(virtual_methods),
        virtual_destructor_owners=frozenset(virtual_destructor_owners),
    )


def fold_virtual_dispatch_facts(graph: SourceGraphSummary) -> None:
    """``VIRTUAL_CALL_MAY_DISPATCH_TO``/``TYPE_HAS_VTABLE``: a pure join over
    the call, type and override state already folded."""
    augment_graph_with_virtual_dispatch(graph)


def fold_template_facts(
    graph: SourceGraphSummary,
    instantiations: list[TemplateInstantiation],
    project_files: frozenset[str] | None = None,
) -> int:
    """The template-instantiation nodes and edges; the number of edges added."""
    from .template_graph_fold import augment_graph_with_templates

    return augment_graph_with_templates(graph, instantiations, project_files)


def fold_callback_facts(
    graph: SourceGraphSummary, edges: list[CallbackEdge]
) -> tuple[CallbackRegistrationResult, CallbackDispatchResult]:
    """Registration/address-of edges, then the ``CALLBACK_MAY_INVOKE`` join
    over them and the already-folded function-pointer calls."""
    registrations = augment_graph_with_callback_registrations(graph, edges)
    return registrations, augment_graph_with_callback_invocations(graph)


def fold_header_semantic_facts(graph: SourceGraphSummary, facts: SemanticFacts) -> None:
    """The header pass's share: every family folded in :data:`FOLD_ORDER`
    after its type/call edges, each header pass stamped.

    Stamped unconditionally, like ``seed_ast_graph``'s own header passes: a
    projection exists only when the whole parse succeeded, so "ran, found
    nothing" is a real zero. A partially failed parse is degraded one layer
    up (``service_header_graph_attach``), for these passes too.
    """
    fold_override_facts(graph, facts.overrides)
    fold_virtual_dispatch_facts(graph)
    fold_template_facts(graph, facts.templates)
    fold_callback_facts(graph, facts.callbacks)
    for pass_name in HEADER_SEMANTIC_PASSES:
        graph.extractor_passes[pass_name] = True
