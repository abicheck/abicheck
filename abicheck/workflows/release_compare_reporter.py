# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The port a release compare reports through while it runs.

:func:`abicheck.workflows.release_compare.execute_release_compare` owns the
release: member pairing and iteration, each member's comparison, the
release-level dispositions, and the exit-axis folds. Three things it must
*cause* without owning them, because ``workflows`` may import neither
``report`` nor a frontend:

* the per-member report projection -- the counts, review summary,
  dependency graph and disposition-ledger blocks a member's release entry
  carries, stamped while the member's full :class:`~abicheck.checker_types.
  DiffResult` and snapshots are still alive (a worker thread compacts them
  right after), and re-stamped when a release-level disposition moves that
  member's findings;
* the member's complete per-library report (``--output-dir``);
* terminal text: progress notices and run warnings.

:class:`ReleaseCompareReporter` names those hooks. The report layer
implements the projection (``report.release_member_entry``); the CLI extends
it with its terminal and ``--output-dir`` behavior
(``frontends.cli.release_member_reporter``). :class:`SilentReleaseReporter`
is the no-op a caller that wants only the workflow's own facts uses -- a
member entry then carries the fields every release-level fold reads and
nothing a renderer alone needs.

**Entry key order is part of the published bytes** (the release JSON
emits each member entry as built). The hooks are called at the points the
keys they own have always been written, so splitting the owner did not
reorder a rendered report: :meth:`~ReleaseCompareReporter.project` right
after ``library``/``verdict``, :meth:`~ReleaseCompareReporter.persist` last.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from .contracts import CompareResult

__all__ = [
    "ComparedMember",
    "ReleaseCompareReporter",
    "SilentReleaseReporter",
]


@dataclass(frozen=True)
class ComparedMember:
    """One member's completed comparison, as the projection hook sees it.

    *scoped* is whether the release scoped members to the public surface
    (the release request's ``scope_to_public_surface``), which decides
    whether a filtered-surface ledger exists to report.
    """

    key: str
    old_path: Path
    compared: CompareResult
    scoped: bool


class ReleaseCompareReporter(Protocol):
    """What a release compare calls out to while it runs.

    :meth:`project` and :meth:`persist` run inside the member's worker
    thread and must not write to a terminal (sibling workers would
    interleave); text meant for one goes on the entry under a private
    ``_``-prefixed key and is written by :meth:`settle`, which the workflow
    calls once per member, in member order, after every worker finished.
    """

    def notice(self, text: str) -> None:
        """A progress or diagnostic line that is always shown."""

    def warning(self, text: str) -> None:
        """A run warning; the workflow also records it in the outcome's
        ``warnings``, so a machine-readable render may choose not to echo it."""

    def project(self, entry: dict[str, object], member: ComparedMember) -> None:
        """Stamp a just-compared member's report fields onto *entry*."""

    def reproject(
        self, entry: dict[str, object], result: DiffResult, *, scope_changed: bool
    ) -> None:
        """Re-stamp *entry*'s report fields after a release-level disposition
        changed *result* (findings moved off the surface when
        *scope_changed*, otherwise suppressed)."""

    def persist(self, entry: dict[str, object], result: DiffResult) -> None:
        """Record the member's complete report wherever this run keeps one."""

    def persist_not_comparable(
        self,
        old_path: Path,
        kind: str,
        reason: str,
        *,
        old_version: str,
        new_version: str,
    ) -> None:
        """Record a member that could not be compared under one contract."""

    def settle(self, entry: dict[str, object]) -> None:
        """Release whatever per-member text :meth:`project` held back."""


class SilentReleaseReporter:
    """A :class:`ReleaseCompareReporter` that projects, persists and prints
    nothing: member entries carry the workflow's own facts only."""

    def notice(self, text: str) -> None:
        pass

    def warning(self, text: str) -> None:
        pass

    def project(self, entry: dict[str, object], member: ComparedMember) -> None:
        pass

    def reproject(
        self, entry: dict[str, object], result: DiffResult, *, scope_changed: bool
    ) -> None:
        pass

    def persist(self, entry: dict[str, object], result: DiffResult) -> None:
        pass

    def persist_not_comparable(
        self,
        old_path: Path,
        kind: str,
        reason: str,
        *,
        old_version: str,
        new_version: str,
    ) -> None:
        pass

    def settle(self, entry: dict[str, object]) -> None:
        pass
