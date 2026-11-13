"""Bracket-depth scanners never go below zero on an unbalanced closer.

A stray closer (``)``, ``>``, ``]``, ``}``) at depth zero used to drive the
depth counter negative, so every later ``depth == 0`` check was wrong for the
rest of the input. The clamped scanners set the contract: a stray closer is
ignored for depth purposes, and what follows it is scanned as if it were not
there. Balanced input must be unaffected by the clamp.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.type_graph_names import (
    _split_top_level_commas,
    _top_level_paren_index,
)
from abicheck.extract.headers.castxml.out_of_line_inline import _top_level_commas
from abicheck.model.namespace_spelling import namespace_suffix_spellings

# --- _top_level_paren_index: first "(" not nested in <...> -----------------


@pytest.mark.parametrize(
    ("balanced", "expected"),
    [
        ("f<int>(x)", 6),
        ("std::function<detail::Impl ()>", -1),
        ("plain(x)", 5),
        ("no paren here", -1),
    ],
)
def test_top_level_paren_index_balanced_unchanged(balanced: str, expected: int) -> None:
    assert _top_level_paren_index(balanced) == expected


@pytest.mark.parametrize(
    ("stray", "content"),
    [
        (">", "f(x)"),
        (">>", "f(x)"),
        (">", "f<int>(x)"),
    ],
)
def test_top_level_paren_index_stray_closer_ignored(stray: str, content: str) -> None:
    # The result equals the one the same content gives without the stray
    # closer, shifted by the stray prefix's length.
    without = _top_level_paren_index(content)
    assert _top_level_paren_index(stray + content) == without + len(stray)


# --- _split_top_level_commas (type_graph_names): split on depth-0 commas --


@pytest.mark.parametrize(
    ("balanced", "expected"),
    [
        ("a, f(b, c)", ["a", " f(b, c)"]),
        ("a, X<b, c>", ["a", " X<b, c>"]),
        ("a, b", ["a", " b"]),
    ],
)
def test_split_top_level_commas_balanced_unchanged(
    balanced: str, expected: list[str]
) -> None:
    assert _split_top_level_commas(balanced) == expected


@pytest.mark.parametrize(
    ("stray", "content"),
    [
        (")", "a, b"),
        (">", "a, b"),
        ("]", "a, X<b, c>"),
        (">>", "a, f(b, c)"),
    ],
)
def test_split_top_level_commas_stray_closer_does_not_hide_commas(
    stray: str, content: str
) -> None:
    # A leading stray closer must not stop the following depth-0 comma from
    # splitting: the split matches the content's own split with the stray
    # closer attached to the first part.
    without = _split_top_level_commas(content)
    with_stray = _split_top_level_commas(stray + content)
    assert len(with_stray) == len(without)
    assert with_stray[0] == stray + without[0]
    assert with_stray[1:] == without[1:]


# --- namespace_suffix_spellings: split on depth-0 "::" ---------------------


@pytest.mark.parametrize(
    ("balanced", "expected"),
    [
        ("api::Outer::Inner", ["api::Outer::Inner", "Outer::Inner", "Inner"]),
        ("x<a::b>::c", ["x<a::b>::c", "c"]),
        ("a::b<c::d>::e", ["a::b<c::d>::e", "b<c::d>::e", "e"]),
        ("Bare", ["Bare"]),
    ],
)
def test_namespace_suffix_spellings_balanced_unchanged(
    balanced: str, expected: list[str]
) -> None:
    assert namespace_suffix_spellings(balanced) == expected


@pytest.mark.parametrize(
    ("stray", "content"),
    [
        (">", "a::b"),
        (">>", "a::b<c>::d"),
        (">", "x<a::b>::c"),
    ],
)
def test_namespace_suffix_spellings_stray_closer_ignored(
    stray: str, content: str
) -> None:
    # The "::" inside the content must still be seen as depth zero, so its
    # suffixes match the content alone, with the stray closer kept on the
    # first spelling.
    without = namespace_suffix_spellings(content)
    with_stray = namespace_suffix_spellings(stray + content)
    assert len(with_stray) == len(without)
    assert with_stray[0] == stray + without[0]
    assert with_stray[1:] == without[1:]


# --- _top_level_commas (out_of_line_inline): arity of a parameter list -----


@pytest.mark.parametrize(
    ("balanced", "expected"),
    [
        ("int, f(a,b), X<c, d>", 2),
        ("a, b", 1),
        ("", 0),
        ("int", 0),
    ],
)
def test_top_level_commas_balanced_unchanged(balanced: str, expected: int) -> None:
    assert _top_level_commas(balanced) == expected


@pytest.mark.parametrize(
    ("stray", "content"),
    [
        (")", "a, b"),
        ("]", "a, b"),
        ("}", "int, f(a,b)"),
        (">", "a, b"),
    ],
)
def test_top_level_commas_stray_closer_does_not_hide_commas(
    stray: str, content: str
) -> None:
    # Commas after a stray closer still count as top-level, so the arity is
    # the same as for the content alone.
    assert _top_level_commas(stray + content) == _top_level_commas(content)
