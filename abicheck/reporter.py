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

"""Reporter — DiffResult → JSON / Markdown / stat output."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .severity import KindSets, SeverityConfig
from .checker import (
    Change,
    DiffResult,
    Verdict,
)
from .checker_policy import (
    ChangeKind,
    EvidenceStatus,
    HasKind,
    evidence_status_for_change,
    impact_for,
    policy_kind_sets as _policy_kind_sets,
)
from .checker_types import validate_check_id, validate_evidence_depth
from .impact import assess_change
from .report_model import VERDICT_TO_SEVERITY_LABEL as _VERDICT_TO_SEVERITY_LABEL
from .report_summary import build_summary, surface_breakdown

# Markdown rendering + the shared --show-only filter and verdict-label maps now
# live in the leaf module reporter_markdown (it imports nothing from here). Kept
# importable under their historical names so the public API is unchanged.
from .reporter_markdown import (
    _ADDITION_ICON as _ADDITION_ICON,
    _BREAKING_ICON as _BREAKING_ICON,
    _BUMP_EMOJI as _BUMP_EMOJI,
    _QUALITY_ICON as _QUALITY_ICON,
    _RISK_ICON as _RISK_ICON,
    _SEVERITY_EMOJI as _SEVERITY_EMOJI,
    _SOURCE_BREAK_ICON as _SOURCE_BREAK_ICON,
    _VERDICT_EMOJI as _VERDICT_EMOJI,
    _VERDICT_LABEL as _VERDICT_LABEL,
    _VERDICT_MERGE_EFFECT as _VERDICT_MERGE_EFFECT,
    ShowOnlyFilter as ShowOnlyFilter,
    _append_confidence_section as _append_confidence_section,
    _append_policy_section as _append_policy_section,
    _append_recommendation_section as _append_recommendation_section,
    _append_redundancy_note as _append_redundancy_note,
    _append_suppression_note as _append_suppression_note,
    _build_impact_table as _build_impact_table,
    _build_internal_rtti_note as _build_internal_rtti_note,
    _build_leaf_type_sections as _build_leaf_type_sections,
    _build_library_files_section as _build_library_files_section,
    _build_severity_sections as _build_severity_sections,
    _build_severity_summary_md as _build_severity_summary_md,
    _fmt_size as _fmt_size,
    _footer_lines as _footer_lines,
    _format_change_md as _format_change_md,
    _format_leaf_type_change as _format_leaf_type_change,
    _section_severity_label as _section_severity_label,
    _to_markdown_leaf as _to_markdown_leaf,
    apply_show_only as apply_show_only,
    operation_for_kind as operation_for_kind,
    to_markdown as to_markdown,
    to_review_digest as to_review_digest,
    to_stat as to_stat,
)
from .schemas import REPORT_SCHEMA_VERSION
from .semver import recommend_release


def _effective_severity_label(
    c: object,
    kind_sets: tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ],
    *,
    policy: str | None = None,
    policy_file: object | None = None,
) -> str:
    """Severity label for a change, honouring its A4 ``effective_verdict``.

    The one place the reporter decides a finding's severity bucket: routes
    through :func:`effective_verdict_for_change` (the same call
    :func:`_change_to_dict` already makes) so an ADR-027 pattern-aware
    demotion, *and* a per-change frozen-namespace floor guarding a
    ``policy_file`` kind-level override, both read consistently with the
    verdict and exit code. Without *policy_file* here, a leaf-mode root-type
    change tagged ``frozen_namespace_violation`` could read "compatible" in
    ``leaf_changes`` while the top-level ``severity`` block (which does pass
    ``policy_file``) correctly reports it as blocking the gate — a direct,
    visible contradiction on the same JSON document (Codex review on #549).
    """
    kind = getattr(c, "kind", None)
    if not isinstance(kind, ChangeKind):
        return "unknown"
    from .severity import effective_verdict_for_change

    verdict = effective_verdict_for_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    return _VERDICT_TO_SEVERITY_LABEL.get(verdict, "unknown")


def _kind_to_severity(kind: ChangeKind, policy: str) -> str:
    """Map a ChangeKind to its severity label under the given policy (FIX-G)."""
    breaking, api_break, compatible, risk = _policy_kind_sets(policy)
    if kind in breaking:
        return "breaking"
    if kind in api_break:
        return "api_break"
    if kind in risk:
        return "risk"
    if kind in compatible:
        return "compatible"
    return "unknown"


def to_stat_json(
    result: DiffResult,
    indent: int = 2,
    *,
    severity_config: SeverityConfig | None = None,
) -> str:
    """JSON output for --stat mode: summary only, no changes array.

    *severity_config*, when given, adds a ``severity`` block (same shape as
    the full JSON report's — see :func:`_build_severity_json`) so ``--stat
    --format json`` reflects the actual severity-aware gate instead of only
    the compatibility verdict. Without it, ``--stat`` output has historically
    bypassed severity handling entirely (it short-circuits in
    ``service.render_output`` before format dispatch).
    """
    summary = build_summary(result)
    effective_policy = result.policy or "strict_abi"
    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
        "policy": effective_policy,
        "summary": {
            "breaking": summary.breaking,
            "source_breaks": summary.source_breaks,
            "risk_changes": summary.risk_count,
            "compatible_additions": summary.compatible_additions,
            "total_changes": summary.total_changes,
            "binary_compatibility_pct": round(summary.binary_compatibility_pct, 1),
            "affected_pct": round(summary.affected_pct, 1),
        },
    }
    _add_check_identity(d, result)
    if severity_config is not None:
        d["severity"] = _build_severity_json(
            result.changes,
            severity_config,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    d["release_recommendation"] = recommend_release(result).to_dict()
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # Confidence & evidence metadata
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)
    return json.dumps(d, indent=indent)


def _add_surface_scope(d: dict[str, object], result: DiffResult) -> None:
    """Attach the ADR-024 §D4/D5 public-surface scope ledger to a JSON dict.

    When header scoping is active, findings that fall outside the public ABI
    surface are demoted to this audit ledger rather than dropped — disclosed
    here (not just on stderr) so the "why was this excluded" trail is
    machine-readable. Shared by the full and leaf JSON paths so both formats
    carry the ledger consistently.
    """
    if not result.scope_to_public_surface:
        return
    d["surface_scope"] = {
        "enabled": True,
        # ADR-024 §D5.3 — structured confidence in the resolution itself.
        "confidence": result.surface_scope_confidence,
        "notes": list(result.surface_scope_notes),
        "out_of_surface_count": result.out_of_surface_count,
        "out_of_surface_changes": [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "source_location": c.source_location,
                "reason": getattr(c, "surface_exclusion_reason", None),
            }
            for c in result.out_of_surface_changes
        ],
    }


def _add_reconciled(d: dict[str, object], result: DiffResult) -> None:
    """Attach the ADR-039 build-context reconciliation ledger to a JSON dict.

    Findings cleared as context-free header-parse artifacts are disclosed here —
    not just dropped from the verdict — so the "why was this removed" trail is
    machine-readable. Independent of surface scoping (reconciliation can run
    without ``--scope-public-headers``); emitted only when something was cleared.
    """
    if not result.reconciled_changes:
        return
    d["build_context_reconciled"] = {
        "count": result.reconciled_count,
        "changes": [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "source_location": c.source_location,
                "reason": getattr(c, "surface_exclusion_reason", None),
            }
            for c in result.reconciled_changes
        ],
    }


def _to_json_leaf(
    result: DiffResult,
    indent: int = 2,
    show_only: str | None = None,
    *,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Leaf-change mode JSON output.

    *severity_config*, when given, adds the same top-level ``severity`` block
    the full-mode JSON report has (see :func:`_build_severity_json`) —
    without it, ``--report-mode leaf`` returned before that block was ever
    built, so it silently had no severity information even when a caller
    passed ``severity_config`` through :func:`to_json`.
    """
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    summary = build_summary(result)
    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    type_changes = [c for c in changes if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in changes if c.kind not in _ROOT_TYPE_CHANGE_KINDS]

    effective_policy = result.policy or "strict_abi"
    eff_sets = result._effective_kind_sets()

    def _leaf_entry(c: Change) -> dict[str, object]:
        entry: dict[str, object] = {
            "kind": c.kind.value,
            "symbol": c.symbol,
            "description": c.description,
            "severity": _effective_severity_label(
                c,
                eff_sets,
                policy=result.policy,
                policy_file=result.policy_file,
            ),
            # Schema 2.3/2.4 fields (Codex review on #557): _leaf_entry builds
            # its own dict rather than routing through _change_to_dict, so
            # root type changes in leaf_changes[]/changes[] were missing
            # operation/finding_id/recommended_action even though non-type
            # leaf entries (via _change_to_dict below) and full-mode entries
            # all have them — breaking a consumer relying on finding_id
            # correlation across --report-mode leaf and full-mode reports.
            "operation": operation_for_kind(c.kind.value),
            "finding_id": _finding_id(c),
            "recommended_action": _recommended_action_for_change(
                c,
                policy=result.policy,
                kind_sets=eff_sets,
                policy_file=result.policy_file,
            ),
            "affected_count": len(c.affected_symbols) if c.affected_symbols else 0,
            "affected_symbols": c.affected_symbols or [],
            "caused_count": c.caused_count,
            "old_value": getattr(c, "old_value", None),
            "new_value": getattr(c, "new_value", None),
        }
        reviewer_action = _reviewer_action_for_change(
            c,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
        if reviewer_action is not None:
            entry["reviewer_action"] = reviewer_action
        evidence_status = evidence_status_for_change(cast(HasKind, c))
        if evidence_status is not None:
            entry["evidence_status"] = evidence_status.value
        # ADR-027 A4: keep the modulation audit trail in leaf mode too, so a
        # demoted root type change still explains *why* it reads compatible.
        mod_reason = getattr(c, "modulation_reason", None)
        if mod_reason:
            entry["modulation_reason"] = mod_reason
            entry["modulation_rule"] = getattr(c, "modulation_rule", None)
            eff = getattr(c, "effective_verdict", None)
            if isinstance(eff, Verdict):
                entry["effective_verdict"] = eff.value
        # ADR-044 P1 item 4: same structured reachability fields
        # _change_to_dict adds for non-type changes — a root TYPE_* change is
        # exactly the category the layout-reachability walk tags most often.
        if getattr(c, "public_reachable", False):
            entry["public_reachable"] = True
            reach_kind = getattr(c, "reachability_kind", None)
            if reach_kind:
                entry["reachability_kind"] = reach_kind
            proof_path = getattr(c, "reachability_proof_path", None)
            if proof_path:
                entry["reachability_proof_path"] = proof_path
        # G29 Phase 3 slice 1 (ADR-051, Codex review): _leaf_entry duplicates
        # _change_to_dict's reachability fields rather than routing through
        # it (see the ADR-044 block above) -- reachability_state/
        # impact_assessment follow the same precedent so a root TYPE_*
        # change (exactly the category the layout-reachability walk tags
        # most often) doesn't lose them in --report-mode leaf.
        assessment = assess_change(c)
        entry["reachability_state"] = assessment.reachability_state.value
        if assessment.has_signal():
            entry["impact_assessment"] = assessment.to_dict()
        return entry

    leaf_changes_list = [_leaf_entry(c) for c in type_changes]
    non_type_list = [
        _change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
        for c in non_type_changes
    ]

    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
        "policy": effective_policy,
        "summary": {
            "breaking": summary.breaking,
            "source_breaks": summary.source_breaks,
            "risk_changes": summary.risk_count,
            "compatible_additions": summary.compatible_additions,
            "total_changes": summary.total_changes,
        },
        "leaf_changes": leaf_changes_list,
        "non_type_changes": non_type_list,
        # FIX-H: populate changes with union for backward-compat consumers
        "changes": leaf_changes_list + non_type_list,
    }
    _add_check_identity(d, result)
    if severity_config is not None:
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    # Release recommendation — always present in JSON, including leaf mode.
    d["release_recommendation"] = recommend_release(result).to_dict()
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # ADR-027 A4 — pattern-aware modulation ledger, carried in leaf mode too.
    if result.pattern_modulations:
        d["pattern_modulations"] = result.pattern_modulations
    # Confidence & evidence metadata
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    scope = _scope_dict(result)
    if scope is not None:
        d["scope"] = scope
    return json.dumps(d, indent=indent)


def _root_cause_key_and_display(
    caused_by_type: str | None,
    symbol: str | None,
    kind_value: str,
    finding_id: str,
) -> tuple[str, str]:
    """Grouping key + display root for one root-cause finding: ``caused_by_type``
    when set, else a non-empty ``symbol``, else a unique per-finding key (an
    empty-symbol/no-caused_by_type fallback would wrongly collapse unrelated
    aggregate findings onto one shared ``""`` group). Shared by
    :func:`_to_json_root_cause` and the scoped-gate fold-in in
    ``cli_compare_fold.py``, which appends synthetic findings afterwards.
    """
    if caused_by_type:
        return caused_by_type, caused_by_type
    if symbol:
        return symbol, symbol
    return f"finding:{finding_id}", kind_value


def _add_entries_to_root_causes(
    d: dict[str, object],
    keyed_entries: list[tuple[str, str, dict[str, object]]],
) -> None:
    """Fold additional ``(key, root_display, entry)`` triples into an
    already-built ``--report-mode root-cause`` payload, for synthetic
    scoped-gate entries computed after :func:`_to_json_root_cause` already
    grouped ``result.changes`` (else they'd sit in ``changes[]`` but never in
    ``root_causes``). No-op if *d* has no ``root_causes`` list.
    """
    root_causes = d.get("root_causes")
    if not isinstance(root_causes, list):
        return
    by_id = {
        group["root_cause_id"]: group
        for group in root_causes
        if isinstance(group, dict)
    }
    for key, root_display, entry in keyed_entries:
        root_cause_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        group = by_id.get(root_cause_id)
        if group is None:
            group = {
                "root_cause_id": root_cause_id,
                "root": root_display,
                "finding_count": 0,
                "findings": [],
            }
            root_causes.append(group)
            by_id[root_cause_id] = group
        group["findings"].append(entry)
        group["finding_count"] = len(group["findings"])
    d["root_cause_count"] = len(root_causes)


def _to_json_root_cause(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
) -> str:
    """``--report-mode root-cause`` JSON output (G29 Phase 3, ADR-051 slice 3).

    Groups ``result.changes`` (after ``--show-only`` filtering) by
    ``Change.caused_by_type`` when set, else each change is its own
    singleton group keyed by its own ``symbol`` -- reusing the existing
    ``caused_by_type`` field ``diff_filtering.py``'s redundancy collapse and
    ``internal_leak.py``'s call-graph-leak overlay already set, rather than
    requiring new producer wiring. This is a first, JSON-only slice of the
    plan's root-cause grouping: the full `RootCauseCorrelator` (G29 Phase 6)
    will additionally correlate across consumer-overlay findings that don't
    share a `caused_by_type` today; `root_cause_id` here is a stable hash of
    the grouping key, not the eventual correlator's own identifier scheme
    (ADR-051, "Deliberately not implemented this slice").
    """
    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    effective_policy = result.policy or "strict_abi"
    eff_sets = result._effective_kind_sets()

    # Build each finding's dict exactly once; group the same dict objects by
    # key so `changes` (flat, backward-compatible -- every existing report
    # mode provides it, `_to_json_leaf` included) and `root_causes[].findings`
    # never drift from each other.
    entries = [
        _change_to_dict(
            c, policy=effective_policy, kind_sets=eff_sets, policy_file=result.policy_file
        )
        for c in changes
    ]
    groups: dict[str, list[dict[str, object]]] = {}
    roots: dict[str, str] = {}
    order: list[str] = []
    for c, entry in zip(changes, entries):
        key, root_display = _root_cause_key_and_display(
            c.caused_by_type, c.symbol, c.kind.value, str(entry["finding_id"])
        )
        if key not in groups:
            groups[key] = []
            roots[key] = root_display
            order.append(key)
        groups[key].append(entry)

    root_causes = [
        {
            "root_cause_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            "root": roots[key],
            "finding_count": len(groups[key]),
            "findings": groups[key],
        }
        for key in order
    ]

    d = _build_json_base(result)
    _add_abi_surface_breakdown(d, result)
    _add_evidence_fields(d, result)
    d["policy"] = effective_policy
    if show_only:
        _add_show_only_filter(d, result, changes, show_only)
    if severity_config is not None:
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    d["changes"] = entries
    d["root_causes"] = root_causes
    d["root_cause_count"] = len(root_causes)
    _add_suppression(d, result)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    _add_detectors(d, result)
    _add_confidence_evidence(d, result)
    _add_policy_overrides(d, result)
    return json.dumps(d, indent=indent)


def _metadata_dict(meta: object | None) -> dict[str, object] | None:
    if meta is None:
        return None
    return {
        "path": getattr(meta, "path", ""),
        "sha256": getattr(meta, "sha256", ""),
        "size_bytes": getattr(meta, "size_bytes", 0),
    }


def _scope_dict(result: DiffResult) -> dict[str, object] | None:
    """Machine-readable public-surface scoping block (ADR-024, issue #235).

    Only emitted when ``--scope-public-headers`` was requested, so default
    reports are unchanged. Records whether scoping resolved or fell back to the
    full export table (``manual_review_required``), the public additions count,
    and the audit ledger of findings filtered as internal/private.
    """
    if not result.scope_to_public_surface:
        return None
    summary = build_summary(result)
    return {
        "public_headers_applied": True,
        "resolved": result.scope_resolved,
        "fell_back": not result.scope_resolved,
        "manual_review_required": not result.scope_resolved,
        "public_additions": summary.compatible_additions,
        "filtered_internal_count": result.out_of_surface_count,
        "filtered_internal_changes": [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
            }
            for c in result.out_of_surface_changes
        ],
    }


def _add_check_identity(d: dict[str, object], result: DiffResult) -> None:
    """Add the ADR-047 §7 report-identity envelope fields (G30 P0.3).

    Each field is omitted entirely when unset — additive, and nothing
    populates these yet (the GitHub Actions integration-model primitives
    that will are G30 P1 work), so a report with none of them set looks
    identical to one from before this schema version.
    """
    if result.check_id is not None:
        validate_check_id(result.check_id)
        d["check_id"] = result.check_id
    if result.profile_id is not None:
        d["profile_id"] = result.profile_id
    if result.requested_depth is not None:
        validate_evidence_depth("requested_depth", result.requested_depth)
        d["requested_depth"] = result.requested_depth
    if result.effective_depth is not None:
        validate_evidence_depth("effective_depth", result.effective_depth)
        d["effective_depth"] = result.effective_depth
    if result.baseline_channel is not None:
        d["baseline_channel"] = result.baseline_channel


def _build_json_base(result: DiffResult) -> dict[str, object]:
    """Build the opening header + summary block of the JSON report dict."""
    summary = build_summary(result)
    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
    }
    _add_check_identity(d, result)
    # Library file metadata (path, SHA-256, size) — always present for schema consistency
    d["old_file"] = _metadata_dict(getattr(result, "old_metadata", None))
    d["new_file"] = _metadata_dict(getattr(result, "new_metadata", None))
    d["summary"] = {
        "breaking": summary.breaking,
        "source_breaks": summary.source_breaks,
        "risk_changes": summary.risk_count,
        "compatible_additions": summary.compatible_additions,
        "total_changes": summary.total_changes,
        "binary_compatibility_pct": round(summary.binary_compatibility_pct, 1),
        "affected_pct": round(summary.affected_pct, 1),
    }
    return d


