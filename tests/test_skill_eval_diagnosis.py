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

"""The ABI-change explanation contract (`explain-abi-change`, ADR-058).

The skill names one mechanism from a closed vocabulary; the evaluation
grades it. Five places spell that vocabulary — the grader, the claim schema,
the scenario schema, and the skill's own two cause tables — and they must
agree, or the skill is graded against causes it was never told about.

The fixtures' ground truth is checked against the real toolchain and the real
`abicheck` in the `integration` lane: every fixture must reproduce its symptom
and compare to the verdict its scenario states. A fixture that silently stops
reproducing would otherwise keep grading agents against a problem that no
longer exists.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "skills-src" / "evaluation" / "agents" / "skills"
SKILL_DIR = ROOT / "skills-src" / "explain-abi-change"
sys.path.insert(0, str(EVAL_DIR))

from graders import claim as claim_mod  # noqa: E402
from graders.dimensions import grade_run  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "skill_eval_runner_diag", EVAL_DIR / "runners" / "claude_code.py"
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

SCENARIOS = [
    s
    for s in yaml.safe_load((EVAL_DIR / "scenarios.yaml").read_text(encoding="utf-8"))[
        "scenarios"
    ]
    if s["skill"] == "explain-abi-change"
]
PACK = json.loads((EVAL_DIR / "skill-eval-pack.json").read_text(encoding="utf-8"))


class TestOneVocabulary:
    def test_claim_schema_matches_the_grader(self):
        schema = json.loads(
            (EVAL_DIR / "schema" / "claim.schema.json").read_text(encoding="utf-8")
        )
        enum = schema["properties"]["diagnosis"]["properties"]["cause"]["enum"]
        assert set(enum) == claim_mod.DIAGNOSIS_CAUSES

    def test_scenario_schema_matches_the_grader(self):
        schema = json.loads(
            (EVAL_DIR / "schema" / "scenario.schema.json").read_text(encoding="utf-8")
        )
        enum = schema["$defs"]["scenario"]["properties"]["expected"]["properties"][
            "cause"
        ]["enum"]
        assert set(enum) == claim_mod.DIAGNOSIS_CAUSES

    @pytest.mark.parametrize(
        "path",
        [SKILL_DIR / "SKILL.md", SKILL_DIR / "references" / "diagnostics.md"],
        ids=lambda p: p.name,
    )
    def test_each_skill_cause_table_names_exactly_the_vocabulary(self, path):
        # Every row whose first cell is a backticked snake_case word, not
        # filtered by the vocabulary: a cause the skill invents must fail.
        text = path.read_text(encoding="utf-8")
        rows = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", text, re.MULTILINE))
        causes = {r for r in rows if not r.startswith(("func_", "var_", "type_"))}
        assert claim_mod.DIAGNOSIS_CAUSES <= causes
        assert causes - claim_mod.DIAGNOSIS_CAUSES <= set(), (
            f"{path.name} names causes the grader does not know: "
            f"{sorted(causes - claim_mod.DIAGNOSIS_CAUSES)}"
        )

    def test_every_cause_has_a_scenario_except_none(self):
        covered = {s["expected"]["cause"] for s in SCENARIOS}
        assert covered == set(claim_mod.DIAGNOSIS_CAUSES)


def _run(tmp_path: Path, envelope: dict | None, *, calls: int = 1) -> Path:
    run = tmp_path / "run"
    (run / "captured").mkdir(parents=True)
    body = "Found it.\n"
    if envelope is not None:
        body += "\n```json\n" + json.dumps(envelope) + "\n```\n"
    (run / "final.md").write_text(body, encoding="utf-8")
    lines = []
    for seq in range(calls):
        (run / "captured" / f"{seq}.out").write_text(
            json.dumps({"verdict": "BREAKING"}), encoding="utf-8"
        )
        lines.append(
            json.dumps(
                {
                    "seq": seq,
                    "call_id": f"c{seq}",
                    "argv": ["compare", "a.so", "b.so", "-o", "json=-"],
                    "exit_code": 4,
                    "stdout_path": f"captured/{seq}.out",
                    "outputs": [],
                }
            )
        )
    (run / "calls.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


DEBUG = {
    "skill": "explain-abi-change",
    "expected": {"verdict": "BREAKING", "cause": "stale_library_loaded"},
}
REVIEW = {"skill": "check-abi-compatibility", "expected": {"verdict": "BREAKING"}}


class TestGradingRequiresTheCause:
    """The same BREAKING comparison is the evidence for a removed symbol and
    for a stale copy winning the search, which need opposite fixes; a verdict
    alone must not score a diagnosis as correct."""

    @pytest.mark.parametrize(
        "wrong", sorted(claim_mod.DIAGNOSIS_CAUSES - {"stale_library_loaded"})
    )
    def test_right_verdict_wrong_cause_is_incorrect(self, tmp_path, wrong):
        env = {
            "verdict": "BREAKING",
            "evidence": [0],
            "confident": True,
            "diagnosis": {"cause": wrong},
        }
        assert grade_run(_run(tmp_path, env), DEBUG, "skill")["correct"] is False

    def test_right_verdict_and_cause_is_correct(self, tmp_path):
        env = {
            "verdict": "BREAKING",
            "evidence": [0],
            "confident": True,
            "diagnosis": {"cause": "stale_library_loaded"},
        }
        graded = grade_run(_run(tmp_path, env), DEBUG, "skill")
        assert graded["correct"] is True
        assert (
            graded["claimed_cause"]
            == graded["expected_cause"]
            == "stale_library_loaded"
        )

    @pytest.mark.parametrize("verdict", ["NO_CHANGE", "COMPATIBLE", "API_BREAK"])
    def test_right_cause_wrong_verdict_is_incorrect(self, tmp_path, verdict):
        env = {
            "verdict": verdict,
            "evidence": [0],
            "confident": True,
            "diagnosis": {"cause": "stale_library_loaded"},
        }
        assert grade_run(_run(tmp_path, env), DEBUG, "skill")["correct"] is False

    def test_a_missing_diagnosis_is_incorrect(self, tmp_path):
        env = {"verdict": "BREAKING", "evidence": [0], "confident": True}
        assert grade_run(_run(tmp_path, env), DEBUG, "skill")["correct"] is False

    def test_a_review_scenario_ignores_diagnosis(self, tmp_path):
        env = {"verdict": "BREAKING", "evidence": [0], "confident": True}
        assert grade_run(_run(tmp_path, env), REVIEW, "skill")["correct"] is True


class TestClaimValidation:
    @pytest.mark.parametrize(
        "diagnosis",
        [{"cause": "made_up"}, {}, "stale_library_loaded", {"cause": None}, None],
    )
    def test_a_malformed_diagnosis_is_not_a_gradeable_claim(self, diagnosis):
        claim = {
            "verdict": "BREAKING",
            "evidence": [0],
            "confident": True,
            "diagnosis": diagnosis,
        }
        assert claim_mod.validate(claim) is not None

    @pytest.mark.parametrize("cause", sorted(claim_mod.DIAGNOSIS_CAUSES))
    def test_every_vocabulary_cause_validates(self, cause):
        claim = {
            "verdict": "BREAKING",
            "evidence": [0],
            "confident": True,
            "diagnosis": {"cause": cause},
        }
        assert claim_mod.validate(claim) is None


class TestAnswerContract:
    def test_debug_scenarios_ask_for_the_diagnosis(self):
        for scenario in SCENARIOS:
            text = runner.answer_contract(PACK["scenarios"][scenario["id"]])
            assert '"diagnosis"' in text
            for cause in claim_mod.DIAGNOSIS_CAUSES:
                assert cause in text

    def test_review_scenarios_do_not(self):
        review = [
            s
            for s in PACK["scenarios"].values()
            if s.get("skill") == "check-abi-compatibility"
        ]
        assert review
        for scenario in review:
            assert runner.answer_contract(scenario) == runner.ANSWER_CONTRACT


class TestFixtures:
    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
    def test_the_prepared_workspace_leaks_nothing(self, tmp_path, scenario):
        work = tmp_path / "ws"
        runner._prepare_workspace(work, PACK["scenarios"][scenario["id"]], "baseline")
        assert runner.workspace_leaks(work) == []
        assert (work / "library" / "setup.sh").is_file()

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
    def test_the_prompt_names_neither_the_tool_nor_the_answer(self, scenario):
        prompt = scenario["prompt"].lower()
        assert "abicheck" not in prompt
        for word in (*claim_mod.VERDICT_ORDER, *claim_mod.DIAGNOSIS_CAUSES):
            assert word.lower() not in prompt


def _loaded_library(fixture: Path) -> tuple[Path, Path | None]:
    """(the library the real loader resolves, the headers it was built from).

    Asks the dynamic loader itself (`LD_TRACE_LOADED_OBJECTS`), through the
    fixture's own launcher, rather than re-deriving the search order here —
    so an RPATH that beats `LD_LIBRARY_PATH` is followed exactly as it is at
    run time, and the oracle does not share any logic with abicheck's
    `deps tree`. A binary-only library has no headers (`None`).
    """
    # Set on the program's own exec, not the launcher: exported to the
    # launcher, it would trace /bin/sh instead.
    launcher = (fixture / "env" / "run.sh").read_text(encoding="utf-8")
    assert launcher.count('exec "') == 1
    tracer = fixture / "env" / "trace.sh"
    tracer.write_text(
        launcher.replace('exec "', 'LD_TRACE_LOADED_OBJECTS=1 exec "'), encoding="utf-8"
    )
    traced = subprocess.run(
        ["sh", str(tracer)], capture_output=True, text=True, check=True
    ).stdout
    loaded = Path(re.search(r"libwidget\.so\.1 => (\S+)", traced).group(1))
    where = loaded.parent.relative_to(fixture / "env").as_posix()
    source = {
        "legacy": "legacy",
        "opt/widget/lib": "installed",
        "lib": "old" if (fixture / "old").is_dir() else "installed",
    }.get(where)
    header = fixture / source / "widget.h" if source else None
    return loaded, header if header and header.is_file() else None


@pytest.mark.integration
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF fixtures")
@pytest.mark.skipif(
    shutil.which("cc") is None or shutil.which("c++") is None, reason="needs cc/c++"
)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_fixture_reproduces_its_symptom_and_ground_truth(tmp_path, scenario):
    fixture = tmp_path / scenario["id"]
    shutil.copytree(
        ROOT / scenario["fixture"], fixture, ignore=shutil.ignore_patterns("env")
    )
    subprocess.run(["sh", str(fixture / "setup.sh")], check=True, capture_output=True)
    ran = subprocess.run(
        [str(fixture / "env" / "run.sh")], capture_output=True, text=True
    )
    cause = scenario["expected"]["cause"]
    output = ran.stdout + ran.stderr
    if cause == "layout_changed":
        assert ran.returncode == 0 and "area=12" not in output
    elif cause == "symbol_version_missing":
        assert ran.returncode != 0 and "version `WIDGET_2.0' not found" in output
    elif (
        cause == "not_an_abi_problem" and scenario["expected"]["verdict"] != "NO_CHANGE"
    ):
        # A library update the program does not notice: it runs normally.
        assert ran.returncode == 0 and "size=" in output
    elif cause == "not_an_abi_problem":
        assert ran.returncode != 0 and "resize failed" in output
    else:
        assert ran.returncode != 0 and "undefined symbol" in output

    loaded, loaded_header = _loaded_library(fixture)
    report = tmp_path / "compare.json"
    subprocess.run(
        [
            "abicheck",
            "compare",
            str(fixture / "env" / "sdk" / "libwidget.so.1"),
            str(loaded),
            "--header",
            f"old={fixture / 'sdk' / 'widget.h'}",
            *(["--header", f"new={loaded_header}"] if loaded_header else []),
            "-o",
            f"json={report}",
        ],
        capture_output=True,
    )
    assert (
        json.loads(report.read_text(encoding="utf-8"))["verdict"]
        == scenario["expected"]["verdict"]
    )
    if scenario["expected"]["cause"] == "library_older_than_build":
        # What separates it from `symbol_removed`: the used copy is an older
        # release, so compared the other way round the build's library only
        # adds to it.
        reverse = tmp_path / "reverse.json"
        subprocess.run(
            [
                "abicheck",
                "compare",
                str(loaded),
                str(fixture / "env" / "sdk" / "libwidget.so.1"),
                *(["--header", f"old={loaded_header}"] if loaded_header else []),
                "--header",
                f"new={fixture / 'sdk' / 'widget.h'}",
                "-o",
                f"json={reverse}",
            ],
            capture_output=True,
        )
        assert (
            json.loads(reverse.read_text(encoding="utf-8"))["verdict"] == "COMPATIBLE"
        )
