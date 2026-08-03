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

"""The composite Action's mapping for ADR-049's contract-coverage exit.

Phase 7 gave exit ``1`` a third meaning. ``action/run.sh`` had no scan mapping
for it at all (so a coverage-gated scan published ``verdict=ERROR``, an
*operational* failure) and mapped it unconditionally to ``SEVERITY_ERROR`` on
compare (a *severity-policy* failure). The axis is neither: it deliberately
never rewrites the compatibility verdict or the gate decision, so publishing
either was a misreport a consumer branching on ``verdict`` acts on (Codex
review).

The script resolves ``abicheck`` from ``PATH``, so a stub binary is enough to
drive the real mapping end to end: it writes the report the script reads and
exits with the code under test. That is what makes these tests exercise
``run.sh`` itself rather than a restatement of its logic.
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

# POSIX only, and not for want of a real bash on Windows: the harness works by
# putting an *extensionless, shebang-dispatched* `abicheck` on PATH, because
# `run.sh` resolves the binary by name. Windows has neither the executable bit
# nor kernel shebang handling, and Git bash's `chmod` is a no-op on NTFS, so
# the stub is not runnable there. The mapping under test is plain shell with no
# platform-dependent behaviour, and the Linux lane exercises all of it.
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)


#: Sentinel for "the run wrote a report jq cannot parse" -- distinct from
#: ``None`` ("no meaningful report"), which still writes a valid ``{}``.
_MALFORMED = object()


def _stub_abicheck(
    tmp_path: Path,
    *,
    exit_code: int,
    report: dict | object | None,
    stderr: str = "",
    to_stdout: bool = False,
) -> Path:
    """A fake ``abicheck`` on PATH: writes *report* to ``-o``/``--secondary-output``.

    Mirrors what the real CLI does for the two things the mapping reads -- the
    JSON report and the stderr notice -- and nothing else. *to_stdout* is the
    documented ``format: json`` with no ``output-file`` mode, where the report
    is only ever on stdout.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(
        "not json" if report is _MALFORMED else json.dumps(report or {}),
        encoding="utf-8",
    )
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "-o" || "$prev" == "--secondary-output" ]]; then\n'
        f'    cp "{payload}" "$arg"\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        + (f'cat "{payload}"\n' if to_stdout else "")
        + (f'printf "%s\\n" {json.dumps(stderr)} >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
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
    # Its own directory so a test can assert the run left no scratch files
    # behind, without seeing another test's (or the system's) temp files.
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
    outputs["_summary"] = summary.read_text(encoding="utf-8")
    outputs["_runner_temp"] = str(runner_temp)
    return outputs


def _lib(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"\x7fELF")
    return str(path)


COVERAGE_FAILURE = {
    "provider": "export_table",
    "side": "old",
    "record_id": "old/export_table",
    "reason": "search_incomplete",
    "status": "unavailable",
    "completeness": "none",
    "mode": "exports",
    "suppressible": False,
}


def _report(*, coverage: int, severity_exit: int) -> dict:
    return {
        "report_schema_version": "2.26",
        "verdict": "COMPATIBLE",
        "contract_coverage_exit_contribution": coverage,
        "contract_coverage_failures": [COVERAGE_FAILURE] if coverage else [],
        "severity": {
            "config": {},
            "categories": {},
            "exit_code": severity_exit,
            "blocking": severity_exit != 0,
            "blocking_categories": ["addition"] if severity_exit else [],
        },
    }


class TestScanMapsTheCoverageExit:
    """`scan`'s own verdict codes are 0/2/4/5, so exit 1 could only ever have
    been the coverage axis -- but it fell through to the catch-all and
    published ERROR."""

    def test_a_coverage_gated_scan_is_not_an_operational_error(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=_report(coverage=1, severity_exit=0),
            stderr=(
                "Contract coverage incomplete for the selected --contract "
                "domain: old/export_table. Exit code floored to 1."
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
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs
        assert outputs["exit-code"] == "1", outputs
        assert "COVERAGE_INCOMPLETE" in outputs["_summary"], outputs["_summary"]
        # The provider is named, since "old/export_table" is the actionable
        # part and a bare "coverage incomplete" is not.
        assert "old/export_table" in outputs["_summary"], outputs["_summary"]

    def test_the_scan_report_nests_the_ledger_under_diff(
        self, tmp_path: Path
    ) -> None:
        """`ScanOutcome.to_dict()` puts the comparison summary under `diff`,
        so the ledger is not where a compare report keeps it.

        With `format: json` the stderr notice is suppressed too -- the report
        carries the ledger, so the CLI does not repeat it -- leaving the
        mapping with neither signal and publishing ERROR (Codex review).
        """
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "diff": {
                    "contract_coverage_exit_contribution": 1,
                    "contract_coverage_failures": [COVERAGE_FAILURE],
                },
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
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs
        # ...and the summary names the provider from the same nested place.
        assert "old/export_table" in outputs["_summary"], outputs["_summary"]

    def test_a_crash_still_maps_to_error(self, tmp_path: Path) -> None:
        """A traceback also exits 1. Claiming the coverage axis for any exit 1
        would turn every scan crash into a reassuring warning, so the mapping
        asks the run whether coverage actually contributed."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=None,
            stderr="Traceback (most recent call last):\n  boom",
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


class TestCompareTellsTheTwoAxesApart:
    """`compare` genuinely shares exit 1 between the severity gate and the
    coverage axis, so the mapping reads the report's pre-fold
    `severity.exit_code` rather than guessing from the code alone."""

    def _compare_outputs(self, tmp_path: Path, report: dict) -> dict:
        bindir = _stub_abicheck(tmp_path, exit_code=1, report=report)
        return _run_action(
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

    def test_coverage_alone_is_not_a_severity_failure(self, tmp_path: Path) -> None:
        outputs = self._compare_outputs(
            tmp_path, _report(coverage=1, severity_exit=0)
        )
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs

    def test_a_severity_gate_at_one_keeps_its_own_verdict(
        self, tmp_path: Path
    ) -> None:
        """Both axes produced 1 and `max` cannot tell them apart. The severity
        gate is the one that was configured to block, so relabelling the run as
        a coverage problem would hide it -- the coverage contribution is
        reported alongside instead."""
        outputs = self._compare_outputs(
            tmp_path, _report(coverage=1, severity_exit=1)
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs

    def test_the_default_legacy_scheme_has_no_severity_block_to_read(
        self, tmp_path: Path
    ) -> None:
        """The legacy scheme is the *default*, and its report carries no
        `severity` block at all -- `cli_compare_helpers` passes
        `severity_config` only when the resolved scheme is `severity`.

        Treating the missing block as "cannot tell" classified every
        coverage-gated run under the default scheme as SEVERITY_ERROR (Codex
        review). Absence is an answer here: legacy compare exits 0/2/4 and
        never 1, so the compatibility axis contributed 0 by construction.
        """
        outputs = self._compare_outputs(
            tmp_path,
            {
                "report_schema_version": "2.26",
                "verdict": "COMPATIBLE",
                "contract_coverage_exit_contribution": 1,
                "contract_coverage_failures": [COVERAGE_FAILURE],
            },
        )
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs

    def test_an_unparseable_report_keeps_the_established_verdict(
        self, tmp_path: Path
    ) -> None:
        """The genuine "cannot tell": jq exits non-zero and prints nothing, so
        the mapping must not read that as a zero severity contribution. The
        stderr notice is what still identifies the axis here."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=_MALFORMED,
            stderr=(
                "Contract coverage incomplete for the selected --contract "
                "domain: old/export_table. Exit code floored to 1."
            ),
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "markdown",
            },
            bindir,
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs

    def test_the_report_is_found_when_json_goes_to_stdout(
        self, tmp_path: Path
    ) -> None:
        """`format: json` with no `output-file` is the documented stdout mode:
        there is no file anywhere, and the JSON renderer deliberately prints no
        stderr notice because its report carries the ledger. Both of the
        mapping's signals were therefore silent for that one configuration, and
        it fell back to SEVERITY_ERROR (Codex review).
        """
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=_report(coverage=1, severity_exit=0),
            to_stdout=True,
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
            },
            bindir,
        )
        assert outputs["verdict"] == "COVERAGE_INCOMPLETE", outputs
        # ...and it left nothing behind. The persisted copy is created in the
        # parent shell so the EXIT trap can see it: creating it lazily inside
        # `_json_report_src` put the memo in a command-substitution subshell,
        # so each of the three lookups minted its own copy and the trap had an
        # empty path to clean up -- a full report leaked per lookup on a
        # persistent self-hosted runner (Codex review).
        leaked = list(Path(outputs["_runner_temp"]).glob("abicheck-stdout-json.*"))
        assert leaked == [], leaked

    def test_a_run_without_contract_evaluation_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        """No `--contract-evaluation` means no ledger keys at all, which must
        read as a contribution of 0 rather than as an unanswered question."""
        outputs = self._compare_outputs(
            tmp_path,
            {
                "report_schema_version": "2.26",
                "verdict": "COMPATIBLE",
                "severity": {
                    "config": {},
                    "categories": {},
                    "exit_code": 1,
                    "blocking": True,
                    "blocking_categories": ["addition"],
                },
            },
        )
        assert outputs["verdict"] == "SEVERITY_ERROR", outputs
