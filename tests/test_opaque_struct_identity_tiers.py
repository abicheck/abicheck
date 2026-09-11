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

"""``_downgrade_opaque_struct_changes``/``find_opaque_struct_types``'s own
identity-tiered opaque-suppression path (ADR-063 Phase 10), split out of
``test_opaque_identity_tiers.py`` (Codex review, PR #1218, round 10) once
this migration's own additions pushed that test module past its 1200-line
``architecture/debt.yaml`` test-file cap. ``test_opaque_identity_tiers.py``
keeps the ADR-063 Phase 2 ``find_opaque_types``/``find_by_value_types``/
``OpaqueTypeIndex`` coverage this module's own fixtures (``_snap``,
``_STABLE_ID``/``_OTHER_STABLE_ID``/``_UNSTABLE_ID``) are still imported
from, since duplicating them would let the two files' oracles drift.

The bug *class* under test is "a post-parse consumer joins two sides on a
rendered display spelling, and the two sides render it differently" -- not
one reported spelling pair. So the stable-tier cases below are generated
across several independently-chosen renderings (bare vs. qualified, on
either side, plus a scope-only difference), against the oracle "the two
declarations carry the same resolved ``EntityId``", which is not the
mechanism the implementation's spelling tier uses.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st
from test_opaque_identity_tiers import _OTHER_STABLE_ID, _STABLE_ID, _UNSTABLE_ID, _snap

from abicheck.checker_policy import ChangeKind
from abicheck.compare.opaque_struct_types import find_opaque_struct_types
from abicheck.compare.opaque_types import OpaqueTypeIndex
from abicheck.diff_filtering import (
    _OPAQUE_DOWNGRADEABLE,
    _downgrade_opaque_struct_changes,
    _resolve_struct_change_entity_id,
    _struct_change_record_name,
)
from abicheck.model import RecordType, TypeField
from abicheck.model.identity import Namespace, entity_id_for_type
from abicheck.model.identity_stability import entity_id_is_cross_snapshot_stable
from abicheck.model.identity_tiers import (
    SnapshotLocalIdentity,
    StableEntityId,
    stable_entity_id,
)

# -- ADR-063 Phase 10: `_downgrade_opaque_struct_changes`'s own,
# previously-unmigrated bare `set[str]` opaqueness tracker -----------------


def _record(
    name: str,
    *,
    is_opaque: bool = False,
    entity_id=None,
    fields=(),
    qualified_name: str | None = None,
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        is_opaque=is_opaque,
        entity_id=entity_id,
        fields=list(fields),
        qualified_name=qualified_name,
    )


def _struct_size_change(symbol: str, entity_id=None):
    from abicheck.diff_helpers import make_change

    return make_change(
        ChangeKind.STRUCT_SIZE_CHANGED,
        symbol=symbol,
        old_value="8",
        new_value="16",
        entity_id=entity_id,
    )


def _struct_field_removed_change(record: str, field_name: str, entity_id=None):
    """Mirrors diff_platform._removed_field_changes's own production shape:
    a compound ``f"{record}::{field_name}"`` symbol, no entity_id (the
    DWARF shape), field_name carried separately."""
    from abicheck.diff_helpers import make_change

    return make_change(
        ChangeKind.STRUCT_FIELD_REMOVED,
        symbol=f"{record}::{field_name}",
        detail=field_name,
        old_value="int",
        field_name=field_name,
        entity_id=entity_id,
    )


class TestDowngradeOpaqueStructChangesIdentityTiers:
    """``_downgrade_opaque_struct_changes`` used to test bare
    ``c.symbol in truly_opaque`` string membership -- a second, independent
    opaque-suppression tracker sitting right next to
    ``_downgrade_opaque_type_changes``'s already-migrated
    ``OpaqueTypeIndex``, missed by every prior ADR-063 Phase 2 slice (it is
    not `find_opaque_types`'s own two-sided-intersection criterion at all;
    it additionally treats a type absent from one side's header-level type
    list as opaque). Now goes through the same
    :class:`~abicheck.compare.opaque_types.OpaqueTypeIndex` primitive via
    :meth:`OpaqueTypeIndex.build`, consulting a change's stable
    ``EntityId`` first and its bare spelling second -- never narrowing,
    since this index carries no paired-completeness proof the way
    ``intersect()``'s own does."""

    def test_bare_spelling_match_is_unchanged(self) -> None:
        """No entity_id anywhere (the DWARF/PE/Mach-O-only shape): behavior
        must be bit-for-bit the pre-migration bare-string-set result."""
        old = _snap([_record("Op", is_opaque=True)])
        new = _snap([_record("Op", is_opaque=True)])
        out, filtered = _downgrade_opaque_struct_changes(
            [_struct_size_change("Op")], old, new
        )
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_stable_identity_closes_a_qualification_mismatch(self) -> None:
        """A real false negative the bare-string tracker could not see:
        the change's own ``symbol`` is rendered differently from
        ``RecordType.name`` (bare vs. namespace-qualified), but both
        resolve to the same stable ``EntityId`` -- proof the two sides
        agree on the declaration regardless of spelling."""
        old = _record("Op", is_opaque=True, entity_id=_STABLE_ID)
        new = _record("Op", is_opaque=True, entity_id=_STABLE_ID)
        change = _struct_size_change("ns::Op", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes(
            [change], _snap([old]), _snap([new])
        )
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_a_stable_tier_miss_always_falls_back_to_spelling(self) -> None:
        """This index is never the product of a paired ``intersect()``, so
        it carries no completeness proof -- a change whose own stable id
        does not match any known-opaque one must still fall through to the
        (always-safe) spelling tier rather than being treated as proof of
        non-opacity."""
        old = _record("Op", is_opaque=True, entity_id=_OTHER_STABLE_ID)
        new = _record("Op", is_opaque=True, entity_id=_OTHER_STABLE_ID)
        # A change carrying an unrelated stable id, but the correct bare
        # spelling.
        change = _struct_size_change("Op", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes(
            [change], _snap([old]), _snap([new])
        )
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_embedded_by_value_stays_spelling_based_and_still_blocks_suppression(
        self,
    ) -> None:
        """The by-value-embedding exclusion is a text/rendered-field-type
        question, not an identity one (mirrors
        ``compare.opaque_types.find_by_value_types`` staying spelling-based
        for the identical reason) -- unaffected by this migration."""
        op = _record("Op", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record("Wrapper", fields=[TypeField(name="inner", type="Op")])
        old = _snap([op, wrapper])
        new = _snap([op, wrapper])
        change = _struct_size_change("Op", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_a_visible_types_stable_id_never_enters_the_index_via_a_name_collision(
        self,
    ) -> None:
        """A real false positive a first revision of this migration
        introduced (Codex review, PR #1218): ``ns::Handle`` is opaque on
        both sides, and an unrelated, *visible* ``other::Handle`` happens to
        render the identical bare ``RecordType.name`` ("Handle") -- a real,
        pre-existing collision this module has always had to tolerate at
        the spelling tier. Populating the stable tier from *every*
        same-named declaration regardless of its own ``is_opaque`` would
        smuggle ``other::Handle``'s own stable id into the opaque index
        merely because it shares a spelling with the genuinely opaque
        declaration -- a strictly worse outcome than the pre-migration bare
        string comparison, which a qualified ``other::Handle`` symbol would
        never have matched. A qualified, identity-carrying change for the
        visible type must not be suppressed."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        visible_handle = _record("Handle", is_opaque=False, entity_id=_OTHER_STABLE_ID)
        old = _snap([opaque_handle, visible_handle])
        new = _snap([opaque_handle, visible_handle])
        change = _struct_size_change("other::Handle", entity_id=_OTHER_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_a_visible_types_bare_name_fallback_is_unaffected_by_the_is_opaque_filter(
        self,
    ) -> None:
        """The local (bare-spelling) tier must still match on the shared
        spelling "Handle" alone when the change carries no identity of its
        own -- the ``is_opaque`` filter added for the collision fix above
        only narrows which declaration's *stable* id enters the index, not
        the local tier's membership (``SnapshotLocalIdentity`` compares by
        spelling only, per its own docstring), so this reproduces the
        pre-migration bare ``c.symbol in truly_opaque`` behavior exactly."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        visible_handle = _record("Handle", is_opaque=False, entity_id=_OTHER_STABLE_ID)
        old = _snap([opaque_handle, visible_handle])
        new = _snap([opaque_handle, visible_handle])
        change = _struct_size_change("Handle")
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_a_now_visible_types_own_stable_id_is_not_smuggled_in_via_a_namesake(
        self,
    ) -> None:
        """A second, subtler false positive (Codex review round 2, PR
        #1218): ``ns::Handle`` is opaque only in ``old`` and goes *visible*
        in ``new`` (same entity, same stable id, ``is_opaque`` flips), while
        an unrelated ``other::Handle`` happens to become opaque only in
        ``new``. ``old_opaque & new_opaque`` still puts the shared bare name
        "Handle" in ``truly_opaque`` -- but that intersection only proves
        *some* declaration named "Handle" is opaque on each side, not that
        it is the *same* declaration. Restricting to ``is_opaque``
        declarations alone (the first-round fix) is not enough: without
        entity-level pairing, ``ns::Handle``'s own old-side stable id would
        still enter the stable tier, wrongly suppressing its own,
        genuinely-observable layout change in ``new`` (the type is no
        longer opaque -- consumers can see it)."""
        stable_id = _STABLE_ID
        other_id = _OTHER_STABLE_ID
        old = _snap([_record("Handle", is_opaque=True, entity_id=stable_id)])
        new = _snap(
            [
                _record("Handle", is_opaque=False, entity_id=stable_id),
                _record("Handle", is_opaque=True, entity_id=other_id),
            ]
        )
        change = _struct_size_change("ns::Handle", entity_id=stable_id)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_missing_identity_evidence_on_the_other_side_is_not_treated_as_absence(
        self,
    ) -> None:
        """A third false positive (Codex review round 3, PR #1218): the
        now-visible ``ns::Handle`` in ``new`` carries no resolvable
        ``entity_id`` at all (a mixed-producer or pre-identity-baseline
        comparison), while an unrelated ``other::Handle`` is opaque only in
        ``new``. "Handle" is present by NAME on both sides, so this is not
        the genuine asymmetric-absence case -- but a naive
        ``other_by_id.get(resolved) is None`` check cannot distinguish
        "this entity is genuinely absent from the other snapshot" from
        "a same-named declaration exists there, but this producer simply
        did not resolve an id for it". Treating the latter as absence would
        wrongly add ``ns::Handle``'s own old-side stable id to the index and
        suppress its own, genuinely-observable visibility change."""
        stable_id = _STABLE_ID
        other_id = _OTHER_STABLE_ID
        old = _snap([_record("Handle", is_opaque=True, entity_id=stable_id)])
        new = _snap(
            [
                _record("Handle", is_opaque=False, entity_id=None),
                _record("Handle", is_opaque=True, entity_id=other_id),
            ]
        )
        change = _struct_size_change("ns::Handle", entity_id=stable_id)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_embedded_by_value_exclusion_matches_across_a_qualification_mismatch(
        self,
    ) -> None:
        """A fourth false positive (Codex review round 3, PR #1218): the
        opaque candidate's own ``RecordType.name`` is bare ("Handle"), but
        the containing, non-opaque record's by-value field renders its type
        qualified ("ns::Handle") -- the exact same qualification mismatch
        :func:`_type_is_by_value_referenced`'s own docstring already
        documents for :func:`find_by_value_types`. A naive
        ``f.type.rstrip(" *&") in opaque_types`` string comparison misses
        this field reference entirely, wrongly leaving "Handle" in
        ``truly_opaque`` despite being embedded by value -- and the stable
        tier then downgrades a real, qualified ``TYPE_SIZE_CHANGED``
        finding for it, something the pre-migration bare ``c.symbol``
        comparison would never have matched in the first place (the
        embedding exclusion's own qualification-mismatch bug is
        pre-existing, but the stable tier gives it new, larger
        consequences)."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record(
            "Wrapper", fields=[TypeField(name="inner", type="ns::Handle")]
        )
        old = _snap([opaque_handle, wrapper])
        new = _snap([opaque_handle, wrapper])
        change = _struct_size_change("ns::Handle", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_a_stable_counterpart_under_a_different_spelling_is_checked_before_name_absence(
        self,
    ) -> None:
        """A fifth false positive (Codex review round 4, PR #1218): old's
        opaque declaration is bare-named ("Handle") while new's now-visible
        counterpart is qualified ("ns::Handle") -- two producers rendering
        the identical entity under two different spellings, this whole
        module's own long-standing premise. A name-level absence check run
        *before* consulting the stable counterpart by id would misread
        this as the genuine asymmetric-absence case (the two names never
        literally match), when the two declarations in fact share one
        stable id and the entity is visible, not absent, on the other
        side. The id-first check must win."""
        stable_id = _STABLE_ID
        old = _record("Handle", is_opaque=True, entity_id=stable_id)
        new = _record("ns::Handle", is_opaque=False, entity_id=stable_id)
        old_snap = _snap([old])
        new_snap = _snap([new])
        change = _struct_size_change("ns::Handle", entity_id=stable_id)
        out, filtered = _downgrade_opaque_struct_changes([change], old_snap, new_snap)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_an_identity_less_counterpart_spelled_via_qualified_name_is_not_absence(
        self,
    ) -> None:
        """An eighth false positive (Codex review, PR #1218, round 10): old's
        opaque declaration is bare-named ("Handle") but its own
        ``qualified_name`` is "ns::Handle"; new's now-visible counterpart is
        spelled directly as "ns::Handle" in ``RecordType.name`` and carries
        no resolvable ``entity_id`` at all. The stable tier's absence check
        used to compare old's bare ``name`` ("Handle") against a
        bare-``name``-only set built from ``new.types`` -- which never
        contains "Handle" -- and conclude the entity was genuinely absent
        from ``new``, wrongly adding old's stable id to the index and then
        letting it suppress a real, observable layout change on the
        now-visible type. Checking old's ``qualified_name`` too (mirroring
        the identity bridge's own round-9 fix) recognizes "ns::Handle" as a
        real counterpart, so this is not the asymmetric-absence case."""
        stable_id = _STABLE_ID
        old = _record(
            "Handle", is_opaque=True, entity_id=stable_id, qualified_name="ns::Handle"
        )
        new = _record("ns::Handle", is_opaque=False, entity_id=None)
        old_snap = _snap([old])
        new_snap = _snap([new])
        change = _struct_size_change("ns::Handle", entity_id=stable_id)
        out, filtered = _downgrade_opaque_struct_changes([change], old_snap, new_snap)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_embedded_by_value_exclusion_ignores_an_unrelated_pointer_elsewhere_in_the_field(
        self,
    ) -> None:
        """A sixth false positive (Codex review round 4, PR #1218): the
        field type text carries an unrelated pointer alongside a genuine
        by-value reference to the opaque candidate (e.g. a template
        argument list like ``Pair<ns::Handle, int *>``) -- a naive
        whole-string ``"*" in f.type`` pre-check would skip the entire
        field just because *a* pointer appears in it somewhere, even though
        the matched ``ns::Handle`` occurrence itself is by value. This must
        be decided per occurrence (:func:`_type_is_by_value_referenced`
        already does this correctly on its own), not by a blanket
        string-level pointer check before ever calling it."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record(
            "Wrapper",
            fields=[TypeField(name="inner", type="Pair<ns::Handle, int *>")],
        )
        old = _snap([opaque_handle, wrapper])
        new = _snap([opaque_handle, wrapper])
        change = _struct_size_change("ns::Handle", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_a_pointer_to_data_member_field_does_not_count_as_by_value_embedding(
        self,
    ) -> None:
        """A seventh false positive (Codex review round 6, PR #1218): a
        field declared as a pointer-to-data-member (``Handle Owner::*``)
        stores a byte offset into ``Owner`` -- it does not embed a
        ``Handle`` object at all -- but the ``::*`` scope qualifier sits
        between the type name and its own sigil, past what the
        cv-qualifier/whitespace skip in :func:`_sigil_follows` covers, so
        this declarator was previously (mis)read as by-value. A genuinely
        opaque type's own layout change must still be suppressed."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record(
            "Wrapper", fields=[TypeField(name="ptr", type="Handle Owner::*")]
        )
        old = _snap([opaque_handle, wrapper])
        new = _snap([opaque_handle, wrapper])
        change = _struct_size_change("Handle", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED


class TestStableIdRequiresEveryDeclarationOpaque:
    """A stable id must never enter the index unless *every* declaration
    resolving to it, on each side that has one, is itself opaque (Codex
    review, PR #1218, round 11).

    A snapshot can legitimately carry more than one declaration resolving
    to the same ``StableEntityId``: ``model/semantic_ir.py``'s own
    ``SemanticIR.occurrences`` deliberately never collapses an
    ODR-duplicate pair, and a header-AST TU-merge that cannot reconcile two
    declarations of one entity preserves both rather than picking one. Such
    a pair can disagree on ``is_opaque`` (one TU only forward-declared it,
    another saw the full definition) -- a last-write-wins ``dict[StableEntityId,
    RecordType]`` silently discarded whichever declaration was not iterated
    last, making the opacity verdict for that id depend on iteration/
    insertion order rather than on whether the entity is genuinely opaque
    everywhere it appears on that side."""

    @pytest.mark.parametrize("dup_order", [0, 1], ids=["visible-first", "opaque-first"])
    def test_a_coexisting_visible_duplicate_blocks_the_id(self, dup_order: int) -> None:
        stable_id = _STABLE_ID
        old = _snap([_record("Handle", is_opaque=True, entity_id=stable_id)])
        visible_dup = _record("Handle", is_opaque=False, entity_id=stable_id)
        opaque_dup = _record("Handle", is_opaque=True, entity_id=stable_id)
        dups = (
            [visible_dup, opaque_dup] if dup_order == 0 else [opaque_dup, visible_dup]
        )
        new = _snap(dups)
        index = find_opaque_struct_types(old, new)
        assert stable_entity_id(stable_id) not in index.stable

    def test_two_opaque_duplicates_still_confirm_the_id(self) -> None:
        """The fix must not become *stricter* than necessary: two
        declarations under one id that are BOTH opaque still confirm it,
        same as a single declaration would."""
        stable_id = _STABLE_ID
        old = _snap([_record("Handle", is_opaque=True, entity_id=stable_id)])
        dup_a = _record("Handle", is_opaque=True, entity_id=stable_id)
        dup_b = _record("Handle", is_opaque=True, entity_id=stable_id)
        new = _snap([dup_a, dup_b])
        index = find_opaque_struct_types(old, new)
        assert stable_entity_id(stable_id) in index.stable


class TestUnresolvedVisibleDuplicateBlocksTheId:
    """An identity-*less* visible declaration under one of a stable id's own
    spellings must block that id, even when every declaration that *does*
    resolve to the id is itself opaque (Codex review, PR #1218, round 12).

    A declaration resolving to a different, positively-identified id is an
    ordinary bare-name collision (already an accepted risk at the local
    tier). An identity-less one is different: it may be an unresolved
    TU-merge duplicate or mixed-producer occurrence of the very entity this
    id names, and a producer simply failing to resolve identity for one
    occurrence is missing *evidence*, not evidence of a distinct, unrelated
    declaration -- confirming the id opaque anyway would let that missing
    evidence silently upgrade the result to a clean compatibility claim."""

    def test_a_coexisting_unresolved_visible_duplicate_on_both_sides_blocks_the_id(
        self,
    ) -> None:
        stable_id = _STABLE_ID
        opaque_old = _record("Handle", is_opaque=True, entity_id=stable_id)
        visible_unresolved_old = _record("Handle", is_opaque=False, entity_id=None)
        opaque_new = _record("Handle", is_opaque=True, entity_id=stable_id)
        visible_unresolved_new = _record("Handle", is_opaque=False, entity_id=None)
        old = _snap([opaque_old, visible_unresolved_old])
        new = _snap([opaque_new, visible_unresolved_new])
        index = find_opaque_struct_types(old, new)
        assert stable_entity_id(stable_id) not in index.stable

    def test_a_coexisting_unresolved_visible_duplicate_on_the_present_side_blocks_the_id(
        self,
    ) -> None:
        """The asymmetric-absence branch: old carries the id plus an
        unresolved visible duplicate under the same spelling, new is
        genuinely empty. The duplicate on old's own side -- not the
        other side -- is what must block the id here."""
        stable_id = _STABLE_ID
        opaque_old = _record("Handle", is_opaque=True, entity_id=stable_id)
        visible_unresolved_old = _record("Handle", is_opaque=False, entity_id=None)
        old = _snap([opaque_old, visible_unresolved_old])
        new = _snap([])
        index = find_opaque_struct_types(old, new)
        assert stable_entity_id(stable_id) not in index.stable

    def test_a_duplicate_resolving_to_a_different_id_does_not_block(self) -> None:
        """An ordinary bare-name collision -- the coexisting declaration
        resolves to its *own*, different, positively-identified id -- is
        not this check's concern; it is a real, distinct entity, not
        missing evidence about the same one."""
        stable_id = _STABLE_ID
        other_id = _OTHER_STABLE_ID
        opaque_old = _record("Handle", is_opaque=True, entity_id=stable_id)
        visible_other_old = _record("Handle", is_opaque=False, entity_id=other_id)
        opaque_new = _record("Handle", is_opaque=True, entity_id=stable_id)
        visible_other_new = _record("Handle", is_opaque=False, entity_id=other_id)
        old = _snap([opaque_old, visible_other_old])
        new = _snap([opaque_new, visible_other_new])
        index = find_opaque_struct_types(old, new)
        assert stable_entity_id(stable_id) in index.stable


class TestDowngradeOpaqueStructChangesNeverFabricatesAnAddition:
    """A matched opaque change's *original* ``ChangeKind`` must survive --
    never silently mislabeled as ``TYPE_FIELD_ADDED_COMPATIBLE`` (Codex
    review, PR #1218, round 10). This predates the ADR-063 Phase 10
    migration itself, but the migration is what brought this call site
    under review: every ``_OPAQUE_DOWNGRADEABLE`` kind used to be replaced
    unconditionally, turning an observed removal or mutation into a
    fabricated addition -- a "record before disposing" violation (root
    ``AGENTS.md``), since the *fact* reported (an addition) was not the
    fact detected (a removal/mutation/size or alignment change)."""

    @pytest.mark.parametrize(
        "kind",
        sorted(_OPAQUE_DOWNGRADEABLE - {ChangeKind.TYPE_FIELD_ADDED}, key=str),
    )
    def test_every_non_addition_downgradeable_kind_is_excluded_not_relabelled(
        self, kind: ChangeKind
    ) -> None:
        from abicheck.diff_helpers import make_change

        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        old = _snap([opaque_handle])
        new = _snap([opaque_handle])
        change = make_change(
            kind, symbol="Handle", entity_id=_STABLE_ID, old_value="a", new_value="b"
        )
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not out
        assert filtered == [change]
        assert filtered[0].kind == kind

    def test_a_genuine_addition_is_still_relabelled_compatible(self) -> None:
        from abicheck.diff_helpers import make_change

        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        old = _snap([opaque_handle])
        new = _snap([opaque_handle])
        change = make_change(
            ChangeKind.TYPE_FIELD_ADDED,
            symbol="Handle",
            entity_id=_STABLE_ID,
            old_value="a",
            new_value="b",
        )
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not filtered
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE


# -- Primitive-level property tests: OpaqueTypeIndex.build ------------------


_names = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu"), max_codepoint=122),
    min_size=1,
    max_size=6,
)
_entity_ids = st.one_of(st.none(), st.just(_STABLE_ID), st.just(_UNSTABLE_ID))


@st.composite
def _declarations_map(draw):
    names = draw(st.lists(_names, min_size=0, max_size=5, unique=True))
    declarations: dict[str, list[RecordType]] = {}
    for name in names:
        count = draw(st.integers(min_value=1, max_value=3))
        declarations[name] = [
            _record(name, entity_id=draw(_entity_ids)) for _ in range(count)
        ]
    return declarations


class TestOpaqueTypeIndexBuildProperties:
    """:meth:`OpaqueTypeIndex.build` is the primitive
    ``_downgrade_opaque_struct_changes`` now shares with the rest of the
    identity-tiers migration -- AGENTS.md's "Primitive-level property
    tests" doctrine applies to it directly, independent of that one
    caller's own domain logic."""

    @given(_declarations_map())
    def test_every_declared_name_reaches_the_local_tier(self, declarations) -> None:
        index = OpaqueTypeIndex.build(declarations)
        for name in declarations:
            assert SnapshotLocalIdentity(name) in index.local

    @given(_declarations_map())
    def test_stable_tier_only_ever_holds_cross_snapshot_stable_ids(
        self, declarations
    ) -> None:
        index = OpaqueTypeIndex.build(declarations)
        for stable in index.stable:
            assert entity_id_is_cross_snapshot_stable(stable.entity_id)

    @given(_declarations_map())
    def test_no_entity_id_means_no_stable_tier_contribution(self, declarations) -> None:
        """A declaration with ``entity_id=None`` never adds anything to the
        stable tier -- the DWARF/PE/Mach-O-only shape degrades to exactly
        the pre-migration bare ``set[str]`` behavior."""
        only_local = {
            name: [_record(name, entity_id=None) for _ in decls]
            for name, decls in declarations.items()
        }
        index = OpaqueTypeIndex.build(only_local)
        assert index.stable == frozenset()

    @given(_declarations_map())
    def test_build_is_independent_of_dict_insertion_order(self, declarations) -> None:
        reversed_declarations = dict(reversed(list(declarations.items())))
        assert OpaqueTypeIndex.build(declarations) == OpaqueTypeIndex.build(
            reversed_declarations
        )

    @given(_declarations_map())
    def test_build_never_licenses_strict_narrowing(self, declarations) -> None:
        """``complete`` stays at the dataclass default (``True``) only in
        the sense of "no narrower claim was computed" -- this builder must
        never be mistaken for :meth:`OpaqueTypeIndex.intersect`'s own
        *proven*-complete result. Callers (this test's own contract) must
        only ever query it with ``strict=False``."""
        index = OpaqueTypeIndex.build(declarations)
        assert index.stable_by_local == {}


# -- Primitive-level property tests: find_opaque_struct_types's stable tier -


_THIRD_STABLE_ID = entity_id_for_type((Namespace("third"),), "Handle")
#: Two *different* entities deliberately sharing one bare rendered spelling
#: ("Handle") -- the real collision shape (ADR-063 Phase 2/10's whole
#: reason for a stable tier at all) -- plus one entity under its own name.
#: A stable id's own leaf name is fixed here and never reassigned to a
#: different bare ``RecordType.name`` within a side below: a real producer
#: derives identity from a declaration's own scope/name, so the same
#: identity cannot legitimately show up under two different spellings
#: inside one already-parsed snapshot (letting the generator do that
#: produced a meaningless first draft of this property -- ids and names
#: must vary together, not independently).
_ENTITY_TABLE = [
    ("Handle", _STABLE_ID),
    ("Handle", _OTHER_STABLE_ID),
    ("Other", _THIRD_STABLE_ID),
]


@st.composite
def _opaque_struct_scenario(draw):
    """A small random (old, new) pair of plain (no-field, so the by-value
    embedding exclusion never applies) declaration lists. Each identified
    entity in :data:`_ENTITY_TABLE` independently decides whether it
    appears on each side at all, and its opacity there if so; a separate
    pool of identity-less declarations (the mixed-producer/pre-baseline
    shape) may additionally collide on an arbitrary bare name, including
    an identified entity's own name -- exactly the shapes four rounds of
    Codex review on PR #1218 found real gaps in."""
    old_records: list[RecordType] = []
    new_records: list[RecordType] = []
    for name, entity_id in _ENTITY_TABLE:
        if draw(st.booleans()):
            old_records.append(
                _record(name, is_opaque=draw(st.booleans()), entity_id=entity_id)
            )
        if draw(st.booleans()):
            new_records.append(
                _record(name, is_opaque=draw(st.booleans()), entity_id=entity_id)
            )
    extra_names = draw(st.lists(_names, min_size=0, max_size=2))
    for name in extra_names:
        if draw(st.booleans()):
            old_records.append(
                _record(name, is_opaque=draw(st.booleans()), entity_id=None)
            )
        if draw(st.booleans()):
            new_records.append(
                _record(name, is_opaque=draw(st.booleans()), entity_id=None)
            )
    return old_records, new_records


class TestFindOpaqueStructTypesStableTierSoundness:
    """Four rounds of Codex review on PR #1218 each found a way for
    :func:`find_opaque_struct_types` to add a stable id to its index
    without that specific entity actually satisfying the function's own
    documented criterion (opaque on both sides, or genuinely absent -- by
    identity, not by name -- from the other snapshot). Rather than
    re-implementing the same bare-name-grouping computation as a second
    "oracle" (which would just reproduce whatever conceptual mistake the
    implementation itself makes), this audits the *output* directly
    against that criterion, resolving each returned id's own declarations
    fresh by identity -- independent of the implementation's internal
    ``truly_opaque``/``declarations`` bookkeeping."""

    @given(_opaque_struct_scenario())
    def test_every_stable_id_satisfies_the_documented_criterion(self, scenario) -> None:
        old_records, new_records = scenario
        old = _snap(old_records)
        new = _snap(new_records)
        index = find_opaque_struct_types(old, new)

        old_by_id: dict[StableEntityId, list[RecordType]] = {}
        for r in old_records:
            resolved = stable_entity_id(r.entity_id)
            if resolved is not None:
                old_by_id.setdefault(resolved, []).append(r)
        new_by_id: dict[StableEntityId, list[RecordType]] = {}
        for r in new_records:
            resolved = stable_entity_id(r.entity_id)
            if resolved is not None:
                new_by_id.setdefault(resolved, []).append(r)
        old_names = {r.name for r in old_records}
        new_names = {r.name for r in new_records}

        for sid in index.stable:
            old_decls = old_by_id.get(sid, [])
            new_decls = new_by_id.get(sid, [])
            # The id must actually be attested as opaque on at least one
            # side (it cannot have entered the index otherwise).
            assert any(d.is_opaque for d in old_decls) or any(
                d.is_opaque for d in new_decls
            )
            if old_decls and new_decls:
                # Present (by identity) on both sides: every declaration
                # under this id on both sides must be opaque -- this is
                # the two-sided criterion, checked by identity rather than
                # by name.
                assert all(d.is_opaque for d in old_decls)
                assert all(d.is_opaque for d in new_decls)
            elif old_decls and not new_decls:
                # Present only in old, by identity -- the asymmetric
                # criterion requires it to ALSO be absent from new by
                # bare name (never merely "no id resolved there").
                assert all(d.name not in new_names for d in old_decls)
            elif new_decls and not old_decls:
                assert all(d.name not in old_names for d in new_decls)

    @given(_opaque_struct_scenario())
    def test_result_is_independent_of_old_new_type_list_order(self, scenario) -> None:
        """Reordering the declarations within one snapshot's own type list
        must not change which stable ids qualify -- this function's
        criterion is about identity and cross-snapshot presence, never
        about position."""
        old_records, new_records = scenario
        old = _snap(old_records)
        new = _snap(new_records)
        old_shuffled = _snap(list(reversed(old_records)))
        new_shuffled = _snap(list(reversed(new_records)))
        assert (
            find_opaque_struct_types(old, new).stable
            == find_opaque_struct_types(old_shuffled, new_shuffled).stable
        )


# -- _resolve_struct_change_entity_id: bridging DWARF changes to identity ---


class TestResolveStructChangeEntityId:
    """The production DWARF struct-layout diff builds its own ``Change``s
    with no ``entity_id`` at all (``StructLayout`` has no such field) --
    Codex review round 5 on PR #1218 found that without this bridge, the
    stable tier this migration adds never actually fires for that real
    caller; only the hand-constructed test changes carrying an explicit
    ``entity_id`` exercised it."""

    def test_borrows_the_unambiguous_id_for_an_identity_less_change(self) -> None:
        old = _snap([_record("Handle", is_opaque=True, entity_id=_STABLE_ID)])
        new = _snap([_record("Handle", is_opaque=True, entity_id=_STABLE_ID)])
        change = _struct_size_change("Handle")  # no entity_id -- the DWARF shape
        resolved = _resolve_struct_change_entity_id(change, old, new)
        assert resolved.entity_id == _STABLE_ID

    def test_the_bridge_is_what_lets_the_qualification_mismatch_actually_suppress(
        self,
    ) -> None:
        """The PR's own headline scenario, but with a *production-shaped*
        change carrying no ``entity_id`` of its own (as a real DWARF-derived
        ``Change`` would) -- the bridge must resolve identity from the
        header-AST ``RecordType`` data for the qualification mismatch to
        close at all."""
        old = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        new = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        # A DWARF-only change: no entity_id, and its own symbol happens to
        # be rendered qualified (a real producer-spelling difference) even
        # though RecordType.name is bare.
        change = _struct_size_change("ns::Handle")
        out, filtered = _downgrade_opaque_struct_changes(
            [change], _snap([old]), _snap([new])
        )
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    def test_declines_to_guess_under_ambiguity(self) -> None:
        """Two declarations named "Handle" resolve two different ids --
        the bridge must not guess either one; the change is returned
        unchanged, leaving the always-safe spelling tier as the only
        path to a match."""
        old = _snap(
            [
                _record("Handle", is_opaque=True, entity_id=_STABLE_ID),
                _record("Handle", is_opaque=True, entity_id=_OTHER_STABLE_ID),
            ]
        )
        new = _snap(
            [
                _record("Handle", is_opaque=True, entity_id=_STABLE_ID),
                _record("Handle", is_opaque=True, entity_id=_OTHER_STABLE_ID),
            ]
        )
        change = _struct_size_change("Handle")
        resolved = _resolve_struct_change_entity_id(change, old, new)
        assert resolved.entity_id is None
        assert resolved is change

    def test_an_exact_name_match_with_no_identity_blocks_the_bare_fallback(
        self,
    ) -> None:
        """A real false positive (Codex review round 8, PR #1218): the
        change is genuinely about ``other::Handle`` (an exact-name
        declaration exists, but carries no resolved ``entity_id``), while
        an unrelated, opaque bare ``Handle`` happens to carry a stable id.
        Falling through to the bare-name candidate here would borrow the
        wrong entity's identity entirely -- the exact-name declaration's
        own missing identity must decline the bridge outright, not excuse
        a guess from an unrelated namesake."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        visible_other_handle = _record("other::Handle", is_opaque=False, entity_id=None)
        old = _snap([opaque_handle, visible_other_handle])
        new = _snap([opaque_handle, visible_other_handle])
        change = _struct_size_change("other::Handle")
        resolved = _resolve_struct_change_entity_id(change, old, new)
        assert resolved.entity_id is None
        assert resolved is change

    def test_a_qualified_name_match_counts_as_exact_too(self) -> None:
        """A real false positive (Codex review round 9, PR #1218): the
        header-AST backend stores the bare leaf in ``RecordType.name`` and
        the real scoped spelling in ``qualified_name`` -- a visible
        ``api::Handle`` (identity-less) with an unrelated, opaque
        ``internal::Handle`` (bare-named "Handle", with a stable id)
        elsewhere. A DWARF change genuinely about "api::Handle" must
        recognize the ``qualified_name`` match as exact (blocking the
        bare-name fallback), not only a ``RecordType.name`` match."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        visible_api_handle = _record(
            "Handle", is_opaque=False, entity_id=None, qualified_name="api::Handle"
        )
        old = _snap([opaque_handle, visible_api_handle])
        new = _snap([opaque_handle, visible_api_handle])
        change = _struct_size_change("api::Handle")
        resolved = _resolve_struct_change_entity_id(change, old, new)
        assert resolved.entity_id is None
        assert resolved is change

    def test_a_change_with_its_own_entity_id_is_returned_unchanged(self) -> None:
        """A change that already carries an identity (a header-AST-sourced
        producer, or an already-bridged copy) is passed through verbatim --
        never overwritten by a lookup result."""
        old = _snap([_record("Handle", is_opaque=True, entity_id=_STABLE_ID)])
        new = _snap([_record("Handle", is_opaque=True, entity_id=_STABLE_ID)])
        change = _struct_size_change("Handle", entity_id=_OTHER_STABLE_ID)
        resolved = _resolve_struct_change_entity_id(change, old, new)
        assert resolved is change

    def test_an_unmatched_changes_own_identity_is_never_leaked_into_the_result(
        self,
    ) -> None:
        """The bridge is only ever consulted for the membership *check* --
        a change that does not qualify for downgrading must come back out
        of :func:`_downgrade_opaque_struct_changes` as the exact same
        object, not a copy carrying a borrowed ``entity_id`` it never had."""
        old = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record("Wrapper", fields=[TypeField(name="inner", type="Handle")])
        old_snap = _snap([old, wrapper])
        new_snap = _snap([old, wrapper])
        change = _struct_size_change("Handle")  # embedded by value -- not downgraded
        out, filtered = _downgrade_opaque_struct_changes([change], old_snap, new_snap)
        assert not filtered
        assert out[0] is change
        assert out[0].entity_id is None


class TestStructChangeRecordName:
    """``_struct_change_record_name`` recovers a field-level DWARF change's
    owning record name -- Codex review round 7 on PR #1218 found the
    identity bridge (and, pre-existing and unrelated to this migration,
    the always-safe spelling tier too) never worked for
    ``STRUCT_FIELD_REMOVED``/``STRUCT_FIELD_OFFSET_CHANGED``/
    ``STRUCT_FIELD_TYPE_CHANGED`` at all, since their own ``symbol`` is
    compounded as ``f"{record}::{field_name}"``."""

    def test_recovers_the_record_name_from_a_compound_field_symbol(self) -> None:
        change = _struct_field_removed_change("ns::Handle", "count")
        assert _struct_change_record_name(change) == "ns::Handle"

    def test_a_whole_struct_changes_symbol_is_returned_unchanged(self) -> None:
        change = _struct_size_change("ns::Handle")
        assert _struct_change_record_name(change) == "ns::Handle"

    def test_the_bridge_resolves_identity_for_a_field_level_dwarf_change(
        self,
    ) -> None:
        """The PR's own headline scenario, but for a field-level change
        (STRUCT_FIELD_REMOVED) rather than a whole-struct one -- the
        bridge must recover "Handle" from "ns::Handle::count", not guess
        "count" via a generic bare-name heuristic on the whole symbol."""
        old = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        new = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        change = _struct_field_removed_change("ns::Handle", "count")
        out, filtered = _downgrade_opaque_struct_changes(
            [change], _snap([old]), _snap([new])
        )
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_FIELD_REMOVED

    def test_the_spelling_tier_also_matches_a_field_level_change_by_record_name(
        self,
    ) -> None:
        """Even with no identity anywhere (the pre-migration shape), a
        field-level change must now be checked against the record's own
        bare spelling -- previously always missed, since the compound
        "Record::field" symbol was compared directly against a bare
        record-name set."""
        old = _record("Handle", is_opaque=True)
        new = _record("Handle", is_opaque=True)
        change = _struct_field_removed_change("Handle", "count")
        out, filtered = _downgrade_opaque_struct_changes(
            [change], _snap([old]), _snap([new])
        )
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_FIELD_REMOVED


class TestMemberPointerFollowsTemplatedOwners:
    """A pointer-to-data-member declarator's owner scope may itself be
    template-qualified (``"Handle Owner<int>::*"``,
    ``"Handle ns::Owner<int>::*"``) -- Codex review round 7 on PR #1218
    found the round-6 fix's plain ``\\w+`` segment matching couldn't
    accept a ``<...>`` template argument list at all."""

    @pytest.mark.parametrize(
        "field_type",
        [
            "Handle Owner<int>::*",
            "Handle ns::Owner<int>::*",
            "Handle Owner<Pair<int, int>>::*",
        ],
    )
    def test_a_templated_owner_is_still_recognized_as_indirect(
        self, field_type: str
    ) -> None:
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record("Wrapper", fields=[TypeField(name="ptr", type=field_type)])
        old = _snap([opaque_handle, wrapper])
        new = _snap([opaque_handle, wrapper])
        change = _struct_size_change("Handle", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED

    @pytest.mark.parametrize(
        "field_type",
        [
            "Handle (Owner::*)[3]",
            "Handle (Owner<int>::*)[3]",
            "Handle (ns::Owner<int>::*)[3]",
        ],
    )
    def test_a_templated_owner_in_the_grouped_form_is_still_recognized(
        self, field_type: str
    ) -> None:
        """The *parenthesized* pointer-to-member-array/-function shape
        (``"Handle (Owner<int>::*)[3]"``) had its own, separate
        non-template-aware owner-scope regex -- Codex review round 9,
        PR #1218: fixing the unparenthesized shape in round 7 did not fix
        this one too."""
        opaque_handle = _record("Handle", is_opaque=True, entity_id=_STABLE_ID)
        wrapper = _record("Wrapper", fields=[TypeField(name="ptr", type=field_type)])
        old = _snap([opaque_handle, wrapper])
        new = _snap([opaque_handle, wrapper])
        change = _struct_size_change("Handle", entity_id=_STABLE_ID)
        out, filtered = _downgrade_opaque_struct_changes([change], old, new)
        assert not out
        assert filtered[0].kind == ChangeKind.STRUCT_SIZE_CHANGED


# -- Primitive-level property tests: _resolve_struct_change_entity_id ------


_bridge_ids = st.one_of(st.none(), st.just(_STABLE_ID), st.just(_OTHER_STABLE_ID))


@st.composite
def _struct_change_bridge_scenario(draw):
    """A change genuinely about "ns::Handle" (record_name), whose bare form
    is "Handle" -- with 0-2 declarations under each of the two names,
    each independently carrying an id (or none), spread arbitrarily across
    old/new (the function scans both combined, so the split doesn't matter
    to its own logic)."""
    exact_decls = [
        _record("ns::Handle", entity_id=draw(_bridge_ids))
        for _ in range(draw(st.integers(min_value=0, max_value=2)))
    ]
    bare_decls = [
        _record("Handle", entity_id=draw(_bridge_ids))
        for _ in range(draw(st.integers(min_value=0, max_value=2)))
    ]
    all_decls = exact_decls + bare_decls
    split = draw(st.integers(min_value=0, max_value=len(all_decls)))
    return all_decls[:split], all_decls[split:]


class TestResolveStructChangeEntityIdSoundness:
    """Codex review round 8 on PR #1218 found the exact-name and bare-name
    candidate pools were merged into one set, letting an exact-name
    declaration's own missing identity be silently papered over by an
    unrelated bare-name namesake's id. This property audits the bridge's
    output directly against the precedence rule that closed it: an
    exact-name match, when one exists at all, is authoritative -- its own
    resolved id(s) decide the outcome, and the bare-name pool is never
    consulted, regardless of what it contains."""

    @given(_struct_change_bridge_scenario())
    def test_exact_name_precedence_and_soundness(self, scenario) -> None:
        old_decls, new_decls = scenario
        old = _snap(old_decls)
        new = _snap(new_decls)
        change = _struct_size_change("ns::Handle")
        resolved = _resolve_struct_change_entity_id(change, old, new)

        all_decls = old_decls + new_decls
        exact_ids = {
            d.entity_id for d in all_decls if d.name == "ns::Handle" and d.entity_id
        }
        exact_exists = any(d.name == "ns::Handle" for d in all_decls)

        if exact_exists:
            expected = next(iter(exact_ids)) if len(exact_ids) == 1 else None
        else:
            bare_ids = {
                d.entity_id for d in all_decls if d.name == "Handle" and d.entity_id
            }
            expected = next(iter(bare_ids)) if len(bare_ids) == 1 else None

        assert resolved.entity_id == expected
