"""The integration workflow's edit-corpus floor judges only finished runs.

``needs.integration.result`` reads ``failure`` when one matrix leg failed and
another was cancelled, so the floor job once ran on a superseded run whose
corpus shard had been cancelled and failed with "neither Linux shard uploaded
edit corpus coverage records". Each Linux shard now leaves a
``shard-N.done`` marker when its integration step finished; the floor is
judged only with both markers. These tests execute the steps themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._workflow_exec import (
    find_run_step,
    have_bash,
    make_workspace,
    outside_is_intact,
    require_bash,
    run_step,
)

pytestmark = pytest.mark.skipif(not have_bash(), reason="needs a real bash")

_WF = "integration.yml"
_FLOOR = find_run_step(_WF, "edit-corpus-coverage", "Check the PR-tier floor")
_SHARD_REPORT = find_run_step(_WF, "integration", "Edit corpus coverage (Linux)")


def _stub_python(tmp_path: Path, rc: int) -> Path:
    """A ``python`` on PATH that exits *rc*: the report CLI's outcome."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "python").write_text(f"#!/bin/sh\nexit {rc}\n", encoding="utf-8")
    (bindir / "python").chmod(0o755)
    return bindir


@pytest.mark.parametrize("shard", ["1", "2"])
@pytest.mark.parametrize(
    ("has_records", "report_rc", "step_rc"),
    [(False, 1, 0), (True, 0, 0), (True, 1, 1)],
    ids=["no-records", "report-ok", "report-fails"],
)
def test_a_finished_shard_leaves_its_marker(
    tmp_path: Path, shard: str, has_records: bool, report_rc: int, step_rc: int
) -> None:
    # The marker says "the integration step finished"; it is written before
    # the report, so a failing report fails the step (and the leg) without
    # making the floor job read the shard as cancelled.
    require_bash()
    files = {"edit-corpus-coverage/.keep": ""} if has_records else {}
    ws = make_workspace(tmp_path, files=files)
    bindir = _stub_python(tmp_path, report_rc)
    result = run_step(
        _SHARD_REPORT,
        workspace=ws,
        env={
            "SHARD": shard,
            "GITHUB_STEP_SUMMARY": str(ws / "_summary"),
            "PATH": f"{bindir}:/usr/bin:/bin",
        },
    )
    assert result.returncode == step_rc, result.stderr
    assert f"edit-corpus-coverage/shard-{shard}.done" in result.tree()
    assert outside_is_intact(tmp_path)


@pytest.mark.parametrize(
    ("markers", "records", "rc", "message"),
    [
        # A cancelled shard: not judged, whatever was uploaded.
        ((), False, 0, "only 0 of 2 Linux shards finished"),
        (("1",), False, 0, "only 1 of 2 Linux shards finished"),
        (("2",), True, 0, "only 1 of 2 Linux shards finished"),
        # Both finished and neither recorded the PR tier: a real failure.
        (
            ("1", "2"),
            False,
            1,
            "neither Linux shard uploaded edit corpus coverage records",
        ),
        # Both finished with records: the report decides (a stub that fails).
        (("1", "2"), True, 1, ""),
    ],
    ids=[
        "none-finished",
        "shard1-only",
        "shard2-only-with-records",
        "both-no-records",
        "both-report-fails",
    ],
)
def test_the_floor_is_judged_only_when_both_shards_finished(
    tmp_path: Path, markers: tuple[str, ...], records: bool, rc: int, message: str
) -> None:
    require_bash()
    files = {
        f"edit-corpus-coverage/edit-corpus-coverage-shard{m}/shard-{m}.done": ""
        for m in markers
    }
    if records:
        files["edit-corpus-coverage/edit-corpus-coverage-shard2/pr-abc.json"] = "{}"
    ws = make_workspace(tmp_path, files=files)
    bindir = _stub_python(tmp_path, 1)
    result = run_step(
        _FLOOR,
        workspace=ws,
        env={"GITHUB_STEP_SUMMARY": str(ws / "_s"), "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == rc, result.stdout + result.stderr
    assert message in result.stdout + result.stderr
    assert outside_is_intact(tmp_path)
