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

"""``compare-release``'s matrix-result/output/gating engine (split from
:mod:`abicheck.cli_compare_release`).

Input discovery (:func:`_discover_files`/:func:`_prepare_compare_release_inputs`),
per-library matrix-result collection (:func:`_collect_matrix_result`),
release-output finalization (:func:`_finalize_release_output`/
:func:`_write_release_summary_file`), early suppression validation
(:func:`_validate_suppression_early`), and severity-bucket/finding-dict
computation feeding the aggregate release verdict
(:func:`_release_gating_buckets`/:func:`_release_finding_dicts`/
:func:`_strip_diff_results_and_adjust_verdict`).

Extracted purely to keep :mod:`abicheck.cli_compare_release` itself under
the AI-readiness 2000-line hard cap -- see this module's own sibling,
:mod:`abicheck.cli_compare_release_pairwise` (the per-pair/per-library
comparison engine -- the *other* half of what
:func:`abicheck.cli_compare_release.compare_release_cmd` calls but does
not itself define), for the fuller rationale: `architecture/debt.yaml`
pins :mod:`abicheck.cli_compare_release` (and its pre-existing sibling
:mod:`abicheck.cli_compare_release_helpers`) at their exact adoption-time
line count, and a single combined engine module would itself have landed
over the AI-readiness 800-line production cap for a *new* file. A
mechanical extraction (unchanged function bodies).
:mod:`abicheck.cli_compare_release` re-exports every name here that an
existing test or caller imports directly (``from
abicheck.cli_compare_release import ...``) for back-compat -- new code
should import from here directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .bundle import BundleDiffResult
from .checker import Change, DiffResult

# Several names imported below are no longer used by this module: release
# input resolution moved to `workflows.release_inputs` (see the re-export
# note further down). They stay imported because `cli_compare_release.py`
# re-exports them *from here* and direct tests import them by those names --
# deleting them would be an import break for no gain, so the blocks are
# marked rather than trimmed.
from .cli import (  # noqa: F401
    _build_match_map,
    _collect_release_inputs,
    _safe_write_output,
    _write_or_echo,
)
from .cli_compare_release_helpers import (  # noqa: F401
    _RELEASE_VERDICT_ORDER,
    _debian_symbols_warning,
    _discover_include_roots,
    _exit_compare_release,
    _format_release_summary,
    _match_release_keys,
    _resolve_release_headers,
    debian_symbols_release_conflict_lines as _debian_symbols_release_conflict_lines,
)
from .frontends.cli.options.params import (
    DEFAULT_POLICY_PROFILE,
    _load_suppression_and_policy,
)
from .frontends.cli.release_summary import (  # moved (ADR-065 S2), re-exported
    _write_release_summary_file as _write_release_summary_file,
)
from .frontends.cli.release_variant_operand import (  # noqa: F401
    _resolve_release_package_side,
)
from .model import AbiSnapshot
from .report.comparison_scope import ComparisonScopeTerms
from .report.release_assurance import ReleaseAssuranceTerms
from .workflows.extraction import package_component_inventory  # noqa: F401
from .workflows.gate import incomplete_scope_diagnostic

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
#: command always produced. The ``_extract_if_package``/``_build_match_map``
#: wrappers in this file's sibling CLI modules keep their own translation for
#: their *other* callers (``dump``, ``compare --bundle-facts``), which still
#: reach them directly.
from .workflows import release_inputs as _release_inputs  # noqa: E402

# Plain assignments, for the same `no_implicit_reexport` reason
# `cli_compare_release_helpers.py`'s own re-exports give.
_discover_files = _release_inputs.discover_files
_prepare_compare_release_inputs = _release_inputs.prepare_release_inputs


def _collect_matrix_result(
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    policy: str,
    worst_verdict: str,
    *,
    suppress: Path | None = None,
    policy_file_path: Path | None = None,
    old_version: str = "",
    new_version: str = "",
    pack_application: PackApplication | None = None,
    project_policy_overrides: Any = None,
) -> tuple[DiffResult | None, str]:
    """Load probe-matrix snapshots, run them through the compare pipeline, fold.

    Returns (matrix_result, worst_verdict). When no matrix snapshots are
    given, matrix_result is None and the verdict is unchanged. The matrix
    findings are release-global build-configuration changes
    (CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV,
    BEHAVIOURAL_DEFAULT_CHANGED).

    Rather than re-deriving a verdict, the changes are fed to
    :func:`checker.compare` as ``extra_changes`` over a pair of empty
    snapshots — exactly the path the single-pair ``compare`` command uses.
    This routes them through the *whole* pipeline uniformly: ``--suppress``
    rules, ``--policy-file`` per-kind overrides, and verdict composition all
    apply, so a suppression like ``cxx_standard_floor_raised`` or a policy
    override is honoured identically on both commands. The returned
    :class:`DiffResult` carries the post-suppression kept findings, which the
    report (JSON / markdown / JUnit) renders.

    *pack_application* (CLI cleanup phase two, "PR B" slice 1) folds the
    release's already-resolved ``--pack`` contribution into this pair's own
    ``PolicyFile`` too -- these matrix findings go through the same
    ``--policy-file`` per-kind overrides every other library does, so a
    pack overriding e.g. ``cxx_standard_floor_raised`` must apply here
    identically, not only to the per-library comparisons. *project_policy_
    overrides* (round 9 finding) is the analogous ``.abicheck.yml``
    ``policy.overrides`` fold at the weaker PROJECT_CONFIG tier -- this
    function used to build its own ``PolicyFile`` by hand
    (``_load_suppression_and_policy`` + a manual pack fold) instead of
    calling :func:`~abicheck.pack_application.resolve_bundle_policy_file`,
    the shared resolver its own sibling bundle-result call already uses,
    which is why the matrix path alone never picked up a project override
    the bundle path already honored.
    """
    from .frontends.cli.runtime import _load_probe_matrix_changes

    matrix_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)
    if not matrix_changes:
        return None, worst_verdict

    from .model import AbiSnapshot
    from .pack_application import resolve_bundle_policy_file
    from .service import compare_snapshots

    suppression, _ = _load_suppression_and_policy(suppress, policy, policy_file_path)
    pf = resolve_bundle_policy_file(
        suppress,
        policy,
        policy_file_path,
        pack_application,
        project_policy_overrides,
    )
    # Empty snapshots contribute no per-binary changes; the matrix findings
    # ride in as extra_changes and inherit the full post-processing pipeline.
    name = "<build-config matrix>"
    result = compare_snapshots(
        AbiSnapshot(library=name, version=old_version or "old"),
        AbiSnapshot(library=name, version=new_version or "new"),
        suppression=suppression,
        policy=policy,
        policy_file=pf,
        scope_to_public_surface=False,
        extra_changes=matrix_changes,
    )
    matrix_verdict = result.verdict.value
    if _RELEASE_VERDICT_ORDER.get(matrix_verdict, 0) > _RELEASE_VERDICT_ORDER.get(
        worst_verdict, 0
    ):
        worst_verdict = matrix_verdict
    return result, worst_verdict


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
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]],
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
    suppress: Path | None = None,
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
    max_findings: int | None = None,
) -> None:
    """Write summary output, step summary, per-library dir report, then exit.

    *scope_terms* (ADR-065 S2) is the release's one resolved
    :class:`~abicheck.report.comparison_scope.ComparisonScopeTerms`: every
    rendered format, the ``--output-dir`` sidecar, the stderr notice, and
    the real process exit read the same object. *removed_keys*/*added_keys*
    are the **proven** sets (D2) by the time they reach here.

    *show_only* (Codex review, PR #1154 second follow-up): this function is
    only ever called for the **primary** ``--format`` render (the secondary
    ``--write`` render is a separate call in ``cli_compare_release.py`` that
    never passes it), so it is the one caller allowed to forward the
    release's ``--view show=`` selection into the rendered text.
    """
    text = _format_release_summary(
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
        diff_pairs=diff_pairs if fmt == "junit" else None,
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
        # The Markdown render applies the *resolved* per-library cap, so the
        # run's own `--max-findings-per-library` has to reach it.
        max_findings=max_findings,
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
            base_exit=_release_compatibility_base_exit(
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
    # `compare`, never said so anywhere except `--format json`'s own
    # `contract_coverage_exit_contribution` field -- so a markdown-format
    # release run gave no visible reason for an exit code coverage alone
    # raised. Only `--format json` already states it; every other format
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
                "domain in: "
                + ", ".join(_affected)
                + f". {_effect} (ADR-049 contract-coverage axis). See "
                "contract_coverage_failure_count in --format json output "
                "for per-library detail.",
                err=True,
            )

    # ADR-064's evidence-contract axis (exit 7). The notice is this layer's
    # own business (the per-member `DiffResult` is gone by now, so the note a
    # single-pair run renders cannot be); the *decision* is not -- it goes
    # through `_exit_compare_release` like every other axis, so the process
    # exit and the persisted `exit` block cannot disagree.
    from .cli_compare_release_helpers import _release_global_verdict

    _exit_compare_release(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
        # The evidence-contract axis and its stderr notice are both derived
        # from these, by the one resolver the persisted `exit` block reads.
        library_results=library_results,
        release_global_verdict=_release_global_verdict(bundle_result, matrix_result),
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


def _release_compatibility_base_exit(
    worst_verdict: str, severity_exit_code: int | None
) -> int:
    """The compatibility axis's own exit code for a stderr notice's wording
    -- the severity-aware code when one is in effect, else the legacy
    verdict mapping with the release's own operational ``ERROR`` floor.
    ``not_comparable`` is ``16`` under either scheme, exactly as
    ``_exit_compare_release`` exits it ahead of every floor (CodeRabbit)."""
    if worst_verdict == "not_comparable":
        return 16
    if severity_exit_code is not None:
        return max(severity_exit_code, 4 if worst_verdict == "ERROR" else 0)
    from .checker_policy import Verdict
    from .workflows.gate import legacy_exit_code

    if worst_verdict in Verdict.__members__:
        return legacy_exit_code(Verdict[worst_verdict])
    return 4 if worst_verdict == "ERROR" else 0


def _validate_suppression_early(
    suppress: Path | None,
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


#: Both values now live in the ``report.release_display_limits`` leaf, so
#: the Markdown renderer that applies the cap can read it without importing
#: this module -- a function-local import between the two was a real new
#: import cycle. Re-exported here under their original private names for
#: every existing importer, tests included.
from .report import release_display_limits as _display_limits  # noqa: E402

# Plain assignments (mypy's `no_implicit_reexport`), for the same reason the
# release-input re-exports below give. The two *resolvers* live in that leaf
# too, not here: the Markdown renderer applies the resolved cap and reaching
# back into this module for it was a real import cycle.
_MAX_RELEASE_FINDINGS_PER_LIBRARY = _display_limits.MAX_RELEASE_FINDINGS_PER_LIBRARY
_MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR = (
    _display_limits.MAX_RELEASE_FINDINGS_PER_LIBRARY_ENV_VAR
)
_release_findings_cap_is_explicit = _display_limits.release_findings_cap_is_explicit
_resolve_max_release_findings_per_library = (
    _display_limits.resolve_max_release_findings_per_library
)






def _release_change_kind_str(c: Any) -> str:
    """The same tolerant ``kind`` read ``release_finding_entry`` uses, standalone.

    Mirrors ``cli_scan_baseline._change_kind_str`` -- kept local rather than
    imported since the two ``cli_*`` command families are independently
    owned and this is a five-line, dependency-free primitive. Shared so the
    per-kind truncation ledger below counts a raw ``Change`` the identical
    way the finding dicts spell its ``kind`` -- a mismatch here would make
    the ledger's keys disagree with the ``kind`` values in
    ``entry["findings"]`` itself.
    """
    kind = getattr(c, "kind", None)
    return str(getattr(kind, "value", str(kind)))


def _accumulate_release_kind_counts(
    entry: dict[str, object], field: str, kinds: Any
) -> None:
    """Add *kinds* (an iterable of kind strings) onto ``entry[field]``.

    Mirrors ``cli_scan_baseline._accumulate_kind_counts``: a running dict
    (not overwritten), sorted by kind name so the JSON is deterministic and
    diff-friendly across runs of the same input.
    """
    from collections import Counter

    existing = entry.get(field) or {}
    counter: Counter[str] = Counter(existing if isinstance(existing, dict) else {})
    counter.update(kinds)
    if counter:
        entry[field] = dict(sorted(counter.items()))


def _release_gating_buckets(
    diff: DiffResult,
    severity_config: SeverityConfig | None,
) -> list[tuple[str, list[Change]]]:
    """Return the named (bucket, changes) groups that gate *diff*'s exit code.

    Without *severity_config* (the legacy verdict-based exit-code scheme),
    only the three verdict buckets that ever gate the legacy exit code are
    used. With *severity_config* active, the release can instead exit
    non-zero because a category that's normally compatible (additions,
    quality issues) was promoted to ``error`` — e.g. ``severity.addition:
    error`` — so every category the active config gates to ``error`` is
    used instead (Codex review on #557: walking only the legacy buckets left
    a library reporting ``severity.exit_code: 1`` with an empty ``findings``
    list even though a specific addition/quality-issue finding was exactly
    what blocked the release).
    """
    if severity_config is not None:
        from .workflows.gate import categorize_changes, gate_decision_for_result

        kind_sets = diff._effective_kind_sets()
        # gate_decision_for_result (the single canonical gate-decision call
        # site, also used by reporter.py/sarif.py/html_report.py — ADR-061
        # D9) decides *which* categories are actually blocking;
        # categorize_changes supplies the change lists for them — a category
        # with no findings never contributes an (empty) bucket, matching how
        # JSON/SARIF's blocking_categories behave.
        gate = gate_decision_for_result(diff, severity_config)
        assert gate is not None  # severity_config is not None here
        categorized = categorize_changes(
            diff.changes,
            policy=diff.policy,
            kind_sets=kind_sets,
            policy_file=diff.policy_file,
        )
        cat_changes_by_name = {
            "abi_breaking": categorized.abi_breaking,
            "potential_breaking": categorized.potential_breaking,
            "quality_issues": categorized.quality_issues,
            "addition": categorized.addition,
        }
        return [(name, cat_changes_by_name[name]) for name in gate.blocking_categories]
    return [
        ("breaking", diff.breaking),
        ("api_break", diff.source_breaks),
        ("risk", diff.risk),
    ]


def _release_display_buckets(
    diff: DiffResult,
    severity_config: SeverityConfig | None,
) -> list[tuple[str, list[Change]]]:
    """Return every (bucket, changes) group in *diff*, for display purposes.

    Codex review (PR #1154 follow-up, "Filter the complete release finding
    set"): :func:`_release_gating_buckets` deliberately narrows to only the
    categories that can gate the release's own exit code -- under the
    legacy scheme that's breaking/api_break/risk, and under a severity
    scheme it's further narrowed to whichever categories
    ``gate_decision_for_result`` says are actually *blocking*. That
    restriction is correct for computing the exit code, but the release
    fan-out's own `findings`/`findings_view` display pool used the same
    restricted buckets -- so a `func_added`/other COMPATIBLE finding could
    never appear in a directory/package release's own findings list (nor
    be reachable via `--view show=compatible`/`show=added`), even though
    the identical single-pair `compare` for that same library displays it.
    This function is the unrestricted counterpart: every category, always,
    regardless of what's blocking -- the same "full diff, not the gate's
    own subset" pool a single-pair `compare` report renders from. Gate
    bucket restriction stays reserved for :func:`_release_gating_buckets`'s
    own exit-code-facing callers.
    """
    if severity_config is not None:
        from .workflows.gate import categorize_changes

        kind_sets = diff._effective_kind_sets()
        categorized = categorize_changes(
            diff.changes,
            policy=diff.policy,
            kind_sets=kind_sets,
            policy_file=diff.policy_file,
        )
        # Dict + name-lookup (matching `_release_gating_buckets`'s own
        # shape above), not a literal list of tuples -- `categorize_changes`
        # types each field as `list[HasKind]`, and building the return list
        # this way is what keeps mypy happy the same way it already is above.
        cat_changes_by_name = {
            "abi_breaking": categorized.abi_breaking,
            "potential_breaking": categorized.potential_breaking,
            "quality_issues": categorized.quality_issues,
            "addition": categorized.addition,
        }
        return [
            (name, cat_changes_by_name[name])
            for name in (
                "abi_breaking",
                "potential_breaking",
                "quality_issues",
                "addition",
            )
        ]
    return [
        ("breaking", diff.breaking),
        ("api_break", diff.source_breaks),
        ("risk", diff.risk),
        ("compatible", diff.compatible),
        # Codex review, fresh evidence ("Preserve not-evaluated findings in
        # release summaries"): `breaking`/`source_breaks`/`risk`/`compatible`
        # all route through `_evaluated_changes()`, which -- under
        # `--contract` -- excludes a finding contract evaluation left
        # NOT_EVALUATED (unknown/unproven/proven-out-of-contract relevance).
        # Without this bucket, the severity_config-is-None branch above (the
        # legacy, non-severity-aware release path) is the one place such a
        # finding disappears entirely from `findings`/`--view show=...`: the
        # severity_config branch above categorizes `diff.changes` directly
        # (kind-based, relevance-blind), so it never drops them in the first
        # place, and the equivalent scalar `compare` JSON/per-library
        # `--output-dir` report serialize `diff.changes` too. `diff.
        # not_evaluated` is the same disclosure the single-pair renderer's
        # own dedicated not-evaluated section gives these findings -- listed
        # under their own bucket, never silently merged into "compatible"
        # (which would misrepresent an unproven/unresolved relevance as an
        # actual compatibility verdict).
        ("not_evaluated", diff.not_evaluated),
    ]


def _release_finding_dicts(
    diff: DiffResult,
    severity_config: SeverityConfig | None = None,
    show_only: str | None = None,
    max_findings: int | None = None,
    *,
    uncapped: bool = False,
) -> tuple[list[dict[str, object]], list[str]]:
    """Project a library's gating findings into small, capped dicts.

    *uncapped* builds the **complete** projection instead, and returns no
    cut kinds -- nothing was cut. Used for the machine documents when no
    cap was actually requested (see
    :func:`_release_findings_cap_is_explicit`): a truncated JSON/JUnit
    document nobody asked to truncate is not a summary, it is a lossy
    result, and this release schema has no per-library findings array
    elsewhere for a consumer to fall back to.

    Same shape as ``cli_scan_baseline._baseline_finding_dicts`` /
    ``stack_report._stack_finding_dicts``. Counts (not already-built dicts)
    decide the cap so a large diff never builds more dicts than the cap can
    ever keep. See :func:`_release_display_buckets` for which findings this
    walks -- the full diff (all categories), not the narrower
    :func:`_release_gating_buckets` subset that only ever gates the exit
    code (Codex review, PR #1154 follow-up: "Filter the complete release
    finding set" -- a compatible addition must be reachable here the same
    way it is in a single-pair `compare` report).

    *show_only* (Codex review, PR #1154 follow-up: `compare --view
    show=...` on a directory/package input) filters each display bucket the
    same way a single-pair `compare`'s own report filters `result.changes`
    (`reporter_markdown.apply_show_only`, resolved against this library's
    own effective kind sets/policy file so it never disagrees with the
    per-finding severity a single-pair report for the same library would
    show) -- applied before the cap, so a filtered-out finding never
    occupies one of the cap's slots a displayed one needed.

    *max_findings* (``compare-release --max-findings-per-library``) is
    resolved via :func:`_resolve_max_release_findings_per_library` --
    ``None`` falls back to the env var, then the built-in default, exactly
    like ``cli_scan_baseline``'s identical knob.

    Each dict also carries ``reclassified_by`` (schema 2.31) when a
    ``reclassify:`` rule decided the change's effective verdict, via
    :func:`abicheck.reporter.release_finding_entry` -- the identical
    resolution `compare`'s ``changes[]``/``scan --against`` use, so the
    three can't drift on which rule fired for a shared finding.

    Returns ``(dicts, cut_kinds)`` -- ``cut_kinds`` is every kind string cut
    from *any* bucket (not just the one that first hit the cap), so a
    caller can accumulate an exact kind -> count truncation ledger the same
    way ``cli_scan_baseline._baseline_summary`` does, instead of a bare
    ``findings_truncated`` boolean that hides the shape of what was cut.
    """
    from .reporter import release_finding_entry
    from .reporter_markdown import apply_show_only

    cap = (
        _resolve_max_release_findings_per_library(max_findings)
        if not uncapped
        else None
    )
    findings: list[dict[str, object]] = []
    cut_kinds: list[str] = []
    for bucket_name, bucket_changes in _release_display_buckets(diff, severity_config):
        if show_only:
            bucket_changes = apply_show_only(
                bucket_changes,
                show_only,
                policy=diff.policy or "strict_abi",
                kind_sets=diff._effective_kind_sets(),
                policy_file=diff.policy_file,
            )
        if cap is None:
            included, excluded = list(bucket_changes), []
        else:
            remaining = max(0, cap - len(findings))
            included, excluded = bucket_changes[:remaining], bucket_changes[remaining:]
        for c in included:
            findings.append(release_finding_entry(c, bucket_name, diff.policy_file))
        # Keep tallying excluded kinds across every remaining bucket (not
        # just the one that first hit the cap) -- see this function's own
        # "Returns" note above.
        cut_kinds.extend(_release_change_kind_str(c) for c in excluded)
    return findings, cut_kinds


def _strip_diff_results_and_adjust_verdict(
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    worst_verdict: str,
    severity_config: SeverityConfig | None = None,
    *,
    needs_annotations: bool = True,
    show_only: str | None = None,
    show_impact: bool = False,
    max_findings: int | None = None,
) -> str:
    """Remove un-serialisable ``_diff_result`` entries and adjust the worst verdict.

    Before the stashed :class:`DiffResult` objects are discarded, each
    library entry gets a capped ``findings`` list (kind/symbol/description/
    location) projected from it — otherwise the JSON summary is entirely
    count-centric (``"breaking": 3``) with no way to identify which symbols
    broke short of a separate `compare` run or the optional per-library
    ``--output-dir`` report file. *severity_config*, when the release's exit
    code is severity-aware, is forwarded to :func:`_release_finding_dicts` so
    a library gated by a promoted addition/quality-issue category (not one of
    the legacy breaking/api_break/risk buckets) still gets a matching
    finding, not an empty list next to a nonzero ``severity.exit_code``.
    Stripping the private keys (``_diff_result`` and friends) keeps the
    summary formatter free of Python-only objects. If any library was
    *removed*, the verdict is bumped to at least ``COMPATIBLE_WITH_RISK``.

    *needs_annotations* gates whether the uncapped ``annotations`` array
    (unlike ``findings`` above, deliberately unbounded -- see its own
    comment) is built at all (Codex review, fresh evidence): only a JSON
    render (primary ``--format json`` or a secondary ``--write
    json=...``) ever reads it, but every entry in ``library_results`` is
    held until the whole release finishes, so building it unconditionally
    grew peak memory by every library's full finding set even for a
    markdown/JUnit-only render that never reads it.

    *show_only* (Codex review, PR #1154 second follow-up: "Apply release
    show filters inside each renderer"): this is the **last** point a real
    :class:`DiffResult` (with its own ``policy``/``policy_file``/
    ``effective_verdict`` overrides) is available for a library, so it is
    where a ``show_only``-filtered *view* of the findings is computed --
    but ``entry["findings"]`` itself always stays the **full**, unfiltered
    projection, exactly as it would with no ``--view show=`` in effect. The
    filtered view is stashed under the private ``findings_view``/
    ``findings_view_truncated`` keys, consumed (and stripped) by
    :func:`abicheck.cli_compare_release_helpers._release_findings_for_render`
    only for whichever renderer is the *primary* (``--format``) one -- a
    secondary ``--write`` report is documented/contracted to always be full
    (mirroring single-pair ``compare``'s own ``--write`` behaviour), so it
    must never see this filtered view. Computing the view here rather than
    re-filtering ``entry["findings"]`` downstream is deliberate: the
    severity dimension of ``--view show=`` resolves through
    ``effective_verdict_for_change`` (frozen-namespace/reclassify/policy-
    file overrides), which needs the live ``Change`` objects this function
    is the last place to see before they are discarded for memory.

    *show_impact* (Codex review, PR #1154 follow-up: "Reject unsupported
    impact views instead of silently dropping them" -- ``compare --view
    impact`` on a directory/package input) computes each library's own
    impact-summary table (:func:`abicheck.reporter_markdown.
    compute_impact_table`, the identical helper a single-pair `compare`
    report's own impact table uses) from the same full display pool
    :func:`_release_display_buckets` builds, stashed as ``entry[
    "impact_table"]`` (``None``-valued tables are simply omitted, matching
    how ``entry["findings"]`` is only set when non-empty). When *show_only*
    is also active, a second, filtered ``entry["impact_table_view"]`` is
    computed the same way :func:`_release_finding_dicts`'s ``findings_view``
    is -- consumed and stripped by
    :func:`abicheck.cli_compare_release_helpers._release_findings_for_render`
    for whichever renderer is primary, never for a secondary ``--write``.

    *max_findings* (``compare-release --max-findings-per-library`` /
    ``$ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY``) overrides the default
    per-library cap -- see :func:`_resolve_max_release_findings_per_library`.
    Whenever a library's ``findings``/``findings_view`` is truncated, the
    kinds cut are also accumulated into ``findings_truncated_kinds``/
    ``findings_view_truncated_kinds`` (kind -> count cut), mirroring
    ``cli_scan_baseline``'s identical ``findings_truncated_kinds`` ledger.

    Returns the (possibly updated) *worst_verdict* string.
    """
    cap = _resolve_max_release_findings_per_library(max_findings)
    # `entry["findings"]` feeds every renderer, and only the human ones want
    # a cap. Left complete unless this run actually asked for truncation, so
    # a machine document is complete by default; `_release_md_library_
    # findings` applies the presentation cap at render time instead.
    uncapped = not _release_findings_cap_is_explicit(max_findings)
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        diff = entry.get("_diff_result")
        if isinstance(diff, DiffResult):
            display_buckets = _release_display_buckets(diff, severity_config)
            total_gating = sum(len(cat_changes) for _, cat_changes in display_buckets)
            findings, cut_kinds = _release_finding_dicts(
                diff, severity_config, None, max_findings, uncapped=uncapped
            )
            if findings:
                entry["findings"] = findings
                if not uncapped and total_gating > cap:
                    entry["findings_truncated"] = True
                    _accumulate_release_kind_counts(
                        entry, "findings_truncated_kinds", cut_kinds
                    )
            # Codex review, fresh evidence ("Count uncapped findings in
            # release filter totals"): the uncapped pool size behind
            # `findings` above -- `len(entry["findings"])` alone
            # under-reports once a library crosses the resolved cap (e.g. 25
            # findings reports as 10 under the default). This is the one
            # place the real, uncapped
            # `total_gating` is still available before `diff` is discarded
            # below; a private, `findings_view`-shaped key (popped by
            # `_release_findings_for_render`, never rendered) so
            # `_format_release_json`'s `filtered_summary`-equivalent totals
            # can read the true pre-filter count instead of re-deriving it
            # from the already-capped display list.
            entry["findings_total_count"] = total_gating
            if show_only:
                # The filtered *view*, alongside (never instead of) the full
                # projection above -- see this function's own docstring.
                from .reporter_markdown import apply_show_only as _apply_show_only

                total_gating_view = sum(
                    len(
                        _apply_show_only(
                            cat_changes,
                            show_only,
                            policy=diff.policy or "strict_abi",
                            kind_sets=diff._effective_kind_sets(),
                            policy_file=diff.policy_file,
                        )
                    )
                    for _, cat_changes in display_buckets
                )
                findings_view, cut_kinds_view = _release_finding_dicts(
                    diff, severity_config, show_only, max_findings, uncapped=uncapped
                )
                entry["findings_view"] = findings_view
                if not uncapped and total_gating_view > cap:
                    entry["findings_view_truncated"] = True
                    _accumulate_release_kind_counts(
                        entry, "findings_view_truncated_kinds", cut_kinds_view
                    )
                # The filtered counterpart of `findings_total_count` above --
                # same rationale, same private/popped contract.
                entry["findings_total_count_view"] = total_gating_view
            # CLI cleanup phase two, PR E: the uncapped, always-classified
            # counterpart to the capped `findings` list above -- the exact
            # same shape single-library `compare --format json` persists at
            # its own top-level `annotations` (schema 2.43,
            # `annotations.annotation_report_entries`), reused verbatim so
            # the two can never disagree. This is what lets the Action's own
            # renderer (`action/run.sh`'s `_emit_annotations`) read a
            # release-style operand's report the same way it reads a
            # single-library one, instead of needing an independent
            # per-library re-run this module used to perform
            # (`_collect_release_extras`, since removed) just to recover the
            # same DiffResult already sitting right here.
            if show_impact:
                import dataclasses

                from .reporter_markdown import compute_impact_table

                full_changes = [
                    c for _, cat_changes in display_buckets for c in cat_changes
                ]
                impact_table = compute_impact_table(diff, full_changes)
                if impact_table is not None:
                    entry["impact_table"] = dataclasses.asdict(impact_table)
                if show_only:
                    from .reporter_markdown import apply_show_only as _apply_show_only2

                    filtered_changes = [
                        c
                        for _, cat_changes in display_buckets
                        for c in _apply_show_only2(
                            cat_changes,
                            show_only,
                            policy=diff.policy or "strict_abi",
                            kind_sets=diff._effective_kind_sets(),
                            policy_file=diff.policy_file,
                        )
                    ]
                    impact_table_view = compute_impact_table(diff, filtered_changes)
                    # Unlike `entry["impact_table"]` above (only set when
                    # non-None), this key is *always* set whenever
                    # `show_only` is active -- even to `None` -- so
                    # `_release_findings_for_render`'s swap (mirroring
                    # `findings_view`'s own always-set contract) can tell
                    # "no view was computed" apart from "the view is empty".
                    entry["impact_table_view"] = (
                        dataclasses.asdict(impact_table_view)
                        if impact_table_view is not None
                        else None
                    )
            if needs_annotations:
                from .annotations import annotation_report_entries

                entry["annotations"] = annotation_report_entries(
                    diff, severity_config=severity_config
                )
        entry.pop("_diff_result", None)
        entry.pop("_old_snapshot", None)
        entry.pop("_new_snapshot", None)
        entry.pop("_old_bundle_evidence", None)
        entry.pop("_new_bundle_evidence", None)
        entry.pop("_bundle_key", None)
    if removed_keys and _RELEASE_VERDICT_ORDER.get(
        worst_verdict, 0
    ) < _RELEASE_VERDICT_ORDER.get("COMPATIBLE_WITH_RISK", 0):
        worst_verdict = "COMPATIBLE_WITH_RISK"
    return worst_verdict


