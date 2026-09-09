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

"""Codex review, PR #1180, fresh evidence ("Preserve per-side PDB
selection"): once ``compare --pdb-path`` lost its per-side ``old=``/``new=``
spelling (Phase 7, ``debug.pdb_path`` is its only source now), the
``old_pdb_path or pdb_path`` / ``new_pdb_path or pdb_path`` fallback every
consumer already used resolves to the *identical* file for both sides of a
two-operand compare, since ``old_pdb_path``/``new_pdb_path`` are now
permanently ``None``. ``locate_pdb`` honors an explicit override
unconditionally (no GUID/age check against the binary it's paired with), so
two different PE binaries could silently read the same PDB and report a
false clean result. ``run_compare`` now rejects ``debug.pdb_path`` outright
for a two-operand compare instead.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _write_snapshot(path: Path, *funcs: str) -> None:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(
                name=f, mangled=f, return_type="void",
                visibility=Visibility.PUBLIC,
            )
            for f in funcs
        ],
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


class TestDebugPdbPathRejectedForTwoOperandCompare:
    def test_rejected_with_two_json_snapshots(self, tmp_path: Path) -> None:
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_snapshot(old_path, "foo")
        _write_snapshot(new_path, "foo", "bar")
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("debug:\n  pdb_path: C:/symbols/foo.pdb\n")

        code, out = _invoke(
            "compare", str(old_path), str(new_path),
            "--config", str(config_path), "--format", "json",
        )

        assert code == 64, out
        assert "debug.pdb_path" in out
        assert "--debug-root" in out

    def test_accepted_without_pdb_path_configured(self, tmp_path: Path) -> None:
        """Companion: the rejection must not regress the ordinary case."""
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_snapshot(old_path, "foo", "bar")
        _write_snapshot(new_path, "foo")

        code, _out = _invoke(
            "compare", str(old_path), str(new_path), "--format", "json",
        )

        assert code == 4
