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

"""The integration commands themselves, in process.

The decision layers under these commands are covered directly elsewhere, and
the Actions' shells are covered by driving ``run.sh`` as a real subprocess.
Neither reaches the *command* bodies: the argument parsing, the refusal exit
code, what is written to ``GITHUB_OUTPUT`` and in what form. A subprocess
cannot report coverage back, so those bodies read as entirely unexercised --
and a command that, say, wrote an output key under the wrong name would pass
every test in this suite.

So these run through Click's ``CliRunner``, in process. What they assert is
the boundary contract each command owes its caller:

* a refusal exits :data:`EXIT_REFUSED` (3), never Click's usage exit (2) and
  never a compatibility exit -- a CI step must be able to tell "this
  declaration is wrong" from "I invoked the tool wrong";
* every ``GITHUB_OUTPUT`` line is a single ``key=value`` with no embedded
  newline, because the Action's shell appends them verbatim and an embedded
  newline would inject or overwrite an unrelated key a later step reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

# Imported for its registration side effect: `cli_base` owns the bare group,
# and the commands are decorated onto it by `cli_integration`, which only
# `cli` imports. Importing `cli_base` alone yields a group with no commands at
# all -- every invocation below would exit 2 ("No such command") and the
# refusal-exit assertions would be testing Click's usage path, not ours.
from abicheck.frontends.action import cli as _cli  # noqa: F401
from abicheck.frontends.action.cli_base import EXIT_REFUSED, action_cli

_PROFILE = "linux-x86_64-gcc"


def _outputs(path: Path) -> dict[str, str]:
    """Parse a ``GITHUB_OUTPUT`` file, insisting on its one-line-per-key shape."""
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        assert "=" in line, f"not a key=value record: {line!r}"
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


def _elf(path: Path, *, e_type: int = 3, machine: int = 62) -> Path:
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4], header[5], header[6] = 2, 1, 1
    header[16:18] = e_type.to_bytes(2, "little")
    header[18:20] = machine.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header))
    return path


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestResolveLibraries:
    @staticmethod
    def _tree(root: Path) -> Path:
        (root / "include" / "demo").mkdir(parents=True)
        for name in ("core.h", "hooks.h"):
            (root / "include" / "demo" / name).write_text("", encoding="utf-8")
        _elf(root / "lib" / "libdemo.so.1.2.3")
        (root / "lib" / "libdemo.so").symlink_to("libdemo.so.1.2.3")
        return root

    def test_it_emits_the_libraries_array_and_outputs(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        root = self._tree(tmp_path / "inst")
        spec = _write(
            tmp_path / "spec.json",
            [
                {
                    "name": "libdemo",
                    "artifact": "lib/libdemo.so*",
                    "header": ["include/demo/*.h"],
                    "header_exclude": ["include/demo/hooks.h"],
                    "include": ["include"],
                }
            ],
        )
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "resolve-libraries",
                str(spec),
                "--root",
                str(root),
                "--out",
                str(tmp_path / "libraries.json"),
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == 0, result.output
        outputs = _outputs(github_output)
        assert outputs["machine"] == "EM_X86_64"
        assert outputs["library-count"] == "1"
        libraries = json.loads(outputs["libraries"])
        assert [entry["name"] for entry in libraries] == ["libdemo"]
        assert libraries == json.loads(
            (tmp_path / "libraries.json").read_text(encoding="utf-8")
        )

    def test_a_refusal_exits_three(self, runner: CliRunner, tmp_path: Path) -> None:
        root = self._tree(tmp_path / "inst")
        spec = _write(
            tmp_path / "spec.json",
            [{"name": "libghost", "artifact": "lib/libghost.so*"}],
        )
        result = runner.invoke(
            action_cli, ["resolve-libraries", str(spec), "--root", str(root)]
        )
        assert result.exit_code == EXIT_REFUSED
        assert "library selection refused" in result.output

    def test_the_machine_output_is_empty_when_agreement_was_not_checked(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """A blank value still has to be a well-formed record, not a dropped key."""
        root = self._tree(tmp_path / "inst")
        (root / "lib" / "libtext.so.1").write_text("not elf", encoding="utf-8")
        spec = _write(
            tmp_path / "spec.json",
            [{"name": "libtext", "artifact": "lib/libtext.so.1"}],
        )
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "resolve-libraries",
                str(spec),
                "--root",
                str(root),
                "--no-require-elf",
                "--no-require-same-machine",
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == 0, result.output
        assert _outputs(github_output)["machine"] == ""

    def test_an_unreadable_spec_is_a_click_exception_not_a_refusal(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Malformed *input to the tool* is the caller's own bug, not a refusal."""
        root = self._tree(tmp_path / "inst")
        spec = tmp_path / "spec.json"
        spec.write_text("{not json", encoding="utf-8")
        result = runner.invoke(
            action_cli, ["resolve-libraries", str(spec), "--root", str(root)]
        )
        assert result.exit_code != 0
        assert result.exit_code != EXIT_REFUSED


