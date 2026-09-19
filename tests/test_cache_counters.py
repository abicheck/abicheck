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

"""The run-wide cache attribution actually reaches the trace.

**Bug class.** Instrumentation whose *wiring* is untested. Every counter
here can be individually correct while the emit never happens -- the gate
inverted, the event names misspelled, the lazy import raising under the one
condition that turns it on. Nothing fails; a benchmark simply reports
nothing and the reader concludes the run was uninteresting rather than
uninstrumented.

That is not hypothetical in this area: the benchmark harness's own guard
for exactly these counters read them from the wrong nesting level on the
first attempt (`record["patterns"]` instead of `record["counts"]["patterns"]`)
and reported 0 bytes for every run. It was caught by exercising the passing
direction, not by inspection.

**General invariant**: with tracing enabled, one call emits both event
records, each carrying the full counter shape its owner publishes, and the
values agree with what the caches report directly. With tracing disabled it
emits nothing at all and imports nothing -- asserted as the paired vacuity
guard, since an emit that fires unconditionally would pass every assertion
about presence.
"""

from __future__ import annotations

import json

import pytest

from abicheck.compare.spelling_match_cache import cache_statistics, clear_caches
from abicheck.compare.spelling_pattern import compile_spelling_pattern, spelling_matches
from abicheck.extract.cache_header_scan import reset_header_scan_statistics
from abicheck.workflows import cache_counters, memory_trace


@pytest.fixture
def traced(tmp_path, monkeypatch):
    """A trace file the counters will be written to."""
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv(memory_trace.ENV_TRACE_PATH, str(path))
    memory_trace.reset_for_testing()
    yield path
    memory_trace.reset_for_testing()


def _records(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTheCountersReachTheTrace:
    def test_both_event_records_are_emitted(self, traced) -> None:
        clear_caches()
        reset_header_scan_statistics()
        cache_counters.record_shared_cache_counters()

        events = {r.get("event") for r in _records(traced)}
        assert "release.spelling_cache" in events
        assert "release.header_scan" in events

    def test_the_emitted_values_agree_with_the_caches(self, traced) -> None:
        """Oracle: the caches' own reading, taken independently of the emit.

        Without this the presence assertion above would pass for an emit
        that wrote a constant, or the wrong owner's numbers.
        """
        clear_caches()
        reset_header_scan_statistics()
        # Do real work, so the counters are not all zero and a mix-up
        # between owners would actually show.
        pattern = compile_spelling_pattern(["dal::Table", "std::vector"])
        assert pattern is not None
        for _ in range(5):
            spelling_matches(pattern, "const dal::Table& t")

        expected = cache_statistics()
        cache_counters.record_shared_cache_counters()

        emitted = next(
            r for r in _records(traced) if r.get("event") == "release.spelling_cache"
        )
        counts = emitted["counts"]
        assert counts["match"]["hits"] == expected["match"]["hits"]
        assert counts["match"]["misses"] == expected["match"]["misses"]
        assert (
            counts["patterns"]["retained_bytes"]
            == (expected["patterns"]["retained_bytes"])
        )
        # The work above must actually have exercised the cache, or every
        # assertion here compares zero with zero.
        assert counts["match"]["hits"] > 0
        assert counts["patterns"]["retained_bytes"] > 0
        clear_caches()

    def test_every_published_counter_survives_the_round_trip(self, traced) -> None:
        """The emit must not drop a field the owner publishes.

        Keyed off ``cache_statistics()`` itself rather than a hard-coded
        list, so a counter added later is covered without editing this test
        -- and a counter that silently stops being emitted fails it.
        """
        clear_caches()
        cache_counters.record_shared_cache_counters()
        emitted = next(
            r for r in _records(traced) if r.get("event") == "release.spelling_cache"
        )
        assert set(emitted["counts"]) == set(cache_statistics())

    def test_the_header_scan_record_carries_its_ratio(self, traced) -> None:
        from pathlib import Path

        from abicheck.extract.cache_header_scan import iter_cache_header_files

        reset_header_scan_statistics()
        target = Path(__file__).parent
        for _ in range(3):
            iter_cache_header_files(target)
        cache_counters.record_shared_cache_counters()

        emitted = next(
            r for r in _records(traced) if r.get("event") == "release.header_scan"
        )
        counts = emitted["counts"]
        assert counts["calls"] == 3
        assert counts["distinct_directories"] == 1
        assert counts["repetition_factor"] == 3
        reset_header_scan_statistics()


class TestNothingIsEmittedWhenTracingIsOff:
    """The paired vacuity guard for every assertion above.

    An emit that fired unconditionally would satisfy all of them, and would
    also cost every ordinary run two `cache_statistics()` calls and two
    imports it has no use for. Tracing off is the default, so this is the
    case that actually runs in production.
    """

    def test_no_trace_file_is_written(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "trace.jsonl"
        monkeypatch.delenv(memory_trace.ENV_TRACE_PATH, raising=False)
        memory_trace.reset_for_testing()
        try:
            cache_counters.record_shared_cache_counters()
            assert not path.exists()
        finally:
            memory_trace.reset_for_testing()

    def test_the_counter_modules_are_not_even_consulted(self, monkeypatch) -> None:
        """The imports are function-local precisely so this holds.

        Enabling instrumentation must not change the module-import graph of
        a run that has it disabled, and reading the counters is not free.
        """
        monkeypatch.delenv(memory_trace.ENV_TRACE_PATH, raising=False)
        memory_trace.reset_for_testing()
        calls: list[str] = []
        monkeypatch.setattr(memory_trace, "counts", lambda *a, **k: calls.append(a[0]))
        try:
            cache_counters.record_shared_cache_counters()
            assert calls == []
        finally:
            memory_trace.reset_for_testing()
