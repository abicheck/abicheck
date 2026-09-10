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
from .no_baseline_document import NO_BASELINE_EXIT_AXIS_LABELS

if TYPE_CHECKING:
    from .finding import ReportFinding
    from .no_baseline_document import NoBaselineDocument

__all__ = ["render_no_baseline_junit", "render_no_baseline_sarif"]


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
