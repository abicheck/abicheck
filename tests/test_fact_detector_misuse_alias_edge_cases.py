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


class TestStructuralSequencePatternCapturesPairWithTheSubject:
    """`_paired_match_sequence_candidates()` pairs a structural sequence
    pattern's own captures against a statically-known `Tuple`/`List`
    subject's elements, positionally -- the `match`/`case` sibling of
    `_paired_unpacking_candidates()` (Codex review, fresh evidence):
    `match (rec.bases_fact, tag): case (fact, _): return fact == other`
    -- `fact` is definitively the subject tuple's first, Fact-typed
    element, but only a bare whole-subject `MatchAs`/OR-of-`MatchAs` was
    previously recognized, never a structural pattern capturing a
    sub-part of the subject."""

    def test_detects_a_sequence_pattern_subvalue_capture(self) -> None:
        src = (
            "def f(rec, tag, other):\n"
            "    match (rec.bases_fact, tag):\n"
            "        case (fact, _):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_nested_sequence_pattern_capture(self) -> None:
        src = (
            "def f(rec, tag, other):\n"
            '    match ((rec.bases_fact, "x"), tag):\n'
            "        case ((fact, _), _):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_trailing_capture_after_a_star_pattern(self) -> None:
        src = (
            "def f(rec1, rec2, other):\n"
            '    match ("x", rec1.bases_fact, rec2.vtable_fact):\n'
            "        case (*_rest, fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_leading_capture_before_a_star_pattern(self) -> None:
        src = (
            "def f(rec1, rec2, other):\n"
            '    match (rec1.bases_fact, "x", rec2.vtable_fact):\n'
            "        case (fact, *_rest):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_a_wildcard_position_does_not_block_a_capture_elsewhere(self) -> None:
        """Positive control: a non-capturing sub-pattern (`_`) at one
        position must not disqualify a real capture found at another --
        only a shape mismatch (length, multiple stars) should."""
        src = (
            "def f(rec, unrelated, other):\n"
            "    match (rec.bases_fact, unrelated()):\n"
            "        case (fact, _):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_ignores_a_captured_non_fact_element(self) -> None:
        """Negative control: the captured element genuinely isn't
        Fact-typed -- must stay unflagged."""
        src = (
            "def f(rec, unrelated, other):\n"
            "    match (unrelated(), rec.bases_fact):\n"
            "        case (x, _):\n"
            "            return x == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_dynamic_non_static_subject(self) -> None:
        """Negative control: the subject isn't a literal display at all,
        so no element can be identified -- must stay unflagged."""
        src = (
            "def f(pair, other):\n"
            "    match pair:\n"
            "        case (fact, _):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_pattern_subject_length_mismatch(self) -> None:
        """Negative control: a pattern with more elements than the
        statically-known subject can never actually match -- no position
        can be confidently attributed, so no candidates are registered."""
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case (fact, extra):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestStarredUnpackingTargetsStillPairFixedPositions:
    """`fact, *rest = old.bases_fact, new.bases_fact, extra` -- a single
    `Starred` *target* element used to disqualify the whole pairing
    outright, the same blanket rule a starred *value* element still
    correctly triggers. Fixed-position elements before and after the star
    now pair against the value display's own (starless) elements the
    identical way a starless unpacking already does (Codex review, fresh
    evidence)."""

    def test_detects_a_fixed_position_before_the_star(self) -> None:
        src = (
            "def f(old, new, extra, other):\n"
            "    fact, *rest = old.bases_fact, new.bases_fact, extra\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_detects_a_fixed_position_after_the_star(self) -> None:
        src = (
            "def f(old, new, extra, other):\n"
            "    *rest, fact = extra, old.bases_fact, new.bases_fact\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 11)]

    def test_ignores_the_starred_capture_itself(self) -> None:
        """Negative control: `rest` captures a runtime-length slice, not
        a single Fact-typed value -- must stay unflagged even though a
        fixed sibling position in the same unpacking is Fact-typed."""
        src = (
            "def f(old, new, extra, other):\n"
            "    fact, *rest = old.bases_fact, new.bases_fact, extra\n"
            "    return rest == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_still_ignores_a_starred_value_element(self) -> None:
        """Regression guard: a starred *value* element (a genuine
        dynamic expansion of unknown length) must still disqualify the
        whole pairing, regardless of the target's own shape."""
        src = (
            "def f(old, new, extras, other):\n"
            "    fact, tag = (*extras, old.bases_fact)\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_starred_target_in_a_for_loop(self) -> None:
        """The identical fix reached through `ast.For`'s own reuse of
        `_paired_unpacking_candidates()` per iteration element."""
        src = (
            "def f(old, new, other):\n"
            "    for fact, *rest in ((old.bases_fact, 1, 2), "
            "(new.bases_fact, 3, 4)):\n"
            "        return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(3, 15)]


class TestStructuralMappingPatternCapturesPairWithTheSubject:
    """`case {"fact": fact}:` -- a structural mapping pattern capturing a
    sub-part of a statically-known `Dict` subject, the `MatchMapping`
    sibling of `TestStructuralSequencePatternCapturesPairWithTheSubject`
    above (Codex review, fresh evidence)."""

    def test_detects_a_capture_by_literal_key(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    match {"fact": rec.bases_fact}:\n'
            '        case {"fact": fact}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_a_rest_capture_does_not_block_a_capture_elsewhere(self) -> None:
        """Positive control: `**rest` at one key must not disqualify a
        real capture at another key."""
        src = (
            "def f(rec, other):\n"
            '    match {"fact": rec.bases_fact, "tag": 1}:\n'
            '        case {"fact": fact, **rest}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_ignores_a_key_absent_from_the_subject(self) -> None:
        """Negative control: a pattern key with no matching literal key
        in the subject contributes no candidate for that key."""
        src = (
            "def f(rec, other):\n"
            '    match {"other_key": rec.bases_fact}:\n'
            '        case {"fact": fact}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_dynamic_non_dict_subject(self) -> None:
        """Negative control: the subject isn't a literal `Dict` display
        at all, so no key can be identified -- must stay unflagged."""
        src = (
            "def f(pair, other):\n"
            "    match pair:\n"
            '        case {"fact": fact}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_non_literal_subject_key(self) -> None:
        """Negative control: a non-literal subject key can't be matched
        against a pattern's own literal key without runtime evaluation."""
        src = (
            "def f(rec, other, k):\n"
            "    match {k: rec.bases_fact}:\n"
            '        case {"fact": fact}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_nested_sequence_pattern_inside_a_mapping(self) -> None:
        """Nesting: a further sequence pattern matched against a further
        literal subject entry, reached from inside a mapping pattern."""
        src = (
            "def f(rec, other):\n"
            '    match {"pair": (rec.bases_fact, 1)}:\n'
            '        case {"pair": (fact, _)}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]


class TestDecoratorExpressionsResolveAgainstTheContainingScope:
    """A decorator (`@deco(fact == other)`) evaluates while the decorated
    statement itself executes -- before the function/class it decorates
    even exists -- in whatever scope directly, syntactically contains
    that statement, the identical def-time treatment
    `_default_and_annotation_scope_overrides()` already gives a
    default/annotation/`ClassDef` base or keyword (Codex review, fresh
    evidence: the previous subtree collection had no `decorator_list`
    entry at all)."""

    def test_detects_a_comparison_inside_a_function_decorator(self) -> None:
        src = (
            "def deco(x):\n"
            "    return lambda f: f\n"
            "fact = rec.bases_fact\n"
            "@deco([x for x in (fact == other,)])\n"
            "def f(fact):\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_comparison_inside_a_class_decorator(self) -> None:
        src = (
            "def deco(x):\n"
            "    return lambda c: c\n"
            "fact = rec.bases_fact\n"
            "@deco(fact == other)\n"
            "class C:\n"
            "    pass\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 6)]

    def test_a_decorator_shadow_does_not_leak_into_the_function_body(self) -> None:
        """Regression guard: the decorator override must not disturb the
        existing rule that a real parameter shadows an outer alias for
        the function's own body -- only the decorator's own comparison
        resolves against the enclosing scope."""
        src = (
            "def deco(x):\n"
            "    return lambda f: f\n"
            "fact = rec.bases_fact\n"
            "@deco(fact == other)\n"
            "def f(fact, other2):\n"
            "    return fact == other2\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 6)]

    def test_ignores_a_non_fact_decorator_expression(self) -> None:
        src = (
            "def deco(x):\n    return lambda f: f\n@deco(1 == 2)\ndef f():\n    pass\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_detects_a_nested_function_decorator_against_its_own_enclosing_function(
        self,
    ) -> None:
        src = (
            "def deco(x):\n"
            "    return lambda f: f\n"
            "def outer(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    @deco(fact == other)\n"
            "    def inner(fact):\n"
            "        return fact\n"
            "    return inner\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 10)]

    def test_detects_a_method_decorator_against_its_own_class_body(self) -> None:
        """A method's decorator is evaluated while its *containing class
        body* executes -- ordinary class-body code, not a closure lookup
        -- the same distinction `_default_and_annotation_scope_
        overrides()`'s own method-default handling already draws."""
        src = (
            "def deco(x):\n"
            "    return lambda f: f\n"
            "class C:\n"
            "    fact = rec.bases_fact\n"
            "    @deco(fact == other)\n"
            "    def m(self, fact):\n"
            "        return fact\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(5, 10)]


class TestNestedMatchAsChainsPropagateWholeSubjectCaptures:
    """`case fact as alias:` parses as a *nested* `MatchAs`
    (`MatchAs(pattern=MatchAs(name="fact"), name="alias")`), not a
    structural sub-pattern -- both `alias` (the outer capture) and `fact`
    (the inner one) are equally real whole-subject aliases (Codex review,
    fresh evidence: the previous fix only ever registered the outer
    `case.pattern.name`)."""

    def test_detects_a_comparison_through_the_inner_name(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact as alias:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_still_detects_a_comparison_through_the_outer_name(self) -> None:
        """Regression guard: the outer name is still recognized, the
        same as before this fix."""
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact as alias:\n"
            "            return alias == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_comparison_through_both_names_independently(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case fact as alias:\n"
            "            return fact == other and alias == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        sites = fact_equality_misuse_sites(tree, "x.py")
        assert len(sites) == 2

    def test_a_single_level_as_pattern_still_works_unchanged(self) -> None:
        """Regression guard: `case SomeClass() as fact:` wraps a real
        structural sub-pattern, not a further `MatchAs` -- still exactly
        the one name it always registered."""
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case object() as alias:\n"
            "            return alias == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_or_pattern_with_identical_nested_chains_is_trusted(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (fact as alias) | (fact as alias):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_or_pattern_with_mismatched_nested_chains_is_not_trusted(self) -> None:
        """Negative control: only the *outer* name (`alias`) is
        guaranteed to be the same set of names Python's own grammar
        requires -- the inner names differ, so neither is safe to trust
        as the raw subject regardless of which alternative matched."""
        src = (
            "def f(rec, other):\n"
            "    match rec.bases_fact:\n"
            "        case (fact as alias) | (other_fact as alias):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_ignores_a_nested_capture_inside_a_structural_sub_pattern(self) -> None:
        """Negative control: `y as fact` inside a sequence pattern
        captures only a *sub*-part of the subject -- must stay unflagged,
        confirming this fix doesn't widen `_matchas_chain_names()` beyond
        genuine whole-subject `MatchAs` nesting."""
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact, 1):\n"
            "        case [x, y as fact]:\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestNestedMatchAsChainsInsideStructuralPositions:
    """`_paired_sub_pattern_candidates()`'s own chained-`MatchAs` handling
    (Codex review, fresh evidence): `case (fact as alias,): return fact ==
    other` -- `fact` is the *inner* name of a chained `MatchAs` at a
    structural-sequence position, the identical nested-`MatchAs` shape
    `TestNestedMatchAsChainsPropagateWholeSubjectCaptures` already covers
    at the whole-subject level, but the structural-pairing branch
    previously extracted only `sub_pattern.name` (the outer name)."""

    def test_detects_a_comparison_through_the_inner_chained_name(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case (fact as alias,):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_still_detects_a_comparison_through_the_outer_chained_name(self) -> None:
        """Regression guard: the outer name stays recognized too."""
        src = (
            "def f(rec, other):\n"
            "    match (rec.bases_fact,):\n"
            "        case (fact as alias,):\n"
            "            return alias == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_detects_a_mapping_position_chained_name(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    match {"fact": rec.bases_fact}:\n'
            '        case {"fact": fact as alias}:\n'
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]


class TestStructuralSubPatternWrappedByMatchAsAtAPosition:
    """A structural sub-pattern can itself be wrapped by `MatchAs` at a
    position (`case ((fact, _) as alias,):`) -- previously fell through
    every branch of the per-position handling untouched, since the
    position's own top-level node is `MatchAs`, not `MatchSequence`
    directly (Codex review, fresh evidence)."""

    def test_detects_a_capture_inside_a_wrapped_nested_sequence(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    match ((rec.bases_fact, "x"),):\n'
            "        case ((fact, _) as alias,):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]

    def test_also_detects_the_wrapping_alias_as_a_whole_element_capture(
        self,
    ) -> None:
        """The wrapping `alias` name is a real whole-*element* alias too
        (bound to the same tuple `(rec.bases_fact, "x")`, not itself
        Fact-typed) -- comparing it must stay unflagged."""
        src = (
            "def f(rec, other):\n"
            '    match ((rec.bases_fact, "x"),):\n'
            "        case ((fact, _) as alias,):\n"
            "            return alias == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestStarredSubjectDisqualifiesStructuralSequencePairing:
    """A starred subject element (`match (*extras, rec.bases_fact):`) is a
    dynamic expansion of unknown length, so no pattern position can be
    confidently attributed to a known subject element -- the identical
    rule `_paired_unpacking_candidates()` already applies to a starred
    value display (Codex review, fresh evidence: this guard existed on
    the assignment-unpacking sibling but not on the match-subject one)."""

    def test_ignores_a_capture_positioned_against_a_starred_subject(self) -> None:
        src = (
            "def f(rec, other, extras):\n"
            "    match (*extras, rec.bases_fact):\n"
            "        case (_, fact):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_unstarred_subject_still_pairs_normally(self) -> None:
        """Regression guard: a subject with no starred element is
        unaffected by the new guard."""
        src = (
            "def f(rec, other, tag):\n"
            "    match (rec.bases_fact, tag):\n"
            "        case (fact, _):\n"
            "            return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == [(4, 19)]
