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

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# The 14 unconditional required-check names, kept in one place here so
# TestBranchRulesetArtifact and TestVerifyMergeChecksWorkflow (below) check
# both of the other two sources of truth against the *same* list rather than
# each hand-copying it independently -- which is exactly how this list has
# drifted wrong three times before (see .github/AGENTS.md's own "Required-
# status-check configuration" section and its "hand-copying a fourth one is
# the wrong fix" note).
REQUIRED_CHECK_NAMES = (
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
    "docs-pr (required)",
    "test-action (required)",
)


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

    @pytest.mark.parametrize(
        ("job_id", "expected_check_name"),
        [
            ("docs-pr-required", "docs-pr (required)"),
            ("test-action-required", "test-action (required)"),
        ],
    )
    def test_gate_job_check_name_is_documented_as_required(
        self, job_id: str, expected_check_name: str
    ) -> None:
        """GitHub Rulesets match a required status check by its emitted
        check-run *name*, not by the workflow job id -- `.github/AGENTS.md`'s
        required-check list must name what the job actually reports
        (`name:`), not the job id, or an admin applying that list configures
        a context that never reports and strands every PR."""
        job = _jobs(_load_workflow("ci.yml"))[job_id]
        assert job.get("name") == expected_check_name
        agents_md = (ROOT / ".github" / "AGENTS.md").read_text(encoding="utf-8")
        assert f"`{expected_check_name}`" in agents_md


class TestGateJobPollingLogic:
    """Guards on the two gate jobs' own `actions/github-script` polling step
    -- a `skipped` conclusion must not be silently accepted as a pass (the
    path filter is what handles "the target workflow never ran"; a check run
    that exists and reports `skipped` is a different, real signal), and the
    step should be SHA-pinned like its neighbors rather than a floating tag."""

    @pytest.mark.parametrize("job_id", ["docs-pr-required", "test-action-required"])
    def test_does_not_accept_a_skipped_conclusion_as_success(self, job_id: str) -> None:
        ci = _load_workflow("ci.yml")
        job = _jobs(ci)[job_id]
        script_step = next(
            s
            for s in job["steps"]
            if s.get("uses", "").startswith("actions/github-script@")
        )
        script = script_step["with"]["script"]
        assert "'success', 'neutral'" in script
        assert "'skipped'" not in script

    @pytest.mark.parametrize("workflow_name", ["ci.yml", "verify-merge-checks.yml"])
    def test_github_script_is_pinned_by_sha(self, workflow_name: str) -> None:
        wf = _load_workflow(workflow_name)
        for job in _jobs(wf).values():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if uses.startswith("actions/github-script@"):
                    ref = uses.split("@", 1)[1]
                    assert len(ref) == 40, (
                        f"{workflow_name}: {uses!r} is not SHA-pinned"
                    )


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
        target = _paths_filter(docs_pr)
        assert target, "docs-pr.yml no longer has an `on: pull_request: paths:` filter"
        assert self._gate_job_filter_paths(ci, "docs-pr-required") == target

    def test_test_action_required_matches_test_action_yml(self) -> None:
        ci = _load_workflow("ci.yml")
        test_action = _load_workflow("test-action.yml")
        target = _paths_filter(test_action)
        assert target, (
            "test-action.yml no longer has an `on: pull_request: paths:` filter"
        )
        assert self._gate_job_filter_paths(ci, "test-action-required") == target


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

    def test_fails_on_a_skipped_dependency_too(self) -> None:
        """`if: always()` (above) makes a skipped dependency visible to this
        job's `needs.*.result`, but visibility alone doesn't fail the run --
        the shell check must also treat 'skipped' as unsuccessful, or a
        partially-skipped fan-out reports green through the required gate
        `test-action-required` (`ci.yml`) polls."""
        test_action = _load_workflow("test-action.yml")
        summary = _jobs(test_action)["test-action-summary"]
        run_step = next(
            s
            for s in summary["steps"]
            if s.get("name") == "Fail if any test-action.yml job did not succeed"
        )
        assert "contains(needs.*.result, 'skipped')" in run_step["run"]


