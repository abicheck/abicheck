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
call sites are unaffected.

Replaces the v1 first-writer-wins ``SourceGraphSummary.add_node``/``add_edge``
behavior: a node or edge accumulates one :class:`GraphFact` per producer that
ever registered it, folded into one order-independent ``resolved`` dict via
:func:`merge_graph_facts`, with genuine cross-producer disagreements recorded
as :class:`FactConflict` instead of silently dropped (D2). :func:`edge_relation_key`
adds a role-aware edge identity alongside the coarse ``(src, dst, kind)`` one
(D1). See ADR-046 and ``docs/development/plans/g29-impact-analysis-layer.md``
Phase 2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Confidence labels (ADR-031 D9). Mirrors the evidence-model vocabulary so the
#: coverage report and graph speak the same language. Canonical home of these
#: constants — ``source_graph.py`` re-exports them for backward compatibility.
CONF_HIGH = "high"
CONF_REDUCED = "reduced"
CONF_UNKNOWN = "unknown"

#: Confidence precedence for the merge below — higher ranks resolve a per-key
#: disagreement first. Anything not in this mapping (an unrecognized
#: confidence label from a hand-built/future pack) ranks alongside
#: ``CONF_UNKNOWN`` rather than erroring.
_CONFIDENCE_RANK: dict[str, int] = {CONF_HIGH: 2, CONF_REDUCED: 1, CONF_UNKNOWN: 0}


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
        node = cls(
            id=str(d["id"]),
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
    evidence-preserving merge — see :class:`GraphNode`.
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
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        # See GraphNode.from_dict: v1-pack compat + always-recompute-from-facts
        # apply identically here.
        edge = cls(
            src=str(d["src"]),
            dst=str(d["dst"]),
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
    """
    if not entity.facts:
        entity.facts = [
            GraphFact(
                producer=entity.provenance,
                confidence=entity.confidence,
                attrs=dict(entity.attrs),
            )
        ]
    entity.resolved, entity.conflicts = merge_graph_facts(entity.facts)
    entity.attrs = dict(entity.resolved)
    top = min(entity.facts, key=_precedence_key)
    entity.confidence = top.confidence
    entity.provenance = top.producer


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

    D1's second half — ``occurrence_id`` (the full, non-deduplicated
    per-call-site/per-configuration evidence trail a ``relation_key`` can
    back many of) — is not implemented here: it needs the pack-size
    cost-model check ADR-046's own Costs section calls for before landing on
    a default, always-on path rather than an opt-in one.
    """
    return (src, dst, kind, str(resolved.get("role", "")))
