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

"""The composite Action's mapping for P0.4's analysis-assurance exit
(``--require-complete-analysis``, ``abicheck/analysis_assurance.py``).

Before this, ``action/run.sh`` had no notion of this axis at all: an exit 1
caused by incomplete ``analysis_assurance`` (with the flag given via
``extra-args``, since neither ``compare`` nor ``scan`` had a dedicated Action
input for it) fell through the existing exit-1 disambiguation and was
mislabeled -- ``SEVERITY_ERROR`` on ``compare`` (a *severity-policy* failure
it is not) or ``ERROR`` on ``scan`` (an *operational* failure it is not).
The axis is neither: like ADR-049's contract-coverage axis it never rewrites
the compatibility verdict, only floors the exit code, and is unconditional
(no ``fail-on-*`` flag can turn it off).

Mirrors ``test_action_coverage_verdict.py``'s own harness style (real
subprocess through ``run.sh``, a shebang-dispatched ``abicheck`` stub on
``PATH``) rather than importing it: this repo's own convention for these
``test_action_*`` modules is that each carries its own copy rather than a
shared import (see ``CLAUDE.md`` "M1-1"'s adjacent note in ``AGENTS.md`` on
the ``~24 test_action_*`` modules predating a canonical resolver).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parent.parent / "action"
RUN_SH = ACTION_DIR / "run.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)

#: The exact wording `assurance_floor_diagnostic` (analysis_assurance.py)
#: emits -- `_assurance_gated()` in run.sh greps for this substring on
#: stderr, since (unlike the coverage ledger) the JSON report's own
#: `analysis_assurance` block is not self-describing about whether the flag
#: was even passed this run.
ASSURANCE_STDERR = (
    "Analysis assurance incomplete (status='partial') under "
    "--require-complete-analysis: header context asymmetric between old and "
    "new. Exit code floored to 1. (P0.4 analysis-assurance axis). Use "
    "--format json for the full analysis_assurance block."
)

COVERAGE_STDERR = (
    "Contract coverage incomplete for the selected --contract domain: "
    "old/export_table. Exit code floored to 1."
)

ASSURANCE_BLOCK = {
    "schema_version": 1,
    "status": "partial",
    "notes": ["header context asymmetric between old and new"],
}


def _stub_abicheck(
    tmp_path: Path,
    *,
    exit_code: int,
    report: dict | None,
    stderr: str = "",
) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(report or {}), encoding="utf-8")
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "-o" ]]; then\n'
        f'    cp "{payload}" "$arg"\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        + (f'printf "%s\\n" {json.dumps(stderr)} >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    summary = tmp_path / "step_summary"
    summary.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env.get('PATH', '')}",
            "ACTION_PATH": str(ACTION_DIR),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUNNER_TEMP": str(runner_temp),
            "INPUT_ADD_JOB_SUMMARY": "true",
            **env_extra,
        }
    )
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    outputs = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_stdout"] = proc.stdout
    outputs["_exit"] = proc.returncode
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    return outputs


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


class TestScanMapsTheAssuranceExit:
    def test_an_assurance_gated_scan_is_not_an_operational_error(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
        assert outputs["exit-code"] == "1", outputs
        assert "ANALYSIS_INCOMPLETE" in outputs["_summary"], outputs["_summary"]
        assert "header context asymmetric" in outputs["_summary"], outputs["_summary"]

    def test_the_step_fails_unconditionally_even_with_fail_on_breaking_false(
        self, tmp_path: Path
    ) -> None:
        """No ``fail-on-*`` flag disables this axis, matching the
        contract-coverage axis's own unconditional gate."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_FAIL_ON_BREAKING": "false",
                "INPUT_FAIL_ON_API_BREAK": "false",
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs

    def test_a_run_without_the_flag_is_unaffected(self, tmp_path: Path) -> None:
        """No stderr diagnostic (the flag wasn't passed) -> exit 1 with
        neither coverage nor assurance signal stays a plain ERROR, exactly
        as it always did."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "ERROR", outputs


class TestCompareMapsTheAssuranceExit:
    def test_an_assurance_gated_compare_is_not_a_severity_failure(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={"verdict": "COMPATIBLE", "analysis_assurance": ASSURANCE_BLOCK},
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
        assert outputs["exit-code"] == "1", outputs
        assert "ANALYSIS_INCOMPLETE" in outputs["_summary"], outputs["_summary"]

    def test_a_severity_gate_and_assurance_gap_both_survive_together(
        self, tmp_path: Path
    ) -> None:
        """When the severity axis also gates at 1, SEVERITY_ERROR keeps the
        verdict label (the pre-existing coverage-vs-severity precedence),
        but the summary still names the assurance gap so it isn't silently
        dropped."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "analysis_assurance": ASSURANCE_BLOCK,
                "severity": {
                    "config": {},
                    "categories": {},
                    "exit_code": 1,
                    "blocking": True,
                    "blocking_categories": ["addition"],
                },
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs
        assert "also reports incomplete analysis assurance" in outputs["_stdout"], (
            outputs["_stdout"]
        )
        assert outputs["_exit"] == 1, outputs

    def test_a_coverage_gap_and_assurance_gap_both_survive_together(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "analysis_assurance": ASSURANCE_BLOCK,
                "contract_coverage_exit_contribution": 1,
                "contract_coverage_failures": [
                    {
                        "provider": "export_table",
                        "side": "old",
                        "record_id": "old/export_table",
                        "reason": "search_incomplete",
                        "status": "unavailable",
                        "completeness": "none",
                        "mode": "exports",
                        "suppressible": False,
                    }
                ],
            },
            stderr=f"{COVERAGE_STDERR}\n{ASSURANCE_STDERR}",
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs
        assert "also reports incomplete analysis assurance" in outputs["_stdout"], (
            outputs["_stdout"]
        )
        assert outputs["_exit"] == 1, outputs

    def test_the_step_fails_unconditionally_even_with_fail_on_breaking_false(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={"verdict": "COMPATIBLE", "analysis_assurance": ASSURANCE_BLOCK},
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_FAIL_ON_BREAKING": "false",
                "INPUT_FAIL_ON_API_BREAK": "false",
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs

    def test_a_run_without_the_flag_is_unaffected(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={"verdict": "COMPATIBLE", "analysis_assurance": ASSURANCE_BLOCK},
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs


class TestHostileReportContentCannotExecute:
    """This diff touches the action/workflow trust boundary (``action/
    run.sh``): the new ``_assurance_gated()`` stderr grep and the new
    ``assurance_notes`` Python query both read content an ``abicheck``
    invocation produced from its inputs, which can themselves come from a
    PR (a header, a symbol name, a policy note) an attacker controls.
    Asserting the *text* of the shell/Python changes does not prove a
    hostile value can't cause a side effect when run.sh's own shell
    interpolates it into a job-summary ``echo`` -- so these tests actually
    execute ``run.sh`` against a report/stderr crafted to look like a shell
    command substitution or an injection into the grep pattern, and assert
    the attempted payload never ran (no marker file materializes) while the
    run still reaches the correct, unspoofed verdict.
    """

    def test_a_command_substitution_in_assurance_notes_does_not_execute(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "pwned"
        payload = f"$(touch {marker})"
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {
                    "analysis_assurance": {
                        "schema_version": 1,
                        "status": "partial",
                        "notes": [payload],
                    }
                },
            },
            stderr=ASSURANCE_STDERR,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert not marker.exists(), (
            "the crafted analysis_assurance note executed as a shell command"
        )
        # The verdict computation is unaffected by the hostile payload --
        # it is carried through as inert text in the summary, not evaluated.
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
        assert payload in outputs["_summary"], outputs["_summary"]

    def test_a_forged_stderr_line_cannot_spoof_the_diagnostic_without_the_marker(
        self, tmp_path: Path
    ) -> None:
        """The exact substring `_assurance_gated()` requires
        ("Analysis assurance incomplete ... under --require-complete-
        analysis") must actually be present -- a stderr line that merely
        *mentions* assurance/incompleteness, crafted by an attacker who
        controls a symbol/header name embedded in some other diagnostic,
        must not be mistaken for the real, code-emitted floor notice."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {
                    "analysis_assurance": {
                        "schema_version": 1,
                        "status": "partial",
                        "notes": ["unrelated"],
                    }
                },
            },
            stderr=(
                "some unrelated warning mentioning analysis assurance "
                "incomplete and require-complete-analysis but not the real "
                "diagnostic shape"
            ),
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        # Falls through to the pre-existing catch-all, exactly as it does
        # with no diagnostic at all -- not a spoofed ANALYSIS_INCOMPLETE.
        assert outputs["verdict"] == "ERROR", outputs

    def test_shell_metacharacters_in_the_stderr_diagnostic_do_not_execute(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "pwned2"
        hostile_stderr = (
            f"Analysis assurance incomplete (status='partial') under "
            f"--require-complete-analysis: `; touch {marker} ; ` "
            f"$({marker}) header context asymmetric. Exit code floored to 1."
        )
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {"analysis_assurance": ASSURANCE_BLOCK},
            },
            stderr=hostile_stderr,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert not marker.exists(), (
            "shell metacharacters in the captured stderr diagnostic executed"
        )
        assert outputs["verdict"] == "ANALYSIS_INCOMPLETE", outputs
