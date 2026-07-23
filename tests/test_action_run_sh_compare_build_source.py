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

"""Behavioral test: ``action/run.sh``'s compare-mode branch forwards
build/source evidence (Codex review, PR #625).

``sources``/``build-info``/``compile-db``/``build-config``/``depth`` were
only ever forwarded to the CLI in ``dump``/``scan`` mode -- a ``compare``
mode invocation (the normal, non-audit path ``actions/check-target`` uses
for a ``--depth build``/``source`` check) silently dropped all five, so the
underlying ``compare`` CLI call never received the evidence needed to
actually reach that depth, regardless of what the report envelope later
claimed was requested. This runs the real ``action/run.sh`` end-to-end
(not the CLI itself, which is a fake shell stub on ``$PATH`` capturing its
own argv) to prove the fix reaches the real command line, not just that the
scripted intent looks right on paper.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"


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


def _run_compare(env_extra: dict[str, str], tmp_path: Path) -> str:
    """Run the real run.sh in compare mode against a fake `abicheck` on
    $PATH that records its own argv; returns the captured command line."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$*" >> "{captured}"\n'
        'echo \'{"verdict":"COMPATIBLE"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    abicheck_stub.chmod(0o755)

    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    old_json.write_text("{}", encoding="utf-8")
    new_json.write_text("{}", encoding="utf-8")

    github_output = tmp_path / "github_output"
    github_output.write_text("")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old_json),
        "INPUT_NEW_LIBRARY": str(new_json),
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(github_output),
        "GITHUB_STEP_SUMMARY": str(github_step_summary),
        **env_extra,
    }
    result = subprocess.run(
        [_bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").strip()


class TestCompareModeForwardsBuildSourceEvidence:
    def test_sources_and_depth_reach_the_cli(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_SOURCES": "/src", "INPUT_DEPTH": "source"}, tmp_path)
        assert "--sources new=/src" in cmd
        assert "--depth source" in cmd

    def test_build_info_reaches_the_cli_scoped_to_new_side(
        self, tmp_path: Path
    ) -> None:
        cmd = _run_compare({"INPUT_BUILD_INFO": "/build"}, tmp_path)
        assert "--build-info new=/build" in cmd

    def test_compile_db_falls_back_when_build_info_unset(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_COMPILE_DB": "/compile_commands.json"}, tmp_path)
        assert "--build-info new=/compile_commands.json" in cmd

    def test_build_info_takes_precedence_over_compile_db(self, tmp_path: Path) -> None:
        cmd = _run_compare(
            {
                "INPUT_BUILD_INFO": "/build",
                "INPUT_COMPILE_DB": "/compile_commands.json",
            },
            tmp_path,
        )
        assert "--build-info new=/build" in cmd
        assert "compile_commands.json" not in cmd

    def test_build_config_reaches_the_cli_as_config(self, tmp_path: Path) -> None:
        cmd = _run_compare({"INPUT_BUILD_CONFIG": "/cfg.yml"}, tmp_path)
        assert "--config /cfg.yml" in cmd

    def test_no_evidence_inputs_adds_no_flags(self, tmp_path: Path) -> None:
        cmd = _run_compare({}, tmp_path)
        assert "--sources" not in cmd
        assert "--build-info" not in cmd
        assert "--config" not in cmd
        assert "--depth" not in cmd
