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

"""`skills-src/evaluation/agents/skills/run_skill_eval.py` — grading a batch across run roots.

Parallel scenario subsets are the practical way to run the A/B (each run is
minutes long), so one batch is routinely several `--out` roots. Grading them
together must equal grading the union, and the one-model rule must span
every root — a per-root check would let two roots run under different models
fold into one comparison.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from _skill_eval_graders_fixtures import a_breaking_call, envelope

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "run_skill_eval",
    ROOT / "skills-src" / "evaluation" / "agents" / "skills" / "run_skill_eval.py",
)
run_skill_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_skill_eval)

SID = "removed-export"  # a real pack scenario, expected BREAKING


def _root(tmp_path: Path, name: str, runs: list[tuple[str, int, str, str]]) -> Path:
    """`runs` = (arm, repetition, claimed verdict, model)."""
    root = tmp_path / name
    index = []
    for arm, rep, verdict, model in runs:
        run = root / SID / arm / str(rep)
        (run / "captured").mkdir(parents=True)
        (run / "final.md").write_text(
            envelope(verdict=verdict, evidence=[0], confident=True), encoding="utf-8"
        )
        (run / "calls.jsonl").write_text(json.dumps(a_breaking_call()) + "\n")
        (run / "captured" / "0.out").write_text('{"verdict": "BREAKING"}')
        index.append(
            {"scenario_id": SID, "arm": arm, "repetition": rep, "model": model}
        )
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return root


def test_several_roots_grade_as_their_union(tmp_path):
    a = _root(
        tmp_path,
        "a",
        [("skill", 0, "BREAKING", "m"), ("baseline", 0, "COMPATIBLE", "m")],
    )
    b = _root(
        tmp_path, "b", [("skill", 0, "BREAKING", "m"), ("baseline", 0, "BREAKING", "m")]
    )
    out = tmp_path / "report.json"
    assert (
        run_skill_eval.main(["--runs", str(a), "--runs", str(b), "--json", str(out)])
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    runs = report["runs"]
    assert len(runs) == 4
    assert {r["runs_root"] for r in runs} == {str(a), str(b)}
    by_arm = {
        arm: sorted(r["correct"] for r in runs if r["arm"] == arm)
        for arm in ("skill", "baseline")
    }
    assert by_arm == {"skill": [True, True], "baseline": [False, True]}

    # Grading each root alone and summing agrees with grading them together.
    singles = []
    for root in (a, b):
        single = tmp_path / f"{root.name}.json"
        assert run_skill_eval.main(["--runs", str(root), "--json", str(single)]) == 0
        singles += json.loads(single.read_text(encoding="utf-8"))["runs"]
    key = lambda r: (r["runs_root"], r["arm"], r["repetition"])  # noqa: E731
    assert sorted(singles, key=key) == sorted(runs, key=key)


def test_the_one_model_rule_spans_roots(tmp_path, capsys):
    a = _root(tmp_path, "a", [("skill", 0, "BREAKING", "model-x")])
    b = _root(tmp_path, "b", [("baseline", 0, "BREAKING", "model-y")])
    assert run_skill_eval.main(["--runs", str(a)]) == 0
    assert run_skill_eval.main(["--runs", str(b)]) == 0
    capsys.readouterr()
    assert run_skill_eval.main(["--runs", str(a), "--runs", str(b)]) == 1
    assert "model-x" in capsys.readouterr().err


def test_a_root_without_an_index_is_an_error(tmp_path, capsys):
    a = _root(tmp_path, "a", [("skill", 0, "BREAKING", "m")])
    assert (
        run_skill_eval.main(["--runs", str(a), "--runs", str(tmp_path / "missing")])
        == 1
    )
    assert "no index.json" in capsys.readouterr().err
