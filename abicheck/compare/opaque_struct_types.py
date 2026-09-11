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

"""``find_opaque_struct_types`` -- the DWARF-oriented, asymmetric-existence
opaque-struct matching criterion (ADR-063 Phase 10), split out of
``opaque_types.py`` into its own sibling leaf module (Codex review, PR
#1218, round 10) once the whole-migration additions to that file pushed it
past ``compare/``'s 800-line new-file production cap
(``scripts/check_architecture.py``'s ``new-file-size`` check). This mirrors
the established pattern for a `compare/` submodule split off a capped
sibling -- see `base_class_diff.py`/`va_list_diff.py`/
`qualified_name_normalization.py`'s own docstrings and `compare/AGENTS.md`'s
module list -- rather than growing either the already-over-cap
``opaque_types.py`` or the zero-slack, ``no_growth``-pinned
``diff_filtering.py`` call site.

Depends on ``opaque_types.py`` (``OpaqueTypeIndex``/
``_type_is_by_value_referenced``), not the reverse -- this module is a
consumer of the shared index type and occurrence-matching primitive, not a
second home for either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.identity_tiers import StableEntityId, stable_entity_id
from .opaque_types import OpaqueTypeIndex, _type_is_by_value_referenced

if TYPE_CHECKING:
    from ..model import AbiSnapshot, RecordType

__all__ = ["find_opaque_struct_types"]


def find_opaque_struct_types(old: AbiSnapshot, new: AbiSnapshot) -> OpaqueTypeIndex:
    """The DWARF-oriented, asymmetric-existence sibling of
    :func:`~abicheck.compare.opaque_types.find_opaque_types` --
    ``diff_filtering._downgrade_opaque_struct_changes``'s own opaqueness
    criterion, moved to ``compare/`` (ADR-063 Phase 10) for the identical
    reason :func:`~abicheck.compare.opaque_types.find_opaque_types`/
    :func:`~abicheck.compare.opaque_types.find_by_value_types` already sit
    there rather than in ``diff_filtering.py``: a matching concern belongs
    at its ADR-061 owner, and ``diff_filtering.py`` sits on a zero-slack
    ``debt.yaml`` no-growth pin that this function's own logic (previously
    inline there) would otherwise have exceeded.

    A type is "opaque to consumers" under this function's criterion when:

    * it is ``is_opaque`` on *both* sides, OR
    * it is ``is_opaque`` on one side and entirely absent from the other
      side's header-level type list (the DWARF-only-definition case: a
      forward-declaration-only header paired with a snapshot that only saw
      the type's full DWARF definition on the other side of the release)

    and it is not embedded by value in any non-opaque record on either side.
    Returns the empty index when nothing in *old*/*new* is opaque under this
    criterion.

    Deliberately does **not** reuse
    :func:`~abicheck.compare.opaque_types.find_opaque_types`'s own
    per-snapshot ``is_opaque``-or-``is_impl_source`` criterion or its
    ``intersect()`` -- this function's asymmetric-existence rule (item 2
    above) has no per-snapshot analogue to intersect two of, and mixing the
    two criteria into one function would make either one harder to verify
    against its own, independently-reviewed test suite. The two
    opaque-suppression paths staying textually separate, each producing its
    own :class:`OpaqueTypeIndex`, is the status quo this migration
    preserves -- only *how* each already-computed name set answers "is this
    ``Change`` about one of them" changed, not whether there are two paths.

    ``opaque_types``/``embedded_types``/``truly_opaque`` stay plain
    ``set[str]`` throughout -- both the asymmetric-existence check and the
    by-value-embedding check are genuine rendered-text/bare-name-set
    questions, not identity ones, mirroring why
    :func:`~abicheck.compare.opaque_types.find_by_value_types`/
    ``_type_is_by_value_referenced`` stay spelling-based too. Only the
    *result* -- the final ``truly_opaque`` name set -- is re-expressed as a
    two-tier index, via :meth:`OpaqueTypeIndex.build`, so a caller's own
    ``Change`` membership test can consult a stable, cross-snapshot
    ``EntityId`` first and the bare spelling second (never narrowing:
    this index is not the product of a paired ``intersect()``, so it has
    no completeness proof to license ``contains(..., strict=True)``).
    """
    old_opaque = {t.name for t in old.types if t.is_opaque}
    new_opaque = {t.name for t in new.types if t.is_opaque}
    old_type_names = {t.name for t in old.types}
    new_type_names = {t.name for t in new.types}

    opaque_types = (old_opaque & new_opaque) | (
        (old_opaque - new_type_names) | (new_opaque - old_type_names)
    )
    if not opaque_types:
        return OpaqueTypeIndex(stable=frozenset(), local=frozenset())

    # Matched via :func:`_type_is_by_value_referenced` (the same
    # qualification-robust helper :func:`find_by_value_types` already uses
    # for function/variable signatures), not a bare
    # ``f.type.rstrip(" *&") in opaque_types`` string comparison -- a field
    # rendered as ``ns::Handle`` against a candidate whose own
    # ``RecordType.name`` is bare ``"Handle"`` (or vice versa) would
    # otherwise miss the embedding exclusion entirely (Codex review, PR
    # #1218, round 3): "Handle" would stay in ``truly_opaque`` despite being
    # embedded by value, and the stable tier -- which does not depend on
    # this same spelling coincidence the way the pre-migration bare
    # ``c.symbol`` comparison did -- would then downgrade a real, qualified
    # layout-change finding for the embedded type.
    #
    # A round-5 review comment proposed narrowing a *bare* candidate's own
    # match to the ``::``-excluding matcher (rejecting a match immediately
    # preceded by ``::``), to stop an unrelated ``other::Handle`` field from
    # wrongly excluding a bare ``"Handle"`` candidate as embedded. That
    # narrowing was tried and reverted: it is textually indistinguishable
    # from the round-3 case it would break -- a field rendered ``ns::Handle``
    # against the *same* bare-named opaque candidate is not distinguishable,
    # by string matching alone, from an unrelated ``other::Handle`` -- and
    # the two failure directions are not symmetric. Under-matching here
    # (round 5's direction) risks *silently suppressing* a genuine,
    # consumer-visible layout break (the round-3 false negative this
    # migration exists to close); over-matching (today's behavior) risks
    # only an extra, spurious non-suppression -- a struct-size finding a
    # human can still see and dismiss. AGENTS.md's "weaker evidence narrows
    # conclusions... never upgrades to a clean compatibility claim" already
    # states the correct default for this exact ambiguity: prefer not
    # suppressing when the two directions cannot be told apart.
    non_opaque_old = [t for t in old.types if not t.is_opaque]
    non_opaque_new = [t for t in new.types if not t.is_opaque]
    embedded_types: set[str] = set()
    for records in (non_opaque_old, non_opaque_new):
        for t in records:
            for f in t.fields:
                for tname in opaque_types - embedded_types:
                    if _type_is_by_value_referenced(tname, f.type):
                        embedded_types.add(tname)

    truly_opaque = opaque_types - embedded_types
    if not truly_opaque:
        return OpaqueTypeIndex(stable=frozenset(), local=frozenset())

    # Only a declaration that itself satisfies ``is_opaque`` may contribute
    # to the local (bare-spelling) tier here -- the pre-migration
    # `c.symbol in truly_opaque` fallback only ever compared against opaque
    # declarations' own names, so a non-opaque namesake must not join
    # `declarations` even for the always-safe spelling tier.
    declarations: dict[str, list[RecordType]] = {}
    for snap in (old, new):
        for t in snap.types:
            if t.name in truly_opaque and t.is_opaque:
                declarations.setdefault(t.name, []).append(t)

    # The *stable* tier needs a stricter, identity-paired check than "any
    # opaque declaration under a qualifying spelling" -- two rounds of Codex
    # review on PR #1218 found that grouping by bare name alone lets a
    # stable id ride along on a coincidence rather than actual opacity:
    #
    # 1. An opaque ``ns::Handle`` and an unrelated, *visible* ``other::
    #    Handle`` collide on the bare spelling "Handle" -- adding every
    #    same-named declaration's id (not just the opaque one's) would
    #    smuggle the visible declaration's own stable id into the index.
    # 2. Even restricted to `is_opaque` declarations, ``old_opaque &
    #    new_opaque`` only proves *some* declaration named "Handle" is
    #    opaque on *each* side -- not that it is the *same* declaration.
    #    If ``ns::Handle`` is opaque in old but goes visible in new, while
    #    an unrelated ``other::Handle`` is opaque only in new, "Handle"
    #    still lands in `truly_opaque` via the intersection, and
    #    ``ns::Handle``'s own old-side stable id would wrongly enter the
    #    index even though *that specific entity* is not opaque on both
    #    sides at all.
    #
    # Resolving per stable EntityId across both snapshots (independent of
    # bare-name grouping) closes both: an id may enter the stable tier only
    # when the *same* entity is confirmed opaque under this function's own
    # criterion --- opaque on both sides, or genuinely absent (by identity,
    # not by name) from the other snapshot entirely.
    # Lists, not a last-write-wins single ``RecordType`` per id (Codex
    # review, PR #1218, round 11): a snapshot can legitimately carry more
    # than one declaration resolving to the same ``StableEntityId`` --
    # ``model/semantic_ir.py``'s own ``SemanticIR.occurrences`` deliberately
    # never collapses an ODR-duplicate pair, and a header-AST TU-merge that
    # cannot reconcile two declarations of one entity (``tu_merge.
    # _merge_group``) preserves both rather than picking one. Such a pair
    # can disagree on ``is_opaque`` (one TU only forward-declared it,
    # another TU saw the full definition), so overwriting one declaration
    # with another under the same dict key made this criterion's own
    # opacity verdict depend on iteration/insertion order -- entirely
    # incidental, not the two-sided-opacity criterion this function claims
    # to compute. Every declaration under an id is now consulted; see
    # below.
    old_by_stable_id: dict[StableEntityId, list[RecordType]] = {}
    for t in old.types:
        resolved = stable_entity_id(t.entity_id)
        if resolved is not None:
            old_by_stable_id.setdefault(resolved, []).append(t)
    new_by_stable_id: dict[StableEntityId, list[RecordType]] = {}
    for t in new.types:
        resolved = stable_entity_id(t.entity_id)
        if resolved is not None:
            new_by_stable_id.setdefault(resolved, []).append(t)

    # Every spelling (``name`` *and*, when present, ``qualified_name``) a
    # declaration on each side is known by -- used only by the stable
    # tier's own absence check just below, never by ``opaque_types``'s
    # bare-name criterion above (that computation stays deliberately
    # spelling-based/lossy per this function's own docstring; only the
    # stable tier's identity-adjacent absence check needs the stricter
    # multi-spelling view). A header-AST backend commonly stores the bare
    # leaf in ``name`` and the real scoped spelling in ``qualified_name``
    # (the same asymmetry ``_resolve_struct_change_entity_id``'s own
    # round-9 fix closed for the sibling identity bridge) -- checking
    # ``name`` alone here would misread a genuinely-present but
    # identity-less counterpart spelled only via ``qualified_name`` as
    # absent, letting an old opaque declaration's stable id enter the
    # index and then get borrowed by that same counterpart's own
    # ``qualified_name`` (Codex review, PR #1218, round 10).
    old_all_spellings = old_type_names | {
        t.qualified_name for t in old.types if t.qualified_name is not None
    }
    new_all_spellings = new_type_names | {
        t.qualified_name for t in new.types if t.qualified_name is not None
    }

    # Check the stable counterpart BY ID first, before ever consulting bare
    # names -- two producers can render the identical entity under two
    # different spellings (this whole module's own premise: "the header
    # backends key RecordType.name bare while DWARF bakes the namespace
    # into name"), so a name-level absence check run first would misread a
    # same-entity, differently-spelled counterpart as genuinely absent
    # (Codex review, PR #1218, round 4): old's opaque bare "Handle" and
    # new's now-visible "ns::Handle" can share one stable id despite never
    # sharing a name at all. Only once the id itself is confirmed absent
    # from ``other_by_id`` does bare-name presence decide anything -- and
    # even then, a resolved id absent from ``other_by_id`` is not, by
    # itself, proof the entity is absent from the other snapshot: a
    # mixed-producer or pre-identity-baseline comparison can leave the
    # other side's matching declaration present but carrying no resolvable
    # ``entity_id`` at all (Codex review, PR #1218, round 3) -- missing
    # identity *evidence* is not evidence of *absence*. The
    # asymmetric-existence criterion this function implements is a
    # NAME-level one in the first place (see ``opaque_types`` above, built
    # from ``old_type_names``/``new_type_names``), so falling back to that
    # same bare-name presence check only once identity itself is silent is
    # not a new criterion, only a faithful re-check of the one already used
    # to decide this name belongs in ``truly_opaque`` at all.
    def _unresolved_visible_duplicate(spellings: set[str]) -> bool:
        """Whether either snapshot holds a *visible* declaration under one
        of *spellings* that carries no resolvable stable identity at all
        (Codex review, PR #1218, round 12).

        A declaration that resolves to a *different*, positively-identified
        id sharing one of these spellings is a real, ordinary bare-name
        collision -- already an accepted risk at the local/spelling tier,
        and not this check's concern. An identity-*less* visible
        declaration is different: it may be an unresolved TU-merge
        duplicate or mixed-producer occurrence of the very entity this id
        names, and a producer simply failing to resolve identity for one
        occurrence is missing *evidence*, not evidence of a distinct,
        unrelated declaration. Confirming this id opaque while such a
        declaration sits under one of its own spellings would let that
        missing evidence silently upgrade the result to a clean
        compatibility claim -- exactly the asymmetry AGENTS.md's "weaker
        evidence narrows conclusions... never upgrades to a clean
        compatibility claim" already rules out for the sibling
        asymmetric-absence case below."""
        for snap in (old, new):
            for t in snap.types:
                if t.is_opaque or stable_entity_id(t.entity_id) is not None:
                    continue
                if t.name in spellings or (
                    t.qualified_name is not None and t.qualified_name in spellings
                ):
                    return True
        return False

    # Per-id, not per-declaration: with lists on both sides, "confirmed
    # opaque on both sides" only means anything asked of *every*
    # declaration under an id, on each side that has one -- one opaque
    # occurrence coexisting with one visible occurrence for the same id is
    # not two-sided opacity, it is the entity being visible somewhere.
    stable_ids: set[StableEntityId] = set()
    for resolved in old_by_stable_id.keys() | new_by_stable_id.keys():
        old_group = old_by_stable_id.get(resolved)
        new_group = new_by_stable_id.get(resolved)
        combined = (old_group or []) + (new_group or [])
        if not any(d.name in truly_opaque for d in combined):
            # This id is unrelated to the coarse bare-name criterion above
            # -- out of scope for this function entirely.
            continue
        id_spellings: set[str] = set()
        for d in combined:
            id_spellings.add(d.name)
            if d.qualified_name is not None:
                id_spellings.add(d.qualified_name)
        if old_group is not None and new_group is not None:
            if (
                all(d.is_opaque for d in old_group)
                and all(d.is_opaque for d in new_group)
                and not _unresolved_visible_duplicate(id_spellings)
            ):
                # The same entity (by id, regardless of spelling),
                # confirmed opaque on both sides -- every declaration
                # under this id, on each side, agrees, and no unresolved
                # occurrence anywhere casts that agreement into doubt.
                stable_ids.add(resolved)
            # Otherwise: at least one declaration under this id is visible,
            # or an unresolved one might be -- decline, regardless of what
            # any other declaration's name looks like.
            continue
        # Exactly one side carries this identity at all -- the
        # asymmetric-existence case. Check the stable counterpart BY ID
        # first, before ever consulting bare names: two producers can
        # render the identical entity under two different spellings (this
        # whole module's own premise: "the header backends key
        # RecordType.name bare while DWARF bakes the namespace into
        # name"), so a name-level absence check run first would misread a
        # same-entity, differently-spelled counterpart as genuinely absent
        # (Codex review, PR #1218, round 4): old's opaque bare "Handle" and
        # new's now-visible "ns::Handle" can share one stable id despite
        # never sharing a name at all. Only once the id itself is
        # confirmed absent from the other side's own bucket does bare-name
        # presence decide anything -- and even then, a resolved id absent
        # from the other bucket is not, by itself, proof the entity is
        # absent from the other snapshot: a mixed-producer or
        # pre-identity-baseline comparison can leave the other side's
        # matching declaration present but carrying no resolvable
        # ``entity_id`` at all (Codex review, PR #1218, round 3) -- missing
        # identity *evidence* is not evidence of *absence*. The
        # asymmetric-existence criterion this function implements is a
        # NAME-level one in the first place (see ``opaque_types`` above,
        # built from ``old_type_names``/``new_type_names``), so falling
        # back to that same bare-name presence check only once identity
        # itself is silent is not a new criterion, only a faithful
        # re-check of the one already used to decide this name belongs in
        # ``truly_opaque`` at all.
        present_group = old_group if old_group is not None else new_group
        assert present_group is not None
        if not all(d.is_opaque for d in present_group):
            # At least one declaration under this id, on its only known
            # side, is itself visible -- not a candidate for the
            # asymmetric-absence case at all.
            continue
        other_all_spellings = (
            new_all_spellings if old_group is not None else old_all_spellings
        )
        # ``id_spellings`` -- every spelling (``name`` *and*, when present,
        # ``qualified_name``) any declaration under this id, on its present
        # side, is known by (Codex review, PR #1218, round 10): a
        # header-AST backend commonly stores the bare leaf in ``name`` and
        # the real scoped spelling in ``qualified_name`` -- checking
        # ``name`` alone would misread a genuinely-present but
        # identity-less counterpart spelled only via ``qualified_name`` as
        # absent, letting this id wrongly enter the index and then get
        # borrowed by that same counterpart's own ``qualified_name``.
        if (id_spellings & other_all_spellings) or _unresolved_visible_duplicate(
            id_spellings
        ):
            # Either a declaration under one of this entity's spellings
            # exists on the other side (but this exact entity's own
            # identity either did not resolve there or resolved to a
            # different entity entirely), or an unresolved, visible
            # declaration under one of its spellings sits somewhere with no
            # identity to rule it out as the same entity -- either way,
            # decline the stable-tier match. The always-safe spelling tier
            # still applies via ``declarations``/``local`` above.
            continue
        # No entity anywhere on the other side resolves to this exact id,
        # no declaration shares any of this entity's known spellings
        # either, and no unresolved occurrence casts doubt -- genuinely
        # absent by both identity and every known spelling, the
        # asymmetric-existence criterion.
        stable_ids.add(resolved)

    return OpaqueTypeIndex.build(declarations, stable_ids=stable_ids)
