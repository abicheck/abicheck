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

Concurrency contract
--------------------

Both caches are process-wide module globals read and written by every
worker of a directory/package ``compare``'s release fan-out (real threads,
one process -- see ``cli_compare_release_pairwise``). They are therefore
**thread-safe**, under one narrow lock each, with these invariants:

* *Every* multi-step state transition is atomic: lookup-plus-recency-
  update, admission-plus-accounting-plus-eviction, pattern reference
  counting, and vocabulary publication. ``OrderedDict`` operations are
  individually atomic under the GIL, which is exactly why this was not
  obvious and not caught: the defect was never a torn dict, it was a
  *compound* operation. ``get`` read a value, another worker's ``put``
  evicted that key, and ``get``'s own ``move_to_end`` then raised
  ``KeyError((token, text, start, end))`` -- observed in a real six-member
  oneDAL release comparison, which reported three members as failed
  extractions on one run and completed cleanly on the next.
* **Expensive work never runs under a lock.** Regex compilation
  (``_VocabularyCache.get_or_compile``'s ``compile_fn``) and matching
  (``matches_for``'s ``compute``) happen outside it, and publication is
  rechecked afterwards. Two workers that miss on the same key
  simultaneously **may both compute it**; that duplicate work is accepted
  deliberately, because the alternative -- one global lock spanning the
  compile -- serializes the very member comparisons the fan-out exists to
  run concurrently. Whichever publication lands first wins, and since a
  result is a pure function of its key, which one wins is unobservable.
* ``clear()`` is safe to call concurrently, but it is a **lifecycle
  reset, not a barrier**. It advances a generation counter; a computation
  already in flight still completes and its caller still receives a
  correct result, but that result is not published into the new epoch. So
  pre-clear state can never reappear afterwards, and no entry, byte count
  or pattern reference outlives the clear that dropped it. ``clear()``
  does *not* wait for in-flight work, and nothing here promises it does.
* Pattern identity stays safe: a token is a bare ``id()``, and it is only
  ever *stored* while ``put`` holds a strong reference to the pattern it
  names, so a recycled id can never resolve to another pattern's results.
  Locking changes none of that -- it only makes the store and the
  reference count one transition.

No lock is held across a caller-supplied callback, so a callback cannot
deadlock or re-enter a held lock, and the two caches' locks are never held
at the same time.
"""

from __future__ import annotations

import re
import threading
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

# Retained cost of one compiled pattern the match cache keeps alive through
# ``_keepalive``, as a multiple of its pattern text's length: the ``str``
# itself plus the compiled program. Leaving it out of the accounting was the
# real gap -- a match entry outlives its vocabulary-cache entry, so an
# evicted vocabulary's pattern stays retained here while ``retained_bytes``
# reported only the entry bookkeeping around it.
#
# **Calibrated, not guessed.** An initial 6 was reasoned from "the str plus
# roughly twice the text again"; measured against the six real vocabularies a
# oneDAL comparison actually compiles, it undercounts by a strikingly stable
# 1.46-1.51x across patterns spanning 8,657 to 3,336,273 characters:
#
#     vocabulary   pattern chars   est @6   measured
#     vocab_001        3,336,273   19.09MB   27.94MB
#     vocab_002        3,141,454   17.98MB   26.27MB
#     vocab_005          859,583    4.92MB    7.20MB
#     vocab_004           55,338    0.32MB    0.48MB
#     vocab_003            9,092    0.05MB    0.08MB
#     vocab_006            8,657    0.05MB    0.07MB
#
# 9 tracks that (~8.8 bytes/char measured). It remains a *lower* bound:
# ``sys.getsizeof`` on a compiled pattern does not reach the internal
# allocations of its compiled program, so the real retention is higher
# still. Erring low is the wrong direction for a budget whose job is to
# bound growth, which is why this is corrected rather than left as a
# "close enough" estimate.
_PATTERN_BYTES_PER_CHAR = 9


class SpellingMatch:
    """One matched spelling: its text and its span in the subject string.

    A drop-in read of the :class:`re.Match` API the matcher's callers
    actually use (``group()``, ``start()``, ``end()``) -- and nothing else,
    on purpose. Immutable and slotted, so a cached result cannot be mutated
    by one reader and observed changed by the next, and retains no
    reference to the subject string or the compiled pattern.

    The immutability is **enforced**, not merely advertised. ``__slots__``
    alone only bounds *which* attributes exist; it does not stop
    ``match._text = ...``, and because a cached result tuple is handed to
    every subsequent reader of the same key, one such assignment silently
    changes what the next consumer sees. That is the sharing invariant this
    module's own docstring promises, so it is closed here rather than left
    to convention.
    """

    __slots__ = ("_end", "_start", "_text")

    # Bare annotations, not assignments: they declare the slots' types for
    # mypy (which cannot see through the ``object.__setattr__`` the
    # read-only ``__setattr__`` below forces ``__init__`` to use) without
    # creating class attributes that would collide with ``__slots__``.
    _text: str
    _start: int
    _end: int

    def __init__(self, text: str, start: int, end: int) -> None:
        object.__setattr__(self, "_text", text)
        object.__setattr__(self, "_start", start)
        object.__setattr__(self, "_end", end)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"SpellingMatch is immutable; cannot set {name!r}. "
            "Results are shared between every reader of a cached key."
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"SpellingMatch is immutable; cannot delete {name!r}. "
            "Results are shared between every reader of a cached key."
        )

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


def _pattern_bytes(pattern: re.Pattern[str]) -> int:
    """Estimated bytes retained by holding *pattern* alive."""
    return len(pattern.pattern) * _PATTERN_BYTES_PER_CHAR


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
        self._lock = threading.Lock()
        self._generation = 0
        self._entries: OrderedDict[
            tuple[int, str, int, int], tuple[SpellingMatch, ...]
        ] = OrderedDict()
        self._bytes = 0
        self._keepalive: dict[int, tuple[re.Pattern[str], int]] = {}
        self.hits = 0
        self.misses = 0
        self.bypasses = 0

    @property
    def generation(self) -> int:
        """The lifecycle epoch a computation should be published into.

        Read *before* an uncached computation starts and handed back to
        :meth:`put`; a :meth:`clear` in between advances it and the result
        is then dropped rather than resurrecting pre-clear state into a
        cache whose whole contract was just reset. See this module's
        "Concurrency contract" note.
        """
        with self._lock:
            return self._generation

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
        if held is None:
            # First entry to name this pattern is the one that starts
            # retaining it, so its cost joins the budget here -- and only
            # here, however many entries go on to share it.
            self._bytes += _pattern_bytes(pattern)
            self._keepalive[token] = (pattern, 1)
            return
        self._keepalive[token] = (held[0], held[1] + 1)

    def _release(self, token: int) -> None:
        held = self._keepalive.get(token)
        if held is None:
            return
        pattern, refs = held
        if refs <= 1:
            # Last entry naming it: the pattern is no longer retained by
            # this cache, so its cost leaves the budget with it.
            self._bytes -= _pattern_bytes(pattern)
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
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                # Lookup and recency update are one transition: splitting
                # them let a concurrent ``put``'s eviction remove this very
                # key between the two halves, and ``move_to_end`` then
                # raised ``KeyError`` out of what is supposed to be a pure
                # cache read, aborting a whole release member comparison.
                self._entries.move_to_end(key)
                self.hits += 1
                return cached
            # An empty result is cached too -- "this text names nothing in
            # this vocabulary" is exactly as reusable as a positive answer,
            # and is the common case for a large vocabulary. So absence
            # here means *not computed*, never *computed as empty*.
            self.misses += 1
            return None

    def put(
        self,
        key: tuple[int, str, int, int],
        matches: tuple[SpellingMatch, ...],
        pattern: re.Pattern[str],
        generation: int | None = None,
    ) -> None:
        """Admit *matches* for *key*, retaining *pattern* alongside it.

        Admission, the byte and entry accounting, the pattern reference
        count and the eviction loop are **one** transition under this
        cache's lock: each of them reads state the others write, so
        interleaving any two of them leaves ``retained_bytes``, the entry
        count and ``_keepalive``'s reference counts disagreeing with the
        entries that are actually present.

        *generation* is the epoch :attr:`generation` reported before the
        caller's computation began. A :meth:`clear` since then means this
        result belongs to a lifetime that has ended, so it is dropped
        rather than resurrected -- see the "Concurrency contract" note.
        """
        text = key[1]
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            if (
                len(text) > MAX_CACHED_TEXT_CHARS
                or len(matches) > MAX_CACHED_RESULT_MATCHES
            ):
                self.bypasses += 1
                return
            if key in self._entries:
                # Two workers missed on the same key and both computed it:
                # the first publication wins and the second is discarded.
                # The result is a pure function of the key, so which one
                # wins cannot change what a later reader observes.
                return
            # A pattern whose own retained cost cannot fit the whole budget is
            # not cached against: admitting it would evict every other entry on
            # the next ``put`` and then be evicted itself, so the cache would
            # thrash while retaining more than it is allowed to. Only applies
            # when this entry would be the one to start retaining it -- an
            # already-retained pattern is a sunk cost its other entries own.
            if (
                self._keepalive.get(key[0]) is None
                and _pattern_bytes(pattern) > MAX_RETAINED_BYTES
            ):
                self.bypasses += 1
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
        """Reset this cache to its empty state and end the current epoch.

        Safe to call while other threads are computing: advancing the
        generation is what stops an in-flight computation publishing a
        pre-clear result afterwards. It is *not* a barrier -- a computation
        already in flight still finishes and its caller still receives its
        (correct, just uncached) result.
        """
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._keepalive.clear()
            self._bytes = 0
            self.hits = 0
            self.misses = 0
            self.bypasses = 0

    @property
    def retained_bytes(self) -> int:
        """Estimated bytes this cache retains, patterns included.

        Covers entry bookkeeping, subject strings and match results **and**
        the compiled patterns held alive through ``_keepalive`` -- which a
        vocabulary-cache eviction does not release, since a match entry
        outlives the vocabulary entry that compiled its pattern.
        """
        with self._lock:
            return self._bytes

    def __len__(self) -> int:
        with self._lock:
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
        self._lock = threading.Lock()
        self._generation = 0
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
        """*spellings*' compiled alternation, compiling it at most once per
        publication.

        ``compile_fn`` runs **outside** the lock. Compiling a multi-megabyte
        alternation is the single most expensive thing this module does, and
        holding a shared lock across it would serialize every release member
        on one worker's compile -- the opposite of what the surrounding
        parallelism is for. The cost is that two workers missing on the same
        vocabulary simultaneously may both compile it; that is **allowed and
        deliberate**. Publication is then rechecked under the lock and the
        first publisher wins, so every caller still ends up with *one*
        pattern object per vocabulary -- which is what keeps the sibling
        match cache from keying two token streams for one vocabulary.
        """
        key = frozenset(spellings)
        with self._lock:
            if key in self._entries:
                # Membership test, recency update and value read are one
                # transition: a concurrent eviction between them would turn
                # a cache hit into a ``KeyError``.
                self._entries.move_to_end(key)
                self.hits += 1
                return self._entries[key]
            self.misses += 1
            generation = self._generation
        compiled = compile_fn(key)
        with self._lock:
            if generation != self._generation:
                # A ``clear`` ended the lifetime this compile belongs to.
                # The pattern itself is still correct, so the caller gets
                # it; it is simply not published into the new epoch.
                return compiled
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
            self._entries[key] = compiled
            while len(self._entries) > MAX_CACHED_VOCABULARIES:
                self._entries.popitem(last=False)
            return compiled

    def clear(self) -> None:
        """Reset this cache and end the current epoch -- see
        :meth:`_MatchCache.clear`, whose contract this mirrors."""
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:
        with self._lock:
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
    # Read before computing, published with the result: a ``clear`` that
    # lands while ``compute`` runs must not have this pre-clear result
    # appear in the post-clear cache. *compute* runs outside every lock --
    # it is the regex work this cache exists to avoid, and serializing it
    # would defeat the release fan-out's parallelism entirely.
    generation = MATCH_CACHE.generation
    normalized = tuple(
        m
        if isinstance(m, SpellingMatch)
        else SpellingMatch(m.group(0), m.start(), m.end())
        for m in compute()
    )
    MATCH_CACHE.put(key, normalized, pattern, generation=generation)
    return normalized


def clear_caches() -> None:
    """Drop everything both caches retain. For tests and for a caller that
    wants to release the working set at a known point (end of a member
    comparison, say) rather than waiting for the byte budget to force it.
    """
    MATCH_CACHE.clear()
    VOCABULARY_CACHE.clear()
