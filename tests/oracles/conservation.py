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
        problems.extend(_audit_violations(audit))

    keys = Counter(_identity(change) for change in changes)
    problems.extend(
        f"finding counted more than once: {key}"
        for key, count in sorted(keys.items())
        if count > 1
    )
    return problems


def _audit_violations(audit: Mapping[str, Any]) -> list[str]:
    """Malformed audit fields are violations; the sum is compared only when they are well-formed."""
    counts = audit.get("counts")
    detected = audit.get("detected_total")
    if not isinstance(counts, Mapping):
        return ["disposition_audit.counts is missing or not a mapping"]
    if not isinstance(detected, int) or isinstance(detected, bool):
        return [
            f"disposition_audit.detected_total is missing or not an integer: {detected!r}"
        ]
    bad = sorted(
        k for k, v in counts.items() if not isinstance(v, int) or isinstance(v, bool)
    )
    if bad:
        return [f"disposition_audit.counts has non-integer values for {bad}"]
    summed = sum(counts.values())
    if summed != detected:
        return [
            f"disposition_audit counts sum to {summed}, detected_total is {detected}"
        ]
    return []


def _identity(change: Mapping[str, Any]) -> str:
    """A finding's per-finding identity, as the JSON serializer emits it.

    ``finding_id`` is the per-finding key (``change_json`` writes it on every
    change). ``id`` and kind-and-symbol are fallbacks for synthetic reports
    that carry neither. ``canonical_finding_id`` is deliberately not used: two
    distinct findings on one symbol can share it.
    """
    for key in ("finding_id", "id"):
        if change.get(key):
            return str(change[key])
    return f"{change.get('kind')}:{change.get('symbol')}"
