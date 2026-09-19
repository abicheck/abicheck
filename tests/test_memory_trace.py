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
import os
import sys
from pathlib import Path

import pytest

from abicheck.workflows import memory_trace

#: ``/proc`` is Linux-only, and so is everything read out of it. Absent on
#: macOS and Windows is an *absent capability* for a probe of it, which is a
#: skip -- not a failure, and not a silently-passing assertion either: the
#: cross-platform half of the contract (every probe answers ``None`` rather
#: than raising) is asserted separately, on every platform, in
#: ``TestTheProbesDegradeEverywhere``.
requires_proc = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="/proc is Linux-only; the probes answer None elsewhere by design",
)
requires_sysconf = pytest.mark.skipif(
    not hasattr(os, "sysconf"), reason="os.sysconf is POSIX-only"
)


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


class TestTheProbesThemselves:
    """The probes, not just the record they produce.

    These are the lines a memory investigation trusts, and each one has a
    failure mode that looks like a plausible number rather than an error: a
    wrong page size under-reports RSS by 4x or 16x, a missed child
    under-reports the tree, and summing RSS instead of PSS over a forked
    tree over-reports it. So each is checked against an independent oracle
    rather than against itself.
    """

    @requires_sysconf
    def test_the_page_size_is_the_kernel_s_not_a_constant(self) -> None:
        """The bug a hard-coded 4096 is: correct on x86_64, wrong elsewhere."""
        assert memory_trace._page_size() == os.sysconf("SC_PAGE_SIZE")
        # ... and the module actually uses the probed value.
        assert memory_trace._PAGE_SIZE == os.sysconf("SC_PAGE_SIZE")

    def test_the_page_size_falls_back_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A platform without the constant must still produce a number."""

        def _no_such(_name: str) -> int:
            raise ValueError("unrecognised configuration name")

        # raising=False so this also covers Windows, where the attribute
        # does not exist and the fallback is the *only* path.
        monkeypatch.setattr(os, "sysconf", _no_such, raising=False)
        assert memory_trace._page_size() == 4096

    @requires_proc
    def test_self_rss_agrees_with_an_independent_reading(self) -> None:
        """``statm`` against ``status``'s VmRSS -- two different files.

        Asserting a *range* rather than equality: the two are sampled at
        different instants and the process allocates between them. What
        this rules out is the class the page-size bug belongs to, an answer
        off by a whole multiple.
        """
        got = memory_trace._self_rss_bytes()
        assert got is not None and got > 0
        vm_rss = None
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                vm_rss = int(line.split()[1]) * 1024
                break
        assert vm_rss is not None
        assert 0.5 < got / vm_rss < 2.0, (got, vm_rss)

    def test_an_unreadable_statm_is_none_not_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_open = builtins.open

        def _fail(path, *a, **kw):  # type: ignore[no-untyped-def]
            if str(path).endswith("statm"):
                raise OSError("gone")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(builtins, "open", _fail)
        assert memory_trace._self_rss_bytes() is None

    @requires_proc
    def test_the_tree_walk_finds_a_real_child(self) -> None:
        """A live child must appear, or a concurrency change looks free."""
        import subprocess

        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
        )
        try:
            pids = memory_trace._proc_tree_pids()
            assert os.getpid() in pids
            assert proc.pid in pids, "a live child was missing from the tree"
            rss, pss = memory_trace._smaps_rollup(proc.pid)
            assert rss is not None and rss > 0
            assert pss is not None and 0 < pss <= rss, (pss, rss)
            tree_rss, tree_pss, count = memory_trace._tree_memory()
            assert count >= 2
            assert tree_rss is not None and tree_pss is not None
            # PSS divides shared pages; summing RSS cannot be smaller.
            assert tree_pss <= tree_rss
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(10)

    def test_a_dead_pid_reads_as_unavailable(self) -> None:
        assert memory_trace._smaps_rollup(2**30) == (None, None)
        assert memory_trace._child_pids(2**30) == []

    def test_cgroup_reading_answers_a_pair_or_nones(self) -> None:
        """Whatever this host runs, both figures come back as ints or None.

        Deliberately not asserting a value: v1, v2 and no-cgroup are all
        legitimate hosts, and pinning one would make the test environmental.
        """
        current, peak = memory_trace._cgroup_memory()
        for value in (current, peak):
            assert value is None or isinstance(value, int)

    def test_an_unreadable_cgroup_file_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(memory_trace, "_cgroup_cache", ("/nope/current", None))
        assert memory_trace._cgroup_memory() == (None, None)


class TestPhaseEach:
    def test_each_item_is_bracketed_by_its_own_phase(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        assert list(memory_trace.phase_each("member", ["a", "b"])) == ["a", "b"]
        events = [r["event"] for r in memory_trace.read_samples(out)]
        assert events == [
            "member:enter",
            "member:exit",
            "member:enter",
            "member:exit",
        ]

    def test_it_yields_the_items_unchanged_when_tracing_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(memory_trace.ENV_TRACE_PATH, raising=False)
        memory_trace.reset_for_testing()
        assert list(memory_trace.phase_each("member", ["x", "y"])) == ["x", "y"]


class TestRecordReleaseMember:
    def test_it_records_the_decision_the_sample_and_the_table(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Three records, because none of them attributes a peak alone."""
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        monkeypatch.setattr(
            "abicheck.dumper_cache.ast_acquisition_stats",
            lambda: {"retained_groups": 2, "retained_raw_entries": 4},
        )
        memory_trace.record_release_member("libx.so", {"old_full": True})
        records = memory_trace.read_samples(out)
        assert [r["event"] for r in records] == [
            "release.member.retained",
            "release.member.retained",
            "release.ast_scope",
        ]
        assert records[0]["counts"] == {"library": "libx.so", "old_full": True}
        assert records[2]["counts"]["retained_raw_entries"] == 4

    def test_outside_an_acquisition_scope_the_table_record_is_omitted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Absent, not zero -- the same rule the probes follow."""
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        monkeypatch.setattr("abicheck.dumper_cache.ast_acquisition_stats", lambda: None)
        memory_trace.record_release_member("libx.so", {})
        assert [r["event"] for r in memory_trace.read_samples(out)] == [
            "release.member.retained",
            "release.member.retained",
        ]

    def test_it_does_nothing_when_tracing_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(memory_trace.ENV_TRACE_PATH, raising=False)
        memory_trace.reset_for_testing()
        called = {"n": 0}
        monkeypatch.setattr(
            "abicheck.dumper_cache.ast_acquisition_stats",
            lambda: called.update(n=called["n"] + 1),
        )
        memory_trace.record_release_member("libx.so", {"old_full": True})
        assert called["n"] == 0


class TestTheProbesDegradeEverywhere:
    """Off Linux the probes answer ``None``; they never raise, never guess.

    This is the half a `/proc` skip would otherwise leave unasserted, and it
    is the half that matters for a macOS or Windows run: instrumentation
    must not be able to break analysis on a platform it cannot measure, and
    an unavailable figure must read as unavailable rather than as zero.
    Runs on every platform, including Linux, where it additionally pins that
    a *readable* probe returns an ``int`` rather than an accidental string.
    """

    def test_no_probe_raises_on_any_platform(self) -> None:
        rss = memory_trace._self_rss_bytes()
        assert rss is None or isinstance(rss, int)
        tree_rss, tree_pss, count = memory_trace._tree_memory()
        assert tree_rss is None or isinstance(tree_rss, int)
        assert tree_pss is None or isinstance(tree_pss, int)
        assert isinstance(count, int) and count >= 1
        current, peak = memory_trace._cgroup_memory()
        assert current is None or isinstance(current, int)
        assert peak is None or isinstance(peak, int)
        assert isinstance(memory_trace._page_size(), int)

    def test_a_sample_is_still_written_where_nothing_can_be_probed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A host with no readable probe still gets phase boundaries.

        Simulated rather than skipped, so Linux covers the non-Linux shape:
        the record must still be written, with each unavailable figure
        `null`, because the phase labels alone are what make a trace from
        such a host worth anything.
        """
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        monkeypatch.setattr(memory_trace, "_self_rss_bytes", lambda: None)
        monkeypatch.setattr(memory_trace, "_tree_memory", lambda: (None, None, 1))
        monkeypatch.setattr(memory_trace, "_cgroup_memory", lambda: (None, None))
        with memory_trace.phase("member", library="libx.so"):
            pass
        records = memory_trace.read_samples(out)
        assert [r["event"] for r in records] == ["member:enter", "member:exit"]
        assert all(r["parent_rss_bytes"] is None for r in records)
        assert all(r["attrs"] == {"library": "libx.so"} for r in records)


