"""Cooperative cancellation of :func:`abicheck.deadline.run_bounded` children."""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from abicheck import deadline
from abicheck.deadline import CancelToken, ProcessCancelled, cancel_scope, run_bounded

_POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
_SLEEP_CHILD = [sys.executable, "-c", "import time; time.sleep(60)"]


def test_process_cancelled_is_not_an_ordinary_error() -> None:
    """Broad ``except Exception`` handlers on the header-graph path must not
    read a cancelled parse as a failed one."""
    assert issubclass(ProcessCancelled, BaseException)
    assert not issubclass(ProcessCancelled, Exception)


def test_no_token_leaves_run_bounded_unchanged() -> None:
    done = run_bounded(
        [sys.executable, "-c", "print('ok')"],
        timeout=30,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0
    assert done.stdout.strip() == "ok"


def test_a_cancelled_token_refuses_to_spawn(tmp_path: Path) -> None:
    marker = tmp_path / "spawned"
    token = CancelToken()
    token.cancel()
    with cancel_scope(token), pytest.raises(ProcessCancelled):
        run_bounded(
            [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
            timeout=30,
        )
    assert not marker.exists()


@_POSIX_ONLY
def test_cancelling_a_running_child_kills_it_promptly() -> None:
    token = CancelToken()
    timer = threading.Timer(0.3, token.cancel)
    timer.start()
    start = time.monotonic()
    try:
        with cancel_scope(token), pytest.raises(ProcessCancelled):
            run_bounded(_SLEEP_CHILD, timeout=120)
    finally:
        timer.cancel()
    # The child sleeps 60 s; a cancel that did not kill it would take that long.
    assert time.monotonic() - start < 15


@_POSIX_ONLY
def test_cancel_reaches_a_child_started_by_a_worker_pool() -> None:
    """contextvars do not cross a pool; carry_cancel_token must carry the token."""
    token = CancelToken()

    def worker() -> None:
        run_bounded(_SLEEP_CHILD, timeout=120)

    timer = threading.Timer(0.3, token.cancel)
    with cancel_scope(token), ThreadPoolExecutor(1) as pool:
        future = pool.submit(deadline.carry_cancel_token(worker))
        timer.start()
        start = time.monotonic()
        with pytest.raises(ProcessCancelled):
            future.result(timeout=30)
    timer.cancel()
    assert time.monotonic() - start < 15


def test_a_worker_submitted_without_carrying_the_token_is_not_cancelled() -> None:
    """The negative control: a pool worker that was not handed the token runs
    its child to completion even though the submitting thread is cancelled."""
    token = CancelToken()
    token.cancel()
    outputs: list[str] = []

    def worker() -> None:
        done = run_bounded(
            [sys.executable, "-c", "print('finished')"],
            timeout=30,
            capture_output=True,
            text=True,
        )
        outputs.append(done.stdout.strip())

    with cancel_scope(token), ThreadPoolExecutor(1) as pool:
        pool.submit(worker).result(timeout=30)
    assert outputs == ["finished"]
