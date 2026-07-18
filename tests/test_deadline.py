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
import signal
import subprocess
import sys
import threading
import time

import pytest

from abicheck import deadline

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="process-group kill semantics are POSIX-specific"
)


def _pid_alive(pid: int) -> bool:
    """True if *pid* is a live, non-zombie process.

    A zombie (defunct) process still answers ``os.kill(pid, 0)`` successfully
    — its PID slot isn't released until its parent reaps it — even though it
    has already terminated and holds no resources beyond the exit-status
    table entry. On a container runner with no proper init/PID-1 reaper, an
    orphaned grandchild we just killed can sit as a zombie indefinitely once
    its (already-reaped) direct-child parent is gone, which would make these
    tests flake as "still alive" even though the kill worked (Codex review).
    ``ps -o stat=`` is portable across Linux/BSD/macOS and reports ``Z`` for a
    zombie; anything else (or the PID no longer existing at all) means it is
    not a still-running process for the purposes of these orphan checks.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # can't determine state; assume alive (conservative)
    state = result.stdout.strip()
    if not state:
        return False  # ps found nothing -> already gone
    return not state.startswith("Z")


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


def test_run_bounded_raises_deadline_exceeded_for_inflight_timeout_under_budget() -> (
    None
):
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


# ── _kill_process_tree: fallback/escalation branches (mocked, no real subprocess) ──


class _FakeProc:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.killed = 0
        self.waits: list[float | None] = []
        self._wait_raises = False

    def kill(self) -> None:
        self.killed += 1

    def wait(self, timeout: float | None = None) -> None:
        self.waits.append(timeout)
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd=["x"], timeout=timeout)


def test_kill_process_tree_without_pgroup_kills_direct_process_only() -> None:
    # No process group to target (use_pgroup=False, e.g. non-POSIX) — fall
    # back to killing just the direct child.
    proc = _FakeProc()
    deadline._kill_process_tree(proc, use_pgroup=False)
    assert proc.killed == 1
    assert proc.waits == [None]


def test_kill_process_tree_reaches_group_after_wrapper_already_exited(
    monkeypatch,
) -> None:
    # Codex review (PR #591): a wrapper that backgrounds the real compiler
    # and exits itself races ahead of the timeout handler -- by the time
    # _kill_process_tree runs, an os.getpgid(proc.pid) lookup could fail
    # (the leader process is already gone) even though the process GROUP
    # (with the still-running backgrounded child) is very much alive.
    # proc.pid IS the pgid for the whole lifetime of that group
    # (start_new_session=True never changes it, even after the leader
    # exits), so no getpgid lookup is needed at all -- prove it is never
    # even called, and that killpg still reaches the group via proc.pid.
    proc = _FakeProc()

    def _boom(_pid: int) -> int:
        raise AssertionError("os.getpgid should not be called any more")

    monkeypatch.setattr(os, "getpgid", _boom)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    deadline._kill_process_tree(proc, use_pgroup=True)

    import signal

    assert (proc.pid, signal.SIGTERM) in signals


def test_kill_process_tree_sigterm_killpg_failure_falls_back_to_direct_kill(
    monkeypatch,
) -> None:
    proc = _FakeProc()

    def _boom(_pgid: int, _sig: int) -> None:
        raise PermissionError("no permission to signal that group")

    monkeypatch.setattr(os, "killpg", _boom)
    deadline._kill_process_tree(proc, use_pgroup=True)
    assert proc.killed == 1
    assert proc.waits == [None]


def test_kill_process_tree_escalates_to_sigkill_after_double_wait_timeout(
    monkeypatch,
) -> None:
    # Full escalation path: SIGTERM, a grace-period wait that itself times out
    # (a group member survives it), an unconditional SIGKILL sweep regardless,
    # then a final drain wait that also times out — both wait timeouts must be
    # swallowed, not propagated, and SIGKILL must still fire.
    proc = _FakeProc()
    proc._wait_raises = True
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    deadline._kill_process_tree(proc, use_pgroup=True)  # must not raise

    import signal

    assert (proc.pid, signal.SIGTERM) in signals
    assert (proc.pid, signal.SIGKILL) in signals
    assert proc.waits == [5, 5]  # both grace-period waits attempted


# ── run_bounded: its own exception-handling edges (mocked Popen) ────────────


class _FakePopen:
    """Stand-in for subprocess.Popen whose .communicate() raises on demand."""

    def __init__(self, cmd, **_kwargs) -> None:
        del cmd
        self.pid = 4321
        self.returncode = 0

    def communicate(self, input=None, timeout=None):  # noqa: A002
        del input
        effect = _POPEN_COMMUNICATE_EFFECTS.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    def kill(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> None:
        pass


_POPEN_COMMUNICATE_EFFECTS: list[BaseException | tuple[str, str]] = []


def test_run_bounded_swallows_second_timeout_while_draining(monkeypatch) -> None:
    # The drain communicate() after a kill can itself time out (a stubborn
    # group member is still holding the pipe open) — that must be swallowed,
    # not left to replace/mask the original TimeoutExpired being raised.
    global _POPEN_COMMUNICATE_EFFECTS
    _POPEN_COMMUNICATE_EFFECTS = [
        subprocess.TimeoutExpired(cmd=["x"], timeout=1),
        subprocess.TimeoutExpired(cmd=["x"], timeout=5),
    ]
    monkeypatch.setattr(deadline.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(deadline, "_kill_process_tree", lambda *a, **k: None)
    with pytest.raises(subprocess.TimeoutExpired):
        deadline.run_bounded(["x"], timeout=1)


def test_run_bounded_kills_tree_on_unexpected_communicate_error(monkeypatch) -> None:
    # An error other than TimeoutExpired (e.g. an OSError mid-communicate)
    # must still trigger the process-tree cleanup before propagating, not
    # leak the child/group.
    global _POPEN_COMMUNICATE_EFFECTS
    _POPEN_COMMUNICATE_EFFECTS = [OSError("pipe broke")]
    monkeypatch.setattr(deadline.subprocess, "Popen", _FakePopen)
    killed: list[bool] = []
    monkeypatch.setattr(
        deadline, "_kill_process_tree", lambda *a, **k: killed.append(True)
    )
    with pytest.raises(OSError, match="pipe broke"):
        deadline.run_bounded(["x"], timeout=1)
    assert killed == [True]


# ── external SIGTERM cleanup (Codex review, PR #591 round 2) ────────────────
#
# run_bounded() detaches its child into its own session (start_new_session=
# True) so a timeout *it detects itself* can kill the whole group. That
# detachment also shields the child from an *external* SIGTERM sent to this
# process (job-scheduler cancellation, a CI step's own timeout) — Python's
# default SIGTERM disposition exits immediately without running run_bounded's
# own except/finally cleanup, orphaning the detached compiler. These tests
# prove the registry + handler installed by install_sigterm_cleanup() close
# that gap for the plain CLI/CI path (no MCP-style outer watchdog there).


def test_run_bounded_blocks_sigterm_across_spawn_and_registration(monkeypatch) -> None:
    # Codex review (PR #591, round 6): an external SIGTERM landing in the gap
    # between Popen() detaching the child and _register_pgroup() tracking its
    # pgid would run install_sigterm_cleanup's handler with an empty
    # registry, permanently orphaning the just-spawned group (the handler
    # only kills what's *tracked*). SIGTERM must be blocked on this thread
    # across that whole window, unblocked only once registration is done, so
    # a SIGTERM that arrived mid-spawn is deferred until the handler can see
    # the group.
    #
    # CodeRabbit review (PR #591, round 10): the restore must use SIG_SETMASK
    # with the exact previous mask pthread_sigmask(SIG_BLOCK, ...) returned,
    # not an unconditional SIG_UNBLOCK — the latter would incorrectly clear
    # SIGTERM even if the calling thread already had it blocked for its own
    # reasons before entering run_bounded(). The fake pthread_sigmask below
    # returns a distinguishable sentinel "previous mask" on SIG_BLOCK and
    # asserts the restore call receives that exact value back.
    calls: list[str] = []
    real_popen = subprocess.Popen
    real_register = deadline._register_pgroup
    sentinel_prev_mask = frozenset({signal.SIGUSR1})

    def _tracking_popen(*a, **kw):
        calls.append("popen")
        return real_popen(*a, **kw)

    def _tracking_register(proc):
        calls.append("register")
        return real_register(proc)

    def _tracking_sigmask(how, mask):
        if how == signal.SIG_BLOCK:
            assert signal.SIGTERM in mask
            calls.append("block")
            return sentinel_prev_mask
        assert how == signal.SIG_SETMASK
        assert mask == sentinel_prev_mask
        calls.append("setmask")
        return mask

    monkeypatch.setattr(subprocess, "Popen", _tracking_popen)
    monkeypatch.setattr(deadline, "_register_pgroup", _tracking_register)
    monkeypatch.setattr(signal, "pthread_sigmask", _tracking_sigmask)
    with deadline._active_pgroups_lock:
        deadline._active_pgroups.clear()

    deadline.run_bounded(
        [sys.executable, "-c", "print('hi')"], timeout=5, capture_output=True, text=True
    )

    assert calls == ["block", "popen", "register", "setmask"]


def test_run_bounded_holds_registry_lock_across_spawn_and_registration(
    monkeypatch,
) -> None:
    # Codex review (PR #591, round 7): pthread_sigmask alone only defers
    # SIGTERM delivery on the thread that calls run_bounded(). The default
    # L4/L5 paths (source_replay, call_graph, type_graph) invoke run_bounded()
    # from ThreadPoolExecutor workers, but CPython always runs the installed
    # SIGTERM handler on the *main* thread regardless of which thread the
    # signal was delivered to — so a worker blocking SIGTERM on itself does
    # not stop the main thread from concurrently running
    # _sigterm_cleanup_handler against a stale registry. Holding
    # _active_pgroups_lock across the same Popen()->_register_pgroup() window
    # closes that gap too, since the handler acquires the same lock before
    # reading the registry (real inter-thread mutual exclusion, unlike
    # thread-local signal masking). This proves the lock is genuinely *held*
    # (not just briefly touched inside _register_pgroup) by having a second
    # thread attempt a blocking acquire while run_bounded is mid-spawn on a
    # different thread and confirming it cannot get in.
    entered = threading.Event()
    release_child = threading.Event()
    real_register = deadline._register_pgroup

    def _slow_register(proc):
        entered.set()
        release_child.wait(timeout=5)
        return real_register(proc)

    monkeypatch.setattr(deadline, "_register_pgroup", _slow_register)
    with deadline._active_pgroups_lock:
        deadline._active_pgroups.clear()

    acquired_during_window: list[bool] = []

    def _prober() -> None:
        entered.wait(timeout=5)
        got = deadline._active_pgroups_lock.acquire(timeout=0.3)
        acquired_during_window.append(got)
        if got:
            deadline._active_pgroups_lock.release()

    prober = threading.Thread(target=_prober)
    prober.start()

    runner = threading.Thread(
        target=lambda: deadline.run_bounded(
            [sys.executable, "-c", "print('hi')"],
            timeout=5,
            capture_output=True,
            text=True,
        )
    )
    runner.start()

    prober.join(timeout=5)
    release_child.set()
    runner.join(timeout=5)

    # A different thread's acquire() attempt, made while run_bounded() is
    # mid-spawn on the runner thread, must fail (block for the whole probe
    # window) — proving the lock is held cross-thread across the critical
    # section, not just momentarily inside _register_pgroup.
    assert acquired_during_window == [False]


def test_run_bounded_leaves_no_leftover_registered_pgroup() -> None:
    with deadline._active_pgroups_lock:
        deadline._active_pgroups.clear()
    deadline.run_bounded(
        [sys.executable, "-c", "print('hi')"], timeout=5, capture_output=True, text=True
    )
    with deadline._active_pgroups_lock:
        assert deadline._active_pgroups == set()


def test_register_pgroup_succeeds_even_when_getpgid_would_fail(monkeypatch) -> None:
    # Round-2 Codex review (PR #591): a fast wrapper that backgrounds the
    # real compiler and exits immediately can make a *live* os.getpgid(pid)
    # lookup raise, even though the backgrounded child is still alive in
    # that same group. _register_pgroup must not depend on that lookup
    # succeeding -- proc.pid IS the pgid by construction
    # (start_new_session=True) -- or the group silently never gets tracked,
    # leaving install_sigterm_cleanup's handler with nothing to kill.
    proc = _FakeProc(pid=5555)

    def _boom(_pid: int) -> int:
        raise AssertionError("os.getpgid should not be called any more")

    monkeypatch.setattr(os, "getpgid", _boom)
    with deadline._active_pgroups_lock:
        deadline._active_pgroups.clear()

    pgid = deadline._register_pgroup(proc)

    assert pgid == 5555
    with deadline._active_pgroups_lock:
        assert 5555 in deadline._active_pgroups


def test_sigterm_cleanup_handler_kills_tracked_process_group(
    monkeypatch, tmp_path
) -> None:
    # Reproduces the orphan shape from the timeout-kill tests above (a
    # backgrounded grandchild in the same process group), but proves the
    # *external SIGTERM* path kills it too -- not just an internally detected
    # timeout/deadline. os.kill is mocked so the handler's own
    # self-re-SIGTERM at the end doesn't actually terminate this test process.
    pid_file = tmp_path / "child.pid"
    cmd = ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"]
    proc = subprocess.Popen(cmd, start_new_session=True)  # noqa: S603 - fixed test argv
    pgid = os.getpgid(proc.pid)
    with deadline._active_pgroups_lock:
        deadline._active_pgroups.add(pgid)
    try:
        deadline_check = time.monotonic() + 5
        child_pid = None
        while time.monotonic() < deadline_check:
            if pid_file.exists() and pid_file.read_text().strip():
                child_pid = int(pid_file.read_text().strip())
                break
            time.sleep(0.05)
        assert child_pid is not None, "grandchild never recorded its PID"

        monkeypatch.setattr(deadline.os, "kill", lambda *_a, **_k: None)
        deadline._sigterm_cleanup_handler(signal.SIGTERM, None)

        for _ in range(50):
            if not _pid_alive(child_pid):
                break
            time.sleep(0.1)
        assert not _pid_alive(child_pid), (
            f"grandchild sleep process {child_pid} survived an external "
            "SIGTERM cleanup pass — the whole tracked group must be killed"
        )
    finally:
        with deadline._active_pgroups_lock:
            deadline._active_pgroups.discard(pgid)
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_install_sigterm_cleanup_installs_handler() -> None:
    original = signal.getsignal(signal.SIGTERM)
    try:
        deadline.install_sigterm_cleanup()
        assert signal.getsignal(signal.SIGTERM) is deadline._sigterm_cleanup_handler
    finally:
        signal.signal(signal.SIGTERM, original)


def test_install_sigterm_cleanup_noop_on_non_posix(monkeypatch) -> None:
    # Stubs the _is_posix() indirection rather than the real os.name -- os.name
    # is read live by pathlib and other stdlib consumers, so mutating it
    # process-wide (even via monkeypatch) corrupts unrelated code for the rest
    # of the test session.
    original = signal.getsignal(signal.SIGTERM)
    try:
        monkeypatch.setattr(deadline, "_is_posix", lambda: False)
        deadline.install_sigterm_cleanup()
        # Unchanged -- the non-POSIX branch returns before touching signal.signal.
        assert signal.getsignal(signal.SIGTERM) is original
    finally:
        signal.signal(signal.SIGTERM, original)
