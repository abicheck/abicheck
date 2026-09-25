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

"""Resolve each side of a comparison in its own forked child process.

Why a process and not a thread: a header-AST dump allocates its parse tree
interleaved with the snapshot objects it builds, so when the tree is freed
the allocator's arenas stay pinned by the few snapshot objects inside each.
Measured on a 45-root oneDAL-style scan, 87% of the post-dump residency was
empty pymalloc pools (366 MiB live of 2.66 GiB resident), and neither
``gc.collect()``, ``malloc_trim`` nor ``PYTHONMALLOC=malloc`` returned it. A
child process that exits returns everything; only the finished snapshot
crosses back, pickled. Dumping each side in its own process measured a
-49.8% peak at unchanged wall time.

Opt-in (``ABICHECK_EXTRACTION_ISOLATION=process``) and Linux-only: it relies
on ``fork`` so the side's resolution closure need not be picklable, and
``fork`` is not a safe default on macOS or available on Windows. Anywhere it
is not enabled or not available, :func:`run_isolated` runs the callables
in-process, exactly as before.

Children are forked from the calling thread before any result is read, so
two sides still resolve concurrently when the caller asked for that; the
parent receives each result, then reaps the child. A child that raises
sends its exception back (re-raised in the parent); one that dies without
answering -- killed by the OOM killer, say -- surfaces as a
:class:`~abicheck.errors.SnapshotError` naming its exit status.
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import pickle
import sys
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from ..errors import SnapshotError
from ..storage.acyclic_json import gc_paused

__all__ = ["isolation_enabled", "run_isolated"]

_T = TypeVar("_T")

_ENV = "ABICHECK_EXTRACTION_ISOLATION"


def isolation_enabled() -> bool:
    """Whether side resolution should run in forked children here."""
    if os.environ.get(_ENV, "").strip().lower() != "process":
        return False
    return sys.platform.startswith("linux")


def _child(conn: Any, fn: Callable[[], Any]) -> None:
    try:
        payload: tuple[str, Any] = ("ok", fn())
    except BaseException as exc:  # noqa: BLE001 -- forwarded to the parent
        payload = ("err", exc)
    try:
        conn.send(payload)
    except (pickle.PicklingError, TypeError, AttributeError) as exc:
        # The result or the exception itself does not pickle: say so rather
        # than dying silently.
        conn.send(
            (
                "err",
                SnapshotError(f"side resolution result could not be returned: {exc!r}"),
            )
        )
    finally:
        conn.close()


def run_isolated(fns: Sequence[Callable[[], _T]], *, concurrent: bool) -> list[_T]:
    """Run each of *fns* and return their results in order.

    In forked children when :func:`isolation_enabled`, else in-process. With
    *concurrent* the children all start before the first result is read;
    otherwise each finishes before the next starts, which bounds peak memory
    to one side at a time (the ``ABICHECK_PARALLEL_EXTRACTION=0`` contract).
    """
    if not isolation_enabled():
        return [fn() for fn in fns]
    ctx = multiprocessing.get_context("fork")
    if not concurrent:
        return [_run_children(ctx, [fn])[0] for fn in fns]
    return _run_children(ctx, list(fns))


def _run_children(ctx: Any, fns: list[Callable[[], _T]]) -> list[_T]:
    started = []
    # Move everything the parent holds into the permanent generation before
    # forking, so a child's collections never traverse (and copy-on-write
    # fault in) the inherited heap -- the fork-safety step the gc docs name.
    gc.freeze()
    try:
        for fn in fns:
            recv, send = ctx.Pipe(duplex=False)
            proc = ctx.Process(target=_child, args=(send, fn), daemon=True)
            proc.start()
            send.close()
            started.append((proc, recv))
    finally:
        gc.unfreeze()
    results: list[_T] = []
    error: BaseException | None = None
    for proc, recv in started:
        try:
            # Unpickling builds the whole snapshot graph at once; a gen-0
            # collection every few hundred allocations would rescan it
            # repeatedly for garbage it cannot contain yet.
            with gc_paused():
                status, value = recv.recv()
        except EOFError:
            status, value = "died", None
        finally:
            recv.close()
        proc.join()
        if error is not None:
            continue
        if status == "ok":
            results.append(value)
        elif status == "err":
            error = value
        else:
            error = SnapshotError(
                f"side resolution process exited with status {proc.exitcode} "
                "without returning a snapshot"
            )
    if error is not None:
        raise error
    return results
