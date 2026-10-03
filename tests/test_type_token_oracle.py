"""Sanity checks on ``tests/_type_token_oracle.py`` itself, so a broken
oracle cannot make ``test_spelling_match_cache.py``'s agreement sweep pass
by agreeing with a broken pattern."""

from __future__ import annotations

import pytest
from _type_token_oracle import type_string_references_name


@pytest.mark.parametrize(
    ("text", "name", "expected"),
    [
        ("const std::string &", "std::string", True),
        ("std::stringstream", "std::string", False),
        ("xstd::string", "std::string", False),
        ("std::vector<std::string>", "std::string", True),
        ("int", "std::string", False),
        ("std::string", "std::string", True),
        ("_std::string", "std::string", False),
        ("ns::std::string", "std::string", False),
        ("éstd::string", "std::string", True),
        ("Foo", "", False),
    ],
)
def test_whole_token_rule(text: str, name: str, expected: bool) -> None:
    assert type_string_references_name(text, name) is expected
