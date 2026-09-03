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

"""`_is_fact_typed_expr()`'s new `ast.Subscript` recognition
(``scripts/fact_detector_misuse.py``) -- ADR-063 Phase 0
(``docs/contribute/plans/one-semantic-pipeline.md``).

Covers the follow-up finding that a literal display indexed at a
statically known position/key (`(rec.bases_fact,)[0]`, `{"x": rec.
bases_fact}["x"]`) fell through this resolver entirely, letting an
ordinary indexing refactor bypass the mandatory gate (Codex review,
fresh evidence).

**A follow-up round found the selected element itself was still passed
through the scope-blind structural predicate only**: `fact = rec.
bases_fact; (fact,)[0] == other` -- a bare alias inside an otherwise
statically resolvable display -- went unrecognized, since `_is_fact_
typed_expr()` deliberately never resolves a bare `ast.Name` (that needs
`known`/`aliases`, which a structural predicate alone doesn't have).
Fixed by extracting the resolution step itself
(`_static_subscript_element()`) and routing it through both alias-aware
resolvers this module has -- `_candidate_resolves_to_fact()` (the
fixed-point resolver) and `fact_equality_misuse_sites()`'s own
`is_fact_typed()` (the top-level comparison-operand resolver) --
instead of only ever landing back in the purely structural `_is_fact_
typed_expr()`. `TestAliasResolutionInsideAStaticSubscript` below covers
this round; the classes above cover the original, structural-only fix.

**A further round found the dict-key resolution used a forward scan,
returning the *first* matching key rather than the last** (Codex
review, fresh evidence): a real Python dict literal keeps the *last*
value for a repeated (or merely `==`-equal, e.g. `1`/`True`) key --
ordinary dict-construction overwrite semantics -- but `{"x":
Fact.present(1), "x": 0}["x"]` was still reported as a misuse site even
though the value actually selected at runtime is `0`, not the `Fact`
value. Fixed by scanning the full `zip(keys, values)` sequence and
keeping the *last* match rather than returning on the first one.
`TestLastMatchingKeyWins` below covers this round.

**A further round found a literal `None` key was conflated with "the
index couldn't be resolved at all"** (Codex review, fresh evidence):
`index`, the sentinel this function's own slice-resolution used, was
literally `None` both when the slice resolved to the constant `None`
(`x[None]`, a perfectly ordinary dict key) and when nothing resolved at
all -- so `{None: Fact.present(1)}[None]` silently declined to resolve,
letting a genuine `None`-keyed lookup bypass the gate. Fixed by tracking
whether the index was actually resolved in a separate `resolved: bool`,
rather than overloading `index is None` for both meanings.
`TestNoneIsAValidDictionaryKey` below covers this round.
"""

from __future__ import annotations

import ast

from scripts.fact_detector_misuse import fact_equality_misuse_sites


