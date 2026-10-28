# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Every human-facing renderer stays inside one budget as the finding count grows.

Bug class ``report.markdown_size_unbounded_by_finding_count``: the Markdown
size budget (``report/markdown_budget.py``) bounded the full Markdown view,
but two other human renderings of the same finding set still itemised one row
per finding: the root-cause Markdown view (a 5,000-addition run rendered
~580 KB) and the HTML report (~1.2 MB). The non-gating part of those is now
capped by the same product rule (``report/gating_budget.py``).

The test parametrizes over the dispatch table in ``frontends/render.py``
(``_PROJECTIONS``) plus Markdown's root-cause mode. Each format is rendered from
one completed comparison of 3 gating breaks and 5,000 non-gating additions, and:

* a human format stays under :data:`HUMAN_BUDGET` bytes (a PR comment's limit);
* the findings a format shows plus the omission count it states equal the
  population, so a cut is always counted, never silent;
* a machine format (JSON, SARIF, JUnit) carries every finding, unbounded by
  design (``markdown_budget``'s own contract).

A gating finding is always itemised; the 3 breaks are therefore shown in every
human format, and the test asserts that too.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # noqa: S405  # nosec B405 (trusted, self-rendered)
from collections.abc import Callable
from functools import cache

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.frontends.render import _PROJECTIONS, render_output
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change

#: The documented cap for a human rendering: a GitHub PR comment's 64 KiB.
#: Written as a literal so the test does not re-read an implementation constant.
HUMAN_BUDGET = 64 * 1024
GATING = 3
NON_GATING = 5000
TOTAL = GATING + NON_GATING

_NUM = r"([\d,]+)"


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def _result() -> DiffResult:
    changes: list[Change] = []
    for i in range(GATING):
        name = f"gone_{i}"
        changes.append(
            Change(
                ChangeKind.FUNC_REMOVED,
                f"_Z{name}",
                f"removed {name}",
                qualified_name=name,
                demangled_symbol=name,
            )
        )
    for i in range(NON_GATING):
        name = f"helper_{i}"
        changes.append(
            Change(
                ChangeKind.FUNC_ADDED,
                f"_Z{name}",
                f"added {name}",
                qualified_name=name,
                demangled_symbol=name,
            )
        )
    return DiffResult("1.0", "2.0", "libfoo.so", changes, Verdict.BREAKING)


@cache
def _rendered(fmt: str, mode: str) -> str:
    snapshot = AbiSnapshot(library="libfoo.so", version="1.0")
    new = AbiSnapshot(library="libfoo.so", version="2.0")
    return render_output(fmt, _result(), snapshot, new, report_mode=mode)


def _markdown_shown_and_stated(text: str) -> tuple[int, int]:
    """Itemised severity rows and the rolled-up counts stated for the rest."""
    # Up to the review-group section, whose own lines are not severity rows.
    section = text.split("## Related review groups", 1)[0]
    rollup = re.compile(rf"^- \*\*\w+\*\*: {_NUM} findings —", re.M)
    rolled = rollup.findall(section)
    itemised = [
        line
        for line in section.splitlines()
        if line.startswith("- **") and rollup.match(line) is None
    ]
    return len(itemised), sum(_int(n) for n in rolled)


def _review_shown_and_stated(text: str) -> tuple[int, int]:
    shown = len(re.findall(r"^\d+\. \*\*", text, re.M))
    stated = sum(
        _int(n) for n in re.findall(rf"… {_NUM} more group\(s\) omitted", text)
    )
    return shown, stated


def _terminal_shown_and_stated(text: str) -> tuple[int, int]:
    shown = len(re.findall(r"^\d+\. \S+: ", text, re.M))
    stated = sum(
        _int(n) for n in re.findall(rf"\.\.\. {_NUM} more groups omitted", text)
    )
    return shown, stated


def _root_cause_shown_and_stated(text: str) -> tuple[int, int]:
    section = text.split("## Root Causes", 1)[1].split("\n---\n", 1)[0]
    shown = len(re.findall(r"^- \*\*", section, re.M))
    stated = sum(
        _int(n) for n in re.findall(rf"… and {_NUM} more non-gating finding", section)
    ) + sum(_int(n) for n in re.findall(rf"holding {_NUM} non-gating finding", section))
    return shown, stated


def _html_shown_and_stated(text: str) -> tuple[int, int]:
    shown = 0
    stated = 0
    for block in text.split("<div class='section section-")[1:]:
        if "<table class='changes'>" not in block:
            continue
        # One symbol cell per itemised change row.
        shown += len(re.findall(r"<td class='sym'>", block))
        stated += sum(
            _int(n) for n in re.findall(rf"… and {_NUM} more non-gating finding", block)
        )
    return shown, stated


def _html_population(text: str) -> int:
    """The headline count of every changes table: what the page says it holds."""
    total = 0
    for block in text.split("<div class='section section-")[1:]:
        heading = re.search(rf"<h3>[^<]*\({_NUM}\)</h3>", block)
        if heading is not None and "<table class='changes'>" in block:
            total += _int(heading.group(1))
    return total


def _markdown_population(text: str) -> int:
    return _int(re.search(rf"\| Detected \(raw\) \| {_NUM} \|", text).group(1))


def _review_population(text: str) -> int:
    return _int(re.search(rf"Count populations:\*\* {_NUM} detected", text).group(1))


def _terminal_population(text: str) -> int:
    return _int(re.search(rf"Findings: {_NUM} detected", text).group(1))


#: Per human format: the population it draws from, and its shown + stated split.
_HUMAN: dict[str, tuple[Callable[[str], int], Callable[[str], tuple[int, int]]]] = {
    "markdown": (_markdown_population, _markdown_shown_and_stated),
    "md": (_markdown_population, _markdown_shown_and_stated),
    "review": (_review_population, _review_shown_and_stated),
    "terminal": (_terminal_population, _terminal_shown_and_stated),
    "html": (_html_population, _html_shown_and_stated),
}
_MACHINE = frozenset({"json", "sarif", "junit"})


def _human_cases() -> list[tuple[str, str]]:
    cases = [(fmt, "full") for fmt in sorted(_PROJECTIONS) if fmt not in _MACHINE]
    cases.append(("markdown", "root-cause"))
    return cases


def test_every_dispatch_format_is_classified() -> None:
    """A format added to the dispatch table fails here until it has a budget oracle."""
    assert set(_PROJECTIONS) == set(_HUMAN) | _MACHINE, sorted(_PROJECTIONS)


@pytest.mark.parametrize(("fmt", "mode"), _human_cases())
def test_human_rendering_is_bounded_and_accounts_for_every_finding(
    fmt: str, mode: str
) -> None:
    text = _rendered(fmt, mode)
    assert len(text.encode()) <= HUMAN_BUDGET, (fmt, mode, len(text.encode()))

    if mode == "root-cause":
        population = _markdown_population(_rendered("markdown", "full"))
        shown, stated = _root_cause_shown_and_stated(text)
    else:
        population_of, split = _HUMAN[fmt]
        population = population_of(text)
        shown, stated = split(text)

    assert population == TOTAL, (fmt, mode, population)
    assert shown + stated == population, (fmt, mode, shown, stated)
    # The gating breaks are never cut: each human rendering itemises all of them.
    assert all(f"gone_{i}" in text for i in range(GATING)), (fmt, mode)


@pytest.mark.parametrize("fmt", sorted(_MACHINE))
def test_machine_rendering_carries_every_finding(fmt: str) -> None:
    text = _rendered(fmt, "full")
    if fmt == "json":
        assert len(json.loads(text)["changes"]) == TOTAL
    elif fmt == "sarif":
        results = json.loads(text)["runs"][0]["results"]
        assert len(results) == TOTAL
    else:
        testcases = [el for el in ET.fromstring(text).iter() if el.tag == "testcase"]  # noqa: S314
        assert len(testcases) == TOTAL
