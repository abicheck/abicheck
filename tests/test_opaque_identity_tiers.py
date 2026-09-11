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
from hypothesis import given, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.compare.opaque_types import OpaqueTypeIndex, find_opaque_struct_types
from abicheck.diff_filtering import (
    _downgrade_opaque_struct_changes,
    _downgrade_opaque_type_changes,
    _find_by_value_types,
    _find_opaque_types,
    _resolve_struct_change_entity_id,
    _struct_change_record_name,
)
from abicheck.model import AbiSnapshot, Function, Param, RecordType, TypeField, Variable
from abicheck.model.identity import (
    Anonymous,
    EntityKind,
    Namespace,
    Record,
    entity_id_for_type,
)
from abicheck.model.identity_stability import entity_id_is_cross_snapshot_stable
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
    def test_bare_name_collision_is_still_reachable_when_the_change_has_no_identity(
        self,
    ) -> None:
        """**Documented, still-open** (see ``OpaqueTypeIndex.contains``'s
        own docstring): narrowing (below) closes the bare-``RecordType.name``
        collision for a change that carries its own resolvable stable
        identity -- but a change with no ``entity_id`` at all has nothing
        for ``strict`` to narrow: it falls straight through to the spelling
        tier, collision and all, exactly as before. Pinned as a test so the
        residual gap is executable rather than prose -- change this
        assertion only if a producer starts stamping every structural-type
        ``Change`` with an ``entity_id`` unconditionally, do not delete it.
        """
        snap = _snap([_opaque("Handle", _STABLE_ID)])
        # A finding about `other::Handle`, rendered bare, with no resolved
        # identity to distinguish it.
        assert _survivors([_size_change("Handle")], snap, snap) == []

    def test_the_entity_kind_vocabulary_is_the_one_shared_enum(self) -> None:
        assert _STABLE_ID.kind is EntityKind.TYPE


