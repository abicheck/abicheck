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
unconditionally (Codex review, three rounds, fresh evidence each time) --
never simply "on" for the whole manifest.** Two independent axes:

1. **Cross-fragment source-location variance, for an externally-linked
   entity.** Disambiguated by ``source_location`` only when it is
   genuinely declared at more than one *distinct* location across
   *different* contributing fragments
   (:func:`_ambiguous_by_source_location`, computed from the fragments'
   own raw declarations, not from ``tu_merge.merge_fragments``'s already-
   folded output). A public header's forward declaration alongside a
   private header's full definition of the same type produces two distinct
   locations and survives as two occurrences; the far more common case --
   one declaration observed redundantly because many TUs ``#include`` the
   same, unmodified header -- reports the identical location everywhere
   and needs no disambiguator at all, since a bare ``EntityId`` key already
   collapses it correctly. **Deliberately scoped to *cross*-fragment
   variance only** (Codex review, third round, fresh evidence): a single
   fragment declaring the identical entity at two different locations of
   its own (e.g. a forward declaration followed by its own definition in
   the same TU -- `tu_merge.py`'s own "same-TU-extras" tolerance already
   treats that as one compatible declaration, not two) must not be
   mistaken for a genuine split -- :func:`_ambiguous_by_source_location`
   compares each fragment's own *complete* set of locations for an entity
   against every other fragment's, so within-fragment variance alone can
   never mark an entity ambiguous. **This is also what keeps a single-TU
   manifest's occurrence IDs canonical** (Codex review, second round):
   with only one contributing fragment there is no second fragment to
   differ from, so no entity is ever disambiguated -- identically to what
   a non-manifest, single-header normalization already produces, instead
   of unconditionally stamping a nonempty path-derived disambiguator onto
   every occurrence purely because ``--dump-manifest`` happened to be the
   entry point.
