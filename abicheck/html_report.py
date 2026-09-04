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

import dataclasses
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .checker_policy import HasKind, impact_for

# Page chrome (DOCTYPE/head/stylesheet/body frame, verdict palette, footer) now
# lives in one shared seam (``html_template``). ``_CSS`` is re-exported via
# redundant alias (it was previously defined in this module) so any code that
# imported it from here keeps working. ``generate_html_report`` itself no
# longer touches ``_VERDICT_STYLE``/``render_document``/``render_footer``
# directly -- those moved into ``report/render_html.py`` alongside the rest
# of this module's formatting responsibility (ADR-061 Phase 2 item 1).
from .html_template import _CSS as _CSS
from .policy.gate_decision import gate_decision_for_result

# ADR-061 Phase 2 item 1: the pure HTML projection half of this module.
# Every ``compute_*`` below returns one of these frozen structs (or, for the
# whole document, a plain JSON-shaped mapping); the matching ``render_*``
# turns it into markup and makes no decision of its own. The low-level
# formatters re-exported under their original private names physically live
# there now (see ``render_html``'s own docstring for why moving them, rather
# than leaving them here, is what avoids a same-layer import cycle) -- every
# existing caller and its direct test coverage resolves through these
# aliases unchanged.
from .report.document import ReportDocument
from .report.render_html import (
    ChangeRow,
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
)
from .report.render_html_document import render_html_document
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

# Kept under its original private name so every existing caller resolves
# unchanged -- `appcompat_html.py` imports `_abbr_symbol_text`/
# `_changes_table` from here, and `tests/test_html_report_demangle.py`
# exercises it by this spelling. A plain assignment rather than a renaming
# import, so both ruff and mypy read it as a deliberate re-export rather
# than an unused import.
_abbr_symbol_text = abbr_symbol_text


def compute_full_change_rows(changes: Iterable[object]) -> tuple[ChangeRow, ...]:
    """Resolve every fact a changes-table row needs for one change: the four
    registry-lookup decisions (kind string, category, impact text, ABICC
    severity band) plus every raw display field `render_changes_table`/
    `render_compat_changes_table` read directly off a live `Change` before
    ADR-061 Phase 2 item 1 closed for HTML.

    Building this once per render, rather than reading `Change` attributes
    mid-render, is what lets those two functions -- and the whole-document
    `render_html_document` -- become pure `ReportDocument` projections: a
    `ChangeRow` is an ordinary, JSON-round-trippable value, so this replaces
    the previous `id(change)`-keyed `ChangeRowFactsById` lookup table (needed
    only because `Change` is not hashable) with a plain ordered tuple.
    """
    rows = []
    for ch in changes:
        ks = kind_str(ch)
        kind = getattr(ch, "kind", None)
        relevance = getattr(ch, "contract_relevance", None)
        assurance = getattr(ch, "contract_assurance", None)
        decision = getattr(ch, "compatibility_decision", None)
        rows.append(
            ChangeRow(
                kind=ks,
                category=category(ks),
                impact=(impact_for(kind) or "") if kind else "",
                severity=severity(ks),
                symbol=getattr(ch, "symbol", "") or "",
                description=getattr(ch, "description", "") or "",
                old_value=str(getattr(ch, "old_value", "") or ""),
                new_value=str(getattr(ch, "new_value", "") or ""),
                source_location=getattr(ch, "source_location", None) or None,
                affected_symbols=tuple(getattr(ch, "affected_symbols", None) or ()),
                caused_count=getattr(ch, "caused_count", 0) or 0,
                contract_relevance=(
                    str(relevance.value) if relevance is not None else None
                ),
                contract_reason_code=(
                    getattr(ch, "contract_reason_code", None) or None
                ),
                contract_assurance=(
                    str(assurance.value) if assurance is not None else None
                ),
                compatibility_decision=(
                    str(decision.value) if decision is not None else None
                ),
                contract_evidence_refs=tuple(
                    getattr(ch, "contract_evidence_refs", None) or ()
                ),
                correlated_change_kind=(
                    getattr(ch, "correlated_change_kind", None) or None
                ),
            )
        )
    return tuple(rows)


