# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Matching a whole vocabulary of type spellings against one type string.

The ``compare``-owned matcher half of ``type_reachability_spelling.py``:
building the one compiled alternation that answers "which of these
spellings occur here, as whole type tokens", and running it in a way that
also reports a match nested strictly inside another. Pure -- no scan
state, no snapshot -- which is what lets the reuse in the sibling
:mod:`abicheck.compare.spelling_match_cache` be safe at all.

Moved here from ``type_reachability_spelling.py`` rather than left beside
the spelling *derivation* it grew up next to: deriving the spellings a type
can appear under and matching a vocabulary against a string are two
responsibilities, and the second is the one the reuse cache and the
boundary semantics belong to. ``type_reachability_spelling`` re-exports
these by value, so every existing import path still resolves.

:data:`BOUNDARY_CHARS` is shared with ``type_reachability_spelling``'s own
single-name :func:`type_string_references_name`, kept as one constant so
the manual check and the compiled alternation cannot silently drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from .spelling_match_cache import (
    VOCABULARY_CACHE,
    SpellingMatch,
    matches_for,
)

__all__ = [
    "BOUNDARY_CHARS",
    "compile_spelling_pattern",
    "finditer_allow_nested",
    "spelling_matches",
]

#: Boundary character class shared by the single-name check and the
#: compiled multi-spelling pattern.
BOUNDARY_CHARS = "_:"


def compile_spelling_pattern(spellings: Collection[str]) -> re.Pattern[str] | None:
    """One compiled alternation matching any of *spellings* as a whole type
    token — the same boundary semantics as :func:`type_string_references_name`
    (non-identifier, non-``:``-scope character, or the string boundary, on
    both sides), but resolved in a single pass over each declaration's type
    string regardless of how many spellings there are.

    This is the fix for the quadratic candidate-by-candidate scan (Codex
    review, fresh evidence: a synthetic snapshot with 1,000 functions and
    1,000 unreferenced stdlib records took over a second in a single
    ``directly_referenced_stdlib_types`` call, and nine independent
    ``diff_types.py`` call sites each repeated it) — building one pattern
    once collapses ``candidates × declarations`` *scans* into one scan per
    declaration.

    **It does not make the scan independent of candidate count, and this
    docstring used to claim that it did.** One ``re`` pass over an
    alternation of *n* literals is not O(1) in *n*: CPython's ``sre`` tries
    the branches at each position, and only factors out a *shared literal
    prefix*. Measured here, microseconds per lookup on a 34-character
    subject, against vocabularies of 1,000 / 5,000 / 20,000 / 60,000
    spellings:

    ===========================  =====  =====  ======  ======
    vocabulary shape             1,000  5,000  20,000  60,000
    ===========================  =====  =====  ======  ======
    one shared prefix, late miss   0.7    0.7     0.7     0.7
    diverse spellings, miss        5.3   24.0   204.3   917.6
    diverse spellings, easy miss   0.4    0.4     0.5     0.5
    ===========================  =====  =====  ======  ======

    So the cost is flat only when the vocabulary factors to a common
    prefix or the subject fails on its first character. For the shape a
    real C++ vocabulary actually has -- many unrelated namespace roots --
    a miss is **linear in the vocabulary**, 173x across a 60x size range.
    Compilation scales too: 1.44 s to build the 60,000-spelling
    alternation (1.74M pattern characters).

    This is why :mod:`abicheck.compare.spelling_match_cache` matters as
    much as the pattern does: on a real oneDAL comparison 98.99% of
    lookups repeat, so the alternation's per-query cost is paid ~11,000
    times rather than ~1.1 million. It is also why a vocabulary-independent
    indexed literal scan is the standing proposal for the cold path -- a
    prototype measured 1.7-2.0 us flat across that same size range (465x
    faster at 60,000) and built in 0.024 s. (The *correctness* half of that
    proposal -- reporting a shorter spelling at the same offset as a longer
    one -- is done without it; see :func:`finditer_allow_nested`.)

    Longest-first ordering
    doesn't change *whether* something matches (every alternative is
    anchored to the same boundary, so a shorter spelling can't "shadow" a
    longer one the way it could in an unanchored first-match scan) but keeps
    the compiled pattern's alternation order deterministic for a stable
    ``.finditer()`` iteration order.
    """
    return VOCABULARY_CACHE.get_or_compile(spellings, _build_spelling_pattern)


