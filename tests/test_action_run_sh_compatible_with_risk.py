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

"""R1 (CLI-audit): the composite Action's exit-0 verdict resolution
(``_resolve_clean_exit_verdict`` in ``action/run.sh``) hard-mapped exit 0 to
``VERDICT=COMPATIBLE`` unconditionally, only escalating when the report's own
``verdict`` said ``BREAKING``/``API_BREAK``. ``COMPATIBLE_WITH_RISK`` --
itself a real, exit-0 tier the CLI's own exit-code contract documents
alongside ``COMPATIBLE``/``NO_CHANGE`` -- fell through unnoticed, so a run
whose JSON report said ``COMPATIBLE_WITH_RISK`` with a full list of risk
findings still published ``verdict=COMPATIBLE`` and a step summary reading
"No binary ABI break detected", silently dropping every risk finding from
the Action's own output.

Mirrors ``test_action_coverage_verdict.py``'s harness (stub ``abicheck`` on
``PATH`` writing the report the script reads, real end-to-end run.sh
execution) rather than restating the shell logic in Python.
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


def _stub_abicheck(tmp_path: Path, *, exit_code: int, report: dict) -> Path:
    """A fake ``abicheck`` on PATH: writes *report* to whatever ``-o`` names."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(report), encoding="utf-8")
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
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    """Run ``action/run.sh`` and return its ``GITHUB_OUTPUT`` key/value pairs."""
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
    outputs["_stderr"] = proc.stderr
    outputs["_exit"] = proc.returncode
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    return outputs


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


def _risk_report() -> dict:
    return {
        "report_schema_version": "2.49",
        "verdict": "COMPATIBLE_WITH_RISK",
        "changes": [
            {
                "kind": "cxx_standard_floor_raised",
                "symbol": "libfoo.so",
                "description": "Toolchain floor raised",
                "severity": "risk",
            }
        ],
    }


class TestCompareExitZeroReportsCompatibleWithRisk:
    def test_verdict_output_is_compatible_with_risk_not_compatible(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, report=_risk_report())
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
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["exit-code"] == "0", outputs

    def test_step_does_not_fail_for_a_clean_exit_risk_verdict(
        self, tmp_path: Path
    ) -> None:
        """COMPATIBLE_WITH_RISK at exit 0 must not be treated as a gated
        break -- fail-on-breaking/fail-on-api-break never match this tier,
        so the step stays green, same as before this fix."""
        bindir = _stub_abicheck(tmp_path, exit_code=0, report=_risk_report())
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
        assert outputs["_exit"] == 0, outputs

    def test_plain_compatible_report_is_unaffected(self, tmp_path: Path) -> None:
        """Regression guard: an ordinary clean run must keep publishing
        plain COMPATIBLE, not COMPATIBLE_WITH_RISK."""
        report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
        }
        bindir = _stub_abicheck(tmp_path, exit_code=0, report=report)
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
        assert outputs["verdict"] == "COMPATIBLE", outputs


class TestFallbackTextReportsCompatibleWithRisk:
    """P2 (Codex review, PR #1016): ``_report_compat_verdict``'s markdown/
    text fallback -- used whenever no JSON report exists, which is exactly
    what happens for a directory/package release compare (``--write`` is
    rejected for that operand) or any ``extra-args: --write markdown=...``
    that suppresses the JSON sidecar (``action/AGENTS.md``) -- only matched
    ``API_BREAK``/``BREAKING``. A report the CLI itself classified
    ``COMPATIBLE_WITH_RISK`` reached this fallback and matched nothing, so
    ``_resolve_clean_exit_verdict`` silently kept its ``VERDICT=COMPATIBLE``
    default even though the rendered report said otherwise. Mirrors
    ``test_action_coverage_verdict.py``'s ``TestTheReleaseTableVerdictIsRead``
    harness (a stub that ``cat``s a fixed report to stdout, no ``-o``, so
    ``_text_report_content`` falls back to captured stdout) rather than a
    second copy of it.
    """

    def _summary(self, tmp_path: Path, verdict_line: str, exit_code: int) -> dict:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        report = tmp_path / "report.md"
        report.write_text(
            f"# ABI report\n\n| | |\n|---|---|\n{verdict_line}\n", encoding="utf-8"
        )
        stub = bindir / "abicheck"
        stub.write_text(
            f"#!/usr/bin/env bash\ncat {report}\nexit {exit_code}\n", encoding="utf-8"
        )
        stub.chmod(0o755)
        return _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "markdown",
            },
            bindir,
        )

    def test_the_colon_form_reports_risk_at_exit_zero(self, tmp_path: Path) -> None:
        outputs = self._summary(
            tmp_path,
            "**Verdict:** ⚠️ `COMPATIBLE_WITH_RISK` "
            "— compatible but carries deployment risk",
            0,
        )
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["_exit"] == 0, outputs

    def test_the_table_row_form_reports_risk_at_exit_zero(self, tmp_path: Path) -> None:
        """The release fan-out's table-row spelling (no colon) must also be
        recognised, same as the existing BREAKING/API_BREAK table-row case."""
        outputs = self._summary(
            tmp_path, "| **Verdict** | ⚠️ `COMPATIBLE_WITH_RISK` |", 0
        )
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_plain_compatible_report_is_unaffected(self, tmp_path: Path) -> None:
        """Negative control: widening the fallback regex must not make a
        plain COMPATIBLE report match on some later word."""
        outputs = self._summary(tmp_path, "**Verdict:** ✅ `COMPATIBLE`", 0)
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_breaking_table_row_still_escalates(self, tmp_path: Path) -> None:
        """Negative control: the pre-existing BREAKING/API_BREAK fallback
        match (exercised at exit 2 by test_action_coverage_verdict.py) must
        keep working unchanged now that a third alternative was added."""
        outputs = self._summary(tmp_path, "| **Verdict** | \U0001f4a5 `BREAKING` |", 2)
        assert outputs["verdict"] == "BREAKING", outputs


class TestScanExitZeroReportsCompatibleWithRisk:
    def test_verdict_output_is_compatible_with_risk_not_compatible(
        self, tmp_path: Path
    ) -> None:
        report = {**_risk_report(), "scan_schema_version": "1.2"}
        bindir = _stub_abicheck(tmp_path, exit_code=0, report=report)
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
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["exit-code"] == "0", outputs
        assert outputs["_exit"] == 0, outputs
