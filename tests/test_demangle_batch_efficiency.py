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

"""``demangle_batch`` must not do the same native work twice.

Two independent defects, both about *redundant* work rather than a wrong
answer -- which is why neither produced a failing test anywhere:

* **Within a batch.** ``_batch_phase1_cache`` filtered its input against
  the process cache but did not deduplicate it, and neither downstream
  phase re-consults the cache it is populating. One symbol passed N times
  in a single call therefore reached ``cxxfilt``/``c++filt`` N times.
* **Across batches.** Both caches cleared themselves *wholesale* at their
  65,536-entry bound, so recording one symbol at the limit discarded every
  previously resolved name. A working set larger than the bound did not
  degrade gracefully; it fell off a cliff and re-resolved everything.

The oracle throughout is the *count of names handed to the demangler*,
observed by wrapping the real phase functions -- not the wall-clock time
(which varies) and not the cache's own bookkeeping. Correctness is asserted
alongside efficiency in every case: a deduplicating or evicting
implementation that dropped or altered a mapping entry would be worse than
the redundancy it replaced.

Registry: ``demangle.redundant_native_work`` in
``tests/regressions/manifest_performance.py``.
"""

from __future__ import annotations

import random
import sys
from types import SimpleNamespace

import pytest

import abicheck.demangle as dm

# Real Itanium-mangled names, chosen so the suite does not depend on a
# demangler being installed to at least exercise the *routing*.
_NAMES = [
    "_ZN4daal15data_management10interface119HomogenNumericTableIxE18releaseBlockOfRowsERNS1_15BlockDescriptorIdEE",
    "_ZN3ns14FooC1Ev",
    "_ZN3ns14Foo3barEi",
    "_ZNK3ns14Foo5valueEv",
    "_ZTVN3ns14FooE",
    "_Z3addii",
    "_ZSt4sortIPiEvT_S1_",
]


@pytest.fixture(autouse=True)
def _clean_cache():
    dm._reset_demangle_batch_cache()
    yield
    dm._reset_demangle_batch_cache()


