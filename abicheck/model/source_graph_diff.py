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

"""The structural delta between two L5 source graphs: :class:`GraphSummaryDiff`
and its changed-relation entry :class:`GraphRelationChange` (ADR-077 D4).

Values only; ``buildsource/source_graph_compare.py`` computes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph_facts import GraphEdge, GraphNode


@dataclass(frozen=True)
class GraphRelationChange:
    """One dependency (``GraphEdge.key()``: src, dst, kind) present on both
    sides whose role set differs (ADR-077 D4) -- e.g. ``param`` -> ``return``.

    ``old_roles``/``new_roles`` are each side's sorted roles for that
    dependency; ``""`` stands for an edge whose producer stamped no role.
    """

    src: str
    dst: str
    kind: str
    old_roles: tuple[str, ...]
    new_roles: tuple[str, ...]

    @property
    def removed_roles(self) -> tuple[str, ...]:
        return tuple(r for r in self.old_roles if r and r not in self.new_roles)

    @property
    def added_roles(self) -> tuple[str, ...]:
        return tuple(r for r in self.new_roles if r and r not in self.old_roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge": self.kind,
            "src": self.src,
            "dst": self.dst,
            "old_roles": list(self.old_roles),
            "new_roles": list(self.new_roles),
        }


@dataclass
class GraphSummaryDiff:
    """Structural delta between two :class:`SourceGraphSummary` snapshots.

    A pure structural diff (which nodes/edges entered or left the graph) — the
    foundation the ``graph compare`` command renders and that a later phase maps
    onto the ADR-031 D6 secondary findings. Per ADR-028 D3 / ADR-031 D6 these
    deltas *explain and prioritize*; they never decide an ABI break on their own.

    ``added_edges``/``removed_edges`` are keyed on the coarse
    ``GraphEdge.key()`` (ADR-046 D1's promise to existing callers). A
    dependency kept on both sides with a different role set is a
    ``changed_edges`` entry instead -- never a removed+added pair, never
    dropped (ADR-077 D4). When exactly one side carries role-less edges for
    that dependency (a graph stored before its producer stamped roles), the
    role sets cannot be compared: the entry goes to ``role_unknown_edges``,
    a coverage gap rather than a change.
    """

    added_nodes: list[GraphNode] = field(default_factory=list)
    removed_nodes: list[GraphNode] = field(default_factory=list)
    added_edges: list[GraphEdge] = field(default_factory=list)
    removed_edges: list[GraphEdge] = field(default_factory=list)
    changed_edges: list[GraphRelationChange] = field(default_factory=list)
    role_unknown_edges: list[GraphRelationChange] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(
            self.added_nodes
            or self.removed_nodes
            or self.added_edges
            or self.removed_edges
            or self.changed_edges
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "added_nodes": [n.to_dict() for n in self.added_nodes],
            "removed_nodes": [n.to_dict() for n in self.removed_nodes],
            "added_edges": [e.to_dict() for e in self.added_edges],
            "removed_edges": [e.to_dict() for e in self.removed_edges],
            "changed_edges": [c.to_dict() for c in self.changed_edges],
            "role_unknown_edges": [c.to_dict() for c in self.role_unknown_edges],
            "counts": {
                "added_nodes": len(self.added_nodes),
                "removed_nodes": len(self.removed_nodes),
                "added_edges": len(self.added_edges),
                "removed_edges": len(self.removed_edges),
                "changed_edges": len(self.changed_edges),
                "role_unknown_edges": len(self.role_unknown_edges),
            },
        }
