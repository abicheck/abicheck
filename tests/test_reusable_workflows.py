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

    def test_baseline_artifact_downloads_into_baseline_path(self) -> None:
        data = _load(CHECK_SINGLE)
        steps = _steps(data["jobs"]["check"])
        step = next(s for s in steps if s.get("name") == "Download baseline artifact")
        assert step["with"]["path"] == "${{ inputs.baseline-path }}"


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
        assert sanitize["env"]["CHECK_ID"] == "${{ steps.run.outputs.check-id }}"

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
        script = sanitize["run"].split('python3 -c "', 1)[1].rsplit('"', 1)[0]

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
        assert (
            sanitize.get("if")
            == upload.get("if")
            == ("always() && steps.run.outputs.report-path != ''")
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
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
        return self._resolver_script().split('python3 -c "', 1)[1].rsplit('"', 1)[0]

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
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
        assert "outside candidate/" in result.stderr

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
            "The actual reusable workflow only ever runs on runs-on: "
            "ubuntu-latest -- this test exercises that real Linux bash "
            "behavior. On windows-latest CI runners, plain 'bash' on PATH "
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
