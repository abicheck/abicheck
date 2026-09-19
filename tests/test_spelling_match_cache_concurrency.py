# SPDX-License-Identifier: Apache-2.0
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

"""Thread-safety contract of the shared type-spelling caches.

Both caches are process-wide module globals every worker of a
directory/package ``compare`` release fan-out reads and writes. The bug
class these tests close is **a compound cache operation observed
mid-transition by another worker** -- not a torn data structure, which the
GIL already rules out. A real six-member oneDAL comparison failed three
members with ``KeyError: (140..., 'const dense&', 0, 12)`` -- a match-cache
key -- raised out of ``_MatchCache.get``'s own ``move_to_end`` after a
concurrent ``put`` evicted the key ``get`` had just read, and completed
cleanly on a rerun of the same inputs.

Each interleaving here is **forced**, not raced for: a mapping hook parks
the first thread exactly between the two halves of the transition and only
then releases the second. Under the unsynchronized implementation the
second thread's eviction lands in that window and the failure is
deterministic; under the fix the second thread cannot enter the critical
section at all, so the hook's wait times out and the transition completes
whole. Timing is never the *proof* -- it only bounds how long the fixed
implementation is given to demonstrate it holds the lock.

The oracle for every cached result remains the uncached matcher, as in
``test_spelling_match_cache.py``: a cache agreeing with itself asserts
nothing.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict

import pytest

from abicheck.compare import spelling_match_cache as smc
from abicheck.compare.spelling_match_cache import (
    MAX_ENTRIES,
    MAX_RETAINED_BYTES,
    SpellingMatch,
    matches_for,
)
from abicheck.compare.spelling_pattern import (
    _build_spelling_pattern,
    finditer_allow_nested,
    spelling_matches,
)

# How long a parked thread waits for the interleaving it is trying to
# force. Under the unsynchronized implementation the other thread arrives
# in microseconds; under the fix it cannot arrive at all, so this bounds
# the wait rather than deciding the outcome.
_INTERLEAVE_TIMEOUT = 1.0


def _pattern(*spellings: str) -> re.Pattern[str]:
    """A compiled alternation built *without* the vocabulary cache, so a
    test of one cache never depends on the other's state."""
    built = _build_spelling_pattern(frozenset(spellings))
    assert built is not None
    return built


def _uncached(pattern: re.Pattern[str], text: str) -> tuple[SpellingMatch, ...]:
    """The oracle: matches computed with no cache in the path at all."""
    return tuple(
        SpellingMatch(m.group(0), m.start(), m.end())
        for m in finditer_allow_nested(pattern, text, 0, len(text))
    )


class _HookedEntries(OrderedDict):  # type: ignore[type-arg]
    """An ``_entries`` mapping that parks its reader mid-transition.

    ``get`` signals that the cache's lookup half has run and then blocks,
    so a second thread's ``put`` is given every opportunity to evict the
    key just read before the recency-update half runs. Only the *first*
    lookup of the watched key is hooked; later ones run normally so the
    test can still observe the cache's final state.
    """

    def __init__(self, watched_key: object) -> None:
        super().__init__()
        self._watched_key = watched_key
        self.reached_lookup = threading.Event()
        self.release_reader = threading.Event()
        self.armed = True

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        value = super().get(key, default)
        if self.armed and key == self._watched_key and value is not None:
            self.armed = False
            self.reached_lookup.set()
            # Bounded: the fix holds the lock across both halves, so the
            # writer never gets here and this simply times out.
            self.release_reader.wait(_INTERLEAVE_TIMEOUT)
        return value


def _fill_to_eviction_pressure(
    cache: smc._MatchCache, pattern: re.Pattern[str]
) -> None:
    """Push *cache* to exactly one entry below its entry cap, so the very
    next admission must evict the least-recently-used entry."""
    while len(cache) < MAX_ENTRIES:
        i = len(cache)
        cache.put((cache.token_for(pattern), f"filler_{i}", 0, 1), (), pattern)


