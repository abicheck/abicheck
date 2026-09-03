# Copyright 2026 Nikolay Petrov
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

"""Unit tests for the reusable diff building blocks in ``diff_helpers``."""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_helpers import (
    bool_transition,
    build_type_map,
    depth_aware_bare_name,
    diff_by_key,
    fact_known_qualified,
    lookup_matched_type,
    type_map_key,
)
from abicheck.model import AbiSnapshot, RecordType

ADDED = (ChangeKind.FUNC_VIRTUAL_ADDED, "added")
REMOVED = (ChangeKind.FUNC_VIRTUAL_REMOVED, "removed")


class TestBoolTransition:
    def test_false_to_true_emits_added(self) -> None:
        out = bool_transition(False, True, "sym", added=ADDED, removed=REMOVED)
        assert [c.kind for c in out] == [ChangeKind.FUNC_VIRTUAL_ADDED]
        assert out[0].symbol == "sym"
        assert out[0].description == "added"

    def test_true_to_false_emits_removed(self) -> None:
        out = bool_transition(True, False, "sym", added=ADDED, removed=REMOVED)
        assert [c.kind for c in out] == [ChangeKind.FUNC_VIRTUAL_REMOVED]

    def test_no_change_emits_nothing(self) -> None:
        assert bool_transition(True, True, "sym", added=ADDED, removed=REMOVED) == []
        assert bool_transition(False, False, "sym", added=ADDED, removed=REMOVED) == []

    def test_direction_without_spec_is_silent(self) -> None:
        # Only `added` registered: a removal transition produces nothing.
        assert bool_transition(True, False, "sym", added=ADDED) == []
        assert bool_transition(False, True, "sym", added=ADDED)[0].kind == ADDED[0]

    def test_values_are_carried_through(self) -> None:
        out = bool_transition(
            False,
            True,
            "sym",
            added=ADDED,
            added_values=("non-virtual", "virtual"),
        )
        assert out[0].old_value == "non-virtual"
        assert out[0].new_value == "virtual"

    def test_removed_values_are_carried_through(self) -> None:
        out = bool_transition(
            True,
            False,
            "sym",
            removed=REMOVED,
            removed_values=("virtual", "non-virtual"),
        )
        assert out[0].old_value == "virtual"
        assert out[0].new_value == "non-virtual"

    def test_default_values_are_none(self) -> None:
        out = bool_transition(False, True, "sym", added=ADDED)
        assert out[0].old_value is None
        assert out[0].new_value is None

    def test_skip_none_suppresses_on_either_side(self) -> None:
        assert bool_transition(None, True, "sym", added=ADDED, skip_none=True) == []
        assert bool_transition(False, None, "sym", added=ADDED, skip_none=True) == []
        assert bool_transition(None, None, "sym", added=ADDED, skip_none=True) == []

    def test_without_skip_none_treats_none_as_falsey(self) -> None:
        # None on the old side behaves like False -> True transition fires.
        out = bool_transition(None, True, "sym", added=ADDED)
        assert out[0].kind == ADDED[0]


