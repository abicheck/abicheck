"""Helpers for tests whose fixture has no type-level evidence (ADR-090).

Such a run's clean compatibility claim is reported ``UNCONFIRMED`` and exits
1 through the ``unconfirmed_compatibility`` axis alone. A test about another
axis (suppression, roll-ups, export-table reads, scope) asserts its own
answer through these, so the UNCONFIRMED axis is checked, not ignored: the
exit is 0, or exactly 1 from that one axis.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["observed_verdict", "passes_but_for_unconfirmed"]


def observed_verdict(report: Mapping[str, Any]) -> str:
    """The change-derived verdict, whether or not the run is UNCONFIRMED."""
    block = report.get("unconfirmed") or {}
    return str(block.get("observed_verdict", report["verdict"]))


def passes_but_for_unconfirmed(code: int, report: Mapping[str, Any] | None) -> bool:
    """Exit 0, or exit 1 decided by the UNCONFIRMED axis and nothing else."""
    if code == 0:
        return True
    if code != 1 or not report:
        return False
    exit_block = report.get("exit") or {}
    return list(exit_block.get("reasons", ())) == ["unconfirmed_compatibility"]
