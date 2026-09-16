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

"""``publish-baseline.yml``'s existing-set mode: publish without capturing.

**Bug class:** ``workflow.mode_branch_leaves_a_downstream_step_unfed``. A
second path through a job that already has one is only as correct as the
steps *after* the branch: a mode that skips the capture step but leaves a
later step reading that step's outputs publishes an asset with an empty
content digest, and the immutability comparison it feeds then silently
accepts anything.

So the invariant here is stated over the whole job rather than over the new
steps: **every downstream step that consumes a capture-produced value must
consume an existing-set-produced one in the same expression**, and **no step
that builds, dumps, queries or compares may run in existing-set mode.** Both
are derived from the workflow file itself, so a new capture output added
later is covered without this module being edited.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PUBLISH_BASELINE = WORKFLOWS_DIR / "publish-baseline.yml"

#: Steps that exist only to capture. Each must be gated to capture mode.
CAPTURE_ONLY_STEPS = (
    "Download build-output artifact",
    "Derive baseline libraries from build-output.json",
    "Dump baseline-set",
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(PUBLISH_BASELINE.read_text(encoding="utf-8"))


def _publish_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return list(workflow["jobs"]["publish"]["steps"])


def _step(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    for step in _publish_steps(workflow):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the publish job")


class TestTheModeIsDeclaredAndResolvedOnce:
    def test_the_input_exists_and_defaults_to_capture(
        self, workflow: dict[str, Any]
    ) -> None:
        spec = workflow[True]["workflow_call"]["inputs"]["baseline-set-artifact-prefix"]
        assert spec["default"] == "", (
            "existing-set mode must be opt-in: a non-empty default would "
            "change every current caller's behaviour"
        )

    def test_the_mode_is_resolved_in_one_place_and_published_as_an_output(
        self, workflow: dict[str, Any]
    ) -> None:
        """One resolution, consumed by both jobs. Two independent
        derivations of "which mode is this" is how the branch gets to
        disagree with itself."""
        discover = workflow["jobs"]["discover"]
        assert discover["outputs"]["mode"] == "${{ steps.mode.outputs.mode }}"
        resolvers = [s for s in discover["steps"] if s.get("id") == "mode"]
        assert len(resolvers) == 1

    def test_the_two_modes_are_mutually_exclusive(
        self, workflow: dict[str, Any]
    ) -> None:
        resolver = next(
            s for s in workflow["jobs"]["discover"]["steps"] if s.get("id") == "mode"
        )
        assert "exit 1" in resolver["run"], (
            "configuring both modes must fail, not be resolved by step order"
        )


class TestEveryCaptureStepIsGated:
    @pytest.mark.parametrize("name", CAPTURE_ONLY_STEPS)
    def test_a_capture_step_never_runs_in_existing_set_mode(
        self, workflow: dict[str, Any], name: str
    ) -> None:
        step = _step(workflow, name)
        assert step.get("if") == "needs.discover.outputs.mode == 'capture'", (
            f"{name} would run while publishing a pre-captured set"
        )

    def test_no_capture_step_escaped_the_list(self, workflow: dict[str, Any]) -> None:
        """The vacuity guard for the list above: any step that reaches for
        the capture Action or the build-output artifact must be one of the
        gated names, so a fourth capture step added later fails here rather
        than quietly running in both modes."""
        capturing = {
            str(step.get("name"))
            for step in _publish_steps(workflow)
            if "actions/baseline" in str(step.get("uses", ""))
            or "build-output" in str(step.get("with", {}))
            or "build-output" in str(step.get("run", ""))
        }
        assert capturing <= set(CAPTURE_ONLY_STEPS), sorted(
            capturing - set(CAPTURE_ONLY_STEPS)
        )


class TestNoDownstreamStepIsLeftUnfed:
    """The bug class itself, derived from the file rather than listed.

    Every ``steps.baseline.outputs.<x>`` reference below the branch must sit
    in an expression that also names ``steps.precaptured.outputs.<x>`` -- the
    same value, from whichever mode produced it.
    """

    def test_every_capture_output_reference_has_an_existing_set_counterpart(
        self, workflow: dict[str, Any]
    ) -> None:
        text = PUBLISH_BASELINE.read_text(encoding="utf-8")
        unfed: list[str] = []
        for line in text.splitlines():
            for match in re.finditer(r"steps\.baseline\.outputs\.([a-z-]+)", line):
                # The capture step's own `id: baseline` declaration and the
                # prose around it are not consumers.
                if line.lstrip().startswith("#"):
                    continue
                field = match.group(1)
                if f"steps.precaptured.outputs.{field}" not in line:
                    unfed.append(line.strip())
        assert unfed == [], (
            "these consume a capture-only output with no existing-set "
            f"counterpart: {unfed}"
        )

    @pytest.mark.parametrize(
        "field", ["baseline-path", "content-digest", "manifest-path"]
    )
    def test_the_validated_set_supplies_each_shared_value(
        self, workflow: dict[str, Any], field: str
    ) -> None:
        run = _step(workflow, "Validate pre-captured baseline-set")["run"]
        assert f"{field}=" in run, (
            f"the validation step never writes {field} to GITHUB_OUTPUT, so "
            "the downstream expression resolves to empty in existing-set mode"
        )

    def test_the_immutability_comparison_is_fed_from_disk_not_from_claims(
        self, workflow: dict[str, Any]
    ) -> None:
        """A pre-captured set's digest must come from its real bytes. Reading
        the manifest's own declared digest would let a set with honest-looking
        claims and different content read as an identical republication --
        the one thing an immutable channel must never do."""
        run = _step(workflow, "Validate pre-captured baseline-set")["run"]
        assert "recompute_content_digest_from_disk" in run
        assert "compute_content_digest(" not in run


class TestTheExistingSetPathPublishesThroughTheSameChain:
    def test_staging_and_upload_are_not_duplicated_for_the_new_mode(
        self, workflow: dict[str, Any]
    ) -> None:
        """No second publisher: the mode branch converges before packaging,
        so the immutability, symlink, project-ref, fact-set, generation and
        schema checks apply identically to a set this workflow did not
        build."""
        names = [str(s.get("name")) for s in _publish_steps(workflow)]
        assert names.count("Package baseline-set") == 1
        assert (
            names.count(
                "Upload release asset (fails closed on an immutability violation)"
            )
            == 1
        )

    def test_packaging_and_upload_are_not_mode_gated(
        self, workflow: dict[str, Any]
    ) -> None:
        for name in (
            "Package baseline-set",
            "Upload release asset (fails closed on an immutability violation)",
        ):
            assert "if" not in _step(workflow, name), (
                f"{name} must run in both modes -- gating it is how one mode "
                "acquires its own, separately-drifting publication path"
            )

    def test_the_existing_set_path_validates_before_it_stages(
        self, workflow: dict[str, Any]
    ) -> None:
        names = [str(s.get("name")) for s in _publish_steps(workflow)]
        assert names.index("Validate pre-captured baseline-set") < names.index(
            "Package baseline-set"
        )

    def test_the_validator_is_the_importable_one_not_a_shell_reimplementation(
        self, workflow: dict[str, Any]
    ) -> None:
        run = _step(workflow, "Validate pre-captured baseline-set")["run"]
        assert "validate_precaptured_baseline_set" in run
        assert "abicheck.buildsource.baseline_precaptured" in run


class TestExistingSetModeNeverBuilds:
    """Publishing a set someone already captured must not reach for a
    toolchain. Checked over the executable surface of every step that can
    run in existing-set mode, the same way
    `tests/test_action_report_contract.py` checks the report publisher."""

    FORBIDDEN = (
        "castxml",
        "compile_commands.json",
        "abicheck dump",
        "abicheck compare",
        "cmake",
        "gcc",
        "g++",
        "install-deps",
    )

    def test_no_existing_set_step_names_a_build_tool(
        self, workflow: dict[str, Any]
    ) -> None:
        offenders: list[tuple[str, str]] = []
        for step in _publish_steps(workflow):
            if step.get("if") == "needs.discover.outputs.mode == 'capture'":
                continue
            surface = "\n".join(
                line.split("#", 1)[0]
                for line in (
                    str(step.get("run", "")) + "\n" + str(step.get("uses", ""))
                ).splitlines()
            )
            for token in self.FORBIDDEN:
                if token in surface:
                    offenders.append((str(step.get("name")), token))
        assert offenders == [], offenders

    def test_the_scan_can_actually_fire(self) -> None:
        planted = "run: castxml --version"
        assert [t for t in self.FORBIDDEN if t in planted] == ["castxml"]


class TestTheEmptyRunFailureNamesTheRightArtifact:
    """Review finding (CodeRabbit, PR #1319): the no-profiles guard was
    hard-wired to build-output.

    Telling an existing-set caller to check its *build-output* artifacts sends
    them to debug a job this run never ran. The guard itself (fail rather than
    report success having published nothing) was already right; only what it
    names was not.
    """

    @pytest.fixture
    def fail_step(self, workflow: dict[str, Any]) -> dict[str, Any]:
        steps = workflow["jobs"]["no-profiles"]["steps"]
        return next(s for s in steps if s.get("name") == "Fail")

    def test_it_still_fails_the_run(self, workflow: dict[str, Any]) -> None:
        """The property that matters most, pinned before the wording: a run
        that published nothing must never report success."""
        job = workflow["jobs"]["no-profiles"]
        assert job["if"] == "needs.discover.outputs.has-profiles != 'true'"
        assert (
            "exit 1" in next(s for s in job["steps"] if s.get("name") == "Fail")["run"]
        )

    def test_both_modes_are_named(self, fail_step: dict[str, Any]) -> None:
        run = fail_step["run"]
        assert "baseline-set artifacts were found" in run
        assert "build-output artifacts were found" in run

    def test_each_mode_reports_its_own_prefix(self, fail_step: dict[str, Any]) -> None:
        env = fail_step["env"]
        assert env["MODE"] == "${{ needs.discover.outputs.mode }}"
        assert env["BASELINE_SET_ARTIFACT_PREFIX"] == (
            "${{ inputs.baseline-set-artifact-prefix }}"
        )
        assert env["BUILD_OUTPUT_ARTIFACT_PREFIX"] == (
            "${{ inputs.build-output-artifact-prefix }}"
        )
        # The two messages must not be able to print the other mode's prefix.
        run = fail_step["run"]
        baseline_line = next(
            line for line in run.splitlines() if "baseline-set artifacts" in line
        )
        build_line = next(
            line for line in run.splitlines() if "build-output artifacts" in line
        )
        assert "BASELINE_SET_ARTIFACT_PREFIX" in baseline_line
        assert "BUILD_OUTPUT_ARTIFACT_PREFIX" not in baseline_line
        assert "BUILD_OUTPUT_ARTIFACT_PREFIX" in build_line
        assert "BASELINE_SET_ARTIFACT_PREFIX" not in build_line

    def test_the_job_name_does_not_claim_one_mode(
        self, workflow: dict[str, Any]
    ) -> None:
        assert "build-output" not in workflow["jobs"]["no-profiles"]["name"]
