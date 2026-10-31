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

"""The release fan-out's output writers: the ``--output-dir`` ``summary.json``
sidecar (:func:`_write_release_summary_file`) and the primary-format output
finalization (:func:`_finalize_release_output`).

The sidecar gives the release the same ``comparison_scope`` block and
scope-aware ``exit``/``run_outcome`` the primary release JSON carries
(``report.release_document.release_json_document``). Finalization was merged
in from the retired ``cli_compare_release_matrix`` module, which held nothing
else of substance: the release's pairing and comparison are
:mod:`abicheck.workflows.release_compare`.
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
from ...report.release_assurance_section import ReleaseAssuranceTerms
from ...workflows.gate import (
    fold_release_outcome,
    incomplete_scope_diagnostic,
    release_global_verdicts,
)
from ...workflows.release_exit_fold import release_compatibility_base_exit
from .options.params import DEFAULT_POLICY_PROFILE
from .release_report import render_release_summary
from .runtime import _exit_compare_release, _safe_write_output, _write_or_echo

if TYPE_CHECKING:
    from ...model.symbol_inventory import SymbolInventory
    from ...pack_application import PackApplication
    from ...report.release_public_surface_section import ReleasePublicSurfaceTerms
    from ...workflows.gate import SeverityConfig

__all__ = ["_finalize_release_output", "_write_release_summary_file"]

#: Release formats rendered from the per-member DiffResults (``diff_pairs``):
#: JUnit suites and SARIF runs each come from one member's own comparison.
_PAIR_FORMATS = frozenset({"junit", "sarif"})


def _write_release_summary_file(
    output_dir: Path,
    worst_verdict: str,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    severity_config: SeverityConfig | None = None,
    fail_on_removed: bool = False,
    severity_exit_code: int | None = None,
    contract_coverage_exit_contribution: int = 0,
    bundle_result: BundleDiffResult | None = None,
    matrix_result: DiffResult | None = None,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | tuple[Path, ...] | None = None,
    pack_application: Any = None,
    scope_public_headers: bool = True,
    scope_terms: ComparisonScopeTerms | None = None,
    assurance_terms: ReleaseAssuranceTerms | None = None,
    write_output: Callable[[Path, str], None] | None = None,
    env_matrix_source_sha256: str | None = None,
    require_complete_analysis: bool = False,
    excluded_header_patterns: str = "",
    public_surface: ReleasePublicSurfaceTerms | None = None,
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
    digest ``release_json_document`` (the primary release report) already
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

    *env_matrix_source_sha256* (Codex review, P2 follow-up): the same
    release-wide deployment-floor digest the primary release JSON's own
    envelope field carries, computed once by the caller at release scope --
    this sidecar previously carried neither that top-level field nor a
    real value for ``effective_config_fields["policy.env_matrix"]``.
    """
    from ...cli_compare_receipt import _release_summary_effective_config_block
    from ...report.not_comparable import run_outcome_dict_for_release
    from ...report.release_document import release_findings_for_render
    from ...workflows.gate import (
        fold_release_outcome,
        release_global_verdicts,
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
        env_matrix_source_sha256=env_matrix_source_sha256,
        require_complete_analysis=require_complete_analysis,
        excluded_header_patterns=excluded_header_patterns,
    )
    outcome = fold_release_outcome(
        (str(entry.get("verdict", "NO_CHANGE")) for entry in library_results),
        release_global_verdicts(bundle_result, matrix_result),
        global_ran=release_global_ran(bundle_result, matrix_result, terms.record),
    )
    release_global_verdict = outcome.global_verdict
    # `require_complete_analysis` reaches this sidecar's own decision too,
    # not only the process exit -- the same reason the release JSON threads
    # it (Codex review, PR #1238, P1): this file is what a consumer reads
    # when `--output-dir` is the artifact it collects, and it must not report
    # `exit.code: 0` for a run that exited `1`. It is also exactly the shape
    # of the bug the evidence-contract axis already hit here once, which is
    # why `release_evidence_contract_contribution` is derived inside the
    # resolver rather than passed in.
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
        require_complete_analysis=require_complete_analysis,
    ).to_dict()
    record = terms.record
    # This sidecar is documented/contracted to always be full and
    # unfiltered, mirroring a secondary `-o`'s own contract -- so the
    # projection below is computed with `show_only=None` regardless of
    # whether the primary render was filtered (`_release_findings_for_
    # render`'s existing no-op-when-unfiltered behaviour), which also
    # strips the private `findings_view`/`findings_total_count`/
    # `findings_total_count_view`/`impact_table_view` accounting keys
    # `project_release_library_results` stashes on each entry --
    # without this, the raw `library_results` list (never otherwise
    # cleaned for this sidecar) leaked those internal keys straight into
    # `summary.json`'s per-library entries (Codex review, fresh evidence,
    # discovered while fixing the adjacent "uncapped release filter
    # totals" finding).
    display_library_results = release_findings_for_render(library_results, None)
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
            outcome.compatibility,
            exit_dict,
            scope=terms.completeness,
        ),
    }
    if env_matrix_source_sha256 is not None:
        summary_data["env_matrix_source_sha256"] = env_matrix_source_sha256
    # The same release contract the primary report states. A consumer
    # collecting `--output-dir` as the artifact reads this file instead of
    # that one, so omitting the product's own contract here left exactly
    # the per-member reading the block exists to replace (CodeRabbit
    # review) -- the same drift the `comparison_scope`/`analysis_assurance`
    # blocks below are threaded through for.
    # Emitted whenever a reconciliation exists, *including* an unevaluated
    # one: `evaluated` goes false exactly when neither side's surface
    # resolved, and that is a stated fact (the section carries the reason),
    # not an absence. Gating on it left a `--output-dir` consumer unable to
    # tell an unresolved product contract from no product contract -- the
    # same "a failed extractor read as silence" inversion the Markdown
    # renderer deliberately refuses (CodeRabbit review).
    if public_surface is not None:
        summary_data["public_surface_reconciliation"] = public_surface.to_dict()
    if terms.section is not None:
        summary_data["comparison_scope"] = terms.section
    # ADR-071 D6: the same fold section and the same canonical top-level
    # gate key the primary release JSON carries. This sidecar is what a
    # consumer collects when `--output-dir` is the artifact, so leaving the
    # axis only inside `exit` here would be the same gate-loses-its-reason
    # gap the top-level key exists to close for the primary document.
    if assurance_terms is not None and assurance_terms.section is not None:
        summary_data["analysis_assurance"] = assurance_terms.section
        summary_data["analysis_assurance_exit_contribution"] = (
            assurance_terms.decision.exit_contribution
        )
    # ADR-067 C-S2: the same folded release-level `disposition_audit` the
    # primary report carries (`report.release_document.
    # release_json_document`), via the identical shared helper, so this sidecar cannot drift
    # from it.
    from ...report.release_document import release_disposition_audit_block

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


