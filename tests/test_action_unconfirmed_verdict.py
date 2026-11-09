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

"""The composite Action's mapping for ADR-090's ``UNCONFIRMED`` axis.

Reports come from the real ``compare`` CLI over export-table-only snapshots
(no DWARF, no headers). Under the default ``assurance.unconfirmed: fail`` the
run exits 1 and the Action must publish ``UNCONFIRMED`` -- not
``SEVERITY_ERROR`` or ``RULES_UNUSED``, which the exit-1 dispatch fell through
to before the axis had its own arm. Under ``warn`` the run exits 0 and the
Action must still publish ``UNCONFIRMED``, never launder it into
``COMPATIBLE``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_action_coverage_verdict import (  # noqa: F401
    _lib,
    _run_action,
    _stub_abicheck,
    pytestmark,
)

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot


def _real_report(
    tmp_path: Path, *, mode: str, funcs_new: list[str]
) -> tuple[int, dict]:
    def snap(version: str, names: list[str]) -> AbiSnapshot:
        return AbiSnapshot(
            library="libx.so.1",
            version=version,
            elf_only_mode=True,
            functions=[Function(name=n, mangled=n, return_type="int") for n in names],
        )

    old, new = tmp_path / "o.json", tmp_path / "n.json"
    save_snapshot(snap("1", ["f", "g"]), old)
    save_snapshot(snap("2", funcs_new), new)
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text(f"assurance:\n  unconfirmed: {mode}\n")
    out = tmp_path / "real.json"
    res = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "--config", str(cfg), "-o", f"json={out}"],
    )
    return res.exit_code, json.loads(out.read_text())


def _action_outputs(tmp_path: Path, code: int, report: dict) -> dict:
    bindir = _stub_abicheck(tmp_path, exit_code=code, report=report)
    env = {
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
        "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }
    return _run_action(tmp_path, env, bindir)


def test_fail_mode_publishes_unconfirmed_and_fails_the_step(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, mode="fail", funcs_new=["f", "g"])
    assert code == 1 and report["exit"]["reasons"] == ["unconfirmed_compatibility"]
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "UNCONFIRMED", outputs
    assert outputs["_exit"] == 1, outputs
    assert "Verdict: UNCONFIRMED" in outputs["_summary"]
    for other in ("SEVERITY_ERROR", "RULES_UNUSED", "COMPATIBLE**"):
        assert other not in outputs["_summary"], other


def test_warn_mode_keeps_the_label_and_passes(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, mode="warn", funcs_new=["f", "g"])
    assert code == 0 and report["verdict"] == "UNCONFIRMED"
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "UNCONFIRMED", outputs
    assert outputs["_exit"] == 0, outputs


@pytest.mark.parametrize("mode", ["fail", "warn"])
def test_an_observed_break_is_never_relabelled(tmp_path: Path, mode: str) -> None:
    """Negative control: a symbol removal is visible in the export table, so
    it stays BREAKING/4 under either mode."""
    code, report = _real_report(tmp_path, mode=mode, funcs_new=["f"])
    assert code == 4 and report["verdict"] == "BREAKING"
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "BREAKING", outputs
