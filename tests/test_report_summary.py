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

"""``report_summary.build_summary``'s two computation paths (independently
resolved vs. reusing an envelope's already-resolved ``findings``, ADR-061
gap C) must always agree.

This is a real merge hazard, not a hypothetical: origin/main's PR #1193
("ADR061 report convergence") rewrote ``build_summary`` from scratch to add
the ``findings`` reuse path, and its rewritten return statement
reintroduced the pre-report-schema-4.0 ``compatible_additions=len(compatible)``
total -- silently reverting the round-10/11 ``compatible_additions``
correction (additions-only, excluding ``quality_issues``) that had landed on
this branch in the meantime. A 3-way merge of the two independent rewrites
auto-resolved everything except the two hunks that touched the same lines,
so the reintroduced regression would have gone unnoticed without a test
exercising both paths together over the exact shape that makes them
disagree: a ``COMPATIBLE`` finding whose raw kind is not a genuine addition.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change, DiffResult
from abicheck.report.finding import report_findings_for
from abicheck.report_summary import build_summary


def _mixed_result() -> DiffResult:
    # One genuine addition (FUNC_ADDED) and one COMPATIBLE, non-addition
    # quality-only kind (BRANCH_PROTECTION_IMPROVED) -- the shape that makes
    # `compatible_additions=len(compatible)` (wrong) diverge from
    # `compatible_additions=len(compatible) - quality_issues` (correct).
    return DiffResult(
        old_version="1",
        new_version="2",
        library="libdemo.so.1",
        changes=[
            Change(kind=ChangeKind.FUNC_ADDED, symbol="new_api", description="x"),
            Change(
                kind=ChangeKind.BRANCH_PROTECTION_IMPROVED,
                symbol="libdemo.so.1",
                description="x",
            ),
        ],
    )


def test_compatible_additions_excludes_quality_issues_without_findings() -> None:
    summary = build_summary(_mixed_result())
    assert summary.quality_issues == 1
    assert summary.compatible_additions == 1


def test_compatible_additions_excludes_quality_issues_with_findings() -> None:
    """The ADR-061 gap C envelope-reuse path (*findings* given) must reach
    the identical, correct answer -- not the pre-4.0 total."""
    result = _mixed_result()
    findings = report_findings_for(result)
    summary = build_summary(result, findings=findings)
    assert summary.quality_issues == 1
    assert summary.compatible_additions == 1


def test_both_paths_agree_on_the_same_result() -> None:
    """The two computation paths are independent implementations of the
    same contract; they must never disagree for the identical input."""
    result = _mixed_result()
    findings = report_findings_for(result)
    without_findings = build_summary(result)
    with_findings = build_summary(result, findings=findings)
    assert without_findings == with_findings
