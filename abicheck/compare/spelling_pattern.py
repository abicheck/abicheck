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
from typing import Any

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
    faster at 60,000) and built in 0.024 s. That replacement is **not**
    made here, for a reason recorded in :func:`finditer_allow_nested`.

    **What is done instead is a prefix-trie pattern** (:func:`_trie_body`):
    the same language, but with shared prefixes factored out, so at each
    subject position ``sre`` follows one branch per character instead of
    trying every alternative. Same matches, same spans: see
    :func:`_build_spelling_pattern` for why the match chosen at each
    position is unchanged. On a 40,000-spelling vocabulary the cold
    per-lookup cost dropped ~500x (5.16 s -> 0.01 s over 4,000 subjects);
    compile time is unchanged, since both are dominated by ``sre``'s own
    compiler.

    Longest-first ordering
    doesn't change *whether* something matches (every alternative is
    anchored to the same boundary, so a shorter spelling can't "shadow" a
    longer one the way it could in an unanchored first-match scan) but keeps
    the compiled pattern's alternation order deterministic for a stable
    ``.finditer()`` iteration order.
    """
    return VOCABULARY_CACHE.get_or_compile(spellings, _build_spelling_pattern)


#: Deepest group nesting :func:`_trie_body` will emit. Past it, the trie
#: shape would push ``re``'s recursive parser/compiler toward
#: ``RecursionError`` (reproduced with 1,000 successively nested template
#: spellings), so the vocabulary is compiled as the flat alternation instead
#: -- slower to match, but exactly the matcher this module always had.
_MAX_TRIE_DEPTH = 64


class _TrieTooDeep(Exception):
    pass


def _build_spelling_pattern(spellings: Collection[str]) -> re.Pattern[str] | None:
    """The uncached build :func:`compile_spelling_pattern` memoizes.

    Compiles *spellings* as a prefix trie (:func:`_trie_body`), falling
    back to the flat longest-first alternation
    (:func:`_build_flat_spelling_pattern`) for a vocabulary too deeply
    nested for one.

    **Why the trie picks the same match as the flat alternation.** At a
    given subject position, the flat pattern tries candidates longest
    first and takes the first whose right boundary holds. In the trie, two
    candidates can both match at one position only if one is a prefix of
    the other (they read the same characters), so they lie on one
    root-to-leaf path -- and every node tries its continuations *before*
    accepting termination, so the longer candidate is tried first there
    too, and a failed right boundary backtracks to the next shorter one on
    that same path. Candidates that are not prefix-related cannot both
    match at one position at all. So the selected match, and therefore
    every span ``finditer``/:func:`finditer_allow_nested` report, is the
    same; the property tests check this differentially against
    :func:`_build_flat_spelling_pattern`.
    """
    if not spellings:
        return None
    root: dict[str, Any] = {}
    for spelling in spellings:
        node = root
        for ch in spelling:
            node = node.setdefault(ch, {})
        node[_TERMINAL] = True
    try:
        body = _trie_body(root, 0)
        return re.compile(_bounded(body))
    except (_TrieTooDeep, RecursionError):
        return _build_flat_spelling_pattern(spellings)


#: Marks "a spelling ends here" in a trie node. Not a single character, so
#: it can never collide with a real edge label.
_TERMINAL = "<end>"


def _trie_body(node: dict[str, Any], depth: int) -> str:
    """Regex text for the trie below *node*.

    A chain of single-child, non-terminal nodes is emitted as one literal.
    At a branch, continuations come first and termination (``?``) last, so
    a longer spelling is always tried before a shorter prefix of it.
    Children are emitted in sorted order, so one vocabulary always yields
    one pattern text (and hits ``re``'s own compile cache).
    """
    if depth > _MAX_TRIE_DEPTH:
        raise _TrieTooDeep
    literal: list[str] = []
    while True:
        edges = [k for k in node if k != _TERMINAL]
        if len(edges) == 1 and _TERMINAL not in node:
            literal.append(re.escape(edges[0]))
            node = node[edges[0]]
            continue
        break
    prefix = "".join(literal)
    alternatives = [
        re.escape(edge) + _trie_body(node[edge], depth + 1) for edge in sorted(edges)
    ]
    if _TERMINAL in node:
        if not alternatives:
            return prefix
        return prefix + "(?:" + "|".join(alternatives) + ")?"
    if len(alternatives) == 1:
        return prefix + alternatives[0]
    return prefix + "(?:" + "|".join(alternatives) + ")"


def _bounded(body: str) -> str:
    return rf"(?<![A-Za-z0-9{BOUNDARY_CHARS}])(?:{body})(?![A-Za-z0-9{BOUNDARY_CHARS}])"


def _build_flat_spelling_pattern(
    spellings: Collection[str],
) -> re.Pattern[str] | None:
    """The flat longest-first alternation: the original matcher, kept as
    the fallback for a vocabulary too deep for :func:`_trie_body` and as the
    oracle the trie is tested against."""
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
    return re.compile(_bounded(alternation))


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

    .. warning::

       **This function under-reports, and the replacement that fixes it is
       a behaviour change, not an optimization.** Each match is followed up
       by searching ``(m.start() + 1, m.end())`` -- a window that by
       construction excludes ``m.start()`` itself -- so a *shorter*
       registered spelling beginning at the **same offset** as a longer
       match is never reported. With both ``Foo`` and ``Foo<int>`` in one
       vocabulary, the text ``"Foo<int>"`` yields only ``Foo<int>``;
       ``Foo`` is a boundary-valid occurrence at offset 0 and is lost.
       Confirmed on realistic spellings: ``dal::Table`` inside
       ``dal::Table<float>``, ``std::vector`` inside ``std::vector<int>``,
       ``Node`` inside ``Node*``, ``A`` inside ``A&&``. This is reachable
       in production, because ``type_reachability`` registers record
       spellings and typedef targets in one vocabulary, so a class
       template and its instantiation routinely co-occur.

       The obvious in-place repair -- re-searching a *narrowed* window to
       find the shorter alternative -- is unsound: ``re``'s ``endpos``
       looks like end-of-string to the right-boundary lookahead, so
       ``Foo`` would be accepted inside ``Foobar``. A correct fix scans
       each boundary-valid start position and tests the candidates there,
       which is the indexed matcher described in
       :func:`compile_spelling_pattern`. A differential run of that
       prototype against this function over 2,400 randomized
       vocabulary/text pairs found 70 divergences, **every one of them a
       strict superset** -- it never missed anything this function finds.

       It is deliberately not adopted in the same change as the
       match-cache ownership fix. Adding those occurrences adds
       reachability edges, which can change findings and obligation
       counts, so it needs its own isolated change and its own rebaseline
       -- bundling it would make a performance patch silently alter
       results, and would invalidate the semantic-equivalence check any
       performance claim rests on. Tracked in
       ``docs/contribute/known-gaps.md``.

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
