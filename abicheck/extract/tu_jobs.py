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

Split out of :mod:`abicheck.extract.headers.manifest`, which runs the pool this sizes.
"""

from __future__ import annotations

import logging

log = logging.getLogger("abicheck.extract.headers.manifest")

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
    The sizing rule itself is :func:`abicheck.process_resources.bounded_jobs`,
    shared with the L4 and L5 call-graph pools; this function supplies only
    its own env vars, RAM budget and RAM probe.
    """
    from ..process_resources import bounded_jobs

    return bounded_jobs(
        n_units,
        jobs_env_var="ABICHECK_TU_JOBS",
        mem_budget_env_var="ABICHECK_TU_JOB_MEM_GIB",
        mem_budget_default_gib=_TU_JOB_MEM_BUDGET_GIB,
        ram_cap=_tu_mem_cap,
        log=log,
        what="manifest-dump workers",
        ram_hint="split the manifest into fewer translation units",
    )


def _tu_mem_cap() -> int | None:
    """Max per-TU workers that fit in available RAM, or ``None`` when unknown."""
    from ..process_resources import job_mem_budget_gib, mem_cap

    return mem_cap(
        job_mem_budget_gib("ABICHECK_TU_JOB_MEM_GIB", _TU_JOB_MEM_BUDGET_GIB)
    )
