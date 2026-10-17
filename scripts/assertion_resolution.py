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

"""Same-file assertion resolution shared by the test-quality gates.

A leaf module (stdlib ``ast`` only) so ``check_ai_readiness.py`` and
``test_mirror_patterns.py`` resolve "does this test assert?" with one piece of
logic. A test asserts when its own body does, or when it calls a function
defined in the same file that does (transitively). Name-based call matching
is deliberate: it follows ``self._helper()`` and ``helper()`` alike, and a
call it cannot resolve is treated as opaque by the callers, never as a check.
"""

from __future__ import annotations

import ast

# Substrings that mark a call as assertion-bearing: explicit asserts, the
# unittest-style ``self.assert*`` family, ``pytest.raises``/``warns``/``fail``,
# and common project helper-naming (``_check_*``, ``verify_*``, ``*_roundtrip``).
ASSERTION_CALL_HINTS: tuple[str, ...] = (
    "assert",
    "check",
    "verify",
    "expect",
    "validate",
    "ensure",
    "roundtrip",
    "raises",
    "warns",
    "fail",
)

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def call_attr_or_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def has_direct_assertion(fn: ast.AST) -> bool:
    """True if *fn*'s body itself asserts (assert stmt, with-block, or a call
    whose name hints at an assertion)."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        # ``with pytest.raises(...)`` / ``with caplog ...`` express expectations.
        if isinstance(node, ast.With | ast.AsyncWith):
            return True
        if isinstance(node, ast.Call):
            name = call_attr_or_name(node).lower()
            if any(h in name for h in ASSERTION_CALL_HINTS):
                return True
    return False


def called_function_names(fn: ast.AST) -> set[str]:
    return {call_attr_or_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}


def function_index(tree: ast.AST) -> dict[str, FunctionNode]:
    """Every function in *tree* by name; the first definition wins."""
    funcs: dict[str, FunctionNode] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            funcs.setdefault(node.name, node)
    return funcs


def asserting_function_names(funcs: dict[str, FunctionNode]) -> set[str]:
    """Names in *funcs* that assert directly or via a same-file callee.

    Propagates to a fixed point: a function asserts if it calls a function
    that asserts.
    """
    asserting = {name for name, fn in funcs.items() if has_direct_assertion(fn)}
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in asserting:
                continue
            if called_function_names(fn) & asserting:
                asserting.add(name)
                changed = True
    return asserting
