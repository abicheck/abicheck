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
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .severity import GateDecision, KindSets, SeverityConfig
from . import reporter_contract_blocks as _reporter_contract_blocks
from .checker import (
    Change,
    DiffResult,
    Verdict,
)
from .checker_policy import (
    ChangeKind,
    EvidenceStatus,
    HasKind,
    evidence_status_for_result,
    impact_for,
    policy_kind_sets as _policy_kind_sets,
)
from .checker_types import validate_check_id, validate_evidence_depth
from .impact import assess_change
from .policy.gate_decision import gate_decision_for_result
from .report_model import VERDICT_TO_SEVERITY_LABEL as _VERDICT_TO_SEVERITY_LABEL
from .report_summary import build_summary, surface_breakdown
from .reporter_contract_blocks import add_contract_context as _add_contract_context

# Markdown rendering is a leaf; names stay importable here for compatibility.
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
    _finding_id as _finding_id,
    _fmt_size as _fmt_size,
    _footer_lines as _footer_lines,
    _format_change_md as _format_change_md,
    _format_leaf_type_change as _format_leaf_type_change,
    _group_changes_by_root_cause as _group_changes_by_root_cause,
    _resolve_scoped_gate_findings as _resolve_scoped_gate_findings,
    _root_cause_key_and_display as _root_cause_key_and_display,
    _section_severity_label as _section_severity_label,
    _suppress_dangling_correlation_notes as _suppress_dangling_correlation_notes,
    _to_markdown_leaf as _to_markdown_leaf,
    _to_markdown_root_cause as _to_markdown_root_cause,
    apply_show_only as apply_show_only,
    operation_for_kind as operation_for_kind,
    root_cause_evidence_lookup_for_changes as root_cause_evidence_lookup_for_changes,
    root_cause_for_change as root_cause_for_change,
    root_cause_lookup_for_changes as root_cause_lookup_for_changes,
    to_markdown as to_markdown,
    to_review_digest as to_review_digest,
    to_stat as to_stat,
)
from .root_cause_evidence import (
    entry_root_cause_evidence,
    fold_evidence_summaries,
    root_cause_group_evidence,
    scoped_only_changes_filtered,
    scoped_only_evidence_lookup,
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
    require_complete_analysis: bool = False,
    show_only: str | None = None, contract_evaluation: bool = False,
) -> str:
    """JSON output for --stat mode: summary only, no changes array.

    *severity_config*, when given, adds a ``severity`` block (same shape as
    the full JSON report's — see :func:`_build_severity_json`) so ``--stat
    --format json`` reflects the actual severity-aware gate instead of only
    the compatibility verdict. Without it, ``--stat`` output has historically
    bypassed severity handling entirely (it short-circuits in
    ``service.render_output`` before format dispatch).

    *show_only*/*contract_evaluation* feed only the scoped-gate fold at the
    tail of ``render_json_with_side_facts`` -- no ``changes`` array here to filter.
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
    gate = gate_decision_for_result(result, severity_config)
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = _build_severity_json(
            result.changes,
            severity_config,
            gate=gate,
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
    from .analysis_assurance import (
        analysis_assurance_exit_contribution,
        analysis_assurance_report_dict,
    )

    if (block := analysis_assurance_report_dict(result)) is not None:
        d["analysis_assurance"] = block
        # Same persistence `add_contract_context` does for the full JSON path, which `--stat` bypasses entirely (Codex review).
        d["analysis_assurance_exit_contribution"] = analysis_assurance_exit_contribution(
            result, require_complete=require_complete_analysis
        )
    from .reporter_contract_blocks import add_effective_config_digest
    add_effective_config_digest(d, result, severity_config=severity_config, require_complete_analysis=require_complete_analysis)
    # Deliberately NOT `add_use_case_impact` here, unlike the full JSON path
    # (`reporter_contract_blocks`): this function's contract is the summary
    # object alone, and a per-finding attribution block is the opposite of a
    # summary. `compare` rejects `--stat --use-cases` outright rather than
    # dropping the manifest silently; this keeps the same promise for a
    # direct caller of the renderer (Codex review).
    return _reporter_contract_blocks.render_json_with_side_facts(d, result, indent=indent, severity_config=severity_config, gate=gate, show_only=show_only, contract_evaluation=contract_evaluation)


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

    def _out_of_surface_entry(c: Change) -> dict[str, object]:
        entry: dict[str, object] = {
            "kind": c.kind.value,
            "symbol": c.symbol,
            "description": c.description,
            "source_location": c.source_location,
            "reason": getattr(c, "surface_exclusion_reason", None),
        }
        # ADR-049 Phase 3 (shadow contract evaluator, opt-in
        # `compare(..., contract_evaluation=True)`) -- a demoted finding's
        # shadow decision (typically PROVEN_OUT_OF_CONTRACT, resolved via
        # its own surface_exclusion_reason above) is exposed here too, not
        # just on an ordinary `changes` entry (Codex review, fresh evidence).
        _add_contract_evaluation_fields(entry, c)
        return entry

    d["surface_scope"] = {
        "enabled": True,
        # ADR-024 §D5.3 — structured confidence in the resolution itself.
        "confidence": result.surface_scope_confidence,
        "notes": list(result.surface_scope_notes),
        "out_of_surface_count": result.out_of_surface_count,
        "out_of_surface_changes": [
            _out_of_surface_entry(c) for c in result.out_of_surface_changes
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
    entries: list[dict[str, object]] = []
    for c in result.reconciled_changes:
        entry: dict[str, object] = {
            "kind": c.kind.value,
            "symbol": c.symbol,
            "description": c.description,
            "source_location": c.source_location,
            "reason": getattr(c, "surface_exclusion_reason", None),
        }
        # ADR-049 Phase 3 (Codex review, fresh evidence): reconciliation runs
        # before checker._apply_contract_evaluation_shadow (a reconciled
        # finding never reaches `kept`), so without this call the ledger
        # entry silently lost the contract decision the finding would
        # otherwise carry -- a no-op when contract_evaluation was never
        # requested, mirroring this helper's other callers.
        _add_contract_evaluation_fields(entry, c)
        entries.append(entry)
    d["build_context_reconciled"] = {
        "count": result.reconciled_count,
        "changes": entries,
    }


def _displayed_with_scoped_only(
    result: Any, changes: list[Change], show_only: str | None
) -> list[Change]:
    """The findings this report will actually list, scoped-only ones included.

    `--used-by`/`--required-symbol` scoping synthesizes fresh `Change`
    objects onto `result.scoped_only_changes` (e.g. `PE_ORDINAL_RETARGETED`),
    and `_fold_scoped_compat_into_text` appends them to the rendered report's
    own `changes` array afterwards. Any block projected onto "what is
    displayed" therefore has to count them too, or it silently describes a
    smaller report than the reader sees -- which is how `use_case_impact`'s
    `total_changes` came out below the adjacent findings list, attributing
    and counting neither (Codex review).

    Filtered through the shared `scoped_only_changes_filtered` rather than a
    local `apply_show_only`, so the two lists can never be filtered
    differently.
    """
    return changes + scoped_only_changes_filtered(result, show_only)


def _to_json_leaf(
    result: DiffResult,
    indent: int = 2,
    show_only: str | None = None,
    *,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False, include_exit_decision: bool = True, contract_evaluation: bool = False,
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
        changes = _suppress_dangling_correlation_notes(changes)
    type_changes = [c for c in changes if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in changes if c.kind not in _ROOT_TYPE_CHANGE_KINDS]
    # G29 Phase 3 follow-up (ADR-052): computed once over the full filtered
    # `changes` (not type_changes/non_type_changes separately) so a
    # TYPE_* change and a non-type change sharing a root cause still get
    # the same root_cause_id, matching --report-mode root-cause's own
    # whole-`changes`-scoped grouping. extra_causes folds in
    # scoped_only_changes the same way _to_json_root_cause does (review
    # finding) -- without it, a leaf-mode finding correlating only via a
    # scoped-only overlay's caused_by_type silently lost its
    # impact_assessment.root_cause_id, disagreeing with root-cause/SARIF/JUnit.
    _rc_lookup = root_cause_lookup_for_changes(
        changes, extra_causes=_scoped_only_extra_causes(result, show_only)
    )
    _rc_evidence = scoped_only_evidence_lookup(result, changes, show_only)

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
        evidence_status = evidence_status_for_result(
            cast(HasKind, c), result.evidence_tiers
        )
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
        # Same "leaf mode duplicates the full-mode builder" gap as the rest
        # of this function (Codex review) -- shares _change_to_dict's own
        # helper so the two entry builders can't drift on this field.
        _reclassified_by = _reclassified_by_for_change(c, result.policy_file)
        if _reclassified_by:
            entry["reclassified_by"] = _reclassified_by
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
        # G29 Phase 3 slice 1 (ADR-052, Codex review): _leaf_entry duplicates
        # _change_to_dict's reachability fields rather than routing through
        # it (see the ADR-044 block above) -- reachability_state/
        # impact_assessment follow the same precedent so a root TYPE_*
        # change (exactly the category the layout-reachability walk tags
        # most often) doesn't lose them in --report-mode leaf.
        assessment = assess_change(
            c,
            root_cause=_rc_lookup.get(_finding_id(c)),
            root_cause_evidence=_rc_evidence.get(_finding_id(c)),
        )
        entry["reachability_state"] = assessment.reachability_state.value
        if assessment.has_signal():
            entry["impact_assessment"] = assessment.to_dict()
        # ADR-049 Phase 3: _leaf_entry builds its own dict rather than
        # routing through _change_to_dict (see the schema 2.3/2.4 comment
        # above -- this is the same, long-standing "leaf mode duplicates
        # the full-mode entry builder" gap, now including the shadow
        # contract-evaluation fields _change_to_dict already carries. A
        # root TYPE_* change under --report-mode leaf previously lost
        # contract_relevance/contract_reason_code/contract_assurance even
        # though the identical finding kept them under --report-mode full
        # (Codex review, fresh evidence).
        #
        # The gate contribution is computed, not left at the helper's default
        # `0`: that default is right for the audit ledgers (an out-of-surface
        # or suppressed finding reaches no gate), but a leaf entry is an
        # ordinary `result.changes` finding that does. Without this, an
        # evaluated `type_size_changed` driving a real exit 4 serialized as
        # `compatibility_decision: BREAKING` beside `gate_contribution: 0`
        # under --report-mode leaf alone (Codex review, fresh evidence).
        from .severity import gate_contribution_for_change

        _add_contract_evaluation_fields(
            entry,
            c,
            gate_contribution=gate_contribution_for_change(
                cast(HasKind, c),
                severity_config,
                policy=effective_policy,
                kind_sets=eff_sets,
                policy_file=result.policy_file,
            ),
        )
        return entry

    leaf_changes_list = [_leaf_entry(c) for c in type_changes]
    non_type_list = [
        _change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            root_cause=_rc_lookup.get(_finding_id(c)),
            root_cause_evidence=_rc_evidence.get(_finding_id(c)),
            policy_file=result.policy_file,
            evidence_tiers=result.evidence_tiers,
            severity_config=severity_config,
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
    gate = gate_decision_for_result(result, severity_config)
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            gate=gate,
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
    _add_contract_context(
        d, result, _displayed_with_scoped_only(result, changes, show_only),
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config, include_exit_decision=include_exit_decision)
    # Codex review: full/root-cause mode call this; leaf mode never did,
    # silently dropping policy_overrides/policy_reclassify here.
    _add_policy_overrides(d, result)
    scope = _scope_dict(result)
    if scope is not None:
        d["scope"] = scope
    return _reporter_contract_blocks.render_json_with_side_facts(d, result, indent=indent, severity_config=severity_config, gate=gate, show_only=show_only, contract_evaluation=contract_evaluation)


def _add_entries_to_root_causes(
    d: dict[str, object],
    keyed_entries: list[tuple[str, str, dict[str, object]]],
) -> None:
    """Fold additional ``(key, root_display, entry)`` triples into an
    already-built ``--report-mode root-cause`` payload, for synthetic
    scoped-gate entries computed after :func:`_to_json_root_cause` already
    grouped ``result.changes`` (else they'd sit in ``changes[]`` but never in
    ``root_causes``). No-op if *d* has no ``root_causes`` list.

    Each touched group's ``strongest_evidence_level``/``evidence_levels``
    (G29 Phase 6, Codex review) is recomputed from *all* its findings --
    pre-existing and newly folded-in alike -- every time a group is touched,
    via :func:`~abicheck.root_cause_evidence.fold_evidence_summaries` over each finding's own
    ``impact_assessment.root_cause_evidence``. Without this, a scoped-only
    entry folded into an existing (or brand-new) group here never
    contributed its own evidence to the group summary at all, unlike a
    group :func:`_to_json_root_cause` builds entirely from ``changes``.
    """
    root_causes = d.get("root_causes")
    if not isinstance(root_causes, list):
        return
    by_id = {
        group["root_cause_id"]: group
        for group in root_causes
        if isinstance(group, dict)
    }
    touched: set[str] = set()
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
        touched.add(root_cause_id)
    for root_cause_id in touched:
        group = by_id[root_cause_id]
        evidence = fold_evidence_summaries(
            entry_root_cause_evidence(f)
            for f in group["findings"]
            if isinstance(f, dict)
        )
        if evidence is not None:
            group["strongest_evidence_level"] = evidence["strongest_evidence_level"]
            group["evidence_levels"] = evidence["evidence_levels"]
    d["root_cause_count"] = len(root_causes)


def _scoped_only_extra_causes(
    result: DiffResult, show_only: str | None
) -> frozenset[str]:
    """``caused_by_type`` values from ``result.scoped_only_changes`` (G29
    Phase 3 follow-up, review finding).

    ``cli_compare_fold.py``'s scoped-gate fold-in appends scoped-only
    changes *after* a JSON serializer already built its root-cause lookup --
    folding their ``caused_by_type`` in here too keeps a `changes` entry
    that only correlates via one of those later-appended findings from
    being locked into its own singleton group, matching
    ``sarif.to_sarif``/``junit_report._build_testsuite``'s identical,
    single-pass fold.
    """
    scoped_only = scoped_only_changes_filtered(result, show_only)
    return frozenset(c.caused_by_type for c in scoped_only if c.caused_by_type)


def _to_json_root_cause(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False, include_exit_decision: bool = True, contract_evaluation: bool = False,
) -> str:
    """``--report-mode root-cause`` JSON output (G29 Phase 3, ADR-052 slice 3).

    Groups ``result.changes`` (after ``--show-only`` filtering) by
    ``Change.caused_by_type`` when set, else each change is its own
    singleton group keyed by its own ``symbol`` -- reusing the existing
    ``caused_by_type`` field ``diff_filtering.py``'s redundancy collapse and
    ``internal_leak.py``'s call-graph-leak overlay already set, rather than
    requiring new producer wiring. ``root_cause_id`` is a stable hash of this
    grouping key -- deliberately a different scheme from
    `RootCauseCorrelator`'s own (G29 Phase 6), whose evidence-ranked groups
    each `root_causes[]` entry additionally annotates with
    ``strongest_evidence_level``/``evidence_levels`` (see
    :func:`~abicheck.root_cause_evidence.root_cause_group_evidence`) without adopting its id scheme.
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
        changes = _suppress_dangling_correlation_notes(changes)
    effective_policy = result.policy or "strict_abi"
    eff_sets = result._effective_kind_sets()

    # G29 Phase 3 slice 3 follow-up (Codex review): the scoped-gate
    # (--used-by/--required-symbol) fold-in in cli_compare_fold.py appends
    # scoped-only changes to this report *after* this function has already
    # built root_causes -- without folding their caused_by_type into the
    # grouping here too, a change in `changes` that only correlates via one
    # of those later-appended findings would already be locked into its own
    # singleton group by the time the fold-in tries to join it, contradicting
    # SARIF's identical grouping (computed in one pass, so it doesn't have
    # this two-phase gap). See _scoped_only_extra_causes for the shared
    # computation every other JSON mode now uses too.
    extra_causes = _scoped_only_extra_causes(result, show_only)

    # Build each finding's dict exactly once; group the same dict objects by
    # key so `changes` (flat, backward-compatible -- every existing report
    # mode provides it, `_to_json_leaf` included) and `root_causes[].findings`
    # never drift from each other.
    _rc_lookup = root_cause_lookup_for_changes(changes, extra_causes=extra_causes)
    _rc_evidence = scoped_only_evidence_lookup(result, changes, show_only)
    entry_by_id = {
        id(c): _change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
            root_cause=_rc_lookup.get(_finding_id(c)),
            root_cause_evidence=_rc_evidence.get(_finding_id(c)),
            evidence_tiers=result.evidence_tiers,
            severity_config=severity_config,
        )
        for c in changes
    }
    entries = [entry_by_id[id(c)] for c in changes]
    grouped = _group_changes_by_root_cause(changes, extra_causes=extra_causes)

    root_causes = []
    for key, root_display, group_changes in grouped:
        root_cause_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        entry: dict[str, object] = {
            "root_cause_id": root_cause_id,
            "root": root_display,
            "finding_count": len(group_changes),
            "findings": [entry_by_id[id(c)] for c in group_changes],
        }
        group_evidence = root_cause_group_evidence(group_changes, _rc_evidence)
        if group_evidence is not None:
            entry["strongest_evidence_level"] = group_evidence[
                "strongest_evidence_level"
            ]
            entry["evidence_levels"] = group_evidence["evidence_levels"]
        root_causes.append(entry)

    d = _build_json_base(result)
    _add_abi_surface_breakdown(d, result)
    _add_evidence_fields(d, result)
    d["policy"] = effective_policy
    if show_only:
        _add_show_only_filter(d, result, changes, show_only)
    gate = gate_decision_for_result(result, severity_config)
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            gate=gate,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    d["changes"] = entries
    d["root_causes"] = root_causes
    d["root_cause_count"] = len(root_causes)
    # Codex review: full mode's _add_changes_block (and leaf mode's own copy)
    # both surface these audit-trail fields whenever they're non-empty --
    # root-cause mode built its own JSON path and skipped them, silently
    # dropping the redundant/modulated-finding trail for a filtered report.
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    if result.pattern_modulations:
        d["pattern_modulations"] = result.pattern_modulations
    _add_suppression(d, result)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    _add_contract_context(
        d, result, _displayed_with_scoped_only(result, changes, show_only),
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config, include_exit_decision=include_exit_decision)
    _add_detectors(d, result)
    _add_confidence_evidence(d, result)
    _add_policy_overrides(d, result)
    # Codex review: full/leaf JSON both emit the machine-readable
    # `scope` block (resolved/fell_back/manual_review_required) when
    # --scope-public-headers was requested -- root-cause mode dropped it,
    # hiding the fallback/manual-review warning for scoped root-cause runs.
    scope = _scope_dict(result)
    if scope is not None:
        d["scope"] = scope
    return _reporter_contract_blocks.render_json_with_side_facts(d, result, indent=indent, severity_config=severity_config, gate=gate, show_only=show_only, contract_evaluation=contract_evaluation)


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
            _filtered_internal_entry(c) for c in result.out_of_surface_changes
        ],
    }


