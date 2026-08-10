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

"""The deterministic skill-eval graders, and the bad bundles they must catch.

Two halves. The first pins each grader's rule directly. The second is a golden
corpus of *bad* runs — the transcript shapes a broken agent or a broken harness
actually produces — because a grader that only ever sees good input passes for
the wrong reason, which is the failure this whole evaluation exists to avoid.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agent-evals" / "skills"
sys.path.insert(0, str(EVAL_DIR))

from graders import (  # noqa: E402
    claim as claim_mod,
    dimensions as dim,
    evidence as ev,
)


def _load_script(path: Path, name: str):
    """Import a file whose name is not a Python module name (the shim)."""
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shim = _load_script(EVAL_DIR / "shim" / "abicheck", "skill_eval_shim")
runner = _load_script(EVAL_DIR / "runners" / "claude_code.py", "skill_eval_runner")


SCENARIO_BREAKING = {
    "skill": "native-binary-compatibility-review",
    "expected": {"verdict": "BREAKING"},
}
SCENARIO_COMPATIBLE = {
    "skill": "native-binary-compatibility-review",
    "expected": {"verdict": "COMPATIBLE"},
}


def build_run(
    tmp_path: Path,
    *,
    final: str,
    calls: list[dict] | None = None,
    artifacts: dict[str, str] | None = None,
    events: list[dict] | None = None,
) -> Path:
    """A run directory shaped exactly the way the shim and runner write one."""
    run = tmp_path / "run"
    (run / "captured").mkdir(parents=True)
    run.joinpath("final.md").write_text(final, encoding="utf-8")
    for rel, text in (artifacts or {}).items():
        target = run / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    lines = [json.dumps(c) for c in (calls or [])]
    run.joinpath("calls.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )
    if events is not None:
        run.joinpath("events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8"
        )
    return run


def a_breaking_call(seq: int = 0, argv: list[str] | None = None) -> dict:
    return {
        "seq": seq,
        "call_id": f"c{seq}",
        "argv": argv or ["compare", "old.so", "new.so", "--format", "json"],
        "exit_code": 4,
        "stdout_path": f"captured/{seq}.out",
        "outputs": [],
    }


def envelope(**fields) -> str:
    return "Here is the answer.\n\n```json\n" + json.dumps(fields) + "\n```\n"


class TestClaimExtraction:
    def test_a_well_formed_envelope_is_extracted(self):
        parsed, status = claim_mod.extract(
            envelope(verdict="BREAKING", evidence=[0], confident=True)
        )
        assert status == "ok"
        assert parsed["verdict"] == "BREAKING"

    def test_no_envelope_at_all_is_absent(self):
        parsed, status = claim_mod.extract("This library is definitely broken.")
        assert (parsed, status) == (None, "absent")

    def test_two_envelopes_are_ambiguous_rather_than_first_wins(self):
        text = envelope(verdict="COMPATIBLE", evidence=[0], confident=True) + envelope(
            verdict="BREAKING", evidence=[0], confident=True
        )
        parsed, status = claim_mod.extract(text)
        assert (parsed, status) == (None, "ambiguous")

    def test_a_quoted_report_is_not_a_second_envelope(self):
        """Citing the report you rest on must not fail the strictest dimension."""
        quoted = '```json\n{"verdict": "BREAKING", "changes": []}\n```\n\n'
        parsed, status = claim_mod.extract(
            quoted + envelope(verdict="BREAKING", evidence=[0], confident=True)
        )
        assert status == "ok"
        assert parsed["evidence"] == [0]

    def test_a_verdict_outside_an_envelope_is_reported_as_such(self):
        parsed, status = claim_mod.extract('```json\n{"verdict": "BREAKING"}\n```')
        assert parsed is None
        assert status.startswith("invalid")

    @pytest.mark.parametrize(
        "fields",
        [
            {"verdict": "TOTALLY_FINE", "evidence": [0], "confident": True},
            {"verdict": "BREAKING", "evidence": [0], "confident": "yes"},
            {"verdict": "BREAKING", "evidence": ["zero"], "confident": True},
            {"verdict": "BREAKING", "evidence": [-1], "confident": True},
            {"verdict": None, "evidence": [], "confident": False},
            {
                "verdict": None,
                "evidence": [],
                "confident": False,
                "uncertainty": {"reason": "vibes", "unresolved": "x"},
            },
            {
                "verdict": None,
                "evidence": [],
                "confident": False,
                "uncertainty": {"reason": "not_comparable", "unresolved": "  "},
            },
        ],
    )
    def test_malformed_envelopes_are_rejected(self, fields):
        parsed, status = claim_mod.extract(envelope(**fields))
        assert parsed is None
        assert status.startswith("invalid")

    def test_an_omitted_evidence_field_is_not_an_empty_list(self):
        """Schema-required. Defaulting it let a `null`-verdict envelope omit it
        and grade clean, because dimension 6 checks evidence only for a stated
        verdict."""
        parsed, status = claim_mod.extract(
            envelope(
                verdict=None,
                confident=False,
                uncertainty={"reason": "not_comparable", "unresolved": "no contract"},
            )
        )
        assert parsed is None
        assert "evidence is missing" in status

    @pytest.mark.parametrize(
        "matrix",
        [
            {"targets": [1]},
            {"targets": "all"},
            {"targets": [{"id": "x"}]},
            "everything",
        ],
    )
    def test_a_malformed_matrix_is_an_invalid_claim_not_a_crash(self, matrix):
        """It used to raise inside a grader and abort the whole batch instead of
        failing the one run that produced it."""
        parsed, status = claim_mod.extract(
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={"reason": "matrix_target_unrun", "unresolved": "linux"},
                matrix=matrix,
            )
        )
        assert parsed is None
        assert status.startswith("invalid")

    def test_a_well_formed_matrix_is_accepted(self):
        parsed, status = claim_mod.extract(
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={"reason": "matrix_target_unrun", "unresolved": "linux"},
                matrix={"targets": [{"id": "linux", "state": "not_run"}]},
            )
        )
        assert status == "ok"
        assert parsed["matrix"]["targets"][0]["state"] == "not_run"

    def test_a_confident_null_verdict_is_rejected(self):
        """`{"verdict": null, "evidence": [], "confident": true}` skipped
        dimension 6's evidence block (nothing claimed) *and* read as
        not_applicable in dimension 2 (it was confident), so a run that
        compared nothing graded clean against a BREAKING scenario."""
        parsed, status = claim_mod.extract(
            envelope(verdict=None, evidence=[], confident=True)
        )
        assert parsed is None
        assert "statement of uncertainty" in status

    def test_a_caveated_null_verdict_is_still_accepted(self):
        parsed, status = claim_mod.extract(
            envelope(
                verdict=None,
                evidence=[0],
                confident=False,
                uncertainty={"reason": "not_comparable", "unresolved": "no contract"},
            )
        )
        assert status == "ok"
        assert parsed["verdict"] is None

    def test_the_severity_ordinal_is_least_to_most_severe(self):
        ranks = [claim_mod.rank(v) for v in claim_mod.VERDICT_ORDER]
        assert ranks == sorted(ranks)
        assert claim_mod.rank("COMPATIBLE") < claim_mod.rank("BREAKING")
        assert claim_mod.rank(None) is None


class TestEvidenceReading:
    def test_the_verb_is_found_past_global_flags(self):
        assert ev.subcommand({"argv": ["-v", "compare", "a", "b"]}) == "compare"
        assert ev.subcommand({"argv": ["--log-level", "debug", "dump", "a"]}) == "dump"
        assert ev.subcommand({"argv": []}) is None

    def test_dump_alone_is_not_a_comparison(self):
        assert not ev.is_comparison({"argv": ["dump", "old.so"]})
        assert ev.is_comparison({"argv": ["compare", "a", "b"]})

    @pytest.mark.parametrize("code", [64, 70, 127, None])
    def test_a_call_that_never_reached_a_verdict_is_not_evidence(self, code):
        assert not ev.ran_to_a_verdict(
            {"argv": ["compare", "a", "b"], "exit_code": code}
        )

    @pytest.mark.parametrize("code", [0, 2, 4])
    def test_a_real_comparison_exit_counts(self, code):
        assert ev.ran_to_a_verdict({"argv": ["compare", "a", "b"], "exit_code": code})

    def test_scan_without_against_is_a_one_build_audit_not_a_comparison(self):
        """The CLI's own help: absence of --against already means a one-build audit."""
        assert not ev.is_comparison({"argv": ["scan", "libfoo.so", "--sources", "."]})
        assert ev.is_comparison(
            {"argv": ["scan", "libfoo.so", "--against", "old.abi.json"]}
        )
        assert ev.is_comparison({"argv": ["scan", "libfoo.so", "--against=old.json"]})

    def test_compat_dump_creates_a_snapshot_rather_than_comparing(self):
        assert not ev.is_comparison({"argv": ["compat", "dump", "-lib", "foo"]})
        assert ev.is_comparison({"argv": ["compat", "check", "-lib", "foo"]})

    def test_bare_compat_is_the_drop_in_check(self):
        """`abicheck compat -lib foo -old v1.xml` auto-invokes `check`."""
        assert ev.is_comparison({"argv": ["compat", "-lib", "foo", "-old", "v1.xml"]})

    @pytest.mark.parametrize(
        ("argv", "code"),
        [
            (["scan", "a.so", "--against", "b.json"], 5),  # --budget overflow
            (["compat", "check", "-lib", "foo"], 5),  # tool/input failure
            (["compat", "check", "-lib", "foo"], 8),
        ],
    )
    def test_a_failure_exit_is_not_a_verdict_for_that_command(self, argv, code):
        assert not ev.ran_to_a_verdict({"argv": argv, "exit_code": code})

    def test_fail_on_removed_library_is_a_real_compare_verdict(self):
        assert ev.ran_to_a_verdict({"argv": ["compare", "a", "b"], "exit_code": 8})

    def test_not_comparable_is_an_outcome_but_not_a_verdict(self):
        call = {"argv": ["scan", "a.so", "--against", "b.json"], "exit_code": 6}
        assert ev.determined_not_comparable(call)
        assert not ev.ran_to_a_verdict(call)

    def test_every_real_severity_override_is_treated_as_re_scoring(self):
        """There is no generic `--severity`; a stem matched none of these."""
        for flag in (
            "--severity-abi-breaking",
            "--severity-potential-breaking",
            "--severity-quality-issues",
            "--severity-addition",
        ):
            call = {"argv": ["compare", "a", "b", flag, "error"]}
            assert ev.suppression_flags(call) == [flag], flag

    @pytest.mark.parametrize("flag", ["--help", "-h", "--dry-run"])
    def test_a_non_executing_mode_is_not_a_comparison(self, flag):
        """`compare --help` exits 0 and `--dry-run` "never returns a verdict
        code" — both looked clean to an exit-code check, so a guessed verdict
        could cite one and satisfy every evidence rule."""
        call = {"argv": ["compare", "a", "b", flag], "exit_code": 0}
        assert not ev.is_comparison(call)
        assert not ev.ran_to_a_verdict(call)

    @pytest.mark.parametrize(
        ("argv", "code"),
        [
            (["compare", "a", "b"], 16),
            (["scan", "a.so", "--against", "b.json"], 6),
            (["compat", "check", "-lib", "foo"], 9),
        ],
    )
    def test_each_command_has_its_own_not_comparable_exit(self, argv, code):
        """Each maintains an independent scheme; recognizing only `scan`'s made
        a correct not-comparable run on the others read as no comparison."""
        call = {"argv": argv, "exit_code": code}
        assert ev.determined_not_comparable(call)
        assert not ev.ran_to_a_verdict(call)

    def test_comparing_one_operand_against_itself_is_detected(self):
        assert ev.compares_one_side_against_itself(
            {"argv": ["compare", "v1.so", "v1.so"]}
        )
        assert not ev.compares_one_side_against_itself(
            {"argv": ["compare", "v1.so", "v2.so"]}
        )

    def test_a_repeated_option_value_is_not_mistaken_for_a_self_comparison(self):
        """Only *adjacent equal* non-flag tokens count, so a value repeated
        elsewhere in the command line does not fail a correct run."""
        assert not ev.compares_one_side_against_itself(
            {"argv": ["compare", "v1.so", "v2.so", "--format", "json", "-o", "json"]}
        )

    def test_suppression_flags_are_seen_in_both_spellings(self):
        call = {"argv": ["compare", "a", "b", "--suppress", "x", "--policy-file=y"]}
        assert ev.suppression_flags(call) == ["--policy-file", "--suppress"]

    def test_the_verdict_is_read_out_of_a_json_report(self, tmp_path):
        run = build_run(
            tmp_path,
            final="",
            calls=[a_breaking_call()],
            artifacts={"captured/0.out": json.dumps({"verdict": "API_BREAK"})},
        )
        assert ev.reported_verdict(run, a_breaking_call()) == "API_BREAK"

    def test_a_markdown_legend_does_not_become_the_verdict(self, tmp_path):
        """The default report ends with a legend naming every verdict. Scanning
        for the most severe token read `tests/golden/compatible_addition.md` as
        BREAKING, so dimension 6 rejected a correct compatible claim as "safer
        than the run's own report"."""
        golden = (ROOT / "tests" / "golden" / "compatible_addition.md").read_text(
            encoding="utf-8"
        )
        run = build_run(
            tmp_path,
            final="",
            calls=[a_breaking_call()],
            artifacts={"captured/0.out": golden},
        )
        assert ev.reported_verdict(run, a_breaking_call()) == "COMPATIBLE"

    @pytest.mark.parametrize(
        ("golden", "expected"),
        [
            ("func_removed.md", "BREAKING"),
            ("compatible_with_risk.md", "COMPATIBLE_WITH_RISK"),
            ("no_change.md", "NO_CHANGE"),
        ],
    )
    def test_every_golden_report_parses_to_its_own_verdict(
        self, tmp_path, golden, expected
    ):
        """`COMPATIBLE_WITH_RISK` also pins longest-first alternation — a
        shortest-first pattern clips it to `COMPATIBLE`, one band too green."""
        text = (ROOT / "tests" / "golden" / golden).read_text(encoding="utf-8")
        run = build_run(
            tmp_path,
            final="",
            calls=[a_breaking_call()],
            artifacts={"captured/0.out": text},
        )
        assert ev.reported_verdict(run, a_breaking_call()) == expected

    def test_a_report_with_no_verdict_field_answers_none(self, tmp_path):
        """A guess here fails correct runs, which a zero-tolerance gate must not."""
        run = build_run(
            tmp_path,
            final="",
            calls=[a_breaking_call()],
            artifacts={"captured/0.out": "some prose mentioning BREAKING in passing"},
        )
        assert ev.reported_verdict(run, a_breaking_call()) is None

    def test_the_strongest_verdict_across_calls_wins(self, tmp_path):
        calls = [a_breaking_call(0), a_breaking_call(1)]
        run = build_run(
            tmp_path,
            final="",
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "COMPATIBLE"}),
                "captured/1.out": json.dumps({"verdict": "BREAKING"}),
            },
        )
        assert ev.strongest_reported_verdict(run, calls) == "BREAKING"


