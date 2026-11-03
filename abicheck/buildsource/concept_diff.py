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

"""L4 concept-constraint comparison (split out of ``source_diff``)."""

from __future__ import annotations

from collections.abc import Callable

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from .source_abi import SourceAbiSurface, SourceEntity
from .source_extractors.concept_constraints import conjunctive_atoms


def _concept_constraint(entity: SourceEntity) -> str:
    """Comparable normalized constraint fingerprint/value for a concept entity."""
    return entity.value or entity.body_hash or entity.signature_hash or entity.type_hash


def _concepts_by_name(entities: list[SourceEntity]) -> dict[str, SourceEntity]:
    return {
        e.qualified_name: e
        for e in entities
        if e.kind == "concept" and e.qualified_name
    }


def diff_concepts(
    old: SourceAbiSurface,
    new: SourceAbiSurface,
    loc: Callable[[SourceEntity], str] = lambda entity: "",
) -> list[Change]:
    """Detect changed C++20 concept constraints in the public surface.

    CastXML does not expose concepts; the L4 clang extractor emits
    ``SourceEntity(kind="concept")`` whose ``value`` is a conjunctive atom
    set (``source_extractors/concept_constraints.py``) or, for a snapshot
    written before that encoding, an opaque fingerprint.

    A changed fingerprint alone proves only that the constraint *changed*,
    not that it became stricter (the reverse edit would differ just as
    much), so direction is decided by set inclusion: ``CONCEPT_TIGHTENED``
    (API_BREAK) only when the new atom set strictly contains the old one --
    every requirement kept, at least one added. Equal atom sets (reordered
    or renamed requirements) are no change. Anything else -- relaxed,
    rewritten, or an opaque side -- is ``CONCEPT_CONSTRAINT_CHANGED``
    (RISK): a relaxed concept can still change overload resolution through
    subsumption, so it is not reported as safe either.
    """
    old_concepts = _concepts_by_name(old.reachable_declarations + old.reachable_types)
    new_concepts = _concepts_by_name(new.reachable_declarations + new.reachable_types)
    changes: list[Change] = []
    for name in sorted(set(old_concepts) & set(new_concepts)):
        old_entity = old_concepts[name]
        new_entity = new_concepts[name]
        if old_entity.body_hash and old_entity.body_hash == new_entity.body_hash:
            continue
        old_constraint = _concept_constraint(old_entity)
        new_constraint = _concept_constraint(new_entity)
        if not (old_constraint and new_constraint) or old_constraint == new_constraint:
            continue
        old_atoms = conjunctive_atoms(old_entity.value)
        new_atoms = conjunctive_atoms(new_entity.value)
        if old_atoms is not None and new_atoms is not None:
            if old_atoms == new_atoms:
                continue
            if old_atoms < new_atoms:
                kind = ChangeKind.CONCEPT_TIGHTENED
                description = (
                    f"Concept constraint tightened: {name} "
                    f"(+{len(new_atoms - old_atoms)} requirement(s), none removed)"
                )
            elif new_atoms < old_atoms:
                kind = ChangeKind.CONCEPT_CONSTRAINT_CHANGED
                description = (
                    f"Concept constraint relaxed: {name} "
                    f"(-{len(old_atoms - new_atoms)} requirement(s)); may change "
                    "overload resolution, no compile failure proven"
                )
            else:
                kind = ChangeKind.CONCEPT_CONSTRAINT_CHANGED
                description = (
                    f"Concept constraint changed: {name} (requirements both "
                    "added and removed; strictness not decidable)"
                )
        else:
            kind = ChangeKind.CONCEPT_CONSTRAINT_CHANGED
            description = (
                f"Concept constraint changed: {name} (no structured constraint "
                "on one side; direction not decidable)"
            )
        changes.append(
            Change(
                kind=kind,
                symbol=name,
                description=description,
                old_value=old_constraint,
                new_value=new_constraint,
                source_location=loc(new_entity),
            )
        )
    return changes
