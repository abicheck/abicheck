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

"""The typedef detector family, reading through
:class:`~abicheck.model.semantic_ir_index.SemanticIrIndex` (ADR-063 Phase 6's
first real checker cutover).

**Why typedefs are the first cohort.** Phase 6's own non-goal is "don't keep
widening producer coverage until one full vertical slice is proven", so the
first cohort has to be one ``SemanticIR`` already covers *completely* rather
than one that would need new extraction work to migrate. Typedefs are the
only family where the IR carries the whole of what the detector needs and
nothing it does not: identity (``EntityId``, resolved by both header-AST
backends since Phase 2's twelfth slice) plus exactly one payload fact
(``CanonicalEntity.canonical_spelling`` -- for a typedef, its resolved
underlying type, which is precisely the value this detector compares).
Records carry layout facts the IR does not yet model; functions need a
canonical signature spelling whose cross-backend agreement is its own open
question; constants' payload is a value literal, not a type spelling. Each
of those would have made the first slice about extraction coverage instead
of about proving the read path.

**This module may not read a legacy typedef collection.** Not by convention
-- ``scripts/semantic_ir_cutover.py`` enforces it as a real AST scan
(``semantic-ir-cutover`` in the AI-readiness gate), with no allowlist: this
family is freshly migrated, so there is nothing grandfathered to permit.
``AbiSnapshot.typedefs``/``typedefs_qualified``/``typedef_entity_ids`` are
read exactly once, inside ``model/semantic_ir_legacy_adapter.py``, which is
where a *projection* of the legacy shape belongs. The pair-wise
"which alias map does this comparison trust" decision
(``diff_helpers.typedef_diff_maps``) and the two snapshot-level suppression
flags stay with the caller in ``diff_types.py`` and arrive here as plain
values, for the same reason: they are questions about the comparison, not
about a typedef.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from ..diff_helpers import make_change, typedef_side_trusts_qualified
from ..model.change_catalog.kinds import ChangeKind
from ..model.fact import Fact
from ..model.identity import Anonymous, EntityId, EntityKind
from ..model.identity_stability import entity_id_is_cross_snapshot_stable
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir import CanonicalEntity, SemanticIR
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    SYNTHETIC_IDENTITY_EXTRA,
    legacy_typedef_ir,
    producer_entity_id,
    render_display_name_or_leaf,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot

__all__ = ["diff_typedefs", "is_version_stamped_typedef", "typedef_index_pair"]


class _SurfacePredicate(Protocol):
    """``model.is_non_abi_surface_type``'s call shape, as this module needs
    it. Injected rather than imported so a detector states no opinion about
    surface policy (ADR-061 assigns that to ``policy``/``model``), and so a
    test can substitute one without patching a module global."""

    def __call__(self, name: str, *, exclude_stdlib_namespaces: bool) -> bool: ...


#: Both header-AST backends spell an unfollowable typedef chain with this
#: placeholder, and ``extract/semantic_normalizer.py`` deliberately records
#: it as ``Fact.failed(...)`` rather than a present spelling (a present
#: placeholder would permanently block a hybrid merge's backfill). This
#: detector maps a non-present spelling back onto the same placeholder so
#: its comparison is bit-for-bit what the legacy raw-string path did -- an
#: unresolved-vs-unresolved pair is still "unchanged", and an
#: unresolved-vs-resolved pair is still a base-type change.
_UNRESOLVED_TYPE_SENTINEL = "?"

_VERSION_STAMPED_TYPEDEF_RE = re.compile(r"^(.*?)_version_\d+_\d+_\d+$", re.IGNORECASE)
"""Pattern for version-stamped compile-time sentinel typedefs.

Some libraries (e.g. libpng) define typedefs whose names encode the library
version, e.g. ``typedef char* png_libpng_version_1_6_46``.  The name changes
every release by design -- this is NOT a binary ABI break because the typedef
is never exported as an ELF symbol; it exists solely to produce a
compile-time error if headers from different versions are mixed.

