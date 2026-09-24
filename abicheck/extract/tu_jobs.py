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

"""Worker count for the per-TU manifest-dump pool (:func:`_tu_jobs`).

Split out of :mod:`abicheck.dumper_manifest`, which runs the pool this sizes.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("abicheck.dumper_manifest")

#: Rough peak resident memory budget per concurrent per-TU worker (GiB) --
#: same default as buildsource.source_replay's L4 pool
#: (``_L4_JOB_MEM_BUDGET_GIB``), since both pools run the identical kind of
#: work (one castxml/clang invocation, one heavy AST parse, per TU). Tunable
#: via ``ABICHECK_TU_JOB_MEM_GIB``; the cap is skipped when RAM can't be read.
_TU_JOB_MEM_BUDGET_GIB = 3.0


def _tu_jobs(n_units: int) -> int:
    """Worker count for the per-TU manifest-dump pool (ADR-050 D6, G32 Phase
    E) -- ``run_tu_loop`` runs one castxml/clang invocation per translation
    unit, instead of today's single aggregate-then-parse call, and this
    function decides how many of those invocations run concurrently.

    Ports ``buildsource.source_replay._l4_jobs``'s already-proven policy
    verbatim (G32 Phase E's own "no new scheduling policy" note): auto ==
    ``min(n_units, cpu_count, 8)``, clamped by available memory; an explicit
    ``ABICHECK_TU_JOBS`` override (set ``1`` to force serial, e.g. for
    deterministic tests) is clamped the same way. Uses this pool's own
    ``ABICHECK_TU_JOBS``/``ABICHECK_TU_JOB_MEM_GIB`` env vars and log
    messages rather than the L4 pool's ``ABICHECK_L4_*`` ones -- a
    manifest dump and an L4 source replay are independent processes that
    may run concurrently (e.g. ``compare --dump-manifest`` alongside a
    separate ``--sources`` scan) and should be tunable independently.
    Shares :mod:`abicheck.process_resources`'s RAM-probing/ceiling
    primitives with the L4 pool rather than reimplementing them.
    """
    # Bound at call time, so a patch of the module's attributes applies.
    from ..process_resources import (
        available_mem_gib,
        job_mem_budget_gib,
        jobs_ceiling,
        mem_cap,
    )

    budget = job_mem_budget_gib("ABICHECK_TU_JOB_MEM_GIB", _TU_JOB_MEM_BUDGET_GIB)
    cap = mem_cap(budget)
    env = os.environ.get("ABICHECK_TU_JOBS")
    if env:
        try:
            requested = max(1, int(env))
        except ValueError:
            log.warning(
                "ABICHECK_TU_JOBS=%r is not a valid integer; falling back to 1 "
                "(serial) worker.",
                env,
            )
            return 1
        ceiling = jobs_ceiling()
        if requested > ceiling:
            log.warning(
                "ABICHECK_TU_JOBS=%d exceeds the oversubscription ceiling (%d "
                "for %d CPUs); clamping to %d",
                requested,
                ceiling,
                os.cpu_count() or 1,
                ceiling,
            )
            requested = ceiling
        if cap is not None and requested > cap:
            log.warning(
                "ABICHECK_TU_JOBS=%d may not fit in available memory (~%.1f GiB "
                "at ~%.1f GiB/worker); clamping to %d to avoid an OOM-killed "
                "manifest dump. Tune ABICHECK_TU_JOB_MEM_GIB, or split the "
                "manifest into fewer translation units.",
                requested,
                available_mem_gib() or 0.0,
                budget,
                cap,
            )
            return cap
        return requested
    auto = max(1, min(n_units, os.cpu_count() or 1, 8))
    if cap is not None and cap < auto:
        log.info(
            "Per-TU manifest-dump workers reduced %d -> %d to fit available "
            "memory (~%.1f GiB at ~%.1f GiB/worker); set ABICHECK_TU_JOBS / "
            "ABICHECK_TU_JOB_MEM_GIB to override.",
            auto,
            cap,
            available_mem_gib() or 0.0,
            budget,
        )
        return cap
    return auto
