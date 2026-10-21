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

from typing import TYPE_CHECKING, cast

from .report.change_json import (
    change_to_dict,
    prewarm_change_demangling,
    reclassified_by_for_change,
    scope_dict,
)
from .report.change_operation import (
    entity_for_change as entity_for_change,
    operation_for_kind as operation_for_kind,
)
from .report.document_builder import build_report_document
from .report.json_sections import (
    SCOPED_GATE_HELPERS,
    add_abi_surface_breakdown,
    add_check_identity,
    add_confidence_evidence,
    add_detectors,
    add_evidence_fields,
    add_policy_overrides,
    add_reconciled,
    add_show_only_filter,
    add_suppression,
    add_surface_scope,
    build_json_base,
    build_severity_json,
    displayed_with_scoped_only,
    scoped_only_extra_causes,
)

if TYPE_CHECKING:
    from .policy.severity import SeverityConfig
from . import reporter_contract_blocks as _reporter_contract_blocks
from .checker import Change, DiffResult
from .report.change_inventory import render_change_inventory_json
from .report.dispatch_markdown import (
    _to_markdown_root_cause as _to_markdown_root_cause,
    to_markdown as to_markdown,
    to_review_digest as to_review_digest,
)
from .report.disposition_audit import add_disposition_audit as _add_disposition_audit
from .report.finding_evolution_section import (
    add_finding_evolution as _add_finding_evolution,
)
from .report.policy_facts import policy_facts_for, stat_facts_for
from .report.render_json import render_json
from .report.surface_changes import add_surface_changes as _add_surface_changes
from .report.view import (
    display_changes,
    finding_id,
    group_changes_by_root_cause,
    root_cause_id_for_key,
)
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
    _fmt_size as _fmt_size,
    _format_change_md as _format_change_md,
    _section_severity_label as _section_severity_label,
    entity_for_kind as entity_for_kind,
    root_cause_evidence_lookup_for_changes as root_cause_evidence_lookup_for_changes,
    root_cause_for_change as root_cause_for_change,
    root_cause_lookup_for_changes as root_cause_lookup_for_changes,
    to_stat as to_stat,
)
from .root_cause_evidence import (
    root_cause_group_evidence,
    scoped_only_evidence_lookup,
    scoped_only_impact_group_lookup,
)
from .schemas import REPORT_SCHEMA_VERSION


def to_stat_json(
    result: DiffResult,
    indent: int = 2,
    *,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False,
    show_only: str | None = None,
    contract_evaluation: bool = False,
) -> str:
    """JSON output for --stat mode: summary only, no changes array.

    *severity_config*, when given, adds a ``severity`` block (same shape as
    the full JSON report's — see :func:`build_severity_json`) so ``--stat
    -o json=...`` reflects the actual severity-aware gate instead of only
    the compatibility verdict. Without it, ``--stat`` output has historically
    bypassed severity handling entirely (it short-circuits in
    ``service.render_output`` before format dispatch).

    *show_only*/*contract_evaluation* feed only the scoped-gate fold at the
    tail of ``render_json_with_side_facts`` -- no ``changes`` array here to filter.
    """
    facts = stat_facts_for(
        result,
        severity_config,
        require_complete_analysis=require_complete_analysis,
    )
    summary = facts.summary
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
            "quality_issues": summary.quality_issues,
            "informational": summary.informational,
            "total_changes": summary.total_changes,
            "binary_compatibility_pct": round(summary.binary_compatibility_pct, 1),
            "affected_pct": round(summary.affected_pct, 1),
            "change_inventory": render_change_inventory_json(summary.inventory),
        },
    }
    # ADR-067 D3: a compact view may collapse detail; it may not omit the
    # raw-versus-effective counts.
    _add_disposition_audit(d, result, severity_config)
    _add_finding_evolution(d, result)
    add_check_identity(d, result)
    gate = facts.policy.gate
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = build_severity_json(
            result.changes,
            severity_config,
            gate=gate,
            policy=result.policy,
            kind_sets=facts.policy.kind_sets,
            policy_file=result.policy_file,
        )
    d["release_recommendation"] = facts.release_recommendation.to_dict()
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # Confidence & evidence metadata
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)
    if facts.assurance is not None:
        block, exit_contribution = facts.assurance
        d["analysis_assurance"] = block
        # Same persistence `add_contract_context` does for the full JSON path, which `--stat` bypasses entirely (Codex review).
        d["analysis_assurance_exit_contribution"] = exit_contribution
    from .reporter_contract_blocks import add_effective_config_digest

    add_effective_config_digest(
        d,
        result,
        severity_config=severity_config,
        require_complete_analysis=require_complete_analysis,
    )
    # Deliberately NOT `add_use_case_impact` here, unlike the full JSON path
    # (`reporter_contract_blocks`): this function's contract is the summary
    # object alone, and a per-finding attribution block is the opposite of a
    # summary. `compare` rejects `--stat --use-cases` outright rather than
    # dropping the manifest silently; this keeps the same promise for a
    # direct caller of the renderer (Codex review).
    return _reporter_contract_blocks.render_json_with_side_facts(
        d,
        result,
        indent=indent,
        helpers=SCOPED_GATE_HELPERS,
        severity_config=severity_config,
        gate=gate,
        show_only=show_only,
        contract_evaluation=contract_evaluation,
    )


