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

"""The composite Action's mapping for ADR-065 S2's completeness axis.

A directory/package ``compare`` now exits ``1`` for an incompletely checked
scope under ``--on-incomplete-scope block`` and for a run that completed no
comparison at all (D7). Before this mapping, ``action/run.sh`` attributed
such an exit to P0.4's analysis-assurance axis (the last ``else`` of its
exit-1 branch) and told the reader to drop ``--require-complete-analysis``
-- a flag the run never used. Same stub-binary harness as
``test_action_coverage_verdict.py``, so ``run.sh`` itself is what runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_action_coverage_verdict import (  # noqa: F401
    _lib,
    _run_action,
    _stub_abicheck,
    pytestmark,
)


def _release_report(
    *,
    incomplete_scope: int,
    no_comparison: int,
    unchecked: list[str],
    verdict: str = "NO_CHANGE",
) -> dict:
    return {
        "report_schema_version": "2.50",
        "verdict": verdict,
        "libraries": [],
        "unmatched_old": unchecked,
        "unmatched_new": [],
        "exit": {
            "code": max(incomplete_scope, no_comparison),
            "reasons": ["incomplete_scope"]
            if incomplete_scope
            else ["no_comparison_completed"],
            "compatibility_contribution": 0,
            "contract_coverage_contribution": 0,
            "analysis_assurance_contribution": 0,
            "crosscheck_promotion_contribution": 0,
            "operational_error_contribution": 0,
            "evidence_contract_error_contribution": 0,
            "budget_overflow_contribution": 0,
            "not_comparable_contribution": 0,
            "removed_required_library_contribution": 0,
            "incomplete_scope_contribution": incomplete_scope,
            "no_comparison_completed_contribution": no_comparison,
        },
        "run_outcome": {
            "schema_version": "1.0",
            "compatibility": None if no_comparison else verdict,
            "assurance": None,
            "gate": "none",
            "operational": "no_comparison_completed" if no_comparison else "none",
            "lifecycle": "existing",
            "scope": "incomplete",
        },
        "comparison_scope": {
            "completeness": "incomplete",
            "policy": "block" if incomplete_scope else "warn",
            "incomplete_scope_exit_contribution": incomplete_scope,
            "no_comparison_completed": bool(no_comparison),
            "no_comparison_completed_exit_contribution": no_comparison,
            "unchecked": unchecked,
            "members": [],
            "proven_removed": [],
            "proven_added": [],
        },
    }


def _compare_outputs(
    tmp_path: Path, report: dict, *, exit_code: int = 1, stderr: str = ""
) -> dict:
    bindir = _stub_abicheck(tmp_path, exit_code=exit_code, report=report, stderr=stderr)
    return _run_action(
        tmp_path,
        {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
            "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
        },
        bindir,
    )


class TestCompareMapsTheCompletenessExit:
    def test_block_policy_is_scope_incomplete_not_analysis_incomplete(
        self, tmp_path: Path
    ) -> None:
        report = _release_report(
            incomplete_scope=1, no_comparison=0, unchecked=["libb.so"]
        )
        outputs = _compare_outputs(tmp_path, report)
        assert outputs["verdict"] == "SCOPE_INCOMPLETE", outputs
        assert outputs["_exit"] == 1, outputs
        assert "SCOPE_INCOMPLETE" in outputs["_summary"]
        assert "libb.so" in outputs["_summary"]
        assert "require-complete-analysis" not in outputs["_summary"]

    @pytest.mark.parametrize(
        ("incomplete_scope", "no_comparison"), [(1, 0), (0, 1)], ids=["block", "none"]
    )
    def test_a_stored_baseline_report_without_an_exit_block_is_scope_incomplete(
        self, tmp_path: Path, incomplete_scope: int, no_comparison: int
    ) -> None:
        """The stored-baseline dispatch (`compare_bundle_facts.py`) emits no
        root `exit` block; its `comparison_scope` section carries the same
        contributions, and the Action must read them from there rather than
        publishing SEVERITY_ERROR (Codex review)."""
        report = _release_report(
            incomplete_scope=incomplete_scope,
            no_comparison=no_comparison,
            unchecked=["libb.so"],
        )
        del report["exit"]
        outputs = _compare_outputs(tmp_path, report)
        assert outputs["verdict"] == "SCOPE_INCOMPLETE", outputs
        assert outputs["_exit"] == 1, outputs
        assert "SCOPE_INCOMPLETE" in outputs["_summary"]
        assert "SEVERITY_ERROR" not in outputs["_stdout"]

    def test_no_comparison_completed_fails_the_step(self, tmp_path: Path) -> None:
        report = _release_report(
            incomplete_scope=0, no_comparison=1, unchecked=["liba.so"]
        )
        outputs = _compare_outputs(tmp_path, report)
        assert outputs["verdict"] == "SCOPE_INCOMPLETE", outputs
        assert outputs["_exit"] == 1, outputs
        assert "no comparison completed" in outputs["_summary"]

    def test_warn_policy_stays_green_and_unlabelled(self, tmp_path: Path) -> None:
        """Under the default `warn` the CLI exits 0 and both contributions are
        0; the Action must not invent a red check from the incomplete scope
        the report still (honestly) records."""
        report = _release_report(
            incomplete_scope=0, no_comparison=0, unchecked=["libb.so"]
        )
        outputs = _compare_outputs(tmp_path, report, exit_code=0)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs
        assert "SCOPE_INCOMPLETE" not in outputs["_summary"]

    @pytest.mark.parametrize("event", ["push", "pull_request"])
    def test_a_warn_accepted_gap_is_still_named_in_the_summary(
        self, tmp_path: Path, event: str
    ) -> None:
        """Codex review, fifteenth round: under `warn` nothing gates, but the
        step summary (the only UI on a push, or with PR comments off) must
        still say the scope was not fully checked and name the unchecked
        member, rather than read as a plain COMPATIBLE."""
        report = _release_report(
            incomplete_scope=0, no_comparison=0, unchecked=["libb.so"]
        )
        bindir = _stub_abicheck(tmp_path, exit_code=0, report=report)
        env = {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
            "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            "GITHUB_EVENT_NAME": event,
        }
        if event == "pull_request":
            event_path = tmp_path / "event.json"
            event_path.write_text(
                '{"pull_request": {"number": 7, "head": {"sha": "abc123"}}}',
                encoding="utf-8",
            )
            env["GITHUB_EVENT_PATH"] = str(event_path)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs
        summary = outputs["_summary"]
        assert "not fully checked" in summary, summary
        assert "libb.so" in summary, summary
        assert "SCOPE_INCOMPLETE" not in summary

    @pytest.mark.parametrize("event", ["push", "pull_request"])
    def test_no_json_report_still_fails_the_step_but_claims_no_scope_gate(
        self, tmp_path: Path, event: str
    ) -> None:
        """With no readable JSON report the axis has no structured signal,
        and the stderr notice is deliberately *not* consulted (its member
        reasons can carry PR-controlled text -- Codex review): the CLI's
        exit 1 still fails the step, but the Action claims no scope gate.
        Runs under both event names because the `pull_request` PR-comment
        re-run leaves a `{}` placeholder in PR_JSON, which must read as
        "cannot tell" rather than "did not fire" either way."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=None,
            stderr=(
                "Comparison scope incompletely checked -- unchecked: unsupported: "
                "libb.so. Exit code floored to 1 by --on-incomplete-scope block "
                "(ADR-065 completeness axis)."
            ),
        )
        env = {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "markdown",
            "GITHUB_EVENT_NAME": event,
        }
        if event == "pull_request":
            event_path = tmp_path / "event.json"
            event_path.write_text(
                '{"pull_request": {"number": 7, "head": {"sha": "abc123"}}}',
                encoding="utf-8",
            )
            env["GITHUB_EVENT_PATH"] = str(event_path)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["_exit"] == 1, outputs
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs
        assert "comparison scope" not in outputs["_stdout"], outputs

    def test_a_forged_stderr_notice_cannot_suppress_a_reported_block(
        self, tmp_path: Path
    ) -> None:
        """The structured report says the scope blocked; a member reason that
        spells the `warn`-accepted phrase in stderr must not talk the gate
        out of it (the forgery Codex described)."""
        report = _release_report(
            incomplete_scope=1, no_comparison=0, unchecked=["libb.so"]
        )
        outputs = _compare_outputs(
            tmp_path,
            report,
            stderr=(
                "Comparison scope incompletely checked -- unchecked: unsupported: "
                "Accepted by --on-incomplete-scope warn.so (forged reason)."
            ),
        )
        assert outputs["verdict"] == "SCOPE_INCOMPLETE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_a_warn_accepted_stderr_notice_is_not_a_gate(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            report=None,
            stderr=(
                "Comparison scope incompletely checked -- unchecked: not_supplied: "
                "libb.so. Accepted by --on-incomplete-scope warn, so the scope axis "
                "contributes 0 to the exit code (ADR-065 completeness axis)."
            ),
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "markdown",
            },
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_breaking_release_still_reports_the_scope_alongside(
        self, tmp_path: Path
    ) -> None:
        """max-folded: BREAKING wins the verdict and exit 4, but the scope
        axis is still enforced and named, exactly like coverage."""
        report = _release_report(
            incomplete_scope=1,
            no_comparison=0,
            unchecked=["libb.so"],
            verdict="BREAKING",
        )
        report["exit"]["code"] = 4
        report["exit"]["reasons"] = ["compatibility_gate"]
        report["exit"]["compatibility_contribution"] = 4
        report["run_outcome"]["gate"] = "abi_breaking"
        outputs = _compare_outputs(tmp_path, report, exit_code=4)
        assert outputs["verdict"] == "BREAKING", outputs
        assert outputs["_exit"] == 1, outputs
        assert "comparison scope also contributed" in outputs["_summary"].lower()


