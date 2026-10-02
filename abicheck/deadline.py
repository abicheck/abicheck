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

"""Scan-wide deadline propagation + process-group-safe subprocess execution.

Closes the P0 header-scan defect (real-world Intel SVS field report): ``scan
--budget`` was only checked once, in the since-retired ``scan_engine.run_scan_core``, *after*
the expensive L2 header AST parse had already run to completion — a
pathological header (deep ``#include``/template complexity) could run for
hours regardless of ``--budget``. Worse, the clang/castxml ``subprocess.run``
calls that do the actual parsing used a fixed, budget-blind timeout with no
process-group isolation: on a timeout, ``subprocess.run`` only kills the
*direct* child, so a compiler driver's grandchildren (cc1/cc1plus, an
integrated assembler, a wrapped ccache/distcc invocation) survived as
orphans, which is how the original bug report measured multi-GiB RSS and a
15,000+ second run that only ended via an *external* SIGKILL.

Two independent pieces close that gap:

- :func:`deadline_scope` / :func:`bounded_timeout` — an absolute wall-clock
  deadline threaded via a ``contextvars.ContextVar`` so any subprocess call
  site *anywhere* in the L2 parse can ask "how much time do I actually have
  left", without threading a new parameter through every intermediate
  function signature between the (retired) ``scan_engine.run_scan_core`` and
  ``dumper.py``'s clang/castxml invocations. A deadline that has already
  passed raises immediately, *before* a new subprocess is spawned — the
  "checked inside the stage, not only after it" requirement.
- :func:`run_bounded` — a drop-in-ish replacement for
  ``subprocess.run(cmd, timeout=...)`` that starts the child in its own
  session (POSIX) and, on timeout, kills the *whole* process group
  (SIGTERM, then SIGKILL after a short grace period) instead of just the one
  process ``subprocess.run`` would kill. Mirrors the escalation shape of the
  MCP-path watchdog (``dry_run_estimate._kill_process_tree``, retired with
  ``run_scan_subprocess`` in ADR-068 Phase 4), which already got this right
  for that outer boundary — this module gives the *inner* per-subprocess call
  sites (dumper.py's clang/castxml invocations) the same no-orphans
  guarantee, without depending on that ``multiprocessing`` machinery.

This module has no dependency on ``click``/CLI/service types — pure process +
time-budget plumbing, safe to import from ``dumper.py``
or any future L3/L4 subprocess call site that wants the same treatment.
"""

from __future__ import annotations

import contextvars
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

#: Process groups run_bounded() currently has in flight, so an external
#: SIGTERM (see install_sigterm_cleanup) can find and kill them even though
#: they are detached (start_new_session=True) from this process's own group.
_active_pgroups: set[int] = set()
#: RLock, not Lock: install_sigterm_cleanup's handler runs on whichever
#: thread was interrupted (Python only delivers signals on the main
#: thread) — if that's the main thread mid-registration, already holding
#: this lock, a plain Lock would self-deadlock trying to re-acquire it
#: from inside the handler (CodeRabbit review, PR #591).
_active_pgroups_lock = threading.RLock()


class DeadlineExceeded(Exception):
    """The active scan deadline has already passed before a subprocess started.

    Distinct from ``subprocess.TimeoutExpired`` (raised once a *running*
    subprocess overruns its allotted slice): this fires up front, so a scan
    whose budget is already exhausted never starts a new multi-minute clang/
    castxml invocation it has no chance of finishing within the deadline.
    """

    def __init__(self, remaining_s: float) -> None:
        self.remaining_s = remaining_s
        super().__init__(
            f"scan deadline already exceeded ({-remaining_s:.1f}s over budget); "
            "refusing to start a new subprocess"
        )


_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "abicheck_scan_deadline", default=None
)


@contextmanager
def deadline_scope(seconds: float | None) -> Iterator[None]:
    """Set an absolute wall-clock deadline for the duration of the ``with`` block.

    *seconds* is a duration from *now* (``time.monotonic() + seconds``), not an
    absolute timestamp — callers pass the same ``--budget`` seconds value
    the retired ``scan_engine._check_scan_budget`` received. ``None`` means "no
    budget": :func:`remaining`/:func:`bounded_timeout` see no deadline inside
    the scope, matching today's unbounded behaviour exactly (no regression
    when ``--budget`` is not given).

    Any subprocess call reached while this scope is active — however deep the
    call stack — can read the shrinking deadline via :func:`bounded_timeout`
    without the caller threading a parameter through every function in
    between, *as long as the call stays on the same OS thread*.
    ``contextvars`` do **not** cross a ``ThreadPoolExecutor``/
    ``ProcessPoolExecutor`` boundary — a worker submitted from inside this
    scope starts with a fresh, empty context and sees no active deadline. A
    caller that dispatches work to such a pool must capture
    :func:`current_deadline_ts` beforehand and re-enter it inside each worker
    via :func:`with_deadline_ts` (see ``buildsource/source_replay.py``'s
    ``_deadline_bound_worker`` for the pattern).
    """
    deadline_ts = time.monotonic() + seconds if seconds is not None else None
    with with_deadline_ts(deadline_ts):
        yield


