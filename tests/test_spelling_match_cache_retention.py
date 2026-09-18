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

"""What the match cache retains must be what its budget counts.

Two defects with one shape: something the cache **guarantees** was
advertised rather than enforced.

**Bug class 1 -- unaccounted retention.** A bounded cache reports a
retained-bytes figure that omits an object it keeps alive. A match entry
holds a strong reference to its compiled pattern through ``_keepalive``,
and that reference outlives the *vocabulary* cache entry that compiled it
(the vocabulary cache is bounded at 64 entries and evicts independently),
so an evicted vocabulary's pattern stayed retained while the budget saw
only the entry bookkeeping around it. A budget that does not count the
largest thing it retains does not bound retention.

**General invariant**: ``retained_bytes`` is monotone in what the cache
holds -- retaining an additional, distinct pattern strictly increases it,
releasing the last entry naming a pattern strictly decreases it, and a
cleared cache reports zero. Asserted over generated pattern sizes rather
than one reproduction, against an oracle derived from the pattern texts
themselves and not from the accounting helper under test.

**Bug class 2 -- advertised-but-unenforced immutability.** ``__slots__``
bounds which attributes exist; it does not make them read-only. A cached
result tuple is handed to every later reader of the same key, so one
in-place assignment changes what the next consumer sees. Asserted over
every slot, for both set and delete, rather than for the one attribute
that happened to be probed.
"""

from __future__ import annotations

import re
import sys

import pytest

from abicheck.compare.spelling_match_cache import (
    MAX_RETAINED_BYTES,
    SpellingMatch,
    _MatchCache,
    _pattern_bytes,
)


def _pattern(n_spellings: int, width: int = 24) -> re.Pattern[str]:
    """A compiled literal alternation of *n_spellings* distinct spellings."""
    words = [f"Sym{i:0{width}d}" for i in range(n_spellings)]
    return re.compile("|".join(re.escape(w) for w in words))


def _match(text: str = "T") -> tuple[SpellingMatch, ...]:
    return (SpellingMatch(text, 0, len(text)),)


class TestRetainedBytesCountsRetainedPatterns:
    @pytest.mark.parametrize("n_spellings", [1, 8, 64, 512, 2048])
    def test_retaining_a_pattern_increases_the_reported_budget(
        self, n_spellings
    ) -> None:
        """The invariant across sizes, not one reproduction."""
        cache = _MatchCache()
        pattern = _pattern(n_spellings)
        before = cache.retained_bytes
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        after = cache.retained_bytes

        # Oracle: derived from the pattern text the cache holds alive, not
        # from the cache's own bookkeeping.
        assert after - before >= len(pattern.pattern), (
            "retained bytes grew by less than the pattern text it now holds"
        )

    def test_each_distinct_pattern_is_counted_once_and_only_once(self) -> None:
        """Many entries over one pattern must not multiply-count it."""
        cache = _MatchCache()
        pattern = _pattern(256)
        token = cache.token_for(pattern)
        cache.put((token, "t0", 0, 1), _match(), pattern)
        after_first = cache.retained_bytes
        for i in range(1, 25):
            cache.put((token, f"t{i}", 0, 1), _match(), pattern)
        growth = cache.retained_bytes - after_first
        # 24 more entries: bookkeeping only, nowhere near another pattern.
        assert growth < _pattern_bytes(pattern), (
            "a shared pattern was counted again per entry"
        )

    def test_distinct_patterns_each_join_the_budget(self) -> None:
        cache = _MatchCache()
        patterns = [_pattern(128) for _ in range(6)]
        # Distinct objects with distinct texts, so no token can coincide.
        patterns = [re.compile(p.pattern + f"|Tail{i}") for i, p in enumerate(patterns)]
        seen = []
        for i, pattern in enumerate(patterns):
            cache.put((cache.token_for(pattern), f"t{i}", 0, 1), _match(), pattern)
            seen.append(cache.retained_bytes)
        assert seen == sorted(seen), "budget did not grow monotonically"
        assert seen[-1] >= sum(len(p.pattern) for p in patterns)

    def test_releasing_the_last_entry_releases_the_pattern_cost(self) -> None:
        cache = _MatchCache()
        pattern = _pattern(512)
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        assert cache.retained_bytes > 0
        cache.clear()
        assert cache.retained_bytes == 0
        assert len(cache) == 0

    def test_eviction_returns_the_budget_to_its_starting_point(self) -> None:
        """Round-trip oracle: put-then-evict everything must net to zero.

        Catches a sign/asymmetry error in the retain/release pair that a
        one-directional test cannot -- an implementation that added pattern
        cost on retain and forgot it on release would pass every "grows"
        assertion above and leak the budget upward forever.
        """
        import abicheck.compare.spelling_match_cache as mod

        cache = _MatchCache()
        start = cache.retained_bytes
        patterns = [re.compile(f"Alpha{i}|Beta{i}|Gamma{i}") for i in range(12)]
        keys = []
        for i, pattern in enumerate(patterns):
            key = (cache.token_for(pattern), f"text-{i}", 0, 4)
            keys.append((key, pattern))
            cache.put(key, _match(), pattern)
        assert cache.retained_bytes > start
        # Force eviction of everything by shrinking the entry cap.
        original = mod.MAX_ENTRIES
        try:
            mod.MAX_ENTRIES = 0
            cache.put(
                (cache.token_for(patterns[0]), "flush", 0, 1), _match(), patterns[0]
            )
        finally:
            mod.MAX_ENTRIES = original
        assert len(cache) == 0
        assert cache.retained_bytes == start


