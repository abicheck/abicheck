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

"""``_lexical_function_parents()`` def-time-subtree scope tests for the
``fact-detector-misuse`` AI-readiness check
(``scripts/fact_detector_misuse.py``, registered by
``scripts/check_ai_readiness.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split out as its own file (rather than appended to
``test_fact_detector_misuse_scoping.py``, already at the architecture
gate's 1200-line test-file cap) for the same reason
``test_fact_detector_misuse_scoping.py`` itself was split out of
``test_fact_detector_misuse.py`` -- see that file's own docstring.

Covers a narrower, more structural bug than the rest of the scoping
suite: `_lexical_function_parents()`'s own recursive descent used to
switch to a `def`/`lambda`'s *new* qualname before visiting its own
default values, parameter annotations, return annotation, and decorators
-- all of which evaluate at def/lambda-creation time, in whatever scope
was active *before* that new qualname takes over (Codex review, fresh
evidence). A lambda or comprehension found inside one of those was
therefore wrongly parented under the function/lambda it is a default
*of*, rather than the scope that actually, syntactically surrounds the
`def`/`lambda` statement itself.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestLexicalFunctionParentsDefTimeSubtrees:
    """Direct end-to-end pins for the def-time-subtree parenting fix."""

    def test_detects_a_comprehension_default_closing_over_an_outer_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` in `f`, then `def g(fact, cb=[fact ==
        other for _ in xs]): ...` -- the comprehension executes while `g`
        is being *defined* (in `f`'s own scope, before `g`'s own
        parameter `fact` even exists), so it genuinely closes over `f`'s
        alias -- but was wrongly parented under `g` itself, where `g`'s
        own same-named parameter incorrectly shadowed it."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, cb=[fact == other for _ in range(3)]):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 20)]

    def test_detects_a_comprehension_default_on_a_lambda(self) -> None:
        """The identical shape for a `lambda`'s own default value, not
        just a named `def`: `g = lambda fact, cb=[fact == other for _ in
        xs]: cb`."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    g = lambda fact, cb=[fact == other for _ in range(3)]: cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 25)]

    def test_ignores_a_comprehension_shadow_genuinely_inside_the_body(
        self,
    ) -> None:
        """Negative control: a comprehension genuinely inside `g`'s own
        *body* (not a default) is still correctly shadowed by `g`'s own
        parameter -- the fix must not widen scope resolution past the
        def-time subtree it's actually about."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact):\n"
            "        return [fact == other for _ in range(3)]\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_plain_closure_with_no_default_is_still_caught(self) -> None:
        """Regression guard on the fix's own implementation: splitting
        the dispatch between a function's def-time subtrees and its body
        must not break the far more common case of an ordinary nested
        function with no parameters/defaults at all closing over an
        outer alias."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g():\n"
            "        return fact == other\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_a_keyword_only_default_after_a_no_default_keyword_arg(
        self,
    ) -> None:
        """`kw_defaults` pairs positionally with `kwonlyargs`, `None` for
        a keyword-only parameter with no default at all -- a real,
        ordinary element of that list, not an edge case to crash on, and
        must not disrupt resolving a *different* keyword-only parameter's
        own comprehension default in the same signature."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, *, cb=[fact == other for _ in range(3)], other_kw):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 23)]

    def test_detects_a_comparison_inside_a_decorator_call(self) -> None:
        """A decorator's own arguments evaluate at def-time in the
        enclosing scope too, the identical timing rule as a default
        value -- `def_time_subtrees()` covers decorators for exactly this
        reason, closing the same class of gap for a shape the reported
        finding didn't name but the same mechanism applies to."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    @deco(fact == other)\n"
            "    def g(fact):\n"
            "        return None\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 10)]


class TestDefContainingQualnamesDefTimeSubtrees:
    """`_def_containing_qualnames()` had the identical unconditional-
    `visit(child, qualname + ".", qualname)` bug `_lexical_function_
    parents()` above was fixed for, in its own separate walk (Codex
    review, fresh evidence, found after the `_lexical_function_parents`
    fix landed): a nested `def`/`lambda`'s own default values, parameter
    annotations, return annotation, and decorators were dispatched under
    the *new* scope instead of the scope that actually, syntactically
    contains the `def`/`lambda` statement."""

    def test_detects_a_lambda_default_nested_inside_another_defs_default(
        self,
    ) -> None:
        """`fact = rec.bases_fact` in `f`, then `def g(fact, cb=lambda
        x=fact: x == other): ...` -- the inner lambda's own `x=fact`
        default evaluates while `g` is being *defined*, in `f`'s own
        scope, before `g`'s own parameter `fact` even exists -- but
        `_def_containing_qualnames()` recorded the lambda's containing
        scope as `g` instead of `f`, so `g`'s own same-named parameter
        `fact` incorrectly appeared to shadow the lambda's `x=fact`
        default and the misuse went undetected."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, cb=lambda x=fact: x == other):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 34)]

    def test_ignores_a_lambda_default_genuinely_inside_the_body(self) -> None:
        """Negative control: a lambda genuinely created *inside* `g`'s own
        body (not as one of `g`'s own def-time subtrees) is correctly
        parented under `g`, so `g`'s own parameter `fact` correctly
        shadows the outer alias there -- this fix must not widen
        resolution past the def-time subtree it's actually about."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact):\n"
            "        cb = lambda x=fact: x == other\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []
