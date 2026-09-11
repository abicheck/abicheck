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
window).

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

    def test_scan_mode_fails_the_step(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, report={"verdict": "COMPATIBLE"})
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_AGAINST": _lib(tmp_path, "libold.so"),
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
