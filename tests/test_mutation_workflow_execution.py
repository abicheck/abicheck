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

"""Execute mutation.yml's "Flag a cancelled or incomplete run" step for real.

`test_mutation_workflow_contract.py::test_no_step_expands_a_github_expression_
inside_its_script` already asserts this step's *text* — that no ``${{ }}``
appears inside its `run:` body, i.e. that `EVENT_NAME` reaches the shell only
through `env:`. That is a real guard, and it is also exactly the shape that
let #705 ship: asserting a workflow's text proves nothing about what happens
when a hostile value actually reaches the shell — #758 had to add the
executing test afterwards. This does that for the one new step PR #877 added,
mirroring `test_reusable_workflow_execution.py`'s established pattern.

`github.event_name` is not realistically attacker-influenced (it is one of a
fixed set of trigger names GitHub itself supplies, not PR content) — but this
step's own trust boundary is "a value reaches the shell via `env:` rather than
inline `${{ }}` substitution" full stop, independent of how controllable that
particular value is in practice, and this repo's own convention (this file's
own docstring, `test_reusable_workflow_execution.py`'s) is to verify that
boundary by execution rather than take it on faith.
"""

from __future__ import annotations

import getpass

import pytest
from _workflow_exec import find_run_step, have_bash, make_workspace, run_step

MUTATION_WORKFLOW = "mutation.yml"
STEP_NAME = "Flag a cancelled or incomplete run"

pytestmark = pytest.mark.skipif(not have_bash(), reason="needs a real bash")

HOSTILE_EVENT_NAMES = [
    pytest.param("pull_request", id="real-value-baseline"),
    pytest.param("schedule", id="real-value-schedule"),
    pytest.param("$(whoami)", id="command-substitution"),
    pytest.param("`whoami`", id="backticks"),
    pytest.param("schedule; rm -rf /", id="shell-metacharacters"),
    pytest.param('schedule" && echo pwned && echo "', id="quote-breakout"),
    pytest.param("schedule\ninjected line", id="newline-injection"),
]


def _flag_step(tmp_path, event_name: str):
    step = find_run_step(MUTATION_WORKFLOW, "mutmut", STEP_NAME)
    workspace = make_workspace(tmp_path)
    summary = workspace / "_step_summary"
    summary.write_text("", encoding="utf-8")
    result = run_step(
        step,
        workspace=workspace,
        env={"EVENT_NAME": event_name, "GITHUB_STEP_SUMMARY": str(summary)},
    )
    return result, summary.read_text(encoding="utf-8")


class TestFlagCancelledRunUnderHostileEventName:
    @pytest.mark.parametrize("event_name", HOSTILE_EVENT_NAMES)
    def test_the_step_never_executes_the_value_as_code(
        self, tmp_path, event_name: str
    ) -> None:
        """The actual security property: a value that would run a command if
        it reached the shell unquoted must not have done so. `whoami`'s real
        output (the sandbox's own login name) would prove execution
        happened — its absence is the check. (A hostile value containing
        literal text like ``echo pwned`` is expected to appear verbatim in
        the summary — that's the safe, quoted-expansion outcome this test
        exists to confirm, checked by the sibling test below; only real
        code execution, not the mere presence of shell-looking text, is
        the failure this test watches for.)"""
        result, summary = _flag_step(tmp_path, event_name)
        assert result.returncode == 0, result.stderr
        assert getpass.getuser() not in summary

    @pytest.mark.parametrize("event_name", HOSTILE_EVENT_NAMES)
    def test_the_hostile_value_appears_verbatim_not_substituted(
        self, tmp_path, event_name: str
    ) -> None:
        result, summary = _flag_step(tmp_path, event_name)
        assert result.returncode == 0, result.stderr
        assert event_name in summary, (
            f"expected the literal value {event_name!r} to appear verbatim in "
            f"the step summary (proving it was quoted data, not executed); "
            f"got:\n{summary}"
        )

    def test_a_real_schedule_event_still_names_the_baseline_drift_gap(
        self, tmp_path
    ) -> None:
        """Positive control: the step's actual purpose still works for a real
        value, not just "doesn't blow up" for a hostile one."""
        _, summary = _flag_step(tmp_path, "schedule")
        assert "baseline drift check did **not**" in summary

    def test_a_non_schedule_event_does_not_mention_the_baseline_drift_gap(
        self, tmp_path
    ) -> None:
        _, summary = _flag_step(tmp_path, "pull_request")
        assert "baseline drift" not in summary
