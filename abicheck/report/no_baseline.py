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

"""The ``compare --no-baseline`` report (ADR-068 D2/D3, plan §6 Phase 2e).

Builds the audit report for a ``--no-baseline`` run directly, rather than
through the legacy ``reporter.to_json`` chokepoint every two-sided
``compare`` report renders through: that whole-report builder is shaped
around a compatibility comparison (an OLD version, a verdict, an
addition/removal/modification summary), none of which a single-build audit
has -- so reusing it would mean padding a two-sided shape out with nulls
and inviting a reader to interpret them. This is the ADR-061 ``report/``
owner's job either way: "Add a report field, report schema, or output
format".

**compute/render split** (``abicheck/report/AGENTS.md``): every format
projects the one frozen :class:`NoBaselineDocument`
:func:`compute_no_baseline_document` builds. The compute half resolves --
per-finding verdict/category, evolution counts, coverage, exit
contributions; the render halves format and decide nothing. A new audit
report section goes in the document, never into one renderer.

**What this reports, now that it reports anything at all.** ADR-068 Phase
2a/2b moved the eleven cross-source hygiene checks and the pattern/
preprocessor pre-scan into ``compare()``, so a self-compared candidate
genuinely produces findings; :mod:`abicheck.policy.no_baseline_findings`
partitions them out of the (provably empty) comparison change set and
``workflows.no_baseline_compare`` hands them here on
``NoBaselineCompareResult.findings``. Every finding carries its ADR-068 D3
evolution state, which for a ``declared_absent`` OLD is always
``persistent`` or ``not_evaluated`` -- never ``introduced``.

**Formats.** ``json``/``markdown``/``sarif``/``junit``/``oneline`` all
render one-sided here. ``html`` and ``review`` deliberately do not -- see
:data:`NO_BASELINE_UNSUPPORTED_FORMATS` for the ruling and where the work
is tracked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..policy.outcome import OperationalStatus, PolicyGateDecision, RunOutcome
from ..policy.scope_completeness import resolve_scope_decision
from .comparison_scope import build_comparison_scope_section
from .cross_source_evolution import (
    change_cross_source_evolution_field,
    compute_cross_source_evolution_summary,
    render_cross_source_evolution_json,
)
from .finding import build_report_findings
from .markdown_text import md_cell
from .no_baseline_document import (
    NO_BASELINE_REPORT_SCHEMA_VERSION as NO_BASELINE_REPORT_SCHEMA_VERSION,
    NO_BASELINE_SUPPORTED_FORMATS as NO_BASELINE_SUPPORTED_FORMATS,
    NO_BASELINE_UNSUPPORTED_FORMATS as NO_BASELINE_UNSUPPORTED_FORMATS,
    NoBaselineDocument as NoBaselineDocument,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..checker_types import Change
    from ..policy.scope_completeness import ScopeDecision
    from ..workflows.no_baseline_compare import NoBaselineCompareResult
    from .finding import ReportFinding

__all__ = [
    "NO_BASELINE_REPORT_SCHEMA_VERSION",
    "NO_BASELINE_SUPPORTED_FORMATS",
    "NO_BASELINE_UNSUPPORTED_FORMATS",
    "NoBaselineDocument",
    "compute_no_baseline_document",
    "no_baseline_exit_code",
    "no_baseline_json_report",
    "no_baseline_markdown_report",
    "render_no_baseline",
    "render_no_baseline_markdown",
    "render_no_baseline_oneline",
]


def _scope_decision(result: NoBaselineCompareResult) -> ScopeDecision:
    return resolve_scope_decision(result.acquisition, policy=None)


def _run_outcome(result: NoBaselineCompareResult) -> RunOutcome:
    """ADR-068 D2: OLD is ``declared_absent``, so this run never carries a
    compatibility contribution -- ``compatibility``/``gate``/``operational``
    read exactly as they would for a report that never ran a real
    comparison at all (:class:`~abicheck.policy.outcome.RunOutcome`'s own
    documented convention), while ``scope`` is decided the same way a
    two-sided run's would be."""
    return RunOutcome(
        compatibility=None,
        assurance=getattr(result.diff, "analysis_assurance", None),
        gate=PolicyGateDecision.NONE,
        operational=_operational_status(result),
        scope=_scope_decision(result).completeness,
    )


def _operational_status(result: NoBaselineCompareResult) -> OperationalStatus:
    """The audit's own operational state, which is not always ``NONE``.

    ``compatibility``/``gate`` genuinely never apply to an audit (ADR-068 D2),
    but *operational* is a different axis: it records a run that never
    produced a real result at all. A pinned ``--depth build``/``--depth
    source`` this run's live extraction did not reach is exactly that, and it
    is already why :func:`no_baseline_exit_code` returns ``7`` -- so reporting
    ``operational: none`` beside that exit code told a structured consumer the
    run was operationally fine when the process said otherwise (Codex review,
    P1). Read off the same flag the exit code folds, so the two cannot
    disagree.
    """
    if getattr(result.diff, "evidence_contract_error", False):
        return OperationalStatus.EVIDENCE_CONTRACT_ERROR
    return OperationalStatus.NONE