class TestForcedGetEvictRace:
    """The observed defect: lookup and recency update as separate steps."""

    def test_lookup_survives_a_concurrent_eviction_of_the_key_it_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A one-entry cap makes the evictor's *ordinary* admission evict
        # exactly the key the reader is parked on. The evictor goes through
        # the public ``put`` -- never through ``_entries`` directly -- so
        # the cache's own synchronization is what is under test.
        monkeypatch.setattr(smc, "MAX_ENTRIES", 1)
        cache = smc._MatchCache()
        pattern = _pattern("dense")
        text = "const dense&"
        key = (cache.token_for(pattern), text, 0, len(text))
        expected = _uncached(pattern, text)
        cache.put(key, expected, pattern)

        hooked = _HookedEntries(key)
        hooked.update(cache._entries)
        cache._entries = hooked  # type: ignore[assignment]

        reader_result: list[object] = []
        reader_error: list[BaseException] = []

        def reader() -> None:
            try:
                reader_result.append(cache.get(key))
            except BaseException as exc:  # noqa: BLE001 - the defect itself
                reader_error.append(exc)

        evicted = threading.Event()

        def evictor() -> None:
            # Wait until the reader is parked between the two halves, then
            # admit another entry -- which, at this cap, evicts precisely
            # the key the reader just read.
            assert hooked.reached_lookup.wait(_INTERLEAVE_TIMEOUT * 5)
            other = (cache.token_for(pattern), "other_text", 0, 10)
            cache.put(other, (), pattern)
            evicted.set()
            hooked.release_reader.set()

        reader_thread = threading.Thread(target=reader)
        evictor_thread = threading.Thread(target=evictor)
        reader_thread.start()
        evictor_thread.start()
        reader_thread.join(timeout=_INTERLEAVE_TIMEOUT * 10)
        evictor_thread.join(timeout=_INTERLEAVE_TIMEOUT * 10)
        assert not reader_thread.is_alive()
        assert not evictor_thread.is_alive()

        # The interleaving was genuinely attempted -- otherwise a passing
        # result would prove nothing about the window it is defending --
        # and the evicting admission really did happen.
        assert hooked.reached_lookup.is_set()
        assert evicted.is_set()
        # The transition completed whole: a cache read is a pure read. It
        # may never raise, and may never hand back anything but the real
        # matches for this key.
        assert not reader_error, f"cache read raised {reader_error[0]!r}"
        assert reader_result == [expected]
        _assert_accounting_consistent(cache)

    def test_a_read_never_reports_a_failed_lookup_as_an_empty_match_set(self) -> None:
        # The forbidden "fix": swallowing the KeyError and answering (). An
        # empty tuple is a *legitimate cached answer* for a text naming
        # nothing, so the two must stay distinguishable at the API.
        cache = smc._MatchCache()
        pattern = _pattern("dense")
        absent = (cache.token_for(pattern), "nothing_here", 0, 12)
        assert cache.get(absent) is None

        empty_text = "int"
        empty_key = (cache.token_for(pattern), empty_text, 0, len(empty_text))
        assert _uncached(pattern, empty_text) == ()
        cache.put(empty_key, (), pattern)
        assert cache.get(empty_key) == ()


