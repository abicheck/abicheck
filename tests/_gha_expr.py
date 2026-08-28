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

"""A tiny, real GitHub Actions expression evaluator -- just enough of the
``${{ ... }}`` grammar this repository's own workflow/action ``with:``/
``if:`` values actually use (dotted context references, string literals,
``!=``/``==``, ``&&``/``||`` with real short-circuit/truthiness semantics,
parens) to let a test *evaluate* a chain of real, unmodified expressions
end to end with one sentinel value, rather than asserting that each
expression's *text* merely contains the right substring.

Substring assertions (what most of this repo's existing workflow-wiring
tests do, and what they should keep doing as a cheap guard -- see `tests/
CLAUDE.md`'s own note on `_workflow_exec.py`) prove an expression *names*
the right upstream field. They do not prove the expression's *semantics*
are still correct: a refactor that swaps `&&`/`||`, inverts a guard, or
reorders a ternary's branches can still contain every substring a
text-only test checks for, while silently changing which value actually
reaches the next hop (Codex review, PR #906 -- the exact class of gap
#705/#758 already illustrate for a different workflow-security property).
Threading one real sentinel value through the real, unmodified expression
text at each hop is what actually rules that class out.

Deliberately narrow: no ``fromJSON``/``toJSON``/function calls, no
``!``/unary operators, no arithmetic -- only the subset this repo's own
``consumer_compile`` forwarding chain (and its likely siblings) actually
uses. Extend the grammar only when a real expression needs it; guessing
ahead of that need risks silently misevaluating a construct nobody has
exercised.
"""

from __future__ import annotations

import re
from typing import Any

_EXPR_RE = re.compile(r"^\s*\$\{\{(.*)\}\}\s*$", re.DOTALL)

# Token patterns, tried in order. Whitespace is skipped between tokens.
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    |(?P<op>&&|\|\||!=|==|\(|\))
    |(?P<string>'(?:[^'\\]|\\.)*')
    |(?P<ident>[A-Za-z_][A-Za-z0-9_.-]*)
    """,
    re.VERBOSE,
)


class GhaExpressionError(ValueError):
    """Raised for a construct this evaluator's deliberately narrow grammar
    doesn't cover, or a malformed expression -- never silently mis-parsed."""


