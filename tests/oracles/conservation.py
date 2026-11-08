"""Invariant D: count conservation.

Every renderer's total must equal the JSON change population, and the
disposition audit must conserve: its per-disposition counts sum to the
detected total (ADR-067 D3). A finding must be counted once, not once per
renderer or once per duplicate identity.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any


def conservation_violations(
    report: Mapping[str, Any],
    rendered_totals: Mapping[str, int],
) -> list[str]:
    """Violations of invariant D for one compare report.

    *rendered_totals* maps a renderer name (``"sarif"``, ``"oneline"``, ...)
    to the finding total that renderer printed.
    """
    changes = list(report.get("changes") or [])
    population = len(changes)
    problems = [
        f"{name} totals {total}, JSON lists {population}"
        for name, total in sorted(rendered_totals.items())
        if total != population
    ]

    audit = report.get("disposition_audit")
    if isinstance(audit, Mapping):
        counts = audit.get("counts") or {}
        detected = audit.get("detected_total")
        summed = sum(counts.values())
        if detected is not None and summed != detected:
            problems.append(
                f"disposition_audit counts sum to {summed}, detected_total is {detected}"
            )

    keys = Counter(_identity(change) for change in changes)
    problems.extend(
        f"finding counted more than once: {key}"
        for key, count in sorted(keys.items())
        if count > 1
    )
    return problems


def _identity(change: Mapping[str, Any]) -> str:
    """A finding's identity: its canonical id when present, else kind and symbol."""
    if change.get("id"):
        return str(change["id"])
    return f"{change.get('kind')}:{change.get('symbol')}"
