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

"""ADR-046 D2: evidence-preserving node/edge fact merge for the L5 source
graph (ADR-031). Split out of ``source_graph.py`` to keep that module under
the AI-readiness line-count cap — a true leaf module with **no** import
(runtime or ``TYPE_CHECKING``) back on ``source_graph.py``: ``_FactHolder``
below is a structural :class:`~typing.Protocol` describing exactly the
attributes ``GraphNode``/``GraphEdge`` carry, so this module never needs to
name those classes and cannot form an import cycle with them (CLAUDE.md
"M1-3" — a new cross-module cycle needs an ADR, not an allowlist entry;
a dependency-free leaf avoids the question entirely). ``source_graph.py``
imports and re-exports the public names here so existing ``from
.source_graph import CONF_HIGH`` etc. call sites are unaffected.

Replaces the v1 first-writer-wins ``SourceGraphSummary.add_node``/``add_edge``
behavior: a node or edge accumulates one :class:`GraphFact` per producer that
ever registered it, folded into one order-independent ``resolved`` dict via
:func:`merge_graph_facts`, with genuine cross-producer disagreements recorded
as :class:`FactConflict` instead of silently dropped. See ADR-046 D2 and
``docs/development/plans/g29-impact-analysis-layer.md`` Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class _FactHolder(Protocol):
    """Structural shape of ``GraphNode``/``GraphEdge`` this module needs —
    see the module docstring for why this is a ``Protocol`` and not an import.
    """

    facts: list[GraphFact]
    resolved: dict[str, Any]
    conflicts: list[FactConflict]
    attrs: dict[str, Any]
    confidence: str
    provenance: str


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

    Order-independent: the result depends only on each fact's confidence and
    producer name, never on registration order, so the same set of facts
    always resolves identically regardless of which producer ran first (the
    property PR #607's review repeatedly needed and had to hand-verify per
    call site). Per key, the highest-confidence fact wins; a tie is broken by
    a stable producer-name sort. A genuine value disagreement between two
    facts that both contribute a key is recorded as a :class:`FactConflict`,
    not silently dropped.
    """
    ordered = sorted(
        facts, key=lambda f: (-_CONFIDENCE_RANK.get(f.confidence, 0), f.producer)
    )
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


def ensure_facts_and_resolve(entity: _FactHolder) -> None:
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
    top = sorted(
        entity.facts,
        key=lambda f: (-_CONFIDENCE_RANK.get(f.confidence, 0), f.producer),
    )[0]
    entity.confidence = top.confidence
    entity.provenance = top.producer


def register_fact(
    entity: _FactHolder,
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
