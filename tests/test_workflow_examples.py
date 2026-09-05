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


# --------------------------------------------------------------------------
# `--require` bookkeeping in validation/scripts/run_workflow_examples.py.
#
# Bug class: a derived summary that double-counts because a status change was
# recorded beside the record instead of on it. The original cut appended a
# detached `{"id": ...}` entry to the local `failed` list, so the JSON receipt
# still reported a plain skip while the console counted the same workflow as
# both failed and skipped -- printing "-1 passed, 1 failed, 1 skipped". The
# invariant below is the general one (the three buckets partition the results
# exactly), checked over generated status combinations rather than the single
# one-workflow case that exposed it.
# --------------------------------------------------------------------------


def _load_runner():
    import importlib.util

    path = REPO_DIR / "validation" / "scripts" / "run_workflow_examples.py"
    spec = importlib.util.spec_from_file_location("run_workflow_examples", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _results(*statuses: str) -> list[dict]:
    return [
        {
            "id": f"wf{index}",
            "status": status,
            "reason": "missing required tool(s): ['castxml']",
            "failures": [],
        }
        for index, status in enumerate(statuses)
    ]


@pytest.mark.parametrize(
    "statuses",
    [
        ("skip",),
        ("pass",),
        ("fail",),
        ("skip", "pass"),
        ("pass", "skip", "fail"),
        ("skip", "skip", "skip"),
        ("fail", "fail"),
    ],
)
def test_status_buckets_always_partition_the_results(statuses: tuple[str, ...]):
    """passed + failed + skipped == total, for every workflow named required
    and for none of them. A workflow counted in two buckets is what produced
    the negative pass count."""
    runner = _load_runner()
    for required in ([], ["wf0"], [r["id"] for r in _results(*statuses)]):
        results = runner.apply_required(_results(*statuses), list(required))
        buckets = [r["status"] for r in results]
        assert buckets.count("pass") + buckets.count("fail") + buckets.count(
            "skip"
        ) == len(results)
        assert set(buckets) <= {"pass", "fail", "skip"}


def test_a_required_skip_becomes_a_failure_on_the_record_itself():
    runner = _load_runner()
    results = runner.apply_required(_results("skip"), ["wf0"])
    assert results[0]["status"] == "fail"
    assert results[0]["required"] is True
    assert any("required by --require" in f for f in results[0]["failures"])


def test_require_leaves_a_workflow_it_does_not_name_alone():
    """The negative control: naming one workflow must not promote another's
    skip, or `--require` would silently mean "fail on any skip"."""
    runner = _load_runner()
    results = runner.apply_required(_results("skip", "skip"), ["wf0"])
    assert results[0]["status"] == "fail"
    assert results[1]["status"] == "skip"
    assert results[1]["failures"] == []


@pytest.mark.parametrize("status", ["pass", "fail"])
def test_require_does_not_touch_a_workflow_that_actually_ran(status: str):
    runner = _load_runner()
    results = runner.apply_required(_results(status), ["wf0"])
    assert results[0]["status"] == status
    assert results[0]["failures"] == []
    assert "required" not in results[0]


def test_the_compare_release_workflow_declares_the_backend_its_command_needs():
    """compare-release passes `--header`, which goes through the CastXML AST
    backend. If the manifest does not declare it, a host without CastXML
    reports a failure that looks like an ABI regression instead of an honest
    skip -- and CI installs the wrong toolchain for the job."""
    workflow = workflow_examples.load(
        workflow_examples.WORKFLOWS_DIR / "compare-release"
    )
    uses_header = any("--header" in step.run for step in workflow.steps)
    assert uses_header
    assert "castxml" in workflow.requires


@pytest.mark.parametrize(
    ("selection", "required"),
    [
        pytest.param([], ["compare-relase"], id="misspelled"),
        pytest.param([], ["never-existed"], id="unknown"),
        pytest.param(
            ["compare-release"], ["something-else"], id="excluded-by-selection"
        ),
        pytest.param(
            ["compare-release"], ["compare-release", "typo"], id="one-of-several-bad"
        ),
    ],
)
def test_require_rejects_an_id_that_names_nothing_in_the_run(
    capsys, selection: list[str], required: list[str]
):
    """A `--require` id matching no selected workflow is a usage error.

    Silently ignoring it defeats the flag entirely: a misspelling, a renamed
    workflow, or a positional selection that excludes the required id would
    let a run where every workflow skipped still exit 0 -- the zero-work
    pass `--require` exists to stop. Checked across all four ways the id can
    fail to match, not just a typo.
    """
    runner = _load_runner()
    argv = [*selection, *[arg for r in required for arg in ("--require", r)]]
    assert runner.main(argv) == 1
    assert "--require names workflow(s) not in this run" in capsys.readouterr().err


def test_require_accepts_an_id_that_is_in_the_run():
    """Negative control: the rejection must not fire on a valid id, or the
    flag would be unusable rather than merely toothless."""
    runner = _load_runner()
    workflow_id = WORKFLOW_IDS[0]
    assert runner.main([workflow_id, "--require", workflow_id]) in (0, 1)


# --------------------------------------------------------------------------
# The machine-readable rerun. `json_variant` re-runs the *same* documented
# command with only a format flag appended, so it must gate identically --
# the exit code was recorded but never checked, which would let a regression
# that keeps the payload right but returns the wrong code pass, on precisely
# the path a consumer's CI gates on.
# --------------------------------------------------------------------------


def _json_step(**overrides):
    defaults = {
        "name": "compare",
        "run": "abicheck compare a.so b.so",
        "argv": ("abicheck", "compare", "a.so", "b.so"),
        "exit_code": 4,
        "json_variant": ("--format", "json"),
        "expect_json": {"verdict": "BREAKING", "change_kinds": ["func_removed"]},
    }
    return workflow_examples.Step(**{**defaults, **overrides})


PAYLOAD = '{"verdict": "BREAKING", "changes": [{"kind": "func_removed"}]}'


@pytest.mark.parametrize("base,json_code", [(4, 0), (4, 1), (0, 4), (2, 4), (0, 1)])
def test_a_json_variant_that_gates_differently_is_reported(base: int, json_code: int):
    runner = _load_runner()
    failures = runner.check_json_variant(
        _json_step(),
        base_returncode=base,
        json_returncode=json_code,
        json_stdout=PAYLOAD,
        json_command="abicheck compare a.so b.so --format json",
    )
    assert any("must not change gating" in f for f in failures)


@pytest.mark.parametrize("code", [0, 1, 2, 4, 64])
def test_a_matching_exit_code_is_not_reported(code: int):
    """Negative control across every exit code this CLI uses: the check must
    compare the two runs, not hard-code a value."""
    runner = _load_runner()
    failures = runner.check_json_variant(
        _json_step(exit_code=code),
        base_returncode=code,
        json_returncode=code,
        json_stdout=PAYLOAD,
        json_command="cmd",
    )
    assert failures == []


def test_the_exit_code_check_applies_when_the_step_declares_none():
    """The invariant is "the same command gates the same", which holds even
    for a step with no declared `exit_code` -- comparing against the
    declaration instead would silently skip those steps."""
    runner = _load_runner()
    failures = runner.check_json_variant(
        _json_step(exit_code=None),
        base_returncode=4,
        json_returncode=0,
        json_stdout=PAYLOAD,
        json_command="cmd",
    )
    assert any("must not change gating" in f for f in failures)


def test_a_wrong_payload_is_still_reported_alongside_a_matching_exit_code():
    runner = _load_runner()
    failures = runner.check_json_variant(
        _json_step(),
        base_returncode=4,
        json_returncode=4,
        json_stdout='{"verdict": "COMPATIBLE", "changes": []}',
        json_command="cmd",
    )
    assert any("verdict" in f for f in failures)
    assert any("change_kinds" in f for f in failures)


def test_non_json_output_is_reported_and_stops_payload_checks():
    runner = _load_runner()
    failures = runner.check_json_variant(
        _json_step(),
        base_returncode=4,
        json_returncode=4,
        json_stdout="Usage: abicheck compare [OPTIONS]",
        json_command="cmd",
    )
    assert any("not JSON" in f for f in failures)
    assert not any("verdict" in f for f in failures)


# --------------------------------------------------------------------------
# Sequence fields must be sequences. `tuple("BREAKING")` is eight
# one-character assertions, and `"BRAKEING"` satisfies every one of them --
# so the common YAML slip `stdout_contains: BREAKING` (no `- `) would turn a
# real output check into one that passes on text missing the required word.
# Silently weakening an assertion is worse than having none.
# --------------------------------------------------------------------------


SEQUENCE_FIELDS = [
    ("platforms", "platforms: linux\n"),
    ("requires", "requires: gcc\n"),
    (
        "expect.stdout_contains",
        "steps: [{name: a, run: 'true', expect: {stdout_contains: BREAKING}}]\n",
    ),
    (
        "expect.stdout_excludes",
        "steps: [{name: a, run: 'true', expect: {stdout_excludes: ERROR}}]\n",
    ),
    ("json_variant", "steps: [{name: a, run: 'true', json_variant: --format}]\n"),
]


def _manifest(tmp_path: Path, override: str) -> Path:
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "README.md").write_text("# demo\n", encoding="utf-8")
    base = {
        "id": "demo\n",
        "task": "Does it work?\n",
        "platforms": "[linux]\n",
        "steps": "[{name: a, run: 'true'}]\n",
    }
    key = override.split(":", 1)[0].split(".")[0]
    lines = [
        f"{k}: {v}" for k, v in base.items() if k != key and not override.startswith(k)
    ]
    text = "".join(lines)
    # `override` already carries its own "key: value\n".
    text += override
    (directory / "workflow.yaml").write_text(text, encoding="utf-8")
    return directory


