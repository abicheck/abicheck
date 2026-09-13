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

"""Bounded execution of the per-compile-unit ``clang -M`` depfile probes.

The *scheduling* half of :mod:`abicheck.buildsource.include_graph`: worker-count
resolution, the process-wide child-process gate, deadline propagation into pool
workers, and the per-probe timeout/aggregate-budget accounting. Split from that
module so "what a depfile means" (argv sanitization, depfile parsing, graph
folding, diagnostics) and "how many compilers may run at once, bounded by what"
have one owner each.

Everything here is deliberately state-free apart from the one process-wide
semaphore: each probe is planned up front (:class:`DepfileProbe`) and reports
its result as data (:class:`ProbeOutcome`), so the caller's own ordered fold --
not completion order -- decides what the pass produced.
"""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - depfile probes shell out to clang (never shell=True)
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .. import deadline, process_resources

__all__ = [
    "DepfileProbe",
    "ProbeOutcome",
    "host_job_limit",
    "resolve_jobs",
    "run_probes",
]


#: Worker-count override for the per-unit ``clang -M`` pass. ``0``/``1``
#: disables parallelism entirely (the documented escape hatch, mirroring
#: ``ABICHECK_PARALLEL_EXTRACTION=0``); unset takes the CPU/RAM-derived
#: default.
_JOBS_ENV_VAR = "ABICHECK_INCLUDE_MAP_JOBS"

#: Per-worker RAM budget for that pool's memory clamp. A ``clang -M`` run is
#: preprocess-only -- it builds no AST and writes only a depfile -- so it is
#: an order of magnitude lighter than the L2/L4 full-parse workers the 2 GiB
#: default elsewhere is sized for. Measured: 32 concurrent-ish header probes
#: peaked around 210 MiB of *total* process-tree RSS at 4 workers.
_JOB_MEM_ENV_VAR = "ABICHECK_INCLUDE_MAP_JOB_MEM_GIB"
_JOB_MEM_DEFAULT_GIB = 0.5

#: Below this many planned units the pass stays single-threaded. Pool setup
#: buys little at that size, and keeping the small case sequential keeps the
#: ``clang -M`` invocation *order* observable for callers that assert on it.
_PARALLEL_MIN_UNITS = 3


class _ProbeGate:
    """Admission control on concurrently spawned ``clang -M`` children.

    One of these exists per process (:data:`_PROBE_GATE`), shared by every
    :func:`run_probes` pool -- see that function for why a per-pool bound is
    not enough.

    Deliberately **not** a ``BoundedSemaphore`` sized once from the host
    budget, which is what three separate review rounds on this gate each
    found a different hole in:

    * A semaphore keyed on a *pool's* own worker count gets rebuilt whenever
      that count changes, and ``resolve_jobs`` clamps to
      ``min(host_limit, unit_count)`` -- so two concurrent sides with
      differing header counts each ended up holding their own semaphore and
      admitting a full quota apiece.
    * A semaphore sized once and kept cannot notice the budget it was sized
      from *shrinking*. Each pool re-reads the budget and correctly narrows
      itself, while the stale, wider gate keeps admitting the old number --
      overcommitting exactly the constrained host the memory clamp exists
      for. Resizing it instead requires knowing when every previous holder
      has drained, which is a lock-ordering problem in its own right.

    A counter plus a condition variable has neither hole, because the limit
    is not baked into an object at all: it is **re-read on every admission
    attempt**, so a shrunk budget takes effect at the next probe and a grown
    one at the next wakeup. Holders already running are never interrupted --
    they cannot be -- so a budget that drops below the current in-flight count
    simply admits nobody new until it recovers, which is the safe direction.

    The per-attempt cost is :func:`host_job_limit`'s few small
    ``/proc``/cgroup reads, against a probe that spawns a compiler: not worth
    caching, and caching is what created the staleness above.
    """

    def __init__(self, limit: Callable[[], int]) -> None:
        self._limit = limit
        self._cond = threading.Condition()
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        """Children currently admitted (for tests and diagnostics)."""
        with self._cond:
            return self._in_flight

    def acquire(self, timeout: float) -> bool:
        """Admit one child within *timeout* seconds, or return ``False``.

        Waits in short slices rather than one long one so a budget that *grows*
        while this caller is queued is noticed promptly, not only when a
        current holder happens to release.
        """
        ends_at = time.monotonic() + timeout
        with self._cond:
            while True:
                if self._in_flight < self._limit():
                    self._in_flight += 1
                    return True
                left = ends_at - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(timeout=min(left, _GATE_POLL_SECONDS))

    def release(self) -> None:
        with self._cond:
            self._in_flight -= 1
            self._cond.notify()


