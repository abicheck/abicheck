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


def a_not_comparable_answer(evidence: list[int] | None = None) -> str:
    """The one uncertainty shape that carries a `null` verdict and no caveat."""
    return envelope(
        verdict=None,
        evidence=[0] if evidence is None else evidence,
        confident=False,
        uncertainty={
            "reason": "not_comparable",
            "unresolved": "the two sides were built for different architectures",
        },
    )


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
            {"targets": [{"state": "not_run"}]},
            {"targets": [{"id": "  ", "state": "not_run"}]},
            {"targets": [{"id": "linux-x86_64", "state": "probably fine"}]},
            {"targets": []},
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

    def test_a_not_comparable_claim_resting_on_nothing_fails(self, tmp_path):
        """A `null` verdict is still a claim about the pair, and the null branch
        used to skip the evidence check entirely — so an empty citation against
        an empty call log passed both zero-tolerance dimensions by making the
        `not-comparable-pair` scenario's expected answer with no run behind it."""
        result = self._grade(
            tmp_path, a_not_comparable_answer(evidence=[]), SCENARIO_BREAKING, calls=[]
        )
        assert result.status == "fail"
        assert any("resting on no recorded call" in r for r in result.reasons)

    def test_a_not_comparable_claim_citing_a_call_that_never_happened_fails(
        self, tmp_path
    ):
        result = self._grade(
            tmp_path, a_not_comparable_answer(), SCENARIO_BREAKING, calls=[]
        )
        assert result.status == "fail"
        assert any("no recorded call matches" in r for r in result.reasons)

    def test_a_not_comparable_claim_citing_a_completed_comparison_fails(self, tmp_path):
        """Citing a call that *did* produce a verdict does not support "these two
        cannot be compared" — the tool answered, so the citation refutes it."""
        result = self._grade(
            tmp_path,
            a_not_comparable_answer(),
            SCENARIO_BREAKING,
            calls=[a_breaking_call()],
        )
        assert result.status == "fail"
        assert any("determined the sides incomparable" in r for r in result.reasons)

    @pytest.mark.parametrize(
        ("argv", "exit_code"),
        [
            (["compare", "old.so", "new.so"], 16),
            (["scan", "new.so", "--against", "old.json"], 6),
            (["compat", "check", "-old", "a.xml", "-new", "b.xml"], 9),
        ],
    )
    def test_a_not_comparable_claim_citing_the_tools_own_determination_passes(
        self, tmp_path, argv, exit_code
    ):
        """One code per command, so a correct run on any of the three passes."""
        call = {"seq": 0, "call_id": "c0", "argv": argv, "exit_code": exit_code}
        result = self._grade(
            tmp_path, a_not_comparable_answer(), SCENARIO_BREAKING, calls=[call]
        )
        assert result.status == "pass"

    def test_the_other_uncertainty_kinds_are_not_asked_for_a_citation(self, tmp_path):
        """A run that stops on shallow evidence may legitimately have produced
        neither a verdict nor a non-comparability determination. Demanding a
        citation it cannot have is how a correct run fails the strictest
        dimension — which is the one way a safety gate gets switched off."""
        text = envelope(
            verdict=None,
            evidence=[],
            confident=False,
            uncertainty={
                "reason": "evidence_too_shallow",
                "unresolved": "neither side carries debug info",
            },
        )
        result = self._grade(tmp_path, text, SCENARIO_BREAKING, calls=[])
        assert result.status == "pass"


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

    def test_the_expected_uncertainty_answer_still_has_to_be_earned(self, tmp_path):
        """Dimension 2 accepts the shape of this claim — it *is* the answer the
        scenario expects — and dimensions 1 and 3 do not gate. So without
        dimension 6 asking for the tool's own determination, a run that recorded
        nothing scores clean by naming the outcome it was going to be graded
        against."""
        scenario = {
            "skill": "native-binary-compatibility-review",
            "expected": {"verdict": None, "uncertainty": "not_comparable"},
        }
        run = build_run(tmp_path, final=a_not_comparable_answer(evidence=[]), calls=[])
        grade = dim.grade_run(run, scenario)
        assert grade["correct"] is True
        assert grade["zero_tolerance_failed"] == [6]
        statuses = {d["dimension"]: d["status"] for d in grade["dimensions"]}
        assert statuses[2] == "pass"


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

    def test_an_unknown_scenario_id_is_rejected_not_dropped(self):
        """Silently filtering a typo produced an apparently complete
        experiment that omitted an explicitly requested case."""
        pack = {
            "scenarios": {
                "removed-export": {"status": "ready"},
                "planned-one": {"status": "planned"},
            }
        }
        assert runner.unknown_scenarios(["removed-export"], pack) == []
        assert runner.unknown_scenarios(["removed-exprot"], pack) == ["removed-exprot"]
        assert runner.unknown_scenarios(["planned-one"], pack) == ["planned-one"]

    def test_a_module_entry_invocation_is_detected_as_a_bypass(self, tmp_path):
        """`python -m abicheck` does not pass through a PATH shim named
        `abicheck`, so its evidence is missing rather than absent — and an
        empty call log reads identically to "never ran the tool"."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "python -m abicheck compare a b"},
                        }
                    ]
                },
            }
        ]
        calls = tmp_path / "calls.jsonl"
        assert runner._bypassed_the_recorder(events, calls)
        calls.write_text(json.dumps({"seq": 0, "argv": ["compare"]}) + "\n")
        assert not runner._bypassed_the_recorder(events, calls)

    def test_the_interposer_only_redirects_the_module_entry_form(self):
        """It sits on PATH as `python`; everything else must exec untouched."""
        script = runner._PYTHON_INTERPOSER
        assert script.startswith("#!/bin/sh")
        assert '"$1" = "-m"' in script and '"$2" = "abicheck"' in script
        assert 'exec "$SKILL_EVAL_REAL_PYTHON" "$@"' in script

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


class TestWorkspaceIsolation:
    """The workspace must not hand either arm the tool or the answer.

    The prompt deliberately never names abicheck — a skill that has to be
    *found* is the thing being measured — so a fixture that names it in a
    README, a build file, or a source comment reproduces one directory down the
    exact confound the out-of-repo `--out` rule exists to prevent.
    """

    def _case(self, tmp_path, files: dict[str, str]) -> Path:
        case = tmp_path / "case99"
        for rel, text in files.items():
            target = case / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return case

    def test_the_readme_and_build_file_never_reach_the_workspace(self, tmp_path):
        """Both name the tool; the README also states the verdict outright."""
        self._case(
            tmp_path,
            {
                "README.md": "**Verdict:** BREAKING\n\n`abicheck compare v1.so v2.so`\n",
                "CMakeLists.txt": "abicheck_add_case(case99 V1_SOURCES v1.c)\n",
                "v1.c": "int f(void){return 1;}\n",
            },
        )
        work = tmp_path / "ws"
        work.mkdir()
        runner.ROOT = tmp_path
        try:
            runner._prepare_workspace(work, {"inputs": "case99"}, "baseline")
        finally:
            runner.ROOT = ROOT
        present = {p.name for p in (work / "library").rglob("*") if p.is_file()}
        assert present == {"v1.c"}

    def test_the_demo_consumer_is_read_from_the_case_definition(self, tmp_path):
        """Not guessed from a filename, and tolerant of keywords it ignores —
        `case05_soname` carries a `V2_LINK_OPTIONS` that a fixed keyword list
        folded into the preceding group."""
        case = self._case(
            tmp_path,
            {
                "CMakeLists.txt": (
                    "abicheck_add_case(case99\n"
                    "    V1_SOURCES old/lib.c\n"
                    "    V2_LINK_OPTIONS -Wl,-soname,libv2.so\n"
                    "    APP_SOURCES demo.c\n"
                    ")\n"
                ),
            },
        )
        assert runner.demo_app_sources(case) == ["demo.c"]

    def test_a_case_with_no_build_file_excludes_nothing_extra(self, tmp_path):
        assert runner.demo_app_sources(self._case(tmp_path, {"v1.c": "\n"})) == []

    def test_an_answer_bearing_comment_is_stripped_on_the_way_in(self, tmp_path):
        source = "/* helper() removed — BREAKING change */\nint f(void){return 1;}\n"
        assert "BREAKING" not in runner.strip_comments(source)
        assert "int f(void){return 1;}" in runner.strip_comments(source)

    def test_stripping_preserves_line_numbers(self, tmp_path):
        """A compiler error must still name the line the reader is looking at."""
        stripped = runner.strip_comments("/* a\nb\nc */\nint f(void);\n")
        assert stripped.splitlines()[3] == "int f(void);"

    def test_a_string_containing_comment_markers_survives(self):
        """String literals are program behaviour, not annotation."""
        source = 'const char *s = "http://x /* not a comment */";\n'
        assert runner.strip_comments(source) == source

    def test_a_raw_string_makes_stripping_refuse_rather_than_guess(self):
        """Its body can hold `//` with no comment meaning; the scan is the backstop."""
        assert runner.strip_comments('auto s = R"(// not a comment)";\n') is None

    def test_the_scan_finds_the_tool_name_and_the_verdict_vocabulary(self, tmp_path):
        work = tmp_path / "ws"
        (work / "library").mkdir(parents=True)
        (work / "library" / "notes.md").write_text(
            "run abicheck compare\nthis is COMPATIBLE\n", encoding="utf-8"
        )
        leaks = runner.workspace_leaks(work)
        assert len(leaks) == 2
        assert any("abicheck" in leak for leak in leaks)
        assert any("COMPATIBLE" in leak for leak in leaks)

    def test_the_installed_skill_is_not_scanned_as_a_leak(self, tmp_path):
        """Naming the tool is the treatment's whole job."""
        work = tmp_path / "ws"
        skill = work / ".claude" / "skills" / "native-api-evolution"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("run abicheck compare\n", encoding="utf-8")
        assert runner.workspace_leaks(work) == []

    def test_every_ready_fixture_reaches_the_workspace_clean(self, tmp_path):
        """The corpus itself, not a synthetic stand-in: three of the eight
        leaked before this — one naming the tool and the exact change kinds it
        reports."""
        pack = json.loads((EVAL_DIR / "skill-eval-pack.json").read_text())
        ready = {s: e for s, e in pack["scenarios"].items() if e["status"] == "ready"}
        assert ready, "the pack lists no ready scenario to check"
        for sid, entry in ready.items():
            work = tmp_path / sid
            work.mkdir()
            runner._prepare_workspace(work, entry, "baseline")
            assert runner.workspace_leaks(work) == [], sid


