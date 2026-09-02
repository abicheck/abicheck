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

"""Primitive-level property tests for
``abicheck.model.identity_stability.entity_id_is_cross_snapshot_stable``
(ADR-063 Phase 2's ``entity:`` promotion gate) -- AGENTS.md's own
"Primitive-level property tests" doctrine: state the primitive's contract
as invariants over the whole input space, not just the one shape a
hand-written example happens to cover.
"""

from __future__ import annotations

import pytest
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

# -- Strategies --------------------------------------------------------

_stable_segments = st.one_of(
    st.builds(Namespace, name=st.text(min_size=1, max_size=8)),
    st.builds(Record, name=st.text(min_size=1, max_size=8)),
    st.builds(
        InlineNamespace,
        name=st.text(min_size=1, max_size=8),
        version_tag=st.text(max_size=4),
    ),
)


def _local_to_function(owner: EntityId, block_ordinal: int) -> LocalToFunction:
    return LocalToFunction(owner=owner, block_ordinal=block_ordinal)


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
        _local_to_function, owner=_leaf_entity_id, block_ordinal=st.integers(0, 10)
    ),
)


def _entity_id_from_scope(scope: tuple[ScopeSegment, ...]) -> EntityId:
    return EntityId(scope=scope, kind=EntityKind.TYPE, leaf_name="x")


@st.composite
def _scope_paths(draw, *, unstable: bool) -> tuple[ScopeSegment, ...]:
    stable_prefix = draw(st.lists(_stable_segments, max_size=4))
    stable_suffix = draw(st.lists(_stable_segments, max_size=4))
    if not unstable:
        return tuple(stable_prefix + stable_suffix)
    unstable_segment = draw(_unstable_segments)
    return tuple(stable_prefix + [unstable_segment] + stable_suffix)


# -- Properties ----------------------------------------------------------


class TestEntityIdIsCrossSnapshotStableProperties:
    @given(scope=_scope_paths(unstable=False))
    def test_true_whenever_every_segment_is_stable(self, scope) -> None:
        """No ``Anonymous``/``LocalToFunction`` segment anywhere in scope,
        and no anonymous-self ``extra`` marker -> always stable. This is
        the common case (an ordinary namespace/record nesting) and must
        never be a false negative, or every real cross-snapshot match
        through an ordinary scope would be needlessly refused.
        """
        entity_id = _entity_id_from_scope(scope)
        assert entity_id_is_cross_snapshot_stable(entity_id)

    @given(scope=_scope_paths(unstable=True))
    def test_false_whenever_any_segment_is_unstable(self, scope) -> None:
        """A single ``Anonymous``/``LocalToFunction`` segment anywhere in
        scope -- regardless of how many stable segments surround it, and
        regardless of position -- makes the whole ``EntityId`` unstable.
        Position-independence matters: an ordinal shift anywhere in the
        chain changes the packed ``key`` for every entity nested under it.
        """
        entity_id = _entity_id_from_scope(scope)
        assert not entity_id_is_cross_snapshot_stable(entity_id)

    @given(
        ordinal=st.integers(min_value=0, max_value=50),
        leaf_name=st.text(max_size=0),  # always "" here
    )
    def test_false_for_anonymous_self_extra_regardless_of_scope(
        self, ordinal, leaf_name
    ) -> None:
        """The anonymous-self ``extra`` marker (an anonymous record/enum's
        own identity, not a segment of its *containing* scope) makes the
        entity unstable even when every scope segment above it is stable
        -- the instability is carried on ``extra``, not ``scope``, so a
        scope-only check would silently miss it.
        """
        entity_id = EntityId(
            scope=(Namespace(name="ns"),),
            kind=EntityKind.TYPE,
            leaf_name=leaf_name,
            extra=("anonymous", str(ordinal)),
        )
        assert not entity_id_is_cross_snapshot_stable(entity_id)

    def test_true_for_the_empty_scope(self) -> None:
        """A top-level (global-scope) entity has no segments at all --
        vacuously stable, and must not raise or misclassify."""
        entity_id = EntityId(scope=(), kind=EntityKind.FUNCTION, leaf_name="f")
        assert entity_id_is_cross_snapshot_stable(entity_id)

    @given(scope=_scope_paths(unstable=False))
    def test_pure_function_of_its_input(self, scope) -> None:
        """Two calls on an equal (but not identical) ``EntityId`` agree --
        no hidden state, no dependence on object identity."""
        a = _entity_id_from_scope(scope)
        b = _entity_id_from_scope(tuple(scope))
        assert a == b
        assert entity_id_is_cross_snapshot_stable(
            a
        ) == entity_id_is_cross_snapshot_stable(b)


class TestEntityIdIsCrossSnapshotStableExamples:
    def test_nested_local_to_function_owner_recursion_is_unstable(self) -> None:
        """A ``LocalToFunction`` segment whose own ``owner`` is itself a
        deeply-scoped ``EntityId`` is still just one unstable segment in
        the outer scope -- recursion into ``owner`` is not required (and
        not attempted): the outer entity is unstable purely because
        ``LocalToFunction`` itself appears in its own ``scope``.
        """
        owner = EntityId(
            scope=(Namespace(name="ns"),), kind=EntityKind.FUNCTION, leaf_name="f"
        )
        entity_id = EntityId(
            scope=(LocalToFunction(owner=owner, block_ordinal=0),),
            kind=EntityKind.TYPE,
            leaf_name="A",
        )
        assert not entity_id_is_cross_snapshot_stable(entity_id)

    def test_named_declaration_in_anonymous_namespace_is_unstable(self) -> None:
        """A *named* declaration inside an anonymous namespace is exactly
        the case ``Record``/``Namespace`` nesting is meant to cover on its
        own once the immediate anonymous scope is resolved -- but the
        anonymous namespace segment itself still makes the whole chain
        unstable, since the ordinal it carries can still shift.
        """
        entity_id = EntityId(
            scope=(Anonymous(kind="namespace", ordinal=0), Namespace(name="ns")),
            kind=EntityKind.TYPE,
            leaf_name="Named",
        )
        assert not entity_id_is_cross_snapshot_stable(entity_id)

    @pytest.mark.parametrize("leaf_name", ["", "Named"])
    def test_anonymous_self_extra_only_applies_when_leaf_name_empty(
        self, leaf_name
    ) -> None:
        """``extra=("anonymous", "0")`` is only ever produced for an empty
        ``leaf_name`` in real usage (see ``_anonymous_self_extra``), but
        the predicate itself checks ``extra`` directly and doesn't care
        why it's there -- it is unstable either way once ``extra`` starts
        with the marker.
        """
        entity_id = EntityId(
            scope=(),
            kind=EntityKind.TYPE,
            leaf_name=leaf_name,
            extra=("anonymous", "0"),
        )
        assert not entity_id_is_cross_snapshot_stable(entity_id)
