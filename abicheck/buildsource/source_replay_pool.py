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

"""L4 source replay's pool execution: extract the cache-miss units, in
parallel or serially, reporting ``i/N`` progress as units complete.

Split out of :mod:`abicheck.buildsource.source_replay` (which keeps the
cache, planning and assembly around this step).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Any, TypeVar

from .. import deadline, process_resources
from ..extract.progress import track
from ..process_resources import BudgetedExecutor

_log = logging.getLogger("abicheck.buildsource.source_replay")
_L4_PROGRESS = "L4 source replay (translation units)"

#: One pool item: a compile unit, or a shared-dump work item carrying one
#: (``source_replay._run_shared_dumps``). The result is the worker's own type.
_Item = TypeVar("_Item")
_Result = TypeVar("_Result")


#: Hard ceiling on the L4 worker count. Each worker drives a heavyweight clang
#: process (one TU, single-threaded); past ~2× the CPU count the processes only
#: contend for cores and the L3/L5/serialization serial fraction dominates anyway
#: (skills-src/evaluation/field/SCALING.md saw jobs=8 on 4 CPUs *regress*). The explicit
#: ``ABICHECK_L4_JOBS`` override is clamped to this so a stray ``=64`` can't
#: thrash the host — a warning is logged when it is.
#:
#: Delegates to :mod:`abicheck.process_resources` (G32 Phase E) — the same
#: leaf module :mod:`abicheck.extract.headers.manifest`'s per-TU pool sizes itself
#: from, so the two pools share one RAM-probing/ceiling implementation
#: rather than two independently-maintained copies.
def _l4_jobs_ceiling() -> int:
    return process_resources.jobs_ceiling()


#: Rough peak resident memory budget per concurrent L4 worker (GiB). A heavily
#: templated C++ TU's ``clang -ast-dump=json`` output — and its in-Python parse —
#: can reach several GiB, so the *default* worker count is sized to available RAM
#: as well as CPU count: a low-memory host oversubscribing giant ASTs into one
#: process is how the UXL oneTBB/oneDNN ``s5``/``s6`` replay got OOM-killed (the
#: kernel SIGKILLs the whole replay → ``exit -9``, all L4 work lost). Tunable via
#: ``ABICHECK_L4_JOB_MEM_GIB``; the cap is skipped when RAM can't be read.
_L4_JOB_MEM_BUDGET_GIB = 3.0

# G32 Phase E: the RAM-probing internals (meminfo/cgroup parsing) live in
# abicheck.process_resources now — see this module's own "RAM-aware worker
# sizing" note above the ThreadPoolExecutor split. This module keeps thin
# ABICHECK_L4_*-named wrappers (unchanged log messages) rather than calling
# process_resources directly everywhere, so ABICHECK_L4_JOBS/
# ABICHECK_L4_JOB_MEM_GIB callers and their own dedicated policy tests
# (test_l4_perf.py) are unaffected; the low-level probe tests themselves
# moved to test_process_resources.py, which targets process_resources
# directly (a refactor test, not new coverage).


def _l4_available_mem_gib() -> float | None:
    """Best-effort available RAM in GiB, honouring cgroup limits in containers.

    Returns the *smaller* of host ``MemAvailable`` and the cgroup memory headroom
    so a process confined to a small cgroup on a large host still sizes its L4
    worker count to what it is actually allowed to use. ``None`` when neither
    source is readable (non-Linux / sandbox), which skips the memory clamp.
    """
    return process_resources.available_mem_gib()


def _l4_job_mem_budget_gib() -> float:
    """Per-worker RAM budget (GiB) for the L4 memory cap.

    ``ABICHECK_L4_JOB_MEM_GIB`` overrides the :data:`_L4_JOB_MEM_BUDGET_GIB`
    default (floored at 0.25 GiB); an unparsable value falls back to the default.
    """
    return process_resources.job_mem_budget_gib(
        "ABICHECK_L4_JOB_MEM_GIB", _L4_JOB_MEM_BUDGET_GIB
    )


def _l4_mem_cap() -> int | None:
    """Max L4 workers that fit in available RAM, or ``None`` when RAM can't be read."""
    avail = _l4_available_mem_gib()
    if avail is None:
        return None
    return max(1, int(avail / _l4_job_mem_budget_gib()))