def _tokenize(src: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise GhaExpressionError(
                f"unrecognized token at {src[pos : pos + 20]!r} in {src!r}"
            )
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        tokens.append(m.group())
    return tokens


class _Parser:
    """Recursive-descent over the precedence chain
    ``or -> and -> equality -> primary``, mirroring GHA's own (JS-derived)
    operator precedence for the operators this grammar supports."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> str:
        tok = self._peek()
        if tok is None:
            raise GhaExpressionError("unexpected end of expression")
        self._pos += 1
        return tok

    def parse(self) -> _Node:
        node = self._or_expr()
        if self._peek() is not None:
            raise GhaExpressionError(f"unexpected trailing token {self._peek()!r}")
        return node

    def _or_expr(self) -> _Node:
        left = self._and_expr()
        while self._peek() == "||":
            self._advance()
            left = _Or(left, self._and_expr())
        return left

    def _and_expr(self) -> _Node:
        left = self._equality_expr()
        while self._peek() == "&&":
            self._advance()
            left = _And(left, self._equality_expr())
        return left

    def _equality_expr(self) -> _Node:
        left = self._primary()
        if self._peek() in ("!=", "=="):
            op = self._advance()
            right = self._primary()
            return _Eq(left, right, negate=(op == "!="))
        return left

    def _primary(self) -> _Node:
        tok = self._peek()
        if tok == "(":
            self._advance()
            node = self._or_expr()
            if self._advance() != ")":
                raise GhaExpressionError("expected ')'")
            return node
        if tok is None:
            raise GhaExpressionError("unexpected end of expression")
        if tok.startswith("'"):
            self._advance()
            return _Literal(tok[1:-1].replace("\\'", "'"))
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", tok):
            self._advance()
            return _Ref(tok)
        raise GhaExpressionError(f"unexpected token {tok!r}")


class _Node:
    def eval(self, context: dict[str, Any]) -> Any:
        raise NotImplementedError


class _Literal(_Node):
    def __init__(self, value: str) -> None:
        self.value = value

    def eval(self, context: dict[str, Any]) -> Any:
        return self.value


class _Ref(_Node):
    """A dotted context reference, e.g. ``matrix.consumer_compile_active``
    or ``inputs.gcc-path`` -- the second segment may contain hyphens
    (a real GHA input-name character), so this splits only on the first
    ``.``, not on every one.

    A genuinely-unset property (e.g. a JSON matrix cell that omits a key
    entirely -- ``RunPlanCheck.to_dict()``'s own convention for a falsy
    field) evaluates to ``""``, matching real GHA runtime behavior for
    referencing an undefined context property (per GitHub's own contexts
    reference: dereferencing a nonexistent context property evaluates to
    an empty string, not `null` -- CodeRabbit review, PR #906, fresh
    evidence: an earlier revision returned `None` here, which is falsy the
    same way `""` is under `_truthy()`/`||`/`&&`, but made
    `matrix.absent == ''` -- itself a legitimate, real expression shape --
    incorrectly evaluate `False` instead of the `True` a real GHA runtime
    would produce). This is a legitimate, common shape this grammar must
    evaluate correctly, since several of this repo's own expressions rely
    on exactly that omission-means-falsy behavior
    (``matrix.consumer_compile_gcc_path || ...``). Only a context *name*
    the caller never supplied at all (``matrix``/``inputs`` themselves) is
    treated as a caller error.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def eval(self, context: dict[str, Any]) -> Any:
        head, _, rest = self.path.partition(".")
        if head not in context:
            raise GhaExpressionError(
                f"context {head!r} not provided to the evaluator "
                f"(referenced as {self.path!r})"
            )
        node = context[head]
        if not isinstance(node, dict):
            raise GhaExpressionError(
                f"context {head!r} must be a dict, got {type(node).__name__}"
            )
        return node.get(rest, "")


class _Eq(_Node):
    def __init__(self, left: _Node, right: _Node, *, negate: bool) -> None:
        self.left, self.right, self.negate = left, right, negate

    def eval(self, context: dict[str, Any]) -> Any:
        result = self.left.eval(context) == self.right.eval(context)
        return (not result) if self.negate else result


def _truthy(value: Any) -> bool:
    """GHA's own (JS-derived) truthiness: ``False``/``""``/``0``/``None``
    are falsy, everything else -- including a non-empty string -- is
    truthy. Matches what every ``&&``/``||`` short-circuit in this
    repo's own workflow/action YAML relies on."""
    return bool(value)


class _And(_Node):
    def __init__(self, left: _Node, right: _Node) -> None:
        self.left, self.right = left, right

    def eval(self, context: dict[str, Any]) -> Any:
        left_value = self.left.eval(context)
        return self.right.eval(context) if _truthy(left_value) else left_value


class _Or(_Node):
    def __init__(self, left: _Node, right: _Node) -> None:
        self.left, self.right = left, right

    def eval(self, context: dict[str, Any]) -> Any:
        left_value = self.left.eval(context)
        return left_value if _truthy(left_value) else self.right.eval(context)


def eval_gha_expression(expr: str, **contexts: dict[str, Any]) -> Any:
    """Evaluate one real ``${{ ... }}`` workflow/action expression string
    against the supplied contexts (e.g. ``matrix={...}, inputs={...}``).

    Raises :class:`GhaExpressionError` for anything outside this module's
    deliberately narrow grammar or for a referenced key the caller didn't
    supply -- both are meant to fail loudly rather than silently
    misevaluate, since a caller relying on this for a propagation-chain
    proof needs to know when the real expression outgrew what this
    evaluator models, not get a wrong answer back.
    """
    m = _EXPR_RE.match(expr)
    inner = m.group(1) if m else expr
    tokens = _tokenize(inner)
    node = _Parser(tokens).parse()
    return node.eval(contexts)
