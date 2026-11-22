# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""HTML states ``consumer_impact_summary`` ("N of M consumers affected").

JSON, SARIF and JUnit already carry it for a ``--used-by`` run; the HTML
consumer-scoped box did not, so an HTML reader saw a scoped verdict with no
way to tell one affected consumer from all of them. The oracle is the
summary dict itself (what JSON emits), checked across several shapes,
including unreadable consumers and a ``required_symbols``-only run.
"""

from __future__ import annotations

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.report.html_compute import generate_html_report


def _scoped(summary: dict | None, *, used_by: bool = True) -> DiffResult:
    result = DiffResult(
        old_version="1",
        new_version="2",
        library="libx.so",
        changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")],
        verdict=Verdict.BREAKING,
    )
    result.scoped_verdict = Verdict.BREAKING  # type: ignore[attr-defined]
    if used_by:
        result.used_by = [{"app": "/bin/a"}]  # type: ignore[attr-defined]
    else:
        result.required_symbols = {"verdict": "BREAKING"}  # type: ignore[attr-defined]
    result.consumer_impact_summary = summary  # type: ignore[attr-defined]
    return result


@pytest.mark.parametrize(
    "summary",
    [
        {
            "total": 1,
            "evaluated": 1,
            "affected": 1,
            "unreadable_advisory": 0,
            "unreadable_paths": [],
        },
        {
            "total": 5,
            "evaluated": 5,
            "affected": 0,
            "unreadable_advisory": 0,
            "unreadable_paths": [],
        },
        {
            "total": 7,
            "evaluated": 4,
            "affected": 2,
            "unreadable_advisory": 3,
            "unreadable_paths": ["/opt/a", "/opt/b", "/opt/c"],
        },
    ],
)
def test_html_states_n_of_m_consumers_affected(summary: dict) -> None:
    out = generate_html_report(_scoped(summary))
    assert (
        f"{summary['affected']} of {summary['evaluated']} evaluated consumers "
        f"affected ({summary['total']} supplied)"
    ) in out
    for path in summary["unreadable_paths"]:
        assert path in out
    assert ("unreadable, not evaluated" in out) == bool(summary["unreadable_paths"])


def test_no_consumer_impact_line_without_used_by() -> None:
    out = generate_html_report(
        _scoped({"total": 1, "evaluated": 1, "affected": 1}, used_by=False)
    )
    assert "Consumer-scoped verdict" in out
    assert "consumers affected" not in out
