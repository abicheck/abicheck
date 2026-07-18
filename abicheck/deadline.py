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
--budget`` was only checked once, in ``scan_engine.run_scan_core``, *after*
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
  function signature between ``scan_engine.run_scan_core`` and
  ``dumper.py``'s clang/castxml invocations. A deadline that has already
  passed raises immediately, *before* a new subprocess is spawned — the
  "checked inside the stage, not only after it" requirement.
- :func:`run_bounded` — a drop-in-ish replacement for
  ``subprocess.run(cmd, timeout=...)`` that starts the child in its own
  session (POSIX) and, on timeout, kills the *whole* process group
  (SIGTERM, then SIGKILL after a short grace period) instead of just the one
  process ``subprocess.run`` would kill. Mirrors the escalation shape of the
  existing MCP-path watchdog (``service_scan._kill_process_tree``), which
  already gets this right for the outer `run_scan_subprocess` boundary — this
  module gives the *inner* per-subprocess call sites (dumper.py's clang/
  castxml invocations) the same no-orphans guarantee, without depending on
  that MCP-only ``multiprocessing`` machinery.

This module has no dependency on ``click``/CLI/service types — pure process +
time-budget plumbing, safe to import from ``dumper.py``, ``scan_engine.py``,
or any future L3/L4 subprocess call site that wants the same treatment.
"""

from __future__ import annotations

import contextvars
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: Process groups run_bounded() currently has in flight, so an external
#: SIGTERM (see install_sigterm_cleanup) can find and kill them even though
#: they are detached (start_new_session=True) from this process's own group.
_active_pgroups: set[int] = set()
_active_pgroups_lock = threading.Lock()


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
    ``scan_engine._check_scan_budget`` already receives. ``None`` means "no
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


def run_bounded(
    cmd: list[str],
    *,
    timeout: float,
    cwd: str | None = None,
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
    use_pgroup = os.name == "posix"
    if capture_output:
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
    proc = subprocess.Popen(  # noqa: S603 — cmd is caller-built argv, never shell text
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=stdout,
        stderr=stderr,
        text=text,
        start_new_session=use_pgroup,
    )
    pgid = _register_pgroup(proc) if use_pgroup else None
    try:
        try:
            out, err = proc.communicate(input=input, timeout=effective_timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(proc, use_pgroup)
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
        except BaseException:
            _kill_process_tree(proc, use_pgroup)
            raise
    finally:
        if pgid is not None:
            _unregister_pgroup(pgid)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _kill_process_tree(proc: subprocess.Popen[Any], use_pgroup: bool) -> None:
    """Terminate *proc* and, on POSIX, its entire process group.

    Escalates SIGTERM -> (short grace) -> SIGKILL, mirroring the existing
    MCP-path watchdog (``service_scan._kill_process_tree``) so the CLI header-
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
    """
    if not use_pgroup:
        proc.kill()
        proc.wait()
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        proc.kill()
        proc.wait()
        return
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


def _register_pgroup(proc: subprocess.Popen[Any]) -> int | None:
    """Track *proc*'s process group for :func:`install_sigterm_cleanup`, if resolvable."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return None
    with _active_pgroups_lock:
        _active_pgroups.add(pgid)
    return pgid


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
    with _active_pgroups_lock:
        pgids = list(_active_pgroups)
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def _is_posix() -> bool:
    """Indirection over ``os.name`` so tests can stub the platform check without
    mutating the real ``os.name`` (which pathlib and other stdlib consumers
    read live — patching it process-wide corrupts unrelated code)."""
    return os.name == "posix"


def install_sigterm_cleanup() -> None:
    """Install the SIGTERM handler that kills orphaned ``run_bounded()`` process groups.

    Call once from the CLI entry point (``cli.main``) — the plain CLI/CI path
    has no outer watchdog analogous to the MCP path's
    ``service_scan._kill_process_tree`` (Codex review, PR #591). A no-op on
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
