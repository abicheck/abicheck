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

"""The composite Action's mapping for ADR-067 D6's additions-review axis.

A ``compare`` gated only by unacknowledged public additions exits ``1``.
Without its own arm, ``action/run.sh``'s exit-1 dispatch would read that as a
severity-policy failure. The report each case replays is produced by the
real ``compare`` CLI (not hand-written), so the Action is checked against
what the emitter actually writes.
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
from abicheck.model import AbiSnapshot, Function, Param
from abicheck.serialization import save_snapshot


def _real_report(tmp_path: Path, *, action: str, break_it: bool) -> tuple[int, dict]:
    f = Function(
        name="f", mangled="f", return_type="int", params=[Param(name="a", type="int")]
    )
    f2 = Function(
        name="f", mangled="f", return_type="long", params=[Param(name="a", type="int")]
    )
    g = Function(name="g", mangled="g", return_type="void")
    old, new = tmp_path / "o.json", tmp_path / "n.json"
    save_snapshot(AbiSnapshot(library="libx.so.1", version="1", functions=[f]), old)
    save_snapshot(
        AbiSnapshot(
            library="libx.so.1", version="2", functions=[f2 if break_it else f, g]
        ),
        new,
    )
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text(f"acknowledgment:\n  unacknowledged_additions: {action}\n")
    out = tmp_path / "real.json"
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg), "-o", f"json={out}"]
    )
    return res.exit_code, json.loads(out.read_text())


def _action_outputs(tmp_path: Path, code: int, report: dict, **extra: str) -> dict:
    bindir = _stub_abicheck(tmp_path, exit_code=code, report=report)
    return _run_action(
        tmp_path,
        {
            **extra,
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": "json",
            "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
        },
        bindir,
    )


def test_block_alone_is_its_own_verdict_and_fails_the_step(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, action="block", break_it=False)
    assert code == 1 and report["exit"]["reasons"] == ["additions_review"]
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "ADDITIONS_UNACKNOWLEDGED", outputs
    assert outputs["_exit"] == 1, outputs
    assert "ADDITIONS_UNACKNOWLEDGED" in outputs["_summary"]
    assert "SEVERITY_ERROR" not in outputs["_summary"]


@pytest.mark.parametrize("action", ["allow", "warn"])
def test_non_blocking_settings_pass(tmp_path: Path, action: str) -> None:
    code, report = _real_report(tmp_path, action=action, break_it=False)
    assert code == 0
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] != "ADDITIONS_UNACKNOWLEDGED", outputs
    assert outputs["_exit"] == 0, outputs


def test_a_real_break_keeps_its_verdict_and_still_reports_the_axis(
    tmp_path: Path,
) -> None:
    code, report = _real_report(tmp_path, action="block", break_it=True)
    assert code == 4
    assert report["exit"]["additions_review_contribution"] == 1
    # fail-on-breaking off, so only the orthogonal axis can fail the step.
    outputs = _action_outputs(tmp_path, code, report, INPUT_FAIL_ON_BREAKING="false")
    assert outputs["verdict"] == "BREAKING", outputs
    assert outputs["_exit"] == 1, outputs


@pytest.mark.parametrize(
    "hostile",
    ["1", True, 1.0, 2, "1\nverdict=COMPATIBLE", ["1"], {"v": 1}],
    ids=["str", "bool", "float", "two", "newline-injection", "list", "dict"],
)
def test_a_malformed_contribution_neither_gates_nor_injects_outputs(
    tmp_path: Path, hostile: object
) -> None:
    """A report whose ``additions_review_contribution`` is anything but the
    integer ``0``/``1`` the schema allows is "cannot tell": it must not gate
    the step, and a newline in it must not reach ``$GITHUB_OUTPUT`` as an
    extra record."""
    code, report = _real_report(tmp_path, action="allow", break_it=False)
    report["exit"]["additions_review_contribution"] = hostile
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] != "ADDITIONS_UNACKNOWLEDGED", outputs
    assert outputs["_exit"] == 0, outputs
    assert "additions" not in outputs["_summary"].lower(), outputs
