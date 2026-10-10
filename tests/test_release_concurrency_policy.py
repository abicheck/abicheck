"""Release fan-out concurrency policy: level-1 member cap, level-2 shared probe
pool, and the single-poller probe gate.

Stated as invariants over enumerated input domains rather than one example
each, per AGENTS.md's bug-class testing guidance: the defects these guard
(a pool sized from CPUs rather than from what can execute, a thread per
unit per member, every queued waiter polling) were each a property of the
*sizing rule*, not of one input.
"""

from __future__ import annotations

import itertools
import random
import threading
import time

import pytest

from abicheck import process_resources
from abicheck.buildsource import include_graph_workers as igw
from abicheck.workflows import release_jobs
from abicheck.workflows.keyed_thread_pool import run_keyed_in_threads

# ---------------------------------------------------------------------------
# Level 1: python_parallelism
# ---------------------------------------------------------------------------


class TestPythonParallelism:
    @pytest.mark.parametrize("cpus", [1, 2, 4, 64, 224])
    def test_gil_interpreter_runs_a_constant_number_of_members(
        self, monkeypatch: pytest.MonkeyPatch, cpus: int
    ) -> None:
        monkeypatch.delenv(process_resources.MEMBER_JOBS_ENV_VAR, raising=False)
        monkeypatch.setattr(process_resources.os, "cpu_count", lambda: cpus)
        monkeypatch.setattr(process_resources, "gil_enabled", lambda: True)
        assert (
            process_resources.python_parallelism()
            == process_resources.GIL_MEMBER_PARALLELISM
        )

    @pytest.mark.parametrize("cpus", [1, 2, 4, 64, 224])
    def test_free_threaded_interpreter_scales_with_cpus_up_to_the_cap(
        self, monkeypatch: pytest.MonkeyPatch, cpus: int
    ) -> None:
        # Oracle: never more members than CPUs, never more than the measured
        # contention knee, and a many-core host must not plan one per core.
        monkeypatch.delenv(process_resources.MEMBER_JOBS_ENV_VAR, raising=False)
        monkeypatch.setattr(process_resources.os, "cpu_count", lambda: cpus)
        monkeypatch.setattr(process_resources, "gil_enabled", lambda: False)
        got = process_resources.python_parallelism()
        assert 1 <= got <= cpus
        assert got <= process_resources.FREE_THREADED_MEMBER_PARALLELISM
        assert got == min(cpus, process_resources.FREE_THREADED_MEMBER_PARALLELISM)

    @pytest.mark.parametrize("gil", [True, False])
    @pytest.mark.parametrize("raw", ["1", "3", "100000"])
    def test_env_override_wins_and_is_clamped(
        self, monkeypatch: pytest.MonkeyPatch, gil: bool, raw: str
    ) -> None:
        monkeypatch.setenv(process_resources.MEMBER_JOBS_ENV_VAR, raw)
        monkeypatch.setattr(process_resources, "gil_enabled", lambda: gil)
        got = process_resources.python_parallelism()
        assert got == min(int(raw), process_resources.jobs_ceiling())

    @pytest.mark.parametrize("raw", ["0", "", "abc", "-3"])
    def test_non_positive_or_bad_env_takes_the_default(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv(process_resources.MEMBER_JOBS_ENV_VAR, raw)
        monkeypatch.setattr(process_resources, "gil_enabled", lambda: True)
        got = process_resources.python_parallelism()
        assert got == process_resources.GIL_MEMBER_PARALLELISM

    def test_gil_probe_matches_the_interpreter(self) -> None:
        import sys

        probe = getattr(sys, "_is_gil_enabled", None)
        expected = True if probe is None else bool(probe())
        assert process_resources.gil_enabled() is expected


# ---------------------------------------------------------------------------
# Level 1: plan_release_workers never exceeds its budgets
# ---------------------------------------------------------------------------

_CPUS = [1, 2, 8, 91, 224]
_AVAIL = [None, 0.5, 4.0, 64.0, 900.0]
_PARALLEL = [1, 2, 7, 224]


@pytest.mark.parametrize("depth", ["headers", "binary", None])
def test_plan_never_exceeds_parallelism_or_memory(
    monkeypatch: pytest.MonkeyPatch, depth: str | None
) -> None:
    """Exhaustive over a small (cpus, RAM, parallelism) grid.

    Oracle stated independently of the function: the auto plan runs no more
    members -- and holds no more threads -- than the interpreter's parallelism,
    and (when RAM is known and not deliberately overridden) its first wave at
    the per-depth budget fits in committable memory unless it is the floor of
    one. An explicit *jobs* is an instruction and is returned untouched.
    """
    from abicheck.process_resources import available_mem_gib as _real  # noqa: F401

    monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
    failures = []
    for cpus, avail, parallel in itertools.product(_CPUS, _AVAIL, _PARALLEL):
        monkeypatch.setattr("os.cpu_count", lambda c=cpus: c)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda a=avail: a)
        monkeypatch.setattr(
            process_resources, "python_parallelism", lambda p=parallel: p
        )
        plan = release_jobs.plan_release_workers(0, depth=depth, header_roots=True)
        budget = release_jobs.release_job_mem_budget_gib(depth, header_roots=True)
        case = (cpus, avail, parallel)
        if not (1 <= plan.initial_jobs <= plan.pool_size <= parallel):
            failures.append((case, "parallelism", plan))
        if avail is not None:
            committable = (
                avail * release_jobs._release_mem_utilization()
                - release_jobs._release_mem_reserve_gib()
            )
            if plan.initial_jobs > 1 and plan.initial_jobs * budget > committable:
                failures.append((case, "memory", plan))
        explicit = release_jobs.plan_release_workers(5, depth=depth, header_roots=True)
        if explicit.initial_jobs != 5:
            failures.append((case, "explicit", explicit))
    assert failures == []


