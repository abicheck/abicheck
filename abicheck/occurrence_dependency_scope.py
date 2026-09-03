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
module's :func:`occurrence_survives_dependency_scope` (built on
:func:`is_dependency_occurrence`) closes that by additionally checking
each individual occurrence's OWN disambiguator-derived location, not
just its shared identity's representative.

Leaf module: depends only on ``.provenance`` (the same header-origin
classifier ``dumper_scoping.py``'s own flat-field filtering already
applies to ``source_header``, reused here rather than a second,
independently-drifting classifier).
"""

from __future__ import annotations

from collections.abc import Sequence, Set as AbstractSet
from pathlib import Path

from .model.identity import EntityId, EntityKind
from .model.occurrence import OccurrenceId
from .provenance import header_from_location, is_dependency_header

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


def occurrence_survives_dependency_scope(
    occ_id: OccurrenceId,
    kept_entity_ids: AbstractSet[EntityId],
    header_roots: Sequence[Path | str] | None,
) -> bool:
    """Whether *occ_id* survives ``dumper_scoping._scoped_semantic_ir``'s
    combined whole-``EntityId`` and per-occurrence dependency scoping:
    non-scoped kinds (typedefs, ...) always survive untouched; a scoped
    kind survives only if its ``EntityId`` is in *kept_entity_ids* AND
    this occurrence's own location is not itself a dependency header (see
    :func:`is_dependency_occurrence` and this module's own docstring).
    """
    if occ_id.entity_id.kind not in _SCOPED_ENTITY_KINDS:
        return True
    return occ_id.entity_id in kept_entity_ids and not is_dependency_occurrence(
        occ_id.disambiguator, header_roots
    )
