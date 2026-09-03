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
"""``dumper_scoping._scoped_semantic_ir``'s per-OCCURRENCE dependency
check (ADR-063 Phase 6, Codex review, PR #1024, fresh evidence) -- split
into this sibling leaf module to keep ``dumper_scoping.py`` under its
``architecture/debt.yaml`` no-growth baseline (move responsibility out,
never raise the baseline, per this repository's own governing
convention).

A manifest dump can carry more than one occurrence for the SAME
``EntityId`` (ADR-063 Phase 6's multi-TU slice) -- e.g. a forward
declaration reached through the library's own header, and the identical
declaration ALSO reached through an unrelated system header from a
different TU. ``dumper_scoping._scoped_semantic_ir``'s existing
whole-``EntityId`` membership check alone is not enough: since the flat
representative ``tu_merge.merge_fragments`` picks for that ``EntityId``
prefers the more-public provenance, the project-header declaration wins
and the ``EntityId`` is kept -- but that check then wrongly kept BOTH
occurrences too, leaking the excluded system-header declaration's own
evidence into a default-scoped snapshot (confirmed empirically). This
module's :func:`scoped_occurrences_excluding_dependencies` closes that by
additionally checking each individual occurrence's OWN
disambiguator-derived location, not just its shared identity's
representative -- but only ever removes a dependency occurrence when
another, non-dependency occurrence of the SAME identity is also kept.

That last qualifier matters: ``scope_snapshot_excluding_dependencies()``
deliberately keeps a dependency-header type/function/variable whose flat
representative is directly named by a kept public declaration (a
``std::string`` parameter, say), even though every one of ITS OWN
occurrences lives under a dependency header. A per-occurrence check with
no such qualifier would drop all of that identity's occurrences purely
because they are all dependency-header ones -- leaving the retained flat
entity with zero surviving ``SemanticIR`` evidence, silently
under-serving an IR-aware consumer relative to a flat-field one (Codex
review, second round, fresh evidence). So an occurrence is only ever
excluded here when a *different*, non-dependency occurrence of the same
``EntityId`` survives to take its place as that identity's IR evidence.

Leaf module: depends only on ``.provenance`` (the same header-origin
classifier ``dumper_scoping.py``'s own flat-field filtering already
applies to ``source_header``, reused here rather than a second,
independently-drifting classifier).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set as AbstractSet
from pathlib import Path

from ..model.identity import EntityId, EntityKind
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity
from ..provenance import header_from_location, is_dependency_header

_SCOPED_ENTITY_KINDS = (
    EntityKind.TYPE,
    EntityKind.ENUM,
    EntityKind.FUNCTION,
    EntityKind.VARIABLE,
)


def is_dependency_occurrence(
    disambiguator: str, header_roots: Sequence[Path | str] | None
) -> bool:
    """Whether *disambiguator* -- one occurrence's OWN location-derived
    disambiguator, as :func:`~abicheck.extract.manifest_semantic_ir.
    manifest_semantic_ir` stamps it -- names a toolchain/dependency header.
    See this module's own docstring for why this per-occurrence check
    exists at all, beyond the whole-``EntityId`` membership test
    ``dumper_scoping._scoped_semantic_ir`` already applies.

    A blank disambiguator (the common case: every fragment agreed on one
    location, so ``manifest_semantic_ir`` blanked it) answers ``False``
    unconditionally: this is consulted only for an ``EntityId`` already
    confirmed non-dependency via its flat representative, so a blank
    occurrence of that identity is already resolved correctly -- dropping
    it here on no evidence would silently orphan a kept identity.

    Deliberately parses the disambiguator text as-is, without first
    stripping a locally-linked occurrence's own ``"<tu_name>:"`` prefix
    (an earlier draft did, and found the split ambiguous in the general
    case -- a Windows drive-letter colon, or an arbitrary TU name
    containing one, both make "split on the first colon" unreliable).
    Splitting is unnecessary: every classification
    :func:`~abicheck.provenance.is_dependency_header` performs is a
    SUFFIX or CONTIGUOUS-SUBSEQUENCE match over path segments, never
    anchored to the first segment, so an extra leading ``"<tu_name>:"``
    segment cannot itself create or hide a match. Verified directly
    against a real clang manifest dump with a locally-linked (``static``)
    declaration placed in a synthetic ``.../usr/include/...`` header path
    (``tests/test_dumper_scoping.py``), not merely reasoned about.
    """
    if not disambiguator:
        return False
    header = header_from_location(disambiguator)
    return is_dependency_header(header, header_roots)


def scoped_occurrences_excluding_dependencies(
    occurrences: Mapping[OccurrenceId, CanonicalEntity],
    kept_entity_ids: AbstractSet[EntityId],
    header_roots: Sequence[Path | str] | None,
) -> tuple[dict[OccurrenceId, CanonicalEntity], list[OccurrenceId]]:
    """Apply ``dumper_scoping._scoped_semantic_ir``'s combined
    whole-``EntityId`` and per-occurrence dependency scoping to
    *occurrences*, returning ``(kept, excluded_ids)``.

    A non-scoped kind (typedefs, ...) always survives untouched. A scoped
    kind's occurrence survives if its ``EntityId`` is not in
    *kept_entity_ids* -- excluded, matching the flat-field filter exactly
    -- OR if this occurrence's own location is not itself a dependency
    header (see :func:`is_dependency_occurrence`).

    A *kept*-identity occurrence whose own location IS a dependency header
    is excluded only when another, non-dependency occurrence of the SAME
    ``EntityId`` also survives (this module's own docstring: dropping every
    occurrence of a directly-referenced dependency identity would leave its
    still-retained flat entity with zero ``SemanticIR`` evidence). This
    requires a first pass over every occurrence to learn, per kept
    ``EntityId``, whether it has any non-dependency occurrence at all --
    a single per-occurrence predicate cannot answer that on its own.
    """
    non_dependency_identities: set[EntityId] = set()
    for occ_id in occurrences:
        entity_id = occ_id.entity_id
        if (
            entity_id.kind in _SCOPED_ENTITY_KINDS
            and entity_id in kept_entity_ids
            and not is_dependency_occurrence(occ_id.disambiguator, header_roots)
        ):
            non_dependency_identities.add(entity_id)

    kept: dict[OccurrenceId, CanonicalEntity] = {}
    excluded: list[OccurrenceId] = []
    for occ_id, entity in occurrences.items():
        entity_id = occ_id.entity_id
        if entity_id.kind not in _SCOPED_ENTITY_KINDS:
            kept[occ_id] = entity
            continue
        if entity_id not in kept_entity_ids:
            excluded.append(occ_id)
            continue
        if (
            is_dependency_occurrence(occ_id.disambiguator, header_roots)
            and entity_id in non_dependency_identities
        ):
            excluded.append(occ_id)
        else:
            kept[occ_id] = entity
    return kept, excluded
