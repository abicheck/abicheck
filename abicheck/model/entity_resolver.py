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

"""``EntityResolver`` — USR-based canonical entity identity for the L5 source
graph (ADR-046 D4, scoped implementation; see ADR-046's "D4 implementation"
section for the full design record).

Deliberately does **not** change ``GraphNode.id`` generation or the v1
node-id scheme (``_decl_node_id``/``_type_node_id`` et al. in
``source_graph.py``) — that is the "materially larger" rewrite ADR-046's own
D4 deferral note flagged as needing every ``GraphNode``-constructing producer
updated in lockstep plus a genuine v1-pack/v2-graph compatibility matrix.
Instead, ``EntityResolver`` computes a *second*, richer canonical identity
alongside the existing v1 id and records the mapping as an alias
(``aliases[v1_id] = canonical_id``) — "v1 IDs kept as aliases so old packs
still load/match" (ADR-046's own G29.3 summary). A v1 pack (no
``entity_resolver`` data) still loads and compares correctly: nothing reads
through ``EntityResolver`` unless a caller explicitly asks for it via
:meth:`SourceGraphSummary.resolve_entities`, so this is strictly additive
capability, not a forced re-collection or a behavior change to any existing
lookup.

Reuses :mod:`abicheck.model.entity_identity`'s USR/mangled-name/
qualified-name preference chain (ADR-048) as the canonical-identity source —
exactly the "natural first alias ``EntityResolver.aliases`` would fold in"
relationship ADR-048's own "Relationship to ADR-046" section already
predicted, rather than a second, independent identity computation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import entity_identity
from .graph_facts import _is_decl_or_type_node_id, _normalize_graph_identity

if TYPE_CHECKING:
    from .graph_facts import GraphNode


@dataclass(frozen=True)
class EntityConflict:
    """Two or more v1 node ids that resolved to the same canonical identity
    (ADR-046's "conflicts gives cross-producer disagreement a visible home
    instead of silent first-writer-wins" — the same design goal D2's
    ``FactConflict`` serves for attrs/confidence, applied to identity here).
    """

    canonical_id: str
    node_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"canonical_id": self.canonical_id, "node_ids": list(self.node_ids)}

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> EntityConflict:
        raw_node_ids = d.get("node_ids") or ()
        node_ids = raw_node_ids if isinstance(raw_node_ids, (list, tuple)) else ()
        return cls(
            canonical_id=str(d.get("canonical_id", "")),
            node_ids=tuple(str(x) for x in node_ids),
        )


@dataclass
class EntityResolver:
    """Resolves each v1 ``GraphNode.id`` to a canonical, USR-preferring
    identity (:func:`entity_identity.resolve_identity_for_node`), recording
    the result as an alias rather than replacing ``GraphNode.id`` itself.

    ``aliases`` maps v1 node id -> canonical id (many-to-one is expected: two
    v1 ids that only differ by which identity signal their producer happened
    to see can share one canonical identity once USRs/mangled names are
    available). ``conflicts`` records the reverse case — two *different*
    canonical identities both claiming the same node id never happens by
    construction (one node, one id), but two *different* v1 ids resolving to
    the *same* canonical identity is exactly the identity-fragmentation
    pattern this ADR exists to surface, so the first-seen v1 id becomes that
    canonical id's representative (:meth:`v1_id_for`) and every subsequent
    collision is recorded in ``conflicts`` instead of silently overwriting.
    """

    aliases: dict[str, str] = field(default_factory=dict)
    conflicts: list[EntityConflict] = field(default_factory=list)
    _canonical_to_v1: dict[str, str] = field(default_factory=dict, repr=False)

    def resolve(self, node: GraphNode) -> str:
        """Compute (and record) *node*'s canonical identity; return it.

        Idempotent — re-resolving an already-seen node id returns the
        previously recorded canonical id without recomputing or re-checking
        for a conflict a second time.
        """
        if node.id in self.aliases:
            return self.aliases[node.id]
        canonical = entity_identity.resolve_identity_for_node(node).primary_id
        self.aliases[node.id] = canonical
        existing_v1 = self._canonical_to_v1.get(canonical)
        if existing_v1 is None:
            self._canonical_to_v1[canonical] = node.id
        elif existing_v1 != node.id:
            self.conflicts.append(EntityConflict(canonical, (existing_v1, node.id)))
        return canonical

    def remap_node_ids(self, remap: Callable[[str], str]) -> None:
        """Rewrite every identity string this resolver references — v1 node
        ids *and* canonical ids alike — through *remap* (Codex review, fresh
        evidence, two rounds).

        ``SourceGraphSummary.from_dict()`` normalizes a loaded
        ``GraphNode.id`` (see ``graph_facts._normalize_graph_identity``, the
        checkout-directory-taint fix) *after* this resolver was already
        built from the persisted JSON — so without this remap,
        ``aliases``/``_canonical_to_v1``/``conflicts`` would still be keyed
        by the OLD, pre-migration id, and ``canonical_id_for(node.id)``
        would silently return ``None`` for a node whose canonical identity
        was already resolved and persisted, forcing a spurious re-resolve
        (:meth:`resolve`'s idempotence never triggers, and a fresh
        ``resolve()`` on a possibly-weaker signal set can add a second,
        redundant alias instead of returning the persisted one).

        A *canonical* id itself can carry the identical taint when no
        USR/mangled name was available at resolve time —
        ``entity_identity.normalized_signature``'s ``"sig:<qualified_name>..."``
        fallback embeds the qualified name verbatim, which is exactly
        ``node.label``'s own raw, checkout-path-bearing spelling before this
        fix existed. A first revision of this method only remapped v1-id
        *keys*, leaving that persisted canonical *value* untouched -- so
        ``canonical_id_for()`` still returned a checkout-dependent id that
        would never match a freshly-resolved graph's canonical id for the
        identical declaration. ``remap`` is applied to every string in this
        structure that could be either kind (it is a no-op for a
        ``usr:``/``mangled:``/``synthetic:sha256:`` id, which never embeds a
        qualified name literally).

        Two distinct old entries collapsing onto the same new key after
        *remap* (the directory-taint fix's own intended effect) is not
        specially reconciled here: a dict comprehension keeps whichever
        entry is last, which is harmless when both named the identical
        declaration (they now agree on both id and canonical value), but is
        not a fully general collision merge. Idempotent for an id *remap*
        does not change.

        Every string is gated by :func:`graph_facts._is_decl_or_type_node_id`
        first (Codex review, fresh evidence, sixth round): ``resolve_entities()``
        resolves *every* node in a graph, not just decl/type ones, so
        ``aliases``/``_canonical_to_v1``/``conflicts`` can legitimately hold
        a v1 id for a ``source://``/``header://``/... node too -- applying
        *remap* to that id (or the canonical id it resolved to) regardless
        of kind risked rewriting marker-shaped text that has nothing to do
        with an anonymous/lambda declaration. A pairing (an alias's v1-id
        key and canonical-id value, or a conflict's ``node_ids``) is only
        remapped when the v1-id side of it is itself a decl/type id.
        """

        def _gated(node_id: str) -> str:
            return remap(node_id) if _is_decl_or_type_node_id(node_id) else node_id

        self.aliases = {
            _gated(k): (remap(v) if _is_decl_or_type_node_id(k) else v)
            for k, v in self.aliases.items()
        }
        self._canonical_to_v1 = {
            (remap(c) if _is_decl_or_type_node_id(v) else c): _gated(v)
            for c, v in self._canonical_to_v1.items()
        }
        self.conflicts = [
            EntityConflict(
                remap(c.canonical_id)
                if all(_is_decl_or_type_node_id(nid) for nid in c.node_ids)
                else c.canonical_id,
                tuple(_gated(nid) for nid in c.node_ids),
            )
            for c in self.conflicts
        ]

    def canonical_id_for(self, v1_id: str) -> str | None:
        """The canonical identity *v1_id* resolved to, or ``None`` if
        :meth:`resolve` was never called for it."""
        return self.aliases.get(v1_id)

    def v1_id_for(self, canonical_id: str) -> str | None:
        """The representative (first-seen) v1 node id for *canonical_id*, or
        ``None`` if nothing has resolved to it yet. When more than one v1 id
        resolves to the same canonical identity, the others are recorded in
        ``conflicts`` rather than returned here."""
        return self._canonical_to_v1.get(canonical_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "aliases": dict(self.aliases),
            "conflicts": [c.to_dict() for c in self.conflicts],
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> EntityResolver:
        raw_aliases = d.get("aliases") or {}
        aliases = {
            str(k): str(v)
            for k, v in (raw_aliases.items() if isinstance(raw_aliases, dict) else ())
        }
        canonical_to_v1: dict[str, str] = {}
        for v1_id, canonical_id in aliases.items():
            canonical_to_v1.setdefault(canonical_id, v1_id)
        raw_conflicts = d.get("conflicts") or ()
        conflicts = [
            EntityConflict.from_dict(dict(c))
            for c in (raw_conflicts if isinstance(raw_conflicts, (list, tuple)) else ())
            if isinstance(c, dict)
        ]
        obj = cls(
            aliases=aliases,
            conflicts=conflicts,
            _canonical_to_v1=canonical_to_v1,
        )
        # Self-heal against source_graph.py's own GraphNode/GraphEdge id
        # migration (Codex review) -- see remap_node_ids's own docstring.
        obj.remap_node_ids(_normalize_graph_identity)
        return obj
