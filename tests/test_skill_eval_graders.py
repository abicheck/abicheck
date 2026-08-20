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

SCENARIO_BREAKING = {
    "skill": "review-native-library-change",
    "expected": {"verdict": "BREAKING"},
}
SCENARIO_COMPATIBLE = {
    "skill": "review-native-library-change",
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
        """Every severity flag the CLI actually has must be recorded.

        Stated against the live CLI rather than a hand-listed set of
        spellings: the original list named the four per-category
        `--severity-*` overrides, which have since been removed, and a
        hardcoded list cannot tell "this flag is gone" from "this flag is
        unrecorded" -- the second is the real failure and it would have gone
        unnoticed. There is still no generic `--severity` option for a stem
        to match, which is why the grader spells its flags out.
        """
        from abicheck.cli import main

        severity_options = {
            opt
            for name in ("compare", "scan")
            for p in main.commands[name].params
            if getattr(p, "param_type_name", None) == "option"
            for opt in p.opts
            if opt.startswith("--severity")
        }
        assert severity_options, "no severity flag found -- the scan is vacuous"
        for flag in sorted(severity_options):
            assert flag in ev.SUPPRESSION_FLAGS, flag
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
        call = {"argv": ["compare", "a", "b", "--suppress", "x", "--policy=y"]}
        assert ev.suppression_flags(call) == ["--policy", "--suppress"]

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

    def test_only_scoped_drops_unscoped_calls_from_the_reckoning(self, tmp_path):
        """An unscoped compare answers a different question than a `--used-by`
        one — `only_scoped` must not let its report dominate the scoped
        answer, even though it is the more severe of the two."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            a_breaking_call(
                1, argv=["compare", "old.so", "new.so", "--used-by", "app"]
            ),
        ]
        run = build_run(
            tmp_path,
            final="",
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "BREAKING"}),
                "captured/1.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                ),
            },
        )
        assert ev.strongest_reported_verdict(run, calls) == "BREAKING"
        assert (
            ev.strongest_reported_verdict(run, calls, only_scoped=True) == "COMPATIBLE"
        )

    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "old.so", "new.so", "--used-by", "app"],
            ["compare", "old.so", "new.so", "--required-symbol", "sym"],
            ["compare", "old.so", "new.so", "--required-symbols", "syms.txt"],
        ],
    )
    def test_is_consumer_scoped_recognizes_every_dial(self, argv):
        assert ev.is_consumer_scoped({"argv": argv})

    def test_is_consumer_scoped_false_for_an_unscoped_call(self):
        assert not ev.is_consumer_scoped({"argv": ["compare", "old.so", "new.so"]})

    @pytest.mark.parametrize(
        "used_by",
        [
            "workspace/consumer/renderer.so",
            "workspace/consumer/renderer.so.2",
            "workspace/consumer/renderer.dll",
            "workspace/consumer/renderer.dylib",
            "workspace/consumer/renderer.exe",
        ],
    )
    def test_a_compiled_used_by_artifact_matches_the_bare_consumer_name(self, used_by):
        """A real build gives `--used-by` a platform suffix; the plain
        basename match alone (`renderer.so`) never equals the scenario's
        declared bare name (`renderer`) -- Codex review, PR #808."""
        targets = ev.consumer_scope_targets(
            {"argv": ["compare", "old.so", "new.so", "--used-by", used_by]}
        )
        assert "renderer" in targets

    def test_an_unsuffixed_used_by_path_is_unaffected(self):
        """No suffix to strip -- the plain basename match still covers this,
        and stripping must not remove or corrupt it."""
        targets = ev.consumer_scope_targets(
            {
                "argv": [
                    "compare",
                    "old.so",
                    "new.so",
                    "--used-by",
                    "workspace/consumer/renderer",
                ]
            }
        )
        assert "renderer" in targets

    def test_required_symbols_file_contents_contribute_targets(self, tmp_path):
        """`--required-symbols FILE` names a file, not the symbols
        themselves -- the file's own listed symbols must each become a
        target, resolved against the call's own recorded cwd (Codex
        review, PR #808)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "syms.txt").write_text(
            "plugin_register\nplugin_teardown\n", encoding="utf-8"
        )
        call = {
            "argv": [
                "compare",
                "old.so",
                "new.so",
                "--required-symbols",
                "syms.txt",
            ],
            "cwd": str(workspace),
        }
        targets = ev.consumer_scope_targets(call)
        assert {"plugin_register", "plugin_teardown"} <= targets

    def test_contract_mode_extracts_the_declared_domain(self):
        assert (
            ev.contract_mode({"argv": ["compare", "a", "b", "--contract", "exports"]})
            == "exports"
        )
        assert (
            ev.contract_mode({"argv": ["compare", "a", "b", "--contract=public"]})
            == "public"
        )
        assert ev.contract_mode({"argv": ["compare", "a", "b"]}) is None

    def test_required_symbols_file_missing_or_unresolvable_is_a_silent_no_op(
        self, tmp_path
    ):
        """False-negative-over-false-positive: a workspace already gone, or
        no recorded cwd at all, must not raise and must not fabricate a
        match."""
        no_cwd = ev.consumer_scope_targets(
            {"argv": ["compare", "a", "b", "--required-symbols", "syms.txt"]}
        )
        assert no_cwd == frozenset()

        missing_file = ev.consumer_scope_targets(
            {
                "argv": ["compare", "a", "b", "--required-symbols", "syms.txt"],
                "cwd": str(tmp_path / "gone"),
            }
        )
        assert missing_file == frozenset()


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

    def test_activating_the_wrong_skill_fails(self, tmp_path, monkeypatch):
        """With only one skill published (ADR-058's 2026-08-20 portfolio-reset
        amendment), there is no second *real* known skill left to mistakenly
        activate instead — `KNOWN_SKILLS` is patched with a synthetic second
        entry so this exercises the same "activated, but not the scenario's
        own skill" branch a second real skill would."""
        monkeypatch.setattr(dim, "KNOWN_SKILLS", (*dim.KNOWN_SKILLS, "other-skill"))
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"command": "other-skill"},
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
                                "review-native-library-change/SKILL.md"
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
        """Without --contract the coverage axis is identically 0."""
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
        call = a_breaking_call(argv=["compare", "a", "b", "--contract", "public"])
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

    def test_an_uncited_contract_call_does_not_excuse_a_cited_uncontracted_claim(
        self, tmp_path
    ):
        """The refutation must scope to the claim's own *cited* calls, not
        every call the run happened to make -- an unrelated --contract call
        elsewhere in the transcript must not excuse a claim resting on a
        plain, uncontracted comparison (Codex review, PR #808)."""
        cited = a_breaking_call(0, argv=["compare", "a", "b"])
        uncited_contract_call = a_breaking_call(
            1, argv=["compare", "a", "b", "--contract", "public"]
        )
        run = build_run(tmp_path, final="", calls=[cited, uncited_contract_call])
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

    def test_a_scoped_claim_is_not_held_to_an_earlier_unscoped_report(self, tmp_path):
        """The skill's own worked example: run the global comparison, then
        narrow via `--used-by`, and report the *scoped* answer. An earlier,
        more severe unscoped report of the same pair must not fail a claim
        that correctly cites the scoped call — that is a different question,
        not a milder report of the same one (Codex review, PR #808)."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            a_breaking_call(
                1, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            ),
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[1], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "BREAKING"}),
                "captured/1.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                ),
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_scoped_claim_is_still_held_to_its_own_scoped_report(self, tmp_path):
        """Restricting the reckoning to scoped calls must not exempt a claim
        from the severity of the scoped call it actually cites."""
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_failed_scoped_call_does_not_exempt_a_cited_unscoped_report(
        self, tmp_path
    ):
        """A claim citing both a successful unscoped BREAKING report and a
        scoped call that FAILED before producing a verdict (e.g. an invalid
        consumer path, exit 64) must not get the only_scoped exemption --
        the scoped call never backed anything, so the real unscoped report
        must still count (Codex review, PR #808)."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            {
                "seq": 1,
                "call_id": "c1",
                "argv": [
                    "compare",
                    "old.so",
                    "new.so",
                    "--used-by",
                    "no-such-consumer",
                ],
                "exit_code": 64,
                "stdout_path": "captured/1.out",
                "outputs": [],
            },
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0, 1], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_scoped_self_comparison_does_not_exempt_a_cited_unscoped_report(
        self, tmp_path
    ):
        """`compare old.so old.so --used-by renderer` exits with a real
        verdict (trivially NO_CHANGE) while comparing nothing -- it must not
        satisfy the only_scoped exemption any more than a failed scoped call
        does, or a real unscoped BREAKING report gets dropped in favor of a
        scoped call that never actually compared the two sides (Codex
        review, PR #808)."""
        calls = [
            a_breaking_call(0, argv=["compare", "old.so", "new.so"]),
            {
                "seq": 1,
                "call_id": "c1",
                "argv": ["compare", "old.so", "old.so", "--used-by", "renderer"],
                "exit_code": 0,
                "stdout_path": "captured/1.out",
                "outputs": [],
            },
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0, 1], confident=True),
            SCENARIO_COMPATIBLE,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "BREAKING"}),
                "captured/1.out": json.dumps({"verdict": "NO_CHANGE"}),
            },
        )
        assert result.status == "fail"
        assert any("own report" in r for r in result.reasons)

    def test_a_scoped_self_comparison_does_not_satisfy_the_declared_target_check(
        self, tmp_path
    ):
        """The same self-comparison exclusion applied to the declared-target
        check: a self-comparison scoped to the exact right consumer still
        never compared the two sides, so it must not satisfy the requirement
        either."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            {
                "seq": 0,
                "call_id": "c0",
                "argv": ["compare", "old.so", "old.so", "--used-by", "renderer"],
                "exit_code": 0,
                "stdout_path": "captured/0.out",
                "outputs": [],
            }
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "NO_CHANGE"})},
        )
        assert result.status == "fail"
        assert any("declared" in r for r in result.reasons)

    def test_scoping_to_the_wrong_consumer_fails(self, tmp_path):
        """A scenario declaring `invocation.used_by: [renderer]` is testing
        whether the run scoped to *that* consumer specifically -- a call
        scoped to an unrelated one satisfies `is_consumer_scoped()` but never
        answered the actual question (Codex review, PR #808)."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "unrelated"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("declared" in r for r in result.reasons)

    def test_scoping_to_the_declared_consumer_passes(self, tmp_path):
        """The positive control for the check above."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_required_symbol_scoping_is_recognized_too(self, tmp_path):
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"required_symbols": ["plugin_teardown"]},
            "expected": {"verdict": "BREAKING", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_teardown",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="BREAKING",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "BREAKING"})},
        )
        assert result.status == "pass", result.reasons

    def test_a_used_by_path_matches_the_declared_bare_consumer_name(self, tmp_path):
        """`--used-by` takes a real path to the consumer binary
        (references/abicheck-adapter.md's own worked example), not the bare
        logical name a scenario's invocation.used_by declares -- the literal
        operand alone would hard-fail every correctly-scoped run (Codex
        review, PR #808)."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--used-by",
                    "workspace/consumer/renderer",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_covering_only_one_of_two_declared_symbols_fails(self, tmp_path):
        """`plugin-required-symbol-loss` declares BOTH plugin_register and
        plugin_teardown, and the removed one (plugin_teardown) is the whole
        point of the scenario -- a claim citing only a call scoped to the
        unaffected symbol must not pass on partial overlap alone (Codex
        review, PR #808)."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"required_symbols": ["plugin_register", "plugin_teardown"]},
            "expected": {"verdict": "BREAKING", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_register",
                ],
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="BREAKING",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "fail"
        assert any("plugin_teardown" in r for r in result.reasons)

    def test_covering_both_declared_symbols_across_two_calls_passes(self, tmp_path):
        """Coverage can be established across multiple calls, matching a
        real workflow that checks each declared symbol separately."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"required_symbols": ["plugin_register", "plugin_teardown"]},
            "expected": {"verdict": "BREAKING", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_register",
                ],
            ),
            a_breaking_call(
                1,
                argv=[
                    "compare",
                    "old.so",
                    "new.so",
                    "--required-symbol",
                    "plugin_teardown",
                ],
            ),
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="BREAKING",
                full_verdict="BREAKING",
                evidence=[0, 1],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps({"verdict": "COMPATIBLE"}),
                "captured/1.out": json.dumps({"verdict": "BREAKING"}),
            },
        )
        assert result.status == "pass", result.reasons

    def test_a_scenario_requiring_contract_evaluation_needs_a_matching_call(
        self, tmp_path
    ):
        """A plain unscoped compare reporting COMPATIBLE, wrapped in a
        contract_coverage_incomplete caveat, must not pass a scenario whose
        invocation declares `contract_evaluation` unless a cited call
        actually used `--contract` -- coverage is identically 0 without it
        (ADR-049 Phase 7), so there is nothing for the caveat to describe
        (Codex review, PR #808)."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [a_breaking_call(0, argv=["compare", "old.so", "new.so"])]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "fail"
        assert any("--contract" in r for r in result.reasons)

    def test_a_matching_contract_mode_call_satisfies_the_declared_requirement(
        self, tmp_path
    ):
        """The positive control: a cited call using the exact declared
        `--contract` mode passes."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"contract": "exports", "contract_evaluation": True},
            "expected": {
                "verdict": "COMPATIBLE",
                "uncertainty": "contract_coverage_incomplete",
            },
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--contract", "exports"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                evidence=[0],
                confident=False,
                uncertainty={
                    "reason": "contract_coverage_incomplete",
                    "unresolved": "the exports domain",
                },
            ),
            scenario,
            calls=calls,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "pass", result.reasons

    def test_a_scenario_with_no_declared_target_is_unaffected(self, tmp_path):
        """A plain (non-consumer-scoped) scenario declares no `invocation`, so
        this check must not fire at all -- confirming it is additive, not a
        universal new requirement."""
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            SCENARIO_COMPATIBLE,
            artifacts={"captured/0.out": json.dumps({"verdict": "COMPATIBLE"})},
        )
        assert result.status == "pass", result.reasons

    def test_missing_full_verdict_fails_when_the_scenario_declares_one(self, tmp_path):
        """Nothing previously graded `full_verdict` at all -- an agent could
        omit the library-wide result entirely and still pass on the scoped
        verdict alone (Codex review, PR #808)."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(verdict="COMPATIBLE", evidence=[0], confident=True),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("expected a full_verdict" in r for r in result.reasons)

    def test_a_greener_full_verdict_than_the_truth_fails(self, tmp_path):
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="COMPATIBLE",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "fail"
        assert any("full_verdict" in r and "safer" in r for r in result.reasons)

    def test_a_full_verdict_matching_truth_but_not_the_cited_report_fails(
        self, tmp_path
    ):
        """A claim can state the scenario's own truth value "by construction"
        while citing a call whose own JSON report said something else
        entirely -- the rank-based "safer than" checks cannot catch this,
        since claiming BREAKING is never "safer than" a BREAKING truth, but
        the claim is still not backed by what its own citation actually
        showed (Codex review, PR #808)."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "COMPATIBLE"}
                )
            },
        )
        assert result.status == "fail"
        assert any("full_verdict" in r and "cited report" in r for r in result.reasons)

    def test_a_full_verdict_matching_the_cited_report_passes(self, tmp_path):
        """The positive control: a claim whose full_verdict matches what its
        own cited report actually said passes."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            )
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                )
            },
        )
        assert result.status == "pass", result.reasons

    def test_disagreeing_cited_reports_do_not_fabricate_a_mismatch(self, tmp_path):
        """Two cited calls whose own reports disagree on full_verdict leave
        no single value to check the claim against -- degrades to no check
        rather than guessing which one is authoritative."""
        scenario = {
            "skill": "review-native-library-change",
            "invocation": {"used_by": ["renderer"]},
            "expected": {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"},
        }
        calls = [
            a_breaking_call(
                0, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            ),
            a_breaking_call(
                1, argv=["compare", "old.so", "new.so", "--used-by", "renderer"]
            ),
        ]
        result = self._grade(
            tmp_path,
            envelope(
                verdict="COMPATIBLE",
                full_verdict="BREAKING",
                evidence=[0, 1],
                confident=True,
            ),
            scenario,
            calls=calls,
            artifacts={
                "captured/0.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "BREAKING"}
                ),
                "captured/1.out": json.dumps(
                    {"verdict": "COMPATIBLE", "full_verdict": "COMPATIBLE"}
                ),
            },
        )
        assert result.status == "pass", result.reasons

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
        """`--policy` is documented, normal usage. Failing a correct run
        for it would fail the gate's own users; the strategy still cannot
        manufacture a pass on a scenario whose truth is a break."""
        call = a_breaking_call(argv=["compare", "a", "b", "--policy", "p.yaml"])
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
        call = a_breaking_call(argv=["compare", "a", "b", "--policy", "p.yaml"])
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
            "skill": "review-native-library-change",
            "expected": {"verdict": None, "uncertainty": "not_comparable"},
        }
        run = build_run(tmp_path, final=a_not_comparable_answer(evidence=[]), calls=[])
        grade = dim.grade_run(run, scenario)
        assert grade["correct"] is True
        assert grade["zero_tolerance_failed"] == [6]
        statuses = {d["dimension"]: d["status"] for d in grade["dimensions"]}
        assert statuses[2] == "pass"


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
            "skill": "other-skill",
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
            # A boolean flag consumes nothing, so the operand after it is still
            # an operand — `--verbose` is `is_flag=True` in cli_options.py.
            ["compare", "x.so", "--verbose", "x.so"],
            ["compare", "x.so", "-v", "x.so"],
            ["compare", "x.so", "--dso-only", "x.so"],
            # Only `compare` names both sides positionally.
            ["scan", "lib.so", "--against", "lib.so"],
            ["compat", "check", "-old", "a.xml", "-new", "a.xml"],
            ["compat", "check", "-d1", "a.xml", "-d2", "a.xml"],
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
            ["compare", "a.so", "b.so", "--policy", "p.yaml"],
            ["compare", "a.so", "b.so", "-o", "b.so"],
            [
                "compare",
                "a.so",
                "b.so",
                "--suppress",
                "r.yaml",
                "--policy",
                "r.yaml",
            ],
            ["scan", "lib.so", "--against", "base.json"],
            ["compat", "check", "-old", "a.xml", "-new", "b.xml"],
            ["dump", "old.so"],
        ],
    )
    def test_ordinary_invocations_are_not_flagged(self, argv):
        """A report written to a path named like an operand is normal usage;
        reading that as a third operand would fail a correct run."""
        assert not ev.compares_one_side_against_itself({"argv": argv})


class TestShortOptionClusters:
    """Click packs short options, and both readers of an argv must agree.

    Verified against the real CLI: `compare x.so -vv x.so` runs the comparison,
    and `compare ... -voreport.json` writes `report.json`.
    """

    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "x.so", "-vv", "x.so"],
            ["compare", "x.so", "-vvv", "x.so"],
            ["compare", "-oreport.json", "a.so", "a.so"],
            ["compare", "x.so", "x.so", "-voreport.json"],
        ],
    )
    def test_a_cluster_does_not_hide_the_second_side(self, argv):
        assert ev.compares_one_side_against_itself({"argv": argv})

    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "a.so", "b.so", "-vo", "report.json"],
            ["compare", "a.so", "b.so", "-j4"],
            ["compare", "a.so", "b.so", "-oreport.json"],
            # ABICC's vocabulary is single-dash *long* options. Expanding
            # `-old` into `-o ld` made an ordinary comparison read as a
            # self-comparison — a correct run failing the strictest dimension.
            ["compat", "check", "-old", "a.xml", "-new", "b.xml"],
            ["compat", "check", "-d1", "a.xml", "-d2", "b.xml"],
        ],
    )
    def test_an_ordinary_invocation_survives_expansion(self, argv):
        assert not ev.compares_one_side_against_itself({"argv": argv})

    def test_a_declared_long_option_is_not_a_cluster(self):
        assert ev._expand_clusters(["compat", "check", "-old", "a.xml"], "compat") == [
            "compat",
            "check",
            "-old",
            "a.xml",
        ]


class TestVerdictRanking:
    def test_a_verdict_outside_the_vocabulary_has_no_rank(self):
        """A scenario's verdict is not validated the way a claim's is, so a
        drifted `ground_truth.json` spelling used to abort the whole batch."""
        assert claim_mod.rank("PROBABLY_FINE") is None
        assert claim_mod.rank(None) is None
        assert claim_mod.rank("BREAKING") == len(claim_mod.VERDICT_ORDER) - 1

    def test_a_drifted_scenario_verdict_fails_only_its_own_run(self, tmp_path):
        run = build_run(
            tmp_path,
            final=envelope(verdict="BREAKING", evidence=[0], confident=True),
            calls=[a_breaking_call()],
        )
        scenario = {
            "skill": "review-native-library-change",
            "expected": {"verdict": "BROKEN"},
        }
        grade = dim.grade_run(run, scenario)
        assert grade["correct"] is False
        assert grade["zero_tolerance_failed"] == []


class TestNonExecutingModes:
    @pytest.mark.parametrize("flag", ["--help", "-h", "--help-all", "--dry-run"])
    def test_a_mode_that_prints_instead_of_comparing_is_not_evidence(self, flag):
        """`--help-all` was the missing one — verified that `compare old new
        --help-all` exits 0 having printed help, and that help text names
        `verdict` repeatedly, which is what the artifact readers scan."""
        call = {"seq": 0, "argv": ["compare", "old.so", "new.so", flag], "exit_code": 0}
        assert not ev.is_comparison(call)
        assert not ev.ran_to_a_verdict(call)

    def test_the_same_call_without_that_flag_is_evidence(self):
        call = {"seq": 0, "argv": ["compare", "old.so", "new.so"], "exit_code": 0}
        assert ev.ran_to_a_verdict(call)


class TestEagerGlobalOptions:
    def test_a_global_version_exit_is_not_a_comparison(self):
        """`abicheck --version compare old new` prints the version and exits 0
        having compared nothing — verified against the real CLI."""
        call = {
            "seq": 0,
            "argv": ["--version", "compare", "old.so", "new.so"],
            "exit_code": 0,
        }
        assert not ev.is_comparison(call)
        assert not ev.ran_to_a_verdict(call)

    def test_the_commands_own_version_option_is_untouched(self):
        """`compare` declares a value-taking `--version` ("Version label used
        when an input is a bare .so file"), so matching the token anywhere
        would have failed a correct run to catch the empty one."""
        call = {
            "seq": 0,
            "argv": ["compare", "old.so", "new.so", "--version", "1.2"],
            "exit_code": 0,
        }
        assert ev.ran_to_a_verdict(call)

    def test_a_global_flag_before_the_verb_is_not_an_eager_exit(self):
        call = {"seq": 0, "argv": ["-v", "compare", "old.so", "new.so"], "exit_code": 0}
        assert ev.ran_to_a_verdict(call)
