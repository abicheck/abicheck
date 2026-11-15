"""``policy.std_slot_identity.std_spelling``: replace a bare alias spelling by
the slot's single resolved ``std::`` identity, keeping cv and declarators.

Oracle: the expected strings are written out by hand from the rule (one
base-name token, one present stdlib identity), not computed by the helper.
"""

from __future__ import annotations

import pytest

from abicheck.model.fact import Fact
from abicheck.policy.std_slot_identity import std_spelling

STR = "std::basic_string<char>"


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("string", STR),
        ("const string&", f"const {STR}&"),
        ("string*", f"{STR}*"),
        ("string &&", f"{STR} &&"),
        ("const volatile string *", f"const volatile {STR} *"),
    ],
)
def test_bare_alias_takes_the_resolved_identity(spelling: str, expected: str) -> None:
    assert std_spelling(spelling, Fact.present((STR,))) == expected


@pytest.mark.parametrize(
    ("spelling", "identities"),
    [
        # already spelled with std::
        ("std::string", Fact.present((STR,))),
        # no fact, or a fact that is not one present stdlib identity
        ("string", None),
        ("string", Fact.not_collected()),
        ("string", Fact.present(())),
        ("string", Fact.present((STR, "std::allocator<char>"))),
        ("string", Fact.present(("lib::String",))),
        # more than one base-name token: not provably one std type
        ("unsigned string", Fact.present((STR,))),
        ("map<string, int>", Fact.present((STR,))),
    ],
)
def test_spelling_is_kept_when_the_slot_is_not_provably_one_std_type(
    spelling: str, identities: Fact | None
) -> None:
    assert std_spelling(spelling, identities) == spelling


def test_a_library_alias_of_a_std_record_is_one_std_type() -> None:
    # ``namespace lib { using string = std::string; }``: the slot still names
    # exactly std::basic_string<char>, whatever the alias is called.
    assert std_spelling("const lib::string&", Fact.present((STR,))) == f"const {STR}&"
