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

"""Evaluate the small GitHub-expression subset this repository's workflows use.

Two guards need to answer "would this actually run / render to this?" from a
workflow's own expressions rather than from its text:
`test_workflow_concurrency_grouping.py` (does a superseding run share a
concurrency group?) and `test_workflow_coverage_consumers.py` (does every
platform that writes a coverage report also have something that reads it?).
The second was written after the first, and a second private copy of an
expression evaluator is exactly the drift `AGENTS.md` warns about — so the
semantics live here once.

Deliberately a *subset*, and the callers are expected to say so: parenthesised
sub-expressions, `||`, `&&`, `==`/`!=` against a literal, `always()`, and
context lookups. Anything unmodelled evaluates falsy rather than raising,
which keeps a guard reporting real findings instead of arguing with syntax it
has not met — at the cost of checking such an expression loosely. That is the
right trade for a guard, and the wrong one for anything that gates behaviour.
"""

from __future__ import annotations

import re
from typing import Any

#: `matrix.os` value -> the `runner.os` GitHub sets for it.
_RUNNER_OS = {"ubuntu": "Linux", "macos": "macOS", "windows": "Windows"}


def runner_os_for(matrix_os: str) -> str:
    """`runner.os` for a `matrix.os` label such as `ubuntu-24.04`."""
    for prefix, name in _RUNNER_OS.items():
        if matrix_os.startswith(prefix):
            return name
    raise ValueError(f"unmodelled runner label: {matrix_os!r}")


def _atom(token: str, ctx: dict[str, Any]) -> Any:
    token = token.strip()
    if token.startswith(("'", '"')):
        return token[1:-1]
    if token in ("always()", "success()"):
        # `always()` is what makes a step run even after an earlier failure;
        # for "does this run on this platform" it is simply true. `success()`
        # is the default and equally true on the path being modelled.
        return True
    if token in ctx:
        value = ctx[token]
        return value if value != "" else False
    if token.startswith(("github.", "matrix.", "runner.", "env.", "needs.")):
        return False  # unmodelled context field: absent, never a guess
    return token


def evaluate(expr: str, ctx: dict[str, Any]) -> Any:
    """Evaluate one expression body (no surrounding `${{ }}`) against *ctx*."""
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        for i, char in enumerate(expr):
            depth += (char == "(") - (char == ")")
            if depth == 0 and i < len(expr) - 1:
                break
        else:
            expr = expr[1:-1].strip()
            continue
        break

    for op in ("||", "&&"):
        depth = 0
        for i in range(len(expr) - 1):
            depth += (expr[i] == "(") - (expr[i] == ")")
            if depth == 0 and expr[i : i + 2] == op:
                left = evaluate(expr[:i], ctx)
                right = evaluate(expr[i + 2 :], ctx)
                if op == "||":
                    return left if left else right
                return right if left else left
    for comparison, negate in (("!=", True), ("==", False)):
        if comparison in expr:
            lhs, rhs = expr.split(comparison, 1)
            equal = _atom(lhs, ctx) == _atom(rhs, ctx)
            return not equal if negate else equal
    return _atom(expr, ctx)


def render(template: str, ctx: dict[str, Any]) -> str:
    """Substitute every `${{ ... }}` in *template* using *ctx*."""

    def _sub(match: re.Match[str]) -> str:
        value = evaluate(match.group(1), ctx)
        return "" if value is False else str(value)

    return re.sub(r"\$\{\{(.+?)\}\}", _sub, template)


def condition_holds(condition: str | None, ctx: dict[str, Any]) -> bool:
    """Whether a step's `if:` would let it run under *ctx*.

    An absent condition is GitHub's default: the step runs. A condition may
    be written with or without the `${{ }}` wrapper; both are accepted, as
    both appear in this repository.
    """
    if condition is None:
        return True
    body = condition.strip()
    wrapped = re.fullmatch(r"\$\{\{(.+)\}\}", body, re.DOTALL)
    if wrapped:
        body = wrapped.group(1)
    return bool(evaluate(body, ctx))
