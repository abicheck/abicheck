# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Scalar-parity review facts for one release/package member.

Every fact here is stamped by one function called at *both* points the
release fan-out writes a member entry -- the initial comparison and the
post-processing refresh that can still add or reclassify findings -- so a
member's blocks can never be left stale by whichever pass ran last. That
is why a new scalar-parity block belongs here rather than beside either
caller's own hand-projected counters: ``summary.change_inventory`` existed
in the scalar ``compare`` report for several releases while the release
path, which already computed the identical ``build_summary(result)``,
copied two of its fields and dropped the inventory -- a discrepancy no
test caught because the scalar module's own tests never reach this
projection boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .change_inventory import render_change_inventory_json
from .disposition_audit import compute_disposition_audit
from .finding import report_findings_for
from .result_counts import compute_result_counts
from .review_groups import build_review_groups

if TYPE_CHECKING:
    from ..checker_types import DiffResult


def add_member_review_summary(
    entry: dict[str, object], result: DiffResult, severity_config: object | None
) -> None:
    from ..report_summary import build_summary

    findings = report_findings_for(result)
    groups = build_review_groups(findings)
    audit = compute_disposition_audit(result, severity_config)
    entry["disposition_audit"] = audit.to_dict()
    entry["review_groups"] = [group.to_dict() for group in groups]
    # The identical split the scalar report's `summary.change_inventory`
    # carries, from the identical computation (`build_summary`'s own
    # `inventory`) -- propagated, never recomputed from kind names, so a
    # per-member block and a scalar report over the same pair cannot
    # disagree. Stamped from the finalized `result`, so release-level
    # postprocessing that appends or reclassifies findings is reflected.
    entry["change_inventory"] = render_change_inventory_json(
        build_summary(result).inventory
    )
    entry["result_counts"] = compute_result_counts(
        findings,
        groups,
        audit,
        observed_changes=tuple(result.changes)
        + tuple(result.suppressed_changes)
        + tuple(result.out_of_surface_changes)
        + tuple(result.reconciled_changes),
    ).to_dict()
