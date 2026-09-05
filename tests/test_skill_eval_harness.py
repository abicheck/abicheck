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

"""The skill-eval harness: what produces a transcript, not what grades one.

Its sibling `test_skill_eval_graders.py` pins the rules applied to a recorded
run. Everything here is about the run being *worth* grading — that the shim
records what happened, that each arm is the treatment it claims, and that the
workspace does not hand either arm the tool or the answer. A grader cannot
detect a run that was contaminated before it started, which is why these checks
live in the runner and are tested here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "agent-evals" / "skills"
sys.path.insert(0, str(EVAL_DIR))


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
    "skill": "check-abi-compatibility",
    "expected": {"verdict": "BREAKING"},
}


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

    def test_write_declares_the_path_half_of_its_operand(self):
        """``--write`` is ``FORMAT=PATH``: only the second half is a path."""
        assert shim._declared_outputs(["compare", "--write", "json=r.json"]) == [
            "r.json"
        ]
        assert shim._declared_outputs(["compare", "--write=json=r.json"]) == ["r.json"]

    def test_same_named_outputs_do_not_overwrite_each_other(self, tmp_path):
        """`-o human/report.json --write json=machine/report.json`."""
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
            "--write",
            "json=machine/report.json",
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
            "baseline", SCENARIO_BREAKING, ["other-skill"]
        )
        assert problem and "baseline arm could see" in problem

    def test_a_skill_arm_seeing_extra_skills_is_not_evidence(self):
        problem = runner.check_treatment(
            "skill",
            SCENARIO_BREAKING,
            sorted([SCENARIO_BREAKING["skill"], "other-skill"]),
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
                "skills": ["pdf", "check-abi-compatibility"],
            }
        ]
        assert runner.visible_native_skills(events) == ["check-abi-compatibility"]
        assert runner.visible_native_skills([]) is None

    def test_a_retired_skill_installed_at_user_scope_is_not_hidden(self):
        """A stale/retired skill must surface, never be filtered out.

        `_published_skill_names()` only reads the *current* checkout's
        published directories — a machine that still has one of the three
        skills removed by ADR-058's 2026-08-20 portfolio-reset amendment (or
        the pre-rename `native-binary-compatibility-review`) installed at
        user scope reports it in the real init event too. Silently dropping
        it here would let `check_treatment()` accept a contaminated
        baseline/skill-arm run as clean evidence instead of rejecting it.
        """
        events = [
            {
                "type": "system",
                "subtype": "init",
                "skills": ["pdf", "native-api-evolution"],
            }
        ]
        assert runner.visible_native_skills(events) == ["native-api-evolution"]

        old_name_events = [
            {
                "type": "system",
                "subtype": "init",
                "skills": ["native-binary-compatibility-review"],
            }
        ]
        assert runner.visible_native_skills(old_name_events) == [
            "native-binary-compatibility-review"
        ]

        # The intermediate name, not just the original and the current one:
        # a host that installed the skill between the two renames (or never
        # updated a user-scope checkout after the second) still has it
        # visible under review-native-library-change specifically — Codex
        # review, PR #811, caught this dropped from the retired-name set
        # when the rename-to-check-abi-compatibility change first landed.
        intermediate_name_events = [
            {
                "type": "system",
                "subtype": "init",
                "skills": ["review-native-library-change"],
            }
        ]
        assert runner.visible_native_skills(intermediate_name_events) == [
            "review-native-library-change"
        ]

    def test_an_unrelated_dev_skill_is_never_a_treatment_conflict(self):
        """`.claude/skills/` also holds `grill-with-docs`, an unrelated
        hand-authored developer skill with nothing to do with the abicheck
        portfolio. Both arms share it identically, so it must never be
        treated as a conflicting treatment — `_published_skill_names()`
        reads `skills-src/` (the abicheck portfolio's own source of truth),
        not `PUBLISHED_SKILLS` (`.claude/skills/`, which mixes the two).
        """
        assert "grill-with-docs" not in runner._published_skill_names()
        events = [
            {
                "type": "system",
                "subtype": "init",
                "skills": ["grill-with-docs", "pdf"],
            }
        ]
        assert runner.visible_native_skills(events) == []
        assert runner.check_treatment("baseline", SCENARIO_BREAKING, []) is None

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
        # `interposed` is stated rather than left to the host: its default is
        # `os.name != "nt"`, and on Windows — where no interposer is installed —
        # every module entry really is a bypass, so an unpinned test asserts
        # something different depending on where it runs. The Windows lane
        # caught exactly that.
        assert runner._bypassed_the_recorder(events, calls, interposed=True)
        calls.write_text(json.dumps({"seq": 0, "argv": ["compare"]}) + "\n")
        assert not runner._bypassed_the_recorder(events, calls, interposed=True)

    def test_the_interposer_falls_through_to_the_real_interpreter(self):
        """A `/bin/sh` script on purpose: one named `python3` whose shebang is
        `/usr/bin/env python3` would re-resolve to itself.

        What it *does* with each spelling is tested by running it —
        `TestInterposerSpellings` — rather than by matching its source text,
        which is how a rewrite silently stopped matching what it asserted.
        """
        script = runner._PYTHON_INTERPOSER
        assert script.startswith("#!/bin/sh")
        assert 'exec "$SKILL_EVAL_REAL_PYTHON" "$@"' in script

    def test_a_run_that_completed_but_was_never_indexed_is_recovered(self, tmp_path):
        out_dir = self._unindexed_run(tmp_path, [SCENARIO_BREAKING["skill"]])
        record = runner._recovered_record(out_dir, "sid", "skill", 0, SCENARIO_BREAKING)
        assert record["recovered"] is True
        assert record["wall_clock_seconds"] == 9.0

    def test_recovery_will_not_launder_a_rejected_run_into_evidence(self, tmp_path):
        """`_run_once` writes final.md *before* checking the treatment, so a
        rejected run looks exactly like a crashed one on the next resume."""
        out_dir = self._unindexed_run(tmp_path, ["other-skill"])
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

    def test_the_scan_reads_json_fixtures_too(self, tmp_path):
        """A pre-dumped snapshot fixture (`not-comparable-pair/{old,new}.json`)
        lands in the workspace verbatim, and a real `AbiSnapshot`'s own field
        names can name the tool even when the value is blank — the *key*
        `abicheck_version` is itself a leak. `.json` was absent from the
        scanned suffixes, so this was invisible until now (Codex review, PR
        #808)."""
        work = tmp_path / "ws"
        work.mkdir()
        (work / "snapshot.json").write_text(
            '{"build_source": {"manifest": {"abicheck_version": ""}}}\n',
            encoding="utf-8",
        )
        leaks = runner.workspace_leaks(work)
        assert len(leaks) == 1
        assert "abicheck" in leaks[0]

    def test_the_installed_skill_is_not_scanned_as_a_leak(self, tmp_path):
        """Naming the tool is the treatment's whole job."""
        work = tmp_path / "ws"
        skill = work / ".claude" / "skills" / "other-skill"
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
            self._scenarios("catalog/cases/case01_symbol_removal")
        ) == {"c"}

    def test_a_cxx_fixture_is_recognized(self):
        assert "c++" in runner.required_languages(
            self._scenarios("catalog/cases/case09_cpp_vtable")
        )

    def test_msvc_counts_as_both_compilers(self):
        """`cl` is one driver for C and C++, and is the normal Windows
        toolchain — without it a Visual Studio host refused to run the two
        scenarios the pack marks windows-supported."""
        assert "cl" in runner._C_COMPILERS and "cl" in runner._CXX_COMPILERS


class TestRecorderBypassDetection:
    def _events(self, command: str) -> list[dict]:
        return [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": command},
                        }
                    ]
                },
            }
        ]

    def _log(self, tmp_path: Path, *, recorded: bool) -> Path:
        calls = tmp_path / "calls.jsonl"
        calls.write_text(
            json.dumps({"seq": 0, "argv": ["--version"]}) + "\n" if recorded else "",
            encoding="utf-8",
        )
        return calls

    def test_a_versioned_interpreter_resolves_past_the_interposer(self, tmp_path):
        """The check used to return early on any non-empty log — and every
        published skill runs `abicheck --version` at preflight, so the log is
        essentially always non-empty by the time a comparison happens. That made
        this backstop dead in practice rather than merely narrow."""
        events = self._events("/usr/bin/python3.12 -m abicheck compare a.so b.so")
        assert runner._bypassed_the_recorder(
            events, self._log(tmp_path, recorded=True), interposed=True
        )

    @pytest.mark.parametrize("spelling", ["python", "python3"])
    def test_the_interposed_spellings_are_accepted(self, tmp_path, spelling):
        events = self._events(f"{spelling} -m abicheck compare a.so b.so")
        assert not runner._bypassed_the_recorder(
            events, self._log(tmp_path, recorded=True), interposed=True
        )

    def test_nothing_recorded_at_all_is_a_bypass_however_it_was_spelled(self, tmp_path):
        events = self._events("python3 -m abicheck compare a.so b.so")
        assert runner._bypassed_the_recorder(
            events, self._log(tmp_path, recorded=False), interposed=True
        )

    def test_without_an_interposer_every_module_entry_bypasses(self, tmp_path):
        """Windows installs none, which is what makes this the likelier case."""
        events = self._events("python3 -m abicheck compare a.so b.so")
        assert runner._bypassed_the_recorder(
            events, self._log(tmp_path, recorded=True), interposed=False
        )

    def test_a_run_that_never_used_the_module_entry_is_not_a_bypass(self, tmp_path):
        events = self._events("abicheck compare a.so b.so")
        assert not runner._bypassed_the_recorder(
            events, self._log(tmp_path, recorded=True), interposed=True
        )
        assert not runner._bypassed_the_recorder(
            events, self._log(tmp_path, recorded=False), interposed=False
        )

    def test_the_interpreter_of_each_invocation_is_recovered(self):
        events = self._events("/opt/py/python3.13 -m abicheck --version")
        assert runner.module_entry_interpreters(events) == ["/opt/py/python3.13"]