def _changes_table(changes: list[object], demangle: bool = True) -> str:
    """Native changes table. Kept at its original signature -- `appcompat_html.py`
    imports it, and it has its own direct test coverage -- so the per-change
    fact resolution happens here rather than being pushed onto every caller."""
    return render_changes_table(compute_full_change_rows(changes), demangle)


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


def _build_compat_problem_data(
    changed: list[object], added: list[object], removed: list[object]
) -> dict[str, object]:
    """Bucket ``changed`` into ABICC's type/symbol/other severity bands.

    This is the one business decision the pre-split ``_generate_compat_html``
    made mid-render (a registry lookup via `is_type_problem`/
    `is_symbol_problem`/`severity`); resolving it here, once, is what lets
    the compat-mode renderer make none of its own.
    """
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

    def _rows_by_severity(
        bucket: dict[str, list[object]],
    ) -> dict[str, list[dict[str, object]]]:
        return {
            sev: [dataclasses.asdict(row) for row in compute_full_change_rows(items)]
            for sev, items in bucket.items()
        }

    return {
        "added_rows": [
            dataclasses.asdict(row) for row in compute_full_change_rows(added)
        ],
        "removed_rows": [
            dataclasses.asdict(row) for row in compute_full_change_rows(removed)
        ],
        "type_problems": _rows_by_severity(type_problems),
        "symbol_problems": _rows_by_severity(symbol_problems),
        "other_problems": _rows_by_severity(other_problems),
    }


