# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Pure Markdown projection for reporter_markdown.py's structured sections.

ADR-061 Phase 2's last open Markdown slice: ``reporter_markdown.py``
historically built its ``## Heading`` / table / bullet-list prose directly
while walking a ``DiffResult`` -- format decisions (headings, table syntax,
blank-line placement) were interleaved with the business logic that decides
*what* belongs in a section (which changes fall in which severity bucket,
whether a note applies, what a table's cell values are).

This module is the render half of that split. Each ``compute_*`` function in
``reporter_markdown.py`` reads a ``DiffResult``/``Change`` sequence and
returns one of the small, frozen dataclasses below -- plain data: strings,
ints, bools, tuples, and (where a section's job is literally "list some
changes") the ``Change`` objects themselves, unformatted. The ``render_*``
function here consumes that structure and returns the same ``list[str]`` of
Markdown lines the pre-split function used to build in one step.

``_format_change_md``/``_format_change_md_oneline``/``_contract_decision_text``
-- the per-change/per-decision low-level string formatters -- live here too,
moved rather than left in ``reporter_markdown.py``: they take a duck-typed
``object``/``Change`` and return a formatted string with no ``DiffResult``
traversal or policy decision of their own, so they belong on the render side
of the split, and keeping them here (rather than in ``reporter_markdown.py``,
which needs to call into this module for every ``render_*`` function) avoids
a same-layer import cycle between the two modules.
``reporter_markdown.py`` re-exports all three under their original names
(``from .report.render_markdown import _format_change_md as
_format_change_md``, ...) so every existing call site --
including ``abicheck.reporter``'s own re-export and its direct test
coverage -- resolves unchanged.

Every ``render_*`` function here is behavior-preserving by construction: each
was extracted line-for-line from the pre-split function it replaces, with
only the *source* of each value changed (a struct field instead of a
re-derivation from ``DiffResult``/``Change``). See
``tests/test_golden_output.py`` for the byte-exact contract this rests on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..checker_types import Change
from .disposition_audit import DispositionAudit, render_disposition_audit_lines
from .kind_rollup import KindRollup, render_kind_rollups
from .surface_changes import SurfaceChangeSection, render_surface_changes_lines


def _contract_decision_text(
    relevance: Any, reason_code: str | None, assurance: Any
) -> str:
    """Core ``<relevance> (<reason_code>), assurance: <level>`` text, shared
    by every already-stamped-``Change`` rendering site (CodeRabbit review:
    the same tag-building pattern was duplicated at several call sites).
    Deliberately excludes any ``Contract:``/``[contract: ...]`` wrapper --
    callers render in visibly different shapes (a leading ``"Contract: "``, a
    bracketed ``"[contract: ...]"``), so each keeps its own exact
    prefix/suffix and casing."""
    tag = str(relevance.value)
    if reason_code:
        tag += f" ({reason_code})"
    if assurance is not None:
        tag += f", assurance: {assurance.value}"
    return tag


def _format_change_md_oneline(c: object) -> str:
    """Format a single change as a bare ``- **kind**: description`` line, plus
    a "See also" correlation note when ``correlated_change_kind`` is set.

    Used by the sections (Deployment Risk, Quality Issues, Not Evaluated)
    that deliberately render a change as a single terse line rather than
    routing through the fuller :func:`_format_change_md` (impact/affected-
    symbols/contract detail) -- but the cross-detector correlation must
    still reach every section a correlated finding (currently only
    ``LAYOUT_UNVERIFIABLE``) can land in, or a policy/contract
    configuration that routes it into one of these terse sections silently
    drops the "See also" note the fuller formatter carries (Codex review,
    fresh evidence).
    """
    kind = getattr(c, "kind", None)
    kind_val = kind.value if kind else ""
    desc = getattr(c, "description", "")
    line = f"- **{kind_val}**: {desc}"
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    return line


def _format_change_md(c: object, impact: str | None = None) -> str:
    """Markdown list item with impact/metadata; *impact* is the caller's
    already-resolved display string, never a registry lookup made here
    (Codex review: ``report/AGENTS.md`` L78-L89 treats ``impact_for()``
    itself as a report decision, not a render-side formatting choice).
    Resolved by ``reporter_markdown.compute_root_cause_section``, matching
    every other projection's ``row["impact"]`` shape.
    """
    kind = getattr(c, "kind", None)
    kind_val = kind.value if kind else ""
    desc = getattr(c, "description", "")
    old_val = getattr(c, "old_value", None)
    new_val = getattr(c, "new_value", None)
    loc = getattr(c, "source_location", None)
    affected = getattr(c, "affected_symbols", None)
    caused_count = getattr(c, "caused_count", 0)

    # Base line
    old_new = ""
    if old_val is not None and new_val is not None:
        old_new = f" (`{old_val}` → `{new_val}`)"
    elif old_val is not None:
        old_new = f" (`{old_val}`)"
    elif new_val is not None:
        old_new = f" (`{new_val}`)"
    line = f"- **{kind_val}**: {desc}{old_new}"

    # Which library, when the run compared more than one. Absent for every
    # single-library comparison, where the report's own header already says
    # it -- so those reports render exactly as before. Without it a
    # multi-library `compat check` report cannot say which DSO a removal came
    # from, and the same symbol removed from two of them renders as two
    # identical lines (Codex review).
    finding_library = getattr(c, "library", None)
    if finding_library:
        line += f" — in `{finding_library}`"

    # Source location
    if loc:
        line += f" — `{loc}`"

    # Impact
    if impact:
        line += f"\n  > {impact}"

    # Collapsed derived changes
    if caused_count > 0:
        line += f"\n  > {caused_count} derived change(s) collapsed"

    # Affected functions
    if affected:
        names = ", ".join(f"`{s}`" for s in affected[:5])
        suffix = f" (+{len(affected) - 5} more)" if len(affected) > 5 else ""
        line += f"\n  > Affected symbols: {names}{suffix}"

    # ADR-049 Phase 3 (Codex review, fresh evidence): --contract's
    # own help text promises every finding is stamped with a contract
    # decision, but only the JSON report (reporter.py's
    # _add_contract_evaluation_fields) ever rendered it -- an ordinary
    # `compare --contract` run (default markdown format) was
    # byte-for-byte identical to one without the flag. A no-op when *c* was
    # never stamped (contract_evaluation not requested), mirroring that
    # helper's own documented default.
    contract_relevance = getattr(c, "contract_relevance", None)
    if contract_relevance is not None:
        reason_code = getattr(c, "contract_reason_code", None)
        contract_assurance = getattr(c, "contract_assurance", None)
        line += f"\n  > Contract: {_contract_decision_text(contract_relevance, reason_code, contract_assurance)}"

    # Cross-detector correlation (e.g. LAYOUT_UNVERIFIABLE annotated by
    # post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged as
    # sharing its evidence gap with a co-reported TYPE_VTABLE_CHANGED). Only
    # JSON (reporter.py) and SARIF (sarif.py) rendered this field before —
    # the default `compare -o markdown=...` report showed the two findings
    # with no visible link between them (Codex review).
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"

    return line


def _format_leaf_type_change(c: object) -> list[str]:
    """Format a single leaf-mode (``--report-mode leaf``) type change entry."""
    symbol = getattr(c, "symbol", None)
    desc = getattr(c, "description", "")
    lines = [f"### {symbol} — {desc}"]
    affected = getattr(c, "affected_symbols", None)
    if affected:
        lines.append(f"\n**Affected interfaces ({len(affected)}):**")
        for sym in affected[:10]:
            lines.append(f"- `{sym}`")
        if len(affected) > 10:
            lines.append(f"- ... ({len(affected) - 10} more)")
    caused_count = getattr(c, "caused_count", 0)
    if caused_count > 0:
        lines.append(f"\n> {caused_count} derived change(s) collapsed")
    # ADR-049 Phase 3 (Codex review, fresh evidence): --report-mode leaf
    # routes root TYPE_* changes through this function, never through
    # _format_change_md -- unlike the full/root-cause views, a leaf-mode
    # type finding's own contract decision (already stamped when
    # --contract was requested) was silently dropped. Mirrors
    # _format_change_md's own "no-op unless already stamped" idiom.
    contract_relevance = getattr(c, "contract_relevance", None)
    if contract_relevance is not None:
        text = _contract_decision_text(
            contract_relevance,
            getattr(c, "contract_reason_code", None),
            getattr(c, "contract_assurance", None),
        )
        lines.append(f"\n> Contract: {text}")
    lines.append("")
    return lines


@dataclass(frozen=True, slots=True)
class LeafTypeSection:
    heading: str
    changes: tuple[Change, ...]


@dataclass(frozen=True, slots=True)
class LeafTypeSectionsData:
    sections: tuple[LeafTypeSection, ...]


def render_leaf_type_sections(data: LeafTypeSectionsData) -> list[str]:
    lines: list[str] = []
    for section in data.sections:
        lines += [section.heading, ""]
        for c in section.changes:
            lines += _format_leaf_type_change(c)
    return lines


# ---------------------------------------------------------------------------
# Root-cause grouped sections (--report-mode root-cause)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RootCauseGroupData:
    root_display: str
    count: int
    finding_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RootCauseSectionData:
    groups: tuple[RootCauseGroupData, ...]


def render_root_cause_section(data: RootCauseSectionData | None) -> list[str]:
    if data is None:
        return []
    lines: list[str] = [f"## Root Causes ({len(data.groups)})", ""]
    for group in data.groups:
        plural = "" if group.count == 1 else "s"
        lines.append(f"### `{group.root_display}` ({group.count} finding{plural})")
        lines.append("")
        lines.extend(group.finding_lines)
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------


def render_footer() -> list[str]:
    return [
        "---",
        "## Legend",
        "",
        "| Verdict | Meaning |",
        "|---------|---------|",
        "| ✅ NO_CHANGE | Identical ABI |",
        "| ✅ COMPATIBLE | No incompatible ABI/API changes — may include additions and quality findings (backward compatible) |",
        "| ⚠️ COMPATIBLE_WITH_RISK | Binary-compatible; verify target environment |",
        "| ⚠️ API_BREAK | Source-level API change — recompilation required |",
        "| ❌ BREAKING | Binary ABI break — recompilation required |",
        "",
        "_Generated by [abicheck](https://github.com/abicheck/abicheck)_",
    ]


# ---------------------------------------------------------------------------
# Redundancy / out-of-surface / suppression notes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RedundancyNote:
    redundant_count: int


def render_redundancy_note(note: RedundancyNote | None) -> list[str]:
    if note is None:
        return []
    return [
        "",
        f"> ℹ️ {note.redundant_count} redundant change(s) hidden "
        "(derived from root type changes). Set `scope.show_redundant: true` in\n"
        "> `.abicheck.yml` to show all.",
    ]


@dataclass(frozen=True, slots=True)
class OutOfSurfaceNote:
    count: int


def render_out_of_surface_note(note: OutOfSurfaceNote | None) -> list[str]:
    if note is None:
        return []
    return [
        "",
        f"> ℹ️ {note.count} finding(s) filtered as non-public ABI surface "
        "(`--scope-public-headers`). Pass `--view filtered` to list them.",
    ]


@dataclass(frozen=True, slots=True)
class SuppressedEntry:
    symbol: str
    description: str
    contract_text: str | None


@dataclass(frozen=True, slots=True)
class SuppressionNote:
    suppressed_count: int
    entries: tuple[SuppressedEntry, ...]


def render_suppression_note(note: SuppressionNote | None) -> list[str]:
    if note is None:
        return []
    lines = [""]
    if note.suppressed_count == 0:
        lines.append(
            "> ℹ️ Suppression file active — 0 changes matched (nothing suppressed)"
        )
        return lines
    lines.append(
        f"> ℹ️ {note.suppressed_count} change(s) suppressed via suppression file"
    )
    for entry in note.entries:
        line = f">   - `{entry.symbol}` — {entry.description}"
        if entry.contract_text is not None:
            line += f" [contract: {entry.contract_text}]"
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Severity configuration summary table
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeverityRow:
    label: str
    emoji: str
    level_upper: str
    count: int
    impact: str


@dataclass(frozen=True, slots=True)
class SeveritySummary:
    rows: tuple[SeverityRow, ...]


def render_severity_summary(summary: SeveritySummary) -> list[str]:
    lines = [
        "## Severity Configuration",
        "",
        "| Category | Severity | Count | Exit Impact |",
        "|----------|----------|-------|-------------|",
    ]
    for row in summary.rows:
        lines.append(
            f"| {row.label} | {row.emoji} `{row.level_upper}` | {row.count} | {row.impact} |"
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Not-evaluated (contract) section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NotEvaluatedEntry:
    change: Change
    label: str
    suffix: str


@dataclass(frozen=True, slots=True)
class NotEvaluatedSection:
    entries: tuple[NotEvaluatedEntry, ...]


def render_not_evaluated_section(section: NotEvaluatedSection | None) -> list[str]:
    if section is None:
        return []
    lines: list[str] = [
        "## 🔍 Not Evaluated (Contract)",
        "",
        "> These findings were detected but **not scored** by compatibility",
        "> policy: each is either proven outside the declared contract or",
        "> unresolved for want of evidence (ADR-049). They contribute nothing",
        "> to the verdict or the gate. Incomplete evidence is reported",
        "> separately on the contract-coverage axis, which has its own exit",
        "> code — uncertainty is never silently treated as compatible.",
        "",
    ]
    for entry in section.entries:
        lines.append(_format_change_md_oneline(entry.change))
        lines.append(f"  > Contract: {entry.label}{entry.suffix}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Impact summary table
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImpactRootEntry:
    symbol: str
    kind: str
    iface_count: int
    caused: int


@dataclass(frozen=True, slots=True)
class ImpactTable:
    root_entries: tuple[ImpactRootEntry, ...]
    direct_removals: int


def render_impact_table(table: ImpactTable | None) -> list[str]:
    if table is None:
        return []
    lines = [
        "## Impact Summary",
        "",
        "| Root Change | Kind | Affected Interfaces | Derived |",
        "|-------------|------|---------------------|---------|",
    ]
    for entry in table.root_entries:
        iface_str = f"{entry.iface_count} functions" if entry.iface_count > 0 else "—"
        caused_str = f"+{entry.caused} collapsed" if entry.caused > 0 else "—"
        lines.append(f"| {entry.symbol} | {entry.kind} | {iface_str} | {caused_str} |")
    if table.direct_removals > 0:
        lines.append(f"| — | removals ({table.direct_removals}) | direct | — |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Library files section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LibraryFilesSection:
    old_path: str
    new_path: str
    old_sha: str
    new_sha: str
    old_size: str
    new_size: str


def render_library_files_section(
    section: LibraryFilesSection | None,
) -> list[str]:
    if section is None:
        return []
    return [
        "## Library Files",
        "",
        "| | Old | New |",
        "|---|---|---|",
        f"| **Path** | `{section.old_path}` | `{section.new_path}` |",
        f"| **SHA-256** | `{section.old_sha}…` | `{section.new_sha}…` |",
        f"| **Size** | {section.old_size} | {section.new_size} |",
        "",
    ]


# ---------------------------------------------------------------------------
# Severity-grouped change sections (full mode)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeGroup:
    heading: str
    changes: tuple[Change, ...]
    oneline: bool
    note_lines: tuple[str, ...] = ()
    #: Per-kind summaries rendered *instead of* the individual findings of
    #: those kinds. A kind appearing here is not in ``changes``.
    rollups: tuple[KindRollup, ...] = ()


@dataclass(frozen=True, slots=True)
class SeveritySectionsData:
    groups: tuple[ChangeGroup, ...]


def render_severity_sections(data: SeveritySectionsData) -> list[str]:
    lines: list[str] = []
    for group in data.groups:
        lines += [group.heading, ""]
        if group.note_lines:
            lines += list(group.note_lines)
            lines.append("")
        fmt = _format_change_md_oneline if group.oneline else _format_change_md
        for c in group.changes:
            lines.append(fmt(c))
        lines += render_kind_rollups(group.rollups)
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Environment & toolchain drift section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnvironmentDriftEntry:
    kind: str
    description: str


@dataclass(frozen=True, slots=True)
class EnvironmentDriftSection:
    entries: tuple[EnvironmentDriftEntry, ...]


def render_environment_drift_section(
    section: EnvironmentDriftSection | None,
) -> list[str]:
    if section is None:
        return []
    lines = [
        "## 🛠️ Environment & Toolchain Drift",
        "",
        "> The findings below are artifacts of the **build environment** — a",
        "> different compiler, binutils/linker default, or glibc/sysroot —",
        "> rather than a change to the library's declared interface. They also",
        "> appear in their severity sections above; this view groups them by",
        "> root cause. If the source did not change, review the build",
        "> environment first.",
        "",
    ]
    for entry in section.entries:
        lines.append(f"- **{entry.kind}**: {entry.description}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Internal/RTTI churn note
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RttiNote:
    rtti: int
    internal: int
    total: int
    public: int


def render_rtti_note(note: RttiNote | None) -> list[str]:
    """Render the symbol-shape breakdown of the breaking set: counts, then stop.

    It used to call these findings "not public-API breaks" and the remainder
    the "genuine public-surface" count -- a claim name shape cannot support,
    which told users to disregard real breaks (a vtable layout change on a
    public, user-derivable class is one). Contract membership is ADR-049's
    question. Bug class ``classification.name_shape_as_contract_membership``.
    """
    if note is None:
        return []
    return [
        f"> \u2139\ufe0f **Breaking findings by symbol shape:** {note.rtti} RTTI/vtable "
        f"artifact(s), {note.internal} in an internal-namespace-by-convention, "
        f"{note.public} other, of {note.total} total.",
        ">",
        "> This is how the symbols are *spelled*, not what is in the public "
        "contract \u2014 a vtable or typeinfo change for a public, user-derivable "
        "class is a genuine public ABI break, and the internal-namespace count "
        "assumes a convention this library may not follow. Churn here *can* mean "
        "a missing `-fvisibility=hidden`, but this breakdown alone does not "
        "establish that; run with `--contract` to classify findings against a "
        "resolved contract instead.",
        "",
    ]


# ---------------------------------------------------------------------------
# Analysis confidence section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfidenceSection:
    confidence_upper: str
    evidence_tier: str
    evidence_tiers_str: str
    coverage_warnings: tuple[str, ...]
    # E-S2 (docs/contribute/plans/cli-cleanup-phase-two.md, Block 5) — the
    # per-dimension comparability breakdown, sorted by dimension name;
    # ``()`` when the run carried no ``--diagnostic-comparison`` mismatch to
    # report (see ``checker_types.DiffResult.comparability_assurance``).
    comparability_dimensions: tuple[tuple[str, str], ...] = ()


def render_confidence_section(section: ConfidenceSection | None) -> list[str]:
    if section is None:
        return []
    lines = [
        "## Analysis Confidence",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Confidence | {section.confidence_upper} |",
        f"| Evidence tier | `{section.evidence_tier}` |",
        f"| Evidence tiers | {section.evidence_tiers_str} |",
    ]
    for warning in section.coverage_warnings:
        lines.append(f"| Coverage gap | {warning} |")
    for dimension, status in section.comparability_dimensions:
        lines.append(f"| Comparability: {dimension} | {status} |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Policy section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicySection:
    policy: str
    overrides_text: str | None
    reclassify_text: str | None


def render_policy_section(section: PolicySection) -> list[str]:
    lines = [f"> **Policy**: `{section.policy}`"]
    if section.overrides_text is not None:
        lines.append(f"> **Policy overrides**: {section.overrides_text}")
    if section.reclassify_text is not None:
        lines.append(f"> **Policy reclassify**: {section.reclassify_text}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Release recommendation section
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecommendationSection:
    bump_emoji: str
    bump_upper: str
    soname_value: str
    state_value: str
    rationale: str


def render_recommendation_section(section: RecommendationSection) -> list[str]:
    return [
        "## Release Recommendation",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Version bump | {section.bump_emoji} **{section.bump_upper}** |",
        f"| SONAME action | `{section.soname_value}` |",
        f"| Recommendation state | `{section.state_value}` |",
        "",
        f"{section.rationale}",
        "",
    ]


# ---------------------------------------------------------------------------
# Headline summary table (full mode)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeadlineTable:
    library: str
    old_version: str
    new_version: str
    verdict_emoji: str
    verdict_label: str
    breaking: int
    source_breaks: int
    risk: int
    compatible: int
    not_evaluated: int


def render_headline_table(table: HeadlineTable) -> list[str]:
    lines: list[str] = [
        f"# ABI Report: {table.library}",
        "",
        "| | |",
        "|---|---|",
        f"| **Old version** | `{table.old_version}` |",
        f"| **New version** | `{table.new_version}` |",
        f"| **Verdict** | {table.verdict_emoji} `{table.verdict_label}` |",
        f"| Breaking changes | {table.breaking} |",
        f"| Source-level breaks | {table.source_breaks} |",
        f"| Deployment risk changes | {table.risk} |",
        f"| Compatible changes | {table.compatible} |",
    ]
    if table.not_evaluated:
        lines.append(f"| Not evaluated (contract) | {table.not_evaluated} |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Review digest (-o review=...)
# ---------------------------------------------------------------------------


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


def render_review_digest(digest: ReviewDigest) -> str:
    lines: list[str] = [
        f"## ABI review — `{digest.library}` {digest.old_version} → {digest.new_version}",
        "",
        f"**Verdict:** {digest.verdict_emoji} `{digest.verdict_label}` — {digest.effect}",
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
    if digest.surface_changes is not None and digest.surface_changes.total:
        lines += render_surface_changes_lines(digest.surface_changes)  # workstream G

    if digest.impacted:
        lines += ["**Top impacted symbols:**", ""]
        for sym in digest.impacted[:10]:
            lines.append(f"- `{sym.symbol}` — {sym.kind}")
        if len(digest.impacted) > 10:
            lines.append(f"- … and {len(digest.impacted) - 10} more")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