class TestRecoveryRechecksEveryRejection:
    """`_run_once` writes `final.md` before both of its rejection checks, so a
    rejected run is indistinguishable from a crashed one on the next resume.
    The treatment check was re-run during recovery; the recorder check was not
    — and on a host without the interposer (Windows) that is the *likelier* of
    the two to be hit, so recovery would index a harness limitation as agent
    behaviour."""

    def _rejected_run(self, tmp_path: Path) -> Path:
        out_dir = tmp_path / "sid" / "skill" / "0"
        out_dir.mkdir(parents=True)
        (out_dir / "final.md").write_text("done", encoding="utf-8")
        (out_dir / "calls.jsonl").write_text("", encoding="utf-8")
        (out_dir / "events.jsonl").write_text(
            "\n".join(
                json.dumps(event)
                for event in (
                    {
                        "type": "system",
                        "subtype": "init",
                        "skills": [SCENARIO_BREAKING["skill"]],
                    },
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Bash",
                                    "input": {
                                        "command": "python -m abicheck compare a.so b.so"
                                    },
                                }
                            ]
                        },
                    },
                )
            ),
            encoding="utf-8",
        )
        return out_dir

    def test_a_run_that_bypassed_the_recorder_is_not_recovered(self, tmp_path):
        out_dir = self._rejected_run(tmp_path)
        with pytest.raises(RuntimeError, match="recorder does not wrap"):
            runner._recovered_record(out_dir, "sid", "skill", 0, SCENARIO_BREAKING)

    def test_the_same_run_with_its_calls_recorded_is_recovered(self, tmp_path):
        out_dir = self._rejected_run(tmp_path)
        (out_dir / "calls.jsonl").write_text(
            json.dumps({"seq": 0, "argv": ["compare", "a.so", "b.so"]}) + "\n",
            encoding="utf-8",
        )
        # Stated, not inherited from the host: without an interposer every
        # module entry is a bypass, so on Windows this run is correctly
        # rejected and the assertion would be about the platform rather than
        # about recovery.
        record = runner._recovered_record(
            out_dir, "sid", "skill", 0, SCENARIO_BREAKING, interposed=True
        )
        assert record["recovered"] is True


