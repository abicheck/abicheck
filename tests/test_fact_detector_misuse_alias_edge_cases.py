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

"""Alias-resolution edge-case tests for the ``fact-detector-misuse``
AI-readiness check (``scripts/fact_detector_misuse.py``, registered by
``scripts/check_ai_readiness.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Split out of ``test_fact_detector_misuse_def_time_scope.py`` once that
file approached the architecture gate's own 1200-line test-file cap --
mechanical extraction, not a redesign: every test class here is moved
unchanged, as a contiguous block, from that file's own tail -- the same
reason that file was itself split out of
``test_fact_detector_misuse_scoping.py``; see either file's own
docstring for the fuller history.

Covers a coherent later slice of the same alias-resolution machinery
(``_fact_aliases()``/``_candidate_resolves_to_fact()``/
``fact_equality_misuse_sites()``'s own terminal ``is_fact_typed()``
predicate) rather than a new bug class of its own: a direct conditional
comparison operand, a `NamedExpr`-wrapped alias, an `Annotated[...]`
type-annotation wrapper, and an OR-pattern whole-subject `match` capture.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestDirectConditionalOperandsResolveThroughAliasBranches:
    """`fact_equality_misuse_sites()`'s own terminal `is_fact_typed()`
    predicate (distinct from `_is_fact_typed_expr()`'s purely-structural
    `IfExp` branch used by `_fact_aliases()`'s fixed point) must resolve an
    alias `Name` inside *either* branch of a conditional expression that is
    itself a direct comparison operand -- `(old_fact if cond else
    new_fact) == other`, never assigned to an intermediate variable at all,
    so there is no candidate for the fixed point to register in the first
    place. Mirrors `TestConditionalExpressionResolvesThroughAliasBranches`
    above, but for the terminal comparison-operand check rather than the
    alias-collection fixed point."""

    def test_detects_a_direct_conditional_operand_through_both_alias_branches(
        self,
    ) -> None:
        src = (
            "def f(rec, other, cond):\n"
            "    old_fact = rec.bases_fact\n"
            "    new_fact = rec.bases_fact\n"
            "    return (old_fact if cond else new_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 11)]

    def test_detects_a_nested_direct_conditional_operand(self) -> None:
        """A conditional nested inside a conditional operand -- both levels
        must resolve, not just the outermost one."""
        src = (
            "def f(rec, other, cond, cond2):\n"
            "    old_fact = rec.bases_fact\n"
            "    new_fact = rec.bases_fact\n"
            "    third_fact = rec.bases_fact\n"
            "    return (old_fact if cond else "
            "(new_fact if cond2 else third_fact)) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 11)]

    def test_ignores_a_direct_conditional_operand_of_non_fact_aliases(self) -> None:
        """Negative control: neither branch resolves to a Fact-typed
        value, so the comparison must stay unflagged."""
        src = (
            "def f(rec, other, cond):\n"
            "    old_x = rec.plain\n"
            "    new_x = rec.plain\n"
            "    return (old_x if cond else new_x) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_direct_conditional_operand_with_only_one_fact_branch(
        self,
    ) -> None:
        """Negative control pinning the established AND semantics (shared
        with `_is_fact_typed_expr()`'s own `IfExp` branch and with
        `_candidate_resolves_to_fact()`): a conditional operand is treated
        as Fact-typed only when *both* branches resolve, since a
        single-branch match cannot be told apart from an ordinary
        conditional expression that merely happens to read one Fact
        attribute among other unrelated locals."""
        src = (
            "def f(rec, other, cond):\n"
            "    old_fact = rec.bases_fact\n"
            "    new_x = rec.plain\n"
            "    return (old_fact if cond else new_x) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestNamedExpressionsResolveThroughAliasBranches:
    """`_is_fact_typed_expr()`'s own `NamedExpr` branch unwraps to the
    walrus's `.value`, but it can't resolve a bare `Name` there itself --
    that needs alias resolution, which is `_candidate_resolves_to_fact()`
    (for a candidate/default) and `is_fact_typed()` (for a direct
    comparison operand) to supply, each now with its own recursive
    `NamedExpr` branch mirroring the `IfExp` one already added for the
    identical reason."""

    def test_detects_a_direct_named_expression_wrapping_an_alias(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    old_fact = rec.bases_fact\n"
            "    return (copy := old_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_an_assignment_through_a_named_expression_alias(self) -> None:
        """A named expression's own result, assigned to a further name,
        must resolve through the ordinary candidate fixed point too --
        not only when used inline as the comparison operand itself."""
        src = (
            "def f(rec, other):\n"
            "    old_fact = rec.bases_fact\n"
            "    fact = (copy := old_fact)\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 11)]

    def test_detects_a_nested_named_expression(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    old_fact = rec.bases_fact\n"
            "    return (a := (b := old_fact)) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_a_named_expression_wrapping_a_non_fact_alias(self) -> None:
        """Negative control: the wrapped name must itself resolve to a
        Fact alias."""
        src = "def f(rec, other):\n    x = rec.plain\n    return (copy := x) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_composed_default_via_conditional_expression(self) -> None:
        """A parameter default that is itself a conditional expression
        composing two already-known aliases must resolve through
        `_candidate_resolves_to_fact()`, not only a bare-name default."""
        src = (
            "def f(rec, other, cond):\n"
            "    old = rec.bases_fact\n"
            "    new = rec.vtable_fact\n"
            "    def inner(value=old if cond else new):\n"
            "        return value == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 15)]

    def test_ignores_a_composed_default_with_only_one_fact_branch(self) -> None:
        """Negative control pinning the established AND semantics: a
        default's own conditional expression is trusted only when both
        branches resolve."""
        src = (
            "def f(rec, other, cond):\n"
            "    old = rec.bases_fact\n"
            "    new = rec.plain\n"
            "    def inner(value=old if cond else new):\n"
            "        return value == other\n"
            "    return inner()\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestAnnotatedWrapperUnwrapsToItsFirstSliceElement:
    """`Annotated[Fact[int], metadata]` (PEP 593) is exactly as Fact-typed
    as the bare `Fact[int]` it wraps -- only the *first* slice element is
    the real type; every following element is arbitrary metadata."""

    def test_detects_a_comparison_of_an_annotated_fact_parameter(self) -> None:
        src = (
            "from typing import Annotated\n"
            'def f(value: Annotated[Fact[int], "meta"], other):\n'
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_comparison_with_multiple_metadata_items(self) -> None:
        src = (
            "from typing import Annotated\n"
            'def f(value: Annotated[Fact[int], "m1", "m2"], other):\n'
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_annotated_composed_with_optional(self) -> None:
        src = (
            "from typing import Annotated, Optional\n"
            'def f(value: Annotated[Optional[Fact[int]], "meta"], other):\n'
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_an_annotated_non_fact_type(self) -> None:
        """Negative control: the wrapped type must itself be Fact-typed."""
        src = (
            "from typing import Annotated\n"
            'def f(value: Annotated[int, "meta"], other):\n'
            "    return value == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestMatchOrPropagatesWholeSubjectCaptures:
    """An OR pattern where *every* alternative is itself a top-level
    `MatchAs` capturing the whole subject under the identical name is a
    real alias of the match subject -- Python requires every alternative
    to bind the same *names*, but not the same binding *shape*, so this
    is only safe when each alternative is individually a whole-subject
    capture."""

    def test_detects_a_comparison_through_an_or_pattern_whole_subject_capture(
        self,
    ) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (list() as fact) | (tuple() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_three_way_or_pattern(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (list() as fact) | (tuple() as fact) | (dict() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_bare_capture_mixed_with_an_as_pattern(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact | (tuple() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_ignores_an_or_pattern_mixing_a_subpart_and_a_whole_capture(self) -> None:
        """Negative control: `fact` is bound to a sub-part in one
        alternative and the whole subject in the other -- not safe to
        trust as a whole-subject alias even though the name is
        consistent."""
        src = (
            "class C:\n"
            "    x = None\n"
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case C(x=fact) | (list() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_or_pattern_capture_of_a_non_fact_subject(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.name:\n"
            "        case (list() as fact) | (tuple() as fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestBoolOpResolvesWhenEveryOperandIsFactTyped:
    """Python's `and`/`or` always return one of their own operands
    verbatim, never a synthesized `True`/`False` -- so if every operand
    is guaranteed Fact-typed, the result is too, regardless of which one
    short-circuit evaluation actually selects."""

    def test_detects_a_direct_or_expression(self) -> None:
        src = (
            "def f(old, new, other):\n"
            "    return (old.bases_fact or new.bases_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_direct_and_expression(self) -> None:
        src = (
            "def f(old, new, other):\n"
            "    return (old.bases_fact and new.bases_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_three_way_chain(self) -> None:
        src = (
            "def f(a, b, c, other):\n"
            "    return (a.bases_fact or b.bases_fact or c.bases_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_an_assignment_through_a_bool_op_alias(self) -> None:
        src = (
            "def f(old, new, other):\n"
            "    fact = old.bases_fact or new.bases_fact\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_bool_op_operands_resolved_through_existing_aliases(self) -> None:
        src = (
            "def f(old, new, other):\n"
            "    a = old.bases_fact\n"
            "    b = new.bases_fact\n"
            "    return (a or b) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 11)]

    def test_ignores_a_bool_op_with_one_non_fact_operand(self) -> None:
        """Negative control pinning the established AND semantics: every
        operand must resolve, since a single-operand match cannot be
        told apart from an ordinary boolean expression that merely
        happens to read one Fact attribute among other unrelated
        locals."""
        src = (
            "def f(old, new, other):\n"
            "    return (old.bases_fact or new.plain) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestComprehensionWalrusResolvesItsRhsInItsOwnScope:
    """A walrus that PEP 572 hops out of a comprehension binds its
    *target* at the enclosing scope it hops to, but its RHS is still
    written -- and must still resolve -- in the comprehension's own
    scope, which can differ from the binding scope."""

    def test_detects_a_hopped_walrus_whose_rhs_is_the_loop_target(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    [(captured := fact) for fact in (rec.bases_fact,)]\n"
            "    return captured == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_a_hopped_walrus_with_a_non_fact_rhs(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    [(captured := fact) for fact in (rec.plain,)]\n"
            "    return captured == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_double_hopped_walrus_through_nested_comprehensions(
        self,
    ) -> None:
        src = (
            "def f(rec, other):\n"
            "    [[(captured := fact) for fact in (rec.bases_fact,)]"
            " for _ in range(1)]\n"
            "    return captured == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_walrus_with_no_hop_still(self) -> None:
        """Regression guard: a walrus directly inside a function body (no
        comprehension, no hop) must still resolve the ordinary way."""
        src = (
            "def f(rec, other):\n"
            "    (captured := rec.bases_fact)\n"
            "    return captured == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]


class TestStaticDisplayLoopTargetsRecognizeSetAndDictKeys:
    """`_static_display_elements()` generalizes the single-target loop/
    comprehension binding case beyond `Tuple`/`List` to a set display
    (`{a, b}`) and a dict display (iterated as its keys, `{a: 1, b: 2}`)
    -- both statically enumerable displays the original `Tuple`/`List`-
    only check missed entirely (Codex review, fresh evidence): `for fact
    in {old.bases_fact, new.bases_fact}: fact == other` reused the
    identical "one loop target bound, one iteration at a time, to every
    element" reasoning already established for a tuple, just spelled with
    a different container literal."""

    def test_detects_a_set_display_for_loop(self) -> None:
        src = (
            "def f(rec1, rec2, other):\n"
            "    for fact in {rec1.bases_fact, rec2.bases_fact}:\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_detects_a_set_display_comprehension(self) -> None:
        src = (
            "def f(rec1, rec2, other):\n"
            "    return [fact == other for fact in "
            "{rec1.bases_fact, rec2.bases_fact}]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]

    def test_detects_a_dict_keys_for_loop(self) -> None:
        src = (
            "def f(rec1, rec2, other):\n"
            "    for fact in {rec1.bases_fact: 1, rec2.bases_fact: 2}:\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_detects_a_dict_keys_comprehension(self) -> None:
        src = (
            "def f(rec1, rec2, other):\n"
            "    return [fact == other for fact in "
            "{rec1.bases_fact: 1, rec2.bases_fact: 2}]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]

    def test_ignores_a_set_display_with_one_non_fact_element(self) -> None:
        """Negative control: only *some* elements are Fact-typed, so the
        loop target is only sometimes a Fact -- must stay unflagged,
        mirroring the identical tuple-display negative control."""
        src = (
            "def f(rec1, other, unrelated):\n"
            "    for fact in {rec1.bases_fact, unrelated()}:\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_dict_display_with_a_double_star_expansion(self) -> None:
        """Negative control: `**extra` makes the dict's own key set not
        statically enumerable at all (an arbitrary key could come from
        `extra`), so the whole display must be treated as unrecognized,
        not as if the expansion silently contributed nothing."""
        src = (
            "def f(rec1, other, extra):\n"
            "    for fact in {rec1.bases_fact: 1, **extra}:\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_an_empty_dict_display(self) -> None:
        """Negative control: an empty display has no elements to be
        Fact-typed, matching the existing empty-tuple/list guard. `{}` is
        Python's only empty-display literal (there is no bare empty-set
        syntax -- `set()` is a call, not a display, and correctly isn't
        recognized as one at all by `_static_display_elements()`)."""
        src = "def f(rec1, other):\n    for fact in {}:\n        return fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestDestructuredLoopsRecognizeSetAndDictKeyDisplays:
    """The tuple-*unpacking* loop/comprehension branches share
    `_static_display_elements()` (via a reused `display_elts`/
    `gen_display_elts` local) with their simple-target siblings, rather
    than a hand-rolled `isinstance(..., (ast.Tuple, ast.List))` check of
    their own (Codex review, fresh evidence): `for fact, tag in
    {(rec.bases_fact, "old")}: fact == other` -- a set of tuples -- was
    invisible, as was the identical dict-keys and comprehension form."""

    def test_detects_a_destructured_set_display_for_loop(self) -> None:
        src = (
            'def f(rec, other):\n    for fact, tag in {(rec.bases_fact, "old")}:\n'
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_detects_a_destructured_set_display_comprehension(self) -> None:
        src = (
            "def f(rec, other):\n    return [fact == other for fact, tag in "
            '{(rec.bases_fact, "old")}]\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 12)]

    def test_detects_a_destructured_dict_keys_for_loop(self) -> None:
        src = (
            'def f(rec, other):\n    for fact, tag in {(rec.bases_fact, "old"): 1}:\n'
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]

    def test_ignores_a_destructured_set_with_a_non_fact_element(self) -> None:
        """Negative control: only *some* tuples' first element is
        Fact-typed, so the target is only sometimes a Fact."""
        src = (
            "def f(rec, other, unrelated):\n"
            '    for fact, tag in {(rec.bases_fact, "old"), (unrelated(), "x")}:\n'
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestComposedLoopElementsDeferToTheAliasFixedPoint:
    """`_admissible_loop_element()` widens a loop/comprehension display
    element's admission gate beyond "already Fact-typed, or a bare name"
    to include every composed shape `_candidate_resolves_to_fact()`
    already knows how to resolve (`NamedExpr`/`IfExp`/`BoolOp`) -- deferred
    to fixed-point time exactly like a bare name already was (Codex
    review, fresh evidence): `old = rec1.bases_fact; new = rec2.
    vtable_fact; for fact in (old if cond else new,): fact == other` was
    rejected outright, even though `_candidate_resolves_to_fact()` already
    has its own `IfExp` branch built for exactly this shape."""

    def test_detects_an_ifexp_loop_element_resolved_through_aliases(self) -> None:
        src = (
            "def f(rec1, rec2, cond, other):\n"
            "    old = rec1.bases_fact\n"
            "    new = rec2.vtable_fact\n"
            "    for fact in (old if cond else new,):\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 15)]

    def test_detects_an_ifexp_loop_element_in_a_comprehension(self) -> None:
        src = (
            "def f(rec1, rec2, cond, other):\n"
            "    old = rec1.bases_fact\n"
            "    new = rec2.vtable_fact\n"
            "    return [fact == other for fact in (old if cond else new,)]\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 12)]

    def test_ignores_an_ifexp_loop_element_with_one_non_fact_branch(self) -> None:
        """Negative control: only one branch resolves as Fact-typed, so
        the element isn't reliably a Fact -- must stay unflagged."""
        src = (
            "def f(rec1, cond, other, unrelated):\n"
            "    old = rec1.bases_fact\n"
            "    for fact in (old if cond else unrelated(),):\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestGenericSpecializedFactConstructorsAreRecognized:
    """`Fact[int](...)`/`Fact[int].present(...)` -- a generic
    specialization of `Fact` is still exactly `Fact` at runtime, but the
    callable is an `ast.Subscript` (or an `ast.Attribute` whose `.value`
    is one), invisible to a check that only ever unwrapped a bare
    `ast.Name` (Codex review, fresh evidence)."""

    def test_detects_a_subscripted_bare_constructor_comparison(self) -> None:
        src = "def f(a, b):\n    return Fact[int](a) == Fact[int](b)\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_detects_a_subscripted_classmethod_constructor_comparison(self) -> None:
        src = "def f(a, b):\n    return Fact[int].present(a) == Fact[int].present(b)\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]

    def test_still_detects_the_unspecialized_constructor_form(self) -> None:
        """Regression guard: the fix must not disturb the plain,
        unspecialized `Fact(...)` form."""
        src = "def f(a, b):\n    return Fact(a) == Fact(b)\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(2, 11)]
