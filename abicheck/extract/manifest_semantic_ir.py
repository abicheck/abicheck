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

"""``AbiSnapshot.semantic_ir`` for a real ``--dump-manifest`` (multi-TU)
dump (ADR-063 Phase 6, multi-TU slice).

Split out of ``dumper_manifest.py`` (which has no line-count headroom left
before the AI-readiness file-size gate's 800-line production cap) purely to
keep that file under the cap -- a mechanical relocation, not a redesign;
``dumper_manifest.run_tu_loop`` is this function's only caller. Lives under
``extract/`` (ADR-061 D9's task-routing table: normalizing already-parsed
facts into a shared IR is squarely this package's job, and a new root
``dumper_*`` sibling is a frozen family the architecture gate rejects
outright) rather than back in ``dumper_manifest.py`` itself, alongside its
sibling ``extract/semantic_normalizer.py``.

**Why this needs its own pass, not just reading `merge_fragments`'s own
output.** ``tu_merge.merge_fragments`` already collapses same-identity
declarations across translation units into one representative entry before
this ever runs -- exactly the distinction ``SemanticIR.occurrences`` (keyed
by ``OccurrenceId``, not ``EntityId``) exists to preserve instead. So this
function normalizes each contributing TU's own RAW, pre-merge fragment
independently, then unions the resulting per-fragment occurrence maps
(first-fragment-wins on a key collision, fragments ordered by ``tu_name``
to match ``tu_merge.merge_fragments``'s own "Determinism" discipline, so
the result never depends on the caller's fragment order).

**What actually makes a genuine split survive without exploding the common
case.** Each occurrence's own ``source_location`` becomes its
``OccurrenceId`` disambiguator (``extract.semantic_normalizer.
normalize_header_ast``'s own ``disambiguate_by_source_location=True`` --
see that parameter's own docstring for the full reasoning). A genuine
cross-TU declaration split -- a public header's forward declaration
alongside a private header's full definition, at two different locations
-- produces two distinct occurrences this way. The ordinary case -- one
declaration observed redundantly because many TUs ``#include`` the same
header -- collapses to a single occurrence instead, since every TU's own
copy of an unmodified, textually-identical declaration reports the
identical ``file:line``. Typedefs/constants carry no ``source_location`` in
this codebase's model at all, so this distinction does not apply to them
either way (neither has an "incomplete" form to begin with) -- unaffected
by this pass, exactly as ``merge_fragments``'s own flat fields already are.

Depends on ``model``/``storage`` and its sibling ``extract.
semantic_normalizer`` (allowed: ``extract -> model, storage``, ADR-061),
plus ``tu_fragment`` -- a dependency-free leaf module (``model`` only)
this package already treats as shared shape vocabulary the same way
``dumper_manifest.py``/``tu_merge.py`` do, not orchestration logic of its
own.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..tu_fragment import TuFragment
from .semantic_normalizer import normalize_header_ast

__all__ = ["manifest_semantic_ir"]


def manifest_semantic_ir(fragments: Sequence[TuFragment]) -> SemanticIR:
    """See this module's own docstring."""
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    for fragment in sorted(fragments, key=lambda f: f.tu_name):
        fragment_ir = normalize_header_ast(
            types=fragment.types,
            enums=fragment.enums,
            typedefs_qualified=fragment.typedefs_qualified,
            typedef_entity_ids=fragment.typedef_entity_ids,
            producer=fragment.ast_producer,
            functions=fragment.functions,
            variables=fragment.variables,
            constants=fragment.constants,
            constant_entity_ids=fragment.constant_entity_ids,
            disambiguate_by_source_location=True,
        )
        for occ_id, entity in fragment_ir.occurrences.items():
            occurrences.setdefault(occ_id, entity)
    return SemanticIR(occurrences=occurrences)
