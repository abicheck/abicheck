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

"""Execute check-single.yml's shell steps against a hostile corpus.

`test_reusable_workflows.py` asserts these steps' *text* — that the clear step
is literally ``rm -rf .check-single-baseline``, that it carries no ``env:``,
that it precedes its download. Those guards are worth keeping, and they are
also exactly the shape that let #705 ship: asserting a workflow's text proves
nothing about what happens when a hostile value reaches it, which is why #758
had to add the executing test afterwards.

These run the real bodies, in a real temporary workspace, with a real
``$GITHUB_OUTPUT``, and assert the *effect*: what was deleted, what survived,
and precisely which records were written.
"""

from __future__ import annotations

import pytest
from _workflow_exec import (
    FORBIDDEN_ARTIFACT_NAME_CHARS,
    HOSTILE_SCALAR_CORPUS,
    find_run_step,
    have_bash,
    make_workspace,
    outside_is_intact,
    run_step,
)

CHECK_SINGLE = "check-single.yml"

pytestmark = pytest.mark.skipif(not have_bash(), reason="needs a real bash")


# --- The caller-controlled value: check-id ------------------------------------
#
# CHECK_ID reaches this step from `steps.run.outputs.check-id`, which derives
# from caller input, and its sanitized form becomes an uploaded *artifact
# name*. That is the trust boundary on this workflow.
#
# The corpus and forbidden-character set are shared (`_workflow_exec.py`) --
# `check-project.yml` carries an independently-maintained copy of this exact
# sanitizer, checked against the identical corpus in
# `test_check_project_workflow_execution.py` (bug-class-regression-testing.md
# Phase 8).
HOSTILE_CHECK_IDS = HOSTILE_SCALAR_CORPUS
_FORBIDDEN_ARTIFACT_CHARS = FORBIDDEN_ARTIFACT_NAME_CHARS


def _sanitize(tmp_path, check_id: str):
    step = find_run_step(CHECK_SINGLE, "check", "Sanitize check-id for artifact name")
    workspace = make_workspace(tmp_path)
    return run_step(step, workspace=workspace, env={"CHECK_ID": check_id})


class TestCheckIdSanitizationUnderHostileInput:
    @pytest.mark.parametrize("check_id", HOSTILE_CHECK_IDS)
    def test_result_is_always_a_safe_artifact_name(
        self, tmp_path, check_id: str
    ) -> None:
        result = _sanitize(tmp_path, check_id)
        assert result.returncode == 0, result.stderr
        value = result.outputs["id"]
        unsafe = sorted(set(value) & _FORBIDDEN_ARTIFACT_CHARS)
        assert not unsafe, f"sanitized id {value!r} still contains {unsafe}"
        assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value), (
            f"sanitized id {value!r} still contains a control character"
        )

    @pytest.mark.parametrize("check_id", HOSTILE_CHECK_IDS)
    def test_exactly_one_record_is_written(self, tmp_path, check_id: str) -> None:
        """The #706 bug class, on this step.

        A newline surviving into $GITHUB_OUTPUT lets a caller append arbitrary
        step outputs. Counting *records* — not just checking the `id` value —
        is what makes an injected extra line visible.
        """
        result = _sanitize(tmp_path, check_id)
        assert len(result.output_lines) == 1, (
            f"expected one record, got {result.output_lines!r}"
        )
        assert result.output_lines[0].startswith("id=")

    @pytest.mark.parametrize("check_id", HOSTILE_CHECK_IDS)
    def test_the_result_is_always_a_single_harmless_path_component(
        self, tmp_path, check_id: str
    ) -> None:
        """A download writes to ``<path>/<artifact-name>``, so the name must be
        one path component and must not be a directory-relative special.

        Note what is *not* asserted: `../../etc/passwd` sanitizes to
        `.._.._etc_passwd-<hash>`, which starts with `..` and is fine — the
        separators are gone, so it is an ordinary component. Requiring "no
        leading dots" would be the test inventing a rule. What matters is that
        it can never be exactly `.` or `..`, and the appended digest is what
        guarantees that even for a check-id of literally `..`.
        """
        result = _sanitize(tmp_path, check_id)
        value = result.outputs["id"]
        assert outside_is_intact(tmp_path)
        assert "/" not in value and "\\" not in value
        assert value not in (".", ".."), value


