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

"""Which L5 graph passes share one ``clang -ast-dump=json`` per TU.

The clang-backed L5 passes (call, type, override, template, macro-range,
callback) all dump a TU with the identical argv
(``call_graph._safe_clang_args_from_compile_unit``) and differ only in the
pure parser they apply. :func:`fold_semantic_graphs` runs them inside a
:func:`~abicheck.buildsource.clang_ast_run.shared_ast_scope` naming every
pass's parser, so the first pass's single dump per TU answers all six. It
lives here rather than in ``inline_graph_fold`` (which owns each pass's own
fold) because running the passes *together* is what this module owns.

A new clang-backed pass adds its (module-level) parser to
:func:`l5_ast_parsers` and calls
:func:`~abicheck.buildsource.clang_ast_run.parse_clang_ast` with it; a parser
missing from this list still works, it just dumps the TU again for itself.
See ``docs/contribute/plans/l4-l2-extraction-convergence.md`` (Phase 1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .clang_ast_run import AstParser, shared_ast_scope
from .inline_graph_fold import (
    fold_call_graph,
    fold_callback_graph,
    fold_include_graph,
    fold_macro_graph,
    fold_override_graph,
    fold_template_graph,
    fold_type_graph,
    fold_virtual_dispatch_graph,
)

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .build_evidence import BuildEvidence
    from .model import ExtractorRecord


def l5_ast_parsers() -> tuple[AstParser, ...]:
    """The parser each clang-backed L5 pass applies to a TU's AST dump."""
    from .call_graph import parse_clang_ast_calls
    from .callback_graph import parse_clang_ast_callbacks
    from .macro_graph import parse_clang_ast_decl_ranges
    from .override_graph_extractor import parse_clang_ast_override_facts
    from .template_graph import parse_clang_ast_templates
    from .type_graph import parse_clang_ast_types

    return (
        parse_clang_ast_calls,
        parse_clang_ast_types,
        parse_clang_ast_override_facts,
        parse_clang_ast_templates,
        parse_clang_ast_decl_ranges,
        parse_clang_ast_callbacks,
    )


def fold_semantic_graphs(
    graph: SourceGraphSummary,
    merged: BuildEvidence,
    clang_bin: str,
    extractors: list[ExtractorRecord] | None,
    changed_paths: tuple[str, ...] = (),
    scoped_units: list[Any] | None = None,
) -> None:
    """Run every Clang-derived L5 graph pass over *graph* in one call:
    :func:`fold_call_graph`, :func:`fold_type_graph`,
    :func:`fold_override_graph` (ADR-041 P2 item 1),
    :func:`fold_virtual_dispatch_graph` (G29 Phase 5 item 3),
    :func:`fold_template_graph` (G29 Phase 5 item 1),
    :func:`fold_macro_graph` (G29 Phase 5 item 2),
    :func:`fold_callback_graph` (G29 Phase 5 item 4), then
    :func:`fold_include_graph` — the exact sequence ``inline._build_inline_graph``
    ran inline before this wrapper existed, kept together here (rather than
    one call site per pass in ``inline.py``, which sits at its own
    line-count cap) so a future pass adds one call here instead of
    growing that file too. ``fold_macro_graph`` sits right after
    ``fold_template_graph`` — both are Clang-backed passes needing the same
    scoping decision, and both close over one of G29 Phase 5's originally
    open graph families. ``fold_virtual_dispatch_graph`` sits right after
    ``fold_override_graph`` and takes no clang/scoping arguments of its own
    (see its own docstring) — it is a pure transformation over the call/type/
    override graph state the three preceding passes just folded, so it must
    run after all three, not merely "somewhere in this sequence".
    ``fold_callback_graph`` sits after ``fold_macro_graph`` for the identical
    reason ``fold_virtual_dispatch_graph`` sits after ``fold_override_graph``:
    its own internal Part A join reads ``call_graph.py``'s already-folded
    function-pointer-kind ``DECL_CALLS_DECL`` edges, so ``fold_call_graph``
    must have already run — any position after it works, and grouping it with
    the other G29 Phase 5 passes keeps this family together. Each
    clang-backed pass shares the same *changed_paths*/*scoped_units* scoping
    precedence and clang-binary resolution, and each degrades independently
    (a missing ``clang++`` or a per-TU parse failure never aborts a later
    pass — ADR-028 D3).

    The six clang-backed passes run inside one :func:`shared_ast_scope`
    (:func:`l5_ast_parsers`), so they read one AST dump per TU.
    """
    with shared_ast_scope(l5_ast_parsers()):
        fold_call_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=scoped_units,
        )
        fold_type_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=scoped_units,
        )
        fold_override_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=scoped_units,
        )
        fold_virtual_dispatch_graph(graph)
        fold_template_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=scoped_units,
        )
        fold_macro_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=scoped_units,
        )
        fold_callback_graph(
            graph,
            merged,
            clang_bin,
            extractors,
            changed_paths,
            scoped_units=scoped_units,
        )
    fold_include_graph(
        graph, merged, clang_bin, extractors, changed_paths, scoped_units=scoped_units
    )
