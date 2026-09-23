"""``finditer_allow_nested`` reports every whole-token occurrence, including
a shorter spelling that starts where a longer match does.

Oracle: a brute-force enumeration of every substring, independent of the
compiled pattern -- a registered spelling at ``[i, e)`` whose left and right
boundaries hold on the *real* text (``e == end`` counting as a boundary for
the caller's window). The previous implementation is kept verbatim as a
second reference, to state the only intended difference: the new result is
always a superset of the old one.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.compare.spelling_pattern import (
    BOUNDARY_CHARS,
    compile_spelling_pattern,
    finditer_allow_nested,
)

_TOKEN = re.compile(rf"[A-Za-z0-9{BOUNDARY_CHARS}]")


def _is_token_char(ch: str) -> bool:
    return bool(_TOKEN.fullmatch(ch))


def _oracle(vocab, text, start=0, end=None):
    end = len(text) if end is None else end
    out = []
    for i in range(start, end):
        if i > 0 and _is_token_char(text[i - 1]):
            continue
        for e in range(end, i, -1):
            if text[i:e] in vocab and (e == end or not _is_token_char(text[e])):
                out.append((i, e, text[i:e]))
    return out


def _previous(pattern, text, start=0, end=None):
    """The implementation this change replaced, verbatim."""
    if end is None:
        end = len(text)
    matches = []
    stack = [(start, end)]
    while stack:
        window_start, window_end = stack.pop()
        for m in pattern.finditer(text, window_start, window_end):
            matches.append(m)
            if m.end() - m.start() > 1:
                stack.append((m.start() + 1, m.end()))
    return matches


def _spans(matches):
    return [(m.start(), m.end(), m.group()) for m in matches]


@pytest.mark.parametrize(
    ("vocab", "text", "lost_before"),
    [
        ({"Foo", "Foo<int>"}, "Foo<int>", "Foo"),
        (
            {"dal::Table", "dal::Table<float>"},
            "const dal::Table<float>& x",
            "dal::Table",
        ),
        ({"std::vector", "std::vector<int>"}, "std::vector<int>", "std::vector"),
        ({"Node", "Node*"}, "Node* next", "Node"),
        ({"A", "A&&"}, "A&& r", "A"),
    ],
)
def test_documented_same_offset_cases_are_now_reported(
    vocab, text, lost_before
) -> None:
    pattern = compile_spelling_pattern(vocab)
    got = _spans(finditer_allow_nested(pattern, text))
    assert got == _oracle(vocab, text)
    assert lost_before in [g for _, _, g in got]
    assert lost_before not in [g for _, _, g in _spans(_previous(pattern, text))]


@pytest.mark.parametrize(
    ("vocab", "text"),
    [
        ({"Foo"}, "Foobar"),  # the endpos trap: must not match
        ({"Foo", "Foobar"}, "Foobarx"),
        ({"a::b"}, "x::a::b"),  # left boundary judged on real text
        ({"Foo"}, "_Foo Foo_ 9Foo :Foo"),
    ],
)
def test_no_match_across_a_token_boundary(vocab, text) -> None:
    got = _spans(finditer_allow_nested(compile_spelling_pattern(vocab), text))
    assert got == _oracle(vocab, text)
    assert got == []


_ATOMS = [
    "Foo",
    "Bar",
    "ns",
    "::",
    ":",
    "<",
    ">",
    "int",
    "*",
    "&",
    " ",
    ",",
    "_",
    "1",
    "x",
    "(",
    ")",
]
_piece = st.lists(st.sampled_from(_ATOMS), min_size=1, max_size=5).map("".join)


@settings(max_examples=500, deadline=None)
@given(
    vocab=st.sets(_piece, min_size=1, max_size=10),
    data=st.data(),
)
def test_matches_brute_force_oracle_and_is_a_superset(vocab, data) -> None:
    pattern = compile_spelling_pattern(vocab)
    pieces = st.sampled_from(sorted(vocab) + _ATOMS)
    text = data.draw(st.lists(pieces, max_size=10).map("".join))
    start = data.draw(st.integers(0, len(text)))
    end = data.draw(st.integers(start, len(text)))

    got = _spans(finditer_allow_nested(pattern, text, start, end))
    assert got == _oracle(vocab, text, start, end)

    before = set(_spans(_previous(pattern, text, start, end)))
    assert before <= set(got)


def test_oracle_is_not_vacuous() -> None:
    assert _oracle({"Foo", "Foo<int>"}, "Foo<int>") == [
        (0, 8, "Foo<int>"),
        (0, 3, "Foo"),
    ]
    assert _oracle({"Foo"}, "Foobar") == []


def test_deep_nesting_does_not_recurse() -> None:
    vocab, s = set(), "A"
    for _ in range(1500):
        vocab.add(s)
        s = "A<" + s + ">"
    text = s
    got = finditer_allow_nested(compile_spelling_pattern(vocab), text)
    # Every spelling occurs (the bare ``A`` at every level, since ``<``
    # ends a token), and nothing outside the vocabulary is reported.
    assert {m.group() for m in got} == vocab


@pytest.mark.parametrize(
    ("template", "instantiation", "signature"),
    [
        ("std::vector", "std::vector<int>", "std::vector<int>"),
        ("std::vector", "std::vector<int>", "const std::vector<int> &"),
        ("std::map", "std::map<int, int>", "std::map<int, int> *"),
    ],
)
def test_stdlib_reachability_sees_the_template_inside_its_instantiation(
    template, instantiation, signature
) -> None:
    """The user-visible consequence, through the reachability API that
    decides which stdlib records a public signature keeps in scope: with
    both the class template and its instantiation registered, the template
    was dropped because it starts at the same offset."""
    from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
    from abicheck.type_reachability import directly_referenced_stdlib_types

    def record(qualified: str) -> RecordType:
        return RecordType(
            name=qualified.split("::", 1)[1], kind="class", qualified_name=qualified
        )

    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type=signature,
                visibility=Visibility.PUBLIC,
            )
        ],
        types=[record(template), record(instantiation)],
    )
    assert directly_referenced_stdlib_types(snap) == {template, instantiation}
