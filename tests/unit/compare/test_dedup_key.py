# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Contract tests for `hashable_value`, stated as invariants.

Per the root `AGENTS.md`'s "Primitive-level property tests" guidance, a
reusable dedup/grouping primitive is tested directly rather than only
through its highest-level caller: an example test written to confirm the
fix just made encodes only the input its author already thought of.

That guidance earned its keep here twice, in both directions a property
can fail. First the strategy drew lists and frozensets but never tuples,
so the no-overmerge property never saw the pair `["a"]` / `("a",)`, which
the implementation collapsed onto one key. Then it drew no `nan` and no
mappings, so it never saw two unequal `{"v": nan}` dicts, which the `repr`
fallback also collapsed. Neither property was wrong; both were starved of
the input that falsifies them, which reads exactly like coverage.

A third round found the same starvation for sets specifically (Codex
review, PR #905): the recursive strategy generated lists, tuples, and
dicts at every level but no sets/frozensets at all -- only a single,
fixed top-level `{"a", "b"}` example in `_UNHASHABLE`, which checks
totality and nothing else. Nested sets, structurally-equal set copies,
and set-order invariance were therefore untested. Fixed by giving the
recursive step its own frozenset branch (frozensets, not plain sets, so a
generated set can itself legally nest inside an outer set/list/tuple/dict
the way the other three container kinds already do); `_ensure_hashable`
converts a drawn child to something Python can actually put in a
`frozenset` before doing so, which is a generation-time constraint of
`hypothesis`/`set` itself, not a statement about what `hashable_value`
accepts.

The strategy below therefore mixes container *types* at every nesting
level and includes `nan` among its leaves -- the one value whose
inequality with itself makes structural encoding observable. Opaque
values have their own class, because they satisfy a deliberately weaker
contract: see `TestOpaqueValuesKeyByIdentity`.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, strategies as st

from abicheck.compare.dedup_key import hashable_value

# Leaves a finding's value slot can actually hold. `Change.old_value` is
# annotated `str | None`, but the annotation is not enforced and
# `diff_python.py` stores lists there at seven sites.
# `nan` is included deliberately: it is unequal to itself, so it is the
# leaf that distinguishes a structural encoding from one that flattens to
# text. A `repr`-based fallback keys two unequal `nan`-bearing values
# identically; a structural one does not.
_LEAVES = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.text(max_size=3)
    | st.floats(allow_nan=True)
)


