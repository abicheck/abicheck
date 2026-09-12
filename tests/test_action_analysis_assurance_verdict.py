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

"""The composite Action's now-retired ``require-complete-analysis`` input
(rulings.py deferred-option followup -- hard removal, no deprecation
window), plus the P1 fix that followup left behind (Codex review, fresh
evidence on the retirement PR itself).

This file used to exercise the Action's dedicated ``require-complete-
analysis`` boolean input, which mapped P0.4's orthogonal analysis-assurance
exit-1 floor onto a labeled ``ANALYSIS_INCOMPLETE`` verdict (see
``docs/reference/exit-codes.md``, ``abicheck/analysis_assurance.py``). That
whole mechanism is gone: the CLI's own ``compare --require-complete-
analysis`` flag it mirrored was demoted to a config-only
``.abicheck.yml`` ``assurance.require_complete: true`` setting with no CLI
or Action-input override (ADR-068 D5 guard #2, "no escape hatch"), so the
Action's own dedicated input has nothing left to forward to and is retired
the same way (``action.yml``'s own description, ``action/validate-
inputs.sh``/``action/run.sh``'s tombstone rejections).

``tests/test_action_validate_inputs.py``'s
``TestRemovedInputTombstones.test_require_complete_analysis_is_a_hard_error``
covers the preflight rejection (the loud, fast path every workflow actually
hits); this file's remaining job is the ``action/run.sh``-level defense in
depth for anyone invoking it directly, mirroring
``TestRemovedConfigDuplicates``-shaped retirement tests elsewhere in this
suite rather than the removed feature's own behavior.

**The P1 bug and its fix.** The retirement PR's first cut left
``run.sh``'s own ``_assurance_gated()`` still keyed off
``INPUT_REQUIRE_COMPLETE_ANALYSIS`` to decide whether the belt-and-suspenders
unconditional exit-1 floor (the block right below "P0.4's analysis-assurance
axis, unconditional exactly like the contract-coverage check immediately
above") should fire at all -- but ``validate-inputs.sh`` now hard-rejects any
non-``false`` value for that input before this step can ever run, so the
env var can never again read ``true``, and the belt-and-suspenders check
became permanently dead for exactly the case it exists to catch: a
config-driven ``assurance.require_complete: true`` (the CLI's only
remaining source) coinciding with a compatibility verdict the caller chose
not to gate on (``fail-on-breaking: false``/default-false
``fail-on-api-break``). The fix makes ``_assurance_gated()`` read the
report's own self-describing ``analysis_assurance_exit_contribution`` field
instead (mirroring ``_coverage_gated()``'s pre-existing JSON-only rule) --
this class of test proves the gate now fires from that field alone, with
``INPUT_REQUIRE_COMPLETE_ANALYSIS`` never set at all.

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
from _workflow_exec import bash_executable, require_bash

ACTION_DIR = Path(__file__).resolve().parent.parent / "action"
RUN_SH = ACTION_DIR / "run.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not RUN_SH.is_file() or shutil.which("bash") is None,
    reason="needs a POSIX shell that can exec a shebang script from PATH",
)


def _stub_abicheck(tmp_path: Path, *, exit_code: int, report: dict | None) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(report or {}), encoding="utf-8")
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        # `-o` carries FORMAT=DESTINATION (plan slice 7m); a `-`
        # destination is stdout and writes no file.
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


def _run_action(tmp_path: Path, env_extra: dict[str, str], bindir: Path) -> dict:
    require_bash()
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
        [bash_executable(), str(RUN_SH)],
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


class TestRunShRejectsTheRetiredInputDirectly:
    """Defense in depth: ``action/validate-inputs.sh`` is the loud, fast
    preflight path every real workflow invocation hits first, but
    ``run.sh`` carries its own copy of the same tombstone rejection for
    anyone invoking it directly (this file's own harness included)."""

    def test_compare_mode_fails_the_step(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, report={"verdict": "COMPATIBLE"})
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs
        assert "require-complete-analysis" in outputs["_stdout"], outputs["_stdout"]
        assert "assurance.require_complete" in outputs["_stdout"], outputs["_stdout"]

    def test_no_baseline_compare_mode_fails_the_step(self, tmp_path: Path) -> None:
        """mode: scan itself is retired outright (ADR-068) -- its own
        replacement, an audit-only ``compare --no-baseline`` (old-library/
        abi-baseline both omitted), must reject the retired input the same
        unconditional way the two-sided shape above does."""
        bindir = _stub_abicheck(tmp_path, exit_code=0, report={"verdict": "COMPATIBLE"})
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "true",
            },
            bindir,
        )
        assert outputs["_exit"] == 1, outputs
        assert "require-complete-analysis" in outputs["_stdout"], outputs["_stdout"]
        assert "assurance.require_complete" in outputs["_stdout"], outputs["_stdout"]

    def test_a_run_without_the_input_is_unaffected(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path, exit_code=0, report={"verdict": "COMPATIBLE", "exit_code": 0}
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
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_an_explicit_false_is_not_an_error(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path, exit_code=0, report={"verdict": "COMPATIBLE", "exit_code": 0}
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
                "INPUT_REQUIRE_COMPLETE_ANALYSIS": "false",
            },
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs


class TestConfigDrivenAssuranceGateReadsTheReport:
    """The P1 fix: ``_assurance_gated()`` reads
    ``analysis_assurance_exit_contribution`` from the JSON report -- the
    only way a config-only ``assurance.require_complete: true`` (no CLI
    flag, no Action input) can be observed at all -- rather than the
    permanently-``false`` ``INPUT_REQUIRE_COMPLETE_ANALYSIS`` env var.
    ``INPUT_REQUIRE_COMPLETE_ANALYSIS`` is never set in any test below.
    """

    def _report(self, *, contribution: int, verdict: str) -> dict:
        return {
            "report_schema_version": "2.40",
            "verdict": verdict,
            "analysis_assurance": {
                "status": "incomplete" if contribution else "complete"
            },
            "analysis_assurance_exit_contribution": contribution,
        }

    def test_gate_fires_independent_of_fail_on_breaking(self, tmp_path: Path) -> None:
        """The exact P1 scenario: a config-driven assurance floor coincides
        with an ABI break the caller chose not to gate on
        (`fail-on-breaking: false`) -- before the fix, this silently passed
        because `_assurance_gated()` could never observe `true` any more."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=4,
            report=self._report(contribution=1, verdict="BREAKING"),
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
            },
            bindir,
        )
        assert outputs["_exit"] != 0, outputs
        assert "assurance.require_complete" in outputs["_stdout"], outputs["_stdout"]

    def test_gate_fires_with_fail_on_api_break_false_too(self, tmp_path: Path) -> None:
        """`fail-on-api-break` already defaults to false -- proves the gate
        does not depend on that default happening to be true either."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=2,
            report=self._report(contribution=1, verdict="API_BREAK"),
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
        assert outputs["_exit"] != 0, outputs
        assert "assurance.require_complete" in outputs["_stdout"], outputs["_stdout"]

    def test_a_zero_contribution_is_not_gated(self, tmp_path: Path) -> None:
        """No false positive: complete assurance alongside a break the
        caller chose not to gate on stays a clean step."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=4,
            report=self._report(contribution=0, verdict="BREAKING"),
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
            },
            bindir,
        )
        assert outputs["_exit"] == 0, outputs

    def test_the_nested_diff_shape_is_read_too(self, tmp_path: Path) -> None:
        """`_either()` in `_report_query` reads this field from the document
        root, else falls back to a `diff`-nested copy -- a shape legacy
        `scan --against` reports used before ADR-068 retired that mode
        outright, kept here as a direct test of the query helper's own
        fallback robustness (`test_action_coverage_verdict.py`'s
        `_scan_outputs()` tests its `contract_coverage_exit_contribution`
        sibling the identical way) rather than a claim that a real
        `mode: compare` invocation produces this exact document today. The
        assurance check itself is unconditional (mirrors `_coverage_gated()`
        immediately above it) and reads `_assurance_gated()` directly rather
        than the published VERDICT label -- a real BREAKING verdict outranks
        ANALYSIS_INCOMPLETE in `_escalate_verdict_to_report`'s severity
        ordering and wins the label, so this asserts the exit/message the
        unconditional check itself produces, not the (by-design, escalated)
        verdict label."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report={
                "diff": self._report(contribution=1, verdict="BREAKING"),
            },
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
        assert outputs["_exit"] != 0, outputs
        assert "assurance.require_complete" in outputs["_stdout"], outputs["_stdout"]


