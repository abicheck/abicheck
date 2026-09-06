# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Cross-source evolution summary section (ADR-068 D3, plan P2, schema 3.3).

Follows this package's compute/render split (see ``abicheck/report/
AGENTS.md``): :func:`compute_cross_source_evolution_summary` reads
``DiffResult.changes`` for any :class:`~abicheck.checker_types.Change`
carrying a non-``None`` ``finding_evolution`` and returns a small frozen
fact; :func:`render_cross_source_evolution_json` turns it into the JSON
block. Neither computes anything the changes list does not already state,
and neither depends on ``--format``/``--write``/demangling/any filter --
ADR-068 D4 (presentation never changes analysis).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..checker_policy import FindingEvolution
from ..checker_types import Change


@dataclass(frozen=True, slots=True)
class CrossSourceEvolutionSummary:
    """Per-state counts of ``Change.finding_evolution``-stamped findings."""

    introduced: int
    resolved: int
    persistent: int
    not_evaluated: int


def compute_cross_source_evolution_summary(
    changes: Sequence[Change],
) -> CrossSourceEvolutionSummary | None:
    """Summarize *changes* by ``finding_evolution``, or ``None`` if none carry it.

    ``None`` (never an all-zero summary) means "this run didn't opt into
    ``compare(..., cross_source_checks=True)``" -- omitted from the JSON
    report entirely, matching every other optional block's own convention.
    """
    counts = dict.fromkeys(FindingEvolution, 0)
    total = 0
    for c in changes:
        evolution = getattr(c, "finding_evolution", None)
        if evolution is None:
            continue
        counts[evolution] += 1
        total += 1
    if total == 0:
        return None
    return CrossSourceEvolutionSummary(
        introduced=counts[FindingEvolution.INTRODUCED],
        resolved=counts[FindingEvolution.RESOLVED],
        persistent=counts[FindingEvolution.PERSISTENT],
        not_evaluated=counts[FindingEvolution.NOT_EVALUATED],
    )


def render_cross_source_evolution_json(
    summary: CrossSourceEvolutionSummary | None,
) -> dict[str, int] | None:
    """Project *summary* into the JSON report's ``cross_source_evolution`` block."""
    if summary is None:
        return None
    return {
        "introduced": summary.introduced,
        "resolved": summary.resolved,
        "persistent": summary.persistent,
        "not_evaluated": summary.not_evaluated,
    }