def _finalize_release_output(
    fmt: str,
    worst_verdict: str,
    old_dir: Path,
    new_dir: Path,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    warning_msgs: list[str],
    diff_pairs: list[tuple[DiffResult, SymbolInventory]],
    bundle_result: BundleDiffResult | None,
    output: Path | None,
    output_dir: Path | None,
    fail_on_removed: bool,
    matrix_result: DiffResult | None = None,
    severity_exit_code: int | None = None,
    severity_config: SeverityConfig | None = None,
    contract_coverage_exit_contribution: int = 0,
    contract_coverage_failure_count: int = 0,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | tuple[Path, ...] | None = None,
    pack_application: PackApplication | None = None,
    scope_public_headers: bool = True,
    scope_terms: ComparisonScopeTerms | None = None,
    # Both of these come from one origin -- `compare_release_cmd` resolves
    # `assurance_terms` *from* `require_complete_analysis` over the same
    # `library_results` -- so they cannot state different settings. The flag
    # is what the exit resolver and the receipt read; the terms are what the
    # report section and the stderr notice are rendered from (ADR-071 D5/D6).
    assurance_terms: ReleaseAssuranceTerms | None = None,
    demangle: bool = False,
    show_only: str | None = None,
    env_matrix_source_sha256: str | None = None,
    require_complete_analysis: bool = False,
    excluded_header_patterns: str = "",
    public_surface: ReleasePublicSurfaceTerms | None = None,
) -> None:
    """Write summary output, step summary, per-library dir report, then exit.

    *scope_terms* (ADR-065 S2) is the release's one resolved
    :class:`~abicheck.report.comparison_scope.ComparisonScopeTerms`: every
    rendered format, the ``--output-dir`` sidecar, the stderr notice, and
    the real process exit read the same object. *removed_keys*/*added_keys*
    are the **proven** sets (D2) by the time they reach here.

    *show_only* (Codex review, PR #1154 second follow-up): this function is
    only ever called for the **primary** ``--format`` render (the secondary
    ``-o`` render is a separate call in ``cli_compare_release.py`` that
    never passes it), so it is the one caller allowed to forward the
    release's ``output.view show=`` selection into the rendered text.
    """
    text = render_release_summary(
        fmt,
        worst_verdict,
        old_dir,
        new_dir,
        library_results,
        removed_keys,
        added_keys,
        old_map,
        new_map,
        warning_msgs,
        diff_pairs=diff_pairs if fmt in _PAIR_FORMATS else None,
        bundle_result=bundle_result,
        matrix_result=matrix_result,
        severity_config=severity_config,
        severity_exit_code=severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
        contract_coverage_failure_count=contract_coverage_failure_count,
        fail_on_removed=fail_on_removed,
        policy=policy,
        policy_file_path=policy_file_path,
        suppress=suppress,
        pack_application=pack_application,
        scope_public_headers=scope_public_headers,
        scope_terms=scope_terms,
        assurance_terms=assurance_terms,
        demangle=demangle,
        show_only=show_only,
        env_matrix_source_sha256=env_matrix_source_sha256,
        require_complete_analysis=require_complete_analysis,
        excluded_header_patterns=excluded_header_patterns,
        public_surface=public_surface,
    )
    _write_or_echo(output, text)

    # CLI cleanup phase two, PR E removed --annotate/--annotate-additions,
    # the flag that used to gate a $GITHUB_STEP_SUMMARY write here. This no
    # longer writes one at all -- see cli.py's _finalize_compare_result's
    # own, longer comment for why "unconditional in CI" (an earlier
    # revision of this comment) was itself a real regression through the
    # composite Action, which already writes its own job summary.

    if output_dir:
        _write_release_summary_file(
            output_dir,
            worst_verdict,
            library_results,
            removed_keys,
            severity_config=severity_config,
            fail_on_removed=fail_on_removed,
            severity_exit_code=severity_exit_code,
            contract_coverage_exit_contribution=contract_coverage_exit_contribution,
            bundle_result=bundle_result,
            matrix_result=matrix_result,
            policy=policy,
            policy_file_path=policy_file_path,
            suppress=suppress,
            pack_application=pack_application,
            scope_public_headers=scope_public_headers,
            scope_terms=scope_terms,
            assurance_terms=assurance_terms,
            write_output=_safe_write_output,
            env_matrix_source_sha256=env_matrix_source_sha256,
            require_complete_analysis=require_complete_analysis,
            excluded_header_patterns=excluded_header_patterns,
            public_surface=public_surface,
        )

    # ADR-065 D6/D7, the completeness axis's own stderr notice -- the same
    # reason the contract-coverage notice below exists: a Markdown/JUnit
    # consumer, or `action/run.sh`'s stderr fallback, must still learn why
    # a run was (or, under `warn`, was not) floored, and that a zero-pair
    # run is never a clean pass. JSON carries the full `comparison_scope`.
    if scope_terms is not None and scope_terms.record is not None and fmt != "json":
        scope_notice = incomplete_scope_diagnostic(
            scope_terms.record,
            scope_terms.policy,
            base_exit=release_compatibility_base_exit(
                worst_verdict, severity_exit_code
            ),
        )
        if scope_notice is not None:
            click.echo(scope_notice, err=True)

    # ADR-049 Phase 7's orthogonal contract-coverage axis, release/package
    # parity (CLI-audit P1, Codex review): a single-pair `compare` announces
    # a stderr notice whenever the rendered format doesn't already carry the
    # ledger (`contract_coverage_exit.announce_coverage_floor`), so a caller
    # reading only stderr (or `action/run.sh`'s own `_coverage_gated()`
    # stderr fallback, reached whenever no JSON report exists -- e.g. a
    # directory/package operand outside a `pull_request` event, where the
    # Action's PR-comment JSON rerun never fires) can still tell the axis
    # fired. The release path folds the same contribution into its exit code
    # unconditionally (`_exit_compare_release`) but, unlike single-pair
    # `compare`, never said so anywhere except `-o json=...`'s own
    # `contract_coverage_exit_contribution` field -- so a markdown-format
    # release run gave no visible reason for an exit code coverage alone
    # raised. Only `-o json=...` already states it; every other format
    # gets this one line.
    #
    # Gated on *failure count*, not exit contribution (Codex review,
    # CLI-audit P2 follow-up): `contract.unresolved: warn` deliberately
    # zeroes the exit contribution while the failures themselves stay real
    # -- an advisory (warn-accepted) coverage gap must still be announced,
    # exactly as single-pair `compare`'s own `coverage_failure_diagnostic`
    # speaks regardless of the floor (see its `floor == 0` "Accepted by
    # contract.unresolved=warn" wording).
    if contract_coverage_failure_count != 0 and fmt != "json":
        _affected = sorted(
            str(lib.get("library"))
            for lib in library_results
            if isinstance(lib, dict) and lib.get("contract_coverage_failure_count", 0)
        )
        if _affected:
            if contract_coverage_exit_contribution == 0:
                # Exact wording match with single-pair compare's own
                # `_coverage_message` (contract_coverage_exit.py) --
                # `=`, not `:` -- deliberately, so a consumer distinguishing
                # "accepted" from "genuinely gated" (e.g. action/run.sh's
                # `_coverage_gated()`) can match one phrase regardless of
                # which command produced the notice (Codex review).
                _effect = (
                    "Accepted by contract.unresolved=warn, so it "
                    "contributes 0 to the release exit code"
                )
            else:
                _effect = (
                    f"Contributes {contract_coverage_exit_contribution} to "
                    "the release exit code"
                )
            click.echo(
                "Contract coverage incomplete for the selected --contract "
                "domain in: " + ", ".join(_affected) + f". {_effect}. See "
                "contract_coverage_failure_count in -o json=... output "
                "for per-library detail.",
                err=True,
            )

    # ADR-064's evidence-contract axis (exit 7). The notice is this layer's
    # own business (the per-member `DiffResult` is gone by now, so the note a
    # single-pair run renders cannot be); the *decision* is not -- it goes
    # through `_exit_compare_release` like every other axis, so the process
    # exit and the persisted `exit` block cannot disagree.
    _exit_compare_release(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
        # The evidence-contract axis and its stderr notice are both derived
        # from these, by the one resolver the persisted `exit` block reads.
        library_results=library_results,
        release_global_verdict=fold_release_outcome(
            (), release_global_verdicts(bundle_result, matrix_result)
        ).global_verdict,
        incomplete_scope_exit_contribution=(
            scope_terms.decision.incomplete_scope_exit_contribution
            if scope_terms
            else 0
        ),
        no_comparison_completed_exit_contribution=(
            scope_terms.decision.no_comparison_completed_exit_contribution
            if scope_terms
            else 0
        ),
        # ADR-071: the whole resolved decision, not just its contribution --
        # `_exit_compare_release` both folds it and formats its notice, and
        # the notice's wording needs the real compatibility exit, which only
        # that function's own resolution knows.
        assurance_decision=(
            assurance_terms.decision if assurance_terms is not None else None
        ),
    )
