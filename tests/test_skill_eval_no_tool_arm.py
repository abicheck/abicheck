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

"""The `no_tool` arm: no skill and no abicheck (runners/claude_code.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._workflow_exec import bash_executable, require_bash

EVAL_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills-src"
    / "evaluation"
    / "agents"
    / "skills"
)
sys.path.insert(0, str(EVAL_DIR))
_spec = importlib.util.spec_from_file_location(
    "skill_eval_runner_no_tool", EVAL_DIR / "runners" / "claude_code.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)
_agg = importlib.util.spec_from_file_location(
    "skill_eval_aggregate_no_tool", EVAL_DIR / "run_skill_eval.py"
)
aggregate = importlib.util.module_from_spec(_agg)
_agg.loader.exec_module(aggregate)


def _tool_dir(root: Path, name: str, *, with_tool: bool) -> Path:
    d = root / name
    d.mkdir()
    if with_tool:
        exe = d / "abicheck"
        exe.write_text("#!/bin/sh\necho reached\n", encoding="utf-8")
        exe.chmod(0o755)
    return d


@pytest.mark.skipif(os.name == "nt", reason="executable bit")
@pytest.mark.parametrize("positions", [(0,), (1,), (0, 2), (2,), ()])
def test_every_path_entry_holding_the_tool_is_dropped(tmp_path, positions):
    dirs = [_tool_dir(tmp_path, f"d{i}", with_tool=i in positions) for i in range(3)]
    parent = {
        "PATH": os.pathsep.join(map(str, dirs)),
        "VIRTUAL_ENV": "/v",
        "PYTHONPATH": "/p",
        "HOME": "/h",
    }
    env = runner.no_tool_environment(parent)
    kept = env["PATH"].split(os.pathsep) if env["PATH"] else []
    assert kept == [str(d) for i, d in enumerate(dirs) if i not in positions]
    assert "VIRTUAL_ENV" not in env and "PYTHONPATH" not in env
    assert env["HOME"] == "/h"
    # The oracle is the shell itself, not the function's own scan.
    require_bash()
    found = subprocess.run(
        [
            shutil.which(bash_executable()) or bash_executable(),
            "-c",
            "command -v abicheck || true",
        ],
        env=env,
        capture_output=True,
        text=True,
    ).stdout
    assert found.strip() == ""


def _events(*pairs):
    events = []
    for i, (command, output) in enumerate(pairs):
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"t{i}",
                            "name": "Bash",
                            "input": {"command": command},
                        }
                    ]
                },
            }
        )
        if output is not None:
            events.append(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"t{i}",
                                "content": output,
                            }
                        ]
                    },
                }
            )
    return events


@pytest.mark.parametrize(
    ("command", "output"),
    [
        ("abicheck --version", "bash: abicheck: command not found"),
        (
            "python3 -m abicheck compare a b",
            "/usr/bin/python3: No module named abicheck",
        ),
        # Both observed for real: looking prints nothing that matches.
        (
            "pip list 2>/dev/null | grep -i abi || command -v abicheck || echo none",
            "none",
        ),
        ("nm -D lib.so; which abi-compat abicheck 2>&1 | head", "0000 T widget_area\n"),
        ("readelf -Ws lib.so", [{"type": "text", "text": "Symbol table '.dynsym'"}]),
        ("abicheck compare a b", None),
        ("cat notes.txt", "we should try abicheck later"),
    ],
)
def test_looking_for_the_tool_is_not_a_leak(command, output):
    assert runner.reached_the_tool(_events((command, output))) == []


@pytest.mark.parametrize(
    "output",
    [
        "abicheck 0.6.0 (abicheck/abicheck)",
        " Usage: abicheck [OPTIONS] COMMAND [ARGS]...",
        '{\n  "report_schema_version": "5.10",\n  "verdict": "BREAKING"}',
        "Successfully installed abicheck-0.6.0 pyelftools-0.31",
        [{"type": "text", "text": "abicheck 0.7.1"}],
    ],
)
def test_output_only_a_reachable_tool_produces_is_a_leak(output):
    assert runner.reached_the_tool(_events(("anything", output))) != []


@pytest.mark.parametrize(
    "event",
    [
        "a string",
        {"message": "a string"},
        {"message": {"content": "text"}},
        {"message": {"content": [None, 3]}},
        None,
    ],
)
def test_odd_event_shapes_are_ignored_not_fatal(event):
    assert runner.reached_the_tool([event]) == []


@pytest.mark.parametrize(
    "visible", [["explain-abi-change"], ["check-abi-compatibility"]]
)
def test_the_no_tool_arm_may_not_see_a_skill(visible):
    assert runner.check_treatment("no_tool", {"skill": "explain-abi-change"}, visible)
    assert (
        runner.check_treatment("no_tool", {"skill": "explain-abi-change"}, []) is None
    )


def test_no_tool_is_opt_in():
    assert "no_tool" in runner.ARMS
    assert "no_tool" not in runner.DEFAULT_ARMS
    assert tuple(aggregate.ARM_ORDER) == tuple(runner.ARMS)


def _graded(arm, sid, correct, cost):
    return {
        "arm": arm,
        "scenario_id": sid,
        "correct": correct,
        "comparisons": int(arm != "no_tool"),
        "claim_status": "ok",
        "zero_tolerance_failed": [] if correct else [6],
        "expected_verdict": "BREAKING",
        "expected_cause": "layout_changed",
        "efficiency": {"cost_usd": cost, "wall_clock_seconds": 10.0},
    }


def test_the_table_gets_one_column_per_arm_present(capsys):
    graded = [
        _graded("no_tool", "s", False, 0.1),
        _graded("skill", "s", True, 0.3),
        _graded("baseline", "s", True, 0.2),
        _graded("no_tool", "s", True, 0.1),
    ]
    aggregate._print_table(graded)
    out = capsys.readouterr().out
    header = out.splitlines()[0].split()
    assert header == ["skill", "baseline", "no_tool"]
    correct = next(
        line for line in out.splitlines() if line.startswith("correct answer")
    )
    assert correct.split()[2:] == ["1", "(100%)", "1", "(100%)", "1", "(50%)"]
    per_correct = next(
        line for line in out.splitlines() if line.startswith("cost per correct")
    )
    assert per_correct.split()[-3:] == ["0.300", "0.200", "0.200"]
    assert json.dumps(graded)  # rows stay serializable for --json


def test_a_skill_tree_override_is_what_the_skill_arm_installs(tmp_path, monkeypatch):
    # The variant differs from the published skill in a way the check can
    # see, so an install from the wrong tree cannot pass by coincidence.
    tree = tmp_path / "variant"
    (tree / "explain-abi-change").mkdir(parents=True)
    (tree / "explain-abi-change" / "SKILL.md").write_text(
        "variant marker\n", encoding="utf-8"
    )
    pack = json.loads((EVAL_DIR / "skill-eval-pack.json").read_text(encoding="utf-8"))
    scenario = pack["scenarios"]["explain-missing-symbol"]
    monkeypatch.setattr(runner, "PUBLISHED_SKILLS", tree)
    work = tmp_path / "ws"
    runner._prepare_workspace(work, scenario, "skill")
    installed = work / ".claude" / "skills" / "explain-abi-change" / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == "variant marker\n"
    base = tmp_path / "base"
    runner._prepare_workspace(base, scenario, "baseline")
    assert not (base / ".claude").exists()
