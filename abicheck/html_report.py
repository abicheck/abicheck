# Copyright 2026 Nikolay Petrov
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

"""Sprint 9: ABICC-compatible HTML report generator.

Generates a self-contained HTML report that mirrors the structure of
abi-compliance-checker (ABICC) reports:

  - Verdict banner (BREAKING / COMPATIBLE / NO_CHANGE)
  - Binary Compatibility % metric (based on old exported symbol count)
  - Summary table: changes by category (functions, variables, types, enums, ELF)
  - Sectioned changes: Removed | Changed | Added (with anchored navigation)
  - Suppressed changes section (if any)
  - Demangled symbol names as display text, mangled as tooltip

No external CSS/JS dependencies — fully self-contained single HTML file.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .checker_policy import HasKind, impact_for
from .demangle import prewarm_demangle_batch

# Page chrome (DOCTYPE/head/stylesheet/body frame, verdict palette, footer) now
# lives in one shared seam (``html_template``). ``_VERDICT_STYLE`` /
# ``render_document`` / ``render_footer`` are used below; ``_CSS`` is re-exported
# via redundant alias (it was previously defined in this module) so any code that
# imported it from here keeps working.
from .html_template import (
    _CSS as _CSS,
    _VERDICT_STYLE,
    render_document,
    render_footer,
)
from .policy.gate_decision import gate_decision_for_result

# ADR-061 Phase 2 item 1: the pure HTML projection half of this module.
# Every ``compute_*`` below returns one of these frozen structs; the
# matching ``render_*`` turns it into markup and makes no decision of its
# own. The five formatters re-exported under their original private names
# physically live there now (see ``render_html``'s own docstring for why
# moving them, rather than leaving them here, is what avoids a same-layer
# import cycle) -- every existing caller and its direct test coverage
# resolves through these aliases unchanged.
from .report.render_html import (
    ChangeRowFacts,
    ChangeRowFactsById,
    ConfidenceData,
    FileMetadataTable,
    GateCardData,
    ImpactData,
    ImpactEntry,
    NavBarData,
    NotEvaluatedRow,
    NotEvaluatedSectionData,
    ScopedVerdictData,
    SummaryCategoryRow,
    SummaryTableData,
    abbr_symbol_text,
    render_changes_table,
    render_compat_changes_table,
    render_confidence,
    render_file_metadata,
    render_gate_card,
    render_impact,
    render_nav_bar,
    render_not_evaluated_section,
    render_scoped_verdict,
    render_summary_table,
    symbol_cell,
    verdict_icon,
)
from .report_classifications import (
    ADDED_KINDS,
    BREAKING_KINDS,
    CATEGORY_PREFIXES,
    REMOVED_KINDS,
    category,
    is_symbol_problem,
    is_type_problem,
    kind_str,
    severity,
)
from .report_summary import compatibility_metrics

if TYPE_CHECKING:
    from .checker import DiffResult
    from .severity import SeverityConfig

# The five formatters this module used to define itself, kept under their
# original private names so every existing caller resolves unchanged --
# `appcompat_html.py` imports `_abbr_symbol_text`/`_changes_table` from
# here, and `tests/test_html_report_demangle.py` exercises them by these
# spellings. Plain assignments rather than renaming imports, so both ruff
# and mypy read them as deliberate re-exports rather than unused imports.
_abbr_symbol_text = abbr_symbol_text
_symbol_cell = symbol_cell
_verdict_icon = verdict_icon


def compute_change_rows(changes: Iterable[object]) -> ChangeRowFactsById:
    """Resolve the per-change *decisions* a table cell needs -- kind string,
    category, impact text, ABICC severity band -- one entry per change.

    Every one is a registry lookup rather than a formatting choice, so it
    belongs on this side of the split; hoisting them here is what lets
    `report/render_html.py` import no report- or policy-classification
    module at all (Codex review on the split's own PR). Keyed by
    `id(change)` because `Change` is not hashable, the same shape
    `report.finding.findings_by_change_id` uses, and equally never persisted
    past the render that built it.
    """
    facts: ChangeRowFactsById = {}
    for ch in changes:
        ks = kind_str(ch)
        kind = getattr(ch, "kind", None)
        facts[id(ch)] = ChangeRowFacts(
            kind=ks,
            category=category(ks),
            impact=(impact_for(kind) or "") if kind else "",
            severity=severity(ks),
        )
    return facts


def _changes_table(changes: list[object], demangle: bool = True) -> str:
    """Native changes table. Kept at its original signature -- `appcompat_html.py`
    imports it, and it has its own direct test coverage -- so the per-change
    fact resolution happens here rather than being pushed onto every caller."""
    return render_changes_table(changes, compute_change_rows(changes), demangle)


def _compat_changes_table(items: list[object], show_severity: bool = False) -> str:
    """ABICC-style changes table, same arrangement as `_changes_table` above."""
    return render_compat_changes_table(
        items, compute_change_rows(items), show_severity
    )


def _change_bucket(
    change: object,
    effective_verdict: Callable[[object], object] | None = None,
) -> str:
    """Classify a change into 'removed', 'added', or 'changed'.

    A kind in ``ADDED_KINDS`` is excluded from the 'added' bucket when it is
    also a canonical breaking kind (e.g. ``type_field_added`` — appending a
    field to a non-final/polymorphic type can break binary layout, unlike
    its sibling ``type_field_added_compatible``). Without this guard, a
    structurally-additive but ABI-breaking finding would render under the
    green "Added" section, reading as safe when it is not.

    *effective_verdict*, when given (typically
    ``result._effective_verdict_for_change``), is also consulted for
    otherwise-additive kinds: a policy file can escalate an inherently
    additive kind (e.g. ``func_added``) to ``Verdict.BREAKING``,
    ``Verdict.API_BREAK``, or ``Verdict.COMPATIBLE_WITH_RISK`` — none of
    which the canonical ``BREAKING_KINDS`` membership check above can ever
    see, since it only looks at the kind's own default classification.
    Any effective verdict other than ``Verdict.COMPATIBLE`` means the
    finding needs review, so it is excluded from "added" here too — not
    just the ``BREAKING`` case — to match the compatibility metrics and
    verdict banner on the same page.
    """
    ks = kind_str(change)
    if ks in REMOVED_KINDS:
        return "removed"
    if ks in ADDED_KINDS and ks not in BREAKING_KINDS:
        if effective_verdict is not None:
            from .checker import Verdict

            if effective_verdict(change) != Verdict.COMPATIBLE:
                return "changed"
        return "added"
    return "changed"


# ---------------------------------------------------------------------------
# HTML generation helpers
# ---------------------------------------------------------------------------


def compute_file_metadata(result: object) -> FileMetadataTable | None:
    """Collect the library-file facts the "Library Files" table shows.

    ``None`` means neither side carried metadata at all, which renders
    nothing -- as distinct from a side that carried metadata with missing
    fields, which keeps the per-field ``—`` placeholder.
    """
    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)
    if not old_meta and not new_meta:
        return None
    return FileMetadataTable(
        old_path=getattr(old_meta, "path", "—") if old_meta else "—",
        new_path=getattr(new_meta, "path", "—") if new_meta else "—",
        old_sha=getattr(old_meta, "sha256", "—") if old_meta else "—",
        new_sha=getattr(new_meta, "sha256", "—") if new_meta else "—",
        old_size=str(getattr(old_meta, "size_bytes", 0)) if old_meta else "—",
        new_size=str(getattr(new_meta, "size_bytes", 0)) if new_meta else "—",
    )


def _file_metadata_html(result: object) -> str:
    """Render library file metadata (path, SHA-256, size) as an HTML table."""
    return render_file_metadata(compute_file_metadata(result))


def compute_summary_table(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> SummaryTableData:
    """Bucket the three change lists by category (mirrors ABICC's overview).

    Which category a change belongs to, and which rows are worth showing at
    all (an all-zero category is dropped), are report decisions -- so they are
    resolved here; the row order is the catalog's own ``CATEGORY_PREFIXES``
    order with ``Other`` last.
    """
    cats: dict[str, dict[str, int]] = {}
    for label, _ in CATEGORY_PREFIXES:
        cats[label] = {"removed": 0, "changed": 0, "added": 0}
    cats["Other"] = {"removed": 0, "changed": 0, "added": 0}

    for ch in removed:
        cats[category(kind_str(ch))]["removed"] += 1
    for ch in changed:
        cats[category(kind_str(ch))]["changed"] += 1
    for ch in added:
        cats[category(kind_str(ch))]["added"] += 1

    rows = []
    for label in [lbl for lbl, _ in CATEGORY_PREFIXES] + ["Other"]:
        c = cats[label]
        if c["removed"] == 0 and c["changed"] == 0 and c["added"] == 0:
            continue
        rows.append(
            SummaryCategoryRow(
                label=label,
                removed=c["removed"],
                changed=c["changed"],
                added=c["added"],
            )
        )
    return SummaryTableData(
        rows=tuple(rows),
        total_removed=len(removed),
        total_changed=len(changed),
        total_added=len(added),
        suppressed_count=suppressed_count,
    )


def _summary_table(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> str:
    """Build category-level summary table (mirrors ABICC's overview section)."""
    return render_summary_table(
        compute_summary_table(removed, changed, added, suppressed_count)
    )


def compute_nav_bar(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> NavBarData:
    return NavBarData(
        removed=len(removed),
        changed=len(changed),
        added=len(added),
        suppressed_count=suppressed_count,
    )


def _nav_bar(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> str:
    return render_nav_bar(compute_nav_bar(removed, changed, added, suppressed_count))


# ---------------------------------------------------------------------------
# ABICC-compatible HTML (compat_html mode)
# ---------------------------------------------------------------------------

_COMPAT_CSS = """\
body { font-family: Arial, sans-serif; margin: 0; padding: 20px; color: #333; }
h1 { font-size: 1.6em; }
h2 { font-size: 1.2em; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 24px; }
table.summary { border-collapse: collapse; margin: 8px 0; }
table.summary td, table.summary th { padding: 4px 12px; border: 1px solid #ddd; }
table.summary th { background: #f5f5f5; text-align: left; }
td.compatible { color: #1b5e20; font-weight: bold; }
td.incompatible { color: #b71c1c; font-weight: bold; }
td.warning { color: #e65100; font-weight: bold; }
table.problem { border-collapse: collapse; width: 100%; margin: 8px 0; }
table.problem td, table.problem th { padding: 4px 8px; border: 1px solid #ddd; vertical-align: top; }
table.problem th { background: #f5f5f5; text-align: left; }
.sym { font-family: monospace; font-size: 0.9em; }
"""


def _generate_compat_html(
    result: object,
    changes: list[object],
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed: list[object],
    suppressed_count: int,
    bc_pct: float,
    affected_pct: float,
    breaking_count: int,
    verdict: str,
    lib_display: str,
    old_display: str,
    new_display: str,
    old_symbol_count: int | None,
    title: str | None,
    report_kind: str = "binary",
) -> str:
    """Generate ABICC-compatible HTML with matching element IDs and structure.

    Produces HTML with the same DOM IDs and section structure that ABICC
    report parsers expect: #Title, #Summary, #Added, #Removed,
    #TypeProblems_High, etc.

    Also embeds the META_DATA comment that ABICC includes for machine parsing.

    Args:
        report_kind: "binary" or "source" — controls title, META_DATA kind, and
            section ID prefixes to match ABICC's per-kind report structure.
    """
    h = html.escape

    # Classify type vs symbol vs other (ELF-layer: soname_/symbol_/needed_/
    # rpath_, …) problems by severity.
    type_problems: dict[str, list[object]] = {"High": [], "Medium": [], "Low": []}
    symbol_problems: dict[str, list[object]] = {"High": [], "Medium": [], "Low": []}
    other_problems: dict[str, list[object]] = {"High": [], "Medium": [], "Low": []}
    for ch in changed:
        ks = kind_str(ch)
        sev = severity(ks)
        if is_type_problem(ks):
            type_problems[sev].append(ch)
        elif is_symbol_problem(ks):
            symbol_problems[sev].append(ch)
        else:
            other_problems[sev].append(ch)

    # Counts for META_DATA
    tp_high = len(type_problems["High"])
    tp_med = len(type_problems["Medium"])
    tp_low = len(type_problems["Low"])
    sp_high = len(symbol_problems["High"])
    sp_med = len(symbol_problems["Medium"])
    sp_low = len(symbol_problems["Low"])

    compat_verdict = (
        "incompatible" if verdict in ("BREAKING", "API_BREAK") else "compatible"
    )
    bc_css = (
        "incompatible" if bc_pct < 90 else ("warning" if bc_pct < 100 else "compatible")
    )
    affected_pct_label = f"{affected_pct:.1f}" if old_symbol_count else "0"

    kind_label = report_kind.capitalize()  # "Binary" or "Source"

    # META_DATA comment (semicolon-delimited, matches ABICC format)
    meta_data = (
        f"verdict:{compat_verdict};kind:{report_kind};"
        f"affected:{affected_pct_label};"
        f"added:{len(added)};removed:{len(removed)};"
        f"type_problems_high:{tp_high};"
        f"type_problems_medium:{tp_med};"
        f"type_problems_low:{tp_low};"
        f"interface_problems_high:{sp_high};"
        f"interface_problems_medium:{sp_med};"
        f"interface_problems_low:{sp_low};"
        f"changed_constants:0;"
        f"tool_version:abicheck"
    )

    # Build title matching ABICC convention
    abicc_title = (
        h(title)
        if title
        else f"{kind_label} compatibility report for the <b>{lib_display}</b> "
        f"library between <b>{old_display}</b> and <b>{new_display}</b> versions"
    )

    # Build sections
    sections_html = []

    # Problem Summary
    sections_html.append(f"""
<div id='Summary'>
<h2>Test Info</h2>
<table class='summary'>
<tr><th>Library Name</th><td>{lib_display}</td></tr>
<tr><th>Version #1</th><td>{old_display}</td></tr>
<tr><th>Version #2</th><td>{new_display}</td></tr>
</table>
{_file_metadata_html(result)}

<h2>Test Results</h2>
<table class='summary'>
<tr><th>Total Symbols</th><td>{old_symbol_count or "N/A"}</td></tr>
<tr><th>{kind_label} Compatibility</th><td class='{bc_css}'>{bc_pct:.1f}%</td></tr>
<tr><th>Verdict</th><td class='{bc_css}'>{compat_verdict}</td></tr>
</table>

<h2>Problem Summary</h2>
<table class='summary'>
<tr><th></th><th>High</th><th>Medium</th><th>Low</th></tr>
<tr><th>Type Problems</th><td>{tp_high}</td><td>{tp_med}</td><td>{tp_low}</td></tr>
<tr><th>Interface Problems</th><td>{sp_high}</td><td>{sp_med}</td><td>{sp_low}</td></tr>
<tr><th>Added Symbols</th><td colspan='3'>{len(added)}</td></tr>
<tr><th>Removed Symbols</th><td colspan='3'>{len(removed)}</td></tr>
</table>
</div>""")

    # Added symbols section
    if added:
        sections_html.append(f"""
<div id='Added'>
<h2>Added Symbols ({len(added)})</h2>
{_compat_changes_table(added)}
</div>""")

    # Removed symbols section
    if removed:
        sections_html.append(f"""
<div id='Removed'>
<h2>Removed Symbols ({len(removed)})</h2>
{_compat_changes_table(removed)}
</div>""")

    # Type problems by severity
    for sev in ("High", "Medium", "Low"):
        items = type_problems[sev]
        if items:
            sections_html.append(f"""
<div id='TypeProblems_{sev}'>
<h2>Problems with Data Types — {sev} Severity ({len(items)})</h2>
{_compat_changes_table(items, show_severity=True)}
</div>""")

    # Interface (symbol) problems by severity
    for sev in ("High", "Medium", "Low"):
        items = symbol_problems[sev]
        if items:
            sections_html.append(f"""
<div id='InterfaceProblems_{sev}'>
<h2>Problems with Symbols — {sev} Severity ({len(items)})</h2>
{_compat_changes_table(items, show_severity=True)}
</div>""")

    # Other problems (ELF-layer: soname, symbol versioning, calling convention)
    other_all = (
        other_problems["High"] + other_problems["Medium"] + other_problems["Low"]
    )
    if other_all:
        sections_html.append(f"""
<div id='OtherProblems'>
<h2>Other Problems ({len(other_all)})</h2>
{_compat_changes_table(other_all, show_severity=True)}
</div>""")

    body_html = "\n".join(sections_html)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{kind_label} compatibility report for {lib_display} between {old_display} and {new_display}</title>
<style>{_COMPAT_CSS}</style>
</head>
<body>
<!-- {meta_data} -->
<div id='Title'>
<h1>{abicc_title}</h1>
</div>
{body_html}
<br/>
<hr/>
<p style="font-size:0.85em; color:#999;">
Generated by <b>abicheck</b> (ABICC-compatible mode)
</p>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_confidence(result: object) -> ConfidenceData | None:
    """Collect the confidence/evidence/policy disclosure facts.

    ``None`` when the result carries no confidence at all, which renders no
    section. The reclassify rules are filtered through
    ``active_reclassify_rules`` here rather than on the render side: whether
    a rule is still in effect is a policy question, and disclosing an expired
    one as though it were would misstate the run (Codex review, mirroring the
    JSON ``policy_reclassify`` disclosure in ``reporter._add_policy_overrides``
    -- the active rule set, not a per-finding "which rule fired" attribution).
    """
    conf = getattr(result, "confidence", None)
    if conf is None:
        return None
    conf_val = conf.value if hasattr(conf, "value") else str(conf)
    policy_file = getattr(result, "policy_file", None)

    overrides: tuple[tuple[str, str], ...] = ()
    if policy_file and getattr(policy_file, "overrides", None):
        overrides = tuple((k.value, v.value) for k, v in policy_file.overrides.items())
    reclassify: tuple[str, ...] = ()
    if policy_file and getattr(policy_file, "reclassify", None):
        from .reclassify import active_reclassify_rules

        reclassify = tuple(
            rule.describe() for rule in active_reclassify_rules(policy_file.reclassify)
        )
    return ConfidenceData(
        confidence=conf_val,
        evidence_tiers=tuple(getattr(result, "evidence_tiers", []) or []),
        policy=getattr(result, "policy", "strict_abi") or "strict_abi",
        policy_overrides=overrides,
        policy_reclassify=reclassify,
        coverage_warnings=tuple(getattr(result, "coverage_warnings", []) or []),
    )


def _confidence_html(result: object) -> str:
    """Render confidence, evidence tiers, and coverage warnings as HTML."""
    return render_confidence(compute_confidence(result))


def compute_impact(
    result: DiffResult,
    displayed_changes: list[object] | None = None,
) -> ImpactData | None:
    """Collect the root type changes worth an impact row.

    When *displayed_changes* is given, only those changes are considered.
    Interface counts use unique ``affected_symbols``; ``caused_count`` is
    carried separately to avoid double-counting. ``None`` when no root change
    reaches anything, which renders no section.
    """
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    entries: list[ImpactEntry] = []
    changes = (
        displayed_changes
        if displayed_changes is not None
        else (getattr(result, "changes", []) or [])
    )
    for c in changes:
        kind = getattr(c, "kind", None)
        if kind and kind in _ROOT_TYPE_CHANGE_KINDS:
            affected = getattr(c, "affected_symbols", None)
            affected_count = len(affected) if affected else 0
            caused = getattr(c, "caused_count", 0)
            if affected_count > 0 or caused > 0:
                entries.append(
                    ImpactEntry(
                        symbol=getattr(c, "symbol", ""),
                        kind_value=kind.value,
                        interface_count=affected_count,
                        caused_count=caused,
                    )
                )
    if not entries:
        return None
    return ImpactData(entries=tuple(entries))


def _build_impact_html(
    result: DiffResult,
    displayed_changes: list[object] | None = None,
    demangle: bool = True,
) -> str:
    """Build an HTML impact summary table.

    ``demangle`` mirrors every other symbol-bearing cell in this module
    (Codex review, fresh evidence: the Root Change column rendered
    ``change.symbol`` raw, bypassing the demangling setting entirely).
    """
    return render_impact(compute_impact(result, displayed_changes), demangle)


def compute_gate_card(result: DiffResult, severity_config: Any) -> GateCardData | None:
    """Collect the CI-gate card's facts, or ``None`` when no severity gate is
    configured.

    The gate decision itself is not computed here -- it is projected from
    :func:`abicheck.policy.gate_decision.gate_decision_for_result`, the same
    single call site ``reporter._build_severity_json`` and
    ``sarif._severity_gate_properties`` read (ADR-061 D9): this function makes
    no policy decision of its own. What it *does* decide is which of the two
    gates the card reports: ``--used-by``/``--required-symbol(s)`` scoping
    (ADR-043) means the CLI exits on the *scoped* gate, not this full-library
    one (CodeRabbit review), so a scoped run reports that one and names the
    full-library gate only as context. ``blocking_categories`` is left empty
    for a scoped card because those categories correspond 1:1 to the full gate
    alone -- attributing them to a scoped-only failure (e.g. a missing
    ``--required-symbol`` entrypoint) would name the wrong decision.
    """
    full_gate = gate_decision_for_result(result, severity_config)
    if full_gate is None:
        return None
    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    scoped_exit_code_scheme = getattr(result, "scoped_exit_code_scheme", None)
    if scoped_exit_code is not None and scoped_exit_code_scheme == "severity":
        return GateCardData(
            scoped=True,
            passed=scoped_exit_code == 0,
            exit_code=scoped_exit_code,
            full_gate_label=(
                "PASS"
                if not full_gate.blocking
                else f"FAIL (exit {full_gate.exit_code})"
            ),
            blocking_categories=(),
        )
    return GateCardData(
        scoped=False,
        passed=not full_gate.blocking,
        exit_code=full_gate.exit_code,
        full_gate_label="",
        blocking_categories=tuple(full_gate.blocking_categories),
    )


def compute_scoped_verdict(result: DiffResult) -> ScopedVerdictData | None:
    """Collect the ``--used-by``/``--required-symbol(s)`` scoped-verdict box
    (ADR-043), or ``None`` when the run was not scoped.

    The verdict box above this one stays computed from the full, unscoped
    diff, but the CLI process exits on the *scoped* verdict floor -- surfaced
    so a reader can't miss the disagreement (mirrors the human-format banner,
    ``_fold_scoped_compat_into_text``).
    """
    scoped_verdict = getattr(result, "scoped_verdict", None)
    if scoped_verdict is None:
        return None
    return ScopedVerdictData(
        verdict_value=(
            scoped_verdict.value
            if hasattr(scoped_verdict, "value")
            else str(scoped_verdict)
        ),
        exit_code=getattr(result, "scoped_exit_code", None),
        exit_code_scheme=getattr(result, "scoped_exit_code_scheme", None),
    )


def _gate_card_html(
    result: DiffResult,
    severity_config: Any,
    *,
    h: Any = None,
) -> str:
    """Render the CI-gate card, or ``""`` when no severity gate is configured.

    *h* is accepted and ignored: it was this function's escaping callable
    before the compute/render split moved every escape decision to the render
    side, and is kept so an existing caller passing ``h=html.escape`` still
    resolves.
    """
    return render_gate_card(compute_gate_card(result, severity_config))


def generate_html_report(
    result: DiffResult,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    title: str | None = None,
    compat_html: bool = False,
    report_kind: str = "binary",
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    demangle: bool = True,
) -> str:
    """Generate a standalone ABICC-compatible HTML ABI report.

    Args:
        result: DiffResult from checker.compare().
        lib_name: Library name for the report header.
        old_version: Old library version string.
        new_version: New library version string.
        old_symbol_count: Total exported public symbol count in the old library.
            Used to compute Binary Compatibility %. If None, approximated from
            changes (legacy behaviour).
        show_only: Optional --show-only filter string (display-only).
        show_impact: If True, append an impact summary table.
        demangle: Demangle C++ symbols in the native table (see ``_symbol_cell``).
        severity_config: Optional severity configuration. When given (native
            report only — the ABICC-compatible ``compat_html`` layout is left
            unchanged), a separate "CI Gate" headline card is rendered
            alongside "Compatibility" so a configured severity gate (e.g. an
            addition promoted to ``error``) is visible even when the
            Compatibility verdict itself reads COMPATIBLE.

    Returns:
        Complete self-contained HTML document as a string.
    """
    verdict = result.verdict.value if hasattr(result.verdict, "value") else str(result.verdict)
    fg, bg = _VERDICT_STYLE.get(verdict, ("#212121", "#f5f5f5"))

    all_changes: list[object] = list(getattr(result, "changes", None) or [])

    # Apply show_only filter (display-only, does not affect metrics)
    if show_only:
        from .checker import Change as _Change
        from .reporter import _suppress_dangling_correlation_notes, apply_show_only

        typed_changes = [c for c in all_changes if isinstance(c, _Change)]
        _kind_sets_fn = getattr(result, "_effective_kind_sets", None)
        filtered = apply_show_only(
            typed_changes,
            show_only,
            policy=result.policy,
            kind_sets=_kind_sets_fn() if _kind_sets_fn is not None else None,
            policy_file=getattr(result, "policy_file", None),
        )
        filtered = _suppress_dangling_correlation_notes(filtered)
        display_changes: list[object] = list(filtered)
    else:
        display_changes = all_changes

    suppressed: list[object] = list(getattr(result, "suppressed_changes", None) or [])
    suppressed_count: int = getattr(result, "suppressed_count", len(suppressed))

    # Split display changes into buckets; duck-typed like compatibility_metrics.
    # ADR-061 Phase 2 item 4b: a real DiffResult reads each verdict from a
    # ReportFinding resolved once per change; a stub falls back as before.
    # Also gate on _effective_kind_sets: report_findings_for needs policy/
    # policy_file too, absent from a verdict-only stub (Codex review).
    _effective_verdict_fn: Callable[[object], object] | None = getattr(result, "_effective_verdict_for_change", None)
    if _effective_verdict_fn is not None and hasattr(result, "_effective_kind_sets"):
        from .report.finding import findings_by_change_id, report_findings_for
        _findings_by_id = findings_by_change_id(report_findings_for(result))  # type: ignore[arg-type]
        def _lookup_verdict(change: object) -> object:
            return _findings_by_id[id(change)].verdict
        _effective_verdict_fn = _lookup_verdict
    # ADR-049 D1: a NOT_EVALUATED finding is partitioned out of the three
    # verdict buckets before they are built, the same way Markdown's own
    # "Not Evaluated (Contract)" section works -- bucketing one by its raw
    # effective verdict rendered it under the red "Changed Symbols (1)"
    # heading on a page whose banner reads NO_CHANGE (Codex review). It
    # gets its own section below instead. Empty without `--contract`.
    from .contract_gating import contract_relevance_of, is_evaluated

    not_evaluated = [ch for ch in display_changes if not is_evaluated(ch)]
    scored_changes = [ch for ch in display_changes if is_evaluated(ch)]
    # Single pass, not three (one per candidate bucket, each re-resolving
    # the effective verdict) as this used to be.
    removed: list[object] = []
    added: list[object] = []
    changed: list[object] = []
    _buckets = {"removed": removed, "added": added, "changed": changed}
    for ch in scored_changes:
        _buckets[_change_bucket(ch, _effective_verdict_fn)].append(ch)

    # Metrics always use the full (unfiltered) change list. policy/kind_sets/
    # policy_file make the Binary Compatibility % agree with the verdict
    # banner above: without them, a policy-demoted removal still counts
    # toward breaking_count by its raw kind, producing e.g. "0.0% binary
    # compatibility" on the same page whose verdict reads COMPATIBLE.
    # Duck-typed via getattr so a lightweight stub result without a real
    # DiffResult's policy machinery still renders (falls back to raw-kind
    # counting when kind_sets is None). ADR-049 D1, same reasoning one level
    # up in `report_summary.build_summary`: a NOT_EVALUATED finding is off
    # the compatibility axis, so counting it here would produce the same
    # kind of disagreement (a page whose banner reads NO_CHANGE showing
    # "0.0% binary compatibility") the policy/kind_sets note above already
    # guards against. Filtered via the shared predicate rather than
    # `result._evaluated_changes()` since a stub result need not expose it.
    from .contract_gating import is_evaluated

    _eff_kind_sets_fn = getattr(result, "_effective_kind_sets", None)
    metrics = compatibility_metrics(
        [c for c in cast(list[HasKind], all_changes) if is_evaluated(c)],
        old_symbol_count,
        policy=getattr(result, "policy", None),
        kind_sets=_eff_kind_sets_fn() if callable(_eff_kind_sets_fn) else None,
        policy_file=getattr(result, "policy_file", None),
    )
    breaking_count = metrics.breaking_count
    bc_pct = metrics.binary_compatibility_pct
    affected_pct = metrics.affected_pct

    h = html.escape
    lib_display = h(lib_name) if lib_name else "library"
    old_display = h(old_version) if old_version else "old"
    new_display = h(new_version) if new_version else "new"

    if compat_html:
        return _generate_compat_html(
            result,
            display_changes,
            removed,
            changed,
            added,
            suppressed,
            suppressed_count,
            bc_pct,
            affected_pct,
            breaking_count,
            verdict,
            lib_display,
            old_display,
            new_display,
            old_symbol_count,
            title,
            report_kind=report_kind,
        )

    verdict_icon = _verdict_icon(verdict)

    gate_html = _gate_card_html(result, severity_config, h=h)

    scoped_html = render_scoped_verdict(compute_scoped_verdict(result))

    if demangle:
        prewarm_demangle_batch(
            [*all_changes, *suppressed, *not_evaluated],
            attrs=("symbol", "description", "old_value", "new_value", "affected_symbols"),
        )
    summary_html = _summary_table(removed, changed, added, suppressed_count)
    nav_html = _nav_bar(removed, changed, added, suppressed_count)

    def _section(title: str, anchor: str, css_class: str, items: list[object]) -> str:
        count = len(items)
        tbl = _changes_table(items, demangle)
        return (
            f"<div class='section {css_class}' id='{anchor}'>"
            f"<h3>{title} ({count})</h3>"
            f"{tbl}"
            f"</div>"
        )

    sections = _build_sections_html(
        removed,
        changed,
        added,
        suppressed,
        suppressed_count,
        _section,
        not_evaluated=not_evaluated,
        relevance_of=contract_relevance_of,
        demangle=demangle,
    )

    if not sections:
        if show_only and all_changes:
            sections.append(
                "<div class='section'><p class='empty'>"
                f"No changes match the current filter (<code>--show-only {h(show_only)}</code>). "
                f"{len(all_changes)} change(s) exist but are excluded by the filter."
                "</p></div>"
            )
        else:
            sections.append(
                "<div class='section'><p class='empty'>"
                "No ABI changes detected between the two versions."
                "</p></div>"
            )

    sections_html = "\n".join(sections)

    symbol_count_note = (
        f" / {old_symbol_count} exported symbols" if old_symbol_count else ""
    )

    redundant_count = getattr(result, "redundant_count", 0)
    redundancy_note = ""
    if redundant_count > 0:
        redundancy_note = (
            f"<div class='section' style='background:#fff3e0; padding:10px; border-left:4px solid #ff9800;'>"
            f"<strong>ℹ️ {redundant_count} redundant change(s)</strong> hidden "
            f"(derived from root type changes). Set <code>scope.show_redundant: true</code> "
            f"in <code>.abicheck.yml</code> to show all."
            f"</div>"
        )

    filter_note = ""
    if show_only:
        filter_note = (
            f"<div class='section' style='background:#e3f2fd; padding:10px; border-left:4px solid #1976d2;'>"
            f"<strong>🔍 Filtered by:</strong> <code>--show-only {h(show_only)}</code> "
            f"({len(display_changes)} of {len(all_changes)} changes shown)"
            f"</div>"
        )

    impact_html = ""
    if show_impact:
        impact_html = _build_impact_html(
            result, displayed_changes=display_changes, demangle=demangle
        )

    body = f"""
<div class="header">
  <h1>{h(title) if title else f"ABI Compatibility Report — {lib_display}"}</h1>
  <div class="meta">
    {old_display} → {new_display} &nbsp;|&nbsp;
    Generated by <strong>abicheck</strong> (ABICC-compatible)
  </div>
  {_file_metadata_html(result)}
</div>

<div class="verdict-box" style="background:{bg}; color:{fg}; border-left:6px solid {fg};">
  <h2>{verdict_icon} Compatibility: {h(verdict)}</h2>
  <div class="bc-metric">
    Binary Compatibility: <strong>{bc_pct:.1f}%</strong>
    <span style="font-size:0.82em; opacity:0.75">
      ({breaking_count} breaking change(s){symbol_count_note})
    </span>
    &nbsp;&nbsp;
    <span style="font-size:0.85em;">
      Removed: <strong>{len(removed)}</strong>
      &nbsp;|&nbsp; Changed: <strong>{len(changed)}</strong>
      &nbsp;|&nbsp; Added: <strong>{len(added)}</strong>
    </span>
  </div>
</div>

{gate_html}
{scoped_html}
{_confidence_html(result)}
{nav_html}
{summary_html}
{filter_note}
{redundancy_note}
{sections_html}
{impact_html}

{render_footer("ABICC-compatible report format")}
"""
    return render_document(
        title=h(title)
        if title
        else f"ABI Report: {lib_display} {old_display} → {new_display}",
        body=body,
    )


def compute_not_evaluated_section(
    not_evaluated: list[object],
    relevance_of: Callable[[object], object] | None = None,
) -> NotEvaluatedSectionData:
    """Collect the rows of the ADR-049 D1 "Not Evaluated (Contract)" table.

    Resolving each finding's contract relevance is the caller's own predicate
    (``contract_gating.contract_relevance_of``), threaded in rather than
    imported here so this stays a plain projection of already-decided facts.
    """
    rows = []
    for ch in not_evaluated:
        relevance = relevance_of(ch) if relevance_of is not None else None
        rows.append(
            NotEvaluatedRow(
                symbol=getattr(ch, "symbol", "") or "",
                kind_value=str(getattr(getattr(ch, "kind", None), "value", "")),
                relevance=str(getattr(relevance, "value", "") or ""),
                reason=str(getattr(ch, "contract_reason_code", "") or ""),
                correlated=str(getattr(ch, "correlated_change_kind", None) or ""),
            )
        )
    return NotEvaluatedSectionData(rows=tuple(rows))


def _build_sections_html(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed: list[object],
    suppressed_count: int,
    section_builder: Callable[[str, str, str, list[object]], str],
    *,
    not_evaluated: list[object] | None = None,
    relevance_of: Callable[[object], object] | None = None,
    demangle: bool = True,
) -> list[str]:
    """Build ordered section blocks for HTML report body.

    *not_evaluated* (ADR-049 D1) renders last, in its own non-verdict
    section: those findings were never scored by compatibility policy, so
    filing them under Removed/Changed/Added would contradict the verdict
    banner at the top of the same page. Defaults to nothing, so a run without
    `--contract` produces the identical document it always did.
    """
    sections: list[str] = []
    for title, anchor, css_class, items in (
        ("⛔ Removed Symbols", "removed", "section-removed", removed),
        ("⚠️ Changed Symbols", "changed", "section-changed", changed),
        ("✅ Added Symbols", "added", "section-added", added),
        ("🔕 Suppressed Changes", "suppressed", "section-suppressed", suppressed),
    ):
        if items:
            sections.append(section_builder(title, anchor, css_class, items))
    if suppressed_count and not suppressed:
        sections.append(
            f"<div class='section section-suppressed' id='suppressed'>"
            f"<h3>🔕 Suppressed Changes ({suppressed_count})</h3>"
            f"<p class='empty'>Details not available (suppressed_changes list is empty).</p>"
            f"</div>"
        )
    if not_evaluated:
        sections.append(
            render_not_evaluated_section(
                compute_not_evaluated_section(not_evaluated, relevance_of),
                demangle,
            )
        )
    return sections


def write_html_report(
    result: DiffResult,
    output_path: Path,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    title: str | None = None,
    compat_html: bool = False,
    report_kind: str = "binary",
    *,
    demangle: bool = True,
) -> None:
    """Write HTML report to *output_path*, creating parent directories as
    needed -- passing ``demangle`` through, unlike the CLI's own
    ``--no-demangle`` this writer previously had no equivalent for."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_html_report(
        result,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        old_symbol_count=old_symbol_count,
        title=title,
        compat_html=compat_html,
        report_kind=report_kind,
        demangle=demangle,
    )
    output_path.write_text(content, encoding="utf-8")
