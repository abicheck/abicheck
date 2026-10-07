# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A Markdown kind rollup is a *partition*, never a drop -- checked through
the public renderer.

Bug class: a presentation stage that removes findings from one list on the
promise that another list carries them, where the other list is never
rendered. ``roll_up_large_kinds`` moved every finding of a flooded
non-gating kind out of its section's rows into ``ChangeGroup.rollups``,
but the ReportDocument projection copied only the rows, so those findings
vanished from Markdown with no count left behind. The unit tests of the
rollup itself all passed, because none of them rendered a report. The
oracle here is the input: for every kind, the rendered Markdown either
names every one of its findings or states a rollup count equal to the
number of findings of that kind.
"""

from __future__ import annotations

import re

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.report.kind_rollup import KIND_ROLLUP_THRESHOLD
from abicheck.reporter import to_markdown

# Non-gating kinds from each rolled-up section: deployment risk and quality.
_KINDS = (
    ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
    ChangeKind.EXPORTED_NOT_PUBLIC,
    ChangeKind.SONAME_BUMP_UNNECESSARY,
)
_SIZES = (
    1,
    KIND_ROLLUP_THRESHOLD,
    KIND_ROLLUP_THRESHOLD + 1,
    3 * KIND_ROLLUP_THRESHOLD,
)
_ROLLUP_LINE = re.compile(
    r"^- \*\*(?P<kind>[a-z0-9_]+)\*\*: (?P<count>[\d,]+) findings", re.M
)


def _render(counts: dict[ChangeKind, int]) -> str:
    changes = [
        Change(
            kind=kind, symbol=f"{kind.value}_sym_{i}", description=f"{kind.value} {i}"
        )
        for kind, n in counts.items()
        for i in range(n)
    ]
    result = DiffResult(
        old_version="1",
        new_version="2",
        library="libfoo.so",
        changes=changes,
        verdict=Verdict.COMPATIBLE_WITH_RISK,
    )
    return to_markdown(result)


def _assert_partition(md: str, counts: dict[ChangeKind, int]) -> None:
    rolled = {
        m["kind"]: int(m["count"].replace(",", "")) for m in _ROLLUP_LINE.finditer(md)
    }
    for kind, n in counts.items():
        if kind.value in rolled:
            assert rolled[kind.value] == n, (kind, rolled[kind.value], n)
        else:
            missing = [i for i in range(n) if f"{kind.value}_sym_{i}" not in md]
            assert not missing, (
                kind,
                n,
                f"{len(missing)} findings neither listed nor counted",
            )


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("n", _SIZES)
def test_every_finding_is_listed_or_counted(kind: ChangeKind, n: int) -> None:
    counts = {kind: n}
    md = _render(counts)
    _assert_partition(md, counts)
    if n > KIND_ROLLUP_THRESHOLD:
        assert f"**{kind.value}**: {n:,} findings" in md


@pytest.mark.parametrize(
    "sizes", [(1, 2 * KIND_ROLLUP_THRESHOLD, KIND_ROLLUP_THRESHOLD + 1), (40, 3, 26)]
)
def test_mixed_kinds_in_one_report_are_each_conserved(sizes: tuple[int, ...]) -> None:
    counts = dict(zip(_KINDS, sizes, strict=True))
    _assert_partition(_render(counts), counts)
