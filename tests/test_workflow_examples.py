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

"""Every curated workflow example under `examples/workflows/` carries a
valid, non-drifting executable contract.

The fast-lane half of Phase 5's workflow gate: no compiler, no `abicheck`
run -- `validation/scripts/run_workflow_examples.py` does that. What is
checked here is that a workflow directory cannot exist without a manifest,
that the manifest parses against the schema, and above all that every
command it runs is a command the README actually shows.

Bug class: a contract file that restates the artifact instead of exercising
it. Workflow coverage used to be a count of subdirectories, so an empty
directory raised it; a manifest with its own private copy of the commands
would be the same failure one level up -- green while the walkthrough a
reader follows has rotted. The drift check is therefore the load-bearing
test here, and it is asserted for every workflow and adversarially
falsified below, not stated once for the one that exists today.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))

import workflow_examples  # noqa: E402

WORKFLOW_DIRS = workflow_examples.workflow_dirs()
WORKFLOW_IDS = [d.name for d in WORKFLOW_DIRS]


def test_there_is_at_least_one_workflow_example():
    """A zero-workflow tree would make every parametrized test below vacuous."""
    assert WORKFLOW_IDS


@pytest.mark.parametrize("directory", WORKFLOW_DIRS, ids=WORKFLOW_IDS)
def test_every_workflow_directory_has_a_valid_manifest(directory: Path):
    workflow = workflow_examples.load(directory)
    assert workflow.id == directory.name
    assert workflow.task.endswith("?") or workflow.task
    assert workflow.steps
    assert workflow.readme.is_file()


@pytest.mark.parametrize("directory", WORKFLOW_DIRS, ids=WORKFLOW_IDS)
def test_every_manifest_command_is_documented_in_the_readme(directory: Path):
    workflow = workflow_examples.load(directory)
    assert workflow_examples.readme_drift(workflow) == []


@pytest.mark.parametrize("directory", WORKFLOW_DIRS, ids=WORKFLOW_IDS)
def test_a_command_the_readme_does_not_show_is_reported(directory: Path):
    """Adversarial: corrupt each step in turn and confirm the drift check
    names it. Asserting the clean state alone would pass just as happily
    against a check that never looks at the README at all."""
    workflow = workflow_examples.load(directory)
    for index, step in enumerate(workflow.steps):
        mutated_step = replace(step, run=step.run + " --not-in-the-readme")
        mutated_steps = list(workflow.steps)
        mutated_steps[index] = mutated_step
        mutated = replace(workflow, steps=tuple(mutated_steps))
        drift = workflow_examples.readme_drift(mutated)
        assert any(step.name in message for message in drift), (
            f"corrupting step {step.name!r} produced no drift report"
        )


@pytest.mark.parametrize("directory", WORKFLOW_DIRS, ids=WORKFLOW_IDS)
def test_every_workflow_asserts_something_about_its_output(directory: Path):
    """A workflow whose steps check nothing would run green forever while
    reporting the wrong verdict -- the same 'coverage without verification'
    shape `test-assertion-density` guards for tests."""
    workflow = workflow_examples.load(directory)
    checks = sum(
        1
        for step in workflow.steps
        if step.exit_code is not None
        or step.stdout_contains
        or step.stdout_excludes
        or step.expect_json
    )
    assert checks, f"{workflow.id}: no step asserts anything about its output"


def test_a_directory_without_a_manifest_is_an_error(tmp_path: Path):
    """The whole reason manifests exist: an empty directory must not be able
    to raise the workflow-coverage count."""
    (tmp_path / "half-finished").mkdir()
    with pytest.raises(workflow_examples.ManifestError, match="no workflow.yaml"):
        workflow_examples.load_all(tmp_path)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        pytest.param("id: wrong-id", "must match the directory name", id="bad-id"),
        pytest.param("task: ''", "must state the user", id="empty-task"),
        pytest.param("platforms: []", "at least one platform", id="no-platforms"),
        pytest.param("platforms: [solaris]", "unknown platform", id="bad-platform"),
        pytest.param("steps: []", "at least one command", id="no-steps"),
        pytest.param("steps: [{name: a}]", "needs a `run`", id="no-command"),
        pytest.param("steps: [{run: 'x'}]", "needs a `name`", id="no-name"),
        pytest.param(
            "steps: [{name: a, run: x, expect: {code: 1}}]",
            "unknown expect key",
            id="bad-expect-key",
        ),
        pytest.param(
            "steps: [{name: a, run: x, expect_json: {verdict: OK}}]",
            "no `json_variant`",
            id="unreachable-expect-json",
        ),
        pytest.param("nonsense: 1", "unknown key", id="unknown-key"),
    ],
)
def test_the_schema_rejects_a_malformed_manifest(
    tmp_path: Path, mutation: str, match: str
):
    """Each rejection is a way a manifest could otherwise claim coverage it
    does not deliver -- an unreachable `expect_json`, a step with no command,
    a typo'd key silently ignored."""
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "README.md").write_text("# demo\n", encoding="utf-8")
    base = {
        "id": "demo",
        "task": "Does it work?",
        "platforms": "[linux]",
        "steps": "[{name: a, run: 'true'}]",
    }
    key, _, value = mutation.partition(":")
    merged = {**base, key.strip(): value.strip()}
    text = "\n".join(f"{k}: {v}" for k, v in merged.items()) + "\n"
    (directory / "workflow.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(workflow_examples.ManifestError, match=match):
        workflow_examples.load(directory)


@pytest.mark.parametrize(
    ("documented", "declared", "expected"),
    [
        ("a  b", "a b", True),
        ("a \\\n    b", "a b", True),
        ("a\tb", "a b", True),
        ("ab", "a b", False),
    ],
)
def test_command_normalization_survives_readme_formatting(
    documented: str, declared: str, expected: bool
):
    """A README wraps a long invocation over several lines; the manifest
    states it as one string. Formatting is not the drift anyone cares
    about -- but a genuinely different command still must not match."""
    normalized = workflow_examples.normalize_command(documented)
    assert (workflow_examples.normalize_command(declared) in normalized) is expected


@pytest.mark.parametrize(
    "command",
    [
        "abicheck compare a.so b.so | tee out.txt",
        "abicheck compare a.so b.so > report.json",
        "abicheck compare a.so b.so && echo done",
        "abicheck compare $LIB_OLD $LIB_NEW",
        "abicheck compare a.so b.so; rm -rf /",
        "abicheck compare *.so",
        "abicheck compare `cat old` b.so",
    ],
)
def test_a_command_needing_a_shell_is_rejected(tmp_path: Path, command: str):
    """Commands run with `shell=False`, so a manifest line needing a shell
    must fail loudly at load rather than being handed to the program as a
    literal argument -- and the runner never gets `shell=True`'s injection
    surface in the first place."""
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "README.md").write_text("# demo\n", encoding="utf-8")
    (directory / "workflow.yaml").write_text(
        "id: demo\n"
        "task: Does it work?\n"
        "platforms: [linux]\n"
        f"steps: [{{name: a, run: '{command}'}}]\n",
        encoding="utf-8",
    )
    with pytest.raises(workflow_examples.ManifestError, match="metacharacter"):
        workflow_examples.load(directory)


@pytest.mark.parametrize("directory", WORKFLOW_DIRS, ids=WORKFLOW_IDS)
def test_every_step_parses_to_a_real_argv(directory: Path):
    workflow = workflow_examples.load(directory)
    for step in workflow.steps:
        assert step.argv
        assert " ".join(step.argv) == workflow_examples.normalize_command(step.run)
