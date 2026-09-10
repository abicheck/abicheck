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
        recorded = ledger.rule_for(entry.finding.change)
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
    rule = Suppression(symbol_pattern=".*", label=label, reason=reason)
    result = _result(
        "case143_audit_accidental_export",
        suppression=SuppressionList([rule], source_path="waivers.yaml"),
    )
    doc = compute_no_baseline_document(result)
    assert doc.suppressed, "this fixture must have something to suppress"

    sarif = json.loads(render_no_baseline(result, "sarif")[0])
    justifications = [
        r["suppressions"][0]["justification"]
        for r in sarif["runs"][0]["results"]
        if r.get("suppressions")
    ]
    assert justifications, "a suppressed finding must carry a SARIF suppression"
    for text in justifications:
        assert text == expected
        # The invariant behind the specific expectations above, stated
        # independently of them: no field is ever printed twice.
        parts = [p.strip() for p in text.split(":")]
        assert len(parts) == len(set(parts)), (
            f"justification {text!r} repeats a field; one source rendered as two"
        )

    junit = ET.fromstring(render_no_baseline(result, "junit")[0])
    skipped = [s.attrib["message"] for s in junit.iter("skipped")]
    assert skipped, "a suppressed finding must be a skipped JUnit case"
    for message in skipped:
        assert message == f"suppressed: {expected}", (
            "JUnit and SARIF share one justification helper and must not "
            "phrase it differently"
        )
