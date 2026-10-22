# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Release members in forked processes: when, and what a member must carry
back so the result is the thread fan-out's.

:func:`member_processes_apply` picks the process fan-out
(:func:`~abicheck.workflows.keyed_process_pool.run_keyed_in_processes`) over
the thread one when all of these hold:

* more than one worker was planned (``-j1`` and the ``low-memory`` profile
  stay sequential, in this process);
* the interpreter has a GIL -- on a free-threaded build threads already
  scale and keep the shared caches below;
* ``fork`` is available and safe: Linux, and the caller runs no other thread
  (:func:`~abicheck.workflows.side_isolation.fork_is_safe`), the rule side
  isolation already follows;
* ``ABICHECK_MEMBER_PROCESSES`` is not ``0`` (an embedding host that must
  not fork, a test that observes members in-process).

A member child forks from a single-threaded parent, so it is itself
single-threaded: its own two sides still resolve in concurrent side children
(``side_isolation.concurrent_children_apply``). The thread fan-out could not
do that -- every worker saw a multi-threaded process and resolved its sides
in threads.

**What a process loses, and why the result is unchanged.** A child's writes
to this process's memory are discarded, so a member's whole effect must be
its returned entry (plus files ``--output-dir`` writes). Audited:

* the reporter's ``project``/``persist`` write into the entry or to disk;
  its ``notice``/``warning`` text is buffered in the child and replayed here
  by :func:`compare_members_in_processes`, in member order once the fan-out
  is done, a failed member's notice included --
  under ``CliRunner`` a forked child's echo to the in-memory stderr would
  otherwise be lost;
* request-scoped caches (the AST acquisition table, the header-graph
  bisection memo, the spelling caches) are memo tables: a child that misses
  recomputes an equal value. Sharing is lost, not correctness -- members
  over one header set each run their own header dump (the on-disk AST cache
  still serves a warm one), and the release-level surface reconciliation
  acquires its ASTs afresh;
* ``release_owned_checks_scope`` and other ``contextvars`` the parent set
  before the fan-out are inherited at fork, read-only;
* the policy-override warning dedup set is per process: when a dedup scope
  is active, :func:`_prime_policy_warning_dedup` loads the policy once here
  first, so every child inherits the warning as already shown;
* identity-keyed state inside a returned ``DiffResult`` would not survive
  the pickle (``id()`` keys name the child's objects): the disposition
  ledger pickles through ``policy.disposition_ledger_pickle``, which rebuilds
  its identity map from the objects themselves;
* run-wide cache counters (``cache_counters``, tracing only) count the
  parent's caches and so miss the children's work.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ..checker_types import DiffResult
    from .release_admission import MemoryAdmission
    from .release_compare_reporter import ComparedMember, ReleaseCompareReporter

__all__ = [
    "compare_members_in_processes",
    "member_processes_apply",
    "member_processes_enabled",
]

#: ``workflows.release_members.ReleaseMemberContext`` -- not imported, even
#: for typing: that module dispatches here, and the import would be a cycle.
_MemberContext = Any


def member_processes_enabled() -> bool:
    """``ABICHECK_MEMBER_PROCESSES`` (default on); ``0`` keeps members on
    threads."""
    from ..extract.env_flags import env_flag

    return env_flag("ABICHECK_MEMBER_PROCESSES")


def member_processes_apply(pool_size: int) -> bool:
    """Whether a release fan-out of *pool_size* workers runs in processes."""
    from ..process_resources import gil_enabled
    from .side_isolation import fork_is_safe

    return (
        pool_size > 1
        and member_processes_enabled()
        and gil_enabled()
        and fork_is_safe()
    )


class _BufferedTextReporter:
    """*inner*, with terminal text held for the parent to replay."""

    def __init__(self, inner: ReleaseCompareReporter) -> None:
        self.inner = inner
        self.lines: list[tuple[str, str]] = []

    def notice(self, text: str) -> None:
        self.lines.append(("notice", text))

    def warning(self, text: str) -> None:
        self.lines.append(("warning", text))

    def project(self, entry: dict[str, object], member: ComparedMember) -> None:
        self.inner.project(entry, member)

    def reproject(
        self, entry: dict[str, object], result: DiffResult, *, scope_changed: bool
    ) -> None:
        self.inner.reproject(entry, result, scope_changed=scope_changed)

    def persist(self, entry: dict[str, object], result: DiffResult) -> None:
        self.inner.persist(entry, result)

    def persist_not_comparable(
        self,
        old_path: Path,
        kind: str,
        reason: str,
        *,
        old_version: str,
        new_version: str,
    ) -> None:
        self.inner.persist_not_comparable(
            old_path, kind, reason, old_version=old_version, new_version=new_version
        )

    def settle(self, entry: dict[str, object]) -> None:
        self.inner.settle(entry)


def _prime_policy_warning_dedup(ctx: _MemberContext) -> None:
    """Surface the policy file's override warnings here, once, before any
    child forks -- see the module docstring. A load failure is left to the
    members, which record it as their own failure exactly as before."""
    from ..policy_file import dedup_scope_active

    path = ctx.request.policy_file_path
    if path is None or not dedup_scope_active():
        return
    from .compare_policy import load_suppression_and_policy

    try:
        load_suppression_and_policy(None, policy_file_path=path)
    except Exception:  # noqa: BLE001 -- reported per member, as before
        return


def compare_members_in_processes(
    compare_keys: list[str],
    ctx: _MemberContext,
    compare: Callable[[str, _MemberContext], dict[str, object]],
    *,
    max_workers: int,
    admission: MemoryAdmission | None,
    on_error: Callable[[Exception, str], dict[str, object]],
) -> list[dict[str, object]]:
    """``compare(key, ctx)`` for every key in forked children, results in
    *compare_keys* order; each child's terminal text is replayed through
    ``ctx.reporter`` afterwards, in that order."""
    # Identity-keyed state must survive the pickle back (see that module).
    from ..policy import disposition_ledger_pickle  # noqa: F401
    from .keyed_process_pool import run_keyed_in_processes

    _prime_policy_warning_dedup(ctx)
    reporter = ctx.reporter

    # Each outcome is (entry, failure, buffered text). A failure that escaped
    # the worker is carried back rather than raised, so the text the member
    # wrote before it is replayed too; `on_error` then runs here, in member
    # order, exactly as the thread path calls it.
    _Outcome = tuple[dict[str, object] | None, Exception | None, list[tuple[str, str]]]

    def _member(key: str) -> _Outcome:
        buffered = _BufferedTextReporter(reporter)
        try:
            entry = compare(key, dataclasses.replace(ctx, reporter=buffered))
        except Exception as exc:  # noqa: BLE001 -- recorded by on_error below
            return None, exc, buffered.lines
        return entry, None, buffered.lines

    def _process_failure(exc: Exception, _key: str) -> _Outcome:
        return None, exc, []

    outcomes = run_keyed_in_processes(
        compare_keys,
        _member,
        max_workers=max_workers,
        admission=admission,
        on_error=_process_failure,
    )
    entries: list[dict[str, object]] = []
    for key, (entry, failure, lines) in zip(compare_keys, outcomes, strict=True):
        for kind, text in lines:
            (reporter.notice if kind == "notice" else reporter.warning)(text)
        if failure is not None:
            entry = on_error(failure, key)
        if entry is None:
            raise RuntimeError(
                f"release member {key!r} returned neither an entry nor a failure"
            )
        entries.append(entry)
    return entries
