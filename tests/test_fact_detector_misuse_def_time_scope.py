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


class TestComprehensionOutermostIterableParentScope:
    """A comprehension's own *outermost* generator's iterable evaluates in
    the enclosing scope, before the comprehension's implicit function is
    even called -- a real Python semantic exception to "the comprehension
    introduces its own scope," which `_enclosing_qualnames`/`_lexical_
    function_parents`/`_def_containing_qualnames` all now account for
    (Codex review, fresh evidence)."""

    def test_detects_a_comparison_in_the_outermost_iterable_shadowed_by_its_own_target(
        self,
    ) -> None:
        """`fact = rec.bases_fact`, then `[x for fact in (fact == other,)
        for x in fact]` -- the comparison sits inside the *outermost*
        iterable, which evaluates before the comprehension's own `fact`
        target even exists to shadow anything, so it still reads the
        outer alias."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return [x for fact in (fact == other,) for x in fact]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 27)]

    def test_ignores_a_comparison_in_a_non_outermost_iterable_genuinely_shadowed(
        self,
    ) -> None:
        """Negative control: only the *first* generator's iterable is
        special -- a *second* generator's own iterable genuinely runs
        inside the comprehension's own scope, after the first target has
        already bound, so a real shadow there is still correctly
        excluded."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return [y for fact in range(3) for y in (fact == other,)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_lambda_closing_over_the_outer_alias_from_the_outermost_iterable(
        self,
    ) -> None:
        """The fix must correctly resolve a real *closure* found in the
        outermost iterable too, not just a bare comparison -- `_lexical_
        function_parents`'s own dispatch is exercised here, not only
        `_enclosing_qualnames`'s span override."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return [x for fact in [(lambda: fact == other)()] for x in fact]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 36)]


class TestClassHeaderContainingScope:
    """A class's own base classes, keyword arguments (e.g. a metaclass),
    and decorators all evaluate while the `class` statement itself
    executes -- in whatever scope directly, syntactically contains that
    statement -- never inside the new class's own body (Codex review,
    fresh evidence, beyond the direct-base-expression case an earlier
    round already fixed)."""

    def test_detects_a_comparison_inside_a_lambda_default_in_a_base_expression(
        self,
    ) -> None:
        """`class Inner((lambda x=fact: make_base(x == other))()): ...`
        -- the lambda default `x=fact` evaluates in `Outer`'s own
        namespace, not `Inner`'s, so `x` must resolve as the outer
        `fact` alias despite `Inner` never itself declaring a
        conflicting name."""
        src = (
            "def make_base(v):\n"
            "    return object\n"
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    class Outer:\n"
            "        class Inner((lambda x=fact: make_base(x == other))()):\n"
            "            pass\n"
            "    return Outer\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 46)]

    def test_detects_a_comparison_in_a_metaclass_keyword(self) -> None:
        """The identical containing-scope rule for a class keyword
        argument (e.g. `metaclass=`), not just a positional base."""
        src = (
            "def make_meta(v):\n"
            "    return type\n"
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    class Outer:\n"
            "        class Inner(metaclass=make_meta(fact == other)):\n"
            "            pass\n"
            "    return Outer\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 40)]

    def test_ignores_a_comparison_genuinely_inside_the_class_body(self) -> None:
        """Negative control: a comparison genuinely inside the class's
        own *body* (not its header) is still correctly attributed to the
        class body's own scope -- this fix must not widen resolution
        past the header it's actually about. Shadowed via a nested
        method's own parameter (a mechanism already known to work),
        rather than a class-body reassignment (a separate, pre-existing,
        unrelated gap -- class-body-level reassignment shadowing is not
        tracked by this module at all, confirmed unaffected by this
        fix)."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    class Outer:\n"
            "        class Inner:\n"
            "            def method(fact, other):\n"
            "                return fact == other\n"
            "    return Outer\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


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


