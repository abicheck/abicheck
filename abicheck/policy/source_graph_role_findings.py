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

"""Role-change findings over the L5 structural graph diff (ADR-077 D4).

:func:`~abicheck.buildsource.source_graph_compare.diff_edge_relations`
reports every dependency kept on both sides with a different role set. This
module decides which of those reach a report:

- only usage roles (:data:`ROLE_COVERAGE_MATRIX` kinds). A role-shaped
  discriminator such as ``CALLBACK_MAY_INVOKE``'s slot id is not a usage role;
- only dependencies of a public entry (on either side) -- an internal
  refactor that re-plumbs a private helper is not a public-surface risk;
- a comparable change is ``PUBLIC_DEPENDENCY_ROLE_CHANGED`` (RISK, like
  every L5 graph finding: it explains, the artifact diff decides);
- a change that is not comparable -- one side carries no role data, or a
  changed role is one the two sides' producers disagree on having examined
  (*untrusted_roles*) -- is ``PUBLIC_DEPENDENCY_ROLE_UNKNOWN``, a COMPATIBLE
  coverage note, one per edge kind. It never reads as a change, and it never
  disappears.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..buildsource.source_graph_compare import diff_edge_relations
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.source_graph import EVIDENCE_TIER_L5, SourceGraphSummary
from ..model.source_graph_coverage import ROLE_COVERAGE_MATRIX
from ..model.source_graph_diff import GraphRelationChange
from ..model.source_graph_query import is_public_dependency_node, label_map

#: How many example dependencies a coverage note names before "and N more".
_UNKNOWN_EXAMPLES = 3


def role_change_findings(
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    untrusted_roles: Mapping[str, frozenset[str]],
) -> list[Change]:
    """Role-change and role-coverage findings for public dependencies.

    *untrusted_roles* is ``{edge kind: roles}`` whose coverage the two
    sides' producers disagree on (``source_graph_findings.
    _untrusted_dependency_roles``): a role one side never examined cannot
    have changed as far as the evidence shows.
    """
    changed, unknown = diff_edge_relations(old.edges, new.edges)
    old_public, new_public = _PublicEntries(old), _PublicEntries(new)

    def relevant(rel: GraphRelationChange) -> bool:
        return rel.kind in ROLE_COVERAGE_MATRIX and (
            old_public.has(rel.src) or new_public.has(rel.src)
        )

    labels = {**label_map(old), **label_map(new)}
    boundary = f"[{EVIDENCE_TIER_L5}]"
    findings: list[Change] = []
    gaps: dict[str, list[GraphRelationChange]] = {}
    for rel in filter(relevant, unknown):
        gaps.setdefault(rel.kind, []).append(rel)
    for rel in filter(relevant, changed):
        disputed = untrusted_roles.get(rel.kind, frozenset())
        if disputed & {*rel.removed_roles, *rel.added_roles}:
            gaps.setdefault(rel.kind, []).append(rel)
            continue
        findings.append(_changed_finding(rel, labels, boundary))
    findings += [
        _unknown_finding(kind, rels, labels, boundary)
        for kind, rels in sorted(gaps.items())
    ]
    return findings


class _PublicEntries:
    """``is_public_dependency_node`` over one graph, indexes built once."""

    def __init__(self, graph: SourceGraphSummary) -> None:
        self._nodes = {n.id: n for n in graph.nodes}
        self._exported = {
            e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
        }

    def has(self, node_id: str) -> bool:
        return is_public_dependency_node(node_id, self._nodes, self._exported)


def _roles(roles: tuple[str, ...]) -> str:
    return ", ".join(r or "<no role>" for r in roles) or "<none>"


def _changed_finding(
    rel: GraphRelationChange, labels: dict[str, str], boundary: str
) -> Change:
    src = labels.get(rel.src, rel.src)
    dst = labels.get(rel.dst, rel.dst)
    return Change(
        kind=ChangeKind.PUBLIC_DEPENDENCY_ROLE_CHANGED,
        symbol=src,
        description=(
            f"Public entry {src!r} still depends on {dst!r} ({rel.kind}) but its "
            f"role changed: {_roles(rel.old_roles)} → {_roles(rel.new_roles)}. "
            "Source-graph evidence to prioritize review; the artifact diff "
            "decides whether the signature or layout broke."
        ),
        old_value=_roles(rel.old_roles),
        new_value=_roles(rel.new_roles),
        source_location=boundary,
    )


def _unknown_finding(
    kind: str,
    rels: list[GraphRelationChange],
    labels: dict[str, str],
    boundary: str,
) -> Change:
    examples = ", ".join(
        f"{labels.get(r.src, r.src)} -> {labels.get(r.dst, r.dst)}"
        for r in rels[:_UNKNOWN_EXAMPLES]
    )
    more = len(rels) - _UNKNOWN_EXAMPLES
    if more > 0:
        examples += f", and {more} more"
    return Change(
        kind=ChangeKind.PUBLIC_DEPENDENCY_ROLE_UNKNOWN,
        symbol=kind,
        description=(
            f"{len(rels)} public {kind} dependenc{'y' if len(rels) == 1 else 'ies'} "
            f"differ in role data the two graphs cannot compare ({examples}): one "
            "side records no role, or a role only one side's producer examined. "
            "A role change there is neither confirmed nor ruled out."
        ),
        old_value="role not comparable",
        new_value="role not comparable",
        source_location=boundary,
    )
