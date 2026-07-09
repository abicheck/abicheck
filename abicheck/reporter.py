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
    HasKind,
    impact_for,
    policy_kind_sets as _policy_kind_sets,
)
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
) -> str:
    """Severity label for a change, honouring its A4 ``effective_verdict``.

    The one place the reporter decides a finding's severity bucket: routes
    through :func:`effective_category` so an ADR-027 pattern-aware demotion reads
    ``compatible`` in the JSON ``severity`` field and the ``filtered_summary``
    counts, consistent with the verdict and exit code.
    """
    kind = getattr(c, "kind", None)
    if kind is None:
        return "unknown"
    # An explicit A4 override wins; otherwise fall back to the exact set-based
    # logic (which yields "unknown" for a kind moved out of every set, e.g. an
    # override to NO_CHANGE) rather than effective_category's BREAKING fail-safe.
    eff = getattr(c, "effective_verdict", None)
    if isinstance(eff, Verdict):
        return _VERDICT_TO_SEVERITY_LABEL.get(eff, "unknown")
    breaking, api_break, compatible, risk = kind_sets
    if kind in breaking:
        return "breaking"
    if kind in api_break:
        return "api_break"
    if kind in risk:
        return "risk"
    if kind in compatible:
        return "compatible"
    return "unknown"


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


def to_stat_json(result: DiffResult, indent: int = 2) -> str:
    """JSON output for --stat mode: summary only, no changes array."""
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
) -> str:
    """Leaf-change mode JSON output."""
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    summary = build_summary(result)
    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)
    type_changes = [c for c in changes if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in changes if c.kind not in _ROOT_TYPE_CHANGE_KINDS]

    effective_policy = result.policy or "strict_abi"
    eff_sets = result._effective_kind_sets()

    def _leaf_entry(c: Change) -> dict[str, object]:
        entry: dict[str, object] = {
            "kind": c.kind.value,
            "symbol": c.symbol,
            "description": c.description,
            "severity": _effective_severity_label(c, eff_sets),
            "affected_count": len(c.affected_symbols) if c.affected_symbols else 0,
            "affected_symbols": c.affected_symbols or [],
            "caused_count": c.caused_count,
            "old_value": getattr(c, "old_value", None),
            "new_value": getattr(c, "new_value", None),
        }
        # ADR-027 A4: keep the modulation audit trail in leaf mode too, so a
        # demoted root type change still explains *why* it reads compatible.
        mod_reason = getattr(c, "modulation_reason", None)
        if mod_reason:
            entry["modulation_reason"] = mod_reason
            entry["modulation_rule"] = getattr(c, "modulation_rule", None)
            eff = getattr(c, "effective_verdict", None)
            if isinstance(eff, Verdict):
                entry["effective_verdict"] = eff.value
        return entry

    leaf_changes_list = [_leaf_entry(c) for c in type_changes]
    non_type_list = [
        _change_to_dict(c, policy=effective_policy, kind_sets=eff_sets)
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


def _add_suppression(d: dict[str, object], result: DiffResult) -> None:
    """Add suppression block (file flag, count, suppressed change list)."""
    d["suppression"] = {
        "file_provided": result.suppression_file_provided,
        "suppressed_count": result.suppressed_count,
        "suppressed_changes": [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
            }
            for c in result.suppressed_changes
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
        return to_stat_json(result, indent=indent)

    if report_mode == "leaf":
        return _to_json_leaf(result, indent=indent, show_only=show_only)

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)

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
) -> dict[str, object]:
    """Convert a Change to a JSON-serializable dict with impact and metadata."""
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
    d: dict[str, object] = {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", ""),
        "description": getattr(c, "description", ""),
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "severity": severity,
    }
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
    from .severity import SeverityLevel, categorize_changes, compute_exit_code

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

    # Exit code uses the full unfiltered change set so --show-only
    # does not affect it.
    exit_changes = all_changes if all_changes is not None else changes
    exit_code = compute_exit_code(
        exit_changes,
        severity_config,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )

    return {
        "config": config_dict,
        "categories": categories,
        "exit_code": exit_code,
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
    d["relevant_changes"] = [
        _change_to_dict(c, policy=appcompat_policy) for c in breaking
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