class TestDiffByKey:
    def _change(self, key: str) -> Change:
        return Change(kind=ChangeKind.VAR_ADDED, symbol=key, description=key)

    def test_dispatches_each_bucket(self) -> None:
        old = {"a": 1, "b": 2}
        new = {"b": 2, "c": 3}
        out = diff_by_key(
            old,
            new,
            on_removed=lambda k, v: [self._change(f"removed:{k}")],
            on_added=lambda k, v: [self._change(f"added:{k}")],
            on_common=lambda k, o, n: [self._change(f"common:{k}")],
        )
        assert [c.symbol for c in out] == ["removed:a", "common:b", "added:c"]

    def test_omitted_callbacks_skip_bucket(self) -> None:
        old = {"a": 1}
        new = {"b": 2}
        out = diff_by_key(old, new, on_added=lambda k, v: [self._change(k)])
        assert [c.symbol for c in out] == ["b"]

    def test_common_key_with_no_on_common_is_skipped(self) -> None:
        # A key present in both maps but with on_common omitted must fall
        # through silently (covers the elif-not-taken branch).
        old = {"a": 1, "b": 2}
        new = {"a": 1, "c": 3}
        out = diff_by_key(
            old,
            new,
            on_removed=lambda k, v: [self._change(f"removed:{k}")],
            on_added=lambda k, v: [self._change(f"added:{k}")],
        )
        assert [c.symbol for c in out] == ["removed:b", "added:c"]

    def test_preserves_map_iteration_order(self) -> None:
        old = {"z": 1, "y": 1, "x": 1}
        new = {"z": 1, "y": 1, "x": 1}
        out = diff_by_key(old, new, on_common=lambda k, o, n: [self._change(k)])
        assert [c.symbol for c in out] == ["z", "y", "x"]

    def test_callback_returning_empty_is_fine(self) -> None:
        old = {"a": 1}
        new = {"a": 1}
        out = diff_by_key(old, new, on_common=lambda k, o, n: [])
        assert out == []

    def test_falsey_value_present_key_routes_to_common(self) -> None:
        # A key whose value is falsey (0) must still count as "present" and
        # route to on_common, not on_removed.
        old = {"a": 0}
        new = {"a": 0}
        out = diff_by_key(
            old,
            new,
            on_removed=lambda k, v: [self._change(f"removed:{k}")],
            on_common=lambda k, o, n: [self._change(f"common:{k}")],
        )
        assert [c.symbol for c in out] == ["common:a"]


class TestTypeMapKey:
    def test_prefers_qualified_name(self) -> None:
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        assert type_map_key(t) == "ns::Foo"

    def test_falls_back_to_bare_name(self) -> None:
        t = RecordType(name="Foo", qualified_name=None, kind="class")
        assert type_map_key(t) == "Foo"


class TestTypeMap:
    def test_lookup_by_qualified_key(self) -> None:
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        m = build_type_map([t])
        assert m["ns::Foo"] is t
        assert m.get("ns::Foo") is t

    def test_bare_alias_resolves_when_unambiguous(self) -> None:
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        m = build_type_map([t])
        assert m.get("Foo") is t
        assert "Foo" in m

    def test_bare_alias_not_added_when_ambiguous(self) -> None:
        a = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class")
        b = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class")
        m = build_type_map([a, b])
        assert m.get("Impl") is None
        assert "Impl" not in m
        assert m["ns1::Impl"] is a
        assert m["ns2::Impl"] is b

    def test_duplicate_same_qualified_identity_does_not_mark_ambiguous(self) -> None:
        # Two entries sharing both the same bare name AND the same qualified
        # key (e.g. an ODR-duplicate re-parse of the identical declaration)
        # is not an ambiguous collision -- the bare alias must still resolve.
        a = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        b = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        m = build_type_map([a, b])
        assert m.get("Foo") is b  # second entry wins the primary slot
        assert "Foo" in m

    def test_global_scope_type_has_no_redundant_alias_entry(self) -> None:
        t = RecordType(name="Foo", qualified_name=None, kind="class")
        m = build_type_map([t])
        assert list(m.items()) == [("Foo", t)]

    def test_items_yields_each_type_exactly_once(self) -> None:
        # A namespaced type's bare-name alias must never leak into iteration
        # (items/values/__iter__) -- only used for get()/__contains__ lookups
        # -- or every detector loop over old_map.items() would double-process
        # (and double-report) every namespaced type (Codex review, PR #608).
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        m = build_type_map([t])
        assert list(m.items()) == [("ns::Foo", t)]
        assert list(m.values()) == [t]
        assert list(m) == ["ns::Foo"]
        assert len(m) == 1

    def test_missing_key_raises_and_get_returns_default(self) -> None:
        m = build_type_map([])
        assert m.get("Foo") is None
        assert m.get("Foo", "default") == "default"
        try:
            m["Foo"]
        except KeyError:
            pass
        else:
            raise AssertionError("expected KeyError")

    def test_bare_name_is_unambiguous(self) -> None:
        unique = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        m = build_type_map([unique])
        assert m.bare_name_is_unambiguous("Foo") is True
        assert m.bare_name_is_unambiguous("Bar") is False  # no such bare name at all

    def test_bare_name_is_ambiguous_when_shared_by_distinct_types(self) -> None:
        a = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class")
        b = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class")
        m = build_type_map([a, b])
        assert m.bare_name_is_unambiguous("Impl") is False


