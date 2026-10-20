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

"""The one release (directory/package) report builder (ADR-061 gap "six
separately-built report documents", release slice).

A release report used to be assembled four times over, once per format, by
``cli_compare_release_helpers``' ``_format_release_{summary,json,markdown,
junit}`` -- each re-deriving the display projection, the ``--view show=``
filtered counts, the exit block and the disposition audit on its own. This
module computes all of it **once**:

* :class:`ReleaseEvaluation` is the completed release evaluation a frontend
  hands over (live fan-out). Anything only a frontend can resolve -- the
  effective-config receipt reloads ``--policy-file``/``--suppress`` -- is
  passed in already resolved.
* :class:`StoredReleaseEvaluation` is the same hand-over for the stored
  ``BundleFacts`` operand (``compare OLD.bundle-facts.json NEW``).
* :func:`build_release_report` takes either operand to its typed view
  (:class:`ReleaseReport` / :class:`StoredReleaseReport`), and
  :func:`release_json_document` / :func:`stored_release_json_document` to the
  immutable :class:`~abicheck.report.document.ReportDocument`.

Verdicts are never re-derived here: the release fold is
``policy.release_outcome``'s (reached through ``workflows.gate``), the exit
block is ``resolve_release_exit_decision_for_report``'s. The renderers in
:mod:`abicheck.report.render_release_report` read this view and decide
nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from ..schemas.release_schema import RELEASE_SCHEMA_VERSION
from ..workflows.gate import (
    RELEASE_OPERATIONAL_SENTINELS,
    fold_release_outcome,
    release_global_verdicts,
    resolve_release_exit_decision_for_report,
)
from . import release_change_inventory as _inventory
from .comparison_scope import ComparisonScopeTerms
from .document import ReportDocument
from .release_assurance_section import ReleaseAssuranceTerms

if TYPE_CHECKING:
    from ..bundle_models import BundleDiffResult, BundleFinding
    from ..checker_types import DiffResult
    from ..model.change import Change
    from ..workflows.gate import SeverityConfig
    from .junit_inventory import JunitOldOperand
    from .release_assurance_section import ReleaseAssuranceDecision
    from .release_public_surface_section import ReleasePublicSurfaceTerms

__all__ = [
    "ReleaseEvaluation",
    "ReleaseReport",
    "StoredReleaseEvaluation",
    "StoredReleaseReport",
    "build_release_report",
    "evidence_contract_error_entries",
    "release_disposition_audit_block",
    "release_findings_for_render",
    "release_json_document",
    "stored_release_json_document",
    "stored_reported_verdict",
]


@dataclass(frozen=True)
class ReleaseEvaluation:
    """A completed live release evaluation, as a frontend hands it over.

    *scope_terms*/*assurance_terms* are the release's one resolved decision
    each (ADR-065 S2 / ADR-071). *effective_config* is the receipt's
    ``(digest, fields)`` pair -- resolved by the frontend because it reloads
    the policy/suppression files -- and is required only by the JSON (and
    hence HTML) projection; ``None`` for a format that renders no receipt.
    *library_results* is never mutated: a primary filtered render and a
    secondary unfiltered one may read the same list.
    """

    worst_verdict: str
    old_dir: Path
    new_dir: Path
    library_results: list[dict[str, object]]
    scope_terms: ComparisonScopeTerms
    assurance_terms: ReleaseAssuranceTerms
    removed_keys: list[str] = field(default_factory=list)
    added_keys: list[str] = field(default_factory=list)
    old_map: Mapping[str, Path] = field(default_factory=dict)
    new_map: Mapping[str, Path] = field(default_factory=dict)
    warning_msgs: list[str] = field(default_factory=list)
    diff_pairs: Sequence[tuple[DiffResult, JunitOldOperand]] | None = None
    bundle_result: BundleDiffResult | None = None
    matrix_result: DiffResult | None = None
    severity_config: SeverityConfig | None = None
    severity_exit_code: int | None = None
    contract_coverage_exit_contribution: int = 0
    contract_coverage_failure_count: int = 0
    fail_on_removed: bool = False
    env_matrix_source_sha256: str | None = None
    require_complete_analysis: bool = False
    public_surface: ReleasePublicSurfaceTerms | None = None
    effective_config: tuple[str, Mapping[str, str]] | None = None


@dataclass(frozen=True)
class ReleaseReport:
    """The typed release view every live-release renderer reads.

    ``display_*`` are the ``--view show=``-filtered projections (equal to the
    full ones when *show_only* is ``None``); ``filtered_counts`` is the
    ``release_filtered_summary`` both JSON and Markdown state, ``None`` when
    no filter is active. ``exit``/``outcome``/``disposition_audit`` are the
    already-resolved decisions, computed once for every format.
    """

    evaluation: ReleaseEvaluation
    show_only: str | None
    display_library_results: list[dict[str, object]]
    display_bundle_findings: list[BundleFinding]
    display_matrix_changes: list[Change]
    filtered_counts: dict[str, int] | None
    exit: dict[str, object]
    compatibility: str | None
    disposition_audit: dict[str, object]
    junit_error_libraries: list[dict[str, object]]


@dataclass(frozen=True)
class StoredReleaseEvaluation:
    """A completed stored-``BundleFacts`` comparison (``compare
    OLD.bundle-facts.json NEW``), as its frontend hands it over.

    *result* is the ``BundleFactsComparisonResult``; *scope_terms* is
    already resolved (``report.release_stored_scope.scope_terms_for``).
    """

    result: Any
    old_facts_path: Path
    new_dir: Path
    scope_terms: ComparisonScopeTerms
    new_is_stored: bool = False
    assurance_decision: ReleaseAssuranceDecision | None = None


@dataclass(frozen=True)
class StoredReleaseReport:
    """The typed view of a stored-operand release comparison."""

    evaluation: StoredReleaseEvaluation
    reported_verdict: str


def release_findings_for_render(
    library_results: Sequence[dict[str, object]], show_only: str | None
) -> list[dict[str, object]]:
    """Project *library_results* into the shape one render should see,
    without mutating the shared list.

    ``report.release_findings.project_release_library_results``
    stashes private per-library keys beside the always-full ``findings``/
    ``findings_truncated`` pair: ``findings_view``/``findings_view_truncated``/
    ``findings_view_truncated_kinds`` (present only when a ``--view show=``
    filter was active), ``impact_table_view`` (``--view impact``) and the
    uncapped ``findings_total_count``/``findings_total_count_view``
    accounting. When *show_only* is given the filtered view replaces the full
    one (the kind ledger with it, so a filtered list never carries the full
    diff's truncation breakdown); either way every private key is stripped so
    it never reaches a rendered report. Returns shallow copies.
    """
    result: list[dict[str, object]] = []
    for entry in library_results:
        if not isinstance(entry, dict):
            result.append(entry)
            continue
        projected = dict(entry)
        has_view = "findings_view" in projected
        view = projected.pop("findings_view", None)
        view_truncated = projected.pop("findings_view_truncated", False)
        view_truncated_kinds = projected.pop("findings_view_truncated_kinds", None)
        if show_only is not None and has_view:
            if view:
                projected["findings"] = view
            else:
                projected.pop("findings", None)
            if view_truncated:
                projected["findings_truncated"] = True
                if view_truncated_kinds:
                    projected["findings_truncated_kinds"] = view_truncated_kinds
                else:
                    projected.pop("findings_truncated_kinds", None)
            else:
                projected.pop("findings_truncated", None)
                projected.pop("findings_truncated_kinds", None)
        has_impact_view = "impact_table_view" in projected
        impact_view = projected.pop("impact_table_view", None)
        if show_only is not None and has_impact_view:
            if impact_view:
                projected["impact_table"] = impact_view
            else:
                projected.pop("impact_table", None)
        projected.pop("findings_total_count", None)
        projected.pop("findings_total_count_view", None)
        result.append(projected)
    return result


def _release_filtered_summary_counts(
    library_results: Sequence[dict[str, object]],
    bundle_result: BundleDiffResult | None,
    matrix_result: DiffResult | None,
    *,
    displayed_bundle_count: int,
    displayed_matrix_count: int,
) -> dict[str, int]:
    """``{"displayed", "total"}`` finding counts across every projection a
    ``--view show=`` selection was applied to (JSON's
    ``release_filtered_summary``, Markdown's ``> Filtered by:`` note).

    A separately-named field from scalar ``compare``'s ``filtered_summary``:
    that one is one ``DiffResult``'s severity buckets, which has no release
    aggregate. Library totals read each entry's *uncapped*
    ``findings_total_count``/``findings_total_count_view``, never the
    display-capped ``findings`` list, which under-reports past the cap.
    """

    def _as_count(value: object) -> int:
        return value if isinstance(value, int) else 0

    total_findings = sum(
        _as_count(lib.get("findings_total_count"))
        for lib in library_results
        if isinstance(lib, dict)
    )
    displayed_findings = sum(
        _as_count(lib.get("findings_total_count_view"))
        for lib in library_results
        if isinstance(lib, dict)
    )
    if bundle_result is not None:
        total_findings += len(bundle_result.bundle_findings)
        displayed_findings += displayed_bundle_count
    if matrix_result is not None:
        total_findings += len(matrix_result.changes)
        displayed_findings += displayed_matrix_count
    return {"displayed": displayed_findings, "total": total_findings}


def release_disposition_audit_block(
    library_results: Sequence[dict[str, object]],
    matrix_result: DiffResult | None = None,
    severity_config: SeverityConfig | None = None,
    bundle_result: BundleDiffResult | None = None,
) -> dict[str, object]:
    """ADR-067 C-S2: the release fan-out's own ``disposition_audit``.

    Folds every library's already-computed per-library block (an operational
    outcome that never reached a comparison carries none and contributes
    nothing), the release-global probe-matrix comparison's audit, and the
    cross-library bundle findings -- without the last, a release whose only
    change is a bundle finding reported ``detected_total: 0``, breaking D3's
    conservation rule. ``BundleDiffResult`` has no change buckets, so a real
    minimal ``DiffResult`` is built from ``bundle_findings.to_change()``
    under the bundle's own policy.
    """
    from ..checker_types import DiffResult as _DiffResult
    from .disposition_audit import (
        DispositionAudit,
        compute_disposition_audit,
        fold_disposition_audits,
    )

    audits: list[DispositionAudit] = []
    for lib in library_results:
        block = lib.get("disposition_audit")
        if isinstance(block, dict):
            audits.append(DispositionAudit.from_dict(block))
    if matrix_result is not None:
        audits.append(compute_disposition_audit(matrix_result, severity_config))
    if bundle_result is not None and bundle_result.bundle_findings:
        bundle_as_diff_result = _DiffResult(
            old_version="",
            new_version="",
            library="<bundle>",
            changes=[f.to_change() for f in bundle_result.bundle_findings],
            verdict=bundle_result.bundle_verdict,
            policy=bundle_result.policy,
            policy_file=bundle_result.policy_file,
        )
        audits.append(compute_disposition_audit(bundle_as_diff_result, severity_config))
    return fold_disposition_audits(audits).to_dict()


def evidence_contract_error_entries(
    library_results: Sequence[object],
) -> list[dict[str, object]]:
    """``{"library", "error"}`` entries for members short of the pinned
    ``--depth`` rung (ADR-064's evidence-contract axis, exit 7).

    Fed to JUnit's ``error_libraries``: the XML is written before the exit is
    taken, so without this a release exiting 7 rendered ``failures="0"
    errors="0"`` (Codex review).
    """
    return [
        {
            "library": str(entry.get("library")),
            "error": (
                "Requested --depth evidence was not reached for this member; "
                "its findings rest on shallower evidence than was asked for. "
                "Contributes 7 to the release exit code "
                "(evidence-contract axis)."
            ),
        }
        for entry in library_results
        if isinstance(entry, dict) and entry.get("evidence_contract_error_contribution")
    ]


def _junit_error_libraries(
    library_results: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """The error testsuites JUnit renders: an ``ERROR``/``not_comparable``
    member (ADR-050 D2 -- ``reason`` carries its message) plus a member
    short of the pinned ``--depth`` rung. An ``unsupported``/``failed``
    member is the scope suite's to render, never listed twice."""
    error_libs: list[dict[str, object]] = [
        {**entry, "error": entry.get("reason", entry["verdict"])}
        if entry.get("verdict") == "not_comparable"
        else entry
        for entry in library_results
        if entry.get("verdict") in ("ERROR", "not_comparable")
    ]
    already = {entry.get("library") for entry in error_libs}
    error_libs += [
        entry
        for entry in evidence_contract_error_entries(library_results)
        if entry["library"] not in already
    ]
    return error_libs


def _build_live(evaluation: ReleaseEvaluation, show_only: str | None) -> ReleaseReport:
    from ..reporter_markdown import (
        release_bundle_findings_for_view,
        release_matrix_changes_for_view,
    )
    from ..workflows.release_scope import release_global_ran

    ev = evaluation
    library_results = ev.library_results
    display_bundle = (
        release_bundle_findings_for_view(ev.bundle_result, show_only)
        if ev.bundle_result is not None
        else []
    )
    display_matrix = (
        release_matrix_changes_for_view(ev.matrix_result, show_only)
        if ev.matrix_result is not None
        else []
    )
    terms = ev.scope_terms
    outcome = fold_release_outcome(
        (str(entry.get("verdict", "NO_CHANGE")) for entry in library_results),
        release_global_verdicts(ev.bundle_result, ev.matrix_result),
        global_ran=release_global_ran(ev.bundle_result, ev.matrix_result, terms.record),
    )
    # The release's own assurance gate is threaded into every decision a
    # reader sees, not just the process exit (Codex review, PR #1238, P1).
    exit_dict = resolve_release_exit_decision_for_report(
        ev.worst_verdict,
        ev.fail_on_removed,
        ev.removed_keys,
        ev.severity_exit_code,
        ev.contract_coverage_exit_contribution,
        library_results,
        outcome.global_verdict,
        incomplete_scope_contribution=terms.decision.incomplete_scope_exit_contribution,
        no_comparison_completed_contribution=terms.decision.no_comparison_completed_exit_contribution,
        require_complete_analysis=ev.require_complete_analysis,
    ).to_dict()
    return ReleaseReport(
        evaluation=ev,
        show_only=show_only,
        display_library_results=release_findings_for_render(library_results, show_only),
        display_bundle_findings=list(display_bundle),
        display_matrix_changes=list(display_matrix),
        filtered_counts=(
            _release_filtered_summary_counts(
                library_results,
                ev.bundle_result,
                ev.matrix_result,
                displayed_bundle_count=len(display_bundle),
                displayed_matrix_count=len(display_matrix),
            )
            if show_only
            else None
        ),
        exit=exit_dict,
        compatibility=outcome.compatibility,
        disposition_audit=release_disposition_audit_block(
            library_results, ev.matrix_result, ev.severity_config, ev.bundle_result
        ),
        junit_error_libraries=_junit_error_libraries(library_results),
    )


def stored_reported_verdict(result: Any) -> str:
    """A stored-operand comparison's reported verdict: the release-outcome
    fold over its sentinels and verdict -- ``"not_comparable"`` (ADR-050 D2)
    when a matched pair's contracts disagreed, else ``"ERROR"`` when a NEW
    member failed extraction (ADR-065 D1), else the verdict. The ranking is
    ``policy.release_outcome``'s; the exit and the document both read this."""
    sentinels = [
        *(["not_comparable"] if result.not_comparable_members else []),
        *(["ERROR"] if result.extraction_failures else []),
    ]
    return fold_release_outcome(sentinels, [result.verdict.value]).reported_verdict


@overload
def build_release_report(
    operand: ReleaseEvaluation, *, show_only: str | None = None
) -> ReleaseReport: ...
@overload
def build_release_report(
    operand: StoredReleaseEvaluation, *, show_only: str | None = None
) -> StoredReleaseReport: ...
def build_release_report(
    operand: ReleaseEvaluation | StoredReleaseEvaluation,
    *,
    show_only: str | None = None,
) -> ReleaseReport | StoredReleaseReport:
    """Build the one release report view for either operand.

    The live fan-out and the stored ``BundleFacts`` operand both come through
    here; each yields its own typed view because the two documents differ in
    shape today (``docs/contribute/known-gaps.md``, "Release report: live and
    stored operands render different documents"). *show_only* applies to the
    live operand only -- the stored path has no ``--view show=`` projection.
    """
    if isinstance(operand, StoredReleaseEvaluation):
        return StoredReleaseReport(
            evaluation=operand,
            reported_verdict=stored_reported_verdict(operand.result),
        )
    return _build_live(operand, show_only)


def _release_json_scope(scoped_libs: list[dict[str, object]]) -> dict[str, object]:
    """The release-level public-surface scoping rollup (ADR-024)."""

    def _as_int(v: object) -> int:
        return v if isinstance(v, int) else 0

    return {
        "public_headers_applied": True,
        "manual_review_required": any(
            not bool(lib.get("scope_resolved", True)) for lib in scoped_libs
        ),
        "public_additions": sum(
            _as_int(lib.get("compatible_additions", 0)) for lib in scoped_libs
        ),
        "filtered_internal_changes": sum(
            _as_int(lib.get("filtered_internal_count", 0)) for lib in scoped_libs
        ),
    }


def release_json_document(report: ReleaseReport) -> ReportDocument:
    """The release JSON document (``release_schema_version``:
    :data:`~abicheck.schemas.release_schema.RELEASE_SCHEMA_VERSION`).

    Key order is part of the published bytes; every optional key follows the
    "present only when active" convention, so a release without a setting is
    byte-identical to one produced before that setting existed.
    ``unmatched_old``/``unmatched_new`` are read off the acquisition record
    (ADR-065 S4), never a set difference; ``removed_keys`` are the proven
    set ``exit`` reads.
    """
    from ..workflows.release_scope import unmatched_names
    from .not_comparable import run_outcome_dict_for_release

    ev = report.evaluation
    library_results = ev.library_results
    terms = ev.scope_terms
    exit_dict = report.exit
    record = terms.record
    changed_libraries = [
        str(lib["library"])
        for lib in library_results
        if str(lib.get("verdict")) != "NO_CHANGE"
        and str(lib.get("verdict")) not in RELEASE_OPERATIONAL_SENTINELS
    ]
    summary: dict[str, object] = {
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "verdict": ev.worst_verdict,
        "old_dir": str(ev.old_dir),
        "new_dir": str(ev.new_dir),
        "libraries": report.display_library_results,
        "changed_libraries": changed_libraries,
        "unmatched_old": unmatched_names(record, side="old") if record else [],
        "unmatched_new": unmatched_names(record, side="new") if record else [],
        "warnings": ev.warning_msgs,
        "exit": exit_dict,
        "run_outcome": run_outcome_dict_for_release(
            report.compatibility,
            exit_dict,
            scope=terms.completeness,
        ),
    }
    if terms.section is not None:
        summary["comparison_scope"] = terms.section
    if (folded := _inventory.release_change_inventory(library_results)) is not None:
        summary["change_inventory"] = folded
    severity_config = ev.severity_config
    if severity_config is not None:
        # Escalate to 4 when the removed-required-library axis drives
        # run_outcome.gate, so severity.exit_code never contradicts it
        # (GateInfo.from_report_data fails closed on that disagreement).
        removed_lib_contribution = exit_dict.get(
            "removed_required_library_contribution"
        )
        escalated_exit_code = (
            max(ev.severity_exit_code or 0, 4)
            if isinstance(removed_lib_contribution, int)
            and removed_lib_contribution != 0
            else ev.severity_exit_code
        )
        summary["severity"] = {
            "config": {
                "abi_breaking": severity_config.abi_breaking.value,
                "potential_breaking": severity_config.potential_breaking.value,
                "quality_issues": severity_config.quality_issues.value,
                "addition": severity_config.addition.value,
            },
            "exit_code": escalated_exit_code,
        }
    a_terms = ev.assurance_terms
    if a_terms.section is not None:
        # ADR-071 D6; the top-level contribution is the key report schema
        # 2.40 defines and aggregate/the Action's deferred gate read.
        summary["analysis_assurance"] = a_terms.section
        summary["analysis_assurance_exit_contribution"] = (
            a_terms.decision.exit_contribution
        )
    if any("contract_coverage_exit_contribution" in lib for lib in library_results):
        # ADR-049 Phase 7; the failure count stays real even when
        # `contract.unresolved: warn` zeroes the contribution.
        summary["contract_coverage_exit_contribution"] = (
            ev.contract_coverage_exit_contribution
        )
        summary["contract_coverage_failure_count"] = ev.contract_coverage_failure_count
    if ev.env_matrix_source_sha256 is not None:
        # Computed once at release scope, so a release with zero completed
        # pairs still records a configured `deployment:` contract.
        summary["env_matrix_source_sha256"] = ev.env_matrix_source_sha256
    if ev.public_surface is not None:
        summary["public_surface_reconciliation"] = ev.public_surface.to_dict()
    scoped_libs = [lib for lib in library_results if "scope_resolved" in lib]
    if scoped_libs:
        summary["scope"] = _release_json_scope(scoped_libs)
    bundle_result = ev.bundle_result
    if bundle_result is not None:
        summary["bundle_verdict"] = bundle_result.bundle_verdict.value
        summary["bundle_findings"] = [
            {
                "kind": f.kind.value,
                "symbol": f.symbol,
                "consumer_library": f.consumer_library,
                "provider_library": f.provider_library,
                "description": f.description,
                "old_value": f.old_value,
                "new_value": f.new_value,
                "affected_libraries": list(f.affected_libraries),
            }
            for f in report.display_bundle_findings
        ]
        # G38 P0-D: a partially failed bundle analysis is structural.
        if bundle_result.analysis_errors:
            summary["bundle_analysis_errors"] = list(bundle_result.analysis_errors)
    matrix_result = ev.matrix_result
    if matrix_result is not None:
        # `matrix_verdict` stays the real, unfiltered verdict.
        summary["matrix_verdict"] = matrix_result.verdict.value
        summary["matrix_findings"] = [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "old_value": c.old_value,
                "new_value": c.new_value,
            }
            for c in report.display_matrix_changes
        ]
    if report.show_only:
        summary["show_only_filter"] = report.show_only
        summary["release_filtered_summary"] = report.filtered_counts
    if ev.effective_config is None:
        raise ValueError(
            "the release JSON document needs the frontend-resolved effective_config"
        )
    digest, fields = ev.effective_config
    summary["effective_config_digest"] = digest
    summary["effective_config_fields"] = dict(fields)
    # ADR-067 C-S2: unconditional -- raw-versus-effective is never dropped.
    summary["disposition_audit"] = report.disposition_audit
    return ReportDocument.from_mapping(summary)


def stored_release_json_document(report: StoredReleaseReport) -> ReportDocument:
    """The stored-``BundleFacts`` comparison's JSON document (``mode:
    bundle_facts``) -- a different shape from :func:`release_json_document`;
    see :func:`build_release_report`."""
    from ..reporter import (
        to_json,
    )
    from .change_json import (
        prewarm_change_demangling,
        resolve_demangled_symbol as _resolve_demangled_symbol,
    )
    from .release_stored_scope import json_scope_fields
    from .release_stored_sections import assurance_sections, public_surface_terms
    from .run_outcome import run_outcome_dict_for_diff_result

    ev = report.evaluation
    result = ev.result
    # One batched demangle for the whole findings list: without it a host
    # with no in-process `cxxfilt` forks a subprocess per distinct symbol.
    prewarm_change_demangling(
        type("_Findings", (), {"changes": list(result.bundle_findings)})()
    )
    import json

    _require = bool(ev.assurance_decision and ev.assurance_decision.require_complete)
    # perf-ok: to_json is the canonical per-library serializer; one round trip per library
    libraries = {
        diff.library: json.loads(to_json(diff, require_complete_analysis=_require))
        for diff in result.per_library
    }
    run_outcome = run_outcome_dict_for_diff_result(result, None, None)
    summary: dict[str, object] = {
        "mode": "bundle_facts",
        "old_bundle_facts": str(ev.old_facts_path),
        # `new_dir` keeps its key even when NEW is itself stored;
        # `new_is_stored` is the additive signal telling the shapes apart.
        "new_dir": str(ev.new_dir),
        "new_is_stored": ev.new_is_stored,
        "verdict": report.reported_verdict,
        "per_library_verdict": result.per_library_verdict.value,
        "bundle_verdict": result.bundle_verdict.value,
        **json_scope_fields(ev.scope_terms, run_outcome, result),
        "libraries": libraries,
        # Every machine projection carries both names (Codex review, PR #1284).
        "bundle_findings": [
            {
                "kind": f.kind.value,
                "symbol": f.symbol,
                **(
                    {"demangled_symbol": demangled}
                    if (demangled := _resolve_demangled_symbol(f))
                    else {}
                ),
                "consumer_library": f.consumer_library,
                "provider_library": f.provider_library,
                "description": f.description,
                "old_value": f.old_value,
                "new_value": f.new_value,
                "affected_libraries": list(f.affected_libraries),
            }
            for f in result.bundle_findings
        ],
        "analysis_errors": list(result.analysis_errors),
    }
    summary.update(assurance_sections(ev.assurance_decision))
    if (_surface := public_surface_terms(result)) is not None:
        summary["public_surface_reconciliation"] = _surface.to_dict()
    if result.env_matrix_source_sha256 is not None:
        summary["env_matrix_source_sha256"] = result.env_matrix_source_sha256
    return ReportDocument.from_mapping(summary)
