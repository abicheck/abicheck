# SPDX-License-Identifier: Apache-2.0
"""`canonical_form`'s exact-type fast paths agree with an independent
reference canonicalizer (sort every mapping by key, every sequence to a
list) on generated trees -- sorted and unsorted keys, tuples, str/int
subclasses -- so skipping the sort for already-ascending keys can never
change the output."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, strategies as st

from abicheck.storage.canonical import canonical_form, is_canonical_tree


class _Key(str):
    pass


def _reference(value):
    if isinstance(value, dict):
        return {str(k): _reference(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_reference(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


_scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.text(max_size=4)
    | st.floats(allow_nan=False, allow_infinity=False)
)
_keys = st.text(max_size=3) | st.text(max_size=3).map(_Key)
_trees = st.recursive(
    _scalars,
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.lists(inner, max_size=4).map(tuple)
        | st.dictionaries(_keys, inner, max_size=5)
    ),
    max_leaves=30,
)


@given(_trees)
def test_matches_reference_in_value_and_key_order(tree):
    got = canonical_form(tree)
    want = _reference(tree)
    assert got == want
    assert json.dumps(got) == json.dumps(want)  # key order included


@pytest.mark.parametrize("bad", [{1: "a"}, {"a": {2: None}}, ({b"x": 1},)])
def test_non_str_keys_still_rejected(bad):
    with pytest.raises(TypeError):
        canonical_form(bad)


_json_leaves = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.text(max_size=4)
    | st.floats(allow_nan=True, allow_infinity=True)
)
_json_trees = st.recursive(
    _json_leaves,
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.lists(inner, max_size=4).map(tuple)
        | st.dictionaries(_keys, inner, max_size=5)
    ),
    max_leaves=30,
)


def _typed(value):
    """*value* with every node's exact type, so equality also compares
    `1` against `1.0`, `True` against `1`, a tuple against a list and a
    `str` subclass against `str` -- distinctions `==` alone erases."""
    if isinstance(value, dict):
        return (type(value), [(type(k), k, _typed(v)) for k, v in value.items()])
    if isinstance(value, (list, tuple)):
        return (type(value), [_typed(v) for v in value])
    return (type(value), value)


@given(_json_trees)
def test_is_canonical_tree_only_accepts_canonical_forms_own_output(tree):
    # Soundness: a tree the check accepts can stand in for canonical_form's
    # result -- same values, key order and leaf types.
    if is_canonical_tree(tree):
        assert _typed(canonical_form(tree)) == _typed(tree)


@given(_trees)
def test_is_canonical_tree_accepts_every_canonical_document_read_back(tree):
    # Completeness where it matters: anything a canonical store wrote and a
    # reader parsed back passes, so the copy really is skipped on load.
    read_back = json.loads(json.dumps(canonical_form(tree)))
    assert is_canonical_tree(read_back)