class TestAPatternTooLargeToBudgetIsNotCachedAgainst:
    def test_an_oversized_pattern_bypasses_rather_than_thrashing(
        self, monkeypatch
    ) -> None:
        """Admitting it would evict the whole cache on every subsequent put.

        The budget is lowered rather than the pattern inflated to the real
        8 MiB cap: compiling a multi-megabyte regex costs seconds and would
        charge every run of this suite for it, while the condition under
        test is purely ``pattern cost > budget``.
        """
        import abicheck.compare.spelling_match_cache as mod

        pattern = _pattern(64)
        monkeypatch.setattr(mod, "MAX_RETAINED_BYTES", _pattern_bytes(pattern) - 1)
        assert _pattern_bytes(pattern) > mod.MAX_RETAINED_BYTES
        cache = _MatchCache()
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        assert len(cache) == 0
        assert cache.retained_bytes == 0
        assert cache.bypasses == 1

    def test_an_ordinary_pattern_is_still_admitted(self) -> None:
        """Vacuity guard: the bypass must not have swallowed the normal case."""
        cache = _MatchCache()
        pattern = _pattern(64)
        assert _pattern_bytes(pattern) < MAX_RETAINED_BYTES
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        assert len(cache) == 1
        assert cache.bypasses == 0


class TestSpellingMatchIsGenuinelyImmutable:
    @pytest.mark.parametrize("slot", ["_text", "_start", "_end"])
    def test_no_slot_can_be_reassigned(self, slot) -> None:
        match = SpellingMatch("std::vector", 4, 15)
        with pytest.raises(AttributeError):
            setattr(match, slot, "mutated")

    @pytest.mark.parametrize("slot", ["_text", "_start", "_end"])
    def test_no_slot_can_be_deleted(self, slot) -> None:
        match = SpellingMatch("std::vector", 4, 15)
        with pytest.raises(AttributeError):
            delattr(match, slot)

    def test_a_new_attribute_cannot_be_attached(self) -> None:
        match = SpellingMatch("std::vector", 4, 15)
        with pytest.raises(AttributeError):
            match.extra = 1

    def test_a_cached_result_survives_an_attempted_mutation(self) -> None:
        """The invariant that actually matters: the next reader sees the same thing."""
        cache = _MatchCache()
        pattern = _pattern(8)
        key = (cache.token_for(pattern), "std::vector<int>", 0, 16)
        cache.put(key, (SpellingMatch("std::vector", 0, 11),), pattern)

        first = cache.get(key)
        assert first is not None
        with pytest.raises(AttributeError):
            first[0]._text = "corrupted"

        second = cache.get(key)
        assert second is not None
        assert second[0].group() == "std::vector"
        assert second[0].start() == 0
        assert second[0].end() == 11

    def test_the_read_api_still_works(self) -> None:
        """Vacuity guard: immutability must not have broken construction/reads."""
        match = SpellingMatch("std::vector", 4, 15)
        assert match.group() == "std::vector"
        assert match.start() == 4
        assert match.end() == 15
        assert match == SpellingMatch("std::vector", 4, 15)
        assert hash(match) == hash(SpellingMatch("std::vector", 4, 15))


class TestThePatternEstimatorTracksRealPatterns:
    """The per-pattern cost is calibrated, and must not silently drift back.

    **Bug class.** A budget constant reasoned from first principles ("a
    str plus about twice the text") rather than measured, guarding a
    resource the program is already short of. Erring low is the failure
    that matters: the budget's whole job is to bound growth, so an
    estimate under the truth lets the cache retain more than it is
    allowed to while reporting that it did not. The original 6 undercount
    the six real vocabularies a oneDAL comparison compiles by a stable
    1.46-1.51x.

    **General invariant**: for boundary-anchored alternations built the
    way production builds them, across vocabulary sizes spanning three
    orders of magnitude, the estimate is at least the measured retention
    and within a small factor of it. The oracle is `sys.getsizeof` of the
    real compiled pattern and its source text -- not the estimator's own
    arithmetic, and not a recorded number from one vocabulary.
    """

    @pytest.mark.parametrize("n_spellings", [8, 64, 512, 4096, 16384])
    def test_the_estimate_is_not_below_measured_retention(self, n_spellings) -> None:
        """Across three orders of magnitude of vocabulary size."""
        from abicheck.compare.spelling_match_cache import _pattern_bytes
        from abicheck.compare.spelling_pattern import compile_spelling_pattern

        # Spellings shaped like the real ones: qualified, varied length.
        spellings = [
            f"ns{i % 7}::detail::v1::Type{i:06d}" + ("<int>" if i % 3 else "")
            for i in range(n_spellings)
        ]
        pattern = compile_spelling_pattern(spellings)
        assert pattern is not None
        measured = sys.getsizeof(pattern) + sys.getsizeof(pattern.pattern)
        estimate = _pattern_bytes(pattern)
        assert estimate >= measured, (
            f"{n_spellings} spellings: estimate {estimate:,} is BELOW measured "
            f"{measured:,}; the budget would under-count what it retains"
        )

    def test_the_estimate_is_not_wildly_above_measured_either(self) -> None:
        """Vacuity guard: an absurdly large constant would pass the test above.

        Setting `_PATTERN_BYTES_PER_CHAR` to a million satisfies "not
        below measured" for every input while making the budget refuse to
        cache anything, so the lower bound alone does not pin the value.
        """
        from abicheck.compare.spelling_match_cache import _pattern_bytes
        from abicheck.compare.spelling_pattern import compile_spelling_pattern

        spellings = [f"ns::Type{i:06d}" for i in range(4096)]
        pattern = compile_spelling_pattern(spellings)
        assert pattern is not None
        measured = sys.getsizeof(pattern) + sys.getsizeof(pattern.pattern)
        assert _pattern_bytes(pattern) <= measured * 4
