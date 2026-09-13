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

"""``ClangIncludeExtractor``'s per-unit ``clang -M`` pass runs in parallel.

The pass is a pure fan-out -- one independent compiler invocation per compile
unit -- but the sequential loop it replaced made several *order-dependent*
decisions along the way (which diagnostics are recorded, in what order, which
failures count against ``diagnostics_limit``, where the walk stops). So the
invariant these tests state is not "it is faster": it is that **the result is a
function of the planned order alone, for every worker count**.

That is asserted differentially -- the same build, the same fake compiler, run
at several ``jobs`` values, compared against the ``jobs=1`` result -- with the
fake compiler deliberately *reordering completion* (staggered sleeps, slowest
unit first) so a fold that trusted completion order cannot pass. Root
``AGENTS.md``'s differential-test rule is also why each run gets its own
extractor instance and the concurrency assertions observe the *mechanism* (how
many workers were inside the gate at once), not just the output.
"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

from abicheck import deadline
from abicheck.buildsource import include_graph as ig, include_graph_workers as igw
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.include_graph import ClangIncludeExtractor

_JOB_COUNTS = (1, 2, 3, 8)


@pytest.fixture(autouse=True)
def _clang_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ig.shutil, "which", lambda _b: "/usr/bin/clang++")
    monkeypatch.delenv(igw._JOBS_ENV_VAR, raising=False)
    monkeypatch.delenv(igw._JOB_MEM_ENV_VAR, raising=False)


def _build(count: int, *, first: int = 0) -> BuildEvidence:
    return BuildEvidence(
        compile_units=[
            CompileUnit(id=f"cu://{i}", source=f"u{i}.cpp", language="CXX")
            for i in range(first, first + count)
        ]
    )


class _FakeCompiler:
    """A ``run_bounded`` stand-in whose completion order fights the planned one.

    Unit 0 sleeps the longest, so with any worker count above 1 the later units
    finish first -- the exact interleaving that makes an order-dependent fold
    produce a different map or a differently-ordered diagnostics list.
    """

    def __init__(self, count: int, *, fail: frozenset[int] = frozenset()) -> None:
        self.count = count
        self.fail = fail
        self.lock = threading.Lock()
        self.inflight = 0
        self.peak_inflight = 0
        self.calls: list[str] = []

    def __call__(
        self, cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        source = cmd[-1]
        index = int(source.removeprefix("u").removesuffix(".cpp"))
        with self.lock:
            self.inflight += 1
            self.peak_inflight = max(self.peak_inflight, self.inflight)
            self.calls.append(source)
        try:
            time.sleep(0.02 * (self.count - index))
        finally:
            with self.lock:
                self.inflight -= 1
        if index in self.fail:
            return subprocess.CompletedProcess(
                cmd, 1, "", f"{source}:1:1: fatal error: 'missing.h' file not found"
            )
        return subprocess.CompletedProcess(
            cmd, 0, f"u{index}.o: u{index}.cpp inc/shared.h inc/u{index}.h", ""
        )


def _run(jobs: int, build: BuildEvidence) -> tuple[dict, list[str]]:
    extractor = ClangIncludeExtractor(jobs=jobs)
    out = extractor.extract_from_build(build)
    return out, list(extractor.diagnostics)


@pytest.mark.parametrize("jobs", _JOB_COUNTS)
def test_result_is_independent_of_worker_count(
    monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    """Same include map for every worker count, despite reversed completion."""
    build = _build(8)
    baseline_compiler = _FakeCompiler(8)
    monkeypatch.setattr(igw.deadline, "run_bounded", baseline_compiler)
    baseline, baseline_diags = _run(1, build)
    assert len(baseline) == 8 and not baseline_diags

    compiler = _FakeCompiler(8)
    monkeypatch.setattr(igw.deadline, "run_bounded", compiler)
    out, diags = _run(jobs, build)
    assert out == baseline
    assert diags == baseline_diags
    assert sorted(compiler.calls) == sorted(baseline_compiler.calls)


@pytest.mark.parametrize("jobs", _JOB_COUNTS)
def test_diagnostics_order_follows_planned_order_not_completion_order(
    monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    """Failure diagnostics appear in compile-unit order at any worker count.

    The failing units are chosen so that completion order (unit 5 first, unit 1
    last) is the reverse of planned order: a fold that appended a diagnostic as
    each worker finished would record them backwards.
    """
    failing = frozenset({1, 3, 5})
    build = _build(6)
    compiler = _FakeCompiler(6, fail=failing)
    monkeypatch.setattr(igw.deadline, "run_bounded", compiler)

    out, diags = _run(jobs, build)

    assert set(out) == {f"cu://{i}" for i in range(6)} - {f"cu://{i}" for i in failing}
    assert [d.split()[4] for d in diags] == ["cu://1:", "cu://3:", "cu://5:"]
    assert all("missing.h" in d for d in diags)


@pytest.mark.parametrize("jobs", _JOB_COUNTS)
def test_diagnostics_limit_accounting_is_worker_count_independent(
    monkeypatch: pytest.MonkeyPatch, jobs: int
) -> None:
    """The "+N more compile units" tail counts the same however work overlaps."""
    build = _build(10)
    compiler = _FakeCompiler(10, fail=frozenset(range(10)))
    monkeypatch.setattr(igw.deadline, "run_bounded", compiler)
    extractor = ClangIncludeExtractor(jobs=jobs, diagnostics_limit=3)

    assert extractor.extract_from_build(build) == {}
    per_unit = [d for d in extractor.diagnostics if "more compile units" not in d]
    assert len(per_unit) == 3
    assert [d.split()[4] for d in per_unit] == ["cu://0:", "cu://1:", "cu://2:"]
    assert extractor.diagnostics[-1] == "clang -M failed for 7 more compile units"


def test_worker_count_actually_bounds_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe the mechanism: never more than ``jobs`` compilers in flight.

    Without this the speed claim is unverified in both directions -- a pool
    that silently ran one at a time would pass every equality test above, and
    so would one that spawned a child per unit regardless of the bound.
    """
    build = _build(12)
    compiler = _FakeCompiler(12)
    monkeypatch.setattr(igw.deadline, "run_bounded", compiler)

    ClangIncludeExtractor(jobs=3).extract_from_build(build)
    assert compiler.peak_inflight > 1, "the pool never actually overlapped any work"
    assert compiler.peak_inflight <= 3

    sequential = _FakeCompiler(12)
    monkeypatch.setattr(igw.deadline, "run_bounded", sequential)
    ClangIncludeExtractor(jobs=1).extract_from_build(build)
    assert sequential.peak_inflight == 1
    assert sequential.calls == [f"u{i}.cpp" for i in range(12)]


