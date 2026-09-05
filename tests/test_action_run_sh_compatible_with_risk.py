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

    def test_job_summary_carries_a_verdict_banner(self, tmp_path: Path) -> None:
        """P2 follow-up (Codex review, PR #1016): the job-summary's own
        ``case $VERDICT`` dispatch had no ``COMPATIBLE_WITH_RISK`` arm and no
        ``*)`` default, so a bash `case` with no match silently omits the
        whole banner -- `add-job-summary: true` published a summary with the
        findings table but no verdict line at all for this tier."""
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
        assert "COMPATIBLE_WITH_RISK" in outputs["_summary"], outputs


class TestRenderedTextIsNeverReconstructedIntoAVerdict:
    """ADR-063 Track T8: ``_report_compat_verdict`` used to end with a ``sed``
    over the *rendered* markdown/text report's own ``Verdict:``/``**Verdict**``
    line, reached whenever no JSON report exists -- a directory/package
    release compare (``--write`` is rejected for that operand), or any
    ``extra-args: --write markdown=...`` that suppresses the JSON sidecar
    (``action/AGENTS.md``). That layer is retired: a verdict is read from the
    structured ``run_outcome``/``verdict`` JSON contract or not at all, and
    prose is never regex-scraped back into one.

    So with no JSON anywhere, ``_report_compat_verdict`` prints nothing and
    the verdict the ``case $ABICHECK_EXIT in ...`` dispatch derived from the
    process exit code -- the transport-level signal the plan deliberately
    keeps -- is published unchanged.

    Same harness as before (a stub that ``cat``s a fixed rendered report to
    stdout, no ``-o``), so these cases pin the *new* answer for exactly the
    inputs that used to be reconstructed.
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

    def test_the_colon_form_is_not_scraped_at_exit_zero(self, tmp_path: Path) -> None:
        """Was ``COMPATIBLE_WITH_RISK`` via the rendered-prose regex; now the
        exit-0 dispatch's own answer, because no JSON stated a verdict."""
        outputs = self._summary(
            tmp_path,
            "**Verdict:** ⚠️ `COMPATIBLE_WITH_RISK` "
            "— compatible but carries deployment risk",
            0,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_the_table_row_form_is_not_scraped_at_exit_zero(
        self, tmp_path: Path
    ) -> None:
        """The release fan-out's table-row spelling (no colon) was the second
        shape the retired regex matched; it is not reconstructed either."""
        outputs = self._summary(
            tmp_path, "| **Verdict** | ⚠️ `COMPATIBLE_WITH_RISK` |", 0
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_plain_compatible_report_is_unaffected(self, tmp_path: Path) -> None:
        """Unchanged in both regimes: a clean exit-0 run publishes
        COMPATIBLE."""
        outputs = self._summary(tmp_path, "**Verdict:** ✅ `COMPATIBLE`", 0)
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_a_breaking_table_row_no_longer_escalates_exit_two(
        self, tmp_path: Path
    ) -> None:
        """The escalation this fallback fed is exactly what T8 retires: exit 2
        is the CLI's direct, unambiguous API_BREAK contract, and a rendered
        ``BREAKING`` table row is no longer evidence that outranks it. Only a
        structured report field may escalate now (see
        ``TestCompareExitZeroReportsCompatibleWithRisk`` for the JSON path
        that still does)."""
        outputs = self._summary(tmp_path, "| **Verdict** | \U0001f4a5 `BREAKING` |", 2)
        assert outputs["verdict"] == "API_BREAK", outputs

    def test_a_json_report_still_escalates_exit_two(self, tmp_path: Path) -> None:
        """Positive control for the half that stays: the same exit-2 run whose
        *structured* report says BREAKING still escalates, so the assertion
        above pins the removal of prose-scraping and not a loss of escalation
        itself."""
        report = {
            "report_schema_version": "2.49",
            "verdict": "BREAKING",
            "changes": [],
        }
        bindir = _stub_abicheck(tmp_path, exit_code=2, report=report)
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
        assert outputs["verdict"] == "BREAKING", outputs


class TestSarifIsNeverReadForAVerdict:
    """ADR-063 Track T8: ``format: sarif`` combined with an ``extra-args
    --write <non-json>=...`` suppresses the automatic JSON sidecar
    (``--write`` is a single-valued CLI option), leaving no abicheck-native
    JSON anywhere. ``_report_compat_verdict`` used to reach into the SARIF
    primary report's own ``runs[0].properties.abiVerdict`` for that case.
    That fallback is retired with the rest of the boundary's verdict
    reconstruction: SARIF is a rendering, not the structured
    ``run_outcome``/``verdict`` contract, and no reader consults it for a
    verdict any more.
    """

    def _sarif_report(self, verdict: str) -> dict:
        return {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {"driver": {"name": "abicheck"}},
                    "results": [],
                    "invocations": [{"executionSuccessful": True, "exitCode": 0}],
                    "properties": {"abiVerdict": verdict},
                }
            ],
        }

    def _run_sarif(self, tmp_path: Path, *, verdict: str, exit_code: int = 0) -> dict:
        bindir = _stub_abicheck(
            tmp_path, exit_code=exit_code, report=self._sarif_report(verdict)
        )
        extra_md = tmp_path / "extra.md"
        return _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "sarif",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.sarif"),
                # A non-JSON --write occupies the CLI's one --write slot,
                # which is exactly what suppresses the Action's own PR_JSON
                # injection (_extra_args_has_write_flag) -- reproducing the
                # reported combination rather than asserting the fixed
                # helpers in isolation.
                "INPUT_EXTRA_ARGS": f"--write markdown={extra_md}",
            },
            bindir,
        )

    @pytest.mark.parametrize(
        "sarif_verdict", ["COMPATIBLE_WITH_RISK", "BREAKING", "API_BREAK"]
    )
    def test_no_sarif_verdict_is_reconstructed_at_exit_zero(
        self, tmp_path: Path, sarif_verdict: str
    ) -> None:
        """Every verdict the retired SARIF fallback could recover -- the risk
        tier it was added for, and the two break tiers it escalated on -- now
        leaves the exit-0 dispatch's own COMPATIBLE untouched, because no
        structured abicheck-native JSON report stated a verdict for this
        run. Parametrized over all three rather than pinning the one
        originally reported, so the assertion covers the class."""
        outputs = self._run_sarif(tmp_path, verdict=sarif_verdict)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_sarif_primary_with_a_working_json_sidecar_is_unaffected(
        self, tmp_path: Path
    ) -> None:
        """The half that stays: when the automatic JSON sidecar is NOT
        suppressed (no conflicting extra-args --write), the real
        abicheck-native JSON report is read exactly as before. The two
        payloads deliberately disagree (SARIF says BREAKING, the native JSON
        sidecar says COMPATIBLE_WITH_RISK) so the assertion can only pass if
        the sidecar decided the outcome -- and, since T8, the SARIF document
        could not have decided it either way."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        sarif_payload = tmp_path / "sarif_payload.json"
        sarif_payload.write_text(
            json.dumps(self._sarif_report("BREAKING")), encoding="utf-8"
        )
        json_payload = tmp_path / "json_payload.json"
        json_payload.write_text(json.dumps(_risk_report()), encoding="utf-8")
        stub = bindir / "abicheck"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "prev=''\n"
            'for arg in "$@"; do\n'
            '  if [[ "$prev" == "-o" ]]; then\n'
            f'    cp "{sarif_payload}" "$arg"\n'
            "  fi\n"
            '  case "$arg" in\n'
            "    json=*)\n"
            f'      cp "{json_payload}" "${{arg#json=}}"\n'
            "      ;;\n"
            "  esac\n"
            '  prev="$arg"\n'
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "sarif",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.sarif"),
            },
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs


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
