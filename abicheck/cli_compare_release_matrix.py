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

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .bundle import BundleDiffResult
from .checker import Change, DiffResult
from .cli import (
    _build_match_map,
    _collect_release_inputs,
    _safe_write_output,
    _write_or_echo,
)
from .cli_compare_release_helpers import (
    _RELEASE_VERDICT_ORDER,
    _collect_release_warnings,
    _debian_symbols_warning,
    _discover_include_roots,
    _exit_compare_release,
    _format_release_summary,
    _match_release_keys,
    _resolve_release_headers,
)
from .frontends.cli.options.params import _load_suppression_and_policy
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig


def _discover_files(
    input_dir: Path,
    lib_dir: Path,
    include_private: bool,
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
) -> list[Path]:
    """Discover library files from a directory or extracted package."""
    if is_package(input_dir):
        files = discover_shared_libraries(lib_dir, include_private=include_private)
        if not files:
            files = _collect_release_inputs(lib_dir)
    else:
        files = _collect_release_inputs(lib_dir)
    return files


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
) -> None:
    """Write per-library summary JSON to output directory.

    *severity_config*, when in effect, feeds the same effective-config
    digest ``_format_release_json`` (the primary release report) already
    stamps, via the one shared helper ``_release_summary_effective_config_
    block`` so the two can never independently drift (Codex review, PR
    #803). Also gains the same ``exit`` block that report does, via the
    same resolver (ADR-064 stage 1b, Codex review).
    """
    from .cli_compare_release_helpers import (
        _release_global_verdict,
        _release_summary_effective_config_block,
    )
    from .report.not_comparable import run_outcome_dict_for_release
    from .workflows.gate import resolve_release_exit_decision_for_report

    digest, fields = _release_summary_effective_config_block(severity_config)
    exit_dict = resolve_release_exit_decision_for_report(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution,
        library_results,
        _release_global_verdict(bundle_result, matrix_result),
    ).to_dict()
    summary_data: dict[str, object] = {
        "verdict": worst_verdict,
        "libraries": library_results,
        "unmatched_old": [old_map[k].name for k in removed_keys],
        "unmatched_new": [new_map[k].name for k in added_keys],
        "effective_config_digest": digest,
        "effective_config_fields": fields,
        "exit": exit_dict,
        "run_outcome": run_outcome_dict_for_release(worst_verdict, exit_dict),
    }
    summary_path = output_dir / "summary.json"
    _safe_write_output(summary_path, json.dumps(summary_data, indent=2))
    click.echo(f"Per-library reports written to {output_dir}/", err=True)


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
    identically, not only to the per-library comparisons.
    """
    from .frontends.cli.runtime import _load_probe_matrix_changes

    matrix_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)
    if not matrix_changes:
        return None, worst_verdict

    from .model import AbiSnapshot
    from .service import compare_snapshots

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    if pack_application is not None:
        from .pack_application import policy_file_with_packs

        pf = policy_file_with_packs(pf, pack_application, base_policy=policy)
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
) -> None:
    """Write summary output, step summary, per-library dir report, then exit."""
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
        )

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

    _exit_compare_release(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
    )


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


_MAX_RELEASE_FINDINGS_PER_LIBRARY = 10


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


def _release_finding_dicts(
    diff: DiffResult,
    severity_config: SeverityConfig | None = None,
) -> list[dict[str, object]]:
    """Project a library's gating findings into small, capped dicts.

    Same shape as ``cli_scan_baseline._baseline_finding_dicts`` /
    ``stack_report._stack_finding_dicts``. Counts (not already-built dicts)
    decide the cap so a large diff never builds more dicts than the cap can
    ever keep. See :func:`_release_gating_buckets` for which findings this
    walks under a legacy vs. severity-aware exit-code scheme.
    """
    findings: list[dict[str, object]] = []
    for bucket_name, bucket_changes in _release_gating_buckets(diff, severity_config):
        remaining = _MAX_RELEASE_FINDINGS_PER_LIBRARY - len(findings)
        if remaining <= 0:
            break
        for c in bucket_changes[:remaining]:
            findings.append(
                {
                    "bucket": bucket_name,
                    "kind": c.kind.value,
                    "symbol": c.symbol,
                    "description": c.description,
                    "source_location": c.source_location,
                }
            )
    return findings


def _strip_diff_results_and_adjust_verdict(
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    worst_verdict: str,
    severity_config: SeverityConfig | None = None,
    *,
    needs_annotations: bool = True,
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

    Returns the (possibly updated) *worst_verdict* string.
    """
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        diff = entry.get("_diff_result")
        if isinstance(diff, DiffResult):
            total_gating = sum(
                len(cat_changes)
                for _, cat_changes in _release_gating_buckets(diff, severity_config)
            )
            findings = _release_finding_dicts(diff, severity_config)
            if findings:
                entry["findings"] = findings
                if total_gating > _MAX_RELEASE_FINDINGS_PER_LIBRARY:
                    entry["findings_truncated"] = True
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


