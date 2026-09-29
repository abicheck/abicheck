# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Enum end-of-list (sentinel) member recognition.

Split out of ``diff_helpers`` (which re-exports both names) so the shared
recognizer used by ``diff_types``, ``diff_platform`` and
``diff_serialization`` has its own owner.
"""

from __future__ import annotations

import re

# Sentinel detection for enum members is name-pattern based, not value based:
# a max-value heuristic accidentally downgrades an ordinary member that merely
# happens to hold the largest value in an evolving enum.
#
# The name is split into lowercase word tokens on ``_``/``::``/non-alnum
# characters *and* on camelCase/PascalCase boundaries, so ``FOO_LAST``,
# ``foo_last``, ``FooLast``, ``kLast`` and ``LastSymbol`` all tokenize the same
# way. A member is a sentinel when its trailing tokens spell an end-of-list
# marker. Matching whole tokens (never raw substrings) is what keeps ordinary
# names like ``backend``, ``blast`` or ``maximum_size`` out.
_SENTINEL_TAIL_TOKENS = frozenset({"last", "max", "count", "end", "sentinel", "num"})
#: Two-token end markers whose second token alone is not a sentinel word
#: (``dnnl_graph_op_last_symbol``, ``kind::LastSymbol``, ``FOO_MAX_VALUE``).
_SENTINEL_TAIL_PAIRS = frozenset(
    {
        ("last", "symbol"),
        ("last", "entry"),
        ("last", "value"),
        ("last", "item"),
        ("last", "enum"),
        ("max", "value"),
        ("max", "enum"),
        ("max", "entry"),
        ("enum", "end"),
        ("enum", "count"),
        ("num", "entries"),
        ("num", "values"),
    }
)
#: ``NUM_FOOS`` / ``kNumFoos`` / ``num_kinds`` -- a leading count marker.
_SENTINEL_LEAD_TOKENS = frozenset({"num"})
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")


def identifier_tokens(name: str) -> list[str]:
    """Split an identifier into lowercase word tokens.

    Splits on any non-alphanumeric separator (``_``, ``::``) and on
    camelCase/PascalCase/acronym boundaries (``HTTPServer`` -> ``http``,
    ``server``). Spelling-independent: ``FOO_LAST_SYMBOL``, ``foo_last_symbol``
    and ``FooLastSymbol`` all yield ``["foo", "last", "symbol"]``.
    """
    tokens: list[str] = []
    for chunk in _TOKEN_SPLIT_RE.split(name):
        if not chunk:
            continue
        tokens.extend(t.lower() for t in _CAMEL_BOUNDARY_RE.split(chunk) if t)
    return tokens


def is_sentinel_enum_member(member_name: str) -> bool:
    """True for a conventional enum *sentinel* / end-of-list member.

    Recognises ``*_LAST``/``*_MAX``/``*_COUNT``/``*_END``/``*_NUM``/
    ``*_SENTINEL`` in any case convention (snake, UPPER, CamelCase, ``k``
    prefix), two-token tails such as ``*_last_symbol``/``LastSymbol``/
    ``*_MAX_VALUE``, and a leading count marker (``NUM_FOOS``, ``kNumFoos``).
    Only the *leaf* of a qualified name is considered.

    Shared by the enum-member detectors in ``diff_types`` (header/DWARF enums)
    and ``diff_platform`` (platform enums), and by ``diff_serialization`` so
    an end marker is never mistaken for a serialization tag id.
    """
    leaf = member_name.rsplit("::", 1)[-1]
    tokens = identifier_tokens(leaf)
    if not tokens:
        return False
    if tokens[-1] in _SENTINEL_TAIL_TOKENS:
        return True
    if len(tokens) >= 2 and (tokens[-2], tokens[-1]) in _SENTINEL_TAIL_PAIRS:
        return True
    # A leading count marker needs something after it (``num`` alone is
    # already covered above).
    lead = tokens[1:] if tokens[0] == "k" and len(tokens) > 1 else tokens
    return len(lead) >= 2 and lead[0] in _SENTINEL_LEAD_TOKENS