class TestForcedVocabularyRace:
    """The same compound-operation class in the vocabulary cache."""

    def test_hit_survives_a_concurrent_eviction_of_the_key_it_matched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(smc, "MAX_CACHED_VOCABULARIES", 1)
        cache = smc._VocabularyCache()
        spellings = frozenset({"dense"})
        compiled = cache.get_or_compile(spellings, _build_spelling_pattern)

        class _HookedVocab(OrderedDict):  # type: ignore[type-arg]
            def __init__(self) -> None:
                super().__init__()
                self.reached = threading.Event()
                self.release = threading.Event()
                self.armed = True

            def __contains__(self, key: object) -> bool:
                present = super().__contains__(key)
                if self.armed and present:
                    self.armed = False
                    self.reached.set()
                    self.release.wait(_INTERLEAVE_TIMEOUT)
                return present

        hooked = _HookedVocab()
        hooked.update(cache._entries)
        cache._entries = hooked  # type: ignore[assignment]

        result: list[object] = []
        error: list[BaseException] = []

        def reader() -> None:
            try:
                result.append(cache.get_or_compile(spellings, _build_spelling_pattern))
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)

        evicted = threading.Event()

        def evictor() -> None:
            assert hooked.reached.wait(_INTERLEAVE_TIMEOUT * 5)
            # An ordinary admission of a different vocabulary, through the
            # public API: at this cap it evicts the key the reader matched.
            cache.get_or_compile(frozenset({"table"}), _build_spelling_pattern)
            evicted.set()
            hooked.release.set()

        threads = [threading.Thread(target=reader), threading.Thread(target=evictor)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_INTERLEAVE_TIMEOUT * 10)
        assert all(not t.is_alive() for t in threads)

        assert hooked.reached.is_set()
        assert evicted.is_set()
        assert not error, f"vocabulary lookup raised {error[0]!r}"
        assert result and result[0] is compiled

    def test_simultaneous_misses_publish_exactly_one_pattern(self) -> None:
        """Duplicate compilation is allowed; duplicate *publication* is not.

        Two workers may both compile the same vocabulary -- the compile is
        deliberately outside the lock so it cannot serialize the release
        fan-out -- but every caller must still receive the one published
        pattern object, or the match cache would key two token streams for
        one vocabulary.
        """
        cache = smc._VocabularyCache()
        spellings = frozenset({"dense", "table", "numeric_table"})
        at_compile = threading.Barrier(2, timeout=_INTERLEAVE_TIMEOUT * 10)
        compiles = 0
        compiles_lock = threading.Lock()

        def slow_compile(key: frozenset[str]) -> re.Pattern[str] | None:
            nonlocal compiles
            with compiles_lock:
                compiles += 1
            # Force both workers to be inside the compile simultaneously,
            # which is only reachable if the compile is lock-free.
            at_compile.wait()
            return _build_spelling_pattern(key)

        results: list[re.Pattern[str] | None] = []
        results_lock = threading.Lock()

        def worker() -> None:
            got = cache.get_or_compile(spellings, slow_compile)
            with results_lock:
                results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_INTERLEAVE_TIMEOUT * 20)
        assert all(not t.is_alive() for t in threads)

        # Both really did compile concurrently (the barrier proves the
        # compile is not serialized) ...
        assert compiles == 2
        # ... and both received the same, single published pattern.
        assert len(results) == 2
        assert results[0] is results[1]
        assert len(cache) == 1

    def test_a_failing_compilation_leaves_no_stuck_lock_or_poisoned_entry(self) -> None:
        cache = smc._VocabularyCache()
        spellings = frozenset({"dense"})

        def boom(_key: frozenset[str]) -> re.Pattern[str] | None:
            raise RuntimeError("compilation failed")

        with pytest.raises(RuntimeError, match="compilation failed"):
            cache.get_or_compile(spellings, boom)
        # No half-published entry, and the cache is still usable from this
        # thread and from another one (a stuck lock would hang the join).
        assert len(cache) == 0
        compiled = cache.get_or_compile(spellings, _build_spelling_pattern)
        assert compiled is not None

        other: list[object] = []
        thread = threading.Thread(
            target=lambda: other.append(
                cache.get_or_compile(spellings, _build_spelling_pattern)
            )
        )
        thread.start()
        thread.join(timeout=_INTERLEAVE_TIMEOUT * 5)
        assert not thread.is_alive(), "vocabulary cache lock was left held"
        assert other == [compiled]


