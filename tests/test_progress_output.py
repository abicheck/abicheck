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

"""Progress lines for long phases (``abicheck/extract/progress.py``).

The reported problem: a ~14-minute ``dump`` wrote nothing at all, so a slow
run was indistinguishable from a hung one. The contract stated here:

* a tracked loop always reports its final ``total/total`` and never more
  than one line per interval in between, whatever the item count;
* tracking never changes what the loop sees;
* progress goes to stderr only, on by default from the CLI, off with
  ``ABICHECK_PROGRESS=0``, and silent for a library caller that configured
  no logging.
"""

from __future__ import annotations

import inspect
import io
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.extract import progress


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch):
    """Capture ``abicheck.progress`` records at INFO, isolated per test."""
    logger = logging.getLogger(progress.LOGGER_NAME)
    captured: list[str] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    sink = _Sink()
    saved_level = logger.level
    # conftest turns progress off suite-wide; these tests are about it.
    monkeypatch.setenv(progress.PROGRESS_ENV, "1")
    monkeypatch.setattr(logger, "handlers", [sink])
    monkeypatch.setattr(logger, "propagate", False)
    # setLevel, not a plain attribute write: it clears the logging module's
    # isEnabledFor cache, which an earlier test may have filled with False.
    logger.setLevel(logging.INFO)
    yield captured
    logger.setLevel(saved_level)


class _Clock:
    """A monotonic clock the test advances explicitly."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize("total", [2, 3, 7, 50, 1000])
@pytest.mark.parametrize("step", [0.0, 0.3, 1.0, 4.9, 5.0, 12.0])
def test_track_is_throttled_and_always_reports_completion(
    records: list[str], monkeypatch: pytest.MonkeyPatch, total: int, step: float
) -> None:
    clock = _Clock()
    monkeypatch.setattr(progress.time, "monotonic", clock)
    seen = []
    for item in progress.track(range(total), "work", total):
        seen.append(item)
        clock.now += step
    # The loop sees exactly its input.
    assert seen == list(range(total))
    # Starts at 0/total, ends at total/total.
    assert records[0] == f"work: 0/{total}"
    assert records[-1].startswith(f"work: {total}/{total}")
    # Throttle oracle, independent of the implementation: over an elapsed
    # time E there is at most one tick per interval, plus the start and the
    # final line.
    elapsed = step * total
    bound = int(elapsed // progress.TICK_INTERVAL_S) + 2
    assert len(records) <= bound
    # Counts only ever increase.
    counts = [int(r.split(": ")[1].split("/")[0]) for r in records]
    assert counts == sorted(counts)


@pytest.mark.parametrize("total", [0, 1])
def test_trivial_loops_report_nothing(records: list[str], total: int) -> None:
    assert list(progress.track(range(total), "work", total)) == list(range(total))
    assert records == []


def test_disabled_logger_is_a_pure_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = logging.getLogger(progress.LOGGER_NAME)
    saved_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        items = object(), object(), object()
        assert tuple(progress.track(items, "work", 3)) == items
    finally:
        logger.setLevel(saved_level)


def test_phase_reports_start_and_duration(
    records: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    monkeypatch.setattr(progress.time, "monotonic", clock)
    with progress.phase("DWARF debug info"):
        clock.now += 2.5
    assert records == ["DWARF debug info ...", "DWARF debug info done (2.5s)"]


def test_phase_reports_failure_and_reraises(records: list[str]) -> None:
    with pytest.raises(KeyError):
        with progress.phase("header AST parse"):
            raise KeyError("x")
    assert records[0] == "header AST parse ..."
    assert records[1].startswith("header AST parse failed after")


def test_timed_keeps_the_wrapped_signature(records: list[str]) -> None:
    def work(a: int, *, b: str = "x") -> str:
        return f"{a}{b}"

    wrapped = progress.timed("work")(work)
    assert inspect.signature(wrapped) == inspect.signature(work)
    assert wrapped(1, b="y") == "1y"
    assert records == ["work ...", records[1]] and records[1].startswith("work done")


# ── the CLI wiring ──────────────────────────────────────────────────────────


def test_cli_names_the_same_logger() -> None:
    """``frontends`` may not import ``extract``, so runtime spells the
    logger name itself; it must stay equal to the module's own."""
    from abicheck.frontends.cli import runtime

    assert runtime._progress_logger.name == progress.LOGGER_NAME


def test_the_switch_is_a_registered_env_flag() -> None:
    """Read through the shared ``env_flags`` registry, not parsed by hand."""
    from abicheck.extract.env_flags import BOOLEAN_ENV_FLAGS

    assert BOOLEAN_ENV_FLAGS[progress.PROGRESS_ENV] is True


@pytest.mark.parametrize(
    ("env", "verbose", "expected"),
    [
        (None, False, True),
        ("1", False, True),
        ("0", False, False),
        ("off", False, False),
        ("0", True, True),
    ],
)
def test_cli_setup_routes_progress_to_stderr_only(
    monkeypatch: pytest.MonkeyPatch,
    env: str | None,
    verbose: bool,
    expected: bool,
) -> None:
    from abicheck.frontends.cli.runtime import _setup_verbosity

    if env is None:
        monkeypatch.delenv(progress.PROGRESS_ENV, raising=False)
    else:
        monkeypatch.setenv(progress.PROGRESS_ENV, env)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    logger = logging.getLogger(progress.LOGGER_NAME)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    try:
        _setup_verbosity(verbose)
        list(progress.track(range(3), "L4 source replay", 3))
    finally:
        logger.handlers[:] = saved[0]
        logger.setLevel(saved[1])
        logger.propagate = saved[2]
    assert out.getvalue() == ""
    assert ("abicheck: L4 source replay: 3/3" in err.getvalue()) is expected


def test_repeated_setup_does_not_duplicate_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck.frontends.cli.runtime import _setup_verbosity

    monkeypatch.setenv(progress.PROGRESS_ENV, "1")
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    logger = logging.getLogger(progress.LOGGER_NAME)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    try:
        _setup_verbosity(False)
        _setup_verbosity(False)
        with progress.phase("once"):
            pass
    finally:
        logger.handlers[:] = saved[0]
        logger.setLevel(saved[1])
        logger.propagate = saved[2]
    assert err.getvalue().count("abicheck: once ...") == 1


_HAVE_TOOLS = shutil.which("gcc") is not None and shutil.which("castxml") is not None


@pytest.mark.integration
@pytest.mark.skipif(not _HAVE_TOOLS, reason="gcc + castxml required")
def test_dump_reports_its_phases_on_stderr_and_keeps_stdout_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from click.testing import CliRunner

    from abicheck.cli import main

    (tmp_path / "api.h").write_text("int f(void);\n")
    (tmp_path / "lib.c").write_text("int f(void){return 1;}\n")
    lib = tmp_path / "libf.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(lib), str(tmp_path / "lib.c")],
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(progress.PROGRESS_ENV, "1")
    logger = logging.getLogger(progress.LOGGER_NAME)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    try:
        result = CliRunner().invoke(
            main, ["dump", str(lib), "-H", str(tmp_path / "api.h")]
        )
    finally:
        logger.handlers[:] = saved[0]
        logger.setLevel(saved[1])
        logger.propagate = saved[2]
    assert result.exit_code == 0, result.output
    json.loads(result.stdout)  # stdout is still exactly the snapshot
    assert "abicheck: header AST parse ..." in result.stderr
    assert "abicheck: header AST parse done (" in result.stderr