#: Longest a queued probe waits before re-reading the host budget, so a budget
#: that *grows* is picked up without waiting for a holder to release.
_GATE_POLL_SECONDS = 0.5

#: The one process-wide gate. A plain module-level instance, not lazily built:
#: it holds no copy of the budget to go stale, so there is nothing to build
#: late or replace (see :class:`_ProbeGate`).
_PROBE_GATE = _ProbeGate(lambda: host_job_limit())


@dataclass(frozen=True)
class DepfileProbe:
    """One compile unit's fully-resolved ``clang -M`` invocation.

    Planned up front (argv sanitization, ``~`` un-redaction, cwd) so the
    worker holds no reference to the ``CompileUnit`` and shares nothing
    mutable with the fold that consumes its outcome.
    """

    unit_id: str
    cmd: list[str]
    cwd: str | None


@dataclass(frozen=True)
class ProbeOutcome:
    """What one planned unit's run produced, as data rather than an effect.

    ``kind`` is one of ``ok`` (``stdout``/``stderr``/``returncode`` carry the
    completed process), ``unit_timeout``/``scan_deadline``/``error`` (``detail``
    carries the message), or ``aggregate_expired`` (this unit never ran because
    the extractor's own wall-clock budget was already gone).
    """

    kind: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    detail: str = ""


def host_job_limit(
    *, jobs: int | None = None, diagnostics: list[str] | None = None
) -> int:
    """Concurrent ``clang -M`` children this host should tolerate (>= 1).

    Deliberately independent of how many units any one caller has: this is the
    *host* budget, which is what makes it a safe size for the one shared gate
    in :class:`_ProbeGate` (a limit derived from one caller's unit count would
    differ between two concurrent callers -- see that class's own docstring for
    what that cost).

    *jobs* (the caller's explicit request) wins when set; otherwise
    ``ABICHECK_INCLUDE_MAP_JOBS`` (``0`` or unset takes the default), otherwise
    the shared CPU/RAM-derived sizing every other abicheck worker pool uses
    (:mod:`abicheck.process_resources`). An explicit request is still clamped
    to the oversubscription ceiling and the memory cap, same as every other
    pool.
    """
    if jobs is not None:
        requested = jobs
    else:
        raw = os.environ.get(_JOBS_ENV_VAR, "").strip()
        if raw:
            try:
                requested = int(raw)
            except ValueError:
                requested = 0
                if diagnostics is not None:
                    diagnostics.append(
                        f"ignoring unparsable {_JOBS_ENV_VAR}={raw!r}; "
                        "using the default worker count"
                    )
        else:
            requested = 0
    if requested <= 0:
        requested = process_resources.jobs_ceiling(floor=2, cpu_multiplier=1)
    else:
        # An explicit override is still clamped, same as every other pool.
        requested = min(requested, process_resources.jobs_ceiling())
    cap = process_resources.mem_cap(
        process_resources.job_mem_budget_gib(_JOB_MEM_ENV_VAR, _JOB_MEM_DEFAULT_GIB)
    )
    if cap is not None:
        requested = min(requested, cap)
    return max(1, requested)


def resolve_jobs(
    unit_count: int, *, jobs: int | None = None, diagnostics: list[str] | None = None
) -> int:
    """Worker count for a pool of *unit_count* planned units (>= 1).

    :func:`host_job_limit`, narrowed to one worker per unit -- never more
    threads than there is work for. Deliberately sequential below
    :data:`_PARALLEL_MIN_UNITS`: a two-unit build gains little, while a
    single-threaded path keeps the call *order* observable, which several
    callers' own tests (and any future debugging session) rely on.

    Note this is a *pool* size, not a concurrency guarantee: total concurrent
    children across every pool in the process is bounded by
    :data:`_PROBE_GATE`, which admits against the host budget alone.
    """
    if unit_count < _PARALLEL_MIN_UNITS:
        return 1
    return max(1, min(host_job_limit(jobs=jobs, diagnostics=diagnostics), unit_count))


