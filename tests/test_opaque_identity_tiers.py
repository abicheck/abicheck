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
