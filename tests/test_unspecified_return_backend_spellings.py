"""The unspecified-return check reads every header backend's spelling of an unnameable type.

Bug class: a detector keyed on one backend's spelling of a fact. Each header
backend renders a deduced closure return type differently -- castxml 0.7 as
``?``, clang as ``(lambda at f.h:3:10)`` or ``(lambda:f.h#1)``, the native gcc
plugin as ``make_cb()::<lambda(int)>`` -- and
``detect_unspecified_return_now_named`` knew only the gcc and DWARF forms, so
the edit corpus's ``unspecified_return_now_named`` read nothing on castxml
and clang.

The oracle is the table below: every spelling a backend or DWARF was seen to
write for an unnameable return, labelled by hand, against named spellings of
the same shapes (pointers, function pointers, templates). Each unnamed
spelling is checked in both directions against each named one.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_templates import detect_unspecified_return_now_named
from abicheck.model import AbiSnapshot, Function, Visibility

UNNAMED = (
    "?",  # castxml 0.7, a deduced closure return
    "(lambda at lib.h:3:10)",  # clang, AST spelling
    "(lambda:lib.h#1)",  # clang backend, identity spelling
    "make_cb()::<lambda(int)>",  # native gcc plugin
    "auto",  # an undeduced placeholder
    "const auto &",
    "decltype(f())",
    "(anonymous struct at lib.h:2:1)",
    "(unnamed struct at lib.h:2:1)",
)
NAMED = (
    "int",
    "cb_t",
    "int (*)(int)",
    "std::function<int (int)>",
    "Box<int>",
    "const char *",
    "automaton *",  # contains "auto" as a word part only
)


def _snap(rt: str) -> AbiSnapshot:
    fn = Function(
        name="make_cb",
        mangled="_Z7make_cbv",
        return_type=rt,
        params=[],
        visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(library="l", version="0", functions=[fn], variables=[], types=[])


@pytest.mark.parametrize(("unnamed", "named"), list(itertools.product(UNNAMED, NAMED)))
@pytest.mark.parametrize("direction", ["now_named", "now_unnamed"])
def test_an_unnamed_return_against_a_named_one_is_reported(
    unnamed: str, named: str, direction: str
) -> None:
    old, new = (unnamed, named) if direction == "now_named" else (named, unnamed)
    found = detect_unspecified_return_now_named(_snap(old), _snap(new))
    assert [(c.kind, c.old_value, c.new_value) for c in found] == [
        (ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED, old, new)
    ]


@pytest.mark.parametrize(("old", "new"), list(itertools.permutations(NAMED, 2)))
def test_two_named_returns_are_not_this_kind(old: str, new: str) -> None:
    # Negative control: a named-to-named retype is another detector's finding.
    assert detect_unspecified_return_now_named(_snap(old), _snap(new)) == []


@pytest.mark.parametrize(("old", "new"), list(itertools.permutations(UNNAMED[:4], 2)))
def test_two_backends_spelling_of_unnamed_returns_are_not_this_kind(
    old: str, new: str
) -> None:
    assert detect_unspecified_return_now_named(_snap(old), _snap(new)) == []


@pytest.mark.parametrize(
    ("spelling", "unspecified"),
    [
        ("", False),  # no return-type fact at all
        ("   ", False),
        (" auto ", True),
        ("const auto &", True),
        ("automaton *", False),
        ("ns::auto_ptr<int>", False),
    ],
)
def test_return_is_unspecified_reads_whole_spellings(
    spelling: str, unspecified: bool
) -> None:
    from abicheck.compare.unnameable_return import return_is_unspecified

    assert return_is_unspecified(spelling) is unspecified