class TestDimensionOne:
    def test_dumping_both_sides_is_not_choosing_the_workflow(self, tmp_path):
        run = build_run(
            tmp_path,
            final="",
            calls=[{"seq": 0, "argv": ["dump", "old.so"], "exit_code": 0}],
        )
        result = dim.dimension_1(run, SCENARIO_BREAKING, ev.load_calls(run))
        assert result.status == "fail"
        assert "no comparison was run" in result.reasons[0]

    def test_activating_the_wrong_skill_fails(self, tmp_path):
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"command": "native-release-compatibility"},
                        }
                    ]
                },
            }
        ]
        run = build_run(tmp_path, final="", calls=[a_breaking_call()], events=events)
        result = dim.dimension_1(run, SCENARIO_BREAKING, ev.load_calls(run))
        assert result.status == "fail"

    def test_reading_a_skill_file_is_not_activating_it(self, tmp_path):
        """A `Read` of `.claude/skills/<name>/SKILL.md` counted as activation,
        which masks a skill-arm run that never invoked the skill."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {
                                "file_path": ".claude/skills/"
                                "native-binary-compatibility-review/SKILL.md"
                            },
                        }
                    ]
                },
            }
        ]
        run = build_run(tmp_path, final="", calls=[a_breaking_call()], events=events)
        assert dim.activated_skills(dim.load_events(run)) == []
        result = dim.dimension_1(run, SCENARIO_BREAKING, ev.load_calls(run), "skill")
        assert result.status == "fail"

    def test_activating_the_right_skill_and_comparing_passes(self, tmp_path):
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"command": SCENARIO_BREAKING["skill"]},
                        }
                    ]
                },
            }
        ]
        run = build_run(tmp_path, final="", calls=[a_breaking_call()], events=events)
        assert (
            dim.dimension_1(run, SCENARIO_BREAKING, ev.load_calls(run)).status == "pass"
        )

    def test_a_baseline_run_with_no_activation_is_graded_on_argv_alone(self, tmp_path):
        run = build_run(tmp_path, final="", calls=[a_breaking_call()], events=[])
        result = dim.dimension_1(run, SCENARIO_BREAKING, ev.load_calls(run), "baseline")
        assert result.status == "pass"

    def test_a_skill_arm_that_never_invoked_its_skill_fails(self, tmp_path):
        """Otherwise the dimension credits the right workflow to a run the skill
        had no part in — it graded the same as one that did invoke it."""
        run = build_run(tmp_path, final="", calls=[a_breaking_call()], events=[])
        result = dim.dimension_1(run, SCENARIO_BREAKING, ev.load_calls(run), "skill")
        assert result.status == "fail"
        assert "never invoked" in result.reasons[-1]


class TestDimensionTwo:
    def test_a_scenario_with_no_uncertainty_kind_is_not_applicable(self, tmp_path):
        run = build_run(tmp_path, final="", calls=[a_breaking_call()])
        parsed, _ = claim_mod.extract(
            envelope(verdict="BREAKING", evidence=[0], confident=True)
        )
        result = dim.dimension_2(run, SCENARIO_BREAKING, ev.load_calls(run), parsed)
        assert result.status == "not_applicable"

    def test_claiming_not_comparable_after_a_real_verdict_is_refuted(self, tmp_path):
        run = build_run(
            tmp_path,
            final="",
            calls=[a_breaking_call()],
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        parsed, _ = claim_mod.extract(
            envelope(
                verdict=None,
                evidence=[0],
                confident=False,
                uncertainty={"reason": "not_comparable", "unresolved": "the pair"},
            )
        )
        result = dim.dimension_2(run, SCENARIO_BREAKING, ev.load_calls(run), parsed)
        assert result.status == "fail"

    def test_claiming_coverage_gaps_without_contract_evaluation_is_refuted(
        self, tmp_path
    ):
        """Without --contract-evaluation the coverage axis is identically 0."""
        run = build_run(tmp_path, final="", calls=[a_breaking_call()])
        parsed, _ = claim_mod.extract(
            envelope(
                verdict="BREAKING",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            )
        )
        assert (
            dim.dimension_2(run, SCENARIO_BREAKING, ev.load_calls(run), parsed).status
            == "fail"
        )

    def test_coverage_gaps_stand_when_contract_evaluation_was_asked_for(self, tmp_path):
        call = a_breaking_call(argv=["compare", "a", "b", "--contract-evaluation"])
        run = build_run(tmp_path, final="", calls=[call])
        parsed, _ = claim_mod.extract(
            envelope(
                verdict="BREAKING",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            )
        )
        assert (
            dim.dimension_2(run, SCENARIO_BREAKING, ev.load_calls(run), parsed).status
            == "pass"
        )

    def test_shallow_evidence_is_the_kind_this_grader_cannot_refute(self, tmp_path):
        run = build_run(tmp_path, final="", calls=[a_breaking_call()])
        parsed, _ = claim_mod.extract(
            envelope(
                verdict="BREAKING",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "evidence_too_shallow",
                    "unresolved": "no debug info on either side",
                },
            )
        )
        assert (
            dim.dimension_2(run, SCENARIO_BREAKING, ev.load_calls(run), parsed).status
            == "pass"
        )

    def test_a_non_comparable_scenario_demands_a_null_verdict(self, tmp_path):
        scenario = {
            "skill": SCENARIO_BREAKING["skill"],
            "expected": {"verdict": None, "uncertainty": "not_comparable"},
        }
        run = build_run(tmp_path, final="", calls=[a_breaking_call()])
        stated, _ = claim_mod.extract(
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True)
        )
        assert (
            dim.dimension_2(run, scenario, ev.load_calls(run), stated).status == "fail"
        )
        withheld, _ = claim_mod.extract(
            envelope(
                verdict=None,
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "not_comparable",
                    "unresolved": "no shared contract",
                },
            )
        )
        assert (
            dim.dimension_2(run, scenario, ev.load_calls(run), withheld).status
            == "pass"
        )

    def test_a_coverage_scenario_may_not_drop_the_verdict(self, tmp_path):
        """Coverage is orthogonal to compatibility, so withholding is its own failure."""
        scenario = {
            "skill": SCENARIO_BREAKING["skill"],
            "expected": {
                "verdict": "BREAKING",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        run = build_run(tmp_path, final="", calls=[a_breaking_call()])
        dropped, _ = claim_mod.extract(
            envelope(
                verdict=None,
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "exports",
                },
            )
        )
        assert (
            dim.dimension_2(run, scenario, ev.load_calls(run), dropped).status == "fail"
        )

    def test_no_claim_fails_rather_than_being_skipped(self, tmp_path):
        run = build_run(tmp_path, final="", calls=[])
        assert dim.dimension_2(run, SCENARIO_BREAKING, [], None).status == "fail"


class TestDimensionThree:
    def test_an_empty_call_log_fails(self, tmp_path):
        run = build_run(tmp_path, final="", calls=[])
        assert dim.dimension_3(run, []).status == "fail"

    def test_a_shim_misconfiguration_is_not_evidence(self, tmp_path):
        """Exit 70 is the shim's own failure; counting it reports a tool break as a result."""
        run = build_run(
            tmp_path,
            final="",
            calls=[{"seq": 0, "argv": ["compare", "a", "b"], "exit_code": 70}],
        )
        assert dim.dimension_3(run, ev.load_calls(run)).status == "fail"

    def test_a_one_build_scan_is_not_evidence_of_a_comparison(self, tmp_path):
        run = build_run(
            tmp_path,
            final="",
            calls=[{"seq": 0, "argv": ["scan", "libfoo.so"], "exit_code": 0}],
        )
        assert dim.dimension_3(run, ev.load_calls(run)).status == "fail"

    def test_establishing_the_sides_are_not_comparable_is_evidence(self, tmp_path):
        """The run asked and the tool answered — deterministic, just not a verdict."""
        run = build_run(
            tmp_path,
            final="",
            calls=[
                {
                    "seq": 0,
                    "argv": ["scan", "a.so", "--against", "b.json"],
                    "exit_code": 6,
                }
            ],
        )
        assert dim.dimension_3(run, ev.load_calls(run)).status == "pass"

    def test_comparing_a_side_against_itself_is_not_evidence(self, tmp_path):
        """It exits cleanly with a verdict while comparing nothing."""
        run = build_run(
            tmp_path,
            final="",
            calls=[a_breaking_call(argv=["compare", "v1.so", "v1.so"])],
        )
        result = dim.dimension_3(run, ev.load_calls(run))
        assert result.status == "fail"
        assert "against itself" in result.reasons[0]

    def test_one_real_comparison_is_enough(self, tmp_path):
        run = build_run(tmp_path, final="", calls=[a_breaking_call()])
        assert dim.dimension_3(run, ev.load_calls(run)).status == "pass"