class TestToolchainPreflight:
    def _scenarios(self, inputs: str) -> dict[str, dict]:
        return {"s": {"inputs": inputs, "expected": {"min_evidence": "L0"}}}

    def test_a_c_only_selection_does_not_demand_a_cxx_compiler(self):
        """Requiring both unconditionally described the corpus, not the run."""
        assert runner.required_languages(
            self._scenarios("examples/case01_symbol_removal")
        ) == {"c"}

    def test_a_cxx_fixture_is_recognized(self):
        assert "c++" in runner.required_languages(
            self._scenarios("examples/case09_cpp_vtable")
        )

    def test_msvc_counts_as_both_compilers(self):
        """`cl` is one driver for C and C++, and is the normal Windows
        toolchain — without it a Visual Studio host refused to run the two
        scenarios the pack marks windows-supported."""
        assert "cl" in runner._C_COMPILERS and "cl" in runner._CXX_COMPILERS


class TestPublishedContractsAgree:
    def test_the_matrix_state_vocabulary_matches_the_schema(self):
        """The graders stay file-I/O-free at import, so the vocabulary is a
        literal — which only stays safe if drift fails loudly."""
        schema = json.loads((EVAL_DIR / "schema" / "claim.schema.json").read_text())
        published = schema["properties"]["matrix"]["properties"]["targets"]["items"][
            "properties"
        ]["state"]["enum"]
        assert claim_mod.MATRIX_STATES == set(published)

    def test_a_target_without_an_id_does_not_satisfy_the_matrix_rule(self, tmp_path):
        """ "A cell is missing" is not the finding; "the Windows cell is missing"
        is — and the schema requires `id` for exactly that reason."""
        scenario = {
            "skill": "native-release-compatibility",
            "expected": {"verdict": "COMPATIBLE", "uncertainty": "matrix_target_unrun"},
        }
        text = envelope(
            verdict="COMPATIBLE",
            evidence=[0],
            confident=False,
            uncertainty={"reason": "matrix_target_unrun", "unresolved": "a target"},
            matrix={"targets": [{"state": "not_run"}]},
        )
        run = build_run(tmp_path, final=text, calls=[a_breaking_call()])
        parsed, status = claim_mod.extract(text)
        assert parsed is None
        result = dim.dimension_2(run, scenario, ev.load_calls(run), parsed)
        assert result.status == "fail"


class TestSelfComparisonDetection:
    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "x.so", "x.so"],
            ["compare", "x.so", "--format", "json", "x.so"],
            ["compare", "--format", "json", "x.so", "x.so"],
        ],
    )
    def test_one_operand_named_twice_is_caught_however_it_is_spelled(self, argv):
        """Verified against the real CLI: `compare x.so --format json x.so` runs
        the comparison and reports NO_CHANGE, while the operands sit three
        apart — so the adjacency test this replaced walked straight past it."""
        assert ev.compares_one_side_against_itself({"argv": argv})

    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "old.so", "new.so"],
            ["compare", "a.so", "b.so", "--policy-file", "p.yaml"],
            ["compare", "a.so", "b.so", "-o", "b.so"],
            [
                "compare",
                "a.so",
                "b.so",
                "--suppress",
                "r.yaml",
                "--policy-file",
                "r.yaml",
            ],
            ["scan", "lib.so", "--against", "base.json"],
        ],
    )
    def test_ordinary_invocations_are_not_flagged(self, argv):
        """A report written to a path named like an operand is normal usage;
        reading that as a third operand would fail a correct run."""
        assert not ev.compares_one_side_against_itself({"argv": argv})
