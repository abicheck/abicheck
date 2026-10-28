# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The ``--no-baseline`` Markdown audit stays inside the Markdown size budget.

Bug class ``report.markdown_size_unbounded_by_finding_count``: the audit's
candidate-side findings table and its suppressed-findings table itemised one
row per finding, so 5,000 additions rendered ~400 KB of Markdown. The audit
gate fires only on a BREAKING/API_BREAK verdict (``policy/audit_gate_exit``),
so every other row is non-gating and may be summarised past the shared budget,
with the count stated and the JSON field that holds the rest named.

The document is a real audit document (``compute_no_baseline_document`` over a
real run) with a synthetic finding and suppression population attached, so the
renderer under test is the production one.
"""

from __future__ import annotations

import dataclasses
import re
from functools import cache

from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.declarations import Function
from abicheck.policy.classification import Verdict
from abicheck.policy.severity import IssueCategory
from abicheck.report.finding import ReportFinding
from abicheck.report.no_baseline import (
    compute_no_baseline_document,
    render_no_baseline_markdown,
)
from abicheck.report.no_baseline_document import NoBaselineDocument, SuppressedFinding
from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

#: A GitHub PR comment's limit, written as a literal (not re-read from the code).
HUMAN_BUDGET = 64 * 1024
ADDITIONS = 5000
BREAKS = 3
SUPPRESSED = 400


@cache
def _base() -> NoBaselineDocument:
    snapshot = AbiSnapshot(
        version="2.0",
        library="libfoo.so",
        functions=[Function(name="k", mangled="_Zk", return_type="void", params=[])],
        variables=[],
        types=[],
        enums=[],
    )
    return compute_no_baseline_document(run_no_baseline_compare(snapshot))


def _finding(
    index: int, verdict: Verdict, category: IssueCategory, kind: ChangeKind
) -> ReportFinding:
    change = Change(
        kind,
        f"_Zsym{index}",
        f"candidate {index}",
        qualified_name=f"sym{index}",
        demangled_symbol=f"sym{index}",
    )
    return ReportFinding(change=change, verdict=verdict, category=category)


def _document() -> NoBaselineDocument:
    findings = tuple(
        _finding(i, Verdict.COMPATIBLE, IssueCategory.ADDITION, ChangeKind.FUNC_ADDED)
        for i in range(ADDITIONS)
    ) + tuple(
        _finding(
            ADDITIONS + i,
            Verdict.BREAKING,
            IssueCategory.ABI_BREAKING,
            ChangeKind.FUNC_REMOVED,
        )
        for i in range(BREAKS)
    )
    suppressed = tuple(
        SuppressedFinding(
            change=Change(
                ChangeKind.FUNC_ADDED,
                f"_Zhidden{i}",
                "hidden",
                qualified_name=f"hidden{i}",
                demangled_symbol=f"hidden{i}",
            ),
            verdict=Verdict.COMPATIBLE,
            category=IssueCategory.ADDITION,
            provenance={
                "reason": "waived",
                "source_file": "waivers.yaml",
                "expires": None,
            },
        )
        for i in range(SUPPRESSED)
    )
    return dataclasses.replace(_base(), findings=findings, suppressed=suppressed)


def _rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("| `func_")]


def test_findings_table_is_bounded_and_counts_every_cut_finding() -> None:
    text = render_no_baseline_markdown(_document())
    assert len(text.encode()) <= HUMAN_BUDGET, len(text.encode())

    findings_section = text.split("## Suppressed findings", 1)[0]
    shown = [line for line in _rows(findings_section) if "`func_added`" in line]
    breaks = [line for line in _rows(findings_section) if "`func_removed`" in line]
    stated = sum(
        int(n)
        for n in re.findall(r"… and (\d+) more non-gating finding", findings_section)
    )
    # Every gating break is itemised; the additions are shown up to the budget
    # and the rest are counted.
    assert len(breaks) == BREAKS
    assert len(shown) + stated == ADDITIONS
    assert "JSON `findings`" in findings_section


def test_suppressed_table_is_bounded_and_counts_every_cut_entry() -> None:
    text = render_no_baseline_markdown(_document())
    suppressed_section = text.split("## Suppressed findings", 1)[1].split("\n## ", 1)[0]
    shown = _rows(suppressed_section)
    stated = sum(
        int(n)
        for n in re.findall(r"… and (\d+) more non-gating finding", suppressed_section)
    )
    assert len(shown) + stated == SUPPRESSED
    assert "JSON `suppressed_findings`" in suppressed_section
