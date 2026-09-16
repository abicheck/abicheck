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

"""Memory-aware worker-count sizing for the ``compare-release`` fan-out (R3,
CLI-audit).

The release fan-out's auto (``jobs=0``) default sized purely off
``os.cpu_count()``, which a very-high-core-count host (a real 224-core CI
runner measured 56.5 GB RSS) or a cpu-count-vs-memory-mismatched container
can push far past available RAM -- ``os.cpu_count()`` in a container
commonly reports the *host's* core count regardless of the container's
actual memory allocation.

Mirrors :mod:`abicheck.buildsource.source_replay`'s identical L4
worker-sizing pattern through the same shared, dependency-free
:mod:`abicheck.process_resources` probe -- one RAM-probing/pool-sizing
implementation, not two independently maintained copies. Lives under
``workflows/`` (a thin ``workflows -> extract`` wrapper) rather than being
called directly from :mod:`abicheck.cli_compare_release_pairwise`: that
module is ``frontends``-classified, and ``frontends -> extract`` is a
forbidden edge (`architecture/modules.yaml`) -- sizing a release's own
worker pool is exactly the kind of "coordinate release behavior" concern
``workflows/`` owns per the root ``AGENTS.md`` task-routing table.
"""

from __future__ import annotations

#: Rough peak resident memory per concurrent release-fan-out worker (GiB):
#: each holds up to two full ``AbiSnapshot``s (old + new) resident at once.
#: Tunable via ``ABICHECK_RELEASE_JOB_MEM_GIB``; the cap is skipped when RAM
#: can't be read. The default (1.0 GiB) is deliberately smaller than L4's
#: 3.0 GiB (``buildsource/source_replay.py``'s ``_L4_JOB_MEM_BUDGET_GIB``): a
#: release comparison's snapshots carry no clang AST, so a worker's real
#: footprint is far lighter (oneDAL's own 56.5 GB / 224 workers measurement
#: is ~0.25 GiB/worker) -- 1.0 GiB leaves headroom for a larger library
#: without being so generous the clamp never actually engages.
_RELEASE_JOB_MEM_BUDGET_GIB = 1.0

#: The same figure per evidence depth, because the 1.0 GiB default above is
#: a *binary-depth* measurement and the fan-out is not a binary-depth-only
#: operation. A worker running at header depth additionally parses that
#: member's headers (a live castxml/clang AST) and then holds two far larger
#: snapshots resident: a six-member toolkit bundle compared at
#: ``--depth headers`` peaked at 20.4 GiB, ~3.4 GiB per member, against a
#: budget that would have let the host run one worker per core. Sizing a
#: header-depth fan-out off the binary-depth figure is how a release job
#: gets OOM-killed rather than clamped, which is the one failure this cap
#: exists to prevent. ``build``/``source`` inherit L4's own, larger
#: per-worker figure (``buildsource/source_replay.py``'s
#: ``_L4_JOB_MEM_BUDGET_GIB``), since such a worker drives that very
#: machinery. An unrecognized/absent depth keeps the binary-depth default,
#: so every pre-existing invocation is sized exactly as before.
_RELEASE_JOB_MEM_BUDGET_GIB_BY_DEPTH: dict[str, float] = {
    "binary": _RELEASE_JOB_MEM_BUDGET_GIB,
    "headers": 4.0,
    "build": 6.0,
    "source": 6.0,
}


#: Fraction of probed-available RAM the fan-out is allowed to commit to
#: worker working sets, and a flat reserve subtracted on top of it.
#:
#: ``release_jobs_mem_cap`` divided *all* of ``available_mem_gib()`` by the
#: per-worker budget, which commits 100% of what the probe reported to the
#: workers and leaves nothing for the three things that are resident at the
#: same time and are not a worker's own two snapshots:
#:
#: * the parent's own retained state -- the fan-out is a
#:   :class:`~concurrent.futures.ThreadPoolExecutor`, so every member's
#:   result accumulates in the *same* address space the workers allocate in,
#:   and is held until the bundle is assembled and rendered;
#: * the shared context a header-depth run builds once (AST/template indexes,
#:   acquisition and metadata reuse) and every worker reads through;
#: * the probe's own staleness -- ``MemAvailable`` is sampled once, at pool
#:   sizing time, before any of the above exists.
#:
#: Measured on this repository's own 16 GB reference host: ``headers`` depth
#: probed 13.17 GiB available and admitted 3 workers at a 4.0 GiB budget, a
#: 12.0 GiB commitment -- 91% of available, with the parent's share still to
#: come out of the remaining 1.17 GiB. That is the shape that OOM-kills
#: rather than clamps, which is the one failure this cap exists to prevent.
#:
#: Both are tunable (``ABICHECK_RELEASE_MEM_UTILIZATION``,
#: ``ABICHECK_RELEASE_MEM_RESERVE_GIB``). The default utilization is the low
#: end of the 80-85% engineering target so the reserve is not the only thing
#: standing between a full bundle and the limit.
_RELEASE_MEM_UTILIZATION = 0.85
_RELEASE_MEM_RESERVE_GIB = 1.0


def _release_mem_utilization() -> float:
    """Committable fraction of probed-available RAM, clamped to (0, 1].

    An unparsable or out-of-range override falls back to the default rather
    than inverting the clamp's meaning -- the same rule
    :func:`abicheck.process_resources.job_mem_budget_gib` applies to its own
    override, and the same reason :func:`process_resources.mem_cap` treats a
    non-positive budget as "can't be read".
    """
    import os

    try:
        value = float(
            os.environ.get("ABICHECK_RELEASE_MEM_UTILIZATION")
            or _RELEASE_MEM_UTILIZATION
        )
    except ValueError:
        return _RELEASE_MEM_UTILIZATION
    if not 0.0 < value <= 1.0:
        return _RELEASE_MEM_UTILIZATION
    return value


