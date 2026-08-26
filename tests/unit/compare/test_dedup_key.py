# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Contract tests for `hashable_value`, stated as invariants.

Per the root `AGENTS.md`'s "Primitive-level property tests" guidance, a
reusable dedup/grouping primitive is tested directly rather than only
through its highest-level caller: an example test written to confirm the
fix just made encodes only the input its author already thought of.

That guidance earned its keep here. The first version of this file drew
lists and frozensets but never tuples, so its no-overmerge property never
saw the pair `["a"]` / `("a",)` -- which the implementation collapsed onto
the same key, the precise failure the property exists to forbid. The
strategy below therefore generates *heterogeneous* containers, and
`TestConvertedFormsCannotCollide` pins the specific pairs by hand as well.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, strategies as st

from abicheck.compare.dedup_key import hashable_value

# Leaves a finding's value slot can actually hold. `Change.old_value` is
# annotated `str | None`, but the annotation is not enforced and
# `diff_python.py` stores lists there at seven sites.
_LEAVES = st.none() | st.booleans() | st.integers() | st.text(max_size=3)

# Containers whose *own* equality is exact, so both directions of the
# equal/unequal invariants hold. Deliberately mixes list and tuple at every
# level: a strategy drawing only one of them cannot express the collision
# that the tagging in `hashable_value` exists to prevent.
_EXACT = st.recursive(
    _LEAVES,
    lambda children: st.lists(children, max_size=3)
    | st.lists(children, max_size=3).map(tuple),
    max_leaves=8,
)

_UNHASHABLE = [
    pytest.param(["a", "b"], id="list"),
    pytest.param([], id="empty-list"),
    pytest.param([["nested"]], id="nested-list"),
    pytest.param((["in-a-tuple"],), id="list-inside-tuple"),
    pytest.param({"a": 1}, id="dict"),
    pytest.param({"a", "b"}, id="set"),
]


class TestTheResultIsAlwaysHashable:
    @given(_EXACT)
    def test_any_value_yields_a_hashable_result(self, value: object) -> None:
        hash(hashable_value(value))

    @pytest.mark.parametrize("value", _UNHASHABLE)
    def test_known_unhashable_shapes(self, value: object) -> None:
        hash(hashable_value(value))

    @pytest.mark.parametrize("value", _UNHASHABLE)
    def test_the_result_survives_entering_a_set(self, value: object) -> None:
        assert len({hashable_value(value)}) == 1

    def test_an_object_with_a_raising_hash_still_keys(self) -> None:
        class Hostile:
            def __hash__(self) -> int:
                raise TypeError("unhashable by design")

        hash(hashable_value(Hostile()))


class TestEqualValuesKeyEqually:
    @given(_EXACT)
    def test_a_value_keys_the_same_as_an_equal_copy(self, value: object) -> None:
        assert hashable_value(value) == hashable_value(copy.deepcopy(value))

    def test_repeated_calls_agree(self) -> None:
        value = ["a", ["b"]]
        assert hashable_value(value) == hashable_value(value)


class TestUnequalValuesKeyDifferently:
    """The invariant that matters more: an over-merge drops a real finding."""

    @given(_EXACT, _EXACT)
    def test_unequal_values_never_merge(self, left: object, right: object) -> None:
        if left == right:
            return
        assert hashable_value(left) != hashable_value(right)

    def test_a_list_does_not_collide_with_its_own_string_spelling(self) -> None:
        assert hashable_value(["a"]) != hashable_value("['a']")


class TestConvertedFormsCannotCollide:
    """Each case below was a real collision before the conversions were tagged."""

    def test_a_list_does_not_collide_with_the_equivalent_tuple(self) -> None:
        assert hashable_value(["a"]) != hashable_value(("a",))

    def test_nesting_preserves_the_container_distinction(self) -> None:
        assert hashable_value([["a"]]) != hashable_value([("a",)])

    def test_a_genuine_tuple_cannot_forge_the_fallback(self) -> None:
        """The fallback's shape is `(tag, type_name, repr)`; only the tag is
        unforgeable, which is why it is there."""
        value = {"a": 1}
        assert hashable_value(value) != hashable_value(
            (type(value).__name__, repr(value))
        )

    def test_an_unhashable_object_does_not_collide_with_its_repr(self) -> None:
        value = {"a": 1}
        assert hashable_value(value) != hashable_value(repr(value))

    def test_two_unhashable_types_sharing_a_repr_stay_distinct(self) -> None:
        """The type name is carried for exactly this case."""

        class Odd:
            def __hash__(self) -> int:
                raise TypeError

            def __repr__(self) -> str:
                return "{'a': 1}"

        assert hashable_value(Odd()) != hashable_value({"a": 1})


class TestHashableScalarsArePassedThroughUnchanged:
    @pytest.mark.parametrize("value", [None, "", "text", 0, False, frozenset({"a"})])
    def test_already_hashable_scalars_are_identical(self, value: object) -> None:
        assert hashable_value(value) is value

    def test_a_tuple_is_tagged_rather_than_passed_through(self) -> None:
        """A bare pass-through is what let a tuple collide with a list."""
        value = ("a", "b")
        assert hashable_value(value) is not value
        assert hash(hashable_value(value))