def _add_abi_surface_breakdown(d: dict[str, object], result: DiffResult) -> None:
    """Conditionally add ABI surface breakdown of the breaking set.

    Only present when there are RTTI/internal-namespace changes — additive,
    machine-facing.
    """
    _bd = surface_breakdown(result.breaking)
    if _bd.rtti or _bd.internal:
        d["abi_surface_breakdown"] = {
            "breaking_total": _bd.total,
            "public": _bd.public,
            "rtti_churn": _bd.rtti,
            "internal_churn": _bd.internal,
        }


def _add_evidence_fields(d: dict[str, object], result: DiffResult) -> None:
    """Add release recommendation, optional evidence coverage/metrics, and policy."""
    # Release recommendation (semver bump + soname action) — additive, machine-facing.
    d["release_recommendation"] = recommend_release(result).to_dict()
    # Evidence coverage (ADR-028 D7) — L0–L5 rows when a BuildSourcePack was
    # supplied; lets consumers tell artifact-proven from build-context-only
    # findings. Additive, present only when evidence was involved.
    if getattr(result, "layer_coverage", None):
        d["layer_coverage"] = result.layer_coverage
    # Evidence metrics (ADR-033 D6/D9) — collection timing + finding split, when
    # build-info/source facts were involved. Additive; lets CI tune mode choice.
    if getattr(result, "evidence_metrics", None):
        d["evidence_metrics"] = result.evidence_metrics


