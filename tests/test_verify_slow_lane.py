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

"""The `slow` marker lane's one definition (CLAUDE.md "M0-3").

ci.yml's `slow-tests` job runs verify.py's `slow` and `slow-perf` steps rather
than its own copy of their pytest lines, so the lane a contributor runs
locally is the lane CI runs. These tests pin that routing, and the shape both
steps must keep: the parallel/serial split, a quiet log, and per-step JUnit
results distinct from the unit lane's. Split out of
tests/test_verify_profiles.py, which holds the rest of verify.py's consumer
contract.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sys
from pathlib import Path
from typing import Any

import _yaml_fast
import pytest

ROOT = Path(__file__).resolve().parent.parent
_VERIFY_PATH = ROOT / "scripts" / "verify.py"
_spec = importlib.util.spec_from_file_location("abicheck_scripts_verify", _VERIFY_PATH)
assert _spec and _spec.loader
verify = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = verify  # dataclass() needs the module registered
_spec.loader.exec_module(verify)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _step(name: str) -> Any:
    for s in verify.STEPS:
        if s.name == name:
            return s
    raise AssertionError(f"no such verify.py step: {name!r}")


def _junit_path(command: str) -> str:
    match = re.search(r"--junitxml=(.*?\.xml)", command)
    assert match is not None, f"no JUnit XML record for: {command}"
    return match.group(1)


_SLOW_STEPS = ("slow", "slow-perf")


def _slow_job_run_lines() -> list[str]:
    pytest.importorskip("yaml")
    workflow = _yaml_fast.safe_load(_read(".github/workflows/ci.yml"))
    return [
        line.strip()
        for step in workflow["jobs"]["slow-tests"]["steps"]
        for line in str(step.get("run", "")).splitlines()
        if line.strip()
    ]


def _slow_job_verify_invocation() -> list[str]:
    calls = [
        shlex.split(line)
        for line in _slow_job_run_lines()
        if "scripts/verify.py" in line
    ]
    assert len(calls) == 1, f"expected one verify.py call in slow-tests: {calls}"
    return calls[0]


def _slow_job_commands() -> list[str]:
    """The pytest command each slow step runs, as the CI job runs it."""
    argv = _slow_job_verify_invocation()
    junit_dir = argv[argv.index("--junit-dir") + 1]
    return [
        " ".join(verify.step_command(_step(name), junit_dir)) for name in _SLOW_STEPS
    ]


def test_the_slow_job_writes_its_own_distinct_result_files() -> None:
    paths = [_junit_path(c) for c in _slow_job_commands()]
    assert len(set(paths)) == len(paths), f"results would overwrite: {paths}"
    for path in paths:
        assert Path(path).name.startswith("test-results-slow"), (
            "the slow job's results must be distinguishable from the unit "
            f"lane's in the uploaded artifacts: {path}"
        )
    uploads = _read(".github/workflows/ci.yml")
    assert "path: test-results-slow*.xml" in uploads


class TestTheSlowLaneHasExactlyOneOwner:
    """The `slow` marker lane is required, runs once, and runs on its own.

    Splitting it out of `unit-tests` is only a critical-path win if it is not
    *also* still run there; and it is only safe if something still runs it at
    all. Both halves are asserted structurally rather than trusted to review,
    because a partial revert of either side is invisible in a green run --
    duplicating the work looks like a pass, and dropping it looks like a pass
    too.
    """

    @staticmethod
    def _job_invocations(job: str) -> list[str]:
        pytest.importorskip("yaml")
        workflow = _yaml_fast.safe_load(_read(".github/workflows/ci.yml"))
        return [
            line.strip()
            for step in workflow["jobs"][job]["steps"]
            for line in str(step.get("run", "")).splitlines()
            if line.strip().startswith("pytest ")
        ]

    def test_the_slow_tests_job_runs_verify_slow_steps(self) -> None:
        """The job calls verify.py's catalog, never an inline copy of it."""
        argv = _slow_job_verify_invocation()
        only = set(argv[argv.index("--only") + 1].split(","))
        assert only == set(_SLOW_STEPS), argv
        assert "--junit-dir" in argv, argv
        inline = [line for line in _slow_job_run_lines() if line.startswith("pytest")]
        assert not inline, f"slow-tests must not hand-copy verify.py steps: {inline}"

    def test_the_slow_steps_split_parallel_from_wall_clock_tests(self) -> None:
        parallel, serial = (_step(n).cmd for n in _SLOW_STEPS)
        assert "slow" in parallel and "-n" in parallel, parallel
        assert "-n" not in serial, (
            "the wall-clock-timed perf tests must run serially "
            f"(concurrency makes scheduler contention part of the measurement): {serial}"
        )
        for path in (
            "tests/test_performance.py",
            "tests/test_header_scan_deadline_integration.py",
        ):
            assert f"--ignore={path}" in parallel
            assert path in serial

    @pytest.mark.parametrize("name", _SLOW_STEPS)
    def test_the_slow_steps_keep_the_log_quiet(self, name: str) -> None:
        cmd = " ".join(_step(name).cmd)
        assert " -v" not in f" {cmd} "
        assert " -q" in f" {cmd} "
        assert "-r fE" in cmd

    def test_unit_tests_no_longer_runs_the_slow_lane(self) -> None:
        offenders = [c for c in self._job_invocations("unit-tests") if '-m "slow"' in c]
        assert not offenders, (
            "the slow lane moved to its own `slow-tests` job; running it in "
            f"`unit-tests` too puts it back on that job's critical path: {offenders}"
        )

    def test_the_unit_lane_still_excludes_slow_tests(self) -> None:
        # The complement: `unit-tests` must keep *excluding* the marker, or the
        # split silently turns into the slow tests running twice.
        commands = self._job_invocations("unit-tests")
        offenders = [c for c in commands if "not slow" not in c]
        assert not offenders, f"unit-tests must exclude the slow marker: {offenders}"


def test_junit_dir_names_each_pytest_step_after_itself(tmp_path: Path) -> None:
    for step in verify.STEPS:
        cmd = verify.step_command(step, str(tmp_path))
        if "pytest" in step.cmd:
            assert cmd[-1] == f"--junitxml={tmp_path / f'test-results-{step.name}.xml'}"
        else:
            assert cmd == step.cmd
        assert verify.step_command(step) == step.cmd
