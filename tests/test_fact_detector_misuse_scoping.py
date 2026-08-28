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

"""Scope-resolution tests for the ``fact-detector-misuse`` AI-readiness
check (``scripts/fact_detector_misuse.py``, registered by
``scripts/check_ai_readiness.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split out of ``test_fact_detector_misuse.py`` once that file grew past the
architecture gate's 1200-line test-file cap (the same split
``test_mutation_run_scoping.py`` made for an identical reason -- see that
module's own docstring in ``tests/CLAUDE.md``'s test-quality-guards
section). This file covers every shadowing/scope-attribution finding from
``TestFactEqualityMisuseSites``: parameter/local/comprehension/lambda/
walrus/match-capture shadowing, closure inheritance through nested
functions and class bodies, `nonlocal`/`global` read- and write-side
routing, parameter-default/annotation scope resolution, and import-based
shadowing. The sibling file keeps the core misuse-detection contract
(direct attribute reads, `getattr`/constructor-call recognition, alias
chains, annotated assignments) plus the two end-to-end `check_fact_
detector_misuse()` tests.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestFactEqualityMisuseSitesScoping:
    """Scope-resolution tests split out of `TestFactEqualityMisuseSites`
    (see this module's own docstring) -- pins the same detection logic's
    scoping contract."""

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

    def test_ignores_a_match_capture_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`case fact:` (a bare `ast.MatchAs` capture) binds `fact`
        locally too, the same as any other capture form (Codex review,
        fresh evidence)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(x, other):\n"
            "        match x:\n"
            "            case fact:\n"
            "                return fact == other\n"
            "    return inner(rec, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_match_star_capture_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`case [*fact]:` (`ast.MatchStar`) binds `fact` locally too."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(items, other):\n"
            "        match items:\n"
            "            case [*fact]:\n"
            "                return fact == other\n"
            "    return inner(rec, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_match_mapping_rest_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`case {**fact}:` (`ast.MatchMapping`'s own `rest`) binds
        `fact` locally too."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(mapping, other):\n"
            "        match mapping:\n"
            "            case {**fact}:\n"
            "                return fact == other\n"
            "    return inner(rec, other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_real_misuse_through_a_match_capture(self) -> None:
        """A genuine misuse reached through a match capture, with no
        shadowing, is exactly the same misuse as anywhere else."""
        src = (
            "def f(rec, other):\n"
            "    match rec:\n"
            "        case fact:\n"
            "            return fact.bases_fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

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

    def test_ignores_a_walrus_target_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` outer, then `def inner(other): (fact
        := 1); return fact == other` -- a walrus outside a comprehension
        is an ordinary local binding, just like a plain assignment (Codex
        review, fresh evidence): an earlier revision of the walrus fix
        exempted every walrus target from `locally_bound` on the theory
        that PEP 572's scope-hopping rule always applies, but that rule
        only fires *inside* a comprehension -- this ordinary, unrelated
        rebinding must still shadow the outer alias."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(other):\n"
            "        (fact := 1)\n"
            "        return fact == other\n"
            "    return inner(other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_walrus_that_hops_out_of_a_comprehension(self) -> None:
        """A walrus used *directly inside* a comprehension is the one
        real PEP 572 exception: it binds into the nearest *enclosing*
        non-comprehension scope, not the comprehension's own -- so a
        later use outside the comprehension entirely must still resolve
        it."""
        src = (
            "def f(recs, other):\n"
            "    [y for y in recs if (fact := y.bases_fact)]\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_parameter_default_referencing_an_outer_fact_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact` outer, then `def inner(fact=fact):
        return fact == other` -- the default is evaluated in the
        enclosing scope, so calling `inner()` with no override genuinely
        compares the outer Fact value, even though the parameter's own
        name is otherwise excluded from the inherited alias set (Codex
        review, fresh evidence)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(fact=fact):\n"
            "        return fact == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_detects_a_directly_fact_typed_parameter_default(self) -> None:
        """`def inner(fact=rec.bases_fact): return fact == other` -- the
        default itself is directly recognizable as Fact-typed, no alias
        chain needed."""
        src = (
            "def f(rec, other):\n"
            "    def inner(fact=rec.bases_fact):\n"
            "        return fact == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_ignores_an_ordinary_parameter_default(self) -> None:
        """A negative control: a parameter default that isn't Fact-typed
        at all must not be flagged just because *some* default exists."""
        src = (
            "def f(rec, other):\n"
            "    def inner(fact=1):\n"
            "        return fact == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_walrus_inside_a_parameter_default_binding_outward(
        self,
    ) -> None:
        """`def inner(x=(fact := rec.bases_fact)): ...` -- a walrus inside
        a parameter's own default expression binds in the *enclosing*
        scope, since a default is evaluated there, not inside `inner`'s
        own body (Codex review, fresh evidence): a later `fact == other`
        in the outer function must still resolve it."""
        src = (
            "def f(rec, other):\n"
            "    def inner(x=(fact := rec.bases_fact)):\n"
            "        return x\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 11)]

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

    def test_detects_a_real_misuse_despite_a_nonlocal_redeclaration(self) -> None:
        """A `nonlocal`-declared name is never a real local rebinding -- it
        refers to the *same* outer variable, so it must not be excluded
        from the outer scope's alias inheritance the way an ordinary local
        reassignment correctly is (Codex review: the shadowing subtraction
        previously treated `nonlocal fact` identically to `fact = ...`,
        silently hiding the outer alias from the read that precedes the
        reassignment)."""
        src = (
            "def outer(rec):\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        nonlocal fact\n"
            "        hit = fact == other\n"
            "        fact = 1\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 14)]

    def test_detects_a_real_misuse_despite_a_global_redeclaration(self) -> None:
        """The same exemption for `global` -- a module-level alias must
        still be visible to a function that later reassigns the same name
        under an explicit `global` declaration."""
        src = (
            "fact_global = registry.bases_fact\n"
            "def use():\n"
            "    global fact_global\n"
            "    hit = fact_global == other\n"
            "    fact_global = 1\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 10)]

    def test_ignores_a_local_reassignment_without_nonlocal_still_shadows(
        self,
    ) -> None:
        """Negative control for the two tests above: an *ordinary* local
        reassignment (no `nonlocal`/`global` declaration) must still shadow
        the outer alias exactly as before -- the exemption is specific to a
        declared nonlocal/global name, not a general loosening of the
        shadowing rule."""
        src = (
            "def outer(rec):\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        fact = 1\n"
            "        hit = fact == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_default_derived_alias_propagates_into_a_nested_function(
        self,
    ) -> None:
        """A parameter default derived from a Fact-typed expression makes
        the parameter itself a same-function alias -- and that alias must
        reach a function nested *inside* the one declaring the default, not
        just the declaring function's own body (Codex review: the old
        single top-to-bottom resolution pass processed a nested function's
        inheritance before its enclosing function's own default-derived
        alias had been resolved, silently missing the propagation)."""
        src = (
            "def inner(rec, fact=rec.bases_fact):\n"
            "    def nested():\n"
            "        return fact == other\n"
            "    return nested\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_class_body_inherits_a_fact_alias_from_its_enclosing_function(
        self,
    ) -> None:
        """A class body's own top-level code inherits from its lexically
        enclosing scope the same way ordinary code does -- unlike a method
        defined inside the class, which does *not* see the class body as
        its own parent scope for closure purposes (Codex review: the class
        layer was skipped entirely for alias inheritance, not just for the
        method-closure case it's correct to skip for)."""
        src = (
            "def outer(rec):\n"
            "    fact = rec.bases_fact\n"
            "    class C:\n"
            "        result = fact == other\n"
            "    return C\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 17)]

    def test_ignores_a_class_body_comparison_with_no_enclosing_fact_alias(
        self,
    ) -> None:
        """Negative control: a class body at module scope, where nothing
        named `fact` is ever Fact-typed, must not report a finding merely
        because class-body inheritance now exists."""
        src = "fact = 1\nclass C:\n    result = fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_method_still_does_not_inherit_from_its_own_class_body(
        self,
    ) -> None:
        """A method must keep skipping its class's own body as a parent
        scope (ordinary Python LEGB), even though the class body itself now
        inherits from its enclosing function -- a class-body-local
        reassignment of `fact` must not leak into a method's own alias
        resolution, which instead sees straight through to the enclosing
        function's real alias."""
        src = (
            "def outer(rec):\n"
            "    fact = rec.bases_fact\n"
            "    class C:\n"
            "        fact = 5\n"
            "        def method(self):\n"
            "            return fact == other\n"
            "    return C\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 19)]

    def test_detects_a_class_body_read_before_a_later_reassignment(
        self,
    ) -> None:
        """A class body's own top-level statements use `LOAD_NAME`, not
        `LOAD_FAST` -- a read that occurs *before* a later local
        reassignment still resolves to the outer alias in real Python, even
        though the exact same reassignment would make the name local for
        the *whole* scope in an ordinary function (Codex review: `hit =
        fact == other` genuinely sees the outer alias here, since the class
        namespace has nothing under `fact` yet at that point)."""
        src = "fact = rec.bases_fact\nclass C:\n    hit = fact == other\n    fact = 1\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 10)]

    def test_ignores_a_nested_def_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """`def fact(): ...` binds the name `fact` in its *containing*
        scope to the function object just defined -- an ordinary local,
        not the outer Fact alias (Codex review: only the nested function's
        own new scope was previously recorded, never its name in the scope
        that contains it)."""
        src = (
            "fact = rec.bases_fact\n"
            "def outer():\n"
            "    def fact():\n"
            "        pass\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_nested_class_that_shadows_an_outer_fact_alias(
        self,
    ) -> None:
        """The identical shadowing for a nested `class fact: ...` statement,
        not just a nested `def`."""
        src = (
            "fact = rec.bases_fact\n"
            "def outer():\n"
            "    class fact:\n"
            "        pass\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_real_misuse_is_still_caught_with_no_nested_definition(
        self,
    ) -> None:
        """Negative control for the two tests above: with no nested `def`/
        `class` shadowing `fact`, the outer alias must still be visible and
        a real misuse still reported."""
        src = "fact = rec.bases_fact\ndef outer():\n    return fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_global_read_through_an_unrelated_intervening_local(
        self,
    ) -> None:
        """`global fact` inside a nested function must resolve directly
        against *module*-scope `fact`, bypassing every intervening
        function's own inheritance entirely -- even one with its own
        unrelated, non-Fact-typed local of the identical bare name (Codex
        review: routing `global` through ordinary `lexical_parents`
        inheritance missed this case, since `middle`'s own plain `fact = 5`
        local has nothing to do with `inner`'s `global fact`)."""
        src = (
            "fact = rec.bases_fact\n"
            "def middle():\n"
            "    fact = 5\n"
            "    def inner():\n"
            "        global fact\n"
            "        return fact == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 15)]

    def test_ignores_an_intervening_functions_own_fact_alias_for_a_global_read(
        self,
    ) -> None:
        """Negative control, the reversed values: an intervening function's
        own genuinely Fact-typed local `fact` must not be wrongly
        attributed to an unrelated, non-Fact module-level `fact` just
        because a nested function declares `global fact`."""
        src = (
            "fact = 5\n"
            "def middle():\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        global fact\n"
            "        return fact == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_comprehension_walrus_shadows_an_outer_alias_in_its_target_scope(
        self,
    ) -> None:
        """`[(fact := x) for x in values]` directly inside `inner` binds
        `fact` as an ordinary local *of `inner`* under PEP 572 -- shadowing
        an outer `fact` alias for a later, real `fact == other` read in
        `inner` (Codex review: the scope-hop fix marked `locally_bound`
        only when NO hop occurred, silently skipping the mark whenever a
        real hop happened, even though it's the hopped-to scope that
        actually needs it)."""
        src = (
            "def outer():\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        result = [(fact := x) for x in values]\n"
            "        return fact == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_real_misuse_is_still_caught_with_no_comprehension_walrus(
        self,
    ) -> None:
        """Negative control for the test above: with no comprehension
        walrus shadowing `fact`, the outer alias must still be visible and
        a real misuse still reported."""
        src = (
            "def outer():\n"
            "    fact = rec.bases_fact\n"
            "    def inner():\n"
            "        return fact == other\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_a_global_declared_assignment_is_visible_to_an_ordinary_sibling_read(
        self,
    ) -> None:
        """A function that declares `global fact` and assigns `fact = rec.
        bases_fact` genuinely writes *module*-scope `fact`, visible to any
        other function reading the module-level name through ordinary
        inheritance -- not just to the declaring function itself (Codex
        review: the read-side global routing fix was symmetric only for
        reads, not writes -- the assignment's own candidate stayed
        attached to the writer's own qualname, so a sibling function never
        saw it)."""
        src = (
            "def seed(rec):\n"
            "    global fact\n"
            "    fact = rec.bases_fact\n"
            "def use(other):\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 11)]

    def test_ignores_an_ordinary_local_assignment_with_no_global_declaration(
        self,
    ) -> None:
        """Negative control: without a `global` declaration, an ordinary
        local `fact = rec.bases_fact` must stay attached to its own
        function -- it must not leak into the module's own alias set and
        be wrongly attributed to an unrelated sibling `fact`."""
        src = (
            "def seed(rec):\n"
            "    fact = rec.bases_fact\n"
            "def use(other):\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_nonlocal_declared_assignment_is_visible_to_a_sibling_reader(
        self,
    ) -> None:
        """The identical write-side routing for `nonlocal`: a setter
        function's `nonlocal fact; fact = rec.bases_fact` writes the
        enclosing function's own `fact`, visible to a sibling function
        (also nested in that same enclosing function) reading it through
        ordinary inheritance."""
        src = (
            "def outer():\n"
            "    fact = None\n"
            "    def setter(rec):\n"
            "        nonlocal fact\n"
            "        fact = rec.bases_fact\n"
            "    def reader(other):\n"
            "        return fact == other\n"
            "    return setter, reader\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(7, 15)]

    def test_a_global_write_through_a_same_scope_local_is_visible_to_a_sibling(
        self,
    ) -> None:
        """A `global fact; local = rec.bases_fact; fact = local` write
        genuinely writes module-scope `fact` -- but the candidate's own
        value (`local`) is only ever meaningful within the *writer's own*
        scope, not the target scope it moves to (Codex review: a first
        revision of the write-side routing fix moved the raw `(name,
        value)` candidate straight into the target scope's own candidate
        list, where `local` could never resolve, since it's local only to
        `seed`). Fixed by resolving each declared assignment against the
        writer's own scope first, then propagating only the *confirmed*
        alias to the target scope."""
        src = (
            "def seed(rec):\n"
            "    global fact\n"
            "    local = rec.bases_fact\n"
            "    fact = local\n"
            "def use(other):\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 11)]

    def test_a_nonlocal_write_through_a_same_scope_local_is_visible_to_a_sibling(
        self,
    ) -> None:
        """The identical RHS-indirection fix for `nonlocal`."""
        src = (
            "def outer():\n"
            "    fact = None\n"
            "    def setter(rec):\n"
            "        nonlocal fact\n"
            "        local = rec.bases_fact\n"
            "        fact = local\n"
            "    def reader(other):\n"
            "        return fact == other\n"
            "    return setter, reader\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(8, 15)]

    def test_detects_a_comparison_inside_a_parameter_default_against_the_enclosing_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact; def inner(fact=(fact == other)): ...` --
        Python evaluates a default at `def`-time, in the *enclosing*
        scope, but this function's own site-to-qualname resolution is
        purely position-based, and the comparison's own position is
        textually inside `inner`'s span -- so it was wrongly resolved
        against `inner`'s own alias set, where `inner`'s own parameter
        `fact` has already removed the inherited alias (Codex review)."""
        src = (
            "fact = rec.bases_fact\ndef inner(fact=(fact == other)):\n    return fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 16)]

    def test_ignores_a_default_comparison_against_an_unrelated_non_fact_value(
        self,
    ) -> None:
        """Negative control: a default comparison that has nothing to do
        with a Fact alias must not be flagged."""
        src = "x = 5\ndef inner(y=(x == 1)):\n    return y\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_nonlocal_skips_a_non_binding_intervening_function(self) -> None:
        """`nonlocal` can skip *multiple* enclosing functions, not just the
        immediate lexical parent -- Python resolves it to the nearest
        enclosing function that actually binds the name itself (Codex
        review): `outer` binds `fact`, `middle` (nested in `outer`) never
        touches it at all, `setter` (nested in `middle`) does `nonlocal
        fact; fact = rec.bases_fact` -- this genuinely writes `outer`'s
        `fact`, skipping `middle` entirely. The previous, immediate-
        parent-only routing published the write to `middle` instead,
        where `reader` (also nested in `middle`) never actually sees it
        the same way through ordinary inheritance -- so this only
        reproduces the bug through `reader`'s own read, not `setter`'s
        own qualname."""
        src = (
            "def outer():\n"
            "    fact = None\n"
            "    def middle():\n"
            "        def setter(rec):\n"
            "            nonlocal fact\n"
            "            fact = rec.bases_fact\n"
            "        def reader(other):\n"
            "            return fact == other\n"
            "        return setter, reader\n"
            "    return middle\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(8, 19)]

    def test_ignores_an_alias_shadowed_by_an_import_as(self) -> None:
        """`import json as fact` is a real local binding, the identical
        shadowing shape as any other assignment form (Codex review: the
        binding collector had no branch for either import statement at
        all)."""
        src = "fact = rec.bases_fact\ndef inner():\n    import json as fact\n    return fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_alias_shadowed_by_a_from_import_as(self) -> None:
        """The identical shadowing for `from pkg import item as fact`."""
        src = (
            "fact = rec.bases_fact\n"
            "def inner():\n"
            "    from pkg import item as fact\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_real_misuse_is_still_caught_with_no_import_shadow(self) -> None:
        """Negative control for the two tests above: with no import
        shadowing `fact`, the outer alias must still be visible and a
        real misuse still reported."""
        src = "fact = rec.bases_fact\ndef inner():\n    return fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_a_comparison_inside_a_nested_lambda_default(self) -> None:
        """`fact = rec.bases_fact; def g(cb=lambda fact: fact == other):
        ...` -- the lambda's own parameter `fact` genuinely shadows the
        outer alias *inside the lambda's own body*, so force-attributing
        the whole default expression (lambda body included) to the
        enclosing scope must not override that nested scope's own,
        already-correct resolution (Codex review, fresh evidence)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(cb=lambda fact: fact == other):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_real_misuse_despite_a_nested_lambda_default(self) -> None:
        """Negative control: a default expression with no lambda of its
        own must still be attributed to the enclosing scope exactly as
        before."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(x=(fact == other)):\n"
            "        return x\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 13)]

    def test_detects_a_comparison_inside_a_return_annotation(self) -> None:
        """`fact = rec.bases_fact; def inner(fact) -> (fact == other): ...`
        -- a return annotation is evaluated in the defining (enclosing)
        scope exactly like a parameter default, but `node.returns` was
        never included in the scope-override traversal (Codex review,
        fresh evidence): `inner`'s own parameter `fact` shadows the outer
        alias for the function body, but the return annotation is
        evaluated before that parameter even exists."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def inner(fact) -> (fact == other):\n"
            "        return None\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 24)]

    def test_a_method_default_inherits_its_containing_class_bodys_alias(
        self,
    ) -> None:
        """`class C: fact = rec.bases_fact; def m(self, value=fact):
        return value == other` -- a method's own default is evaluated
        while its *containing class body* executes, ordinary class-body
        code, not a closure lookup through `_lexical_function_parents`
        (which intentionally skips the class layer for a method *body*'s
        own free-variable lookup, a different question) (Codex review,
        fresh evidence)."""
        src = (
            "def f(rec, other):\n"
            "    class C:\n"
            "        fact = rec.bases_fact\n"
            "        def m(self, value=fact):\n"
            "            return value == other\n"
            "    return C\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 19)]

    def test_a_methods_own_parameter_still_shadows_in_its_body(self) -> None:
        """Negative control: when the method's own parameter genuinely
        shadows the class-body alias (`def m(self, fact): return fact ==
        other`, no default at all), the class-body-alias fix above must
        not widen back into a false positive for the method's own body."""
        src = (
            "def f(rec, other):\n"
            "    class C:\n"
            "        fact = rec.bases_fact\n"
            "        def m(self, fact):\n"
            "            return fact == other\n"
            "    return C\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_walrus_inside_a_nested_default_lambda(self) -> None:
        """`fact = 1; def configure(cb=lambda: (fact := rec.bases_fact)):
        ...` -- the lambda is only *created* at def-time in the enclosing
        scope; the walrus inside its body binds `fact` in the *lambda's
        own* scope when it is later called, never the enclosing one. An
        unrestricted walk of the default expression wrongly published the
        lambda-local walrus target as an alias of the enclosing (here,
        module) scope, making a later, genuinely unrelated `fact ==
        other` read as a misuse (Codex review, fresh evidence)."""
        src = (
            "fact = 1\n"
            "def configure(cb=lambda: (fact := rec.bases_fact)):\n"
            "    return cb\n"
            "print(fact == other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_walrus_directly_in_a_default_with_no_nested_scope(
        self,
    ) -> None:
        """Negative control: a walrus directly inside a default (no
        nested lambda/comprehension in the way) must still be published
        to the enclosing scope exactly as before."""
        src = (
            "fact = 1\n"
            "def inner(x=(fact := rec.bases_fact)):\n"
            "    return x\n"
            "print(fact == other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 6)]

    def test_a_lambdas_own_default_inherits_its_containing_scopes_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact; cb = lambda x=fact: x == other` -- a
        lambda has no *name* to bind (unlike a `def`/`class` statement),
        but its own default value still evaluates at lambda-creation time
        in whatever scope directly contains it. `_def_containing_
        qualnames` previously recorded a containing scope for a `def`/
        `class` but never for a `Lambda`, so both the pending-default
        alias resolution and the comparison-scope override silently fell
        back to `<module>` regardless of the lambda's real containing
        scope (Codex review, fresh evidence)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    cb = lambda x=fact: x == other\n"
            "    return cb\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 24)]

    def test_a_nested_class_header_evaluates_in_the_containing_class_scope(
        self,
    ) -> None:
        """`class Outer: fact = rec.bases_fact; class Inner(make_base(fact
        == other)): ...` -- a nested class's own base/keyword expressions
        execute while the `class Inner` statement itself runs, in
        `Outer`'s own class-body scope, not inside `Inner`'s own (not yet
        even created) body scope. `_enclosing_qualnames` assigns the
        entire `ClassDef` span -- bases and keywords included -- to the
        inner class-body scope, which is right for the body's own
        statements but wrong for the header that precedes them (Codex
        review, fresh evidence)."""
        src = (
            "def f(rec, other):\n"
            "    class Outer:\n"
            "        fact = rec.bases_fact\n"
            "        class Inner(make_base(fact == other)):\n"
            "            pass\n"
            "    return Outer\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 30)]

    def test_a_method_default_walrus_binds_in_the_class_namespace(
        self,
    ) -> None:
        """`fact = 1; class C: def f(self, x=(fact := rec.bases_fact)):
        ...` -- the walrus inside a *method's* own default executes while
        the containing class body runs, so it must bind `C`'s own
        class-body `fact`, not the module-level `fact` -- `lexical_
        parents` deliberately skips the class layer (it answers the
        method *body*'s own free-variable lookup, a different question),
        so this must use the same `def_containing` primitive the sibling
        `pending_defaults` fix already uses (Codex review, fresh
        evidence)."""
        src = (
            "fact = 1\n"
            "class C:\n"
            "    def f(self, x=(fact := rec.bases_fact)):\n"
            "        return x\n"
            "print(fact == other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_a_method_default_walrus_is_visible_within_the_same_class_body(
        self,
    ) -> None:
        """Positive control for the test above: the class-body `fact`
        the method-default walrus actually binds must still be visible to
        an ordinary read elsewhere in that same class body."""
        src = (
            "class C:\n"
            "    fact = 1\n"
            "    def f(self, x=(fact := rec.bases_fact)):\n"
            "        return x\n"
            "    result = fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 13)]