def _add_show_only_filter(
    d: dict[str, object],
    result: DiffResult,
    changes: list[Change],
    show_only: str,
) -> None:
    """Add show_only_filter + filtered_summary when a show_only filter is active."""
    d["show_only_filter"] = show_only
    d["filtered_summary"] = {
        "breaking": sum(
            1
            for c in changes
            if result._effective_verdict_for_change(c) == Verdict.BREAKING
        ),
        "source_breaks": sum(
            1
            for c in changes
            if result._effective_verdict_for_change(c) == Verdict.API_BREAK
        ),
        "risk_changes": sum(
            1
            for c in changes
            if result._effective_verdict_for_change(c) == Verdict.COMPATIBLE_WITH_RISK
        ),
        "total_changes": len(changes),
    }


def _suppressed_change_entry(c: Change) -> dict[str, object]:
    """Minimal audit-trail entry for one suppressed change, plus the
    impact-assessment decision it was actually suppressed with (G29 Phase 3
    slice 1, ADR-051 follow-up, Codex review: this is the one call site that
    passes ``suppressed=True`` -- without it, ``decision.state:
    "suppressed"`` was advertised but never actually reachable from
    production reporting)."""
    entry: dict[str, object] = {
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
    }
    assessment = assess_change(c, suppressed=True)
    entry["reachability_state"] = assessment.reachability_state.value
    if assessment.has_signal():
        entry["impact_assessment"] = assessment.to_dict()
    return entry


