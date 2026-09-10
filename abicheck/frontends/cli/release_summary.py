# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The release fan-out's ``--output-dir`` ``summary.json`` sidecar writer.

Moved out of ``cli_compare_release_matrix.py`` (at the 800-line production
cap) when ADR-065 S2 gave the sidecar the same ``comparison_scope`` block
and scope-aware ``exit``/``run_outcome`` the primary release JSON carries
(``cli_compare_release_helpers._format_release_json``); that module still
re-exports the name for every pre-existing importer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ...bundle import BundleDiffResult
from ...checker import DiffResult
from ...report.comparison_scope import ComparisonScopeTerms, comparison_scope_terms
from .options.params import DEFAULT_POLICY_PROFILE

if TYPE_CHECKING:
    from ...workflows.gate import SeverityConfig

__all__ = ["_write_release_summary_file"]


def _write_release_summary_file(
    output_dir: Path,
    worst_verdict: str,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    severity_config: SeverityConfig | None = None,
    fail_on_removed: bool = False,
    severity_exit_code: int | None = None,
    contract_coverage_exit_contribution: int = 0,
    bundle_result: BundleDiffResult | None = None,
    matrix_result: DiffResult | None = None,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | None = None,
    pack_application: Any = None,
    scope_public_headers: bool = True,
    scope_terms: ComparisonScopeTerms | None = None,
    write_output: Callable[[Path, str], None] | None = None,
) -> None:
    """Write per-library summary JSON to output directory.

    *scope_terms* (ADR-065 S2) is the same resolved
    :class:`~abicheck.report.comparison_scope.ComparisonScopeTerms` the
    primary report used, so this sidecar's ``exit``/``run_outcome``/
    ``comparison_scope`` cannot drift from it. *pack_application* is the
    release's ``PackApplication`` (typed loosely: ``abicheck.pack_
    application`` is not yet ADR-061-classified, and this migrated module
    may not import it). *write_output* is the caller's own writer
    (``frontends.cli.runtime._safe_write_output`` in production) -- taken
    as a parameter so this module stays outside the CLI-registration import
    cycle that module belongs to; ``Path.write_text`` when omitted.

    *severity_config*, when in effect, feeds the same effective-config
    digest ``_format_release_json`` (the primary release report) already
    stamps, via the one shared helper ``_release_summary_effective_config_
    block`` so the two can never independently drift (Codex review, PR
    #803). Also gains the same ``exit`` block that report does, via the
    same resolver (ADR-064 stage 1b, Codex review). *policy*/
    *policy_file_path*/*suppress*/*pack_application*/*scope_public_headers*
    (P1, CLI-audit) are the release's own resolved policy/surface inputs,
    forwarded so this sidecar's ``effective_config_fields`` reflects the
    real configuration every library was compared under, same as the
    primary report (see ``_release_summary_effective_config_block``'s own
    docstring).
    """
    from ...cli_compare_receipt import _release_summary_effective_config_block
    from ...cli_compare_release_helpers import (
        _release_completed_compatibility_verdict,
        _release_findings_for_render,
        _release_global_verdict,
    )
    from ...report.not_comparable import run_outcome_dict_for_release
    from ...workflows.gate import (
        resolve_release_exit_decision_for_report,
        resolve_scope_decision,
    )
    from ...workflows.release_scope import release_global_ran, unmatched_names

    terms = (
        scope_terms
        if scope_terms is not None
        else comparison_scope_terms(resolve_scope_decision(None, None))
    )

    digest, fields = _release_summary_effective_config_block(
        severity_config,
        policy=policy,
        policy_file_path=policy_file_path,
        suppress=suppress,
        pack_application=pack_application,
        scope_public_headers=scope_public_headers,
        on_incomplete_scope=terms.policy,
        fail_on_removed_library=fail_on_removed,
    )
    release_global_verdict = _release_global_verdict(bundle_result, matrix_result)
    exit_dict = resolve_release_exit_decision_for_report(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution,
        library_results,
        release_global_verdict,
        incomplete_scope_contribution=terms.decision.incomplete_scope_exit_contribution,
        no_comparison_completed_contribution=terms.decision.no_comparison_completed_exit_contribution,
    ).to_dict()
    record = terms.record
    # This sidecar is documented/contracted to always be full and
    # unfiltered, mirroring a secondary `--write`'s own contract -- so the
    # projection below is computed with `show_only=None` regardless of
    # whether the primary render was filtered (`_release_findings_for_
    # render`'s existing no-op-when-unfiltered behaviour), which also
    # strips the private `findings_view`/`findings_total_count`/
    # `findings_total_count_view`/`impact_table_view` accounting keys
    # `_strip_diff_results_and_adjust_verdict` stashes on each entry --
    # without this, the raw `library_results` list (never otherwise
    # cleaned for this sidecar) leaked those internal keys straight into
    # `summary.json`'s per-library entries (Codex review, fresh evidence,
    # discovered while fixing the adjacent "uncapped release filter
    # totals" finding).
    display_library_results = _release_findings_for_render(library_results, None)
    summary_data: dict[str, object] = {
        "verdict": worst_verdict,
        "libraries": display_library_results,
        # ADR-065 S4: read off the acquisition record, never off the deleted
        # set difference; a driver with no record reports nothing here.
        "unmatched_old": unmatched_names(record, side="old") if record else [],
        "unmatched_new": unmatched_names(record, side="new") if record else [],
        "effective_config_digest": digest,
        "effective_config_fields": fields,
        "exit": exit_dict,
        "run_outcome": run_outcome_dict_for_release(
            _release_completed_compatibility_verdict(
                library_results,
                release_global_verdict,
                release_global_ran=release_global_ran(
                    bundle_result, matrix_result, record
                ),
            ),
            exit_dict,
            scope=terms.completeness,
        ),
    }
    if terms.section is not None:
        summary_data["comparison_scope"] = terms.section
    # ADR-067 C-S2: the same folded release-level `disposition_audit` the
    # primary report carries (`cli_compare_release_helpers._format_release_
    # json`), via the identical shared helper, so this sidecar cannot drift
    # from it.
    from ...cli_compare_receipt import release_disposition_audit_block

    summary_data["disposition_audit"] = release_disposition_audit_block(
        library_results, matrix_result, severity_config, bundle_result
    )
    summary_path = output_dir / "summary.json"
    writer = write_output if write_output is not None else _write_text
    writer(summary_path, json.dumps(summary_data, indent=2))
    click.echo(f"Per-library reports written to {output_dir}/", err=True)


def _write_text(path: Path, text: str) -> None:
    """Write *text* through the caller-supplied writer (keeps this module out of the CLI import cycle)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