def no_baseline_exit_code(
    result: NoBaselineCompareResult, *, require_complete_analysis: bool = False
) -> int:
    """The whole exit-code contribution of a ``--no-baseline`` run.

    The compatibility axis contributes nothing (ADR-068 D2) -- coverage,
    analysis assurance and the evidence-contract axis still apply exactly
    as they would for a two-sided run, and the completeness axis (D6/D7)
    reads ``0`` for the same reason ``no_comparison_completed``/
    ``is_incomplete`` never fire for a ``declared_absent`` member -- all
    folded via the same ``max`` discipline every other orthogonal axis in
    this codebase uses (never an inline ``sys.exit`` computation of its
    own).

    ``evidence_contract_error`` (ADR-064's exit-7 axis, recorded by
    ``policy.depth_evidence_contract.
    record_no_baseline_depth_evidence_contract_error`` when a pinned
    ``--depth build``/``--depth source`` was not reached) is folded here
    rather than in the CLI, so the audit's exit code has exactly one owner
    -- the same reason every other axis above is folded here. It is a plain
    ``max`` member like the rest: exit ``7`` is a *failed evidence
    contract*, orthogonal to how much coverage the run had.
    """
    from ..analysis_assurance import analysis_assurance_exit_contribution
    from ..policy.contract_coverage_exit import coverage_exit_floor
    from ..policy.exit_decision_precedence import EXIT_EVIDENCE_CONTRACT_ERROR

    scope_decision = _scope_decision(result)
    coverage = coverage_exit_floor(result.diff)
    assurance = analysis_assurance_exit_contribution(
        result.diff, require_complete=require_complete_analysis
    )
    evidence_contract = (
        EXIT_EVIDENCE_CONTRACT_ERROR
        if getattr(result.diff, "evidence_contract_error", False)
        else 0
    )
    return max(
        coverage,
        assurance,
        evidence_contract,
        scope_decision.incomplete_scope_exit_contribution,
        scope_decision.no_comparison_completed_exit_contribution,
    )


def _finding_resolver(
    diff: Any,
) -> Callable[[Sequence[Change]], tuple[ReportFinding, ...]]:
    """A resolver bound to *diff*'s policy, for any list of its findings.

    One place reads ``DiffResult._effective_kind_sets()`` -- the report layer
    has no public accessor for it, and every renderer in this package reaches
    for it the same way -- so the reported and suppressed halves are resolved
    against provably identical policy inputs rather than by two call sites
    that must be kept in step.
    """

    def resolve(changes: Sequence[Change]) -> tuple[ReportFinding, ...]:
        return build_report_findings(
            changes,
            policy=diff.policy,
            kind_sets=diff._effective_kind_sets(),  # noqa: SLF001
            policy_file=diff.policy_file,
        )

    return resolve


def compute_no_baseline_document(
    result: NoBaselineCompareResult, *, require_complete_analysis: bool = False
) -> NoBaselineDocument:
    """Resolve *result* into the one document every format below projects."""
    from ..policy.contract_coverage_exit import coverage_exit_floor
    from .pattern_preprocessor_scan import compute_pattern_preprocessor_scan_json

    diff = result.diff
    resolve = _finding_resolver(diff)
    return NoBaselineDocument(
        library=diff.library,
        new_version=diff.new_version,
        old_acquisition_state=result.acquisition.members[0].state.value,
        evidence_tiers=tuple(diff.evidence_tiers),
        findings=resolve(result.findings),
        suppressed=resolve(result.suppressed_findings),
        evolution=compute_cross_source_evolution_summary(result.findings),
        pattern_preprocessor_scan=_candidate_side_scan(
            compute_pattern_preprocessor_scan_json(diff)
        ),
        run_outcome=_run_outcome(result).to_dict(),
        comparison_scope=build_comparison_scope_section(_scope_decision(result)),
        coverage_exit_contribution=coverage_exit_floor(diff),
        exit_code=no_baseline_exit_code(
            result, require_complete_analysis=require_complete_analysis
        ),
    )


