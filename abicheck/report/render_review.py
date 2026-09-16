# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Bounded Markdown projection for the canonical review digest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .disposition_audit import DispositionAudit, render_disposition_audit_lines
from .surface_changes import SurfaceChangeSection, render_surface_changes_lines


@dataclass(frozen=True, slots=True)
class ImpactedSymbol:
    symbol: str
    kind: str


@dataclass(frozen=True, slots=True)
class ReviewDigest:
    library: str
    old_version: str
    new_version: str
    verdict_emoji: str
    verdict_label: str
    effect: str
    manual_review_banner: bool
    coverage_warnings: tuple[str, ...]
    additions_label: str
    breaking_count: int
    source_breaks_count: int
    risk_count: int
    additions_count: int
    scoped: bool
    out_of_surface_count: int
    bump_value: str
    soname_value: str
    impacted: tuple[ImpactedSymbol, ...]
    #: ADR-067 D3: raw-vs-effective counts (optional for a hand-built digest).
    disposition_audit: DispositionAudit | None = None
    surface_changes: SurfaceChangeSection | None = None  #: workstream G S1
    quality_issues_count: int = 0  #: non-addition compatible findings, own row
    env_matrix_source_sha256: str | None = None
    #: ADR-067: the rules that reclassified findings, as
    #: `PatternModulation.to_dict()` rows. The digest is the summary a
    #: reviewer approves a merge from, so an accepted result may not omit
    #: why it was accepted -- the same reason `disposition_audit` is here.
    #: Empty for every run with ADR-027's opt-in `--pattern-verdicts` off.
    pattern_modulations: tuple[Any, ...] = ()
    review_groups: tuple[dict[str, Any], ...] = ()
    policy: str = "strict_abi"
    gate_exit_code: int | None = None
    result_counts: dict[str, int] = field(default_factory=dict)
    evidence_summary: str = ""
    show_release_recommendation: bool = False


#: Per-list caps for the bounded review digest. Named rather than inline so
#: each bound sits next to the disclosure that reports it. Each caps a single
#: flat list, so none can exhibit the cross-group starvation documented at
#: ``surface_changes.MAX_COMPACT_SURFACE_ITEMS``; the counts rendered above
#: these lists come from the digest's own totals, so they stay complete
#: however far a list is cut.
MAX_REVIEW_PATTERN_MODULATIONS = 4
MAX_REVIEW_IMPACTED_SYMBOLS = 10
MAX_REVIEW_DISPOSITION_RULES = 4


