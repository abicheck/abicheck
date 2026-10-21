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

"""ADR-067 D5/S4 surfaces: ``compare --acknowledgments`` and the Action's
``acknowledgments`` input, both over the one resolver.

The CLI flag is an operator's own explicit input, so it may supply records even
with no config at all, and it overrides ``.abicheck.yml``'s ``acknowledgment.file``.
The Action forwards its input as ``--acknowledgments`` and validates the path
before the Python install, so a misspelled path fails at the top of the step.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _lib,
    _run_action,
    _stub_abicheck,
)
from click.testing import CliRunner
from test_action_validate_inputs import VALIDATE_SH, _run_validate

from abicheck.cli import main
from abicheck.model import AbiSnapshot
from abicheck.model.declarations import Function
from abicheck.serialization import save_snapshot

pytestmark = REQUIRES_POSIX_SHELL


def _pair(tmp: Path) -> tuple[Path, Path]:
    old = AbiSnapshot(
        library="libx.so.1",
        version="1.0",
        functions=[
            Function(name="keep", mangled="keep", return_type="void", params=[])
        ],
    )
    new = AbiSnapshot(
        library="libx.so.1",
        version="2.0",
        functions=[
            Function(name="keep", mangled="keep", return_type="void", params=[]),
            Function(name="added", mangled="added", return_type="void", params=[]),
        ],
    )
    save_snapshot(old, tmp / "old.json")
    save_snapshot(new, tmp / "new.json")
    return tmp / "old.json", tmp / "new.json"


_ACK_ADDED = (
    "version: 1\nacknowledgments:\n  - symbol: added\n"
    "    change_kind: func_added\n    reason: planned\n"
)
_ACK_NOTHING = "version: 1\nacknowledgments: []\n"


def test_flag_supplies_records_with_no_config_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _pair(tmp_path)
    (tmp_path / "acks.yml").write_text(_ACK_ADDED)
    (tmp_path / ".abicheck.yml").write_text(
        "acknowledgment:\n  unacknowledged_additions: block\n"
    )
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--acknowledgments",
            str(tmp_path / "acks.yml"),
        ],
    )
    assert res.exit_code == 0, res.output


def test_flag_overrides_the_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config names a records file that accepts nothing; the flag wins."""
    monkeypatch.chdir(tmp_path)
    old, new = _pair(tmp_path)
    (tmp_path / "config-acks.yml").write_text(_ACK_NOTHING)
    (tmp_path / "flag-acks.yml").write_text(_ACK_ADDED)
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text(
        "acknowledgment:\n  file: config-acks.yml\n  unacknowledged_additions: block\n"
    )
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--config",
            str(cfg),
            "--acknowledgments",
            str(tmp_path / "flag-acks.yml"),
        ],
    )
    assert res.exit_code == 0, res.output


def test_config_key_alone_still_gates_when_the_flag_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the override test: without the flag, the empty file gates."""
    monkeypatch.chdir(tmp_path)
    old, new = _pair(tmp_path)
    (tmp_path / "config-acks.yml").write_text(_ACK_NOTHING)
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text(
        "acknowledgment:\n  file: config-acks.yml\n  unacknowledged_additions: block\n"
    )
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg)]
    )
    assert res.exit_code == 1, res.output


def test_missing_records_file_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _pair(tmp_path)
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--acknowledgments",
            str(tmp_path / "nope.yml"),
        ],
    )
    assert res.exit_code == 64, res.output
    assert "--acknowledgments" in res.output


# --- Action: the `acknowledgments` input ------------------------------------------


def _argv_capture_stub(tmp_path: Path, capture: Path) -> Path:
    """An abicheck that writes its argv, one per line, to *capture*."""
    bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
    stub = bindir / "abicheck"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{capture}"\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell harness")
def test_action_forwards_the_acknowledgments_input_as_the_flag(tmp_path: Path) -> None:
    records = tmp_path / "abi" / "acks.yml"
    records.parent.mkdir()
    records.write_text(_ACK_ADDED)
    capture = tmp_path / "argv.txt"
    bindir = _argv_capture_stub(tmp_path, capture)
    env = {
        **_compare_env(tmp_path),
        "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
        "INPUT_ACKNOWLEDGMENTS": str(records),
    }
    _run_action(tmp_path, env, bindir)
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert "--acknowledgments" in argv
    assert argv[argv.index("--acknowledgments") + 1] == str(records)


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
def test_action_fails_fast_on_a_missing_records_file(tmp_path: Path) -> None:
    """Validated before the Python install, not as a CLI usage error after it."""
    result = _run_validate(
        {
            "INPUT_MODE": "compare",
            "INPUT_ACKNOWLEDGMENTS": str(tmp_path / "missing.yml"),
        }
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "acknowledgments file not found" in result.stdout


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
def test_action_warns_when_acknowledgments_is_set_outside_compare(
    tmp_path: Path,
) -> None:
    records = tmp_path / "acks.yml"
    records.write_text(_ACK_ADDED)
    result = _run_validate(
        {"INPUT_MODE": "dump", "INPUT_ACKNOWLEDGMENTS": str(records)}
    )
    assert "acknowledgments is set but has no effect" in result.stdout
