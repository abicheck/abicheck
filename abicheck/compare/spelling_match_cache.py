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
  ever *stored* while :class:`_PatternRegistry` holds a strong reference to
  the pattern it names, so a recycled id can never resolve to another
  pattern's results. Locking changes none of that -- it only makes the
  store and the reference count one transition.

No lock is held across a caller-supplied callback, so a callback cannot
deadlock or re-enter a held lock, and the two caches' locks are never held
at the same time. :class:`_PatternRegistry` has a third lock which is
always **innermost**: each cache takes it while holding its own, the
registry never calls back into either cache, and the caches never call each
other -- so the order is total and no cycle exists.

Pattern ownership: one accounting owner
---------------------------------------

:class:`_PatternRegistry` is the single *accounting* owner of compiled
patterns. It holds the reference that keeps a pattern reachable from these
caches and charges its bytes **once**, against :data:`MAX_PATTERN_BYTES`,
however many entries and vocabularies name it. Both caches hold refcounted
*handles* into it rather than the pattern object, so neither can charge for
it, and :data:`MAX_RETAINED_BYTES` bounds strictly what the match cache
owns -- keys, subject strings and result tuples.

That separation is the fix for a self-defeating bypass. Charging a shared
pattern's full size as *incremental* ownership in the match cache meant a
pattern larger than the whole match budget could never have a single entry
admitted, so every lookup against it recomputed -- while the vocabulary
cache kept that very pattern alive regardless, so the bypass released
nothing. On a real oneDAL release comparison four of seven vocabularies
crossed that line (1.12M, 1.12M, 2.87M and 3.96M pattern characters against
an 8 MiB match budget at ~9 bytes/char), taking the match cache from 98.99%
to 63.6% hits with 411,232 bypasses and tripling matching time from 19.99 s
to 58.93 s. Reproduced in isolation at that scale: the same 40,000 hot
lookups cost 0.02 s when admitted and 41.68 s when bypassed.

Single accounting owner is **not** the same as sole strong-reference
owner, and this module does not claim the latter. The matcher's callers
hold their own ordinary references, on two different lifetimes:
``type_reachability``'s ``_StdlibReferenceScan`` keeps its stdlib, record
and typedef patterns in instance attributes for the scanner's whole life,
while ``dumper_scoping`` binds one in a local for the duration of a single
call. Those references keep the pattern alive whatever the registry does,
and they are outside every byte this module reports.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Collection, Iterable

from .spelling_pattern_registry import (
    MAX_PATTERN_BYTES as MAX_PATTERN_BYTES,
    PATTERN_REGISTRY as PATTERN_REGISTRY,
    _pattern_bytes as _pattern_bytes,
    _PatternRegistry as _PatternRegistry,
)