class TestLookupMatchedType:
    """Codex review, PR #608 (second round): a plain ``other.get(type_map_key(t))``
    lookup only resolves the legacy-old/fresh-new direction (via the fresh
    side's bare-name alias). The reverse -- fresh old, legacy new -- has no
    alias to hit, since aliases only map bare -> qualified, never qualified ->
    bare. ``lookup_matched_type`` retries with the bare name to cover both --
    but ONLY when ``t``'s own bare name is unambiguous in its own map
    (``own``), or a genuine same-leaf-name collision on the probing side
    would retry into an unrelated survivor on the other side (Codex review,
    PR #608, third round).
    """

    def test_fresh_side_against_legacy_other_falls_back_to_bare(self) -> None:
        t = RecordType(name="Handle", qualified_name="ns::Handle", kind="class")
        own = build_type_map([t])
        legacy_counterpart = RecordType(
            name="Handle", qualified_name=None, kind="class"
        )
        other = build_type_map([legacy_counterpart])

        assert lookup_matched_type(own, other, t) is legacy_counterpart

    def test_direct_qualified_hit_needs_no_fallback(self) -> None:
        t = RecordType(name="Handle", qualified_name="ns::Handle", kind="class")
        own = build_type_map([t])
        counterpart = RecordType(
            name="Handle", qualified_name="ns::Handle", kind="class"
        )
        other = build_type_map([counterpart])

        assert lookup_matched_type(own, other, t) is counterpart

    def test_global_scope_type_key_equals_bare_no_redundant_lookup(self) -> None:
        t = RecordType(name="Foo", qualified_name=None, kind="class")
        own = build_type_map([t])
        counterpart = RecordType(name="Foo", qualified_name=None, kind="class")
        other = build_type_map([counterpart])

        assert lookup_matched_type(own, other, t) is counterpart

    def test_genuinely_absent_returns_none(self) -> None:
        t = RecordType(name="Handle", qualified_name="ns::Handle", kind="class")
        own = build_type_map([t])
        other = build_type_map([])

        assert lookup_matched_type(own, other, t) is None

    def test_ambiguous_probing_side_does_not_fall_back_to_survivor(self) -> None:
        """The exact scenario Codex flagged: old side has two distinct
        namespaced types sharing the bare name 'Impl'; the new side kept
        only one of them. Probing the REMOVED one must not retry into the
        unrelated SURVIVING one just because it also happens to be the
        other map's sole 'Impl'.
        """
        removed = RecordType(name="Impl", qualified_name="ns1::Impl", kind="class")
        survivor_old = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class")
        own = build_type_map([removed, survivor_old])  # ambiguous bare "Impl" in own
        survivor_new = RecordType(name="Impl", qualified_name="ns2::Impl", kind="class")
        other = build_type_map([survivor_new])

        assert lookup_matched_type(own, other, removed) is None
        # The genuinely-unchanged type still matches fine via its own
        # qualified key -- ambiguity in `own` doesn't block direct hits.
        assert lookup_matched_type(own, other, survivor_old) is survivor_new


