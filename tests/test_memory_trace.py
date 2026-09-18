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

"""The memory trace records four *distinct* figures, and costs nothing off.

Two contracts, and the second is the one that keeps the first acceptable:

1. Each sample carries parent RSS, process-tree RSS and PSS, and cgroup
   billing as *separate* fields, with a missing probe recorded as ``null``
   rather than ``0``. Conflating them is the failure mode this module exists
   to prevent, so a test that only checked "some number was written" would
   miss exactly the defect.
2. With the env var unset, nothing is probed and nothing is written. That is
   asserted by *observing the probes*, not by checking the file is absent:
   an implementation that probed and then discarded would pass a
   file-absence check while costing every run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.workflows import memory_trace


@pytest.fixture(autouse=True)
def _reset() -> object:
    memory_trace.reset_for_testing()
    yield
    memory_trace.reset_for_testing()


def _enable(monkeypatch: pytest.MonkeyPatch, path: Path, tracemalloc: bool = False):
    monkeypatch.setenv(memory_trace.ENV_TRACE_PATH, str(path))
    if tracemalloc:
        monkeypatch.setenv(memory_trace.ENV_TRACEMALLOC, "1")
    else:
        monkeypatch.delenv(memory_trace.ENV_TRACEMALLOC, raising=False)
    memory_trace.reset_for_testing()


class TestDisabledIsFree:
    def test_nothing_is_probed_when_the_env_var_is_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv(memory_trace.ENV_TRACE_PATH, raising=False)
        memory_trace.reset_for_testing()
        probed = {"n": 0}

        def _boom() -> tuple[None, None, int]:
            probed["n"] += 1
            return (None, None, 0)

        monkeypatch.setattr(memory_trace, "_tree_memory", _boom)
        monkeypatch.setattr(
            memory_trace, "_self_rss_bytes", lambda: probed.update(n=99)
        )
        memory_trace.sample("x")
        memory_trace.counts("y", a=1)
        with memory_trace.phase("z"):
            pass
        assert probed["n"] == 0
        assert memory_trace.memory_trace_enabled() is False
        assert memory_trace.memory_trace_path() is None

    def test_an_empty_env_var_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(memory_trace.ENV_TRACE_PATH, "   ")
        memory_trace.reset_for_testing()
        assert memory_trace.memory_trace_enabled() is False


class TestTheFourFiguresStaySeparate:
    def test_a_sample_carries_each_figure_under_its_own_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        monkeypatch.setattr(memory_trace, "_self_rss_bytes", lambda: 111)
        monkeypatch.setattr(memory_trace, "_tree_memory", lambda: (222, 333, 4))
        monkeypatch.setattr(memory_trace, "_cgroup_memory", lambda: (555, 666))
        memory_trace.sample("phase", library="libx.so")
        (record,) = memory_trace.read_samples(out)
        assert record["parent_rss_bytes"] == 111
        assert record["tree_rss_bytes"] == 222
        assert record["tree_pss_bytes"] == 333
        assert record["tree_processes"] == 4
        assert record["cgroup_current_bytes"] == 555
        assert record["cgroup_peak_bytes"] == 666
        assert record["attrs"] == {"library": "libx.so"}
        # No tracemalloc unless the *separate* profiling gate is set.
        assert "tracemalloc_current_bytes" not in record

    def test_an_unavailable_probe_is_null_not_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A missing figure must be visibly missing.

        Zero is a legal memory reading; recording it for "could not read"
        would make a host without ``smaps_rollup`` or a cgroup look like a
        host using no memory, which is worse than reporting nothing.
        """
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        monkeypatch.setattr(memory_trace, "_self_rss_bytes", lambda: None)
        monkeypatch.setattr(memory_trace, "_tree_memory", lambda: (None, None, 1))
        monkeypatch.setattr(memory_trace, "_cgroup_memory", lambda: (None, None))
        memory_trace.sample("phase")
        (record,) = memory_trace.read_samples(out)
        for key in (
            "parent_rss_bytes",
            "tree_rss_bytes",
            "tree_pss_bytes",
            "cgroup_current_bytes",
            "cgroup_peak_bytes",
        ):
            assert record[key] is None

    def test_tracemalloc_is_opt_in_separately(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out, tracemalloc=True)
        memory_trace.sample("phase")
        (record,) = memory_trace.read_samples(out)
        assert isinstance(record["tracemalloc_current_bytes"], int)
        assert isinstance(record["tracemalloc_peak_bytes"], int)


class TestPhasesAndCounts:
    def test_a_phase_brackets_its_body(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        with memory_trace.phase("release.member", library="a"):
            memory_trace.counts("retained", full_snapshots=1)
        events = [r["event"] for r in memory_trace.read_samples(out)]
        assert events == [
            "release.member:enter",
            "retained",
            "release.member:exit",
        ]

    def test_a_raising_phase_still_records_its_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The sample that matters most: what was resident when it died."""
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        with pytest.raises(RuntimeError):
            with memory_trace.phase("member"):
                raise RuntimeError("boom")
        events = [r["event"] for r in memory_trace.read_samples(out)]
        assert events == ["member:enter", "member:exit"]

    def test_counts_are_structural_and_skip_the_probes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        calls = {"n": 0}

        def _tree() -> tuple[None, None, int]:
            calls["n"] += 1
            return (None, None, 0)

        monkeypatch.setattr(memory_trace, "_tree_memory", _tree)
        memory_trace.counts("resident", retained_raw_entries=4, released_raw_entries=20)
        assert calls["n"] == 0
        (record,) = memory_trace.read_samples(out)
        assert record["kind"] == "counts"
        assert record["counts"] == {
            "retained_raw_entries": 4,
            "released_raw_entries": 20,
        }

    def test_a_truncated_final_line_does_not_lose_the_rest(
        self, tmp_path: Path
    ) -> None:
        """A run killed mid-write is when the trace is needed most."""
        out = tmp_path / "trace.jsonl"
        out.write_text(
            json.dumps({"event": "a"}) + "\n" + '{"event": "b", "par',
            encoding="utf-8",
        )
        assert [r["event"] for r in memory_trace.read_samples(out)] == ["a"]

    def test_an_unwritable_destination_never_fails_the_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Observation must not be able to break analysis."""
        blocked = tmp_path / "nope"
        blocked.write_text("not a directory", encoding="utf-8")
        target = blocked / "sub" / "trace.jsonl"
        _enable(monkeypatch, target)
        memory_trace.sample("phase")
        memory_trace.counts("c", a=1)
        # Both calls returned; nothing was written, and no exception escaped.
        assert not target.exists()
