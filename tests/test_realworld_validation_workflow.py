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

"""Structural tests for `.github/workflows/realworld-validation.yml`
(CLI-audit P1: this lane must not be skipped for a change to the
subsystems it actually exercises against a real package binary).

Mirrors ``test_reusable_workflows.py``'s "parse the real file, assert the
real content" discipline rather than re-implementing the YAML.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "realworld-validation.yml"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _pr_paths() -> list[str]:
    data = _load()
    # YAML parses the bare `on:` key as the boolean True in PyYAML's
    # default resolver -- index with the boolean, not the string "on".
    return data[True]["pull_request"]["paths"]


class TestPullRequestPathsCoverTheSubsystemsThisLaneExercises:
    """A paths filter scoped only to package/bundle plumbing let a change to
    contract evaluation, comparability, compiler-profile resolution, or a
    report schema skip the one lane that runs `abicheck compare` against a
    real package binary rather than a synthetic snapshot -- a status-review
    gap fixed here. These are regression guards against the filter quietly
    narrowing back to that gap, not an exhaustive path list."""

    def test_original_package_and_bundle_paths_are_still_covered(self) -> None:
        paths = _pr_paths()
        for expected in (
            "abicheck/package.py",
            "abicheck/cli_compare_release.py",
            "abicheck/bundle.py",
        ):
            assert expected in paths

    def test_contract_pipeline_paths_are_covered(self) -> None:
        paths = _pr_paths()
        assert "abicheck/contract_*.py" in paths
        assert "abicheck/compatibility_evaluation_*.py" in paths
        assert "abicheck/pack_application.py" in paths

    def test_comparability_paths_are_covered(self) -> None:
        assert "abicheck/comparability*.py" in _pr_paths()

    def test_consumer_graph_paths_are_covered(self) -> None:
        assert "abicheck/impact/**" in _pr_paths()

    def test_compiler_profile_paths_are_covered(self) -> None:
        paths = _pr_paths()
        for expected in (
            "abicheck/buildsource/project_targets.py",
            "abicheck/buildsource/run_plan.py",
            "abicheck/buildsource/toolchain_probe.py",
            "abicheck/buildsource/toolchain_bindings.py",
        ):
            assert expected in paths

    def test_report_schema_paths_are_covered(self) -> None:
        assert "abicheck/schemas/*.schema.json" in _pr_paths()

    def test_checker_core_paths_are_covered(self) -> None:
        paths = _pr_paths()
        assert "abicheck/checker.py" in paths
        assert "abicheck/checker_policy.py" in paths

    def test_the_workflow_file_itself_is_covered(self) -> None:
        # Otherwise a future edit to the paths list itself could silently
        # narrow scope without the change ever running through this lane.
        assert ".github/workflows/realworld-validation.yml" in _pr_paths()


class TestContractEvaluationStepAgainstARealPackage:
    """The lane must actually exercise --contract-evaluation against a real
    package, not merely be triggered by a change to it (CLI-audit P1) --
    the whole reason a paths-filter fix alone is not enough."""

    def _step(self) -> dict:
        data = _load()
        steps = data["jobs"]["ubuntu-package-smoke"]["steps"]
        return next(
            s
            for s in steps
            if s.get("name")
            == "Compare a real Debian package against itself, with --contract-evaluation"
        )

    def test_the_step_exists(self) -> None:
        assert self._step() is not None

    def test_the_step_passes_contract_evaluation_and_a_domain(self) -> None:
        run = self._step()["run"]
        assert "--contract-evaluation" in run
        assert "--contract public" in run

    def test_the_step_asserts_a_compatible_verdict(self) -> None:
        run = self._step()["run"]
        assert 'in {"NO_CHANGE", "COMPATIBLE"}' in run

    def test_the_step_asserts_clean_contract_coverage(self) -> None:
        """A self-compare has complete evidence on both sides -- the
        orthogonal ADR-049 Phase 7 coverage axis must resolve to 0, not
        merely be present, both at the release level and per-library."""
        run = self._step()["run"]
        assert 'data["contract_coverage_exit_contribution"] == 0' in run
        assert 'lib["contract_coverage_exit_contribution"] == 0' in run

    def test_the_step_runs_after_the_plain_self_compare(self) -> None:
        data = _load()
        steps = data["jobs"]["ubuntu-package-smoke"]["steps"]
        names = [s.get("name") for s in steps]
        assert names.index(
            "Compare a real Debian package against itself"
        ) < names.index(
            "Compare a real Debian package against itself, with --contract-evaluation"
        )
