# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The default run budget and the bound for a blocking wait on a child.

A ``compare`` run is never unbounded (ADR-068's 2026-11 amendment): with no
stated budget (``--budget`` omitted, ``CompareRequest.budget_s=None``, the
Action's ``budget`` input empty) it runs under :data:`DEFAULT_BUDGET_S`. Every
wait on a forked child or on a sibling member -- a pipe read, a
``multiprocessing.connection.wait``, a condition wait -- asks
:func:`wait_bound` how long it may block, so a child that is alive but never
answers fails the run with :class:`~abicheck.deadline.DeadlineExceeded`
instead of hanging it.
"""

from __future__ import annotations

import threading
from typing import Any

from ..deadline import DeadlineExceeded, remaining

__all__ = [
    "DEFAULT_BUDGET_S",
    "DEFAULT_BUDGET_TEXT",
    "recv_bounded",
    "wait_bound",
    "wait_expired",
]

#: The one wall-clock budget a run gets when the caller states none: 2 hours.
#: Registered as the ``budget`` concept's default in
#: ``architecture/option-vocabulary.yaml`` (ADR-091 D6). Also the bound for a
#: wait reached with no deadline active at all.
DEFAULT_BUDGET_S = 2 * 3600.0

#: :data:`DEFAULT_BUDGET_S` in ``--budget``'s own grammar, for messages.
DEFAULT_BUDGET_TEXT = "2h"


def wait_bound(waiting_for: str) -> float:
    """Seconds a blocking wait on a child (a pipe read, a condition) may last.

    The time left on the active deadline; with no deadline active,
    :data:`DEFAULT_BUDGET_S`. Raises :class:`~abicheck.deadline.DeadlineExceeded`
    naming *waiting_for* when the deadline has already passed. Pair it with
    :func:`wait_expired` for the wait that returns without an answer.
    """
    left = remaining()
    if left is None:
        return DEFAULT_BUDGET_S
    if left <= 0:
        raise DeadlineExceeded(left, waiting_for=waiting_for)
    return left


def wait_expired(waiting_for: str) -> DeadlineExceeded:
    """The error for a :func:`wait_bound`-bounded wait that timed out."""
    left = remaining()
    return DeadlineExceeded(left if left is not None else 0.0, waiting_for=waiting_for)


def recv_bounded(conn: Any, waiting_for: str) -> Any:
    """``conn.recv()`` from a child, bounded by :func:`wait_bound`.

    ``poll()`` reports a pipe readable as soon as any byte arrives, the
    length header included, and ``recv()`` then blocks until the whole frame
    is in. A child that stops mid-frame (deadlocked, stopped) would block
    that read for ever, so the read runs on a reader thread joined with the
    deadline's remaining time. On expiry this raises
    :class:`~abicheck.deadline.DeadlineExceeded`; the caller then stops the
    child, whose closed pipe ends the reader thread with ``EOFError``. Any
    error ``recv()`` raises (``EOFError``, ``OSError``, an unpickling error)
    is re-raised here unchanged.
    """
    from ..storage.acyclic_json import gc_paused

    bound = wait_bound(waiting_for)
    outcome: dict[str, Any] = {}

    def _read() -> None:
        try:
            # Unpickling builds a whole result graph at once; a gen-0
            # collection every few hundred allocations would rescan it.
            with gc_paused():
                outcome["value"] = conn.recv()
        except BaseException as exc:  # noqa: BLE001 -- re-raised in the caller
            outcome["error"] = exc

    reader = threading.Thread(target=_read, name="abicheck-child-recv", daemon=True)
    reader.start()
    reader.join(bound)
    if reader.is_alive():
        raise wait_expired(waiting_for)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
