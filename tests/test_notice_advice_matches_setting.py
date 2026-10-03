# SPDX-License-Identifier: Apache-2.0
"""A stderr notice never advises the setting the run already has.

Bug class: ``config.propagation_completeness`` -- a resolved setting one
consumer does not use, here the advice half of the notice. The
contract-coverage notice ended every message with "or set
contract.unresolved=warn to accept incomplete coverage" -- including the
message that opens "Accepted by contract.unresolved=warn". The incomplete-scope
notice likewise told a run already under ``scope.on_incomplete: block`` to set
``block`` "to fail the run". Both send the reader looking for a change that
changes nothing.

Oracle, written out here from the settings' documented meaning: a notice
advises setting ``contract.unresolved=warn`` exactly when the run's
``contract.unresolved`` is not ``warn``, and advises ``scope.on_incomplete:
block`` exactly when the run's effective ``scope.on_incomplete`` is ``warn``
(the default). The rest of each notice -- where the full record is, how to
close the gap -- is stated whatever the setting. Driven through ``compare``
itself, across a compatible and a breaking pair, so the advice is checked on
every exit-code wording the notice has.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

_ADVISE_WARN = "set contract.unresolved=warn"
_ADVISE_BLOCK = "set scope.on_incomplete: block"


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z5{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _snap(version: str, names: tuple[str, ...], library: str = "libfoo.so.1"):
    return AbiSnapshot(
        library=library,
        version=version,
        functions=[_fn(n) for n in names],
        from_headers=True,
    )


def _pair(breaking: bool) -> tuple[AbiSnapshot, AbiSnapshot]:
    new_names = ("pub_a",) if breaking else ("pub_a", "pub_b")
    return _snap("1.0", ("pub_a", "pub_b")), _snap("2.0", new_names)


def _stderr(args: list[str]) -> str:
    result = CliRunner().invoke(main, args)
    assert result.exit_code in (0, 1, 2, 4), result.output
    return result.stderr


@pytest.mark.parametrize(
    ("unresolved", "breaking"),
    list(itertools.product((None, "not_checkable", "warn"), (False, True))),
)
def test_the_coverage_notice_advises_only_a_change(
    tmp_path: Path, unresolved: str | None, breaking: bool
) -> None:
    old, new = _pair(breaking)
    old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    args = ["compare", str(old_p), str(new_p), "--contract", "exports"]
    if unresolved is not None:
        pack = tmp_path / "pack.yml"
        pack.write_text(
            "id: unresolved\nversion: 1\nkind: contract\n"
            f"assignments:\n  contract.unresolved: {unresolved}\n",
            encoding="utf-8",
        )
        args += ["--pack", str(pack)]

    err = _stderr(args)
    assert "Contract coverage incomplete" in err
    assert "-o json=..." in err
    assert (_ADVISE_WARN in err) is (unresolved != "warn")


@pytest.mark.parametrize(
    ("on_incomplete", "breaking"),
    list(itertools.product((None, "warn", "block"), (False, True))),
)
def test_the_scope_notice_advises_only_a_change(
    tmp_path: Path, on_incomplete: str | None, breaking: bool
) -> None:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old, new = _pair(breaking)
    (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
    (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")
    # A second baseline member NEW does not supply: the scope is incomplete.
    (old_dir / "libbar.json").write_text(
        snapshot_to_json(_snap("1.0", ("bar",), library="libbar.so.1")),
        encoding="utf-8",
    )
    args = ["compare", str(old_dir), str(new_dir)]
    if on_incomplete is not None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(f"scope:\n  on_incomplete: {on_incomplete}\n", encoding="utf-8")
        args += ["--config", str(cfg)]

    err = _stderr(args)
    assert "Comparison scope incompletely checked" in err
    assert "Supply the missing members" in err
    assert "-o json=..." in err
    effective = on_incomplete or "warn"
    assert (_ADVISE_BLOCK in err) is (effective == "warn")
