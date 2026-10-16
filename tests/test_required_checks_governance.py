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

"""Structural guards for the required-status-check governance artifacts.

`main` requires exactly one status check, `lint-and-types`
(`.github/AGENTS.md`, "Required-status-check configuration"). These tests
keep the ruleset artifact pinned to that decision, keep the required job
running on every pull request, and keep the retired polling bridge jobs and
the retired `test-action summary` roll-up job from coming back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import _yaml_fast
import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _load_workflow(name: str) -> dict[str, Any]:
    raw = _yaml_fast.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{name}: expected a mapping at the top level"
    return raw


def _jobs(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow has no 'jobs' mapping"
    return jobs


class TestNoPollingBridgeJobs:
    """`ci.yml` once carried `docs-pr (required)`/`test-action (required)`:
    jobs that re-ran another workflow's path filter and then *polled* its
    aggregate check for up to 25/35 minutes, occupying a runner while doing
    nothing. They existed only so a path-filtered workflow could be named in
    a required-status-checks Ruleset -- and `main` deliberately requires no
    status checks (`.github/AGENTS.md`, "Required-status-check
    configuration"), so they were pure runner occupancy on the most
    congested pool. `test-action.yml`'s jobs and `build-docs` report directly.

    If merge-blocking is ever re-enabled, bridge a path-filtered workflow
    with a reusable-workflow call and ordinary `needs:` (plan
    `ci-cost-and-assurance.md`, Phase 1), never a sleep loop."""

    @pytest.mark.parametrize(
        "workflow", sorted(p.name for p in WORKFLOWS.glob("*.yml"))
    )
    def test_no_job_sleeps_waiting_for_another_check(self, workflow: str) -> None:
        wf = _load_workflow(workflow)
        for job_id, job in _jobs(wf).items():
            for step in job.get("steps", []) or []:
                script = str((step.get("with") or {}).get("script", ""))
                assert not ("listForRef" in script and "setTimeout" in script), (
                    f"{workflow}:{job_id} polls another check in a sleep loop"
                )

    def test_the_removed_bridge_jobs_stay_removed(self) -> None:
        jobs = _jobs(_load_workflow("ci.yml"))
        assert "docs-pr-required" not in jobs
        assert "test-action-required" not in jobs


class TestTestActionHasNoRollUpJob:
    """`test-action.yml` once carried `test-action summary`, a `needs:`-only job
    standing in for every fan-out job as one stable required check. With no
    required checks on `main` it gated nothing, and on its last measured run
    it queued 30 of the workflow's 43 minutes to report two jobs' results.
    It was removed; this keeps it from returning silently. If merge-blocking
    is re-enabled, reinstate a single aggregate whose predicate fails on a
    failed, cancelled OR skipped dependency (plan `ci-cost-and-assurance.md`,
    Phase 1) and replace this test with one that pins that predicate."""

    def test_no_job_only_aggregates_other_jobs(self) -> None:
        jobs = _jobs(_load_workflow("test-action.yml"))
        assert "test-action-summary" not in jobs
        for job_id, job in jobs.items():
            assert not (
                job.get("needs")
                and not any("uses" in step for step in job.get("steps", []) or [])
            ), f"{job_id} is a needs-only roll-up job"


class TestBranchRulesetArtifact:
    """`.github/branch-protection-ruleset.json` is the applied Ruleset for
    `main`. Its one required check is `lint-and-types` (`.github/AGENTS.md`,
    "Required-status-check configuration", decision 2026-10-15). Making any
    other check merge-blocking is a decision that must change this test too."""

    REQUIRED_CONTEXTS = {"lint-and-types"}

    @staticmethod
    def _ruleset() -> dict[str, Any]:
        raw = (ROOT / ".github" / "branch-protection-ruleset.json").read_text(
            encoding="utf-8"
        )
        data = json.loads(raw)
        assert isinstance(data, dict)
        return data

    def _rules_of_type(self, rule_type: str) -> list[dict[str, Any]]:
        rules = self._ruleset().get("rules")
        assert isinstance(rules, list) and rules
        return [r for r in rules if r.get("type") == rule_type]

    def test_targets_main_and_is_active(self) -> None:
        ruleset = self._ruleset()
        assert ruleset.get("target") == "branch"
        assert ruleset.get("enforcement") == "active"
        include = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
        assert "refs/heads/main" in include

    def test_keeps_non_fast_forward_protection(self) -> None:
        assert len(self._rules_of_type("non_fast_forward")) == 1

    def test_exactly_one_required_status_check_rule(self) -> None:
        assert len(self._rules_of_type("required_status_checks")) == 1

    def test_requires_exactly_lint_and_types_and_nothing_else(self) -> None:
        """The one thing this suite must keep true: the required set is
        `lint-and-types` and no other check is merge-blocking."""
        (rule,) = self._rules_of_type("required_status_checks")
        contexts = {c["context"] for c in rule["parameters"]["required_status_checks"]}
        assert contexts == self.REQUIRED_CONTEXTS

    def test_repository_admin_bypass_is_declared(self) -> None:
        """Maintainer choice (removable): repository admins may merge past a
        red `lint-and-types` for an urgent fix. Role id 5 is the admin
        repository role in GitHub's Rulesets API."""
        bypass = self._ruleset().get("bypass_actors")
        assert bypass == [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ]

    def test_required_check_is_a_job_that_runs_on_every_pull_request(self) -> None:
        """The required context must name a job whose check-run name is the
        job id (no `name:` override), that has no `if:`/`needs:` gate, and
        whose workflow runs on every pull request to `main` with no path
        filter. Otherwise the check is missing on some PRs and the Ruleset
        would wait on it forever."""
        wf = _load_workflow("ci.yml")
        job = _jobs(wf)["lint-and-types"]
        assert "name" not in job, "check-run name must equal the job id"
        assert "if" not in job and "needs" not in job
        triggers = wf.get("on", wf.get(True)) or {}
        assert "pull_request" in triggers
        pull_request = triggers.get("pull_request") or {}
        assert "main" in pull_request.get("branches", [])
        assert "paths" not in pull_request
        assert "paths-ignore" not in pull_request

    def test_runbook_references_the_json_file_and_gh_command(self) -> None:
        runbook = (ROOT / ".github" / "branch-protection-ruleset.md").read_text(
            encoding="utf-8"
        )
        assert "branch-protection-ruleset.json" in runbook
        assert "gh api" in runbook
        assert "/repos/abicheck/abicheck/rulesets" in runbook
