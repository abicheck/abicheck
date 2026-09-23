"""The trie-compiled spelling pattern must match exactly like the flat
longest-first alternation it replaced.

Oracle: ``_build_flat_spelling_pattern`` -- the original builder, unchanged.
Compared through both ``finditer`` and ``finditer_allow_nested`` (the
latter searches narrowed windows, where ``re`` treats ``endpos`` as end of
string for the right-boundary lookahead -- a quirk the trie must share).
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.compare.spelling_pattern import (
    _MAX_TRIE_DEPTH,
    _build_flat_spelling_pattern,
    _build_spelling_pattern,
    finditer_allow_nested,
)

_ATOMS = [
    "std", "dal", "v1", "Table", "Foo", "int", "vector", "string", "Node",
    "A", "x", "const", "*", "&", "<", ">", ",", " ", "::", ":", "(", ")",
    "_", "1", "[", "]", "\\", ".", "$",
]  # fmt: skip


def _spans(pattern, text):
    nested = [
        (m.start(), m.end(), m.group()) for m in finditer_allow_nested(pattern, text)
    ]
    flat = [(m.start(), m.end(), m.group()) for m in pattern.finditer(text)]
    return nested, flat


def _assert_same(vocab, texts):
    trie = _build_spelling_pattern(vocab)
    flat = _build_flat_spelling_pattern(vocab)
    for text in texts:
        assert _spans(trie, text) == _spans(flat, text), (sorted(vocab), text)


_atom_text = st.lists(st.sampled_from(_ATOMS), max_size=6).map("".join)


@settings(max_examples=400, deadline=None)
@given(
    vocab=st.sets(_atom_text.filter(bool), min_size=1, max_size=12),
    extra=st.lists(_atom_text, max_size=6),
    data=st.data(),
)
def test_trie_matches_flat_alternation(vocab, extra, data) -> None:
    pieces = st.sampled_from(sorted(vocab) + _ATOMS)
    texts = [data.draw(st.lists(pieces, max_size=10).map("".join)) for _ in range(4)]
    _assert_same(vocab, texts + extra)


def test_prefix_chains_prefer_the_longest_valid_spelling() -> None:
    vocab = {
        "Foo",
        "Foo<int>",
        "Foo<int>::X",
        "dal::Table",
        "dal::Table<float>",
        "A",
        "A&&",
    }
    texts = [
        "Foo<int>", "Foo<int>::X *", "Foo<int>::Xy", "Foo<in", "dal::Table<float> &",
        "dal::Tables", "A&&", "A&", "const Foo<int> &", "Foo_",
    ]  # fmt: skip
    _assert_same(vocab, texts)


def test_randomized_realistic_vocabularies() -> None:
    rng = random.Random(20260923)
    ns = ["oneapi::dal", "daal::algorithms", "std", "dal::backend"]
    names = ["Table", "Params", "Result", "Input", "descriptor", "NumericTable"]
    args = ["float", "double", "std::int64_t", "method::dense", "Params<float>"]
    for _ in range(40):
        vocab = set()
        for _ in range(rng.randint(1, 300)):
            s = "::".join(
                [rng.choice(ns)]
                + [
                    rng.choice(names) + str(rng.randint(0, 3))
                    for _ in range(rng.randint(1, 2))
                ]
            )
            if rng.random() < 0.5:
                s += (
                    "<"
                    + ", ".join(rng.choice(args) for _ in range(rng.randint(1, 2)))
                    + ">"
                )
            vocab.add(s)
            if rng.random() < 0.3:
                vocab.add(s.split("<")[0])
        vl = sorted(vocab)
        texts = [
            rng.choice(["const ", "", "std::shared_ptr<"])
            + rng.choice(vl)
            + rng.choice([" &", ">", "*", "", "x"])
            for _ in range(30)
        ]
        _assert_same(vocab, texts)


@pytest.mark.parametrize("depth", [_MAX_TRIE_DEPTH // 2, _MAX_TRIE_DEPTH * 2, 1000])
def test_deep_nesting_falls_back_to_the_flat_matcher(depth: int) -> None:
    vocab, s = set(), "A"
    for _ in range(depth):
        vocab.add(s)
        s = "A<" + s + ">"
    trie = _build_spelling_pattern(vocab)
    flat = _build_flat_spelling_pattern(vocab)
    if depth > _MAX_TRIE_DEPTH:
        assert trie.pattern == flat.pattern
    texts = [s, "A<A<A>>", "x A<A> y"]
    for text in texts:
        assert _spans(trie, text) == _spans(flat, text)


def test_one_vocabulary_one_pattern_text() -> None:
    vocab = ["b::X", "a::Y", "a::Y<int>", "c"]
    assert (
        _build_spelling_pattern(vocab).pattern
        == _build_spelling_pattern(list(reversed(vocab))).pattern
    )


def test_empty_vocabulary() -> None:
    assert _build_spelling_pattern([]) is None