@pytest.mark.parametrize("depth", ["headers", "binary", None])
def test_plan_never_exceeds_the_process_thread_budget(
    monkeypatch: pytest.MonkeyPatch, depth: str | None
) -> None:
    """ABICHECK_MAX_THREADS bounds the plan itself, auto and explicit alike.

    Oracle: an independent minimum. With a budget of *cap* the plan holds at
    most *cap* threads and runs at most *cap* members; with no budget it is
    the unbudgeted plan unchanged. A budget of 1 therefore selects the
    sequential member path (``pool_size == 1``), which is what the H5
    reference arm needs to compare the pooled dispatch against.
    """
    monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
    failures = []
    for cpus, avail, parallel, jobs, cap in itertools.product(
        _CPUS, _AVAIL, _PARALLEL, (0, 1, 5, 64), (None, 1, 2, 3, 500)
    ):
        monkeypatch.setattr("os.cpu_count", lambda c=cpus: c)
        monkeypatch.setattr(process_resources, "available_mem_gib", lambda a=avail: a)
        monkeypatch.setattr(
            process_resources, "python_parallelism", lambda p=parallel: p
        )
        monkeypatch.delenv(process_resources.MAX_THREADS_ENV_VAR, raising=False)
        free = release_jobs.plan_release_workers(jobs, depth=depth, header_roots=True)
        if cap is not None:
            monkeypatch.setenv(process_resources.MAX_THREADS_ENV_VAR, str(cap))
        plan = release_jobs.plan_release_workers(jobs, depth=depth, header_roots=True)
        bound = free.pool_size if cap is None else min(free.pool_size, cap)
        case = (cpus, avail, parallel, jobs, cap)
        if plan.pool_size != bound:
            failures.append((case, "pool", plan.pool_size, bound))
        if plan.initial_jobs != (
            free.initial_jobs if cap is None else min(free.initial_jobs, cap)
        ):
            failures.append((case, "jobs", plan.initial_jobs))
        if cap == 1 and plan.pool_size != 1:
            failures.append((case, "sequential"))
    assert failures == []


# ---------------------------------------------------------------------------
# Level 1: result independent of submission and completion order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_keyed_results_do_not_depend_on_submission_or_completion_order(
    seed: int,
) -> None:
    rng = random.Random(seed)
    keys = [f"lib{i}" for i in range(9)]
    delays = {k: rng.random() * 0.01 for k in keys}

    def work(key: str) -> str:
        time.sleep(delays[key])
        return key.upper()

    baseline = run_keyed_in_threads(
        keys, work, max_workers=1, on_error=lambda e, k: f"ERR {k}"
    )
    shuffled = keys[:]
    rng.shuffle(shuffled)
    got = run_keyed_in_threads(
        shuffled, work, max_workers=4, on_error=lambda e, k: f"ERR {k}"
    )
    assert dict(zip(shuffled, got)) == dict(zip(keys, baseline))
    assert got == [k.upper() for k in shuffled], "results follow the given key order"


# ---------------------------------------------------------------------------
# Level 2: bounded thread high-water mark for the include pass
# ---------------------------------------------------------------------------