class TestCheckIdSanitizationIsInjective:
    """The must-merge / must-not-merge pair for this sanitizer.

    Safety alone is satisfiable by returning a constant, which would make every
    matrix leg upload to one artifact name and silently overwrite the others.
    The step appends a hash of the *original* id precisely so two ids that
    sanitize to the same readable prefix stay distinct — so both halves are
    asserted, not just the safe-characters half.
    """

    @pytest.mark.parametrize(
        "left, right",
        [
            ("lib/a", "lib:a"),  # both sanitize to lib_a
            ("a b", "a-b"),  # space vs dash
            ("x!y", "x?y"),
            ("../a", "..%a"),
            ("lib\na", "lib\ta"),
        ],
    )
    def test_distinct_ids_that_sanitize_alike_stay_distinct(
        self, tmp_path, left: str, right: str
    ) -> None:
        a = _sanitize(tmp_path / "a", left).outputs["id"]
        b = _sanitize(tmp_path / "b", right).outputs["id"]
        assert a != b, f"{left!r} and {right!r} both produced {a!r}"

    def test_the_same_id_is_stable_across_runs(self, tmp_path) -> None:
        """A rerun must upload under the same artifact name."""
        a = _sanitize(tmp_path / "a", "my-lib").outputs["id"]
        b = _sanitize(tmp_path / "b", "my-lib").outputs["id"]
        assert a == b

    def test_a_benign_id_stays_readable(self, tmp_path) -> None:
        """Sanitizing must not make the common case unrecognisable."""
        assert _sanitize(tmp_path, "libfoo").outputs["id"].startswith("libfoo-")


# --- The staging-clear steps --------------------------------------------------

CLEAR_STEPS = [
    pytest.param(
        "Clear candidate staging before download", "candidate", id="candidate"
    ),
    pytest.param(
        "Clear baseline staging before download",
        ".check-single-baseline",
        id="baseline",
    ),
    pytest.param(
        "Clear build-output staging before download", "build-output", id="build-output"
    ),
]