def current_deadline_ts() -> float | None:
    """The active deadline as an absolute ``time.monotonic()`` timestamp, or ``None``.

    Unlike :func:`remaining`, this value is stable to capture once (e.g. just
    before dispatching work to a ``ThreadPoolExecutor``/``ProcessPoolExecutor``,
    whose workers don't inherit the calling thread's ``ContextVar`` state) and
    pass explicitly into a worker, which re-establishes it with
    :func:`with_deadline_ts`.
    """
    return _deadline.get()


@contextmanager
def with_deadline_ts(deadline_ts: float | None) -> Iterator[None]:
    """Like :func:`deadline_scope`, but takes an absolute timestamp already
    captured via :func:`current_deadline_ts` rather than a duration from now.

    Use this inside a pool worker to re-establish a deadline captured on the
    submitting thread — see :func:`deadline_scope` for why that's necessary.
    """
    token = _deadline.set(deadline_ts)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining() -> float | None:
    """Seconds left on the active deadline, or ``None`` if no deadline is set."""
    deadline_ts = _deadline.get()
    if deadline_ts is None:
        return None
    return deadline_ts - time.monotonic()


def check() -> None:
    """Raise :class:`DeadlineExceeded` if the active deadline has already passed.

    A no-op when no deadline is active. Call this before starting any
    expensive per-header/per-TU unit of work (not just before spawning a
    subprocess) so a multi-header scan stops *between* headers as soon as the
    budget is gone, rather than only being caught by :func:`bounded_timeout`
    on the next subprocess call.
    """
    left = remaining()
    if left is not None and left <= 0:
        raise DeadlineExceeded(left)


def bounded_timeout(default: float) -> float:
    """The effective subprocess timeout for this call.

    With no active deadline (no ``--budget`` given), returns *default*
    unchanged — the caller's own fixed timeout, exactly today's behaviour, so
    an unbudgeted scan never regresses. With an active deadline, returns
    whatever time is actually left on it — **not** ``min(default, left)`` —
    because the whole point of ``--budget`` is that the caller asked for up to
    that much time; silently truncating a generous explicit budget back down
    to the internal default would defeat it (and produce a confusing "timed
    out after Ns" message under a budget the user set far higher than N).
    Raises :class:`DeadlineExceeded` up front (without spawning anything) when
    the deadline has already passed.
    """
    left = remaining()
    if left is None:
        return default
    if left <= 0:
        raise DeadlineExceeded(left)
    return left


