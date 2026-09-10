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

"""Behavioral tests for ``action/run.sh``'s ``build-target`` forwarding
(P0.2, lab report follow-up).

``dump`` has long forwarded a ``build-target`` Action input as one or more
``--build-target`` flags. ``scan`` gained the identical CLI flag in this
same change (``scan_engine.run_scan_core``'s own ``build_targets``
parameter), but the Action itself needed a new input plus forwarding in
both mode branches to actually reach it -- otherwise a real workflow
setting ``build-target: //:math`` would scope ``dump``'s baseline but
leave ``scan``'s own L3 collection unscoped, silently reintroducing the
divergence P0.2 exists to close (mirrors the ``public-header-dir``
divergence this same lab report already found and fixed).

Extracts the full mode-branch region of ``run.sh`` verbatim -- same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_artifact_set.py`` -- and runs it with a harness that
sets the relevant ``INPUT_*`` env vars, capturing the resulting ``CMD``
array.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    script = (
        _mode_branches_region()
        + '\nprintf \'%s\\x1f\' ${CMD[@]+"${CMD[@]}"}\n'
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )
    return [item for item in result.stdout.split("\x1f") if item]


def _run_mode_branches(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the mode-branch region and return the raw result, so a rejected
    input's own nonzero exit and `::error::` line can be asserted (`_run_cmd`
    treats a nonzero exit as a harness failure)."""
    script = _mode_branches_region() + '\nprintf \'%s\\x1f\' ${CMD[@]+"${CMD[@]}"}\n'
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    env = dict(os.environ)
    env.update(env_extra)
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
    finally:
        os.unlink(script_path)


def _build_target_pairs(cmd: list[str]) -> list[str]:
    return [cmd[j + 1] for j, v in enumerate(cmd) if v == "--build-target"]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestDumpBuildTarget:
    def test_forwarded_as_build_target_flags(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "dump",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_BUILD_TARGET": "//:math //:util",
            }
        )
        assert "dump" in cmd
        assert _build_target_pairs(cmd) == ["//:math", "//:util"]

    def test_absent_forwards_none(self) -> None:
        cmd = _run_cmd({"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": "lib.so"})
        assert "--build-target" not in cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestScanBuildTargetIsRetired:
    """ADR-068's second 2026-09-09 amendment rules `scan --build-target` (b)
    -- retired; `dump --build-target` (above) is unchanged. The Action
    rejects the input for `mode: scan` with an explicit `::error::` instead
    of forwarding a flag the command no longer has."""

    def test_rejected_with_an_error_naming_dump(self) -> None:
        result = _run_mode_branches(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.json",
                "INPUT_BUILD_TARGET": "//:math",
            }
        )
        assert result.returncode != 0
        assert "no longer supports build-target" in result.stdout
        assert "mode: dump" in result.stdout

    def test_absent_forwards_none(self) -> None:
        cmd = _run_cmd(
            {
                "INPUT_MODE": "scan",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_AGAINST": "baseline.json",
            }
        )
        assert "--build-target" not in cmd
