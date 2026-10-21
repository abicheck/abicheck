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

"""Reporter (Markdown) — DiffResult → Markdown / review-digest output.

Leaf module: holds the Markdown rendering path plus the shared --show-only
filter and verdict-label maps it depends on. Imports nothing from ``reporter``
so it stays a leaf; ``reporter`` re-exports these names for backward compat.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .report.change_operation import (
    ACTION_TOKEN_OPERATIONS as ACTION_TOKEN_OPERATIONS,
    ELEMENT_TOKEN_ENTITIES as ELEMENT_TOKEN_ENTITIES,
    entity_for_kind as entity_for_kind,
    operation_for_change as operation_for_change,
)
from .report.kind_rollup import roll_up_large_kinds
from .report.not_evaluated_section import (
    compute_not_evaluated as compute_not_evaluated,
)
from .report.policy_facts import kind_sets_for, release_recommendation_for, summary_for
from .report.view import (
    apply_show_only,
    finding_id,
    group_changes_by_root_cause,
    root_cause_id_for_key,
    root_cause_key_and_display,
    show_only_matches,
)

if TYPE_CHECKING:
    from datetime import date

    from .bundle_models import BundleDiffResult
    from .policy.severity import GateDecision, KindSets, SeverityConfig
    from .report.finding import ReportFinding

from .checker import (
    Change,
    DiffResult,
    LibraryMetadata,
    Verdict,
)
from .model.change_catalog.kinds import HasKind
from .model.contract_finding_relevance import is_evaluated
from .model.evidence_status import EvidenceStatus
from .policy.classification import evidence_status_for_result, impact_for
from .report import (
    contract_conflicts_markdown as _ccm,
    render_markdown as _rmd,
    render_review as _review,
    review_compute as _review_compute,
)
from .report.cross_source_evolution import (
    cross_source_evolution_md_suffix as _cross_source_evolution_md_suffix,
)
from .report.disposition_audit import (
    DispositionAudit,
    compute_disposition_audit,
    render_disposition_audit_note,
)
from .report.render_markdown import (
    _contract_decision_text as _contract_decision_text,
    _format_change_md as _format_change_md,
    _format_change_md_oneline as _format_change_md_oneline,
)
from .report.use_case_impact_render import use_cases_by_finding_for

_VERDICT_EMOJI = {
    Verdict.NO_CHANGE: "✅",
    Verdict.COMPATIBLE: "✅",
    Verdict.COMPATIBLE_WITH_RISK: "⚠️",
    Verdict.API_BREAK: "⚠️",
    Verdict.BREAKING: "❌",
}

_VERDICT_LABEL = {
    Verdict.NO_CHANGE: "NO_CHANGE",
    Verdict.COMPATIBLE: "COMPATIBLE",
    Verdict.COMPATIBLE_WITH_RISK: "COMPATIBLE_WITH_RISK",
    Verdict.API_BREAK: "API_BREAK",
    Verdict.BREAKING: "BREAKING",
}


# ---------------------------------------------------------------------------
# Stat mode (text)
# ---------------------------------------------------------------------------


def to_stat(
    result: DiffResult, *, severity_config: SeverityConfig | None = None
) -> str:
    """One-line summary for CI gates.

    *severity_config*, when given, appends a ``gate: PASS|FAIL`` suffix
    reflecting the actual severity-aware exit code — without it, ``--stat``
    output has historically bypassed severity handling entirely (it
    short-circuits in ``service.render_output`` before format dispatch), so
    the verdict label alone could misreport whether the run actually blocks
    CI once severity configuration is in play.
    """
    from .report.change_inventory import render_change_inventory_json
    from .report.document import ReportDocument
    from .report.render_text import render_stat_document

    summary = summary_for(result)
    d: dict[str, object] = {
        "verdict_label": _VERDICT_LABEL[result.verdict],
        "summary": {
            "breaking": summary.breaking,
            "source_breaks": summary.source_breaks,
            "risk_changes": summary.risk_count,
            "compatible_additions": summary.compatible_additions,
            "quality_issues": summary.quality_issues,
            "informational": summary.informational,
            "total_changes": summary.total_changes,
            "change_inventory": render_change_inventory_json(summary.inventory),
        },
        "redundant_count": result.redundant_count,
        # ADR-067 D3: even the one-line view carries the raw-versus-effective
        # counts -- a fully-suppressed comparison must not read as "no
        # changes".
        "disposition_audit_note": render_disposition_audit_note(
            compute_disposition_audit(result, severity_config)
        ),
    }
    # Codex review: oneline/`--stat` lacked the deployment digest.
    if (_dig := getattr(result, "env_matrix_source_sha256", None)) is not None:
        d["env_matrix_source_sha256"] = _dig
    if severity_config is not None:
        from .policy.severity import compute_exit_code

        exit_code = compute_exit_code(
            result.changes,
            severity_config,
            policy=result.policy,
            kind_sets=kind_sets_for(result),
            policy_file=result.policy_file,
        )
        d["severity"] = {"exit_code": exit_code}
    return render_stat_document(ReportDocument.from_mapping(d))


def filter_release_bundle_findings(
    findings: Sequence[Any],
    show_only: str,
    policy: str = "strict_abi",
    policy_file: object | None = None,
) -> list[Any]:
    """Filter ``compare-release`` bundle (cross-library) findings via --show-only.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): bundle findings are release-global, not tied to
    one library's own ``DiffResult``, so :func:`apply_show_only` (which
    expects a real :class:`Change`) cannot be called on them directly.
    Each finding is lowered through its own ``to_change()`` projection --
    the identical lowering
    ``cli_compare_release_helpers._fold_release_global_severity`` already
    uses to resolve a bundle finding's severity for the release's exit
    code -- so this display filter can never disagree with what that exit
    code already computed for the same finding. *kind_sets* is deliberately
    not accepted: bundle findings carry canonical, already-partitioned
    ``ChangeKind``s, matching that same existing severity fold's own
    omission of it.

    *findings* is typed ``Sequence[Any]`` rather than
    ``Sequence[BundleFinding]`` to avoid this leaf module importing
    :mod:`abicheck.bundle_models` at runtime for a type-only reference; any
    object exposing ``to_change() -> Change`` (every real
    :class:`~abicheck.bundle_models.BundleFinding` does) satisfies it.
    """
    return [
        f
        for f in findings
        if show_only_matches(
            show_only, f.to_change(), policy=policy, policy_file=policy_file
        )
    ]


def release_bundle_findings_for_view(
    bundle_result: BundleDiffResult, show_only: str | None
) -> list[Any]:
    """Return *bundle_result*'s findings, ``--view show=``-filtered when active.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): shared by the release fan-out's JSON
    (``cli_compare_release_helpers._format_release_json``) and Markdown
    (``cli_compare_release_helpers._format_release_markdown``) bundle
    sections so the two formats can never disagree about which bundle
    findings a given ``show_only`` selection keeps. Both call sites live in
    ``cli_compare_release_helpers.py`` (a ``frontends`` module, which may
    import both this module and ``report``) as of Codex review, fresh
    evidence, PR #1154 follow-up ("Move release filtering out of the
    Markdown renderer") -- ``report/render_release_markdown.py``'s own
    Markdown section renderer used to call this function directly, which
    `report/AGENTS.md`'s renderer contract forbids (a renderer may only
    format an already-computed projection, never filter one itself); it now
    takes the pre-filtered result as a parameter instead. A no-op (returns
    every finding) when *show_only* is falsy.
    """
    if not show_only:
        return list(bundle_result.bundle_findings)
    return filter_release_bundle_findings(
        bundle_result.bundle_findings,
        show_only,
        policy=bundle_result.policy,
        policy_file=bundle_result.policy_file,
    )


def release_matrix_changes_for_view(
    matrix_result: DiffResult, show_only: str | None
) -> list[Change]:
    """Return *matrix_result*'s changes, ``--view show=``-filtered when active.

    Shared by the release fan-out's JSON and Markdown release-global matrix
    (build-configuration) sections, both called from
    ``cli_compare_release_helpers.py`` for the identical reason as
    :func:`release_bundle_findings_for_view` -- see that function's own
    docstring. Unlike a bundle finding, a matrix result is a real
    :class:`DiffResult`, so it is filtered the identical way a per-library
    one is (``kind_sets``/``policy_file`` included, so the severity
    dimension resolves consistently). A no-op (returns every change) when
    *show_only* is falsy.
    """
    if not show_only:
        return list(matrix_result.changes)
    return apply_show_only(
        matrix_result.changes,
        show_only,
        policy=matrix_result.policy or "strict_abi",
        kind_sets=kind_sets_for(matrix_result),
        policy_file=matrix_result.policy_file,
    )


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
    from .checker import _ROOT_TYPE_CHANGE_KINDS

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
        from .report.finding import build_report_findings

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
# Leaf-change mode helpers
# ---------------------------------------------------------------------------


def root_cause_for_change(
    c: Change, *, referenced_causes: frozenset[str] = frozenset()
) -> tuple[str, str] | None:
    """This change's ``(root_cause_id, root_display)``, or ``None`` when it
    has no real correlation signal (G29 Phase 3 follow-up, ADR-052).

    Uses the exact same grouping key ``--report-mode root-cause`` computes
    (:func:`root_cause_key_and_display`), hashed the same way
    :func:`~abicheck.reporter._to_json_root_cause`/``sarif._root_cause_for``
    already do — so a finding's ``root_cause_id`` here is always identical to
    its ``root_causes[].root_cause_id`` in JSON root-cause mode, or its
    ``properties.rootCauseId`` in SARIF root-cause mode, for the same report.

    Deliberately returns ``None`` for the trivial self-referencing singleton
    case (no ``caused_by_type``, and *c*'s own ``symbol`` isn't referenced by
    any other finding's ``caused_by_type``) — unlike ``--report-mode
    root-cause``'s own grouping (which buckets *every* finding, including
    singletons, since that mode's whole point is showing the full grouping
    structure), a per-finding ``ImpactAssessment.root_cause_id`` naming
    nothing but the finding's own identity is not real information; see
    :func:`root_cause_lookup_for_changes`.
    """
    fid = finding_id(c)
    key, root_display = root_cause_key_and_display(
        c.caused_by_type,
        c.symbol,
        c.kind.value,
        fid,
        referenced_causes=referenced_causes,
    )
    if key == f"finding:{fid}":
        return None
    return root_cause_id_for_key(key), root_display


def root_cause_lookup_for_changes(
    changes: list[Change], *, extra_causes: frozenset[str] = frozenset()
) -> dict[str, tuple[str, str]]:
    """``finding_id -> (root_cause_id, root_display)`` for every change in
    *changes* that has one (G29 Phase 3 follow-up, ADR-052).

    Built once per report (or per self-contained scope, e.g. a suppressed-
    changes list or a scoped-only fold-in) so per-change lookup during
    serialization is O(1) rather than re-deriving ``referenced_causes`` per
    finding. Feeds ``impact.engine.assess_change``'s ``root_cause`` parameter
    — see :func:`root_cause_for_change` for why a finding with no real
    correlation signal is simply absent from the returned dict rather than
    mapped to a self-referencing singleton id.
    """
    referenced_causes = (
        frozenset(c.caused_by_type for c in changes if c.caused_by_type) | extra_causes
    )
    lookup: dict[str, tuple[str, str]] = {}
    for c in changes:
        rc = root_cause_for_change(c, referenced_causes=referenced_causes)
        if rc is not None:
            lookup[finding_id(c)] = rc
    return lookup


def root_cause_evidence_lookup_for_changes(
    changes: list[Change],
) -> dict[str, dict[str, object]]:
    """``finding_id -> root_cause_evidence`` for every change that is a
    member of a G29 Phase 6 ``RootCauseCorrelator`` group (G29 Phase 6
    follow-up: wiring the correlator's output into the JSON/SARIF
    ``impact_assessment`` surface).

    Deliberately independent of :func:`root_cause_lookup_for_changes` above:
    that function's ``root_cause_id``/``root_cause_display`` grouping covers
    *any* two findings sharing a ``caused_by_type``/``symbol``, for every
    ``ChangeKind``; :func:`~abicheck.impact.correlation.correlate_root_causes`
    covers only the load-failure kinds its own module docstring names
    (``FUNC_REMOVED``/``INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API``/
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED``),
    ranked by evidence strength, and drops a symbol with only one correlated
    piece. Built directly from the actual ``Change`` objects the correlator
    grouped — not by matching root-cause ids after the fact — so this stays
    correct independent of whether the two functions' grouping keys happen
    to agree for a given finding.

    Scoped to *changes* only: unlike ``root_cause_lookup_for_changes``, this
    has no ``extra_causes`` parameter, so a correlated sibling that exists
    only as a scoped-only (``--used-by``/``--required-symbol``) finding
    appended to a report after this runs is not seen — deliberately left for
    a follow-up, mirroring this module's own "Deliberately not implemented
    this slice" precedent rather than growing this helper's contract
    unverified.
    """
    from .impact.correlation import correlate_root_causes

    lookup: dict[str, dict[str, object]] = {}
    for group in correlate_root_causes(changes):
        for member_change, level in group.members:
            lookup[finding_id(member_change)] = {
                "evidence_level": level,
                "strongest_evidence_level": group.strongest_evidence_level,
                "evidence_levels": list(group.evidence_levels),
            }
    return lookup


def compute_root_cause_section(
    changes: list[Change],
    scoped_only: list[Change],
    missing_labels: list[str],
    blocks: bool,
    missing_kind: str,
    *,
    contract_evaluation: bool,
    evidence_tiers: Sequence[str] = (),
) -> _rmd.RootCauseSectionData | None:
    """The structured intermediate for ``--report-mode root-cause``'s
    "## Root Causes" section.

    Groups *changes* + *scoped_only* by root cause (:func:`group_changes_by_root_cause`)
    and merges each *missing_labels* entry into a matching group -- or starts
    a new singleton group -- by the identical key
    :func:`root_cause_key_and_display` computes. Returns ``None`` (no
    section at all) only when there is neither a real group nor a missing
    label to show.

    *evidence_tiers* (``DiffResult.evidence_tiers``), when given, lets this
    function qualify an ``UNATTRIBUTED`` finding's impact text here too
    (Codex review, fresh evidence: this root-cause path kept the
    unconditional text even after the full/leaf Markdown views were
    fixed). The resolved impact string -- not just the evidence status --
    is computed here and handed to ``_format_change_md`` as a plain
    value: the earlier fix passed a resolved ``EvidenceStatus`` in and let
    the renderer call ``impact_for()`` itself, which still left that
    registry lookup on the render side (Codex review, fresh evidence --
    ``report/AGENTS.md``'s compute/render split treats a per-change
    ``impact_for()`` call as a report decision the compute half owes the
    renderer, not a formatting choice).

    A *scoped_only* member keeps its ``EvidenceStatus.CONSUMER_PROVEN``
    override rather than being re-scored from *evidence_tiers* like an
    ordinary comparison finding -- JSON (``reporter.to_json``'s
    ``_scoped_only_change_dict``) and SARIF already stamp scoped_only
    findings this way (they are proven by the supplied consumer's own
    import table, independent of what evidence the library-to-library
    comparison itself carries), so recomputing via
    ``evidence_status_for_result`` here would contradict those two formats
    and could demote a consumer-proven finding to "plausible, not
    confirmed" (Codex review, fresh evidence).
    """
    groups = group_changes_by_root_cause(changes + scoped_only)
    if not groups and not missing_labels:
        return None

    scoped_only_ids = {finding_id(c) for c in scoped_only}

    order: list[str] = []
    root_by_key: dict[str, str] = {}
    finding_lines_by_key: dict[str, list[str]] = {}
    count_by_key: dict[str, int] = {}

    def _resolved_impact(c: Change) -> str | None:
        kind = getattr(c, "kind", None)
        if kind is None:
            return None
        evidence_status = (
            EvidenceStatus.CONSUMER_PROVEN
            if finding_id(c) in scoped_only_ids
            else evidence_status_for_result(c, evidence_tiers)
        )
        return impact_for(kind, evidence_status)

    for key, root_display, group_changes in groups:
        order.append(key)
        root_by_key[key] = root_display
        finding_lines_by_key[key] = [
            _format_change_md(c, _resolved_impact(c))
            + _cross_source_evolution_md_suffix(c)
            for c in group_changes
        ]
        count_by_key[key] = len(group_changes)

    if missing_labels:
        referenced_causes = frozenset(
            c.caused_by_type for c in changes + scoped_only if c.caused_by_type
        )
        severity_tag = "breaking" if blocks else "compatible"
        for label in missing_labels:
            key, root_display = root_cause_key_and_display(
                None,
                label,
                missing_kind,
                label,
                referenced_causes=referenced_causes,
            )
            line = (
                f"- `{label}` is required but missing from the new "
                f"library ({severity_tag})"
            )
            # ADR-049 Phase 3 (Codex review, fresh evidence): the
            # non-root-cause markdown/text/review fold-in
            # (cli_compare_fold._fold_scoped_compat_into_text) already
            # tags a missing-contract label with its stamped decision;
            # this root-cause path builds the identical label shape
            # independently and was missing the same treatment, so
            # --report-mode root-cause silently dropped the contract
            # decision for this one finding shape. A missing-contract
            # label has no Change object of its own to read an
            # already-stamped decision off of (unlike scoped_only,
            # rendered via _format_change_md above), so unlike every
            # other contract-rendering site in this fix, this one
            # genuinely needs the caller's own --contract
            # intent threaded through explicitly.
            if contract_evaluation:
                from .contract_scoped_promotion import (
                    stamp_explicit_scope_contract_evaluation,
                )

                label_decision: dict[str, object] = {}
                stamp_explicit_scope_contract_evaluation(label_decision)
                line += (
                    f" [contract: {label_decision['contract_relevance']} "
                    f"({label_decision['contract_reason_code']}), "
                    f"assurance: {label_decision['contract_assurance']}]"
                )
            if key in finding_lines_by_key:
                finding_lines_by_key[key].append(line)
                count_by_key[key] += 1
            else:
                order.append(key)
                root_by_key[key] = root_display
                finding_lines_by_key[key] = [line]
                count_by_key[key] = 1

    return _rmd.RootCauseSectionData(
        groups=tuple(
            _rmd.RootCauseGroupData(
                root_display=root_by_key[key],
                count=count_by_key[key],
                finding_lines=tuple(finding_lines_by_key[key]),
            )
            for key in order
        )
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
    from .model.evidence_status import CrossSourceEvolution

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


_SEVERITY_EMOJI = {
    "error": "❌",  # ❌
    "warning": "⚠️",  # ⚠️
    "info": "ℹ️",  # ℹ️
}


def _section_severity_level(
    severity_config: SeverityConfig | None, category_attr: str
) -> object | None:
    """That section's own resolved severity level, or ``None`` when no
    severity setting is in effect.

    The value half of :func:`_section_severity_label`, which renders the
    same lookup for a heading. Split out because the rollup needs to *act*
    on the level (a gating section is never summarised) and parsing it back
    out of a rendered label would be exactly the kind of second derivation
    this codebase keeps getting bitten by.
    """
    if severity_config is None:
        return None
    return getattr(severity_config, category_attr, None)


def _section_severity_label(
    severity_config: SeverityConfig | None, category_attr: str
) -> str:
    """Return a severity label suffix like ' [ERROR]' for a report section header."""
    if severity_config is None:
        return ""
    level = getattr(severity_config, category_attr, None)
    if level is None:
        return ""
    level_val = level.value if hasattr(level, "value") else str(level)
    emoji = _SEVERITY_EMOJI.get(level_val, "")
    return f" {emoji} `{level_val.upper()}`"


def compute_severity_summary(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    all_changes: list[Change] | None = None,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    scoped_counts: dict[str, int] | None = None,
    scoped_blocking_categories: tuple[str, ...] | None = None,
    today: date | None = None,
) -> _rmd.SeveritySummary:
    """Build the severity configuration summary table's structured intermediate.

    *changes* are the (possibly ``--show-only``-filtered) changes used for
    the displayed ``Count`` column. *all_changes*, when provided, is the
    unfiltered set used for the ``Exit Impact`` column so that filtering the
    display doesn't make this table claim "no exit impact" for a category
    that still fails the actual (unfiltered) severity gate.

    *scoped_counts*/*scoped_blocking_categories* (Codex review), when given
    (from ``result.scoped_severity_counts``/``scoped_blocking_categories``
    on a ``--used-by``/``--required-symbol`` run), override both columns
    with the scoped gate's own numbers -- otherwise this table always
    reflects the full-library ``changes``, so a scoped run whose only
    gating issue is a scoped-only change or missing-contract label (neither
    of which is in ``result.changes``) would show every category at 0 and
    "no exit impact" while the report elsewhere names a real, blocking
    finding. *today*: an envelope's own ``resolved_today`` (ADR-061 gap C,
    Codex, fresh evidence).
    """
    from .policy.severity import (
        SeverityLevel,
        categorize_changes,
        gate_eligible_changes,
    )

    categorized = categorize_changes(
        changes,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )
    # ADR-049 D1: the `Count` column above is factual over what is
    # displayed, but `Exit Impact` is a claim about the *gate* -- so it has
    # to be classified over the same set `severity.compute_exit_code` scores.
    # Without this, a comparison whose only finding is a proven-out-of-contract
    # TYPE_SIZE_CHANGED rendered "causes non-zero exit" beside an exit code of
    # 0 and a NO_CHANGE verdict (Codex review, fresh evidence).
    exit_categorized = (
        categorize_changes(
            gate_eligible_changes(all_changes),
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            today=today,
        )
        if all_changes is not None
        else categorized
    )

    _CATEGORY_INFO: list[tuple[str, str, list[HasKind], list[HasKind]]] = [
        (
            "ABI/API Incompatibilities",
            "abi_breaking",
            categorized.abi_breaking,
            exit_categorized.abi_breaking,
        ),
        (
            "Potential Incompatibilities",
            "potential_breaking",
            categorized.potential_breaking,
            exit_categorized.potential_breaking,
        ),
        (
            "Quality Issues",
            "quality_issues",
            categorized.quality_issues,
            exit_categorized.quality_issues,
        ),
        (
            "Additions",
            "addition",
            categorized.addition,
            exit_categorized.addition,
        ),
    ]

    rows: list[_rmd.SeverityRow] = []
    for label, attr, cat_changes, exit_cat_changes in _CATEGORY_INFO:
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        level_val = level.value if hasattr(level, "value") else str(level)
        emoji = _SEVERITY_EMOJI.get(level_val, "")
        count = (
            scoped_counts.get(attr, 0)
            if scoped_counts is not None
            else len(cat_changes)
        )
        impact = (
            "causes non-zero exit"
            if (
                attr in scoped_blocking_categories
                if scoped_blocking_categories is not None
                else level_val == "error" and len(exit_cat_changes) > 0
            )
            else "no exit impact"
        )
        rows.append(
            _rmd.SeverityRow(
                label=label,
                emoji=emoji,
                level_upper=level_val.upper(),
                count=count,
                impact=impact,
            )
        )
    # ADR-078: the informational category has no configurable level and
    # never gates; it gets a row only when it holds something to disclose.
    info_count = (
        scoped_counts.get("informational", 0)
        if scoped_counts is not None
        else len(categorized.informational)
    )
    if info_count:
        rows.append(
            _rmd.SeverityRow(
                label="Informational (no material change)",
                emoji=_SEVERITY_EMOJI.get(SeverityLevel.INFO.value, ""),
                level_upper=SeverityLevel.INFO.value.upper(),
                count=info_count,
                impact="no exit impact",
            )
        )

    return _rmd.SeveritySummary(rows=tuple(rows))


def compute_library_files(
    old_meta: LibraryMetadata | None, new_meta: LibraryMetadata | None
) -> _rmd.LibraryFilesSection | None:
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
            )
        )

    if source_breaks:
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        groups.append(
            _rmd.ChangeGroup(
                heading=f"## {_SOURCE_BREAK_ICON} Source-Level Breaks{sev_label}",
                changes=tuple(source_breaks),
                oneline=False,
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
        if deployment_risk:
            risk_items, risk_rollups = roll_up_large_kinds(
                deployment_risk,
                severity_level=_section_severity_level(
                    severity_config, "potential_breaking"
                ),
            )
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_RISK_ICON} Deployment Risk Changes{sev_label}",
                    changes=tuple(risk_items),
                    rollups=risk_rollups,
                    oneline=True,
                    note_lines=(
                        "> These changes are **binary-compatible** but may cause the library to fail",
                        "> loading on older systems (e.g. a new GLIBC version requirement). Verify",
                        "> your target environment before deploying.",
                    ),
                )
            )
        if hygiene:
            hygiene_items, hygiene_rollups = roll_up_large_kinds(
                hygiene,
                severity_level=_section_severity_level(
                    severity_config, "potential_breaking"
                ),
            )
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_HYGIENE_ICON} Cross-Source Hygiene Findings{sev_label}",
                    changes=tuple(hygiene_items),
                    rollups=hygiene_rollups,
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
        from .policy.classification import (
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
            quality_items, quality_rollups = roll_up_large_kinds(
                quality,
                severity_level=_section_severity_level(
                    severity_config, "quality_issues"
                ),
            )
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_QUALITY_ICON} Quality Issues{sev_label}",
                    changes=tuple(quality_items),
                    rollups=quality_rollups,
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
                _rmd.ChangeGroup(
                    heading=f"## {_ADDITION_ICON} Additions{sev_label}",
                    changes=tuple(additions_list),
                    oneline=False,
                )
            )
        if informational:
            # ADR-078: recorded, never gated -- no configurable level, so the
            # heading carries no severity label.
            info_items, info_rollups = roll_up_large_kinds(
                informational, severity_level=None
            )
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_INFORMATIONAL_ICON} Informational (no material change)",
                    changes=tuple(info_items),
                    rollups=info_rollups,
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
    from .report_classifications import ENVIRONMENT_DRIFT_KINDS

    drift = [c for c in changes if c.kind.value in ENVIRONMENT_DRIFT_KINDS]
    if not drift:
        return None
    return _rmd.EnvironmentDriftSection(
        entries=tuple(
            _rmd.EnvironmentDriftEntry(kind=c.kind.value, description=c.description)
            for c in drift
        )
    )


# Verdict -> short merge-effect phrase for the reviewer digest.
_VERDICT_MERGE_EFFECT = {
    Verdict.NO_CHANGE: "no ABI/API change — safe to merge",
    Verdict.COMPATIBLE: "backward-compatible — safe to merge",
    Verdict.COMPATIBLE_WITH_RISK: "compatible but carries deployment risk — review advised",
    Verdict.API_BREAK: "source-level (API) break — consumers must recompile",
    Verdict.BREAKING: "binary (ABI) break — blocks merge under a strict gate",
}


def _merge_effect_from_exit_code(exit_code: int) -> str:
    """The two merge-effect phrases, keyed by the resolved severity exit code."""
    if exit_code == 0:
        return "no error-level findings under the configured severity policy — safe to merge"
    return "blocked by severity policy — review required before merge"


def _severity_merge_effect(
    result: DiffResult, severity_config: SeverityConfig
) -> tuple[str, int]:
    """Merge-effect phrase reflecting the actual severity-aware gate.

    Compatibility (``result.verdict``) and the CI gate are independent
    decisions once a severity configuration is in play — e.g. an ``addition``
    finding configured as ``error`` blocks the build even though the verdict
    is ``COMPATIBLE``, and an ``abi_breaking`` finding configured below
    ``error`` does not. The hard-coded ``_VERDICT_MERGE_EFFECT`` phrases would
    misreport both cases, so this asks the severity gate directly instead of
    inferring "safe to merge" from the verdict alone.

    Only called for a direct caller with no already-resolved
    :class:`~abicheck.policy.severity.GateDecision` -- an envelope-driven
    render passes one to :func:`compute_review_digest` instead (ADR-061 gap
    C; CodeRabbit review) so this digest's merge-effect phrase reads the same
    gate SARIF's/HTML's gate blocks do, rather than a second, independent
    ``compute_exit_code`` call that happens to agree.
    """
    from .policy.severity import compute_exit_code

    eff_sets = kind_sets_for(result)
    exit_code = compute_exit_code(
        result.changes,
        severity_config,
        policy=result.policy,
        kind_sets=eff_sets,
        policy_file=result.policy_file,
    )
    return _merge_effect_from_exit_code(exit_code), exit_code


def compute_review_digest(
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
    disposition_audit: DispositionAudit | None = None,
    findings: Sequence[ReportFinding] | None = None,
    gate: GateDecision | None = None,
    review_groups: Sequence[dict[str, object]] | None = None,
    result_counts: dict[str, int] | None = None,
    gate_exit_code: int | None = None,
) -> _review.ReviewDigest:
    """The structured intermediate for :func:`to_review_digest`.

    *severity_config*, when given, drives the merge-effect phrase from the
    actual severity-aware CI gate instead of the raw compatibility verdict —
    compatibility and "blocks CI" are independent decisions once severity
    configuration is in play (see :func:`_severity_merge_effect`).

    *gate*, when given, is the already-resolved
    :class:`~abicheck.policy.severity.GateDecision` the merge-effect phrase
    reads instead of a second, independent ``compute_exit_code`` call --
    the ADR-061 gap C caller (``report/render_markdown_document.
    build_review_digest_document``) passes the ``ReportEnvelope``'s own gate
    (CodeRabbit review), the same decision SARIF's/HTML's gate blocks
    project. A direct caller with no envelope (``severity_config`` given,
    ``gate`` not) keeps the prior behaviour via :func:`_severity_merge_effect`.

    *disposition_audit*, when given, is used verbatim instead of resolving a
    fresh one from *result*/*severity_config* -- the ADR-061 gap C caller
    (``report/render_markdown_document.build_review_digest_document``) passes
    the one already computed by ``report/build.build_report_document``'s
    single shared call, rather than this function re-deriving an identical
    value from the same ledger a second time.

    *findings* is the same reuse for the per-change verdicts the impacted-
    symbols list below rests on: the ADR-061 gap C caller passes the
    ``ReportEnvelope``'s already-resolved set instead of leaving this
    function to call ``report_findings_for`` a second time for the same
    render.
    """
    from .report.finding import report_findings_for

    findings = findings if findings is not None else report_findings_for(result)
    summary = summary_for(result, findings=findings)
    v = result.verdict
    emoji = _VERDICT_EMOJI.get(v, "?")
    label = _VERDICT_LABEL.get(v, v.value)
    if gate is not None:
        effect = _merge_effect_from_exit_code(gate.exit_code)
    elif severity_config is not None:
        effect, gate_exit_code = _severity_merge_effect(result, severity_config)
    else:
        effect = _VERDICT_MERGE_EFFECT.get(v, "")

    # Manual-review banner: scoping requested but the public surface could not
    # be confirmed, so compatibility is unconfirmed (don't overclaim).
    manual_review_banner = bool(
        result.scope_to_public_surface and not result.scope_resolved
    )

    # Coverage-warning banner (Codex review): a clean verdict can still rest
    # on incomplete evidence -- e.g. compare.note_if_same_binary_compared's
    # byte-identical-inputs warning -- and this digest is exactly the
    # GitHub-facing summary a reviewer approves a merge from, so it must not
    # read as unconditionally clean when one of these is present.
    coverage_warnings = _review_compute.compact_coverage_warnings(
        result.coverage_warnings or ()
    )

    scoped = result.scope_to_public_surface
    additions_label = "Public additions" if scoped else "Additions"

    rec = release_recommendation_for(result)

    # Top impacted symbols (breaking + API), capped for readability. Filters
    # by each change's *effective* verdict (policy.evaluate.effective_verdict)
    # rather than raw kind-set membership, so a per-finding override (A4
    # pattern-verdict modulation, frozen-namespace guard) is reflected here
    # the same way it already is in the counts table and merge-effect phrase
    # above — otherwise this section could list a finding the rest of the
    # digest reports as compatible, or omit one it reports as breaking.
    # ADR-049 D1: and over the findings compatibility policy actually scored,
    # for the same reason -- the merge-effect phrase above is derived from the
    # verdict, which a NOT_EVALUATED finding did not reach, so listing one
    # here printed "safe to merge" directly above the symbol it says is
    # impacted (Codex review). The excluded finding keeps its own disclosed
    # section elsewhere in the report; this list is the digest of what gated.
    from .report.surface_changes import compute_surface_changes

    uc = use_cases_by_finding_for(result)  # `compare --use-cases` only
    groups = tuple(review_groups or _review_compute.review_groups_for(findings))
    impacted = [
        f.change
        for f in findings
        if is_evaluated(f.change)
        if f.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    ]

    return _review.ReviewDigest(
        library=result.library,
        old_version=result.old_version,
        new_version=result.new_version,
        verdict_emoji=emoji,
        verdict_label=label,
        effect=effect,
        manual_review_banner=manual_review_banner,
        coverage_warnings=coverage_warnings,
        additions_label=additions_label,
        breaking_count=summary.breaking,
        source_breaks_count=summary.source_breaks,
        risk_count=summary.risk_count,
        # additions_count/quality_issues_count are two rows in the same
        # table, so they must not overlap. `summary.compatible_additions`
        # (report_schema_version 4.0) already counts only genuine additions
        # -- use it directly, no second `quality_issues` subtraction (that
        # is `pr_comment.py`'s own release-field derivation, a separate,
        # differently-scoped field -- `workflows/release_members.py`).
        additions_count=summary.compatible_additions,
        quality_issues_count=summary.quality_issues,
        informational_count=summary.informational,
        scoped=bool(scoped),
        out_of_surface_count=result.out_of_surface_count,
        bump_value=rec.bump.value,
        soname_value=rec.soname.value,
        impacted=tuple(
            _review.ImpactedSymbol(
                symbol=c.symbol or "?",
                kind=c.kind.value,
                use_cases=(uc or {}).get(finding_id(c), ()),
            )
            for c in impacted
        ),
        disposition_audit=(
            disposition_audit
            if disposition_audit is not None
            else compute_disposition_audit(result, severity_config)
        ),
        surface_changes=compute_surface_changes(result, findings),
        env_matrix_source_sha256=result.env_matrix_source_sha256,
        pattern_modulations=tuple(getattr(result, "pattern_modulations", ()) or ()),
        review_groups=groups,
        review_group_use_cases=_review_compute.use_cases_by_group(groups, uc),
        result_counts=result_counts or {},
        policy=result.policy or "strict_abi",
        gate_exit_code=gate.exit_code if gate is not None else gate_exit_code,
        evidence_summary=_review_compute.compact_evidence_summary(result),
        show_release_recommendation=bool(
            result.policy_file is not None
            and getattr(result.policy_file, "versioning_stated", False)
        ),
        policy_acceptance=(
            None if rec.policy_acceptance is None else rec.policy_acceptance.to_dict()
        ),
    )


def compute_headline_table(
    result: DiffResult, emoji: str, label: str
) -> _rmd.HeadlineTable:
    """The structured intermediate for the report's headline summary table.

    ADR-049 D1/D11: when contract evaluation excluded findings from the
    compatibility axis, say so in the headline table. The four counts above
    are now over the *evaluated* findings only, so a reader who sees a
    `NO_CHANGE` verdict beside a populated "Not Evaluated" section has the
    count that reconciles them rather than an apparent contradiction. The
    row is absent for every run that did not opt in, where it is always 0.
    """
    from .policy.evaluate import evaluate

    classified = evaluate(result)
    return _rmd.HeadlineTable(
        library=result.library,
        old_version=result.old_version,
        new_version=result.new_version,
        verdict_emoji=emoji,
        verdict_label=label,
        breaking=len(classified.breaking),
        source_breaks=len(classified.source_breaks),
        risk=len(classified.risk),
        compatible=len(classified.compatible),
        not_evaluated=len(result.not_evaluated),
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
        from .policy.reclassify import active_reclassify_rules

        active = active_reclassify_rules(result.policy_file.reclassify, today)
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
