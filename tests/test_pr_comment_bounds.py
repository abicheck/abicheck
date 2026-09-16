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

"""What the PR comment is allowed to leave out, and what it never may.

Split from ``test_pr_comment.py`` (at its `architecture/debt.yaml`
no-growth baseline) because these are one subject rather than more of that
file's general rendering coverage: every case here is a *bound* the comment
applies -- a row budget, a per-kind symbol cap, a redundant-value
suppression -- and the property under test is the same each time. A bound
must cut only what the reader can still account for, and must never cut a
value the row is the authoritative source of.

Both defects these cover were found by review on PR #1304: the
informational section obeyed no row budget at all in standard detail, and
the redundant-delta suppression used a substring test that a *different*
transition could satisfy.
"""

from __future__ import annotations

import pytest


class TestDeltaSuppressionRequiresValueBoundaries:
    """A row's authoritative old/new values are suppressed only when the
    description really states *that* transition.

    `delta in desc` is a substring test and a transition is not a
    substring-safe token: `"0 → 1"` occurs inside `"10 → 11"`. A description
    mentioning any other numeric transition therefore hid the row's own
    values (CodeRabbit review). These enumerate the boundary cases rather
    than pinning the one reported string, and assert both directions --
    hiding a real delta is as wrong as showing a redundant one.
    """

    @pytest.mark.parametrize(
        ("desc", "delta", "expected"),
        [
            # The reported defect and its siblings: a different transition
            # that merely contains this one.
            ("struct size changed 10 → 11", "0 → 1", False),
            ("align 20 → 18", "2 → 1", False),
            ("type uint → longer", "int → long", False),
            ("offset 100 → 104", "0 → 10", False),
            # Genuine statements of the same transition, in the shapes a
            # description actually takes.
            ("size changed 0 → 1", "0 → 1", True),
            ("0 → 1", "0 → 1", True),
            ("changed (0 → 1)", "0 → 1", True),
            ("return type int → long", "int → long", True),
            # A decoy before the real one must not mask it.
            ("offset 100 → 104 and 0 → 1", "0 → 1", True),
            # Nothing to match.
            ("no transition here", "0 → 1", False),
            ("", "0 → 1", False),
        ],
    )
    def test_boundary_cases(self, desc: str, delta: str, expected: bool) -> None:
        from abicheck.pr_comment import _states_delta

        assert _states_delta(desc, delta) is expected

    def test_the_predicate_is_not_a_constant(self) -> None:
        """Vacuity guard: a predicate stuck at either constant would pass a
        one-sided table, so both answers must be reachable."""
        from abicheck.pr_comment import _states_delta

        assert _states_delta("size 0 → 1", "0 → 1") is True
        assert _states_delta("size 10 → 11", "0 → 1") is False


class TestInformationalSectionObeysTheRowBudget:
    """The standard-detail informational section is bounded too.

    It capped symbols *within* each (component, kind) group but emitted
    every group, so a report with many distinct groups grew without limit
    while the comment budget was being tightened around it — the identical
    defect the full-detail branch of the same section was already fixed for
    (CodeRabbit review). Asserted over group counts spanning the cap rather
    than one fixed report, with the omission disclosed in findings.
    """

    @staticmethod
    def _findings(n_groups: int, per_group: int = 2) -> list:
        from abicheck.pr_comment_base import Finding

        return [
            Finding(
                kind=f"quality_kind_{g}",
                symbol=f"sym_{g}_{i}",
                detail="d",
                severity="compatible",
            )
            for g in range(n_groups)
            for i in range(per_group)
        ]

    @pytest.mark.parametrize("n_groups", [1, 5, 25, 26, 60])
    def test_groups_are_capped_and_the_remainder_disclosed(self, n_groups: int) -> None:
        from abicheck.pr_comment_render import (
            _SAFE_SYMBOLS_PER_KIND,
            _STANDARD_ROW_CAP,
            _safe_section,
        )

        per_group = 2
        assert per_group <= _SAFE_SYMBOLS_PER_KIND  # isolate the group cap
        findings = self._findings(n_groups, per_group)
        text = "\n".join(_safe_section(findings, "standard"))

        shown_groups = min(n_groups, _STANDARD_ROW_CAP)
        emitted = sum(1 for g in range(n_groups) if f"quality_kind_{g}`:" in text)
        assert emitted == shown_groups, (
            f"{n_groups} groups rendered {emitted}, expected {shown_groups}"
        )
        dropped = (n_groups - shown_groups) * per_group
        if dropped:
            assert f"… {dropped} more informational findings omitted." in text
        else:
            assert "more informational finding" not in text

    def test_an_explicit_row_cap_tightens_the_section_further(self) -> None:
        """A budget being tightened must actually reach this section."""
        from abicheck.pr_comment_render import _safe_section

        findings = self._findings(30, 2)
        text = "\n".join(_safe_section(findings, "standard", row_cap=3))
        emitted = sum(1 for g in range(30) if f"quality_kind_{g}`:" in text)
        assert emitted == 3
        assert "… 54 more informational findings omitted." in text

    def test_the_heading_total_stays_complete_when_capped(self) -> None:
        """The cap bounds the body, never the stated total."""
        from abicheck.pr_comment_render import _safe_section

        findings = self._findings(40, 2)
        text = "\n".join(_safe_section(findings, "standard"))
        assert "Informational findings (80)" in text
