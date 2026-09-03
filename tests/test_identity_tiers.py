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

"""Primitive-level property tests for ``abicheck.model.identity_tiers``
(ADR-063 Phase 2's two-tier post-parse identity) -- AGENTS.md's own
"Primitive-level property tests" doctrine.

The invariants stated here are the ones a *consumer* rests on, generated
over the whole input space rather than pinned to one hand-picked shape:
the two tiers never compare equal, the stable tier can never be entered
without the stability gate, the fallback tier's equality ignores its
``EntityId`` payload, and the keys of the two tiers never collide.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.model.identity import (
    Anonymous,
    EntityId,
    EntityKind,
    InlineNamespace,
    LocalToFunction,
    Namespace,
    Record,
    ScopeSegment,
)
from abicheck.model.identity_stability import entity_id_is_cross_snapshot_stable
from abicheck.model.identity_tiers import (
    SnapshotLocalIdentity,
    StableEntityId,
    resolve_identity,
    snapshot_local_identity,
    stable_entity_id,
)

# -- Strategies (deliberately mirroring tests/test_identity_stability.py's,
# so the two suites cover the same input space from both sides) ------------

_stable_segments = st.one_of(
    st.builds(Namespace, name=st.text(min_size=1, max_size=8)),
    st.builds(Record, name=st.text(min_size=1, max_size=8)),
    st.builds(
        InlineNamespace,
        name=st.text(min_size=1, max_size=8),
        version_tag=st.text(max_size=4),
    ),
)

_leaf_entity_id = st.builds(
    EntityId,
    scope=st.just(()),
    kind=st.sampled_from(list(EntityKind)),
    leaf_name=st.text(min_size=1, max_size=8),
)

_unstable_segments = st.one_of(
    st.builds(
        Anonymous,
        kind=st.sampled_from(["struct", "union", "enum", "namespace"]),
        ordinal=st.integers(min_value=0, max_value=10),
    ),
    st.builds(
        LocalToFunction,
        owner=_leaf_entity_id,
        block_ordinal=st.integers(min_value=0, max_value=10),
    ),
)


@st.composite
def _scope_paths(draw, *, unstable: bool) -> tuple[ScopeSegment, ...]:
    prefix = draw(st.lists(_stable_segments, max_size=3))
    suffix = draw(st.lists(_stable_segments, max_size=3))
    if not unstable:
        return tuple(prefix + suffix)
    return tuple(prefix + [draw(_unstable_segments)] + suffix)


def _entity_id(scope: tuple[ScopeSegment, ...]) -> EntityId:
    return EntityId(scope=scope, kind=EntityKind.TYPE, leaf_name="x")


_any_entity_id = st.one_of(
    _scope_paths(unstable=False).map(_entity_id),
    _scope_paths(unstable=True).map(_entity_id),
)

_spellings = st.text(min_size=0, max_size=12)


# -- The stable tier's gate ------------------------------------------------


class TestStableEntityIdGate:
    @given(entity_id=_any_entity_id)
    def test_wrapping_agrees_with_the_stability_predicate_exactly(
        self, entity_id: EntityId
    ) -> None:
        """``stable_entity_id`` is exactly ``entity_id_is_cross_snapshot_
        stable`` lifted to a type: it wraps iff the predicate admits, for
        every input. This is the property that lets a consumer holding a
        ``StableEntityId`` skip re-checking -- if the two could ever
        disagree, the type would be carrying a guarantee it does not have.
        """
        wrapped = stable_entity_id(entity_id)
        assert (wrapped is not None) == entity_id_is_cross_snapshot_stable(entity_id)
        if wrapped is not None:
            assert wrapped.entity_id == entity_id

    def test_absent_entity_id_is_not_stable(self) -> None:
        assert stable_entity_id(None) is None

    @given(scope=_scope_paths(unstable=True))
    def test_no_unstable_ordinal_can_enter_the_stable_tier(
        self, scope: tuple[ScopeSegment, ...]
    ) -> None:
        """The whole point of the split: a parse-order ordinal anywhere in
        the scope chain -- at any depth, surrounded by any number of stable
        segments -- can never be wrapped as cross-release comparable. There
        is deliberately no second constructor that skips this."""
        assert stable_entity_id(_entity_id(scope)) is None

    @given(entity_id=_any_entity_id)
    def test_anonymous_self_extra_marker_is_refused_regardless_of_scope(
        self, entity_id: EntityId
    ) -> None:
        """An *anonymous declaration's own* ordinal lives in ``extra``, not
        in ``scope`` -- an entirely stable scope path does not rescue it."""
        marked = EntityId(
            scope=entity_id.scope,
            kind=entity_id.kind,
            leaf_name="",
            extra=("anonymous", "3"),
        )
        assert stable_entity_id(marked) is None


# -- Tier separation -------------------------------------------------------


class TestTiersNeverCollide:
    @given(entity_id=_scope_paths(unstable=False).map(_entity_id), spelling=_spellings)
    def test_the_two_tiers_never_compare_equal(
        self, entity_id: EntityId, spelling: str
    ) -> None:
        """Even for the *same* declaration, and even when the fallback tier
        carries that declaration's own ``EntityId`` as its payload, the two
        tiers are never equal. A set of one tier can therefore never be
        satisfied by a lookup in the other -- which is what stops a
        snapshot-local ordinal from silently answering a cross-release
        question.
        """
        stable = stable_entity_id(entity_id)
        assert stable is not None
        local = snapshot_local_identity(spelling, entity_id)
        assert stable != local
        assert local != stable
        assert {stable} & {local} == set()

    @given(entity_id=_scope_paths(unstable=False).map(_entity_id), spelling=_spellings)
    def test_flat_keys_never_collide_across_tiers(
        self, entity_id: EntityId, spelling: str
    ) -> None:
        """The tier tag survives flattening to a string, so a consumer that
        keys a plain ``dict``/``set`` by ``.key`` keeps the same separation
        the type system gives it."""
        stable = stable_entity_id(entity_id)
        assert stable is not None
        assert stable.key != snapshot_local_identity(spelling, entity_id).key

    @given(a=_spellings, b=_spellings)
    def test_snapshot_local_keys_are_injective_over_spellings(
        self, a: str, b: str
    ) -> None:
        assert (snapshot_local_identity(a).key == snapshot_local_identity(b).key) == (
            a == b
        )


# -- The fallback tier's identity-vs-payload split -------------------------


class TestSnapshotLocalIdentityPayload:
    @given(spelling=_spellings, entity_id=_any_entity_id)
    def test_entity_id_payload_never_affects_equality_or_hash(
        self, spelling: str, entity_id: EntityId
    ) -> None:
        """Load-bearing, not cosmetic: an entity whose anonymous ordinal
        shifted between two parses must still match itself by spelling --
        that is the entire fallback this tier provides. Folding the
        ``EntityId`` into equality would make the fallback fail in exactly
        the case it exists for.
        """
        bare = snapshot_local_identity(spelling)
        carried = snapshot_local_identity(spelling, entity_id)
        assert bare == carried
        assert hash(bare) == hash(carried)
        assert {bare, carried} == {bare}
        assert carried.entity_id == entity_id

    @given(a=_spellings, b=_spellings, entity_id=_any_entity_id)
    def test_differing_spellings_never_merge_however_the_payload_agrees(
        self, a: str, b: str, entity_id: EntityId
    ) -> None:
        if a == b:
            return
        assert snapshot_local_identity(a, entity_id) != snapshot_local_identity(
            b, entity_id
        )

    @given(entity_id=_any_entity_id, spelling=_spellings)
    def test_a_perfectly_stable_entity_id_is_not_silently_promoted(
        self, entity_id: EntityId, spelling: str
    ) -> None:
        """``snapshot_local_identity`` returns the fallback tier for every
        input, including a stable ``EntityId``. Which tier a consumer gets
        must depend on what it asked for, never on the input's shape --
        otherwise a consumer's precedence order changes silently with its
        data."""
        assert isinstance(
            snapshot_local_identity(spelling, entity_id), SnapshotLocalIdentity
        )


# -- resolve_identity ------------------------------------------------------


class TestResolveIdentity:
    @given(entity_id=_any_entity_id, spelling=_spellings)
    def test_returns_the_strongest_available_tier(
        self, entity_id: EntityId, spelling: str
    ) -> None:
        resolved = resolve_identity(entity_id=entity_id, spelling=spelling)
        if entity_id_is_cross_snapshot_stable(entity_id):
            assert resolved == StableEntityId(entity_id)
        else:
            assert resolved == SnapshotLocalIdentity(spelling)

    @given(spelling=_spellings)
    def test_no_entity_id_always_falls_back_to_the_spelling(
        self, spelling: str
    ) -> None:
        """The DWARF/PE/Mach-O-only case: no backend resolves an
        ``EntityId`` at all, so every declaration lands in the fallback
        tier and consumers keep working on exactly the spelling they used
        before this split existed."""
        assert resolve_identity(entity_id=None, spelling=spelling) == (
            SnapshotLocalIdentity(spelling)
        )

    @given(entity_id=_any_entity_id, spelling=_spellings)
    def test_is_a_pure_function_of_its_inputs(
        self, entity_id: EntityId, spelling: str
    ) -> None:
        first = resolve_identity(entity_id=entity_id, spelling=spelling)
        second = resolve_identity(entity_id=entity_id, spelling=spelling)
        assert first == second
        assert hash(first) == hash(second)
