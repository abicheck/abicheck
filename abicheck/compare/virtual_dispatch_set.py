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

"""``PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED`` (G29 Phase 6): the possible runtime
dispatch target set of a public virtual method changed between two versions.

The producer is :mod:`abicheck.buildsource.virtual_dispatch_graph`. Every
virtual call edge ``DECL_CALLS_DECL`` (``call_kind == "virtual"``) is joined
against the ``METHOD_POSSIBLE_OVERRIDE`` candidates of its base method, which
materializes ``VIRTUAL_CALL_MAY_DISPATCH_TO`` edges whose ``base_method``
attribute names that base. This module compares, per public base method that
is virtually called on both sides, the override candidate set old vs new.

It lives in ``compare/`` because it is a pure old-vs-new comparison over two
summaries and needs only the model layer and the edge-coverage proof.

Conservative by construction:

* **RISK, never a break.** The set is an over-approximation of a runtime
  decision, so a change in it is a source-graph risk to review. It is
  ``COMPATIBLE_WITH_RISK``, never ``BREAKING`` or ``API_BREAK`` on its own.
* **Set semantics.** Candidates are compared as frozensets, so a reordered
  but otherwise identical override set is no change.
* **Missing evidence yields nothing.** If the call, override or dispatch
  family did not run to completion on either side (narrowed, degraded,
  header-only without its matching flag, or no pass flags recorded at all),
  :func:`~abicheck.compare.edge_query.source_graph_covers` refuses it and no
  finding is emitted. Nothing is claimed clean either: silence here means
  "not established", not "unchanged".
* **Public on both sides only.** A base that is private on either side is
  another detector's concern.
* **Only materialized dispatch sets are compared.** A base with no virtual
  call site on a side has no dispatch set recorded there, so it is skipped.
  A first-ever override of a base nobody calls is therefore not reported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.source_graph import EVIDENCE_TIER_L5
from ..model.source_graph_coverage import (
    OVERRIDE_GRAPH_PASS,
    VIRTUAL_DISPATCH_GRAPH_PASS,
)
from ..model.source_graph_query import is_public_dependency_node
from .edge_query import source_graph_covers

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..model.source_graph import SourceGraphSummary

_CALLS = "DECL_CALLS_DECL"
_DISPATCH = "VIRTUAL_CALL_MAY_DISPATCH_TO"

#: The producer passes whose complete run a dispatch set is trusted on. The
#: override and dispatch edges belong to the semantic-fact families, which
#: ``source_graph_covers`` does not answer for, so completeness is read from
#: the pass flags directly: a flagged pass that was neither narrowed nor
#: degraded. A header-only pass is deliberately not accepted.
_DISPATCH_PASSES: tuple[str, ...] = (OVERRIDE_GRAPH_PASS, VIRTUAL_DISPATCH_GRAPH_PASS)

#: How many example targets a description names per direction before "+N more".
_EXAMPLE_LIMIT = 2


def _dispatch_sets(graph: SourceGraphSummary) -> dict[str, frozenset[str]]:
    """``public virtual base id -> possible override candidate ids``.

    A base with a virtual call site but no candidates maps to the empty set:
    that is an observed "no possible override" fact, not a missing entry, and
    it is what lets a method that *gains* its first override register as a
    change. Bases that are not public on this side are left out.
    """
    node_by_id = {n.id: n for n in graph.nodes}
    exported = {e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"}
    virtual_bases = {
        e.dst
        for e in graph.edges
        if e.kind == _CALLS and e.resolved.get("call_kind") == "virtual"
    }
    candidates: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.kind == _DISPATCH:
            candidates.setdefault(str(e.attrs.get("base_method", "")), set()).add(e.dst)
    return {
        base: frozenset(candidates.get(base, ()))
        for base in virtual_bases
        if is_public_dependency_node(base, node_by_id, exported)
    }


def _names(targets: Sequence[str], labels: dict[str, str], prefix: str) -> str:
    if not targets:
        return ""
    shown = ", ".join(labels.get(t, t) for t in targets[:_EXAMPLE_LIMIT])
    more = len(targets) - _EXAMPLE_LIMIT
    extra = f" (+{more} more)" if more > 0 else ""
    return f" {prefix} {shown}{extra}."


def _complete(graph: SourceGraphSummary) -> bool:
    """Whether *graph*'s call edges are proven project-wide and its override
    and virtual-dispatch passes both ran to completion."""
    return source_graph_covers(graph, _CALLS) and all(
        graph.extractor_passes.get(name, False)
        and not graph.narrowed_passes.get(name, False)
        and not graph.degraded_passes.get(name, False)
        for name in _DISPATCH_PASSES
    )


def virtual_dispatch_set_findings(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> list[Change]:
    """``PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED`` findings between two L5 graphs.

    Returns ``[]`` unless both sides are complete (see :func:`_complete`).
    """
    if not (_complete(old) and _complete(new)):
        return []

    old_sets = _dispatch_sets(old)
    new_sets = _dispatch_sets(new)
    # A removed candidate exists only on the old side, and an added one only on
    # the new side: name each from whichever graph declares it.
    labels = {n.id: (n.label or n.id) for n in old.nodes}
    labels.update({n.id: (n.label or n.id) for n in new.nodes})
    boundary = f"[{EVIDENCE_TIER_L5}]"

    findings: list[Change] = []
    for base in sorted(old_sets.keys() & new_sets.keys()):
        before, after = old_sets[base], new_sets[base]
        if before == after:
            continue
        label = labels.get(base, base)
        description = (
            f"Possible runtime dispatch targets of public virtual method {label!r} "
            f"changed ({len(before)} → {len(after)} override candidates, "
            "approximate). A call through this stable public virtual may now reach "
            "a different implementation. Source-graph risk to review; not an "
            "artifact-proven ABI break."
            + _names(sorted(after - before), labels, "Added:")
            + _names(sorted(before - after), labels, "Removed:")
        )
        findings.append(
            Change(
                kind=ChangeKind.PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED,
                symbol=label,
                description=description,
                old_value=f"{len(before)} possible targets",
                new_value=f"{len(after)} possible targets",
                source_location=boundary,
            )
        )
    return findings
