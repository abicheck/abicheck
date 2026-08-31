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

"""The consumer side of the impact graph (G29 Phase 4 slice 1, ADR-057).

``appcompat.py`` already intersects a real ``--used-by`` application binary's
undefined-symbol requirements against a library diff, but it does so as a
*set* operation: the resulting
:data:`~abicheck.checker_policy.ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED`
finding can say "``training-service`` requires missing symbol
``_ZN6detail21train_ops_dispatcherEv``" and nothing more. This module promotes
those requirements to first-class graph facts on the *same* schema the L5
source graph uses, so the requirement can be joined back through
``SOURCE_DECL_MAPS_TO_SYMBOL`` and the call graph to answer *why* the consumer
depends on that symbol — "because its call graph reaches it from public
``train()``".

The join works because a required symbol is **not** given a node kind of its
own: :func:`build_consumer_graph` emits a ``CONSUMER_REQUIRES_SYMBOL`` edge
onto the ordinary ``binary_symbol://<symbol>`` node id the library's own graph
already uses for its exports. One shared node id is the whole mechanism; a
parallel ``consumer_required_symbol`` node kind would have produced two
disconnected graphs that merely look joined.

Everything here degrades to "no answer" rather than to a wrong one: with no
library-side L5 graph (no ``--sources``/``--build-info`` and no header-only
graph), or with a graph that carries no ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge
for the symbol in question, :func:`explain_required_symbol` returns ``None``
and the finding keeps exactly the wording it had before this module existed.
Absence of a consumer edge is never evidence of absence of a dependency — the
same coverage-honesty rule the rest of the graph follows (ADR-031 D9).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ..buildsource.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    CONSUMER_EDGE_KINDS as CONSUMER_EDGE_KINDS,
    CONSUMER_NODE_KINDS as CONSUMER_NODE_KINDS,
    GraphEdge,
    GraphNode,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..model.source_graph import SourceGraphSummary


class ConsumerRequirements(Protocol):
    """The three fields this module reads off a consumer's requirement set.

    Structurally identical to ``appcompat.AppRequirements``, which is what
    every real caller passes — but declared as a ``Protocol`` rather than
    imported, so this module never names ``appcompat``. That module already
    calls into this one, and the AI-readiness ``import-cycle-growth`` gate
    counts a ``TYPE_CHECKING``-only import as a real edge, so importing the
    concrete class back would form a new two-module cycle (CLAUDE.md "M1-3":
    a new cycle is a dependency-direction problem to fix, not an allowlist
    entry to add). It is also the honest shape: nothing here needs
    ``appcompat``'s ELF/PE parsing, only these three fields, so a caller with
    its own requirement source (a future runtime trace, a consumer build's
    own facts) can pass one without constructing an ``AppRequirements``.
    """

    @property
    def needed_libs(self) -> list[str]: ...

    @property
    def undefined_symbols(self) -> set[str]: ...

    @property
    def required_versions(self) -> dict[str, str]: ...


# CONSUMER_NODE_KINDS/CONSUMER_EDGE_KINDS are re-exported (imported above)
# from buildsource.graph_facts, the leaf that owns the whole graph vocabulary
# and that source_graph.NODE_KINDS/EDGE_KINDS union them in from — see the
# comment there for why they live in a leaf rather than beside the producer.

#: ``provenance`` tag every node/edge this module creates carries, so a
#: consumer fact is distinguishable from a build-evidence or replay one in the
#: ADR-046 D2 merge (``GraphFact.producer``).
CONSUMER_PROVENANCE = "used_by_consumer"

_SYMBOL_PREFIX = "binary_symbol://"


def consumer_node_id(name: str) -> str:
    """Node id for a consumer binary. Mirrors the ``<scheme>://<name>``
    convention every other id helper in ``source_graph.py`` uses."""
    return f"consumer://{name}"


def symbol_node_id(symbol: str) -> str:
    """The ``binary_symbol://`` id a required symbol joins onto.

    Deliberately duplicated from ``source_graph._symbol_node_id`` rather than
    imported: this module must stay importable without pulling in
    ``source_graph``'s own (much heavier) import surface, and the one-line
    format is itself the load-bearing contract — a private helper's rename
    must not silently break the join. :func:`test_symbol_node_id_matches_source_graph`
    pins the two to each other so a divergence fails a test instead of
    silently producing two disconnected graphs.
    """
    return f"{_SYMBOL_PREFIX}{symbol}"


def build_consumer_graph(
    consumer_name: str,
    reqs: ConsumerRequirements,
    *,
    symbols: frozenset[str] | set[str] | None = None,
) -> SourceGraphSummary:
    """Promote one consumer's requirement set (in practice an
    ``appcompat.AppRequirements``; see :class:`ConsumerRequirements`) to graph
    facts.

    *symbols*, when given, restricts the emitted ``CONSUMER_REQUIRES_SYMBOL``
    edges to that subset — the caller's already-library-scoped requirement set
    (``appcompat._scope_app_symbols_to_library`` narrows a consumer's raw
    undefined-symbol table to the symbols the *target* library actually
    exports). Omitted, every undefined symbol is recorded; an ELF consumer
    with many ``DT_NEEDED`` entries would then contribute requirements that
    belong to some other library, which is exactly the over-collection that
    scoping exists to avoid.

    Confidence is ``CONF_HIGH`` for symbol requirements — an undefined symbol
    in a real linked binary is a fact about that binary, not an inference —
    and ``CONF_REDUCED`` for the ``external_dependency`` node a version
    requirement points at, which is a ``DT_NEEDED`` soname string rather than
    a resolved artifact.
    """
    from ..model.source_graph import SourceGraphSummary

    graph = SourceGraphSummary()
    consumer_id = consumer_node_id(consumer_name)
    graph.add_node(
        GraphNode(
            id=consumer_id,
            kind="consumer_binary",
            label=consumer_name,
            attrs={"needed_libs": sorted(reqs.needed_libs)},
            provenance=CONSUMER_PROVENANCE,
            confidence=CONF_HIGH,
        )
    )

    required = set(reqs.undefined_symbols)
    if symbols is not None:
        required &= set(symbols)
    for sym in sorted(required):
        sym_id = symbol_node_id(sym)
        graph.add_node(
            GraphNode(
                id=sym_id,
                kind="binary_symbol",
                label=sym,
                provenance=CONSUMER_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )
        graph.add_edge(
            GraphEdge(
                src=consumer_id,
                dst=sym_id,
                kind="CONSUMER_REQUIRES_SYMBOL",
                provenance=CONSUMER_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )

    # required_versions maps an ELF version tag -> the DT_NEEDED soname that
    # defines it (appcompat.parse_app_requirements). The soname is the natural
    # target: an `external_dependency` node keyed on the bare name, matching
    # how build_source_graph keys a target's own non-project dependencies, so
    # a consumer's DT_NEEDED and a build target's dependency on the same
    # library fold onto one node instead of two spellings of it.
    for tag, lib in sorted(reqs.required_versions.items()):
        graph.add_node(
            GraphNode(
                id=lib,
                kind="external_dependency",
                label=lib,
                provenance=CONSUMER_PROVENANCE,
                confidence=CONF_REDUCED,
            )
        )
        graph.add_edge(
            GraphEdge(
                src=consumer_id,
                dst=lib,
                kind="CONSUMER_REQUIRES_VERSION",
                attrs={"version": tag, "role": tag},
                provenance=CONSUMER_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )
    return graph


def join_consumer_graph(
    library_graph: SourceGraphSummary, consumer: SourceGraphSummary
) -> SourceGraphSummary:
    """Fold *consumer*'s nodes/edges into a deep copy of *library_graph*.

    A **deep** copy, not an in-place fold and not a shallow re-registration of
    the same node objects: the library graph is read off an ``AbiSnapshot``'s
    embedded pack and is shared with every other consumer of that snapshot
    (``internal_leak``'s walks, ``source_graph_findings``' diff). Registering
    the library's own ``GraphNode`` objects into a fresh summary would keep
    those objects shared, and ``add_node``'s ADR-046 D2 merge mutates the
    *stored* object in place — so the consumer's facts would land on the
    library graph's own ``binary_symbol`` nodes, leaking one ``--used-by``
    application's requirements into every unrelated analysis of the same run.

    Within the copy, that same D2 merge is exactly what performs the join: a
    ``binary_symbol`` node the library exports and the consumer also requires
    ends up as **one** node carrying both producers' facts.

    ``coverage``/``extractor_passes``/``narrowed_passes``/``degraded_passes``
    are carried over from *library_graph* unchanged — the consumer side adds
    no source-extraction coverage of its own, and rewriting those would make
    the library's own honesty flags describe a pass that never ran.
    Deliberately not :meth:`~abicheck.model.source_graph.SourceGraphSummary.finalize`\\ d
    either: the result is a transient analysis graph (it is walked and
    rendered, never serialized into a pack or a report), so recomputing a
    ``graph_id``/``coverage`` block for it would only produce a second,
    slightly different description of the same extraction with nothing to
    read it.
    """
    joined = copy.deepcopy(library_graph)
    for node in consumer.nodes:
        joined.add_node(copy.deepcopy(node))
    for edge in consumer.edges:
        joined.add_edge(copy.deepcopy(edge))
    return joined


def consumer_required_symbol_nodes(graph: SourceGraphSummary) -> frozenset[str]:
    """Node ids some consumer in *graph* requires — the set that makes a proof
    path "consumer-proven" (ADR-046 D6 tier 1, ADR-057).

    Empty for any graph with no consumer facts folded in, which is every graph
    produced before this module existed and every run without ``--used-by``.
    Defined in ``buildsource.graph_impact`` (the selector that reads it must
    not import back into ``impact/`` to compute its own tier) and re-exported
    here so this module stays the one place a caller looks for the consumer
    vocabulary.
    """
    from ..buildsource.graph_impact import _consumer_required_nodes

    return _consumer_required_nodes(graph)


def _node_visibility(node: GraphNode | None) -> str:
    """*node*'s own declared visibility, reading ``resolved`` ahead of
    ``attrs`` — the same order ``graph_impact._node_is_public`` uses, so a
    node whose visibility arrived through the ADR-046 D2 merge rather than
    through a direct construction is classified identically by both."""
    if node is None:
        return ""
    return str((node.resolved or node.attrs).get("visibility", ""))


@dataclass(frozen=True)
class ConsumerImpactPath:
    """Why one consumer depends on one library symbol (ADR-057).

    ``entry_path`` is the preferred public-entry-to-symbol chain, already
    including the closing ``SOURCE_DECL_MAPS_TO_SYMBOL`` hop, so it is a
    contiguous edge chain ``buildsource.graph_impact.structured_proof_path``
    can render directly. It is ``[]`` when the requirement is *direct* — the
    symbol's own declaration is itself a public entry, with no intervening
    call — which is a real and common answer, not a missing one; that case is
    distinguished by ``public_entries`` being non-empty while ``entry_path``
    is empty.

    ``public_entries`` are the entries' labels, the preferred path's entry
    first. ``alternative_entry_paths`` is the *uncapped* runner-up list
    (ADR-046 D6's shape): capping and counting the discards is
    ``graph_impact.attach_impact_metadata``'s job, so the cap lives in exactly
    one place rather than being applied here and then re-applied there.
    """

    consumer: str
    symbol: str
    declarations: tuple[str, ...] = ()
    public_entries: tuple[str, ...] = ()
    entry_path: list[GraphEdge] = field(default_factory=list)
    alternative_entry_paths: list[list[GraphEdge]] = field(default_factory=list)

    def is_direct(self) -> bool:
        """Whether the required symbol's own declaration is a public entry
        (no intervening call hop)."""
        return not self.entry_path and bool(self.public_entries)


def explain_required_symbols(
    graph: SourceGraphSummary,
    symbols: Iterable[str],
    *,
    consumer: str = "",
) -> dict[str, ConsumerImpactPath]:
    """Answer *why* a consumer requires each of *symbols*, from the joined
    *graph* — the batch form, and the one real implementation.

    Batch rather than per-symbol because the expensive half (the restricted
    BFS from every consumer-compiled public entry) does not depend on which
    symbol is being explained: running it once per missing symbol would
    re-walk the whole call graph for each, which for a library shedding many
    symbols at once is exactly the case where the walk is least affordable.

    A symbol is simply **absent** from the returned mapping — never present
    with a partial or speculative answer — when the graph cannot support the
    question for it: no ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge (its declaration
    was never captured, so there is nothing to walk back from), or no
    consumer-compiled public entry reaching it. A caller must leave such a
    finding exactly as it was.

    The walk is deliberately the *same* restricted traversal
    ``internal_leak.compute_call_graph_leak_paths`` uses
    (``CALL_GRAPH_TRAVERSAL_POLICY``), not a fresh unrestricted BFS: an
    ordinary out-of-line exported function's body is compiled into the
    library, never into a consumer, so expanding past it would attribute a
    purely internal implementation-detail call to a consumer that cannot see
    it. Reusing the one policy also means a proof path shown here can never
    contradict one shown by the internal-leak findings for the same graph.
    """
    # Function-local: internal_leak imports impact.engine, so a module-level
    # import here would put abicheck.impact and abicheck.internal_leak in one
    # import cycle (root CLAUDE.md's "What NOT to do" — prefer a
    # function-local import over extending IMPORT_CYCLE_ALLOWLIST).
    from ..buildsource.graph_impact import select_preferred_graph_path
    from ..buildsource.source_graph_query import (
        PUBLIC_VISIBILITIES,
        is_consumer_compiled_public_entry,
    )
    from ..internal_leak import (
        CALL_GRAPH_TRAVERSAL_POLICY,
        _consumer_compiled_reachability,
        _reconstruct_path,
    )

    wanted = {symbol_node_id(s): s for s in symbols}
    if not wanted:
        return {}
    mapping_by_symbol: dict[str, dict[str, GraphEdge]] = {}
    exported_decls: set[str] = set()
    for e in graph.edges:
        if e.kind != "SOURCE_DECL_MAPS_TO_SYMBOL":
            continue
        exported_decls.add(e.src)
        name = wanted.get(e.dst)
        if name is not None:
            mapping_by_symbol.setdefault(name, {})[e.src] = e
    if not mapping_by_symbol:
        return {}

    node_by_id = {n.id: n for n in graph.nodes}
    # May legitimately be empty — a library whose every exported declaration
    # is an ordinary out-of-line function has no consumer-compiled public
    # entry to walk *from*. That blocks only the walk, not the
    # direct-requirement answer below, which needs nothing but the
    # declaration's own visibility (Codex review): returning early here lost
    # the proof path for the most ordinary case of all, a removed public
    # function the consumer named directly.
    entries = [
        n.id
        for n in graph.nodes
        if is_consumer_compiled_public_entry(n.id, node_by_id, exported_decls)
    ]
    labels = {n.id: (n.label or n.id) for n in graph.nodes}

    # Computed lazily: a run whose every missing symbol turns out to be a
    # direct public entry never needs the walk at all. Tracked by an explicit
    # flag rather than by testing the result's contents (CodeRabbit review) —
    # "have we walked" is what the batch design promises, and reading it off
    # the mapping would silently couple that promise to another module's
    # invariant that the mapping is never empty.
    reachability: dict[
        str, tuple[frozenset[str], dict[str, GraphEdge], frozenset[str]]
    ] = {}
    walked = False
    out: dict[str, ConsumerImpactPath] = {}
    for symbol, mapping_by_decl in mapping_by_symbol.items():
        decl_ids = set(mapping_by_decl)
        # A declaration a public header *declares* is a direct requirement:
        # the consumer names the symbol because the public API is that symbol.
        # No call-graph walk is needed (or meaningful) for it, and reporting
        # the trivial zero-hop path would read as "no evidence found" rather
        # than as the strongest possible answer.
        #
        # Deliberately keyed on the node's own ``visibility``, NOT on
        # :func:`is_consumer_compiled_public_entry`, even though that is the
        # predicate seeding the walk below: that one treats *any* exported
        # declaration as public, which is true of every symbol reaching this
        # loop by construction (a consumer can only require a symbol the
        # library exported). Using it here would classify every requirement as
        # direct and the walk would never run — the exact
        # internal-exported-dispatcher case this join exists for.
        direct_entries = sorted(
            d for d in decl_ids if _node_visibility(node_by_id.get(d)) in PUBLIC_VISIBILITIES
        )
        if direct_entries:
            out[symbol] = ConsumerImpactPath(
                consumer=consumer,
                symbol=symbol,
                declarations=tuple(labels.get(d, d) for d in direct_entries),
                public_entries=tuple(labels.get(d, d) for d in direct_entries),
            )
            continue

        if not entries:
            # Nothing to walk from; this symbol is simply unexplainable.
            continue
        if not walked:
            reachability = _consumer_compiled_reachability(
                graph, CALL_GRAPH_TRAVERSAL_POLICY, entries, node_by_id
            )
            walked = True
        candidates: list[tuple[str, list[GraphEdge]]] = []
        for entry in entries:
            # The target's own declaration is itself an "entry" by
            # is_consumer_compiled_public_entry's exported-decl rule (see the
            # direct-requirement comment above); a zero-hop self-path is not
            # an explanation of anything.
            if entry in decl_ids:
                continue
            seen, came_from, _degraded = reachability.get(
                entry, (frozenset(), {}, frozenset())
            )
            for decl in sorted(decl_ids & seen):
                # Neither of _reconstruct_path's two empty answers can occur
                # here — it returns [] only for entry == decl (excluded
                # above) and None only for an unreachable target (excluded by
                # `decl in seen`) — so the result is asserted rather than
                # skipped over: a silent `continue` would turn a future
                # change to either invariant into a missing explanation
                # instead of a failure.
                path = _reconstruct_path(came_from, entry, decl)
                assert path, f"{entry} reached {decl} but has no path to it"
                candidates.append((entry, [*path, mapping_by_decl[decl]]))
        if not candidates:
            continue

        paths = [p for _entry, p in candidates]
        preferred = select_preferred_graph_path(graph, paths)
        # Entry order puts the preferred path's own entry first, then the
        # rest label-sorted, so a report never depends on graph node
        # iteration order.
        preferred_entry = next(e for e, p in candidates if p is preferred)
        preferred_label = labels.get(preferred_entry, preferred_entry)
        other_entries = sorted(
            {labels.get(e, e) for e, _p in candidates} - {preferred_label}
        )
        out[symbol] = ConsumerImpactPath(
            consumer=consumer,
            symbol=symbol,
            declarations=tuple(sorted(labels.get(d, d) for d in decl_ids)),
            public_entries=(preferred_label, *other_entries),
            entry_path=preferred,
            alternative_entry_paths=[p for p in paths if p is not preferred],
        )
    return out


def explain_required_symbol(
    graph: SourceGraphSummary, symbol: str, *, consumer: str = ""
) -> ConsumerImpactPath | None:
    """Single-symbol convenience over :func:`explain_required_symbols`.

    Prefer the batch form when explaining more than one symbol from the same
    graph — see its docstring for why.
    """
    return explain_required_symbols(graph, [symbol], consumer=consumer).get(symbol)