class TestStaticTupleAndListSubscriptResolution:
    def test_single_element_tuple_index_zero(self) -> None:
        src = "def f(rec, other):\n    return (rec.bases_fact,)[0] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_list_display_index_zero(self) -> None:
        src = "def f(rec, other):\n    return [rec.bases_fact][0] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_negative_index(self) -> None:
        src = "def f(rec, other):\n    return (rec.bases_fact,)[-1] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_negative_index_selecting_a_non_fact_element_is_ignored(self) -> None:
        src = "def f(rec, tag, other):\n    return (rec.bases_fact, tag)[-1] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_multi_element_tuple_correct_index(self) -> None:
        src = "def f(rec, tag, other):\n    return (tag, rec.bases_fact)[1] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_multi_element_tuple_wrong_index_is_ignored(self) -> None:
        src = "def f(rec, tag, other):\n    return (tag, rec.bases_fact)[0] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_out_of_bounds_index_is_ignored(self) -> None:
        src = "def f(rec, other):\n    return (rec.bases_fact,)[5] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_non_literal_index_is_ignored(self) -> None:
        """No type inference -- an index that isn't a literal constant
        can't be resolved without runtime evaluation."""
        src = "def f(rec, i, other):\n    return (rec.bases_fact,)[i] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_starred_element_disqualifies_the_whole_display(self) -> None:
        """The identical rule `_paired_unpacking_candidates()` already
        applies to a starred value display: a starred element makes the
        display's own fixed positions no longer statically known."""
        src = (
            "def f(rec, extras, other):\n"
            "    return (*extras, rec.bases_fact)[0] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_recurses_into_a_resolved_nested_element(self) -> None:
        src = (
            "def f(rec, tag, other):\n"
            "    return (tag, tag, (rec.bases_fact,)[0])[2] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_a_non_display_subscript_receiver_is_unaffected(self) -> None:
        """Regression guard: subscripting an ordinary (non-display)
        expression is untouched by this new branch."""
        src = "def f(rec, other):\n    return rec[0] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestStaticDictSubscriptResolution:
    def test_string_key_lookup(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    fact = {"x": rec.bases_fact}["x"]\n'
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_wrong_key_is_ignored(self) -> None:
        src = (
            "def f(rec, other):\n"
            '    fact = {"x": rec.bases_fact}.get("y")\n'
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_non_literal_key_is_ignored(self) -> None:
        src = (
            "def f(rec, k, other):\n"
            '    fact = {"x": rec.bases_fact}[k]\n'
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_dict_expansion_disqualifies_the_whole_display(self) -> None:
        src = (
            "def f(rec, extra, other):\n"
            '    fact = {**extra, "x": rec.bases_fact}["x"]\n'
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestExistingComposedShapesStayUnaffected:
    """Regression guard: the new `Subscript` branch composes with, and
    doesn't disturb, every pre-existing shape `_is_fact_typed_expr()`
    already recognized."""

    def test_ifexp_still_detected(self) -> None:
        src = (
            "def f(old, new, condition, other):\n"
            "    return (old.bases_fact if condition else "
            "new.bases_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_boolop_still_detected(self) -> None:
        src = (
            "def f(old, new, other):\n"
            "    return (old.bases_fact or new.bases_fact) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_namedexpr_still_detected(self) -> None:
        src = "def f(rec, other):\n    return (fact := rec.bases_fact) == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_constructor_call_still_detected(self) -> None:
        src = (
            "from abicheck.model.fact import Fact\n"
            "def f(other):\n"
            "    return Fact(1) == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_attribute_access_still_detected(self) -> None:
        src = "def f(rec, other):\n    return rec.bases_fact == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1


class TestAliasResolutionInsideAStaticSubscript:
    """A bare alias name selected by a static index/key resolves through
    this module's alias-aware machinery, not just the purely structural
    `_is_fact_typed_expr()`."""

    def test_alias_inside_a_tuple_subscript_used_directly(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return (fact,)[0] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_inside_a_dict_subscript_used_directly(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            '    return {"x": fact}["x"] == other\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_alias_selected_and_assigned_before_comparison(self) -> None:
        """The fixed-point resolver (`_candidate_resolves_to_fact()`)
        picks this up too -- the selected element is assigned to a new
        local before it's ever compared."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    g = (fact,)[0]\n"
            "    return g == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_dict_alias_selected_and_assigned_before_comparison(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            '    g = {"x": fact}["x"]\n'
            "    return g == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_wrong_position_alias_is_ignored(self) -> None:
        src = (
            "def f(rec, tag, other):\n"
            "    fact = rec.bases_fact\n"
            "    return (tag, fact)[0] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_chained_alias_inside_a_subscript_is_resolved(self) -> None:
        """The fixed point's own alias-chain resolution (`fact2 = fact`)
        composes with the new Subscript recursion."""
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    fact2 = fact\n"
            "    return (fact2,)[0] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_negative_index_alias_is_resolved(self) -> None:
        src = (
            "def f(rec, other):\n"
            "    fact = rec.bases_fact\n"
            "    return (fact,)[-1] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_non_alias_non_fact_element_stays_ignored(self) -> None:
        src = "def f(tag, other):\n    return (tag,)[0] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []


class TestLastMatchingKeyWins:
    """A real dict literal keeps the *last* value for a repeated key --
    ordinary dict-construction overwrite semantics, not a forward scan
    (Codex review, fresh evidence)."""

    def test_literal_duplicate_key_selects_the_last_value(self) -> None:
        src = 'def f(other):\n    return {"x": Fact.present(1), "x": 0}["x"] == other\n'
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_literal_duplicate_key_where_the_fact_value_is_last(self) -> None:
        src = 'def f(other):\n    return {"x": 0, "x": Fact.present(1)}["x"] == other\n'
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_triple_duplicate_key_selects_the_last_of_three(self) -> None:
        src = (
            'def f(other):\n    return {"x": 0, "x": Fact.present(1), "x": '
            '2}["x"] == other\n'
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_equality_colliding_constants_true_and_one(self) -> None:
        """`True == 1` in Python -- a real dict literal collapses these
        two keys exactly the way a literal duplicate does."""
        src = "def f(other):\n    return {True: Fact.present(1), 1: 0}[1] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_equality_colliding_constants_fact_value_is_last(self) -> None:
        src = "def f(other):\n    return {1: 0, True: Fact.present(1)}[True] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_single_non_duplicate_key_is_unaffected(self) -> None:
        src = 'def f(other):\n    return {"x": Fact.present(1)}["x"] == other\n'
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1


class TestNoneIsAValidDictionaryKey:
    """A literal `None` key is an ordinary, resolvable dict key -- not
    the same thing as "the index couldn't be resolved" (Codex review,
    fresh evidence)."""

    def test_none_key_selects_the_fact_value_directly(self) -> None:
        src = "def f(other):\n    return {None: Fact.present(1)}[None] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_none_key_selects_the_fact_value_via_an_alias(self) -> None:
        src = (
            "def f(other):\n"
            "    fact = {None: Fact.present(1)}[None]\n"
            "    return fact == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_none_key_present_but_a_different_key_selected_is_ignored(self) -> None:
        src = "def f(other):\n    return {None: Fact.present(1)}[1] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_none_wins_a_duplicate_key_tie_as_the_last_entry(self) -> None:
        src = (
            "def f(other):\n    return {None: 0, None: Fact.present(1)}[None] "
            "== other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1

    def test_regression_dict_expansion_still_unresolvable_with_a_none_key(
        self,
    ) -> None:
        """`**expansion` disqualifies the whole display regardless of
        which key is being looked up -- unrelated to this fix, but
        verified not to interact badly with it."""
        src = (
            "def f(extra, other):\n"
            "    return {**extra, None: Fact.present(1)}[None] == other\n"
        )
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_regression_non_literal_index_on_a_dict_with_a_none_key_unresolvable(
        self,
    ) -> None:
        src = "def f(k, other):\n    return {None: Fact.present(1)}[k] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_regression_tuple_indexed_by_none_stays_unresolvable(self) -> None:
        """`None` is never a valid tuple/list index -- a real `TypeError`
        at runtime -- so this must stay unresolved, unlike the dict
        case this fix actually addresses."""
        src = "def f(other):\n    return (Fact.present(1),)[None] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert fact_equality_misuse_sites(tree, "x.py") == []

    def test_regression_negative_index_zero_still_resolves(self) -> None:
        """Regression guard for the `resolved` flag itself: a falsy but
        genuinely resolved index (`0`) must not be mistaken for
        "unresolved" either."""
        src = "def f(other):\n    return (Fact.present(1),)[0] == other\n"
        tree = ast.parse(src, filename="x.py")
        assert len(fact_equality_misuse_sites(tree, "x.py")) == 1
