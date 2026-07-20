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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .source_graph import GraphEdge, SourceGraphSummary


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


def attach_impact_metadata(
    change: Any,
    *,
    affected_public_roots: list[str],
    path: list[GraphEdge],
    graph: SourceGraphSummary,
) -> None:
    """Attach B3's structured impact fields to an existing ``Change`` object
    in place. Never constructs a new ``Change`` — enrichment only.
    """
    change.affected_public_roots = list(affected_public_roots) or None
    change.impact_proof_path = structured_proof_path(graph, path) or None
    change.impact_is_direct = (
        is_direct_path(path) if path or affected_public_roots else None
    )
