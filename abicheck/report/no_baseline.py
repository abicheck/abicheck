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

from ..policy.audit_gate_exit import (
    audit_gate_enabled_for_severity_preset as audit_gate_enabled_for_severity_preset,
)
from ..policy.outcome import OperationalStatus, PolicyGateDecision, RunOutcome
from ..policy.scope_completeness import resolve_scope_decision
from .comparison_scope import build_comparison_scope_section
from .cross_source_evolution import (
    change_cross_source_evolution_field,
    compute_cross_source_evolution_summary,
    render_cross_source_evolution_json,
)
from .document import ReportDocument
from .finding import build_report_findings
from .markdown_text import md_cell
from .no_baseline_document import (
    AUDIT_REPORT_SCHEMA_VERSION as AUDIT_REPORT_SCHEMA_VERSION,
    NO_BASELINE_EXIT_AXIS_LABELS as NO_BASELINE_EXIT_AXIS_LABELS,
    NO_BASELINE_EXIT_AXIS_NOTICES as NO_BASELINE_EXIT_AXIS_NOTICES,
    NO_BASELINE_REPORT_SCHEMA_VERSION as NO_BASELINE_REPORT_SCHEMA_VERSION,
    NO_BASELINE_SUPPORTED_FORMATS as NO_BASELINE_SUPPORTED_FORMATS,
    NO_BASELINE_UNSUPPORTED_FORMATS as NO_BASELINE_UNSUPPORTED_FORMATS,
    NoBaselineDocument as NoBaselineDocument,
    # Deliberately not re-exported (`X as X`) like the names above: those are
    # this module's published surface, while this one is an internal detail
    # of how `suppressed` is shaped. A consumer wanting the type imports it
    # from `no_baseline_document`, which owns it.
    SuppressedFinding,
    suppression_provenance_of,
    suppression_rule_label,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..checker_types import Change
    from ..policy.scope_completeness import ScopeDecision
    from ..workflows.no_baseline_compare import NoBaselineCompareResult
    from .finding import ReportFinding

__all__ = [
    "AUDIT_REPORT_SCHEMA_VERSION",
    "NO_BASELINE_REPORT_SCHEMA_VERSION",
    "NO_BASELINE_SUPPORTED_FORMATS",
    "NO_BASELINE_UNSUPPORTED_FORMATS",
    "NoBaselineDocument",
    "audit_gate_enabled_for_severity_preset",
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


#: The audit's orthogonal exit axes, in the order a reader meets them.
#: Rendered as notices by every format and folded into one exit code by
#: :func:`no_baseline_exit_code`, both off :func:`_no_baseline_exit_axes` --
#: so a nonzero exit is always accompanied by the axis that produced it. A
#: Markdown report that stated only the coverage axis exited 7 on a missed
#: evidence contract while saying nothing about why (Codex review, P2).
def _no_baseline_exit_axes(
    result: NoBaselineCompareResult,
    require_complete_analysis: bool,
    audit_gate_enabled: bool = False,
) -> dict[str, int]:
    """Every orthogonal axis's own contribution, keyed as in
    :data:`NO_BASELINE_EXIT_AXIS_NOTICES`.

    One owner, read by both the fold and the renderers, so the number a
    reader is shown and the number that gated them cannot disagree -- the
    same discipline ``contract_coverage_exit`` already applies to its own
    axis. The compatibility axis is absent by construction, not zeroed:
    ADR-068 D2 gives an audit none.
    """
    from ..analysis_assurance import analysis_assurance_exit_contribution
    from ..policy.audit_gate_exit import audit_gate_exit_contribution
    from ..policy.contract_coverage_exit import coverage_exit_floor
    from ..policy.exit_decision_precedence import EXIT_EVIDENCE_CONTRACT_ERROR

    scope_decision = _scope_decision(result)
    return {
        # ADR-068 2026-09-10 amendment: the audit-gate axis, opt-in via
        # --severity-preset (never active unless the run asked for it).
        # Ordered first so it reads alongside the other content axes rather
        # than after the evidence/scope ones below, which is purely a
        # presentation choice -- `max` does not care about dict order.
        #
        # Resolved through `_finding_resolver` (the same policy-effective
        # verdict every renderer reads), never the raw `result.findings`
        # `Change` tuple -- gating on a finding's raw `ChangeKind` category
        # would silently miss a `--policy` override/`reclassify:` rule that
        # promotes it to breaking (Codex security review, P1; see
        # `policy.audit_gate_exit`'s own module docstring).
        "audit_gate": audit_gate_exit_contribution(
            _finding_resolver(result.diff)(result.findings),
            enabled=audit_gate_enabled,
        ),
        "contract_coverage": coverage_exit_floor(result.diff),
        "analysis_assurance": analysis_assurance_exit_contribution(
            result.diff, require_complete=require_complete_analysis
        ),
        "evidence_contract": (
            EXIT_EVIDENCE_CONTRACT_ERROR
            if getattr(result.diff, "evidence_contract_error", False)
            else 0
        ),
        "incomplete_scope": scope_decision.incomplete_scope_exit_contribution,
        "no_comparison_completed": (
            scope_decision.no_comparison_completed_exit_contribution
        ),
    }


def no_baseline_exit_code(
    result: NoBaselineCompareResult,
    *,
    require_complete_analysis: bool = False,
    audit_gate_enabled: bool = False,
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

    ``audit_gate_enabled`` (default ``False``, ADR-068 2026-09-10
    amendment) is the opt-in audit-gate axis: ``False`` for every
    pre-existing caller, so no existing invocation's exit code changes.
    """
    return max(
        _no_baseline_exit_axes(
            result, require_complete_analysis, audit_gate_enabled
        ).values()
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
    result: NoBaselineCompareResult,
    *,
    require_complete_analysis: bool = False,
    audit_gate_enabled: bool = False,
) -> NoBaselineDocument:
    """Resolve *result* into the one document every format below projects."""
    from ..policy.contract_coverage_exit import coverage_exit_floor
    from .disposition_audit import compute_disposition_audit
    from .pattern_preprocessor_scan import compute_pattern_preprocessor_scan_json

    diff = result.diff
    resolve = _finding_resolver(diff)
    return NoBaselineDocument(
        library=diff.library,
        new_version=diff.new_version,
        old_acquisition_state=result.acquisition.members[0].state.value,
        evidence_tiers=tuple(diff.evidence_tiers),
        findings=resolve(result.findings),
        suppressed=_suppressed_entries(diff, resolve(result.suppressed_findings)),
        evolution=compute_cross_source_evolution_summary(result.findings),
        pattern_preprocessor_scan=_candidate_side_scan(
            compute_pattern_preprocessor_scan_json(diff)
        ),
        run_outcome=_run_outcome(result).to_dict(),
        comparison_scope=build_comparison_scope_section(_scope_decision(result)),
        coverage_exit_contribution=coverage_exit_floor(diff),
        coverage_failures=_coverage_failures(diff),
        contract_selected=getattr(diff, "contract_context", None) is not None,
        exit_code=no_baseline_exit_code(
            result,
            require_complete_analysis=require_complete_analysis,
            audit_gate_enabled=audit_gate_enabled,
        ),
        exit_axes=_no_baseline_exit_axes(
            result, require_complete_analysis, audit_gate_enabled
        ),
        policy=diff.policy,
        env_matrix_source_sha256=getattr(diff, "env_matrix_source_sha256", None),
        disposition_audit=compute_disposition_audit(diff).to_dict(),
    )


def _suppressed_entries(
    diff: Any, findings: Sequence[ReportFinding]
) -> tuple[SuppressedFinding, ...]:
    """Pair each suppressed finding with ADR-067 D3's full rule provenance.

    Joined off the run's own ``disposition_ledger`` by object identity
    (``DispositionLedger.rule_for``) -- the same lookup ``reporter.py``'s
    two-sided ``suppression.suppressed_changes`` block uses, so the audit and
    the comparison report name the same rule from one owner rather than two.
    Deliberately not ``Change.suppression_rule``: that is
    ``SuppressionOutcome.rule_label()``'s ``label or reason`` collapse, so a
    rule carrying both dropped its reason, source file and expiry from every
    audit projection (Codex review, P1).

    ``None`` per entry, never a fabricated row, when the ledger holds no
    record for that finding -- a ``DiffResult`` rebuilt from JSON has no
    ledger at all, and inventing provenance there would be worse than
    reporting none.
    """
    ledger = getattr(diff, "disposition_ledger", None)
    return tuple(
        SuppressedFinding(
            change=finding.change,
            verdict=finding.verdict,
            category=finding.category,
            provenance=_provenance_row(ledger, finding.change),
        )
        for finding in findings
    )


def _provenance_row(ledger: Any, change: Any) -> Mapping[str, Any] | None:
    if ledger is None:
        return None
    rule = ledger.rule_for(change)
    return None if rule is None else rule.to_dict()


def _coverage_failures(diff: Any) -> tuple[dict[str, Any], ...]:
    """The audit's contract-coverage ledger, already serialized.

    Derived from the run's own persisted contract context by the same
    function the two-sided report uses (``policy.coverage_ledger.
    coverage_failures_for_context``), rather than re-derived here -- so the
    failures a reader is shown are the ones the exit contribution beside them
    was computed from. Empty when no contract context exists, which is every
    run without ``--contract``.
    """
    from ..policy.coverage_ledger import coverage_failures_for_context

    ctx = getattr(diff, "contract_context", None)
    if ctx is None:
        return ()
    return tuple(f.to_dict() for f in coverage_failures_for_context(ctx))


def _candidate_side_scan(block: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce the two-sided pattern/preprocessor block to its candidate half.

    ``compare()`` computes this stage per side and folds the two into
    ``old``/``new`` keys. On a self-compare those two are the *same
    snapshot*, so emitting both would invite a reader to compare them and
    conclude a baseline was consulted. Only the ``new`` half is kept, under
    the honest key ``candidate``; the ``*_evolution`` maps are dropped
    entirely rather than emitted all-``persistent`` for the same reason --
    an evolution map states an OLD -> NEW relationship this run has no OLD
    for. ``coverage`` *is* kept, reduced to the candidate the same way: it
    states how completely this side's own evidence was read, which is a
    one-sided fact and the one thing a reader needs in order to tell "the
    candidate has none of these constructs" from "we could not look".
    ``None`` in, ``None`` out (the stage did not run).
    """
    if block is None:
        return None
    pattern = block.get("pattern") or {}
    preprocessor = block.get("preprocessor") or {}
    coverage = block.get("coverage") or {}
    return {
        "version": block.get("version"),
        "pattern": {"candidate": pattern.get("new") or {}},
        "preprocessor": {"candidate": preprocessor.get("new") or {}},
        # Per-check sufficiency is kept, reduced to the candidate the same way
        # everything else here is. Dropping it would leave an audit unable to
        # say whether its own facts rest on complete evidence -- and this stage
        # is exactly where "found nothing" and "could not look" must not read
        # alike (Codex review, P2). Unlike the evolution maps, this is not an
        # OLD -> NEW claim, so there is nothing about it an audit cannot state.
        "coverage": {
            check: {"candidate": sides.get("new") or {}}
            for check, sides in coverage.items()
        },
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


def _suppressed_json(entry: SuppressedFinding) -> dict[str, Any]:
    """One suppressed finding's JSON row: the finding, plus what hid it.

    Two fields, because they answer different questions and one cannot stand
    in for the other:

    * ``suppression_rule`` -- the rule's short display label
      (``SuppressionOutcome.rule_label()``'s ``label or reason``), kept
      unchanged so an existing consumer reading it is unaffected.
    * ``suppression_provenance`` -- ADR-067 D3's full record: rule id,
      source file, reason, label, expiry. The display label collapses
      ``label`` and ``reason`` into one string, so a rule stating both
      published its label and silently dropped the reason and the file it
      came from -- exactly the identify-and-review information the
      disposition audit exists to preserve (Codex review, P1). ``null``
      when the run kept no ledger entry for this finding.
    """
    row = _finding_json(entry)
    row["disposition"] = "suppressed"
    row["suppression_rule"] = getattr(entry.change, "suppression_rule", None)
    provenance = suppression_provenance_of(entry)
    row["suppression_provenance"] = dict(provenance) if provenance else None
    return row


def _document_json(doc: NoBaselineDocument) -> dict[str, Any]:
    return {
        # The audit's *own* namespace, deliberately not
        # `report_schema_version`: that field belongs to the compare
        # report, whose schema tells consumers to accept any matching
        # MAJOR -- so an audit stamped there is a different document
        # wearing the compare report's identity (Codex review, P1).
        "audit_report_schema_version": AUDIT_REPORT_SCHEMA_VERSION,
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
        "suppressed_findings": [_suppressed_json(entry) for entry in doc.suppressed],
        "suppressed_count": len(doc.suppressed),
        "cross_source_evolution": render_cross_source_evolution_json(doc.evolution),
        "pattern_preprocessor_scan": doc.pattern_preprocessor_scan,
        "evidence_tiers": list(doc.evidence_tiers),
        "run_outcome": doc.run_outcome,
        "comparison_scope": doc.comparison_scope,
        "contract_coverage_exit_contribution": doc.coverage_exit_contribution,
        # Every orthogonal axis's own contribution, never omitted: the exit
        # code above is a `max` over these, so publishing only the total
        # leaves a gated consumer unable to tell *which* axis gated them --
        # the same reason the Markdown projection renders a notice per
        # contributing axis (Codex review, P2). `contract_coverage_exit_
        # contribution` above stays as its own long-standing key rather than
        # being folded away, so an existing consumer is unaffected.
        # Emitted only under a contract, and then always -- `[]` is the real
        # "this domain closed", which an absent key could not distinguish
        # from "no contract was selected". Same convention as the two-sided
        # report's own block.
        **(
            {"contract_coverage_failures": [dict(f) for f in doc.coverage_failures]}
            if doc.contract_selected
            else {}
        ),
        "exit_axes": dict(doc.exit_axes),
        "exit_code": doc.exit_code,
        "policy": doc.policy,
        # Codex review, P2: the declared-deployment-floor contract's content
        # digest -- omitted, not `null`, when this audit's candidate declared
        # no `deployment.runtime_floors`/`EnvironmentMatrix` contract at all,
        # the same additive convention `reporter._add_env_matrix_digest`
        # follows for the two-sided compare report under this identical key.
        **(
            {"env_matrix_source_sha256": doc.env_matrix_source_sha256}
            if doc.env_matrix_source_sha256 is not None
            else {}
        ),
        # ADR-067 C-S2's raw-versus-effective ledger, at the same root key
        # every two-sided `compare` report carries it under -- so a reader
        # (including `abicheck aggregate`'s own generic
        # `disposition_audit_block` fold-in) does not need a shape-specific
        # path to find it. Never omitted: `None` only for a hand-built
        # document a test constructs directly.
        "disposition_audit": doc.disposition_audit,
    }


def no_baseline_report_document(doc: NoBaselineDocument) -> ReportDocument:
    """The audit as a canonical :class:`~abicheck.report.document.ReportDocument`.

    ADR-061 Phase 2's boundary: a completed report a renderer cannot change.
    :class:`NoBaselineDocument` is the audit's *compute* half -- typed,
    resolved, with no ``DiffResult`` or live policy object left on it, the
    same role ``ReportFinding`` and HTML's own frozen section structs play --
    and this is where it crosses into the shared document type, so the
    audit's structured output participates in that boundary rather than
    running beside it (Codex review, P1). ``from_mapping`` takes a defensive
    immutable snapshot, so a caller holding the result cannot mutate what
    was rendered.

    One residual, recorded rather than implied: the SARIF and JUnit
    projections still read the typed document directly instead of this
    frozen mapping. See ``docs/contribute/known-gaps.md``.
    """
    return ReportDocument.from_mapping(_document_json(doc))


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
    # Codex review, P2: the same declared-deployment-floor digest the JSON
    # projection carries under `env_matrix_source_sha256` -- omitted
    # entirely (not a "(none)" placeholder line) when no `deployment:`
    # contract governed this run, matching the JSON convention's own
    # additive omission and keeping a plain run's Markdown unchanged.
    if doc.env_matrix_source_sha256 is not None:
        lines.append(f"- Deployment floor digest: `{doc.env_matrix_source_sha256}`")
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
            "| Finding | Symbol | Severity | Suppressed by | Reason | Source | Expires |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for entry in doc.suppressed:
            finding = entry
            change = entry.change
            # A reader deciding whether the waiver still applies needs the
            # reason it was written for, the file it lives in, and when it
            # lapses -- so those get their own columns beside the label.
            #
            # The label comes from the shared resolver, never from
            # `Change.suppression_rule` directly: that field is
            # `label or reason`, so reading it here printed a reason-only
            # rule's reason twice, once under a heading claiming it was a
            # separate rule label (Codex review, P2).
            entry_provenance = suppression_provenance_of(entry)
            rule = suppression_rule_label(change, entry_provenance) or (
                "(rule gave no label)"
            )
            prov = entry_provenance or {}
            reason = prov.get("reason") or "(none stated)"
            source = prov.get("source_file") or "(not recorded)"
            expires = prov.get("expires") or "(never)"
            lines.append(
                f"| `{md_cell(change.kind.value)}` | `{md_cell(change.symbol or '-')}` | "
                f"{md_cell(finding.category.value)} | {md_cell(rule)} | "
                f"{md_cell(str(reason))} | `{md_cell(str(source))}` | "
                f"{md_cell(str(expires))} |"
            )
    lines += _exit_axis_notice_lines(doc)
    return "\n".join(lines) + "\n"


def _exit_axis_notice_lines(doc: NoBaselineDocument) -> list[str]:
    """One Markdown blockquote per axis that actually contributed.

    Every contributing axis, not just contract coverage: an audit's exit
    code is a `max` over several orthogonal axes, and a report that names
    one of them leaves a reader who was gated by another with no
    explanation at all (Codex review, P2). Reads the resolved
    ``doc.exit_axes`` rather than re-deriving anything, so what is
    explained is exactly what was folded. Nothing is emitted on a clean
    run, and the notices carry no numbers of their own -- the exit code is
    stated once, by the process.
    """
    contributing = [
        NO_BASELINE_EXIT_AXIS_NOTICES[key]
        for key, value in doc.exit_axes.items()
        if value and key in NO_BASELINE_EXIT_AXIS_NOTICES
    ]
    if not contributing:
        return []
    return ["", *(f"> {notice}" for notice in contributing)]


def no_baseline_markdown_report(result: NoBaselineCompareResult) -> str:
    """The Markdown rendering of a ``--no-baseline`` audit."""
    return render_no_baseline_markdown(compute_no_baseline_document(result))


def render_no_baseline_oneline(doc: NoBaselineDocument) -> str:
    """The one-sentence ``-o oneline=...`` projection of *doc*."""
    count = len(doc.findings)
    noun = "finding" if count == 1 else "findings"
    # A suppressed finding is named even in the one-line view: "0 findings"
    # on a run that detected and hid three is the exact misreading
    # "Record before disposing" exists to prevent, and this is the view most
    # likely to be the only thing a reader sees.
    suppressed = f", {len(doc.suppressed)} suppressed" if doc.suppressed else ""
    # Every contributing axis, not just contract coverage. A bare `[exit 7]`
    # tells a reader they were gated and nothing about why, and this is the
    # view most likely to be the only thing they see.
    contributing = [
        NO_BASELINE_EXIT_AXIS_LABELS[key]
        for key in NO_BASELINE_EXIT_AXIS_LABELS
        if doc.exit_axes.get(key)
    ]
    axes = f"; {', '.join(contributing)}" if contributing else ""
    # Codex review, P2: same digest the JSON/Markdown projections carry,
    # omitted (not a placeholder) when no `deployment:` contract governed
    # this run -- a within-floor audit must not read the same as a run with
    # no deployment contract at all in the one view most likely to be the
    # only thing a reader sees.
    deployment = (
        f"; deployment floor {doc.env_matrix_source_sha256}"
        if doc.env_matrix_source_sha256 is not None
        else ""
    )
    return (
        f"{doc.library or '(unnamed)'} audit (no baseline): {count} candidate-side "
        f"{noun}{suppressed}, no compatibility verdict{axes}{deployment} "
        f"[exit {doc.exit_code}]\n"
    )


def render_no_baseline(
    result: NoBaselineCompareResult,
    fmt: str,
    *,
    require_complete_analysis: bool = False,
    audit_gate_enabled: bool = False,
) -> tuple[str, int]:
    """Render a ``--no-baseline`` audit in *fmt*; return ``(text, exit_code)``.

    The one entry point the CLI calls: it computes the document once and
    hands both the rendered text and the already-resolved exit code back,
    so a front end never re-derives either. *fmt* must be in
    :data:`NO_BASELINE_SUPPORTED_FORMATS` -- the CLI rejects anything else
    as a usage error before reaching here, so an unknown value is an
    internal error, not a user one.

    *audit_gate_enabled* (default ``False``, ADR-068 2026-09-10 amendment)
    threads the opt-in audit-gate axis through to the exit code -- see
    :func:`abicheck.policy.audit_gate_exit.audit_gate_enabled_for_severity_preset`
    for how the CLI derives it from ``--severity-preset``.
    """
    import json as _json

    from .no_baseline_render import (
        render_no_baseline_junit,
        render_no_baseline_sarif,
    )

    doc = compute_no_baseline_document(
        result,
        require_complete_analysis=require_complete_analysis,
        audit_gate_enabled=audit_gate_enabled,
    )
    if fmt == "json":
        return (
            _json.dumps(no_baseline_report_document(doc).to_mapping(), indent=2),
            doc.exit_code,
        )
    if fmt == "markdown":
        return render_no_baseline_markdown(doc), doc.exit_code
    if fmt == "sarif":
        return _json.dumps(render_no_baseline_sarif(doc), indent=2), doc.exit_code
    if fmt == "junit":
        return render_no_baseline_junit(doc), doc.exit_code
    if fmt == "oneline":
        return render_no_baseline_oneline(doc), doc.exit_code
    raise ValueError(f"unsupported --no-baseline format: {fmt!r}")
