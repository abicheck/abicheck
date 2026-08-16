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

"""Structural guards for the required-status-check governance mechanism
(CLI cleanup phase two, PR A / PR 0B; see `.github/AGENTS.md`'s
"Required-status-check configuration" section for the rule these jobs
implement).

Two path-filtered workflows (`docs-pr.yml`, `test-action.yml`) cannot be
required directly -- no native GitHub mechanism conditions "required" on
which paths a PR touched -- so `ci.yml` carries an always-triggered
neutral-aggregate gate job per workflow (`docs-pr-required`/
`test-action-required`) that re-evaluates the *same* path filter and, when
it matches, polls the target workflow's own aggregate check for the same
head SHA. These tests don't execute the polling logic (that needs a live
GitHub API and a real PR, per `.github/AGENTS.md`'s own note) -- they guard
the two things that would otherwise silently rot: the copied path-filter
list drifting out of sync with the workflow it mirrors, and a new job added
to `test-action.yml` escaping its own aggregate's `needs:` list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> dict[str, Any]:
    raw = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{name}: expected a mapping at the top level"
    return raw


def _jobs(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow has no 'jobs' mapping"
    return jobs


def _paths_filter(workflow: dict[str, Any]) -> list[str]:
    """The `on: pull_request: paths:` list, or ``[]`` if the workflow carries
    none (matching PyYAML's own key normalization: a bare ``on:`` key parses
    as the boolean ``True`` under YAML 1.1, so this looks up both spellings)."""
    on_block = workflow.get("on", workflow.get(True, {}))
    pr_block = on_block.get("pull_request", {}) if isinstance(on_block, dict) else {}
    paths = pr_block.get("paths", []) if isinstance(pr_block, dict) else []
    assert isinstance(paths, list)
    return paths


class TestNeutralAggregateGateJobsExist:
    def test_ci_yml_has_docs_pr_required_gate(self) -> None:
        ci = _load_workflow("ci.yml")
        jobs = _jobs(ci)
        assert "docs-pr-required" in jobs

    def test_ci_yml_has_test_action_required_gate(self) -> None:
        ci = _load_workflow("ci.yml")
        jobs = _jobs(ci)
        assert "test-action-required" in jobs

    @pytest.mark.parametrize("job_id", ["docs-pr-required", "test-action-required"])
    def test_gate_jobs_only_run_on_pull_request(self, job_id: str) -> None:
        """Neither `docs-pr.yml` nor `test-action.yml`'s own aggregate check
        re-runs on a `push`-triggered merge commit (a different, untested
        SHA) -- waiting on `push` would only time out, not gate anything."""
        ci = _load_workflow("ci.yml")
        job = _jobs(ci)[job_id]
        assert job.get("if") == "github.event_name == 'pull_request'"

    @pytest.mark.parametrize("job_id", ["docs-pr-required", "test-action-required"])
    def test_gate_jobs_carry_no_paths_filter(self, job_id: str) -> None:
        """The gate jobs live in `ci.yml`, which must stay unconditioned
        (`ci.yml` as a whole has no `paths:` filter) -- a path filter on
        `ci.yml` itself would reintroduce the exact "may not run at all"
        problem these jobs exist to close."""
        ci = _load_workflow("ci.yml")
        assert not _paths_filter(ci)
        # Confirm the job itself exists (sanity: parametrize didn't typo).
        assert job_id in _jobs(ci)


class TestPathFilterStaysInSyncWithTargetWorkflow:
    """The gate jobs' own `dorny/paths-filter` filter lists are hand-copied
    from `docs-pr.yml`'s/`test-action.yml`'s own `on: pull_request: paths:`
    blocks. If one changes without the other, the gate silently stops
    matching what it's supposed to guard -- exactly the kind of drift this
    file exists to catch mechanically instead of relying on someone
    remembering to update both places."""

    def _gate_job_filter_paths(self, ci: dict[str, Any], job_id: str) -> list[str]:
        job = _jobs(ci)[job_id]
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("dorny/paths-filter@"):
                filters_yaml = step["with"]["filters"]
                parsed = yaml.safe_load(filters_yaml)
                # One named filter key per gate job (e.g. `docs:`/`action:`);
                # take its value regardless of the key's own spelling.
                (paths,) = parsed.values()
                assert isinstance(paths, list)
                return paths
        pytest.fail(f"{job_id}: no dorny/paths-filter step found")

    def test_docs_pr_required_matches_docs_pr_yml(self) -> None:
        ci = _load_workflow("ci.yml")
        docs_pr = _load_workflow("docs-pr.yml")
        assert self._gate_job_filter_paths(ci, "docs-pr-required") == _paths_filter(
            docs_pr
        )

    def test_test_action_required_matches_test_action_yml(self) -> None:
        ci = _load_workflow("ci.yml")
        test_action = _load_workflow("test-action.yml")
        assert self._gate_job_filter_paths(ci, "test-action-required") == _paths_filter(
            test_action
        )


class TestTestActionSummaryCoversEveryJob:
    """`test-action-summary` is the one stable required check standing in for
    all of `test-action.yml`'s fan-out jobs -- a job added to that workflow
    without also being added to the summary's `needs:` list would silently
    never gate anything, exactly the kind of escape a matrix-leg-by-leg
    required-check list already has a documented history of causing."""

    def test_needs_lists_every_other_job(self) -> None:
        test_action = _load_workflow("test-action.yml")
        jobs = _jobs(test_action)
        assert "test-action-summary" in jobs
        summary = jobs["test-action-summary"]
        needs = summary.get("needs")
        assert isinstance(needs, list) and needs, "test-action-summary must list needs"
        other_jobs = set(jobs) - {"test-action-summary"}
        assert set(needs) == other_jobs

    def test_runs_even_if_a_dependency_failed_or_was_cancelled(self) -> None:
        test_action = _load_workflow("test-action.yml")
        summary = _jobs(test_action)["test-action-summary"]
        assert summary.get("if") == "always()"


class TestVerifyMergeChecksWorkflow:
    def test_triggers_only_on_push_to_main(self) -> None:
        wf = _load_workflow("verify-merge-checks.yml")
        on_block = wf.get("on", wf.get(True))
        assert set(on_block) == {"push"}
        assert on_block["push"]["branches"] == ["main"]

    def test_required_checks_list_present_in_script(self) -> None:
        raw = (WORKFLOWS / "verify-merge-checks.yml").read_text(encoding="utf-8")
        assert "REQUIRED_CHECKS" in raw
        # Every unconditional required check named in .github/AGENTS.md's
        # own derived list should appear in the script's own list literal --
        # a loose substring check (not a full JS parse), but enough to catch
        # a check silently dropped from one place and not the other.
        for name in (
            "ai-readiness",
            "FAIR metadata and packaging",
            "lint-and-types",
            "unit-tests (ubuntu-latest, 3.13, false)",
            "packaging (ubuntu-latest)",
            "packaging (windows-latest)",
            "changelog-fragment",
            "cli-interface-diff",
            "test-contract",
            "Dependency Review",
            "Security Scan",
            "CodeQL Analysis (python)",
        ):
            assert name in raw, f"{name!r} missing from verify-merge-checks.yml"

    def test_does_not_require_the_path_filtered_aggregate_checks(self) -> None:
        """`build-docs`/`test-action summary` are deliberately excluded --
        whether they exist at all for a given PR head SHA depends on the
        same path filter the `ci.yml` gate jobs already re-evaluate, so
        requiring them here too would either duplicate that logic or, if it
        drifted, produce a false failure on a PR that never touched those
        paths."""
        raw = (WORKFLOWS / "verify-merge-checks.yml").read_text(encoding="utf-8")
        assert "'build-docs'" not in raw
        assert "'test-action summary'" not in raw