def render_review_digest(digest: ReviewDigest) -> str:
    def compact(value: object, limit: int = 180) -> str:
        text = str(value).replace("\n", " ")
        return text if len(text) <= limit else text[: limit - 1] + "…"

    lines: list[str] = [
        f"## ABI review — `{digest.library}` {digest.old_version} → {digest.new_version}",
        "",
        f"**Verdict:** {digest.verdict_emoji} `{digest.verdict_label}` — {digest.effect}",
        f"**Policy:** `{digest.policy}` · **Gate:** "
        + (
            "not configured"
            if digest.gate_exit_code is None
            else (
                f"FAIL (exit {digest.gate_exit_code})"
                if digest.gate_exit_code
                else "PASS (exit 0)"
            )
        ),
        "",
    ]

    if digest.manual_review_banner:
        lines += [
            "> ⚠️ **Manual review required.** `--scope-public-headers` could not "
            "resolve the public surface, so analysis fell back to the full export "
            "table. Treat this result as *unconfirmed*, not a clean public surface.",
            "",
        ]
    if digest.coverage_warnings:
        lines += [f"> ⚠️ {w}" for w in digest.coverage_warnings]
        lines.append("")
    if digest.evidence_summary:
        lines += [f"**Evidence:** {digest.evidence_summary}.", ""]

    if digest.review_groups:
        gating = sum(int(group["gating_findings"]) for group in digest.review_groups)
        gating_groups = sum(
            bool(group["gating_findings"]) for group in digest.review_groups
        )
        lines += [
            f"**Review:** {gating} gating finding(s) in {gating_groups} gating "
            f"group(s); {len(digest.review_groups)} retained group(s) total",
            "",
        ]
        shown = digest.review_groups[:8]
        for number, group in enumerate(shown, 1):
            lines += [
                f"{number}. **{compact(group['display_name'])}** — {compact(group['transition'], 240)}",
                f"   {compact(group['consequence'], 240)}",
                f"   Action: {compact(group['action'], 200)}",
                f"   Findings: {', '.join(group['member_kinds'])}",
                "",
            ]
        if len(digest.review_groups) > len(shown):
            lines += [
                f"… {len(digest.review_groups) - len(shown)} more group(s) omitted; export `markdown` or `json` for details.",
                "",
            ]

    lines += [
        "| Category | Count |",
        "|---|---|",
        f"| ❌ Breaking (ABI) | {digest.breaking_count} |",
        f"| ⚠️ API breaks (source) | {digest.source_breaks_count} |",
        f"| ⚠️ Risk findings | {digest.risk_count} |",
        f"| ✅ {digest.additions_label} | {digest.additions_count} |",
    ]
    if digest.quality_issues_count:  # own row, not part of Additions above
        lines.append(f"| ℹ️ Quality issues | {digest.quality_issues_count} |")
    if digest.scoped:
        lines.append(
            f"| 🔒 Filtered (internal/private) | {digest.out_of_surface_count} |"
        )
    lines.append("")

    if digest.result_counts:
        counts = digest.result_counts
        lines += [
            "**Count populations:** "
            f"{counts['raw_detected']} detected; {counts['retained']} retained "
            f"({counts['gating']} gating, {counts['non_gating']} non-gating); "
            f"{counts['review_groups']} review group(s).",
            "",
            "**Public surface:** "
            f"{counts['public_additions']} additions, "
            f"{counts['public_removals']} removals, "
            f"{counts['public_modifications']} modifications. "
            f"Runtime/dependency: {counts['runtime_dependency']} finding(s).",
            "",
            "**Header/binary consistency:** "
            f"{counts['hygiene_introduced']} introduced, "
            f"{counts['hygiene_resolved']} resolved, "
            f"{counts['hygiene_persistent']} persistent, "
            f"{counts['hygiene_not_evaluated']} not evaluated.",
            "",
        ]
        detected_ops = sum(
            counts.get(key, 0)
            for key in (
                "detected_public_additions",
                "detected_public_removals",
                "detected_public_modifications",
            )
        )
        retained_ops = sum(
            counts[key]
            for key in ("public_additions", "public_removals", "public_modifications")
        )
        if detected_ops != retained_ops:
            lines += [
                "**Detected public operations before disposition:** "
                f"{counts.get('detected_public_additions', 0)} additions, "
                f"{counts.get('detected_public_removals', 0)} removals, "
                f"{counts.get('detected_public_modifications', 0)} modifications.",
                "",
            ]

    if digest.show_release_recommendation:
        lines += [
            f"**Release recommendation:** `{digest.bump_value}` version bump · "
            f"SONAME `{digest.soname_value}`",
            "",
        ]
    if digest.env_matrix_source_sha256 is not None:
        lines += [
            f"**Deployment floor digest:** `{digest.env_matrix_source_sha256}`",
            "",
        ]

    # ADR-067 D3: the digest is the summary a reviewer approves a merge from,
    # so the counts table above must not be the whole story -- what was
    # detected, what actually gated, and which rule accounts for the
    # difference belong in the same view.
    if digest.disposition_audit is not None:
        lines += ["**Disposition audit:**", ""]
        lines += render_disposition_audit_lines(digest.disposition_audit)
    if digest.pattern_modulations:
        from .pattern_modulations_markdown import (
            render_pattern_modulations_from_mapping,
        )

        lines += ["**Pattern-modulated findings:**"]
        lines += render_pattern_modulations_from_mapping(
            digest.pattern_modulations[:MAX_REVIEW_PATTERN_MODULATIONS],
            include_heading=False,
        )
        if len(digest.pattern_modulations) > MAX_REVIEW_PATTERN_MODULATIONS:
            lines.append(
                f"- … {len(digest.pattern_modulations) - MAX_REVIEW_PATTERN_MODULATIONS} more omitted; export JSON for details"
            )
        lines.append("")
    if (
        not digest.review_groups
        and digest.surface_changes is not None
        and digest.surface_changes.total
    ):
        lines += render_surface_changes_lines(digest.surface_changes)  # workstream G

    if digest.impacted and not digest.review_groups:
        lines += ["**Top impacted symbols:**", ""]
        for sym in digest.impacted[:MAX_REVIEW_IMPACTED_SYMBOLS]:
            lines.append(f"- `{sym.symbol}` — {sym.kind}")
        if len(digest.impacted) > MAX_REVIEW_IMPACTED_SYMBOLS:
            lines.append(
                f"- … and {len(digest.impacted) - MAX_REVIEW_IMPACTED_SYMBOLS} more"
            )
        lines.append("")

    lines += [
        "**Details:** export the same completed comparison with "
        "`-o markdown=abi-report.md -o json=abi-report.json`.",
        "",
    ]

    return "\n".join(lines).rstrip() + "\n"


