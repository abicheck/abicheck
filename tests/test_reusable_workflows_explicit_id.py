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

"""G42 "Explicit check identifiers" coverage for ``check-project.yml``'s
"Synthesize pre-check operational-error report" inline script -- split out
of ``test_reusable_workflows.py`` (that file carries a ``no_growth``
debt-baseline entry, per this repo's own ``file-size`` gate convention:
grow via a new sibling test file, not by extending the file at its
baseline).

Mirrors ``TestPreCheckOperationalErrorReport.
test_precheck_script_writes_a_valid_operational_error_report_end_to_end``
in that file (which this test file's fixtures deliberately match), but
asserts the positive case: ``matrix.explicit_id`` actually reaches this
script's own ``report_envelope.py`` invocation and qualifies the emitted
``check-id``. See ``test_action_check_target_explicit_id.py`` for the
sibling coverage on the ``actions/check-target/run.sh`` path this script
runs *instead of* (issue #628: exactly one of the two ever runs per matrix
cell).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_reusable_workflows import CHECK_PROJECT, _load, _steps


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "This step runs under the Actions runner's own `shell: bash` -- "
        "this test exercises that real POSIX bash behavior, which a "
        "Windows test runner cannot provide."
    ),
)
def test_precheck_script_threads_explicit_id_into_check_id(tmp_path: Path) -> None:
    data = _load(CHECK_PROJECT)
    steps = _steps(data["jobs"]["check"])
    precheck = next(
        s
        for s in steps
        if s.get("name") == "Synthesize pre-check operational-error report"
    )
    script = precheck["run"]

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = tmp_path / ".check-project-src"
    src_dir.mkdir()
    (src_dir / "actions").symlink_to(repo_root / "actions", target_is_directory=True)

    github_output = tmp_path / "github_output"
    github_output.write_text("")
    env = {
        **os.environ,
        "MATRIX_NAME": "libfoo",
        "MATRIX_PROFILE_ID": "linux-x86_64",
        "MATRIX_BASELINE_CHANNEL": "accepted-main",
        "MATRIX_REQUESTED_DEPTH": "headers",
        "MATRIX_EXPLICIT_ID": "l4-plugin",
        "MATRIX_GATE_MODE": "deferred",
        "PROJECT": "abicheck/abicheck",
        "HEAD_SHA": "deadbeef",
        "BASE_REF": "main",
        "ACTION_VERSION": "abicheck/abicheck@main",
        "BUILD_OUTPUT_FAILED": "false",
        "GITHUB_OUTPUT": str(github_output),
    }
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr

    output_lines = dict(
        line.split("=", 1)
        for line in github_output.read_text().splitlines()
        if "=" in line
    )
    assert output_lines["check-id"] == (
        "libfoo@linux-x86_64#accepted-main@headers~l4-plugin"
    )

    report = json.loads((tmp_path / "precheck-report.json").read_text())
    assert report["check_id"] == output_lines["check-id"]
    assert report["target_id"] == output_lines["check-id"]
