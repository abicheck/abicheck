# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``eval-suite.yml``'s dispatch ``only`` input never reaches the shell as code.

Each tier job (binary, source, header) passes ``github.event.inputs.only``
through ``env:`` and quotes it into an argv array. This executes each job's
real ``run:`` script under bash with a hostile ``ONLY_INPUT`` and a stub
``python`` on ``PATH``, and checks that nothing the payload asks for happens
and that the whole value arrives as one ``--only`` argument.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from _yaml_fast import safe_load

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/eval-suite.yml"

_PAYLOADS = (
    "zlib; touch PWNED",
    "$(touch PWNED)",
    "`touch PWNED`",
    "zlib && touch PWNED",
    "zlib | touch PWNED",
    "zlib\ntouch PWNED",
    "zlib' ; touch PWNED ; '",
)


def _runner_steps() -> list[tuple[str, str]]:
    jobs = safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    steps = []
    for name, job in jobs.items():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if "runner.py" in run and "ONLY_INPUT" in str(step.get("env", {})):
                steps.append((name, run))
    return steps


def test_every_tier_job_routes_only_through_env() -> None:
    jobs = {name for name, _ in _runner_steps()}
    assert {"binary-tier", "source-tier", "header-tier"} <= jobs, jobs


@pytest.mark.parametrize("payload", _PAYLOADS)
@pytest.mark.parametrize("job_run", _runner_steps(), ids=lambda s: s[0])
def test_hostile_only_input_is_one_inert_argument(
    tmp_path: Path, job_run: tuple[str, str], payload: str
) -> None:
    require_bash()
    _, run = job_run
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    argv_log = tmp_path / "argv"
    stub = stub_dir / "python"
    stub.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\0" "$a"; done > "{argv_log}"\n'
    )
    stub.chmod(0o755)
    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "ONLY_INPUT": payload,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    subprocess.run(
        [bash_executable(), "-e", "-c", run], cwd=tmp_path, env=env, check=False
    )
    assert not list(tmp_path.rglob("PWNED*"))
    args = argv_log.read_text().split("\0")[:-1]
    assert args[args.index("--only") + 1] == payload
