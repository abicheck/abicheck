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

"""Regression tests for ``abicheck.deadline`` (P0 SVS header-scan defect).

These are the fast, synthetic-fixture proofs the field report asked for:

- a fake worker that overruns an active deadline is stopped *before* it can
  start a new subprocess (never just "checked once at the end");
- a subprocess that IS started and overruns its timeout has its **entire
  process group** killed, not just the immediate child — the actual root
  cause of the original 15,000+ second / 3+ GiB orphaned-process run;
- ``--budget``, once set, is not silently re-capped back down to the old
  fixed internal timeout.

No external tool (clang/castxml) is required — these exercise
``abicheck.deadline`` directly via ``sh``/``sleep``, which is present on any
POSIX CI runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from abicheck import deadline

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="process-group kill semantics are POSIX-specific"
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_no_active_deadline_is_unbounded() -> None:
    assert deadline.remaining() is None
    assert deadline.bounded_timeout(120) == 120
    deadline.check()  # must not raise


def test_deadline_scope_sets_and_resets_remaining() -> None:
    assert deadline.remaining() is None
    with deadline.deadline_scope(10.0):
        left = deadline.remaining()
        assert left is not None
        assert 0 < left <= 10.0
    # The scope's ContextVar token is reset on exit — no leakage into later code.
    assert deadline.remaining() is None


def test_deadline_scope_none_is_a_no_op() -> None:
    with deadline.deadline_scope(None):
        assert deadline.remaining() is None
        assert deadline.bounded_timeout(45) == 45


def test_bounded_timeout_uses_remaining_even_when_larger_than_default() -> None:
    # A generous explicit --budget must not be silently truncated back down to
    # the caller's internal fixed default (the P0 report: the old code always
    # used a fixed 120s regardless of --budget). With 10 minutes left on the
    # budget and a 120s internal default, the effective timeout must reflect
    # the 10-minute budget, not 120s.
    with deadline.deadline_scope(600.0):
        eff = deadline.bounded_timeout(120)
        assert eff > 120


def test_bounded_timeout_uses_remaining_when_smaller_than_default() -> None:
    with deadline.deadline_scope(5.0):
        eff = deadline.bounded_timeout(120)
        assert 0 < eff <= 5.0


def test_check_raises_once_deadline_has_passed() -> None:
    with deadline.deadline_scope(0.01):
        time.sleep(0.05)
        with pytest.raises(deadline.DeadlineExceeded):
            deadline.check()


def test_bounded_timeout_raises_once_deadline_has_passed() -> None:
    with deadline.deadline_scope(0.01):
        time.sleep(0.05)
        with pytest.raises(deadline.DeadlineExceeded):
            deadline.bounded_timeout(120)


def test_run_bounded_never_spawns_once_deadline_has_passed(tmp_path) -> None:
    # The "mid-stage, not just after the fact" requirement: a worker with no
    # time left must not even start the next subprocess. Proven by a marker
    # file the command would create — it must never appear.
    marker = tmp_path / "spawned.marker"
    with deadline.deadline_scope(0.01):
        time.sleep(0.05)
        with pytest.raises(deadline.DeadlineExceeded):
            deadline.run_bounded(
                ["touch", str(marker)], timeout=120, capture_output=True, text=True
            )
    assert not marker.exists()


def test_run_bounded_normal_completion() -> None:
    result = deadline.run_bounded(
        [sys.executable, "-c", "print('hi')"],
        timeout=30,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "hi" in result.stdout


def test_run_bounded_raises_timeout_expired_on_overrun() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        deadline.run_bounded(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.2,
            capture_output=True,
            text=True,
        )


def test_run_bounded_raises_deadline_exceeded_for_inflight_timeout_under_budget() -> None:
    # Codex review finding: a subprocess that is already *running* when an
    # active --budget's deadline hits must still be reported as a budget
    # overflow (DeadlineExceeded -> _BudgetOverflow/exit 5), not a generic
    # subprocess.TimeoutExpired/parse-timeout (SnapshotError/exit 1) — the two
    # mean very different things to a caller like scan_engine.run_scan_core.
    # Distinct from test_bounded_timeout_raises_once_deadline_has_passed
    # (deadline already gone *before* spawning): here the deadline expires
    # *while* the process is running, which is the actual clang/castxml case
    # (a `--budget 5s` header parse that overruns mid-parse, not one that
    # never got to start).
    with deadline.deadline_scope(0.2):
        with pytest.raises(deadline.DeadlineExceeded):
            deadline.run_bounded(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                # A generous per-call default: the *active budget* (0.2s), not
                # this default, is what actually bounds the call.
                timeout=120,
                capture_output=True,
                text=True,
            )


def test_run_bounded_kills_group_member_that_ignores_sigterm(tmp_path) -> None:
    # Codex review finding: the old escalation only sent SIGKILL when
    # proc.wait() (the *direct* child) itself timed out. A grandchild that
    # traps/ignores SIGTERM while the direct child exits promptly on SIGTERM
    # (the common case — most processes don't override the default handler)
    # would then dodge SIGKILL entirely: proc.wait() succeeds quickly, so the
    # escalation guarded on its TimeoutExpired never ran. Reproduce exactly
    # that shape: a direct child with default SIGTERM handling that spawns a
    # grandchild which explicitly ignores SIGTERM.
    pid_file = tmp_path / "ignorer.pid"
    grandchild_src = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    parent_src = (
        "import subprocess, sys, time\n"
        f"gc = subprocess.Popen([sys.executable, '-c', {grandchild_src!r}])\n"
        f"open({str(pid_file)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n"  # direct child: default SIGTERM handling -> dies promptly
    )
    cmd = [sys.executable, "-c", parent_src]
    with pytest.raises(subprocess.TimeoutExpired):
        deadline.run_bounded(cmd, timeout=0.3, capture_output=True, text=True)

    deadline_check = time.monotonic() + 5
    child_pid = None
    while time.monotonic() < deadline_check:
        if pid_file.exists():
            text = pid_file.read_text().strip()
            if text:
                child_pid = int(text)
                break
        time.sleep(0.05)
    assert child_pid is not None, "grandchild never recorded its PID"

    for _ in range(50):
        if not _pid_alive(child_pid):
            break
        time.sleep(0.1)
    assert not _pid_alive(child_pid), (
        f"SIGTERM-ignoring grandchild {child_pid} survived — the SIGKILL "
        "escalation must run unconditionally after the grace period, not "
        "only when the direct child itself fails to exit in time"
    )


def test_run_bounded_kills_entire_process_group_on_timeout(tmp_path) -> None:
    # The actual root cause: a bare `subprocess.run(cmd, timeout=N)` only kills
    # the *direct* child on TimeoutExpired. A compiler driver's grandchildren
    # (cc1/cc1plus, an integrated assembler, a wrapped ccache/distcc call) are
    # not in that child's own process group and survive as orphans — which is
    # how the original SVS bug report measured 3+ GiB RSS and a run that only
    # ended via an *external* SIGKILL after 15,000+ seconds. Reproduce the
    # shape with `sh` backgrounding a long-lived grandchild and prove
    # run_bounded's timeout kill reaches it too.
    pid_file = tmp_path / "child.pid"
    cmd = ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"]
    with pytest.raises(subprocess.TimeoutExpired):
        deadline.run_bounded(cmd, timeout=0.3, capture_output=True, text=True)

    # Give the OS a brief moment to actually reap the killed processes.
    deadline_check = time.monotonic() + 5
    child_pid = None
    while time.monotonic() < deadline_check:
        if pid_file.exists():
            text = pid_file.read_text().strip()
            if text:
                child_pid = int(text)
                break
        time.sleep(0.05)
    assert child_pid is not None, "grandchild never recorded its PID"

    for _ in range(50):
        if not _pid_alive(child_pid):
            break
        time.sleep(0.1)
    assert not _pid_alive(child_pid), (
        f"grandchild sleep process {child_pid} survived as an orphan after "
        "run_bounded's timeout — the whole process group must be killed, not "
        "just the immediate child"
    )


def test_run_bounded_kills_process_group_on_deadline_exceeded_mid_run(tmp_path) -> None:
    # Same orphan-proof as above, but triggered through an active
    # deadline_scope (the --budget path) rather than a bare `timeout=` kwarg —
    # proves the scan-wide deadline, not just a per-call timeout, also cleans
    # up the whole tree. An in-flight timeout under an active deadline raises
    # DeadlineExceeded (not TimeoutExpired) — see
    # test_run_bounded_raises_deadline_exceeded_for_inflight_timeout_under_budget.
    pid_file = tmp_path / "child2.pid"
    cmd = ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"]
    with deadline.deadline_scope(0.3):
        with pytest.raises(deadline.DeadlineExceeded):
            deadline.run_bounded(cmd, timeout=120, capture_output=True, text=True)

    deadline_check = time.monotonic() + 5
    child_pid = None
    while time.monotonic() < deadline_check:
        if pid_file.exists():
            text = pid_file.read_text().strip()
            if text:
                child_pid = int(text)
                break
        time.sleep(0.05)
    assert child_pid is not None

    for _ in range(50):
        if not _pid_alive(child_pid):
            break
        time.sleep(0.1)
    assert not _pid_alive(child_pid)
