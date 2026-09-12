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

"""Contract of the shared GitHub-expression evaluator.

`_gha_expressions` is a primitive two guards depend on, so it gets the
standalone contract suite `AGENTS.md` asks for ("Primitive-level property
tests") rather than being exercised only through its callers' domain logic.

The load-bearing property is **which way it fails**. Both callers ask "would
this step run / does this render to that", and both are guards: an
unmodelled expression must read as *absent*, never as *present*. Returning
the raw token made a non-empty string truthy, so a step gated on
`failure()` or a `steps.*` output evaluated as "runs" — which in
`test_workflow_coverage_consumers.py` made such a step look like an active
consumer unconditionally, masking the orphaned-report defect that guard
exists to catch (CodeRabbit review on PR #1244). A guard that fails open is
worse than no guard, because it reads as coverage.

The one-line fix suggested with that finding — return `False` from the
final fallback — would have been wrong in a way the tests below pin:
`examples-validation.yml` really does compare against a bare numeric
literal (`matrix.shard == 1`), and collapsing literals to `False` silently
changes that answer. Literals stay literals; only *references* go absent.
"""

from __future__ import annotations

import pytest
from _gha_expressions import condition_holds, evaluate, render, runner_os_for

_CTX = {
    "github.event_name": "push",
    "github.ref": "refs/heads/main",
    "matrix.os": "ubuntu-24.04",
    "matrix.shard": "1",
    "runner.os": "Linux",
}


@pytest.mark.parametrize(
    "expr",
    [
        "failure()",
        "cancelled()",
        "contains(github.event.pull_request.labels.*.name, 'skip')",
        "steps.probe.outputs.result",
        "fromJson(needs.setup.outputs.matrix)",
        "some_bareword",
        "github.event.merge_group.head_sha",
    ],
)
def test_unmodelled_expressions_are_absent_not_present(expr: str) -> None:
    """The direction that matters: never truthy by accident."""
    assert evaluate(expr, _CTX) is False
    assert condition_holds(expr, _CTX) is False
    assert condition_holds("${{ " + expr + " }}", _CTX) is False


def test_literals_survive_the_absent_rule() -> None:
    """The complement, and why the suggested one-liner was not taken: a bare
    numeric or boolean literal is a *value*, not a reference."""
    assert evaluate("matrix.shard == 1", _CTX) is True
    assert evaluate("matrix.shard == 2", _CTX) is False
    assert evaluate("true", _CTX) is True
    assert evaluate("false", _CTX) is False
    assert evaluate("'push' == 'push'", _CTX) is True


def test_a_real_workflow_condition_shape_still_evaluates() -> None:
    """The exact `if:` that carries the numeric literal in this repository,
    so the rule above is pinned against real syntax rather than a sample."""
    expr = "always() && matrix.toolchain == 'gcc' && matrix.shard == 1"
    assert evaluate(expr, {**_CTX, "matrix.toolchain": "gcc"}) is True
    assert evaluate(expr, {**_CTX, "matrix.toolchain": "clang"}) is False


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("runner.os != 'Windows'", True),
        ("runner.os == 'Windows'", False),
        ("github.event_name == 'push' || github.event_name == 'schedule'", True),
        ("(github.event_name == 'pull_request') && runner.os == 'Linux'", False),
        ("always() && matrix.os == 'ubuntu-24.04'", True),
    ],
)
def test_supported_operators(expr: str, expected: bool) -> None:
    assert bool(evaluate(expr, _CTX)) is expected


def test_absent_condition_means_the_step_runs() -> None:
    """GitHub's default, and the reason `condition_holds` takes `None`."""
    assert condition_holds(None, _CTX) is True


def test_render_substitutes_and_drops_absent_fields() -> None:
    assert render("x-${{ github.event_name }}", _CTX) == "x-push"
    assert render("x-${{ steps.nope.outputs.v }}", _CTX) == "x-"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("ubuntu-24.04", "Linux"),
        ("macos-latest", "macOS"),
        ("windows-latest", "Windows"),
    ],
)
def test_runner_os_mapping(label: str, expected: str) -> None:
    assert runner_os_for(label) == expected


def test_an_unmodelled_runner_label_raises_rather_than_guessing() -> None:
    """The one place this module *does* raise: a matrix OS it cannot map
    would otherwise silently mis-evaluate every `runner.os` condition for
    that platform, which is a wrong answer rather than a missing one."""
    with pytest.raises(ValueError, match="unmodelled runner label"):
        runner_os_for("freebsd-14")
