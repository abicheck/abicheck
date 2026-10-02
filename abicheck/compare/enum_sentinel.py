# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Enum end-of-list (sentinel) member recognition.

Split out of ``diff_helpers`` (which re-exports both names) so the shared
recognizer used by ``diff_types``, ``diff_platform`` and
``diff_serialization`` has its own owner.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..model.name_heuristics import (
    NameHeuristicEffect,
    StructuralFact,
    register_name_heuristic,
)

# Sentinel detection is name-nominated and structure-confirmed. The name alone
# never decides: a member is a sentinel only when it *also* holds the maximum
# value among its peers (``holds_enum_maximum``). Value alone never decides
# either: an ordinary member that merely holds the largest value is not
# nominated by its name.
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


#: Values C/C++ code assigns to a member whose only job is to force the
#: enum's storage width (``FOO_FORCE_32BIT = 0x7FFFFFFF``, Vulkan's
#: ``VK_*_MAX_ENUM``). Such a member is not a peer of the real end marker.
_WIDTH_FORCING_VALUES = frozenset(
    {0x7FFFFFFF, 0xFFFFFFFF, 0x7FFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF}
)


def holds_enum_maximum(member_name: str, values: Mapping[str, int]) -> bool:
    """True when *member_name* holds the largest value among its peers.

    The structural half of sentinel recognition: an end-of-list marker sits at
    the top of its enum's *ordinary* value range. Peers are the members that
    are neither themselves sentinel-named nor width-forcing (see
    ``_WIDTH_FORCING_VALUES``); ties count. A member whose name merely *looks*
    like a marker (``E_MAX`` between ``A`` and ``B``, or ``LAST`` below an
    ordinary ``OTHER = 99``) is an ordinary member, and its value change is a
    real break.
    """
    value = values.get(member_name)
    if value is None:
        return False
    return all(
        value >= v
        for n, v in values.items()
        if n != member_name
        and v not in _WIDTH_FORCING_VALUES
        and not is_sentinel_enum_member(n)
    )


def _holds_maximum_on_every_side(
    fact_input: tuple[str, Sequence[Mapping[str, int]]],
) -> bool:
    member_name, sides = fact_input
    return all(holds_enum_maximum(member_name, side) for side in sides)


#: Registered name heuristic (design-hardening Phase 5): the end-of-list name
#: only *lowers* a value change to ``enum_last_member_value_changed``, and
#: only when ``holds_enum_maximum`` confirms it on every compared side.
ENUM_SENTINEL = register_name_heuristic(
    "enum_sentinel",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    lowers_from=("ENUM_MEMBER_VALUE_CHANGED",),
    description=(
        "an end-of-list enum member name (*_LAST/*_MAX/*_COUNT/NUM_*) demotes "
        "its value change to a risk"
    ),
    matcher=is_sentinel_enum_member,
    helpers=(identifier_tokens,),
    confirmed_by=StructuralFact(
        "compare.enum_sentinel.holds_enum_maximum", _holds_maximum_on_every_side
    ),
    vocabularies=(
        "_SENTINEL_TAIL_TOKENS",
        "_SENTINEL_TAIL_PAIRS",
        "_SENTINEL_LEAD_TOKENS",
    ),
    patterns=(_CAMEL_BOUNDARY_RE, _TOKEN_SPLIT_RE),
)


def is_confirmed_enum_sentinel(
    member_name: str,
    *sides: Mapping[str, int],
) -> bool:
    """A sentinel by name *and* by structure on every given side.

    The name-only :func:`is_sentinel_enum_member` may only nominate; a member
    is classified as a sentinel only when it also holds its peers' maximum
    value in each snapshot it is compared across. Name evidence alone never
    demotes a finding.
    """
    return ENUM_SENTINEL.confirmed(member_name, (member_name, sides))
