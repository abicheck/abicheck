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

"""``publish-baseline.yml``'s tag-resolution step, executed as a shell script.

**Bug class:** ``workflow.decision_tested_only_as_a_python_function``. The
rules themselves live in ``abicheck.frontends.action.tag_resolution`` and are
tested there against fixtures. What *that* cannot reach is the half that only
exists in YAML: whether the step's ``env:`` block actually carries the values
the script reads, whether the two-request interleave (ref -> peel -> tag
object) is wired in the right order, and whether the resolved value reaches
the validator's ``EXPECTED_PROJECT_REF`` rather than the release tag it used
to be handed.

So these execute the real step's embedded ``run:`` verbatim against a stubbed
``gh``, exactly as ``test_publish_baseline_upload_step.py`` does for the
immutability step, and then assert the *wiring* separately over the parsed
workflow. A test that only imported the Python would pass against a workflow
that never calls it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from _workflow_exec import bash_executable, require_bash

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_BASELINE = REPO_ROOT / ".github" / "workflows" / "publish-baseline.yml"

STEP_NAME = "Resolve the release tag to the revision the capture must record"
VALIDATE_STEP = "Validate pre-captured baseline-set"

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
TAG_OBJECT = "c" * 40

_WINDOWS_PYTHON3_SKIP = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "the extracted step invokes `python3` directly -- Git Bash on Windows "
        "typically resolves only `python` (mirrors "
        "test_publish_baseline_upload_step.py's identical convention)."
    ),
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return yaml.safe_load(PUBLISH_BASELINE.read_text(encoding="utf-8"))


def _step(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    for step in workflow["jobs"]["publish"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in the publish job")


# ---------------------------------------------------------------------------
# Executing the real step
# ---------------------------------------------------------------------------


class _GhStub:
    """A ``gh`` that answers the two endpoints this step calls, and nothing else.

    Refusing every other endpoint is the point, not tidiness: the step must
    not be able to resolve a tag through ``repos/{repo}/commits/{tag}``,
    which would happily answer for a *branch* of the same name. A stub that
    answered anything would let that regression pass.
    """

    def __init__(self, tmp_path: Path, responses: dict[str, object]) -> None:
        self.dir = tmp_path / "stub-bin"
        self.dir.mkdir(exist_ok=True)
        payload = tmp_path / "gh-responses.json"
        payload.write_text(json.dumps(responses), encoding="utf-8")
        self.calls = tmp_path / "gh-calls.txt"
        script = self.dir / "gh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$*" >> {self.calls}\n'
            f"exec {sys.executable} -c '\nimport json, sys\n"
            f'responses = json.load(open("{payload}"))\n'
            # `gh api <endpoint>`: the first argument that is neither the\n
            # subcommand nor a flag is the endpoint.
            'args = [a for a in sys.argv[1:] if a not in ("api", "--") and not a.startswith("-")]\n'
            'endpoint = args[0] if args else ""\n'
            "if endpoint not in responses:\n"
            '    sys.stderr.write(f"stub gh: unexpected endpoint {endpoint}\\n")\n'
            "    raise SystemExit(1)\n"
            "body = responses[endpoint]\n"
            "if body is None:\n"
            '    sys.stderr.write("stub gh: Not Found\\n")\n'
            "    raise SystemExit(1)\n"
            "json.dump(body, sys.stdout)\n"
            '\' -- "$@"\n',
            encoding="utf-8",
        )
        script.chmod(0o755)

    def endpoints(self) -> list[str]:
        if not self.calls.exists():
            return []
        endpoints = []
        for line in self.calls.read_text(encoding="utf-8").splitlines():
            args = [a for a in line.split() if a != "api" and not a.startswith("-")]
            if args:
                endpoints.append(args[0])
        return endpoints


def _run_step(
    workflow: dict[str, Any],
    tmp_path: Path,
    *,
    responses: dict[str, object],
    release_tag: str = "1.5.2",
    declared: str = "",
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], _GhStub]:
    script = _step(workflow, STEP_NAME)["run"]
    stub = _GhStub(tmp_path, responses)
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    script_path = tmp_path / "step.sh"
    script_path.write_text(script, encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "GH_TOKEN": "stub-token",
            "REPO": "example/project",
            "RELEASE_TAG": release_tag,
            "DECLARED": declared,
            "GITHUB_OUTPUT": str(github_output),
            "RUNNER_TEMP": str(tmp_path),
            "PATH": f"{stub.dir}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    proc = subprocess.run(
        [bash_executable(), str(script_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
    )
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return proc, outputs, stub


def _lightweight(tag: str, commit: str = COMMIT) -> dict[str, object]:
    return {"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": commit}}


def _annotated(tag: str) -> dict[str, object]:
    return {"ref": f"refs/tags/{tag}", "object": {"type": "tag", "sha": TAG_OBJECT}}


@_WINDOWS_PYTHON3_SKIP
class TestTheStepResolvesRealTagShapes:
    """The acceptance-table row: "Numeric and annotated tags work."."""

    def test_a_numeric_lightweight_tag(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, outputs, stub = _run_step(
            workflow,
            tmp_path,
            responses={
                "repos/example/project/git/ref/tags/1.5.2": _lightweight("1.5.2")
            },
        )
        assert proc.returncode == 0, proc.stderr
        assert outputs["commit-sha"] == COMMIT
        assert outputs["expected-project-ref"] == COMMIT
        # One request, because a lightweight tag needs no peel.
        assert stub.endpoints() == ["repos/example/project/git/ref/tags/1.5.2"]

    def test_an_annotated_tag_is_peeled_through_a_second_request(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, outputs, stub = _run_step(
            workflow,
            tmp_path,
            responses={
                "repos/example/project/git/ref/tags/1.5.2": _annotated("1.5.2"),
                f"repos/example/project/git/tags/{TAG_OBJECT}": {
                    "sha": TAG_OBJECT,
                    "object": {"type": "commit", "sha": COMMIT},
                },
            },
        )
        assert proc.returncode == 0, proc.stderr
        # The tag object's own SHA is well-formed and is NOT the answer.
        assert outputs["commit-sha"] == COMMIT
        assert outputs["expected-project-ref"] == COMMIT
        assert stub.endpoints() == [
            "repos/example/project/git/ref/tags/1.5.2",
            f"repos/example/project/git/tags/{TAG_OBJECT}",
        ]

    def test_a_tag_resembling_a_semver_prefix_is_not_satisfied_by_a_longer_tag(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, _outputs, _stub = _run_step(
            workflow,
            tmp_path,
            release_tag="1.5",
            responses={
                # What GitHub really answers for a prefix with no exact ref.
                "repos/example/project/git/ref/tags/1.5": [
                    _lightweight("1.5.2", OTHER_COMMIT),
                    _lightweight("1.5.3", OTHER_COMMIT),
                ]
            },
        )
        assert proc.returncode != 0
        assert "tag-not-found" in (proc.stderr + proc.stdout)


@_WINDOWS_PYTHON3_SKIP
class TestTheStepRefusesRatherThanFallingBack:
    def test_a_missing_tag_fails_the_step(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, outputs, _stub = _run_step(
            workflow,
            tmp_path,
            responses={"repos/example/project/git/ref/tags/1.5.2": None},
        )
        assert proc.returncode != 0
        # Specifically: it does not quietly publish against the tag string.
        assert outputs.get("expected-project-ref") is None

    def test_an_annotated_tag_pointing_at_a_tree_is_refused(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, _outputs, _stub = _run_step(
            workflow,
            tmp_path,
            responses={
                "repos/example/project/git/ref/tags/1.5.2": _annotated("1.5.2"),
                f"repos/example/project/git/tags/{TAG_OBJECT}": {
                    "sha": TAG_OBJECT,
                    "object": {"type": "tree", "sha": OTHER_COMMIT},
                },
            },
        )
        assert proc.returncode != 0
        assert "tag-object-not-a-commit" in (proc.stderr + proc.stdout)

    def test_an_unsupported_declared_value_is_a_usage_error(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, _outputs, _stub = _run_step(
            workflow,
            tmp_path,
            declared="HEAD",
            responses={
                "repos/example/project/git/ref/tags/1.5.2": _lightweight("1.5.2")
            },
        )
        assert proc.returncode != 0
        assert "expected-project-ref" in (proc.stderr + proc.stdout)


@_WINDOWS_PYTHON3_SKIP
class TestTheLegacyModesNeedNoTagLookup:
    """A republication must not start failing because the tag was deleted.

    ``tag`` and an explicit SHA are answerable without the API, and the
    assertion is on the *absence* of a request rather than only on the
    value: an implementation that resolved the tag anyway and then ignored
    the answer would produce the right string and still break exactly the
    case this branch exists for.
    """

    def test_tag_mode_answers_the_literal_tag_without_calling_gh(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, outputs, stub = _run_step(
            workflow, tmp_path, declared="tag", responses={}
        )
        assert proc.returncode == 0, proc.stderr
        assert outputs["expected-project-ref"] == "1.5.2"
        assert stub.endpoints() == []

    def test_an_explicit_sha_is_taken_verbatim_without_calling_gh(
        self, workflow: dict[str, Any], tmp_path: Path
    ) -> None:
        require_bash()
        proc, outputs, stub = _run_step(
            workflow, tmp_path, declared=OTHER_COMMIT, responses={}
        )
        assert proc.returncode == 0, proc.stderr
        assert outputs["expected-project-ref"] == OTHER_COMMIT
        assert stub.endpoints() == []


# ---------------------------------------------------------------------------
# The wiring, over the workflow itself
# ---------------------------------------------------------------------------


class TestTheResolvedValueIsWhatTheValidatorReceives:
    """The half that only the YAML can get wrong.

    The original defect was not a bad resolution -- there was none -- but a
    validator handed the release tag. So the assertion is that
    ``EXPECTED_PROJECT_REF`` names this step's output and, specifically,
    that it no longer names ``inputs.release-tag``.
    """

    def test_the_validator_reads_the_resolved_output(
        self, workflow: dict[str, Any]
    ) -> None:
        env = _step(workflow, VALIDATE_STEP)["env"]
        assert "steps.expected-ref.outputs.expected-project-ref" in str(
            env["EXPECTED_PROJECT_REF"]
        )

    def test_the_validator_no_longer_reads_the_release_tag(
        self, workflow: dict[str, Any]
    ) -> None:
        expression = str(_step(workflow, VALIDATE_STEP)["env"]["EXPECTED_PROJECT_REF"])
        assert "release-tag" not in expression
        assert "github.ref_name" not in expression

    def test_resolution_runs_before_validation(self, workflow: dict[str, Any]) -> None:
        names = [s.get("name", "") for s in workflow["jobs"]["publish"]["steps"]]
        assert names.index(STEP_NAME) < names.index(VALIDATE_STEP)

    def test_both_steps_are_gated_to_the_pre_captured_mode(
        self, workflow: dict[str, Any]
    ) -> None:
        # The capture path stamps project-ref itself; resolving a tag for it
        # would spend two API requests on a value nothing reads.
        for name in (STEP_NAME, VALIDATE_STEP):
            assert "existing-set" in str(_step(workflow, name)["if"])

    def test_the_step_never_resolves_a_tag_through_the_commits_endpoint(
        self, workflow: dict[str, Any]
    ) -> None:
        # `repos/{repo}/commits/{ref}` resolves a BRANCH of the same name
        # just as happily, which is the one shortcut that reintroduces the
        # mutable-ref hazard while still looking like it works.
        script = _step(workflow, STEP_NAME)["run"]
        assert "git/ref/tags/" in script
        assert "/commits/" not in script

    def test_the_input_is_declared_with_its_closed_value_set(
        self, workflow: dict[str, Any]
    ) -> None:
        spec = workflow[True]["workflow_call"]["inputs"]["expected-project-ref"]
        assert spec["default"] == ""
        description = spec["description"]
        for value in ("commit", "tag"):
            assert value in description


class TestPublicationStillInvokesNoAnalysis:
    """Acceptance-table row: "Publication invokes no build, capture, or comparison.".

    Asserted over the new step's own script rather than over the job as a
    whole (``test_publish_baseline_existing_set.py`` owns that), because a
    resolution step is exactly the kind of place a convenience ``git
    fetch``/``dump`` creeps into later.
    """

    FORBIDDEN = (
        "abicheck dump",
        "abicheck compare",
        "actions/baseline",
        "castxml",
        "cmake",
        "make ",
    )

    def test_the_resolution_step_runs_nothing_that_analyses(
        self, workflow: dict[str, Any]
    ) -> None:
        script = _step(workflow, STEP_NAME)["run"]
        for token in self.FORBIDDEN:
            assert token not in script

    def test_the_scan_can_actually_fire(self) -> None:
        # Vacuity guard: the assertion above passes trivially against an
        # empty script, so prove the needle set matches something.
        assert any(token in "abicheck dump --foo" for token in self.FORBIDDEN)


def test_jq_is_not_required_by_the_new_step(workflow: dict[str, Any]) -> None:
    """The decision belongs to the importable owner, not to a filter here.

    Comments are stripped before the search: this step's own prose explains
    why it does *not* use one, and a raw substring scan would report the
    very thing it is checking for absent (the same trap
    ``AGENTS.md`` records for the ``performance.yml`` assertion).
    """
    script = _step(workflow, STEP_NAME)["run"]
    code = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )
    assert "jq " not in code
    assert "--jq" not in code