class TestVerifyMergeChecksWorkflow:
    def test_triggers_only_on_push_to_main(self) -> None:
        wf = _load_workflow("verify-merge-checks.yml")
        on_block = wf.get("on", wf.get(True))
        assert set(on_block) == {"push"}
        assert on_block["push"]["branches"] == ["main"]

    def test_required_checks_list_present_in_script(self) -> None:
        raw = (WORKFLOWS / "verify-merge-checks.yml").read_text(encoding="utf-8")
        assert "REQUIRED_CHECKS" in raw
        # Every unconditional required check in the shared REQUIRED_CHECK_NAMES
        # list should appear in the script's own list literal -- a loose
        # substring check (not a full JS parse), but enough to catch a check
        # silently dropped from one place and not the other.
        for name in REQUIRED_CHECK_NAMES:
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

    def test_check_run_listing_is_paginated(self) -> None:
        """A hand-added `per_page: 100` ceiling silently drops any check run
        past the 100th once the repo's check-run count grows past it -- use
        `github.paginate` instead so there's no ceiling to outgrow."""
        raw = (WORKFLOWS / "verify-merge-checks.yml").read_text(encoding="utf-8")
        assert "github.paginate(github.rest.checks.listForRef" in raw

    def test_target_pr_selection_is_restricted_to_merged_prs_targeting_main(
        self,
    ) -> None:
        """`listPullRequestsAssociatedWithCommit` can return an unrelated
        open PR that happens to contain the same commit -- selection must be
        restricted to merged PRs based against `main` before falling back to
        anything, or an unrelated PR's head SHA could be verified instead."""
        raw = (WORKFLOWS / "verify-merge-checks.yml").read_text(encoding="utf-8")
        assert "pr.merged_at" in raw
        assert "pr.base.ref === 'main'" in raw

    def test_empty_candidates_does_not_fall_back_to_an_unfiltered_pr(self) -> None:
        """If every commit<->PR association GitHub returns is ineligible (not
        merged, or not based against `main`), the workflow must treat that
        the same as "no PR associated" rather than silently falling back to
        `prs[0]` -- an unfiltered fallback can validate a completely
        unrelated PR's head SHA and read as green or red for reasons that
        have nothing to do with the actual merge being verified."""
        raw = (WORKFLOWS / "verify-merge-checks.yml").read_text(encoding="utf-8")
        assert "candidates.length === 0" in raw
        assert "|| prs[0]" not in raw


class TestBranchRulesetArtifact:
    """`.github/branch-protection-ruleset.json` is the API payload an admin
    applies by hand to actually enforce the required-check list (see
    `.github/branch-protection-ruleset.md`'s runbook) -- the one manual step
    PR 0B/P0 cannot automate. It is a *third* place this same 14-name list is
    spelled out, alongside `AGENTS.md`'s prose and `verify-merge-checks.yml`'s
    `REQUIRED_CHECKS` array; these tests keep it from becoming a fourth
    hand-copied list that drifts out of sync unnoticed."""

    @staticmethod
    def _ruleset() -> dict[str, Any]:
        raw = (ROOT / ".github" / "branch-protection-ruleset.json").read_text(
            encoding="utf-8"
        )
        data = json.loads(raw)
        assert isinstance(data, dict)
        return data

    @staticmethod
    def _status_checks_rule(ruleset: dict[str, Any]) -> dict[str, Any]:
        rules = ruleset.get("rules")
        assert isinstance(rules, list) and rules
        (status_rule,) = [r for r in rules if r.get("type") == "required_status_checks"]
        return status_rule

    def test_targets_main_and_is_active(self) -> None:
        ruleset = self._ruleset()
        assert ruleset.get("target") == "branch"
        assert ruleset.get("enforcement") == "active"
        include = ruleset.get("conditions", {}).get("ref_name", {}).get("include", [])
        assert "refs/heads/main" in include

    def test_required_status_checks_matches_the_shared_list_exactly(self) -> None:
        rule = self._status_checks_rule(self._ruleset())
        contexts = tuple(
            c["context"] for c in rule["parameters"]["required_status_checks"]
        )
        # Order-insensitive: what matters for enforcement is the set, and
        # accidental reordering during a hand-edit shouldn't fail this test.
        assert set(contexts) == set(REQUIRED_CHECK_NAMES)
        assert len(contexts) == len(REQUIRED_CHECK_NAMES), (
            "duplicate context entry in branch-protection-ruleset.json"
        )

    def test_strict_status_checks_policy_is_enabled(self) -> None:
        """Without `strict_required_status_checks_policy`, a required check
        that passed against a stale base branch can still count -- matching
        the same up-to-date-branch expectation `non_fast_forward` implies."""
        rule = self._status_checks_rule(self._ruleset())
        assert rule["parameters"]["strict_required_status_checks_policy"] is True

    def test_runbook_references_the_json_file_and_gh_command(self) -> None:
        runbook = (ROOT / ".github" / "branch-protection-ruleset.md").read_text(
            encoding="utf-8"
        )
        assert "branch-protection-ruleset.json" in runbook
        assert "gh api" in runbook
        assert "/repos/abicheck/abicheck/rulesets" in runbook

    def test_shared_list_is_actually_documented_in_agents_md(self) -> None:
        """The two tests above only prove the JSON agrees with
        `REQUIRED_CHECK_NAMES` -- a hard-coded tuple in *this* file, which
        could itself drift from `AGENTS.md`'s prose without anything
        failing. Close that gap the same way
        `test_gate_job_check_name_is_documented_as_required` already does
        for the two neutral-aggregate gate checks: every name must appear
        backtick-quoted in `AGENTS.md`'s own required-check list, so a name
        added to/removed from one place and not the other is a real
        failure, not just a same-file tautology (Codex review on #887)."""
        agents_md = (ROOT / ".github" / "AGENTS.md").read_text(encoding="utf-8")
        for name in REQUIRED_CHECK_NAMES:
            assert f"`{name}`" in agents_md, (
                f"{name!r} is in REQUIRED_CHECK_NAMES but not documented, "
                f"backtick-quoted, in .github/AGENTS.md"
            )