def _ensure_hashable(value: object) -> object:
    """Make a drawn child safe to put in a `frozenset`.

    A generation-time constraint of Python's own `set`/`frozenset` (their
    elements must be hashable), not a statement about what `hashable_value`
    itself accepts -- it accepts anything. Recurses the same way
    `hashable_value` does, so the *shape* of what ends up inside the
    frozenset is still a real, recursively-unhashable-until-converted
    value, not a leaf-only stand-in.
    """
    if isinstance(value, list):
        return tuple(_ensure_hashable(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_ensure_hashable(item) for item in value)
    if isinstance(value, dict):
        return frozenset((k, _ensure_hashable(v)) for k, v in value.items())
    if isinstance(value, (set, frozenset)):
        return frozenset(_ensure_hashable(item) for item in value)
    return value


# Containers whose *own* equality is exact, so both directions of the
# equal/unequal invariants hold. Deliberately mixes list, tuple, dict, and
# frozenset at every level: a strategy drawing only some of them cannot
# express the collision that the tagging in `hashable_value` exists to
# prevent.
_EXACT = st.recursive(
    _LEAVES,
    lambda children: (
        st.lists(children, max_size=3)
        | st.lists(children, max_size=3).map(tuple)
        | st.dictionaries(st.text(max_size=3), children, max_size=3)
        | st.lists(children, max_size=3).map(
            lambda xs: frozenset(_ensure_hashable(x) for x in xs)
        )
    ),
    max_leaves=8,
)


class _OpaqueFixture:
    """Unhashable, with no structure to encode: equal to any sibling and
    represented identically, so only identity distinguishes two of them."""

    __hash__ = None  # type: ignore[assignment]

    def __init__(self, text: str) -> None:
        self.text = text

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _OpaqueFixture) and self.text == other.text

    def __repr__(self) -> str:
        return self.text


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

    def test_unequal_values_sharing_a_repr_stay_distinct(self) -> None:
        """Two `nan`-bearing dicts are unequal and print identically."""
        left = {"v": float("nan")}
        right = {"v": float("nan")}
        assert left != right
        assert hashable_value(left) != hashable_value(right)

    def test_the_same_nan_object_still_keys_equally(self) -> None:
        """The mirror case: identity makes these equal, so they must merge."""
        shared = float("nan")
        assert hashable_value([shared]) == hashable_value([shared])


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

    def test_a_mapping_keys_independently_of_member_order(self) -> None:
        """Mappings encode as frozensets, so insertion order never reaches
        the key -- these are equal inputs and must merge."""
        assert hashable_value({"a": 1, "b": 2}) == hashable_value({"b": 2, "a": 1})

    def test_a_set_keys_independently_of_construction_order(self) -> None:
        """Sets are already order-blind by their own equality, but the
        *encoding* must not smuggle insertion order back in regardless."""
        left = set()
        left.add("a")
        left.add("b")
        right = set()
        right.add("b")
        right.add("a")
        assert hashable_value(left) == hashable_value(right)

    def test_a_set_does_not_collide_with_the_equivalent_list(self) -> None:
        assert hashable_value({"a", "b"}) != hashable_value(["a", "b"])

    def test_nesting_preserves_the_set_distinction(self) -> None:
        """A set does not collide with a list/tuple holding the same
        (frozenset-wrapped) elements, one level of nesting down."""
        inner = frozenset({"a"})
        assert hashable_value([inner]) != hashable_value((inner,))
        assert hashable_value({inner}) != hashable_value([inner])


class TestOpaqueValuesKeyByIdentity:
    """A value with no structure to encode satisfies a weaker contract.

    No general encoding of an arbitrary object is injective, so one of the
    two invariants has to give. Identity keeps the one that matters -- it
    can never over-merge -- and sacrifices the other: two equal but distinct
    opaque values key apart, duplicating a finding rather than dropping one.
    """

    @staticmethod
    def _opaque(text: str) -> object:
        # `_Opaque` is defined at module scope, not per call: two instances
        # from separate calls have to be genuinely equal for the
        # never-over-merge case below to test anything.
        return _OpaqueFixture(text)

    def test_an_opaque_value_is_hashable(self) -> None:
        hash(hashable_value(self._opaque("x")))

    def test_the_same_object_keys_equally_every_time(self) -> None:
        value = self._opaque("x")
        assert hashable_value(value) == hashable_value(value)

    def test_equal_but_distinct_opaque_values_never_over_merge(self) -> None:
        """Equal *and* identically represented, so only identity separates
        them -- and it must."""
        left, right = self._opaque("same"), self._opaque("same")
        assert left == right
        assert hashable_value(left) != hashable_value(right)

    def test_an_opaque_key_does_not_collide_with_a_plain_value(self) -> None:
        assert hashable_value(self._opaque("same")) != hashable_value("same")

    def test_the_key_holds_the_value_so_its_id_cannot_be_reused(self) -> None:
        """Identity keying is only sound while the object stays alive."""
        key = hashable_value(self._opaque("x"))
        assert getattr(key, "value", None) is not None


class TestHashableScalarsArePassedThroughUnchanged:
    @pytest.mark.parametrize("value", [None, "", "text", 0, False, 1.5])
    def test_already_hashable_scalars_are_identical(self, value: object) -> None:
        assert hashable_value(value) is value

    def test_a_tuple_is_tagged_rather_than_passed_through(self) -> None:
        """A bare pass-through is what let a tuple collide with a list."""
        value = ("a", "b")
        assert hashable_value(value) is not value
        assert hash(hashable_value(value))
