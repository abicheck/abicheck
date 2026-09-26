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

Selected by the ``low-memory`` performance profile
(:mod:`abicheck.model.performance`), never by this module. Linux-only: it
relies on ``fork`` so the side's resolution closure need not be picklable,
and ``fork`` is not a safe default on macOS or available on Windows. Where
:func:`isolation_supported` is false, :func:`run_isolated` runs the
callables in-process, exactly as before.

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
import pickle
import sys
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from ..errors import SnapshotError
from ..storage.acyclic_json import gc_paused

__all__ = ["isolation_supported", "run_isolated"]

_T = TypeVar("_T")


def isolation_supported() -> bool:
    """Whether side resolution can run in forked children on this platform."""
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

    In forked children when :func:`isolation_supported`, else in-process. With
    *concurrent* the children all start before the first result is read;
    otherwise each finishes before the next starts, which bounds peak memory
    to one side at a time (the ``ABICHECK_PARALLEL_EXTRACTION=0`` contract).
    """
    if not isolation_supported():
        return [fn() for fn in fns]
    ctx = multiprocessing.get_context("fork")
    if not concurrent:
        return [_run_children(ctx, [fn])[0] for fn in fns]
    return _run_children(ctx, list(fns))


def _run_children(ctx: Any, fns: list[Callable[[], _T]]) -> list[_T]:
    started: list[tuple[Any, Any]] = []
    results: list[_T] = []
    error: BaseException | None = None
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
    try:
        for proc, recv in started:
            if error is not None:
                break
            try:
                # Unpickling builds the whole snapshot graph at once; a gen-0
                # collection every few hundred allocations would rescan it
                # repeatedly for garbage it cannot contain yet.
                with gc_paused():
                    status, value = recv.recv()
            except EOFError:
                proc.join()
                status, value = "died", None
            except OSError as exc:
                # A partially written frame: the child died mid-transfer.
                status, value = (
                    "err",
                    SnapshotError(f"side resolution result transfer failed: {exc!r}"),
                )
            if status == "ok":
                results.append(value)
            elif status == "err":
                error = value
            else:
                error = SnapshotError(
                    f"side resolution process exited with status {proc.exitcode} "
                    "without returning a snapshot"
                )
    finally:
        # On failure a sibling's result is no longer wanted, and closing our
        # receive end cannot unblock its send: every later child inherited
        # that read end at fork. Terminate what is still running, then close
        # and reap everything -- on success and on any failure.
        abandoned = error is not None or len(results) != len(started)
        if abandoned:
            for proc, _recv in started:
                if proc.is_alive():
                    proc.terminate()
        for _proc, recv in started:
            recv.close()
        for proc, _recv in started:
            _reap(proc, escalate=abandoned)
    if error is not None:
        raise error
    return results


#: How long an abandoned child gets to act on SIGTERM before SIGKILL.
_TERMINATE_GRACE_SECONDS = 5.0


def _reap(proc: Any, *, escalate: bool) -> None:
    """Join *proc*; for an abandoned child, never wait on SIGTERM alone.

    A forked child inherits the parent's signal dispositions, including the
    Python-level SIGTERM handler ``cli.main`` installs, and that handler can
    block on a lock the fork copied in its held state. SIGTERM is then a
    request the child cannot honour, and an unbounded ``join`` hangs the
    parent forever -- so an abandoned child that outlives the grace period is
    killed. A child whose result was fully received has already sent it and
    is exiting on its own, so it is joined without escalation.
    """
    if not escalate:
        proc.join()
        return
    proc.join(_TERMINATE_GRACE_SECONDS)
    if proc.is_alive():
        proc.kill()
        proc.join()
