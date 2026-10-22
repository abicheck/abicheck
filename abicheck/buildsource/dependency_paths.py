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

"""Public-dependency closure and proof paths over an L5 source graph.

Moved out of ``source_graph_findings.py`` (which sits at its no-growth
architecture baseline) so that module can host the G31 Phase D public-dependency
rename dispositions instead. The two helpers are unchanged: a public-entry
reachability closure over the dependency edge kinds, and one concrete shortest
edge chain (the "proof path") for an entry/target pair.
"""

from __future__ import annotations

from collections import deque

from ..model.graph_facts import GraphEdge
from ..model.source_graph import SourceGraphSummary
from ..model.source_graph_query import is_public_dependency_node


def _dependency_reachability(
    graph: SourceGraphSummary, edge_kinds: frozenset[str]
) -> dict[str, frozenset[str]]:
    """For each public entry (exported decl or public type), what it reaches.

    Generalizes :func:`_public_entry_call_reachability` from ``DECL_CALLS_DECL``
    alone to *edge_kinds* (normally :data:`DEPENDENCY_EDGE_KINDS`, or the
    old/new-common subset a version diff must restrict to — see
    :func:`_common_dependency_edge_kinds`): a public struct's private base class
    (``TYPE_INHERITS``) or private field type (``TYPE_HAS_FIELD_TYPE``), a
    function's private parameter type (``DECL_HAS_TYPE``), and a body reading a
    private constant (``DECL_REFERENCES_DECL``) are exactly the "not a call at
    all" risks ADR-041 opens with — a call-only closure never sees them.

    Entries are every node :func:`is_public_dependency_node` accepts: a decl
    backing an exported symbol (``SOURCE_DECL_MAPS_TO_SYMBOL``), *or* any
    decl/type node with public-header visibility — not exported-symbol-backed
    decls alone (tenth Codex review). A public inline/template/constexpr
    function or a public variable declared in a public header commonly has no
    exported binary symbol of its own (inlined at every call site, or never
    emitted standalone), so restricting entries to
    ``SOURCE_DECL_MAPS_TO_SYMBOL`` missed exactly the ADR's own headline
    example — ``inline int f() { return detail::SECRET; }`` — whenever ``f``
    isn't separately exported. ``cross_source_checks.py``'s intra-version check already
    treats a ``visibility="public_header"`` decl as public
    (``is_public_dependency_node``, shared since the fourth review); this
    closure now uses the identical rule, so a public type is no longer a
    special case (:func:`_public_types` is unused here now — public-header
    visibility already covers it).
    Returns ``{}`` when *edge_kinds* is empty or the graph carries none of them,
    so callers can skip the comparison entirely.
    """
    adjacency: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind in edge_kinds:
            adjacency.setdefault(e.src, []).append(e.dst)
    if not adjacency:
        return {}
    exported_decls = {
        e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    node_by_id = {n.id: n for n in graph.nodes}
    entries = {
        n.id
        for n in graph.nodes
        if is_public_dependency_node(n.id, node_by_id, exported_decls)
    }
    out: dict[str, frozenset[str]] = {}
    for entry in entries:
        seen: set[str] = set()
        stack = list(adjacency.get(entry, []))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adjacency.get(node, []))
        out[entry] = frozenset(seen)
    return out


def _dependency_path(
    graph: SourceGraphSummary, edge_kinds: frozenset[str], entry: str, target: str
) -> list[GraphEdge] | None:
    """One concrete shortest edge chain from *entry* to *target* over *edge_kinds*.

    ADR-041 P0 roadmap item 3 ("graph explain proof path"): a reachability
    fact (:func:`_dependency_reachability` already proved *target* is
    reachable from *entry*) is a bare assertion until a reader can see *how* —
    the actual edge-by-edge chain, not just an endpoint list. BFS over the
    same *edge_kinds* adjacency, tracking one predecessor edge per node so the
    chain can be reconstructed once *target* is reached (shortest, not every
    path — one witness is enough to explain a finding). Returns ``[]`` when
    *entry* == *target*, or ``None`` if no such path exists (defensive; should
    not happen for a (entry, target) pair sourced from
    :func:`_dependency_reachability`'s own output).
    """
    if entry == target:
        return []
    adjacency: dict[str, list[GraphEdge]] = {}
    for e in graph.edges:
        if e.kind in edge_kinds:
            adjacency.setdefault(e.src, []).append(e)
    visited = {entry}
    queue: deque[str] = deque([entry])
    came_from: dict[str, GraphEdge] = {}
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for e in adjacency.get(node, []):
            if e.dst in visited:
                continue
            visited.add(e.dst)
            came_from[e.dst] = e
            queue.append(e.dst)
    if target not in came_from:
        return None
    path: list[GraphEdge] = []
    cur = target
    while cur != entry:
        e = came_from[cur]
        path.append(e)
        cur = e.src
    path.reverse()
    return path
