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
retained-bytes figure that omits an object it keeps alive. A compiled
pattern outlives the *vocabulary* cache entry that compiled it whenever a
match entry still names it, so a budget counting only entry bookkeeping
did not bound retention.

**General invariant**: ``retained_bytes`` is monotone in what is held --
retaining an additional, distinct pattern strictly increases it, releasing
the last reference to a pattern strictly decreases it, and a cleared owner
reports zero. Asserted over generated pattern sizes rather than one
reproduction, against an oracle derived from the pattern texts themselves
and not from the accounting helper under test.

Since the ownership redesign the *owner* of that number is
``_PatternRegistry``, not ``_MatchCache``: a pattern is charged once, by
the one object holding the strong reference, however many vocabulary
entries, match entries and callers name it. The invariant is unchanged;
only who answers it moved. The match cache's own ``retained_bytes`` now
covers strictly what it owns (keys, subject strings, results), which is
what makes "charged once" checkable at all.

**Bug class 3 -- an admission rule that defeats its own budget.** Charging
a *shared* resource as though each holder owned it incrementally made a
pattern larger than one cache's whole budget permanently unadmissible:
every lookup against it recomputed, while the other cache kept that exact
pattern alive, so the refusal released nothing and bought nothing. On a
real oneDAL release comparison this took the match cache from 98.99% to
63.6% hits with 411,232 bypasses, tripling matching time.

**General invariant**: admission must not depend on the size of a resource
this cache does not own. Asserted across pattern sizes spanning both sides
of every byte budget in the module -- a hot lookup is served from cache
regardless of how large its vocabulary is -- with a vacuity guard that the
entry-cost bypasses which *are* about this entry still fire.

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
from functools import cache

import pytest

from abicheck.compare.spelling_match_cache import (
    MAX_CACHED_RESULT_MATCHES,
    MAX_CACHED_TEXT_CHARS,
    MAX_RETAINED_BYTES,
    SpellingMatch,
    _MatchCache,
    _pattern_bytes,
    _PatternRegistry,
)


def _cache() -> tuple[_MatchCache, _PatternRegistry]:
    """A match cache over its *own* pattern owner.

    Injected rather than shared, so these assertions about a budget are
    not silently coupled to whatever the process-wide caches happen to
    hold when the suite runs in a different order.
    """
    registry = _PatternRegistry()
    return _MatchCache(registry), registry


@cache
def _pattern(n_spellings: int, width: int = 24) -> re.Pattern[str]:
    """A compiled literal alternation of *n_spellings* distinct spellings.

    Memoized because the 60,000-spelling case is built by both the
    parametrized admission sweep and its vacuity guard, and that alternation
    is 1.74M characters costing ~1.4 s to compile -- paid twice on every
    fast unit run for no added coverage.

    Sharing the compiled pattern is safe here precisely because of what this
    module tests: ``_cache()`` hands every test its own ``_MatchCache`` and
    ``_PatternRegistry``, so per-test accounting and eviction state stay
    isolated. The ``re.Pattern`` itself is immutable and carries no
    cache state -- the registry keys on it, it does not key on the registry.
    """
    words = [f"Sym{i:0{width}d}" for i in range(n_spellings)]
    return re.compile("|".join(re.escape(w) for w in words))


def _match(text: str = "T") -> tuple[SpellingMatch, ...]:
    return (SpellingMatch(text, 0, len(text)),)


