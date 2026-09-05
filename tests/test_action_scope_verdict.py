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
    def test_the_stderr_notice_is_the_no_json_fallback(
        self, tmp_path: Path, event: str
    ) -> None:
        """Under a `pull_request` event the PR-comment step re-runs the CLI
        for JSON and can leave a `{}`-shaped placeholder in PR_JSON when the
        primary run wrote no report; the final gate must not read that
        scope-less document as "the axis did not fire" (a macOS CI lane
        caught exactly that -- the runner's ambient GITHUB_EVENT_NAME)."""
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
        # With no JSON to read the severity gate from, the exit-1 dispatch
        # keeps its established label (exactly as the coverage axis's own
        # no-JSON fallback does) -- but the axis is still enforced and named.
        assert outputs["_exit"] == 1, outputs
        assert "incompletely checked comparison scope" in outputs["_stdout"], outputs
        assert "comparison scope was not fully checked" in outputs["_stdout"], outputs

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
