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

"""The release report's CLI-side rendering helpers.

Release-output finalization (:func:`_finalize_release_output`/
:func:`_write_release_summary_file`), early suppression validation
(:func:`_validate_suppression_early`), and the per-member finding projection
every renderer reads (:func:`release_finding_dicts`/
:func:`project_release_library_results`). The release itself --
member pairing and comparison, bundle and build-configuration matrix
analysis, the exit folds -- is :mod:`abicheck.workflows.release_compare`;
:func:`abicheck.cli_compare_release.compare_release_cmd` runs it and hands
the outcome here to render.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from .bundle import BundleDiffResult
from .checker import DiffResult

# Several names imported below are no longer used by this module: release
# input resolution moved to `workflows.release_inputs` (see the re-export
# note further down). They stay imported because `cli_compare_release.py`
# re-exports them *from here* and direct tests import them by those names --
# deleting them would be an import break for no gain, so the blocks are
# marked rather than trimmed.
from .frontends.cli.options.params import (
    DEFAULT_POLICY_PROFILE,
    _load_suppression_and_policy,
)
from .frontends.cli.release_exit import _exit_compare_release
from .frontends.cli.release_report import render_release_summary
from .frontends.cli.release_summary import (  # moved (ADR-065 S2), re-exported
    _write_release_summary_file as _write_release_summary_file,
)
from .frontends.cli.runtime import _safe_write_output, _write_or_echo  # noqa: F401
from .model.symbol_inventory import SymbolInventory
from .report.comparison_scope import ComparisonScopeTerms
from .report.release_assurance_section import ReleaseAssuranceTerms
from .report.release_public_surface_section import ReleasePublicSurfaceTerms
from .workflows.extraction import package_component_inventory  # noqa: F401
from .workflows.gate import (
    fold_release_outcome,
    incomplete_scope_diagnostic,
    release_global_verdicts,
)
from .workflows.release_exit_fold import release_compatibility_base_exit

#: Release formats rendered from the per-member DiffResults (``diff_pairs``):
#: JUnit suites and SARIF runs each come from one member's own comparison.
_PAIR_FORMATS = frozenset({"junit", "sarif"})

if TYPE_CHECKING:
    from .model.package_inventory import PackageInventory  # noqa: F401
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig


#: Release *input resolution* -- package/debug/devel extraction, library
#: discovery, stored-``ProjectSnapshot`` variant materialization,
#: ``--dso-only`` classification, per-side headers/includes and key matching
#: -- moved to ``workflows.release_inputs`` (ADR-061 gap D's "Remaining
#: scope"): it is engine work, it raised ``click`` errors from inside the
#: resolution, and both facts are what kept the release request/plan off
#: ``abicheck.service``. Re-exported here under their original private names
#: for every existing importer, tests included. Neither raises ``click``
#: anything now -- they raise the typed ``errors.ReleaseOperandContentError``/
#: ``ReleaseOperandUsageError``, which
#: ``frontends.cli.release_compare_request`` translates at the CLI boundary
#: into the two ``click`` types (and therefore the two exit codes) this
#: command always produced.
from .workflows import release_inputs as _release_inputs  # noqa: E402

# Plain assignments, for the same `no_implicit_reexport` reason
# `cli_compare_release_helpers.py`'s own re-exports give.
_discover_files = _release_inputs.discover_files
_prepare_compare_release_inputs = _release_inputs.prepare_release_inputs


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
    release's ``--view show=`` selection into the rendered text.
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
            added_keys,
            old_map,
            new_map,
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


def _validate_suppression_early(
    suppress: Path | tuple[Path, ...] | None,
    policy: str,
    policy_file_path: Path | None,
    strict_suppressions: bool,
    require_justification: bool,
) -> None:
    """Load and validate the suppression file before entering the per-library loop.

    Only invoked when the user passes a suppression file together with
    *strict_suppressions* or *require_justification*, so that stale or
    undocumented rules are rejected before any expensive per-library work.
    """
    if suppress is not None and (strict_suppressions or require_justification):
        _load_suppression_and_policy(
            suppress,
            policy,
            policy_file_path,
            strict_suppressions=strict_suppressions,
            require_justification=require_justification,
        )
