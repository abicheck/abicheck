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
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
from typing import Any

from .. import deadline
from ..extract.progress import track
from .build_evidence import CompileUnit
from .source_abi import SourceAbiTu

_log = logging.getLogger("abicheck.buildsource.source_replay")
_L4_PROGRESS = "L4 source replay (translation units)"


def _deadline_bound_worker(
    deadline_ts: float | None,
    worker: Callable[[CompileUnit], tuple[SourceAbiTu | None, str | None]],
    unit: CompileUnit,
) -> tuple[SourceAbiTu | None, str | None]:
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
    worker: Callable[[CompileUnit], tuple[SourceAbiTu | None, str | None]],
    miss_units: list[CompileUnit],
    jobs: int,
    *,
    use_process_pool: bool = False,
) -> list[tuple[SourceAbiTu | None, str | None]]:
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
        executor_cls = ProcessPoolExecutor if use_process_pool else ThreadPoolExecutor
        executor_kwargs: dict[str, Any] = {"max_workers": jobs}
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
    worker: Callable[[CompileUnit], tuple[SourceAbiTu | None, str | None]],
    miss_units: list[CompileUnit],
) -> list[tuple[SourceAbiTu | None, str | None]]:
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
