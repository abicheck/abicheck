# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The report projection of one release member's comparison.

:class:`ReleaseMemberEntryProjection` implements the projection half of
:class:`~abicheck.workflows.release_compare_reporter.ReleaseCompareReporter`:
the fields a member's release entry carries for a renderer -- the verdict
counts, the deployment-floor digest and coverage warnings that must survive
the ``_diff_result`` strip, the review summary (ADR-067 disposition audit,
review groups, change inventory, result counts), the recorded dependency
graph, and the disposition-ledger blocks. It prints and writes nothing; the
CLI's reporter extends it with terminal text and ``--output-dir`` files.

One place stamps these fields, for the first projection and for every
re-stamp after a release-level disposition (unreached types, lockstep
SONAME) moved a member's findings -- the re-stamps used to be two
hand-written copies of the worker's own block, each re-deriving a slightly
different subset.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..workflows.release_compare_reporter import ComparedMember, SilentReleaseReporter

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..workflows.gate import SeverityConfig

__all__ = ["ReleaseMemberEntryProjection", "stamp_member_counts"]


def stamp_member_counts(entry: dict[str, object], result: DiffResult) -> None:
    """The per-member counts, split exactly as the scalar report's own
    summary splits them (``build_summary``'s effective categories, not a raw
    kind test -- the two once disagreed on the same one-member comparison)."""
    from ..report_summary import build_summary
    from ..workflows.gate import evaluate

    summary = build_summary(result)
    classified = evaluate(result)
    entry["breaking"] = len(classified.breaking)
    entry["source_breaks"] = len(classified.source_breaks)
    entry["risk_changes"] = len(classified.risk)
    entry["compatible_additions"] = summary.compatible_additions
    entry["quality_issues"] = summary.quality_issues
    entry["informational"] = summary.informational
    from .release_public_api import stamp_member_public_api

    stamp_member_public_api(entry, result)


class ReleaseMemberEntryProjection(SilentReleaseReporter):
    """Projects member report fields; silent otherwise."""

    def __init__(self, *, severity_config: SeverityConfig | None = None) -> None:
        self.severity_config = severity_config

    def project(self, entry: dict[str, object], member: ComparedMember) -> None:
        from ..reporter import disposition_ledger_blocks
        from .release_dependency_graph import member_dependencies
        from .release_member_summary import add_member_review_summary

        compared = member.compared
        result = compared.diff
        stamp_member_counts(entry, result)
        if result.coverage_warnings:
            entry["coverage_warnings"] = list(result.coverage_warnings)
        # Survives the `_diff_result` strip; also promoted once to the
        # release envelope (`release_json_document`).
        if result.env_matrix_source_sha256 is not None:
            entry["env_matrix_source_sha256"] = result.env_matrix_source_sha256
        add_member_review_summary(entry, result, self.severity_config)
        # Release schema 1.9: the recorded DT_SONAME/DT_NEEDED facts, per side.
        if deps := member_dependencies(compared.old_snapshot, compared.new_snapshot):
            entry["dependencies"] = deps
        # ADR-067's structured half; see `reporter.disposition_ledger_blocks`.
        entry.update(disposition_ledger_blocks(result))

    def reproject(
        self, entry: dict[str, object], result: DiffResult, *, scope_changed: bool
    ) -> None:
        from ..reporter import disposition_ledger_blocks
        from .release_member_summary import add_member_review_summary

        stamp_member_counts(entry, result)
        add_member_review_summary(entry, result, self.severity_config)
        entry.update(disposition_ledger_blocks(result))


def member_report_path(output_dir: Path, entry: dict[str, object]) -> Path:
    """Where member *entry*'s complete per-library report lives under
    *output_dir* (``<old library stem>.json``)."""
    return output_dir / f"{Path(str(entry['library'])).stem}.json"