def _l4_jobs(n_units: int) -> int:
    """Worker count for parallel L4 extraction (P06).

    ``ABICHECK_L4_JOBS`` overrides (set ``1`` to force serial — used by tests to
    prove determinism). Otherwise auto: one worker per cache-miss TU, capped at
    the CPU count and 8 (clang is heavy; more workers mostly add contention).

    Both the auto default and an explicit override are additionally capped by
    *available memory* (``_l4_mem_cap``): a single template-heavy TU's clang JSON
    AST can be multiple GiB, so N concurrent workers in one process can exhaust a
    low-memory host and get the whole replay OOM-killed. The memory clamp prevents
    that (set ``ABICHECK_L4_JOB_MEM_GIB`` to tune, or seed/scope the scan to fewer
    TUs); like the oversubscription ceiling, a clamp is logged, never silent.
    """
    mem_cap = _l4_mem_cap()
    env = os.environ.get("ABICHECK_L4_JOBS")
    if env:
        try:
            requested = max(1, int(env))
        except ValueError:
            return 1
        ceiling = _l4_jobs_ceiling()
        if requested > ceiling:
            _log.warning(
                "ABICHECK_L4_JOBS=%d exceeds the oversubscription ceiling (%d "
                "for %d CPUs); clamping to %d",
                requested,
                ceiling,
                os.cpu_count() or 1,
                ceiling,
            )
            requested = ceiling
        if mem_cap is not None and requested > mem_cap:
            _log.warning(
                "ABICHECK_L4_JOBS=%d may not fit in available memory (~%.1f GiB at "
                "~%.1f GiB/worker); clamping to %d to avoid an OOM-killed L4 replay. "
                "Tune ABICHECK_L4_JOB_MEM_GIB, or seed/scope the scan to fewer TUs.",
                requested,
                _l4_available_mem_gib() or 0.0,
                _l4_job_mem_budget_gib(),
                mem_cap,
            )
            return mem_cap
        return requested
    auto = max(1, min(n_units, os.cpu_count() or 1, 8))
    if mem_cap is not None and mem_cap < auto:
        _log.info(
            "L4 workers reduced %d -> %d to fit available memory (~%.1f GiB at "
            "~%.1f GiB/worker); set ABICHECK_L4_JOBS / ABICHECK_L4_JOB_MEM_GIB to override.",
            auto,
            mem_cap,
            _l4_available_mem_gib() or 0.0,
            _l4_job_mem_budget_gib(),
        )
        return mem_cap
    return auto


def _l4_use_process_pool() -> bool:
    """Whether to run the L4 extract phase in a process pool rather than threads.

    After clang returns, the extractor parses clang's (large) JSON AST dump and
    builds structural fingerprints — pure-Python, **GIL-bound** work. A thread
    pool therefore parallelizes only the clang subprocess wait, not that
    post-processing, so the AST work serializes on the GIL (part of the Amdahl
    serial fraction in skills-src/evaluation/field/SCALING.md). A *process* pool parallelizes both, at
    the cost of pickling each ``SourceAbiTu`` back and per-process spawn.

    Opt-in via ``ABICHECK_L4_EXECUTOR=process`` (default ``thread``) so the
    measured win can be validated (``skills-src/evaluation/field/scaling.py``) before it becomes the
    default; an unrecognized value falls back to threads.
    """
    return os.environ.get("ABICHECK_L4_EXECUTOR", "thread").strip().lower() == "process"


