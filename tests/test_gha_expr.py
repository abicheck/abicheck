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

"""Contract tests for ``_gha_expr.eval_gha_expression`` -- the primitive
the consumer_compile end-to-end propagation test builds on (bug-class-
regression-testing.md Phase 6). Per this repo's own "Primitive-level
property tests" convention, the primitive gets its own direct coverage
rather than only being exercised through its one caller.
"""

from __future__ import annotations

import pytest
from _gha_expr import GhaExpressionError, eval_gha_expression


def test_bare_reference() -> None:
    assert (
        eval_gha_expression("${{ inputs.gcc-path }}", inputs={"gcc-path": "x"}) == "x"
    )


def test_string_literal() -> None:
    assert eval_gha_expression("${{ '' }}", inputs={}) == ""


def test_missing_property_is_empty_string_not_an_error() -> None:
    """A genuinely-omitted matrix key (RunPlanCheck.to_dict()'s own
    falsy-field-omission convention) must evaluate to "" (CodeRabbit
    review, PR #906: GitHub's own contexts reference documents that
    dereferencing a nonexistent context property evaluates to an empty
    string, not `null` -- an earlier revision returned `None`, which is
    falsy the same way "" is under short-circuit `||`/`&&`, but made
    `matrix.absent == ''` wrongly evaluate `False` instead of the `True` a
    real GHA runtime produces)."""
    assert eval_gha_expression("${{ matrix.absent }}", matrix={}) == ""


def test_missing_property_equals_empty_string() -> None:
    """The real-world case the `None` bug above actually broke: comparing
    an unset property against the empty-string literal, exactly as several
    real workflow expressions in this repo do."""
    assert eval_gha_expression("${{ matrix.absent == '' }}", matrix={}) is True


def test_unsupplied_context_name_is_a_caller_error() -> None:
    with pytest.raises(GhaExpressionError, match="not provided"):
        eval_gha_expression("${{ matrix.x }}", inputs={})


def test_or_short_circuits_on_first_truthy() -> None:
    assert (
        eval_gha_expression(
            "${{ matrix.a || inputs.b }}", matrix={"a": "left"}, inputs={"b": "right"}
        )
        == "left"
    )


def test_or_falls_through_on_falsy_left() -> None:
    assert (
        eval_gha_expression(
            "${{ matrix.a || inputs.b }}", matrix={"a": ""}, inputs={"b": "right"}
        )
        == "right"
    )


def test_and_returns_right_when_left_truthy() -> None:
    assert (
        eval_gha_expression(
            "${{ matrix.a && matrix.b }}", matrix={"a": True, "b": "value"}
        )
        == "value"
    )


def test_and_short_circuits_on_falsy_left() -> None:
    assert (
        eval_gha_expression("${{ matrix.a && matrix.b }}", matrix={"a": False}) is False
    )


def test_inequality() -> None:
    assert eval_gha_expression(
        "${{ matrix.kind != 'bundle' }}", matrix={"kind": "target"}
    )
    assert not eval_gha_expression(
        "${{ matrix.kind != 'bundle' }}", matrix={"kind": "bundle"}
    )


def test_equality() -> None:
    assert eval_gha_expression(
        "${{ matrix.kind == 'bundle' }}", matrix={"kind": "bundle"}
    )


def test_parentheses_change_grouping() -> None:
    """Codex review (PR #906): the original ctx (``a=""``) made both
    groupings resolve to the same value (`""`), so a precedence or
    parenthesis-handling regression could have kept this test green. A
    truthy `a` alongside a falsy `c` is what actually distinguishes the two
    groupings -- without parens, `&&` binds tighter, so `a || b && c` is
    `a || (b && c)`, and `a`'s own short-circuit on `||` means `b && c` is
    never even reached; with parens it's `(a || b) && c`, which reaches
    `&& c` and returns `c`'s falsy value instead."""
    # Without parens, && binds tighter than ||, so this would be
    # `a || (b && c)`; with parens it's `(a || b) && c`.
    ctx = {"a": "A", "b": "", "c": ""}
    assert (
        eval_gha_expression("${{ matrix.a || matrix.b && matrix.c }}", matrix=ctx)
        == "A"
    )
    assert (
        eval_gha_expression("${{ (matrix.a || matrix.b) && matrix.c }}", matrix=ctx)
        == ""
    )


def test_unrecognized_token_raises() -> None:
    with pytest.raises(GhaExpressionError):
        eval_gha_expression("${{ matrix.a ! matrix.b }}", matrix={})


@pytest.mark.parametrize(
    ("expr", "matrix", "inputs", "expected"),
    [
        pytest.param(
            "${{ (matrix.kind != 'bundle' && matrix.consumer_compile_active) && "
            "(matrix.consumer_compile_gcc_path || inputs.gcc-path) || '' }}",
            {
                "kind": "target",
                "consumer_compile_active": True,
                "consumer_compile_gcc_path": "/opt/llvm-20/bin/clang++",
            },
            {"gcc-path": ""},
            "/opt/llvm-20/bin/clang++",
            id="active-overlay-with-resolved-path",
        ),
        pytest.param(
            "${{ (matrix.kind != 'bundle' && matrix.consumer_compile_active) && "
            "(matrix.consumer_compile_gcc_path || inputs.gcc-path) || '' }}",
            {"kind": "target", "consumer_compile_active": False},
            {"gcc-path": "/opt/host/gcc"},
            "",
            id="inactive-overlay-never-falls-back-to-workflow-global",
        ),
        pytest.param(
            "${{ (matrix.kind != 'bundle' && matrix.consumer_compile_active) && "
            "(matrix.consumer_compile_gcc_path || inputs.gcc-path) || '' }}",
            {"kind": "bundle", "consumer_compile_active": True},
            {"gcc-path": "/opt/host/gcc"},
            "",
            id="bundle-cell-never-gets-consumer-fields",
        ),
    ],
)
def test_real_consumer_gcc_path_expression_from_check_project_yml(
    expr: str, matrix: dict, inputs: dict, expected: str
) -> None:
    """The exact expression text lifted from ``check-project.yml``'s own
    ``consumer-gcc-path:`` line (see `test_consumer_compile_full_chain_
    propagation.py`, which extracts it from the real file rather than
    retyping it here)."""
    assert eval_gha_expression(expr, matrix=matrix, inputs=inputs) == expected