class TestCollectChecks:
    def test_it_collects_and_reports_counts(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        report = _write(tmp_path / "in.json", {"verdict": "COMPATIBLE", "changes": []})
        declaration = _write(
            tmp_path / "checks.json",
            [
                {
                    "id": f"libfoo@{_PROFILE}#accepted-main@headers",
                    "report": str(report),
                },
                {"id": f"libbar@{_PROFILE}#accepted-main@headers", "report": ""},
            ],
        )
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "collect-checks",
                str(declaration),
                "--reports-dir",
                str(tmp_path / "reports"),
                "--manifest",
                str(tmp_path / "expected.json"),
                "--gate",
                '{"missing_required": "warn"}',
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == 0, result.output
        outputs = _outputs(github_output)
        assert (outputs["expected"], outputs["present"], outputs["missing"]) == (
            "2",
            "1",
            "1",
        )
        manifest = json.loads((tmp_path / "expected.json").read_text(encoding="utf-8"))
        assert manifest["gate"] == {"missing_required": "warn"}

    def test_a_refused_declaration_exits_three(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        declaration = _write(tmp_path / "checks.json", [])
        result = runner.invoke(
            action_cli,
            [
                "collect-checks",
                str(declaration),
                "--reports-dir",
                str(tmp_path / "reports"),
                "--manifest",
                str(tmp_path / "expected.json"),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert "check collection refused" in result.output

    def test_a_non_object_gate_is_rejected(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        declaration = _write(
            tmp_path / "checks.json", [{"id": "a@p#c@headers", "report": ""}]
        )
        result = runner.invoke(
            action_cli,
            [
                "collect-checks",
                str(declaration),
                "--reports-dir",
                str(tmp_path / "reports"),
                "--manifest",
                str(tmp_path / "expected.json"),
                "--gate",
                '["not", "an", "object"]',
            ],
        )
        assert result.exit_code != 0
        assert "JSON object" in result.output


class TestValidateAggregate:
    @staticmethod
    def _document(**overrides: object) -> dict[str, object]:
        document: dict[str, object] = {
            "aggregate_schema_version": "1.0",
            "status": "pass",
            "compatibility": {},
            "coverage": {"status": "complete"},
            "gate": {},
            "targets": [
                {
                    "target_id": f"libfoo@{_PROFILE}#accepted-main@headers",
                    "state": "analyzed",
                }
            ],
        }
        document.update(overrides)
        return document

    def test_it_emits_the_summary_outputs(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        document = _write(tmp_path / "aggregate.json", self._document())
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "validate-aggregate",
                str(document),
                "--expected",
                "1",
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == 0, result.output
        outputs = _outputs(github_output)
        assert outputs["status"] == "pass"
        assert outputs["coverage"] == "complete"
        assert outputs["analyzed"] == "1"
        # JSON on one line: the shell appends these verbatim.
        assert json.loads(outputs["channels"])["accepted-main"] == {
            "analyzed": 1,
            "unavailable": 0,
        }

    def test_a_document_describing_nothing_exits_three(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        document = _write(
            tmp_path / "aggregate.json", {"aggregate_schema_version": "1.0"}
        )
        result = runner.invoke(action_cli, ["validate-aggregate", str(document)])
        assert result.exit_code == EXIT_REFUSED
        assert "does not describe" in result.output or "status" in result.output


class TestVerifyTag:
    def test_an_annotated_tag_is_peeled_and_reported(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(
            tmp_path / "ref.json", {"object": {"sha": "TAGOBJ", "type": "tag"}}
        )
        tag_object = _write(
            tmp_path / "tag.json", {"object": {"sha": "COMMITX", "type": "commit"}}
        )
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "verify-tag",
                "1.5.2",
                "--ref-json",
                str(ref),
                "--tag-object-json",
                str(tag_object),
                "--built-sha",
                "COMMITX",
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == 0, result.output
        outputs = _outputs(github_output)
        assert outputs["outcome"] == "tag"
        assert outputs["is-tag"] == "true"
        assert outputs["commit-sha"] == "COMMITX"
        assert outputs["annotated"] == "true"

    def test_an_absent_ref_reads_as_not_a_tag(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """No ``--ref-json`` at all is the 404 the shell passes through."""
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            ["verify-tag", "main", "--github-output", str(github_output)],
        )
        assert result.exit_code == EXIT_REFUSED
        assert _outputs(github_output)["outcome"] == "not_a_tag"

    def test_an_empty_ref_file_is_treated_as_absent(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """The shell writes an empty file on a 404 rather than deleting it."""
        empty = tmp_path / "ref.json"
        empty.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli, ["verify-tag", "main", "--ref-json", str(empty)]
        )
        assert result.exit_code == EXIT_REFUSED
        assert "not a tag" in result.output

    def test_a_commit_mismatch_reports_that_outcome(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        ref = _write(
            tmp_path / "ref.json", {"object": {"sha": "COMMITX", "type": "commit"}}
        )
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "verify-tag",
                "1.5.2",
                "--ref-json",
                str(ref),
                "--built-sha",
                "SOMETHINGELSE",
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert _outputs(github_output)["outcome"] == "tag-commit-mismatch"

    def test_lookup_failed_is_its_own_outcome(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "verify-tag",
                "1.5.2",
                "--lookup-failed",
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert _outputs(github_output)["outcome"] == "lookup_failed"


class TestSelectProducerRun:
    @staticmethod
    def _run(run_id: int, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "id": run_id,
            "run_number": run_id,
            "run_attempt": 1,
            "repository": {"full_name": "o/r"},
            "path": ".github/workflows/ci.yml",
            "name": "CI",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_sha": "BASE",
            "head_branch": "main",
        }
        row.update(overrides)
        return row

    _EXPECT = [
        "--expect-repository",
        "o/r",
        "--expect-workflow",
        ".github/workflows/ci.yml",
        "--expect-event",
        "push",
        "--expect-head-sha",
        "BASE",
        "--expect-head-branch",
        "main",
    ]

    def test_it_selects_and_reports_the_run(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runs = _write(
            tmp_path / "runs.json",
            {"workflow_runs": [self._run(1), self._run(7)]},
        )
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "select-producer-run",
                str(runs),
                *self._EXPECT,
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == 0, result.output
        outputs = _outputs(github_output)
        assert outputs["outcome"] == "resolved"
        assert outputs["run-id"] == "7"
        assert outputs["considered"] == "2"

    def test_a_bare_array_is_accepted(self, runner: CliRunner, tmp_path: Path) -> None:
        """``--jq '.workflow_runs'`` hands the array, not the envelope."""
        runs = _write(tmp_path / "runs.json", [self._run(1)])
        result = runner.invoke(
            action_cli, ["select-producer-run", str(runs), *self._EXPECT]
        )
        assert result.exit_code == 0, result.output

    def test_no_eligible_run_exits_three_with_not_found(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runs = _write(tmp_path / "runs.json", {"workflow_runs": []})
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "select-producer-run",
                str(runs),
                *self._EXPECT,
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        outputs = _outputs(github_output)
        assert outputs["outcome"] == "not_found"
        assert outputs["run-id"] == ""

    def test_lookup_failed_is_distinguished(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runs = _write(tmp_path / "runs.json", {"workflow_runs": [self._run(1)]})
        github_output = tmp_path / "gh"
        github_output.write_text("", encoding="utf-8")
        result = runner.invoke(
            action_cli,
            [
                "select-producer-run",
                str(runs),
                *self._EXPECT,
                "--lookup-failed",
                "--github-output",
                str(github_output),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert _outputs(github_output)["outcome"] == "lookup_failed"

    def test_required_jobs_are_read_from_the_jobs_map(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runs = _write(tmp_path / "runs.json", {"workflow_runs": [self._run(1)]})
        jobs = _write(
            tmp_path / "jobs.json",
            {"1": {"jobs": [{"name": "capture", "conclusion": "failure"}]}},
        )
        result = runner.invoke(
            action_cli,
            [
                "select-producer-run",
                str(runs),
                *self._EXPECT,
                "--required-jobs",
                "capture",
                "--jobs-json",
                str(jobs),
            ],
        )
        assert result.exit_code == EXIT_REFUSED
        assert "failed: capture" in result.output

    def test_a_document_that_is_not_a_runs_array_is_a_usage_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runs = _write(tmp_path / "runs.json", {"workflow_runs": "not a list"})
        result = runner.invoke(
            action_cli, ["select-producer-run", str(runs), *self._EXPECT]
        )
        assert result.exit_code != 0
        assert result.exit_code != EXIT_REFUSED


class TestRegistration:
    def test_importing_cli_registers_every_integration_command(self) -> None:
        """The group is only populated through ``cli``'s side-effect import.

        Stated here because the rest of this module depends on it silently: if
        registration moved, every test above would fail with Click's usage exit
        rather than a meaningful message.
        """
        registered = set(action_cli.commands)
        assert {
            "resolve-libraries",
            "collect-checks",
            "validate-aggregate",
            "verify-tag",
            "select-producer-run",
        } <= registered

    def test_the_refusal_code_is_distinct_from_clicks_usage_exit(self) -> None:
        """A CI step must tell "your declaration is wrong" from "you invoked
        me wrong", and from every compatibility exit abicheck uses."""
        assert EXIT_REFUSED == 3
        assert EXIT_REFUSED not in (0, 1, 2, 4, 64)
