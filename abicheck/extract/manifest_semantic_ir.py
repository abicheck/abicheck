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

**Disambiguation is added only where an actual collision could occur, not
unconditionally (Codex review, two rounds, fresh evidence both times) --
never simply "on" for the whole manifest.** Two independent axes:

1. **Cross-fragment source-location variance.** An entity is disambiguated
   by ``source_location`` only when it is genuinely declared at more than
   one *distinct* location across the contributing fragments
   (:func:`_ambiguous_by_source_location`, computed from the fragments'
   own raw declarations, not from ``tu_merge.merge_fragments``'s already-
   folded output). A public header's forward declaration alongside a
   private header's full definition of the same type produces two distinct
   locations and survives as two occurrences; the far more common case --
   one declaration observed redundantly because many TUs ``#include`` the
   same, unmodified header -- reports the identical location everywhere
   and needs no disambiguator at all, since a bare ``EntityId`` key already
   collapses it correctly. **This is also what keeps a single-TU manifest's
   occurrence IDs canonical** (Codex review, second round): with only one
   contributing fragment there is no second location to differ from, so no
   entity is ever disambiguated -- ``OccurrenceId.disambiguator`` stays
   ``""`` for every occurrence, identically to what a non-manifest,
   single-header normalization already produces, instead of unconditionally
   stamping a nonempty path-derived disambiguator onto every occurrence
   purely because ``--dump-manifest`` happened to be the entry point.
2. **TU-local (internal) linkage.** A ``static``/anonymous-namespace
   function or variable is a *different, TU-scoped declaration* in every
   TU that defines one, even when two TUs happen to declare their own
   private copy from the identical shared header at the identical
   location -- the ordinary source-location signal above cannot tell these
   apart, since both the ``EntityId`` (built from the mangled name, which
   carries no per-TU distinguishing information for internal linkage --
   see :func:`abicheck.extract.headers.castxml.names.
   _mangled_name_is_local_linkage`) and the location are identical
   (Codex review, fresh evidence: reproduced with two TUs sharing one
   header containing a ``static`` function, where ``tu_merge.
   _function_key`` itself already keys by ``tu_name`` for exactly this
   reason -- see that function's own docstring). Every occurrence of a
   TU-local function/variable is therefore disambiguated by its own
   ``tu_name`` unconditionally, regardless of location variance, mirroring
   ``tu_merge.py``'s own internal-linkage scoping rather than reinventing
   it -- reusing the identical mangled-name signal
   (:func:`_is_locally_linked_function`/:func:`_is_locally_linked_variable`)
   since ``extract/`` may not import the root-level ``tu_merge`` module
   (ADR-061: ``extract -> model, storage`` only) but the underlying
   ``extract.headers.castxml.names`` primitive both already share.

Typedefs/constants carry no ``source_location`` in this codebase's model at
all, so neither axis applies to them -- unaffected by this pass, exactly as
``merge_fragments``'s own flat fields already are.

Depends on ``model``/``storage`` and its siblings ``extract.
semantic_normalizer``/``extract.headers.castxml.names`` (allowed:
``extract -> model, storage``, ADR-061), plus ``tu_fragment`` -- a
dependency-free leaf module (``model`` only) this package already treats as
shared shape vocabulary the same way ``dumper_manifest.py``/``tu_merge.py``
do, not orchestration logic of its own.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..tu_fragment import TuFragment
from .headers.castxml.names import _mangled_name_is_local_linkage
from .semantic_normalizer import normalize_header_ast

if TYPE_CHECKING:
    from ..model.declarations import Function, Variable
    from ..model.identity import EntityId

__all__ = ["manifest_semantic_ir"]


def _has_local_linkage_mangling(mangled: str) -> bool:
    """See ``tu_merge._has_local_linkage_mangling``'s own docstring --
    reused verbatim rather than re-derived, since ``extract/`` may not
    import that root-level module."""
    return _mangled_name_is_local_linkage(mangled) or "_GLOBAL__N_" in mangled


def _is_locally_linked_function(fn: Function) -> bool:
    """See ``tu_merge._function_key``'s own docstring for the full
    reasoning behind each branch -- reused, not reinvented."""
    if fn.mangled == fn.name:
        return fn.is_static
    return _has_local_linkage_mangling(fn.mangled)


def _is_locally_linked_variable(var: Variable) -> bool:
    """See ``tu_merge._variable_key``'s own docstring."""
    return _has_local_linkage_mangling(var.mangled)


def _locally_linked_entity_ids(fragments: Sequence[TuFragment]) -> set[EntityId]:
    """Every ``EntityId`` belonging to a TU-local function/variable,
    across all *fragments* -- computed once, globally, since a genuinely
    TU-local declaration's own mangled spelling (and therefore its
    ``EntityId``) carries the same locality signal in every fragment it
    appears in."""
    local_ids: set[EntityId] = set()
    for fragment in fragments:
        local_ids.update(
            fn.entity_id
            for fn in fragment.functions
            if fn.entity_id is not None and _is_locally_linked_function(fn)
        )
        local_ids.update(
            var.entity_id
            for var in fragment.variables
            if var.entity_id is not None and _is_locally_linked_variable(var)
        )
    return local_ids


def _ambiguous_by_source_location(fragments: Sequence[TuFragment]) -> set[EntityId]:
    """Every ``EntityId`` declared at more than one *distinct*
    ``source_location`` across *fragments*' own raw declarations -- the
    only entities a genuine cross-TU split could hide behind. An entity
    seen at exactly one location (whether from one fragment or repeated
    identically across many) is not ambiguous: a bare ``EntityId`` key
    already resolves it to a single occurrence correctly."""
    locations: dict[EntityId, set[str]] = defaultdict(set)
    for fragment in fragments:
        for rt in fragment.types:
            if rt.entity_id is not None:
                locations[rt.entity_id].add(rt.source_location or "")
        for et in fragment.enums:
            if et.entity_id is not None:
                locations[et.entity_id].add(et.source_location or "")
        for fn in fragment.functions:
            if fn.entity_id is not None:
                locations[fn.entity_id].add(fn.source_location or "")
        for var in fragment.variables:
            if var.entity_id is not None:
                locations[var.entity_id].add(var.source_location or "")
    return {entity_id for entity_id, locs in locations.items() if len(locs) > 1}


def manifest_semantic_ir(fragments: Sequence[TuFragment]) -> SemanticIR:
    """See this module's own docstring."""
    local_entity_ids = _locally_linked_entity_ids(fragments)
    ambiguous_entity_ids = _ambiguous_by_source_location(fragments)
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
            if occ_id.entity_id in local_entity_ids:
                occ_id = OccurrenceId(
                    entity_id=occ_id.entity_id, disambiguator=fragment.tu_name
                )
            elif occ_id.entity_id not in ambiguous_entity_ids:
                occ_id = OccurrenceId(entity_id=occ_id.entity_id, disambiguator="")
            occurrences.setdefault(occ_id, entity)
    return SemanticIR(occurrences=occurrences)
