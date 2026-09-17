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

"""``publish-baseline.yml``'s cross-run acquisition, as wired.

**Bug class:** ``workflow.second_acquisition_path_bypasses_the_first_one's
_checks``. The rules themselves live in
``abicheck.frontends.action.precaptured_source`` and are tested there. What
only the YAML can get wrong is whether the new path *reaches* them -- and
specifically whether it converges on the same validator, staging and
immutability chain the same-run path already goes through, or quietly grows a
shortcut beside it.

So the invariants here are stated over the workflow document:

* both acquisition modes feed the one validator, and nothing downstream of it
  branches on how the bytes arrived;
* the download is bound to the artifact **id** discovery established, with no
  name-pattern fallback;
* the read-only producer query and the write-capable publication stay in
  different jobs with different permission sets;
* a producer-local directory is never the interface -- every crossing is an
  artifact through the API.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from _workflow_exec import bash_executable, require_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_BASELINE = REPO_ROOT / ".github" / "workflows" / "publish-baseline.yml"

VALIDATE_STEP = "Validate pre-captured baseline-set"
SELECT_STEP = "Verify the producer run and select its baseline-sets"
SAME_RUN_DOWNLOAD = "Download pre-captured baseline-set (same run)"
CROSS_RUN_DOWNLOAD = "Download pre-captured baseline-set (cross run)"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(PUBLISH_BASELINE.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    return list(workflow["jobs"][job]["steps"])


def _step(workflow: dict[str, Any], job: str, name: str) -> dict[str, Any]:
    for step in _steps(workflow, job):
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the {job} job")


# ---------------------------------------------------------------------------
# Harness: run a step's real `run:` against a `gh` that answers only the
# endpoints that step is supposed to call.
# ---------------------------------------------------------------------------

_WINDOWS_PYTHON3_SKIP = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the extracted steps invoke `python3` directly -- Git Bash on Windows "
        "typically resolves only `python` (mirrors "
        "test_publish_baseline_upload_step.py's identical convention)."
    ),
)

REPOSITORY = "example/project"
SET_PREFIX = "abicheck-baseline-set-"


@dataclass
class _Context:
    """What one acquisition run left behind, for the next step to read."""

    workspace: Path
    env: dict[str, str]
    tmp: Path


def _baseline_set_zip(tmp: Path) -> Path:
    """A minimal but real baseline-set archive: manifest.json and nothing else."""
    source = tmp / "set"
    source.mkdir(exist_ok=True)
    (source / "manifest.json").write_text(
        json.dumps({"profile": "linux-x86_64", "project_ref": "a" * 40}),
        encoding="utf-8",
    )
    archive = tmp / "set.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.write(source / "manifest.json", "manifest.json")
    return archive


def _gh_stub(tmp: Path, responses: dict[str, Any], archive: Path) -> Path:
    directory = tmp / "bin"
    directory.mkdir(exist_ok=True)
    payload = tmp / "gh-responses.json"
    payload.write_text(json.dumps(responses), encoding="utf-8")
    stub = directory / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "args=()\n"
        'for a in "$@"; do case "$a" in api|--*|.artifacts) ;; *) args+=("$a") ;; esac; done\n'
        'ep="${args[0]}"\n'
        f'if [[ "$ep" == *"/zip" ]]; then cat {archive}; exit 0; fi\n'
        f"{sys.executable} - \"$ep\" <<'PYSTUB'\n"
        "import json, sys\n"
        f"responses = json.load(open({str(payload)!r}))\n"
        "endpoint = sys.argv[1]\n"
        "if endpoint not in responses:\n"
        "    sys.stderr.write('stub gh: unexpected endpoint ' + endpoint + chr(10))\n"
        "    raise SystemExit(1)\n"
        "json.dump(responses[endpoint], sys.stdout)\n"
        "PYSTUB\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return directory


def _run_select(
    workflow: dict[str, Any],
    tmp_path: Path,
    run_document: dict[str, Any],
    artifacts: Any,
    *,
    attempt: str = "1",
    expect_event: str = "push",
    run_id: str = "5001",
    expect_workflow: str = ".github/workflows/release.yml",
    allowed_conclusions: str = "success",
) -> tuple[subprocess.CompletedProcess[str], _Context]:
    tmp = (
        tmp_path
        / f"run-{run_id}-{expect_event}-{attempt}-{len(expect_workflow)}-{len(allowed_conclusions)}"
    )
    tmp.mkdir(parents=True, exist_ok=True)
    archive = _baseline_set_zip(tmp)
    entries = artifacts if isinstance(artifacts, list) else [artifacts]
    stub = _gh_stub(
        tmp,
        {
            f"repos/{REPOSITORY}/actions/runs/5001/attempts/{attempt}": run_document,
            f"repos/{REPOSITORY}/actions/runs/5001": run_document,
            f"repos/{REPOSITORY}/actions/runs/5001/artifacts": entries,
        },
        archive,
    )
    workspace = tmp / "ws"
    workspace.mkdir(exist_ok=True)
    github_output = tmp / "step_output"
    github_output.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "stub-token",
            "SOURCE_REPOSITORY": REPOSITORY,
            "SOURCE_RUN_ID": run_id,
            "SOURCE_RUN_ATTEMPT": attempt,
            "EXPECT_WORKFLOW": expect_workflow,
            "EXPECT_EVENT": expect_event,
            "ALLOWED_CONCLUSIONS": allowed_conclusions,
            "SET_PREFIX": SET_PREFIX,
            "GITHUB_OUTPUT": str(github_output),
            "RUNNER_TEMP": str(tmp),
            "PATH": f"{stub}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    script = tmp / "select.sh"
    script.write_text(_step(workflow, "discover", SELECT_STEP)["run"], encoding="utf-8")
    proc = subprocess.run(
        [bash_executable(), str(script)],
        capture_output=True,
        text=True,
        cwd=workspace,
        env=env,
    )
    return proc, _Context(workspace=workspace, env=env, tmp=tmp)


def _run_discover(
    workflow: dict[str, Any], context: _Context
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    github_output = context.tmp / "discover_output"
    github_output.write_text("", encoding="utf-8")
    env = dict(context.env, GITHUB_OUTPUT=str(github_output))
    script = context.tmp / "discover.sh"
    script.write_text(
        _step(workflow, "discover", "Discover profile ids (pre-captured sets)")["run"],
        encoding="utf-8",
    )
    proc = subprocess.run(
        [bash_executable(), str(script)],
        capture_output=True,
        text=True,
        cwd=context.workspace,
        env=env,
    )
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return proc, outputs


class TestTheAcquisitionAxisIsResolvedOnce:
    """Where the set comes from is orthogonal to whether it was captured.

    Two independent questions -- "capture or publish an existing set?" and
    "from this run or another?" -- resolved in one place, so no later step
    re-decides either from whether some value happens to be empty. That
    re-derivation is how a workflow acquires two answers to one question.
    """

    def test_the_mode_step_publishes_both_axes(self, workflow: dict[str, Any]) -> None:
        outputs = workflow["jobs"]["discover"]["outputs"]
        assert "mode" in outputs
        assert "acquisition" in outputs

    def test_only_the_mode_step_writes_them(self, workflow: dict[str, Any]) -> None:
        writers = [
            step.get("name")
            for step in _steps(workflow, "discover")
            if "acquisition=" in str(step.get("run", ""))
        ]
        assert writers == ["Resolve publication mode"]

    @pytest.mark.parametrize("value", ["same-run", "cross-run", "none"])
    def test_every_acquisition_value_is_produced_somewhere(
        self, workflow: dict[str, Any], value: str
    ) -> None:
        script = _step(workflow, "discover", "Resolve publication mode")["run"]
        assert f"acquisition={value}" in script

    def test_a_source_run_without_a_set_prefix_is_a_usage_error(
        self, workflow: dict[str, Any]
    ) -> None:
        # Otherwise the run CAPTURES and silently ignores the producer it
        # was told to publish from -- publishing real bytes, from the wrong
        # place, with no message.
        script = _step(workflow, "discover", "Resolve publication mode")["run"]
        assert "would CAPTURE instead and silently ignore it" in script


class TestBothAcquisitionsConvergeOnOneValidator:
    """The whole point: a second way in, not a second set of rules."""

    def test_the_validator_is_not_gated_on_the_acquisition(
        self, workflow: dict[str, Any]
    ) -> None:
        condition = str(_step(workflow, "publish", VALIDATE_STEP)["if"])
        assert "existing-set" in condition
        assert "acquisition" not in condition

    def test_nothing_after_the_validator_branches_on_the_acquisition(
        self, workflow: dict[str, Any]
    ) -> None:
        steps = _steps(workflow, "publish")
        names = [s.get("name") for s in steps]
        after = steps[names.index(VALIDATE_STEP) + 1 :]
        offenders = [s.get("name") for s in after if "acquisition" in yaml.safe_dump(s)]
        assert not offenders, offenders

    def test_the_two_downloads_write_the_same_destination(
        self, workflow: dict[str, Any]
    ) -> None:
        same = _step(workflow, "publish", SAME_RUN_DOWNLOAD)
        cross = _step(workflow, "publish", CROSS_RUN_DOWNLOAD)
        assert "baseline-set/" in str(same["with"]["path"])
        assert "baseline-set/" in str(cross["run"])

    def test_the_two_downloads_are_mutually_exclusive(
        self, workflow: dict[str, Any]
    ) -> None:
        same = str(_step(workflow, "publish", SAME_RUN_DOWNLOAD)["if"])
        cross = str(_step(workflow, "publish", CROSS_RUN_DOWNLOAD)["if"])
        assert "'same-run'" in same
        assert "'cross-run'" in cross


class TestTheDownloadIsBoundToAnEstablishedIdentity:
    def test_the_matrix_carries_the_artifact_id(self, workflow: dict[str, Any]) -> None:
        script = _step(
            workflow, "discover", "Discover profile ids (pre-captured sets)"
        )["run"]
        assert "artifact_id" in script
        assert "source-artifacts.json" in script

    def test_a_set_with_no_established_origin_is_refused(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(
            workflow, "discover", "Discover profile ids (pre-captured sets)"
        )["run"]
        assert "was not extracted from any verified source artifact" in script

    def test_the_cross_run_download_uses_the_id_not_the_name(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, "publish", CROSS_RUN_DOWNLOAD)["run"]
        assert "actions/artifacts/$ARTIFACT_ID/zip" in script
        # Specifically: no fallback that would resolve a name instead.
        assert "baseline-set-artifact-prefix" not in script

    def test_a_missing_artifact_id_fails_rather_than_falling_back(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, "publish", CROSS_RUN_DOWNLOAD)["run"]
        assert "refusing to fall back to a name lookup" in script

    def test_the_selection_and_the_download_name_the_same_repository(
        self, workflow: dict[str, Any]
    ) -> None:
        # Resolved once in the mode step and carried, rather than each side
        # re-deriving it from the same expression and one of them drifting.
        cross = _step(workflow, "publish", CROSS_RUN_DOWNLOAD)["env"]
        assert "needs.discover.outputs.source-repository" in str(
            cross["SOURCE_REPOSITORY"]
        )
        select = _step(workflow, "discover", SELECT_STEP)["env"]
        assert "steps.mode.outputs.source-repository" in str(
            select["SOURCE_REPOSITORY"]
        )


class TestTheRequestedAttemptIsBoundOrRefused:
    def test_a_declared_attempt_reads_that_attempts_own_document(
        self, workflow: dict[str, Any]
    ) -> None:
        # Asking for the run and comparing its `run_attempt` answers about
        # whatever attempt is latest when the request lands, so a re-run
        # started after the trigger is either published under the first
        # attempt's decision or reported as a mismatch that is really a race.
        script = _step(workflow, "discover", SELECT_STEP)["run"]
        assert "/attempts/$SOURCE_RUN_ATTEMPT" in script

    def test_an_unreadable_attempt_fails_rather_than_using_another(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, "discover", SELECT_STEP)["run"]
        assert "could not read $endpoint" in script
        assert "exit 1" in script

    def test_the_attempt_is_also_declared_to_the_verifier(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, "discover", SELECT_STEP)["run"]
        assert "--expect-run-attempt" in script

    def test_the_attempt_input_is_shape_checked(self, workflow: dict[str, Any]) -> None:
        script = _step(workflow, "discover", SELECT_STEP)["run"]
        assert "must be a positive integer" in script


class TestTrustSeparation:
    """Read-only establishment and write-capable publication stay apart."""

    def test_the_discovery_job_can_read_actions_but_write_nothing(
        self, workflow: dict[str, Any]
    ) -> None:
        granted = workflow["jobs"]["discover"]["permissions"]
        assert granted.get("actions") == "read"
        assert "write" not in set(granted.values())

    def test_the_producer_query_is_not_in_the_writing_job(
        self, workflow: dict[str, Any]
    ) -> None:
        publish = yaml.safe_dump(workflow["jobs"]["publish"])
        assert "select-precaptured-source" not in publish

    def test_the_publishing_job_only_fetches_an_already_established_id(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, "publish", CROSS_RUN_DOWNLOAD)["run"]
        # It fetches bytes; it does not re-decide eligibility.
        assert "actions/runs/" not in script
        assert "--expect-" not in script

    def test_the_eligibility_rules_are_the_importable_ones(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, "discover", SELECT_STEP)["run"]
        assert "abicheck.frontends.action.cli" in script
        assert "select-precaptured-source" in script


class TestTheCrossingIsAlwaysAnArtifact:
    """A producer-local directory is not a cross-job interface."""

    def test_every_acquisition_reads_an_artifact_not_a_path_input(
        self, workflow: dict[str, Any]
    ) -> None:
        inputs = workflow[True]["workflow_call"]["inputs"]
        for name, spec in inputs.items():
            if "baseline-set" not in name:
                continue
            description = str(spec.get("description", ""))
            assert "directory" not in description.lower() or "artifact" in (
                description.lower()
            ), name

    def test_the_cross_run_bytes_go_through_the_hostile_input_extractor(
        self, workflow: dict[str, Any]
    ) -> None:
        # A set from another run is an input whatever its origin turned out
        # to be, so it is unpacked under the same caps the publication
        # boundary applies -- not with `unzip`.
        for job, step in (
            ("discover", SELECT_STEP),
            ("publish", CROSS_RUN_DOWNLOAD),
        ):
            script = _step(workflow, job, step)["run"]
            assert "cli extract-artifact" in script
            assert not re.search(r"\bunzip\b", script)


class TestCrossRunModeStillNeverBuilds:
    """Acceptance-table row: publication invokes no build, capture or comparison."""

    FORBIDDEN = ("abicheck dump", "abicheck compare", "actions/baseline", "castxml")

    def test_no_cross_run_step_names_a_build_tool(
        self, workflow: dict[str, Any]
    ) -> None:
        for job, step in (
            ("discover", SELECT_STEP),
            ("publish", CROSS_RUN_DOWNLOAD),
        ):
            script = _step(workflow, job, step)["run"]
            for token in self.FORBIDDEN:
                assert token not in script, (job, step, token)

    def test_the_scan_can_actually_fire(self) -> None:
        assert any(token in "abicheck dump --x" for token in self.FORBIDDEN)


# ---------------------------------------------------------------------------
# Executing the acquisition, against a stubbed `gh`
# ---------------------------------------------------------------------------


@_WINDOWS_PYTHON3_SKIP
class TestTheAcquisitionStepActuallyRuns:
    """The half no amount of reading the YAML establishes.

    Every assertion above is about the workflow document; none of them says
    the script runs, that its two API calls are shaped the way `gh` expects,
    or that a refusal reaches the step's exit code instead of being printed
    and stepped over. The stub answers exactly the endpoints this step is
    supposed to call and refuses everything else, so a shortcut through a
    different endpoint fails here rather than passing quietly.
    """

    RUN = {
        "id": 5001,
        "run_attempt": 1,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "path": ".github/workflows/release.yml",
        "name": "Release",
        "repository": {"full_name": "example/project"},
        "head_repository": {"full_name": "example/project"},
    }
    ARTIFACT = {
        "id": 901,
        "name": "abicheck-baseline-set-linux-x86_64",
        "size_in_bytes": 10,
        "expired": False,
        "workflow_run": {"id": 5001},
    }

    def test_a_clean_producer_is_acquired_and_bound_to_its_artifact_id(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        select, context = _run_select(workflow, tmp_path, self.RUN, [self.ARTIFACT])
        assert select.returncode == 0, select.stderr
        discover, outputs = _run_discover(workflow, context)
        assert discover.returncode == 0, discover.stderr
        matrix = json.loads(outputs["matrix"])
        assert matrix == {
            "include": [{"profile_id": "linux-x86_64", "artifact_id": "901"}]
        }
        assert outputs["has-profiles"] == "true"

    @pytest.mark.parametrize(
        ("label", "run_overrides", "artifacts", "expected_code"),
        [
            (
                "pull-request origin",
                {"event": "pull_request"},
                None,
                "producer-event-forbidden",
            ),
            (
                "a different workflow",
                {"path": ".github/workflows/other.yml"},
                None,
                "wrong-workflow",
            ),
            ("a failed run", {"conclusion": "failure"}, None, "wrong-conclusion"),
            ("a different attempt", {"run_attempt": 3}, None, "wrong-attempt"),
            (
                "an artifact of another run",
                {},
                [{**ARTIFACT, "workflow_run": {"id": 7777}}],
                "artifact-not-found",
            ),
            (
                "an expired artifact",
                {},
                [{**ARTIFACT, "expired": True}],
                "artifact-expired",
            ),
            (
                "no matching artifact",
                {},
                [{**ARTIFACT, "name": "build-logs"}],
                "artifact-not-found",
            ),
        ],
    )
    def test_each_refusal_fails_the_step_with_its_own_code(
        self,
        workflow: dict[str, Any],
        tmp_path: Path,
        label: str,
        run_overrides: dict[str, Any],
        artifacts: list[dict[str, Any]] | None,
        expected_code: str,
    ) -> None:
        require_bash()
        select, context = _run_select(
            workflow,
            tmp_path,
            {**self.RUN, **run_overrides},
            self.ARTIFACT if artifacts is None else artifacts,
            # A caller declaring `pull_request` as its expected event is the
            # configurable-rule case: verification passes and the trust rule
            # must still refuse.
            expect_event=str(run_overrides.get("event", "push")),
        )
        assert select.returncode != 0, f"{label} was accepted"
        assert expected_code in select.stderr, select.stderr
        # And nothing was staged: a refusal must not leave bytes behind for
        # a later step to find and publish.
        assert not (context.workspace / "baseline-sets").exists()

    def test_the_stub_refuses_an_endpoint_this_step_should_not_call(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        # Vacuity guard for every case above: the stub must actually be
        # capable of failing, or a refusal test proves nothing.
        require_bash()
        select, _context = _run_select(
            workflow, tmp_path, self.RUN, [self.ARTIFACT], run_id="9999"
        )
        assert select.returncode != 0


@_WINDOWS_PYTHON3_SKIP
class TestEveryOptionalInputMayBeOmitted:
    """The combinations a happy-path test never reaches.

    Each optional input is assembled into an argv array by a conditional in
    the step's shell, and every one of those conditionals is a place a run
    with the input *unset* takes a different path than the one a test that
    always sets it exercised. `set -euo pipefail` makes several of them
    outright fatal when they go wrong -- an unset-variable expansion, an
    empty-array expansion on an older bash, or a bare `[[ ... ]] && ...`
    whose condition is false -- and none of those failures is visible to a
    test that only ever passes every input.

    `allowed-conclusions` gets its own case for a second reason: empty means
    "any conclusion", which is a documented choice, so a `${VAR:-default}`
    substituting `success` over it would silently make that branch
    unreachable. That exact defect already shipped once in
    `actions/verify-source-run/run.sh` and is recorded in its own comment
    there; this is the same shape in a new script.
    """

    RUN = TestTheAcquisitionStepActuallyRuns.RUN
    ARTIFACT = TestTheAcquisitionStepActuallyRuns.ARTIFACT

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            (
                "every optional input empty",
                {"attempt": "", "expect_workflow": "", "expect_event": ""},
            ),
            ("empty allowed-conclusions means any", {"allowed_conclusions": ""}),
            ("only the workflow declared", {"attempt": "", "expect_event": ""}),
            ("only the attempt declared", {"expect_workflow": "", "expect_event": ""}),
            ("only the event declared", {"attempt": "", "expect_workflow": ""}),
        ],
    )
    def test_the_step_still_acquires(
        self,
        workflow: dict[str, Any],
        tmp_path: Path,
        label: str,
        overrides: dict[str, str],
    ) -> None:
        require_bash()
        select, context = _run_select(
            workflow, tmp_path, self.RUN, [self.ARTIFACT], **overrides
        )
        assert select.returncode == 0, f"{label}: {select.stderr}"
        assert (context.workspace / "baseline-sets").is_dir(), label

    def test_an_empty_allowed_conclusions_really_allows_a_failed_run(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        # The half the case above cannot state: that the branch is not only
        # reachable but means what it says. A `${VAR:-default}` would pass
        # the case above (success is still allowed) and fail this one.
        require_bash()
        select, _context = _run_select(
            workflow,
            tmp_path,
            {**self.RUN, "conclusion": "failure"},
            [self.ARTIFACT],
            allowed_conclusions="",
        )
        assert select.returncode == 0, select.stderr

    def test_a_declared_conclusion_still_refuses_the_others(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        # Vacuity guard for the row above.
        require_bash()
        select, _context = _run_select(
            workflow,
            tmp_path,
            {**self.RUN, "conclusion": "failure"},
            [self.ARTIFACT],
            allowed_conclusions="success",
        )
        assert select.returncode != 0
        assert "wrong-conclusion" in select.stderr