class TestModuleEntryDetection:
    """What counts as reaching the tool by a route the recorder does not wrap."""

    def _bash(self, command: str) -> list[dict]:
        return [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": command},
                        }
                    ]
                },
            }
        ]

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("python -m abicheck compare a b", ["python"]),
            ("python3 -m abicheck --version", ["python3"]),
            # CPython accepts the attached form; verified with a real
            # `python -mabicheck --version`.
            ("python -mabicheck compare a b", ["python"]),
            ("/usr/bin/python3.12 -m abicheck compare a b", ["/usr/bin/python3.12"]),
            # `dev` is `-X`'s value, not the interpreter — reading it as one
            # would report an interposed run as a bypass.
            ("python -X dev -m abicheck compare a b", ["python"]),
            ("abicheck compare a b", []),
        ],
    )
    def test_the_interpreter_is_read_from_the_command(self, command, expected):
        assert runner.module_entry_interpreters(self._bash(command)) == expected

    def test_a_file_payload_that_merely_mentions_the_form_is_not_an_invocation(self):
        """Serializing every tool input made any `Write` whose *content* says
        `-m abicheck` — this file among them — register as one, and the word
        before it is not an interpreter, so a clean run aborted."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {"content": "the docs say python -m abicheck"},
                        }
                    ]
                },
            }
        ]
        assert runner.module_entry_interpreters(events) == []


class TestShimShortOptionClusters:
    """Click packs short options; the shim reads the same argv the CLI does."""

    def test_the_short_value_options_match_the_real_cli(self):
        """A literal, because the shim runs as a PATH executable and must not
        depend on abicheck being importable. Pinned so a new short option fails
        loudly instead of going silently unrecognized."""
        import click

        from abicheck.cli import main as cli_main

        declared = {
            opt
            for name in ("compare", "scan")
            for param in cli_main.commands[name].params
            if isinstance(param, click.Option) and not param.is_flag
            for opt in (*param.opts, *param.secondary_opts)
            if len(opt) == 2 and opt.startswith("-") and not opt.startswith("--")
        }
        assert set(shim.SHORT_VALUE_OPTIONS) == declared

    def test_an_output_packed_into_a_cluster_is_recognized(self):
        """`compare ... -voreport.json` writes `report.json` — verified against
        the real CLI. An unrecognized output is never snapshotted."""
        assert shim._declared_outputs(["compare", "a", "b", "-voreport.json"]) == [
            "report.json"
        ]

    def test_a_letter_that_takes_its_own_value_is_not_an_output(self):
        """`-Ho` is `-H` with the value `o`, not an output option."""
        assert shim._declared_outputs(["compare", "a", "b", "-Ho", "inc"]) == []

    def test_a_following_option_is_not_recorded_as_a_path(self):
        """`-o --format json` otherwise put a phantom `--format` in the record."""
        assert shim._declared_outputs(["compare", "a", "b", "-o", "--format"]) == []


class TestInterposerSpellings:
    """Detection and interception must agree on which spellings reach the shim.

    They did not: the backstop reads `python -X dev -m abicheck` as spelled
    with `python` — correct — while the interposer matched `-m` only at `$1`
    and sent that command to the real interpreter. The unrecorded call would
    then have been accepted as interposed, grading a correct comparison as
    having obtained no evidence.
    """

    def _run(self, tmp_path: Path, args: list[str]) -> str:
        import os
        import subprocess

        script = tmp_path / "python"
        script.write_text(runner._PYTHON_INTERPOSER, encoding="utf-8")
        script.chmod(0o755)
        for name, tag in (("shim", "SHIM"), ("real", "REALPY")):
            stub = tmp_path / name
            stub.write_text(f'#!/bin/sh\necho "{tag}: $*"\n', encoding="utf-8")
            stub.chmod(0o755)
        proc = subprocess.run(  # noqa: S603
            [str(script), *args],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "SKILL_EVAL_SHIM": str(tmp_path / "shim"),
                "SKILL_EVAL_REAL_PYTHON": str(tmp_path / "real"),
            },
        )
        return proc.stdout.strip()

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["-m", "abicheck", "compare", "a", "b"], "SHIM: compare a b"),
            (["-mabicheck", "compare", "a", "b"], "SHIM: compare a b"),
            (["-X", "dev", "-m", "abicheck", "compare", "a"], "SHIM: compare a"),
            (["-X", "dev", "-mabicheck", "--version"], "SHIM: --version"),
            (["-W", "ignore", "-m", "abicheck", "--version"], "SHIM: --version"),
            (["-u", "-m", "abicheck", "compare", "a", "b"], "SHIM: compare a b"),
            # Everything else must exec the real interpreter untouched.
            (["-m", "json.tool", "f.json"], "REALPY: -m json.tool f.json"),
            (["script.py", "--flag"], "REALPY: script.py --flag"),
            (["-c", "print(1)"], "REALPY: -c print(1)"),
        ],
    )
    @pytest.mark.skipif(os.name == "nt", reason="the interposer is /bin/sh")
    def test_each_spelling_goes_where_it_should(self, tmp_path, args, expected):
        assert self._run(tmp_path, args) == expected


class TestOneModelPerBatch:
    """The arms run sequentially and nothing pinned a model.

    A default that moves between them — or between a batch and the resume that
    finishes it — would be aggregated by arm alone, so what reads as skill lift
    could be a model difference. Same class of silent confound as the in-repo
    workspace, answered the same way: observe it, record it, refuse when the
    arms are not comparable.
    """

    def test_the_model_is_read_out_of_the_init_event(self):
        events = [{"type": "system", "subtype": "init", "model": "claude-sonnet-5"}]
        assert runner.resolved_model(events) == "claude-sonnet-5"

    def test_an_unreported_model_is_none_rather_than_a_guess(self):
        assert runner.resolved_model([]) is None
        assert runner.resolved_model([{"type": "system", "subtype": "init"}]) is None

    def test_a_run_on_a_different_model_is_refused(self):
        index = [{"model": "claude-sonnet-5"}, {"model": "claude-sonnet-5"}]
        problem = runner.check_one_model(index, {"model": "claude-opus-5"})
        assert problem and "claude-opus-5" in problem and "--model" in problem

    def test_the_same_model_is_accepted(self):
        index = [{"model": "claude-sonnet-5"}]
        assert runner.check_one_model(index, {"model": "claude-sonnet-5"}) is None

    def test_an_unknown_model_does_not_refuse_the_batch(self):
        """None means the CLI never said, not that it differs — refusing there
        would fail a run for the harness's own blind spot."""
        assert runner.check_one_model([{"model": "claude-sonnet-5"}], {}) is None
        assert runner.check_one_model([{}], {"model": "claude-sonnet-5"}) is None