# Re-exported by value, not incidentally: ``_pattern_bytes``, the registry
# type and its budget were defined here before pattern ownership moved to
# its own module, and both this package's tests and PR #1336's concurrency
# suite reach for them through this module. Keeping the names resolvable
# here is what lets that split be a move rather than a rename.

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

    def __init__(self, registry: _PatternRegistry | None = None) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        #: Injected so a test can exercise one cache against its own owner
        #: instead of the process-wide one. Defaults to the shared registry,
        #: which is what every production caller wants.
        self._registry = registry if registry is not None else PATTERN_REGISTRY
        self._entries: OrderedDict[
            tuple[int, str, int, int], tuple[SpellingMatch, ...]
        ] = OrderedDict()
        self._bytes = 0
        #: token -> how many live entries name it, so this cache takes one
        #: registry reference on the first entry and drops it on the last.
        #: The pattern itself is the registry's; this is only the count of
        #: what *this* cache still needs it for.
        self._entries_per_token: dict[int, int] = {}
        self.hits = 0
        self.misses = 0
        self.bypasses = 0
        self.bypass_text_too_long = 0
        self.bypass_too_many_matches = 0
        self.evictions = 0

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
        """Count one more entry naming *token*, referencing it on the first.

        The pattern's *bytes* are not added here: they are the registry's,
        charged once however many entries and vocabularies name it. What
        this cache tracks is only how many of its own entries still need it.
        Called with this cache's lock held; the registry's lock is innermost.
        """
        count = self._entries_per_token.get(token, 0)
        self._entries_per_token[token] = count + 1
        if count == 0:
            self._registry.acquire(pattern, holder="match")

    def _release(self, token: int) -> None:
        """Drop one entry's claim, returning the reference on the last."""
        count = self._entries_per_token.get(token, 0)
        if count <= 1:
            self._entries_per_token.pop(token, None)
            if count == 1:
                self._registry.release(token, holder="match")
            return
        self._entries_per_token[token] = count - 1

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
        count and the registry's reference counts disagreeing with the
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
            if len(text) > MAX_CACHED_TEXT_CHARS:
                self.bypasses += 1
                self.bypass_text_too_long += 1
                return
            if len(matches) > MAX_CACHED_RESULT_MATCHES:
                self.bypasses += 1
                self.bypass_too_many_matches += 1
                return
            if key in self._entries:
                # Two workers missed on the same key and both computed it:
                # the first publication wins and the second is discarded.
                # The result is a pure function of the key, so which one
                # wins cannot change what a later reader observes.
                return
            # There is deliberately no "this pattern is too big" bypass. A
            # pattern's size is the registry's charge, not this entry's, so
            # comparing it against *this* cache's budget was an admission
            # rule keyed on something this cache does not own -- and it
            # refused every entry for a large vocabulary while releasing
            # nothing, since the vocabulary cache held the same pattern
            # regardless. See the module docstring. The two bypasses left
            # above are the ones genuinely about this entry's own cost.
            self._entries[key] = matches
            self._bytes += _entry_bytes(text, matches)
            self._retain(key[0], pattern)
            while self._entries and (
                self._bytes > MAX_RETAINED_BYTES or len(self._entries) > MAX_ENTRIES
            ):
                evicted_key, evicted = self._entries.popitem(last=False)
                self._bytes -= _entry_bytes(evicted_key[1], evicted)
                self._release(evicted_key[0])
                self.evictions += 1

    def drop_pattern(self, token: int) -> int:
        """Forget every entry naming *token*; returns how many were dropped.

        The coordinated half of eviction: a caller that knows a vocabulary
        is finished can release its results without waiting for LRU pressure
        to reach them. One transition under this cache's lock, for the same
        reason :meth:`put` is.
        """
        with self._lock:
            doomed = [k for k in self._entries if k[0] == token]
            for k in doomed:
                evicted = self._entries.pop(k)
                self._bytes -= _entry_bytes(k[1], evicted)
                self._release(k[0])
                self.evictions += 1
            return len(doomed)

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
            # Hand every reference back rather than dropping the map: the
            # patterns are the registry's, and a cleared cache that simply
            # forgot its handles would leak them there forever.
            #
            # Exactly **one** release per token, not one per entry: ``_retain``
            # takes a single handle when a token's first entry arrives, and
            # ``_entries_per_token`` counts entries, not handles. Releasing per
            # entry over-returns, and with two caches sharing one registry that
            # consumes the *other* cache's handle and frees a pattern it still
            # has entries for.
            for token in self._entries_per_token:
                self._registry.release(token, holder="match")
            self._entries_per_token.clear()
            self._entries.clear()
            self._bytes = 0
            self.hits = 0
            self.misses = 0
            self.bypasses = 0
            self.bypass_text_too_long = 0
            self.bypass_too_many_matches = 0
            self.evictions = 0

    @property
    def retained_bytes(self) -> int:
        """Estimated bytes this cache retains, patterns included.

        Covers strictly what this cache owns: entry bookkeeping, subject
        strings and match results. Compiled patterns are deliberately
        excluded -- they are :class:`_PatternRegistry`'s, charged there once
        however many entries and vocabularies name them, which is what makes
        "charged once" checkable at all. :func:`cache_statistics` reports
        both, by owner, so the two are never silently summed as if
        independent.
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

    def __init__(
        self,
        registry: _PatternRegistry | None = None,
        match_cache: _MatchCache | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._registry = registry if registry is not None else PATTERN_REGISTRY
        #: Where to send a *retired* vocabulary's token. Retiring a
        #: vocabulary makes the sibling match cache's entries for it
        #: unreachable -- their key is a token only this cache hands out --
        #: so without this they pin the pattern's bytes forever. Explicit
        #: rather than defaulted to the process-wide cache: an isolated
        #: instance must not reach into the production one. The production
        #: wiring is asserted by a test, so "forgot to wire it" cannot be
        #: silent.
        self._match_cache = match_cache
        self._entries: OrderedDict[frozenset[str], re.Pattern[str] | None] = (
            OrderedDict()
        )
        #: The registry token held for each cached pattern, so eviction
        #: returns exactly the reference this cache took.
        self._tokens: dict[frozenset[str], int] = {}
        self.hits = 0
        self.misses = 0
        self.compilations = 0
        self.evictions = 0

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
            self.compilations += 1
            if generation != self._generation:
                # A ``clear`` ended the lifetime this compile belongs to.
                # The pattern itself is still correct, so the caller gets
                # it; it is simply not published into the new epoch.
                return compiled
            if key in self._entries:
                # Another worker published first. Return theirs and let ours
                # be collected, so one vocabulary is never retained twice --
                # which is also what keeps the registry from being charged
                # for a duplicate nobody can reach.
                self._entries.move_to_end(key)
                return self._entries[key]
            self._entries[key] = compiled
            if compiled is not None:
                self._tokens[key] = self._registry.acquire(
                    compiled, holder="vocabulary"
                )
            retired = self._evict_locked()
            self._retire_tokens(retired)
        return compiled

    def _evict_locked(self) -> list[int]:
        """Bounded two ways, because an entry count is not a byte budget.

        64 vocabularies of 3.96M pattern characters each is gigabytes
        reported as a tidy "64", so eviction consults the registry's byte
        budget as well as :data:`MAX_CACHED_VOCABULARIES`. Always leaves at
        least one entry, so a single oversized vocabulary degrades to
        "retained while in use" rather than to recompiling on every request.

        Called with this cache's lock held. Returns the tokens it retired,
        for the caller to hand to :meth:`_retire_tokens` *after* releasing
        the lock -- see there for why the notification cannot happen here.
        """
        retired: list[int] = []
        while len(self._entries) > 1 and (
            len(self._entries) > MAX_CACHED_VOCABULARIES or self._registry.over_budget()
        ):
            evicted_key, _ = self._entries.popitem(last=False)
            token = self._tokens.pop(evicted_key, None)
            if token is not None:
                self._registry.release(token, holder="vocabulary")
                retired.append(token)
            self.evictions += 1
        return retired

    def _retire_tokens(self, tokens: Iterable[int]) -> None:
        """Drop the match cache's entries for vocabularies that are gone.

        **Called with this cache's lock HELD**, and that is the whole point.

        The first version deferred this until after the lock was released,
        to avoid a vocabulary-then-match lock edge, and justified it with
        "a retired token is retired for good". That reasoning was wrong: a
        token is an ``id()``. Releasing the vocabulary handle can drop the
        registry's last reference, whereupon the pattern is collectable and
        **its address is free to be reused** -- so a concurrent
        ``get_or_compile`` publishing in that window can be handed the same
        token, and the deferred drop then deletes *its* freshly published
        entries. Losing them costs a recomputation rather than a wrong
        answer, but it is exactly the recomputation this change exists to
        stop.

        Holding the lock makes retirement atomic with publication, which
        closes it. The lock order is therefore stated as **vocabulary cache
        -> match cache -> registry**, and it is acyclic because no
        ``_MatchCache`` method reaches the vocabulary cache at all --
        pinned by a test, since that is the precondition this ordering
        rests on.

        Why it must happen at all: the match cache is bounded by its
        *result* bytes, which are tiny next to a compiled alternation's. A
        324-byte result entry keeps a 7 MiB pattern alive, so forty retired
        members pinned 287 MiB behind 13 KB of results -- past the
        registry's 256 MiB budget, which then evicted live vocabularies
        looking for space it could never recover, collapsing the cache to
        one entry and recompiling every member's vocabulary from scratch.
        """
        if self._match_cache is None:
            return
        for token in tokens:
            self._match_cache.drop_pattern(token)

    def clear(self) -> None:
        """Reset this cache and end the current epoch -- see
        :meth:`_MatchCache.clear`, whose contract this mirrors."""
        with self._lock:
            self._generation += 1
            retired = list(self._tokens.values())
            for token in retired:
                self._registry.release(token, holder="vocabulary")
            self._tokens.clear()
            self._entries.clear()
            self.hits = 0
            self.misses = 0
            self.compilations = 0
            self.evictions = 0
            self._retire_tokens(retired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


MATCH_CACHE = _MatchCache()
#: Wired to the match cache so a retired vocabulary takes its now-unreachable
#: match results with it -- see `_VocabularyCache._retire_tokens`.
VOCABULARY_CACHE = _VocabularyCache(match_cache=MATCH_CACHE)


def cache_statistics() -> dict[str, object]:
    """Every counter these caches keep, **grouped by owner**.

    Deliberately reports ``retained_bytes`` per owner and a total, rather
    than one merged number: a pattern's cost belongs to the registry, a
    result's to the match cache, and the working set the matcher's callers
    hold in their own attributes belongs to neither. Summing them as if they
    were independent is how the double-charge this module was redesigned to
    remove got introduced in the first place.

    Each owner is read under its own lock, so every group is internally
    consistent. The groups are not a single instant of the whole module --
    that would need all three locks at once, which this module never does.
    """
    match_total = MATCH_CACHE.hits + MATCH_CACHE.misses
    return {
        "match": {
            "entries": len(MATCH_CACHE),
            "hits": MATCH_CACHE.hits,
            "misses": MATCH_CACHE.misses,
            "hit_rate": (MATCH_CACHE.hits / match_total if match_total else None),
            "bypasses": MATCH_CACHE.bypasses,
            "bypass_text_too_long": MATCH_CACHE.bypass_text_too_long,
            "bypass_too_many_matches": MATCH_CACHE.bypass_too_many_matches,
            "evictions": MATCH_CACHE.evictions,
            "retained_bytes": MATCH_CACHE.retained_bytes,
        },
        "vocabulary": {
            "entries": len(VOCABULARY_CACHE),
            "hits": VOCABULARY_CACHE.hits,
            "misses": VOCABULARY_CACHE.misses,
            "compilations": VOCABULARY_CACHE.compilations,
            "evictions": VOCABULARY_CACHE.evictions,
        },
        "patterns": {
            "held": len(PATTERN_REGISTRY),
            "registered": PATTERN_REGISTRY.registrations,
            "released": PATTERN_REGISTRY.releases,
            "retained_bytes": PATTERN_REGISTRY.retained_bytes,
        },
        "retained_bytes_total": (
            MATCH_CACHE.retained_bytes + PATTERN_REGISTRY.retained_bytes
        ),
    }


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
    # Deliberately **no** ``PATTERN_REGISTRY.clear()`` here. It used to be
    # belt-and-braces for a caller that built its own cache against the
    # shared registry and dropped it -- but wiping shared state to cover
    # someone else's leak is what turns a leak into corruption. The registry
    # is the *sole* strong-reference owner of every compiled pattern (a
    # match-cache entry stores a token, never the pattern object), and a
    # token is an ``id()``. Drop the registry's reference while any entry
    # still names that token and the pattern becomes collectable, its
    # address is reused by the next one, and the cache answers a lookup for
    # one vocabulary with another vocabulary's matches.
    #
    # Reachable because these three calls are not one transition: a worker
    # publishing into the new epoch between the first clear and the third
    # would have had its handle wiped underneath it. Both cache clears
    # return every handle they took -- asserted directly -- so the registry
    # is empty here whenever the caches were its only holders, which is the
    # only case the old line actually covered.
