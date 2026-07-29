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

"""Behavioral test: ``action/run.sh``'s scan-mode job summary renders the
artifact-set operand (ADR-056, G34; Codex review).

The job-summary block's ``scan`` branch always rendered ``INPUT_NEW_LIBRARY``
as the "Binary" property -- for a ``new-library-set`` run there is no
``INPUT_NEW_LIBRARY`` at all, so every ``--artifact-set`` Action run produced
a summary with an empty target, losing which directory/library list was
audited. This runs the real ``action/run.sh`` end-to-end against a fake
``abicheck`` stub on ``$PATH`` and inspects the rendered
``GITHUB_STEP_SUMMARY`` file.
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


def _run_scan_summary(env_extra: dict[str, str], tmp_path: Path) -> str:
    """Run the real run.sh in scan mode against a fake `abicheck` stub;
    returns the rendered GITHUB_STEP_SUMMARY content."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    abicheck_stub = fake_bin / "abicheck"
    abicheck_stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo \'{"scan_schema_version":"1.4","verdict":"COMPATIBLE","exit_code":0,"per_artifact":[]}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    abicheck_stub.chmod(0o755)

    github_output = tmp_path / "github_output"
    github_output.write_text("")
    github_step_summary = tmp_path / "github_step_summary"
    github_step_summary.write_text("")

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "scan",
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
    return github_step_summary.read_text(encoding="utf-8")


class TestScanJobSummaryArtifactSet:
    def test_artifact_set_renders_in_summary(self, tmp_path: Path) -> None:
        summary = _run_scan_summary(
            {"INPUT_NEW_LIBRARY_SET": "a.so,b.so"}, tmp_path
        )
        assert "Artifact set" in summary
        assert "a.so,b.so" in summary
        # The old always-Binary row must not render an empty target.
        assert "| Binary | `` |" not in summary

    def test_bare_new_library_still_renders_binary(self, tmp_path: Path) -> None:
        artifact = tmp_path / "new.json"
        artifact.write_text("{}", encoding="utf-8")
        summary = _run_scan_summary(
            {"INPUT_NEW_LIBRARY": str(artifact)}, tmp_path
        )
        assert "| Binary |" in summary
        assert str(artifact) in summary
        assert "Artifact set" not in summary
