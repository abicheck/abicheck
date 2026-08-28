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

"""Unit-test mirror of the ``fact-detector-misuse`` AI-readiness check
(``scripts/fact_detector_misuse.py``, registered by
``scripts/check_ai_readiness.py``) — ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

The check ERRORs on any ``==``/``!=`` comparison, anywhere under
``abicheck/``, where at least one side is recognizably ``Fact[T]``-typed —
a `<attr>_fact` field access (``bases_fact``/``virtual_bases_fact``/
``vtable_fact``/``vptr_offset_bits_fact``/``is_va_list_fact``) or a
``Fact(...)``/``Fact.<classmethod>(...)`` constructor call. Unlike the
sibling ``fact-field-readers`` check, this one ships with **no baseline**:
zero such comparisons exist under ``abicheck/`` today (verified by running
the real scan), so any hit is an unconditional error, not an allowlisted
one. This file pins that the real repository is clean and that the
detection logic itself actually catches the misuse pattern
``abicheck/model/fact.py``'s own docstring describes.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import Findings  # noqa: E402
from scripts.fact_detector_misuse import (  # noqa: E402
    FACT_FIELD_NAMES,
    check_fact_detector_misuse,
    fact_equality_misuse_sites,
)


def test_no_violation_in_real_repo() -> None:
    """The real repository has zero `Fact[T]` equality-misuse sites under
    `abicheck/` — this check has no baseline, so any hit at all is an
    error; this pins that the check is clean against the actual tree."""
    findings = Findings()
    check_fact_detector_misuse(findings)
    errors = [m for c, m in findings.errors if c == "fact-detector-misuse"]
    assert errors == [], "Fact[T] equality misuse:\n" + "\n".join(errors)


class TestFactEqualityMisuseSites:
    """Direct tests on the AST-walking primitive, independent of the real
    repository tree — pins the detection logic's own contract."""

    @pytest.mark.parametrize("attr", sorted(FACT_FIELD_NAMES))
    def test_detects_a_comparison_between_two_fact_attrs(self, attr: str) -> None:
        src = f"def f(a, b):\n    return a.{attr} == b.{attr}\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_not_equal_too(self) -> None:
        src = "def f(a, b):\n    return a.vtable_fact != b.vtable_fact\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_comparison_against_a_fact_constructor_call(self) -> None:
        src = "def f(rec):\n    return rec.bases_fact == Fact.present([])\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_comparison_against_a_bare_fact_call(self) -> None:
        src = "def f(rec, status):\n    return rec.bases_fact == Fact(status)\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_each_pair_in_a_chained_comparison(self) -> None:
        """`a == b == c` is two adjacent comparisons, not one — both should
        be caught if the relevant operand is Fact-typed."""
        src = "def f(a, b, c):\n    return a.vtable_fact == b.vtable_fact == c\n"
        tree = ast.parse(src, filename="x.py")
        # Both (a.vtable_fact == b.vtable_fact) and (b.vtable_fact == c)
        # involve a Fact-typed operand, so both pairs are reported — same
        # ast.Compare node, so both sites share the node's own location.
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11), (2, 11)]

    def test_ignores_identity_comparison(self) -> None:
        """`is`/`is not` (e.g. checking whether a Fact sibling was ever
        supplied, `model/fact.py`'s own bridge pattern) is not the misuse
        this check exists to catch — only `==`/`!=`."""
        src = "def f(rec):\n    return rec.bases_fact is None\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_ordinary_comparison(self) -> None:
        src = "def f(rec):\n    return rec.size_bits == 64\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_call_to_an_unrelated_function_named_like_a_classmethod(
        self,
    ) -> None:
        """`SomethingElse.present(x) == y` must not match merely because
        the *method* name happens to collide — only a call on the bare
        name `Fact` itself counts."""
        src = "def f(x, y):\n    return SomethingElse.present(x) == y\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_comparison_through_a_local_alias(self) -> None:
        """`old_fact = old.bases_fact` then `old_fact == new_fact` -- both
        operands are bare `ast.Name`s, invisible to attribute/call matching
        alone (Codex review: an ordinary local-variable refactor must not
        launder this misuse past the gate)."""
        src = (
            "def f(old, new_fact):\n"
            "    old_fact = old.bases_fact\n"
            "    return old_fact == new_fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert sites == [(3, 11)]

    def test_detects_a_comparison_through_a_chained_alias(self) -> None:
        """`first = rec.bases_fact; second = first; second == other` --
        `second`'s own RHS is a bare `ast.Name` (`first`), not directly
        Fact-typed, so a single pass over assignments alone would stop at
        `first` (Codex review: a second ordinary local-variable refactor
        must not launder past the alias-tracking fix either)."""
        src = (
            "def f(rec, other):\n"
            "    first = rec.bases_fact\n"
            "    second = first\n"
            "    return second == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 11)]

    def test_detects_a_comparison_through_an_annotated_local_assignment(
        self,
    ) -> None:
        """`old_fact: Fact[list[str]] = old.bases_fact` is an `ast.AnnAssign`,
        a distinct node type the original `ast.Assign`-only candidate
        collection never matched at all (Codex review: the ordinary
        annotated-assignment spelling must not bypass the gate)."""
        src = (
            "def f(old, other):\n"
            "    old_fact: Fact[list[str]] = old.bases_fact\n"
            "    return old_fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_bare_annotated_local_with_no_value(self) -> None:
        """The annotation alone is an unconditional signal, mirroring the
        function-parameter case -- `old_fact: Fact[list[str]]` with no RHS
        at all is still Fact-typed."""
        src = "def f(other):\n    old_fact: Fact[list[str]]\n    return old_fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_comparison_through_a_closure_over_an_outer_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` in an outer function, then `def
        inner(): return fact == other` -- `inner`'s own qualname has no
        assignment of its own establishing `fact`, but it's a real,
        visible closure variable there (Codex review: a nested function
        must inherit its enclosing scope's aliases)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        return fact == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_inherited_alias_does_not_leak_into_an_unrelated_sibling(
        self,
    ) -> None:
        """The closure-inheritance fix must not widen back into the
        already-fixed sibling-leakage case: a name aliased in `f` still
        must not make an unrelated same-named parameter in an unrelated,
        non-nested sibling function `g` read as Fact-typed."""
        src = (
            "def f(rec):\n"
            "    x = rec.bases_fact\n"
            "    return x\n"
            "def g(x, y):\n"
            "    return x == y\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_comparison_through_a_closure_over_a_class_nested_method(
        self,
    ) -> None:
        """`fact = rec.bases_fact` in an outer function, then `class C:
        def method(self): return fact == other` -- Python still closes
        `method` over `fact` right through the intervening class body
        (Codex review: a class scope between the alias and its use must
        not break the closure-inheritance fix)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    class C:\n"
            "        def method(self):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 19)]

    def test_ignores_a_parameter_that_shadows_an_outer_fact_alias(self) -> None:
        """`fact = rec.bases_fact` in an outer function, then `def
        inner(fact, other): return fact == other` -- `inner`'s own `fact`
        parameter is an ordinary, unrelated local that merely reuses the
        name; Python's scoping makes it local to the whole function,
        shadowing the outer alias throughout (Codex review, fresh
        evidence: unconditionally inheriting the parent's alias set is a
        real false positive here, not a missed detection -- valid code
        must not be flagged)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(fact, other):\n"
            "        return fact == other\n"
            "    return inner(1, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_reassigned_local_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """The same shadowing rule for a plain reassignment, not just a
        parameter: `fact = rec.bases_fact` outer, then `def inner(other):
        fact = 1; return fact == other` -- `fact` is local to `inner` for
        its whole body, not just after the reassignment line."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(other):\n"
            "        fact = 1\n"
            "        return fact == other\n"
            "    return inner(other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_tuple_unpacking_target_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """The shadowing rule extends to a tuple-unpacking assignment
        target, not just a bare-name one (Codex review, fresh evidence):
        `fact = rec.bases_fact` outer, then `def inner(pair, other):
        fact, other = pair; return fact == other` -- `inner`'s own `fact`
        is bound by ordinary unpacking, still local to the whole function,
        still shadowing the outer alias throughout."""
        src = (
            "def f(rec, pair, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(pair, other):\n"
            "        fact, other = pair\n"
            "        return fact == other\n"
            "    return inner(pair, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_for_loop_target_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """A `for` loop target is a real local binding too, the same as an
        assignment target or a parameter."""
        src = (
            "def f(rec, items, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(items, other):\n"
            "        for fact in items:\n"
            "            pass\n"
            "        return fact == other\n"
            "    return inner(items, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_with_statement_target_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`with ctx() as fact:` binds `fact` locally too."""
        src = (
            "def f(rec, ctx, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(ctx, other):\n"
            "        with ctx() as fact:\n"
            "            return fact == other\n"
            "    return inner(ctx, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_except_handler_name_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`except SomeError as fact:` binds `fact` locally too, even
        though Python deletes it again at the end of the handler block."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(other):\n"
            "        try:\n"
            "            pass\n"
            "        except Exception as fact:\n"
            "            return fact == other\n"
            "        return None\n"
            "    return inner(other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_real_misuse_after_two_functions_share_a_bare_name(
        self,
    ) -> None:
        """Two independent, same-named function definitions (the shape an
        `@overload` stub and its real implementation share) must each be
        checked on their own -- a real misuse in the *second* `f` must
        still be reported (Codex review, fresh evidence: the qualname
        collision that let one `f`'s alias data leak into the other's
        must not swing the other way into silently merging away a real
        finding, either)."""
        src = (
            "def f(rec, other):\n"
            "    return rec.bases_fact == other\n"
            "def f(rec, other):\n"
            "    return rec.bases_fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert sites == [(2, 11), (4, 11)]

    def test_ignores_an_overload_stubs_annotated_parameter_in_the_real_impl(
        self,
    ) -> None:
        """`@overload`-shaped collision: a stub's `x: Fact[int]` parameter
        must not leak into a same-named real implementation's own,
        unrelated `x` local (Codex review, fresh evidence) -- distinct
        `def`s of the same name are distinct scopes, exactly as much as
        two same-named functions in different files would be."""
        src = (
            "def f(x: Fact[int], other):\n"
            "    return None\n"
            "\n"
            "def f(x, other):\n"
            "    return x == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_class_body_alias_leaking_into_the_enclosing_function(
        self,
    ) -> None:
        """A class body is its own namespace, not a local of the function
        it's nested in (Codex review, fresh evidence): `fact = rec.
        bases_fact` written directly in a class body is a class attribute,
        never visible to the enclosing function as a bare name -- an
        unrelated, later `fact == 1` in that same function (here, a real
        module-global `fact`) must not be flagged."""
        src = (
            "fact = 12345\n"
            "def outer(rec):\n"
            "    class C:\n"
            "        fact = rec.bases_fact\n"
            "    return fact == 1\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_real_misuse_within_the_same_class_body(self) -> None:
        """A genuine misuse *within* the class body itself -- `fact =
        rec.bases_fact` immediately followed by `y = fact == other`, both
        directly in the class body -- must still be caught: the class
        body is a real scope of its own, not a black hole."""
        src = (
            "def outer(rec, other):\n"
            "    class C:\n"
            "        fact = rec.bases_fact\n"
            "        y = fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 12)]

    def test_ignores_a_lambda_parameter_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` outer, then `(lambda fact: fact ==
        other)(1)` -- the lambda's own parameter shadows the outer alias,
        the same as a nested `def`'s parameter already does (Codex
        review, fresh evidence: a lambda introduced no scope of its own
        before this fix, so the shadow went unrecognized)."""
        src = "def f(rec, other):\n    fact = rec.bases_fact\n    return (lambda fact: fact == other)(1)\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_real_misuse_inside_a_lambda(self) -> None:
        """A lambda closing over a real Fact-typed value, with no
        shadowing, is exactly the same misuse as anywhere else."""
        src = "def f(rec, other):\n    return (lambda: rec.bases_fact == other)()\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 20)]

    def test_ignores_a_comprehension_target_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` outer, then `[fact == other for fact in
        values]` -- the comprehension's own `for` target shadows the
        outer alias (Codex review, fresh evidence: a comprehension
        introduced no scope of its own before this fix either)."""
        src = (
            "def f(rec, values, other):\n"
            "    fact = rec.bases_fact\n"
            "    return [fact == other for fact in values]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_real_misuse_inside_a_comprehension(self) -> None:
        """A comprehension closing over a real Fact-typed value per
        element, with no shadowing, is exactly the same misuse as
        anywhere else."""
        src = "def f(recs, other):\n    return [rec.bases_fact == other for rec in recs]\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]

    def test_resolves_expression_scopes_by_position_not_by_line(self) -> None:
        """Fresh evidence after the lambda/comprehension scope fix (Codex
        review): a lambda sharing its *line* with unrelated code must not
        swallow that other code into its own scope. `fact = rec.
        bases_fact` outer, then `(lambda fact: fact == other)(1); return
        fact == other` on one line -- the first `fact == other` (inside
        the lambda, shadowed by its own parameter) must NOT be flagged,
        but the second (the real outer alias, textually on the same line
        but not part of the lambda at all) MUST be. A line-keyed lookup
        can only pick one winner for the whole line; only a
        position-keyed one gets both halves right at once."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    (lambda fact: fact == other)(1); return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 1
        lineno, col_offset = sites[0]
        assert lineno == 3
        # The reported site is the second `fact == other` (after the
        # lambda's own closing `)(1); return `), not the first.
        line = "    (lambda fact: fact == other)(1); return fact == other"
        assert line[col_offset : col_offset + len("fact == other")] == "fact == other"
        assert col_offset > line.index(")(1)")

    def test_detects_a_comparison_through_a_chained_assignment(self) -> None:
        """`first = second = rec.bases_fact` -- a chained assignment gives
        every plain-name target the identical RHS value (Codex review,
        fresh evidence: unlike tuple-unpacking, this is not ambiguous at
        all, but the single-target restriction excluded it too)."""
        src = (
            "def f(rec, other):\n"
            "    first = second = rec.bases_fact\n"
            "    return first == other or second == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11), (3, 29)]

    def test_detects_an_inline_walrus_comparison(self) -> None:
        """`(fact := rec.bases_fact) == other` -- the assignment
        expression's own value is exactly as Fact-typed as its RHS
        (Codex review, fresh evidence)."""
        src = "def f(rec, other):\n    return (fact := rec.bases_fact) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_comparison_through_a_walrus_alias_reused_later(
        self,
    ) -> None:
        """`if (fact := rec.bases_fact) is not None: return fact ==
        other` -- the walrus binds `fact` for later use in the same
        scope, not just at the assignment expression's own site."""
        src = (
            "def f(rec, other):\n"
            "    if (fact := rec.bases_fact) is not None:\n"
            "        return fact == other\n"
            "    return False\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_detects_a_comparison_between_two_fact_annotated_parameters(
        self,
    ) -> None:
        """`def f(a: Fact[list[str]], b: Fact[bool])` then `a == b` -- a
        parameter explicitly typed `Fact[...]` is exactly as Fact-typed as
        an attribute access, with no assignment to trigger the alias
        tracking above."""
        src = "def f(a: Fact[list[str]], b: Fact[bool]) -> bool:\n    return a == b\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_comparison_through_an_aliased_fact_constructor(
        self,
    ) -> None:
        """`from abicheck.model.fact import Fact as F` then
        `F.present(a) == F.present(b)` -- the identical misuse as
        `Fact.present(a) == Fact.present(b)` (CodeRabbit: an import alias
        of `Fact` itself must not be invisible to constructor-call
        recognition)."""
        src = (
            "from abicheck.model.fact import Fact as F\n"
            "def f(a, b):\n"
            "    return F.present(a) == F.present(b)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_comparison_between_aliased_fact_annotated_parameters(
        self,
    ) -> None:
        """The same import alias applied to a `F[...]` parameter
        annotation."""
        src = (
            "from abicheck.model.fact import Fact as F\n"
            "def f(a: F[list[str]], b: F[bool]) -> bool:\n"
            "    return a == b\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_an_unaliased_import_of_an_unrelated_name(self) -> None:
        src = (
            "from somewhere import Unrelated as F\n"
            "def f(a, b):\n"
            "    return F.present(a) == F.present(b)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_alias_in_one_function_does_not_leak_into_a_sibling(self) -> None:
        """A local named `x` holding a `Fact[T]` value in `f` must not make
        an unrelated `x` in a sibling function `g` (never assigned from a
        Fact-typed expression there) read as Fact-typed too -- aliasing is
        scoped per function, not global."""
        src = (
            "def f(rec):\n"
            "    x = rec.bases_fact\n"
            "    return x\n"
            "def g(x, y):\n"
            "    return x == y\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_ordinary_variable_never_assigned_from_a_fact(self) -> None:
        src = "def f(rec):\n    x = rec.size_bits\n    return x == 64\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


def test_check_reports_a_new_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: a real Fact-equality comparison in a throwaway
    `abicheck/`-shaped tree fails the gate."""
    import scripts.fact_detector_misuse as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "a_new_misuse.py").write_text(
        "def f(rec, other):\n    return rec.bases_fact == other.bases_fact\n"
    )

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)

    findings = Findings()
    check_fact_detector_misuse(findings)
    errors = [m for c, m in findings.errors if c == "fact-detector-misuse"]
    assert len(errors) == 1
    assert "a_new_misuse.py:2" in errors[0]


def test_check_is_silent_for_clean_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: ordinary `.status`-based unwrapping raises no
    finding — this check must not fire on the *correct* usage pattern."""
    import scripts.fact_detector_misuse as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "clean.py").write_text(
        "def f(rec):\n"
        "    if rec.bases_fact.is_present:\n"
        "        return rec.bases_fact.value\n"
        "    return None\n"
    )

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "PKG", pkg)

    findings = Findings()
    check_fact_detector_misuse(findings)
    errors = [m for c, m in findings.errors if c == "fact-detector-misuse"]
    assert errors == []
