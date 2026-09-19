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
surrounding performance work is trying to reduce.

**Three owners, three budgets, each cost charged exactly once.**
:class:`_PatternRegistry` is the single *accounting* owner of compiled
patterns: it issues the stable generation token both caches key on, holds
the reference that keeps a pattern reachable from these caches, and charges
each pattern's bytes once against :data:`MAX_PATTERN_BYTES`, however many
entries go on to name it. :class:`_VocabularyCache` and :class:`_MatchCache`
hold refcounted *handles* into that registry rather than the pattern object,
so neither can charge for it; the match cache's own budget
(:data:`MAX_RETAINED_BYTES`) covers only what it actually owns -- keys,
subject strings and result tuples.

Single accounting owner is **not** the same as sole strong-reference owner,
and this module deliberately does not claim the latter. The matcher's
callers hold their own ordinary references, on two different lifetimes:
``type_reachability``'s ``_StdlibReferenceScan`` keeps its stdlib, record
and typedef patterns in instance attributes for the scanner's whole life,
while ``dumper_scoping`` binds one in a local for the duration of a single
call. Those references keep the pattern alive whatever the registry does,
and they are outside every byte this module reports.

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
lookups cost 0.02 s when admitted and 41.68 s when bypassed
(``tests/test_spelling_match_cache_retention.py``).

A budget here bounds *these caches*. It is deliberately **not** a bound on
the matcher's working set, for the caller-reference reason given above.

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

import itertools
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

# The *pattern owner's* budget, and the one an entry count alone could never
# express: 64 vocabularies is 64 patterns of any size, and the vocabularies a
# real oneDAL release comparison compiles run to 3.96M characters each -- so
# the entry cap above admits a worst case in the gigabytes while reporting a
# tidy "64". This caps what :class:`_PatternRegistry` retains in bytes.
#
# Sized against the measured workload rather than picked round: that
# comparison's seven vocabularies total 9.67M pattern characters, ~87 MiB at
# the calibrated ~9 bytes/char below, and recompiling one costs seconds
# (23.42 s for those seven). 256 MiB holds that working set, and several
# members' worth of it, while still bounding the per-declaration-vocabulary
# pathology the entry cap was reaching for. Eviction here is a *cost*
# decision, never a correctness one -- an evicted pattern is recompiled on
# the next request, and a caller still holding one keeps using it.
MAX_PATTERN_BYTES = 256 * 1024 * 1024

# Rough per-entry bookkeeping overhead (key tuple, dict slot, result tuple,
# and one SpellingMatch object per match). Deliberately an estimate: the
# budget's job is to bound growth, not to report exact RSS.
_BYTES_PER_ENTRY = 224
_BYTES_PER_MATCH = 96

# Retained cost of one compiled pattern, as a multiple of its pattern text's
# length: the ``str`` itself plus the compiled program. Charged once, by
# :class:`_PatternRegistry`, for as long as either cache refers to it.
#
# This number used to be charged by the *match* cache, as incremental
# ownership per pattern it kept alive -- which double-counted a pattern the
# vocabulary cache was already holding, and made a pattern bigger than the
# match budget permanently unadmissible. See the module docstring.
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


#: One lock guarding the registry and both caches. Their refcounts are a
#: *cross-object* invariant -- an entry admitted to the match cache and the
#: registry reference it acquires must become visible together, or a
#: concurrent eviction can release a pattern an entry still names -- so the
#: coordination cannot be expressed as three independent per-object locks.
#:
#: .. note::
#:    A separate functional workstream owns thread safety for these caches
#:    (the shared match-cache race that failed three members of a traced
#:    oneDAL run). That patch was not available on ``main`` when this was
#:    written, so this lock is the *ownership redesign's own* correctness
#:    requirement, not a competing fix, and is the designated integration
#:    point: reconcile the two by keeping this single lock and folding that
#:    patch's invariants and tests onto it, rather than layering a second
#:    locking scheme over these objects.
_CACHE_LOCK = threading.RLock()


