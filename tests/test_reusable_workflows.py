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

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
CHECK_SINGLE = WORKFLOWS_DIR / "check-single.yml"
CHECK_PROJECT = WORKFLOWS_DIR / "check-project.yml"
TEST_ACTION = WORKFLOWS_DIR / "test-action.yml"
TEST_CHECK_PROJECT_FAILURE_PATH = WORKFLOWS_DIR / "test-check-project-failure-path.yml"
SCHEDULE_CHECK_PROJECT_FAILURE_PATH = (
    WORKFLOWS_DIR / "schedule-check-project-failure-path.yml"
)


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
        assert set(data["jobs"]) == {"plan", "check", "no-checks", "aggregate"}


class TestCheckSingleMirrorsCheckTargetInputs:
    """`reusable-workflows.md` states check-single's inputs mirror
    check-target's 1:1. A new check-target input that this workflow accepts
    but never forwards — or never accepts at all — silently breaks that
    claim, and leaves a single check unable to select a toolchain a matrix
    cell can (G34 Phase C)."""

    def test_dependency_source_is_accepted_and_forwarded(self) -> None:
        data = _load(CHECK_SINGLE)
        inputs = data[True]["workflow_call"]["inputs"]
        assert inputs["dependency-source"]["type"] == "string"
        assert inputs["dependency-source"]["default"] == ""
        run_step = next(
            s
            for s in _steps(data["jobs"]["check"])
            if s.get("name") == "Run check-target"
        )
        assert run_step["with"]["dependency-source"] == (
            "${{ inputs.dependency-source }}"
        )

    def test_expected_project_ref_is_accepted_and_forwarded(self) -> None:
        # Regression (Codex review): check-target's expected-project-ref
        # input (the accepted-main wrong-commit guard) was unreachable
        # from check-single.yml -- it declared no such input and forwarded
        # nothing, so an accepted-main PR gate built on this reusable
        # workflow (not the bare Action) had no way to opt into the guard.
        data = _load(CHECK_SINGLE)
        inputs = data[True]["workflow_call"]["inputs"]
        assert inputs["expected-project-ref"]["type"] == "string"
        assert inputs["expected-project-ref"]["default"] == ""
        run_step = next(
            s
            for s in _steps(data["jobs"]["check"])
            if s.get("name") == "Run check-target"
        )
        assert run_step["with"]["expected-project-ref"] == (
            "${{ inputs.expected-project-ref }}"
        )

    def test_expected_baseline_generation_is_accepted_and_forwarded(self) -> None:
        data = _load(CHECK_SINGLE)
        inputs = data[True]["workflow_call"]["inputs"]
        assert inputs["expected-baseline-generation"]["type"] == "string"
        assert inputs["expected-baseline-generation"]["default"] == ""
        run_step = next(
            s
            for s in _steps(data["jobs"]["check"])
            if s.get("name") == "Run check-target"
        )
        assert run_step["with"]["expected-baseline-generation"] == (
            "${{ inputs.expected-baseline-generation }}"
        )


class TestCheckSingleSelfCheckout:
    """Mirrors check-target/action.yml's own "Capture this Action's
    identity" -> "Checkout abicheck" -> nested `uses:` pattern, but keyed
    off `job.workflow_ref`/`job.workflow_sha` (the reusable-workflow
    equivalent of `github.action_repository`/`github.action_ref` -- NOT
    `github.workflow_ref`/`github.workflow_sha`, which GitHub's own docs
    document as caller-associated inside a called reusable workflow, so it
    would resolve to an external consumer's own repository/ref rather than
    this one) since a relative `uses: ./x` step inside THIS reusable
    workflow's own steps resolves against the caller's checkout, not this
    repository, exactly like the composite-Action case check-target itself
    already had to fix (confirmed via GitHub Community Discussion
    #107558)."""

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
            "report-artifact-name",
        }
        job_outputs = set(data["jobs"]["check"]["outputs"])
        assert wf_outputs == job_outputs


