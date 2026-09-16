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

"""The headline a ``compare --no-baseline`` audit gets.

This block was moved verbatim from ``pr_comment_render.py`` into
``report/pr_comment_headline.py``, which is what surfaced that it had no
test of its own driving ``headline()`` -- every existing no-baseline test
asserts the *report*, the exit code, or the Action's verdict string, none
of them the comment's first line. The logic is not new and not trivial: it
took eleven review rounds on PR #1210, and three of its four outcomes exist
only because a specific wrong headline shipped and was caught.

**General invariant:** an audit headline is green *only* when the run
blocked nothing and produced nothing to show. Stated below as the complete
enumeration of the four outcomes against the two independent inputs that
choose between them -- whether the run blocked (the report's own
max-folded ``exit_code``, never re-derived from bucket membership) and
whether anything is renderable in the body -- rather than as one example
per branch.
"""

from __future__ import annotations

import pytest

from abicheck.pr_comment import build_model, render_comment

#: The four verdicts a no-baseline finding carries, and the bucket each
#: lands in. `safe`/`suppressed` are the two shapes that made round 11:
#: both leave `breaking`/`review` empty while the body still shows a
#: finding, so a bucket-derived headline called the run empty.
VERDICTS = ("BREAKING", "API_BREAK", "COMPATIBLE_WITH_RISK", "COMPATIBLE")


def audit_report(
    *,
    findings: int = 0,
    verdict: str = "BREAKING",
    exit_code: int = 0,
    audit_gate_exit: int = 0,
    suppressed_count: int = 0,
) -> dict[str, object]:
    """A `compare --no-baseline` audit report."""
    return {
        "audit_report_schema_version": "1.0",
        "library": "libthing.so",
        "new_version": "1.1",
        "exit_code": exit_code,
        "exit_axes": {"audit_gate": audit_gate_exit},
        "suppressed_count": suppressed_count,
        "findings": [
            {
                "kind": "exported_not_public",
                "symbol": f"thing_symbol_{i}",
                "verdict": verdict,
                "description": "Candidate-side finding",
            }
            for i in range(findings)
        ],
    }


def headline_of(report: dict[str, object]) -> str:
    """The comment's headline, through the real public path.

    The `## ` heading, not `splitlines()[0]` -- the body opens with the
    sticky-identity marker, so reading line 0 returns that comment for
    every input and would make the whole enumeration below vacuous.
    """
    body = render_comment(build_model(report), sha="0123456789ab", detail="full")
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings, f"no headline in rendered body:\n{body[:400]}"
    return headings[0]


class TestTheFourOutcomes:
    """Complete enumeration, against an oracle derived from the *inputs*."""

    @pytest.mark.parametrize("verdict", VERDICTS)
    @pytest.mark.parametrize("findings", [0, 1, 3])
    @pytest.mark.parametrize(
        ("exit_code", "audit_gate_exit"),
        [(0, 0), (1, 0), (4, 4), (1, 1), (7, 0)],
        ids=["clean", "other-axis-1", "gate-4", "gate-1", "evidence-contract-7"],
    )
    def test_the_headline_follows_blocking_then_emptiness(
        self, verdict: str, findings: int, exit_code: int, audit_gate_exit: int
    ) -> None:
        line = headline_of(
            audit_report(
                findings=findings,
                verdict=verdict,
                exit_code=exit_code,
                audit_gate_exit=audit_gate_exit,
            )
        )
        if exit_code > 0:
            # Blocking wins over everything, including an empty finding set:
            # `evidence_contract` (exit 7) blocks with nothing itemizable.
            assert "🛑" in line
            if audit_gate_exit > 0:
                assert "Audit gate" in line, (
                    "the gate axis fired, so the headline should name it"
                )
            else:
                # A different orthogonal axis blocked, so the headline must
                # not attribute it to the (opt-in) audit gate.
                assert "Audit gate" not in line
                assert "this run blocks the step" in line
        elif findings == 0:
            assert "✅" in line
            assert "no baseline to compare" in line
        else:
            assert "⚠️" in line
            assert "not gated" in line

    def test_a_blocking_run_never_reads_green(self) -> None:
        """The vacuity guard that matters: if the oracle above were reduced
        to a constant it would still pass, so state the one thing that must
        never happen, separately."""
        greens = [
            headline_of(
                audit_report(findings=n, verdict=v, exit_code=code, audit_gate_exit=g)
            )
            for n in (0, 1, 3)
            for v in VERDICTS
            for code, g in [(1, 0), (4, 4), (7, 0)]
        ]
        assert greens, "the sweep must actually produce cases"
        assert not [line for line in greens if "✅" in line]


class TestTheRoundElevenShapes:
    """A finding the buckets cannot see must still not read as 'nothing'."""

    def test_a_policy_downgraded_finding_keeps_the_headline_honest(self) -> None:
        """Reclassified to `compatible`, so it lands in `safe` -- neither
        `breaking` nor `review`. The body renders it, so the headline must
        not claim there was nothing to compare."""
        line = headline_of(audit_report(findings=2, verdict="COMPATIBLE"))
        assert "✅" not in line
        assert "not gated" in line

    def test_a_fully_suppressed_audit_keeps_the_headline_honest(self) -> None:
        """Every bucket empty, only `suppressed_count` non-zero."""
        line = headline_of(audit_report(findings=0, suppressed_count=5))
        assert "✅" not in line
        assert "not gated" in line

    def test_only_a_genuinely_empty_audit_reads_green(self) -> None:
        assert "✅" in headline_of(audit_report())


def test_the_fixture_produces_the_audit_shape() -> None:
    """Vacuity guard: every assertion rests on `build_model` taking the
    no-baseline branch, which it selects on this key alone."""
    model = build_model(audit_report(findings=1))
    assert model.no_baseline_audit is True
    assert "audit_report_schema_version" in audit_report()