class TestStagingClearActuallyClears:
    @pytest.mark.parametrize("step_name, staging", CLEAR_STEPS)
    def test_a_stale_checked_in_file_does_not_survive(
        self, tmp_path, step_name: str, staging: str
    ) -> None:
        """The property the structural test can only describe.

        `actions/checkout` populates the workspace from the caller's own
        repository, so a checked-in file at the staging path would otherwise be
        scanned as if it were the downloaded artifact.
        """
        workspace = make_workspace(
            tmp_path, files={f"{staging}/stale.so": "checked-in impostor"}
        )
        result = run_step(
            find_run_step(CHECK_SINGLE, "check", step_name), workspace=workspace
        )
        assert result.returncode == 0, result.stderr
        assert not (workspace / staging).exists()

    @pytest.mark.parametrize("step_name, staging", CLEAR_STEPS)
    def test_unrelated_workspace_content_survives(
        self, tmp_path, step_name: str, staging: str
    ) -> None:
        """Clearing must be scoped to its own staging directory."""
        workspace = make_workspace(
            tmp_path,
            files={f"{staging}/stale.so": "x", "src/keep.c": "int main(void){}"},
        )
        run_step(find_run_step(CHECK_SINGLE, "check", step_name), workspace=workspace)
        assert (workspace / "src" / "keep.c").is_file()

    @pytest.mark.parametrize("step_name, staging", CLEAR_STEPS)
    def test_nothing_outside_the_workspace_is_touched(
        self, tmp_path, step_name: str, staging: str
    ) -> None:
        workspace = make_workspace(tmp_path, files={f"{staging}/x": "x"})
        run_step(find_run_step(CHECK_SINGLE, "check", step_name), workspace=workspace)
        assert outside_is_intact(tmp_path)

    @pytest.mark.parametrize("step_name, staging", CLEAR_STEPS)
    def test_a_symlinked_staging_path_does_not_delete_the_target(
        self, tmp_path, step_name: str, staging: str
    ) -> None:
        """A checked-in *symlink* at the staging path is the escape shape.

        `rm -rf` on a symlink removes the link, not the tree it points at —
        this pins that, because the opposite would let a caller's repository
        delete anything the runner can reach.
        """
        workspace = make_workspace(tmp_path)
        victim = tmp_path / "outside"
        (workspace / staging).parent.mkdir(parents=True, exist_ok=True)
        try:
            (workspace / staging).symlink_to(victim, target_is_directory=True)
        except OSError as exc:  # Windows without the symlink privilege
            pytest.skip(f"cannot create a symlink here: {exc}")
        result = run_step(
            find_run_step(CHECK_SINGLE, "check", step_name), workspace=workspace
        )
        assert result.returncode == 0, result.stderr
        assert outside_is_intact(tmp_path), "rm -rf followed the symlink"
        assert not (workspace / staging).exists()

    @pytest.mark.parametrize("step_name, staging", CLEAR_STEPS)
    def test_clearing_an_absent_path_is_not_an_error(
        self, tmp_path, step_name: str, staging: str
    ) -> None:
        """The ordinary case: no stale file, and the step still succeeds."""
        workspace = make_workspace(tmp_path)
        result = run_step(
            find_run_step(CHECK_SINGLE, "check", step_name), workspace=workspace
        )
        assert result.returncode == 0, result.stderr


# --- The identity step --------------------------------------------------------


class TestWorkflowIdentityCapture:
    STEP = "Capture this reusable workflow's identity"

    def _run(self, tmp_path, **env):
        base = {
            "WORKFLOW_REF": "owner/repo/.github/workflows/check-single.yml@refs/heads/main",
            "WORKFLOW_SHA": "0123456789abcdef",
            "FALLBACK_REPOSITORY": "fallback/repo",
            "FALLBACK_REF": "refs/heads/fallback",
        }
        base.update(env)
        return run_step(
            find_run_step(CHECK_SINGLE, "check", self.STEP),
            workspace=make_workspace(tmp_path),
            env=base,
        )

    def test_repository_is_parsed_out_of_the_workflow_ref(self, tmp_path) -> None:
        result = self._run(tmp_path)
        assert result.outputs == {
            "repository": "owner/repo",
            "ref": "0123456789abcdef",
        }

    def test_falls_back_when_the_context_values_are_empty(self, tmp_path) -> None:
        result = self._run(tmp_path, WORKFLOW_REF="", WORKFLOW_SHA="")
        assert result.outputs["repository"] == "fallback/repo"
        assert result.outputs["ref"] == "refs/heads/fallback"

    def test_a_nested_workflow_path_still_yields_the_repository(self, tmp_path) -> None:
        result = self._run(
            tmp_path,
            WORKFLOW_REF="o/r/.github/workflows/dir/nested.yml@refs/pull/1/merge",
        )
        assert result.outputs["repository"] == "o/r"

    def test_writes_exactly_two_records(self, tmp_path) -> None:
        """Record-count, not just values — an injected third record is the
        thing a value-only assertion cannot see.

        Note on reachability: `WORKFLOW_SHA`/`WORKFLOW_REF` are
        `github.workflow_sha`/`github.workflow_ref`, both runner-provided, and
        git refnames cannot contain a newline — so unlike `CHECK_ID` above this
        is not a caller-reachable injection today. It is pinned because the
        step writes to `$GITHUB_OUTPUT` with a bare `echo`, so the property
        would silently stop holding if either value ever became
        caller-influenced.
        """
        assert len(self._run(tmp_path).output_lines) == 2