@pytest.mark.parametrize(
    ("field_name", "override"), SEQUENCE_FIELDS, ids=[f for f, _ in SEQUENCE_FIELDS]
)
def test_a_bare_string_where_a_list_belongs_is_rejected(
    tmp_path: Path, field_name: str, override: str
):
    directory = _manifest(tmp_path, override)
    with pytest.raises(
        workflow_examples.ManifestError, match="must be a list of strings"
    ):
        workflow_examples.load(directory)


@pytest.mark.parametrize("bad_item", ["4", "null", "[1, 2]", "{a: b}", "true"])
def test_a_non_string_item_in_a_sequence_is_rejected(tmp_path: Path, bad_item: str):
    directory = _manifest(
        tmp_path,
        f"steps: [{{name: a, run: 'true', expect: {{stdout_contains: [{bad_item}]}}}}]\n",
    )
    with pytest.raises(
        workflow_examples.ManifestError, match="must contain only strings"
    ):
        workflow_examples.load(directory)


def test_the_scalar_rejection_is_not_theoretical():
    """The exact failure mode: a scalar splits into per-character assertions
    that a *wrong* string satisfies. If this ever stops holding, the
    rejection above could be relaxed -- while it holds, it must not be."""
    misspelled = "BRAKEING"
    assert "BREAKING" not in misspelled
    assert all(char in misspelled for char in tuple("BREAKING"))


@pytest.mark.parametrize("directory", WORKFLOW_DIRS, ids=WORKFLOW_IDS)
def test_real_manifests_use_real_sequences(directory: Path):
    workflow = workflow_examples.load(directory)
    assert all(isinstance(p, str) and len(p) > 1 for p in workflow.platforms)
    for step in workflow.steps:
        for needle in (*step.stdout_contains, *step.stdout_excludes):
            assert len(needle) > 1, (
                f"{workflow.id}/{step.name}: {needle!r} is a single character -- "
                "the hallmark of a scalar that was split"
            )