def _add_suppression(d: dict[str, object], result: DiffResult) -> None:
    """Add suppression block (file flag, count, suppressed change list)."""
    d["suppression"] = {
        "file_provided": result.suppression_file_provided,
        "suppressed_count": result.suppressed_count,
        "suppressed_changes": [
            _suppressed_change_entry(c) for c in result.suppressed_changes
        ],
    }


def _add_detectors(d: dict[str, object], result: DiffResult) -> None:
    """Add detector metadata — only detectors with findings or a coverage gap."""
    d["detectors"] = [
        {
            "name": det.name,
            "changes_count": det.changes_count,
            "enabled": det.enabled,
            "coverage_gap": det.coverage_gap,
        }
        for det in result.detector_results
        if det.changes_count > 0 or det.coverage_gap is not None
    ]


def _add_confidence_evidence(d: dict[str, object], result: DiffResult) -> None:
    """Add confidence level, evidence tier/tiers, and optional coverage warnings."""
    # Confidence & evidence metadata — helps users assess verdict trust level
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)


def _add_policy_overrides(d: dict[str, object], result: DiffResult) -> None:
    """Add policy file overrides (custom re-classifications) when present."""
    if result.policy_file and result.policy_file.overrides:
        d["policy_overrides"] = {
            kind.value: verdict.value
            for kind, verdict in result.policy_file.overrides.items()
        }
        if result.policy_file.source_path:
            d["policy_file"] = str(result.policy_file.source_path)


def _add_changes_block(
    d: dict[str, object],
    result: DiffResult,
    changes: list[Change],
    effective_policy: str,
    eff_sets: KindSets | None,
) -> None:
    """Add changes list and optional redundant-count / pattern-modulations fields."""
    d["changes"] = [
        _change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
        for c in changes
    ]
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # ADR-027 A4 — pattern-aware modulation ledger (disclosed, reversible).
    if result.pattern_modulations:
        d["pattern_modulations"] = result.pattern_modulations


