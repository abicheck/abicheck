# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A keyed fan-out over forked child processes -- the release member loop's
process path, the counterpart of :mod:`~abicheck.workflows.keyed_thread_pool`.

Why processes: a member comparison is ~99.8% pure-Python CPU, so under the
GIL a thread pool does not scale -- measured on a 56-member release, ``-j2``
over threads *inflated* CPU seconds 1.93x for no wall-time gain, while three
forked processes ran three members 2.88x faster than one process ran them
serially, at unchanged total CPU.

The fork machinery is :mod:`~abicheck.workflows.side_isolation`'s
(:func:`~abicheck.workflows.side_isolation.fork_child`,
:func:`~abicheck.workflows.side_isolation.reap_child`): ``fork`` so neither the
callable nor its closure need pickle; only each result crosses back,
pickled. The caller decides whether forking is safe
(:func:`~abicheck.workflows.side_isolation.fork_is_safe`) -- this runner only
forks from the calling thread, which must be the process's only thread.

Contract shared with ``run_keyed_in_threads``: results in *keys* order
whatever the completion order; a raising call becomes ``on_error(exc, key)``;
a child that dies without answering (the OOM killer, a signal) becomes
``on_error`` with a :class:`~abicheck.errors.SnapshotError` naming its exit
status -- a recorded failure, never a crash of the run. *admission* gates
each fork through its non-blocking
:meth:`~abicheck.workflows.release_admission.MemoryAdmission.try_reserve` and
learns from the header-AST bytes the child's dumps reported.

What does **not** cross the process boundary: anything a child mutates in
this process's memory -- request-scoped caches, ``contextvars`` values,
module globals, the reporter's state. A child starts with a copy-on-write
snapshot of the parent's state at fork time and its writes die with it. A
caller may only pass work whose *returned value* is its whole effect (plus
side effects outside the process, such as files).
"""

from __future__ import annotations

import multiprocessing
from collections import deque
from collections.abc import Callable, Sequence
from multiprocessing.connection import wait as wait_ready
from typing import TYPE_CHECKING, Any, TypeVar

from ..errors import SnapshotError
from . import memory_trace
from .side_isolation import fork_child, reap_child

if TYPE_CHECKING:
    from .release_admission import MemoryAdmission

__all__ = ["run_keyed_in_processes"]

_T = TypeVar("_T")


def _child_body(fn: Callable[[str], _T], key: str) -> Callable[[], tuple[_T, int]]:
    def run() -> tuple[_T, int]:
        from ..storage.ast_size_observer import observe_ast_sizes

        seen = [0]

        def _sink(nbytes: int) -> None:
            seen[0] += nbytes

        with memory_trace.phase("release.member", key=key), observe_ast_sizes(_sink):
            value = fn(key)
        return value, seen[0]

    return run


def _receive(recv: Any, proc: Any) -> tuple[str, Any]:
    """One child's answer: ``("ok", (value, ast_bytes))`` or ``("err", exc)``."""
    from ..storage.acyclic_json import gc_paused

    try:
        # Unpickling builds a whole result graph at once; see side_isolation.
        with gc_paused():
            status, value = recv.recv()
    except EOFError:
        proc.join()
        return "err", SnapshotError(
            f"member process exited with status {proc.exitcode} without "
            "returning a result"
        )
    except OSError as exc:
        return "err", SnapshotError(f"member result transfer failed: {exc!r}")
    return status, value


def _settle(
    recv: Any,
    proc: Any,
    key: str,
    on_error: Callable[[Exception, str], _T],
) -> tuple[_T, int]:
    """Collect one finished child: its value (or ``on_error``'s) and the
    header-AST bytes it reported."""
    status, value = _receive(recv, proc)
    recv.close()
    reap_child(proc, escalate=status != "ok")
    if status == "ok":
        result, ast_bytes = value
        return result, ast_bytes
    exc = (
        value
        if isinstance(value, Exception)
        else SnapshotError(f"member process failed: {value!r}")
    )
    return on_error(exc, key), 0


def run_keyed_in_processes(
    keys: Sequence[str],
    fn: Callable[[str], _T],
    *,
    max_workers: int,
    on_error: Callable[[Exception, str], _T],
    admission: MemoryAdmission | None = None,
) -> list[_T]:
    """Run ``fn(key)`` for every key, each in its own forked child, at most
    *max_workers* alive at once; see the module docstring."""
    ctx = multiprocessing.get_context("fork")
    pending = deque(keys)
    running: dict[Any, tuple[str, Any, float | None]] = {}
    results_by_key: dict[str, _T] = {}

    def _release(cost: float | None, ast_bytes: int) -> None:
        if admission is not None and cost is not None:
            admission.release(cost, ast_bytes)

    try:
        while pending or running:
            while pending and len(running) < max(1, max_workers):
                cost = admission.try_reserve() if admission is not None else None
                if admission is not None and cost is None:
                    break  # wait for a running member to free its budget
                key = pending.popleft()
                # Not daemonic: a member forks its own side children.
                proc, recv = fork_child(ctx, _child_body(fn, key), daemon=False)
                running[recv] = (key, proc, cost)
            for recv in wait_ready(list(running)):
                key, proc, cost = running.pop(recv)
                results_by_key[key], ast_bytes = _settle(recv, proc, key, on_error)
                _release(cost, ast_bytes)
    finally:
        # Only reached with children still running when this frame is left by
        # an exception (an `on_error` that raised, KeyboardInterrupt): stop
        # them rather than leave orphans computing results nobody reads.
        for recv, (_key, proc, cost) in running.items():
            if proc.is_alive():
                proc.terminate()
            recv.close()
            reap_child(proc, escalate=True)
            _release(cost, 0)
    return [results_by_key[key] for key in keys if key in results_by_key]