def _filtered_internal_entry(c: Change) -> dict[str, object]:
    entry: dict[str, object] = {
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
    }
    # ADR-049 Phase 3 (Codex review, fresh evidence): result.out_of_surface_changes
    # is the same list _apply_contract_evaluation_shadow already stamps
    # (folded into all_changes alongside `kept`) -- this second, independent
    # serialization of the identical Change objects
    # (scope.filtered_internal_changes, distinct from
    # surface_scope.out_of_surface_changes above) never read the fields, so a
    # --contract consumer of this established ledger missed the decision even
    # though the sibling ledger already carried it.
    _add_contract_evaluation_fields(entry, c)
    return entry


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
    """Add show_only_filter + filtered_summary when a show_only filter is active.

    The three verdict counters are over the *evaluated* subset, exactly like
    the main ``summary`` block (ADR-049 D1) -- a NOT_EVALUATED finding was
    never scored by compatibility policy, so counting one here reported
    ``verdict: NO_CHANGE`` and ``summary.breaking: 0`` beside
    ``filtered_summary.breaking: 1`` in a single document (Codex review,
    reproduced with ``--show-only breaking`` on a proven-out-of-contract
    finding). ``total_changes`` stays inclusive, matching the main summary's
    own rule: it counts what the filter *displays*, not what gated.
    """
    from .contract_gating import is_evaluated

    d["show_only_filter"] = show_only
    scored = [c for c in changes if is_evaluated(c)]
    d["filtered_summary"] = {
        "breaking": sum(
            1
            for c in scored
            if result._effective_verdict_for_change(c) == Verdict.BREAKING
        ),
        "source_breaks": sum(
            1
            for c in scored
            if result._effective_verdict_for_change(c) == Verdict.API_BREAK
        ),
        "risk_changes": sum(
            1
            for c in scored
            if result._effective_verdict_for_change(c) == Verdict.COMPATIBLE_WITH_RISK
        ),
        "total_changes": len(changes),
    }


