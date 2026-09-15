# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Compact review facts that do not belong in the legacy report monolith."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .review_groups import build_review_groups

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..checker_types import DiffResult
    from .finding import ReportFinding


def review_groups_for(
    findings: Sequence[ReportFinding],
) -> tuple[dict[str, object], ...]:
    return tuple(group.to_dict() for group in build_review_groups(findings))


def compact_coverage_warnings(warnings: Sequence[str]) -> tuple[str, ...]:
    """Keep material limitations; routine applicability stays in detail output."""
    material = []
    for warning in warnings:
        lowered = warning.lower()
        if warning.startswith("Detector '") and not any(
            word in lowered for word in ("failed", "required", "asymmetric")
        ):
            continue
        material.append(warning)
    return tuple(material[:6])


def compact_evidence_summary(result: DiffResult) -> str:
    rows = getattr(result, "layer_coverage", ()) or ()
    present = {
        str(row.get("layer"))
        for row in rows
        if isinstance(row, dict) and row.get("status") == "present"
    }
    names = []
    if "L0" in present:
        names.append("binary exports")
    if "L2" in present:
        names.append("public headers/signatures")
    if "L1" in present:
        names.append("debug-derived compiled layout")
    if not names:
        names.append("recorded snapshot facts")
    limits = []
    if "L1" not in present:
        limits.append("no debug-derived layout verification")
    if "L4_source_abi" not in present:
        limits.append("no source replay")
    return f"{', '.join(names)}; {', '.join(limits)}"
