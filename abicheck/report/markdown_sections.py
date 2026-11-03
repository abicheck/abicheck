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

"""Compute halves of the default Markdown view's per-section structs.

Each ``compute_*`` reads a ``DiffResult``/``Change`` sequence and returns one
of ``render_markdown``'s frozen section structs (``None`` when the section does
not exist); ``markdown_compute`` assembles them into the ``ReportDocument`` the
Markdown renderers project. Moved from the retired root ``reporter_markdown``
(ADR-061).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..checker import (
    Change,
    DiffResult,
    LibraryMetadata,
)
from . import (
    change_inventory as _inventory,
    contract_conflicts_markdown as _ccm,
    render_markdown as _rmd,
    weak_removals as _weak,
)
from .kind_rollup import roll_up_large_kinds
from .outcome_summary import (
    section_severity_label as _section_severity_label,
    section_severity_level as _section_severity_level,
)
from .policy_facts import (
    ENVIRONMENT_DRIFT_KINDS,
    classification_for,
    kind_sets_for,
    reclassify_rules_in_effect,
    release_recommendation_for,
)

if TYPE_CHECKING:
    from datetime import date

    from ..policy.severity import SeverityConfig
    from .finding import ReportFinding

# ---------------------------------------------------------------------------
# Impact summary
# ---------------------------------------------------------------------------


def compute_impact_table(
    result: DiffResult,
    displayed_changes: list[Change] | None = None,
    findings: Sequence[ReportFinding] | None = None,
) -> _rmd.ImpactTable | None:
    """Build the impact summary table's structured intermediate.

    When *displayed_changes* is given (e.g. after ``--show-only`` filtering),
    only those changes are considered.  Interface counts use unique
    ``affected_symbols`` names; ``caused_count`` is shown separately to
    avoid double-counting.
    """
    from ..checker import _ROOT_TYPE_CHANGE_KINDS

    changes = (
        displayed_changes if displayed_changes is not None else list(result.changes)
    )

    # Collect root type changes with their impact
    root_entries: list[_rmd.ImpactRootEntry] = []
    for c in changes:
        if c.kind in _ROOT_TYPE_CHANGE_KINDS:
            affected_count = len(c.affected_symbols) if c.affected_symbols else 0
            if affected_count > 0 or c.caused_count > 0:
                root_entries.append(
                    _rmd.ImpactRootEntry(
                        symbol=c.symbol,
                        kind=c.kind.value,
                        iface_count=affected_count,
                        caused=c.caused_count,
                    )
                )

    # Non-type direct removals, read from each finding's resolved bucket
    # (*findings*: the envelope's, when the caller holds one), never from a
    # kind-name suffix -- `func_noexcept_removed` is a trait change, and
    # `func_deleted` a removal.
    if findings is None:
        from .finding import build_report_findings

        findings = build_report_findings(
            changes,
            policy=result.policy,
            kind_sets=kind_sets_for(result),
            policy_file=result.policy_file,
        )
    direct_removals = sum(
        1
        for f in findings
        if f.bucket == "removed" and f.change.kind not in _ROOT_TYPE_CHANGE_KINDS
    )

    if not root_entries and direct_removals == 0:
        return None

    return _rmd.ImpactTable(
        root_entries=tuple(root_entries), direct_removals=direct_removals
    )


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------


def _fmt_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Severity section helpers
# ---------------------------------------------------------------------------

_BREAKING_ICON = "❌"  # ❌
_SOURCE_BREAK_ICON = "⚠️"  # ⚠️
_RISK_ICON = "⚠️"  # ⚠️
_HYGIENE_ICON = "\U0001f9f9"  # 🧹
_QUALITY_ICON = "\U0001f50d"  # 🔍
_ADDITION_ICON = "✅"  # ✅
_INFORMATIONAL_ICON = "ℹ️"  # ℹ️


def _hygiene_evolution_counts_line(hygiene: list[Change]) -> str:
    """``"introduced: 2 · resolved: 1 · persistent: 40016 · not evaluated: 0"``
    for a list of cross-source-evolution-stamped findings -- the Markdown
    counterpart of ``report.cross_source_evolution.
    CrossSourceEvolutionSummary`` (JSON's own per-state counts), so a
    reader can see the split without counting bullet points (ADR-068
    finding A)."""
    from ..model.evidence_status import CrossSourceEvolution

    counts = dict.fromkeys(CrossSourceEvolution, 0)
    for c in hygiene:
        cse = getattr(c, "cross_source_evolution", None)
        if cse is not None:
            counts[cse] += 1
    return (
        f"Introduced: {counts[CrossSourceEvolution.INTRODUCED]} · "
        f"Resolved: {counts[CrossSourceEvolution.RESOLVED]} · "
        f"Persistent: {counts[CrossSourceEvolution.PERSISTENT]} · "
        f"Not evaluated: {counts[CrossSourceEvolution.NOT_EVALUATED]}"
    )


def compute_library_files(
    old_meta: LibraryMetadata | None, new_meta: LibraryMetadata | None
) -> _rmd.LibraryFilesSection:
    """The structured intermediate for the '## Library Files' section."""
    old_path = getattr(old_meta, "path", "—") if old_meta else "—"
    new_path = getattr(new_meta, "path", "—") if new_meta else "—"
    old_sha = getattr(old_meta, "sha256", "—")[:12] if old_meta else "—"
    new_sha = getattr(new_meta, "sha256", "—")[:12] if new_meta else "—"
    old_size = _fmt_size(old_meta.size_bytes) if old_meta else "—"
    new_size = _fmt_size(new_meta.size_bytes) if new_meta else "—"
    return _rmd.LibraryFilesSection(
        old_path=old_path,
        new_path=new_path,
        old_sha=old_sha,
        new_sha=new_sha,
        old_size=old_size,
        new_size=new_size,
    )


def _rolled_up_group(
    heading: str, changes: list[Change], level: object | None, **kw: Any
) -> _rmd.ChangeGroup:
    """A non-gating section's group with its flooded kinds rolled up."""
    items, rollups = roll_up_large_kinds(changes, severity_level=level)
    return _rmd.ChangeGroup(
        heading=heading, changes=tuple(items), rollups=rollups, **kw
    )


def compute_severity_sections(
    breaking: list[Change],
    source_breaks: list[Change],
    risk: list[Change],
    compatible: list[Change],
    *,
    severity_config: SeverityConfig | None = None,
) -> _rmd.SeveritySectionsData:
    """The structured intermediate for all severity-grouped sections."""
    groups: list[_rmd.ChangeGroup] = []

    if breaking:
        sev_label = _section_severity_label(severity_config, "abi_breaking")
        groups.append(
            _rmd.ChangeGroup(
                heading=f"## {_BREAKING_ICON} Breaking Changes{sev_label}",
                changes=tuple(breaking),
                oneline=False,
                note_lines=_weak.weak_removal_note_lines(breaking),
            )
        )

    if source_breaks:
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        groups.append(
            _rmd.ChangeGroup(
                heading=f"## {_SOURCE_BREAK_ICON} Source-Level Breaks{sev_label}",
                changes=tuple(source_breaks),
                oneline=False,
                note_lines=_weak.weak_removal_note_lines(source_breaks),
            )
        )

    if risk:
        # ADR-068 finding A: RISK_KINDS mixes two different stories --
        # ordinary deployment-compatibility risk (a new GLIBC version
        # requirement, etc.) and the cross-source hygiene checks
        # (workflows.cross_source_hygiene) `compare()` runs automatically
        # on every invocation. The latter are stamped with an OLD->NEW
        # evolution state (introduced/resolved/persistent/not_evaluated) --
        # a `persistent` finding is pre-existing hygiene debt, not new
        # drift, and lumping it under "Deployment Risk Changes" with that
        # section's GLIBC-oriented blurb both mislabels it and drowns any
        # genuine deployment-risk finding in the same section. Split them
        # into their own section instead.
        hygiene = [
            c for c in risk if getattr(c, "cross_source_evolution", None) is not None
        ]
        deployment_risk = [
            c for c in risk if getattr(c, "cross_source_evolution", None) is None
        ]
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        risk_level = _section_severity_level(severity_config, "potential_breaking")
        if deployment_risk:
            groups.append(
                _rolled_up_group(
                    f"## {_RISK_ICON} Deployment Risk Changes{sev_label}",
                    deployment_risk,
                    risk_level,
                    oneline=True,
                    note_lines=(
                        "> These changes are **binary-compatible** but may cause the library to fail",
                        "> loading on older systems (e.g. a new GLIBC version requirement). Verify",
                        "> your target environment before deploying.",
                    ),
                )
            )
        if hygiene:
            groups.append(
                _rolled_up_group(
                    f"## {_HYGIENE_ICON} Cross-Source Hygiene Findings{sev_label}",
                    hygiene,
                    risk_level,
                    oneline=True,
                    note_lines=(
                        "> These findings compare each snapshot's own evidence sources against each",
                        "> other (e.g. an exported symbol missing from public headers) -- they are",
                        "> **not** OLD vs. NEW drift. Each carries its own evolution state relative to",
                        "> the OLD snapshot: `persistent` findings already existed before this change",
                        f"> and are not new; only `introduced` ones are new. {_hygiene_evolution_counts_line(hygiene)}",
                    ),
                )
            )

    if compatible:
        from ..policy.classification import (
            ADDITION_KINDS as _ADDITION_KINDS,
            INFORMATIONAL_KINDS as _INFORMATIONAL_KINDS,
        )

        quality = [
            c
            for c in compatible
            if c.kind not in _ADDITION_KINDS and c.kind not in _INFORMATIONAL_KINDS
        ]
        additions_list = [c for c in compatible if c.kind in _ADDITION_KINDS]
        informational = [c for c in compatible if c.kind in _INFORMATIONAL_KINDS]
        if quality:
            sev_label = _section_severity_label(severity_config, "quality_issues")
            groups.append(
                _rolled_up_group(
                    f"## {_QUALITY_ICON} Quality Issues{sev_label}",
                    quality,
                    _section_severity_level(severity_config, "quality_issues"),
                    oneline=True,
                )
            )
        if additions_list:
            # Same per-change detail as Breaking/Source-Level Breaks
            # (kind, location, impact) — a bare description dropped the
            # kind and any per-kind caveat (e.g. enum_member_added's
            # "may shift subsequent values" note), silently losing
            # information a reviewer needs to approve new public API
            # surface.
            sev_label = _section_severity_label(severity_config, "addition")
            groups.append(
                _rolled_up_group(
                    f"## {_ADDITION_ICON} Additions{sev_label}",
                    additions_list,
                    _section_severity_level(severity_config, "addition"),
                    oneline=False,
                )
            )
        if informational:
            # ADR-078: recorded, never gated -- no configurable level, so the
            # heading carries no severity label.
            groups.append(
                _rolled_up_group(
                    f"## {_INFORMATIONAL_ICON} Informational (no material change)",
                    informational,
                    None,
                    oneline=True,
                )
            )

    return _rmd.SeveritySectionsData(groups=tuple(groups))


def compute_environment_drift(
    changes: list[Change],
) -> _rmd.EnvironmentDriftSection | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_environment_drift_section`.

    Group environment/toolchain-drift findings under one heading.

    These findings share a root cause the severity sections cannot express:
    the *build environment* moved (compiler, binutils/linker defaults,
    glibc/sysroot), not the library's declared interface. Summarizing them
    together answers the reviewer's first question — "was this diff caused by
    a source change or by a rebuild?" — without duplicating the per-finding
    details already listed in the severity sections above.
    """
    drift = [c for c in changes if c.kind.value in ENVIRONMENT_DRIFT_KINDS]
    if not drift:
        return None
    return _rmd.EnvironmentDriftSection(
        entries=tuple(
            _rmd.EnvironmentDriftEntry(kind=c.kind.value, description=c.description)
            for c in drift
        )
    )


def compute_headline_table(
    result: DiffResult, emoji: str, label: str
) -> _rmd.HeadlineTable:
    """Headline table intermediate: evaluated-only counts (ADR-049 D1/D11), hygiene on its own row."""
    classified = classification_for(result)
    return _rmd.HeadlineTable(
        library=result.library,
        old_version=result.old_version,
        new_version=result.new_version,
        verdict_emoji=emoji,
        verdict_label=label,
        breaking=len(classified.breaking),
        source_breaks=len(classified.source_breaks),
        risk=len(_inventory.unstamped(classified.risk)),
        compatible=len(classified.compatible),
        not_evaluated=len(result.not_evaluated),
        **_inventory.headline_hygiene_fields(classified.risk),
    )


def compute_confidence_section(result: DiffResult) -> _rmd.ConfidenceSection | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_confidence_section`."""
    conf = getattr(result, "confidence", None)
    if conf is None:
        return None
    tiers = getattr(result, "evidence_tiers", None)
    cov_warns = getattr(result, "coverage_warnings", None)
    conf_val = conf.value if hasattr(conf, "value") else str(conf)
    tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
    etier = getattr(result, "evidence_tier", None)
    etier_val = (
        etier.value if (etier is not None and hasattr(etier, "value")) else str(etier)
    )
    comparability = getattr(result, "comparability_assurance", None)
    comparability_dimensions = (
        tuple(sorted(comparability.items())) if comparability else ()
    )
    return _rmd.ConfidenceSection(
        confidence_upper=conf_val.upper(),
        evidence_tier=etier_val,
        evidence_tiers_str=tier_str,
        coverage_warnings=tuple(cov_warns) if cov_warns else (),
        comparability_dimensions=comparability_dimensions,
    )


def compute_contract_conflicts_section(
    result: DiffResult,
) -> _ccm.ContractConflictsSection | None:
    """The structured intermediate for Workstream E slice S3's Markdown
    section (:func:`~abicheck.report.contract_conflicts_markdown.render_contract_conflicts_section`).

    ``None`` when ``contract_conflicts`` is not a list (contract evaluation
    never ran, or ran against a hand-built ``DiffResult`` that never set the
    field) — the same "typed field, defensive narrowing" pattern
    ``reporter_contract_blocks.add_contract_context`` already uses for
    ``contract_context``.
    """
    conflicts = getattr(result, "contract_conflicts", None)
    if not isinstance(conflicts, list):
        return None
    rows = tuple(
        _ccm.ContractConflictRow(
            conflict_kind=str(c.get("conflict_kind", "")),
            entity=str(c.get("entity", "")),
            side=(c.get("side") if isinstance(c.get("side"), str) else None),
            reason_code=str(c.get("reason_code", "")),
            source_lines=tuple(
                f"{s.get('source_kind', '?')}: {s.get('claim', '')}"
                for s in c.get("sources", ())
                if isinstance(s, dict)
            ),
        )
        for c in conflicts
        if isinstance(c, dict)
    )
    return _ccm.ContractConflictsSection(rows=rows)


def compute_policy_section(
    result: DiffResult, *, today: date | None = None
) -> _rmd.PolicySection:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_policy_section`.

    *today*: an envelope's ``resolved_today`` (ADR-061 gap C, Codex, fresh).
    """
    overrides_text = None
    if result.policy_file and result.policy_file.overrides:
        overrides_text = ", ".join(
            f"`{kind.value}` → `{severity.value}`"
            for kind, severity in result.policy_file.overrides.items()
        )
    reclassify_text = None
    if result.policy_file and result.policy_file.reclassify:
        # Codex review: mirrors the JSON `policy_reclassify` disclosure
        # (reporter.py's `add_policy_overrides`) -- the active rule set,
        # not a per-finding "which rule fired" attribution (see that
        # function's docstring / schema 2.30 history entry). Filtered
        # through active_reclassify_rules so an expired rule -- which
        # ReclassifyRule.matches() would already refuse to apply -- isn't
        # disclosed as though it were still in effect.
        active = reclassify_rules_in_effect(result.policy_file.reclassify, today)
        if active:
            # CodeRabbit review: code-span-wrap describe()'s raw selector
            # text (e.g. `_ZN6oneapi3dal.*`) -- unescaped, `_`/`*` read as
            # Markdown emphasis, same as `Policy overrides` above already does.
            reclassify_text = "; ".join(f"`{rule.describe()}`" for rule in active)
    return _rmd.PolicySection(
        policy=result.policy or "strict_abi",
        overrides_text=overrides_text,
        reclassify_text=reclassify_text,
    )


_BUMP_EMOJI = {"major": "🔴", "minor": "🟢", "patch": "🟢", "none": "✅"}

#: ``reporter_markdown._view_preamble`` (the opening block leaf/root-cause
#: mode share -- title/verdict table, coverage-warning banner, optional
#: recommendation section, the ``--show-only`` filter note) is retired.
#: ``report.render_markdown_alternate.build_leaf_document``/
#: ``build_root_cause_document`` call ``_view_preamble_mapping`` (that
#: module's own JSON-safe compute half) directly instead -- see this
#: module's own docstring and ``_to_markdown_leaf``/``_to_markdown_root_
#: cause``.


def compute_recommendation_section(result: DiffResult) -> _rmd.RecommendationSection:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_recommendation_section`."""
    rec = release_recommendation_for(result)
    return _rmd.RecommendationSection(
        bump_emoji=_BUMP_EMOJI.get(rec.bump.value, ""),
        bump_upper=rec.bump.value.upper(),
        soname_value=rec.soname.value,
        state_value=rec.state.value,
        rationale=rec.rationale,
        policy_acceptance=(
            None if rec.policy_acceptance is None else rec.policy_acceptance.to_dict()
        ),
    )
