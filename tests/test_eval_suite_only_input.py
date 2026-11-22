# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``eval-suite.yml``'s dispatch ``only`` input never reaches the shell as code.

Each tier job (binary, source, header) passes ``github.event.inputs.only``
through ``env:`` and quotes it into an argv array. This executes each job's
real ``run:`` step through ``_workflow_exec.run_step`` with a hostile
``ONLY_INPUT`` and a stub ``python`` on ``PATH``, and checks that nothing the
payload asks for happens and that the whole value arrives as one ``--only``
argument.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _workflow_exec import find_run_step, require_bash, run_step

_WORKFLOW = "eval-suite.yml"
_STEPS = (
    ("binary-tier", "Run binary tier (fail on verdict drift)"),
    ("source-tier", "Run source tier (coverage; gated only against systemic breakage)"),
    ("header-tier", "Run header tier (non-gating)"),
)
_PAYLOADS = (
    "zlib; touch PWNED",
    "$(touch PWNED)",
    "`touch PWNED`",
    "zlib && touch PWNED",
    "zlib | touch PWNED",
    "zlib\ntouch PWNED",
    "zlib' ; touch PWNED ; '",
)


@pytest.mark.parametrize("job, step_name", _STEPS, ids=[j for j, _ in _STEPS])
def test_tier_step_routes_only_through_env(job: str, step_name: str) -> None:
    step = find_run_step(_WORKFLOW, job, step_name)
    assert "ONLY_INPUT" in (step.get("env") or {})
    assert "inputs.only" not in step["run"]


@pytest.mark.skipif(
    os.name == "nt", reason="the python stub needs a shebang script exec'd from PATH"
)
@pytest.mark.parametrize("payload", _PAYLOADS)
@pytest.mark.parametrize("job, step_name", _STEPS, ids=[j for j, _ in _STEPS])
def test_hostile_only_input_is_one_inert_argument(
    tmp_path: Path, job: str, step_name: str, payload: str
) -> None:
    require_bash()
    step = find_run_step(_WORKFLOW, job, step_name)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    argv_log = tmp_path / "argv"
    stub = stub_dir / "python"
    stub.write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\0" "$a"; done > "{argv_log}"\n'
    )
    stub.chmod(0o755)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_step(
        step,
        workspace=workspace,
        env={
            "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "ONLY_INPUT": payload,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
    )
    assert not list(tmp_path.rglob("PWNED*"))
    args = argv_log.read_text().split("\0")[:-1]
    assert args[args.index("--only") + 1] == payload