@contextmanager
def supervised_popen(
    cmd: list[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
    text: bool = False,
) -> Iterator[subprocess.Popen[Any]]:
    """Start *cmd* as a supervised child and yield its ``Popen`` handle.

    The one place in the codebase that starts a child in its own process
    group (design-hardening plan, Phase 6). Every caller that needs the live
    handle -- a capped reader, an RSS sampler -- uses this rather than its own
    ``start_new_session=True``/``killpg`` pair, so each child gets the same
    guarantees :func:`run_bounded` gives: its group is registered for
    :func:`install_sigterm_cleanup` before any signal can miss it, and any
    exception leaving the ``with`` block (a timeout, ``KeyboardInterrupt``,
    a reader error) tears the whole group down via
    :func:`terminate_process_tree`. A normal exit leaves the child alone; the
    caller owns waiting for it.
    """
    use_pgroup = os.name == "posix"
    if use_pgroup:
        # Close the Popen()->_register_pgroup() race two ways at once, since
        # each closes a different half of it (Codex review, PR #591, rounds
        # 6-7). Without either, an external SIGTERM (job-scheduler
        # cancellation, a CI step's own timeout) landing in that gap would
        # run install_sigterm_cleanup's handler with an empty/stale registry,
        # leaving the just-spawned group outside both this process's own
        # group and the cleanup set — permanently orphaned:
        #
        # - A per-thread registration window, which _sigterm_cleanup_handler
        #   honours by deferring itself, closes the same-thread case: a
        #   signal landing while this very thread is running
        #   Popen()/_register_pgroup() (e.g. the single-threaded CLI path
        #   with no thread pool). Necessary because _active_pgroups_lock is
        #   an RLock -- a same-thread handler invocation would re-enter it
        #   instead of blocking, and see the registry before
        #   _register_pgroup() ran. A deferred signal is replayed the moment
        #   the window closes, when the new pgid is tracked.
        #
        #   This used to be pthread_sigmask(SIG_BLOCK, {SIGTERM}). A signal
        #   mask is inherited across fork+exec, so every child started here
        #   ran with SIGTERM *blocked*: _kill_process_tree's SIGTERM never
        #   arrived, each timeout waited out the full grace period before
        #   SIGKILL, and no compiler ever got a graceful shutdown. Python
        #   only ever runs a signal handler on the main thread, between
        #   bytecodes, so a window flag it reads is an exact, child-invisible
        #   replacement.
        # - An in-flight spawn count closes the cross-thread case:
        #   run_bounded() is invoked from worker threads (the L4/L5 paths,
        #   the release fan-out's member jobs), but CPython only ever runs
        #   the installed SIGTERM handler on the *main* thread, whose own
        #   window is closed -- so the handler first stops new spawns, then
        #   waits for every spawn already past Popen() to register before
        #   it reads the registry (_begin_spawn/_end_spawn).
        #
        #   This used to hold _active_pgroups_lock across Popen() itself,
        #   which gave the same guarantee by serializing every process spawn
        #   in the interpreter on one lock: on a real release run that lock
        #   was the hottest wait in the profile. The count is held only for
        #   the increment and the register-and-decrement, so concurrent
        #   spawns proceed concurrently, and the handler still never reads
        #   a registry missing a group some thread has started.
        _enter_registration_window()
        try:
            _begin_spawn()
        except BaseException:
            _exit_registration_window()
            raise
    proc: subprocess.Popen[Any] | None = None
    pgid: int | None = None
    try:
        proc = subprocess.Popen(  # noqa: S603 — cmd is caller-built argv, never shell text
            cmd,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            start_new_session=use_pgroup,
        )
    finally:
        if use_pgroup:
            try:
                pgid = _end_spawn(proc)
            finally:
                _exit_registration_window()
    assert proc is not None
    try:
        try:
            yield proc
        except BaseException:
            _kill_process_tree(proc, use_pgroup)
            raise
    finally:
        if pgid is not None:
            _unregister_pgroup(pgid)


def terminate_process_tree(proc: subprocess.Popen[Any]) -> None:
    """Tear down a :func:`supervised_popen` child and its whole group.

    SIGTERM, a short grace, then SIGKILL (see :func:`_kill_process_tree`).
    Safe to call more than once and on an already-exited child.
    """
    _kill_process_tree(proc, os.name == "posix")


def run_bounded(
    cmd: list[str],
    *,
    timeout: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
    text: bool = False,
    stdout: Any = None,
    stderr: Any = None,
    input: Any = None,
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run``, but bounded by the active deadline and safe to kill.

    *input*, like ``subprocess.run``'s, feeds the child's stdin and implies a
    piped stdin — without it the child inherits this process's stdin, which
    would hang a probe that reads from ``-`` (e.g. ``cc -E -x c++ -v -``)
    under an interactive terminal instead of the empty/redirected stdin
    ``subprocess.run(input=...)`` gives it.

    The child is started in its own process group on POSIX
    (``start_new_session=True``), so a timeout kills the *whole* tree via
    :func:`_kill_process_tree` instead of leaving compiler-driver grandchildren
    running as orphans. On non-POSIX platforms this degrades to
    ``Popen.kill()`` on the single process (best effort; process-group
    semantics don't exist the same way there).

    Re-raises ``subprocess.TimeoutExpired`` on an in-flight timeout **only**
    when no deadline is active — same contract as ``subprocess.run``, so
    existing ``except subprocess.TimeoutExpired`` handlers keep working
    unmodified for the unbudgeted case. When a deadline *is* active,
    :func:`bounded_timeout` already capped ``effective_timeout`` to exactly
    what was left of it, so any in-flight timeout under that scope is by
    construction the budget running out, not an ordinary parse hang — this
    raises :class:`DeadlineExceeded` instead, so a caller that (like
    ``dumper.py``) deliberately leaves ``DeadlineExceeded`` uncaught gets a
    budget-overflow signal instead of a plain-timeout one even when the
    subprocess was already running when the deadline hit (not just when it
    was already exhausted before spawning).
    """
    had_deadline = remaining() is not None
    effective_timeout = bounded_timeout(timeout)
    if capture_output:
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    with supervised_popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=stdout,
        stderr=stderr,
        text=text,
    ) as proc:
        try:
            out, err = proc.communicate(input=input, timeout=effective_timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process_tree(proc)
            # Drain the now-dead process's pipes so it doesn't linger as a zombie;
            # a short grace timeout, not the original (already-exhausted) one.
            try:
                drained_out, drained_err = proc.communicate(timeout=5)
                exc.output = drained_out
                exc.stderr = drained_err
            except subprocess.TimeoutExpired:
                pass
            if had_deadline:
                left = remaining()
                raise DeadlineExceeded(left if left is not None else 0.0) from exc
            raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _kill_process_tree(proc: subprocess.Popen[Any], use_pgroup: bool) -> None:
    """Terminate *proc* and, on POSIX, its entire process group.

    Escalates SIGTERM -> (short grace) -> SIGKILL, mirroring the existing
    MCP-path watchdog (``dry_run_estimate._kill_process_tree``) so the CLI header-
    scan path gets the same no-orphans guarantee. Best-effort: a process that
    already exited between the timeout firing and this call is not an error.

    The SIGKILL escalation runs unconditionally after the grace period —
    **not** only when ``proc.wait()`` itself times out. ``proc.wait()``
    tracks just the *direct* child; a grandchild that traps/ignores SIGTERM
    (or a wrapper that backgrounds a job and exits itself) can leave the
    direct child reaped while a sibling/child in the same group is still
    alive, and gating SIGKILL on the direct child's own exit would let that
    survivor dodge it. ``killpg(SIGKILL)`` on an already-fully-dead group is
    a harmless ``ProcessLookupError``, so escalating unconditionally costs
    nothing on the common case where SIGTERM was enough.

    Uses ``proc.pid`` directly as the pgid rather than looking it up via
    ``os.getpgid(proc.pid)``: ``run_bounded``'s ``start_new_session=True``
    makes the child both its session and process-group leader, so its pid
    *is* the pgid for the whole lifetime of that group — a POSIX process
    group's id never changes, even after its leader itself exits, as long as
    a member remains. A wrapper that backgrounds the real compiler and exits
    immediately would otherwise make ``os.getpgid(proc.pid)`` fail (the
    leader is already gone) and fall back to killing only the already-dead
    direct process, leaving the still-running backgrounded compiler orphaned
    (Codex review, PR #591).
    """
    if not use_pgroup:
        proc.kill()
        proc.wait()
        return
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
        proc.wait()
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


#: Signalled whenever an in-flight spawn finishes registering. Shares
#: _active_pgroups_lock, so "no spawn in flight" and "the registry holds
#: every started group" are observed together.
_spawn_cond = threading.Condition(_active_pgroups_lock)
#: run_bounded() calls past _begin_spawn() and not yet through _end_spawn().
_spawns_in_flight = 0
#: Set by the SIGTERM handler while it tears groups down: no new spawn may
#: start, or it could begin after the handler read the registry.
_terminating = False
#: How long the handler waits for in-flight spawns to register. A spawn is a
#: fork+exec, normally milliseconds; the bound only keeps a wedged Popen()
#: from turning a termination request into a hang.
_SPAWN_DRAIN_TIMEOUT_S = 5.0


class ProcessTerminating(subprocess.SubprocessError):
    """run_bounded() refused to start a child because this process is
    handling SIGTERM -- a child started now could escape the cleanup."""


def _begin_spawn() -> None:
    global _spawns_in_flight
    with _spawn_cond:
        if _terminating:
            raise ProcessTerminating(
                "not starting a subprocess: this process is terminating"
            )
        _spawns_in_flight += 1


def _end_spawn(proc: subprocess.Popen[Any] | None) -> int | None:
    """Register *proc*'s group (``None``: Popen() raised) and retire the
    in-flight count in one critical section, then wake a waiting handler."""
    global _spawns_in_flight
    with _spawn_cond:
        pgid = _register_pgroup(proc) if proc is not None else None
        _spawns_in_flight -= 1
        _spawn_cond.notify_all()
    return pgid


def _register_pgroup(proc: subprocess.Popen[Any]) -> int:
    """Track *proc*'s process group for :func:`install_sigterm_cleanup`.

    Uses ``proc.pid`` directly rather than looking it up via
    ``os.getpgid(proc.pid)``: with ``start_new_session=True`` the child's pid
    *is* its own pgid for the whole lifetime of that group, and a live lookup
    can fail on a fast wrapper that backgrounds the real work and exits
    immediately — even though the backgrounded child is still very much
    alive in that same group. A failed lookup here would silently skip
    registration, so an external SIGTERM would see no tracked group and
    leave the detached compiler orphaned (Codex review, PR #591) — the same
    class of race :func:`_kill_process_tree` avoids the same way.
    """
    pgid = proc.pid
    with _active_pgroups_lock:
        _active_pgroups.add(pgid)
    return pgid


# Per-thread depth of run_bounded()'s Popen()->_register_pgroup() window
# (nested calls stack), and whether a SIGTERM arrived on the main thread while
# its window was open. See run_bounded()'s comment for why this is not a
# signal mask.
_registration_window = threading.local()
_deferred_sigterm = False


def _registration_window_depth() -> int:
    depth: int = getattr(_registration_window, "depth", 0)
    return depth


def _enter_registration_window() -> None:
    _registration_window.depth = _registration_window_depth() + 1


def _exit_registration_window() -> None:
    global _deferred_sigterm
    _registration_window.depth = _registration_window_depth() - 1
    # Only the main thread can have deferred the handler (CPython runs it
    # nowhere else), and only the main thread may replay it: the handler
    # calls signal.signal(), which raises off the main thread. A worker
    # closing its own window leaves the flag for the main thread.
    if (
        _registration_window.depth == 0
        and _deferred_sigterm
        and threading.current_thread() is threading.main_thread()
    ):
        _deferred_sigterm = False
        _sigterm_cleanup_handler(signal.SIGTERM, None)


def _unregister_pgroup(pgid: int) -> None:
    with _active_pgroups_lock:
        _active_pgroups.discard(pgid)


def _sigterm_cleanup_handler(signum: int, frame: Any) -> None:  # noqa: ARG001 - signal handler signature
    """Kill every ``run_bounded()`` process group still in flight, then re-exit via SIGTERM.

    ``run_bounded`` deliberately detaches its child into its own session
    (``start_new_session=True``) so a *timeout it detects itself* can kill the
    whole group via :func:`_kill_process_tree`. That detachment has a side
    effect: it also shields the child from an *external* SIGTERM sent to this
    process (a job scheduler cancelling the run, a CI step's own timeout,
    ``kill -TERM <pid>``) — Python's default SIGTERM disposition terminates
    the process immediately, without running ``run_bounded``'s own
    ``except``/``finally`` cleanup, so the detached compiler would be
    orphaned. This handler (installed by :func:`install_sigterm_cleanup`)
    closes that gap: best-effort SIGKILL every tracked group (no time for a
    graceful SIGTERM+wait inside a signal handler), then restores the
    default SIGTERM disposition and re-sends SIGTERM to this process so it
    still exits with normal signal-termination semantics (exit status,
    shell ``$?``, etc.) rather than swallowing the signal.
    """
    global _deferred_sigterm, _terminating
    if _registration_window_depth() > 0:
        # This (main) thread is between Popen() and _register_pgroup():
        # the newest group is not tracked yet. Replayed on window exit.
        _deferred_sigterm = True
        return
    with _spawn_cond:
        # Stop new spawns, then let every spawn already past Popen() on
        # another thread register, so the registry read below is complete.
        # This thread has none in flight (its window is closed), so the
        # wait cannot be on itself.
        _terminating = True
        _spawn_cond.wait_for(
            lambda: _spawns_in_flight == 0, timeout=_SPAWN_DRAIN_TIMEOUT_S
        )
        pgids = list(_active_pgroups)
    try:
        for pgid in pgids:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)
    finally:
        # Reached only if the self-SIGTERM did not end the process (it is
        # ignored or blocked, or a test stubbed os.kill): spawning is
        # allowed again rather than refused for the rest of its life.
        with _spawn_cond:
            _terminating = False


def _is_posix() -> bool:
    """Indirection over ``os.name`` so tests can stub the platform check without
    mutating the real ``os.name`` (which pathlib and other stdlib consumers
    read live — patching it process-wide corrupts unrelated code)."""
    return os.name == "posix"


def install_sigterm_cleanup() -> None:
    """Install the SIGTERM handler that kills orphaned ``run_bounded()`` process groups.

    Call once from the CLI entry point (``cli.main``) — the plain CLI/CI path
    has no outer watchdog analogous to the MCP path's
    ``dry_run_estimate._kill_process_tree`` (Codex review, PR #591). A no-op on
    non-POSIX platforms (no process groups) or off the main thread (Python
    only allows installing signal handlers there) — best-effort by design,
    same as the rest of this module's process cleanup.
    """
    if not _is_posix():
        return
    try:
        signal.signal(signal.SIGTERM, _sigterm_cleanup_handler)
    except ValueError:
        pass