class _Counter:
    """Wraps both resolution phases and records how many names each saw."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.phase2 = 0
        self.phase3 = 0
        real2 = dm._batch_phase2_cxxfilt
        real3 = dm._batch_phase3_cppfilt

        def counting2(uncached, result):
            self.phase2 += len(uncached)
            return real2(uncached, result)

        def counting3(remaining, result):
            self.phase3 += len(remaining)
            return real3(remaining, result)

        monkeypatch.setattr(dm, "_batch_phase2_cxxfilt", counting2)
        monkeypatch.setattr(dm, "_batch_phase3_cppfilt", counting3)

    @property
    def submitted(self) -> int:
        return self.phase2


class TestWithinBatchDeduplication:
    def test_one_symbol_repeated_is_resolved_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        counter = _Counter(monkeypatch)
        out = dm.demangle_batch([_NAMES[1]] * 50)
        assert counter.submitted == 1, "a repeated symbol reached the demangler twice"
        # Correctness is not traded for the saving: the mapping is whatever
        # a single-element call would produce.
        dm._reset_demangle_batch_cache()
        assert out == dm.demangle_batch([_NAMES[1]])

    @pytest.mark.parametrize("dup", [2, 3, 7, 24])
    def test_submission_count_is_the_unique_count_for_any_duplication(
        self, dup: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The invariant, over several independently chosen factors.

        Stated as "submitted == unique", not "submitted < len(input)": the
        latter passes for an implementation that removes only *adjacent*
        duplicates, which is the obvious wrong fix.
        """
        counter = _Counter(monkeypatch)
        batch = _NAMES * dup
        dm.demangle_batch(batch)
        assert counter.submitted == len(set(_NAMES))

    def test_duplicates_are_deduplicated_however_they_are_interleaved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Order must not matter -- adjacent, interleaved or shuffled alike."""
        rng = random.Random(4)
        for arrangement in (
            [n for n in _NAMES for _ in range(4)],  # adjacent runs
            _NAMES * 4,  # interleaved cycles
            rng.sample(_NAMES * 4, len(_NAMES) * 4),  # shuffled
        ):
            dm._reset_demangle_batch_cache()
            counter = _Counter(monkeypatch)
            dm.demangle_batch(arrangement)
            assert counter.submitted == len(set(_NAMES)), arrangement[:3]

    def test_a_deduplicated_batch_still_maps_every_occurrence(self) -> None:
        """Every input name must appear in the result, not just the first.

        The result is keyed by symbol, so deduplication is invisible to a
        caller -- but a fix that deduplicated the *result* rather than the
        work queue would break exactly this.
        """
        out = dm.demangle_batch(_NAMES * 3)
        single = {}
        for name in _NAMES:
            dm._reset_demangle_batch_cache()
            single.update(dm.demangle_batch([name]))
        assert out == single

    def test_a_partially_cached_batch_submits_only_the_new_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The warm-up has to actually *succeed* for the second call's
        # submission count to mean anything, and on a host with neither
        # cxxfilt nor c++filt it resolves nothing. Inject a deterministic
        # in-process demangler so this test measures cache reuse rather
        # than the environment's demangler inventory.
        monkeypatch.setitem(
            sys.modules,
            "cxxfilt",
            SimpleNamespace(demangle=lambda name: f"demangled({name})"),
        )
        dm.demangle_batch(_NAMES[:3])
        # Vacuity guard: without three cached successes the count below
        # would be satisfied by an implementation that caches nothing.
        assert len(dm._BATCH_CACHE_OK) == 3
        counter = _Counter(monkeypatch)
        dm.demangle_batch(_NAMES * 5)
        assert counter.submitted == len(set(_NAMES)) - 3


class TestCacheEvictionIsIncremental:
    """A working set past the bound degrades, it does not fall off a cliff."""

    @staticmethod
    def _fill_ok(n: int) -> None:
        for i in range(n):
            dm._batch_cache_record_ok(f"_ZN1f{i}Ev", f"f{i}()")

    def test_recording_one_entry_at_the_bound_drops_exactly_one(self) -> None:
        """The deterministic control at the actual threshold.

        Before: 65,536 cached successes, insert one more, **one** entry
        remained. This asserts the exact boundary rather than a percentage,
        because the defect was specifically a whole-cache clear.
        """
        self._fill_ok(dm._BATCH_CACHE_MAX)
        assert len(dm._BATCH_CACHE_OK) == dm._BATCH_CACHE_MAX
        oldest = next(iter(dm._BATCH_CACHE_OK))
        dm._batch_cache_record_ok("_ZN3newEv", "new()")
        assert len(dm._BATCH_CACHE_OK) == dm._BATCH_CACHE_MAX
        assert oldest not in dm._BATCH_CACHE_OK
        assert dm._BATCH_CACHE_OK["_ZN3newEv"] == "new()"

    def test_the_failure_cache_evicts_the_same_way(self) -> None:
        """Both caches, not just the one the incident was noticed on."""
        for i in range(dm._BATCH_CACHE_MAX):
            dm._batch_cache_record_fail(f"_Zbad{i}")
        oldest = next(iter(dm._BATCH_CACHE_FAIL))
        dm._batch_cache_record_fail("_Zbad_new")
        assert len(dm._BATCH_CACHE_FAIL) == dm._BATCH_CACHE_MAX
        assert oldest not in dm._BATCH_CACHE_FAIL
        assert "_Zbad_new" in dm._BATCH_CACHE_FAIL

    def test_the_cache_stays_full_across_an_oversized_working_set(self) -> None:
        """Past the bound, retention plateaus at the bound rather than sawtoothing.

        Measured on a real oneDAL DSO (``libonedal_core.so.1``, 91,510
        unique mangled exports -- genuinely larger than the bound): under
        the whole-cache clear only 25,974 entries survived a full pass and
        65,536 names had to be re-resolved on the next one; with
        oldest-entry eviction 65,536 survive and 25,974 are re-resolved.
        This states the property that produced that difference, at a
        smaller scale so it runs in the unit lane.
        """
        overshoot = dm._BATCH_CACHE_MAX + 5_000
        low_water = dm._BATCH_CACHE_MAX
        for i in range(overshoot):
            dm._batch_cache_record_ok(f"_ZN1g{i}Ev", f"g{i}()")
            if i >= dm._BATCH_CACHE_MAX:
                low_water = min(low_water, len(dm._BATCH_CACHE_OK))
        assert len(dm._BATCH_CACHE_OK) == dm._BATCH_CACHE_MAX
        # The cliff was a drop to ~1; anything near the bound is a plateau.
        assert low_water == dm._BATCH_CACHE_MAX

    def test_the_most_recent_entries_are_the_ones_kept(self) -> None:
        """FIFO, not arbitrary: the names most recently resolved survive.

        A "drop one entry" fix that picked the *newest* would keep the
        cache full while retaining nothing useful, and would pass the size
        assertions above.
        """
        self._fill_ok(dm._BATCH_CACHE_MAX)
        newest_before = list(dm._BATCH_CACHE_OK)[-100:]
        for i in range(100):
            dm._batch_cache_record_ok(f"_ZN5extra{i}Ev", f"extra{i}()")
        for key in newest_before:
            assert key in dm._BATCH_CACHE_OK
        assert all(f"_ZN5extra{i}Ev" in dm._BATCH_CACHE_OK for i in range(100))

    def test_re_recording_an_existing_key_never_evicts(self) -> None:
        """An update is not an insertion.

        Without the membership check, a cache sitting at the bound would
        evict a live entry on every *re-record* of a name it already held —
        so a caller repeatedly resolving one hot symbol would evict the
        rest of its working set one entry at a time.
        """
        self._fill_ok(dm._BATCH_CACHE_MAX)
        oldest = next(iter(dm._BATCH_CACHE_OK))
        hot = list(dm._BATCH_CACHE_OK)[-1]
        for _ in range(50):
            dm._batch_cache_record_ok(hot, dm._BATCH_CACHE_OK[hot])
        assert len(dm._BATCH_CACHE_OK) == dm._BATCH_CACHE_MAX
        assert oldest in dm._BATCH_CACHE_OK

    def test_evicted_names_are_re_resolved_not_reported_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Eviction must cost work, never correctness.

        A cache that "remembered" an evicted name as absent would silently
        stop demangling it.
        """
        out_first = dm.demangle_batch([_NAMES[1]])
        dm._BATCH_CACHE_OK.clear()
        dm._BATCH_CACHE_FAIL.clear()
        counter = _Counter(monkeypatch)
        out_again = dm.demangle_batch([_NAMES[1]])
        assert counter.submitted == 1
        assert out_again == out_first
