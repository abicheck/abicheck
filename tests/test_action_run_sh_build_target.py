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

"""Behavioral tests for ``action/run.sh``'s ``build-target`` input, now
retired on every mode.

``build-target`` used to forward to ``dump``'s own ``--build-target`` CLI
flag (``scan``'s copy of the same flag was retired first, ADR-068's second
2026-09-09 amendment). ``dump --build-target`` was later retired too, once
that removal resolved the routing hazard that had deferred it
(``frontends/cli/options/rulings.py``'s former deferred ruling) --
``.abicheck.yml``'s ``build.targets`` is now the only front-end-reachable
source of root-target scoping for either command. Setting the ``build-target``
Action input on any mode is now a hard usage error (``::error::``, exit
nonzero), not a mode-scoped forward -- mirroring ``scan --build-target``'s
own earlier, narrower rejection.

Extracts the full mode-branch region of ``run.sh`` verbatim -- same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_artifact_set.py`` -- and runs it with a harness that
sets the relevant ``INPUT_*`` env vars, capturing the resulting ``CMD``
array (or the rejection's exit code/``::error::`` text).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_END_MARKER = 'if [[ "${INPUT_VERBOSE:-false}" == "true" ]]; then'


def _mode_branches_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    return text[: text.index(_END_MARKER)]


def _run_cmd(env_extra: dict[str, str]) -> list[str]:
    require_bash()
    script = _mode_branches_region() + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
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
            [bash_executable(), script_path],
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
    return [item for item in result.stdout.split("\x1f") if item]


def _run_mode_branches(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the mode-branch region and return the raw result, so a rejected
    input's own nonzero exit and `::error::` line can be asserted (`_run_cmd`
    treats a nonzero exit as a harness failure)."""
    require_bash()
    script = _mode_branches_region() + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
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
        return subprocess.run(
            [bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
    finally:
        os.unlink(script_path)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBuildTargetIsRetiredOnEveryMode:
    """`mode: dump` no longer forwards `build-target` -- it rejects it
    outright, naming `.abicheck.yml`'s `build.targets` as the replacement.
    (`mode: scan`'s own copy of this flag was retired first, ADR-068's
    second 2026-09-09 amendment; `mode: scan` itself is now retired
    outright too, so there is no `scan`-shaped rejection left to test
    here -- `action/validate-inputs.sh`'s unconditional `mode: scan is no
    longer supported` check runs, and fails the step, before build-target
    is ever consulted.)"""

    def test_dump_rejected_with_an_error_naming_config(self) -> None:
        result = _run_mode_branches(
            {
                "INPUT_MODE": "dump",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_BUILD_TARGET": "//:math //:util",
            }
        )
        assert result.returncode != 0
        assert "build-target is retired" in result.stdout
        assert "build.targets" in result.stdout

    def test_dump_absent_forwards_none(self) -> None:
        cmd = _run_cmd({"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": "lib.so"})
        assert "dump" in cmd
        assert "--build-target" not in cmd


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestCompareRejectsBuildTarget:
    """`compare` never had a `--build-target` flag at all (`dump`-only), but
    the retirement check is unconditional across every mode (not just
    `dump`/`scan`) -- so setting build-target on mode: compare (either
    shape) is now a hard `::error::`, the same as `dump`, rather than a
    silent no-op the way it used to be before this input existed for
    `compare` at all."""

    def test_rejected_on_two_sided_compare(self) -> None:
        result = _run_mode_branches(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_BUILD_TARGET": "//:math",
            }
        )
        assert result.returncode != 0
        assert "build-target is retired" in result.stdout
        assert "build.targets" in result.stdout

    def test_rejected_on_audit_only_compare(self) -> None:
        result = _run_mode_branches(
            {
                "INPUT_MODE": "compare",
                "INPUT_NEW_LIBRARY": "lib.so",
                "INPUT_BUILD_TARGET": "//:math",
            }
        )
        assert result.returncode != 0
        assert "build-target is retired" in result.stdout
        assert "build.targets" in result.stdout