class TestAPhasePeakIsThePhasesOwn:
    """The per-phase peak window (E1 instrumentation).

    A phase's ``tracemalloc_phase_peak_bytes`` must describe *that phase*.
    Without the reset, every phase after the largest one inherits the
    largest one's number and attribution becomes impossible -- which is the
    whole reason the field exists, so the tests below state it as an
    invariant over generated phase sizes rather than one hand-picked pair.
    """

    @staticmethod
    def _run(out: Path, sizes: list[int]) -> list[dict]:
        held: list[bytearray] = []
        for i, size in enumerate(sizes):
            with memory_trace.phase(f"p{i}"):
                block = bytearray(size)
                held.append(block)
                held.pop()
                del block
        return [
            r for r in memory_trace.read_samples(out) if r["event"].endswith(":exit")
        ]

    @pytest.mark.parametrize(
        "sizes",
        [
            [8_000_000, 1_000_000],
            [1_000_000, 8_000_000],
            [8_000_000, 1_000_000, 4_000_000, 500_000],
            [2_000_000] * 5,
            [500_000, 8_000_000, 500_000],
        ],
    )
    def test_each_phase_peak_tracks_its_own_allocation_not_an_earlier_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sizes: list[int]
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out, tracemalloc=True)
        exits = self._run(out, sizes)
        assert len(exits) == len(sizes)
        for size, record in zip(sizes, exits, strict=True):
            phase_peak = record["tracemalloc_phase_peak_bytes"]
            # The oracle is the allocation this phase actually made, derived
            # from the input rather than from the implementation: the phase
            # must account for its own block, and must not be inflated to a
            # bigger sibling's size. The upper bound carries the real claim
            # -- it is what an omitted reset_peak() fails.
            assert phase_peak >= size
            assert phase_peak < size + max(sizes)

    def test_the_cumulative_peak_is_the_max_over_phases(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``tracemalloc_peak_bytes`` keeps its published meaning.

        Independent oracle: the largest phase peak anywhere in the trace.
        The resets must not be allowed to lose the run's high-water.
        """
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out, tracemalloc=True)
        sizes = [1_000_000, 9_000_000, 1_000_000, 3_000_000]
        exits = self._run(out, sizes)
        cumulative = [r["tracemalloc_peak_bytes"] for r in exits]
        assert cumulative == sorted(cumulative), "cumulative peak went down"
        assert cumulative[-1] >= max(r["tracemalloc_phase_peak_bytes"] for r in exits)
        assert cumulative[-1] >= max(sizes)

    def test_the_two_peaks_are_genuinely_different_numbers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Vacuity guard.

        An implementation that reported the cumulative value in both fields
        would pass every bound above whose upper limit is generous. This
        pins that the per-phase field is actually narrower somewhere.
        """
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out, tracemalloc=True)
        exits = self._run(out, [9_000_000, 1_000_000])
        last = exits[-1]
        assert last["tracemalloc_phase_peak_bytes"] < last["tracemalloc_peak_bytes"]


