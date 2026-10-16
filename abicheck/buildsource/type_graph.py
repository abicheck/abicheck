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

"""Optional Clang type/reference-graph extraction for the L5 graph (ADR-041 P0).

The call graph (``call_graph.py``) captures *one* kind of compiler-derived
dependency — "A calls B". Many API/ABI risks are not call edges at all: a
public struct with a private field type, a public class inheriting an
internal base, or an inline function reading an internal constant. This
module extracts those from the same ``clang -ast-dump=json`` tree the call
graph already knows how to parse, producing the edge kinds
``source_graph.py`` already reserves for them (ADR-031 D2 schema) but that,
before this module, no extractor ever populated:

- :data:`EDGE_TYPE_INHERITS` — a record's base class.
- :data:`EDGE_TYPE_HAS_FIELD_TYPE` — a record's field type.
- :data:`EDGE_DECL_HAS_TYPE` — a function/method's parameter type.
- :data:`EDGE_DECL_REFERENCES_DECL` — a function body referencing a
  variable/enumerator that is not itself a call (the call graph already
  covers call-target references).

Architecture mirrors ``call_graph.py`` deliberately:

- :func:`~abicheck.buildsource.type_graph_walk.parse_clang_ast_types` is a
  **pure function** over a ``clang -Xclang -ast-dump=json`` tree — unit-tested
  without a compiler. Its walkers live in ``type_graph_walk.py``; the name
  resolution they call lives in ``type_graph_index.py``, and the spelling
  helpers in ``type_graph_names.py``. This module keeps the edge value type,
  the cross-TU fold and the graph fold.
- :func:`merge_type_edges` folds every TU's edges; the live clang run is
  ``l5_ast_pass`` (integration-only; a missing compiler degrades gracefully).
- :func:`augment_graph_with_types` folds the resulting edges into a
  :class:`~abicheck.model.source_graph.SourceGraphSummary`.

Every edge is best-effort and approximate — type names are matched by their
*textual* base spelling (cv/pointer/reference/array stripped). clang's
``qualType`` prints a type *as written*, not fully qualified, so a first AST
pass (``type_graph_index._index_declared_entities``) indexes every record's qualified name
and declaring file and the second pass resolves an unqualified spelling
against the nearest enclosing scope (``type_graph_index._resolve_type_name``) —
approximate unqualified-name lookup, not real semantic resolution, so two
same-named types in different scopes can still collide, and an edge whose
target could not be resolved is kept at reduced confidence rather than
dropped (the same accepted tradeoff ``SourceEntity.identity()`` documents for
unmangled declarations). This is a second, independent
``clang -ast-dump=json`` pass over each translation unit
(alongside the call graph's own pass) — a real compiler-facts source (a
build-integrated plugin emitting both edge families from one frontend pass)
is future work; see ADR-041 P0/P1.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ..model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    register_fact,
)

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary

# ── edge kinds (already reserved by source_graph.GRAPH_EDGE_KINDS) ─────────
EDGE_TYPE_INHERITS = "TYPE_INHERITS"
EDGE_TYPE_HAS_FIELD_TYPE = "TYPE_HAS_FIELD_TYPE"
EDGE_DECL_HAS_TYPE = "DECL_HAS_TYPE"
EDGE_DECL_REFERENCES_DECL = "DECL_REFERENCES_DECL"


@dataclass(frozen=True)
class TypeEdge:
    """One type/reference edge extracted from a clang AST (ADR-041 P0)."""

    src: str
    dst: str
    kind: str
    confidence: str = CONF_HIGH
    #: "base" | "field" | "param" | "return" | "ref" — the role of *dst*
    #: relative to *src*.
    role: str = ""
    #: The file *dst* is declared in, when resolvable within this TU's AST —
    #: used to mark an AST-only dependency node ``defined_in_project`` (Codex
    #: review: without this, a private-header dst node carries no visibility
    #: and ``public_to_internal_dependency`` never fires on it).
    dst_file: str = ""
    #: How *dst*'s name/identity was resolved (richer confidence/provenance,
    #: ADR-041 addendum + P1 #4): for `TYPE_INHERITS`/`TYPE_HAS_FIELD_TYPE`/
    #: `DECL_HAS_TYPE`, one of :func:`_resolve_type_name`'s
    #: :data:`RESOLUTION_SCOPE` (matched via the nearest-enclosing-scope walk
    #: — real C++ lookup order, the confident case), :data:`RESOLUTION_UNIQUE_CANDIDATE`
    #: (no scope matched, but exactly one same-bare-name declaration exists
    #: anywhere in the TU — a last-resort guess, weaker than a scope match
    #: even though both were previously folded into the same flat
    #: ``CONF_HIGH`` tier), or :data:`RESOLUTION_UNRESOLVED` (no candidate at
    #: all; the raw spelling is kept). For `DECL_REFERENCES_DECL`, one of
    #: :func:`_resolve_ref_identity`'s own :data:`RESOLUTION_REF_EXACT`/
    #: :data:`RESOLUTION_REF_UNIQUE_CANDIDATE`/:data:`RESOLUTION_REF_UNRESOLVED`
    #: — a distinct vocabulary (the underlying mechanism differs: stub
    #: completeness/id-index lookup, not C++ scope-qualified name lookup),
    #: but the same confidence pattern (only the "exact"-equivalent tier
    #: earns ``CONF_HIGH``). Excluded from equality/hash (``compare=False``):
    #: it's a purely informational refinement of *why* ``confidence`` (a
    #: normally-compared field) already came out what it did — the many
    #: existing tests asserting exact ``TypeEdge(...) ==`` equality against
    #: ``confidence``/``role``/``dst_file`` alone should not need updating
    #: for a field that adds detail without changing what they already
    #: verify.
    resolution: str = field(default="", compare=False)


def _dedupe_edges(edges: list[TypeEdge]) -> list[TypeEdge]:
    """Dedup on ``(src, dst, kind, role)``, not just ``(src, dst, kind)``
    (Codex review, fresh evidence): a function that both returns and takes
    the same private type emits two real, role-distinct ``DECL_HAS_TYPE``
    edges sharing ``(src, dst, kind)`` (``role="return"`` vs.
    ``role="param"``) -- deduping on the coarser triple silently dropped
    the second role before it ever reached ``augment_graph_with_types``/
    ``add_edge``, so the ADR-046 D1 relation-key split downstream could
    never actually observe both roles from this producer no matter how
    ``add_edge`` itself was keyed.
    """
    seen: set[tuple[str, str, str, str]] = set()
    out: list[TypeEdge] = []
    for e in edges:
        key = (e.src, e.dst, e.kind, e.role)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def augment_graph_with_types(
    graph: SourceGraphSummary,
    edges: list[TypeEdge],
    project_files: frozenset[str] | None = None,
) -> int:
    """Fold type/reference edges into *graph* (ADR-041 P0).

    Declaration endpoints map onto ``source_decl`` nodes (``decl://...``, the
    same id scheme ``augment_graph_with_calls`` uses); type endpoints map onto
    ``record_type`` nodes (``type://...``, matching
    ``source_graph._type_node_kind``'s default) since an AST-only type
    reference cannot distinguish record/enum/typedef without the L4 surface.
    A node already present (e.g. folded from L4 with the correct kind and
    visibility) is left untouched — first-writer-wins (``add_node``).

    When *project_files* (the project's compile-unit sources + private
    headers, see ``call_graph.project_source_files``) is supplied, a ``dst``
    node whose ``dst_file`` is one of them is marked ``defined_in_project`` —
    the exact case this module exists for (a public decl/type reaching a
    private-header type/variable) would otherwise create an unannotated node
    that ``crosscheck.public_to_internal_dependency`` cannot classify as
    internal. This applies whether the node is created fresh by this edge
    *or* was already added by an earlier edge in this same call (e.g. the
    private type was first seen as another edge's ``src`` and had no
    provenance yet) — the marker is backfilled onto the existing node rather
    than only being set at creation time (Codex review), unless the node
    already carries a ``visibility`` attr (real L4 evidence, never
    overridden by this best-effort AST-only marker).

    Returns the number of edges added.
    """
    from ..model.graph_facts import _decl_node_id, _type_node_id
    from .call_graph import _file_in_project

    node_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes}

    added = 0
    for e in edges:
        if e.kind == EDGE_DECL_HAS_TYPE:
            src_id, dst_id = _decl_node_id(e.src), _type_node_id(e.dst)
            src_kind, dst_kind = "source_decl", "record_type"
        elif e.kind in (EDGE_TYPE_INHERITS, EDGE_TYPE_HAS_FIELD_TYPE):
            src_id, dst_id = _type_node_id(e.src), _type_node_id(e.dst)
            src_kind, dst_kind = "record_type", "record_type"
        else:  # DECL_REFERENCES_DECL
            src_id, dst_id = _decl_node_id(e.src), _decl_node_id(e.dst)
            src_kind, dst_kind = "source_decl", "source_decl"
        dst_in_project = bool(
            project_files and e.dst_file and _file_in_project(e.dst_file, project_files)
        )
        for node_id, node_kind, ident, is_dst in (
            (src_id, src_kind, e.src, False),
            (dst_id, dst_kind, e.dst, True),
        ):
            existing = node_by_id.get(node_id)
            if existing is None:
                attrs = (
                    {"defined_in_project": True, "def_file": e.dst_file}
                    if is_dst and dst_in_project
                    else {}
                )
                node = GraphNode(
                    id=node_id,
                    kind=node_kind,
                    label=ident,
                    provenance="type_graph",
                    confidence=e.confidence,
                    attrs=attrs,
                )
                graph.add_node(node)
                node_by_id[node_id] = node
            elif (
                is_dst
                and dst_in_project
                and not existing.attrs.get("defined_in_project")
                and not existing.attrs.get("visibility")
            ):
                # ADR-046 D2: route through register_fact, not a direct
                # existing.attrs[...] mutation — see source_graph.py's
                # identical fix (fold_source_edges) for why a direct
                # mutation is silently lost on the next to_dict()/from_dict()
                # round-trip.
                register_fact(
                    existing,
                    "type_graph",
                    e.confidence,
                    {"defined_in_project": True, "def_file": e.dst_file},
                )
        before = len(graph.edges)
        edge_attrs: dict[str, Any] = {}
        if e.role:
            edge_attrs["role"] = e.role
        if e.resolution:
            edge_attrs["resolution"] = e.resolution
        graph.add_edge(
            GraphEdge(
                src=src_id,
                dst=dst_id,
                kind=e.kind,
                provenance="type_graph",
                confidence=e.confidence,
                attrs=edge_attrs,
            )
        )
        added += len(graph.edges) - before
    return added


#: Confidence label -> rank, for picking the stronger of two duplicate edges.
_CONFIDENCE_RANK = {CONF_HIGH: 2, CONF_REDUCED: 1, "unknown": 0}


def _merge_type_edges(existing: TypeEdge, new: TypeEdge) -> TypeEdge:
    """Merge two edges sharing a ``(src, dst, kind, role)`` key from different TUs.

    Keeps the stronger ``confidence`` and fills a missing ``dst_file`` from
    whichever edge has one — a TU that doesn't include the header declaring a
    private ``dst`` sees no file for it, while another TU that does include
    it resolves it fully; picking whichever ran first would silently drop the
    richer provenance the graph needs to mark the node ``defined_in_project``
    (Codex review).
    """
    dst_file = existing.dst_file or new.dst_file
    if _CONFIDENCE_RANK.get(new.confidence, 0) > _CONFIDENCE_RANK.get(
        existing.confidence, 0
    ):
        return replace(new, dst_file=dst_file)
    if dst_file != existing.dst_file:
        return replace(existing, dst_file=dst_file)
    return existing


def merge_type_edges(per_unit: Iterable[Iterable[TypeEdge]]) -> list[TypeEdge]:
    """Fold every TU's type edges into one list, in TU order.

    Keyed by ``(src, dst, kind, role)``: two TUs emitting the same private
    type as one function's return type and another's parameter type must not
    collapse onto one role. A repeat is merged (:func:`_merge_type_edges`),
    not dropped -- a TU that includes the header declaring the private
    ``dst`` sees richer provenance than one that does not.
    """
    merged: list[TypeEdge] = []
    seen: dict[tuple[str, str, str, str], int] = {}
    for edges in per_unit:
        for e in edges:
            key = (e.src, e.dst, e.kind, e.role)
            idx = seen.get(key)
            if idx is None:
                seen[key] = len(merged)
                merged.append(e)
            else:
                merged[idx] = _merge_type_edges(merged[idx], e)
    return merged