def _candidate_side_scan(block: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce the two-sided pattern/preprocessor block to its candidate half.

    ``compare()`` computes this stage per side and folds the two into
    ``old``/``new`` keys. On a self-compare those two are the *same
    snapshot*, so emitting both would invite a reader to compare them and
    conclude a baseline was consulted. Only the ``new`` half is kept, under
    the honest key ``candidate``; the ``*_evolution`` maps are dropped
    entirely rather than emitted all-``persistent`` for the same reason --
    an evolution map states an OLD -> NEW relationship this run has no OLD
    for. ``None`` in, ``None`` out (the stage did not run).
    """
    if block is None:
        return None
    pattern = block.get("pattern") or {}
    preprocessor = block.get("preprocessor") or {}
    return {
        "version": block.get("version"),
        "pattern": {"candidate": pattern.get("new") or {}},
        "preprocessor": {"candidate": preprocessor.get("new") or {}},
    }


def _finding_json(finding: ReportFinding) -> dict[str, Any]:
    """One candidate-side finding's JSON row.

    Deliberately not the two-sided report's own per-``Change`` row: that row
    carries ``old_value``/``new_value`` as a *before and after* pair, which
    is a claim this run cannot make. A cross-source finding's values are two
    evidence sources disagreeing *within* the candidate, so they are named
    for what they are and the evolution state rides alongside.
    """
    change = finding.change
    row: dict[str, Any] = {
        "kind": change.kind.value,
        "symbol": change.symbol,
        "description": change.description,
        "verdict": finding.verdict.value,
        "category": finding.category.value,
        "evolution": change_cross_source_evolution_field(change),
        "candidate_side_enrichment": change.candidate_side_enrichment,
    }
    if change.old_value is not None:
        row["declared_value"] = change.old_value
    if change.new_value is not None:
        row["observed_value"] = change.new_value
    if change.source_location:
        row["source_location"] = change.source_location
    return row


def _suppressed_json(finding: ReportFinding) -> dict[str, Any]:
    """One suppressed finding's JSON row: the finding, plus what hid it.

    ``suppression_rule`` is the reason text the matching rule carried, so a
    reader can answer "which rule, and why" without re-running with the
    suppression file removed -- ADR-067's disposition-audit principle
    applied to this report's own shape.
    """
    row = _finding_json(finding)
    row["disposition"] = "suppressed"
    row["suppression_rule"] = getattr(finding.change, "suppression_rule", None)
    return row


def _document_json(doc: NoBaselineDocument) -> dict[str, Any]:
    return {
        "report_schema_version": NO_BASELINE_REPORT_SCHEMA_VERSION,
        "no_baseline": True,
        "library": doc.library,
        "new_version": doc.new_version,
        "verdict": None,
        "old_acquisition_state": doc.old_acquisition_state,
        # ADR-068 D2: a comparison change set this run never produced. Kept
        # (empty, never omitted) so a consumer that reads `changes` off any
        # abicheck report sees "no comparison findings" rather than a
        # KeyError, while `findings` below carries the audit's own content.
        "changes": [],
        "findings": [_finding_json(f) for f in doc.findings],
        # Never omitted, even when empty: an absent key and "nothing was
        # suppressed" must not look the same to a consumer checking whether
        # policy hid anything (the same convention `contract_coverage_
        # failures` follows -- `[]` rather than omitted).
        "suppressed_findings": [_suppressed_json(f) for f in doc.suppressed],
        "suppressed_count": len(doc.suppressed),
        "cross_source_evolution": render_cross_source_evolution_json(doc.evolution),
        "pattern_preprocessor_scan": doc.pattern_preprocessor_scan,
        "evidence_tiers": list(doc.evidence_tiers),
        "run_outcome": doc.run_outcome,
        "comparison_scope": doc.comparison_scope,
        "contract_coverage_exit_contribution": doc.coverage_exit_contribution,
        "exit_code": doc.exit_code,
    }


def no_baseline_json_report(result: NoBaselineCompareResult) -> dict[str, Any]:
    """The full JSON report for a ``--no-baseline`` audit.

    Deliberately not a projection of the two-sided report's shape padded
    out with nulls: ``no_baseline: true`` marks the shape up front, there is
    no ``old_version``/``old_file`` (OLD was never supplied, not merely
    empty), and ``verdict`` reads as the audit it is.
    """
    return _document_json(compute_no_baseline_document(result))


_EVOLUTION_NOTE = {
    "persistent": "present in this build",
    "not_evaluated": "not evaluated (insufficient evidence)",
}


def render_no_baseline_markdown(doc: NoBaselineDocument) -> str:
    """Markdown projection of *doc*."""
    lines = [
        f"# ABI audit: {doc.library} (no baseline)",
        "",
        "OLD side: **declared absent** (`--no-baseline`) -- this is an audit "
        "of the candidate build alone, not a compatibility comparison. No "
        "additions, removals, or compatibility verdict are reported.",
        "",
        f"- Candidate version: `{doc.new_version or '(unspecified)'}`",
        f"- Acquisition state (OLD): `{doc.old_acquisition_state}`",
        f"- Evidence tiers: {', '.join(doc.evidence_tiers) or '(none recorded)'}",
    ]
    lines += ["", "## Candidate-side findings", ""]
    if not doc.findings:
        lines.append(
            "No cross-source or candidate-side finding on this build. Note that "
            "an evidence-gated check that could not run reports nothing here -- "
            "see `cross_source_evolution.not_evaluated` in the JSON report."
        )
    else:
        lines += [
            "| Finding | Symbol | Severity | State | Detail |",
            "| --- | --- | --- | --- | --- |",
        ]
        for finding in doc.findings:
            change = finding.change
            state = change_cross_source_evolution_field(change) or "candidate-side"
            lines.append(
                f"| `{md_cell(change.kind.value)}` | `{md_cell(change.symbol or '-')}` | "
                f"{md_cell(finding.category.value)} | "
                f"{md_cell(_EVOLUTION_NOTE.get(state, state))} | "
                f"{md_cell(change.description or '')} |"
            )
    if doc.suppressed:
        lines += [
            "",
            "## Suppressed findings",
            "",
            f"{len(doc.suppressed)} finding(s) were detected and then hidden by a "
            "`--suppress` rule. They are listed because a suppressed finding is "
            "a *disposition*, not an absence -- a passing audit must still show "
            "what policy hid, and which rule hid it.",
            "",
            "| Finding | Symbol | Severity | Suppressed by |",
            "| --- | --- | --- | --- |",
        ]
        for finding in doc.suppressed:
            change = finding.change
            rule = getattr(change, "suppression_rule", None) or "(rule gave no reason)"
            lines.append(
                f"| `{md_cell(change.kind.value)}` | `{md_cell(change.symbol or '-')}` | "
                f"{md_cell(finding.category.value)} | {md_cell(rule)} |"
            )
    if doc.coverage_exit_contribution:
        lines += [
            "",
            "> **Contract coverage incomplete** -- the selected `--contract` "
            "domain's required evidence was not fully available on this "
            "candidate (exit contribution 1, ADR-049 Phase 7).",
        ]
    return "\n".join(lines) + "\n"


def no_baseline_markdown_report(result: NoBaselineCompareResult) -> str:
    """The Markdown rendering of a ``--no-baseline`` audit."""
    return render_no_baseline_markdown(compute_no_baseline_document(result))


def render_no_baseline_oneline(doc: NoBaselineDocument) -> str:
    """The one-sentence ``--format oneline`` projection of *doc*."""
    count = len(doc.findings)
    noun = "finding" if count == 1 else "findings"
    # A suppressed finding is named even in the one-line view: "0 findings"
    # on a run that detected and hid three is the exact misreading
    # "Record before disposing" exists to prevent, and this is the view most
    # likely to be the only thing a reader sees.
    suppressed = f", {len(doc.suppressed)} suppressed" if doc.suppressed else ""
    coverage = (
        "; contract coverage incomplete" if doc.coverage_exit_contribution else ""
    )
    return (
        f"{doc.library or '(unnamed)'} audit (no baseline): {count} candidate-side "
        f"{noun}{suppressed}, no compatibility verdict{coverage} "
        f"[exit {doc.exit_code}]\n"
    )


def render_no_baseline(
    result: NoBaselineCompareResult,
    fmt: str,
    *,
    require_complete_analysis: bool = False,
) -> tuple[str, int]:
    """Render a ``--no-baseline`` audit in *fmt*; return ``(text, exit_code)``.

    The one entry point the CLI calls: it computes the document once and
    hands both the rendered text and the already-resolved exit code back,
    so a front end never re-derives either. *fmt* must be in
    :data:`NO_BASELINE_SUPPORTED_FORMATS` -- the CLI rejects anything else
    as a usage error before reaching here, so an unknown value is an
    internal error, not a user one.
    """
    import json as _json

    from .no_baseline_render import (
        render_no_baseline_junit,
        render_no_baseline_sarif,
    )

    doc = compute_no_baseline_document(
        result, require_complete_analysis=require_complete_analysis
    )
    if fmt == "json":
        return _json.dumps(_document_json(doc), indent=2), doc.exit_code
    if fmt == "markdown":
        return render_no_baseline_markdown(doc), doc.exit_code
    if fmt == "sarif":
        return _json.dumps(render_no_baseline_sarif(doc), indent=2), doc.exit_code
    if fmt == "junit":
        return render_no_baseline_junit(doc), doc.exit_code
    if fmt == "oneline":
        return render_no_baseline_oneline(doc), doc.exit_code
    raise ValueError(f"unsupported --no-baseline format: {fmt!r}")
