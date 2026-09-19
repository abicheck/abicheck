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

"""Everything ``build_header_only_graph`` reads out of a clang AST, projected.

Why this module exists
----------------------
``service_header_graph_attach._attach_header_graph`` parses a whole
``clang -ast-dump=json`` document into Python dicts and then builds the
header-only (L5) semantic graph **while that tree is still resident**. On a
real library that overlap is the member's peak: measured on oneDAL's
``libonedal_core.so.3`` the AST alone is ~1.4-1.5 GiB of dicts and the graph
adds ~271 MiB on top of it (``docs/contribute/measurements/
header-graph-attach-memory.md``).

The graph builder never needs the tree itself. It reads exactly four pure,
compact projections of it:

* :func:`~abicheck.buildsource.type_graph.index_declared_type_files` --
  qualified type name -> declaring file;
* :func:`~abicheck.buildsource.type_graph.parse_clang_ast_types` --
  ``TypeEdge`` list;
* :func:`~abicheck.buildsource.call_graph.parse_clang_ast_calls` --
  ``CallEdge`` list;
* :func:`~abicheck.buildsource.type_graph.index_declared_entity_files` --
  declared identity -> declaring file, consulted only to resolve the
  *source* side of a ``DECL_REFERENCES_DECL`` edge.

Taking all four **before** the graph exists lets the caller drop its
reference to the AST first, so the two never occupy memory at the same time
and the graph's own long-lived objects land in arenas the AST parse has
already released rather than pinning them.

What that is and is not worth, measured on that same library (the
2026-09-19 follow-up in the doc above) rather than argued: the graph
build's own residency cost falls from **+147 MiB to +25 MiB**, and the
projection costs 23.6 MiB against a 1044 MiB tree. It does **not** lower
the member's peak (2215.4 -> 2215.2 MiB) or what the attach retains once it
returns (1287.6 vs 1291.5 MiB, within noise) -- the peak is inside
``json.load``, where the document is held as one ``str`` while the tree is
built from it, so it is ``document + tree`` and never ``tree + graph``.
Don't cite this module as having fixed that; the open lever is pruning the
tree, and this projection is the statement of what such a prune would have
to preserve.

This is a *memory-ordering* change only. The same four functions run, over
the same tree, in the same order, and their results are handed to the same
consumer unchanged -- no finding, verdict, exit code or report section can
move, ``DECL_CALLS_DECL`` edges included.

``entity_files`` preserves the caller's own laziness exactly:
:func:`_unseeded_decl_endpoints` computed it only when at least one
``DECL_REFERENCES_DECL`` edge existed, so this module does the same rather
than paying an unconditional extra AST walk on every dump.

The seeding half (:func:`seed_ast_graph` and its two helpers) lives here
too, with the projection it reads: together they are the AST-sourced half
of :func:`~abicheck.buildsource.header_graph.build_header_only_graph`,
whose own file sits at its ``architecture/debt.yaml`` line cap. The
snapshot-sourced half (``_seed_flat_graph``, the clang-unavailable
fallback) stays there -- it touches no AST at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..model import ScopeOrigin
from ..model.graph_facts import (
    CONF_HIGH,
    GraphEdge,
    GraphNode,
    _decl_node_id,
    _type_node_id,
)
from .call_graph import augment_graph_with_calls, parse_clang_ast_calls
from .inline_graph_fold import _mark_role_coverage
from .type_graph import (
    augment_graph_with_types,
    index_declared_entity_files,
    index_declared_type_files,
    parse_clang_ast_types,
)

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary
    from .call_graph import CallEdge
    from .type_graph import TypeEdge


#: Extractor-pass names the AST-sourced seeding below stamps onto
#: ``SourceGraphSummary.extractor_passes`` (ADR-031 D9 coverage honesty),
#: distinct from ``inline_graph_fold``'s build-integrated ``"call_graph"``/
#: ``"type_graph"`` so a reader (and ``source_graph_findings.
#: _common_dependency_edge_kinds``) never conflates a header-only pass with
#: a full build-integrated one. Owned here rather than in ``header_graph``
#: because the code that stamps them lives here and that module imports
#: this one, never the reverse; ``header_graph`` re-exports both names.
HEADER_CALL_GRAPH_PASS = "header_call_graph"
HEADER_TYPE_GRAPH_PASS = "header_type_graph"

#: Provenance tag for nodes/edges built from the clang AST.
_PROVENANCE = "header_ast_l2"


@dataclass(frozen=True)
class HeaderGraphAstProjection:
    """The whole of a clang AST that the header-only graph builder reads.

    Deliberately holds no reference to the AST it was projected from: the
    point of the projection is that the tree can be released the moment this
    object exists.
    """

    #: Qualified record/enum/typedef name -> its declaring file.
    type_files: dict[str, str] = field(default_factory=dict)
    #: ``DECL_HAS_TYPE``/``DECL_REFERENCES_DECL``-style edges.
    type_edges: list[TypeEdge] = field(default_factory=list)
    #: ``DECL_CALLS_DECL`` edges.
    call_edges: list[CallEdge] = field(default_factory=list)
    #: Every declared identity -> declaring file. Empty when no
    #: ``DECL_REFERENCES_DECL`` edge needed it, matching the lazy
    #: computation this replaced -- never a claim that the AST declares
    #: nothing.
    entity_files: dict[str, str] = field(default_factory=dict)


def project_header_graph_ast(ast_root: dict[str, Any]) -> HeaderGraphAstProjection:
    """Read *ast_root* once into the compact form the graph builder needs."""
    type_files = index_declared_type_files(ast_root)
    type_edges = parse_clang_ast_types(ast_root)
    call_edges = parse_clang_ast_calls(ast_root)
    needs_entity_files = any(e.kind == "DECL_REFERENCES_DECL" for e in type_edges)
    entity_files = index_declared_entity_files(ast_root) if needs_entity_files else {}
    return HeaderGraphAstProjection(
        type_files=type_files,
        type_edges=type_edges,
        call_edges=call_edges,
        entity_files=entity_files,
    )


def _seed_ast_type_nodes(
    graph: SourceGraphSummary,
    type_files: dict[str, str],
    header_node: Callable[[str], str],
    classify: Callable[[str], ScopeOrigin],
) -> None:
    """Seed ``record_type`` nodes from the AST's own qualified-name index.

    Deliberately not from ``snapshot.types``/``snapshot.enums``: the flat
    snapshot model records a *bare*, unqualified type name (see
    ``dumper_clang._ClangAstParser._build_record``), while the type graph's node
    ids are the AST's *resolved qualified* name (``ns::Widget``) — two
    representations that would silently fail to join on any namespaced type.
    Deriving both the file (hence origin) and the node id from the same AST
    index sidesteps that mismatch, and covers the ADR's headline case: a public
    struct rarely has its own exported binary symbol, so it needs ``visibility``
    set directly on the type node to act as a valid graph "entry"
    (``is_public_dependency_node``).
    """
    for qname, file in type_files.items():
        origin = classify(file)
        if origin == ScopeOrigin.UNKNOWN:
            continue
        node_id = _type_node_id(qname)
        # ``augment_graph_with_types`` defaults every AST-only type node to
        # "record_type" uniformly (it cannot distinguish record/enum/typedef
        # without an L4 surface) — matching that convention here keeps
        # first-writer-wins joins consistent either way.
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="record_type",
                label=qname,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
                attrs={"visibility": origin.value},
            )
        )
        graph.add_edge(
            GraphEdge(
                src=header_node(file),
                dst=node_id,
                kind="SOURCE_DECLARES",
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )


def _unseeded_decl_endpoints(
    entity_files: dict[str, str], type_edges: list[Any], call_edges: list[Any]
) -> tuple[tuple[str, str], ...]:
    """``(identity, file)`` for every edge endpoint the snapshot never seeded.

    Annotates any AST-only decl target ``augment_graph_with_types``/
    ``augment_graph_with_calls`` would otherwise create with no provenance at
    all — a private declaration that is not a function or (namespace-scope)
    variable, e.g. an ``EnumConstantDecl`` referenced by ``inline int f() {
    return Color::RED; }``, is never seeded from
    ``snapshot.functions``/``snapshot.variables``, since the flat AbiSnapshot
    model has no equivalent per-enumerator entity to iterate. The
    build-integrated path backfills this via ``augment_graph_with_types``'s
    ``project_files`` parameter (matched against ``BuildEvidence``'s
    compile-unit sources); a header-only world has no such set, but each edge
    already carries its own target's declaring file
    (``dst_file``/``callee_file``/``caller_file``), which is exactly what
    ``classify_origin`` needs (Codex review).

    A ``DECL_REFERENCES_DECL`` edge's *source* can be unseeded too — a field's
    default member initializer (``struct Widget { int x = detail::k; };``)
    makes ``Widget::x`` the edge's ``src``, and a field is never in
    ``snapshot.functions``/``snapshot.variables`` either (Codex review). Unlike
    the targets, ``TypeEdge`` carries no ``src_file``, so this falls back to
    ``index_declared_entity_files`` (the unfiltered declaring-file index,
    including fields) — computed once, lazily, only if there is at least one
    such source to look up.
    """
    ref_srcs = {e.src for e in type_edges if e.kind == "DECL_REFERENCES_DECL"}
    return (
        *((e.dst, e.dst_file) for e in type_edges if e.kind == "DECL_REFERENCES_DECL"),
        *((src, entity_files.get(src, "")) for src in ref_srcs),
        *((e.caller, e.caller_file) for e in call_edges),
        *((e.callee, e.callee_file) for e in call_edges),
    )


def seed_ast_graph(
    graph: SourceGraphSummary,
    projection: HeaderGraphAstProjection,
    header_node: Callable[[str], str],
    classify: Callable[[str], ScopeOrigin],
) -> None:
    """Seed type nodes and fold the clang type/call edges into *graph*."""
    _seed_ast_type_nodes(graph, projection.type_files, header_node, classify)
    type_edges = projection.type_edges
    call_edges = projection.call_edges
    for identity, file in _unseeded_decl_endpoints(
        projection.entity_files, type_edges, call_edges
    ):
        if not identity or not file:
            continue
        node_id = _decl_node_id(identity)
        if graph.has_node(node_id):
            # Already seeded as a real function/variable.
            continue
        origin = classify(file)
        graph.add_node(
            GraphNode(
                id=node_id,
                kind="source_decl",
                label=identity,
                provenance=_PROVENANCE,
                confidence=CONF_HIGH,
                attrs=(
                    {"visibility": origin.value}
                    if origin != ScopeOrigin.UNKNOWN
                    else {}
                ),
            )
        )
    augment_graph_with_types(graph, type_edges)
    augment_graph_with_calls(graph, call_edges)
    # A header-only pass is a single parse over the whole header aggregate —
    # never narrowed/scoped like a per-compile-unit build-integrated pass, and
    # ``_clang_header_dump`` raises on a failed/empty parse rather than
    # returning a degraded partial result (ADR-028 D3 "never abort collection"
    # lives one layer up, in the caller's try/except around the clang
    # invocation) — so reaching this line means the whole pass ran cleanly.
    # Stamped unconditionally, regardless of edge count (ADR-041 P0 slice 2
    # coverage-honesty convention: "ran, zero output" must be distinguishable
    # from "never ran").
    graph.extractor_passes[HEADER_CALL_GRAPH_PASS] = True
    graph.extractor_passes[HEADER_TYPE_GRAPH_PASS] = True
    # ADR-046 D3 role coverage (Codex review, fresh evidence): this call above
    # is the *same* ``type_graph.parse_clang_ast_types()`` walker the
    # build-integrated ``fold_type_graph`` drives — reused unmodified, per
    # this module's own docstring — so it has the identical per-role fidelity
    # (no known gap for any ``ROLE_COVERAGE_MATRIX`` role, same as the
    # build-integrated pass). Without this, `source_graph_findings.
    # _disagreeing_roles()` would see *no* role key on either side of a
    # header-only-vs-header-only comparison, forever (this pass has never
    # stamped one, in any abicheck version) — reading as vacuous agreement
    # rather than "role coverage unknown," and letting a genuinely new
    # `template_param`/`default_template_arg`/`enum_underlying` edge slip
    # through as a false `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` the same way
    # the build-integrated pass's own pre-D3-consumer gap did.
    _mark_role_coverage(graph.extractor_passes, HEADER_TYPE_GRAPH_PASS)