def _release_mem_reserve_gib() -> float:
    """Flat GiB held back for parent-retained and shared-context state.

    Floored at 0.0 (never negative, which would *raise* the admission), and
    an unparsable override falls back to the default.
    """
    import os

    try:
        return max(
            0.0,
            float(
                os.environ.get("ABICHECK_RELEASE_MEM_RESERVE_GIB")
                or _RELEASE_MEM_RESERVE_GIB
            ),
        )
    except ValueError:
        return _RELEASE_MEM_RESERVE_GIB


def sizing_depth(depth: str | None, *, header_roots: bool) -> str | None:
    """The evidence depth a fan-out worker will actually reach.

    ``--depth`` is a *floor*, not a description of the run: it is ``None``
    whenever the caller did not type one, and the comparison pipeline then
    *infers* header evidence from the header roots the run was given. Sizing
    the worker pool off the raw option therefore reads an ordinary
    ``compare OLD_DIR NEW_DIR --header ...`` (no ``--depth``) as binary
    depth and budgets a quarter of what such a worker needs -- four to six
    times too many workers, which is exactly the overcommit the depth table
    exists to prevent (Codex review, P1).

    So an explicit rung wins, and otherwise header roots on either side mean
    ``headers``. Deliberately conservative in one direction only: it never
    reports *less* than the explicit floor, since a worker cannot reach less
    evidence than the run demands.
    """
    if depth is not None:
        return depth
    return "headers" if header_roots else None


def release_job_mem_budget_gib(
    depth: str | None = None, *, header_roots: bool = False
) -> float:
    """Per-worker RAM budget (GiB) for the release-fan-out memory cap.

    ``ABICHECK_RELEASE_JOB_MEM_GIB`` overrides the default for *depth*
    (floored at 0.25 GiB); an unparsable value falls back to that default.
    An operator's explicit override still wins at every depth -- the
    depth table only moves the *default*, which is what a caller who set
    nothing gets. See :data:`_RELEASE_JOB_MEM_BUDGET_GIB_BY_DEPTH`.
    """
    from ..process_resources import job_mem_budget_gib

    return job_mem_budget_gib(
        "ABICHECK_RELEASE_JOB_MEM_GIB",
        _RELEASE_JOB_MEM_BUDGET_GIB_BY_DEPTH.get(
            sizing_depth(depth, header_roots=header_roots) or "",
            _RELEASE_JOB_MEM_BUDGET_GIB,
        ),
    )


def release_jobs_mem_cap(
    depth: str | None = None, *, header_roots: bool = False
) -> int | None:
    """Max release-fan-out workers that fit in available RAM, or ``None``
    when RAM can't be read (host/cgroup memory probing failed, or a
    non-Linux platform) -- the memory clamp is then skipped entirely,
    matching :mod:`abicheck.process_resources`'s own documented behaviour.

    Workers are admitted against *committable* memory, not against everything
    the probe reported: a utilization fraction of available RAM, less a flat
    reserve for the parent-retained and shared-context state that is resident
    alongside them in this same process. See
    :data:`_RELEASE_MEM_UTILIZATION` for why both exist and what was measured.

    Deliberately conservative in one direction only. It can only ever admit
    *fewer* workers than the plain ``available / budget`` it replaces (the
    utilization is at most 1.0 and the reserve at least 0.0), and it still
    floors at one worker -- a host too small to hold a single member's budget
    is not made to run zero of them, it runs one and is allowed to fail on its
    own terms rather than silently comparing nothing.

    Note this does not make the admission *periodic*: the probe is still read
    once, at pool-sizing time, and the resulting count is fixed for the run.
    That is what keeps several workers from each observing the same free
    memory and simultaneously oversubscribing it -- the commitment is decided
    once, up front, rather than re-checked per worker.
    """
    from ..process_resources import available_mem_gib

    avail = available_mem_gib()
    if avail is None:
        return None
    committable = avail * _release_mem_utilization() - _release_mem_reserve_gib()
    budget = release_job_mem_budget_gib(depth, header_roots=header_roots)
    return max(1, int(committable / budget))


def resolve_release_worker_count(
    jobs: int, *, depth: str | None = None, header_roots: bool = False
) -> tuple[int, int | None, float]:
    """How many fan-out workers this run gets, and the budget that decided it.

    Returns ``(effective_jobs, clamped_from, budget_gib)`` -- *clamped_from*
    is the pre-clamp count when a clamp applied and ``None`` when none did
    (an explicitly requested *jobs*, a host whose RAM cannot be probed, or a
    cap at or above the CPU-derived default), so a caller renders its notice
    on exactly that condition and needs no count of its own.

    The whole decision lives here rather than at the CLI call site: sizing a
    release's own worker pool is "coordinate release behavior", which
    ``workflows/`` owns (AGENTS.md's task-routing table), and the caller
    then has no opportunity to size off the raw ``--depth`` -- the P1 this
    signature exists to make unrepresentable. A positive *jobs* is never
    clamped: unlike the ``ABICHECK_L4_JOBS`` override this pattern mirrors,
    it carries a real "the caller deliberately chose this" signal.
    """
    import os

    effective = jobs if jobs > 0 else (os.cpu_count() or 1)
    budget = release_job_mem_budget_gib(depth, header_roots=header_roots)
    if jobs > 0:
        return effective, None, budget
    cap = release_jobs_mem_cap(depth, header_roots=header_roots)
    if cap is None or cap >= effective:
        return effective, None, budget
    return cap, effective, budget