def _prepare_compare_release_inputs(
    old_dir: Path,
    new_dir: Path,
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    include_private_dso: bool,
    dso_only: bool,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    config_includes: tuple[Path, ...],
    extract_if_package: Callable[
        [Path, Path | None, Path | None],
        tuple[Path, Path | None, Path | None, Path | None],
    ],
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
    is_elf_shared_object: Callable[[Path], bool],
) -> tuple[
    Path | None,
    Path | None,
    list[Path],
    list[Path],
    list[Path],
    list[Path],
    dict[str, Path],
    dict[str, Path],
    list[str],
    list[str],
    list[str],
    list[str],
]:
    """Prepare inputs/maps/keys for compare-release command."""
    old_lib_dir, old_debug_dir, old_header_dir, old_symbols_file = extract_if_package(
        old_dir,
        debug_info1,
        devel_pkg1,
    )
    new_lib_dir, new_debug_dir, new_header_dir, new_symbols_file = extract_if_package(
        new_dir,
        debug_info2,
        devel_pkg2,
    )
    old_files = _discover_files(
        old_dir,
        old_lib_dir,
        include_private_dso,
        discover_shared_libraries,
        is_package,
    )
    new_files = _discover_files(
        new_dir,
        new_lib_dir,
        include_private_dso,
        discover_shared_libraries,
        is_package,
    )
    if dso_only:
        old_files = [f for f in old_files if is_elf_shared_object(f)]
        new_files = [f for f in new_files if is_elf_shared_object(f)]
    old_map, old_warns = _build_match_map(old_files)
    new_map, new_warns = _build_match_map(new_files)
    warning_msgs: list[str] = [
        f"Warning: {warning}" for warning in (old_warns + new_warns)
    ]
    debian_symbols_note = _debian_symbols_warning(old_symbols_file, new_symbols_file)
    if debian_symbols_note is not None:
        warning_msgs.append(debian_symbols_note)
    old_h, new_h = _resolve_release_headers(
        headers,
        old_headers_only,
        new_headers_only,
        old_header_dir,
        new_header_dir,
    )
    # config_includes (the project .abicheck.yml compile.include_dirs
    # suffix, already folded into `includes` by the caller) must survive a
    # per-library-pair --old-include/--new-include override, which
    # otherwise fully replaces `includes` for that side -- so it is
    # re-appended explicitly here rather than relied on via `includes`
    # (Codex review, fresh evidence).
    old_inc = (
        list(old_includes_only) + list(config_includes)
        if old_includes_only
        else list(includes)
    )
    new_inc = (
        list(new_includes_only) + list(config_includes)
        if new_includes_only
        else list(includes)
    )
    old_inc.extend(_discover_include_roots(old_header_dir))
    new_inc.extend(_discover_include_roots(new_header_dir))
    matched_keys, removed_keys, added_keys, old_map, new_map = _match_release_keys(
        old_dir,
        new_dir,
        old_map,
        new_map,
        old_files,
        new_files,
        is_package,
    )
    _collect_release_warnings(
        warning_msgs,
        matched_keys,
        removed_keys,
        added_keys,
        old_map,
        new_map,
    )
    return (
        old_debug_dir,
        new_debug_dir,
        old_h,
        new_h,
        old_inc,
        new_inc,
        old_map,
        new_map,
        warning_msgs,
        matched_keys,
        removed_keys,
        added_keys,
    )
