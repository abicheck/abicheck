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

"""L5 source-graph node/edge schema (ADR-031 D2) and the ADR-046 D1/D2
evidence-preserving fact merge. Split out of ``source_graph.py`` (moved here
across two rounds — first the merge machinery, then ``GraphNode``/
``GraphEdge`` themselves) to keep that module under the AI-readiness
line-count cap; ``source_graph.py`` imports and re-exports every public name
here so existing ``from .source_graph import GraphNode``/``CONF_HIGH`` etc.
call sites are unaffected. The confidence labels and node/edge-kind
vocabulary sets live in the sibling ``graph_vocabulary.py`` (split out when
this module moved to ``abicheck/model/`` — ADR-061 Phase 5 item 2 follow-up
— to stay under the new-file 800-line production cap); re-exported from here
for backward compatibility.

Replaces the v1 first-writer-wins ``SourceGraphSummary.add_node``/``add_edge``
behavior: a node or edge accumulates one :class:`GraphFact` per producer that
ever registered it, folded into one order-independent ``resolved`` dict via
:func:`merge_graph_facts`, with genuine cross-producer disagreements recorded
as :class:`FactConflict` instead of silently dropped (D2). :func:`edge_relation_key`
adds a role-aware edge identity alongside the coarse ``(src, dst, kind)`` one,
and :func:`edge_occurrence_id` layers an opt-in per-call-site occurrence
identity on top of it (D1). See ADR-046 and
``docs/contribute/plans/g29-impact-analysis-layer.md`` Phase 2.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .graph_identity import (
    _decl_node_id as _decl_node_id,
    _is_decl_or_type_node_id as _is_decl_or_type_node_id,
    _normalize_graph_identity as _normalize_graph_identity,
    _normalize_identity_attrs as _normalize_identity_attrs,
    _normalize_if_decl_or_type as _normalize_if_decl_or_type,
    _type_node_id as _type_node_id,
)
from .graph_vocabulary import (
    _CONFIDENCE_RANK as _CONFIDENCE_RANK,
    CALLBACK_EDGE_KINDS as CALLBACK_EDGE_KINDS,
    CONF_HIGH as CONF_HIGH,
    CONF_REDUCED as CONF_REDUCED,
    CONF_UNKNOWN as CONF_UNKNOWN,
    CONSUMER_EDGE_KINDS as CONSUMER_EDGE_KINDS,
    CONSUMER_NODE_KINDS as CONSUMER_NODE_KINDS,
    LINK_PROVENANCE_EDGE_KINDS as LINK_PROVENANCE_EDGE_KINDS,
    LINK_PROVENANCE_NODE_KINDS as LINK_PROVENANCE_NODE_KINDS,
    MACRO_DEP_EDGE_KINDS as MACRO_DEP_EDGE_KINDS,
    TEMPLATE_EDGE_KINDS as TEMPLATE_EDGE_KINDS,
    TEMPLATE_NODE_KINDS as TEMPLATE_NODE_KINDS,
    USE_CASE_EDGE_KINDS as USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS as USE_CASE_NODE_KINDS,
    VIRTUAL_DISPATCH_EDGE_KINDS as VIRTUAL_DISPATCH_EDGE_KINDS,
    VIRTUAL_DISPATCH_NODE_KINDS as VIRTUAL_DISPATCH_NODE_KINDS,
)


def _precedence_key(fact: GraphFact) -> tuple[int, str, str]:
    """Deterministic total order over facts: highest confidence first, tie
    broken by producer name, and a further tie (the same producer
    contributing two facts at equal confidence with different attrs) by a
    JSON-content sort — so arrival/registration order never decides a
    winner, satisfying ``merge_graph_facts``'s order-independence property.
    """
    return (
        -_CONFIDENCE_RANK.get(fact.confidence, 0),
        fact.producer,
        json.dumps(fact.attrs, sort_keys=True, default=str),
    )


@dataclass
class GraphFact:
    """One producer's contribution to a node/edge's ``attrs`` (ADR-046 D2).

    A node/edge accumulates one ``GraphFact`` per producer that ever
    registered it, instead of the v1 first-writer-wins behavior silently
    dropping every registration after the first.
    """

    producer: str
    confidence: str = CONF_UNKNOWN
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "confidence": self.confidence,
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphFact:
        return cls(
            producer=str(d.get("producer", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            attrs=dict(d.get("attrs", {})),
        )


@dataclass
class FactConflict:
    """A genuine attrs disagreement between two facts at equal precedence
    (ADR-046 D2) — e.g. ``is_virtual: true`` vs. ``is_virtual: false`` from
    two producers of the same confidence. Advisory only (never authoritative
    on its own, ADR-028 D3): recorded so the disagreement is visible instead
    of one value silently winning with no trace of the other.
    """

    key: str
    winning_value: Any
    winning_producer: str
    losing_value: Any
    losing_producer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "winning_value": self.winning_value,
            "winning_producer": self.winning_producer,
            "losing_value": self.losing_value,
            "losing_producer": self.losing_producer,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FactConflict:
        return cls(
            key=str(d.get("key", "")),
            winning_value=d.get("winning_value"),
            winning_producer=str(d.get("winning_producer", "")),
            losing_value=d.get("losing_value"),
            losing_producer=str(d.get("losing_producer", "")),
        )


def merge_graph_facts(
    facts: list[GraphFact],
) -> tuple[dict[str, Any], list[FactConflict]]:
    """Fold ``facts`` into one ``resolved`` attrs dict (ADR-046 D2).

    Order-independent: the result depends only on each fact's confidence,
    producer name, and content, never on registration order, so the same set
    of facts always resolves identically regardless of which producer ran
    first (the property PR #607's review repeatedly needed and had to
    hand-verify per call site). Per key, the highest-confidence fact wins; a
    tie is broken by a stable producer-name sort, and a further tie (the same
    producer contributing two facts at equal confidence with different attrs
    — e.g. an initial registration and a later backfill) by a deterministic
    JSON-content sort so arrival order still never decides the winner. A
    genuine value disagreement between two facts that both contribute a key
    is recorded as a :class:`FactConflict`, not silently dropped.
    """
    ordered = sorted(facts, key=_precedence_key)
    resolved: dict[str, Any] = {}
    winners: dict[str, GraphFact] = {}
    conflicts: list[FactConflict] = []
    for fact in ordered:
        for k, v in fact.attrs.items():
            if k not in resolved:
                resolved[k] = v
                winners[k] = fact
            elif resolved[k] != v:
                conflicts.append(
                    FactConflict(
                        key=k,
                        winning_value=resolved[k],
                        winning_producer=winners[k].producer,
                        losing_value=v,
                        losing_producer=fact.producer,
                    )
                )
    return resolved, conflicts


@dataclass
class GraphNode:
    """A single ABI/API-relevant graph node (ADR-031 D2).

    ``facts``/``resolved``/``conflicts``: the ADR-046 D2 evidence-preserving
    merge. ``attrs``/``provenance``/``confidence`` stay real fields (v1
    read-compat), (re)populated from the merged facts, not frozen at
    first registration.
    """

    id: str
    kind: str  # one of source_graph.NODE_KINDS (preserved even if unknown)
    label: str = ""  # human-readable name/path (redacted upstream)
    attrs: dict[str, Any] = field(default_factory=dict)
    provenance: str = ""  # how this node was derived, e.g. "build_evidence"
    confidence: str = CONF_UNKNOWN
    facts: list[GraphFact] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)
    conflicts: list[FactConflict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "attrs": dict(self.attrs),
            "provenance": self.provenance,
            "confidence": self.confidence,
            "facts": [f.to_dict() for f in self.facts],
            "resolved": dict(self.resolved),
            "conflicts": [c.to_dict() for c in self.conflicts],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        # v1 pack has no "facts" key; ensure_facts_and_resolve synthesizes it
        # from attrs/provenance/confidence (no forced re-collection). A stored
        # "resolved"/"conflicts" is never trusted — always recomputed, so a
        # hand-edited pack self-heals instead of persisting a stale merge.
        #
        # id passes through _normalize_if_decl_or_type on load (Codex
        # review, fresh evidence) so a pack persisted before that
        # normalization existed self-heals the same way: without this, an
        # old pack's raw, checkout-path-bearing decl/type node id would
        # never match the normalized id a freshly-built graph now produces
        # for the identical declaration, and diff_source_graph's direct id
        # comparison would read it as removed+added rather than unchanged
        # (the exact false positive this whole normalization exists to
        # close, just reached from a stale-pack angle instead of a
        # fresh-vs-fresh one). Gated to decl://type:// ids only -- a no-op
        # for every other kind (source://, header://, build_option://,
        # symbol://, target://, vtable://, ...), idempotent for an id a
        # current build already normalized. ``label`` needs no explicit
        # normalization here — ensure_facts_and_resolve below (called
        # unconditionally for every loaded node) already covers it, the
        # same single choke point a freshly-added node's label goes through
        # via SourceGraphSummary.add_node.
        node = cls(
            id=_normalize_if_decl_or_type(str(d["id"])),
            kind=str(d.get("kind", "file")),
            label=str(d.get("label", "")),
            attrs=dict(d.get("attrs", {})),
            provenance=str(d.get("provenance", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            facts=[GraphFact.from_dict(f) for f in d.get("facts", [])],
        )
        ensure_facts_and_resolve(node)
        return node


@dataclass
class GraphEdge:
    """A directed edge between two nodes, with provenance + confidence (D2, D9).

    ``attrs`` carries edge-kind-specific labels — most importantly the
    ``call_kind``/``resolution`` pair for ``DECL_CALLS_DECL`` edges (ADR-031
    D4). ``facts``/``resolved``/``conflicts`` are the ADR-046 D2
    evidence-preserving merge — see :class:`GraphNode`. ``occurrences`` is
    D1's second half: the deduplicated set of per-call-site
    :func:`edge_occurrence_id` values contributed by this edge's facts (empty
    when no fact carries occurrence-level attrs — the common case today).
    """

    src: str
    dst: str
    kind: str  # one of source_graph.EDGE_KINDS (preserved even if unknown)
    provenance: str = ""
    confidence: str = CONF_UNKNOWN
    attrs: dict[str, Any] = field(default_factory=dict)
    facts: list[GraphFact] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)
    conflicts: list[FactConflict] = field(default_factory=list)
    occurrences: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str, str]:
        """Identity for diffing/de-dup: (src, dst, kind) — ADR-046 D1's
        coarsest (role-blind) projection. Still used by
        :func:`~abicheck.buildsource.source_graph.diff_source_graph`'s
        edge-set comparison (deliberately role-blind there); no longer used
        by ``SourceGraphSummary.add_edge``, which dedups on
        :meth:`relation_key` instead (a follow-up fix — see that method's
        docstring). Role-aware code should use :meth:`relation_key`.
        """
        return (self.src, self.dst, self.kind)

    def relation_key(self) -> tuple[str, str, str, str]:
        """Role-aware identity (ADR-046 D1) — see :func:`edge_relation_key`.
        Falls back to raw ``attrs`` pre-registration, when ``resolved`` is
        still empty.
        """
        return edge_relation_key(
            self.src, self.dst, self.kind, self.resolved or self.attrs
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge": self.kind,
            "src": self.src,
            "dst": self.dst,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "attrs": dict(self.attrs),
            "facts": [f.to_dict() for f in self.facts],
            "resolved": dict(self.resolved),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "occurrences": list(self.occurrences),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        # See GraphNode.from_dict: v1-pack compat + always-recompute-from-facts
        # apply identically here, including the src/dst normalize-on-load
        # migration for a pack persisted before decl/type node ids were
        # checkout-directory-normalized.
        edge = cls(
            src=_normalize_if_decl_or_type(str(d["src"])),
            dst=_normalize_if_decl_or_type(str(d["dst"])),
            kind=str(d.get("edge", d.get("kind", ""))),
            provenance=str(d.get("provenance", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            attrs=dict(d.get("attrs", {})),
            facts=[GraphFact.from_dict(f) for f in d.get("facts", [])],
        )
        ensure_facts_and_resolve(edge)
        return edge


def ensure_facts_and_resolve(entity: GraphNode | GraphEdge) -> None:
    """Ensure ``entity.facts`` is non-empty and (re)derive ``resolved``/
    ``conflicts``/``attrs``/``confidence``/``provenance`` from it.

    Synthesizes a single fact from ``attrs``/``provenance``/``confidence``
    when ``facts`` is empty — the common case for a bare ``GraphNode(...)``/
    ``GraphEdge(...)`` construction that bypasses
    ``SourceGraphSummary.add_node``/``add_edge`` (a v1-shaped call site, a
    loaded v1 pack with no ``facts`` key, or constructor-seeded test/builder
    code). Always recomputes ``resolved``/``conflicts`` from the (possibly
    just-synthesized) fact list via :func:`merge_graph_facts`, and
    ``confidence``/``provenance`` from the top-precedence fact — so a
    hand-edited or stale stored ``resolved`` value in a loaded pack never
    silently persists; it self-heals to what the facts actually support.

    Also normalizes a :class:`GraphNode`'s own ``label`` (Codex review, fresh
    evidence): this is the one choke point every decl/type producer already
    routes a fresh node through, on first registration, via
    ``SourceGraphSummary.add_node`` — the merge path for an
    already-registered node's *second* registration never touches ``label``
    at all ("kind/label keep the first registration's value" — see
    :meth:`SourceGraphSummary.add_node`'s own docstring), so normalizing
    here covers every producer's label the same way ``source_graph.
    _decl_node_id``/``_type_node_id`` already cover every producer's node
    id, rather than needing an explicit ``_normalize_graph_identity(...)``
    call duplicated at each producer's own node-construction site (a prior
    revision of this fix normalized only the two call sites that happened to
    build ``GraphNode``s directly in ``source_graph.py``/``type_graph.py``,
    silently leaving every other producer's label unnormalized). Safe
    unconditionally, same reasoning as :func:`_normalize_graph_identity`
    itself: the substitution only ever touches an embedded anonymous/lambda
    location marker, so it is a no-op for any other label.
    """
    if not entity.facts:
        entity.facts = [
            GraphFact(
                producer=entity.provenance,
                confidence=entity.confidence,
                attrs=dict(entity.attrs),
            )
        ]
    is_decl_or_type_node = isinstance(entity, GraphNode) and _is_decl_or_type_node_id(
        entity.id
    )
    if is_decl_or_type_node:
        # Normalize each fact's own identity attrs *before* merging (Codex
        # review, fresh evidence): merge_graph_facts() compares raw attrs
        # values for equality, so two facts differing only by checkout root
        # (e.g. two producers, or two coalesced pre-migration nodes, each
        # reporting name="raii_guard<(lambda at /a/lib.hpp:4:37)>" vs.
        # ".../b/lib.hpp...") would otherwise record a spurious FactConflict
        # over evidence that is actually identical once directory-taint is
        # stripped. Mutating entity.facts in place (not just the merged
        # ``resolved`` view) also keeps to_dict()'s persisted "facts" list
        # clean -- the same "never preserve the raw, checkout-tainted
        # spelling anywhere in the emitted graph" rule id/label/attrs
        # already follow, extended to the per-producer evidence trail too.
        for fact in entity.facts:
            _normalize_identity_attrs(fact.attrs)
    entity.resolved, entity.conflicts = merge_graph_facts(entity.facts)
    entity.attrs = dict(entity.resolved)
    top = min(entity.facts, key=_precedence_key)
    entity.confidence = top.confidence
    entity.provenance = top.producer
    if is_decl_or_type_node and isinstance(entity, GraphNode):
        entity.label = _normalize_graph_identity(entity.label)
    if isinstance(entity, GraphEdge):
        entity.occurrences = _compute_occurrences(entity)


def register_fact(
    entity: GraphNode | GraphEdge,
    provenance: str,
    confidence: str,
    attrs: dict[str, Any],
) -> None:
    """Merge one more producer's fact into an already-registered node/edge.

    The evidence-preserving counterpart of the v1 first-writer-wins drop: a
    duplicate ``(producer, confidence, attrs)`` registration is a no-op
    (idempotent re-registration), a genuinely new fact is appended, and
    ``resolved``/``conflicts``/``confidence``/``provenance`` are recomputed
    over the full accumulated fact set.
    """
    new_fact = GraphFact(producer=provenance, confidence=confidence, attrs=dict(attrs))
    if new_fact not in entity.facts:
        entity.facts.append(new_fact)
    ensure_facts_and_resolve(entity)


def merge_entity_facts(
    existing: GraphNode | GraphEdge, incoming: GraphNode | GraphEdge
) -> None:
    """Merge every fact from an already-registered *incoming* node/edge into
    *existing* (Codex review, fresh evidence).

    ``SourceGraphSummary.add_node``/``add_edge``'s duplicate-registration
    branch used to call :func:`register_fact` with just *incoming*'s own
    top-level ``provenance``/``confidence``/``attrs`` — correct for the
    common case where *incoming* is a bare, single-producer
    ``GraphNode(...)``/``GraphEdge(...)`` construction, but wrong for an
    *incoming* that already carries multiple facts of its own (e.g. a node
    re-added from an already evidence-merged graph): only one flattened fact
    got appended, silently discarding the individual per-producer facts (and
    any ``conflicts`` already recorded) *incoming* carried. Resolves
    *incoming* first (so a *incoming* whose evidence still lives only in
    ``facts``, not yet mirrored into ``attrs``, is not missed either — same
    fix as the ``add_edge`` resolve-before-index bug), then merges its full
    ``facts`` list into *existing*, one fact at a time (duplicates are a
    no-op, matching :func:`register_fact`'s own idempotence).
    """
    ensure_facts_and_resolve(incoming)
    for fact in incoming.facts:
        if fact not in existing.facts:
            existing.facts.append(fact)
    ensure_facts_and_resolve(existing)


def edge_relation_key(
    src: str, dst: str, kind: str, resolved: dict[str, Any]
) -> tuple[str, str, str, str]:
    """ADR-046 D1 role-aware edge identity: (src, dst, kind, role).

    Adds ``resolved.get("role", "")`` (D2's merged view, not raw ``attrs``)
    as a fourth discriminator to the coarse ``(src, dst, kind)`` key
    (``GraphEdge.key()``), so two structurally different dependencies that
    happen to share that triple — e.g. a type used as a ``"return"`` type on
    one edge and as a ``"param"`` type on another, both ``DECL_HAS_TYPE`` —
    stay distinguishable to code that needs that distinction.
    ``SourceGraphSummary.add_edge`` dedups on this role-aware key (a
    follow-up fix, Codex review on PR #620 — deduping on the coarse
    ``key()`` alone silently folded two real, role-distinct edges into one).
    ``diff_source_graph``'s edge-set comparison deliberately keeps using the
    coarser ``key()`` — role-level diff granularity is out of scope for this
    ADR's D1 slice.

    D1's second half — the full, non-deduplicated per-call-site/
    per-configuration evidence trail a ``relation_key`` can back many of —
    is :func:`edge_occurrence_id`/:class:`GraphEdge`'s ``occurrences`` field,
    kept deliberately opt-in (a no-op unless a producer supplies
    occurrence-level attrs) so it never lands on a default, always-on path
    without the pack-size cost-model check ADR-046's Costs section calls for.
    """
    return (src, dst, kind, str(resolved.get("role", "")))


#: The four ADR-046 D1 occurrence-level attrs an edge fact may carry, beyond
#: its ``relation_key``, to pin down exactly *which* call site/configuration/
#: template instantiation it came from. No current producer populates any of
#: these — :func:`edge_occurrence_id` returns ``None`` when none is present,
#: so occurrence tracking costs nothing until a producer opts in.
OCCURRENCE_ATTR_KEYS = (
    "source_location",
    "configuration_id",
    "instantiation_id",
    "callsite_id",
)


def edge_occurrence_id(
    relation_key: tuple[str, str, str, str], attrs: dict[str, Any]
) -> str | None:
    """ADR-046 D1's per-call-site occurrence identity: a stable
    ``sha256:<hex>`` over ``(relation_key, source_location, configuration_id,
    instantiation_id, callsite_id)``, read from *attrs*.

    Two facts that share a ``relation_key`` (and so collapse onto one
    :class:`GraphEdge`) but come from different call sites, ``#ifdef``
    configurations, or template instantiations get distinct occurrence ids —
    preserving the full evidence trail a ``relation_key``-deduped edge would
    otherwise discard.

    Returns ``None`` when *attrs* carries none of :data:`OCCURRENCE_ATTR_KEYS`
    — the common case today, since no producer populates them yet — so a
    fact with no occurrence-level data contributes nothing to
    :class:`GraphEdge`'s ``occurrences`` list rather than a spurious
    all-``None`` id.
    """
    if not any(k in attrs for k in OCCURRENCE_ATTR_KEYS):
        return None
    blob = json.dumps(
        {
            "relation_key": list(relation_key),
            **{k: attrs.get(k) for k in OCCURRENCE_ATTR_KEYS},
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _compute_occurrences(edge: GraphEdge) -> list[str]:
    """Recompute :attr:`GraphEdge.occurrences` from *edge*'s current facts.

    Called by :func:`ensure_facts_and_resolve` alongside ``resolved``/
    ``conflicts`` — always derived fresh from ``facts``, never trusted from a
    loaded pack, matching that function's self-healing convention.
    """
    rk = edge.relation_key()
    seen: list[str] = []
    for fact in edge.facts:
        oid = edge_occurrence_id(rk, fact.attrs)
        if oid is not None and oid not in seen:
            seen.append(oid)
    return sorted(seen)


@runtime_checkable
class SurfaceGraphLike(Protocol):
    """The read/write surface :class:`~abicheck.model.snapshot.AbiSnapshot.
    surface_graph` needs (ADR-063 Phase 3 D5) — narrow and structural
    (``Protocol``, not a base class) so ``model/snapshot.py`` can declare the
    field's type with no import of ``buildsource.source_graph.
    SourceGraphSummary`` at all, keeping the concrete L3-L5 evidence types out
    of ``model``'s own dependency-free layer while ``SourceGraphSummary``
    still satisfies this protocol structurally, unchanged.

    Covers both directions a real consumer needs, not only the write side a
    first draft of this protocol had: a graph *builder* (``compare/
    surface_graph.py``'s public-surface builder, the existing L5 builder)
    only ever calls :meth:`add_node`/:meth:`add_edge`; the *query* layer
    (``policy/public_surface.py``'s ``PublicSurfaceQuery``) must read
    :attr:`nodes`/:attr:`edges` back to traverse them, and check
    :meth:`has_node` for O(1) membership rather than a linear scan.
    ``Sequence``, not ``list`` — a read-only view is all traversal needs.
    Declared as read-only ``@property`` getters, not plain attributes: a
    plain ``Protocol`` attribute implies both read *and* write access, which
    would require the concrete class's own ``nodes``/``edges`` to be
    invariantly typed ``Sequence`` rather than the (mutable, appended-to)
    ``list`` they actually are — a real static mismatch `mypy` correctly
    catches, not a false positive to silence. A read-only getter only needs
    covariance, which ``list[GraphNode]`` already satisfies.

    A caller that needs ``SourceGraphSummary``-specific behavior
    (``resolve_entities`` and the rest of its concrete API) narrows back to
    the concrete class at its own call site via an ordinary
    ``isinstance(graph, SourceGraphSummary)`` check against the imported
    class — plain ``isinstance`` against a concrete class needs no
    ``@runtime_checkable`` support at all; this protocol carries that
    decorator only for the *other* direction, a caller that wants to confirm
    structural conformance without importing ``buildsource`` at all.
    """

    @property
    def nodes(self) -> Sequence[GraphNode]: ...

    @property
    def edges(self) -> Sequence[GraphEdge]: ...

    def has_node(self, node_id: str) -> bool: ...

    def add_node(self, node: GraphNode) -> None: ...

    def add_edge(self, edge: GraphEdge) -> None: ...

    def to_dict(self) -> dict[str, Any]: ...
