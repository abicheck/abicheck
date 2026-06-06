# Copyright 2026 Nikolay Petrov
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

"""CLI tests for the ``plugin-check`` host-contract command (gap G5).

Driven entirely through committed JSON snapshots so they run in the fast
(pure-Python) suite — no plugin binaries or toolchain required.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _plugin_snapshot(tmp_path: Path, name: str, version: str, symbols: list[str]) -> Path:
    snap = AbiSnapshot(
        library="libplugin.so",
        version=version,
        functions=[
            Function(name=s, mangled=s, return_type="int", visibility=Visibility.PUBLIC)
            for s in symbols
        ],
    )
    path = tmp_path / name
    path.write_text(snapshot_to_json(snap))
    return path


def test_plugin_check_breaks_on_dropped_entrypoint(tmp_path: Path) -> None:
    old = _plugin_snapshot(tmp_path, "p1.json", "1.0", ["plugin_init", "plugin_run", "aux"])
    new = _plugin_snapshot(tmp_path, "p2.json", "2.0", ["plugin_init", "aux"])  # drops plugin_run

    result = CliRunner().invoke(main, [
        "plugin-check", str(old), str(new), "-r", "plugin_init", "-r", "plugin_run",
    ])
    assert result.exit_code == 4, result.output
    assert "BREAKING" in result.output
    assert "plugin_run" in result.output


def test_plugin_check_compatible_when_contract_satisfied(tmp_path: Path) -> None:
    old = _plugin_snapshot(tmp_path, "p1.json", "1.0", ["plugin_init", "plugin_run"])
    # Drops an auxiliary symbol the host never resolves → host is safe.
    new = _plugin_snapshot(tmp_path, "p2.json", "2.0", ["plugin_init", "plugin_run"])

    result = CliRunner().invoke(main, [
        "plugin-check", str(old), str(new), "-r", "plugin_init", "-r", "plugin_run",
    ])
    assert result.exit_code == 0, result.output
    assert "COMPATIBLE" in result.output


def test_plugin_check_json_output_and_host_contract_file(tmp_path: Path) -> None:
    old = _plugin_snapshot(tmp_path, "p1.json", "1.0", ["plugin_init", "plugin_run", "aux"])
    new = _plugin_snapshot(tmp_path, "p2.json", "2.0", ["plugin_init", "aux"])
    contract = tmp_path / "host.syms"
    contract.write_text("plugin_init\nplugin_run  # core entrypoint\n# a comment line\n\n")

    result = CliRunner().invoke(main, [
        "plugin-check", str(old), str(new),
        "--host-contract", str(contract), "--format", "json",
    ])
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "BREAKING"
    assert payload["missing_entrypoints"] == ["plugin_run"]
    assert sorted(payload["required_entrypoints"]) == ["plugin_init", "plugin_run"]
    assert payload["coverage"] == 50.0


def test_plugin_check_writes_output_file(tmp_path: Path) -> None:
    old = _plugin_snapshot(tmp_path, "p1.json", "1.0", ["plugin_init", "plugin_run"])
    new = _plugin_snapshot(tmp_path, "p2.json", "2.0", ["plugin_init", "plugin_run"])
    out = tmp_path / "report.md"

    result = CliRunner().invoke(main, [
        "plugin-check", str(old), str(new), "-r", "plugin_init", "-o", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "host-contract" in out.read_text().lower()
    assert "Report written to" in result.output


def test_plugin_check_requires_entrypoints(tmp_path: Path) -> None:
    old = _plugin_snapshot(tmp_path, "p1.json", "1.0", ["plugin_init"])
    new = _plugin_snapshot(tmp_path, "p2.json", "2.0", ["plugin_init"])

    result = CliRunner().invoke(main, ["plugin-check", str(old), str(new)])
    assert result.exit_code != 0
    assert "No required entrypoints" in result.output
