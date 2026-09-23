"""`workflows.release_admission.MemoryAdmission`: members are charged a cost
learned from the AST sizes they report, never more than the committable
budget is in flight (except one member alone), and an ungated or unmeasured
run behaves exactly as the fixed per-depth sizing did."""

from __future__ import annotations

import random
import sys
import threading
import time

import pytest

from abicheck.storage.ast_size_observer import observe_ast_sizes, report_ast_size
from abicheck.workflows.release_admission import PEAK_PER_AST_BYTE, MemoryAdmission

_GIB = 1 << 30


def test_report_without_an_observer_is_a_no_op():
    from abicheck.storage import ast_size_observer

    assert ast_size_observer._SINK.get() is None
    report_ast_size(123)  # nothing observing: must not raise
    assert ast_size_observer._SINK.get() is None


def test_observer_receives_reports_and_nests():
    outer, inner = [], []
    with observe_ast_sizes(outer.append):
        report_ast_size(5)
        with observe_ast_sizes(inner.append):
            report_ast_size(7)
        report_ast_size(0)  # an empty document is not a size
        report_ast_size(9)
    assert outer == [5, 9] and inner == [7]


def test_estimate_is_the_default_until_a_member_reports():
    gate = MemoryAdmission(10.0, default_cost_gib=4.0, floor_gib=1.0)
    with gate.admit():
        pass  # a binary-depth member: no AST
    assert gate.estimate_gib() == 4.0


@pytest.mark.parametrize("seed", range(30))
def test_learned_cost_is_the_largest_member_observed(seed):
    rng = random.Random(seed)
    gate = MemoryAdmission(None, default_cost_gib=4.0, floor_gib=1.0)
    members = [
        [rng.randrange(1, 4 * _GIB) for _ in range(rng.randrange(1, 3))]
        for _ in range(5)
    ]
    for sizes in members:
        with gate.admit():
            for n in sizes:
                report_ast_size(n)
    # Independent oracle: floor + ratio * (sum per member), maximum over members.
    expected = max(1.0 + PEAK_PER_AST_BYTE * sum(m) / _GIB for m in members)
    assert gate.estimate_gib() == pytest.approx(expected)


def test_reports_from_a_copied_context_thread_reach_the_member():
    import contextvars

    gate = MemoryAdmission(None, default_cost_gib=4.0, floor_gib=1.0)
    with gate.admit():
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(report_ast_size, _GIB))
        t.start()
        t.join()
    assert gate.estimate_gib() == pytest.approx(1.0 + PEAK_PER_AST_BYTE)


@pytest.mark.parametrize("seed", range(10))
def test_in_flight_cost_never_exceeds_committable_except_one_alone(seed):
    rng = random.Random(seed)
    committable = rng.choice([3.0, 6.0, 10.0])
    gate = MemoryAdmission(
        committable, default_cost_gib=rng.choice([1.0, 4.0]), floor_gib=1.0
    )
    lock = threading.Lock()
    running: list[float] = []
    violations: list[tuple[int, float]] = []
    peak = [0]

    def member(ast_bytes: int) -> None:
        with gate.admit():
            with lock:
                running.append(gate.estimate_gib())
                peak[0] = max(peak[0], len(running))
                # The gate's own accounting, read under its lock.
                with gate._cond:
                    if gate._in_flight > 1 and gate._committed > committable + 1e-9:
                        violations.append((gate._in_flight, gate._committed))
            time.sleep(0.002)
            report_ast_size(ast_bytes)
            with lock:
                running.pop()

    threads = [
        threading.Thread(target=member, args=(rng.randrange(1, 3 * _GIB),))
        for _ in range(12)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not violations
    assert peak[0] >= 1


def test_ungated_admits_everything_at_once():
    gate = MemoryAdmission(None, default_cost_gib=100.0, floor_gib=1.0)
    barrier = threading.Barrier(4, timeout=5)

    def member():
        with gate.admit():
            barrier.wait()  # deadlocks (BrokenBarrierError) unless all 4 run at once

    threads = [threading.Thread(target=member) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not barrier.broken


def test_smaller_measured_members_admit_more_than_the_default_would():
    """The point of the gate: 4 GiB default on 6 GiB committable admits one;
    once a member measures at 1.5 GiB, four fit."""
    gate = MemoryAdmission(6.0, default_cost_gib=4.0, floor_gib=1.0)
    with gate.admit():
        report_ast_size(_GIB // 4)  # 1.0 + 2.0 * 0.25 = 1.5 GiB
    barrier = threading.Barrier(4, timeout=5)

    def member():
        with gate.admit():
            barrier.wait()

    threads = [threading.Thread(target=member) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not barrier.broken


def test_plan_disables_the_gate_for_an_operator_budget(monkeypatch):
    from abicheck.workflows import release_jobs

    monkeypatch.setattr("abicheck.process_resources.available_mem_gib", lambda: 16.0)
    monkeypatch.setenv("ABICHECK_RELEASE_JOB_MEM_GIB", "2")
    plan = release_jobs.plan_release_workers(0, depth="headers")
    assert plan.admission._committable is None
    assert plan.pool_size == plan.initial_jobs
    monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB")
    plan = release_jobs.plan_release_workers(0, depth="headers")
    assert plan.admission._committable == pytest.approx(16.0 * 0.85 - 1.0)
    assert plan.pool_size >= plan.initial_jobs
    assert release_jobs.plan_release_workers(3).admission._committable is None


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32", reason="clang header path is exercised on ELF hosts"
)
def test_a_real_clang_dump_reports_its_ast_cold_and_warm(tmp_path, monkeypatch):
    """The sites the gate learns from: clang's spilled output on a cold run,
    the cache entry on a warm one -- both must reach the observer."""
    import shutil

    from click.testing import CliRunner

    from abicheck import snapshot_cache
    from abicheck.cli import main

    if shutil.which("clang") is None:
        pytest.skip("needs clang")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    header = tmp_path / "api.h"
    header.write_text("struct S { int a; };\nint f(struct S *);\n", encoding="utf-8")
    config = tmp_path / "cfg.yml"
    config.write_text("compile:\n  frontend: clang\n", encoding="utf-8")
    sizes: list[list[int]] = []
    for run in ("cold", "warm"):
        # A fresh whole-snapshot cache each time, so the warm run reaches
        # the AST cache instead of being served the finished snapshot.
        monkeypatch.setattr(snapshot_cache, "_CACHE_DIR", tmp_path / f"snap-{run}")
        seen: list[int] = []
        with observe_ast_sizes(seen.append):
            result = CliRunner().invoke(
                main,
                [
                    "dump",
                    "--config",
                    str(config),
                    "-H",
                    str(header),
                    "-o",
                    str(tmp_path / f"{run}.json"),
                ],
            )
        assert result.exit_code == 0, result.output
        sizes.append(seen)
    cold, warm = sizes
    assert cold and warm, sizes
    assert cold == warm  # same document, reported once per acquisition
