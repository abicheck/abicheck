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
an advisory ``flock`` (``msvcrt.locking`` on Windows), which the OS
releases if the holder dies, so a crash never leaves a stale lock. Where
the lock cannot be taken (no lock primitive, an
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


class _LockBackend:
    """The OS primitive behind :func:`single_flight`: POSIX ``flock`` or, on
    Windows, ``msvcrt.locking`` over the file's first byte. Both are
    released by the OS when the holder's process dies, so a crash never
    leaves a stale lock. ``try_lock`` returns ``True`` once held, ``False``
    while another holder has it, and raises ``OSError`` where the
    filesystem cannot lock at all."""

    def __init__(self, module: Any, kind: str) -> None:
        self.module = module
        self.kind = kind

    def try_lock(self, fd: int) -> bool:
        if self.kind == "fcntl":
            try:
                self.module.flock(fd, self.module.LOCK_EX | self.module.LOCK_NB)
            except BlockingIOError:
                return False
            return True
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            self.module.locking(fd, self.module.LK_NBLCK, 1)
        except PermissionError:
            return False  # EACCES: another process holds the byte
        except OSError as exc:
            if getattr(exc, "errno", None) in _MSVCRT_BUSY_ERRNOS:
                return False
            raise
        return True

    def unlock(self, fd: int) -> None:
        if self.kind == "fcntl":
            self.module.flock(fd, self.module.LOCK_UN)
            return
        os.lseek(fd, 0, os.SEEK_SET)
        self.module.locking(fd, self.module.LK_UNLCK, 1)


#: ``msvcrt.locking`` reports a held byte as EACCES or EDEADLOCK.
_MSVCRT_BUSY_ERRNOS = frozenset({13, 36})


def _lock_backend() -> _LockBackend | None:
    """``fcntl`` where it exists, else ``msvcrt`` (Windows), else none."""
    try:
        import fcntl
    except ImportError:
        try:
            import msvcrt
        except ImportError:  # pragma: no cover - neither primitive
            return None
        return _LockBackend(msvcrt, "msvcrt")
    return _LockBackend(fcntl, "fcntl")


@contextmanager
def single_flight(name: str) -> Iterator[None]:
    """Hold the exclusive lock named *name* for the duration of the block.

    Waits for a holder in any process (or any thread: both primitives lock
    per open file description or handle, and each call opens its own),
    polling so the active request deadline still bounds the wait. The
    ``.lock`` file stays behind, tiny and reused; unlinking it would race a
    waiter that already has it open.
    """
    backend = _lock_backend()
    if backend is None:  # pragma: no cover - no lock primitive
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
    held = False
    try:
        while True:
            try:
                if backend.try_lock(fd):
                    held = True
                    break
                deadline.check()
                time.sleep(_POLL_S)  # perf-ok: bounded lock poll
            except OSError:
                # A filesystem without lock support: run unlocked.
                os.close(fd)
                fd = -1
                break
        yield
    finally:
        if fd >= 0:
            try:
                if held:
                    backend.unlock(fd)
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