class TestRetainedBytesCountsRetainedPatterns:
    """Bug class 1, restated for the owner that now answers it."""

    @pytest.mark.parametrize("n_spellings", [1, 8, 64, 512, 2048])
    def test_retaining_a_pattern_increases_the_reported_budget(
        self, n_spellings
    ) -> None:
        """The invariant across sizes, not one reproduction."""
        cache, registry = _cache()
        pattern = _pattern(n_spellings)
        before = registry.retained_bytes
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        after = registry.retained_bytes

        # Oracle: derived from the pattern text now held alive, not from
        # the accounting helper under test.
        assert after - before >= len(pattern.pattern), (
            "retained bytes grew by less than the pattern text it now holds"
        )

    def test_each_distinct_pattern_is_counted_once_and_only_once(self) -> None:
        """Many entries over one pattern must not multiply-count it."""
        cache, registry = _cache()
        pattern = _pattern(256)
        cache.put((cache.token_for(pattern), "t0", 0, 1), _match(), pattern)
        after_first = registry.retained_bytes
        for i in range(1, 25):
            cache.put((cache.token_for(pattern), f"t{i}", 0, 1), _match(), pattern)
        assert registry.retained_bytes == after_first, (
            "a shared pattern was charged again per entry"
        )
        # ...and the entries themselves are charged, to the cache that owns
        # them. Without this the assertion above passes for a cache that
        # stored nothing at all.
        assert len(cache) == 25
        assert cache.retained_bytes > 0

    def test_a_pattern_shared_by_both_caches_is_charged_once(self) -> None:
        """The specific double-charge the redesign removes.

        A vocabulary entry and a match entry naming one pattern is the
        ordinary production state, and it must cost one pattern, not two.
        """
        registry = _PatternRegistry()
        cache = _MatchCache(registry)
        pattern = _pattern(256)
        registry.acquire(pattern, holder="vocabulary")
        charged_once = registry.retained_bytes
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        assert registry.retained_bytes == charged_once
        assert charged_once >= len(pattern.pattern)

    def test_distinct_patterns_each_join_the_budget(self) -> None:
        cache, registry = _cache()
        patterns = [_pattern(128) for _ in range(6)]
        # Distinct objects with distinct texts, so no token can coincide.
        patterns = [re.compile(p.pattern + f"|Tail{i}") for i, p in enumerate(patterns)]
        seen = []
        for i, pattern in enumerate(patterns):
            cache.put((cache.token_for(pattern), f"t{i}", 0, 1), _match(), pattern)
            seen.append(registry.retained_bytes)
        assert seen == sorted(seen), "budget did not grow monotonically"
        assert seen[-1] >= sum(len(p.pattern) for p in patterns)

    def test_releasing_the_last_entry_releases_the_pattern_cost(self) -> None:
        cache, registry = _cache()
        pattern = _pattern(512)
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        assert registry.retained_bytes > 0
        cache.clear()
        assert cache.retained_bytes == 0
        assert registry.retained_bytes == 0
        assert len(cache) == 0
        assert len(registry) == 0

    def test_a_pattern_still_held_by_the_other_cache_is_not_released(self) -> None:
        """Refcounting, not last-writer-wins.

        Releasing the match cache's reference while a vocabulary entry still
        names the pattern must keep it charged -- it is still retained. The
        symmetric error (dropping it) would under-report retention exactly
        the way bug class 1 did.
        """
        registry = _PatternRegistry()
        cache = _MatchCache(registry)
        pattern = _pattern(256)
        registry.acquire(pattern, holder="vocabulary")
        cache.put((cache.token_for(pattern), "t", 0, 1), _match(), pattern)
        held = registry.retained_bytes
        cache.clear()
        assert registry.retained_bytes == held, (
            "pattern released while the vocabulary cache still referenced it"
        )
        registry.release(id(pattern), holder="vocabulary")
        assert registry.retained_bytes == 0

    def test_eviction_returns_the_budget_to_its_starting_point(self) -> None:
        """Round-trip oracle: put-then-evict everything must net to zero.

        Catches a sign/asymmetry error in the acquire/release pair that a
        one-directional test cannot -- an implementation that added pattern
        cost on acquire and forgot it on release would pass every "grows"
        assertion above and leak the budget upward forever.
        """
        import abicheck.compare.spelling_match_cache as mod

        cache, registry = _cache()
        start = registry.retained_bytes
        patterns = [re.compile(f"Alpha{i}|Beta{i}|Gamma{i}") for i in range(12)]
        for i, pattern in enumerate(patterns):
            cache.put((cache.token_for(pattern), f"text-{i}", 0, 4), _match(), pattern)
        assert registry.retained_bytes > start
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
        assert registry.retained_bytes == start

    def test_dropping_a_pattern_releases_every_entry_naming_it(self) -> None:
        """Coordinated eviction: the owner and the results go together."""
        cache, registry = _cache()
        pattern = _pattern(64)
        other = re.compile("Unrelated")
        for i in range(10):
            cache.put((cache.token_for(pattern), f"t{i}", 0, 1), _match(), pattern)
        cache.put((cache.token_for(other), "keep", 0, 1), _match(), other)
        assert cache.drop_pattern(id(pattern)) == 10
        assert len(cache) == 1
        assert not registry.is_held(id(pattern)), "pattern outlived its last entry"
        assert registry.is_held(id(other)), "unrelated pattern was dropped"


