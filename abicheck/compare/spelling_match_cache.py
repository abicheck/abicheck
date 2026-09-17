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

"""Bounded reuse of the two *lexical* results type-spelling matching keeps
recomputing: the compiled vocabulary alternation, and the match list a
given (vocabulary, text, window) triple produces.

Why this is its own ``compare``-owned leaf module rather than two
module-level dicts in :mod:`abicheck.type_reachability_spelling`: both caches need one explicit
owner with an *aggregate* byte budget. An unbounded ``@lru_cache`` per
pattern -- or a per-pattern maxsize multiplied by however many patterns a
run compiles and however many workers a release fan-out admits -- is not a
bounded memory plan for a request, and retention is the very axis the
surrounding performance work is trying to reduce. Everything here is
therefore capped three ways: per-input size, per-result size, and a total
retained-bytes budget across every pattern.

**What is cached is strictly lexical.** ``matches_for`` answers "which
registered spellings occur, as whole type tokens, in this text window" --
a pure function of (vocabulary, text, window). It deliberately does *not*
cache the reachability *operation* that consumes those matches: the same
type string can be reached directly, through one typedef, and through
another typedef, and those visits contribute different alias/provenance
evidence to the scan's own state. Skipping the scan for an
already-seen text would lose findings; reusing its lexical matches cannot,
because the scan re-runs its state updates over the reused matches exactly
as it would over freshly computed ones.

Results are immutable (a tuple of :class:`SpellingMatch`) holding only
``str``/``int``, never a :class:`re.Match` (which retains the whole subject
string and the compiled pattern) and never a mutable list a caller could
edit in place and so corrupt for the next reader. Keys hold only a
pattern-identity token, the text, and the window -- never a snapshot,
declaration, or bound scanner instance.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable, Collection, Iterable

# A text longer than this is still matched -- just not cached. An enormous
# instantiated-template spelling is both the least likely to repeat and the
# most expensive thing to retain, so it bypasses the cache rather than
# evicting a working set of ordinary signatures to make room for itself.
MAX_CACHED_TEXT_CHARS = 4_096

# Likewise for a result: a text yielding more matches than this is a
# pathological nesting case, not the repeated ordinary lookup this cache
# exists for.
MAX_CACHED_RESULT_MATCHES = 512

# Aggregate retained-bytes budget across *all* patterns, plus a hard entry
# count so a stream of tiny texts cannot retain unboundedly many keys.
MAX_RETAINED_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 32_768

# How many distinct vocabularies keep a compiled pattern alive. A run
# compiles a handful (stdlib / record / typedef vocabularies per side); the
# cap exists so a caller that derives a fresh vocabulary per declaration
# degrades to recompiling rather than retaining every one of them.
MAX_CACHED_VOCABULARIES = 64

# Rough per-entry bookkeeping overhead (key tuple, dict slot, result tuple,
# and one SpellingMatch object per match). Deliberately an estimate: the
# budget's job is to bound growth, not to report exact RSS.
_BYTES_PER_ENTRY = 224
_BYTES_PER_MATCH = 96


class SpellingMatch:
    """One matched spelling: its text and its span in the subject string.

    A drop-in read of the :class:`re.Match` API the matcher's callers
    actually use (``group()``, ``start()``, ``end()``) -- and nothing else,
    on purpose. Immutable and slotted, so a cached result cannot be mutated
    by one reader and observed changed by the next, and retains no
    reference to the subject string or the compiled pattern.
    """

    __slots__ = ("_end", "_start", "_text")

    def __init__(self, text: str, start: int, end: int) -> None:
        self._text = text
        self._start = start
        self._end = end

    def group(self, index: int = 0) -> str:
        if index != 0:
            raise IndexError("no such group")
        return self._text

    def start(self, index: int = 0) -> int:
        if index != 0:
            raise IndexError("no such group")
        return self._start

    def end(self, index: int = 0) -> int:
        if index != 0:
            raise IndexError("no such group")
        return self._end

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpellingMatch):
            return NotImplemented
        return (self._text, self._start, self._end) == (
            other._text,
            other._start,
            other._end,
        )

    def __hash__(self) -> int:
        return hash((self._text, self._start, self._end))

    def __repr__(self) -> str:
        return f"SpellingMatch({self._text!r}, {self._start}, {self._end})"


def _entry_bytes(text: str, matches: tuple[SpellingMatch, ...]) -> int:
    return (
        _BYTES_PER_ENTRY
        + len(text) * 2
        + sum(_BYTES_PER_MATCH + len(m.group()) * 2 for m in matches)
    )


class _MatchCache:
    """LRU over ``(pattern token, text, start, end) -> matches``.

    The pattern is identified by a token rather than by its own (very
    large) pattern text: hashing a 40 KiB alternation on every lookup would
    hand back a meaningful slice of what the cache saves. A token is only
    ever issued alongside a strong reference to the pattern it names, so an
    ``id()``-derived token can never be recycled onto a different pattern
    while any entry still refers to it.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[
            tuple[int, str, int, int], tuple[SpellingMatch, ...]
        ] = OrderedDict()
        self._bytes = 0
        self._keepalive: dict[int, tuple[re.Pattern[str], int]] = {}
        self.hits = 0
        self.misses = 0
        self.bypasses = 0

    def token_for(self, pattern: re.Pattern[str]) -> int:
        """*pattern*'s cache token -- **without** retaining it.

        Deliberately not where the keepalive reference is taken: a lookup
        that misses, and a result too large to retain, would otherwise
        leave a permanent entry holding a compiled pattern the cache stores
        nothing for, which is an unbounded leak of exactly the thing this
        module exists to bound. Retention happens in :meth:`put`, beside
        the entry that needs it.

        Using a bare ``id()`` for the lookup is still sound: a token only
        ever appears in ``_entries`` while :meth:`put` holds a strong
        reference to the pattern it names, so a *live* pattern's id cannot
        equal a retained-but-different pattern's id.
        """
        return id(pattern)

    def _retain(self, token: int, pattern: re.Pattern[str]) -> None:
        held = self._keepalive.get(token)
        refs = held[1] if held is not None else 0
        self._keepalive[token] = (pattern, refs + 1)

    def _release(self, token: int) -> None:
        held = self._keepalive.get(token)
        if held is None:
            return
        pattern, refs = held
        if refs <= 1:
            del self._keepalive[token]
        else:
            self._keepalive[token] = (pattern, refs - 1)

    def get(self, key: tuple[int, str, int, int]) -> tuple[SpellingMatch, ...] | None:
        """The cached matches for *key*, or ``None`` when not yet computed.

        ``None`` means *not computed*, never *computed as empty*: an empty
        result is cached like any other, because "this text names nothing
        in this vocabulary" is exactly as reusable as a positive answer and
        is the common case for a large vocabulary.
        """
        cached = self._entries.get(key)
        if cached is None:
            # An empty result is cached too -- "this text names nothing in
            # this vocabulary" is exactly as reusable as a positive answer,
            # and is the common case for a large vocabulary. So absence
            # here means *not computed*, never *computed as empty*.
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return cached

    def put(
        self,
        key: tuple[int, str, int, int],
        matches: tuple[SpellingMatch, ...],
        pattern: re.Pattern[str],
    ) -> None:
        text = key[1]
        if (
            len(text) > MAX_CACHED_TEXT_CHARS
            or len(matches) > MAX_CACHED_RESULT_MATCHES
        ):
            self.bypasses += 1
            return
        if key in self._entries:
            return
        self._entries[key] = matches
        self._bytes += _entry_bytes(text, matches)
        self._retain(key[0], pattern)
        while self._entries and (
            self._bytes > MAX_RETAINED_BYTES or len(self._entries) > MAX_ENTRIES
        ):
            evicted_key, evicted = self._entries.popitem(last=False)
            self._bytes -= _entry_bytes(evicted_key[1], evicted)
            self._release(evicted_key[0])

    def clear(self) -> None:
        self._entries.clear()
        self._keepalive.clear()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.bypasses = 0

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)


