"""The non-canonical-interpreter lanes of ``python-compat.yml`` stay full-suite,
on every PR, with no ``continue-on-error``.

A smoke-only, ``continue-on-error`` 3.15 leg that skipped ordinary PRs let a
3.15-only ``json``/str-Enum serialization break (~1,265 failing tests) merge
unnoticed. This pins each property whose silent loss would reopen that escape:
which interpreters run the full suite, that every shard exists, that nothing
gates the jobs off a PR or swallows their failure, and that the test selection
matches the canonical 3.14 ``unit-tests`` lane (the oracle is ``ci.yml``, not a
copy of the expression). The oldest supported Python (the ``requires-python``
floor) is smoke-only: it must stay in the ``smoke`` matrix, not ``full-suite``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import _yaml_fast
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_COMPAT = _yaml_fast.safe_load(
    (_ROOT / ".github/workflows/python-compat.yml").read_text()
)
_CI = _yaml_fast.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
_FULL_JOBS = ("full-suite", "free-threading")


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def _marker_expr(job: dict[str, Any]) -> str:
    found = re.findall(r'-m "([^"]+)"', _run_text(job))
    assert len(found) == 1, found
    return found[0]


def _triggers() -> dict[str, Any]:
    # PyYAML reads the bare key ``on`` as boolean True.
    return _COMPAT.get("on") or _COMPAT[True]


def test_runs_on_every_pull_request() -> None:
    assert "pull_request" in _triggers()


@pytest.mark.parametrize("name", [*_FULL_JOBS, "smoke"])
def test_no_lane_is_gated_off_a_pr_or_allowed_to_fail(name: str) -> None:
    job = _COMPAT["jobs"][name]
    assert "if" not in job, f"{name} must run on every PR"
    assert "continue-on-error" not in job, f"{name} must not swallow failures"
    for step in job.get("steps", []):
        assert "continue-on-error" not in step, (name, step.get("name"))


def test_full_suite_runs_the_newest_interpreter_and_not_the_floor() -> None:
    matrix = _COMPAT["jobs"]["full-suite"]["strategy"]["matrix"]
    floor = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"][
        "requires-python"
    ].removeprefix(">=")
    assert set(matrix["python-version"]) == {"3.15"}
    # The floor is still exercised, as a smoke lane (wheel install + imports).
    assert floor in _COMPAT["jobs"]["smoke"]["strategy"]["matrix"]["python-version"]


def test_free_threading_job_runs_a_free_threaded_interpreter_on_path() -> None:
    text = _run_text(_COMPAT["jobs"]["free-threading"])
    assert "_cp315t" in text
    assert "GITHUB_PATH" in text  # tests that shell out to python3 need it
    full_step = next(
        s
        for s in _COMPAT["jobs"]["free-threading"]["steps"]
        if "--shard" in str(s.get("run", ""))
    )
    assert full_step.get("env", {}).get("PYTHON_GIL") == "0"


@pytest.mark.parametrize("name", _FULL_JOBS)
def test_every_shard_runs(name: str) -> None:
    job = _COMPAT["jobs"][name]
    shards = job["strategy"]["matrix"]["shard"]
    text = _run_text(job)
    m = re.search(r"--shard=\$\{\{ matrix\.shard \}\}/(\d+)", text)
    assert m, text
    assert sorted(shards) == list(range(1, int(m.group(1)) + 1))


@pytest.mark.parametrize("name", _FULL_JOBS)
def test_selection_matches_the_canonical_lane(name: str) -> None:
    assert _marker_expr(_COMPAT["jobs"][name]) == _marker_expr(
        _CI["jobs"]["unit-tests"]
    )


def _suite_step(job: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in job["steps"] if "--shard" in str(s.get("run", "")))


def _minutes(spec: str) -> float:
    m = re.fullmatch(r"(\d+)([smh]?)", spec)
    assert m, spec
    return int(m.group(1)) * {"s": 1 / 60, "": 1 / 60, "m": 1, "h": 60}[m.group(2)]


@pytest.mark.parametrize("name", _FULL_JOBS)
def test_a_hang_is_diagnosed_inside_the_job_budget(name: str) -> None:
    """A hung shard must name its test and keep its log, not run into the job
    timeout silently (the 3.15 hang on #1961 left no log at all).

    Each layer must fire before the next one up: faulthandler's C-level stack
    dump before pytest-timeout's per-test kill, that kill before the shell
    watchdog, and the shell watchdog (plus its SIGKILL grace) before the job's
    own ``timeout-minutes``.
    """
    job = _COMPAT["jobs"][name]
    run = str(_suite_step(job)["run"])
    assert "set -o pipefail" in run  # `| tee` must not mask pytest's status
    per_test = tomllib.loads((_ROOT / "pyproject.toml").read_text())["tool"]["pytest"][
        "ini_options"
    ]["timeout"]
    dump = re.search(r"-o faulthandler_timeout=(\d+)", run)
    assert dump and 0 < int(dump.group(1)) < per_test, run
    # The signal method cannot interrupt a main thread stuck in C code.
    assert "-o timeout_method=thread" in run
    watchdog = re.search(
        r"timeout --signal=ABRT --kill-after=(\d+[smh]?) (\d+[smh]?) ", run
    )
    assert watchdog, run
    kill_after, limit = (_minutes(g) for g in watchdog.groups())
    assert per_test / 60 < limit
    assert limit + kill_after < job["timeout-minutes"] - 2  # setup + upload
    assert re.search(r'--junitxml="\$RUNNER_TEMP/[^"]+"', run)
    assert re.search(r'\| tee "\$RUNNER_TEMP/[^"]+"', run)

    steps = job["steps"]
    upload = next(
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert steps.index(upload) > steps.index(_suite_step(job))
    assert upload.get("if") == "always()"  # also on failure and cancellation
    paths = upload["with"]["path"]
    assert "pytest.log" in paths and "pytest-junit.xml" in paths