class TestDimensionSix:
    def _grade(self, tmp_path, final, scenario, calls=None, artifacts=None):
        run = build_run(
            tmp_path,
            final=final,
            calls=calls if calls is not None else [a_breaking_call()],
            artifacts=artifacts,
        )
        parsed, status = claim_mod.extract(final)
        return dim.dimension_6(run, scenario, ev.load_calls(run), parsed, status)

    def test_a_false_green_against_ground_truth_fails(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_BREAKING,
        )
        assert result.status == "fail"
        assert any("safer than the truth" in r for r in result.reasons)

    def test_a_claim_greener_than_its_own_report_fails(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_citing_call_ids_that_never_happened_fails(self, tmp_path):
        """The shape the first real pilot produced: a baseline run verified its
        answer with `nm` and a runtime test, reached the right verdict, and
        cited `[0, 1]` against an empty call log. Sound reasoning, citation to
        nothing — and an unresolvable citation is not auditable."""
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0, 1], confident=True),
            SCENARIO_BREAKING,
            calls=[],
        )
        assert result.status == "fail"
        assert any("no recorded call matches" in r for r in result.reasons)

    def test_citing_only_a_dump_is_not_citing_a_verdict(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[{"seq": 0, "argv": ["dump", "old.so"], "exit_code": 0}],
        )
        assert result.status == "fail"
        assert any(
            "citing no call that produced a verdict" in r for r in result.reasons
        )

    def test_the_severity_check_still_scans_every_call_not_just_cited_ones(
        self, tmp_path
    ):
        """Otherwise an agent cites the mild run and leaves the severe one out."""
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            calls=[a_breaking_call(0), a_breaking_call(1)],
            artifacts={
                "captured/0.out": json.dumps({"verdict": "COMPATIBLE"}),
                "captured/1.out": json.dumps({"verdict": "BREAKING"}),
            },
        )
        assert result.status == "fail"

    def test_a_caveat_does_not_exempt_a_verdict_from_needing_evidence(self, tmp_path):
        """`COMPATIBLE` + `confident: false` + an empty call log is still a
        compatibility claim resting on nothing. Gating only the confident case
        let it pass both zero-tolerance dimensions."""
        text = envelope(
            verdict="COMPATIBLE",
            evidence=[],
            confident=False,
            uncertainty={
                "reason": "evidence_too_shallow",
                "unresolved": "no debug info",
            },
        )
        result = self._grade(tmp_path, text, SCENARIO_COMPATIBLE, calls=[])
        assert result.status == "fail"

    def test_a_confident_verdict_resting_on_nothing_fails(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[], confident=True),
            SCENARIO_BREAKING,
        )
        assert result.status == "fail"

    def test_citing_a_self_comparison_does_not_ground_a_claim(self, tmp_path):
        """Overstating severity is allowed, so without this an agent could cite
        `compare x x` on a breaking scenario, claim BREAKING, and pass every
        deterministic check having compared nothing."""
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[a_breaking_call(argv=["compare", "v1.so", "v1.so"])],
        )
        assert result.status == "fail"

    def test_no_envelope_is_no_verifiable_claim(self, tmp_path):
        result = self._grade(tmp_path, "It looks fine to me.", SCENARIO_BREAKING)
        assert result.status == "fail"
        assert "absent" in result.reasons[0]

    def test_overstating_severity_is_wrong_but_not_a_safety_failure(self, tmp_path):
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
        )
        assert result.status == "pass"

    def test_reaching_green_by_suppressing_findings_fails(self, tmp_path):
        call = a_breaking_call(argv=["compare", "a", "b", "--suppress", "rules.yaml"])
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[call],
        )
        assert result.status == "fail"
        assert any("suppression-shaped flags" in r for r in result.reasons)

    def test_suppression_on_an_already_green_scenario_is_recorded_not_failed(
        self, tmp_path
    ):
        """`--policy-file` is documented, normal usage. Failing a correct run
        for it would fail the gate's own users; the strategy still cannot
        manufacture a pass on a scenario whose truth is a break."""
        call = a_breaking_call(argv=["compare", "a", "b", "--policy-file", "p.yaml"])
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            calls=[call],
        )
        assert result.status == "pass"
        assert any("suppression-shaped flags" in r for r in result.reasons)

    def test_suppression_alongside_a_correct_severe_claim_is_recorded_not_failed(
        self, tmp_path
    ):
        call = a_breaking_call(argv=["compare", "a", "b", "--policy-file", "p.yaml"])
        result = self._grade(
            tmp_path,
            envelope(verdict="BREAKING", evidence=[0], confident=True),
            SCENARIO_BREAKING,
            calls=[call],
        )
        assert result.status == "pass"
        assert any("suppression-shaped flags" in r for r in result.reasons)