def _probe(i: int) -> igw.DepfileProbe:
    return igw.DepfileProbe(unit_id=f"cu://{i}", cmd=["clang", f"u{i}.cpp"], cwd=None)


def test_thread_high_water_is_bounded_by_the_host_not_by_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many concurrent callers, many units each: thread count stays constant.

    The per-call pool this replaced created ``min(host, units)`` threads per
    call, so N callers x U units produced ~N*min(host, U) threads -- 3293 on
    a 28-member release. The oracle is the host-sized pool plus the callers'
    own threads, independent of how many units they bring.
    """
    monkeypatch.setattr(igw, "host_job_limit", lambda **_kw: 64)
    sample = threading.Event()
    peak = [threading.active_count()]

    def sampler() -> None:
        while not sample.is_set():
            peak[0] = max(peak[0], threading.active_count())
            time.sleep(0.001)

    def fake_run(cmd: list[str], **_kw: object):
        import subprocess

        time.sleep(0.002)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(igw.deadline, "run_bounded", fake_run)
    callers = 12
    before = threading.active_count()
    watcher = threading.Thread(target=sampler)
    watcher.start()
    results: list[list[igw.ProbeOutcome]] = []

    def caller() -> None:
        results.append(
            igw.run_probes(
                [_probe(i) for i in range(60)],
                aggregate_deadline=time.monotonic() + 60,
                per_unit_timeout_s=30,
                jobs=64,
            )
        )

    threads = [threading.Thread(target=caller) for _ in range(callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sample.set()
    watcher.join()

    assert len(results) == callers and all(len(r) == 60 for r in results)
    assert all(o.kind == "ok" for r in results for o in r)
    bound = before + callers + 1 + igw._shared_pool_size()
    assert peak[0] <= bound, (peak[0], bound)
    assert igw._PROBE_GATE.in_flight == 0


@pytest.mark.parametrize("window", [1, 2, 5])
def test_explicit_jobs_still_bounds_one_caller_on_the_shared_pool(
    monkeypatch: pytest.MonkeyPatch, window: int
) -> None:
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def fake_run(cmd: list[str], **_kw: object):
        import subprocess

        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.005)
        with lock:
            state["now"] -= 1
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(igw.deadline, "run_bounded", fake_run)
    monkeypatch.setattr(igw.process_resources, "available_mem_gib", lambda: None)
    out = igw.run_probes(
        [_probe(i) for i in range(20)],
        aggregate_deadline=time.monotonic() + 60,
        per_unit_timeout_s=30,
        jobs=window,
    )
    assert len(out) == 20
    assert state["peak"] <= max(1, window)


# ---------------------------------------------------------------------------
# Level 2: the gate has one poller, not one per waiter
# ---------------------------------------------------------------------------


def test_queued_waiters_do_not_each_poll_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With W waiters queued for T seconds, budget reads stay ~T/slice.

    Oracle: the old design re-read the budget once per waiter per slice,
    i.e. ~W*T/slice reads. Asserting well below that (and independent of W)
    is what distinguishes a single poller from W of them.
    """
    reads = [0]
    lock = threading.Lock()

    def limit() -> int:
        with lock:
            reads[0] += 1
        return 1

    gate = igw._ProbeGate(limit)
    assert gate.acquire(timeout=1)
    waiters = 40
    hold_s = 3.0
    admitted: list[bool] = []

    def waiter() -> None:
        admitted.append(gate.acquire(timeout=hold_s))

    threads = [threading.Thread(target=waiter) for _ in range(waiters)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    gate.release()

    slices = hold_s / igw._GATE_POLL_SECONDS
    assert admitted == [False] * waiters
    # Allowed: a constant per waiter (entry, poller hand-off, exit -- ~3) plus
    # one poller's slices. Per-waiter polling costs waiters * slices on top,
    # which at these sizes (240) exceeds the whole allowance (176).
    allowance = 4 * waiters + 2 * slices + 4
    assert waiters * slices > allowance, "fixture no longer separates the designs"
    assert reads[0] <= allowance, reads[0]
    assert gate.in_flight == 0


def test_a_release_hands_the_slot_to_a_blocked_waiter_promptly() -> None:
    gate = igw._ProbeGate(lambda: 1)
    assert gate.acquire(timeout=1)
    got: list[float] = []

    def waiter() -> None:
        start = time.monotonic()
        assert gate.acquire(timeout=10)
        got.append(time.monotonic() - start)
        gate.release()

    threads = [threading.Thread(target=waiter) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.release()
    for t in threads:
        t.join(timeout=10)
    assert len(got) == 5
    assert gate.in_flight == 0


# ---------------------------------------------------------------------------
# AST retention: the release-tail flush
# ---------------------------------------------------------------------------


class _Root:
    def __init__(self, name: str) -> None:
        self.payload = name


@pytest.mark.parametrize("grouped", [0, 3, 9])
@pytest.mark.parametrize("ungrouped", [0, 2, 7])
def test_release_completed_drops_every_completed_entry(
    grouped: int, ungrouped: int
) -> None:
    from abicheck.storage.header_ast_cache import AstAcquisitionScope

    scope = AstAcquisitionScope()
    roots = [_Root(f"r{i}") for i in range(grouped)]
    for root in roots:
        scope.run("b", repr(id(root)), lambda r=root: r.payload, group=root)
    for i in range(ungrouped):
        scope.run("b", f"content{i}", lambda i=i: i)
    scope.release_completed()
    stats = scope.group_stats()
    assert stats["entries"] == 0
    assert stats["retained_groups"] == 0
    assert stats["retained_raw_entries"] == 0
    # Still a cache: asking again recomputes an equal result.
    assert scope.run("b", "content0", lambda: 0) == 0


def test_release_completed_keeps_in_flight_entries_and_their_groups() -> None:
    from abicheck.storage.header_ast_cache import AstAcquisitionScope

    scope = AstAcquisitionScope()
    done_root, busy_root = _Root("done"), _Root("busy")
    scope.run("b", repr(id(done_root)), lambda: 1, group=done_root)
    started, finish = threading.Event(), threading.Event()

    def slow() -> int:
        started.set()
        finish.wait(5)
        return 2

    worker = threading.Thread(
        target=lambda: scope.run("b", repr(id(busy_root)), slow, group=busy_root)
    )
    worker.start()
    assert started.wait(5)
    scope.release_completed()
    # The in-flight entry and the object its key names both survive (the
    # id-reuse invariant); the completed group is gone.
    assert set(scope._groups) == {id(busy_root)}
    assert list(scope._entries) == [("b", repr(id(busy_root)))]
    finish.set()
    worker.join(5)


# ---------------------------------------------------------------------------
# Level 2: failure paths of the shared probe pool
# ---------------------------------------------------------------------------


def _probes(n: int) -> list[igw.DepfileProbe]:
    return [_probe(i) for i in range(n)]


@pytest.mark.parametrize("window", [1, 3, 8])
@pytest.mark.parametrize("failing", [frozenset(), frozenset({0}), frozenset({2, 5})])
def test_a_raising_unit_fails_only_its_own_slot_and_the_rest_still_run(
    window: int, failing: frozenset[int]
) -> None:
    ran: list[str] = []
    lock = threading.Lock()

    def work(unit: igw.DepfileProbe) -> str:
        with lock:
            ran.append(unit.unit_id)
        index = int(unit.unit_id.removeprefix("cu://"))
        if index in failing:
            raise RuntimeError(unit.unit_id)
        return unit.unit_id

    units = _probes(7)
    proxies = igw._submit_windowed(units, lambda u: (work, u), window=window)
    for i, proxy in enumerate(proxies):
        exc = proxy.exception(timeout=10)
        if i in failing:
            assert isinstance(exc, RuntimeError) and str(exc) == f"cu://{i}"
        else:
            assert exc is None and proxy.result() == f"cu://{i}"
    assert sorted(ran) == sorted(u.unit_id for u in units)


def test_a_pool_that_refuses_work_fails_every_unit_without_recursing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Closed:
        def submit(self, *_a: object) -> None:
            raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(igw, "_shared_pool", lambda: _Closed())
    # Far more units than the recursion limit would tolerate if each failed
    # submission recursed into the next.
    proxies = igw._submit_windowed(_probes(3000), lambda u: (str, u), window=4)
    assert all(isinstance(p.exception(timeout=5), RuntimeError) for p in proxies)


def test_a_forked_child_forgets_the_parents_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(igw, "_SHARED_POOL", sentinel)
    igw._forget_shared_pool_in_child()
    assert igw._SHARED_POOL is None


def test_bad_member_jobs_without_a_diagnostics_list_is_ignored_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(process_resources.MEMBER_JOBS_ENV_VAR, "lots")
    monkeypatch.setattr(process_resources, "gil_enabled", lambda: True)
    assert (
        process_resources.python_parallelism()
        == process_resources.GIL_MEMBER_PARALLELISM
    )
