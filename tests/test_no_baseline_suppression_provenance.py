# SPDX-License-Identifier: Apache-2.0
"""Suppression provenance in the ``--no-baseline`` audit's projections.

Split out of ``test_no_baseline_report_formats.py`` when that module crossed
1000 lines: these tests share one subject -- ADR-067 D3's rule record, and
how each renderer states it -- rather than the format-invariant subject the
parent module owns. Splitting by responsibility is what this repository asks
for when a file outgrows its cap (``AGENTS.md``: move responsibility out to a
properly-owned module, never trim to fit).

The subject in one sentence: ``Change.suppression_rule`` is
``SuppressionOutcome.rule_label()``'s ``label or reason`` collapse -- a single
string that does not say which of the two it holds -- so every projection
must read the run's own disposition ledger instead, and none may present one
field as two.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from abicheck.report.no_baseline import (
    NO_BASELINE_SUPPORTED_FORMATS,
    compute_no_baseline_document,
    render_no_baseline,
)
from abicheck.report.no_baseline_document import NO_BASELINE_EXIT_AXIS_LABELS
from abicheck.report.no_baseline_render import render_no_baseline_junit
from abicheck.suppression import Suppression, SuppressionList
from abicheck.workflows.no_baseline_compare import (
    resolve_no_baseline_candidate,
    run_no_baseline_compare,
)

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

#: The same G20 audit corpus the parent module gates on, restated here rather
#: than imported from it: a test module importing another test module for a
#: constant couples their collection order, and this list is the corpus, not
#: an implementation detail of either file.
_FIXTURES = (
    ("case143_audit_accidental_export", "snapshot.abi.json"),
    ("case144_audit_private_header_leak", "snapshot.abi.json"),
    ("case145_audit_unversioned_export", "snapshot.abi.json"),
    ("case146_audit_rtti_for_internal", "snapshot.abi.json"),
    ("case147_scan_depth_ladder", "snapshot.abi.json"),
    ("case148_xcheck_header_build_mismatch", "snapshot.abi.json"),
    ("case149_xcheck_odr_variant", "snapshot.abi.json"),
    ("case150_xcheck_export_public_pair", "snapshot.abi.json"),
    ("case151_xcheck_provider_matrix", "snapshot.abi.json"),
    ("case151_xcheck_provider_matrix", "thin.abi.json"),
    ("case181_xcheck_public_to_internal_dependency", "snapshot.abi.json"),
)


def _result(case: str, filename: str = "snapshot.abi.json", *, suppression=None):
    path = example_catalog.case_dir(case) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    snapshot = resolve_no_baseline_candidate(path)
    return run_no_baseline_compare(snapshot, suppression=suppression)


# ---------------------------------------------------------------------------
# Suppression provenance: the disposition audit's own record, not a label.
# ---------------------------------------------------------------------------


#: The three ways a rule can state its identity. A rule stating *both* is
#: what exposed the original defect, and a fix that special-cased equality
#: would still get label-only wrong -- so all three are always exercised.
_STATING_COMBINATIONS = [
    pytest.param("audit-waiver-17", "temporary vendor debug hook", id="both"),
    pytest.param(None, "temporary vendor debug hook", id="reason-only"),
    pytest.param("audit-waiver-17", None, id="label-only"),
]


def _labelled_result(label: str | None, reason: str | None):
    """An audit whose single finding is suppressed by one rule stating *label*/*reason*."""
    return _result(
        "case143_audit_accidental_export",
        suppression=SuppressionList(
            [Suppression(symbol_pattern=".*", label=label, reason=reason)],
            source_path="waivers.yaml",
        ),
    )


def _sarif_justifications(result) -> list[str]:
    sarif = json.loads(render_no_baseline(result, "sarif")[0])
    texts = [
        r["suppressions"][0]["justification"]
        for r in sarif["runs"][0]["results"]
        if r.get("suppressions")
    ]
    assert texts, "a suppressed finding must carry a SARIF suppression"
    return texts


def _junit_skip_messages(result) -> list[str]:
    junit = ET.fromstring(render_no_baseline(result, "junit")[0])
    messages = [s.attrib["message"] for s in junit.iter("skipped")]
    assert messages, "a suppressed finding must be a skipped JUnit case"
    return messages


def _assert_no_field_shown_twice(text: str) -> None:
    """The invariant behind every expected string here, stated separately.

    Checked by splitting the rendered text and comparing part counts, never
    by re-running the helper that produced it -- an oracle that shares the
    implementation's logic cannot falsify it.
    """
    parts = [p.strip() for p in text.split(":")]
    assert len(parts) == len(set(parts)), (
        f"justification {text!r} repeats a field; one source rendered as two"
    )


def _labelled_and_reasoned() -> SuppressionList:
    """A rule stating BOTH a label and a reason.

    This is the shape that exposed the bug: `SuppressionOutcome.rule_label()`
    is `label or reason`, so a rule carrying both publishes its label and
    silently drops the reason. A rule with only one of them cannot detect
    that, which is why every test here uses both.
    """
    return SuppressionList(
        [
            Suppression(
                symbol_pattern=".*",
                label="audit-waiver-17",
                reason="temporary vendor debug hook",
            )
        ],
        source_path="waivers.yaml",
    )


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
def test_every_suppressed_finding_keeps_its_full_rule_provenance(
    case: str, filename: str
) -> None:
    """ADR-067 D3: a disposition keeps the rule *and the reason* that hid it.

    The audit published `suppression_rule`, which is the display label —
    `label or reason`, never both — so a reader could not tell why a waiver
    was written or which file to edit to review it (Codex review, P1).

    The oracle is the run's own disposition ledger, not the report: every
    field the ledger recorded for the rule that fired must survive into the
    projection. Asserted over the whole fixture corpus and over every
    ledger-recorded field, so a projection that carries one field and drops
    another still fails.
    """
    result = _result(case, filename, suppression=_labelled_and_reasoned())
    doc = compute_no_baseline_document(result)
    ledger = result.diff.disposition_ledger
    assert doc.suppressed, "this fixture must have something to suppress"

    body = json.loads(render_no_baseline(result, "json")[0])
    rows = body["suppressed_findings"]
    assert len(rows) == len(doc.suppressed)

    for row, entry in zip(rows, doc.suppressed, strict=True):
        recorded = ledger.rule_for(entry.change)
        assert recorded is not None, (
            "the run recorded no ledger entry, so this test would pass "
            "vacuously against a projection that emits nothing"
        )
        assert row["suppression_provenance"] == recorded.to_dict(), (
            "every field the ledger recorded must reach the report; the "
            "display label alone collapses label and reason into one string"
        )
        # The specific loss the fix closes, stated independently of the
        # ledger comparison above so it cannot be satisfied by an equality
        # that happens to compare two equally-lossy values.
        assert row["suppression_provenance"]["reason"] == (
            "temporary vendor debug hook"
        )
        assert row["suppression_provenance"]["label"] == "audit-waiver-17"
        assert row["suppression_provenance"]["source_file"] == "waivers.yaml"


@pytest.mark.parametrize("fmt", sorted(NO_BASELINE_SUPPORTED_FORMATS - {"oneline"}))
def test_every_format_carries_the_reason_and_source_not_just_the_label(
    fmt: str,
) -> None:
    """The provenance reaches *every* projection, not the two wired first.

    JSON and Markdown gained it first; SARIF and JUnit kept reading the
    display label, so a consumer of either structured CI format still could
    not tell why or where a finding was suppressed (Codex review, P2). A
    fact one format publishes while its siblings drop it leaves a reader of
    the quiet format unable to act on the run they were handed.

    Parametrized over the supported set rather than naming today's four, so
    a format added later is held to this without anyone remembering to.
    `oneline` is excluded deliberately: it is one sentence by contract and
    carries no per-finding detail at all.
    """
    result = _result(
        "case143_audit_accidental_export", suppression=_labelled_and_reasoned()
    )
    assert compute_no_baseline_document(result).suppressed
    text = render_no_baseline(result, fmt)[0]

    assert "audit-waiver-17" in text, f"{fmt} must name the rule"
    assert "temporary vendor debug hook" in text, (
        f"{fmt} must carry the reason — it is what tells a reviewer whether "
        "the waiver still applies, and the display label drops it whenever "
        "the rule also states a label"
    )
    assert "waivers.yaml" in text, (
        f"{fmt} must carry the source file — it is where a reviewer would "
        "edit or remove the waiver"
    )


def test_a_run_without_a_ledger_reports_no_provenance_rather_than_faking_one() -> None:
    """The complement: absent provenance is `null`, never invented.

    A `DiffResult` rebuilt from JSON carries no disposition ledger. Emitting
    a row derived from `Change.suppression_rule` there would look like a real
    ADR-067 record while carrying strictly less, which is worse than saying
    nothing.
    """
    result = _result(
        "case143_audit_accidental_export", suppression=_labelled_and_reasoned()
    )
    object.__setattr__(result.diff, "disposition_ledger", None)
    doc = compute_no_baseline_document(result)

    assert doc.suppressed
    assert all(entry.provenance is None for entry in doc.suppressed)
    body = json.loads(render_no_baseline(result, "json")[0])
    assert all(
        row["suppression_provenance"] is None for row in body["suppressed_findings"]
    )

    # Every projection is *rendered*, not just the document inspected. The
    # first version of this test asserted the document and the JSON row and
    # stopped there, so the renderers' own no-provenance fallbacks were
    # never executed — this test claimed the fallback was right while never
    # running it (found via the patch-coverage report, which flagged exactly
    # those lines).
    #
    # With no ledger the label falls back to the collapsed display field,
    # which for this rule holds its label; what must NOT appear is a
    # fabricated structured record.
    markdown = render_no_baseline(result, "markdown")[0]
    assert "audit-waiver-17" in markdown, (
        "with no ledger the collapsed display field is all that is knowable, "
        "and it is still shown"
    )
    assert "waivers.yaml" not in markdown, (
        "the source file was never recorded for this run, so inventing it "
        "would be a fabricated ADR-067 record"
    )

    sarif = json.loads(render_no_baseline(result, "sarif")[0])
    suppressions = [
        r["suppressions"][0]
        for r in sarif["runs"][0]["results"]
        if r.get("suppressions")
    ]
    assert suppressions, "a suppressed finding must still carry a suppression"
    for suppression in suppressions:
        assert "properties" not in suppression, (
            "no ledger entry means no structured record to publish"
        )
        assert suppression["justification"] == "audit-waiver-17"

    junit = ET.fromstring(render_no_baseline(result, "junit")[0])
    skipped = list(junit.iter("skipped"))
    assert skipped, "a suppressed finding must still be a skipped JUnit case"
    for element in skipped:
        assert element.attrib["message"] == "suppressed: audit-waiver-17"
        assert "suppression rule:" not in (element.text or ""), (
            "the structured block belongs only to a run that recorded one"
        )


def test_a_rule_stating_neither_label_nor_reason_still_reads_as_suppressed() -> None:
    """The last fallback: a rule with nothing to say about itself.

    `--suppress` does not require a label or a reason (only
    `suppression.require_justification` does), so a bare selector-only rule
    is legal input. Every projection must still say the finding was
    suppressed rather than rendering an empty or missing attribution.
    """
    result = _result(
        "case143_audit_accidental_export",
        suppression=SuppressionList([Suppression(symbol_pattern=".*")]),
    )
    doc = compute_no_baseline_document(result)
    assert doc.suppressed

    sarif = json.loads(render_no_baseline(result, "sarif")[0])
    justifications = [
        r["suppressions"][0]["justification"]
        for r in sarif["runs"][0]["results"]
        if r.get("suppressions")
    ]
    assert justifications == ["suppressed by an abicheck --suppress rule"] * len(
        justifications
    )
    assert justifications

    markdown = render_no_baseline(result, "markdown")[0]
    assert "(rule gave no label)" in markdown
    assert "(none stated)" in markdown


@pytest.mark.parametrize(
    ("label", "reason", "expected"),
    [
        pytest.param(
            "audit-waiver-17",
            "temporary vendor debug hook",
            "audit-waiver-17: temporary vendor debug hook",
            id="both",
        ),
        pytest.param(
            None,
            "temporary vendor debug hook",
            "temporary vendor debug hook",
            id="reason-only",
        ),
        pytest.param("audit-waiver-17", None, "audit-waiver-17", id="label-only"),
    ],
)
def test_a_suppression_justification_never_repeats_one_field_as_two(
    label: str | None, reason: str | None, expected: str
) -> None:
    """`label: reason` only when the rule really states both.

    `Change.suppression_rule` is `label or reason` — one string that does
    not say which of the two it holds. Reading it as a label whenever
    provenance had none rendered a reason-only rule as
    `"<reason>: <reason>"`, presenting the reason as a separate rule label
    (Codex review, P2). That is the shape
    `suppression.require_justification` actively encourages, so it is the
    common case rather than an edge one.

    All three stating combinations are exercised, not just the reported
    reason-only one: a fix that special-cased equality would still get
    label-only wrong, and the invariant is "never present one field as
    two", not "handle this input".
    """
    result = _labelled_result(label, reason)
    assert compute_no_baseline_document(result).suppressed, (
        "this fixture must have something to suppress"
    )

    # The single-string projections. Both go through one helper, so they are
    # checked together -- if they ever diverge, that is itself the bug.
    for text in _sarif_justifications(result):
        assert text == expected
        _assert_no_field_shown_twice(text)
    for message in _junit_skip_messages(result):
        assert message == f"suppressed: {expected}", (
            "JUnit and SARIF share one justification helper and must not "
            "phrase it differently"
        )


@pytest.mark.parametrize(("label", "reason"), _STATING_COMBINATIONS)
def test_the_markdown_columns_never_show_one_field_as_two(
    label: str | None, reason: str | None
) -> None:
    """The same rule at Markdown's shape: two columns, not one string.

    Split from the single-string test above rather than folded into it,
    because this is where the defect recurred: Markdown shows the label and
    the reason in *separate columns*, so a test written only against the
    helper's one-string shape could not have caught it (Codex review, P2,
    the second time).
    """
    result = _labelled_result(label, reason)
    markdown = render_no_baseline(result, "markdown")[0]
    rows = [
        line
        for line in markdown.splitlines()
        if line.startswith("| `exported_not_public`")
    ]
    assert rows, "the suppressed finding must appear in the Markdown table"
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[3] != cells[4], (
            f"Markdown row {row!r} prints the same text as both the rule "
            "label and the reason; one field shown as two"
        )
        assert cells[3] == (label or "(rule gave no label)")
        assert cells[4] == (reason or "(none stated)")


def test_a_non_coverage_gate_names_its_axis_without_a_coverage_block() -> None:
    """A gate on an axis that names no provider omits the provider block.

    The JUnit failure text has two parts: the contributing axes, and the
    contract-coverage ledger's per-provider rows. Only the coverage axis can
    name a provider, so a run gated purely on (say) analysis assurance must
    state its axis and print no coverage section at all — an empty
    "contract coverage failures:" heading would imply a ledger that closed
    with nothing missing, which is a different fact from having no ledger.

    Asserted by projecting a document built for that state rather than by
    hunting a fixture that happens to gate that way: `render_no_baseline_junit`
    is a pure function of the document, so the document *is* the input under
    test, and constructing it directly is what lets the combination be
    exercised at all.
    """
    base = compute_no_baseline_document(_result("case143_audit_accidental_export"))
    doc = dataclasses.replace(
        base,
        exit_code=1,
        exit_axes={"analysis_assurance": 1, "contract_coverage": 0},
        coverage_failures=(),
    )

    failure = ET.fromstring(render_no_baseline_junit(doc)).find(".//failure")
    assert failure is not None and failure.text

    assert NO_BASELINE_EXIT_AXIS_LABELS["analysis_assurance"] in failure.text
    assert NO_BASELINE_EXIT_AXIS_LABELS["contract_coverage"] not in failure.text, (
        "an axis that contributed 0 must not be named as a cause"
    )
    assert "contract coverage failures:" not in failure.text, (
        "no ledger rows means no section; an empty heading would imply a "
        "domain that closed cleanly, which is a different fact"
    )


def test_a_suppressed_entry_is_still_a_report_finding() -> None:
    """`doc.suppressed` entries stay usable as ordinary findings.

    `NoBaselineDocument` is reachable from `abicheck.report.no_baseline`, and
    a consumer iterating `.suppressed` reads `.change`/`.verdict`/`.category`
    the same way it reads `.findings`. An earlier version of this branch
    paired the finding with its provenance side by side, which turned every
    such read into an `AttributeError` — trading one regression for the
    provenance fix (Codex review, P2).

    Making `SuppressedFinding` *a* `ReportFinding` rather than a wrapper is
    what keeps both true at once, and this pins it: the attribute reads and
    the `isinstance` relationship are both asserted, since forwarding
    properties would satisfy the first alone and still fail a consumer that
    type-checks.
    """
    from abicheck.report.finding import ReportFinding

    result = _result(
        "case143_audit_accidental_export", suppression=_labelled_and_reasoned()
    )
    doc = compute_no_baseline_document(result)
    assert doc.suppressed

    for entry in doc.suppressed:
        assert isinstance(entry, ReportFinding)
        # Read exactly as a `.findings` entry would be.
        assert entry.change is not None
        assert entry.verdict is not None
        assert entry.category is not None
        # And the provenance rides along rather than replacing any of it.
        assert entry.provenance is not None
        assert entry.provenance["reason"] == "temporary vendor debug hook"

    # The two collections are interchangeable to a consumer that does not
    # care which is which -- the property that makes `.suppressed` a
    # disposition of findings rather than a separate kind of thing.
    for entry in [*doc.findings, *doc.suppressed]:
        assert isinstance(entry, ReportFinding)
