"""ADR-067 D5/D6 through the real ``compare`` CLI, configured from
``.abicheck.yml``'s ``acknowledgment:`` block.

The oracle is written from the ADR's rules, not from the resolver:

* the review gate fires (exit floor ``1``) only under ``block`` and only
  when some public addition is not covered by a loaded record;
* it never lowers a real ``2``/``4`` (it is a ``max`` fold);
* a ``--policy`` file that states its own ``acknowledgment:`` block outranks
  ``.abicheck.yml`` (ADR-049 D7);
* records load only from an explicitly named ``--config``; the gate setting
  applies from an auto-discovered one too, since it can only tighten.

Every run also checks that the report's ``exit.code`` equals the process
exit -- the reason the axis moved inside ``ExitDecision``.
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


def _snapshots(tmp: Path, *, break_it: bool) -> tuple[Path, Path]:
    f_old = Function(
        name="f", mangled="f", return_type="int", params=[Param(name="a", type="int")]
    )
    f_new = (
        Function(
            name="f",
            mangled="f",
            return_type="long",
            params=[Param(name="a", type="int")],
        )
        if break_it
        else f_old
    )
    g = Function(name="g", mangled="g", return_type="void")
    old = AbiSnapshot(library="libx.so.1", version="1.0", functions=[f_old])
    new = AbiSnapshot(library="libx.so.1", version="2.0", functions=[f_new, g])
    paths = (tmp / "old.json", tmp / "new.json")
    save_snapshot(old, paths[0])
    save_snapshot(new, paths[1])
    return paths


def _write_records(path: Path, acknowledge_g: bool) -> None:
    entries = (
        '  - symbol: "g"\n    reason: "Planned addition"\n' if acknowledge_g else ""
    )
    path.write_text("version: 1\nacknowledgments:\n" + entries)


def _run(tmp: Path, args: list[str]) -> tuple[int, dict, str]:
    out = tmp / "report.json"
    res = CliRunner().invoke(main, [*args, "-o", f"json={out}"])
    assert out.exists(), res.output
    return res.exit_code, json.loads(out.read_text()), res.output


def _expected(
    action: str | None, acknowledged: bool, break_it: bool, policy_action: str | None
) -> int:
    effective = policy_action if policy_action is not None else action
    base = 4 if break_it else 0
    floor = 1 if (effective == "block" and not acknowledged) else 0
    return max(base, floor)


@pytest.mark.parametrize(
    ("action", "acknowledged", "break_it", "policy_action"),
    list(
        itertools.product(
            [None, "allow", "warn", "block"],
            [False, True],
            [False, True],
            [None, "allow"],
        )
    ),
)
def test_explicit_config(
    tmp_path, monkeypatch, action, acknowledged, break_it, policy_action
):
    monkeypatch.chdir(tmp_path)
    old, new = _snapshots(tmp_path, break_it=break_it)
    _write_records(tmp_path / "acks.yml", acknowledged)
    block = {"file": "acks.yml"}
    if action is not None:
        block["unacknowledged_additions"] = action
    cfg = tmp_path / "project.abicheck.yml"
    cfg.write_text(
        "acknowledgment:\n" + "".join(f"  {k}: {v}\n" for k, v in block.items())
    )
    args = ["compare", str(old), str(new), "--config", str(cfg)]
    if policy_action is not None:
        pol = tmp_path / "policy.yml"
        pol.write_text(
            f"acknowledgment:\n  unacknowledged_additions: {policy_action}\n"
        )
        args += ["--policy", str(pol)]

    code, doc, out = _run(tmp_path, args)

    want = _expected(action, acknowledged, break_it, policy_action)
    assert code == want, out
    assert doc["exit"]["code"] == code
    effective = policy_action if policy_action is not None else action
    assert doc["exit"]["additions_review_contribution"] == (
        1 if (effective == "block" and not acknowledged) else 0
    )


def test_oracle_reaches_every_code() -> None:
    codes = {
        _expected(a, k, b, p)
        for a, k, b, p in itertools.product(
            [None, "allow", "warn", "block"],
            [False, True],
            [False, True],
            [None, "allow"],
        )
    }
    assert codes == {0, 1, 4}


def test_discovered_config_applies_the_gate_but_not_the_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old, new = _snapshots(tmp_path, break_it=False)
    _write_records(tmp_path / "acks.yml", acknowledge_g=True)
    (tmp_path / ".abicheck.yml").write_text(
        "acknowledgment:\n  file: acks.yml\n  unacknowledged_additions: block\n"
    )

    code, doc, out = _run(tmp_path, ["compare", str(old), str(new)])

    assert "not trusted to accept findings" in out
    # The record would have accepted `g`; untrusted, it is not loaded, so the
    # block gate (which only tightens) still fires.
    assert code == 1
    assert doc["exit"]["reasons"] == ["additions_review"]


def test_no_block_means_no_review_and_no_exit_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old, new = _snapshots(tmp_path, break_it=False)
    code, doc, _ = _run(tmp_path, ["compare", str(old), str(new)])
    assert code == 0
    assert doc["exit"]["additions_review_contribution"] == 0


def test_unreadable_records_file_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old, new = _snapshots(tmp_path, break_it=False)
    (tmp_path / "acks.yml").write_text("version: 2\n")
    cfg = tmp_path / "project.abicheck.yml"
    cfg.write_text("acknowledgment:\n  file: acks.yml\n")
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg)]
    )
    assert res.exit_code == 64
    assert "acknowledgment.file" in res.output