class TestComprehensionOutermostIterableIsDispatchedExactlyOnce:
    """`_enclosing_qualnames`/`_lexical_function_parents`/`_def_containing_
    qualnames` each dispatch a comprehension's outermost generator's
    iterable exactly once, under the enclosing scope -- a first version of
    this fix still finished with a blanket re-walk of the whole
    comprehension, which silently re-registered any closure found in the
    outermost iterable a second time, under the comprehension's own
    (wrong) scope (Codex review, fresh evidence)."""

    def test_detects_a_lambda_default_in_the_outermost_iterable(self) -> None:
        """`[x for fact in (lambda y=fact: (y == other,))() for x in
        fact]` -- the lambda's own default `y=fact` evaluates in `f`'s
        own scope, before the comprehension's `fact` target exists to
        shadow it, but a blanket re-walk after the correct dispatch used
        to re-register the lambda under the comprehension's own scope
        instead, silently missing the misuse."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return [x for fact in (lambda y=fact: (y == other,))() "
            "for x in fact]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 43)]

    def test_ignores_a_comparison_in_a_dict_comprehensions_value_shadowed_by_key(
        self,
    ) -> None:
        """A `DictComp`'s own `key`/`value` split is covered by the same
        explicit-field dispatch as `elt` -- a genuine shadow inside the
        `value` expression (not the outermost iterable) is still
        correctly excluded."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return {fact: (fact == other) for fact in range(3)}\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestElementwiseTupleUnpackingAliases:
    """A simple tuple/list-unpacking assignment whose RHS is itself a
    literal `Tuple`/`List` display of the identical length has a real,
    identifiable per-element value -- `old_fact, new_fact = old.
    bases_fact, new.bases_fact` is an ordinary detector refactor of two
    independent Fact-typed values, ordinary code the check must still
    catch (Codex review, fresh evidence)."""

    def test_detects_a_comparison_through_paired_tuple_unpacking(self) -> None:
        src = (
            "def f(old, new):\n"
            "    old_fact, new_fact = old.bases_fact, new.bases_fact\n"
            "    return old_fact == new_fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_comparison_through_paired_list_unpacking(self) -> None:
        """The identical pairing for a `List` display on both sides, not
        just `Tuple`."""
        src = (
            "def f(old, new):\n"
            "    [old_fact, new_fact] = [old.bases_fact, new.bases_fact]\n"
            "    return old_fact == new_fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_comparison_through_nested_paired_unpacking(self) -> None:
        """Pairing recurses through a further nested tuple target, the
        same way `_bound_names()` already does."""
        src = (
            "def f(old, new):\n"
            "    (old_fact, (new_fact, x)) = (old.bases_fact, (new.bases_fact, 1))\n"
            "    return old_fact == new_fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_an_opaque_single_valued_tuple_unpacking(self) -> None:
        """Negative control: `a, b = pair` has no per-element RHS sub-
        expression to pair against -- `pair` is one opaque value, so `a`
        must not be attributed a Fact-typed value it was never shown to
        hold, the same distinction the module's own pre-existing
        docstring already draws."""
        src = "def f(pair, other):\n    a, b = pair\n    return a == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_the_starred_capture_itself_but_still_pairs_fixed_positions(
        self,
    ) -> None:
        """A starred target (`*rest`) captures an arbitrary-length slice
        with no single corresponding RHS sub-expression, so no candidate
        is derived for `rest` itself -- confirmed here by a comparison
        involving `rest` staying unflagged. `old_fact`, the *fixed*-
        position element before the star, still pairs against its own
        real `old.bases_fact` sub-expression the identical way a starless
        unpacking already does (Codex review, fresh evidence: the
        previous blanket "any Starred anywhere disqualifies the whole
        pairing" rule wrongly discarded this fixed-position pairing too,
        which is what made `old_fact == rest` read as *not* spurious --
        it wrongly looked that way only because `old_fact` wasn't yet
        recognized as Fact-typed either; now that it correctly is, the
        comparison is a genuine finding, not a false positive)."""
        src = (
            "def f(old, new, extra, other):\n"
            "    old_fact, *rest = old.bases_fact, new.bases_fact, extra\n"
            "    unrelated = rest == other\n"
            "    return old_fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert sites == [(4, 11)]


class TestClassHeaderWalrusContainingScope:
    """A walrus inside a class base or metaclass keyword expression is
    the *binding*-side sibling of ``TestClassHeaderContainingScope``
    above: it binds its target in the scope containing the `class`
    statement, not the new class's own body (Codex review, fresh
    evidence). The read-side fix already covered a comparison found
    inside a base/keyword expression; this covers a walrus found there
    that later needs to be *usable* as an alias outside the class."""

    def test_detects_a_comparison_through_a_walrus_in_a_class_base(self) -> None:
        """`class C(make_base(fact := rec.vtable_fact)): ...` -- `fact`
        binds while the `class C(...)` header executes, in whatever
        scope directly contains it, so a later, genuinely outer `fact ==
        other` must resolve it."""
        src = (
            "def make_base(v):\n"
            "    return object\n"
            "def f(rec, other):\n"
            "    class C(make_base(fact := rec.vtable_fact)):\n"
            "        pass\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 11)]

    def test_detects_a_comparison_through_a_walrus_in_a_metaclass_keyword(
        self,
    ) -> None:
        """The identical containing-scope rule for a walrus inside a
        keyword argument (e.g. `metaclass=`), not just a positional
        base."""
        src = (
            "def make_meta(v):\n"
            "    return type\n"
            "def f(rec, other):\n"
            "    class C(metaclass=make_meta(fact := rec.vtable_fact)):\n"
            "        pass\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(6, 11)]

    def test_ignores_a_walrus_shadowed_by_a_real_outer_parameter(self) -> None:
        """Negative control: an outer parameter of the same name
        genuinely shadows the class-header walrus's target, so no
        finding should fire."""
        src = (
            "def make_base(v):\n"
            "    return object\n"
            "def outer(fact):\n"
            "    def f(rec, other):\n"
            "        class C(make_base(fact := rec.vtable_fact)):\n"
            "            pass\n"
            "        return fact == other\n"
            "    return f\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(7, 15)]


class TestForLoopLiteralCollectionAliases:
    """A single `for`/comprehension loop target bound, one iteration at a
    time, to every element of a literal `Tuple`/`List` display of
    definitively Fact-typed values is a real alias for the loop body
    (Codex review, fresh evidence) -- the loop-target counterpart of
    `_paired_unpacking_candidates`'s assignment-side handling."""

    def test_detects_a_comparison_through_a_for_loop_over_a_fact_tuple(
        self,
    ) -> None:
        src = (
            "def f(old, new, other):\n"
            "    for fact in (old.bases_fact, new.bases_fact):\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_detects_a_comparison_through_a_for_loop_over_a_fact_list(self) -> None:
        src = (
            "def f(old, new, other):\n"
            "    for fact in [old.bases_fact, new.bases_fact]:\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_detects_a_comparison_through_a_comprehension_over_a_fact_tuple(
        self,
    ) -> None:
        src = (
            "def f(old, new, other):\n"
            "    return [fact == other for fact in (old.bases_fact, new.bases_fact)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]

    def test_ignores_a_for_loop_over_a_partially_fact_typed_tuple(self) -> None:
        """Negative control: only *some* elements are Fact-typed, so the
        loop target isn't reliably a Fact on every iteration -- must not
        be treated as an alias."""
        src = (
            "def f(old, other):\n"
            "    def some_call():\n"
            "        return 1\n"
            "    for x in (old.bases_fact, some_call()):\n"
            "        return x == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_for_loop_over_an_opaque_iterable(self) -> None:
        """Negative control: an ordinary opaque iterable (a bare `Name`,
        not a literal display) has no per-element evidence at all, so
        this must not spuriously flag it."""
        src = "def f(pairs, other):\n    for x in pairs:\n        return x == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_sibling_functions_unrelated_comparison(self) -> None:
        """Negative control: the alias is genuinely scoped to the
        function whose loop actually established it -- an unrelated
        sibling function's own, unrelated `fact == other` (a plain
        parameter, never bound to a Fact anywhere in that function) must
        not be flagged just because *some* function in the module has a
        Fact-typed loop target of the same name."""
        src = (
            "def f(old, new):\n"
            "    for fact in (old.bases_fact, new.bases_fact):\n"
            "        pass\n"
            "    return fact\n"
            "def g(fact, other):\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestForLoopLiteralCollectionResolvesThroughExistingAliases:
    """A literal `Tuple`/`List` element that is itself a bare `Name`
    already known to be a Fact alias is a real Fact-typed element too --
    `_is_fact_typed_expr()` deliberately never resolves a bare name (that
    needs the whole-tree alias fixed point, which doesn't exist yet
    during collection), so this resolves through the same fixed point
    `tuple_loop_candidates` participates in, not at collection time."""

    def test_detects_a_for_loop_over_a_tuple_of_an_existing_alias(self) -> None:
        src = (
            "def f(old, other):\n"
            "    old_fact = old.bases_fact\n"
            "    for fact in (old_fact,):\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_detects_a_comprehension_over_a_tuple_of_an_existing_alias(self) -> None:
        src = (
            "def f(old, other):\n"
            "    old_fact = old.bases_fact\n"
            "    return [fact == other for fact in (old_fact,)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 12)]

    def test_detects_a_for_loop_mixing_an_alias_and_a_direct_fact_read(self) -> None:
        """A tuple mixing a resolved alias element with an ordinary
        directly Fact-typed element -- both must individually satisfy
        the conjunctive check."""
        src = (
            "def f(old, new, other):\n"
            "    old_fact = old.bases_fact\n"
            "    for fact in (old_fact, new.bases_fact):\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 15)]

    def test_ignores_a_for_loop_over_a_name_that_never_resolves(self) -> None:
        """Negative control: a bare-`Name` element is only *eligible*
        for deferred resolution -- if it never actually resolves to a
        known Fact alias, the loop target must stay unflagged, the same
        as before this fix."""
        src = (
            "def f(other, unrelated):\n"
            "    for x in (unrelated,):\n"
            "        return x == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_for_loop_mixing_an_alias_and_an_unresolved_call(self) -> None:
        """Negative control: even with one element resolving to a known
        alias, a *different* element that is neither Fact-typed nor a
        name at all still disqualifies the whole loop -- the conjunctive
        requirement holds regardless of which element fails it."""
        src = (
            "def f(old, other):\n"
            "    old_fact = old.bases_fact\n"
            "    def some_call():\n"
            "        return 1\n"
            "    for x in (old_fact, some_call()):\n"
            "        return x == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestWalrusInAnnotationsContainingScope:
    """A walrus inside a parameter's own annotation or the `->` return
    annotation evaluates at the identical def-time, in the identical
    containing scope, as a default value does -- the walrus-collection
    loop in `_fact_aliases()` only ever walked defaults, not annotations,
    the binding-side counterpart of a gap this module's read-side sibling
    (`_default_and_annotation_scope_overrides()`) already closed."""

    def test_detects_a_comparison_through_a_walrus_in_a_parameter_annotation(
        self,
    ) -> None:
        src = (
            "def inner(x: (fact := rec.bases_fact)):\n"
            "    return x\n"
            "print(fact == other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 6)]

    def test_detects_a_comparison_through_a_walrus_in_a_return_annotation(
        self,
    ) -> None:
        src = (
            "def inner() -> (fact := rec.bases_fact):\n"
            "    return None\n"
            "print(fact == other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 6)]

    def test_ignores_a_walrus_in_a_nested_lambda_inside_an_annotation(self) -> None:
        """Negative control: the identical nested-scope-boundary rule
        `_iter_default_subtree()` already applies to defaults applies
        here too -- a walrus inside a lambda's own body, itself sitting
        inside an annotation, binds in the lambda's own scope when
        called, never the enclosing one."""
        src = (
            "def inner(x: (lambda: (fact := rec.bases_fact))()):\n"
            "    return x\n"
            "print(fact == other)\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestConditionalExpressionsRecognizedWhenBothBranchesAreFactTyped:
    """`(old.bases_fact if condition else new.bases_fact) == other` --
    both branches independently resolve as Fact-typed, so the whole
    expression is guaranteed to produce one regardless of which branch
    runs."""

    def test_detects_a_comparison_against_an_inline_conditional_expression(
        self,
    ) -> None:
        src = (
            "def f(old, new, condition, other):\n"
            "    return (old.bases_fact if condition else new.bases_fact) "
            "== other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_comparison_against_an_assigned_conditional_expression(
        self,
    ) -> None:
        src = (
            "def f(old, new, condition, other):\n"
            "    fact = old.bases_fact if condition else new.bases_fact\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_a_conditional_expression_with_one_non_fact_branch(self) -> None:
        """Negative control: only one branch is Fact-typed, so the
        expression isn't reliably a Fact regardless of which branch
        actually runs -- must stay unflagged."""
        src = (
            "def f(old, condition, other):\n"
            "    def some_call():\n"
            "        return 1\n"
            "    return (old.bases_fact if condition else some_call()) "
            "== other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestForLoopUnpackingTargetsResolveElementwise:
    """`for fact, tag in ((old.bases_fact, "old"), (new.bases_fact,
    "new")): fact == other` -- the tuple-unpacking sibling of the
    single-target literal-collection case: each target position is
    checked against its own per-iteration value, reusing
    `_paired_unpacking_candidates()`'s own elementwise pairing once per
    iteration element."""

    def test_detects_a_comparison_against_an_unpacked_loop_target(self) -> None:
        src = (
            "def f(old, new, other):\n"
            '    for fact, tag in ((old.bases_fact, "old"), '
            '(new.bases_fact, "new")):\n'
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_ignores_the_sibling_position_that_is_never_fact_typed(self) -> None:
        """Negative control: the *other* unpacked position (`tag`) is
        never Fact-typed across any iteration and must stay unflagged,
        even though a sibling position in the same loop is."""
        src = (
            "def f(old, new, other):\n"
            '    for fact, tag in ((old.bases_fact, "old"), '
            '(new.bases_fact, "new")):\n'
            "        return tag == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_loop_where_one_iteration_element_does_not_pair(self) -> None:
        """Negative control: an iteration element that isn't itself a
        literal display of matching length disqualifies the *whole*
        loop, not just that one iteration -- a partial pairing is never
        attempted."""
        src = (
            "def f(old, other, unrelated):\n"
            '    for fact, tag in ((old.bases_fact, "old"), unrelated):\n'
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_the_starred_capture_but_still_pairs_the_fixed_position(
        self,
    ) -> None:
        """A starred loop target (`*rest`) captures an arbitrary-length,
        per-iteration slice with no single corresponding sub-expression,
        so no candidate is derived for `rest` itself -- confirmed by a
        comparison involving `rest` staying unflagged, mirroring the
        identical starred-target treatment
        `TestElementwiseTupleUnpackingAliases`'s own plain-assignment
        sibling test now pins. `fact`, the *fixed*-position element
        before the star, still pairs against its own real per-iteration
        `bases_fact` sub-expression on every iteration (Codex review,
        fresh evidence: the previous blanket "any Starred anywhere
        disqualifies the whole pairing" rule, propagated here from
        `_paired_unpacking_candidates()`, wrongly discarded this
        fixed-position pairing too)."""
        src = (
            "def f(old, new, other):\n"
            "    for fact, *rest in ((old.bases_fact, 1, 2), "
            "(new.bases_fact, 3, 4)):\n"
            "        unrelated = rest == other\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert sites == [(4, 15)]


class TestConditionalExpressionResolvesThroughAliasBranches:
    """`_candidate_resolves_to_fact()` -- the fixed-point-aware sibling of
    `_is_fact_typed_expr()`'s own `IfExp` branch -- recognizes a
    conditional expression whose branches are themselves bare aliases
    already confirmed Fact-typed, not only structurally Fact-typed
    expressions."""

    def test_detects_a_comparison_against_a_conditional_of_two_aliases(
        self,
    ) -> None:
        src = (
            "def f(old, new, cond, other):\n"
            "    old_fact = old.bases_fact\n"
            "    new_fact = new.bases_fact\n"
            "    fact = old_fact if cond else new_fact\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 11)]

    def test_ignores_a_conditional_where_one_alias_never_resolves(self) -> None:
        """Negative control: only one branch resolves to a known Fact
        alias -- the conditional as a whole isn't reliably a Fact
        regardless of which branch runs."""
        src = (
            "def f(old, cond, other):\n"
            "    old_fact = old.bases_fact\n"
            "    other_val = 5\n"
            "    fact = old_fact if cond else other_val\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestComprehensionFirstIterableResolvesAgainstTheParentScope:
    """A comprehension's tuple-loop-target candidate resolves each
    element against the scope the element's own iterable actually
    evaluates in -- only the first generator's iterable evaluates in the
    parent scope -- while the resolved target name still becomes known in
    the comprehension's own scope, where the actual read happens."""

    def test_detects_a_comparison_where_the_first_iterable_names_a_parent_alias(
        self,
    ) -> None:
        """`fact = rec.bases_fact; [fact == other for fact in (fact,)]`
        -- the tuple element `fact` names the *outer* alias, evaluated
        before the comprehension's own (shadowing) target exists."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return [fact == other for fact in (fact,)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 12)]

    def test_ignores_an_unrelated_name_in_the_first_iterable(self) -> None:
        """Negative control: the tuple element is a bare name that never
        resolves to a known Fact alias in the parent scope."""
        src = (
            "def f(other, unrelated):\n    return [x == other for x in (unrelated,)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_still_detects_a_comparison_over_two_direct_reads(self) -> None:
        """Regression guard: the original, already-fixed shape (both
        elements structurally Fact-typed, no alias indirection) must
        still work after this fix."""
        src = (
            "def f(old, new, other):\n"
            "    return [fact == other for fact in "
            "(old.bases_fact, new.bases_fact)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]


class TestComprehensionUnpackingTargetsResolveElementwise:
    """The comprehension equivalent of `TestForLoopUnpackingTargetsResolveElementwise`
    above -- a comprehension generator's own target can be a
    `Tuple`/`List` unpacking display too, not only a bare `Name`."""

    def test_detects_a_comparison_against_an_unpacked_comprehension_target(
        self,
    ) -> None:
        src = (
            "def f(old, other):\n"
            "    return [fact == other for fact, tag in "
            '((old.bases_fact, "old"),)]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]

    def test_ignores_the_sibling_position_in_an_unpacked_comprehension_target(
        self,
    ) -> None:
        """Negative control: the *other* unpacked position (`tag`) is
        never Fact-typed and must stay unflagged."""
        src = (
            "def f(old, other):\n"
            "    return [tag == other for fact, tag in "
            '((old.bases_fact, "old"),)]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestWholeSubjectMatchCapturesAreAliases:
    """`case fact:` (a bare capture) and `case SomeClass() as fact:` (an
    `as`-pattern) both bind the *entire* match subject unconditionally,
    making the captured name a real alias of `node.subject` -- not merely
    an arbitrary local shadow."""

    def test_detects_a_comparison_through_a_bare_capture(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_comparison_through_an_as_pattern(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case object() as fact:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_ignores_a_nested_capture_inside_a_structural_pattern(self) -> None:
        """Negative control: a capture nested inside a larger structural
        pattern only captures a *sub*-part of the subject, not the whole
        thing, and must not be treated as an alias of the subject itself.
        Uses a dynamic (non-statically-known) subject -- `pair`, not a
        literal display -- so the elementwise structural-sequence pairing
        (`TestStructuralSequencePatternCapturesPairWithTheSubject` in
        `tests/test_fact_detector_misuse_alias_edge_cases.py`, which
        legitimately *does* resolve a nested capture once the subject is a
        literal `Tuple`/`List`) doesn't independently catch this repro
        either, isolating this test to its original, narrower purpose."""
        src = (
            "def f(pair, other):\n"
            "    match pair:\n"
            "        case [x, y]:\n"
            "            return x == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_capture_of_a_non_fact_subject(self) -> None:
        """Negative control: the match subject itself isn't Fact-typed,
        so the capture is an ordinary local, not an alias."""
        src = (
            "def f(rec, other):\n"
            "    match rec.name:\n"
            "        case fact:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestDefaultComprehensionOutermostIterableStaysDefTimeScoped:
    """`_iter_default_subtree()`'s own stop-at-any-scope-boundary rule has
    one exception, mirroring `_enclosing_qualnames`'s/
    `_lexical_function_parents`'s identical carve-out for the same
    construct: a comprehension's *outermost* generator's iterable
    evaluates in the def-time scope, not the comprehension's own new one
    -- `fact = rec.bases_fact; def g(fact, cb=[x for x in (fact ==
    other,)]): ...` was silently missed because the comprehension itself
    was treated as an opaque boundary the moment it was reached, before
    ever descending into that one exempt iterable."""

    def test_detects_a_comparison_in_the_outermost_iterable_of_a_default_comprehension(
        self,
    ) -> None:
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, cb=[x for x in (fact == other,)]):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 32)]

    def test_detects_a_comparison_through_a_double_hopped_nested_comprehension(
        self,
    ) -> None:
        """The exception recurses: the *inner* comprehension's own
        outermost iterable is itself the outer comprehension's outermost
        iterable, so it too evaluates at def-time, two hops out."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, cb=[x for x in [y for y in (fact == other,)]]):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 44)]

    def test_still_detects_a_comparison_in_the_comprehension_elt_via_closure(
        self,
    ) -> None:
        """Positive control, not a regression case: unlike the outermost
        iterable, the `elt` genuinely runs in the comprehension's own new
        scope -- but since that scope is itself created at def-time (while
        `g` is still being defined) and declares no `fact` of its own, it
        closes over the *def-time* scope's alias by ordinary lexical
        scoping, so this must still be detected, just via the general
        closure mechanism rather than this fix's own carve-out."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, cb=[fact == other for x in range(3)]):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 20)]

    def test_ignores_a_comparison_in_a_default_lambdas_own_body(self) -> None:
        """Negative control: a `lambda` nested inside a default (unlike a
        comprehension's outermost iterable) is a genuine, ordinary scope
        boundary -- its own parameter legitimately shadows the outer
        alias for its whole body, and must stay unflagged."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    def g(fact, cb=lambda fact: fact == other):\n"
            "        return cb\n"
            "    return g\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestStringizedFactAnnotationsAreRecognized:
    """`_is_fact_typed_annotation()` recognizes a quoted/stringized
    forward-reference annotation (`def f(old_fact: "Fact[list[str]]",
    other): return old_fact == other`) by parsing the string literal as an
    expression and recursing into it -- a real, common spelling (required
    under `from __future__ import annotations` for anything evaluated
    lazily, and used ad hoc even without it), invisible to every prior
    shape check since it parses as a bare `ast.Constant` string."""

    def test_detects_a_bare_stringized_fact_annotation(self) -> None:
        src = (
            'def f(old_fact: "Fact[list[str]]", other):\n    return old_fact == other\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_stringized_fact_annotation_wrapped_in_optional(
        self,
    ) -> None:
        src = 'def f(old_fact: "Optional[Fact[int]]", other):\n    return old_fact == other\n'
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_stringized_fact_annotation_with_pep604_union(self) -> None:
        src = 'def f(old_fact: "Fact[int] | None", other):\n    return old_fact == other\n'
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_a_malformed_stringized_annotation_does_not_raise(self) -> None:
        """Negative control: an annotation string that isn't valid Python
        at all degrades to "not Fact-typed" rather than propagating a
        `SyntaxError`, matching every other best-effort parse in this
        module."""
        src = 'def f(old_fact: "not: valid ( python", other):\n    return old_fact == other\n'
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_an_unrelated_stringized_annotation_does_not_misfire(self) -> None:
        """Negative control: an ordinary stringized non-Fact annotation
        (`"int"`) must not be treated as Fact-typed just because it's a
        string."""
        src = 'def f(x: "int", other):\n    return x == other\n'
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []
