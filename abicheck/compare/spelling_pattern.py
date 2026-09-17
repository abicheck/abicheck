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
    once turns the scan from O(candidates × declarations) into
    O(declarations), independent of candidate count. Longest-first ordering
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
    """Every match of *pattern* in ``text[start:end]``, including one nested
    strictly inside another match's own span (Codex review, fresh evidence):
    plain ``.finditer()`` only returns *non-overlapping* matches, continuing
    its search from the end of each match — so when one candidate's spelling
    is a substring of another's own registered spelling (e.g. ``"std::string"``
    inside ``"std::vector<std::string>"``, or a non-stdlib ``"Inner"`` inside
    ``"Wrapper<Inner>"``), the alternation's longest-first ordering matches
    the *outer* candidate first, consuming the whole span, and the inner one
    is never independently reported even though it is directly present in
    the signature text. Splitting stdlib vs. non-stdlib into two independent
    patterns (an earlier fix) only solved *cross*-index masking — two
    candidates from the *same* index (both stdlib, or both non-stdlib
    records) can still mask each other this way.

    Uses an explicit stack rather than recursing into ``text[m.start() + 1 :
    m.end()]`` for every match found (Codex review, fresh evidence): a
    genuinely deep chain of registered spellings each nested one inside the
    next — plausible for template-metaprogramming-heavy C++ under a
    compiler's configured ``-ftemplate-depth`` (GCC/Clang both default well
    into the hundreds, and it's routinely raised higher) — previously
    recursed one Python call per nesting level. Confirmed empirically: 1,000
    successively nested registered candidate spellings raised
    ``RecursionError`` under Python's default 1,000-frame recursion limit,
    aborting the whole comparison rather than degrading gracefully. An
    explicit stack has no such limit — each entry is still a strictly
    narrower window than the match that produced it, so the search still
    always terminates, just without consuming Python's call stack to do it.
    """
    if end is None:
        end = len(text)
    matches: list[re.Match[str]] = []
    stack: list[tuple[int, int]] = [(start, end)]
    while stack:
        window_start, window_end = stack.pop()
        for m in pattern.finditer(text, window_start, window_end):
            matches.append(m)
            if m.end() - m.start() > 1:
                stack.append((m.start() + 1, m.end()))
    return matches
