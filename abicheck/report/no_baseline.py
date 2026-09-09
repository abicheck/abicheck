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

import xml.etree.ElementTree as ET
from dataclasses import dataclass
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

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..checker_types import Change
    from ..policy.scope_completeness import ScopeDecision
    from ..workflows.no_baseline_compare import NoBaselineCompareResult
    from .cross_source_evolution import CrossSourceEvolutionSummary
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
    "render_no_baseline_junit",
    "render_no_baseline_markdown",
    "render_no_baseline_oneline",
    "render_no_baseline_sarif",
]

#: Independent of ``reporter.REPORT_SCHEMA_VERSION`` (the two-sided report's
#: own schema) -- this report carries a different shape (no ``old_version``,
#: no ``verdict``, no addition/removal summary), so it gets its own counter
#: rather than borrowing one that promises a shape this report doesn't have.
#:
#: ``2.0``: the audit's cross-source/candidate-side findings reach the
#: document (``findings``, ``suppressed_findings``, ``suppressed_count``,
#: ``cross_source_evolution``, ``pattern_preprocessor_scan``). ``1.0``
#: always emitted ``"changes": []``, which was the gap, not the schema's
#: intent. ``suppressed_findings`` landed in the same unreleased ``2.0``
#: rather than as a ``2.1``: a suppressed finding disappearing entirely was
#: a defect in this shape, not a later addition to a shipped one, and
#: version numbers exist to warn consumers of a *published* change.
NO_BASELINE_REPORT_SCHEMA_VERSION = "2.0"

#: Formats a ``--no-baseline`` audit renders one-sided.
#:
#: ``json``/``markdown`` are the audit's own native shapes. ``sarif`` and
#: ``junit`` were added once the audit had a real finding set to carry:
#: both are *findings* formats with no verdict slot to leave empty (a SARIF
#: run is a list of results with rule ids and levels; a JUnit suite is a
#: list of test cases), which is exactly what a single-build audit
#: produces, and both are how a CI job consumes one -- code scanning upload
#: and test-report annotation respectively. ``oneline`` is the "just tell
#: me" flow and needs one sentence.
NO_BASELINE_SUPPORTED_FORMATS = frozenset(
    {"json", "markdown", "sarif", "junit", "oneline"}
)

#: Formats that stay a declared usage error, and why (ADR-068 D2 ruling,
#: 2026-09-09 -- recorded here rather than left undated in a plan).
#:
#: Both are *narrative* renderings built around a compatibility comparison,
#: not projections of a finding list:
#:
#: * ``html`` -- the HTML report's whole information architecture is a
#:   verdict badge, an OLD -> NEW version headline, and
#:   addition/removal/modification tables. A single-build audit has no
#:   verdict (D2 forbids one), no OLD version, and no additions or
#:   removals, so a one-sided HTML page is a *new page design* rather than
#:   a projection of the document below -- genuinely different work from
#:   the four formats above, none of which needed a layout decision.
#: * ``review`` -- a compact GitHub-facing digest whose content is
#:   literally "what changed between these two releases, and should you
#:   ship it": verdict, counts by direction, release recommendation,
#:   manual-review banner. With no baseline there is no change to review
#:   and no release to recommend; the honest digest is ``oneline``, which
#:   is supported.
#:
#: Tracked in ``docs/contribute/plans/one-comparison-product.md`` (Phase 2e
#: follow-up) and ``docs/contribute/known-gaps.md``. Not a silent omission:
#: the CLI's usage error names this ruling.
NO_BASELINE_UNSUPPORTED_FORMATS = frozenset({"html", "review"})


@dataclass(frozen=True)
class NoBaselineDocument:
    """The one frozen, plain-value audit document every format projects.

    Holds resolved values only -- no ``DiffResult``, no live policy
    objects -- so a renderer cannot re-derive a verdict or reach past what
    the compute half decided.
    """

    library: str
    new_version: str
    #: OLD's acquisition state, always ``"declared_absent"`` today.
    old_acquisition_state: str
    evidence_tiers: tuple[str, ...]
    #: One entry per candidate-side finding, in emission order.
    findings: tuple[ReportFinding, ...]
    #: The findings a ``--suppress`` rule matched, same resolution, kept
    #: alongside rather than dropped: ``vision.md``'s "Record before
    #: disposing" rule requires "detected, then suppressed by rule X" to
    #: stay visible on a passing run, never to read as "nothing found".
    suppressed: tuple[ReportFinding, ...]
    evolution: CrossSourceEvolutionSummary | None
    pattern_preprocessor_scan: dict[str, Any] | None
    run_outcome: dict[str, Any]
    comparison_scope: dict[str, Any]
    #: ADR-049 Phase 7's orthogonal coverage contribution, and the total
    #: exit code this run reports. Both resolved compute-side so no
    #: renderer computes an exit code of its own.
    coverage_exit_contribution: int
    exit_code: int


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
        operational=OperationalStatus.NONE,
        scope=_scope_decision(result).completeness,
    )


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
                f"| `{change.kind.value}` | `{change.symbol or '-'}` | "
                f"{finding.category.value} | {_EVOLUTION_NOTE.get(state, state)} | "
                f"{change.description or ''} |"
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
                f"| `{change.kind.value}` | `{change.symbol or '-'}` | "
                f"{finding.category.value} | {rule} |"
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