When such a typedef disappears (``typedef_removed``), abicheck would
otherwise report BREAKING.  This guard downgrades the change to
TYPEDEF_VERSION_SENTINEL (COMPATIBLE) instead.

Moved here verbatim from ``diff_types.py`` with this cohort -- the same
pattern, not a re-derived one.
"""


def is_version_stamped_typedef(name: str) -> bool:
    """True if *name* looks like a version-stamped sentinel typedef.

    Moved here from ``diff_types.py`` with this cohort: it is typedef-family
    logic with no other caller, and leaving it behind would have meant the
    migrated detector importing back into the module it was split out of.
    """
    return bool(_VERSION_STAMPED_TYPEDEF_RE.match(name))


def _has_version_family_successor(name: str, new_aliases: frozenset[str]) -> bool:
    """True if *new_aliases* contains another version-stamped typedef with the
    same family prefix (e.g. ``png_libpng_version_``).

    Distinguishes a sentinel rotation (old version removed, new version
    added) from a genuine removal whose name merely matches the pattern.
    Takes the alias *key set* rather than the alias map, since only the keys
    were ever consulted -- the narrower input is what lets this run off the
    index's display names with no legacy dict in scope.
    """
    m = _VERSION_STAMPED_TYPEDEF_RE.match(name)
    if not m:
        return False
    prefix = m.group(1).lower()
    # Require a non-empty family prefix so an unrelated sentinel whose own
    # name starts with `_version_` (e.g. `_version_1_0_0`) doesn't match.
    if not prefix:
        return False
    prefix = prefix + "_version_"
    return any(k.lower().startswith(prefix) for k in new_aliases)


def _underlying(index: SemanticIRIndex, occurrence_id: OccurrenceId) -> str:
    """*occurrence_id*'s resolved underlying type, or the unresolved
    placeholder.

    Reads *occurrence_id*'s own ``CanonicalEntity`` directly off
    ``index.ir.occurrences`` -- never ``SemanticIRIndex.fact()``/
    ``.entity()``, which answer off the *reduced*, one-entry-per-``EntityId``
    view (``SemanticIR.canonical_entities()``) and would silently collapse
    two genuine ODR-duplicate/multi-TU occurrences sharing one identity back
    onto a single winner (Codex review, PR #1078, fifteenth round) -- see
    :func:`_aliases`'s own docstring for the full account.

    See :data:`_UNRESOLVED_TYPE_SENTINEL`: a ``Fact`` that is not present
    means the producer could not follow the chain, which the legacy path
    represented as the literal ``"?"`` string. Collapsing the two back
    together here is what keeps the cutover behavior-preserving.
    """
    entity = index.ir.occurrences.get(occurrence_id)
    spelling = entity.canonical_spelling if entity is not None else None
    if spelling is not None and spelling.is_present and spelling.value is not None:
        value = spelling.value
        assert isinstance(value, str)
        return value
    return _UNRESOLVED_TYPE_SENTINEL


def _aliases(index: SemanticIRIndex) -> dict[str, list[OccurrenceId]]:
    """This index's typedef occurrences, grouped by their rendered alias --
    a *list* per alias, not a single winner (Codex review, PR #1078, sixth
    round), since two distinct entities can render to the identical alias
    (two anonymous-scoped typedefs sharing a leaf name, per
    ``render_display_name_or_leaf``'s own accepted collision risk) and both
    are still real, distinguishable evidence: collapsing to one via
    ``setdefault`` silently discarded whichever occurrence didn't win the
    race, so a real value change on the discarded one was invisible even
    though the underlying ``SemanticIR`` never actually merged the two
    occurrences the way a flat legacy map's own key collision would have.
    :func:`diff_typedefs` compares the *set* of values under a colliding
    alias rather than a single representative, so a real difference is
    never silently read as unchanged merely because attribution to one
    specific occurrence is ambiguous.

    **Grouped by raw occurrence, not by the reduced entity view** (Codex
    review, PR #1078, fifteenth round): ``SemanticIRIndex.entities_of_kind``
    is explicitly the *reduced* projection -- ``SemanticIR.
    canonical_entities()``'s one-entry-per-``EntityId`` "most facts present"
    winner, its own docstring's stated purpose being "a consumer that
    genuinely wants a single canonical view". Reading through it here
    collapsed a genuine ODR-duplicate/multi-TU pair sharing one ``EntityId``
    (exactly the case ``SemanticIR.occurrences`` -- keyed by
    :class:`~abicheck.model.occurrence.OccurrenceId`, not bare ``EntityId``
    -- exists to keep distinct) down to whichever occurrence won that
    reduction, silently discarding the other's own value evidence. Iterating
    ``index.ir.occurrences`` directly instead means two occurrences sharing
    an identity are just another instance of the alias-collision this
    function already groups and :func:`diff_typedefs` already compares by
    multiset -- no second mechanism needed, only reading at the right
    granularity to begin with.
    """
    by_alias: dict[str, list[OccurrenceId]] = {}
    for occurrence_id in index.ir.occurrences:
        if occurrence_id.entity_id.kind is not EntityKind.TYPEDEF:
            continue
        by_alias.setdefault(
            render_display_name_or_leaf(occurrence_id.entity_id), []
        ).append(occurrence_id)
    return by_alias


def diff_typedefs(
    old_index: SemanticIRIndex,
    new_index: SemanticIRIndex,
    *,
    exclude_stdlib_namespaces: bool,
    suppress_removed: bool,
    is_non_abi_surface_type: _SurfacePredicate,
) -> list[Change]:
    """Detect typedef removals, base-type changes, and version-sentinel
    rotations, reading only through the two indexes.

    *exclude_stdlib_namespaces* / *suppress_removed* are the comparison-level
    decisions the caller already made (``model.stdlib_namespaces_excluded``
    and ``diff_types._removals_are_unconfirmed``); *is_non_abi_surface_type*
    is injected rather than imported so this module states no opinion about
    surface policy, which ADR-061 assigns to ``policy``/``model``, not to a
    detector.

    Behavior is identical to the pre-cutover ``diff_types._diff_typedefs``,
    including the two spellings it emits: ``symbol``/``name`` stay *bare*
    (``diff_filtering._enrich_affected_symbols`` joins on the bare form),
    while the qualified spelling is appended to the description so dedup
    cannot collapse two same-leaf-named aliases in different scopes.

    **A colliding alias is compared by its whole value multiset, not one
    representative** (Codex review, PR #1078, sixth round): ``_aliases``
    groups every entity that renders to the same alias, since two distinct
    anonymous-scoped typedefs can share one leaf name. Picking an arbitrary
    representative per side (as an earlier version of this function did)
    could miss a real value change on whichever occurrence didn't become
    the representative, silently reporting no change at all. Comparing the
    sorted list of values under the alias on each side instead means a
    real difference is always detected, even though *which* specific
    occurrence changed remains genuinely ambiguous when more than one
    shares the alias -- the same ambiguity a flat legacy map's own
    bare-name key collision already accepts, just without this fix's
    additional failure mode of silently declaring "unchanged".

    **That sorted-list comparison itself had three further gaps**, mirroring
    ``compare.constants.diff_constants``'s identical, more fully-documented
    history (Codex review, PR #1078, tenth/eleventh rounds; see that
    function's own docstring for the full account): a colliding group that
    grew or shrank by an already-present value read as a spurious
    ``TYPEDEF_BASE_CHANGED`` instead of a pure (untracked, for typedefs)
    addition or a ``TYPEDEF_REMOVED``; a mixed group could lose an
    independently provable residual removal; and converting a value
    difference to a ``set`` for iteration made both which colliding value
    became the representative pair and repeated-value multiplicity itself
    depend on ``PYTHONHASHSEED``/silently collapse. All three are closed by
    the identical occurrence-level (``old_by_value``/``new_by_value``,
    plain ``dict``s, not ``Counter``/``set``) bookkeeping used there.
    """
    changes: list[Change] = []
    old_aliases = _aliases(old_index)
    new_aliases = _aliases(new_index)
    new_alias_keys = frozenset(new_aliases)

    for alias, old_ids in old_aliases.items():
        # Full alias: correct for both legacy-DWARF's and the qualified
        # map's keys.
        if is_non_abi_surface_type(
            alias, exclude_stdlib_namespaces=exclude_stdlib_namespaces
        ):
            continue
        bare_alias = alias.rsplit("::", 1)[-1]
        qualified_suffix = f" ({alias})" if alias != bare_alias else ""
        new_ids = new_aliases.get(alias)
        if new_ids is None and suppress_removed:
            # RD2-5: don't manufacture a phantom removal when the new side
            # is stripped of type evidence entirely.
            continue
        if new_ids is None:
            if is_version_stamped_typedef(alias) and _has_version_family_successor(
                alias, new_alias_keys
            ):
                # A version-stamped typedef (e.g. png_libpng_version_1_6_46)
                # is a compile-time sentinel that changes every release by
                # design and is never exported as an ELF symbol -- not a
                # binary ABI break. Requiring a same-family successor avoids
                # hiding a genuine removal for a name that merely matches
                # the pattern. One finding per contributing entity, not just
                # `old_ids[0]` (Codex review, PR #1078, twelfth round): a
                # colliding group's own version-sentinel rotation is still
                # one rotation per entity.
                for rotated_id in old_ids:
                    changes.append(
                        make_change(
                            ChangeKind.TYPEDEF_VERSION_SENTINEL,
                            symbol=bare_alias,
                            name=bare_alias,
                            old_value=_underlying(old_index, rotated_id),
                            entity_id=producer_entity_id(rotated_id.entity_id),
                        )
                    )
                continue
            # One `TYPEDEF_REMOVED` per contributing entity, not just
            # `old_ids[0]` (Codex review, PR #1078, twelfth round): when
            # the whole colliding group vanishes -- not merely shrinks --
            # every one of its distinct entities is an independent, real
            # removal, and each carries its own entity_id rather than
            # every finding sharing a single id.
            for removed_id in old_ids:
                changes.append(
                    make_change(
                        ChangeKind.TYPEDEF_REMOVED,
                        symbol=bare_alias,
                        name=bare_alias,
                        old_value=_underlying(old_index, removed_id),
                        entity_id=producer_entity_id(removed_id.entity_id),
                        description=f"Typedef removed: {bare_alias}{qualified_suffix}",
                    )
                )
            continue
        # Shared real identity resolved *before* any value-based pairing
        # (Codex review, PR #1078, thirteenth round): an entity present
        # under the identical `EntityId` on both sides of the comparison is
        # not an ambiguous member of the colliding group at all -- it is
        # the same declaration, so its own old/new value comparison is
        # exact, never a heuristic pairing. Skipping this and going
        # straight to value-based matching let a stable entity's own real
        # value change be silently absorbed into an unrelated occurrence's
        # addition/removal whenever the multiset arithmetic happened to
        # find a same-valued partner elsewhere in the group -- e.g. stable
        # `Alias` changing `int` -> `long` while a *different*, newly-added
        # colliding entity is `int`: value-only subtraction cancels the
        # stable entity's old `int` against the new entity's `int`,
        # reporting only a compatible-looking value mismatch instead of the
        # real breaking change to the stable entity. `set(old_ids) &
        # set(new_ids)` is exact, not a heuristic, *for a stable identity*:
        # `entities_of_kind()` never repeats an `EntityId` within one side,
        # so each shared id names exactly one occurrence per side -- but an
        # `Anonymous`/`LocalToFunction` scope segment's own ordinal is
        # explicitly not stable across two snapshots
        # (``model.identity_stability``'s own docstring: inserting an
        # earlier anonymous sibling shifts every later one's ordinal, and
        # therefore its whole `EntityId`, even though nothing about that
        # later declaration changed). Trusting a raw intersection here
        # would risk pairing two genuinely unrelated declarations that
        # happen to collide on a shifted ordinal plus the same bare leaf
        # name -- fabricating a `TYPEDEF_BASE_CHANGED` for what is really
        # just an unrelated addition (Codex review, PR #1078, fourteenth
        # round). Gated through
        # :func:`~abicheck.model.identity_stability.
        # entity_id_is_cross_snapshot_stable` -- this collision path is
        # exactly the "real consumer" that predicate's own docstring says
        # needs its own adversarial review before being wired in, so this
        # is the first real call site.
        shared_ids = {
            i
            for i in set(old_ids) & set(new_ids)
            if entity_id_is_cross_snapshot_stable(i.entity_id)
        }
        for shared_id in shared_ids:
            old_type = _underlying(old_index, shared_id)
            new_type = _underlying(new_index, shared_id)
            if old_type == new_type:
                continue
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_BASE_CHANGED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=old_type,
                    new_value=new_type,
                    entity_id=producer_entity_id(shared_id.entity_id),
                    description=(
                        f"Typedef base type changed: {bare_alias}{qualified_suffix}"
                    ),
                )
            )
        # Occurrence-level bookkeeping over the *remaining*, identity-
        # unmatched occurrences only, not a bare value `Counter`/`set`
        # (Codex review, PR #1078, eleventh round -- mirroring
        # ``compare.constants.diff_constants``'s identical fix, see that
        # function's own docstring for the full three-defect account): a
        # `Counter` alone cannot attribute a removed/added value back to
        # the specific entity that carried it, and converting its
        # difference to a `set` for iteration made both the choice of
        # which colliding value pairs into one ``TYPEDEF_BASE_CHANGED`` and
        # multiplicity itself (repeated identical values collapsed to one)
        # depend on `PYTHONHASHSEED`/silently lose evidence.
        # `old_by_value`/`new_by_value` group each side's own entities by
        # value in insertion order (deterministic, unlike a `set`); the
        # excess count for a value on one side over the other is exactly
        # that many removed/added occurrences, each keeping its own real
        # entity_id.
        old_by_value: dict[str, list[OccurrenceId]] = {}
        for i in old_ids:
            if i in shared_ids:
                continue
            old_by_value.setdefault(_underlying(old_index, i), []).append(i)
        new_by_value: dict[str, list[OccurrenceId]] = {}
        for i in new_ids:
            if i in shared_ids:
                continue
            new_by_value.setdefault(_underlying(new_index, i), []).append(i)
        removed_occurrences: list[tuple[str, OccurrenceId]] = []
        for value, ids_for_value in old_by_value.items():
            excess = len(ids_for_value) - len(new_by_value.get(value, ()))
            if excess > 0:
                removed_occurrences.extend((value, i) for i in ids_for_value[:excess])
        added_occurrences: list[tuple[str, OccurrenceId]] = []
        for value, ids_for_value in new_by_value.items():
            excess = len(ids_for_value) - len(old_by_value.get(value, ()))
            if excess > 0:
                added_occurrences.extend((value, i) for i in ids_for_value[:excess])
        if not removed_occurrences and not added_occurrences:
            continue
        if removed_occurrences and added_occurrences:
            # A genuine one-to-one substitution: consumes exactly one
            # occurrence from each side, leaving any further residual
            # occurrences to the loop below rather than folding them into
            # this one ``TYPEDEF_BASE_CHANGED`` (Codex review, PR #1078,
            # tenth round's constant-family sibling finding -- a mixed
            # removed-and-added group can carry more than one independent
            # piece of evidence).
            old_type, old_id = removed_occurrences.pop(0)
            new_type, new_id = added_occurrences.pop(0)
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_BASE_CHANGED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=old_type,
                    new_value=new_type,
                    entity_id=producer_entity_id(old_id.entity_id)
                    or producer_entity_id(new_id.entity_id),
                    description=(
                        f"Typedef base type changed: {bare_alias}{qualified_suffix}"
                    ),
                )
            )
        for leftover_old_value, leftover_old_id in removed_occurrences:
            # A pure removal within a colliding group: the alias itself
            # still exists on the new side (via another colliding member),
            # but this specific occurrence no longer does.
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_REMOVED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=leftover_old_value,
                    entity_id=producer_entity_id(leftover_old_id.entity_id),
                    description=f"Typedef removed: {bare_alias}{qualified_suffix}",
                )
            )
        # `added_occurrences`' own leftovers are a pure addition --
        # deliberately unreported, the same as a brand-new alias in
        # `new_aliases` that never appears in `old_aliases` at all: typedef
        # additions carry no `ChangeKind` and are always compatible.
    return changes


def _typedef_side_index(
    snapshot: AbiSnapshot, typedefs: dict[str, str]
) -> SemanticIRIndex:
    """One side's index: its real ``SemanticIR`` when it has one, or the
    legacy adapter's projection of *its own* flat typedef collection
    otherwise. See :func:`typedef_index_pair` for why this is decided
    per side rather than jointly."""
    if snapshot.semantic_ir is not None:
        return SemanticIRIndex(snapshot.semantic_ir)
    return SemanticIRIndex(legacy_typedef_ir(snapshot, typedefs))


def _bare_typedef_side_index(
    snapshot: AbiSnapshot, typedefs: dict[str, str]
) -> SemanticIRIndex:
    """One side's index for the bare-key-space branch of
    :func:`typedef_index_pair` -- projects this side's own real
    ``SemanticIR`` onto bare (unqualified) aliases when it has one, rather
    than trusting the caller-supplied *typedefs* bare map on its own (Codex
    review, PR #1078, seventh round).

    Every *real* header-AST producer populates ``typedefs``/
    ``typedefs_qualified``/``semantic_ir`` from the identical parsed element
    set in one pass (see ``dumper_castxml.py``'s/``dumper_clang.py``'s
    ``parse_typedefs``/``parse_typedefs_qualified``, both built from
    ``_typedefs_helpers.iter_typedef_entries``'s shared filtered element
    list) -- so for any snapshot a real producer emits, *typedefs* is empty
    if and only if *typedefs_qualified* is, and therefore if and only if
    ``semantic_ir`` carries no typedef occurrences either. A genuinely
    pre-v25 snapshot (this branch's actual target: no ``typedefs_qualified``
    at all, which also means no ``semantic_ir``) always falls through to
    the plain legacy-adapter path below unaffected.

    But this module's own per-side-independence design (see
    :func:`typedef_index_pair`'s docstring) already rejects relying on "a
    real ``SemanticIR`` snapshot always has its legacy sidecars populated
    too" as a standing invariant -- a hand-built or future-producer snapshot
    carrying real typedef ``SemanticIR`` occurrences with an empty bare
    *typedefs* map is exactly the case that reasoning calls out as not
    hypothetical. Without this projection, such a side's real evidence was
    silently discarded the moment the *other* side forced bare-key
    comparison (a genuinely pre-v25 baseline on one side, this side on the
    other) -- fabricating a removal for every typedef only this side's
    ``SemanticIR`` carries. Projecting the real IR down to bare aliases
    here, instead of trusting the possibly-incomplete *typedefs* parameter,
    closes that gap the same way per-side independence closes it in the
    qualified-key-space case, without reopening the bare/qualified
    granularity mismatch :func:`typedef_index_pair` splits on: the
    *comparison* still runs entirely on bare keys.

    **Preserves ambiguity rather than picking a last-wins winner** (Codex
    review, PR #1078, twelfth round): projecting straight into a
    ``dict[str, str]`` and handing it to :func:`legacy_typedef_ir` -- which
    can only ever construct one occurrence per key -- collapsed two
    distinct qualified entities that share a bare leaf name (e.g.
    ``a::Alias`` and a newly-added ``b::Alias``) onto a single bare-string
    assignment, with the later one silently overwriting the earlier. That
    fabricated a spurious ``TYPEDEF_BASE_CHANGED`` for an unchanged
    declaration whenever the discarded entity's value differed from the
    survivor's -- exactly the collision this module's own occurrence-level
    bookkeeping (:func:`diff_typedefs`) already knows how to handle
    correctly, if only it were given the chance. So each real IR entity
    gets its own synthetic bare-projected identity instead of collapsing
    into a shared dict key: a fresh :class:`~abicheck.model.identity.
    Anonymous` scope segment (tagged with a per-alias ordinal purely to
    keep the identities distinct -- these are not real anonymous
    declarations) renders to nothing via
    :func:`~abicheck.model.semantic_ir_legacy_adapter.
    render_display_name_or_leaf`, so every such entity still renders under
    its shared bare *alias*, but as genuinely separate entries
    :func:`_aliases` groups into one colliding list rather than one dict
    key overwriting another.
    """
    if snapshot.semantic_ir is None:
        return SemanticIRIndex(legacy_typedef_ir(snapshot, typedefs))
    ir_index = SemanticIRIndex(snapshot.semantic_ir)
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    covered_aliases: set[str] = set()
    ordinal_by_alias: dict[str, int] = {}
    for occurrence_id in ir_index.ir.occurrences:
        # Iterates raw occurrences, not the reduced `entities_of_kind()` view
        # (Codex review, PR #1078, fifteenth round -- the same fix as
        # `_aliases`/`_underlying`, applied here too: this loop used to read
        # through the reduced one-entry-per-`EntityId` projection, which
        # would have collapsed a genuine ODR-duplicate/multi-TU typedef pair
        # sharing one identity down to a single occurrence's value *and*
        # then fed that reduced `EntityId` straight into `_underlying`,
        # which now takes an `OccurrenceId` and would find nothing for a
        # bare `EntityId` key -- silently reading every bare-projected
        # typedef in this branch as unresolved).
        if occurrence_id.entity_id.kind is not EntityKind.TYPEDEF:
            continue
        alias = render_display_name_or_leaf(occurrence_id.entity_id)
        bare_alias = alias.rsplit("::", 1)[-1]
        covered_aliases.add(bare_alias)
        ordinal = ordinal_by_alias.get(bare_alias, 0)
        ordinal_by_alias[bare_alias] = ordinal + 1
        bare_id = EntityId(
            scope=(Anonymous("bare-typedef-projection", ordinal),),
            kind=EntityKind.TYPEDEF,
            leaf_name=bare_alias,
            extra=SYNTHETIC_IDENTITY_EXTRA,
        )
        occurrences[OccurrenceId(bare_id)] = CanonicalEntity(
            canonical_spelling=Fact.present(_underlying(ir_index, occurrence_id))
        )
    # A bare alias this side's own *typedefs* map carries but the real IR
    # doesn't cover at all still needs representation -- delegate to the
    # legacy adapter's own single-occurrence-per-alias construction for
    # exactly those leftover aliases (there is no collision risk for them,
    # since the caller-supplied bare map is itself already single-valued
    # per key).
    leftover = {k: v for k, v in typedefs.items() if k not in covered_aliases}
    if leftover:
        occurrences.update(legacy_typedef_ir(snapshot, leftover).occurrences)
    return SemanticIRIndex(SemanticIR(occurrences=occurrences))


def typedef_index_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_typedefs: dict[str, str],
    new_typedefs: dict[str, str],
) -> tuple[SemanticIRIndex, SemanticIRIndex]:
    """The typedef cohort's index pair: each side's real ``SemanticIR``
    whenever it has one (ADR-063 Track T3, "typedef/constant authority
    cutover" -- superseding the fidelity gate this function used to run).

    **Before T3:** this function built *both* an IR-backed and a
    legacy-projected index on every comparison, and used the IR only when
    its own rendered display names/values/identities exactly reproduced the
    legacy alias maps this comparison already resolved
    (``_typedef_diff_maps``, in the caller) -- so the legacy projection, not
    the IR, decided the outcome. That was a fidelity *gate*, not an
    authority transfer: an IR that disagreed with the legacy projection was
    never actually trusted, only ever silently routed around.

    **After T3:** each side is decided independently -- :func:`_typedef_
    side_index` reads that side's own real ``SemanticIR`` directly when it
    has one, falling back to the legacy adapter's projection of *that same
    side's own* flat collection only when it has none. No second index is
    ever built for a side that already has a real one, and there is nothing
    left to adjudicate: a snapshot whose ``SemanticIR`` disagrees, by
    identity, with its own ``typedef_entity_ids`` sidecar can no longer
    reach this function at all -- that disagreement is caught earlier, at
    snapshot construction (``AbiSnapshot.__post_init__`` ->
    ``model.semantic_ir_legacy_adapter.assert_typedef_ir_consistent``),
    which raises :class:`~abicheck.errors.SemanticIrAuthorityError` rather
    than leaving this selector to quietly fall back.

    **Deliberately not both-or-neither** (Codex review, PR #1078): an
    earlier version of this cutover gated on *both* sides carrying a real
    IR, falling back to the legacy adapter for *both* sides otherwise --
    reasoning, by analogy with the old fidelity gate, that mixing an
    IR-backed side with an adapted one would compare "two
    differently-derived key spaces". That reasoning does not actually hold
    here: :func:`diff_typedefs` matches by *rendered alias name* (a plain
    string), not by ``EntityId``, so a real-IR-backed index and a
    legacy-adapted one are directly comparable through that shared key
    space regardless of which side is which -- and each is already the most
    faithful representation available for its own side. The both-or-neither
    version actively discarded evidence: comparing a live dump (real
    ``SemanticIR``, from a producer that always populates the flat
    collections identically) against a pre-v38 stored baseline (no
    ``SemanticIR`` at all) forced the live side through its *own* legacy
    adapter too, which is harmless only because that side's flat collection
    happens to agree with its own IR today. A hand-built or future-producer
    snapshot carrying real typedef ``SemanticIR`` occurrences with no
    matching flat collection populated at all is not a hypothetical this
    module should rely on never occurring: under the old both-or-neither
    rule it would have silently read as "this side has zero typedefs",
    fabricating a removal for every typedef the flat collection never
    carried. Deciding per side removes that failure mode entirely, since a
    side's own real ``SemanticIR`` is now always preferred over any
    reconstruction of it, on either side, independently.

    **One exception where per-side independence would itself fabricate a
    change** (Codex review, PR #1078, second round): a real ``SemanticIR``
    always renders under its own fully *qualified* name
    (:func:`~abicheck.model.semantic_ir_legacy_adapter.render_display_name`
    walks the whole ``ScopePath``). ``_typedef_diff_maps`` sometimes
    resolves *bare*-keyed maps instead -- specifically when one side
    predates schema v25's ``typedefs_qualified`` field (which also means it
    predates v38's ``SemanticIR``, so that side is already on the legacy
    path regardless) -- to keep both sides comparable at the coarser
    granularity the older side can express at all
    (``diff_helpers.typedef_side_trusts_qualified``). Deciding the *other*
    side purely per-side would key it under its own real, qualified names
    while the schema-incompatible side is keyed bare, e.g. ``"ns::Alias"``
    on one side against ``"Alias"`` on the other for the identical
    declaration -- a projection mismatch masquerading as a removal, not a
    real one. So the two decisions are layered, not independent of each
    other: this function first asks whether *both* sides trust qualified
    naming (the same predicate `_typedef_diff_maps` uses); only when they do
    does each side separately decide whether to trust its own real
    ``SemanticIR`` or its own legacy projection. When either side does not
    trust qualified naming, both sides render through the legacy adapter
    over their own bare-keyed maps -- each still projected from that side's
    own real ``SemanticIR`` when it has one
    (:func:`_bare_typedef_side_index`, Codex review, PR #1078, seventh
    round), rather than trusting the passed-in *old_typedefs*/*new_typedefs*
    bare maps alone, which a hand-built or future-producer snapshot could
    leave incomplete even with real typedef evidence in ``semantic_ir`` --
    matching what every pre-T3 comparison already did for any snapshot whose
    legacy maps were themselves complete.
    """
    if not (typedef_side_trusts_qualified(old) and typedef_side_trusts_qualified(new)):
        return (
            _bare_typedef_side_index(old, old_typedefs),
            _bare_typedef_side_index(new, new_typedefs),
        )
    return _typedef_side_index(old, old_typedefs), _typedef_side_index(
        new, new_typedefs
    )