def render_terminal_digest(digest: ReviewDigest) -> str:
    """Bounded, markup-free terminal projection of the same decided digest."""

    def compact(value: object, limit: int) -> str:
        text = str(value).replace("\r", " ").replace("\n", " ")
        return text if len(text) <= limit else text[: limit - 1] + "…"

    gate = (
        "not configured"
        if digest.gate_exit_code is None
        else (
            f"REJECTED (exit {digest.gate_exit_code})"
            if digest.gate_exit_code
            else "ACCEPTED (exit 0)"
        )
    )
    lines = [
        f"{digest.library}   {digest.old_version} -> {digest.new_version}",
        f"Compatibility: {digest.verdict_label} | Policy: {digest.policy} | Gate: {gate}",
    ]
    if digest.evidence_summary:
        lines.append(f"Evidence: {digest.evidence_summary}.")
    lines.extend(
        f"WARNING: {compact(warning, 300)}" for warning in digest.coverage_warnings
    )
    if digest.result_counts:
        c = digest.result_counts
        lines += [
            f"Findings: {c['raw_detected']} detected; {c['retained']} retained "
            f"({c['gating']} gating, {c['non_gating']} non-gating); "
            f"{c['review_groups']} review groups.",
            f"Public surface: {c['public_additions']} additions, "
            f"{c['public_removals']} removals, {c['public_modifications']} modifications.",
            f"Runtime/dependency: {c['runtime_dependency']} findings.",
            f"Header/binary consistency: {c['hygiene_introduced']} introduced, "
            f"{c['hygiene_resolved']} resolved, {c['hygiene_persistent']} persistent, "
            f"{c['hygiene_not_evaluated']} not evaluated.",
        ]
        detected_ops = (
            c.get("detected_public_additions", 0)
            + c.get("detected_public_removals", 0)
            + c.get("detected_public_modifications", 0)
        )
        retained_ops = (
            c["public_additions"] + c["public_removals"] + c["public_modifications"]
        )
        if detected_ops != retained_ops:
            lines.append(
                "Detected public operations before disposition: "
                f"{c.get('detected_public_additions', 0)} additions, "
                f"{c.get('detected_public_removals', 0)} removals, "
                f"{c.get('detected_public_modifications', 0)} modifications."
            )
    if digest.review_groups:
        lines.append("")
        for index, group in enumerate(digest.review_groups[:8], 1):
            name = str(group["display_name"])
            if len(name) > 160:
                name = name[:159] + "…"
            lines += [
                f"{index}. {name}: {compact(group['transition'], 240)}",
                f"   Consequence: {compact(group['consequence'], 240)}",
                f"   Action: {compact(group['action'], 200)}",
                f"   Findings: {', '.join(group['member_kinds'])}",
            ]
        if len(digest.review_groups) > 8:
            lines.append(f"... {len(digest.review_groups) - 8} more groups omitted.")
    audit = digest.disposition_audit
    if audit is not None:
        counts = dict(audit.counts)
        lines += [
            "",
            f"Audit: {audit.detected_total} detected; {audit.effective_total} gating; "
            f"{counts.get('suppressed', 0)} suppressed by {len(audit.rules)} rules; "
            f"{counts.get('out_of_contract', 0) + counts.get('unresolved_relevance', 0)} scope-excluded.",
        ]
        for rule, matched in audit.rules[:MAX_REVIEW_DISPOSITION_RULES]:
            lines.append(
                f"  Suppression {rule.rule_id or 'rule'}: {matched} finding(s) — "
                f"{compact(rule.reason or rule.label or 'no reason given', 180)}"
            )
        if len(audit.rules) > MAX_REVIEW_DISPOSITION_RULES:
            lines.append(
                f"  ... {len(audit.rules) - MAX_REVIEW_DISPOSITION_RULES} more suppression rules omitted."
            )
    lines.append(
        "Details: add -o markdown=abi-report.md -o json=abi-report.json to this invocation."
    )
    return "\n".join(lines) + "\n"
