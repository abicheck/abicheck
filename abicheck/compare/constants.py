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

"""The constant detector family, reading through
:class:`~abicheck.model.semantic_ir_index.SemanticIRIndex` (ADR-063 Phase 6B,
cohort 2 -- see ``compare/typedefs.py``'s own docstring for cohort 1 and why
typedefs went first).

**Why constants are the second cohort.** Cohort 1's own reasoning ruled out
records (layout facts the IR does not yet model) and functions (a canonical
signature spelling whose cross-backend agreement is still an open question)
as the next-simplest family, leaving constants: ``extract/
semantic_normalizer.py``'s fourth slice already gave every public constant a
real ``EntityId`` (Phase 2's ``parse_constant_entity_ids()``) plus exactly
one payload fact -- its raw, deliberately-uncanonicalized value text
(``CanonicalEntity.canonical_spelling``, matching ``diff_symbols.
_diff_constants``'s own long-standing raw-string ``!=`` comparison). Like
typedefs, this is a family the IR already covers completely rather than one
whose migration would really be extraction work.

**This module may not read a legacy constant collection.** Not by
convention -- ``scripts/semantic_ir_cutover.py`` enforces it as a real AST
scan (``semantic-ir-cutover`` in the AI-readiness gate), with no allowlist:
this family is freshly migrated, so there is nothing grandfathered to
permit. ``AbiSnapshot.constants``/``constant_entity_ids`` are read exactly
once, inside ``model/semantic_ir_legacy_adapter.py``, which is where a
*projection* of the legacy shape belongs. The comparison-level decisions --
which raw map the pair trusts, and whether a value disagreement is a
fingerprint-comparison artifact rather than a real edit -- stay with the
caller in ``diff_symbols.py`` and arrive here as plain values/an injected
predicate, for the same reason typedefs' surface-filter predicate does: they
are questions about the comparison, not about a constant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId, EntityKind
from ..model.identity_stability import entity_id_is_cross_snapshot_stable
from ..model.occurrence import OccurrenceId
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    legacy_constant_ir,
    producer_entity_id,
    producer_occurrence_disambiguator,
    render_display_name_or_leaf,
    semantic_ir_covers_kind,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot

__all__ = ["constant_index_pair", "diff_constants"]


class _ReliabilityPredicate(Protocol):
    """``diff_default_value_reliability.
    constant_value_fingerprint_comparison_unreliable``'s call shape, with
    both snapshots already closed over by the caller. Injected rather than
    imported so this module states no opinion about cross-generation
    fingerprint reliability -- a comparison-level concern, not a per-constant
    one -- and so a test can substitute one without constructing two full
    ``AbiSnapshot``\\ s. Mirrors ``compare.typedefs._SurfacePredicate``'s own
    injection reasoning."""

    def __call__(self, old_value: str, new_value: str) -> bool: ...


#: Sentinel for "this occurrence's value could not be established" inside a
#: colliding group's ``Counter`` multiset (``diff_constants``, Codex review,
#: PR #1078, ninth round) -- a reserved, unlikely-to-collide string rather
#: than typedefs' bare ``"?"`` (``compare.typedefs._UNRESOLVED_TYPE_
#: SENTINEL``), since a constant's own raw value text is arbitrary source
#: content and a real constant literally spelled ``"?"`` is not implausible
#: the way an underlying-type spelling of ``"?"`` would be.
_UNRESOLVED_MARKER = "\x00<abicheck-constant-unresolved>"


def _unresolved_to_none(value: str) -> str | None:
    """*value* unless it is :data:`_UNRESOLVED_MARKER`, in which case
    ``None`` -- the public shape a ``Change``'s ``old_value``/``new_value``
    already uses for "no recoverable value text", same as a whole-name
    ``CONSTANT_ADDED``/``CONSTANT_REMOVED`` for an unsupported fact."""
    return None if value == _UNRESOLVED_MARKER else value


def _collision_safe_disambiguator(occurrence_id: OccurrenceId) -> str | None:
    """*occurrence_id*'s own producer disambiguator when set, else a
    fallback derived from its own (real, non-synthetic) entity id (Codex
    review, PR #1078, twentieth round).

    Two colliding, entity-distinct occurrences with no real source
    disambiguator -- the common case for two anonymous-scope entities --
    would otherwise both carry ``disambiguator=None`` and collide on
    ``finding_identity.report_finding_id`` even though ``diff_filtering.
    _dedup_exact`` already tells them apart via ``entity_id.key``. Folding
    ``entity_id`` in here rather than directly into ``report_finding_id``
    carries none of that function's backward-compatibility risk:
    ``Change.disambiguator`` is a field this PR introduces (eighteenth
    round), so every value this helper can produce is new, not a rehash of
    an already-shipped id (see ``report_finding_id``'s own docstring for
    the full account of why folding ``entity_id`` in there directly was
    tried and reverted)."""
    disambiguator = producer_occurrence_disambiguator(occurrence_id)
    if disambiguator:
        return disambiguator
    entity_id = producer_entity_id(occurrence_id.entity_id)
    return None if entity_id is None else str(entity_id.key)


def _residual_entity_id(occurrence_id: OccurrenceId | None) -> EntityId | None:
    """*occurrence_id*'s producer entity id, or ``None`` when *occurrence_id*
    itself is ``None`` (an ambiguous residual, see :func:`_attribute_residuals`)."""
    return (
        None if occurrence_id is None else producer_entity_id(occurrence_id.entity_id)
    )


def _residual_disambiguator(occurrence_id: OccurrenceId | None) -> str | None:
    """*occurrence_id*'s collision-safe disambiguator, or ``None`` when
    *occurrence_id* itself is ``None``."""
    return (
        None if occurrence_id is None else _collision_safe_disambiguator(occurrence_id)
    )


def _attribute_residuals(
    ids_for_value: list[OccurrenceId], excess: int
) -> list[OccurrenceId | None]:
    """The *excess* occurrences of one value bucket to report as
    removed/added residuals, each attributed to a real identity only when
    attribution is unambiguous (Codex review, PR #1078, twentieth round).

    When the *entire* bucket vanishes from one side (``excess ==
    len(ids_for_value)``), every occurrence in it really did stop appearing
    with this value -- each one's own identity is genuine evidence, even
    though which physical declaration became what is still unknown.  When
    only *some* of the bucket's occurrences are excess (``excess <
    len(ids_for_value)``), which specific occurrence(s) the excess
    represents is unrecoverable from a bare value match alone: the
    unstable/anonymous identities in ``ids_for_value`` are interchangeable
    given only "N occurrences share this value", so presenting an arbitrary
    list prefix (previously ``ids_for_value[:excess]``) as if it were
    observed attribution could stamp a still-*present* declaration's
    ``entity_id`` onto a finding claiming it vanished. Reporting the
    residual without a specific identity in that case is the honest
    reading of the same evidence.
    """
    if excess == len(ids_for_value):
        return list(ids_for_value)
    return [None] * excess


def _values(index: SemanticIRIndex) -> dict[str, list[OccurrenceId]]:
    """This index's constant occurrences, grouped by their rendered name --
    a *list* per name, not a single winner (Codex review, PR #1078, sixth
    round). Mirrors ``compare.typedefs._aliases`` exactly, including using
    ``render_display_name_or_leaf`` rather than the strict
    ``render_display_name`` (Codex review, PR #1078, fourth round) -- see
    that function's own docstring for why an anonymous/local-to-function-
    scoped constant still needs a name to compare under, and for why a
    collision on that rendered name must keep every occurrence rather than
    ``setdefault``-ing to the first: two distinct anonymous-scoped constants
    sharing a leaf name are still two distinct pieces of evidence, and
    collapsing to one silently discarded whichever occurrence didn't win the
    race. :func:`diff_constants` compares the *set* of values under a
    colliding name rather than a single representative, for the same reason
    :func:`~abicheck.compare.typedefs.diff_typedefs` does.

    **Grouped by raw occurrence, not by the reduced entity view** (Codex
    review, PR #1078, fifteenth round -- mirrors ``compare.typedefs.
    _aliases``'s identical fix; see that function's own docstring for the
    full account): ``SemanticIRIndex.entities_of_kind`` answers off
    ``SemanticIR.canonical_entities()``'s explicit one-entry-per-``EntityId``
    reduction, which would collapse a genuine ODR-duplicate/multi-TU pair of
    constants sharing one ``EntityId`` down to a single "most facts present"
    winner -- exactly the case ``SemanticIR.occurrences`` (keyed by
    :class:`~abicheck.model.occurrence.OccurrenceId`, not bare ``EntityId``)
    exists to keep distinct. Iterating ``index.ir.occurrences`` directly
    instead means such a pair is just another instance of the alias-collision
    this function already groups and :func:`diff_constants` already compares
    by multiset."""
    by_name: dict[str, list[OccurrenceId]] = {}
    for occurrence_id in index.ir.occurrences:
        if occurrence_id.entity_id.kind is not EntityKind.CONSTANT:
            continue
        by_name.setdefault(
            render_display_name_or_leaf(occurrence_id.entity_id), []
        ).append(occurrence_id)
    return by_name


def _value(index: SemanticIRIndex, occurrence_id: OccurrenceId) -> str | None:
    """*occurrence_id*'s value text, or ``None`` when this producer has no
    comparable spelling for it (``Fact.unsupported()`` -- a clang compound-
    initializer fingerprint or Python-bool-derived literal spelling, see
    ``extract/semantic_normalizer.py``'s "Scope of the fourth slice").

    Reads *occurrence_id*'s own ``CanonicalEntity`` directly off
    ``index.ir.occurrences`` -- never ``SemanticIRIndex.fact()``/``.entity()``,
    which answer off the reduced view and would silently collapse two
    genuine ODR-duplicate/multi-TU occurrences sharing one identity onto a
    single winner (Codex review, PR #1078, fifteenth round; see
    :func:`_values`'s own docstring for the full account).

    Unlike a typedef's unresolved-chain placeholder (``"?"``, a real string
    both backends agree on), a constant carries no legacy sentinel for this
    case -- the raw fingerprint text a ``Fact.unsupported()`` occurrence
    would need to compare against is not retained *on the ``Fact`` itself*
    at all. Since ADR-063 Track T3 made ``SemanticIR`` the sole
    comparison-time source for this cohort, ``diff_constants`` genuinely
    reaches this ``None`` for a real clang compound-initializer/bool-literal
    constant -- it is no longer routed around by falling back to the legacy
    adapter's always-a-raw-string projection first. This function's own
    caller, ``diff_constants``, does not simply give up here the way it
    once did: the identical raw text is still available from each
    snapshot's own flat ``AbiSnapshot.constants`` map (the same producer's
    *legacy* declaration parser populates it independently of the
    ``SemanticIR`` normalizer's cross-backend-safety decision), so
    ``diff_constants`` falls back to that map for a *value comparison*
    specifically, gated through the existing
    *is_fingerprint_comparison_unreliable* predicate the same way any other
    fingerprint comparison is (Codex review, PR #1078, second round --
    silently treating "value incomparable" as "value unchanged" is exactly
    what this codebase's "weaker evidence narrows conclusions" principle
    (AGENTS.md) forbids). A membership change
    (``CONSTANT_ADDED``/``CONSTANT_REMOVED``) needs no such fallback to
    fire at all: whether a constant exists does not depend on whether its
    value can be rendered (Codex review, PR #1078, first round) -- the
    fallback there only makes the finding's own old/new value text more
    informative.
    """
    entity = index.ir.occurrences.get(occurrence_id)
    spelling = entity.canonical_spelling if entity is not None else None
    if spelling is not None and spelling.is_present and spelling.value is not None:
        value = spelling.value
        assert isinstance(value, str)
        return value
    return None


def diff_constants(
    old_index: SemanticIRIndex,
    new_index: SemanticIRIndex,
    *,
    is_fingerprint_comparison_unreliable: _ReliabilityPredicate,
    old_constants: dict[str, str],
    new_constants: dict[str, str],
) -> list[Change]:
    """Detect constant additions, removals, and value changes, reading only
    through the two indexes -- plus, for a value comparison specifically,
    each snapshot's own flat ``AbiSnapshot.constants`` map as a same-backend
    fallback when the canonical ``SemanticIR`` value is unsupported.

    An addition or removal fires regardless of whether the constant's own
    value can be rendered (``_value`` returning ``None`` for a
    ``Fact.unsupported()`` occurrence) -- only a value-*comparison*
    (``CONSTANT_CHANGED``) requires both sides' values to actually be
    comparable text (Codex review, PR #1078: this used to skip the
    membership check too for an unsupported old-side value, silently
    dropping a real removal).

    **The value comparison itself falls back to *constants* when
    ``_value`` returns ``None``** (Codex review, PR #1078, second round):
    ``Fact.unsupported()`` means the canonical ``SemanticIR`` spelling is
    not a *cross-backend*-comparable value (a clang compound-initializer
    fingerprint or Python-bool-derived literal spelling, see
    ``extract/semantic_normalizer.py``) -- it does not mean the raw text is
    unavailable. The same producer's *legacy* declaration parser
    (``dumper_clang.py``'s own ``parse_constants()``) still populates
    ``AbiSnapshot.constants`` with that identical raw text, independently
    of the ``SemanticIR`` normalizer's cross-backend-safety decision, and a
    *same-run, same-backend* comparison of two such fingerprints is exactly
    what *is_fingerprint_comparison_unreliable* already exists to gate
    (``diff_default_value_reliability.
    constant_value_fingerprint_comparison_unreliable``) -- this function
    already checked that predicate before this fix, it simply had nothing
    to check it against once the canonical value went missing. Without this
    fallback, a real edit to a compound initializer or a `constexpr bool`
    aliased to a `True`/`False`-named identifier between two same-backend
    header snapshots produced no finding at all, even though the pre-T3
    legacy-only path (always ``Fact.present(value)``, never ``unsupported``)
    caught it. Membership (``CONSTANT_ADDED``/``CONSTANT_REMOVED``) reports
    the same fallback value too, purely for a more informative
    old_value/new_value -- it never gates *whether* that finding fires.

    *is_fingerprint_comparison_unreliable* is the comparison-level decision
    the caller already makes, closed over both snapshots -- injected for
    the same reason ``diff_typedefs``'s own ``is_non_abi_surface_type`` is.

    Behavior is identical to the pre-cutover ``diff_symbols._diff_constants``,
    including which two spellings a ``CONSTANT_CHANGED`` finding carries
    (``old``/``new`` as ``repr()`` text alongside ``old_value``/``new_value``
    as the raw strings).

    **A colliding name is compared by its whole value multiset, not one
    representative** (Codex review, PR #1078, sixth round): ``_values``
    groups every entity that renders to the same name, since two distinct
    anonymous-scoped constants can share one leaf name. Picking an arbitrary
    representative per side could miss a real value change on whichever
    occurrence didn't become the representative, silently reporting no
    change at all -- mirrors ``compare.typedefs.diff_typedefs``'s own fix
    for the identical failure mode.

    **The multiset comparison alone was not enough** (Codex review, PR
    #1078, eighth round): filtering out unsupported (``None``) values
    *before* comparing the multiset silently discarded a genuine membership
    change inside a colliding group whenever the discarded occurrence's
    absence didn't change the *filtered* list -- e.g. one comparable value
    (``"1"``) plus one unsupported occurrence on the old side, the same
    comparable value alone on the new side: both sides' filtered multisets
    read ``["1"]``, so the group's own shrinking from two occurrences to one
    was invisible. This function now also compares each side's raw
    occurrence *count*, independent of value comparability, and reports a
    change (with no recoverable value text) whenever the counts disagree
    even though the filtered values agree -- the same "which occurrence
    changed remains ambiguous" acceptance every other colliding-group case
    here already carries, just extended to a membership change instead of a
    value change.

    **Several further gaps in the collision path itself**, across three
    Codex review rounds (PR #1078, ninth/tenth/eleventh):

    1. (Ninth round) A colliding group that grew (or shrank) by a value
       *already present* in the group (e.g. a second anonymous-namespace
       ``X=1`` alongside an existing ``X=1``) has sorted lists of different
       length that a naive representative pick could still read as a value
       *change* -- reporting ``CONSTANT_CHANGED`` (an API break) for what
       is a purely compatible addition. Fixed (initially via
       ``collections.Counter`` subtraction, later folded into the
       occurrence-level bookkeeping below) so a pure addition/removal
       inside the group is classified as ``CONSTANT_ADDED``/
       ``CONSTANT_REMOVED``, and only a group with both a net addition and
       a net removal is reported as ``CONSTANT_CHANGED``.
    2. (Ninth round) The per-name legacy fallback (``constants.get(name)``)
       reflects only *one* raw value per bare name -- whichever
       occurrence's own parse happened to win that same collision upstream
       -- so applying it to *every* unresolved occurrence in a colliding
       group risked misattributing one occurrence's legacy text to a
       different occurrence. The collision path never consults the
       fallback at all -- an unresolved occurrence inside a colliding group
       is represented by an internal sentinel, never a borrowed value.
    3. (Tenth round) A *mixed* group (both a net removal and a net addition
       at once, e.g. a stable ``X=1`` becoming ``X=2`` while a different,
       newly-added anonymous-scope ``X=3`` also appears) used to pick one
       representative pair and emit a single ``CONSTANT_CHANGED``, silently
       dropping the independently provable residual ``CONSTANT_ADDED``/
       ``CONSTANT_REMOVED``. Fixed by pairing off exactly one removed value
       with one added value as that one ``CONSTANT_CHANGED``, then
       reporting every other leftover value as its own finding.
    4. (Eleventh round) The ninth/tenth rounds' own fix converted a
       ``Counter`` difference to a ``set`` for iteration, which introduced
       two further defects: iteration order (and therefore which colliding
       value became the ``CONSTANT_CHANGED`` pairing, and therefore the
       *outcome* of the ``is_fingerprint_comparison_unreliable`` gate on
       it) depended on ``PYTHONHASHSEED``, so the identical comparison
       could alternate between passing and failing across runs; and
       converting to a ``set`` collapsed repeated values to one entry,
       silently dropping every additional identical removal/addition
       beyond the first (three colliding ``X=1`` occurrences shrinking to
       one must report the loss of *two* occurrences, not one). Fixed by
       replacing the ``Counter``/``set`` machinery entirely with
       occurrence-level bookkeeping (``old_by_value``/``new_by_value``,
       plain ``dict``s grouping each side's own entities by value in
       insertion order) -- deterministic regardless of hash seed, exact on
       multiplicity, and each removed/added occurrence keeps its own real
       entity_id rather than every finding for the name sharing a single
       id computed once from ``old_ids[0]``/``new_ids[0]`` (a third,
       related eleventh-round finding: that shared id misattributed a
       residual finding to whichever entity happened to occupy that
       position, not the occurrence that actually changed).
    """
    changes: list[Change] = []
    old_values = _values(old_index)
    new_values = _values(new_index)

    def _value_or_legacy(
        index: SemanticIRIndex,
        occurrence_id: OccurrenceId,
        name: str,
        constants: dict[str, str],
    ) -> str | None:
        value = _value(index, occurrence_id)
        return value if value is not None else constants.get(name)

    for name, old_ids in old_values.items():
        new_ids = new_values.get(name)
        if new_ids is None:
            # A membership change (removed) is real regardless of whether
            # this constant's own value was ever comparable -- checked
            # before the `old_val is None` unsupported-value skip below, so
            # a clang compound-initializer/bool-literal constant (or any
            # future Fact.unsupported() producer) still reports its
            # removal, just with no recoverable old_value text (Codex
            # review: this used to `continue` here before ever reaching the
            # membership check, silently dropping the removal).
            #
            # One `CONSTANT_REMOVED` per contributing entity, not just
            # `old_ids[0]` (Codex review, PR #1078, twelfth round): when
            # the whole colliding group vanishes -- not merely shrinks --
            # every one of its distinct entities is an independent, real
            # removal, and each carries its own entity_id rather than
            # every finding sharing a single id.
            #
            # The per-name legacy fallback is used only when there is
            # exactly one entity to attribute it to (Codex review, PR
            # #1078, thirteenth round): `old_constants.get(name)` retains
            # only one raw value per bare name, so applying it to *every*
            # member of a multi-entity group vanishing at once would credit
            # the same borrowed text to every one of them, same as the
            # ninth round's identical reasoning for the general collision
            # path -- this whole-group path just hadn't been given the
            # same treatment yet.
            for old_id in old_ids:
                old_value = (
                    _value_or_legacy(old_index, old_id, name, old_constants)
                    if len(old_ids) == 1
                    else _value(old_index, old_id)
                )
                changes.append(
                    make_change(
                        ChangeKind.CONSTANT_REMOVED,
                        symbol=name,
                        name=name,
                        old_value=old_value,
                        entity_id=producer_entity_id(old_id.entity_id),
                        disambiguator=_collision_safe_disambiguator(old_id),
                    )
                )
            continue
        if len(old_ids) == 1 and len(new_ids) == 1:
            # The common, non-colliding case: preserved exactly as before
            # any collision handling existed.
            old_val = _value_or_legacy(old_index, old_ids[0], name, old_constants)
            new_val = _value_or_legacy(new_index, new_ids[0], name, new_constants)
            if old_val is None or new_val is None or new_val == old_val:
                continue
            if is_fingerprint_comparison_unreliable(old_val, new_val):
                continue
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_CHANGED,
                    symbol=name,
                    name=name,
                    old=repr(old_val),
                    new=repr(new_val),
                    old_value=old_val,
                    new_value=new_val,
                    entity_id=producer_entity_id(old_ids[0].entity_id)
                    or producer_entity_id(new_ids[0].entity_id),
                    disambiguator=(
                        producer_occurrence_disambiguator(old_ids[0])
                        if producer_entity_id(old_ids[0].entity_id) is not None
                        else producer_occurrence_disambiguator(new_ids[0])
                    ),
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
        # find a same-valued partner elsewhere in the group -- e.g. a
        # stable `X` changing `1` -> `2` while a *different*, newly-added
        # colliding entity is `1`: value-only subtraction cancels the
        # stable entity's old `1` against the new entity's `1`, reporting
        # only a compatible-looking addition of `2` instead of the real
        # breaking change to the stable entity, and losing the genuine
        # addition entirely. `set(old_ids) & set(new_ids)` is exact, not a
        # heuristic, *for a stable identity*: `_values()` never repeats an
        # `EntityId` within one side, so each shared id names exactly one
        # occurrence per side -- but an `Anonymous`/`LocalToFunction` scope
        # segment's own ordinal is explicitly not stable across two
        # snapshots (``model.identity_stability``'s own docstring:
        # inserting an earlier anonymous sibling shifts every later one's
        # ordinal, and therefore its whole `EntityId`, even though nothing
        # about that later declaration changed). Trusting a raw
        # intersection here would risk pairing two genuinely unrelated
        # declarations that happen to collide on a shifted ordinal plus the
        # same bare name -- fabricating a `CONSTANT_CHANGED` for what is
        # really just an unrelated addition (Codex review, PR #1078,
        # fourteenth round). Gated through
        # :func:`~abicheck.model.identity_stability.
        # entity_id_is_cross_snapshot_stable` -- this collision path is
        # exactly the "real consumer" that predicate's own docstring says
        # needs its own adversarial review before being wired in, so this
        # is the first real call site.
        # No per-name legacy fallback here either, for the identical
        # ninth-round reason the rest of this path avoids it.
        shared_id_set = {
            i
            for i in set(old_ids) & set(new_ids)
            if entity_id_is_cross_snapshot_stable(i.entity_id)
        }
        # Iterated in `old_ids`'s own order, not `shared_id_set`'s (Codex
        # review, PR #1078, twentieth round): a `set` has no defined
        # iteration order, so when more than one stable shared entity in
        # one colliding group each independently emits a `CONSTANT_CHANGED`,
        # their relative order in the report varied with `PYTHONHASHSEED`
        # for two runs over byte-identical input -- nothing downstream
        # re-sorts findings to correct for it. `old_ids` and `new_ids` name
        # the same shared identity in the same relative position on both
        # sides (`_values()` groups each side in encounter order and a
        # shared id's position doesn't move relative to its own side's
        # other entries just because a differently-ordered side changed),
        # so ordering by `old_ids` is deterministic and arbitrary-only in
        # the same sense the collection's own encounter order already is.
        shared_ids = [i for i in old_ids if i in shared_id_set]
        for shared_id in shared_ids:
            old_val = _value(old_index, shared_id)
            new_val = _value(new_index, shared_id)
            if old_val is None or new_val is None or old_val == new_val:
                continue
            if is_fingerprint_comparison_unreliable(old_val, new_val):
                continue
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_CHANGED,
                    symbol=name,
                    name=name,
                    old=repr(old_val),
                    new=repr(new_val),
                    old_value=old_val,
                    new_value=new_val,
                    entity_id=producer_entity_id(shared_id.entity_id),
                    disambiguator=_collision_safe_disambiguator(shared_id),
                )
            )
        # A colliding group on at least one side (Codex review, PR #1078,
        # ninth/tenth/eleventh rounds). Compared occurrence-by-occurrence,
        # not by a bare value `Counter`: a `Counter` alone answers "how many
        # of each value" but cannot say *which entity* carried a specific
        # removed or added occurrence, and converting its difference to a
        # `set` for iteration (an earlier version of this fix) made both
        # the choice of which colliding value pairs into one
        # `CONSTANT_CHANGED` and, through it, the `is_fingerprint_
        # comparison_unreliable` verdict depend on `PYTHONHASHSEED` (string
        # hashing) -- the identical comparison could alternate between
        # passing and failing across runs (eleventh round). `set` also
        # collapsed repeated values to one entry, silently dropping every
        # additional identical removal/addition beyond the first
        # (eleventh round's second finding: three colliding `X=1`
        # occurrences shrinking to one must report the loss of *two*
        # occurrences, not one).
        #
        # `old_by_value`/`new_by_value` group each side's own entities by
        # value in insertion order (a plain ``dict``, not a ``set`` --
        # deterministic regardless of hash seed, and matching the same
        # `SemanticIR`'s own occurrence order every run). For each value,
        # the excess count on one side over the other is exactly that many
        # removed/added *occurrences*, each still carrying its own real
        # entity_id -- fixing the third finding too (an emitted residual
        # finding used to be stamped with a single id shared across every
        # finding for the name, misattributing it to whichever entity
        # happened to be `old_ids[0]`/`new_ids[0]`, not the occurrence that
        # actually changed).
        #
        # This path also does not use `_value_or_legacy`'s per-name
        # fallback at all: `AbiSnapshot.constants` retains only ONE raw
        # value per bare name -- whichever occurrence's parse happened to
        # win that same collision upstream -- so applying it to *every*
        # unresolved occurrence in a colliding group would misattribute one
        # occurrence's legacy text to a different occurrence entirely,
        # potentially making two genuinely different unresolvable
        # occurrences look identical (a false "unchanged") or crediting an
        # unrelated occurrence's edit to one that didn't change. Reading
        # `_value` directly and falling back to the `_UNRESOLVED_MARKER`
        # sentinel is deliberately less informative than the single-entity
        # fallback, but never fabricates a per-occurrence value this
        # function cannot actually attribute.
        old_by_value: dict[str, list[OccurrenceId]] = {}
        for i in old_ids:
            if i in shared_id_set:
                continue
            v = _value(old_index, i)
            old_by_value.setdefault(
                v if v is not None else _UNRESOLVED_MARKER, []
            ).append(i)
        new_by_value: dict[str, list[OccurrenceId]] = {}
        for i in new_ids:
            if i in shared_id_set:
                continue
            v = _value(new_index, i)
            new_by_value.setdefault(
                v if v is not None else _UNRESOLVED_MARKER, []
            ).append(i)
        removed_occurrences: list[tuple[str, OccurrenceId | None]] = []
        for value, ids_for_value in old_by_value.items():
            excess = len(ids_for_value) - len(new_by_value.get(value, ()))
            if excess > 0:
                removed_occurrences.extend(
                    (value, i) for i in _attribute_residuals(ids_for_value, excess)
                )
        added_occurrences: list[tuple[str, OccurrenceId | None]] = []
        for value, ids_for_value in new_by_value.items():
            excess = len(ids_for_value) - len(old_by_value.get(value, ()))
            if excess > 0:
                added_occurrences.extend(
                    (value, i) for i in _attribute_residuals(ids_for_value, excess)
                )
        if not removed_occurrences and not added_occurrences:
            continue
        # A mixed group (both a net removal and a net addition) carries more
        # than one independent piece of evidence -- pairing off exactly one
        # removed occurrence with one added occurrence as a single "value
        # changed" story, then reporting whatever is *left over* as its own
        # `CONSTANT_REMOVED`/`CONSTANT_ADDED`, rather than collapsing every
        # mixed difference into one `CONSTANT_CHANGED` that silently drops
        # the rest (tenth round: e.g. a stable-identity `X=1` becoming
        # `X=2` while a *different*, newly-added anonymous-scope `X=3` also
        # appears -- previously reported only one `CONSTANT_CHANGED` and
        # silently lost the independently provable `CONSTANT_ADDED`).
        # Every removed/added pair still available after the shared-identity
        # pass is an independent substitution story, not just the first one
        # (Codex review, PR #1078, twentieth round): pairing only once and
        # letting every further residual fall through to the leftover loops
        # below reported `X=[1,2]` -> `X=[3,4]` (equal cardinality, two
        # substitutions) as one `CONSTANT_CHANGED` plus a fabricated
        # `CONSTANT_REMOVED`/`CONSTANT_ADDED` pair, instead of two
        # `CONSTANT_CHANGED`s. Pairing off as many removed/added occurrences
        # as both sides have in common is the direct generalization of the
        # tenth round's own one-pair fix -- exactly the excess beyond
        # `min(len(removed), len(added))` is real leftover evidence, and no
        # less.
        while removed_occurrences and added_occurrences:
            # Prefers a pair with resolved value evidence on both sides
            # (Codex review, PR #1078, nineteenth round) over always taking
            # position 0: an unresolved occurrence (`_UNRESOLVED_MARKER`)
            # can occupy the first slot on either side, which would demote
            # a genuinely comparable removed/added pair sitting right next
            # to it -- both to no recoverable value text (`old`/`new`
            # rendering as `repr(None)`) and out of
            # `is_fingerprint_comparison_unreliable`'s own reach (it
            # requires both values to be non-`None`). Falls back to
            # position 0 when no resolved pair exists, unchanged from
            # before. Re-evaluated fresh each iteration since both lists
            # shrink as pairs are consumed.
            removed_pos = next(
                (
                    i
                    for i, (value, _) in enumerate(removed_occurrences)
                    if value != _UNRESOLVED_MARKER
                ),
                0,
            )
            added_pos = next(
                (
                    i
                    for i, (value, _) in enumerate(added_occurrences)
                    if value != _UNRESOLVED_MARKER
                ),
                0,
            )
            removed_value, removed_id = removed_occurrences.pop(removed_pos)
            added_value, added_id = added_occurrences.pop(added_pos)
            old_val = _unresolved_to_none(removed_value)
            new_val = _unresolved_to_none(added_value)
            unreliable = (
                old_val is not None
                and new_val is not None
                and is_fingerprint_comparison_unreliable(old_val, new_val)
            )
            if not unreliable:
                changes.append(
                    make_change(
                        ChangeKind.CONSTANT_CHANGED,
                        symbol=name,
                        name=name,
                        old=repr(old_val),
                        new=repr(new_val),
                        old_value=old_val,
                        new_value=new_val,
                        entity_id=_residual_entity_id(removed_id)
                        or _residual_entity_id(added_id),
                        disambiguator=(
                            _residual_disambiguator(removed_id)
                            if _residual_entity_id(removed_id) is not None
                            else _residual_disambiguator(added_id)
                        ),
                    )
                )
        for leftover_old_value, leftover_old_id in removed_occurrences:
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_REMOVED,
                    symbol=name,
                    name=name,
                    old_value=_unresolved_to_none(leftover_old_value),
                    entity_id=_residual_entity_id(leftover_old_id),
                    disambiguator=_residual_disambiguator(leftover_old_id),
                )
            )
        for leftover_new_value, leftover_new_id in added_occurrences:
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_ADDED,
                    symbol=name,
                    name=name,
                    new_value=_unresolved_to_none(leftover_new_value),
                    entity_id=_residual_entity_id(leftover_new_id),
                    disambiguator=_residual_disambiguator(leftover_new_id),
                )
            )

    for name, new_ids in new_values.items():
        if name in old_values:
            continue
        # Mirrors the removal side above: an addition is real regardless of
        # whether the new value is itself comparable. One `CONSTANT_ADDED`
        # per contributing entity, not just `new_ids[0]` (Codex review, PR
        # #1078, twelfth round): an entirely new colliding group can carry
        # more than one distinct entity, each an independent addition. The
        # per-name legacy fallback is used only for a single-entity group,
        # for the identical reason the removal side above is (Codex
        # review, PR #1078, thirteenth round).
        for new_id in new_ids:
            new_value = (
                _value_or_legacy(new_index, new_id, name, new_constants)
                if len(new_ids) == 1
                else _value(new_index, new_id)
            )
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_ADDED,
                    symbol=name,
                    name=name,
                    new_value=new_value,
                    entity_id=producer_entity_id(new_id.entity_id),
                    disambiguator=_collision_safe_disambiguator(new_id),
                )
            )
    return changes


def _constant_side_index(
    snapshot: AbiSnapshot, constants: dict[str, str]
) -> SemanticIRIndex:
    """One side's index -- mirrors ``compare.typedefs._typedef_side_index``
    exactly; see that function's own docstring, including the
    :func:`~abicheck.model.semantic_ir_legacy_adapter.semantic_ir_covers_kind`
    gate (Codex review, PR #1078, nineteenth round)."""
    if snapshot.semantic_ir is not None and semantic_ir_covers_kind(
        snapshot.semantic_ir, EntityKind.CONSTANT
    ):
        return SemanticIRIndex(snapshot.semantic_ir)
    return SemanticIRIndex(legacy_constant_ir(snapshot, constants))


def constant_index_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_constants: dict[str, str],
    new_constants: dict[str, str],
) -> tuple[SemanticIRIndex, SemanticIRIndex]:
    """The constant cohort's index pair: each side's real ``SemanticIR``
    whenever it has one (ADR-063 Track T3, "typedef/constant authority
    cutover").

    Mirrors ``compare.typedefs.typedef_index_pair`` exactly, substituting
    the constant collections, ``EntityKind.CONSTANT``, and
    :func:`~abicheck.model.semantic_ir_legacy_adapter.assert_constant_ir_consistent`
    as the construction-time identity check -- see that function's own
    docstring for the full before/after reasoning, including why each side
    is decided independently rather than both-or-neither (Codex review,
    PR #1078). Nothing about the shape differs between the two families --
    only which legacy collections and which entity kind are being
    projected.
    """
    return (
        _constant_side_index(old, old_constants),
        _constant_side_index(new, new_constants),
    )