def _add_trailing_fields(
    d: dict[str, object],
    result: DiffResult,
    show_impact: bool,
    show_only: str | None,
) -> None:
    """Add show_only_applied flag and public-surface scope block (both optional)."""
    if show_impact:
        d["show_only_applied"] = show_only is not None
    scope = _scope_dict(result)
    if scope is not None:
        d["scope"] = scope


def to_json(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
) -> str:
    if stat:
        return to_stat_json(result, indent=indent, severity_config=severity_config)

    if report_mode == "leaf":
        return _to_json_leaf(
            result,
            indent=indent,
            show_only=show_only,
            severity_config=severity_config,
        )

    if report_mode == "root-cause":
        return _to_json_root_cause(
            result,
            indent=indent,
            show_only=show_only,
            severity_config=severity_config,
        )

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

    d = _build_json_base(result)
    _add_abi_surface_breakdown(d, result)
    _add_evidence_fields(d, result)
    effective_policy = result.policy or "strict_abi"
    d["policy"] = effective_policy
    eff_sets = result._effective_kind_sets()

    if show_only:
        _add_show_only_filter(d, result, changes, show_only)

    # Severity-categorized summary when severity config is provided
    if severity_config is not None:
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )

    _add_changes_block(d, result, changes, effective_policy, eff_sets)
    _add_suppression(d, result)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    _add_detectors(d, result)
    _add_confidence_evidence(d, result)
    _add_policy_overrides(d, result)
    _add_trailing_fields(d, result, show_impact, show_only)
    return json.dumps(d, indent=indent)


