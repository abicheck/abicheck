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

The release fan-out's auto ``--jobs 0`` default sized purely off
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


def release_job_mem_budget_gib() -> float:
    """Per-worker RAM budget (GiB) for the release-fan-out memory cap.

    ``ABICHECK_RELEASE_JOB_MEM_GIB`` overrides the
    :data:`_RELEASE_JOB_MEM_BUDGET_GIB` default (floored at 0.25 GiB); an
    unparsable value falls back to the default.
    """
    from ..process_resources import job_mem_budget_gib

    return job_mem_budget_gib(
        "ABICHECK_RELEASE_JOB_MEM_GIB", _RELEASE_JOB_MEM_BUDGET_GIB
    )


def release_jobs_mem_cap() -> int | None:
    """Max release-fan-out workers that fit in available RAM, or ``None``
    when RAM can't be read (host/cgroup memory probing failed, or a
    non-Linux platform) -- the memory clamp is then skipped entirely,
    matching :mod:`abicheck.process_resources`'s own documented behaviour.
    """
    from ..process_resources import available_mem_gib

    avail = available_mem_gib()
    if avail is None:
        return None
    return max(1, int(avail / release_job_mem_budget_gib()))