class _PatternRegistry:
    """The one owner of compiled patterns: identity, lifetime and bytes.

    Issues the **stable generation token** both caches key on. A token comes
    from a monotonic counter, never from ``id()``, so it is never recycled
    onto a different pattern and can be recorded in a diagnostic or a test
    without depending on an address.

    The registry charges the pattern's bytes **once**, against
    :data:`MAX_PATTERN_BYTES`, for as long as either cache refers to it, and
    holds the reference that keeps it reachable from here. The two caches
    hold refcounted handles rather than the pattern itself; a holder is
    dropped -- and its bytes leave the budget -- when both counts reach zero.

    This is the *accounting* owner, not the only object with a reference: a
    caller that obtained the pattern from :func:`compile_spelling_pattern`
    keeps its own, and dropping a holder here does not free a pattern that
    caller is still using. That is deliberate -- eviction is a cost
    decision, never a correctness one.

    The ``id()``-keyed lookup that maps a caller's pattern object back to its
    token is sound because of one invariant, which every mutation here
    preserves: ``_by_id`` holds an entry **iff** the registry holds a strong
    reference to that pattern. A live strong reference means the address
    cannot have been reused, so a hit is never a different pattern wearing a
    recycled id. When a holder is dropped, its ``_by_id`` entry goes with it,
    and a caller still holding that pattern simply re-registers it under a
    fresh token.
    """

    def __init__(self) -> None:
        self._tokens = itertools.count(1)
        # token -> (pattern, bytes, vocabulary refs, match refs)
        self._held: dict[int, tuple[re.Pattern[str], int, int, int]] = {}
        self._by_id: dict[int, int] = {}
        self._bytes = 0
        self.compilations_registered = 0
        self.evictions = 0

    def token_for(self, pattern: re.Pattern[str]) -> int | None:
        """*pattern*'s token, or ``None`` when it is not registered.

        Deliberately does **not** register: a lookup that will miss, and a
        result too large to retain, must not leave a holder behind charging
        for a pattern nothing stores anything against. Registration happens
        beside the reference that needs it, in :meth:`acquire`.
        """
        return self._by_id.get(id(pattern))

    def acquire(self, pattern: re.Pattern[str], *, holder: str) -> int:
        """Take a *holder* (``"vocabulary"`` or ``"match"``) reference on
        *pattern*, registering it on first use, and return its token."""
        token = self._by_id.get(id(pattern))
        if token is None:
            token = next(self._tokens)
            size = _pattern_bytes(pattern)
            self._held[token] = (pattern, size, 0, 0)
            self._by_id[id(pattern)] = token
            self._bytes += size
            self.compilations_registered += 1
        held, size, vocab_refs, match_refs = self._held[token]
        if holder == "vocabulary":
            vocab_refs += 1
        else:
            match_refs += 1
        self._held[token] = (held, size, vocab_refs, match_refs)
        return token

    def release(self, token: int, *, holder: str) -> None:
        """Drop a *holder* reference, freeing the pattern when none remain."""
        entry = self._held.get(token)
        if entry is None:
            return
        pattern, size, vocab_refs, match_refs = entry
        if holder == "vocabulary":
            vocab_refs = max(0, vocab_refs - 1)
        else:
            match_refs = max(0, match_refs - 1)
        if vocab_refs == 0 and match_refs == 0:
            del self._held[token]
            self._by_id.pop(id(pattern), None)
            self._bytes -= size
            self.evictions += 1
            return
        self._held[token] = (pattern, size, vocab_refs, match_refs)

    def over_budget(self) -> bool:
        return self._bytes > MAX_PATTERN_BYTES

    def pattern_for(self, token: int) -> re.Pattern[str] | None:
        entry = self._held.get(token)
        return None if entry is None else entry[0]

    def clear(self) -> None:
        self._held.clear()
        self._by_id.clear()
        self._bytes = 0
        self.compilations_registered = 0
        self.evictions = 0

    @property
    def retained_bytes(self) -> int:
        """Bytes this registry retains in compiled patterns, counted once each."""
        return self._bytes

    def __len__(self) -> int:
        return len(self._held)


