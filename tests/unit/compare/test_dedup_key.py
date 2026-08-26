# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Contract tests for `hashable_value`, stated as invariants.

Per the root `AGENTS.md`'s "Primitive-level property tests" guidance, a
reusable dedup/grouping primitive is tested directly rather than only
through its highest-level caller: an example test written to confirm the
fix just made encodes only the input its author already thought of.

The three invariants are the ones a dedup key must satisfy:

- **hashable** — a `set` can hold it, which is the whole reason it exists;
- **equal in, equal out** — otherwise a dedup stops deduplicating;
- **unequal in, unequal out** — otherwise a dedup silently *over*-merges
  two genuinely different findings, which is the worse failure of the two.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.compare.dedup_key import hashable_value

# Values a finding's value slot can actually receive. `Change.old_value` is
# annotated `str | None`, but the annotation is not enforced and
# `diff_python.py` stores lists there at seven sites.
_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda children: st.lists(children) | st.frozensets(st.integers()),
    max_leaves=12,
)

_UNHASHABLE = [
    pytest.param(["a", "b"], id="list"),
    pytest.param([], id="empty-list"),
    pytest.param([["nested"]], id="nested-list"),
    pytest.param({"a": 1}, id="dict"),
    pytest.param({"a", "b"}, id="set"),
]


class TestTheResultIsAlwaysHashable:
    @given(_VALUES)
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
    @given(_VALUES)
    def test_a_value_keys_the_same_as_an_equal_copy(self, value: object) -> None:
        import copy

        assert hashable_value(value) == hashable_value(copy.deepcopy(value))

    def test_repeated_calls_agree(self) -> None:
        value = ["a", ["b"]]
        assert hashable_value(value) == hashable_value(value)


class TestUnequalValuesKeyDifferently:
    @given(_VALUES, _VALUES)
    def test_unequal_values_never_merge(self, left: object, right: object) -> None:
        if left == right:
            return
        assert hashable_value(left) != hashable_value(right)

    def test_a_list_does_not_collide_with_its_own_string_spelling(self) -> None:
        """A conversion that flattened to text would merge unequal values."""
        assert hashable_value(["a"]) != hashable_value("['a']")

    def test_an_unhashable_object_does_not_collide_with_its_repr(self) -> None:
        """The fallback is type-tagged precisely so this cannot collide."""
        value = {"a": 1}
        assert hashable_value(value) != hashable_value(repr(value))


class TestHashableValuesArePassedThroughUnchanged:
    @pytest.mark.parametrize("value", [None, "", "text", 0, False, ("a", "b")])
    def test_already_hashable_values_are_identical(self, value: object) -> None:
        assert hashable_value(value) is value

    def test_a_list_becomes_a_tuple_rather_than_text(self) -> None:
        assert hashable_value(["a", "b"]) == ("a", "b")

    def test_nesting_is_converted_recursively(self) -> None:
        assert hashable_value([["a"], ["b"]]) == (("a",), ("b",))
