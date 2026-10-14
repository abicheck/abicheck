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

"""JSON report section builders used by ``report.build.build_report_document``.

Extracted from ``reporter.py`` (ADR-061 report/ migration) so ``report.build``
no longer reaches back into the legacy root module through private helpers and
an ``importlib`` hop. Behaviour is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..policy.evaluate import effective_verdict
from .change_json import (
    change_to_dict,
    metadata_dict,
    scope_dict,
    suppressed_change_entry,
)

if TYPE_CHECKING:
    from datetime import date

    from ..policy.severity import GateDecision, KindSets, SeverityConfig
from ..checker import Change, DiffResult, Verdict
from ..model.change import validate_check_id, validate_evidence_depth
from ..policy.disposition_close import ledger_for
from ..report_summary import build_summary, surface_breakdown
from ..reporter_contract_blocks import add_env_matrix_digest
from ..reporter_markdown import (
    root_cause_evidence_lookup_for_changes as root_cause_evidence_lookup_for_changes,
    root_cause_for_change as root_cause_for_change,
    root_cause_lookup_for_changes as root_cause_lookup_for_changes,
)
from ..root_cause_evidence import (
    entry_root_cause_evidence,
    fold_evidence_summaries,
    scoped_only_changes_filtered,
    scoped_only_evidence_lookup,
)
from ..schemas import REPORT_SCHEMA_VERSION
from ..semver import recommend_release_for_report
from .change_inventory import render_change_inventory_json
from .contract_fields import (
    add_contract_evaluation_fields as _add_contract_evaluation_fields,
)
from .scoped_gate import ScopedGateChangeHelpers
from .view import (
    finding_id,
    resolve_scoped_gate_findings,
    root_cause_id_for_key,
    root_cause_key_and_display,
)


def add_abi_surface_breakdown(d: dict[str, object], result: DiffResult) -> None:
    """Conditionally add ABI surface breakdown of the breaking set.

    Only present when there are RTTI/internal-namespace changes — additive,
    machine-facing.
    """
    from ..policy.evaluate import evaluate

    _bd = surface_breakdown(evaluate(result).breaking)
    if _bd.rtti or _bd.internal:
        d["abi_surface_breakdown"] = {
            "breaking_total": _bd.total,
            "public": _bd.public,
            "rtti_churn": _bd.rtti,
            "internal_churn": _bd.internal,
        }


def add_changes_block(
    d: dict[str, object],
    result: DiffResult,
    changes: list[Change],
    effective_policy: str,
    eff_sets: KindSets | None,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    today: date | None = None,
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
    *today*, forwarded to :func:`change_to_dict` for every entry, keeps this
    agreeing with an already-frozen ``ReportEnvelope`` (Codex review, fresh
    evidence).
    """
    _rc_lookup = root_cause_lookup_for_changes(
        changes, extra_causes=scoped_only_extra_causes(result, show_only)
    )
    _rc_evidence = scoped_only_evidence_lookup(result, changes, show_only)
    d["changes"] = [
        change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
            root_cause=_rc_lookup.get(finding_id(c)),
            root_cause_evidence=_rc_evidence.get(finding_id(c)),
            evidence_tiers=result.evidence_tiers,
            severity_config=severity_config,
            today=today,
        )
        for c in changes
    ]
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # ADR-027 A4 — pattern-aware modulation ledger (disclosed, reversible).
    if result.pattern_modulations:
        d["pattern_modulations"] = result.pattern_modulations


def add_confidence_evidence(d: dict[str, object], result: DiffResult) -> None:
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
    # E-S2 (docs/contribute/plans/cli-cleanup-phase-two.md, Block 5,
    # report_schema_version 3.1) — the per-dimension breakdown behind the
    # coarse `assurance` flag above. Same "only under --diagnostic-comparison,
    # omitted entirely otherwise" convention.
    if result.comparability_assurance is not None:
        d["comparability_assurance"] = dict(result.comparability_assurance)


def add_detectors(d: dict[str, object], result: DiffResult) -> None:
    """Add detector metadata — only detectors with findings or a coverage gap."""
    d["detectors"] = [
        {
            "name": det.name,
            "changes_count": det.changes_count,
            "enabled": det.enabled,
            "coverage_gap": det.coverage_gap,
            # ADR-067 D3: "did not run" is a different statement from "ran
            # and found nothing", and `changes_count: 0` cannot tell them
            # apart on its own.
            "not_evaluated": getattr(det, "not_evaluated", False),
            # ADR-063 T9 (schema 5.11): comparisons the detector ran but
            # declined to judge, each with its evidence reason.
            "declined": [
                {"entity": entity, "reason": reason} for entity, reason in det.declined
            ],
        }
        for det in result.detector_results
        if det.changes_count > 0 or det.coverage_gap is not None or bool(det.declined)
    ]


