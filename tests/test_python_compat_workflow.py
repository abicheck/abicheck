"""The non-canonical-interpreter lanes of ``python-compat.yml`` stay full-suite,
on every PR, with no ``continue-on-error``.

A smoke-only, ``continue-on-error`` 3.15 leg that skipped ordinary PRs let a
3.15-only ``json``/str-Enum serialization break (~1,265 failing tests) merge
unnoticed. This pins each property whose silent loss would reopen that escape:
which interpreters run the full suite, that every shard exists, that nothing
gates the jobs off a PR or swallows their failure, and that the test selection
matches the canonical 3.13 ``unit-tests`` lane (the oracle is ``ci.yml``, not a
copy of the expression).
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


def test_full_suite_covers_the_floor_and_the_newest_interpreter() -> None:
    matrix = _COMPAT["jobs"]["full-suite"]["strategy"]["matrix"]
    floor = tomllib.loads((_ROOT / "pyproject.toml").read_text())["project"][
        "requires-python"
    ].removeprefix(">=")
    assert set(matrix["python-version"]) == {floor, "3.15"}


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
