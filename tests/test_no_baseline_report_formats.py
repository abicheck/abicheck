# SPDX-License-Identifier: Apache-2.0
"""Cross-format invariants for the ``compare --no-baseline`` audit report.

Three defects motivate this module, all found by review rather than by a
test, and all of the same shape: a *derived* answer disagreeing with the
answer it was derived from.

* **A suppressed finding vanished.** ``checker.compare()`` moves a matched
  finding out of ``diff.changes`` into ``diff.suppressed_changes``; the
  audit read only the former, so a suppressed run was indistinguishable
  from a clean one — no way to tell that policy hid a finding, or which
  rule did. ``vision.md``'s "Record before disposing" forbids exactly that:
  "100 removals detected, 100 suppressed by rule X" must stay visible on a
  passing run.
* **JUnit failed a build the CLI passed.** Every finding got a
  ``<failure>``, while the same document reports exit ``0`` — audit hygiene
  findings are advisory (ADR-028 D3 / ADR-035 D1) and ADR-068 D2 gives the
  run no compatibility contribution at all. This is the identical bug
  ``junit_report._is_failure`` records having already been fixed once for
  the two-sided report.
* **The dry run disagreed with the run.** A pinned ``--depth build`` on a
  *stored* candidate was previewed as a blocker while the real run exempts
  it (no extraction happened, so nothing can have fallen short).

The fixes are per-format, so the tests are not: each invariant below is
stated once, over every format and every fixture, so a future format cannot
be added that satisfies none of them. The unifying rule is that
:func:`~abicheck.report.no_baseline.compute_no_baseline_document` is the
single source of truth and each renderer is a pure projection of it — so no
renderer may report a finding count, a suppression, or a pass/fail the
document does not.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.report.no_baseline import (
    NO_BASELINE_EXIT_AXIS_LABELS,
    NO_BASELINE_EXIT_AXIS_NOTICES,
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

_HSETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)

#: The G20 audit corpus, as ``(case, file)`` — the same fixtures
#: ``tests/parity/test_no_baseline_audit_corpus_parity.py`` gates on, reused
#: here so the format invariants are checked against real detector output
#: rather than only against hand-built findings.
_FIXTURES = (
    ("case143_audit_accidental_export", "snapshot.abi.json"),
    ("case144_audit_private_header_leak", "snapshot.abi.json"),
    ("case145_audit_unversioned_export", "snapshot.abi.json"),
    ("case146_audit_rtti_for_internal", "snapshot.abi.json"),
    ("case148_xcheck_header_build_mismatch", "snapshot.abi.json"),
    ("case149_xcheck_odr_variant", "snapshot.abi.json"),
    ("case150_xcheck_export_public_pair", "snapshot.abi.json"),
    ("case181_xcheck_public_to_internal_dependency", "snapshot.abi.json"),
)


def _result(case: str, filename: str = "snapshot.abi.json", *, suppression=None):
    path = example_catalog.case_dir(case) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    snapshot = resolve_no_baseline_candidate(path)
    return run_no_baseline_compare(snapshot, suppression=suppression)


def _suppress_everything() -> SuppressionList:
    """A rule matching every symbol, so the whole finding set is suppressed.

    Deliberately total rather than targeted: the conservation invariant is
    most likely to break at the extreme where *nothing* is left in
    ``findings``, which is exactly the state a reader would misread as
    "clean".
    """
    # `symbol_pattern` is a regular expression, not a glob -- `.*` is the
    # match-everything spelling here, and a bare `*` is a construction error.
    return SuppressionList(
        [Suppression(symbol_pattern=".*", reason="suppress-everything test rule")]
    )


# ---------------------------------------------------------------------------
# Conservation: a finding is reported or dispositioned, never dropped.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
def test_suppressing_everything_reports_everything_as_suppressed(
    case: str, filename: str
) -> None:
    """The whole finding set survives suppression as a *disposition*.

    Compares against the same fixture's unsuppressed run rather than a
    hard-coded count, so the oracle is the detector's own output and the
    test cannot drift as the corpus changes.
    """
    baseline = _result(case, filename)
    suppressed = _result(case, filename, suppression=_suppress_everything())

    assert baseline.findings, f"{case}: fixture reports nothing to suppress"
    assert suppressed.findings == (), f"{case}: a matched rule must empty findings"
    assert [c.kind for c in suppressed.suppressed_findings] == [
        c.kind for c in baseline.findings
    ], f"{case}: suppression lost a finding instead of dispositioning it"


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
@pytest.mark.parametrize("fmt", sorted(NO_BASELINE_SUPPORTED_FORMATS))
def test_every_format_discloses_a_suppressed_finding(
    case: str, filename: str, fmt: str
) -> None:
    """No format may render a fully-suppressed audit as an empty one.

    The failure this prevents is silent by nature: the run passes, the
    output is well-formed, and the finding is simply absent. Each format is
    therefore checked for a positive disclosure signal, not merely for "not
    crashing".
    """
    result = _result(case, filename, suppression=_suppress_everything())
    assert result.suppressed_findings, "fixture precondition"
    kind = result.suppressed_findings[0].kind.value
    text, _ = render_no_baseline(result, fmt)

    if fmt == "json":
        payload = json.loads(text)
        assert payload["suppressed_count"] == len(result.suppressed_findings)
        assert kind in {f["kind"] for f in payload["suppressed_findings"]}
    elif fmt == "sarif":
        payload = json.loads(text)
        results = payload["runs"][0]["results"]
        assert kind in {r["ruleId"] for r in results}
        assert all("suppressions" in r for r in results), (
            "a suppressed finding must carry SARIF's own suppressions array, "
            "otherwise a code-scanning consumer shows it as a live finding"
        )
    elif fmt == "junit":
        root = ET.fromstring(text)
        assert root.findall(".//skipped"), "suppressed findings render as skipped"
        assert kind in text
    else:  # markdown, oneline
        assert "suppress" in text.lower(), f"{fmt} does not disclose suppression"
        if fmt == "markdown":
            assert kind in text


# ---------------------------------------------------------------------------
# Coherence: no renderer may disagree with the document it projects.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
def test_junit_failure_count_tracks_the_exit_code_exactly(
    case: str, filename: str
) -> None:
    """JUnit's pass/fail is the run's own gate, not the presence of findings.

    The reported bug in one assertion: a hygiene finding never gates, so a
    suite that fails on one fails a build the CLI passed.
    """
    for suppression in (None, _suppress_everything()):
        result = _result(case, filename, suppression=suppression)
        doc = compute_no_baseline_document(result)
        text, exit_code = render_no_baseline(result, "junit")
        root = ET.fromstring(text)
        failures = int(root.get("failures", "-1"))
        assert exit_code == doc.exit_code
        assert failures == (1 if doc.exit_code else 0), (
            f"{case}: JUnit reports {failures} failure(s) beside exit "
            f"{doc.exit_code} — the file and the process must agree"
        )
        if doc.exit_code == 0:
            assert not root.findall(".//failure"), (
                "a clean audit must contain no <failure>, however many "
                "advisory findings it reports"
            )


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
def test_junit_reports_every_finding_even_though_none_fail(
    case: str, filename: str
) -> None:
    """Advisory does not mean invisible (ADR-068 D9's "the fact stays visible").

    Guards the over-correction of the reported bug: making findings pass by
    dropping them would satisfy the failure-count invariant above while
    losing the audit's entire content.
    """
    result = _result(case, filename)
    text, _ = render_no_baseline(result, "junit")
    root = ET.fromstring(text)
    names = {tc.get("classname", "") for tc in root.findall(".//testcase")}
    for change in result.findings:
        assert f"abicheck.audit.{change.kind.value}" in names, (
            f"{change.kind.value} is absent from the JUnit suite entirely"
        )


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
def test_every_format_agrees_on_the_exit_code(case: str, filename: str) -> None:
    """ADR-068 D4: presentation never changes analysis."""
    result = _result(case, filename)
    codes = {
        fmt: render_no_baseline(result, fmt)[1]
        for fmt in sorted(NO_BASELINE_SUPPORTED_FORMATS)
    }
    assert len(set(codes.values())) == 1, f"{case}: formats disagree: {codes}"


@pytest.mark.parametrize(("case", "filename"), _FIXTURES)
def test_json_and_sarif_agree_on_the_finding_set(case: str, filename: str) -> None:
    """The two machine formats are projections of one document, so their
    finding sets are the same set — not merely both non-empty."""
    result = _result(case, filename)
    payload = json.loads(render_no_baseline(result, "json")[0])
    sarif = json.loads(render_no_baseline(result, "sarif")[0])
    assert sorted(f["kind"] for f in payload["findings"]) == sorted(
        r["ruleId"] for r in sarif["runs"][0]["results"]
    )


# ---------------------------------------------------------------------------
# The conservation invariant, over generated suppression rules.
# ---------------------------------------------------------------------------


@given(
    pattern=st.sampled_from(
        [".*", "_Z.*", ".*v", "nomatch_.*", "_Z11debug_dumpv", ".+"]
    ),
    case=st.sampled_from([c for c, _ in _FIXTURES]),
)
@_HSETTINGS
def test_no_finding_is_ever_lost_whatever_the_rule_matches(
    pattern: str, case: str
) -> None:
    """Conservation, stated over rules that match all, some, or none.

    The reported bug only showed up when a rule *did* match, so a test
    fixed to one pattern proves little: a partial match is where a
    conservation bug is most likely to hide, since the run still reports
    something and looks healthy. The oracle is the unsuppressed run's own
    finding set, and the invariant is that suppression only ever *moves*
    a finding between the two lists.
    """
    baseline = _result(case)
    suppressed = _result(
        case,
        suppression=SuppressionList(
            [Suppression(symbol_pattern=pattern, reason="generated rule")]
        ),
    )
    moved = [c.kind for c in suppressed.findings] + [
        c.kind for c in suppressed.suppressed_findings
    ]
    assert sorted(k.value for k in moved) == sorted(
        k.value for k in (c.kind for c in baseline.findings)
    ), f"{case}/{pattern}: suppression changed the total finding set"


@given(
    pattern=st.sampled_from([".*", "_Z.*", "nomatch_.*"]),
    case=st.sampled_from([c for c, _ in _FIXTURES]),
)
@_HSETTINGS
def test_a_suppressed_finding_never_gates(pattern: str, case: str) -> None:
    """Suppression may change what is *reported*, never the exit code here.

    An audit's findings do not gate at all, so hiding one cannot change the
    outcome — if it could, suppression would be silently load-bearing for
    CI, which is the opposite of advisory.
    """
    baseline = compute_no_baseline_document(_result(case))
    suppressed = compute_no_baseline_document(
        _result(
            case,
            suppression=SuppressionList(
                [Suppression(symbol_pattern=pattern, reason="generated rule")]
            ),
        )
    )
    assert baseline.exit_code == suppressed.exit_code


# ---------------------------------------------------------------------------
# The gating half: what happens when an orthogonal axis actually fires.
# ---------------------------------------------------------------------------


def _gated_result(case: str = "case143_audit_accidental_export"):
    """A real audit result whose evidence-contract axis has fired.

    Set on the ``DiffResult`` directly rather than driven through a live
    ``--depth build`` run, so the gating half is testable without a
    toolchain — this asserts what the *renderers* do with a non-zero exit,
    which is independent of which axis produced it.
    """
    result = _result(case)
    result.diff.evidence_contract_error = True
    return result


def test_a_gated_audit_fails_exactly_one_junit_case() -> None:
    """The other half of the JUnit rule: a real gate must be visible.

    Making findings passing (the fix for the reported bug) is only correct
    if the run's *own* failure still surfaces — otherwise JUnit would report
    a clean suite for a run that exited non-zero, which is the reported bug
    inverted.
    """
    result = _gated_result()
    doc = compute_no_baseline_document(result)
    assert doc.exit_code != 0, "fixture precondition: the axis must have fired"

    text, exit_code = render_no_baseline(result, "junit")
    assert exit_code == doc.exit_code
    root = ET.fromstring(text)
    failures = root.findall(".//failure")
    assert len(failures) == 1, "exactly the run-level gate, never a finding"
    assert root.find(".//testcase[@name='exit code']") is not None
    assert str(doc.exit_code) in (failures[0].text or "")


def test_a_gated_audit_still_reports_its_findings_as_passing() -> None:
    """A gate firing must not retroactively make advisory findings failures."""
    result = _gated_result()
    text, _ = render_no_baseline(result, "junit")
    root = ET.fromstring(text)
    for case in root.findall(".//testcase"):
        if case.get("name") == "exit code":
            continue
        assert case.find("failure") is None, (
            f"{case.get('classname')} became a failure because the run gated"
        )


@pytest.mark.parametrize("fmt", sorted(NO_BASELINE_SUPPORTED_FORMATS))
def test_every_format_reports_the_gated_exit_code(fmt: str) -> None:
    """ADR-068 D4 again, on the path where the number is not 0."""
    result = _gated_result()
    doc = compute_no_baseline_document(result)
    _, exit_code = render_no_baseline(result, fmt)
    assert exit_code == doc.exit_code != 0


def test_an_unknown_format_is_an_internal_error_not_a_silent_default() -> None:
    """``render_no_baseline`` must not quietly fall back to some format.

    The CLI rejects an unsupported format before reaching here, so an
    unknown value is a programming error — and returning markdown instead
    would hand a caller the wrong artifact under the name it asked for.
    """
    with pytest.raises(ValueError, match="unsupported --no-baseline format"):
        render_no_baseline(_result("case143_audit_accidental_export"), "html")


# ---------------------------------------------------------------------------
# Markdown structural integrity: a value the report does not control can
# never restructure the document that renders it.
# ---------------------------------------------------------------------------


_MARKDOWN_HOSTILE = st.text(
    alphabet=st.sampled_from(
        ["|", "`", "\n", "\r", "\t", "\x00", "#", "-", "\\", " ", "x", "—"]
    ),
    min_size=0,
    max_size=40,
)


def _markdown_finding_rows(text: str) -> list[str]:
    """Every row of the candidate-side findings table in *text*."""
    lines = text.splitlines()
    start = lines.index("## Candidate-side findings")
    rows = [
        line
        for line in lines[start:]
        if line.startswith("| ") and not line.startswith("| --- ")
    ]
    assert rows, text
    return rows


def _cell_count(row: str) -> int:
    """Cells in a Markdown table *row*, honoring backslash-escaped pipes.

    A deliberately independent oracle: it counts unescaped ``|`` separators
    from the rendered text rather than reusing ``md_cell``'s own escaping,
    so a bug in that helper cannot make this measurement agree with it.
    """
    count = 0
    escaped = False
    for ch in row.strip().strip("|"):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == "|":
            count += 1
    return count + 1


@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(description=_MARKDOWN_HOSTILE, symbol=_MARKDOWN_HOSTILE)
def test_a_hostile_finding_value_never_restructures_the_markdown_table(
    description: str, symbol: str
) -> None:
    """A detector's own text is data, never Markdown structure.

    Stated over generated adversarial values rather than one crafted
    string (AGENTS.md's bug-class rule): a finding's ``description``/
    ``symbol`` reach the renderer from a detector, a demangler, or a
    header path, so *any* pipe, newline, backtick, or control character in
    them must land inside one cell — never end the row early, split the
    table, or leave the code span open for the rest of the document. The
    invariant is structural (every row has the header's cell count, and
    the table stays one contiguous block), not a golden string.
    """
    result = _result("case143_audit_accidental_export")
    findings = list(result.findings)
    assert findings, "fixture must carry at least one finding to perturb"
    findings[0].description = description
    findings[0].symbol = symbol

    text, _ = render_no_baseline(result, "markdown")
    rows = _markdown_finding_rows(text)
    header_cells = _cell_count(rows[0])
    assert header_cells == 5, rows[0]
    for row in rows[1:]:
        assert _cell_count(row) == header_cells, (row, description, symbol)
    # One contiguous table: no generated newline may split it apart.
    assert len(rows) == 1 + len(findings), (rows, description, symbol)


# ---------------------------------------------------------------------------
# Every nonzero exit is explained, on every axis and in every format.
# ---------------------------------------------------------------------------


#: Per axis: the fixture that gates on it, and whether the run needs
#: ``--require-complete-analysis``. Each entry is a *real* audit gated the
#: way a user would gate it, never a hand-built document -- a document
#: constructed with the axis pre-set would test the renderer against its own
#: assumption rather than against what the engine produces.
#: ``case145``'s assurance is genuinely ``partial`` (verified against the
#: committed fixture), which is what makes the second row bite instead of
#: skipping.
_GATED_AXIS_FIXTURES = {
    "evidence_contract": ("case143_audit_accidental_export", False),
    "analysis_assurance": ("case145_audit_unversioned_export", True),
}


def _axis_result(axis: str):
    """A result gated on *axis*, from the fixture that really gates on it."""
    case, _ = _GATED_AXIS_FIXTURES[axis]
    result = _result(case)
    if axis == "evidence_contract":
        # The one axis no committed fixture reaches on its own: it records a
        # *live extraction* falling short of a pinned depth, and every G20
        # fixture is a stored snapshot, which the floor deliberately exempts.
        # Set through the same flag the workflow sets.
        result.diff.evidence_contract_error = True
    return result


@pytest.mark.parametrize(
    ("axis", "require_complete"),
    [(axis, req) for axis, (_, req) in _GATED_AXIS_FIXTURES.items()],
)
def test_a_nonzero_exit_is_always_explained_in_the_report(
    axis: str, require_complete: bool
) -> None:
    """A gated audit must say which axis gated it — in text and in JSON.

    Stated over the axes themselves rather than over one reproducer: the
    exit code is a `max` over several orthogonal axes, so a projection that
    explains only the axis its author had in mind leaves every other axis
    silently gating (Codex review, P2 — the Markdown report exited 7 on a
    missed evidence contract while reporting only "no findings" and shallow
    evidence). The oracle is the document's own axis breakdown, which is the
    same mapping `no_baseline_exit_code` folds, so an axis added later is
    covered by the same assertion instead of needing a new one.
    """
    result = _axis_result(axis)
    doc = compute_no_baseline_document(
        result, require_complete_analysis=require_complete
    )
    contributing = {k for k, v in doc.exit_axes.items() if v}
    assert axis in contributing, (
        f"fixture for {axis} no longer gates on it — the row would pass "
        "vacuously, which is the failure this table exists to prevent"
    )
    assert doc.exit_code == max(doc.exit_axes.values())

    text, exit_code = render_no_baseline(
        result, "markdown", require_complete_analysis=require_complete
    )
    assert exit_code != 0
    for key in contributing:
        notice = NO_BASELINE_EXIT_AXIS_NOTICES[key]
        # The notice's own leading phrase, up to the em-dash separator.
        headline = notice.split(" -- ")[0]
        assert headline in text, (key, text)

    payload, _ = render_no_baseline(
        result, "json", require_complete_analysis=require_complete
    )
    assert json.loads(payload)["exit_axes"] == dict(doc.exit_axes)


def test_a_clean_audit_carries_no_axis_notice(
    case: str = "case143_audit_accidental_export",
) -> None:
    """The complement: notices appear only when an axis actually gated.

    Without this, "explain every nonzero exit" is trivially satisfied by
    printing every notice unconditionally, which would tell a reader their
    clean run failed five contracts.
    """
    result = _result(case)
    doc = compute_no_baseline_document(result)
    assert doc.exit_code == 0
    text, _ = render_no_baseline(result, "markdown")
    for notice in NO_BASELINE_EXIT_AXIS_NOTICES.values():
        assert notice.split(" -- ")[0] not in text


def test_a_coverage_gated_audit_publishes_the_ledger_that_gated_it() -> None:
    """A number is not actionable; the ledger behind it is.

    Under `--contract`, the audit kept only `contract_coverage: 1` — so even
    `--format json` exited 1 with no way to see which provider, on which
    side, fell short and why (Codex review, P2). The ledger is now derived
    from the run's own persisted contract context by the same function the
    two-sided report uses, so what a reader sees and what gated them come
    from one source.
    """
    result = _result("case143_audit_accidental_export")
    doc = compute_no_baseline_document(result)
    if not doc.contract_selected:
        # The committed fixtures carry no contract context; drive the
        # contract path explicitly rather than skipping, so this asserts.
        result = run_no_baseline_compare(
            resolve_no_baseline_candidate(
                example_catalog.case_dir("case143_audit_accidental_export")
                / "snapshot.abi.json"
            ),
            contract_evaluation=True,
            contract_mode="public",
        )
        doc = compute_no_baseline_document(result)

    assert doc.contract_selected, "the contract path must actually have run"
    payload, exit_code = render_no_baseline(result, "json")
    body = json.loads(payload)
    assert "contract_coverage_failures" in body, (
        "the ledger is emitted whenever a contract domain was selected — `[]` "
        "is the real 'this domain closed', which an absent key cannot express"
    )
    assert body["contract_coverage_failures"] == [
        dict(f) for f in doc.coverage_failures
    ]
    if exit_code:
        assert body["contract_coverage_failures"], (
            "a run gated on the coverage axis must list what fell short; "
            "publishing only the contribution is the defect this closes"
        )


def test_a_run_without_a_contract_omits_the_ledger_entirely() -> None:
    """The complement: no selected domain is not an empty one.

    `[]` means "the domain closed with nothing missing". A run that selected
    no domain has nothing to be short of, and must not claim otherwise.
    """
    result = _result("case143_audit_accidental_export")
    doc = compute_no_baseline_document(result)
    assert doc.contract_selected is False
    payload, _ = render_no_baseline(result, "json")
    assert "contract_coverage_failures" not in json.loads(payload)


def test_the_two_axis_tables_cover_the_same_axes() -> None:
    """A long notice and a short label for every axis, and no orphan of either.

    This is the drift guard the previous round needed and did not have: the
    Markdown fix added notices, and `oneline` kept printing a bare exit code
    because nothing tied the two projections to one list (Codex review, P2).
    Keyed identically, an axis added to one is now missing from the other
    loudly.
    """
    assert set(NO_BASELINE_EXIT_AXIS_LABELS) == set(NO_BASELINE_EXIT_AXIS_NOTICES)


@pytest.mark.parametrize(
    ("axis", "require_complete"),
    [(axis, req) for axis, (_, req) in _GATED_AXIS_FIXTURES.items()],
)
def test_every_text_projection_names_the_axis_that_gated(
    axis: str, require_complete: bool
) -> None:
    """Markdown *and* oneline must both say why, not just how much.

    Stated over both text projections at once rather than per-renderer: the
    defect was one renderer being fixed while its sibling silently kept
    dropping the cause.
    """
    result = _axis_result(axis)
    doc = compute_no_baseline_document(
        result, require_complete_analysis=require_complete
    )
    contributing = {k for k, v in doc.exit_axes.items() if v}
    assert axis in contributing

    for fmt in ("markdown", "oneline"):
        text, exit_code = render_no_baseline(
            result, fmt, require_complete_analysis=require_complete
        )
        assert exit_code != 0
        for key in contributing:
            phrase = NO_BASELINE_EXIT_AXIS_LABELS[key]
            headline = NO_BASELINE_EXIT_AXIS_NOTICES[key].split(" -- ")[0]
            assert phrase in text or headline in text, (fmt, key, text)


def test_a_clean_audit_oneline_names_no_axis() -> None:
    """The complement, so "name the axis" cannot become "always name them all"."""
    result = _result("case143_audit_accidental_export")
    text, exit_code = render_no_baseline(result, "oneline")
    assert exit_code == 0
    for phrase in NO_BASELINE_EXIT_AXIS_LABELS.values():
        assert phrase not in text


def test_the_audit_json_validates_against_its_own_published_schema() -> None:
    """The audit has a schema identity of its own, and it is a real one.

    It stamped `report_schema_version`, the compare report's field, whose
    schema tells consumers to accept any matching MAJOR — so an audit was
    offered under a different document's identity, and failed that schema on
    two counts: a null `verdict` there means ADR-050 D2's "comparability
    rejected" and requires a `reason` an audit must not claim, and
    `no_baseline` is outside the enum its `selection` field allows (Codex
    review, P1).

    Asserted both directions, since renaming the field alone would satisfy
    the negative half while leaving the audit unvalidatable.
    """
    jsonschema = pytest.importorskip("jsonschema")
    from abicheck.schemas import load_audit_report_schema, load_compare_report_schema

    result = _result("case143_audit_accidental_export")
    payload, _ = render_no_baseline(result, "json")
    doc = json.loads(payload)

    audit_errors = list(
        jsonschema.Draft202012Validator(load_audit_report_schema()).iter_errors(doc)
    )
    assert not audit_errors, [e.message for e in audit_errors]

    assert "report_schema_version" not in doc, (
        "the compare report's identity field must not appear on an audit"
    )
    assert doc["audit_report_schema_version"], "the audit carries its own version"
    compare_errors = list(
        jsonschema.Draft202012Validator(load_compare_report_schema()).iter_errors(doc)
    )
    assert compare_errors, (
        "an audit must not validate as a compare report — the two documents "
        "mean different things by a null verdict"
    )