class TestCheckSingleReportUpload:
    """report-path is a path inside check-single.yml's own ephemeral `check`
    job workspace -- unreachable by the calling workflow without an
    explicit upload/download round-trip, since (unlike actions/check-target,
    a composite Action a caller nests in their OWN job) this reusable
    workflow's job runs on a separate runner. Before this fix there was no
    upload step at all, making report-path effectively unusable for any
    reusable-workflow caller (Codex review). The artifact name is
    <report-artifact-prefix><sanitized-check-id>, not a bare fixed name --
    a caller invoking check-single.yml more than once in the same workflow
    run (e.g. from a matrix) would otherwise collide, since
    actions/upload-artifact requires unique names within one run (a second
    Codex review), mirroring check-project.yml's own per-cell convention."""

    def test_report_artifact_prefix_input_has_a_default(self) -> None:
        data = _load(CHECK_SINGLE)
        prefix_input = data[True]["workflow_call"]["inputs"]["report-artifact-prefix"]
        assert prefix_input["default"]

    def test_sanitize_step_runs_between_check_target_and_upload(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        names = [s.get("name") for s in steps]
        assert "Run check-target" in names
        assert "Sanitize check-id for artifact name" in names
        assert "Upload report" in names
        run_idx = names.index("Run check-target")
        sanitize_idx = names.index("Sanitize check-id for artifact name")
        upload_idx = names.index("Upload report")
        assert run_idx < sanitize_idx < upload_idx
        sanitize_step = next(
            s for s in steps if s.get("name") == "Sanitize check-id for artifact name"
        )
        assert sanitize_step["if"] == "always() && steps.run.outputs.report-path != ''"
        assert sanitize_step["env"]["CHECK_ID"] == "${{ steps.run.outputs.check-id }}"

    def test_upload_report_step_uses_prefix_plus_sanitized_id(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        upload_step = next(s for s in steps if s.get("name") == "Upload report")
        assert upload_step["if"] == "always() && steps.run.outputs.report-path != ''"
        assert upload_step["with"]["name"] == (
            "${{ inputs.report-artifact-prefix }}${{ steps.sanitized.outputs.id }}"
        )
        assert upload_step["with"]["path"] == "${{ steps.run.outputs.report-path }}"

    def test_report_artifact_name_output_matches_the_uploaded_name(self) -> None:
        # A bare, unconditional concatenation would leave this output as just
        # the bare prefix (naming a never-uploaded artifact) whenever
        # report-path is empty, since the "Sanitize check-id for artifact
        # name" step -- and therefore steps.sanitized.outputs.id -- only runs
        # when report-path is non-empty (CodeRabbit review). The output must
        # itself be conditioned on report-path, not just concatenate blindly.
        data = _load(CHECK_SINGLE)
        job_outputs = data["jobs"]["check"]["outputs"]
        value = job_outputs["report-artifact-name"]
        assert "steps.run.outputs.report-path != ''" in value
        assert (
            "format('{0}{1}', inputs.report-artifact-prefix, steps.sanitized.outputs.id)"
            in value
        )
        assert value.rstrip().endswith("|| '' }}")


class TestCheckSingleOptionalArtifactStaging:
    """check-single.yml's own `check` job always runs in a fresh, isolated
    runner -- unlike check-target itself (a composite Action a caller can
    nest as one step inside their OWN job, sharing that job's filesystem).
    A new-library/baseline-path/candidate-build-output path from the
    caller's own build job doesn't exist here unless explicitly staged as
    an artifact download; the documented usage example silently assumed
    otherwise (Codex review)."""

    def test_three_artifact_name_inputs_default_to_empty(self) -> None:
        data = _load(CHECK_SINGLE)
        inputs = data[True]["workflow_call"]["inputs"]
        for name in (
            "candidate-artifact-name",
            "baseline-artifact-name",
            "build-output-artifact-name",
        ):
            assert inputs[name]["type"] == "string"
            assert inputs[name]["default"] == ""

    def test_download_steps_are_conditioned_on_their_own_artifact_name_input(
        self,
    ) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        by_name = {
            "Download candidate artifact": "inputs.candidate-artifact-name != ''",
            "Download baseline artifact": "inputs.baseline-artifact-name != ''",
            "Download build-output artifact": "inputs.build-output-artifact-name != ''",
        }
        for step_name, expected_if in by_name.items():
            step = next(s for s in steps if s.get("name") == step_name)
            assert step.get("if") == expected_if
            assert step["uses"].startswith("actions/download-artifact@")

    def test_download_steps_run_before_run_check_target(self) -> None:
        data = _load(CHECK_SINGLE)
        names = _step_names(data["jobs"]["check"])
        run_idx = names.index("Run check-target")
        for step_name in (
            "Download candidate artifact",
            "Download baseline artifact",
            "Download build-output artifact",
        ):
            assert names.index(step_name) < run_idx

    def test_baseline_artifact_downloads_into_workflow_owned_staging(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        step = next(s for s in steps if s.get("name") == "Download baseline artifact")
        assert step["with"]["path"] == ".check-single-baseline"

    @pytest.mark.parametrize(
        ("clear_name", "download_name"),
        [
            ("Clear candidate staging before download", "Download candidate artifact"),
            ("Clear baseline staging before download", "Download baseline artifact"),
            (
                "Clear build-output staging before download",
                "Download build-output artifact",
            ),
        ],
    )
    def test_each_download_is_preceded_by_a_clear_step_with_the_same_condition(
        self, clear_name: str, download_name: str
    ) -> None:
        """The earlier `actions/checkout` step already populates the whole
        workspace from the caller's own repository -- a stale checked-in
        file at `candidate`/the fixed baseline staging path/`build-output`
        would otherwise survive an artifact download that doesn't happen to
        overwrite it (a missing file, or a differently-laid-out artifact),
        and `new-library` is a fixed caller-supplied path
        here (unlike check-project.yml's glob-based candidate resolver), so
        a stale file is scanned/compared as if it were the real upload
        (Codex review). The clear must share its download's own `if:` --
        clearing unconditionally would destroy a deliberately checked-in
        fixture path when the caller leaves the artifact-name input empty."""
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        names = _step_names(data["jobs"]["check"])
        clear_step = next(s for s in steps if s.get("name") == clear_name)
        download_step = next(s for s in steps if s.get("name") == download_name)
        assert clear_step.get("if") == download_step.get("if")
        assert names.index(clear_name) < names.index(download_name)

    def test_baseline_staging_cannot_target_a_caller_controlled_path(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        clear_step = next(
            s
            for s in steps
            if s.get("name") == "Clear baseline staging before download"
        )
        assert clear_step["run"] == "rm -rf .check-single-baseline"
        assert "env" not in clear_step

        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["baseline-path"] == (
            "${{ inputs.baseline-artifact-name != '' && '.check-single-baseline' "
            "|| inputs.baseline-path }}"
        )


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


class TestCheckProjectFailsLoudOnEmptyRunPlan:
    """`abicheck project plan` is fail-closed by default on an empty
    checks[] (ADR-054), but this workflow's plan step passes --allow-empty
    so it degrades to a WARNING instead -- `check`/`aggregate` are both
    gated on has-checks == 'true', so an empty run would otherwise skip
    both and report the whole workflow as a success having gated nothing.
    The `no-checks` job exists to fail that case loud instead (self-review
    finding, not from an external review round)."""

    def test_no_checks_job_exists_and_depends_on_plan(self) -> None:
        data = _load(CHECK_PROJECT)
        assert "no-checks" in data["jobs"]
        assert data["jobs"]["no-checks"]["needs"] == "plan"

    def test_no_checks_job_runs_exactly_when_check_job_would_not(self) -> None:
        data = _load(CHECK_PROJECT)
        no_checks_condition = data["jobs"]["no-checks"]["if"]
        check_condition = data["jobs"]["check"]["if"]
        assert no_checks_condition == "needs.plan.outputs.has-checks != 'true'"
        assert check_condition == "needs.plan.outputs.has-checks == 'true'"

    def test_no_checks_job_fails(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["no-checks"])
        assert len(steps) == 1
        assert "exit 1" in steps[0]["run"]


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
        assert "project plan" in run
        assert "--allow-empty" in run
        assert "plan.get('checks'" in run or "checks = plan.get" in run


class TestCheckProjectAggregateManifestProjection:
    """ADR-047 §5's required sub-task: aggregate matches reports by each
    check's own check_id, not the bare target name -- via `abicheck
    aggregate --run-plan run-plan.json`, which projects run-plan.json to
    the expected-target set internally (ADR-054), no separate projection
    step or intermediate manifest file."""

    def test_aggregate_command_consumes_run_plan_directly(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["aggregate"])
        assert not any(
            s.get("name") == "Project run-plan.json to an aggregate manifest"
            for s in steps
        )
        aggregate_step = next(s for s in steps if s.get("name") == "Run aggregate")
        run = aggregate_step["run"]
        assert "--run-plan run-plan.json" in run
        assert "aggregate-manifest.json" not in run


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

    def test_baseline_artifact_name_is_keyed_by_profile_as_well_as_channel(
        self,
    ) -> None:
        """A baseline-set is itself profile-specific (actions/baseline's
        manifest records exactly one `profile`; resolve-baseline rejects a
        mismatch as `wrong_profile`), so two contract profiles sharing one
        baseline_channel each need their own artifact -- a channel-only name
        would make every profile but one resolve the wrong baseline (Codex
        review)."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        dl = next(s for s in steps if s.get("name") == "Download baseline-set artifact")
        assert dl["with"]["name"] == (
            "${{ inputs.baseline-artifact-prefix }}${{ matrix.profile_id }}"
            "-${{ matrix.baseline_channel }}"
        )
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["baseline-path"] == (
            "baseline-sets/${{ matrix.profile_id }}-${{ matrix.baseline_channel }}"
        )

    def test_report_artifact_name_uses_the_checks_own_sanitized_check_id(self) -> None:
        """check_id is `target@profile#baseline_channel@depth` -- `#` in an
        artifact name is a documented, reproducible bug
        (actions/upload-artifact#473: causes an Authorization error), so the
        raw check-id output must be sanitized before use. The sanitizer
        keeps a readable prefix (collapsing disallowed characters to `_`,
        mirroring check-target/run.sh's own `tr -c 'A-Za-z0-9._-' '_'`
        precedent for its per-check report filename) but that alone is
        lossy -- distinct check_ids can collapse to the same string (Codex
        review) -- so a content-hash suffix of the original check_id is
        appended to keep artifact names distinct."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        sanitize = next(
            s for s in steps if s.get("name") == "Sanitize check-id for artifact name"
        )
        run = sanitize["run"]
        assert "hashlib.sha256" in run
        assert sanitize["env"]["CHECK_ID"] == (
            "${{ steps.run.outputs.check-id || steps.precheck.outputs.check-id }}"
        )

        upload = next(s for s in steps if s.get("name") == "Upload report")
        assert upload["with"]["name"] == (
            "${{ inputs.report-artifact-prefix }}${{ steps.sanitized.outputs.id }}"
        )
        assert "steps.run.outputs.check-id" not in upload["with"]["name"], (
            "the upload step must use the sanitized id, not the raw check-id "
            "(which can contain '#', a documented artifact-name bug trigger)"
        )

    def test_sanitizer_disambiguates_check_ids_that_collide_under_the_readable_prefix(
        self,
    ) -> None:
        """Extract the real sanitizer script and run it against the exact
        collision Codex flagged: target `a`/profile `b_c` and target
        `a_b`/profile `c` on the same channel/depth both collapse to the
        same string once `@`/`#` become `_` -- the appended hash suffix
        must still make the two artifact names distinct."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        sanitize = next(
            s for s in steps if s.get("name") == "Sanitize check-id for artifact name"
        )
        # Split on the flag, not the interpreter name: these steps resolve
        # their interpreter (`"$PY" -c`) so a Windows-scheduled cell can run
        # them, and hard-coding `python3` here broke the moment that landed.
        script = sanitize["run"].split(' -c "', 1)[1].rsplit('"', 1)[0]

        def sanitized_id(check_id: str) -> str:
            with tempfile.TemporaryDirectory() as tmp:
                output_path = Path(tmp) / "github_output"
                output_path.write_text("")
                env = {
                    **os.environ,
                    "CHECK_ID": check_id,
                    "GITHUB_OUTPUT": str(output_path),
                }
                subprocess.run([sys.executable, "-c", script], env=env, check=True)
                line = output_path.read_text().strip()
                assert line.startswith("id=")
                return line[len("id=") :]

        first = sanitized_id("a@b_c#chan@headers")
        second = sanitized_id("a_b@c#chan@headers")
        assert first != second

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
        # Either "Run check-target" or the pre-check operational-error
        # envelope step produced the report -- exactly one runs per matrix
        # cell (issue #628).
        assert (
            sanitize.get("if")
            == upload.get("if")
            == (
                "always() && (steps.run.outputs.report-path != '' || "
                "steps.precheck.outputs.report-path != '')"
            )
        )


class TestCheckProjectClearsStagingDirsBeforeTolerantDownloads:
    """The three artifact downloads in the `check` job (candidate,
    baseline-set, build-output) are all `continue-on-error: true`, so a
    missing/failed download must not leave stale content behind for the
    later resolve/consume steps to pick up instead. The earlier
    `actions/checkout` step populates the whole workspace from the caller's
    own repository first -- if that repository happens to contain checked-in
    `candidate/`, `build-output/`, or `baseline-sets/...` directories at
    these same paths, a swallowed download failure would otherwise fall
    through to comparing against those stale repository files rather than
    the intended build artifact (Codex review)."""

    def test_clear_step_exists_and_removes_all_three_staging_paths(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        clear = next(
            s
            for s in steps
            if s.get("name") == "Clear staging directories before tolerated downloads"
        )
        run = clear["run"]
        assert "rm -rf candidate build-output" in run
        assert clear["env"]["BASELINE_STAGING_DIR"] == (
            "baseline-sets/${{ matrix.profile_id }}-${{ matrix.baseline_channel }}"
        )
        assert "$BASELINE_STAGING_DIR" in run

    def test_clear_step_runs_before_all_three_downloads(self) -> None:
        data = _load(CHECK_PROJECT)
        names = _step_names(data["jobs"]["check"])
        clear_idx = names.index("Clear staging directories before tolerated downloads")
        for download_name in (
            "Download candidate artifact",
            "Download baseline-set artifact",
            "Download build-output artifact",
        ):
            assert clear_idx < names.index(download_name), download_name


class TestCheckProjectClearsReportsDirBeforeAggregateDownload:
    """The `aggregate` job's own earlier `actions/checkout` step populates
    the whole workspace from the caller's own repository first -- if that
    repository happens to contain a checked-in reports/*.json directory,
    the `merge-multiple: true` download below would extract every
    downloaded report into that same directory without removing what's
    already there, and `abicheck aggregate` rejects duplicate target IDs
    across every *.json it finds under reports/ (Codex review)."""

    def test_clear_step_exists_and_precedes_the_download(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["aggregate"])
        names = _step_names(data["jobs"]["aggregate"])
        clear = next(
            s for s in steps if s.get("name") == "Clear reports staging before download"
        )
        assert clear["run"].strip() == "rm -rf reports"
        assert names.index("Clear reports staging before download") < names.index(
            "Download every check report"
        )


class TestPreCheckOperationalErrorReport:
    """issue #628 (G30 P1.4 known gap, plan doc round-5/round-8/round-9
    addenda): a candidate-resolution failure or a required build-output
    download failure used to end the matrix job with no report at all for
    `aggregate` to see. The fix reuses
    actions/check-target/report_envelope.py's own `--mode operational-error`
    directly (the same script check-target's own run.sh drives for a real
    resolve-baseline failure), so neither of report_envelope.py's own logic
    nor check-target's input surface needed to change."""

    def test_build_output_download_has_no_continue_on_error(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        dl = next(s for s in steps if s.get("name") == "Download build-output artifact")
        assert dl.get("id") == "download_build_output"
        assert "continue-on-error" not in dl

    def test_candidate_resolution_has_continue_on_error(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        assert resolver.get("continue-on-error") is True

    def test_precheck_step_exists_and_is_gated_on_either_prior_failure(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        precheck = next(
            s
            for s in steps
            if s.get("name") == "Synthesize pre-check operational-error report"
        )
        assert precheck["id"] == "precheck"
        condition = precheck["if"]
        assert "always()" in condition
        assert "steps.download_build_output.outcome == 'failure'" in condition
        assert "steps.candidate.outcome == 'failure'" in condition

    def test_precheck_step_reuses_report_envelope_py_operational_error_mode(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        precheck = next(
            s
            for s in steps
            if s.get("name") == "Synthesize pre-check operational-error report"
        )
        run = precheck["run"]
        assert "report_envelope.py" in run
        assert "--mode operational-error" in run
        assert '--resolve-outcome "ambiguous"' in run

    def test_run_check_target_gated_on_candidate_success(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["if"] == "steps.candidate.outcome == 'success'"

    def test_step_ordering(self) -> None:
        data = _load(CHECK_PROJECT)
        names = _step_names(data["jobs"]["check"])
        assert (
            names.index("Download build-output artifact")
            < names.index("Resolve candidate binary/binaries")
            < names.index("Synthesize pre-check operational-error report")
            < names.index("Run check-target")
            < names.index("Sanitize check-id for artifact name")
            < names.index("Upload report")
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash` "
            "-- this test exercises that real POSIX bash behavior, which a "
            "Windows test runner cannot provide (plain 'bash' there is the "
            "System32 WSL launcher, not Git Bash)."
        ),
    )
    def test_precheck_script_writes_a_valid_operational_error_report_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """Extract the real precheck script and run it via bash -c (exactly
        as the runner does), pointed at the real report_envelope.py through
        the same relative `.check-project-src/` layout the job checks out,
        confirming the emitted report-path/exit-code/check-id outputs and
        that the underlying report.json is a valid operational-error
        envelope."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        precheck = next(
            s
            for s in steps
            if s.get("name") == "Synthesize pre-check operational-error report"
        )
        script = precheck["run"]

        repo_root = Path(__file__).resolve().parents[1]
        src_dir = tmp_path / ".check-project-src"
        src_dir.mkdir()
        (src_dir / "actions").symlink_to(
            repo_root / "actions", target_is_directory=True
        )

        github_output = tmp_path / "github_output"
        github_output.write_text("")
        env = {
            **os.environ,
            "MATRIX_NAME": "libfoo",
            "MATRIX_PROFILE_ID": "linux-x86_64",
            "MATRIX_BASELINE_CHANNEL": "accepted-main",
            "MATRIX_REQUESTED_DEPTH": "headers",
            "MATRIX_EXPLICIT_ID": "",
            "MATRIX_GATE_MODE": "deferred",
            "PROJECT": "abicheck/abicheck",
            "HEAD_SHA": "deadbeef",
            "BASE_REF": "main",
            "ACTION_VERSION": "abicheck/abicheck@main",
            "BUILD_OUTPUT_FAILED": "false",
            "GITHUB_OUTPUT": str(github_output),
        }
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, result.stderr  # operational errors fail the step

        output_lines = dict(
            line.split("=", 1)
            for line in github_output.read_text().splitlines()
            if "=" in line
        )
        assert output_lines["check-id"] == ("libfoo@linux-x86_64#accepted-main@headers")
        assert output_lines["report-path"] == "precheck-report.json"
        assert (
            "exit-code" not in output_lines
        )  # grep -v'd out, like run.sh's own pattern

        report = json.loads((tmp_path / "precheck-report.json").read_text())
        assert report["verdict"] == "ERROR"
        assert report["operational_errors"][0]["kind"] == "ambiguous"
        assert (
            "resolution failed for 'libfoo'"
            in report["operational_errors"][0]["message"]
        )


class TestBaselineRequiredAndCandidateBuildOutputForwarded:
    """check-single.yml already forwards baseline-required/
    candidate-build-output to check-target -- check-project.yml's own
    Run check-target step didn't (Codex review). Without baseline-required,
    a bootstrap check (required: false) reports a hard not_found
    operational error instead of the intended advisory bootstrap pass.
    Without candidate-build-output, resolve-baseline's
    incompatible_evidence check never runs, so a baseline produced by a
    different evidence-producer/tool-version can be silently compared
    against."""

    def test_run_check_target_forwards_baseline_required_from_matrix(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["baseline-required"] == "${{ matrix.required }}"

    def test_run_check_target_forwards_candidate_build_output(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["candidate-build-output"] == (
            "${{ steps.candidate.outputs.build-output }}"
        )

    def test_run_check_target_forwards_expected_project_ref(self) -> None:
        # Regression (Codex review): check-target's own
        # expected-project-ref input (the accepted-main wrong-commit
        # guard) was unreachable from this composed workflow -- it
        # declared no such input and forwarded nothing, so a caller
        # relying on check-project.yml (not the bare Action) had no way to
        # opt into the guard at all.
        data = _load(CHECK_PROJECT)
        assert "expected-project-ref" in data[True]["workflow_call"]["inputs"]
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["expected-project-ref"] == (
            "${{ matrix.baseline_channel == 'accepted-main' && "
            "inputs.expected-project-ref || '' }}"
        )

    def test_expected_project_ref_forwarding_is_scoped_to_accepted_main(
        self,
    ) -> None:
        # Second-round regression (Codex review): forwarding
        # expected-project-ref UNCONDITIONALLY to every cell breaks a
        # mixed-channel project matrix -- a release-contract cell's
        # manifest records a release tag, not a Git ref, so an unscoped
        # forward would make every release-contract cell fail closed on a
        # project_ref it was never going to match. Pins the exact
        # per-channel ternary rather than only checking it mentions the
        # input at all.
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        expr = run_step["with"]["expected-project-ref"]
        assert "matrix.baseline_channel == 'accepted-main'" in expr
        assert "inputs.expected-project-ref" in expr

    def test_run_check_target_forwards_expected_baseline_generation(self) -> None:
        # Mirrors test_run_check_target_forwards_expected_project_ref above,
        # except unscoped: unlike a Git ref, a scanner-compatibility
        # generation applies uniformly regardless of baseline-channel.
        data = _load(CHECK_PROJECT)
        assert "expected-baseline-generation" in data[True]["workflow_call"]["inputs"]
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["expected-baseline-generation"] == (
            "${{ inputs.expected-baseline-generation }}"
        )

    def test_run_check_target_prefers_per_cell_compile_overlay_over_global_inputs(
        self,
    ) -> None:
        """P1 toolchain-profile audit: gcc-path/gcc-options get a per-cell
        override from that cell's profiles.<id>.compile overlay
        (run_plan.RunPlanCheck.compile_gcc_path/compile_gcc_options), falling
        back to this workflow's own global gcc-path/gcc-options inputs when
        the profile declares no overlay (backward compatible).

        Gated on `kind != 'bundle'` — see the bundle test below for why.
        """
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["gcc-path"] == (
            "${{ matrix.kind != 'bundle' && matrix.compile_gcc_path "
            "|| inputs.gcc-path }}"
        )
        assert run_step["with"]["gcc-options"] == (
            "${{ matrix.kind != 'bundle' && matrix.compile_gcc_options "
            "|| inputs.gcc-options }}"
        )
        # gcc-prefix has no RunPlanCheck counterpart -- stays global-only.
        assert run_step["with"]["gcc-prefix"] == "${{ inputs.gcc-prefix }}"

    @pytest.mark.parametrize(
        ("key", "global_input"),
        [
            ("gcc-path", "inputs.gcc-path"),
            ("gcc-options", "inputs.gcc-options"),
        ],
    )
    def test_per_cell_gcc_path_and_options_are_not_forwarded_to_bundle_cells(
        self, key: str, global_input: str
    ) -> None:
        """The same hazard `test_per_cell_ast_frontend_is_not_forwarded_to_
        bundle_cells` covers for `ast-frontend`, for `gcc-path`/`gcc-options`
        (CLI-audit P1, G34 Phase B plan doc's acknowledged "pre-existing
        bug, not one this phase introduced"): a bundle cell's `new-library`
        is the `bundle-staging` *directory* it stages its members into, and
        `cli_resolve._reject_compile_context_for_set_inputs` hard-rejects
        `--compiler`/`--gcc-options` for a directory/package compare, since
        the per-library release fan-out never threads a single-pair L2
        compile context to each pair. Before this guard, a profile that set
        `compile.binding` for its target cells would have that same per-cell
        override reach every bundle cell too, turning a previously working
        bundle check into a hard operational error.

        As with `ast-frontend`, the fallback for a bundle cell is the
        workflow-global input, not the empty string — a bundle cell must
        behave exactly as it did before the per-cell override existed,
        including that a workflow-global gcc-path/gcc-options can still
        hard-error a bundle cell exactly as before. `sysroot` has no
        per-cell overlay field (unlike gcc-path/gcc-options, nothing wires
        `profiles.<id>.compile.sysroot` into `RunPlanCheck`), so its
        forwarding is unchanged by this fix and deliberately stays
        unconditional — gating it would be a new, undecided behaviour
        change (silently dropping an explicit global `--sysroot` for bundle
        cells), not a fix for the acknowledged bug. Covered separately by
        `test_sysroot_stays_unconditional_for_bundle_cells` below.
        """
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        expr = run_step["with"][key]
        assert "matrix.kind != 'bundle'" in expr, (
            f"a bundle cell compares a directory, where the CLI rejects "
            f"--{key} for a directory/package compare outright"
        )
        # GitHub's `a && b || c`: when `a` is false the result is `c`, so a
        # bundle cell lands on the workflow-global input, not ''.
        assert expr.endswith(f"|| {global_input} }}}}")

    def test_sysroot_stays_unconditional_for_bundle_cells(self) -> None:
        """`sysroot` has no per-cell overlay field, so it stays unconditional
        rather than gated -- see the docstring above for why."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["sysroot"] == "${{ inputs.sysroot }}"

    def test_run_check_target_prefers_per_cell_ast_frontend(self) -> None:
        """G34 Phase B: the cell's own `profiles.<id>.compile.frontend`
        (`run_plan.RunPlanCheck.compile_ast_frontend`) drives that cell's
        `--ast-frontend`, falling back to the workflow-global input when the
        profile declares no overlay.

        This is the step that makes the field real rather than projected:
        without it, every cell in a GCC/Clang matrix resolves the one
        workflow-level frontend, which is exactly the conflation the phase
        exists to remove.

        Gated on `kind != 'bundle'` — see the bundle test below for why.
        """
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["ast-frontend"] == (
            "${{ matrix.kind != 'bundle' && matrix.compile_ast_frontend "
            "|| inputs.ast-frontend }}"
        )

    def test_per_cell_ast_frontend_is_not_forwarded_to_bundle_cells(self) -> None:
        """A bundle cell's `new-library` is the `bundle-staging` *directory*
        it stages its members into, and the root Action rejects every
        non-`auto` `ast-frontend` for a directory/package operand outright
        (`action/run.sh`'s `_is_release_style_operand` guard) — the
        per-library fan-out never threads an L2 compile context to each
        pair's header dump, so honouring it is impossible and silently
        dropping it would parse headers under the wrong frontend.

        So forwarding a profile's `frontend:` onto a bundle cell would turn
        a previously working bundle check into a hard operational error
        (Codex review). This asserts the guard clause is present and, just
        as importantly, that the fallback is the workflow-global input
        rather than the empty string: a bundle cell must behave exactly as
        it did before the per-cell override existed, including that guard
        still firing on a workflow-global non-`auto` value.
        """
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        expr = run_step["with"]["ast-frontend"]
        assert "matrix.kind != 'bundle'" in expr, (
            "a bundle cell compares a directory, where the root Action "
            "rejects any non-auto ast-frontend outright"
        )
        # GitHub's `a && b || c`: when `a` is false the result is `c`, so a
        # bundle cell lands on the workflow-global input, not ''.
        assert expr.endswith("|| inputs.ast-frontend }}")

    def test_check_job_shell_steps_resolve_their_python_interpreter(self) -> None:
        """G34 Phase C consequence (Codex review): this job can land on a
        Windows runner now, where Git Bash resolves `python` but not
        necessarily `python3` — the Windows CPython layout ships
        `python.exe` only. A bare `python3` in any of these steps would fail
        candidate resolution, and the envelope-writing fallback would fail
        with it, so the cell produces no report at all.
        """
        data = _load(CHECK_PROJECT)
        # An *invocation* — `python3` followed by a flag or a script — not a
        # mention. `command -v python3` in the resolver itself is correct, and
        # so is naming it in a comment.
        invocation = re.compile(r"\bpython3\s+(?:-|\./|\S+\.py)")
        offenders = [
            s.get("name")
            for s in _steps(data["jobs"]["check"])
            if invocation.search(s.get("run") or "")
        ]
        assert not offenders, (
            f"{offenders} invoke `python3` directly. This job's runs-on comes "
            f"from the cell's profile and can be windows-latest — resolve the "
            f'interpreter instead (PY="$(command -v python3 || command -v '
            f'python)"), as the other steps here and action/run.sh do.'
        )

    def test_check_job_is_scheduled_on_the_cells_own_runner(self) -> None:
        """G34 Phase C: `runs-on` comes from the cell, not a hardcoded
        `ubuntu-latest`, so an `os: windows` profile's cell runs natively.

        The `|| 'ubuntu-latest'` fallback is for one case only: a
        run-plan.json produced by an older abicheck, carrying no `runs_on`.
        Without it that cell resolves `runs-on:` to the empty string and is
        never scheduled — a silently missing check.
        """
        data = _load(CHECK_PROJECT)
        assert data["jobs"]["check"]["runs-on"] == (
            "${{ matrix.runs_on || 'ubuntu-latest' }}"
        )

    def test_run_check_target_prefers_per_cell_dependency_source(self) -> None:
        """G34 Phase C: same precedence as the compile overlay above — the
        profile's own `dependency_source:` wins for its cells, the
        workflow-level input covers the rest, and both empty leaves the
        legacy `install-deps` boolean deciding (the root action.yml owns
        that fallback, so it is not re-implemented here)."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["dependency-source"] == (
            "${{ matrix.dependency_source || inputs.dependency-source }}"
        )
        assert run_step["with"]["install-deps"] == "${{ inputs.install-deps }}"

    def test_dependency_source_input_defaults_to_empty(self) -> None:
        data = _load(CHECK_PROJECT)
        inputs = data[True]["workflow_call"]["inputs"]
        assert inputs["dependency-source"]["type"] == "string"
        assert inputs["dependency-source"]["default"] == ""

    def test_toolchain_bindings_path_input_defaults_to_empty(self) -> None:
        data = _load(CHECK_PROJECT)
        inputs = data[True]["workflow_call"]["inputs"]
        assert inputs["toolchain-bindings-path"]["default"] == ""
        assert inputs["toolchain-bindings-path"]["type"] == "string"

    def test_plan_step_forwards_toolchain_bindings_path_when_set(self) -> None:
        """Generate run-plan.json's shell step must actually pass
        --toolchain-bindings through to `abicheck project plan` when
        the workflow input is non-empty -- otherwise the input above would
        be silently inert."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["plan"])
        plan_step = next(s for s in steps if s.get("name") == "Generate run-plan.json")
        assert (
            plan_step["env"]["TOOLCHAIN_BINDINGS_PATH"]
            == "${{ inputs.toolchain-bindings-path }}"
        )
        assert "--toolchain-bindings" in plan_step["run"]

    def test_build_output_artifact_is_downloaded_before_it_is_resolved(self) -> None:
        data = _load(CHECK_PROJECT)
        names = _step_names(data["jobs"]["check"])
        assert names.index("Download build-output artifact") < names.index(
            "Resolve candidate binary/binaries"
        )

    def test_build_output_download_uses_profile_scoped_artifact_name(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        dl = next(s for s in steps if s.get("name") == "Download build-output artifact")
        assert dl["with"]["name"] == (
            "${{ inputs.build-output-artifact-prefix }}${{ matrix.profile_id }}"
        )

    def test_build_output_download_also_runs_for_no_baseline_wrapper_or_clang_plugin_evidence(
        self,
    ) -> None:
        # channel: none with evidence-producer: wrapper/clang-plugin still
        # needs this artifact if evidence-pack-path points inside it -- the
        # download must not be gated on baseline_channel alone (Codex review).
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        dl = next(s for s in steps if s.get("name") == "Download build-output artifact")
        condition = dl["if"]
        assert "matrix.baseline_channel != 'none'" in condition
        assert "inputs.evidence-producer == 'wrapper'" in condition
        assert "inputs.evidence-producer == 'clang-plugin'" in condition

    def test_resolver_emits_empty_build_output_when_download_did_not_land_a_file(
        self,
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        run = resolver["run"]
        assert "os.path.isfile(build_output_path)" in run

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_resolver_reports_build_output_end_to_end(self, tmp_path: Path) -> None:
        """Extract the real resolver script and run it via bash -c (exactly
        as the runner does) against both a present and an absent
        build-output.json, confirming the emitted output matches."""
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        script = resolver["run"]

        def build_output_output(*, stage_file: bool) -> str:
            root = tmp_path / ("with-file" if stage_file else "without-file")
            (root / "candidate").mkdir(parents=True)
            (root / "candidate" / "libexample.so").write_text("real")
            if stage_file:
                (root / "build-output").mkdir()
                (root / "build-output" / "build-output.json").write_text("{}")
            github_output = root / "github_output"
            github_output.write_text("")
            env = {
                **os.environ,
                "MATRIX_JSON": json.dumps(
                    {"kind": "target", "name": "libexample", "binary_pattern": "*.so"}
                ),
                "GITHUB_OUTPUT": str(github_output),
            }
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            line = next(
                line
                for line in github_output.read_text().splitlines()
                if line.startswith("build-output=")
            )
            return line[len("build-output=") :]

        assert build_output_output(stage_file=True) == "build-output/build-output.json"
        assert build_output_output(stage_file=False) == ""


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
    call to check-project.yml itself is *expected* to fail. GitHub Actions
    rejects `continue-on-error` on a job that calls a reusable workflow via
    `uses:` (https://github.com/orgs/community/discussions/77915) -- an
    earlier version of this fixture set it anyway, which made the whole
    workflow file invalid and silently dropped every job in the run (0
    scheduled jobs, conclusion failure, no parse-error surfaced by any of
    the job-log-based CI checks). The fix is structural, not a flag: this
    job group lives in its own, deliberately non-required workflow file
    (`test-check-project-failure-path.yml`), separate from the required
    `Test GitHub Action` workflow (`test-action.yml`)."""

    def test_test_check_project_job_does_not_have_continue_on_error(self) -> None:
        """continue-on-error is invalid on a `uses:` job -- GitHub Actions
        rejects the whole workflow file if it's present here."""
        data = _load(TEST_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["test-check-project"]
        assert "continue-on-error" not in job

    def test_verify_job_runs_even_though_its_needs_job_is_expected_to_fail(
        self,
    ) -> None:
        """Without continue-on-error to paper over the expected failure,
        test-check-project-verify needs its own if: always() so a plain
        `needs:`-skip doesn't skip the assertion that the failure was
        reported *correctly* instead of silently dropped."""
        data = _load(TEST_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["test-check-project-verify"]
        assert job.get("if") == "always()"
        assert "continue-on-error" not in job

    def test_failure_path_jobs_are_not_in_the_required_test_action_workflow(
        self,
    ) -> None:
        data = _load(TEST_ACTION)
        assert "test-check-project" not in data["jobs"]
        assert "test-check-project-stage" not in data["jobs"]
        assert "test-check-project-verify" not in data["jobs"]

    def test_test_action_no_longer_triggers_on_check_project_yml_changes(
        self,
    ) -> None:
        """test-action.yml has no job left that exercises check-project.yml
        -- its path filters should no longer list it (that live coverage
        moved to test-check-project-failure-path.yml's own paths)."""
        data = _load(TEST_ACTION)
        pr_paths = data[True]["pull_request"]["paths"]
        push_paths = data[True]["push"]["paths"]
        assert ".github/workflows/check-project.yml" not in pr_paths
        assert ".github/workflows/check-project.yml" not in push_paths

    def test_failure_path_workflow_does_not_trigger_on_pull_request_or_push(
        self,
    ) -> None:
        """Even in its own non-required workflow file, this fixture's
        expected failure still posts a real, visible red check-run against
        whatever commit SHA triggered it -- 'not required' doesn't mean
        'invisible' on a checks list, and a human (or an automated health
        signal) has no way to tell 'expected, by design' apart from a
        genuine failure without reading this file's own header comment.
        Neither `pull_request` nor `push` (which would attach the run to a
        commit SHA that is part of `main`'s own history) may trigger this
        workflow -- only `workflow_dispatch`, which the sibling
        `schedule-check-project-failure-path.yml` uses against a dedicated,
        disposable fixture ref, never against `main` directly."""
        data = _load(TEST_CHECK_PROJECT_FAILURE_PATH)
        assert "pull_request" not in data[True]
        assert "push" not in data[True]
        assert "schedule" not in data[True]
        assert data[True]["workflow_dispatch"] == {}

    def test_failure_path_workflow_has_no_schedule_of_its_own(self) -> None:
        """A `schedule:` trigger declared directly on this file would still
        run against `main`'s own tip commit (schedule always resolves the
        workflow file from the default branch) -- exactly the SHA-pollution
        problem this file exists to avoid. Automated scheduling must live in
        the sibling dispatcher workflow instead, which targets a disjoint
        fixture ref."""
        data = _load(TEST_CHECK_PROJECT_FAILURE_PATH)
        assert "schedule" not in data[True]


class TestScheduleCheckProjectFailurePathDispatcher:
    """`schedule-check-project-failure-path.yml` is the automated-trigger
    half of test-check-project-failure-path.yml: it runs on `main` on a
    schedule, but its own job's pass/fail must never be the dispatched run's
    own overall conclusion (expected to be "failure" every time, by design)
    -- `gh workflow run` is fire-and-forget and does not block on the run it
    creates, so a job that only dispatches and stops would report green
    both when the fixture behaves correctly AND when it regresses (Codex
    review). This job must instead poll for that specific run, wait for it
    to complete, and gate its own exit code on the dispatched run's
    `test-check-project-verify` job conclusion alone."""

    def test_dispatcher_workflow_has_no_python_assertions_of_its_own(self) -> None:
        """This job doesn't re-implement test-check-project-verify's own
        JSON-parsing assertions -- it only reads back that job's already-
        computed conclusion."""
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        run_steps = " ".join(s.get("run", "") for s in _steps(job))
        assert "assert" not in run_steps
        assert "uses: ./.github/workflows/check-project.yml" not in str(data)

    def test_dispatcher_targets_a_fixture_ref_not_main(self) -> None:
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        run_steps = " ".join(s.get("run", "") for s in _steps(job))
        assert "ci-fixture/check-project-failure-path" in run_steps
        assert 'REF="ci-fixture/check-project-failure-path"' in run_steps
        assert "git commit --allow-empty" in run_steps

    def test_dispatcher_dispatches_the_failure_path_workflow(self) -> None:
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        run_steps = " ".join(s.get("run", "") for s in _steps(job))
        assert 'WORKFLOW="test-check-project-failure-path.yml"' in run_steps
        assert 'gh workflow run "$WORKFLOW" --repo "$REPO" --ref "$REF"' in run_steps

    def test_dispatcher_waits_for_the_dispatched_run_to_complete(self) -> None:
        """`gh workflow run` alone doesn't block on the run it creates --
        this job must separately poll `gh run list`/`gh run view` for the
        specific run it just dispatched (correlated by the fixture commit's
        own SHA) and wait for `status == completed` before drawing any
        conclusion from it (Codex review)."""
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        run_steps = " ".join(s.get("run", "") for s in _steps(job))
        assert "gh run list --repo" in run_steps
        assert "FIXTURE_SHA" in run_steps
        assert "gh run view" in run_steps
        assert "--json status --jq .status" in run_steps
        assert (
            'status = "completed"' in run_steps
            or '"$status" = "completed"' in run_steps
        )

    def test_dispatcher_gates_on_the_verify_job_conclusion_not_the_run_conclusion(
        self,
    ) -> None:
        """The dispatched run's own overall conclusion is expected to be
        "failure" on every correct run -- the only signal this job may act
        on is test-check-project-verify's own job conclusion (Codex
        review)."""
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        run_steps = " ".join(s.get("run", "") for s in _steps(job))
        assert 'select(.name | test("verify"))' in run_steps
        assert "verify_conclusion" in run_steps
        assert '"$verify_conclusion" != "success"' in run_steps
        # Must not gate on the run's own top-level conclusion field.
        assert "--json conclusion" not in run_steps

    def test_dispatcher_poll_budget_exceeds_the_child_workflow_worst_case(
        self,
    ) -> None:
        """The completion-wait poll loop's own total budget (iterations *
        sleep seconds) -- and the job's own timeout-minutes, the real
        backstop -- must both exceed the dispatched workflow's worst-case
        sequential duration: test-check-project-stage, then
        check-project.yml's plan -> check -> aggregate (sequential, not
        parallel -- check needs: plan, aggregate needs: [plan, check]), then
        test-check-project-verify. A fixed poll bound that undersold this
        real worst case would report a scheduler failure on an in-progress,
        entirely healthy run (Codex review)."""
        schedule_data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = schedule_data["jobs"]["dispatch"]
        run_steps = " ".join(s.get("run", "") for s in _steps(job))

        fixture_data = _load(TEST_CHECK_PROJECT_FAILURE_PATH)
        stage_timeout = fixture_data["jobs"]["test-check-project-stage"][
            "timeout-minutes"
        ]
        verify_timeout = fixture_data["jobs"]["test-check-project-verify"][
            "timeout-minutes"
        ]

        check_project_data = _load(CHECK_PROJECT)
        sequential_timeout = sum(
            check_project_data["jobs"][name]["timeout-minutes"]
            for name in ("plan", "check", "aggregate")
        )

        worst_case_minutes = stage_timeout + sequential_timeout + verify_timeout

        # Parse the completion-wait loop's own `for _ in $(seq 1 N); do ...
        # sleep S; done` shape directly out of the script, rather than
        # hand-copying the numbers here, so this test can't itself drift
        # out of sync with the workflow the way the workflow drifted from
        # check-project.yml's real timeouts.
        match = re.search(
            r"Waiting for run.*?seq 1 (\d+).*?sleep (\d+)\n\s*done",
            run_steps,
            re.DOTALL,
        )
        assert match, "could not find the completion-wait poll loop"
        iterations, sleep_seconds = int(match.group(1)), int(match.group(2))
        poll_budget_minutes = iterations * sleep_seconds / 60

        assert poll_budget_minutes > worst_case_minutes, (
            poll_budget_minutes,
            worst_case_minutes,
        )
        assert job["timeout-minutes"] > worst_case_minutes

    def test_dispatcher_has_write_permissions_for_its_job(self) -> None:
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        assert job["permissions"]["contents"] == "write"
        assert job["permissions"]["actions"] == "write"
        # Read-only at the workflow level -- the elevated permissions are
        # scoped to just this one job, not inherited as a workflow default.
        assert data["permissions"]["contents"] == "read"

    def test_dispatcher_pins_checkout_to_a_commit_sha(self) -> None:
        """This job carries contents:write + actions:write, so AGENTS.md's
        Action-pinning convention for a write-permission job applies: pin
        `actions/checkout` to a full commit SHA rather than the floating
        `v6` tag (Codex review)."""
        data = _load(SCHEDULE_CHECK_PROJECT_FAILURE_PATH)
        job = data["jobs"]["dispatch"]
        checkout_step = next(
            s
            for s in _steps(job)
            if str(s.get("uses", "")).startswith("actions/checkout@")
        )
        assert checkout_step["uses"] != "actions/checkout@v6"
        # Full commit SHA, not a floating tag -- matches the pin already
        # used by ci.yml/pages.yml/agentready.yml/dependency-review.yml.
        sha = checkout_step["uses"].split("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", sha), checkout_step["uses"]


class TestEveryCheckProjectJobInstallsAbicheckFromItsOwnSource:
    """`pip install .` on the preceding `actions/checkout@v6` step installs
    whatever is at the CALLER's own repository root -- correct only when
    this workflow is invoked from within abicheck/abicheck itself. An
    external consumer (`uses: abicheck/abicheck/.github/workflows/
    check-project.yml@v1` from their own repository) would have every job
    try to install their own project instead of abicheck (Codex review).
    Every job that runs an `abicheck` CLI command must self-checkout
    abicheck's own source first and install from that directory."""

    @pytest.mark.parametrize("job_name", ["plan", "check", "aggregate"])
    def test_job_installs_from_the_self_checkout_not_the_caller_repo(
        self, job_name: str
    ) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"][job_name])
        names = [s.get("name") for s in steps]
        assert "Checkout abicheck (for installing the CLI)" in names or (
            "Checkout abicheck (for installing the CLI and nested Action composition)"
            in names
        )
        install_step = next(s for s in steps if s.get("name") == "Install abicheck")
        assert install_step["run"] == "pip install ./.check-project-src"
        assert install_step["run"] != "pip install ."

    @pytest.mark.parametrize("job_name", ["plan", "check", "aggregate"])
    def test_self_checkout_runs_before_install(self, job_name: str) -> None:
        data = _load(CHECK_PROJECT)
        names = _step_names(data["jobs"][job_name])
        checkout_idx = next(
            i for i, n in enumerate(names) if n and n.startswith("Checkout abicheck")
        )
        install_idx = names.index("Install abicheck")
        assert checkout_idx < install_idx


class TestCandidateResolverRejectsAmbiguousMatches:
    """A glob pattern matching more than one file under candidate/ (e.g.
    both a linker symlink and the real versioned DSO) must fail loud, not
    silently pick the first sorted match and compare/scope against an
    arbitrary artifact (Codex review)."""

    def test_resolve_helper_fails_on_more_than_one_match(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        run = resolver["run"]
        assert "len(matches) > 1" in run
        assert "matches[0] if matches else None" in run

    def test_target_binary_pattern_call_site_passes_a_label(self) -> None:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        run = resolver["run"]
        assert "label=f'target {cell" in run
        assert "label=f'bundle {cell" in run


class TestCandidateResolverConfinesMatchesToTheArtifactRoot:
    """binary_pattern/consumer_binary_pattern/member_binary_patterns come
    from the project's own .abicheck.yml -- an absolute or `../`-escaping
    pattern must not be able to glob outside candidate/ (Codex review)."""

    def _resolver_script(self) -> str:
        data = _load(CHECK_PROJECT)
        steps = _steps(data["jobs"]["check"])
        resolver = next(
            s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
        )
        return resolver["run"]

    def _inner_python(self) -> str:
        # See the sanitizer test above for why this splits on the flag rather
        # than the interpreter name.
        return self._resolver_script().split(' -c "', 1)[1].rsplit('"', 1)[0]

    def test_resolver_checks_commonpath_against_the_root(self) -> None:
        run = self._inner_python()
        assert "os.path.commonpath" in run
        assert "root_abs" in run

    def _run_bash(
        self, tmp_path: Path, matrix: dict[str, Any]
    ) -> subprocess.CompletedProcess[str]:
        # Run the FULL bash script (not just the inner python3 -c text) via
        # `bash -c`, exactly as the real runner does -- the inner script
        # contains bash-escaped `\"` sequences that are only valid Python
        # once bash's own double-quote unescaping has run; extracting and
        # feeding the raw text straight to `python3 -c` skips that step and
        # is a SyntaxError (backslash in an f-string expression part).
        github_output = tmp_path / "github_output"
        github_output.write_text("")
        env = {
            **os.environ,
            "MATRIX_JSON": json.dumps(matrix),
            "GITHUB_OUTPUT": str(github_output),
        }
        result = subprocess.run(
            ["bash", "-c", self._resolver_script()],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        result.stdout = github_output.read_text() + result.stdout
        return result

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_escaping_pattern_is_rejected_end_to_end(self, tmp_path: Path) -> None:
        (tmp_path / "candidate").mkdir()
        (tmp_path / "candidate" / "libexample.so").write_text("real")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leaked.so").write_text("secret")

        result = self._run_bash(
            tmp_path,
            {
                "kind": "target",
                "name": "libexample",
                "binary_pattern": "../outside/leaked.so",
            },
        )
        assert result.returncode != 0
        assert "'..' path component" in result.stderr

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_absolute_pattern_is_rejected_without_globbing(
        self, tmp_path: Path
    ) -> None:
        # An absolute recursive pattern like '/**/*' would otherwise expand
        # glob.glob against the whole runner filesystem BEFORE the
        # commonpath confinement check ever ran -- a needlessly slow/heavy
        # pre-check failure instead of an immediate, contained validation
        # error (Codex review). Reject upfront on the pattern string alone,
        # before glob.glob is ever called.
        (tmp_path / "candidate").mkdir()

        result = self._run_bash(
            tmp_path,
            {"kind": "target", "name": "libexample", "binary_pattern": "/etc/passwd"},
        )
        assert result.returncode != 0
        assert "is absolute or contains a '..' path component" in result.stderr

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_in_root_pattern_still_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "candidate").mkdir()
        (tmp_path / "candidate" / "libexample.so").write_text("real")

        result = self._run_bash(
            tmp_path,
            {"kind": "target", "name": "libexample", "binary_pattern": "*.so"},
        )
        assert result.returncode == 0, result.stderr
        assert "new-library=candidate/libexample.so" in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_newline_bearing_match_is_rejected_end_to_end(self, tmp_path: Path) -> None:
        # A candidate filename containing a newline would otherwise be
        # written as a bare key=value line to $GITHUB_OUTPUT, which GitHub
        # documents as line-oriented -- letting it through could inject or
        # override a later output line (Codex review).
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        (candidate / "libfoo\nconsumer-binary=evil.so").write_bytes(b"real")

        result = self._run_bash(
            tmp_path,
            {"kind": "target", "name": "libexample", "binary_pattern": "*.so"},
        )
        assert result.returncode != 0
        assert "newline character" in result.stderr
        assert "consumer-binary=evil.so" not in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_carriage_return_bearing_match_is_also_rejected(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        (candidate / "libfoo\rbar.so").write_bytes(b"real")

        result = self._run_bash(
            tmp_path,
            {"kind": "target", "name": "libexample", "binary_pattern": "*.so"},
        )
        assert result.returncode != 0
        assert "newline character" in result.stderr

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_bundle_members_with_colliding_basenames_are_rejected(
        self, tmp_path: Path
    ) -> None:
        # Two distinct members resolving to files with the same basename
        # (e.g. build/linux/libfoo.so vs. build/plugins/libfoo.so) would
        # otherwise silently overwrite one another in the shared flat
        # bundle-staging/ directory (Codex review).
        candidate = tmp_path / "candidate"
        (candidate / "linux").mkdir(parents=True)
        (candidate / "plugins").mkdir(parents=True)
        (candidate / "linux" / "libfoo.so").write_bytes(b"core")
        (candidate / "plugins" / "libfoo.so").write_bytes(b"plugin")

        result = self._run_bash(
            tmp_path,
            {
                "kind": "bundle",
                "name": "mybundle",
                "member_binary_patterns": {
                    "core": "linux/libfoo.so",
                    "plugin": "plugins/libfoo.so",
                },
            },
        )
        assert result.returncode != 0
        assert "libfoo.so" in result.stderr
        assert "'core'" in result.stderr and "'plugin'" in result.stderr

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_bundle_members_with_distinct_basenames_still_resolve(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        (candidate / "libpvxs.so").write_bytes(b"core")
        (candidate / "libpvxsIoc.so").write_bytes(b"ioc")

        result = self._run_bash(
            tmp_path,
            {
                "kind": "bundle",
                "name": "pvxs",
                "member_binary_patterns": {
                    "libpvxs": "libpvxs.so",
                    "libpvxsIoc": "libpvxsIoc.so",
                },
            },
        )
        assert result.returncode == 0, result.stderr
        assert "new-library=bundle-staging" in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "These steps run under the Actions runner's own `shell: bash`, "
            "which is a real POSIX bash on every platform -- this test "
            "exercises that behavior. (The check job's runs-on now comes "
            "from the cell's profile and can be windows-latest, so the "
            "older wording here, that the workflow only ever runs on "
            "ubuntu-latest, no longer holds -- G34 Phase C.) What this "
            "*test* cannot get is a POSIX bash on a Windows test runner: "
            "plain 'bash' on PATH "
            "resolves to the System32 WSL launcher (not Git Bash) and fails "
            "before running anything if no WSL distro is installed, which "
            "isn't a bug in the workflow script itself."
        ),
    )
    def test_stale_preexisting_bundle_staging_dir_is_cleared_first(
        self, tmp_path: Path
    ) -> None:
        # The earlier `actions/checkout` step already populates the whole
        # workspace from the caller's own repository -- a checked-in
        # bundle-staging/ tree there must not survive into the comparison,
        # since `compare` fans out a directory operand by collecting every
        # supported file under it (Codex review).
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        (candidate / "libpvxs.so").write_bytes(b"core")

        stale = tmp_path / "bundle-staging"
        stale.mkdir()
        (stale / "leftover.so").write_bytes(b"stale repo content")

        result = self._run_bash(
            tmp_path,
            {
                "kind": "bundle",
                "name": "pvxs",
                "member_binary_patterns": {"libpvxs": "libpvxs.so"},
            },
        )
        assert result.returncode == 0, result.stderr
        assert not (stale / "leftover.so").exists()
        assert (stale / "libpvxs.so").exists()


class TestCheckTargetIdentityPassthrough:
    """check-target's own github.action_repository/github.action_ref
    auto-detection cannot tell a nested `uses: ./.check-project-src/...`
    (or `./.check-single-src/...`) local reference apart from a genuine
    same-repository invocation -- both reusable workflows must pass their
    own already-resolved identity through explicitly (Codex review)."""

    @pytest.mark.parametrize(
        ("path", "job_name"),
        [(CHECK_PROJECT, "check"), (CHECK_SINGLE, "check")],
    )
    def test_run_check_target_forwards_resolved_identity(
        self, path: Path, job_name: str
    ) -> None:
        data = _load(path)
        steps = _steps(data["jobs"][job_name])
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        assert run_step["with"]["abicheck-repository"] == (
            "${{ steps.identity.outputs.repository }}"
        )
        assert run_step["with"]["abicheck-ref"] == "${{ steps.identity.outputs.ref }}"