def run_probes(
    planned: list[DepfileProbe],
    *,
    aggregate_deadline: float,
    per_unit_timeout_s: float,
    jobs: int | None = None,
    diagnostics: list[str] | None = None,
) -> list[ProbeOutcome]:
    """Run every planned unit, returning one outcome per unit in order.

    *aggregate_deadline* is an absolute ``time.monotonic()`` instant supplied
    by the caller, **not** a duration started here: the budget has to cover the
    caller's own planning work too (response-file expansion and argv
    sanitization over many units is not free), which starting the clock at this
    boundary would silently exclude. Each unit re-reads it as it starts -- so a unit that is still
    queued when the budget runs out reports ``aggregate_expired`` without
    spawning anything, which is the sequential loop's "stop, don't start
    this one" decision expressed per unit instead of per iteration.

    With ``jobs > 1`` a few units past that point can already be in
    flight; their results are simply discarded by the ordered fold, which
    is the price of not serializing the whole pass to keep the cut-off
    exact. Nothing *runs* that the sequential pass would not have started
    given the same elapsed time.
    """
    resolved_jobs = resolve_jobs(len(planned), jobs=jobs, diagnostics=diagnostics)
    slots = _PROBE_GATE
    if resolved_jobs <= 1:
        # Still gated. A serial pool is one child rather than none, and
        # `service.compare` runs the two sides concurrently: a side whose unit
        # count (or explicit `jobs=1`) makes it serial would otherwise add an
        # *ungated* child alongside the other side's full quota --
        # ``host_job_limit + 1``, or two children where the memory-derived cap
        # is one, which is the clamp defeated exactly on the host that needed
        # it (Codex review, PR #1275). Uncontended, the acquire is free.
        return [
            _run_probe(unit, aggregate_deadline, per_unit_timeout_s, slots)
            for unit in planned
        ]
    # One process-wide gate, not one per pool: `service.compare` resolves
    # the old and new sides concurrently (ABICHECK_PARALLEL_EXTRACTION),
    # and each side builds its own pool -- two pools each sized for the
    # whole host would jointly oversubscribe it, the same hazard
    # `service_compare_pipeline.resolve_sides_sequentially` documents for
    # manifest dumps. The semaphore bounds *concurrently spawned clang
    # processes* across the request; the pools above it can stay
    # independent. Admits against the live host budget, never a per-pool size
    # or a cached one -- see `_ProbeGate` for the holes both of those had.
    deadline_ts = deadline.current_deadline_ts()
    with ThreadPoolExecutor(max_workers=resolved_jobs) as pool:
        futures = [
            pool.submit(
                _run_probe_in_worker,
                deadline_ts,
                unit,
                aggregate_deadline,
                per_unit_timeout_s,
                slots,
            )
            for unit in planned
        ]
        return [f.result() for f in futures]


def _run_probe_in_worker(
    deadline_ts: float | None,
    unit: DepfileProbe,
    aggregate_deadline: float,
    per_unit_timeout_s: float,
    slots: _ProbeGate,
) -> ProbeOutcome:
    """Pool-worker entry: re-establish the scan deadline, then run.

    ``contextvars`` do not cross a ``ThreadPoolExecutor`` boundary, so
    without ``with_deadline_ts`` a worker would see no active deadline and
    ``run_bounded`` would silently fall back to the fixed local timeout
    regardless of ``--budget`` -- the same wiring ``source_replay``'s and
    ``call_graph``'s own ``_deadline_bound_worker`` helpers exist for.
    """
    with deadline.with_deadline_ts(deadline_ts):
        return _run_probe(unit, aggregate_deadline, per_unit_timeout_s, slots)


def _acquire_slot(slots: _ProbeGate, aggregate_deadline: float) -> ProbeOutcome | None:
    """Take a slot in the process-wide gate, or say why this probe gives up.

    ``None`` means the slot is held and the caller must release it. Otherwise
    the returned outcome is the one the ordered fold would have recorded had
    the probe never started, which is the honest reading: it never did.

    The wait is capped at whichever of the two live budgets expires first --
    this extractor's own aggregate wall clock and the request's ``--budget``
    scan deadline -- rather than being unbounded. Unbounded is not merely
    slow: in a process serving concurrent requests, one request's long probe
    would hold a slot while another request's short-budget probe blocks
    behind it, so the second request overruns its own budget waiting and only
    reports it afterwards. Which budget ran out decides the outcome, because
    the two mean different things to the fold: an exhausted aggregate stops
    this extractor, an exhausted scan deadline stops the whole walk.
    """
    wait = aggregate_deadline - time.monotonic()
    scan_remaining = deadline.remaining()
    if scan_remaining is not None:
        wait = min(wait, scan_remaining)
    if wait <= 0 or not slots.acquire(timeout=wait):
        if time.monotonic() >= aggregate_deadline:
            return ProbeOutcome("aggregate_expired")
        left = deadline.remaining()
        if left is not None and left <= 0:
            return ProbeOutcome(
                "scan_deadline",
                detail="budget exhausted while waiting for a clang -M slot",
            )
        # Neither budget is out, so the wait was cut short by nothing this
        # function can name (a spurious timeout). Treated as the aggregate
        # budget rather than guessed at: it stops this extractor and leaves
        # the rest of the walk -- and every other extractor -- alone.
        return ProbeOutcome("aggregate_expired")
    return None