def _finding_id(c: object) -> str:
    """Stable per-finding fingerprint (schema 2.3, additive).

    Deterministic across repeated runs of the same comparison, so a
    consumer can tell "is this the same finding" across two report runs
    (e.g. to correlate a suppression/waiver, or diff two CI runs' findings)
    without relying on array order or index — neither of which abicheck
    guarantees stays stable release to release.

    Derived only from fields that identify the finding's *identity* (kind,
    symbol, old/new value, source location, description) — deliberately
    excluding ``severity``/``evidence_status``, which are policy-derived and
    would make the same underlying finding hash differently under a
    different ``--policy``.

    ``description`` is included as a discriminator: two findings of the same
    kind on the same symbol with the same old/new value and no distinct
    source location (e.g. ``param_pointer_level_changed`` on two different
    parameters of one function, both going from pointer-depth 1 to 2) would
    otherwise collide on an identical id even though they are different
    findings — ``description`` embeds the per-finding detail (parameter
    name/index, member name, …) that disambiguates them.
    """
    key = "\x1f".join(
        [
            str(getattr(getattr(c, "kind", None), "value", getattr(c, "kind", ""))),
            str(getattr(c, "symbol", None) or ""),
            str(getattr(c, "old_value", None) or ""),
            str(getattr(c, "new_value", None) or ""),
            str(getattr(c, "source_location", None) or ""),
            str(getattr(c, "description", None) or ""),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


_VERDICT_TO_RECOMMENDED_ACTION: dict[Verdict, str] = {
    Verdict.BREAKING: "recompile_and_relink_required",
    Verdict.API_BREAK: "recompile_required",
    Verdict.COMPATIBLE_WITH_RISK: "verify_deployment_compatibility",
}


def _recommended_action_for_change(
    c: object,
    *,
    policy: str | None,
    kind_sets: KindSets | None,
    policy_file: object | None,
) -> str:
    """Return a structured, machine-readable next step for *c* (schema 2.4).

    Derived from the same effective verdict/category resolution
    ``severity``/``operation``/``finding_id`` already use, so it can never
    disagree with them for the same finding:

    - ``BREAKING`` → ``recompile_and_relink_required`` (binary ABI break)
    - ``API_BREAK`` → ``recompile_required`` (source-level break only)
    - ``COMPATIBLE_WITH_RISK`` → ``verify_deployment_compatibility``
    - ``COMPATIBLE`` additions → ``no_action_required``
    - ``COMPATIBLE`` non-additions (quality issues) → ``review_recommended``
    """
    from .severity import (
        IssueCategory,
        classify_effective_change,
        effective_verdict_for_change,
    )

    verdict = effective_verdict_for_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    action = _VERDICT_TO_RECOMMENDED_ACTION.get(verdict)
    if action is not None:
        return action
    # COMPATIBLE: distinguish a genuine addition (nothing to do) from a
    # quality issue (compatible, but worth a look) via the same category
    # classification the severity JSON block uses.
    category = classify_effective_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    return (
        "no_action_required"
        if category == IssueCategory.ADDITION
        else "review_recommended"
    )


#: Per-kind reviewer guidance for a COMPATIBLE addition, keyed by
#: ``ChangeKind.value``. Falls back to ``_DEFAULT_ADDITION_REVIEWER_ACTION``
#: for any addition kind not listed here.
_ADDITION_REVIEWER_ACTION: dict[str, str] = {
    # Old binaries are unaffected, but exhaustive `switch`/sentinel-value
    # patterns in *source* consumers can miss the new case silently.
    "enum_member_added": "review_exhaustive_switches",
    # A semantic addition with no new symbol: the API existed but was
    # unstable; graduating it is a documentation/support-contract change,
    # not a binary one.
    "experimental_graduated": "document_stable_replacement",
}
_DEFAULT_ADDITION_REVIEWER_ACTION = "confirm_public_api_intent"


def _reviewer_action_for_change(
    c: object,
    *,
    policy: str | None,
    kind_sets: KindSets | None,
    policy_file: object | None,
) -> str | None:
    """Finer-grained reviewer guidance for a COMPATIBLE addition (additive).

    ``recommended_action`` collapses every addition to one value,
    ``no_action_required`` — accurate for the *old binary consumer*
    (nothing to recompile, nothing to relink), but a reviewer approving a
    new public export almost always has something to check: was it
    intentional, does it need a release note, do exhaustive switches need
    the new case. This field carries that reviewer-facing nuance without
    changing ``recommended_action``'s existing meaning or schema enum.
    Returns ``None`` for every non-addition finding, since those already
    have reviewer-actionable guidance via ``recommended_action`` itself.
    """
    from .severity import IssueCategory, classify_effective_change

    category = classify_effective_change(
        cast(HasKind, c),
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    if category != IssueCategory.ADDITION:
        return None
    kind = getattr(c, "kind", None)
    kind_val = kind.value if kind else ""
    return _ADDITION_REVIEWER_ACTION.get(kind_val, _DEFAULT_ADDITION_REVIEWER_ACTION)


def _change_to_dict(
    c: object,
    *,
    policy: str = "strict_abi",
    kind_sets: tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ]
    | None = None,
    policy_file: object | None = None,
    evidence_status_override: EvidenceStatus | None = None,
) -> dict[str, object]:
    """Convert a Change to a JSON-serializable dict with impact and metadata.

    ``evidence_status_override`` lets a caller assert a stronger epistemic
    status than the finding's own classification implies — e.g.
    ``appcompat_to_json`` marks every finding it already proved a specific
    consumer depends on as ``EvidenceStatus.CONSUMER_PROVEN``, regardless of
    the finding's own kind.
    """
    kind = getattr(c, "kind", None)
    if isinstance(kind, ChangeKind) and kind_sets:
        from .severity import effective_verdict_for_change

        verdict = effective_verdict_for_change(
            cast(HasKind, c),
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
        )
        severity = _VERDICT_TO_SEVERITY_LABEL.get(verdict, "unknown")
    elif kind:
        severity = _kind_to_severity(kind, policy)
    else:
        severity = "unknown"
    evidence_status = evidence_status_override
    if evidence_status is None and isinstance(kind, ChangeKind):
        evidence_status = evidence_status_for_change(cast(HasKind, c))
    d: dict[str, object] = {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", ""),
        "description": getattr(c, "description", ""),
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "severity": severity,
    }
    if isinstance(kind, ChangeKind):
        d["operation"] = operation_for_kind(kind.value)
        d["finding_id"] = _finding_id(c)
        d["recommended_action"] = _recommended_action_for_change(
            c,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
        )
        reviewer_action = _reviewer_action_for_change(
            c,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
        )
        if reviewer_action is not None:
            d["reviewer_action"] = reviewer_action
    if evidence_status is not None:
        d["evidence_status"] = evidence_status.value
    # Impact explanation
    if kind:
        impact = impact_for(kind)
        if impact:
            d["impact"] = impact
    # Source location
    loc = getattr(c, "source_location", None)
    if loc:
        d["source_location"] = loc
    # Affected symbols
    affected = getattr(c, "affected_symbols", None)
    if affected:
        d["affected_symbols"] = affected
    # Redundancy annotation
    caused_by = getattr(c, "caused_by_type", None)
    if caused_by:
        d["caused_by_type"] = caused_by
    caused_count = getattr(c, "caused_count", 0)
    if caused_count > 0:
        d["caused_count"] = caused_count
    # ADR-027 A4 — disclose a pattern-aware modulation on the finding itself.
    mod_reason = getattr(c, "modulation_reason", None)
    if mod_reason:
        d["modulation_reason"] = mod_reason
        d["modulation_rule"] = getattr(c, "modulation_rule", None)
        eff = getattr(c, "effective_verdict", None)
        if isinstance(eff, Verdict):
            d["effective_verdict"] = eff.value
    # ADR-041 P0 roadmap item 2 — this finding correlates with another
    # finding (currently: PUBLIC_API_INTERNAL_DEPENDENCY_ADDED correlating
    # with the same entry's own body/type-hash change), named by ChangeKind
    # value so a machine consumer can act on it without parsing description.
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        d["correlated_change_kind"] = correlated
    # ADR-044 P1 item 4 — structured reachability evidence (previously
    # description-text-only): whether a suppression rule's reachability gate
    # tagged this change public-reachable, how (layout/call-graph/direct),
    # and the shortest proof path, so a machine consumer doesn't need to
    # parse the suppression_would_hide_public_break diagnostic's prose.
    if getattr(c, "public_reachable", False):
        d["public_reachable"] = True
        reach_kind = getattr(c, "reachability_kind", None)
        if reach_kind:
            d["reachability_kind"] = reach_kind
        proof_path = getattr(c, "reachability_proof_path", None)
        if proof_path:
            d["reachability_proof_path"] = proof_path
    # G31 Phase B B3 (ADR-048) — structured graph impact/proof-path data:
    # the machine-readable counterpart of reachability_proof_path's prose,
    # as a list of node/edge reference dicts, plus which public root(s) it
    # traces back to and whether the dependency is direct or transitive.
    affected_roots = getattr(c, "affected_public_roots", None)
    if affected_roots:
        d["affected_public_roots"] = affected_roots
    impact_path = getattr(c, "impact_proof_path", None)
    if impact_path:
        d["impact_proof_path"] = impact_path
    impact_direct = getattr(c, "impact_is_direct", None)
    if impact_direct is not None:
        d["impact_is_direct"] = impact_direct
    # G29 Phase 3 slice 1 (ADR-051): reachability_state has existed on Change
    # since PR #607 but was never serialized -- without it, a JSON consumer
    # cannot tell a PROVEN_UNREACHABLE finding apart from one the graph walk
    # never examined at all (UNKNOWN), since both leave public_reachable
    # False. impact_assessment is the unified read view over the scattered
    # reachability/impact fields above; only emitted when it carries
    # information beyond the all-defaults case, matching this function's own
    # convention of not padding every plain finding with an empty object.
    assessment = assess_change(c)
    d["reachability_state"] = assessment.reachability_state.value
    if assessment.has_signal():
        d["impact_assessment"] = assessment.to_dict()
    return d


def _build_severity_json(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    all_changes: list[Change] | None = None,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
) -> dict[str, object]:
    """Build severity information for JSON output.

    *changes* are the (possibly filtered) changes for display counts.
    *all_changes*, when provided, is the unfiltered set used to compute
    the exit code so that ``--show-only`` does not affect the exit code.
    *kind_sets* from ``DiffResult._effective_kind_sets()`` includes
    PolicyFile overrides.
    """
    from .severity import SeverityLevel, categorize_changes, compute_gate_decision

    categorized = categorize_changes(
        changes,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )

    config_dict: dict[str, str] = {}
    for attr in ("abi_breaking", "potential_breaking", "quality_issues", "addition"):
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        config_dict[attr] = level.value if hasattr(level, "value") else str(level)

    categories: dict[str, object] = {
        "abi_breaking": {
            "severity": config_dict["abi_breaking"],
            "count": len(categorized.abi_breaking),
        },
        "potential_breaking": {
            "severity": config_dict["potential_breaking"],
            "count": len(categorized.potential_breaking),
        },
        "quality_issues": {
            "severity": config_dict["quality_issues"],
            "count": len(categorized.quality_issues),
        },
        "addition": {
            "severity": config_dict["addition"],
            "count": len(categorized.addition),
        },
    }

    # ``blocking``/``blocking_categories`` (schema 2.3, additive): a typed,
    # auditable gate summary mirroring SARIF's ``severityGate`` block
    # (``sarif._severity_gate_properties``) — without them, a JSON consumer
    # had to independently recompute "which category is actually failing the
    # build" from ``config``/``categories`` itself; this makes that answer a
    # first-class, versioned part of the report.
    #
    # Derived from *exit_changes* (the unfiltered gate set), not ``changes``
    # (the possibly --show-only-filtered *display* set) — otherwise hiding
    # the one category that's actually failing the build (e.g.
    # ``--show-only=breaking`` when an addition promoted to ``error`` is
    # what's blocking) would report ``blocking: true`` alongside
    # ``blocking_categories: []`` (Codex review on #557). Routed through
    # ``compute_gate_decision`` — the single canonical gate computation —
    # rather than hand-rolling exit_code and blocking_categories as two
    # independent computations that could drift apart from each other.
    exit_changes = all_changes if all_changes is not None else changes
    gate = compute_gate_decision(
        exit_changes,
        severity_config,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )

    return {
        "config": config_dict,
        "categories": categories,
        "exit_code": gate.exit_code,
        "blocking": gate.blocking,
        "blocking_categories": list(gate.blocking_categories),
    }


def _classify_changes_by_kind(
    changes: list[Change],
    result: DiffResult,
) -> tuple[list[Change], list[Change], list[Change], list[Change]]:
    """Split *changes* into (breaking, source_breaks, risk, compatible) using the
    effective kind sets (respects PolicyFile overrides) and per-finding A4
    ``effective_verdict`` overrides (ADR-027), so a demoted opaque/PIMPL layout
    change lands in the compatible bucket of the text report too.

    Thin wrapper over :meth:`ReportModel.classify` (C2/ADR-036) — the single
    canonical verdict-axis bucketer shared with the report view-model."""
    from .report_model import ReportModel

    return ReportModel.classify(changes, result)


def appcompat_to_json(result: object, indent: int = 2) -> str:
    """Render an AppCompatResult as JSON."""
    import json as _json

    verdict = getattr(result, "verdict", None)
    full_diff = getattr(result, "full_diff", None)

    d: dict[str, object] = {
        "application": getattr(result, "app_path", ""),
        "old_library": getattr(result, "old_lib_path", ""),
        "new_library": getattr(result, "new_lib_path", ""),
        "verdict": verdict.value if verdict else "UNKNOWN",
        "symbol_coverage_pct": round(getattr(result, "symbol_coverage", 0.0), 1),
        "required_symbol_count": getattr(result, "required_symbol_count", 0),
    }

    missing = getattr(result, "missing_symbols", [])
    d["missing_symbols"] = list(missing)

    missing_ver = getattr(result, "missing_versions", [])
    d["missing_versions"] = list(missing_ver)

    breaking = getattr(result, "breaking_for_app", [])
    appcompat_policy = (
        getattr(getattr(result, "full_diff", None), "policy", "strict_abi")
        or "strict_abi"
    )
    # Thread the full_diff's PolicyFile/effective kind_sets through, mirroring
    # to_json's _change_to_dict calls (reporter.py _add_changes_block) —
    # without them, a per-finding severity here falls back to raw-kind
    # classification and can contradict full_library_verdict below, which
    # already honours the PolicyFile via full_diff.verdict.
    _kind_sets_fn = getattr(full_diff, "_effective_kind_sets", None)
    appcompat_kind_sets = _kind_sets_fn() if callable(_kind_sets_fn) else None
    appcompat_policy_file = getattr(full_diff, "policy_file", None)
    d["relevant_changes"] = [
        _change_to_dict(
            c,
            policy=appcompat_policy,
            kind_sets=appcompat_kind_sets,
            policy_file=appcompat_policy_file,
            evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
        )
        for c in breaking
    ]
    d["relevant_change_count"] = len(breaking)

    irrelevant = getattr(result, "irrelevant_for_app", [])
    d["irrelevant_change_count"] = len(irrelevant)

    total = len(breaking) + len(irrelevant)
    d["total_library_changes"] = total

    if full_diff:
        d["full_library_verdict"] = full_diff.verdict.value
        # Traceability: file metadata from the underlying library diff
        d["old_file"] = _metadata_dict(getattr(full_diff, "old_metadata", None))
        d["new_file"] = _metadata_dict(getattr(full_diff, "new_metadata", None))
        # Confidence & evidence
        conf = getattr(full_diff, "confidence", None)
        if conf is not None:
            d["confidence"] = conf.value if hasattr(conf, "value") else str(conf)
            etier = getattr(full_diff, "evidence_tier", None)
            if etier is not None:
                d["evidence_tier"] = (
                    etier.value if hasattr(etier, "value") else str(etier)
                )
            d["evidence_tiers"] = list(getattr(full_diff, "evidence_tiers", []) or [])
            cov_warns = getattr(full_diff, "coverage_warnings", []) or []
            if cov_warns:
                d["coverage_warnings"] = list(cov_warns)

    return _json.dumps(d, indent=indent)


def appcompat_to_markdown(result: object, *, show_irrelevant: bool = False) -> str:
    """Render an AppCompatResult as Markdown."""
    verdict = getattr(result, "verdict", None)
    v_label = verdict.value if verdict else "UNKNOWN"
    v_emoji = _VERDICT_EMOJI.get(verdict, "?") if verdict else "?"

    app_path = getattr(result, "app_path", "")
    old_lib = getattr(result, "old_lib_path", "")
    new_lib = getattr(result, "new_lib_path", "")
    required_count = getattr(result, "required_symbol_count", 0)
    coverage = getattr(result, "symbol_coverage", 0.0)
    missing = getattr(result, "missing_symbols", [])
    missing_ver = getattr(result, "missing_versions", [])
    breaking = getattr(result, "breaking_for_app", [])
    irrelevant = getattr(result, "irrelevant_for_app", [])

    total_changes = len(breaking) + len(irrelevant)

    lines: list[str] = [
        "# Application Compatibility Report",
        "",
    ]

    lines += _appcompat_header_lines(app_path, old_lib, new_lib, v_emoji, v_label)

    # File metadata (traceability)
    full_diff = getattr(result, "full_diff", None)
    old_meta = getattr(full_diff, "old_metadata", None) if full_diff else None
    new_meta = getattr(full_diff, "new_metadata", None) if full_diff else None
    if old_meta or new_meta:
        lines += ["## Library Files", "", "| | Old | New |", "|---|---|---|"]
        old_path = getattr(old_meta, "path", "—") if old_meta else "—"
        new_path = getattr(new_meta, "path", "—") if new_meta else "—"
        old_sha = getattr(old_meta, "sha256", "—")[:12] if old_meta else "—"
        new_sha = getattr(new_meta, "sha256", "—")[:12] if new_meta else "—"
        old_size = _fmt_size(old_meta.size_bytes) if old_meta else "—"
        new_size = _fmt_size(new_meta.size_bytes) if new_meta else "—"
        lines += [
            f"| **Path** | `{old_path}` | `{new_path}` |",
            f"| **SHA-256** | `{old_sha}…` | `{new_sha}…` |",
            f"| **Size** | {old_size} | {new_size} |",
            "",
        ]

    # Confidence info
    conf = getattr(full_diff, "confidence", None) if full_diff else None
    if conf is not None:
        conf_val = conf.value if hasattr(conf, "value") else str(conf)
        tiers = getattr(full_diff, "evidence_tiers", []) or []
        tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
        policy_val = getattr(full_diff, "policy", None) or "strict_abi"
        lines += [
            f"> **Confidence**: {conf_val.upper()} | **Evidence**: {tier_str} | **Policy**: `{policy_val}`",
            "",
        ]
    else:
        # Still show policy when confidence is absent
        policy_val = getattr(full_diff, "policy", None) if full_diff else None
        if policy_val:
            lines += [f"> **Policy**: `{policy_val}`", ""]

    lines += _appcompat_coverage_lines(required_count, coverage, missing)
    lines += _appcompat_missing_lines(missing, missing_ver)
    lines += _appcompat_relevant_lines(breaking, total_changes)
    lines += _appcompat_irrelevant_lines(irrelevant, show_irrelevant)

    lines += [
        "---",
        "_Generated by [abicheck](https://github.com/abicheck/abicheck)_",
    ]
    return "\n".join(lines)


def _appcompat_header_lines(
    app_path: str,
    old_lib: str,
    new_lib: str,
    v_emoji: str,
    v_label: str,
) -> list[str]:
    """Build the report header lines for appcompat markdown."""
    header = [
        f"**Application:** `{app_path}`",
        f"**Verdict:** {v_emoji} `{v_label}`",
        "",
    ]
    if old_lib:
        header.insert(1, f"**Library:** `{old_lib}` → `{new_lib}`")
        return header
    header.insert(1, f"**Library:** `{new_lib}`")
    return header


def _appcompat_coverage_lines(
    required_count: int,
    coverage: float,
    missing: list[object],
) -> list[str]:
    """Build symbol coverage section lines."""
    lines = [
        "## Symbol Coverage",
        "",
        f"App requires **{required_count}** library symbols.",
    ]
    if missing:
        lines.append(
            f"**{len(missing)}** required symbol(s) missing from new version "
            f"({coverage:.0f}% coverage).",
        )
    elif required_count > 0:
        lines.append(
            f"All {required_count} required symbols present in new version "
            f"({coverage:.0f}% coverage).",
        )
    lines.append("")
    return lines


def _appcompat_missing_lines(
    missing: list[object],
    missing_ver: list[object],
) -> list[str]:
    """Build missing symbol/version sections."""
    lines: list[str] = []
    if missing:
        lines += ["## Missing Symbols", ""]
        lines.append(
            "These symbols are required by the application but absent from the new library:"
        )
        lines.append("")
        for sym in missing:
            lines.append(f"- `{sym}`")
        lines.append("")
    if missing_ver:
        lines += ["## Missing Symbol Versions", ""]
        for ver in missing_ver:
            lines.append(f"- `{ver}`")
        lines.append("")
    return lines


def _appcompat_relevant_lines(breaking: list[Change], total_changes: int) -> list[str]:
    """Build relevant changes section lines."""
    if breaking:
        lines: list[str] = [
            f"## Relevant Changes ({len(breaking)} of {total_changes} total)",
            "",
            "These library changes affect symbols your application uses:",
            "",
            "| Kind | Symbol | Description |",
            "|------|--------|-------------|",
        ]
        for change in breaking:
            kind_val = change.kind.value if change.kind else ""
            lines.append(f"| `{kind_val}` | `{change.symbol}` | {change.description} |")
        lines.append("")
        return lines
    if total_changes > 0:
        return [
            f"## Relevant Changes (0 of {total_changes} total)",
            "",
            "None of the library's ABI changes affect your application.",
            "",
        ]
    return []


def _appcompat_irrelevant_lines(
    irrelevant: list[Change], show_irrelevant: bool
) -> list[str]:
    """Build irrelevant changes section/note lines."""
    if irrelevant and not show_irrelevant:
        return [
            f"_{len(irrelevant)} library ABI change(s) do NOT affect your application. "
            "Use `--show-irrelevant` to see them._",
            "",
        ]
    if irrelevant and show_irrelevant:
        lines = [
            f"## Irrelevant Changes ({len(irrelevant)})",
            "",
            "These library changes do NOT affect your application:",
            "",
        ]
        for change in irrelevant:
            kind_val = change.kind.value if change.kind else ""
            lines.append(f"- **{kind_val}**: {change.description}")
        lines.append("")
        return lines
    return []
