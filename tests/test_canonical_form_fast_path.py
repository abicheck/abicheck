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

from abicheck.storage.canonical import canonical_form


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
