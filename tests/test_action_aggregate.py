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

"""``actions/aggregate``'s shell: the three phases, and what each may fail on.

The distinction every test here defends is the one the hand-rolled
`aggregate … || true` destroyed: **a compatibility outcome is not an
operational one**. A real ABI break must leave this Action successful (it is a
reporting step, and its caller owns the gate); a document that does not
describe a real outcome must fail it.

`run.sh` is invoked as a real subprocess, so the phase dispatch, the exit-code
mapping and the `GITHUB_OUTPUT` contract are exercised as published rather
than as re-implemented in Python.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "aggregate"
RUN_SH = ACTION_DIR / "run.sh"
PROFILE = "linux-x86_64-gcc"


def _run(
    phase: str, env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    require_bash()
    github_output = cwd / f"github_output_{phase}"
    github_output.write_text("")
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    result = subprocess.run(
        [bash_executable(), str(RUN_SH), phase],
        capture_output=True,
        text=True,
        env={**base_env, "GITHUB_OUTPUT": str(github_output), **env_extra},
        cwd=cwd,
        check=False,
    )
    outputs: dict[str, str] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return result, outputs


def _report(path: Path, verdict: str = "COMPATIBLE") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"verdict": verdict, "changes": []}), encoding="utf-8")
    return path


def _declaration(tmp_path: Path) -> str:
    return json.dumps(
        [
            {
                "id": f"libfoo@{PROFILE}#accepted-main@headers",
                "report": str(_report(tmp_path / "in" / "foo.json")),
                "required": True,
            },
            {
                "id": f"libbar@{PROFILE}#accepted-main@headers",
                "report": "",
                "required": True,
            },
        ]
    )


@pytest.mark.skipif(not RUN_SH.is_file(), reason="actions/aggregate/run.sh not found")
class TestPhases:
    def test_collect_then_aggregate_then_validate(self, tmp_path: Path) -> None:
        reports = tmp_path / "reports"
        manifest = tmp_path / "expected.json"
        collect, collect_out = _run(
            "collect",
            {
                "INPUT_CHECKS": _declaration(tmp_path),
                "INPUT_REPORTS_DIR": str(reports),
                "INPUT_MANIFEST_PATH": str(manifest),
            },
            tmp_path,
        )
        assert collect.returncode == 0, collect.stdout + collect.stderr
        assert collect_out["expected"] == "2"
        assert collect_out["missing"] == "1"

        aggregate, aggregate_out = _run(
            "aggregate",
            {
                "INPUT_REPORTS_DIR": str(reports),
                "INPUT_MANIFEST_PATH": str(manifest),
                "INPUT_OUTPUTS": "json=aggregate.json",
            },
            tmp_path,
        )
        assert aggregate.returncode == 0, aggregate.stdout + aggregate.stderr
        # One declared check produced no report, so the coverage gate is
        # unmet -- a compatibility/coverage outcome, reported not raised.
        assert aggregate_out["compatibility-exit"] != "0"
        assert Path(aggregate_out["aggregate-path"]).is_file()

        validate, validate_out = _run(
            "validate",
            {
                "INPUT_AGGREGATE_PATH": aggregate_out["aggregate-path"],
                "INPUT_EXPECTED": collect_out["expected"],
            },
            tmp_path,
        )
        assert validate.returncode == 0, validate.stdout + validate.stderr
        assert validate_out["coverage"] == "partial"
        assert validate_out["analyzed"] == "1"
        assert json.loads(validate_out["channels"])["accepted-main"] == {
            "analyzed": 1,
            "unavailable": 1,
        }

    def test_a_refused_declaration_fails_the_step(self, tmp_path: Path) -> None:
        """A configuration error is operational, never a compatibility result."""
        reports = tmp_path / "reports"
        result, _ = _run(
            "collect",
            {
                "INPUT_CHECKS": json.dumps([{"id": "a@p#c@headers", "report": ""}]),
                "INPUT_REPORTS_DIR": str(reports),
                # Inside the reports directory -- the refusal under test.
                "INPUT_MANIFEST_PATH": str(reports / "expected.json"),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "configuration error" in result.stdout

    def test_a_missing_aggregate_document_fails_validation(
        self, tmp_path: Path
    ) -> None:
        result, _ = _run(
            "validate",
            {
                "INPUT_AGGREGATE_PATH": str(tmp_path / "nope.json"),
                "INPUT_EXPECTED": "1",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "never an empty finding set" in result.stdout

    def test_a_document_that_describes_nothing_fails_validation(
        self, tmp_path: Path
    ) -> None:
        document = tmp_path / "aggregate.json"
        document.write_text('{"aggregate_schema_version": "1.0"}', encoding="utf-8")
        result, _ = _run(
            "validate",
            {"INPUT_AGGREGATE_PATH": str(document), "INPUT_EXPECTED": "1"},
            tmp_path,
        )
        assert result.returncode == 1
        assert "does not describe a real outcome" in result.stdout

    def test_outputs_must_name_a_json_document(self, tmp_path: Path) -> None:
        result, _ = _run(
            "aggregate",
            {
                "INPUT_REPORTS_DIR": str(tmp_path),
                "INPUT_MANIFEST_PATH": str(tmp_path / "m.json"),
                "INPUT_OUTPUTS": "text=aggregate.txt",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "json=" in result.stdout

    def test_an_outputs_entry_naming_a_path_is_refused(self, tmp_path: Path) -> None:
        """A per-target ``report_path`` is resolved relative to the aggregate
        document's own directory, so writing it elsewhere silently costs every
        per-target detail downstream."""
        result, _ = _run(
            "aggregate",
            {
                "INPUT_REPORTS_DIR": str(tmp_path),
                "INPUT_MANIFEST_PATH": str(tmp_path / "m.json"),
                "INPUT_OUTPUTS": "json=/elsewhere/aggregate.json",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "bare filenames" in result.stdout

    def test_an_unknown_phase_is_refused(self, tmp_path: Path) -> None:
        result, _ = _run("publish", {}, tmp_path)
        assert result.returncode == 1
        assert "unknown phase" in result.stdout


@pytest.mark.skipif(
    not (ACTION_DIR / "action.yml").is_file(), reason="action.yml not found"
)
def test_every_declared_input_reaches_run_sh() -> None:
    """A declared input the composite steps forget to wire through is silently
    ignored by the *published* Action, even though every test that invokes
    ``run.sh`` directly still passes -- the same gap
    ``test_action_resolve_baseline`` pins for its own Action.

    The claim is "every declared input is *read somewhere in the composite
    steps*", which is what a caller setting it is owed. It is asserted two
    ways because the Action wires inputs two ways: most arrive as
    ``INPUT_<NAME>`` for ``run.sh``, while the analysis-context block names
    its variables for the CLI command that reads them
    (``ABICHECK_CTX_*``) and gates the whole step on
    ``inputs.record-analysis-context``. Matching only the first spelling
    would report a genuinely-wired input as dead; matching on the
    ``inputs.<name>`` *reference* covers both without weakening the check --
    an input nothing mentions at all still fails.
    """
    import yaml

    raw = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    forwarded: set[str] = set()
    referenced = ""
    for step in document["runs"]["steps"]:
        forwarded.update(step.get("env", {}))
        referenced += yaml.safe_dump(
            {k: step.get(k) for k in ("if", "env", "run", "with", "uses")}
        )
    # Consumed by the setup-python / install steps rather than by run.sh.
    consumed_elsewhere = {"python-version", "install"}
    for name in document["inputs"]:
        if name in consumed_elsewhere:
            continue
        expected = f"INPUT_{name.upper().replace('-', '_')}"
        assert expected in forwarded or f"inputs.{name}" in referenced, (
            f"input {name!r} is never read by any composite step"
        )


def test_the_input_wiring_scan_can_actually_fail() -> None:
    """Vacuity guard for the check above.

    The assertion is a disjunction over a dumped blob, which is exactly the
    shape that quietly becomes true for everything. So prove the negative
    case: a name no step mentions must not be found by either arm.
    """
    import yaml

    document = yaml.safe_load((ACTION_DIR / "action.yml").read_text(encoding="utf-8"))
    referenced = "".join(
        yaml.safe_dump({k: step.get(k) for k in ("if", "env", "run", "with", "uses")})
        for step in document["runs"]["steps"]
    )
    forwarded = {k for step in document["runs"]["steps"] for k in step.get("env", {})}
    assert "INPUT_NO_SUCH_INPUT" not in forwarded
    assert "inputs.no-such-input" not in referenced


class TestActionCliSurface:
    """Every command must be reachable through the entry point the Actions use.

    ``actions/*/run.sh`` invokes ``python -m abicheck.frontends.action.cli``,
    which executes ``cli.py`` as ``__main__``. Its sibling command module
    imports ``abicheck.frontends.action.cli`` -- a *different* module object --
    so the group the sibling decorates is not the one ``__main__`` holds.
    Calling the local group therefore offers only the commands defined in
    ``cli.py`` itself, and every command in the sibling fails with Click's
    "No such command", at runtime, in CI, with the unit tests all green.

    Asserted through a real subprocess, because that is the only way the two
    module objects actually come into existence.
    """

    COMMANDS = (
        "resolve-libraries",
        "collect-checks",
        "validate-aggregate",
        "verify-tag",
        "select-producer-run",
        # The publication commands that already lived in cli.py -- listed so a
        # future split cannot quietly move the breakage the other way.
        "comment",
        "verify-run",
        "extract-artifact",
        "emit-fields",
        "flatten-pages",
    )

    def test_every_command_is_listed(self) -> None:
        listing = subprocess.run(
            [sys.executable, "-m", "abicheck.frontends.action.cli", "--help"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        missing = [name for name in self.COMMANDS if name not in listing]
        assert not missing, f"not reachable via -m: {missing}\n{listing}"

    @pytest.mark.parametrize("command", COMMANDS)
    def test_every_command_can_actually_be_invoked(self, command: str) -> None:
        """Listing is not invocation: assert Click resolves each name.

        ``--help`` on the command exits 0; a name the group does not carry
        exits 2 with "No such command".
        """
        result = subprocess.run(
            [sys.executable, "-m", "abicheck.frontends.action.cli", command, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No such command" not in result.stderr
