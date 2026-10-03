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

"""The ``compare --no-baseline DIR`` report: the ``audit_set`` envelope
(one-comparison-product F-23, ADR-068 D2).

One document per run, holding one *scalar* audit document per member --
each member's ``report`` is, verbatim, what ``compare --no-baseline
<that member>`` emits (:mod:`abicheck.report.no_baseline`) -- plus what only
a set has: the per-member acquisition states, the ``comparison_scope``
section (ADR-065's completeness axis), and one exit code folded over every
member.

**Compute/render split** (``report/AGENTS.md``):
:func:`compute_no_baseline_set_document` resolves everything once -- each
member's own scalar document, the scope decision, the folded exit axes --
into a frozen :class:`NoBaselineSetDocument`; the JSON, Markdown and
one-line renderers below format that and decide nothing.

**What it never says.** OLD is declared absent for every member, so the
envelope carries ``verdict: null``, ``old_acquisition_state:
"declared_absent"``, an always-empty ``changes`` list, and no member is ever
reported added, removed, introduced or resolved.

**Formats.** ``json``, ``markdown`` and ``oneline``.
:data:`NO_BASELINE_SET_UNSUPPORTED_FORMATS` states why each other format is
a usage error rather than a lossy projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..policy.outcome import OperationalStatus, PolicyGateDecision, RunOutcome
from ..policy.scope_completeness import resolve_scope_decision
from .comparison_scope import build_comparison_scope_section, comparison_scope_notice
from .markdown_text import md_cell
from .no_baseline import (
    compute_no_baseline_document,
    no_baseline_report_document,
    render_no_baseline_markdown,
)
from .no_baseline_document import (
    AUDIT_SET_REPORT_SCHEMA_VERSION,
    NO_BASELINE_EXIT_AXIS_LABELS,
    NO_BASELINE_EXIT_AXIS_NOTICES,
    NoBaselineDocument,
)

if TYPE_CHECKING:
    from ..workflows.no_baseline_set import NoBaselineSetResult

__all__ = [
    "AUDIT_SET_REPORT_SCHEMA_VERSION",
    "AUDIT_SET_EXIT_AXIS_LABELS",
    "MEMBER_EXIT_AXES",
    "NO_BASELINE_SET_SUPPORTED_FORMATS",
    "NO_BASELINE_SET_UNSUPPORTED_FORMATS",
    "OPERATIONAL_ERROR_EXIT",
    "AuditSetMember",
    "NoBaselineSetDocument",
    "compute_no_baseline_set_document",
    "no_baseline_set_json",
    "render_no_baseline_set",
    "render_no_baseline_set_markdown",
    "render_no_baseline_set_oneline",
]


#: The formats an N-library audit renders.
NO_BASELINE_SET_SUPPORTED_FORMATS = frozenset({"json", "markdown", "oneline"})

#: Every other ``compare`` format, and why it is a usage error here.
NO_BASELINE_SET_UNSUPPORTED_FORMATS: dict[str, str] = {
    "sarif": (
        "a SARIF log could carry each member's findings as its own run, but "
        "the set's own facts -- which members were not audited, the "
        "completeness and operational-error axes behind the exit code -- have "
        "no SARIF slot, so the log would read as complete when the run was not"
    ),
    "junit": (
        "the set's acquisition states and folded exit axes have no JUnit "
        "shape a CI test report would surface; audit the members one at a "
        "time for a per-library JUnit report"
    ),
    "html": "it renders a compatibility comparison, and an audit has none",
    "review": "it renders a compatibility comparison, and an audit has none",
    "terminal": "it is the bounded projection of one completed comparison",
}

#: The operational-error axis's contribution: the same ``4`` the two-sided
#: release path folds for a member whose comparison failed with an
#: unexpected error (``policy.release_exit_decision``'s
#: ``operational_error_contribution``), so one failed member reads the same
#: way whichever command ran it.
OPERATIONAL_ERROR_EXIT = 4

#: The axes each member's own scalar document resolves and the set folds
#: with ``max``. The scope axes are deliberately *not* among them: a
#: member's own one-member record always reads complete, and the set's
#: completeness is decided over the set's record instead.
MEMBER_EXIT_AXES: tuple[str, ...] = (
    "audit_gate",
    "contract_coverage",
    "analysis_assurance",
    "evidence_contract",
)

#: Short phrases per set-level axis -- the scalar labels plus the one axis
#: only a set has.
AUDIT_SET_EXIT_AXIS_LABELS: dict[str, str] = {
    **{key: NO_BASELINE_EXIT_AXIS_LABELS[key] for key in MEMBER_EXIT_AXES},
    "operational_error": "member audit failed",
    "incomplete_scope": NO_BASELINE_EXIT_AXIS_LABELS["incomplete_scope"],
    "no_comparison_completed": NO_BASELINE_EXIT_AXIS_LABELS["no_comparison_completed"],
}

_AUDIT_SET_EXIT_AXIS_NOTICES: dict[str, str] = {
    **{
        key: NO_BASELINE_EXIT_AXIS_NOTICES[key]
        for key in AUDIT_SET_EXIT_AXIS_LABELS
        if key in NO_BASELINE_EXIT_AXIS_NOTICES
    },
    "operational_error": (
        "**Member audit failed** -- at least one selected member could not be "
        "audited (an extraction or analysis error, listed with its reason "
        "above); the run contributes the same operational-error exit a "
        "directory/package `compare` uses for a failed member."
    ),
    "no_comparison_completed": (
        "**No audit completed** -- no selected member reached a completed "
        "audit, which never reads as a clean pass."
    ),
}

_STATE_LABEL = {
    "declared_absent": "audited",
    "expected_not_produced": "expected, not produced",
    "failed": "failed",
    "unsupported": "unsupported",
    "out_of_scope": "out of scope",
}


@dataclass(frozen=True)
class AuditSetMember:
    """One member of the set, as reported: its acquisition, and -- when it
    was audited -- its own scalar audit document."""

    member: str
    display_name: str
    acquisition_state: str
    required: bool
    reason: str
    document: NoBaselineDocument | None
    #: The member's scalar JSON document, exactly as ``compare --no-baseline
    #: <member> -o json=...`` would emit it; ``None`` when not audited.
    report: Mapping[str, Any] | None
    error_type: str = ""


@dataclass(frozen=True)
class NoBaselineSetDocument:
    """The one frozen ``audit_set`` document every format projects."""

    library: str
    operand_kind: str
    new_version: str
    members: tuple[AuditSetMember, ...]
    comparison_scope: Mapping[str, Any]
    exit_axes: Mapping[str, int]
    exit_code: int
    run_outcome: Mapping[str, Any]
    policy: str
    disposition_audit: Mapping[str, Any]
    warnings: tuple[str, ...] = ()

    @property
    def audited(self) -> tuple[AuditSetMember, ...]:
        """The members with a completed audit document."""
        return tuple(m for m in self.members if m.document is not None)

    def findings_rows(self, key: str) -> list[dict[str, Any]]:
        """Every member's ``findings``/``suppressed_findings`` rows, each
        tagged with the ``member`` it came from, in member order."""
        rows: list[dict[str, Any]] = []
        for m in self.members:
            if m.report is None:
                continue
            for row in m.report.get(key) or ():
                rows.append({"member": m.member, **dict(row)})
        return rows


def _operational(
    result: NoBaselineSetResult, evidence_contract: bool
) -> OperationalStatus:
    """The set's ``run_outcome.operational``: nothing completed outranks a
    failed member, which outranks a missed evidence contract -- the
    statuses are mutually exclusive per report, so the most fundamental one
    is named."""
    if result.record.no_comparison_completed:
        return OperationalStatus.NO_COMPARISON_COMPLETED
    if result.operationally_failed:
        return OperationalStatus.EXTRACTION_ERROR
    if evidence_contract:
        return OperationalStatus.EVIDENCE_CONTRACT_ERROR
    return OperationalStatus.NONE


def compute_no_baseline_set_document(
    result: NoBaselineSetResult,
    *,
    on_incomplete: str | None = None,
    require_complete_analysis: bool = False,
    audit_gate_enabled: bool = False,
) -> NoBaselineSetDocument:
    """Resolve *result* into the one document every format below projects."""
    from .disposition_audit import compute_disposition_audit, fold_disposition_audits

    decision = resolve_scope_decision(result.record, on_incomplete)
    audits = {a.member: a for a in result.audits}
    members: list[AuditSetMember] = []
    policy = "strict_abi"
    dispositions = []
    for acq in result.record.members:
        audit = audits.get(acq.member)
        doc = None
        report = None
        if audit is not None and audit.result is not None:
            doc = compute_no_baseline_document(
                audit.result,
                require_complete_analysis=require_complete_analysis,
                audit_gate_enabled=audit_gate_enabled,
            )
            report = no_baseline_report_document(doc).to_mapping()
            policy = doc.policy
            dispositions.append(compute_disposition_audit(audit.result.diff))
        members.append(
            AuditSetMember(
                member=acq.member,
                display_name=acq.name,
                acquisition_state=acq.state.value,
                required=acq.required,
                reason=acq.reason,
                document=doc,
                report=report,
                error_type=audit.error_type if audit is not None else "",
            )
        )
    audited_docs = [m.document for m in members if m.document is not None]
    exit_axes: dict[str, int] = {
        key: max((d.exit_axes.get(key, 0) for d in audited_docs), default=0)
        for key in MEMBER_EXIT_AXES
    }
    exit_axes["operational_error"] = (
        OPERATIONAL_ERROR_EXIT if result.operationally_failed else 0
    )
    exit_axes["incomplete_scope"] = decision.incomplete_scope_exit_contribution
    exit_axes["no_comparison_completed"] = (
        decision.no_comparison_completed_exit_contribution
    )
    run_outcome = RunOutcome(
        compatibility=None,
        assurance=None,
        gate=PolicyGateDecision.NONE,
        operational=_operational(result, bool(exit_axes["evidence_contract"])),
        scope=decision.completeness,
    ).to_dict()
    return NoBaselineSetDocument(
        library=result.plan.operand.name,
        operand_kind=result.plan.operand_kind,
        # One candidate version only when every audited member agrees on it;
        # a set of independently versioned libraries has none of its own.
        new_version=(
            audited_docs[0].new_version
            if audited_docs
            and all(d.new_version == audited_docs[0].new_version for d in audited_docs)
            else ""
        ),
        members=tuple(members),
        comparison_scope=build_comparison_scope_section(decision),
        exit_axes=exit_axes,
        exit_code=max(exit_axes.values()),
        run_outcome=run_outcome,
        policy=policy,
        disposition_audit=fold_disposition_audits(dispositions).to_dict(),
        warnings=result.plan.warnings,
    )


def _member_json(member: AuditSetMember) -> dict[str, Any]:
    row: dict[str, Any] = {
        "member": member.member,
        "display_name": member.display_name,
        "acquisition_state": member.acquisition_state,
        "required": member.required,
        "reason": member.reason,
        "report": dict(member.report) if member.report is not None else None,
    }
    if member.error_type:
        row["error_type"] = member.error_type
    return row


def no_baseline_set_json(doc: NoBaselineSetDocument) -> dict[str, Any]:
    """The ``audit_set`` JSON envelope."""
    contract_selected = any(
        m.document is not None and m.document.contract_selected for m in doc.members
    )
    suppressed = doc.findings_rows("suppressed_findings")
    return {
        "audit_set_report_schema_version": AUDIT_SET_REPORT_SCHEMA_VERSION,
        "audit_set": True,
        # The same two markers a scalar audit carries, so every consumer
        # that recognizes "an audit, not a comparison" by them (the Action's
        # `report_query`, `aggregate`'s loader) reads this one as an audit
        # too -- never as a comparison with a missing verdict.
        "no_baseline": True,
        "library": doc.library,
        "operand_kind": doc.operand_kind,
        "new_version": doc.new_version,
        "verdict": None,
        "old_acquisition_state": "declared_absent",
        "changes": [],
        "members": [_member_json(m) for m in doc.members],
        "findings": doc.findings_rows("findings"),
        "suppressed_findings": suppressed,
        "suppressed_count": len(suppressed),
        "comparison_scope": dict(doc.comparison_scope),
        "contract_coverage_exit_contribution": doc.exit_axes.get(
            "contract_coverage", 0
        ),
        **(
            {
                "contract_coverage_failures": [
                    {"member": m.member, **dict(f)}
                    for m in doc.members
                    if m.document is not None
                    for f in m.document.coverage_failures
                ]
            }
            if contract_selected
            else {}
        ),
        "exit_axes": dict(doc.exit_axes),
        "exit_code": doc.exit_code,
        "run_outcome": dict(doc.run_outcome),
        "policy": doc.policy,
        "disposition_audit": dict(doc.disposition_audit),
        "warnings": list(doc.warnings),
    }


def _demoted(markdown: str) -> list[str]:
    """A member's own scalar Markdown, every heading one level deeper, so it
    nests under the set's per-member heading."""
    return [
        f"#{line}" if line.startswith("#") else line
        for line in markdown.rstrip("\n").split("\n")
    ]