def _build_spelling_pattern(spellings: Collection[str]) -> re.Pattern[str] | None:
    """The uncached build :func:`compile_spelling_pattern` memoizes."""
    if not spellings:
        return None
    # Sorted longest-first *and then lexicographically*, so one vocabulary
    # has exactly one pattern text regardless of the order its spellings
    # were produced in. Length alone leaves equal-length alternatives in
    # incoming order, so an identical vocabulary arriving from two
    # differently-ordered sources built two distinct pattern strings --
    # which missed CPython's own internal ``re`` compile cache, genuinely
    # recompiling the alternation rather than merely re-requesting it
    # (measured on seven distinct vocabularies requested 56 times: 7 engine
    # compilations under a stable candidate order, 56 once equal-length
    # candidates were reordered).
    #
    # Note the tie-break is *defensive* on today's only route into this
    # function: `compile_spelling_pattern` keys its cache on
    # ``frozenset(spellings)`` and passes that set here, and a frozenset of
    # equal elements already iterates in one order -- so the cache, not the
    # sort, is what actually collapses the recompilations above. The
    # tie-break is what makes the *builder itself* order-independent, so a
    # future direct caller (or a cache miss on a differently-shaped input)
    # cannot reintroduce the same divergence. It changes no match either
    # way: every alternative is anchored to the same boundary on both sides,
    # so a shorter spelling cannot shadow a longer one.
    ordered = sorted(spellings, key=lambda s: (-len(s), s))
    alternation = "|".join(re.escape(s) for s in ordered)
    return re.compile(
        rf"(?<![A-Za-z0-9{BOUNDARY_CHARS}])(?:{alternation})(?![A-Za-z0-9{BOUNDARY_CHARS}])"
    )


def spelling_matches(
    pattern: re.Pattern[str], text: str, start: int = 0, end: int | None = None
) -> tuple[SpellingMatch, ...]:
    """:func:`finditer_allow_nested`'s result, reused from a bounded cache.

    The matching itself is unchanged -- this is purely reuse of a *lexical*
    result (which registered spellings occur as whole type tokens in this
    text window), which is a pure function of the vocabulary, the text and
    the window. The scan's own state updates still run over every reused
    match exactly as they would over a freshly computed one, because the
    same text reached directly, through one typedef, and through another
    typedef contributes different alias/provenance evidence; only the
    regex work is skipped, never the consumption of its result. See
    :mod:`abicheck.type_reachability_match_cache` for the cache's bounds
    and for why an oversized input bypasses it rather than evicting the
    working set to make room.
    """
    if end is None:
        end = len(text)
    return matches_for(
        pattern,
        text,
        start,
        end,
        lambda: finditer_allow_nested(pattern, text, start, end),
    )


def finditer_allow_nested(
    pattern: re.Pattern[str], text: str, start: int = 0, end: int | None = None
) -> list[re.Match[str]]:
    """Every whole-token occurrence of a registered spelling in
    ``text[start:end]`` -- including one nested strictly inside another
    match (``std::string`` inside ``std::vector<std::string>``) **and** a
    shorter spelling that begins at the *same* offset as a longer one
    (``Foo`` inside ``Foo<int>``, ``dal::Table`` inside
    ``dal::Table<float>``, ``Node`` inside ``Node*``).

    An occurrence is a registered spelling at ``[i, e)`` whose left and
    right boundaries the pattern's own lookarounds accept against the
    **real** text, except that ``e == end`` counts as a boundary, as it
    always has for the caller's window. Returned in ``(start, -end)``
    order.

    **Why every offset is probed.** Plain ``finditer`` returns only
    non-overlapping matches, and the alternation always takes the
    *longest* candidate at an offset, so both kinds of shorter occurrence
    above were invisible. The previous version recovered nested ones by
    re-searching ``(m.start() + 1, m.end())``, which by construction never
    revisits ``m.start()`` -- it under-reported every same-offset shorter
    spelling, which is reachable in production because
    ``type_reachability`` registers a class template and its
    instantiations in one vocabulary.

    **Why the right boundary is checked here, not by the pattern.** The
    shorter candidates at offset ``i`` are found by matching again with a
    smaller ``endpos`` -- but ``re`` treats ``endpos`` as end of string, so
    the pattern's own right-boundary lookahead would accept ``Foo`` inside
    ``Foobar`` there. A candidate ending exactly at a lowered ``endpos`` is
    therefore re-checked against the real text (with
    :data:`BOUNDARY_CHARS`, the class this module's patterns use), and a
    rejected candidate only lowers the next ``endpos`` below its own end. At each offset the loop visits
    candidates longest first and stops when none is left, so it finds
    every valid one: any valid candidate ending at or before the current
    ``endpos`` also satisfies the pattern there, so the pattern never skips
    past it. (The left-boundary lookbehind already reads the real text
    before ``pos``.)

    Iterative: no recursion however deeply spellings nest.
    """
    if end is None:
        end = len(text)
    matches: list[re.Match[str]] = []
    for i in range(start, end):
        limit = end
        while limit > i:
            m = pattern.match(text, i, limit)
            if m is None:
                break
            stop = m.end()
            # The pattern's own lookarounds read the real text everywhere
            # except at a *lowered* ``endpos``: only a candidate ending
            # exactly there needs its right boundary re-checked.
            if limit == end or stop < limit or not _is_boundary_char(text[stop]):
                matches.append(m)
            if stop <= i:
                break
            limit = stop - 1
    return matches


def _is_boundary_char(ch: str) -> bool:
    """Whether *ch* would continue a token (so cannot sit at its edge)."""
    return ch.isascii() and (ch.isalnum() or ch in BOUNDARY_CHARS)
