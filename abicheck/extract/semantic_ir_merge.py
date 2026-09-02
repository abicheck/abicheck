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

"""Reconcile two backends' :class:`~abicheck.model.semantic_ir.SemanticIR`
into one (ADR-063 Phase 6, the ``--ast-frontend hybrid`` path).

``dumper_hybrid.merge_snapshots()`` reconciles two *already-assembled*
snapshots: castxml is the base, clang backfills only the facts castxml did
not resolve, and ``fact_provenance`` records which backend won per legacy
fact. Once each sub-snapshot also carries its own ``semantic_ir``, that
field needs the identical treatment — a merged snapshot whose legacy
``functions``/``types`` include clang-only entities while its ``semantic_ir``
still holds only castxml's would be two representations of one freshly
built snapshot disagreeing with each other, which is the one outcome
ADR-063's governing invariant forbids.

The matching rule (plan, "Phase 6") is deliberately fail-closed at every
cardinality it cannot resolve uniquely:

1. **Match on the bare ``EntityId``**, not the full ``OccurrenceId``. Both
   backends parse the same headers, so the ordinary case is two
   structurally identical ``EntityId``s. A disambiguator is a *per-backend*
   TU-context signal (clang derives a USR; castxml has no USR concept at
   all), so an empty disambiguator on either side is "no additional signal
   from that backend", never a disagreement.
2. **Cardinality is checked before any pairwise comparison.** One
   ``EntityId`` may legitimately name several occurrences on one side (the
   ODR-duplicate/incomplete-declaration case ``SemanticIR.occurrences``
   exists to preserve), so the group is matched as a whole:

   * every non-empty disambiguator value present on **both** sides must
     name exactly one occurrence per side, and those pair 1:1;
   * the leftovers (which need not be empty-disambiguator occurrences — a
     one-sided non-empty disambiguator is still "no signal from the other
     backend") pair only when exactly one remains per side, and only unless
     *both* are non-empty and unequal — the one real, two-sided
     disagreement this rule refuses;
   * a leftover with no counterpart at all (one side exhausted) is unioned
     verbatim, which is not an ambiguity.

   Any group this leaves without a unique complete matching — a non-empty
   disambiguator value claimed twice on one side, more than one leftover
   per side, or a genuinely disagreeing leftover pair — is left **entirely
   unmerged**: every occurrence from both sides is unioned in verbatim,
   rather than guessing at a pairing.
3. **The base wins every matched pair.** Clang backfills only facts
   castxml carries as non-``PRESENT``; a fact both resolved, disagreeing,
   keeps castxml's value and records the discarded one in
   ``AbiSnapshot.semantic_ir_conflicts`` (occurrence-keyed — see
   :func:`~abicheck.model.semantic_ir.semantic_ir_conflict_key` for why
   ``fact_provenance``'s declaration-only key is the wrong shape here).
4. **An overlay-only ``EntityId`` is unioned verbatim**, exactly mirroring
   how a genuinely clang-only function/type is appended rather than dropped
   by the legacy-field merge.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any

from ..model.fact import Fact
from ..model.identity import EntityId
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR, semantic_ir_conflict_key

__all__ = ["MERGED_PRODUCER", "merge_semantic_ir"]

#: ``CanonicalEntity.producer`` for a matched pair that actually took at
#: least one fact from the overlay. A pair the overlay contributed nothing
#: to keeps the base entity untouched, producer included — claiming
#: ``"hybrid"`` there would overstate what the second backend supplied.
MERGED_PRODUCER = "hybrid"


def _by_entity_id(
    ir: SemanticIR,
) -> dict[EntityId, list[OccurrenceId]]:
    grouped: dict[EntityId, list[OccurrenceId]] = defaultdict(list)
    for occ_id in ir.occurrences:
        grouped[occ_id.entity_id].append(occ_id)
    return grouped


def _pair_group(
    base_ids: list[OccurrenceId], overlay_ids: list[OccurrenceId]
) -> list[tuple[OccurrenceId, OccurrenceId | None]] | None:
    """The unique complete matching between one ``EntityId``'s occurrences on
    each side, or ``None`` when no unique matching exists.

    Returns ``(base_occurrence, overlay_occurrence_or_None)`` pairs covering
    every base occurrence; overlay occurrences left unmatched are the
    caller's to union in verbatim (this function does not decide that, since
    it reports only how the base side was matched).
    """
    base_by_tag: dict[str, list[OccurrenceId]] = defaultdict(list)
    overlay_by_tag: dict[str, list[OccurrenceId]] = defaultdict(list)
    for occ in base_ids:
        if occ.disambiguator:
            base_by_tag[occ.disambiguator].append(occ)
    for occ in overlay_ids:
        if occ.disambiguator:
            overlay_by_tag[occ.disambiguator].append(occ)

    pairs: list[tuple[OccurrenceId, OccurrenceId | None]] = []
    matched_base: set[OccurrenceId] = set()
    matched_overlay: set[OccurrenceId] = set()
    for tag in sorted(base_by_tag.keys() & overlay_by_tag.keys()):
        if len(base_by_tag[tag]) != 1 or len(overlay_by_tag[tag]) != 1:
            # One side spells the same TU-context signal on two
            # occurrences: a genuine ambiguity this rule cannot resolve.
            return None
        base_occ, overlay_occ = base_by_tag[tag][0], overlay_by_tag[tag][0]
        pairs.append((base_occ, overlay_occ))
        matched_base.add(base_occ)
        matched_overlay.add(overlay_occ)

    base_left = [occ for occ in base_ids if occ not in matched_base]
    overlay_left = [occ for occ in overlay_ids if occ not in matched_overlay]
    if len(base_left) > 1 or len(overlay_left) > 1:
        return None
    if base_left and overlay_left:
        left_base, left_overlay = base_left[0], overlay_left[0]
        if (
            left_base.disambiguator
            and left_overlay.disambiguator
            and left_base.disambiguator != left_overlay.disambiguator
        ):
            # Both backends derived a TU-context signal and they disagree —
            # the one case this rule genuinely refuses.
            return None
        pairs.append((left_base, left_overlay))
    elif base_left:
        pairs.append((base_left[0], None))
    return pairs


def _merge_entity(
    base: CanonicalEntity,
    overlay: CanonicalEntity,
    base_occ: OccurrenceId,
    conflicts: dict[str, str],
) -> CanonicalEntity:
    """*base* with every non-``PRESENT`` fact backfilled from *overlay*,
    recording each two-sided disagreement into *conflicts*."""
    updates: dict[str, Any] = {}
    overlay_facts = dict(overlay.fact_items())
    for name, base_fact in base.fact_items():
        overlay_fact: Fact[Any] | None = overlay_facts.get(name)
        if overlay_fact is None or not overlay_fact.is_present:
            continue
        if not base_fact.is_present:
            updates[name] = overlay_fact
        elif base_fact.value != overlay_fact.value:
            conflicts[semantic_ir_conflict_key(base_occ, name)] = repr(
                overlay_fact.value
            )
    if not updates:
        return base
    return replace(base, producer=MERGED_PRODUCER, **updates)


def merge_semantic_ir(
    base: SemanticIR | None, overlay: SemanticIR | None
) -> tuple[SemanticIR | None, dict[str, str]]:
    """Reconcile *overlay* onto *base*, returning the merged IR and the
    occurrence-keyed conflict records it produced.

    ``None`` on either side means that backend produced no IR at all: the
    other side is returned unchanged (and ``None``/``{}`` when neither did),
    never a half-merged IR claiming evidence nobody supplied.
    """
    if base is None or not base.occurrences:
        return (base if overlay is None else overlay), {}
    if overlay is None or not overlay.occurrences:
        return base, {}

    conflicts: dict[str, str] = {}
    merged: dict[OccurrenceId, CanonicalEntity] = {}
    overlay_groups = _by_entity_id(overlay)
    consumed_overlay: set[OccurrenceId] = set()

    for entity_id, base_ids in _by_entity_id(base).items():
        overlay_ids = overlay_groups.get(entity_id, [])
        if not overlay_ids:
            for occ in base_ids:
                merged[occ] = base.occurrences[occ]
            continue
        pairs = _pair_group(base_ids, overlay_ids)
        if pairs is None:
            # No unique matching: union both sides, base first. An occurrence
            # both sides key *identically* is not a guessed pairing at all --
            # one `OccurrenceId` names one occurrence, by that type's own
            # definition -- so it is merged here under the ordinary
            # base-plus-backfill rule rather than dropped. "Union verbatim"
            # has no meaning for a key collision: `setdefault` would silently
            # discard the overlay's entity, losing exactly the facts (and
            # conflict records) this function exists to preserve, which is a
            # strictly worse outcome than either merging or keeping both --
            # and keeping both is not representable (Codex review). What
            # stays fail-closed is what the ambiguity is actually about:
            # a pairing is refused only when both sides supply a
            # disambiguator and they disagree, never merely because two
            # keys differ (castxml supplies none, so the ordinary match
            # pairs an empty key against a non-empty one).
            for occ in base_ids:
                merged[occ] = base.occurrences[occ]
            for occ in overlay_ids:
                consumed_overlay.add(occ)
                if occ in merged:
                    merged[occ] = _merge_entity(
                        merged[occ], overlay.occurrences[occ], occ, conflicts
                    )
                else:
                    merged[occ] = overlay.occurrences[occ]
            continue
        for base_occ, overlay_occ in pairs:
            base_entity = base.occurrences[base_occ]
            if overlay_occ is None:
                merged[base_occ] = base_entity
                continue
            consumed_overlay.add(overlay_occ)
            merged[base_occ] = _merge_entity(
                base_entity, overlay.occurrences[overlay_occ], base_occ, conflicts
            )

    for occ, entity in overlay.occurrences.items():
        if occ not in consumed_overlay:
            merged.setdefault(occ, entity)
    return SemanticIR(occurrences=merged), conflicts
