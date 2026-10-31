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

"""ADR-091 Phase 5: the `--set` config keys grade like the flags they replaced.

`compare --used-by-manifest`, `--required-symbol` and `--view` are gone; their
inputs are now `--set consumers.manifests=`, `--set consumers.required_symbols=`
and `--set output.view=`, and `--severity-preset` is `--set severity.preset=`.
Each pair below states the same request in the old and the new spelling. The
grader must return the same result for both, and the result is asserted to be
the one the request actually implies, so an identical failure cannot pass.
"""

from __future__ import annotations

import importlib.util
import json

import pytest
from _skill_eval_graders_fixtures import (
    ROOT,
    a_breaking_call,
    build_run,
    claim_mod,
    dim,
    envelope,
    ev,
)

_CI_SETUP = ROOT / "skills-src" / "evaluation" / "agents" / "ci-setup"


def _workflow_rules():
    spec = importlib.util.spec_from_file_location(
        "workflow_rules_under_test", _CI_SETUP / "workflow_rules.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _scenario(declared_symbols=None):
    scenario = {"skill": "check-abi-compatibility", "expected": {"verdict": "BREAKING"}}
    if declared_symbols is not None:
        scenario["invocation"] = {"required_symbols": list(declared_symbols)}
    return scenario


def _grade(root, argv, scenario, cwd=None):
    """`(status, sorted reasons)` of dimension 6 for one scoped breaking call."""
    final = envelope(
        verdict="BREAKING", consumer_verdict="BREAKING", evidence=[0], confident=True
    )
    call = a_breaking_call(0, argv=argv)
    if cwd is not None:
        call["cwd"] = cwd
    run = build_run(
        root,
        final=final,
        calls=[call],
        artifacts={
            "captured/0.out": json.dumps(
                {"verdict": "BREAKING", "consumer_scope": {"verdict": "BREAKING"}}
            )
        },
    )
    parsed, status = claim_mod.extract(final)
    result = dim.dimension_6(run, scenario, ev.load_calls(run), parsed, status)
    return result.status, sorted(result.reasons)


#: (old spelling, new spelling, symbols the scenario declares). Every pair asks
#: for the same consumer symbols, so the declared-target check must see them.
SCOPE_PAIRS = [
    (
        ["compare", "old.so", "new.so", "--required-symbol", "plugin_init"],
        [
            "compare",
            "old.so",
            "new.so",
            "--set",
            "consumers.required_symbols=[plugin_init]",
        ],
        ["plugin_init"],
    ),
    (
        [
            "compare",
            "old.so",
            "new.so",
            "--required-symbol",
            "plugin_init",
            "--required-symbol",
            "plugin_teardown",
        ],
        [
            "compare",
            "old.so",
            "new.so",
            "--set",
            "consumers.required_symbols=[plugin_init, plugin_teardown]",
        ],
        ["plugin_init", "plugin_teardown"],
    ),
    (
        ["compare", "old.so", "new.so", "--required-symbol=plugin_init"],
        [
            "compare",
            "old.so",
            "new.so",
            "--set=consumers.required_symbols=[plugin_init]",
        ],
        ["plugin_init"],
    ),
    (
        ["compare", "old.so", "new.so", "--required-symbol", "plugin_init"],
        [
            "compare",
            "old.so",
            "new.so",
            "--set",
            'consumers.required_symbols=["plugin_init"]',
        ],
        ["plugin_init"],
    ),
]


class TestScopeSpellingsGradeIdentically:
    @pytest.mark.parametrize(("old", "new", "declared"), SCOPE_PAIRS)
    def test_old_and_new_spelling_are_both_recognised_as_scoped(
        self, old, new, declared
    ):
        assert ev.is_consumer_scoped({"argv": old})
        assert ev.is_consumer_scoped({"argv": new})
        assert ev.consumer_scope_targets_by_kind({"argv": old}) == (
            ev.consumer_scope_targets_by_kind({"argv": new})
        )

    @pytest.mark.parametrize(("old", "new", "declared"), SCOPE_PAIRS)
    def test_the_grade_is_the_same_for_both_spellings(
        self, tmp_path, old, new, declared
    ):
        old_grade = _grade(tmp_path / "old", old, _scenario(declared))
        new_grade = _grade(tmp_path / "new", new, _scenario(declared))
        assert old_grade == new_grade
        # The request covers what the scenario declared, so the target check
        # passes -- the equality above is not two identical failures.
        assert old_grade[0] == "pass", old_grade

    @pytest.mark.parametrize(("old", "new", "declared"), SCOPE_PAIRS)
    def test_an_undeclared_symbol_fails_under_both_spellings(
        self, tmp_path, old, new, declared
    ):
        scenario = _scenario([*declared, "plugin_not_asked_for"])
        old_grade = _grade(tmp_path / "old", old, scenario)
        new_grade = _grade(tmp_path / "new", new, scenario)
        assert old_grade == new_grade
        assert old_grade[0] == "fail"
        assert any("plugin_not_asked_for" in r for r in old_grade[1]), old_grade

    def test_at_file_contents_grade_the_same_under_both_spellings(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "syms.txt").write_text("plugin_init\n", encoding="utf-8")
        old = ["compare", "old.so", "new.so", "--required-symbol", "@syms.txt"]
        new = [
            "compare",
            "old.so",
            "new.so",
            "--set",
            'consumers.required_symbols=["@syms.txt"]',
        ]
        scenario = _scenario(["plugin_init"])
        old_grade = _grade(tmp_path / "old", old, scenario, cwd=str(workspace))
        new_grade = _grade(tmp_path / "new", new, scenario, cwd=str(workspace))
        assert old_grade == new_grade
        assert old_grade[0] == "pass", old_grade

    def test_a_later_set_repeat_replaces_the_earlier_list(self):
        """`--set` repeats are last-wins, as `parse_set_assignments` makes them."""
        argv = [
            "compare",
            "old.so",
            "new.so",
            "--set",
            "consumers.required_symbols=[plugin_init]",
            "--set",
            "consumers.required_symbols=[plugin_teardown]",
        ]
        targets = ev.consumer_scope_targets_by_kind({"argv": argv})
        assert targets.required_symbols == frozenset({"plugin_teardown"})

    def test_an_empty_list_does_not_scope_the_call(self):
        argv = ["compare", "old.so", "new.so", "--set", "consumers.required_symbols=[]"]
        assert not ev.is_consumer_scoped({"argv": argv})


class TestSeverityPresetSpellings:
    def test_set_severity_preset_reports_as_the_flag_it_replaced(self):
        old = ["compare", "old.so", "new.so", "--severity-preset", "error"]
        new = ["compare", "old.so", "new.so", "--set", "severity.preset=error"]
        assert ev.suppression_flags({"argv": old}) == ["--severity-preset"]
        assert ev.suppression_flags({"argv": new}) == ["--severity-preset"]

    def test_the_grade_is_the_same_for_both_spellings(self, tmp_path):
        old = ["compare", "old.so", "new.so", "--severity-preset", "info-only"]
        new = ["compare", "old.so", "new.so", "--set", "severity.preset=info-only"]
        scenario = _scenario()
        assert _grade(tmp_path / "old", old, scenario) == _grade(
            tmp_path / "new", new, scenario
        )


class TestTheRemainingSpellings:
    def test_output_view_changes_no_grade(self, tmp_path):
        """`output.view` replaced `--view`, which never moved a grade: the
        request with it must grade exactly like the request without it."""
        plain = ["compare", "old.so", "new.so"]
        viewed = [
            "compare",
            "old.so",
            "new.so",
            "--set",
            "output.view=[show=breaking,functions]",
        ]
        assert ev.suppression_flags({"argv": viewed}) == []
        assert not ev.is_consumer_scoped({"argv": viewed})
        scenario = _scenario()
        assert _grade(tmp_path / "plain", plain, scenario) == _grade(
            tmp_path / "viewed", viewed, scenario
        )

    def test_consumers_manifests_is_not_a_scope_dial(self):
        """`--used-by-manifest` never reached the scope dial, so its config
        spelling must not either: the two grade the same, unscoped."""
        argv = [
            "compare",
            "old.so",
            "new.so",
            "--set",
            "consumers.manifests=[app.yml]",
        ]
        assert not ev.is_consumer_scoped({"argv": argv})
        assert ev.consumer_scope_targets_by_kind({"argv": argv}) == (
            ev.consumer_scope_targets_by_kind({"argv": ["compare", "old.so", "new.so"]})
        )

    def test_a_set_value_is_not_an_operand(self):
        """`--set` takes a value, so `compare --set k=v a.so a.so` still names
        one side twice. The value must not be read as an extra operand."""
        call = {"argv": ["compare", "--set", "severity.preset=strict", "a.so", "a.so"]}
        assert ev.compares_one_side_against_itself(call)

    def test_workflow_rules_reads_set_before_the_operands(self):
        rules = _workflow_rules()
        old = rules.parse_cli(
            "compare", ["--severity-preset", "strict", "old.so", "new.so"]
        )
        new = rules.parse_cli(
            "compare", ["--set", "severity.preset=strict", "old.so", "new.so"]
        )
        assert old == new
        assert new["old-library"] == "old.so"
        assert new["new-library"] == "new.so"
