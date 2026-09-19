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

"""The one accounting owner of compiled type-spelling patterns.

Split out of :mod:`abicheck.compare.spelling_match_cache` because it is a
different responsibility from either cache: the caches decide *what to keep*,
this decides *who pays for a pattern and when it is freed*. Keeping them in
one module also pushed that file past this repository's production line cap,
and the cap is a prompt to move responsibility out rather than to trim.

The thing it exists to prevent is a shared cost charged as though each holder
owned it. A compiled alternation is named by a vocabulary entry, by every
match entry keyed on it, and by the caller that is still matching with it;
charging its bytes to each independently over-reports retention, and -- worse
-- comparing that per-holder charge against one cache's own budget turns it
into an *admission rule keyed on something that cache does not own. That is
exactly what made a vocabulary larger than the match budget permanently
unadmissible while the vocabulary cache held the same pattern regardless, so
the refusal released nothing and every lookup recomputed forever.

**Lock order.** This module's lock is innermost. A cache takes it while
holding its own; nothing here calls back into a cache, and the caches never
call each other, so the order is total and no cycle exists. See
``spelling_match_cache``'s "Concurrency contract".
"""

from __future__ import annotations

import re
import threading
from typing import Literal

__all__ = ["MAX_PATTERN_BYTES", "PATTERN_REGISTRY", "Holder", "_PatternRegistry"]

#: The two kinds of reference a pattern can carry. They are counted
#: separately -- ``reference_counts()`` reports each, so a leak can be
#: attributed to the cache that holds it -- so a typo in a *holder* string
#: would silently decrement nothing rather than raise.
Holder = Literal["vocabulary", "match"]

# The *pattern owner's* budget, and the one an entry count alone could never
# express: 64 vocabularies is 64 patterns of any size, and the vocabularies a
# real oneDAL release comparison compiles run to 3.96M characters each -- so
# a bare entry cap admits a worst case in the gigabytes while reporting a
# tidy "64".
#
# Sized against the measured workload rather than picked round: that
# comparison's seven vocabularies total 9.67M pattern characters, ~87 MiB at
# the calibrated ~9 bytes/char, and recompiling one costs seconds (23.42 s
# for those seven). 256 MiB holds that working set, and several members'
# worth of it, while still bounding the per-declaration-vocabulary pathology
# an entry cap was reaching for. Eviction here is a *cost* decision, never a
# correctness one -- an evicted pattern is recompiled on the next request,
# and a caller still holding one keeps using it.
MAX_PATTERN_BYTES = 256 * 1024 * 1024

# Retained cost of one compiled pattern, as a multiple of its pattern text's
# length: the ``str`` itself plus the compiled program.
#
# **Calibrated, not guessed.** An initial 6 was reasoned from "the str plus
# roughly twice the text again"; measured against the six real vocabularies a
# oneDAL comparison actually compiles, it undercounts by a strikingly stable
# 1.46-1.51x across patterns spanning 8,657 to 3,336,273 characters. 9 tracks
# that (~8.8 bytes/char measured). It remains a *lower* bound:
# ``sys.getsizeof`` on a compiled pattern does not reach the internal
# allocations of its compiled program, so the real retention is higher still.
# Erring low is the wrong direction for a budget whose job is to bound
# growth.
_PATTERN_BYTES_PER_CHAR = 9


def _pattern_bytes(pattern: re.Pattern[str]) -> int:
    """Estimated bytes retained by holding *pattern* alive."""
    return len(pattern.pattern) * _PATTERN_BYTES_PER_CHAR


class _PatternRegistry:
    """The one *accounting* owner of compiled patterns.

    Holds the single reference that keeps a pattern reachable from these
    caches, and charges its bytes once against :data:`MAX_PATTERN_BYTES`
    for as long as either cache refers to it. The caches hold refcounted
    handles, one count per holder kind, and a pattern is dropped -- its
    bytes leaving the budget with it -- only when both reach zero.

    Tokens are ``id(pattern)``, deliberately unchanged from the bare-token
    scheme the caches already used: the soundness argument is the same one
    this module's concurrency contract states, and it survives here because
    a token is only ever *stored* while this registry holds a strong
    reference to the pattern it names. A live reference means the address
    cannot have been reused, so a stored token never resolves to a
    different pattern.

    This is the accounting owner, not the only object with a reference: a
    caller that obtained the pattern from ``compile_spelling_pattern`` keeps
    its own, and dropping a holder here does not free a pattern that caller
    is still using. That is deliberate -- eviction is a cost decision, never
    a correctness one.

    **Lock order.** This lock is innermost: a cache takes it while holding
    its own, nothing here calls back into a cache, and the caches never call
    each other. See the module's concurrency contract.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # token -> (pattern, bytes, vocabulary refs, match refs)
        self._held: dict[int, tuple[re.Pattern[str], int, int, int]] = {}
        self._bytes = 0
        self.registrations = 0
        self.releases = 0

    def acquire(self, pattern: re.Pattern[str], *, holder: Holder) -> int:
        """Take a *holder* (``"vocabulary"`` or ``"match"``) reference on
        *pattern*, registering it on first use, and return its token."""
        token = id(pattern)
        with self._lock:
            entry = self._held.get(token)
            if entry is None:
                size = _pattern_bytes(pattern)
                self._held[token] = (
                    pattern,
                    size,
                    1 if holder == "vocabulary" else 0,
                    0 if holder == "vocabulary" else 1,
                )
                self._bytes += size
                self.registrations += 1
                return token
            held, size, vocab_refs, match_refs = entry
            if holder == "vocabulary":
                vocab_refs += 1
            else:
                match_refs += 1
            self._held[token] = (held, size, vocab_refs, match_refs)
            return token

    def release(self, token: int, *, holder: Holder) -> None:
        """Drop a *holder* reference, freeing the pattern when none remain."""
        with self._lock:
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
                self._bytes -= size
                self.releases += 1
                return
            self._held[token] = (pattern, size, vocab_refs, match_refs)

    def is_held(self, token: int) -> bool:
        with self._lock:
            return token in self._held

    def pattern_for(self, token: int) -> re.Pattern[str] | None:
        with self._lock:
            entry = self._held.get(token)
            return None if entry is None else entry[0]

    def reference_counts(self) -> dict[int, tuple[int, int]]:
        """``token -> (vocabulary refs, match refs)``, for assertions."""
        with self._lock:
            return {t: (v, m) for t, (_p, _s, v, m) in self._held.items()}

    def over_budget(self) -> bool:
        with self._lock:
            return self._bytes > MAX_PATTERN_BYTES

    def clear(self) -> None:
        with self._lock:
            self._held.clear()
            self._bytes = 0
            self.registrations = 0
            self.releases = 0

    @property
    def retained_bytes(self) -> int:
        """Bytes retained in compiled patterns, counted once each."""
        with self._lock:
            return self._bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._held)


PATTERN_REGISTRY = _PatternRegistry()
