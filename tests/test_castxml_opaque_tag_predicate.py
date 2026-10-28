"""The castxml opaque ``FunctionType`` tag is recognized by whole token, never
by substring.

castxml renders an anonymous function type behind a pointer as the bare tag
``FunctionType`` (``FunctionType*``). Testing a spelling with
``"FunctionType" in s`` also matches real, legitimately named types such as
``MyFunctionTypeWrapper*`` or ``FunctionTypeT``, which then get mistaken for
the artifact. The predicates here are anchored to the whole spelling.

The oracle is a token-level regex written independently of the predicate's
own pattern. It reduces a spelling to one letter per token, keeping the
whitespace facts the grammar depends on, and matches the letter string.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.idioms import _is_callback_type
from abicheck.model.castxml_spelling_artifacts import (
    is_castxml_opaque_function_type,
    mentions_function_type_tag,
)

# -- independent oracle -------------------------------------------------------

# One token per match, with whether whitespace preceded it. ``_Atomic(`` is a
# single token because castxml writes it with no space before the parenthesis.
_ORACLE_TOKEN = re.compile(r"(\s*)(_Atomic\(|\[\d*\]|[A-Za-z_]\w*|\S)")

# Letter codes, one per token:
#   k  const/volatile, whitespace-separated (or first)
#   K  const/volatile glued to a previous sigil or ')'  (never valid)
#   o  ``_Atomic(``
#   T  the tag ``FunctionType``      X  the tag glued to ``_Atomic(`` by space
#   *  pointer or reference sigil    a  array extent ``[]``/``[N]``
#   x  ``)`` closing an atomic group W  ``)`` preceded by whitespace (never valid)
#   z  anything else
#
# Valid shapes: an optional cv prefix, then either the bare tag or an
# ``_Atomic(`` group around the tag, then any number of sigils/extents, each
# optionally followed by whitespace-separated cv words.
_ORACLE_SHAPE = re.compile(r"k*(?:T|oT(?:[*a]k*)*x)(?:[*a]k*)*")


def _oracle_opaque(spelling: str) -> bool:
    if spelling != spelling.strip():
        return False
    codes: list[str] = []
    for index, match in enumerate(_ORACLE_TOKEN.finditer(spelling)):
        spaced, token = bool(match.group(1)), match.group(2)
        if token in ("const", "volatile"):
            codes.append("k" if spaced or index == 0 else "K")
        elif token == "FunctionType":
            codes.append("X" if spaced and codes[-1:] == ["o"] else "T")
        elif token == "_Atomic(":
            codes.append("o")
        elif token in ("*", "&"):
            codes.append("*")
        elif token.startswith("["):
            codes.append("a")
        elif token == ")":
            codes.append("W" if spaced else "x")
        else:
            codes.append("z")
    return _ORACLE_SHAPE.fullmatch("".join(codes)) is not None


def _oracle_mentions_tag(spelling: str) -> bool:
    # A maximal run of word characters equal to the tag. ``\w+`` (not a
    # letter-initial identifier) keeps ``1FunctionType`` one word, so it is
    # not the tag.
    return "FunctionType" in re.findall(r"\w+", spelling)


# -- spelling generators ------------------------------------------------------

_VOCABULARY = [
    "const",
    "volatile",
    "FunctionType",
    "_Atomic(",
    ")",
    "*",
    "&",
    "[]",
    "[3]",
    "int",
    "struct",
    "MyFunctionType",
    "FunctionTypeT",
    "FunctionType_t",
    "(",
    "x",
]
_SEPARATORS = ["", " ", "  ", "\t"]


@st.composite
def token_spellings(draw: st.DrawFn) -> str:
    pieces = draw(
        st.lists(
            st.tuples(st.sampled_from(_SEPARATORS), st.sampled_from(_VOCABULARY)),
            max_size=7,
        )
    )
    return "".join(separator + token for separator, token in pieces)


_CHAR_SOUP = "FunctionTypeconstvolatil_A*&[]() \t0123x"
_IDENT_START = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"
_IDENT_REST = _IDENT_START + "0123456789"

identifier_part = st.text(alphabet=_IDENT_REST, max_size=8)


# -- the anchored predicate against the oracle -------------------------------


@given(token_spellings())
@settings(max_examples=600, deadline=None)
def test_opaque_predicate_matches_token_oracle(spelling: str) -> None:
    assert is_castxml_opaque_function_type(spelling, "castxml") == _oracle_opaque(
        spelling
    ), repr(spelling)


@given(st.text(alphabet=_CHAR_SOUP, max_size=24))
@settings(max_examples=600, deadline=None)
def test_opaque_predicate_matches_token_oracle_on_character_soup(spelling: str) -> None:
    assert is_castxml_opaque_function_type(spelling, "castxml") == _oracle_opaque(
        spelling
    ), repr(spelling)


@given(token_spellings())
@settings(max_examples=300, deadline=None)
def test_opaque_predicate_is_gated_on_producer(spelling: str) -> None:
    # clang never emits the castxml tag text, so the same spelling is never
    # the artifact for it, whatever its shape.
    assert not is_castxml_opaque_function_type(spelling, "clang")
    assert not is_castxml_opaque_function_type(spelling, "")


@pytest.mark.parametrize(
    "spelling",
    [
        "FunctionType",
        "FunctionType*",
        "FunctionType &",
        "FunctionType * const",
        "const FunctionType*",
        "volatile const FunctionType&",
        "FunctionType[3]",
        "FunctionType*[]",
        "_Atomic(FunctionType*)",
        "const _Atomic(FunctionType*)",
        "_Atomic(FunctionType*)*",
        "const _Atomic(FunctionType*)[4]",
    ],
)
def test_genuine_opaque_spellings_are_recognized(spelling: str) -> None:
    assert is_castxml_opaque_function_type(spelling, "castxml")


@pytest.mark.parametrize(
    "spelling",
    [
        "MyFunctionTypeWrapper*",
        "FunctionTypeT",
        "FunctionType_t*",
        "FunctionType2*",
        "struct FunctionTypeHelper&",
        "std::FunctionType*",
        "FunctionType const*",  # cv must follow a sigil, not the bare tag
        "FunctionType*const",  # cv after a sigil needs whitespace
        "_Atomic( FunctionType*)",  # no space inside the group
        "_Atomic(FunctionType* )",  # whitespace before the close paren
        "_Atomic(MyFunctionType*)",
        "_Atomic(int)",
        "constFunctionType*",
        " FunctionType*",  # not a bare spelling
        "FunctionType* ",
        "int",
        "void (*)(int)",
    ],
)
def test_names_that_merely_contain_the_tag_are_not_opaque(spelling: str) -> None:
    assert not is_castxml_opaque_function_type(spelling, "castxml")
    assert _oracle_opaque(spelling) is False, "oracle disagrees with the case list"


# -- the whole-word mention predicate ----------------------------------------


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("FunctionType*", True),
        ("int (*)(FunctionType*)", True),
        ("ns::FunctionType", True),
        ("MyFunctionTypeWrapper*", False),
        ("FunctionTypeT", False),
        ("FunctionType_t", False),
        ("1FunctionType", False),
        ("void (*)(int)", False),
    ],
)
def test_mention_predicate_is_whole_word(spelling: str, expected: bool) -> None:
    assert mentions_function_type_tag(spelling) is expected


@given(token_spellings())
@settings(max_examples=400, deadline=None)
def test_mention_predicate_matches_word_oracle(spelling: str) -> None:
    assert mentions_function_type_tag(spelling) == _oracle_mentions_tag(spelling), repr(
        spelling
    )


@given(
    prefix=identifier_part,
    suffix=identifier_part,
    sigil=st.sampled_from(["", "*", " &"]),
)
@settings(max_examples=400, deadline=None)
def test_identifier_glued_to_tag_is_never_the_tag(
    prefix: str, suffix: str, sigil: str
) -> None:
    name = prefix + "FunctionType" + suffix
    if name == "FunctionType":
        # The bare tag itself: the positive case, covered elsewhere.
        return
    spelling = name + sigil
    assert not is_castxml_opaque_function_type(spelling, "castxml"), spelling
    assert mentions_function_type_tag(spelling) == _oracle_mentions_tag(spelling)


# -- the callback idiom recognizer, through its public consumer ---------------


@pytest.mark.parametrize(
    ("type_str", "expected"),
    [
        ("FunctionType*", True),
        ("const FunctionType*", True),
        ("FunctionType&", True),
        ("_Atomic(FunctionType*)", True),
        ("void (*)(int)", True),
        ("MyFunctionTypeWrapper*", False),
        ("struct FunctionTypeHelper&", False),
        ("FunctionTypeT", False),
        ("int*", False),
    ],
)
def test_callback_idiom_recognizes_tag_not_substring(
    type_str: str, expected: bool
) -> None:
    assert _is_callback_type(type_str, {}) is expected


@pytest.mark.parametrize(
    ("alias_target", "expected"),
    [
        ("FunctionType*", True),
        ("void (*)(int)", True),
        ("MyFunctionTypeWrapper*", False),
    ],
)
def test_callback_idiom_follows_typedef_target_by_whole_tag(
    alias_target: str, expected: bool
) -> None:
    typedefs = {"cb_t": alias_target}
    assert _is_callback_type("cb_t", typedefs) is expected
    assert _is_callback_type("cb_t*", typedefs) is expected


@pytest.mark.parametrize(
    ("typedefs", "expected"),
    [
        # Two hops: the middle alias is not a callback itself, so the walk
        # must continue past it to reach the function pointer.
        ({"outer_t": "inner_t", "inner_t": "void (*)(int)"}, True),
        ({"outer_t": "inner_t*", "inner_t": "FunctionType*"}, True),
        ({"outer_t": "inner_t", "inner_t": "MyFunctionTypeWrapper*"}, False),
        ({"outer_t": "inner_t", "inner_t": "int"}, False),
    ],
)
def test_callback_idiom_follows_a_multi_hop_typedef_chain(
    typedefs: dict[str, str], expected: bool
) -> None:
    assert _is_callback_type("outer_t", typedefs) is expected
    assert _is_callback_type("outer_t*", typedefs) is expected


@given(prefix=identifier_part, suffix=identifier_part)
@settings(max_examples=300, deadline=None)
def test_callback_idiom_rejects_identifiers_glued_to_tag(
    prefix: str, suffix: str
) -> None:
    name = prefix + "FunctionType" + suffix
    if name == "FunctionType":
        return
    assert _is_callback_type(name + "*", {}) is False, name
    assert _is_callback_type("cb_t", {"cb_t": name + "*"}) is False, name
