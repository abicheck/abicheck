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
    diff_by_key,
    lookup_matched_type,
    type_map_key,
)
from abicheck.model import RecordType

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
            False, True, "sym",
            added=ADDED,
            added_values=("non-virtual", "virtual"),
        )
        assert out[0].old_value == "non-virtual"
        assert out[0].new_value == "virtual"

    def test_removed_values_are_carried_through(self) -> None:
        out = bool_transition(
            True, False, "sym",
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
            old, new,
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
            old, new,
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
            old, new,
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
        legacy_counterpart = RecordType(name="Handle", qualified_name=None, kind="class")
        other = build_type_map([legacy_counterpart])

        assert lookup_matched_type(own, other, t) is legacy_counterpart

    def test_direct_qualified_hit_needs_no_fallback(self) -> None:
        t = RecordType(name="Handle", qualified_name="ns::Handle", kind="class")
        own = build_type_map([t])
        counterpart = RecordType(name="Handle", qualified_name="ns::Handle", kind="class")
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