def test_process_wide_gate_bounds_two_concurrent_extractors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sides resolving at once share one clang-concurrency budget.

    ``service.compare`` resolves the old and new operands in separate threads,
    each building its own pool; a per-pool bound alone would let the host be
    oversubscribed by exactly the factor of sides.
    """
    build = _build(12)
    compiler = _FakeCompiler(12)
    monkeypatch.setattr(igw.deadline, "run_bounded", compiler)

    def side() -> None:
        ClangIncludeExtractor(jobs=2).extract_from_build(build)

    threads = [threading.Thread(target=side) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert compiler.peak_inflight <= 2


class TestResolveJobs:
    """Worker-count resolution: thresholds, env override, clamps.

    Lives on the scheduling module (``include_graph_workers``) rather than the
    extractor: the extractor only forwards its own ``jobs`` field.
    """

    def test_small_unit_counts_stay_sequential(self) -> None:
        for count in range(igw._PARALLEL_MIN_UNITS):
            assert igw.resolve_jobs(count) == 1

    def test_default_is_positive_and_never_exceeds_unit_count(self) -> None:
        for count in (3, 4, 16, 500):
            jobs = igw.resolve_jobs(count)
            assert 1 <= jobs <= count
            assert jobs <= igw.process_resources.jobs_ceiling()

    @pytest.mark.parametrize("raw", ["0", "1", "-4"])
    def test_env_var_disables_or_is_floored(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv(igw._JOBS_ENV_VAR, raw)
        jobs = igw.resolve_jobs(64)
        if raw == "1":
            assert jobs == 1
        else:
            # 0/negative mean "use the default", not "one worker".
            assert jobs >= 1

    def test_env_var_selects_an_explicit_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(igw._JOBS_ENV_VAR, "2")
        assert igw.resolve_jobs(64) == 2

    def test_absurd_env_value_is_clamped_not_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(igw._JOBS_ENV_VAR, "100000")
        assert igw.resolve_jobs(100000) <= igw.process_resources.jobs_ceiling()

    def test_unparsable_env_value_falls_back_and_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(igw._JOBS_ENV_VAR, "four")
        diagnostics: list[str] = []
        assert igw.resolve_jobs(64, diagnostics=diagnostics) >= 1
        assert any("ABICHECK_INCLUDE_MAP_JOBS" in d for d in diagnostics)

    def test_instance_field_outranks_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(igw._JOBS_ENV_VAR, "8")
        assert igw.resolve_jobs(64, jobs=2) == 2

    def test_memory_clamp_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(igw.process_resources, "available_mem_gib", lambda: 0.5)
        monkeypatch.setenv(igw._JOB_MEM_ENV_VAR, "0.25")
        assert igw.resolve_jobs(64) == 2


class TestDeadlinesUnderParallelism:
    """The deadline contracts the sequential loop established, per worker."""

    def test_scan_deadline_propagates_into_pool_workers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A worker must see the outer ``--budget``, which contextvars do not carry.

        Without the explicit re-establishment, ``deadline.remaining()`` inside a
        pool worker reads ``None`` and every ``run_bounded`` call silently falls
        back to its fixed local timeout regardless of the user's budget.
        """
        seen: list[float | None] = []

        def _observe(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            seen.append(deadline.remaining())
            assert isinstance(kwargs["timeout"], float)
            return subprocess.CompletedProcess(cmd, 0, "a.o: a.cpp x.h", "")

        monkeypatch.setattr(igw.deadline, "run_bounded", _observe)
        extractor = ClangIncludeExtractor(jobs=4)
        with deadline.deadline_scope(30.0):
            extractor.extract_from_build(_build(6))
        assert len(seen) == 6
        assert all(r is not None and 0 < r <= 30.0 for r in seen)

    def test_scan_deadline_exceeded_stops_the_walk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An outer-budget overflow discards later units' maps, as before."""

        def _raise(*_a: object, **_k: object) -> None:
            raise deadline.DeadlineExceeded(-1.0)

        monkeypatch.setattr(igw.deadline, "run_bounded", _raise)
        extractor = ClangIncludeExtractor(jobs=4)
        with deadline.deadline_scope(5.0):  # tighter than the 120s per-unit cap
            assert extractor.extract_from_build(_build(6)) == {}
        assert any("scan deadline exceeded" in d for d in extractor.diagnostics)

    def test_per_unit_timeout_degrades_without_dropping_other_units(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One unit's own timeout is a per-CU diagnostic, at any worker count."""

        def _run_one(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
            if cmd[-1] == "u2.cpp":
                raise deadline.DeadlineExceeded(-1.0)
            return subprocess.CompletedProcess(cmd, 0, "o: s.cpp inc/x.h", "")

        monkeypatch.setattr(igw.deadline, "run_bounded", _run_one)
        extractor = ClangIncludeExtractor(jobs=4)
        out = extractor.extract_from_build(_build(5))
        assert set(out) == {"cu://0", "cu://1", "cu://3", "cu://4"}
        assert any("clang -M timed out for cu://2" in d for d in extractor.diagnostics)

    def test_aggregate_budget_exhaustion_truncates_and_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Units never started once the wall-clock budget ran out are reported.

        The count in the message is the number of units that produced a map,
        so it stays meaningful when a few in-flight results are discarded.
        """
        calls: list[str] = []

        def _slow(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd[-1])
            time.sleep(0.05)
            return subprocess.CompletedProcess(cmd, 0, "o: s.cpp inc/x.h", "")

        monkeypatch.setattr(igw.deadline, "run_bounded", _slow)
        extractor = ClangIncludeExtractor(jobs=2, aggregate_timeout_s=0.12)
        out = extractor.extract_from_build(_build(40))
        assert 0 < len(out) < 40
        assert len(calls) < 40, "the budget must stop work, not just trim results"
        assert any("time budget exhausted" in d for d in extractor.diagnostics)

    def test_cap_diagnostic_lands_after_the_units_own_diagnostics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Order fidelity: the cap ended the walk, so it is reported last.

        Planning the invocations up front makes it tempting to record the cap
        the moment it is hit, which would move it *ahead* of the diagnostics of
        units that ran before it -- the reverse of the sequential loop, where
        the cap was the last thing that happened.
        """
        compiler = _FakeCompiler(10, fail=frozenset({0, 1}))
        monkeypatch.setattr(igw.deadline, "run_bounded", compiler)
        extractor = ClangIncludeExtractor(jobs=4, max_compile_units=4)
        extractor.extract_from_build(_build(10))
        assert [d.split()[4] for d in extractor.diagnostics[:2]] == [
            "cu://0:",
            "cu://1:",
        ]
        assert extractor.diagnostics[-1] == (
            "clang -M include-map budget exhausted: stopped after 4 compile units"
        )

    def test_a_walk_that_stopped_early_does_not_also_claim_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One termination reason, not two: a deadline stop never reached the cap.

        The sequential loop could only ever leave the walk for one reason; with
        every invocation planned up front, both conditions are *knowable* at
        once, so the fold has to keep them exclusive.
        """

        def _raise(*_a: object, **_k: object) -> None:
            raise deadline.DeadlineExceeded(-1.0)

        monkeypatch.setattr(igw.deadline, "run_bounded", _raise)
        extractor = ClangIncludeExtractor(jobs=4, max_compile_units=4)
        with deadline.deadline_scope(5.0):
            extractor.extract_from_build(_build(10))
        assert any("scan deadline exceeded" in d for d in extractor.diagnostics)
        assert not any("budget exhausted" in d for d in extractor.diagnostics)

    def test_compile_unit_cap_is_planned_before_any_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``max_compile_units`` still bounds how many compilers ever run."""
        compiler = _FakeCompiler(20)
        monkeypatch.setattr(igw.deadline, "run_bounded", compiler)
        extractor = ClangIncludeExtractor(jobs=4, max_compile_units=5)
        out = extractor.extract_from_build(_build(20))
        assert len(out) == 5
        assert len(compiler.calls) == 5
        assert extractor.diagnostics == [
            "clang -M include-map budget exhausted: stopped after 5 compile units"
        ]


def test_os_error_is_reported_per_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ``OSError`` from one spawn does not abort the rest of the fan-out."""

    def _flaky(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        if cmd[-1] == "u1.cpp":
            raise OSError("Too many open files")
        return subprocess.CompletedProcess(cmd, 0, "o: s.cpp inc/x.h", "")

    monkeypatch.setattr(igw.deadline, "run_bounded", _flaky)
    extractor = ClangIncludeExtractor(jobs=4)
    out = extractor.extract_from_build(_build(4))
    assert set(out) == {"cu://0", "cu://2", "cu://3"}
    assert extractor.diagnostics == ["clang -M failed for cu://1: Too many open files"]


def test_units_without_a_source_are_skipped_before_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler = _FakeCompiler(4)
    monkeypatch.setattr(igw.deadline, "run_bounded", compiler)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://0", source="u0.cpp"),
            CompileUnit(id="cu://nosrc", source=""),
            CompileUnit(id="cu://1", source="u1.cpp"),
            CompileUnit(id="cu://2", source="u2.cpp"),
        ]
    )
    out = ClangIncludeExtractor(jobs=4).extract_from_build(build)
    assert set(out) == {"cu://0", "cu://1", "cu://2"}


def test_clang_slots_are_reused_for_a_stable_limit() -> None:
    """The process-wide gate is one object per size, not one per call."""
    first = igw._clang_slots(3)
    assert igw._clang_slots(3) is first
    assert igw._clang_slots(4) is not first


@pytest.mark.integration
def test_real_clang_parallel_matches_serial_byte_for_byte(tmp_path) -> None:
    """Live ``clang -M``: every depfile identical to the serial run's.

    The mocked tests above prove the fold is order-independent; only a real
    compiler proves the *commands* the workers run are still each unit's own
    (a shared cwd, a shared temp path, or a leaked ``-o`` would show up here
    and nowhere else).
    """
    if ig.shutil.which("clang++") is None:  # pragma: no cover - env dependent
        pytest.skip("clang++ not available")
    (tmp_path / "base.h").write_text("#pragma once\nstruct B { int x; };\n")
    units = []
    for i in range(8):
        header = tmp_path / f"w{i}.h"
        header.write_text(f'#pragma once\n#include "base.h"\nstruct W{i} {{ B b; }};\n')
        units.append(
            CompileUnit(
                id=f"cu://{i}",
                source=str(header),
                argv=[f"-I{tmp_path}", str(header)],
                language="CXX",
            )
        )
    build = BuildEvidence(compile_units=units)

    serial = ClangIncludeExtractor(jobs=1)
    expected = serial.extract_from_build(build)
    assert not serial.diagnostics
    assert len(expected) == 8

    for jobs in (2, 4):
        parallel = ClangIncludeExtractor(jobs=jobs)
        assert parallel.extract_from_build(build) == expected
        assert not parallel.diagnostics
