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

"""The release's recorded member-to-library dependency relation, as drawn.

The *recording* half -- reading each member's ELF, PE/COFF or Mach-O
dependency facts off its snapshots -- lives in
``workflows/release_dependency_facts.py`` and lands on the release member
entry as ``libraries[].dependencies``.

This module is the *projection* half. It reads only the published release
document (the same mapping ``-o json`` writes) and returns plain values: one
node per member, one node per needed library no member provides, and one edge
per recorded relation, each edge saying on which side(s) it was recorded.
A member whose dependency facts were not evaluated is a node that says so; it
is never silently a node with no edges.

Names are matched per format: an ELF ``DT_NEEDED`` entry is its soname
verbatim, while a PE import (a DLL name) and a Mach-O install name are matched
by their final path component, case-insensitively for PE (Windows file names).
Layout is deterministic: layered by dependency depth (a library that needs
nothing recorded sits at depth 0), names sorted within a layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

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

#: Dependency state of a node, as drawn. A library no member provides, or a
#: scope-only member, was never read, so its state is ``not_evaluated``.
DEPS_RECORDED = "recorded"
DEPS_PARTIAL = "partial"
DEPS_NOT_EVALUATED = "not_evaluated"


def _match_key(name: str, fmt: str) -> str:
    """The name two dependency records are compared by, for *fmt*."""
    if fmt == "pe":
        return PurePosixPath(name.replace("\\", "/")).name.lower()
    if fmt == "macho":
        return PurePosixPath(name.replace("\\", "/")).name
    return name


@dataclass(frozen=True, slots=True)
class GraphNode:
    name: str
    status: str  #: breaking/api_break/compatible/no_change/unchecked/external
    detail: str  #: verdict or scope state, as recorded
    layer: int
    deps: str  #: DEPS_RECORDED/DEPS_PARTIAL/DEPS_NOT_EVALUATED


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
    members_with_facts: int  #: recorded or partial
    members_partial: int  #: some fact family on a side was not captured
    members_not_evaluated: int
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


def _evaluated_sides(deps: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """The sides that recorded a dependency list; a side whose state is
    ``not_evaluated`` carries no ``needed`` and is not one of them."""
    if not isinstance(deps, Mapping):
        return []
    return [
        (side, facts)
        for side in ("old", "new")
        if isinstance(facts := deps.get(side), Mapping) and "needed" in facts
    ]


def compute_release_dependency_graph(
    document: Mapping[str, Any],
) -> ReleaseDependencyGraph:
    """Nodes and edges recorded in *document*; see the module docstring."""
    members = _member_rows(document)
    status: dict[str, tuple[str, str]] = {}
    deps_state: dict[str, str] = {}
    # match key -> the member node it names
    provides: dict[str, str] = {}
    # match key -> the spelling first seen for a library no member provides
    external_names: dict[str, str] = {}
    # source member -> match key -> (spelling, sides it was recorded on)
    needed: dict[str, dict[str, tuple[str, set[str]]]] = {}
    for m in members:
        name = str(m.get("library"))
        verdict = str(m.get("verdict"))
        status[name] = (_STATUS_FOR_VERDICT.get(verdict, verdict.lower()), verdict)
        evaluated = _evaluated_sides(m.get("dependencies"))
        fmt = str(evaluated[0][1].get("format", "elf")) if evaluated else "elf"
        provides.setdefault(_match_key(name, fmt), name)
        if not evaluated:
            deps_state[name] = DEPS_NOT_EVALUATED
            continue
        deps_state[name] = DEPS_RECORDED
        for side, facts in evaluated:
            side_fmt = str(facts.get("format", "elf"))
            if facts.get("incomplete"):
                deps_state[name] = DEPS_PARTIAL
            if facts.get("soname"):
                provides.setdefault(_match_key(str(facts["soname"]), side_fmt), name)
            for lib in facts.get("needed") or ():
                key = _match_key(str(lib), side_fmt)
                slot = needed.setdefault(name, {}).setdefault(key, (str(lib), set()))
                slot[1].add(side)
    scope = document.get("comparison_scope")
    scope_members = scope.get("members") if isinstance(scope, Mapping) else None
    for m in scope_members or ():
        if not isinstance(m, Mapping):
            continue
        name = str(m.get("name"))
        if name not in status:
            status[name] = ("unchecked", str(m.get("state")))
            deps_state[name] = DEPS_NOT_EVALUATED
            # The member's file name may be the provider of a PE or Mach-O need
            # too, so register it under every format's key (first one wins).
            for scope_fmt in ("elf", "pe", "macho"):
                provides.setdefault(_match_key(name, scope_fmt), name)

    edge_rows: list[GraphEdge] = []
    adjacency: dict[str, set[str]] = {}
    for source in sorted(needed):
        for key in sorted(needed[source]):
            spelling, sides = needed[source][key]
            target = provides.get(key)
            if target is None:
                target = external_names.setdefault(key, spelling)
                status.setdefault(target, ("external", "not a release member"))
                deps_state.setdefault(target, DEPS_NOT_EVALUATED)
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
    kept = set(ranked[:MAX_GRAPH_NODES])
    nodes = tuple(
        GraphNode(
            name=n,
            status=status[n][0],
            detail=status[n][1],
            layer=layers[n],
            deps=deps_state.get(n, DEPS_NOT_EVALUATED),
        )
        for n in sorted(kept, key=lambda n: (-layers[n], n))
    )
    edges = tuple(e for e in edge_rows if e.source in kept and e.target in kept)
    states = [deps_state.get(str(m.get("library"))) for m in members]
    return ReleaseDependencyGraph(
        nodes=nodes,
        edges=edges,
        omitted_nodes=len(status) - len(kept),
        omitted_edges=len(edge_rows) - len(edges),
        members_with_facts=sum(s in (DEPS_RECORDED, DEPS_PARTIAL) for s in states),
        members_partial=sum(s == DEPS_PARTIAL for s in states),
        members_not_evaluated=sum(s == DEPS_NOT_EVALUATED for s in states),
        members_total=len(members),
    )