#: The SARIF 2.1.0 schema this document declares. Named rather than inlined
#: only so the literal does not force a 116-column line.
_SARIF_SCHEMA_URL = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master"
    "/Schemata/sarif-schema-2.1.0.json"
)


def _sarif_result(
    finding: ReportFinding, *, rule_id: str, suppressed: bool
) -> dict[str, Any]:
    """One SARIF ``result`` for a candidate-side finding.

    Split out of :func:`render_no_baseline_sarif` so the envelope there reads
    as the run it describes, and the per-finding shape sits next to its JUnit
    counterpart (:func:`_junit_finding_case`) rather than buried in a loop.
    """
    from ..sarif import _parse_source_location

    change = finding.change
    entry: dict[str, Any] = {
        "ruleId": rule_id,
        "level": _SEVERITY_TO_SARIF_LEVEL.get(finding.category.value, "warning"),
        "message": {
            "text": change.description or f"{change.kind.value}: {change.symbol or ''}"
        },
        "properties": {
            "noBaseline": True,
            "crossSourceEvolution": change_cross_source_evolution_field(change),
            "candidateSideEnrichment": change.candidate_side_enrichment,
            "symbol": change.symbol,
        },
    }
    if suppressed:
        entry["suppressions"] = [
            {
                "kind": "external",
                "justification": getattr(change, "suppression_rule", None)
                or "suppressed by an abicheck --suppress rule",
            }
        ]
    if change.source_location:
        uri, line, column = _parse_source_location(change.source_location)
        region: dict[str, Any] = {}
        if line is not None:
            region["startLine"] = line
        if column is not None:
            region["startColumn"] = column
        physical: dict[str, Any] = {"artifactLocation": {"uri": uri}}
        if region:
            physical["region"] = region
        entry["locations"] = [{"physicalLocation": physical}]
    return entry


def render_no_baseline_sarif(doc: NoBaselineDocument) -> dict[str, Any]:
    """SARIF 2.1.0 projection of *doc*.

    Built here rather than through ``sarif.to_sarif`` because that function
    frames a run around a compatibility verdict (its ``exitCode``/
    ``exitCodeDescription`` are derived from ``DiffResult.verdict``, which
    on a self-compare reads ``NO_CHANGE`` -- a compatibility claim ADR-068
    D2 forbids this run from making). The *per-finding* metadata is reused
    rather than reinvented, though: ``_rule_for``/``_severity`` are
    imported from that same module, so a rule id, help URI and level mean
    exactly what they mean in a two-sided SARIF document and a code-scanning
    consumer needs no special case.
    """
    from ..sarif import _rule_for, _tool_version

    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    # A suppressed finding is emitted as a real result carrying SARIF's own
    # `suppressions` array -- the format's native way to say "found, then
    # dispositioned" -- rather than dropped. A code-scanning consumer then
    # shows it as suppressed instead of never learning it existed
    # (``vision.md``'s "Record before disposing"; Codex review, P1).
    for finding, suppressed in [(f, False) for f in doc.findings] + [
        (f, True) for f in doc.suppressed
    ]:
        rule = _rule_for(finding.change.kind)
        rules.setdefault(rule["id"], rule)
        results.append(
            _sarif_result(finding, rule_id=rule["id"], suppressed=suppressed)
        )
    return {
        "$schema": _SARIF_SCHEMA_URL,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "abicheck",
                        "version": _tool_version(),
                        "informationUri": "https://github.com/abicheck/abicheck",
                        "rules": list(rules.values()),
                    }
                },
                "invocations": [
                    {
                        # Per the SARIF spec this reports whether the tool ran
                        # to completion, not whether it found anything -- an
                        # audit that completed is successful regardless of how
                        # many hygiene findings it reports.
                        "executionSuccessful": True,
                        "exitCode": doc.exit_code,
                        "exitCodeDescription": (
                            "single-build audit (--no-baseline): no compatibility "
                            "verdict is reported"
                        ),
                    }
                ],
                "results": results,
                "properties": {
                    "noBaseline": True,
                    "library": doc.library,
                    "candidateVersion": doc.new_version,
                    "oldAcquisitionState": doc.old_acquisition_state,
                    "evidenceTiers": list(doc.evidence_tiers),
                    "contractCoverageExitContribution": doc.coverage_exit_contribution,
                },
            }
        ],
    }


#: ``IssueCategory`` value -> SARIF level for a one-sided audit finding.
#: ADR-028 D3/ADR-035 D1 keep every cross-source finding advisory
#: (``RISK``/``API_BREAK``, never ``BREAKING``), so nothing here maps to
#: ``error`` on its own -- promoting one is a ``--policy`` override's job,
#: which ``build_report_findings`` has already applied by the time this is
#: read.
_SEVERITY_TO_SARIF_LEVEL = {
    "abi_breaking": "error",
    "potential_breaking": "warning",
    "quality_issues": "warning",
    "addition": "note",
}