def _to_json_root_cause(
    result: DiffResult,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False,
    contract_evaluation: bool = False,
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
    # Plan slice 7o (Codex review, PR #1284): this mode builds its finding
    # dicts directly rather than through `report.document_builder.build_report_document`,
    # so it needs its own batched demangle -- see
    # `prewarm_change_demangling`'s own docstring for why one per-projection
    # call is not enough.
    prewarm_change_demangling(result)
    policy_facts = policy_facts_for(result, severity_config)
    eff_sets = policy_facts.kind_sets
    changes = list(result.changes)
    if show_only:
        changes = display_changes(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
            today=None,
        )
    effective_policy = result.policy or "strict_abi"

    # G29 Phase 3 slice 3 follow-up (Codex review): the scoped-gate
    # (--used-by/--required-symbol) fold-in in cli_compare_fold.py appends
    # scoped-only changes to this report *after* this function has already
    # built root_causes -- without folding their caused_by_type into the
    # grouping here too, a change in `changes` that only correlates via one
    # of those later-appended findings would already be locked into its own
    # singleton group by the time the fold-in tries to join it, contradicting
    # SARIF's identical grouping (computed in one pass, so it doesn't have
    # this two-phase gap). See scoped_only_extra_causes for the shared
    # computation every other JSON mode now uses too.
    extra_causes = scoped_only_extra_causes(result, show_only)

    # Build each finding's dict exactly once; group the same dict objects by
    # key so `changes` (flat, backward-compatible -- every existing report
    # mode provides it, `_to_json_leaf` included) and `root_causes[].findings`
    # never drift from each other.
    _rc_lookup = root_cause_lookup_for_changes(changes, extra_causes=extra_causes)
    _rc_evidence = scoped_only_evidence_lookup(result, changes, show_only)
    _ig_lookup = scoped_only_impact_group_lookup(result, changes, show_only)
    entry_by_id = {
        id(c): change_to_dict(
            c,
            policy=effective_policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
            root_cause=_rc_lookup.get(finding_id(c)),
            root_cause_evidence=_rc_evidence.get(finding_id(c)),
            impact_group_id=_ig_lookup.get(finding_id(c)),
            evidence_tiers=result.evidence_tiers,
            severity_config=severity_config,
        )
        for c in changes
    }
    entries = [entry_by_id[id(c)] for c in changes]
    grouped = group_changes_by_root_cause(changes, extra_causes=extra_causes)

    root_causes = []
    for key, root_display, group_changes in grouped:
        root_cause_id = root_cause_id_for_key(key)
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

    d = build_json_base(result)
    add_abi_surface_breakdown(d, result)
    add_evidence_fields(d, result)
    d["policy"] = effective_policy
    if show_only:
        add_show_only_filter(d, result, changes, show_only)
    gate = policy_facts.gate
    if gate is not None:
        assert severity_config is not None  # gate is None otherwise
        d["severity"] = build_severity_json(
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
    # Codex review: full mode's add_changes_block (and leaf mode's own copy)
    # both surface these audit-trail fields whenever they're non-empty --
    # root-cause mode built its own JSON path and skipped them, silently
    # dropping the redundant/modulated-finding trail for a filtered report.
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    if result.pattern_modulations:
        d["pattern_modulations"] = result.pattern_modulations
    add_suppression(d, result)
    _add_disposition_audit(d, result, severity_config)
    _add_surface_changes(d, result, changes)
    _add_finding_evolution(d, result)
    add_surface_scope(d, result)
    add_reconciled(d, result)
    _add_contract_context(
        d,
        result,
        displayed_with_scoped_only(result, changes, show_only),
        require_complete_analysis=require_complete_analysis,
        severity_config=severity_config,
    )
    add_detectors(d, result)
    add_confidence_evidence(d, result)
    add_policy_overrides(d, result)
    # Codex review: full/leaf JSON both emit the machine-readable
    # `scope` block (resolved/fell_back/manual_review_required) when
    # --scope-public-headers was requested -- root-cause mode dropped it,
    # hiding the fallback/manual-review warning for scoped root-cause runs.
    scope = scope_dict(result)
    if scope is not None:
        d["scope"] = scope
    return _reporter_contract_blocks.render_json_with_side_facts(
        d,
        result,
        indent=2,
        helpers=SCOPED_GATE_HELPERS,
        severity_config=severity_config,
        gate=gate,
        show_only=show_only,
        contract_evaluation=contract_evaluation,
    )


def disposition_ledger_blocks(result: DiffResult) -> dict[str, object]:
    """The ADR-067 *disposition* disclosure blocks for one comparison.

    ``suppression`` (what a rule hid, with the rule's own id/source/reason),
    ``surface_scope`` (what public-surface scoping demoted, with each
    finding's exclusion reason), ``build_context_reconciled`` (what ADR-039
    reconciliation cleared as a context-free header-parse artifact) and
    ``pattern_modulations`` (what an ADR-027 rule reclassified) -- built by
    the very same functions the scalar ``compare`` JSON path uses, so there
    is one shape, not a second release-flavoured copy of it.

    Exists because the release fan-out had none of them (Codex review,
    PR #1284). A directory/package ``compare`` captured the ledgers as *text*
    and echoed them to stderr, so a requested JSON or Markdown release artifact
    carried the per-library counts but named neither the rules that fired
    nor the findings they disposed of -- a passing report could hide every
    break in it, which is the exact failure ADR-067's record-before-disposing
    rule exists to prevent, and a terminal log is not a report.

    Returns only the blocks that actually say something: ``surface_scope``
    when scoping ran (``add_surface_scope``'s own condition), ``suppression``
    when a suppression document was supplied or a finding was suppressed, and
    ``build_context_reconciled`` when reconciliation cleared anything. A
    library with none of them adds no keys, which is what keeps every release
    document produced without those settings unchanged -- and note this is a
    deliberate asymmetry with the scalar report, which emits an empty
    ``suppression`` block unconditionally.
    """
    # Warm the shared demangle cache once, here rather than at each caller:
    # every suppressed row resolves a demangled symbol and the scope ledger
    # demangles per row, so an unwarmed cache forks a `c++filt` per distinct
    # symbol. The release fan-out reaches this before any `to_json`, whose
    # own prewarm is therefore too late to help it (CodeRabbit review, PR
    # #1284). Batched and process-wide, so it also serves the scope ledger
    # the caller renders next.
    prewarm_change_demangling(result)
    blocks: dict[str, object] = {}
    add_suppression(blocks, result)
    suppression = cast("dict[str, object]", blocks["suppression"])
    if not (suppression.get("file_provided") or suppression.get("suppressed_count")):
        del blocks["suppression"]
    add_surface_scope(blocks, result)
    # Build-context reconciliation is a *disposition* too, and the release
    # fan-out omitted it: a release whose every break was cleared as a
    # context-free header-parse artifact passed while its JSON and Markdown
    # named neither the findings nor the reasons (Codex review, PR #1284).
    # Same function the scalar path uses, so the block has one shape; it is
    # independent of both siblings above (reconciliation runs without
    # `--scope-public-headers` and without any suppression document) and
    # self-gating -- `add_reconciled` returns early when nothing was
    # cleared, so a library that reconciled nothing still adds no key.
    add_reconciled(blocks, result)
    # ADR-027 pattern modulation is a *reclassification*, which ADR-067 names
    # as a disposition alongside suppression and scope exclusion -- so the
    # same rule applies: a release whose every breaking finding a rule demoted
    # may not render a compatible artifact that names neither the rule nor the
    # reason. The release fan-out carried this only as a private
    # `_pattern_modulations_text` key, which its caller pops and writes to
    # stderr, so the requested JSON/Markdown had no structured block at all
    # (Codex review, PR #1284). Same list the scalar JSON path emits under the
    # same key, and self-gating: ADR-027 is opt-in (`--pattern-verdicts`, off
    # by default), so this is absent from every run that did not ask for it.
    if result.pattern_modulations:
        blocks["pattern_modulations"] = result.pattern_modulations
    # The suppression *audit* (stale, expired and near-expiry rules, and
    # rules that hid a BREAKING change) was the one per-library disclosure
    # the release fan-out still carried only as stderr text. Same builder and
    # shape as the scalar JSON's `suppression_audit`; self-gating, absent
    # when no audit was attached (release schema 1.12).
    from .reporter_contract_blocks import add_suppression_audit

    add_suppression_audit(blocks, result)
    return blocks


def to_json(
    result: DiffResult,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    require_complete_analysis: bool = False,
    contract_evaluation: bool = False,  # ADR-061 P2 item 5
) -> str:
    # A `stat` parameter used to short-circuit to `to_stat_json` here. Call
    # `to_stat_json` directly for the summary-only document; this function
    # renders the full report.
    #
    # Every public rendering entry point shares one report-mode check
    # (report/report_modes.py): enforcing the ``leaf`` retirement only in
    # `service_render` left this one silently rendering a *full* report for
    # a retired mode (CodeRabbit review, PR #1284).
    from .report.report_modes import normalize_report_mode

    report_mode, show_impact = normalize_report_mode(report_mode, show_impact)
    if report_mode == "root-cause":
        return _to_json_root_cause(
            result,
            show_only=show_only,
            severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        )

    doc = build_report_document(
        result,
        show_only=show_only,
        show_impact=show_impact,
        severity_config=severity_config,
        require_complete_analysis=require_complete_analysis,
        contract_evaluation=contract_evaluation,
    )
    return render_json(doc)


def release_finding_entry(
    c: Change, bucket: str, policy_file: object | None
) -> dict[str, object]:
    """The release fan-out's own minimal finding dict, plus ``reclassified_by``.

    Same ``bucket``/``kind``/``symbol``/``description``/``source_location``
    shape ``report.release_findings.release_finding_dicts`` has always
    built inline, with one addition: ``reclassified_by`` (schema 2.31) when a
    ``reclassify:`` rule decided this change's effective verdict, resolved
    through the identical :func:`reclassified_by_for_change` helper
    ``change_to_dict``/``_leaf_entry``/the retired ``cli_scan_baseline.
    _baseline_finding_dicts`` use -- so all four entry builders agree
    on which rule (if any) fired for a shared finding, instead of the release
    fan-out's own projection never computing the field at all. Lives here
    (not in ``cli_compare_release_matrix.py``, which sits at its own
    ``no_growth`` line cap with no headroom) since this module already owns
    ``reclassified_by_for_change`` and every other entry builder that calls
    it.
    """
    entry: dict[str, object] = {
        "bucket": bucket,
        "kind": c.kind.value,
        "symbol": c.symbol,
        "description": c.description,
        "source_location": c.source_location,
    }
    reclassified_by = reclassified_by_for_change(c, policy_file)
    if reclassified_by:
        entry["reclassified_by"] = reclassified_by
    return entry


def release_finding_detail_lines(entry: dict[str, object]) -> list[str]:
    """Markdown detail bullets (``description``/``reclassified_by``) for one
    :func:`release_finding_entry` dict -- the Markdown counterpart of that
    JSON entry, for ``cli_compare_receipt._release_md_library_findings``
    (Codex review, PR #1176 follow-up: the release Markdown report rendered
    kind/symbol only, discarding ``reclassified_by`` even after the JSON
    projection above gained it).
    """
    lines: list[str] = []
    description = entry.get("description")
    if description:
        lines.append(f"  - {description}")
    reclassified_by = entry.get("reclassified_by")
    if reclassified_by:
        lines.append(f"  - _Reclassified by:_ {reclassified_by}")
    return lines
