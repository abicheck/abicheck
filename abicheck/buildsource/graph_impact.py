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

"""Structured graph impact/proof-path data attached to findings (G31 Phase B
B3, ADR-048).

``graph explain`` (``source_graph.localize_symbol``) has always produced
*prose* proof paths. This module adds the structured, machine-readable
equivalent — a list of node/edge references, not a formatted string — so a
JSON/SARIF/JUnit consumer can walk the evidence programmatically instead of
parsing ``description`` text.

Deliberately **enriches an existing finding**, never creates a duplicate
synthetic one (mirrors ``source_graph_findings.py``'s own "explain, don't
duplicate" pattern): call :func:`structured_proof_path` on a path a detector
already computed (e.g. ``source_graph_findings._dependency_path``'s
``list[GraphEdge]``) and set the result on the ``Change`` object that
detector was already going to emit.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .call_graph import (
    CALL_KIND_FUNCTION_POINTER,
    CALL_KIND_VIRTUAL,
    RESOLUTION_OVERAPPROX,
)
from .graph_facts import CONF_HIGH
from .source_graph_query import PUBLIC_VISIBILITIES

if TYPE_CHECKING:
    from ..model.graph_facts import GraphEdge, GraphNode
    from ..model.source_graph import SourceGraphSummary


def structured_proof_path(
    graph: SourceGraphSummary, path: list[GraphEdge]
) -> list[dict[str, Any]]:
    """Render a shortest-path edge chain (as returned by
    ``source_graph_findings._dependency_path``) as a list of node/edge
    reference dicts, in traversal order: ``node, edge, node, edge, node, ...``.

    Each node entry carries ``{"type": "node", "id", "kind", "label"}``; each
    edge entry carries ``{"type": "edge", "kind", "role", "confidence"}``.
    Returns ``[]`` for an empty path (the entry node reached the target
    directly with no traversal, or no path was found).
    """
    if not path:
        return []
    labels = {n.id: (n.label or n.id) for n in graph.nodes}
    kinds = {n.id: n.kind for n in graph.nodes}
    out: list[dict[str, Any]] = [
        {
            "type": "node",
            "id": path[0].src,
            "kind": kinds.get(path[0].src, ""),
            "label": labels.get(path[0].src, path[0].src),
        }
    ]
    for e in path:
        out.append(
            {
                "type": "edge",
                "kind": e.kind,
                "role": str(e.attrs.get("role", "")),
                "confidence": e.confidence,
            }
        )
        out.append(
            {
                "type": "node",
                "id": e.dst,
                "kind": kinds.get(e.dst, ""),
                "label": labels.get(e.dst, e.dst),
            }
        )
    return out


def is_direct_path(path: list[GraphEdge]) -> bool:
    """Whether *path* is a single-edge (direct) dependency rather than a
    multi-hop (transitive) one. An empty path (entry == target) counts as
    direct.
    """
    return len(path) <= 1


#: ADR-046 D6's six-tier proof-path preference order, numbered by how many of
#: this module's *computable* tiers a path satisfies (lower is stronger).
#: Tier 1 ("consumer-proven") became computable in G29 Phase 4 (ADR-057):
#: ``impact.consumer_graph`` folds a real ``--used-by`` binary's requirements
#: onto the same ``binary_symbol://`` node ids the library graph exports, so a
#: path whose endpoint some consumer actually requires is externally proven
#: rather than merely inferred. Tiers 2-6 map onto the constants below in the
#: ADR's own order.
_TIER_CONSUMER_PROVEN = 0  # ADR tier 1: a real consumer requires the endpoint
_TIER_EXACT = 1  # ADR tier 2: every edge is CONF_HIGH
_TIER_PUBLIC_STRUCTURAL = 2  # ADR tier 3: every node on the path is public
_TIER_MULTI_PRODUCER = 3  # ADR tier 4: some edge has >1 distinct fact producer
_TIER_REDUCED = 4  # ADR tier 5: no stronger signal found (the residual case)
_TIER_OVERAPPROX = 5  # ADR tier 6: crosses a virtual/function-pointer call


def _edge_is_overapprox(edge: GraphEdge) -> bool:
    """Whether *edge* is a virtual/function-pointer call or otherwise
    resolved ``overapprox`` (ADR-031 D4/D9, ADR-046 D5's ``call_kind``
    vocabulary — the same signal ``internal_leak.CALL_GRAPH_TRAVERSAL_POLICY.
    effect_transitions`` downgrades a walk's precision on).
    """
    resolved = edge.resolved or edge.attrs
    call_kind = resolved.get("call_kind")
    if call_kind in (CALL_KIND_VIRTUAL, CALL_KIND_FUNCTION_POINTER):
        return True
    return resolved.get("resolution") == RESOLUTION_OVERAPPROX


def _node_is_public(node: GraphNode | None) -> bool:
    """Mirrors :func:`_edge_is_overapprox`'s ``resolved or attrs`` read order
    (CodeRabbit review): a fully-processed node always has ``attrs`` synced
    to ``resolved`` (``ensure_facts_and_resolve``), but a bare ``GraphNode``
    constructed directly without going through that step could have
    visibility only in ``resolved`` — preferring it keeps this in step with
    every other tier check in this module.
    """
    if node is None:
        return False
    resolved = node.resolved or node.attrs
    return resolved.get("visibility") in PUBLIC_VISIBILITIES


def _path_node_ids(path: list[GraphEdge]) -> list[str]:
    if not path:
        return []
    return [path[0].src, *(e.dst for e in path)]


def _consumer_required_nodes(graph: SourceGraphSummary) -> frozenset[str]:
    """Node ids some consumer in *graph* requires (ADR-057) — the tier-1
    signal :func:`_graph_path_tier` reads.

    The canonical implementation, re-exported as
    ``impact.consumer_graph.consumer_required_symbol_nodes``: that module
    already depends on this one (one-directional), so keeping the definition
    here means the selector never has to import back into ``impact/`` to
    compute its own tier. Empty — and therefore inert — for every graph with
    no consumer facts folded in.
    """
    return frozenset(
        e.dst for e in graph.edges if e.kind == "CONSUMER_REQUIRES_SYMBOL"
    )


def _graph_path_tier(
    node_by_id: dict[str, GraphNode],
    path: list[GraphEdge],
    consumer_required: frozenset[str] = frozenset(),
) -> int:
    """This path's ADR-046 D6 tier — see the module-level ``_TIER_*``
    constants. Overapprox is checked first and wins regardless of any other
    signal: a path that crosses a virtual/function-pointer call is never
    "exact", however high-confidence its other edges are.

    That precedence deliberately applies to the consumer-proven tier too
    (ADR-057): a path crossing a virtual/function-pointer call is an
    over-approximation of the real dispatch chain, so the fact that *some*
    consumer requires its endpoint does not make the *chain* proven. Tier 1
    therefore means "consumer-proven **and** exactly resolved" — the
    conservative reading, matching how ``effect_transitions`` already refuses
    to let a degraded walk present itself as an exact one.

    Tier 1 tests the path's **endpoint**, not any node on it (CodeRabbit
    review): the tier answers "is this path's subject something a real
    consumer requires", and a consumer requiring some *intermediate* hop says
    nothing about the entity the path actually points at. Matching anywhere
    would let a path that merely passes through a required node — then
    continues on to an unrelated target — outrank a genuinely exact one.
    """
    if any(_edge_is_overapprox(e) for e in path):
        return _TIER_OVERAPPROX
    if path and path[-1].dst in consumer_required:
        return _TIER_CONSUMER_PROVEN
    if all(e.confidence == CONF_HIGH for e in path):
        return _TIER_EXACT
    if all(_node_is_public(node_by_id.get(nid)) for nid in _path_node_ids(path)):
        return _TIER_PUBLIC_STRUCTURAL
    if any(len({f.producer for f in e.facts}) > 1 for e in path):
        return _TIER_MULTI_PRODUCER
    return _TIER_REDUCED


def select_preferred_graph_path(
    graph: SourceGraphSummary, paths: list[list[GraphEdge]]
) -> list[GraphEdge]:
    """Pick the strongest candidate proof path among *paths* (ADR-046 D6).

    Unlike :func:`~abicheck.internal_leak.select_preferred_path`'s plain
    ``list[str]`` layout-walk paths, a structured ``list[GraphEdge]`` path
    carries per-edge confidence, fact-producer count (ADR-046 D2), and —
    via each hop's node ``visibility`` attr — public/private surface
    information, so this selector implements five of the ADR's six tiers
    (see :func:`_graph_path_tier`). "Consumer-proven" (tier 1) is read
    straight off *graph*: it applies whenever a consumer graph has been
    folded in (``impact.consumer_graph.join_consumer_graph``, ADR-057) and is
    inert — an empty set, exactly as before that module existed — for every
    graph without one, which is every run without ``--used-by``. Only a
    genuinely finer "reduced-confidence name resolution" axis (tier 5, beyond
    the residual case this collapses into) is still left for a future slice.

    Ties within a tier keep the shortest path (fewest hops), matching
    :func:`~abicheck.internal_leak.select_preferred_path`'s own tie-break.
    Returns ``[]`` for an empty *paths* list; a single-candidate list is
    returned unchanged with no tier computation (the common case today, same
    as before this function existed).
    """
    if not paths:
        return []
    if len(paths) == 1:
        return paths[0]
    node_by_id = {n.id: n for n in graph.nodes}
    consumer_required = _consumer_required_nodes(graph)
    return min(
        paths,
        key=lambda p: (_graph_path_tier(node_by_id, p, consumer_required), len(p)),
    )


def _path_occurrence_id(path: list[GraphEdge]) -> str | None:
    """A stable id for *path*'s underlying graph occurrences (ADR-046 D1,
    ADR-052's ``occurrence_id`` follow-up), independent of ``description``
    text — distinct from ``reporter._finding_id``, which deliberately still
    includes it.

    Folds every edge's own :attr:`GraphEdge.occurrences` (ADR-046 D1's
    per-call-site trail) into one hash. ``None`` when no edge on *path*
    carries any occurrence-level attrs — still the common case today, since
    no producer populates them yet (same opt-in cost model as D1 itself: this
    stays free until one does).
    """
    ids = sorted({oid for e in path for oid in e.occurrences})
    if not ids:
        return None
    blob = json.dumps(ids, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def attach_impact_metadata(
    change: Any,
    *,
    affected_public_roots: list[str],
    path: list[GraphEdge],
    graph: SourceGraphSummary,
    alternative_paths: list[list[GraphEdge]] | None = None,
) -> None:
    """Attach B3's structured impact fields to an existing ``Change`` object
    in place. Never constructs a new ``Change`` — enrichment only.

    *alternative_paths* (ADR-046 D6, G29 Phase 2 follow-up): the runner-up
    candidates *path* was preferred over, when the caller had more than one
    (e.g. via :func:`select_preferred_graph_path`). Capped at 3, matching
    :func:`~abicheck.internal_leak._build_leak_change`'s own "+N more paths"
    cap — ``change.impact_discarded_path_count`` records how many more were
    dropped beyond that cap.
    """
    change.affected_public_roots = list(affected_public_roots) or None
    change.impact_proof_path = structured_proof_path(graph, path) or None
    change.impact_is_direct = (
        is_direct_path(path) if path or affected_public_roots else None
    )
    change.impact_occurrence_id = _path_occurrence_id(path)
    alts = [p for p in (alternative_paths or []) if p is not path]
    cap = 3
    kept = alts[:cap]
    change.impact_alternative_paths = [
        structured_proof_path(graph, p) for p in kept
    ] or None
    change.impact_discarded_path_count = max(0, len(alts) - len(kept))
