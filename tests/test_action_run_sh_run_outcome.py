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

"""ADR-063 Track 4 / 7B: ``action/run.sh`` reads the report's own
``run_outcome`` block (ADR-063 Phase 7 / D6) as the *primary* source for its
compatibility-verdict and severity-gate resolution, instead of purely
re-deriving those facts from the legacy ``verdict``/``severity.exit_code``
fields -- ``_report_compat_verdict``/``_severity_gate_exit`` now consult
``run_outcome.compatibility``/``run_outcome.gate`` first, falling back to the
pre-existing fields only when no ``run_outcome`` block is present (an older
abicheck, or no readable JSON at all).

Each test below gives the report a ``run_outcome`` value that *disagrees*
with the legacy field it's paired with -- proving the script actually reads
``run_outcome`` rather than merely falling through to the unchanged legacy
path by coincidence (every legacy-field test elsewhere in this suite already
covers the "no run_outcome block" fallback).
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


class TestCompareExitZeroPrefersRunOutcomeCompatibility:
    def test_run_outcome_wins_over_a_disagreeing_legacy_verdict_field(
        self, tmp_path: Path
    ) -> None:
        report = {
            "report_schema_version": "2.49",
            # Deliberately disagrees with run_outcome below -- if the script
            # fell back to this legacy field, it would publish COMPATIBLE.
            "verdict": "COMPATIBLE",
            "changes": [],
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": "COMPATIBLE_WITH_RISK",
                "assurance": None,
                "gate": "none",
                "operational": "none",
                "lifecycle": "existing",
            },
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
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs
        assert outputs["_exit"] == 0, outputs

    def test_run_outcome_breaking_is_reported_at_exit_zero(
        self, tmp_path: Path
    ) -> None:
        """A severity policy demoting a real break to exit 0 must still
        surface the break -- same ADVISORY_BREAK shape as the legacy
        verdict-field path, sourced from run_outcome instead."""
        report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": "BREAKING",
                "assurance": None,
                "gate": "none",
                "operational": "none",
                "lifecycle": "existing",
            },
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
        assert outputs["verdict"] == "BREAKING", outputs
        # ADVISORY_BREAK: never gates the step (fail-on-breaking never
        # matches this path), same as the legacy-field equivalent.
        assert outputs["_exit"] == 0, outputs


class TestSeverityGateExitPrefersRunOutcomeGate:
    """`_severity_gate_exit` (consulted at exit 1, to tell a severity-policy
    failure apart from the orthogonal coverage/assurance axes) now reads
    `run_outcome.gate` first."""

    def test_addition_quality_gate_is_reported_as_severity_error(
        self, tmp_path: Path
    ) -> None:
        report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
            # No `severity` block at all -- the pre-existing `severity_exit`
            # field/text fallback would answer "0" (legacy scheme), so a
            # SEVERITY_ERROR verdict here can only come from run_outcome.gate.
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": "COMPATIBLE",
                "assurance": None,
                "gate": "addition_quality",
                "operational": "none",
                "lifecycle": "existing",
            },
        }
        bindir = _stub_abicheck(tmp_path, exit_code=1, report=report)
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

    def test_none_gate_leaves_room_for_the_orthogonal_coverage_axis(
        self, tmp_path: Path
    ) -> None:
        """`run_outcome.gate: none` must not itself claim exit 1 -- the
        orthogonal contract-coverage axis (still read from its own field,
        unaffected by this change) is what actually produced it here."""
        report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
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
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": "COMPATIBLE",
                "assurance": None,
                "gate": "none",
                "operational": "none",
                "lifecycle": "existing",
            },
        }
        bindir = _stub_abicheck(tmp_path, exit_code=1, report=report)
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


class TestReportCompatVerdictPreservesOperationalFailures:
    """`_report_compat_verdict` must not launder a real operational failure
    into a plain compatibility break (Codex review, fresh evidence).

    A directory/package release can legitimately carry BOTH axes at once: one
    library's real `BREAKING` result (`run_outcome.compatibility`) alongside a
    *different* library's failed extraction (`run_outcome.operational:
    extraction_error`) -- with the release's own top-level `verdict` sentinel
    (`"ERROR"`) recording exactly that combination
    (`policy.outcome.run_outcome_dict_for_release`'s own docstring:
    `compatibility` is deliberately never the release's reported sentinel).
    Reading `run_outcome.compatibility` unconditionally would let
    `_escalate_verdict_to_report` promote the Action's published verdict to
    `BREAKING` and claim the severity policy produced this run's exit, hiding
    that a library never finished comparing at all.
    """

    def test_an_operational_failure_is_not_escalated_to_a_compatibility_break(
        self, tmp_path: Path
    ) -> None:
        report = {
            "report_schema_version": "2.49",
            # The release's own reported sentinel -- an operational failure,
            # not a compatibility result. If `_report_compat_verdict` read
            # `run_outcome.compatibility` here, it would see "BREAKING"
            # instead and escalate to it, masking this sentinel entirely.
            "verdict": "ERROR",
            "changes": [],
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": "BREAKING",
                "assurance": None,
                "gate": "none",
                "operational": "extraction_error",
                "lifecycle": "existing",
            },
        }
        bindir = _stub_abicheck(tmp_path, exit_code=1, report=report)
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
        assert outputs["verdict"] != "BREAKING", outputs
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs

    def test_a_none_operational_axis_still_lets_compatibility_escalate(
        self, tmp_path: Path
    ) -> None:
        """The contrast case: with `operational: none`, a real `BREAKING`
        result must still escalate exactly as before -- this fix narrows the
        trust condition, it doesn't remove escalation altogether."""
        report = {
            "report_schema_version": "2.49",
            "verdict": "COMPATIBLE",
            "changes": [],
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": "BREAKING",
                "assurance": None,
                "gate": "none",
                "operational": "none",
                "lifecycle": "existing",
            },
        }
        bindir = _stub_abicheck(tmp_path, exit_code=1, report=report)
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