def add_evidence_fields(d: dict[str, object], result: DiffResult) -> None:
    """Add release recommendation, optional evidence coverage/metrics, and policy."""
    # Release recommendation (semver bump + soname action) — additive, machine-facing.
    d["release_recommendation"] = recommend_release_for_report(result).to_dict()
    # Evidence coverage (ADR-028 D7) — L0–L5 rows when a BuildSourcePack was
    # supplied; lets consumers tell artifact-proven from build-context-only
    # findings. Additive, present only when evidence was involved.
    if getattr(result, "layer_coverage", None):
        d["layer_coverage"] = result.layer_coverage
    # Evidence-entity-model I4: which relationships' absence is proven vs
    # unknown, per side (`compare.edge_query.edge_coverage_report`).
    if getattr(result, "edge_coverage", None):
        d["edge_coverage"] = result.edge_coverage
    # Evidence metrics (ADR-033 D6/D9) — collection timing + finding split, when
    # build-info/source facts were involved. Additive; lets CI tune mode choice.
    if getattr(result, "evidence_metrics", None):
        d["evidence_metrics"] = result.evidence_metrics


def add_policy_overrides(
    d: dict[str, object], result: DiffResult, *, today: date | None = None
) -> None:
    """Add policy file overrides/reclassify rules (custom re-classifications)
    when present.

    ``policy_reclassify`` (report_schema_version 2.30) lists the *active
    rule set* -- the same level of audit detail ``policy_overrides`` already
    gives for kind-global overrides. The per-finding "which rule fired"
    attribution 2.30's own history entry originally deferred is a separate
    field, ``change.reclassified_by`` (report_schema_version 2.31, stamped in
    ``change_to_dict`` via ``severity.reclassify_rule_for_change`` -- see
    ``abicheck/schemas/__init__.py``'s 2.31 history entry). Each rule's dict
    here comes from ``ReclassifyRule.to_report_dict()`` -- shared with
    ``sarif.py``'s ``policyReclassify`` so the two can't drift on field
    set/spelling. *today*, forwarded to :func:`active_reclassify_rules`, keeps
    the active-rule set consistent with an already-frozen ``ReportEnvelope``
    (Codex review, fresh evidence).
    """
    if result.policy_file and result.policy_file.overrides:
        d["policy_overrides"] = {
            kind.value: verdict.value
            for kind, verdict in result.policy_file.overrides.items()
        }
        if result.policy_file.source_path:
            d["policy_file"] = str(result.policy_file.source_path)
    if result.policy_file and result.policy_file.reclassify:
        from ..policy.reclassify import active_reclassify_rules

        active = active_reclassify_rules(result.policy_file.reclassify, today)
        if active:
            d["policy_reclassify"] = [rule.to_report_dict() for rule in active]
            if result.policy_file.source_path:
                d["policy_file"] = str(result.policy_file.source_path)


def add_reconciled(d: dict[str, object], result: DiffResult) -> None:
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