def _hybrid_snap(fact_provenance: dict[str, str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        from_headers=True,
        ast_producer="hybrid",
        fact_provenance=fact_provenance,
    )


class TestFactKnownQualified:
    """G31 Phase C, third review round: dumper_hybrid.py qualifies
    deprecated/is_scoped provenance keys by namespace, but a hybrid baseline
    persisted before that fix still has real provenance recorded under the
    former bare key. fact_known_qualified must accept that legacy data
    (Codex review, fresh evidence) without reopening the bare-name collision
    the qualification itself was introduced to close."""

    def test_qualified_key_present_needs_no_fallback(self) -> None:
        old = _hybrid_snap({"type:ns::Foo:deprecated": "castxml"})
        new = _hybrid_snap({"type:ns::Foo:deprecated": "clang"})
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        old_map = build_type_map([t])
        new_map = build_type_map([t])
        assert fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            "Foo",
            "type:ns::Foo:deprecated",
            "type:ns::Foo:deprecated",
            "type:Foo:deprecated",
        )

    def test_legacy_bare_key_falls_back_when_unambiguous(self) -> None:
        # Both sides only ever recorded the pre-qualification bare key.
        old = _hybrid_snap({"type:Foo:deprecated": "castxml"})
        new = _hybrid_snap({"type:Foo:deprecated": "castxml"})
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        old_map = build_type_map([t])
        new_map = build_type_map([t])
        assert fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            "Foo",
            "type:ns::Foo:deprecated",
            "type:ns::Foo:deprecated",
            "type:Foo:deprecated",
        )

    def test_ambiguous_bare_name_does_not_fall_back(self) -> None:
        # Two distinct namespaced types share the bare name "Foo" on the old
        # side -- the bare-key provenance entry (if any) cannot be safely
        # attributed to either one, so no fallback is allowed there.
        old = _hybrid_snap({"type:Foo:deprecated": "castxml"})
        new = _hybrid_snap({})
        a_foo = RecordType(name="Foo", qualified_name="a::Foo", kind="class")
        b_foo = RecordType(name="Foo", qualified_name="b::Foo", kind="class")
        old_map = build_type_map([a_foo, b_foo])
        new_map = build_type_map([a_foo])
        assert not fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            "Foo",
            "type:a::Foo:deprecated",
            "type:a::Foo:deprecated",
            "type:Foo:deprecated",
        )

    def test_genuinely_unknown_stays_unknown(self) -> None:
        old = _hybrid_snap({})
        new = _hybrid_snap({})
        t = RecordType(name="Foo", qualified_name="ns::Foo", kind="class")
        old_map = build_type_map([t])
        new_map = build_type_map([t])
        assert not fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            "Foo",
            "type:ns::Foo:deprecated",
            "type:ns::Foo:deprecated",
            "type:Foo:deprecated",
        )

    def test_asymmetric_qualified_identity_probes_each_side_independently(self) -> None:
        """Codex review, second round: old predates ``qualified_name``
        entirely (its own ``type_map_key()`` is bare), while new carries the
        real namespaced spelling -- probing new's side with OLD's
        (bare-shaped) qualified key must not be what makes this fail; each
        side's OWN qualified key must be tried."""
        old = _hybrid_snap({"type:Color:deprecated": "castxml"})
        new = _hybrid_snap({"type:ns::Color:deprecated": "clang"})
        old_color = RecordType(name="Color", qualified_name=None, kind="class")
        new_color = RecordType(name="Color", qualified_name="ns::Color", kind="class")
        old_map = build_type_map([old_color])
        new_map = build_type_map([new_color])
        assert fact_known_qualified(
            old,
            new,
            old_map,
            new_map,
            "Color",
            "type:Color:deprecated",  # old's own type_map_key() -- bare
            "type:ns::Color:deprecated",  # new's own type_map_key() -- qualified
            "type:Color:deprecated",
        )