def build_html_document(
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
) -> ReportDocument:
    """Resolve every fact the HTML report needs into one JSON-shaped
    :class:`~abicheck.report.document.ReportDocument` -- the compute half of
    ADR-061 Phase 2 item 1's HTML closure.

    ``generate_html_report`` is now a two-line wrapper over this function and
    ``report.render_html.render_html_document``: every business decision
    this module makes (show_only filtering, bucketing, compatibility
    metrics, which sections exist, ABICC severity-band classification for
    the ``compat_html`` layout) lives here, never in the renderer.
    """
    verdict = (
        result.verdict.value
        if hasattr(result.verdict, "value")
        else str(result.verdict)
    )

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
    _effective_verdict_fn: Callable[[object], object] | None = getattr(
        result, "_effective_verdict_for_change", None
    )
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

    file_metadata = compute_file_metadata(result)
    shared: dict[str, object] = {
        "verdict": verdict,
        "lib_name": lib_name,
        "old_version": old_version,
        "new_version": new_version,
        "title": title,
        "old_symbol_count": old_symbol_count,
        "bc_pct": bc_pct,
        "affected_pct": affected_pct,
        "breaking_count": breaking_count,
        "file_metadata": (
            dataclasses.asdict(file_metadata) if file_metadata is not None else None
        ),
    }

    # compat_html (ABICC-clone layout) ignores severity_config entirely and
    # never demangles -- matching the pre-split short-circuit exactly.
    if compat_html:
        return ReportDocument.from_mapping(
            {
                **shared,
                "mode": "compat",
                "report_kind": report_kind,
                # The 5-way Verdict -> ABICC's 2-way compatible/incompatible
                # bucketing is a policy interpretation (which verdicts count
                # as "incompatible" for ABICC-compatibility purposes), not a
                # formatting choice, so it belongs here -- not re-derived in
                # the renderer (Codex review, fresh evidence).
                "compat_verdict": (
                    "incompatible"
                    if verdict in ("BREAKING", "API_BREAK")
                    else "compatible"
                ),
                "compat": _build_compat_problem_data(changed, added, removed),
            }
        )

    # Demangle-cache prewarming is a rendering concern, not a document fact,
    # so it happens once in `render_html_document` (the function that
    # actually walks rows and calls `demangle`/`demangle_text`) rather than
    # here -- see that function's own docstring. Prewarming here too, ahead
    # of a render that always follows in the same process via
    # `generate_html_report`, would just be redundant cache-hit work.

    sections = _build_sections_data(
        removed,
        changed,
        added,
        suppressed,
        suppressed_count,
        not_evaluated=not_evaluated,
        relevance_of=contract_relevance_of,
    )

    empty_state: dict[str, object] | None = None
    if not sections:
        if show_only and all_changes:
            empty_state = {
                "kind": "filtered",
                "show_only": show_only,
                "all_changes_count": len(all_changes),
            }
        else:
            empty_state = {"kind": "no_changes"}

    confidence = compute_confidence(result)
    gate_card = compute_gate_card(result, severity_config)
    scoped_verdict = compute_scoped_verdict(result)
    impact = compute_impact(result, display_changes) if show_impact else None

    return ReportDocument.from_mapping(
        {
            **shared,
            "mode": "native",
            "demangle": demangle,
            "show_only": show_only,
            "show_impact": show_impact,
            "all_changes_count": len(all_changes),
            "display_changes_count": len(display_changes),
            "redundant_count": getattr(result, "redundant_count", 0),
            "nav_bar": dataclasses.asdict(
                compute_nav_bar(removed, changed, added, suppressed_count)
            ),
            "summary_table": dataclasses.asdict(
                compute_summary_table(removed, changed, added, suppressed_count)
            ),
            "confidence": (
                dataclasses.asdict(confidence) if confidence is not None else None
            ),
            "gate_card": (
                dataclasses.asdict(gate_card) if gate_card is not None else None
            ),
            "scoped_verdict": (
                dataclasses.asdict(scoped_verdict)
                if scoped_verdict is not None
                else None
            ),
            "impact": dataclasses.asdict(impact) if impact is not None else None,
            "sections": sections,
            "empty_state": empty_state,
        }
    )


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
        demangle: Demangle C++ symbols in the native table (see ``abbr_symbol_text``).
        severity_config: Optional severity configuration. When given (native
            report only — the ABICC-compatible ``compat_html`` layout is left
            unchanged), a separate "CI Gate" headline card is rendered
            alongside "Compatibility" so a configured severity gate (e.g. an
            addition promoted to ``error``) is visible even when the
            Compatibility verdict itself reads COMPATIBLE.

    Returns:
        Complete self-contained HTML document as a string.
    """
    document = build_html_document(
        result,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        old_symbol_count=old_symbol_count,
        title=title,
        compat_html=compat_html,
        report_kind=report_kind,
        show_only=show_only,
        show_impact=show_impact,
        severity_config=severity_config,
        demangle=demangle,
    )
    return render_html_document(document)


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


def _build_sections_data(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed: list[object],
    suppressed_count: int,
    *,
    not_evaluated: list[object] | None = None,
    relevance_of: Callable[[object], object] | None = None,
) -> list[dict[str, object]]:
    """Build the ordered list of section facts for the native HTML report
    body -- the JSON-shaped counterpart of the pre-split
    ``_build_sections_html``, which built markup directly. Each entry names
    its own ``kind`` so ``report.render_html.render_html_document`` can
    dispatch without making any decision of its own.

    *not_evaluated* (ADR-049 D1) renders last, in its own non-verdict
    section: those findings were never scored by compatibility policy, so
    filing them under Removed/Changed/Added would contradict the verdict
    banner at the top of the same page. Defaults to nothing, so a run without
    `--contract` produces the identical document it always did.
    """
    sections: list[dict[str, object]] = []
    for title, anchor, css_class, items in (
        ("⛔ Removed Symbols", "removed", "section-removed", removed),
        ("⚠️ Changed Symbols", "changed", "section-changed", changed),
        ("✅ Added Symbols", "added", "section-added", added),
        ("🔕 Suppressed Changes", "suppressed", "section-suppressed", suppressed),
    ):
        if items:
            sections.append(
                {
                    "kind": "changes",
                    "title": title,
                    "anchor": anchor,
                    "css_class": css_class,
                    "rows": [
                        dataclasses.asdict(row)
                        for row in compute_full_change_rows(items)
                    ],
                }
            )
    if suppressed_count and not suppressed:
        sections.append({"kind": "suppressed_placeholder", "count": suppressed_count})
    if not_evaluated:
        sections.append(
            {
                "kind": "not_evaluated",
                "data": dataclasses.asdict(
                    compute_not_evaluated_section(not_evaluated, relevance_of)
                ),
            }
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
