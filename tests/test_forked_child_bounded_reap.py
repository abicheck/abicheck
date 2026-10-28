# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A forked child that never exits must not hang the run past the reap bound.

Bug class ``concurrency.forked_child_unbounded_reap``: every ``join()`` the
parent makes on a forked child is bounded. Three sites are covered here:

* the end-of-file branch of the side-isolation receive, where a child closes
  its result pipe and then never exits;
* the same branch in the keyed release process pool (``keyed_process_pool``);
* the normal-completion reap, where a child has sent its result but never
  exits (``reap_child(escalate=False)``).

Each stub child ignores SIGTERM, so only the SIGKILL escalation can end it.
The grace period is shrunk to a fraction of a second; the assertions bound
the whole call by a ceiling far above the expected cost, and the call runs
on a daemon thread so that the pre-fix hang fails the test instead of
wedging the suite.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest

import abicheck.workflows.keyed_process_pool as kpp
import abicheck.workflows.side_isolation as iso
from abicheck.errors import SnapshotError
from abicheck.workflows.keyed_process_pool import run_keyed_in_processes

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="isolation forks; Linux only"
)

#: Grace period substituted for the production 5 s; every escalation path
#: therefore costs a fraction of a second here.
GRACE_S = 0.2

#: Generous ceiling. A bounded reap costs about two grace periods; a hang
#: never returns, so any value short of forever distinguishes the two.
CEILING_S = 8.0


def _close_result_pipe_then_hang() -> None:
    """Close this child's two ends of its result pipe (the parent reads EOF),
    keep the exit sentinel open, then ignore SIGTERM forever.

    The result pipe is the one pipe whose both ends the child inherited: the
    parent's receive end and the child's send end. The sentinel must stay
    open. multiprocessing's ``Popen.wait(timeout)`` treats a readable sentinel
    as "exited" and then blocks in ``waitpid``, so a child that closed it would
    test that CPython behaviour rather than abicheck's bound.
    """
    links = {}
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        try:
            links[fd] = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
    counts = Counter(target for target in links.values() if target.startswith("pipe:"))
    result_pipes = {target for target, n in counts.items() if n == 2}
    for fd, target in links.items():
        if target in result_pipes:
            os.close(fd)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(3600)


def _hang_with_key(_key: str) -> None:
    _close_result_pipe_then_hang()


def _ignore_sigterm_forever() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(3600)


def _call_with_ceiling(fn: Callable[[], Any]) -> tuple[bool, float, Any, Any]:
    """Run *fn* on a daemon thread; return (finished, elapsed, value, error)."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 -- reported to the test
            box["error"] = exc

    start = time.monotonic()
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(CEILING_S)
    elapsed = time.monotonic() - start
    return (not worker.is_alive(), elapsed, box.get("value"), box.get("error"))


@pytest.fixture
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    # keyed_process_pool imports the constant by value, so patch both copies.
    monkeypatch.setattr(iso, "_TERMINATE_GRACE_SECONDS", GRACE_S)
    monkeypatch.setattr(kpp, "_TERMINATE_GRACE_SECONDS", GRACE_S)


@pytest.fixture(autouse=True)
def _no_leaked_children():
    yield
    # Never leave a SIGTERM-proof child behind, whatever the test observed.
    for child in multiprocessing.active_children():
        child.kill()
        child.join(GRACE_S)


@linux_only
@pytest.mark.timeout(60)
def test_eof_child_that_never_exits_is_bounded_in_side_isolation(short_grace) -> None:
    finished, elapsed, _value, error = _call_with_ceiling(
        lambda: iso.run_isolated([_close_result_pipe_then_hang], concurrent=False)
    )
    assert finished, (
        "side-isolation run hung on a child that closed its pipe and never exited"
    )
    assert elapsed < CEILING_S
    assert isinstance(error, SnapshotError)
    assert "without returning a snapshot" in str(error)


@linux_only
@pytest.mark.timeout(60)
def test_eof_child_that_never_exits_is_bounded_in_keyed_process_pool(
    short_grace,
) -> None:
    finished, elapsed, value, error = _call_with_ceiling(
        lambda: run_keyed_in_processes(
            ["member"],
            _hang_with_key,
            max_workers=1,
            on_error=lambda exc, key: ("failed", key, exc),
        )
    )
    assert finished, (
        "keyed process pool hung on a member that closed its pipe and never exited"
    )
    assert elapsed < CEILING_S
    assert error is None
    assert len(value) == 1
    _, key, exc = value[0]
    assert key == "member"
    assert isinstance(exc, SnapshotError)
    assert "without returning a result" in str(exc)


@linux_only
@pytest.mark.timeout(60)
def test_completed_child_that_never_exits_is_reaped_within_the_bound(
    short_grace,
) -> None:
    proc = multiprocessing.get_context("fork").Process(
        target=_ignore_sigterm_forever, daemon=True
    )
    proc.start()
    finished, elapsed, _value, error = _call_with_ceiling(
        lambda: iso.reap_child(proc, escalate=False)
    )
    assert finished, "reap_child(escalate=False) hung on a child that never exits"
    assert error is None
    assert elapsed < CEILING_S
    assert not proc.is_alive()
