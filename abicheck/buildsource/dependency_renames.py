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

"""Public-dependency rename and move dispositions (G31 Phase D, ADR-048 D2).

``source_graph_findings._internal_dependency_findings`` reports every internal
declaration a public entry newly reaches as ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``.
When graph reconciliation (:mod:`.graph_reconcile`) resolved that newly reached
node as the *renamed* spelling of a node the same entry already reached in the
old graph, the "add" is one existing dependency whose target was renamed. This
module owns that disposition:

- a renamed target is reported once, as ``PUBLIC_API_INTERNAL_TYPE_RENAMED``, in
  place of the ADDED finding; the reconciliation's own ``declaration_renamed``
  finding for that same pair is not emitted again (one rename, one finding);
- a *moved* target (same qualified name, different declaring file) is not a new
  kind: the ADDED finding stays, and its description names the move;
- anything reconciliation did not establish as a rename or a move -- an ambiguous
  candidate, an unresolved or coordinates-only pair, or no pair at all -- keeps
  today's behaviour exactly. A rename is never fabricated from weak evidence.

Record before disposing: the consumed pair's match evidence is carried into the
new finding's description, so the rename stays visible on a passing run.

Reconciliation remains enrichment (ADR-028 D3 / ADR-031 D6): nothing here
touches an artifact-proven finding, and an L5 ADDED finding is replaced only by
the more specific kind that describes the same dependency.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from .graph_impact import attach_impact_metadata
from .graph_reconcile import (
    GraphReconciliation,
    ReconciledPair,
    diff_graph_reconciliation_findings,
)
from .graph_reconcile_outcome import OUTCOME_MOVED, OUTCOME_RECONCILED, OUTCOME_RENAMED

if TYPE_CHECKING:
    from ..model.graph_facts import GraphNode
    from ..model.source_graph import SourceGraphSummary

#: Outcomes whose new node is the renamed spelling of the old one.
_RENAME_OUTCOMES = frozenset({OUTCOME_RENAMED, OUTCOME_RECONCILED})


def index_reconciled_targets(
    reconciliation: GraphReconciliation,
) -> tuple[dict[str, ReconciledPair], dict[str, ReconciledPair]]:
    """``(renamed, moved)`` reconciled pairs, each keyed by the new node's id."""
    renamed: dict[str, ReconciledPair] = {}
    moved: dict[str, ReconciledPair] = {}
    for pair in reconciliation.reconciled:
        if pair.outcome in _RENAME_OUTCOMES:
            renamed[pair.new_node.id] = pair
        elif pair.outcome == OUTCOME_MOVED:
            moved[pair.new_node.id] = pair
    return renamed, moved


def renamed_dependency_targets(
    newly_reached: Iterable[tuple[str, str]],
    old_reach: Mapping[str, Iterable[str]],
    renamed_by_new_id: Mapping[str, ReconciledPair],
) -> dict[tuple[str, str], ReconciledPair]:
    """The ``(entry, target)`` pairs that are one renamed dependency, not a new one.

    A newly reached target is consumed only when its reconciled rename's *old*
    side was already reached from the same public entry in the old graph. A
    target reached for the first time, with no such old-side edge, is a genuine
    new dependency and stays an ADDED finding.
    """
    consumed: dict[tuple[str, str], ReconciledPair] = {}
    for entry, target in sorted(newly_reached):
        pair = renamed_by_new_id.get(target)
        if pair is None:
            continue
        if pair.old_node.id in old_reach.get(entry, ()):
            consumed[(entry, target)] = pair
    return consumed


def move_note(
    targets: Iterable[str], moved_by_new_id: Mapping[str, ReconciledPair]
) -> str:
    """Description sentence naming any moved target among *targets*, or ``""``."""
    moved = [moved_by_new_id[t] for t in targets if t in moved_by_new_id]
    if not moved:
        return ""
    spelled = "; ".join(f"{_label(p.old_node)} -> {_label(p.new_node)}" for p in moved)
    return (
        " Reconciled as a move (same qualified name, different declaring file): "
        f"{spelled}."
    )


def renamed_dependency_changes(
    renamed: Mapping[tuple[str, str], ReconciledPair],
    new_labels: Mapping[str, str],
    boundary: str,
    graph: SourceGraphSummary,
    path_for: Callable[[str, str], Any],
) -> list[Change]:
    """One ``PUBLIC_API_INTERNAL_TYPE_RENAMED`` finding per consumed ``(entry, target)``.

    *path_for* returns the proof path (or ``None``) for an entry and target; the
    caller owns the closure it is read from.
    """
    return [
        _renamed_change(
            new_labels.get(entry, entry),
            pair,
            boundary,
            path_for(entry, target),
            graph,
        )
        for (entry, target), pair in renamed.items()
    ]


def _renamed_change(
    entry_label: str,
    pair: ReconciledPair,
    boundary: str,
    path: Any,
    graph: SourceGraphSummary,
) -> Change:
    old_label = _label(pair.old_node)
    new_label = _label(pair.new_node)
    moved = (
        ", and it also moved to another declaring file"
        if pair.outcome == OUTCOME_RECONCILED
        else ""
    )
    change = Change(
        kind=ChangeKind.PUBLIC_API_INTERNAL_TYPE_RENAMED,
        symbol=entry_label,
        description=(
            f"Public entry {entry_label!r} still reaches the same internal "
            f"declaration/type, now spelled {new_label!r} instead of {old_label!r} "
            f"(reconciled as the same declaration{moved}; match evidence: "
            f"{pair.match_kind}). The dependency did not change, only its internal "
            "spelling did, so this is reported once as a rename rather than as an "
            "unrelated add plus remove. Not an ABI break by itself; a risk to review "
            "because a change to that internal entity reaches the public surface."
        ),
        old_value=old_label,
        new_value=new_label,
        source_location=boundary,
    )
    if path is not None:
        attach_impact_metadata(
            change,
            affected_public_roots=[entry_label],
            path=path,
            graph=graph,
            alternative_paths=[],
        )
    return change


def reconciliation_findings(
    reconciliation: GraphReconciliation,
    old: SourceGraphSummary,
    new: SourceGraphSummary,
    consumed_new_ids: frozenset[str],
) -> list[Any]:
    """The declaration-level reconciliation findings, minus consumed renames.

    A pair whose new node was already reported as a public-dependency rename
    (:func:`renamed_dependency_changes`) is not reported a second time here. Every
    other pair is passed through unchanged, so a pair not consumed keeps its
    ``declaration_renamed``/``declaration_moved``/... finding exactly as before.
    """
    remaining = replace(
        reconciliation,
        reconciled=[
            p
            for p in reconciliation.reconciled
            if p.new_node.id not in consumed_new_ids
        ],
    )
    return diff_graph_reconciliation_findings(remaining, old, new)


def _label(node: GraphNode) -> str:
    return node.label or node.id
