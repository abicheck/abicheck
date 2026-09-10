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

"""SARIF and JUnit projections of a ``compare --no-baseline`` audit.

The machine-format half of :mod:`abicheck.report.no_baseline`, split out the
same way this package already separates ``render_json.py``/``render_xml.py``
from ``document.py``: both renderers here are pure projections of one
:class:`~abicheck.report.no_baseline.NoBaselineDocument` and decide nothing.

Both are built here rather than through ``sarif.to_sarif``/
``junit_report.to_junit_xml`` because those frame a run around a
compatibility verdict, which ADR-068 D2 forbids this run from claiming --
their ``exitCode``/suite partitioning derive from ``DiffResult.verdict``,
which on a self-compare reads ``NO_CHANGE``. The per-finding *metadata* is
reused rather than reinvented (``sarif._rule_for``,
``_parse_source_location``), so a rule id, help URI and level mean exactly
what they mean in a two-sided document.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

from .cross_source_evolution import change_cross_source_evolution_field
from .no_baseline_document import (
    NO_BASELINE_EXIT_AXIS_LABELS,
    suppression_provenance_of,
    suppression_rule_label,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .finding import ReportFinding
    from .no_baseline_document import NoBaselineDocument

__all__ = ["render_no_baseline_junit", "render_no_baseline_sarif"]


#: The SARIF 2.1.0 schema this document declares. Named rather than inlined
#: only so the literal does not force a 116-column line.
_SARIF_SCHEMA_URL = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master"
    "/Schemata/sarif-schema-2.1.0.json"
)


def _suppression_justification(
    change: Any, provenance: Mapping[str, Any] | None
) -> str:
    """The human-readable half of a suppression, best information first.

    ``label: reason`` when the rule states both, otherwise whichever single
    field it states, otherwise a generic fallback.

    The two sources are read separately and never mixed, which is the whole
    point. ``Change.suppression_rule`` is ``label or reason`` -- one string
    that does not say *which* it holds -- so treating it as a label whenever
    provenance lacked one rendered a reason-only rule as
    ``"<reason>: <reason>"``, presenting the reason as a separate rule label
    (Codex review, P2). That is the shape ``suppression.require_justification``
    actively encourages, so it is the common case, not an edge one.
    The collapsed field is therefore consulted *only* when there is no
    provenance at all -- the case where nothing better is knowable -- and
    then stands alone rather than being paired with anything.
    """
    label = suppression_rule_label(change, provenance)
    reason = (provenance or {}).get("reason")
    if reason and label:
        return f"{label}: {reason}"
    if reason or label:
        return str(reason or label)
    return "suppressed by an abicheck --suppress rule"


def _sarif_result(
    finding: ReportFinding,
    *,
    rule_id: str,
    suppressed: bool,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One SARIF ``result`` for a candidate-side finding.

    Split out of :func:`render_no_baseline_sarif` so the envelope there reads
    as the run it describes, and the per-finding shape sits next to its JUnit
    counterpart (:func:`_junit_finding_case`) rather than buried in a loop.

    *provenance* is the suppressing rule's full ADR-067 D3 record. It is
    carried into the SARIF suppression rather than only the display label,
    which is `label or reason` and so drops whichever the rule stated
    second (Codex review, P2 -- the JSON and Markdown projections gained
    this first, and a fact one format publishes while its siblings drop it
    leaves a consumer of the quiet format unable to act on the run it was
    handed).
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
        suppression: dict[str, Any] = {
            "kind": "external",
            # SARIF's `justification` is free text, so it carries the reason
            # a human wrote when the rule states one -- that is the field's
            # actual purpose -- and falls back to the label only when there
            # is no reason to give.
            "justification": _suppression_justification(change, provenance),
        }
        if provenance:
            # The structured record too: `justification` is one string, and a
            # consumer deciding whether a waiver still applies needs the
            # source file and expiry as data, not prose.
            suppression["properties"] = dict(provenance)
        entry["suppressions"] = [suppression]
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


def _sarif_invocation(doc: NoBaselineDocument) -> dict[str, Any]:
    """The SARIF ``invocation`` for an audit, including *why* it exited.

    ``executionSuccessful`` is the SARIF spec's "did the tool run to
    completion", not "did it find anything" -- an audit that completed is
    successful however many hygiene findings it reports.

    The exit *description* is where a consumer learns why a nonzero code
    happened, and this published only a generic sentence: a coverage-gated
    audit said `exitCode: 1` and nothing about which provider fell short,
    even after the ledger reached the document (Codex review, P2). Each
    contributing axis is now named, and a coverage failure additionally
    becomes a ``toolExecutionNotification`` -- the shape SARIF has for "the
    run itself was limited", which is what an incomplete evidence domain is,
    as opposed to a `result` about the code.
    """
    contributing = [
        NO_BASELINE_EXIT_AXIS_LABELS[key]
        for key in NO_BASELINE_EXIT_AXIS_LABELS
        if doc.exit_axes.get(key)
    ]
    description = (
        "single-build audit (--no-baseline): no compatibility verdict is reported"
    )
    if contributing:
        description += "; " + ", ".join(contributing)
    invocation: dict[str, Any] = {
        "executionSuccessful": True,
        "exitCode": doc.exit_code,
        "exitCodeDescription": description,
    }
    notifications = [
        {
            "level": "error",
            "message": {
                "text": (
                    f"contract coverage incomplete: provider "
                    f"{failure.get('provider', '(unknown)')} on side "
                    f"{failure.get('side', '(unknown)')} -- "
                    f"{failure.get('reason', '(no reason recorded)')} "
                    f"(status {failure.get('status', '(unknown)')}, "
                    f"completeness {failure.get('completeness', '(unknown)')})"
                )
            },
            "descriptor": {"id": "abicheck.contract-coverage-failure"},
        }
        for failure in doc.coverage_failures
    ]
    if notifications:
        invocation["toolExecutionNotifications"] = notifications
    return invocation


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
    for finding, suppressed, provenance in [(f, False, None) for f in doc.findings] + [
        (entry, True, suppression_provenance_of(entry)) for entry in doc.suppressed
    ]:
        rule = _rule_for(finding.change.kind)
        rules.setdefault(rule["id"], rule)
        results.append(
            _sarif_result(
                finding,
                rule_id=rule["id"],
                suppressed=suppressed,
                provenance=provenance,
            )
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
                "invocations": [_sarif_invocation(doc)],
                "results": results,
                "properties": {
                    "noBaseline": True,
                    "library": doc.library,
                    "candidateVersion": doc.new_version,
                    "oldAcquisitionState": doc.old_acquisition_state,
                    "evidenceTiers": list(doc.evidence_tiers),
                    "contractCoverageExitContribution": doc.coverage_exit_contribution,
                    # The ledger itself, not just its contribution -- a
                    # code-scanning consumer reading properties gets the same
                    # actionable rows the JSON report carries.
                    **(
                        {
                            "contractCoverageFailures": [
                                dict(f) for f in doc.coverage_failures
                            ]
                        }
                        if doc.contract_selected
                        else {}
                    ),
                    "exitAxes": dict(doc.exit_axes),
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

    # One property per orthogonal exit axis, carrying its own contribution.
    # Without these the suite published only the total and the coverage
    # contribution, so a JUnit consumer gated by (say) the evidence contract
    # could not tell *which* axis fired -- the failure text merely listed
    # every axis that might have (Codex review, P2). Every axis the run
    # resolved is emitted, contributing or not, so a `0` is a real "this axis
    # was evaluated and cleared" rather than an absent key.
    for axis, contribution in doc.exit_axes.items():
        ET.SubElement(
            props,
            "property",
            {"name": f"exit_axis.{axis}", "value": str(contribution)},
        )

    for finding in doc.findings:
        _junit_finding_case(suite, finding, suppressed=False)
    for entry in doc.suppressed:
        # `<skipped>`, not a silent omission and not a failure: SARIF has a
        # `suppressions` array for this and JUnit's nearest honest
        # equivalent is a skipped case -- the finding is reported, and its
        # disposition is legible, without claiming it broke anything.
        _junit_finding_case(
            suite,
            entry,
            suppressed=True,
            provenance=suppression_provenance_of(entry),
        )

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
        # Name the axes that actually fired, and what each one means -- the
        # same resolved `exit_axes` the JSON, Markdown, oneline and SARIF
        # projections read, so no format explains an exit the others cannot
        # (Codex review, P2). Listing every possible axis instead, as this
        # used to, tells a gated consumer nothing.
        contributing = [
            axis for axis, contribution in doc.exit_axes.items() if contribution
        ]
        detail = [
            f"exit code: {doc.exit_code}",
            f"contract coverage contribution: {doc.coverage_exit_contribution}",
            "note: a candidate-side hygiene finding never gates on its own "
            "(ADR-028 D3 / ADR-035 D1); an audit's exit code is a max over "
            "orthogonal axes.",
        ]
        if contributing:
            detail.append("")
            detail.append("contributing axes:")
            detail += [
                f"- {NO_BASELINE_EXIT_AXIS_LABELS.get(axis, axis)} "
                f"(contributed {doc.exit_axes[axis]})"
                for axis in contributing
            ]
        else:
            # A document this package builds cannot reach here: its exit
            # code is `max(exit_axes.values())`, so a nonzero one always has
            # a nonzero axis behind it. A *hand-built* document can, since
            # `exit_axes` defaults to empty -- and the honest answer is to
            # say the record is missing rather than to leave a gated
            # consumer with a bare number, which is the very failure the
            # comment above names. Inventing an axis would be worse than
            # silence; saying nothing was recorded is neither.
            detail.append("")
            detail.append(
                "contributing axes: none recorded -- this document carries an "
                "exit code with no per-axis breakdown, so which axis gated "
                "cannot be answered from it."
            )
        # The coverage ledger itself, for the one axis that can name a
        # specific provider -- the number alone says nothing about which
        # provider on which side fell short.
        if doc.coverage_failures:
            detail.append("")
            detail.append("contract coverage failures:")
            detail += [
                "- " + ", ".join(f"{k}={v}" for k, v in sorted(f.items()))
                for f in doc.coverage_failures
            ]
        failure.text = "\n".join(detail)
    ET.indent(suite, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        suite, encoding="unicode"
    )


def _junit_finding_case(
    suite: ET.Element,
    finding: ReportFinding,
    *,
    suppressed: bool,
    provenance: Mapping[str, Any] | None = None,
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
        # The message carries the reason where the rule states one, not just
        # the display label (`label or reason`, which drops whichever the
        # rule stated second) -- the same statement the JSON, Markdown and
        # SARIF projections make (Codex review, P2).
        skipped = ET.SubElement(
            case,
            "skipped",
            {
                "message": f"suppressed: {_suppression_justification(change, provenance)}"
            },
        )
        # The structured record below the message, so a consumer can read the
        # source file and expiry as data rather than parsing prose out of an
        # attribute.
        skipped.text = "\n".join([detail, *_provenance_detail_lines(provenance)])
    else:
        ET.SubElement(case, "system-out").text = detail


def _provenance_detail_lines(provenance: Mapping[str, Any] | None) -> list[str]:
    """The suppressing rule's ADR-067 record, as plain ``key: value`` lines.

    Empty when the run kept no ledger entry -- never a fabricated row, which
    would look like a real record while carrying strictly less.
    """
    if not provenance:
        return []
    return [
        "",
        "suppression rule:",
        *[f"  {key}: {value}" for key, value in sorted(provenance.items())],
    ]
