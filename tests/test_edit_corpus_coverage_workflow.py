"""The integration workflow's edit-corpus floor judges only finished runs.

``needs.integration.result`` reads ``failure`` when one matrix leg failed and
another was cancelled, so the floor job once ran on a superseded run whose
corpus shard had been cancelled and failed with "no Linux shard uploaded
edit corpus coverage records". Each Linux shard now leaves a
``shard-N.done`` marker once its test command returned (never when the
step's timeout killed it); the floor is
judged only with every shard's marker. These tests execute the steps themselves.
"""

from __future__ import annotations

import shutil
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
_TEST_STEP = find_run_step(
    _WF, "integration", "Integration tests (Linux, with coverage)"
)


def _stub_python(tmp_path: Path, rc: int) -> Path:
    """A ``python`` on PATH that exits *rc*: the report CLI's outcome."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "python").write_text(f"#!/bin/sh\nexit {rc}\n", encoding="utf-8")
    (bindir / "python").chmod(0o755)
    return bindir


def _stub_bsd_wc(bindir: Path) -> None:
    """A ``wc`` that pads its count like BSD/macOS ``wc -l`` (``       2``)."""
    real = shutil.which("wc")
    assert real is not None
    (bindir / "wc").write_text(
        f"#!/bin/sh\nn=$({real} \"$@\" | tr -d ' ')\nprintf '%8s\\n' \"$n\"\n",
        encoding="utf-8",
    )
    (bindir / "wc").chmod(0o755)


@pytest.mark.parametrize("shard", ["1", "2", "3"])
@pytest.mark.parametrize("test_rc", [0, 1, 3], ids=["pass", "fail", "error"])
def test_a_returned_test_command_leaves_its_marker(
    tmp_path: Path, shard: str, test_rc: int
) -> None:
    # The marker says "the test command returned": the test step writes it
    # whatever the command's exit, and keeps that exit as its own. A step
    # its timeout kills never reaches the write (it also reads `failure`, so
    # no later step could tell it from a finished one).
    require_bash()
    ws = make_workspace(tmp_path)
    bindir = _stub_python(tmp_path, test_rc)
    result = run_step(
        _TEST_STEP,
        workspace=ws,
        env={"SHARD": shard, "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == test_rc, result.stderr
    assert f"edit-corpus-coverage/shard-{shard}.done" in result.tree()
    assert outside_is_intact(tmp_path)


def test_a_killed_test_command_leaves_no_marker(tmp_path: Path) -> None:
    # Negative control: a command that never returns (the timeout's SIGTERM
    # stands in as the kill) leaves no marker.
    require_bash()
    ws = make_workspace(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").write_text(
        "#!/bin/sh\nkill -TERM $PPID\nsleep 5\n", encoding="utf-8"
    )
    (bindir / "python").chmod(0o755)
    result = run_step(
        _TEST_STEP,
        workspace=ws,
        env={"SHARD": "2", "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode != 0
    assert "edit-corpus-coverage/shard-2.done" not in result.tree()


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs Linux /proc")
def test_the_test_command_runs_niced_without_waiting_on_the_sampler(
    tmp_path: Path,
) -> None:
    # The tests run below the runner agent's priority, so a fully busy machine
    # still lets the agent heartbeat; the load sampler must neither delay the
    # step's end nor outlive it (it polls once a minute).
    require_bash()
    ws = make_workspace(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python").write_text(
        "#!/bin/sh\ncut -d' ' -f19 /proc/self/stat\n", encoding="utf-8"
    )
    (bindir / "python").chmod(0o755)
    import time

    started = time.monotonic()
    result = run_step(
        _TEST_STEP,
        workspace=ws,
        env={"SHARD": "1", "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    base = int(Path("/proc/self/stat").read_text().split()[18])
    assert int(result.stdout.split()[0]) == min(base + 10, 19)
    assert time.monotonic() - started < 30


@pytest.mark.parametrize(
    ("has_records", "report_rc", "step_rc"),
    [(False, 1, 0), (True, 0, 0), (True, 1, 1)],
    ids=["no-records", "report-ok", "report-fails"],
)
def test_the_shard_report_runs_only_on_records(
    tmp_path: Path, has_records: bool, report_rc: int, step_rc: int
) -> None:
    # The report step writes no marker; it reports a shard's records when a
    # corpus cell left any, and a failing report fails the step.
    require_bash()
    files = {"edit-corpus-coverage/rec.json": "{}"} if has_records else {}
    files["edit-corpus-coverage/shard-1.done"] = ""
    ws = make_workspace(tmp_path, files=files)
    bindir = _stub_python(tmp_path, report_rc)
    result = run_step(
        _SHARD_REPORT,
        workspace=ws,
        env={
            "SHARD": "1",
            "GITHUB_STEP_SUMMARY": str(ws / "_summary"),
            "PATH": f"{bindir}:/usr/bin:/bin",
        },
    )
    assert result.returncode == step_rc, result.stderr
    assert outside_is_intact(tmp_path)


@pytest.mark.parametrize(
    ("markers", "records", "rc", "message"),
    [
        # A cancelled shard: not judged, whatever was uploaded.
        ((), False, 0, "only 0 of 3 Linux shards finished"),
        (("1",), False, 0, "only 1 of 3 Linux shards finished"),
        (("2",), True, 0, "only 1 of 3 Linux shards finished"),
        (("1", "3"), True, 0, "only 2 of 3 Linux shards finished"),
        # All finished and none recorded the PR tier: a real failure.
        (
            ("1", "2", "3"),
            False,
            1,
            "no Linux shard uploaded edit corpus coverage records",
        ),
        # All finished with records: the report decides (a stub that fails).
        (("1", "2", "3"), True, 1, ""),
    ],
    ids=[
        "none-finished",
        "shard1-only",
        "shard2-only-with-records",
        "two-of-three",
        "all-no-records",
        "all-report-fails",
    ],
)
@pytest.mark.parametrize("bsd_wc", [False, True], ids=["gnu-wc", "bsd-wc"])
def test_the_floor_is_judged_only_when_every_shard_finished(
    tmp_path: Path,
    markers: tuple[str, ...],
    records: bool,
    rc: int,
    message: str,
    bsd_wc: bool,
) -> None:
    # The count is read through `wc -l`, which BSD pads with blanks: the step
    # must give the same answer under either wc (macOS runs this test).
    require_bash()
    files = {
        f"edit-corpus-coverage/edit-corpus-coverage-shard{m}/shard-{m}.done": ""
        for m in markers
    }
    if records:
        files["edit-corpus-coverage/edit-corpus-coverage-shard2/pr-abc.json"] = "{}"
    ws = make_workspace(tmp_path, files=files)
    bindir = _stub_python(tmp_path, 1)
    if bsd_wc:
        _stub_bsd_wc(bindir)
    result = run_step(
        _FLOOR,
        workspace=ws,
        env={"GITHUB_STEP_SUMMARY": str(ws / "_s"), "PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == rc, result.stdout + result.stderr
    assert message in result.stdout + result.stderr
    assert outside_is_intact(tmp_path)
