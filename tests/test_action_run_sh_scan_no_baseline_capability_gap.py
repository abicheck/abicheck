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

"""Codex review (PR #1210) found three real regressions in the audit-only
``mode: scan`` -> ``compare --no-baseline`` migration, each a capability an
existing legacy-``scan``-CLI-backed audit-only workflow relied on:

- ``since``/``changed-path`` used to be forwarded and *worked* for an
  audit-only scan (legacy ``scan``'s own audit mode genuinely supports
  revision-range evidence scoping with no baseline). The migration forwarded
  them unconditionally to ``compare --no-baseline``, which hard-rejects both
  as a usage error (ADR-068 D2) -- turning a previously-working workflow
  into an exit-64 failure.
- ``budget`` was dropped from the merged command assembly entirely (no
  ``INPUT_BUDGET`` reference existed anywhere in ``run.sh`` after the
  merge), silently removing the wall-clock guard from *every* ``mode:
  scan`` job that set it -- baseline scans included, not just audit-only
  ones.
- ``require-complete-analysis`` was forwarded unconditionally too, but
  ``action.yml`` has always documented it as having "no effect" on an
  audit-only scan (legacy ``scan``'s own CLI rejected the flag without a
  baseline, so ``run.sh`` never forwarded it in that shape) -- ``compare
  --no-baseline`` accepts the flag and gives it real teeth, so a caller
  who set ``require-complete-analysis: true`` uniformly across baseline and
  audit-only jobs, trusting the documented no-op, now gets new, undocumented
  red CI on the audit-only ones.

This module locks down the fix: an audit-only scan (no ``against``) that
sets ``since``/``changed-path``/``budget`` is rejected upfront with a clear
``::error::`` (the same "reject, don't silently narrow" treatment as the
four hard-retired inputs in ``TestRetiredScanInputsAreCheckedBeforeRouteSelection``,
``test_action_run_contract.py``) instead of reaching ``compare``'s own
usage error deep in the run; a baseline scan (``against`` set) still
forwards all three, plus ``require-complete-analysis``, exactly as before;
and an audit-only scan that does *not* set ``require-complete-analysis``
is completely unaffected (the common case).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

RUN_SH = _REPO / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'
_REAL_ABICHECK = shutil.which("abicheck")

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or _REAL_ABICHECK is None,
    reason="needs a POSIX shell, a real abicheck on PATH, and action/run.sh",
)

_NON_GATING_CASE = "case143_audit_accidental_export"


def _snapshot_path(case_name: str) -> Path:
    path = example_catalog.case_dir(case_name) / "snapshot.abi.json"
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


def _run_action(tmp_path: Path, env_extra: dict[str, str]) -> dict[str, object]:
    """Same harness as ``test_action_run_sh_audit_gate.py``: runs the real
    ``action/run.sh`` end to end and returns its ``GITHUB_OUTPUT`` pairs
    plus the raw process result. Tolerates a nonzero exit (the whole point
    of the rejection tests below)."""
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("", encoding="utf-8")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "INPUT_MODE": "scan",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        "RUNNER_TEMP": str(runner_temp),
        **env_extra,
    }
    proc = subprocess.run(
        ["bash", str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    outputs: dict[str, object] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    outputs["_returncode"] = proc.returncode
    outputs["_stdout"] = proc.stdout
    outputs["_stderr"] = proc.stderr
    return outputs


class TestAuditOnlyScanRejectsSinceChangedPathBudgetUpfront:
    """An audit-only scan (no ``against``) setting ``since``/
    ``changed-path``/``budget`` fails fast with a clear, specific
    ``::error::`` naming the actual gap -- never a bare Click usage error
    surfaced deep in the run, and never a silent drop."""

    def test_since_is_rejected(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_SINCE": "origin/main",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support since" in outputs["_stdout"], outputs
        assert "against:" in outputs["_stdout"], outputs

    def test_changed_path_is_rejected(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_CHANGED_PATH": "src/foo.c",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support changed-path" in outputs["_stdout"], outputs

    def test_budget_is_rejected(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_BUDGET": "15m",
            },
        )
        assert outputs["_returncode"] == 1, outputs
        assert "does not support budget" in outputs["_stdout"], outputs


class TestAuditOnlyScanUnaffectedWhenTheseInputsAreUnset:
    """The common case -- an audit-only scan that never touches since/
    changed-path/budget/require-complete-analysis -- is unaffected by any
    of the guards above: it still reaches `compare --no-baseline` and
    completes normally."""

    def test_plain_audit_only_scan_still_succeeds(self, tmp_path: Path) -> None:
        outputs = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        assert outputs["_returncode"] == 0, outputs


class TestAuditOnlyScanRequireCompleteAnalysisHasNoEffect:
    """``action.yml`` has always documented ``require-complete-analysis`` as
    having "no effect" on an audit-only scan. Before this fix, ``compare
    --no-baseline`` silently gained real teeth for it (an incomplete
    analysis-assurance candidate-side finding now fails the step) --
    verified directly against the real CLI in this PR's own investigation.
    This test proves the *documented* contract holds again: the flag is
    simply never forwarded for an audit-only shape, so this run's outcome
    is identical with or without it."""

    def test_require_complete_analysis_does_not_change_the_outcome(
        self, tmp_path: Path
    ) -> None:
        without_flag = _run_action(
            tmp_path,
            {"INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE))},
        )
        with_flag = _run_action(
            tmp_path,
            {
                "INPUT_NEW_LIBRARY": str(_snapshot_path(_NON_GATING_CASE)),
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
            },
        )
        assert without_flag["_returncode"] == 0, without_flag
        assert with_flag["_returncode"] == 0, with_flag
        assert with_flag.get("verdict") == without_flag.get("verdict")


# --- Static CMD-assembly checks for the baseline-scan shape ---------------
#
# A real baseline-scan end-to-end run needs two real artifacts (a baseline
# and a candidate) rather than one committed snapshot fixture; the static
# harness below (mirrors `test_action_run_sh_scan_routing_edge_cases.py`'s
# own `_run_cmd`) asserts directly on the assembled `CMD` array instead,
# which needs no real binaries at all.


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


_CMD_MARKER = "__ABICHECK_TEST_CMD_START__"


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    script = (
        _mode_branches_region()
        + f"\nprintf '%s' '{_CMD_MARKER}'"
        + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            ["bash", script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    marker_index = result.stdout.rfind(_CMD_MARKER)
    assert marker_index != -1, result.stdout
    payload = result.stdout[marker_index + len(_CMD_MARKER) :]
    return [item for item in payload.split("\x1f") if item]


_BASELINE_INPUTS = {
    "INPUT_MODE": "scan",
    "INPUT_NEW_LIBRARY": "lib.so",
    "INPUT_AGAINST": "baseline.so",
}


class TestBaselineScanStillForwardsSinceChangedPathBudget:
    def test_since_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_SINCE": "origin/main"})
        assert "--since" in cmd, cmd
        assert "origin/main" in cmd, cmd

    def test_changed_path_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_CHANGED_PATH": "src/foo.c"})
        assert "--changed-path" in cmd, cmd
        assert "src/foo.c" in cmd, cmd

    def test_budget_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_BUDGET": "15m"})
        assert "--budget" in cmd, cmd
        assert "15m" in cmd, cmd

    def test_require_complete_analysis_reaches_compare(self) -> None:
        cmd = _run_cmd({**_BASELINE_INPUTS, "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true"})
        assert "--require-complete-analysis" in cmd, cmd


class TestAuditOnlyScanNeverForwardsRequireCompleteAnalysis:
    def test_flag_is_absent_from_cmd_even_when_input_is_true(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
            }
        )
        assert "--require-complete-analysis" not in cmd, cmd


class TestAuditOnlyScanNeverForwardsBudget:
    def test_budget_flag_is_absent_from_cmd(self) -> None:
        # The early-rejection preflight (tested via the real end-to-end
        # harness above) means an audit-only run reaching this point never
        # has INPUT_BUDGET set -- but the command-assembly branch itself
        # must still never emit --budget unconditionally either, since the
        # static harness here bypasses that preflight (it starts partway
        # through the file, at the mode-branches region).
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
            }
        )
        assert "--budget" not in cmd, cmd
