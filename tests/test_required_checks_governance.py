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

`main` deliberately requires no status checks (`.github/AGENTS.md`,
"Required-status-check configuration"). These tests keep the ruleset
artifact honest about that, and keep the retired polling bridge jobs and
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
    """`.github/branch-protection-ruleset.json` records the *non-fast-forward*
    protection this repo actually wants on `main`. It deliberately carries no
    `required_status_checks` rule -- see `.github/AGENTS.md`'s
    "Required-status-check configuration" section: this repo made a conscious
    choice not to block a merge on CI completion, so there is no
    required-check list left for this artifact to keep in sync with."""

    @staticmethod
    def _ruleset() -> dict[str, Any]:
        raw = (ROOT / ".github" / "branch-protection-ruleset.json").read_text(
            encoding="utf-8"
        )
        data = json.loads(raw)
        assert isinstance(data, dict)
        return data

    def test_targets_main_and_is_active(self) -> None:
        ruleset = self._ruleset()
        assert ruleset.get("target") == "branch"
        assert ruleset.get("enforcement") == "active"
        include = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
        assert "refs/heads/main" in include

    def test_carries_no_required_status_checks_rule(self) -> None:
        """The one thing this test suite must keep true: no rule of type
        `required_status_checks` sneaks back into the applied ruleset without
        a deliberate decision to re-enable merge-blocking on CI -- see
        `.github/AGENTS.md`'s "Required-status-check configuration" section."""
        rules = self._ruleset().get("rules")
        assert isinstance(rules, list) and rules
        assert not [r for r in rules if r.get("type") == "required_status_checks"]

    def test_runbook_references_the_json_file_and_gh_command(self) -> None:
        runbook = (ROOT / ".github" / "branch-protection-ruleset.md").read_text(
            encoding="utf-8"
        )
        assert "branch-protection-ruleset.json" in runbook
        assert "gh api" in runbook
        assert "/repos/abicheck/abicheck/rulesets" in runbook