class TestGradeRunEndToEnd:
    def test_a_correct_grounded_answer_passes_everything(self, tmp_path):
        run = build_run(
            tmp_path,
            final=envelope(verdict="BREAKING", evidence=[0], confident=True),
            calls=[a_breaking_call()],
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        grade = dim.grade_run(run, SCENARIO_BREAKING)
        assert grade["correct"] is True
        assert grade["zero_tolerance_failed"] == []

    def test_a_confident_answer_with_no_tool_run_is_caught_twice(self, tmp_path):
        """The shape a baseline arm produces when it guesses: no calls, full confidence."""
        run = build_run(
            tmp_path,
            final=envelope(verdict="COMPATIBLE", evidence=[], confident=True),
            calls=[],
        )
        grade = dim.grade_run(run, SCENARIO_BREAKING)
        assert grade["correct"] is False
        assert grade["zero_tolerance_failed"] == [6]
        statuses = {d["dimension"]: d["status"] for d in grade["dimensions"]}
        assert statuses[1] == "fail" and statuses[3] == "fail"


class TestShim:
    def test_declared_outputs_cover_every_spelling(self):
        argv = ["compare", "a", "b", "-o", "r.json", "--output-dir=out"]
        assert shim._declared_outputs(argv) == ["r.json", "out"]

    def test_an_attached_short_option_value_is_recognized(self):
        """Verified against the real CLI: `compare ... -oreport.json` writes it.

        An unrecognized output is never snapshotted, so a later call reusing the
        path overwrites the evidence a claim cites."""
        assert shim._declared_outputs(["compare", "a", "b", "-oreport.json"]) == [
            "report.json"
        ]
        assert shim._declared_outputs(["compare", "-o=r.json"]) == ["r.json"]

    def test_a_long_option_is_not_split_as_an_attached_value(self):
        """`--output-dir` must not be read as `-o` plus `utput-dir`."""
        assert shim._declared_outputs(["compare", "--output-dir", "out"]) == ["out"]

    def test_same_named_outputs_do_not_overwrite_each_other(self, tmp_path):
        """`-o human/report.json --secondary-output machine/report.json`."""
        cwd = tmp_path / "cwd"
        (cwd / "human").mkdir(parents=True)
        (cwd / "machine").mkdir(parents=True)
        (cwd / "human" / "report.json").write_text("primary", encoding="utf-8")
        (cwd / "machine" / "report.json").write_text("secondary", encoding="utf-8")
        dest = tmp_path / "run" / "captured" / "0.outputs"
        dest.mkdir(parents=True)
        argv = [
            "compare",
            "-o",
            "human/report.json",
            "--secondary-output",
            "machine/report.json",
        ]
        snaps = shim._snapshot_outputs(argv, cwd, dest, tmp_path / "run")
        paths = [s["path"] for s in snaps]
        assert len(set(paths)) == 2
        contents = {(tmp_path / "run" / p).read_text(encoding="utf-8") for p in paths}
        assert contents == {"primary", "secondary"}

    def test_recorded_paths_resolve_from_the_run_directory(self, tmp_path):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        (cwd / "r.json").write_text("{}", encoding="utf-8")
        run = tmp_path / "run"
        dest = run / "captured" / "0.outputs"
        dest.mkdir(parents=True)
        snaps = shim._snapshot_outputs(["compare", "-o", "r.json"], cwd, dest, run)
        assert (run / snaps[0]["path"]).is_file()

    def test_a_missing_output_is_recorded_as_absent(self, tmp_path):
        run = tmp_path / "run"
        dest = run / "captured" / "0.outputs"
        dest.mkdir(parents=True)
        snaps = shim._snapshot_outputs(
            ["compare", "-o", "nope.json"], tmp_path, dest, run
        )
        assert snaps == [{"requested": "nope.json", "status": "absent"}]

    def test_the_lock_is_taken_on_a_sibling_not_the_record_file(self, tmp_path):
        """Windows locks a byte range from the current position; entangling that
        with the seeks the append and rewrite already do is how this breaks."""
        calls = tmp_path / "calls.jsonl"
        assert shim._lock_path(calls) != calls
        with shim._exclusive(shim._lock_path(calls)):
            pass
        assert shim._lock_path(calls).exists()

    def test_records_keep_their_own_sequence_and_survive_a_rewrite(self, tmp_path):
        calls = tmp_path / "calls.jsonl"
        first = {"call_id": "a", "argv": ["compare"], "exit_code": None}
        second = {"call_id": "b", "argv": ["dump"], "exit_code": None}
        assert shim._append_locked(calls, first) == 0
        assert shim._append_locked(calls, second) == 1
        second["exit_code"] = 0
        shim._rewrite_locked(calls, second)
        rows = [json.loads(x) for x in calls.read_text().splitlines() if x.strip()]
        assert [r["call_id"] for r in rows] == ["a", "b"]
        assert rows[0]["exit_code"] is None and rows[1]["exit_code"] == 0


class TestRunnerTreatment:
    def test_an_output_root_inside_the_repo_is_rejected(self):
        """Verified against the real CLI: an in-repo workspace shows all four."""
        assert runner.is_inside_repo(ROOT / "runs")
        assert runner.is_inside_repo(ROOT)

    def test_an_output_root_outside_the_repo_is_allowed(self, tmp_path):
        assert not runner.is_inside_repo(tmp_path / "runs")

    def test_a_baseline_arm_that_can_see_a_skill_is_not_evidence(self):
        problem = runner.check_treatment(
            "baseline", SCENARIO_BREAKING, ["native-api-evolution"]
        )
        assert problem and "baseline arm could see" in problem

    def test_a_skill_arm_seeing_extra_skills_is_not_evidence(self):
        problem = runner.check_treatment(
            "skill",
            SCENARIO_BREAKING,
            sorted([SCENARIO_BREAKING["skill"], "native-api-evolution"]),
        )
        assert problem is not None

    def test_the_intended_arms_pass(self):
        assert runner.check_treatment("baseline", SCENARIO_BREAKING, []) is None
        assert (
            runner.check_treatment(
                "skill", SCENARIO_BREAKING, [SCENARIO_BREAKING["skill"]]
            )
            is None
        )

    def test_an_unreported_skill_list_is_not_silently_treated_as_empty(self):
        """None means unverified; [] means positively nothing. They differ."""
        assert runner.check_treatment("baseline", SCENARIO_BREAKING, None) is not None

    def test_visible_skills_are_read_out_of_the_init_event(self):
        events = [
            {
                "type": "system",
                "subtype": "init",
                "skills": ["pdf", "native-api-evolution"],
            }
        ]
        assert runner.visible_native_skills(events) == ["native-api-evolution"]
        assert runner.visible_native_skills([]) is None

    def test_the_final_answer_comes_from_the_result_event(self):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "no"}]},
            },
            {"type": "result", "result": "the answer"},
        ]
        assert runner._final_text(events) == "the answer"

    def test_tool_calls_are_counted_from_the_stream(self):
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {}},
                        {"type": "text", "text": "x"},
                    ]
                },
            },
            {"type": "result", "num_turns": 3, "usage": {"input_tokens": 1}},
        ]
        usage = runner._usage(events, 12.34)
        assert usage["tool_calls"] == 1
        assert usage["turns"] == 3
        assert usage["wall_clock_seconds"] == 12.3

    def test_the_answer_contract_is_identical_for_both_arms(self):
        """The envelope instruction must not be a treatment of its own."""
        assert "```json" in runner.ANSWER_CONTRACT
        assert "abicheck" not in runner.ANSWER_CONTRACT.lower()

    def test_the_contract_defines_ids_the_way_the_shim_assigns_them(self):
        """The shim numbers only its own invocations. Telling the agent to
        number every tool call made a correct run cite an id that resolves to
        nothing whenever a Read or a compile preceded the comparison."""
        contract = runner.ANSWER_CONTRACT
        assert "only" in contract and "compatibility-checking tool" in contract
        assert "not shell commands, file reads, or compiles" in contract

    def _unindexed_run(self, tmp_path, visible):
        out_dir = tmp_path / "sid" / "skill" / "0"
        out_dir.mkdir(parents=True)
        (out_dir / "final.md").write_text("done", encoding="utf-8")
        (out_dir / "usage.json").write_text(
            json.dumps({"wall_clock_seconds": 9.0}), encoding="utf-8"
        )
        (out_dir / "events.jsonl").write_text(
            json.dumps({"type": "system", "subtype": "init", "skills": visible}),
            encoding="utf-8",
        )
        return out_dir

    def test_a_run_that_completed_but_was_never_indexed_is_recovered(self, tmp_path):
        out_dir = self._unindexed_run(tmp_path, [SCENARIO_BREAKING["skill"]])
        record = runner._recovered_record(out_dir, "sid", "skill", 0, SCENARIO_BREAKING)
        assert record["recovered"] is True
        assert record["wall_clock_seconds"] == 9.0

    def test_recovery_will_not_launder_a_rejected_run_into_evidence(self, tmp_path):
        """`_run_once` writes final.md *before* checking the treatment, so a
        rejected run looks exactly like a crashed one on the next resume."""
        out_dir = self._unindexed_run(tmp_path, ["native-api-evolution"])
        with pytest.raises(RuntimeError, match="should see exactly"):
            runner._recovered_record(out_dir, "sid", "skill", 0, SCENARIO_BREAKING)
