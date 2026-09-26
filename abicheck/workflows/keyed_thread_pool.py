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

"""A keyed thread-pool runner that carries the caller's ``contextvars``
context into every task -- the release fan-out's parallel member loop.

Moved out of ``cli_compare_release_pairwise._compare_release_parallel`` (at
its ``no_growth`` baseline) as a responsibility of its own: nothing in it is
about comparing libraries. Its docstring records two concurrency subtleties
found by real test failures; keep them with the code.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from . import memory_trace

if TYPE_CHECKING:
    from .release_admission import MemoryAdmission

__all__ = ["run_keyed_in_threads"]

_T = TypeVar("_T")


def run_keyed_in_threads(
    keys: Sequence[str],
    fn: Callable[[str], _T],
    *,
    max_workers: int,
    on_error: Callable[[Exception, str], _T],
    admission: MemoryAdmission | None = None,
) -> list[_T]:
    """Run ``fn(key)`` for every key on a thread pool, each call admitted
    through *admission* when given (``workflows.release_admission``) and
    traced as a ``release.member`` phase; a raising call becomes
    ``on_error(exc, key)``.

    Results are collected by key and returned in *matched_keys* order so the
    report is deterministic regardless of completion timing (parallel is now the
    default via ``jobs=0``, auto-detect); CI snapshots and downstream diffs
    depend on this.

    Uses a :class:`ThreadPoolExecutor` (real OS threads sharing this
    process's memory), *not* a ``ProcessPoolExecutor`` -- a stale claim in
    an earlier revision of this docstring said otherwise (Codex review,
    fresh evidence). That distinction matters for `policy_file.
    dedup_validate_overrides_warnings()`: a `ContextVar` set in the calling
    thread is *not* automatically visible to a new thread `ThreadPoolExecutor`
    spawns -- each worker thread starts with the `ContextVar`'s default value
    -- so submitting bare per-member calls would silently escape
    the caller's dedup scope and warn once per library even under the
    default (`jobs=0`, auto-detected CPU count > 1) parallel path. Fixed by
    explicitly propagating a copy of the calling thread's
    `contextvars.Context` into each submitted call via ``Context.run``.

    Two subtleties this went through, both caught by a real (initially
    intermittent, then reliably reproducing) test failure rather than by
    inspection -- worth recording so a future edit here doesn't reintroduce
    either:

    1. ``copy_context()`` must be called in *this* (the calling) thread, at
       submission time -- not inside the function a worker thread executes.
       Calling it from within the submitted callable copies whatever context
       that already-new worker thread started with (the `ContextVar`
       default), not this thread's dedup scope, silently reproducing the
       exact bug this fix exists to close.
    2. Each submission needs its *own* fresh copy, not one `Context` object
       shared across tasks -- ``Context.run`` raises ``RuntimeError`` if the
       same `Context` object is entered from more than one thread
       concurrently.

    Every copy still shares the same mutable dedup ``set`` object the
    `ContextVar` points to (copying a context copies variable *bindings*,
    not the values they point to), so every worker's dedup check is against
    the one real, shared set regardless of which thread runs it -- guarded
    by `policy_file`'s own dedup lock against the resulting cross-thread
    race on that shared set (also caught by the same test failure).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextlib import nullcontext
    from contextvars import Context, copy_context

    def _run_in_context(ctx: Context, key: str) -> _T:
        with memory_trace.phase("release.member", key=key):
            return ctx.run(_admitted, key)

    def _admitted(key: str) -> _T:
        with admission.admit() if admission else nullcontext():
            return fn(key)

    results_by_key: dict[str, _T] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_run_in_context, copy_context(), key): key for key in keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results_by_key[key] = future.result()
            except Exception as exc:
                results_by_key[key] = on_error(exc, key)
    return [results_by_key[key] for key in keys if key in results_by_key]
