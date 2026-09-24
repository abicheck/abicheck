# SPDX-License-Identifier: Apache-2.0
"""``spellings_possible_in`` never changes what a spelling pattern matches.

Oracle: the pattern compiled over the *unfiltered* vocabulary. Generated
vocabularies and haystacks share a small alphabet of identifier pieces and
the separators C++ spellings use (``::``, ``<>``, ``*``, spaces, ``_``), so
prefix-related spellings, boundary characters, nested matches and
partially-present spellings all occur.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.compare.spelling_pattern import (
    compile_spelling_pattern,
    finditer_allow_nested,
    spellings_possible_in,
)

_piece = st.sampled_from(["Foo", "Bar", "std", "dep", "x1", "T", "_", "vector"])
_sep = st.sampled_from(["", "::", "<", ">", " ", "*", "_", ", ", "&"])
_text = st.lists(st.tuples(_piece, _sep), min_size=1, max_size=6).map(
    lambda parts: "".join(a + b for a, b in parts).strip() or "Foo"
)


def _spans(vocabulary: set[str], haystack: str) -> list[tuple[int, int, str]]:
    pattern = compile_spelling_pattern(vocabulary)
    if pattern is None:
        return []
    return sorted(
        (m.start(), m.end(), m.group())
        for m in finditer_allow_nested(pattern, haystack)
    )


@settings(max_examples=400, deadline=None)
@given(st.sets(_text, min_size=1, max_size=12), st.lists(_text, min_size=1, max_size=3))
def test_filtered_vocabulary_matches_identically(
    vocabulary: set[str], haystacks: list[str]
) -> None:
    kept = spellings_possible_in(vocabulary, haystacks)
    assert kept <= vocabulary
    for haystack in haystacks:
        assert _spans(kept, haystack) == _spans(vocabulary, haystack)


@given(st.sets(_text, min_size=1, max_size=12), st.lists(_text, min_size=1, max_size=3))
def test_every_matching_spelling_is_kept(
    vocabulary: set[str], haystacks: list[str]
) -> None:
    kept = spellings_possible_in(vocabulary, haystacks)
    for haystack in haystacks:
        assert {g for _s, _e, g in _spans(vocabulary, haystack)} <= kept


def test_filter_is_not_vacuous() -> None:
    kept = spellings_possible_in(
        {"std::vector<int>", "dep::Thing", "Foo"}, ["void f(Foo*)"]
    )
    assert kept == {"Foo"}