class _MatchCache:
    """LRU over ``(pattern token, text, start, end) -> matches``.

    The pattern is identified by :class:`_PatternRegistry`'s stable token
    rather than by its own (very large) pattern text: hashing a 3 MiB
    alternation on every lookup would hand back a meaningful slice of what
    the cache saves.

    ``_bytes`` covers **only what this cache owns** -- key tuples, subject
    strings and result tuples. The compiled pattern behind a token is the
    registry's, charged there once; this cache takes a refcounted reference
    to it while it holds any entry naming it, and that reference is what
    keeps the token's ``id()`` mapping sound.
    """

    def __init__(self, registry: _PatternRegistry | None = None) -> None:
        #: Injected so a test can exercise one cache against its own owner
        #: instead of the process-wide one. Defaults to the shared registry,
        #: which is what every production caller wants.
        self._registry = registry if registry is not None else PATTERN_REGISTRY
        self._entries: OrderedDict[
            tuple[int, str, int, int], tuple[SpellingMatch, ...]
        ] = OrderedDict()
        self._bytes = 0
        #: token -> how many live entries name it, so the registry reference
        #: is taken once on the first and dropped once on the last.
        self._entries_per_token: dict[int, int] = {}
        self.hits = 0
        self.misses = 0
        self.bypasses = 0
        self.bypass_text_too_long = 0
        self.bypass_too_many_matches = 0
        self.evictions = 0

    def token_for(self, pattern: re.Pattern[str]) -> int | None:
        return self._registry.token_for(pattern)

    def get(self, key: tuple[int, str, int, int]) -> tuple[SpellingMatch, ...] | None:
        """The cached matches for *key*, or ``None`` when not yet computed.

        ``None`` means *not computed*, never *computed as empty*: an empty
        result is cached like any other, because "this text names nothing in
        this vocabulary" is exactly as reusable as a positive answer and is
        the common case for a large vocabulary.
        """
        cached = self._entries.get(key)
        if cached is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return cached

    def put(
        self,
        key_text: str,
        start: int,
        end: int,
        matches: tuple[SpellingMatch, ...],
        pattern: re.Pattern[str],
    ) -> None:
        """Retain *matches* for this (pattern, text, window), if admissible.

        The two bypasses left are the ones that are genuinely about *this
        entry's* cost -- an enormous subject string, and a pathological
        nesting result. The third, "this pattern is bigger than the whole
        match budget", is gone with the accounting that produced it: a
        pattern's size is the registry's charge, not this entry's, and
        refusing every entry for a big vocabulary released nothing while
        costing every lookup against it a full rescan.
        """
        if len(key_text) > MAX_CACHED_TEXT_CHARS:
            self.bypasses += 1
            self.bypass_text_too_long += 1
            return
        if len(matches) > MAX_CACHED_RESULT_MATCHES:
            self.bypasses += 1
            self.bypass_too_many_matches += 1
            return
        token = self._registry.acquire(pattern, holder="match")
        key = (token, key_text, start, end)
        if key in self._entries:
            # Already retained by a concurrent writer: hand the reference
            # straight back rather than double-counting this token.
            self._registry.release(token, holder="match")
            return
        self._entries[key] = matches
        self._bytes += _entry_bytes(key_text, matches)
        self._entries_per_token[token] = self._entries_per_token.get(token, 0) + 1
        while self._entries and (
            self._bytes > MAX_RETAINED_BYTES or len(self._entries) > MAX_ENTRIES
        ):
            evicted_key, evicted = self._entries.popitem(last=False)
            self._bytes -= _entry_bytes(evicted_key[1], evicted)
            self._drop_token_reference(evicted_key[0])
            self.evictions += 1

    def _drop_token_reference(self, token: int) -> None:
        remaining = self._entries_per_token.get(token, 0) - 1
        if remaining <= 0:
            self._entries_per_token.pop(token, None)
        else:
            self._entries_per_token[token] = remaining
        self._registry.release(token, holder="match")

    def drop_pattern(self, token: int) -> int:
        """Forget every entry naming *token*; returns how many were dropped.

        The coordinated half of eviction: a caller that knows a vocabulary is
        finished (end of a member comparison) can release its results without
        waiting for LRU pressure to reach them.
        """
        doomed = [k for k in self._entries if k[0] == token]
        for k in doomed:
            evicted = self._entries.pop(k)
            self._bytes -= _entry_bytes(k[1], evicted)
            self._drop_token_reference(k[0])
            self.evictions += 1
        return len(doomed)

    def clear(self) -> None:
        for token, count in list(self._entries_per_token.items()):
            for _ in range(count):
                self._registry.release(token, holder="match")
        self._entries.clear()
        self._entries_per_token.clear()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.bypasses = 0
        self.bypass_text_too_long = 0
        self.bypass_too_many_matches = 0
        self.evictions = 0

    @property
    def retained_bytes(self) -> int:
        """Estimated bytes **this cache** retains: keys, subject strings and
        results. Compiled patterns are deliberately excluded -- they are
        :class:`_PatternRegistry`'s, charged there once however many entries
        and callers name them. :func:`cache_statistics` reports both, by
        owner, so the two are never silently summed as if independent.
        """
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

    Bounded two ways, because an entry count is not a byte budget: 64
    vocabularies of 3.96M pattern characters each is gigabytes reported as
    "64". Eviction consults :data:`MAX_PATTERN_BYTES` through the registry
    as well as :data:`MAX_CACHED_VOCABULARIES`, and always leaves at least
    one entry so a single oversized vocabulary degrades to "retained while
    in use" rather than to recompiling on every request.
    """

    def __init__(self, registry: _PatternRegistry | None = None) -> None:
        self._registry = registry if registry is not None else PATTERN_REGISTRY
        self._entries: OrderedDict[frozenset[str], re.Pattern[str] | None] = (
            OrderedDict()
        )
        #: Parallel map of the registry token held for each cached pattern,
        #: so eviction releases exactly the reference this cache took.
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
        key = frozenset(spellings)
        with _CACHE_LOCK:
            if key in self._entries:
                self._entries.move_to_end(key)
                self.hits += 1
                return self._entries[key]
            self.misses += 1
        # Compiled outside the lock: building a multi-megabyte alternation
        # takes seconds, and holding the lock across it would serialize every
        # other worker's cache *hits* behind one compilation. A concurrent
        # duplicate compilation of the same vocabulary is the accepted cost --
        # it is wasted work, never a wrong answer, and the loser's pattern is
        # dropped below rather than retained beside the winner's.
        compiled = compile_fn(key)
        with _CACHE_LOCK:
            self.compilations += 1
            existing = self._entries.get(key)
            if existing is not None or key in self._entries:
                # Another worker compiled the same vocabulary while we did.
                # Return theirs and let ours be collected, so one vocabulary
                # is never retained twice.
                self._entries.move_to_end(key)
                return existing
            self._entries[key] = compiled
            if compiled is not None:
                self._tokens[key] = self._registry.acquire(
                    compiled, holder="vocabulary"
                )
            self._evict_locked()
            return compiled

    def _evict_locked(self) -> None:
        while len(self._entries) > 1 and (
            len(self._entries) > MAX_CACHED_VOCABULARIES or self._registry.over_budget()
        ):
            evicted_key, _ = self._entries.popitem(last=False)
            token = self._tokens.pop(evicted_key, None)
            if token is not None:
                self._registry.release(token, holder="vocabulary")
            self.evictions += 1

    def clear(self) -> None:
        for token in self._tokens.values():
            self._registry.release(token, holder="vocabulary")
        self._entries.clear()
        self._tokens.clear()
        self.hits = 0
        self.misses = 0
        self.compilations = 0
        self.evictions = 0

    def __len__(self) -> int:
        return len(self._entries)


PATTERN_REGISTRY = _PatternRegistry()
MATCH_CACHE = _MatchCache()
VOCABULARY_CACHE = _VocabularyCache()


def cache_statistics() -> dict[str, object]:
    """Every counter these caches keep, **grouped by owner**.

    Deliberately reports ``retained_bytes`` per owner and a ``total``, rather
    than one merged number: a pattern's cost belongs to the registry, a
    result's to the match cache, and the working set the matcher's callers
    hold in their own attributes belongs to neither. Summing them as if they
    were independent is how the double-charge this module was redesigned to
    remove got introduced in the first place.
    """
    with _CACHE_LOCK:
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
                "registered": PATTERN_REGISTRY.compilations_registered,
                "released": PATTERN_REGISTRY.evictions,
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

    *compute* runs **outside** the lock. It is a pure function of its
    captured pattern, text and window, so two workers racing on the same key
    produce equal results and :meth:`_MatchCache.put` keeps the first; doing
    it under the lock would serialize the very scans the cache exists to let
    run concurrently.
    """
    with _CACHE_LOCK:
        token = MATCH_CACHE.token_for(pattern)
        if token is not None:
            cached = MATCH_CACHE.get((token, text, start, end))
            if cached is not None:
                return cached
        else:
            # Not registered, so there is nothing to hit; count the miss the
            # same way a registered-but-absent key would.
            MATCH_CACHE.misses += 1
    normalized = tuple(
        m
        if isinstance(m, SpellingMatch)
        else SpellingMatch(m.group(0), m.start(), m.end())
        for m in compute()
    )
    with _CACHE_LOCK:
        MATCH_CACHE.put(text, start, end, normalized, pattern)
    return normalized


def clear_caches() -> None:
    """Drop everything all three owners retain. For tests and for a caller
    that wants to release the working set at a known point (end of a member
    comparison, say) rather than waiting for a byte budget to force it.
    """
    with _CACHE_LOCK:
        MATCH_CACHE.clear()
        VOCABULARY_CACHE.clear()
        PATTERN_REGISTRY.clear()