class _VocabularyCache:
    """LRU over ``frozenset(spellings) -> compiled pattern | None``.

    Keyed on the vocabulary rather than on the built pattern text, since
    building that text (one ``re.escape`` per spelling plus the join) is
    itself a meaningful share of the cost -- and because two calls with the
    same vocabulary in a different iteration order must reuse one pattern,
    not merely compile to one that happens to be equal.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[frozenset[str], re.Pattern[str] | None] = (
            OrderedDict()
        )
        self.hits = 0
        self.misses = 0

    def get_or_compile(
        self,
        spellings: Collection[str],
        compile_fn: Callable[[frozenset[str]], re.Pattern[str] | None],
    ) -> re.Pattern[str] | None:
        key = frozenset(spellings)
        if key in self._entries:
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]
        self.misses += 1
        compiled = compile_fn(key)
        self._entries[key] = compiled
        self._entries.move_to_end(key)
        while len(self._entries) > MAX_CACHED_VOCABULARIES:
            self._entries.popitem(last=False)
        return compiled

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._entries)


MATCH_CACHE = _MatchCache()
VOCABULARY_CACHE = _VocabularyCache()


def matches_for(
    pattern: re.Pattern[str],
    text: str,
    start: int,
    end: int,
    compute: Callable[[], Iterable[SpellingMatch | re.Match[str]]],
) -> tuple[SpellingMatch, ...]:
    """The cached lexical match tuple for ``text[start:end]`` under *pattern*.

    *compute* is called (with no arguments) on a miss and must return an
    iterable of objects exposing ``group()``/``start()``/``end()``; its
    result is normalized into immutable :class:`SpellingMatch` objects
    before being retained, so no :class:`re.Match` ever enters the cache.
    """
    token = MATCH_CACHE.token_for(pattern)
    key = (token, text, start, end)
    cached = MATCH_CACHE.get(key)
    if cached is not None:
        return cached
    normalized = tuple(
        m
        if isinstance(m, SpellingMatch)
        else SpellingMatch(m.group(0), m.start(), m.end())
        for m in compute()
    )
    MATCH_CACHE.put(key, normalized, pattern)
    return normalized


def clear_caches() -> None:
    """Drop everything both caches retain. For tests and for a caller that
    wants to release the working set at a known point (end of a member
    comparison, say) rather than waiting for the byte budget to force it.
    """
    MATCH_CACHE.clear()
    VOCABULARY_CACHE.clear()
