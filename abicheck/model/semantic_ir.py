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

"""``SemanticIR``/``CanonicalEntity`` — the one canonical IR between the
extraction backends and the checker (ADR-063 Phase 6).

This module is Phase 6's *first* step, deliberately: the plan
(``docs/contribute/plans/one-semantic-pipeline.md``, "Phase 6") requires the
IR itself to be defined and tested before any backend parser is narrowed to
feed it, so the per-backend migrations converge on one shape rather than
each backend's own reading of "canonical" behind a shared name.

**Keyed by ``OccurrenceId``, not collapsed to one entry per ``EntityId``.**
A complete definition and an incomplete/ODR-duplicate declaration can
legitimately share one :class:`~abicheck.model.identity.EntityId` while
carrying different availability, origin, or producer facts;
a one-entry-per-identity map would overwrite or merge that evidence away
before comparison ever saw it. :meth:`SemanticIR.canonical_entities` is the
explicit, separate reduction for a consumer that genuinely wants one view
per identity — never the only shape this IR offers, and never the shape the
legacy ``AbiSnapshot.functions``/``types``/... projection is built from.

**``CanonicalEntity`` carries no identity of its own.** No ``ScopePath``, no
``EntityId``, no ``OccurrenceId`` field: identity lives exclusively in the
mapping's key, so a normalizer or deserializer bug cannot produce a mapping
whose key names one scope and whose value reports another (ADR-063's
governing invariant — one concept, one representation). A caller that needs
an entity's scope reads it off the key it was retrieved with
(``occurrence_id.entity_id.scope``); a function handing a
:class:`CanonicalEntity` to a caller without that key in scope returns the
*pair*, never a value carrying a second copy of what the key already states.

Leaf module: depends only on ``model.fact``/``model.availability``/
``model.identity``/``model.occurrence``, per ADR-063 D10.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any

from .fact import Fact
from .identity import EntityId, _packed
from .occurrence import OccurrenceId, canonical_key

__all__ = [
    "CV_QUALIFIER_ORDER",
    "CanonicalEntity",
    "SemanticIR",
    "canonical_cv_qualification",
    "renumber_conflict_keys",
    "semantic_ir_conflict_key",
]

#: The canonical order CV-qualifiers are rendered in, so two backends
#: spelling the same qualification in different orders (``const volatile``
#: vs. ``volatile const``) produce one value, not two. Ordering — rather
#: than a set — keeps the field a plain, JSON-round-trippable tuple while
#: still being canonical.
CV_QUALIFIER_ORDER = ("const", "volatile", "restrict")


def canonical_cv_qualification(spellings: Iterable[str]) -> tuple[str, ...]:
    """Canonicalize CV-qualifier *spellings* into :data:`CV_QUALIFIER_ORDER`
    order, deduplicated, ignoring surrounding whitespace and empty entries.

    Raises ``ValueError`` for a qualifier this vocabulary does not name — a
    silently-dropped unknown qualifier would be exactly the "two backends,
    two readings of canonical" outcome this IR exists to prevent.
    """
    seen: set[str] = set()
    for raw in spellings:
        spelling = raw.strip()
        if not spelling:
            continue
        if spelling not in CV_QUALIFIER_ORDER:
            raise ValueError(
                f"unknown CV-qualifier {spelling!r} "
                f"(known: {', '.join(CV_QUALIFIER_ORDER)})"
            )
        seen.add(spelling)
    return tuple(q for q in CV_QUALIFIER_ORDER if q in seen)


@dataclass(frozen=True)
class CanonicalEntity:
    """One occurrence's canonicalized, backend-independent payload.

    Every semantic field is a :class:`~abicheck.model.fact.Fact`, so a
    backend that structurally cannot produce one states
    ``Fact.unsupported()`` rather than a default a reader would mistake for
    a confirmed value (ADR-063 Phase 0). ``producer`` is *not* a fact: it
    names which backend produced this payload (``"castxml"``/``"clang"``/
    ``"dwarf"``/``"pdb"``/``"btf"``/``"ctf"``, or ``"hybrid"`` for an
    occurrence the hybrid merge backfilled across two backends), and is
    empty for a hand-constructed entity with no backend behind it.
    """

    #: The canonical spelling of the declaration's own type (a record's
    #: canonical qualified name, a function's canonical signature spelling,
    #: a typedef's canonical underlying type), after anonymous-marker,
    #: closure-identity and namespace-join canonicalization.
    canonical_spelling: Fact[str]
    #: The canonical, ordered template-argument spellings, empty for a
    #: non-template declaration (``Fact.present(())``, which is a *confirmed*
    #: absence — distinct from ``Fact.not_collected()``).
    template_arguments: Fact[tuple[str, ...]] = field(
        default_factory=lambda: Fact.not_collected()
    )
    #: CV-qualification in :data:`CV_QUALIFIER_ORDER` order — see
    #: :func:`canonical_cv_qualification`.
    cv_qualification: Fact[tuple[str, ...]] = field(
        default_factory=lambda: Fact.not_collected()
    )
    producer: str = ""

    def __post_init__(self) -> None:
        # A usable fact must actually carry its declared value. These fields
        # are `Fact[str]`/`Fact[tuple[str, ...]]`, and their own docstrings
        # name the confirmed-absence spelling for each: `Fact.present(())`
        # for a non-template declaration, an empty tuple for no
        # CV-qualification. `Fact.present(None)` is legitimate in the general
        # `Fact` vocabulary (for a field whose `T` includes `None`) but not
        # for these three, and admitting it here would let
        # `resolved_fact_count` — and through it `canonical_entities()`'s
        # reduction and the hybrid merge's backfill — treat a value the
        # entity does not carry as usable evidence (Codex review).
        for name, fact in self.fact_items():
            if fact.is_present and fact.value is None:
                raise ValueError(
                    f"{name} is {fact.status.value} but carries no value; "
                    "confirmed absence is spelled with this field's own "
                    "empty value (\"\" or ()), never None"
                )
        cv = self.cv_qualification
        # `is_present`, not `status is PRESENT`: `PARTIAL` is usable evidence
        # everywhere else in this IR (`Fact.is_present`,
        # `resolved_fact_count`, the hybrid merge's backfill), so checking
        # only `PRESENT` would accept a non-canonical ("volatile", "const")
        # from a partial fact while rejecting the identical present one --
        # two spellings of one qualification, which is what canonicalization
        # exists to prevent, and a false hybrid conflict when two backends
        # land on different ones (Codex review).
        if cv.is_present and cv.value is not None:
            canonical = canonical_cv_qualification(cv.value)
            if tuple(cv.value) != canonical:
                raise ValueError(
                    f"cv_qualification {tuple(cv.value)!r} is not canonical; "
                    f"expected {canonical!r} (see canonical_cv_qualification)"
                )

    def fact_items(self) -> tuple[tuple[str, Fact[Any]], ...]:
        """This entity's ``Fact``-typed fields as ``(name, fact)`` pairs, in
        declaration order.

        Every consumer that has to walk "each semantic fact" — the hybrid
        merge's backfill, the wire codec, a completeness check — walks this
        instead of restating the field list, so adding a field to this class
        cannot leave one of them silently ignoring it.
        """
        return tuple(
            (f.name, value)
            for f in fields(self)
            if isinstance(value := getattr(self, f.name), Fact)
        )

    def resolved_fact_count(self) -> int:
        """How many of this entity's facts carry usable evidence
        (``PRESENT``/``PARTIAL``, i.e. ``Fact.is_present``) — the ranking
        :meth:`SemanticIR.canonical_entities` reduces on."""
        return sum(1 for _, fact in self.fact_items() if fact.is_present)


@dataclass(frozen=True)
class SemanticIR:
    """Every canonicalized occurrence one extraction produced, keyed by
    :class:`~abicheck.model.occurrence.OccurrenceId`.

    ``frozen`` guards the binding, not the mapping: ``occurrences`` is
    typed as a read-only :class:`~collections.abc.Mapping` so a consumer
    cannot mutate it through this type, matching how every other model-layer
    collection in this codebase is handed out.
    """

    occurrences: Mapping[OccurrenceId, CanonicalEntity] = field(default_factory=dict)

    def occurrences_for(self, entity_id: EntityId) -> tuple[OccurrenceId, ...]:
        """Every occurrence key naming *entity_id*, in this IR's own order.

        More than one is the ordinary ODR-duplicate/incomplete-declaration
        case this IR exists to preserve, not an anomaly.
        """
        return tuple(occ for occ in self.occurrences if occ.entity_id == entity_id)

    def canonical_entities(self) -> dict[EntityId, CanonicalEntity]:
        """One entity per :class:`EntityId` — an explicit *reduction*, for a
        consumer that genuinely wants a single canonical view.

        The winner is the occurrence with the most ``PRESENT`` facts; ties
        break on :func:`~abicheck.model.occurrence.canonical_key` order, so
        the result never depends on insertion order (two IRs holding the
        same occurrences reduce identically regardless of how each was
        built).

        **Never the projection the legacy ``AbiSnapshot.functions``/
        ``types``/... fields are built from** — those keep one entry per
        occurrence, exactly as today's assembly already produces; routing
        them through this method would collapse an ODR-duplicate pair to
        one entry and lose the evidence ``occurrences`` exists to keep.
        """
        best: dict[EntityId, tuple[int, str, CanonicalEntity]] = {}
        for occ_id, entity in self.occurrences.items():
            rank = (-entity.resolved_fact_count(), canonical_key(occ_id))
            current = best.get(occ_id.entity_id)
            if current is None or rank < (current[0], current[1]):
                best[occ_id.entity_id] = (rank[0], rank[1], entity)
        return {entity_id: chosen for entity_id, (_, _, chosen) in best.items()}


def semantic_ir_conflict_key(occurrence_id: OccurrenceId, fact_name: str) -> str:
    """The ``AbiSnapshot.semantic_ir_conflicts`` key for *fact_name* on
    *occurrence_id*.

    Keyed on the *occurrence*, not the declaration, unlike
    ``fact_provenance``'s own ``func_fact_key``/``type_fact_key`` family:
    those name a declaration, which is correct for every legacy field the
    hybrid merge reconciles (none of which can have more than one matched
    pair per identity), but this IR's own matching explicitly allows two
    matched pairs to share one ``EntityId`` — two conflicts on the same fact
    name would then collide on one declaration-keyed string and the second
    would silently discard the first.
    """
    return _packed(canonical_key(occurrence_id), fact_name)


def renumber_conflict_keys(
    conflicts: dict[str, str],
    old_occurrence_ids: Iterable[OccurrenceId],
    new_semantic_ir: SemanticIR,
) -> None:
    """Re-key *conflicts* (``AbiSnapshot.semantic_ir_conflicts``) after its
    matching ``SemanticIR.occurrences`` keys were renumbered elsewhere
    (``qualified_name_segments.renumber_anonymous_closure_identities``,
    ADR-063 Phase 6 second slice, Codex review, PR #1001).

    Each conflict key is :func:`semantic_ir_conflict_key` — a
    length-prefixed packed string — that an in-place substring rewrite of
    an embedded closure/anonymous-marker (the same rewrite already correctly
    applied to ``CanonicalEntity.canonical_spelling`` and every legacy
    field) would corrupt: the outer length prefix would no longer match the
    rewritten text's real length, producing a string that equals neither
    the original key nor a freshly-recomputed one for the renumbered
    occurrence. This function instead recomputes each affected key fresh
    from the paired old/new :class:`~abicheck.model.occurrence.OccurrenceId`
    and the fact names *new_semantic_ir*'s own matching entity actually
    carries (:meth:`CanonicalEntity.fact_items` — the exhaustive set
    :func:`semantic_ir_conflict_key` is ever called with; see
    ``extract/semantic_ir_merge.py``, this dict's only writer).

    *old_occurrence_ids* must be ``new_semantic_ir.occurrences``'s own keys
    *before* they were rewritten, in the identical order (both built by
    iterating one dict, before and after its keys were replaced) — paired
    positionally. A no-op if the two sequences differ in length (a rare
    post-renumber key collision onto one entity: bail rather than guess a
    wrong correspondence, the same fail-closed posture this module's
    matching logic already takes elsewhere). Mutates *conflicts* in place;
    an occurrence pair that didn't actually change contributes nothing
    (checked by equality, not identity, so this is safe to call whether or
    not the caller already knows which pairs changed).
    """
    old_ids = list(old_occurrence_ids)
    new_ids = list(new_semantic_ir.occurrences)
    if not conflicts or len(old_ids) != len(new_ids):
        return
    rewritten: dict[str, str] = {}
    for old_occ_id, new_occ_id in zip(old_ids, new_ids):
        if old_occ_id == new_occ_id:
            continue
        entity = new_semantic_ir.occurrences.get(new_occ_id)
        if entity is None:
            continue
        for fact_name, _fact in entity.fact_items():
            old_key = semantic_ir_conflict_key(old_occ_id, fact_name)
            if old_key in conflicts:
                rewritten[semantic_ir_conflict_key(new_occ_id, fact_name)] = (
                    conflicts.pop(old_key)
                )
    conflicts.update(rewritten)
