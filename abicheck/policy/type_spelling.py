# Copyright 2026 Nikolay Petrov
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

"""Pointer/cv-token stripping for idiom recognition, with a bounded memo.

A dependency-free leaf (it imports nothing from this package), split out of
:mod:`abicheck.idioms` so the memo and the regex table have an owner of their
own rather than growing that module past its architecture line budget. It owns
exactly one transformation -- the *existing* one, unchanged -- plus the cache
that stops it being recomputed for a spelling already seen.

This is deliberately **not** a general C++ type normaliser and must not become
one: ``abicheck.model.signature_normalization`` owns cross-backend signature
canonicalisation. The transformation here is idiom recognition's own long-
standing, slightly lossy token strip, and its exact output (including the
residue two adjacent keywords leave behind) is what the recognisers in
:mod:`abicheck.idioms` are defined against.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict

_POINTER_RE = re.compile(r"[*&]")


# The *existing* sequential keyword substitutions, precompiled once instead of
# re-formatting an ``rf"\b{kw}\b"`` pattern and re-consulting ``re``'s internal
# cache on every call. Order and individual application are load-bearing: they
# are NOT equivalent to one combined alternation, which would additionally strip
# the residue two adjacent keywords leave behind (``Foo*const`` -> ``Fooconst``
# here, ``Foo`` under a combined pattern). Whether the sequential result is the
# *desirable* spelling is a separate semantic question; this module's recognition
# behaviour is defined by it, so it is preserved exactly.
_KEYWORD_RES = tuple(
    re.compile(rf"\b{kw}\b")
    for kw in ("const", "volatile", "struct", "class", "union", "enum")
)

# Bounded memoisation of the pure ``str -> str`` normalisation below. Sized from
# the observed shape of a large real workload (an externally reported oneDAL
# profile: 22,717 normalisation requests over 2,161 *unique* spellings), so a
# library of that scale fits entirely without eviction while the retained set
# stays hard-capped. Measured retained size when full (tracemalloc, not RSS):
# 2.59 MiB worst case -- 4096 entries at the 512-char admission limit, still
# 4096 after pushing 8192 distinct keys through it -- and 0.59 MiB for a
# realistic 40-character spelling set. A workload of the reported oneDAL shape
# (2,161 spellings) retains ~0.36 MiB and never evicts.
#
# Only pure strings are ever stored -- never a graph, snapshot, record, parser
# or bound method -- so the cache cannot extend any object's lifetime.
STRIP_PTR_CACHE_MAXSIZE = 4096
STRIP_PTR_CACHE_MAX_INPUT = 512


def strip_ptr_uncached(type_str: str) -> str:
    """Drop pointer/reference/cv tokens, yielding the pointee type name."""
    s = _POINTER_RE.sub("", type_str)
    for pattern in _KEYWORD_RES:
        s = pattern.sub("", s)
    return s.strip()


# The bounded LRU memo, written out rather than delegated to ``lru_cache``.
#
# ``lru_cache`` is faster, but its contents are unreachable except through
# CPython-internal details, so the "only plain strings are ever retained"
# guarantee stated above could not be *checked* -- and a guarantee whose test
# cannot observe it is not a guarantee. An ``OrderedDict`` gives the same
# semantics (bounded size, least-recently-used eviction) with a retained set a
# test can read through :func:`strip_ptr_cache_entries`. The measured cost of
# that choice is recorded in the module's tests; it is small because a
# recognition pass reaches this function a few thousand times, not millions,
# once the public-use index removes the per-record rescan.
#
# Every read and write goes through ``_CACHE_LOCK``. The individual dict
# operations are each atomic under the GIL, but the *sequence* is not: a hit
# that looks up a key, then marks it most-recently-used, can have that key
# evicted by another thread in between, and ``move_to_end`` then raises
# ``KeyError``. Independent comparisons do run concurrently (threads, not just
# processes), so this is reachable rather than theoretical -- and it is a race
# ``lru_cache`` did not have, introduced by taking the cache into Python to
# make its contents inspectable. The lock is held across the normalisation
# too: it keeps the hit/miss counters exact, and the critical section is a few
# microseconds of pure regex work with no I/O.
_strip_ptr_memo: OrderedDict[str, str] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_strip_ptr_hits = 0
_strip_ptr_misses = 0


def strip_ptr(type_str: str) -> str:
    """Memoised :func:`strip_ptr_uncached` -- identical result for every input.

    An over-long spelling *bypasses* the cache (it is still normalised, in full
    and unmodified); it is never truncated or rejected. That keeps one
    pathological machine-generated template spelling from consuming the byte
    budget the bound above is expressed in.
    """
    global _strip_ptr_hits, _strip_ptr_misses
    if len(type_str) > STRIP_PTR_CACHE_MAX_INPUT:
        return strip_ptr_uncached(type_str)
    with _CACHE_LOCK:
        cached = _strip_ptr_memo.get(type_str)
        if cached is not None:
            _strip_ptr_memo.move_to_end(type_str)
            _strip_ptr_hits += 1
            return cached
        _strip_ptr_misses += 1
        result = strip_ptr_uncached(type_str)
        _strip_ptr_memo[type_str] = result
        if len(_strip_ptr_memo) > STRIP_PTR_CACHE_MAXSIZE:
            # Evict the least recently used entry. Deliberately *not* a
            # clear(): the cache is process-wide, and dropping every entry
            # would throw away what a sibling worker mid-recognition relies on.
            _strip_ptr_memo.popitem(last=False)
        return result


def strip_ptr_cache_entries() -> tuple[tuple[str, str], ...]:
    """A snapshot of what the cache currently retains (tests, diagnostics).

    Returns copies in least- to most-recently-used order, so a caller cannot
    mutate the live mapping. This is the supported way to assert the retention
    contract -- that every retained object is a plain ``str``, and that no
    graph, snapshot, record, parser or bound method is reachable from here.
    """
    with _CACHE_LOCK:
        return tuple(_strip_ptr_memo.items())


def strip_ptr_cache_stats() -> dict[str, int]:
    """Hits/misses/occupancy for the normalisation cache (tests, benchmarks)."""
    with _CACHE_LOCK:
        return {
            "hits": _strip_ptr_hits,
            "misses": _strip_ptr_misses,
            "occupancy": len(_strip_ptr_memo),
            "maxsize": STRIP_PTR_CACHE_MAXSIZE,
        }


def strip_ptr_cache_clear() -> None:
    """Drop the memoised entries.

    For tests and benchmarks that need a *cold* cache. Production code never
    calls this: the cache is process-wide, so clearing it while a sibling
    worker is mid-recognition would throw away entries that worker is relying
    on for its own speed. Correctness is unaffected either way -- every entry
    is a pure function of its key -- but the eviction policy is deliberately
    LRU, not "clear on overflow".
    """
    global _strip_ptr_hits, _strip_ptr_misses
    with _CACHE_LOCK:
        _strip_ptr_memo.clear()
        _strip_ptr_hits = 0
        _strip_ptr_misses = 0