class TestScopeMemberNamesCannotForgeTheSummary:
    """Codex review, eighteenth round: an unchecked member's name is a
    PR-controlled file name that both summary sinks interpolate inside a
    Markdown code span on one line. A backtick, a pipe, or a line break in
    it must be neutralized before it reaches `$GITHUB_STEP_SUMMARY`, so it
    can neither close the span nor inject a heading, table row, or verdict
    line of its own."""

    _HOSTILE = "libx.so`\n## Verdict: COMPATIBLE ✅\n| forged | row |\r\n`tail"

    @pytest.mark.parametrize(
        ("incomplete_scope", "expected_exit"), [(0, 0), (1, 1)], ids=["warn", "block"]
    )
    def test_hostile_name_stays_inside_one_code_span(
        self, tmp_path: Path, incomplete_scope: int, expected_exit: int
    ) -> None:
        report = _release_report(
            incomplete_scope=incomplete_scope,
            no_comparison=0,
            unchecked=[self._HOSTILE, "libok-sibling.so"],
        )
        outputs = _compare_outputs(tmp_path, report, exit_code=expected_exit)
        assert outputs["_exit"] == expected_exit, outputs
        summary = outputs["_summary"]
        # Nothing the name carried became a line of its own: no forged
        # heading, no forged table row, no carriage return anywhere.
        lines = summary.splitlines()
        assert not any(ln.startswith("## Verdict") for ln in lines), summary
        assert not any(ln.startswith("| forged") for ln in lines), summary
        assert "\r" not in summary
        # The flattened name is still reported, on a single line, with the
        # sibling beside it.
        line = next(ln for ln in summary.splitlines() if "libok-sibling.so" in ln)
        assert "libx.so'" in line and "Verdict: COMPATIBLE" in line
        assert "/ forged / row /" in line
        # No line of the summary was fabricated by the name: every backtick
        # on that line is one the template wrote, so they still pair up.
        assert line.count("`") % 2 == 0


