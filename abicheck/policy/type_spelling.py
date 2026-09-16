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
from functools import lru_cache

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
# stays hard-capped. Worst case retained: 4096 entries x (<=512-char key +
# <=512-char value) ~= 4 MiB; a realistic ~40-char spelling set is ~1 MiB.
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


_strip_ptr_memo = lru_cache(maxsize=STRIP_PTR_CACHE_MAXSIZE)(strip_ptr_uncached)


def strip_ptr(type_str: str) -> str:
    """Memoised :func:`strip_ptr_uncached` -- identical result for every input.

    An over-long spelling *bypasses* the cache (it is still normalised, in full
    and unmodified); it is never truncated or rejected. That keeps one
    pathological machine-generated template spelling from consuming the byte
    budget the bound above is expressed in.
    """
    if len(type_str) > STRIP_PTR_CACHE_MAX_INPUT:
        return strip_ptr_uncached(type_str)
    return _strip_ptr_memo(type_str)


def strip_ptr_cache_stats() -> dict[str, int]:
    """Hits/misses/occupancy for the normalisation cache (tests, benchmarks)."""
    info = _strip_ptr_memo.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "occupancy": info.currsize,
        "maxsize": info.maxsize or 0,
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
    _strip_ptr_memo.cache_clear()