class TestRecoveryPreservesTheModel:
    def test_a_recovered_row_carries_the_model_it_ran_on(self, tmp_path):
        """The resume path is what the one-model guard was written for, and a
        recovered row without a model is invisible to it — the batch would
        then accept the next run on whatever the default moved to."""
        out_dir = tmp_path / "sid" / "skill" / "0"
        out_dir.mkdir(parents=True)
        (out_dir / "final.md").write_text("done", encoding="utf-8")
        (out_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "skills": [SCENARIO_BREAKING["skill"]],
                    "model": "claude-sonnet-5",
                }
            ),
            encoding="utf-8",
        )
        record = runner._recovered_record(
            out_dir, "sid", "skill", 0, SCENARIO_BREAKING, interposed=True
        )
        assert record["model"] == "claude-sonnet-5"
        assert runner.check_one_model([record], {"model": "claude-opus-5"}) is not None


class TestSupportedHere:
    """`supported_here()`'s two independent restriction axes — OS
    (`platforms`) and CPU architecture (`architectures`) — must each gate on
    their own, since a fixture can be OS-portable but architecture-specific
    (a committed, prebuilt binary on one side) or vice versa."""

    def test_no_restriction_runs_everywhere(self):
        assert runner.supported_here({}) is True
        assert runner.supported_here({"platforms": [], "architectures": []}) is True

    def test_platform_restriction_is_honored(self):
        other_platform = next(
            p for p in ("linux", "macos", "windows") if p != runner.host_platform()
        )
        assert runner.supported_here({"platforms": [runner.host_platform()]}) is True
        assert runner.supported_here({"platforms": [other_platform]}) is False

    def test_architecture_restriction_is_honored(self):
        """Regression: this axis did not exist before evidence-too-shallow's
        prebuilt x86_64 binary needed it — before the fix, a scenario
        declaring only `architectures` (no `platforms`) was indistinguishable
        from an unrestricted one."""
        other_arch = "arm64" if runner.host_architecture() != "arm64" else "x86_64"
        assert (
            runner.supported_here({"architectures": [runner.host_architecture()]})
            is True
        )
        assert runner.supported_here({"architectures": [other_arch]}) is False

    def test_both_axes_must_pass(self):
        """A fixture restricted on the host's own platform but the *other*
        architecture must still be refused — one matching axis is not
        enough."""
        other_arch = "arm64" if runner.host_architecture() != "arm64" else "x86_64"
        assert (
            runner.supported_here(
                {
                    "platforms": [runner.host_platform()],
                    "architectures": [other_arch],
                }
            )
            is False
        )
