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

"""Differential pin of the Action's fail-on gate (ADR-091 R15).

ADR-091 R15 (as amended) makes ``fail-on-breaking`` and ``fail-on-api-break``
a pure mapping of the CLI exit code in ``action/run.sh``: exit 4 fails the
step when fail-on-breaking is true, exit 2 when fail-on-api-break is true.
The step outcome, exit code and gate annotations are pinned across

    fail-on-breaking x fail-on-api-break x CLI result x severity-preset

Each cell runs the real ``run.sh`` against a stub ``abicheck`` that exits
with the CLI result of that cell and writes the matching JSON report. The
stub stands in for the CLI's exit computation, so this module pins the
Action's side of the contract: what the step does with a given CLI result.
``test_the_gate_follows_the_exit_code_not_the_report`` pins that a report
verdict disagreeing with the exit code does not move the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _run_action,
)

pytestmark = REQUIRES_POSIX_SHELL

#: name -> (CLI exit code, report verdict). ``not_comparable`` is the one
#: operational/assurance outcome: exit 16 (ADR-050 D2) fails unconditionally.
CLI_RESULTS = {
    "compatible": (0, "COMPATIBLE"),
    "api_break": (2, "API_BREAK"),
    "abi_break": (4, "BREAKING"),
    "not_comparable": (16, "NOT_COMPARABLE"),
}

#: Expected step outcome, from today's gate in ``run.sh``
#: (``_finalize_verdict``'s compare branch): a BREAKING verdict fails when
#: fail-on-breaking is true, an API_BREAK fails when fail-on-api-break is
#: true, NOT_COMPARABLE always fails, and a clean compatible run passes.
EXPECTED_PASS = {
    ("compatible", True, True): True,
    ("compatible", True, False): True,
    ("compatible", False, True): True,
    ("compatible", False, False): True,
    ("api_break", True, True): False,
    ("api_break", True, False): True,
    ("api_break", False, True): False,
    ("api_break", False, False): True,
    ("abi_break", True, True): False,
    ("abi_break", True, False): False,
    ("abi_break", False, True): True,
    ("abi_break", False, False): True,
    ("not_comparable", True, True): False,
    ("not_comparable", True, False): False,
    ("not_comparable", False, True): False,
    ("not_comparable", False, False): False,
}


def _stub(tmp_path: Path, *, exit_code: int, verdict: str) -> Path:
    """An ``abicheck`` that writes a report carrying *verdict* to every file
    ``-o FORMAT=PATH`` names, then exits *exit_code*."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"verdict": verdict, "changes": []}), encoding="utf-8"
    )
    log = tmp_path / "argv.log"
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'for arg in "$@"; do printf \'%s\\n\' "$arg"; done >> "{log}"\n'
        f"printf -- '---\\n' >> \"{log}\"\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "-o" && "$arg" == *=* && "$arg" != *=- ]]; then\n'
        f'    cp "{payload}" "${{arg#*=}}"\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _run_cell(
    tmp_path: Path,
    *,
    fail_on_breaking: bool,
    fail_on_api_break: bool,
    cli: str,
    preset: str | None,
) -> tuple[dict, str]:
    exit_code, verdict = CLI_RESULTS[cli]
    bindir = _stub(tmp_path, exit_code=exit_code, verdict=verdict)
    env = _compare_env(tmp_path)
    env["INPUT_FAIL_ON_BREAKING"] = str(fail_on_breaking).lower()
    env["INPUT_FAIL_ON_API_BREAK"] = str(fail_on_api_break).lower()
    if preset is not None:
        env["INPUT_SEVERITY_PRESET"] = preset
    outputs = _run_action(tmp_path, env, bindir)
    argv = (tmp_path / "argv.log").read_text(encoding="utf-8").split("---\n")[0]
    return outputs, argv


CELLS = [
    (cli, fob, foa, preset)
    for cli in CLI_RESULTS
    for fob in (True, False)
    for foa in (True, False)
    for preset in (None, "default")
]


@pytest.mark.parametrize(("cli", "fob", "foa", "preset"), CELLS)
def test_step_outcome_and_exit_code(
    tmp_path: Path, cli: str, fob: bool, foa: bool, preset: str | None
) -> None:
    outputs, _ = _run_cell(
        tmp_path, fail_on_breaking=fob, fail_on_api_break=foa, cli=cli, preset=preset
    )
    passed = outputs["_exit"] == 0
    assert passed == EXPECTED_PASS[(cli, fob, foa)], outputs["_stdout"][-400:]
    assert outputs["verdict"] == CLI_RESULTS[cli][1]
    assert outputs["exit-code"] == str(CLI_RESULTS[cli][0])


@pytest.mark.parametrize(("cli", "fob", "foa", "preset"), CELLS)
def test_gate_annotation_names_the_failing_gate(
    tmp_path: Path, cli: str, fob: bool, foa: bool, preset: str | None
) -> None:
    outputs, _ = _run_cell(
        tmp_path, fail_on_breaking=fob, fail_on_api_break=foa, cli=cli, preset=preset
    )
    stdout = outputs["_stdout"]
    abi_error = "::error::ABI break detected" in stdout
    api_error = "::error::API break detected" in stdout
    assert abi_error == (cli == "abi_break" and fob)
    assert api_error == (cli == "api_break" and foa)


@pytest.mark.parametrize(("cli", "fob", "foa", "preset"), CELLS)
def test_the_preset_is_forwarded_and_does_not_change_the_gate(
    tmp_path: Path, cli: str, fob: bool, foa: bool, preset: str | None
) -> None:
    outputs, argv = _run_cell(
        tmp_path, fail_on_breaking=fob, fail_on_api_break=foa, cli=cli, preset=preset
    )
    assert ("--severity-preset default" in argv.replace("\n", " ")) == (
        preset == "default"
    )
    passed = outputs["_exit"] == 0
    assert passed == EXPECTED_PASS[(cli, fob, foa)]


#: (CLI exit code, report verdict) pairs where the report disagrees with the
#: exit code; the gate must follow the exit code alone (R15).
MISMATCHED = {
    "exit2_report_breaking": (2, "BREAKING"),
    "exit0_report_breaking": (0, "BREAKING"),
}


@pytest.mark.parametrize("case", sorted(MISMATCHED))
@pytest.mark.parametrize("fob", [True, False])
@pytest.mark.parametrize("foa", [True, False])
def test_the_gate_follows_the_exit_code_not_the_report(
    tmp_path: Path, case: str, fob: bool, foa: bool
) -> None:
    exit_code, verdict = MISMATCHED[case]
    bindir = _stub(tmp_path, exit_code=exit_code, verdict=verdict)
    env = _compare_env(tmp_path)
    env["INPUT_FAIL_ON_BREAKING"] = str(fob).lower()
    env["INPUT_FAIL_ON_API_BREAK"] = str(foa).lower()
    outputs = _run_action(tmp_path, env, bindir)
    expected_pass = not (exit_code == 2 and foa)
    assert (outputs["_exit"] == 0) == expected_pass, outputs["_stdout"][-400:]
    assert "::error::ABI break detected" not in outputs["_stdout"]
