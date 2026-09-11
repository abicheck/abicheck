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

"""Shared execution helpers for ``actions/check-target``'s shell-layer
tests (``validate-inputs.sh``/``run.sh``), split out of
``tests/test_action_check_target.py`` purely to keep that file under its
``architecture/debt.yaml`` ``no_growth`` baseline (PR #1222) -- the same
"move responsibility, don't trim to fit" convention the root ``AGENTS.md``
states, mirroring how ``tests/_workflow_exec.py`` already holds the shared
execution machinery for the reusable-workflow tests rather than each
consumer keeping its own copy.

Not a `test_*` module itself: pytest never collects this file, and every
consumer (``test_action_check_target.py`` and its own siblings, e.g.
``test_action_check_target_assurance_validation.py``) imports these names
directly rather than redefining them. Names are unchanged from their
original home so every pre-existing call site keeps working unmodified.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "check-target"
RUN_SH = ACTION_DIR / "run.sh"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"

PROFILE = "linux-x86_64-gcc13-release"

_BASE_IDENTITY = {
    "INPUT_NAME": "libpvxs",
    "INPUT_PROFILE": PROFILE,
    "INPUT_BASELINE_CHANNEL": "accepted-main",
    "INPUT_REQUESTED_DEPTH": "headers",
    "INPUT_GATE_MODE": "local",
    "INPUT_PROJECT": "epics-base/pvxs",
    "INPUT_HEAD_SHA": "deadbeef",
    "INPUT_BASE_REF": "main",
    "INPUT_ACTION_VERSION": "abicheck/abicheck@v1",
}


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


def _run(
    script: Path, env_extra: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {**base_env, "ACTION_PATH": str(ACTION_DIR), **env_extra}
    return subprocess.run(
        [_bash_executable(), str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _run_finalize(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    github_output = cwd / "github_output"
    github_output.write_text("")
    result = _run(RUN_SH, {"GITHUB_OUTPUT": str(github_output), **env_extra}, cwd)
    outputs: dict[str, str] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            outputs[k] = v
    return result, outputs
