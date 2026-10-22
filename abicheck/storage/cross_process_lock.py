# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""One producer per disk-cache entry across processes.

The in-process acquisition table (``storage.header_ast_cache``) gives every
thread of one process a single producer per AST key. Forked release members
(``workflows.release_member_processes``) and forked compare sides do not share
that table, so two children over one header set each ran clang, and each
re-streamed the same projection from a 250 MB AST, where the thread fan-out ran
it once (measured on a 28-member release: ``dump.binary`` 2.4x per dump, and
``clang_ast`` 166.5 s against 5.9 s).

:func:`single_flight` restores the sharing through the disk cache: the first
process to take the lock runs the producer and writes the entry, and a
process that waited on it then finds the entry and reads it. The lock is
an advisory ``flock``, which the OS releases if the holder dies, so a crash
never leaves a stale lock. Where the lock cannot be taken (no ``fcntl``, an
unwritable lock directory, reference mode) it is a no-op: the only cost is
the duplicate work this module exists to remove, never a wrong result.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

import abicheck.deadline as deadline

from ..model.execution_cache import reference_mode

__all__ = ["produce_ast_once_across_processes", "single_flight"]

_T = TypeVar("_T")

#: Poll interval while another process holds the lock. Short against the
#: work it waits on (a clang run, a projection stream: seconds to minutes).
_POLL_S = 0.05


def _lock_path(name: str) -> str | None:
    from .ast_cache_location import backend_cache_dir

    try:
        lock_dir = backend_cache_dir("locks")
    except OSError:
        return None
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]
    return str(lock_dir / f"{digest}.lock")


@contextmanager
def single_flight(name: str) -> Iterator[None]:
    """Hold the exclusive lock named *name* for the duration of the block.

    Waits for a holder in any process (or any thread: ``flock`` locks an
    open file description, and each call opens its own), polling so the
    active request deadline still bounds the wait. The ``.lock`` file stays
    behind, tiny and reused; unlinking it would race a waiter that already
    has it open.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        yield
        return
    path = None if reference_mode() else _lock_path(name)
    fd = -1
    if path is not None:
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError:
            fd = -1
    if fd < 0:
        yield
        return
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                deadline.check()
                time.sleep(_POLL_S)  # perf-ok: bounded lock poll
            except OSError:
                # A filesystem without flock support: run unlocked.
                os.close(fd)
                fd = -1
                break
        yield
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


#: Backends whose producer writes a disk-cache entry a waiting process can
#: read back. Other acquisitions (normalizations, id-keyed indexes) are
#: process-local, and serializing them across processes would buy nothing.
_DISK_BACKED_BACKENDS = frozenset({"clang", "castxml"})


def produce_ast_once_across_processes(
    backend: str, key: str, producer: Callable[[], _T], group: Any
) -> _T:
    """*producer*, holding the cross-process lock for a disk-backed AST key.

    ``storage.header_ast_cache``'s acquisition table gives one producer per
    key *within* a process; forked members and sides each have their own.
    Under this lock the first process runs the frontend and writes the
    entry, and a process that waited runs its producer after it -- which
    then decodes that entry instead of re-running the frontend.
    """
    if group is not None or backend not in _DISK_BACKED_BACKENDS:
        return producer()
    from .ast_cache_location import AST_DISK_CACHE

    if not AST_DISK_CACHE.enabled():
        return producer()
    with single_flight(f"ast:{backend}:{key}"):
        return producer()