class TestBareNameCollisionNarrowing:
    """ADR-063 Phase 2's closing slice: a change carrying its own resolvable
    stable identity is no longer masked by an unrelated opaque declaration
    that merely shares its bare leaf spelling -- the collision
    ``TestKnownGapStaysDocumented`` above still documents for a change with
    no identity at all, closed here for the (real, ``diff_types.py``-typical)
    case where the change does carry one.
    """

    def test_a_distinct_type_sharing_a_bare_name_is_no_longer_masked(self) -> None:
        """The real bug class this narrowing closes. ``ns1::Handle`` is
        opaque; ``ns2::Handle`` is a different, non-opaque declaration that
        happens to share the bare leaf spelling ``"Handle"``. Before this
        slice, a genuine structural change on ``ns2::Handle`` was wrongly
        suppressed through the spelling tier's bare-name collision -- even
        though its own layout is fully visible to consumers. The change
        below carries ``ns2::Handle``'s own identity, exactly as
        ``diff_types.py``'s real ``entity_id=t_old.entity_id or
        t_new.entity_id`` always does.
        """
        ns1_id = entity_id_for_type((Namespace("ns1"),), "Handle")
        ns2_id = entity_id_for_type((Namespace("ns2"),), "Handle")
        snap = _snap([_opaque("Handle", ns1_id)])  # only ns1::Handle is opaque
        change = _size_change("Handle", ns2_id)  # a change about the OTHER Handle
        assert _survivors([change], snap, snap) == ["Handle"]

    def test_narrowing_declines_when_either_side_is_incomplete(self) -> None:
        """Completeness gates narrowing per *comparison*, not per
        declaration: when ``ns1::Handle`` resolved no stable identity on one
        side (a mixed-producer comparison, or one side loaded from a
        pre-``entity_id``-population archived baseline), ``ns2::Handle``'s
        own change must still fall back to the permissive spelling tier
        rather than let an incomplete ``stable`` set stand in as proof of
        non-opacity -- the exact live false-positive risk
        ``OpaqueTypeIndex.complete``'s own docstring names. This is provably
        the same (safe, collision-prone) behavior as before this slice,
        not a regression: completeness is what makes narrowing an
        *additional*, gated capability rather than a change to the default.
        """
        ns1_id = entity_id_for_type((Namespace("ns1"),), "Handle")
        ns2_id = entity_id_for_type((Namespace("ns2"),), "Handle")
        old = _snap([_opaque("Handle", ns1_id)])
        new = _snap([_opaque("Handle")])  # same declaration, unresolved here
        change = _size_change("Handle", ns2_id)
        assert _survivors([change], old, new) == []

    def test_a_genuine_edit_on_the_opaque_type_itself_still_reports(self) -> None:
        """Narrowing must never turn a *hit* into anything but a hit --
        this only ever changes what happens on a *miss*. A change that
        really is about the opaque declaration is suppressed exactly as
        before, whether or not the comparison happens to be complete."""
        snap = _snap([_opaque("Handle", _STABLE_ID)])
        assert _survivors([_size_change("Handle", _STABLE_ID)], snap, snap) == []

    def test_disagreeing_stable_ids_for_the_same_spelling_do_not_go_strict(
        self,
    ) -> None:
        """Regression for the Codex review on PR #1045: presence alone
        ("did each side resolve *something*?") is not completeness. Both
        sides here individually resolve a stable id for their own
        ``ns1::Handle`` declaration -- so a naive whole-index "every
        declaration resolved something" flag reads ``True`` on both sides --
        but the two ids *disagree* (mirroring two producers that scope the
        same enclosing declaration differently, e.g. namespace vs. record).
        ``self.stable & other.stable`` holds no match for it, yet the
        *local* (spelling) tier still correctly proves ``ns1::Handle`` is
        opaque on both sides. A completeness signal computed from bare
        presence would still go strict here and treat the resulting
        stable-tier miss on a change about that SAME (still-opaque)
        declaration as proof of non-opacity -- silently un-suppressing a
        genuinely invisible layout change. Paired completeness must decline
        instead, exactly as when one side resolves nothing at all.
        """
        ns1_id_old = entity_id_for_type((Namespace("ns1"),), "Handle")
        ns1_id_new = entity_id_for_type((Record("ns1"),), "Handle")
        assert ns1_id_old != ns1_id_new  # the two producers genuinely disagree
        old = _snap([_opaque("Handle", ns1_id_old)])
        new = _snap([_opaque("Handle", ns1_id_new)])
        # A change about the SAME still-opaque declaration -- old-preferred
        # entity_id, exactly as diff_types.py's real `t_old.entity_id or
        # t_new.entity_id` always resolves it.
        change = _size_change("Handle", ns1_id_old)
        assert _survivors([change], old, new) == []

    def test_partial_pairing_among_colliding_declarations_does_not_go_strict(
        self,
    ) -> None:
        """Regression for the Codex review on PR #1045, second round, fresh
        evidence: the single-declaration case above is not the whole story.
        Two *distinct* opaque declarations, ``ns1::Handle`` and
        ``ns2::Handle``, genuinely collide on the bare spelling
        ``"Handle"``. The two sides agree on ``ns1::Handle``'s id but --
        the same producer-scoping disagreement as above -- disagree on
        ``ns2::Handle``'s. An intersection-based pairing check (this
        module's first fix) sees the *shared* ``ns1`` id, calls the whole
        spelling "paired", and goes strict -- then wrongly trusts a
        stable-tier miss on ``ns2::Handle``'s own still-opaque finding as
        proof of non-opacity. Only exact set equality proves *every* id
        either side resolved for a spelling has a match on the other side,
        which correctly declines here instead.
        """
        ns1_id = entity_id_for_type((Namespace("ns1"),), "Handle")
        ns2_id_old = entity_id_for_type((Namespace("ns2"),), "Handle")
        ns2_id_new = entity_id_for_type((Record("ns2"),), "Handle")
        assert ns2_id_old != ns2_id_new  # the two producers genuinely disagree
        old = _snap([_opaque("Handle", ns1_id), _opaque("Handle", ns2_id_old)])
        new = _snap([_opaque("Handle", ns1_id), _opaque("Handle", ns2_id_new)])
        # A change about ns2::Handle -- still genuinely opaque on both
        # sides, just resolved under disagreeing ids.
        change = _size_change("Handle", ns2_id_old)
        assert _survivors([change], old, new) == []


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


