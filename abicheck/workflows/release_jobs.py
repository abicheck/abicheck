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


def release_job_mem_budget_gib(depth: str | None = None) -> float:
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
            depth or "", _RELEASE_JOB_MEM_BUDGET_GIB
        ),
    )


def release_jobs_mem_cap(depth: str | None = None) -> int | None:
    """Max release-fan-out workers that fit in available RAM, or ``None``
    when RAM can't be read (host/cgroup memory probing failed, or a
    non-Linux platform) -- the memory clamp is then skipped entirely,
    matching :mod:`abicheck.process_resources`'s own documented behaviour.
    """
    from ..process_resources import available_mem_gib

    avail = available_mem_gib()
    if avail is None:
        return None
    return max(1, int(avail / release_job_mem_budget_gib(depth)))