class TestClearLifecycle:
    """``clear`` is a lifecycle reset, and it is not a barrier."""

    def test_a_computation_in_flight_across_clear_is_not_resurrected(self) -> None:
        cache = smc._MatchCache()
        pattern = _pattern("dense")
        text = "const dense&"
        key = (cache.token_for(pattern), text, 0, len(text))

        generation = cache.generation
        cache.put(key, _uncached(pattern, text), pattern)
        assert len(cache) == 1

        cache.clear()
        # The pre-clear computation lands afterwards: it belongs to a
        # lifetime that has ended, so it must not reappear.
        cache.put(key, _uncached(pattern, text), pattern, generation=generation)
        assert len(cache) == 0
        assert cache.retained_bytes == 0
        assert cache._keepalive == {}

        # A *post*-clear computation publishes normally.
        cache.put(key, _uncached(pattern, text), pattern, generation=cache.generation)
        assert len(cache) == 1

    def test_clear_under_concurrent_readers_leaves_consistent_accounting(self) -> None:
        cache = smc._MatchCache()
        pattern = _pattern("dense", "table")
        stop = threading.Event()
        errors: list[BaseException] = []

        def churn(worker: int) -> None:
            try:
                for i in range(400):
                    if stop.is_set():
                        return
                    text = f"const dense& t{worker}_{i}"
                    key = (cache.token_for(pattern), text, 0, len(text))
                    generation = cache.generation
                    if cache.get(key) is None:
                        cache.put(key, _uncached(pattern, text), pattern, generation)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                stop.set()

        clearer_errors: list[BaseException] = []

        def clearer() -> None:
            try:
                for _ in range(40):
                    cache.clear()
            except BaseException as exc:  # noqa: BLE001
                clearer_errors.append(exc)

        threads = [threading.Thread(target=churn, args=(w,)) for w in range(4)]
        threads.append(threading.Thread(target=clearer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert all(not t.is_alive() for t in threads)
        assert not errors, f"worker raised {errors[0]!r}"
        assert not clearer_errors, f"clear raised {clearer_errors[0]!r}"

        # Accounting still describes the entries that are actually present.
        _assert_accounting_consistent(cache)


def _assert_accounting_consistent(cache: smc._MatchCache) -> None:
    """Re-derive the cache's bookkeeping from its entries, independently of
    the incremental arithmetic that maintained it."""
    entries = dict(cache._entries)
    expected_refs: dict[int, int] = {}
    for token, _text, _start, _end in entries:
        expected_refs[token] = expected_refs.get(token, 0) + 1
    assert {t: r for t, (_p, r) in cache._keepalive.items()} == expected_refs, (
        "pattern reference counts disagree with the entries naming them"
    )
    expected_bytes = sum(
        smc._entry_bytes(key[1], value) for key, value in entries.items()
    ) + sum(smc._pattern_bytes(p) for p, _r in cache._keepalive.values())
    assert cache.retained_bytes == expected_bytes
    assert len(entries) <= MAX_ENTRIES
    assert cache.retained_bytes <= MAX_RETAINED_BYTES or not entries


class TestBoundedContention:
    """Many workers, forced eviction, results checked against the oracle."""

    @pytest.mark.parametrize("workers", [2, 4, 8])
    def test_concurrent_workers_agree_with_the_uncached_matcher(
        self, workers: int
    ) -> None:
        cache = smc._MatchCache()
        # Several distinct patterns, so pattern reference counting is
        # exercised by eviction rather than assumed.
        patterns = [
            _pattern("dense"),
            _pattern("dense", "table"),
            _pattern("Wrapper<Inner>", "Inner"),
            _pattern("std::string", "std::vector<std::string>"),
        ]
        texts = [
            "const dense&",
            "table*",
            "Wrapper<Inner>",
            "std::vector<std::string> const&",
            "int",  # an empty result, cached like any other
            "unrelated_identifier",
        ]
        expected = {(id(p), t): _uncached(p, t) for p in patterns for t in texts}

        mismatches: list[str] = []
        errors: list[BaseException] = []
        # Small budget so admission genuinely evicts during the run.
        original_entries = smc.MAX_ENTRIES

        def worker(seed: int) -> None:
            try:
                for i in range(600):
                    pattern = patterns[(seed + i) % len(patterns)]
                    text = texts[(seed * 3 + i) % len(texts)]
                    key = (cache.token_for(pattern), text, 0, len(text))
                    got = cache.get(key)
                    if got is None:
                        got = _uncached(pattern, text)
                        # No ``clear`` runs in this test, so no generation
                        # guard is needed -- and leaving it out keeps this
                        # test runnable against the unsynchronized
                        # implementation it is meant to falsify.
                        cache.put(key, got, pattern)
                    want = expected[(id(pattern), text)]
                    if tuple(got) != want:
                        mismatches.append(f"{text!r}: {got!r} != {want!r}")
                    # Spans, texts and ordering, not just equality of
                    # tuples: nested matches must survive reuse in order.
                    if [(m.group(), m.start(), m.end()) for m in got] != [
                        (m.group(), m.start(), m.end()) for m in want
                    ]:
                        mismatches.append(f"span/order drift for {text!r}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(s,)) for s in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert all(not t.is_alive() for t in threads)
        assert not errors, f"worker raised {errors[0]!r}"
        assert not mismatches, mismatches[:5]
        assert smc.MAX_ENTRIES == original_entries
        _assert_accounting_consistent(cache)

    def test_forced_eviction_under_contention_keeps_accounting_consistent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A tiny entry cap turns every admission into an eviction, so the
        # admit/account/retain/evict transition is what is under contention.
        monkeypatch.setattr(smc, "MAX_ENTRIES", 16)
        cache = smc._MatchCache()
        patterns = [_pattern("dense"), _pattern("table"), _pattern("dense", "table")]
        errors: list[BaseException] = []

        def worker(seed: int) -> None:
            try:
                for i in range(500):
                    pattern = patterns[(seed + i) % len(patterns)]
                    text = f"const dense& x{seed}_{i}"
                    key = (cache.token_for(pattern), text, 0, len(text))
                    if cache.get(key) is None:
                        cache.put(key, _uncached(pattern, text), pattern)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(s,)) for s in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert all(not t.is_alive() for t in threads)
        assert not errors, f"worker raised {errors[0]!r}"
        assert len(cache) <= 16
        _assert_accounting_consistent(cache)


class TestSharedGlobalsUnderContention:
    """The real entry point ``spelling_matches``/``matches_for``, on the
    module globals the release fan-out actually shares."""

    def test_module_level_matchers_are_consistent_across_threads(self) -> None:
        smc.clear_caches()
        vocabularies = [
            frozenset({"dense", "table"}),
            frozenset({"Inner", "Wrapper<Inner>"}),
            frozenset({"std::string"}),
        ]
        texts = [
            "const dense&",
            "Wrapper<Inner>*",
            "std::string const&",
            "int",
        ]
        oracle = {}
        for vocab in vocabularies:
            built = _build_spelling_pattern(vocab)
            assert built is not None
            for text in texts:
                oracle[(vocab, text)] = _uncached(built, text)

        mismatches: list[str] = []
        errors: list[BaseException] = []

        def worker(seed: int) -> None:
            try:
                from abicheck.compare.spelling_pattern import compile_spelling_pattern

                for i in range(300):
                    vocab = vocabularies[(seed + i) % len(vocabularies)]
                    text = texts[(seed * 2 + i) % len(texts)]
                    pattern = compile_spelling_pattern(vocab)
                    assert pattern is not None
                    got = spelling_matches(pattern, text)
                    want = oracle[(vocab, text)]
                    if [(m.group(), m.start(), m.end()) for m in got] != [
                        (m.group(), m.start(), m.end()) for m in want
                    ]:
                        mismatches.append(f"{sorted(vocab)} / {text!r}: {got!r}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(s,)) for s in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert all(not t.is_alive() for t in threads)
        assert not errors, f"worker raised {errors[0]!r}"
        assert not mismatches, mismatches[:5]
        _assert_accounting_consistent(smc.MATCH_CACHE)
        smc.clear_caches()

    def test_matches_for_result_is_immutable_for_every_sharing_reader(self) -> None:
        smc.clear_caches()
        pattern = _pattern("dense")
        text = "const dense&"
        first = matches_for(
            pattern, text, 0, len(text), lambda: _uncached(pattern, text)
        )
        assert isinstance(first, tuple)
        with pytest.raises(AttributeError):
            first[0]._text = "other"  # type: ignore[misc]
        second = matches_for(
            pattern, text, 0, len(text), lambda: pytest.fail("recomputed a hit")
        )
        assert second == first
        smc.clear_caches()
