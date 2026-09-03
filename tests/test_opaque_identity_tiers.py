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

"""``diff_filtering``'s opaque-type suppression, after its migration onto
the two identity tiers (ADR-063 Phase 2's post-parse consumer migration).

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

from abicheck.checker_policy import ChangeKind
from abicheck.diff_filtering import (
    _downgrade_opaque_type_changes,
    _find_by_value_types,
    _find_opaque_types,
)
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Variable
from abicheck.model.identity import (
    Anonymous,
    EntityKind,
    Namespace,
    entity_id_for_type,
)
from abicheck.model.identity_tiers import (
    SnapshotLocalIdentity,
    StableEntityId,
    snapshot_local_identity,
    stable_entity_id,
)

_STABLE_ID = entity_id_for_type((Namespace("ns"),), "Handle")
_OTHER_STABLE_ID = entity_id_for_type((Namespace("other"),), "Handle")
_UNSTABLE_ID = entity_id_for_type((Anonymous("namespace", 0),), "Handle")


def _snap(types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so.1", version="1.0.0", types=types)


def _opaque(name: str, entity_id=None) -> RecordType:
    return RecordType(name=name, kind="struct", is_opaque=True, entity_id=entity_id)


def _size_change(symbol: str, entity_id=None):
    from abicheck.diff_helpers import make_change

    return make_change(
        ChangeKind.TYPE_SIZE_CHANGED,
        symbol=symbol,
        old_value="8",
        new_value="16",
        entity_id=entity_id,
    )


def _survivors(changes, old, new):
    return [c.symbol for c in _downgrade_opaque_type_changes(changes, old, new)]


# -- The index itself -------------------------------------------------------


class TestOpaqueTypeIndexTiers:
    def test_a_declaration_with_a_stable_identity_populates_both_tiers(self) -> None:
        """Every opaque declaration reaches the spelling tier; one with a
        stable ``EntityId`` additionally reaches the stable tier. Keeping
        both is what makes the migration a superset of the pre-migration
        string behavior rather than a narrowing of it."""
        index = _find_opaque_types(_snap([_opaque("Handle", _STABLE_ID)]))
        assert index.stable == frozenset({StableEntityId(_STABLE_ID)})
        assert index.local == frozenset({SnapshotLocalIdentity("Handle")})

    def test_a_declaration_with_no_identity_reaches_only_the_spelling_tier(
        self,
    ) -> None:
        """The DWARF/PE/Mach-O-only shape: no backend resolves an
        ``EntityId``, so the index degrades to exactly the ``set[str]`` of
        ``RecordType.name`` this consumer used before the migration."""
        index = _find_opaque_types(_snap([_opaque("Handle")]))
        assert index.stable == frozenset()
        assert index.local == frozenset({SnapshotLocalIdentity("Handle")})

    def test_a_parse_order_ordinal_is_kept_out_of_the_stable_tier(self) -> None:
        """An ``EntityId`` whose scope carries an ``Anonymous`` ordinal is
        resolved, but not cross-snapshot comparable -- it must not enter
        the stable tier, or an unrelated anonymous sibling insertion in a
        later release could silently move which type is suppressed."""
        index = _find_opaque_types(_snap([_opaque("Handle", _UNSTABLE_ID)]))
        assert index.stable == frozenset()
        assert index.local == frozenset({SnapshotLocalIdentity("Handle")})
        # The unstable id is still carried as a diagnostics-only payload.
        assert next(iter(index.local)).entity_id == _UNSTABLE_ID

    def test_intersection_is_per_tier(self) -> None:
        """A type opaque on both sides but carrying a stable identity on
        only one still meets in the spelling tier -- the mixed-producer
        comparison that a stable-tier-only intersection would silently drop
        a suppression for."""
        left = _find_opaque_types(_snap([_opaque("Handle", _STABLE_ID)]))
        right = _find_opaque_types(_snap([_opaque("Handle")]))
        both = left.intersect(right)
        assert both.stable == frozenset()
        assert both.local == frozenset({SnapshotLocalIdentity("Handle")})
        assert bool(both)


# -- The bug class: two sides rendering one declaration differently ---------


class TestStableIdentityJoinsWhatSpellingsDoNotMatch:
    @pytest.mark.parametrize(
        ("record_name", "change_symbol"),
        [
            # The header backends key `RecordType.name` bare while several
            # `Change` producers render `symbol` qualified.
            ("Handle", "ns::Handle"),
            # ... and DWARF bakes the namespace into `name` instead, so the
            # mismatch also occurs in the opposite direction.
            ("ns::Handle", "Handle"),
            # Two independently-chosen renderings that agree on neither
            # side, to keep this a class rather than a pair.
            ("ns::Handle", "ns::detail::Handle"),
            ("Handle<int>", "ns::Handle<int>"),
        ],
    )
    def test_matching_stable_identities_suppress_across_a_spelling_mismatch(
        self, record_name: str, change_symbol: str
    ) -> None:
        """Oracle: the two sides carry the same resolved ``EntityId``, so
        they *are* the same declaration -- independent of how either side
        chose to render it. The pre-migration string join missed every one
        of these (asserted directly below), which is a false negative: a
        genuinely invisible layout change on an opaque handle got reported.
        """
        snap = _snap([_opaque(record_name, _STABLE_ID)])
        change = _size_change(change_symbol, _STABLE_ID)

        # The spelling tier alone does not join these two renderings.
        index = _find_opaque_types(snap)
        assert snapshot_local_identity(change_symbol) not in index.local

        # The stable tier does.
        assert _survivors([change], snap, snap) == []

    def test_differing_stable_identities_never_merge_on_a_shared_spelling(
        self,
    ) -> None:
        """The converse direction: an identical rendered spelling is not
        enough to *add* a match when the two resolved identities disagree
        -- the stable tier answers, and it answers no. (The spelling tier
        still fires here; see the collision note below for why that is a
        documented, still-open gap rather than something this test can
        assert away.)"""
        assert stable_entity_id(_STABLE_ID) != stable_entity_id(_OTHER_STABLE_ID)
        index = _find_opaque_types(_snap([_opaque("Handle", _STABLE_ID)]))
        assert stable_entity_id(_OTHER_STABLE_ID) not in index.stable


# -- Behavior preservation for everything that already worked --------------


class TestSpellingTierIsUnchanged:
    def test_identity_free_snapshots_behave_exactly_as_before(self) -> None:
        snap = _snap([_opaque("Handle")])
        assert _survivors([_size_change("Handle")], snap, snap) == []
        assert _survivors([_size_change("Other")], snap, snap) == ["Other"]

    def test_a_change_carrying_a_stable_id_still_falls_back_to_its_spelling(
        self,
    ) -> None:
        """A stable-tier *miss* must not stop the spelling tier from
        answering. Treating the stable tier as authoritative would drop
        this suppression whenever the two sides' producers disagree about
        whether an identity was resolved at all."""
        snap = _snap([_opaque("Handle")])  # no entity_id on the declaration
        assert _survivors([_size_change("Handle", _STABLE_ID)], snap, snap) == []

    def test_a_type_opaque_on_only_one_side_is_not_suppressed(self) -> None:
        old = _snap([_opaque("Handle", _STABLE_ID)])
        new = _snap(
            [
                RecordType(
                    name="Handle", kind="struct", entity_id=_STABLE_ID, size_bits=128
                )
            ]
        )
        assert _survivors([_size_change("Handle", _STABLE_ID)], old, new) == ["Handle"]

    def test_non_structural_kinds_are_never_touched(self) -> None:
        from abicheck.diff_helpers import make_change

        snap = _snap([_opaque("Handle", _STABLE_ID)])
        change = make_change(
            ChangeKind.FUNC_REMOVED,
            symbol="Handle",
            description="Function removed: Handle",
            entity_id=_STABLE_ID,
        )
        assert _survivors([change], snap, snap) == ["Handle"]


class TestKnownGapStaysDocumented:
    def test_bare_name_collision_across_scopes_is_still_reachable(self) -> None:
        """**Documented, still-open** (see
        ``_downgrade_opaque_type_changes``'s own docstring): two unrelated
        types sharing a leaf spelling in different scopes still collide
        through the spelling tier when the change carries no stable
        identity of its own. Pinned as a test so the gap is executable
        rather than prose -- change this assertion when the stable tier is
        made authoritative, do not delete it.
        """
        snap = _snap([_opaque("Handle", _STABLE_ID)])
        # A finding about `other::Handle`, rendered bare, with no resolved
        # identity to distinguish it.
        assert _survivors([_size_change("Handle")], snap, snap) == []

    def test_the_entity_kind_vocabulary_is_the_one_shared_enum(self) -> None:
        assert _STABLE_ID.kind is EntityKind.TYPE


class TestByValueExposureAcrossAQualificationMismatch:
    """Regression for the Codex review on PR #1041, end to end through
    :func:`_find_opaque_types`: a public by-value parameter exposing a
    ``RecordType`` must keep it out of ``opaque`` -- and therefore keep a
    real finding about it unsuppressed -- even when the parameter's
    rendered type text spells the record bare while ``RecordType.name``
    is qualified. Before the by-value scan's own leaf-spelling widening,
    this exposure went undetected, and once the stable tier could reliably
    join the two sides despite the same qualification mismatch, that missed
    exposure turned into a real, silent false-negative suppression.
    """

    def test_a_by_value_exposure_is_not_masked_by_the_qualification_mismatch(
        self,
    ) -> None:
        from abicheck.model import Function, Param, Visibility

        record = RecordType(
            name="ns::Handle", kind="struct", is_opaque=True, entity_id=_STABLE_ID
        )
        func = Function(
            name="use_handle",
            mangled="use_handle",
            return_type="void",
            params=[Param(name="h", type="Handle", pointer_depth=0)],
            visibility=Visibility.PUBLIC,
        )
        snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0.0",
            types=[record],
            functions=[func],
        )
        # The type is by-value exposed, so it must never enter `opaque` --
        # regardless of which identity tier a later join would use.
        index = _find_opaque_types(snap)
        assert not index

        change = _size_change("ns::Handle", _STABLE_ID)
        assert _survivors([change], snap, snap) == ["ns::Handle"]


def test_find_by_value_types_array_subscript_relational_angle_is_not_a_bracket():
    """Regression for the sixth-round Codex review on PR #1041: an
    array-subscript comparison (`arr[1 > 0]`) needs no surrounding parens
    to be valid C++, unlike a bare relational non-type template argument,
    so tracking parenthesis nesting alone still let this shape's stray
    `>` close the outer template one `>` early, leaving the
    genuinely-nested `&h` wrongly read as top-level indirection. Square-
    bracket nesting is now tracked the same way parenthesis nesting is."""
    template_spelling = "S<arr[1 > 0], &h>"
    opaque = {"S"}
    snap = _snap(
        [RecordType(name="S", kind="struct", is_opaque=True)],
    )
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        types=snap.types,
        functions=[
            Function(
                name="f",
                mangled="f",
                return_type=template_spelling,
                params=[Param(name="p", type=template_spelling, pointer_depth=0)],
            )
        ],
        variables=[Variable(name="g", mangled="g", type=template_spelling)],
    )
    assert "S" in _find_by_value_types(snap, opaque)


def test_find_by_value_types_quoted_literal_angle_is_not_a_bracket():
    """Regression for the seventh-round Codex review on PR #1041: a quoted
    character literal used as a non-type template argument (`S<'>', &h>`,
    valid C++, retained verbatim by clang) has the identical problem one
    level down from the parenthesized/bracketed relational cases: the `>`
    inside the literal sits at neither paren nor bracket depth, so it
    still closed the outer template one `>` early, leaving the
    genuinely-nested `&h` wrongly read as top-level indirection. Quoted
    text is now skipped outright by the shared
    `iter_top_level_chars` primitive."""
    template_spelling = "S<'>', &h>"
    opaque = {"S"}
    snap = _snap(
        [RecordType(name="S", kind="struct", is_opaque=True)],
    )
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        types=snap.types,
        functions=[
            Function(
                name="f",
                mangled="f",
                return_type=template_spelling,
                params=[Param(name="p", type=template_spelling, pointer_depth=0)],
            )
        ],
        variables=[Variable(name="g", mangled="g", type=template_spelling)],
    )
    assert "S" in _find_by_value_types(snap, opaque)