def test_occurrence_is_indirect_recognizes_a_pointer_nested_in_a_function_pointer():
    """Regression for the Codex review on PR #1041 that replaced the old
    whole-text `_is_indirect_spelling` scan with an occurrence-relative
    check: an implementation record named `ns::Handle` referenced only
    through a public function-pointer parameter/return like
    `"void (*)(Handle*)"` must be recognized as pointer-only (not by
    value) -- the `*` genuinely applies to `Handle`, even though it sits
    inside the function-pointer's own nested parameter-list parens, which
    the old whole-text top-level scan wrongly ignored as belonging to a
    different part of the declarator."""
    opaque = {"ns::Handle"}
    template = "void (*)(Handle*)"

    return_snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[Function(name="f", mangled="f", return_type=template)],
    )
    assert _find_by_value_types(return_snap, opaque) == set()

    param_snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[
            Function(
                name="f",
                mangled="f",
                return_type="void",
                params=[Param(name="p", type=template, pointer_depth=0)],
            )
        ],
    )
    assert _find_by_value_types(param_snap, opaque) == set()

    var_snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        variables=[Variable(name="g", mangled="g", type=template)],
    )
    assert _find_by_value_types(var_snap, opaque) == set()


def test_occurrence_is_indirect_recognizes_a_declarator_group_pointer():
    """Regression for the Codex review on PR #1041, follow-up round:
    ``"Handle (*)[3]"`` (pointer to an array of ``Handle``) and
    ``"Handle (*)(int)"`` (pointer to a function returning ``Handle``)
    are both genuinely indirect, even though the ``*``/``&`` itself sits
    inside a declarator-grouping paren rather than immediately after the
    type name -- these parens exist purely to override normal declarator
    precedence (an array/function suffix binds tighter than a bare ``*``
    would), which C emits whenever a plain trailing ``*`` would otherwise
    bind to the wrong part of the declarator. A pointer-to-member
    spelling (``"Handle (Class::*)[3]"``) is covered too."""
    opaque = {"Handle"}
    for template in ("Handle (*)[3]", "Handle (*)(int)", "Handle (Class::*)[3]"):
        var_snap = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0.0",
            variables=[Variable(name="g", mangled="g", type=template)],
        )
        assert _find_by_value_types(var_snap, opaque) == set(), template


def test_occurrence_is_indirect_handles_unbalanced_template_arguments():
    """Defensive-floor coverage for `skip_template_arguments`'s bracket
    stack: an unterminated `<...>` (malformed/adversarial rendered text)
    must not raise or infinite-loop -- degrades to "consumed the rest of
    the text", same discipline `iter_top_level_chars` already holds
    itself to."""
    from abicheck.compare.opaque_types import _occurrence_is_indirect

    assert _occurrence_is_indirect("Handle<unterminated", 6) is False


def test_find_by_value_types_checks_every_occurrence_not_just_the_first():
    """Regression for the Codex review on PR #1041, follow-up round: when
    the same opaque type occurs more than once in one rendered type text
    and the first occurrence is indirect but a later one is by value, the
    by-value occurrence must still be found. `Pair<Handle*, Handle>` has
    `Handle` both as a pointer (first) and by value (second) template
    argument -- the second must not be shadowed by the first."""
    opaque = {"Handle"}
    template = "Pair<Handle*, Handle>"
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[Function(name="f", mangled="f", return_type=template)],
    )
    assert "Handle" in _find_by_value_types(snap, opaque)


