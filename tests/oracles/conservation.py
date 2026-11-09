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

    if "disposition_audit" in report and report["disposition_audit"] is not None:
        audit = report["disposition_audit"]
        if isinstance(audit, Mapping):
            problems.extend(_audit_violations(audit))
        else:
            problems.append(
                f"disposition_audit is not a mapping: {type(audit).__name__}"
            )

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
    negative = sorted(k for k, v in counts.items() if v < 0)
    if negative or detected < 0:
        return [
            "disposition_audit has negative values "
            f"(counts {negative}, detected_total {detected})"
        ]
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


def rendered_audit_violations(
    report: Mapping[str, Any],
    rendered: Mapping[str, Mapping[str, int]],
) -> list[str]:
    """Rendered detected and gating totals that differ from the JSON audit.

    *rendered* maps a renderer name to the totals it printed, keyed
    ``"detected"`` (the audit's ``detected_total``) and ``"gating"`` (its
    ``effective_total``). A renderer may print only one of the two.
    """
    audit = report.get("disposition_audit")
    if not isinstance(audit, Mapping):
        return ["disposition_audit is missing, so rendered totals have no reference"]
    expected = {
        "detected": audit.get("detected_total"),
        "gating": audit.get("effective_total"),
    }
    return [
        f"{name} prints {field} {value}, JSON audit says {expected[field]}"
        for name, totals in sorted(rendered.items())
        for field, value in sorted(totals.items())
        if value != expected.get(field)
    ]


def disposition_violations(report: Mapping[str, Any]) -> list[str]:
    """The audit's gating total disagrees with the change list it summarizes.

    ``effective_total`` is the gating subset (schema) and must equal
    ``counts.gating``. Each change in ``changes`` states its own
    ``gate_contribution``; the changes that contributed to the gate are the
    retained gating ones. Their number is ``result_counts.gating_retained``,
    and never more than ``effective_total``, which may exceed it by a
    redundant finding folded out of ``changes`` but still scored
    (``held_out_gating``). When ``result_counts`` is present, its ``gating``
    is ``effective_total`` and its ``retained`` is the length of ``changes``.

    Judge a full report: a ``--show-only`` view filters ``changes`` and is not
    expected to satisfy this.
    """
    audit = report.get("disposition_audit")
    if not isinstance(audit, Mapping) or not isinstance(audit.get("counts"), Mapping):
        return ["disposition_audit or its counts is missing"]
    effective = audit.get("effective_total")
    problems: list[str] = []
    if effective != audit["counts"].get("gating"):
        problems.append(
            f"effective_total {effective} != counts.gating {audit['counts'].get('gating')}"
        )
    changes = list(report.get("changes") or [])
    missing = [c.get("kind") for c in changes if "gate_contribution" not in c]
    if missing:
        problems.append(f"changes without gate_contribution: {missing}")
    counts = report.get("result_counts")
    if not missing:
        gating = sum(1 for c in changes if c["gate_contribution"] > 0)
        if isinstance(effective, int) and gating > effective:
            problems.append(
                f"{gating} changes contribute to the gate, more than effective_total {effective}"
            )
        if isinstance(counts, Mapping) and "gating_retained" in counts:
            if gating != counts["gating_retained"]:
                problems.append(
                    f"{gating} changes contribute to the gate, "
                    f"result_counts.gating_retained is {counts['gating_retained']}"
                )
    if isinstance(counts, Mapping):
        if counts.get("gating") != effective:
            problems.append(
                f"result_counts.gating {counts.get('gating')} != effective_total {effective}"
            )
        if counts.get("retained") != len(changes):
            problems.append(
                f"result_counts.retained {counts.get('retained')} != {len(changes)} changes listed"
            )
    return problems