class TestConfigDrivenAssuranceGateReadsTheNoBaselineShapeToo:
    """Finding 3 (P2, PR #1222 Codex review): ``_assurance_gated()`` (fixed
    above to read ``analysis_assurance_exit_contribution``) didn't account
    for the DIFFERENT report shape a ``compare --no-baseline`` audit-only
    run produces (the real replacement for legacy ``mode: scan`` with no
    baseline, ADR-068) -- that document
    (``report/no_baseline.py::_document_json``) has no top-level
    ``analysis_assurance_exit_contribution`` key at all (unlike the
    two-sided ``compare`` shape) and no ``diff`` wrapper either (unlike the
    legacy nested shape ``test_the_nested_diff_shape_is_read_too`` above
    covers) -- the same information lives under
    ``exit_axes.analysis_assurance`` instead. Before the fix, an audit-only
    run with ``assurance.require_complete: true`` read a missing key here,
    silently answered "not gated", and this Action reported a plain ERROR
    instead of the correct ANALYSIS_INCOMPLETE classification even though
    the CLI itself correctly exited 1. ``INPUT_OLD_LIBRARY`` is
    deliberately never set below -- that omission is what selects the
    ``compare --no-baseline`` audit path."""

    def _no_baseline_report(self, *, contribution: int) -> dict:
        return {
            "audit_report_schema_version": "1.0",
            "no_baseline": True,
            "verdict": None,
            "findings": [],
            # `report/no_baseline.py` emits `suppressed_findings` unconditionally
            # beside `findings`, and the reader now requires both: an absent
            # `suppressed_findings` cannot establish that policy suppressed
            # nothing, so a half-present pair is not a result (ADR-067).
            "suppressed_findings": [],
            "exit_axes": {"analysis_assurance": contribution, "audit_gate": 0},
            "exit_code": 1 if contribution else 0,
        }

    def test_the_exit_axes_shape_gates_too(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=1,
            report=self._no_baseline_report(contribution=1),
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["_exit"] != 0, outputs
        assert "assurance.require_complete" in outputs["_stdout"], outputs["_stdout"]

    def test_a_zero_exit_axes_contribution_is_not_gated(self, tmp_path: Path) -> None:
        """No false positive: the exit_axes fallback must not fire on a
        real, explicit 0 either."""
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            report=self._no_baseline_report(contribution=0),
        )
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "json",
                "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
            },
            bindir,
        )
        assert outputs["_exit"] == 0, outputs
