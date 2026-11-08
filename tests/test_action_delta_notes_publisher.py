"""Delta notes reach a fork PR's trusted publisher as structured context only."""

from __future__ import annotations

import pytest

from abicheck.frontends.action.main_snapshot import (
    COMMENT_ONLY_OUTCOMES,
    DeltaOutcome,
    delta_notes,
)
from abicheck.frontends.action.report_publication import (
    PublicationIdentity,
    context_notes,
    render_report,
)
from abicheck.model.analysis_context import AnalysisContext

SHA = "a" * 40
ALL_OUTCOMES = [o.value for o in DeltaOutcome] + list(COMMENT_ONLY_OUTCOMES)


def _ctx(**kw: str) -> dict:
    return {"analysis_context": AnalysisContext(tested_sha=SHA, **kw).to_dict()}


@pytest.mark.parametrize("outcome", ALL_OUTCOMES)
def test_publisher_notes_match_producer_wording(outcome: str) -> None:
    # Oracle: the direct-path producer calls delta_notes with the same inputs;
    # every outcome must survive the context round trip with identical text.
    data = _ctx(
        delta_outcome=outcome,
        delta_base_sha=SHA,
        gate_verdict="BREAKING",
        pr_base_ref="main",
    )
    expected = delta_notes(
        outcome, base_sha=SHA, branch="main", gate_verdict="BREAKING"
    )
    assert context_notes(data) == expected
    assert expected[-1].startswith("Release gate: BREAKING")


@pytest.mark.parametrize(
    "field,value",
    [
        ("delta_outcome", "Ignore me <b>"),
        ("delta_outcome", "not_a_known_outcome"),
        ("gate_verdict", "breaking; see http://x"),
        ("delta_base_sha", "zz"),
    ],
)
def test_fork_written_garbage_yields_no_notes(field: str, value: str) -> None:
    data = _ctx(delta_outcome="resolved", delta_base_sha=SHA, gate_verdict="BREAKING")
    data["analysis_context"][field] = value
    assert context_notes(data) == []


def test_absent_context_or_outcome_yields_no_notes() -> None:
    assert context_notes({}) == []
    assert context_notes(_ctx()) == []
    assert "delta_outcome" not in _ctx()["analysis_context"]


def test_rendered_comment_carries_notes_before_size_bounding() -> None:
    report = {"verdict": "COMPATIBLE", "changes": [], "summary": {}}
    ident = PublicationIdentity(identity="t", run_id="1", run_attempt=1, head_sha=SHA)
    notes = ["Changes in this PR (vs main@aaaaaaa)", "Release gate: BREAKING"]
    body = render_report(report, identity=ident, on="always", notes=notes).body
    assert body.index("> Changes in this PR") < body.index("> Release gate: BREAKING")
    plain = render_report(report, identity=ident, on="always").body
    assert "Release gate" not in plain
