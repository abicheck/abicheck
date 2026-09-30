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

"""The release's recorded member-to-library dependency relation.

Two halves, following this package's compute/render split:

* :func:`member_dependencies` is the *recording* half. It copies the one
  dependency fact the snapshots already carry -- the ELF dynamic section's
  ``DT_SONAME`` and ``DT_NEEDED`` entries -- onto a release member entry as
  ``libraries[].dependencies`` (release schema 1.9), per side. Nothing is
  inferred: a side without ELF metadata (a PE or Mach-O member, or a side
  that was never read) is simply absent from the block.
* :func:`compute_release_dependency_graph` is the *projection* half. It reads
  only the published release document (the same mapping ``-o json`` writes)
  and returns plain values: one node per member, one node per needed library
  no member provides, and one edge per recorded ``DT_NEEDED`` relation, each
  edge saying on which side(s) it was recorded. Layout is deterministic:
  layered by dependency depth (a library that needs nothing recorded sits at
  depth 0), names sorted within a layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..model.snapshot import AbiSnapshot

#: Nodes drawn before the rest are summarised with a disclosed count.
MAX_GRAPH_NODES = 60

#: Verdict -> node status. Anything not listed keeps its lower-cased verdict.
_STATUS_FOR_VERDICT = {
    "BREAKING": "breaking",
    "API_BREAK": "api_break",
    "COMPATIBLE": "compatible",
    "COMPATIBLE_WITH_RISK": "compatible",
    "NO_CHANGE": "no_change",
}


def _side_dependencies(snapshot: AbiSnapshot | None) -> dict[str, object] | None:
    elf = None if snapshot is None else getattr(snapshot, "elf", None)
    if elf is None:
        return None
    return {"soname": elf.soname or None, "needed": list(elf.needed)}


def member_dependencies(
    old_snapshot: AbiSnapshot | None, new_snapshot: AbiSnapshot | None
) -> dict[str, object] | None:
    """``{"old": {...}, "new": {...}}`` of recorded ELF dependency facts, or
    ``None`` when neither side recorded any."""
    block: dict[str, object] = {
        side: facts
        for side, facts in (
            ("old", _side_dependencies(old_snapshot)),
            ("new", _side_dependencies(new_snapshot)),
        )
        if facts is not None
    }
    return block or None


@dataclass(frozen=True, slots=True)
class GraphNode:
    name: str
    status: str  #: breaking/api_break/compatible/no_change/unchecked/external
    detail: str  #: verdict or scope state, as recorded
    layer: int


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str  #: the member that records the dependency
    target: str  #: the node it needs
    sides: str  #: "old", "new" or "old+new"


@dataclass(frozen=True, slots=True)
class ReleaseDependencyGraph:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    omitted_nodes: int
    omitted_edges: int
    members_with_facts: int
    members_total: int


def _member_rows(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [m for m in document.get("libraries") or () if isinstance(m, Mapping)]


def _depth(name: str, edges: Mapping[str, set[str]], memo: dict[str, int]) -> int:
    """Longest recorded dependency chain below *name*; a cycle is cut at
    the first revisit, so the result is defined for any recorded graph."""
    if name in memo:
        return memo[name]
    memo[name] = 0  # cycle guard
    targets = sorted(edges.get(name, ()))
    memo[name] = 1 + max((_depth(t, edges, memo) for t in targets), default=-1)
    return memo[name]


def compute_release_dependency_graph(
    document: Mapping[str, Any], *, max_nodes: int = MAX_GRAPH_NODES
) -> ReleaseDependencyGraph:
    """Nodes and edges recorded in *document*; see the module docstring."""
    members = _member_rows(document)
    status: dict[str, tuple[str, str]] = {}
    provides: dict[str, str] = {}
    needed: dict[str, dict[str, set[str]]] = {}
    with_facts = 0
    for m in members:
        name = str(m.get("library"))
        verdict = str(m.get("verdict"))
        status[name] = (_STATUS_FOR_VERDICT.get(verdict, verdict.lower()), verdict)
        provides.setdefault(name, name)
        deps = m.get("dependencies")
        if not isinstance(deps, Mapping) or not deps:
            continue
        with_facts += 1
        for side in ("old", "new"):
            facts = deps.get(side)
            if not isinstance(facts, Mapping):
                continue
            if facts.get("soname"):
                provides.setdefault(str(facts["soname"]), name)
            for lib in facts.get("needed") or ():
                needed.setdefault(name, {}).setdefault(str(lib), set()).add(side)
    scope = document.get("comparison_scope")
    scope_members = scope.get("members") if isinstance(scope, Mapping) else None
    for m in scope_members or ():
        if not isinstance(m, Mapping):
            continue
        name = str(m.get("name"))
        if name not in status:
            status[name] = ("unchecked", str(m.get("state")))
            provides.setdefault(name, name)

    edge_rows: list[GraphEdge] = []
    adjacency: dict[str, set[str]] = {}
    for source in sorted(needed):
        for lib in sorted(needed[source]):
            target = provides.get(lib, lib)
            if target not in status:
                status[target] = ("external", "not a release member")
            sides = needed[source][lib]
            edge_rows.append(
                GraphEdge(
                    source=source,
                    target=target,
                    sides="old+new" if len(sides) == 2 else next(iter(sides)),
                )
            )
            adjacency.setdefault(source, set()).add(target)

    memo: dict[str, int] = {}
    layers = {name: _depth(name, adjacency, memo) for name in sorted(status)}
    # Members first (by severity, then name), then externals, for the cap.
    order = {
        "breaking": 0,
        "api_break": 1,
        "compatible": 2,
        "no_change": 3,
        "unchecked": 4,
        "external": 6,
    }
    ranked = sorted(status, key=lambda n: (order.get(status[n][0], 5), n))
    kept = set(ranked[: max(0, max_nodes)])
    nodes = tuple(
        GraphNode(name=n, status=status[n][0], detail=status[n][1], layer=layers[n])
        for n in sorted(kept, key=lambda n: (-layers[n], n))
    )
    edges = tuple(e for e in edge_rows if e.source in kept and e.target in kept)
    return ReleaseDependencyGraph(
        nodes=nodes,
        edges=edges,
        omitted_nodes=len(status) - len(kept),
        omitted_edges=len(edge_rows) - len(edges),
        members_with_facts=with_facts,
        members_total=len(members),
    )