def _run_probe(
    unit: DepfileProbe,
    aggregate_deadline: float,
    per_unit_timeout_s: float,
    slots: _ProbeGate,
) -> ProbeOutcome:
    """Run one planned ``clang -M``, reporting the outcome as data.

    Records nothing anywhere -- no shared state at all -- so it is safe to
    call from several threads at once: the caller's ordered fold owns every
    mutation, which is also what keeps the diagnostics order deterministic.

    Waiting for the process-wide gate is itself bounded by the same two
    budgets that bound the probe (see :func:`_acquire_slot`): a bare
    ``acquire()`` would let a short-``--budget`` request block on an unrelated
    concurrent request's long probe and only *then* report that its own budget
    was gone (Codex review, PR #1275).
    """
    if time.monotonic() >= aggregate_deadline:
        return ProbeOutcome("aggregate_expired")
    denied = _acquire_slot(slots, aggregate_deadline)
    if denied is not None:
        return denied
    try:
        # Re-read after any queueing: the per-call timeout must reflect
        # what is left of the aggregate budget *now*, not at submit time.
        remaining = aggregate_deadline - time.monotonic()
        if remaining <= 0:
            return ProbeOutcome("aggregate_expired")
        per_call_timeout = min(per_unit_timeout_s, remaining)
        scan_remaining = deadline.remaining()
        # Whether the OUTER scan --budget (not this extractor's own
        # per-unit/aggregate cap) is what will actually bind the nested
        # scope below — decides how a DeadlineExceeded from it is
        # classified (Codex review, PR #591, round 3).
        bound_by_scan_deadline = (
            scan_remaining is not None and scan_remaining < per_call_timeout
        )
        if scan_remaining is not None:
            # run_bounded() honors an active outer deadline verbatim (not
            # min(timeout, left) — a generous --budget must not get
            # silently re-capped), so a bare `timeout=` here would let a
            # hung call eat the *whole* remaining scan budget instead of
            # this extractor's own per-unit/aggregate ceiling. Nest a
            # narrower scope so this call is bound by whichever is
            # tighter (Codex review, PR #591).
            per_call_timeout = min(per_call_timeout, scan_remaining)
        try:
            # Process-group-safe on timeout, same as the L2/L4/L5 clang calls.
            with deadline.deadline_scope(per_call_timeout):
                proc = deadline.run_bounded(  # noqa: S603 - fixed argv, never shell=True
                    unit.cmd,
                    cwd=unit.cwd,
                    capture_output=True,
                    text=True,
                    timeout=per_call_timeout,
                )
        except deadline.DeadlineExceeded as exc:
            if not bound_by_scan_deadline:
                # The entry-time snapshot said this extractor's OWN
                # per-unit/aggregate cap was binding, not the outer scan
                # deadline — but run_bounded's own escalation (SIGTERM
                # -> grace -> SIGKILL, plus a fixed 5s pipe-drain) can
                # push real elapsed time past that snapshot, so the
                # outer deadline can still be exhausted by now even
                # though it wasn't at entry. Re-check it directly
                # instead of trusting the stale snapshot alone (Codex
                # review, PR #591, round 3).
                try:
                    deadline.check()
                except deadline.DeadlineExceeded:
                    pass
                else:
                    return ProbeOutcome("unit_timeout", detail=str(exc))
            return ProbeOutcome("scan_deadline", detail=str(exc))
        except (OSError, subprocess.SubprocessError) as exc:
            return ProbeOutcome("error", detail=str(exc))
    finally:
        slots.release()
    # ``getattr`` rather than direct attribute access on ``stderr``/
    # ``returncode``: the sequential loop only read those two when
    # ``stdout`` came back blank, so a stand-in result object that carries
    # just ``stdout`` (several callers' test doubles for
    # ``deadline.run_bounded``) stayed valid. A real
    # ``subprocess.CompletedProcess`` always carries all three, so this
    # only preserves that tolerance -- it does not mask a failing process,
    # whose ``returncode`` is present by construction.
    return ProbeOutcome(
        "ok",
        stdout=getattr(proc, "stdout", "") or "",
        stderr=getattr(proc, "stderr", "") or "",
        returncode=getattr(proc, "returncode", 0) or 0,
    )
