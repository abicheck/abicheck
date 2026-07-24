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

"""Structural tests for the ``check-single.yml``/``check-project.yml``
reusable workflows (G30 P1.4, ADR-047 §4/§5).

These workflows' own step orchestration needs a real GitHub Actions runner
to exercise end-to-end (nested composite-Action `uses:`, matrix expansion,
artifact download/upload) -- mirroring ``tests/test_action_check_target.py``'s
own precedent, these tests assert structure over the parsed YAML instead:
the required always()-on conditions (ADR-047 §4's required sub-tasks for
``check-project.yml``), step ordering, and the self-checkout identity
pattern every nested ``uses: ./x`` step depends on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CHECK_SINGLE = WORKFLOWS_DIR / "check-single.yml"
CHECK_PROJECT = WORKFLOWS_DIR / "check-project.yml"
TEST_ACTION = WORKFLOWS_DIR / "test-action.yml"


def _load(path: Path) -> dict[str, Any]:
    # PyYAML's default (YAML 1.1) resolver reads the bare `on:` key as the
    # boolean `True`, not the string `"on"` -- every real-world GitHub
    # Actions parser example does the same and callers index with
    # `data[True]`/`data.get(True)`; done once here rather than per test.
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


def _step_names(job: dict[str, Any]) -> list[str]:
    return [s.get("name") for s in _steps(job)]


class TestBothFilesParseAsValidWorkflowYaml:
    def test_check_single_parses(self) -> None:
        data = _load(CHECK_SINGLE)
        assert "check" in data["jobs"]

    def test_check_project_parses(self) -> None:
        data = _load(CHECK_PROJECT)
        assert set(data["jobs"]) == {"plan", "check", "aggregate"}


class TestCheckSingleSelfCheckout:
    """Mirrors check-target/action.yml's own "Capture this Action's
    identity" -> "Checkout abicheck" -> nested `uses:` pattern, but keyed
    off `job.workflow_ref`/`job.workflow_sha` (the reusable-workflow
    equivalent of `github.action_repository`/`github.action_ref`) since a
    relative `uses: ./x` step inside THIS reusable workflow's own steps
    resolves against the caller's checkout, not this repository, exactly
    like the composite-Action case check-target itself already had to fix
    (confirmed via GitHub Community Discussion #107558)."""

    def test_identity_captured_before_the_nested_checkout(self) -> None:
        data = _load(CHECK_SINGLE)
        names = _step_names(data["jobs"]["check"])
        assert "Capture this reusable workflow's identity" in names
        assert "Checkout abicheck (for nested Action composition)" in names
        identity_idx = names.index("Capture this reusable workflow's identity")
        checkout_idx = names.index("Checkout abicheck (for nested Action composition)")
        assert identity_idx < checkout_idx

    def test_identity_step_falls_back_to_github_repository_and_sha(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        identity_step = next(
            s
            for s in steps
            if s.get("name") == "Capture this reusable workflow's identity"
        )
        run = identity_step["run"]
        assert "WORKFLOW_REF" in identity_step["env"]
        assert "WORKFLOW_SHA" in identity_step["env"]
        assert "${FALLBACK_REPOSITORY}" in run or "FALLBACK_REPOSITORY" in run
        assert "${FALLBACK_REF}" in run or "FALLBACK_REF" in run

    def test_nested_check_target_uses_the_checked_out_copy(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["uses"] == "./.check-single-src/actions/check-target"

    def test_outputs_forward_every_check_target_output(self) -> None:
        data = _load(CHECK_SINGLE)
        wf_outputs = set(data[True]["workflow_call"]["outputs"])
        assert wf_outputs == {
            "outcome",
            "check-id",
            "verdict",
            "compatibility-verdict",
            "policy-gate-decision",
            "report-path",
        }
        job_outputs = set(data["jobs"]["check"]["outputs"])
        assert wf_outputs == job_outputs


class TestCheckProjectAlwaysOnRequirements:
    """ADR-047 §4's two required sub-tasks for check-project.yml: the
    trailing aggregate job must run with `if: always()` (never a bare
    `needs:`), and each matrix cell's report-upload step must too --
    otherwise a gate-mode: deferred operational failure on one leg either
    silently skips the aggregate job (which reports success when skipped)
    or never uploads the one report `aggregate` most needs to see."""

    def test_aggregate_job_condition_is_always(self) -> None:
        data = _load(CHECK_PROJECT)
        condition = data["jobs"]["aggregate"].get("if", "")
        assert "always()" in condition, (
            "the aggregate job must be conditioned on always() (or "
            "!cancelled()) -- a bare `needs: [plan, check]` with no `if:` "
            "gets skipped when any matrix leg fails, and a skipped job "
            "reports success"
        )

    def test_aggregate_job_still_depends_on_plan_and_check(self) -> None:
        data = _load(CHECK_PROJECT)
        needs = data["jobs"]["aggregate"]["needs"]
        assert set(needs) == {"plan", "check"}

    def test_report_upload_step_condition_is_always(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        upload_step = next(s for s in steps if s.get("name") == "Upload report")
        assert "always()" in upload_step.get("if", "")

    def test_run_check_target_step_has_no_continue_on_error(self) -> None:
        """A real ABI break (gate-mode: local) or operational error must
        fail this matrix job's own conclusion so branch-protection sees it
        -- continue-on-error here would swallow that; always()-conditioned
        later steps (report upload) still run regardless, per plain GitHub
        Actions semantics, so continue-on-error is neither needed nor
        wanted on this specific step."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert "continue-on-error" not in run_step


class TestCheckProjectStepOrdering:
    def test_upload_report_runs_after_run_check_target(self) -> None:
        data = _load(CHECK_PROJECT)
        names = _step_names(data["jobs"]["check"])
        assert names.index("Run check-target") < names.index("Upload report")

    def test_candidate_resolution_runs_before_run_check_target(self) -> None:
        data = _load(CHECK_PROJECT)
        names = _step_names(data["jobs"]["check"])
        assert names.index("Resolve candidate binary/binaries") < names.index(
            "Run check-target"
        )

    def test_check_job_depends_on_plan(self) -> None:
        data = _load(CHECK_PROJECT)
        assert data["jobs"]["check"]["needs"] == "plan"

    def test_check_job_is_gated_on_plan_having_checks(self) -> None:
        data = _load(CHECK_PROJECT)
        condition = data["jobs"]["check"].get("if", "")
        assert "needs.plan.outputs.has-checks" in condition

    def test_check_job_uses_fail_fast_false(self) -> None:
        data = _load(CHECK_PROJECT)
        strategy = data["jobs"]["check"]["strategy"]
        assert strategy["fail-fast"] is False


class TestCheckProjectMatrixWiring:
    def test_matrix_comes_from_plan_job_output(self) -> None:
        data = _load(CHECK_PROJECT)
        strategy = data["jobs"]["check"]["strategy"]
        assert strategy["matrix"] == "${{ fromJSON(needs.plan.outputs.matrix) }}"

    def test_plan_job_emits_matrix_and_has_checks_outputs(self) -> None:
        data = _load(CHECK_PROJECT)
        outputs = data["jobs"]["plan"]["outputs"]
        assert set(outputs) == {"matrix", "has-checks", "run-plan-artifact-name"}

    def test_plan_step_builds_matrix_from_run_plan_checks(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["plan"])
        plan_step = next(s for s in steps if s.get("name") == "Generate run-plan.json")
        run = plan_step["run"]
        assert "run-plan generate" in run
        assert "plan.get('checks'" in run or "checks = plan.get" in run


class TestCheckProjectAggregateManifestProjection:
    """ADR-047 §5's required sub-task: project run-plan.json to
    `aggregate --manifest`'s wire shape using each check's own check_id,
    not the bare target name, via `abicheck run-plan to-aggregate-manifest`
    -- never passing run-plan.json straight through to `aggregate`."""

    def test_aggregate_job_calls_the_projection_command(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["aggregate"])
        project_step = next(
            s
            for s in steps
            if s.get("name") == "Project run-plan.json to an aggregate manifest"
        )
        assert "run-plan to-aggregate-manifest" in project_step["run"]

    def test_aggregate_command_consumes_the_projected_manifest_not_run_plan_directly(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["aggregate"])
        aggregate_step = next(s for s in steps if s.get("name") == "Run aggregate")
        run = aggregate_step["run"]
        assert "aggregate-manifest.json" in run
        assert "run-plan.json" not in run.replace("run-plan.json --", "")


class TestCheckProjectArtifactNaming:
    def test_build_output_artifacts_downloaded_by_pattern(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["plan"])
        dl = next(
            s for s in steps if s.get("name") == "Download build-output artifacts"
        )
        assert dl["with"]["pattern"] == "${{ inputs.build-output-artifact-prefix }}*"

    def test_candidate_artifact_name_uses_profile_id_from_matrix(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        dl = next(s for s in steps if s.get("name") == "Download candidate artifact")
        assert dl["with"]["name"] == (
            "${{ inputs.candidate-artifact-prefix }}${{ matrix.profile_id }}"
        )

    def test_baseline_artifact_download_is_skipped_for_baseline_channel_none(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        dl = next(s for s in steps if s.get("name") == "Download baseline-set artifact")
        assert dl.get("if") == "matrix.baseline_channel != 'none'"

    def test_report_artifact_name_uses_the_checks_own_sanitized_check_id(self) -> None:
        """check_id is `target@profile#baseline_channel@depth` -- `#` in an
        artifact name is a documented, reproducible bug
        (actions/upload-artifact#473: causes an Authorization error), so the
        raw check-id output must be sanitized before use, mirroring
        check-target/run.sh's own `tr -c 'A-Za-z0-9._-' '_'` precedent for
        its per-check report filename."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        sanitize = next(
            s for s in steps if s.get("name") == "Sanitize check-id for artifact name"
        )
        assert "tr -c 'A-Za-z0-9._-' '_'" in sanitize["run"]
        assert sanitize["env"]["CHECK_ID"] == "${{ steps.run.outputs.check-id }}"

        upload = next(s for s in steps if s.get("name") == "Upload report")
        assert upload["with"]["name"] == (
            "${{ inputs.report-artifact-prefix }}${{ steps.sanitized.outputs.id }}"
        )
        assert "steps.run.outputs.check-id" not in upload["with"]["name"], (
            "the upload step must use the sanitized id, not the raw check-id "
            "(which can contain '#', a documented artifact-name bug trigger)"
        )

    def test_sanitize_step_runs_before_upload_and_shares_its_always_condition(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        names = _step_names(data["jobs"]["check"])
        assert names.index("Sanitize check-id for artifact name") < names.index(
            "Upload report"
        )
        sanitize = next(
            s for s in steps if s.get("name") == "Sanitize check-id for artifact name"
        )
        upload = next(s for s in steps if s.get("name") == "Upload report")
        assert (
            sanitize.get("if")
            == upload.get("if")
            == ("always() && steps.run.outputs.report-path != ''")
        )


class TestNoArrayLiteralsInExpressions:
    """GitHub Actions expression syntax has no array-literal form -- only
    boolean/null/number/string literals plus values from contexts or
    fromJSON() (confirmed: docs.github.com/actions/reference/workflows-and-
    actions/expressions, and community discussion #27223 reproducing the
    parse failure). A bare `[]` inside `${{ ... }}` is a workflow-file
    syntax error, which fails the ENTIRE workflow before any job schedules
    -- not just the one expression using it (Codex review; confirmed
    empirically: this repo's own real CI run for the commit that introduced
    the bug shows the `test-action.yml` run resolving to zero jobs)."""

    def test_check_project_yml_has_no_bare_array_literal_expressions(self) -> None:
        text = CHECK_PROJECT.read_text(encoding="utf-8")
        assert "|| [])" not in text
        assert "&& [])" not in text
        assert "fromJSON('[]')" in text, (
            "bundle-members must build its empty-array fallback via "
            "fromJSON('[]'), not a bare [] literal"
        )

    def test_check_single_yml_has_no_bare_array_literal_expressions(self) -> None:
        text = CHECK_SINGLE.read_text(encoding="utf-8")
        assert "|| [])" not in text
        assert "&& [])" not in text


class TestAppConsumerBinaryResolvedSeparately:
    """target_kind: app-consumer needs TWO distinct candidate artifacts --
    the library (new-library, from binary_pattern) and the consumer
    executable (consumer-binary, from consumer_binary_pattern) -- reusing
    new-library for both would scope --used-by against the library itself
    instead of the actual consumer (Codex review)."""

    def test_resolver_script_resolves_consumer_binary_pattern_separately(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        run = resolver["run"]
        assert "consumer_binary_pattern" in run
        assert "consumer-binary=" in run

    def test_run_check_target_consumer_binary_uses_its_own_resolved_output(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        consumer_binary_expr = run_step["with"]["consumer-binary"]
        assert "steps.candidate.outputs.consumer-binary" in consumer_binary_expr
        assert "steps.candidate.outputs.new-library" not in consumer_binary_expr


class TestCheckProjectFixtureDoesNotFailTheRequiredWorkflow:
    """The test-check-project job group deliberately exercises an expected
    failure (a matrix cell whose baseline can never resolve) to prove the
    always()-conditioned aggregate job survives it -- but that means the
    call to check-project.yml itself is *expected* to fail, and without
    continue-on-error that expected failure would fail the whole required
    `Test GitHub Action` workflow on every single run (Codex review)."""

    def test_test_check_project_job_has_continue_on_error(self) -> None:
        data = _load(TEST_ACTION)
        job = data["jobs"]["test-check-project"]
        assert job.get("continue-on-error") is True

    def test_verify_job_does_not_have_continue_on_error(self) -> None:
        """The verification job's own assertions must still fail the
        workflow for real if they're wrong -- only the intentionally-
        failing fixture call itself should be shielded."""
        data = _load(TEST_ACTION)
        job = data["jobs"]["test-check-project-verify"]
        assert "continue-on-error" not in job