def _deadline_bound_worker(
    deadline_ts: float | None,
    worker: Callable[[_Item], _Result],
    unit: _Item,
) -> _Result:
    """Re-establish a captured scan deadline inside a pool worker.

    ``contextvars`` don't cross a ``ThreadPoolExecutor``/``ProcessPoolExecutor``
    boundary, so without this a worker would see no active deadline and each
    extractor's ``deadline.run_bounded`` call would silently fall back to its
    fixed default timeout regardless of ``--budget`` (Codex review, PR #591).
    Module-level so it stays picklable for the process pool.
    """
    with deadline.with_deadline_ts(deadline_ts):
        return worker(unit)


def _extract_cache_misses(
    worker: Callable[[_Item], _Result],
    miss_units: list[_Item],
    jobs: int,
    *,
    use_process_pool: bool = False,
) -> list[_Result]:
    """Phase 2 (parallel): extract the cache misses, in parallel when ``jobs > 1``.

    The per-TU work is stateless, so the worker is a module-level function
    (picklable for the process pool) fed the actual unit rather than an index
    into a closed-over list.
    """
    if jobs > 1 and len(miss_units) > 1:
        pool_worker = partial(
            _deadline_bound_worker, deadline.current_deadline_ts(), worker
        )
        # Process pool parallelizes the GIL-bound AST post-processing too, not
        # just the clang subprocess wait (opt-in; see _l4_use_process_pool).
        executor_cls = ProcessPoolExecutor if use_process_pool else BudgetedExecutor
        executor_kwargs: dict[str, Any] = (
            {"max_workers": jobs}
            if executor_cls is ProcessPoolExecutor
            else {"requested": jobs}
        )
        if executor_cls is ProcessPoolExecutor:
            # A process-pool worker is a genuinely separate OS process, so it
            # never inherits the SIGTERM handler cli.main installed in the
            # main process (fork-inherited handlers are also not guaranteed —
            # ABICHECK_L4_EXECUTOR's default start method can be "spawn"). An
            # external SIGTERM landing on this worker mid-run_bounded() would
            # otherwise kill it with Python's default disposition, leaving its
            # detached clang/castxml process group untracked and orphaned
            # (Codex review, PR #591, round 3).
            executor_kwargs["initializer"] = deadline.install_sigterm_cleanup
        try:
            with executor_cls(**executor_kwargs) as pool:
                done = pool.map(pool_worker, miss_units)
                return list(track(done, _L4_PROGRESS, len(miss_units)))
        except Exception as exc:  # noqa: BLE001
            # A process pool can fail to start (spawn import error, sandbox with
            # no /dev/shm, …) where threads would not. Degrade to a serial pass
            # rather than aborting L4 — the artifact tiers stay authoritative.
            if executor_cls is ProcessPoolExecutor:
                _log.warning(
                    "L4 process pool failed (%s); falling back to serial extraction",
                    exc,
                )
                return _extract_serially(worker, miss_units)
            raise
    return _extract_serially(worker, miss_units)


def _extract_serially(
    worker: Callable[[_Item], _Result],
    miss_units: list[_Item],
) -> list[_Result]:
    """Serial (``jobs<=1``, single-unit, or process-pool-failure fallback)
    extraction loop.

    Checks the shrinking scan-wide deadline *before* each unit rather than
    relying solely on each unit's own extractor-level check (PR #641
    follow-up: real-world evidence from a large multi-TU library showed
    ``--budget`` still dispatching further TUs after the deadline had
    already passed, wasting the interpreter/extractor-startup overhead for
    each one before it self-aborted). The parallel ``pool.map`` path above
    still relies on each dispatched unit's own fast self-abort (see
    ``_deadline_bound_worker``/``bounded_timeout``) — a full stop-enqueuing
    check there would need a bespoke, non-``pool.map`` dispatch loop, which
    is a larger change than this fix; the serial path is the one that can
    trivially check between iterations.
    """
    results = []
    for unit in track(miss_units, _L4_PROGRESS, len(miss_units)):
        deadline.check()
        results.append(worker(unit))
    return results
