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

"""The composite Action's mapping for the unused-rules review axis.

A policy file's ``unused_rules: block`` makes ``compare`` exit ``1`` when a
configured suppression/reclassify rule matched nothing. Before this axis had
its own arm, ``action/run.sh``'s exit-1 dispatch fell through to
``ADDITIONS_UNACKNOWLEDGED`` on the two-sided shape and did not recognize it
on the audit-only shape (``exit_axes.unused_rules``). Two-sided reports are
produced by the real ``compare`` CLI; the audit-only report is a real
``compare --no-baseline`` report with the axis set as its fold emits it.
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

_STALE_RULE = (
    "reclassify:\n"
    "  - symbol: nope\n"
    "    kind: func_removed\n"
    "    to: ignore\n"
    "    reason: stale\n"
)


def _snapshots(tmp_path: Path, *, add: bool) -> tuple[Path, Path]:
    f = Function(name="f", mangled="f", return_type="int")
    g = Function(name="g", mangled="g", return_type="void")
    old, new = tmp_path / "o.json", tmp_path / "n.json"
    save_snapshot(AbiSnapshot(library="libx.so.1", version="1", functions=[f]), old)
    save_snapshot(
        AbiSnapshot(library="libx.so.1", version="2", functions=[f, g] if add else [f]),
        new,
    )
    return old, new


def _real_report(
    tmp_path: Path, *, unused: str, additions: str, add: bool, no_baseline: bool = False
) -> tuple[int, dict]:
    old, new = _snapshots(tmp_path, add=add)
    policy = tmp_path / "policy.yml"
    policy.write_text(f"unused_rules: {unused}\n" + _STALE_RULE)
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text(f"acknowledgment:\n  unacknowledged_additions: {additions}\n")
    out = tmp_path / "real.json"
    operands = ["--no-baseline", str(new)] if no_baseline else [str(old), str(new)]
    res = CliRunner().invoke(
        main,
        [
            "compare",
            *operands,
            "--policy",
            str(policy),
            "--config",
            str(cfg),
            "-o",
            f"json={out}",
        ],
    )
    return res.exit_code, json.loads(out.read_text())


def _action_outputs(
    tmp_path: Path, code: int, report: dict, *, no_baseline: bool = False
) -> dict:
    bindir = _stub_abicheck(tmp_path, exit_code=code, report=report)
    env = {
        "INPUT_MODE": "compare",
        "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }
    if not no_baseline:
        env["INPUT_OLD_LIBRARY"] = _lib(tmp_path, "libold.so")
    return _run_action(tmp_path, env, bindir)


def test_two_sided_unused_rules_alone_is_rules_unused(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, unused="block", additions="allow", add=False)
    assert code == 1 and report["exit"]["reasons"] == ["unused_rules"]
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "RULES_UNUSED", outputs
    assert outputs["_exit"] == 1, outputs
    assert "RULES_UNUSED" in outputs["_summary"]
    assert "ADDITIONS_UNACKNOWLEDGED" not in outputs["_summary"]
    assert "SEVERITY_ERROR" not in outputs["_summary"]


def test_additions_alone_is_still_additions_unacknowledged(tmp_path: Path) -> None:
    """Negative control: the additions axis keeps its own verdict."""
    code, report = _real_report(tmp_path, unused="allow", additions="block", add=True)
    assert code == 1 and report["exit"]["reasons"] == ["additions_review"]
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "ADDITIONS_UNACKNOWLEDGED", outputs
    assert "RULES_UNUSED" not in outputs["_summary"]


def test_co_firing_keeps_additions_and_also_reports_unused_rules(
    tmp_path: Path,
) -> None:
    code, report = _real_report(tmp_path, unused="block", additions="block", add=True)
    assert code == 1
    assert sorted(report["exit"]["reasons"]) == ["additions_review", "unused_rules"]
    outputs = _action_outputs(tmp_path, code, report)
    assert outputs["verdict"] == "ADDITIONS_UNACKNOWLEDGED", outputs
    assert outputs["_exit"] == 1, outputs
    assert "also reports policy rules that matched nothing" in outputs["_stdout"], (
        outputs
    )


@pytest.mark.parametrize("flag", [1, 0])
def test_audit_only_unused_rules_axis(tmp_path: Path, flag: int) -> None:
    code, report = _real_report(
        tmp_path, unused="block", additions="allow", add=False, no_baseline=True
    )
    assert report["no_baseline"] is True and "exit_axes" in report
    # The axis as `_no_baseline_exit_axes` emits it, and the exit it folds to.
    report["exit_axes"]["unused_rules"] = flag
    outputs = _action_outputs(tmp_path, flag, report, no_baseline=True)
    if flag:
        assert outputs["verdict"] == "RULES_UNUSED", outputs
        assert outputs["_exit"] == 1, outputs
    else:
        assert outputs["verdict"] != "RULES_UNUSED", outputs
        assert outputs["_exit"] == 0, outputs


@pytest.mark.parametrize(
    "hostile",
    ["1", "$(touch PWNED)", "1; touch PWNED", True, 1.5, 2, -1, None, [1], {"x": 1}],
)
def test_a_malformed_axis_value_never_gates_and_never_executes(
    tmp_path: Path, hostile: object
) -> None:
    """A report is data a PR-controlled build can shape: only an integer 0/1
    is an answer. Anything else reads as "cannot tell", is never mapped to
    RULES_UNUSED, and is never evaluated by the shell."""
    code, report = _real_report(
        tmp_path, unused="block", additions="allow", add=False, no_baseline=True
    )
    report["exit_axes"]["unused_rules"] = hostile
    outputs = _action_outputs(tmp_path, 1, report, no_baseline=True)
    assert outputs["verdict"] != "RULES_UNUSED", outputs
    assert not list(tmp_path.rglob("PWNED"))
    assert not (Path.cwd() / "PWNED").exists()