def add_show_only_filter(
    d: dict[str, object], result: DiffResult, changes: list[Change], show_only: str
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
    from ..model.contract_finding_relevance import is_evaluated

    d["show_only_filter"] = show_only
    scored = [c for c in changes if is_evaluated(c)]
    d["filtered_summary"] = {
        "breaking": sum(
            1 for c in scored if effective_verdict(result, c) == Verdict.BREAKING
        ),
        "source_breaks": sum(
            1 for c in scored if effective_verdict(result, c) == Verdict.API_BREAK
        ),
        "risk_changes": sum(
            1
            for c in scored
            if effective_verdict(result, c) == Verdict.COMPATIBLE_WITH_RISK
        ),
        "total_changes": len(changes),
    }


def add_suppression(d: dict[str, object], result: DiffResult) -> None:
    """Add suppression block (file flag, count, suppressed change list).

    Each entry carries the rule that hid it (ADR-067 D3) plus the
    ``--suppress`` document's own path, so "which rule hid this and why"
    survives into the report instead of being computed and dropped. The
    per-entry projection itself is
    ``report.change_json.suppressed_change_entry``; this function stays
    here because the root-cause lookups it feeds that projection live in
    ``reporter_markdown``, which this package may not import back.
    """
    ledger = ledger_for(result)
    _rc_lookup = root_cause_lookup_for_changes(result.suppressed_changes)
    _rc_evidence = root_cause_evidence_lookup_for_changes(result.suppressed_changes)
    d["suppression"] = {
        "file_provided": result.suppression_file_provided,
        "suppressed_count": result.suppressed_count,
        "suppressed_changes": [
            suppressed_change_entry(
                c,
                root_cause=_rc_lookup.get(finding_id(c)),
                root_cause_evidence=_rc_evidence.get(finding_id(c)),
                rule=ledger.rule_for(c),
            )
            for c in result.suppressed_changes
        ],
    }


def add_surface_scope(d: dict[str, object], result: DiffResult) -> None:
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


def add_trailing_fields(
    d: dict[str, object], result: DiffResult, show_impact: bool, show_only: str | None
) -> None:
    """Add show_only_applied flag and public-surface scope block (both optional)."""
    if show_impact:
        d["show_only_applied"] = show_only is not None
    scope = scope_dict(result)
    if scope is not None:
        d["scope"] = scope


def build_json_base(result: DiffResult) -> dict[str, object]:
    """Build the opening header + summary block of the JSON report dict."""
    summary = build_summary(result)
    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
    }
    add_check_identity(d, result)
    # Library file metadata (path, SHA-256, size) — always present for schema consistency
    d["old_file"] = metadata_dict(getattr(result, "old_metadata", None))
    d["new_file"] = metadata_dict(getattr(result, "new_metadata", None))
    add_env_matrix_digest(d, result)
    d["summary"] = {
        "breaking": summary.breaking,
        "source_breaks": summary.source_breaks,
        "risk_changes": summary.risk_count,
        "compatible_additions": summary.compatible_additions,
        "quality_issues": summary.quality_issues,
        "informational": summary.informational,
        "total_changes": summary.total_changes,
        "binary_compatibility_pct": round(summary.binary_compatibility_pct, 1),
        "affected_pct": round(summary.affected_pct, 1),
        "change_inventory": render_change_inventory_json(summary.inventory),
    }
    return d


def build_severity_json(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    gate: GateDecision,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """Build severity information for JSON output.

    *changes* are the (possibly filtered) changes for display counts. *gate*
    is the caller's already-computed :func:`gate_decision_for_result` value
    (ADR-061 D9: this function projects a decision, it does not recompute
    one) -- always derived from the *unfiltered* change set, so
    ``--show-only`` does not affect the exit code it reports. *kind_sets*
    from ``policy.evaluate.effective_kind_sets(DiffResult)`` includes PolicyFile overrides.
    *today*, forwarded to :func:`categorize_changes`, keeps this agreeing
    with an already-frozen ``ReportEnvelope`` (Codex review, fresh evidence).
    """
    from ..policy.severity import SeverityLevel, categorize_changes

    categorized = categorize_changes(
        changes,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
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
        # ADR-078: fixed at ``info`` -- not in ``config``, never in
        # ``blocking_categories``; reported so no finding leaves the block.
        "informational": {
            "severity": SeverityLevel.INFO.value,
            "count": len(categorized.informational),
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


def displayed_with_scoped_only(
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


def scoped_only_extra_causes(
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


def add_entries_to_root_causes(
    d: dict[str, object], keyed_entries: list[tuple[str, str, dict[str, object]]]
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
        root_cause_id = root_cause_id_for_key(key)
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


def add_check_identity(d: dict[str, object], result: DiffResult) -> None:
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


# ADR-063 T10: the bundle of per-change helpers report.scoped_gate.
# apply_scoped_gate needs but may not import itself (see that module's own
# docstring for the cycle a static/importlib back-reference would close).
# Built once here, since every name is already defined or imported in
# this module by this point, and threaded through render_json_with_side_
# facts's four call sites above rather than re-resolved per call. Must stay
# below change_to_dict's/add_entries_to_root_causes's own definitions
# (both defined in this module) -- a module executes top to bottom, so a
# forward reference here would raise NameError at import time.
SCOPED_GATE_HELPERS = ScopedGateChangeHelpers(
    change_to_dict=change_to_dict,
    add_entries_to_root_causes=add_entries_to_root_causes,
    finding_id=finding_id,
    root_cause_key_and_display=root_cause_key_and_display,
    root_cause_for_change=root_cause_for_change,
    resolve_scoped_gate_findings=resolve_scoped_gate_findings,
)
