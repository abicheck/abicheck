# SPDX-License-Identifier: Apache-2.0
"""Blocking keys for rename matching are exact, not a heuristic.

``match_renamed_functions(name_keys=...)`` only consults the name predicate
on partners sharing a key, which is sound exactly when the predicate never
accepts a pair whose key sets are disjoint. Two oracles:

* the key contract itself, checked against ``_plausible_rename`` on every
  pair of a generated name population (exhaustive within each example);
* the matcher's whole answer with keys equals its answer without them.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.binary_fingerprint import FunctionFingerprint, match_renamed_functions
from abicheck.diff_symbols_renames import _plausible_rename, _plausible_rename_keys

_scopes = st.sampled_from(["", "ns::", "ns::v1::", "other::", "ns::Widget::"])
_leaves = st.sampled_from(
    [
        "get",
        "set",
        "get_value",
        "value_get",
        "process_batch",
        "process_items",
        "Widget",
        "~Widget",
        "operator+",
        "operator-",
        "foo<int>",
        "foo<long>",
        "make_thing",
        "make_other",
    ]
)
_params = st.sampled_from(["()", "(int)", "(long)", "(int, char const*)", ""])
_returns = st.sampled_from(["", "int ", "long "])


@st.composite
def _names(draw: st.DrawFn) -> str:
    leaf = draw(_leaves)
    ret = draw(_returns) if "<" in leaf else ""
    return f"{ret}{draw(_scopes)}{leaf}{draw(_params)}"


@settings(max_examples=150, deadline=None)
@given(st.lists(_names(), min_size=2, max_size=14, unique=True))
def test_predicate_accepts_only_pairs_sharing_a_key(names: list[str]) -> None:
    for a in names:
        ka = set(_plausible_rename_keys(a))
        for b in names:
            if _plausible_rename(a, b):
                assert ka & set(_plausible_rename_keys(b)), (a, b)


def _fps(names: list[str], sizes: list[int]) -> dict[str, FunctionFingerprint]:
    return {
        n: FunctionFingerprint(name=n, size=s, code_hash="")
        for n, s in zip(names, sizes)
    }


@settings(max_examples=200, deadline=None)
@given(
    st.lists(_names(), min_size=1, max_size=12, unique=True),
    st.lists(_names(), min_size=1, max_size=12, unique=True),
    st.data(),
)
def test_keyed_matching_equals_unkeyed(
    old_names: list[str], new_names: list[str], data: st.DataObject
) -> None:
    # Few distinct sizes, so buckets are crowded and the fuzzy window spans
    # several of them -- the shape the index exists for.
    size = st.sampled_from([64, 64, 66, 100, 128])
    old = _fps(
        old_names,
        data.draw(st.lists(size, min_size=len(old_names), max_size=len(old_names))),
    )
    new = _fps(
        new_names,
        data.draw(st.lists(size, min_size=len(new_names), max_size=len(new_names))),
    )
    plain = match_renamed_functions(old, new, name_filter=_plausible_rename)
    keyed = match_renamed_functions(
        old, new, name_filter=_plausible_rename, name_keys=_plausible_rename_keys
    )
    assert keyed == plain


def test_vacuity_some_generated_pairs_really_match() -> None:
    old = _fps(["ns::process_batch(int)", "ns::get()"], [64, 128])
    new = _fps(["ns::v1::process_batch(int)", "ns::set()"], [64, 128])
    got = match_renamed_functions(
        old, new, name_filter=_plausible_rename, name_keys=_plausible_rename_keys
    )
    assert [(c.old_name, c.new_name) for c in got] == [
        ("ns::process_batch(int)", "ns::v1::process_batch(int)")
    ]