def test_find_by_value_types_honors_an_enclosing_pointer():
    """Regression for the Codex review on PR #1041, follow-up round: when
    a matched type is a template argument of an outer type that is itself
    only ever exposed by pointer (`Pair<Handle>*`), the enclosing pointer
    protects the nested occurrence too -- a consumer holding only a
    `Pair<Handle>*` never needs `Handle`'s own layout, since it never
    constructs or copies a `Pair<Handle>` by value."""
    opaque = {"Handle"}
    template = "Pair<Handle>*"
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[Function(name="f", mangled="f", return_type=template)],
    )
    assert _find_by_value_types(snap, opaque) == set()


def test_find_by_value_types_enclosing_pointer_does_not_shield_other_arguments():
    """Complement of the enclosing-pointer fix: `Pair<Handle>*` protects
    `Handle` because `Pair<Handle>` itself is only ever exposed by
    pointer -- it must not also protect an unrelated *sibling* type that
    is genuinely exposed by value elsewhere in the same signature."""
    opaque = {"Handle", "Other"}
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[
            Function(
                name="f",
                mangled="f",
                return_type="void",
                params=[
                    Param(name="p", type="Pair<Handle>*", pointer_depth=1),
                    Param(name="q", type="Other", pointer_depth=0),
                ],
            )
        ],
    )
    found = _find_by_value_types(snap, opaque)
    assert "Handle" not in found
    assert "Other" in found


def test_find_by_value_types_ignores_a_braced_structural_template_argument():
    """Regression for the Codex review on PR #1041, follow-up round: a
    C++20 structural non-type template argument's own braced initializer
    (`S<A{1 < 2}>`, which clang can render verbatim) must not have its
    internal `<` mistaken for a template opener -- `S` itself is by value
    here (no top-level indirection follows the whole `<...>`), and the
    brace's own `<`/`>` must not desynchronize the bracket stack for
    whatever follows."""
    opaque = {"S"}
    template = "S<A{1 < 2}> *"
    snap = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[Function(name="f", mangled="f", return_type=template)],
    )
    # The trailing top-level '*' (after the braced argument closes) makes
    # this a pointer, not a by-value exposure.
    assert _find_by_value_types(snap, opaque) == set()

    template_by_value = "S<A{1 < 2}>"
    snap_by_value = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0.0",
        functions=[Function(name="f", mangled="f", return_type=template_by_value)],
    )
    assert "S" in _find_by_value_types(snap_by_value, opaque)


# -- ADR-063 Phase 10: `_downgrade_opaque_struct_changes`'s own,
# previously-unmigrated bare `set[str]` opaqueness tracker -----------------


def _record(
    name: str, *, is_opaque: bool = False, entity_id=None, fields=()
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        is_opaque=is_opaque,
        entity_id=entity_id,
        fields=list(fields),
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
        out = _downgrade_opaque_struct_changes([_struct_size_change("Op")], old, new)
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

    def test_stable_identity_closes_a_qualification_mismatch(self) -> None:
        """A real false negative the bare-string tracker could not see:
        the change's own ``symbol`` is rendered differently from
        ``RecordType.name`` (bare vs. namespace-qualified), but both
        resolve to the same stable ``EntityId`` -- proof the two sides
        agree on the declaration regardless of spelling."""
        old = _record("Op", is_opaque=True, entity_id=_STABLE_ID)
        new = _record("Op", is_opaque=True, entity_id=_STABLE_ID)
        change = _struct_size_change("ns::Op", entity_id=_STABLE_ID)
        out = _downgrade_opaque_struct_changes([change], _snap([old]), _snap([new]))
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

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
        out = _downgrade_opaque_struct_changes([change], _snap([old]), _snap([new]))
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], old, new)
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], old_snap, new_snap)
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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], old, new)
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
        out = _downgrade_opaque_struct_changes([change], _snap([old]), _snap([new]))
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

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
        out = _downgrade_opaque_struct_changes([change], old_snap, new_snap)
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
        out = _downgrade_opaque_struct_changes([change], _snap([old]), _snap([new]))
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE

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
        out = _downgrade_opaque_struct_changes([change], _snap([old]), _snap([new]))
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE


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
        out = _downgrade_opaque_struct_changes([change], old, new)
        assert out[0].kind == ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE
