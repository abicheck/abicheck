"""Whole-type-token containment, written as a manual index walk -- the
independent oracle the compiled spelling alternation
(``compare.spelling_pattern``) is checked against.

It was ``type_reachability_spelling.type_string_references_name`` until
production stopped calling it (dead-code plan, Stage D). The boundary rule
is the one production applies: a match must have no ASCII alphanumeric and
no ``BOUNDARY_CHARS`` character on either side (``_is_boundary_char``), so a
non-ASCII letter next to a name is a boundary, not part of the token.
"""

from __future__ import annotations

from abicheck.compare.spelling_pattern import BOUNDARY_CHARS


def _continues_token(ch: str) -> bool:
    return ch != "" and ch.isascii() and (ch.isalnum() or ch in BOUNDARY_CHARS)


def type_string_references_name(type_string: str, name: str) -> bool:
    """Whether *type_string* mentions *name* as a whole type token.

    >>> type_string_references_name("const std::string &", "std::string")
    True
    >>> type_string_references_name("std::stringstream", "std::string")
    False
    >>> type_string_references_name("xstd::string", "std::string")
    False
    """
    if not name:
        return False
    start = 0
    while True:
        idx = type_string.find(name, start)
        if idx == -1:
            return False
        before = type_string[idx - 1] if idx > 0 else ""
        end = idx + len(name)
        after = type_string[end] if end < len(type_string) else ""
        if not _continues_token(before) and not _continues_token(after):
            return True
        start = idx + 1
