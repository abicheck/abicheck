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

"""``SemanticIRIndex`` — a read-only query facade over a :class:`~abicheck.
model.semantic_ir.SemanticIR` (ADR-063 Phase 6B, "PR 2" first slice).

This is deliberately the narrow, additive first step of the "semantic
consumer cutover" the plan (``docs/contribute/plans/one-semantic-pipeline.md``,
Phase 6B) and the external review it responds to both describe as the
missing piece before any detector can read ``SemanticIR`` instead of the
legacy ``AbiSnapshot.functions``/``variables``/``types`` projections: "one
index over ``SemanticIR``" naming ``entity(EntityId)``, ``occurrences
(EntityId)``, ``functions()``, ``records()``, and ``facts(entity,
fact_id)``. Building the index before any detector is migrated to use it
mirrors exactly how ``ResolvedExecutionContext``
(``workflows/resolved_execution_context.py``, "PR 1") landed: a fully
shaped, fully tested type with **no live caller yet** — the type is landed
and proven correct in isolation first, so the eventual detector migration
is a mechanical read-path swap rather than a place where a query bug and a
behavior change are debugged together.

**What this class does not do.** It never mutates or re-derives
``SemanticIR`` content — every lookup is a plain read over
``SemanticIR.canonical_entities()``'s existing reduction (one entry per
:class:`~abicheck.model.identity.EntityId`, computed once and cached on
this instance) or, for occurrence-level lookups, ``SemanticIR.occurrences``
itself. It carries no comparison, matching, or ambiguity-resolution logic
— that is ``compare/``'s job, per ADR-061's routing table, once a consumer
migration actually wires a detector to this index. ``references(entity)``
— the plan's sixth named query — is deliberately not implemented here: it
names a graph-shaped traversal that belongs with the public-surface
reference index (ADR-063's own D5 amendment, plan Phase 3 / the review's
"seventh разрыв"), not a plain per-entity lookup, and adding it here ahead
of that design would invent a second answer to "what does this reference"
alongside whatever ``export_surface.py``/the public-surface closure walk
settle on.

Leaf module: depends only on ``model.fact``/``model.identity``/
``model.occurrence``/``model.semantic_ir``, per ADR-061 D1's `model/`
import ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .fact import Fact
from .identity import EntityId, EntityKind
from .occurrence import OccurrenceId
from .semantic_ir import CanonicalEntity, SemanticIR

__all__ = ["SemanticIRIndex"]


@dataclass(frozen=True)
class SemanticIRIndex:
    """Read-only query facade over one :class:`SemanticIR`.

    ``frozen`` mirrors :class:`SemanticIR` itself: this type holds no
    mutable state a caller could observe changing out from under it. The
    one-entry-per-:class:`~abicheck.model.identity.EntityId` view
    (:meth:`entity`, :meth:`functions`, :meth:`variables`, :meth:`records`)
    is computed once, in ``__post_init__``, via ``ir.canonical_entities()``
    — never recomputed per call, and never mutated after construction.
    """

    ir: SemanticIR
    _canonical: dict[EntityId, CanonicalEntity] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        # `object.__setattr__` because the dataclass is frozen; this is the
        # one field this type computes rather than accepts, exactly the
        # pattern `CompatibilityEvaluationConfig`'s own derived fields use.
        object.__setattr__(self, "_canonical", self.ir.canonical_entities())

    def entity(self, entity_id: EntityId) -> CanonicalEntity | None:
        """The single canonical entity for *entity_id*, or ``None`` if this
        IR carries no occurrence naming it.

        This is the *reduced* view (:meth:`SemanticIR.canonical_entities`'s
        own "most facts present, tie-broken deterministically" winner) —
        for the full, unreduced set of occurrences an ODR-duplicate or
        incomplete declaration may carry under the same identity, use
        :meth:`occurrences_for` instead.
        """
        return self._canonical.get(entity_id)

    def occurrences_for(self, entity_id: EntityId) -> tuple[OccurrenceId, ...]:
        """Every occurrence key naming *entity_id*, in this IR's own
        insertion order — a thin passthrough to
        :meth:`SemanticIR.occurrences_for`, named identically so a caller
        moving between the two types does not have to learn a second
        vocabulary for the same operation."""
        return self.ir.occurrences_for(entity_id)

    def entities_of_kind(self, kind: EntityKind) -> dict[EntityId, CanonicalEntity]:
        """Every canonical entity whose :attr:`EntityId.kind` is *kind*.

        Filters the already-reduced :meth:`entity` view — a caller wanting
        the unreduced occurrence set for one of these kinds combines this
        with :meth:`occurrences_for` per key, rather than this class
        offering a second, occurrence-level filtered view nothing yet
        needs.
        """
        return {
            entity_id: entity
            for entity_id, entity in self._canonical.items()
            if entity_id.kind is kind
        }

    def functions(self) -> dict[EntityId, CanonicalEntity]:
        """Every canonical function entity (:attr:`EntityKind.FUNCTION`)."""
        return self.entities_of_kind(EntityKind.FUNCTION)

    def variables(self) -> dict[EntityId, CanonicalEntity]:
        """Every canonical variable entity (:attr:`EntityKind.VARIABLE`)."""
        return self.entities_of_kind(EntityKind.VARIABLE)

    def records(self) -> dict[EntityId, CanonicalEntity]:
        """Every canonical record/class/struct/union entity
        (:attr:`EntityKind.TYPE`)."""
        return self.entities_of_kind(EntityKind.TYPE)

    def fact(self, entity_id: EntityId, fact_name: str) -> Fact[Any] | None:
        """The named :class:`~abicheck.model.fact.Fact` off *entity_id*'s
        canonical entity (:meth:`~abicheck.model.semantic_ir.
        CanonicalEntity.fact_items`'s own field names — e.g.
        ``"canonical_spelling"``, ``"template_arguments"``,
        ``"cv_qualification"``), or ``None`` when either *entity_id* has no
        entity in this index or names no fact called *fact_name* (a typo'd
        name is indistinguishable from an absent entity — both mean "this
        index has nothing to say" — never a raised ``KeyError`` for what is,
        from a read-only query facade's perspective, an ordinary miss).
        """
        entity = self.entity(entity_id)
        if entity is None:
            return None
        for name, fact in entity.fact_items():
            if name == fact_name:
                return fact
        return None