def _suppressed_change_entry(
    c: Change,
    *,
    root_cause: tuple[str, str] | None = None,
    root_cause_evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    """Minimal audit-trail entry for one suppressed change, plus the
    impact-assessment decision it was actually suppressed with (G29 Phase 3
    slice 1, ADR-052 follow-up, Codex review: this is the one call site that
    passes ``suppressed=True`` -- without it, ``decision.state:
    "suppressed"`` was advertised but never actually reachable from
    production reporting).

    ``root_cause``/``root_cause_evidence`` (G29 Phase 3/6 follow-ups): the
    caller resolves both from a lookup scoped to ``result.suppressed_changes``
    itself -- a suppressed finding's root cause is computed relative to other
    *suppressed* findings, not folded together with the kept ``changes[]``
    list's own grouping.
    """
    entry: dict[str, object] = {
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
    }
    assessment = assess_change(
        c,
        suppressed=True,
        root_cause=root_cause,
        root_cause_evidence=root_cause_evidence,
    )
    entry["reachability_state"] = assessment.reachability_state.value
    if assessment.has_signal():
        entry["impact_assessment"] = assessment.to_dict()
    if getattr(c, "symbol_binding", None):
        entry["symbol_binding"] = c.symbol_binding
    # ADR-049 Phase 3 (Codex review, fresh evidence): suppression is a
    # display/gate decision, not a reason to erase the contract-relevance
    # decision checker._apply_contract_evaluation_shadow already stamped on
    # this Change -- a no-op when contract_evaluation was never requested,
    # mirroring this helper's other callers.
    _add_contract_evaluation_fields(entry, c)
    return entry


def _add_suppression(d: dict[str, object], result: DiffResult) -> None:
    """Add suppression block (file flag, count, suppressed change list)."""
    _rc_lookup = root_cause_lookup_for_changes(result.suppressed_changes)
    _rc_evidence = root_cause_evidence_lookup_for_changes(result.suppressed_changes)
    d["suppression"] = {
        "file_provided": result.suppression_file_provided,
        "suppressed_count": result.suppressed_count,
        "suppressed_changes": [
            _suppressed_change_entry(
                c,
                root_cause=_rc_lookup.get(_finding_id(c)),
                root_cause_evidence=_rc_evidence.get(_finding_id(c)),
            )
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
    # ADR-050 D2 (schema 2.17) — report-level comparability metadata, never a
    # Change/ChangeKind finding, so it stays unreachable by severity promotion.
    # Omitted entirely (not emitted as null) when unset, matching every other
    # optional field in this builder.
    if result.contract_coverage is not None:
        d["contract_coverage"] = result.contract_coverage
    if result.assurance is not None:
        d["assurance"] = result.assurance


def _add_policy_overrides(d: dict[str, object], result: DiffResult) -> None:
    """Add policy file overrides/reclassify rules (custom re-classifications)
    when present.

    ``policy_reclassify`` (report_schema_version 2.30) lists the *active
    rule set* -- the same level of audit detail ``policy_overrides`` already
    gives for kind-global overrides. The per-finding "which rule fired"
    attribution 2.30's own history entry originally deferred is a separate
    field, ``change.reclassified_by`` (report_schema_version 2.31, stamped in
    ``_change_to_dict`` via ``severity.reclassify_rule_for_change`` -- see
    ``abicheck/schemas/__init__.py``'s 2.31 history entry). Each rule's dict
    here comes from ``ReclassifyRule.to_report_dict()`` -- shared with
    ``sarif.py``'s ``policyReclassify`` so the two can't drift on field
    set/spelling.
    """
    if result.policy_file and result.policy_file.overrides:
        d["policy_overrides"] = {
            kind.value: verdict.value
            for kind, verdict in result.policy_file.overrides.items()
        }
        if result.policy_file.source_path:
            d["policy_file"] = str(result.policy_file.source_path)
    if result.policy_file and result.policy_file.reclassify:
        from .reclassify import active_reclassify_rules

        active = active_reclassify_rules(result.policy_file.reclassify)
        if active:
            d["policy_reclassify"] = [rule.to_report_dict() for rule in active]
            if result.policy_file.source_path:
                d["policy_file"] = str(result.policy_file.source_path)


def _add_changes_block(
    d: dict[str, object],
    result: DiffResult,
    changes: list[Change],
    effective_policy: str,
    eff_sets: KindSets | None,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
) -> None:
    """Add changes list and optional redundant-count / pattern-modulations fields.

    *show_only* folds ``result.scoped_only_changes``' ``caused_by_type``
    values into the root-cause grouping the same way ``_to_json_root_cause``
    does (review finding) -- without it, a finding here correlating only via
    a scoped-only overlay silently lost its ``impact_assessment.root_cause_id``,
    disagreeing with root-cause mode, SARIF, and JUnit. ``root_cause_evidence``
    (G29 Phase 6, Codex review) gets the same treatment via
    :func:`~abicheck.root_cause_evidence.scoped_only_evidence_lookup`, correlating against the real
    scoped-only ``Change`` objects rather than just their ``caused_by_type``.
    """
    _rc_lookup = root_cause_lookup_for_changes(
        changes, extra_causes=_scoped_only_extra_causes(result, show_only)
    )
    _rc_evidence = scoped_only_evidence_lookup(result, changes, show_only)
    d["changes"] = [
        _change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
            root_cause=_rc_lookup.get(_finding_id(c)),
            root_cause_evidence=_rc_evidence.get(_finding_id(c)),
            evidence_tiers=result.evidence_tiers,
            severity_config=severity_config,
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
    require_complete_analysis: bool = False,
    include_exit_decision: bool = True,  # exit block (2.41); see exit_decision.py
    contract_evaluation: bool = False,  # ADR-061 P2 item 5
) -> str:
    if stat:
        return to_stat_json(
            result, indent=indent, severity_config=severity_config,
            require_complete_analysis=require_complete_analysis, show_only=show_only, contract_evaluation=contract_evaluation)

    if report_mode == "leaf":
        return _to_json_leaf(
            result, indent=indent, show_only=show_only, severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            include_exit_decision=include_exit_decision, contract_evaluation=contract_evaluation)

    if report_mode == "root-cause":
        return _to_json_root_cause(
            result, indent=indent, show_only=show_only, severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            include_exit_decision=include_exit_decision, contract_evaluation=contract_evaluation)

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        changes = _suppress_dangling_correlation_notes(changes)

    d = _build_json_base(result)
    _add_abi_surface_breakdown(d, result)
    _add_evidence_fields(d, result)
    effective_policy = result.policy or "strict_abi"
    d["policy"] = effective_policy
    eff_sets = result._effective_kind_sets()

    if show_only:
        _add_show_only_filter(d, result, changes, show_only)

    # Severity-categorized summary when severity config is provided
    gate = gate_decision_for_result(result, severity_config)
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = _build_severity_json(
            changes,
            severity_config,
            gate=gate,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )

    _add_changes_block(
        d,
        result,
        changes,
        effective_policy,
        eff_sets,
        show_only,
        severity_config=severity_config,
    )
    _add_suppression(d, result)
    _add_surface_scope(d, result)
    _add_reconciled(d, result)
    _add_contract_context(
        d, result, _displayed_with_scoped_only(result, changes, show_only),
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config, include_exit_decision=include_exit_decision)
    _add_detectors(d, result)
    _add_confidence_evidence(d, result)
    _add_policy_overrides(d, result)
    _add_trailing_fields(d, result, show_impact, show_only)
    return _reporter_contract_blocks.render_json_with_side_facts(d, result, indent=indent, severity_config=severity_config, gate=gate, show_only=show_only, contract_evaluation=contract_evaluation)


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


def _add_contract_evaluation_fields(
    d: dict[str, object],
    c: object,
    *,
    gate_contribution: int = 0,
) -> None:
    """Attach ADR-049's per-finding contract decision fields to *d*, if *c*
    carries one; always stamps ``finding_id``/``canonical_finding_id`` first.

    Shared by :func:`_change_to_dict` and :func:`_add_surface_scope` so a
    demoted finding's decision is exposed the same way a kept finding's
    already is. ``contract_relevance is None`` (the default) skips only
    the contract-specific fields below.

    *gate_contribution* completes ADR-049 D1's canonical per-finding shape.
    It defaults to ``0`` -- the true answer for every audit ledger this
    helper serializes, since none of those findings reach a gate. Only the
    ``changes`` path passes a computed value, from
    :func:`~abicheck.severity.gate_contribution_for_change`.
    """
    # The audit-ledger serializers (`_out_of_surface_entry`,
    # `_suppressed_change_entry`, `_add_reconciled`, `_filtered_internal_entry`)
    # build compact dicts that never emit `finding_id` themselves, so a
    # consumer can't join a demoted/suppressed/reconciled finding to its
    # decision. Stamped here, only when absent -- and unconditionally,
    # ahead of the contract_relevance early return below: an ordinary run
    # without `--contract` still calls this on every one of those entries and
    # still needs a joinable id (Codex review: an earlier revision stamped
    # this after the early return instead, silently skipping it on every
    # default-run audit-ledger entry).
    if "finding_id" not in d:
        from .finding_identity import report_finding_id

        d["finding_id"] = report_finding_id(c)
    # Same reasoning, for finding_id's backend-independent sibling (2.36).
    if "canonical_finding_id" not in d:
        from .finding_identity import report_canonical_finding_id

        d["canonical_finding_id"] = report_canonical_finding_id(c)

    contract_relevance = getattr(c, "contract_relevance", None)
    if contract_relevance is None:
        return
    d["contract_relevance"] = contract_relevance.value
    d["contract_reason_code"] = getattr(c, "contract_reason_code", None)
    contract_assurance = getattr(c, "contract_assurance", None)
    if contract_assurance is not None:
        d["contract_assurance"] = contract_assurance.value
    # ADR-049 D1's canonical trio. `compatibility_decision` is JSON `null`
    # for a NOT_EVALUATED finding and must stay that way: `null` records that
    # compatibility policy never ran, which is a different statement from any
    # verdict -- including COMPATIBLE -- that a renderer might be tempted to
    # fill in.
    from .contract_gating import evaluation_status_of

    status = evaluation_status_of(c)
    if status is not None:
        d["compatibility_evaluation_status"] = status.value
    decision = getattr(c, "compatibility_decision", None)
    d["compatibility_decision"] = getattr(decision, "value", None)
    d["gate_contribution"] = gate_contribution
    # ADR-049 Phase 3's provider-evidence ledger. Emitted even when empty --
    # `[]` is the real answer for a non-entity finding, and omitting the key
    # would be indistinguishable from an unstamped finding.
    contract_evidence_refs = getattr(c, "contract_evidence_refs", None)
    if contract_evidence_refs is not None:
        d["contract_evidence_refs"] = list(contract_evidence_refs)


def _change_annotation_fields(c: Any) -> dict[str, Any]:
    """Optional per-change attribution/annotation fields for the JSON report.

    Each is omitted rather than emitted as ``null`` when it carries nothing, so
    a consumer can tell "not applicable" from "empty". The reasoning behind the
    individual entries is kept with them below.
    """
    out: dict[str, Any] = {}
    # Source location
    loc = getattr(c, "source_location", None)
    if loc:
        out["source_location"] = loc
    # Affected symbols
    affected = getattr(c, "affected_symbols", None)
    if affected:
        out["affected_symbols"] = affected
    # Redundancy annotation
    caused_by = getattr(c, "caused_by_type", None)
    if caused_by:
        out["caused_by_type"] = caused_by
    caused_count = getattr(c, "caused_count", 0)
    if caused_count > 0:
        out["caused_count"] = caused_count
    # ADR-027 A4 — disclose a pattern-aware modulation on the finding itself.
    mod_reason = getattr(c, "modulation_reason", None)
    if mod_reason:
        out["modulation_reason"] = mod_reason
        out["modulation_rule"] = getattr(c, "modulation_rule", None)
        eff = getattr(c, "effective_verdict", None)
        if isinstance(eff, Verdict):
            out["effective_verdict"] = eff.value
    # ADR-041 P0 roadmap item 2 — this finding correlates with another
    # finding (currently: PUBLIC_API_INTERNAL_DEPENDENCY_ADDED correlating
    # with the same entry's own body/type-hash change), named by ChangeKind
    # value so a machine consumer can act on it without parsing description.
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        out["correlated_change_kind"] = correlated
    if getattr(c, "symbol_binding", None):
        out["symbol_binding"] = c.symbol_binding
    return out


def _change_reachability_fields(c: Any) -> dict[str, Any]:
    """Structured reachability evidence and graph-impact data for one change.

    Machine-readable counterparts to what used to be description prose only --
    see the per-entry comments for which ADR each came from.
    """
    out: dict[str, Any] = {}
    # ADR-044 P1 item 4 — structured reachability evidence (previously
    # description-text-only): whether a suppression rule's reachability gate
    # tagged this change public-reachable, how (layout/call-graph/direct),
    # and the shortest proof path, so a machine consumer doesn't need to
    # parse the suppression_would_hide_public_break diagnostic's prose.
    if getattr(c, "public_reachable", False):
        out["public_reachable"] = True
        reach_kind = getattr(c, "reachability_kind", None)
        if reach_kind:
            out["reachability_kind"] = reach_kind
        proof_path = getattr(c, "reachability_proof_path", None)
        if proof_path:
            out["reachability_proof_path"] = proof_path
    # G31 Phase B B3 (ADR-048) — structured graph impact/proof-path data:
    # the machine-readable counterpart of reachability_proof_path's prose,
    # as a list of node/edge reference dicts, plus which public root(s) it
    # traces back to and whether the dependency is direct or transitive.
    affected_roots = getattr(c, "affected_public_roots", None)
    if affected_roots:
        out["affected_public_roots"] = affected_roots
    impact_path = getattr(c, "impact_proof_path", None)
    if impact_path:
        out["impact_proof_path"] = impact_path
    impact_direct = getattr(c, "impact_is_direct", None)
    if impact_direct is not None:
        out["impact_is_direct"] = impact_direct
    return out


def _reclassified_by_for_change(c: object, policy_file: object | None) -> str | None:
    """``reclassified_by`` audit value for *c*, or ``None`` -- shared by
    :func:`_change_to_dict` and leaf mode's ``_leaf_entry`` (Codex review)
    so the two entry builders can't drift on this field. Falls back to
    ``rule.to_verdict.value`` rather than ``rule.to``, since a directly-
    constructed ``ReclassifyRule`` could leave the latter empty/mismatched
    -- see ``severity.reclassify_rule_for_change`` for the full precedence.
    """
    from .severity import reclassify_rule_for_change

    rule = reclassify_rule_for_change(cast(HasKind, c), policy_file)
    if rule is None:
        return None
    return cast(str, rule.label or rule.reason or rule.to_verdict.value)


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
    root_cause: tuple[str, str] | None = None,
    root_cause_evidence: dict[str, object] | None = None,
    evidence_tiers: Sequence[str] = (),
    severity_config: SeverityConfig | None = None,
) -> dict[str, object]:
    """Convert a Change to a JSON-serializable dict with impact and metadata.

    ``evidence_status_override`` lets a caller assert a stronger epistemic
    status than the finding's own classification implies — e.g.
    ``appcompat_to_json`` marks every finding it already proved a specific
    consumer depends on as ``EvidenceStatus.CONSUMER_PROVEN``, regardless of
    the finding's own kind.

    ``root_cause`` (G29 Phase 3 follow-up, ADR-052), when given, is *c*'s
    ``(root_cause_id, root_display)`` pair — forwarded to
    :func:`~abicheck.impact.engine.assess_change` so
    ``impact_assessment.root_cause_id`` is populated. Callers resolve this
    once per report via
    :func:`~abicheck.reporter_markdown.root_cause_lookup_for_changes` rather
    than recomputing it per change. ``root_cause_evidence`` (G29 Phase 6) is
    the analogous per-change entry from
    :func:`~abicheck.impact.correlation.correlate_root_causes`, resolved the
    same way.

    ``evidence_tiers`` is the owning comparison's ``DiffResult.evidence_tiers``
    (P0 evidence-provider audit) — when a caller has it (most do; the
    default ``()`` reads as "unknown", not "absent", matching
    :func:`~.checker_policy.has_binary_evidence`'s own convention), it
    downgrades an otherwise ``ARTIFACT_PROVEN`` status to ``UNATTRIBUTED``
    for a comparison positively known to have never examined a real binary.

    ``severity_config`` is the run's resolved gate configuration, when it has
    one — it decides ADR-049's per-finding ``gate_contribution`` (see the call
    to :func:`~abicheck.severity.gate_contribution_for_change` below). ``None``
    means the legacy verdict-based scheme, not "no gate".
    """
    kind = getattr(c, "kind", None)
    reclassified_by: str | None = None
    if isinstance(kind, ChangeKind) and kind_sets:
        from .severity import effective_verdict_for_change

        verdict = effective_verdict_for_change(
            cast(HasKind, c),
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
        )
        severity = _VERDICT_TO_SEVERITY_LABEL.get(verdict, "unknown")
        # Per-change reclassify: disclosure (Codex review) -- see
        # _reclassified_by_for_change's own docstring.
        reclassified_by = _reclassified_by_for_change(c, policy_file)
    elif kind:
        severity = _kind_to_severity(kind, policy)
    else:
        severity = "unknown"
    evidence_status = evidence_status_override
    if evidence_status is None and isinstance(kind, ChangeKind):
        evidence_status = evidence_status_for_result(cast(HasKind, c), evidence_tiers)
    d: dict[str, object] = {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", ""),
        "description": getattr(c, "description", ""),
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "severity": severity,
    }
    if reclassified_by:
        d["reclassified_by"] = reclassified_by
    if isinstance(kind, ChangeKind):
        d["operation"] = operation_for_kind(kind.value)
        d["finding_id"] = _finding_id(c)
        # Backend-independent sibling of finding_id (schema 2.36).
        from .finding_identity import report_canonical_finding_id

        d["canonical_finding_id"] = report_canonical_finding_id(c)
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
    d.update(_change_annotation_fields(c))
    d.update(_change_reachability_fields(c))
    # G29 Phase 3 slice 1 (ADR-052): reachability_state has existed on Change
    # since PR #607 but was never serialized -- without it, a JSON consumer
    # cannot tell a PROVEN_UNREACHABLE finding apart from one the graph walk
    # never examined at all (UNKNOWN), since both leave public_reachable
    # False. impact_assessment is the unified read view over the scattered
    # reachability/impact fields above; only emitted when it carries
    # information beyond the all-defaults case, matching this function's own
    # convention of not padding every plain finding with an empty object.
    assessment = assess_change(
        c, root_cause=root_cause, root_cause_evidence=root_cause_evidence
    )
    d["reachability_state"] = assessment.reachability_state.value
    if assessment.has_signal():
        d["impact_assessment"] = assessment.to_dict()
    # ADR-049 D1's `gate_contribution`, computed for a `changes` entry (the
    # only findings that reach a gate at all) from the same severity/legacy
    # scheme the run itself exits on -- see
    # `severity.gate_contribution_for_change`. `severity_config` is None on
    # the legacy scheme, where the contribution is the finding's own
    # verdict-to-exit mapping.
    from .severity import gate_contribution_for_change

    _add_contract_evaluation_fields(
        d,
        c,
        gate_contribution=(
            gate_contribution_for_change(
                cast(HasKind, c),
                severity_config,
                policy=policy,
                kind_sets=kind_sets,
                policy_file=policy_file,
            )
            if isinstance(kind, ChangeKind)
            else 0
        ),
    )
    return d


def _build_severity_json(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    gate: GateDecision,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
) -> dict[str, object]:
    """Build severity information for JSON output.

    *changes* are the (possibly filtered) changes for display counts. *gate*
    is the caller's already-computed :func:`gate_decision_for_result` value
    (ADR-061 D9: this function projects a decision, it does not recompute
    one) -- always derived from the *unfiltered* change set, so
    ``--show-only`` does not affect the exit code it reports. *kind_sets*
    from ``DiffResult._effective_kind_sets()`` includes PolicyFile overrides.
    """
    from .severity import SeverityLevel, categorize_changes

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
    # ``gate`` was computed once by the caller from the unfiltered change
    # set, not ``changes`` (the possibly --show-only-filtered *display*
    # set) — otherwise hiding the one category that's actually failing the
    # build (e.g. ``--show-only=breaking`` when an addition promoted to
    # ``error`` is what's blocking) would report ``blocking: true`` alongside
    # ``blocking_categories: []`` (Codex review on #557).
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
    _rc_lookup = root_cause_lookup_for_changes(breaking)
    _rc_evidence = root_cause_evidence_lookup_for_changes(breaking)
    d["relevant_changes"] = [
        _change_to_dict(
            c,
            policy=appcompat_policy,
            kind_sets=appcompat_kind_sets,
            policy_file=appcompat_policy_file,
            evidence_status_override=EvidenceStatus.CONSUMER_PROVEN,
            root_cause=_rc_lookup.get(_finding_id(c)),
            root_cause_evidence=_rc_evidence.get(_finding_id(c)),
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

    return json.dumps(d, indent=indent)


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