class TestDepthAwareBareName:
    """`depth_aware_bare_name` splits a qualified name at its outermost
    (depth-zero) `"::"` only, never inside a template argument's own
    qualification or nested expression brackets."""

    def test_bare_name_is_returned_unchanged(self) -> None:
        assert depth_aware_bare_name("Handle") == "Handle"

    def test_splits_at_a_plain_scope_boundary(self) -> None:
        assert depth_aware_bare_name("ns::Handle") == "Handle"

    def test_does_not_split_inside_a_qualified_template_argument(self) -> None:
        """Regression for the Codex review on PR #1041: a naive
        `rsplit("::", 1)` on `"api::Wrapper<dep::Tag>"` wrongly extracts
        `"Tag>"` instead of the real leaf `"Wrapper<dep::Tag>"`."""
        assert depth_aware_bare_name("api::Wrapper<dep::Tag>") == "Wrapper<dep::Tag>"

    def test_does_not_split_on_a_parenthesized_relational_angle(self) -> None:
        """A relational `>` used as a parenthesized non-type template
        argument (`S<(N > 0), dep::Tag>`) is not a real template delimiter
        -- the depth-zero `::` before `Tag` must still be found only
        *inside* the template, not read as the outer scope boundary."""
        assert (
            depth_aware_bare_name("api::S<(N > 0), dep::Tag>") == "S<(N > 0), dep::Tag>"
        )

    def test_does_not_split_on_an_array_subscript_relational_angle(self) -> None:
        """Regression for the Codex review on PR #1041, fourth follow-up
        round: an array-subscript comparison (`arr[1 > 0]`) needs no
        surrounding parens to be valid C++, so parenthesis tracking alone
        still let this shape's stray `>` close the outer template early,
        splitting inside `dep::Tag` instead of returning the whole
        unqualified leaf."""
        assert (
            depth_aware_bare_name("api::S<arr[1 > 0], dep::Tag>")
            == "S<arr[1 > 0], dep::Tag>"
        )

    def test_splits_after_a_bracketed_template_argument(self) -> None:
        """Complement of the bracket-nesting fix: a genuine depth-zero
        `::` *after* the templated segment closes must still split."""
        assert depth_aware_bare_name("ns::S<arr[1 > 0], dep::Tag>::Inner") == "Inner"

    def test_does_not_split_on_a_quoted_literal_angle(self) -> None:
        """Regression for the Codex review on PR #1041, seventh follow-up
        round: a quoted character literal used as a non-type template
        argument (`S<'>', dep::Tag>`, valid C++, retained verbatim by
        clang) has the same problem one level down from the parenthesized/
        bracketed relational cases: the `>` inside the literal sits at
        neither paren nor bracket depth, so it still closed the outer
        template early, splitting inside `dep::Tag` instead of returning
        the whole unqualified leaf."""
        assert depth_aware_bare_name("api::S<'>', dep::Tag>") == "S<'>', dep::Tag>"

    def test_handles_a_right_shift_inside_a_parenthesized_non_type_argument(
        self,
    ) -> None:
        """The bracket-KIND-aware stack `iter_top_level_chars` shares with
        `extract.semantic_normalizer_artifacts.has_unresolved_component`
        also resolves a case no round of this fix set out to close
        directly: a real `>>` shift/comparison operator inside a
        parenthesized non-type template argument (`S<(N >> 1), dep::Tag>`)
        is not two template closers, so it must not split inside
        `dep::Tag` either."""
        assert (
            depth_aware_bare_name("api::S<(N >> 1), dep::Tag>")
            == "S<(N >> 1), dep::Tag>"
        )

    def test_tolerates_unbalanced_brackets(self) -> None:
        """Defensive-floor coverage for the paren/bracket/angle depth
        guards: a stray closing `)`, `]`, or `>` with no matching opener
        must not drive any depth counter negative. No real, well-formed
        qualified name produces an unbalanced spelling, but this scan runs
        on rendered text from multiple producers rather than parsing it."""
        assert depth_aware_bare_name("ns)::Handle") == "Handle"
        assert depth_aware_bare_name("ns]::Handle") == "Handle"
        assert depth_aware_bare_name("ns>::Handle") == "Handle"
