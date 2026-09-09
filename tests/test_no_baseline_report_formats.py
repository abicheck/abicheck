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