class TestAdmissionDoesNotDependOnPatternSize:
    """Bug class 3: a shared resource's size must not veto admission.

    The regression guard for the measured oneDAL defect. Stated as the
    invariant over the whole size range rather than as the one vocabulary
    that reproduced it, since the defect was a *rule* (charge the shared
    pattern as this entry's incremental cost) and not a threshold.
    """

    @pytest.mark.parametrize("n_spellings", [1, 16, 256, 4096, 20_000, 60_000])
    def test_a_hot_lookup_is_reused_at_every_vocabulary_size(self, n_spellings) -> None:
        """The whole point: repeated lookups hit, however big the vocabulary.

        60,000 spellings compiles to a pattern well past the 8 MiB match
        budget at the calibrated bytes-per-char -- the exact shape that was
        permanently bypassed. The oracle is the cache's own hit counter
        against a known number of repeats, not a byte figure derived from
        the accounting being exercised.
        """
        cache, _registry = _cache()
        pattern = _pattern(n_spellings)
        cache.put((cache.token_for(pattern), "hot-text", 0, 8), _match(), pattern)
        for _ in range(50):
            assert cache.get((cache.token_for(pattern), "hot-text", 0, 8)) is not None
        assert cache.hits == 50
        assert cache.bypasses == 0, (
            f"{n_spellings} spellings: admission was refused on pattern size"
        )

    def test_the_oversized_pattern_case_specifically_exceeds_the_budget(self) -> None:
        """Vacuity guard for the parametrization above.

        Without this, every case could sit *under* the budget and the sweep
        would assert nothing about the defect -- passing equally against the
        buggy implementation. Pins that the largest case really does cross
        the line that used to veto it.
        """
        pattern = _pattern(60_000)
        assert _pattern_bytes(pattern) > MAX_RETAINED_BYTES

    @pytest.mark.parametrize(
        ("text_len", "n_matches", "counter"),
        [
            (MAX_CACHED_TEXT_CHARS + 1, 1, "bypass_text_too_long"),
            (4, MAX_CACHED_RESULT_MATCHES + 1, "bypass_too_many_matches"),
        ],
    )
    def test_the_entry_cost_bypasses_still_fire(
        self, text_len, n_matches, counter
    ) -> None:
        """Vacuity guard: removing one bypass must not have removed all three.

        These two are genuinely about *this entry's* own cost -- an enormous
        subject string, a pathological nesting result -- and stay. Each is
        checked through its own named counter, so a change that collapsed
        them into one would fail here.
        """
        cache, _registry = _cache()
        pattern = _pattern(8)
        matches = tuple(SpellingMatch("T", i, i + 1) for i in range(n_matches))
        cache.put(
            (cache.token_for(pattern), "x" * text_len, 0, text_len), matches, pattern
        )
        assert len(cache) == 0
        assert cache.bypasses == 1
        assert getattr(cache, counter) == 1


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
        cache, _registry = _cache()
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


class TestClearReturnsExactlyTheHandlesItTook:
    """One handle per token, so one release per token — not one per entry.

    **Bug class.** Acquire and release counted in different units. ``_retain``
    takes a single registry handle when a token's *first* entry arrives, while
    ``_entries_per_token`` counts *entries*; a ``clear`` that released per
    entry therefore returned handles it never took.

    Invisible on the production path, which has exactly one ``_MatchCache``:
    the registry floors its counts at zero, so the surplus releases are
    absorbed. It becomes real corruption the moment two caches share a
    registry — the surplus consumes the *other* cache's handle and frees a
    pattern that cache still has entries for. That is also precisely the
    configuration these tests use, which is why the invariant is asserted
    here rather than left to the single-cache case that cannot show it.

    **General invariant**: for any distribution of entries over tokens, and
    for any number of caches sharing one registry, clearing one cache leaves
    every *other* cache's tokens still held. Swept over entry counts on both
    sides, since the defect only appears once a cache holds more than one
    entry for a token.
    """

    @pytest.mark.parametrize("entries_in_a", [1, 2, 5])
    @pytest.mark.parametrize("entries_in_b", [1, 3])
    def test_clearing_one_cache_leaves_another_cache_s_pattern_held(
        self, entries_in_a, entries_in_b
    ) -> None:
        registry = _PatternRegistry()
        first = _MatchCache(registry)
        second = _MatchCache(registry)
        pattern = _pattern(16)
        token = first.token_for(pattern)
        for i in range(entries_in_a):
            first.put((token, f"a{i}", 0, 1), _match(), pattern)
        for i in range(entries_in_b):
            second.put((token, f"b{i}", 0, 1), _match(), pattern)

        # One handle each, however many entries each holds.
        assert registry.reference_counts()[token] == (0, 2)

        first.clear()
        assert len(second) == entries_in_b, "the other cache lost entries"
        assert registry.is_held(token), (
            "clearing one cache freed a pattern the other still has entries for"
        )
        assert registry.reference_counts()[token] == (0, 1)

        second.clear()
        assert not registry.is_held(token), "the last handle was never returned"

    def test_clear_is_idempotent_against_the_registry(self) -> None:
        """Vacuity guard: a `clear` that released *nothing* would also pass
        the assertions above, and would leak instead. Clearing twice must
        return the handles once and then do nothing."""
        registry = _PatternRegistry()
        cache = _MatchCache(registry)
        pattern = _pattern(16)
        token = cache.token_for(pattern)
        for i in range(4):
            cache.put((token, f"t{i}", 0, 1), _match(), pattern)
        assert registry.is_held(token)
        cache.clear()
        assert not registry.is_held(token), "clear did not return its handle"
        cache.clear()
        assert len(registry) == 0
