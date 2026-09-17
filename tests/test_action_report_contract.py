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

"""``actions/report`` and ``actions/verify-source-run`` publish; they never analyse.

That is the whole reason either may run in a *trusted* ``workflow_run`` job
holding a token that can write to a pull request (ADR-073). If one of them
ever installed project dependencies, invoked a compiler, ran a build query,
or performed a comparison, it would be executing contributor-controlled
build logic in a privileged context -- the precise thing the two-workflow
split exists to prevent.

The property is checked two ways, because either alone is weak:

* **Statically**, over the executable surface of each Action -- its ``run:``
  scripts and its ``uses:`` steps, with comments stripped first. Comment
  text is not a program, and a rule that matched it would be tripped by the
  very comments explaining the rule (AGENTS.md's own caution after the
  ``performance.yml`` assertion that failed on a documented counter-example).
* **Behaviourally**, by running the publisher end to end against a ``PATH``
  whose compilers, build tools and ``gh`` all record their own invocation
  and fail. Asserting a script's text proves nothing about what it does;
  #705 -> #758 is this repository's own record of that lesson.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests._workflow_exec import bash_executable, require_bash

REPO_DIR = Path(__file__).resolve().parent.parent
REPORT_ACTION = REPO_DIR / "actions" / "report"
VERIFY_ACTION = REPO_DIR / "actions" / "verify-source-run"

#: Tokens that would mean the Action is analysing something rather than
#: reporting on an analysis someone else already did. Each is a *command or
#: file* an analysing step would name, not a word that happens to appear in
#: prose -- `comparison` and `compiler` are fine to write about.
FORBIDDEN_EXECUTABLE_TOKENS = (
    "abicheck compare",
    "abicheck dump",
    "abicheck deps",
    "abicheck aggregate",
    "abicheck project",
    "castxml",
    "compile_commands.json",
    "--compile-db",
    "compile-db",
    "install-deps",
    "install-castxml",
    "abicheck-cc",
    "cmake",
    "meson",
    "ninja",
    "gcc",
    "g++",
    "clang++",
    "objdump",
    "readelf",
    "checker.compare",
    "dumper.dump",
    "service.run_compare",
    "service.run_dump",
)

#: `bear` and `make` are matched as whole words: "bear" appears inside
#: nothing here, but a substring rule for a three-letter tool is exactly how
#: a text check acquires a false positive it then gets relaxed away.
FORBIDDEN_WORD_TOKENS = ("bear", "make", "cc", "ld")


def _strip_shell_comments(text: str) -> str:
    """Drop whole-line and trailing ``#`` comments.

    Deliberately conservative: a ``#`` inside a quoted string would be
    stripped too, which can only make this check *stricter* (it removes text
    from the haystack), never laxer.
    """
    out = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip():
            out.append(stripped)
    return "\n".join(out)


def _executable_surface(action_dir: Path) -> str:
    """Every line of either Action that a runner actually executes.

    The composite Action's ``run:`` bodies and ``uses:`` values, plus the
    shell scripts they invoke. Input/output *descriptions* are excluded on
    purpose: they are documentation, and this repository has already been
    bitten once by a text assertion that matched a comment describing the
    thing it was checking for the absence of.
    """
    action = yaml.safe_load((action_dir / "action.yml").read_text(encoding="utf-8"))
    parts: list[str] = []
    for step in action["runs"]["steps"]:
        if "uses" in step:
            parts.append(str(step["uses"]))
        if "run" in step:
            parts.append(_strip_shell_comments(str(step["run"])))
    for script in sorted(action_dir.glob("*.sh")):
        parts.append(_strip_shell_comments(script.read_text(encoding="utf-8")))
    return "\n".join(parts)


@pytest.mark.parametrize(
    "action_dir", [REPORT_ACTION, VERIFY_ACTION], ids=["report", "verify-source-run"]
)
class TestNeitherActionAnalysesAnything:
    def test_no_analysis_or_toolchain_command_is_referenced(
        self, action_dir: Path
    ) -> None:
        surface = _executable_surface(action_dir)
        found = [t for t in FORBIDDEN_EXECUTABLE_TOKENS if t in surface]
        assert found == [], (
            f"{action_dir.name} references analysis/toolchain command(s) {found}; "
            "a report-only Action must not analyse anything"
        )

    def test_no_build_tool_is_invoked_as_a_word(self, action_dir: Path) -> None:
        surface = _executable_surface(action_dir)
        found = [
            t
            for t in FORBIDDEN_WORD_TOKENS
            if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", surface)
        ]
        assert found == [], f"{action_dir.name} invokes build tool(s) {found}"

    def test_the_only_third_party_step_is_setup_python(self, action_dir: Path) -> None:
        action = yaml.safe_load((action_dir / "action.yml").read_text(encoding="utf-8"))
        uses = [s["uses"] for s in action["runs"]["steps"] if "uses" in s]
        assert all(u.startswith("actions/setup-python@") for u in uses), uses

    def test_the_only_install_is_abicheck_itself(self, action_dir: Path) -> None:
        """Exactly two install targets are permitted, and both are abicheck.

        `${ACTION_PATH}/../..` is the Action's own repository root -- the
        checkout the Action ref already pins -- and `abicheck==<version>` is
        the PyPI alternative. Anything else means the Action is installing
        something the analysed project supplied, which is how a report-only
        step acquires arbitrary code execution.
        """
        surface = _executable_surface(action_dir)
        installs = re.findall(r"pip install[^\n]*", surface)
        assert installs, "the Action must install abicheck"
        for install in installs:
            target = install.split()[-1].strip('"')
            assert target in (
                "${ACTION_PATH}/../..",
                "abicheck==${INPUT_ABICHECK_VERSION}",
            ), f"unexpected pip install target: {install}"
            # No requirements file, no project extras, nothing editable.
            assert not re.search(r"-r\s|requirements|\.\[|\s-e\s", install), install

    def test_it_is_a_composite_action_with_no_docker_step(
        self, action_dir: Path
    ) -> None:
        action = yaml.safe_load((action_dir / "action.yml").read_text(encoding="utf-8"))
        assert action["runs"]["using"] == "composite"
        assert all(
            "docker" not in str(s.get("uses", "")) for s in action["runs"]["steps"]
        )


class TestVacuityGuards:
    """The static checks above would pass on an empty file.

    These prove the haystack is real and that the matcher can actually fire,
    so a refactor that silently emptied `_executable_surface` (a renamed
    key, a moved script) fails here instead of turning every assertion above
    green for the wrong reason.
    """

    @pytest.mark.parametrize(
        "action_dir", [REPORT_ACTION, VERIFY_ACTION], ids=["report", "verify"]
    )
    def test_the_surface_is_not_empty(self, action_dir: Path) -> None:
        surface = _executable_surface(action_dir)
        assert len(surface) > 500
        assert "abicheck.frontends.action.cli" in surface

    def test_the_matcher_detects_a_planted_violation(self) -> None:
        planted = "run: castxml --version\nrun: gcc -c x.c\n"
        assert [t for t in FORBIDDEN_EXECUTABLE_TOKENS if t in planted] == [
            "castxml",
            "gcc",
        ]

    def test_the_word_matcher_does_not_fire_on_substrings(self) -> None:
        """`make` must not match `makefile-free`, and `cc` must not match
        `success`. A rule that did would be relaxed away the first time it
        cried wolf, which is how these checks stop being checks."""
        benign = "success and a bearing and a makeshift and ldd-free"
        found = [
            t
            for t in FORBIDDEN_WORD_TOKENS
            if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", benign)
        ]
        assert found == []


class TestDeclaredSurface:
    """The Action's own input/output contract, pinned.

    An Action's outputs are consumed by workflows this repository does not
    own, so removing or renaming one is a breaking change that should have
    to be made deliberately -- the same reason
    `tests/test_cli_root_surface.py` pins the CLI's root command set.
    """

    def test_report_action_outputs(self) -> None:
        action = yaml.safe_load(
            (REPORT_ACTION / "action.yml").read_text(encoding="utf-8")
        )
        assert set(action["outputs"]) == {
            "posted",
            "comment-url",
            "body-path",
            "body-bytes",
            "skipped-reason",
        }

    def test_report_action_inputs_cover_the_documented_contract(self) -> None:
        action = yaml.safe_load(
            (REPORT_ACTION / "action.yml").read_text(encoding="utf-8")
        )
        names = {str(k) for k in action["inputs"]}
        required = {
            "report",
            "detail",
            "on",
            "sha",
            "run-label",
            "report-url",
            "report-artifact-url",
            "path-prefix",
            "gate-api-break",
            "gate-breaking",
            "comment-identity",
            "profile",
            "repository",
            "pr-number",
            "github-token",
            "job-summary",
            "max-comment-bytes",
            "max-summary-bytes",
            "dry-run",
        }
        assert required <= names, sorted(required - names)

    #: Outputs published before ADR-073's provenance slice. A consumer may
    #: already read any of them, so none may DISAPPEAR; adding one is not a
    #: breaking change and is not pinned here.
    ESTABLISHED_VERIFY_OUTPUTS = frozenset(
        {
            "verified",
            "pr-number",
            "pr-head-sha",
            "tested-sha",
            "from-fork",
            "artifact-path",
            "refusal-code",
        }
    )

    def test_verify_action_outputs(self) -> None:
        """No published output disappears, and every declared one is wired.

        Exact-set equality pinned the wrong thing: it fails for an added
        output, which breaks nobody, while saying nothing about whether the
        outputs it lists actually resolve. An output declared with a typo in
        the step id is silently empty for every consumer, which is the
        failure that actually reaches people.
        """
        action = yaml.safe_load(
            (VERIFY_ACTION / "action.yml").read_text(encoding="utf-8")
        )
        declared = set(action["outputs"])
        missing = self.ESTABLISHED_VERIFY_OUTPUTS - declared
        assert not missing, f"published outputs removed: {sorted(missing)}"

        step_ids = {step["id"] for step in action["runs"]["steps"] if step.get("id")}
        for name, spec in action["outputs"].items():
            value = str(spec["value"])
            referenced = {
                fragment.split(".")[0]
                for fragment in value.split("steps.")[1:]
                if "." in fragment
            }
            assert referenced, f"output {name} names no step"
            unknown = referenced - step_ids
            assert not unknown, f"output {name} names missing step(s) {unknown}"

    def test_every_input_carries_help_text(self) -> None:
        for action_dir in (REPORT_ACTION, VERIFY_ACTION):
            action = yaml.safe_load(
                (action_dir / "action.yml").read_text(encoding="utf-8")
            )
            for name, spec in action["inputs"].items():
                assert str(spec.get("description", "")).strip(), (
                    f"{action_dir.name}: input {name} has no description"
                )


# ---------------------------------------------------------------------------
# The behavioural half
# ---------------------------------------------------------------------------


_TRAP = """#!/bin/sh
echo "$0 $*" >> "$ABICHECK_TRAP_LOG"
exit 127
"""


def _trap_path(tmp_path: Path, names: tuple[str, ...]) -> tuple[Path, Path]:
    """A PATH prefix whose *names* all record their invocation and fail."""
    trap_dir = tmp_path / "trapbin"
    trap_dir.mkdir()
    log = tmp_path / "trap.log"
    log.touch()
    for name in names:
        script = trap_dir / name
        script.write_text(_TRAP, encoding="utf-8")
        script.chmod(0o755)
    return trap_dir, log


class TestThePublisherReallyDoesNotAnalyse:
    """Run the Action's own script, not a reading of it."""

    @staticmethod
    def _report(tmp_path: Path) -> Path:
        path = tmp_path / "compare.json"
        path.write_text(
            json.dumps(
                {
                    "library": "libthing.so",
                    "old_version": "1.0",
                    "new_version": "1.1",
                    "verdict": "BREAKING",
                    "changes": [
                        {
                            "kind": "func_removed",
                            "symbol": "thing_open",
                            "severity": "breaking",
                            "description": "Function removed",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _run(self, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
        require_bash()
        trap_dir, log = _trap_path(
            tmp_path,
            ("gh", "gcc", "g++", "cc", "clang", "castxml", "bear", "cmake", "make"),
        )
        environment = dict(os.environ)
        # Cleared rather than merely defaulted: this process may itself be
        # running inside a workflow with a real token and repository in its
        # environment, and a test that silently inherited them would assert
        # against a different configuration than it declares.
        for inherited in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GITHUB_REPOSITORY",
            "GITHUB_REF",
        ):
            environment.pop(inherited, None)
        environment.update(
            {
                "PATH": f"{trap_dir}{os.pathsep}{environment['PATH']}",
                "ABICHECK_TRAP_LOG": str(log),
                "RUNNER_TEMP": str(tmp_path / "runner-temp"),
                "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
                "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
                "GITHUB_RUN_ID": "4242",
                "GITHUB_RUN_ATTEMPT": "1",
                "INPUT_REPORT": str(self._report(tmp_path)),
                "INPUT_DRY_RUN": "true",
                "INPUT_PROFILE": "linux-gcc",
            }
        )
        environment.update(env)
        (tmp_path / "runner-temp").mkdir(exist_ok=True)
        (tmp_path / "outputs.txt").touch()
        (tmp_path / "summary.md").touch()
        result = subprocess.run(
            [bash_executable(), str(REPORT_ACTION / "run.sh")],
            capture_output=True,
            text=True,
            env=environment,
            cwd=tmp_path,
        )
        self.trap_log = log
        return result

    @staticmethod
    def _outputs(tmp_path: Path) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for line in (tmp_path / "outputs.txt").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key] = value
        return parsed

    def test_a_dry_run_renders_without_invoking_any_tool_or_the_api(
        self, tmp_path: Path
    ) -> None:
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert self.trap_log.read_text(encoding="utf-8") == "", (
            "the publisher invoked a trapped tool: "
            + self.trap_log.read_text(encoding="utf-8")
        )
        outputs = self._outputs(tmp_path)
        assert outputs["posted"] == "false"
        assert outputs["skipped-reason"] == "dry-run"
        body = Path(outputs["body-path"]).read_text(encoding="utf-8")
        assert "ABI BREAKING" in body
        assert "thing_open" in body
        assert int(outputs["body-bytes"]) == len(body.encode("utf-8"))

    def test_the_sticky_identity_defaults_to_the_profile(self, tmp_path: Path) -> None:
        result = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        body = Path(self._outputs(tmp_path)["body-path"]).read_text(encoding="utf-8")
        assert '"identity": "abicheck:linux-gcc"' in body
        assert '"run_id": "4242"' in body

    def test_a_clean_report_under_on_changes_publishes_nothing(
        self, tmp_path: Path
    ) -> None:
        clean = tmp_path / "clean.json"
        clean.write_text(
            json.dumps(
                {
                    "library": "libthing.so",
                    "old_version": "1.0",
                    "new_version": "1.1",
                    "verdict": "NO_CHANGE",
                    "changes": [],
                }
            ),
            encoding="utf-8",
        )
        result = self._run(tmp_path, INPUT_REPORT=str(clean))
        assert result.returncode == 0, result.stderr
        outputs = self._outputs(tmp_path)
        assert outputs["posted"] == "false"
        assert outputs["skipped-reason"] == "no-changes"

    def test_an_aggregate_document_renders_through_the_action(
        self, tmp_path: Path
    ) -> None:
        """End to end over Gap 1's shape: the publisher is the consumer that
        made the missing aggregate branch a user-visible bug."""
        (tmp_path / "linux.json").write_text(
            json.dumps(
                {
                    "library": "libthing.so",
                    "old_version": "1.0",
                    "new_version": "1.1",
                    "verdict": "BREAKING",
                    "changes": [
                        {
                            "kind": "func_removed",
                            "symbol": "thing_open",
                            "severity": "breaking",
                            "description": "Function removed",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        aggregate = tmp_path / "aggregate.json"
        aggregate.write_text(
            json.dumps(
                {
                    "aggregate_schema_version": "1.4",
                    "status": "fail",
                    "compatibility": {"verdict": "BREAKING", "analyzed_targets": 1},
                    "coverage": {
                        "status": "complete",
                        "required_targets": 1,
                        "analyzed_required_targets": 1,
                        "missing_required_targets": [],
                        "blocking": False,
                    },
                    "gate": {
                        "passed": False,
                        "exit_code": 4,
                        "blocking_targets": ["linux-x86_64"],
                        "coverage_blocking": False,
                    },
                    "contract_coverage": {
                        "exit_contribution": 0,
                        "incomplete_targets": [],
                    },
                    "analysis_assurance": {
                        "exit_contribution": 0,
                        "incomplete_targets": [],
                    },
                    "scope_completeness": {
                        "exit_contribution": 0,
                        "incomplete_targets": [],
                    },
                    "disposition_audit_missing_targets": [],
                    "effective_policy": {
                        "missing_required": "fail",
                        "unexpected_target": "include",
                        "source": "default",
                    },
                    "targets": [
                        {
                            "target_id": "linux-x86_64",
                            "required": True,
                            "state": "analyzed",
                            "compatibility_verdict": "BREAKING",
                            "gate": {
                                "exit_code": 4,
                                "blocking": True,
                                "blocking_categories": ["abi_breaking"],
                                "from_report": True,
                            },
                            "contract_coverage_exit": 0,
                            "analysis_assurance_exit": 0,
                            "scope_completeness_exit": 0,
                            "report_path": "linux.json",
                        }
                    ],
                    "unexpected_targets": [],
                    "profile_matrix": [],
                    "finding_matrix": [],
                }
            ),
            encoding="utf-8",
        )
        result = self._run(tmp_path, INPUT_REPORT=str(aggregate))
        assert result.returncode == 0, result.stderr
        body = Path(self._outputs(tmp_path)["body-path"]).read_text(encoding="utf-8")
        assert "ABI BREAKING" in body
        assert "linux-x86_64" in body
        assert "thing_open" in body

    @pytest.mark.parametrize(
        "bad,expected",
        [
            ({"INPUT_ON": "sometimes"}, "must be always, changes or never"),
            ({"INPUT_DETAIL": "verbose"}, "must be summary, standard or full"),
            ({"INPUT_REPORT": "/nonexistent/report.json"}, "does not exist"),
        ],
    )
    def test_an_invalid_input_fails_the_step_rather_than_publishing_nothing(
        self, tmp_path: Path, bad: dict[str, str], expected: str
    ) -> None:
        """A publication-side failure is loud. Silently producing no comment
        would be indistinguishable from "the report had nothing to say"."""
        result = self._run(tmp_path, **bad)
        assert result.returncode != 0
        assert expected in result.stderr

    def test_publishing_without_a_token_fails_rather_than_degrading(
        self, tmp_path: Path
    ) -> None:
        result = self._run(tmp_path, INPUT_DRY_RUN="false", INPUT_PR_NUMBER="7")
        assert result.returncode != 0
        assert "github-token" in result.stderr

    @pytest.mark.parametrize(
        "pr_number", ["", "not-a-number", "7; rm -rf /", "../../etc", "7 8"]
    )
    def test_a_non_numeric_pr_number_never_reaches_an_api_path(
        self, tmp_path: Path, pr_number: str
    ) -> None:
        result = self._run(
            tmp_path,
            INPUT_DRY_RUN="false",
            INPUT_PR_NUMBER=pr_number,
            GH_TOKEN="fake-token",
            INPUT_REPOSITORY="example-org/example-lib",
        )
        assert result.returncode != 0
        assert "pr-number" in result.stderr
        assert self.trap_log.read_text(encoding="utf-8") == "", (
            "gh was invoked with an unvalidated pull-request number"
        )

    @pytest.mark.parametrize(
        "repository", ["not-a-repo", "a/b/c", "owner/repo; id", "$(id)/x"]
    )
    def test_a_malformed_repository_never_reaches_an_api_path(
        self, tmp_path: Path, repository: str
    ) -> None:
        result = self._run(
            tmp_path,
            INPUT_DRY_RUN="false",
            INPUT_PR_NUMBER="7",
            GH_TOKEN="fake-token",
            INPUT_REPOSITORY=repository,
        )
        assert result.returncode != 0
        assert "repository" in result.stderr
        assert self.trap_log.read_text(encoding="utf-8") == ""


class TestVerifySourceRunInputValidation:
    """The selection Action's own shell, run for real.

    Only its *input grammar* is exercised here — the checks themselves are
    covered exhaustively, and without a runner, in
    `tests/test_action_run_selection.py`. What this adds is the half a unit
    test cannot reach: that the script actually refuses a malformed value
    *before* any of it reaches an API path, and that it does not fall over
    on its own `set -euo pipefail` when an optional input is left empty.
    """

    def _run(self, tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
        require_bash()
        trap_dir, log = _trap_path(tmp_path, ("gh",))
        environment = dict(os.environ)
        for inherited in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_REPOSITORY"):
            environment.pop(inherited, None)
        environment.update(
            {
                "PATH": f"{trap_dir}{os.pathsep}{environment['PATH']}",
                "ABICHECK_TRAP_LOG": str(log),
                "RUNNER_TEMP": str(tmp_path),
                "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
                "INPUT_SOURCE_RUN_ID": "555",
                "INPUT_EXPECT_REPOSITORY": "example-org/example-lib",
                "INPUT_ARTIFACT_NAME": "abi-reports",
                "GH_TOKEN": "fake-token",
            }
        )
        environment.update(env)
        (tmp_path / "outputs.txt").touch()
        result = subprocess.run(
            [bash_executable(), str(VERIFY_ACTION / "run.sh")],
            capture_output=True,
            text=True,
            env=environment,
            cwd=tmp_path,
        )
        self.trap_log = log
        return result

    @pytest.mark.parametrize(
        "env,expected",
        [
            ({"INPUT_SOURCE_RUN_ID": "not-a-number"}, "source-run-id"),
            ({"INPUT_SOURCE_RUN_ID": "555; id"}, "source-run-id"),
            ({"INPUT_SOURCE_RUN_ID": ""}, "source-run-id"),
            ({"INPUT_EXPECT_REPOSITORY": "not-a-repo"}, "expect-repository"),
            ({"INPUT_EXPECT_REPOSITORY": "a/b/c"}, "expect-repository"),
            ({"INPUT_ARTIFACT_NAME": ""}, "artifact-name"),
            ({"INPUT_TESTED_SHA": "not-a-sha!"}, "tested-sha"),
            ({"INPUT_CLAIMED_PR_NUMBER": "seven"}, "claimed-pr-number"),
            ({"INPUT_EXPECT_RUN_ATTEMPT": "second"}, "expect-run-attempt"),
        ],
    )
    def test_a_malformed_input_is_refused_before_any_api_call(
        self, tmp_path: Path, env: dict[str, str], expected: str
    ) -> None:
        result = self._run(tmp_path, **env)
        assert result.returncode != 0
        assert expected in result.stderr
        assert self.trap_log.read_text(encoding="utf-8") == "", (
            "gh was invoked before the input was validated"
        )
        outputs = (tmp_path / "outputs.txt").read_text(encoding="utf-8")
        assert "verified=false" in outputs

    def test_valid_inputs_get_as_far_as_the_first_api_call(
        self, tmp_path: Path
    ) -> None:
        """The positive control. Without it, every case above passes against
        a script that refuses unconditionally — and the trapped `gh` makes
        the call itself fail, so no network is touched either way."""
        result = self._run(tmp_path)
        assert result.returncode != 0  # the trapped gh fails
        assert "actions/runs/555" in self.trap_log.read_text(encoding="utf-8")