def render_no_baseline_junit(doc: NoBaselineDocument) -> str:
    """JUnit XML projection of *doc*.

    **A finding is never a ``<failure>`` here.** An audit's cross-source
    hygiene findings are advisory by construction (ADR-028 D3 / ADR-035 D1:
    they stay ``RISK``/``API_BREAK`` and never become ``BREAKING``), and
    ADR-068 D2 gives this run no compatibility contribution at all -- so
    they contribute nothing to the exit code, and a run reporting several
    of them still exits ``0``. Emitting a ``<failure>`` per finding made the
    JUnit file fail a build the CLI said passed, which is precisely the bug
    ``junit_report._is_failure`` records having already been fixed once for
    the two-sided report ("reporting one ``<failure>`` beside a
    ``NO_CHANGE`` verdict and a clean exit was the bug"). Each finding
    instead gets its own **passing** ``<testcase>``, carrying its severity
    and evolution as properties, because D9 requires the fact to stay
    visible -- it just is not a failure.

    What *can* fail is the run itself: a single ``exit code`` testcase
    fails when, and only when, one of the audit's orthogonal axes actually
    gated the run (contract coverage, analysis assurance, the evidence
    contract). So the suite's failure count and the process exit code agree
    by construction rather than by coincidence -- the invariant
    ``tests/test_no_baseline_report_formats.py`` pins.

    Built here rather than through ``junit_report.to_junit_xml`` for the
    same reason as SARIF above: that builder partitions its suite by
    compatibility verdict and emits a verdict property block this run must
    not claim.
    """
    gate_failed = doc.exit_code != 0
    cases = len(doc.findings) + len(doc.suppressed) + 1
    suite = ET.Element(
        "testsuite",
        {
            "name": f"abicheck audit: {doc.library}",
            "tests": str(cases),
            "failures": "1" if gate_failed else "0",
            "errors": "0",
            "skipped": str(len(doc.suppressed)),
        },
    )
    props = ET.SubElement(suite, "properties")
    for name, value in (
        ("no_baseline", "true"),
        ("library", doc.library),
        ("candidate_version", doc.new_version),
        ("old_acquisition_state", doc.old_acquisition_state),
        ("evidence_tiers", ",".join(doc.evidence_tiers)),
        ("findings", str(len(doc.findings))),
        ("suppressed_findings", str(len(doc.suppressed))),
        ("contract_coverage_exit_contribution", str(doc.coverage_exit_contribution)),
        ("exit_code", str(doc.exit_code)),
    ):
        ET.SubElement(props, "property", {"name": name, "value": value or ""})

    for finding in doc.findings:
        _junit_finding_case(suite, finding, suppressed=False)
    for finding in doc.suppressed:
        # `<skipped>`, not a silent omission and not a failure: SARIF has a
        # `suppressions` array for this and JUnit's nearest honest
        # equivalent is a skipped case -- the finding is reported, and its
        # disposition is legible, without claiming it broke anything.
        _junit_finding_case(suite, finding, suppressed=True)

    gate = ET.SubElement(
        suite,
        "testcase",
        {"classname": "abicheck.audit", "name": "exit code"},
    )
    if gate_failed:
        failure = ET.SubElement(
            gate,
            "failure",
            {
                "type": "audit_gate",
                "message": f"audit exited {doc.exit_code}",
            },
        )
        failure.text = (
            f"exit code: {doc.exit_code}\n"
            f"contract coverage contribution: {doc.coverage_exit_contribution}\n"
            "note: a candidate-side hygiene finding never gates on its own "
            "(ADR-028 D3 / ADR-035 D1); this is one of the orthogonal axes "
            "(contract coverage, analysis assurance, evidence contract)."
        )
    ET.indent(suite, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        suite, encoding="unicode"
    )


def _junit_finding_case(
    suite: ET.Element, finding: ReportFinding, *, suppressed: bool
) -> None:
    """One passing (or skipped) ``<testcase>`` for a candidate-side finding."""
    change = finding.change
    case = ET.SubElement(
        suite,
        "testcase",
        {
            "classname": f"abicheck.audit.{change.kind.value}",
            "name": change.symbol or change.kind.value,
        },
    )
    state = change_cross_source_evolution_field(change) or "candidate-side"
    detail = (
        f"{change.description or change.kind.value}\n"
        f"severity: {finding.category.value}\n"
        f"verdict: {finding.verdict.value}\n"
        f"evolution: {state}\n"
        "note: single-build audit (--no-baseline); no baseline was consulted, "
        "and a hygiene finding is advisory -- it does not gate."
    )
    if suppressed:
        rule = getattr(change, "suppression_rule", None)
        skipped = ET.SubElement(
            case,
            "skipped",
            {"message": f"suppressed: {rule or 'a --suppress rule matched'}"},
        )
        skipped.text = detail
    else:
        ET.SubElement(case, "system-out").text = detail


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
