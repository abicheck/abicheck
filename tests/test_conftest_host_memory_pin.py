"""Guard for ``tests/conftest.py``'s ``_pin_host_memory_probe`` autouse fixture.

The bug class: a unit test's outcome depending on live host memory. Under
``pytest -n 4`` the other workers moved ``MemAvailable``; when it dipped, the
release fan-out clamped its worker pool and printed a (correct) ``Note:``
line to stderr, which ``CliRunner`` folds into ``result.output`` -- and every
release test parsing that as JSON failed on whichever worker was unlucky.

These tests state the contract from both sides: the pin is in force (and
restorable/opt-out-able), the mechanism it neutralizes really does engage
when the probe reports pressure (so the pin is not guarding a phantom), and
at every probed value the stdout document itself stays machine-readable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import process_resources
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

_NOTE = "parallel release workers reduced"


def _release_dirs(tmp_path: Path, members: int = 4) -> tuple[Path, Path]:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for i in range(members):
        snap = AbiSnapshot(
            library=f"lib{i}.so",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
        )
        for d in (old_dir, new_dir):
            (d / f"lib{i}.json").write_text(snapshot_to_json(snap), encoding="utf-8")
    return old_dir, new_dir


def _run(old_dir: Path, new_dir: Path):
    return CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "-o", "json=-"]
    )


def test_autouse_pin_reports_unprobeable_memory() -> None:
    assert process_resources.available_mem_gib() is None


@pytest.mark.host_memory_probe
def test_marker_opts_out_of_the_pin() -> None:
    # The real function, not the conftest lambda: its own module owns it.
    fn = process_resources.available_mem_gib
    assert fn.__module__ == "abicheck.process_resources"
    assert fn.__name__ == "available_mem_gib"


def test_pinned_release_output_is_pure_json(tmp_path: Path) -> None:
    result = _run(*_release_dirs(tmp_path))
    assert result.exit_code == 0, result.output
    assert _NOTE not in result.output
    assert json.loads(result.output)["libraries"]


@pytest.mark.parametrize("avail_gib", [0.05, 0.5, 1.5, 2.5, 3.9, 64.0])
def test_stdout_document_survives_any_probed_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, avail_gib: float
) -> None:
    """Oracle independent of the planner: under *any* probe value the stdout
    stream is exactly one JSON document; any clamp note is stderr-only."""
    monkeypatch.setattr(process_resources, "available_mem_gib", lambda: avail_gib)
    result = _run(*_release_dirs(tmp_path))
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert len(doc["libraries"]) == 4
    assert _NOTE not in result.stdout


def test_low_memory_really_engages_the_clamp_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity: the hazard the pin removes is real, so without the pin a
    host under pressure would put the note into ``result.output``."""
    monkeypatch.setattr(process_resources, "python_parallelism", lambda: 4)
    monkeypatch.setattr(process_resources, "available_mem_gib", lambda: 0.05)
    monkeypatch.delenv("ABICHECK_RELEASE_JOB_MEM_GIB", raising=False)
    result = _run(*_release_dirs(tmp_path))
    assert result.exit_code == 0, result.output
    assert _NOTE in result.stderr
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)
