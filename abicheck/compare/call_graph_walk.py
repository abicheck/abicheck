"""Consumer-compiled call-graph reachability walk (ADR-046 D5).

The graph-only traversal behind the call-graph leak overlay and the
use-case explain: a restricted BFS from each entry over a
:class:`~abicheck.model.source_graph.SourceGraphSummary`, under a named
:class:`TraversalPolicy`. It reads only model graph types. Both the
``internal_leak`` detector and ``policy/use_case_reachability`` consume it,
so it lives in ``compare`` rather than in either caller.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..model.graph_facts import CONF_HIGH, CONF_REDUCED, CONF_UNKNOWN
from ..model.graph_vocabulary import (
    CALL_KIND_FUNCTION_POINTER,
    CALL_KIND_VIRTUAL,
    RESOLUTION_OVERAPPROX,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ..model.graph_facts import GraphEdge, GraphNode
    from ..model.source_graph import SourceGraphSummary

#: Local copy of graph_facts._CONFIDENCE_RANK (module-private there) — same
#: duplication pattern as buildsource/type_graph.py's own local rank dict.
_CONFIDENCE_RANK: dict[str, int] = {CONF_HIGH: 2, CONF_REDUCED: 1, CONF_UNKNOWN: 0}


def _is_consumer_compiled_node(node_id: str, node_by_id: dict[str, GraphNode]) -> bool:
    """Whether *node_id*'s own body is compiled into consumer code.

    Thin re-export of
    :func:`~abicheck.model.source_graph_query.is_consumer_compiled_node` (the
    shared predicate both the entry check and this walk's own
    expand-past-this-node check use) so the rest of this module keeps its
    existing call shape. See that function's docstring for the exact
    default rule — permissive only for a genuine ``header_graph.py`` node,
    conservative for everything else attr-less (notably a build-integrated
    ``call_graph.py`` fallback node, Codex review, fresh evidence).
    """
    from abicheck.model.source_graph_query import is_consumer_compiled_node

    return is_consumer_compiled_node(node_id, node_by_id)


@dataclass(frozen=True)
class TraversalPolicy:
    """Named, reusable graph-walk rules (ADR-046 D5).

    Formalizes what was previously :func:`consumer_compiled_reachability`'s
    own hard-coded edge-kind set and stop check into one object a future
    walk can construct and reuse instead of re-deriving the same rules
    inline. ``stop_conditions`` matches the ADR's own polarity: True means
    "do not expand past this node" — the node itself is still recorded as
    reachable (its own removal/change is still consumer-visible), only its
    outgoing edges are not queued for further descent.

    ``effect_transitions`` maps an edge's resolved ``call_kind`` (the only
    per-edge precision discriminator any current producer populates — ADR-031
    D4/D9) to the walk-level precision label a walk that crosses such an edge
    should downgrade to from that point on — e.g. ``{"virtual": "overapprox"}``
    downgrades a reachability proof from "exact" to "over-approximation" the
    moment it crosses a virtual call, and every node reached transitively
    past it inherits that downgrade too (see
    :func:`consumer_compiled_reachability`'s ``degraded`` return value).
    Empty by default — a policy that never sets it (e.g. any future walk with
    no precision-varying edge kinds) behaves exactly as before this field
    existed.

    Not (yet) adopted by :func:`compute_leak_paths`'s layout/type-graph
    walk — that walk traverses ``RecordType``/typedef structures, not the L5
    ``GraphNode``/``GraphEdge`` graph this policy shape describes, so it does
    not naturally fit without first changing its data model, which is out of
    scope here.
    """

    allowed_edges: frozenset[str]
    stop_conditions: Callable[[str, dict[str, GraphNode]], bool]
    minimum_confidence: str = CONF_UNKNOWN
    effect_transitions: dict[str, str] = field(
        default_factory=dict, hash=False, compare=False
    )


#: The call-graph leak walk's own rules (ADR-044 P1 item 1), reified as a
#: policy instance instead of the inline edge_kinds/_is_consumer_compiled_node
#: pair :func:`compute_call_graph_leak_paths` hard-coded before D5. A virtual
#: or function-pointer call is never statically exact (ADR-031 D4's own
#: ``resolution=overapprox`` label on that ``CallEdge``) — the walk's
#: ``effect_transitions`` mirrors that at the path level, so reachability
#: proven only through such a call is distinguishable from one proven through
#: an unbroken chain of direct calls (ADR-046 D5).
CALL_GRAPH_TRAVERSAL_POLICY = TraversalPolicy(
    allowed_edges=frozenset({"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"}),
    stop_conditions=lambda node_id, node_by_id: (
        not _is_consumer_compiled_node(node_id, node_by_id)
    ),
    effect_transitions={
        CALL_KIND_VIRTUAL: RESOLUTION_OVERAPPROX,
        CALL_KIND_FUNCTION_POINTER: RESOLUTION_OVERAPPROX,
    },
)


def _edge_effect_transition(policy: TraversalPolicy, edge: GraphEdge) -> str | None:
    """The precision label crossing *edge* downgrades a walk to, per
    ``policy.effect_transitions`` (ADR-046 D5), or ``None`` when *edge*'s
    resolved ``call_kind`` isn't a key the policy transitions on.
    """
    call_kind = str(edge.resolved.get("call_kind") or edge.attrs.get("call_kind") or "")
    return policy.effect_transitions.get(call_kind) if call_kind else None


def consumer_compiled_reachability(
    graph: SourceGraphSummary,
    policy: TraversalPolicy,
    entries: Iterable[str],
    node_by_id: dict[str, GraphNode],
) -> dict[str, tuple[frozenset[str], dict[str, GraphEdge], frozenset[str]]]:
    """BFS from each *entry*, restricted per *policy* (ADR-046 D5).

    Unlike :func:`~abicheck.buildsource.source_graph_findings._dependency_reachability`
    (which this call-graph walk used before this fix), the traversal does not
    simply expand every edge transitively from an already-validated entry
    (Codex review, fresh evidence): a public inline ``wrap()`` calling an
    ordinary out-of-line exported ``api()``, which in turn calls
    ``ns::detail::helper()``, has ``api()`` genuinely reachable from
    ``wrap()``'s own compiled body (a consumer really does link against
    ``api()``'s exported symbol) -- but ``helper()`` is not, since that call
    happens entirely inside the library's binary, in a function
    (``api()``) whose own body a consumer never compiles. The walk therefore
    stops **expanding past** (not discovering) a node whose own
    ``consumer_compiled_body`` is false: such a node is still recorded as
    reachable (its own removal/change IS consumer-visible through the entry
    that calls it), but whatever it calls is not queued for further descent.
    Also returns each entry's predecessor-edge map so the caller can
    reconstruct a proof path without a second, unrestricted graph walk that
    could show a route the restriction above would have rejected.

    The third element of each entry's tuple is *degraded*: the subset of
    ``seen`` first reached via an edge :func:`_edge_effect_transition` maps to
    a lower-precision label (ADR-046 D5's ``effect_transitions``) — sticky,
    so every node reached transitively past a degrading edge is degraded too,
    even if the rest of its own path is direct calls.
    """
    min_rank = _CONFIDENCE_RANK.get(policy.minimum_confidence, 0)
    adjacency: dict[str, list[GraphEdge]] = {}
    for e in graph.edges:
        if (
            e.kind in policy.allowed_edges
            and _CONFIDENCE_RANK.get(e.confidence, 0) >= min_rank
        ):
            adjacency.setdefault(e.src, []).append(e)
    out: dict[str, tuple[frozenset[str], dict[str, GraphEdge], frozenset[str]]] = {}
    for entry in entries:
        # 0-1 BFS (Codex review, CodeRabbit review): a plain BFS that marks a
        # node "seen" (and therefore degraded, if first reached via a
        # virtual/function-pointer edge) permanently on first discovery would
        # keep that degraded label even when a later, equal-or-longer *exact*
        # route to the same node exists elsewhere in the graph -- an
        # ordering artifact of which edge happens to be visited first, not a
        # real precision limit. Treat a degrading edge as weight 1 and an
        # exact edge as weight 0: pushing exact-edge targets to the front of
        # the deque and degraded-edge targets to the back keeps the deque in
        # non-decreasing weight order, so a node is only finalized (its
        # `came_from`/`degraded` state locked in) once its true minimum
        # degradation is known -- the standard two-weight-class shortest-path
        # algorithm, not a heuristic.
        finalized: set[str] = set()
        came_from: dict[str, GraphEdge] = {}
        best_degraded: dict[str, bool] = {entry: False}
        dq: collections.deque[str] = collections.deque([entry])
        while dq:
            node = dq.popleft()
            if node in finalized:
                continue
            finalized.add(node)
            if node != entry and policy.stop_conditions(node, node_by_id):
                continue
            node_degraded = best_degraded[node]
            for e in adjacency.get(node, []):
                edge_degraded = (
                    node_degraded or _edge_effect_transition(policy, e) is not None
                )
                prior = best_degraded.get(e.dst)
                # Relax only on first discovery, or when this route improves
                # an already-seen-but-not-yet-finalized node from degraded to
                # exact -- an already-finalized node's minimum is final by
                # the 0-1 BFS invariant above, so it is never revisited.
                if prior is not None and not (prior and not edge_degraded):
                    continue
                best_degraded[e.dst] = edge_degraded
                came_from[e.dst] = e
                if edge_degraded:
                    dq.append(e.dst)
                else:
                    dq.appendleft(e.dst)
        del best_degraded[entry]
        seen = frozenset(best_degraded)
        degraded = frozenset(n for n, d in best_degraded.items() if d)
        out[entry] = (seen, came_from, degraded)
    return out


def reconstruct_path(
    came_from: dict[str, GraphEdge], entry: str, target: str
) -> list[GraphEdge] | None:
    """Rebuild the entry-to-target edge chain from a predecessor map.

    Mirrors :func:`~abicheck.buildsource.source_graph_findings._dependency_path`'s
    return shape (``[]`` for ``entry == target``, ``None`` if unreachable) but
    replays the *same* restricted walk's predecessors instead of re-deriving a
    path from an unrestricted one, so the displayed proof path can never show
    a route :func:`consumer_compiled_reachability` would not itself take.
    """
    if entry == target:
        return []
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