2. **TU-local (internal) linkage, classified per fragment, not globally
   by EntityId (Codex review, third round, fresh evidence).** A
   ``static``/anonymous-namespace function or variable is a *different,
   TU-scoped declaration* in every TU that defines one, even when two TUs
   happen to declare their own private copy from the identical shared
   header at the identical location -- the source-location signal above
   cannot tell these apart, since both the ``EntityId`` (built from the
   mangled name, which carries no per-TU distinguishing information for
   internal linkage -- see :func:`abicheck.extract.headers.castxml.names.
   _mangled_name_is_local_linkage`) and the location are identical
   (Codex review, fresh evidence: reproduced with two TUs sharing one
   header containing a ``static`` function, where ``tu_merge.
   _function_key`` itself already keys by ``tu_name`` for exactly this
   reason -- see that function's own docstring). Locality is checked
   against *each fragment's own* declaration
   (:func:`_locally_linked_entity_ids_in_fragment`), never a single global
   set built once across every fragment: a plain-C function's own
   ``EntityId`` construction does not encode static-vs-external linkage at
   all (confirmed empirically -- an externally-linked `int helper();` and
   an unrelated file's `static int helper() { ... }` resolve to the
   *identical* `extra=("extern_c",)` `EntityId`), so a global set would
   wrongly TU-qualify every occurrence sharing that collided identity,
   including the genuinely external ones, and prevent them from
   collapsing into the single occurrence they should be. Every occurrence
   of a TU-local function/variable is disambiguated by combining its own
   ``tu_name`` with whatever location-based disambiguator this fragment's
   own normalization pass already computed (never replacing it outright --
   a second, independent Codex finding, third round: a TU-local entity can
   itself have more than one raw declaration within one fragment, e.g. its
   own prototype and definition, and `tu_merge.py` does not collapse those
   either even within a single TU-scoped key, confirmed empirically -- two
   TUs each contributing their own local prototype+definition pair for the
   same-named `static` function leaves four raw declarations after
   `tu_merge.merge_fragments`, and all four must survive as four distinct
   occurrences, not two), mirroring ``tu_merge.py``'s own internal-linkage
   scoping rather than reinventing it -- reusing the identical
   mangled-name signal (:func:`_is_locally_linked_function`/
   :func:`_is_locally_linked_variable`) since ``extract/`` may not import
   the root-level ``tu_merge`` module (ADR-061: ``extract -> model,
   storage`` only) but the underlying ``extract.headers.castxml.names``
   primitive both already share.

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
from .headers.scope_segments import entity_is_record_member
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


def _entity_id_is_extern_c(entity_id: EntityId | None) -> bool:
    """See ``tu_merge._entity_id_is_extern_c``'s own docstring -- reused,
    not reinvented, for the identical ``extract/`` may-not-import-
    ``tu_merge`` reason the rest of this module's small helpers already
    give."""
    return entity_id is not None and entity_id.extra == ("extern_c",)


def _is_locally_linked_function(fn: Function) -> bool:
    """See ``tu_merge._function_key``'s own docstring for the full
    reasoning behind each branch -- reused, not reinvented, including the
    ``entity_is_record_member`` gate closing that function's static-
    member-function sub-case (its sibling non-static-method collision
    remains a separately-documented, still-open limitation) and the
    Darwin-leading-underscore fix (macOS CI, fresh evidence): ``fn.mangled
    == fn.name`` is not proof of "no C++ mangling" on a Darwin target, so
    ``fn.is_extern_c`` -- each header-AST backend's own Darwin-aware
    determination -- is read directly instead, exactly mirroring
    ``tu_merge._function_key``'s identical fix."""
    if fn.mangled == fn.name or fn.is_extern_c:
        return fn.is_static and not entity_is_record_member(fn.entity_id)
    return _has_local_linkage_mangling(fn.mangled)


def _is_locally_linked_variable(var: Variable) -> bool:
    """See ``tu_merge._variable_key``'s own docstring -- including the
    plain-C ``var.mangled == var.name`` fallback branch, closed by
    ``Variable.is_static`` (PR #1024, Codex/CodeRabbit review), the
    ``entity_is_record_member`` gate closing that same function's
    uninstantiated-template-static-data-member gap, and the Darwin-
    leading-underscore fix (macOS CI, fresh evidence) mirroring
    ``tu_merge._variable_key``'s identical fix: :class:`Variable` carries
    no ``is_extern_c`` field of its own, so :func:`_entity_id_is_extern_c`
    reads the same Darwin-aware signal back off ``var.entity_id`` instead."""
    if var.mangled == var.name or _entity_id_is_extern_c(var.entity_id):
        return var.is_static and not entity_is_record_member(var.entity_id)
    return _has_local_linkage_mangling(var.mangled)


def _locally_linked_entity_ids_in_fragment(fragment: TuFragment) -> set[EntityId]:
    """Every ``EntityId`` belonging to a TU-local function/variable in
    *this fragment alone* -- never aggregated globally across fragments
    (Codex review, third round, fresh evidence): a plain-C function's own
    ``EntityId`` construction does not encode static-vs-external linkage
    (confirmed empirically -- an externally-linked and an unrelated
    internally-linked same-named plain-C function resolve to the
    *identical* ``extra=("extern_c",)`` identity), so a global set would
    wrongly promote every occurrence sharing that collided identity to be
    TU-scoped, including genuinely external ones from other fragments that
    must still collapse together."""
    local_ids: set[EntityId] = set()
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


def _fragment_locations(fragment: TuFragment) -> dict[EntityId, set[str]]:
    """This *one* fragment's own ``entity_id -> {source_location, ...}``
    map, from its raw (pre-merge) declarations."""
    locations: dict[EntityId, set[str]] = defaultdict(set)
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
    return locations


def _per_entity_location_sets(
    fragments: Sequence[TuFragment],
) -> tuple[dict[EntityId, set[frozenset[str]]], dict[EntityId, int]]:
    """Every ``EntityId``'s own set of per-fragment *complete* location
    sets, AND the raw count of fragments that observed it at all -- shared
    by :func:`_ambiguous_by_source_location` (needs only the former) and
    :func:`_multi_location_non_ambiguous_entity_ids` (needs both -- see
    that function's own docstring for why the raw fragment count, not the
    deduped location-set count, is the gate it actually needs). One pass
    over the fragments, read two different ways rather than recomputed
    twice."""
    per_entity_fragment_sets: dict[EntityId, set[frozenset[str]]] = defaultdict(set)
    per_entity_fragment_count: dict[EntityId, int] = defaultdict(int)
    for fragment in fragments:
        for entity_id, locs in _fragment_locations(fragment).items():
            per_entity_fragment_sets[entity_id].add(frozenset(locs))
            per_entity_fragment_count[entity_id] += 1
    return per_entity_fragment_sets, per_entity_fragment_count


def _ambiguous_by_source_location(
    per_entity_fragment_sets: dict[EntityId, set[frozenset[str]]],
) -> set[EntityId]:
    """Every ``EntityId`` whose declarations span more than one *distinct*
    per-fragment location-set -- the only entities a genuine cross-TU
    split could hide behind. Compares each fragment's own *complete* set
    of locations for an entity against every other fragment's, rather than
    pooling every location globally (Codex review, third round, fresh
    evidence): a single fragment declaring the same entity at two
    locations of its own (e.g. a forward declaration followed by its own
    definition in the same TU) contributes exactly one such set and can
    never make an entity ambiguous by itself, matching ``tu_merge.py``'s
    own "same-TU-extras" tolerance for that shape. Two fragments
    contributing the identical location-set (the redundant shared-header
    case, whether from one or several locations each) are also not
    ambiguous -- only fragments whose own location-sets genuinely differ
    from each other are."""
    return {
        entity_id
        for entity_id, fragment_sets in per_entity_fragment_sets.items()
        if len(fragment_sets) > 1
    }


def _multi_location_non_ambiguous_entity_ids(
    per_entity_fragment_sets: dict[EntityId, set[frozenset[str]]],
    per_entity_fragment_count: dict[EntityId, int],
) -> set[EntityId]:
    """Every ``EntityId`` that is NOT cross-fragment ambiguous (every
    fragment that observes it agrees on the identical, single location
    set), was observed by AT LEAST TWO fragments, and whose one
    agreed-upon location set itself has MORE THAN ONE member -- e.g. an
    externally-linked prototype immediately followed by its own
    definition, both declared together in one shared header that every
    including TU sees identically (Codex review, PR #1024, fresh evidence
    beyond the prior same-TU-only case: this also happens across
    *multiple* fragments whose complete multi-location sets are equal to
    each other, not only within one fragment).

    :func:`manifest_semantic_ir` must NOT blank the per-location
    disambiguator for one of these -- doing so unconditionally (as an
    earlier revision did for every non-ambiguous entity) collapses the two
    real, distinct declarations into one occurrence, exactly the ODR-
    duplicate-collapsing bug this IR exists to avoid. An entity whose one
    agreed-upon location set has exactly one member (the ordinary case --
    a single declaration observed redundantly by every including TU) is
    correctly excluded here: blanking its disambiguator is what lets
    ``occurrences.setdefault`` fold every fragment's redundant observation
    of that one real declaration down to the single occurrence it is.

    The *fragment count* gate (Codex review, second round, fresh evidence)
    is what keeps a genuinely single-fragment manifest run consistent with
    the non-manifest single-TU path: a lone fragment's own
    prototype-then-definition pair reduces to one ``frozenset`` of size 2
    the identical way a real cross-fragment agreement does, so the
    location-set-size check alone cannot tell them apart -- but only the
    cross-fragment case has a non-manifest counterpart to stay consistent
    with; a `--dump-manifest` run over exactly one TU must reproduce the
    ordinary (non-manifest) `normalize_header_ast` call's own collapse of
    every declaration onto its first-seen occurrence byte-for-byte, not
    invent a persisted-occurrence-ID difference that exists solely because
    a manifest was used. A single fragment's own multi-location entity
    (count == 1) is therefore excluded here regardless of its location-set
    size, leaving it to the ordinary blank-disambiguator branch below."""
    result: set[EntityId] = set()
    for entity_id, fragment_sets in per_entity_fragment_sets.items():
        if len(fragment_sets) != 1:
            continue
        if per_entity_fragment_count[entity_id] < 2:
            continue
        (only_location_set,) = fragment_sets
        if len(only_location_set) > 1:
            result.add(entity_id)
    return result


def manifest_semantic_ir(fragments: Sequence[TuFragment]) -> SemanticIR:
    """See this module's own docstring."""
    per_entity_fragment_sets, per_entity_fragment_count = _per_entity_location_sets(
        fragments
    )
    ambiguous_entity_ids = _ambiguous_by_source_location(per_entity_fragment_sets)
    multi_location_entity_ids = _multi_location_non_ambiguous_entity_ids(
        per_entity_fragment_sets, per_entity_fragment_count
    )
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    for fragment in sorted(fragments, key=lambda f: f.tu_name):
        local_entity_ids = _locally_linked_entity_ids_in_fragment(fragment)
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
                # Combine, never replace: this fragment's own declaration
                # may itself carry more than one raw location (its own
                # prototype and definition), and each must stay distinct.
                occ_id = OccurrenceId(
                    entity_id=occ_id.entity_id,
                    disambiguator=f"{fragment.tu_name}:{occ_id.disambiguator}",
                )
            elif (
                occ_id.entity_id not in ambiguous_entity_ids
                and occ_id.entity_id not in multi_location_entity_ids
            ):
                # Blank the disambiguator only when this entity's one
                # agreed-upon location set has exactly one member -- the
                # ordinary "redundant observation of one real declaration"
                # case. An entity in `multi_location_entity_ids` keeps its
                # own per-location disambiguator unchanged (never replaced
                # with `tu_name` -- that combining form is only for the
                # locally-linked branch above): since every fragment that
                # sees it produces the identical location-derived text for
                # each of its real declarations, `occurrences.setdefault`
                # below still naturally folds the same declaration seen
                # redundantly across TUs while keeping the distinct real
                # declarations distinct (see
                # `_multi_location_non_ambiguous_entity_ids`'s own
                # docstring).
                occ_id = OccurrenceId(entity_id=occ_id.entity_id, disambiguator="")
            occurrences.setdefault(occ_id, entity)
    return SemanticIR(occurrences=occurrences)
