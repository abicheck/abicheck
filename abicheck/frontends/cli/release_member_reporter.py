# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The CLI's reporter for a directory/package ``compare``.

:class:`CliReleaseReporter` is the report projection
(:class:`~abicheck.report.release_member_entry.ReleaseMemberEntryProjection`)
plus what only the terminal-owning front end does while
:func:`~abicheck.workflows.release_compare.execute_release_compare` runs:

* stderr notices, and run warnings unless the primary format is JSON (a
  JSON report carries them in its own ``warnings`` array);
* each member's disposition text -- pattern-verdict modulations (plan slice
  7o: disclosure is unconditional, ADR-067), the suppression audit, and the
  filtered-surface ledger -- held on the entry by the worker and echoed by
  :meth:`settle` in member order, so parallel workers never interleave;
* ``--output-dir``: each member's complete, *unfiltered* per-library report
  (the "always full" escape hatch a truncated aggregate report points to --
  ``--view show=`` is deliberately not applied), and a ``not_comparable``
  member's own document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import click

from ...report.release_member_entry import (
    ReleaseMemberEntryProjection,
    member_report_path,
)
from .runtime import _safe_write_output

if TYPE_CHECKING:
    from ...checker_types import DiffResult
    from ...workflows.gate import SeverityConfig
    from ...workflows.release_compare_reporter import ComparedMember

__all__ = ["CliReleaseReporter"]

#: The held-back per-member texts, in the order :meth:`settle` echoes them.
_HELD_TEXT_KEYS = (
    "_pattern_modulations_text",
    "_suppression_audit_text",
    "_scope_ledger_text",
)


def _scope_ledger_text(
    library: str, result: DiffResult, *, contract_evaluation: bool
) -> str:
    from ...cli_audit import ledger_lines_for

    count = result.out_of_surface_count
    return "\n".join(
        [
            f"\nFiltered as non-public ABI surface in {library} ({count} "
            f"{'finding' if count == 1 else 'findings'}):",
            *ledger_lines_for(
                result.out_of_surface_changes,
                contract_evaluation=contract_evaluation,
            ),
        ]
    )


class CliReleaseReporter(ReleaseMemberEntryProjection):
    """Projection + stderr + ``--output-dir`` for one release run."""

    def __init__(
        self,
        *,
        severity_config: SeverityConfig | None = None,
        require_complete_analysis: bool = False,
        contract_evaluation: bool = False,
        output_dir: Path | None = None,
        echo_warnings: bool = True,
    ) -> None:
        super().__init__(severity_config=severity_config)
        self.require_complete_analysis = require_complete_analysis
        self.contract_evaluation = contract_evaluation
        self.output_dir = output_dir
        self.echo_warnings = echo_warnings

    def notice(self, text: str) -> None:
        click.echo(text, err=True)

    def warning(self, text: str) -> None:
        if self.echo_warnings:
            click.echo(text, err=True)

    def project(self, entry: dict[str, object], member: ComparedMember) -> None:
        super().project(entry, member)
        result = member.compared.diff
        name = member.old_path.name
        if result.pattern_modulations:
            from ...cli_audit import render_pattern_modulations

            entry["_pattern_modulations_text"] = (
                f"\n== {name} ==\n{render_pattern_modulations(result)}"
            )
        # ADR-067: a passing release report may not hide which breaking
        # findings a rule disposed of.
        if result.suppression_audit is not None:
            from ...cli_compare_fold import _fold_suppression_audit_into_text

            section = _fold_suppression_audit_into_text(
                "", "markdown", result.suppression_audit, demangle=True
            )
            if section.strip():
                entry["_suppression_audit_text"] = f"\n### {name}{section}"
        # ADR-067: a count alone does not say *what* was excluded or why.
        if member.scoped and result.out_of_surface_changes:
            entry["_scope_ledger_text"] = _scope_ledger_text(
                name, result, contract_evaluation=self.contract_evaluation
            )

    def reproject(
        self, entry: dict[str, object], result: DiffResult, *, scope_changed: bool
    ) -> None:
        super().reproject(entry, result, scope_changed=scope_changed)
        if scope_changed:
            entry["_scope_ledger_text"] = _scope_ledger_text(
                str(entry.get("library", "")),
                result,
                contract_evaluation=self.contract_evaluation,
            )

    def persist(self, entry: dict[str, object], result: DiffResult) -> None:
        if self.output_dir is None:
            return
        from ...reporter import to_json

        path = member_report_path(self.output_dir, entry)
        _safe_write_output(
            path,
            # ADR-071: the member's own `exit` block follows the release's
            # severity scheme and assurance setting.
            to_json(
                result,
                severity_config=self.severity_config,
                require_complete_analysis=self.require_complete_analysis,
            ),
        )
        # The unambiguous index a truncated machine document owes its
        # reader: this member's complete, uncapped report, by path.
        entry["complete_report"] = str(path)

    def persist_not_comparable(
        self,
        old_path: Path,
        kind: str,
        reason: str,
        *,
        old_version: str,
        new_version: str,
    ) -> None:
        if self.output_dir is None:
            return
        from ...report.not_comparable import (
            OperationalStatus,
            not_comparable_document,
        )
        from ...schemas import REPORT_SCHEMA_VERSION

        doc = not_comparable_document(
            old_path.name,
            old_version,
            new_version,
            kind,
            reason,
            report_schema_version=REPORT_SCHEMA_VERSION,
            operational=OperationalStatus.NOT_COMPARABLE,
        ).to_mapping()
        _safe_write_output(
            self.output_dir / f"{old_path.stem}.json", json.dumps(doc, indent=2)
        )

    def settle(self, entry: dict[str, object]) -> None:
        for key in _HELD_TEXT_KEYS:
            text = entry.pop(key, None)
            if text is not None:
                click.echo(text, err=True)