def render_no_baseline_set_markdown(doc: NoBaselineSetDocument) -> str:
    """Markdown projection of *doc*: the scope table, then each member."""
    audited = len(doc.audited)
    lines = [
        f"# ABI audit set: {doc.library} (no baseline)",
        "",
        "OLD side: **declared absent** (`--no-baseline`) for every member -- "
        "this audits each library of the candidate "
        f"{doc.operand_kind} alone, not a compatibility comparison. No "
        "additions, removals, or compatibility verdict are reported.",
        "",
        f"- Members audited: {audited} of {len(doc.members)}",
        f"- Scope: `{doc.comparison_scope.get('completeness')}` (inventory "
        f"`{(doc.comparison_scope.get('new_inventory') or {}).get('completeness', '?')}`)",
        "",
        "| Member | State | Required | Findings | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for m in doc.members:
        count = (
            str(len(m.document.findings) + len(m.document.suppressed))
            if m.document is not None
            else "-"
        )
        lines.append(
            f"| `{md_cell(m.display_name)}` | "
            f"{md_cell(_STATE_LABEL.get(m.acquisition_state, m.acquisition_state))} | "
            f"{'yes' if m.required else 'no'} | {count} | {md_cell(m.reason)} |"
        )
    notice = comparison_scope_notice(doc.comparison_scope)
    if notice:
        lines += ["", f"> {notice}"]
    for m in doc.members:
        if m.document is None:
            continue
        lines += ["", f"## Member `{md_cell(m.display_name)}`", ""]
        lines += _demoted(render_no_baseline_markdown(m.document))
    contributing = [
        _AUDIT_SET_EXIT_AXIS_NOTICES[key]
        for key, value in doc.exit_axes.items()
        if value and key in _AUDIT_SET_EXIT_AXIS_NOTICES
    ]
    if contributing:
        lines += ["", *(f"> {n}" for n in contributing)]
    return "\n".join(lines) + "\n"


def render_no_baseline_set_oneline(doc: NoBaselineSetDocument) -> str:
    """The one-sentence ``-o oneline=...`` projection of *doc*."""
    findings = sum(len(m.document.findings) for m in doc.audited if m.document)
    suppressed = sum(len(m.document.suppressed) for m in doc.audited if m.document)
    not_audited = (
        len(doc.members)
        - len(doc.audited)
        - sum(1 for m in doc.members if m.acquisition_state == "out_of_scope")
    )
    noun = "finding" if findings == 1 else "findings"
    parts = [f"{len(doc.audited)} member(s) audited"]
    if not_audited:
        parts.append(f"{not_audited} not audited")
    detail = f"{findings} candidate-side {noun}" + (
        f", {suppressed} suppressed" if suppressed else ""
    )
    contributing = [
        AUDIT_SET_EXIT_AXIS_LABELS[key]
        for key in AUDIT_SET_EXIT_AXIS_LABELS
        if doc.exit_axes.get(key)
    ]
    axes = f"; {', '.join(contributing)}" if contributing else ""
    return (
        f"{doc.library or '(unnamed)'} audit set (no baseline): "
        f"{', '.join(parts)}, {detail}, no compatibility verdict{axes} "
        f"[exit {doc.exit_code}]\n"
    )


def render_no_baseline_set(
    result: NoBaselineSetResult,
    fmt: str,
    *,
    on_incomplete: str | None = None,
    require_complete_analysis: bool = False,
    audit_gate_enabled: bool = False,
) -> tuple[str, int]:
    """Render an N-library audit in *fmt*; return ``(text, exit_code)``.

    *fmt* must be in :data:`NO_BASELINE_SET_SUPPORTED_FORMATS` -- the CLI
    rejects anything else before any analysis runs.
    """
    import json as _json

    doc = compute_no_baseline_set_document(
        result,
        on_incomplete=on_incomplete,
        require_complete_analysis=require_complete_analysis,
        audit_gate_enabled=audit_gate_enabled,
    )
    if fmt == "json":
        return _json.dumps(no_baseline_set_json(doc), indent=2), doc.exit_code
    if fmt == "markdown":
        return render_no_baseline_set_markdown(doc), doc.exit_code
    if fmt == "oneline":
        return render_no_baseline_set_oneline(doc), doc.exit_code
    raise ValueError(f"unsupported --no-baseline set format: {fmt!r}")