class TestMarkIsAPhaseBoundary:
    def test_a_mark_records_one_sample_named_exactly_as_given(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out)
        memory_trace.mark("stage:done", library="a")
        (record,) = memory_trace.read_samples(out)
        assert record["event"] == "stage:done"
        assert record["attrs"] == {"library": "a"}

    def test_consecutive_marks_partition_the_peak_between_them(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A mark closes the window the previous mark opened.

        Same invariant as a phase, stated for the flat form: the second
        stage's peak must reflect the second stage's allocation, not the
        first stage's larger one.
        """
        out = tmp_path / "trace.jsonl"
        _enable(monkeypatch, out, tracemalloc=True)
        # Freed *before* the boundary that closes its window: a peak counts
        # still-live blocks too, so holding the big block across the second
        # mark would make the second window legitimately 9 MB and the test
        # would be asserting the wrong thing.
        big = bytearray(9_000_000)
        del big
        memory_trace.mark("first:done")
        small = bytearray(1_000_000)
        del small
        memory_trace.mark("second:done")
        first, second = memory_trace.read_samples(out)
        assert first["tracemalloc_phase_peak_bytes"] >= 9_000_000
        assert second["tracemalloc_phase_peak_bytes"] < 9_000_000

    def test_a_mark_is_a_no_op_when_tracing_is_off(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        out = tmp_path / "trace.jsonl"
        monkeypatch.delenv(memory_trace.ENV_TRACE_PATH, raising=False)
        memory_trace.reset_for_testing()
        memory_trace.mark("stage:done")
        assert not out.exists()