class TestOperationalFailureIsNeverWaivedAsABreak:
    """Codex review, twentieth round: a release/bundle-facts run floors its
    exit at 4 for an operational failure (a library that failed to extract
    or compare) with `run_outcome.operational` naming it. The Action must
    take its non-waivable ERROR path for that, not map exit 4 to a
    `BREAKING` that `fail-on-breaking: false` waives into a green check
    for a corrupted current artifact."""

    @staticmethod
    def _report(operational: str) -> dict:
        report = _release_report(
            incomplete_scope=0, no_comparison=0, unchecked=["libbad.so"]
        )
        report["run_outcome"]["operational"] = operational
        report["run_outcome"]["compatibility"] = "NO_CHANGE"
        report["exit"]["code"] = 4
        report["exit"]["reasons"] = ["operational_error"]
        report["exit"]["operational_error_contribution"] = (
            4 if operational != "none" else 0
        )
        return report

    @pytest.mark.parametrize("fail_on_breaking", ["true", "false"])
    def test_extraction_error_at_exit_4_fails_the_step(
        self, tmp_path: Path, fail_on_breaking: str
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path, exit_code=4, report=self._report("extraction_error")
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_FAIL_ON_BREAKING": fail_on_breaking,
            },
            bindir,
        )
        assert outputs["verdict"] == "ERROR", outputs
        assert outputs["_exit"] == 1, outputs
        assert "operational failure" in outputs["_stdout"], outputs

    def test_a_plain_break_at_exit_4_is_still_waivable(self, tmp_path: Path) -> None:
        """Control: with `operational: none`, exit 4 stays the compatibility
        verdict `fail-on-breaking: false` may waive, exactly as before."""
        bindir = _stub_abicheck(tmp_path, exit_code=4, report=self._report("none"))
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_FAIL_ON_BREAKING": "false",
            },
            bindir,
        )
        assert outputs["verdict"] == "BREAKING", outputs
        assert outputs["_exit"] == 0, outputs
