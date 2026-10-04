"""ADR-067 D5/D6 beyond the single-pair CLI: the directory/package release
fan-out and the typed ``CompareRequest``.

Same oracle as ``test_acknowledgment_config_cli.py`` (written from the ADR's
rules, not the resolver): the review floor is ``1`` exactly when the
configured action is ``block`` and some public addition has no loaded
record; it is a ``max`` fold, so it never lowers a real ``4``. The release
path additionally requires records to load only from an explicit
``--config``, as the single pair does.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Param
from abicheck.serialization import save_snapshot
from abicheck.service_compare_pipeline import run_compare_request
from abicheck.workflows.contracts import CompareRequest, InputSpec, ValidationError


def _pair(break_it: bool) -> tuple[AbiSnapshot, AbiSnapshot]:
    f_old = Function(
        name="f", mangled="f", return_type="int", params=[Param(name="a", type="int")]
    )
    f_new = Function(
        name="f",
        mangled="f",
        return_type="long" if break_it else "int",
        params=[Param(name="a", type="int")],
    )
    g = Function(name="g", mangled="g", return_type="void")
    return (
        AbiSnapshot(library="libx.so.1", version="1.0", functions=[f_old]),
        AbiSnapshot(library="libx.so.1", version="2.0", functions=[f_new, g]),
    )


def _records(path: Path, acknowledged: bool) -> Path:
    body = '  - symbol: "g"\n    reason: "Planned"\n' if acknowledged else ""
    path.write_text("version: 1\nacknowledgments:\n" + body)
    return path


def _expected(action: str | None, acknowledged: bool, break_it: bool) -> int:
    return max(4 if break_it else 0, 1 if action == "block" and not acknowledged else 0)


_GRID = list(itertools.product([None, "warn", "block"], [False, True], [False, True]))


def test_grid_reaches_every_code() -> None:
    assert {_expected(*row) for row in _GRID} == {0, 1, 4}


@pytest.mark.parametrize(("action", "acknowledged", "break_it"), _GRID)
def test_release_fan_out(tmp_path, monkeypatch, action, acknowledged, break_it) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _pair(break_it)
    for side, snap in (("old", old), ("new", new)):
        (tmp_path / side).mkdir()
        save_snapshot(snap, tmp_path / side / "libx.so.1.json")
    _records(tmp_path / "acks.yml", acknowledged)
    block = "acknowledgment:\n  file: acks.yml\n"
    if action is not None:
        block += f"  unacknowledged_additions: {action}\n"
    (tmp_path / "cfg.yml").write_text(block)
    res = CliRunner().invoke(
        main, ["compare", "old", "new", "--config", "cfg.yml", "-o", "json=out.json"]
    )
    assert res.exit_code == _expected(action, acknowledged, break_it), res.output
    data = json.loads((tmp_path / "out.json").read_text())
    assert data["exit"]["code"] == res.exit_code
    assert data["exit"]["additions_review_contribution"] == (
        1 if action == "block" and not acknowledged else 0
    )


def test_release_discovered_config_does_not_load_records(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _pair(False)
    for side, snap in (("old", old), ("new", new)):
        (tmp_path / side).mkdir()
        save_snapshot(snap, tmp_path / side / "libx.so.1.json")
    _records(tmp_path / "acks.yml", True)
    (tmp_path / ".abicheck.yml").write_text(
        "acknowledgment:\n  file: acks.yml\n  unacknowledged_additions: block\n"
    )
    res = CliRunner().invoke(main, ["compare", "old", "new"])
    # The gate tightens from a discovered config; the records cannot accept.
    assert res.exit_code == 1, res.output
    assert "not loaded" in res.output


@pytest.mark.parametrize(("action", "acknowledged", "break_it"), _GRID)
def test_typed_api(tmp_path, action, acknowledged, break_it) -> None:
    old, new = _pair(break_it)
    save_snapshot(old, tmp_path / "old.json")
    save_snapshot(new, tmp_path / "new.json")
    request = CompareRequest(
        old=InputSpec(path=tmp_path / "old.json"),
        new=InputSpec(path=tmp_path / "new.json"),
        acknowledgments_path=_records(tmp_path / "acks.yml", acknowledged),
        acknowledgment_unacknowledged_additions=action,
    )
    result = run_compare_request(request)
    review = 1 if action == "block" and not acknowledged else 0
    assert result.exit_decision.additions_review_contribution == review
    assert result.exit_decision.code == _expected(action, acknowledged, break_it)


def test_typed_api_rejects_an_unknown_action(tmp_path) -> None:
    request = CompareRequest(
        old=InputSpec(path=tmp_path / "a"),
        new=InputSpec(path=tmp_path / "b"),
        acknowledgment_unacknowledged_additions="maybe",
        acknowledgments_path=tmp_path / "missing.yml",
    )
    with pytest.raises(ValidationError) as info:
        request.validate()
    text = str(info.value)
    assert "acknowledgment_unacknowledged_additions" in text
    assert "acknowledgments file not found" in text
