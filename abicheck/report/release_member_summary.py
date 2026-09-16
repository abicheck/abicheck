# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Scalar-parity review facts for one release/package member."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .disposition_audit import compute_disposition_audit
from .finding import report_findings_for
from .result_counts import compute_result_counts
from .review_groups import build_review_groups

if TYPE_CHECKING:
    from ..checker_types import DiffResult


def add_member_review_summary(
    entry: dict[str, object], result: DiffResult, severity_config: object | None
) -> None:
    findings = report_findings_for(result)
    groups = build_review_groups(findings)
    audit = compute_disposition_audit(result, severity_config)
    entry["disposition_audit"] = audit.to_dict()
    entry["review_groups"] = [group.to_dict() for group in groups]
    entry["result_counts"] = compute_result_counts(
        findings,
        groups,
        audit,
        observed_changes=tuple(result.changes)
        + tuple(result.suppressed_changes)
        + tuple(result.out_of_surface_changes)
        + tuple(result.reconciled_changes),
    ).to_dict()
